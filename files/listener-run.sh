#!/bin/bash
# ==========================================================================
# AgentMail Agent — WebSocket listener wrapper (supervised service entrypoint)
# ==========================================================================
# Mirror of gateway-run.sh: root, both env files bridged, dormant (not
# crash-looping) until the SDK venv exists, and the process held under a
# lifetime flock so the duplicate-supervisord boot race can never run two
# listeners (twin subscriptions → twin responder passes).
set +e
export HOME=/root
export HERMES_HOME=/root/.hermes
export PATH=/usr/local/bin:/root/.local/bin:/root/.hermes/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH

VENV_PY=/opt/agentmail-agent/venv/bin/python
until [ -x "$VENV_PY" ]; do sleep 5; done

# Source the Orgo vault-injection target (/root/.env — may be absent) and
# Hermes' own env so the listener sees the AgentMail key without restarts.
set -a
[ -f /root/.env ] && . /root/.env
[ -f "$HERMES_HOME/.env" ] && . "$HERMES_HOME/.env"
set +a

# Self-replace: supervisorctl stop/restart TERMs only the flock wrapper — the
# python grandchild survives, keeps the lock, and the fresh instance would
# block on flock forever (field-hit on a live VM). Kill any survivor first;
# in the duplicate-supervisord race this converges to exactly one listener
# (loser's pkill drops the winner's child, winner's flock exits and releases,
# loser acquires and spawns — same net effect as the gateway's --replace).
pkill -f '/usr/local/bin/agentmail-inbox-listener.py' 2>/dev/null || true
sleep 1

# Command-form flock in /var/lib/orgo (NOT /var/lock — dangling symlink on
# these VMs); same field-tested defense as the gateway wrapper.
mkdir -p /var/lib/orgo
exec flock /var/lib/orgo/agentmail-listener.lock "$VENV_PY" /usr/local/bin/agentmail-inbox-listener.py
