from celery import Celery
from asin_scraper import get_categories_with_delay
import logging
import time

logging.basicConfig(level=logging.INFO)

celery = Celery("tasks", broker="redis://redis:6379/0", backend="redis://redis:6379/0")


@celery.task(bind=True)
def scrape_asins_task(self, asins):
    total = len(asins)
    logging.info(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting scraping for {total} ASINs."
    )
    for i, asin in enumerate(asins):
        current = i + 1
        try:
            logging.info(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting processing for ASIN {asin} ({current} of {total})."
            )
            get_categories_with_delay(asin, current, total)
            logging.info(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Completed processing for ASIN {asin} ({current} of {total})."
            )
        except Exception as e:
            logging.error(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error processing ASIN {asin}: {e}"
            )
        progress = min(int((current / total) * 100), 100)
        logging.info(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Updating progress state for ASIN {asin}: {progress}%"
        )
        self.update_state(
            state="PROGRESS",
            meta={
                "percent": progress,
                "status": f"Processing ASIN {current} of {total}",
            },
        )
    logging.info(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Finalizing progress state to 100%."
    )
    self.update_state(
        state="SUCCESS",
        meta={
            "percent": 100,
            "status": "Scraping completed!",
        },
    )
    logging.info(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Task completed. Returning final state."
    )
    return {"status": "Task completed!", "percent": 100}


if __name__ == "__main__":
    celery.start()
