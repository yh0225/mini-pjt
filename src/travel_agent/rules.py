"""Theme rule checks (SERVICE.md section 8). Pure functions: no LLM, no I/O."""

from __future__ import annotations

from pydantic import BaseModel

from travel_agent.state import Activity, DayPlan, Itinerary, TripRequest

# Family
FAMILY_MAX_MOVES = 3
NAP_MAX_AGE = 6
NAP_MIN_MINUTES = 60
NAP_START_WINDOW = ("12:00", "15:00")  # nap/rest block must start in this window
FAMILY_DAY_END = "20:00"
LUNCH_WINDOW = ("11:30", "13:00")
# Healing
HEALING_MAX_MAIN = 3
HEALING_MIN_GAP = 30
FREE_TIME_MIN_MINUTES = 60
# A day "has an afternoon" (nap / free time required) if it lasts until at least this time.
FULL_DAY_UNTIL = "16:00"
# A day "covers lunch" if it starts by and lasts until these times.
LUNCH_DAY_START_BY, LUNCH_DAY_UNTIL = "12:00", "13:30"

MAIN_CATEGORIES = ("sight", "activity")


class Violation(BaseModel):
    day: int | None  # None = whole itinerary
    rule: str
    message: str  # Korean, shown to the traveller and given to the planner


def to_minutes(hhmm: str) -> int | None:
    try:
        h, m = hhmm.split(":")
        value = int(h) * 60 + int(m)
    except ValueError:
        return None
    return value if 0 <= value <= 24 * 60 else None


def duration(a: Activity) -> int:
    start, end = to_minutes(a.start), to_minutes(a.end)
    return 0 if start is None or end is None else end - start


def day_span(day: DayPlan) -> tuple[int, int] | None:
    times = [(to_minutes(a.start), to_minutes(a.end)) for a in day.activities]
    if not times or any(s is None or e is None for s, e in times):
        return None
    return min(s for s, _ in times), max(e for _, e in times)


# --- common -----------------------------------------------------------------------------


def check_structure(itinerary: Itinerary, request: TripRequest) -> list[Violation]:
    out: list[Violation] = []
    if request.days and len(itinerary.days) != request.days:
        out.append(
            Violation(
                day=None,
                rule="day_count",
                message=f"{request.days}일 일정이어야 하는데 {len(itinerary.days)}일이에요.",
            )
        )
    for day in itinerary.days:
        prev_end: int | None = None
        for a in day.activities:
            start, end = to_minutes(a.start), to_minutes(a.end)
            if start is None or end is None or end <= start:
                out.append(
                    Violation(
                        day=day.day,
                        rule="time_format",
                        message=f"'{a.title}'의 시간({a.start}-{a.end})이 올바르지 않아요.",
                    )
                )
                continue
            if prev_end is not None and start < prev_end:
                out.append(
                    Violation(
                        day=day.day,
                        rule="time_overlap",
                        message=f"'{a.title}'({a.start})이 앞 일정과 시간이 겹쳐요.",
                    )
                )
            prev_end = end
    return out


# --- family -----------------------------------------------------------------------------


def check_family_day(day: DayPlan, request: TripRequest) -> list[Violation]:
    out: list[Violation] = []
    span = day_span(day)
    if span is None:
        return out
    first, last = span

    moves = sum(a.category == "move" for a in day.activities)
    if moves > FAMILY_MAX_MOVES:
        out.append(
            Violation(
                day=day.day,
                rule="family_max_moves",
                message=f"이동이 {moves}번이에요. 아이와 함께라면 하루 {FAMILY_MAX_MOVES}번 이하로 "
                "줄여 주세요.",
            )
        )

    if last > to_minutes(FAMILY_DAY_END):
        out.append(
            Violation(
                day=day.day,
                rule="family_day_end",
                message=f"일정이 {FAMILY_DAY_END} 이후까지 이어져요. {FAMILY_DAY_END} 전에 "
                "끝내 주세요.",
            )
        )

    young = request.children_ages and min(request.children_ages) <= NAP_MAX_AGE
    if young and last >= to_minutes(FULL_DAY_UNTIL):
        lo, hi = (to_minutes(t) for t in NAP_START_WINDOW)
        has_nap = any(
            a.category == "rest"
            and duration(a) >= NAP_MIN_MINUTES
            and lo <= to_minutes(a.start) <= hi
            for a in day.activities
        )
        if not has_nap:
            out.append(
                Violation(
                    day=day.day,
                    rule="family_nap",
                    message=f"{NAP_START_WINDOW[0]}~{NAP_START_WINDOW[1]} 사이에 시작하는 "
                    f"{NAP_MIN_MINUTES}분 이상의 낮잠·휴식 시간이 필요해요.",
                )
            )

    if first <= to_minutes(LUNCH_DAY_START_BY) and last >= to_minutes(LUNCH_DAY_UNTIL):
        lo, hi = (to_minutes(t) for t in LUNCH_WINDOW)
        has_lunch = any(
            a.category == "meal" and lo <= to_minutes(a.start) <= hi for a in day.activities
        )
        if not has_lunch:
            out.append(
                Violation(
                    day=day.day,
                    rule="family_lunch",
                    message=f"점심 식사가 {LUNCH_WINDOW[0]}~{LUNCH_WINDOW[1]} 사이에 "
                    "시작해야 해요.",
                )
            )
    return out


# --- healing ----------------------------------------------------------------------------


def check_healing_day(day: DayPlan) -> list[Violation]:
    out: list[Violation] = []
    span = day_span(day)
    if span is None:
        return out
    _, last = span

    mains = [a for a in day.activities if a.category in MAIN_CATEGORIES]
    if len(mains) > HEALING_MAX_MAIN:
        out.append(
            Violation(
                day=day.day,
                rule="healing_max_main",
                message=f"주요 활동이 {len(mains)}개예요. 힐링 여행은 하루 {HEALING_MAX_MAIN}개 "
                "이하로 해 주세요.",
            )
        )

    for prev, nxt in zip(mains, mains[1:], strict=False):
        gap = to_minutes(nxt.start) - to_minutes(prev.end)
        if gap < HEALING_MIN_GAP:
            out.append(
                Violation(
                    day=day.day,
                    rule="healing_gap",
                    message=f"'{prev.title}'과 '{nxt.title}' 사이 여유가 {gap}분이에요. "
                    f"{HEALING_MIN_GAP}분 이상 쉬어 가게 해 주세요.",
                )
            )

    if last >= to_minutes(FULL_DAY_UNTIL):
        has_free = any(
            a.category == "rest" and duration(a) >= FREE_TIME_MIN_MINUTES for a in day.activities
        )
        if not has_free:
            out.append(
                Violation(
                    day=day.day,
                    rule="healing_free_time",
                    message=f"{FREE_TIME_MIN_MINUTES}분 이상의 자유 시간(휴식)이 필요해요.",
                )
            )
    return out


def check_itinerary(itinerary: Itinerary, request: TripRequest) -> list[Violation]:
    violations = check_structure(itinerary, request)
    for day in itinerary.days:
        if request.theme == "family":
            violations += check_family_day(day, request)
        elif request.theme == "healing":
            violations += check_healing_day(day)
    return violations
