"""The fleet budget reader's third and fourth tables (DRE-8897), driven off
fake log text — no `gh` call anywhere in here.

Every process that sent Linear a read through `linear_ops.py` prints, at exit,
one line per (site, caller, bucket):

    linear-read: reader="<workflow> / <job> / <script>" site=<module>.<function> count=<n> budget=<bucket> process=<token> caller=<module>.<function>

and every process whose read-door mode was `on` prints one line:

    linear-door: reader="<workflow> / <job> / <script>" served=<n> fallbacks=<read>/<reason>:<n>[,...] budget=<bucket> process=<token>

These tests pin how `check_linear_budget.py` counts them out of the run logs
— only where they open a line, a line repeated with the same process once —
what it prints after the fallback table, with and without `--by-caller`, and
that the first two tables of a log carrying neither line are byte-identical
to what they were before.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
)

import check_linear_budget as clb  # noqa: E402

# What `gh run view --log` prints: job, step, timestamp, then the line.
_PREFIX = "sweep\tSweep\t2026-11-02T17:08:29.1234567Z "

_SWEEP = "Reconcile / sweep / reconcile.py"
_PROOF = "Reconcile / sweep / proof_dispatch.py"
_HEADING = "direct Linear reads, last 1h — per reader and site"
_BY_CALLER_HEADING = "direct Linear reads, last 1h — per reader, site and caller"
_DOOR_HEADING = "door answers, last 1h — per reader and bucket"


def _log(*lines: str) -> str:
    return "\n".join(_PREFIX + line for line in lines) + "\n"


def _read(reader, site, caller, count, process, budget="fleet") -> str:
    """The line linear_ops prints, exactly as the contract writes it."""
    return (f'linear-read: reader="{reader}" site={site} count={count} '
            f"budget={budget} process={process} caller={caller}")


def _door(reader, served, fallbacks, process, budget="fleet") -> str:
    return (f'linear-door: reader="{reader}" served={served} fallbacks={fallbacks} '
            f"budget={budget} process={process}")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(clb, "_sleep", lambda _s: None, raising=False)


class _FakeGh:
    """Stands in for `_gh`: `run list` answers each repo's runs, created now,
    or rc 1 for a repo in `unreadable`; `run view <id> --log` its log."""

    def __init__(self, runs, unreadable=()):
        self.runs, self.unreadable = runs, unreadable

    def __call__(self, *args):
        if args[:2] == ("run", "list"):
            repo = args[args.index("-R") + 1]
            if repo in self.unreadable:
                return subprocess.CompletedProcess(args, 1, "", "HTTP 404")
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            out = [{"databaseId": rid, "workflowName": wf, "createdAt": now}
                   for rid, wf, _log_text in self.runs.get(repo, [])]
            return subprocess.CompletedProcess(args, 0, json.dumps(out), "")
        rid = int(args[2])
        for runs in self.runs.values():
            for run_id, _wf, log_text in runs:
                if run_id == rid:
                    return subprocess.CompletedProcess(args, 0, log_text, "")
        raise AssertionError(f"unknown run {rid}")


def _main(monkeypatch, capsys, runs, *argv, unreadable=(), repos=None):
    repos = repos or {"bureau-pipeline": "o/bureau-pipeline"}
    monkeypatch.setattr(clb, "load_repo_map", lambda: dict(repos))
    monkeypatch.setattr(clb, "_gh", _FakeGh(runs, unreadable))
    assert clb.main(list(argv)) == 0
    return capsys.readouterr().out


def _section(out: str, heading: str) -> list[str]:
    """The lines of the table under `heading`, up to the blank line after it."""
    lines = out.splitlines()
    start = lines.index(heading)
    end = next((i for i in range(start, len(lines)) if not lines[i].strip()), len(lines))
    return lines[start:end]


# A sweep run with two processes. Process A read from one site through two
# callers, and printed its lines twice (a step that `cat`s a log); process B
# read on the planner bucket. An echoed script names the tag mid-line.
_SWEEP_LOG = _log(
    "linear-calls: 9 request(s) this run (process: 11@sweep-aa; budget: fleet)",
    _read(_SWEEP, "reconcile.build_started", "linear_ops.lane_history", 5, "11@sweep-aa"),
    _read(_SWEEP, "reconcile.build_started", "linear_ops.get_issue", 3, "11@sweep-aa"),
    _read(_SWEEP, "reconcile._fetch_active_cards", "reconcile._fetch_active_cards", 1,
          "11@sweep-aa"),
    _read(_SWEEP, "reconcile.build_started", "linear_ops.lane_history", 5, "11@sweep-aa"),
    _read(_PROOF, "proof_dispatch.main", "linear_ops.get_issue", 4, "12@proof-bb",
          budget="planner-oauth"),
    '\x1b[36;1mgrep "linear-read: reader=\\"x\\" site=a.b count=99 budget=fleet '
    'process=1@x caller=a.b" log\x1b[0m',
    "+ echo " + _read(_SWEEP, "echo.me", "echo.me", 50, "1@echo"),
)


def test_the_default_table_sums_callers_and_totals_each_reader(monkeypatch, capsys):
    out = _main(monkeypatch, capsys, {"o/bureau-pipeline": [(1, "Reconcile", _SWEEP_LOG)]})
    summary = clb.aggregate_reads([("bureau-pipeline", "Reconcile", _SWEEP_LOG)])
    assert [(r["reader"], r["site"], r["budget"], r["count"]) for r in summary["rows"]] == [
        (_SWEEP, "reconcile.build_started", "fleet", 8),
        (_PROOF, "proof_dispatch.main", "planner-oauth", 4),
        (_SWEEP, "reconcile._fetch_active_cards", "fleet", 1),
    ]
    assert [(t["reader"], t["count"]) for t in summary["totals"]] == [(_SWEEP, 9), (_PROOF, 4)]
    table = _section(out, _HEADING)
    body = "\n".join(table)
    assert "echo.me" not in body and "count=99" not in body and " 99" not in body
    rows = [ln.split() for ln in table if ln.startswith("bureau-pipeline")]
    assert ["reconcile.build_started", "fleet", "8"] == rows[0][-3:]
    assert ["proof_dispatch.main", "planner-oauth", "4"] == rows[1][-3:]
    assert ["reconcile._fetch_active_cards", "fleet", "1"] == rows[2][-3:]
    totals = [ln for ln in table if "TOTAL" in ln]
    assert len(totals) == 2
    assert _SWEEP in totals[0] and totals[0].split()[-1] == "9"
    assert _PROOF in totals[1] and totals[1].split()[-1] == "4"


def test_the_by_caller_table_keeps_callers_apart_and_sums_to_the_default(
        monkeypatch, capsys):
    out = _main(monkeypatch, capsys, {"o/bureau-pipeline": [(1, "Reconcile", _SWEEP_LOG)]},
                "--by-caller")
    assert _HEADING not in out.splitlines()
    table = _section(out, _BY_CALLER_HEADING)
    by_caller = clb.aggregate_reads([("bureau-pipeline", "Reconcile", _SWEEP_LOG)],
                                    by_caller=True)
    assert [(r["site"], r["caller"], r["count"]) for r in by_caller["rows"]] == [
        ("reconcile.build_started", "linear_ops.lane_history", 5),
        ("proof_dispatch.main", "linear_ops.get_issue", 4),
        ("reconcile.build_started", "linear_ops.get_issue", 3),
        ("reconcile._fetch_active_cards", "reconcile._fetch_active_cards", 1),
    ]
    default = clb.aggregate_reads([("bureau-pipeline", "Reconcile", _SWEEP_LOG)])
    for row in default["rows"]:
        key = (row["repo"], row["reader"], row["site"], row["budget"])
        assert row["count"] == sum(
            r["count"] for r in by_caller["rows"]
            if (r["repo"], r["reader"], r["site"], r["budget"]) == key)
    assert by_caller["totals"] == default["totals"]
    assert any("linear_ops.lane_history" in ln and ln.split()[-1] == "5" for ln in table)
    assert any("caller" in ln.split() for ln in table)
    assert len([ln for ln in table if "TOTAL" in ln]) == 2


def test_a_line_repeated_by_the_same_process_counts_once():
    log = _log(*[_read(_SWEEP, "reconcile.build_started", "linear_ops.get_issue", 3,
                       "11@sweep-aa")] * 3)
    (row,) = clb.aggregate_reads([("bureau-pipeline", "Reconcile", log)])["rows"]
    assert row["count"] == 3


def test_two_processes_with_the_same_line_both_count():
    log = _log(_read(_SWEEP, "reconcile.build_started", "linear_ops.get_issue", 3, "11@a"),
               _read(_SWEEP, "reconcile.build_started", "linear_ops.get_issue", 3, "12@b"))
    (row,) = clb.aggregate_reads([("bureau-pipeline", "Reconcile", log)])["rows"]
    assert row["count"] == 6


def test_reads_add_up_across_runs():
    log = _log(_read(_SWEEP, "reconcile.build_started", "linear_ops.get_issue", 3, "11@a"))
    obs = [("bureau-pipeline", "Reconcile", log), ("bureau-pipeline", "Reconcile", log)]
    (row,) = clb.aggregate_reads(obs)["rows"]
    assert row["count"] == 6


def test_a_repo_the_token_cannot_read_is_unknown_in_the_new_tables(monkeypatch, capsys):
    out = _main(monkeypatch, capsys, {"o/bureau-pipeline": [(1, "Reconcile", _SWEEP_LOG)]},
                unreadable=("o/portico",),
                repos={"bureau-pipeline": "o/bureau-pipeline", "portico": "o/portico"})
    for heading in (_HEADING, _DOOR_HEADING):
        unknown = [ln for ln in _section(out, heading) if ln.startswith("portico")]
        assert unknown and clb.UNKNOWN in unknown[0], heading


def test_no_read_line_says_so_in_words(monkeypatch, capsys):
    log = _log("linear-calls: 2 request(s) this run (process: 1@a; budget: fleet)")
    out = _main(monkeypatch, capsys, {"o/bureau-pipeline": [(1, "Reconcile", log)]})
    assert _section(out, _HEADING) == [_HEADING, "no linear-read line in the window"]
    assert _section(out, _DOOR_HEADING) == [_DOOR_HEADING, "no linear-door line in the window"]


# ── the door table ──────────────────────────────────────────────────────────

_DOOR_LOG = _log(
    _door(_SWEEP, 40, "board/stale:2,cards/stale:1", "11@sweep-aa"),
    _door(_SWEEP, 40, "board/stale:2,cards/stale:1", "11@sweep-aa"),
    _door(_SWEEP, 10, "cards/stale:3", "13@sweep-cc"),
    _door(_PROOF, 7, "-", "12@proof-bb", budget="planner-oauth"),
    "+ echo " + _door(_SWEEP, 99, "-", "1@echo"),
)


def test_the_door_table_sums_served_and_each_fallback(monkeypatch, capsys):
    summary = clb.aggregate_doors([("bureau-pipeline", "Reconcile", _DOOR_LOG)])
    assert summary["fallbacks"] == ["board/stale", "cards/stale"]
    assert [(r["reader"], r["budget"], r["served"], r["fallbacks"])
            for r in summary["rows"]] == [
        (_SWEEP, "fleet", 50, {"board/stale": 2, "cards/stale": 4}),
        (_PROOF, "planner-oauth", 7, {}),
    ]
    out = _main(monkeypatch, capsys, {"o/bureau-pipeline": [(1, "Reconcile", _DOOR_LOG)]})
    table = _section(out, _DOOR_HEADING)
    header = next(ln for ln in table if ln.startswith("repo"))
    assert header.split()[-3:] == ["served", "board/stale", "cards/stale"]
    rows = [ln.split() for ln in table if ln.startswith("bureau-pipeline")]
    assert rows[0][-4:] == ["fleet", "50", "2", "4"]
    assert rows[1][-4:] == ["planner-oauth", "7", "0", "0"]
    assert "99" not in "\n".join(table)


def test_the_door_table_follows_the_read_table_with_and_without_by_caller(
        monkeypatch, capsys):
    runs = {"o/bureau-pipeline": [(1, "Reconcile", _SWEEP_LOG + _DOOR_LOG)]}
    for argv, heading in (((), _HEADING), (("--by-caller",), _BY_CALLER_HEADING)):
        lines = _main(monkeypatch, capsys, runs, *argv).splitlines()
        fallback = next(i for i, ln in enumerate(lines) if ln.startswith("read fallbacks"))
        assert fallback < lines.index(heading) < lines.index(_DOOR_HEADING)


# ── a log from before this card reads exactly as it did ─────────────────────

_OLD_LOG = (
    "build\tRun agent\t2026-11-02T17:08:29.1234567Z linear-budget: 900 → 880 (spent 20 "
    "this run; window resets 16:00 PT; budget: fleet)\n"
    "build\tRun agent\t2026-11-02T17:08:29.1234567Z linear-calls: 7 request(s) this run "
    "(process: 11@run-aa; budget: fleet)\n"
    "build\tRun agent\t2026-11-02T17:08:29.1234567Z read-door: mode on (BUREAU_READ=on, "
    "read once for this run)\n"
    'build\tRun agent\t2026-11-02T17:08:29.1234567Z read-fallback: reader="Reconcile / '
    'sweep / reconcile.py" read=board reason=stale\n'
)

# The first two tables as they printed before DRE-8897, byte for byte.
_OLD_TABLES = (
    "linear budget, last 1h\n"
    "repo                 workflow                          runs   spent  max/run\n"
    "----------------------------------------------------------------------------\n"
    "bureau-pipeline      Reconcile                            1       7        7\n"
    "portico              UNKNOWN                          UNKNOWN UNKNOWN  UNKNOWN\n"
    "----------------------------------------------------------------------------\n"
    "TOTAL                                                     1       7   (+ 1 repo(s) UNKNOWN)\n"
    "\n"
    "read fallbacks, last 1h — reads only: Linear writes and the console's own Linear "
    "sync never print the line\n"
    "repo                 reader                           read             reason          count\n"
    "--------------------------------------------------------------------------------------------\n"
    "bureau-pipeline      Reconcile / sweep / reconcile.py board            stale               1\n"
    "portico              UNKNOWN                          UNKNOWN          UNKNOWN        UNKNOWN\n"
    "--------------------------------------------------------------------------------------------\n"
    "TOTAL                1 fallback(s) in 1 run(s) read in mode on\n"
)


@pytest.mark.parametrize("argv", [(), ("--by-caller",)])
def test_the_first_two_tables_are_byte_identical_without_the_new_lines(
        monkeypatch, capsys, argv):
    out = _main(monkeypatch, capsys, {"o/bureau-pipeline": [(1, "Reconcile", _OLD_LOG)]},
                *argv, unreadable=("o/portico",),
                repos={"bureau-pipeline": "o/bureau-pipeline", "portico": "o/portico"})
    assert out.startswith(_OLD_TABLES + "\n")


def test_the_new_lines_change_nothing_in_the_first_two_tables():
    log = _OLD_LOG + _log(
        _read(_SWEEP, "reconcile.build_started", "linear_ops.get_issue", 3, "11@run-aa"),
        _door(_SWEEP, 4, "board/stale:1", "11@run-aa"))
    old, new = [("bureau-pipeline", "Reconcile", _OLD_LOG)], [("bureau-pipeline", "Reconcile", log)]
    assert clb.render_table(clb.aggregate(old), []) == clb.render_table(clb.aggregate(new), [])
    assert (clb.render_fallbacks(clb.aggregate_fallbacks(old), [])
            == clb.render_fallbacks(clb.aggregate_fallbacks(new), []))


# ── the words ───────────────────────────────────────────────────────────────


def test_the_help_and_docstring_name_the_new_tables_and_the_flag(capsys):
    with pytest.raises(SystemExit):
        clb.main(["--help"])
    out = capsys.readouterr().out
    assert "--by-caller" in out
    assert "linear-read:" in clb.__doc__ and "linear-door:" in clb.__doc__
    assert "--by-caller" in clb.__doc__
