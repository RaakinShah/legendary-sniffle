# Aide — a personal assistant for macOS

Aide is a Siri-style assistant that runs on **Claude Haiku** by default (via the Claude
Agent SDK and your Claude login) — accurate, fast, and light on your machine. When a turn
is genuinely hard it brings in a stronger brain: Haiku consults Sonnet mid-turn (Opus for
the hardest problems), and a turn that fails is retried once on the stronger model. It
floats on your desktop, sees what you're working on, remembers what matters across
sessions, manages your tasks, connects to your email and calendar, and proactively
briefs you. Prefer fully offline? Switch to a **local model** via
[Ollama](https://ollama.com) (`ASSISTANT_BACKEND=ollama`) — free and private, with a
**Haiku advisor** that steps in on the hard turns when Claude credentials are present.
Heads-up: small local models are noticeably more prone to making things up; the default
exists because of that.

Summon it anywhere with **⌥Space**, ask in plain language, expand into a full chat app when
you want room to work.

```
you> remind me to email Sam about the contract tomorrow at 3
  ⚙ Adding a task
Done — task #4, due tomorrow 3pm. I'll surface it in tomorrow's briefing too.
```

---

## Quick start

Requires Python 3.10+ and Claude credentials for the default backend
(macOS for the GUI; the CLI runs anywhere).

```bash
git clone <this repo> && cd legendary-sniffle
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[gui]"          # or ".[dev]" for just the CLI + tests

cp .env.example .env             # optional — a stored `claude` login already works
assistant-doctor                 # preflight: backend, deps, macOS permissions
assistant-gui                    # launch the desktop app  (or: assistant, for the terminal)
```

**Auth — pick one** (the default brain is `claude-haiku-4-5`):

- **Claude Pro/Max subscription** (no API credits): install Claude Code
  (`curl -fsSL https://claude.ai/install.sh | bash`), run `claude setup-token`, and paste
  the token as `CLAUDE_CODE_OAUTH_TOKEN=...` in `.env`. A plain `claude` login on the same
  machine also works with no `.env` entry at all.
- **API key**: from [console.anthropic.com](https://console.anthropic.com), set
  `ANTHROPIC_API_KEY=sk-ant-...`.

**Optional — go fully local** (free, offline, private; weaker and more prone to
making things up):

```bash
ollama pull llama3.1:8b              # ~4.9GB; keep Ollama running
echo 'ASSISTANT_BACKEND=ollama' >> .env
```

With Claude credentials still present, the local model gets a **Haiku advisor**: it can
consult it when stuck, and stalled turns auto-escalate to it.

Run it at login + schedule the daily and weekly jobs (launchd):

```bash
./scripts/install_macos.sh             # GUI at login, briefing 07:30, insights 21:30,
                                       # memory consolidation Sundays 19:00
./scripts/install_macos.sh 08:00 22:00 # custom briefing/insights times
```

---

## What it can do

| Capability | How it works |
|---|---|
| **Desktop app** | A floating "Ask Aide" pill (⌥Space to summon, Esc to dismiss). The expand button opens a full sidebar app — New Chat, Search, Routines, Favorites, and saved conversations you can reopen and continue. |
| **Hard-turn escalation** | Haiku answers every turn; when one is genuinely hard it calls the `think_harder` tool to consult Sonnet (or Opus for the very hardest), and a turn that errors is retried once on the escalation model. Same Claude credentials, no extra setup. |
| **On-screen awareness** | The screen button (or "help me with this") captures your screen so Aide sees what you see. Every message also carries a one-line note of what app you're in. |
| **Ambient recall** | A background observer remembers what's on your screen near-continuously: an OCR shot every ~10s, with unchanged screens deduped so a fast cadence costs CPU, not disk. Full-text searchable for ~30 days under a disk cap (oldest pruned first), 100% on-device. Skips private browsing, password managers, authenticators, and common banking/finance apps. Pause from the panel, or say "forget the last hour". |
| **Personal context** | Searches your files (Spotlight), Calendar, Reminders, Notes, Mail, and Contacts; drafts email and creates events (always confirming first). |
| **Memory & tasks** | Remembers preferences, people, and projects across sessions. When a fact changes it is revised in place (`update_memory`) or removed (`forget_fact`) instead of piling up contradictions. Tracks to-dos and deadlines. |
| **Email / calendar / anything** | One command sets up Gmail and Google Calendar (`python3 scripts/connect_google.py`); any other [MCP server](https://modelcontextprotocol.io) plugs in via `mcp_servers.json`. |
| **Proactive briefings** | A morning briefing and an evening digest that distills the day's activity into long-term memory. A weekly job (Sundays 19:00) consolidates memory itself: files loose facts, merges duplicates, drops what's stale. It runs on the escalation model and leaves a report in `~/.assistant/consolidation/`. |

---

## Code map

Everything lives in the `assistant/` package. Start with `agent.py` (the brain) and
`gui.py` (the app).

| File | Role |
|---|---|
| `agent.py` | Assembles the Claude side: system prompt (persona + macOS playbook), model/effort, and the MCP tool set. **The persona lives here.** |
| `engine.py` | The backend-agnostic engine: a normalized event stream (tokens, tool calls, done) over either the local Ollama model or the Claude SDK — plus the Haiku auto-rescue. |
| `ollama.py` | Async client for the local Ollama server (streaming `/api/chat` + tool calling). |
| `advisor.py` | The Haiku advisor — the stronger model the local one consults (`ask_advisor`) or is rescued by when stuck. |
| `toolkit.py` | The tool set for the local backend: tasks, memory, web, shell, screen, recall, and `ask_advisor`. |
| `gui.py` | The macOS desktop app — a pywebview window and the Python↔JS bridge that drives the engine. |
| `static/chat.html` | The entire front-end: the pill, the sidebar app, streaming, animations (one self-contained file). |
| `cli.py` | The same assistant in your terminal. |
| `config.py` | Paths, `.env` loading, backend/advisor settings, auth detection, and MCP-server config — the one place settings live. |
| `doctor.py` | `assistant-doctor` preflight — checks backend, advisor, connectors, optional deps, and macOS permissions. |
| `tools.py` | The Claude backend's in-process tools (tasks, memory, and `think_harder` escalation), the MCP analogue of `toolkit.py`. |
| `mac_tools.py` | macOS-only tools: screen capture and ambient-recall search/pause/forget. |
| `observer.py` | The background recall observer (window timeline + OCR screen memory). |
| `history.py` | Saved conversations (SQLite + full-text search) behind the sidebar. |
| `memory.py` | Long-term markdown memory (profile, preferences, projects, journal). |
| `tasks.py` | The task/reminder store (SQLite). |
| `briefing.py` / `insights.py` / `consolidate.py` | The morning briefing, evening distillation, and weekly memory-consolidation jobs… |
| `scheduled.py` | …which share one runner here. |
| `log.py` | The rotating file log (`~/.assistant/logs/aide.log`) shared by the app, the observer, and the scheduled jobs. |
| `notify.py` · `util.py` | macOS notifications · tiny shared helpers. |

All state (memory, tasks, conversations, recall, briefings) lives under `~/.assistant/`
— the repo stays stateless, so cloning it anywhere brings your assistant's *code*, and
copying that one folder brings everything it has *learned*.

---

## Connecting email, calendar, and more

Connectors are standard MCP servers.

**Gmail / Google Calendar** have a turnkey script:

```bash
python3 scripts/connect_google.py        # writes ~/.assistant/mcp_servers.json (both)
python3 scripts/connect_google.py --gcal # just Calendar; --gmail and --print also work
```

You need Node.js (`npx` on your PATH) and, for Calendar, a Google OAuth client of type
"Desktop app" with `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in `.env`; the script
checks both, prints step-by-step setup instructions for anything missing, merges the
entries without touching connectors you've added by hand, and verifies the result loads
the way the app loads it. Gmail manages its own auth, so it only needs Node. The browser
sign-in happens on each server's first launch after you restart Aide, and
`assistant-doctor` reports connector status (config file, Node, and whether the env vars
resolve).

**Anything else** (Slack, Notion, Todoist, smart home…) goes in the same file:

```bash
cp mcp_servers.example.json ~/.assistant/mcp_servers.json   # then enable what you want
```

Its tools are picked up on the next launch; secrets stay in `.env` via `${VAR}` references.

Aide always confirms before sending email or changing calendar events.

---

## Configuration

All optional, set in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `ASSISTANT_NAME` | `Aide` | What it calls itself |
| `ASSISTANT_BACKEND` | `claude` | `claude` (Claude Agent SDK) or `ollama` (local) |
| `ASSISTANT_OLLAMA_MODEL` | `llama3.1:8b` | Local model (must support tool calling) |
| `ASSISTANT_OLLAMA_NUM_CTX` | `8192` | Local context window; bigger = more RAM |
| `ASSISTANT_OLLAMA_KEEP_ALIVE` | `5m` | How long the local model stays resident after a turn |
| `ASSISTANT_ADVISOR` | `1` | Haiku advisor on the local base (`ask_advisor` + auto-rescue); needs Claude auth |
| `ASSISTANT_ADVISOR_MODEL` | `claude-haiku-4-5` | The advisor model |
| `ASSISTANT_ADVISOR_RESCUE` | `1` | Auto-escalate a stalled turn to the advisor |
| `ASSISTANT_MODEL` | `claude-haiku-4-5` | Claude model ID (when `ASSISTANT_BACKEND=claude`) |
| `ASSISTANT_EFFORT` | `high` | Claude reasoning depth (`low`→`xhigh`); higher = smarter, more tokens |
| `ASSISTANT_ESCALATE` | `1` | Hard-turn escalation (`think_harder` + the one-shot retry of a failed turn); `0` to disable |
| `ASSISTANT_ESCALATE_MODEL` | `claude-sonnet-4-6` | Everyday escalation target (`think_harder` default and the auto-retry) |
| `ASSISTANT_ESCALATE_MODEL_MAX` | `claude-opus-4-8` | Heavyweight target (`think_harder` level `opus`) |
| `ASSISTANT_ESCALATE_EFFORT` | `high` | Reasoning effort the escalation model runs with |
| `ASSISTANT_HOME` | `~/.assistant` | Where all state lives |
| `ASSISTANT_FULL_ACCESS` | `1` | Act across your home dir without prompts; `0` to sandbox |
| `ASSISTANT_ALLOWED_DIRS` | *(empty)* | Extra dirs when sandboxed, e.g. `~/Documents:~/Projects` |
| `ASSISTANT_RECALL` | `1` | Ambient recall on; `0` to disable |
| `ASSISTANT_RECALL_DAYS` | `30` | How long screen memory is kept |
| `ASSISTANT_RECALL_MAX_MB` | `1500` | Disk cap for stored screenshots; oldest pruned first |
| `ASSISTANT_SHOT_SECONDS` | `10` | OCR-shot heartbeat; unchanged screens are deduped, so a fast cadence costs CPU, not disk |
| `ASSISTANT_SHOT_MIN_GAP` | `3` | Minimum seconds between shots on rapid window switches |
| `ASSISTANT_RECALL_EXCLUDE` | *(empty)* | Extra apps/titles to never record, comma-separated; password managers, authenticators, and common banking/finance contexts are excluded by default |
| `ASSISTANT_LOG_LEVEL` | `INFO` | Verbosity of `~/.assistant/logs/aide.log` (`DEBUG`/`INFO`/`WARNING`/`ERROR`) |

Persona and tools live in `assistant/agent.py`. Add an ability by writing a `@tool`
function in `assistant/tools.py` — it's exposed to the agent automatically.

---

## Manual commands

```bash
assistant            # terminal chat
assistant-gui        # desktop app
assistant-doctor     # preflight: check credentials, deps, and macOS permissions
assistant-briefing   # generate today's briefing now
assistant-insights   # run the evening distillation now
assistant-consolidate # run the weekly memory consolidation now
pytest               # tests (no API key needed)
```

When something misbehaves, look at `~/.assistant/logs/aide.log` first. Everything writes
there: the app, the recall observer, and the scheduled jobs, so a silent background
failure leaves a trail. The file rotates so it never grows unbounded, and
`ASSISTANT_LOG_LEVEL=DEBUG` turns up the detail.

Then run `assistant-doctor`. It verifies your Claude credentials, connector setup
(Node, env vars), the optional GUI/OCR stack, and the macOS privacy permissions
(Screen Recording, Automation, Full Disk Access) that otherwise only surface as a
failed action mid-conversation. It's read-only and makes no API calls.

To remove the launchd agents:
`launchctl unload ~/Library/LaunchAgents/com.aide.*.plist && rm ~/Library/LaunchAgents/com.aide.*.plist`

---

## Privacy & security

- Ambient recall and all memory are **local**, nothing is uploaded. Recall skips
  private/incognito browsing, and by default also password managers, authenticators, and
  common banking/finance apps and sites; extend the list with `ASSISTANT_RECALL_EXCLUDE`.
- Recall data (screenshots and OCR text) is stored unencrypted under `~/.assistant` and
  relies on FileVault for protection at rest, so keep FileVault on. In chat, the
  `recall_pause` tool stops capture and `recall_forget` erases a window ("pause recall",
  "forget the last hour").
- Your API key and `mcp_servers.json` are gitignored — secrets never get committed.
- `ASSISTANT_FULL_ACCESS=1` lets Aide act across your home directory; set it to `0` to
  restrict it to `~/.assistant` plus `ASSISTANT_ALLOWED_DIRS`. Outward or destructive
  actions (sending mail, deleting files) always ask first.
