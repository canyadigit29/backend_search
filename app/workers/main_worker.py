import logging
import asyncio
from app.core.supabase_client import supabase
from app.services.file_processing_service import FileProcessingService
from app.api.Responses.vs_ingest_worker import upload_missing_files_to_vector_store

logger = logging.getLogger(__name__)

class MainWorker:

    @staticmethod
    async def run_ocr_task():
        """
        Finds and processes files that need OCR.
        """
        logger.info("OCR Task: Checking for files needing OCR...")
        try:
            # Find files that are PDFs, haven't been scanned, and where ocr_needed is true
            logger.info(
                "supabase_op: table.select",
                extra={"table": "files", "filter": {"ocr_needed": True, "ocr_scanned": False}, "columns": ["id"]},
            )
            files_to_ocr = supabase.table("files").select("id") \
                .eq("ocr_needed", True) \
                .eq("ocr_scanned", False) \
                .execute()

            if not files_to_ocr.data:
                logger.info("OCR Task: No files found requiring OCR.")
                return

            logger.info(f"OCR Task: Found {len(files_to_ocr.data)} file(s) to OCR.")
            for file in files_to_ocr.data:
                try:
                    FileProcessingService.process_file_for_ocr(file['id'])
                except Exception as e:
                    logger.error(f"OCR Task: Error during OCR for file {file['id']}: {e}")

        except Exception as e:
            logger.error(f"OCR Task: Error fetching files for OCR: {e}")

    @staticmethod
    async def run_ingestion_task():
        """
        Legacy ingestion (chunk/embed) is deprecated. Vector Store attach is handled by the VS ingest worker.
        Keeping this method as a no-op to preserve scheduling flow.
        """
        logger.info("Ingestion Task: Legacy ingestion disabled; skipping.")
        return

    @staticmethod
    async def run_main_loop(interval_seconds=3600):
        """
        The main loop for the background worker.
        """
        logger.info(f"Starting main worker loop. Will run every {interval_seconds} seconds.")
        while True:
            logger.info("--- Worker cycle starting ---")
            await MainWorker.run_ocr_task()
            await MainWorker.run_ingestion_task()
            # Attach eligible files to Vector Store (Responses flow)
            try:
                await upload_missing_files_to_vector_store()
            except Exception as e:
                logger.warning(f"VS ingest worker run encountered an error (continuing): {e}")
            logger.info(f"--- Worker cycle complete. Sleeping for {interval_seconds} seconds. ---")
            await asyncio.sleep(interval_seconds)