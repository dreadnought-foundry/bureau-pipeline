#!/usr/bin/env python3
"""Dispatch-pool worker selection (DRE-2013, re-metered by DRE-4290; stdlib only).

Why
---
Every agent run hammers ONE GitHub App installation's REST quota
(5,000 req/hr) — the 2026-06-28 incident exhausted it twice. Three
worker-identical Apps now exist (agent-bureau-bot-2/3/4, App IDs
4266537/4266538/4266539) next to the original, giving four independent
buckets (~20,000 req/hr total, $0). This script picks WHICH app a
workflow's token mint should use for the current run.

Who consults it
---------------
  * ``agent-task.yml`` — the engineer's worker token (DRE-2013, the first).
  * ``verify.yml`` — the verifier's token (DRE-2429).
  * ``red-main-repair.yml`` — the repair worker, keyed ``repair:<sha>``.
  * ``agent-fix.yml`` — the fixing agent's own token (DRE-4412), minted from
    the same selection as its reader below. DRE-4282 moved that workflow's
    reads and left its one write-capable model step on slot 1, where two
    portico fix runs died on 2026-09-20 in claude-code-action's prepare step.
  * Since DRE-4282, every heavy READER: ``qa-review.yml`` (the critic's PR
    reads; slot 1 there is the qa-bot App, DRE-1921's bucket. Its check-run
    write does NOT ride the pool — publish_review_check.py updates that check
    in place and GitHub lets only the App that CREATED a check run update it,
    so a re-review on another slot would be refused), ``reconcile.yml``
    (``GH_READ_TOKEN`` for the sweep's reads),
    ``agent-fix.yml`` (the thread and verdict fetches), ``plan.yml`` (every
    planner and critic run's token) and ``harness.yml`` (the driver's
    reader). In each the identity-sensitive writes — the PR push, the
    worker-attributed comments, the verdict comment, the review check run,
    the merge — keep the App they used before; only the READS moved (and,
    in agent-fix, its model step, by the DRE-4412 bullet above — which does
    mean a fix commit may now be pushed under any pool member; accepted, all
    four are authors and never the merger).
  * NOT ``medic.yml``: its reads ride ``github.token`` because the App has no
    ``actions:read`` (DRE-1346), a bucket this pool cannot improve on.

Every consumer is a copy of the same shape (probe mints gated on the slot
being configured, this selector, a mint that maps the slot to its secret
pair and falls back to the workflow's original pair), pinned by
tests/test_readers_on_the_pool.py, tests/test_dispatch_pool_wiring.py and
tests/test_dispatch_pool_real_meter.py.

Interface (safety-first)
------------------------
The pool is discovered from the env convention alone — never a hardcoded
count, and never a private key:

  * ``BUREAU_APP_ID`` (slot 1, the original app) and ``BUREAU_APP_ID_<N>``
    (slot N) mark a slot CONFIGURED when non-empty; absent/empty pairs just
    shrink the pool. A repo without the new secrets degrades to slot 1 with
    one log line — exactly today's behavior.
  * ``BUREAU_POOL_TOKEN`` / ``BUREAU_POOL_TOKEN_<N>`` carry short-lived
    installation tokens minted by the workflow (create-github-app-token) so
    this script can probe each candidate's meter. The script NEVER sees
    private keys: Actions can't index secrets dynamically anyway, so the
    workflow keeps an explicit per-N map and this script only outputs WHICH
    slot to feed it.
  * ``BUREAU_POOL_KEY`` (the card id; run id as fallback) seeds the
    deterministic hash used for the spread and the fallback.
  * ``GITHUB_REPOSITORY`` (set on every runner) names the repo the probe
    calls; ``BUREAU_POOL_PROBE_REPO`` overrides it for a consumer whose pool
    tokens are scoped to a different repo than the one it runs in (harness.yml
    mints every pool token for ``bureau-harness`` while running in
    bureau-pipeline). With neither there is nothing to call: the pool falls
    back to the hash and the log says so.

The meter (DRE-4290) — read this before "simplifying" it back
--------------------------------------------------------------
Each probe is ONE real, counted call — ``GET /repos/{owner}/{repo}`` with the
candidate's token — and the reading is the ``x-ratelimit-remaining`` header on
that response (``x-ratelimit-reset`` beside it, for the log). The probe costs
one request per candidate per run: at the 184 runs/hour measured on
2026-09-18 that is ~184 requests an hour on each bucket, under 4% of 5,000.

It is NOT ``GET /rate_limit``. That endpoint is free, and DRE-2013 ranked on
its ``resources.core.remaining`` for that reason — but it reports a counter
the runners' calls are not charged against, and there are two meters:

  * 2026-09-17 09:39 PT: ``/rate_limit`` for an installation read ``used 0``
    in the same second a real call's ``x-ratelimit-used`` header read 127,
    on a different reset clock.
  * 2026-09-18 15:37 PT: two harness runs were refused from GitHub-hosted
    runners while the same installation's ``/rate_limit``, read from the
    operator's machine, showed 3,000+ remaining.

Ranked on that body the main App almost always read as the roomiest, so
``max-remaining`` answered slot 1 on every one of the 184 fleet runs in the
hour after DRE-4282 (#447) went live, the three spares used 1–3 requests
each, and portico's Reconcile (run 35413054438, 18:38 PT) was refused on slot
1 — ``API rate limit exceeded for installation ID 123249480`` — the App the
pool had just chosen as the one with the most room.

Selection
---------
Readings come in three kinds: a number (the header), REFUSED (the probe
itself answered 403/429 — that bucket is out for this run) and unreadable
(network, timeout, no header — nothing is known, and the slot is never picked
while a readable one exists).

  * Two or more readable, two or more at or above ``SPREAD_BAND`` (2,000
    remaining)                -> ``spread``: hash the key across those. Four
    healthy Apps then share the load instead of the fullest being drained
    first and every other run re-choosing it.
  * Two or more readable, fewer than two in the band
                              -> ``max-remaining``: the roomiest readable;
    ties hashed. A bucket near exhaustion is never chosen while a roomier
    one exists.
  * Exactly one readable      -> ``only-readable``.
  * Nothing readable          -> ``fallback``: hash the key across the
    configured slots that were not refused (all of them if all refused —
    nothing better is known, and the run must still get a token).
  * Single-slot pool / no pool -> short-circuit, no probes at all.

Why 2,000: the hour before this card, one bucket carried the whole fleet at
974 requests twelve minutes in (DRE-4282's measurement), i.e. roughly
4,000–5,000 an hour on one App — about 1,000–1,250 per bucket once spread
four ways. A bucket with 2,000 left holds close to two hours of its even
share, so spreading onto it is safe and the choice among such buckets is
free; below 2,000 a bucket is inside its last hour of share and the pool
steers to the roomiest instead.

Output contract
---------------
stdout is appended VERBATIM to ``$GITHUB_OUTPUT`` by the workflow, so it
carries only ``n=<slot>`` and ``reason=<why>`` lines — plus, with
``--with-headroom``, one ``headroom=1:4812,2:refused,3:unreadable,4:4990``
line naming what the probe read off every slot. That flag is opt-in BECAUSE
the default shape is a contract seven workflows append to: only harness.yml
asks for it, so the reader's mid-run fallbacks can be ordered roomiest-first
instead of by slot number (DRE-4575). Humans read stderr:
one greppable line per run, every slot's reading and the rule —
``dispatch-pool: slot1=4,812 slot2=4,997 slot3=refused slot4=4,990 →
selected slot 4 (spread)``. The selector must never fail a build: any
unexpected error routes to slot 1 (the original app) with exit 0.

Test hook: ``BUREAU_FAKE_POOL_PROBES`` — JSON map slot -> ``{"status": 200,
"x-ratelimit-remaining": "4812", "x-ratelimit-reset": "..."}`` | null —
replaces the real network read with a RESPONSE, parsed by the same header
parser the probe uses; it cannot express a ``/rate_limit`` body, which is
how DRE-2013's fake hid this defect.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass

# Slot-1 ids are the bare names; pool slots carry a numeric suffix.
_APP_ID_RE = re.compile(r"^BUREAU_APP_ID(?:_([0-9]+))?$")

_API_URL = "https://api.github.com"

#: Candidates with at least this many requests left share the load by hash
#: (see "Why 2,000" above); below it the roomiest wins.
SPREAD_BAND = 2000

#: The probe's own refusal: the bucket is out for this run.
_REFUSED_STATUSES = (403, 429)


# --------------------------------------------------------------------------- #
# Pool discovery                                                               #
# --------------------------------------------------------------------------- #

def configured_slots(env=None) -> list[int]:
    """Sorted slot numbers configured in `env` (default os.environ).

    A slot is configured iff its BUREAU_APP_ID[_N] is non-empty — an unset
    repo secret renders as '' in workflow env, which counts as absent, so
    missing pairs just shrink the pool (never a hardcoded count).
    """
    env = os.environ if env is None else env
    slots = set()
    for name, value in env.items():
        m = _APP_ID_RE.match(name)
        if m and (value or "").strip():
            slots.add(int(m.group(1)) if m.group(1) else 1)
    return sorted(slots)


def token_env_name(slot: int) -> str:
    return "BUREAU_POOL_TOKEN" if slot == 1 else f"BUREAU_POOL_TOKEN_{slot}"


def probe_repo(env) -> str:
    """The repo the probe calls: the override, else the runner's own."""
    return (
        (env.get("BUREAU_POOL_PROBE_REPO") or "").strip()
        or (env.get("GITHUB_REPOSITORY") or "").strip()
    )


# --------------------------------------------------------------------------- #
# The meter                                                                    #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Reading:
    """One candidate's meter as the runner is charged: `remaining` is the
    header's number, None when nothing was read; `refused` marks a probe
    GitHub itself turned away (403/429); `reset` is the epoch second the
    bucket refills, when the header carried it."""

    remaining: int | None
    reset: int | None = None
    refused: bool = False

    @property
    def readable(self) -> bool:
        return self.remaining is not None and not self.refused


UNREADABLE = Reading(None)


def _header(headers, name: str) -> str | None:
    """Case-insensitive header lookup over any mapping with .items()."""
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return None


def _int_or_none(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def parse_probe(status: int, headers) -> Reading:
    """The Reading on one probe response.

    403/429 is a refusal whatever the headers say (a secondary limit answers
    403 with remaining > 0). Any other status carrying `x-ratelimit-remaining`
    is a reading — GitHub charges a 404 and stamps the meter on it, so it
    counts too. No header, or one that is not a number, is unreadable.
    """
    reset = _int_or_none(_header(headers, "x-ratelimit-reset"))
    if status in _REFUSED_STATUSES:
        return Reading(None, reset, refused=True)
    return Reading(_int_or_none(_header(headers, "x-ratelimit-remaining")), reset)


def _probe_real(token: str, repo: str) -> Reading:
    """GET /repos/{repo} with one candidate's installation token — one
    counted request — and the meter off its response headers.

    urllib RAISES on 403/429 (HTTPError), which is the whole reason a
    refusal is caught separately here: swallowed as "unreadable" it would be
    hashed back into the pool. Anything else — network, timeout — is
    unreadable. stdlib urllib; 10s timeout.
    """
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"{_API_URL}/repos/{repo}",
        headers={
            "authorization": f"Bearer {token}",
            "accept": "application/vnd.github+json",
            "user-agent": "bureau-dispatch-pool",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return parse_probe(resp.status, resp.headers)
    except urllib.error.HTTPError as err:
        return parse_probe(err.code, err.headers or {})
    except Exception:
        return UNREADABLE


def _fake_readings_from_env(env, slots: list[int]) -> dict[int, Reading] | None:
    """Test hook: BUREAU_FAKE_POOL_PROBES = JSON map slot -> response
    ({"status": ..., "x-ratelimit-remaining": ..., ...}) | null. Each fake
    response goes through parse_probe exactly as a real one would, so the
    hook can fake a header and nothing else."""
    raw = (env.get("BUREAU_FAKE_POOL_PROBES") or "").strip()
    if not raw:
        return None
    try:
        table = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(table, dict):
        return None
    readings: dict[int, Reading] = {}
    for slot in slots:
        response = table.get(str(slot))
        if not isinstance(response, dict):
            readings[slot] = UNREADABLE
            continue
        status = response.get("status")
        headers = {k: v for k, v in response.items() if k != "status"}
        readings[slot] = parse_probe(
            status if isinstance(status, int) else 0, headers
        )
    return readings


# --------------------------------------------------------------------------- #
# Selection                                                                    #
# --------------------------------------------------------------------------- #

def hash_pick(key: str | None, slots: list[int]) -> int:
    """Deterministic pick of one slot: sha256(key) over the sorted slots.

    sha256, NOT builtin hash() — that is salted per process, and the pick
    must be reproducible across runs/reruns of the same card. No key ->
    lowest slot (still deterministic).
    """
    slots = sorted(slots)
    if not key:
        return slots[0]
    digest = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16)
    return slots[digest % len(slots)]


def _as_reading(value) -> Reading:
    """choose() takes Readings, bare ints (DRE-2013's callers and tests) or
    None (unreadable)."""
    if isinstance(value, Reading):
        return value
    if isinstance(value, bool) or not isinstance(value, int):
        return UNREADABLE
    return Reading(value)


def choose(readings: dict, key: str | None) -> tuple[int, str]:
    """(slot, reason) from per-slot readings — the rules in the module doc."""
    slots = sorted(readings)
    if not slots:
        return 1, "no-pool"
    if len(slots) == 1:
        return slots[0], "single-app"
    normalised = {s: _as_reading(readings[s]) for s in slots}
    readable = {s: r.remaining for s, r in normalised.items() if r.readable}
    if not readable:
        candidates = [s for s, r in normalised.items() if not r.refused] or slots
        return hash_pick(key, candidates), "fallback"
    if len(readable) == 1:
        return next(iter(readable)), "only-readable"
    in_band = sorted(s for s, r in readable.items() if r >= SPREAD_BAND)
    if len(in_band) >= 2:
        return hash_pick(key, in_band), "spread"
    best = max(readable.values())
    tied = sorted(s for s, r in readable.items() if r == best)
    if len(tied) == 1:
        return tied[0], "max-remaining"
    return hash_pick(key, tied), "max-remaining"


def decide(env=None, reader=None) -> tuple[int, str, dict[int, Reading]]:
    """(slot, reason, readings) for this run; readings is empty when nothing
    was probed (no pool, a pool of one)."""
    env = os.environ if env is None else env
    slots = configured_slots(env)
    if not slots:
        # Not even the original app id in env — route to slot 1 and let the
        # workflow's expression fallback handle it; never block the build.
        return 1, "no-pool", {}
    if len(slots) == 1:
        # Degradation path (repo without the new secrets): exactly today's
        # behavior, and no probes for a choice of one.
        return slots[0], "single-app", {}
    readings = _fake_readings_from_env(env, slots)
    if readings is None:
        if reader is None:
            reader = _probe_real
        repo = probe_repo(env)
        readings = {}
        for slot in slots:
            token = (env.get(token_env_name(slot)) or "").strip()
            readings[slot] = reader(token, repo) if (token and repo) else UNREADABLE
    slot, reason = choose(readings, (env.get("BUREAU_POOL_KEY") or "").strip())
    return slot, reason, readings


def select(env=None, reader=None) -> tuple[int, str]:
    """The (slot, reason) the worker mint should use for this run."""
    slot, reason, _ = decide(env, reader)
    return slot, reason


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _describe(reading: Reading, now: float) -> str:
    """One slot's reading as the log line prints it."""
    if reading.refused:
        if reading.reset:
            minutes = max(0, int((reading.reset - now) // 60))
            return f"refused/reset-in-{minutes}m"
        return "refused"
    if reading.remaining is None:
        return "unreadable"
    return f"{reading.remaining:,}"


def log_line(slot: int, reason: str, readings: dict[int, Reading], now: float) -> str:
    parts = [f"slot{s}={_describe(readings[s], now)}" for s in sorted(readings)]
    return f"dispatch-pool: {' '.join(parts)} → selected slot {slot} ({reason})"


def headroom_line(readings: dict[int, Reading]) -> str:
    """`headroom=1:4812,2:refused,3:unreadable,4:4990` — every slot's meter as
    an OUTPUT, for a consumer that has to make a second choice later in the
    run (DRE-4575: the harness reader's fallback order).

    Numbers only, never an app id and never a token: the remaining count is
    the whole of it. No spaces, so the line stays one `key=value` for
    `$GITHUB_OUTPUT`.
    """
    parts = []
    for slot in sorted(readings):
        reading = readings[slot]
        if reading.refused:
            parts.append(f"{slot}:refused")
        elif reading.remaining is None:
            parts.append(f"{slot}:unreadable")
        else:
            parts.append(f"{slot}:{reading.remaining}")
    return "headroom=" + ",".join(parts)


def main(argv: list[str]) -> int:
    """CLI for every consumer workflow.

      select                    print `n=<slot>` + `reason=<why>`
                                (stdout -> $GITHUB_OUTPUT)
      select --with-headroom    …and a `headroom=<slot>:<remaining>,…` line

    Exit 0 ALWAYS on `select`: a selector failure must never fail a build —
    any unexpected error routes to the original app (slot 1).
    """
    import time

    if not argv or argv[0] != "select" or set(argv[1:]) - {"--with-headroom"}:
        print("usage: dispatch_pool.py select [--with-headroom]", file=sys.stderr)
        return 2
    with_headroom = "--with-headroom" in argv[1:]
    try:
        slot, reason, readings = decide()
    except Exception as exc:  # never block a build on the selector
        print(f"dispatch-pool: selector error ({exc}) — using the original "
              "worker app (slot 1)", file=sys.stderr)
        slot, reason, readings = 1, "selector-error", {}
    if reason in ("no-pool", "single-app"):
        print("dispatch-pool: no additional pool apps configured — using the "
              "original worker app only", file=sys.stderr)
    elif reason != "selector-error":
        if not probe_repo(os.environ) and not os.environ.get("BUREAU_FAKE_POOL_PROBES"):
            print("dispatch-pool: no repo to probe (GITHUB_REPOSITORY and "
                  "BUREAU_POOL_PROBE_REPO unset) — no meter read this run",
                  file=sys.stderr)
        print(log_line(slot, reason, readings, time.time()), file=sys.stderr)
    print(f"n={slot}")
    print(f"reason={reason}")
    if with_headroom:
        print(headroom_line(readings))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
