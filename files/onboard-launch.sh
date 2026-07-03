#!/bin/bash
# AgentMail Agent — autostart launcher for the first-boot onboarding.
# Opens the guided setup in a terminal on the desktop, unless already done.
export DISPLAY="${DISPLAY:-:99}"
STAMP=/var/lib/orgo/agentmail-agent-onboarded
[ -f "$STAMP" ] && exit 0
ONBOARD=/usr/local/bin/agentmail-onboard.sh
if command -v xfce4-terminal >/dev/null 2>&1; then
  exec xfce4-terminal --title="AgentMail Agent Setup" --geometry=100x32 --command="$ONBOARD"
elif command -v x-terminal-emulator >/dev/null 2>&1; then
  exec x-terminal-emulator -e "$ONBOARD"
elif command -v xterm >/dev/null 2>&1; then
  exec xterm -T "AgentMail Agent Setup" -geometry 100x32 -e "$ONBOARD"
fi
# No terminal emulator: run headless; prompts land in the log.
mkdir -p /var/log/orgo
exec "$ONBOARD" >/var/log/orgo/agentmail-onboard.log 2>&1
