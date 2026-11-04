"""Write the final itinerary to output/<thread_id>/itinerary.{json,md}."""

from __future__ import annotations

import json
from pathlib import Path

from travel_agent.config import OUTPUT_DIR
from travel_agent.state import Candidate, Itinerary, TripRequest
from travel_agent.ui import describe_request_ko

CATEGORY_KO = {"sight": "관광", "activity": "체험", "meal": "식사", "rest": "휴식", "move": "이동"}
LICENSE_NOTE = (
    "장소 정보는 [위키보이지(Wikivoyage)](https://www.wikivoyage.org/)를 바탕으로 하며, "
    "[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) 라이선스를 따릅니다. "
    "요약·번역은 AI가 작성했으므로 방문 전 운영 시간 등을 확인해 주세요."
)


def sources(itinerary: Itinerary, candidates: list[Candidate]) -> list[tuple[str, str]]:
    """(place, url) pairs for every candidate actually used, in order of first use."""
    by_name = {c.place: c for c in candidates}
    seen: dict[str, str] = {}
    for day in itinerary.days:
        for a in day.activities:
            url = a.source_url or (by_name[a.place].source_url if a.place in by_name else "")
            if url and a.place not in seen:
                seen[a.place] = url
    return list(seen.items())


def to_markdown(itinerary: Itinerary, request: TripRequest, candidates: list[Candidate]) -> str:
    lines = [f"# {itinerary.title}", "", f"> {describe_request_ko(request)}", ""]
    if itinerary.warnings:
        lines += ["> [!WARNING]", *[f"> - {w}" for w in itinerary.warnings], ""]
    for day in itinerary.days:
        lines += [f"## {day.day}일차 — {day.summary}", "", "| 시간 | 구분 | 일정 | 장소 | 메모 |"]
        lines.append("|---|---|---|---|---|")
        for a in day.activities:
            cells = [
                f"{a.start}-{a.end}",
                CATEGORY_KO.get(a.category, a.category),
                a.title,
                a.place,
                a.note or "",
            ]
            lines.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
        lines.append("")
    if itinerary.tips:
        lines += ["## 여행 팁", "", *[f"- {t}" for t in itinerary.tips], ""]
    used = sources(itinerary, candidates)
    lines += ["## 출처", ""]
    lines += [f"- [{place}]({url})" for place, url in used] or ["- (없음)"]
    lines += ["", LICENSE_NOTE, ""]
    return "\n".join(lines)


def save_itinerary(
    itinerary: Itinerary,
    request: TripRequest,
    candidates: list[Candidate],
    thread_id: str,
    base_dir: Path = OUTPUT_DIR,
) -> list[Path]:
    out_dir = base_dir / thread_id
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "request": request.model_dump(mode="json"),
        "itinerary": itinerary.model_dump(mode="json"),
        "sources": [{"place": p, "url": u} for p, u in sources(itinerary, candidates)],
    }
    json_path = out_dir / "itinerary.json"
    md_path = out_dir / "itinerary.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(to_markdown(itinerary, request, candidates), encoding="utf-8")
    return [json_path, md_path]
