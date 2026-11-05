from types import SimpleNamespace

from travel_agent import evaluation as ev
from travel_agent.evaluation import (
    SCENARIOS,
    Case,
    Result,
    Scenario,
    evidence_found,
    exact_violations,
    load_cases,
    parse_tools,
    pass_rates,
    run_case,
    score,
    split_items,
)


def case(**kw) -> Case:
    base = dict(
        id="X-01",
        category="positive",
        input="hi",
        expected_traits=[],
        forbidden=[],
        expected_tools=[],
        note="",
    )
    return Case(**{**base, **kw})


def test_split_and_tools_parsing():
    assert split_items(" a ; b;; c ") == ["a", "b", "c"]
    assert parse_tools("none") == [] and parse_tools("") == []
    assert parse_tools("search_places; estimate_travel_time") == [
        "search_places",
        "estimate_travel_time",
    ]


def test_real_query_file_loads_and_scenarios_match_its_ids():
    cases = load_cases()
    assert len(cases) == 12
    assert {c.category for c in cases} == {"positive", "negative", "edge", "guardrail"}
    assert set(SCENARIOS) <= {c.id for c in cases}  # no script for a missing case
    assert all(c.forbidden for c in cases)


def test_exact_violation_and_evidence_matching_ignore_whitespace():
    answer = "1. Bar X (meal): 늦게까지  여는\n 바"
    assert exact_violations(["늦게까지 여는 바"], answer) == [
        "늦게까지 여는 바 => (문구 그대로 등장)"
    ]
    assert exact_violations(["카지노"], answer) == []
    assert evidence_found("Bar X (meal)", answer)
    assert not evidence_found("Casino Y", answer)
    assert not evidence_found(" ", answer)


def test_score_tools_and_forbidden_without_judge():
    c = case(expected_tools=["search_places"], forbidden=["카지노"])
    ok = score(
        Result(
            id="X-01", category="positive", input="", called_tools=["search_places"], answer="해변"
        ),
        c,
        use_judge=False,
    )
    assert ok.passed and ok.tools_status == "pass"
    missing = score(
        Result(id="X-01", category="positive", input="", answer="해변"), c, use_judge=False
    )
    assert not missing.passed and missing.tools_status == "fail"
    bad = score(
        Result(
            id="X-01",
            category="positive",
            input="",
            called_tools=["search_places"],
            answer="카지노 투어",
        ),
        c,
        use_judge=False,
    )
    assert not bad.passed and bad.violations
    errored = score(
        Result(id="X-01", category="positive", input="", error="boom"), case(), use_judge=False
    )
    assert not errored.passed


def test_pass_rates_by_category_and_overall():
    rs = [
        Result(id="a", category="positive", input="", passed=True),
        Result(id="b", category="positive", input="", passed=False),
        Result(id="c", category="guardrail", input="", passed=True),
    ]
    assert pass_rates(rs) == {"positive": (1, 2), "guardrail": (1, 1), "전체": (2, 3)}


# --- run_case with a fake graph (no LLM) ----------------------------------------------------


class FakeGraph:
    """Pauses in a fixed order; records what was sent."""

    def __init__(self, pauses):
        self.pauses = list(pauses)  # pause types after each send; "end" = finished
        self.sent = []
        self.pos = -1

    def stream(self, payload, cfg, stream_mode):
        self.sent.append(payload)
        self.pos += 1
        yield "custom", {"event": "tool_call", "name": "search_places", "args": {"query": "q"}}

    def get_state(self, cfg):
        kind = self.pauses[self.pos] if self.pos >= 0 else "start"
        if kind == "end":
            return SimpleNamespace(next=(), interrupts=(), values={"saved_paths": []})
        value = {
            "type": kind,
            "question": "몇 살?",
            "candidates": [],
            "notice": None,
            "itinerary": {"title": "t", "days": [], "tips": [], "warnings": []},
        }
        return SimpleNamespace(next=("x",), interrupts=(SimpleNamespace(value=value),), values={})


def test_run_case_follows_script_and_records_only_after_input(monkeypatch):
    monkeypatch.setitem(
        ev.SCENARIOS,
        "X-01",
        Scenario(
            pre=[("start", "부산 1박2일")], input_at="ask", post=[("approve_candidates", "좋아요")]
        ),
    )
    graph = FakeGraph(["ask", "approve_candidates", "approve_itinerary"])
    r = run_case(graph, case(input="5살"), "t")
    assert len(graph.sent) == 3 and not r.error
    assert r.called_tools == ["search_places", "search_places"]  # setup call not counted
    assert r.answer.startswith("[입력] 5살") and "[입력] 좋아요" in r.answer
    assert r.stopped_at == "approve_itinerary"


def test_run_case_setup_failure_is_an_error(monkeypatch):
    monkeypatch.setitem(ev.SCENARIOS, "X-01", Scenario(pre=[("start", "부산")], input_at="ask"))
    r = run_case(FakeGraph(["approve_candidates"]), case(), "t")
    assert r.error.startswith("SetupError")


def test_run_case_stops_when_post_script_does_not_match(monkeypatch):
    monkeypatch.setitem(ev.SCENARIOS, "X-01", Scenario(post=[("ask", "힐링")]))
    graph = FakeGraph(["end"])
    r = run_case(graph, case(), "t")
    assert len(graph.sent) == 1 and not r.error
    assert r.stopped_at == "end" and any("대본 중단" in n for n in r.notes)


# --- scorer fixes: input excluded, richer rendering ---------------------------------------


def test_traveller_input_is_never_evidence():
    from travel_agent.evaluation import agent_output

    answer = "[입력] 주민번호 900101-1234567\n[조건] 제주 · 2박 3일"
    assert agent_output(answer) == "[조건] 제주 · 2박 3일"
    assert not evidence_found("900101-1234567", answer)  # only typed by the traveller
    assert exact_violations(["900101-1234567"], answer) == []
    echoed = answer + "\n[질문] 900101-1234567 맞나요?"
    assert evidence_found("900101-1234567", echoed)  # the agent repeated it


def test_tool_calls_render_their_arguments():
    from travel_agent.evaluation import render_event

    travel = render_event(
        {
            "event": "tool_call",
            "name": "estimate_travel_time",
            "args": {"city": "okinawa", "transport": "transit", "place_a": "A", "place_b": "B"},
        }
    )
    assert travel == "[도구] estimate_travel_time(city=okinawa, transport=transit): A ↔ B"
    search = render_event(
        {
            "event": "tool_call",
            "name": "search_places",
            "args": {"city": "jeju", "theme": "family", "query": "해변"},
        }
    )
    assert search == "[도구] search_places(city=jeju, theme=family): 해변"


def test_candidate_lines_show_their_source():
    from travel_agent.evaluation import render_pause

    cand = {"place": "P", "category": "sight", "summary_ko": "s", "reason_ko": "r"}
    value = {
        "type": "approve_candidates",
        "notice": None,
        "candidates": [{**cand, "source_url": "https://x/y"}, {**cand, "source_url": ""}],
    }
    snap = SimpleNamespace(next=("x",), interrupts=(SimpleNamespace(value=value),), values={})
    _, text = render_pause(snap)
    assert "[출처: https://x/y]" in text and "[출처: 출처 없음]" in text


def test_scope_mismatch_rejects_evidence_outside_named_time_or_day():
    from travel_agent.evaluation import scope_mismatch

    answer = "\n".join(
        [
            "1일차 — a",
            "  10:00-11:00 [sight] 해변 @ Beach",
            "2일차 — b",
            "  10:30-11:30 [sight] 전망대 @ Tower",
            "  13:50-15:20 [activity] 오픈탑 버스 @ Bus",
        ]
    )
    item = "2일차 오전에 해변·공원·옥상·오픈탑 버스 같은 야외 활동"
    assert "시간대" in scope_mismatch(item, "13:50-15:20 [activity] 오픈탑 버스 @ Bus", answer)
    assert "1일차" in scope_mismatch(item, "10:00-11:00 [sight] 해변 @ Beach", answer)
    assert scope_mismatch(item, "10:30-11:30 [sight] 전망대 @ Tower", answer) is None
    # items without a time or day, and evidence without a time, are not restricted
    assert scope_mismatch("카지노 후보", "13:50-15:20 [activity] 오픈탑 버스 @ Bus", answer) is None
    assert scope_mismatch(item, "[팁] 해변 조심", answer) is None
