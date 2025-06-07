import os
import asyncio
import logging
from datetime import datetime
import mimetypes
import uuid

from fastapi import APIRouter
from app.core.supabase_client import supabase
from app.api.file_ops.ingest import process_file

logger = logging.getLogger("ingestion_worker")
logger.setLevel(logging.INFO)

BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET")

async def sync_storage_to_files_table():
    """
    Ensure every file in the storage bucket is present in the files table.
    """
    logger.info("🔄 Syncing storage bucket with files table...")
    # List all files in the storage bucket (recursive)
    all_files = []
    page = None
    while True:
        resp = supabase.storage.from_(BUCKET).list(path="", limit=1000, offset=(page or 0))
        if not resp:
            break
        files = resp
        if not files:
            break
        all_files.extend(files)
        if len(files) < 1000:
            break
        page = (page or 0) + 1000

    # Get all file_paths in the files table
    db_files = supabase.table("files").select("file_path").execute()
    db_file_paths = set(f["file_path"] for f in (db_files.data or []))

    # For each file in storage, if not in files table, insert
    for f in all_files:
        file_path = f.get("name")
        if not file_path or file_path in db_file_paths:
            continue
        # Compose metadata
        name = os.path.basename(file_path)
        size = f.get("metadata", {}).get("size") or f.get("size") or 0
        created_at = f.get("created_at") or datetime.utcnow().isoformat()
        updated_at = f.get("updated_at") or created_at
        file_type, _ = mimetypes.guess_type(name)
        if not file_type:
            file_type = os.path.splitext(name)[1][1:] or "unknown"
        # Insert row
        supabase.table("files").insert({
            "id": str(uuid.uuid4()),
            "description": "",
            "file_path": file_path,
            "folder_id": None,
            "name": name,
            "sharing": "public",
            "size": size,
            "tokens": 0,
            "type": file_type,
            "created_at": created_at,
            "updated_at": updated_at,
            "user_id": "4a867500-7423-4eaa-bc79-94e368555e05"
        }).execute()
        logger.info(f"🆕 Registered missing file in DB: {file_path}")

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
