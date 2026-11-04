"""CLI chat loop.

python -m travel_agent.main                 # new conversation
python -m travel_agent.main --thread <id>   # continue a saved conversation
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import uuid
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import Command
from rich.console import Console

from travel_agent.agents.intake import unsupported_message
from travel_agent.config import CHECKPOINT_DB
from travel_agent.graph import RECURSION_LIMIT, build_graph, make_checkpointer
from travel_agent.state import Candidate, Itinerary
from travel_agent.ui import candidates_table, describe_request_ko, itinerary_tables

console = Console()
QUIT_WORDS = {"q", "quit", "exit", "종료", "그만"}

NODE_STATUS = {
    "intake": "여행 조건 정리",
    "research": "장소 조사",
    "family_planner": "가족 여행 일정 생성",
    "healing_planner": "힐링 여행 일정 생성",
    "save": "파일 저장",
}
WORKER_KO = {
    "research": "장소 조사 (Researcher)",
    "family_planner": "가족 일정 플래너",
    "healing_planner": "힐링 일정 플래너",
    "critic": "규칙 검증 (Critic)",
    "final_approval": "최종 확인",
    "save": "저장",
    "finish": "마무리",
}


def run(graph, payload: Any, config: dict) -> None:
    """Stream one graph run, printing progress, until it finishes or hits an interrupt."""
    with console.status("[bold]생각 중...[/]") as status:
        for mode, chunk in graph.stream(payload, config, stream_mode=["updates", "custom"]):
            if mode == "custom" and chunk.get("event") == "supervisor":
                worker = WORKER_KO.get(chunk["next"], chunk["next"])
                console.print(f"[magenta]🧑‍✈️ Supervisor → {worker}[/] [dim]{chunk['reason']}[/]")
                status.update(f"[bold]{worker} 작업 중...[/]")
            elif mode == "custom" and chunk.get("event") == "critic":
                found = chunk["violations"]
                if not found:
                    console.print("[green]✓[/] 규칙 검증 통과")
                else:
                    redo = chunk["retry"] < chunk["max_retries"]
                    action = "해당 날짜를 다시 만들어요" if redo else "⚠️ 경고로 표시해요"
                    console.print(f"[yellow]✗ 규칙 위반 {len(found)}건 → {action}[/]")
                    for message in found:
                        console.print(f"  [dim]· {message}[/]")
            elif mode == "custom" and chunk.get("event") == "revise":
                days = ", ".join(map(str, chunk["days"]))
                console.print(f"  [dim]✏️  {days}일차만 다시 만들어요[/]")
            elif mode == "custom" and chunk.get("event") == "tool_call":
                args = chunk["args"]
                detail = args.get("query") or f"{args.get('place_a')} ↔ {args.get('place_b')}"
                console.print(f"  [dim]🔎 {chunk['name']}: {detail}[/]")
            elif mode == "updates":
                for node, update in chunk.items():
                    if node in NODE_STATUS:
                        console.print(f"[green]✓[/] {NODE_STATUS[node]}")
                    if node == "intake" and update and update.get("request"):
                        summary = describe_request_ko(update["request"])
                        if summary:
                            console.print(f"  [dim]{summary}[/]")


def ask(prompt: str) -> str | None:
    answer = console.input(f"[bold cyan]{prompt}[/] ").strip()
    return None if answer.lower() in QUIT_WORDS else answer


def handle_interrupt(value: dict) -> str | None:
    if value["type"] == "ask":
        console.print(f"\n[bold]🧭 {value['question']}[/]")
        return ask("›")
    if value["type"] == "approve_candidates":
        shown = [Candidate(**c) for c in value["candidates"]]
        console.print()
        if value.get("notice"):
            console.print(f"[bold]💬 {value['notice']}[/]")
        console.print(candidates_table(shown, f"후보 장소 {len(shown)}곳"))
        console.print("이대로 진행할까요? 뺄 곳(예: 3번 빼줘)이나 추가할 곳을 말씀해 주세요.")
        return ask("›")
    if value["type"] == "approve_itinerary":
        show_itinerary(Itinerary(**value["itinerary"]))
        console.print(
            "\n이 일정으로 저장할까요? 바꾸고 싶은 부분이 있으면 말씀해 주세요. "
            "(예: 2일차 오후는 실내로 바꿔줘)"
        )
        return ask("›")
    raise ValueError(f"Unknown interrupt type: {value['type']}")


def show_itinerary(itinerary: Itinerary) -> None:
    console.print(f"\n[bold green]✈️  {itinerary.title}[/]\n")
    for table in itinerary_tables(itinerary):
        console.print(table)
    for tip in itinerary.tips:
        console.print(f"💡 {tip}")
    for warning in itinerary.warnings:
        console.print(f"[yellow]⚠️ {warning}[/]")


def print_result(values: dict) -> None:
    if values.get("unsupported_destination"):
        console.print(
            f"\n[bold yellow]🚫 {unsupported_message(values['unsupported_destination'])}[/]"
        )
        return
    itinerary: Itinerary | None = values.get("itinerary")
    if itinerary is None:
        console.print(
            "\n[bold yellow]일정을 만들지 못했어요.[/] 조건을 바꿔 새 대화를 시작해 주세요."
        )
        return
    if not values.get("saved_paths"):
        show_itinerary(itinerary)
        console.print("[yellow]일정이 저장되지 않았어요.[/]")
        return
    console.print(f"\n[bold green]✅ '{itinerary.title}' 일정을 저장했어요.[/]")
    console.print(f"[dim]{describe_request_ko(values['request'])}[/]")
    for path in values["saved_paths"]:
        console.print(f"  📄 {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="테마별 여행 일정 에이전트")
    parser.add_argument("--thread", help="이어서 진행할 대화 id")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    thread_id = args.thread or uuid.uuid4().hex[:8]
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}
    CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
    try:
        graph = build_graph(make_checkpointer(conn))
        snapshot = graph.get_state(config)

        if snapshot.next:
            console.print(f"[bold]대화 {thread_id}를 이어서 진행해요.[/]")
        elif snapshot.values:
            print_result(snapshot.values)
            return
        else:
            console.print(f"[bold]새 대화를 시작해요[/] [dim](id: {thread_id})[/]")
            first = ask("어떤 여행을 계획하고 계신가요?\n›")
            if first is None:
                return
            run(graph, {"messages": [HumanMessage(first)]}, config)

        while True:
            snapshot = graph.get_state(config)
            if not snapshot.next:
                print_result(snapshot.values)
                return
            if not snapshot.interrupts:
                # A previous run stopped inside a step (e.g. a crash); re-run that step.
                run(graph, None, config)
                continue
            answer = handle_interrupt(snapshot.interrupts[0].value)
            if answer is None:
                console.print(f"\n대화를 저장했어요. 이어서 하려면: {resume_hint(thread_id)}")
                return
            run(graph, Command(resume=answer), config)
    except KeyboardInterrupt:
        console.print(f"\n중단했어요. 이어서 하려면: {resume_hint(thread_id)}")
    except Exception as exc:  # noqa: BLE001 - show a friendly message, state is checkpointed
        logging.getLogger(__name__).exception("run failed")
        console.print(
            f"\n[red]오류가 발생했어요: {exc}[/]\n"
            f"마지막으로 저장된 지점부터 다시 시도하려면: {resume_hint(thread_id)}"
        )
    finally:
        conn.close()


def resume_hint(thread_id: str) -> str:
    return f"[bold]python -m travel_agent.main --thread {thread_id}[/]"


if __name__ == "__main__":
    main()
