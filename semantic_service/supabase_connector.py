import os
from supabase import create_client
from common.config import get_env

SUPABASE_URL = get_env("SUPABASE_URL")
SUPABASE_SERVICE_ROLE = get_env("SUPABASE_SERVICE_ROLE")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)
