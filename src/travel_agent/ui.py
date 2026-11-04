"""Terminal rendering helpers (rich)."""

from __future__ import annotations

from rich.table import Table

from travel_agent.ingest.cities import load_cities
from travel_agent.state import Candidate, TripRequest

CATEGORY_KO = {"sight": "관광", "activity": "체험", "meal": "식사", "rest": "휴식", "shop": "쇼핑"}
THEME_KO = {"family": "가족", "healing": "힐링"}
TRANSPORT_KO = {"car": "렌터카", "taxi": "택시", "transit": "대중교통"}


def candidates_table(candidates: list[Candidate], title: str) -> Table:
    table = Table(title=title, show_lines=False)
    for col in ["#", "장소", "분류", "요약", "추천 이유"]:
        table.add_column(col, overflow="fold")
    for i, c in enumerate(candidates, 1):
        table.add_row(
            str(i), c.place, CATEGORY_KO.get(c.category, c.category), c.summary_ko, c.reason_ko
        )
    return table


def describe_request_ko(request: TripRequest) -> str:
    cities = {c.id: c for c in load_cities()}
    parts = []
    if request.destination in cities:
        city = cities[request.destination]
        parts.append(city.name_ko)
        transport = request.transport or city.transport
    else:
        transport = request.transport
    if request.days is not None:
        parts.append("당일치기" if request.nights == 0 else f"{request.nights}박 {request.days}일")
    if request.theme:
        parts.append(THEME_KO[request.theme])
    if request.children_ages:
        parts.append("아이 " + ", ".join(f"{a}세" for a in request.children_ages))
    if transport:
        parts.append(TRANSPORT_KO.get(transport, transport))
    if request.preferences:
        parts.append("선호: " + ", ".join(request.preferences))
    return " · ".join(parts)
