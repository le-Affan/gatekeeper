FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

# Liveness probe (is the process serving). Dependency readiness is exposed
# separately at /gatekeeper/ready for orchestrators; keeping the container
# healthcheck shallow avoids restart loops on transient Redis blips.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/gatekeeper/health || exit 1

# Single worker is REQUIRED as-is: prometheus_client uses a per-process registry,
# so multiple workers would make /metrics return one random worker's counters per
# scrape. To scale workers, configure prometheus multiprocess mode first.
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
