"""LangGraph assembly.

START → intake ─(missing)→ ask_user ─→ intake
               ├(unsupported city)→ reject → END
               └→ supervisor ─→ research → approve ─(add more)→ research
                     ▲    │                       ├(unclear reply)→ approve
                     │    │                       └(confirmed)→ supervisor
                     │    ├→ family_planner / healing_planner  (full plan or revise_days only)
                     │    ├→ critic          (violations → revise_days, max 2 rewrites)
                     │    ├→ final_approval  (HITL③: approve, or revise days)
                     │    ├→ save            (output/<thread>/itinerary.{json,md})
                     └────┤   each worker returns to the supervisor
                          └→ finish → END

The supervisor's legal choices come from agents.supervisor.allowed_workers().
"""

from __future__ import annotations

import sqlite3

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from travel_agent.agents.approval import apply_removals, parse_reply
from travel_agent.agents.critic import MAX_RETRIES, critique, days_to_redo, format_violation
from travel_agent.agents.final_approval import days_to_revise, parse_final_reply
from travel_agent.agents.intake import (
    build_question,
    cities_by_id,
    extract,
    merge_request,
    missing_fields,
    unsupported_destination,
    unsupported_message,
)
from travel_agent.agents.planner import stream_plan
from travel_agent.agents.researcher import ADD_RECURSION_LIMIT, describe_additions, stream_research
from travel_agent.agents.researcher import RECURSION_LIMIT as RESEARCH_RECURSION_LIMIT
from travel_agent.agents.supervisor import decide
from travel_agent.output import save_itinerary
from travel_agent.state import AgentState, TripRequest

RECURSION_LIMIT = 50

# Pydantic models stored in the state; LangGraph only restores allow-listed classes.
CHECKPOINT_TYPES = [
    ("travel_agent.state", name)
    for name in ("TripRequest", "Candidate", "Activity", "DayPlan", "Itinerary")
]


# --- intake / HITL① ---------------------------------------------------------------


def intake_node(state: AgentState) -> dict:
    ext = extract(state["messages"])
    request = merge_request(state.get("request") or TripRequest(), ext)
    unsupported = unsupported_destination(ext)
    return {
        "request": request,
        "missing": missing_fields(request),
        "unsupported_destination": unsupported,
    }


def route_after_intake(state: AgentState) -> str:
    # An unsupported city ends the conversation, even if other fields are missing.
    if state.get("unsupported_destination"):
        return "reject"
    if state.get("missing"):
        return "ask_user"
    return "supervisor"


def ask_user_node(state: AgentState) -> dict:
    question = build_question(state.get("missing", []))
    answer = interrupt({"type": "ask", "question": question})
    return {"messages": [AIMessage(question), HumanMessage(str(answer))]}


def reject_node(state: AgentState) -> dict:
    """Unsupported destination: explain and stop (no planning without source data)."""
    return {"messages": [AIMessage(unsupported_message(state["unsupported_destination"]))]}


# --- research ------------------------------------------------------------------------


def research_node(state: AgentState) -> dict:
    writer = get_stream_writer()
    request = state["request"]
    city = cities_by_id()[request.destination]
    existing = state.get("candidates") or []
    add_requests = state.get("add_requests") or []
    if add_requests:
        extra = describe_additions(add_requests, [c for c in existing if c.approved])
    else:
        brief = state.get("brief")
        extra = f"\n\nSupervisor brief: {brief}" if brief else ""

    found = []
    limit = ADD_RECURSION_LIMIT if add_requests else RESEARCH_RECURSION_LIMIT
    for kind, payload in stream_research(request, city, extra, limit):
        if kind == "tool_call" and payload["name"] != "CandidateList":
            writer({"event": "tool_call", "name": payload["name"], "args": payload["args"]})
        elif kind == "result":
            found = payload

    if add_requests:
        known = {c.place for c in existing}
        new = [c for c in found if c.place not in known]
        candidates = existing + new
        wants = ", ".join(add_requests)
        note = (
            f"요청하신 장소({wants}) {len(new)}곳을 추가했어요."
            if new
            else f"요청하신 '{wants}'에 맞는 장소를 여행 자료에서 찾지 못했어요. 목록은 그대로예요."
        )
    else:
        candidates = found
        note = (
            f"후보 장소 {len(found)}곳을 찾았어요."
            if found
            else "조건에 맞는 장소를 여행 자료에서 찾지 못했어요. 조건을 바꿔 다시 시도해 주세요."
        )
    return {
        "candidates": candidates,
        "add_requests": [],
        "notice": note,
        "messages": [AIMessage(note)],
    }


# --- HITL② ---------------------------------------------------------------------------


def approve_node(state: AgentState) -> dict:
    shown = [c for c in state.get("candidates", []) if c.approved]
    reply = interrupt(
        {
            "type": "approve_candidates",
            "candidates": [c.model_dump() for c in shown],
            "notice": state.get("notice"),
        }
    )
    decision = parse_reply(str(reply), shown)
    candidates = apply_removals(state["candidates"], decision.remove)
    confirmed = not decision.add_requests and (decision.approve or bool(decision.remove))
    return {
        "candidates": candidates,
        "add_requests": decision.add_requests,
        "candidates_confirmed": confirmed,
        "messages": [HumanMessage(str(reply))],
    }


def route_after_approve(state: AgentState) -> str:
    if state.get("candidates_confirmed"):
        return "supervisor"
    if state.get("add_requests"):
        return "research"
    return "approve"  # unclear reply: show the list again


# --- supervisor / planners ---------------------------------------------------------------


def supervisor_node(state: AgentState) -> dict:
    decision = decide(state)
    get_stream_writer()({"event": "supervisor", "next": decision.next, "reason": decision.reason})
    return {
        "next": decision.next,
        "brief": decision.brief,
        "step_count": state.get("step_count", 0) + 1,
    }


def route_after_supervisor(state: AgentState) -> str:
    return END if state["next"] == "finish" else state["next"]


def make_planner_node(theme: str):
    def planner_node(state: AgentState) -> dict:
        writer = get_stream_writer()
        request = state["request"]
        city = cities_by_id()[request.destination]
        approved = [c for c in state.get("candidates") or [] if c.approved]
        current = state.get("itinerary")
        revise_days = state.get("revise_days") or []
        if revise_days and current is not None:
            writer({"event": "revise", "days": revise_days})
        itinerary = None
        for kind, payload in stream_plan(
            theme,
            request,
            city,
            approved,
            state.get("brief", ""),
            current=current if revise_days else None,
            revise_days=revise_days,
            reason=state.get("revise_reason", ""),
        ):
            if kind == "tool_call" and payload["name"] != "Itinerary":
                writer({"event": "tool_call", "name": payload["name"], "args": payload["args"]})
            elif kind == "result":
                itinerary = payload
        if itinerary is None:
            raise RuntimeError(f"{theme} planner did not produce an itinerary")
        note = (
            f"{', '.join(map(str, revise_days))}일차를 다시 만들었어요."
            if revise_days
            else f"{len(itinerary.days)}일 일정을 만들었어요."
        )
        return {
            "itinerary": itinerary,
            "revise_days": [],
            "revise_reason": "",
            "itinerary_checked": False,
            "messages": [AIMessage(note)],
        }

    planner_node.__name__ = f"{theme}_planner_node"
    return planner_node


# --- critic ----------------------------------------------------------------------------------


def critic_node(state: AgentState) -> dict:
    itinerary = state["itinerary"]
    approved = [c for c in state.get("candidates") or [] if c.approved]
    violations = critique(itinerary, state["request"], approved)
    messages = [format_violation(v) for v in violations]
    retries = state.get("retry_count", 0)
    get_stream_writer()(
        {"event": "critic", "violations": messages, "retry": retries, "max_retries": MAX_RETRIES}
    )
    if violations and retries < MAX_RETRIES:
        all_days = [d.day for d in itinerary.days]
        return {
            "critique": messages,
            "revise_days": days_to_redo(violations, all_days),
            "revise_reason": "\n".join(f"- {m}" for m in messages),
            "retry_count": retries + 1,
            "itinerary_checked": False,
        }
    # Clean, or out of retries: remaining problems become visible warnings.
    return {
        "critique": messages,
        "itinerary": itinerary.model_copy(update={"warnings": messages}),
        "revise_days": [],
        "itinerary_checked": True,
    }


# --- HITL③ / save ------------------------------------------------------------------------------


def final_approval_node(state: AgentState) -> dict:
    itinerary = state["itinerary"]
    reply = interrupt({"type": "approve_itinerary", "itinerary": itinerary.model_dump()})
    decision = parse_final_reply(str(reply), itinerary)
    days = days_to_revise(decision, len(itinerary.days))
    update: dict = {"messages": [HumanMessage(str(reply))]}
    if days:
        return {
            **update,
            "final_approved": False,
            "revise_days": days,
            "revise_reason": decision.request or str(reply),
            "retry_count": 0,  # a traveller revision starts a new critic cycle
            "itinerary_checked": False,
        }
    # Approve, or an unclear reply (the supervisor will ask again).
    return {**update, "final_approved": decision.approve}


def save_node(state: AgentState, config: RunnableConfig) -> dict:
    thread_id = config["configurable"]["thread_id"]
    approved = [c for c in state.get("candidates") or [] if c.approved]
    paths = save_itinerary(state["itinerary"], state["request"], approved, thread_id)
    return {"saved_paths": [str(p) for p in paths]}


def make_checkpointer(conn: sqlite3.Connection) -> SqliteSaver:
    """SQLite checkpointer that is allowed to restore our Pydantic state models."""
    serde = JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)
    return SqliteSaver(conn, serde=serde)


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    g = StateGraph(AgentState)
    g.add_node("intake", intake_node)
    g.add_node("ask_user", ask_user_node)
    g.add_node("reject", reject_node)
    g.add_node("research", research_node)
    g.add_node("approve", approve_node)
    g.add_node("supervisor", supervisor_node)
    g.add_node("family_planner", make_planner_node("family"))
    g.add_node("healing_planner", make_planner_node("healing"))
    g.add_node("critic", critic_node)
    g.add_node("final_approval", final_approval_node)
    g.add_node("save", save_node)

    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", route_after_intake, ["ask_user", "reject", "supervisor"])
    g.add_edge("reject", END)
    g.add_edge("ask_user", "intake")
    g.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        [
            "research",
            "family_planner",
            "healing_planner",
            "critic",
            "final_approval",
            "save",
            END,
        ],
    )
    g.add_edge("research", "approve")
    g.add_conditional_edges("approve", route_after_approve, ["research", "approve", "supervisor"])
    for worker in ("family_planner", "healing_planner", "critic", "final_approval", "save"):
        g.add_edge(worker, "supervisor")
    return g.compile(checkpointer=checkpointer)
