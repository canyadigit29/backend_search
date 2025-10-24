# Re-linting file
import os
import uuid
import logging
from datetime import datetime
from pathlib import Path

from app.core.supabase_client import supabase
from app.core.config import settings
from app.core.extract_text import extract_text
from app.api.file_ops.chunk import chunk_file
from app.api.file_ops.embed import embed_chunks
from app.api.file_ops.ocr import ocr_pdf

logger = logging.getLogger(__name__)

class FileProcessingService:
    
    @staticmethod
    async def upload_and_register_file(file_content: bytes, file_name: str, content_type: str):
        """
        Creates a file record in the database and uploads the file to storage.
        This is the entry point for all new files.
        """
        try:
            file_extension = os.path.splitext(file_name)[1]
            file_path = f"{uuid.uuid4()}{file_extension}"

            insert_data = {
                "file_name": file_name,
                "file_path": file_path,
                "file_type": content_type,
                "created_at": datetime.utcnow().isoformat(),
                "ingested": False,
                "ocr_needed": False,
                "ocr_scanned": False,
            }
            
            inserted_file = supabase.table("files").insert(insert_data).execute()

            if not inserted_file.data:
                raise Exception("Failed to create file record in database.")

            file_id = inserted_file.data[0]['id']
            logger.info(f"Created file record with ID: {file_id} for path: {file_path}")

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
        Orchestrates the ingestion process for a single file.
        This includes text extraction, chunking, and embedding.
        """
        logger.info(f"Starting ingestion process for file_id: {file_id}")
        
        file_record = supabase.table("files").select("*").eq("id", file_id).single().execute().data
        if not file_record:
            raise Exception(f"File record not found for ID: {file_id}")

        file_path = file_record["file_path"]
        text = None

        # Priority 1: Use pre-existing OCR text if available and valid.
        if file_record.get("ocr_scanned") and file_record.get("ocr_text_path"):
            logger.info(f"Found OCR text at {file_record['ocr_text_path']}. Loading it.")
            try:
                response = supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).download(file_record["ocr_text_path"])
                if response:
                    text = response.decode('utf-8')
                    logger.info(f"Successfully loaded OCR text. Length: {len(text)} chars.")
                else:
                    logger.warning("Failed to download OCR text file, it may be empty.")
            except Exception as e:
                logger.error(f"Error downloading OCR text for file {file_id}: {e}")
        
        # Priority 2: If no valid OCR text was loaded, try direct extraction.
        if text is None:
            logger.info(f"No valid OCR text found. Attempting direct text extraction from: {file_path}")
            local_temp_path = None
            try:
                response = supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).download(file_path)
                
                local_temp_path = f"/tmp/{file_id}{Path(file_path).suffix}"
                with open(local_temp_path, "wb") as f:
                    f.write(response)

                extracted_content = extract_text(local_temp_path)
                
                # Check if the extracted content is meaningful
                if file_path.lower().endswith('.pdf') and (extracted_content is None or len(re.sub(r'---PAGE \d+---', '', extracted_content).strip()) < 100):
                    logger.warning(f"Direct extraction yielded little or no content. Marking for OCR.")
                    supabase.table("files").update({"ocr_needed": True}).eq("id", file_id).execute()
                    FileProcessingService.process_file_for_ocr(file_id)
                    return # Stop this ingestion cycle; OCR will run and trigger a new one.
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
        # Pass the extracted text directly to the chunking function
        chunking_result = chunk_file(file_id, text=text)
        chunks = chunking_result.get("chunks")

        if not chunks:
            logger.warning(f"No chunks were generated for {file_path}. Ingestion skipped.")
            return

        logger.info(f"Generated {len(chunks)} chunks. Now embedding.")
        embed_chunks(chunks)

        supabase.table("files").update(
            {"ingested": True}
        ).eq("id", file_id).execute()
        
        logger.info(f"✅ Successfully ingested file: {file_record['file_name']} (ID: {file_id})")

    @staticmethod
    def process_file_for_ocr(file_id: str):
        """
        Orchestrates the OCR process for a single file.
        """
        logger.info(f"Starting OCR process for file_id: {file_id}")
        file_record = supabase.table("files").select("*").eq("id", file_id).single().execute().data
        if not file_record:
            raise Exception(f"File record not found for ID: {file_id}")

        ocr_pdf(file_path=file_record["file_path"], file_id=file_id)
        logger.info(f"✅ Successfully performed OCR on file: {file_record['file_name']} (ID: {file_id})")