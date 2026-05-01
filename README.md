# Gatekeeper

A self-hostable reverse proxy and API gateway built from scratch in Python writing each line of code by hand. Sits in front of any backend service and adds traffic management — rate limiting, circuit breaking, request routing, analytics, and middleware composition without modifying the upstream service at all.

> Built by Affan Shaikh as a systems project. Every line written from first principles.

---

## What it does

- **Reverse proxies** HTTP traffic to configured upstream services
- **Rate limits** requests per client using token bucket or sliding window algorithms
- **Circuit breaks** failing upstreams to prevent cascade failures
- **Composes middleware** in an ordered, pluggable chain
- **Logs** every request as structured JSON
- **Exposes analytics** via a REST API and live WebSocket dashboard
- **Validates API keys** with Redis-backed auth middleware

---

## Architecture

```
Client Request
      │
      ▼
┌─────────────────────────────────────┐
│           Gatekeeper                │
│                                     │
│  ┌─────────────────────────────┐    │
│  │      Middleware Chain       │    │
│  │                             │    │
│  │  1. Logger                  │    │
│  │  2. Auth                    │    │
│  │  3. Rate Limiter            │    │
│  │  4. Circuit Breaker         │    │
│  │  5. Proxy (terminal)        │    │
│  └─────────────────────────────┘    │
│                                     │
└─────────────────────────────────────┘
      │
      ▼
 Upstream Service
```

Each request flows forward through the chain. Any middleware can abort early and return a response immediately. After the upstream responds, `on_response` hooks fire in reverse order for cleanup, logging, and state updates.

---

## Project Structure

```
GATEKEEPER/
├── src/
│   ├── models.py               # Core data models (ProxyRequest, ProxyResponse, etc.)
│   ├── proxy.py                # Terminal proxy middleware — actual HTTP forwarding
│   ├── middleware/
│   │   ├── __init__.py         # MiddlewareChain runner
│   │   ├── base.py             # Abstract Middleware interface
│   │   ├── rate_limiter.py     # Token bucket + sliding window
│   │   ├── circuit_breaker.py  # Closed → Open → Half-Open state machine
│   │   ├── logger.py           # Structured JSON access logging
│   │   ├── auth.py             # API key validation
│   │   └── transformer.py      # Request/response header manipulation
│   ├── storage/
│   │   ├── base.py             # Abstract storage interface
│   │   ├── memory.py           # In-memory store (testing)
│   │   └── redis_store.py      # Redis-backed store (production)
│   ├── config/
│   │   ├── settings.py         # Pydantic settings (env vars)
│   │   └── routes.py           # Route config loader
│   ├── analytics/
│   │   ├── collector.py        # Rolling metrics aggregation
│   │   └── dashboard.py        # Live WebSocket dashboard
│   └── api/
│       └── main.py             # FastAPI entry point
├── tests/
│   ├── conftest.py
│   ├── test_rate_limiter.py
│   ├── test_circuit_breaker.py
│   ├── test_middleware_chain.py
│   ├── test_integration.py
│   └── test_benchmarks.py
├── config/
│   └── routes.yaml             # Route definitions
├── scripts/
│   ├── benchmark.sh            # wrk load test
│   └── fault_inject.sh         # tc netem network simulation
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Core Concepts

### Middleware Chain

Every feature in Gatekeeper is a middleware. The chain runs in order — each middleware either continues to the next or aborts and returns early. After the upstream responds, `on_response` fires in reverse order.

```
Forward:  Logger → Auth → RateLimiter → CircuitBreaker → Proxy
Reverse:  Proxy → CircuitBreaker → RateLimiter → Auth → Logger
```

This mirrors how Express.js, Django, and FastAPI middleware all work internally.

---

### Rate Limiting

Two algorithms, both implemented with atomic Lua scripts in Redis to prevent race conditions:

**Token Bucket** — each client gets a bucket of N tokens refilled at R tokens/second. Allows bursting up to bucket capacity. Good for APIs where occasional traffic spikes are acceptable.

**Sliding Window** — tracks all requests in the last N seconds using a Redis sorted set. No burst allowance. Strictly enforces the rate. Good for hard caps.

---

### Circuit Breaker

Three-state machine that protects upstream services from cascade failures:

```
CLOSED ──(threshold failures)──► OPEN ──(timeout elapsed)──► RECOVERY
  ▲                                                               │
  └──────────────(probe succeeds)────────────────────────────────┘
```

- **Closed** — normal operation, all requests flow through
- **Open** — upstream is failing, reject all requests immediately with 503
- **Recovery** — let one probe request through to test if upstream recovered

Without a circuit breaker, a down upstream causes every request to wait for the full timeout. With it, failure is instant and the upstream gets time to recover.

---

### Storage Abstraction

Rate limiter and circuit breaker state can be stored in memory or Redis. Same interface, swappable without changing any middleware logic. In-memory for tests, Redis for production multi-instance deployments.

---

### Route Configuration

Routes are defined in `config/routes.yaml`:

```yaml
routes:
  - route_id: my-service
    path_prefix: /api
    upstream_url: http://localhost:8001
    strip_prefix: true
    timeout_seconds: 30
    middleware:
      - logger
      - rate_limiter
      - circuit_breaker
    metadata:
      service_name: my-service
      owner: affan
```

Routing uses longest prefix match — `/api/v1` wins over `/api` for a request to `/api/v1/users`.

---

## Benchmarks

> Results will be updated after running benchmarks.

```
Benchmark 1: Raw upstream (no gateway)
  Requests/sec
  Latency p99

Benchmark 2: Through Gatekeeper
  Requests/sec
  Latency p99

General Metrics:
Middleware overhead (p99)
Rate limiter accuracy
Circuit breaker open time
```
---

## Tech Stack

- **Python 3.11** — core language
- **FastAPI** — HTTP framework and gateway entry point
- **httpx** — async HTTP client for upstream forwarding
- **Redis** — distributed rate limit and circuit breaker state
- **Pydantic** — settings and data validation
- **Docker** — containerisation
- **Prometheus + Grafana** — metrics and dashboards
- **pytest** — testing
- **wrk** — load testing

---

## Author

**Affan Shaikh**
- GitHub: [le-Affan](https://github.com/le-Affan)
- LinkedIn: [affan-shaikh-ml](https://linkedin.com/in/affan-shaikh-ml)
