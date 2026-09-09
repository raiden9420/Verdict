"""
Shared FastAPI dependencies.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.database import get_anon_supabase


_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Verified request identity and the token needed for an RLS client."""

    id: str
    access_token: str

    @property
    def user_id(self) -> str:
        """Readable alias for call sites that persist ownership columns."""
        return self.id

    @property
    def token(self) -> str:
        """Compatibility alias for integrations that call it a bearer token."""
        return self.access_token


def canonical_uuid(value: str, field_name: str) -> str:
    """Validate an externally supplied UUID and return its canonical form."""
    try:
        parsed = uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be a valid UUID.",
        ) from exc
    if parsed.int == 0:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must not be the nil UUID.",
        )
    return str(parsed)


def _unauthorized(detail: str = "Invalid or expired access token") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _response_user(response: Any) -> Any:
    """Read SDK response objects while keeping dependency tests mock-friendly."""
    if isinstance(response, dict):
        return response.get("user")
    return getattr(response, "user", None)


def _user_id(user: Any) -> Any:
    if isinstance(user, dict):
        return user.get("id")
    return getattr(user, "id", None)


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ] = None,
) -> CurrentUser:
    """Verify a Supabase Bearer JWT and return its canonical user identity.

    ``auth.get_user`` validates the token with Supabase Auth rather than trusting
    an unverified local decode. Only a definitive authentication rejection is a
    401: an Auth outage must not cause the browser to discard a valid session.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Missing Bearer access token")

    access_token = credentials.credentials.strip()
    if not access_token:
        raise _unauthorized("Missing Bearer access token")

    try:
        response = get_anon_supabase().auth.get_user(access_token)
    except Exception as exc:
        # Supabase Auth API errors expose ``status``; HTTPX status errors use
        # ``response.status_code``. Do not classify arbitrary exception text
        # (which may contain a provider body or a token) as an expired session.
        raw_status = getattr(exc, "status", None)
        if raw_status is None:
            raw_status = getattr(getattr(exc, "response", None), "status_code", None)
        invalid_token_codes = {
            "bad_jwt", "invalid_jwt", "no_authorization", "session_not_found",
            "user_not_found", "user_banned", "unexpected_audience",
        }
        if str(raw_status) in {"401", "403"} or (
            str(raw_status) in {"400", "404"}
            and getattr(exc, "code", None) in invalid_token_codes
        ):
            raise _unauthorized() from exc
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sign-in verification is temporarily unavailable. Please retry shortly.",
            headers={"Retry-After": "5"},
        ) from exc

    try:
        raw_user_id = _user_id(_response_user(response))
        parsed_user_id = uuid.UUID(str(raw_user_id))
        if parsed_user_id.int == 0:
            raise ValueError("nil user UUID")
    except Exception as exc:
        raise _unauthorized() from exc

    return CurrentUser(
        id=str(parsed_user_id),
        access_token=access_token,
    )
