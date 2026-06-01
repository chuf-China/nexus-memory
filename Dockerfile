FROM python:3.12-slim

LABEL maintainer="nexus-memory"
LABEL description="Nexus Knowledge Memory System"

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && \
    rm -rf /var/lib/apt/lists/*

# Copy and install Python deps
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir -e ".[full]"

# Data directory
RUN mkdir -p /data

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV NEXUS_DB_PATH=/data/nexus.db

EXPOSE 8080
CMD ["python", "-m", "src.api_server", "--host", "0.0.0.0", "--port", "8080"]
