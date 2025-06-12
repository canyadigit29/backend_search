import os
from pathlib import Path
from app.api.file_ops.ingest import process_file
from app.core.supabase_client import supabase
from uuid import uuid4
from datetime import datetime

# Set the root directory to scan (local folder, not Supabase bucket)
ROOT_DIR = os.path.join(os.path.dirname(__file__), '..')

# Folders to scan
FOLDERS = [
    'Agendas',
    'Minutes',
    'Misc',
    'Ordinaces',
]


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


def ingest_all_local_files():
    for folder in FOLDERS:
        folder_path = os.path.abspath(os.path.join(ROOT_DIR, folder))
        if not os.path.exists(folder_path):
            continue
        for dirpath, _, filenames in os.walk(folder_path):
            for filename in filenames:
                if filename.lower().endswith('.pdf'):
                    file_path = os.path.relpath(os.path.join(dirpath, filename), ROOT_DIR)
                    print(f"[INFO] Ingesting: {file_path}")
                    file_id = ensure_file_record(file_path)
                    try:
                        process_file(file_path, file_id)
                    except Exception as e:
                        print(f"[ERROR] Failed to ingest {file_path}: {e}")

if __name__ == "__main__":
    ingest_all_local_files()
