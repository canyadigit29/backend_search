import asyncio
import logging
from app.api.gdrive_ops.sync import run_google_drive_sync
from app.core.config import settings

# Legacy (non-Responses) Drive sync loop; relocated from repo root.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    interval_minutes = settings.GDRIVE_SYNC_INTERVAL_MINUTES
    sleep_seconds = max(60, int(interval_minutes * 60))
    logger.info(f"[local_dev] Starting legacy Google Drive sync worker (interval: {interval_minutes} minutes)...")
    while True:
        try:
            logger.info("[local_dev] Running scheduled Google Drive sync...")
            result = await run_google_drive_sync()
            logger.info(f"[local_dev] Google Drive sync finished: {result}")
        except Exception as e:
            logger.error(f"[local_dev] Error in Drive sync loop: {e}")

        logger.info(f"[local_dev] Sleeping for {interval_minutes} minutes...")
        try:
            await asyncio.sleep(sleep_seconds)
        except asyncio.CancelledError:
            logger.info("[local_dev] Drive sync worker cancelled. Exiting.")
            break

if __name__ == "__main__":
    asyncio.run(main())
