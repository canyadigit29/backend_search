import asyncio
import logging
from app.workers.main_worker import MainWorker

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

if __name__ == "__main__":
    logging.info("Starting the main background worker...")
    try:
        asyncio.run(MainWorker.run_main_loop())
    except KeyboardInterrupt:
        logging.info("Worker stopped manually.")
    except Exception as e:
        logging.critical(f"The main worker loop crashed: {e}", exc_info=True)
