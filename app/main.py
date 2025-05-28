from app.api.file_ops import ingestion_worker
import os  # Moved up as per PEP8

from fastapi import FastAPI
from app.api.file_ops import embed_api
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat_ops import chat
from app.api.file_ops import (background_tasks, search_docs,  # Added: search_docs
                              ingest, upload, download)
from app.api.project_ops import project, session_log
from app.api.NerdGPT import code_chat, github_api
from app.api.file_ops.search_docs import perform_search as search_documents
from app.api.memory_ops.session_memory import retrieve_memory
from app.api.writing_ops import report  # 📄 PDF Report Writer
from app.core.config import settings

# 🔍 Optional: Print env variables for debugging
print("🔍 Environment Variable Check:")
print("OPENAI_API_KEY =", os.getenv("OPENAI_API_KEY"))
print("SUPABASE_URL =", os.getenv("SUPABASE_URL"))

app = FastAPI(
    title=settings.PROJECT_NAME, openapi_url=f"{settings.API_PREFIX}/openapi.json"
app.include_router(embed_api.router, prefix="/api")
)

# ✅ CORS middleware for local testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "https://maxgptfrontend.vercel.app,http://localhost:3000").split(",") ,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"message": f"{settings.PROJECT_NAME} is running."}


# ✅ Route mounts (memory routes removed)
app.include_router(upload.router, prefix=settings.API_PREFIX)
app.include_router(download.router, prefix=settings.API_PREFIX)
app.include_router(project.router, prefix=settings.API_PREFIX)
app.include_router(background_tasks.router, prefix=settings.API_PREFIX)
app.include_router(session_log.router, prefix=settings.API_PREFIX)
app.include_router(ingest.router, prefix=settings.API_PREFIX)
app.include_router(chat.router, prefix=settings.API_PREFIX)
app.include_router(code_chat.router, prefix=settings.API_PREFIX)  # 🔹 NerdGPT route mounted
app.include_router(github_api.router, prefix=settings.API_PREFIX)  # 🔹 GitHub route mounted

app.include_router(report.router, prefix=settings.API_PREFIX)

# 🚫 No ingestion worker trigger on startup — now called manually from chat

app.include_router(ingestion_worker.router, prefix=settings.API_PREFIX)
app.include_router(search_docs.router, prefix=settings.API_PREFIX)
