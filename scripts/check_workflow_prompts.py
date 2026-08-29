#!/usr/bin/env python3
"""Inline agent prompts are agent instructions too — check their lanes (DRE-2727).

`briefs/` and `standards/` are the obvious surface for "what the agents are
told". They miss a whole class: prompts written in prose inside a `prompt: |`
block in `.github/workflows/`. It is not a brief and not a standard, so nothing
else in the repo reaches it — and a prompt that names a lane the board no longer
has sends its agent looking somewhere real cards are not.

The live example: medic.yml told its agent to search Linear for an existing
failure card in Triage, Todo or In Progress before creating a new one. Point the
create seam at a lane outside that list and a repeatedly-failing workflow mints a
fresh card on every failure instead of commenting on the one that exists.

WHAT MAKES THIS SURVIVE THE *NEXT* RENAME, not just the last one: the denylist is
fed from `config/lane-contract.json`'s own `aliases` block, which is where a lane
rename is already required to record the retired name ("this is the ONE place the
retired name survives"). Renaming a lane through the house mechanism therefore
arms this check automatically, with no edit here.

WHY NO NAME IS HARD-CODED IN THIS FILE. A lane that was deleted OUTRIGHT rather
than aliased is remembered in exactly one place — the tests. The contract may not
spell such a name (tests/test_lane_contract.py::test_the_contract_file_spells_
neither_name_anywhere) and neither may anything under scripts/ or
.github/workflows/ (tests/test_lane_contract_conformance.py::test_the_live_
pipeline_names_neither_retired_lane, which reads Python as an AST, so a docstring
counts). So `lanes_that_do_not_exist` takes an `extra` argument and the test suite
passes its own list in; that half of the check runs in pytest, which is the same
CI job as this step.

stdlib only, and regex rather than a YAML parse: this runs in CI next to the
other check_*.py steps, where PyYAML is installed, but it must stay importable
from a product-repo agent job where it is not.

Usage:

    python3 scripts/check_workflow_prompts.py            # CI: exit 1 on findings
    python3 scripts/check_workflow_prompts.py --list     # the enumeration
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "config" / "lane-contract.json"
WORKFLOWS = ROOT / ".github" / "workflows"

_PROMPT = re.compile(r"^(?P<indent>\s*)prompt:\s*\|")


@dataclass(frozen=True)
class Prompt:
    """One inline agent prompt: which workflow, which line, and its prose."""

    workflow: str
    line: int
    body: str


@dataclass(frozen=True)
class Finding:
    prompt: Prompt
    lane: str

    def __str__(self) -> str:
        return (
            f".github/workflows/{self.prompt.workflow}:{self.prompt.line}: the "
            f"agent prompt names {self.lane!r}, which is not a live lane in "
            "config/lane-contract.json"
        )


def _contract() -> dict:
    with open(CONTRACT, encoding="utf-8") as fh:
        return json.load(fh)


def live_lanes(contract: dict | None = None) -> list[str]:
    doc = _contract() if contract is None else contract
    return [lane["name"] for lane in doc["lanes"] if lane.get("status") == "live"]


def not_lanes(contract: dict | None = None, extra: Iterable[str] = ()) -> list[str]:
    """Names a prompt must never use: every rename alias the contract declares,
    plus whatever the caller adds (see the module docstring on why the
    deleted-outright names are supplied by the tests rather than held here).

    An alias that has since been RE-ADOPTED as a live lane name is not a
    finding — the board has it, so a prompt may name it.
    """
    doc = _contract() if contract is None else contract
    aliases = [entry["from"] for entry in doc["aliases"]["entries"]]
    live = set(live_lanes(doc))
    return [name for name in list(aliases) + list(extra) if name not in live]


def lanes_that_do_not_exist(
    body: str, contract: dict | None = None, extra: Iterable[str] = ()
) -> list[str]:
    """The dead lane names this prose mentions, in the order they are declared."""
    return [
        name
        for name in not_lanes(contract, extra)
        if re.search(rf"\b{re.escape(name)}\b", body)
    ]


def prompts(workflows: Path | None = None) -> list[Prompt]:
    """Every `prompt: |` block in every workflow, with its body dedented enough
    to read. Block scalars end at the first non-blank line indented at or below
    the key — the same rule the YAML loader applies."""
    directory = WORKFLOWS if workflows is None else workflows
    found: list[Prompt] = []
    for path in sorted(directory.glob("*.yml")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            match = _PROMPT.match(line)
            if not match:
                continue
            indent = len(match.group("indent"))
            body: list[str] = []
            for following in lines[index + 1 :]:
                if following.strip() and len(following) - len(following.lstrip()) <= indent:
                    break
                body.append(following)
            found.append(Prompt(path.name, index + 1, "\n".join(body)))
    return found


def findings(workflows: Path | None = None, extra: Iterable[str] = ()) -> list[Finding]:
    contract = _contract()
    extra = list(extra)
    return [
        Finding(prompt, lane)
        for prompt in prompts(workflows)
        for lane in lanes_that_do_not_exist(prompt.body, contract, extra)
    ]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    found = prompts()

    if "--list" in argv:
        for prompt in found:
            lanes = [
                name
                for name in live_lanes()
                if re.search(rf"\b{re.escape(name)}\b", prompt.body)
            ]
            print(
                f".github/workflows/{prompt.workflow}:{prompt.line}"
                f"  ({len(prompt.body.splitlines())} lines)"
                f"  lanes: {', '.join(lanes) if lanes else '—'}"
            )
        return 0

    bad = findings()
    for finding in bad:
        print(str(finding), file=sys.stderr)
    if bad:
        print(
            f"\n{len(bad)} inline agent prompt(s) name a lane that does not "
            "exist. Fix the prompt, or — if a lane really was renamed — record "
            "the old name in config/lane-contract.json's aliases block first.",
            file=sys.stderr,
        )
        return 1
    print(f"ok: {len(found)} inline agent prompt(s), all lane names live")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
