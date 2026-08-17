"""Focused tests for Phase 3 paper reports and version comparisons."""

from __future__ import annotations

import copy
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import report_service


class QueryStub:
    """Small in-memory subset of the Supabase/PostgREST query API."""

    def __init__(self, database: "SupabaseStub", table_name: str) -> None:
        self.database = database
        self.table_name = table_name
        self.filters: list[tuple[str, str, object]] = []
        self.ordering: tuple[str, bool] | None = None
        self.row_limit: int | None = None
        self.upsert_row: dict | None = None
        self.conflict_fields: list[str] = []

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field: str, value: object):
        self.filters.append(("eq", field, value))
        return self

    def in_(self, field: str, values: list[object]):
        self.filters.append(("in", field, list(values)))
        return self

    def order(self, field: str, desc: bool = False):
        self.ordering = (field, desc)
        return self

    def limit(self, value: int):
        self.row_limit = value
        return self

    def upsert(self, row: dict, on_conflict: str):
        self.upsert_row = dict(row)
        self.conflict_fields = [field.strip() for field in on_conflict.split(",")]
        self.database.upsert_conflicts.append((self.table_name, on_conflict))
        return self

    def _matches(self, row: dict) -> bool:
        for operation, field, expected in self.filters:
            if operation == "eq" and row.get(field) != expected:
                return False
            if operation == "in" and row.get(field) not in expected:
                return False
        return True

    def execute(self):
        rows = self.database.tables.setdefault(self.table_name, [])
        if self.upsert_row is not None:
            existing = next(
                (
                    row
                    for row in rows
                    if all(
                        row.get(field) == self.upsert_row.get(field)
                        for field in self.conflict_fields
                    )
                ),
                None,
            )
            if existing is None:
                existing = {
                    "id": str(uuid.uuid4()),
                    "created_at": "2026-08-12T00:00:00Z",
                    **self.upsert_row,
                }
                rows.append(existing)
            else:
                existing.update(self.upsert_row)
            return SimpleNamespace(data=[copy.deepcopy(existing)])

        result = [copy.deepcopy(row) for row in rows if self._matches(row)]
        if self.ordering is not None:
            field, descending = self.ordering
            result.sort(key=lambda row: row.get(field) or 0, reverse=descending)
        if self.row_limit is not None:
            result = result[: self.row_limit]
        return SimpleNamespace(data=result)


class SupabaseStub:
    def __init__(self, tables: dict[str, list[dict]]) -> None:
        self.tables = copy.deepcopy(tables)
        self.upsert_conflicts: list[tuple[str, str]] = []

    def table(self, name: str) -> QueryStub:
        return QueryStub(self, name)


class ReportRenderingTests(unittest.TestCase):
    def test_author_renderer_uses_coaching_sections_not_review_template(self) -> None:
        markdown = report_service.render_final_report_markdown(
            "author",
            {
                "overall_assessment": "The core idea is promising but needs revision.",
                "preserved_strengths": ["The theorem is clearly motivated."],
                "priority_revisions": ["Add a powered experiment."],
                "open_judgment_calls": ["The scope remains a venue judgment."],
                "revision_plan": ["Prioritize the experiment before reframing."],
            },
        )

        headings = [
            line.removeprefix("## ")
            for line in markdown.splitlines()
            if line.startswith("## ")
        ]
        self.assertEqual(
            headings,
            [
                "Overall Assessment",
                "Preserved Strengths",
                "Priority Revisions",
                "Open Judgment Calls",
                "Revision Plan",
            ],
        )
        self.assertNotIn("## Questions for Authors", markdown)

    def test_reviewer_renderer_uses_exact_required_headings(self) -> None:
        markdown = report_service.render_final_report_markdown(
            "reviewer_assist",
            {
                "strengths": ["Clear motivation."],
                "weaknesses": ["The sample is underpowered."],
                "questions_for_authors": ["Was the analysis pre-registered?"],
                "recommendation": "Major revision pending a powered analysis.",
            },
        )

        headings = [
            line.removeprefix("## ")
            for line in markdown.splitlines()
            if line.startswith("## ")
        ]
        self.assertEqual(
            headings,
            ["Strengths", "Weaknesses", "Questions for Authors", "Recommendation"],
        )

    def test_version_renderer_uses_stable_comparison_headings(self) -> None:
        markdown = report_service.render_version_diff_markdown(
            "statistical_rigor",
            {
                "resolved_issues": ["Power analysis was added."],
                "still_open_issues": [],
                "new_issues": ["A new subgroup test lacks correction."],
                "summary": "The revision improves power reporting.",
            },
        )
        headings = [line for line in markdown.splitlines() if line.startswith("## ")]
        self.assertEqual(
            headings,
            [
                "## Resolved Issues",
                "## Still Open",
                "## New Issues",
                "## Summary",
            ],
        )


class FinalReportServiceTests(unittest.TestCase):
    def test_author_mode_selects_author_schema_and_coaching_output(self) -> None:
        audit_id = "audit-author"
        database = SupabaseStub(
            {
                "audits": [
                    {
                        "id": audit_id,
                        "paper_id": "paper-author",
                        "mode": "author",
                        "status": "in_progress",
                    }
                ],
                "rounds": [
                    {
                        "id": "round-author",
                        "audit_id": audit_id,
                        "round_number": 1,
                        "topic": "theoretical_soundness",
                        "status": "completed",
                    }
                ],
                "debrief_cards": [
                    {
                        "id": "card-author",
                        "round_id": "round-author",
                        "executive_synthesis": "A sound idea needs one clarification.",
                        "solidified_strengths": ["Motivation is clear."],
                        "actionable_weaknesses": ["Clarify the boundary case."],
                        "contested_points": [],
                    }
                ],
                "final_reports": [],
            }
        )
        generated = (
            {
                "overall_assessment": "Promising with a focused revision.",
                "preserved_strengths": ["Keep the clear motivation."],
                "priority_revisions": ["Clarify the boundary case."],
                "open_judgment_calls": [],
                "revision_plan": ["Revise the proof before expanding scope."],
            },
            Mock(),
            "test-provider",
        )

        with (
            patch.object(report_service, "get_supabase", return_value=database),
            patch.object(report_service, "get_llm_client", return_value=Mock()),
            patch.object(
                report_service,
                "generate_structured_with_meta",
                return_value=generated,
            ) as generate,
        ):
            report = report_service.generate_final_report(audit_id)

        self.assertIs(
            generate.call_args.args[3],
            report_service.AuthorFinalReportOutput,
        )
        self.assertIn("paper's authors", generate.call_args.args[1])
        self.assertIn("## Revision Plan", report["content"])
        self.assertNotIn("## Recommendation", report["content"])

    def test_report_is_blocked_until_every_round_completes(self) -> None:
        audit_id = "audit-new"
        database = SupabaseStub(
            {
                "audits": [
                    {
                        "id": audit_id,
                        "paper_id": "paper-new",
                        "mode": "author",
                        "status": "in_progress",
                    }
                ],
                "rounds": [
                    {
                        "id": "round-1",
                        "audit_id": audit_id,
                        "round_number": 1,
                        "topic": "theoretical_soundness",
                        "status": "completed",
                    },
                    {
                        "id": "round-2",
                        "audit_id": audit_id,
                        "round_number": 2,
                        "topic": "experimental_setup",
                        "status": "in_progress",
                    },
                ],
                "debrief_cards": [],
                "final_reports": [],
            }
        )

        with (
            patch.object(report_service, "get_supabase", return_value=database),
            patch.object(report_service, "generate_structured_with_meta") as generate,
        ):
            with self.assertRaises(report_service.FinalReportNotReadyError):
                report_service.generate_final_report(audit_id)

        generate.assert_not_called()
        self.assertEqual(database.tables["final_reports"], [])

    def test_report_is_blocked_when_any_completed_round_lacks_a_debrief(self) -> None:
        audit_id = "audit-missing-card"
        database = SupabaseStub(
            {
                "audits": [
                    {
                        "id": audit_id,
                        "paper_id": "paper-new",
                        "mode": "author",
                        "status": "in_progress",
                    }
                ],
                "rounds": [
                    {
                        "id": "round-1",
                        "audit_id": audit_id,
                        "round_number": 1,
                        "topic": "theoretical_soundness",
                        "status": "completed",
                    },
                    {
                        "id": "round-2",
                        "audit_id": audit_id,
                        "round_number": 2,
                        "topic": "experimental_setup",
                        "status": "completed",
                    },
                ],
                "debrief_cards": [
                    {
                        "id": "card-1",
                        "round_id": "round-1",
                        "executive_synthesis": "First only.",
                        "solidified_strengths": [],
                        "actionable_weaknesses": [],
                        "contested_points": [],
                    }
                ],
                "final_reports": [],
            }
        )

        with (
            patch.object(report_service, "get_supabase", return_value=database),
            patch.object(report_service, "generate_structured_with_meta") as generate,
            self.assertRaises(report_service.FinalReportNotReadyError),
        ):
            report_service.generate_final_report(audit_id)

        generate.assert_not_called()
        self.assertEqual(database.tables["final_reports"], [])

    def test_report_uses_round_order_and_is_synthesized_only_once(self) -> None:
        audit_id = "audit-new"
        database = SupabaseStub(
            {
                "audits": [
                    {
                        "id": audit_id,
                        "paper_id": "paper-new",
                        "mode": "reviewer_assist",
                        "status": "in_progress",
                    }
                ],
                # Deliberately reverse storage order; the prompt must use round order.
                "rounds": [
                    {
                        "id": "round-2",
                        "audit_id": audit_id,
                        "round_number": 2,
                        "topic": "experimental_setup",
                        "status": "completed",
                    },
                    {
                        "id": "round-1",
                        "audit_id": audit_id,
                        "round_number": 1,
                        "topic": "theoretical_soundness",
                        "status": "completed",
                    },
                ],
                "debrief_cards": [
                    {
                        "id": "card-2",
                        "round_id": "round-2",
                        "executive_synthesis": "Second round.",
                        "solidified_strengths": [],
                        "actionable_weaknesses": ["Weak experiment."],
                        "contested_points": [],
                    },
                    {
                        "id": "card-1",
                        "round_id": "round-1",
                        "executive_synthesis": "First round.",
                        "solidified_strengths": ["Sound proof."],
                        "actionable_weaknesses": [],
                        "contested_points": [],
                    },
                ],
                "final_reports": [],
            }
        )
        generated = (
            {
                "strengths": ["Sound proof."],
                "weaknesses": ["Weak experiment."],
                "questions_for_authors": ["Can the experiment be expanded?"],
                "recommendation": "Major revision.",
            },
            Mock(),
            "test-provider",
        )

        with (
            patch.object(report_service, "get_supabase", return_value=database),
            patch.object(report_service, "get_llm_client", return_value=Mock()),
            patch.object(
                report_service,
                "generate_structured_with_meta",
                return_value=generated,
            ) as generate,
        ):
            first = report_service.generate_final_report(audit_id)
            second = report_service.generate_final_report(audit_id)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(generate.call_count, 1)
        prompt = generate.call_args.args[2]
        self.assertLess(prompt.index("First round."), prompt.index("Second round."))
        self.assertIn("## Questions for Authors", first["content"])
        self.assertEqual(len(database.tables["final_reports"]), 1)
        self.assertIn(("final_reports", "audit_id"), database.upsert_conflicts)


class VersionDiffServiceTests(unittest.TestCase):
    def test_unrelated_paper_families_cannot_be_compared(self) -> None:
        database = SupabaseStub(
            {
                "audits": [
                    {
                        "id": "audit-old",
                        "paper_id": "paper-old",
                        "status": "completed",
                    },
                    {
                        "id": "audit-new",
                        "paper_id": "paper-new",
                        "status": "completed",
                    },
                ],
                "papers": [
                    {
                        "id": "paper-old",
                        "parent_paper_id": None,
                        "version_number": 1,
                    },
                    {
                        "id": "paper-new",
                        "parent_paper_id": None,
                        "version_number": 1,
                    },
                ],
                "version_diffs": [],
            }
        )

        with (
            patch.object(report_service, "get_supabase", return_value=database),
            patch.object(report_service, "get_llm_client") as llm,
            self.assertRaises(report_service.VersionComparisonError),
        ):
            report_service.generate_version_diffs("audit-old", "audit-new")

        llm.assert_not_called()
        self.assertEqual(database.tables["version_diffs"], [])

    def test_previous_audit_lookup_uses_nearest_audited_family_version(self) -> None:
        database = SupabaseStub(
            {
                "audits": [
                    {
                        "id": "audit-v1",
                        "paper_id": "paper-v1",
                        "mode": "author",
                        "status": "completed",
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                    {
                        "id": "audit-v2",
                        "paper_id": "paper-v2",
                        "mode": "author",
                        "status": "completed",
                        "created_at": "2026-02-01T00:00:00Z",
                    },
                    {
                        "id": "audit-v3",
                        "paper_id": "paper-v3",
                        "mode": "author",
                        "status": "in_progress",
                    },
                ],
                "papers": [
                    {
                        "id": "paper-v1",
                        "parent_paper_id": None,
                        "version_number": 1,
                    },
                    {
                        "id": "paper-v2",
                        "parent_paper_id": "paper-v1",
                        "version_number": 2,
                    },
                    {
                        "id": "paper-v3",
                        "parent_paper_id": "paper-v1",
                        "version_number": 3,
                    },
                ],
            }
        )

        with patch.object(report_service, "get_supabase", return_value=database):
            previous = report_service.find_previous_audit_id("audit-v3")

        self.assertEqual(previous, "audit-v2")

    def test_previous_lookup_skips_newer_disjoint_audit_for_older_overlap(self) -> None:
        database = SupabaseStub(
            {
                "audits": [
                    {
                        "id": "audit-v2-overlap",
                        "paper_id": "paper-v2",
                        "status": "completed",
                        "created_at": "2026-02-01T00:00:00Z",
                    },
                    {
                        "id": "audit-v2-disjoint-newest",
                        "paper_id": "paper-v2",
                        "status": "completed",
                        "created_at": "2026-03-01T00:00:00Z",
                    },
                    {
                        "id": "audit-v3-new",
                        "paper_id": "paper-v3",
                        "status": "in_progress",
                        "created_at": "2026-04-01T00:00:00Z",
                    },
                ],
                "papers": [
                    {
                        "id": "paper-v1",
                        "parent_paper_id": None,
                        "version_number": 1,
                    },
                    {
                        "id": "paper-v2",
                        "parent_paper_id": "paper-v1",
                        "version_number": 2,
                    },
                    {
                        "id": "paper-v3",
                        "parent_paper_id": "paper-v1",
                        "version_number": 3,
                    },
                ],
                "rounds": [
                    {
                        "id": "round-v2-overlap",
                        "audit_id": "audit-v2-overlap",
                        "round_number": 1,
                        "topic": "theoretical_soundness",
                        "status": "completed",
                    },
                    {
                        "id": "round-v2-disjoint",
                        "audit_id": "audit-v2-disjoint-newest",
                        "round_number": 1,
                        "topic": "experimental_setup",
                        "status": "completed",
                    },
                    {
                        "id": "round-v3-new",
                        "audit_id": "audit-v3-new",
                        "round_number": 1,
                        "topic": "theoretical_soundness",
                        "status": "completed",
                    },
                ],
            }
        )

        with patch.object(report_service, "get_supabase", return_value=database):
            previous = report_service.find_previous_audit_id("audit-v3-new")

        self.assertEqual(previous, "audit-v2-overlap")

    def test_only_matching_topics_are_compared_and_storage_is_idempotent(self) -> None:
        old_audit_id = "audit-old"
        new_audit_id = "audit-new"
        database = SupabaseStub(
            {
                "audits": [
                    {
                        "id": old_audit_id,
                        "paper_id": "paper-old",
                        "mode": "author",
                        "status": "completed",
                    },
                    {
                        "id": new_audit_id,
                        "paper_id": "paper-new",
                        "mode": "author",
                        "status": "in_progress",
                    },
                ],
                "papers": [
                    {
                        "id": "paper-old",
                        "parent_paper_id": None,
                        "version_number": 1,
                    },
                    {
                        "id": "paper-new",
                        "parent_paper_id": "paper-old",
                        "version_number": 2,
                    },
                ],
                "rounds": [
                    {
                        "id": "old-shared",
                        "audit_id": old_audit_id,
                        "round_number": 1,
                        "topic": "theoretical_soundness",
                        "status": "completed",
                    },
                    {
                        "id": "old-only",
                        "audit_id": old_audit_id,
                        "round_number": 2,
                        "topic": "experimental_setup",
                        "status": "completed",
                    },
                    {
                        "id": "new-only",
                        "audit_id": new_audit_id,
                        "round_number": 1,
                        "topic": "statistical_rigor",
                        "status": "completed",
                    },
                    {
                        "id": "new-shared",
                        "audit_id": new_audit_id,
                        "round_number": 2,
                        "topic": "theoretical_soundness",
                        "status": "completed",
                    },
                ],
                "verdicts": [
                    {
                        "id": "verdict-old-shared",
                        "round_id": "old-shared",
                        "exchange_number": 1,
                        "claim_summary": "The proof omits a boundary case.",
                        "verdict_type": "ACTIONABLE_FLAW",
                        "confidence": 0.9,
                        "rationale": "The case is absent.",
                        "cited_chunk_ids": [],
                    },
                    {
                        "id": "verdict-old-only",
                        "round_id": "old-only",
                        "exchange_number": 1,
                        "claim_summary": "A baseline is absent.",
                        "verdict_type": "ACTIONABLE_FLAW",
                        "confidence": 0.8,
                        "rationale": "No comparison appears.",
                        "cited_chunk_ids": [],
                    },
                    {
                        "id": "verdict-new-only",
                        "round_id": "new-only",
                        "exchange_number": 1,
                        "claim_summary": "A correction is absent.",
                        "verdict_type": "ACTIONABLE_FLAW",
                        "confidence": 0.8,
                        "rationale": "Multiple tests are uncorrected.",
                        "cited_chunk_ids": [],
                    },
                    {
                        "id": "verdict-new-shared",
                        "round_id": "new-shared",
                        "exchange_number": 1,
                        "claim_summary": "The boundary case is now addressed.",
                        "verdict_type": "SOLIDIFIED",
                        "confidence": 0.9,
                        "rationale": "The new appendix supplies it.",
                        "cited_chunk_ids": [],
                    },
                ],
                "version_diffs": [],
            }
        )
        generated = (
            {
                "resolved_issues": ["The missing boundary case is addressed."],
                "still_open_issues": [],
                "new_issues": [],
                "summary": "The theoretical revision resolves the prior gap.",
            },
            Mock(),
            "test-provider",
        )

        with (
            patch.object(report_service, "get_supabase", return_value=database),
            patch.object(report_service, "get_llm_client", return_value=Mock()),
            patch.object(
                report_service,
                "generate_structured_with_meta",
                return_value=generated,
            ) as generate,
        ):
            first = report_service.generate_version_diffs(
                old_audit_id, new_audit_id
            )
            second = report_service.generate_version_diffs(
                old_audit_id, new_audit_id
            )

        self.assertEqual(generate.call_count, 1)
        self.assertEqual([row["round_topic"] for row in first], ["theoretical_soundness"])
        self.assertEqual(first[0]["id"], second[0]["id"])
        self.assertIn("## Resolved Issues", first[0]["diff_summary"])
        self.assertEqual(len(database.tables["version_diffs"]), 1)
        self.assertIn(
            (
                "version_diffs",
                "audit_id_old,audit_id_new,round_topic",
            ),
            database.upsert_conflicts,
        )


if __name__ == "__main__":
    unittest.main()
