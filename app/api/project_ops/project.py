import datetime
import uuid
import os

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.core.supabase_client import supabase

router = APIRouter()

# Optional default user id for backwards compatibility
DEFAULT_USER_ID = os.getenv("DEFAULT_USER_ID")


class ProjectRequest(BaseModel):
    project_name: str
    description: str = ""


# ✅ NEW: Internal function so chat.py can import directly
async def get_projects(user_id: str, request: Request):
    try:
        print(f"📦 [Internal] Fetching projects (no user_id filtering)")
        response = (
            supabase.table("projects")
            .select("id, name, description, created_at")
            .order("created_at", desc=True)
            .execute()
        )
        if getattr(response, "error", None):
            msg = getattr(response.error, "message", "unknown error")
            raise Exception(f"Supabase query error: {msg}")

        return response.data or []
    except Exception as e:
        print(f"❌ Error fetching projects (internal): {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/project")
async def create_new_project(request: ProjectRequest):
    try:
        print("➡️ Step 1: Checking for existing project...")
        existing_project = (
            supabase.table("projects")
            .select("id")
            .eq("name", request.project_name)
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
            .insert(
                {
                    "id": project_id,
                    "name": request.project_name,
                    "description": request.description,
                    "created_at": created_at,
                }
            )
            .execute()
        )
        print(f"🧾 Insert response: {insert_response}")

        if not insert_response or getattr(insert_response, "error", None):
            msg = getattr(insert_response.error, "message", "unknown DB insert error")
            raise Exception(f"Database error: {msg}")

        print("📁 Step 3: Checking existing storage folders...")
        folder_path = f"{request.project_name}/"
        list_response = supabase.storage.from_("maxgptstorage").list(
            path=f"{request.project_name}/", options={"limit": 100}
        )
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
                f"{folder_path}.init", b"", {"content-type": "text/plain"}
            )
            print(f"📤 Upload response: {upload_response}")

            if not upload_response or getattr(upload_response, "error", None):
                msg = getattr(
                    upload_response.error, "message", "unknown storage upload error"
                )
                raise Exception(f"Storage error: {msg}")

        print("✅ Project created successfully.")
        return {
            "message": "Project created successfully.",
            "project_id": project_id,
            "project_name": request.project_name,
        }

    except Exception as e:
        print(f"❌ Final Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects")
async def list_projects(name: str = Query(None), description: str = Query(None)):
    try:
        print("📦 Fetching projects (no user_id filtering)")
        query = (
            supabase.table("projects")
            .select("id, name, description, created_at")
        )

        if name:
            query = query.ilike("name", f"%{name}%")
        if description:
            query = query.ilike("description", f"%{description}%")

        response = query.order("created_at", desc=True).execute()

        if getattr(response, "error", None):
            msg = getattr(response.error, "message", "unknown error")
            raise Exception(f"Supabase query error: {msg}")

        print(f"📁 Projects retrieved: {len(response.data or [])}")
        return response.data or []

    except Exception as e:
        print(f"❌ Error fetching projects: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/project")
async def delete_project(project_name: str = Query(...)):
    try:
        print(f"🗑️ Deleting project: {project_name}")

        # Step 1: Lookup project
        project_lookup = (
            supabase.table("projects")
            .select("id")
            .eq("name", project_name)
            .maybe_single()
            .execute()
        )
        if not project_lookup or not getattr(project_lookup, "data", None):
            raise HTTPException(
                status_code=404, detail=f"Project '{project_name}' not found."
            )
        project_id = project_lookup.data["id"]

        # Step 2: Delete files linked to this project
        files = (
            supabase.table("files")
            .select("id", "file_name", "file_path")
            .eq("project_id", project_id)
            .execute()
        ).data or []

        file_names = [file["file_name"] for file in files]
        file_paths = [file["file_path"] for file in files]

        if file_paths:
            print(f"🧹 Deleting {len(file_paths)} file(s) from storage.")
            supabase.storage.from_("maxgptstorage").remove(file_paths)

        # Step 3: Delete file chunks
        for file_name in file_names:
            supabase.table("document_chunks").delete().eq(
                "file_name", file_name
            ).execute()

        # Step 4: Delete file records
        supabase.table("files").delete().eq("project_id", project_id).execute()

        # Step 5: Delete the project record
        supabase.table("projects").delete().eq("id", project_id).execute()

        print(f"✅ Deleted project '{project_name}' and all related files/memory.")
        return {"status": "success", "message": f"Project '{project_name}' deleted."}

    except Exception as e:
        print(f"❌ Failed to delete project: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Project deletion failed: {str(e)}"
        )


# ✅ Internal tool call handler
async def delete_project_by_name(project_name: str, user_id: str = None) -> dict:
    try:
        print(f"🗑️ (Internal) Deleting project: {project_name}")

        # Debug: determine which user to operate on
        debug_user = user_id or DEFAULT_USER_ID
        print("📦 (Internal) Listing projects without user filter")
        debug_projects = (
            supabase.table("projects").select("name").execute()
        )
        all_names = [p["name"] for p in debug_projects.data or []]
        print(f"📋 Available project names: {all_names}")

        # Use ilike to avoid 406 errors from spacing/case
        project_lookup = (
            supabase.table("projects")
            .select("id")
            .ilike("name", project_name)
            .maybe_single()
            .execute()
        )

        if not project_lookup or not getattr(project_lookup, "data", None):
            return {"success": False, "error": f"Project '{project_name}' not found."}

        project_id = project_lookup.data["id"]

        files = (
            supabase.table("files")
            .select("id", "file_name", "file_path")
            .eq("project_id", project_id)
            .execute()
        ).data or []

        file_names = [file["file_name"] for file in files]
        file_paths = [file["file_path"] for file in files]

        if file_paths:
            supabase.storage.from_("maxgptstorage").remove(file_paths)

        for file_name in file_names:
            supabase.table("document_chunks").delete().eq(
                "file_name", file_name
            ).execute()

        supabase.table("files").delete().eq("project_id", project_id).execute()
        supabase.table("projects").delete().eq("id", project_id).execute()

        return {"success": True}

    except Exception as e:
        print(f"❌ Internal delete failed: {str(e)}")
        return {"success": False, "error": str(e)}
