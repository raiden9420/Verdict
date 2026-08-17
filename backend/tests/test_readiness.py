from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app
from app.services import readiness_service


class _TableQuery:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.columns: str | None = None

    def select(self, columns: str) -> "_TableQuery":
        self.columns = columns
        return self

    def limit(self, _count: int) -> "_TableQuery":
        return self

    def execute(self) -> SimpleNamespace:
        if self.error is not None:
            raise self.error
        return SimpleNamespace(data=[])


class _ReadinessClient:
    def __init__(
        self,
        *,
        table_error: Exception | None = None,
        bucket: object | None = None,
    ) -> None:
        self.table_error = table_error
        self.tables: list[str] = []
        self.storage = Mock()
        self.storage.get_bucket.return_value = (
            {"id": "papers", "public": False} if bucket is None else bucket
        )

    def table(self, name: str) -> _TableQuery:
        self.tables.append(name)
        return _TableQuery(error=self.table_error)


class ReadinessServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        readiness_service.reset_readiness_cache()

    def tearDown(self) -> None:
        readiness_service.reset_readiness_cache()

    def test_success_checks_product_tables_and_private_bucket_then_caches(self) -> None:
        client = _ReadinessClient()
        with patch.object(
            readiness_service,
            "get_service_supabase",
            return_value=client,
        ) as get_client:
            readiness_service.verify_phase3_readiness()
            readiness_service.verify_phase3_readiness()

        self.assertEqual(
            client.tables,
            ["papers", "audits", "final_reports", "version_diffs"],
        )
        client.storage.get_bucket.assert_called_once_with("papers")
        get_client.assert_called_once_with()

    def test_schema_failure_is_sanitized_and_not_cached(self) -> None:
        client = _ReadinessClient(table_error=RuntimeError("secret database detail"))
        with patch.object(
            readiness_service,
            "get_service_supabase",
            return_value=client,
        ):
            for _ in range(2):
                with self.assertRaises(readiness_service.ReadinessError) as raised:
                    readiness_service.verify_phase3_readiness()
                self.assertEqual(raised.exception.component, "database_schema")
                self.assertNotIn("secret database detail", str(raised.exception))

        self.assertEqual(client.tables, ["papers", "papers"])

    def test_public_storage_bucket_is_not_ready(self) -> None:
        client = _ReadinessClient(bucket={"id": "papers", "public": True})
        with (
            patch.object(
                readiness_service,
                "get_service_supabase",
                return_value=client,
            ),
            self.assertRaises(readiness_service.ReadinessError) as raised,
        ):
            readiness_service.verify_phase3_readiness()

        self.assertEqual(raised.exception.component, "paper_storage")


class ReadinessRouteTests(unittest.TestCase):
    def test_health_identifies_phase3_release(self) -> None:
        response = TestClient(app).get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "version": "0.3.0", "phase": 3},
        )

    def test_ready_returns_sanitized_503(self) -> None:
        failure = readiness_service.ReadinessError(
            "database_schema",
            "The Phase 3 database schema is unavailable. Apply all migrations.",
        )
        with patch("app.main.verify_phase3_readiness", side_effect=failure):
            response = TestClient(app).get("/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")
        self.assertEqual(response.json()["component"], "database_schema")
        self.assertNotIn("traceback", response.text.lower())

    def test_ready_returns_release_identity(self) -> None:
        with patch("app.main.verify_phase3_readiness"):
            response = TestClient(app).get("/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ready", "version": "0.3.0", "phase": 3},
        )


if __name__ == "__main__":
    unittest.main()
