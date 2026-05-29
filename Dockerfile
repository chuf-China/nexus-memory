FROM python:3.12-slim

LABEL maintainer="nexus-memory"
LABEL description="Nexus Knowledge Memory System"

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . nexus/

# Data directory
RUN mkdir -p /root/.hermes/data/nexus

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python3 -c "from nexus.health import health_check; import sys; r=health_check(); sys.exit(0 if r.get('status')=='healthy' else 1)"

# MCP server (stdio by default, SSE on --port)
EXPOSE 8080
CMD ["python3", "-m", "nexus.mcp_server"]
