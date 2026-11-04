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

MAX_STEPS = 10


class SupervisorDecision(BaseModel):
    next: Worker
    brief: str = Field(default="", description="English instruction for the chosen member")
    reason: str = Field(default="", description="One short Korean sentence")


def planner_for(theme: str | None) -> Worker:
    return "healing_planner" if theme == "healing" else "family_planner"


def allowed_workers(state: AgentState) -> list[Worker]:
    """Legal next steps for the current state (first item is the default)."""
    if state.get("step_count", 0) >= MAX_STEPS:
        return ["finish"]
    if not state.get("candidates_confirmed"):
        return ["research"]
    if not any(c.approved for c in state.get("candidates") or []):
        return ["finish"]  # traveller confirmed an empty list; nothing to plan with
    if state.get("itinerary") is None:
        return [planner_for(state["request"].theme)]
    return ["finish"]  # M5 adds critic / final approval here


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
        f"Allowed members now: {', '.join(allowed)}",
    ]
    return "\n".join(lines)


def decide(state: AgentState) -> SupervisorDecision:
    allowed = allowed_workers(state)
    if allowed == ["finish"] and state.get("step_count", 0) >= MAX_STEPS:
        log.warning("Supervisor step limit reached; finishing")
        return SupervisorDecision(next="finish", reason="작업 단계 한도에 도달해 마무리합니다.")

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
