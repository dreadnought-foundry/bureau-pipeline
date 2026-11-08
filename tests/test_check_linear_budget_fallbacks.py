"""The fleet budget reader's second table (DRE-8795): the Linear reads that
fell back from the read door, per pipeline reader, driven off fake log text —
no `gh` call anywhere in here. The client prints one `read-fallback:` line per
fallback and `bureau_read.mode()` prints the `read-door: mode` line; these
tests pin how the reader counts them and what it prints after the spend
table."""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
)

import check_linear_budget as clb  # noqa: E402

# What `gh run view --log` prints: job, step, timestamp, then the line.
_PREFIX = "build\tRun agent\t2026-11-02T17:08:29.1234567Z "


def _log(*lines: str) -> str:
    return "\n".join(_PREFIX + line for line in lines) + "\n"


def _fallback(reader: str, read: str, reason: str) -> str:
    """The line the client card prints, exactly as the contract writes it."""
    return f'read-fallback: reader="{reader}" read={read} reason={reason}'


def _mode(value: str) -> str:
    """The line bureau_read.mode() prints, unchanged by any sibling."""
    return (f"read-door: mode {value} (BUREAU_READ={value}, "
            "read once for this run)")


_SNAPSHOT = "Agent Task / build / agent_task_snapshot.py"
_SWEEP = "Reconcile / sweep / reconcile.py"
_SPENT_20 = ("linear-budget: 900 → 880 (spent 20 this run; window resets "
             "16:00 PT; budget: fleet)")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(clb, "_sleep", lambda _s: None, raising=False)


class _FakeGh:
    """Stands in for `_gh` and records every call. The `api --paginate
    --slurp` listing answers each repo's runs as the one page GitHub prints,
    created in the window's last second (DRE-9165), or rc 1 for a repo listed
    in `unreadable`; `run view <id> --log` answers that run's log."""

    def __init__(self, runs: dict[str, list[tuple[int, str, str]]],
                 unreadable: tuple[str, ...] = ()):
        self.runs, self.unreadable = runs, unreadable
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> subprocess.CompletedProcess:
        self.calls.append(args)
        if args[0] == "api":
            url = args[-1]
            repo = url.split("/actions/")[0].removeprefix("repos/")
            if repo in self.unreadable:
                return subprocess.CompletedProcess(args, 1, "", "HTTP 404")
            last = url.split("created=")[1].split("&")[0].split("..")[1]
            out = [{"id": rid, "name": wf, "created_at": last, "status": "completed"}
                   for rid, wf, _log_text in self.runs.get(repo, [])]
            page = {"total_count": len(out), "workflow_runs": out}
            return subprocess.CompletedProcess(args, 0, json.dumps([page]), "")
        assert args[:2] == ("run", "view") and "--log" in args, args
        rid = int(args[2])
        for runs in self.runs.values():
            for run_id, _wf, log_text in runs:
                if run_id == rid:
                    return subprocess.CompletedProcess(args, 0, log_text, "")
        raise AssertionError(f"unknown run {rid}")


def _drive(monkeypatch, capsys, fake: _FakeGh,
           repo_map: dict[str, str], *, quiet: bool = True) -> str:
    monkeypatch.setattr(clb, "_gh", fake)
    monkeypatch.setattr(clb, "load_repo_map", lambda: repo_map)
    assert clb.main(["--hours", "1"]) == 0
    got = capsys.readouterr()
    if quiet:
        # DRE-9165: the one line a repo prints when it is done is all it holds.
        assert all(ln.startswith("budget-check: ") for ln in got.err.splitlines())
    return got.out


def _fallback_section(out: str) -> list[str]:
    """Every line printed after the spend table's TOTAL line and the two
    `runs listed` lines under it (DRE-9165), up to the direct-read table that
    follows the fallback table (DRE-8897)."""
    lines = out.splitlines()
    total = next(i for i, line in enumerate(lines) if line.startswith("TOTAL")) + 2
    end = next((i for i, line in enumerate(lines)
                if line.startswith("direct Linear reads")), len(lines))
    return [line for line in lines[total + 1:end] if line.strip()]


def _two_repos() -> tuple[_FakeGh, dict[str, str]]:
    fake = _FakeGh({
        "o/atlas": [
            (101, "Agent Task", _log(
                _mode("on"), _SPENT_20,
                _fallback(_SNAPSHOT, "cards", "stale"),
                _fallback(_SNAPSHOT, "cards", "stale"),
                _fallback(_SNAPSHOT, "board", "unavailable"),
            )),
            (102, "Agent Task", _log(
                _mode("on"), _fallback(_SNAPSHOT, "cards", "stale"),
            )),
        ],
        "o/portico": [
            (201, "Reconcile", _log(
                _mode("on"), _SPENT_20,
                _fallback(_SWEEP, "dependents", "refused"),
                _fallback(_SWEEP, "dependents", "refused"),
            )),
        ],
    })
    return fake, {"atlas": "o/atlas", "portico": "o/portico"}


def test_a_line_is_parsed_whole_and_a_reader_may_hold_spaces():
    log = _log(_fallback(_SNAPSHOT, "workflow-states", "not-found"))
    assert clb.fallbacks_from_log(log) == [(_SNAPSHOT, "workflow-states", "not-found")]


def test_an_echoed_line_that_does_not_open_its_line_is_not_counted():
    """GitHub echoes a step's script before running it; a script that merely
    names the tag must never be read as a fallback (DRE-5201)."""
    log = _log(
        '\x1b[36;1mecho \'read-fallback: reader="x / y / z.py" read=cards reason=stale\'\x1b[0m',
        '# read-fallback: reader="x / y / z.py" read=cards reason=stale',
        'grep "read-fallback: reader=" log.txt',
    ) + 'echo read-fallback: reader="a / b / c.py" read=board reason=stale\n'
    assert clb.fallbacks_from_log(log) == []


def test_one_row_per_repo_reader_read_reason_after_the_spend_table(monkeypatch, capsys):
    fake, repo_map = _two_repos()
    out = _drive(monkeypatch, capsys, fake, repo_map)
    section = _fallback_section(out)
    assert section, out
    assert "reads only" in section[0]
    rows = [line for line in section
            if line.startswith(("atlas ", "portico "))]
    assert len(rows) == 3
    # sorted by count, then by name: atlas cards (3), portico dependents (2),
    # atlas board (1)
    assert rows[0].startswith("atlas ") and _SNAPSHOT in rows[0]
    assert rows[0].split()[-3:] == ["cards", "stale", "3"]
    assert rows[1].startswith("portico ") and _SWEEP in rows[1]
    assert rows[1].split()[-3:] == ["dependents", "refused", "2"]
    assert rows[2].startswith("atlas ") and _SNAPSHOT in rows[2]
    assert rows[2].split()[-3:] == ["board", "unavailable", "1"]
    # the fallback table comes AFTER the spend table, which still ends in TOTAL
    assert out.index("TOTAL") < out.index(_SNAPSHOT)


def test_the_spend_table_is_byte_identical_to_render_table(monkeypatch, capsys):
    """The spend table's rows and totals are what render_table prints for the
    same logs, byte for byte: the second table only follows it."""
    fake, repo_map = _two_repos()
    out = _drive(monkeypatch, capsys, fake, repo_map)
    observations = [(slug, wf, log_text)
                    for slug, repo in sorted(repo_map.items())
                    for _rid, wf, log_text in fake.runs[repo]]
    spend = clb.render_table(clb.aggregate(observations), [], hours=1)
    assert out.startswith(spend + "\n")
    assert "read-fallback" not in spend and _SNAPSHOT not in spend


def test_the_reader_makes_no_gh_call_it_did_not_make_before(monkeypatch, capsys):
    """One `api` listing per repo and one `run view --log` per run — the logs
    the spend table already fetched — and nothing else."""
    fake, repo_map = _two_repos()
    _drive(monkeypatch, capsys, fake, repo_map)
    assert sorted(c[:2] for c in fake.calls) == (
        [("api", "--paginate")] * 2 + [("run", "view")] * 3
    )


def test_a_door_off_run_is_named_and_adds_nothing(monkeypatch, capsys):
    """A repo whose door is not on never asked it, so it prints no fallback
    line — that must read as `door off`, never as zero fallbacks."""
    fake = _FakeGh({
        "o/atlas": [
            (301, "Agent Task", _log(_mode("off"),
                                     _fallback(_SNAPSHOT, "cards", "stale"))),
            (302, "Agent Task", _log(_mode("shadow"),
                                     _fallback(_SNAPSHOT, "board", "stale"))),
        ],
        "o/portico": [
            (401, "Reconcile", _log(_mode("on"),
                                    _fallback(_SWEEP, "search", "refused"))),
            (402, "Medic", _log(_mode("shadow"))),
        ],
    })
    out = _drive(monkeypatch, capsys, fake,
                 {"atlas": "o/atlas", "portico": "o/portico"})
    section = _fallback_section(out)
    rows = [line for line in section if line.startswith(("atlas ", "portico "))]
    assert len(rows) == 1
    assert rows[0].split()[-3:] == ["search", "refused", "1"]
    assert _SNAPSHOT not in out
    door = [line for line in section if line.startswith("door off")]
    assert len(door) == 1
    assert "atlas 2 run(s)" in door[0] and "portico 1 run(s)" in door[0]


def test_no_fallback_line_says_so_in_words_with_the_runs_read_in_mode_on(
        monkeypatch, capsys):
    fake = _FakeGh({
        "o/atlas": [
            (501, "Agent Task", _log(_mode("on"), _SPENT_20)),
            (502, "Agent Task", _log(_mode("on"))),
            (503, "Medic", _log(_SPENT_20)),
        ],
        "o/portico": [(601, "Reconcile", _log(_mode("on")))],
    })
    out = _drive(monkeypatch, capsys, fake,
                 {"atlas": "o/atlas", "portico": "o/portico"})
    section = _fallback_section(out)
    words = [line for line in section if "no read-fallback line" in line]
    assert len(words) == 1, section
    assert "3 run(s) read in mode on" in words[0]
    # never an empty table: no header row and no rule with nothing under it
    assert not any(line.startswith("repo ") for line in section)
    assert not any(line.startswith("door off") for line in section)


def test_an_unreadable_repo_prints_unknown_in_the_fallback_table_never_0(
        monkeypatch, capsys):
    fake = _FakeGh({"o/atlas": [(701, "Agent Task", _log(_mode("on")))]},
                   unreadable=("o/vault",))
    out = _drive(monkeypatch, capsys, fake, {"atlas": "o/atlas", "vault": "o/vault"},
                 quiet=False)
    section = _fallback_section(out)
    vault = [line for line in section if line.startswith("vault ")]
    assert len(vault) == 1
    assert clb.UNKNOWN in vault[0]
    assert not any(tok == "0" for tok in vault[0].split())
    assert any("no read-fallback line" in line and "1 run(s) read in mode on" in line
               for line in section)


def test_the_docstring_describes_the_second_table():
    doc = " ".join(clb.__doc__.split())
    assert "read-fallback:" in doc and "door off" in doc
