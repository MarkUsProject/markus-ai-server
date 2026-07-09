# markus-ai-server: Set Up, Configure, and Test

This guide has three parts. Part 1 says what the server does. Part 2 says how the
watching works. Part 3 shows how to set it up and test it.

For a step-by-step test from the MarkUs UI, see
[markus-testing-guide.md](./markus-testing-guide.md).

---

## Part 1: What it does

The server runs AI prompts for MarkUs. MarkUs sends a prompt. The server picks a
local model (Ollama or llama.cpp), runs it, and returns the answer.

Every request needs an API key. The server checks the key first:

- No key: the request is refused.
- Unknown key: the request is refused.
- Good key: the prompt runs and the answer comes back.

Each refusal is a small warning sign. One refusal means little. It could be a typo
or an old key. Many refusals from one address in a few minutes look like someone
guessing keys. That is a brute-force attack. Many refusals from many addresses look
like a group attack.

The server writes each refusal down as an audit event. The events go to a log
store. Rules watch the store and send an email when the count gets too high.

### What a refusal records

| Field | What it tells you |
|---|---|
| `event` | Always `auth_failure`, so security events are easy to find |
| `result` | `missing_key` (no key) or `invalid_key` (unknown key) |
| `client_ip` | The address the request came from |
| `endpoint` | The path that was hit, such as `/chat` |

### What a refusal never records

The server never logs the API key. It never logs the prompt. It never logs the
answer. Audit events hold only the four fields above. Student work stays out of the
log store.

---

## Part 2: How the watching works

The key check happens before any model runs. So you can test the watching with no
model running.

```
MarkUs --> markus-ai-server (Flask) --> Ollama / llama.cpp --> answer
                  |
                  | on a refused key: one audit event (OTLP log)
                  v
          OTel Collector --> Loki --> Grafana rules --> email
```

### Where the parts live

| File | What it does |
|---|---|
| `src/markus_ai_server/server.py` | The Flask app. `authenticate()` records each refusal |
| `src/markus_ai_server/telemetry.py` | Audit logging and proxy handling |
| `opentelemetry_collector/config.yml` | Collector: OTLP in, then out to Jaeger, Prometheus, Loki |
| `opentelemetry_collector/loki.yml` | Loki log store, 90-day retention |
| `opentelemetry_collector/grafana/provisioning/` | Data sources, alert rules, email contact point |
| `docker-compose.yml` | The whole stack. Monitoring sits behind a profile |
| `test/test_audit_logging.py` | Unit tests for the app |
| `test/e2e/suite.sh` | End-to-end test, the acceptance gate |

### Three things the design promises

1. **Logging never breaks the app.** Audit logging turns on only when
   `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Exports run on a background thread. Setup
   errors are caught and logged. A down collector drops no request.
2. **The client IP can be trusted.** The server uses `X-Forwarded-For` only when
   `TRUSTED_PROXY_HOPS` is above 0. Reached directly (`TRUSTED_PROXY_HOPS=0`), it
   ignores that header. No caller can fake an address.
3. **A refused key returns 401, not 500.** The error handler keeps HTTP error
   codes. A bad key returns a clean 401.

### The two alarm rules

Both live in `grafana/provisioning/alerting/rules.yml` and run every minute.

| Rule | Fires when | Severity |
|---|---|---|
| Brute force | One IP has more than 10 refusals in 5 minutes | warning |
| Spray | More than 50 refusals across all IPs in 5 minutes | critical |

The thresholds are starting points. Tune them against real traffic. When a rule
fires, Grafana sends an email (Mailpit catches it in the test stack).

---

## Part 3: Set it up, configure it, test it

Run it two ways. Local mode runs the app and unit tests. Docker mode runs the full
chain from audit event to email. The ports below match `docker-compose.yml`.

### 3.1 Local mode (app and unit tests)

```bash
cd ~/work/ai-server
uv sync                    # or: pip install -e . --group dev  (pip >= 25.1)
pre-commit install
```

Run the tests. The audit tests need no services running:

```bash
pytest                                  # whole suite
pytest test/test_audit_logging.py -q    # audit tests only
```

> **Verified:** `pytest` passes (53 tests, 13 in the audit file). The audit tests
> cover both `result` values with all four fields, no key or body in the record,
> the 401 and 400 responses, the gated OTLP setup, and the proxy IP rules.

### 3.2 Configuration (environment variables)

The server reads these at startup, from a `.env` file or the container environment.

| Variable | Default | What it controls |
|---|---|---|
| `REDIS_URL` | *(required)* | Redis that holds the API keys (`api-key:<key>` maps to a username) |
| `DEFAULT_MODEL` | `deepseek-coder-v2:latest` | Model used when a request names none |
| `OLLAMA_HOST` | *(unset)* | Ollama backend URL |
| `LLAMA_SERVER_URL` | *(unset)* | llama.cpp HTTP server URL |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | *(unset)* | Set this to turn audit logging on |
| `TRUSTED_PROXY_HOPS` | `0` | `0` reads the direct address. `1` trusts one proxy in front |
| `FLASK_DEBUG` | *(unset)* | Set to `1` for the dev debugger and auto-reload. Never set it in production |

> **Security note.** Match `TRUSTED_PROXY_HOPS` to the real setup. Set it to `1`
> only when one trusted proxy sits in front. A number above the true count lets a
> caller fake `X-Forwarded-For`. That breaks per-IP detection.

API keys live in Redis, one per user. The value is the username.

```bash
redis-cli set "api-key:secret123" alice
```

Callers present the key in the `X-API-KEY` header. The MarkUs Autotester's AI
tester reads its copy from the `REMOTE_API_KEY` environment variable (set on
the Autotester, or in a `.env` file uploaded with the assignment's test files).
If it is unset, every request fails with 401 `Missing API key`.

### 3.3 Start the full stack in Docker

The monitoring services start only with the `monitoring` profile.

```bash
cd ~/work/ai-server
docker compose --profile monitoring up -d --build
docker compose ps
```

The compose file sets `OTEL_EXPORTER_OTLP_ENDPOINT` and `TRUSTED_PROXY_HOPS=1` on
the app. Audit events flow. A test can set a client IP with `X-Forwarded-For`.

| Service | URL or port | Use it for |
|---|---|---|
| ai-server | http://localhost:5001 | the app (`POST /chat`) |
| Loki | http://localhost:3100 | audit log store. API only, no UI — browse the logs in Grafana → Explore → Loki |
| Grafana | http://localhost:3001 | browse audit logs (Explore), alert rules, contact points (anonymous admin) |
| Mailpit | http://localhost:8025 | read the alert emails |
| Jaeger | http://localhost:16686 | traces |
| Prometheus | http://localhost:9090 | metrics |
| Alertmanager | http://localhost:9093 | metric alerts |

Add a working key to the stack's Redis:

```bash
docker exec ai-server-redis redis-cli set "api-key:secret123" alice
```

### 3.4 Test the key check and the audit event (no model needed)

A missing key returns 401 and records one event:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:5001/chat -F 'content=hi'
# Expect: 401
```

A bad key returns 401 and records the address the proxy passes through:

```bash
curl -s -X POST http://localhost:5001/chat -H 'X-API-KEY: bogus' -H 'X-Forwarded-For: 203.0.113.42' -F 'content=hi'
# Expect body: {"error":"Invalid API key"}  (HTTP 401)
```

See the event in the app log:

```bash
docker logs ai-server-app 2>&1 | grep "authentication failure"
```

Find the event in Loki by client IP. The fields are structured metadata, so the
query needs no parser:

```bash
curl -s "http://localhost:3100/loki/api/v1/query" --data-urlencode 'query=sum by (client_ip) (count_over_time({service_name="ai-server"} | event="auth_failure" | client_ip="203.0.113.42" [5m]))'
# Expect: one result with a value of 1 or more
```

### 3.5 Test a good key against a model (happy path)

This path needs a model at `OLLAMA_HOST` (Ollama on the host machine).

```bash
curl -s -X POST http://localhost:5001/chat -H 'X-API-KEY: secret123' -F 'content=Say hello in one word.'
# Expect: HTTP 200 with the model's answer as a JSON string
```

With no model running, the key check still passes and records no event. The call
then fails with a model error. That is correct: the watching lives in the key
check, before any model runs.

### 3.6 Test the alarms and the email (acceptance gate)

The end-to-end suite replays brute-force and spray traffic. It then checks that
real alert emails land in Mailpit.

```bash
# The stack must be up (3.3). Then:
bash test/e2e/suite.sh
```

> **Verified:** the suite passed every check (`PASSED: 12 FAILED: 0`). Both rules
> fired and emailed Mailpit. The below-threshold IP did not fire. No secret
> reached Loki.

It runs eight scenarios (12 checks in total), prints a pass/fail count, and exits
non-zero on any failure.

| # | Check |
|---|---|
| T1 | All monitoring services answer |
| T2 | The app records an event with the proxied client IP |
| T3 | The event reaches Loki, searchable by `client_ip` |
| T4 | Grafana loaded the data source, both rules, and the email contact point |
| T5 | Rule 1 (brute force) fires and emails |
| T6 | Rule 2 (spray) fires and emails |
| T7 | An IP below the threshold does not trip Rule 1 |
| T8 | No API key or secret reaches Loki |

Watch the emails arrive at http://localhost:8025 while the suite runs.

To drive Rule 1 by hand, send 12 bad-key requests from one IP, wait up to 3
minutes, then read Mailpit:

```bash
for i in $(seq 1 12); do curl -s -o /dev/null -X POST http://localhost:5001/chat -H "X-API-KEY: bogus-$i" -H 'X-Forwarded-For: 203.0.113.66' -F 'content=hi'; done
curl -s http://localhost:8025/api/v1/messages | python3 -c "import sys,json;[print(m['Subject']) for m in json.load(sys.stdin)['messages']]"
```

### 3.7 Tear it down

```bash
docker compose --profile monitoring down          # keep data
docker compose --profile monitoring down -v       # also drop Loki, Grafana, Redis data
```

---

## Production deployment

The Docker stack is the test rig. In production, the sysadmins run central Loki
and Grafana. The server's collector ships logs to that central endpoint. Nothing
else in the image changes. The handoff steps:

1. Provision central Loki (3.0+) with OTLP ingest and 90-day retention.
2. Point the collector's `otlphttp/loki` exporter at it, over TLS.
3. Add Loki as a data source in central Grafana.
4. Import the two rules and the email contact point. Set the real email alias.
5. Run the app under gunicorn, not the Flask dev server.

## Troubleshooting

| Problem | Look here first |
|---|---|
| Events do not reach Loki | Is `OTEL_EXPORTER_OTLP_ENDPOINT` set on the app? Is the collector up? Export is batched, so wait a few seconds and query again |
| Every attacker shows one IP | `TRUSTED_PROXY_HOPS` is wrong. Behind a proxy it must equal the hop count. Direct it must be `0` |
| No alert email arrives | Grafana SMTP (`GF_SMTP_*`) must point at a reachable relay. Check `docker logs grafana` |
| `/chat` returns 500, not 401 | A non-auth error happened. Read the app log for the traceback |
| A good key still fails | The model backend (`OLLAMA_HOST`) is unreachable. The key passed. The failure is downstream |
