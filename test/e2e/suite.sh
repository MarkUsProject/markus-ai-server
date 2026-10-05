#!/usr/bin/env bash
# End-to-end test SUITE for the intrusion-detection feature.
#
# Assumes the stack is running:
#   docker compose --profile monitoring up -d --build
# with OTEL_EXPORTER_OTLP_ENDPOINT=http://loki:3100/otlp set in .env.
#
# Validates the whole chain across several scenarios and prints a pass/fail
# tally. Exit code is non-zero if any scenario fails.
set -uo pipefail

APP=${APP_URL:-http://localhost:5001}
LOKI=${LOKI_URL:-http://localhost:3100}
GRAFANA=${GRAFANA_URL:-http://localhost:3001}
MAILPIT=${MAILPIT_URL:-http://localhost:8025}

PASS=0
FAIL=0
note() { printf '\n=== %s ===\n' "$1"; }
ok()   { echo "PASS: $1"; PASS=$((PASS + 1)); }
bad()  { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

send_bad_key() { # $1=api-key suffix  $2=client ip
  curl -s -o /dev/null -X POST "${APP}/chat" -H "X-API-KEY: bogus-$1" -H "X-Forwarded-For: $2" -F "content=hi" || true
}

loki_count() { # $1=logql -> single number (0 if none)
  curl -s "${LOKI}/loki/api/v1/query" --data-urlencode "query=$1" | python3 -c "import sys,json; r=json.load(sys.stdin).get('data',{}).get('result',[]); print(int(float(r[0]['value'][1])) if r else 0)" 2>/dev/null || echo 0
}

auth_failures_from_ip() { # $1=client ip -> auth-failure count in Loki over the last 5m
  loki_count "sum by (client_ip) (count_over_time({service_name=\"ai-server\"} | event=\"auth_failure\" | client_ip=\"$1\" [5m]))"
}

# Firing subjects only. Grafana also mails "[RESOLVED] <same title>" when an alert
# clears, and that string matches the firing check, which would be a false pass.
mailpit_subjects() {
  curl -s "${MAILPIT}/api/v1/messages" | python3 -c "import sys,json; [print(m.get('Subject','')) for m in json.load(sys.stdin).get('messages',[]) if '[RESOLVED]' not in m.get('Subject','')]" 2>/dev/null
}

mailpit_clear() { curl -s -X DELETE "${MAILPIT}/api/v1/messages" >/dev/null || true; }

wait_for_subject() { # $1=case-insensitive subject substring; 0 if seen within ~3 min.
  # grep -c reads the whole stream, so a match cannot SIGPIPE the pipe under pipefail.
  for _ in $(seq 1 18); do
    [ "$(mailpit_subjects | grep -ci "$1")" -ge 1 ] && return 0
    sleep 10
  done
  return 1
}

# ---------------------------------------------------------------------------
note "T0: preflight"
# Without this the app exports nothing and T3 fails as "Loki missing events",
# which points at Loki rather than at the unset variable. Say so up front.
endpoint=$(docker exec ai-server-app printenv OTEL_EXPORTER_OTLP_ENDPOINT 2>/dev/null || true)
if [ -z "$endpoint" ]; then
  echo "FATAL: OTEL_EXPORTER_OTLP_ENDPOINT is unset on ai-server-app."
  echo "       Set it in .env (see .env.example) and restart:"
  echo "         OTEL_EXPORTER_OTLP_ENDPOINT=http://loki:3100/otlp"
  echo "         docker compose --profile monitoring up -d"
  exit 1
fi
ok "app exports audit events to $endpoint"

# Loki answers /ready with 503 until its ingester is up. Wait for a real 200 so
# T3 measures ingestion rather than a race against startup.
wait_for_loki_ready() {
  for _ in $(seq 1 30); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' "${LOKI}/ready")" = "200" ] && return 0
    sleep 2
  done
  return 1
}
wait_for_loki_ready && ok "loki ready" || bad "loki never became ready (still not 200 on /ready)"

# Alert state carries across runs. policies.yml sets repeat_interval 1h, so a rule
# still firing from a previous run sends no new mail and T5/T6 would fail on a
# re-run. The rules count over a 5m window, so they clear ~5-6 min after the last
# failed key. Wait that out here to keep the suite re-runnable.
rules_firing() {
  curl -s "${GRAFANA}/api/prometheus/grafana/api/v1/rules" | python3 -c "import sys,json; print(sum(1 for g in json.load(sys.stdin).get('data',{}).get('groups',[]) for r in g.get('rules',[]) if r.get('state')=='firing'))" 2>/dev/null || echo 0
}
wait_for_rules_inactive() {
  for _ in $(seq 1 48); do
    [ "$(rules_firing)" = "0" ] && return 0
    sleep 10
  done
  return 1
}
if [ "$(rules_firing)" != "0" ]; then
  echo "alerts still firing from an earlier run; waiting up to 8 min for them to clear..."
fi
wait_for_rules_inactive && ok "alert rules start from a clean (inactive) state" \
  || bad "alert rules never cleared; T5/T6 will report false failures"
mailpit_clear

note "T1: all monitoring services reachable"
# `app` answers 404 on `/` (no route), so any real HTTP code counts as reachable
# there. loki, grafana and mailpit expose true health endpoints: demand a 200.
for pair in "app:${APP}/:any" "loki:${LOKI}/ready:200" "grafana:${GRAFANA}/api/health:200" "mailpit:${MAILPIT}/api/v1/messages:200"; do
  svc=${pair%%:*}; rest=${pair#*:}; want=${rest##*:}; url=${rest%:*}
  code=$(curl -s -o /dev/null -w '%{http_code}' "$url")
  if [ "$want" = "any" ]; then
    [ "$code" != "000" ] && ok "$svc reachable ($code)" || bad "$svc unreachable"
    continue
  fi
  [ "$code" = "$want" ] && ok "$svc healthy ($code)" || bad "$svc unhealthy (got $code, want $want)"
done

note "T2: app emits structured audit event with proxied client IP"
send_bad_key probe 203.0.113.42
# The WARNING line reaches stdout asynchronously, so retry briefly. Use
# `grep -c` (reads the whole stream) rather than `grep -q` (exits early and
# SIGPIPEs `docker logs`, which under `set -o pipefail` would falsely fail).
app_logged_auth_failure() {
  for _ in $(seq 1 10); do
    [ "$(docker logs ai-server-app 2>&1 | grep -c 'authentication failure')" -ge 1 ] && return 0
    sleep 1
  done
  return 1
}
app_logged_auth_failure && ok "auth-failure event emitted by app" || bad "no auth-failure event in app logs"

note "T3: events reach Loki as structured metadata (queryable by client_ip)"
# OTLP export -> Loki is batched/async, so poll instead of a single sleep
# (which races the first export flush after an app restart).
c=0
for _ in $(seq 1 20); do
  c=$(auth_failures_from_ip 203.0.113.42)
  [ "$c" -ge 1 ] && break
  sleep 2
done
[ "$c" -ge 1 ] && ok "Loki has events for 203.0.113.42 (count=$c)" || bad "Loki missing events for probe IP (count=$c)"

note "T4: Grafana provisioning loaded (datasource + 2 rules + contact point)"
rules=$(curl -s "${GRAFANA}/api/v1/provisioning/alert-rules" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "$rules" -ge 2 ] && ok "$rules alert rules provisioned" || bad "expected >=2 alert rules, got $rules"
cp_hits=$(curl -s "${GRAFANA}/api/v1/provisioning/contact-points" | grep -c "ai-server-email")
[ "${cp_hits:-0}" -ge 1 ] && ok "email contact point present" || bad "email contact point missing"

note "T5: Rule 1 (brute force) fires and emails"
for i in $(seq 1 12); do send_bad_key "$i" 203.0.113.66; done
echo "waiting up to 3 min for Rule 1 email..."
wait_for_subject "brute force" && ok "Rule 1 brute-force alert email received" || bad "Rule 1 email not received"

note "T6: Rule 2 (spray) fires and emails"
for i in $(seq 1 55); do send_bad_key spray "198.51.100.$i"; done
echo "waiting up to 3 min for Rule 2 email..."
wait_for_subject "spray" && ok "Rule 2 spray alert email received" || bad "Rule 2 email not received"

note "T7: negative — a sub-threshold IP stays below the brute-force bar"
send_bad_key low 192.0.2.7; send_bad_key low 192.0.2.7; send_bad_key low 192.0.2.7
sleep 3
c=$(auth_failures_from_ip 192.0.2.7)
[ "$c" -lt 10 ] && ok "sub-threshold IP count=$c (<10, would not fire Rule 1)" || bad "sub-threshold IP unexpectedly high (count=$c)"

note "T8: secrets never reach the log store"
# Use `grep -c` (reads the whole stream) so a match cannot SIGPIPE curl and,
# under `set -o pipefail`, mask a leaked secret as a false PASS.
secret_hits=$(curl -s "${LOKI}/loki/api/v1/query_range" --data-urlencode 'query={service_name="ai-server"}' --data-urlencode 'limit=200' | grep -ciE "x-api-key|bogus-|api-key:")
if [ "${secret_hits:-0}" -ge 1 ]; then
  bad "possible secret/key material found in Loki"
else
  ok "no API keys or secrets found in Loki logs"
fi

# ---------------------------------------------------------------------------
printf '\n================ E2E SUITE RESULT ================\n'
printf 'PASSED: %d   FAILED: %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && { echo "ALL GREEN"; exit 0; } || { echo "SUITE FAILED"; exit 1; }
