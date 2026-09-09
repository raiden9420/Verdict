"""Deployment readiness checks for the Phase 3 database boundary.

The liveness endpoint only proves that the Python process can answer requests.
This module additionally verifies the schema that the Phase 3 API requires and
that the papers bucket is private. Successful checks are cached briefly to
avoid excess probe traffic; failures invalidate that cache and are never cached.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any

from app.database import get_service_supabase


APP_VERSION = "0.3.0"
PRODUCT_PHASE = 3

_REQUIRED_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "papers": (
        "id",
        "filename",
        "storage_path",
        "page_count",
        "uploaded_at",
        "embedding_space",
        "reproducibility_signals",
        "reference_list",
        "user_id",
        "parent_paper_id",
        "version_number",
        "detected_domain",
    ),
    "chunks": (
        "id", "paper_id", "section", "text", "embedding", "page_number", "chunk_index",
    ),
    "audits": (
        "id",
        "paper_id",
        "user_id",
        "strictness_level",
        "depth",
        "mode",
        "domain",
        "round_topic",
        "status",
        "error_message",
        "created_at",
    ),
    "rounds": ("id", "audit_id", "round_number", "topic", "status"),
    "turns": (
        "id", "round_id", "exchange_number", "agent_type", "sequence", "content", "created_at",
    ),
    "verdicts": (
        "id", "round_id", "exchange_number", "claim_summary", "verdict_type",
        "confidence", "rationale", "cited_chunk_ids",
    ),
    "debrief_cards": (
        "id", "round_id", "executive_synthesis", "solidified_strengths",
        "actionable_weaknesses", "contested_points", "created_at",
    ),
    "final_reports": ("id", "audit_id", "mode", "content", "created_at"),
    "version_diffs": (
        "id",
        "audit_id_old",
        "audit_id_new",
        "round_topic",
        "diff_summary",
        "created_at",
    ),
}


class ReadinessError(RuntimeError):
    """A required Phase 3 dependency is missing or misconfigured."""

    def __init__(self, component: str, message: str) -> None:
        super().__init__(message)
        self.component = component


_READINESS_CACHE_TTL_SECONDS = 30
_ready_until = 0.0
_ready_lock = threading.Lock()


def _value(item: Any, field: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(field)
    return getattr(item, field, None)


def verify_phase3_readiness(*, force: bool = False) -> None:
    """Raise :class:`ReadinessError` until the deployed schema is usable."""
    global _ready_until
    if not force and time.monotonic() < _ready_until:
        return

    with _ready_lock:
        if not force and time.monotonic() < _ready_until:
            return

        # A failed forced probe must invalidate even a still-fresh success.
        _ready_until = 0.0
        try:
            client = get_service_supabase()
            for table, columns in _REQUIRED_TABLE_COLUMNS.items():
                (
                    client.table(table)
                    .select(",".join(columns))
                    .limit(1)
                    .execute()
                )
        except Exception as exc:
            raise ReadinessError(
                "database_schema",
                "The Phase 3 database schema is unavailable. Apply all migrations.",
            ) from exc

        try:
            bucket = client.storage.get_bucket("papers")
            if _value(bucket, "public") is not False:
                raise ValueError("papers bucket is public or its visibility is unknown")
        except Exception as exc:
            raise ReadinessError(
                "paper_storage",
                "The private papers storage bucket is unavailable.",
            ) from exc

        _ready_until = time.monotonic() + _READINESS_CACHE_TTL_SECONDS


def reset_readiness_cache() -> None:
    """Reset the process cache for tests and explicit operational probes."""
    global _ready_until
    with _ready_lock:
        _ready_until = 0.0
