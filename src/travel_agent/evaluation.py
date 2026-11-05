"""Run evaluation/test_queries.csv against the agent and score each case.

Per case:
- tools:     every tool in `expected_tools` must be called after the input ("none" = no check)
- forbidden: no forbidden item may appear in the agent output — exact text match, plus (unless
             --no-judge) an LLM judge whose verdict only counts if its evidence quote is found
             verbatim in the agent output (not the traveller input) and lies in the day / time
             of day the item names
- traits:    not scored; written next to the answer in review.md for a human

A case passes when the run finished without error, the tools check passed, and no
forbidden item was violated.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel, Field

from travel_agent.config import ROOT_DIR
from travel_agent.graph import RECURSION_LIMIT, build_graph, make_checkpointer
from travel_agent.safety import guard_input
from travel_agent.ui import describe_request_ko

log = logging.getLogger(__name__)

EVAL_DIR = ROOT_DIR / "evaluation"
QUERIES_FILE = EVAL_DIR / "test_queries.csv"
RESULTS_DIR = EVAL_DIR / "results"
JUDGE_PROMPT = EVAL_DIR / "judge_forbidden.md"
CATEGORIES = ("positive", "negative", "edge", "guardrail")

# --- conversation scripts ----------------------------------------------------------------
# A step is (pause the agent must be at, text to send). "start" = the first message.
# Pauses: "ask" (HITL①), "approve_candidates" (②), "approve_itinerary" (③).
Step = tuple[str, str]


@dataclass
class Scenario:
    pre: list[Step] = field(default_factory=list)  # setup before the case input
    input_at: str = "start"  # pause at which the case input is sent
    post: list[Step] = field(default_factory=list)  # replies after the input, until judging


BUSAN_AT_ITINERARY = [("start", "부산 1박2일 5살 아이랑"), ("approve_candidates", "좋아요")]

# Setup/continuation per case id (from each row's note). Unlisted ids: input only.
SCENARIOS: dict[str, Scenario] = {
    "POS-02": Scenario(post=[("approve_candidates", "좋아요")]),
    "POS-03": Scenario(post=[("approve_candidates", "좋아요")]),
    "POS-04": Scenario(pre=BUSAN_AT_ITINERARY, input_at="approve_itinerary"),
    "POS-05": Scenario(pre=BUSAN_AT_ITINERARY, input_at="approve_itinerary"),
    "NEG-01": Scenario(pre=[("start", "후쿠오카 2박3일 힐링")], input_at="approve_candidates"),
    "EDGE-02": Scenario(post=[("approve_candidates", "좋아요")]),
    "EDGE-03": Scenario(pre=[("start", "부산 1박2일 5살 아이랑")], input_at="approve_candidates"),
    "GRD-01": Scenario(post=[("ask", "힐링 여행이에요")]),
    "GRD-02": Scenario(
        post=[("approve_candidates", "좋아요"), ("approve_itinerary", "좋아 저장해줘")]
    ),
}


class SetupError(RuntimeError):
    """The conversation did not reach the state the case needs before its input."""


# --- data ---------------------------------------------------------------------------------


@dataclass
class Case:
    id: str
    category: str
    input: str
    expected_traits: list[str]
    forbidden: list[str]
    expected_tools: list[str]
    note: str


@dataclass
class Result:
    id: str
    category: str
    input: str
    passed: bool = False
    error: str = ""
    tools_status: str = "skip"  # pass / fail / skip
    expected_tools: list[str] = field(default_factory=list)
    called_tools: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)  # "item => evidence"
    judge: str = "exact"  # exact / exact+llm
    stopped_at: str = ""
    notes: list[str] = field(default_factory=list)
    expected_traits: list[str] = field(default_factory=list)
    answer: str = ""
    seconds: float = 0.0


def split_items(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(";") if item.strip()]


def parse_tools(value: str) -> list[str]:
    items = split_items(value)
    return [] if [i.lower() for i in items] in ([], ["none"]) else items


def load_cases(path: Path = QUERIES_FILE) -> list[Case]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [
            Case(
                id=row["id"].strip(),
                category=row["category"].strip(),
                input=row["input"],
                expected_traits=split_items(row["expected_traits"]),
                forbidden=split_items(row["forbidden"]),
                expected_tools=parse_tools(row["expected_tools"]),
                note=row.get("note", ""),
            )
            for row in csv.DictReader(f)
        ]


# --- rendering what the traveller sees --------------------------------------------------------


def render_event(chunk: dict) -> str | None:
    event = chunk.get("event")
    if event == "tool_call":
        args = chunk["args"]
        if chunk["name"] == "estimate_travel_time":
            return (
                f"[도구] estimate_travel_time(city={args.get('city')}, "
                f"transport={args.get('transport')}): {args.get('place_a')} ↔ {args.get('place_b')}"
            )
        if chunk["name"] == "search_places":
            return (
                f"[도구] search_places(city={args.get('city')}, theme={args.get('theme')}): "
                f"{args.get('query')}"
            )
        return f"[도구] {chunk['name']}: {args}"
    if event == "supervisor":
        return f"[Supervisor → {chunk['next']}] {chunk.get('reason', '')}"
    if event == "critic":
        found = chunk.get("violations") or []
        return "[검증] 통과" if not found else "[검증] 위반: " + " / ".join(found)
    if event == "revise":
        return f"[재작업] {', '.join(map(str, chunk['days']))}일차"
    return None


def render_itinerary(it: dict) -> str:
    lines = [f"[일정] {it['title']}"]
    for day in it["days"]:
        lines.append(f"{day['day']}일차 — {day['summary']}")
        for a in day["activities"]:
            note = f" ({a['note']})" if a.get("note") else ""
            lines.append(
                f"  {a['start']}-{a['end']} [{a['category']}] {a['title']} @ {a['place']}{note}"
            )
    lines += [f"[팁] {t}" for t in it.get("tips", [])]
    lines += [f"[경고] {w}" for w in it.get("warnings", [])]
    return "\n".join(lines)


def render_pause(snapshot) -> tuple[str, str]:
    """(pause type, text the traveller sees at this point)."""
    values = snapshot.values
    if not snapshot.next:
        if values.get("unsupported_destination"):
            from travel_agent.agents.intake import unsupported_message

            return "end", "[종료] " + unsupported_message(values["unsupported_destination"])
        paths = values.get("saved_paths") or []
        if not paths:
            return "end", "[종료] 저장된 일정 없음"
        parts = [f"[저장] {', '.join(paths)}"]
        for p in paths:
            if p.endswith(".md") and Path(p).exists():
                parts.append(Path(p).read_text(encoding="utf-8"))
        json_paths = [p for p in paths if p.endswith(".json") and Path(p).exists()]
        for p in json_paths:
            parts.append("[JSON]\n" + Path(p).read_text(encoding="utf-8"))
        return "end", "\n".join(parts)
    if not snapshot.interrupts:
        return "pending", "[중단] 작업 도중 멈춘 상태"
    value = snapshot.interrupts[0].value
    kind = value["type"]
    if kind == "ask":
        return kind, f"[질문] {value['question']}"
    if kind == "approve_candidates":
        lines = [f"[알림] {value['notice']}"] if value.get("notice") else []
        lines.append(f"[후보 {len(value['candidates'])}곳]")
        for i, c in enumerate(value["candidates"], 1):
            source = c.get("source_url") or "출처 없음"
            lines.append(
                f"{i}. {c['place']} ({c['category']}): {c['summary_ko']} / {c['reason_ko']} "
                f"[출처: {source}]"
            )
        return kind, "\n".join(lines)
    if kind == "approve_itinerary":
        return kind, render_itinerary(value["itinerary"])
    return kind, f"[{kind}]"


# --- running one case ------------------------------------------------------------------------


def run_case(graph, case: Case, thread_id: str) -> Result:
    result = Result(
        id=case.id,
        category=case.category,
        input=case.input,
        expected_tools=case.expected_tools,
        expected_traits=case.expected_traits,
    )
    scenario = SCENARIOS.get(case.id, Scenario())
    steps = scenario.pre + [(scenario.input_at, case.input)] + scenario.post
    input_index = len(scenario.pre)
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}
    transcript: list[str] = []
    started = time.monotonic()
    try:
        for i, (want, text) in enumerate(steps):
            if i > 0:
                current, _ = render_pause(graph.get_state(cfg))
                if current != want:
                    if i <= input_index:
                        raise SetupError(f"step {i}: expected pause '{want}', got '{current}'")
                    result.notes.append(f"대본 중단: '{want}'를 기대했지만 '{current}'에서 멈춤")
                    break
            recording = i >= input_index
            guarded = guard_input(text)  # same entry guard as the CLI
            if recording:
                transcript.append(f"[입력] {text}")
                transcript += [f"[안내] {n}" for n in guarded.notices]
            payload = (
                {"messages": [HumanMessage(guarded.text)]}
                if i == 0
                else Command(resume=guarded.text)
            )
            for mode, chunk in graph.stream(payload, cfg, stream_mode=["updates", "custom"]):
                if not recording:
                    continue
                if mode == "custom":
                    if chunk.get("event") == "tool_call":
                        result.called_tools.append(chunk["name"])
                    line = render_event(chunk)
                    if line:
                        transcript.append(line)
                elif mode == "updates" and "intake" in chunk and chunk["intake"]:
                    request = chunk["intake"].get("request")
                    if request is not None:
                        transcript.append(f"[조건] {describe_request_ko(request)}")
            if recording:
                kind, text_seen = render_pause(graph.get_state(cfg))
                result.stopped_at = kind
                transcript.append(text_seen)
    except Exception as exc:  # noqa: BLE001 - one failing case must not stop the run
        log.exception("case %s failed", case.id)
        result.error = f"{type(exc).__name__}: {exc}"
    result.answer = "\n".join(transcript)
    result.seconds = round(time.monotonic() - started, 1)
    return result


# --- scoring ---------------------------------------------------------------------------------


class ForbiddenVerdict(BaseModel):
    index: int
    violated: bool
    evidence: str = ""


class ForbiddenJudgement(BaseModel):
    results: list[ForbiddenVerdict] = Field(default_factory=list)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


INPUT_PREFIX = "[입력]"


def agent_output(answer: str) -> str:
    """The transcript without the traveller's own messages.

    Forbidden content only counts if the agent produced it — echoing the input (e.g. the
    personal data the traveller typed) is not a violation.
    """
    return "\n".join(line for line in answer.splitlines() if not line.startswith(INPUT_PREFIX))


def evidence_found(evidence: str, answer: str) -> bool:
    """True if `evidence` appears verbatim in the agent's part of the transcript."""
    ev = normalize(evidence)
    return len(ev) >= 2 and ev in normalize(agent_output(answer))


TIME_RE = re.compile(r"^\s*(\d{1,2}:\d{2})-\d{1,2}:\d{2}\b")
DAY_HEADER_RE = re.compile(r"^(\d+)일차 —")
DAY_IN_ITEM_RE = re.compile(r"(\d+)일차")


def line_day(answer: str, evidence: str) -> int | None:
    """Day number of the itinerary section containing the evidence line, if any."""
    day = None
    ev = normalize(evidence)
    for line in agent_output(answer).splitlines():
        header = DAY_HEADER_RE.match(line)
        if header:
            day = int(header.group(1))
        if ev and ev in normalize(line):
            return day
    return None


def scope_mismatch(item: str, evidence: str, answer: str) -> str | None:
    """Why the evidence is outside the day / time of day the item names (None = in scope).

    Deterministic guard for judge mistakes like calling a 13:50 activity "오전".
    """
    from travel_agent.agents.critic import request_windows
    from travel_agent.rules import to_minutes

    windows = request_windows(item)
    timed = TIME_RE.match(evidence)
    if windows and timed:
        start = to_minutes(timed.group(1))
        if start is not None and not any(lo <= start < hi for lo, hi in windows):
            return f"{timed.group(1)}은(는) 항목이 말한 시간대가 아님"
    named_day = DAY_IN_ITEM_RE.search(item)
    if named_day and timed:
        day = line_day(answer, evidence)
        if day is not None and day != int(named_day.group(1)):
            return f"근거는 {day}일차인데 항목은 {named_day.group(1)}일차"
    return None


# Placeholders the planner may use instead of a named place (prompts/planner_base.md,
# prompts/critic_review.md). They are never "unknown named places".
GENERIC_PLACE_LABELS = {"숙소", "이동", "호텔", "공항", "역", "자유 시간"}
GENERIC_PLACE_PREFIXES = ("근처 ", "숙소 ")
PLACE_IN_LINE_RE = re.compile(r"@ ([^(]+?)\s*(?:\(|$)")
UNKNOWN_PLACE_ITEM_RE = re.compile(r"자료에 없는|장소 이름|지어낸|생성")


def generic_place_evidence(item: str, evidence: str) -> str | None:
    """The generic label an "unknown place" verdict was based on, if any (None = not generic)."""
    if not UNKNOWN_PLACE_ITEM_RE.search(item):
        return None
    match = PLACE_IN_LINE_RE.search(evidence)
    if not match:
        return None
    place = match.group(1).strip()
    if place in GENERIC_PLACE_LABELS or place.startswith(GENERIC_PLACE_PREFIXES):
        return place
    return None


def exact_violations(forbidden: list[str], answer: str) -> list[str]:
    norm = normalize(agent_output(answer))
    return [f"{item} => (문구 그대로 등장)" for item in forbidden if normalize(item) in norm]


def judge_violations(
    forbidden: list[str], answer: str, judge_model: str = "agent"
) -> tuple[list[str], list[str]]:
    """LLM verdicts backed by a verbatim, in-scope quote. Returns (violations, notes)."""
    from travel_agent.llm import get_chat_model

    llm = get_chat_model(judge_model, max_tokens=2000).with_structured_output(ForbiddenJudgement)
    items = "\n".join(f"{i}. {item}" for i, item in enumerate(forbidden))
    human = f"Forbidden items:\n{items}\n\nTranscript:\n{answer}"
    judgement: ForbiddenJudgement = llm.invoke(
        [("system", JUDGE_PROMPT.read_text(encoding="utf-8")), ("human", human)]
    )
    violations, notes = [], []
    for verdict in judgement.results:
        if not verdict.violated or not 0 <= verdict.index < len(forbidden):
            continue
        item = forbidden[verdict.index]
        if evidence_found(verdict.evidence, answer):
            mismatch = scope_mismatch(item, verdict.evidence, answer)
            generic = generic_place_evidence(item, verdict.evidence)
            if generic:
                notes.append(f"판정 무시(일반 표기 '{generic}'는 장소 이름이 아님): {item}")
            elif mismatch:
                notes.append(f"판정 무시(범위 밖: {mismatch}): {item} => {verdict.evidence!r}")
            else:
                violations.append(f"{item} => {normalize(verdict.evidence)}")
        else:
            notes.append(f"판정 무시(근거가 에이전트 출력에 없음): {item} => {verdict.evidence!r}")
    return violations, notes


def score(result: Result, case: Case, use_judge: bool, judge_model: str = "agent") -> Result:
    if case.expected_tools:
        missing = [t for t in case.expected_tools if t not in result.called_tools]
        result.tools_status = "fail" if missing else "pass"
        if missing:
            result.notes.append(f"호출되지 않은 도구: {', '.join(missing)}")
    result.violations = exact_violations(case.forbidden, result.answer)
    if use_judge and case.forbidden and result.answer and not result.error:
        result.judge = f"exact+llm({judge_model})"
        try:
            judged, notes = judge_violations(case.forbidden, result.answer, judge_model)
            seen = {v.split(" => ")[0] for v in result.violations}
            result.violations += [v for v in judged if v.split(" => ")[0] not in seen]
            result.notes += notes
        except Exception as exc:  # noqa: BLE001
            result.notes.append(f"LLM 판정 실패({type(exc).__name__}) — 문구 일치만 적용")
    result.passed = not result.error and result.tools_status != "fail" and not result.violations
    return result


def pass_rates(results: list[Result]) -> dict[str, tuple[int, int]]:
    by_cat: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_cat[r.category].append(r.passed)
    ordered = [c for c in CATEGORIES if c in by_cat] + sorted(set(by_cat) - set(CATEGORIES))
    rates = {c: (sum(by_cat[c]), len(by_cat[c])) for c in ordered}
    rates["전체"] = (sum(r.passed for r in results), len(results))
    return rates


# --- output ------------------------------------------------------------------------------------


def write_outputs(results: list[Result], cases: dict[str, Case], out_dir: Path, meta: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rates = pass_rates(results)

    with (out_dir / "results.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "id",
                "category",
                "passed",
                "error",
                "tools_status",
                "expected_tools",
                "called_tools",
                "forbidden_violations",
                "judge",
                "stopped_at",
                "notes",
                "input",
                "expected_traits",
                "answer",
                "seconds",
            ]
        )
        for r in results:
            w.writerow(
                [
                    r.id,
                    r.category,
                    "PASS" if r.passed else "FAIL",
                    r.error,
                    r.tools_status,
                    "; ".join(r.expected_tools),
                    "; ".join(sorted(set(r.called_tools))),
                    " | ".join(r.violations),
                    r.judge,
                    r.stopped_at,
                    " | ".join(r.notes),
                    r.input,
                    "; ".join(r.expected_traits),
                    r.answer,
                    r.seconds,
                ]
            )

    summary = {
        **meta,
        "pass_rates": {
            k: {"passed": p, "total": t, "rate": round(p / t, 3) if t else None}
            for k, (p, t) in rates.items()
        },
        "results": [asdict(r) for r in results],
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        f"# 평가 결과 ({meta['started_at']})",
        "",
        "| 구분 | 통과 | 전체 | 통과율 |",
        "|---|---|---|---|",
    ]
    for k, (p, t) in rates.items():
        lines.append(f"| {k} | {p} | {t} | {p / t:.0%} |" if t else f"| {k} | 0 | 0 | - |")
    lines += ["", "expected_traits는 자동 채점하지 않습니다. 아래 답변을 보고 체크하세요.", ""]
    for r in results:
        case = cases[r.id]
        status = "✅ PASS" if r.passed else "❌ FAIL"
        lines += [f"## {r.id} ({r.category}) — {status}", "", f"**입력**: {r.input}", ""]
        if case.note:
            lines += [f"**메모**: {case.note}", ""]
        lines += [
            "**expected_traits (사람이 확인)**",
            *[f"- [ ] {t}" for t in r.expected_traits],
            "",
        ]
        tools = f"{r.tools_status}"
        if r.expected_tools:
            called = ", ".join(sorted(set(r.called_tools))) or "없음"
            tools += f" (기대: {', '.join(r.expected_tools)} / 호출: {called})"
        lines += [f"**도구 검사**: {tools}", ""]
        lines += ["**forbidden 위반**", *([f"- {v}" for v in r.violations] or ["- 없음"]), ""]
        if r.error:
            lines += [f"**오류**: `{r.error}`", ""]
        if r.notes:
            lines += ["**참고**", *[f"- {n}" for n in r.notes], ""]
        lines += [
            f"**답변** (멈춘 곳: {r.stopped_at or '-'}, {r.seconds}초)",
            "",
            "```text",
            r.answer or "(없음)",
            "```",
            "",
        ]
    (out_dir / "review.md").write_text("\n".join(lines), encoding="utf-8")


def print_summary(results: list[Result], out_dir: Path) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title="케이스별 결과")
    for col in ["id", "category", "결과", "도구", "forbidden 위반", "오류"]:
        table.add_column(col, overflow="fold")
    for r in results:
        table.add_row(
            r.id,
            r.category,
            "[green]PASS[/]" if r.passed else "[red]FAIL[/]",
            r.tools_status,
            str(len(r.violations)),
            r.error[:60],
        )
    console.print(table)

    rates = Table(title="통과율")
    for col in ["구분", "통과", "전체", "통과율"]:
        rates.add_column(col)
    for k, (p, t) in pass_rates(results).items():
        rates.add_row(k, str(p), str(t), f"{p / t:.0%}" if t else "-")
    console.print(rates)
    console.print(f"결과 파일: {out_dir}")


# --- CLI -----------------------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="test_queries.csv 평가 실행")
    parser.add_argument("--queries", type=Path, default=QUERIES_FILE)
    parser.add_argument("--ids", nargs="*", help="실행할 문항 id만")
    parser.add_argument("--category", nargs="*", choices=CATEGORIES, help="실행할 카테고리만")
    parser.add_argument("--no-judge", action="store_true", help="forbidden을 문구 일치로만 판정")
    parser.add_argument(
        "--judge-model",
        choices=["agent", "light"],
        default="agent",
        help="forbidden 판정에 쓸 모델 (기본: agent 모델)",
    )
    parser.add_argument("--out", type=Path, help="결과 폴더 (기본: evaluation/results/<시각>)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    for noisy in ("httpx", "sentence_transformers", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    cases = load_cases(args.queries)
    if args.ids:
        cases = [c for c in cases if c.id in set(args.ids)]
    if args.category:
        cases = [c for c in cases if c.category in set(args.category)]
    if not cases:
        raise SystemExit("실행할 문항이 없습니다.")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.out or RESULTS_DIR / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(out_dir / "checkpoints.db", check_same_thread=False)
    graph = build_graph(make_checkpointer(conn))

    results = []
    try:
        for n, case in enumerate(cases, 1):
            print(f"[{n}/{len(cases)}] {case.id} ({case.category}) …", flush=True)
            result = run_case(graph, case, thread_id=f"eval-{stamp}-{case.id}")
            results.append(
                score(result, case, use_judge=not args.no_judge, judge_model=args.judge_model)
            )
            print(f"    → {'PASS' if result.passed else 'FAIL'} ({result.seconds}s)", flush=True)
    finally:
        conn.close()

    meta = {
        "started_at": stamp,
        "queries": str(args.queries),
        "judge": None if args.no_judge else args.judge_model,
        "case_count": len(results),
        "category_counts": dict(Counter(r.category for r in results)),
    }
    write_outputs(results, {c.id: c for c in cases}, out_dir, meta)
    print_summary(results, out_dir)


if __name__ == "__main__":
    main()
