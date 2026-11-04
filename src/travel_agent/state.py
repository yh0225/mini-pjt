"""Shared data models for the travel agent (grows milestone by milestone)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

Theme = Literal["family", "healing"]
Transport = Literal["car", "taxi", "transit"]
CandidateCategory = Literal["sight", "activity", "meal", "rest", "shop"]


class TripRequest(BaseModel):
    """Trip conditions extracted from the user (intake fills this in M3)."""

    destination: str | None = None  # city id from data/cities.yaml, e.g. "jeju"
    nights: int | None = None
    days: int | None = None
    start_date: date | None = None
    companions: str | None = None
    children_ages: list[int] = Field(default_factory=list)
    theme: Theme | None = None
    budget: str | None = None
    preferences: list[str] = Field(default_factory=list)
    transport: Transport | None = None


class Candidate(BaseModel):
    """A place proposed by the Researcher and shown at HITL②."""

    place: str  # exact `place` name from the index
    city: str
    category: CandidateCategory
    summary_ko: str  # Korean summary, even when the source is English
    reason_ko: str  # why it fits this trip (theme, ages, preferences)
    source_url: str = ""
    lat: float | None = None
    lon: float | None = None
    approved: bool = True


ActivityCategory = Literal["sight", "activity", "meal", "rest", "move"]


class Activity(BaseModel):
    start: str = Field(description='Start time "HH:MM" (24h)')
    end: str = Field(description='End time "HH:MM" (24h)')
    title: str = Field(description="Short Korean title, e.g. '협재해수욕장에서 물놀이'")
    place: str = Field(
        description="Exact candidate place name, or a generic Korean label such as "
        "'숙소', '이동', '근처 식당' when it is not a candidate"
    )
    category: ActivityCategory
    note: str | None = Field(default=None, description="Optional Korean tip")
    source_url: str | None = None  # filled from the candidate after planning


class DayPlan(BaseModel):
    day: int = Field(description="1-based day number")
    summary: str = Field(description="One-line Korean summary of the day")
    activities: list[Activity]


class Itinerary(BaseModel):
    title: str = Field(description="Korean title for the whole trip")
    theme: str
    days: list[DayPlan]
    tips: list[str] = Field(default_factory=list, description="Short Korean tips and cautions")
    warnings: list[str] = Field(
        default_factory=list,
        description="Reserved for automatic checks. Leave empty; put cautions in tips.",
    )


Worker = Literal[
    "research", "family_planner", "healing_planner", "critic", "final_approval", "save", "finish"
]
MissingField = Literal["destination", "duration", "theme", "children_ages"]


class AgentState(TypedDict, total=False):
    """LangGraph state. Pydantic models are stored as-is and persisted by the checkpointer."""

    messages: Annotated[list[AnyMessage], add_messages]
    request: TripRequest
    missing: list[MissingField]  # required fields still unknown (HITL①)
    unsupported_destination: str | None  # destination the user named but we have no data for
    similar_cities: list[str]  # supported city ids suggested instead
    candidates: list[Candidate]
    add_requests: list[str]  # extra places the user asked for at HITL②
    notice: str | None  # latest research result message shown at HITL②
    candidates_confirmed: bool
    # --- M4: supervisor / planners ---
    next: Worker  # supervisor's routing decision
    brief: str  # supervisor's instruction for the chosen worker
    step_count: int  # supervisor decisions so far
    itinerary: Itinerary | None
    # --- M5: critic / HITL③ / save ---
    revise_days: list[int]  # days the planner must rewrite next (critic or traveller)
    revise_reason: str  # what must change on those days
    retry_count: int  # critic-triggered rewrites in the current cycle (max 2)
    itinerary_checked: bool  # critic has checked the current itinerary
    critique: list[str]  # latest critic findings (Korean)
    final_approved: bool
    saved_paths: list[str]
