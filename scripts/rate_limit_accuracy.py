#!/usr/bin/env python3
"""
Rate limit accuracy check.

Flushes Redis, fires 500 concurrent requests (50 workers) at the gateway,
and measures how close the number of allowed (200) requests is to the
configured limit of 100 requests/window.
"""
import subprocess
from concurrent.futures import ThreadPoolExecutor

import requests

URL = "http://localhost:8080/api/user/"
TOTAL_REQUESTS = 500
WORKERS = 50
EXPECTED_LIMIT = 100


def flush_redis():
    subprocess.run(
        ["docker", "exec", "gatekeeper-redis-1", "redis-cli", "flushall"],
        check=True,
        capture_output=True,
    )


def send_request(_):
    try:
        resp = requests.get(URL, timeout=5)
        return resp.status_code
    except requests.RequestException:
        return None


def main():
    flush_redis()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        statuses = list(pool.map(send_request, range(TOTAL_REQUESTS)))

    allowed = statuses.count(200)
    limited = statuses.count(429)
    other = len(statuses) - allowed - limited

    error_margin = abs(allowed - EXPECTED_LIMIT) / EXPECTED_LIMIT * 100

    print(f"Total requests sent : {TOTAL_REQUESTS}")
    print(f"200 OK              : {allowed}")
    print(f"429 Too Many Reqs   : {limited}")
    print(f"Other/errors        : {other}")
    print(f"Expected limit      : {EXPECTED_LIMIT}")
    print(f"Error margin        : {error_margin:.2f}%")


if __name__ == "__main__":
    main()
