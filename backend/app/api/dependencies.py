"""
Shared FastAPI dependencies.
"""

import uuid

from fastapi import Header, HTTPException, Query


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


def get_session_id(x_session_id: str = Header(default=None)) -> str:
    """
    Extract the anonymous session UUID from the X-Session-Id header.
    Phase 1 has no auth — this is just a browser-scoped identifier
    stored in localStorage.
    """
    if not x_session_id:
        raise HTTPException(
            status_code=400,
            detail="Missing X-Session-Id header. "
            "The frontend should generate a UUID on first visit.",
        )
    return canonical_uuid(x_session_id, "X-Session-Id")


def get_session_query_id(session_id: str = Query(...)) -> str:
    """Validate the session query token used by EventSource and the PDF viewer."""
    return canonical_uuid(session_id, "session_id")
