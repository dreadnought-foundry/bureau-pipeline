#!/usr/bin/env python3
"""A full sweep pass that fell back to Linear is a console alert (DRE-8798).

A read the door could not answer falls back to Linear and spends its budget,
and until this card the only trace was one `read-fallback:` line per read on
a run's stderr (DRE-8794, DRE-8796) — a log nobody reads. The sweep holds
every one it made in `bureau_read.fallbacks()`, so at the end of its own pass
it knows which reads it could not get from the door.

`settle(fallbacks, repo_slug, mode)` is the full sweep's last step before its
red or green exit, beside `standing_defect_alert.settle`, handed
`bureau_read.fallbacks()` and `bureau_read.mode()`. With one or more fallbacks
it raises one WARN console alert for the repo, naming the readers; with none
it resolves the repo's alert, so a repo that stops falling back clears. The
card-scoped, `--promote-only`, `--close-only` and idle passes never call it:
they make fewer reads than a full pass, so settling there would resolve what
the next full pass raises again.

## The alert

Notice key `read-fallback:<repo>` (console kind
`pipeline_notice:read-fallback:<repo>`), one per repo, severity WARN, raised
through `pipeline_notice.raise_notice` with no fallback card. The title names
the repo and the count of fallbacks; the body has one line per distinct
`(reader, read, reason)` with its count. A standing condition raises the same
key every pass and the door answers `repeat`, which delivers nothing again.

A raise the door cannot take is `lost`, named on the line, and never fails the
pass. It never files a Linear card: the door being down is exactly when a card
would spend the Linear budget the alert is about.

In a mode other than `on` no fallback can be noted (`bureau_read.note_fallback`
notes none), so there is nothing to read and no notice call is made — neither
a raise nor a resolve.

## The line

Exactly one per pass, on stdout, and `settle` returns it:

    read-fallback-alert: <outcome> repo=<slug> <n> fallback(s) — reader="<reader>" read=<read> reason=<reason> ×<k>; …
    read-fallback-alert: <outcome> repo=<slug> 0 fallback(s)
    read-fallback-alert: door <mode> repo=<slug> — no fallback is noted; no notice call

`<outcome>` is the client's: `raised`, `repeat`, `escalated` or `lost` for a
raise, `resolved`, `not-open` or `resolve-refused` for a resolve. The sweep
prints its own line on an idle pass and when `settle` itself raises, so every
full pass still says one.

What it does not see: fallbacks made by other workflows (a build, a review)
are not in the sweep's process. The budget check's fallback table counts them.

## The vendor boundary (`standards/vendor-boundaries.md`)

* Q1, the actor: the reconcile reusable already raises notices
  (`standing-defect:`, `reviewer_outage:fleet`), and the console door admits it.
* Q2, the credentials: the sweep job already holds `id-token: write`, which
  the notice client needs.
* Q3, a retry: a re-run raises the same key, and the door answers `repeat`.
* Q4, the limits: the door refuses a key outside `^[A-Za-z0-9_.:-]{1,120}$`,
  and `read-fallback:<repo>` is inside it; the client cuts a long body.
* Q5, a crash mid-flow: a crash before the call leaves the alert as the last
  full pass left it, and the next full pass settles it.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

import pipeline_notice

TAG = "read-fallback-alert:"
NOTICE_PREFIX = "read-fallback"
SEVERITY = "WARN"
RESOLVE_SENTENCE = ("This alert resolves on the first full sweep of this repo that "
                    "reads everything it needs from the read door.")


def notice_key(repo_slug: str) -> str:
    return f"{NOTICE_PREFIX}:{repo_slug}"


def groups(fallbacks: Iterable) -> Counter:
    """Each distinct `(reader, read, reason)` and its count, in first-seen order."""
    return Counter(tuple(f) for f in fallbacks)


def group_text(group: tuple, count: int) -> str:
    reader, read, reason = group
    return f'reader="{reader}" read={read} reason={reason} ×{count}'


def title(repo_slug: str, total: int) -> str:
    return f"The {repo_slug} sweep fell back to Linear on {total} read(s)"


def body(repo_slug: str, counted: Counter) -> str:
    named = "\n".join(f"- {group_text(g, n)}" for g, n in counted.items())
    return (f"The last full {repo_slug} sweep could not get these reads from the read "
            f"door and read Linear instead, spending its budget:\n\n{named}\n\n"
            f"{RESOLVE_SENTENCE}")


def settle(fallbacks, repo_slug: str, mode: str) -> str:
    """Raise the repo's alert when `fallbacks` holds any, resolve it when it
    holds none, call the door not at all when `mode` is not `on` — and return
    the one line printed."""
    counted = groups(fallbacks)
    total = sum(counted.values())
    if mode != "on":
        text = f"door {mode} repo={repo_slug} — no fallback is noted; no notice call"
    elif counted:
        outcome = pipeline_notice.raise_notice(
            notice_key(repo_slug), severity=SEVERITY, title=title(repo_slug, total),
            body=body(repo_slug, counted), fallback=None)
        named = "; ".join(group_text(g, n) for g, n in counted.items())
        text = f"{outcome} repo={repo_slug} {total} fallback(s) — {named}"
    else:
        outcome = pipeline_notice.resolve_notice(notice_key(repo_slug))
        text = f"{outcome} repo={repo_slug} 0 fallback(s)"
    line = f"{TAG} {text}"
    print(line, flush=True)
    return line
