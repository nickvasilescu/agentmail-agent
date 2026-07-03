#!/bin/bash
# ==========================================================================
# AgentMail Agent — Hermes gateway wrapper (supervised service entrypoint)
# ==========================================================================
# Runs as root with HOME=/root and bridges BOTH env files, so the gateway is
# genuinely supervised, reboot-safe, and sees every key. Stays dormant (not
# crash-looping) until the baked config exists AND the user has completed the
# Nous sign-in (`hermes auth` — done by the first-boot onboarding).
set +e
export HOME=/root
export HERMES_HOME=/root/.hermes
export PATH=/usr/local/bin:/root/.local/bin:/root/.hermes/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH

until [ -f "$HERMES_HOME/config.yaml" ] && [ -s "$HERMES_HOME/auth.json" ]; do sleep 5; done

# Source the Orgo vault-injection target (/root/.env — may be absent) and
# Hermes' own env so the model-provider auth + the AgentMail key are visible
# to `hermes gateway run` (it does NOT auto-export either file).
set -a
[ -f /root/.env ] && . /root/.env
[ -f "$HERMES_HOME/.env" ] && . "$HERMES_HOME/.env"
set +a

# Exclusive-run lock held for the gateway's whole lifetime. Field-tested
# reason: an Orgo boot race can start TWO supervisords, each spawning this
# service — without the lock the twin gateways SIGTERM each other via
# --replace every ~2s, forever, and no cron run ever completes. Blocking
# flock makes the loser wait silently and take over if the winner dies.
# (Command form, not `exec 200>` — numbered-fd redirection failed with
# EBADF under supervisord's exec context. Lock lives in /var/lib/orgo, NOT
# /var/lock — that's a dangling symlink to a missing /run/lock on these VMs.)
mkdir -p /var/lib/orgo
exec flock /var/lib/orgo/agentmail-gateway.lock hermes gateway run --replace
