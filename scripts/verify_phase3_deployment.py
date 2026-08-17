#!/usr/bin/env python3
"""Read-only smoke test for a deployed Verdict Phase 3 release.

The script deliberately performs no uploads or audit runs. Set
``VERDICT_SMOKE_ACCESS_TOKEN`` to include authenticated list checks without
putting a JWT in shell history. Cross-account RLS is verified separately by the
opt-in integration test documented in README.md.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

try:
    import certifi
except ImportError:  # pragma: no cover - system trust store remains supported
    certifi = None


EXPECTED_VERSION = "0.3.0"
EXPECTED_PATH_METHODS = {
    "/papers": ("get", "post"),
    "/papers/{paper_id}/pdf-url": ("get",),
    "/audits": ("get", "post"),
    "/audits/{audit_id}/stream": ("get",),
    "/audits/{audit_id}/turns": ("get",),
    "/audits/{audit_id}/debriefs": ("get",),
    "/audits/{audit_id}/final-report": ("get",),
    "/audits/{audit_id}/final-report/markdown": ("get",),
    "/audits/{audit_id}/version-diffs": ("get", "post"),
}

SSL_CONTEXT = ssl.create_default_context(
    cafile=certifi.where() if certifi is not None else None
)


@dataclass(slots=True)
class Response:
    status: int
    headers: Any
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class CheckFailure(RuntimeError):
    pass


def request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: float = 30,
) -> Response:
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(
            req,
            timeout=timeout,
            context=SSL_CONTEXT,
        ) as result:
            return Response(result.status, result.headers, result.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, exc.headers, exc.read())
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CheckFailure(f"could not reach {url}: {exc.reason if hasattr(exc, 'reason') else exc}") from exc


def endpoint(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def verify_release_identity(api_url: str, timeout: float) -> None:
    health = request(endpoint(api_url, "/health"), timeout=timeout)
    expect(health.status == 200, f"GET /health returned HTTP {health.status}")
    try:
        payload = health.json()
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckFailure("GET /health did not return JSON") from exc
    expect(payload.get("status") == "ok", "GET /health did not report status=ok")
    expect(payload.get("phase") == 3, "backend is not identifying itself as Phase 3")
    expect(
        payload.get("version") == EXPECTED_VERSION,
        f"backend version is {payload.get('version')!r}, expected {EXPECTED_VERSION!r}",
    )


def verify_readiness(api_url: str, timeout: float) -> None:
    response = request(endpoint(api_url, "/ready"), timeout=timeout)
    try:
        payload = response.json()
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckFailure(f"GET /ready returned HTTP {response.status} without JSON") from exc
    if response.status != 200:
        component = payload.get("component", "unknown")
        detail = payload.get("detail", "no detail")
        raise CheckFailure(
            f"GET /ready returned HTTP {response.status}: {component}: {detail}"
        )
    expect(payload.get("status") == "ready", "GET /ready did not report ready")


def verify_openapi(api_url: str, timeout: float) -> None:
    response = request(endpoint(api_url, "/openapi.json"), timeout=timeout)
    expect(response.status == 200, f"GET /openapi.json returned HTTP {response.status}")
    try:
        document = response.json()
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckFailure("GET /openapi.json did not return JSON") from exc

    expect(
        document.get("info", {}).get("version") == EXPECTED_VERSION,
        "OpenAPI version does not match the Phase 3 release",
    )
    schemes = document.get("components", {}).get("securitySchemes", {})
    expect(
        any(
            scheme.get("type") == "http" and scheme.get("scheme") == "bearer"
            for scheme in schemes.values()
            if isinstance(scheme, dict)
        ),
        "OpenAPI has no HTTP Bearer security scheme",
    )

    paths = document.get("paths", {})
    for path, methods in EXPECTED_PATH_METHODS.items():
        expect(path in paths, f"OpenAPI is missing {path}")
        for method in methods:
            operation = paths[path].get(method)
            expect(operation is not None, f"OpenAPI is missing {method.upper()} {path}")
            expect(
                bool(operation.get("security")),
                f"{method.upper()} {path} is not marked as authenticated",
            )


def verify_unauthenticated_boundary(api_url: str, timeout: float) -> None:
    for path in ("/papers", "/audits"):
        response = request(endpoint(api_url, path), timeout=timeout)
        expect(response.status == 401, f"unauthenticated GET {path} returned HTTP {response.status}")
        authenticate = (response.headers.get("WWW-Authenticate") or "").lower()
        expect("bearer" in authenticate, f"GET {path} omitted the Bearer challenge")


def verify_authenticated_lists(api_url: str, timeout: float, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    for path in ("/papers", "/audits"):
        response = request(endpoint(api_url, path), headers=headers, timeout=timeout)
        expect(response.status == 200, f"authenticated GET {path} returned HTTP {response.status}")
        try:
            payload = response.json()
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CheckFailure(f"authenticated GET {path} did not return JSON") from exc
        expect(isinstance(payload, list), f"authenticated GET {path} did not return a list")


def verify_cors(api_url: str, frontend_url: str, timeout: float) -> None:
    origin = frontend_url.rstrip("/")
    response = request(
        endpoint(api_url, "/papers"),
        method="OPTIONS",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
        timeout=timeout,
    )
    expect(response.status == 200, f"CORS preflight returned HTTP {response.status}")
    expect(
        response.headers.get("Access-Control-Allow-Origin") == origin,
        "CORS does not allow the deployed frontend origin",
    )
    allowed_headers = (response.headers.get("Access-Control-Allow-Headers") or "").lower()
    expect("authorization" in allowed_headers, "CORS does not allow Authorization")

    frontend = request(origin, timeout=timeout)
    expect(frontend.status == 200, f"frontend returned HTTP {frontend.status}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url",
        default=os.environ.get("VERDICT_API_URL"),
        help="deployed backend base URL (or set VERDICT_API_URL)",
    )
    parser.add_argument(
        "--frontend-url",
        default=os.environ.get("VERDICT_FRONTEND_URL"),
        help="optional deployed frontend URL for CORS and availability checks",
    )
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument(
        "--require-authenticated",
        action="store_true",
        help="fail unless VERDICT_SMOKE_ACCESS_TOKEN is set and accepted",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.api_url:
        print("FAIL  --api-url or VERDICT_API_URL is required", file=sys.stderr)
        return 2

    checks = [
        ("release identity", lambda: verify_release_identity(args.api_url, args.timeout)),
        ("schema/storage readiness", lambda: verify_readiness(args.api_url, args.timeout)),
        ("Phase 3 API contract", lambda: verify_openapi(args.api_url, args.timeout)),
        ("unauthenticated boundary", lambda: verify_unauthenticated_boundary(args.api_url, args.timeout)),
    ]
    if args.frontend_url:
        checks.append(
            (
                "frontend availability and CORS",
                lambda: verify_cors(args.api_url, args.frontend_url, args.timeout),
            )
        )

    access_token = os.environ.get("VERDICT_SMOKE_ACCESS_TOKEN", "").strip()
    if access_token:
        checks.append(
            (
                "authenticated API lists",
                lambda: verify_authenticated_lists(args.api_url, args.timeout, access_token),
            )
        )
    elif args.require_authenticated:
        print("FAIL  VERDICT_SMOKE_ACCESS_TOKEN is required", file=sys.stderr)
        return 2

    failed = False
    for name, check in checks:
        try:
            check()
        except CheckFailure as exc:
            failed = True
            print(f"FAIL  {name}: {exc}")
        except Exception as exc:  # defensive: keep every check visible
            failed = True
            print(f"FAIL  {name}: unexpected {type(exc).__name__}: {exc}")
        else:
            print(f"PASS  {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
