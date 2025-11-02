import asyncio
import logging
from app.api.Responses.gdrive_sync import run_responses_gdrive_sync
from app.api.Responses.vs_ingest_worker import upload_missing_files_to_vector_store
from app.core.config import settings
from app.workers.main_worker import MainWorker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    interval_minutes = settings.GDRIVE_SYNC_INTERVAL_MINUTES or 60
    sleep_seconds = max(60, int(interval_minutes * 60))
    logger.info(
        f"Starting Responses GDrive sync worker (interval: {interval_minutes} minutes)..."
    )
    while True:
        try:
            logger.info("Running scheduled Responses GDrive sync...")
            result = await run_responses_gdrive_sync()
            logger.info(f"Responses GDrive sync finished: {result}")

            # Run OCR sweep on the same cadence so eligible PDFs gain a text layer
            # before attempting Vector Store ingestion.
            try:
                logger.info("Running scheduled OCR sweep (OCRmyPDF where needed)...")
                await MainWorker.run_ocr_task()
                logger.info("OCR sweep finished")
            except Exception as ocr_e:
                logger.error(f"OCR sweep encountered an error: {ocr_e}")

            logger.info("Running scheduled Vector Store ingestion (pending files)...")
            vs_result = await upload_missing_files_to_vector_store()
            logger.info(f"Vector Store ingestion finished: {vs_result}")
        except Exception as e:
            logger.error(
                f"An error occurred in the Responses GDrive sync worker loop: {e}",
                exc_info=True,
            )

        logger.info(
            f"Responses GDrive sync worker sleeping for {interval_minutes} minutes..."
        )
        try:
            await asyncio.sleep(sleep_seconds)
        except asyncio.CancelledError:
            logger.info("Responses GDrive sync worker received cancellation. Exiting.")
            break


if __name__ == "__main__":
    asyncio.run(main())
