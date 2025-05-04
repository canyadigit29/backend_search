from fastapi import APIRouter, HTTPException
from supabase import create_client
from app.core.config import settings
from app.api.chunk import chunk_file
from app.api.embed import embed_chunks
from datetime import datetime
import time

router = APIRouter()
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE)

@router.post("/ingest_unprocessed")
async def ingest_unprocessed():
    storage_list = supabase.storage.from_("maxgptstorage").list("uploads/")
    if not storage_list:
        raise HTTPException(status_code=404, detail="No files found in storage.")

    for item in storage_list:
        file_path = f"uploads/{item['name']}"

        # 🔍 Try to infer project name from file path
        path_parts = file_path.split("/")
        project_folder = path_parts[1] if len(path_parts) > 1 else None

        # 📡 Query Supabase to find project_id
        project_id = None
        if project_folder:
            project_res = supabase.table("projects").select("id").eq("name", project_folder).eq("user_id", "2532a036-5988-4e0b-8c0e-b0e94aabc1c9").execute()
            if project_res and project_res.data:
                project_id = project_res.data[0]["id"]

        # Check if already ingested
        file_check = supabase.table("files").select("*").eq("file_path", file_path).execute()
        file_record = file_check.data[0] if file_check.data else None
        if file_record and file_record.get("ingested") is True:
            continue

        try:
            # Ensure file is registered and capture the UUID
            if not file_record:
                insert_data = {
                    "file_path": file_path,
                    "file_name": item["name"],
                    "ingested": False
                }
                if project_id:
                    insert_data["project_id"] = project_id

                insert_result = supabase.table("files").insert(insert_data).execute()
                file_record = insert_result.data[0]

            real_file_id = file_record["id"]
            user_id = file_record.get("user_id", None)

            # ✅ Use shared process function
            process_file(file_path, real_file_id, user_id)

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")

    return {"status": "success", "message": "Ingestion complete"}

# ✅ Shared function for upload-triggered or bulk ingestion
def process_file(file_path: str, file_id: str, user_id: str = None):
    print(f"⚙️ Processing file: {file_path} (ID: {file_id}, User: {user_id})")

    # ✅ Wait up to 2 minutes for file row to exist
    max_retries = 24
    retry_interval = 5.0  # seconds
    file_record = None

    for attempt in range(max_retries):
        result = supabase.table("files").select("*").eq("id", file_id).execute()
        if result and result.data:
            file_record = result.data[0]
            break
        print(f"⏳ Waiting for file to appear in DB... attempt {attempt + 1}")
        time.sleep(retry_interval)

    if not file_record:
        raise Exception(f"File record not found after {max_retries} retries: {file_path}")

    # Chunk the file
    chunk_file(file_id, user_id=user_id)

    # Embed the chunks
    embed_chunks(file_id)

    # Mark the file as ingested
    supabase.table("files").update({
        "ingested": True,
        "ingested_at": datetime.utcnow().isoformat()
    }).eq("id", file_id).execute()
