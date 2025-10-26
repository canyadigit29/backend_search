import logging
import asyncio
from app.core.supabase_client import supabase
from app.services.file_processing_service import FileProcessingService

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
        Finds and processes files that are ready for ingestion.
        """
        logger.info("Ingestion Task: Checking for un-ingested files...")
        try:
            # Find files that are not ingested yet.
            # This will include new files and files that just finished OCR.
            files_to_ingest = supabase.table("files").select("id").eq("ingested", False).execute()

            if not files_to_ingest.data:
                logger.info("Ingestion Task: No new files to ingest.")
                return
            
            logger.info(f"Ingestion Task: Found {len(files_to_ingest.data)} file(s) to ingest.")
            for file in files_to_ingest.data:
                # We need to double-check the OCR status of the file
                file_details = supabase.table("files").select("id, ocr_needed, ocr_scanned").eq("id", file['id']).single().execute().data
                
                # Skip if OCR is needed but not yet complete
                if file_details and file_details.get('ocr_needed') and not file_details.get('ocr_scanned'):
                    logger.info(f"Ingestion Task: Skipping file {file['id']} because it is pending OCR completion.")
                    continue

                try:
                    FileProcessingService.process_file_for_ingestion(file['id'])
                except Exception as e:
                    logger.error(f"Ingestion Task: Error during ingestion for file {file['id']}: {e}")

        except Exception as e:
            logger.error(f"Ingestion Task: Error fetching files for ingestion: {e}")

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
            logger.info(f"--- Worker cycle complete. Sleeping for {interval_seconds} seconds. ---")
            await asyncio.sleep(interval_seconds)