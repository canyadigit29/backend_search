from supabase import create_client, Client
from app.core.config import settings

def get_supabase_client() -> Client:
    url = settings.SUPABASE_URL
    key = settings.SUPABASE_SERVICE_ROLE
    supabase: Client = create_client(url, key)
    return supabase

supabase = get_supabase_client()
