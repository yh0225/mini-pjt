"""Write the final itinerary to output/<thread_id>/itinerary.{json,md,pdf}."""

from __future__ import annotations

import html
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from travel_agent.config import (
    OUTPUT_DIR,
    PDF_BROWSER,
    PDF_BROWSER_DEFAULTS,
    PDF_TIMEOUT_SECONDS,
)
from travel_agent.state import Candidate, Itinerary, TripRequest
from travel_agent.ui import describe_request_ko

CATEGORY_KO = {"sight": "관광", "activity": "체험", "meal": "식사", "rest": "휴식", "move": "이동"}
LICENSE_NOTE = (
    "장소 정보는 [위키보이지(Wikivoyage)](https://www.wikivoyage.org/)를 바탕으로 하며, "
    "[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) 라이선스를 따릅니다. "
    "요약·번역은 AI가 작성했으므로 방문 전 운영 시간 등을 확인해 주세요."
)
LICENSE_NOTE_HTML = (
    '장소 정보는 <a href="https://www.wikivoyage.org/">위키보이지(Wikivoyage)</a>를 바탕으로 '
    '하며, <a href="https://creativecommons.org/licenses/by-sa/4.0/">CC BY-SA 4.0</a> '
    "라이선스를 따릅니다. 요약·번역은 AI가 작성했으므로 방문 전 운영 시간 등을 확인해 주세요."
)
PDF_CSS = """
@page { size: A4 landscape; margin: 12mm; }
body { font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', 'Noto Sans CJK KR', sans-serif;
       color: #222; font-size: 10pt; }
h1 { font-size: 18pt; margin: 0 0 4px; color: #1f4e79; }
h2 { font-size: 12pt; margin: 18px 0 6px; color: #1f4e79; border-bottom: 2px solid #1f4e79;
     padding-bottom: 3px; break-after: avoid; }
.meta { color: #555; margin: 0 0 10px; }
.warn { background: #fff4e5; border-left: 4px solid #e69500; padding: 6px 10px; margin: 8px 0; }
.warn ul { margin: 4px 0 0; padding-left: 18px; }
table { width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 9pt; }
th { background: #1f4e79; color: #fff; padding: 5px; text-align: left; }
td { border-bottom: 1px solid #ddd; padding: 4px 5px; vertical-align: top; }
tr { break-inside: avoid; }
tr.move td { color: #777; background: #f6f6f6; }
col.time { width: 11%; } col.cat { width: 6%; } col.title { width: 25%; } col.place { width: 18%; }
a { color: #1f4e79; text-decoration: none; }
.license { color: #666; font-size: 8.5pt; }
"""

log = logging.getLogger(__name__)


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


def to_html(itinerary: Itinerary, request: TripRequest, candidates: list[Candidate]) -> str:
    """Printable HTML (A4 landscape) with the same content as the Markdown file."""
    e = html.escape
    parts = [
        f"<h1>{e(itinerary.title)}</h1>",
        f'<p class="meta">{e(describe_request_ko(request))}</p>',
    ]
    if itinerary.warnings:
        items = "".join(f"<li>{e(w)}</li>" for w in itinerary.warnings)
        parts.append(f'<div class="warn"><strong>⚠️ 주의</strong><ul>{items}</ul></div>')
    for day in itinerary.days:
        parts.append(f"<h2>{day.day}일차 — {e(day.summary)}</h2>")
        rows = []
        for a in day.activities:
            cells = [
                f"{a.start}-{a.end}",
                CATEGORY_KO.get(a.category, a.category),
                a.title,
                a.place,
                a.note or "",
            ]
            cls = ' class="move"' if a.category == "move" else ""
            rows.append(f"<tr{cls}>" + "".join(f"<td>{e(c)}</td>" for c in cells) + "</tr>")
        parts.append(
            '<table><colgroup><col class="time"><col class="cat"><col class="title">'
            '<col class="place"><col></colgroup>'
            "<thead><tr><th>시간</th><th>구분</th><th>일정</th><th>장소</th><th>메모</th></tr>"
            f"</thead><tbody>{''.join(rows)}</tbody></table>"
        )
    if itinerary.tips:
        parts.append(
            "<h2>여행 팁</h2><ul>" + "".join(f"<li>{e(t)}</li>" for t in itinerary.tips) + "</ul>"
        )
    used = sources(itinerary, candidates)
    links = [f'<li><a href="{e(url)}">{e(place)}</a></li>' for place, url in used] or [
        "<li>(없음)</li>"
    ]
    parts.append("<h2>출처</h2><ul>" + "".join(links) + "</ul>")
    parts.append(f'<p class="license">{LICENSE_NOTE_HTML}</p>')
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        f"<title>{e(itinerary.title)}</title><style>{PDF_CSS}</style></head>"
        f"<body>{''.join(parts)}</body></html>"
    )


def find_browser() -> str | None:
    """PDF_BROWSER if set, else the first installed default browser."""
    for candidate in [PDF_BROWSER] if PDF_BROWSER else PDF_BROWSER_DEFAULTS:
        if Path(candidate).is_file():
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    return None


def write_pdf(html_text: str, pdf_path: Path) -> bool:
    """Print HTML to PDF with a headless browser. Returns False (and logs) on any failure."""
    browser = find_browser()
    if browser is None:
        log.warning("PDF skipped: no Edge/Chrome/Chromium found (set PDF_BROWSER)")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "itinerary.html"
        html_path.write_text(html_text, encoding="utf-8")
        cmd = [
            browser,
            "--headless",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--user-data-dir={Path(tmp) / 'profile'}",
            f"--print-to-pdf={pdf_path.resolve()}",
            html_path.as_uri(),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=PDF_TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("PDF skipped: browser failed (%s)", exc)
            return False
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        log.warning("PDF skipped: browser produced no file")
        return False
    return True


def save_itinerary(
    itinerary: Itinerary,
    request: TripRequest,
    candidates: list[Candidate],
    thread_id: str,
    base_dir: Path = OUTPUT_DIR,
    pdf: bool = True,
) -> list[Path]:
    """Write JSON and Markdown; also PDF when ``pdf`` and a browser is available."""
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
    paths = [json_path, md_path]
    pdf_path = out_dir / "itinerary.pdf"
    if pdf and write_pdf(to_html(itinerary, request, candidates), pdf_path):
        paths.append(pdf_path)
    return paths
