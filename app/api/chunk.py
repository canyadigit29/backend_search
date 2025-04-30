from supabase import create_client
from app.core.config import settings
from app.services.chunking import run_chunking_pipeline

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)

def chunk_file(file_id: str):
    print(f"🔍 Chunking file: {file_id}")
    run_chunking_pipeline(file_id)
