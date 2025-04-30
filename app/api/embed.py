from fastapi import APIRouter, HTTPException
from app.core.supabase_client import supabase
from app.core.openai_client import embed_text
import uuid

router = APIRouter()

@router.post("/embed")
async def embed_chunks(file_id: str):
    try:
        # Query for chunks with matching file_id
        chunk_response = supabase.table("chunks").select("*").eq("file_id", file_id).execute()
        chunks = chunk_response.data

        if not chunks:
            raise HTTPException(status_code=404, detail="No chunks found for this file.")

        embeddings_to_insert = []
        for chunk in chunks:
            embedding = embed_text(chunk["content"])
            embeddings_to_insert.append({
                "id": str(uuid.uuid4()),
                "file_id": file_id,
                "chunk_id": chunk["id"],
                "embedding": embedding
            })

        # Insert all embeddings
        insert_response = supabase.table("embeddings").insert(embeddings_to_insert).execute()

        return {"message": f"Created {len(embeddings_to_insert)} embeddings."}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
