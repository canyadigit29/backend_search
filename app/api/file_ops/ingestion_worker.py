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

BUCKET = "files"  # Always use the correct bucket name

async def sync_storage_to_files_table():
    """
    Ensure every file in the 'files' storage bucket is present in the files table, with correct user folder path.
    """
    logger.info("🔄 Syncing storage bucket with files table...")

    def list_all_files_recursive(folder_path=""):
        files = []
        resp = supabase.storage.from_(BUCKET).list(folder_path)
        if not resp:
            return files
        for entry in resp:
            if entry.get("type") == "file":
                # Compose full path: folder_path/filename
                full_path = f"{folder_path}/{entry['name']}" if folder_path else entry["name"]
                entry["name"] = full_path
                files.append(entry)
            elif entry.get("type") == "folder":
                subfolder = f"{folder_path}/{entry['name']}" if folder_path else entry["name"]
                files.extend(list_all_files_recursive(subfolder))
        return files

    all_files = list_all_files_recursive()

    # Get all file_paths in the files table
    db_files = supabase.table("files").select("file_path").execute()
    db_file_paths = set(f["file_path"] for f in (db_files.data or []))

    for f in all_files:
        file_path = f.get("name")
        if not file_path or file_path in db_file_paths:
            continue
        # Only register files that are in a user folder (UUID folder)
        parts = file_path.split("/", 1)
        if len(parts) != 2:
            logger.warning(f"Skipping file not in user folder: {file_path}")
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
            "user_id": parts[0],
            "ingested": False
        }).execute()
        logger.info(f"🆕 Registered missing file in DB: {file_path}")

async def run_ingestion_once():
    await sync_storage_to_files_table()  # <-- Ensure sync happens first
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
