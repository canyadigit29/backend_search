import io
import uuid
import zipfile
from datetime import datetime

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    UploadFile
)

from app.api.file_ops.ingest import process_file
from app.core.supabase_client import supabase

router = APIRouter()

@router.post("/upload")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project_id: str = Form(...),
    user_id: str = Form(...),
    file_id: str = Form(...),
    name: str = Form(...)
):
    try:
        contents = await file.read()

        project_lookup = (
            supabase.table("projects")
            .select("name")
            .eq("id", project_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )

        if not project_lookup.data:
            raise HTTPException(status_code=404, detail="Invalid project_id")

        project_name = project_lookup.data["name"]
        folder_path = f"{user_id}/{project_name}/"

        final_name = name or file.filename

        if final_name.endswith(".zip"):
            extracted = zipfile.ZipFile(io.BytesIO(contents))
            ingested_files = []

            for name in extracted.namelist():
                if name.lower().endswith((".pdf", ".docx", ".doc", ".rtf", ".txt", ".odt")):
                    inner_file = extracted.read(name)
                    inner_path = f"{folder_path}{name}"
                    inner_file_id = str(uuid.uuid4())

                    supabase.storage.from_("maxgptstorage").upload(inner_path, inner_file)

                    existing = supabase.table("files").select("id").eq("id", inner_file_id).execute()
                    if not existing.data:
                        supabase.table("files").insert(
                            {
                                "id": inner_file_id,
                                "file_path": inner_path,
                                "file_name": name,
                                "uploaded_at": datetime.utcnow().isoformat(),
                                "ingested": False,
                                "ingested_at": None,
                                "user_id": user_id,
                                "project_id": project_id,
                            }
                        ).execute()

                    background_tasks.add_task(
                        process_file,
                        file_path=inner_path,
                        file_id=inner_file_id,
                        user_id=user_id
                    )
                    ingested_files.append(name)

            return {"status": "success", "ingested_files": ingested_files}

        else:
            file_path = f"{folder_path}{final_name}"

            supabase.storage.from_("maxgptstorage").upload(
                file_path, contents, {"content-type": file.content_type}
            )

            existing = supabase.table("files").select("id").eq("id", file_id).execute()
            if not existing.data:
                supabase.table("files").insert(
                    {
                        "id": file_id,
                        "file_path": file_path,
                        "file_name": final_name,
                        "uploaded_at": datetime.utcnow().isoformat(),
                        "ingested": False,
                        "ingested_at": None,
                        "user_id": user_id,
                        "project_id": project_id,
                    }
                ).execute()

            background_tasks.add_task(
                process_file,
                file_path=file_path,
                file_id=file_id,
                user_id=user_id
            )

            return {"status": "success", "file_path": file_path}

    except Exception as e:
        print(f"❌ Upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
