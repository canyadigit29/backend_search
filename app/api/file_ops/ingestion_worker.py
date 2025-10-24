import os
import asyncio
import logging
from datetime import datetime
import traceback

from fastapi import APIRouter
from app.core.supabase_client import supabase
from app.api.file_ops.ingest import process_file

logger = logging.getLogger("ingestion_worker")
logger.setLevel(logging.INFO)

BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET")

async def run_ingestion_once():
    logger.info("🔁 Starting ingestion cycle (refactored logic)")
    print("[DEBUG] run_ingestion_once started")
    try:
        logger.info("[DEBUG] Querying files table for un-ingested files...")
        files_to_ingest = supabase.table("files").select("*").neq("ingested", True).execute()
        logger.info(f"[DEBUG] files_to_ingest query result: {files_to_ingest}")
        print(f"[DEBUG] files_to_ingest: {len(files_to_ingest.data) if files_to_ingest and files_to_ingest.data else 0}")
        if not files_to_ingest or not files_to_ingest.data:
            logger.info("📭 No un-ingested files found.")
            print("[DEBUG] No un-ingested files found.")
            return
        for file in files_to_ingest.data:
            file_path = file.get("file_path")
            file_id = file.get("id")
            print(f"[DEBUG] Processing file: {file_path}, id: {file_id}")
            if not file_path or not file_id:
                logger.warning(f"⚠️ Skipping invalid row: {file}")
                print(f"[DEBUG] Skipping invalid row: {file}")
                continue
            # Directly attempt to download the file to check existence
            try:
                response = supabase.storage.from_(BUCKET).download(file_path)
                if not response:
                    logger.warning(f"🚫 File not found in storage (download failed): {file_path}")
                    print(f"[DEBUG] File not found in storage (download failed): {file_path}")
                    continue
            except Exception as e:
                logger.warning(f"🚫 Exception during file download: {file_path} - {e}")
                print(f"[DEBUG] Exception during file download: {file_path} - {e}")
                traceback.print_exc()
                continue
            logger.info(f"🧾 Ingesting: {file_path}")
            print(f"[DEBUG] Ingesting: {file_path}")
            try:
                process_file(file_path=file_path, file_id=file_id)
                print(f"[DEBUG] process_file completed for {file_path}")
            except Exception as e:
                logger.error(f"[ERROR] Exception during process_file: {e}")
                print(f"[ERROR] Exception during process_file: {e}")
                traceback.print_exc()
                continue
            try:
                logger.info(f"[DEBUG] Marking file as ingested in DB: {file_id}")
                supabase.table("files").update({
                    "ingested": True,
                    "ingested_at": datetime.utcnow().isoformat()
                }).eq("id", file_id).execute()
                print(f"[DEBUG] Marked file as ingested: {file_id}")
            except Exception as e:
                logger.error(f"[ERROR] Exception during DB update: {e}")
                print(f"[ERROR] Exception during DB update: {e}")
                traceback.print_exc()
        logger.info("✅ Ingestion cycle complete")
        print("[DEBUG] run_ingestion_once completed")
    except Exception as e:
        logger.error(f"[ERROR] Exception in run_ingestion_once: {e}")
        print(f"[ERROR] Exception in run_ingestion_once: {e}")
        traceback.print_exc()


router = APIRouter()

@router.post("/run-ingestion")
async def run_ingestion_endpoint():
    asyncio.create_task(run_ingestion_once())
    return {"status": "Ingestion started"}

@router.post("/sync-files")
async def alias_sync_route():
    return await run_ingestion_endpoint()
