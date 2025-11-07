import asyncio
import logging
from app.workers.main_worker import MainWorker

# Local dev worker launcher (moved from repo root).
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

if __name__ == "__main__":
    logging.info("[local_dev] Starting the main background worker loop (hourly)...")
    try:
        asyncio.run(MainWorker.run_main_loop(interval_seconds=3600))
    except KeyboardInterrupt:
        logging.info("[local_dev] Worker stopped manually.")
    except Exception as e:
        logging.critical(f"[local_dev] Worker loop crashed: {e}", exc_info=True)
