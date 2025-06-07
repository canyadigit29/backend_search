from fastapi import APIRouter, BackgroundTasks
import asyncio
import traceback

from app.api.file_ops.chunk import chunk_file
from app.api.file_ops.embed import embed_chunks
from app.api.file_ops.ingestion_worker import run_ingestion_once
from app.core.supabase_client import supabase

router = APIRouter()


@router.post("/background_ingest")
async def background_ingest(file_id: str, background_tasks: BackgroundTasks):
    print(f"[DEBUG] background_ingest called with file_id={file_id}")
    try:
        background_tasks.add_task(chunk_file, file_id)
        background_tasks.add_task(_embed_from_file_id, file_id)
        print(f"[DEBUG] background_tasks added for file_id={file_id}")
    except Exception as e:
        print(f"[ERROR] Exception in background_ingest: {e}")
        traceback.print_exc()
    return {"message": "Ingestion started in background."}


@router.post("/background_ingest_all")
async def background_ingest_all(background_tasks: BackgroundTasks):
    print(f"[DEBUG] background_ingest_all called")
    try:
        background_tasks.add_task(asyncio.run, run_ingestion_once())
        print(f"[DEBUG] background_tasks added for global ingestion")
    except Exception as e:
        print(f"[ERROR] Exception in background_ingest_all: {e}")
        traceback.print_exc()
    return {"message": "Global ingestion started in background."}


def _embed_from_file_id(file_id: str):
    print(f"[DEBUG] _embed_from_file_id called with file_id={file_id}")
    try:
        file_result = (
            supabase.table("files")
            .select("file_name, project_id")
            .eq("id", file_id)
            .maybe_single()
            .execute()
        )
        file_data = getattr(file_result, "data", None)
        if not file_data:
            print(f"[ERROR] File not found: {file_id}")
            return
        chunk_records = chunk_file(file_id, enrich_metadata=True)
        if not chunk_records:
            print(f"⚠️ No new chunks to embed for file {file_id}")
            return
        chunks = [c["content"] for c in chunk_records]
        chunk_hashes = [c["chunk_hash"] for c in chunk_records]
        section_headers = [c["section_header"] for c in chunk_records]
        page_numbers = [c["page_number"] for c in chunk_records]
        print(f"[DEBUG] Calling embed_chunks for file_id={file_id} with {len(chunks)} chunks")
        embed_chunks(chunks, file_data["project_id"], file_data["file_name"], chunk_hashes=chunk_hashes, section_headers=section_headers, page_numbers=page_numbers)
        print(f"[DEBUG] embed_chunks completed for file_id={file_id}")
    except Exception as e:
        print(f"[ERROR] Exception in _embed_from_file_id: {e}")
        traceback.print_exc()
