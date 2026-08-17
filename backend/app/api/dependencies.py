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
    an unverified local decode. All authentication failures intentionally map to
    the same clean 401 so expired tokens never surface as downstream 500s.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Missing Bearer access token")

    access_token = credentials.credentials.strip()
    if not access_token:
        raise _unauthorized("Missing Bearer access token")

    try:
        response = get_anon_supabase().auth.get_user(access_token)
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
