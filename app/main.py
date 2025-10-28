import os
import asyncio
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

# Import the new unified worker
from app.workers.main_worker import MainWorker

# Import existing, still-needed routers
from app.api.file_ops import upload, search_docs, embed_api, extract_text_api
from app.api.gdrive_ops import router as gdrive_router
from app.api.query_analyzer import router as query_analyzer_router
from app.api.rag import router as rag_router
from app.core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_PREFIX}/openapi.json"
)

# CORS middleware
# Get allowed origins from environment variable, split by comma
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
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
app.include_router(search_docs.router, prefix=settings.API_PREFIX)
app.include_router(embed_api.router, prefix=settings.API_PREFIX)
app.include_router(extract_text_api.router, prefix=settings.API_PREFIX)
app.include_router(gdrive_router.router, prefix=f"{settings.API_PREFIX}/gdrive")
app.include_router(query_analyzer_router.router, prefix=settings.API_PREFIX)
app.include_router(rag_router.router, prefix=f"{settings.API_PREFIX}/search")
