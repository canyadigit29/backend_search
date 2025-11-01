import asyncio
import logging
from app.api.gdrive_ops.sync import run_google_drive_sync
from app.core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    interval_minutes = settings.GDRIVE_SYNC_INTERVAL_MINUTES
    sleep_seconds = max(60, int(interval_minutes * 60))
    logger.info(f"Starting the Google Drive sync worker (interval: {interval_minutes} minutes)...")
    while True:
        try:
            logger.info("Running scheduled Google Drive sync...")
            result = await run_google_drive_sync()
            logger.info(f"Google Drive sync finished: {result}")
        except Exception as e:
            logger.error(f"An error occurred in the Google Drive sync worker loop: {e}")
        
        # Wait for configured interval before syncing again
        logger.info(f"Google Drive sync worker sleeping for {interval_minutes} minutes...")
        try:
            await asyncio.sleep(sleep_seconds)
        except asyncio.CancelledError:
            logger.info("Google Drive sync worker received cancellation. Exiting.")
            break

if __name__ == "__main__":
    asyncio.run(main())
