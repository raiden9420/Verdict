"""Contract tests for Phase 3's authenticated product-layer API."""

from __future__ import annotations

import asyncio
import copy
import io
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import audits, papers
from app.api.dependencies import CurrentUser
from app.main import app
from app.models.schemas import AuditCreateRequest
from app.services import report_service


USER_ID = "00000000-0000-4000-8000-000000000201"
OTHER_USER_ID = "00000000-0000-4000-8000-000000000202"
PAPER_ID = "00000000-0000-4000-8000-000000000203"
AUDIT_ID = "00000000-0000-4000-8000-000000000204"
OLD_AUDIT_ID = "00000000-0000-4000-8000-000000000205"
ROUND_1_ID = "00000000-0000-4000-8000-000000000206"
ROUND_2_ID = "00000000-0000-4000-8000-000000000207"


def current_user() -> CurrentUser:
    return CurrentUser(id=USER_ID, access_token="signed-user-jwt")


class QueryStub:
    """Read-oriented PostgREST subset with enough recording for API contracts."""

    def __init__(self, database: "SupabaseStub", table_name: str) -> None:
        self.database = database
        self.table_name = table_name
        self.filters: list[tuple[str, str, object]] = []
        self.ordering: tuple[str, bool] | None = None
        self.row_limit: int | None = None
        self.pending_update: dict | None = None
        self.pending_insert: dict | list[dict] | None = None
        self.pending_upsert: dict | None = None
        self.upsert_fields: list[str] = []
        self.delete_requested = False

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field: str, value: object):
        self.filters.append(("eq", field, value))
        self.database.filters.append((self.table_name, "eq", field, value))
        return self

    def neq(self, field: str, value: object):
        self.filters.append(("neq", field, value))
        return self

    def in_(self, field: str, values: list[object]):
        self.filters.append(("in", field, list(values)))
        return self

    def or_(self, expression: str):
        self.filters.append(("or", "", expression))
        return self

    def order(self, field: str, desc: bool = False):
        self.ordering = (field, desc)
        return self

    def limit(self, value: int):
        self.row_limit = value
        return self

    def update(self, values: dict):
        self.pending_update = dict(values)
        return self

    def insert(self, values: dict | list[dict]):
        self.pending_insert = copy.deepcopy(values)
        return self

    def upsert(self, values: dict, on_conflict: str):
        self.pending_upsert = copy.deepcopy(values)
        self.upsert_fields = [field.strip() for field in on_conflict.split(",")]
        self.database.upsert_conflicts.append((self.table_name, on_conflict))
        return self

    def delete(self):
        self.delete_requested = True
        return self

    def _matches(self, row: dict) -> bool:
        for operation, field, expected in self.filters:
            if operation == "eq" and row.get(field) != expected:
                return False
            if operation == "neq" and row.get(field) == expected:
                return False
            if operation == "in" and row.get(field) not in expected:
                return False
            if operation == "or":
                # Only the paper-version query shape is needed here:
                # id.eq.<uuid>,parent_paper_id.eq.<uuid>.
                alternatives = []
                for condition in str(expected).split(","):
                    name, comparator, value = condition.split(".", 2)
                    if comparator == "eq":
                        alternatives.append(row.get(name) == value)
                if alternatives and not any(alternatives):
                    return False
        return True

    def execute(self):
        rows = self.database.tables.setdefault(self.table_name, [])
        matched = [row for row in rows if self._matches(row)]
        if self.pending_upsert is not None:
            stored = next(
                (
                    row
                    for row in rows
                    if all(
                        row.get(field) == self.pending_upsert.get(field)
                        for field in self.upsert_fields
                    )
                ),
                None,
            )
            if stored is None:
                stored = {
                    "id": str(uuid.uuid4()),
                    "created_at": "2026-08-12T00:00:00Z",
                    **self.pending_upsert,
                }
                rows.append(stored)
            else:
                stored.update(self.pending_upsert)
            return SimpleNamespace(data=[copy.deepcopy(stored)])
        if self.pending_insert is not None:
            inserted = (
                self.pending_insert
                if isinstance(self.pending_insert, list)
                else [self.pending_insert]
            )
            rows.extend(copy.deepcopy(inserted))
            return SimpleNamespace(data=copy.deepcopy(inserted))
        if self.pending_update is not None:
            for row in matched:
                row.update(self.pending_update)
            self.database.updates.append(
                (self.table_name, copy.deepcopy(self.pending_update), len(matched))
            )
            return SimpleNamespace(data=copy.deepcopy(matched))
        if self.delete_requested:
            self.database.tables[self.table_name] = [
                row for row in rows if not self._matches(row)
            ]
            return SimpleNamespace(data=copy.deepcopy(matched))

        result = copy.deepcopy(matched)
        if self.ordering:
            field, descending = self.ordering
            result.sort(key=lambda row: row.get(field) or 0, reverse=descending)
        if self.row_limit is not None:
            result = result[: self.row_limit]
        return SimpleNamespace(data=result)


class SupabaseStub:
    def __init__(self, tables: dict[str, list[dict]] | None = None) -> None:
        self.tables = copy.deepcopy(tables or {})
        self.filters: list[tuple[str, str, str, object]] = []
        self.updates: list[tuple[str, dict, int]] = []
        self.upsert_conflicts: list[tuple[str, str]] = []

    def table(self, name: str) -> QueryStub:
        return QueryStub(self, name)


class ProtectedEndpointTests(unittest.TestCase):
    def test_read_endpoints_return_clean_401_without_a_bearer_token(self) -> None:
        client = TestClient(app)
        paths = (
            "/papers",
            f"/papers/{PAPER_ID}/pdf",
            f"/papers/{PAPER_ID}/pdf-url",
            "/audits",
            f"/audits/{AUDIT_ID}/stream",
            f"/audits/{AUDIT_ID}/turns",
            f"/audits/{AUDIT_ID}/debrief",
            f"/audits/{AUDIT_ID}/debriefs",
            f"/audits/{AUDIT_ID}/final-report",
            f"/audits/{AUDIT_ID}/final-report/markdown",
            f"/audits/{AUDIT_ID}/version-diffs",
        )
        for path in paths:
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 401, response.text)
                self.assertEqual(response.headers.get("www-authenticate"), "Bearer")
                self.assertNotIn("traceback", response.text.lower())

    def test_write_endpoints_also_fail_authentication_cleanly(self) -> None:
        client = TestClient(app)
        audit_response = client.post(
            "/audits",
            json={
                "paper_id": PAPER_ID,
                "round_topics": ["theoretical_soundness"],
            },
        )
        upload_response = client.post(
            "/papers",
            files={"file": ("candidate.pdf", b"%PDF-test", "application/pdf")},
        )
        diff_retry_response = client.post(
            f"/audits/{AUDIT_ID}/version-diffs",
        )
        for response in (audit_response, upload_response, diff_retry_response):
            self.assertEqual(response.status_code, 401, response.text)
            self.assertEqual(response.headers.get("www-authenticate"), "Bearer")

    def test_expired_bearer_token_is_401_at_the_http_boundary(self) -> None:
        auth = Mock()
        auth.get_user.side_effect = RuntimeError("expired JWT internals")
        with patch(
            "app.api.dependencies.get_anon_supabase",
            return_value=SimpleNamespace(auth=auth),
        ):
            response = TestClient(app).get(
                "/papers",
                headers={"Authorization": "Bearer expired-token"},
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("www-authenticate"), "Bearer")
        self.assertNotIn("expired JWT internals", response.text)


class AuditCreationContractTests(unittest.IsolatedAsyncioTestCase):
    def test_row_creation_persists_one_audit_and_one_ordered_round_per_topic(self) -> None:
        database = SupabaseStub()
        topics = ["theoretical_soundness", "experimental_setup", "reproducibility"]

        rows = audits._create_audit_rows(
            database,
            audit_id=AUDIT_ID,
            paper_id=PAPER_ID,
            user_id=USER_ID,
            topics=topics,
            strictness_level="constructive",
            depth="deep",
            mode="author",
            domain="life_sciences",
        )

        self.assertEqual(len(database.tables["audits"]), 1)
        stored_audit = database.tables["audits"][0]
        self.assertEqual(stored_audit["user_id"], USER_ID)
        self.assertEqual(stored_audit["round_topic"], topics[0])
        self.assertEqual(stored_audit["strictness_level"], "constructive")
        self.assertEqual(stored_audit["depth"], "deep")
        self.assertEqual(stored_audit["mode"], "author")
        self.assertEqual(stored_audit["domain"], "life_sciences")
        self.assertEqual(stored_audit["status"], "in_progress")
        self.assertEqual([row["topic"] for row in rows], topics)
        self.assertEqual([row["round_number"] for row in rows], [1, 2, 3])
        self.assertTrue(all(row["status"] == "pending" for row in rows))
        self.assertEqual(database.tables["rounds"], rows)

    async def test_two_topic_audit_persists_full_config_and_schedules_one_outer_job(self) -> None:
        user_database = object()
        service_database = object()
        rows = [
            {"id": ROUND_1_ID, "round_number": 1, "topic": "theoretical_soundness"},
            {"id": ROUND_2_ID, "round_number": 2, "topic": "experimental_setup"},
        ]
        body = AuditCreateRequest(
            paper_id=PAPER_ID,
            round_topics=["theoretical_soundness", "experimental_setup"],
            depth="fast",
            strictness_level="brutal",
            domain="auto",
            mode="reviewer_assist",
        )
        executor = Mock()
        executor.submit.return_value = Mock()
        capacity = Mock()
        capacity.acquire.return_value = True

        with (
            patch.object(audits, "get_user_supabase", return_value=user_database) as get_user_db,
            patch.object(
                audits,
                "get_service_supabase",
                return_value=service_database,
            ) as get_service_db,
            patch.object(
                audits,
                "_load_owned_paper",
                return_value={"id": PAPER_ID, "detected_domain": "social_science"},
            ) as load_paper,
            patch.object(audits, "_paper_has_indexed_chunks", return_value=True),
            patch.object(audits, "_create_audit_rows", return_value=rows) as create_rows,
            patch.object(audits, "_audit_capacity", capacity),
            patch.object(audits, "_audit_executor", executor),
        ):
            response = await audits.create_audit(body, current_user())

        get_user_db.assert_called_once_with("signed-user-jwt")
        get_service_db.assert_called_once_with()
        load_paper.assert_called_once_with(user_database, PAPER_ID, USER_ID)
        self.assertIs(create_rows.call_args.args[0], service_database)
        self.assertEqual(create_rows.call_args.kwargs["topics"], body.round_topics)
        self.assertEqual(create_rows.call_args.kwargs["user_id"], USER_ID)
        self.assertEqual(create_rows.call_args.kwargs["strictness_level"], "brutal")
        self.assertEqual(create_rows.call_args.kwargs["mode"], "reviewer_assist")
        self.assertEqual(create_rows.call_args.kwargs["domain"], "social_science")

        scheduled = executor.submit.call_args
        self.assertIs(scheduled.args[0], audits._run_audit_job)
        self.assertEqual(scheduled.kwargs["round_rows"], rows)
        self.assertIs(scheduled.kwargs["user_supabase"], service_database)
        self.assertEqual(scheduled.kwargs["strictness_level"], "brutal")
        self.assertEqual(scheduled.kwargs["domain"], "social_science")
        self.assertEqual(scheduled.kwargs["mode"], "reviewer_assist")

        self.assertEqual(response.round_id, ROUND_1_ID)
        self.assertEqual(response.round_ids, [ROUND_1_ID, ROUND_2_ID])
        self.assertEqual(response.round_topics, body.round_topics)
        self.assertEqual(response.domain, "social_science")

        # The executor is mocked, so release the acquired slot and remove its hub.
        capacity.release()
        audits._event_hubs.pop(response.audit_id, None)

    async def test_domain_override_wins_over_detected_domain(self) -> None:
        body = AuditCreateRequest(
            paper_id=PAPER_ID,
            round_topics=["reproducibility"],
            domain="life_sciences",
        )
        capacity = Mock()
        capacity.acquire.return_value = True
        executor = Mock()
        executor.submit.return_value = Mock()
        with (
            patch.object(audits, "get_user_supabase", return_value=object()),
            patch.object(audits, "get_service_supabase", return_value=object()),
            patch.object(
                audits,
                "_load_owned_paper",
                return_value={"id": PAPER_ID, "detected_domain": "ml_cs"},
            ),
            patch.object(audits, "_paper_has_indexed_chunks", return_value=True),
            patch.object(
                audits,
                "_create_audit_rows",
                return_value=[
                    {"id": ROUND_1_ID, "round_number": 1, "topic": "reproducibility"}
                ],
            ) as create_rows,
            patch.object(audits, "_audit_capacity", capacity),
            patch.object(audits, "_audit_executor", executor),
        ):
            response = await audits.create_audit(body, current_user())

        self.assertEqual(create_rows.call_args.kwargs["domain"], "life_sciences")
        self.assertEqual(response.domain, "life_sciences")
        audits._event_hubs.pop(response.audit_id, None)

    async def test_hidden_or_foreign_paper_is_404_before_queue_acquisition(self) -> None:
        body = AuditCreateRequest(
            paper_id=PAPER_ID,
            round_topics=["theoretical_soundness"],
        )
        capacity = Mock()
        with (
            patch.object(audits, "get_user_supabase", return_value=object()),
            patch.object(audits, "get_service_supabase", return_value=object()),
            patch.object(audits, "_load_owned_paper", return_value=None),
            patch.object(audits, "_audit_capacity", capacity),
            self.assertRaises(HTTPException) as raised,
        ):
            await audits.create_audit(body, current_user())

        self.assertEqual(raised.exception.status_code, 404)
        capacity.acquire.assert_not_called()

    async def test_explicit_comparison_must_be_owned_and_completed(self) -> None:
        body = AuditCreateRequest(
            paper_id=PAPER_ID,
            round_topics=["theoretical_soundness"],
            compare_to_audit_id=OLD_AUDIT_ID,
        )
        for comparison, expected_status in (
            (None, 404),
            ({"id": OLD_AUDIT_ID, "status": "in_progress"}, 409),
        ):
            capacity = Mock()
            with (
                self.subTest(comparison=comparison),
                patch.object(audits, "get_user_supabase", return_value=object()),
                patch.object(audits, "get_service_supabase", return_value=object()),
                patch.object(
                    audits,
                    "_load_owned_paper",
                    return_value={"id": PAPER_ID, "detected_domain": "ml_cs"},
                ),
                patch.object(audits, "_paper_has_indexed_chunks", return_value=True),
                patch.object(audits, "_fetch_owned_audit", return_value=comparison),
                patch.object(audits, "_audit_capacity", capacity),
                self.assertRaises(HTTPException) as raised,
            ):
                await audits.create_audit(body, current_user())

            self.assertEqual(raised.exception.status_code, expected_status)
            capacity.acquire.assert_not_called()

    async def test_explicit_comparison_must_target_an_earlier_paper_version(self) -> None:
        root_paper_id = "00000000-0000-4000-8000-000000000209"
        comparison_paper_id = "00000000-0000-4000-8000-000000000210"
        body = AuditCreateRequest(
            paper_id=PAPER_ID,
            round_topics=["theoretical_soundness"],
            compare_to_audit_id=OLD_AUDIT_ID,
        )

        for comparison_version in (2, 3):
            with self.subTest(comparison_version=comparison_version):
                user_database = object()
                capacity = Mock()
                executor = Mock()
                comparison = {
                    "id": OLD_AUDIT_ID,
                    "status": "completed",
                    "paper_id": comparison_paper_id,
                    "paper": {
                        "id": comparison_paper_id,
                        "parent_paper_id": root_paper_id,
                        "version_number": comparison_version,
                    },
                }
                with (
                    patch.object(
                        audits,
                        "get_user_supabase",
                        return_value=user_database,
                    ),
                    patch.object(audits, "get_service_supabase", return_value=object()),
                    patch.object(
                        audits,
                        "_load_owned_paper",
                        return_value={
                            "id": PAPER_ID,
                            "parent_paper_id": root_paper_id,
                            "version_number": 2,
                            "detected_domain": "ml_cs",
                        },
                    ),
                    patch.object(audits, "_paper_has_indexed_chunks", return_value=True),
                    patch.object(
                        audits,
                        "_fetch_owned_audit",
                        return_value=comparison,
                    ) as fetch_comparison,
                    patch.object(audits, "_load_round_rows") as load_rounds,
                    patch.object(audits, "_create_audit_rows") as create_rows,
                    patch.object(audits, "_audit_capacity", capacity),
                    patch.object(audits, "_audit_executor", executor),
                    self.assertRaises(HTTPException) as raised,
                ):
                    await audits.create_audit(body, current_user())

                self.assertEqual(raised.exception.status_code, 409)
                self.assertEqual(
                    raised.exception.detail,
                    "The comparison audit must belong to an earlier paper version.",
                )
                fetch_comparison.assert_called_once_with(
                    user_database,
                    OLD_AUDIT_ID,
                    USER_ID,
                    "id, status, paper_id, paper:papers(id, parent_paper_id, version_number)",
                )
                load_rounds.assert_not_called()
                capacity.acquire.assert_not_called()
                create_rows.assert_not_called()
                executor.submit.assert_not_called()


class MultiTopicOuterRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.rows = [
            {"id": ROUND_1_ID, "round_number": 1, "topic": "theoretical_soundness"},
            {"id": ROUND_2_ID, "round_number": 2, "topic": "experimental_setup"},
        ]
        audits._event_hubs[AUDIT_ID] = audits._AuditEventHub()

    def tearDown(self) -> None:
        audits._event_hubs.pop(AUDIT_ID, None)

    def test_error_transition_preserves_already_completed_rounds(self) -> None:
        third_round_id = "00000000-0000-4000-8000-000000000208"
        database = SupabaseStub(
            {
                "audits": [{"id": AUDIT_ID, "status": "in_progress"}],
                "rounds": [
                    {"id": ROUND_1_ID, "status": "completed"},
                    {"id": ROUND_2_ID, "status": "in_progress"},
                    {"id": third_round_id, "status": "pending"},
                ],
            }
        )

        with audits.use_supabase(database):
            audits._mark_audit_error(
                AUDIT_ID,
                [ROUND_1_ID, ROUND_2_ID, third_round_id],
                "provider failed",
            )

        self.assertEqual(database.tables["audits"][0]["status"], "error")
        self.assertEqual(
            [row["status"] for row in database.tables["rounds"]],
            ["completed", "error", "error"],
        )

    async def test_topics_run_in_order_then_report_once_under_bound_backend_client(self) -> None:
        database = Mock()
        capacity = Mock()
        lifecycle: list[str] = []

        def fake_run(paper_id, round_id, topic, callback, **kwargs):
            self.assertIs(audits.get_supabase(), database)
            lifecycle.append(f"round:{topic}")
            callback({"type": "turn", "data": {"id": f"turn-{topic}"}})
            callback({"type": "complete", "data": {}})
            return {"status": "completed"}

        def fake_report(audit_id, mode=None):
            self.assertIs(audits.get_supabase(), database)
            lifecycle.append("report")
            return {
                "id": "report-1",
                "audit_id": audit_id,
                "mode": mode,
                "content": "# Reviewer-Assist Report",
            }

        def fake_diffs(new_id, old_id):
            self.assertIs(audits.get_supabase(), database)
            lifecycle.append("diff")
            self.assertEqual((new_id, old_id), (AUDIT_ID, OLD_AUDIT_ID))
            return [{"id": "diff-1"}]

        with (
            patch.object(audits, "run_audit", side_effect=fake_run) as run,
            patch.object(audits, "generate_final_report", side_effect=fake_report) as report,
            patch.object(audits, "generate_version_diffs_for_audit", side_effect=fake_diffs) as diffs,
            patch.object(audits, "find_previous_audit_id") as find_previous,
            patch.object(audits, "_audit_capacity", capacity),
        ):
            await asyncio.to_thread(
                audits._run_audit_job,
                audit_id=AUDIT_ID,
                paper_id=PAPER_ID,
                round_rows=self.rows,
                strictness_level="brutal",
                domain="life_sciences",
                mode="reviewer_assist",
                compare_to_audit_id=OLD_AUDIT_ID,
                user_supabase=database,
                loop=asyncio.get_running_loop(),
            )
            await asyncio.sleep(0)

        self.assertEqual(
            lifecycle,
            ["round:theoretical_soundness", "round:experimental_setup", "report", "diff"],
        )
        self.assertEqual(run.call_count, 2)
        for run_call in run.call_args_list:
            self.assertEqual(run_call.kwargs["strictness_level"], "brutal")
            self.assertEqual(run_call.kwargs["domain"], "life_sciences")
            self.assertNotIn("mode", run_call.kwargs)
        report.assert_called_once_with(AUDIT_ID, mode="reviewer_assist")
        diffs.assert_called_once_with(AUDIT_ID, OLD_AUDIT_ID)
        find_previous.assert_not_called()
        capacity.release.assert_called_once()

        events = list(audits._event_hubs[AUDIT_ID].history)
        self.assertEqual(
            [event.type for event in events],
            [
                "round_start",
                "turn",
                "round_start",
                "turn",
                "final_report",
                "version_diffs",
                "complete",
            ],
        )
        turn_events = [event for event in events if event.type == "turn"]
        self.assertEqual(
            [event.data["round_number"] for event in turn_events],
            [1, 2],
        )
        self.assertEqual(events[-1].data["version_diff_count"], 1)
        database.table.assert_called_with("audits")

    async def test_a_failed_middle_topic_stops_later_topics_and_final_synthesis(self) -> None:
        third_round = {
            "id": "00000000-0000-4000-8000-000000000208",
            "round_number": 3,
            "topic": "reproducibility",
        }
        attempted: list[str] = []

        def fake_run(_paper_id, _round_id, topic, callback, **_kwargs):
            attempted.append(topic)
            if topic == "experimental_setup":
                callback({"type": "error", "data": {"message": "provider exhausted"}})
                raise RuntimeError("raw provider exception")
            return {"status": "completed"}

        capacity = Mock()
        with (
            patch.object(audits, "run_audit", side_effect=fake_run),
            patch.object(audits, "generate_final_report") as report,
            patch.object(audits, "generate_version_diffs_for_audit") as diffs,
            patch.object(audits, "_mark_audit_error") as mark_error,
            patch.object(audits, "_audit_capacity", capacity),
        ):
            await asyncio.to_thread(
                audits._run_audit_job,
                audit_id=AUDIT_ID,
                paper_id=PAPER_ID,
                round_rows=[*self.rows, third_round],
                strictness_level="brutal",
                domain="ml_cs",
                user_supabase=Mock(),
                loop=asyncio.get_running_loop(),
            )
            await asyncio.sleep(0)

        self.assertEqual(attempted, ["theoretical_soundness", "experimental_setup"])
        report.assert_not_called()
        diffs.assert_not_called()
        mark_error.assert_called_once()
        self.assertEqual(mark_error.call_args.args[0], AUDIT_ID)
        self.assertEqual(
            mark_error.call_args.args[1],
            [ROUND_1_ID, ROUND_2_ID, third_round["id"]],
        )
        self.assertEqual(mark_error.call_args.args[2], "provider exhausted")
        self.assertEqual(
            [event.type for event in audits._event_hubs[AUDIT_ID].history],
            ["round_start", "round_start", "audit_error"],
        )
        capacity.release.assert_called_once()

    async def test_optional_version_diff_failure_does_not_invalidate_final_report(self) -> None:
        database = SupabaseStub(
            {
                "audits": [{"id": AUDIT_ID, "status": "in_progress"}],
                "rounds": copy.deepcopy(self.rows),
            }
        )
        capacity = Mock()
        with (
            patch.object(
                audits,
                "run_audit",
                return_value={"status": "completed"},
            ),
            patch.object(
                audits,
                "generate_final_report",
                return_value={"id": "report-survives"},
            ) as report,
            patch.object(
                audits,
                "generate_version_diffs_for_audit",
                side_effect=RuntimeError("version diff database unavailable"),
            ) as diffs,
            patch.object(audits, "_mark_audit_error") as mark_error,
            patch.object(audits, "_audit_capacity", capacity),
        ):
            await asyncio.to_thread(
                audits._run_audit_job,
                audit_id=AUDIT_ID,
                paper_id=PAPER_ID,
                round_rows=self.rows,
                compare_to_audit_id=OLD_AUDIT_ID,
                user_supabase=database,
                loop=asyncio.get_running_loop(),
            )
            await asyncio.sleep(0)

        mark_error.assert_not_called()
        report.assert_called_once_with(AUDIT_ID, mode="author")
        diffs.assert_called_once_with(AUDIT_ID, OLD_AUDIT_ID)
        capacity.release.assert_called_once()
        event_types = [event.type for event in audits._event_hubs[AUDIT_ID].history]
        self.assertEqual(
            event_types,
            [
                "round_start",
                "round_start",
                "final_report",
                "version_diff_error",
                "complete",
            ],
        )
        complete = audits._event_hubs[AUDIT_ID].history[-1]
        self.assertEqual(complete.data["final_report_id"], "report-survives")
        self.assertEqual(complete.data["version_diff_count"], 0)
        self.assertEqual(database.tables["audits"][0]["status"], "completed")
        self.assertIsNone(database.tables["audits"][0]["error_message"])

    async def test_previous_audit_lookup_failure_is_nonfatal_after_report(self) -> None:
        database = SupabaseStub(
            {
                "audits": [{"id": AUDIT_ID, "status": "in_progress"}],
                "rounds": copy.deepcopy(self.rows),
            }
        )
        capacity = Mock()

        def fail_previous_lookup(_audit_id: str) -> None:
            # Completion is durable before any optional auto-comparison lookup.
            self.assertEqual(database.tables["audits"][0]["status"], "completed")
            raise RuntimeError("previous audit query unavailable")

        with (
            patch.object(audits, "run_audit", return_value={"status": "completed"}),
            patch.object(
                audits,
                "generate_final_report",
                return_value={"id": "report-survives-lookup"},
            ) as report,
            patch.object(
                audits,
                "find_previous_audit_id",
                side_effect=fail_previous_lookup,
            ) as find_previous,
            patch.object(audits, "generate_version_diffs_for_audit") as diffs,
            patch.object(audits, "_mark_audit_error") as mark_error,
            patch.object(audits, "_audit_capacity", capacity),
        ):
            await asyncio.to_thread(
                audits._run_audit_job,
                audit_id=AUDIT_ID,
                paper_id=PAPER_ID,
                round_rows=self.rows,
                user_supabase=database,
                loop=asyncio.get_running_loop(),
            )
            await asyncio.sleep(0)

        report.assert_called_once_with(AUDIT_ID, mode="author")
        find_previous.assert_called_once_with(AUDIT_ID)
        diffs.assert_not_called()
        mark_error.assert_not_called()
        capacity.release.assert_called_once()
        self.assertEqual(database.tables["audits"][0]["status"], "completed")
        self.assertIsNone(database.tables["audits"][0]["error_message"])
        events = audits._event_hubs[AUDIT_ID].history
        self.assertEqual(
            [event.type for event in events],
            [
                "round_start",
                "round_start",
                "final_report",
                "version_diff_error",
                "complete",
            ],
        )
        self.assertEqual(events[-1].data["final_report_id"], "report-survives-lookup")
        self.assertEqual(events[-1].data["version_diff_count"], 0)


class ProductReadApiTests(unittest.IsolatedAsyncioTestCase):
    def database(self) -> SupabaseStub:
        return SupabaseStub(
            {
                "audits": [
                    {
                        "id": AUDIT_ID,
                        "paper_id": PAPER_ID,
                        "user_id": USER_ID,
                        "status": "completed",
                        "domain": "social_science",
                    }
                ],
                # Deliberately reversed to test stable round ordering.
                "rounds": [
                    {
                        "id": ROUND_2_ID,
                        "audit_id": AUDIT_ID,
                        "round_number": 2,
                        "topic": "reproducibility",
                        "status": "completed",
                    },
                    {
                        "id": ROUND_1_ID,
                        "audit_id": AUDIT_ID,
                        "round_number": 1,
                        "topic": "theoretical_soundness",
                        "status": "completed",
                    },
                ],
                "debrief_cards": [
                    {
                        "id": "card-2",
                        "round_id": ROUND_2_ID,
                        "executive_synthesis": "Reproducibility review.",
                        "solidified_strengths": "[]",
                        "actionable_weaknesses": '["Missing materials"]',
                        "contested_points": "[]",
                    },
                    {
                        "id": "card-1",
                        "round_id": ROUND_1_ID,
                        "executive_synthesis": "Theory review.",
                        "solidified_strengths": '["Clear theorem"]',
                        "actionable_weaknesses": "[]",
                        "contested_points": "[]",
                    },
                ],
                "papers": [
                    {
                        "id": PAPER_ID,
                        "user_id": USER_ID,
                        "reproducibility_signals": {
                            "by_domain": {
                                "ml_cs": {"domain": "ml_cs", "code_available": True},
                                "social_science": {
                                    "domain": "social_science",
                                    "preregistered": True,
                                },
                            }
                        },
                    }
                ],
                "final_reports": [
                    {
                        "id": "report-1",
                        "audit_id": AUDIT_ID,
                        "mode": "reviewer_assist",
                        "content": "# Reviewer-Assist Report\n\n## Strengths\n\n- Clear theorem.\n",
                        "created_at": "2026-08-12T00:00:00Z",
                    }
                ],
                "version_diffs": [
                    # Deliberately stored in the opposite order from the new
                    # audit's rounds. The endpoint contract follows topic
                    # order, not insertion or timestamp order.
                    {
                        "id": "diff-2",
                        "audit_id_old": OLD_AUDIT_ID,
                        "audit_id_new": AUDIT_ID,
                        "round_topic": "reproducibility",
                        "diff_summary": "# Version Diff\n\nMaterials added.",
                        "created_at": "2026-08-11T00:00:00Z",
                    },
                    {
                        "id": "diff-1",
                        "audit_id_old": OLD_AUDIT_ID,
                        "audit_id_new": AUDIT_ID,
                        "round_topic": "theoretical_soundness",
                        "diff_summary": "# Version Diff\n\nResolved.",
                        "created_at": "2026-08-12T00:00:00Z",
                    },
                    {
                        "id": "diff-other-old",
                        "audit_id_old": str(uuid.uuid4()),
                        "audit_id_new": AUDIT_ID,
                        "round_topic": "reproducibility",
                        "diff_summary": "# Other comparison",
                        "created_at": "2026-08-13T00:00:00Z",
                    },
                ],
            }
        )

    async def test_debriefs_are_per_topic_ordered_and_domain_calibrated(self) -> None:
        database = self.database()
        with patch.object(audits, "get_user_supabase", return_value=database):
            rows = await audits.get_debriefs(AUDIT_ID, current_user())
            legacy = await audits.get_debrief(AUDIT_ID, current_user())

        self.assertEqual([row.round_number for row in rows], [1, 2])
        self.assertEqual(
            [row.round_topic for row in rows],
            ["theoretical_soundness", "reproducibility"],
        )
        self.assertIsNone(rows[0].reproducibility_checklist)
        self.assertEqual(
            rows[1].reproducibility_checklist,
            {"domain": "social_science", "preregistered": True},
        )
        self.assertEqual(legacy.id, "card-1")

    async def test_singular_debrief_waits_for_whole_multi_topic_audit(self) -> None:
        database = self.database()
        database.tables["audits"][0]["status"] = "in_progress"
        database.tables["rounds"][0]["status"] = "in_progress"
        database.tables["debrief_cards"] = [
            row
            for row in database.tables["debrief_cards"]
            if row["round_id"] == ROUND_1_ID
        ]

        with patch.object(audits, "get_user_supabase", return_value=database):
            partial = await audits.get_debriefs(AUDIT_ID, current_user())
            with self.assertRaises(HTTPException) as raised:
                await audits.get_debrief(AUDIT_ID, current_user())

        self.assertEqual([card.id for card in partial], ["card-1"])
        self.assertEqual(partial[0].round_number, 1)
        self.assertEqual(partial[0].round_topic, "theoretical_soundness")
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.detail, "Debrief card not yet generated.")

    async def test_turns_and_verdicts_include_round_identity_in_outer_order(self) -> None:
        database = self.database()
        database.tables["turns"] = [
            {
                "id": "turn-round-2",
                "round_id": ROUND_2_ID,
                "exchange_number": 1,
                "agent_type": "attacker",
                "sequence": 1,
                "content": '{"claim_summary":"second topic"}',
            },
            {
                "id": "turn-round-1",
                "round_id": ROUND_1_ID,
                "exchange_number": 1,
                "agent_type": "attacker",
                "sequence": 1,
                "content": '{"claim_summary":"first topic"}',
            },
        ]
        database.tables["verdicts"] = [
            {
                "id": "verdict-round-2",
                "round_id": ROUND_2_ID,
                "exchange_number": 1,
                "verdict_type": "ACTIONABLE_FLAW",
                "cited_chunk_ids": "[]",
            },
            {
                "id": "verdict-round-1",
                "round_id": ROUND_1_ID,
                "exchange_number": 1,
                "verdict_type": "SOLIDIFIED",
                "cited_chunk_ids": "[]",
            },
        ]

        with patch.object(audits, "get_user_supabase", return_value=database):
            result = await audits.list_turns(AUDIT_ID, current_user())

        self.assertEqual(
            [turn.id for turn in result.turns],
            ["turn-round-1", "turn-round-2"],
        )
        self.assertEqual(
            [turn.round_topic for turn in result.turns],
            ["theoretical_soundness", "reproducibility"],
        )
        self.assertEqual(
            [verdict.round_number for verdict in result.verdicts],
            [1, 2],
        )
        self.assertEqual(result.turns[0].content["claim_summary"], "first topic")

    async def test_stored_report_and_markdown_export_have_stable_contracts(self) -> None:
        database = self.database()
        with patch.object(audits, "get_user_supabase", return_value=database) as get_db:
            report = await audits.get_final_report(AUDIT_ID, current_user())
            exported = await audits.export_final_report_markdown(
                AUDIT_ID,
                current_user(),
            )

        self.assertEqual(report.id, "report-1")
        self.assertEqual(report.mode, "reviewer_assist")
        self.assertEqual(exported.body.decode(), report.content)
        self.assertEqual(
            exported.headers["content-type"],
            "text/markdown; charset=utf-8",
        )
        self.assertEqual(
            exported.headers["content-disposition"],
            f'attachment; filename="verdict-report-{AUDIT_ID}.md"',
        )
        self.assertEqual(get_db.call_args_list, [call("signed-user-jwt"), call("signed-user-jwt")])

    async def test_version_diff_endpoint_filters_and_follows_new_round_order(self) -> None:
        database = self.database()
        with patch.object(audits, "get_user_supabase", return_value=database):
            rows = await audits.get_version_diffs(
                AUDIT_ID,
                OLD_AUDIT_ID,
                current_user(),
            )

        self.assertEqual([row.id for row in rows], ["diff-1", "diff-2"])
        self.assertEqual(
            [row.round_topic for row in rows],
            ["theoretical_soundness", "reproducibility"],
        )
        self.assertIn(
            ("version_diffs", "eq", "audit_id_new", AUDIT_ID),
            database.filters,
        )
        self.assertIn(
            ("version_diffs", "eq", "audit_id_old", OLD_AUDIT_ID),
            database.filters,
        )

    async def test_unowned_audit_is_indistinguishable_from_missing_for_reports(self) -> None:
        database = self.database()
        database.tables["audits"][0]["user_id"] = OTHER_USER_ID
        with (
            patch.object(audits, "get_user_supabase", return_value=database),
            self.assertRaises(HTTPException) as raised,
        ):
            await audits.get_final_report(AUDIT_ID, current_user())

        self.assertEqual(raised.exception.status_code, 404)

    async def test_failed_audit_never_serves_stored_report_or_markdown(self) -> None:
        database = self.database()
        database.tables["audits"][0]["status"] = "error"
        for endpoint in (
            audits.get_final_report,
            audits.export_final_report_markdown,
        ):
            with (
                self.subTest(endpoint=endpoint.__name__),
                patch.object(audits, "get_user_supabase", return_value=database),
                patch.object(audits, "_load_final_report", wraps=audits._load_final_report) as load,
                self.assertRaises(HTTPException) as raised,
            ):
                await endpoint(AUDIT_ID, current_user())

            self.assertEqual(raised.exception.status_code, 409)
            load.assert_not_called()

    async def test_invalid_comparison_uuid_is_clean_400(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            await audits.get_version_diffs(AUDIT_ID, "not-a-uuid", current_user())
        self.assertEqual(raised.exception.status_code, 400)


class VersionDiffRetryApiTests(unittest.IsolatedAsyncioTestCase):
    old_paper_id = "00000000-0000-4000-8000-000000000221"
    other_root_id = "00000000-0000-4000-8000-000000000222"

    def user_database(
        self,
        *,
        new_status: str = "completed",
        old_status: str = "completed",
        include_new: bool = True,
        include_old: bool = True,
    ) -> SupabaseStub:
        rows = []
        if include_new:
            rows.append(
                {
                    "id": AUDIT_ID,
                    "user_id": USER_ID,
                    "status": new_status,
                }
            )
        if include_old:
            rows.append(
                {
                    "id": OLD_AUDIT_ID,
                    "user_id": USER_ID,
                    "status": old_status,
                }
            )
        return SupabaseStub({"audits": rows})

    def service_database(
        self,
        *,
        old_version: int = 1,
        new_version: int = 2,
        new_parent: str | None = None,
        old_topic: str = "theoretical_soundness",
        new_topic: str = "theoretical_soundness",
        old_round_status: str = "completed",
    ) -> SupabaseStub:
        parent = self.old_paper_id if new_parent is None else new_parent
        return SupabaseStub(
            {
                "audits": [
                    {
                        "id": OLD_AUDIT_ID,
                        "paper_id": self.old_paper_id,
                        "user_id": USER_ID,
                        "mode": "author",
                        "status": "completed",
                    },
                    {
                        "id": AUDIT_ID,
                        "paper_id": PAPER_ID,
                        "user_id": USER_ID,
                        "mode": "author",
                        "status": "completed",
                    },
                ],
                "papers": [
                    {
                        "id": self.old_paper_id,
                        "parent_paper_id": None,
                        "version_number": old_version,
                    },
                    {
                        "id": PAPER_ID,
                        "parent_paper_id": parent,
                        "version_number": new_version,
                    },
                ],
                "rounds": [
                    {
                        "id": ROUND_1_ID,
                        "audit_id": OLD_AUDIT_ID,
                        "round_number": 1,
                        "topic": old_topic,
                        "status": old_round_status,
                    },
                    {
                        "id": ROUND_2_ID,
                        "audit_id": AUDIT_ID,
                        "round_number": 1,
                        "topic": new_topic,
                        "status": "completed",
                    },
                ],
                "verdicts": [
                    {
                        "id": "old-verdict",
                        "round_id": ROUND_1_ID,
                        "exchange_number": 1,
                        "claim_summary": "A boundary case is missing.",
                        "verdict_type": "ACTIONABLE_FLAW",
                        "confidence": 0.9,
                        "rationale": "The proof omits it.",
                        "cited_chunk_ids": [],
                    },
                    {
                        "id": "new-verdict",
                        "round_id": ROUND_2_ID,
                        "exchange_number": 1,
                        "claim_summary": "The boundary case is addressed.",
                        "verdict_type": "SOLIDIFIED",
                        "confidence": 0.9,
                        "rationale": "The revision adds the case.",
                        "cited_chunk_ids": [],
                    },
                ],
                "version_diffs": [],
            }
        )

    async def test_explicit_retry_is_service_owned_and_idempotent(self) -> None:
        user_database = self.user_database()
        service_database = self.service_database()
        lock = Mock()
        lock.acquire.return_value = True
        generated = (
            {
                "resolved_issues": ["The missing boundary case is addressed."],
                "still_open_issues": [],
                "new_issues": [],
                "summary": "The revision resolves the prior issue.",
            },
            Mock(),
            "test-provider",
        )
        with (
            patch.object(audits, "get_user_supabase", return_value=user_database),
            patch.object(audits, "get_service_supabase", return_value=service_database),
            patch.object(audits, "_llm_work_lock", lock),
            patch.object(report_service, "get_llm_client", return_value=Mock()),
            patch.object(
                report_service,
                "generate_structured_with_meta",
                return_value=generated,
            ) as generate,
        ):
            first = await audits.retry_version_diffs(
                AUDIT_ID,
                OLD_AUDIT_ID,
                current_user(),
            )
            second = await audits.retry_version_diffs(
                AUDIT_ID,
                OLD_AUDIT_ID,
                current_user(),
            )

        self.assertEqual(generate.call_count, 1)
        self.assertEqual([row.id for row in first], [row.id for row in second])
        self.assertEqual(first[0].audit_id_old, OLD_AUDIT_ID)
        self.assertEqual(first[0].audit_id_new, AUDIT_ID)
        self.assertEqual(len(service_database.tables["version_diffs"]), 1)
        self.assertEqual(lock.acquire.call_count, 2)
        self.assertEqual(lock.release.call_count, 2)

    async def test_omitted_compare_to_auto_selects_under_user_rls(self) -> None:
        user_database = self.user_database()
        lock = Mock()
        lock.acquire.return_value = True
        stored = {
            "id": "auto-diff",
            "audit_id_old": OLD_AUDIT_ID,
            "audit_id_new": AUDIT_ID,
            "round_topic": "theoretical_soundness",
            "diff_summary": "# Version Diff",
            "created_at": "2026-08-12T00:00:00Z",
        }
        with (
            patch.object(audits, "get_user_supabase", return_value=user_database),
            patch.object(
                audits,
                "_find_previous_audit_as_user",
                return_value=OLD_AUDIT_ID,
            ) as find_previous,
            patch.object(
                audits,
                "_generate_version_diffs_as_service",
                return_value=[stored],
            ) as generate,
            patch.object(audits, "_llm_work_lock", lock),
        ):
            rows = await audits.retry_version_diffs(AUDIT_ID, None, current_user())

        self.assertEqual([row.id for row in rows], ["auto-diff"])
        find_previous.assert_called_once_with(user_database, AUDIT_ID)
        generate.assert_called_once_with(AUDIT_ID, OLD_AUDIT_ID, USER_ID)
        lock.release.assert_called_once()

    async def test_retry_requires_owned_completed_new_and_old_audits(self) -> None:
        cases = (
            ({"include_new": False}, 404),
            ({"new_status": "in_progress"}, 409),
            ({"include_old": False}, 404),
            ({"old_status": "in_progress"}, 409),
        )
        for database_kwargs, expected_status in cases:
            with self.subTest(database_kwargs=database_kwargs):
                lock = Mock()
                with (
                    patch.object(
                        audits,
                        "get_user_supabase",
                        return_value=self.user_database(**database_kwargs),
                    ),
                    patch.object(audits, "_llm_work_lock", lock),
                    patch.object(
                        audits,
                        "_generate_version_diffs_as_service",
                    ) as generate,
                    self.assertRaises(HTTPException) as raised,
                ):
                    await audits.retry_version_diffs(
                        AUDIT_ID,
                        OLD_AUDIT_ID,
                        current_user(),
                    )

                self.assertEqual(raised.exception.status_code, expected_status)
                lock.acquire.assert_not_called()
                generate.assert_not_called()

    async def test_service_side_ownership_recheck_hides_changed_owner(self) -> None:
        service_database = self.service_database()
        service_database.tables["audits"][0]["user_id"] = OTHER_USER_ID
        lock = Mock()
        lock.acquire.return_value = True
        with (
            patch.object(
                audits,
                "get_user_supabase",
                return_value=self.user_database(),
            ),
            patch.object(
                audits,
                "get_service_supabase",
                return_value=service_database,
            ),
            patch.object(audits, "_llm_work_lock", lock),
            patch.object(report_service, "get_llm_client") as llm,
            self.assertRaises(HTTPException) as raised,
        ):
            await audits.retry_version_diffs(
                AUDIT_ID,
                OLD_AUDIT_ID,
                current_user(),
            )

        self.assertEqual(raised.exception.status_code, 404)
        llm.assert_not_called()
        lock.release.assert_called_once()

    async def test_auto_selection_readiness_and_outages_map_cleanly(self) -> None:
        cases = (
            (None, None, 409),
            (
                None,
                report_service.VersionComparisonError("comparison is not ready"),
                409,
            ),
            (None, RuntimeError("database unavailable"), 503),
        )
        for result, failure, expected_status in cases:
            with self.subTest(failure=type(failure).__name__ if failure else "none"):
                lock = Mock()
                with (
                    patch.object(
                        audits,
                        "get_user_supabase",
                        return_value=self.user_database(),
                    ),
                    patch.object(
                        audits,
                        "_find_previous_audit_as_user",
                        return_value=result,
                        side_effect=failure,
                    ),
                    patch.object(
                        audits,
                        "_generate_version_diffs_as_service",
                    ) as generate,
                    patch.object(audits, "_llm_work_lock", lock),
                    self.assertRaises(HTTPException) as raised,
                ):
                    await audits.retry_version_diffs(
                        AUDIT_ID,
                        None,
                        current_user(),
                    )

                self.assertEqual(raised.exception.status_code, expected_status)
                generate.assert_not_called()
                lock.acquire.assert_not_called()

    async def test_report_validation_errors_are_409_without_llm_work(self) -> None:
        variants = (
            (
                {"new_parent": self.other_root_id},
                "same paper version family",
            ),
            (
                {"old_version": 2, "new_version": 2},
                "earlier paper version",
            ),
            (
                {"new_topic": "experimental_setup"},
                "do not share any completed round topics",
            ),
            (
                {"old_round_status": "in_progress"},
                "requires every round to be completed",
            ),
        )
        for database_kwargs, detail_fragment in variants:
            with self.subTest(database_kwargs=database_kwargs):
                lock = Mock()
                lock.acquire.return_value = True
                with (
                    patch.object(
                        audits,
                        "get_user_supabase",
                        return_value=self.user_database(),
                    ),
                    patch.object(
                        audits,
                        "get_service_supabase",
                        return_value=self.service_database(**database_kwargs),
                    ),
                    patch.object(audits, "_llm_work_lock", lock),
                    patch.object(report_service, "get_llm_client") as llm,
                    self.assertRaises(HTTPException) as raised,
                ):
                    await audits.retry_version_diffs(
                        AUDIT_ID,
                        OLD_AUDIT_ID,
                        current_user(),
                    )

                self.assertEqual(raised.exception.status_code, 409)
                self.assertIn(detail_fragment, str(raised.exception.detail))
                llm.assert_not_called()
                lock.release.assert_called_once()

    async def test_busy_or_transient_generation_failure_maps_to_503(self) -> None:
        for busy, failure in (
            (True, None),
            (False, report_service.ReportServiceError("database unavailable")),
            (False, RuntimeError("provider unavailable")),
        ):
            with self.subTest(busy=busy, failure=type(failure).__name__ if failure else None):
                lock = Mock()
                lock.acquire.return_value = not busy
                with (
                    patch.object(
                        audits,
                        "get_user_supabase",
                        return_value=self.user_database(),
                    ),
                    patch.object(
                        audits,
                        "_generate_version_diffs_as_service",
                        side_effect=failure,
                    ) as generate,
                    patch.object(audits, "_llm_work_lock", lock),
                    self.assertRaises(HTTPException) as raised,
                ):
                    await audits.retry_version_diffs(
                        AUDIT_ID,
                        OLD_AUDIT_ID,
                        current_user(),
                    )

                self.assertEqual(raised.exception.status_code, 503)
                if busy:
                    generate.assert_not_called()
                    lock.release.assert_not_called()
                else:
                    generate.assert_called_once_with(AUDIT_ID, OLD_AUDIT_ID, USER_ID)
                    lock.release.assert_called_once()


class AuthenticatedPaperReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_pdf_signing_uses_raw_access_token_and_same_rls_client(self) -> None:
        database = object()
        with (
            patch.object(papers, "get_user_supabase", return_value=database) as get_db,
            patch.object(
                papers,
                "_load_authorized_pdf",
                return_value=f"{USER_ID}/{PAPER_ID}.pdf",
            ) as authorize,
            patch.object(
                papers,
                "_create_signed_pdf_url",
                return_value="https://storage.test/signed-paper",
            ) as sign,
        ):
            url = await papers._signed_pdf_for_user(PAPER_ID, current_user())

        self.assertEqual(url, "https://storage.test/signed-paper")
        get_db.assert_called_once_with("signed-user-jwt")
        authorize.assert_called_once_with(database, PAPER_ID, USER_ID)
        sign.assert_called_once_with(f"{USER_ID}/{PAPER_ID}.pdf", database)

    async def test_unowned_pdf_is_404_and_never_attempts_storage_signing(self) -> None:
        with (
            patch.object(papers, "get_user_supabase", return_value=object()),
            patch.object(papers, "_load_authorized_pdf", return_value=None),
            patch.object(papers, "_create_signed_pdf_url") as sign,
            self.assertRaises(HTTPException) as raised,
        ):
            await papers._signed_pdf_for_user(PAPER_ID, current_user())

        self.assertEqual(raised.exception.status_code, 404)
        sign.assert_not_called()


class VersionLinkContractTests(unittest.TestCase):
    def test_version_conflict_classifier_is_narrow(self) -> None:
        class PostgrestConflict(RuntimeError):
            code = "23505"
            message = "duplicate key value violates unique constraint"
            details = (
                "Key (parent_paper_id, version_number)=(root, 2) already exists."
            )

        self.assertTrue(
            papers._is_version_number_conflict(
                RuntimeError('duplicate key violates "uq_papers_parent_version"')
            )
        )
        self.assertTrue(papers._is_version_number_conflict(PostgrestConflict()))
        self.assertFalse(
            papers._is_version_number_conflict(
                RuntimeError("23505 duplicate key on papers_pkey")
            )
        )
        self.assertFalse(
            papers._is_version_number_conflict(RuntimeError("database unavailable"))
        )

    def test_uploading_from_any_revision_links_to_root_and_increments_family(self) -> None:
        root_id = "00000000-0000-4000-8000-000000000211"
        version_two_id = "00000000-0000-4000-8000-000000000212"
        database = SupabaseStub(
            {
                "papers": [
                    {"id": root_id, "parent_paper_id": None, "version_number": 1},
                    {
                        "id": version_two_id,
                        "parent_paper_id": root_id,
                        "version_number": 2,
                    },
                    {
                        "id": "00000000-0000-4000-8000-000000000213",
                        "parent_paper_id": root_id,
                        "version_number": 3,
                    },
                ]
            }
        )

        resolved_root, next_version = papers._resolve_version_link(
            database,
            version_two_id,
        )

        self.assertEqual(resolved_root, root_id)
        self.assertEqual(next_version, 4)

    def test_hidden_parent_is_clean_404(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            papers._resolve_version_link(SupabaseStub({"papers": []}), PAPER_ID)
        self.assertEqual(raised.exception.status_code, 404)


class VersionUploadRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_version_conflict_retries_only_persistence(self) -> None:
        service_database = object()
        parsed = {
            "pages": [(1, "A research methods and results section." * 10)],
            "page_count": 1,
            "storage_path": "placeholder.pdf",
        }
        chunks = [
            {
                "text": "research content",
                "page_number": 1,
                "chunk_index": 0,
            }
        ]
        conflict = RuntimeError(
            'duplicate key violates unique constraint "uq_papers_parent_version"'
        )
        upload = UploadFile(
            filename="revision.pdf",
            file=io.BytesIO(b"%PDF-test"),
        )

        with (
            patch.object(papers, "get_user_supabase", return_value=object()),
            patch.object(
                papers,
                "get_service_supabase",
                return_value=service_database,
            ),
            patch.object(
                papers,
                "_resolve_version_link",
                return_value=(PAPER_ID, 2),
            ),
            patch.object(
                papers,
                "_next_version_number",
                side_effect=[2, 3],
            ) as next_version,
            patch.object(
                papers,
                "validate_and_parse_pdf",
                return_value=parsed,
            ) as parse,
            patch.object(
                papers,
                "classify_document_relevance",
                return_value=SimpleNamespace(
                    is_research_paper=True,
                    reason="Research paper.",
                    detected_domain="ml_cs",
                ),
            ) as classify,
            patch.object(papers, "chunk_pages", return_value=chunks) as chunk,
            patch.object(papers, "embed_batch", return_value=[[0.0] * 384]) as embed,
            patch.object(papers, "scan_reproducibility_by_domain", return_value={}) as scan,
            patch.object(
                papers,
                "_persist_ingestion",
                side_effect=[conflict, None],
            ) as persist,
        ):
            response = await papers.upload_paper(
                file=upload,
                force=False,
                parent_paper_id=PAPER_ID,
                current_user=current_user(),
            )

        self.assertEqual(next_version.call_count, 2)
        self.assertEqual(
            [attempt.kwargs["version_number"] for attempt in persist.call_args_list],
            [2, 3],
        )
        self.assertEqual(response.parent_paper_id, PAPER_ID)
        self.assertEqual(response.version_number, 3)
        parse.assert_called_once()
        classify.assert_called_once()
        chunk.assert_called_once()
        embed.assert_called_once()
        scan.assert_called_once()


if __name__ == "__main__":
    unittest.main()
