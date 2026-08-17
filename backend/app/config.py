"""
Application configuration — loaded from environment variables.
Never import secrets directly; always go through this module.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Ensure .env inside backend/ is located regardless of CWD
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
SUPABASE_URL: str = os.environ.get("SUPABASE_URL", "")

# PostgREST only evaluates ``auth.uid()`` when the request carries the user's
# access token and the project client was created with a browser-safe key.  Keep
# that key separate from the privileged service-role key so a routine request
# can never accidentally bypass Row-Level Security.
SUPABASE_ANON_KEY: str = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY: str = os.environ.get(
    "SUPABASE_SERVICE_ROLE_KEY",
    os.environ.get("SUPABASE_KEY", ""),  # Phase 1/2 deployment compatibility
)

# Deprecated compatibility alias for Phase 1/2 imports. New code must choose
# SUPABASE_ANON_KEY or SUPABASE_SERVICE_ROLE_KEY explicitly.
SUPABASE_KEY: str = SUPABASE_SERVICE_ROLE_KEY

# Phase 2 optional env vars
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
OPENALEX_MAILTO: str = os.environ.get("OPENALEX_MAILTO", "")

# Comma-separated browser origins. Keep local development usable by default;
# deployments should set this to their exact Vercel/custom-domain origins.
FRONTEND_ORIGINS: tuple[str, ...] = tuple(
    origin.strip().rstrip("/")
    for origin in os.environ.get(
        "FRONTEND_ORIGINS",
        "http://localhost:3000,https://verdict-nu-rouge.vercel.app",
    ).split(",")
    if origin.strip()
)



def validate_config() -> None:
    """Raise early if any required env var is missing."""
    missing = []
    if not GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY")
    if not SUPABASE_URL:
        missing.append("SUPABASE_URL")
    if not SUPABASE_ANON_KEY:
        missing.append("SUPABASE_ANON_KEY")
    if not SUPABASE_SERVICE_ROLE_KEY:
        missing.append("SUPABASE_SERVICE_ROLE_KEY (or legacy SUPABASE_KEY)")
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Copy .env.example → .env and fill in the values."
        )
