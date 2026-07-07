#!/bin/bash
# ==========================================================================
# AgentMail Agent — responder watchdog (supervised service)
# ==========================================================================
# Two independent checks every 120s:
#
# 1) Stuck-cron guard. Hermes's cron ticker has an in-flight dedupe guard:
#    while a job's previous run is still executing, new ticks log "already
#    running — skipping". Normally correct — but a model call with no timeout
#    (observed live: a chat stream to the inference API that never returned)
#    wedges the guard FOREVER, and the ticker heartbeat stays green the whole
#    time, so nothing else notices. Restart the gateway only on that exact
#    signature: consecutive "already running" skips AND no run output
#    recently (window sized for the 5-minute fallback schedule).
#
# 2) Stale listener guard (0.2.0). The WebSocket listener owns a heartbeat
#    file while connected. If the AgentMail key + inbox exist but the
#    heartbeat has been stale >5 min, the listener is wedged in-process (its
#    own backoff loop can't fix a generator blocked on a half-open socket) —
#    restart it. Rate-limited so an AgentMail outage doesn't cause restart
#    storms (the cron fallback is covering the inbox meanwhile).
set +e
LOG=/root/.hermes/logs/agent.log
OUT=/root/.hermes/cron/output/agentmail-inb01
ENVF=/root/.hermes/.env
HB=/root/.hermes/state/agentmail_listener_heartbeat
WLOG=/var/log/orgo/agentmail-watchdog.log
LAST_KICK=0
mkdir -p /var/log/orgo

while true; do
  sleep 120

  # ---- 1) stuck cron run → gateway restart --------------------------------
  if [ -f /root/.hermes/cron/jobs.json ] && \
     grep -q 'agentmail-inb01' /root/.hermes/cron/jobs.json 2>/dev/null; then
    SKIPS=$(tail -n 6 "$LOG" 2>/dev/null | grep -c 'already running')
    # A healthy 5-min job writes one output file per tick; 16 min ≈ 3 ticks.
    RECENT=$(find "$OUT" -name '*.md' -mmin -16 2>/dev/null | wc -l)
    if [ "$SKIPS" -ge 3 ] && [ "$RECENT" -eq 0 ]; then
      echo "$(date -Is) stuck in-flight guard (skips=$SKIPS, no output 16m) — restarting gateway" >> "$WLOG"
      if ! supervisorctl restart hermes-gateway >> "$WLOG" 2>&1; then
        # supervisorctl can lose its socket (field-tested: a duplicate-
        # supervisord boot where the socket owner died). Killing the gateway
        # works regardless: whichever supervisor owns it has restart:always.
        echo "$(date -Is) supervisorctl failed — pkill fallback" >> "$WLOG"
        pkill -f 'hermes gateway run' >> "$WLOG" 2>&1
      fi
      sleep 300
      continue
    fi
  fi

  # ---- 2) stale listener heartbeat → listener restart ----------------------
  grep -q '^AGENTMAIL_API_KEY=..' "$ENVF" 2>/dev/null || continue
  grep -q '^AGENTMAIL_INBOX' "$ENVF" 2>/dev/null || continue
  NOW=$(date +%s)
  HB_AGE=$(( NOW - $(stat -c %Y "$HB" 2>/dev/null || echo 0) ))
  [ "$HB_AGE" -gt 300 ] || continue
  [ $(( NOW - LAST_KICK )) -gt 600 ] || continue
  LAST_KICK=$NOW
  echo "$(date -Is) listener heartbeat stale (${HB_AGE}s) — restarting agentmail-listener" >> "$WLOG"
  if ! supervisorctl restart agentmail-listener >> "$WLOG" 2>&1; then
    echo "$(date -Is) supervisorctl failed — pkill fallback" >> "$WLOG"
    pkill -f 'agentmail-inbox-listener.py' >> "$WLOG" 2>&1
  fi
done
