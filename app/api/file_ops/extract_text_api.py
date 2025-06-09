from fastapi import APIRouter, HTTPException, Query
from app.core.extract_text import extract_text
import os

router = APIRouter()

@router.get("/extract_text")
async def api_extract_text(file_path: str = Query(...)):
    """
    Extracts text from a PDF file given its path (relative to the backend_search root).
    Returns the extracted text and the file name.
    """
    try:
        # Security: Only allow files within the file_ops directory
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__)))
        abs_path = os.path.abspath(os.path.join(base_dir, file_path))
        if not abs_path.startswith(base_dir):
            raise HTTPException(status_code=400, detail="Invalid file path.")
        if not os.path.exists(abs_path):
            raise HTTPException(status_code=404, detail="File not found.")
        text = extract_text(abs_path)
        file_name = os.path.basename(abs_path)
        return {"text": text, "file_name": file_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to extract text: {str(e)}")
