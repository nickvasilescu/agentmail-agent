#!/usr/bin/env python3
# ==========================================================================
# AgentMail Agent — WebSocket inbox listener (supervised service, 0.2.0)
# ==========================================================================
# Holds a persistent AgentMail WebSocket subscription to this VM's inbox and,
# on message.received, immediately triggers ONE responder pass — the same
# "helper poll → Hermes composes → helper reply" pipeline the cron fallback
# runs. The listener NEVER composes replies and NEVER trusts the event
# payload: all dedupe/guards live in agentmail_inbox_core, so a listener
# pass and a cron pass can never double-reply.
#
# Runs under /opt/agentmail-agent/venv/bin/python (the only place the
# agentmail SDK exists — the reply path stays stdlib REST on purpose).
# Wrapped by agentmail-listener-run.sh (lifetime flock, env sourcing).
#
# Lifecycle:
#   keyless idle (10s env poll, NO heartbeat)  →  connect + subscribe
#   →  heartbeat thread (30s while connected)  →  events trigger passes
#   →  on error: heartbeat stops (cron gate opens), backoff, reconnect.
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

sys.path.insert(0, "/usr/local/bin")
import agentmail_inbox_core as core  # noqa: E402

RESPONDER_LOCK = "/var/lib/orgo/agentmail-responder.lock"
PASS_TIMEOUT_S = "600"          # guards the known no-LLM-timeout wedge class
IDLE_POLL_S = 10
HEARTBEAT_EVERY_S = 30
SAFETY_PASS_EVERY_S = 600       # sweep for stranded mail (== claim TTL)

_pass_wanted = threading.Event()
_connected = threading.Event()


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
    print(f"{ts} [inbox-listener] {msg}", flush=True)


def responder_worker():
    """Serialize responder passes; coalesce bursts into at most one queued pass."""
    while True:
        _pass_wanted.wait()
        _pass_wanted.clear()
        prompt = core.responder_prompt(core.inbox())
        log("responder pass starting")
        try:
            # flock -n: if the cron path is mid-pass, skip — its poll picks the
            # message up; our claims file keeps the two from double-replying.
            r = subprocess.run(
                ["flock", "-n", RESPONDER_LOCK, "timeout", PASS_TIMEOUT_S,
                 "hermes", "-z", prompt],
                capture_output=True, text=True, timeout=int(PASS_TIMEOUT_S) + 30,
            )
            log(f"responder pass done (rc={r.returncode})")
            out = (r.stdout or "") + (r.stderr or "")
            # hermes -z exits 0 even when the model call failed (field-hit:
            # a zero-credit account 404s every turn while rc stays 0) —
            # surface it so the failure isn't silent.
            if r.returncode != 0 or "API call failed" in out:
                log(f"pass output tail: {out.strip()[-400:]}")
        except Exception as e:  # noqa: BLE001
            log(f"responder pass error: {e}")


def heartbeat_worker():
    while True:
        if _connected.is_set():
            core.touch_heartbeat()
        time.sleep(HEARTBEAT_EVERY_S)


def safety_pass_worker():
    # A pass that fails mid-work (model error, timeout) leaves its message
    # claimed-then-expired with NOTHING to re-trigger it: no new event fires,
    # and the cron skips while our heartbeat is fresh (field-hit on a live
    # VM). Sweep periodically; poll() no-ops when nothing is stranded.
    while True:
        time.sleep(SAFETY_PASS_EVERY_S)
        if _connected.is_set():
            _pass_wanted.set()


def sync_clock():
    # Fresh-VM clock skew breaks TLS ("certificate not yet valid") — same
    # HTTP-Date fix the on_resume hook uses.
    subprocess.run(
        ["bash", "-c",
         "date -s \"$(curl -sI http://www.google.com | awk 'tolower($1)==\"date:\""
         "{sub($1 FS,\"\");print}')\" 2>/dev/null || true"],
        capture_output=True, timeout=30,
    )


def listen_once():
    from agentmail import AgentMail, MessageReceivedEvent, Subscribe, Subscribed

    client = AgentMail(api_key=core.key())
    inbox_id = core.inbox()
    with client.websockets.connect() as socket:
        socket.send_subscribe(Subscribe(inbox_ids=[inbox_id]))
        for event in socket:
            if isinstance(event, Subscribed):
                log(f"subscribed: {inbox_id}")
                _connected.set()
                core.touch_heartbeat()
                # Catch-up pass: a message that arrived while we were down got
                # no event (no replay on reconnect), and with a fresh heartbeat
                # the cron gate skips — so sweep once on every (re)subscribe.
                # The pass's poll() no-ops when nothing is new.
                _pass_wanted.set()
            elif isinstance(event, MessageReceivedEvent):
                frm = getattr(event.message, "from_", "?")
                log(f"message.received from {frm}")
                core.touch_heartbeat()
                _pass_wanted.set()


def main():
    threading.Thread(target=responder_worker, daemon=True).start()
    threading.Thread(target=heartbeat_worker, daemon=True).start()
    threading.Thread(target=safety_pass_worker, daemon=True).start()

    log("waiting for AgentMail key + inbox…")
    while not (core.key() and core.inbox()):
        time.sleep(IDLE_POLL_S)
    log(f"configured for {core.inbox()} — connecting")

    backoff = 1
    while True:
        started = time.monotonic()
        try:
            listen_once()
            log("socket closed cleanly")
        except Exception as e:  # noqa: BLE001
            log(f"socket error: {type(e).__name__}: {e}")
            if "certificate" in str(e).lower() or "ssl" in type(e).__name__.lower():
                sync_clock()
        _connected.clear()
        # Stable for 10+ minutes → treat the drop as fresh, reset backoff.
        if time.monotonic() - started > 600:
            backoff = 1
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    main()
