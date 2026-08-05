FROM python:3.12-slim

LABEL maintainer="nexus-memory"
LABEL description="Nexus Knowledge Memory System"

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && \
    rm -rf /var/lib/apt/lists/*

# Copy and install Python deps
# plugins/memory/nexus/schema.sql 不在 src 包内（nexus_core_db 从
# <root>/plugins/... 路径加载），必须单独复制，否则容器内建库无 schema
COPY pyproject.toml .
COPY src/ src/
COPY plugins/ plugins/
RUN pip install --no-cache-dir -e ".[full]"

# Data directory
RUN mkdir -p /data

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV NEXUS_DB_PATH=/data/nexus.db

EXPOSE 8080
CMD ["python", "-m", "src.api_server", "--host", "0.0.0.0", "--port", "8080"]
