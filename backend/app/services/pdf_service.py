"""
PDF parsing, validation, and chunking service.
Uses PyMuPDF (fitz) for extraction. OCR is out of scope for Phase 1.
"""

import os
import logging
from pathlib import Path

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
    Validate, save, and parse a PDF.

    Returns
    -------
    dict with keys: pages, page_count, filepath
        pages — list of (1-indexed page_number, extracted_text) tuples
        page_count — total pages
        filepath — absolute path to the saved file

    Raises
    ------
    PDFValidationError
        If the file exceeds size/page limits or appears to be scanned.
    """
    # ---- size gate ----
    if len(file_bytes) > MAX_PDF_SIZE_BYTES:
        size_mb = len(file_bytes) / (1024 * 1024)
        limit_mb = MAX_PDF_SIZE_BYTES / (1024 * 1024)
        raise PDFValidationError(
            f"PDF is {size_mb:.1f} MB which exceeds the {limit_mb:.0f} MB limit."
        )

    # ---- open & page-count gate ----
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:
        raise PDFValidationError(f"Unable to open PDF: {exc}") from exc

    if doc.page_count > MAX_PDF_PAGES:
        raise PDFValidationError(
            f"PDF has {doc.page_count} pages, which exceeds the "
            f"{MAX_PDF_PAGES}-page limit."
        )

    # ---- extract text & emptiness gate ----
    pages: list[tuple[int, str]] = []
    full_text_len = 0
    for idx in range(doc.page_count):
        text = doc[idx].get_text()
        pages.append((idx + 1, text))  # 1-indexed
        full_text_len += len(text.strip())

    if full_text_len < MIN_EXTRACTED_TEXT_LENGTH:
        raise PDFValidationError(
            "This PDF appears to be scanned or image-only — the extracted "
            "text is empty or near-empty. Please upload a text-based PDF. "
            "(OCR is not supported.)"
        )

    # ---- persist file to Supabase Storage ----
    storage_path = f"{paper_id}.pdf"
    supabase = get_supabase()
    
    supabase.storage.from_("papers").upload(
        file=file_bytes,
        path=storage_path,
        file_options={"content-type": "application/pdf"}
    )
    logger.info("Saved PDF %s (%d pages) to Supabase storage → %s", filename, doc.page_count, storage_path)

    return {
        "pages": pages,
        "page_count": doc.page_count,
        "filepath": storage_path,
    }


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
    # Flatten pages into a list of (page_number, paragraph_text) pairs.
    paragraphs: list[tuple[int, str]] = []
    for page_num, text in pages:
        for para in text.split("\n\n"):
            para = para.strip()
            # Skip tiny fragments (headers, footers, page numbers, etc.)
            if para and len(para.split()) > 3:
                paragraphs.append((page_num, para))

    if not paragraphs:
        return []

    chunks: list[dict] = []
    current_words: list[str] = []
    current_page: int = paragraphs[0][0]
    chunk_index = 0

    for page_num, para in paragraphs:
        words = para.split()
        current_words.extend(words)

        if len(current_words) >= CHUNK_SIZE_WORDS:
            chunks.append({
                "text": " ".join(current_words),
                "page_number": current_page,
                "chunk_index": chunk_index,
            })
            chunk_index += 1

            # Carry over the tail as overlap for the next chunk.
            current_words = list(current_words[-CHUNK_OVERLAP_WORDS:])
            current_page = page_num

    # Flush any remaining words as a final chunk.
    if current_words:
        chunks.append({
            "text": " ".join(current_words),
            "page_number": current_page,
            "chunk_index": chunk_index,
        })

    logger.info("Chunked %d pages → %d chunks", len(pages), len(chunks))
    return chunks
