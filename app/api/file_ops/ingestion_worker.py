import asyncio
import logging
import os
import uuid
from datetime import datetime

from app.core.supabase_client import supabase
from app.api.file_ops.ingest import process_file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ingestion_worker")

BUCKET = "maxgptstorage"
CHECK_INTERVAL = 300  # seconds (5 minutes)


async def run_ingestion_loop():
    while True:
        try:
            logger.info("🔁 Starting ingestion cycle")

            # Step 1: List all files from Supabase storage
            all_files = supabase.storage.from_(BUCKET).list("", {"limit": 10000, "recursive": True})
            if not all_files:
                logger.info("📭 No files found in storage bucket.")
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            # Step 2: For each file path: user_id/project_name/filename
            for file in all_files:
                path_parts = file["name"].split("/")
                if len(path_parts) != 3:
                    continue  # Skip invalid paths

                user_id, project_name, file_name = path_parts
                file_path = f"{user_id}/{project_name}/{file_name}"

                # Step 3: Check if file is already in files table
                exists = (
                    supabase.table("files")
                    .select("id")
                    .eq("file_path", file_path)
                    .maybe_single()
                    .execute()
                )

                if not exists.data:
                    logger.info(f"➕ New file found: {file_path}. Registering.")
                    
                    # Look up project_id from user_id + project_name
                    project_lookup = (
                        supabase.table("projects")
                        .select("id")
                        .eq("user_id", user_id)
                        .eq("name", project_name)
                        .maybe_single()
                        .execute()
                    )

                    if not project_lookup.data:
                        logger.warning(f"⚠️ No matching project for {project_name} under {user_id}")
                        continue

                    project_id = project_lookup.data["id"]
                    file_id = str(uuid.uuid4())

                    supabase.table("files").insert({
                        "id": file_id,
                        "file_path": file_path,
                        "file_name": file_name,
                        "user_id": user_id,
                        "project_id": project_id,
                        "uploaded_at": datetime.utcnow().isoformat(),
                        "ingested": False,
                        "ingested_at": None,
                    }).execute()

            # Step 4: Find un-ingested files
            unprocessed = (
                supabase.table("files")
                .select("id, file_path, user_id")
                .eq("ingested", False)
                .limit(20)
                .execute()
            )

            for file in unprocessed.data:
                try:
                    logger.info(f"🧠 Ingesting {file['file_path']}")
                    process_file(file_path=file["file_path"], file_id=file["id"], user_id=file["user_id"])
                except Exception as e:
                    logger.error(f"❌ Failed to ingest {file['file_path']}: {e}")

            logger.info("✅ Ingestion cycle complete.")

        except Exception as e:
            logger.exception(f"💥 Unexpected ingestion error: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


# Hook for FastAPI startup
async def startup_event():
    asyncio.create_task(run_ingestion_loop())
