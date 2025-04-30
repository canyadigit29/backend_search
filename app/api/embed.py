from supabase import create_client
from app.core.config import settings
from app.services.embedding import run_embedding_pipeline

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)

def embed_chunks(file_id: str):
    print(f"🧠 Embedding chunks for file: {file_id}")
    run_embedding_pipeline(file_id)
