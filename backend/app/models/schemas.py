"""
Pydantic models for API requests and responses.
These are the contracts between frontend ↔ backend.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator

from app.constants import DEFAULT_TOPICS_BY_DEPTH, DEPTH_TOPIC_LIMITS, ROUND_TOPICS


Domain = Literal["ml_cs", "life_sciences", "social_science", "other"]
DomainSelection = Literal["auto", "ml_cs", "life_sciences", "social_science", "other"]
StrictnessLevel = Literal["constructive", "standard", "brutal"]
AuditDepth = Literal["fast", "deep", "exhaustive"]
AuditMode = Literal["author", "reviewer_assist"]


# ---------------------------------------------------------------------------
# Papers
# ---------------------------------------------------------------------------
class PaperUploadResponse(BaseModel):
    paper_id: str
    filename: str
    page_count: int
    chunk_count: int
    detected_domain: Domain = "other"
    parent_paper_id: Optional[str] = None
    version_number: int = 1


class PaperSummaryResponse(BaseModel):
    id: str
    filename: str
    page_count: Optional[int] = None
    detected_domain: Domain = "other"
    parent_paper_id: Optional[str] = None
    version_number: int = 1
    uploaded_at: str = ""


class PdfUrlResponse(BaseModel):
    url: str
    expires_in: int = 300


class DocumentRelevanceResult(BaseModel):
    """Strict output contract for the research-document classifier."""

    is_research_paper: StrictBool
    reason: str = Field(min_length=1, max_length=1000)
    detected_domain: Domain


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
    # ``round_topic`` remains as the Phase 1/2 compatibility input. New clients
    # send ``round_topics``; rounds.topic is authoritative once rows are created.
    round_topic: Optional[str] = None
    round_topics: list[str] = Field(default_factory=list, max_length=6)
    strictness_level: StrictnessLevel = "standard"
    depth: AuditDepth = "fast"
    mode: AuditMode = "author"
    domain: DomainSelection = "auto"
    compare_to_audit_id: Optional[str] = None

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

    @field_validator("compare_to_audit_id")
    @classmethod
    def comparison_id_must_be_uuid(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        import uuid

        try:
            parsed = uuid.UUID(str(value))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("compare_to_audit_id must be a valid UUID") from exc
        if parsed.int == 0:
            raise ValueError("compare_to_audit_id must not be the nil UUID")
        return str(parsed)

    @model_validator(mode="after")
    def validate_topic_plan(self) -> "AuditCreateRequest":
        topics = list(self.round_topics)
        if not topics and self.round_topic:
            topics = [self.round_topic]
        elif not topics:
            topics = list(DEFAULT_TOPICS_BY_DEPTH[self.depth])

        if len(topics) != len(set(topics)):
            raise ValueError("round_topics must not contain duplicates")
        invalid = [topic for topic in topics if topic not in ROUND_TOPICS]
        if invalid:
            raise ValueError(
                f"invalid round topic(s): {invalid}; must use known topic slugs"
            )
        minimum, maximum = DEPTH_TOPIC_LIMITS[self.depth]
        if not minimum <= len(topics) <= maximum:
            raise ValueError(
                f"{self.depth} depth requires {minimum}-{maximum} round topics"
            )
        self.round_topics = topics
        # The plural Phase 3 plan is authoritative. Keep the legacy singular
        # alias deterministic even if a transitional client sends both fields.
        self.round_topic = topics[0]
        return self


class AuditCreateResponse(BaseModel):
    audit_id: str
    round_id: str
    status: str
    round_ids: list[str] = Field(default_factory=list)
    round_topics: list[str] = Field(default_factory=list)
    strictness_level: StrictnessLevel = "standard"
    depth: AuditDepth = "fast"
    mode: AuditMode = "author"
    domain: Domain = "other"


class AuditStatusResponse(BaseModel):
    audit_id: str
    status: str
    round_topic: str
    created_at: str
    round_topics: list[str] = Field(default_factory=list)
    strictness_level: StrictnessLevel = "standard"
    depth: AuditDepth = "fast"
    mode: AuditMode = "author"
    domain: Domain = "other"


class AuditSummaryResponse(AuditStatusResponse):
    paper_id: str
    filename: Optional[str] = None


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
    round_id: Optional[str] = None
    round_number: Optional[int] = None
    round_topic: Optional[str] = None


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
    round_id: Optional[str] = None
    round_number: Optional[int] = None
    round_topic: Optional[str] = None


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
    round_id: Optional[str] = None
    round_number: Optional[int] = None
    round_topic: Optional[str] = None


class FinalReportResponse(BaseModel):
    id: str
    audit_id: str
    mode: AuditMode
    content: str
    created_at: str = ""


class VersionDiffResponse(BaseModel):
    id: str
    audit_id_old: str
    audit_id_new: str
    round_topic: str
    diff_summary: str
    created_at: str = ""



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
