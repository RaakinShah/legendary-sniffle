#!/usr/bin/env bash
# macOS integration: start the GUI at login + run the daily briefing via launchd.
# Usage: ./scripts/install_macos.sh [briefing-time HH:MM]   (default 07:30)
set -euo pipefail
[[ "$(uname)" == "Darwin" ]] || { echo "macOS only." >&2; exit 1; }

TIME="${1:-07:30}"; HOUR="${TIME%%:*}"; MIN="${TIME##*:}"
GUI_BIN="$(command -v assistant-gui || true)"
BRIEF_BIN="$(command -v assistant-briefing || true)"
[[ -n "$GUI_BIN" && -n "$BRIEF_BIN" ]] || { echo "Run: pip install -e '.[gui]' first" >&2; exit 1; }

AGENTS="$HOME/Library/LaunchAgents"; mkdir -p "$AGENTS"
LOG="${ASSISTANT_HOME:-$HOME/.assistant}"; mkdir -p "$LOG"

write_plist() { # $1=label $2=program $3=extra-keys
cat > "$AGENTS/$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$1</string>
  <key>ProgramArguments</key><array><string>$2</string></array>
  <key>EnvironmentVariables</key><dict>
    <key>ANTHROPIC_API_KEY</key><string>${ANTHROPIC_API_KEY:-}</string>
    <key>CLAUDE_CODE_OAUTH_TOKEN</key><string>${CLAUDE_CODE_OAUTH_TOKEN:-}</string>
  </dict>
  <key>StandardOutPath</key><string>$LOG/$1.log</string>
  <key>StandardErrorPath</key><string>$LOG/$1.log</string>
  $3
</dict></plist>
EOF
launchctl unload "$AGENTS/$1.plist" 2>/dev/null || true
launchctl load "$AGENTS/$1.plist"
}

write_plist "com.aide.gui" "$GUI_BIN" "<key>RunAtLoad</key><true/>"
write_plist "com.aide.briefing" "$BRIEF_BIN" \
  "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>"

echo "Installed:"
echo "  com.aide.gui      — chat window opens at login"
echo "  com.aide.briefing — daily briefing at $TIME (notification when ready)"
