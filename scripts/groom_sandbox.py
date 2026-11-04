#!/usr/bin/env python3
"""The sandbox groom proposal — which cards it grooms, and where it posts
(DRE-8782).

The groomer's memory proofs (DRE-8331, DRE-8333, DRE-8335, DRE-8357) press
on sandbox cards and never on real ones, so they read a proposal that grooms
the sandbox alone. `self-groomer.yml`'s morning makes that proposal as a
second `propose` run with `sandbox` set, after the real one, and posts it on
the sandbox groom card.

  * **The card** is named in committed config, `config/groom-sandbox.json`,
    and never only in a repo variable: a proof run cannot read a repo
    variable (`config/proof-reach.json`). `card()` reads it.
  * **The cards it grooms** are the lane's cards labeled
    `repo:agent-bureau-demo` (`REPO_LABEL`), the sandbox fixtures a proof
    run filed among them, and never the sandbox groom card itself.
    `scope()` cuts the lane down to them.

Nothing here reads Linear or a clock. `groomer.py` imports it for the scope
and for the drain's refusal; the gate job runs `card` for the morning:

    python3 .bureau-pipeline/scripts/groom_sandbox.py card

which appends `card=<ID>` to `$GITHUB_OUTPUT` and exits 1, naming the file,
when the config names no card.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import github_output  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: The committed config naming the sandbox groom card.
CONFIG = ROOT / "config" / "groom-sandbox.json"

#: The sandbox's label, the one the console's `makeTaskCard` puts on a
#: sandbox card — the same label the DRE-7783 fixture exclusion reads
#: (`groom_verify_agent.SANDBOX_FIXTURE_REPO_LABEL`), held equal by
#: tests/test_groom_sandbox.py.
REPO_LABEL = "repo:agent-bureau-demo"

_CARD = re.compile(r"^[A-Z]+-\d+$")


def card(path: Path | None = None) -> str | None:
    """The sandbox groom card `config/groom-sandbox.json` names, or None when
    the file is missing, unreadable or names no card."""
    path = CONFIG if path is None else path
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = doc.get("card") if isinstance(doc, dict) else None
    if isinstance(value, str) and _CARD.match(value.strip()):
        return value.strip()
    return None


def _labels(issue: dict) -> set[str]:
    return {(node.get("name") or "").lower()
            for node in ((issue.get("labels") or {}).get("nodes") or [])}


def scope(cards: list[dict], *, card: str) -> list[dict]:
    """The lane cut down to the sandbox's cards: every card labeled
    `REPO_LABEL` — whichever of its labels it is — but the sandbox groom card
    `card`. The order is the lane's."""
    return [c for c in cards
            if REPO_LABEL in _labels(c) and c.get("identifier") != card]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_card = sub.add_parser("card", help="the sandbox groom card, for the gate")
    p_card.add_argument("--github-output", default=None,
                        help="the step-output file (default: $GITHUB_OUTPUT)")
    args = parser.parse_args(argv)

    found = card(CONFIG)
    if found is None:
        print(f"groom_sandbox: config/{CONFIG.name} names no sandbox "
              "groom card, so there is nowhere to post the sandbox proposal",
              file=sys.stderr)
        return 1
    path = args.github_output or os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(github_output.render([("card", found)]))
    print(f"the sandbox groom card is {found}")
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
