from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import upload, project
from app.core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_PREFIX}/openapi.json"
)

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

app.include_router(upload.router, prefix=settings.API_PREFIX)
app.include_router(project.router, prefix=settings.API_PREFIX)
