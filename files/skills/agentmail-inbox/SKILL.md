---
name: agentmail-inbox
description: Operate this computer's own AgentMail inbox — send, reply, search, threads, labels, webhooks, custom domains. Use whenever the user (by email or chat) asks about the agent's email, sending mail, checking mail, or wiring email into a workflow.
---

# AgentMail Inbox

This computer owns a real email inbox, powered by [AgentMail](https://agentmail.to)
(email infrastructure built for agents — inboxes are API-first, no OAuth dance).

## Your identity

- Your address lives in `~/.hermes/.env` as `AGENTMAIL_INBOX`
  (and `AGENTMAIL_INBOX_ID`, which is the same address, used in API paths).
- Your API key is `AGENTMAIL_API_KEY` in the same file. **Never put the key in
  an email body or a reply.**
- A background cron job (`agentmail-inbox-auto-responder`, every 1 minute)
  already answers inbound mail. Don't double-reply to messages it has already
  handled — its ledger is `/root/.hermes/state/agentmail_inbox_processed.json`.

## Two ways to act

1. **AgentMail MCP** — the `agentmail` MCP server is configured in
   `~/.hermes/config.yaml` (hosted at `https://mcp.agentmail.to/mcp`). Once the
   key is present its tools cover inboxes, messages, threads, drafts, labels.
   Prefer MCP tools when they're available.
2. **REST** — `https://api.agentmail.to/v0` with header
   `Authorization: Bearer $AGENTMAIL_API_KEY`. Full endpoint notes in
   [references/agentmail-api.md](references/agentmail-api.md). Use
   `execute_code` + Python stdlib `urllib` for one-off calls.

## Common moves

- **Send**: `POST /v0/inboxes/{inbox_id}/messages/send` with
  `{"to": ["a@b.com"], "subject": "…", "text": "…"}`.
- **Reply in-thread**: `POST /v0/inboxes/{inbox_id}/messages/{message_id}/reply`
  with `{"text": "…"}` (add `"reply_all": true` to include everyone).
- **Check mail**: `GET /v0/inboxes/{inbox_id}/messages?limit=20&labels=received`;
  full body via `GET /v0/inboxes/{inbox_id}/messages/{message_id}`.
- **Search**: `GET /v0/inboxes/{inbox_id}/messages/search?…` or
  `/threads/search`.
- **New inbox** (e.g. a role address like sales@): `POST /v0/inboxes` with
  `{"username": "sales", "client_id": "…"}` — always pass a `client_id` so
  creation stays idempotent.

URL-encode the inbox id in paths — it's an email address (`@` → `%40`).

## Etiquette baked into this agent

- Reply in-thread; don't start new threads for answers.
- Do requested work BEFORE replying; the reply carries the result.
- Plain text (`"text"`) is the default; only use `"html"` when layout matters.
- Ignore spam/bounces; never auto-reply to them.
