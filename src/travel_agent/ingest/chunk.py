"""Turn Wikivoyage wikitext into retrieval chunks.

Two kinds of chunks are produced:
- listing: one chunk per {{see}}/{{do}}/{{eat}}/... template (a concrete place, with coordinates)
- prose:   section text split with RecursiveCharacterTextSplitter (background context)
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal

import mwparserfromhell as mwp
from langchain_text_splitters import RecursiveCharacterTextSplitter
from mwparserfromhell.nodes import Heading, Template
from pydantic import BaseModel

PROSE_CHUNK_SIZE = 800
PROSE_CHUNK_OVERLAP = 100
MIN_PROSE_CHARS = 80

# Listing template name -> Candidate category. `sleep` is skipped (lodging is out of scope).
LISTING_CATEGORIES: dict[str, str] = {
    "see": "sight",
    "do": "activity",
    "eat": "meal",
    "drink": "meal",
    "buy": "shop",
}
LISTING_TEMPLATES = set(LISTING_CATEGORIES) | {"listing", "sleep"}

# English level-2 sections whose prose is not useful for itinerary planning.
SKIPPED_SECTIONS_EN = {"get in", "sleep", "connect", "go next", "talk", "contact"}

Kind = Literal["listing", "prose"]


class Chunk(BaseModel):
    id: str
    city: str
    lang: str
    source: str = "wikivoyage"
    url: str
    page: str
    section: str
    kind: Kind
    place: str | None = None
    category: str | None = None
    lat: float | None = None
    lon: float | None = None
    text: str


def page_url(lang: str, page: str) -> str:
    return f"https://{lang}.wikivoyage.org/wiki/{page.replace(' ', '_')}"


def _param(tpl: Template, key: str) -> str:
    if not tpl.has(key):
        return ""
    return tpl.get(key).value.strip_code().strip()


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _clean(text: str) -> str:
    text = re.sub(r"^[*#:;]+\s*", "", text, flags=re.MULTILINE)  # list markers
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _chunk_id(city: str, lang: str, page: str, kind: str, text: str, index: int) -> str:
    digest = hashlib.sha1(f"{page}|{kind}|{index}|{text}".encode()).hexdigest()[:12]
    return f"{city}:{lang}:{digest}"


def _listing_text(tpl: Template, category: str) -> tuple[str, str]:
    name = _param(tpl, "name")
    alt = _param(tpl, "alt")
    parts = [f"{name} ({alt})" if alt else name, f"[{category}]"]
    for key, label in [
        ("content", ""),
        ("description", ""),
        ("address", "Address: "),
        ("hours", "Hours: "),
        ("price", "Price: "),
    ]:
        value = _param(tpl, key)
        if value:
            parts.append(f"{label}{value}")
    return name, _clean(" ".join(parts))


def parse_page(city: str, lang: str, page: str, wikitext: str) -> list[Chunk]:
    """Parse one page into listing and prose chunks."""
    url = page_url(lang, page)
    code = mwp.parse(wikitext)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=PROSE_CHUNK_SIZE, chunk_overlap=PROSE_CHUNK_OVERLAP
    )
    chunks: list[Chunk] = []
    section = "Lead"
    buffer: list[str] = []

    def flush() -> None:
        if lang == "en" and section.lower() in SKIPPED_SECTIONS_EN:
            buffer.clear()
            return
        text = _clean(mwp.parse("".join(buffer)).strip_code())
        buffer.clear()
        if len(text) < MIN_PROSE_CHARS:
            return
        for piece in splitter.split_text(text):
            body = f"{page} — {section}\n{piece}"
            chunks.append(
                Chunk(
                    id=_chunk_id(city, lang, page, "prose", body, len(chunks)),
                    city=city,
                    lang=lang,
                    url=url,
                    page=page,
                    section=section,
                    kind="prose",
                    text=body,
                )
            )

    for node in code.nodes:
        if isinstance(node, Heading):
            title = node.title.strip_code().strip()
            if node.level == 2:
                flush()
                section = title
            else:
                buffer.append(f"\n{title}\n")
            continue

        if isinstance(node, Template):
            name = str(node.name).strip().lower()
            if name not in LISTING_TEMPLATES:
                continue  # drop banners, maps, and other non-content templates
            kind = _param(node, "type").lower() if name == "listing" else name
            category = LISTING_CATEGORIES.get(kind)
            if category is None:
                continue  # sleep listings and unknown types
            place, text = _listing_text(node, category)
            if not place:
                continue
            chunks.append(
                Chunk(
                    id=_chunk_id(city, lang, page, "listing", text, len(chunks)),
                    city=city,
                    lang=lang,
                    url=url,
                    page=page,
                    section=section,
                    kind="listing",
                    place=place,
                    category=category,
                    lat=_to_float(_param(node, "lat")),
                    lon=_to_float(_param(node, "long")),
                    text=text,
                )
            )
            continue

        buffer.append(str(node))

    flush()
    return chunks
