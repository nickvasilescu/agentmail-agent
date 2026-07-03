#!/usr/bin/env python3
# ==========================================================================
# AgentMail Agent — deterministic inbox helper (the cron job's hands)
# ==========================================================================
# The 1-minute auto-responder cron delegates ALL mechanical work here so the
# LLM never re-implements polling/ledger code (that's how ledgers get
# corrupted). The model only decides WHAT to reply; this script decides what
# is new, guards against backlog-replies, and performs the sends.
#
#   agentmail-inbox-helper poll
#       → JSON to stdout:
#         {"initialized": N}                       first run / ledger reset
#         {"new": [{message_id, thread_id, from, subject, timestamp, text}]}
#         {"new": []}                              nothing to do
#   agentmail-inbox-helper reply <message_id>      reply text on stdin
#       → sends the reply; on 2xx records the id in the ledger
#   agentmail-inbox-helper mark <message_id>       record WITHOUT replying
#       → for spam / bounces / stale mail
#
# Reads AGENTMAIL_API_KEY + AGENTMAIL_INBOX_ID from the environment, falling
# back to parsing /root/.hermes/.env directly (cron contexts don't source it).
# Ledger: /root/.hermes/state/agentmail_inbox_processed.json =
# {"processed_ids": ["<message_id>", ...]} — written atomically, never any
# other schema.
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.agentmail.to/v0"
ENV_FILE = "/root/.hermes/.env"
LEDGER = "/root/.hermes/state/agentmail_inbox_processed.json"
UA = "orgo-agentmail-agent/0.1 (+https://orgo.ai)"
MAX_REPLIES_PER_TICK = 3
STALE_HOURS = 48


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


KEY = env("AGENTMAIL_API_KEY")
INBOX = env("AGENTMAIL_INBOX_ID") or env("AGENTMAIL_INBOX")


def die(msg):
    print(json.dumps({"error": msg}))
    sys.exit(1)


def call(method, path, body=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {KEY}",
                 "Content-Type": "application/json",
                 "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def load_ledger():
    # An EMPTY list is valid — a brand-new inbox has zero backlog at init
    # time, and treating empty-as-corrupt made the next poll "re-initialize"
    # the user's first email into the backlog (field-tested, twice). Only a
    # missing file, unparseable JSON, or a wrong schema triggers init; the
    # >5-new flood guard in cmd_poll covers genuine corruption.
    try:
        with open(LEDGER) as fh:
            d = json.load(fh)
        ids = d.get("processed_ids")
        if isinstance(ids, list):
            return set(ids)
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return None  # missing/unparseable/wrong-schema → caller must initialize


def save_ledger(ids):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"processed_ids": sorted(ids)}, fh, indent=1)
    os.replace(tmp, LEDGER)


def fetch_received():
    q = urllib.parse.quote(INBOX)
    d = call("GET", f"/inboxes/{q}/messages?limit=20&labels=received")
    return d.get("messages", [])


def parse_ts(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def cmd_poll():
    msgs = fetch_received()
    ledger = load_ledger()
    if ledger is None:
        save_ledger({m["message_id"] for m in msgs})
        print(json.dumps({"initialized": len(msgs), "new": []}))
        return

    fresh = [m for m in msgs if m.get("message_id") not in ledger]
    # Oldest first so conversations stay ordered.
    fresh.sort(key=lambda m: str(m.get("timestamp", "")))

    # Guard: a flood of "new" mail means a broken ledger, not real traffic.
    if len(fresh) > 5:
        save_ledger(ledger | {m["message_id"] for m in msgs})
        print(json.dumps({"reinitialized": len(fresh), "new": []}))
        return

    now = datetime.now(timezone.utc)
    out = []
    for m in fresh:
        ts = parse_ts(m.get("timestamp"))
        if ts and now - ts > timedelta(hours=STALE_HOURS):
            ledger.add(m["message_id"])          # stale → never auto-reply
            continue
        if len(out) >= MAX_REPLIES_PER_TICK:
            break                                # rest picked up next tick
        q = urllib.parse.quote(INBOX)
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
    save_ledger(ledger)
    print(json.dumps({"new": out}, indent=1))


def cmd_reply(message_id):
    text = sys.stdin.read().strip()
    if not text:
        die("empty reply text on stdin")
    q = urllib.parse.quote(INBOX)
    try:
        call("POST", f"/inboxes/{q}/messages/{urllib.parse.quote(message_id)}/reply",
             {"text": text})
    except urllib.error.HTTPError as e:
        die(f"reply failed: HTTP {e.code}")
    ledger = load_ledger() or set()
    ledger.add(message_id)
    save_ledger(ledger)
    print(json.dumps({"replied": message_id}))


def cmd_mark(message_id):
    ledger = load_ledger() or set()
    ledger.add(message_id)
    save_ledger(ledger)
    print(json.dumps({"marked": message_id}))


def main():
    if not KEY:
        die("AGENTMAIL_API_KEY not set")
    if not INBOX:
        die("AGENTMAIL_INBOX_ID not set")
    args = sys.argv[1:]
    if args[:1] == ["poll"]:
        cmd_poll()
    elif args[:1] == ["reply"] and len(args) == 2:
        cmd_reply(args[1])
    elif args[:1] == ["mark"] and len(args) == 2:
        cmd_mark(args[1])
    else:
        die("usage: agentmail-inbox-helper poll | reply <message_id> | mark <message_id>")


if __name__ == "__main__":
    main()
