#!/usr/bin/env python3
# ==========================================================================
# AgentMail Agent — idempotent inbox bootstrap
# ==========================================================================
# Creates the agent's AgentMail inbox the moment a key is available and pins
# the address into ~/.hermes/.env (AGENTMAIL_INBOX / AGENTMAIL_INBOX_ID) so
# the gateway, the poller cron, and the agent itself all know their address.
#
# Idempotent three ways:
#   - exits 0 immediately if AGENTMAIL_INBOX is already pinned,
#   - creates with a stable per-VM client_id (AgentMail dedupes on client_id),
#   - falls back to finding an existing inbox with our client_id via GET.
#
# Reads AGENTMAIL_API_KEY from the process env (callers source /root/.env and
# ~/.hermes/.env first). Stdlib only — runs under any python3.
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.agentmail.to/v0"
ENV_FILE = "/root/.hermes/.env"
UA = "orgo-agentmail-agent/0.1 (+https://orgo.ai)"

key = os.environ.get("AGENTMAIL_API_KEY", "").strip()
if not key:
    print("[bootstrap-inbox] AGENTMAIL_API_KEY not set — skipping (will retry next resume).")
    sys.exit(0)

env_text = ""
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as fh:
        env_text = fh.read()
if re.search(r"^AGENTMAIL_INBOX=..", env_text, re.M):
    print("[bootstrap-inbox] inbox already pinned in ~/.hermes/.env — nothing to do.")
    sys.exit(0)


def call(method, path, body=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": UA,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


# Stable per-VM client_id so re-runs (and golden re-launches that kept state)
# converge on the same inbox instead of minting new ones.
# NOT /etc/machine-id: that is baked into the golden snapshot, so it is
# IDENTICAL on every VM launched from the same golden — all launches would
# converge on ONE inbox (field-hit: a fresh launch 409'd "Inbox is being
# deleted" against a prior VM's tombstoned inbox). This file never exists in
# the golden (bootstrap only runs at launch), so each VM mints its own once
# and re-runs on the same VM keep it.
IID_FILE = "/var/lib/orgo/agentmail-instance-id"
try:
    with open(IID_FILE) as fh:
        vm = fh.read().strip()
    if not vm:
        raise OSError
except OSError:
    import uuid
    vm = uuid.uuid4().hex[:12]
    os.makedirs(os.path.dirname(IID_FILE), exist_ok=True)
    with open(IID_FILE, "w") as fh:
        fh.write(vm)
client_id = f"orgo-agentmail-agent-{vm}"

inbox = None
try:
    inbox = call("POST", "/inboxes", {
        "client_id": client_id,
        "display_name": "Orgo Agent",
    })
except urllib.error.HTTPError as e:
    detail = ""
    try:
        detail = json.loads(e.read()).get("message", "")
    except Exception:  # noqa: BLE001
        pass
    print(f"[bootstrap-inbox] create returned HTTP {e.code}: {detail}")
    # ONLY adopt an existing inbox that carries OUR client_id — never an
    # arbitrary one (an org key can see every inbox in the account, and
    # auto-responding on someone else's inbox would be a disaster).
    try:
        listing = call("GET", "/inboxes")
        for ib in listing.get("inboxes", []):
            if ib.get("client_id") == client_id:
                inbox = ib
                break
    except Exception as e2:  # noqa: BLE001
        print(f"[bootstrap-inbox] list fallback failed: {e2}")
    if inbox is None and "limit" in detail.lower():
        print("[bootstrap-inbox] your AgentMail org is at its inbox limit — "
              "delete an unused inbox at console.agentmail.to (or upgrade), "
              "then re-run this setup. Alternatively pin an inbox you own by "
              "adding AGENTMAIL_INBOX=<address> and AGENTMAIL_INBOX_ID=<address> "
              "to ~/.hermes/.env yourself.")
except Exception as e:  # noqa: BLE001
    print(f"[bootstrap-inbox] create failed: {e}")

if not inbox:
    print("[bootstrap-inbox] could not create or find an inbox — will retry next resume.")
    sys.exit(0)

inbox_id = inbox.get("inbox_id", "")
email = inbox.get("email") or inbox_id
if not inbox_id:
    print(f"[bootstrap-inbox] unexpected response: {inbox}")
    sys.exit(0)

lines = [l for l in env_text.splitlines()
         if not l.startswith(("AGENTMAIL_INBOX=", "AGENTMAIL_INBOX_ID="))]
lines += [f"AGENTMAIL_INBOX={email}", f"AGENTMAIL_INBOX_ID={inbox_id}"]
tmp = ENV_FILE + ".tmp"
with open(tmp, "w") as fh:
    fh.write("\n".join(lines).rstrip("\n") + "\n")
os.replace(tmp, ENV_FILE)
os.chmod(ENV_FILE, 0o600)

print(f"[bootstrap-inbox] inbox ready: {email}")
