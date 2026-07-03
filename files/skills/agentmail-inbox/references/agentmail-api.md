# AgentMail API notes (verified against openapi.json, Jul 2026)

- Base: `https://api.agentmail.to/v0` (EU: `https://api.agentmail.eu/v0`)
- Auth: `Authorization: Bearer <am_… key>` on every request
- Docs: `https://docs.agentmail.to/llms.txt` (index) /
  `https://docs.agentmail.to/llms-full.txt` (full) /
  `openapi.json` / `asyncapi.json` (WebSockets)
- Hosted MCP: `https://mcp.agentmail.to/mcp` (`x-api-key` header)

## Inboxes

- `GET  /inboxes` — list (response key: `inboxes`)
- `POST /inboxes` — create; body fields all optional:
  `username` (random if omitted), `domain` (must be verified; default
  `agentmail.to`), `display_name`, `client_id` (**pass one — makes creation
  idempotent**), `metadata`
- `GET/PATCH/DELETE /inboxes/{inbox_id}` — the `inbox_id` IS the email
  address; URL-encode it in paths (`@` → `%40`)

## Messages

- `GET  /inboxes/{inbox_id}/messages` — params: `limit`, `page_token`,
  `labels` (e.g. `received`, `sent`, `unread`), `before`/`after`, `from`/`to`/
  `subject` (substring filters), `ascending`. Response: `{count, messages[]}`
  ordered by timestamp desc; items carry `message_id`, `thread_id`, `labels`,
  `from`, `to`, `subject`, `preview`, `timestamp`.
- `GET  /inboxes/{inbox_id}/messages/{message_id}` — full message incl.
  `text`, `html`, `extracted_text` (new content only — great for replies),
  `attachments`.
- `POST /inboxes/{inbox_id}/messages/send` — `{to, cc, bcc, subject, text,
  html, labels, attachments}`
- `POST /inboxes/{inbox_id}/messages/{message_id}/reply` — `{text, html,
  reply_all, to, cc, bcc, attachments}`; threads automatically.
- `…/forward`, `…/reply-all`, `…/draft-reply` also exist.
- `GET /inboxes/{inbox_id}/messages/search?…` and `/threads/search` for search.

## Threads / drafts / labels

- `GET /inboxes/{inbox_id}/threads`, `GET /threads/{thread_id}`
- Drafts: `POST /inboxes/{inbox_id}/drafts` → `POST …/drafts/{id}/send`
- Labels ride on messages (`PATCH …/messages/{id}` with `{add_labels,
  remove_labels}` style body — check schema before use)

## Push (for later — the poller cron covers v1)

- Webhooks: `POST /webhooks` or `POST /inboxes/{inbox_id}/webhooks`
  `{url, event_types}` — needs a public HTTPS endpoint.
- WebSockets: see `asyncapi.json` — subscribe to message events over WSS,
  no public endpoint needed. Candidate v2 upgrade for real-time replies.

## Other surfaces

- Custom domains: `POST /domains` → DNS zone file → `POST /domains/{id}/verify`
- Allow/block lists: `/lists/{direction}/{type}`
- Metrics/usage: `/metrics/events`, `/metrics/usage`
- Pods (multi-tenant isolation), per-inbox API keys, `GET /auth/me` (whoami)
