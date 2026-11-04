"""Travel time estimate between two indexed places (deterministic, no LLM)."""

from __future__ import annotations

import math

from langchain_core.tools import tool

from travel_agent.tools.places import lookup_place

DEFAULT_MINUTES = 30  # used when a place has no coordinates
WALK_MAX_KM = 1.0
WALK_KMH = 4.5

# Straight-line distance is multiplied by a detour factor, then divided by speed,
# plus a fixed overhead (parking, waiting, transfers). Initial guesses; tune later.
TRANSPORT_PROFILES: dict[str, dict[str, float]] = {
    "car": {"detour": 1.3, "kmh": 40.0, "overhead_min": 5.0},
    "taxi": {"detour": 1.3, "kmh": 25.0, "overhead_min": 5.0},
    "transit": {"detour": 1.4, "kmh": 22.0, "overhead_min": 10.0},
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def travel_minutes(distance_km: float, transport: str) -> tuple[int, str]:
    """Return (minutes, mode) for a straight-line distance."""
    if distance_km <= WALK_MAX_KM:
        return max(1, round(distance_km * 1.2 / WALK_KMH * 60)), "walk"
    mode = transport if transport in TRANSPORT_PROFILES else "transit"
    profile = TRANSPORT_PROFILES[mode]
    hours = distance_km * profile["detour"] / profile["kmh"]
    return round(hours * 60 + profile["overhead_min"]), mode


@tool
def estimate_travel_time(city: str, place_a: str, place_b: str, transport: str) -> str:
    """Estimate travel time between two places from search_places results.

    Args:
        city: City id, e.g. "jeju".
        place_a: Exact place name as returned by search_places.
        place_b: Exact place name as returned by search_places.
        transport: "car", "taxi", or "transit".
    """
    a, b = lookup_place(city, place_a), lookup_place(city, place_b)
    missing = [name for name, m in [(place_a, a), (place_b, b)] if m is None]
    if missing:
        return f"Unknown place(s): {', '.join(missing)}. Use exact names from search_places."
    if not all(m and "lat" in m and "lon" in m for m in (a, b)):
        return f"About {DEFAULT_MINUTES} min (estimate: coordinates missing for a place)."
    km = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
    minutes, mode = travel_minutes(km, transport)
    return f"About {minutes} min by {mode} (straight-line {km:.1f} km)."
