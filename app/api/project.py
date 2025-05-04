
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.supabase_client import supabase
import uuid
import datetime

router = APIRouter()

USER_ID = "2532a036-5988-4e0b-8c0e-b0e94aabc1c9"

class ProjectRequest(BaseModel):
    project_name: str
    description: str = ""

@router.post("/project")
async def create_new_project(request: ProjectRequest):
    try:
        print("➡️ Checking if project already exists...")
        existing_project = (
            supabase.table("projects")
            .select("id")
            .eq("name", request.project_name)
            .maybe_single()
            .execute()
        )
        print(f"🔍 Existing project check result: {existing_project}")

        if existing_project and getattr(existing_project, "data", None):
            print("⚠️ Project already exists.")
            raise HTTPException(status_code=400, detail="Project name already exists.")

        print("✅ Creating new project record in database...")
        project_id = str(uuid.uuid4())
        created_at = datetime.datetime.utcnow().isoformat()

        insert_response = supabase.table("projects").insert({
            "id": project_id,
            "name": request.project_name,
            "description": request.description,
            "created_at": created_at
        }).execute()
        print(f"🧾 Insert response: {insert_response}")

        if not insert_response or getattr(insert_response, "error", None):
            raise Exception(f"Failed to create project: {getattr(insert_response.error, 'message', 'unknown')}")

        folder_path = f"{USER_ID}/{request.project_name}/"
        print(f"📁 Checking for folder: {folder_path}")
        list_response = supabase.storage.from_("maxgptstorage").list(path=f"{USER_ID}/", options={"limit": 100})
        print(f"📂 Folder list response: {list_response}")

        folder_data = getattr(list_response, "data", [])
        existing_folders = [
            item["name"]
            for item in folder_data or []
            if isinstance(item, dict)
            and item.get("metadata", {}).get("type") == "folder"
        ]
        print(f"📁 Existing folders: {existing_folders}")

        if request.project_name not in existing_folders:
            print("📤 Folder doesn't exist — uploading placeholder to create it...")
            upload_response = supabase.storage.from_("maxgptstorage").upload(
                f"{folder_path}.init",
                b"",
                {"content-type": "text/plain"}
            )
            print(f"📤 Upload response: {upload_response}")

            if not upload_response or getattr(upload_response, "error", None):
                raise Exception(f"Failed to create folder: {getattr(upload_response.error, 'message', 'unknown')}")

        return {
            "message": "Project created successfully.",
            "project_id": project_id,
            "project_name": request.project_name
        }

    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/projects")
async def list_projects():
    try:
        response = supabase.table("projects").select("*").execute()
        print(f"📋 Project list response: {response}")

        if not response or getattr(response, "error", None):
            raise HTTPException(status_code=500, detail="Failed to fetch project list")

        return getattr(response, "data", [])

    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
