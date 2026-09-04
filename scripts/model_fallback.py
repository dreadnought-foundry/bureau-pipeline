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

THE ROLE KINDS (DRE-2317, DRE-3015)
-----------------------------------
Every role is classified in that config as one of three KINDS, and a role is
assigned a kind — never a raw ladder name:

  * `workhorse` — high-volume build work (engineer, frontend, devops,
    database-architect, fixer, repairer). Hundreds of turns per card: this is
    the HOT PATH, and it gets the cost-appropriate model. Today
    `claude-opus-5`, falling back to `claude-sonnet-4-6`.
  * `advisory` — bounded consults at decision points (critic, verifier, medic,
    the two plan critics). The critic gates EVERY unattended merge, so it is
    the correctness backstop for a pipeline where no human reads a diff.
    Sonnet 5 since 2026-08-12, on measured cost.
  * `judgement` — the planner, alone. One run per epic, at a decision point,
    and the plan it writes is the specification every child card is built
    from — so a bad output costs a fix loop per child rather than a retry. Low
    volume, highest leverage: it gets the STRONGEST model
    (`claude-fable-5-1`), falling LOUDLY to the workhorse rungs beneath it.

Before DRE-2317 the allocation was exactly inverted: the critic ran on the
cheapest model we had while build agents walked a ladder topped by the most
expensive one. A build failure is loud; a shallow review is silent.

Fable is absent from every workhorse ladder by POLICY, not availability
(2026-08-09). Fable costs ~2x Opus per token; when Anthropic enabled it on our
subscription the probe stopped returning 404, the best-first ladder promoted
the whole fleet onto it in a single TTL window, the subscription's rolling
usage drained, and agents started dying `is_error` mid-run. AVAILABILITY IS NOT
PERMISSION: a stronger model becoming available must never promote itself onto
the build path — that is a spend decision belonging to a human editing
config/models.yaml, not to a probe. Availability detection only decides how far
DOWN a ladder we walk, never how far up.

That is why the planner's promotion is not a counter-example: it is a HUMAN
edit to config/models.yaml in a reviewed PR, which is the sanctioned way up and
the only one. The volume that caused the incident is absent by construction —
the planner runs once per epic, not per card — and no build role's ladder
gained a rung.

`policy_errors()` below turns all of that from a convention into a validated
invariant: a config that puts the top of a non-build ladder onto a build
ladder, demotes the critic to a build kind, or declares `on_new_model:
workhorse` (or `judgement`) is REJECTED — the selector degrades to the
last-known-good mirror rather than honour it, and `sync_model_config.py
--check` fails CI red.

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

# The role kinds (DRE-2317, DRE-3015). A role is assigned one of THESE, never a
# ladder name — the indirection is what stops a future edit from inventing a
# ladder of its own and quietly putting build work on the strongest model.
WORKHORSE_KIND = "workhorse"
ADVISORY_KIND = "advisory"
JUDGEMENT_KIND = "judgement"
ROLE_KINDS = (WORKHORSE_KIND, ADVISORY_KIND, JUDGEMENT_KIND)

# The roles that MUST stay advisory: they are the correctness backstop for a
# pipeline where no human reads a diff. Demoting either is a config error.
BACKSTOP_ROLES = ("critic", "verifier")

# What `discovery.on_new_model` may say. `workhorse` is deliberately absent —
# auto-promoting a newly seen model onto the build path IS the 2026-08-09
# incident, so the schema rejects it rather than trusting a reviewer to notice.
# `judgement` is absent for the same reason and it matters MORE, not less: the
# planning ladder is the strongest one we run, so it is the most attractive
# place for an unattended promotion to land. A newly-discovered model may never
# reach a build OR a planning ladder by itself; a human editing
# config/models.yaml is the only way up.
DISCOVERY_TARGETS = (ADVISORY_KIND, "none")

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
    "kinds": {
        "workhorse": "workhorse",
        "advisory": "advisory",
        "judgement": "judgement",
    },
    "ladders": {
        "workhorse": ["claude-opus-5", "claude-sonnet-4-6"],
        "advisory": ["claude-sonnet-5", "claude-opus-5"],
        "judgement": ["claude-fable-5-1", "claude-opus-5", "claude-sonnet-4-6"],
    },
    "agents": {
        "engineer": "workhorse",
        "frontend": "workhorse",
        "database-architect": "workhorse",
        "devops": "workhorse",
        "fixer": "workhorse",
        "repairer": "workhorse",
        "planner": "judgement",
        "critic": "advisory",
        "verifier": "advisory",
        "medic": "advisory",
        "plan-critic-pre": "advisory",
        "plan-critic-post": "advisory",
    },
    "discovery": {"on_new_model": "advisory", "alert": True},
    "retired": ["claude-opus-4-8"],
    "excluded": ["claude-fable-5"],
}
# --- END generated model config ---


def _normalize_config(raw) -> dict | None:
    """Coerce a parsed config document into the plain-id shape above, or None if
    it is unusable. Ladder rungs may be bare ids or `{model:, reason:}` mappings
    (the canonical file uses the latter so each rung carries its justification);
    a kind may be a bare ladder name or a `{ladder:, …}` mapping.

    Normalization deliberately does NOT repair a policy violation — an agent
    pointed at something that is not a kind is carried through verbatim so
    `policy_errors` can REJECT the document. Silently dropping the entry would
    fall the role back to the default ladder and hide the exact class of edit
    DRE-2317 exists to catch.
    """
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

    kinds: dict[str, str] = {}
    for name, spec in (raw.get("kinds") or {}).items():
        if isinstance(spec, Mapping):
            ladder = spec.get("ladder")
        else:
            ladder = spec
        kinds[str(name)] = str(ladder) if ladder is not None else ""

    agents = {str(name): str(kind) for name, kind in (raw.get("agents") or {}).items()}

    raw_discovery = raw.get("discovery") or {}
    discovery = {
        "on_new_model": str(raw_discovery.get("on_new_model") or "").strip().lower(),
        "alert": bool(raw_discovery.get("alert")),
    }
    return {
        "default_ladder": default,
        "kinds": kinds,
        "ladders": ladders,
        "agents": agents,
        "discovery": discovery,
        "retired": ids(raw.get("retired")),
        "excluded": ids(raw.get("excluded")),
    }


# --------------------------------------------------------------------------- #
# Policy validation — the schema half of "availability is not permission"      #
# --------------------------------------------------------------------------- #

def policy_errors(config) -> list[str]:
    """Every way this config would put the strongest model on the build path,
    as plain-English strings. Empty list = the config is admissible.

    Accepts either a raw parsed `config/models.yaml` document or the normalized
    shape, so the CI check, the tests and the runtime loader all judge by ONE
    set of rules. It is a pure function: no I/O, no network, no import cycle.

    The rules, each one an edit that would recreate the 2026-08-09 outage:

      1. Exactly the declared kinds exist, and each names a real ladder that
         no other kind names — build work, advisory work and planning work
         each walk their own.
      2. Every role is assigned a KIND (not a ladder name, not an invented
         fourth thing).
      3. The critic and the verifier are advisory. They gate unattended merges;
         a demotion is the inversion this policy removes.
      4. The TOP RUNG of every non-workhorse ladder is absent from every
         workhorse ladder, and once such a ladder descends onto a workhorse
         model every rung below it is a workhorse model too. This is the
         incident condition: the model a judging kind reaches for must be
         unreachable from the build path at every availability, and it may not
         hide below the fallback where the first half of the rule cannot see
         it. The rungs BENEATH the top are deliberately shared — that is the
         DEGRADED fall onto the build model, which is the designed shape of
         both the advisory and the judgement ladder.
      5. `default_ladder` is the workhorse ladder, so an unrecognized role
         lands on the cheap side of the fence.
      6. `discovery.on_new_model` is `advisory` or `none`. `workhorse` — a
         newly seen model auto-joining the build path — is rejected outright,
         `judgement` with it (the planning ladder is the strongest one we run),
         and `discovery.alert` must be true: discovery is never silent.
      7. No `excluded` model appears on ANY ladder. Rule 4 only bars the top of
         a non-build ladder from the build path; when the advisory ladder moved
         off Fable (2026-08-12) that stopped covering Fable, and a config
         putting it back on the workhorse ladder validated clean. Exclusion is
         the decision "we do not run this at all" and has to be enforced on its
         own terms.
    """
    cfg = _normalize_config(config)
    if cfg is None:
        return ["config declares no usable ladders"]

    errors: list[str] = []
    kinds, ladders = cfg["kinds"], cfg["ladders"]

    if sorted(kinds) != sorted(ROLE_KINDS):
        # Spelled FROM `ROLE_KINDS`, never hand-written. This is the line a
        # maintainer reads when they typo a kind name, and a hand-written
        # enumeration of it went stale the day `judgement` arrived — telling
        # the reader the kind they meant had been removed rather than
        # mistyped (DRE-3015).
        every_kind = f"{', '.join(ROLE_KINDS[:-1])} or {ROLE_KINDS[-1]}"
        return [
            f"kinds: must declare exactly {list(ROLE_KINDS)}, got {sorted(kinds)} — "
            f"every role is {every_kind}, and nothing else"
        ]
    for kind, ladder in kinds.items():
        if ladder not in ladders:
            errors.append(f"kinds.{kind}: unknown ladder {ladder!r}")
    if errors:
        return errors
    if len(set(kinds.values())) != len(kinds):
        errors.append(
            "kinds: every kind needs its OWN ladder — sharing one puts the "
            f"strongest model on the hot path (got {kinds})"
        )

    for role, kind in cfg["agents"].items():
        if kind not in kinds:
            errors.append(
                f"agents.{role}: {kind!r} is not a role kind — roles are assigned "
                f"{list(ROLE_KINDS)}, never a ladder name"
            )
    for role in BACKSTOP_ROLES:
        assigned = cfg["agents"].get(role)
        if assigned is not None and assigned != ADVISORY_KIND:
            errors.append(
                f"agents.{role}: must be {ADVISORY_KIND!r} (it gates unattended "
                f"merges), got {assigned!r}"
            )

    workhorse_models = set(ladders.get(kinds[WORKHORSE_KIND], []))
    # Rule 4, for every kind that is not the build path. A non-workhorse ladder
    # has one shape: the model that kind is FOR on top, then the workhorse
    # rungs it degrades onto (loudly — see `selection_note`). So the top rung
    # is what the build path must not be able to reach, and everything from the
    # first workhorse rung down must stay workhorse: a premium model parked
    # BELOW the fallback would be invisible to the top-rung check and one
    # availability flip away from running.
    for kind, ladder_name in kinds.items():
        if kind == WORKHORSE_KIND:
            continue
        rungs = ladders.get(ladder_name, [])
        if len(rungs) > 1 and rungs[0] in workhorse_models:
            errors.append(
                f"ladders.{ladder_name}: {rungs[0]} tops the {kind} ladder and "
                "must not appear on a build ladder — availability is not "
                "permission (2026-08-09)"
            )
        descended = False
        for model in rungs:
            if model in workhorse_models:
                descended = True
            elif descended:
                errors.append(
                    f"ladders.{ladder_name}: {model} sits BELOW the workhorse "
                    f"fallback on the {kind} ladder — a stronger model may not "
                    "hide beneath the rung the ladder degrades onto"
                )
    # Rule 7 (2026-08-12): an EXCLUDED model is unreachable from every ladder.
    #
    # Rule 4 above bars the advisory model from the build path, which used to
    # cover Fable because Fable WAS the advisory model. When the advisory ladder
    # moved to Sonnet 5 on measured cost, Fable became `excluded` — and rule 4
    # stopped mentioning it, so a config putting Fable straight back on the
    # workhorse ladder validated clean. That is the 2026-08-09 incident
    # condition, unguarded. The two rules are deliberately independent: rule 4
    # is about the build/advisory fence, this is about a model we have decided
    # not to run at all, wherever it is listed.
    for name, models in ladders.items():
        for model in models:
            if model in set(cfg["excluded"]):
                errors.append(
                    f"ladders.{name}: {model} is excluded and must not appear on "
                    "any ladder — availability is not permission (2026-08-09)"
                )

    if cfg["default_ladder"] != kinds[WORKHORSE_KIND]:
        errors.append(
            f"default_ladder: must be the {WORKHORSE_KIND} ladder so an "
            "unrecognized role never reaches the advisory model"
        )

    target = cfg["discovery"]["on_new_model"]
    if target not in DISCOVERY_TARGETS:
        errors.append(
            f"discovery.on_new_model: {target!r} is not allowed (choose one of "
            f"{list(DISCOVERY_TARGETS)}). A newly seen model joining the "
            "workhorse ladder with no human deciding IS the 2026-08-09 "
            f"incident, and {JUDGEMENT_KIND!r} — the planning ladder, the "
            "strongest one we run — is refused for the same reason."
        )
    if not cfg["discovery"]["alert"]:
        errors.append("discovery.alert: must be true — discovery is never silent")
    return errors


def _load_config() -> dict:
    """The model config: the bundled YAML when readable AND admissible, else the
    generated literal above. Degrades, never raises — this sits on the dispatch
    path of every card, and a config we cannot parse must not block a build.

    A config that PARSES but violates policy is refused the same way: the fleet
    keeps running the last-known-good ladders and says so on stderr, rather than
    adopting the thing the policy exists to prevent.
    """
    try:
        import yaml  # PyYAML ships in the runner image; absence is degradable

        raw = yaml.safe_load(_CONFIG_PATH.read_text())
        parsed = _normalize_config(raw)
        if parsed:
            violations = policy_errors(raw)
            if not violations:
                return parsed
            print(
                f"model_fallback: REFUSING model config {_CONFIG_PATH} — "
                + "; ".join(violations)
                + "; using last-known-good fallback",
                file=sys.stderr,
            )
        else:
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

# Role kind → ladder name (DRE-2317, DRE-3015): `workhorse` → the build ladder,
# `advisory` → the reviewers', `judgement` → the planner's (the strongest
# model). One ladder each, enforced by policy_errors.
KINDS: dict[str, str] = CONFIG["kinds"]

# agent/role name → role KIND. No role hardcodes a model, and no role names a
# ladder directly: the critic, verifier and medic resolve theirs here too
# (DRE-2316), through the kind they are classified as (DRE-2317).
AGENT_KINDS: dict[str, str] = CONFIG["agents"]

# What happens to a model id we see but do not configure: `advisory` (may be
# proposed for the advisory ladder by a human) or `none`. Never `workhorse`.
DISCOVERY: dict = CONFIG["discovery"]

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


def kind_for(role: str) -> str:
    """The role KIND — `workhorse`, `advisory` or `judgement` — an agent/role is
    classified as. An unknown name is WORKHORSE: an unrecognized role must land
    on the cheap side of the fence, never on a stronger model."""
    kind = AGENT_KINDS.get(role or "", "")
    return kind if kind in KINDS else WORKHORSE_KIND


def ladder_for(role: str) -> list[str]:
    """The ordered ladder an agent/role walks: role → KIND → ladder. An unknown
    name gets the workhorse (default) ladder — select() must never block a
    build on a role it does not recognize, and must never promote one."""
    return LADDERS.get(KINDS.get(kind_for(role), ""), LADDER)

# Cache availability results so we don't probe on every dispatch. ~12 min keeps
# latency negligible across a burst of cards while picking up an Anthropic
# re-enable within one TTL window (auto-recovery).
#
# The cache is IN-PROCESS, which also makes it per-account by construction:
# accounts are per-repo, not one fleet-wide pool (bureau-pipeline's critic ran
# green right through the 2026-08-09 incident while two other repos' agents
# died). A shared health cache would let one repo's exhaustion decide another
# repo's model. Any future budget accounting must keep that property.
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


# Why a rung was passed over, in the words the heartbeat carries. The two
# outcomes are deliberately distinguishable: a CONFIRMED 404 means the model is
# gone for us, an inconclusive probe means we could not tell and refused to
# gamble the run on it.
_SKIP_UNAVAILABLE = "probe reported it unavailable (404 / not found)"
_SKIP_INCONCLUSIVE = "probe inconclusive — could not confirm it is up"


def select_with_reasons(
    role: str = "engineer", *, probe=None, clock=None, ladder=None
) -> dict:
    """The full selection DECISION, not just the answer (DRE-2317).

    Returns::

        {"role":…, "kind": "workhorse"|"advisory", "ladder": [ids…],
         "model": chosen, "skipped": [{"model":…, "reason":…}, …],
         "degraded": bool, "exhausted": bool}

    `skipped` lists every rung ABOVE the chosen one and why it was passed over.
    Recording the selection alone was never enough: a run that quietly dropped
    from the strongest model to the next rung looked identical to one that had
    the strongest model all along. That is the silent half of the 2026-08-09
    failure, and for an ADVISORY role it means a weakened critic nobody saw.

    `degraded` is True whenever anything above the chosen model was skipped.
    `exhausted` is True when NOTHING probed available and we fell through to the
    lowest rung rather than block the build.
    """
    if probe is None:
        probe = _probe_real
    if clock is None:
        clock = time.monotonic
    walk = _normalize_ladder(ladder) or ladder_for(role)

    skipped: list[dict[str, str]] = []
    for model in walk:
        available = _is_available(model, probe, clock)
        if available:
            return {
                "role": role,
                "kind": kind_for(role),
                "ladder": list(walk),
                "model": model,
                "skipped": skipped,
                "degraded": bool(skipped),
                "exhausted": False,
            }
        skipped.append(
            {
                "model": model,
                "reason": _SKIP_UNAVAILABLE if available is False else _SKIP_INCONCLUSIVE,
            }
        )

    # Nothing probed available — don't block the build. Fall through to the
    # last (lowest-ranked, broadest-availability) ladder model, and say so.
    return {
        "role": role,
        "kind": kind_for(role),
        "ladder": list(walk),
        "model": walk[-1],
        "skipped": skipped[:-1],
        "degraded": True,
        "exhausted": True,
    }


def selection_note(decision: Mapping) -> str:
    """The one-line, human-readable record of a decision: which model ran and
    why anything above it was skipped.

    ONE line, always — it rides a `$GITHUB_OUTPUT` assignment and a Linear
    heartbeat comment, and a second line would break both.

    A degraded decision is prefixed `DEGRADED` so the workflow can turn it into
    a `::warning::` with a shell `case`. That prefix is the alert half of the
    policy: if an advisory role ever falls off the strongest model — because it
    is unavailable, or because an advisory budget was introduced and exhausted —
    the run says so out loud instead of shipping a quietly cheaper critic.
    """
    model = decision.get("model")
    role = decision.get("role") or "agent"
    kind = decision.get("kind") or WORKHORSE_KIND
    head = "DEGRADED " if decision.get("degraded") else ""
    note = f"{head}model-policy: {model} chosen for {role} ({kind} kind)"
    skipped = list(decision.get("skipped") or [])
    if skipped:
        note += " — skipped " + "; ".join(
            f"{s['model']} ({s['reason']})" for s in skipped
        )
    else:
        note += " — top of the ladder, nothing above it was skipped"
    if decision.get("exhausted"):
        note += " — no rung probed available; fell through to the lowest rung"
    return " ".join(note.split())


def select(role: str = "engineer", *, probe=None, clock=None, ladder=None) -> str:
    """The model the next attempt should use: the first AVAILABLE model walking
    that agent's ordered ladder best→worst.

    `role` is an agent/role name from config/models.yaml (`engineer`, `planner`,
    `critic`, `verifier`, `medic`, …). Every build role is classified
    `workhorse` and walks that ladder; the advisory roles walk theirs and the
    planner walks the `judgement` one. An unrecognized name gets the workhorse
    ladder rather than an error.

    This is the answer only. `select_with_reasons()` returns the whole decision
    — including why each higher rung was skipped — and every workflow records
    that note; this wrapper stays for callers (and the console's vendored copy)
    that just want the id.

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
    return select_with_reasons(role, probe=probe, clock=clock, ladder=ladder)["model"]


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

      select [<agent>] [--explain-file <path>]
                                   print the model the next attempt should use
                                   (walks that agent's ladder from
                                   config/models.yaml, probes availability) and,
                                   with --explain-file, write the one-line
                                   selection note beside it
      role-of <label,label,...>    print engineer|planner|devops|frontend from
                                   a card's labels

    `select` probes the live API using the CLAUDE token in the env. `<agent>` is
    a name from config/models.yaml (`engineer`, `planner`, `critic`, `verifier`,
    `medic`, `fixer`, `repairer`, …); an unknown name walks the workhorse
    ladder.

    STDOUT IS ONLY THE MODEL ID — every workflow does `MODEL=$(… select …)`.
    The note (which model ran, and why anything above it was skipped) goes to
    the `--explain-file` path and to stderr, so adding it can never corrupt the
    captured id. A degraded selection's note starts with `DEGRADED`, which is
    what the workflows turn into a `::warning::`."""
    if not argv:
        print(
            "usage: model_fallback.py select [<agent>] [--explain-file <path>] "
            "| role-of <labels>"
        )
        return 2
    cmd, *rest = argv
    if cmd == "role-of":
        labels = (rest[0] if rest else "").split(",")
        print(_role_from_labels([l.strip() for l in labels if l.strip()]))
        return 0
    if cmd == "select":
        explain_path = None
        args: list[str] = []
        pending = list(rest)
        while pending:
            arg = pending.pop(0)
            if arg == "--explain-file":
                explain_path = pending.pop(0) if pending else None
            else:
                args.append(arg)
        # Ignore a legacy comments-file 2nd arg if the workflow still passes one
        # — selection no longer reads card history; availability drives it.
        role = args[0] if args else "engineer"
        clear_availability_cache()
        decision = select_with_reasons(role, probe=_fake_probe_from_env())
        note = selection_note(decision)
        print(decision["model"])
        print(note, file=sys.stderr)
        if explain_path:
            try:
                with open(explain_path, "w") as fh:
                    fh.write(note + "\n")
            except OSError as exc:  # a note we cannot write must not kill a run
                print(f"model_fallback: could not write {explain_path} ({exc})",
                      file=sys.stderr)
        return 0
    print(f"unknown command {cmd!r}")
    return 2


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
