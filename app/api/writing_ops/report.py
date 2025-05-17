from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.writing_ops.report_writer import generate_pdf_report

router = APIRouter()

class ReportRequest(BaseModel):
    title: str
    content: str

@router.post("/generate-report")
async def generate_report(request: ReportRequest):
    try:
        url = generate_pdf_report(request.title, request.content)
        return {"url": url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
