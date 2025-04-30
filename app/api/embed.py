
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.supabase_client import supabase
from app.core.openai_client import embed_text
import uuid

router = APIRouter()

class EmbedRequest(BaseModel):
    file_id: str

@router.post("/embed")
async def embed_chunks(req: EmbedRequest):
    try:
        file_id = req.file_id

        # Retrieve chunks
        chunk_data = supabase.table("chunks").select("*").eq("file_id", file_id).execute()
        if not chunk_data.get("data"):
            raise HTTPException(status_code=404, detail="No chunks found for this file.")

        embeddings_to_insert = []
        for chunk in chunk_data["data"]:
            embedding = embed_text(chunk["content"])
            embeddings_to_insert.append({
                "id": str(uuid.uuid4()),
                "file_id": file_id,
                "chunk_id": chunk["id"],
                "embedding": embedding
            })

        supabase.table("embeddings").insert(embeddings_to_insert).execute()

        return {"message": f"Created {len(embeddings_to_insert)} embeddings."}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
