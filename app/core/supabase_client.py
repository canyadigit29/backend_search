from supabase import Client, create_client as _create_client
from app.core.config import settings

_supabase_instance = None


def get_supabase_client() -> Client:
    """Get or create the Supabase client singleton."""
    global _supabase_instance
    if _supabase_instance is None:
        url = settings.SUPABASE_URL
        key = settings.SUPABASE_SERVICE_ROLE
        _supabase_instance = _create_client(url, key)
    return _supabase_instance


# Alias for backwards compatibility - returns client lazily
class _LazySupabaseClient:
    def __getattr__(self, name):
        return getattr(get_supabase_client(), name)


supabase = _LazySupabaseClient()

# Re-export create_client for modules that import it directly
create_client = _create_client
