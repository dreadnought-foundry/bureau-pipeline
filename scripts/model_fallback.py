#!/usr/bin/env python3
"""Model selection: ordered preference ladders with runtime availability
detection (DRE-1490). Plus the preserved is_error heartbeat + hold-cap markers
from DRE-1354.

WHERE THE LADDERS COME FROM (DRE-2316)
--------------------------------------
`config/models.yaml` in this repo is the ONE file a human edits to change which
model an agent runs on, and this module READS it — no Python constant is
maintained separately. The config is a FILE in the public bureau-pipeline
checkout the product-repo workflows already do (`.bureau-pipeline` @main),
because those workflows have NO AWS credentials and NO private-repo token; it
can never be a runtime lookup. Same constraint, same shape as
`config/repo-map.json` (see config/README.md).

`_FALLBACK_MODEL_CONFIG` below is a GENERATED mirror of that file, used ONLY
when the YAML is missing/unreadable — a truncated checkout must not strand a
dispatch. Do not hand-edit it: run `python3 scripts/sync_model_config.py`
(`--check` fails CI on drift).

The default ladder is best-first and is the WORKHORSE ladder: what every build
agent (engineer, frontend, devops, planner, fixer, repairer) runs on. It starts
at `claude-opus-5` and falls back to `claude-sonnet-4-6`. Ref DRE-1490. The
review tier (critic, verifier, medic) walks its own ladder — those three used to
bypass selection with a `--model` string pinned in the workflow YAML, which no
config edit could change.

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

The model is now chosen by a SINGLE ordered ladder shared by both roles (the
`workhorse` ladder in config/models.yaml, best→worst).

`select()` walks the role's ladder top→bottom and returns the FIRST AVAILABLE
model.
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
import sys
import time
from collections.abc import Mapping
from pathlib import Path

FABLE = "claude-fable-5"
OPUS = "claude-opus-5"
SONNET = "claude-sonnet-4-6"

# The canonical model config, bundled in this checkout (never a runtime lookup —
# the workflows that read it hold no cloud credentials and no private-repo
# token). config/README.md documents that constraint.
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "models.yaml"

# Last-known-good fallback: the config as of this commit. `_load_config` reads
# the on-disk YAML FIRST (so changing a model is a data edit to one file); this
# literal is used ONLY when that file is missing/unreadable/malformed, so a
# truncated checkout degrades to the last-known ladders instead of stranding a
# dispatch.
#
# GENERATED REGION: do not hand-edit. Edit config/models.yaml, then run
# `python3 scripts/sync_model_config.py` to regenerate it (`--check` in CI fails
# if the two drift).
# --- BEGIN generated model config (from config/models.yaml) ---
_FALLBACK_MODEL_CONFIG = {
    "default_ladder": "workhorse",
    "ladders": {
        "workhorse": ["claude-opus-5", "claude-sonnet-4-6"],
        "reviewer": ["claude-sonnet-4-6"],
    },
    "agents": {
        "engineer": "workhorse",
        "frontend": "workhorse",
        "database-architect": "workhorse",
        "devops": "workhorse",
        "fixer": "workhorse",
        "critic": "reviewer",
        "verifier": "reviewer",
        "planner": "workhorse",
        "medic": "reviewer",
        "repairer": "workhorse",
    },
    "retired": ["claude-opus-4-8"],
    "excluded": ["claude-fable-5"],
}
# --- END generated model config ---


def _normalize_config(raw) -> dict | None:
    """Coerce a parsed config document into the plain-id shape above, or None if
    it is unusable. Ladder rungs may be bare ids or `{model:, reason:}` mappings
    (the canonical file uses the latter so each rung carries its justification)."""
    if not isinstance(raw, Mapping) or not isinstance(raw.get("ladders"), Mapping):
        return None

    def ids(rungs) -> list[str]:
        out: list[str] = []
        for rung in rungs or []:
            if isinstance(rung, str):
                out.append(rung)
            elif isinstance(rung, Mapping) and isinstance(rung.get("model"), str):
                out.append(rung["model"])
        return out

    ladders = {str(n): ids(r) for n, r in raw["ladders"].items()}
    ladders = {n: m for n, m in ladders.items() if m}
    if not ladders:
        return None
    default = str(raw.get("default_ladder") or "")
    if default not in ladders:
        default = next(iter(ladders))
    agents = {
        str(name): str(ladder)
        for name, ladder in (raw.get("agents") or {}).items()
        if str(ladder) in ladders
    }
    return {
        "default_ladder": default,
        "ladders": ladders,
        "agents": agents,
        "retired": ids(raw.get("retired")),
        "excluded": ids(raw.get("excluded")),
    }


def _load_config() -> dict:
    """The model config: the bundled YAML when readable, else the generated
    literal above. Degrades, never raises — this sits on the dispatch path of
    every card, and a config we cannot parse must not block a build."""
    try:
        import yaml  # PyYAML ships in the runner image; absence is degradable

        parsed = _normalize_config(yaml.safe_load(_CONFIG_PATH.read_text()))
        if parsed:
            return parsed
        print(
            f"model_fallback: model config {_CONFIG_PATH} empty/unusable; "
            "using last-known-good fallback",
            file=sys.stderr,
        )
    except Exception as exc:  # missing / unreadable / malformed / no PyYAML
        print(
            f"model_fallback: could not read model config {_CONFIG_PATH} "
            f"({exc}); using last-known-good fallback",
            file=sys.stderr,
        )
    return _normalize_config(_FALLBACK_MODEL_CONFIG) or dict(_FALLBACK_MODEL_CONFIG)


CONFIG = _load_config()

# Named ordered ladders, best → worst, straight from the config. select()
# returns the first available entry walking a ladder top→bottom.
LADDERS: dict[str, list[str]] = CONFIG["ladders"]

# agent/role name → ladder name. No role hardcodes a model: the critic,
# verifier and medic resolve theirs here too (DRE-2316).
AGENT_LADDERS: dict[str, str] = CONFIG["agents"]

# The default (WORKHORSE) ladder — what any unrecognized role falls back to, and
# what every build agent walks. FABLE is deliberately absent (2026-08-09): it is
# excluded by POLICY — cost and subscription quota, ~2x Opus per token — not by
# availability, so re-enabling it upstream must NOT put it back. Membership is
# the spend decision and it is made in config/models.yaml; the probe only
# decides how far down we walk. (Haiku is intentionally absent too: build work
# realistically wants Sonnet-or-better.)
LADDER: list[str] = LADDERS[CONFIG["default_ladder"]]

# Models that have rotated OUT of every ladder. NOT selectable — kept only so a
# marker stamped before the rotation stays attributable. `last_error_model`
# validates a marker's payload against KNOWN_MODELS, so dropping a retired id
# outright would make an in-flight card's death silently resolve to None and
# the console would lose the attribution rather than report it.
RETIRED_MODELS = set(CONFIG["retired"])

# Every model id we recognize when validating a marker's payload. This is a
# SUPERSET of the ladders: the ladders are what we may select, this is what we
# can still read. Retired ids belong here and nowhere else — and so does FABLE,
# which left the ladder on cost policy while cards in flight still carry
# `model-attempt:`/`model-error: claude-fable-5`. Dropping it would resolve
# those deaths to None and lose the attribution rather than report it.
KNOWN_MODELS = (
    {m for models in LADDERS.values() for m in models}
    | RETIRED_MODELS
    | set(CONFIG["excluded"])
)


def ladder_for(role: str) -> list[str]:
    """The ordered ladder an agent/role walks, from the config. An unknown name
    gets the default ladder — select() must never block a build on a role it
    does not recognize."""
    return LADDERS.get(AGENT_LADDERS.get(role or "", ""), LADDER)

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
      * config/models.yaml's own ladder shape, a list of {model:, reason:}
        mappings. Callers (and the console) read ladders out of YAML, so
        accepting the shape it actually stores costs nothing and removes a
        TypeError from the hot path.

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
    that agent's ordered ladder best→worst.

    `role` is an agent/role name from config/models.yaml (`engineer`, `planner`,
    `critic`, `verifier`, `medic`, …). All the build roles share the workhorse
    ladder; the review tier has its own. An unrecognized name gets the default
    ladder rather than an error.

    `ladder` is an explicit ordered model id list to walk (best→worst),
    overriding the config lookup. The console passes the ladder it read from the
    registry so the SINGLE source of truth drives the order while this function
    provides the SINGLE shared walk. The kwarg lives here rather than in the
    console's vendored copy so that copy can be diffed against this file with no
    allowed exceptions — an exception is a hole a drift check cannot see
    through.

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
    walk = _normalize_ladder(ladder) or ladder_for(role)

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
    """CLI for the workflows — the entry point every agent workflow calls.

      select [<agent>]             print the model the next attempt should use
                                   (walks that agent's ladder from
                                   config/models.yaml, probes availability)
      role-of <label,label,...>    print engineer|planner|devops|frontend from
                                   a card's labels

    `select` probes the live API using the CLAUDE token in the env. `<agent>` is
    a name from config/models.yaml (`engineer`, `planner`, `critic`, `verifier`,
    `medic`, `fixer`, `repairer`, …); an unknown name walks the default
    ladder."""
    if not argv:
        print("usage: model_fallback.py select [<agent>] | role-of <labels>")
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
