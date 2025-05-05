from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import (
    upload, project, background_tasks, search, session_log,
    ingest_unprocessed, store_memory, recall_memory,
    search_memory, smart_memory, match_project_context,
    chat  # ✅ Added chat route
)
from app.core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_PREFIX}/openapi.json"
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

# ✅ API route mounts
app.include_router(upload.router, prefix=settings.API_PREFIX)
app.include_router(project.router, prefix=settings.API_PREFIX)
app.include_router(background_tasks.router, prefix=settings.API_PREFIX)
app.include_router(search.router, prefix=settings.API_PREFIX)
app.include_router(session_log.router, prefix=settings.API_PREFIX)
app.include_router(ingest_unprocessed.router, prefix=settings.API_PREFIX)
app.include_router(store_memory.router, prefix=settings.API_PREFIX)
app.include_router(recall_memory.router, prefix=settings.API_PREFIX)
app.include_router(search_memory.router, prefix=settings.API_PREFIX)
app.include_router(smart_memory.router, prefix=settings.API_PREFIX)
app.include_router(match_project_context.router, prefix=settings.API_PREFIX)
app.include_router(chat.router, prefix=settings.API_PREFIX)  # ✅ Mounted chat route
