# AgentMail Agent — an AI agent you talk to by email

**Run with [Orgo](https://orgo.ai) · Powered by [AgentMail](https://agentmail.to)**

A persistent AI agent whose front door is its own email inbox, running on its
own cloud computer. Email it anything — a question, "summarize this", "look
this up" — and it reads the message, does the work (it has a real Linux
desktop: terminal, Chrome, files), and replies in-thread within about a
minute.

No webhooks to configure, no server to host, no infra. The inbox is created
for you at first boot, and a supervised 1-minute responder gives you
inbound → agent → reply behavior with zero public endpoints.

![AgentMail × Orgo](files/wallpaper.jpg)

## What you need

| | |
|---|---|
| **AgentMail API key** (`am_…`) | [console.agentmail.to](https://console.agentmail.to) — the one key this template needs |
| **Nous account** | free sign-in during setup powers the model (gpt-5.5) — no model API key |
| **Orgo account** | [orgo.ai](https://orgo.ai) — publishing templates needs a Scale plan |
| Composio key (`ck_…`), optional | [app.composio.dev](https://app.composio.dev) — gives the agent 1000+ app tools |

No secrets are baked anywhere in this repo or in the built template.

## Quick start

```bash
git clone https://github.com/nickvasilescu/agentmail-agent
cd agentmail-agent
pip install jsonschema pyyaml   # for local validation

# 1) assemble + validate
python3 build_template.py

# 2) publish + build on your Orgo account (streams build events to "ready")
export ORGO_API_KEY=sk_live_...
python3 build_template.py --build

# 3) launch a computer from it
curl -X POST https://www.orgo.ai/api/computers \
  -H "Authorization: Bearer $ORGO_API_KEY" -H "Content-Type: application/json" \
  -d '{"workspace_id":"<WS_ID>","name":"my-email-agent","template_ref":"default/agentmail-agent@0.1.3"}'
```

Open the computer's desktop and a guided **2-step setup** is already waiting:

1. **Connect Nous** — a device-code sign-in (open the link, enter the code).
2. **Paste your AgentMail key** — the setup creates your agent's inbox,
   seeds the responder, and prints your agent's email address:

```
📬  your-agent@agentmail.to
```

Email it. It replies in under a minute — work included.

> Tip: set `agentmail_api_key` in your Orgo workspace vault before launching
> and step 2 happens automatically.

## What's inside

- **Hermes runtime** ([hermes-agent](https://hermes-agent.nousresearch.com),
  gpt-5.5 via Nous OAuth), supervised and reboot-safe, dormant until setup.
- **AgentMail MCP** (`mcp.agentmail.to`) — full inbox/thread/draft/label
  tools for the agent, enabled the moment your key exists.
- **Idempotent inbox bootstrap** — created with a per-VM `client_id`; never
  adopts an inbox it didn't create.
- **Deterministic inbox responder** — a 1-minute cron job where a helper
  script does everything mechanical (poll, dedupe ledger, guard rails,
  sending) and the LLM only decides what to say. Guards: never answers
  backlog, never answers mail older than 48h, max 3 replies/tick,
  flood → re-initialize.
- **Self-healing** — a supervised watchdog detects the one known way the
  scheduler can wedge (a model call that never returns) and restarts the
  gateway; the gateway itself runs under an exclusive lock so duplicate
  service spawns can never fight.
- **Optional Composio MCP** — add `COMPOSIO_CONSUMER_KEY=ck_…` to
  `~/.hermes/.env` (or just email your agent the key and ask it to wire
  itself up).

## Repo layout

```
agentmail-agent.orgo.yaml   # the whole template as one readable document
build_template.py           # assemble → validate → publish → build → launch
make_wallpaper.py           # regenerates files/wallpaper.jpg (pure PIL)
files/                      # everything baked into the VM
├── config.yaml             #   Hermes config (model, MCP servers)
├── SOUL.md                 #   the agent's email-native persona
├── onboard.sh              #   the 2-step desktop setup
├── bootstrap-inbox.py      #   idempotent inbox creation
├── seed-inbox-cron.py      #   seeds the 1-minute responder job
├── inbox-helper.py         #   deterministic poll/reply/mark + ledger
├── sync-mcp.py             #   enables MCP servers only when keys exist
├── watchdog.sh             #   un-wedges a stuck scheduler
├── gateway-run.sh          #   supervised gateway wrapper (locked, gated)
└── skills/agentmail-inbox/ #   the agent's guide to its own inbox
```

`build_template.py` and `agentmail-agent.orgo.yaml` contain the same
template — the script is the source of truth (it inlines `files/` byte-for-
byte); the YAML is generated from it for easy reading.

## Battle-tested

Validated end-to-end on live VMs (July 2026): build → boot → onboard →
email in → agent does the work → in-thread reply in under 60 seconds, fully
autonomous. The guard rails above each exist because a real test broke
something first — details in the commit history.

## License

MIT
