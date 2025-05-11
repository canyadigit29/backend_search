@router.post("/upload")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project_name: str = Form(...)
):
    try:
        import zipfile
        import io

        contents = await file.read()
        folder_path = f"{USER_ID}/{project_name}/"

        # 🔍 Lookup project ID by name
        project_lookup = supabase.table("projects").select("id").eq("user_id", USER_ID).eq("name", project_name).execute()
        if not project_lookup.data:
            raise HTTPException(status_code=404, detail=f"No project found with name '{project_name}'")
        project_id = project_lookup.data[0]["id"]

        # ✅ Check if it's a zip file
        if file.filename.endswith(".zip"):
            extracted = zipfile.ZipFile(io.BytesIO(contents))
            ingested_files = []

            for name in extracted.namelist():
                if name.lower().endswith((".pdf", ".docx", ".doc", ".rtf", ".txt", ".odt")):
                    inner_file = extracted.read(name)
                    file_id = str(uuid.uuid4())
                    inner_path = f"{folder_path}{name}"

                    # Upload each extracted file to Supabase
                    supabase.storage.from_("maxgptstorage").upload(inner_path, inner_file)

                    # Register in DB
                    supabase.table("files").upsert({
                        "id": file_id,
                        "file_path": inner_path,
                        "file_name": name,
                        "uploaded_at": datetime.utcnow().isoformat(),
                        "ingested": False,
                        "ingested_at": None,
                        "user_id": USER_ID,
                        "project_id": project_id
                    }, on_conflict="file_path").execute()

                    # Trigger chunk + embed
                    background_tasks.add_task(process_file, file_path=inner_path, file_id=file_id, user_id=USER_ID)
                    ingested_files.append(name)

            return {"status": "success", "ingested_files": ingested_files}

        else:
            # 🧾 Normal file upload
            file_path = f"{folder_path}{file.filename}"
            file_id = str(uuid.uuid4())

            supabase.storage.from_("maxgptstorage").upload(
                file_path, contents, {"content-type": file.content_type}
            )

            supabase.table("files").upsert({
                "id": file_id,
                "file_path": file_path,
                "file_name": file.filename,
                "uploaded_at": datetime.utcnow().isoformat(),
                "ingested": False,
                "ingested_at": None,
                "user_id": USER_ID,
                "project_id": project_id
            }, on_conflict="file_path").execute()

            background_tasks.add_task(process_file, file_path=file_path, file_id=file_id, user_id=USER_ID)

            return {"status": "success", "file_path": file_path}

    except Exception as e:
        print(f"❌ Upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
