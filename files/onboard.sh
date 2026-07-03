#!/bin/bash
# ==========================================================================
# AgentMail Agent — first-boot onboarding (runs on the desktop, once)
# ==========================================================================
# Two steps and you have a live email agent:
#   1. Nous model auth   (device-code — required for the agent to think)
#   2. AgentMail API key (am_… from console.agentmail.to — or pre-set in the
#      Orgo vault, in which case this step is automatic)
# Then the inbox is created, the 1-minute inbox responder is seeded, the
# gateway restarts, and the agent's email address is printed. Email it.
#
# Idempotent: each step is skipped once satisfied; re-running only does
# what's left. Marks a stamp when both steps are done so it stops nagging.
set +e
export HOME=/root
export HERMES_HOME=/root/.hermes
export DISPLAY="${DISPLAY:-:99}"
export PATH=/usr/local/bin:/root/.local/bin:/root/.hermes/bin:/usr/bin:/bin:$PATH

ENV_FILE="$HERMES_HOME/.env"
STAMP=/var/lib/orgo/agentmail-agent-onboarded
mkdir -p /var/lib/orgo "$HERMES_HOME"

hr() { printf '\n\033[1;36m%s\033[0m\n' "────────────────────────────────────────────────────────"; }
say() { printf '\033[1;32m%s\033[0m\n' "$*"; }

clear 2>/dev/null
say "  AgentMail Agent — your email-native AI agent"
hr

# --- 1. Nous model auth ----------------------------------------------------
if [ ! -s "$HERMES_HOME/auth.json" ]; then
  say "Step 1/2 — Connect your Nous account (model: gpt-5.5)"
  echo "A device-code sign-in will start. Follow the URL + code it prints."
  echo
  # `hermes auth` (bare) opens a credential-pool menu in v0.18 — call the
  # provider flow directly so the user lands straight in the device-code login.
  hermes auth add nous --type oauth
  echo
else
  say "Step 1/2 — Nous account already connected ✓"
fi

# --- 2. AgentMail key → inbox → responder ----------------------------------
# Pick up a vault-injected key first so this step is zero-touch when the
# launch set AGENTMAIL_API_KEY.
set -a
[ -f /root/.env ] && . /root/.env
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
set +a

if ! grep -q '^AGENTMAIL_API_KEY=..' "$ENV_FILE" 2>/dev/null && [ -z "$AGENTMAIL_API_KEY" ]; then
  hr
  say "Step 2/2 — Connect AgentMail"
  echo "Grab an API key at https://console.agentmail.to (Settings → API keys),"
  echo "then paste it here."
  echo
  printf 'AgentMail API key (am_…): '
  read -r AM_KEY
  AM_KEY="$(printf '%s' "$AM_KEY" | tr -d '[:space:]')"
  if [ -n "$AM_KEY" ]; then
    grep -vE '^AGENTMAIL_API_KEY=' "$ENV_FILE" > "$ENV_FILE.tmp" 2>/dev/null || true
    mv "$ENV_FILE.tmp" "$ENV_FILE"
    echo "AGENTMAIL_API_KEY=$AM_KEY" >> "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    export AGENTMAIL_API_KEY="$AM_KEY"
  fi
else
  say "Step 2/2 — AgentMail key already present ✓"
  # Make sure a vault-supplied key is persisted where Hermes reads it.
  if [ -n "$AGENTMAIL_API_KEY" ] && ! grep -q '^AGENTMAIL_API_KEY=..' "$ENV_FILE" 2>/dev/null; then
    echo "AGENTMAIL_API_KEY=$AGENTMAIL_API_KEY" >> "$ENV_FILE"
    chmod 600 "$ENV_FILE"
  fi
fi

if [ -n "$AGENTMAIL_API_KEY" ] || grep -q '^AGENTMAIL_API_KEY=..' "$ENV_FILE" 2>/dev/null; then
  set -a; . "$ENV_FILE"; set +a
  python3 /usr/local/bin/agentmail-bootstrap-inbox.py
  set -a; . "$ENV_FILE"; set +a
  python3 /usr/local/bin/agentmail-seed-inbox-cron.py
  # Initialize the dedupe ledger NOW, before we ever show the address — an
  # email arriving before the poller's first tick must count as new, not get
  # absorbed as backlog (bit a real user on day one).
  python3 /usr/local/bin/agentmail-inbox-helper.py poll >/dev/null 2>&1 || true
  # Enable the MCP servers whose keys now exist (agentmail / composio).
  python3 /usr/local/bin/agentmail-sync-mcp.py || true
  say "Restarting the gateway so it picks everything up…"
  supervisorctl restart hermes-gateway 2>/dev/null || true
fi

# --- Done ------------------------------------------------------------------
hr
set -a; [ -f "$ENV_FILE" ] && . "$ENV_FILE"; set +a
if [ -s "$HERMES_HOME/auth.json" ] && [ -n "$AGENTMAIL_INBOX" ]; then
  date -Iseconds > "$STAMP"
  say "Your agent is LIVE. Its email address:"
  echo
  printf '\033[1;33m    📬  %s\033[0m\n' "$AGENTMAIL_INBOX"
  echo
  echo "Send it an email right now — it replies within about a minute, and it"
  echo "has a real computer: ask it to research, fetch, summarize, or build."
  echo
  echo "Optional extras (paste a key in ~/.hermes/.env, or just tell the agent):"
  echo "  • Composio (1000+ apps):  COMPOSIO_CONSUMER_KEY=ck_…  (app.composio.dev)"
  echo "  • Custom domain: verify it at console.agentmail.to, then ask the agent"
  echo "    to create an inbox on it."
else
  say "Setup paused — re-open 'AgentMail Agent Setup' from the desktop to finish."
fi
echo
read -r -p "Press Enter to close…" _
