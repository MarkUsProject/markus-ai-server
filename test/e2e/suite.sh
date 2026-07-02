#!/usr/bin/env bash
# End-to-end test SUITE for the intrusion-detection feature.
#
# Assumes the stack is running:
#   docker compose --profile monitoring up -d --build
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

mailpit_subjects() {
  curl -s "${MAILPIT}/api/v1/messages" | python3 -c "import sys,json; [print(m.get('Subject','')) for m in json.load(sys.stdin).get('messages',[])]" 2>/dev/null
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
note "T1: all monitoring services reachable"
for pair in "app:${APP}/" "loki:${LOKI}/ready" "grafana:${GRAFANA}/api/health" "mailpit:${MAILPIT}/api/v1/messages"; do
  url=${pair#*:}; svc=${pair%%:*}
  code=$(curl -s -o /dev/null -w '%{http_code}' "$url")
  [ "$code" != "000" ] && ok "$svc reachable ($code)" || bad "$svc unreachable"
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
# OTLP export -> collector -> Loki is batched/async, so poll instead of a
# single sleep (which races the first export flush after an app restart).
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
mailpit_clear
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
