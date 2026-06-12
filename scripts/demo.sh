#!/usr/bin/env bash
#
# End-to-end GateKeeper demo: traffic generator + connectivity probe +
# fault injection, running together so Grafana shows the full
# 200 -> 502/504 -> 503 (circuit open) -> 200 sequence.
#
# Must be run as: sudo bash scripts/demo.sh
#
set -uo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: run as: sudo bash scripts/demo.sh" >&2
    exit 1
fi

# Run from the repo root regardless of invocation cwd.
cd "$(dirname "${BASH_SOURCE[0]}")/.."

TRAFFIC_PID=""
PROBE_PID=""

cleanup() {
    echo
    echo "Cleaning up background jobs..."
    [[ -n "${TRAFFIC_PID}" ]] && kill "${TRAFFIC_PID}" 2>/dev/null || true
    [[ -n "${PROBE_PID}" ]] && kill "${PROBE_PID}" 2>/dev/null || true
    wait "${TRAFFIC_PID}" "${PROBE_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting traffic generator..."
(
    for i in $(seq 1 600); do
        curl -m 2 -o /dev/null -s -w "$(date +%T)  http_code=%{http_code}\n" http://localhost:8080/api/user/ || true
        sleep 0.5
    done
) &
TRAFFIC_PID=$!

echo "Starting connectivity probe..."
GK_CID=$(docker compose ps -q gatekeeper)
(
    for i in $(seq 1 600); do
        docker exec "${GK_CID}" curl -m 1 -o /dev/null -s -w "$(date +%T)  probe http_code=%{http_code}\n" http://mock-upstream:8000/ || true
        sleep 0.5
    done
) &
PROBE_PID=$!

echo "Waiting 5s for traffic to establish..."
sleep 5

echo "Running fault injection..."
if ! bash scripts/fault_inject.sh; then
    echo "ERROR: fault injection failed - see output above." >&2
fi

echo "Waiting 90s for recovery..."
sleep 90

echo "Done."
