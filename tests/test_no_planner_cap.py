"""The planner cap is gone, and this guard keeps its names from coming back
(DRE-8622, epic DRE-8539).

The fleet-wide planner cap was removed (`architecture/decisions/
adr-planner-cap-removed.md`). Its pieces left one card at a time: a plan run
claims no slot (DRE-8617), the sweep keeps no line (DRE-8618), the groomer
keeps no queue (DRE-8619), and the console reads no cap (DRE-8620, DRE-8621).
DRE-8622 deleted what was left — the slot ledger module, its settings file and
the sweep's one-way drain of the old line — and this file is the guard.

WHAT IT READS. Every tracked file (`git ls-files`), as text. A file that is not
text, or that is tracked but absent from the working tree, is skipped.

WHAT IT REFUSES. Any of `NAMES`, each matched as a whole name: a letter, a
digit or an underscore on either side makes it part of another name, so
`planner_cap` never matches `planner_capacity`, the unrelated model-fallback
module, nor this file's own name. A hyphen, a dot, a slash or a space does
not, so `config/planner-queue.json`, `import planner_queue` and
`vars.PLANNER_MAX_RUNNING` are all found. A failure names every file and
line.

`PATTERN` is that match, for the tests that prove one file names none of
them: they read it from here rather than spelling a name.

WHAT IT LEAVES. The three history sets, in `HISTORY`, each with its reason —
and this file, which has to spell the names it searches for. Nothing else is
excepted: a test that proves a name is absent reads it from `NAMES` or
`PATTERN` rather than spelling it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_no_planner_cap.py -v
"""

from __future__ import annotations

import re
import subprocess  # nosec B404 — runs git with fixed arguments
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THIS = Path(__file__).resolve().relative_to(ROOT).as_posix()

#: The cap's names: its settings and their keys, its environment variable, the
#: free-slot read the groomer made, its settings file, its module, the
#: console's field for it, and its receipt.
NAMES = (
    "max_running",
    "PLANNER_MAX_RUNNING",
    "max_running_ceiling",
    "free_planner_slots",
    "planner-queue.json",
    "planner_queue",
    "plannersAtOnce",
    "planner_cap",
    "planner-slot",
)

#: The only places a name may stay, each a glob over the repository-relative
#: path (`*` stays inside one directory, `**` crosses them), with its reason.
HISTORY = {
    "docs/*proof*.md":
        "a proof record states what was true when it was proved, the cap "
        "included, and is never rewritten",
    "architecture/*proof*.md":
        "the same, for the proof records kept beside the architecture",
    "architecture/decisions/**":
        "a decision record is the history of a decision — the cap's own "
        "removal is recorded there",
    "tests/fixtures/**":
        "a recorded thread is what Linear held when it was recorded, old "
        "planner-slot receipts included",
}

#: Any of `NAMES`, as a whole name.
PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    + "|".join(re.escape(n) for n in sorted(NAMES, key=len, reverse=True))
    + r")(?![A-Za-z0-9_])")


def _glob(pattern: str) -> re.Pattern[str]:
    parts = re.split(r"(\*\*|\*)", pattern)
    body = "".join(".*" if p == "**" else "[^/]*" if p == "*" else re.escape(p)
                   for p in parts)
    return re.compile(body + r"\Z")


_HISTORY = tuple(_glob(g) for g in HISTORY)


def _tracked() -> list[str]:
    out = subprocess.run(  # nosec B603 B607 — fixed args, no shell
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True,
        check=True)
    files = [p for p in out.stdout.split("\0") if p]
    assert files, "git ls-files returned nothing — the guard cannot run"
    return files


def _excepted(rel: str) -> bool:
    return rel == THIS or any(g.match(rel) for g in _HISTORY)


def _hits(rel: str, text: str) -> list[str]:
    return [f"{rel}:{n}: {m.group(0)}"
            for n, line in enumerate(text.splitlines(), 1)
            for m in PATTERN.finditer(line)]


def test_no_tracked_file_names_the_planner_cap():
    found = []
    for rel in _tracked():
        if _excepted(rel):
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        found += _hits(rel, text)
    assert not found, (
        "the planner cap is gone (DRE-8622) — these lines still name it:\n  "
        + "\n  ".join(found))


def test_the_cap_files_are_deleted():
    tracked = set(_tracked())
    for rel in ("scripts/planner_queue.py", "config/planner-queue.json"):
        assert rel not in tracked and not (ROOT / rel).exists(), rel


def test_a_name_is_matched_whole():
    assert _hits("x", "import planner_queue")
    assert _hits("x", "scripts/planner_queue.py")
    assert _hits("x", "config/planner-queue.json")
    assert _hits("x", "🎟️ planner-slot: waiting")
    assert _hits("x", "vars.PLANNER_MAX_RUNNING")
    assert _hits("x", '"max_running": 3')
    assert _hits("x", "plannersAtOnce: null")
    assert _hits("x", "planner_cap = 3")
    assert not _hits("x", "import planner_capacity")
    assert not _hits("x", "from test_no_planner_cap import NAMES")
    assert not _hits("x", "the planner cap was removed")


def test_only_the_history_sets_and_this_file_are_excepted():
    assert _excepted(THIS)
    assert _excepted("docs/planner-queue-proof-2026-10.md")
    assert _excepted("architecture/green-light-lines-proof-2026-10.md")
    assert _excepted("architecture/decisions/adr-planner-cap-removed.md")
    assert _excepted("tests/fixtures/green-light-2026-10-10/dre-6617.json")
    assert not _excepted("docs/sub/planner-queue-proof.md")
    assert not _excepted("docs/README.md")
    assert not _excepted("architecture/notes.md")
    assert not _excepted("tests/test_fixtures.py")
    assert not _excepted("scripts/reconcile.py")
    assert all(reason.strip() for reason in HISTORY.values())
