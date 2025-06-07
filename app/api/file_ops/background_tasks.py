from fastapi import APIRouter, BackgroundTasks
import asyncio

from app.api.file_ops.chunk import chunk_file
from app.api.file_ops.embed import embed_chunks
from app.api.file_ops.ingestion_worker import run_ingestion_once
from app.core.supabase_client import supabase

router = APIRouter()


@router.post("/background_ingest")
async def background_ingest(file_id: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(chunk_file, file_id)
    background_tasks.add_task(_embed_from_file_id, file_id)
    return {"message": "Ingestion started in background."}


@router.post("/background_ingest_all")
async def background_ingest_all(background_tasks: BackgroundTasks):
    background_tasks.add_task(asyncio.run, run_ingestion_once())
    return {"message": "Global ingestion started in background."}


def _embed_from_file_id(file_id: str):
    file_result = (
        supabase.table("files")
        .select("file_name, project_id")
        .eq("id", file_id)
        .maybe_single()
        .execute()
    )
    file_data = getattr(file_result, "data", None)
    if not file_data:
        raise Exception(f"File not found: {file_id}")

    chunks = chunk_file(file_id)
    embed_chunks(chunks, file_data["project_id"], file_data["file_name"])
