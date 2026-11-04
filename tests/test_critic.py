from travel_agent.agents.critic import (
    RequestCheck,
    Review,
    ReviewIssue,
    activities_to_judge,
    days_to_redo,
    format_violation,
    request_windows,
    review_input,
    to_violations,
)
from travel_agent.agents.planner import merge_days
from travel_agent.rules import Violation
from travel_agent.state import Activity, DayPlan, Itinerary


def v(day):
    return Violation(day=day, rule="r", message="m")


def plan(tag: str, days: list[int]) -> Itinerary:
    act = Activity(start="10:00", end="11:00", title=tag, place="p", category="sight")
    return Itinerary(
        title=tag,
        theme="family",
        days=[DayPlan(day=d, summary=tag, activities=[act]) for d in days],
        tips=[f"{tag}-tip"],
    )


def test_days_to_redo_collects_violation_days():
    assert days_to_redo([v(3), v(1), v(3)], [1, 2, 3]) == [1, 3]
    assert days_to_redo([v(2), v(None)], [1, 2, 3]) == [1, 2, 3]
    assert days_to_redo([], [1, 2]) == []


def test_format_violation():
    assert format_violation(v(2)) == "2일차: m"
    assert format_violation(v(None)) == "m"


def test_merge_days_replaces_only_requested_days():
    old, new = plan("old", [1, 2, 3]), plan("new", [1, 2, 3])
    merged, missing = merge_days(old, new, [2])
    assert [d.summary for d in merged.days] == ["old", "new", "old"]
    assert missing == []
    assert merged.title == "old"  # the trip title stays
    assert merged.tips == ["new-tip"]  # tips follow the revised trip


def test_merge_days_keeps_old_tips_when_revision_has_none():
    old, new = plan("old", [1, 2]), plan("new", [1, 2]).model_copy(update={"tips": []})
    merged, _ = merge_days(old, new, [1])
    assert merged.tips == ["old-tip"]


def test_merge_days_keeps_old_day_when_new_plan_lacks_it():
    old, new = plan("old", [1, 2]), plan("new", [1])
    merged, missing = merge_days(old, new, [1, 2])
    assert [d.summary for d in merged.days] == ["new", "old"]
    assert missing == [2]


def day_plan(acts):
    return Itinerary(
        title="t",
        theme="family",
        days=[
            DayPlan(day=1, summary="s", activities=[acts[0]]),
            DayPlan(day=2, summary="s", activities=acts),
        ],
    )


def a(start, category, title):
    return Activity(start=start, end=start, title=title, place="p", category=category)


DAY2 = [
    a("09:00", "meal", "아침"),
    a("09:30", "move", "이동"),
    a("10:00", "sight", "백화점"),
    a("12:30", "rest", "낮잠"),
    a("14:00", "activity", "공원 놀이터"),
    a("19:00", "sight", "야경"),
]


def test_request_windows_from_time_words():
    assert request_windows("2일차 오전은 실내로") == [(0, 720)]
    assert request_windows("저녁은 쉬고 싶어") == [(1080, 1440)]
    assert request_windows("전체적으로 덜 걷게") == []


def test_activities_to_judge_filters_day_time_and_categories():
    it = day_plan(DAY2)
    morning = activities_to_judge(it, "오전은 실내로", [2])
    assert [x.title for x in morning[2]] == ["백화점"]  # meal/move skipped, afternoon ignored
    afternoon = activities_to_judge(it, "오후는 실내로", [2])
    assert [x.title for x in afternoon[2]] == ["낮잠", "공원 놀이터"]
    whole = activities_to_judge(it, "덜 걷게", [2])
    assert [x.title for x in whole[2]] == ["백화점", "낮잠", "공원 놀이터", "야경"]
    assert 1 not in morning  # only requested days


def test_review_input_lists_only_judged_activities():
    it = day_plan(DAY2)
    assert "change request" not in review_input(it, [])
    text = review_input(it, [], "2일차 오전은 실내로", [2])
    judged = text.split("judge ONLY these):")[1]
    assert "백화점" in judged and "공원 놀이터" not in judged


def test_request_violations_only_for_unsatisfied_requested_days():
    it = day_plan(DAY2)
    review = Review(
        issues=[ReviewIssue(kind="duplicate", day=9, message="중복")],
        request_checks=[
            RequestCheck(day=2, satisfied=False, feedback="백화점 대신 실내로"),
            RequestCheck(day=1, satisfied=False, feedback="not asked about"),
        ],
    )
    out = to_violations(review, it, "오전은 실내로", [2])
    assert [(v.rule, v.day) for v in out] == [("review", None), ("request", 2)]
    assert "오전은 실내로" in out[1].message
    satisfied = Review(request_checks=[RequestCheck(day=2, satisfied=True)])
    assert to_violations(satisfied, it, "오전은 실내로", [2]) == []
    # without a request, request checks are ignored entirely
    assert to_violations(review, it, "", []) == [out[0]]
