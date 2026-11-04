"""Intake: extract TripRequest from the conversation and decide what is still missing.

The LLM only extracts; merging, validation, and the follow-up question are deterministic.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache

from langchain_core.messages import AnyMessage, HumanMessage
from pydantic import BaseModel, Field

from travel_agent.config import PROMPTS_DIR
from travel_agent.ingest.cities import City, load_cities
from travel_agent.llm import get_chat_model
from travel_agent.state import MissingField, Theme, Transport, TripRequest


class IntakeExtraction(BaseModel):
    destination_name: str | None = None
    city_id: str | None = None
    similar_city_ids: list[str] = Field(default_factory=list)
    nights: int | None = None
    days: int | None = None
    theme: Theme | None = None
    children_ages: list[int] = Field(default_factory=list)
    companions: str | None = None
    preferences: list[str] = Field(default_factory=list)
    transport: Transport | None = None
    budget: str | None = None
    start_date: date | None = None


@lru_cache(maxsize=1)
def cities_by_id() -> dict[str, City]:
    return {c.id: c for c in load_cities()}


def _system_prompt() -> str:
    listing = "\n".join(f"- {c.id}: {c.name_ko}" for c in cities_by_id().values())
    template = (PROMPTS_DIR / "intake.md").read_text(encoding="utf-8")
    return template.replace("{cities}", listing)


def extract(messages: list[AnyMessage]) -> IntakeExtraction:
    llm = get_chat_model("light", max_tokens=2000).with_structured_output(IntakeExtraction)
    conversation = "\n".join(
        f"{'traveller' if isinstance(m, HumanMessage) else 'assistant'}: {m.content}"
        for m in messages
    )
    return llm.invoke([("system", _system_prompt()), ("human", conversation)])


def normalize_duration(nights: int | None, days: int | None) -> tuple[int | None, int | None]:
    if days is None and nights is not None:
        days = nights + 1
    if nights is None and days is not None:
        nights = max(days - 1, 0)
    return nights, days


def merge_request(current: TripRequest, ext: IntakeExtraction) -> TripRequest:
    """Apply newly extracted values on top of what we already know."""
    updates: dict = {}
    for field in ("theme", "companions", "transport", "budget", "start_date", "nights", "days"):
        value = getattr(ext, field)
        if value is not None:
            updates[field] = value
    for field in ("children_ages", "preferences"):
        value = getattr(ext, field)
        if value:
            updates[field] = value
    if ext.city_id in cities_by_id():
        updates["destination"] = ext.city_id
    elif ext.destination_name:
        updates["destination"] = None  # user switched to a destination we don't support
    merged = current.model_copy(update=updates)
    nights, days = normalize_duration(merged.nights, merged.days)
    return merged.model_copy(update={"nights": nights, "days": days})


def unsupported_destination(ext: IntakeExtraction) -> str | None:
    if ext.destination_name and ext.city_id not in cities_by_id():
        return ext.destination_name
    return None


def missing_fields(request: TripRequest) -> list[MissingField]:
    missing: list[MissingField] = []
    if request.destination is None:
        missing.append("destination")
    if request.days is None:
        missing.append("duration")
    if request.theme is None:
        missing.append("theme")
    if request.theme == "family" and not request.children_ages:
        missing.append("children_ages")
    return missing


QUESTIONS: dict[str, str] = {
    "destination": "어디로 여행을 가시나요?",
    "duration": "며칠 일정인가요? (예: 2박 3일, 당일치기)",
    "theme": "어떤 여행인가요? 아이와 함께하는 가족 여행인가요, 쉬러 가는 힐링 여행인가요?",
    "children_ages": "함께 가는 아이의 나이가 어떻게 되나요? (여러 명이면 모두 알려 주세요)",
}


def build_question(
    missing: list[MissingField],
    unsupported: str | None = None,
    similar: list[str] | None = None,
) -> str:
    cities = cities_by_id()
    if unsupported:
        names = [cities[c].name_ko for c in (similar or []) if c in cities]
        all_names = ", ".join(c.name_ko for c in cities.values())
        lines = [f"'{unsupported}'은(는) 아직 여행 자료가 없는 도시예요."]
        if names:
            lines.append(f"비슷한 여행지로 {', '.join(names)}은(는) 어떠세요?")
        lines.append(f"지원 도시: {all_names}")
        lines.append("가고 싶은 도시를 다시 알려 주세요.")
        others = [QUESTIONS[m] for m in missing if m != "destination"]
        return "\n".join(lines + others)
    return "\n".join(QUESTIONS[m] for m in missing)
