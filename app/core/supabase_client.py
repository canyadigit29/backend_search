"""Supabase client helper with lazy initialization and validation.

Avoids raising exceptions at import time (e.g. Invalid URL/API key) so the
service can still start and serve a health endpoint explaining the issue.
"""

from typing import Optional
from supabase import Client, create_client  # type: ignore
from app.core.config import settings
import os
import threading

_supabase_lock = threading.Lock()
_supabase_client: Optional[Client] = None


def _env_var_set(val: Optional[str]) -> bool:
    return bool(val and val.strip() and not val.strip().startswith("CHANGE_ME"))


def get_supabase_client(force_refresh: bool = False) -> Client:
    """Return a cached Supabase client, creating it lazily.

    force_refresh: rebuild the client (e.g. after rotating credentials).
    Raises RuntimeError with a helpful message instead of low-level SupabaseException.
    """
    global _supabase_client
    if _supabase_client is not None and not force_refresh:
        return _supabase_client
    with _supabase_lock:
        if _supabase_client is not None and not force_refresh:
            return _supabase_client

        url = settings.SUPABASE_URL
        key = settings.SUPABASE_SERVICE_ROLE

        if not _env_var_set(url) or not _env_var_set(key):
            raise RuntimeError("Supabase credentials not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE)")
        try:
            _supabase_client = create_client(url, key)
            return _supabase_client
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Supabase client: {e}")


def supabase_optional() -> Optional[Client]:
    """Best-effort client getter that never raises; returns None on failure."""
    try:
        return get_supabase_client()
    except Exception:
        return None


# Backwards compatibility: expose name `supabase` lazily via property-like object
class _SupabaseProxy:
    def __getattr__(self, item):
        client = get_supabase_client()
        return getattr(client, item)


supabase = _SupabaseProxy()
