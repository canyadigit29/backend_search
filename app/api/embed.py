
from app.core.config import settings
from supabase import create_client
from app.core.openai_client import embed_text
from uuid import uuid4
import re

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE)

def embed_chunks(file_id: str):
    print(f"🧠 Embedding chunks for: {file_id}")
    try:
        file_entry = None
        is_uuid = re.fullmatch(r"[0-9a-fA-F\-]{36}", file_id)

        if is_uuid:
            result = supabase.table("files").select("*").eq("id", file_id).execute()
            file_entry = result.data[0] if result.data else None
        else:
            file_path = f"uploads/{file_id}.pdf"
            result = supabase.table("files").select("*").eq("file_path", file_path).execute()
            file_entry = result.data[0] if result.data else None

        if not file_entry:
            print(f"❌ No matching file entry for: {file_id}")
            return

        actual_file_id = file_entry["id"]

        chunks = supabase.table("chunks").select("*").eq("file_id", actual_file_id).execute().data
        if not chunks:
            print(f"⚠️ No chunks found for file ID: {actual_file_id}")
            return

        embeds = []
        for chunk in chunks:
            embedding = embed_text(chunk["content"])
            embeds.append({
                "id": str(uuid4()),
                "chunk_id": chunk["id"],
                "embedding": embedding
            })

        if embeds:
            supabase.table("embeddings").insert(embeds).execute()
            print(f"✅ Embedded {len(embeds)} chunks.")

    except Exception as e:
        print(f"❌ Embed error: {str(e)}")
