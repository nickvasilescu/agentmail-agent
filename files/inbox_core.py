#!/usr/bin/env python3
# ==========================================================================
# AgentMail Agent — deterministic inbox core (shared by helper CLI + listener)
# ==========================================================================
# Since 0.2.0 there are TWO writers of the reply/ledger state — the cron
# helper (agentmail-inbox-helper.py) and the WebSocket listener's responder
# passes — so everything stateful lives here behind file locks:
#
#   • processed-ledger  /root/.hermes/state/agentmail_inbox_processed.json
#       {"processed_ids": [...]}   — schema is frozen; guard rails intact
#       (init-at-setup, >5-new flood reinit, 48h stale skip, ≤3 per tick).
#   • claims file       /root/.hermes/state/agentmail_inbox_claims.json
#       {"claims": {"<message_id>": "<iso-ts>"}} — a message returned by one
#       poll() is invisible to concurrent polls for CLAIM_TTL, so a listener
#       pass and a cron pass can never both reply to the same message. An
#       expired claim re-qualifies (a pass that died mid-work gets retried).
#   • listener heartbeat /root/.hermes/state/agentmail_listener_heartbeat —
#       fresh file == "the WebSocket listener is covering this inbox", which
#       lets the cron fallback skip its API poll entirely.
#
# All REST stays stdlib urllib on purpose: the agentmail SDK (venv-installed)
# is used ONLY for the WebSocket, so an SDK regression can never break the
# reply path or the cron fallback.
import fcntl
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

API = "https://api.agentmail.to/v0"
ENV_FILE = "/root/.hermes/.env"
STATE_DIR = "/root/.hermes/state"
LEDGER = f"{STATE_DIR}/agentmail_inbox_processed.json"
CLAIMS = f"{STATE_DIR}/agentmail_inbox_claims.json"
HEARTBEAT = f"{STATE_DIR}/agentmail_listener_heartbeat"
LOCK_FILE = LEDGER + ".lock"
UA = "orgo-agentmail-agent/0.2 (+https://orgo.ai)"
MAX_REPLIES_PER_TICK = 3
STALE_HOURS = 48
CLAIM_TTL_MIN = 10
HEARTBEAT_FRESH_S = 120


def env(name):
    v = os.environ.get(name, "").strip()
    if v:
        return v
    try:
        with open(ENV_FILE) as fh:
            for line in fh:
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def key():
    return env("AGENTMAIL_API_KEY")


def inbox():
    return env("AGENTMAIL_INBOX_ID") or env("AGENTMAIL_INBOX")


def call(method, path, body=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {key()}",
                 "Content-Type": "application/json",
                 "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


@contextmanager
def state_lock():
    # One lock for ledger + claims: both files are tiny and every mutation
    # is a read-modify-write, so a single exclusive section is simplest.
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LOCK_FILE, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _load_ledger():
    # An EMPTY list is valid — a brand-new inbox has zero backlog at init
    # time, and treating empty-as-corrupt made the next poll "re-initialize"
    # the user's first email into the backlog (field-tested, twice). Only a
    # missing file, unparseable JSON, or a wrong schema triggers init; the
    # >5-new flood guard in poll() covers genuine corruption.
    try:
        with open(LEDGER) as fh:
            d = json.load(fh)
        ids = d.get("processed_ids")
        if isinstance(ids, list):
            return set(ids)
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return None  # missing/unparseable/wrong-schema → caller must initialize


def _save_ledger(ids):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"processed_ids": sorted(ids)}, fh, indent=1)
    os.replace(tmp, LEDGER)


def _load_claims():
    try:
        with open(CLAIMS) as fh:
            d = json.load(fh)
        c = d.get("claims")
        if isinstance(c, dict):
            return c
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return {}


def _save_claims(claims):
    tmp = CLAIMS + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"claims": claims}, fh, indent=1)
    os.replace(tmp, CLAIMS)


def parse_ts(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_received():
    q = urllib.parse.quote(inbox())
    d = call("GET", f"/inboxes/{q}/messages?limit=20&labels=received")
    return d.get("messages", [])


def poll():
    """One deterministic poll. Returns the dict the CLI prints as JSON."""
    msgs = fetch_received()
    now = datetime.now(timezone.utc)
    with state_lock():
        ledger = _load_ledger()
        if ledger is None:
            _save_ledger({m["message_id"] for m in msgs})
            return {"initialized": len(msgs), "new": []}

        claims = _load_claims()
        live = {}
        for mid, ts in claims.items():
            t = parse_ts(ts)
            if t and now - t < timedelta(minutes=CLAIM_TTL_MIN):
                live[mid] = ts
        claims = live

        fresh = [m for m in msgs
                 if m.get("message_id") not in ledger
                 and m.get("message_id") not in claims]
        # Oldest first so conversations stay ordered.
        fresh.sort(key=lambda m: str(m.get("timestamp", "")))

        # Guard: a flood of "new" mail means a broken ledger, not real traffic.
        if len(fresh) > 5:
            _save_ledger(ledger | {m["message_id"] for m in msgs})
            _save_claims(claims)
            return {"reinitialized": len(fresh), "new": []}

        picked = []
        for m in fresh:
            ts = parse_ts(m.get("timestamp"))
            if ts and now - ts > timedelta(hours=STALE_HOURS):
                ledger.add(m["message_id"])          # stale → never auto-reply
                continue
            if len(picked) >= MAX_REPLIES_PER_TICK:
                break                                # rest picked up next pass
            picked.append(m)
            claims[m["message_id"]] = now.isoformat()
        _save_ledger(ledger)
        _save_claims(claims)

    out = []
    for m in picked:
        q = urllib.parse.quote(inbox())
        try:
            full = call("GET", f"/inboxes/{q}/messages/"
                               f"{urllib.parse.quote(m['message_id'])}")
        except Exception:  # noqa: BLE001
            full = m
        out.append({
            "message_id": m.get("message_id"),
            "thread_id": m.get("thread_id"),
            "from": full.get("from") or m.get("from"),
            "subject": full.get("subject") or m.get("subject"),
            "timestamp": m.get("timestamp"),
            "text": (full.get("extracted_text") or full.get("text")
                     or m.get("preview") or "")[:4000],
        })
    return {"new": out}


def reply(message_id, text):
    q = urllib.parse.quote(inbox())
    call("POST", f"/inboxes/{q}/messages/{urllib.parse.quote(message_id)}/reply",
         {"text": text})
    mark(message_id)


def mark(message_id):
    with state_lock():
        ledger = _load_ledger() or set()
        ledger.add(message_id)
        _save_ledger(ledger)
        claims = _load_claims()
        claims.pop(message_id, None)
        _save_claims(claims)


def touch_heartbeat():
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(HEARTBEAT, "w") as fh:
        fh.write(datetime.now(timezone.utc).isoformat())


def heartbeat_fresh(max_age_s=HEARTBEAT_FRESH_S):
    try:
        return (datetime.now(timezone.utc).timestamp()
                - os.stat(HEARTBEAT).st_mtime) < max_age_s
    except OSError:
        return False


def responder_prompt(inbox_addr, cron=False):
    """The auto-responder prompt — single source for cron seed AND listener.

    cron=True makes step 1 use `poll --cron`, which skips the API poll while
    the listener heartbeat is fresh; the listener itself always polls."""
    poll_cmd = "poll --cron" if cron else "poll"
    return (
        "You are the email auto-responder for this computer's AgentMail inbox "
        f"({inbox_addr}). A deterministic helper script does ALL the mechanical work "
        "(polling, dedupe ledger, guard rails, sending). You only decide what to say.\n\n"
        "Identity: a concise, helpful AI assistant with its own cloud computer. "
        "Do not claim to be human.\n\n"
        "Run procedure every tick:\n"
        f"1. Run in the terminal:  python3 /usr/local/bin/agentmail-inbox-helper.py {poll_cmd}\n"
        "   It prints JSON. If \"new\" is empty (or it reports initialized/reinitialized/"
        "skipped), end with exactly: \"No new mail.\" Because deliver is local, this "
        "notifies no one.\n"
        "2. For each message in \"new\" (they arrive with from/subject/text):\n"
        "   - If it asks for real work (research, summarize, fetch a page, compute "
        "something), DO the work with your tools first, then write a short, natural "
        "reply containing the answer or result.\n"
        "   - If it is just a greeting or a test, briefly acknowledge that the agent "
        "is live and say what you can do.\n"
        "   - Obvious spam, marketing, or bounce notifications: do NOT reply — run  "
        "python3 /usr/local/bin/agentmail-inbox-helper.py mark <message_id>\n"
        "3. Send each reply by piping the text to:  "
        "python3 /usr/local/bin/agentmail-inbox-helper.py reply <message_id>\n"
        "   (heredoc works well:  python3 …helper.py reply '<id>' <<'EOF' … EOF )\n"
        "   The helper records the ledger on success; if it errors, do not retry more "
        "than once — the next tick will retry.\n"
        "4. Keep replies plain text, friendly, and signed off simply. Never include "
        "API keys or file paths from this machine in an email.\n\n"
        "Important: You are running unattended. Do not ask for clarification. "
        "Do not schedule more cron jobs."
    )
