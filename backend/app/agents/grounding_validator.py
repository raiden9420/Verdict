"""
Grounding Validator — deterministic, NO LLM call.

Validates every citation from both Attacker and Defender:
1. Confirm the cited chunk exists in the database.
2. Compute embedding similarity between the claim and the chunk text.
3. Flag invalid if similarity < GROUNDING_SIMILARITY_THRESHOLD.

Attacker omission critiques and Defender concessions have no citation to
check. Non-conceding Defenders must provide valid paper-local evidence.
"""

import logging
from app.constants import (
    GROUNDING_SIMILARITY_THRESHOLD,
    LEXICAL_GROUNDING_SIMILARITY_THRESHOLD,
)
from app.services.embedding_service import embed_batch, cosine_similarity
from app.services.retrieval_service import get_chunk_by_id, lexical_similarity

logger = logging.getLogger(__name__)


def validate_citation(
    chunk_id: str,
    claim_text: str,
    paper_id: str | None = None,
) -> dict:
    """
    Validate a single citation.

    Parameters
    ----------
    chunk_id : str
        UUID of the cited chunk.
    claim_text : str
        The claim / paraphrase attributed to this chunk.

    Returns
    -------
    dict with keys: chunk_id, valid, similarity_score, chunk_text, page_number
    """
    return _validate_citation_batch([chunk_id], claim_text, paper_id=paper_id)[0]


def _validate_citation_batch(
    chunk_ids: list[str],
    claim_text: str,
    paper_id: str | None = None,
) -> list[dict]:
    """Validate one claim's citations with one embedding request.

    The claim vector is shared across every cited chunk. Invalid/missing IDs
    retain their original positions and never reach the embedding provider.
    """
    chunks: list[dict | None] = [
        get_chunk_by_id(chunk_id, paper_id=paper_id) for chunk_id in chunk_ids
    ]
    usable = [(index, chunk) for index, chunk in enumerate(chunks) if chunk is not None]

    similarities: dict[int, float] = {}
    validation_method = "embedding_batch"
    if usable:
        try:
            vectors = embed_batch([claim_text, *[chunk["text"] for _, chunk in usable]])
            claim_vector = vectors[0]
            for (index, _), chunk_vector in zip(usable, vectors[1:]):
                similarities[index] = cosine_similarity(claim_vector, chunk_vector)
        except Exception as exc:
            validation_method = "lexical_fallback"
            logger.warning(
                "Batch embedding citation validation failed; using conservative "
                "lexical validation for %d citation(s): %s",
                len(usable),
                exc,
            )
            for index, chunk in usable:
                similarities[index] = lexical_similarity(claim_text, chunk["text"])

    results: list[dict] = []
    for index, (chunk_id, chunk) in enumerate(zip(chunk_ids, chunks)):
        if chunk is None:
            logger.warning("Citation validation: chunk %s not found", chunk_id)
            results.append({
                "chunk_id": chunk_id,
                "valid": False,
                "similarity_score": 0.0,
                "chunk_text": None,
                "page_number": None,
                "validation_method": "database_lookup",
                "reason": "chunk_not_found_or_not_in_paper",
            })
            continue

        similarity = similarities.get(index, 0.0)
        threshold = (
            LEXICAL_GROUNDING_SIMILARITY_THRESHOLD
            if validation_method == "lexical_fallback"
            else GROUNDING_SIMILARITY_THRESHOLD
        )
        is_valid = similarity >= threshold
        logger.info(
            "Citation validation: chunk %s, similarity=%.3f, valid=%s",
            chunk_id,
            similarity,
            is_valid,
        )
        results.append({
            "chunk_id": chunk_id,
            "valid": is_valid,
            "similarity_score": round(similarity, 4),
            "chunk_text": chunk["text"][:200],
            "page_number": chunk.get("page_number"),
            "validation_method": validation_method,
            "reason": "grounded" if is_valid else "insufficient_textual_support",
        })

    return results


def validate_attacker_citations(
    attacker_output: dict,
    paper_id: str | None = None,
) -> list[dict]:
    """
    Validate all Attacker citations.

    If critique_type is 'omission', skip — there's nothing to validate.
    Returns a list of validation results (one per cited chunk).
    """
    if attacker_output.get("critique_type") == "omission":
        logger.info("Attacker critique is an omission — skipping validation")
        return []

    chunk_ids = attacker_output.get("cited_chunk_ids", [])
    if not chunk_ids:
        if attacker_output.get("external_citations"):
            logger.info(
                "Attacker uses external-only evidence; document citation "
                "validation is not required"
            )
            return []
        # Non-omission critique MUST cite at least one chunk.
        # Return a synthetic failure so the validator_node marks this invalid
        # and the retry/skip routing in graph.py kicks in.
        logger.warning(
            "Attacker provided no citations for non-omission critique "
            "(type=%s) — marking invalid",
            attacker_output.get("critique_type", "unknown"),
        )
        return [{
            "chunk_id": None,
            "valid": False,
            "similarity_score": 0.0,
            "chunk_text": None,
            "page_number": None,
            "validation_method": "required_evidence_check",
            "reason": "missing_required_evidence",
        }]

    claim = attacker_output.get("critique_text", "")
    return _validate_citation_batch(chunk_ids, claim, paper_id=paper_id)


def validate_defender_citations(
    defender_output: dict,
    paper_id: str | None = None,
) -> list[dict]:
    """
    Validate all Defender citations.

    If the Defender concedes, skip — there's nothing to validate.
    Returns a list of validation results (one per cited chunk).
    """
    if defender_output.get("concedes", False):
        logger.info("Defender conceded — skipping validation")
        return []

    chunk_ids = defender_output.get("cited_chunk_ids", [])
    if not chunk_ids:
        logger.warning("Non-conceding Defender provided no citations — marking invalid")
        return [{
            "chunk_id": None,
            "valid": False,
            "similarity_score": 0.0,
            "chunk_text": None,
            "page_number": None,
            "validation_method": "required_evidence_check",
            "reason": "missing_required_evidence",
        }]

    claim = defender_output.get("rebuttal_text", "")
    return _validate_citation_batch(chunk_ids, claim, paper_id=paper_id)


def validate_external_citations(
    external_citations: list[dict],
    search_results: list[dict],
) -> list[dict]:
    """
    Validate external literature citations by checking existence in search_results.

    Parameters
    ----------
    external_citations : list of dicts from Attacker turn, e.g. [{"title": "...", "authors": [...]}]
    search_results : list of dicts returned by search_external_literature()

    Returns
    -------
    list of dicts with keys: title, valid, source
    """
    import re
    if not external_citations:
        return []

    def normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value).lower())

    candidates_by_title: dict[str, list[dict]] = {}
    for paper in search_results:
        normalized = normalize(paper.get("title", ""))
        if normalized:
            candidates_by_title.setdefault(normalized, []).append(paper)

    results = []
    for citation_index, cite in enumerate(external_citations):
        cite_title = cite.get("title", "")
        norm_title = normalize(cite_title)
        matching_candidates = candidates_by_title.get(norm_title, [])
        matched_candidate = None
        failure_reason = "title_not_in_search_results"

        for candidate in matching_candidates:
            source_matches = normalize(candidate.get("source", "")) == normalize(cite.get("source", ""))
            candidate_year = candidate.get("year")
            year_matches = candidate_year is None or cite.get("year") == candidate_year

            candidate_authors = {
                normalize(author) for author in candidate.get("authors", []) if normalize(author)
            }
            cited_authors = {
                normalize(author) for author in cite.get("authors", []) if normalize(author)
            }
            authors_match = not candidate_authors or bool(candidate_authors & cited_authors)

            candidate_url = str(candidate.get("url") or "").rstrip("/")
            cited_url = str(cite.get("url") or "").rstrip("/")
            url_matches = not candidate_url or candidate_url == cited_url

            if source_matches and year_matches and authors_match and url_matches:
                matched_candidate = candidate
                break
            if not source_matches:
                failure_reason = "source_mismatch"
            elif not year_matches:
                failure_reason = "year_mismatch"
            elif not authors_match:
                failure_reason = "author_mismatch"
            else:
                failure_reason = "url_mismatch"

        is_valid = matched_candidate is not None

        results.append({
            "citation_index": citation_index,
            "title": cite_title,
            "valid": is_valid,
            "source": cite.get("source", "External Literature"),
            "url": cite.get("url", ""),
            "reason": "matched_retrieved_candidate" if is_valid else failure_reason,
        })
        logger.info("External citation validation: '%s' -> valid=%s", cite_title, is_valid)

    return results
