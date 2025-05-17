from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from app.api.writing_ops.report_writer import generate_report_from_chunks

router = APIRouter()

class Chunk(BaseModel):
    content: str
    file_name: Optional[str] = None
    chunk_index: Optional[int] = 0

class ReportRequest(BaseModel):
    user_prompt: str
    chunks: List[Chunk]
    tone: Optional[str] = "formal"

@router.post("/generate-report")
async def generate_report(request: ReportRequest):
    try:
        urls = generate_report_from_chunks(
            chunks=[chunk.dict() for chunk in request.chunks],
            user_prompt=request.user_prompt,
            tone=request.tone
        )
        return {"status": "success", **urls}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
