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
    `claude-opus-5`, falling back to `claude-sonnet-5` (DRE-3880 — it was
    `claude-sonnet-4-6` until 2026-09-16, which kept the judgement ladder's
    last rung and was not retired).
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

THE ONE DECLARED OVERLAP (DRE-3880, CEO decision 2026-09-16)
------------------------------------------------------------
`claude-sonnet-5` tops the ADVISORY ladder and backs the WORKHORSE one, so the
build/review fence is no longer bought by keeping those two lists disjoint. It
is bought at SELECTION time, per pull request: `select(role, built_on=…)` walks
the reviewer past the model the build ran on. `config/models.yaml`'s
`review_separation` block declares that, the schema permits the overlap ONLY
together with the declaration, and the generated mirror below carries it so a
truncated checkout separates too.

None of that touches the 2026-08-09 rule. The STRONGEST model we run — the top
of the judgement ladder — is still unreachable from a build ladder at every
availability, and the judgement ladder gets no such exemption: a declaration
buys nothing for the planner's model.

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

# The effort levels the CLI accepts (`claude --effort <level>`). A config
# naming anything else would hand the agent step an argument the CLI rejects,
# so the schema refuses it here rather than at the top of a build run.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# The canonical model config, bundled in this checkout (never a runtime lookup —
# the workflows that read it hold no cloud credentials and no private-repo
# token). config/README.md documents that constraint.
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "models.yaml"

# The DECLARED price of every model, same checkout, same constraint (DRE-3895).
# Policy validation reads it for one question — "is this rung dearer than the
# rung its ladder degrades onto?" — and a price is never guessed, so an id with
# no entry fails that question closed.
_PRICES_PATH = Path(__file__).resolve().parent.parent / "config" / "model-prices.yaml"

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
        "workhorse": ["claude-opus-5", "claude-sonnet-5"],
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
    "effort": {
    },
    "discovery": {"on_new_model": "advisory", "alert": True},
    "review_separation": {
        "roles": ["critic", "verifier"],
        "rules": {
            "claude-sonnet-5": "claude-opus-5",
        },
    },
    "retired": ["claude-opus-4-8"],
    "excluded": ["claude-fable-5", "claude-opus-5-5"],
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

    # The declared effort level per model (DRE-4836). Carried through verbatim,
    # exactly as `agents` is: a level `policy_errors` would refuse must reach
    # it to BE refused, and a silently dropped entry is a model running at its
    # own default with nothing saying so.
    effort = {
        str(model): str(level).strip().lower()
        for model, level in (raw.get("effort") or {}).items()
    }

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
        "effort": effort,
        "discovery": discovery,
        "review_separation": _normalize_separation(raw.get("review_separation")),
        "retired": ids(raw.get("retired")),
        "excluded": ids(raw.get("excluded")),
    }


def _normalize_separation(raw) -> dict:
    """`review_separation` in ONE shape: `{"declared": bool, "roles": [...],
    "rules": {built_on: reviewers_use}}` (DRE-3880).

    Two input shapes are accepted deliberately, so the canonical file, the
    generated mirror, the CI check and the tests all judge by one set of rules:
    the YAML's list of `{built_on:, reviewers_use:, reason:}` mappings, and the
    mirror's compact `{built_on: reviewers_use}` mapping.

    `declared` distinguishes "this config says nothing about separation" from
    "it declares none": the first is the edit that DELETED the block, and
    `policy_errors` has to be able to tell them apart to name what is missing.
    A malformed rule is carried through with an empty `reviewers_use` rather
    than dropped — dropping it would turn a broken declaration into a silently
    absent one, which is the bare overlap wearing a declaration.
    """
    if not isinstance(raw, Mapping):
        return {"declared": False, "roles": [], "rules": {}}
    roles = [str(r) for r in (raw.get("roles") or []) if isinstance(r, str)]
    rules: dict[str, str] = {}
    entries = raw.get("rules")
    if isinstance(entries, Mapping):
        for built_on, use in entries.items():
            rules[str(built_on)] = str(use) if isinstance(use, str) else ""
    else:
        for entry in entries or []:
            if not isinstance(entry, Mapping):
                continue
            built_on, use = entry.get("built_on"), entry.get("reviewers_use")
            if isinstance(built_on, str) and built_on:
                rules[built_on] = use if isinstance(use, str) else ""
    return {"declared": True, "roles": roles, "rules": rules}


def declared_prices(path: Path | None = None) -> dict[str, dict[str, float]]:
    """`{model_id: {"input": …, "output": …}}` from config/model-prices.yaml.

    A reader, not a validator — `model_adoption.load_prices` is the validator
    and raises on a half-declared entry; this one is on the dispatch path of
    every card, so it DEGRADES: an unreadable file, absent PyYAML or a
    half-declared entry all yield "no price for that id". That is fail-closed
    where it counts, because the one rule reading this refuses a rung it cannot
    show to be no dearer than the fallback.

    Deliberately not imported from `model_adoption`: that module imports this
    one, and an import cycle on the dispatch path is not worth ten lines.
    """
    global _PRICES_CACHE
    if path is None and _PRICES_CACHE is not None:
        return _PRICES_CACHE
    prices: dict[str, dict[str, float]] = {}
    try:
        import yaml

        raw = yaml.safe_load(Path(path or _PRICES_PATH).read_text())
        entries = raw.get("prices") if isinstance(raw, Mapping) else None
        for model_id, entry in (entries or {}).items():
            if not isinstance(entry, Mapping):
                continue
            side_in, side_out = entry.get("input"), entry.get("output")
            if any(
                isinstance(v, bool) or not isinstance(v, (int, float))
                for v in (side_in, side_out)
            ):
                continue
            prices[str(model_id)] = {
                "input": float(side_in), "output": float(side_out)
            }
    except Exception as exc:  # missing / unreadable / malformed / no PyYAML
        print(
            f"model_fallback: could not read declared prices {_PRICES_PATH} "
            f"({exc}); every rung below a ladder's fallback will be refused",
            file=sys.stderr,
        )
    if path is None:
        _PRICES_CACHE = prices
    return prices


# Read once per process: the file is small, but `policy_errors` is called on
# every dispatch and repeatedly across the suite's schema sweeps.
_PRICES_CACHE: dict[str, dict[str, float]] | None = None


# --------------------------------------------------------------------------- #
# Policy validation — the schema half of "availability is not permission"      #
# --------------------------------------------------------------------------- #

def policy_errors(config, prices=None) -> list[str]:
    """Every way this config would put the strongest model on the build path,
    as plain-English strings. Empty list = the config is admissible.

    Accepts either a raw parsed `config/models.yaml` document or the normalized
    shape, so the CI check, the tests and the runtime loader all judge by ONE
    set of rules. No network and no import cycle; the one thing it reads off
    disk is `config/model-prices.yaml` (rule 4b), through `declared_prices`,
    which degrades rather than raises — pass `prices` to supply them directly.

    The rules, each one an edit that would recreate the 2026-08-09 outage:

      1. Exactly the declared kinds exist, and each names a real ladder that
         no other kind names — build work, advisory work and planning work
         each walk their own.
      2. Every role is assigned a KIND (not a ladder name, not an invented
         fourth thing).
      3. The critic and the verifier are advisory. They gate unattended merges;
         a demotion is the inversion this policy removes.
      4. The TOP RUNG of every non-workhorse ladder is absent from every
         workhorse ladder. This is the incident condition: the model a judging
         kind reaches for must be unreachable from the build path at every
         availability. The rungs BENEATH the top are deliberately shared — that
         is the DEGRADED fall onto the build model, which is the designed shape
         of both the advisory and the judgement ladder.

         ONE exemption, and only for the ADVISORY kind (DRE-3880): the advisory
         top MAY be a workhorse rung when rule 8 below declares what the
         reviewers run instead. The judgement ladder gets no such exemption —
         a declaration is about REVIEWERS and buys nothing for the planner's
         model.
      4b. No rung below the one a non-workhorse ladder DEGRADES ONTO is dearer
         than that rung. This used to read "every rung below the fallback is a
         workhorse model", which was a proxy for the same thing and stopped
         working the moment `claude-sonnet-4-6` left the build ladder and
         stayed the planner's last resort. The question is the declared price
         (`config/model-prices.yaml`), and a rung with no declared price cannot
         be shown to be no dearer, so it is refused: a premium model parked
         below the fallback is invisible to rule 4 and one availability flip
         away from running.
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
      8. `review_separation` (DRE-3880) binds both the critic and the verifier,
         names only advisory roles, and carries exactly one rule per real
         overlap: `reviewers_use` is on the advisory ladder and is not the
         build model itself, and a rule describing an overlap that no longer
         exists is STALE and refused. The stale half is the DRE-3892 carry-
         forward: a same-family adoption moves the overlapping rung on both
         ladders at once, so an adoption that leaves the rule behind fails
         twice — the new rung is a bare overlap, and the old rule names a model
         that is no longer one.
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
    if prices is None:
        prices = declared_prices()

    # Rule 8 (DRE-3880), first — rule 4 reads its answer. The advisory ladder's
    # TOP rung is the only place an overlap is admissible, and only with a rule
    # that says what the reviewers run instead.
    advisory_ladder_name = kinds[ADVISORY_KIND]
    advisory_models = ladders.get(advisory_ladder_name, [])
    advisory_top = advisory_models[0] if advisory_models else None
    overlap = advisory_top if advisory_top in workhorse_models else None
    separation = cfg["review_separation"]
    declared: dict[str, str] = {}
    if separation["declared"]:
        for role in BACKSTOP_ROLES:
            if role not in separation["roles"]:
                errors.append(
                    f"review_separation.roles: must bind {role!r} — the "
                    "separation is what keeps a build off its own reviewer, "
                    "and a reviewer it does not name is not separated"
                )
        for role in separation["roles"]:
            if cfg["agents"].get(role) != ADVISORY_KIND:
                errors.append(
                    f"review_separation.roles: {role!r} is not an "
                    f"{ADVISORY_KIND} role — the separation binds the roles "
                    "that REVIEW a pull request, and binding a build role "
                    "would be a spend change wearing a safety rule"
                )
    for built_on, use in separation["rules"].items():
        if built_on != overlap:
            errors.append(
                f"review_separation.rules: {built_on} is not on both the "
                f"{advisory_ladder_name} ladder's top rung and a build ladder, "
                "so this rule describes an overlap that does not exist — a "
                "STALE rule is how the guarantee is lost without anybody "
                "editing it (DRE-3892)"
            )
        elif use == built_on:
            errors.append(
                f"review_separation.rules: {built_on} may not be reviewed on "
                f"{use} — a rule that sends the reviewer back to the model the "
                "build ran on is the overlap wearing a declaration"
            )
        elif use not in advisory_models:
            errors.append(
                f"review_separation.rules: {use} is not on the "
                f"{advisory_ladder_name} ladder, so the reviewers cannot "
                f"actually run on it when a build ran on {built_on}"
            )
        else:
            declared[built_on] = use
    if overlap is not None and overlap not in declared:
        errors.append(
            f"ladders.{advisory_ladder_name}: {overlap} tops the "
            f"{ADVISORY_KIND} ladder and also sits on a build ladder — a BARE "
            "overlap is refused. Declare it in `review_separation.rules` "
            "(which model the reviewers use when a build ran on it) or take it "
            "off one of the two ladders"
        )

    # Rule 4, for every kind that is not the build path. A non-workhorse ladder
    # has one shape: the model that kind is FOR on top, then the workhorse
    # rungs it degrades onto (loudly — see `selection_note`). So the top rung
    # is what the build path must not be able to reach, and nothing DEARER than
    # the rung it degrades onto may hide below that rung, where the top-rung
    # check cannot see it.
    for kind, ladder_name in kinds.items():
        if kind == WORKHORSE_KIND:
            continue
        rungs = ladders.get(ladder_name, [])
        # The advisory overlap is reported once, above, with the repair in it —
        # `declared` is exactly the set rule 8 admitted.
        if (
            len(rungs) > 1
            and rungs[0] in workhorse_models
            and not (kind == ADVISORY_KIND and rungs[0] in declared)
        ):
            errors.append(
                f"ladders.{ladder_name}: {rungs[0]} tops the {kind} ladder and "
                "must not appear on a build ladder — availability is not "
                "permission (2026-08-09)"
            )
        # Rule 4b: the fallback is the FIRST workhorse rung this ladder reaches,
        # and everything under it has to be provably no dearer.
        fallback = None
        for model in rungs:
            if model in workhorse_models:
                if fallback is None:
                    fallback = model
                continue
            if fallback is None:
                continue
            here, onto = prices.get(model), prices.get(fallback)
            if here is None or onto is None:
                unpriced = model if here is None else fallback
                errors.append(
                    f"ladders.{ladder_name}: {model} sits BELOW the {fallback} "
                    f"rung the {kind} ladder degrades onto and "
                    f"config/model-prices.yaml declares no price for "
                    f"{unpriced} — a price is never guessed, so a rung that "
                    "cannot be shown to be no dearer is refused"
                )
            elif here["input"] > onto["input"] or here["output"] > onto["output"]:
                errors.append(
                    f"ladders.{ladder_name}: {model} sits BELOW the {fallback} "
                    f"rung the {kind} ladder degrades onto and is DEARER "
                    f"(${here['input']:.2f}/${here['output']:.2f} against "
                    f"${onto['input']:.2f}/${onto['output']:.2f} per MTok) — a "
                    "stronger model may not hide beneath the fallback"
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

    # Rule 9 (DRE-4836): a declared effort level is real and reaches something.
    # Both halves fail the same way in production — an unknown level is an
    # argument the CLI rejects at the top of a build run, and an id no ladder
    # names is a level nothing applies, whose likeliest cause is a typo in the
    # id the fleet is meant to be running at that level.
    on_a_ladder = {m for models in ladders.values() for m in models}
    for model, level in cfg["effort"].items():
        if level not in EFFORT_LEVELS:
            errors.append(
                f"effort.{model}: {level!r} is not an effort level (choose one "
                f"of {list(EFFORT_LEVELS)}) — the CLI refuses anything else"
            )
        if model not in on_a_ladder:
            errors.append(
                f"effort.{model}: names a model that is on no ladder, so the "
                "level applies to nothing — check the id"
            )
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

# model id → the effort level every run on it asks for (DRE-4836). Read by
# `effort_for`; a model absent from it runs with no `--effort` argument.
EFFORT: dict[str, str] = CONFIG["effort"]

# The build/review fence (DRE-3880): `{"declared": bool, "roles": [critic,
# verifier], "rules": {built_on: reviewers_use}}`. `select(..., built_on=…)`
# reads it, and `policy_errors` refuses a ladder overlap that is not in it.
SEPARATION: dict = CONFIG["review_separation"]

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
_ATTEMPT_MARKER_RE = re.compile(
    re.escape(MARKER_PREFIX) + r"\s*([a-z0-9.-]+)", re.IGNORECASE
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
# DRE-3824. Worded WITHOUT the death-marker prefix on purpose: this note rides
# the planner heartbeat comment, and a marker substring in it would be counted
# as a death by the next attempt's read.
_SKIP_DIED = "died on this card's last attempt (is_error death recorded)"
# DRE-3970. A rung this RUN already saw refused for capacity (a spend limit, a
# usage limit, a rate limit, overload). Not a death on the card — nothing was
# attempted on it — so it says so in its own words, and it carries no marker.
_SKIP_CAPACITY = (
    "out of capacity this run (refused on a spend, usage or rate limit, or "
    "overloaded)"
)


# --------------------------------------------------------------------------- #
# Out of capacity (DRE-3970)                                                   #
# --------------------------------------------------------------------------- #

# What a model says when it refuses for CAPACITY rather than for anything about
# the request. Lower-cased; matched as substrings. The first is the sentence
# `claude-fable-5-1` refused every planning call with on 2026-09-12/13/14
# ("You've hit your monthly spend limit. Switch to another model to continue."
# — portico run 34924370626), and the vendor names the remedy in the second.
# The rest are the account usage limit as claude-code-action and the CLI word
# it, and the API's own error types for a rate limit and for overload.
CAPACITY_SIGNATURES = (
    "monthly spend limit",
    "switch to another model",
    "hit your limit",
    "usage limit reached",
    # The CLI's own sentences for the same wall, read out of the installed
    # Claude Code 2.1.271 binary's strings (DRE-3970).
    "fable limit",
    "out of usage credits",
    "shared budget",
    "rate_limit_error",
    "overloaded_error",
)
# The turn ceiling, spelled as check_agent_result spells it (not imported: this
# module is vendored and imports nothing of ours). A run that reached it did
# real work and did not meet a capacity wall on its first call.
_TURN_CAP_WORDS = ("maximum number of turns", "error_max_turns")


def capacity_refusal(text: str | None, record: Mapping | None = None) -> str | None:
    """The capacity signature `text` carries, or None (DRE-3970).

    Answers ONE question: was this model refused because it is out of capacity,
    so that the SAME step may ask the next rung instead? A spend limit, a usage
    limit, a rate limit or overload is. Everything else — a bad credential, a
    5xx, a timeout — is not, and gets no second call.

    A run that did real work is NEVER read as one, whatever its text says,
    because the words are ordinary English an agent writes after merely
    reading the standard that quotes them (DRE-3499). The veto reads the
    execution `record` when there is one — the turn-cap subtype, more than one
    turn, any spend — and the turn-cap sentence in the text either way.
    """
    lowered = (text or "").lower()
    if any(word in lowered for word in _TURN_CAP_WORDS):
        return None
    if isinstance(record, Mapping):
        if str(record.get("subtype") or "").strip() in _TURN_CAP_WORDS:
            return None
        turns, cost = record.get("num_turns"), record.get("total_cost_usd")
        if isinstance(turns, (int, float)) and not isinstance(turns, bool) and turns > 1:
            return None
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0:
            return None
    for signature in CAPACITY_SIGNATURES:
        if signature in lowered:
            return signature
    return None


def effort_for(model: str) -> str | None:
    """The effort level a run on `model` asks for, or None when this config
    declares none (DRE-4836).

    Claude Opus 5.5 defaults to `medium`, one level below Claude Opus 5's
    `high`, so the level is set EXPLICITLY wherever the fleet runs it — the
    CEO's decision of 2026-09-24. Every other model is left alone: None means
    the step passes no `--effort` argument at all, so adopting one model's
    level never moves another model's spend.

    Read off the config, never a literal, for the same reason the model id is
    (DRE-2316) — the level is a spend decision and it is made in
    config/models.yaml, in a reviewed PR.
    """
    level = EFFORT.get(model or "")
    return level if level in EFFORT_LEVELS else None


def fallback_for(role: str, model: str) -> str | None:
    """The rung directly below `model` on `role`'s ladder, or None when it is
    the last rung or not on the ladder at all (DRE-3970). What a step hands the
    CLI as `--fallback-model` — read off the reviewed ladder, never a literal."""
    walk = ladder_for(role)
    if model not in walk:
        return None
    at = walk.index(model)
    return walk[at + 1] if at + 1 < len(walk) else None


def separated_walk(role: str, walk: list[str], built_on=None, built_on_unknown=False):
    """`(walk, separated)` — the ladder a REVIEWER walks once the build/review
    separation has been applied, and the rungs it removed (DRE-3880).

    `built_on` is the model the pull request in front of this reviewer was
    BUILT on, read off the card's own `model-attempt:` marker. When a declared
    rule covers it, that rung comes off this reviewer's walk and the model the
    rule names LEADS what is left: the declaration says what the reviewers run,
    so a ladder that happened to keep something above it would be ignoring it.

    `built_on_unknown` fails CLOSED: a card we could not read is not evidence
    the build ran on something else, so every declared overlap comes off. The
    guarantee has to survive a Linear blip.

    Roles the separation does not bind — the medic, every build role — walk
    their ladder untouched. So does a reviewer looking at a build that ran on a
    model no rule names, which is the ordinary Opus case.
    """
    rules = SEPARATION["rules"]
    if role not in SEPARATION["roles"] or not rules:
        return list(walk), []
    if built_on_unknown:
        barred = {m for m in walk if m in rules}
    else:
        barred = {built_on} & set(rules) if built_on else set()
    if not barred:
        return list(walk), []
    separated = [m for m in walk if m in barred]
    kept = [m for m in walk if m not in barred]
    lead = [rules[m] for m in separated if rules[m] in kept]
    reordered = list(dict.fromkeys(lead)) + [m for m in kept if m not in lead]
    # Defensive: the schema guarantees `reviewers_use` is a different rung on
    # this same ladder, so `reordered` is non-empty for any config that
    # validated. A config that did NOT must still not strand a dispatch.
    return (reordered, separated) if reordered else (list(walk), [])


def select_with_reasons(
    role: str = "engineer", *, probe=None, clock=None, ladder=None, avoid=(),
    out_of_capacity=(), built_on=None, built_on_unknown=False,
) -> dict:
    """The full selection DECISION, not just the answer (DRE-2317).

    Returns::

        {"role":…, "kind": "workhorse"|"advisory", "ladder": [ids…],
         "model": chosen, "skipped": [{"model":…, "reason":…}, …],
         "separated": [ids…], "built_on_unknown": bool,
         "degraded": bool, "exhausted": bool}

    `skipped` lists every rung ABOVE the chosen one and why it was passed over.
    Recording the selection alone was never enough: a run that quietly dropped
    from the strongest model to the next rung looked identical to one that had
    the strongest model all along. That is the silent half of the 2026-08-09
    failure, and for an ADVISORY role it means a weakened critic nobody saw.

    `degraded` is True whenever anything above the chosen model was skipped.
    `exhausted` is True when NOTHING probed available and we fell through to the
    lowest rung rather than block the build.

    `avoid` names rungs to walk past WITHOUT probing — models the caller has
    evidence just died for this card (DRE-3824). The probe cannot see a refusal:
    in subscription mode raw /v1/messages answers 429 for every model, which
    reads "available", so a model refusing every run on a monthly spend limit
    kept being chosen. The caller reads the card's own `model-error:` marker and
    passes that model here. An avoided rung is a skip like any other — degraded,
    named in the note — and avoiding every rung still falls through to the
    lowest one rather than block the run.

    `out_of_capacity` (DRE-3970) names rungs this RUN has already seen refused
    for capacity — the classifier's `fell_from`. Walked past without probing,
    exactly like `avoid`, but with its own reason: nothing died on the card.

    `built_on` / `built_on_unknown` (DRE-3880) are the build/review separation,
    and they are NOT a skip: the rungs `separated_walk` removes never enter the
    walk, are reported in `separated` rather than `skipped`, and leave
    `degraded` alone. Falling off the top of a ladder is a run worth looking
    at; running the reviewer on a different model than the build did is the
    DESIGNED path, and a ::warning:: on every Sonnet-5 build would teach the
    fleet to ignore the warning that means a reviewer lost its model.
    """
    if probe is None:
        probe = _probe_real
    if clock is None:
        clock = time.monotonic
    walk = _normalize_ladder(ladder) or ladder_for(role)
    walk, separated = separated_walk(role, walk, built_on, built_on_unknown)
    avoided = {m for m in (avoid or ()) if isinstance(m, str) and m}
    refused = {m for m in (out_of_capacity or ()) if isinstance(m, str) and m}

    skipped: list[dict[str, str]] = []
    for model in walk:
        if model in refused:
            skipped.append({"model": model, "reason": _SKIP_CAPACITY})
            continue
        if model in avoided:
            skipped.append({"model": model, "reason": _SKIP_DIED})
            continue
        available = _is_available(model, probe, clock)
        if available:
            return {
                "role": role,
                "kind": kind_for(role),
                "ladder": list(walk),
                "model": model,
                "skipped": skipped,
                "separated": separated,
                "built_on_unknown": bool(built_on_unknown),
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
        "separated": separated,
        "built_on_unknown": bool(built_on_unknown),
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
    # The separation is recorded but is NOT a degradation (DRE-3880): it is
    # named here so a verdict can be traced to the model that wrote it, and it
    # never adds the DEGRADED prefix — see `select_with_reasons`.
    separated = list(decision.get("separated") or [])
    if separated:
        joined = ", ".join(separated)
        note += (
            " — build/review separation: this build's model could not be read, "
            f"so the reviewer skips every declared overlap ({joined})"
            if decision.get("built_on_unknown")
            else f" — build/review separation: this build ran on {joined}, so "
            "the reviewer does not"
        )
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


def select(
    role: str = "engineer", *, probe=None, clock=None, ladder=None, avoid=(),
    out_of_capacity=(), built_on=None, built_on_unknown=False,
) -> str:
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

    `built_on` is the model the pull request under review was BUILT on, and
    `built_on_unknown` says we could not find out (DRE-3880). For a role the
    `review_separation` block binds — the critic and the verifier — a declared
    overlap comes off the walk, so a Sonnet-5 build is never reviewed by Sonnet
    5 at any availability. Every other role ignores both.
    """
    return select_with_reasons(
        role, probe=probe, clock=clock, ladder=ladder, avoid=avoid,
        out_of_capacity=out_of_capacity, built_on=built_on,
        built_on_unknown=built_on_unknown,
    )["model"]


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


def last_attempt_model(comment_bodies) -> str | None:
    """The model id from the MOST RECENT attempt heartbeat, or None (DRE-3880).

    This is where "what did the build run on?" is answered: the heartbeat is
    already written on every attempt, so the reviewer reads the build's model
    off the card rather than out of a second record that could disagree with
    it. Same shape as `last_error_model` — oldest→newest in, scanned from the
    end, and only a KNOWN model id counts, so an unrecognized payload reads as
    "we do not know" (which the caller fails CLOSED on) rather than as a model.

    A `model-error:` marker is NOT an attempt: a death says which model died,
    not which one the build that produced this diff ran on.
    """
    for body in reversed(list(comment_bodies or [])):
        for found in _ATTEMPT_MARKER_RE.findall(body or ""):
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


def _build_model_from_thread(path: str) -> str | None:
    """The build's model out of a dumped comment thread, or None (DRE-3880).

    Reads the two shapes `linear_ops.py dump-comments` writes: a JSON array of
    bodies, and the `--with-authors` array of records. DEGRADES to None on a
    missing file, unreadable JSON or an unexpected shape — the caller's answer
    is then `--built-on-unknown`, which bars every declared overlap, so a
    Linear outage costs a cheaper reviewer and never a failed review job.
    """
    import json

    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, list):
        return None
    bodies = [
        entry if isinstance(entry, str)
        else entry.get("body") if isinstance(entry, Mapping)
        else None
        for entry in data
    ]
    return last_attempt_model(bodies)


def main(argv: list[str]) -> int:
    """CLI for the workflows — the entry point every agent workflow calls.

      select [<agent>] [--explain-file <path>] [--avoid <model>]...
             [--out-of-capacity <model>]... [--fallback-file <path>]
             [--effort-file <path>] [--built-on <model>] [--built-on-unknown]
                                   print the model the next attempt should use
                                   (walks that agent's ladder from
                                   config/models.yaml, probes availability) and,
                                   with --explain-file, write the one-line
                                   selection note beside it. --avoid walks past
                                   a model this card's last attempt died on
                                   (DRE-3824), without probing it;
                                   --out-of-capacity walks past a model this
                                   run saw refused for capacity (DRE-3970);
                                   --fallback-file writes the rung below the
                                   chosen one (empty when there is none);
                                   --effort-file writes the effort level the
                                   CHOSEN model runs at, empty when it declares
                                   none (DRE-4836);
                                   --built-on names the model the pull request
                                   under review was BUILT on and
                                   --built-on-unknown says we could not find
                                   out, which fails closed (DRE-3880)
      effort <model>               print the effort level that model runs at,
                                   or NOTHING when it declares none (DRE-4836).
                                   Exit 0 either way: the caller writes
                                   `effort_arg=${EFFORT:+--effort $EFFORT}`, so
                                   an empty answer is "pass no argument"
      build-model <comments.json>  print the model the build ran on, read from
                                   the card's own attempt heartbeats — the JSON
                                   array `linear_ops.py dump-comments` writes.
                                   An unreadable thread prints NOTHING and
                                   exits 0: the caller's answer is then
                                   --built-on-unknown, which is the fail-closed
                                   one, so a Linear blip must not fail the job
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
            "[--built-on <model>|--built-on-unknown] "
            "| build-model <comments.json> | role-of <labels>"
        )
        return 2
    cmd, *rest = argv
    if cmd == "role-of":
        labels = (rest[0] if rest else "").split(",")
        print(_role_from_labels([l.strip() for l in labels if l.strip()]))
        return 0
    if cmd == "build-model":
        print(_build_model_from_thread(rest[0] if rest else "") or "")
        return 0
    if cmd == "effort":
        print(effort_for(rest[0] if rest else "") or "")
        return 0
    if cmd == "select":
        explain_path = None
        avoid: list[str] = []
        refused: list[str] = []
        fallback_path = None
        effort_path = None
        built_on = None
        built_on_unknown = False
        args: list[str] = []
        pending = list(rest)
        while pending:
            arg = pending.pop(0)
            if arg == "--explain-file":
                explain_path = pending.pop(0) if pending else None
            elif arg == "--avoid":
                avoid.append(pending.pop(0) if pending else "")
            elif arg == "--out-of-capacity":
                refused.append(pending.pop(0) if pending else "")
            elif arg == "--fallback-file":
                fallback_path = pending.pop(0) if pending else None
            elif arg == "--effort-file":
                effort_path = pending.pop(0) if pending else None
            elif arg == "--built-on":
                built_on = pending.pop(0) if pending else None
            elif arg == "--built-on-unknown":
                built_on_unknown = True
            else:
                args.append(arg)
        # Ignore a legacy comments-file 2nd arg if the workflow still passes one
        # — selection no longer reads card history; availability drives it.
        role = args[0] if args else "engineer"
        clear_availability_cache()
        decision = select_with_reasons(
            role, probe=_fake_probe_from_env(), avoid=avoid,
            out_of_capacity=refused, built_on=built_on,
            built_on_unknown=built_on_unknown,
        )
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
        if fallback_path:
            try:
                with open(fallback_path, "w") as fh:
                    fh.write((fallback_for(role, decision["model"]) or "") + "\n")
            except OSError as exc:  # same rule as the note
                print(f"model_fallback: could not write {fallback_path} ({exc})",
                      file=sys.stderr)
        if effort_path:
            # The level of the model actually CHOSEN, never the ladder's top:
            # a run that fell to another rung takes that rung's level, which
            # for every model but Opus 5.5 is none at all (DRE-4836).
            try:
                with open(effort_path, "w") as fh:
                    fh.write((effort_for(decision["model"]) or "") + "\n")
            except OSError as exc:  # same rule as the note
                print(f"model_fallback: could not write {effort_path} ({exc})",
                      file=sys.stderr)
        return 0
    print(f"unknown command {cmd!r}")
    return 2


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
