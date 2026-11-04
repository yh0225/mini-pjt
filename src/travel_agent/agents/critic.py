"""Critic: rule checks (authoritative, code) + a short LLM review (grounding/consistency)."""

from __future__ import annotations

import json
import logging
from typing import Literal

from pydantic import BaseModel, Field

from travel_agent.config import PROMPTS_DIR
from travel_agent.llm import get_chat_model
from travel_agent.rules import Violation, check_itinerary, to_minutes
from travel_agent.state import Activity, Candidate, Itinerary, TripRequest

log = logging.getLogger(__name__)

MAX_RETRIES = 2


IssueKind = Literal["unknown_place", "duplicate", "contradiction"]


class ReviewIssue(BaseModel):
    kind: IssueKind
    day: int | None = None
    message: str = Field(description="One Korean sentence telling the planner what to fix")


class RequestCheck(BaseModel):
    day: int
    satisfied: bool
    feedback: str = Field(
        default="", description="When not satisfied: one Korean sentence on what to change"
    )


class Review(BaseModel):
    issues: list[ReviewIssue] = Field(default_factory=list)
    request_checks: list[RequestCheck] = Field(
        default_factory=list,
        description="One entry per day listed under 'Activities to judge for the request'",
    )


# Parts of the day a request can name -> (start, end) in minutes. Code, not the model,
# decides which activities a request is about.
REQUEST_WINDOWS: dict[str, tuple[int, int]] = {
    "오전": (0, 12 * 60),
    "아침": (0, 12 * 60),
    "점심": (11 * 60, 14 * 60),
    "오후": (12 * 60, 18 * 60),
    "저녁": (18 * 60, 24 * 60),
    "밤": (18 * 60, 24 * 60),
}
REQUEST_SKIP_CATEGORIES = ("move", "meal")


def request_windows(text: str) -> list[tuple[int, int]]:
    """Time windows named in a request; empty list = the whole day."""
    return [window for word, window in REQUEST_WINDOWS.items() if word in text]


def activities_to_judge(
    itinerary: Itinerary, traveller_request: str, request_days: list[int]
) -> dict[int, list[Activity]]:
    """Per requested day, the activities whose start falls in the named part of the day."""
    windows = request_windows(traveller_request)
    out: dict[int, list[Activity]] = {}
    for day in itinerary.days:
        if day.day not in request_days:
            continue
        picked = []
        for a in day.activities:
            start = to_minutes(a.start)
            if a.category in REQUEST_SKIP_CATEGORIES or start is None:
                continue
            if not windows or any(lo <= start < hi for lo, hi in windows):
                picked.append(a)
        out[day.day] = picked
    return out


def review_input(
    itinerary: Itinerary,
    candidates: list[Candidate],
    traveller_request: str = "",
    request_days: list[int] | None = None,
) -> str:
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
    text = (
        "Approved candidates: "
        + ", ".join(c.place for c in candidates)
        + "\n\nItinerary:\n"
        + json.dumps(plan, ensure_ascii=False)
    )
    if traveller_request and request_days:
        judged = activities_to_judge(itinerary, traveller_request, request_days)
        lines = [
            f"\n\nTraveller's change request: {traveller_request}",
            "Activities to judge for the request (already filtered to the right "
            "day and time of day; judge ONLY these):",
        ]
        for day, acts in judged.items():
            listed = (
                "; ".join(f"{a.start}-{a.end} [{a.category}] {a.title} @ {a.place}" for a in acts)
                or "(none)"
            )
            lines.append(f"- day {day}: {listed}")
        text += "\n".join(lines)
    return text


def llm_review(
    itinerary: Itinerary,
    candidates: list[Candidate],
    traveller_request: str = "",
    request_days: list[int] | None = None,
) -> list[Violation]:
    llm = get_chat_model("light", max_tokens=2000).with_structured_output(Review)
    system = (PROMPTS_DIR / "critic_review.md").read_text(encoding="utf-8")
    human = review_input(itinerary, candidates, traveller_request, request_days)
    try:
        review: Review = llm.invoke([("system", system), ("human", human)])
    except Exception:  # noqa: BLE001 - the review is advisory; rules still apply
        log.exception("LLM review failed; continuing with rule checks only")
        return []
    return to_violations(review, itinerary, traveller_request, request_days or [])


def to_violations(
    review: Review,
    itinerary: Itinerary,
    traveller_request: str = "",
    request_days: list[int] | None = None,
) -> list[Violation]:
    valid_days = {d.day for d in itinerary.days}
    out = [
        Violation(
            day=issue.day if issue.day in valid_days else None, rule="review", message=issue.message
        )
        for issue in review.issues
    ]
    if traveller_request:
        # Only explicit "not satisfied" answers for days the traveller asked about count.
        for check in review.request_checks:
            if not check.satisfied and check.day in (request_days or []):
                feedback = check.feedback or "요청이 반영되지 않았어요."
                out.append(
                    Violation(
                        day=check.day,
                        rule="request",
                        message=f"요청 '{traveller_request}' 미반영: {feedback}",
                    )
                )
    return out


def critique(
    itinerary: Itinerary,
    request: TripRequest,
    candidates: list[Candidate],
    traveller_request: str = "",
    request_days: list[int] | None = None,
) -> list[Violation]:
    return check_itinerary(itinerary, request) + llm_review(
        itinerary, candidates, traveller_request, request_days
    )


def days_to_redo(violations: list[Violation], all_days: list[int]) -> list[int]:
    """Days the planner must rewrite. A whole-itinerary violation means every day."""
    if any(v.day is None for v in violations):
        return list(all_days)
    return sorted({v.day for v in violations if v.day is not None})


def format_violation(v: Violation) -> str:
    return f"{v.day}일차: {v.message}" if v.day is not None else v.message
