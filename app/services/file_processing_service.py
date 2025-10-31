import os
import uuid
import logging
import re
from datetime import datetime
from pathlib import Path

from app.core.supabase_client import supabase
from app.core.config import settings
from app.core.extract_text import extract_text
from app.api.file_ops.chunk import chunk_file
import inspect
from app.api.file_ops.embed import embed_chunks
from app.api.file_ops.ocr import ocr_pdf

logger = logging.getLogger(__name__)

class FileProcessingService:
    
    @staticmethod
    async def upload_and_register_file(user_id: str, file_content: bytes, file_name: str, content_type: str, sharing: str = "private"):
        try:
            file_extension = os.path.splitext(file_name)[1]
            file_path = f"{uuid.uuid4()}{file_extension}"
            
            # Override with specified user_id and sharing status
            user_id = "773e2630-2cca-44c3-957c-0cf5ccce7411"
            sharing = "public"

            insert_data = {
                "user_id": user_id,
                "name": file_name,
                "file_path": file_path,
                "type": content_type,
                "sharing": sharing,
                "description": "",
                "size": len(file_content),
                "tokens": 0,
                "created_at": datetime.utcnow().isoformat(),
                "ingested": False,
                "ocr_needed": False,
                "ocr_scanned": False,
            }
            logger.info(
                "supabase_op: table.insert",
                extra={
                    "table": "files",
                    "fields": list(insert_data.keys()),
                    "size_bytes": insert_data.get("size"),
                },
            )
            inserted_file = supabase.table("files").insert(insert_data).execute()
            if not inserted_file.data:
                raise Exception("Failed to create file record in database.")
            file_id = inserted_file.data[0]['id']
            logger.info(f"Created file record with ID: {file_id} for path: {file_path}")
            logger.info(
                "supabase_op: storage.upload",
                extra={
                    "bucket": settings.SUPABASE_STORAGE_BUCKET,
                    "path": file_path,
                    "content_type": content_type,
                },
            )
            supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).upload(
                file_path, file_content, {"content-type": content_type}
            )
            logger.info(f"Successfully uploaded file to storage at: {file_path}")
            return {"file_id": file_id, "file_path": file_path}
        except Exception as e:
            logger.error(f"Error in upload_and_register_file: {e}")
            raise

    @staticmethod
    def process_file_for_ingestion(file_id: str):
        logger.info(f"Starting ingestion process for file_id: {file_id}")
        logger.info(
            "supabase_op: table.select",
            extra={"table": "files", "filter": {"id": file_id}, "single": True},
        )
        file_record = supabase.table("files").select("*").eq("id", file_id).single().execute().data
        if not file_record:
            raise Exception(f"File record not found for ID: {file_id}")

        file_path = file_record["file_path"]
        text = None

        if file_record.get("ocr_scanned") and file_record.get("ocr_text_path"):
            logger.info(f"Found OCR text at {file_record['ocr_text_path']}. Loading it.")
            try:
                logger.info(
                    "supabase_op: storage.download",
                    extra={
                        "bucket": settings.SUPABASE_STORAGE_BUCKET,
                        "path": file_record.get("ocr_text_path"),
                    },
                )
                response = supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).download(file_record["ocr_text_path"])
                if response:
                    text = response.decode('utf-8')
                    logger.info(f"Successfully loaded OCR text. Length: {len(text)} chars.")
                else:
                    logger.warning("Failed to download OCR text file, it may be empty.")
            except Exception as e:
                logger.error(f"Error downloading OCR text for file {file_id}: {e}")
        
        if text is None:
            logger.info(f"No valid OCR text found. Attempting direct text extraction from: {file_path}")
            local_temp_path = None
            try:
                logger.info(
                    "supabase_op: storage.download",
                    extra={"bucket": settings.SUPABASE_STORAGE_BUCKET, "path": file_path},
                )
                response = supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).download(file_path)
                local_temp_path = f"/tmp/{file_id}{Path(file_path).suffix}"
                with open(local_temp_path, "wb") as f:
                    f.write(response)
                extracted_content = extract_text(local_temp_path)
                if file_path.lower().endswith('.pdf') and (extracted_content is None or len(re.sub(r'---PAGE \d+---', '', extracted_content).strip()) < 100):
                    logger.warning(f"Direct extraction yielded little or no content. Marking for OCR.")
                    logger.info(
                        "supabase_op: table.update",
                        extra={"table": "files", "filter": {"id": file_id}, "fields": ["ocr_needed"]},
                    )
                    supabase.table("files").update({"ocr_needed": True}).eq("id", file_id).execute()
                    FileProcessingService.process_file_for_ocr(file_id)
                    return
                else:
                    text = extracted_content
            except Exception as e:
                logger.error(f"An error occurred during direct text extraction: {e}")
            finally:
                if local_temp_path and os.path.exists(local_temp_path):
                    os.remove(local_temp_path)

        if not text or not text.strip():
            logger.warning(f"No text could be extracted from {file_path}. Skipping chunking and embedding.")
            return

        logger.info(f"Text extracted successfully. Length: {len(text)} chars. Now chunking.")
        # Backward-compatible call: some deployments may still have an older chunk_file signature
        try:
            sig = inspect.signature(chunk_file)
            params = sig.parameters
            if "user_id" in params:
                logger.info("Using chunk_file with user_id.")
                chunking_result = chunk_file(
                    file_id=file_id,
                    file_name=file_record["name"],
                    text=text,
                    description=file_record["description"],
                    user_id=file_record["user_id"],
                    sharing=file_record["sharing"]
                )
            elif "description" in params:
                logger.info("Using chunk_file(file_id, file_name, text, description) signature.")
                chunking_result = chunk_file(
                    file_id=file_id,
                    file_name=file_record["name"],
                    text=text,
                    description=file_record["description"],
                )
            elif "file_name" in params:
                logger.info("Using chunk_file(file_id, file_name, text) signature.")
                chunking_result = chunk_file(
                    file_id=file_id,
                    file_name=file_record["name"],
                    text=text,
                )
            else:
                logger.info("Using legacy chunk_file(file_id, text) signature.")
                # Call using positional args for compatibility
                chunking_result = chunk_file(file_id, text)  # type: ignore
        except Exception as e:
            logger.error(f"Error invoking chunk_file: {e}")
            chunking_result = {"chunks": []}

        # Normalize chunking_result to a list of chunks
        chunks = None
        if isinstance(chunking_result, dict):
            chunks = chunking_result.get("chunks")
        elif isinstance(chunking_result, list):
            chunks = chunking_result
        else:
            chunks = []

        if not chunks:
            logger.warning(f"No chunks were generated for {file_record['name']}. Ingestion skipped.")
            return

        logger.info(f"Generated {len(chunks)} chunks. Now embedding.")
        embed_chunks(chunks)
        logger.info(
            "supabase_op: table.update",
            extra={"table": "files", "filter": {"id": file_id}, "fields": ["ingested"]},
        )
        supabase.table("files").update(
            {"ingested": True}
        ).eq("id", file_id).execute()
        logger.info(f"✅ Successfully ingested file: {file_record['name']} (ID: {file_id})")

    @staticmethod
    def process_file_for_ocr(file_id: str):
        logger.info(f"Starting OCR process for file_id: {file_id}")
        logger.info(
            "supabase_op: table.select",
            extra={"table": "files", "filter": {"id": file_id}, "single": True},
        )
        file_record = supabase.table("files").select("*").eq("id", file_id).single().execute().data
        if not file_record:
            raise Exception(f"File record not found for ID: {file_id}")
        ocr_pdf(file_path=file_record["file_path"], file_id=file_id)
        logger.info(f"✅ Successfully performed OCR on file: {file_record['name']} (ID: {file_id})")