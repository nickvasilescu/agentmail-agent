#!/usr/bin/env python3
# ==========================================================================
# AgentMail Agent — template builder / publisher  (key-less, self-contained)
# ==========================================================================
# The AgentMail × Orgo one-click template: a persistent Hermes email agent
# whose front door is its own AgentMail inbox. Assembles the orgo.ai/v1 doc
# programmatically from the byte-exact files in ./files, validates it, and
# (optionally) runs the proven publish -> build -> stream -> launch REST flow.
#
#   python3 build_template.py                     # assemble + local jsonschema validate + dump resolved.json
#   python3 build_template.py --remote-validate   # + POST /api/templates/validate
#   python3 build_template.py --publish           # publish (wrapped envelope)
#   python3 build_template.py --build             # publish + trigger build + stream events to ready
#   python3 build_template.py --launch WS_ID      # + launch a test VM into that workspace
#   VERSION=0.1.1 python3 build_template.py --build    # bump the patch each rebuild
#
# NO SECRETS are baked. The template declares the secrets a user brings; the
# on_resume hook bridges any that Orgo injects into /root/.env, and the
# first-boot onboarding accepts a pasted am_ key. build-recipe.md §4: we do
# NOT use env:{secret}/files:secret:// (they crash the build at compile), so
# the secrets block is declarative only.
import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL = ssl.create_default_context()
    try:
        _SSL.load_default_certs()
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = os.path.join(HERE, "files")
NAMESPACE = "default"
NAME = "agentmail-agent"
VERSION = os.environ.get("VERSION", "0.2.0")
AGENTMAIL_SDK = "agentmail==0.5.6"   # exact pin — an unpinned SDK is a rebuild time-bomb

# Keyed-golden knobs (catalog convention: @X.Y.(Z+1) tagged [PERSONAL]).
# The repo stays key-less: pass these ONLY as env vars at build time.
#   AGENTMAIL_BAKE_KEY   — am_… key baked into ~/.hermes/.env in the golden;
#                          onboarding step 2 auto-skips and on_resume creates
#                          the inbox at launch with zero touches.
#   AGENTMAIL_BAKE_MODEL — override the model everywhere (config default for
#                          the listener's `hermes -z` + AGENTMAIL_AGENT_MODEL
#                          for the cron seed), e.g. tencent/hy3:free for a
#                          Nous account without credits.
BAKE_KEY = os.environ.get("AGENTMAIL_BAKE_KEY", "").strip()
BAKE_MODEL = os.environ.get("AGENTMAIL_BAKE_MODEL", "").strip()
API_BASE = os.environ.get("ORGO_API_BASE", "https://www.orgo.ai/api")
API_KEY = os.environ.get("ORGO_API_KEY", "")


def rd(rel):
    with open(os.path.join(FILES, rel), "r", encoding="utf-8") as fh:
        return fh.read()


def rd_b64(rel):
    with open(os.path.join(FILES, rel), "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


def F(to, body, mode="0644", when="build"):
    return {"to": to, "inline": body, "mode": mode, "when": when}


# --------------------------------------------------------------------------
# files[] — staged under /opt/agentmail-agent/stage (copied into place post-
# install so the Hermes installer can never clobber them) + scripts/icons
# --------------------------------------------------------------------------
STAGE = "/opt/agentmail-agent/stage"

_config_yaml = rd("config.yaml")
_hermes_env = rd("hermes.env")
if BAKE_MODEL:
    _config_yaml = _config_yaml.replace("default: openai/gpt-5.5",
                                        f"default: {BAKE_MODEL}", 1)
    _hermes_env += f"\nAGENTMAIL_AGENT_MODEL={BAKE_MODEL}\n"
if BAKE_KEY:
    _hermes_env += f"AGENTMAIL_API_KEY={BAKE_KEY}\n"

files = [
    # --- staged Hermes config / persona / env / skill ---
    F(f"{STAGE}/hermes/config.yaml", _config_yaml, "0600"),
    F(f"{STAGE}/hermes/SOUL.md", rd("SOUL.md"), "0644"),
    F(f"{STAGE}/hermes/env", _hermes_env, "0600"),
    F(f"{STAGE}/hermes/skills/email/agentmail-inbox/SKILL.md",
      rd("skills/agentmail-inbox/SKILL.md"), "0644"),
    F(f"{STAGE}/hermes/skills/email/agentmail-inbox/references/agentmail-api.md",
      rd("skills/agentmail-inbox/references/agentmail-api.md"), "0644"),
    # --- wallpaper (binary → base64, decoded in the install step) ---
    F("/opt/agentmail-agent/wallpaper.b64", rd_b64("wallpaper.jpg"), "0644"),
    # --- executables (installer never touches /usr/local/bin) ---
    F("/usr/local/bin/hermes-gateway-run.sh", rd("gateway-run.sh"), "0755"),
    F("/usr/local/bin/agentmail-onboard.sh", rd("onboard.sh"), "0755"),
    F("/usr/local/bin/agentmail-onboard-launch.sh", rd("onboard-launch.sh"), "0755"),
    F("/usr/local/bin/agentmail-bootstrap-inbox.py", rd("bootstrap-inbox.py"), "0755"),
    F("/usr/local/bin/agentmail-seed-inbox-cron.py", rd("seed-inbox-cron.py"), "0755"),
    F("/usr/local/bin/agentmail-inbox-helper.py", rd("inbox-helper.py"), "0755"),
    F("/usr/local/bin/agentmail_inbox_core.py", rd("inbox_core.py"), "0644"),
    F("/usr/local/bin/agentmail-inbox-listener.py", rd("inbox-listener.py"), "0755"),
    F("/usr/local/bin/agentmail-listener-run.sh", rd("listener-run.sh"), "0755"),
    F("/usr/local/bin/agentmail-sync-mcp.py", rd("sync-mcp.py"), "0755"),
    F("/usr/local/bin/agentmail-watchdog.sh", rd("watchdog.sh"), "0755"),
    # --- desktop icon ---
    F("/root/Desktop/AgentMailSetup.desktop", rd("AgentMailSetup.desktop"), "0755"),
]

# --------------------------------------------------------------------------
# apps[].install — the one build-time script (runs as root, after files staged)
# --------------------------------------------------------------------------
INSTALL = f"""
set -e
export DEBIAN_FRONTEND=noninteractive
export HOME=/root
export HERMES_HOME=/root/.hermes
export PATH=/usr/local/bin:/root/.hermes/bin:/root/.hermes/node/bin:/root/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH

# 1) Hermes Agent — non-interactive, no wizard, no Playwright.
#    git/ripgrep/ffmpeg are already apt-installed (build.apt), so the installer
#    takes no apt path here; Node is auto-provisioned if the base lacks it.
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --non-interactive --skip-setup --skip-browser
hash -r || true

# 2) Place staged Hermes config/persona/env/skill AFTER the install so our
#    files always win over anything the installer wrote.
mkdir -p /root/.hermes/skills /root/.hermes/memories /root/.hermes/state \\
         /var/log/orgo /var/lib/orgo
cp -f  {STAGE}/hermes/config.yaml /root/.hermes/config.yaml
cp -f  {STAGE}/hermes/SOUL.md     /root/.hermes/SOUL.md
cp -f  {STAGE}/hermes/env         /root/.hermes/.env
cp -rf {STAGE}/hermes/skills/.    /root/.hermes/skills/
chmod 600 /root/.hermes/config.yaml /root/.hermes/.env

# 3) Desktop wallpaper (decode the baked base64).
mkdir -p /usr/share/backgrounds
base64 -d /opt/agentmail-agent/wallpaper.b64 > /usr/share/backgrounds/wallpaper.jpg

# 4) Dedicated venv for the AgentMail SDK (WebSocket listener only — the
#    reply/ledger path stays stdlib REST). The base image has no pip, but the
#    Hermes installer ships uv; `uv venv` needs no python3-venv/ensurepip and
#    the SDK deps are pure wheels — zero apt, zero libssl/supervisord risk.
UV="$(command -v uv || echo /root/.hermes/bin/uv)"
"$UV" venv /opt/agentmail-agent/venv
for i in 1 2 3; do
  "$UV" pip install --python /opt/agentmail-agent/venv/bin/python "{AGENTMAIL_SDK}" && break
  sleep 5
done
# Import self-test: a broken SDK install must fail the build LOUDLY here,
# not ship a silently dead listener.
/opt/agentmail-agent/venv/bin/python -c "from agentmail import AgentMail, Subscribe, MessageReceivedEvent" \\
  || {{ echo "agentmail SDK import self-test FAILED"; exit 1; }}

echo "agentmail-agent install complete"
""".strip()

# --------------------------------------------------------------------------
# hooks
# --------------------------------------------------------------------------
ON_FIRST_BOOT = """
mkdir -p /var/lib/orgo /var/log/orgo /root/.hermes/memories /root/.hermes/state
[ -f /var/lib/orgo/agentmail-agent.stamp ] || echo "agentmail-agent first boot $(date -Iseconds)" > /var/lib/orgo/agentmail-agent.stamp
# Lean + supervisord-safe (this also runs during the build). No hermes calls.
""".strip()

# on_resume: bridge vault secrets -> ~/.hermes/.env, bootstrap the inbox, seed
# the poller cron, restart the gateway. Everything is idempotent + no-ops until
# a key exists, so a key dropped in the vault makes launch fully zero-touch.
ON_RESUME = """
# Fix any fresh-VM clock skew before the agent touches the network (SSL).
date -s "$(curl -sI http://www.google.com | awk 'tolower($1)=="date:"{sub($1 FS,"");print}')" 2>/dev/null || true

set -a; [ -f /root/.env ] && . /root/.env; [ -f /root/.hermes/.env ] && . /root/.hermes/.env; set +a
mkdir -p /root/.hermes /root/.hermes/state
E=/root/.hermes/.env; touch "$E"

# Bridge any Orgo-vault-injected secrets into the file Hermes actually reads.
# Idempotent strip-then-append; vault UPPER_SNAKE names == the keys config wants.
for K in AGENTMAIL_API_KEY COMPOSIO_CONSUMER_KEY; do
  V="$(printenv "$K" 2>/dev/null || true)"
  [ -z "$V" ] && continue
  grep -vE "^${K}=" "$E" > "$E.tmp" 2>/dev/null || true
  mv "$E.tmp" "$E"
  echo "${K}=${V}" >> "$E"
done
chmod 600 "$E"

# Create the agent's inbox the moment a key exists (idempotent, no-op without
# one), re-source the env it pinned, then seed the 1-minute inbox responder.
set -a; . "$E"; set +a
python3 /usr/local/bin/agentmail-bootstrap-inbox.py || true
set -a; . "$E"; set +a
python3 /usr/local/bin/agentmail-seed-inbox-cron.py || true

# Initialize the dedupe ledger immediately so mail arriving before the first
# poller tick is treated as new, never absorbed as backlog.
[ -n "$AGENTMAIL_INBOX_ID" ] && python3 /usr/local/bin/agentmail-inbox-helper.py poll >/dev/null 2>&1 || true

# Enable/disable the agentmail+composio MCP servers to match key presence
# (a key-less enabled server costs every cron run ~8s of 401 retries).
python3 /usr/local/bin/agentmail-sync-mcp.py || true

# Restart the gateway so it re-reads .env + config (Hermes has no hot reload).
supervisorctl restart hermes-gateway 2>/dev/null || true
# Kick the WebSocket listener too so a vault-injected key is picked up
# immediately (it would otherwise wait for its 10s idle poll).
supervisorctl restart agentmail-listener 2>/dev/null || true
""".strip()

# on_every_boot: reassert the branded wallpaper (monitor-name-independent loop).
ON_EVERY_BOOT = """
export DISPLAY="${DISPLAY:-:99}"
WP=/usr/share/backgrounds/wallpaper.jpg
[ -f "$WP" ] || exit 0
for p in $(xfconf-query -c xfce4-desktop -l 2>/dev/null | grep -E 'last-image|image-path'); do
  xfconf-query -c xfce4-desktop -p "$p" -s "$WP" 2>/dev/null || true
done
xfconf-query -c xfce4-desktop -p /backdrop/screen0/monitor0/image-path -s "$WP" --create -t string 2>/dev/null || true
""".strip()

# --------------------------------------------------------------------------
# The template document
# --------------------------------------------------------------------------
template = {
    "api_version": "orgo.ai/v1",
    "template": {
        "name": NAME,
        "version": VERSION,
        "description": ("AgentMail Agent — a persistent AI agent whose front door is its "
                        "own email inbox. One key (am_…) and it's live: the inbox is "
                        "created automatically and a live WebSocket watches it, so every "
                        "inbound email gets read, worked on, and answered in seconds — "
                        "no public endpoint, no webhook setup (a 5-minute poller covers "
                        "outages). Hermes runtime, gpt-5.5 via Nous, full computer "
                        "included."
                        + (" [PERSONAL — AgentMail key baked; onboarding is the "
                           "Nous sign-in only.]" if BAKE_KEY else "")),
        "publisher": "orgo",
        "license": "MIT",
        "homepage": "https://agentmail.to",
        "source": "https://github.com/agentmail-to/agentmail-examples",
    },
    # Modest, matches the proven-green build shape (no explicit os/gpu).
    "hardware": {
        "cpu": 2,
        "ram_gb": 4,
        "disk_gb": 20,
        "resolution": "1280x720x24",
    },
    # Declarative only (names shown in the launch UI + vault). NOT referenced via
    # {secret:} anywhere (that crashes the build); on_resume bridges whatever the
    # vault injects, and the onboarding accepts a pasted key too.
    "secrets": [
        {"name": "agentmail_api_key", "optional": True,
         "description": "AgentMail API key (am_…) — the ONE key this template needs. "
                        "Set it here and the agent's inbox + auto-responder come up with "
                        "zero touches; otherwise the first-boot setup asks you to paste it.",
         "example": "am_...", "docs_url": "https://console.agentmail.to"},
        {"name": "composio_consumer_key", "optional": True,
         "description": "Optional Composio consumer key (ck_…) — sent as the "
                        "x-consumer-api-key header to the Composio MCP "
                        "(connect.composio.dev/mcp). Unlocks your connected apps "
                        "(Gmail, Slack, Calendar, Notion, …) as tools.",
         "example": "ck_...", "docs_url": "https://app.composio.dev"},
    ],
    "build": {
        # MINIMAL + build-safe: no ca-certificates/openssl/curl (they'd upgrade
        # libssl3t64 and kill build-time supervisord). Pre-installing ripgrep +
        # ffmpeg makes the Hermes installer skip its own apt path entirely.
        "apt": ["git", "xz-utils", "python3-yaml", "ripgrep", "ffmpeg"],
    },
    "files": files,
    "apps": [
        {
            "name": "hermes-gateway",
            "title": "Hermes Gateway",
            "description": ("Hermes gateway daemon — the AgentMail MCP connection and the "
                            "cron scheduler running the 5-minute fallback poller behind "
                            "the WebSocket inbox listener."),
            "install": INSTALL,
            "services": [
                {
                    "name": "hermes-gateway",
                    "title": "Hermes gateway",
                    "run": "/usr/local/bin/hermes-gateway-run.sh",
                    "user": "root",
                    "restart": "always",
                },
                {
                    "name": "agentmail-watchdog",
                    "title": "Responder watchdog",
                    "run": "/usr/local/bin/agentmail-watchdog.sh",
                    "user": "root",
                    "restart": "always",
                },
                {
                    "name": "agentmail-listener",
                    "title": "AgentMail WebSocket listener",
                    "run": "/usr/local/bin/agentmail-listener-run.sh",
                    "user": "root",
                    "restart": "always",
                },
            ],
            "autostart": [
                {"run": "/usr/local/bin/agentmail-onboard-launch.sh", "delay": 10},
            ],
        }
    ],
    "hooks": {
        "on_first_boot": ON_FIRST_BOOT,
        "on_resume": ON_RESUME,
        "on_every_boot": ON_EVERY_BOOT,
    },
    "terminal": [
        {
            "name": "hermes",
            "title": "Hermes",
            "description": "Host shell for the email agent (hermes auth, logs, config).",
            "cwd": "/root",
        }
    ],
}


# --------------------------------------------------------------------------
# validate / publish / build / launch
# --------------------------------------------------------------------------
def local_validate():
    try:
        import jsonschema
    except ImportError:
        print("! jsonschema not installed — skipping local schema check "
              "(pip install jsonschema to enable)")
        return
    # Prefer a bundled/relative schema; otherwise fetch Orgo's public one so
    # this works from a standalone checkout too.
    schema = None
    for p in (os.path.join(HERE, "template-schema.json"),
              os.path.join(HERE, "..", "..", "docs", "orgo", "template-schema.json")):
        if os.path.exists(p):
            with open(p) as fh:
                schema = json.load(fh)
            break
    if schema is None:
        try:
            req = urllib.request.Request(f"{API_BASE}/template-schema")
            with urllib.request.urlopen(req, context=_SSL, timeout=20) as r:
                schema = json.loads(r.read().decode())
        except Exception as e:
            print(f"! could not load schema ({e}); skipping local check "
                  f"(use --remote-validate instead)")
            return
    jsonschema.validate(template, schema)
    print("✓ local jsonschema validation PASSED")


def _req(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {API_KEY}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, context=_SSL) as r:
            raw = r.read().decode()
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def remote_validate():
    # The validate endpoint wants a wrapped body ({"template": doc}) as of
    # Jul 2026 — raw-doc posts now 400 with "template required".
    st, body = _req("POST", f"{API_BASE}/templates/validate", {"template": template})
    print(f"POST /templates/validate → {st}: {body[:400]}")
    return st < 300


def publish():
    envelope = {"namespace": NAMESPACE, "name": NAME, "version": VERSION, "template": template}
    st, body = _req("POST", f"{API_BASE}/templates", envelope)
    print(f"POST /templates → {st}: {body[:400]}")
    return st < 300 or st == 409


def build_and_stream():
    st, body = _req("POST", f"{API_BASE}/templates/{NAMESPACE}/{NAME}/{VERSION}/build")
    print(f"POST …/build → {st}: {body[:200]}")
    url = f"{API_BASE}/templates/{NAMESPACE}/{NAME}/{VERSION}/build/events"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {API_KEY}"})
    print("streaming build events (SSE):")
    ok = False
    with urllib.request.urlopen(req, timeout=600, context=_SSL) as r:
        for line in r:
            s = line.decode(errors="replace").rstrip()
            if s:
                print("  " + s)
            if '"phase":"ready"' in s or ("ready" in s and "golden" in s):
                ok = True
            if "build failed" in s or '"failed"' in s:
                ok = False
    return ok


def launch(ws_id):
    body = {"workspace_id": ws_id, "name": "agentmail-agent-test",
            "template_ref": f"{NAMESPACE}/{NAME}@{VERSION}", "ram": 4, "cpu": 2}
    st, resp = _req("POST", f"{API_BASE}/computers", body)
    print(f"POST /computers → {st}: {resp[:400]}")


def main():
    args = sys.argv[1:]
    # Always assemble + dump the resolved, inspectable artifact.
    out = os.path.join(HERE, "agentmail-agent.resolved.json")
    with open(out, "w") as fh:
        json.dump(template, fh, indent=2)
    n_files = len(template["files"])
    approx = sum(len(f.get("inline", "")) for f in template["files"])
    print(f"assembled template v{VERSION}: {n_files} files, ~{approx//1024}KB inline "
          f"→ {os.path.relpath(out, HERE)}")
    local_validate()
    if "--remote-validate" in args:
        remote_validate()
    if "--publish" in args or "--build" in args:
        if not API_KEY:
            sys.exit("ORGO_API_KEY not set")
        if not publish():
            sys.exit("publish failed")
    if "--build" in args:
        if not build_and_stream():
            sys.exit("build did not reach ready")
        print("✓ build ready")
    if "--launch" in args:
        i = args.index("--launch")
        launch(args[i + 1])


if __name__ == "__main__":
    main()
