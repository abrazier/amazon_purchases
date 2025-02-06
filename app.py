from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
import re
import os
import time
from celery.result import AsyncResult
from celery_worker import scrape_asins_task, celery
from asin_scraper import create_database, get_existing_category
import sqlite3
import logging

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = "uploads"
app.config["SECRET_KEY"] = "secret!"
socketio = SocketIO(
    app,
    message_queue="redis://redis:6379/0",
    ping_timeout=60,
    ping_interval=25,
    cors_allowed_origins="*",
)

if not os.path.exists(app.config["UPLOAD_FOLDER"]):
    os.makedirs(app.config["UPLOAD_FOLDER"])

create_database()  # Ensure the database is created at startup

# Global variable to store the DataFrame
global_df = None
CSV_PERSIST_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "uploads", "global_df.csv"
)


def get_db_path():
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data", "asin_categories.db"
    )


def extract_unique_addresses(addresses):
    unique_addresses = set()
    for address in addresses:
        if address != "Not Available":
            address = address.lower()  # Convert to lowercase
            match = re.search(r"\d.*", address)
            if match:
                unique_address = match.group(0)
                unique_addresses.add(unique_address)
    return sorted(list(unique_addresses))  # Sort the addresses


def ensure_asin_categories_table():
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS asin_categories (asin TEXT PRIMARY KEY, category TEXT)"
    )
    conn.commit()
    conn.close()


def get_categories_for_asins(asins):
    ensure_asin_categories_table()
    db_path = get_db_path()
    logging.info(f"Using DB path: {db_path}")
    conn = sqlite3.connect(db_path)
    # Convert to uppercase, strip whitespace, and deduplicate while filtering out empty values
    uppercase_asins = list(
        {str(x).upper().strip() for x in asins if x and str(x).upper().strip()}
    )
    logging.info(f"Final ASINs for query ({len(uppercase_asins)}): {uppercase_asins}")
    if not uppercase_asins:
        logging.info("No valid ASINs provided for querying")
        conn.close()
        return pd.DataFrame(columns=["asin", "category"])

    result_df = pd.DataFrame(columns=["asin", "category"])
    # Batch size chosen to avoid SQLite parameter limit (e.g., 500 per batch)
    batch_size = 500
    for i in range(0, len(uppercase_asins), batch_size):
        batch = uppercase_asins[i : i + batch_size]
        placeholders = ",".join("?" for _ in batch)
        query = (
            f"SELECT UPPER(TRIM(asin)) as asin, category FROM asin_categories "
            f"WHERE UPPER(TRIM(asin)) IN ({placeholders})"
        )
        logging.info(f"Executing query batch: {query} with params: {batch}")
        try:
            batch_df = pd.read_sql_query(query, conn, params=batch)
            logging.info(f"Got rows from DB batch: {batch_df.to_dict('records')}")
            result_df = pd.concat([result_df, batch_df], ignore_index=True)
        except Exception as e:
            logging.error(f"Error querying categories for batch {batch}: {e}")
    conn.close()
    # Remove duplicate ASIN entries if any
    return result_df.drop_duplicates(subset=["asin"])


def load_global_df():
    global global_df
    try:
        if os.path.exists(CSV_PERSIST_PATH):
            global_df = pd.read_csv(CSV_PERSIST_PATH)
            if "category" not in global_df.columns:
                global_df["category"] = None
            # Ensure the ASIN column is uppercase and stripped for proper matching
            if "ASIN" in global_df.columns:
                global_df["ASIN"] = (
                    global_df["ASIN"].astype(str).str.upper().str.strip()
                )
            asins = global_df["ASIN"].tolist() if "ASIN" in global_df.columns else []
            if asins:
                df_db = get_categories_for_asins(asins)
                # Strip whitespace from the DB asin values as well
                df_db["asin"] = df_db["asin"].astype(str).str.strip()
                mapping = dict(zip(df_db["asin"], df_db["category"]))
                global_df["category"] = (
                    global_df["ASIN"].map(mapping).fillna(global_df["category"])
                )
            logging.info(f"global_df loaded with columns: {global_df.columns.tolist()}")
    except Exception as e:
        logging.error(f"Error loading global_df from disk: {e}")


@app.route("/test_get_categories", methods=["GET"])
def test_get_categories():
    test_asins = ["B0DFLXT766"]  # Use a known ASIN from your DB
    df_test = get_categories_for_asins(test_asins)
    logging.info(f"Test get_categories_for_asins result: {df_test.to_dict('records')}")
    return jsonify(df_test.to_dict("records"))


@app.route("/", methods=["GET", "POST"])
def index():
    global global_df
    billing_addresses = []
    categories = []
    graph_monthly = ""
    graph_yearly = ""
    dark_mode = request.form.get("dark_mode", "light")

    try:
        if request.method == "POST":
            logging.info("POST request received")
            file = request.files.get("file")
            if file:
                try:
                    logging.info("File uploaded")
                    # Generate a unique filename using the original filename and a timestamp
                    timestamp = int(time.time())
                    filename = f"{timestamp}_{file.filename}"
                    file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                    file.save(file_path)
                    df = pd.read_csv(file_path)

                    global_df = df.copy()  # Store the DataFrame in the global variable
                    if "category" not in global_df.columns:
                        global_df["category"] = None
                    logging.info(
                        f"global_df initialized with columns: {global_df.columns.tolist()}"
                    )
                except Exception as e:
                    logging.error(f"Error processing CSV file: {e}")
            else:
                file_path = request.form.get("file_path")
                if file_path:
                    logging.info("Using existing file path")
                    df = pd.read_csv(file_path)
                    global_df = df.copy()  # Store the DataFrame in the global variable
                    logging.info(f"global_df columns: {global_df.columns.tolist()}")
                    if "category" not in global_df.columns:
                        global_df["category"] = None
                else:
                    logging.info("No file path provided")
                    return redirect(url_for("index"))

            logging.info("Processing DataFrame")
            df["Order Date"] = pd.to_datetime(df["Order Date"], errors="coerce")
            df["month"] = df["Order Date"].dt.to_period("M")
            df["year"] = df["Order Date"].dt.year

            billing_addresses = extract_unique_addresses(df["Billing Address"].unique())
            logging.info(f"Billing addresses: {billing_addresses}")

            selected_billing = request.form.getlist("billing_address")
            selected_categories = request.form.getlist("category")
            logging.info(f"Selected billing addresses: {selected_billing}")
            logging.info(f"Selected categories: {selected_categories}")
            filtered_df = df
            if selected_billing:
                filtered_df = filtered_df[
                    filtered_df["Billing Address"].apply(
                        lambda x: any(addr in x.lower() for addr in selected_billing)
                    )
                ]

            if "category" in df.columns:
                if selected_categories:
                    filtered_df = filtered_df[
                        filtered_df["category"].apply(
                            lambda x: any(cat in x for cat in selected_categories)
                        )
                    ]

            monthly_totals = (
                filtered_df.groupby("month")["Total Owed"].sum().reset_index()
            )
            monthly_totals["month"] = monthly_totals["month"].dt.strftime("%b-%Y")

            yearly_totals = (
                filtered_df.groupby("year")["Total Owed"].sum().reset_index()
            )

            # Calculate 3-month rolling average
            monthly_totals["3_month_avg"] = (
                monthly_totals["Total Owed"].rolling(window=3).mean()
            )

            # Calculate 3-year rolling average
            yearly_totals["3_year_avg"] = (
                yearly_totals["Total Owed"].rolling(window=3).mean()
            )

            fig_monthly = px.bar(
                monthly_totals,
                x="month",
                y="Total Owed",
                labels={"Total Owed": "Total Spent ($)"},
            )
            fig_yearly = px.bar(
                yearly_totals,
                x="year",
                y="Total Owed",
                labels={"Total Owed": "Total Spent ($)"},
            )

            fig_monthly.update_layout(yaxis_tickprefix="$", yaxis_tickformat=",")
            fig_yearly.update_layout(yaxis_tickprefix="$", yaxis_tickformat=",")

            fig_monthly.update_traces(texttemplate="%{y:$,.2f}", textposition="outside")
            fig_yearly.update_traces(texttemplate="%{y:$,.2f}", textposition="outside")

            # Add trend lines
            fig_monthly.add_trace(
                go.Scatter(
                    x=monthly_totals["month"],
                    y=monthly_totals["3_month_avg"],
                    mode="lines",
                    name="3-Month Average",
                    line=dict(color="firebrick", width=2),
                )
            )
            fig_yearly.add_trace(
                go.Scatter(
                    x=yearly_totals["year"],
                    y=yearly_totals["3_year_avg"],
                    mode="lines",
                    name="3-Year Average",
                    line=dict(color="firebrick", width=2),
                )
            )

            graph_monthly = pio.to_html(fig_monthly, full_html=False)
            graph_yearly = pio.to_html(fig_yearly, full_html=False)

            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                logging.info("AJAX request detected")
                # Trigger the background task to scrape ASIN categories
                asins = df["ASIN"].unique().tolist()
                task = scrape_asins_task.delay(asins)
                logging.info(f"Task info: {task.info}")
                return jsonify(
                    {
                        "graph_monthly": graph_monthly,
                        "graph_yearly": graph_yearly,
                        "task_id": task.id,
                        "billing_addresses": billing_addresses,
                    }
                )

            return render_template(
                "index.html",
                graph_monthly=graph_monthly,
                graph_yearly=graph_yearly,
                billing_addresses=billing_addresses,
                categories=categories,
                file_path=file_path,
                dark_mode=dark_mode,
            )

    except Exception as e:
        logging.error(f"Error: {e}")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"error": str(e)}), 500
        else:
            return render_template("error.html", error=str(e)), 500

    return render_template(
        "index.html",
        graph_monthly=graph_monthly,
        graph_yearly=graph_yearly,
        billing_addresses=billing_addresses,
        categories=categories,
        dark_mode=dark_mode,
    )


@socketio.on("track_task")
def handle_track_task(data):
    global global_df
    task_id = data["task_id"]
    task = AsyncResult(task_id, app=celery)

    while task.state not in ["SUCCESS", "FAILURE"]:
        socketio.sleep(1)
        task = AsyncResult(task_id, app=celery)
        if task.state == "PROGRESS":
            logging.info(f"Emitting progress: {task.info.get('percent', 0)}%")
            socketio.emit(
                "task_progress",
                {
                    "percent": task.info.get("percent", 0),
                    "status": task.info.get("status", ""),
                },
            )

        if task.state == "SUCCESS":
            logging.info("Task is SUCCESS; emitting final 100% progress")
            socketio.emit(
                "task_progress",
                {
                    "percent": 100,
                    "status": "Scraping completed!",
                },
            )
            # Reload the global_df if necessary
            load_global_df()
            if global_df is not None:
                logging.info("Updating global_df with categories")
                asins = (
                    global_df["ASIN"].tolist() if "ASIN" in global_df.columns else []
                )
                logging.info(f"ASINs from CSV after cleanup: {asins}")
                df_db = get_categories_for_asins(asins)
                logging.info(f"DB query returned rows: {df_db.to_dict('records')}")
                # Ensure both DB and CSV ASIN columns are clean
                df_db["asin"] = df_db["asin"].astype(str).str.strip()
                global_df["ASIN"] = global_df["ASIN"].astype(str).str.strip()
                mapping = dict(zip(df_db["asin"], df_db["category"]))
                logging.info(f"Mapping from DB: {mapping}")
                global_df["category"] = (
                    global_df["ASIN"].map(mapping).fillna(global_df["category"])
                )
                # Save the updated DataFrame back to disk
                # Write to persistent CSV file to share data between processes
                if not os.path.exists(
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
                ):
                    os.makedirs(
                        os.path.join(
                            os.path.dirname(os.path.abspath(__file__)), "uploads"
                        )
                    )
                global_df.to_csv(CSV_PERSIST_PATH, index=False)
                unique_categories = global_df["category"].dropna().unique().tolist()
                logging.info(f"Unique categories: {unique_categories}")
                socketio.emit(
                    "task_progress",
                    {"percent": 100, "status": "Scraping completed!"},
                )
            else:
                logging.error("global_df is still None after reloading")


@app.route("/fetch_categories", methods=["GET"])
def fetch_categories():
    global global_df
    logging.info("Received request for /fetch_categories")
    load_global_df()
    if global_df is not None:
        unique_categories = global_df["category"].dropna().unique().tolist()
        logging.info(f"Fetched categories for /fetch_categories: {unique_categories}")
        return jsonify({"categories": unique_categories})
    else:
        logging.error("global_df is None or missing in /fetch_categories")
        return jsonify({"categories": []})


if __name__ == "__main__":
    socketio.run(app, debug=True, host="0.0.0.0")
