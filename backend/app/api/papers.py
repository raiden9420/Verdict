"""
Paper upload & ingestion endpoint.

POST /papers — upload a PDF, validate, parse, chunk, embed, store.
GET  /papers/{paper_id}/pdf — serve the raw PDF file for the Document Viewer.
"""

import uuid
import json
import logging

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Query
from fastapi.responses import RedirectResponse

from app.api.dependencies import get_session_id
from app.database import get_supabase
from app.models.schemas import PaperUploadResponse, ErrorResponse
from app.services.pdf_service import (
    validate_and_parse_pdf,
    chunk_pages,
    PDFValidationError,
    DocumentRelevanceError,
)
from app.services.embedding_service import embed_batch
from app.services.reproducibility_service import scan_paper_reproducibility
from app.services.relevance_service import classify_document_relevance

logger = logging.getLogger(__name__)
router = APIRouter(tags=["papers"])



@router.post(
    "/papers",
    response_model=PaperUploadResponse,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
)
async def upload_paper(
    file: UploadFile = File(...),
    force: bool = Query(False),
    session_id: str = Depends(get_session_id),
):
    """
    Upload a PDF, validate it, parse + chunk + embed, and store everything.

    Rejects:
    - Files > 20 MB
    - PDFs > 40 pages
    - Scanned / image-only PDFs (empty or near-empty extracted text)
    - Non-research documents (unless force=True)
    """
    paper_id = str(uuid.uuid4())
    file_bytes = await file.read()

    # ---- Validate & parse ----
    try:
        parsed = validate_and_parse_pdf(file_bytes, file.filename or "upload.pdf", paper_id)
    except PDFValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # ---- Document Relevance Gate ----
    if not force:
        sample_text = "\n\n".join(text for _, text in parsed["pages"][:3])[:6000]
        relevance = classify_document_relevance(sample_text)
        if not relevance.get("is_research_paper", True):
            reason = relevance.get("reason", "The document does not appear to be an academic manuscript.")
            logger.warning("Document relevance check failed for '%s': %s", file.filename, reason)
            raise HTTPException(
                status_code=400,
                detail={
                    "message": f"This document doesn't appear to be a research paper — {reason}. Verdict audits academic manuscripts.",
                    "reason": reason,
                    "relevance_failed": True,
                },
            )

    # ---- Chunk ----
    chunks = chunk_pages(parsed["pages"])
    if not chunks:
        raise HTTPException(
            status_code=400,
            detail="Could not extract meaningful text chunks from this PDF.",
        )

    # ---- Embed all chunks ----
    texts = [c["text"] for c in chunks]
    embeddings = embed_batch(texts)

    # ---- Scan reproducibility signals ----
    reproducibility_signals = scan_paper_reproducibility(parsed["pages"])

    # ---- Store paper ----
    supabase = get_supabase()
    paper_data = {
        "id": paper_id,
        "filename": file.filename or "upload.pdf",
        "storage_path": parsed["filepath"],
        "page_count": parsed["page_count"],
        "reproducibility_signals": reproducibility_signals,
    }
    try:
        supabase.table("papers").insert(paper_data).execute()
    except Exception as exc:
        if "reproducibility_signals" in str(exc):
            logger.error(
                "CRITICAL SCHEMA MISMATCH: 'papers' table in Supabase is missing 'reproducibility_signals' column! "
                "Migration 002 MUST be executed in the Supabase SQL editor (ALTER TABLE papers ADD COLUMN IF NOT EXISTS reproducibility_signals JSONB;). "
                "Retrying insert without reproducibility_signals: %s",
                exc,
            )
            paper_data.pop("reproducibility_signals", None)
            supabase.table("papers").insert(paper_data).execute()
        else:
            raise



    # ---- Store chunks with embeddings ----
    chunk_rows = []
    for i, chunk in enumerate(chunks):
        emb_str = "[" + ",".join(str(v) for v in embeddings[i]) + "]"
        chunk_rows.append({
            "id": str(uuid.uuid4()),
            "paper_id": paper_id,
            "text": chunk["text"],
            "page_number": chunk["page_number"],
            "chunk_index": chunk["chunk_index"],
            "embedding": emb_str,
        })

    # Insert chunks in batches to avoid payload limits
    BATCH_SIZE = 20
    for start in range(0, len(chunk_rows), BATCH_SIZE):
        batch = chunk_rows[start : start + BATCH_SIZE]
        supabase.table("chunks").insert(batch).execute()

    logger.info(
        "Paper %s uploaded: %d pages, %d chunks",
        paper_id,
        parsed["page_count"],
        len(chunks),
    )

    return PaperUploadResponse(
        paper_id=paper_id,
        filename=file.filename or "upload.pdf",
        page_count=parsed["page_count"],
        chunk_count=len(chunks),
    )


@router.get("/papers/{paper_id}/pdf")
async def serve_pdf(paper_id: str):
    """Serve the raw PDF file for the Document Viewer by redirecting to Supabase Storage."""
    supabase = get_supabase()
    paper = supabase.table("papers").select("storage_path").eq("id", paper_id).execute()
    if not paper.data:
        raise HTTPException(status_code=404, detail="PDF not found")

    storage_path = paper.data[0]["storage_path"]
    url = supabase.storage.from_("papers").get_public_url(storage_path)

    return RedirectResponse(url)
