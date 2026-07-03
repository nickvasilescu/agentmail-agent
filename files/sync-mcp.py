#!/usr/bin/env python3
# ==========================================================================
# AgentMail Agent — enable MCP servers only when their key exists
# ==========================================================================
# The agentmail + composio MCP blocks ship enabled:false. A key-less enabled
# server costs every cron run ~8 seconds of 401→retry→give-up (observed live)
# and floods errors.log. This script flips each server's `enabled` to match
# key presence; on_resume and the onboarding both call it (before the gateway
# restart they already do), so pasting/vaulting a key is all a user does.
#
# Reads keys from the process env, falling back to ~/.hermes/.env.
import os

import yaml

CONFIG = "/root/.hermes/config.yaml"
ENV_FILE = "/root/.hermes/.env"
SERVERS = {"agentmail": "AGENTMAIL_API_KEY", "composio": "COMPOSIO_CONSUMER_KEY"}


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


with open(CONFIG) as fh:
    cfg = yaml.safe_load(fh)

mcp = cfg.get("mcp_servers") or {}
changed = []
for server, var in SERVERS.items():
    if server not in mcp:
        continue
    want = bool(env(var))
    if bool(mcp[server].get("enabled")) != want:
        mcp[server]["enabled"] = want
        changed.append(f"{server}={'on' if want else 'off'}")

if changed:
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as fh:
        yaml.safe_dump(cfg, fh, default_flow_style=False, sort_keys=False,
                       allow_unicode=True, width=1000)
    os.replace(tmp, CONFIG)
    os.chmod(CONFIG, 0o600)
    print(f"[sync-mcp] {', '.join(changed)} (restart the gateway to apply)")
else:
    print("[sync-mcp] no changes")
