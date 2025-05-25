import os
import uuid
import logging
from datetime import datetime
from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from app.core.supabase_client import supabase

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    name: str = Form(...),
    file_id: str = Form(None)  # Accept it but ignore it
):
    try:
        file_path = f"{user_id}/{file.filename}"

        # 🔍 Lookup file_id based on name and user_id
        result = supabase.table("files").select("id").eq("name", name).eq("user_id", user_id).single().execute()
        if result.data:
            file_id = result.data["id"]
        else:
            # Insert a new record if it doesn't exist
            file_id = str(uuid.uuid4())
            supabase.table("files").insert({
                "id": file_id,
                "file_path": file_path,
                "user_id": user_id,
                "name": file.filename,
                "status": "uploaded",
                "uploaded_at": datetime.utcnow().isoformat(),
            }).execute()

        contents = await file.read()
        supabase.storage.from_("maxgptstorage").upload(
            file_path, contents, {"content-type": file.content_type}
        )

        return {"filePath": file_path}

    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")
