from fastapi import APIRouter, HTTPException
from app.core.supabase_client import supabase
import uuid
import fitz  # PyMuPDF
import pytesseract
import os
import textract

router = APIRouter()

@router.post("/chunk")
async def chunk_file(file_id: str):
    try:
        # Retrieve file info
        file_info = supabase.table("files").select("*").eq("id", file_id).single().execute()
        if not file_info.get("data"):
            raise HTTPException(status_code=404, detail="File not found.")

        filepath = file_info["data"]["filepath"]
        filename = file_info["data"]["filename"]
        extension = filename.split(".")[-1].lower()

        # Download file temporarily
        response = supabase.storage.from_("maxgptstorage").download(filepath)
        if isinstance(response, dict) and response.get("error"):
            raise HTTPException(status_code=500, detail="Error downloading file from storage.")

        local_file_path = f"/tmp/{uuid.uuid4()}_{filename}"
        with open(local_file_path, "wb") as f:
            f.write(response)

        # Extract text
        text = ""
        if extension == "pdf":
            doc = fitz.open(local_file_path)
            for page in doc:
                text += page.get_text()
        elif extension in ["png", "jpg", "jpeg", "tiff"]:
            text = pytesseract.image_to_string(local_file_path)
        else:
            try:
                text = textract.process(local_file_path).decode()
            except Exception:
                raise HTTPException(status_code=500, detail="Unsupported file type or extraction failed.")

        # Basic chunking logic
        max_chunk_size = 1000
        chunks = [text[i:i+max_chunk_size] for i in range(0, len(text), max_chunk_size)]

        # Insert chunks into Supabase
        chunk_records = [{
            "id": str(uuid.uuid4()),
            "file_id": file_id,
            "content": chunk
        } for chunk in chunks]

        supabase.table("chunks").insert(chunk_records).execute()

        # Cleanup
        os.remove(local_file_path)

        return {"message": f"File chunked into {len(chunks)} pieces."}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
