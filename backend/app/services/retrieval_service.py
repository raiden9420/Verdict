"""
Vector-similarity retrieval via Supabase pgvector RPC.
Constructs persona-aware queries so the Attacker avoids repeats and the
Defender finds the most relevant rebuttal evidence.
"""

import logging
from app.database import get_supabase
from app.services.embedding_service import embed_text
from app.constants import TOP_K_RETRIEVAL

logger = logging.getLogger(__name__)


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
    query_embedding = embed_text(query)
    embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    supabase = get_supabase()
    result = supabase.rpc(
        "match_chunks",
        {
            "query_embedding": embedding_str,
            "match_paper_id": paper_id,
            "match_count": top_k,
        },
    ).execute()

    chunks = result.data or []
    logger.debug(
        "Retrieved %d chunks for paper %s (query length %d chars)",
        len(chunks),
        paper_id,
        len(query),
    )
    return chunks


def build_attacker_query(
    round_topic_name: str,
    prior_claims: list[str],
) -> str:
    """
    Build the retrieval query for the Attacker.

    Includes the round topic + summaries of already-raised claims so the
    vector search steers toward regions NOT yet covered.
    """
    parts = [f"Research paper weaknesses related to: {round_topic_name}"]
    if prior_claims:
        parts.append(
            "The following critiques have ALREADY been raised — "
            "find a DIFFERENT area to critique:\n"
            + "\n".join(f"- {c}" for c in prior_claims)
        )
    return "\n\n".join(parts)


def build_defender_query(
    critique_text: str,
    cited_chunk_ids: list[str],
) -> str:
    """
    Build the retrieval query for the Defender.

    Uses the Attacker's critique text so similarity search finds the most
    relevant rebuttal evidence, even if the Attacker cited different sections.
    """
    parts = [f"Evidence that addresses this critique: {critique_text}"]
    if cited_chunk_ids:
        parts.append(
            "The critique cited these chunks: "
            + ", ".join(cited_chunk_ids)
        )
    return "\n\n".join(parts)


def get_chunk_by_id(chunk_id: str) -> dict | None:
    """Fetch a single chunk by its UUID. Returns None if not found."""
    supabase = get_supabase()
    result = (
        supabase.table("chunks")
        .select("id, paper_id, section, text, page_number, chunk_index")
        .eq("id", chunk_id)
        .execute()
    )
    if result.data:
        return result.data[0]
    return None
