"""Proactive engine: Aide surfaces things you would otherwise miss.

One scheduled runner (run.py) evaluates a registry of Checks each cycle, dedupes
their Insights against a local feed store (store.py), drops them quietly into the
GUI's Routines feed, and pings macOS only for genuinely time-sensitive items.
One engine is built per cycle and shared across every LLM check, so twenty
features cost one short-lived process, not twenty.

Design rules baked in:
- Quiet by default: everything lands in the feed; only urgency="notify" items
  ping, and only outside quiet hours, rate limited.
- Email/calendar are READ-ONLY. No check organizes, labels, archives, sends, or
  drafts into Gmail; they read to surface, nothing more.
- Deterministic checks (tasks, recall, memory) need no model and run every cycle
  for free. LLM checks (email/calendar/study) share the one engine.
"""

from .core import FEED, NOTIFY, Check, Context, Insight

__all__ = ["FEED", "NOTIFY", "Check", "Context", "Insight"]
