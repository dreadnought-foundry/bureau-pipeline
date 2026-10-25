#!/usr/bin/env python3
"""Can this run reach the model tests? (DRE-6873)

`tests.yml`'s `console change keeps the suite green` job runs
`tests/test_console_change_keeps_the_suite_green.py` (DRE-6865) with its
per-case runs switched on: every test that can read the model files, once per
console change it derives — about ten minutes of pytest a case. A pull request
that touches none of what those tests read cannot turn one of them red, so the
job's first step asks this script, and every later step is gated on the answer
(the CEO's CI decision of 2026-09-24, `standards/engineering.md`: narrow per
change, whole every night). A script rather than an `on: paths:` filter,
because a filtered-out run creates no check run at all.

THE RULE — the harness's own, `tests/model_fixture.py`'s `model_reading_tests`:

  * anything under `config/`, `agents.yaml`, `tests/model_fixture.py`,
    anything under `tests/fixtures/model-config-frozen/` and
    `.github/workflows/tests.yml` reach outright;
  * a `.py` under `tests/` or `scripts/` reaches when its source at the tree
    names one of `MODEL_FILE_NAMES` or a rung id of the tree's
    `config/models.yaml` ladders — the same six names and the same ids
    `model_reading_tests` reads, so a test that joins the derived set reaches
    the moment it is added;
  * a `.py` there that the tree no longer holds (deleted or renamed) reaches;
  * nothing else does.

FAILS OPEN. A list it cannot read — missing, empty, a line that is not a path
in the repository — or a `models.yaml` it cannot parse answers `run`. The
workflow step answers `run` too when the compare that writes the list fails,
and a schedule run has no range to compare, so the nightly runs everything.

Usage:
    python3 scripts/suite_green_reach.py decide --changed <file> --tree <root>

Prints `run` or `skip` on its first line and why on its second — the first
reaching path, or what could not be read — and exits 0 either way.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

#: Written identically to `tests/model_fixture.py`'s `MODEL_FILE_NAMES`;
#: `tests/test_suite_green_job.py` holds the two equal.
MODEL_FILE_NAMES = (
    "models.yaml", "agents.yaml", "model-choices.json",
    "model_fallback", "sync_model_config", "model_config_change",
)

#: Paths that reach whatever they hold.
OUTRIGHT = ("agents.yaml", "tests/model_fixture.py", ".github/workflows/tests.yml")
OUTRIGHT_UNDER = ("config/", "tests/fixtures/model-config-frozen/")

#: Where a `.py` reaches by what its source names.
SOURCE_UNDER = ("tests/", "scripts/")


class Unreadable(Exception):
    """An input the decision cannot be made from — which answers `run`."""


def _rung_ids(rungs) -> list[str]:
    return [r["model"] if isinstance(r, dict) else r for r in rungs or []]


def tokens(tree: Path) -> tuple[str, ...]:
    """The six names, then every rung id of `tree/config/models.yaml`'s
    ladders, sorted — what `model_reading_tests` looks for in a test's source."""
    path = Path(tree) / "config" / "models.yaml"
    try:
        import yaml
    except ImportError as exc:
        raise Unreadable(f"PyYAML is not installed, so {path} cannot be read") from exc
    try:
        cfg = yaml.safe_load(path.read_text()) or {}
        rungs = {m for rungs in (cfg.get("ladders") or {}).values()
                 for m in _rung_ids(rungs)}
    except Exception as exc:  # noqa: BLE001 — any failure here is a list it cannot read
        raise Unreadable(f"{path}: {type(exc).__name__}: {exc}") from exc
    if not all(isinstance(m, str) for m in rungs):
        raise Unreadable(f"{path}: a rung id is not a string")
    return (*MODEL_FILE_NAMES, *sorted(rungs))


def changed_paths(changed: Path) -> list[str]:
    """The changed paths, one per line. Raises `Unreadable` on a missing or
    empty file and on any line that is not a repository-relative path."""
    try:
        text = Path(changed).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Unreadable(f"the changed-files list {changed}: {exc}") from exc
    lines = text.splitlines()
    if not lines:
        raise Unreadable(f"the changed-files list {changed} is empty")
    for n, line in enumerate(lines, 1):
        if (not line or line != line.strip() or line.startswith("/")
                or any(ord(c) < 32 for c in line)):
            raise Unreadable(f"line {n} of {changed} is not a path: {line!r}")
    return lines


def reaches(path: str, tree: Path, names: tuple[str, ...]) -> bool:
    """Whether the changed `path` can reach the model tests, by the rule."""
    if path in OUTRIGHT or path.startswith(OUTRIGHT_UNDER):
        return True
    if path.endswith(".py") and path.startswith(SOURCE_UNDER):
        source = Path(tree) / path
        if not source.is_file():
            return True
        try:
            text = source.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise Unreadable(f"{path}: {exc}") from exc
        return any(name in text for name in names)
    return False


def decide(changed: Path, tree: Path) -> tuple[str, str]:
    """`("run", <the first reaching path>)`, `("skip", <why>)`, or
    `("run", <what could not be read>)`."""
    try:
        paths = changed_paths(changed)
        names = tokens(tree)
        for path in paths:
            if reaches(path, tree, names):
                return "run", path
    except Unreadable as exc:
        return "run", f"cannot read: {exc}"
    return "skip", f"none of the {len(paths)} changed files can reach the model tests"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("decide", help="print run or skip for a changed-files list")
    cmd.add_argument("--changed", required=True, type=Path,
                     help="the changed paths, one per line")
    cmd.add_argument("--tree", required=True, type=Path,
                     help="the checked-out tree the paths are read from")
    args = parser.parse_args(argv)
    answer, why = decide(args.changed, args.tree)
    print(answer)
    print(why)
    return 0


if __name__ == "__main__":
    sys.exit(main())
