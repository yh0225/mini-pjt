from travel_agent.agents.critic import days_to_redo, format_violation
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
    assert merged.title == "old"  # untouched trip-level fields stay


def test_merge_days_keeps_old_day_when_new_plan_lacks_it():
    old, new = plan("old", [1, 2]), plan("new", [1])
    merged, missing = merge_days(old, new, [1, 2])
    assert [d.summary for d in merged.days] == ["new", "old"]
    assert missing == [2]
