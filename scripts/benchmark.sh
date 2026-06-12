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
