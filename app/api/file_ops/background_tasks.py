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


def queue_ingestion_task(file_id: str, file_path: str, user_id: str, metadata: dict):
    """
    Adds the ingestion task to the background queue.
    This function is now the central point for queuing ingestion work.
    """
    # We can use FastAPI's BackgroundTasks for simplicity if the router is available,
    # but a more robust system would use Celery or RQ.
    # For now, we'll just call the worker function directly in a background task.
    # This assumes the web server process can handle these tasks without timing out.

    # A simple way to run a background task without the `background_tasks` object
    # from a request is to use asyncio.create_task if in an async context.
    # However, since this is called from an async endpoint, we can just schedule it.

    # The ingestion worker now needs the metadata.
    async def run_task():
        from app.api.file_ops.ingestion_worker import process_file_with_metadata

        await process_file_with_metadata(file_id, file_path, user_id, metadata)

    asyncio.create_task(run_task())


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
