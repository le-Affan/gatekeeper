#!/usr/bin/env bash
#
# Fault injection for GateKeeper's circuit breaker.
#
# Simulates a complete network partition between GateKeeper and
# mock-upstream for 30 seconds using host-side tc netem (100% packet
# loss on mock-upstream's host-side veth), then removes the fault.
#
set -euo pipefail

# Run from the repo root regardless of invocation cwd, so `docker compose`
# resolves the correct project (needed for resolve_veth below).
cd "$(dirname "${BASH_SOURCE[0]}")/.."

FAULT_DURATION=30
FAULT_ACTIVE=false

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: this script must be run as root (tc on host network interfaces requires it)." >&2
    echo "Re-run with: sudo $0" >&2
    exit 1
fi

if ! command -v tc >/dev/null 2>&1; then
    echo "ERROR: 'tc' (iproute2) is not installed on this host." >&2
    exit 1
fi

# Resolve mock-upstream's host-side veth interface. Re-resolved every time
# it's needed (apply, cleanup) since the veth name changes if the container
# is recreated, and is never hardcoded.
resolve_veth() {
    local mu_cid iflink veth

    mu_cid="$(docker compose ps -q mock-upstream 2>/dev/null)"
    if [[ -z "${mu_cid}" ]]; then
        return 1
    fi

    iflink="$(docker exec "${mu_cid}" cat /sys/class/net/eth0/iflink 2>/dev/null)" || return 1
    if [[ -z "${iflink}" ]]; then
        return 1
    fi

    veth="$(ip -o link 2>/dev/null | awk -F': ' -v idx="${iflink}" '$1==idx {print $2}' | cut -d@ -f1)"
    if [[ -z "${veth}" ]]; then
        return 1
    fi

    echo "${veth}"
}

# Cleanup/restoration handler. Runs on normal exit, errors, Ctrl+C, and
# termination signals so the host is never left with a partitioned
# mock-upstream. Idempotent and tolerates:
#   - qdisc already removed
#   - container recreated (veth resolved fresh, or no longer resolvable)
#   - interface no longer existing
cleanup() {
    if [[ "${FAULT_ACTIVE}" != "true" ]]; then
        return 0
    fi

    echo
    echo "============================================================"
    echo "Restoring network (cleanup)"
    echo "============================================================"

    local veth
    if veth="$(resolve_veth)"; then
        tc qdisc del dev "${veth}" root 2>/dev/null || true
        echo "Removed netem qdisc from ${veth} (or it was already absent)."
    else
        echo "mock-upstream veth could not be resolved (container recreated"
        echo "or removed) - nothing to clean up on this interface."
    fi

    FAULT_ACTIVE=false
}

trap cleanup EXIT INT TERM HUP

VETH="$(resolve_veth)" || {
    echo "ERROR: could not resolve mock-upstream's host-side veth interface." >&2
    echo "Is the stack up? (docker compose up -d)" >&2
    exit 1
}

echo "============================================================"
echo "Fault injection: complete network partition to mock-upstream"
echo "Target interface (host-side veth): ${VETH}"
echo "Duration: ${FAULT_DURATION}s"
echo "============================================================"
echo
echo "BEFORE FAULT - what to watch in Grafana (Gatekeeper Stack - Overview):"
echo "  - gatekeeper_circuit_open          -> should be 0 (closed)"
echo "  - Request Rate by Route / Status   -> mostly 2xx"
echo "  - p99 request/upstream duration    -> normal baseline latency"
echo
echo "Generate traffic against http://localhost:8080/api/user/ now"
echo "(e.g. a wrk or curl loop) so the circuit breaker has requests to act on."
sleep 2

# 'replace' instead of 'add' so a stale qdisc left by a prior interrupted
# run (which 'add' would reject with "File exists") is overwritten cleanly.
tc qdisc replace dev "${VETH}" root netem loss 100%
FAULT_ACTIVE=true

echo
echo "============================================================"
echo "FAULT ACTIVE (${FAULT_DURATION}s)"
echo "============================================================"
echo "Expected behavior:"
echo "  - Requests to /api/user/* start failing with 502/504"
echo "    (httpx ConnectError/TimeoutException -> ProxyMiddleware abort)"
echo "  - After CB_FAILURE_THRESHOLD failures within CB_WINDOW_SECONDS,"
echo "    the circuit breaker trips CLOSED -> OPEN"
echo "  - gatekeeper_circuit_open should flip from 0 to 1"
echo "  - Once OPEN, requests fail fast with 503 'circuit breaker open'"
echo "    (no upstream call attempted - upstream_duration stops updating)"

sleep "${FAULT_DURATION}"

echo
echo "Removing network partition..."
if VETH_NOW="$(resolve_veth)"; then
    tc qdisc del dev "${VETH_NOW}" root 2>/dev/null || true
else
    echo "mock-upstream veth could not be resolved - it may have been recreated."
fi
FAULT_ACTIVE=false

echo
echo "============================================================"
echo "RECOVERY"
echo "============================================================"
echo "Expected behavior:"
echo "  - Circuit stays OPEN until CB_RECOVERY_TIMEOUT seconds after it"
echo "    tripped (independent of this script's 30s fault window)"
echo "  - Once that timeout elapses, OPEN -> RECOVERY: the next request"
echo "    becomes a single probe sent to mock-upstream"
echo "  - If the probe succeeds, RECOVERY -> CLOSED"
echo "    (after CB_SUCCESS_THRESHOLD consecutive successful probes)"
echo "  - gatekeeper_circuit_open should return to 0"
echo "  - Request Rate by Route / Status should return to mostly 2xx"
echo
echo "Done."
