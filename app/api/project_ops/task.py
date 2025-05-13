import datetime
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.supabase_client import supabase

router = APIRouter()


class TaskCreateRequest(BaseModel):
    project_id: str
    description: str
    status: str = "pending"


@router.post("/task")
async def create_task(payload: TaskCreateRequest):
    try:
        task_id = str(uuid.uuid4())
        created_at = datetime.datetime.utcnow().isoformat()

        response = (
            supabase.table("tasks")
            .insert(
                {
                    "id": task_id,
                    "project_id": payload.project_id,
                    "description": payload.description,
                    "status": payload.status,
                    "created_at": created_at,
                }
            )
            .execute()
        )

        if response.get("error"):
            raise Exception(response["error"]["message"])

        return {"message": "Task created", "task_id": task_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/{project_id}")
async def list_tasks(project_id: str):
    try:
        response = (
            supabase.table("tasks")
            .select("*")
            .eq("project_id", project_id)
            .order("created_at", desc=True)
            .execute()
        )

        if response.get("error"):
            raise Exception(response["error"]["message"])

        return response.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
