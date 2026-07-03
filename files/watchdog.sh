#!/bin/bash
# ==========================================================================
# AgentMail Agent — cron stuck-run watchdog (supervised service)
# ==========================================================================
# Hermes's cron ticker has an in-flight dedupe guard: while a job's previous
# run is still executing, new ticks log "already running — skipping". Normally
# correct — but a model call with no timeout (observed live: a chat stream to
# the inference API that never returned) wedges the guard FOREVER, and the
# ticker heartbeat stays green the whole time, so nothing else notices.
#
# This watchdog restarts the gateway only on that exact signature: the last
# few ticker lines are all "already running" skips for our job AND no run
# output has been produced recently. A restart clears the guard (the dedupe
# set is in-process); the ledger and job survive, and the next tick resumes
# replying — including anything that queued up while wedged.
set +e
LOG=/root/.hermes/logs/agent.log
OUT=/root/.hermes/cron/output/agentmail-inb01
WLOG=/var/log/orgo/agentmail-watchdog.log
mkdir -p /var/log/orgo

while true; do
  sleep 120
  [ -f /root/.hermes/cron/jobs.json ] || continue
  grep -q 'agentmail-inb01' /root/.hermes/cron/jobs.json 2>/dev/null || continue

  # Signature 1: ≥3 consecutive "already running — skipping" ticker lines.
  SKIPS=$(tail -n 6 "$LOG" 2>/dev/null | grep -c 'already running')
  [ "$SKIPS" -ge 3 ] || continue

  # Signature 2: no run output in the last 5 minutes (a healthy 1-min job
  # writes one file per run) — guards against acting on stale log tails.
  RECENT=$(find "$OUT" -name '*.md' -mmin -5 2>/dev/null | wc -l)
  [ "$RECENT" -eq 0 ] || continue

  echo "$(date -Is) stuck in-flight guard (skips=$SKIPS, no output 5m) — restarting gateway" >> "$WLOG"
  if ! supervisorctl restart hermes-gateway >> "$WLOG" 2>&1; then
    # supervisorctl can lose its socket (field-tested: a duplicate-supervisord
    # boot where the socket owner died). Killing the gateway works regardless:
    # whichever supervisor owns it has restart:always and revives it clean.
    echo "$(date -Is) supervisorctl failed — pkill fallback" >> "$WLOG"
    pkill -f 'hermes gateway run' >> "$WLOG" 2>&1
  fi
  sleep 300
done
