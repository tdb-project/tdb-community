FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifests
COPY requirements.txt ./

# Install all runtime dependencies into the system Python
# (project source is added via PYTHONPATH below — no need to install the package itself)
RUN uv pip install --system --no-cache -r requirements.txt

# Copy source
COPY src/ ./src/

# Runtime directories
RUN mkdir -p /app/data /app/logs

# Non-root user
RUN adduser --disabled-password --gecos "" tdb && chown -R tdb:tdb /app
USER tdb

ENV PYTHONPATH=/app/src

# Confine registered CSV paths to the read-only data mount by default, so the
# bundled deployment is secure out of the box (see TDB_ALLOWED_DATA_DIR).
ENV TDB_ALLOWED_DATA_DIR=/data

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "tdb.main:app", "--host", "0.0.0.0", "--port", "8000"]
