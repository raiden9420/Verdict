"""
LangGraph state machine for the adversarial audit.

Graph: Attacker → Defender → Validator → Referee  (×3 exchanges)
                                                    → Debrief → END

Each node stores its output in the DB, pushes an SSE event via
`event_callback`, and returns partial state updates.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, TypedDict, Annotated, Callable
import operator

from langgraph.graph import StateGraph, END

from app.constants import (
    EXCHANGES_PER_ROUND,
    MAX_ATTACKER_RETRIES,
    ROUND_TOPICS,
)
from app.database import get_supabase
from app.services.llm_client import get_llm_client
from app.services.retrieval_service import (
    retrieve_chunks,
    build_attacker_query,
    build_defender_query,
)
from app.agents.prompts import (
    attacker_system_prompt,
    defender_system_prompt,
    referee_system_prompt,
    debrief_system_prompt,
)
from app.agents.grounding_validator import (
    validate_attacker_citations,
    validate_defender_citations,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------
class AuditState(TypedDict):
    # Fixed context (set once)
    paper_id: str
    round_id: str
    round_topic: str        # slug
    round_topic_name: str   # display name

    # Exchange tracking
    exchange_number: int    # current exchange (1-indexed)
    attacker_retries: int

    # Accumulating lists — operator.add appends new items
    prior_claims: Annotated[list[str], operator.add]
    all_turns: Annotated[list[dict], operator.add]
    all_verdicts: Annotated[list[dict], operator.add]

    # Current exchange data (overwritten each cycle)
    attacker_output: dict
    defender_output: dict
    attacker_validation: list
    defender_validation: list
    attacker_valid: bool

    # SSE streaming callback: Callable[[dict], None] or None
    event_callback: Any

    # Status
    status: str
    error: str

    # Sequence counter for turn ordering
    sequence_counter: int


# ---------------------------------------------------------------------------
# Helper: store a turn in the DB and push an SSE event
# ---------------------------------------------------------------------------
def _store_turn(
    round_id: str,
    exchange_number: int,
    agent_type: str,
    sequence: int,
    content: dict,
    callback: Any,
) -> dict:
    """Insert a turn row and push an SSE event. Returns the stored turn."""
    supabase = get_supabase()
    turn_id = str(uuid.uuid4())

    turn_row = {
        "id": turn_id,
        "round_id": round_id,
        "exchange_number": exchange_number,
        "agent_type": agent_type,
        "sequence": sequence,
        "content": json.dumps(content) if isinstance(content, dict) else content,
    }
    supabase.table("turns").insert(turn_row).execute()

    event = {
        "type": "turn",
        "data": {
            "id": turn_id,
            "exchange_number": exchange_number,
            "agent_type": agent_type,
            "sequence": sequence,
            "content": content,
        },
    }
    if callback:
        try:
            callback(event)
        except Exception:
            logger.warning("SSE callback failed for turn %s", turn_id)

    return event["data"]


def _store_verdict(
    round_id: str,
    exchange_number: int,
    claim_summary: str,
    verdict_type: str,
    confidence: float,
    rationale: str,
    cited_chunk_ids: list[str],
    callback: Any,
) -> dict:
    """Insert a verdict row and push an SSE event."""
    supabase = get_supabase()
    verdict_id = str(uuid.uuid4())

    verdict_row = {
        "id": verdict_id,
        "round_id": round_id,
        "exchange_number": exchange_number,
        "claim_summary": claim_summary,
        "verdict_type": verdict_type,
        "confidence": confidence,
        "rationale": rationale,
        "cited_chunk_ids": json.dumps(cited_chunk_ids),
    }
    supabase.table("verdicts").insert(verdict_row).execute()

    event = {
        "type": "verdict",
        "data": {
            "id": verdict_id,
            "exchange_number": exchange_number,
            "claim_summary": claim_summary,
            "verdict_type": verdict_type,
            "confidence": confidence,
            "rationale": rationale,
            "cited_chunk_ids": cited_chunk_ids,
        },
    }
    if callback:
        try:
            callback(event)
        except Exception:
            logger.warning("SSE callback failed for verdict %s", verdict_id)

    return event["data"]


# ---------------------------------------------------------------------------
# Node: Attacker
# ---------------------------------------------------------------------------
def attacker_node(state: AuditState) -> dict:
    """Retrieve relevant chunks and generate the Attacker's critique."""
    logger.info(
        "Exchange %d — Attacker (retry %d)",
        state["exchange_number"],
        state["attacker_retries"],
    )

    llm = get_llm_client()
    query = build_attacker_query(
        state["round_topic_name"],
        state["prior_claims"],
    )
    chunks = retrieve_chunks(state["paper_id"], query)

    # Build the user prompt with chunks + prior claims
    chunk_text = "\n\n".join(
        f"[chunk_id: {c['id']}] (page {c.get('page_number', '?')}):\n{c['text']}"
        for c in chunks
    )
    prior = (
        "\n".join(f"- {c}" for c in state["prior_claims"])
        if state["prior_claims"]
        else "(none yet)"
    )
    user_prompt = (
        f"## Retrieved Paper Excerpts\n\n{chunk_text}\n\n"
        f"## Claims Already Raised This Round (DO NOT REPEAT)\n\n{prior}\n\n"
        f"Now identify the single most significant NEW weakness."
    )

    system = attacker_system_prompt(state["round_topic_name"], state["round_topic"])
    output = llm.generate(system, user_prompt)

    # Ensure required fields exist with defaults
    output.setdefault("claim_summary", "")
    output.setdefault("critique_text", "")
    output.setdefault("cited_chunk_ids", [])
    output.setdefault("critique_type", "inconsistency")

    seq = state["sequence_counter"]
    turn = _store_turn(
        state["round_id"],
        state["exchange_number"],
        "attacker",
        seq,
        output,
        state["event_callback"],
    )

    return {
        "attacker_output": output,
        "all_turns": [turn],
        "sequence_counter": seq + 1,
    }


# ---------------------------------------------------------------------------
# Node: Defender
# ---------------------------------------------------------------------------
def defender_node(state: AuditState) -> dict:
    """Retrieve relevant chunks and generate the Defender's rebuttal."""
    logger.info("Exchange %d — Defender", state["exchange_number"])

    llm = get_llm_client()
    attacker = state["attacker_output"]

    query = build_defender_query(
        attacker.get("critique_text", ""),
        attacker.get("cited_chunk_ids", []),
    )
    chunks = retrieve_chunks(state["paper_id"], query)

    chunk_text = "\n\n".join(
        f"[chunk_id: {c['id']}] (page {c.get('page_number', '?')}):\n{c['text']}"
        for c in chunks
    )
    user_prompt = (
        f"## Attacker's Critique\n\n"
        f"**Summary:** {attacker.get('claim_summary', '')}\n\n"
        f"**Full critique:** {attacker.get('critique_text', '')}\n\n"
        f"**Cited chunks:** {attacker.get('cited_chunk_ids', [])}\n\n"
        f"**Type:** {attacker.get('critique_type', '')}\n\n"
        f"## Retrieved Paper Excerpts (for rebuttal)\n\n{chunk_text}\n\n"
        f"Now provide your rebuttal or concession."
    )

    system = defender_system_prompt()
    output = llm.generate(system, user_prompt)

    output.setdefault("rebuttal_text", "")
    output.setdefault("cited_chunk_ids", [])
    output.setdefault("concedes", False)

    seq = state["sequence_counter"]
    turn = _store_turn(
        state["round_id"],
        state["exchange_number"],
        "defender",
        seq,
        output,
        state["event_callback"],
    )

    return {
        "defender_output": output,
        "all_turns": [turn],
        "sequence_counter": seq + 1,
    }


# ---------------------------------------------------------------------------
# Node: Grounding Validator (deterministic — no LLM)
# ---------------------------------------------------------------------------
def validator_node(state: AuditState) -> dict:
    """Validate citations from both Attacker and Defender."""
    logger.info("Exchange %d — Validator", state["exchange_number"])

    attacker_val = validate_attacker_citations(state["attacker_output"])
    defender_val = validate_defender_citations(state["defender_output"])

    # Check if ALL attacker citations are valid (or there are none to check).
    attacker_all_valid = all(v["valid"] for v in attacker_val) if attacker_val else True

    # Store validator results as a turn
    validation_content = {
        "attacker_validations": attacker_val,
        "defender_validations": defender_val,
        "attacker_citations_valid": attacker_all_valid,
    }
    seq = state["sequence_counter"]
    turn = _store_turn(
        state["round_id"],
        state["exchange_number"],
        "validator",
        seq,
        validation_content,
        state["event_callback"],
    )

    return {
        "attacker_validation": attacker_val,
        "defender_validation": defender_val,
        "attacker_valid": attacker_all_valid,
        "all_turns": [turn],
        "sequence_counter": seq + 1,
        # Increment retry counter when attacker citations fail —
        # this is what route_after_validation checks to decide
        # whether to retry or skip the exchange.
        "attacker_retries": state["attacker_retries"] + (0 if attacker_all_valid else 1),
    }


# ---------------------------------------------------------------------------
# Node: Referee
# ---------------------------------------------------------------------------
def referee_node(state: AuditState) -> dict:
    """Adjudicate the exchange using the 3-state verdict matrix."""
    logger.info("Exchange %d — Referee", state["exchange_number"])

    llm = get_llm_client()
    attacker = state["attacker_output"]
    defender = state["defender_output"]

    # Format validation results for the Referee's context
    atk_val_text = json.dumps(state["attacker_validation"], indent=2) or "[]"
    def_val_text = json.dumps(state["defender_validation"], indent=2) or "[]"

    user_prompt = (
        f"## Round Topic: {state['round_topic_name']}\n\n"
        f"## Attacker's Critique\n"
        f"**Summary:** {attacker.get('claim_summary', '')}\n"
        f"**Full text:** {attacker.get('critique_text', '')}\n"
        f"**Type:** {attacker.get('critique_type', '')}\n"
        f"**Citations:** {attacker.get('cited_chunk_ids', [])}\n\n"
        f"## Defender's Rebuttal\n"
        f"**Text:** {defender.get('rebuttal_text', '')}\n"
        f"**Citations:** {defender.get('cited_chunk_ids', [])}\n"
        f"**Concedes:** {defender.get('concedes', False)}\n\n"
        f"## Grounding Validation Results (Authoritative)\n"
        f"**Attacker citations:** {atk_val_text}\n"
        f"**Defender citations:** {def_val_text}\n\n"
        f"Now adjudicate this exchange."
    )

    system = referee_system_prompt()
    output = llm.generate(system, user_prompt)

    output.setdefault("verdict", "CONTESTED")
    output.setdefault("confidence", 0.5)
    output.setdefault("rationale", "")

    # Normalize verdict value
    verdict_type = output["verdict"].upper().replace(" ", "_")
    if verdict_type not in ("SOLIDIFIED", "ACTIONABLE_FLAW", "CONTESTED"):
        verdict_type = "CONTESTED"

    seq = state["sequence_counter"]
    turn = _store_turn(
        state["round_id"],
        state["exchange_number"],
        "referee",
        seq,
        output,
        state["event_callback"],
    )

    # Collect all cited chunk IDs from both sides for the verdict record
    all_cited = list(set(
        attacker.get("cited_chunk_ids", [])
        + defender.get("cited_chunk_ids", [])
    ))

    verdict = _store_verdict(
        state["round_id"],
        state["exchange_number"],
        attacker.get("claim_summary", ""),
        verdict_type,
        output["confidence"],
        output["rationale"],
        all_cited,
        state["event_callback"],
    )

    return {
        "all_turns": [turn],
        "all_verdicts": [verdict],
        "prior_claims": [attacker.get("claim_summary", "")],
        "exchange_number": state["exchange_number"] + 1,
        "attacker_retries": 0,  # reset for next exchange
        "sequence_counter": seq + 1,
    }


# ---------------------------------------------------------------------------
# Node: Skip Exchange (when attacker citations repeatedly fail)
# ---------------------------------------------------------------------------
def skip_exchange_node(state: AuditState) -> dict:
    """Increment exchange counter when attacker can't produce valid cites."""
    logger.warning(
        "Exchange %d skipped — attacker citations failed validation",
        state["exchange_number"],
    )
    return {
        "exchange_number": state["exchange_number"] + 1,
        "attacker_retries": 0,
    }


# ---------------------------------------------------------------------------
# Node: Debrief Card synthesis
# ---------------------------------------------------------------------------
def debrief_node(state: AuditState) -> dict:
    """Generate the round-level Debrief Card from the full transcript."""
    logger.info("Generating Debrief Card …")

    llm = get_llm_client()

    # Build a readable transcript of all turns and verdicts
    transcript_parts: list[str] = []
    for turn in state["all_turns"]:
        agent = turn.get("agent_type", "unknown")
        exch = turn.get("exchange_number", "?")
        content = turn.get("content", {})
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                pass
        transcript_parts.append(
            f"[Exchange {exch} — {agent.upper()}]\n{json.dumps(content, indent=2)}"
        )

    for verdict in state["all_verdicts"]:
        exch = verdict.get("exchange_number", "?")
        transcript_parts.append(
            f"[Exchange {exch} — VERDICT]\n"
            f"Type: {verdict.get('verdict_type')}\n"
            f"Confidence: {verdict.get('confidence')}\n"
            f"Rationale: {verdict.get('rationale')}"
        )

    user_prompt = (
        f"## Round Topic: {state['round_topic_name']}\n\n"
        f"## Full Debate Transcript\n\n"
        + "\n\n---\n\n".join(transcript_parts)
        + "\n\nNow produce the Debrief Card."
    )

    system = debrief_system_prompt()
    output = llm.generate(system, user_prompt)

    output.setdefault("executive_synthesis", "")
    output.setdefault("solidified_strengths", [])
    output.setdefault("actionable_weaknesses", [])
    output.setdefault("contested_points", [])

    # Store in DB
    supabase = get_supabase()
    debrief_id = str(uuid.uuid4())
    supabase.table("debrief_cards").insert({
        "id": debrief_id,
        "round_id": state["round_id"],
        "executive_synthesis": output["executive_synthesis"],
        "solidified_strengths": json.dumps(output["solidified_strengths"]),
        "actionable_weaknesses": json.dumps(output["actionable_weaknesses"]),
        "contested_points": json.dumps(output["contested_points"]),
    }).execute()

    # Update round + audit status
    supabase.table("rounds").update({"status": "completed"}).eq("id", state["round_id"]).execute()
    # Get audit_id from the round
    round_data = supabase.table("rounds").select("audit_id").eq("id", state["round_id"]).execute()
    if round_data.data:
        audit_id = round_data.data[0]["audit_id"]
        supabase.table("audits").update({"status": "completed"}).eq("id", audit_id).execute()

    # Push SSE event
    callback = state["event_callback"]
    if callback:
        try:
            callback({
                "type": "debrief",
                "data": {
                    "id": debrief_id,
                    **output,
                },
            })
            callback({"type": "complete", "data": {}})
        except Exception:
            logger.warning("SSE callback failed for debrief")

    return {
        "status": "completed",
    }


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------
def route_after_validation(state: AuditState) -> str:
    """After validation, decide: proceed to referee, retry attacker, or skip."""
    if not state["attacker_valid"]:
        if state["attacker_retries"] < MAX_ATTACKER_RETRIES:
            logger.info("Attacker citations invalid — retrying")
            return "attacker"
        else:
            logger.info("Attacker citations invalid — max retries, skipping")
            return "skip_exchange"
    return "referee"


def route_after_referee(state: AuditState) -> str:
    """After referee, decide: next exchange or debrief."""
    if state["exchange_number"] > EXCHANGES_PER_ROUND:
        return "debrief"
    return "attacker"


def route_after_skip(state: AuditState) -> str:
    """After skipping, decide: next exchange or debrief."""
    if state["exchange_number"] > EXCHANGES_PER_ROUND:
        return "debrief"
    return "attacker"


# ---------------------------------------------------------------------------
# Build & compile the graph
# ---------------------------------------------------------------------------
def build_audit_graph() -> StateGraph:
    """Construct the LangGraph audit pipeline."""
    graph = StateGraph(AuditState)

    # Nodes
    graph.add_node("attacker", attacker_node)
    graph.add_node("defender", defender_node)
    graph.add_node("validator", validator_node)
    graph.add_node("referee", referee_node)
    graph.add_node("skip_exchange", skip_exchange_node)
    graph.add_node("debrief", debrief_node)

    # Fixed edges
    graph.add_edge("attacker", "defender")
    graph.add_edge("defender", "validator")

    # Conditional edges
    graph.add_conditional_edges(
        "validator",
        route_after_validation,
        {"referee": "referee", "attacker": "attacker", "skip_exchange": "skip_exchange"},
    )
    graph.add_conditional_edges(
        "referee",
        route_after_referee,
        {"attacker": "attacker", "debrief": "debrief"},
    )
    graph.add_conditional_edges(
        "skip_exchange",
        route_after_skip,
        {"attacker": "attacker", "debrief": "debrief"},
    )
    graph.add_edge("debrief", END)

    # Entry point
    graph.set_entry_point("attacker")

    return graph


# Pre-compile the graph (stateless — safe to share across threads)
_compiled_graph = None


def get_compiled_graph():
    """Return the compiled graph (singleton)."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_audit_graph().compile()
    return _compiled_graph


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_audit(
    paper_id: str,
    round_id: str,
    round_topic: str,
    event_callback: Callable[[dict], None] | None = None,
) -> dict:
    """
    Execute a full audit round.

    This is a BLOCKING call — run it in a background thread so the API
    endpoint can return immediately.

    Parameters
    ----------
    paper_id : UUID of the uploaded paper
    round_id : UUID of the round row
    round_topic : topic slug (e.g. 'novelty_scope')
    event_callback : optional callable to receive SSE events

    Returns
    -------
    Final AuditState dict.
    """
    topic_name = ROUND_TOPICS.get(round_topic, round_topic)

    initial_state: AuditState = {
        "paper_id": paper_id,
        "round_id": round_id,
        "round_topic": round_topic,
        "round_topic_name": topic_name,
        "exchange_number": 1,
        "attacker_retries": 0,
        "prior_claims": [],
        "all_turns": [],
        "all_verdicts": [],
        "attacker_output": {},
        "defender_output": {},
        "attacker_validation": [],
        "defender_validation": [],
        "attacker_valid": True,
        "event_callback": event_callback,
        "status": "in_progress",
        "error": "",
        "sequence_counter": 1,
    }

    compiled = get_compiled_graph()
    try:
        result = compiled.invoke(initial_state)
        return result
    except Exception as exc:
        logger.exception("Audit graph failed: %s", exc)
        # Update audit status to error
        try:
            supabase = get_supabase()
            round_data = supabase.table("rounds").select("audit_id").eq("id", round_id).execute()
            if round_data.data:
                audit_id = round_data.data[0]["audit_id"]
                supabase.table("audits").update({"status": "error"}).eq("id", audit_id).execute()
            supabase.table("rounds").update({"status": "error"}).eq("id", round_id).execute()
        except Exception:
            logger.error("Failed to update audit status after error")
        if event_callback:
            try:
                event_callback({"type": "error", "data": {"message": str(exc)}})
            except Exception:
                pass
        raise
