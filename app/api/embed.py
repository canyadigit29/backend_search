from supabase import create_client
from app.core.config import settings
from openai import OpenAI
import os

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
openai = OpenAI(api_key=settings.OPENAI_API_KEY)

def embed_chunks(file_id: str):
    print(f"🧠 Embedding chunks for file_id: {file_id}")
    result = supabase.table("chunks").select("*").eq("file_id", file_id).execute().data
    if not result:
        print("⚠️ No chunks found for embedding.")
        return

    embeddings = []
    for chunk in result:
        response = openai.embeddings.create(
            input=chunk["content"],
            model="text-embedding-ada-002"
        )
        embeddings.append({
            "chunk_id": chunk["id"],
            "embedding": response.data[0].embedding
        })

    if embeddings:
        supabase.table("embeddings").insert(embeddings).execute()
        print(f"✅ Embedded {len(embeddings)} chunks.")
    else:
        print("⚠️ No embeddings created.")
