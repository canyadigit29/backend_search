from fastapi import APIRouter, BackgroundTasks, HTTPException, Body
from typing import Dict
from app.api.chunk import chunk_file
from app.api.embed import embed_chunks

router = APIRouter()

def chunk_and_embed_file(file_id: str):
    try:
        chunk_file(file_id)
        embed_chunks(file_id)
    except Exception as e:
        print(f"🔥 V2 ingestion failed for file_id {file_id}: {str(e)}")

@router.post("/background_ingest_v2")
async def background_ingest_v2(
    background_tasks: BackgroundTasks,
    payload: Dict = Body(...)
):
    print("🔥 INGEST V2 ROUTE HIT")
    file_id = payload.get("file_id")
    if not file_id:
        return {"error": "file_id is missing in body"}
    background_tasks.add_task(chunk_and_embed_file, file_id)
    return {"message": f"V2: Ingestion started for file_id {file_id}"}
