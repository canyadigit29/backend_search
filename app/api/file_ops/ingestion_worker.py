import os  # Moved up as per PEP8

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat_ops import chat
from app.api.file_ops import (background_tasks,  # Removed: embed, chunk
                              ingest, upload, ingestion_worker)
from app.api.project_ops import project, session_log
from app.core.config import settings

# 🧪 Optional: Print env variables for debugging
print("🔍 Environment Variable Check:")
print("OPENAI_API_KEY =", os.getenv("OPENAI_API_KEY"))
print("SUPABASE_URL =", os.getenv("SUPABASE_URL"))

app = FastAPI(
    title=settings.PROJECT_NAME, openapi_url=f"{settings.API_PREFIX}/openapi.json"
)

# ✅ CORS middleware for local testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"message": f"{settings.PROJECT_NAME} is running."}


# ✅ Route mounts (memory routes removed)
app.include_router(upload.router, prefix=settings.API_PREFIX)
app.include_router(project.router, prefix=settings.API_PREFIX)
app.include_router(background_tasks.router, prefix=settings.API_PREFIX)
app.include_router(session_log.router, prefix=settings.API_PREFIX)
app.include_router(ingest.router, prefix=settings.API_PREFIX)
app.include_router(chat.router, prefix=settings.API_PREFIX)


@app.on_event("startup")
async def start_background_ingestion():
    await ingestion_worker.startup_event()


# 🔁 Patch ingestion worker to debug missing file inserts
import asyncio
import logging
import uuid
from datetime import datetime

from app.core.supabase_client import supabase
from app.api.file_ops.ingest import process_file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ingestion_worker")

BUCKET = "maxgptstorage"
CHECK_INTERVAL = 300


async def run_ingestion_loop():
    while True:
        try:
            logger.info("🔁 Starting ingestion cycle")

            user_folders = supabase.storage.from_(BUCKET).list("")
            if not user_folders:
                logger.info("📭 No user folders found in storage bucket.")
                await asyncio.sleep(CHECK_INTERVAL)
                continue

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
                        file_path = f"{user_id}/{project_name}/{file_name}"
                        logger.info(f"🔍 Checking for file_path: {file_path}")

                        try:
                            exists = (
                                supabase.table("files")
                                .select("id")
                                .eq("file_path", file_path)
                                .maybe_single()
                                .execute()
                            )
                        except Exception as e:
                            logger.warning(f"⚠️ Skipping file due to query error: {file_path} → {e}")
                            continue

                        if not exists or not getattr(exists, "data", None):
                            logger.info(f"➕ New file found: {file_path}. Registering.")
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

                            record = {
                                "id": file_id,
                                "file_path": file_path,
                                "file_name": file_name,
                                "user_id": user_id,
                                "project_id": project_id,
                                "uploaded_at": datetime.utcnow().isoformat(),
                                "ingested": False,
                                "ingested_at": None,
                            }
                            logger.info(f"📦 Attempting to insert: {record}")
                            insert_result = supabase.table("files").insert(record).execute()
                            if getattr(insert_result, "error", None):
                                logger.error(f"❌ Insert error: {insert_result.error.message}")
                            else:
                                logger.info(f"✅ Inserted file metadata for {file_path}")

            for run in range(3):
                logger.info(f"🔁 Ingestion pass {run + 1}/3")

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


async def startup_event():
    asyncio.create_task(run_ingestion_loop())
