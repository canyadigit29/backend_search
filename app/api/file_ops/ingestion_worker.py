import os
import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter
from app.core.supabase_client import supabase
from app.api.file_ops.ingest import process_file

logger = logging.getLogger("ingestion_worker")
logger.setLevel(logging.INFO)

BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET")

async def run_ingestion_once():
    logger.info("🔁 Starting ingestion cycle (refactored logic)")

    files_to_ingest = supabase.table("files").select("*").neq("ingested", True).execute()

    if not files_to_ingest or not files_to_ingest.data:
        logger.info("📭 No un-ingested files found.")
        return

    for file in files_to_ingest.data:
        file_path = file.get("file_path")
        file_id = file.get("id")
        user_id = file.get("user_id")

        if not file_path or not user_id or not file_id:
            logger.warning(f"⚠️ Skipping invalid row: {file}")
            continue

        # Check if file exists in Supabase storage
        file_name = file_path.split("/")[-1]
        folder = "/".join(file_path.split("/")[:-1])
        file_check = supabase.storage.from_(BUCKET).list(folder)

        if not any(f.get("name") == file_name for f in file_check):
            logger.warning(f"🚫 File not found in storage: {file_path}")
            continue

        logger.info(f"🧾 Ingesting: {file_path}")
        process_file(file_path=file_path, file_id=file_id, user_id=user_id)

        supabase.table("files").update({
            "ingested": True,
            "ingested_at": datetime.utcnow().isoformat()
        }).eq("id", file_id).execute()

    logger.info("✅ Ingestion cycle complete")


router = APIRouter()

@router.post("/run-ingestion")
async def run_ingestion_endpoint():
    asyncio.create_task(run_ingestion_once())
    return {"status": "Ingestion started"}

@router.post("/sync-files")
async def alias_sync_route():
    return await run_ingestion_endpoint()
