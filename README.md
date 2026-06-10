# Aide — a personal assistant for macOS

Aide is a Siri-style assistant built on the **Claude Agent SDK** (the same engine behind
Claude Code). It floats on your desktop, sees what you're working on, remembers what
matters across sessions, manages your tasks, connects to your email and calendar, and
proactively briefs you — all running locally against your own Claude account.

Summon it anywhere with **⌥Space**, ask in plain language, expand into a full chat app when
you want room to work.

```
you> remind me to email Sam about the contract tomorrow at 3
  ⚙ Adding a task
Done — task #4, due tomorrow 3pm. I'll surface it in tomorrow's briefing too.
```

---

## Quick start

Requires Python 3.10+ and macOS for the GUI (the CLI runs anywhere).

```bash
git clone <this repo> && cd legendary-sniffle
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[gui]"          # or ".[dev]" for just the CLI + tests

cp .env.example .env             # then pick ONE auth option below
assistant-gui                    # launch the desktop app  (or: assistant, for the terminal)
```

**Auth — pick one** (edit `.env`):

- **Claude Pro/Max subscription** (no API credits): install Claude Code
  (`curl -fsSL https://claude.ai/install.sh | bash`), run `claude setup-token`, and paste
  the token as `CLAUDE_CODE_OAUTH_TOKEN=...`. A plain `claude` login on the same machine
  also works with no `.env` entry.
- **API key**: from [console.anthropic.com](https://console.anthropic.com), set
  `ANTHROPIC_API_KEY=sk-ant-...`.

Run it at login + schedule the daily/evening jobs (launchd):

```bash
./scripts/install_macos.sh             # GUI at login, briefing 07:30, insights 21:30
./scripts/install_macos.sh 08:00 22:00 # custom times
```

---

## What it can do

| Capability | How it works |
|---|---|
| **Desktop app** | A floating "Ask Aide" pill (⌥Space to summon, Esc to dismiss). The expand button opens a full sidebar app — New Chat, Search, Routines, Favorites, and saved conversations you can reopen and continue. |
| **On-screen awareness** | The screen button (or "help me with this") captures your screen so Aide sees what you see. Every message also carries a one-line note of what app you're in. |
| **Ambient recall** | A background observer continuously remembers what's on your screen — OCR'd and full-text searchable for ~30 days, 100% on-device. Skips private browsing and password managers. Pause from the panel, or say "forget the last hour". |
| **Personal context** | Searches your files (Spotlight), Calendar, Reminders, Notes, Mail, and Contacts; drafts email and creates events (always confirming first). |
| **Memory & tasks** | Remembers preferences, people, and projects across sessions; tracks to-dos and deadlines. |
| **Email / calendar / anything** | Any [MCP server](https://modelcontextprotocol.io) plugs in via `mcp_servers.json`. |
| **Proactive briefings** | A morning briefing and an evening digest that distills the day's activity into long-term memory. |

---

## Code map

Everything lives in the `assistant/` package. Start with `agent.py` (the brain) and
`gui.py` (the app).

| File | Role |
|---|---|
| `agent.py` | Assembles the agent: system prompt (persona + macOS playbook), model/effort, and the tool set. **The behavior lives here.** |
| `gui.py` | The macOS desktop app — a pywebview window and the Python↔JS bridge that drives the agent. |
| `static/chat.html` | The entire front-end: the pill, the sidebar app, streaming, animations (one self-contained file). |
| `cli.py` | The same assistant in your terminal. |
| `config.py` | Paths, `.env` loading, auth detection, and MCP-server config — the one place settings live. |
| `tools.py` | In-process tools the agent calls: tasks + memory (`add_task`, `remember`, …). |
| `mac_tools.py` | macOS-only tools: screen capture and ambient-recall search/pause/forget. |
| `observer.py` | The background recall observer (window timeline + OCR screen memory). |
| `history.py` | Saved conversations (SQLite + full-text search) behind the sidebar. |
| `memory.py` | Long-term markdown memory (profile, preferences, projects, journal). |
| `tasks.py` | The task/reminder store (SQLite). |
| `briefing.py` / `insights.py` | The morning briefing and evening distillation jobs… |
| `scheduled.py` | …which share one runner here. |
| `notify.py` · `util.py` | macOS notifications · tiny shared helpers. |

All state (memory, tasks, conversations, recall, briefings) lives under `~/.assistant/`
— the repo stays stateless, so cloning it anywhere brings your assistant's *code*, and
copying that one folder brings everything it has *learned*.

---

## Connecting email, calendar, and more

Connectors are standard MCP servers:

```bash
cp mcp_servers.example.json ~/.assistant/mcp_servers.json   # then enable what you want
```

- **Gmail / Google Calendar** — the example uses community MCP servers; first run walks you
  through Google OAuth. Put any client ID/secret in `.env`; they're injected via `${VAR}`.
- **Anything else** — Slack, Notion, Todoist, smart home… add it to the same file and its
  tools are picked up on the next launch.

Aide always confirms before sending email or changing calendar events.

---

## Configuration

All optional, set in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `ASSISTANT_NAME` | `Aide` | What it calls itself |
| `ASSISTANT_MODEL` | `claude-opus-4-8` | Any Claude model ID |
| `ASSISTANT_EFFORT` | `high` | Reasoning depth (`low`→`xhigh`); higher = smarter, more tokens |
| `ASSISTANT_HOME` | `~/.assistant` | Where all state lives |
| `ASSISTANT_FULL_ACCESS` | `1` | Act across your home dir without prompts; `0` to sandbox |
| `ASSISTANT_ALLOWED_DIRS` | *(empty)* | Extra dirs when sandboxed, e.g. `~/Documents:~/Projects` |
| `ASSISTANT_RECALL` | `1` | Ambient recall on; `0` to disable |
| `ASSISTANT_RECALL_DAYS` | `30` | How long screen memory is kept |
| `ASSISTANT_RECALL_EXCLUDE` | *(empty)* | Extra apps/titles to never record, comma-separated |

Persona and tools live in `assistant/agent.py`. Add an ability by writing a `@tool`
function in `assistant/tools.py` — it's exposed to the agent automatically.

---

## Manual commands

```bash
assistant            # terminal chat
assistant-gui        # desktop app
assistant-briefing   # generate today's briefing now
assistant-insights   # run the evening distillation now
pytest               # tests (no API key needed)
```

To remove the launchd agents:
`launchctl unload ~/Library/LaunchAgents/com.aide.*.plist && rm ~/Library/LaunchAgents/com.aide.*.plist`

---

## Privacy & security

- Ambient recall and all memory are **local** — nothing is uploaded; recall skips private
  browsing and password managers and is fully eraseable ("forget…").
- Your API key and `mcp_servers.json` are gitignored — secrets never get committed.
- `ASSISTANT_FULL_ACCESS=1` lets Aide act across your home directory; set it to `0` to
  restrict it to `~/.assistant` plus `ASSISTANT_ALLOWED_DIRS`. Outward or destructive
  actions (sending mail, deleting files) always ask first.
