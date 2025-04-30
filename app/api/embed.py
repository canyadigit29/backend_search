from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.supabase_client import supabase
from app.core.openai_client import embed_text
import uuid

router = APIRouter()

class EmbedRequest(BaseModel):
    file_id: str

@router.post("/embed")
async def embed_chunks(request: EmbedRequest):
    try:
        file_id = request.file_id

        # Retrieve chunks for the given file_id
        chunk_response = supabase.table("chunks").select("*").eq("file_id", file_id).execute()
        chunks = chunk_response.data

        if not chunks:
            raise HTTPException(status_code=404, detail="No chunks found for this file.")

        embeddings_to_insert = []
        for chunk in chunks:
            try:
                # Validate content
                if "content" not in chunk or not isinstance(chunk["content"], str) or not chunk["content"].strip():
                    raise ValueError(f"Invalid chunk content: {chunk.get('content')}")

                embedding = embed_text(chunk["content"])
                embeddings_to_insert.append({
                    "id": str(uuid.uuid4()),
                    "file_id": file_id,
                    "chunk_id": chunk["id"],
                    "embedding": embedding
                })
            except Exception as embed_error:
                raise HTTPException(
                    status_code=500,
                    detail=f"Embedding failed for chunk ID {chunk['id']}: {embed_error}"
                )

        # Insert embeddings into Supabase
        supabase.table("embeddings").insert(embeddings_to_insert).execute()

        return {"message": f"Created {len(embeddings_to_insert)} embeddings."}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
