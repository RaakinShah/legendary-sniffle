#!/usr/bin/env bash
# macOS integration via launchd: GUI at login, morning briefing, evening insights,
# weekly memory consolidation (Sundays 19:00), proactive watcher (every 20 min).
# Usage: ./scripts/install_macos.sh [briefing-time HH:MM] [insights-time HH:MM]
#        (defaults 07:30 and 21:30)
set -euo pipefail
[[ "$(uname)" == "Darwin" ]] || { echo "macOS only." >&2; exit 1; }

TIME="${1:-07:30}"; HOUR="${TIME%%:*}"; MIN="${TIME##*:}"
ITIME="${2:-21:30}"; IHOUR="${ITIME%%:*}"; IMIN="${ITIME##*:}"
GUI_BIN="$(command -v assistant-gui || true)"
BRIEF_BIN="$(command -v assistant-briefing || true)"
INSIGHTS_BIN="$(command -v assistant-insights || true)"
CONSOLIDATE_BIN="$(command -v assistant-consolidate || true)"
WATCH_BIN="$(command -v assistant-watch || true)"
[[ -n "$GUI_BIN" && -n "$BRIEF_BIN" && -n "$INSIGHTS_BIN" && -n "$CONSOLIDATE_BIN" && -n "$WATCH_BIN" ]] || { echo "Run: pip install -e '.[gui]' first" >&2; exit 1; }

AGENTS="$HOME/Library/LaunchAgents"; mkdir -p "$AGENTS"
LOG="${ASSISTANT_HOME:-$HOME/.assistant}"; mkdir -p "$LOG"

# XML-escape interpolated values (&, <, >) so a path or token containing XML
# metacharacters can't produce a malformed plist that launchctl silently rejects.
xesc() { local s="$1"; s="${s//&/&amp;}"; s="${s//</&lt;}"; s="${s//>/&gt;}"; printf '%s' "$s"; }

write_plist() { # $1=label $2=program $3=extra-keys (already-valid XML)
cat > "$AGENTS/$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$1</string>
  <key>ProgramArguments</key><array><string>$(xesc "$2")</string></array>
  <key>EnvironmentVariables</key><dict>
    <key>ANTHROPIC_API_KEY</key><string>$(xesc "${ANTHROPIC_API_KEY:-}")</string>
    <key>CLAUDE_CODE_OAUTH_TOKEN</key><string>$(xesc "${CLAUDE_CODE_OAUTH_TOKEN:-}")</string>
  </dict>
  <key>StandardOutPath</key><string>$(xesc "$LOG")/$1.log</string>
  <key>StandardErrorPath</key><string>$(xesc "$LOG")/$1.log</string>
  $3
</dict></plist>
EOF
launchctl unload "$AGENTS/$1.plist" 2>/dev/null || true
launchctl load "$AGENTS/$1.plist"
}

write_plist "com.aide.gui" "$GUI_BIN" "<key>RunAtLoad</key><true/>"
write_plist "com.aide.briefing" "$BRIEF_BIN" \
  "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>"
write_plist "com.aide.insights" "$INSIGHTS_BIN" \
  "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>$IHOUR</integer><key>Minute</key><integer>$IMIN</integer></dict>"
write_plist "com.aide.consolidate" "$CONSOLIDATE_BIN" \
  "<key>StartCalendarInterval</key><dict><key>Weekday</key><integer>0</integer><key>Hour</key><integer>19</integer><key>Minute</key><integer>0</integer></dict>"
write_plist "com.aide.watch" "$WATCH_BIN" \
  "<key>StartInterval</key><integer>1200</integer>"

echo "Installed:"
echo "  com.aide.gui      — assistant opens at login (⌥Space to summon)"
echo "  com.aide.briefing — morning briefing at $TIME (notification when ready)"
echo "  com.aide.insights — evening digest at $ITIME (distills the day into memory)"
echo "  com.aide.consolidate — weekly memory tidy-up (Sundays 19:00)"
echo "  com.aide.watch    - proactive check-in every 20 min (notifies only when something needs you)"
