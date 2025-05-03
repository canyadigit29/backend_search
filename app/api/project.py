from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.supabase_client import supabase
import uuid
import datetime

router = APIRouter()

class ProjectRequest(BaseModel):
    project_name: str
    description: str = ""

@router.post("/project")
async def create_new_project(request: ProjectRequest):
    try:
        existing_project = supabase.table("projects").select("id").eq("project_name", request.project_name).single().execute()
        if existing_project.get("data"):
            raise HTTPException(status_code=400, detail="Project name already exists.")

        project_id = str(uuid.uuid4())
        created_at = datetime.datetime.utcnow().isoformat()

        insert_response = supabase.table("projects").insert({
            "id": project_id,
            "project_name": request.project_name,
            "description": request.description,
            "created_at": created_at
        }).execute()

        if insert_response.get("error"):
            raise Exception(f"Failed to create project: {insert_response['error']['message']}")

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

        if response.get("error"):
            raise HTTPException(status_code=500, detail="Failed to fetch project list")

        return response.get("data", [])

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
