# MindCI — Cloud-engineer knowledge pipeline (Streamlit + Claude)
#
# Build:   docker build -t mindci:latest .
# Run:     docker run --rm -p 8501:8501 \
#            -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
#            -v $PWD/data:/app/data \
#            -v $PWD/raw:/app/raw \
#            -v $PWD/output:/app/output \
#            -v $PWD/jd_reports:/app/jd_reports \
#            mindci:latest

FROM python:3.11-slim AS base

# System deps kept minimal; curl is included for the healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    # Streamlit defaults — overridable at runtime
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    # MindCI paths inside container — match docker-compose volume mounts
    MINDCI_DATA_DIR=/app/data \
    MINDCI_OUTPUT_DIR=/app/output \
    MINDCI_RAW_DIR=/app/raw \
    MINDCI_JD_REPORTS_DIR=/app/jd_reports \
    MINDCI_LOG_LEVEL=INFO

WORKDIR /app

# Install Python deps first to maximize layer caching.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy application source.
COPY . .

# Pre-create the mountable directories (volumes can shadow them at runtime).
RUN mkdir -p /app/data /app/output /app/raw /app/jd_reports

# Expose the dashboard UI.
EXPOSE 8501

# Streamlit ships its own /_stcore/health endpoint. Use it for healthchecks.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl --fail http://localhost:8501/_stcore/health || exit 1

# Default to the new dashboard UI. Override CMD to run the legacy app.py.
CMD ["streamlit", "run", "app_dashboard.py", \
     "--server.port=8501", "--server.address=0.0.0.0"]
