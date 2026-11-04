"""Load the supported city list from data/cities.yaml."""

from __future__ import annotations

from typing import Literal

import yaml
from pydantic import BaseModel

from travel_agent.config import CITIES_FILE


class City(BaseModel):
    id: str
    name_ko: str
    transport: Literal["car", "taxi", "transit"]
    wikivoyage_en: list[str] = []
    wikivoyage_ko: list[str] = []


def load_cities() -> list[City]:
    data = yaml.safe_load(CITIES_FILE.read_text(encoding="utf-8"))
    return [City(**c) for c in data["cities"]]


def select_cities(ids: list[str] | None) -> list[City]:
    """Return all cities, or only those whose id is in `ids`."""
    cities = load_cities()
    if not ids:
        return cities
    unknown = set(ids) - {c.id for c in cities}
    if unknown:
        raise ValueError(f"Unknown city id(s): {', '.join(sorted(unknown))}")
    return [c for c in cities if c.id in ids]
