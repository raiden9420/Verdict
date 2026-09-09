"""Paper upload, validation, ingestion, and viewer endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse

from app.api.dependencies import CurrentUser, canonical_uuid, get_current_user
from app.constants import EMBEDDING_SPACE_ID, MAX_PDF_SIZE_BYTES
from app.database import (
    get_service_supabase,
    get_supabase,
    get_user_supabase,
    use_supabase,
)
from app.models.schemas import (
    ErrorResponse,
    PaperSummaryResponse,
    PaperUploadResponse,
    PdfUrlResponse,
    RelevanceRejectionDetail,
)
from app.services.embedding_service import EmbeddingServiceError, embed_batch
from app.services.pdf_service import (
    PDFValidationError,
    chunk_pages,
    delete_pdf,
    upload_pdf,
    validate_and_parse_pdf,
)
from app.services.relevance_service import (
    RelevanceServiceUnavailable,
    classify_document_relevance,
)
from app.services.reference_service import extract_reference_list
from app.services.reproducibility_service import scan_reproducibility_by_domain

logger = logging.getLogger(__name__)
router = APIRouter(tags=["papers"])

_DB_BATCH_SIZE = 20
_RELEVANCE_SAMPLE_CHARS = 6000
_RELEVANCE_SAMPLE_SEGMENTS = 6


def _missing_column(exc: Exception, column: str) -> bool:
    """Recognize PostgREST's missing-column/schema-cache error conservatively."""
    message = str(exc).lower()
    return column.lower() in message and any(
        marker in message
        for marker in ("column", "schema cache", "does not exist", "could not find")
    )


def _build_relevance_sample(pages: list[tuple[int, str]]) -> str:
    """Sample the full document instead of trusting an academic-looking prefix."""
    full_text = "\n\n".join(
        f"[PAGE {page_number}]\n{text.strip()}"
        for page_number, text in pages
        if text and text.strip()
    )
    if len(full_text) <= _RELEVANCE_SAMPLE_CHARS:
        return full_text

    window = _RELEVANCE_SAMPLE_CHARS // _RELEVANCE_SAMPLE_SEGMENTS
    max_start = len(full_text) - window
    starts = {
        round(index * max_start / (_RELEVANCE_SAMPLE_SEGMENTS - 1))
        for index in range(_RELEVANCE_SAMPLE_SEGMENTS)
    }
    return "\n\n[DOCUMENT SAMPLE GAP]\n\n".join(
        full_text[start : start + window] for start in sorted(starts)
    )[:_RELEVANCE_SAMPLE_CHARS]


def _insert_paper_compat(supabase: Any, paper_data: dict[str, Any]) -> None:
    """Insert a paper while tolerating only non-security optional old columns."""
    row = dict(paper_data)
    optional_columns = (
        "reference_list",
        "reproducibility_signals",
        "embedding_space",
        "detected_domain",
    )
    while True:
        try:
            supabase.table("papers").insert(row).execute()
            return
        except Exception as exc:
            missing = next(
                (
                    column
                    for column in optional_columns
                    if column in row and _missing_column(exc, column)
                ),
                None,
            )
            if missing is None:
                raise
            logger.warning(
                "Optional papers.%s column is not deployed; continuing in compatibility mode. "
                "Apply the latest backend migration.",
                missing,
            )
            row.pop(missing)


def _rollback_ingestion(paper_id: str, storage_path: str, uploaded: bool) -> None:
    """Compensate for partial object/database writes without masking the root error."""
    supabase = get_supabase()
    try:
        supabase.table("chunks").delete().eq("paper_id", paper_id).execute()
    except Exception as exc:
        logger.error("Failed to roll back chunks for paper %s (%s)", paper_id, type(exc).__name__)
    try:
        supabase.table("papers").delete().eq("id", paper_id).execute()
    except Exception as exc:
        logger.error("Failed to roll back paper %s (%s)", paper_id, type(exc).__name__)
    if uploaded:
        try:
            delete_pdf(storage_path)
        except Exception as exc:
            logger.error("Failed to roll back stored PDF for paper %s (%s)", paper_id, type(exc).__name__)


def _persist_ingestion(
    *,
    paper_id: str,
    filename: str,
    user_id: str,
    detected_domain: str,
    parent_paper_id: str | None,
    version_number: int,
    file_bytes: bytes,
    parsed: dict[str, Any],
    chunks: list[dict[str, Any]],
    embeddings: list[list[float]],
    reproducibility_signals: dict[str, Any],
    reference_list: list[dict[str, Any]],
) -> None:
    """Persist accepted ingestion artifacts with compensating rollback."""
    if len(chunks) != len(embeddings):
        raise EmbeddingServiceError(
            f"Embedding count mismatch: {len(embeddings)} vectors for {len(chunks)} chunks"
        )

    storage_path = parsed["storage_path"]
    uploaded = False
    try:
        # Mark before the request so a response-loss after a successful remote
        # write still triggers a compensating delete.
        uploaded = True
        upload_pdf(file_bytes, storage_path)

        supabase = get_supabase()
        _insert_paper_compat(
            supabase,
            {
                "id": paper_id,
                "filename": filename,
                "storage_path": storage_path,
                "page_count": parsed["page_count"],
                "user_id": user_id,
                "detected_domain": detected_domain,
                "parent_paper_id": parent_paper_id,
                "version_number": version_number,
                "reproducibility_signals": reproducibility_signals,
                "reference_list": reference_list,
                "embedding_space": EMBEDDING_SPACE_ID,
            },
        )

        chunk_rows = []
        for chunk, embedding in zip(chunks, embeddings):
            chunk_rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "paper_id": paper_id,
                    "text": chunk["text"],
                    "page_number": chunk["page_number"],
                    "chunk_index": chunk["chunk_index"],
                    "embedding": json.dumps(embedding, separators=(",", ":")),
                }
            )

        for start in range(0, len(chunk_rows), _DB_BATCH_SIZE):
            supabase.table("chunks").insert(
                chunk_rows[start : start + _DB_BATCH_SIZE]
            ).execute()
    except Exception:
        _rollback_ingestion(paper_id, storage_path, uploaded)
        raise


def _resolve_version_link(
    supabase: Any,
    parent_paper_id: str | None,
) -> tuple[str | None, int]:
    """Resolve a selected prior version to the owned version-series root."""
    if not parent_paper_id:
        return None, 1

    parent_id = canonical_uuid(parent_paper_id, "parent_paper_id")
    parent_result = (
        supabase.table("papers")
        .select("id, parent_paper_id, version_number")
        .eq("id", parent_id)
        .limit(1)
        .execute()
    )
    if not parent_result.data:
        # RLS intentionally makes a foreign user's UUID indistinguishable from
        # a nonexistent paper.
        raise HTTPException(status_code=404, detail="Parent paper not found")

    parent = parent_result.data[0]
    root_id = str(parent.get("parent_paper_id") or parent["id"])
    series = (
        supabase.table("papers")
        .select("version_number")
        .or_(f"id.eq.{root_id},parent_paper_id.eq.{root_id}")
        .order("version_number", desc=True)
        .limit(1)
        .execute()
    )
    highest = max(
        [int(row.get("version_number") or 1) for row in (series.data or [])]
        or [int(parent.get("version_number") or 1)]
    )
    return root_id, highest + 1


def _next_version_number(supabase: Any, root_id: str, user_id: str) -> int:
    """Re-read a version family immediately before the trusted insert."""
    result = (
        supabase.table("papers")
        .select("version_number")
        .eq("user_id", user_id)
        .or_(f"id.eq.{root_id},parent_paper_id.eq.{root_id}")
        .order("version_number", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise RuntimeError("The selected paper version family no longer exists")
    return int(result.data[0].get("version_number") or 1) + 1


def _is_version_number_conflict(exc: Exception) -> bool:
    """Identify only the revision-series unique constraint for safe retry."""
    details = " ".join(
        str(value)
        for value in (
            exc,
            getattr(exc, "code", ""),
            getattr(exc, "message", ""),
            getattr(exc, "details", ""),
        )
    ).lower()
    return "uq_papers_parent_version" in details or (
        "23505" in details
        and "parent_paper_id" in details
        and "version_number" in details
    )


@router.post(
    "/papers",
    response_model=PaperUploadResponse,
    responses={
        400: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def upload_paper(
    file: UploadFile = File(...),
    force: bool = Query(False),
    parent_paper_id: str | None = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Validate and ingest one research PDF, unless the user explicitly overrides."""
    filename = (file.filename or "upload.pdf").strip() or "upload.pdf"
    paper_id = str(uuid.uuid4())
    user_supabase = get_user_supabase(current_user.access_token)
    service_supabase = get_service_supabase()

    try:
        parent_root_id, version_number = await asyncio.to_thread(
            _resolve_version_link,
            user_supabase,
            parent_paper_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to authorize parent paper (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Paper version service is temporarily unavailable.",
        ) from exc

    # A bounded read rejects oversized uploads without loading the remainder into RAM.
    file_bytes = await file.read(MAX_PDF_SIZE_BYTES + 1)
    if len(file_bytes) > MAX_PDF_SIZE_BYTES:
        limit_mb = MAX_PDF_SIZE_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"PDF exceeds the {limit_mb} MB upload limit.",
        )

    try:
        parsed = await asyncio.to_thread(
            validate_and_parse_pdf,
            file_bytes,
            filename,
            paper_id,
        )
    except PDFValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Storage RLS policies scope objects by the authenticated user's first path
    # segment. Both values are canonical UUIDs, so the key is unambiguous.
    parsed["storage_path"] = f"{current_user.id}/{paper_id}.pdf"

    # Classification is mandatory even for an override. ``force`` changes what
    # happens after a confident non-research result; it never bypasses an outage.
    sample_text = _build_relevance_sample(parsed["pages"])
    try:
        relevance = await asyncio.to_thread(
            classify_document_relevance,
            sample_text,
        )
    except RelevanceServiceUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "relevance_service_unavailable",
                "message": "Research-paper validation is temporarily unavailable. Please retry.",
                "relevance_failed": False,
                "override_allowed": False,
                "retryable": True,
            },
        ) from exc

    if not relevance.is_research_paper:
        if not force:
            logger.warning(
                "Document relevance check rejected paper %s",
                paper_id,
            )
            rejection = RelevanceRejectionDetail(
                message=(
                    "This document does not appear to be a research paper. "
                    "Verdict audits academic manuscripts."
                ),
                reason=relevance.reason,
            )
            raise HTTPException(status_code=400, detail=rejection.model_dump())
        logger.info(
            "Research relevance gate explicitly overridden for paper %s",
            paper_id,
        )

    # Bibliography extraction is intentionally after the mandatory relevance
    # gate: rejected non-papers never consume citation-system LLM capacity. Once
    # admitted, extraction itself degrades to [] so this optional capability can
    # never become a new upload failure mode.
    reference_list = await asyncio.to_thread(
        extract_reference_list,
        parsed["pages"],
    )

    chunks = await asyncio.to_thread(chunk_pages, parsed["pages"])
    if not chunks:
        raise HTTPException(
            status_code=400,
            detail="Could not extract meaningful text chunks from this PDF.",
        )

    try:
        embeddings = await asyncio.to_thread(
            embed_batch,
            [chunk["text"] for chunk in chunks],
        )
    except EmbeddingServiceError as exc:
        logger.error("Embedding failed for accepted paper %s (%s)", paper_id, type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "embedding_service_unavailable",
                "message": "Document indexing is temporarily unavailable. Please retry.",
                "retryable": True,
            },
        ) from exc

    reproducibility_by_domain = await asyncio.to_thread(
        scan_reproducibility_by_domain,
        parsed["pages"],
    )
    reproducibility_signals = {
        "detected_domain": relevance.detected_domain,
        "by_domain": reproducibility_by_domain,
    }

    persisted_version_number = version_number
    try:
        def persist_as_backend() -> None:
            nonlocal persisted_version_number
            # Ownership was established by the verified JWT above. Mutations
            # use the backend identity so a queued/large upload cannot fail if
            # the browser token rotates, and public users never receive direct
            # write privileges to trusted audit artifacts or Storage.
            with use_supabase(service_supabase):
                # The unique family/version index is the final arbiter. A
                # concurrent revision upload can win between the initial UI
                # ownership lookup and this insert, so refresh and retry only
                # that well-identified conflict without repeating parsing,
                # embeddings, or classification.
                for attempt in range(4):
                    if parent_root_id:
                        persisted_version_number = _next_version_number(
                            service_supabase,
                            parent_root_id,
                            current_user.id,
                        )
                    try:
                        _persist_ingestion(
                            paper_id=paper_id,
                            filename=filename,
                            user_id=current_user.id,
                            detected_domain=relevance.detected_domain,
                            parent_paper_id=parent_root_id,
                            version_number=persisted_version_number,
                            file_bytes=file_bytes,
                            parsed=parsed,
                            chunks=chunks,
                            embeddings=embeddings,
                            reproducibility_signals=reproducibility_signals,
                            reference_list=reference_list,
                        )
                        return
                    except Exception as exc:
                        if (
                            not parent_root_id
                            or attempt == 3
                            or not _is_version_number_conflict(exc)
                        ):
                            raise
                        logger.info(
                            "Paper version %d was allocated concurrently; retrying",
                            persisted_version_number,
                        )

        await asyncio.to_thread(persist_as_backend)
    except Exception as exc:
        logger.error("Ingestion failed for paper %s (%s)", paper_id, type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ingestion_failed",
                "message": "The document could not be saved completely. Please retry.",
                "retryable": True,
            },
        ) from exc

    logger.info(
        "Paper %s uploaded: %d pages, %d chunks",
        paper_id,
        parsed["page_count"],
        len(chunks),
    )
    return PaperUploadResponse(
        paper_id=paper_id,
        filename=filename,
        page_count=parsed["page_count"],
        chunk_count=len(chunks),
        detected_domain=relevance.detected_domain,
        parent_paper_id=parent_root_id,
        version_number=persisted_version_number,
    )


@router.get("/papers", response_model=list[PaperSummaryResponse])
async def list_papers(current_user: CurrentUser = Depends(get_current_user)):
    """List the signed-in user's paper/version history (RLS enforced)."""
    supabase = get_user_supabase(current_user.access_token)
    result = await asyncio.to_thread(
        lambda: (
            supabase.table("papers")
            .select(
                "id, filename, page_count, detected_domain, parent_paper_id, "
                "version_number, uploaded_at"
            )
            .eq("user_id", current_user.id)
            .order("uploaded_at", desc=True)
            .execute()
        )
    )
    return [
        PaperSummaryResponse(
            id=str(row["id"]),
            filename=str(row.get("filename") or "paper.pdf"),
            page_count=row.get("page_count"),
            detected_domain=row.get("detected_domain") or "other",
            parent_paper_id=(
                str(row["parent_paper_id"]) if row.get("parent_paper_id") else None
            ),
            version_number=int(row.get("version_number") or 1),
            uploaded_at=str(row.get("uploaded_at") or ""),
        )
        for row in (result.data or [])
    ]


def _load_authorized_pdf(supabase: Any, paper_id: str, user_id: str) -> str | None:
    """Return an owned storage path; RLS provides the primary boundary."""
    result = (
        supabase.table("papers")
        .select("storage_path")
        .eq("id", paper_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return result.data[0].get("storage_path") if result.data else None


def _create_signed_pdf_url(storage_path: str, supabase: Any | None = None) -> str:
    signed = (
        (supabase or get_supabase())
        .storage.from_("papers")
        .create_signed_url(storage_path, expires_in=5 * 60)
    )
    url = signed.get("signedURL") or signed.get("signedUrl")
    if not url:
        raise RuntimeError("Storage service did not return a signed PDF URL")
    return str(url)


async def _signed_pdf_for_user(
    paper_id: str,
    current_user: CurrentUser,
) -> str:
    paper_id = canonical_uuid(paper_id, "paper_id")
    supabase = get_user_supabase(current_user.access_token)
    try:
        storage_path = await asyncio.to_thread(
            _load_authorized_pdf,
            supabase,
            paper_id,
            current_user.id,
        )
    except Exception as exc:
        logger.error("Failed to authorize PDF %s (%s)", paper_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="PDF service is temporarily unavailable.") from exc
    if not storage_path:
        # Do not reveal whether another account owns the UUID.
        raise HTTPException(status_code=404, detail="PDF not found")

    try:
        return await asyncio.to_thread(_create_signed_pdf_url, storage_path, supabase)
    except Exception as exc:
        logger.error("Failed to sign PDF URL for %s (%s)", paper_id, type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="PDF service is temporarily unavailable.",
        ) from exc


@router.get("/papers/{paper_id}/pdf")
async def serve_pdf(
    paper_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Preserve the Phase 1/2 redirect endpoint with Bearer authentication."""
    return RedirectResponse(await _signed_pdf_for_user(paper_id, current_user))


@router.get("/papers/{paper_id}/pdf-url", response_model=PdfUrlResponse)
async def get_pdf_url(
    paper_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return a short-lived URL for an authenticated iframe/browser tab."""
    return PdfUrlResponse(url=await _signed_pdf_for_user(paper_id, current_user))
