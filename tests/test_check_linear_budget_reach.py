"""How much the fleet budget reader reads, and when it stops (DRE-9163).

PROOF DRE-8911 ran `check_linear_budget.py --hours 1 --by-caller` under the
proof run's 600-second tool ceiling. It walked every repo in the map, read
every run's log, and was killed at the sixth of ten repos having printed
nothing — its tables come only after the last log. The row wanted one repo
and one workflow. Three optional flags answer that:

- `--repo <slug>`, repeatable: only the named slugs are listed and read; a
  slug the map does not hold exits 2 before any `gh` call.
- `--workflow <name>`, repeatable: only runs of the named workflows are
  fetched with `gh run view --log`.
- `--deadline <seconds>`: once that much time has passed, nothing more is
  read and the four tables print from what was, the spend footer naming what
  was left unread.

With none of them, the output is the text the script printed before this
card, pinned byte for byte below. These drive `main` off a fake standing in
for `_gh` — no live `gh` — and the clock off `_monotonic`.
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
_PREFIX = "sweep\tSweep\t2026-11-03T18:28:00.1234567Z "

_REPOS = {"bureau-pipeline": "o/bureau-pipeline", "portico": "o/portico"}

_HEADINGS_BY_CALLER = (
    "linear budget, last 1h",
    "read fallbacks, last 1h — reads only: Linear writes and the console's own "
    "Linear sync never print the line",
    "direct Linear reads, last 1h — per reader, site and caller",
    "door answers, last 1h — per reader and bucket",
)


def _log(*lines: str) -> str:
    return "\n".join(_PREFIX + line for line in lines) + "\n"


def _run_log(spent: int, process: str, reader: str, site: str) -> str:
    """One run's log carrying every line the four tables read."""
    return _log(
        f"linear-budget: 900 → {900 - spent} (spent {spent} this run; window resets "
        "12:00 PT; budget: fleet)",
        f"linear-calls: {spent} request(s) this run (process: {process}; budget: fleet)",
        "read-door: mode on (BUREAU_READ=on, read once for this run)",
        f'read-fallback: reader="{reader}" read=cards reason=missing-field',
        f'linear-read: reader="{reader}" site={site} count={spent} budget=fleet '
        f"process={process} caller=linear_ops.get_issue",
        f'linear-door: reader="{reader}" served={spent * 2} '
        f"fallbacks=cards/missing-field:1 budget=fleet process={process}",
    )


_SWEEP = "Reconcile / sweep / reconcile.py"
_PROOF = "Reconcile / sweep / proof_overdue_sweep.py"
_AGENT = "Agent Task / execute / linear_ops.py"


# "Now", for every run of `main` here: the window the two `runs listed`
# lines name (DRE-9165) ends at it — 12:16 PT on 2026-11-03.
_NOW = datetime(2026, 11, 3, 20, 16, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(clb, "_sleep", lambda _s: None, raising=False)
    monkeypatch.setattr(clb, "_now", lambda: _NOW)
    # These pin the ORDER of the log reads and a deadline tripped by the
    # second, which only one read at a time can fix. Reading several at once
    # is pinned in test_check_linear_budget_runs_listed.py (DRE-9165).
    monkeypatch.setattr(clb, "LOG_WORKERS", 1)


class _FakeGh:
    """Stands in for `_gh`, recording every call. The `api --paginate
    --slurp` listing answers each repo's runs as the one page GitHub prints,
    created in the window's last second (DRE-9165); `run view <id> --log`
    that run's log. `on_view` is called after each view, with how many views
    there have been."""

    def __init__(self, runs, on_view=None):
        self.runs, self.on_view = runs, on_view
        self.calls: list[tuple[str, ...]] = []

    def listed(self) -> list[str]:
        return [c[-1].split("/actions/")[0].removeprefix("repos/")
                for c in self.calls if c[0] == "api"]

    def viewed(self) -> list[int]:
        return [int(c[2]) for c in self.calls if c[:2] == ("run", "view")]

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "api":
            url = args[-1]
            repo = url.split("/actions/")[0].removeprefix("repos/")
            last = url.split("created=")[1].split("&")[0].split("..")[1]
            out = [{"id": rid, "name": wf, "created_at": last, "status": "completed"}
                   for rid, wf, _log_text in self.runs.get(repo, [])]
            page = {"total_count": len(out), "workflow_runs": out}
            return subprocess.CompletedProcess(args, 0, json.dumps([page]), "")
        assert args[:2] == ("run", "view") and "--log" in args, args
        rid = int(args[2])
        log = next(text for runs in self.runs.values()
                   for run_id, _wf, text in runs if run_id == rid)
        if self.on_view:
            self.on_view(len(self.viewed()))
        return subprocess.CompletedProcess(args, 0, log, "")


def _main(monkeypatch, capsys, fake, *argv, repos=None):
    monkeypatch.setattr(clb, "load_repo_map", lambda: dict(repos or _REPOS))
    monkeypatch.setattr(clb, "_gh", fake)
    rc = clb.main(list(argv))
    got = capsys.readouterr()
    return rc, got.out, got.err


def _section(out: str, heading: str) -> list[str]:
    """The lines of the table under `heading`, up to the blank line after it."""
    lines = out.splitlines()
    start = lines.index(heading)
    end = next((i for i in range(start, len(lines)) if not lines[i].strip()), len(lines))
    return lines[start:end]


def _total_line(out: str) -> str:
    return next(line for line in out.splitlines() if line.startswith("TOTAL"))


# One fixture hour across two repos: two Reconcile runs and an Agent Task run
# in bureau-pipeline, one Reconcile run in portico.
_HOUR = {
    "o/bureau-pipeline": [
        (101, "Reconcile", _run_log(7, "11@sweep-aa", _SWEEP, "reconcile.build_started")),
        (102, "Agent Task", _run_log(30, "21@agent-bb", _AGENT, "linear_ops.main")),
        (103, "Reconcile", _run_log(5, "12@sweep-cc", _PROOF, "proof_overdue_sweep.main")),
    ],
    "o/portico": [
        (201, "Reconcile", _run_log(4, "31@sweep-dd", _SWEEP, "reconcile.build_started")),
    ],
}


# What `main` printed for `_HOUR` before DRE-9163, byte for byte — taken from
# the script as it stood, in this file's first commit — with the two `runs
# listed` lines DRE-9165 prints after the spend table's TOTAL.
_PINNED = (
    'linear budget, last 1h\n'
    'repo                 workflow                          runs   spent  max/run\n'
    '----------------------------------------------------------------------------\n'
    'bureau-pipeline      Agent Task                           1      30       30\n'
    'bureau-pipeline      Reconcile                            2      12        7\n'
    'portico              Reconcile                            1       4        4\n'
    '----------------------------------------------------------------------------\n'
    'TOTAL                                                     4      46\n'
    'runs listed, 1h to 2026-11-03T20:16:00Z: bureau-pipeline 3, portico 1\n'
    'runs in progress, not read: none\n'
    '\n'
    "read fallbacks, last 1h — reads only: Linear writes and the console's own Linear sync never print the line\n"
    'repo                 reader                                     read             reason          count\n'
    '------------------------------------------------------------------------------------------------------\n'
    'bureau-pipeline      Agent Task / execute / linear_ops.py       cards            missing-field       1\n'
    'bureau-pipeline      Reconcile / sweep / proof_overdue_sweep.py cards            missing-field       1\n'
    'bureau-pipeline      Reconcile / sweep / reconcile.py           cards            missing-field       1\n'
    'portico              Reconcile / sweep / reconcile.py           cards            missing-field       1\n'
    '------------------------------------------------------------------------------------------------------\n'
    'TOTAL                4 fallback(s) in 4 run(s) read in mode on\n'
    '\n'
    'direct Linear reads, last 1h — per reader and site\n'
    'repo                 reader                                     site                     budget   count\n'
    '-------------------------------------------------------------------------------------------------------\n'
    'bureau-pipeline      Agent Task / execute / linear_ops.py       linear_ops.main          fleet       30\n'
    'bureau-pipeline      Reconcile / sweep / reconcile.py           reconcile.build_started  fleet        7\n'
    'bureau-pipeline      Reconcile / sweep / proof_overdue_sweep.py proof_overdue_sweep.main fleet        5\n'
    'portico              Reconcile / sweep / reconcile.py           reconcile.build_started  fleet        4\n'
    '-------------------------------------------------------------------------------------------------------\n'
    'bureau-pipeline      Agent Task / execute / linear_ops.py       TOTAL                                30\n'
    'bureau-pipeline      Reconcile / sweep / reconcile.py           TOTAL                                 7\n'
    'bureau-pipeline      Reconcile / sweep / proof_overdue_sweep.py TOTAL                                 5\n'
    'portico              Reconcile / sweep / reconcile.py           TOTAL                                 4\n'
    '\n'
    'door answers, last 1h — per reader and bucket\n'
    'repo                 reader                                     budget   served cards/missing-field\n'
    '---------------------------------------------------------------------------------------------------\n'
    'bureau-pipeline      Agent Task / execute / linear_ops.py       fleet        60                   1\n'
    'bureau-pipeline      Reconcile / sweep / reconcile.py           fleet        14                   1\n'
    'bureau-pipeline      Reconcile / sweep / proof_overdue_sweep.py fleet        10                   1\n'
    'portico              Reconcile / sweep / reconcile.py           fleet         8                   1\n'
    '---------------------------------------------------------------------------------------------------\n'
)
_PINNED_BY_CALLER = (
    'linear budget, last 1h\n'
    'repo                 workflow                          runs   spent  max/run\n'
    '----------------------------------------------------------------------------\n'
    'bureau-pipeline      Agent Task                           1      30       30\n'
    'bureau-pipeline      Reconcile                            2      12        7\n'
    'portico              Reconcile                            1       4        4\n'
    '----------------------------------------------------------------------------\n'
    'TOTAL                                                     4      46\n'
    'runs listed, 1h to 2026-11-03T20:16:00Z: bureau-pipeline 3, portico 1\n'
    'runs in progress, not read: none\n'
    '\n'
    "read fallbacks, last 1h — reads only: Linear writes and the console's own Linear sync never print the line\n"
    'repo                 reader                                     read             reason          count\n'
    '------------------------------------------------------------------------------------------------------\n'
    'bureau-pipeline      Agent Task / execute / linear_ops.py       cards            missing-field       1\n'
    'bureau-pipeline      Reconcile / sweep / proof_overdue_sweep.py cards            missing-field       1\n'
    'bureau-pipeline      Reconcile / sweep / reconcile.py           cards            missing-field       1\n'
    'portico              Reconcile / sweep / reconcile.py           cards            missing-field       1\n'
    '------------------------------------------------------------------------------------------------------\n'
    'TOTAL                4 fallback(s) in 4 run(s) read in mode on\n'
    '\n'
    'direct Linear reads, last 1h — per reader, site and caller\n'
    'repo                 reader                                     site                     caller               budget   count\n'
    '----------------------------------------------------------------------------------------------------------------------------\n'
    'bureau-pipeline      Agent Task / execute / linear_ops.py       linear_ops.main          linear_ops.get_issue fleet       30\n'
    'bureau-pipeline      Reconcile / sweep / reconcile.py           reconcile.build_started  linear_ops.get_issue fleet        7\n'
    'bureau-pipeline      Reconcile / sweep / proof_overdue_sweep.py proof_overdue_sweep.main linear_ops.get_issue fleet        5\n'
    'portico              Reconcile / sweep / reconcile.py           reconcile.build_started  linear_ops.get_issue fleet        4\n'
    '----------------------------------------------------------------------------------------------------------------------------\n'
    'bureau-pipeline      Agent Task / execute / linear_ops.py       TOTAL                                                     30\n'
    'bureau-pipeline      Reconcile / sweep / reconcile.py           TOTAL                                                      7\n'
    'bureau-pipeline      Reconcile / sweep / proof_overdue_sweep.py TOTAL                                                      5\n'
    'portico              Reconcile / sweep / reconcile.py           TOTAL                                                      4\n'
    '\n'
    'door answers, last 1h — per reader and bucket\n'
    'repo                 reader                                     budget   served cards/missing-field\n'
    '---------------------------------------------------------------------------------------------------\n'
    'bureau-pipeline      Agent Task / execute / linear_ops.py       fleet        60                   1\n'
    'bureau-pipeline      Reconcile / sweep / reconcile.py           fleet        14                   1\n'
    'bureau-pipeline      Reconcile / sweep / proof_overdue_sweep.py fleet        10                   1\n'
    'portico              Reconcile / sweep / reconcile.py           fleet         8                   1\n'
    '---------------------------------------------------------------------------------------------------\n'
)


# ── with no new flag, the output is what it was ─────────────────────────────


@pytest.mark.parametrize("argv, pinned", [((), _PINNED),
                                          (("--by-caller",), _PINNED_BY_CALLER)])
def test_with_no_new_flag_main_prints_exactly_what_it_did(monkeypatch, capsys, argv, pinned):
    fake = _FakeGh(_HOUR)
    rc, out, err = _main(monkeypatch, capsys, fake, *argv)
    assert rc == 0
    assert out == pinned
    # DRE-9165: one line on stderr as each repo is done, and nothing else.
    assert [ln.split(" in ")[0] for ln in err.splitlines()] == [
        "budget-check: bureau-pipeline 3 run log(s) read",
        "budget-check: portico 1 run log(s) read"]
    assert fake.listed() == ["o/bureau-pipeline", "o/portico"]
    assert fake.viewed() == [101, 102, 103, 201]


# ── --repo ──────────────────────────────────────────────────────────────────


def test_repo_lists_and_reads_only_the_named_slug(monkeypatch, capsys):
    fake = _FakeGh(_HOUR)
    rc, out, _err = _main(monkeypatch, capsys, fake, "--by-caller", "--repo", "bureau-pipeline")
    assert rc == 0
    assert fake.listed() == ["o/bureau-pipeline"]
    assert fake.viewed() == [101, 102, 103]
    assert "portico" not in out
    for heading in _HEADINGS_BY_CALLER:
        assert heading in out.splitlines()
    assert "UNKNOWN" not in out


def test_repo_is_repeatable(monkeypatch, capsys):
    fake = _FakeGh(_HOUR)
    rc, out, _err = _main(monkeypatch, capsys, fake, "--repo", "portico",
                          "--repo", "bureau-pipeline",
                          repos={**_REPOS, "atlas": "o/atlas"})
    assert rc == 0
    assert fake.listed() == ["o/bureau-pipeline", "o/portico"]
    assert "atlas" not in out


@pytest.mark.parametrize("argv", [("--repo", "nosuch"),
                                  ("--repo", "bureau-pipeline", "--repo", "nosuch")])
def test_a_slug_the_map_does_not_hold_exits_2_before_any_gh_call(monkeypatch, capsys, argv):
    fake = _FakeGh(_HOUR)
    rc, out, err = _main(monkeypatch, capsys, fake, *argv)
    assert rc == 2
    assert fake.calls == []
    assert out == ""
    assert "nosuch" in err
    for slug in _REPOS:
        assert slug in err


# ── --workflow ──────────────────────────────────────────────────────────────


def test_workflow_fetches_only_the_named_workflows_runs(monkeypatch, capsys):
    hour = {"o/bureau-pipeline": _HOUR["o/bureau-pipeline"][:2]}
    fake = _FakeGh(hour)
    rc, out, _err = _main(monkeypatch, capsys, fake, "--by-caller", "--workflow", "Reconcile",
                          repos={"bureau-pipeline": "o/bureau-pipeline"})
    assert rc == 0
    assert fake.listed() == ["o/bureau-pipeline"]
    assert fake.viewed() == [101]
    assert "Agent Task" not in out and "linear_ops.main" not in out
    spend = [ln.split() for ln in _section(out, _HEADINGS_BY_CALLER[0])
             if ln.startswith("bureau-pipeline")]
    assert spend == [["bureau-pipeline", "Reconcile", "1", "7", "7"]]
    reads = [ln for ln in _section(out, _HEADINGS_BY_CALLER[2])
             if ln.startswith("bureau-pipeline") and "TOTAL" not in ln]
    assert len(reads) == 1 and _SWEEP in reads[0] and reads[0].split()[-1] == "7"
    assert _total_line(out).split()[:3] == ["TOTAL", "1", "7"]


def test_workflow_is_repeatable(monkeypatch, capsys):
    fake = _FakeGh(_HOUR)
    rc, _out, _err = _main(monkeypatch, capsys, fake, "--workflow", "Agent Task",
                           "--workflow", "Reconcile", "--repo", "bureau-pipeline")
    assert rc == 0
    assert fake.viewed() == [101, 102, 103]


def test_a_workflow_no_run_carries_reads_no_log(monkeypatch, capsys):
    fake = _FakeGh(_HOUR)
    rc, out, _err = _main(monkeypatch, capsys, fake, "--workflow", "Medic")
    assert rc == 0
    assert fake.listed() == ["o/bureau-pipeline", "o/portico"]
    assert fake.viewed() == []
    assert _total_line(out).split()[:3] == ["TOTAL", "0", "0"]


# ── --deadline ──────────────────────────────────────────────────────────────


class _Clock:
    """The `_monotonic` seam's stand-in: reads `now`, which a test moves."""

    def __init__(self, now: float = 0.0):
        self.now = now
        self.reads = 0

    def __call__(self) -> float:
        self.reads += 1
        return self.now


def test_the_clock_goes_through_one_module_level_seam():
    import time
    assert clb._monotonic is time.monotonic


def test_deadline_stops_reading_and_still_prints_the_four_tables(monkeypatch, capsys):
    clock = _Clock()
    monkeypatch.setattr(clb, "_monotonic", clock)

    def tick(views):
        if views == 2:
            clock.now = 101.0

    fake = _FakeGh(_HOUR, on_view=tick)
    rc, out, _err = _main(monkeypatch, capsys, fake, "--by-caller", "--deadline", "100")
    assert rc == 0
    for heading in _HEADINGS_BY_CALLER:
        assert heading in out.splitlines()
    assert fake.listed() == ["o/bureau-pipeline"]
    assert fake.viewed() == [101, 102]
    tail = _total_line(out)
    assert tail.split()[:3] == ["TOTAL", "2", "37"]
    assert ("(stopped at the deadline (100s): bureau-pipeline 1 run(s) not read; "
            "not reached: portico)") in tail
    assert "proof_overdue_sweep" not in out
    for heading in _HEADINGS_BY_CALLER:
        rows = [ln for ln in _section(out, heading) if ln.startswith("portico")]
        assert rows, heading
        assert all(clb.UNKNOWN in ln for ln in rows), heading
        assert all("0" not in ln.split()[1:] for ln in rows), heading


def test_a_deadline_already_reached_reads_nothing(monkeypatch, capsys):
    monkeypatch.setattr(clb, "_monotonic", _Clock())
    fake = _FakeGh(_HOUR)
    rc, out, _err = _main(monkeypatch, capsys, fake, "--deadline", "0")
    assert rc == 0
    assert fake.calls == []
    tail = _total_line(out)
    assert "(stopped at the deadline (0s): not reached: bureau-pipeline, portico)" in tail
    assert "(+ 2 repo(s) UNKNOWN)" in tail


def test_the_deadline_is_read_before_every_list_and_view(monkeypatch, capsys):
    """A run whose log `gh` cannot return yet is asked again — and each ask is
    a `gh run view`, so the clock is read before it too. Past the deadline
    after the first ask, there is no second, and the run is not read rather
    than skipped."""
    clock = _Clock()
    monkeypatch.setattr(clb, "_monotonic", clock)
    asks = []

    def busy(*args):
        asks.append(args)
        if args[:2] == ("run", "view"):
            clock.now = 50.0
            return subprocess.CompletedProcess(args, 1, "", "run 101 is still in progress")
        return _FakeGh({"o/bureau-pipeline": _HOUR["o/bureau-pipeline"][:1]})(*args)

    monkeypatch.setattr(clb, "load_repo_map", lambda: {"bureau-pipeline": "o/bureau-pipeline"})
    monkeypatch.setattr(clb, "_gh", busy)
    assert clb.main(["--deadline", "40"]) == 0
    out = capsys.readouterr().out
    assert [a[:2] for a in asks] == [("api", "--paginate"), ("run", "view")]
    tail = _total_line(out)
    assert "skipped" not in tail
    assert "(stopped at the deadline (40s): bureau-pipeline 1 run(s) not read)" in tail


def test_a_deadline_never_reached_adds_no_note(monkeypatch, capsys):
    monkeypatch.setattr(clb, "_monotonic", _Clock())
    fake = _FakeGh(_HOUR)
    rc, out, _err = _main(monkeypatch, capsys, fake, "--deadline", "480")
    assert rc == 0
    assert out == _PINNED
    assert fake.viewed() == [101, 102, 103, 201]


def test_the_three_flags_combine_as_the_next_proof_runs_them(monkeypatch, capsys):
    monkeypatch.setattr(clb, "_monotonic", _Clock())
    fake = _FakeGh(_HOUR)
    rc, out, _err = _main(monkeypatch, capsys, fake, "--hours", "1", "--by-caller",
                          "--repo", "bureau-pipeline", "--workflow", "Reconcile",
                          "--deadline", "480")
    assert rc == 0
    assert fake.listed() == ["o/bureau-pipeline"]
    assert fake.viewed() == [101, 103]
    assert "stopped at the deadline" not in out
    assert _total_line(out).split()[:3] == ["TOTAL", "2", "12"]


# ── the words ───────────────────────────────────────────────────────────────


def test_the_usage_line_names_the_three_flags():
    assert ("python3 scripts/check_linear_budget.py [--hours N] [--by-caller] "
            "[--repo SLUG ...] [--workflow NAME ...] [--deadline SECONDS]"
            ) in [line.strip() for line in clb.__doc__.splitlines()]


def test_the_docstring_and_the_help_describe_the_three_flags(capsys):
    doc = " ".join(clb.__doc__.split())
    for flag in ("`--repo", "`--workflow", "`--deadline"):
        assert doc.count(flag) >= 1, flag
    assert "stopped at the deadline" in doc
    with pytest.raises(SystemExit):
        clb.main(["--help"])
    out = capsys.readouterr().out
    for flag in ("--repo", "--workflow", "--deadline"):
        assert flag in out
