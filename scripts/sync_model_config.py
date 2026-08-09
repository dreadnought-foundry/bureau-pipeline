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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "models.yaml"
FALLBACK_TARGET = ROOT / "scripts" / "model_fallback.py"
REGISTRY_TARGET = ROOT / "agents.yaml"

_PY_BEGIN = "BEGIN generated model config"
_PY_END = "END generated model config"
_YAML_BEGIN = "BEGIN generated model"
_YAML_END = "END generated model"

REPAIR_COMMAND = "python3 scripts/sync_model_config.py"


# --------------------------------------------------------------------------- #
# Canonical config                                                             #
# --------------------------------------------------------------------------- #

def load_config(path: Path = CONFIG) -> dict:
    """The canonical config, normalized to plain ids: ladders as ordered model
    id lists, agents as name → ladder name, retired/excluded as id lists."""
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

    ladders = {str(name): ids(rungs) for name, rungs in raw["ladders"].items()}
    default = str(raw.get("default_ladder") or next(iter(ladders)))
    if default not in ladders:
        raise SystemExit(f"{path}: default_ladder {default!r} is not a ladder")
    agents = {str(k): str(v) for k, v in (raw.get("agents") or {}).items()}
    for name, ladder in agents.items():
        if ladder not in ladders:
            raise SystemExit(f"{path}: agent {name!r} names unknown ladder {ladder!r}")
    return {
        "default_ladder": default,
        "ladders": ladders,
        "agents": agents,
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
    lines.append('    "ladders": {')
    for name, models in cfg["ladders"].items():
        rendered = ", ".join(f'"{m}"' for m in models)
        lines.append(f'        "{name}": [{rendered}],')
    lines.append("    },")
    lines.append('    "agents": {')
    for name, ladder in cfg["agents"].items():
        lines.append(f'        "{name}": "{ladder}",')
    lines.append("    },")
    for key in ("retired", "excluded"):
        rendered = ", ".join(f'"{m}"' for m in cfg[key])
        lines.append(f'    "{key}": [{rendered}],')
    lines.append("}")
    return "\n".join(lines)


def render_registry_model(cfg: dict, agent: str) -> str:
    """The generated body of one agent's ``model:`` region in agents.yaml: the
    top of that agent's configured ladder, with the ladder named inline so the
    roster still reads as documentation."""
    ladder = cfg["agents"].get(agent) or cfg["default_ladder"]
    models = cfg["ladders"][ladder]
    return f"    model: {models[0]}  # top of the `{ladder}` ladder"


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
    """
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
    missing = [a for a in cfg["agents"] if a not in seen]
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
    ]

    stale = False
    for path, rewrite in targets:
        original = path.read_text()
        updated = rewrite(original)
        rel = path.relative_to(ROOT)
        if updated == original:
            print(f"ok:    {rel}")
            continue
        if args.check:
            print(
                f"STALE: {rel} — its generated region does not match "
                f"config/models.yaml; run `{REPAIR_COMMAND}` and commit the "
                "result.",
                file=sys.stderr,
            )
            stale = True
            continue
        path.write_text(updated)
        print(f"wrote: {rel}")
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
