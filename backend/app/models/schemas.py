"""
Pydantic models for API requests and responses.
These are the contracts between frontend ↔ backend.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, StrictBool, field_validator


# ---------------------------------------------------------------------------
# Papers
# ---------------------------------------------------------------------------
class PaperUploadResponse(BaseModel):
    paper_id: str
    filename: str
    page_count: int
    chunk_count: int


class DocumentRelevanceResult(BaseModel):
    """Strict output contract for the research-document classifier."""

    is_research_paper: StrictBool
    reason: str = Field(min_length=1, max_length=1000)


class RelevanceRejectionDetail(BaseModel):
    """Machine-readable response used by the upload override popup."""

    code: Literal["document_not_research"] = "document_not_research"
    message: str
    reason: str
    relevance_failed: Literal[True] = True
    override_allowed: Literal[True] = True
    retryable: Literal[False] = False


# ---------------------------------------------------------------------------
# Audits
# ---------------------------------------------------------------------------
class AuditCreateRequest(BaseModel):
    paper_id: str
    round_topic: str  # must be one of the topic slugs

    @field_validator("paper_id")
    @classmethod
    def paper_id_must_be_uuid(cls, value: str) -> str:
        import uuid

        try:
            parsed = uuid.UUID(str(value))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("paper_id must be a valid UUID") from exc
        if parsed.int == 0:
            raise ValueError("paper_id must not be the nil UUID")
        return str(parsed)


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
    reproducibility_checklist: Optional[dict] = None



# ---------------------------------------------------------------------------
# Polling fallback — bundles turns + verdicts
# ---------------------------------------------------------------------------
class TurnsListResponse(BaseModel):
    turns: list[TurnResponse]
    verdicts: list[VerdictResponse]
    status: str  # audit status
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------
class ErrorResponse(BaseModel):
    detail: str | dict[str, Any]
