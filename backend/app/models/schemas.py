"""
Pydantic models for API requests and responses.
These are the contracts between frontend ↔ backend.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Papers
# ---------------------------------------------------------------------------
class PaperUploadResponse(BaseModel):
    paper_id: str
    filename: str
    page_count: int
    chunk_count: int


# ---------------------------------------------------------------------------
# Audits
# ---------------------------------------------------------------------------
class AuditCreateRequest(BaseModel):
    paper_id: str
    round_topic: str  # must be one of the topic slugs


class AuditCreateResponse(BaseModel):
    audit_id: str
    round_id: str
    status: str


class AuditStatusResponse(BaseModel):
    audit_id: str
    status: str
    round_topic: str
    created_at: str


# ---------------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------------
class TurnResponse(BaseModel):
    id: str
    exchange_number: int
    agent_type: str
    sequence: int
    content: dict
    created_at: str


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------
class VerdictResponse(BaseModel):
    id: str
    exchange_number: int
    claim_summary: Optional[str] = None
    verdict_type: str
    confidence: Optional[float] = None
    rationale: Optional[str] = None
    cited_chunk_ids: Optional[list] = None


# ---------------------------------------------------------------------------
# Debrief Card
# ---------------------------------------------------------------------------
class DebriefCardResponse(BaseModel):
    id: str
    executive_synthesis: Optional[str] = None
    solidified_strengths: Optional[list] = None
    actionable_weaknesses: Optional[list] = None
    contested_points: Optional[list] = None


# ---------------------------------------------------------------------------
# Polling fallback — bundles turns + verdicts
# ---------------------------------------------------------------------------
class TurnsListResponse(BaseModel):
    turns: list[TurnResponse]
    verdicts: list[VerdictResponse]
    status: str  # audit status


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------
class ErrorResponse(BaseModel):
    detail: str
