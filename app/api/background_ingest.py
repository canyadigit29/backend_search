from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from app.api.chunk import chunk_file
from app.api.embed import embed_chunks

router = APIRouter()

# ✅ This defines the expected input shape for /background_ingest
class IngestRequest(BaseModel):
    file_id: str

# ✅ This wraps both chunking and embedding into one background task
def chunk_and_embed_file(file_id: str):
    try:
        chunk_file(file_id)
        embed_chunks(file_id)
    except Exception as e:
        # In production, consider logging this instead
        print(f"Background ingestion failed for file_id {file_id}: {str(e)}")

# ✅ Main route that queues the ingestion task
@router.post("/background_ingest")
async def background_ingest(request: IngestRequest, background_tasks: BackgroundTasks):
    file_id = request.file_id
    background_tasks.add_task(chunk_and_embed_file, file_id)
    return {"message": f"Ingestion started for file_id {file_id}"}
