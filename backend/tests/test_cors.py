from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app


class ProductionCorsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def preflight(self, origin: str):
        return self.client.options(
            "/audits",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    def test_stable_production_alias_is_allowed(self) -> None:
        origin = "https://verdict-nu-rouge.vercel.app"
        response = self.preflight(origin)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get("access-control-allow-origin"), origin)

    def test_project_scoped_vercel_deployment_url_is_allowed(self) -> None:
        origin = "https://verdict-321rr79t3-response-composer.vercel.app"
        response = self.preflight(origin)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get("access-control-allow-origin"), origin)

    def test_lookalike_and_other_vercel_origins_are_rejected(self) -> None:
        origins = (
            "https://verdict-321rr79t3-response-composer.vercel.app.evil.example",
            "https://verdict-321rr79t3-another-team.vercel.app",
            "https://attacker.vercel.app",
        )
        for origin in origins:
            with self.subTest(origin=origin):
                response = self.preflight(origin)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIsNone(response.headers.get("access-control-allow-origin"))


if __name__ == "__main__":
    unittest.main()
