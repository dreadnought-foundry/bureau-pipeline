"""The escape census must stay bound to the code (DRE-2682).

Ten routes by which work leaves the one path — card → branch → PR → CI →
critic → merge gate → Done — each with a real incident behind it. The census
was assembled on 2026-08-23 and its most useful column is the last one: does
anything actually READ for this route today, or is it merely known?

That column is exactly the claim most likely to rot. `hand-built` proved a
label nothing reads is not a gate; a census row claiming a watcher that no
longer exists is the same trap one level up. So every reader the document
names is resolved here — a `reconcile.*` attribute that must exist and be
callable, or a repo path that must exist — and the document's own headline
count of unwatched routes is recomputed from its own table.

Run: cd bureau-pipeline && python3 -m pytest tests/test_escape_census_doc.py -v
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402

DOC = ROOT / "docs" / "escape-census.md"
ROUTES = list("ABCDEFGHIJ")
UNWATCHED = "—"

_ROW = re.compile(r"^\|\s*([A-J])\s*\|(.+)$")
_CODE = re.compile(r"`([^`]+)`")


def rows() -> dict:
    """{route letter: [cells]} for the census table."""
    found = {}
    for line in DOC.read_text().splitlines():
        m = _ROW.match(line.strip())
        if not m:
            continue
        cells = [c.strip() for c in m.group(2).split("|")]
        found[m.group(1)] = cells
    return found


def test_the_document_exists_and_says_what_it_is():
    text = DOC.read_text()
    assert "DRE-2682" in text, "the census must name the card it came from"
    assert "DRE-2655" in text, "route A's incident is the census's origin"


def test_every_route_a_through_j_is_present_with_evidence_and_a_reader():
    table = rows()
    assert sorted(table) == ROUTES, sorted(table)
    for letter, cells in table.items():
        # route | evidence | reader (the leading `| A |` is the key)
        assert len(cells) >= 3, (letter, cells)
        route, evidence, reader = cells[0], cells[1], cells[2]
        assert route, letter
        assert evidence and evidence != UNWATCHED, (
            f"route {letter} carries no incident — the census is evidence, "
            "not a list of worries"
        )
        assert reader, letter


def test_every_named_reader_resolves_to_something_that_exists():
    """The whole point: a reader is something that runs, looks and writes —
    never a marker someone is expected to notice."""
    for letter, cells in rows().items():
        reader = cells[2]
        if reader.startswith(UNWATCHED):
            continue
        names = _CODE.findall(reader)
        assert names, f"route {letter} claims a reader but names none: {reader}"
        for name in names:
            if name.startswith("reconcile."):
                attr = name.split(".", 1)[1]
                assert callable(getattr(reconcile, attr, None)), (
                    f"route {letter} names {name}, which reconcile.py does "
                    "not define"
                )
            else:
                assert (ROOT / name).exists(), (
                    f"route {letter} names {name}, which does not exist"
                )


def test_an_unwatched_route_claims_no_reader():
    for letter, cells in rows().items():
        reader = cells[2]
        if reader.startswith(UNWATCHED):
            assert not _CODE.findall(reader), (
                f"route {letter} is marked unwatched but names a reader"
            )


def test_the_headline_count_matches_the_table():
    """The census's one summary number is recomputed, so it cannot drift as
    routes get closed."""
    unwatched = sum(
        1 for cells in rows().values() if cells[2].startswith(UNWATCHED)
    )
    m = re.search(r"\*\*(\d+) of 10[^*]*unwatched\*\*", DOC.read_text())
    assert m, "the census must state how many of its ten routes are unwatched"
    assert int(m.group(1)) == unwatched, (
        f"the document says {m.group(1)}, the table says {unwatched}"
    )


def test_route_a_is_read_by_the_watchdog_this_card_built():
    assert "reconcile.flag_unlanded_work" in rows()["A"][2]


def test_route_c_names_both_readers_it_actually_has():
    """A dispatched card with no run receipt is flag_stranded's case; a
    hand-built one is the replacement alarm's, because `hand-built`
    suppresses flag_stranded by design (DRE-2524)."""
    reader = rows()["C"][2]
    assert "reconcile.flag_stranded" in reader
    assert "reconcile.flag_unlanded_work" in reader


def test_authorship_is_recorded_as_a_constraint_on_every_row():
    text = DOC.read_text().lower()
    assert "author" in text and "dispatch record" in text, (
        "route G is a constraint on all the others: no detection may tell "
        "HAND from FLEET by git author"
    )
