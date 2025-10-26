import asyncio
import logging
from app.api.gdrive_ops.sync import run_google_drive_sync

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    logger.info("Starting the Google Drive sync worker...")
    while True:
        try:
            logger.info("Running scheduled Google Drive sync...")
            result = await run_google_drive_sync()
            logger.info(f"Google Drive sync finished: {result}")
        except Exception as e:
            logger.error(f"An error occurred in the Google Drive sync worker loop: {e}")
        
        # Wait for 60 seconds before syncing again
        logger.info("Google Drive sync worker sleeping for 60 seconds...")
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
