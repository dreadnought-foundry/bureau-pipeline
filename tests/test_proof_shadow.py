"""Proof shadow: the shadow's throwaway branch, written and read back (DRE-7288).

A proof run can be shadowed: a second run on another model, dispatched
beside the real one, writes its record to `proof-shadow/<CARD>-<run id>`
with a `proof-shadow.json` sidecar instead of opening a pull request.
`scripts/proof_shadow.py` owns that branch at both ends:

* `push` writes it, from the checkout the shadow agent wrote into — one ref,
  and never one outside `proof-shadow/DRE-<n>-<digits>`;
* `compare` reads every such branch back, pairs each shadow record with the
  real Opus record on `agent/<CARD>-proof-record` (the first commit at or
  after the shadow's `dispatched_at`), and lines the two up row by row.

What this file pins: `compare`'s line shapes, counts, pairing, window and
reports against an injected fake `gh`; its Linear reads (none without
`--linear-check`, batched by ten with it); that it writes nowhere; and
`push` against a throwaway repository whose remote is a bare one.

Run: python3 -m pytest tests/test_proof_shadow.py -v
"""

from __future__ import annotations

import base64
import inspect
import io
import json
import re
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from urllib.parse import parse_qs, unquote

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import linear_ops  # noqa: E402
import proof_shadow as ps  # noqa: E402

REPO = "dreadnought-foundry/bureau-pipeline"
MODEL = "shadow-model"
SHADOW_RUN = "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/"

CARD_LINE = re.compile(
    r"^proof-shadow: card=DRE-\d+ shadow=proof-shadow/DRE-\d+-\d+ opus=(?:[0-9a-f]{40}|none) "
    r"rows=\d+ agree=\d+ shadow_met_opus_not=\d+ shadow_run=\S+ opus_run=\S+( rows-differ)?$")
MET_NOT_LINE = re.compile(
    r'^proof-shadow: met-not card=DRE-\d+ row=\d+ criterion="[^"]{0,70}" opus="[^"]{0,40}"$')
UNPAIRED_LINE = re.compile(r"^proof-shadow: unpaired card=DRE-\d+ shadow=\S+ — \S.*$")
LINEAR_LINE = re.compile(
    r"^proof-shadow: linear card=DRE-\d+ state=\S.* shadow_mentions=\d+ "
    r"record_pr=\S+ record_pr_merged=(?:yes|no)$")
LINEAR_CHECK_LINE = re.compile(
    r"^proof-shadow: linear-check cards=\d+ requests=\d+ shadow_mentions=\d+ "
    r"done_without_merged_record=\d+$")
SUMMARY_LINE = re.compile(
    r"^proof-shadow: cards=\d+ rows=\d+ agree=\d+ \(\d+%\) shadow_met_opus_not=\d+ "
    r"model=\S+ window=\S+\.\.\S+ days=\d+ bar=(?:met|not-met)$")


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #

def record(rows, *, run_url="https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/900",
           preamble=""):
    """A proof record: a `## How this was recorded` section naming the run,
    then the criterion table, `rows` being (criterion, result) pairs."""
    table = "\n".join(f"| {i} | {c} | {r} |" for i, (c, r) in enumerate(rows, 1))
    return (f"# Proof record\n\n{preamble}\n\n## How this was recorded\n\n"
            f"Recorded by the proof run {run_url} on the default branch.\n\n"
            f"## Criteria\n\n| # | Criterion | Result |\n| - | - | - |\n{table}\n")


class World:
    """GitHub as `compare` reads it, answered from fixtures through the one
    `gh` callable it is handed. Every call is recorded."""

    def __init__(self):
        self.files = {}      # (ref, path) -> text
        self.branches = []
        self.pulls = {}      # card -> [pull]
        self.commits = {}    # number -> [commit]
        self.pr_files = {}   # number -> [file]
        self.calls = []
        self._n = 0

    def sha(self) -> str:
        self._n += 1
        return f"{self._n:040x}"

    def shadow(self, card, run_id, *, dispatched, rows=None, record_path="docs/proof-shadow-record.md",
               sidecar=True, model=MODEL, text=None):
        branch = f"proof-shadow/{card}-{run_id}"
        self.branches.append(branch)
        if sidecar:
            self.files[(branch, ps.SIDECAR)] = json.dumps({
                "card": card, "model": model, "run_url": f"{SHADOW_RUN}{run_id}",
                "dispatched_at": dispatched, "record_path": record_path})
        if record_path and (rows is not None or text is not None):
            self.files[(branch, record_path)] = text if text is not None else record(rows)
        return branch

    def opus(self, card, commits, *, number, merged=True, state="closed",
             record_path="docs/opus-proof-record.md"):
        """`commits` is [(committed at, record text)], oldest first."""
        url = f"https://github.com/{REPO}/pull/{number}"
        self.pulls.setdefault(card, []).append({
            "number": number, "state": state, "html_url": url,
            "merged_at": "2026-11-05T00:00:00Z" if merged else None,
            "head": {"ref": f"agent/{card}-proof-record"}})
        shas = []
        listed = []
        for at, text in commits:
            sha = self.sha()
            shas.append(sha)
            listed.append({"sha": sha, "commit": {"committer": {"date": at},
                                                  "author": {"date": at}}})
            if text is not None:
                self.files[(sha, record_path)] = text
        self.commits[number] = listed
        self.pr_files[number] = [{"filename": record_path, "status": "added"},
                                 {"filename": "scripts/other.py", "status": "modified"}]
        return shas

    def __call__(self, args):
        self.calls.append(list(args))
        assert args[0] == "api", args
        path = args[1]
        prefix = f"repos/{REPO}/"
        assert path.startswith(prefix), args
        rest = path[len(prefix):]
        if rest.startswith("git/matching-refs/heads/proof-shadow"):
            return json.dumps([[{"ref": f"refs/heads/{b}", "object": {"sha": self.sha()}}
                                for b in self.branches]])
        found = re.fullmatch(r"contents/(.+)\?ref=(.+)", rest)
        if found:
            key = (unquote(found.group(2)), unquote(found.group(1)))
            if key not in self.files:
                raise RuntimeError("gh: Not Found (HTTP 404)")
            content = base64.b64encode(self.files[key].encode()).decode()
            return json.dumps({"encoding": "base64", "content": content})
        found = re.fullmatch(r"pulls\?(.+)", rest)
        if found:
            query = parse_qs(found.group(1))
            head = query["head"][0]
            owner, _, branch = head.partition(":")
            assert owner == REPO.split("/")[0], head
            assert query["state"] == ["all"], query
            card = re.fullmatch(r"agent/(DRE-\d+)-proof-record", branch).group(1)
            return json.dumps([self.pulls.get(card, [])])
        found = re.fullmatch(r"pulls/(\d+)/commits(?:\?.*)?", rest)
        if found:
            return json.dumps([self.commits[int(found.group(1))]])
        found = re.fullmatch(r"pulls/(\d+)/files(?:\?.*)?", rest)
        if found:
            return json.dumps([self.pr_files[int(found.group(1))]])
        raise AssertionError(f"unexpected gh call: {args}")


def run(world, *argv, out_dir):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = ps.main(["compare", "--repo", REPO, *argv, "--out-dir", str(out_dir)], gh=world)
    return code, buf.getvalue().splitlines()


@pytest.fixture(autouse=True)
def no_linear(monkeypatch):
    """No test reaches Linear unless it hands `compare` a fake `gql`."""
    def refuse(*_a, **_k):
        raise AssertionError("compare sent a Linear request")
    monkeypatch.setattr(linear_ops, "gql", refuse)


def three_cards(world):
    met, nobs, nmet = "Met. seen in run 1", "Not observed. the run never fired", "Not met. it failed"
    world.shadow("DRE-101", 1001, dispatched="2026-10-20T18:00:00Z",
                 rows=[("Alpha works", met), ("Beta works", met), ("Merged to main", "Met.")])
    world.opus("DRE-101", [("2026-10-20T19:00:00Z",
                            record([("Alpha works", met), ("Beta works", met),
                                    ("Merged to main", "Not observed. pending")]))], number=11)
    world.shadow("DRE-102", 1002, dispatched="2026-10-21T18:00:00Z",
                 rows=[("Gamma holds", met), ("Delta holds", nmet)])
    world.opus("DRE-102", [("2026-10-21T19:00:00Z",
                            record([("Gamma holds", nobs), ("Delta holds", nmet)]))], number=12)
    world.shadow("DRE-103", 1003, dispatched="2026-10-22T18:00:00Z",
                 rows=[("Eps", met), ("Zeta", met), ("Eta", met)])
    world.opus("DRE-103", [("2026-10-22T19:00:00Z",
                            record([("Eps", met), ("Zeta", met), ("Eta", met)]))], number=13)


# --------------------------------------------------------------------------- #
# compare: the lines and the reports                                           #
# --------------------------------------------------------------------------- #

def test_compare_over_three_cards_prints_every_line_in_its_shape_and_writes_both_reports(tmp_path):
    world = World()
    three_cards(world)
    code, lines = run(world, out_dir=tmp_path)
    assert code == 0, lines
    assert all(line.startswith("proof-shadow:") for line in lines), lines
    card_lines = [line for line in lines if CARD_LINE.match(line)]
    assert [re.search(r"card=(\S+)", line).group(1) for line in card_lines] == [
        "DRE-101", "DRE-102", "DRE-103"]
    met_not = [line for line in lines if MET_NOT_LINE.match(line)]
    assert len(met_not) == 1 and "card=DRE-102 row=1" in met_not[0], met_not
    assert 'criterion="Gamma holds"' in met_not[0]
    assert 'opus="Not observed. the run never fired"' in met_not[0]
    assert SUMMARY_LINE.match(lines[-1]), lines[-1]
    assert len(lines) == 5, lines
    # Order: the card lines, then the met-not lines, then the summary.
    assert lines[:3] == card_lines and lines[3] == met_not[0]
    assert "rows=2 agree=2 shadow_met_opus_not=0" in card_lines[0]
    assert "rows=2 agree=1 shadow_met_opus_not=1" in card_lines[1]
    assert "rows=3 agree=3 shadow_met_opus_not=0" in card_lines[2]
    assert f"shadow_run={SHADOW_RUN}1001" in card_lines[0]
    assert "opus_run=https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/900" \
        in card_lines[0]
    assert lines[-1].startswith(
        "proof-shadow: cards=3 rows=7 agree=6 (85%) shadow_met_opus_not=1 "
        f"model={MODEL} window=2026-10-20T18:00:00Z..2026-10-22T18:00:00Z days=2 bar=not-met")

    md = (tmp_path / ps.REPORT_MD).read_text(encoding="utf-8")
    for needle in ("DRE-101", "DRE-102", "DRE-103", f"{SHADOW_RUN}1002",
                   "actions/runs/900", lines[-1]):
        assert needle in md, needle
    doc = json.loads((tmp_path / ps.REPORT_JSON).read_text(encoding="utf-8"))
    assert [s["card"] for s in doc["shadows"]] == ["DRE-101", "DRE-102", "DRE-103"]
    assert doc["summary"]["rows"] == 7 and doc["summary"]["agree"] == 6
    assert doc["summary"]["bar"] == "not-met"
    assert doc["lines"] == lines


def test_a_shadow_met_row_opus_did_not_observe_is_a_met_not_line_and_fails_the_bar(tmp_path):
    world = World()
    world.shadow("DRE-7", 70, dispatched="2026-10-20T18:00:00Z",
                 rows=[("The sweep reroutes the card", "Met. rerouted in run 5")])
    world.opus("DRE-7", [("2026-10-20T19:00:00Z", record(
        [("The sweep reroutes the card", "Not observed. no sweep ran in the window")]))],
        number=7)
    _, lines = run(world, out_dir=tmp_path)
    assert "shadow_met_opus_not=1" in lines[0]
    assert lines[1] == ('proof-shadow: met-not card=DRE-7 row=1 '
                        'criterion="The sweep reroutes the card" '
                        'opus="Not observed. no sweep ran in the window"')
    assert lines[-1].endswith("bar=not-met")
    assert "rows=1 agree=0 (0%) shadow_met_opus_not=1" in lines[-1]


def test_met_not_cuts_the_criterion_to_70_and_the_opus_cell_to_40(tmp_path):
    world = World()
    long = "A criterion " + "x" * 100
    world.shadow("DRE-8", 80, dispatched="2026-10-20T18:00:00Z", rows=[(long, "Met.")])
    world.opus("DRE-8", [("2026-10-20T19:00:00Z", record([(long, "Not met. " + "y" * 80)]))],
               number=8)
    _, lines = run(world, out_dir=tmp_path)
    found = MET_NOT_LINE.match(lines[1])
    assert found, lines[1]
    assert f'criterion="{long[:70]}"' in lines[1]
    assert f'opus="{("Not met. " + "y" * 80)[:40]}"' in lines[1]


# --------------------------------------------------------------------------- #
# compare: pairing with the Opus record                                        #
# --------------------------------------------------------------------------- #

def test_the_opus_record_is_the_first_commit_at_or_after_dispatched_at(tmp_path):
    world = World()
    world.shadow("DRE-9", 90, dispatched="2026-10-20T18:00:00Z", rows=[("One", "Met.")])
    before = record([("One", "Not met. stale")],
                    run_url="https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/1")
    first = record([("One", "Met. the first after")],
                   run_url="https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/2",
                   preamble="An earlier run: https://github.com/o/r/actions/runs/777 is not it.")
    first = first.replace(
        "on the default branch.",
        "on the default branch, beside https://github.com/o/r/actions/runs/3.")
    second = record([("One", "Not met. the second after")],
                    run_url="https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/4")
    shas = world.opus("DRE-9", [("2026-10-20T17:59:59Z", before),
                                ("2026-10-20T18:00:00Z", first),
                                ("2026-10-20T20:00:00Z", second)], number=9)
    _, lines = run(world, out_dir=tmp_path)
    assert f"opus={shas[1]}" in lines[0], lines[0]
    assert "rows=1 agree=1" in lines[0]
    assert lines[0].endswith(
        "opus_run=https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/2")


def test_a_closed_unmerged_record_pull_request_is_not_paired(tmp_path):
    world = World()
    world.shadow("DRE-10", 100, dispatched="2026-10-20T18:00:00Z", rows=[("One", "Met.")])
    world.opus("DRE-10", [("2026-10-20T19:00:00Z", record([("One", "Met.")]))],
               number=20, merged=False, state="closed")
    _, lines = run(world, out_dir=tmp_path)
    assert UNPAIRED_LINE.match(lines[0]), lines


def test_an_open_record_pull_request_is_paired(tmp_path):
    world = World()
    world.shadow("DRE-10", 100, dispatched="2026-10-20T18:00:00Z", rows=[("One", "Met.")])
    world.opus("DRE-10", [("2026-10-20T19:00:00Z", record([("One", "Met.")]))],
               number=20, merged=False, state="open")
    _, lines = run(world, out_dir=tmp_path)
    assert CARD_LINE.match(lines[0]), lines


def test_no_run_link_in_the_how_section_is_opus_run_none(tmp_path):
    world = World()
    world.shadow("DRE-12", 120, dispatched="2026-10-20T18:00:00Z", rows=[("One", "Met.")])
    text = record([("One", "Met.")], run_url="by hand",
                  preamble="See https://github.com/o/r/actions/runs/5 above the section.")
    world.opus("DRE-12", [("2026-10-20T19:00:00Z", text)], number=21)
    _, lines = run(world, out_dir=tmp_path)
    assert lines[0].endswith("opus_run=none"), lines[0]


# --------------------------------------------------------------------------- #
# compare: rows                                                                #
# --------------------------------------------------------------------------- #

def test_closing_rows_are_skipped_rows_pair_by_position_and_extras_disagree(tmp_path):
    world = World()
    world.shadow("DRE-13", 130, dispatched="2026-10-20T18:00:00Z", rows=[
        ("The CEO closes the card", "Met."),
        ("One", "Met."),
        ("Two", "Not observed. nothing ran"),
        ("Three", "Met."),
    ])
    world.opus("DRE-13", [("2026-10-20T19:00:00Z", record([
        ("One", "Met. also"),
        ("Merged to main", "Not met. not yet"),
        ("Two", "Not observed. also nothing"),
        ("Three", "Not met. failed"),
        ("Four", "Met."),
        ("Five", "Accepted by operator decision"),
    ]))], number=22)
    _, lines = run(world, out_dir=tmp_path)
    # Shadow: One, Two, Three. Opus: One, Two, Three, Four, Five. Five rows
    # compared: two agree, Three disagrees (shadow-only Met), the two extras
    # disagree.
    assert CARD_LINE.match(lines[0]), lines[0]
    assert " rows=5 agree=2 shadow_met_opus_not=1 " in lines[0], lines[0]
    assert lines[0].endswith(" rows-differ"), lines[0]
    assert 'row=3 criterion="Three" opus="Not met. failed"' in lines[1]


def test_equal_row_counts_say_nothing_of_rows_differ(tmp_path):
    world = World()
    three_cards(world)
    _, lines = run(world, out_dir=tmp_path)
    assert not any("rows-differ" in line for line in lines)


def test_rows_are_classed_met_not_met_not_observed_and_other():
    assert ps.row_class("Met. seen") == "met"
    assert ps.row_class("**Holds** in run 3") == "met"
    assert ps.row_class("Not met. failed") == "not-met"
    assert ps.row_class("Not observed. cause: window — none") == "not-observed"
    assert ps.row_class("Met, but only once") == "other"
    assert ps.row_class("ACCEPTED by operator decision") == "other"


# --------------------------------------------------------------------------- #
# compare: unpaired shadows                                                    #
# --------------------------------------------------------------------------- #

def test_unreadable_shadows_are_unpaired_with_a_reason_and_outside_every_count(tmp_path):
    world = World()
    three_cards(world)
    world.shadow("DRE-201", 2001, dispatched="2026-10-21T00:00:00Z", sidecar=False,
                 rows=[("One", "Met.")])
    world.shadow("DRE-202", 2002, dispatched="2026-10-21T00:00:00Z", record_path=None)
    world.opus("DRE-202", [("2026-10-21T01:00:00Z", record([("One", "Met.")]))], number=31)
    world.shadow("DRE-203", 2003, dispatched="2026-10-21T00:00:00Z", rows=[("One", "Met.")])
    world.opus("DRE-203", [("2026-10-20T23:00:00Z", record([("One", "Met.")]))], number=32)
    _, lines = run(world, out_dir=tmp_path)
    unpaired = [line for line in lines if UNPAIRED_LINE.match(line)]
    assert len(unpaired) == 3, lines
    by_card = {re.search(r"card=(\S+)", line).group(1): line for line in unpaired}
    assert "proof-shadow.json" in by_card["DRE-201"]
    assert "shadow=proof-shadow/DRE-201-2001" in by_card["DRE-201"]
    assert "record_path" in by_card["DRE-202"]
    assert "2026-10-21T00:00:00Z" in by_card["DRE-203"]
    assert len([line for line in lines if CARD_LINE.match(line)]) == 3
    # The counts are the three paired cards' alone.
    assert lines[-1].startswith("proof-shadow: cards=3 rows=7 agree=6 (85%) shadow_met_opus_not=1 ")
    # Unpaired lines come after the met-not lines and before the summary.
    first_unpaired = lines.index(unpaired[0])
    assert all(MET_NOT_LINE.match(line) or CARD_LINE.match(line) for line in lines[:first_unpaired])
    doc = json.loads((tmp_path / ps.REPORT_JSON).read_text(encoding="utf-8"))
    assert sorted(u["card"] for u in doc["unpaired"]) == ["DRE-201", "DRE-202", "DRE-203"]
    assert all(u["reason"] for u in doc["unpaired"])
    md = (tmp_path / ps.REPORT_MD).read_text(encoding="utf-8")
    assert "proof-shadow/DRE-203-2003" in md


# --------------------------------------------------------------------------- #
# the summary: percent, bar, window, days                                      #
# --------------------------------------------------------------------------- #

def test_the_percent_rounds_down():
    assert ps.percent(19, 21) == 90
    assert ps.percent(18, 20) == 90
    assert ps.percent(17, 19) == 89
    assert ps.percent(0, 0) == 0


def test_the_bar_needs_no_shadow_only_met_row_and_ninety_percent():
    assert ps.bar(19, 21, 0) == "met"
    assert ps.bar(17, 19, 0) == "not-met"
    assert ps.bar(20, 20, 1) == "not-met"
    assert ps.bar(0, 0, 0) == "not-met"


def test_the_bar_is_met_end_to_end_when_every_row_agrees(tmp_path):
    world = World()
    world.shadow("DRE-14", 140, dispatched="2026-10-20T18:00:00Z", rows=[("One", "Met.")])
    world.opus("DRE-14", [("2026-10-20T19:00:00Z", record([("One", "Met.")]))], number=40)
    _, lines = run(world, out_dir=tmp_path)
    assert "(100%)" in lines[-1] and lines[-1].endswith("bar=met"), lines[-1]


def window_world():
    world = World()
    for card, run_id, at in (("DRE-301", 3001, "2026-10-20T18:00:00Z"),
                             ("DRE-302", 3002, "2026-10-27T12:00:00Z"),
                             ("DRE-303", 3003, "2026-11-03T09:00:00Z")):
        world.shadow(card, run_id, dispatched=at, rows=[("One", "Met.")])
        world.opus(card, [("2026-11-04T00:00:00Z", record([("One", "Met.")]))],
                   number=run_id)
    return world


def test_the_window_is_since_until_when_both_are_given(tmp_path):
    _, lines = run(window_world(), "--since", "2026-10-20T17:00:00Z",
                   "--until", "2026-11-03T17:00:00Z", out_dir=tmp_path)
    assert "window=2026-10-20T17:00:00Z..2026-11-03T17:00:00Z days=14 " in lines[-1], lines[-1]


def test_the_window_is_first_to_last_dispatch_with_neither_flag(tmp_path):
    _, lines = run(window_world(), out_dir=tmp_path)
    assert "window=2026-10-20T18:00:00Z..2026-11-03T09:00:00Z days=13 " in lines[-1], lines[-1]


def test_an_until_one_minute_short_of_fourteen_days_reads_thirteen(tmp_path):
    _, lines = run(window_world(), "--since", "2026-10-20T17:00:00Z",
                   "--until", "2026-11-03T16:59:00Z", out_dir=tmp_path)
    assert "window=2026-10-20T17:00:00Z..2026-11-03T16:59:00Z days=13 " in lines[-1], lines[-1]


def test_since_until_and_cards_filter_on_the_sidecar(tmp_path):
    _, lines = run(window_world(), "--since", "2026-10-21", "--until",
                   "2026-11-03T09:59:00+01:00", out_dir=tmp_path)
    cards = [re.search(r"card=(\S+)", line).group(1) for line in lines if CARD_LINE.match(line)]
    assert cards == ["DRE-302"], lines
    assert "window=2026-10-21T00:00:00Z..2026-11-03T08:59:00Z " in lines[-1], lines[-1]
    _, lines = run(window_world(), "--cards", "DRE-301,DRE-303", out_dir=tmp_path)
    cards = [re.search(r"card=(\S+)", line).group(1) for line in lines if CARD_LINE.match(line)]
    assert cards == ["DRE-301", "DRE-303"], lines


def test_a_card_shadowed_twice_prints_two_card_lines_and_counts_once_in_cards(tmp_path):
    world = World()
    world.shadow("DRE-15", 150, dispatched="2026-10-20T18:00:00Z", rows=[("One", "Met.")])
    world.shadow("DRE-15", 151, dispatched="2026-10-21T18:00:00Z", rows=[("One", "Met.")])
    world.opus("DRE-15", [("2026-10-20T19:00:00Z", record([("One", "Met.")])),
                          ("2026-10-21T19:00:00Z", record([("One", "Not met.")]))], number=50)
    _, lines = run(world, out_dir=tmp_path)
    assert len([line for line in lines if CARD_LINE.match(line)]) == 2
    assert lines[-1].startswith("proof-shadow: cards=1 rows=2 agree=1 (50%) shadow_met_opus_not=1 ")


# --------------------------------------------------------------------------- #
# Linear                                                                       #
# --------------------------------------------------------------------------- #

def test_without_linear_check_compare_makes_no_linear_request(tmp_path):
    # The autouse fixture has already replaced `linear_ops.gql` with one that
    # fails the test.
    world = World()
    three_cards(world)
    code, lines = run(world, out_dir=tmp_path)
    assert code == 0
    assert not any(" linear" in line for line in lines)


class FakeLinear:
    def __init__(self, cards):
        self.cards = cards   # identifier -> (state, [comment bodies])
        self.calls = []

    def __call__(self, query, variables=None, **_):
        self.calls.append((query, variables))
        assert query.lstrip().startswith("query"), query
        variables = variables or {}
        if "numbers" in variables:
            assert 'team: {key: {eq: "DRE"}}' in query and "number: {in: $numbers}" in query
            assert "comments(first: 100)" in query
            assert len(variables["numbers"]) <= 10
            nodes = []
            for n in variables["numbers"]:
                ident = f"DRE-{int(n)}"
                if ident not in self.cards:
                    continue
                state, bodies = self.cards[ident]
                nodes.append({"id": f"id-{ident}", "identifier": ident, "state": {"name": state},
                              "comments": {"nodes": [{"body": b} for b in bodies[:100]],
                                           "pageInfo": {"hasNextPage": len(bodies) > 100,
                                                        "endCursor": "c100"}}})
            return {"issues": {"nodes": nodes}}
        ident = variables["id"][len("id-"):]
        _, bodies = self.cards[ident]
        start = int((variables.get("after") or "c0")[1:])
        page = bodies[start:start + 100]
        more = start + 100 < len(bodies)
        return {"issue": {"comments": {"nodes": [{"body": b} for b in page],
                                       "pageInfo": {"hasNextPage": more,
                                                    "endCursor": f"c{start + 100}"}}}}


def test_linear_check_over_38_cards_sends_four_batched_queries(tmp_path, monkeypatch):
    world = World()
    linear = {}
    for i in range(38):
        card, run_id = f"DRE-{500 + i}", 5000 + i
        world.shadow(card, run_id, dispatched="2026-10-20T18:00:00Z", rows=[("One", "Met.")])
        world.opus(card, [("2026-10-20T19:00:00Z", record([("One", "Met.")]))],
                   number=600 + i, merged=(i != 2), state="closed" if i != 2 else "open")
        linear[card] = ("In Review", ["⏳ 1/5 plan", "an unrelated note"])
    linear["DRE-500"] = ("Done", [f"shadow ran: {SHADOW_RUN}5000", "another"])
    linear["DRE-501"] = ("Done", ["see the branch proof-shadow/DRE-501-5001"])
    linear["DRE-502"] = ("Done", ["no mention here"])
    fake = FakeLinear(linear)
    monkeypatch.setattr(linear_ops, "gql", fake)
    code, lines = run(world, "--linear-check", out_dir=tmp_path)
    assert code == 0, lines
    assert len(fake.calls) == 4, [v for _, v in fake.calls]
    assert all(q.lstrip().startswith("query") for q, _ in fake.calls)
    check = [line for line in lines if LINEAR_CHECK_LINE.match(line)]
    assert check == ["proof-shadow: linear-check cards=38 requests=4 shadow_mentions=2 "
                     "done_without_merged_record=1"], lines
    per_card = [line for line in lines if LINEAR_LINE.match(line)]
    assert len(per_card) == 38
    assert ("proof-shadow: linear card=DRE-500 state=Done shadow_mentions=1 "
            f"record_pr=https://github.com/{REPO}/pull/600 record_pr_merged=yes") in per_card
    assert ("proof-shadow: linear card=DRE-502 state=Done shadow_mentions=0 "
            f"record_pr=https://github.com/{REPO}/pull/602 record_pr_merged=no") in per_card
    assert any("card=DRE-503 state=In Review shadow_mentions=0" in line for line in per_card)
    # Order: linear lines, then the linear-check line, then the summary.
    assert lines[-2] == check[0] and SUMMARY_LINE.match(lines[-1])
    assert lines.index(per_card[0]) > max(i for i, line in enumerate(lines) if CARD_LINE.match(line))
    doc = json.loads((tmp_path / ps.REPORT_JSON).read_text(encoding="utf-8"))
    assert doc["linear_check"]["requests"] == 4


def test_a_card_past_100_comments_is_paged_further_and_only_that_card(tmp_path, monkeypatch):
    world = World()
    for card, run_id in (("DRE-701", 7001), ("DRE-702", 7002)):
        world.shadow(card, run_id, dispatched="2026-10-20T18:00:00Z", rows=[("One", "Met.")])
        world.opus(card, [("2026-10-20T19:00:00Z", record([("One", "Met.")]))], number=run_id)
    many = ["noise"] * 150 + [f"{SHADOW_RUN}7001"]
    fake = FakeLinear({"DRE-701": ("Done", many), "DRE-702": ("Done", ["x"])})
    monkeypatch.setattr(linear_ops, "gql", fake)
    _, lines = run(world, "--linear-check", out_dir=tmp_path)
    assert len(fake.calls) == 2, fake.calls
    assert "card=DRE-701 state=Done shadow_mentions=1" in "\n".join(lines)
    assert "requests=2 shadow_mentions=1 done_without_merged_record=0" in lines[-2]


def test_a_card_with_no_record_pull_request_reads_none(tmp_path, monkeypatch):
    world = World()
    world.shadow("DRE-801", 8001, dispatched="2026-10-20T18:00:00Z", rows=[("One", "Met.")])
    fake = FakeLinear({"DRE-801": ("Done", [])})
    monkeypatch.setattr(linear_ops, "gql", fake)
    _, lines = run(world, "--linear-check", out_dir=tmp_path)
    assert ("proof-shadow: linear card=DRE-801 state=Done shadow_mentions=0 "
            "record_pr=none record_pr_merged=no") in lines
    assert "done_without_merged_record=1" in lines[-2]


# --------------------------------------------------------------------------- #
# compare writes nowhere                                                       #
# --------------------------------------------------------------------------- #

_WRITE_FLAGS = {"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"}


def test_compare_makes_no_write(tmp_path, monkeypatch):
    world = World()
    three_cards(world)
    fake = FakeLinear({c: ("Done", []) for c in ("DRE-101", "DRE-102", "DRE-103")})
    # Scoped to the body, so conftest's own resets of the seam run unpatched.
    with monkeypatch.context() as patch:
        for name, fn in inspect.getmembers(linear_ops, inspect.isfunction):
            if fn.__module__ == linear_ops.__name__ and name != "gql":
                def refuse(*_a, _name=name, **_k):
                    raise AssertionError(f"compare called linear_ops.{_name}")
                patch.setattr(linear_ops, name, refuse)
        patch.setattr(linear_ops, "gql", fake)
        code, _ = run(world, "--linear-check", out_dir=tmp_path)
    assert code == 0
    assert world.calls, "GitHub was read"
    for call in world.calls:
        assert call[0] == "api", call
        assert not _WRITE_FLAGS & set(call), call
    assert fake.calls and all("mutation" not in q for q, _ in fake.calls)


# --------------------------------------------------------------------------- #
# push                                                                         #
# --------------------------------------------------------------------------- #

def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout


@pytest.fixture
def checkout(tmp_path):
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    git(work, "config", "user.name", "test")
    git(work, "config", "user.email", "test@example.com")
    (work / "docs").mkdir()
    (work / "docs" / "old.md").write_text("old\n")
    (work / "README.md").write_text("readme\n")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "init")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "-q", "origin", "main")
    return work, remote


def refs(remote):
    return sorted(line.split()[1] for line in git(remote, "for-each-ref",
                                                  "--format=%(objectname) %(refname)").splitlines())


def push_args(card="DRE-1234", run_id="38638006167"):
    return dict(card=card, run_id=run_id, model="claude-sonnet-5-5",
                run_url=f"{SHADOW_RUN}{run_id}", dispatched_at="2026-10-20T18:00:00Z")


def test_push_writes_one_ref_with_the_record_and_the_sidecar(checkout):
    work, remote = checkout
    (work / "docs" / "proofs").mkdir()
    (work / "docs" / "proofs" / "dre-1234-proof.md").write_text("# record\n")
    (work / "docs" / "old.md").write_text("changed, not new\n")
    (work / "scratch.txt").write_text("not a record\n")
    args = push_args()
    assert ps.push(**args, cwd=work) == 0
    branch = "proof-shadow/DRE-1234-38638006167"
    assert refs(remote) == ["refs/heads/main", f"refs/heads/{branch}"]
    sidecar = json.loads(git(remote, "show", f"{branch}:proof-shadow.json"))
    assert sidecar == {"card": "DRE-1234", "model": args["model"],
                       "run_url": f"{SHADOW_RUN}38638006167",
                       "dispatched_at": "2026-10-20T18:00:00Z",
                       "record_path": "docs/proofs/dre-1234-proof.md"}
    assert git(remote, "show", f"{branch}:docs/proofs/dre-1234-proof.md") == "# record\n"
    changed = git(remote, "diff", "--name-only", "main", branch).split()
    assert sorted(changed) == ["docs/proofs/dre-1234-proof.md", "proof-shadow.json"]


def test_push_with_no_record_still_pushes_the_sidecar_with_a_null_record_path(checkout):
    work, remote = checkout
    assert ps.push(**push_args(run_id="77"), cwd=work) == 0
    branch = "proof-shadow/DRE-1234-77"
    assert refs(remote) == ["refs/heads/main", f"refs/heads/{branch}"]
    sidecar = json.loads(git(remote, "show", f"{branch}:proof-shadow.json"))
    assert sidecar["record_path"] is None
    assert set(sidecar) == {"card", "model", "run_url", "dispatched_at", "record_path"}


def test_push_with_two_records_names_none(checkout):
    work, remote = checkout
    (work / "docs" / "a.md").write_text("a\n")
    (work / "architecture").mkdir()
    (work / "architecture" / "b.md").write_text("b\n")
    assert ps.push(**push_args(run_id="78"), cwd=work) == 0
    sidecar = json.loads(git(remote, "show", "proof-shadow/DRE-1234-78:proof-shadow.json"))
    assert sidecar["record_path"] is None


@pytest.mark.parametrize("card,run_id", [
    ("DRE-1234", "38x"),
    ("DRE-1234", ""),
    ("agent/DRE-1234", "1"),
    ("DRE-12/../../main", "1"),
    ("DRE-", "1"),
    ("dre-1234", "1"),
    ("DRE-1234", "1:refs/heads/main"),
])
def test_push_refuses_a_ref_outside_proof_shadow_and_pushes_nothing(checkout, card, run_id):
    work, remote = checkout
    (work / "docs" / "x.md").write_text("x\n")
    before = refs(remote)
    assert ps.push(**push_args(card=card, run_id=run_id), cwd=work) != 0
    assert refs(remote) == before == ["refs/heads/main"]


def test_push_through_the_cli(checkout, monkeypatch):
    work, remote = checkout
    monkeypatch.chdir(work)
    args = push_args(run_id="99")
    code = ps.main(["push", "--card", args["card"], "--run-id", args["run_id"],
                    "--model", args["model"], "--run-url", args["run_url"],
                    "--dispatched-at", args["dispatched_at"], "--remote", "origin"])
    assert code == 0
    assert "refs/heads/proof-shadow/DRE-1234-99" in refs(remote)
