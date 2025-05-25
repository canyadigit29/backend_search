import os
import uuid
import logging
from datetime import datetime
from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from app.core.supabase_client import supabase
from app.utils.storage import upload_to_storage

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    name: str = Form(...)
):
    try:
        file_path = f"{user_id}/{file.filename}"

        # 🔍 Lookup file_id based on name and user_id
        result = supabase.table("files").select("id").eq("name", name).eq("user_id", user_id).single().execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="No matching file found for name")
        file_id = result.data["id"]

        upload_to_storage(file, file_path)

        # ✅ Check for existing file_path before insert
        try:
            existing = supabase.table("files").select("id").eq("file_path", file_path).execute()
            if not existing.data:
                supabase.table("files").insert({
                    "id": file_id,
                    "file_path": file_path,
                    "user_id": user_id,
                    "name": file.filename,
                    "status": "uploaded",
                    "uploaded_at": datetime.utcnow().isoformat(),
                }).execute()
        except Exception as e:
            logger.error(f"🚩 Failed checking/inserting file record: {e}")
            raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

        return {"filePath": file_path}

    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")
