import os
import asyncio
import time
import uuid
import logging
from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware

# Import the new unified worker
# Old background worker (legacy ingestion) intentionally disabled
# from app.workers.main_worker import MainWorker

# Import existing, still-needed routers
from app.api.file_ops import upload, search_docs, embed_api, extract_text_api
from app.api.file_ops import ocr_searchable_pdf
from app.api.gdrive_ops import router as gdrive_router
from app.api.Responses import router as responses_router
from app.api.query_analyzer import router as query_analyzer_router
from app.api.rag import router as rag_router
from app.core.config import settings
from app.core.logger import setup_logging, request_id_var, log_info
from app.core.logging_config import setup_logging, set_request_id
import uuid
from fastapi import Request

setup_logging()

# Initialize logging early
setup_logging()

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

# Lightweight request logging with request id propagation
@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    token = request_id_var.set(rid)
    start = time.perf_counter()
    try:
        log_info(logging.getLogger("request"), "request.start", {
            "path": request.url.path,
            "method": request.method,
        })
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000.0
        response.headers["X-Request-ID"] = rid
        log_info(logging.getLogger("request"), "request.end", {
            "path": request.url.path,
            "method": request.method,
            "status": getattr(response, "status_code", None),
            "duration_ms": round(duration_ms, 2),
        })
        return response
    finally:
        request_id_var.reset(token)


@app.middleware("http")
async def add_request_id_middleware(request: Request, call_next):
    # Generate a per-request ID for correlation
    req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    set_request_id(req_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response

@app.get("/")
async def root():
    return {"message": f"{settings.PROJECT_NAME} is running."}

# Legacy worker route disabled
@app.post("/api/run-worker")
async def run_worker_manually_disabled():
    return {"message": "Legacy ingestion worker disabled. Use /api/responses/gdrive/sync and /api/responses/vector-store/ingest instead."}

# Mount routers: keep Responses; gate legacy behind ENABLE_LEGACY_ROUTES
if settings.ENABLE_LEGACY_ROUTES:
    app.include_router(upload.router, prefix=settings.API_PREFIX)
    app.include_router(search_docs.router, prefix=settings.API_PREFIX)
    app.include_router(embed_api.router, prefix=settings.API_PREFIX)
    app.include_router(extract_text_api.router, prefix=settings.API_PREFIX)
    app.include_router(ocr_searchable_pdf.router, prefix=settings.API_PREFIX)
    app.include_router(gdrive_router.router, prefix=f"{settings.API_PREFIX}/gdrive")
    app.include_router(query_analyzer_router.router, prefix=settings.API_PREFIX)
    app.include_router(rag_router.router, prefix=f"{settings.API_PREFIX}/search")

app.include_router(responses_router, prefix=settings.API_PREFIX)
