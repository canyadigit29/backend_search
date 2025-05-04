from fastapi import APIRouter, HTTPException
from supabase import create_client
from app.core.config import settings
from app.api.chunk import chunk_file
from app.api.embed import embed_chunks
from datetime import datetime

router = APIRouter()
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE)


@router.post("/ingest_unprocessed")
async def ingest_unprocessed():
    storage_list = supabase.storage.from_("maxgptstorage").list("uploads/")
    if not storage_list:
        raise HTTPException(status_code=404, detail="No files found in storage.")

    for item in storage_list:
        file_path = f"uploads/{item['name']}"

        # Check if already ingested
        file_check = supabase.table("files").select("*").eq("file_path", file_path).execute()
        file_record = file_check.data[0] if file_check.data else None
        if file_record and file_record.get("ingested") is True:
            continue

        try:
            # Ensure file is registered and capture the UUID
            if not file_record:
                insert_result = supabase.table("files").insert({
                    "file_path": file_path,
                    "file_name": item["name"],
                    "ingested": False
                }).execute()
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

    # Chunk the file
    chunk_file(file_id, user_id=user_id)

    # Embed the chunks
    embed_chunks(file_id)

    # Mark the file as ingested
    supabase.table("files").update({
        "ingested": True,
        "ingested_at": datetime.utcnow().isoformat()
    }).eq("id", file_id).execute()
