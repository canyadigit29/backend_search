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
        # Check for existing project (returns the row or None)
        existing_project = (
            supabase.table("projects")
            .select("id")
            .eq("name", request.project_name)
            .maybe_single()
            .execute()
        )

        if existing_project is not None:
            raise HTTPException(status_code=400, detail="Project name already exists.")

        # Create DB record
        project_id = str(uuid.uuid4())
        created_at = datetime.datetime.utcnow().isoformat()

        insert_response = supabase.table("projects").insert({
            "id": project_id,
            "name": request.project_name,
            "description": request.description,
            "created_at": created_at
        }).execute()

        if insert_response is None:
            raise Exception("Failed to create project (no response).")

        # Check if folder exists
        folder_path = f"{USER_ID}/{request.project_name}/"
        list_response = supabase.storage.from_("maxgptstorage").list(path=f"{USER_ID}/", options={"limit": 100})
        folder_data = list_response or []
        existing_folders = [
            item["name"]
            for item in folder_data
            if isinstance(item, dict) and item.get("metadata", {}).get("type") == "folder"
        ]

        if request.project_name not in existing_folders:
            upload_response = supabase.storage.from_("maxgptstorage").upload(
                f"{folder_path}.init",
                b"init",
                {"content-type": "text/plain"}
            )
            if upload_response is None:
                raise Exception("Failed to create storage folder.")

        return {
            "message": "Project created successfully.",
            "project_id": project_id,
            "project_name": request.project_name
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/projects")
async def list_projects():
    try:
        response = supabase.table("projects").select("*").execute()
        if response is None:
            raise HTTPException(status_code=500, detail="Failed to fetch project list")
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")