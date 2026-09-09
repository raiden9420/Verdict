"""
LangGraph state machine for the adversarial audit.

Graph: Attacker → Attacker Validator → Defender → Defender Validator → Referee
         ↑ retry invalid attack                                      (×3)
                                                                    → Debrief → END

Only accepted exchange artifacts are stored in the DB and pushed through
`event_callback`; rejected Attacker attempts remain retry-only state.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, TypedDict, Annotated, Callable
import operator

from langgraph.graph import StateGraph, END

from app.constants import (
    EXCHANGES_PER_ROUND,
    MAX_ATTACKER_RETRIES,
    ROUND_TOPICS,
    SELF_CONSISTENCY_THRESHOLD,
)

from app.database import get_supabase
from app.services.llm_client import get_llm_client, generate_structured_with_meta
from app.services.retrieval_service import (
    retrieve_chunks,
    build_attacker_query,
    build_defender_query,
)
from app.agents.prompts import (
    AUDIT_PROMPT_VERSION,
    attacker_system_prompt,
    defender_system_prompt,
    referee_system_prompt,
    debrief_system_prompt,
)
from app.agents.grounding_validator import (
    validate_attacker_citations,
    validate_citation_critique,
    validate_defender_citations,
)
from app.agents.schemas import (
    AttackerOutput,
    DefenderOutput,
    RefereeOutput,
    DebriefOutput,
)
from app.services.literature_search_service import (
    filter_candidates_already_referenced,
    normalize_title,
    search_external_literature,
    rank_candidates_by_novelty_overlap,
)


logger = logging.getLogger(__name__)


def _provenance(client: Any, provider: str) -> dict:
    """Server-owned generation metadata; never accepted from model output."""
    model = getattr(client, "model_name", None)
    return {
        "provider": provider,
        "model": model if isinstance(model, str) else type(client).__name__,
        "prompt_version": AUDIT_PROMPT_VERSION,
    }


def _repeated_critique(state: "AuditState") -> bool:
    """Reject literal repeats; avoid fuzzy guesses about distinct arguments."""
    def key(text: Any) -> str:
        return re.sub(r"[^\w]+", " ", str(text or "").casefold()).strip()

    attacker = state["attacker_output"]
    summary = key(attacker.get("claim_summary"))
    if summary and any(summary == key(prior) for prior in state.get("prior_claims", [])):
        return True
    critique = key(attacker.get("critique_text"))
    for turn in state.get("all_turns", []):
        if turn.get("agent_type") != "attacker":
            continue
        content = turn.get("content", {})
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (ValueError, TypeError):
                continue
        if isinstance(content, dict) and critique and critique == key(content.get("critique_text")):
            return True
    return False


def _validation_checks(results: list[dict]) -> list[dict]:
    """Compact validation metadata without duplicating private source text."""
    return [{key: value for key, value in result.items() if key != "chunk_text"} for result in results]


def _referee_sources(state: "AuditState") -> list[dict]:
    """Provide each cited passage once within a fixed adjudication budget."""
    sources: dict[str, dict] = {}
    for result in [*state["attacker_validation"], *state["defender_validation"]]:
        chunk_id = result.get("chunk_id")
        text = result.get("chunk_text")
        if isinstance(chunk_id, str) and isinstance(text, str) and text:
            sources.setdefault(chunk_id, result)
    # Normal one-to-three-citation exchanges retain complete ~400-word chunks.
    # A model returning the schema's maximum citation count still cannot create
    # an unbounded Referee prompt. Explicit truncation prevents false certainty.
    per_source_budget = min(6000, 36_000 // max(1, len(sources)))
    return [{
        "chunk_id": chunk_id,
        "page_number": result.get("page_number"),
        "section": result.get("section"),
        "text": result["chunk_text"][:per_source_budget],
        "source_truncated": bool(result.get("source_truncated")) or len(result["chunk_text"]) > per_source_budget,
    } for chunk_id, result in sources.items()]


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------
class AuditState(TypedDict):
    # Fixed context (set once)
    paper_id: str
    round_id: str
    round_topic: str        # slug
    round_topic_name: str   # display name
    strictness_level: str
    domain: str

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
    external_search_results: list
    external_validation: list
    reference_list: list
    paper_context: str
    attacker_valid: bool


    # Retry context — populated when attacker citations fail validation,
    # so the retried attacker call knows what went wrong.
    last_failed_critique: dict
    last_failure_reason: str

    # SSE streaming callback: Callable[[dict], None] or None
    event_callback: Any

    # Status
    status: str
    error: str

    # Sequence counter for turn ordering
    sequence_counter: int


def _load_reference_list(paper_id: str) -> list[dict]:
    """Load one paper's stored bibliography once at round startup.

    Compatibility with a not-yet-migrated deployment is deliberately graceful:
    citation_integrity becomes unavailable, while every other audit path remains
    usable.
    """
    try:
        result = (
            get_supabase()
            .table("papers")
            .select("reference_list")
            .eq("id", paper_id)
            .limit(1)
            .execute()
        )
        stored = result.data[0].get("reference_list") if result.data else []
        if isinstance(stored, str):
            stored = json.loads(stored)
        if not isinstance(stored, list):
            return []
        return [
            entry
            for entry in stored
            if isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and isinstance(entry.get("title"), str)
            and entry["id"].strip()
            and entry["title"].strip()
        ]
    except Exception as exc:
        logger.warning(
            "Could not load papers.reference_list; citation_integrity disabled: %s",
            type(exc).__name__,
        )
        return []


def _canonicalize_supplied_candidates(
    cited: list[dict],
    supplied_candidates: list[dict],
) -> list[dict] | None:
    """Replace LLM-copied metadata with the exact prompt-supplied candidates."""
    canonical: list[dict] = []
    for requested in cited:
        if not isinstance(requested, dict):
            return None
        requested_title = normalize_title(str(requested.get("title") or ""))
        requested_source = str(requested.get("source") or "").casefold().strip()
        match = next(
            (
                candidate
                for candidate in supplied_candidates
                if isinstance(candidate, dict)
                and requested_title
                and normalize_title(str(candidate.get("title") or ""))
                == requested_title
                and str(candidate.get("source") or "").casefold().strip()
                == requested_source
            ),
            None,
        )
        if match is None:
            return None
        canonical.append(dict(match))
    return canonical


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
        state["round_topic"],
    )
    chunks = retrieve_chunks(state["paper_id"], query)

    # Build the user prompt with chunks + prior claims
    chunk_text = "\n\n".join(
        f"[chunk_id: {c['id']}] (page {c.get('page_number', '?')}):\n{c['text']}"
        for c in chunks
    )
    # Topic-retrieved paper text is the relevance corpus for any citation the
    # Attacker actually selects. It stays in graph state only for this exchange.
    paper_context = "\n\n".join(
        str(chunk.get("text") or "") for chunk in chunks
    )[:8000]
    prior = (
        "\n".join(f"- {c}" for c in state["prior_claims"])
        if state["prior_claims"]
        else "(none yet)"
    )
    # If this is a retry after a failed citation validation, include
    # context about the rejected attempt so the LLM can course-correct.
    retry_section = ""
    if state["attacker_retries"] > 0 and state.get("last_failed_critique"):
        failed = state["last_failed_critique"]
        reason = state.get("last_failure_reason", "Unknown validation failure")
        retry_section = (
            f"## ⚠️ Previous Attempt Rejected\n\n"
            f"Your last critique was rejected by the evidence or novelty check. "
            f"Do NOT repeat this critique or use the "
            f"same citations.\n\n"
            f"**Rejected critique:** {failed.get('critique_text', '')}\n\n"
            f"**Failure reason:** {reason}\n\n"
        )

    reproducibility_section = ""
    if state["round_topic"] == "reproducibility":
        try:
            supabase = get_supabase()
            paper_res = supabase.table("papers").select("reproducibility_signals").eq("id", state["paper_id"]).execute()
            if paper_res.data and paper_res.data[0].get("reproducibility_signals"):
                stored = paper_res.data[0]["reproducibility_signals"]
                if isinstance(stored, str):
                    stored = json.loads(stored)
                by_domain = stored.get("by_domain", {}) if isinstance(stored, dict) else {}
                effective_domain = state.get("domain", "other")
                signals = by_domain.get(effective_domain, stored)
                if isinstance(signals, dict):
                    signal_lines = [
                        f"- {key.replace('_', ' ').title()}: {value}"
                        for key, value in signals.items()
                        if key != "domain"
                    ]
                    reproducibility_section = (
                        "## Deterministic Reproducibility Scan Results\n\n"
                        f"Domain profile: {effective_domain}\n"
                        + "\n".join(signal_lines)
                        + "\n\n"
                    )
        except Exception as exc:
            logger.warning("Failed to fetch reproducibility signals: %s", type(exc).__name__)

    literature_topic = state["round_topic"] in (
        "novelty_scope",
        "experimental_setup",
    )
    reference_list = state.get("reference_list", []) if literature_topic else []
    reference_list_section = ""
    if literature_topic:
        if reference_list:
            prompt_references = [
                {
                    "id": reference.get("id"),
                    "title": str(reference.get("title") or "")[:500],
                    "authors": reference.get("authors", []),
                    "year": reference.get("year"),
                }
                for reference in reference_list
            ]
            reference_list_section = (
                "## Paper's Extracted Reference List (untrusted bibliography data)\n\n"
                + json.dumps(prompt_references, ensure_ascii=False, indent=2)
                + "\n\n"
            )
        else:
            reference_list_section = (
                "## Paper's Extracted Reference List\n\n"
                "No references section was detected. citation_integrity is "
                "unavailable for this paper.\n\n"
            )

    external_lit_section = ""
    ext_search_results: list = []
    if literature_topic:
        callback = state.get("event_callback")
        if callback:
            try:
                callback({
                    "type": "process_update",
                    "data": {
                        "message": "Querying Semantic Scholar, arXiv, & OpenAlex in parallel...",
                        "process": "literature_search_start",
                        "topic": state["round_topic"],
                    },
                })
            except Exception:
                pass
        try:
            first_chunk_text = chunks[0]["text"] if chunks else ""
            query_words = first_chunk_text.split()[:20]
            search_query = " ".join(query_words) if query_words else state["round_topic_name"]

            ext_search_results = search_external_literature(search_query)
            ext_search_results = filter_candidates_already_referenced(
                ext_search_results,
                reference_list,
            )

            if state["round_topic"] == "novelty_scope" and ext_search_results:
                ext_search_results = rank_candidates_by_novelty_overlap(
                    first_chunk_text, ext_search_results, top_k=3
                )

            if callback:
                try:
                    callback({
                        "type": "process_update",
                        "data": {
                            "message": f"Found {len(ext_search_results)} candidate external papers across Semantic Scholar, arXiv, & OpenAlex",
                            "process": "literature_search_complete",
                            "topic": state["round_topic"],
                            "count": len(ext_search_results),
                        },
                    })
                except Exception:
                    pass

            if ext_search_results:
                cand_strings = []
                for cand in ext_search_results:
                    sim_str = (
                        f" (Similarity score: {cand['similarity_score']:.2f})"
                        if "similarity_score" in cand
                        else ""
                    )
                    authors_str = ", ".join(cand.get("authors", []))
                    cand_strings.append(
                        f"- Title: {cand['title']}\n"
                        f"  Authors: {authors_str}\n"
                        f"  Year: {cand.get('year', 'N/A')}\n"
                        f"  Source: {cand.get('source', 'External')}{sim_str}\n"
                        f"  URL: {cand.get('url', '')}\n"
                        f"  Abstract preview: {cand.get('abstract', '')[:250]}"
                    )
                external_lit_section = (
                    "## Retrieved External Literature Candidates\n\n"
                    + "\n\n".join(cand_strings)
                    + "\n\n"
                )
        except Exception as exc:
            logger.warning("Literature search in Attacker node failed: %s", type(exc).__name__)

        if not ext_search_results:
            external_lit_section = (
                "## Retrieved External Literature Candidates\n\n"
                "No external candidates were retrieved. You MUST leave "
                "external_citations empty and ground the critique in the paper "
                "or make a genuine omission critique.\n\n"
            )

    user_prompt = (
        f"## Retrieved Paper Excerpts\n\n{chunk_text}\n\n"
        + reproducibility_section
        + reference_list_section
        + external_lit_section
        + f"## Claims Already Raised This Round (DO NOT REPEAT)\n\n{prior}\n\n"
        + retry_section
        + f"Now identify the single most significant NEW weakness."
    )

    system = attacker_system_prompt(
        state["round_topic_name"],
        state["round_topic"],
        state.get("strictness_level", "standard"),
        state.get("domain", "other"),
        has_reference_list=bool(reference_list),
    )
    output, serving_client, provider = generate_structured_with_meta(
        llm,
        system,
        user_prompt,
        AttackerOutput,
    )
    output["provenance"] = _provenance(serving_client, provider)
    output["evidence_scope"] = {
        "retrieved_chunk_ids": [chunk["id"] for chunk in chunks],
        "retrieval_methods": sorted({str(chunk.get("retrieval_method") or "vector") for chunk in chunks}),
        "reference_count": len(reference_list),
    }

    if literature_topic:
        output["external_search_performed"] = True
        output["external_sources"] = ["Semantic Scholar", "arXiv", "OpenAlex"]
        output["external_candidate_count"] = len(ext_search_results)
        if output.get("critique_type") != "missing_baseline" or not ext_search_results:
            output["external_citations"] = []
        if output.get("critique_type") != "citation_integrity":
            output["cited_reference_id"] = None
    else:
        # Models occasionally volunteer plausible-sounding outside papers even
        # when no literature search was performed. Such metadata is not evidence
        # and must not make an otherwise valid in-document/omission critique fail.
        output["external_citations"] = []
        output["cited_reference_id"] = None


    # Do not persist or stream this attempt yet.  The deterministic Attacker
    # validator is the acceptance boundary; rejected attempts must never enter
    # the transcript or the Debrief Card.
    return {
        "attacker_output": output,
        "external_search_results": ext_search_results,
        "paper_context": paper_context,
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
        f"**Cited reference ID:** {attacker.get('cited_reference_id')}\n\n"
        f"**Citation metadata:** "
        f"{json.dumps(attacker.get('external_citations', []), indent=2)}\n\n"
        f"**Deterministic citation check:** "
        f"{json.dumps(state.get('external_validation', []), indent=2)}\n\n"
        f"**Type:** {attacker.get('critique_type', '')}\n\n"
        f"## Retrieved Paper Excerpts (for rebuttal)\n\n{chunk_text}\n\n"
        f"Now provide your rebuttal or concession."
    )

    system = defender_system_prompt()
    output, serving_client, provider = generate_structured_with_meta(
        llm,
        system,
        user_prompt,
        DefenderOutput,
    )
    output["provenance"] = _provenance(serving_client, provider)
    output["evidence_scope"] = {
        "retrieved_chunk_ids": [chunk["id"] for chunk in chunks],
        "retrieval_methods": sorted({str(chunk.get("retrieval_method") or "vector") for chunk in chunks}),
    }

    return {
        "defender_output": output,
    }


# ---------------------------------------------------------------------------
# Nodes: Grounding Validators (deterministic — no LLM)
# ---------------------------------------------------------------------------
def attacker_validator_node(state: AuditState) -> dict:
    """Accept an Attacker attempt only after all claimed evidence is valid."""
    logger.info("Exchange %d — Attacker Validator", state["exchange_number"])

    attacker = state["attacker_output"]
    critique_type = attacker.get("critique_type")
    repeated = _repeated_critique(state)
    attacker_val = [] if repeated else validate_attacker_citations(attacker, paper_id=state["paper_id"])
    canonical_attacker = dict(attacker)
    provenance_valid = True

    if repeated:
        external_val = []
    elif critique_type == "missing_baseline":
        canonical_citations = _canonicalize_supplied_candidates(
            attacker.get("external_citations", []),
            state.get("external_search_results", []),
        )
        provenance_valid = canonical_citations is not None and bool(canonical_citations)
        if provenance_valid:
            canonical_attacker["external_citations"] = canonical_citations
            external_val = validate_citation_critique(
                canonical_attacker,
                state.get("reference_list", []),
                state.get("paper_context", ""),
            )
        else:
            external_val = [{
                "citation_index": 0,
                "citation_type": "missing_baseline",
                "reference_id": None,
                "title": str(
                    (attacker.get("external_citations") or [{}])[0].get("title", "")
                ),
                "exists": False,
                "relevant": False,
                "valid": False,
                "similarity_score": None,
                "validation_complete": True,
                "reason": "candidate_not_supplied",
            }]
    elif critique_type == "citation_integrity":
        external_val = validate_citation_critique(
            canonical_attacker,
            state.get("reference_list", []),
            state.get("paper_context", ""),
        )
    else:
        external_val = []

    ext_cites = canonical_attacker.get("external_citations", [])

    def validation_completed(result: dict) -> bool:
        if "validation_complete" in result:
            return bool(result.get("validation_complete"))
        return result.get("reason") in {
            "verified",
            "citation_not_found",
            "topically_unrelated",
        }

    has_chunks = bool(attacker.get("cited_chunk_ids"))
    chunk_valid = len(attacker_val) == len(attacker.get("cited_chunk_ids", [])) and all(
        result.get("valid", False) for result in attacker_val
    )

    if critique_type == "citation_integrity":
        # The stored-reference membership is the Attacker grounding boundary.
        # A completed false health result is the critique finding and must reach
        # the debate; only an unknown ID or unavailable check is retryable.
        reference_id = attacker.get("cited_reference_id")
        reference_member = any(
            isinstance(reference, dict) and reference.get("id") == reference_id
            for reference in state.get("reference_list", [])
        )
        citation_evidence_valid = (
            reference_member
            and len(external_val) == 1
            and all(validation_completed(result) for result in external_val)
            and all(
                result.get("reason") not in {
                    "reference_not_in_paper",
                    "citation_title_missing",
                    "existence_check_unavailable",
                    "relevance_check_unavailable",
                }
                for result in external_val
            )
        )
    elif critique_type == "missing_baseline":
        citation_evidence_valid = (
            provenance_valid
            and len(external_val) == len(ext_cites)
            and bool(external_val)
            and all(validation_completed(result) for result in external_val)
            and all(result.get("valid", False) for result in external_val)
        )
    else:
        citation_evidence_valid = False

    evidence_required = critique_type != "omission"
    if critique_type in {"citation_integrity", "missing_baseline"}:
        has_valid_evidence_kind = citation_evidence_valid
    else:
        has_valid_evidence_kind = has_chunks or not evidence_required
    attacker_all_valid = (
        not repeated
        and has_valid_evidence_kind
        and (not has_chunks or chunk_valid)
    )

    if not attacker_all_valid:
        failure_parts: list[str] = []
        if repeated:
            failure_parts.append("duplicate_critique: this exact concern was already adjudicated; choose a different substantive issue")
        if evidence_required and not has_chunks and critique_type not in {
            "citation_integrity",
            "missing_baseline",
        }:
            failure_parts.append("non-omission critique supplied no evidence")
        for result in attacker_val:
            if not result.get("valid"):
                failure_parts.append(
                    f"chunk {result.get('chunk_id') or 'missing'}: "
                    f"{result.get('reason', 'citation validation failed')}"
                )
        for result in external_val:
            is_integrity_finding = (
                critique_type == "citation_integrity"
                and validation_completed(result)
                and result.get("reason") in {
                    "citation_not_found",
                    "topically_unrelated",
                    "verified",
                }
            )
            if not is_integrity_finding and not result.get("valid"):
                failure_parts.append(
                    f"external paper '{result.get('title', '')}': "
                    f"{result.get('reason', 'citation validation failed')}"
                )

        callback = state.get("event_callback")
        if callback:
            try:
                callback({
                    "type": "process_update",
                    "data": {
                        "message": "Checking a different concern after an evidence or repetition check...",
                        "process": "attacker_retry",
                        "exchange_number": state["exchange_number"],
                    },
                })
            except Exception:
                logger.warning("SSE callback failed for attacker retry update")

        return {
            "attacker_validation": attacker_val,
            "external_validation": external_val,
            "attacker_valid": False,
            "attacker_retries": state["attacker_retries"] + 1,
            "last_failed_critique": attacker,
            "last_failure_reason": "; ".join(failure_parts) or "citation validation failed",
        }

    # Materialize only server-resolved metadata. This keeps the existing UI's
    # citation cards useful for both paths without trusting model-copied details.
    accepted_attacker = canonical_attacker
    if critique_type == "citation_integrity":
        reference = next(
            reference
            for reference in state.get("reference_list", [])
            if reference.get("id") == attacker.get("cited_reference_id")
        )
        validation = external_val[0]
        accepted_attacker["external_citations"] = [{
            "title": reference.get("title", ""),
            "authors": reference.get("authors", []),
            "year": reference.get("year"),
            "url": validation.get("url", ""),
            "source": (
                validation.get("source", "External Literature")
                if validation.get("matched_title")
                else "Paper reference list"
            ),
            "similarity_score": validation.get("similarity_score"),
            "validated": validation.get("valid", False),
            "reference_id": reference.get("id"),
        }]
    else:
        accepted_attacker["external_citations"] = [
            {
                **citation,
                "similarity_score": validation.get("similarity_score"),
                "validated": validation.get("valid", False),
            }
            for citation, validation in zip(ext_cites, external_val)
        ]
    seq = state["sequence_counter"]
    turn = _store_turn(
        state["round_id"],
        state["exchange_number"],
        "attacker",
        seq,
        accepted_attacker,
        state["event_callback"],
    )
    return {
        "attacker_output": accepted_attacker,
        "attacker_validation": attacker_val,
        "external_validation": external_val,
        "attacker_valid": True,
        "all_turns": [turn],
        "sequence_counter": seq + 1,
    }


def defender_validator_node(state: AuditState) -> dict:
    """Validate and persist the Defender plus the combined validation turn."""
    logger.info("Exchange %d — Defender Validator", state["exchange_number"])
    defender = state["defender_output"]
    defender_val = validate_defender_citations(defender, paper_id=state["paper_id"])
    defender_valid = (
        defender.get("concedes", False)
        or (
            len(defender_val) == len(defender.get("cited_chunk_ids", []))
            and bool(defender_val)
            and all(result.get("valid", False) for result in defender_val)
        )
    )

    seq = state["sequence_counter"]
    defender_turn = _store_turn(
        state["round_id"],
        state["exchange_number"],
        "defender",
        seq,
        defender,
        state["event_callback"],
    )
    validation_content = {
        "attacker_validations": state["attacker_validation"],
        "defender_validations": defender_val,
        "external_validations": state.get("external_validation", []),
        "attacker_citations_valid": True,
        "defender_citations_valid": defender_valid,
    }
    validator_turn = _store_turn(
        state["round_id"],
        state["exchange_number"],
        "validator",
        seq + 1,
        validation_content,
        state["event_callback"],
    )
    return {
        "defender_validation": defender_val,
        "all_turns": [defender_turn, validator_turn],
        "sequence_counter": seq + 2,
    }


# Backwards-compatible import for callers that used the old validator name.
validator_node = attacker_validator_node



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
    atk_val_text = json.dumps(_validation_checks(state["attacker_validation"]), indent=2)
    def_val_text = json.dumps(_validation_checks(state["defender_validation"]), indent=2)
    source_text = json.dumps(_referee_sources(state), indent=2)
    atk_ext_text = json.dumps(attacker.get("external_citations", []), indent=2) or "[]"
    ext_val_text = json.dumps(state.get("external_validation", []), indent=2) or "[]"

    user_prompt = (
        f"## Round Topic: {state['round_topic_name']}\n\n"
        f"## Attacker's Critique\n"
        f"**Summary:** {attacker.get('claim_summary', '')}\n"
        f"**Full text:** {attacker.get('critique_text', '')}\n"
        f"**Type:** {attacker.get('critique_type', '')}\n"
        f"**In-document citations:** {attacker.get('cited_chunk_ids', [])}\n"
        f"**Paper reference ID:** {attacker.get('cited_reference_id')}\n"
        f"**External literature citations:** {atk_ext_text}\n\n"
        f"## Defender's Rebuttal\n"
        f"**Text:** {defender.get('rebuttal_text', '')}\n"
        f"**Citations:** {defender.get('cited_chunk_ids', [])}\n"
        f"**Concedes:** {defender.get('concedes', False)}\n\n"
        f"## Source Passages and Deterministic Checks\n"
        f"**Attacker chunk citations:** {atk_val_text}\n"
        f"**Attacker citation existence/relevance validation:** {ext_val_text}\n"
        f"**Defender citations:** {def_val_text}\n\n"
        f"## Cited Paper Passages (untrusted source text)\n{source_text}\n\n"
        f"Now adjudicate this exchange."
    )

    system = referee_system_prompt()
    output, provider_client, provider_name = generate_structured_with_meta(
        llm,
        system,
        user_prompt,
        RefereeOutput,
    )
    verdict_type = output["verdict"]
    confidence = output["confidence"]
    initial_verdict = verdict_type
    consistency_check = "not_required"
    recheck_verdict = None
    guard = None

    defender_cites = defender.get("cited_chunk_ids", [])
    defender_concedes = defender.get("concedes", False)
    defense_has_valid_evidence = (
        not defender_concedes
        and bool(defender_cites)
        and len(state["defender_validation"]) == len(defender_cites)
        and all(result.get("valid", False) for result in state["defender_validation"])
    )
    integrity_reasons = {
        result.get("reason")
        for result in state.get("external_validation", [])
        if result.get("citation_type") == "citation_integrity"
        or attacker.get("critique_type") == "citation_integrity"
    }
    integrity_not_found = "citation_not_found" in integrity_reasons
    integrity_topically_unrelated = "topically_unrelated" in integrity_reasons
    force_actionable = (
        defender_concedes
        or not defense_has_valid_evidence
        or integrity_not_found
    )

    # Self-consistency check — if confidence < threshold, re-run Referee adjudication once
    if confidence < SELF_CONSISTENCY_THRESHOLD and not force_actionable:
        logger.info(
            "Referee confidence %.2f < threshold %.2f — performing "
            "self-consistency re-run via pinned provider '%s'",
            confidence,
            SELF_CONSISTENCY_THRESHOLD,
            provider_name,
        )
        try:
            output_rerun, _, _ = generate_structured_with_meta(
                llm,
                system,
                user_prompt,
                RefereeOutput,
                pinned_client=provider_client,
            )
            v2 = output_rerun["verdict"]
            recheck_verdict = v2
            consistency_check = "agreed" if v2 == verdict_type else "disagreed"

            if v2 != verdict_type:
                logger.warning(
                    "Self-consistency disagreement: initial '%s' vs re-run '%s' — forcing CONTESTED",
                    verdict_type,
                    v2,
                )
                output["rationale"] = (
                    f"{output.get('rationale', '')} [Self-Consistency Note: Initial adjudication "
                    f"resulted in '{verdict_type}', but re-run resulted in '{v2}'. "
                    f"Forcing verdict to CONTESTED due to adjudication disagreement.]"
                )
                verdict_type = "CONTESTED"
                output["verdict"] = "CONTESTED"
            else:
                logger.info("Self-consistency re-run agreed on '%s'", verdict_type)
        except Exception as exc:
            consistency_check = "unavailable"
            logger.warning(
                "Self-consistency re-run failed on pinned provider '%s': %s",
                provider_name,
                type(exc).__name__,
            )

    # Deterministic trust boundary: a concession, missing evidence, or any
    # invalid/cross-paper defense citation is always an actionable flaw.  The
    # model may explain evidence quality, but cannot override this invariant.
    if force_actionable:
        if integrity_not_found:
            guard = "citation_not_found"
            reason = "The paper's cited reference could not be verified as an existing work."
        elif defender_concedes:
            guard = "defender_concession"
            reason = "The Defender conceded the critique."
        else:
            guard = "invalid_defense_evidence"
            reason = "The Defender did not provide fully validated in-paper evidence."
        if verdict_type != "ACTIONABLE_FLAW":
            output["rationale"] = f"{output['rationale']} [Grounding guard: {reason}]"
        verdict_type = "ACTIONABLE_FLAW"
        output["verdict"] = verdict_type
    elif integrity_topically_unrelated and verdict_type == "SOLIDIFIED":
        guard = "topically_unrelated"
        # Topical similarity is intentionally a coarse signal, so it should not
        # force a flaw by itself. It does, however, make a fully solidified
        # defense unsafe without the deferred claim-level citation check.
        output["rationale"] = (
            f"{output['rationale']} [Citation-integrity guard: the cited work "
            "exists but appears topically unrelated; human review is required.]"
        )
        verdict_type = "CONTESTED"
        output["verdict"] = verdict_type

    output["provenance"] = _provenance(provider_client, provider_name)
    output["adjudication"] = {
        "initial_verdict": initial_verdict,
        "guard": guard,
        "consistency_check": consistency_check,
        "recheck_verdict": recheck_verdict,
        "confidence_kind": "uncalibrated_model_assessment",
    }

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
    all_cited = sorted(set(
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
        # Clear retry context for the next exchange
        "last_failed_critique": {},
        "last_failure_reason": "",
    }


# ---------------------------------------------------------------------------
# Node: Debrief Card synthesis
# ---------------------------------------------------------------------------
def debrief_node(state: AuditState) -> dict:
    """Generate the round-level Debrief Card from the full transcript."""
    logger.info("Generating Debrief Card …")

    verdict_count = len(state["all_verdicts"])
    verdict_exchanges = {
        verdict.get("exchange_number") for verdict in state["all_verdicts"]
    }
    expected_exchanges = set(range(1, EXCHANGES_PER_ROUND + 1))
    if verdict_count != EXCHANGES_PER_ROUND or verdict_exchanges != expected_exchanges:
        raise RuntimeError(
            "Refusing to complete an incomplete audit: expected exactly "
            f"{EXCHANGES_PER_ROUND} adjudicated exchanges, received "
            f"{verdict_count} with exchange IDs {sorted(verdict_exchanges, key=str)}"
        )

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
        if agent == "validator" and isinstance(content, dict):
            # The Referee already read the sources. Synthesis consumes its
            # adjudication plus all check outcomes, avoiding another copy of
            # every private passage for each of the three exchanges.
            content = {
                **content,
                "attacker_validations": _validation_checks(content.get("attacker_validations", [])),
                "defender_validations": _validation_checks(content.get("defender_validations", [])),
            }
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
    output, _, _ = generate_structured_with_meta(
        llm,
        system,
        user_prompt,
        DebriefOutput,
    )

    supabase = get_supabase()
    reproducibility_signals = None
    if state["round_topic"] == "reproducibility":
        try:
            paper_res = supabase.table("papers").select("reproducibility_signals").eq("id", state["paper_id"]).execute()
            if paper_res.data:
                stored = paper_res.data[0].get("reproducibility_signals")
                if isinstance(stored, str):
                    stored = json.loads(stored)
                if isinstance(stored, dict):
                    reproducibility_signals = stored.get("by_domain", {}).get(
                        state.get("domain", "other"), stored
                    )
        except Exception as exc:
            logger.warning("Failed to fetch reproducibility signals in debrief: %s", type(exc).__name__)

    if reproducibility_signals:
        output["reproducibility_checklist"] = reproducibility_signals

    # Store in DB
    debrief_id = str(uuid.uuid4())
    supabase.table("debrief_cards").insert({
        "id": debrief_id,
        "round_id": state["round_id"],
        "executive_synthesis": output["executive_synthesis"],
        "solidified_strengths": json.dumps(output["solidified_strengths"]),
        "actionable_weaknesses": json.dumps(output["actionable_weaknesses"]),
        "contested_points": json.dumps(output["contested_points"]),
    }).execute()


    # The inner graph owns only this round. The Phase 3 outer orchestrator marks
    # the audit complete after every selected topic and final synthesis finish.
    supabase.table("rounds").update({"status": "completed"}).eq("id", state["round_id"]).execute()

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
        except Exception:
            logger.warning("SSE callback failed for debrief")

    return {
        "status": "completed",
    }


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------
def route_after_validation(state: AuditState) -> str:
    """After Attacker validation, proceed to Defender or regenerate."""
    if not state["attacker_valid"]:
        if state["attacker_retries"] <= MAX_ATTACKER_RETRIES:
            logger.info("Attacker citations invalid — retrying")
            return "attacker"
        raise RuntimeError(
            "Attacker failed to produce a grounded critique after "
            f"{state['attacker_retries']} attempts; audit was not completed"
        )
    return "defender"


def route_after_referee(state: AuditState) -> str:
    """After referee, decide: next exchange or debrief."""
    verdict_count = len(state["all_verdicts"])
    if verdict_count > EXCHANGES_PER_ROUND:
        raise RuntimeError("Audit produced more verdicts than configured exchanges")
    if verdict_count == EXCHANGES_PER_ROUND:
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
    graph.add_node("attacker_validator", attacker_validator_node)
    graph.add_node("defender", defender_node)
    graph.add_node("defender_validator", defender_validator_node)
    graph.add_node("referee", referee_node)
    graph.add_node("debrief", debrief_node)

    # Fixed edges
    graph.add_edge("attacker", "attacker_validator")
    graph.add_edge("defender", "defender_validator")
    graph.add_edge("defender_validator", "referee")

    # Conditional edges
    graph.add_conditional_edges(
        "attacker_validator",
        route_after_validation,
        {"defender": "defender", "attacker": "attacker"},
    )
    graph.add_conditional_edges(
        "referee",
        route_after_referee,
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
    strictness_level: str = "standard",
    domain: str = "other",
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
    reference_list = (
        _load_reference_list(paper_id)
        if round_topic in {"novelty_scope", "experimental_setup"}
        else []
    )

    initial_state: AuditState = {
        "paper_id": paper_id,
        "round_id": round_id,
        "round_topic": round_topic,
        "round_topic_name": topic_name,
        "strictness_level": strictness_level,
        "domain": domain,
        "exchange_number": 1,
        "attacker_retries": 0,
        "prior_claims": [],
        "all_turns": [],
        "all_verdicts": [],
        "attacker_output": {},
        "defender_output": {},
        "attacker_validation": [],
        "defender_validation": [],
        "external_search_results": [],
        "external_validation": [],
        "reference_list": reference_list,
        "paper_context": "",
        "attacker_valid": True,

        "last_failed_critique": {},
        "last_failure_reason": "",
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
        logger.error("Audit graph failed (%s)", type(exc).__name__)
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
        raise
