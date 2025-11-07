import asyncio
import logging
from app.api.Responses.gdrive_sync import run_responses_gdrive_sync
from app.api.Responses.vs_ingest_worker import upload_missing_files_to_vector_store
from app.core.config import settings
from app.workers.main_worker import MainWorker

# Unified Responses Drive sync + OCR + Vector Store ingest loop (moved from root).
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    interval_minutes = settings.GDRIVE_SYNC_INTERVAL_MINUTES or 60
    sleep_seconds = max(60, int(interval_minutes * 60))
    logger.info(f"[local_dev] Starting Responses GDrive sync worker (interval: {interval_minutes} minutes)...")
    while True:
        try:
            logger.info("[local_dev] Running scheduled Responses GDrive sync...")
            result = await run_responses_gdrive_sync()
            logger.info(f"[local_dev] Responses GDrive sync finished: {result}")
            try:
                logger.info("[local_dev] Running scheduled OCR sweep...")
                await MainWorker.run_ocr_task()
                logger.info("[local_dev] OCR sweep finished")
            except Exception as ocr_e:
                logger.error(f"[local_dev] OCR sweep error: {ocr_e}")
            logger.info("[local_dev] Running scheduled Vector Store ingestion (pending files)...")
            vs_result = await upload_missing_files_to_vector_store()
            logger.info(f"[local_dev] Vector Store ingestion finished: {vs_result}")
        except Exception as e:
            logger.error(f"[local_dev] Loop error: {e}", exc_info=True)
        logger.info(f"[local_dev] Sleeping for {interval_minutes} minutes...")
        try:
            await asyncio.sleep(sleep_seconds)
        except asyncio.CancelledError:
            logger.info("[local_dev] Responses GDrive sync worker cancelled. Exiting.")
            break

if __name__ == "__main__":
    asyncio.run(main())
