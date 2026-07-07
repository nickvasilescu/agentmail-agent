#!/usr/bin/env python3
# ==========================================================================
# AgentMail Agent — seed the inbox auto-responder cron job (fallback path)
# ==========================================================================
# Since 0.2.0 the PRIMARY responder trigger is the WebSocket listener
# (agentmail-inbox-listener.py — instant message.received events, no public
# endpoint). This cron is the degraded-mode fallback: every 5 minutes, and
# `poll --cron` makes it a no-op (zero API calls) while the listener
# heartbeat is fresh. It only actually polls when the socket is down
# (AgentMail outage, network blip, listener crash-loop).
#
# Idempotent + self-migrating: seeds when AGENTMAIL_API_KEY +
# AGENTMAIL_INBOX_ID are both present; if the job already exists with the
# pre-0.2.0 shape (1-minute schedule / prompt without --cron) it is updated
# in place, otherwise left untouched. Callers source /root/.env and
# ~/.hermes/.env first (on_resume and the onboarding both do).
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/usr/local/bin")
import agentmail_inbox_core as core  # noqa: E402

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

PROMPT = core.responder_prompt(INBOX, cron=True)
SCHEDULE = {"kind": "interval", "minutes": 5, "display": "every 5m"}

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
    "schedule": dict(SCHEDULE),
    "schedule_display": SCHEDULE["display"],
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
existing = next((j for j in jobs if j.get("name") == JOB_NAME), None)
if existing is not None:
    up_to_date = (existing.get("schedule", {}).get("minutes") == SCHEDULE["minutes"]
                  and "--cron" in str(existing.get("prompt", "")))
    if up_to_date:
        print("[seed-inbox-cron] inbox auto-responder already current — leaving as-is.")
        sys.exit(0)
    # In-place migration (0.1.x → 0.2.0): keep identity/state fields, update
    # the schedule + prompt; next_run_at cleared so the loader recomputes.
    existing["prompt"] = PROMPT
    existing["schedule"] = dict(SCHEDULE)
    existing["schedule_display"] = SCHEDULE["display"]
    existing["next_run_at"] = None
    action = "Migrated"
else:
    jobs.append(job)
    action = "Seeded"

data["jobs"] = jobs
data["updated_at"] = datetime.now(timezone.utc).isoformat()

tmp = JOBS_FILE + ".tmp"
with open(tmp, "w") as fh:
    json.dump(data, fh, indent=2)
os.replace(tmp, JOBS_FILE)
os.chmod(JOBS_FILE, 0o600)
os.chmod(os.path.dirname(JOBS_FILE), 0o700)
print(f"[seed-inbox-cron] {action} {JOB_NAME} (every 5m fallback, deliver local; "
      "WebSocket listener is the primary trigger).")
