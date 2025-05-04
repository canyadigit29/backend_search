from fastapi import APIRouter, File, UploadFile, Form, HTTPException
from supabase import create_client
from app.core.config import settings
from datetime import datetime

router = APIRouter()
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE)

USER_ID = "2532a036-5988-4e0b-8c0e-b0e94aabc1c9"  # Temporary hardcoded user ID

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    project_name: str = Form(...)
):
    try:
        contents = await file.read()
        folder_path = f"{USER_ID}/{project_name}/"
        file_path = f"{folder_path}{file.filename}"

        # Upload to Supabase Storage
        print(f"📤 Uploading file to: {file_path}")
        upload_response = supabase.storage.from_("maxgptstorage").upload(
            file_path, contents, {"content-type": file.content_type}
        )
        print(f"📤 Upload response: {upload_response}")

        # Register file in DB
        supabase.table("files").upsert({
            "file_path": file_path,
            "file_name": file.filename,
            "uploaded_at": datetime.utcnow().isoformat(),
            "ingested": False,
            "ingested_at": None
        }, on_conflict="file_path").execute()

        return {"status": "success", "file_path": file_path}

    except Exception as e:
        print(f"❌ Upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
