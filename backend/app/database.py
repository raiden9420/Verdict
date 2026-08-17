"""Supabase clients with an explicit Row-Level Security boundary.

Normal request and audit-worker code should create a user client with
``get_user_supabase`` and bind it with ``use_supabase``. Legacy services still
call ``get_supabase``; the context binding lets those services participate in
the caller's RLS transaction boundary without threading a client argument
through the audited Phase 1/2 graph.

Only startup/admin work which intentionally spans users should call
``get_service_supabase``. ``get_supabase`` fails closed when no client has been
bound, so forgetting a request/worker context can never silently become an RLS
bypass.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

from supabase import Client, ClientOptions, create_client

from app.config import (
    SUPABASE_ANON_KEY,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
)

_anon_client: Client | None = None
_service_client: Client | None = None
_bound_client: ContextVar[Client | None] = ContextVar(
    "verdict_supabase_client",
    default=None,
)


def get_anon_supabase() -> Client:
    """Return the unprivileged client used to verify Supabase Auth tokens."""
    global _anon_client
    if _anon_client is None:
        _anon_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _anon_client


def get_user_supabase(access_token: str) -> Client:
    """Create an isolated client whose database/storage calls exercise RLS.

    A client is intentionally created per authenticated request or background
    job. Mutating a shared client's auth header would allow concurrent requests
    to race and run as the wrong user.
    """
    token = str(access_token or "").strip()
    if not token:
        raise ValueError("A Supabase access token is required")

    options = ClientOptions(
        headers={"Authorization": f"Bearer {token}"},
        auto_refresh_token=False,
        persist_session=False,
    )
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY, options=options)


def get_service_supabase() -> Client:
    """Return the privileged cross-user client for explicit admin work only."""
    global _service_client
    if _service_client is None:
        _service_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _service_client


@contextmanager
def use_supabase(client: Client) -> Iterator[Client]:
    """Bind ``client`` to this execution context and restore it reliably."""
    if client is None:
        raise ValueError("A Supabase client is required")
    reset_token = _bound_client.set(client)
    try:
        yield client
    finally:
        _bound_client.reset(reset_token)


def get_supabase() -> Client:
    """Return the explicitly bound request/worker client or fail closed."""
    client = _bound_client.get()
    if client is None:
        raise RuntimeError(
            "No Supabase client is bound to this execution context. "
            "Use use_supabase(...) or call get_service_supabase() explicitly "
            "for intentional admin work."
        )
    return client
