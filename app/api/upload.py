# app/api/upload.py

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from app.core.supabase_client import supabase
from app.core.config import settings
import uuid
import datetime

router = APIRouter()

@router.post("/upload")
async def upload_file(file: UploadFile = File(...), project_name: str = Form(None)):
    try:
        # Prepare unique ID for the file
        file_id = str(uuid.uuid4())
        filename = file.filename
        content = await file.read()

        # Determine file path
        upload_path = f"uploads/{file_id}/{filename}"

        # Upload file content to Supabase Storage
        response = supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).upload(upload_path, content)
        if isinstance(response, dict) and response.get("error"):
            raise HTTPException(status_code=500, detail=f"Storage upload error: {response['error']['message']}")

        # Look up project_id if project_name provided
        project_id = None
        fallback_message = None
        if project_name:
            project_lookup = supabase.table("projects").select("id").eq("project_name", project_name).single().execute()
            if project_lookup.get("error") or not project_lookup.get("data"):
                fallback_message = f"Project '{project_name}' not found. Uploaded under 'Unassigned'."
            else:
                project_id = project_lookup["data"]["id"]

        # Insert file metadata into 'files' table
        file_record = {
            "id": file_id,
            "project_id": project_id,
            "filename": filename,
            "filepath": upload_path,
            "size": len(content),
            "uploaded_at": datetime.datetime.utcnow().isoformat()
        }
        insert_response = supabase.table("files").insert(file_record).execute()
        if isinstance(insert_response, dict) and insert_response.get("error"):
            raise HTTPException(status_code=500, detail=f"Database insert error: {insert_response['error']['message']}")

        return {
            "message": fallback_message or "File uploaded successfully.",
            "file_id": file_id,
            "filename": filename,
            "project_linked": project_name if project_id else "Unassigned"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
