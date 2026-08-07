"""
Paper upload & ingestion endpoint.

POST /papers — upload a PDF, validate, parse, chunk, embed, store.
GET  /papers/{paper_id}/pdf — serve the raw PDF file for the Document Viewer.
"""

import uuid
import json
import logging

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import FileResponse

from app.api.dependencies import get_session_id
from app.database import get_supabase
from app.models.schemas import PaperUploadResponse, ErrorResponse
from app.services.pdf_service import (
    validate_and_parse_pdf,
    chunk_pages,
    PDFValidationError,
    UPLOAD_DIR,
)
from app.services.embedding_service import embed_batch

logger = logging.getLogger(__name__)
router = APIRouter(tags=["papers"])


@router.post(
    "/papers",
    response_model=PaperUploadResponse,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
)
async def upload_paper(
    file: UploadFile = File(...),
    session_id: str = Depends(get_session_id),
):
    """
    Upload a PDF, validate it, parse + chunk + embed, and store everything.

    Rejects:
    - Files > 20 MB
    - PDFs > 40 pages
    - Scanned / image-only PDFs (empty or near-empty extracted text)
    """
    paper_id = str(uuid.uuid4())
    file_bytes = await file.read()

    # ---- Validate & parse ----
    try:
        parsed = validate_and_parse_pdf(file_bytes, file.filename or "upload.pdf", paper_id)
    except PDFValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

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

    # ---- Store paper ----
    supabase = get_supabase()
    supabase.table("papers").insert({
        "id": paper_id,
        "filename": file.filename or "upload.pdf",
        "storage_path": parsed["filepath"],
        "page_count": parsed["page_count"],
    }).execute()

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
    """Serve the raw PDF file for the Document Viewer."""
    filepath = UPLOAD_DIR / f"{paper_id}.pdf"
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(
        str(filepath),
        media_type="application/pdf",
        filename=f"{paper_id}.pdf",
        content_disposition_type="inline",
    )
