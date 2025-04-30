from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.supabase_client import supabase

router = APIRouter()

class ChunkRequest(BaseModel):
    file_id: str

@router.post("/chunk")
async def chunk_file(request: ChunkRequest):
    file_id = request.file_id

    # Dummy logic — you can replace this with real chunking logic later
    try:
        content = "This is a placeholder chunk."
        chunk_data = {
            "id": "00000000-0000-0000-0000-000000000000",
            "file_id": file_id,
            "content": content,
            "chunk_index": 0,
        }
        supabase.table("chunks").insert(chunk_data).execute()
        return {"message": f"1 chunk created for file_id {file_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
