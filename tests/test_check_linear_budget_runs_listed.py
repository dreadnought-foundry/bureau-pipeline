"""The budget check reads every run in its window (DRE-9165).

It listed a repo's runs with `gh run list --limit 100` and kept the ones
created in the window. On 2026-11-03, for the hour ending 12:16 PT, GitHub
listed 183 runs in bureau-pipeline and the check read at most 100: its
fallback table showed 3 fallbacks where the run logs held 16. And under a
proof's `timeout 580` it was stopped with rc 124 and nothing on stdout.

So the listing asks GitHub for exactly the window, every page, as one JSON
document (`gh api --paginate --slurp …/actions/runs?created=<a>..<b>`);
`--end` names the window's end; a run still in progress is never asked for
its log; logs are read `LOG_WORKERS` at a time; and each repo says on stderr
when it is done. Two lines follow the spend table's TOTAL line on every run:

    runs listed, <N>h to <end>: <slug> <count>, …
    runs in progress, not read: <slug> <run id>, …

These drive `main` off a fake standing in for `_gh` — no live `gh` — the
clock off `_monotonic`, and "now" off `_now`.
"""

import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
)

import check_linear_budget as clb  # noqa: E402

_PREFIX = "sweep\tSweep\t2026-11-03T20:00:00.1234567Z "
_NOW = datetime(2026, 11, 5, 20, 0, 0, tzinfo=timezone.utc)
_REPOS = {"bureau-pipeline": "o/bureau-pipeline"}


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(*lines: str) -> str:
    return "\n".join(_PREFIX + line for line in lines) + "\n"


def _spent(n: int, process: str) -> str:
    return _log(
        f"linear-budget: 900 → {900 - n} (spent {n} this run; window resets "
        "12:00 PT; budget: fleet)",
        f"linear-calls: {n} request(s) this run (process: {process}; budget: fleet)",
        "read-door: mode on (BUREAU_READ=on, read once for this run)",
        'read-fallback: reader="Reconcile / sweep / reconcile.py" read=cards '
        "reason=missing-field",
        'linear-read: reader="Reconcile / sweep / reconcile.py" '
        f"site=reconcile.build_started count={n} budget=fleet process={process} "
        "caller=linear_ops.get_issue",
        'linear-door: reader="Reconcile / sweep / reconcile.py" '
        f"served={n} fallbacks=cards/missing-field:1 budget=fleet process={process}",
    )


def _run(rid: int, name: str = "Reconcile", created: datetime | None = None,
         status: str = "completed") -> dict:
    """One run as the REST listing carries it."""
    created = created or _NOW - timedelta(minutes=30)
    return {"id": rid, "name": name, "display_title": f"a run's own title {rid}",
            "created_at": _iso(created), "status": status}


def _paged(runs: list[dict], per_page: int = 100, total: int | None = None) -> list[dict]:
    """What `gh api --paginate --slurp` prints: one JSON array of page objects."""
    total = len(runs) if total is None else total
    chunks = [runs[i:i + per_page] for i in range(0, len(runs), per_page)] or [[]]
    return [{"total_count": total, "workflow_runs": chunk} for chunk in chunks]


class _FakeGh:
    """Stands in for `_gh`, recording every call. The listing answers
    `pages[repo]` (as `_paged` builds it), or rc 1 for a repo in `fail`;
    `run view <id> --log` answers `logs[id]`, or a log spending 1 by default.
    `on_view` is called inside each view with the run id."""

    def __init__(self, pages: dict[str, list[dict]], logs=None, fail=(), on_view=None):
        self.pages, self.logs, self.fail, self.on_view = pages, logs or {}, fail, on_view
        self.calls: list[tuple[str, ...]] = []
        self.lock = threading.Lock()

    def urls(self) -> list[str]:
        return [c[-1] for c in self.calls if c[0] == "api"]

    def listed(self) -> list[str]:
        return [u.split("/actions/")[0].removeprefix("repos/") for u in self.urls()]

    def viewed(self) -> list[int]:
        return [int(c[2]) for c in self.calls if c[:2] == ("run", "view")]

    def __call__(self, *args):
        with self.lock:
            self.calls.append(args)
        if args[0] == "api":
            assert args[:3] == ("api", "--paginate", "--slurp"), args
            repo = args[-1].split("/actions/")[0].removeprefix("repos/")
            if repo in self.fail:
                first = json.dumps(self.pages.get(repo, [])[:1])
                return subprocess.CompletedProcess(
                    args, 1, first, "gh: HTTP 502: Server Error (page 2)")
            return subprocess.CompletedProcess(
                args, 0, json.dumps(self.pages.get(repo, _paged([]))), "")
        assert args[:2] == ("run", "view") and "--log" in args, args
        rid = int(args[2])
        if self.on_view:
            self.on_view(rid)
        return subprocess.CompletedProcess(
            args, 0, self.logs.get(rid, _spent(1, f"{rid}@run")), "")


class _Clock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _seams(monkeypatch):
    """Nothing sleeps, "now" is `_NOW`, and the clock stands still."""
    slept: list[float] = []
    monkeypatch.setattr(clb, "_sleep", slept.append)
    monkeypatch.setattr(clb, "_now", lambda: _NOW)
    monkeypatch.setattr(clb, "_monotonic", _Clock())
    return slept


def _main(monkeypatch, capsys, fake, *argv, repos=None):
    monkeypatch.setattr(clb, "load_repo_map", lambda: dict(repos or _REPOS))
    monkeypatch.setattr(clb, "_gh", fake)
    rc = clb.main(list(argv))
    got = capsys.readouterr()
    return rc, got.out, got.err


def _total_line(out: str) -> str:
    return next(line for line in out.splitlines() if line.startswith("TOTAL"))


def _listed_lines(out: str) -> tuple[str, str]:
    """The two lines directly after the spend table's TOTAL line."""
    lines = out.splitlines()
    i = lines.index(_total_line(out))
    return lines[i + 1], lines[i + 2]


def _window(url: str) -> str:
    return parse_qs(urlsplit(url).query)["created"][0]


# ── the listing: the window, every page ─────────────────────────────────────


def test_a_183_run_hour_over_two_pages_is_read_whole(monkeypatch, capsys):
    runs = [_run(1000 + i, "Pipeline Medic" if i % 2 else "Reconcile",
                 created=_NOW - timedelta(seconds=10 + i * 15)) for i in range(183)]
    fake = _FakeGh({"o/bureau-pipeline": _paged(runs)})
    rc, out, _err = _main(monkeypatch, capsys, fake, "--hours", "1")
    assert rc == 0
    assert len(fake.pages["o/bureau-pipeline"]) == 2
    assert sorted(fake.viewed()) == sorted(r["id"] for r in runs)
    assert _total_line(out).split()[:3] == ["TOTAL", "183", "183"]
    listed, progress = _listed_lines(out)
    assert listed == f"runs listed, 1h to {_iso(_NOW)}: bureau-pipeline 183"
    assert progress == "runs in progress, not read: none"
    assert "183 fallback(s) in 183 run(s) read in mode on" in out


def test_the_listing_is_one_paged_get_of_the_window(monkeypatch, capsys):
    fake = _FakeGh({"o/bureau-pipeline": _paged([_run(1)])})
    _main(monkeypatch, capsys, fake, "--hours", "1")
    (call,) = [c for c in fake.calls if c[0] == "api"]
    assert call == (
        "api", "--paginate", "--slurp",
        "repos/o/bureau-pipeline/actions/runs?created=2026-11-05T19:00:00Z.."
        "2026-11-05T19:59:59Z&per_page=100")


def test_a_listing_that_fails_on_its_second_page_is_unknown_never_the_first(
        monkeypatch, capsys):
    runs = [_run(1000 + i) for i in range(150)]
    fake = _FakeGh({"o/bureau-pipeline": _paged(runs), "o/portico": _paged([_run(7)])},
                   fail=("o/bureau-pipeline",))
    rc, out, _err = _main(monkeypatch, capsys, fake, "--hours", "1",
                          repos={**_REPOS, "portico": "o/portico"})
    assert rc == 0
    assert fake.viewed() == [7]
    for heading in ("linear budget, last 1h", "read fallbacks, last 1h",
                    "direct Linear reads, last 1h", "door answers, last 1h"):
        start = next(i for i, ln in enumerate(out.splitlines()) if ln.startswith(heading))
        section = out.splitlines()[start:]
        section = section[:next((i for i, ln in enumerate(section) if not ln.strip()),
                                len(section))]
        rows = [ln for ln in section if ln.startswith("bureau-pipeline")]
        assert rows and all(clb.UNKNOWN in ln for ln in rows), heading
    listed, _progress = _listed_lines(out)
    assert listed == f"runs listed, 1h to {_iso(_NOW)}: bureau-pipeline UNKNOWN, portico 1"


def test_a_total_count_past_the_cap_is_unknown_never_the_runs_given(monkeypatch, capsys):
    runs = [_run(1000 + i) for i in range(150)]
    fake = _FakeGh({"o/bureau-pipeline": _paged(runs, total=1001)})
    rc, out, err = _main(monkeypatch, capsys, fake, "--hours", "1")
    assert rc == 0
    assert fake.viewed() == []
    assert _total_line(out).split()[:3] == ["TOTAL", "0", "0"]
    assert "(+ 1 repo(s) UNKNOWN)" in _total_line(out)
    assert _listed_lines(out)[0].endswith(": bureau-pipeline UNKNOWN")
    assert "1001" in err and "1,000" in err


def test_a_run_on_both_pages_is_read_once(monkeypatch, capsys):
    """A run that slides across a page boundary while the pages are read
    appears on both; it is kept once, by `id`."""
    pages = [{"total_count": 3, "workflow_runs": [_run(1), _run(2)]},
             {"total_count": 3, "workflow_runs": [_run(2), _run(3)]}]
    fake = _FakeGh({"o/bureau-pipeline": pages})
    rc, out, _err = _main(monkeypatch, capsys, fake, "--hours", "1")
    assert rc == 0
    assert sorted(fake.viewed()) == [1, 2, 3]
    assert _listed_lines(out)[0].endswith(": bureau-pipeline 3")
    assert _total_line(out).split()[:3] == ["TOTAL", "3", "3"]


def test_a_run_outside_the_window_is_not_read_and_the_end_second_is_excluded(
        monkeypatch, capsys):
    """GitHub's `created=<a>..<b>` includes both ends, so the listing asks
    for `<start>..<end − 1 s>`; and a run created at or after `<end>`, or
    before `<start>`, is not read or counted even when GitHub sends it."""
    end = datetime(2026, 11, 5, 19, 0, 0, tzinfo=timezone.utc)
    start = end - timedelta(hours=1)
    runs = [_run(1, created=start), _run(2, created=end - timedelta(seconds=1)),
            _run(3, created=end), _run(4, created=start - timedelta(seconds=1)),
            _run(5, created=end + timedelta(minutes=5))]
    fake = _FakeGh({"o/bureau-pipeline": _paged(runs)})
    rc, out, _err = _main(monkeypatch, capsys, fake, "--hours", "1", "--end", _iso(end))
    assert rc == 0
    assert sorted(fake.viewed()) == [1, 2]
    (url,) = fake.urls()
    assert _window(url) == "2026-11-05T18:00:00Z..2026-11-05T18:59:59Z"
    assert _listed_lines(out)[0] == "runs listed, 1h to 2026-11-05T19:00:00Z: bureau-pipeline 2"


# ── --end ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("given", ["2026-11-03T19:00:00Z", "2026-11-03T11:00:00-08:00"])
def test_end_two_days_back_reads_only_that_hour(monkeypatch, capsys, given):
    end = datetime(2026, 11, 3, 19, 0, 0, tzinfo=timezone.utc)
    runs = [_run(1, created=end - timedelta(minutes=59)),
            _run(2, created=end - timedelta(minutes=1)),
            _run(3, created=end - timedelta(hours=2)),
            _run(4, created=_NOW - timedelta(minutes=5))]
    fake = _FakeGh({"o/bureau-pipeline": _paged(runs)})
    rc, out, _err = _main(monkeypatch, capsys, fake, "--hours", "1", "--end", given)
    assert rc == 0
    assert sorted(fake.viewed()) == [1, 2]
    (url,) = fake.urls()
    assert _window(url) == "2026-11-03T18:00:00Z..2026-11-03T18:59:59Z"
    assert _listed_lines(out)[0] == "runs listed, 1h to 2026-11-03T19:00:00Z: bureau-pipeline 2"


@pytest.mark.parametrize("given", ["yesterday", "2026-13-40T99:00:00Z", "2026-11-05T21:00:00Z"])
def test_an_end_that_does_not_parse_or_is_in_the_future_exits_2(monkeypatch, capsys, given):
    fake = _FakeGh({"o/bureau-pipeline": _paged([_run(1)])})
    rc, out, err = _main(monkeypatch, capsys, fake, "--end", given)
    assert rc == 2
    assert fake.calls == []
    assert out == ""
    assert len(err.strip().splitlines()) == 1
    assert "--end" in err and given in err


# ── --repo and --workflow, with the listing ─────────────────────────────────


def test_repo_with_end_lists_and_reads_only_that_repo(monkeypatch, capsys):
    end = datetime(2026, 11, 4, 19, 0, 0, tzinfo=timezone.utc)
    inside = end - timedelta(minutes=10)
    fake = _FakeGh({
        "o/atlas": _paged([_run(11, created=inside)]),
        "o/bureau-pipeline": _paged([_run(21, created=inside), _run(22, created=inside)]),
        "o/portico": _paged([_run(31, created=inside)]),
    })
    repos = {"atlas": "o/atlas", "bureau-pipeline": "o/bureau-pipeline",
             "portico": "o/portico"}
    rc, out, err = _main(monkeypatch, capsys, fake, "--repo", "bureau-pipeline",
                         "--end", _iso(end), repos=repos)
    assert rc == 0
    assert fake.listed() == ["o/bureau-pipeline"]
    assert sorted(fake.viewed()) == [21, 22]
    assert not any("atlas" in a or "portico" in a for call in fake.calls for a in call)
    assert "atlas" not in out and "portico" not in out
    assert _listed_lines(out)[0] == (
        "runs listed, 1h to 2026-11-04T19:00:00Z: bureau-pipeline 2")
    assert "atlas" not in err and "portico" not in err


def test_workflow_counts_every_run_listed_and_fetches_only_its_logs(monkeypatch, capsys):
    runs = [_run(1, "Reconcile"), _run(2, "Pipeline Medic"), _run(3, "Agent Plan"),
            _run(4, "Reconcile")]
    fake = _FakeGh({"o/bureau-pipeline": _paged(runs)})
    rc, out, _err = _main(monkeypatch, capsys, fake, "--workflow", "Reconcile")
    assert rc == 0
    assert sorted(fake.viewed()) == [1, 4]
    assert _listed_lines(out)[0].endswith(": bureau-pipeline 4")
    assert _total_line(out).split()[:3] == ["TOTAL", "2", "2"]


# ── a run still in progress ─────────────────────────────────────────────────


def test_a_run_in_progress_is_never_asked_for_its_log(monkeypatch, capsys, _seams):
    runs = [_run(501, status="in_progress"), _run(502), _run(503)]
    fake = _FakeGh({"o/bureau-pipeline": _paged(runs)})
    rc, out, err = _main(monkeypatch, capsys, fake, "--hours", "1")
    assert rc == 0
    assert sorted(fake.viewed()) == [502, 503]
    assert _seams == []
    listed, progress = _listed_lines(out)
    assert listed.endswith(": bureau-pipeline 3")
    assert progress == "runs in progress, not read: bureau-pipeline 501"
    assert _total_line(out).split()[:3] == ["TOTAL", "2", "2"]
    assert "skipped" not in out and "501" not in err


def test_runs_in_progress_are_named_in_slug_order(monkeypatch, capsys):
    fake = _FakeGh({
        "o/atlas": _paged([_run(12, status="queued"), _run(11, status="in_progress")]),
        "o/bureau-pipeline": _paged([_run(21, status="waiting"), _run(22)]),
    })
    rc, out, _err = _main(monkeypatch, capsys, fake, "--hours", "1",
                          repos={"atlas": "o/atlas", "bureau-pipeline": "o/bureau-pipeline"})
    assert rc == 0
    assert fake.viewed() == [22]
    assert _listed_lines(out) == (
        f"runs listed, 1h to {_iso(_NOW)}: atlas 2, bureau-pipeline 2",
        "runs in progress, not read: atlas 11, atlas 12, bureau-pipeline 21",
    )


# ── logs read several at a time ─────────────────────────────────────────────


def _mixed_runs() -> tuple[list[dict], dict[int, str]]:
    """Twenty runs whose logs differ: spends, an empty log, and one log `gh`
    never returns — so the order the rows are added in could show."""
    runs = [_run(700 + i, ("Reconcile", "Agent Task", "Pipeline Medic")[i % 3])
            for i in range(20)]
    logs = {r["id"]: _spent(r["id"] % 17 + 1, f"{r['id']}@p") for r in runs}
    logs[705] = ""
    return runs, logs


class _Holding(_FakeGh):
    """Holds each `run view` until a second one has started, and counts how
    many are in flight at once. The log `gh` never returns is run 711."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.in_flight = self.most = self.started = 0
        self.second = threading.Event()
        self.timed_out = False

    def __call__(self, *args):
        if args[0] == "api":
            return super().__call__(*args)
        with self.lock:
            self.in_flight += 1
            self.started += 1
            self.most = max(self.most, self.in_flight)
            if self.started >= 2:
                self.second.set()
        try:
            if not self.second.wait(timeout=5):
                self.timed_out = True
            time.sleep(0.02)
            if int(args[2]) == 711:
                with self.lock:
                    self.calls.append(args)
                return subprocess.CompletedProcess(args, 1, "", "log not found: 711")
            return super().__call__(*args)
        finally:
            with self.lock:
                self.in_flight -= 1


def test_logs_are_read_several_at_a_time_never_more_than_log_workers(monkeypatch, capsys):
    assert clb.LOG_WORKERS == 8
    runs, logs = _mixed_runs()
    fake = _Holding({"o/bureau-pipeline": _paged(runs)}, logs)
    rc, out, err = _main(monkeypatch, capsys, fake, "--hours", "1")
    assert rc == 0
    assert not fake.timed_out
    assert 1 < fake.most <= clb.LOG_WORKERS
    assert "711" in err

    class _Gone(_FakeGh):
        def __call__(self, *args):
            if args[:3] == ("run", "view", "711"):
                self.calls.append(args)
                return subprocess.CompletedProcess(args, 1, "", "log not found: 711")
            return super().__call__(*args)

    monkeypatch.setattr(clb, "LOG_WORKERS", 1)
    _rc, one_at_a_time, _err = _main(
        monkeypatch, capsys, _Gone({"o/bureau-pipeline": _paged(runs)}, logs), "--hours", "1")
    assert out == one_at_a_time
    assert "(1 run(s) skipped: log not available after 3 attempts — bureau-pipeline 711)" in out
    assert "(1 run(s) empty log: no job ran)" in out


def test_a_deadline_reached_partway_through_the_parallel_reads(monkeypatch, capsys):
    """Eight reads start and hold; the eighth moves the clock past the
    deadline before any of them finishes. No read starts after it, the eight
    count, and the twelve never started are named in the footer."""
    clock = _Clock()
    monkeypatch.setattr(clb, "_monotonic", clock)
    runs = [_run(900 + i) for i in range(20)]
    started: list[int] = []
    gate = threading.Event()
    lock = threading.Lock()

    def on_view(rid):
        with lock:
            started.append(rid)
            assert clock.now < 100, f"run {rid} started after the deadline"
            if len(started) == clb.LOG_WORKERS:
                clock.now = 101.0
                gate.set()
        assert gate.wait(timeout=5)

    fake = _FakeGh({"o/bureau-pipeline": _paged(runs)}, on_view=on_view)
    rc, out, _err = _main(monkeypatch, capsys, fake, "--hours", "1", "--deadline", "100")
    assert rc == 0
    assert sorted(started) == [r["id"] for r in runs[:8]]
    assert sorted(fake.viewed()) == [r["id"] for r in runs[:8]]
    tail = _total_line(out)
    assert tail.split()[:3] == ["TOTAL", "8", "8"]
    assert "(stopped at the deadline (100s): bureau-pipeline 12 run(s) not read)" in tail
    assert _listed_lines(out)[0].endswith(": bureau-pipeline 20")


# ── how far it has got ──────────────────────────────────────────────────────

_PROGRESS = re.compile(r"^budget-check: (\S+) (\d+) run log\(s\) read in (\d+\.\d)s$")


def test_each_repo_says_on_stderr_when_it_is_done_as_it_finishes(monkeypatch, capsys):
    clock = _Clock()
    monkeypatch.setattr(clb, "_monotonic", clock)
    # One read at a time, so the clock the fake moves adds up exactly.
    monkeypatch.setattr(clb, "LOG_WORKERS", 1)
    err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err)
    seen_at_listing: dict[str, str] = {}

    class _Watching(_FakeGh):
        def __call__(self, *args):
            if args[0] == "api":
                seen_at_listing[args[-1].split("/")[2]] = err.getvalue()
            else:
                clock.now += 1.5
            return super().__call__(*args)

    fake = _Watching({"o/atlas": _paged([_run(1), _run(2, status="in_progress")]),
                      "o/bureau-pipeline": _paged([_run(3), _run(4), _run(5)])})
    rc, _out, _ = _main(monkeypatch, capsys, fake, "--hours", "1",
                        repos={"atlas": "o/atlas", "bureau-pipeline": "o/bureau-pipeline"})
    assert rc == 0
    lines = err.getvalue().splitlines()
    assert lines == ["budget-check: atlas 1 run log(s) read in 1.5s",
                     "budget-check: bureau-pipeline 3 run log(s) read in 4.5s"]
    assert all(_PROGRESS.match(line) for line in lines)
    assert seen_at_listing["atlas"] == ""
    assert seen_at_listing["bureau-pipeline"] == lines[0] + "\n"


# ── the proof's call, as the contract writes it ─────────────────────────────


def test_the_proofs_call_prints_the_two_lines_after_the_first_total(monkeypatch, capsys):
    end = datetime(2026, 11, 4, 19, 0, 0, tzinfo=timezone.utc)
    runs = [_run(1, created=end - timedelta(minutes=3)),
            _run(2, created=end - timedelta(minutes=2), status="in_progress")]
    fake = _FakeGh({"o/bureau-pipeline": _paged(runs)})
    rc, out, err = _main(monkeypatch, capsys, fake, "--hours", "1", "--end", _iso(end),
                         "--repo", "bureau-pipeline", "--deadline", "480",
                         repos={**_REPOS, "portico": "o/portico"})
    assert rc == 0
    lines = out.splitlines()
    i = lines.index(_total_line(out))
    assert lines[i + 1:i + 4] == [
        "runs listed, 1h to 2026-11-04T19:00:00Z: bureau-pipeline 2",
        "runs in progress, not read: bureau-pipeline 2",
        "",
    ]
    assert lines[i + 4].startswith("read fallbacks, last 1h")
    assert err.splitlines() == ["budget-check: bureau-pipeline 1 run log(s) read in 0.0s"]


def test_a_repo_the_deadline_never_reached_is_unknown_on_the_line(monkeypatch, capsys):
    fake = _FakeGh({"o/bureau-pipeline": _paged([_run(1)])})
    rc, out, _err = _main(monkeypatch, capsys, fake, "--deadline", "0",
                          repos={**_REPOS, "portico": "o/portico"})
    assert rc == 0
    assert fake.calls == []
    assert _listed_lines(out) == (
        f"runs listed, 1h to {_iso(_NOW)}: bureau-pipeline UNKNOWN, portico UNKNOWN",
        "runs in progress, not read: none",
    )


# ── the words ───────────────────────────────────────────────────────────────


def test_the_docstring_says_the_window_is_read_whole_and_why():
    doc = " ".join(clb.__doc__.split())
    for words in ("183", "100", "1,000", "`--end", "LOG_WORKERS", "in progress",
                  "budget-check:", "rc 124", "gh api --paginate --slurp",
                  "`gh run view --log`"):
        assert words in doc, words
    assert "gh run list" not in doc
