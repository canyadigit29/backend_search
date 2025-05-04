from fastapi import APIRouter, File, UploadFile, Form, HTTPException, BackgroundTasks
from supabase import create_client
from app.core.config import settings
from datetime import datetime
import uuid
from app.api.ingest_unprocessed import process_file  # ✅ Fixed import location

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
            "project_id": project_id  # ✅ Inject project_id
        }, on_conflict="file_path").execute()

        # 🚀 Trigger background chunk+embed
        background_tasks.add_task(process_file, file_path=file_path, file_id=file_id, user_id=USER_ID)

        return {"status": "success", "file_path": file_path}

    except Exception as e:
        print(f"❌ Upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
