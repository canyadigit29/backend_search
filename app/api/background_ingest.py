
from fastapi import APIRouter, BackgroundTasks, HTTPException
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
        # In production, consider using a logger instead of print
        print(f"Background ingestion failed for file_id {file_id}: {str(e)}")

@router.post("/background_ingest")
async def background_ingest(request: IngestRequest, background_tasks: BackgroundTasks):
    file_id = request.file_id
    background_tasks.add_task(chunk_and_embed_file, file_id)
    return {"message": f"Ingestion started for file_id {file_id}"}
