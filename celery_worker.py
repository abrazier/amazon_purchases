from celery import Celery
from asin_scraper import get_categories_with_delay
import logging

logging.basicConfig(level=logging.INFO)

celery = Celery("tasks", broker="redis://redis:6379/0", backend="redis://redis:6379/0")


@celery.task(bind=True)
def scrape_asins_task(self, asins):
    total = len(asins)
    for i, asin in enumerate(asins):
        current = i + 1
        get_categories_with_delay(asin, current, total)
        self.update_state(
            state="PROGRESS",
            meta={
                "percent": int((current / total) * 100),
                "status": f"Processing ASIN {current} of {total}",
            },
        )
    return {"status": "Task completed!", "percent": 100}


if __name__ == "__main__":
    celery.start()
