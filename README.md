# Aide — your personal assistant

A deeply integrated personal assistant built on the **Claude Agent SDK** (the same engine
that powers Claude Code). It lives in your terminal, has real access to your computer
(files, shell, web), remembers what matters to you across sessions, manages your tasks
and reminders, connects to your email and calendar, and proactively briefs you every
morning.

```
you> remind me to email Sam about the contract tomorrow at 3
  [mcp__assistant__add_task]
Done — task #4, due 2026-06-11T15:00. I'll surface it in tomorrow's briefing too.
```

## How it works

| Piece | What it does |
|---|---|
| `assistant-gui` | Native macOS chat window (WKWebView — no Electron), auto-starts at login |
| `assistant` (CLI) | Same assistant in the terminal |
| Memory | Markdown files in `~/.assistant/memory/` the agent reads at startup and updates as it learns about you |
| Tasks | SQLite store in `~/.assistant/assistant.db`, managed via custom tools |
| Connectors | Any MCP server (Gmail, Google Calendar, Slack, ...) plugged in via `mcp_servers.json` |
| `assistant-briefing` | One-shot proactive run: checks tasks, calendar, email, and writes a daily brief — schedule it with cron |

All of your data lives under `~/.assistant/` (configurable via `ASSISTANT_HOME`), so the
repo itself stays stateless — clone it on any machine and your assistant code comes with you.

## Setup

Requires Python 3.10+.

```bash
git clone <this repo> && cd legendary-sniffle
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env   # then pick ONE auth option:

# A) Claude Pro/Max subscription (no API credits needed):
#    curl -fsSL https://claude.ai/install.sh | bash   # install Claude Code
#    claude setup-token                               # log in via browser
#    -> paste the token into .env as CLAUDE_CODE_OAUTH_TOKEN=...
#    (a plain `claude` login on the same machine also works, with no .env entry)

# B) API key: https://console.anthropic.com -> API Keys
#    -> paste into .env as ANTHROPIC_API_KEY=sk-ant-...

assistant          # start chatting
```

Run the tests (no API key needed): `pytest`

## macOS GUI

```bash
pip install -e ".[gui]"
assistant-gui                  # open the chat window
./scripts/install_macos.sh 07:30   # full integration via launchd:
```

The install script sets up two login agents: the chat window opens automatically at
login, and the daily briefing runs every morning and pops a macOS notification when
it's ready. Logs land in `~/.assistant/`. To remove:
`launchctl unload ~/Library/LaunchAgents/com.aide.*.plist && rm ~/Library/LaunchAgents/com.aide.*.plist`

## Connecting your life (email, calendar, anything)

Connectors are standard [MCP servers](https://modelcontextprotocol.io). Copy the template
and enable what you want:

```bash
cp mcp_servers.example.json ~/.assistant/mcp_servers.json
```

- **Gmail** — the example uses a community Gmail MCP server (`npx` needs Node installed).
  On first run it walks you through Google OAuth in your browser.
- **Google Calendar** — create OAuth credentials in Google Cloud Console, put the client
  ID/secret in your `.env`, and they're injected via `${VAR}` expansion.
- **Anything else** — add any MCP server (Slack, Notion, Todoist, your smart home...) to
  the same file; its tools are picked up automatically on the next start.

The assistant will always confirm with you before sending email or changing calendar events.

## Proactive daily briefing

```bash
assistant-briefing                      # run once, right now
./scripts/install_briefing_cron.sh 07:30   # or run automatically every morning
```

Each briefing checks what's due, scans your calendar/inbox (if connected), pulls relevant
memory, and saves a scannable brief to `~/.assistant/briefings/YYYY-MM-DD.md`.

## Customizing

Everything tunable lives in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `ASSISTANT_NAME` | `Aide` | What your assistant calls itself |
| `ASSISTANT_MODEL` | `claude-opus-4-8` | Any Claude model ID |
| `ASSISTANT_HOME` | `~/.assistant` | Where memory/tasks/briefings live |
| `ASSISTANT_ALLOWED_DIRS` | *(empty)* | Extra dirs it may touch, e.g. `~/Documents:~/Projects` |

Persona and behavior live in `assistant/agent.py` (`system_prompt`). Add new abilities by
writing a function in `assistant/tools.py` with the `@tool` decorator — it's automatically
exposed to the agent.

## Moving to your own computer later

1. Push this repo (already done if you're reading this on GitHub) and `git clone` it there.
2. Repeat **Setup** above (venv, `pip install -e .`, `.env` with your key).
3. If you want to carry over what it has learned, copy the data directory too:
   `scp -r old-machine:~/.assistant ~/.assistant` (or any file sync). Memory, tasks, and
   briefings all travel in that one folder.

## Security notes

- Your API key and `mcp_servers.json` are gitignored — never commit secrets.
- By default the assistant can only write inside `~/.assistant`; widen access deliberately
  with `ASSISTANT_ALLOWED_DIRS`.
- `Bash` is enabled for deep integration. If that's more power than you want, remove it
  from `BASE_TOOLS` in `assistant/agent.py`.
