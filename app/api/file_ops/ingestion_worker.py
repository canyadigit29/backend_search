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

async def sync_storage_to_files_table():
    """
    Ensure every file in the Supabase storage bucket has a row in the files table.
    Recursively list all files in all subfolders (user folders).
    Only add rows for files not already present, with ingestion=False.
    """
    logger.info("[SYNC] Checking for storage files missing from files table...")
    bucket = BUCKET or "files"
    try:
        # Helper to recursively list all files in all folders
        def list_all_files(path=""):
            files = []
            list_response = supabase.storage.from_(bucket).list(path=path, options={"limit": 1000})
            items = getattr(list_response, "data", [])
            for item in items:
                if item.get("metadata", {}).get("type") == "folder":
                    # Recurse into subfolder
                    files.extend(list_all_files(path + item["name"] + "/"))
                else:
                    # File object
                    item_path = path + item["name"]
                    item["name"] = item_path
                    files.append(item)
            return files

        storage_files = list_all_files("")
        logger.info(f"[SYNC] Found {len(storage_files)} files in storage bucket '{bucket}' (recursive)")
        # Get all file_paths in the files table
        db_files = supabase.table("files").select("file_path").execute()
        db_file_paths = set(f["file_path"] for f in (db_files.data or []))
        new_files = [f for f in storage_files if f["name"] not in db_file_paths]
        logger.info(f"[SYNC] {len(new_files)} files in storage not in files table")
        for file_obj in new_files:
            file_path = file_obj["name"]
            size = file_obj.get("metadata", {}).get("size", file_obj.get("size", 0))
            created_at = file_obj.get("created_at") or datetime.utcnow().isoformat()
            user_id = None
            if "/" in file_path:
                user_id = file_path.split("/")[0]
            file_row = {
                "user_id": user_id or None,
                "file_path": file_path,
                "name": os.path.basename(file_path),
                "description": "(auto-ingested)",
                "size": size,
                "tokens": 0,
                "type": os.path.splitext(file_path)[-1][1:] or "unknown",
                "created_at": created_at,
                "ingestion": False,
            }
            try:
                supabase.table("files").insert(file_row).execute()
                logger.info(f"[SYNC] Inserted missing file row for {file_path}")
            except Exception as e:
                logger.warning(f"[SYNC] Failed to insert file row for {file_path}: {e}")
    except Exception as e:
        logger.error(f"[SYNC] Exception during storage-to-table sync: {e}")

async def run_ingestion_once():
    logger.info("🔁 Starting ingestion cycle (refactored logic)")
    print("[DEBUG] run_ingestion_once started")
    try:
        await sync_storage_to_files_table()
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
            user_id = file.get("user_id")
            print(f"[DEBUG] Processing file: {file_path}, id: {file_id}, user: {user_id}")
            if not file_path or not user_id or not file_id:
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
                process_file(file_path=file_path, file_id=file_id, user_id=user_id)
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
