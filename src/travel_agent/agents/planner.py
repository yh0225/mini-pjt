"""Theme planners (ReAct): build an Itinerary from approved candidates.

The family and healing planners share one implementation and differ only in their
theme-rule prompt, so a new theme = one new prompt file + one graph node.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError

from travel_agent.config import PROMPTS_DIR
from travel_agent.ingest.cities import City
from travel_agent.llm import get_chat_model
from travel_agent.state import Candidate, Itinerary, TripRequest
from travel_agent.tools.travel import estimate_travel_time

log = logging.getLogger(__name__)

RECURSION_LIMIT = 40  # ~18 tool round-trips


def system_prompt(theme: str) -> str:
    base = (PROMPTS_DIR / "planner_base.md").read_text(encoding="utf-8")
    rules = (PROMPTS_DIR / f"planner_{theme}.md").read_text(encoding="utf-8")
    return f"{base}\n{rules}"


def build_planner(theme: str):
    return create_agent(
        model=get_chat_model("agent"),
        tools=[estimate_travel_time],
        system_prompt=system_prompt(theme),
        response_format=ToolStrategy(Itinerary),
        name=f"{theme}_planner",
    )


def describe_task(request: TripRequest, city: City, candidates: list[Candidate], brief: str) -> str:
    lines = [
        f"City id: {city.id} ({city.name_ko})",
        f"Days: {request.days} ({request.nights} nights)",
        f"Transport: {request.transport or city.transport}",
        f"Theme: {request.theme}",
    ]
    if request.children_ages:
        lines.append(f"Children's ages: {', '.join(map(str, request.children_ages))}")
    if request.preferences:
        lines.append(f"Preferences: {', '.join(request.preferences)}")
    if brief:
        lines.append(f"\nSupervisor brief: {brief}")
    lines.append("\nApproved candidates:")
    for c in candidates:
        coords = "has coords" if c.lat is not None else "no coords"
        lines.append(f"- {c.place} [{c.category}, {coords}]: {c.summary_ko}")
    return "\n".join(lines)


def ground_itinerary(itinerary: Itinerary, candidates: list[Candidate], days: int) -> Itinerary:
    """Attach source URLs from candidates and record structural problems as warnings.

    `warnings` is reserved for problems the system detects; anything the model wrote
    there is advice, so it is moved to `tips`.
    """
    by_name = {c.place: c for c in candidates}
    tips = list(itinerary.tips) + list(itinerary.warnings)
    warnings: list[str] = []
    days_out = []
    for day in itinerary.days:
        activities = [
            a.model_copy(update={"source_url": by_name[a.place].source_url})
            if a.place in by_name
            else a
            for a in day.activities
        ]
        days_out.append(day.model_copy(update={"activities": activities}))
    if len(days_out) != days:
        warnings.append(f"요청한 {days}일과 달리 {len(days_out)}일 일정이 만들어졌어요.")
    return itinerary.model_copy(update={"days": days_out, "tips": tips, "warnings": warnings})


def describe_revision(current: Itinerary, revise_days: list[int], reason: str) -> str:
    plan = [
        {
            "day": d.day,
            "summary": d.summary,
            "activities": [
                f"{a.start}-{a.end} [{a.category}] {a.title} @ {a.place}" for a in d.activities
            ],
        }
        for d in current.days
    ]
    days = ", ".join(map(str, revise_days))
    return (
        "\n\n## Revision task\n"
        f"An itinerary already exists. Rewrite ONLY day(s) {days}; the other days stay exactly "
        "as they are (do not reuse their candidate places).\n"
        f"What must change:\n{reason}\n\n"
        "Current itinerary:\n" + json.dumps(plan, ensure_ascii=False, indent=1) + "\n\n"
        "Return the full itinerary with all days (unchanged days copied as they are)."
    )


def merge_days(
    current: Itinerary, revised: Itinerary, revise_days: list[int]
) -> tuple[Itinerary, list[int]]:
    """Take only `revise_days` from `revised`; every other day stays from `current`.

    Trip-level tips come from `revised` (the planner saw the whole updated trip), so they
    don't mention places that were just removed. The title stays from `current`.
    Returns the merged itinerary and the requested days the revised plan did not contain.
    """
    new_by_day = {d.day: d for d in revised.days}
    days = []
    missing = []
    for day in current.days:
        if day.day in revise_days and day.day in new_by_day:
            days.append(new_by_day[day.day])
        else:
            if day.day in revise_days:
                missing.append(day.day)
            days.append(day)
    update: dict = {"days": days}
    if revised.tips:
        update["tips"] = revised.tips
    return current.model_copy(update=update), missing


def stream_plan(
    theme: str,
    request: TripRequest,
    city: City,
    candidates: list[Candidate],
    brief: str = "",
    current: Itinerary | None = None,
    revise_days: list[int] | None = None,
    reason: str = "",
) -> Iterator[tuple[str, Any]]:
    """Yield ("tool_call", call) events, then ("result", Itinerary | None).

    With `current` and `revise_days`, only those days are re-planned and merged back.
    """
    agent = build_planner(theme)
    task = describe_task(request, city, candidates, brief)
    revising = current is not None and bool(revise_days)
    if revising:
        task += describe_revision(current, revise_days, reason)
    inputs = {"messages": [("user", task)]}
    final: Itinerary | None = None
    try:
        for update in agent.stream(
            inputs, config={"recursion_limit": RECURSION_LIMIT}, stream_mode="updates"
        ):
            for node_output in update.values():
                if not isinstance(node_output, dict):
                    continue
                for msg in node_output.get("messages", []):
                    if isinstance(msg, AIMessage):
                        for call in msg.tool_calls:
                            yield "tool_call", call
                if node_output.get("structured_response") is not None:
                    final = node_output["structured_response"]
    except GraphRecursionError:
        log.warning("%s planner hit its step limit", theme)
    if final is None:
        yield "result", current if revising else None
        return
    grounded = ground_itinerary(final, candidates, request.days or 0)
    if revising:
        merged, missing = merge_days(current, grounded, revise_days)
        if missing:
            log.warning("Revised plan lacked day(s) %s; kept the previous version", missing)
        yield "result", merged
    else:
        yield "result", grounded
