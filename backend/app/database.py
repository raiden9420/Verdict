"""
Supabase client singleton.
All database access goes through get_supabase() so we hold one connection.
"""

from supabase import create_client, Client
from app.config import SUPABASE_URL, SUPABASE_KEY

_client: Client | None = None


def get_supabase() -> Client:
    """Return the shared Supabase client, creating it on first call."""
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client
