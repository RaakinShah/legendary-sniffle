"""Ambient recall (macOS): a background observer that remembers what you were doing.

Every OBSERVE_EVERY seconds it records the frontmost app + window title into a
local SQLite timeline; every SHOT_EVERY seconds it saves a small screenshot.
Everything stays in ASSISTANT_HOME (nothing leaves the machine) and is pruned
after RETAIN_HOURS. Disable with ASSISTANT_RECALL=0.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import subprocess
import sys
import threading
import time

from . import config

OBSERVE_EVERY = 30
SHOT_EVERY = 300
# Long memory by default: 30 days (ASSISTANT_RECALL_DAYS to change).
RETAIN_HOURS = 24 * int(__import__("os").environ.get("ASSISTANT_RECALL_DAYS", "30"))
SHOT_DIR = config.ASSISTANT_HOME / "recall"
DB = config.ASSISTANT_HOME / "recall.db"

_started = False


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS activity (ts TEXT, app TEXT, title TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ts ON activity(ts)")
    return con


def _osa(script: str) -> str:
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ""


def _frontmost() -> tuple[str, str]:
    app = _osa('tell application "System Events" to get name of first process whose frontmost is true')
    title = ""
    if app:
        title = _osa(
            f'tell application "System Events" to get name of front window of process "{app}"'
        )
    return app, title


def _snapshot(now: dt.datetime) -> None:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOT_DIR / f"{now.strftime('%Y%m%d-%H%M')}.jpg"
    subprocess.run(["screencapture", "-x", "-t", "jpg", str(path)], capture_output=True)
    if path.exists():
        subprocess.run(["sips", "--resampleWidth", "1024", str(path)], capture_output=True)


def _prune(con: sqlite3.Connection, now: dt.datetime) -> None:
    cutoff = (now - dt.timedelta(hours=RETAIN_HOURS)).isoformat(timespec="seconds")
    con.execute("DELETE FROM activity WHERE ts < ?", (cutoff,))
    con.commit()
    if SHOT_DIR.is_dir():
        stamp = (now - dt.timedelta(hours=RETAIN_HOURS)).strftime("%Y%m%d-%H%M")
        for f in SHOT_DIR.glob("*.jpg"):
            if f.stem < stamp:
                f.unlink(missing_ok=True)


def _loop() -> None:
    last_shot = 0.0
    con = _conn()
    while True:
        try:
            now = dt.datetime.now()
            app, title = _frontmost()
            if app:
                con.execute(
                    "INSERT INTO activity VALUES (?,?,?)",
                    (now.isoformat(timespec="seconds"), app, title),
                )
                con.commit()
            if time.time() - last_shot >= SHOT_EVERY:
                last_shot = time.time()
                _snapshot(now)
                _prune(con, now)
        except Exception:
            pass
        time.sleep(OBSERVE_EVERY)


def start() -> bool:
    """Start the observer thread (macOS only, once). Returns True if running."""
    global _started
    if _started or sys.platform != "darwin" or not config.RECALL:
        return _started
    _started = True
    threading.Thread(target=_loop, daemon=True, name="recall-observer").start()
    return True


def timeline(since_hours: float = 24, query: str = "") -> str:
    """Compressed activity log: consecutive identical app/title rows become ranges."""
    if not DB.exists():
        return "No activity recorded yet."
    cutoff = (dt.datetime.now() - dt.timedelta(hours=since_hours)).isoformat(timespec="seconds")
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT ts, app, title FROM activity WHERE ts >= ? ORDER BY ts", (cutoff,)
    ).fetchall()
    con.close()
    if query:
        q = query.lower()
        rows = [r for r in rows if q in (r[1] or "").lower() or q in (r[2] or "").lower()]
    if not rows:
        return "No matching activity."
    out, start_ts, prev = [], None, None
    for ts, app, title in rows + [(None, None, None)]:
        key = (app, title)
        if prev is None:
            start_ts, prev, prev_ts = ts, key, ts
        elif key != prev or ts is None:
            t0, t1 = start_ts[11:16], prev_ts[11:16]
            span = t0 if t0 == t1 else f"{t0}-{t1}"
            label = f"{prev[0]}" + (f" — {prev[1]}" if prev[1] else "")
            out.append(f"{start_ts[:10]} {span}  {label}")
            start_ts, prev = ts, key
        prev_ts = ts
    return "\n".join(out[-200:])


def nearest_shot(when: str = "") -> str | None:
    """Path of the screenshot closest to `when` (ISO-ish or empty for latest)."""
    if not SHOT_DIR.is_dir():
        return None
    shots = sorted(SHOT_DIR.glob("*.jpg"))
    if not shots:
        return None
    if not when:
        return str(shots[-1])
    want = when.replace("-", "").replace(":", "").replace("T", "-").replace(" ", "-")[:13]
    best = min(shots, key=lambda f: abs(int(f.stem.replace("-", "")) - int(want.replace("-", "") or 0)))
    return str(best)
