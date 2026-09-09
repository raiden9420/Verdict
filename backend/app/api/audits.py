"""Authenticated audit orchestration, streaming, reports, and revision diffs."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sse_starlette.sse import EventSourceResponse

from app.agents.graph import run_audit
from app.api.dependencies import CurrentUser, canonical_uuid, get_current_user
from app.constants import ROUND_TOPICS
from app.database import (
    get_service_supabase,
    get_supabase,
    get_user_supabase,
    use_supabase,
)
from app.models.schemas import (
    AuditCreateRequest,
    AuditCreateResponse,
    AuditSummaryResponse,
    DebriefCardResponse,
    ErrorResponse,
    FinalReportResponse,
    TurnResponse,
    TurnsListResponse,
    VerdictResponse,
    VersionDiffResponse,
)
from app.services.report_service import (
    AuditNotFoundError,
    FinalReportNotReadyError,
    ReportServiceError,
    VersionComparisonError,
    find_previous_audit_id,
    generate_final_report,
    generate_version_diffs_for_audit,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["audits"])

_TERMINAL_EVENT_TYPES = frozenset({"complete", "audit_error"})
_EVENT_HISTORY_LIMIT = 2048  # enough for an Exhaustive audit plus retries
_FINISHED_HUB_TTL_SECONDS = 15 * 60
_MAX_PENDING_AUDITS = 4
_ORPHANED_AUDIT_MESSAGE = (
    "The audit worker restarted before this audit completed. Please launch it again."
)
_AUDIT_FAILURE_MESSAGE = (
    "The audit could not be completed. Completed topic findings are preserved. "
    "Please launch a new audit to try again."
)
_AUDIT_HISTORY_BATCH_SIZE = 100

# One worker preserves the Phase 2 free-tier quota boundary. The semaphore caps
# the running job plus queued audits without creating unbounded blocked threads.
_audit_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="verdict-audit")
_audit_capacity = threading.BoundedSemaphore(_MAX_PENDING_AUDITS)
# Manual diff retries also call an LLM. Keep them mutually exclusive with the
# single audit worker so the retry path cannot accidentally double free-tier
# provider pressure while an Exhaustive audit is running.
_llm_work_lock = threading.Lock()
_event_loop: asyncio.AbstractEventLoop | None = None
# Retain shielded admission tasks until they finish even if their HTTP request
# goes away. A cancelled client must not strand a durable job or its capacity.
_admission_tasks: set[asyncio.Task[AuditCreateResponse]] = set()


def _public_audit_error(message: Any) -> str:
    """Expose only server-authored errors, including when reading legacy rows."""
    if message in (_ORPHANED_AUDIT_MESSAGE, _AUDIT_FAILURE_MESSAGE):
        return str(message)
    return _AUDIT_FAILURE_MESSAGE


@dataclass(frozen=True)
class _EventRecord:
    id: int
    type: str
    data: dict[str, Any]


class _AuditEventHub:
    """Replayable fan-out stream; every connected client gets every event."""

    def __init__(self) -> None:
        self.history: deque[_EventRecord] = deque(maxlen=_EVENT_HISTORY_LIMIT)
        self.subscribers: set[asyncio.Queue[_EventRecord]] = set()
        self.next_id = 1
        self.terminal_type: str | None = None
        self.cleanup_scheduled = False

    def publish(self, event_type: str, data: dict[str, Any]) -> _EventRecord | None:
        if self.terminal_type is not None:
            return None
        if event_type in _TERMINAL_EVENT_TYPES:
            self.terminal_type = event_type
        record = _EventRecord(self.next_id, event_type, data)
        self.next_id += 1
        self.history.append(record)
        for queue in tuple(self.subscribers):
            queue.put_nowait(record)
        return record

    def subscribe(
        self,
        last_event_id: int,
    ) -> tuple[asyncio.Queue[_EventRecord], list[_EventRecord]]:
        queue: asyncio.Queue[_EventRecord] = asyncio.Queue()
        replay = [record for record in self.history if record.id > last_event_id]
        if self.terminal_type is None:
            self.subscribers.add(queue)
        return queue, replay

    def unsubscribe(self, queue: asyncio.Queue[_EventRecord]) -> None:
        self.subscribers.discard(queue)


_event_hubs: dict[str, _AuditEventHub] = {}


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _event_loop
    _event_loop = loop


def shutdown_audit_executor() -> None:
    _audit_executor.shutdown(wait=False, cancel_futures=True)


def recover_orphaned_audits() -> int:
    """Fail durable in-progress rows left behind by this single worker."""
    supabase = get_service_supabase()  # explicit cross-user startup reconciliation
    result = supabase.table("audits").select("id").eq("status", "in_progress").execute()
    audit_ids = [str(row["id"]) for row in (result.data or []) if row.get("id")]
    for audit_id in audit_ids:
        supabase.table("audits").update(
            {"status": "error", "error_message": _ORPHANED_AUDIT_MESSAGE}
        ).eq("id", audit_id).execute()
        supabase.table("rounds").update({"status": "error"}).eq(
            "audit_id", audit_id
        ).neq("status", "completed").execute()
    if audit_ids:
        logger.warning("Marked %d interrupted audit(s) as failed", len(audit_ids))
    return len(audit_ids)


def _publish_event(audit_id: str, event_type: str, data: Any) -> None:
    hub = _event_hubs.get(audit_id)
    if hub is None:
        return
    safe_data = data if isinstance(data, dict) else {"value": data}
    record = hub.publish(event_type, safe_data)
    if record and event_type in _TERMINAL_EVENT_TYPES and not hub.cleanup_scheduled:
        hub.cleanup_scheduled = True
        loop = asyncio.get_running_loop()

        def expire() -> None:
            if _event_hubs.get(audit_id) is hub:
                _event_hubs.pop(audit_id, None)

        loop.call_later(_FINISHED_HUB_TTL_SECONDS, expire)


def _mark_audit_error(
    audit_id: str,
    round_ids: str | list[str],
    message: str,
) -> None:
    """Persist a terminal failure without exposing provider internals to clients."""
    supabase = get_supabase()
    safe_message = _public_audit_error(message)
    supabase.table("audits").update(
        {"status": "error", "error_message": safe_message}
    ).eq("id", audit_id).execute()
    ids = [round_ids] if isinstance(round_ids, str) else list(round_ids)
    if ids:
        supabase.table("rounds").update({"status": "error"}).in_(
            "id", ids
        ).neq("status", "completed").execute()


def _round_event_callback(
    callback: Any,
    round_row: dict[str, Any],
) -> Any:
    """Add round identity to otherwise unchanged inner-graph event payloads."""
    def enrich(event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        data = event.get("data")
        safe_data = dict(data) if isinstance(data, dict) else {"value": data}
        safe_data.update(
            {
                "round_id": str(round_row["id"]),
                "round_number": int(round_row["round_number"]),
                "round_topic": str(round_row["topic"]),
            }
        )
        callback({"type": event.get("type", "message"), "data": safe_data})

    return enrich


def _run_audit_job(
    *,
    audit_id: str,
    paper_id: str,
    loop: asyncio.AbstractEventLoop,
    round_rows: list[dict[str, Any]] | None = None,
    strictness_level: str = "standard",
    domain: str = "other",
    mode: str = "author",
    compare_to_audit_id: str | None = None,
    user_supabase: Any | None = None,
    # Legacy test/caller seam; the public API always supplies round_rows.
    round_id: str | None = None,
    round_topic: str | None = None,
) -> None:
    """Run selected topics sequentially, then synthesize/report/diff once."""
    legacy_single_round_call = round_rows is None and user_supabase is None
    rows = list(round_rows or [])
    if not rows and round_id and round_topic:
        rows = [
            {
                "id": round_id,
                "round_number": 1,
                "topic": round_topic,
            }
        ]
    round_ids = [str(row["id"]) for row in rows]
    required_artifacts_complete = False

    def publish_graph_event(event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        event_type = str(event.get("type") or "message")
        data = event.get("data", {})
        if event_type == "error":
            return
        if event_type == "complete":
            return
        loop.call_soon_threadsafe(_publish_event, audit_id, event_type, data)

    context = use_supabase(user_supabase) if user_supabase is not None else nullcontext()
    _llm_work_lock.acquire()
    try:
        with context:
            for row in rows:
                topic = str(row["topic"])
                if not legacy_single_round_call:
                    get_supabase().table("rounds").update(
                        {"status": "in_progress"}
                    ).eq("id", str(row["id"])).execute()
                if not legacy_single_round_call:
                    loop.call_soon_threadsafe(
                        _publish_event,
                        audit_id,
                        "round_start",
                        {
                            "round_id": str(row["id"]),
                            "round_number": int(row["round_number"]),
                            "round_topic": topic,
                            "round_topic_name": ROUND_TOPICS[topic],
                        },
                    )
                callback = _round_event_callback(publish_graph_event, row)
                # Keep the old four-argument call for the default path so the
                # audited Phase 1/2 seam remains compatible with test doubles.
                if strictness_level == "standard" and domain == "other":
                    result = run_audit(paper_id, str(row["id"]), topic, callback)
                else:
                    result = run_audit(
                        paper_id,
                        str(row["id"]),
                        topic,
                        callback,
                        strictness_level=strictness_level,
                        domain=domain,
                    )
                if not isinstance(result, dict) or result.get("status") != "completed":
                    raise RuntimeError("Audit round did not complete")

            # Internal Phase 1/2 callers invoked this worker with only one round
            # and no database context. Preserve that seam for regression tests;
            # every HTTP-created Phase 3 job supplies both round_rows and an
            # authenticated RLS client and therefore always synthesizes a report.
            if legacy_single_round_call:
                loop.call_soon_threadsafe(
                    _publish_event,
                    audit_id,
                    "complete",
                    {"status": "completed"},
                )
                return

            report = generate_final_report(audit_id, mode=mode)  # type: ignore[arg-type]
            loop.call_soon_threadsafe(
                _publish_event,
                audit_id,
                "final_report",
                report,
            )

            # The required product path ends at a durable paper-level report.
            # Commit that success before attempting the additive revision diff,
            # so a provider outage, database hiccup, or worker restart during
            # comparison can never retroactively invalidate a complete audit.
            get_supabase().table("audits").update(
                {"status": "completed", "error_message": None}
            ).eq("id", audit_id).execute()
            required_artifacts_complete = True

            diffs: list[dict[str, Any]] = []
            try:
                previous_id = compare_to_audit_id or find_previous_audit_id(audit_id)
                if previous_id:
                    diffs = generate_version_diffs_for_audit(audit_id, previous_id)
                    loop.call_soon_threadsafe(
                        _publish_event,
                        audit_id,
                        "version_diffs",
                        {"items": diffs},
                    )
            except Exception as diff_exc:
                # Version comparison is an additive artifact. This deliberately
                # catches provider and persistence exceptions too, not only
                # domain validation errors from report_service.
                logger.warning(
                    "Version diff for audit %s could not be produced: %s",
                    audit_id,
                    type(diff_exc).__name__,
                )
                loop.call_soon_threadsafe(
                    _publish_event,
                    audit_id,
                    "version_diff_error",
                    {
                        "message": (
                            "The audit completed, but its optional version "
                            "comparison could not be generated."
                        )
                    },
                )

            loop.call_soon_threadsafe(
                _publish_event,
                audit_id,
                "complete",
                {
                    "status": "completed",
                    "final_report_id": report.get("id"),
                    "version_diff_count": len(diffs),
                },
            )
    except Exception as exc:
        message = _AUDIT_FAILURE_MESSAGE
        logger.error("Audit %s failed (%s)", audit_id, type(exc).__name__)
        if required_artifacts_complete:
            # The rounds, report, and completed status are already durable.
            # A shutting-down event loop (or another post-commit notification
            # failure) must not rewrite that successful audit as an error.
            return
        try:
            with (
                use_supabase(user_supabase)
                if user_supabase is not None
                else nullcontext()
            ):
                _mark_audit_error(audit_id, round_ids, message)
        except Exception as persist_exc:
            logger.error("Failed to persist audit %s error (%s)", audit_id, type(persist_exc).__name__)
        loop.call_soon_threadsafe(
            _publish_event,
            audit_id,
            "audit_error",
            {"status": "error", "message": message},
        )
    finally:
        _llm_work_lock.release()
        _audit_capacity.release()


def _load_owned_paper(supabase: Any, paper_id: str, user_id: str) -> dict[str, Any] | None:
    result = (
        supabase.table("papers")
        .select(
            "id, detected_domain, reproducibility_signals, "
            "parent_paper_id, version_number"
        )
        .eq("id", paper_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return dict(result.data[0]) if result.data else None


def _paper_has_indexed_chunks(supabase: Any, paper_id: str) -> bool:
    result = (
        supabase.table("chunks")
        .select("id")
        .eq("paper_id", paper_id)
        .limit(1)
        .execute()
    )
    return bool(result.data)


def _create_audit_rows(
    supabase: Any,
    *,
    audit_id: str,
    paper_id: str,
    user_id: str,
    topics: list[str],
    strictness_level: str,
    depth: str,
    mode: str,
    domain: str,
) -> list[dict[str, Any]]:
    """Atomically-as-practical create one audit and all selected round rows."""
    rows = [
        {
            "id": str(uuid.uuid4()),
            "audit_id": audit_id,
            "round_number": index,
            "topic": topic,
            "status": "pending",
        }
        for index, topic in enumerate(topics, start=1)
    ]
    inserted_audit = False
    try:
        # A successful remote insert can lose its response. This request owns
        # a fresh UUID, so compensation is safe even if no row was inserted.
        inserted_audit = True
        supabase.table("audits").insert(
            {
                "id": audit_id,
                "paper_id": paper_id,
                "user_id": user_id,
                # Retained for Phase 1/2 response compatibility; rounds are
                # authoritative for a multi-topic audit.
                "round_topic": topics[0],
                "strictness_level": strictness_level,
                "depth": depth,
                "mode": mode,
                "domain": domain,
                "status": "in_progress",
                "error_message": None,
            }
        ).execute()
        supabase.table("rounds").insert(rows).execute()
        return rows
    except Exception:
        if inserted_audit:
            try:
                supabase.table("audits").delete().eq("id", audit_id).execute()
            except Exception as cleanup_exc:
                logger.error("Failed to roll back audit row %s (%s)", audit_id, type(cleanup_exc).__name__)
        raise


def _delete_audit_rows(supabase: Any, audit_id: str) -> None:
    try:
        supabase.table("audits").delete().eq("id", audit_id).execute()
    except Exception as exc:
        logger.error("Failed to clean up unscheduled audit %s (%s)", audit_id, type(exc).__name__)


@router.post(
    "/audits",
    response_model=AuditCreateResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def create_audit(
    body: AuditCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Create one configured multi-topic audit and queue its outer runner."""
    supabase = get_user_supabase(current_user.access_token)
    service_supabase = get_service_supabase()
    try:
        paper = await asyncio.to_thread(
            _load_owned_paper,
            supabase,
            body.paper_id,
            current_user.id,
        )
    except Exception as exc:
        logger.error("Failed to authorize paper %s (%s)", body.paper_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Paper service is temporarily unavailable.") from exc
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")

    try:
        indexed = await asyncio.to_thread(_paper_has_indexed_chunks, supabase, body.paper_id)
    except Exception as exc:
        logger.error("Failed to verify paper index %s (%s)", body.paper_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Paper index service is temporarily unavailable.") from exc
    if not indexed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "paper_not_indexed",
                "message": "This document was not fully indexed and cannot be audited. Please upload it again.",
                "retryable": False,
            },
        )

    effective_domain = (
        str(paper.get("detected_domain") or "other")
        if body.domain == "auto"
        else body.domain
    )
    if body.compare_to_audit_id:
        comparison = await asyncio.to_thread(
            _fetch_owned_audit,
            supabase,
            body.compare_to_audit_id,
            current_user.id,
            "id, status, paper_id, paper:papers(id, parent_paper_id, version_number)",
        )
        if comparison is None:
            raise HTTPException(status_code=404, detail="Comparison audit not found")
        if comparison.get("status") != "completed":
            raise HTTPException(
                status_code=409,
                detail="The comparison audit must be completed.",
            )
        comparison_paper = comparison.get("paper")
        if isinstance(comparison_paper, list):
            comparison_paper = comparison_paper[0] if comparison_paper else None
        if not isinstance(comparison_paper, dict):
            comparison_paper = await asyncio.to_thread(
                _load_owned_paper,
                supabase,
                str(comparison.get("paper_id") or ""),
                current_user.id,
            )
        if comparison_paper is None:
            raise HTTPException(status_code=404, detail="Comparison audit not found")
        new_root = str(paper.get("parent_paper_id") or paper.get("id") or "")
        old_root = str(
            comparison_paper.get("parent_paper_id")
            or comparison_paper.get("id")
            or ""
        )
        if new_root != old_root:
            raise HTTPException(
                status_code=409,
                detail="The comparison audit belongs to a different paper version family.",
            )
        if int(comparison_paper.get("version_number") or 1) >= int(
            paper.get("version_number") or 1
        ):
            raise HTTPException(
                status_code=409,
                detail="The comparison audit must belong to an earlier paper version.",
            )
        comparison_topics = {
            str(row.get("topic") or "")
            for row in await asyncio.to_thread(
                _load_round_rows,
                supabase,
                body.compare_to_audit_id,
            )
        }
        if not comparison_topics.intersection(body.round_topics):
            raise HTTPException(
                status_code=409,
                detail="The comparison audit must share at least one selected round topic.",
            )
    # Once admission starts it owns persistence, scheduling, and capacity.
    # asyncio.to_thread work keeps running after cancellation, so shielding only
    # the database call would still skip the subsequent scheduling/cleanup.
    task = asyncio.create_task(
        _admit_audit(body, current_user.id, effective_domain, service_supabase)
    )
    _admission_tasks.add(task)

    def admission_finished(completed: asyncio.Task[AuditCreateResponse]) -> None:
        _admission_tasks.discard(completed)
        if not completed.cancelled():
            # Retrieve a detached request's exception without emitting provider
            # details or an unhandled-task traceback. Normal awaits still raise.
            completed.exception()

    task.add_done_callback(admission_finished)
    return await asyncio.shield(task)


async def _admit_audit(
    body: AuditCreateRequest,
    user_id: str,
    effective_domain: str,
    service_supabase: Any,
) -> AuditCreateResponse:
    """Finish a validated request's admission independently of its connection."""
    if not _audit_capacity.acquire(blocking=False):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "audit_queue_full",
                "message": "The audit queue is currently full. Please retry shortly.",
                "retryable": True,
            },
        )

    audit_id = str(uuid.uuid4())
    try:
        round_rows = await asyncio.to_thread(
            _create_audit_rows,
            service_supabase,
            audit_id=audit_id,
            paper_id=body.paper_id,
            user_id=user_id,
            topics=body.round_topics,
            strictness_level=body.strictness_level,
            depth=body.depth,
            mode=body.mode,
            domain=effective_domain,
        )
    except Exception as exc:
        _audit_capacity.release()
        logger.error("Failed to create audit records (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="The audit could not be started. Please retry.") from exc

    loop = _event_loop or asyncio.get_running_loop()
    _event_hubs[audit_id] = _AuditEventHub()
    try:
        _audit_executor.submit(
            _run_audit_job,
            audit_id=audit_id,
            paper_id=body.paper_id,
            round_rows=round_rows,
            strictness_level=body.strictness_level,
            domain=effective_domain,
            mode=body.mode,
            compare_to_audit_id=body.compare_to_audit_id,
            user_supabase=service_supabase,
            loop=loop,
        )
    except Exception as exc:
        _event_hubs.pop(audit_id, None)
        _audit_capacity.release()
        await asyncio.to_thread(_delete_audit_rows, service_supabase, audit_id)
        logger.error("Failed to schedule audit %s (%s)", audit_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="The audit worker is unavailable. Please retry.") from exc

    round_ids = [str(row["id"]) for row in round_rows]
    logger.info("Audit %s started (%d topic(s))", audit_id, len(round_rows))
    return AuditCreateResponse(
        audit_id=audit_id,
        round_id=round_ids[0],
        round_ids=round_ids,
        round_topics=list(body.round_topics),
        strictness_level=body.strictness_level,
        depth=body.depth,
        mode=body.mode,
        domain=effective_domain,  # type: ignore[arg-type]
        status="in_progress",
    )


def _fetch_owned_audit(
    supabase: Any,
    audit_id: str,
    user_id: str,
    columns: str = "status",
) -> dict[str, Any] | None:
    result = (
        supabase.table("audits")
        .select(columns)
        .eq("id", audit_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return dict(result.data[0]) if result.data else None


@router.get("/audits", response_model=list[AuditSummaryResponse])
async def list_audits(current_user: CurrentUser = Depends(get_current_user)):
    supabase = get_user_supabase(current_user.access_token)
    result = await asyncio.to_thread(
        lambda: (
            supabase.table("audits")
            .select(
                "id, paper_id, round_topic, strictness_level, depth, mode, "
                "domain, status, created_at"
            )
            .eq("user_id", current_user.id)
            .order("created_at", desc=True)
            .execute()
        )
    )
    summaries: list[AuditSummaryResponse] = []
    audit_rows = list(result.data or [])
    topics_by_audit: dict[str, list[str]] = {}
    # A topic plan has at most six rows. Batching 100 audits stays below
    # PostgREST's usual 1,000-row cap without one round query per library item.
    for start in range(0, len(audit_rows), _AUDIT_HISTORY_BATCH_SIZE):
        audit_ids = [str(row["id"]) for row in audit_rows[start:start + _AUDIT_HISTORY_BATCH_SIZE]]
        rounds = await asyncio.to_thread(
            lambda ids=audit_ids: (
                supabase.table("rounds")
                .select("audit_id, topic, round_number")
                .in_("audit_id", ids)
                .order("round_number")
                .execute()
            )
        )
        for row in sorted(rounds.data or [], key=lambda item: int(item.get("round_number") or 0)):
            topics_by_audit.setdefault(str(row["audit_id"]), []).append(str(row["topic"]))
    for row in audit_rows:
        topics = topics_by_audit.get(str(row["id"]), [])
        summaries.append(
            AuditSummaryResponse(
                audit_id=str(row["id"]),
                paper_id=str(row["paper_id"]),
                status=str(row.get("status") or "pending"),
                round_topic=str(row.get("round_topic") or (topics[0] if topics else "")),
                round_topics=topics,
                strictness_level=row.get("strictness_level") or "standard",
                depth=row.get("depth") or "fast",
                mode=row.get("mode") or "author",
                domain=row.get("domain") or "other",
                created_at=str(row.get("created_at") or ""),
            )
        )
    return summaries


def _parse_last_event_id(request: Request) -> int:
    raw = request.headers.get("last-event-id", "0")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _as_sse(record: _EventRecord) -> dict[str, str]:
    return {
        "id": str(record.id),
        "event": record.type,
        "data": json.dumps(record.data, default=str),
    }


@router.get("/audits/{audit_id}/stream")
async def stream_audit(
    audit_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Stream authenticated events; clients reconnect with Last-Event-ID."""
    audit_id = canonical_uuid(audit_id, "audit_id")
    supabase = get_user_supabase(current_user.access_token)
    audit = await asyncio.to_thread(
        _fetch_owned_audit,
        supabase,
        audit_id,
        current_user.id,
        "status, error_message",
    )
    if audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")

    hub = _event_hubs.get(audit_id)
    if hub is None and audit.get("status") == "in_progress":
        raise HTTPException(status_code=409, detail="Live stream is unavailable; use polling.")
    last_event_id = _parse_last_event_id(request)

    async def event_generator():
        if hub is None:
            event_type = "complete" if audit.get("status") == "completed" else "audit_error"
            data = (
                {"status": "completed"}
                if event_type == "complete"
                else {"status": "error", "message": _public_audit_error(audit.get("error_message"))}
            )
            yield {"id": "1", "event": event_type, "data": json.dumps(data)}
            return

        queue, replay = hub.subscribe(last_event_id)
        try:
            for record in replay:
                yield _as_sse(record)
                if record.type in _TERMINAL_EVENT_TYPES:
                    return
            if hub.terminal_type is not None:
                return
            while True:
                if await request.is_disconnected():
                    return
                try:
                    record = await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
                    continue
                yield _as_sse(record)
                if record.type in _TERMINAL_EVENT_TYPES:
                    return
        finally:
            hub.unsubscribe(queue)

    return EventSourceResponse(event_generator())


def _coerce_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return fallback
    return value if value is not None else fallback


def _load_round_rows(supabase: Any, audit_id: str) -> list[dict[str, Any]]:
    result = (
        supabase.table("rounds")
        .select("id, round_number, topic, status")
        .eq("audit_id", audit_id)
        .order("round_number")
        .execute()
    )
    return [dict(row) for row in (result.data or [])]


def _load_turns(
    supabase: Any,
    audit_id: str,
    user_id: str,
) -> TurnsListResponse | None:
    audit = _fetch_owned_audit(
        supabase, audit_id, user_id, "status, error_message"
    )
    if audit is None:
        return None
    rounds = _load_round_rows(supabase, audit_id)
    if not rounds:
        return TurnsListResponse(
            turns=[], verdicts=[], status=audit["status"],
            error_message=_public_audit_error(audit.get("error_message")) if audit["status"] == "error" else None,
        )

    metadata = {
        str(row["id"]): {
            "round_id": str(row["id"]),
            "round_number": int(row.get("round_number") or 0),
            "round_topic": str(row.get("topic") or ""),
        }
        for row in rounds
    }
    round_ids = list(metadata)
    turns_data = supabase.table("turns").select("*").in_("round_id", round_ids).execute()
    verdicts_data = supabase.table("verdicts").select("*").in_("round_id", round_ids).execute()

    turns: list[TurnResponse] = []
    for row in turns_data.data or []:
        meta = metadata[str(row["round_id"])]
        content = _coerce_json(row.get("content"), {})
        turns.append(
            TurnResponse(
                id=str(row["id"]),
                exchange_number=int(row["exchange_number"]),
                agent_type=str(row["agent_type"]),
                sequence=int(row["sequence"]),
                content=content if isinstance(content, dict) else {"raw": content},
                created_at=str(row.get("created_at") or ""),
                **meta,
            )
        )
    turns.sort(key=lambda item: (item.round_number or 0, item.exchange_number, item.sequence))

    verdicts: list[VerdictResponse] = []
    for row in verdicts_data.data or []:
        meta = metadata[str(row["round_id"])]
        cited = _coerce_json(row.get("cited_chunk_ids"), None)
        verdicts.append(
            VerdictResponse(
                id=str(row["id"]),
                exchange_number=int(row["exchange_number"]),
                claim_summary=row.get("claim_summary"),
                verdict_type=str(row["verdict_type"]),
                confidence=row.get("confidence"),
                rationale=row.get("rationale"),
                cited_chunk_ids=cited if isinstance(cited, list) else None,
                **meta,
            )
        )
    verdicts.sort(key=lambda item: (item.round_number or 0, item.exchange_number))
    return TurnsListResponse(
        turns=turns,
        verdicts=verdicts,
        status=str(audit["status"]),
        error_message=_public_audit_error(audit.get("error_message")) if audit["status"] == "error" else None,
    )


@router.get("/audits/{audit_id}/turns", response_model=TurnsListResponse)
async def list_turns(
    audit_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    audit_id = canonical_uuid(audit_id, "audit_id")
    supabase = get_user_supabase(current_user.access_token)
    result = await asyncio.to_thread(_load_turns, supabase, audit_id, current_user.id)
    if result is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    return result


def _load_debriefs(
    supabase: Any,
    audit_id: str,
    user_id: str,
) -> list[dict[str, Any]] | None:
    audit = _fetch_owned_audit(
        supabase,
        audit_id,
        user_id,
        "paper_id, status, domain, error_message",
    )
    if audit is None:
        return None
    # A later topic or synthesis failure does not invalidate a completed topic.
    # Only completed rounds are eligible, even if a partial card was persisted
    # before its round failed. The singular endpoint keeps its final-only gate.
    rounds = [row for row in _load_round_rows(supabase, audit_id) if row.get("status") == "completed"]
    if not rounds:
        return []
    round_ids = [str(row["id"]) for row in rounds]
    cards_result = (
        supabase.table("debrief_cards")
        .select("*")
        .in_("round_id", round_ids)
        .execute()
    )
    cards = {str(row["round_id"]): dict(row) for row in (cards_result.data or [])}

    signals: dict[str, Any] | None = None
    if any(row.get("topic") == "reproducibility" for row in rounds):
        paper = (
            supabase.table("papers")
            .select("reproducibility_signals")
            .eq("id", audit["paper_id"])
            .limit(1)
            .execute()
        )
        if paper.data:
            stored = _coerce_json(paper.data[0].get("reproducibility_signals"), {})
            if isinstance(stored, dict):
                signals = stored.get("by_domain", {}).get(audit.get("domain"), stored)

    loaded: list[dict[str, Any]] = []
    for round_row in rounds:
        round_id = str(round_row["id"])
        if round_id not in cards:
            continue
        row = cards[round_id]
        for field in ("solidified_strengths", "actionable_weaknesses", "contested_points"):
            value = _coerce_json(row.get(field), [])
            row[field] = value if isinstance(value, list) else []
        row.update(
            {
                "round_id": round_id,
                "round_number": int(round_row.get("round_number") or 0),
                "round_topic": str(round_row.get("topic") or ""),
                "reproducibility_checklist": (
                    signals if round_row.get("topic") == "reproducibility" else None
                ),
            }
        )
        loaded.append(row)
    return loaded


def _debrief_response(row: dict[str, Any]) -> DebriefCardResponse:
    return DebriefCardResponse(
        id=str(row["id"]),
        executive_synthesis=row.get("executive_synthesis"),
        solidified_strengths=row.get("solidified_strengths"),
        actionable_weaknesses=row.get("actionable_weaknesses"),
        contested_points=row.get("contested_points"),
        reproducibility_checklist=row.get("reproducibility_checklist"),
        round_id=row.get("round_id"),
        round_number=row.get("round_number"),
        round_topic=row.get("round_topic"),
    )


async def _owned_debrief_rows(audit_id: str, current_user: CurrentUser) -> list[dict[str, Any]]:
    supabase = get_user_supabase(current_user.access_token)
    rows = await asyncio.to_thread(_load_debriefs, supabase, audit_id, current_user.id)
    if rows is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    return rows


@router.get("/audits/{audit_id}/debrief", response_model=DebriefCardResponse)
async def get_debrief(
    audit_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Compatibility endpoint returning the first card after the whole audit.

    Phase 1/2 clients treated a successful singular debrief response as the
    terminal audit result. Keep that contract even though Phase 3's plural
    endpoint can expose completed topic cards while later rounds are running.
    """
    audit_id = canonical_uuid(audit_id, "audit_id")
    supabase = get_user_supabase(current_user.access_token)
    audit = await asyncio.to_thread(
        _fetch_owned_audit,
        supabase,
        audit_id,
        current_user.id,
        "status",
    )
    if audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    if audit.get("status") == "error":
        raise HTTPException(
            status_code=409,
            detail="The audit failed and has no valid final result.",
        )
    if audit.get("status") != "completed":
        raise HTTPException(status_code=404, detail="Debrief card not yet generated.")
    rows = await _owned_debrief_rows(audit_id, current_user)
    if not rows:
        raise HTTPException(status_code=404, detail="Debrief card not yet generated.")
    return _debrief_response(rows[0])


@router.get("/audits/{audit_id}/debriefs", response_model=list[DebriefCardResponse])
async def get_debriefs(
    audit_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    audit_id = canonical_uuid(audit_id, "audit_id")
    rows = await _owned_debrief_rows(audit_id, current_user)
    return [_debrief_response(row) for row in rows]


def _load_final_report(supabase: Any, audit_id: str) -> dict[str, Any] | None:
    result = (
        supabase.table("final_reports")
        .select("id, audit_id, mode, content, created_at")
        .eq("audit_id", audit_id)
        .limit(1)
        .execute()
    )
    return dict(result.data[0]) if result.data else None


async def _owned_final_report(audit_id: str, current_user: CurrentUser) -> dict[str, Any]:
    supabase = get_user_supabase(current_user.access_token)
    audit = await asyncio.to_thread(
        _fetch_owned_audit, supabase, audit_id, current_user.id, "id, status"
    )
    if audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    if audit.get("status") == "error":
        raise HTTPException(
            status_code=409,
            detail="The audit failed and did not produce a valid final report.",
        )
    row = await asyncio.to_thread(_load_final_report, supabase, audit_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Final report not yet generated.")
    return row


@router.get("/audits/{audit_id}/final-report", response_model=FinalReportResponse)
async def get_final_report(
    audit_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    audit_id = canonical_uuid(audit_id, "audit_id")
    return FinalReportResponse(**await _owned_final_report(audit_id, current_user))


@router.get("/audits/{audit_id}/final-report/markdown")
async def export_final_report_markdown(
    audit_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    audit_id = canonical_uuid(audit_id, "audit_id")
    row = await _owned_final_report(audit_id, current_user)
    filename = f"verdict-report-{audit_id}.md"
    return Response(
        content=str(row["content"]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _load_version_diffs(
    supabase: Any,
    audit_id_new: str,
    audit_id_old: str | None,
) -> list[dict[str, Any]]:
    query = (
        supabase.table("version_diffs")
        .select("id, audit_id_old, audit_id_new, round_topic, diff_summary, created_at")
        .eq("audit_id_new", audit_id_new)
    )
    if audit_id_old:
        query = query.eq("audit_id_old", audit_id_old)
    result = query.execute()
    rows = [dict(row) for row in (result.data or [])]
    round_order = {
        str(round_row.get("topic") or ""): int(
            round_row.get("round_number") or 0
        )
        for round_row in _load_round_rows(supabase, audit_id_new)
    }
    rows.sort(
        key=lambda row: (
            round_order.get(str(row.get("round_topic") or ""), 10_000),
            str(row.get("round_topic") or ""),
            str(row.get("created_at") or ""),
        )
    )
    return rows


@router.get("/audits/{audit_id}/version-diffs", response_model=list[VersionDiffResponse])
async def get_version_diffs(
    audit_id: str,
    compare_to: str | None = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
):
    audit_id = canonical_uuid(audit_id, "audit_id")
    old_id = canonical_uuid(compare_to, "compare_to") if compare_to else None
    supabase = get_user_supabase(current_user.access_token)
    audit = await asyncio.to_thread(
        _fetch_owned_audit, supabase, audit_id, current_user.id, "id"
    )
    if audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    rows = await asyncio.to_thread(_load_version_diffs, supabase, audit_id, old_id)
    return [VersionDiffResponse(**row) for row in rows]


def _find_previous_audit_as_user(
    supabase: Any,
    audit_id: str,
) -> str | None:
    """Run automatic comparison selection inside the caller's RLS boundary."""
    with use_supabase(supabase):
        return find_previous_audit_id(audit_id)


def _generate_version_diffs_as_service(
    audit_id: str,
    compare_to: str,
    user_id: str,
) -> list[dict[str, Any]]:
    """Regenerate additive diffs after a fresh trusted ownership check."""
    service_supabase = get_service_supabase()
    ownership = (
        service_supabase.table("audits")
        .select("id, user_id")
        .in_("id", [audit_id, compare_to])
        .execute()
    )
    owned_ids = {
        str(row.get("id"))
        for row in (ownership.data or [])
        if str(row.get("user_id") or "") == user_id
    }
    if owned_ids != {audit_id, compare_to}:
        raise AuditNotFoundError("Audit not found")
    with use_supabase(service_supabase):
        return generate_version_diffs_for_audit(audit_id, compare_to)


class _ModelWorkBusyError(RuntimeError):
    """Another worker currently owns model-provider capacity."""


def _generate_version_diffs_exclusively(
    audit_id: str,
    compare_to: str,
    user_id: str,
) -> list[dict[str, Any]]:
    # Acquire and release in the same worker: cancelling an HTTP coroutine
    # cannot stop to_thread work and must not release its provider lock early.
    with_lock = _llm_work_lock
    if not with_lock.acquire(blocking=False):
        raise _ModelWorkBusyError()
    try:
        return _generate_version_diffs_as_service(audit_id, compare_to, user_id)
    finally:
        with_lock.release()


@router.post(
    "/audits/{audit_id}/version-diffs",
    response_model=list[VersionDiffResponse],
    responses={409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def retry_version_diffs(
    audit_id: str,
    compare_to: str | None = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get or retry the stored revision comparison without rerunning debate.

    The report service is idempotent per audit pair/topic, so already-stored
    comparisons are reused and only missing topic diffs consume LLM calls.
    """
    audit_id = canonical_uuid(audit_id, "audit_id")
    old_id = canonical_uuid(compare_to, "compare_to") if compare_to else None
    supabase = get_user_supabase(current_user.access_token)

    new_audit = await asyncio.to_thread(
        _fetch_owned_audit,
        supabase,
        audit_id,
        current_user.id,
        "id, status",
    )
    if new_audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    if new_audit.get("status") != "completed":
        raise HTTPException(
            status_code=409,
            detail="Version comparison requires a completed audit.",
        )

    if old_id is None:
        try:
            old_id = await asyncio.to_thread(
                _find_previous_audit_as_user,
                supabase,
                audit_id,
            )
        except ReportServiceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "Failed to select a prior audit for version diff retry %s (%s)",
                audit_id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Version comparison is temporarily unavailable.",
            ) from exc
        if old_id is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "No earlier completed audit with a shared round topic "
                    "is available for this paper version."
                ),
            )

    old_audit = await asyncio.to_thread(
        _fetch_owned_audit,
        supabase,
        old_id,
        current_user.id,
        "id, status",
    )
    if old_audit is None:
        # Missing and cross-account UUIDs stay deliberately indistinguishable.
        raise HTTPException(status_code=404, detail="Comparison audit not found")
    if old_audit.get("status") != "completed":
        raise HTTPException(
            status_code=409,
            detail="The comparison audit must be completed.",
        )

    try:
        rows = await asyncio.to_thread(
            _generate_version_diffs_exclusively,
            audit_id,
            old_id,
            current_user.id,
        )
    except _ModelWorkBusyError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Another audit or comparison is using the model provider. "
                "Please retry shortly."
            ),
        ) from exc
    except (FinalReportNotReadyError, VersionComparisonError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AuditNotFoundError as exc:
        # Ownership was already checked above; this indicates a concurrent
        # deletion without leaking whether a different user owns either UUID.
        raise HTTPException(status_code=404, detail="Audit not found") from exc
    except ReportServiceError as exc:
        logger.warning("Version diff retry for audit %s failed (%s)", audit_id, type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Version comparison is temporarily unavailable.",
        ) from exc
    except Exception as exc:
        logger.error("Version diff retry for audit %s failed (%s)", audit_id, type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Version comparison is temporarily unavailable.",
        ) from exc
    return [VersionDiffResponse(**row) for row in rows]
