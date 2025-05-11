from fastapi import Query

@router.get("/files")
async def list_files(project_name: str = Query(None)):
    try:
        # Default: no filtering, fetch all user's files
        filter_query = supabase.table("files").select("*").eq("user_id", USER_ID)

        # Optional: filter by project name
        if project_name:
            project_lookup = supabase.table("projects").select("id").eq("user_id", USER_ID).eq("name", project_name).execute()
            if not project_lookup.data:
                raise HTTPException(status_code=404, detail=f"No project found with name '{project_name}'")
            project_id = project_lookup.data[0]["id"]
            filter_query = filter_query.eq("project_id", project_id)

        result = filter_query.order("uploaded_at", desc=True).execute()

        return {"files": result.data}

    except Exception as e:
        print(f"❌ Failed to list files: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to list files: {str(e)}")
