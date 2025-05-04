from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.supabase_client import supabase
import uuid
import datetime

router = APIRouter()

USER_ID = "2532a036-5988-4e0b-8c0e-b0e94aabc1c9"  # Replace with dynamic auth later

class ProjectRequest(BaseModel):
    project_name: str
    description: str = ""

@router.post("/project")
async def create_new_project(request: ProjectRequest):
    try:
        print("➡️ Step 1: Checking for existing project...")
        existing_project = (
            supabase.table("projects")
            .select("id")
            .eq("name", request.project_name)
            .eq("user_id", USER_ID)
            .maybe_single()
            .execute()
        )
        print(f"🔍 Project lookup result: {existing_project}")

        if existing_project and getattr(existing_project, "data", None):
            print("⚠️ Project already exists.")
            raise HTTPException(status_code=400, detail="Project name already exists.")

        print("✅ Step 2: Inserting new project record...")
        project_id = str(uuid.uuid4())
        created_at = datetime.datetime.utcnow().isoformat()

        insert_response = (
            supabase.table("projects")
            .insert({
                "id": project_id,
                "name": request.project_name,
                "description": request.description,
                "created_at": created_at,
                "user_id": USER_ID  # ✅ Insert user_id
            })
            .execute()
        )
        print(f"🧾 Insert response: {insert_response}")

        if not insert_response or getattr(insert_response, "error", None):
            msg = getattr(insert_response.error, "message", "unknown DB insert error")
            raise Exception(f"Database error: {msg}")

        print("📁 Step 3: Checking existing storage folders...")
        folder_path = f"{USER_ID}/{request.project_name}/"
        list_response = supabase.storage.from_("maxgptstorage").list(path=f"{USER_ID}/", options={"limit": 100})
        print(f"📂 Folder list response: {list_response}")

        folder_data = getattr(list_response, "data", [])
        existing_folders = [
            item["name"]
            for item in folder_data or []
            if isinstance(item, dict)
            and item.get("metadata", {}).get("type") == "folder"
        ]
        print(f"📁 Found folders: {existing_folders}")

        if request.project_name not in existing_folders:
            print("📤 Step 4: Creating folder via dummy .init upload...")
            upload_response = supabase.storage.from_("maxgptstorage").upload(
                f"{folder_path}.init",
                b"",
                {"content-type": "text/plain"}
            )
            print(f"📤 Upload response: {upload_response}")

            if not upload_response or getattr(upload_response, "error", None):
                msg = getattr(upload_response.error, "message", "unknown storage upload error")
                raise Exception(f"Storage error: {msg}")

        print("✅ Project created successfully.")
        return {
            "message": "Project created successfully.",
            "project_id": project_id,
            "project_name": request.project_name
        }

    except Exception as e:
        print(f"❌ Final Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ✅ NEW: Filter by user_id
@router.get("/projects")
async def list_projects():
    try:
        print("📦 Fetching all projects for user...")
        response = (
            supabase.table("projects")
            .select("name")
            .eq("user_id", USER_ID)
            .execute()
        )
        return response.data or []
    except Exception as e:
        print(f"❌ Error fetching projects: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
