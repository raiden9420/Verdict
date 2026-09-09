"""PDF validation, text extraction, storage, and deterministic chunking."""

import logging
import re

import pymupdf as fitz  # PyMuPDF

from app.constants import (
    MAX_PDF_PAGES,
    MAX_PDF_SIZE_BYTES,
    MIN_EXTRACTED_TEXT_LENGTH,
    CHUNK_SIZE_WORDS,
    CHUNK_OVERLAP_WORDS,
)
from app.database import get_supabase

logger = logging.getLogger(__name__)


class PDFValidationError(Exception):
    """Raised when an uploaded PDF fails a validation gate."""
    pass


# ---------------------------------------------------------------------------
# Validation + parsing
# ---------------------------------------------------------------------------

def validate_and_parse_pdf(
    file_bytes: bytes,
    filename: str,
    paper_id: str,
) -> dict:
    """
    Validate and parse a PDF without persisting it.

    Storage is intentionally a separate operation: callers must complete the
    research relevance gate and all expensive preprocessing before invoking
    :func:`upload_pdf`.  A rejected document therefore leaves no stored object.

    Returns
    -------
    dict with keys: pages, page_count, storage_path
        pages — list of (1-indexed page_number, extracted_text) tuples
        page_count — total pages
        storage_path — the eventual object-storage key (not uploaded yet)

    Raises
    ------
    PDFValidationError
        If the file exceeds size/page limits or appears to be scanned.
    """
    if not file_bytes:
        raise PDFValidationError("The uploaded file is empty.")

    # ---- size gate ----
    if len(file_bytes) > MAX_PDF_SIZE_BYTES:
        size_mb = len(file_bytes) / (1024 * 1024)
        limit_mb = MAX_PDF_SIZE_BYTES / (1024 * 1024)
        raise PDFValidationError(
            f"PDF is {size_mb:.1f} MB which exceeds the {limit_mb:.0f} MB limit."
        )

    # A PDF header may legally be preceded by a small binary preamble, hence
    # the bounded search instead of requiring byte zero to be ``%PDF-``.
    if b"%PDF-" not in file_bytes[:1024]:
        raise PDFValidationError("The uploaded file is not a valid PDF document.")

    # ---- open & page-count gate ----
    doc = None
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:
        logger.info("Rejected malformed PDF for paper %s (%s)", paper_id, type(exc).__name__)
        raise PDFValidationError("Unable to open this PDF because it is malformed.") from exc

    try:
        if getattr(doc, "needs_pass", False):
            raise PDFValidationError(
                "This PDF is password-protected or encrypted. Please upload an unlocked PDF."
            )

        try:
            page_count = int(doc.page_count)
        except Exception as exc:
            raise PDFValidationError("Unable to read the PDF page count.") from exc

        if page_count <= 0:
            raise PDFValidationError("The PDF does not contain any pages.")
        if page_count > MAX_PDF_PAGES:
            raise PDFValidationError(
                f"PDF has {page_count} pages, which exceeds the "
                f"{MAX_PDF_PAGES}-page limit."
            )

        # ---- extract text & emptiness gate ----
        pages: list[tuple[int, str]] = []
        full_text_len = 0
        for idx in range(page_count):
            try:
                text = doc[idx].get_text("text") or ""
            except Exception as exc:
                raise PDFValidationError(
                    f"Unable to extract text from page {idx + 1}; the PDF may be malformed."
                ) from exc
            pages.append((idx + 1, text))  # 1-indexed
            full_text_len += len(text.strip())

        if full_text_len < MIN_EXTRACTED_TEXT_LENGTH:
            raise PDFValidationError(
                "This PDF appears to be scanned or image-only — the extracted "
                "text is empty or near-empty. Please upload a text-based PDF. "
                "(OCR is not supported.)"
            )
    finally:
        doc.close()

    return {
        "pages": pages,
        "page_count": page_count,
        "storage_path": f"{paper_id}.pdf",
    }


def upload_pdf(file_bytes: bytes, storage_path: str) -> None:
    """Persist an already accepted PDF to object storage."""
    get_supabase().storage.from_("papers").upload(
        file=file_bytes,
        path=storage_path,
        file_options={"content-type": "application/pdf", "upsert": "false"},
    )
    logger.info("Stored accepted PDF at %s", storage_path)


def delete_pdf(storage_path: str) -> None:
    """Best-effort-compatible object deletion used by ingestion rollback."""
    get_supabase().storage.from_("papers").remove([storage_path])


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_pages(pages: list[tuple[int, str]]) -> list[dict]:
    """
    Split page text into ~300–500 word chunks with ~50-word overlap,
    splitting on paragraph boundaries where possible.

    Parameters
    ----------
    pages : list of (page_number, text) tuples (1-indexed page numbers)

    Returns
    -------
    list of dicts with keys: text, page_number, chunk_index
    """
    if CHUNK_SIZE_WORDS <= 0 or CHUNK_OVERLAP_WORDS < 0:
        raise ValueError("Chunk size and overlap must be non-negative")
    if CHUNK_OVERLAP_WORDS >= CHUNK_SIZE_WORDS:
        raise ValueError("Chunk overlap must be smaller than chunk size")

    # Flatten paragraph words while retaining the source page of every token.
    tokens: list[tuple[str, int]] = []
    for page_num, text in pages:
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            # Skip tiny fragments (headers, footers, page numbers, etc.)
            words = para.split()
            if len(words) > 3:
                tokens.extend((word, page_num) for word in words)

    if not tokens:
        return []

    chunks: list[dict] = []
    start = 0
    chunk_index = 0
    total = len(tokens)
    while start < total:
        end = min(start + CHUNK_SIZE_WORDS, total)
        window = tokens[start:end]
        chunks.append({
            "text": " ".join(word for word, _ in window),
            "page_number": window[0][1],
            "chunk_index": chunk_index,
        })
        chunk_index += 1

        if end == total:
            break
        start = end - CHUNK_OVERLAP_WORDS

    logger.info("Chunked %d pages → %d chunks", len(pages), len(chunks))
    return chunks
