"""Researcher agent (ReAct): collects candidate places with RAG tools.

Run standalone (M2):
    python -m travel_agent.agents.researcher --city jeju --theme family --days 4 --children 5
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Iterator
from typing import Any

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

from travel_agent.config import PROMPTS_DIR
from travel_agent.ingest.cities import City, load_cities
from travel_agent.llm import get_chat_model
from travel_agent.safety import is_blocked_place
from travel_agent.state import Candidate, TripRequest
from travel_agent.tools.places import lookup_place, search_places
from travel_agent.tools.travel import estimate_travel_time

log = logging.getLogger(__name__)

RECURSION_LIMIT = 30  # ~14 tool round-trips
ADD_RECURSION_LIMIT = 16  # "add more" requests: ~7 round-trips, then give up


class CandidateList(BaseModel):
    candidates: list[Candidate]


def build_researcher():
    return create_agent(
        model=get_chat_model("agent"),
        tools=[search_places, estimate_travel_time],
        system_prompt=(PROMPTS_DIR / "researcher.md").read_text(encoding="utf-8"),
        response_format=ToolStrategy(CandidateList),
        name="researcher",
    )


def describe_request(request: TripRequest, city: City) -> str:
    lines = [
        f"City id: {city.id} ({city.name_ko})",
        f"Theme: {request.theme or 'not specified'}",
        f"Length: {request.nights if request.nights is not None else '?'} nights / "
        f"{request.days or '?'} days",
        f"Transport: {request.transport or city.transport}",
    ]
    if request.children_ages:
        lines.append(f"Children's ages: {', '.join(map(str, request.children_ages))}")
    if request.companions:
        lines.append(f"Companions: {request.companions}")
    if request.preferences:
        lines.append(f"Preferences: {', '.join(request.preferences)}")
    return "Collect candidate places for this trip.\n" + "\n".join(lines)


def ground_candidates(candidates: list[Candidate], city: str) -> list[Candidate]:
    """Fill source_url/coords from the index and drop places the index doesn't know."""
    grounded: list[Candidate] = []
    seen: set[str] = set()
    for c in candidates:
        meta = lookup_place(city, c.place)
        if meta is None:
            log.warning("Dropping candidate not found in index: %s", c.place)
            continue
        if is_blocked_place(meta, c.summary_ko):
            log.warning("Dropping off-topic candidate (nightlife/gambling/adult): %s", c.place)
            continue
        if c.place in seen:
            continue
        seen.add(c.place)
        grounded.append(
            c.model_copy(
                update={
                    "city": city,
                    "source_url": meta.get("url", ""),
                    "lat": meta.get("lat"),
                    "lon": meta.get("lon"),
                }
            )
        )
    return grounded


def describe_additions(add_requests: list[str], existing: list[Candidate]) -> str:
    have = ", ".join(c.place for c in existing) or "(none)"
    wants = "; ".join(add_requests)
    return (
        "\n\nThe traveller has reviewed the list and wants more places.\n"
        f"Add for: {wants}\n"
        f"Already in the list (do not repeat): {have}\n"
        "Propose only NEW candidates that match these requests: as many as the traveller "
        "asked for (one per request if no number was given, never more than 3 in total)."
    )


def stream_research(
    request: TripRequest,
    city: City,
    extra_instruction: str = "",
    recursion_limit: int = RECURSION_LIMIT,
) -> Iterator[tuple[str, Any]]:
    """Run the agent, yielding ("tool_call" | "tool_result" | "result", payload) events."""
    agent = build_researcher()
    inputs = {"messages": [("user", describe_request(request, city) + extra_instruction)]}
    final: CandidateList | None = None
    try:
        for update in agent.stream(
            inputs, config={"recursion_limit": recursion_limit}, stream_mode="updates"
        ):
            for node_output in update.values():
                if not isinstance(node_output, dict):
                    continue
                for msg in node_output.get("messages", []):
                    if isinstance(msg, AIMessage):
                        for call in msg.tool_calls:
                            yield "tool_call", call
                    elif isinstance(msg, ToolMessage):
                        yield "tool_result", msg
                if node_output.get("structured_response") is not None:
                    final = node_output["structured_response"]
    except GraphRecursionError:
        # The agent kept searching (usually for something the index doesn't have).
        log.warning("Researcher hit its step limit; returning no new candidates")
        yield "warning", "step_limit"
    candidates = final.candidates if final else []
    yield "result", ground_candidates(candidates, city.id)


def research(request: TripRequest, city: City) -> list[Candidate]:
    result: list[Candidate] = []
    for kind, payload in stream_research(request, city):
        if kind == "result":
            result = payload
    return result


def main() -> None:
    from rich.console import Console
    from rich.table import Table

    parser = argparse.ArgumentParser(description="Run the Researcher agent alone (M2).")
    parser.add_argument("--city", required=True, help="city id, e.g. jeju")
    parser.add_argument("--theme", choices=["family", "healing"], required=True)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--children", type=int, nargs="*", default=[], help="children's ages")
    parser.add_argument("--prefs", nargs="*", default=[], help="preferences, e.g. 바다 실내")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    console = Console()
    cities = {c.id: c for c in load_cities()}
    city = cities[args.city]
    request = TripRequest(
        destination=city.id,
        nights=max(args.days - 1, 0),
        days=args.days,
        theme=args.theme,
        children_ages=args.children,
        preferences=args.prefs,
    )
    console.print(f"[bold]Request[/]\n{describe_request(request, city)}\n")

    candidates: list[Candidate] = []
    tool_calls = 0
    for kind, payload in stream_research(request, city):
        if kind == "tool_call":
            if payload["name"] == "CandidateList":
                continue  # structured-output pseudo tool
            tool_calls += 1
            args_str = ", ".join(f"{k}={v!r}" for k, v in payload["args"].items())
            console.print(f"[cyan]→ {payload['name']}[/]({args_str})")
        elif kind == "tool_result" and payload.name != "CandidateList":
            first = str(payload.content).splitlines()[0] if payload.content else ""
            console.print(f"  [dim]← {first[:110]}[/]")
        elif kind == "result":
            candidates = payload

    table = Table(title=f"후보 장소 {len(candidates)}곳 (도구 호출 {tool_calls}회)")
    for col in ["#", "장소", "분류", "요약", "추천 이유", "좌표"]:
        table.add_column(col, overflow="fold")
    for i, c in enumerate(candidates, 1):
        coords = "✓" if c.lat is not None else "-"
        table.add_row(str(i), c.place, c.category, c.summary_ko, c.reason_ko, coords)
    console.print(table)


if __name__ == "__main__":
    main()
