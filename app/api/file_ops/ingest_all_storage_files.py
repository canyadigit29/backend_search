import os
from app.core.supabase_client import supabase
from app.api.file_ops.ingest import process_file
from uuid import uuid4
from datetime import datetime

BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "files")


def list_all_files_in_bucket(bucket: str):
    """Recursively list all files in the given Supabase storage bucket, with debug output."""
    all_files = []
    # Use the Supabase Storage API to list all files recursively
    # The supabase-py client returns a list of dicts with 'name' and 'id' keys
    def walk(prefix=""):
        print(f"[DEBUG] Listing: '{prefix}'")
        page = supabase.storage.from_(bucket).list(prefix)
        print(f"[DEBUG] Result for '{prefix}': {page}")
        if not page:
            return
        for obj in page:
            print(f"[DEBUG] Object: {obj}")
            if obj.get("id") and obj.get("name"):
                if obj.get("name").endswith("/"):
                    # It's a folder, recurse
                    walk(prefix + obj["name"])
                else:
                    # It's a file
                    all_files.append(prefix + obj["name"])

    walk("")
    print(f"[DEBUG] All files found: {all_files}")
    return all_files


def ensure_file_record(file_path: str):
    """Ensure a record exists in the files table for the given file_path. Returns file_id."""
    # Try to find an existing record
    result = supabase.table("files").select("id").eq("file_path", file_path).maybe_single().execute()
    if result.data and result.data.get("id"):
        return result.data["id"]
    # Otherwise, create a new record
    file_id = str(uuid4())
    file_name = os.path.basename(file_path)
    supabase.table("files").insert({
        "id": file_id,
        "file_path": file_path,
        "file_name": file_name,
        "created_at": datetime.utcnow().isoformat(),
        "description": file_name,
        "type": "pdf" if file_name.lower().endswith(".pdf") else "other",
        "size": 0,
        "tokens": 0,
        "user_id": None,
        "sharing": "private"
    }).execute()
    return file_id


def ingest_all_storage_files():
    files = list_all_files_in_bucket(BUCKET)
    print(f"[INFO] Found {len(files)} files in bucket '{BUCKET}'")
    for file_path in files:
        print(f"[INFO] Ingesting: {file_path}")
        file_id = ensure_file_record(file_path)
        try:
            process_file(file_path, file_id)
        except Exception as e:
            print(f"[ERROR] Failed to ingest {file_path}: {e}")


if __name__ == "__main__":
    ingest_all_storage_files()
