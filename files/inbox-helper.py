#!/usr/bin/env python3
# ==========================================================================
# AgentMail Agent — deterministic inbox helper (the responder's hands)
# ==========================================================================
# Thin CLI over agentmail_inbox_core (all state + guards live there, shared
# with the WebSocket listener since 0.2.0). The LLM never re-implements
# polling/ledger code (that's how ledgers get corrupted): the model only
# decides WHAT to reply; this decides what is new and performs the sends.
#
#   agentmail-inbox-helper poll [--cron]
#       → JSON to stdout:
#         {"initialized": N}                       first run / ledger reset
#         {"new": [{message_id, thread_id, from, subject, timestamp, text}]}
#         {"new": []}                              nothing to do
#         {"new": [], "skipped": "listener-active"}   (--cron only) the
#             WebSocket listener heartbeat is fresh — no API call was made
#   agentmail-inbox-helper reply <message_id>      reply text on stdin
#       → sends the reply; on 2xx records the id in the ledger
#   agentmail-inbox-helper mark <message_id>       record WITHOUT replying
#       → for spam / bounces / stale mail
#
# Reads AGENTMAIL_API_KEY + AGENTMAIL_INBOX_ID from the environment, falling
# back to parsing /root/.hermes/.env directly (cron contexts don't source it).
import json
import sys
import urllib.error

sys.path.insert(0, "/usr/local/bin")
import agentmail_inbox_core as core  # noqa: E402


def die(msg):
    print(json.dumps({"error": msg}))
    sys.exit(1)


def main():
    if not core.key():
        die("AGENTMAIL_API_KEY not set")
    if not core.inbox():
        die("AGENTMAIL_INBOX_ID not set")
    args = sys.argv[1:]
    if args[:1] == ["poll"]:
        # The 5-minute cron is a fallback since 0.2.0: while the WebSocket
        # listener is alive (fresh heartbeat) the cron tick costs nothing.
        if "--cron" in args and core.heartbeat_fresh():
            print(json.dumps({"new": [], "skipped": "listener-active"}))
            return
        print(json.dumps(core.poll(), indent=1))
    elif args[:1] == ["reply"] and len(args) == 2:
        text = sys.stdin.read().strip()
        if not text:
            die("empty reply text on stdin")
        try:
            core.reply(args[1], text)
        except urllib.error.HTTPError as e:
            die(f"reply failed: HTTP {e.code}")
        print(json.dumps({"replied": args[1]}))
    elif args[:1] == ["mark"] and len(args) == 2:
        core.mark(args[1])
        print(json.dumps({"marked": args[1]}))
    else:
        die("usage: agentmail-inbox-helper poll [--cron] | reply <message_id> | mark <message_id>")


if __name__ == "__main__":
    main()
