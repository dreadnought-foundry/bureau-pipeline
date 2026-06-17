#!/usr/bin/env python3
"""Model selection: ordered preference ladder with runtime availability
detection (DRE-1490, stdlib only). Plus the preserved is_error heartbeat +
hold-cap markers from DRE-1354.

The ladder is best-first with runtime availability detection. Today
`claude-fable-5` is 404 on our subscriptions, so `select` resolves to
`claude-opus-4-8`; it self-heals to Fable the moment Anthropic re-enables it,
with NO code change (the next probe after the cache TTL sees it available and
`select` returns it again). Ref DRE-1490.

Why this replaced DRE-1354's per-role pair
-------------------------------------------
DRE-1354 pinned ONE model per role as a fixed 2-tuple (engineer Opus→Fable,
planner Fable→Opus) and switched to the alternate on an `is_error` death. But
the fallback target was a DEAD model: `claude-fable-5` returns HTTP 404, so
failures routed INTO a 404 wall — the planner's primary IS Fable (404 on the
first attempt) and the engineer's error-retry bounced to Fable→404→dead. Good
cards hit needs-human holds.

The model is now chosen by a SINGLE ordered ladder shared by both roles:

    LADDER = ["claude-fable-5", "claude-opus-4-8", "claude-sonnet-4-6"]   # best→worst

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
dispatch. Auto-recovery falls out for free: when Fable stops 404ing, the next
probe after the TTL sees it available and `select` returns it again.

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

FABLE = "claude-fable-5"
OPUS = "claude-opus-4-8"
SONNET = "claude-sonnet-4-6"

# The ordered preference ladder, best → worst. Used by BOTH engineer and
# planner — no role hardcodes a model. select() returns the first available
# entry walking top→bottom. (Haiku is intentionally not here: engineer/planner
# work realistically wants Sonnet-or-better.)
LADDER: list[str] = [FABLE, OPUS, SONNET]

# Every known model id, so we can validate a marker's payload and the ladder.
KNOWN_MODELS = {FABLE, OPUS, SONNET}

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
    subscription OAuth token CLAUDE_CODE_OAUTH_TOKEN) — no new secret. Returns
    True (AVAILABLE) on any non-404 response and on an inconclusive probe
    (network error / no token), so we never block a build on the probe. Only a
    definite 404 / not-found returns False.

    stdlib only (urllib); matches the file's no-dependency style.
    """
    import json
    import urllib.error
    import urllib.request

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    oauth = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if not api_key and not oauth:
        # No token to probe with → inconclusive → treat as available so the
        # ladder degrades to best-first without blocking.
        return True

    headers = {
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if api_key:
        headers["x-api-key"] = api_key
    else:
        headers["authorization"] = f"Bearer {oauth}"
        headers["anthropic-beta"] = "oauth-2025-04-20"

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

def select(role: str = "engineer", *, probe=None, clock=None) -> str:
    """The model the next attempt should use: the first AVAILABLE model walking
    the ordered ladder best→worst.

    Shared by BOTH engineer and planner — `role` is accepted for call-site
    symmetry and heartbeats but does NOT change the ladder.

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

    for model in LADDER:
        available = _is_available(model, probe, clock)
        if available:
            return model
        # available is False (confirmed 404) or None (inconclusive) → keep
        # walking; never return a model just confirmed gone.

    # Nothing probed available — don't block the build. Fall through to the
    # last (lowest-ranked, broadest-availability) ladder model.
    return LADDER[-1]


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
