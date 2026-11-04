"""LangGraph assembly.

M3 flow:
    START → intake ─(missing / unsupported city)→ ask_user ─→ intake
                   └→ research → approve ─(add more)→ research
                                         ├(unclear reply)→ approve
                                         └(confirmed)→ END   (M4: → Supervisor)
"""

from __future__ import annotations

import sqlite3

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from travel_agent.agents.approval import apply_removals, parse_reply
from travel_agent.agents.intake import (
    build_question,
    cities_by_id,
    extract,
    merge_request,
    missing_fields,
    unsupported_destination,
)
from travel_agent.agents.researcher import ADD_RECURSION_LIMIT, describe_additions, stream_research
from travel_agent.agents.researcher import RECURSION_LIMIT as RESEARCH_RECURSION_LIMIT
from travel_agent.state import AgentState, TripRequest

RECURSION_LIMIT = 50

# Pydantic models stored in the state; LangGraph only restores allow-listed classes.
CHECKPOINT_TYPES = [("travel_agent.state", name) for name in ("TripRequest", "Candidate")]


# --- intake / HITL① ---------------------------------------------------------------


def intake_node(state: AgentState) -> dict:
    ext = extract(state["messages"])
    request = merge_request(state.get("request") or TripRequest(), ext)
    unsupported = unsupported_destination(ext)
    return {
        "request": request,
        "missing": missing_fields(request),
        "unsupported_destination": unsupported,
        "similar_cities": ext.similar_city_ids if unsupported else [],
    }


def route_after_intake(state: AgentState) -> str:
    if state.get("missing") or state.get("unsupported_destination"):
        return "ask_user"
    return "research"


def ask_user_node(state: AgentState) -> dict:
    question = build_question(
        state.get("missing", []),
        state.get("unsupported_destination"),
        state.get("similar_cities"),
    )
    answer = interrupt({"type": "ask", "question": question})
    return {"messages": [AIMessage(question), HumanMessage(str(answer))]}


# --- research ------------------------------------------------------------------------


def research_node(state: AgentState) -> dict:
    writer = get_stream_writer()
    request = state["request"]
    city = cities_by_id()[request.destination]
    existing = state.get("candidates") or []
    add_requests = state.get("add_requests") or []
    extra = (
        describe_additions(add_requests, [c for c in existing if c.approved])
        if add_requests
        else ""
    )

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
        return END
    if state.get("add_requests"):
        return "research"
    return "approve"  # unclear reply: show the list again


def make_checkpointer(conn: sqlite3.Connection) -> SqliteSaver:
    """SQLite checkpointer that is allowed to restore our Pydantic state models."""
    serde = JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)
    return SqliteSaver(conn, serde=serde)


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    g = StateGraph(AgentState)
    g.add_node("intake", intake_node)
    g.add_node("ask_user", ask_user_node)
    g.add_node("research", research_node)
    g.add_node("approve", approve_node)

    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", route_after_intake, ["ask_user", "research"])
    g.add_edge("ask_user", "intake")
    g.add_edge("research", "approve")
    g.add_conditional_edges("approve", route_after_approve, ["research", "approve", END])
    return g.compile(checkpointer=checkpointer)
