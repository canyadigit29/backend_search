
from fastapi import APIRouter, File, UploadFile, HTTPException
from supabase import create_client
from app.core.config import settings
from datetime import datetime

router = APIRouter()
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE)

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    contents = await file.read()
    file_path = f"uploads/{file.filename}"

    try:
        # Upload to Supabase Storage
        supabase.storage.from_("maxgptstorage").upload(file_path, contents, {"content-type": file.content_type})

        # Register file in 'files' table (or update if already exists)
        supabase.table("files").upsert({
            "file_path": file_path,
            "file_name": file.filename,
            "uploaded_at": datetime.utcnow().isoformat(),
            "ingested": False,
            "ingested_at": None
        }, on_conflict="file_path").execute()

        return {"status": "success", "file_path": file_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
