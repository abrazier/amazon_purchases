FROM python:3.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install gevent

# Create app user
RUN useradd -m appuser && \
    mkdir -p /app/uploads /app/data && \
    chown -R appuser:appuser /app

USER appuser

COPY . .

CMD ["gunicorn", "-w", "4", "-k", "gevent", "-b", "0.0.0.0:5000", "app:app"]