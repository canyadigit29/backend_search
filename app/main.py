import os
import asyncio
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

# Import the new unified worker
from app.workers.main_worker import MainWorker

# Import existing, still-needed routers
from app.api.chat_ops import chat
from app.api.file_ops import upload, download, search_docs, embed_api, enrich_agenda, extract_text_api, item_history
from app.api.project_ops import project, session_log
from app.api.NerdGPT import code_chat, github_api
from app.api.writing_ops import report
from app.api.gdrive_ops import router as gdrive_router
from app.api.assistant import document_index
from app.core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_PREFIX}/openapi.json"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGins", "https://maxgptfrontend.vercel.app,http://localhost:3000").split(","),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": f"{settings.PROJECT_NAME} is running."}

# New endpoint to manually trigger the worker
@app.post("/api/run-worker")
async def run_worker_manually(background_tasks: BackgroundTasks):
    """
    Manually triggers a one-off run of the background worker to perform OCR and Ingestion tasks.
    """
    background_tasks.add_task(MainWorker.run_ocr_task)
    background_tasks.add_task(MainWorker.run_ingestion_task)
    return {"message": "Background worker tasks (OCR and Ingestion) have been triggered."}

# Mount all the necessary routers
app.include_router(upload.router, prefix=settings.API_PREFIX)
app.include_router(download.router, prefix=settings.API_PREFIX)
app.include_router(search_docs.router, prefix=settings.API_PREFIX)
app.include_router(embed_api.router, prefix="/api")
app.include_router(chat.router, prefix=settings.API_PREFIX)
app.include_router(project.router, prefix=settings.API_PREFIX)
app.include_router(session_log.router, prefix=settings.API_PREFIX)
app.include_router(code_chat.router, prefix=settings.API_PREFIX)
app.include_router(github_api.router, prefix=settings.API_PREFIX)
app.include_router(report.router, prefix=settings.API_PREFIX)
app.include_router(enrich_agenda.router, prefix=settings.API_PREFIX)
app.include_router(extract_text_api.router, prefix=settings.API_PREFIX)
app.include_router(item_history.router, prefix=f"{settings.API_PREFIX}/file_ops")
app.include_router(gdrive_router.router, prefix=f"{settings.API_PREFIX}/gdrive")
app.include_router(document_index.router, prefix=f"{settings.API_PREFIX}/assistant")
