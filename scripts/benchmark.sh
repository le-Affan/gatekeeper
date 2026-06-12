#!/usr/bin/env bash
#
# Benchmark suite for GateKeeper using wrk.
# Runs 4 benchmarks:
#   1. Raw upstream baseline (no gateway)
#   2. GateKeeper, normal concurrency
#   3. GateKeeper, high concurrency
#   4. Rate limit enforcement check
#
set -euo pipefail

if ! command -v wrk >/dev/null 2>&1; then
    echo "ERROR: 'wrk' is not installed." >&2
    echo "Install it first, e.g.:" >&2
    echo "  Debian/Ubuntu: sudo apt-get install wrk" >&2
    echo "  macOS:         brew install wrk" >&2
    exit 1
fi

UPSTREAM_URL="http://localhost:8000"
GATEKEEPER_URL="http://localhost:8080/api/user/"

# Set once production config has been restored, so cleanup is a no-op
# if it runs again (e.g. trap firing after the explicit pre-Benchmark-4
# restore already happened).
PROD_RESTORED=false

# Wait until the gatekeeper container reports a "healthy" status.
wait_for_gatekeeper_healthy() {
    local cid status
    cid="$(docker compose ps -q gatekeeper)"
    echo "Waiting for gatekeeper to become healthy..."
    for _ in $(seq 1 60); do
        status="$(docker inspect --format='{{.State.Health.Status}}' "${cid}" 2>/dev/null || echo "unknown")"
        if [[ "${status}" == "healthy" ]]; then
            echo "gatekeeper is healthy."
            return 0
        fi
        sleep 1
    done
    echo "ERROR: gatekeeper did not become healthy in time (last status: ${status})." >&2
    return 1
}

# Cleanup/restoration handler. Runs on normal exit, errors, Ctrl+C, and
# termination signals so GateKeeper never gets stuck on the benchmark
# (high RATE_LIMIT_LIMIT) override. Idempotent: skips if production
# config was already restored, and tolerates GateKeeper already being
# stopped/removed.
restore_production_config() {
    if [[ "${PROD_RESTORED}" == "true" ]]; then
        return 0
    fi

    echo
    echo "============================================================"
    echo "Restoring production configuration (cleanup)"
    echo "Recreating GateKeeper with docker-compose.yml only"
    echo "(production rate-limit defaults: RATE_LIMIT_LIMIT=100,"
    echo " RATE_LIMIT_WINDOW_SECONDS=60)"
    echo "============================================================"

    docker compose -f docker-compose.yml up -d --force-recreate gatekeeper || true
    wait_for_gatekeeper_healthy || true

    PROD_RESTORED=true
    echo "Production configuration restored."
}

trap restore_production_config EXIT INT TERM HUP

echo "============================================================"
echo "Benchmark mode ENABLED"
echo "Starting GateKeeper with docker-compose.bench.yml override"
echo "(RATE_LIMIT_LIMIT raised so Benchmarks 2-3 measure throughput,"
echo " not rate-limit rejections)"
echo "============================================================"
docker compose -f docker-compose.yml -f docker-compose.bench.yml up -d --force-recreate gatekeeper
wait_for_gatekeeper_healthy

echo
echo "============================================================"
echo "Benchmark 1 — Raw upstream baseline"
echo "Target:   ${UPSTREAM_URL}"
echo "Duration: 30s"
echo "============================================================"
wrk -t4 -c50 -d30s "${UPSTREAM_URL}"

echo
echo "============================================================"
echo "Benchmark 2 — GateKeeper, normal concurrency"
echo "Target:   ${GATEKEEPER_URL}"
echo "Duration: 30s"
echo "============================================================"
wrk -t4 -c50 -d30s "${GATEKEEPER_URL}"

echo
echo "============================================================"
echo "Benchmark 3 — GateKeeper, high concurrency"
echo "Target:   ${GATEKEEPER_URL}"
echo "Duration: 30s"
echo "============================================================"
wrk -t8 -c200 -d30s "${GATEKEEPER_URL}"

echo
restore_production_config

echo
echo "Flushing Redis state before rate limit check..."
docker compose exec redis redis-cli FLUSHDB
sleep 2

echo
echo "============================================================"
echo "Benchmark 4 — Rate limit enforcement check"
echo "Target:   ${GATEKEEPER_URL}"
echo "Duration: 5s"
echo "============================================================"
wrk -t1 -c1 -d5s "${GATEKEEPER_URL}"

echo
echo "Rate limit keys in Redis after enforcement check:"
docker compose exec redis redis-cli keys "rate-limiter:*"
