from fastapi import APIRouter, BackgroundTasks
from app.api.chunk import chunk_file
from app.api.embed import embed_chunks

router = APIRouter()

@router.post("/background_ingest")
async def background_ingest(file_id: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(chunk_file, file_id)
    background_tasks.add_task(embed_chunks, file_id)
    return {"message": "Ingestion started in background."}
