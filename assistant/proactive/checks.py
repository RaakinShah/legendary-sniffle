"""The proactive checks. Each is a small object the runner evaluates per cycle.

Deterministic checks (tasks, recall, config) use only local data and cost
nothing. LLM checks batch their reasoning into one shared-engine turn and parse
its lines into Insights. Email and calendar are read-only throughout.

A check is enabled unless ASSISTANT_PROACTIVE_<NAME>=0. Build-order: the
registry at the bottom controls which run and in what order.
"""

from __future__ import annotations

import datetime as dt
import os

from .. import config
from ..log import get_logger
from .core import (
    CYCLE, DAILY, FEED, HOURLY, NOTIFY, ON_WAKE, WEEKLY, Check, Context, Insight,
)

log = get_logger(__name__)


def _enabled(name: str) -> bool:
    return os.environ.get(f"ASSISTANT_PROACTIVE_{name.upper()}", "1") != "0"


def _parse_iso(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", ""))
    except ValueError:
        return None


def _day(now: dt.datetime) -> str:
    return now.date().isoformat()


# --- deterministic checks ----------------------------------------------------

class DueTasks(Check):
    """Overdue tasks ping; tasks due in the next few hours land in the feed.
    Replaces the old watcher's task arm."""
    name, category, cadence = "due_tasks", "tasks", CYCLE

    def run(self, ctx: Context) -> list[Insight]:
        from .. import tasks
        out: list[Insight] = []
        try:
            due = tasks.due_soon(within_hours=4)
        except Exception:  # noqa: BLE001
            log.warning("due_tasks check failed", exc_info=True)
            return out
        now = ctx.now
        for t in due:
            when = _parse_iso(t.due)
            overdue = when is not None and when < now
            out.append(Insight(
                key=f"due:{t.id}:{_day(now)}",
                category="tasks",
                title=("Overdue: " if overdue else "Due soon: ") + t.title,
                body=t.render(),
                urgency=NOTIFY if overdue else FEED,
                action_prompt=f"Help me handle this task now: {t.title}",
                source="from your task list",
            ))
        return out


class StaleTaskGardener(Check):
    """Open tasks untouched for a while: still relevant, or reschedule/drop?"""
    name, category, cadence = "stale_tasks", "tasks", WEEKLY

    def run(self, ctx: Context) -> list[Insight]:
        from .. import tasks
        try:
            items = tasks.list_tasks(status="open")
        except Exception:  # noqa: BLE001
            log.warning("stale_tasks check failed", exc_info=True)
            return []
        floor = ctx.now - dt.timedelta(days=14)
        stale = [t for t in items
                 if (_parse_iso(t.created_at) or ctx.now) < floor and not t.due]
        if not stale:
            return []
        listing = "\n".join(f"- {t.render()}" for t in stale[:10])
        n = len(stale)
        return [Insight(
            key=f"stale:{_day(ctx.now)}",
            category="tasks",
            title=f"{n} task{'s' if n > 1 else ''} {'have' if n > 1 else 'has'} gone stale",
            body="Open for 2+ weeks with no due date:\n" + listing,
            urgency=FEED,
            action_prompt="Walk me through my stale tasks one by one: keep, reschedule, "
                          "or drop each.",
            source="open tasks untouched for 14+ days",
        )]


class ContextResume(Check):
    """When you come back to the Mac, surface where you left off."""
    name, category, cadence = "context_resume", "focus", ON_WAKE

    def gate(self, ctx: Context) -> bool:
        return ctx.returned_from_away and config.RECALL

    def run(self, ctx: Context) -> list[Insight]:
        from .. import observer
        try:
            digest = observer.latest_digest()
        except Exception:  # noqa: BLE001
            return []
        if not digest:
            return []
        ts, summary = digest
        return [Insight(
            key=f"resume:{ts[:13]}",   # one per hour of digest
            category="focus",
            title="Where you left off",
            body=summary,
            urgency=FEED,
            action_prompt="Help me pick up exactly where I left off, using recall for "
                          "detail if needed.",
            source="from your ambient recall",
        )]


class ConnectorHealth(Check):
    """Tell the user when something is quietly broken instead of degrading."""
    name, category, cadence = "health", "system", DAILY

    def run(self, ctx: Context) -> list[Insight]:
        problems: list[str] = []
        if not config.auth_available():
            problems.append("No Claude credentials, the assistant can't think.")
        try:
            servers = config.load_external_mcp_servers()
            for nm, spec in servers.items():
                for k, v in (spec.get("env") or {}).items():
                    if isinstance(v, str) and (v == "" or v.startswith("${")):
                        problems.append(f"Connector '{nm}' is configured but {k} is unset.")
        except Exception:  # noqa: BLE001
            problems.append("mcp_servers.json could not be read.")
        if config.RECALL:
            db = config.ASSISTANT_HOME / "recall.db"
            if not db.exists():
                problems.append("Ambient recall is on but has recorded nothing yet.")
        if not problems:
            return []
        return [Insight(
            key=f"health:{_day(ctx.now)}:{hash(tuple(problems)) & 0xffff}",
            category="system",
            title="Aide needs attention",
            body="\n".join(f"- {p}" for p in problems),
            urgency=NOTIFY,
            action_prompt="Help me fix these setup problems with Aide.",
            source="self-check",
        )]


# --- LLM checks (one shared engine turn each, read-only) ---------------------

def _parse_insight_lines(text: str, category: str, default_urgency: str,
                         key_prefix: str, day: str) -> list[Insight]:
    """Parse `URGENCY | TITLE | BODY` lines (one insight per line) from a check's
    reply. 'ALL-CLEAR' or empty yields nothing. Malformed lines are skipped."""
    out: list[Insight] = []
    for i, raw in enumerate((text or "").splitlines()):
        line = raw.strip().lstrip("-* ").strip()
        if not line or line.upper().startswith("ALL-CLEAR"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        urg = parts[0].lower()
        urgency = NOTIFY if urg.startswith("notif") or urg.startswith("urgent") else FEED
        if len(parts) == 2:                  # "URGENCY | TITLE"
            title, body = parts[1], ""
        else:
            title, body = parts[1], " ".join(parts[2:])
        if not title:
            continue
        out.append(Insight(
            key=f"{key_prefix}:{day}:{title.lower()[:48]}",
            category=category,
            title=title[:120],
            body=body[:600],
            urgency=urgency if default_urgency != FEED or urgency == NOTIFY else FEED,
            action_prompt=f"Tell me more and help me act on: {title}",
            source=f"{category} sweep",
        ))
    return out


_SWEEP_PROMPT = """Do a read-only check of my email and calendar for things I might be \
missing in roughly the next 48 hours. Use only read tools (mcp__gcal__*, mcp__gmail__* if \
available). NEVER send, label, archive, organize, or draft anything.

Look for, and only report when real:
- a calendar event in the next ~2 hours that I should prep for (who, what)
- a deadline or commitment mentioned in email that is NOT on my calendar or task list
- a promise I made in a sent email ("I'll send X by Friday") that needs a task
- an email I sent that has gone unanswered for days and may need a follow-up
- an important unanswered email from a real person (not a newsletter/promo)
- a calendar conflict today (double-booking, or back-to-back with no gap)
- a bill, renewal, or free-trial ending soon
- a flight/hotel/reservation confirmation I should turn into calendar entries

Output ONE line per finding in EXACTLY this format:
URGENCY | SHORT TITLE | one-sentence detail
where URGENCY is "notify" for time-sensitive (next few hours / today) or "feed" otherwise.
If nothing needs me, output exactly: ALL-CLEAR
No preamble, no other text. Never invent anything; only report what the tools returned."""


class EmailCalendarSweep(Check):
    """One batched read-only pass over email + calendar. Covers meeting prep,
    unscheduled deadlines, important unanswered mail, bills, and travel."""
    name, category, cadence = "sweep", "comms", CYCLE

    def gate(self, ctx: Context) -> bool:
        # Only worth an engine turn if a connector is actually configured.
        try:
            servers = config.load_external_mcp_servers()
        except Exception:  # noqa: BLE001
            return False
        return bool({"gmail", "gcal"} & set(servers)) and config.auth_available()

    async def arun(self, ctx: Context) -> list[Insight]:
        text = await ctx.ask(_SWEEP_PROMPT, max_turns=16)
        return _parse_insight_lines(text, "comms", NOTIFY, "sweep", _day(ctx.now))


_STUDY_PROMPT = """Look at what I have been doing on screen recently using recall tools \
(recall_timeline since_hours=3, and recall_search for detail). Decide if I just studied \
something worth capturing. Report ONLY if I was clearly studying real material (a lecture, \
a textbook like First Aid, a paper, lecture slides, a problem set), not casual browsing.

Output ONE line per suggestion in EXACTLY this format:
feed | SHORT TITLE | one-sentence detail
Suggest at most two, e.g. "summarize the <lecture> I just finished" or "pull the key \
testable points from <topic>". Do NOT suggest Anki or flashcards. If I was not studying, \
output exactly: ALL-CLEAR
No preamble. Never invent a topic I did not actually have on screen."""


class StudyScan(Check):
    """Spots finished study material in recall and offers summaries/key points."""
    name, category, cadence = "study", "study", HOURLY

    def gate(self, ctx: Context) -> bool:
        return config.RECALL and config.auth_available()

    async def arun(self, ctx: Context) -> list[Insight]:
        text = await ctx.ask(_STUDY_PROMPT, max_turns=12)
        items = _parse_insight_lines(text, "study", FEED, "study", _day(ctx.now))
        # Study suggestions are never urgent.
        for it in items:
            it.urgency = FEED
            it.action_prompt = f"Do this for me now: {it.title}"
        return items


_RABBIT_PROMPT = """Using recall_timeline (since_hours=3) and recall_search, decide if I \
have spent a long uninterrupted stretch (roughly an hour or more) deep in ONE topic across \
many windows/tabs (research, debugging, shopping comparison, a deep read). If so, offer to \
pull what I found into one organized note.

Output ONE line, exactly: feed | SHORT TITLE | one-sentence detail
If there was no clear deep-dive, output exactly: ALL-CLEAR. No preamble, no invention."""


class RabbitHoleSynth(Check):
    """A long single-topic session -> offer to synthesize the findings."""
    name, category, cadence = "rabbithole", "focus", HOURLY

    def gate(self, ctx: Context) -> bool:
        return config.RECALL and config.auth_available()

    async def arun(self, ctx: Context) -> list[Insight]:
        text = await ctx.ask(_RABBIT_PROMPT, max_turns=10)
        out = _parse_insight_lines(text, "focus", FEED, "rabbithole", _day(ctx.now))
        for it in out:
            it.urgency = FEED
            it.action_prompt = f"Pull together what I found into one organized note: {it.title}"
        return out


_REVIEW_PROMPT = """Write me a short weekly review for the week ending {date}. Use \
recall_timeline (since_hours=168 if available, else as far back as it goes), list_tasks, and \
read-only calendar/email if connected. Cover: what I got done (2-4 wins), open loops still \
unfinished, and 2-3 concrete things to set up for next week. Keep it tight.

Output ONE line per item, exactly: feed | SECTION: SHORT POINT | optional detail
where SECTION is Win, Loose end, or Next week. If there is genuinely nothing, ALL-CLEAR.
No preamble. Never invent; only use what the tools returned."""


class WeeklyReview(Check):
    """A weekly wins / open-loops / next-week digest into the feed."""
    name, category, cadence = "weekly_review", "review", WEEKLY

    def gate(self, ctx: Context) -> bool:
        return config.auth_available()

    async def arun(self, ctx: Context) -> list[Insight]:
        text = await ctx.ask(_REVIEW_PROMPT.format(date=_day(ctx.now)), max_turns=16)
        return _parse_insight_lines(text, "review", FEED, "weekly", _day(ctx.now))


# Registry. Order matters only for notification priority within a cycle.
REGISTRY: list[Check] = [
    ConnectorHealth(),
    DueTasks(),
    StaleTaskGardener(),
    ContextResume(),
    EmailCalendarSweep(),
    StudyScan(),
    RabbitHoleSynth(),
    WeeklyReview(),
]


def active_checks() -> list[Check]:
    return [c for c in REGISTRY if _enabled(c.name)]
