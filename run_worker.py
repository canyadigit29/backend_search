import asyncio
import logging
from app.tasks.chunk_and_embed_logs import process_unindexed_files

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    logger.info("Starting the ingestion worker...")
    while True:
        try:
            await process_unindexed_files()
        except Exception as e:
            logger.error(f"An error occurred in the worker loop: {e}")
        
        # Wait for 60 seconds before checking for new files again
        logger.info("Worker sleeping for 60 seconds...")
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
