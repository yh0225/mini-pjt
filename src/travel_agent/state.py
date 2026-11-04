"""Shared data models for the travel agent (grows milestone by milestone)."""

from __future__ import annotations

from datetime import date
from typing import Literal

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
