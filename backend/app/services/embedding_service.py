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
import math
import time
import numpy as np

import google.genai as genai
from google.genai import types

from app.config import GEMINI_API_KEY
from app.constants import EMBEDDING_MODEL_NAME, EMBEDDING_DIMENSION

logger = logging.getLogger(__name__)

_client: genai.Client | None = None
_REQUEST_TIMEOUT_MS = 30_000
_MAX_ATTEMPTS = 4
_BATCH_LIMIT = 100


class EmbeddingServiceError(RuntimeError):
    """Raised after a bounded embedding request cannot be completed safely."""


def _get_client() -> genai.Client:
    """Lazy-init the Gemini client (re-uses the same API key as the LLM)."""
    global _client
    if not GEMINI_API_KEY:
        raise EmbeddingServiceError("GEMINI_API_KEY is not configured")
    if _client is None:
        _client = genai.Client(
            api_key=GEMINI_API_KEY,
            http_options=types.HttpOptions(timeout=_REQUEST_TIMEOUT_MS),
        )
    return _client


def _is_retryable(exc: Exception) -> bool:
    error = str(exc).lower()
    return any(
        marker in error
        for marker in (
            "408",
            "429",
            "500",
            "502",
            "503",
            "504",
            "deadline",
            "resource exhausted",
            "rate limit",
            "quota",
            "temporarily unavailable",
            "timeout",
            "timed out",
            "connection reset",
            "connection aborted",
        )
    )


def _validate_embeddings(response: object, expected_count: int) -> list[list[float]]:
    raw_embeddings = getattr(response, "embeddings", None)
    if not isinstance(raw_embeddings, (list, tuple)) or len(raw_embeddings) != expected_count:
        actual_count = len(raw_embeddings) if isinstance(raw_embeddings, (list, tuple)) else 0
        raise EmbeddingServiceError(
            f"Embedding provider returned {actual_count} vectors; "
            f"expected {expected_count}."
        )

    validated: list[list[float]] = []
    for raw in raw_embeddings:
        values = getattr(raw, "values", None)
        if values is None:
            raise EmbeddingServiceError("Embedding provider returned a vector without values.")
        vector = [float(value) for value in values]
        if len(vector) != EMBEDDING_DIMENSION:
            raise EmbeddingServiceError(
                f"Embedding provider returned dimension {len(vector)}; "
                f"expected {EMBEDDING_DIMENSION}."
            )
        if not all(math.isfinite(value) for value in vector):
            raise EmbeddingServiceError("Embedding provider returned non-finite values.")
        validated.append(vector)
    return validated


def _embed_with_retry(contents: str | list[str], expected_count: int) -> list[list[float]]:
    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = _get_client().models.embed_content(
                model=EMBEDDING_MODEL_NAME,
                contents=contents,
                config=types.EmbedContentConfig(
                    output_dimensionality=EMBEDDING_DIMENSION,
                ),
            )
            return _validate_embeddings(response, expected_count)
        except Exception as exc:
            last_error = exc
            retryable = _is_retryable(exc)
            if not retryable or attempt == _MAX_ATTEMPTS - 1:
                break
            delay = min(8, 2**attempt)
            logger.warning(
                "Transient embedding failure (attempt %d/%d); retrying in %ds (%s)",
                attempt + 1,
                _MAX_ATTEMPTS,
                delay,
                type(exc).__name__,
            )
            time.sleep(delay)

    if isinstance(last_error, EmbeddingServiceError):
        raise last_error
    raise EmbeddingServiceError(
        f"Embedding request failed after bounded retries: {type(last_error).__name__}"
    ) from last_error


def embed_text(text: str) -> list[float]:
    """Embed a single string → EMBEDDING_DIMENSION-dim vector."""
    if not text or not text.strip():
        raise ValueError("Cannot embed empty text")
    return _embed_with_retry(text, 1)[0]


def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Embed a batch of strings → list of EMBEDDING_DIMENSION-dim vectors.

    The Gemini API accepts up to 250 texts per request, so we chunk
    larger batches ourselves.
    """
    if not texts:
        return []

    all_embeddings: list[list[float]] = []

    for start in range(0, len(texts), _BATCH_LIMIT):
        batch = texts[start : start + _BATCH_LIMIT]
        if any(not isinstance(text, str) or not text.strip() for text in batch):
            raise ValueError("Cannot embed an empty or non-string batch item")
        all_embeddings.extend(_embed_with_retry(batch, len(batch)))

    return all_embeddings


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Cosine similarity between two vectors (both assumed non-zero)."""
    a = np.asarray(vec_a, dtype=np.float32)
    b = np.asarray(vec_b, dtype=np.float32)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)
