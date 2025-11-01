import os
import subprocess
import tempfile
from fastapi import APIRouter, HTTPException, Query
from app.core.supabase_client import supabase
from app.core.config import settings

router = APIRouter()

def _has_ocrmypdf() -> bool:
    try:
        subprocess.run(["ocrmypdf", "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except Exception:
        return False

@router.get("/ocr_searchable_pdf")
async def ocr_searchable_pdf(file_path: str = Query(...)):
    """
    Create a searchable (text-layer) PDF from the given PDF stored in Supabase Storage using OCRmyPDF.

    Returns the storage path to the OCR'd PDF. Requires 'ocrmypdf' to be installed in the environment.
    """
    if not _has_ocrmypdf():
        raise HTTPException(status_code=501, detail="ocrmypdf is not installed in this environment")

    bucket = os.getenv("SUPABASE_STORAGE_BUCKET", settings.SUPABASE_STORAGE_BUCKET)
    try:
        # Download source PDF
        data = supabase.storage.from_(bucket).download(file_path)
        if not data:
            raise HTTPException(status_code=404, detail="File not found in storage")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as src:
            src.write(data)
            src_path = src.name

        # Prepare output temp path
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as dst:
            dst_path = dst.name

        # Run OCRmyPDF (in-place text layer)
        # Common flags: --optimize 0 to be safe; you can tweak as needed
        cmd = [
            "ocrmypdf",
            "--skip-text",
            "--optimize", "0",
            src_path,
            dst_path,
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"ocrmypdf failed: {proc.stderr.decode(errors='ignore')}")

        # Upload OCR'd PDF to storage
        ocr_key = f"ocr_pdf/ocr_{os.path.basename(file_path)}"
        with open(dst_path, "rb") as f:
            supabase.storage.from_(bucket).upload(ocr_key, f.read(), {"content-type": "application/pdf"})

        # Clean up temp files
        try:
            os.remove(src_path)
        except Exception:
            pass
        try:
            os.remove(dst_path)
        except Exception:
            pass

        return {"ocr_pdf_path": ocr_key}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to OCR PDF: {e}")
