"""Deterministic guardrails (SERVICE.md 16절 decisions on GRD-01 / GRD-02).

- Off-topic requests: nightlife, gambling, and adult venues are not supported. Such
  preferences are dropped and such places never become candidates.
- Personal data: resident registration numbers, card numbers, phone numbers, and e-mail
  addresses are masked before text enters the graph, so they are never stored.
- Booking: not supported; the traveller is told so and planning continues.

All checks are plain code (no LLM), so they also hold against "ignore previous
instructions" style prompts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

# --- off-topic requests / places ------------------------------------------------------------

# Request/preference words (Korean and English). Matched as substrings, lower-cased.
# Kept specific on purpose: "나이트" alone would hit "나이트 사파리" (a family attraction)
# and "성인" alone would hit "성인 2명" (a party description).
BLOCKED_REQUEST_TERMS = (
    "카지노",
    "도박",
    "유흥",
    "클럽",
    "나이트클럽",
    "나이트라이프",
    "술집",
    "룸살롱",
    "성인용",
    "성인 쇼",
    "19금",
    "홍등가",
    "고고바",
    "casino",
    "gambl",
    "nightclub",
    "night club",
    "nightlife",
    "go-go",
    "gogo",
    "strip club",
    "red light",
    "red-light",
    "brothel",
    "adult show",
)

# Place words that are never suitable, in any guide section.
BLOCKED_PLACE_PATTERN = re.compile(
    r"casino|gambl|nightclub|night club|go-?go|strip club|red[- ]light|brothel|cabaret"
    r"|adult show|ping ?pong show|카지노|나이트클럽|유흥|클럽",
    re.IGNORECASE,
)
# Wikivoyage's "Drink" section mixes bars/clubs with cafés. Keep the cafés.
CAFE_PATTERN = re.compile(
    r"caf[eé]|coffee|\btea\b|teahouse|tea house|bakery|juice|dessert|카페|커피|찻집",
    re.IGNORECASE,
)
NIGHTLIFE_NAME_PATTERN = re.compile(
    r"\bbar\b|\bpub\b|club|night|cocktail|beer|brew|wine|whisk|izakaya|lounge|go-?go|sake",
    re.IGNORECASE,
)

OFF_TOPIC_NOTICE = (
    "유흥·도박·성인 관련 요청은 도와드리지 않아요. 가족·힐링 여행에 맞는 장소로 계획할게요."
)


def is_blocked_request(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in BLOCKED_REQUEST_TERMS)


def filter_preferences(preferences: list[str]) -> tuple[list[str], list[str]]:
    """Split preferences into (kept, removed) by the off-topic word list."""
    kept = [p for p in preferences if not is_blocked_request(p)]
    removed = [p for p in preferences if is_blocked_request(p)]
    return kept, removed


def is_blocked_place(meta: Mapping, summary: str = "") -> bool:
    """True if an indexed listing is a bar/club/casino/adult venue (cafés are allowed)."""
    place = str(meta.get("place", ""))
    if BLOCKED_PLACE_PATTERN.search(f"{place} {summary}"):
        return True
    if str(meta.get("section", "")).lower() == "drink":
        looks_like_cafe = CAFE_PATTERN.search(f"{place} {summary}")
        return not looks_like_cafe or bool(NIGHTLIFE_NAME_PATTERN.search(place))
    return False


# --- personal data ---------------------------------------------------------------------------

# Digit lookarounds instead of \b: Korean letters count as word characters, so \b would not
# match between "1234567" and a following particle like "인데".
PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "주민등록번호": re.compile(r"(?<!\d)\d{6}\s*-\s*[1-8]\d{6}(?!\d)"),
    "카드번호": re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)"),
    "전화번호": re.compile(r"(?<!\d)01[016789][\s-]?\d{3,4}[\s-]?\d{4}(?!\d)"),
    "이메일": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"),
}
MASK = "•••"

BOOKING_PATTERN = re.compile(r"예약|예매|booking|book a|reserve|reservation", re.IGNORECASE)
BOOKING_NOTICE = "숙소·항공·입장권 예약은 지원하지 않아요. 일정만 짜 드릴게요."


def mask_pii(text: str) -> tuple[str, list[str]]:
    """Replace personal data with a mask. Returns (masked text, kinds found)."""
    found = []
    for kind, pattern in PII_PATTERNS.items():
        text, count = pattern.subn(MASK, text)
        if count:
            found.append(kind)
    return text, found


@dataclass
class GuardedInput:
    text: str  # what may enter the graph (personal data masked)
    notices: list[str] = field(default_factory=list)  # shown to the traveller right away


def guard_input(text: str) -> GuardedInput:
    """Run every traveller message through this before it reaches the graph."""
    masked, kinds = mask_pii(text)
    notices = []
    if kinds:
        notices.append(
            f"개인정보({', '.join(kinds)})는 여행 계획에 필요하지 않아 가리고 저장하지 않았어요."
        )
    if BOOKING_PATTERN.search(text):
        notices.append(BOOKING_NOTICE)
    if is_blocked_request(text):
        notices.append(OFF_TOPIC_NOTICE)
    return GuardedInput(text=masked, notices=notices)
