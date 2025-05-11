from fastapi import APIRouter, BackgroundTasks
from app.api.file_ops.chunk import chunk_file
from app.api.file_ops.embed import embed_chunks
from app.core.supabase_client import supabase

router = APIRouter()

@router.post("/background_ingest")
async def background_ingest(file_id: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(chunk_file, file_id)
    background_tasks.add_task(_embed_from_file_id, file_id)
    return {"message": "Ingestion started in background."}

def _embed_from_file_id(file_id: str):
    file_result = supabase.table("files").select("file_name, project_id").eq("id", file_id).maybe_single().execute()
    file_data = getattr(file_result, "data", None)
    if not file_data:
        raise Exception(f"File not found: {file_id}")

    chunks = chunk_file(file_id)
    embed_chunks(chunks, file_data["project_id"], file_data["file_name"])
