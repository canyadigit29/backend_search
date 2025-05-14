
import io
import uuid
import zipfile
from datetime import datetime
import io
import uuid
import zipfile
from datetime import datetime

from fastapi import (APIRouter, BackgroundTasks, File, Form, HTTPException,
                     UploadFile)

from app.core.supabase_client import supabase

router = APIRouter()

USER_ID = "2532a036-5988-4e0b-8c0e-b0e94aabc1c9"  # temporary hardcoded user

@router.post("/upload")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project_id: str = Form(...),
):
    try:
        contents = await file.read()

        # 🧠 Resolve project name from project_id
        project_lookup = (
            supabase.table("projects")
            .select("name")
            .eq("id", project_id)
            .eq("user_id", USER_ID)
            .single()
            .execute()
        )
        if not project_lookup.data:
            raise HTTPException(status_code=404, detail="Invalid project_id")

        project_name = project_lookup.data["name"]
        folder_path = f"{USER_ID}/{project_name}/"

        if file.filename.endswith(".zip"):
            extracted = zipfile.ZipFile(io.BytesIO(contents))
            ingested_files = []

            for name in extracted.namelist():
                if name.lower().endswith(
                    (".pdf", ".docx", ".doc", ".rtf", ".txt", ".odt")
                ):
                    inner_file = extracted.read(name)
                    file_id = str(uuid.uuid4())
                    inner_path = f"{folder_path}{name}"

                    supabase.storage.from_("maxgptstorage").upload(inner_path, inner_file)

                    supabase.table("files").upsert(
                        {
                            "id": file_id,
                            "file_path": inner_path,
                            "file_name": name,
                            "uploaded_at": datetime.utcnow().isoformat(),
                            "ingested": False,
                            "ingested_at": None,
                            "user_id": USER_ID,
                            "project_id": project_id,
                        },
                        on_conflict="file_path",
                    ).execute()

                    ingested_files.append(name)

            return {"status": "success", "ingested_files": ingested_files}

        else:
            file_path = f"{folder_path}{file.filename}"
            file_id = str(uuid.uuid4())

            supabase.storage.from_("maxgptstorage").upload(
                file_path, contents, {"content-type": file.content_type}
            )

            supabase.table("files").upsert(
                {
                    "id": file_id,
                    "file_path": file_path,
                    "file_name": file.filename,
                    "uploaded_at": datetime.utcnow().isoformat(),
                    "ingested": False,
                    "ingested_at": None,
                    "user_id": USER_ID,
                    "project_id": project_id,
                },
                on_conflict="file_path",
            ).execute()

            return {"status": "success", "file_path": file_path}

    except Exception as e:
        print(f"❌ Upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

from fastapi import (APIRouter, BackgroundTasks, File, Form, HTTPException,
                     UploadFile)

from app.api.file_ops.ingest import process_file
from app.core.supabase_client import supabase

router = APIRouter()

USER_ID = "2532a036-5988-4e0b-8c0e-b0e94aabc1c9"  # temporary hardcoded user

@router.post("/upload")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project_id: str = Form(...),
):
    try:
        contents = await file.read()

        # 🧠 Resolve project name from project_id
        project_lookup = (
            supabase.table("projects")
            .select("name")
            .eq("id", project_id)
            .eq("user_id", USER_ID)
            .single()
            .execute()
        )
        if not project_lookup.data:
            raise HTTPException(status_code=404, detail="Invalid project_id")

        project_name = project_lookup.data["name"]
        folder_path = f"{USER_ID}/{project_name}/"

        if file.filename.endswith(".zip"):
            extracted = zipfile.ZipFile(io.BytesIO(contents))
            ingested_files = []

            for name in extracted.namelist():
                if name.lower().endswith(
                    (".pdf", ".docx", ".doc", ".rtf", ".txt", ".odt")
                ):
                    inner_file = extracted.read(name)
                    file_id = str(uuid.uuid4())
                    inner_path = f"{folder_path}{name}"

                    supabase.storage.from_("maxgptstorage").upload(inner_path, inner_file)

                    supabase.table("files").upsert(
                        {
                            "id": file_id,
                            "file_path": inner_path,
                            "file_name": name,
                            "uploaded_at": datetime.utcnow().isoformat(),
                            "ingested": False,
                            "ingested_at": None,
                            "user_id": USER_ID,
                            "project_id": project_id,
                        },
                        on_conflict="file_path",
                    ).execute()

                    background_tasks.add_task(
                        process_file,
                        file_path=inner_path,
                        file_id=file_id,
                        user_id=USER_ID,
                    )
                    ingested_files.append(name)

            return {"status": "success", "ingested_files": ingested_files}

        else:
            file_path = f"{folder_path}{file.filename}"
            file_id = str(uuid.uuid4())

            supabase.storage.from_("maxgptstorage").upload(
                file_path, contents, {"content-type": file.content_type}
            )

            supabase.table("files").upsert(
                {
                    "id": file_id,
                    "file_path": file_path,
                    "file_name": file.filename,
                    "uploaded_at": datetime.utcnow().isoformat(),
                    "ingested": False,
                    "ingested_at": None,
                    "user_id": USER_ID,
                    "project_id": project_id,
                },
                on_conflict="file_path",
            ).execute()

            background_tasks.add_task(
                process_file, file_path=file_path, file_id=file_id, user_id=USER_ID
            )

            return {"status": "success", "file_path": file_path}

    except Exception as e:
        print(f"❌ Upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
