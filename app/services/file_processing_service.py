import os
import uuid
import logging
import re
from datetime import datetime
from pathlib import Path

from app.core.supabase_client import supabase
from app.core.config import settings
from app.core.extract_text import extract_text
import inspect

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
        """
        Legacy embedding/chunking ingestion is deprecated.
        The Responses-based flow uses app.api.Responses.vs_ingest_worker to attach files to the Vector Store.
        This method is now a no-op to avoid importing legacy modules.
        """
        logger.info(f"[ingest] Skipping legacy embedding ingestion for file_id={file_id}; using VS ingest worker instead.")
        return

    @staticmethod
    def process_file_for_ocr(file_id: str):
        """
        OCR pipeline using OCRmyPDF to produce a single PDF with an embedded text layer.
        We overwrite the original PDF in Supabase Storage (upsert) so downstream ingestion
        uses the enhanced file directly. Sets files.ocr_scanned=true and files.ocr_needed=false.
        """
        logger.info(f"Starting OCRmyPDF process for file_id: {file_id}")
        logger.info(
            "supabase_op: table.select",
            extra={"table": "files", "filter": {"id": file_id}, "single": True},
        )
        file_record = supabase.table("files").select("*").eq("id", file_id).single().execute().data
        if not file_record:
            raise Exception(f"File record not found for ID: {file_id}")

        file_path = file_record["file_path"]
        # Download original PDF
        logger.info(
            "supabase_op: storage.download",
            extra={"bucket": settings.SUPABASE_STORAGE_BUCKET, "path": file_path},
        )
        pdf_bytes = supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).download(file_path)

        # Run OCRmyPDF and overwrite the original PDF with an OCR'd (text-layer) PDF
        import tempfile
        import ocrmypdf

        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file_path).suffix or ".pdf") as tmp_in:
            tmp_in.write(pdf_bytes)
            tmp_in_path = tmp_in.name

        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file_path).suffix or ".pdf") as tmp_out:
            tmp_out_path = tmp_out.name

        try:
            # Use OCRmyPDF's own detection so pages with text are preserved (no force_ocr).
            # Hardcoded recommended defaults: language=eng, deskew=true, rotate_pages=true, threshold=15, optimize=1.
            ocrmypdf.ocr(
                tmp_in_path,
                tmp_out_path,
                skip_text=True,
                # Do NOT force OCR; rely on detection to OCR image-only pages
                # force_ocr=False,
                language="eng",
                deskew=True,
                rotate_pages=True,
                rotate_pages_threshold=15,
                optimize=1,
                progress_bar=False,
            )

            with open(tmp_out_path, "rb") as fh:
                ocr_pdf_bytes = fh.read()

            # Upload back to the same storage path with upsert=true to replace the original file
            logger.info(
                "supabase_op: storage.upload (upsert)",
                extra={"bucket": settings.SUPABASE_STORAGE_BUCKET, "path": file_path, "content_type": "application/pdf", "upsert": True},
            )
            supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).upload(
                file_path,
                ocr_pdf_bytes,
                {"content-type": "application/pdf", "upsert": "true"},
            )
        finally:
            try:
                if os.path.exists(tmp_in_path):
                    os.remove(tmp_in_path)
            except Exception:
                pass
            try:
                if os.path.exists(tmp_out_path):
                    os.remove(tmp_out_path)
            except Exception:
                pass

        # Update DB flags (no separate text file stored)
        logger.info(
            "supabase_op: table.update",
            extra={"table": "files", "filter": {"id": file_id}, "fields": ["ocr_scanned", "ocr_needed"]},
        )
        supabase.table("files").update({
            "ocr_scanned": True,
            "ocr_needed": False,
        }).eq("id", file_id).execute()
        logger.info(f"✅ Successfully performed OCRmyPDF on file: {file_record['name']} (ID: {file_id}); replaced original with text-layer PDF")