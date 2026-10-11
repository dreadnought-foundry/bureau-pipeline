#!/usr/bin/env python3
"""Regenerate every mirror of config/models.yaml (DRE-2316).

``config/models.yaml`` is the ONE file a human edits to change which model an
agent runs on. Two places mirror it, and both are GENERATED regions delimited by
marker comments — never a hand edit:

  * ``scripts/model_fallback.py`` — ``_FALLBACK_MODEL_CONFIG``, the last-known-
    good literal the selector degrades to when the YAML is missing/unreadable
    (the same shape ``validate_card.py`` carries for ``config/repo-map.json``).
  * ``agents.yaml`` — each agent's ``model:`` line, which is the console
    roster's contract (the console reads the registry via the GitHub contents
    API; it does not read this config).

The ladders themselves live ONLY in the canonical file: the registry's 5x
copy-pasted ``ladder:`` block is gone.

A third target is a whole generated file (DRE-6635):

  * ``config/model-choices.json`` — what the console's Settings › Agents &
    models page reads: every priced model, every group with the choices it
    allows, every agent with the moves it allows, and the per-project
    exceptions. Every ``allowed`` / ``reason`` is ``policy_errors``' answer on
    a copy of the canonical config carrying that one option as a real entry,
    so the page is read off the gate a pull request meets. An agent's move is
    judged as ``model_config_change.py`` judges it, on the edit it would make,
    and a refused move publishes the plain line that script prints for it
    (DRE-6833).

Usage:

    python3 scripts/sync_model_config.py          # rewrite the regions in place
    python3 scripts/sync_model_config.py --check  # CI: exit 1 if stale, no write

Exit 0 → in sync (or written). Exit 1 → --check found a region stale.

This is the model-config sibling of scripts/sync_fallback_map.py and follows its
shape deliberately: canonical file, marked region, writer + ``--check``, and a
failure message that names the one repair command.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

# The policy rules live in ONE place (model_fallback.policy_errors) and are
# imported here rather than restated: two copies of "what a legal config is"
# drift the day one side gains a rule, and the whole point of DRE-2317 is that
# the rule cannot be edited away by accident.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_fallback import _normalize_config  # noqa: E402
from model_fallback import _normalize_separation  # noqa: E402
from model_fallback import declared_prices  # noqa: E402
from model_fallback import declared_repo_slugs  # noqa: E402
from model_fallback import policy_errors as _policy_errors  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "models.yaml"
FALLBACK_TARGET = ROOT / "scripts" / "model_fallback.py"
REGISTRY_TARGET = ROOT / "agents.yaml"
CHOICES_TARGET = ROOT / "config" / "model-choices.json"

_PY_BEGIN = "BEGIN generated model config"
_PY_END = "END generated model config"
_YAML_BEGIN = "BEGIN generated model"
_YAML_END = "END generated model"

REPAIR_COMMAND = "python3 scripts/sync_model_config.py"

# The slug a choice is probed under (DRE-6635). Injected into the repo slugs the
# probe is judged against, so it can never collide with a real project entry.
PROBE_SLUG = "__choice__"


# --------------------------------------------------------------------------- #
# Canonical config                                                             #
# --------------------------------------------------------------------------- #

def load_config(path: Path = CONFIG) -> dict:
    """The canonical config, normalized to plain ids: ladders as ordered model
    id lists, kinds as kind → ladder name, agents as role → KIND,
    retired/excluded as id lists.

    Schema validation runs here and is FATAL. The rules live in
    ``model_fallback.policy_errors`` — one definition, shared by the CI check,
    the runtime loader and the tests — so a config that would put the strongest
    model back on the build path (`on_new_model: workhorse` above all) fails
    the check before any mirror is even rendered.
    """
    import yaml

    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict) or not raw.get("ladders"):
        raise SystemExit(f"{path} is empty or declares no ladders")

    def ids(rungs) -> list[str]:
        out = []
        for rung in rungs or []:
            if isinstance(rung, str):
                out.append(rung)
            elif isinstance(rung, dict) and isinstance(rung.get("model"), str):
                out.append(rung["model"])
            else:
                raise SystemExit(f"{path}: bad ladder rung {rung!r}")
        return out

    # Judged against the prices declared BESIDE this config (DRE-6785): every
    # rung needs a declared price now, and a tree the adoption edited carries
    # the candidate's price in its own model-prices.yaml.
    beside = path.parent / "model-prices.yaml"
    violations = _policy_errors(raw, declared_prices(beside) if beside.exists() else None)
    if violations:
        raise SystemExit(
            f"{path}: rejected by model policy (DRE-2317)\n  - "
            + "\n  - ".join(violations)
        )

    ladders = {str(name): ids(rungs) for name, rungs in raw["ladders"].items()}
    default = str(raw.get("default_ladder") or next(iter(ladders)))
    if default not in ladders:
        raise SystemExit(f"{path}: default_ladder {default!r} is not a ladder")
    kinds = {}
    for name, spec in (raw.get("kinds") or {}).items():
        ladder = spec.get("ladder") if isinstance(spec, dict) else spec
        if str(ladder) not in ladders:
            raise SystemExit(f"{path}: kind {name!r} names unknown ladder {ladder!r}")
        kinds[str(name)] = str(ladder)
    agents = {str(k): str(v) for k, v in (raw.get("agents") or {}).items()}
    for name, kind in agents.items():
        if kind not in kinds:
            raise SystemExit(f"{path}: agent {name!r} names unknown kind {kind!r}")
    discovery = raw.get("discovery") or {}
    # The console's four keys (DRE-6633), through the one normalizer, so the
    # mirror gives the console the same groups, roster and per-project
    # exceptions a healthy checkout reads.
    console = _normalize_config(raw)
    return {
        "default_ladder": default,
        "kinds": kinds,
        "ladders": ladders,
        "agents": agents,
        # The declared effort level per model (DRE-4836). It rides into the
        # mirror with everything else: a checkout whose YAML will not parse
        # still runs the ladders, and a mirror that dropped the level would run
        # Opus 5.5 at its own `medium` default with nothing saying so.
        "effort": {
            str(model): str(level).strip().lower()
            for model, level in (raw.get("effort") or {}).items()
        },
        "discovery": {
            "on_new_model": str(discovery.get("on_new_model") or "").strip().lower(),
            "alert": bool(discovery.get("alert")),
        },
        # The build/review fence, normalized by the one normalizer (DRE-3880),
        # so the mirror the selector degrades to carries the SAME separation a
        # healthy checkout reads. A degrade that dropped it would review a
        # Sonnet-5 build on Sonnet 5 and look exactly like a healthy run.
        "review_separation": _normalize_separation(raw.get("review_separation")),
        "groups": console["groups"],
        "ungrouped": console["ungrouped"],
        "session_agents": console["session_agents"],
        "about": console["about"],
        "projects": console["projects"],
        "retired": ids(raw.get("retired")),
        "excluded": ids(raw.get("excluded")),
    }


# --------------------------------------------------------------------------- #
# Rendering                                                                    #
# --------------------------------------------------------------------------- #

def render_literal(cfg: dict) -> str:
    """The ``_FALLBACK_MODEL_CONFIG = {`` … ``}`` literal, in the canonical
    file's key order and the module's 4-space style — regenerating an in-sync
    region is a byte-for-byte no-op."""
    lines = ["_FALLBACK_MODEL_CONFIG = {"]
    lines.append(f'    "default_ladder": "{cfg["default_ladder"]}",')
    lines.append('    "kinds": {')
    for name, ladder in cfg["kinds"].items():
        lines.append(f'        "{name}": "{ladder}",')
    lines.append("    },")
    lines.append('    "ladders": {')
    for name, models in cfg["ladders"].items():
        rendered = ", ".join(f'"{m}"' for m in models)
        lines.append(f'        "{name}": [{rendered}],')
    lines.append("    },")
    lines.append('    "agents": {')
    for name, kind in cfg["agents"].items():
        lines.append(f'        "{name}": "{kind}",')
    lines.append("    },")
    lines.append('    "effort": {')
    for model, level in cfg["effort"].items():
        lines.append(f'        "{model}": "{level}",')
    lines.append("    },")
    lines.append(
        '    "discovery": {"on_new_model": '
        f'"{cfg["discovery"]["on_new_model"]}", "alert": '
        f'{bool(cfg["discovery"]["alert"])}}},'
    )
    # The rules go out COMPACT — {built_on: reviewers_use} — because the mirror
    # is read by `_normalize_separation`, not by a human, and the `reason:` a
    # rule carries is documentation that belongs in the canonical file.
    separation = cfg["review_separation"]
    lines.append('    "review_separation": {')
    rendered = ", ".join(f'"{r}"' for r in separation["roles"])
    lines.append(f'        "roles": [{rendered}],')
    lines.append('        "rules": {')
    for built_on, use in separation["rules"].items():
        lines.append(f'            "{built_on}": "{use}",')
    lines.append("        },")
    lines.append("    },")
    # The console's keys (DRE-6633), after the fence and in a fixed order.
    # Free text goes out through `_text`, so a line with a quote in it is still
    # a Python string literal.
    lines.append('    "groups": {')
    for kind, group in cfg["groups"].items():
        lines.append(
            f'        "{kind}": {{"label": {_text(group["label"])}, '
            f'"purpose": {_text(group["purpose"])}}},'
        )
    lines.append("    },")
    rendered = ", ".join(f'"{name}"' for name in cfg["ungrouped"])
    lines.append(f'    "ungrouped": [{rendered}],')
    rendered = ", ".join(f'"{name}"' for name in cfg["session_agents"])
    lines.append(f'    "session_agents": [{rendered}],')
    lines.append('    "about": {')
    for name, line in cfg["about"].items():
        lines.append(f'        "{name}": {_text(line)},')
    lines.append("    },")
    if cfg["projects"]:
        lines.append('    "projects": {')
        for slug, entry in cfg["projects"].items():
            rendered = ", ".join(f'"{kind}": "{model}"' for kind, model in entry.items())
            lines.append(f'        "{slug}": {{{rendered}}},')
        lines.append("    },")
    else:
        lines.append('    "projects": {},')
    for key in ("retired", "excluded"):
        rendered = ", ".join(f'"{m}"' for m in cfg[key])
        lines.append(f'    "{key}": [{rendered}],')
    lines.append("}")
    return "\n".join(lines)


def _text(value: str) -> str:
    """A double-quoted Python string literal for one line of free text."""
    return json.dumps(value, ensure_ascii=False)


def render_registry_model(cfg: dict, agent: str) -> str:
    """The generated body of one agent's model region in agents.yaml: the role
    KIND it is classified as, plus the top of that kind's ladder, with the
    ladder named inline so the roster still reads as documentation.

    The `kind:` line is what makes the workhorse/advisory split visible in the
    console roster — the roster is the surface a human reads to ask "what is
    the critic running on, and why is it allowed to be the expensive one?"
    """
    kind = cfg["agents"].get(agent) or ""
    ladder = cfg["kinds"].get(kind) or cfg["default_ladder"]
    models = cfg["ladders"][ladder]
    return (
        f"    kind: {kind}\n"
        f"    model: {models[0]}  # top of the `{ladder}` ladder"
    )


def render_choices(raw: dict, cfg: dict, prices: dict,
                   registry: set[str] | None = None) -> str:
    """config/model-choices.json, the console's contract (DRE-6635): key order
    fixed, 2-space indent, trailing newline.

    No rule is restated here. A group's choice is ``policy_errors``' answer on
    a deep copy of ``raw`` carrying that model as an exception under
    ``projects[PROBE_SLUG]``, filtered to that entry's own prefix so an error
    elsewhere in the config never reaches it.

    An agent's move is judged as the change script judges it (DRE-6833): every
    agent, grouped or not, gets one move per group it is not in, and each is
    ``model_config_change``'s pre-flight checks, else the errors its own edit
    (``edit_data`` of that ``move_agent``, which edits a deep copy of ``raw``)
    ADDS to the canonical config's. A bare ``agents[name] = group`` would leave an
    ungrouped agent in ``ungrouped`` too, which the validator refuses. A
    refused move's ``reason`` is the plain line the change script prints for
    it. ``registry`` is the agents.yaml names with a generated region, read
    off ``REGISTRY_TARGET`` when not given.
    """
    # The change script imports this module, so this one imports it late.
    import model_config_change as mcc

    if registry is None:
        registry = mcc.registry_agents(REGISTRY_TARGET.read_text(encoding="utf-8"))
    excluded = {
        entry["model"]: entry.get("reason")
        for entry in raw.get("excluded") or []
        if isinstance(entry, dict) and isinstance(entry.get("model"), str)
    }
    # Only a priced model is offered: a price is never invented.
    models = [
        {
            "id": model,
            "label": _model_label(model),
            "input": price["input"],
            "output": price["output"],
            "excluded": excluded.get(model),
        }
        for model, price in prices.items()
    ]

    probe_slugs = (declared_repo_slugs() or set()) | {PROBE_SLUG}
    groups = []
    for kind, words in cfg["groups"].items():
        ladder = cfg["ladders"][cfg["kinds"][kind]]
        prefix = f"projects.{PROBE_SLUG}.{kind}: "
        choices = []
        for model in models:
            trial = copy.deepcopy(raw)
            trial["projects"] = {PROBE_SLUG: {kind: model["id"]}}
            refused = [
                error[len(prefix):]
                for error in _policy_errors(trial, prices, repo_slugs=probe_slugs)
                if error.startswith(prefix)
            ]
            choices.append(_verdict("model", model["id"], refused))
        groups.append({
            "key": kind,
            "label": words["label"],
            "purpose": words["purpose"],
            "default_model": ladder[0],
            "fallbacks": ladder[1:],
            "choices": choices,
        })

    repo_slugs = declared_repo_slugs()
    baseline = set(_policy_errors(raw, prices, repo_slugs=repo_slugs))
    agents = []
    roster = [(name, kind) for name, kind in cfg["agents"].items()]
    roster += [(name, None) for name in cfg["ungrouped"]]
    for name, kind in roster:
        moves = []
        for other in cfg["groups"]:
            if other == kind:
                continue
            change = {"op": "move_agent", "agent": name, "group": other}
            refused = mcc._problems(change, raw, prices, registry) or [
                error
                for error in _policy_errors(
                    mcc.edit_data(raw, change, ""), prices, repo_slugs=repo_slugs)
                if error not in baseline
            ]
            moves.append({
                "group": other,
                "allowed": not refused,
                "reason": " ".join(mcc.plain_lines(change, refused, raw))
                if refused else None,
            })
        agents.append(_agent_entry(cfg, name, kind, moves))

    document = {
        "generated_from": "config/models.yaml",
        "models": models,
        "groups": groups,
        "agents": agents,
        "projects": [
            {"slug": slug, "exceptions": dict(entry)}
            for slug, entry in cfg["projects"].items()
        ],
    }
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


_MODEL_ID = re.compile(r"claude-([a-z]+)-(\d+)(?:-(\d+))?")


def _model_label(model: str) -> str:
    """`claude-<family>-<major>-<minor>` → `<Family> <major>.<minor>`
    (`claude-opus-5` → `Opus 5`). An id of any other shape is its own label."""
    match = _MODEL_ID.fullmatch(model)
    if not match:
        return model
    family, major, minor = match.groups()
    return f"{family.title()} {major}" + (f".{minor}" if minor else "")


def _verdict(key: str, value: str, refused: list[str]) -> dict:
    return {
        key: value,
        "allowed": not refused,
        "reason": "; ".join(refused) if refused else None,
    }


def _agent_entry(cfg: dict, name: str, kind: str | None, moves: list) -> dict:
    return {
        "name": name,
        "label": name.replace("-", " ").title(),
        "about": cfg["about"].get(name),
        "group": kind,
        # Where it runs (DRE-6833): `session` for a name in `session_agents`,
        # whose group the console shows and nothing selects on.
        "runs_in": "session" if name in cfg["session_agents"] else "pipeline",
        "moves": moves,
    }


# --------------------------------------------------------------------------- #
# Splicing                                                                     #
# --------------------------------------------------------------------------- #

def splice(text: str, body: str, begin: str, end: str, *, where: str) -> str:
    """Replace the lines strictly between the single BEGIN/END marker pair with
    ``body``, leaving the marker lines intact. Raises if the markers are missing
    — the region MUST be declared."""
    lines = text.splitlines(keepends=True)
    b = e = None
    for i, line in enumerate(lines):
        if begin in line:
            b = i
        elif end in line and b is not None:
            e = i
            break
    if b is None or e is None or e <= b:
        raise SystemExit(f"{where}: missing '{begin}' / '{end}' markers")
    return "".join(lines[: b + 1]) + body + "\n" + "".join(lines[e:])


def splice_registry(text: str, cfg: dict) -> str:
    """Rewrite EVERY per-agent generated model region in agents.yaml.

    Each region belongs to the agent whose ``- name:`` line most recently
    preceded it, so the writer never depends on entry order and an agent added
    to the registry without a config entry is a loud failure rather than a
    silent default.

    A session agent (``session_agents``, DRE-6833) has no region and needs
    none, in a group or not: it is never a pipeline role. One that HAS a
    region is refused — an agent runs in a pipeline workflow or in a person's
    session, never both.
    """
    session = set(cfg.get("session_agents") or [])
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    agent = None
    seen: list[str] = []
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("- name:"):
            agent = stripped.split(":", 1)[1].strip()
        if _YAML_BEGIN in line:
            if agent is None:
                raise SystemExit(
                    f"{REGISTRY_TARGET}: generated model region before any `- name:`"
                )
            if agent in session:
                raise SystemExit(
                    f"{REGISTRY_TARGET}: agent {agent!r} has a generated model "
                    f"region and is listed under `session_agents` in "
                    f"{CONFIG.name} — an agent runs in a pipeline workflow or in "
                    "a person's session, never both"
                )
            if agent not in cfg["agents"]:
                raise SystemExit(
                    f"{REGISTRY_TARGET}: agent {agent!r} has no entry in "
                    f"{CONFIG.name} — add one (ladder assignment) and rerun"
                )
            close = None
            for j in range(i + 1, len(lines)):
                if _YAML_END in lines[j]:
                    close = j
                    break
            if close is None:
                raise SystemExit(
                    f"{REGISTRY_TARGET}: unterminated '{_YAML_BEGIN}' region "
                    f"for agent {agent!r}"
                )
            out.append(line)
            out.append(render_registry_model(cfg, agent) + "\n")
            out.append(lines[close])
            seen.append(agent)
            i = close + 1
            continue
        out.append(line)
        i += 1
    missing = [a for a in cfg["agents"] if a not in seen and a not in session]
    if missing:
        raise SystemExit(
            f"{REGISTRY_TARGET}: no generated model region for {missing} — "
            f"every agent in {CONFIG.name} needs one"
        )
    return "".join(out)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="verify every mirror matches config/models.yaml; exit 1 if stale "
        "(do not write).",
    )
    args = ap.parse_args(argv)

    cfg = load_config()
    # The raw document, beside the normalized one: a choice is judged on a deep
    # copy of it carrying that one option (DRE-6635). load_config validated it.
    import yaml

    raw = yaml.safe_load(CONFIG.read_text())
    choices = render_choices(raw, cfg, declared_prices())
    targets = [
        (
            FALLBACK_TARGET,
            lambda text: splice(
                text,
                render_literal(cfg),
                _PY_BEGIN,
                _PY_END,
                where=str(FALLBACK_TARGET),
            ),
        ),
        (REGISTRY_TARGET, lambda text: splice_registry(text, cfg)),
        # A whole generated file, so a missing one is stale, not an error.
        (CHOICES_TARGET, lambda text: choices),
    ]

    stale = False
    for path, rewrite in targets:
        original = path.read_text(encoding="utf-8") if path.exists() else None
        updated = rewrite(original)
        rel = path.relative_to(ROOT)
        if updated == original:
            print(f"ok:    {rel}")
            continue
        if args.check:
            why = (
                "it is missing" if original is None
                else "its generated content does not match config/models.yaml"
            )
            print(
                f"STALE: {rel} — {why}; run `{REPAIR_COMMAND}` and commit the "
                "result.",
                file=sys.stderr,
            )
            stale = True
            continue
        path.write_text(updated, encoding="utf-8")
        print(f"wrote: {rel}")
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
