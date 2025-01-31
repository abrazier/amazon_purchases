import sqlite3
import logging
import time
import requests
from bs4 import BeautifulSoup
import os

logging.basicConfig(level=logging.INFO)

# Define paths relative to project root
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DATABASE_PATH = os.path.join(DATA_DIR, "asin_categories.db")

# Ensure data directory exists with correct permissions
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, mode=0o777)
    logging.info(f"Created data directory at {DATA_DIR}")


def create_database():
    try:
        if os.path.exists(DATABASE_PATH):
            logging.info("Database already exists.")
            # Ensure permissions are correct
            os.chmod(DATABASE_PATH, 0o666)
            return

        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS asin_categories (
                asin TEXT PRIMARY KEY,
                category TEXT
            )
        """)
        conn.commit()
        conn.close()
        # Set permissions for new database
        os.chmod(DATABASE_PATH, 0o666)
        logging.info("Database created with read/write permissions.")
    except Exception as e:
        logging.error(f"Error creating database: {e}")
        raise


def get_existing_category(asin):
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT category FROM asin_categories WHERE asin = ?", (asin,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    except Exception as e:
        logging.error(f"Error getting category for ASIN {asin}: {e}")
        return None


def insert_category(asin, category):
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO asin_categories (asin, category) VALUES (?, ?)",
            (asin, category),
        )
        conn.commit()
        logging.info(f"Category '{category}' saved to database for ASIN {asin}")

        # Verify the insert
        cursor.execute("SELECT category FROM asin_categories WHERE asin = ?", (asin,))
        result = cursor.fetchone()
        if result:
            logging.info(f"Verified database entry for ASIN {asin}: {result[0]}")
    except Exception as e:
        logging.error(f"Database error for ASIN {asin}: {e}")
    finally:
        if conn:
            conn.close()


def get_product_categories(asin):
    url = f"https://www.amazon.com/dp/{asin}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, "html.parser")

        breadcrumb = soup.find(id="wayfinding-breadcrumbs_feature_div")
        if breadcrumb:
            categories = [a.text.strip() for a in breadcrumb.find_all("a")]
            if categories:
                logging.info(f"Successfully scraped categories for ASIN {asin}")
                return categories[0]

        logging.warning(f"Categories not found for ASIN {asin}")
        return "No Category"
    except Exception as e:
        logging.error(f"Error scraping ASIN {asin}: {e}")
        return "No Category"


def get_categories_with_delay(asin, index, total):
    logging.info(
        f"Starting to scrape categories for ASIN {asin} (index {index + 1} of {total})"
    )

    existing_category = get_existing_category(asin)
    if existing_category:
        logging.info(
            f"ASIN {asin} already exists in database with category: {existing_category}"
        )
        return existing_category

    category = get_product_categories(asin)
    insert_category(asin, category)

    time.sleep(2)
    progress = (index + 1) / total * 100
    logging.info(f"Progress: {progress:.2f}% complete")
    return category
