from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os

from app.api.writing_ops.report_writer import generate_pdf_report

router = APIRouter()

class ReportRequest(BaseModel):
    title: str
    content: str

@router.post("/generate-report")
async def generate_report(request: ReportRequest):
    try:
        filepath = generate_pdf_report(request.title, request.content)
        filename = os.path.basename(filepath)
        return FileResponse(path=filepath, filename=filename, media_type='application/pdf')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
