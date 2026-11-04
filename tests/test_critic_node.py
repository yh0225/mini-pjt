"""critic_node decision logic, with the rule/LLM checks stubbed out."""

import pytest

from travel_agent import graph
from travel_agent.rules import Violation
from travel_agent.state import Activity, Candidate, DayPlan, Itinerary, TripRequest

REQUEST = TripRequest(destination="jeju", days=3, theme="family", children_ages=[4])
CAND = Candidate(place="A", city="jeju", category="sight", summary_ko="", reason_ko="")


def plan() -> Itinerary:
    act = Activity(start="10:00", end="11:00", title="t", place="A", category="sight")
    days = [DayPlan(day=d, summary="s", activities=[act]) for d in (1, 2, 3)]
    return Itinerary(title="t", theme="family", days=days)


@pytest.fixture
def stub(monkeypatch):
    events = []
    monkeypatch.setattr(graph, "get_stream_writer", lambda: events.append)

    def set_violations(violations):
        monkeypatch.setattr(graph, "critique", lambda *_: violations)
        return events

    return set_violations


def state(retry: int) -> dict:
    return {"itinerary": plan(), "request": REQUEST, "candidates": [CAND], "retry_count": retry}


def test_violations_with_retries_left_send_only_those_days_back(stub):
    stub(
        [
            Violation(day=2, rule="r", message="이동이 많아요"),
            Violation(day=2, rule="x", message="m"),
        ]
    )
    out = graph.critic_node(state(retry=0))
    assert out["revise_days"] == [2]
    assert out["retry_count"] == 1
    assert out["itinerary_checked"] is False
    assert "2일차: 이동이 많아요" in out["revise_reason"]


def test_out_of_retries_turns_violations_into_warnings(stub):
    events = stub([Violation(day=3, rule="r", message="낮잠이 없어요")])
    out = graph.critic_node(state(retry=graph.MAX_RETRIES))
    assert out["itinerary_checked"] is True
    assert out["revise_days"] == []
    assert out["itinerary"].warnings == ["3일차: 낮잠이 없어요"]
    assert events[-1]["event"] == "critic"


def test_clean_plan_clears_old_warnings(stub):
    stub([])
    s = state(retry=1)
    s["itinerary"] = s["itinerary"].model_copy(update={"warnings": ["old"]})
    out = graph.critic_node(s)
    assert out["itinerary_checked"] is True
    assert out["itinerary"].warnings == []
