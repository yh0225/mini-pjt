"""Critic: rule checks (authoritative, code) + a short LLM review (grounding/consistency)."""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field

from travel_agent.config import PROMPTS_DIR
from travel_agent.llm import get_chat_model
from travel_agent.rules import Violation, check_itinerary
from travel_agent.state import Candidate, Itinerary, TripRequest

log = logging.getLogger(__name__)

MAX_RETRIES = 2


class ReviewIssue(BaseModel):
    day: int | None = None
    message: str = Field(description="One Korean sentence telling the planner what to fix")


class Review(BaseModel):
    issues: list[ReviewIssue] = Field(default_factory=list)


def llm_review(itinerary: Itinerary, candidates: list[Candidate]) -> list[Violation]:
    llm = get_chat_model("light", max_tokens=2000).with_structured_output(Review)
    system = (PROMPTS_DIR / "critic_review.md").read_text(encoding="utf-8")
    plan = [
        {
            "day": d.day,
            "activities": [
                {
                    "time": f"{a.start}-{a.end}",
                    "category": a.category,
                    "title": a.title,
                    "place": a.place,
                    "note": a.note,
                }
                for a in d.activities
            ],
        }
        for d in itinerary.days
    ]
    human = (
        "Approved candidates: "
        + ", ".join(c.place for c in candidates)
        + "\n\nItinerary:\n"
        + json.dumps(plan, ensure_ascii=False)
    )
    try:
        review: Review = llm.invoke([("system", system), ("human", human)])
    except Exception:  # noqa: BLE001 - the review is advisory; rules still apply
        log.exception("LLM review failed; continuing with rule checks only")
        return []
    valid_days = {d.day for d in itinerary.days}
    return [
        Violation(day=i.day if i.day in valid_days else None, rule="review", message=i.message)
        for i in review.issues
    ]


def critique(
    itinerary: Itinerary, request: TripRequest, candidates: list[Candidate]
) -> list[Violation]:
    return check_itinerary(itinerary, request) + llm_review(itinerary, candidates)


def days_to_redo(violations: list[Violation], all_days: list[int]) -> list[int]:
    """Days the planner must rewrite. A whole-itinerary violation means every day."""
    if any(v.day is None for v in violations):
        return list(all_days)
    return sorted({v.day for v in violations if v.day is not None})


def format_violation(v: Violation) -> str:
    return f"{v.day}일차: {v.message}" if v.day is not None else v.message
