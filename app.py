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


@app.route("/", methods=["GET", "POST"])
def index():
    billing_addresses = []
    graph_monthly = ""
    graph_yearly = ""
    dark_mode = request.form.get("dark_mode", "light")

    if request.method == "POST":
        file = request.files.get("file")
        if file:
            # Generate a unique filename using the original filename and a timestamp
            timestamp = int(time.time())
            filename = f"{timestamp}_{file.filename}"
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(file_path)
            df = pd.read_csv(file_path)
        else:
            file_path = request.form.get("file_path")
            if file_path:
                df = pd.read_csv(file_path)
            else:
                return redirect(url_for("index"))

        df["Order Date"] = pd.to_datetime(df["Order Date"], errors="coerce")
        df["month"] = df["Order Date"].dt.to_period("M")
        df["year"] = df["Order Date"].dt.year

        billing_addresses = extract_unique_addresses(df["Billing Address"].unique())

        selected_billing = request.form.getlist("billing_address")

        filtered_df = df
        if selected_billing:
            filtered_df = filtered_df[
                filtered_df["Billing Address"].apply(
                    lambda x: any(addr in x.lower() for addr in selected_billing)
                )
            ]

        monthly_totals = filtered_df.groupby("month")["Total Owed"].sum().reset_index()
        monthly_totals["month"] = monthly_totals["month"].dt.strftime("%b-%Y")

        yearly_totals = filtered_df.groupby("year")["Total Owed"].sum().reset_index()

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

        # Trigger the background task to scrape ASIN categories
        asins = df["ASIN"].unique().tolist()
        task = scrape_asins_task.delay(asins)

        return render_template(
            "index.html",
            graph_monthly=graph_monthly,
            graph_yearly=graph_yearly,
            billing_addresses=billing_addresses,
            file_path=file_path,
            dark_mode=dark_mode,
            task_id=task.id,
        )

    return render_template(
        "index.html",
        graph_monthly=graph_monthly,
        graph_yearly=graph_yearly,
        billing_addresses=billing_addresses,
        dark_mode=dark_mode,
    )


@app.route("/filter_by_category", methods=["GET", "POST"])
def filter_by_category():
    categories = []
    graph_monthly = ""
    graph_yearly = ""
    dark_mode = request.form.get("dark_mode", "light")

    if request.method == "POST":
        file_path = request.form.get("file_path")
        if file_path:
            df = pd.read_csv(file_path)
        else:
            return redirect(url_for("index"))

        df["Order Date"] = pd.to_datetime(df["Order Date"], errors="coerce")
        df["month"] = df["Order Date"].dt.to_period("M")
        df["year"] = df["Order Date"].dt.year

        # Get unique categories from the database
        conn = sqlite3.connect("data/asin_categories.db")
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT category FROM asin_categories")
        categories = [row[0] for row in cursor.fetchall()]
        conn.close()

        selected_category = request.form.get("category")

        if selected_category and selected_category != "All":
            asins = df["ASIN"].unique().tolist()
            filtered_asins = []
            for asin in asins:
                category = get_existing_category(asin)
                if category == selected_category:
                    filtered_asins.append(asin)
            filtered_df = df[df["ASIN"].isin(filtered_asins)]
        else:
            filtered_df = df

        monthly_totals = filtered_df.groupby("month")["Total Owed"].sum().reset_index()
        monthly_totals["month"] = monthly_totals["month"].dt.strftime("%b-%Y")

        yearly_totals = filtered_df.groupby("year")["Total Owed"].sum().reset_index()

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

        return render_template(
            "filter_by_category.html",
            graph_monthly=graph_monthly,
            graph_yearly=graph_yearly,
            categories=categories,
            file_path=file_path,
            dark_mode=dark_mode,
        )

    return render_template(
        "filter_by_category.html",
        graph_monthly=graph_monthly,
        graph_yearly=graph_yearly,
        categories=categories,
        dark_mode=dark_mode,
    )


@socketio.on("track_task")
def handle_track_task(data):
    task_id = data["task_id"]
    task = AsyncResult(task_id, app=celery)

    while task.state not in ["SUCCESS", "FAILURE"]:
        socketio.sleep(1)
        task = AsyncResult(task_id, app=celery)
        if task.state == "PROGRESS":
            socketio.emit(
                "task_progress",
                {
                    "percent": task.info.get("percent", 0),
                    "status": task.info.get("status", ""),
                },
            )

    if task.state == "SUCCESS":
        socketio.emit("task_progress", {"percent": 100, "status": "Task completed!"})
    elif task.state == "FAILURE":
        socketio.emit("task_progress", {"percent": 0, "status": "Task failed!"})


if __name__ == "__main__":
    socketio.run(app, debug=True, host="0.0.0.0")
