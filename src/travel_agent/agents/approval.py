"""HITL② helper: parse the traveller's reply to the candidate list."""

from __future__ import annotations

from pydantic import Field

from travel_agent.config import PROMPTS_DIR
from travel_agent.llm import LLMOutput, get_chat_model
from travel_agent.state import Candidate


class ApprovalDecision(LLMOutput):
    remove: list[int] = Field(default_factory=list)
    add_requests: list[str] = Field(default_factory=list)
    approve: bool = False


def parse_reply(reply: str, shown: list[Candidate]) -> ApprovalDecision:
    llm = get_chat_model("light", max_tokens=1000).with_structured_output(ApprovalDecision)
    listing = "\n".join(f"{i}. {c.place} ({c.category})" for i, c in enumerate(shown, 1))
    system = (PROMPTS_DIR / "approval.md").read_text(encoding="utf-8")
    return llm.invoke([("system", system), ("human", f"List:\n{listing}\n\nReply: {reply}")])


def apply_removals(candidates: list[Candidate], remove: list[int]) -> list[Candidate]:
    """Mark the given 1-based positions among *approved* candidates as not approved."""
    shown = [c for c in candidates if c.approved]
    drop = {shown[i - 1].place for i in remove if 1 <= i <= len(shown)}
    return [c.model_copy(update={"approved": False}) if c.place in drop else c for c in candidates]
