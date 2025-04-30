from fastapi import APIRouter, BackgroundTasks, HTTPException, Body
from pydantic import BaseModel
from app.api.chunk import chunk_file
from app.api.embed import embed_chunks

router = APIRouter()

class IngestRequest(BaseModel):
    file_id: str

def chunk_and_embed_file(file_id: str):
    try:
        chunk_file(file_id)
        embed_chunks(file_id)
    except Exception as e:
        print(f"❌ Ingestion failed for file_id {file_id}: {str(e)}")

@router.post("/background_ingest")
async def background_ingest(
    background_tasks: BackgroundTasks,
    payload: IngestRequest = Body(..., embed=True)
):
    print("🔥 BACKGROUND_INGEST FINAL VERSION HIT")
    file_id = payload.file_id
    if not file_id:
        raise HTTPException(status_code=400, detail="Missing file_id in request body")
    background_tasks.add_task(chunk_and_embed_file, file_id)
    return {"message": f"Ingestion started for file_id {file_id}"}
