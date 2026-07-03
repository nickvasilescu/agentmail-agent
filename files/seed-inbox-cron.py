#!/usr/bin/env python3
# ==========================================================================
# AgentMail Agent — seed the inbox auto-responder cron job
# ==========================================================================
# The heart of the template: a 1-minute Hermes cron job (deliver: local,
# terminal toolset) that polls the agent's AgentMail inbox and replies to new
# inbound mail — the "webhook experience" with zero webhook setup and no
# public endpoint on the VM. Same proven pattern as Nick's Stack's AgentPhone
# SMS bridge.
#
# Idempotent: only seeds when AGENTMAIL_API_KEY + AGENTMAIL_INBOX_ID are both
# present AND the job doesn't already exist. Callers source /root/.env and
# ~/.hermes/.env first (on_resume and the onboarding both do).
import json
import os
import sys
from datetime import datetime, timezone

JOBS_FILE = "/root/.hermes/cron/jobs.json"
JOB_ID = "agentmail-inb01"           # any 12+ char path-safe id
JOB_NAME = "agentmail-inbox-auto-responder"

API_KEY = os.environ.get("AGENTMAIL_API_KEY", "").strip()
INBOX_ID = os.environ.get("AGENTMAIL_INBOX_ID", "").strip()
INBOX = os.environ.get("AGENTMAIL_INBOX", INBOX_ID).strip()
MODEL = os.environ.get("AGENTMAIL_AGENT_MODEL", "openai/gpt-5.5").strip()
PROVIDER = os.environ.get("AGENTMAIL_AGENT_PROVIDER", "nous").strip()

if not (API_KEY and INBOX_ID):
    print("[seed-inbox-cron] AgentMail key/inbox not both set — skipping cron seed.")
    sys.exit(0)

PROMPT = (
    "You are the email auto-responder for this computer's AgentMail inbox "
    f"({INBOX}). A deterministic helper script does ALL the mechanical work "
    "(polling, dedupe ledger, guard rails, sending). You only decide what to say.\n\n"
    "Identity: a concise, helpful AI assistant with its own cloud computer. "
    "Do not claim to be human.\n\n"
    "Run procedure every tick:\n"
    "1. Run in the terminal:  python3 /usr/local/bin/agentmail-inbox-helper.py poll\n"
    "   It prints JSON. If \"new\" is empty (or it reports initialized/reinitialized), "
    "end with exactly: \"No new mail.\" Because deliver is local, this notifies no one.\n"
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

job = {
    "id": JOB_ID,
    "name": JOB_NAME,
    "prompt": PROMPT,
    "skills": [],
    "skill": None,
    "model": MODEL,
    "provider": PROVIDER,
    "base_url": None,
    "script": None,
    "no_agent": False,
    "context_from": None,
    "schedule": {"kind": "interval", "minutes": 1, "display": "every 1m"},
    "schedule_display": "every 1m",
    "repeat": {"times": None, "completed": 0},
    "enabled": True,
    "state": "scheduled",
    "paused_at": None,
    "paused_reason": None,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "next_run_at": None,          # loader recomputes for a recurring job
    "last_run_at": None,
    "last_status": None,
    "last_error": None,
    "last_delivery_error": None,
    "deliver": "local",
    "origin": None,
    "enabled_toolsets": ["terminal"],
    "workdir": None,
    "profile": None,
}

os.makedirs(os.path.dirname(JOBS_FILE), mode=0o700, exist_ok=True)
try:
    with open(JOBS_FILE) as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        data = {"jobs": data if isinstance(data, list) else []}
except (FileNotFoundError, json.JSONDecodeError):
    data = {"jobs": []}

jobs = data.get("jobs", [])
if any(j.get("name") == JOB_NAME for j in jobs):
    print("[seed-inbox-cron] inbox auto-responder already present — leaving as-is.")
    sys.exit(0)

jobs.append(job)
data["jobs"] = jobs
data["updated_at"] = datetime.now(timezone.utc).isoformat()

tmp = JOBS_FILE + ".tmp"
with open(tmp, "w") as fh:
    json.dump(data, fh, indent=2)
os.replace(tmp, JOBS_FILE)
os.chmod(JOBS_FILE, 0o600)
os.chmod(os.path.dirname(JOBS_FILE), 0o700)
print("[seed-inbox-cron] Seeded agentmail-inbox-auto-responder (every 1m, deliver local).")
