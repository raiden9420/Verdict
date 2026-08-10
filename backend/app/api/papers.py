"""Paper upload, validation, ingestion, and viewer endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse

from app.api.dependencies import canonical_uuid, get_session_id, get_session_query_id
from app.constants import EMBEDDING_SPACE_ID, MAX_PDF_SIZE_BYTES
from app.database import get_supabase
from app.models.schemas import (
    ErrorResponse,
    PaperUploadResponse,
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
from app.services.reproducibility_service import scan_paper_reproducibility

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
    """Insert with compatibility for deployments awaiting optional migrations."""
    row = dict(paper_data)
    optional_columns = ("session_id", "reproducibility_signals", "embedding_space")
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
        logger.error("Failed to roll back chunks for paper %s: %s", paper_id, exc)
    try:
        supabase.table("papers").delete().eq("id", paper_id).execute()
    except Exception as exc:
        logger.error("Failed to roll back paper %s: %s", paper_id, exc)
    if uploaded:
        try:
            delete_pdf(storage_path)
        except Exception as exc:
            logger.error("Failed to roll back stored PDF %s: %s", storage_path, exc)


def _persist_ingestion(
    *,
    paper_id: str,
    filename: str,
    session_id: str,
    file_bytes: bytes,
    parsed: dict[str, Any],
    chunks: list[dict[str, Any]],
    embeddings: list[list[float]],
    reproducibility_signals: dict[str, Any],
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
                "session_id": session_id,
                "reproducibility_signals": reproducibility_signals,
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
    session_id: str = Depends(get_session_id),
):
    """Validate and ingest one research PDF, unless the user explicitly overrides."""
    filename = (file.filename or "upload.pdf").strip() or "upload.pdf"
    paper_id = str(uuid.uuid4())

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

    # The object key is an ownership fallback for deployments while the
    # papers.session_id migration is rolling out. UUID validation makes both
    # path segments safe and non-ambiguous.
    parsed["storage_path"] = f"{session_id}/{paper_id}.pdf"

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
                "Document relevance check rejected '%s': %s",
                filename,
                relevance.reason,
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
            "Research relevance gate explicitly overridden for '%s' after "
            "a confirmed non-research classification: %s",
            filename,
            relevance.reason,
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
        logger.error("Embedding failed for accepted upload '%s': %s", filename, exc)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "embedding_service_unavailable",
                "message": "Document indexing is temporarily unavailable. Please retry.",
                "retryable": True,
            },
        ) from exc

    reproducibility_signals = await asyncio.to_thread(
        scan_paper_reproducibility,
        parsed["pages"],
    )

    try:
        await asyncio.to_thread(
            _persist_ingestion,
            paper_id=paper_id,
            filename=filename,
            session_id=session_id,
            file_bytes=file_bytes,
            parsed=parsed,
            chunks=chunks,
            embeddings=embeddings,
            reproducibility_signals=reproducibility_signals,
        )
    except Exception as exc:
        logger.exception("Atomic ingestion failed for paper %s: %s", paper_id, exc)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ingestion_failed",
                "message": "The document could not be saved. No audit data was retained; please retry.",
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
    )


def _load_authorized_pdf(paper_id: str, session_id: str) -> str | None:
    """Return storage path if the session owns the paper (or its legacy audit)."""
    supabase = get_supabase()
    try:
        result = (
            supabase.table("papers")
            .select("storage_path, session_id")
            .eq("id", paper_id)
            .execute()
        )
        if not result.data:
            return None
        row = result.data[0]
        if row.get("session_id") == session_id:
            return row.get("storage_path")
        if row.get("session_id"):
            return None
        storage_path = row.get("storage_path")
    except Exception as exc:
        if not _missing_column(exc, "session_id"):
            raise
        result = (
            supabase.table("papers")
            .select("storage_path")
            .eq("id", paper_id)
            .execute()
        )
        if not result.data:
            return None
        storage_path = result.data[0].get("storage_path")

    if storage_path == f"{session_id}/{paper_id}.pdf":
        return storage_path

    # Compatibility path for papers created before session ownership existed.
    audit = (
        supabase.table("audits")
        .select("id")
        .eq("paper_id", paper_id)
        .eq("session_id", session_id)
        .limit(1)
        .execute()
    )
    return storage_path if audit.data else None


def _create_signed_pdf_url(storage_path: str) -> str:
    signed = (
        get_supabase()
        .storage.from_("papers")
        .create_signed_url(storage_path, expires_in=5 * 60)
    )
    url = signed.get("signedURL") or signed.get("signedUrl")
    if not url:
        raise RuntimeError("Storage service did not return a signed PDF URL")
    return str(url)


@router.get("/papers/{paper_id}/pdf")
async def serve_pdf(
    paper_id: str,
    session_id: str = Depends(get_session_query_id),
):
    """Authorize and redirect the document viewer to its stored PDF."""
    paper_id = canonical_uuid(paper_id, "paper_id")
    try:
        storage_path = await asyncio.to_thread(
            _load_authorized_pdf,
            paper_id,
            session_id,
        )
    except Exception as exc:
        logger.exception("Failed to authorize PDF %s: %s", paper_id, exc)
        raise HTTPException(status_code=503, detail="PDF service is temporarily unavailable.") from exc
    if not storage_path:
        # Do not reveal whether another session owns the UUID.
        raise HTTPException(status_code=404, detail="PDF not found")

    try:
        url = await asyncio.to_thread(_create_signed_pdf_url, storage_path)
    except Exception as exc:
        logger.exception("Failed to sign PDF URL for %s: %s", paper_id, exc)
        raise HTTPException(
            status_code=503,
            detail="PDF service is temporarily unavailable.",
        ) from exc
    return RedirectResponse(url)
