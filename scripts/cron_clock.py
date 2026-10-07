"""Elapsed time and cron cadence, for the watchers that read a clock (DRE-6053).

Three helpers, moved unchanged out of the channel-staleness alarm (DRE-2552)
when that job was retired on 2026-10-06 — the console's channel-health monitor
raises its alarm now. They had other readers: `nightly_watch.py` phrases its
elapsed time with `hours_since` and `elapsed_days`, and the wiring tests read a
schedule's cadence with `cron_interval_hours`.

No network, no git — pure functions of their arguments.
"""

from __future__ import annotations

import datetime as dt


def hours_since(when: str | None, *, now: str | None = None) -> float | None:
    """Elapsed hours since an ISO timestamp, or None if it cannot be read.

    Unreadable and absent are the same answer — we do not know — and the
    caller renders that as unknown rather than as a number that looks fine.
    """
    if not when:
        return None
    try:
        then = dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
        end = (
            dt.datetime.fromisoformat(now.replace("Z", "+00:00"))
            if now
            else dt.datetime.now(dt.timezone.utc)
        )
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=dt.timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=dt.timezone.utc)
    return (end - then).total_seconds() / 3600.0


def cron_interval_hours(cron: str) -> float | None:
    """The interval of a daily/hourly 5-field cron, or None if it is neither.

    Only rich enough to keep a schedule and the interval its decision module
    assumes honest with each other; it is not a cron engine.
    """
    fields = (cron or "").split()
    if len(fields) != 5:
        return None
    minute, hour, dom, month, dow = fields
    if dom != "*" or month != "*" or dow != "*":
        return None
    if hour == "*":
        return 1.0
    if hour.isdigit() and minute.isdigit():
        return 24.0
    return None


def elapsed_days(hours: float) -> str:
    """'29 days' / '30 hours' — the unit a human would have used."""
    if hours >= 48:
        value, unit = hours / 24, "day"
    else:
        value, unit = hours, "hour"
    return f"{value:.0f} {unit}" + ("" if f"{value:.0f}" == "1" else "s")
