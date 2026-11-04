"""HITL③ helper: parse the traveller's reply to the finished itinerary."""

from __future__ import annotations

from pydantic import BaseModel, Field

from travel_agent.config import PROMPTS_DIR
from travel_agent.llm import get_chat_model
from travel_agent.state import Itinerary


class FinalDecision(BaseModel):
    approve: bool = False
    revise_days: list[int] = Field(default_factory=list)
    all_days: bool = False
    request: str = ""


def parse_final_reply(reply: str, itinerary: Itinerary) -> FinalDecision:
    llm = get_chat_model("light", max_tokens=1000).with_structured_output(FinalDecision)
    system = (PROMPTS_DIR / "final_approval.md").read_text(encoding="utf-8")
    outline = "\n".join(f"{d.day}일차: {d.summary}" for d in itinerary.days)
    return llm.invoke([("system", system), ("human", f"Itinerary:\n{outline}\n\nReply: {reply}")])


def days_to_revise(decision: FinalDecision, day_count: int) -> list[int]:
    """Validated, sorted day numbers to re-plan (empty = nothing to revise)."""
    if decision.all_days:
        return list(range(1, day_count + 1))
    return sorted({d for d in decision.revise_days if 1 <= d <= day_count})
