#!/usr/bin/env python3
"""Model selection: ordered preference ladder with runtime availability
detection (DRE-1490, stdlib only). Plus the preserved is_error heartbeat +
hold-cap markers from DRE-1354.

The ladder is best-first with runtime availability detection, and it is the
WORKHORSE ladder: what every build agent (engineer, frontend, devops, planner,
repairer) runs on. It starts at `claude-opus-5` and falls back to
`claude-sonnet-4-6`. Ref DRE-1490.

`claude-fable-5` is NOT on it, and its absence is a policy decision, not an
availability one (2026-08-09). Fable costs ~2x Opus per token; when Anthropic
enabled it on our subscription the probe stopped returning 404, the best-first
ladder promoted the whole fleet onto it in a single TTL window, the
subscription's rolling usage drained, and agents started dying `is_error`
mid-run. A stronger model becoming AVAILABLE must never silently promote itself
onto the build path — that is a spend decision, and it belongs to a human
editing this ladder, not to a probe. Fable is reserved for an advisory/reviewer
role; it is still a KNOWN model so existing markers attribute, but nothing
selects it. Availability detection remains: it decides how far DOWN the ladder
we walk, never how far up.

Why this replaced DRE-1354's per-role pair
-------------------------------------------
DRE-1354 pinned ONE model per role as a fixed 2-tuple (engineer Opus→Fable,
planner Fable→Opus) and switched to the alternate on an `is_error` death. But
the fallback target was a DEAD model: `claude-fable-5` returns HTTP 404, so
failures routed INTO a 404 wall — the planner's primary IS Fable (404 on the
first attempt) and the engineer's error-retry bounced to Fable→404→dead. Good
cards hit needs-human holds.

The model is now chosen by a SINGLE ordered ladder shared by both roles:

    LADDER = ["claude-opus-5", "claude-sonnet-4-6"]                     # best→worst

`select()` walks the ladder top→bottom and returns the FIRST AVAILABLE model.
Availability is probed at runtime via a minimal `/v1/messages` POST
(max_tokens:1) using the CLAUDE token already in the workflow env (no new
secret):
  * HTTP 404 / "not_found_error" / "not available"  → UNAVAILABLE → skip.
  * ANY other response, INCLUDING HTTP 429          → AVAILABLE → choose it
    (throttling != gone; the existing transient retry handles 429s).
  * probe network error/timeout/empty              → INCONCLUSIVE → fall
    through to the next KNOWN-GOOD model; never block a build on the probe, and
    never return a model just confirmed 404.

Availability is CACHED with a short TTL (in-process) so we don't probe on every
dispatch. Recovery within the ladder falls out for free: when a skipped ladder
model comes back, the next probe after the TTL sees it and `select` returns it
again. That recovery is bounded by the ladder's CONTENTS — it can restore a
model we already chose to run on, never add one we didn't.

What's preserved from DRE-1354
------------------------------
  * The "which model was used" heartbeat (`attempt_marker`) and the `is_error`
    death marker (`error_marker` / `last_error_model`). The workflows still
    write the heartbeat and stamp `model-error:` on an is_error death; the
    hold-cap logic that counts those deaths lives in dead_run.py (unchanged), so
    is_error deaths still count toward the shared cap — no 18× loops.

Pure-ish functions: `select` takes an injectable `probe`/`clock` so the ladder
walk is unit-tested with a fake — NO real network in tests.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Mapping

FABLE = "claude-fable-5"
OPUS = "claude-opus-5"
SONNET = "claude-sonnet-4-6"

# Models that have rotated OUT of the ladder. NOT selectable — kept only so a
# marker stamped before the rotation stays attributable. `last_error_model`
# validates a marker's payload against KNOWN_MODELS, so dropping a retired id
# outright would make an in-flight card's death silently resolve to None and
# the console would lose the attribution rather than report it.
RETIRED_MODELS = {"claude-opus-4-8"}

# The ordered WORKHORSE ladder, best → worst. Used by BOTH engineer and
# planner — no role hardcodes a model. select() returns the first available
# entry walking top→bottom. (Haiku is intentionally not here: engineer/planner
# work realistically wants Sonnet-or-better.)
#
# FABLE is deliberately absent (2026-08-09). It is excluded by POLICY — cost and
# subscription quota, ~2x Opus per token — not by availability, so re-enabling it
# upstream must NOT put it back here. Membership of this list is the spend
# decision; the probe only decides how far down we walk. Fable stays in
# KNOWN_MODELS below so existing markers still attribute.
LADDER: list[str] = [OPUS, SONNET]

# Every model id we recognize when validating a marker's payload. This is a
# SUPERSET of the ladder: the ladder is what we may select, this is what we can
# still read. Retired ids belong here and nowhere else — and so does FABLE,
# which left the ladder on cost policy while cards in flight still carry
# `model-attempt:`/`model-error: claude-fable-5`. Dropping it would resolve
# those deaths to None and lose the attribution rather than report it.
KNOWN_MODELS = {FABLE, OPUS, SONNET} | RETIRED_MODELS

# Cache availability results so we don't probe on every dispatch. ~12 min keeps
# latency negligible across a burst of cards while picking up an Anthropic
# re-enable within one TTL window (auto-recovery).
AVAILABILITY_TTL_SECONDS = 12 * 60

# Comment markers (machine-parseable; also human-readable on the Linear card).
# The report step writes MARKER_PREFIX + the model used for THIS attempt, and on
# an is_error death it writes ERROR_MARKER_PREFIX + the model that died. These
# are preserved from DRE-1354 — the board/console surface per-attempt model and
# dead_run.py counts is_error deaths via the error marker.
MARKER_PREFIX = "model-attempt:"
ERROR_MARKER_PREFIX = "model-error:"

_ERROR_MARKER_RE = re.compile(
    re.escape(ERROR_MARKER_PREFIX) + r"\s*([a-z0-9.-]+)", re.IGNORECASE
)

# Substrings that mean "this model is not available for us" even absent a clean
# HTTP status (Anthropic's 404 body says "not_found_error" / "not available").
_UNAVAILABLE_HINTS = ("not_found_error", "not available", "not found")

# Anthropic API constants — the version every request pins, and the beta header
# the subscription OAuth token requires.
ANTHROPIC_VERSION = "2023-06-01"
OAUTH_BETA = "oauth-2025-04-20"


# --------------------------------------------------------------------------- #
# Auth (the ONE seam every Anthropic call in this repo goes through)           #
# --------------------------------------------------------------------------- #

def auth_headers() -> dict[str, str] | None:
    """Anthropic auth + version headers from the workflow env, or None when
    there is no credential at all.

    Two token shapes, in precedence order — an API key wins when both are set:
      * `ANTHROPIC_API_KEY`         -> `x-api-key`
      * `CLAUDE_CODE_OAUTH_TOKEN`   -> `Authorization: Bearer` plus the
                                       `anthropic-beta: oauth-2025-04-20`
                                       header the subscription token needs.

    Extracted from `_probe_real` (DRE-2236) so the availability probe and the
    model catalog share ONE block rather than two copies that drift the day one
    side gains a header. Returning None (rather than raising) keeps the
    callers' degrade paths intact: the probe treats "no token" as inconclusive
    and the catalog returns empty — neither blocks a dispatch.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    oauth = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if not api_key and not oauth:
        return None

    headers = {"anthropic-version": ANTHROPIC_VERSION}
    if api_key:
        headers["x-api-key"] = api_key
    else:
        headers["authorization"] = f"Bearer {oauth}"
        headers["anthropic-beta"] = OAUTH_BETA
    return headers


# --------------------------------------------------------------------------- #
# Availability detection                                                       #
# --------------------------------------------------------------------------- #

def classify_available(status: int | None, body: str = "") -> bool:
    """Classify a probe response into AVAILABLE (True) / UNAVAILABLE (False).

    UNAVAILABLE iff the model is genuinely gone: HTTP 404, or a body that says
    not_found / not available. EVERYTHING else is AVAILABLE — including 429
    (rate-limited): throttling is not absence, and the existing transient-HTTP
    retry handles 429s. A 400/500 also means the model exists for us.
    """
    if status == 404:
        return False
    text = (body or "").lower()
    if any(hint in text for hint in _UNAVAILABLE_HINTS):
        return False
    return True


def _probe_real(model: str) -> bool:
    """Probe one model's availability via a minimal /v1/messages POST.

    Uses the CLAUDE token already in the workflow env (ANTHROPIC_API_KEY, or the
    subscription OAuth token CLAUDE_CODE_OAUTH_TOKEN) via the shared
    `auth_headers()` seam — no new secret, one auth block. Returns
    True (AVAILABLE) on any non-404 response and on an inconclusive probe
    (network error / no token), so we never block a build on the probe. Only a
    definite 404 / not-found returns False.

    stdlib only (urllib); matches the file's no-dependency style.
    """
    import json
    import urllib.error
    import urllib.request

    headers = auth_headers()
    if headers is None:
        # No token to probe with → inconclusive → treat as available so the
        # ladder degrades to best-first without blocking.
        return True
    headers = {"content-type": "application/json", **headers}

    payload = json.dumps(
        {
            "model": model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
    ).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return classify_available(resp.status)
    except urllib.error.HTTPError as e:  # non-2xx — the common probe outcome
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:  # pragma: no cover - defensive
            body = ""
        return classify_available(e.code, body)
    except Exception:
        # Network error / timeout / empty → inconclusive → available, so a flaky
        # probe never blocks a build. The ladder still skips a CONFIRMED 404.
        return True


# In-process availability cache: model -> (available: bool, expires_at: float).
_AVAILABILITY_CACHE: dict[str, tuple[bool, float]] = {}


def clear_availability_cache() -> None:
    """Drop all cached availability results (test/dispatch hook)."""
    _AVAILABILITY_CACHE.clear()


def _is_available(model, probe, clock):
    """Cached availability for one model. `probe(model) -> bool` does the work;
    `clock() -> float` is a monotonic time source (both injectable for tests)."""
    now = clock()
    cached = _AVAILABILITY_CACHE.get(model)
    if cached is not None and cached[1] > now:
        return cached[0]
    try:
        available = bool(probe(model))
    except Exception:
        # An exploding probe is inconclusive: don't return the model (it may be
        # the one that's 404ing), and don't cache — fall through to the next.
        return None
    _AVAILABILITY_CACHE[model] = (available, now + AVAILABILITY_TTL_SECONDS)
    return available


# --------------------------------------------------------------------------- #
# Selection                                                                    #
# --------------------------------------------------------------------------- #

def _normalize_ladder(ladder) -> list[str]:
    """Coerce a caller-supplied ladder into a list of model-id strings.

    Returns [] for anything unusable so `select` can fall back to LADDER rather
    than raise — this function sits on the dispatch path of every card, and the
    module's contract is to degrade, never to block a build.

    Two shapes are handled deliberately:

      * A bare string. `ladder: claude-opus-5` in YAML is a scalar, not a
        sequence, and a str is both truthy and iterable — walked unguarded it
        selects a single LETTER as the model id and hands it to the run.
      * agents.yaml's own `ladder:`, a list of {model:, reason:} mappings. The
        docstring points callers at that file, so accepting the shape it
        actually stores costs nothing and removes a TypeError from the hot path.

    Entries that are neither a string nor a mapping with a string `model` are
    dropped rather than fatal — one malformed row must not strand a dispatch.
    """
    if ladder is None or isinstance(ladder, (str, bytes)):
        return []
    try:
        entries = list(ladder)
    except TypeError:  # not iterable at all
        return []
    out: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, Mapping) and isinstance(entry.get("model"), str):
            out.append(entry["model"])
    return out


def select(role: str = "engineer", *, probe=None, clock=None, ladder=None) -> str:
    """The model the next attempt should use: the first AVAILABLE model walking
    the ordered ladder best→worst.

    Shared by BOTH engineer and planner — `role` is accepted for call-site
    symmetry and heartbeats but does NOT change the ladder.

    `ladder` is the ordered model id list to walk (best→worst); it defaults to
    the module-level ``LADDER``. The console passes the ladder it read from
    ``agents.yaml`` so the SINGLE source of truth (the YAML) drives the order
    while this function provides the SINGLE shared walk. The kwarg lives here
    rather than in the console's vendored copy so that copy can be diffed
    against this file with no allowed exceptions — an exception is a hole a
    drift check cannot see through.

    `probe(model) -> bool` and `clock() -> float` are injectable; the defaults
    do a real minimal /v1/messages probe and use a monotonic clock. Degrade
    safely: a model is skipped only when CONFIRMED unavailable (or its probe is
    inconclusive); if nothing resolves available, fall through to the last
    (lowest, most-likely-up) known-good model rather than block the build or
    return a model just confirmed 404.
    """
    if probe is None:
        probe = _probe_real
    if clock is None:
        clock = time.monotonic
    walk = _normalize_ladder(ladder) or LADDER

    for model in walk:
        available = _is_available(model, probe, clock)
        if available:
            return model
        # available is False (confirmed 404) or None (inconclusive) → keep
        # walking; never return a model just confirmed gone.

    # Nothing probed available — don't block the build. Fall through to the
    # last (lowest-ranked, broadest-availability) ladder model.
    return walk[-1]


# --------------------------------------------------------------------------- #
# Heartbeat + is_error markers (preserved from DRE-1354)                       #
# --------------------------------------------------------------------------- #

def last_error_model(comment_bodies: list[str | None]) -> str | None:
    """The model id from the MOST RECENT is_error death marker, or None.

    `comment_bodies` is oldest→newest (Linear's natural order); we scan from the
    end so the latest death wins. Only a KNOWN model id counts. Retained so the
    board/console can attribute the last is_error death to a model."""
    for body in reversed(comment_bodies):
        for found in _ERROR_MARKER_RE.findall(body or ""):
            model = found.lower()
            if model in KNOWN_MODELS:
                return model
    return None


def attempt_marker(model: str) -> str:
    """Marker recording which model an attempt used (the heartbeat)."""
    return f"{MARKER_PREFIX} {model}"


def error_marker(model: str) -> str:
    """Marker recording an is_error death and the model that died (counted
    toward the shared hold cap by dead_run.py — no 18× loops)."""
    return f"{ERROR_MARKER_PREFIX} {model}"


def _role_from_labels(labels: list[str]) -> str:
    low = [l.lower() for l in labels]
    if "agent:planner" in low:
        return "planner"
    if "agent:devops" in low:
        return "devops"
    if "agent:frontend" in low:
        return "frontend"
    return "engineer"


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _fake_probe_from_env():
    """Test hook: if BUREAU_FAKE_AVAILABLE is set to a JSON map model->bool, the
    CLI uses it instead of the real network probe — so the CLI is exercisable in
    tests without hitting Anthropic."""
    import json

    raw = os.environ.get("BUREAU_FAKE_AVAILABLE", "").strip()
    if not raw:
        return None
    try:
        table = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(table, dict):
        return None
    return lambda m: bool(table.get(m, True))


def main(argv: list[str]) -> int:
    """CLI for the workflow.

      select [<role>]              print the model the next attempt should use
                                   (walks the ladder, probes availability)
      role-of <label,label,...>    print engineer|planner from labels

    `select` probes the live API using the CLAUDE token in the env. The role
    arg is accepted (and labels still resolve a role for the heartbeat) but does
    not change the ladder — both roles share it."""
    if not argv:
        print("usage: model_fallback.py select [<role>] | role-of <labels>")
        return 2
    cmd, *rest = argv
    if cmd == "role-of":
        labels = (rest[0] if rest else "").split(",")
        print(_role_from_labels([l.strip() for l in labels if l.strip()]))
        return 0
    if cmd == "select":
        role = rest[0] if rest else "engineer"
        # Ignore a legacy comments-file 2nd arg if the workflow still passes one
        # — selection no longer reads card history; availability drives it.
        clear_availability_cache()
        print(select(role, probe=_fake_probe_from_env()))
        return 0
    print(f"unknown command {cmd!r}")
    return 2


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
