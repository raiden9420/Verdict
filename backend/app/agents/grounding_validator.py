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
    EXTERNAL_CITATION_RELEVANCE_THRESHOLD,
    GROUNDING_SIMILARITY_THRESHOLD,
    LEXICAL_GROUNDING_SIMILARITY_THRESHOLD,
)
from app.services.embedding_service import embed_batch, cosine_similarity
from app.services.literature_search_service import (
    LITERATURE_SOURCE_COUNT,
    search_external_literature,
    titles_fuzzy_match,
)
from app.services.retrieval_service import get_chunk_by_id, lexical_similarity

logger = logging.getLogger(__name__)
MAX_SOURCE_PASSAGE_CHARS = 6000


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
                type(exc).__name__,
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
            # The adjudicator needs the actual passage, not a leading preview:
            # semantic overlap alone cannot establish that a rebuttal follows.
            "chunk_text": chunk["text"][:MAX_SOURCE_PASSAGE_CHARS],
            "source_truncated": len(chunk["text"]) > MAX_SOURCE_PASSAGE_CHARS,
            "section": chunk.get("section"),
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
    chunk_ids = attacker_output.get("cited_chunk_ids", [])
    if not chunk_ids and attacker_output.get("critique_type") in {
        "omission",
        "citation_integrity",
        "missing_baseline",
    }:
        logger.info(
            "Attacker critique type %s does not require an in-document citation",
            attacker_output.get("critique_type"),
        )
        return []

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


def _citation_result(
    *,
    citation_index: int,
    citation_type: str,
    title: str,
    reference_id: str | None = None,
    exists: bool = False,
    relevant: bool = False,
    valid: bool = False,
    reason: str,
    validation_complete: bool = True,
    similarity_score: float | None = None,
    matched_work: dict | None = None,
) -> dict:
    """Build the stable result shape shared by both citation critique types."""
    matched = matched_work or {}
    return {
        "citation_index": citation_index,
        "citation_type": citation_type,
        "reference_id": reference_id,
        "title": title,
        "matched_title": matched.get("title"),
        "exists": exists,
        "relevant": relevant,
        "valid": valid,
        "similarity_score": (
            round(similarity_score, 4)
            if isinstance(similarity_score, (int, float))
            else None
        ),
        "source": matched.get("source", "External Literature"),
        "url": matched.get("url", ""),
        "validation_method": "title_search_and_embedding_similarity",
        "validation_complete": validation_complete,
        "reason": reason,
    }


def validate_citation_critique(
    attacker_output: dict,
    reference_list: list[dict],
    paper_context: str,
) -> list[dict]:
    """Run one lazy existence-and-relevance validator for both citation paths.

    ``citation_integrity`` metadata is resolved only from ``reference_list``;
    model-authored metadata is never trusted. ``missing_baseline`` is expected to
    have been replaced with canonical, prompt-supplied candidates by the graph's
    provenance boundary before it reaches this function.

    A false ``valid`` result means the cited work is suspect. Graph routing then
    treats that result differently by critique type: it is the finding for an
    integrity critique, but invalid evidence for a missing-baseline critique.
    """
    critique_type = str(attacker_output.get("critique_type") or "")
    canonical_citations: list[dict] = []

    if critique_type == "citation_integrity":
        reference_id = attacker_output.get("cited_reference_id")
        reference = next(
            (
                entry
                for entry in reference_list
                if isinstance(entry, dict) and entry.get("id") == reference_id
            ),
            None,
        )
        if reference is None:
            return [
                _citation_result(
                    citation_index=0,
                    citation_type=critique_type,
                    reference_id=(str(reference_id) if reference_id is not None else None),
                    title="",
                    reason="reference_not_in_paper",
                )
            ]
        canonical_citations = [
            {
                "title": reference.get("title", ""),
                "authors": reference.get("authors", []),
                "year": reference.get("year"),
                "reference_id": str(reference.get("id")),
            }
        ]
    elif critique_type == "missing_baseline":
        canonical_citations = [
            dict(citation)
            for citation in attacker_output.get("external_citations", [])
            if isinstance(citation, dict)
        ]
        if not canonical_citations:
            return [
                _citation_result(
                    citation_index=0,
                    citation_type=critique_type,
                    title="",
                    reason="missing_baseline_citation_missing",
                )
            ]
    else:
        return []

    matched: list[tuple[int, dict, dict]] = []
    results_by_index: dict[int, dict] = {}
    for citation_index, citation in enumerate(canonical_citations):
        title = str(citation.get("title") or "").strip()
        reference_id = citation.get("reference_id")
        if not title:
            results_by_index[citation_index] = _citation_result(
                citation_index=citation_index,
                citation_type=critique_type,
                reference_id=reference_id,
                title="",
                reason="citation_title_missing",
                validation_complete=False,
            )
            continue

        try:
            search_response = search_external_literature(title, with_status=True)
        except Exception as exc:
            logger.warning(
                "External citation existence validation unavailable for '%s': %s",
                title,
                type(exc).__name__,
            )
            results_by_index[citation_index] = _citation_result(
                citation_index=citation_index,
                citation_type=critique_type,
                reference_id=reference_id,
                title=title,
                reason="existence_check_unavailable",
                validation_complete=False,
            )
            continue
        if (
            isinstance(search_response, tuple)
            and len(search_response) == 2
        ):
            search_results, successful_sources = search_response
        else:
            # Test doubles and older compatible implementations may still
            # return only the result list. Without explicit partial status, a
            # completed aggregate call retains the legacy conclusive semantics.
            search_results = search_response
            successful_sources = LITERATURE_SOURCE_COUNT
        real_match = next(
            (
                candidate
                for candidate in search_results
                if isinstance(candidate, dict)
                and titles_fuzzy_match(title, candidate.get("title"))
            ),
            None,
        )
        if real_match is None:
            # A positive identity match is conclusive even when only one source
            # answered. A negative is conclusive only after every configured
            # source completed; otherwise the missing provider may contain the
            # work (for example, a journal article absent from arXiv).
            if successful_sources < LITERATURE_SOURCE_COUNT:
                results_by_index[citation_index] = _citation_result(
                    citation_index=citation_index,
                    citation_type=critique_type,
                    reference_id=reference_id,
                    title=title,
                    reason="existence_check_unavailable",
                    validation_complete=False,
                )
                continue
            results_by_index[citation_index] = _citation_result(
                citation_index=citation_index,
                citation_type=critique_type,
                reference_id=reference_id,
                title=title,
                reason="citation_not_found",
            )
            continue
        matched.append((citation_index, citation, real_match))

    if matched:
        context = str(paper_context or "").strip()
        if not context:
            for citation_index, citation, real_match in matched:
                results_by_index[citation_index] = _citation_result(
                    citation_index=citation_index,
                    citation_type=critique_type,
                    reference_id=citation.get("reference_id"),
                    title=str(citation.get("title") or ""),
                    exists=True,
                    reason="relevance_check_unavailable",
                    validation_complete=False,
                    matched_work=real_match,
                )
        else:
            work_texts = [
                " ".join(
                    part
                    for part in (
                        str(real_match.get("title") or "").strip(),
                        str(real_match.get("abstract") or "").strip(),
                    )
                    if part
                )[:4000]
                for _, _, real_match in matched
            ]
            try:
                vectors = embed_batch([context[:8000], *work_texts])
                context_vector = vectors[0]
                similarities = [
                    cosine_similarity(context_vector, work_vector)
                    for work_vector in vectors[1:]
                ]
            except Exception as exc:
                logger.warning(
                    "External citation relevance validation unavailable: %s",
                    type(exc).__name__,
                )
                similarities = []

            for matched_index, (citation_index, citation, real_match) in enumerate(matched):
                if matched_index >= len(similarities):
                    results_by_index[citation_index] = _citation_result(
                        citation_index=citation_index,
                        citation_type=critique_type,
                        reference_id=citation.get("reference_id"),
                        title=str(citation.get("title") or ""),
                        exists=True,
                        reason="relevance_check_unavailable",
                        validation_complete=False,
                        matched_work=real_match,
                    )
                    continue

                similarity = similarities[matched_index]
                relevant = similarity >= EXTERNAL_CITATION_RELEVANCE_THRESHOLD
                results_by_index[citation_index] = _citation_result(
                    citation_index=citation_index,
                    citation_type=critique_type,
                    reference_id=citation.get("reference_id"),
                    title=str(citation.get("title") or ""),
                    exists=True,
                    relevant=relevant,
                    valid=relevant,
                    reason="verified" if relevant else "topically_unrelated",
                    similarity_score=similarity,
                    matched_work=real_match,
                )

    ordered = [results_by_index[index] for index in range(len(canonical_citations))]
    for result in ordered:
        logger.info(
            "Citation critique validation: type=%s title='%s' reason=%s",
            critique_type,
            result.get("title", ""),
            result.get("reason"),
        )
    return ordered
