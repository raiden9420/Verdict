"""
Embedding service using the Gemini Embedding API.

Replaces the previous sentence-transformers (PyTorch) implementation to
eliminate ~300 MB of RAM overhead.  Uses the same google-genai SDK already
installed for LLM calls — no new dependencies.

The Gemini API returns 768-dim embeddings by default, but we request
384-dim via output_dimensionality to stay compatible with the existing
Supabase `vector(384)` column and pgvector RPC.

NOTE: Switching from MiniLM to Gemini embeddings changes the vector space.
Any papers uploaded with the old model should be re-uploaded so their
chunk embeddings are regenerated in the new space.
"""

import logging
import time
import numpy as np

import google.genai as genai
from google.genai import types

from app.config import GEMINI_API_KEY
from app.constants import EMBEDDING_MODEL_NAME, EMBEDDING_DIMENSION

logger = logging.getLogger(__name__)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Lazy-init the Gemini client (re-uses the same API key as the LLM)."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def embed_text(text: str) -> list[float]:
    """Embed a single string → EMBEDDING_DIMENSION-dim vector."""
    client = _get_client()

    response = client.models.embed_content(
        model=EMBEDDING_MODEL_NAME,
        contents=text,
        config=types.EmbedContentConfig(
            output_dimensionality=EMBEDDING_DIMENSION,
        ),
    )
    return response.embeddings[0].values


def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Embed a batch of strings → list of EMBEDDING_DIMENSION-dim vectors.

    The Gemini API accepts up to 250 texts per request, so we chunk
    larger batches ourselves.
    """
    if not texts:
        return []

    client = _get_client()
    BATCH_LIMIT = 100  # Stay well under the 250-text API limit

    all_embeddings: list[list[float]] = []

    for start in range(0, len(texts), BATCH_LIMIT):
        batch = texts[start : start + BATCH_LIMIT]

        # Retry with backoff on rate-limit errors
        for attempt in range(5):
            try:
                response = client.models.embed_content(
                    model=EMBEDDING_MODEL_NAME,
                    contents=batch,
                    config=types.EmbedContentConfig(
                        output_dimensionality=EMBEDDING_DIMENSION,
                    ),
                )
                all_embeddings.extend(
                    emb.values for emb in response.embeddings
                )
                break
            except Exception as exc:
                err = str(exc).lower()
                if any(tok in err for tok in ("429", "resource exhausted", "rate", "quota")):
                    delay = min(60, 2 * (2 ** attempt))
                    logger.warning(
                        "Embedding rate-limited (attempt %d/5), retrying in %ds …",
                        attempt + 1, delay,
                    )
                    time.sleep(delay)
                else:
                    raise
        else:
            raise RuntimeError("Embedding batch failed after 5 retries")

    return all_embeddings


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Cosine similarity between two vectors (both assumed non-zero)."""
    a = np.asarray(vec_a, dtype=np.float32)
    b = np.asarray(vec_b, dtype=np.float32)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)
