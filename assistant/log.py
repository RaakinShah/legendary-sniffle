"""Structured logging for the whole app.

One rotating log file at ~/.assistant/logs/aide.log captures everything; warnings
and errors also go to stderr so they surface when you run from a terminal. The
level is set with ASSISTANT_LOG_LEVEL (DEBUG/INFO/WARNING/ERROR; default INFO).

Usage:
    from .log import get_logger
    log = get_logger(__name__)
    log.info("started")
    log.exception("the observer loop hit an error")   # inside an except block

Background threads (the observer) and unattended jobs (briefing/insights/
consolidate) write here too, so a silent failure leaves a trail instead of
vanishing.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from . import config

_ROOT_NAME = "aide"
_configured = False


def _level() -> int:
    name = os.environ.get("ASSISTANT_LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, name, logging.INFO)


def _configure() -> None:
    """Attach handlers to the 'aide' root logger exactly once."""
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger(_ROOT_NAME)
    root.setLevel(_level())
    root.propagate = False           # don't double-log through the stdlib root

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler — the durable record. Skipped under pytest: the test home is a
    # per-test temp dir that's deleted afterward, so a cached file handle would go
    # stale (and we don't want to write into the real ~/.assistant during tests).
    # If the log dir can't be created (rare), we still get the stderr handler
    # below, so logging never crashes the app.
    #
    # Multi-process note: the GUI and the launchd jobs (briefing/insights/
    # consolidate/watch) share this file. POSIX O_APPEND keeps interleaved lines
    # intact, but RotatingFileHandler's rename-at-2MB is not cross-process safe:
    # a job writing during the GUI's rotation can land a few lines in the
    # rotated-away file. Accepted: rotation is rare, the loss is bounded, and
    # each launchd job also has its own stdout/stderr log via its plist.
    # delay=True avoids even opening the file in processes that never log.
    if "pytest" not in sys.modules:
        try:
            log_dir = config.ASSISTANT_HOME / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(
                log_dir / "aide.log", maxBytes=2_000_000, backupCount=3,
                encoding="utf-8", delay=True,
            )
            fh.setLevel(_level())
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except Exception:  # noqa: BLE001 - logging must degrade, never raise
            pass

    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.WARNING)     # keep stderr quiet; warnings/errors only
    sh.setFormatter(fmt)
    root.addHandler(sh)


def get_logger(name: str = _ROOT_NAME) -> logging.Logger:
    """Return a logger under the 'aide' namespace, configuring handlers on first use.

    Pass __name__ from a module; it's normalized so every logger is a child of
    'aide' and shares the one file/stderr handler pair.
    """
    _configure()
    if name == _ROOT_NAME or name.startswith(_ROOT_NAME + "."):
        full = name
    else:
        # turn "assistant.observer" into "aide.observer" so all logs share handlers
        leaf = name.rsplit(".", 1)[-1]
        full = f"{_ROOT_NAME}.{leaf}"
    return logging.getLogger(full)
