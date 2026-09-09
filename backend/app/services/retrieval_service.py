"""
Vector-similarity retrieval via Supabase pgvector RPC.
Constructs persona-aware queries so the Attacker avoids repeats and the
Defender finds the most relevant rebuttal evidence.
"""

import logging
import re
import uuid
from app.database import get_supabase
from app.services.embedding_service import embed_text
from app.constants import EMBEDDING_SPACE_ID, TOP_K_RETRIEVAL

logger = logging.getLogger(__name__)

_REVIEW_LENSES = {
    "novelty_scope": (
        "problem definition contribution claims prior work comparison",
        "scope generalization assumptions differences from existing methods",
        "evidence for novelty limitations competing explanations",
    ),
    "theoretical_soundness": (
        "theorem assumptions definitions proof derivation",
        "boundary cases counterexamples convergence identifiability",
        "consistency between mathematical claims and stated conditions",
    ),
    "experimental_setup": (
        "experimental design controls datasets baseline comparisons",
        "evaluation metrics train test split leakage confounding",
        "robustness sensitivity alternative explanations ablations",
    ),
    "reproducibility": (
        "methods protocol implementation parameters materials availability",
        "data provenance preprocessing randomization seeds instrumentation",
        "computational resources reproducibility supplementary details",
    ),
    "limitations_impact": (
        "limitations failure modes stated scope",
        "generalization populations distribution shift edge cases",
        "broader impact risks ethics unintended consequences",
    ),
    "statistical_rigor": (
        "statistical design sample size uncertainty effect size",
        "multiple comparisons assumptions confounders independence",
        "sensitivity robustness inference limitations confidence intervals",
    ),
}


_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "in", "is", "it", "of", "on", "or", "that", "the",
    "their", "this", "to", "was", "were", "which", "with",
}


def _canonical_uuid(value: str) -> str | None:
    """Return a canonical UUID string, or ``None`` for unsafe input."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def lexical_similarity(query: str, text: str) -> float:
    """Conservative token-overlap score used when embeddings are unavailable.

    Query coverage is weighted most heavily because paper chunks are normally
    much longer than a critique.  The small Jaccard component prevents a few
    generic shared words from looking strongly grounded.
    """
    query_tokens = {
        token for token in re.findall(r"[a-z0-9]+", query.lower())
        if len(token) > 2 and token not in _STOP_WORDS
    }
    text_tokens = {
        token for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 2 and token not in _STOP_WORDS
    }
    if not query_tokens or not text_tokens:
        return 0.0
    overlap = query_tokens & text_tokens
    if len(overlap) < 2:
        return 0.0
    coverage = len(overlap) / len(query_tokens)
    jaccard = len(overlap) / len(query_tokens | text_tokens)
    return round((0.85 * coverage) + (0.15 * jaccard), 4)


def _fetch_paper_chunks(paper_id: str) -> list[dict]:
    """Fetch only chunks belonging to ``paper_id`` for local fallback."""
    supabase = get_supabase()
    result = (
        supabase.table("chunks")
        .select("id, paper_id, section, text, page_number, chunk_index")
        .eq("paper_id", paper_id)
        .execute()
    )
    return [
        chunk for chunk in (result.data or [])
        if str(chunk.get("paper_id")) == paper_id
    ]


def _lexical_retrieve(paper_id: str, query: str, top_k: int) -> list[dict]:
    """Rank paper-local chunks without any external embedding dependency."""
    chunks = _fetch_paper_chunks(paper_id)
    ranked = []
    for chunk in chunks:
        item = dict(chunk)
        item["similarity"] = lexical_similarity(query, item.get("text", ""))
        item["retrieval_method"] = "lexical_fallback"
        ranked.append(item)
    ranked.sort(
        key=lambda item: (
            item.get("similarity", 0.0),
            -int(item.get("chunk_index") or 0),
        ),
        reverse=True,
    )
    return ranked[:top_k]


def _paper_uses_current_embedding_space(paper_id: str) -> bool:
    """Legacy/unknown vectors must never be compared with current query vectors."""
    try:
        result = (
            get_supabase()
            .table("papers")
            .select("embedding_space")
            .eq("id", paper_id)
            .execute()
        )
        return bool(
            result.data
            and result.data[0].get("embedding_space") == EMBEDDING_SPACE_ID
        )
    except Exception as exc:
        # Deployments awaiting the embedding-space migration and legacy rows
        # deliberately use paper-local lexical retrieval until re-ingested.
        logger.warning(
            "Could not confirm embedding space for paper %s; using lexical retrieval: %s",
            paper_id,
            type(exc).__name__,
        )
        return False


def retrieve_chunks(
    paper_id: str,
    query: str,
    top_k: int = TOP_K_RETRIEVAL,
) -> list[dict]:
    """
    Retrieve the top-k most relevant chunks for *query* from *paper_id*.

    Uses the Supabase match_chunks RPC (pgvector cosine similarity).
    Returns a list of chunk dicts, each with an added `similarity` score.
    """
    canonical_paper_id = _canonical_uuid(paper_id)
    if canonical_paper_id is None:
        raise ValueError("paper_id must be a valid UUID")

    if not _paper_uses_current_embedding_space(canonical_paper_id):
        return _lexical_retrieve(canonical_paper_id, query, top_k)

    try:
        query_embedding = embed_text(query)
        embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

        supabase = get_supabase()
        result = supabase.rpc(
            "match_chunks",
            {
                "query_embedding": embedding_str,
                "match_paper_id": canonical_paper_id,
                "match_count": top_k,
            },
        ).execute()
        chunks = [
            chunk for chunk in (result.data or [])
            if str(chunk.get("paper_id", canonical_paper_id)) == canonical_paper_id
            and _canonical_uuid(chunk.get("id")) is not None
        ]
        if not chunks:
            logger.warning(
                "Vector retrieval returned no usable chunks for paper %s; "
                "using paper-local lexical ranking",
                canonical_paper_id,
            )
            chunks = _lexical_retrieve(canonical_paper_id, query, top_k)
    except Exception as exc:
        logger.warning(
            "Embedding retrieval failed for paper %s; using paper-local lexical ranking: %s",
            canonical_paper_id,
            type(exc).__name__,
        )
        chunks = _lexical_retrieve(canonical_paper_id, query, top_k)
    logger.debug(
        "Retrieved %d chunks for paper %s (query length %d chars)",
        len(chunks),
        canonical_paper_id,
        len(query),
    )
    return chunks


def build_attacker_query(
    round_topic_name: str,
    prior_claims: list[str],
    round_topic_slug: str | None = None,
) -> str:
    """Rotate substantive review lenses without embedding negated old claims.

    Similarity search is not instruction-following: including 'do not repeat X'
    retrieves more X. Previous claims belong only in the generator's context.
    """
    lenses = _REVIEW_LENSES.get(round_topic_slug or "", (
        "central claims methods assumptions supporting evidence",
        "boundary conditions controls alternative explanations",
        "limitations robustness sensitivity generalization",
    ))
    lens = lenses[min(len(prior_claims), len(lenses) - 1)]
    return f"{round_topic_name}. Paper evidence about {lens}."


def build_defender_query(
    critique_text: str,
    cited_chunk_ids: list[str],
) -> str:
    """
    Build the retrieval query for the Defender.

    Uses the Attacker's critique text so similarity search finds the most
    relevant rebuttal evidence, even if the Attacker cited different sections.
    """
    # UUIDs carry no semantic evidence and only pollute the query vector.
    return f"Paper evidence addressing this concern: {critique_text}"


def get_chunk_by_id(chunk_id: str, paper_id: str | None = None) -> dict | None:
    """Fetch a UUID chunk, optionally enforcing paper ownership.

    Invalid UUIDs are rejected before constructing a database filter.  Passing
    ``paper_id`` is strongly preferred for audit-time citation validation.
    """
    canonical_chunk_id = _canonical_uuid(chunk_id)
    canonical_paper_id = _canonical_uuid(paper_id) if paper_id is not None else None
    if canonical_chunk_id is None or (paper_id is not None and canonical_paper_id is None):
        logger.warning("Rejected malformed chunk or paper UUID during citation lookup")
        return None

    supabase = get_supabase()
    query = (
        supabase.table("chunks")
        .select("id, paper_id, section, text, page_number, chunk_index")
        .eq("id", canonical_chunk_id)
    )
    if canonical_paper_id is not None:
        query = query.eq("paper_id", canonical_paper_id)
    result = query.execute()
    if result.data:
        chunk = result.data[0]
        if canonical_paper_id is not None and str(chunk.get("paper_id")) != canonical_paper_id:
            logger.warning(
                "Rejected cross-paper citation: chunk %s does not belong to paper %s",
                canonical_chunk_id,
                canonical_paper_id,
            )
            return None
        return chunk
    return None
