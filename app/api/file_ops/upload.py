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
        # 🔍 Lookup file_id and file_path based on name and user_id
        result = supabase.table("files").select("id", "file_path").eq("name", name).eq("user_id", user_id).single().execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="No matching file entry found in files table")

        file_id = result.data["id"]
        file_path = result.data["file_path"]

        contents = await file.read()
        supabase.storage.from_("maxgptstorage").upload(
            file_path, contents, {"content-type": file.content_type}
        )

        return {"filePath": file_path}

    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")
