import asyncio
import logging
import uuid
from datetime import datetime

from app.core.supabase_client import supabase
from app.api.file_ops.ingest import process_file

logger = logging.getLogger("ingestion_worker")
logger.setLevel(logging.INFO)

BUCKET = "maxgptstorage"


async def run_ingestion_once():
    logger.info("🔁 Starting ingestion cycle (manual trigger)")

    user_folders = supabase.storage.from_(BUCKET).list("")
    if not user_folders:
        logger.info("📭 No user folders found in storage bucket.")
        return

    for user_folder in user_folders:
        user_id = user_folder["name"].rstrip("/")
        logger.info(f"📂 Scanning user folder: {user_id}")

        project_folders = supabase.storage.from_(BUCKET).list(f"{user_id}/")
        for project_folder in project_folders:
            project_name = project_folder["name"].rstrip("/")
            logger.info(f"📁 Scanning project folder: {project_name}")

            files = supabase.storage.from_(BUCKET).list(f"{user_id}/{project_name}/")
            for file in files:
                logger.info(f"🧾 Found file: {file['name']}")
                file_name = file["name"]
                if not file_name or file_name.startswith("."):
                    continue

                file_path = f"{user_id}/{project_name}/{file_name}"

                exists = (
                    supabase.table("files")
                    .select("id, ingested")
                    .eq("file_path", file_path)
                    .maybe_single()
                    .execute()
                )

                if not exists or not exists.data:
                    project_id_result = (
                        supabase.table("projects")
                        .select("id")
                        .eq("user_id", user_id)
                        .eq("name", project_name)
                        .maybe_single()
                        .execute()
                    )
                    if not project_id_result.data:
                        logger.warning(f"⚠️ Project not found for: {project_name}")
                        continue

                    file_id = str(uuid.uuid4())
                    supabase.table("files").insert(
                        {
                            "id": file_id,
                            "file_path": file_path,
                            "file_name": file_name,
                            "project_id": project_id_result.data["id"],
                            "user_id": user_id,
                            "uploaded_at": datetime.utcnow().isoformat(),
                            "ingested": False,
                            "ingested_at": None,
                        }
                    ).execute()
                else:
                    file_id = exists.data["id"]

                if not exists or not exists.data or not exists.data.get("ingested"):
                    process_file(
                        file_path=file_path,
                        file_id=file_id,
                        user_id=user_id,
                    )

    logger.info("✅ Ingestion cycle complete")


from fastapi import APIRouter
import asyncio

router = APIRouter()

@router.post("/run-ingestion")
async def run_ingestion_endpoint():
    asyncio.create_task(run_ingestion_once())
    return {"status": "Ingestion started"}
