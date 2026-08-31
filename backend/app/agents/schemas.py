"""Strict schemas for every LLM-authored audit artifact.

Keeping these contracts separate from the HTTP models makes it possible to
reject syntactically-valid but structurally unsafe model responses before they
can enter graph state or be persisted.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)


NonEmptyText = Annotated[StrictStr, Field(min_length=1)]


def _canonical_chunk_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("chunk IDs must be valid UUID strings") from exc


ChunkId = Annotated[
    StrictStr,
    Field(min_length=36, max_length=36),
    AfterValidator(_canonical_chunk_id),
]


class StrictAgentModel(BaseModel):
    """Base contract shared by all agent outputs."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class ExternalCitation(StrictAgentModel):
    title: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    authors: Annotated[list[NonEmptyText], Field(max_length=100)] = Field(default_factory=list)
    year: Annotated[StrictInt, Field(ge=1600, le=2100)] | None = None
    url: Annotated[StrictStr, Field(max_length=2000)] = ""
    source: Literal["Semantic Scholar", "arXiv", "OpenAlex"]


class AttackerOutput(StrictAgentModel):
    claim_summary: Annotated[StrictStr, Field(min_length=1, max_length=1000)]
    critique_text: Annotated[StrictStr, Field(min_length=1, max_length=12000)]
    cited_chunk_ids: Annotated[list[ChunkId], Field(max_length=20)]
    external_citations: Annotated[list[ExternalCitation], Field(max_length=20)]
    cited_reference_id: Annotated[StrictStr, Field(min_length=1, max_length=100)] | None = None
    critique_type: Literal[
        "omission",
        "inconsistency",
        "unstated_assumption",
        "dataset_limitation",
        "citation_integrity",
        "missing_baseline",
    ]

    @model_validator(mode="after")
    def evidence_matches_critique_type(self) -> "AttackerOutput":
        if self.critique_type == "omission" and self.cited_chunk_ids:
            raise ValueError("omission critiques must leave cited_chunk_ids empty")

        if self.critique_type == "citation_integrity":
            if self.cited_reference_id is None:
                raise ValueError(
                    "citation_integrity critiques must provide cited_reference_id"
                )
            if self.external_citations:
                raise ValueError(
                    "citation_integrity metadata is resolved from the stored "
                    "reference list; external_citations must be empty"
                )
        elif self.critique_type == "missing_baseline":
            if self.cited_reference_id is not None:
                raise ValueError(
                    "missing_baseline critiques must not provide cited_reference_id"
                )
            if not self.external_citations:
                raise ValueError(
                    "missing_baseline critiques must cite a supplied external candidate"
                )
        else:
            if self.cited_reference_id is not None:
                raise ValueError(
                    "only citation_integrity critiques may provide cited_reference_id"
                )
            if self.external_citations:
                raise ValueError(
                    "external citations are only valid for missing_baseline critiques"
                )
        return self


class DefenderOutput(StrictAgentModel):
    rebuttal_text: Annotated[StrictStr, Field(min_length=1, max_length=12000)]
    cited_chunk_ids: Annotated[list[ChunkId], Field(max_length=20)]
    concedes: StrictBool

    @model_validator(mode="after")
    def evidence_matches_position(self) -> "DefenderOutput":
        if self.concedes and self.cited_chunk_ids:
            raise ValueError("a concession must leave cited_chunk_ids empty")
        if not self.concedes and not self.cited_chunk_ids:
            raise ValueError("a non-conceding defense must cite paper evidence")
        return self


class RefereeOutput(StrictAgentModel):
    verdict: Literal["SOLIDIFIED", "ACTIONABLE_FLAW", "CONTESTED"]
    confidence: Annotated[StrictFloat, Field(ge=0.0, le=1.0)]
    rationale: Annotated[StrictStr, Field(min_length=1, max_length=12000)]


class DebriefOutput(StrictAgentModel):
    executive_synthesis: Annotated[StrictStr, Field(min_length=1, max_length=20000)]
    solidified_strengths: Annotated[list[NonEmptyText], Field(max_length=100)]
    actionable_weaknesses: Annotated[list[NonEmptyText], Field(max_length=100)]
    contested_points: Annotated[list[NonEmptyText], Field(max_length=100)]


class AuthorFinalReportOutput(StrictAgentModel):
    overall_assessment: Annotated[StrictStr, Field(min_length=1, max_length=20000)]
    preserved_strengths: Annotated[list[NonEmptyText], Field(max_length=100)]
    priority_revisions: Annotated[list[NonEmptyText], Field(max_length=100)]
    open_judgment_calls: Annotated[list[NonEmptyText], Field(max_length=100)]
    revision_plan: Annotated[list[NonEmptyText], Field(max_length=100)]


class ReviewerFinalReportOutput(StrictAgentModel):
    strengths: Annotated[list[NonEmptyText], Field(max_length=100)]
    weaknesses: Annotated[list[NonEmptyText], Field(max_length=100)]
    questions_for_authors: Annotated[list[NonEmptyText], Field(max_length=100)]
    recommendation: Annotated[StrictStr, Field(min_length=1, max_length=12000)]


class VersionDiffOutput(StrictAgentModel):
    resolved_issues: Annotated[list[NonEmptyText], Field(max_length=100)]
    still_open_issues: Annotated[list[NonEmptyText], Field(max_length=100)]
    new_issues: Annotated[list[NonEmptyText], Field(max_length=100)]
    summary: Annotated[StrictStr, Field(min_length=1, max_length=12000)]
