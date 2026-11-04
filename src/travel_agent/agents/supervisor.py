"""Supervisor: chooses the next worker and writes its brief.

The LLM decides *who* and *what to emphasise*; code decides which choices are legal
(`allowed_workers`) and overrides illegal ones, so routing can never skip a step.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from travel_agent.config import PROMPTS_DIR
from travel_agent.llm import get_chat_model
from travel_agent.state import AgentState, Worker
from travel_agent.ui import describe_request_ko

log = logging.getLogger(__name__)

# research + (planner + critic) x3 + final approval + save + finish is already 10 steps,
# so leave room for one or two traveller revisions.
MAX_STEPS = 20


class SupervisorDecision(BaseModel):
    next: Worker
    brief: str = Field(default="", description="English instruction for the chosen member")
    reason: str = Field(default="", description="One short Korean sentence")


def planner_for(theme: str | None) -> Worker:
    return "healing_planner" if theme == "healing" else "family_planner"


def allowed_workers(state: AgentState) -> list[Worker]:
    """Legal next steps for the current state (first item is the default).

    At the step limit no more planning or checking happens: the current itinerary goes
    straight to the traveller (HITL③) and is saved.
    """
    at_limit = state.get("step_count", 0) >= MAX_STEPS
    if not state.get("candidates_confirmed"):
        return ["finish"] if at_limit else ["research"]
    if not any(c.approved for c in state.get("candidates") or []):
        return ["finish"]  # traveller confirmed an empty list; nothing to plan with
    planner = planner_for(state["request"].theme)
    if state.get("itinerary") is None:
        return ["finish"] if at_limit else [planner]
    if state.get("revise_days") and not at_limit:
        return [planner]
    if not state.get("itinerary_checked") and not at_limit:
        return ["critic"]
    if not state.get("final_approved"):
        return ["final_approval"]
    if not state.get("saved_paths"):
        return ["save"]
    return ["finish"]


def describe_state(state: AgentState, allowed: list[Worker]) -> str:
    request = state["request"]
    approved = [c for c in state.get("candidates") or [] if c.approved]
    lines = [
        f"Trip: {describe_request_ko(request)}",
        f"Theme: {request.theme}; children's ages: {request.children_ages or 'none'}; "
        f"preferences: {request.preferences or 'none'}",
        f"Candidates confirmed by traveller: {bool(state.get('candidates_confirmed'))} "
        f"({len(approved)} places: {', '.join(c.place for c in approved) or '-'})",
        f"Itinerary exists: {state.get('itinerary') is not None}",
        f"Days to rewrite: {state.get('revise_days') or 'none'}"
        + (f" because: {state.get('revise_reason')}" if state.get("revise_days") else ""),
        f"Itinerary checked by critic: {bool(state.get('itinerary_checked'))}; "
        f"critic rewrites so far: {state.get('retry_count', 0)}",
        f"Approved by traveller: {bool(state.get('final_approved'))}; "
        f"saved: {bool(state.get('saved_paths'))}",
        f"Allowed members now: {', '.join(allowed)}",
    ]
    return "\n".join(lines)


def decide(state: AgentState) -> SupervisorDecision:
    allowed = allowed_workers(state)
    if allowed == ["finish"] and state.get("step_count", 0) >= MAX_STEPS:
        log.warning("Supervisor step limit reached; finishing")
        return SupervisorDecision(next="finish", reason="작업 단계 한도에 도달해 마무리합니다.")
    if allowed == ["finish"] and state.get("saved_paths"):
        return SupervisorDecision(next="finish", reason="일정을 저장했어요. 즐거운 여행 되세요!")

    llm = get_chat_model("light", max_tokens=1000).with_structured_output(SupervisorDecision)
    system = (PROMPTS_DIR / "supervisor.md").read_text(encoding="utf-8")
    decision: SupervisorDecision = llm.invoke(
        [("system", system), ("human", describe_state(state, allowed))]
    )
    return enforce(decision, allowed)


def enforce(decision: SupervisorDecision, allowed: list[Worker]) -> SupervisorDecision:
    """Replace an illegal choice with the default allowed worker."""
    if decision.next not in allowed:
        log.warning(
            "Supervisor chose %s (allowed %s); using %s", decision.next, allowed, allowed[0]
        )
        return decision.model_copy(update={"next": allowed[0]})
    return decision
