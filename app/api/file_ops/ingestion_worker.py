import os
import asyncio
import logging
from datetime import datetime
import traceback
import uuid

from fastapi import APIRouter
from app.core.supabase_client import supabase
from app.api.file_ops.ingest import process_file

logger = logging.getLogger("ingestion_worker")
logger.setLevel(logging.INFO)

BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET")

def list_all_files_in_bucket(bucket: str):
    logger.info(f"[DEBUG] list_all_files_in_bucket called with bucket: {bucket}")
    all_files = []
    def walk(prefix="", is_root=True):
        logger.info(f"[DEBUG] walk called with prefix: '{prefix}' (is_root={is_root})")
        if prefix and not prefix.endswith("/"):
            prefix_slash = prefix + "/"
        else:
            prefix_slash = prefix
        page = supabase.storage.from_(bucket).list(prefix_slash)
        logger.info(f"[DEBUG] API response for prefix '{prefix_slash}': {page}")
        if not page:
            logger.info(f"[DEBUG] No entries at prefix: '{prefix_slash}'")
            return
        for obj in page:
            name = obj.get("name")
            if not name:
                continue
            if obj.get("id") is None:
                logger.info(f"[DEBUG] Recursing into folder: {prefix_slash + name}")
                walk(prefix_slash + name, is_root=False)
            else:
                logger.info(f"[DEBUG] Found file: {prefix_slash + name}")
                all_files.append(prefix_slash + name)
    walk("", is_root=True)
    logger.info(f"[DEBUG] list_all_files_in_bucket: Found {len(all_files)} files. Sample: {all_files[:5]}")
    return all_files


def ensure_file_record(file_path: str):
    result = supabase.table("files").select("id").eq("file_path", file_path).maybe_single().execute()
    if result is None:
        logger.error(f"[ERROR] Supabase query for file_path '{file_path}' returned None (possible HTTP error)")
    elif hasattr(result, 'data') and result.data and result.data.get("id"):
        return result.data["id"]
    file_id = str(uuid.uuid4())
    file_name = os.path.basename(file_path)
    # Extract user_id as the first folder in the file path
    user_id = file_path.split("/")[0] if "/" in file_path else file_path
    supabase.table("files").insert({
        "id": file_id,
        "file_path": file_path,
        "file_name": file_name,
        "created_at": datetime.utcnow().isoformat(),
        "description": file_name,
        "type": "pdf" if file_name.lower().endswith(".pdf") else "other",
        "size": 0,
        "tokens": 0,
        "user_id": user_id,
        "sharing": "private"
    }).execute()
    return file_id

async def run_ingestion_once():
    logger.info("🔁 Starting ingestion cycle (refactored logic)")
    logger.info(f"[DEBUG] About to call list_all_files_in_bucket with BUCKET={BUCKET!r}")
    try:
        # --- NEW: Sync files table with all files in storage bucket ---
        all_files = list_all_files_in_bucket(BUCKET)
        print(f"[DEBUG] Found {len(all_files)} files in bucket '{BUCKET}'")
        for file_path in all_files:
            ensure_file_record(file_path)
        # --- END SYNC ---
        files_to_ingest = supabase.table("files").select("*").neq("ingested", True).execute()
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
                process_file(file_path=file_path, file_id=file_id, user_id=None)
                print(f"[DEBUG] process_file completed for {file_path}")
            except Exception as e:
                logger.error(f"[ERROR] Exception during process_file: {e}")
                print(f"[ERROR] Exception during process_file: {e}")
                traceback.print_exc()
                continue
            try:
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
