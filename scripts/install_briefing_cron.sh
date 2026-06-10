#!/usr/bin/env bash
# Install a cron job that generates your daily briefing every morning.
# Usage: ./scripts/install_briefing_cron.sh [HH:MM]   (default 07:30)
set -euo pipefail

TIME="${1:-07:30}"
HOUR="${TIME%%:*}"
MIN="${TIME##*:}"

BRIEFING_BIN="$(command -v assistant-briefing || true)"
if [[ -z "$BRIEFING_BIN" ]]; then
  echo "assistant-briefing not on PATH — run 'pip install -e .' first" >&2
  exit 1
fi

LOG_DIR="${ASSISTANT_HOME:-$HOME/.assistant}"
mkdir -p "$LOG_DIR"
ENTRY="$MIN $HOUR * * * $BRIEFING_BIN >> $LOG_DIR/briefing.log 2>&1"

( crontab -l 2>/dev/null | grep -v assistant-briefing; echo "$ENTRY" ) | crontab -
echo "Installed: daily briefing at $TIME"
echo "  $ENTRY"
echo "Briefings land in $LOG_DIR/briefings/ (log: $LOG_DIR/briefing.log)"
