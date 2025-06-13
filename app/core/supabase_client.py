from supabase import Client, create_client
from app.core.config import settings


def get_supabase_client() -> Client:
    url = settings.SUPABASE_URL
    key = settings.SUPABASE_SERVICE_ROLE
    supabase: Client = create_client(url, key)
    # Ensure all requests include the apikey and Authorization headers
    supabase._client.headers["apikey"] = key
    supabase._client.headers["Authorization"] = f"Bearer {key}"
    return supabase


supabase = get_supabase_client()
