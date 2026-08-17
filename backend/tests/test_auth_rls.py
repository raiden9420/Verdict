from __future__ import annotations

import asyncio
import os
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import database
from app.api import dependencies
from app.main import app


class AuthDependencyTests(unittest.TestCase):
    def credentials(self, token: str = "verified-jwt") -> HTTPAuthorizationCredentials:
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    def test_verified_user_returns_canonical_id_and_token(self) -> None:
        user_id = uuid.uuid4()
        auth = Mock()
        auth.get_user.return_value = SimpleNamespace(
            user=SimpleNamespace(id=str(user_id).upper())
        )
        auth_client = SimpleNamespace(auth=auth)

        with patch.object(dependencies, "get_anon_supabase", return_value=auth_client):
            current_user = dependencies.get_current_user(
                self.credentials("signed-user-token")
            )

        self.assertEqual(current_user.id, str(user_id))
        self.assertEqual(current_user.user_id, str(user_id))
        self.assertEqual(current_user.access_token, "signed-user-token")
        self.assertEqual(current_user.token, "signed-user-token")
        auth.get_user.assert_called_once_with("signed-user-token")

    def test_missing_bearer_token_is_clean_401(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            dependencies.get_current_user(None)

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.headers, {"WWW-Authenticate": "Bearer"})
        self.assertNotIn("500", str(raised.exception.detail))

    def test_expired_or_invalid_token_is_clean_401(self) -> None:
        auth = Mock()
        auth.get_user.side_effect = RuntimeError("JWT expired")
        auth_client = SimpleNamespace(auth=auth)

        with (
            patch.object(dependencies, "get_anon_supabase", return_value=auth_client),
            self.assertRaises(HTTPException) as raised,
        ):
            dependencies.get_current_user(self.credentials("expired-token"))

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.detail, "Invalid or expired access token")
        self.assertNotIn("JWT expired", raised.exception.detail)

    def test_missing_or_malformed_auth_user_is_rejected(self) -> None:
        for response in (
            None,
            SimpleNamespace(user=None),
            {"user": {"id": "not-a-uuid"}},
            {"user": {"id": str(uuid.UUID(int=0))}},
        ):
            with self.subTest(response=response):
                auth = Mock()
                auth.get_user.return_value = response
                auth_client = SimpleNamespace(auth=auth)
                with (
                    patch.object(
                        dependencies,
                        "get_anon_supabase",
                        return_value=auth_client,
                    ),
                    self.assertRaises(HTTPException) as raised,
                ):
                    dependencies.get_current_user(self.credentials())
                self.assertEqual(raised.exception.status_code, 401)


class SupabaseClientBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        database._anon_client = None
        database._service_client = None

    def tearDown(self) -> None:
        database._anon_client = None
        database._service_client = None

    def test_anon_and_service_clients_use_distinct_keys(self) -> None:
        anon_client = object()
        service_client = object()
        with (
            patch.object(database, "SUPABASE_URL", "https://project.test"),
            patch.object(database, "SUPABASE_ANON_KEY", "browser-safe-key"),
            patch.object(database, "SUPABASE_SERVICE_ROLE_KEY", "service-secret"),
            patch.object(
                database,
                "create_client",
                side_effect=[anon_client, service_client],
            ) as create,
        ):
            self.assertIs(database.get_anon_supabase(), anon_client)
            self.assertIs(database.get_anon_supabase(), anon_client)
            self.assertIs(database.get_service_supabase(), service_client)
            self.assertIs(database.get_service_supabase(), service_client)

        self.assertEqual(create.call_count, 2)
        self.assertEqual(
            create.call_args_list[0].args,
            ("https://project.test", "browser-safe-key"),
        )
        self.assertEqual(
            create.call_args_list[1].args,
            ("https://project.test", "service-secret"),
        )

    def test_user_client_carries_jwt_on_anon_key_and_is_not_shared(self) -> None:
        with (
            patch.object(database, "SUPABASE_URL", "https://project.test"),
            patch.object(database, "SUPABASE_ANON_KEY", "browser-safe-key"),
            patch.object(
                database,
                "create_client",
                side_effect=[object(), object()],
            ) as create,
        ):
            first = database.get_user_supabase("user-a-jwt")
            second = database.get_user_supabase("user-b-jwt")

        self.assertIsNot(first, second)
        self.assertEqual(create.call_count, 2)
        for call, token in zip(create.call_args_list, ("user-a-jwt", "user-b-jwt")):
            self.assertEqual(call.args, ("https://project.test", "browser-safe-key"))
            options = call.kwargs["options"]
            self.assertEqual(
                options.headers["Authorization"],
                f"Bearer {token}",
            )
            self.assertFalse(options.auto_refresh_token)
            self.assertFalse(options.persist_session)

    def test_user_client_rejects_missing_token(self) -> None:
        for value in ("", "   ", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                database.get_user_supabase(value)  # type: ignore[arg-type]

    async def test_bound_client_is_nested_exception_safe_and_reaches_to_thread(self) -> None:
        user_a_client = object()
        user_b_client = object()

        with self.assertRaisesRegex(RuntimeError, "No Supabase client is bound"):
            database.get_supabase()
        with database.use_supabase(user_a_client):
            self.assertIs(database.get_supabase(), user_a_client)
            self.assertIs(
                await asyncio.to_thread(database.get_supabase),
                user_a_client,
            )
            with self.assertRaisesRegex(RuntimeError, "worker failed"):
                with database.use_supabase(user_b_client):
                    self.assertIs(database.get_supabase(), user_b_client)
                    raise RuntimeError("worker failed")
            self.assertIs(database.get_supabase(), user_a_client)
        with self.assertRaisesRegex(RuntimeError, "No Supabase client is bound"):
            database.get_supabase()


class MigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "004_phase3_product.sql"
        )
        cls.sql = migration_path.read_text(encoding="utf-8").lower()

    def test_all_user_data_tables_enable_rls_and_have_owner_policies(self) -> None:
        tables = (
            "papers",
            "chunks",
            "audits",
            "rounds",
            "turns",
            "verdicts",
            "debrief_cards",
            "final_reports",
            "version_diffs",
        )
        for table in tables:
            with self.subTest(table=table):
                self.assertIn(
                    f"alter table public.{table} enable row level security",
                    self.sql,
                )
                self.assertRegex(
                    self.sql,
                    rf"create policy [a-z0-9_]+ on public\.{table}\s+for select to authenticated",
                )
        self.assertNotIn("for all to authenticated", self.sql)

    def test_authenticated_role_is_read_only_for_trusted_product_tables(self) -> None:
        product_tables = (
            "public.papers",
            "public.chunks",
            "public.audits",
            "public.rounds",
            "public.turns",
            "public.verdicts",
            "public.debrief_cards",
            "public.final_reports",
            "public.version_diffs",
        )
        compact_sql = " ".join(self.sql.split())
        revoked_block = (
            "revoke all on table "
            + ", ".join(product_tables)
            + " from authenticated;"
        )
        select_block = (
            "grant select on table " + ", ".join(product_tables) + " to authenticated;"
        )
        self.assertIn(revoked_block, compact_sql)
        self.assertIn(select_block, compact_sql)
        self.assertNotRegex(
            compact_sql,
            r"grant\s+(?:insert|update|delete|select\s*,\s*insert)[^;]*to authenticated",
        )

    def test_storage_is_private_owner_scoped_and_read_only(self) -> None:
        self.assertIn("values ('papers', 'papers', false)", self.sql)
        self.assertIn('drop policy if exists "public access"', self.sql)
        self.assertRegex(
            self.sql,
            r"create policy papers_storage_owner_select[\s\S]*?for select to authenticated",
        )
        for operation in ("insert", "update", "delete"):
            with self.subTest(operation=operation):
                self.assertNotIn(
                    f"create policy papers_storage_owner_{operation}",
                    self.sql,
                )
        self.assertEqual(
            self.sql.count("(storage.foldername(name))[1] = (select auth.uid())::text"),
            1,
        )
        compact_sql = " ".join(self.sql.split())
        self.assertIn(
            "revoke all on table storage.objects from anon;",
            compact_sql,
        )
        self.assertIn(
            "revoke all on table storage.objects from authenticated;",
            compact_sql,
        )
        self.assertIn(
            "grant select on table storage.objects to authenticated;",
            compact_sql,
        )

    def test_vector_rpc_is_invoker_scoped_and_not_public(self) -> None:
        compact_sql = " ".join(self.sql.split())
        signature = "public.match_chunks(text, uuid, integer)"
        self.assertIn(
            f"alter function {signature} security invoker;",
            compact_sql,
        )
        self.assertIn(
            f"revoke all on function {signature} from public;",
            compact_sql,
        )
        self.assertIn(
            f"revoke all on function {signature} from anon;",
            compact_sql,
        )
        self.assertIn(
            f"grant execute on function {signature} to authenticated, service_role;",
            compact_sql,
        )

    def test_product_schema_replaces_anonymous_ownership(self) -> None:
        self.assertGreaterEqual(
            self.sql.count("drop column if exists session_id"),
            2,
        )
        self.assertIn("create table if not exists public.final_reports", self.sql)
        self.assertIn("create table if not exists public.version_diffs", self.sql)
        self.assertIn("foreign key (paper_id, user_id)", self.sql)
        self.assertIn("foreign key (parent_paper_id, user_id)", self.sql)
        self.assertIn("check (version_number >= 1)", self.sql)
        self.assertIn("check (mode in ('author', 'reviewer_assist'))", self.sql)
        self.assertIn("security invoker", self.sql)

    def test_every_create_index_statement_is_complete_and_unique(self) -> None:
        """Catch malformed copy/paste DDL before it reaches Supabase."""
        index_names: list[str] = []
        for statement in self.sql.split(";"):
            normalized = " ".join(statement.split())
            create_count = normalized.count("create index if not exists")
            unique_count = normalized.count("create unique index if not exists")
            if create_count + unique_count == 0:
                continue
            self.assertEqual(
                create_count + unique_count,
                1,
                f"multiple CREATE INDEX clauses share one statement: {normalized}",
            )
            self.assertRegex(
                normalized,
                r"create (?:unique )?index if not exists [a-z0-9_]+ "
                r"on public\.[a-z0-9_]+\s*\(",
            )
            tokens = normalized.split()
            index_names.append(tokens[tokens.index("exists") + 1])

        self.assertEqual(len(index_names), len(set(index_names)))


_LIVE_RLS_BASE_ENV = (
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
)
_LIVE_RLS_USER_ENV = (
    "VERDICT_RLS_TEST_USER_A_EMAIL",
    "VERDICT_RLS_TEST_USER_A_PASSWORD",
    "VERDICT_RLS_TEST_USER_B_EMAIL",
    "VERDICT_RLS_TEST_USER_B_PASSWORD",
)


@unittest.skipUnless(
    os.environ.get("VERDICT_RUN_LIVE_RLS") == "1"
    and all(os.environ.get(name) for name in _LIVE_RLS_BASE_ENV),
    "set VERDICT_RUN_LIVE_RLS=1 and Supabase keys for the live RLS check",
)
class LiveCrossAccountRLSTests(unittest.TestCase):
    """PostgREST integration check to run after applying migration 004.

    Both users log in through Supabase Auth. The backend identity seeds User A's
    paper/audit, then both JWT clients exercise direct PostgREST access. This proves
    row isolation and read-only artifact protection at the database boundary.
    When dedicated credentials are not supplied, the test creates two confirmed
    temporary users through the service admin API and removes them afterwards.
    """

    _temporary_user_ids: list[str] = []
    _temporary_env_names: list[str] = []

    @classmethod
    def setUpClass(cls) -> None:
        supplied = [bool(os.environ.get(name)) for name in _LIVE_RLS_USER_ENV]
        if any(supplied) and not all(supplied):
            raise RuntimeError(
                "Either set all four VERDICT_RLS_TEST_USER_* values or none of them."
            )
        if all(supplied):
            return

        import secrets

        from supabase import create_client

        service_client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        )
        credentials: list[tuple[str, str]] = []
        try:
            for label in ("a", "b"):
                email = f"verdict-rls-{label}-{uuid.uuid4().hex}@example.com"
                password = f"V3-{secrets.token_urlsafe(24)}-aA1!"
                response = service_client.auth.admin.create_user(
                    {
                        "email": email,
                        "password": password,
                        "email_confirm": True,
                    }
                )
                user = getattr(response, "user", None)
                if user is None or not getattr(user, "id", None):
                    raise RuntimeError("Supabase did not return the temporary user")
                cls._temporary_user_ids.append(str(user.id))
                credentials.append((email, password))
        except Exception:
            for user_id in reversed(cls._temporary_user_ids):
                try:
                    service_client.auth.admin.delete_user(user_id)
                except Exception:
                    pass
            cls._temporary_user_ids.clear()
            raise

        values = {
            "VERDICT_RLS_TEST_USER_A_EMAIL": credentials[0][0],
            "VERDICT_RLS_TEST_USER_A_PASSWORD": credentials[0][1],
            "VERDICT_RLS_TEST_USER_B_EMAIL": credentials[1][0],
            "VERDICT_RLS_TEST_USER_B_PASSWORD": credentials[1][1],
        }
        for name, value in values.items():
            os.environ[name] = value
            cls._temporary_env_names.append(name)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._temporary_user_ids:
            from supabase import create_client

            service_client = create_client(
                os.environ["SUPABASE_URL"],
                os.environ["SUPABASE_SERVICE_ROLE_KEY"],
            )
            for user_id in reversed(cls._temporary_user_ids):
                service_client.auth.admin.delete_user(user_id)
        for name in cls._temporary_env_names:
            os.environ.pop(name, None)
        cls._temporary_user_ids.clear()
        cls._temporary_env_names.clear()

    def test_second_authenticated_account_cannot_access_first_accounts_rows(self) -> None:
        from supabase import create_client

        url = os.environ["SUPABASE_URL"]
        anon_key = os.environ["SUPABASE_ANON_KEY"]
        auth_a = create_client(url, anon_key).auth.sign_in_with_password(
            {
                "email": os.environ["VERDICT_RLS_TEST_USER_A_EMAIL"],
                "password": os.environ["VERDICT_RLS_TEST_USER_A_PASSWORD"],
            }
        )
        auth_b = create_client(url, anon_key).auth.sign_in_with_password(
            {
                "email": os.environ["VERDICT_RLS_TEST_USER_B_EMAIL"],
                "password": os.environ["VERDICT_RLS_TEST_USER_B_PASSWORD"],
            }
        )
        self.assertIsNotNone(auth_a.session)
        self.assertIsNotNone(auth_b.session)
        self.assertIsNotNone(auth_a.user)
        self.assertIsNotNone(auth_b.user)

        client_a = database.get_user_supabase(auth_a.session.access_token)
        client_b = database.get_user_supabase(auth_b.session.access_token)
        service_client = create_client(
            url,
            os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        )
        paper_id = str(uuid.uuid4())
        chunk_id = str(uuid.uuid4())
        comparison_audit_id = str(uuid.uuid4())
        audit_id = str(uuid.uuid4())
        round_id = str(uuid.uuid4())
        turn_id = str(uuid.uuid4())
        verdict_id = str(uuid.uuid4())
        debrief_id = str(uuid.uuid4())
        report_id = str(uuid.uuid4())
        diff_id = str(uuid.uuid4())
        storage_path = f"{auth_a.user.id}/rls-{paper_id}.pdf"

        try:
            # Production writes use the verified backend identity. Seed through
            # the same boundary, then exercise reads as both authenticated users.
            service_client.table("papers").insert(
                {
                    "id": paper_id,
                    "user_id": str(auth_a.user.id),
                    "filename": "rls-isolation-test.pdf",
                    "storage_path": storage_path,
                }
            ).execute()
            service_client.table("chunks").insert(
                {
                    "id": chunk_id,
                    "paper_id": paper_id,
                    "section": "Methods",
                    "text": "RLS integration test chunk.",
                    "embedding": [0.0] * 384,
                    "page_number": 1,
                    "chunk_index": 0,
                }
            ).execute()
            service_client.table("audits").insert(
                {
                    "id": comparison_audit_id,
                    "paper_id": paper_id,
                    "user_id": str(auth_a.user.id),
                    "round_topic": "theoretical_soundness",
                    "status": "completed",
                }
            ).execute()
            service_client.table("audits").insert(
                {
                    "id": audit_id,
                    "paper_id": paper_id,
                    "user_id": str(auth_a.user.id),
                    "round_topic": "theoretical_soundness",
                    "status": "completed",
                }
            ).execute()
            service_client.table("rounds").insert(
                {
                    "id": round_id,
                    "audit_id": audit_id,
                    "round_number": 1,
                    "topic": "theoretical_soundness",
                    "status": "completed",
                }
            ).execute()
            service_client.table("turns").insert(
                {
                    "id": turn_id,
                    "round_id": round_id,
                    "exchange_number": 1,
                    "agent_type": "attacker",
                    "sequence": 1,
                    "content": {"claim": "RLS integration test"},
                }
            ).execute()
            service_client.table("verdicts").insert(
                {
                    "id": verdict_id,
                    "round_id": round_id,
                    "exchange_number": 1,
                    "claim_summary": "RLS integration test",
                    "verdict_type": "CONTESTED",
                    "confidence": 0.5,
                    "rationale": "Fixture",
                    "cited_chunk_ids": [chunk_id],
                }
            ).execute()
            service_client.table("debrief_cards").insert(
                {
                    "id": debrief_id,
                    "round_id": round_id,
                    "executive_synthesis": "RLS integration fixture.",
                    "solidified_strengths": [],
                    "actionable_weaknesses": [],
                    "contested_points": [],
                }
            ).execute()
            service_client.table("final_reports").insert(
                {
                    "id": report_id,
                    "audit_id": audit_id,
                    "mode": "author",
                    "content": "# RLS integration fixture",
                }
            ).execute()
            service_client.table("version_diffs").insert(
                {
                    "id": diff_id,
                    "audit_id_old": comparison_audit_id,
                    "audit_id_new": audit_id,
                    "round_topic": "theoretical_soundness",
                    "diff_summary": "No cross-account visibility.",
                }
            ).execute()
            service_client.storage.from_("papers").upload(
                path=storage_path,
                file=b"%PDF-1.4\n% RLS fixture\n",
                file_options={"content-type": "application/pdf", "upsert": "false"},
            )

            protected_rows = {
                "papers": paper_id,
                "chunks": chunk_id,
                "audits": audit_id,
                "rounds": round_id,
                "turns": turn_id,
                "verdicts": verdict_id,
                "debrief_cards": debrief_id,
                "final_reports": report_id,
                "version_diffs": diff_id,
            }
            for table, row_id in protected_rows.items():
                with self.subTest(table=table):
                    own = (
                        client_a.table(table)
                        .select("id")
                        .eq("id", row_id)
                        .execute()
                    )
                    other = (
                        client_b.table(table)
                        .select("id")
                        .eq("id", row_id)
                        .execute()
                    )
                    self.assertEqual([row["id"] for row in own.data], [row_id])
                    self.assertEqual(other.data, [])

            object_name = storage_path.rsplit("/", 1)[-1]
            own_objects = client_a.storage.from_("papers").list(str(auth_a.user.id))
            other_objects = client_b.storage.from_("papers").list(str(auth_a.user.id))
            self.assertIn(object_name, {row["name"] for row in own_objects})
            self.assertNotIn(object_name, {row["name"] for row in other_objects})

            # Exercise the same isolation through the product API, not only
            # raw PostgREST. These use the real Supabase access tokens, so the
            # dependency and per-request RLS-client binding are both covered.
            api = TestClient(app)
            headers_a = {"Authorization": f"Bearer {auth_a.session.access_token}"}
            headers_b = {"Authorization": f"Bearer {auth_b.session.access_token}"}

            papers_a = api.get("/papers", headers=headers_a)
            audits_a = api.get("/audits", headers=headers_a)
            self.assertEqual(papers_a.status_code, 200, papers_a.text)
            self.assertEqual(audits_a.status_code, 200, audits_a.text)
            self.assertIn(paper_id, {row["id"] for row in papers_a.json()})
            self.assertIn(audit_id, {row["audit_id"] for row in audits_a.json()})

            papers_b = api.get("/papers", headers=headers_b)
            audits_b = api.get("/audits", headers=headers_b)
            hidden_turns = api.get(
                f"/audits/{audit_id}/turns",
                headers=headers_b,
            )
            self.assertEqual(papers_b.status_code, 200, papers_b.text)
            self.assertEqual(audits_b.status_code, 200, audits_b.text)
            self.assertNotIn(paper_id, {row["id"] for row in papers_b.json()})
            self.assertNotIn(
                audit_id,
                {row["audit_id"] for row in audits_b.json()},
            )
            self.assertEqual(hidden_turns.status_code, 404, hidden_turns.text)

            # Authenticated clients are read-only, including for their own rows;
            # browser callers cannot rewrite a paper or forge trusted turns.
            with self.assertRaises(Exception):
                client_a.table("papers").update(
                    {"filename": "forged-owner-write.pdf"}
                ).eq("id", paper_id).execute()
            with self.assertRaises(Exception):
                client_a.table("turns").insert(
                    {
                        "round_id": round_id,
                        "exchange_number": 1,
                        "agent_type": "referee",
                        "sequence": 1,
                        "content": {"forged": True},
                    }
                ).execute()
            with self.assertRaises(Exception):
                client_b.table("papers").update(
                    {"filename": "cross-account-write.pdf"}
                ).eq("id", paper_id).execute()
        finally:
            try:
                service_client.storage.from_("papers").remove([storage_path])
            finally:
                service_client.table("papers").delete().eq("id", paper_id).execute()
