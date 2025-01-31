from celery import Celery, current_task
from asin_scraper import create_database, get_categories_with_delay

celery = Celery("tasks", broker="redis://redis:6379/0", backend="redis://redis:6379/0")


@celery.task(bind=True)
def scrape_asins_task(self, asins):
    create_database()
    total_asins = len(asins)

    try:
        for idx, asin in enumerate(asins):
            # Simulate scraping or do the actual scraping
            get_categories_with_delay(asin, idx, total_asins)
            self.update_state(
                state="PROGRESS",
                meta={
                    "current": idx + 1,
                    "total": total_asins,
                    "status": f"Scraping {asin}",
                },
            )
        # Return completion info
        return {
            "current": total_asins,
            "total": total_asins,
            "status": "Task completed successfully!",
            "percent": 100,
        }

    except Exception as e:
        # Handle any errors
        self.update_state(
            state="FAILURE",
            meta={
                "current": idx,
                "total": total_asins,
                "status": str(e),
            },
        )
        raise e


if __name__ == "__main__":
    celery.start()
