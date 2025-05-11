from fastapi import APIRouter, File, UploadFile, Form, HTTPException, BackgroundTasks, Query
from supabase import create_client
from app.core.config import settings
from datetime import datetime
import uuid
from app.api.file_ops.ingest import process_file, delete_embedding  # ✅ Added memory purge hook

router = APIRouter()
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE)

USER_ID = "2532a036-5988-4e0b-8c0e-b0e94aabc1c9"  # Temporary hardcoded user ID

@router.post("/upload")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project_name: str = Form(...)
):
    try:
        contents = await file.read()
        folder_path = f"{USER_ID}/{project_name}/"
        file_path = f"{folder_path}{file.filename}"
        file_id = str(uuid.uuid4())

        # 🔍 Lookup project ID by name
        project_lookup = supabase.table("projects").select("id").eq("user_id", USER_ID).eq("name", project_name).execute()
        if not project_lookup.data:
            raise HTTPException(status_code=404, detail=f"No project found with name '{project_name}'")
        project_id = project_lookup.data[0]["id"]

        # 📤 Upload to Supabase Storage
        print(f"📤 Uploading file to: {file_path}")
        upload_response = supabase.storage.from_("maxgptstorage").upload(
            file_path, contents, {"content-type": file.content_type}
        )
        print(f"📤 Upload response: {upload_response}")

        # 📝 Register file in DB with user_id and project_id
        supabase.table("files").upsert({
            "id": file_id,
            "file_path": file_path,
            "file_name": file.filename,
            "uploaded_at": datetime.utcnow().isoformat(),
            "ingested": False,
            "ingested_at": None,
            "user_id": USER_ID,
            "project_id": project_id
        }, on_conflict="file_path").execute()

        # 🚀 Trigger background chunk+embed
        background_tasks.add_task(process_file, file_path=file_path, file_id=file_id, user_id=USER_ID)

        return {"status": "success", "file_path": file_path}

    except Exception as e:
        print(f"❌ Upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.get("/files")
async def list_files(project_name: str = Query(None)):
    try:
        # Default: no filtering, fetch all user's files
        filter_query = supabase.table("files").select("*").eq("user_id", USER_ID)

        # Optional: filter by project name
        if project_name:
            project_lookup = supabase.table("projects").select("id").eq("user_id", USER_ID).eq("name", project_name).execute()
            if not project_lookup.data:
                raise HTTPException(status_code=404, detail=f"No project found with name '{project_name}'")
            project_id = project_lookup.data[0]["id"]
            filter_query = filter_query.eq("project_id", project_id)

        result = filter_query.order("uploaded_at", desc=True).execute()
        return {"files": result.data}

    except Exception as e:
        print(f"❌ Failed to list files: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to list files: {str(e)}")


@router.delete("/file")
async def delete_file(
    project_name: str = Query(...),
    file_name: str = Query(...)
):
    try:
        # 1. Project lookup
        project_lookup = supabase.table("projects").select("id").eq("user_id", USER_ID).eq("name", project_name).execute()
        if not project_lookup.data:
            raise HTTPException(status_code=404, detail=f"No project found with name '{project_name}'")
        project_id = project_lookup.data[0]["id"]

        # 2. File lookup
        file_query = (
            supabase.table("files")
            .select("*")
            .eq("user_id", USER_ID)
            .eq("project_id", project_id)
            .eq("file_name", file_name)
            .maybe_single()
            .execute()
        )
        file_data = getattr(file_query, "data", None)
        if not file_data:
            raise HTTPException(status_code=404, detail="File not found in project.")

        file_id = file_data["id"]
        file_path = file_data["file_path"]
        is_ingested = file_data["ingested"]

        print(f"🗑️ Deleting file: {file_name} from {project_name}")
        print(f"📁 Supabase path: {file_path} | Ingested: {is_ingested}")

        # 3. Delete from Supabase Storage
        storage_delete = supabase.storage.from_("maxgptstorage").remove([file_path])
        print(f"🧹 Storage delete result: {storage_delete}")

        # 4. Delete from files table
        db_delete = supabase.table("files").delete().eq("id", file_id).execute()
        print(f"🧾 DB delete result: {db_delete}")

        # 5. Delete from memory store if embedded
        if is_ingested:
            print(f"🧠 Deleting vector memory for file_id: {file_id}")
            delete_embedding(file_id=file_id, user_id=USER_ID)

        return {"status": "success", "message": "File deleted and memory purged."}

    except Exception as e:
        print(f"❌ File deletion failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"File deletion failed: {str(e)}")
