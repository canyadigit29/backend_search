from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from app.api.chunk import chunk_file
from app.api.embed import embed_chunks

router = APIRouter()

def chunk_and_embed_file(file_id: str):
    try:
        chunk_file(file_id)
        embed_chunks(file_id)
    except Exception as e:
        print(f"❌ Ingestion failed for file_id {file_id}: {str(e)}")

@router.post("/background_ingest")
async def background_ingest(
    file_id: str = Query(..., description="ID of the file to process"),
    background_tasks: BackgroundTasks = None
):
    print("⚙️ BACKGROUND_INGEST QUERY ROUTE HIT")
    if not file_id:
        raise HTTPException(status_code=400, detail="Missing file_id")
    background_tasks.add_task(chunk_and_embed_file, file_id)
    return {"message": f"Ingestion started for file_id {file_id}"}
