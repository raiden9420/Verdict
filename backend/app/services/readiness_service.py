"""Deployment readiness checks for the Phase 3 database boundary.

The liveness endpoint only proves that the Python process can answer requests.
This module additionally verifies the schema that the Phase 3 API requires and
that the papers bucket is private. A successful result is cached for the life
of the process; failures are never cached, so applying a migration allows a
waiting deployment to become ready without a restart.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from app.database import get_service_supabase


APP_VERSION = "0.3.0"
PRODUCT_PHASE = 3

_REQUIRED_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "papers": (
        "id",
        "user_id",
        "parent_paper_id",
        "version_number",
        "detected_domain",
    ),
    "audits": (
        "id",
        "paper_id",
        "user_id",
        "strictness_level",
        "depth",
        "mode",
        "domain",
    ),
    "final_reports": ("id", "audit_id", "mode", "content"),
    "version_diffs": (
        "id",
        "audit_id_old",
        "audit_id_new",
        "round_topic",
        "diff_summary",
    ),
}


class ReadinessError(RuntimeError):
    """A required Phase 3 dependency is missing or misconfigured."""

    def __init__(self, component: str, message: str) -> None:
        super().__init__(message)
        self.component = component


_ready = False
_ready_lock = threading.Lock()


def _value(item: Any, field: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(field)
    return getattr(item, field, None)


def verify_phase3_readiness(*, force: bool = False) -> None:
    """Raise :class:`ReadinessError` until the deployed schema is usable."""
    global _ready
    if _ready and not force:
        return

    with _ready_lock:
        if _ready and not force:
            return

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

        _ready = True


def reset_readiness_cache() -> None:
    """Reset the process cache for tests and explicit operational probes."""
    global _ready
    with _ready_lock:
        _ready = False
