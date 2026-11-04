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

from travel_agent.config import CHECKPOINT_DB
from travel_agent.graph import RECURSION_LIMIT, build_graph, make_checkpointer
from travel_agent.state import Candidate
from travel_agent.ui import candidates_table, describe_request_ko

console = Console()
QUIT_WORDS = {"q", "quit", "exit", "종료", "그만"}

NODE_STATUS = {
    "intake": "여행 조건 정리",
    "research": "장소 조사",
}


def run(graph, payload: Any, config: dict) -> None:
    """Stream one graph run, printing progress, until it finishes or hits an interrupt."""
    with console.status("[bold]생각 중...[/]") as status:
        for mode, chunk in graph.stream(payload, config, stream_mode=["updates", "custom"]):
            if mode == "custom" and chunk.get("event") == "tool_call":
                status.update("[bold]장소 조사 중...[/]")
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
    raise ValueError(f"Unknown interrupt type: {value['type']}")


def print_result(values: dict) -> None:
    approved = [c for c in values.get("candidates", []) if c.approved]
    console.print(f"\n[bold green]후보 장소 {len(approved)}곳이 확정됐어요.[/]")
    console.print(candidates_table(approved, describe_request_ko(values["request"])))
    console.print("[dim](일정 생성은 다음 단계(M4)에서 연결됩니다.)[/]")


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
