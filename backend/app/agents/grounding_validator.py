"""
Grounding Validator — deterministic, NO LLM call.

Validates every citation from both Attacker and Defender:
1. Confirm the cited chunk exists in the database.
2. Compute embedding similarity between the claim and the chunk text.
3. Flag invalid if similarity < GROUNDING_SIMILARITY_THRESHOLD.

Skips validation for Attacker omission critiques (no citation to check)
and Defender concessions (no citation to check).
"""

import logging
from app.constants import GROUNDING_SIMILARITY_THRESHOLD
from app.services.embedding_service import embed_text, cosine_similarity
from app.services.retrieval_service import get_chunk_by_id

logger = logging.getLogger(__name__)


def validate_citation(
    chunk_id: str,
    claim_text: str,
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
    chunk = get_chunk_by_id(chunk_id)

    if chunk is None:
        logger.warning("Citation validation: chunk %s not found", chunk_id)
        return {
            "chunk_id": chunk_id,
            "valid": False,
            "similarity_score": 0.0,
            "chunk_text": None,
            "page_number": None,
        }

    # Embed both the claim and the actual chunk text, then compare.
    claim_vec = embed_text(claim_text)
    chunk_vec = embed_text(chunk["text"])
    similarity = cosine_similarity(claim_vec, chunk_vec)

    is_valid = similarity >= GROUNDING_SIMILARITY_THRESHOLD

    logger.info(
        "Citation validation: chunk %s, similarity=%.3f, valid=%s",
        chunk_id,
        similarity,
        is_valid,
    )

    return {
        "chunk_id": chunk_id,
        "valid": is_valid,
        "similarity_score": round(similarity, 4),
        "chunk_text": chunk["text"][:200],  # truncated for context
        "page_number": chunk.get("page_number"),
    }


def validate_attacker_citations(
    attacker_output: dict,
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
        }]

    claim = attacker_output.get("critique_text", "")
    results = []
    for cid in chunk_ids:
        results.append(validate_citation(cid, claim))
    return results


def validate_defender_citations(
    defender_output: dict,
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
        logger.info("Defender provided no citations — skipping")
        return []

    claim = defender_output.get("rebuttal_text", "")
    results = []
    for cid in chunk_ids:
        results.append(validate_citation(cid, claim))
    return results
