"""Audit creation, resilient live streaming, polling, and debrief endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.agents.graph import run_audit
from app.api.dependencies import canonical_uuid, get_session_id, get_session_query_id
from app.constants import ROUND_TOPICS
from app.database import get_supabase
from app.models.schemas import (
    AuditCreateRequest,
    AuditCreateResponse,
    DebriefCardResponse,
    ErrorResponse,
    TurnResponse,
    TurnsListResponse,
    VerdictResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["audits"])

_TERMINAL_EVENT_TYPES = frozenset({"complete", "audit_error"})
_EVENT_HISTORY_LIMIT = 512
_FINISHED_HUB_TTL_SECONDS = 15 * 60
_MAX_PENDING_AUDITS = 4
_ORPHANED_AUDIT_MESSAGE = (
    "The audit worker restarted before this audit completed. Please launch it again."
)

# A bounded single worker respects provider limits without creating one blocked
# daemon thread per request.  The semaphore caps both the running and queued jobs.
_audit_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="verdict-audit")
_audit_capacity = threading.BoundedSemaphore(_MAX_PENDING_AUDITS)

_event_loop: asyncio.AbstractEventLoop | None = None


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
    """Capture the application's loop for graph-thread → SSE fan-out."""
    global _event_loop
    _event_loop = loop


def shutdown_audit_executor() -> None:
    """Stop accepting queued work during application shutdown."""
    _audit_executor.shutdown(wait=False, cancel_futures=True)


def recover_orphaned_audits() -> int:
    """Fail durable in-progress rows left behind by this single worker.

    Render is configured with one Uvicorn worker, so no legitimate audit can
    be running when the replacement process executes its startup lifespan.
    """
    supabase = get_supabase()
    result = (
        supabase.table("audits")
        .select("id")
        .eq("status", "in_progress")
        .execute()
    )
    audit_ids = [str(row["id"]) for row in (result.data or []) if row.get("id")]
    for audit_id in audit_ids:
        try:
            supabase.table("audits").update({
                "status": "error",
                "error_message": _ORPHANED_AUDIT_MESSAGE,
            }).eq("id", audit_id).execute()
        except Exception as exc:
            if not _missing_column(exc, "error_message"):
                raise
            supabase.table("audits").update({"status": "error"}).eq(
                "id", audit_id
            ).execute()
        supabase.table("rounds").update({"status": "error"}).eq(
            "audit_id", audit_id
        ).execute()
    if audit_ids:
        logger.warning("Marked %d interrupted audit(s) as failed", len(audit_ids))
    return len(audit_ids)


def _missing_column(exc: Exception, column: str) -> bool:
    message = str(exc).lower()
    return column.lower() in message and any(
        marker in message
        for marker in ("column", "schema cache", "does not exist", "could not find")
    )


def _publish_event(audit_id: str, event_type: str, data: Any) -> None:
    """Publish on the event loop and normalize untrusted callback payloads."""
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


def _mark_audit_error(audit_id: str, round_id: str, message: str) -> None:
    """Persist terminal failure, compatible with the optional error column."""
    supabase = get_supabase()
    safe_message = (message or "Audit failed")[:1000]
    try:
        supabase.table("audits").update(
            {"status": "error", "error_message": safe_message}
        ).eq("id", audit_id).execute()
    except Exception as exc:
        if not _missing_column(exc, "error_message"):
            logger.error("Failed to persist audit %s error: %s", audit_id, exc)
        try:
            supabase.table("audits").update({"status": "error"}).eq(
                "id", audit_id
            ).execute()
        except Exception as fallback_exc:
            logger.error("Failed to persist audit %s status: %s", audit_id, fallback_exc)
    try:
        supabase.table("rounds").update({"status": "error"}).eq(
            "id", round_id
        ).execute()
    except Exception as exc:
        logger.error("Failed to persist round %s error: %s", round_id, exc)


def _run_audit_job(
    *,
    audit_id: str,
    paper_id: str,
    round_id: str,
    round_topic: str,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Run exactly one graph invocation and emit exactly one terminal event."""
    graph_error_message: str | None = None

    def graph_callback(event: dict[str, Any]) -> None:
        nonlocal graph_error_message
        if not isinstance(event, dict):
            return
        event_type = str(event.get("type") or "message")
        data = event.get("data", {})

        # Graph terminal callbacks are buffered.  Success is emitted only after
        # invoke returns; failure only after the exception path has completed.
        if event_type == "error":
            if isinstance(data, dict):
                graph_error_message = str(data.get("message") or "Audit failed")
            else:
                graph_error_message = str(data)
            return
        if event_type == "complete":
            return
        loop.call_soon_threadsafe(_publish_event, audit_id, event_type, data)

    try:
        result = run_audit(paper_id, round_id, round_topic, graph_callback)
        status = result.get("status") if isinstance(result, dict) else None
        if status != "completed":
            raise RuntimeError(
                graph_error_message or f"Audit graph ended in unexpected state: {status!r}"
            )
        loop.call_soon_threadsafe(
            _publish_event,
            audit_id,
            "complete",
            {"status": "completed"},
        )
    except Exception as exc:
        message = graph_error_message or str(exc) or "Audit failed"
        logger.exception("Audit %s failed: %s", audit_id, exc)
        _mark_audit_error(audit_id, round_id, message)
        loop.call_soon_threadsafe(
            _publish_event,
            audit_id,
            "audit_error",
            {"status": "error", "message": message[:1000]},
        )
    finally:
        _audit_capacity.release()


def _load_paper_for_session(paper_id: str, session_id: str) -> bool:
    supabase = get_supabase()
    storage_path: str | None = None
    try:
        paper = (
            supabase.table("papers")
            .select("id, session_id, storage_path")
            .eq("id", paper_id)
            .execute()
        )
        if not paper.data:
            return False
        owner = paper.data[0].get("session_id")
        if owner:
            return owner == session_id
        storage_path = paper.data[0].get("storage_path")
    except Exception as exc:
        if not _missing_column(exc, "session_id"):
            raise
        paper = (
            supabase.table("papers")
            .select("id, storage_path")
            .eq("id", paper_id)
            .execute()
        )
        if not paper.data:
            return False
        storage_path = paper.data[0].get("storage_path")

    # Uploads made while the ownership migration is pending are scoped by a
    # session-prefixed object key. Legacy flat paths require an already-owned
    # audit and cannot be claimed by an arbitrary new session.
    if storage_path == f"{session_id}/{paper_id}.pdf":
        return True
    prior_audit = (
        supabase.table("audits")
        .select("id")
        .eq("paper_id", paper_id)
        .eq("session_id", session_id)
        .limit(1)
        .execute()
    )
    return bool(prior_audit.data)


def _paper_has_indexed_chunks(paper_id: str) -> bool:
    """An audit cannot be grounded unless ingestion produced at least one chunk."""
    result = (
        get_supabase()
        .table("chunks")
        .select("id")
        .eq("paper_id", paper_id)
        .limit(1)
        .execute()
    )
    return bool(result.data)


def _create_audit_rows(
    audit_id: str,
    round_id: str,
    paper_id: str,
    session_id: str,
    round_topic: str,
) -> None:
    supabase = get_supabase()
    inserted_audit = False
    try:
        supabase.table("audits").insert(
            {
                "id": audit_id,
                "paper_id": paper_id,
                "session_id": session_id,
                "round_topic": round_topic,
                "strictness_level": "standard",
                "depth": "fast",
                "status": "in_progress",
            }
        ).execute()
        inserted_audit = True
        supabase.table("rounds").insert(
            {
                "id": round_id,
                "audit_id": audit_id,
                "round_number": 1,
                "topic": round_topic,
                "status": "in_progress",
            }
        ).execute()
    except Exception:
        if inserted_audit:
            try:
                supabase.table("audits").delete().eq("id", audit_id).execute()
            except Exception as cleanup_exc:
                logger.error("Failed to roll back audit row %s: %s", audit_id, cleanup_exc)
        raise


def _delete_audit_rows(audit_id: str) -> None:
    try:
        get_supabase().table("audits").delete().eq("id", audit_id).execute()
    except Exception as exc:
        logger.error("Failed to clean up unscheduled audit %s: %s", audit_id, exc)


@router.post(
    "/audits",
    response_model=AuditCreateResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def create_audit(
    body: AuditCreateRequest,
    session_id: str = Depends(get_session_id),
):
    """Create one three-exchange audit and schedule its single graph invocation."""
    if body.round_topic not in ROUND_TOPICS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid round_topic '{body.round_topic}'. "
                f"Must be one of: {list(ROUND_TOPICS.keys())}"
            ),
        )

    try:
        paper_exists = await asyncio.to_thread(
            _load_paper_for_session,
            body.paper_id,
            session_id,
        )
    except Exception as exc:
        logger.exception("Failed to authorize paper %s: %s", body.paper_id, exc)
        raise HTTPException(status_code=503, detail="Paper service is temporarily unavailable.") from exc
    if not paper_exists:
        raise HTTPException(status_code=404, detail="Paper not found")

    try:
        paper_is_indexed = await asyncio.to_thread(
            _paper_has_indexed_chunks,
            body.paper_id,
        )
    except Exception as exc:
        logger.exception("Failed to verify paper index %s: %s", body.paper_id, exc)
        raise HTTPException(
            status_code=503,
            detail="Paper index service is temporarily unavailable.",
        ) from exc
    if not paper_is_indexed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "paper_not_indexed",
                "message": (
                    "This document was not fully indexed and cannot be audited. "
                    "Please upload it again."
                ),
                "retryable": False,
            },
        )

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
    round_id = str(uuid.uuid4())
    try:
        await asyncio.to_thread(
            _create_audit_rows,
            audit_id,
            round_id,
            body.paper_id,
            session_id,
            body.round_topic,
        )
    except Exception as exc:
        _audit_capacity.release()
        logger.exception("Failed to create audit records: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="The audit could not be started. Please retry.",
        ) from exc

    loop = _event_loop or asyncio.get_running_loop()
    hub = _AuditEventHub()
    _event_hubs[audit_id] = hub
    try:
        _audit_executor.submit(
            _run_audit_job,
            audit_id=audit_id,
            paper_id=body.paper_id,
            round_id=round_id,
            round_topic=body.round_topic,
            loop=loop,
        )
    except Exception as exc:
        _event_hubs.pop(audit_id, None)
        _audit_capacity.release()
        await asyncio.to_thread(_delete_audit_rows, audit_id)
        logger.exception("Failed to schedule audit %s: %s", audit_id, exc)
        raise HTTPException(
            status_code=503,
            detail="The audit worker is unavailable. Please retry.",
        ) from exc

    logger.info("Audit %s started (topic: %s)", audit_id, body.round_topic)
    return AuditCreateResponse(audit_id=audit_id, round_id=round_id, status="in_progress")


def _fetch_owned_audit(
    audit_id: str,
    session_id: str,
    columns: str = "status",
) -> dict[str, Any] | None:
    supabase = get_supabase()
    select_columns = columns
    try:
        result = (
            supabase.table("audits")
            .select(select_columns)
            .eq("id", audit_id)
            .eq("session_id", session_id)
            .execute()
        )
    except Exception as exc:
        if "error_message" not in select_columns or not _missing_column(exc, "error_message"):
            raise
        select_columns = select_columns.replace(", error_message", "").replace(
            "error_message, ", ""
        )
        result = (
            supabase.table("audits")
            .select(select_columns)
            .eq("id", audit_id)
            .eq("session_id", session_id)
            .execute()
        )
    return result.data[0] if result.data else None


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
    session_id: str = Depends(get_session_query_id),
):
    """Fan out live events and replay missed events after reconnect."""
    audit_id = canonical_uuid(audit_id, "audit_id")
    audit = await asyncio.to_thread(
        _fetch_owned_audit,
        audit_id,
        session_id,
        "status, error_message",
    )
    if audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")

    hub = _event_hubs.get(audit_id)
    if hub is None and audit.get("status") == "in_progress":
        # Another process may own the worker.  A non-200 response intentionally
        # moves the client to the durable /turns polling fallback.
        raise HTTPException(
            status_code=409,
            detail="Live stream is unavailable; use the polling endpoint.",
        )

    last_event_id = _parse_last_event_id(request)

    async def event_generator():
        if hub is None:
            if audit.get("status") == "completed":
                yield {
                    "id": "1",
                    "event": "complete",
                    "data": json.dumps({"status": "completed"}),
                }
            else:
                yield {
                    "id": "1",
                    "event": "audit_error",
                    "data": json.dumps(
                        {
                            "status": "error",
                            "message": audit.get("error_message") or "Audit failed",
                        }
                    ),
                }
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
    return value


def _load_turns(audit_id: str, session_id: str) -> TurnsListResponse | None:
    audit = _fetch_owned_audit(audit_id, session_id, "status, error_message")
    if audit is None:
        return None

    supabase = get_supabase()
    rounds = supabase.table("rounds").select("id").eq("audit_id", audit_id).execute()
    if not rounds.data:
        return TurnsListResponse(
            turns=[],
            verdicts=[],
            status=audit["status"],
            error_message=audit.get("error_message"),
        )

    round_ids = [row["id"] for row in rounds.data]
    turns_data = (
        supabase.table("turns")
        .select("*")
        .in_("round_id", round_ids)
        .order("exchange_number")
        .order("sequence")
        .execute()
    )
    verdicts_data = (
        supabase.table("verdicts")
        .select("*")
        .in_("round_id", round_ids)
        .order("exchange_number")
        .execute()
    )

    turns: list[TurnResponse] = []
    for row in turns_data.data or []:
        content = _coerce_json(row.get("content"), {})
        if not isinstance(content, dict):
            content = {"raw": content}
        turns.append(
            TurnResponse(
                id=str(row["id"]),
                exchange_number=int(row["exchange_number"]),
                agent_type=str(row["agent_type"]),
                sequence=int(row["sequence"]),
                content=content,
                created_at=str(row.get("created_at") or ""),
            )
        )

    verdicts: list[VerdictResponse] = []
    for row in verdicts_data.data or []:
        cited = _coerce_json(row.get("cited_chunk_ids"), None)
        if cited is not None and not isinstance(cited, list):
            cited = None
        verdicts.append(
            VerdictResponse(
                id=str(row["id"]),
                exchange_number=int(row["exchange_number"]),
                claim_summary=row.get("claim_summary"),
                verdict_type=str(row["verdict_type"]),
                confidence=row.get("confidence"),
                rationale=row.get("rationale"),
                cited_chunk_ids=cited,
            )
        )

    return TurnsListResponse(
        turns=turns,
        verdicts=verdicts,
        status=str(audit["status"]),
        error_message=audit.get("error_message"),
    )


@router.get("/audits/{audit_id}/turns", response_model=TurnsListResponse)
async def list_turns(
    audit_id: str,
    session_id: str = Depends(get_session_id),
):
    """Return durable turns/verdicts for reconciliation and polling fallback."""
    audit_id = canonical_uuid(audit_id, "audit_id")
    result = await asyncio.to_thread(_load_turns, audit_id, session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    return result


def _load_debrief(audit_id: str, session_id: str) -> dict[str, Any] | None:
    audit = _fetch_owned_audit(audit_id, session_id, "paper_id, round_topic, status")
    if audit is None:
        return None
    if audit.get("status") == "error":
        return {"audit_error": True}
    if audit.get("status") != "completed":
        return {"not_ready": True}

    supabase = get_supabase()
    rounds = supabase.table("rounds").select("id").eq("audit_id", audit_id).execute()
    if not rounds.data:
        return {"not_ready": True}
    round_id = rounds.data[0]["id"]
    debrief = (
        supabase.table("debrief_cards")
        .select("*")
        .eq("round_id", round_id)
        .execute()
    )
    if not debrief.data:
        return {"not_ready": True}

    row = dict(debrief.data[0])
    for field in ("solidified_strengths", "actionable_weaknesses", "contested_points"):
        value = _coerce_json(row.get(field), [])
        row[field] = value if isinstance(value, list) else []

    row["reproducibility_checklist"] = None
    if audit.get("round_topic") == "reproducibility" and audit.get("paper_id"):
        try:
            paper = (
                supabase.table("papers")
                .select("reproducibility_signals")
                .eq("id", audit["paper_id"])
                .execute()
            )
            if paper.data:
                signals = _coerce_json(paper.data[0].get("reproducibility_signals"), None)
                if isinstance(signals, dict):
                    row["reproducibility_checklist"] = signals
        except Exception as exc:
            logger.warning("Could not fetch reproducibility checklist: %s", exc)
    return row


@router.get(
    "/audits/{audit_id}/debrief",
    response_model=DebriefCardResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_debrief(
    audit_id: str,
    session_id: str = Depends(get_session_id),
):
    """Return the completed debrief for this session's audit."""
    audit_id = canonical_uuid(audit_id, "audit_id")
    row = await asyncio.to_thread(_load_debrief, audit_id, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    if row.get("audit_error"):
        raise HTTPException(
            status_code=409,
            detail="The audit failed and did not produce a valid final debrief.",
        )
    if row.get("not_ready"):
        raise HTTPException(
            status_code=404,
            detail="Debrief card not yet generated — audit may still be in progress.",
        )
    return DebriefCardResponse(
        id=str(row["id"]),
        executive_synthesis=row.get("executive_synthesis"),
        solidified_strengths=row.get("solidified_strengths"),
        actionable_weaknesses=row.get("actionable_weaknesses"),
        contested_points=row.get("contested_points"),
        reproducibility_checklist=row.get("reproducibility_checklist"),
    )
