"""
Local embedding service using sentence-transformers.
CPU-only — no external API calls, no quota consumed.
The model is loaded lazily on first use and cached as a singleton.
"""

import logging
import numpy as np
from sentence_transformers import SentenceTransformer

from app.constants import EMBEDDING_MODEL_NAME

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    """Lazy-load the embedding model (downloads ~80 MB on first run)."""
    global _model
    if _model is None:
        logger.info("Loading embedding model '%s' …", EMBEDDING_MODEL_NAME)
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        logger.info("Embedding model loaded.")
    return _model


def embed_text(text: str) -> list[float]:
    """Embed a single string → 384-dim unit vector."""
    model = _get_model()
    vec = model.encode(text, normalize_embeddings=True)
    return vec.tolist()


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of strings → list of 384-dim unit vectors."""
    if not texts:
        return []
    model = _get_model()
    vecs = model.encode(texts, normalize_embeddings=True, batch_size=32)
    return vecs.tolist()


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Cosine similarity between two vectors (both assumed non-zero)."""
    a = np.asarray(vec_a, dtype=np.float32)
    b = np.asarray(vec_b, dtype=np.float32)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)
