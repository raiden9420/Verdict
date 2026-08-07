"""
Shared FastAPI dependencies.
"""

from fastapi import Header, HTTPException


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
    return x_session_id
