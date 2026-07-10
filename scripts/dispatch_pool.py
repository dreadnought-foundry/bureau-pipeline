#!/usr/bin/env python3
"""Dispatch-pool worker selection (DRE-2013, stdlib only).

Why
---
Every agent run hammers ONE GitHub App installation's REST quota
(5,000 req/hr) — the 2026-06-28 incident exhausted it twice. Three
worker-identical Apps now exist (agent-bureau-bot-2/3/4, App IDs
4266537/4266538/4266539) next to the original, giving four independent
buckets (~20,000 req/hr total, $0). This script picks WHICH app the
worker-token mint in agent-task.yml should use for the current run.

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
    this script can read each candidate's ``GET /rate_limit`` — the endpoint
    is quota-exempt, so probing costs nothing. The script NEVER sees private
    keys: Actions can't index secrets dynamically anyway, so the workflow
    keeps an explicit per-N map and this script only outputs WHICH slot to
    feed it.
  * ``BUREAU_POOL_KEY`` (the card id; run id as fallback) seeds the
    deterministic hash fallback.

Selection
---------
  * All candidates readable  -> max ``resources.core.remaining``; ties break
    deterministically by hashing the key across the tied slots.
  * Some reads failed        -> hash the key across the READABLE pool (an
    unreadable slot is never picked blind).
  * All reads failed         -> hash the key across all configured slots.
  * Single-slot pool         -> short-circuit, no reads at all.

Output contract
---------------
stdout is appended VERBATIM to ``$GITHUB_OUTPUT`` by the workflow, so it
carries only ``n=<slot>`` and ``reason=<why>`` lines; humans read stderr.
The selector must never fail a build: any unexpected error routes to slot 1
(the original app) with exit 0.

Test hook: ``BUREAU_FAKE_RATE_LIMITS`` (JSON map slot->remaining|null)
replaces the real network read, mirroring model_fallback's fake-probe hook.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys

# Slot-1 ids are the bare names; pool slots carry a numeric suffix.
_APP_ID_RE = re.compile(r"^BUREAU_APP_ID(?:_([0-9]+))?$")

_RATE_LIMIT_URL = "https://api.github.com/rate_limit"


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


# --------------------------------------------------------------------------- #
# Rate reading                                                                 #
# --------------------------------------------------------------------------- #

def parse_remaining(body: str) -> int | None:
    """resources.core.remaining out of a /rate_limit body, else None."""
    try:
        remaining = json.loads(body)["resources"]["core"]["remaining"]
    except (ValueError, KeyError, TypeError):
        return None
    return remaining if isinstance(remaining, int) else None


def _read_remaining_real(token: str) -> int | None:
    """GET /rate_limit with one candidate's installation token.

    Quota-exempt endpoint, so probing never spends what it measures. Any
    failure (network, non-2xx, bad JSON) returns None — an unreadable slot,
    handled by the hash fallback. stdlib urllib; 10s timeout.
    """
    import urllib.request

    req = urllib.request.Request(
        _RATE_LIMIT_URL,
        headers={
            "authorization": f"Bearer {token}",
            "accept": "application/vnd.github+json",
            "user-agent": "bureau-dispatch-pool",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return parse_remaining(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _fake_readings_from_env(env, slots: list[int]) -> dict[int, int | None] | None:
    """Test hook: BUREAU_FAKE_RATE_LIMITS = JSON map slot->remaining|null
    injects the per-slot readings directly (no tokens, no network) so the
    CLI is exercisable end-to-end without GitHub."""
    raw = (env.get("BUREAU_FAKE_RATE_LIMITS") or "").strip()
    if not raw:
        return None
    try:
        table = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(table, dict):
        return None
    readings: dict[int, int | None] = {}
    for slot in slots:
        value = table.get(str(slot))
        readings[slot] = value if isinstance(value, int) else None
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


def choose(readings: dict[int, int | None], key: str | None) -> tuple[int, str]:
    """(slot, reason) from per-slot core.remaining readings (None=unreadable).

    Max remaining when everything read; hash the key across the READABLE
    pool on partial failure, across all configured on total failure.
    """
    slots = sorted(readings)
    if not slots:
        return 1, "no-pool"
    if len(slots) == 1:
        return slots[0], "single-app"
    readable = {s: r for s, r in readings.items() if isinstance(r, int)}
    if len(readable) == len(slots):
        best = max(readable.values())
        tied = sorted(s for s, r in readable.items() if r == best)
        if len(tied) == 1:
            return tied[0], "max-remaining"
        return hash_pick(key, tied), "max-remaining"
    if readable:
        return hash_pick(key, sorted(readable)), "hash-readable"
    return hash_pick(key, slots), "hash-all"


def select(env=None, reader=None) -> tuple[int, str]:
    """The (slot, reason) the worker mint should use for this run."""
    env = os.environ if env is None else env
    slots = configured_slots(env)
    if not slots:
        # Not even the original app id in env — route to slot 1 and let the
        # workflow's expression fallback handle it; never block the build.
        return 1, "no-pool"
    if len(slots) == 1:
        # Degradation path (repo without the new secrets): exactly today's
        # behavior, and no /rate_limit reads for a choice of one.
        return slots[0], "single-app"
    readings = _fake_readings_from_env(env, slots)
    if readings is None:
        if reader is None:
            reader = _read_remaining_real
        readings = {}
        for slot in slots:
            token = (env.get(token_env_name(slot)) or "").strip()
            readings[slot] = reader(token) if token else None
    return choose(readings, (env.get("BUREAU_POOL_KEY") or "").strip())


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def main(argv: list[str]) -> int:
    """CLI for agent-task.yml.

      select    print `n=<slot>` + `reason=<why>` (stdout -> $GITHUB_OUTPUT)

    Exit 0 ALWAYS on `select`: a selector failure must never fail a build —
    any unexpected error routes to the original app (slot 1).
    """
    if not argv or argv[0] != "select":
        print("usage: dispatch_pool.py select", file=sys.stderr)
        return 2
    try:
        slot, reason = select()
        pool = configured_slots()
    except Exception as exc:  # never block a build on the selector
        print(f"dispatch-pool: selector error ({exc}) — using the original "
              "worker app (slot 1)", file=sys.stderr)
        slot, reason, pool = 1, "selector-error", []
    if reason in ("no-pool", "single-app"):
        print("dispatch-pool: no additional pool apps configured — using the "
              "original worker app only", file=sys.stderr)
    elif reason != "selector-error":
        print(f"dispatch-pool: {len(pool)} candidate apps, selected slot "
              f"{slot} ({reason})", file=sys.stderr)
    print(f"n={slot}")
    print(f"reason={reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
