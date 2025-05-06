from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.project_ops import project, session_log
from app.api.memory_ops import (
    store_memory, recall_memory, search_memory,
    smart_memory, memory, memory_client,  # ✅ imported only for internal use
    memory_logger, router_brain, search   # ✅ moved here from file_ops
)
from app.api.chat_ops import chat
from app.api.file_ops import (
    upload, ingest, embed, chunk,
    background_tasks  # ❌ removed search from here
)
from app.core.config import settings
import os

# 🧪 Optional: Print env variables for debugging
print("🔍 Environment Variable Check:")
print("OPENAI_API_KEY =", os.getenv("OPENAI_API_KEY"))
print("SUPABASE_URL =", os.getenv("SUPABASE_URL"))

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

# ✅ Route mounts
app.include_router(upload.router, prefix=settings.API_PREFIX)
app.include_router(project.router, prefix=settings.API_PREFIX)
app.include_router(background_tasks.router, prefix=settings.API_PREFIX)
app.include_router(search.router, prefix=settings.API_PREFIX)  # ✅ from memory_ops
app.include_router(session_log.router, prefix=settings.API_PREFIX)
app.include_router(ingest.router, prefix=settings.API_PREFIX)
app.include_router(store_memory.router, prefix=settings.API_PREFIX)
app.include_router(recall_memory.router, prefix=settings.API_PREFIX)
app.include_router(search_memory.router, prefix=settings.API_PREFIX)
app.include_router(smart_memory.router, prefix=settings.API_PREFIX)
app.include_router(memory.router, prefix=settings.API_PREFIX)
# ❌ Removed memory_client.router — helper only
# ❌ Removed memory_logger.router — helper only
app.include_router(router_brain.router, prefix=settings.API_PREFIX)
app.include_router(chat.router, prefix=settings.API_PREFIX)
