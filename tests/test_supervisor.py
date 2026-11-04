from travel_agent.agents.planner import ground_itinerary
from travel_agent.agents.supervisor import (
    MAX_STEPS,
    SupervisorDecision,
    allowed_workers,
    enforce,
)
from travel_agent.state import Activity, Candidate, DayPlan, Itinerary, TripRequest


def cand(name: str, approved: bool = True) -> Candidate:
    return Candidate(
        place=name,
        city="jeju",
        category="sight",
        summary_ko="",
        reason_ko="",
        source_url=f"https://example.org/{name}",
        approved=approved,
    )


def itinerary(days: int) -> Itinerary:
    act = Activity(start="10:00", end="11:00", title="t", place="A", category="sight")
    other = Activity(start="11:00", end="12:00", title="t", place="숙소", category="rest")
    return Itinerary(
        title="x",
        theme="family",
        days=[DayPlan(day=i, summary="s", activities=[act, other]) for i in range(1, days + 1)],
    )


FAMILY = TripRequest(destination="jeju", days=2, theme="family", children_ages=[5])
HEALING = TripRequest(destination="bali", days=2, theme="healing")


def test_research_is_the_only_option_until_candidates_are_confirmed():
    assert allowed_workers({"request": FAMILY}) == ["research"]
    state = {"request": FAMILY, "candidates": [cand("A")], "candidates_confirmed": False}
    assert allowed_workers(state) == ["research"]


def test_planner_matches_theme_after_confirmation():
    base = {"candidates": [cand("A")], "candidates_confirmed": True}
    assert allowed_workers({**base, "request": FAMILY}) == ["family_planner"]
    assert allowed_workers({**base, "request": HEALING}) == ["healing_planner"]


def test_finish_after_itinerary_or_when_nothing_to_plan_or_at_step_limit():
    done = {"request": FAMILY, "candidates": [cand("A")], "candidates_confirmed": True}
    assert allowed_workers({**done, "itinerary": itinerary(2)}) == ["finish"]
    empty = {
        "request": FAMILY,
        "candidates": [cand("A", approved=False)],
        "candidates_confirmed": True,
    }
    assert allowed_workers(empty) == ["finish"]
    assert allowed_workers({"request": FAMILY, "step_count": MAX_STEPS}) == ["finish"]


def test_enforce_overrides_illegal_choice_and_keeps_brief():
    decision = SupervisorDecision(next="healing_planner", brief="keep it short", reason="r")
    fixed = enforce(decision, ["family_planner"])
    assert fixed.next == "family_planner" and fixed.brief == "keep it short"
    assert enforce(decision, ["healing_planner"]) is decision


def test_ground_itinerary_attaches_sources_and_flags_day_mismatch():
    grounded = ground_itinerary(itinerary(1), [cand("A")], days=2)
    acts = grounded.days[0].activities
    assert acts[0].source_url == "https://example.org/A"
    assert acts[1].source_url is None  # generic place, not a candidate
    assert any("2일" in w for w in grounded.warnings)
    assert ground_itinerary(itinerary(2), [cand("A")], days=2).warnings == []


def test_ground_itinerary_moves_model_warnings_into_tips():
    it = itinerary(2).model_copy(update={"tips": ["tip"], "warnings": ["book ahead"]})
    grounded = ground_itinerary(it, [cand("A")], days=2)
    assert grounded.tips == ["tip", "book ahead"]
    assert grounded.warnings == []
