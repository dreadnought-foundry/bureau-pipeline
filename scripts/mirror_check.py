"""Run agent-bureau's mirror tests against the candidate before `stable` moves
(DRE-6496).

WHAT HAPPENED ON 2026-10-09
---------------------------
One bureau-pipeline change, the `operator-step` vocabulary (DRE-6227 #836 and
DRE-6228), merged and was promoted to `stable` around 03:00 PT. It then broke
agent-bureau's CI three separate times that day — the read door's e2e at 10:25
PT, the relay's lane guard at about 13:50 PT (live behavior in the deployed
relay as well), and the open-holds report at 14:55 PT — and each break held
every agent-bureau pull request until someone fixed it by hand.

agent-bureau mirrors several of this repo's vocabularies because its CI checks
out one repo (the two-copies rule). Each mirror has a drift test that reads
bureau-pipeline at `stable`, checked out as `.bureau-pipeline` and named by
`BUREAU_PIPELINE_DIR`. So a change here was detected only AFTER it was
promoted, by every agent-bureau pull request at once.

WHAT THIS MODULE DOES
---------------------
`promote-channel.yml` calls it only where the existing decision would promote,
so a refused run never clones agent-bureau. Three subcommands:

  discover  names the requirement files the install step needs, and the count:
            agent-bureau's own, then this repository's `requirements-dev.txt`,
            which pins the test tools the check runs — pytest, pytest-cov and
            pytest-asyncio (DRE-6622, DRE-6657).
  run       the check itself — writes the result file `promote_channel.py`
            reads, and the `⏱ mirror check` line to the run summary.
  card      files the one card a refusal owes in agent-bureau, or comments on
            the open one.

The check, step by step:

  1. Which tests: every `test_*.py` / `*_test.py` in the agent-bureau checkout
     whose text names `BUREAU_PIPELINE_DIR` or `.bureau-pipeline`. Discovered on
     the day — on 2026-10-09 that was 103 files — and kept nowhere.
  2. The candidate sits at `<agent-bureau>/.bureau-pipeline` AND is exported as
     `BUREAU_PIPELINE_DIR`, so both ways a test finds the pipeline read it.
  3. pytest from agent-bureau's root, once it is proven to start, with
     `--no-cov` (agent-bureau's coverage floor cannot be met by its mirror
     tests alone, DRE-6622), ONE PROCESS PER SUITE (DRE-6670). A test's suite
     is the first component of its root-relative path — `cloud`, `console`,
     `.` at the root — read off the discovered files and nothing else. One
     process for every file let three relay files' `jwt` stub reach the
     console's tests: 1238 setup errors on run 38072065598, none of them the
     candidate's. Each suite runs its files in sorted order, inside one
     shared budget.
  4. Every suite with a red test on the candidate — a failure, or a setup,
     teardown or collection error — runs again, whole: the same files, in
     the same order, in the same process layout, with `stable` in the
     candidate's place. A suite with nothing red is not re-read, and says so.
     Each red test is judged by its outcome in the two identical runs: red on
     both is agent-bureau's own red, or the run's own doing, named `already
     red on stable`, and refuses nothing. Only a test red on the candidate
     and not on `stable` refuses. (Until DRE-6657 the re-read ran only the
     failing files, and a test that failed beside the relay's stub passed
     alone and read as the candidate's red.)
  5. Each suite's totals for both runs — passed, failed, errors, skipped, as
     pytest counts them — go in the result file's `suites`, and side by side,
     one line per suite, in the run summary, the step's output and the
     refusal's card.
  6. The bureau-pipeline file a failing test mirrors is read off the test's own
     string literals — a literal, or a chain of them joined by `/` or a call,
     that names a file in the candidate checkout. None found reads `mirrors:
     not read from the test`; it is never guessed.

A check that COULD NOT RUN — a token that did not mint, a checkout or an
install that failed, pytest that would not start, a run past its time limit —
is `blocked`, never `failed`. That is the `harness-blocked-by-sandbox`
distinction (DRE-3076): nothing was proven either way, and a reader sent to the
candidate's diff would be looking at code nobody judged. So is one SUITE that
could not run — pytest exiting other than 0 or 1, writing no report, or
running past the budget: that suite's mirrors are unproven and a promotion is
all or nothing, so it blocks the check, while the other suites still run and
are still reported, the blocked one first with its reason.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath

#: The check's three answers. `promote_channel.with_mirror` reads them.
MIRROR_PASSED = "passed"
MIRROR_FAILED = "failed"
MIRROR_BLOCKED = "blocked"

#: The two ways an agent-bureau test finds bureau-pipeline. A test whose text
#: names either is a mirror test; the candidate is put behind both.
PIPELINE_ENV = "BUREAU_PIPELINE_DIR"
PIPELINE_DIRNAME = ".bureau-pipeline"
MARKERS = (PIPELINE_ENV, PIPELINE_DIRNAME)

#: The suite of a test at agent-bureau's root, which has no first directory.
ROOT_SUITE = "."

#: The outcomes that are red (DRE-6670): a failure in the test body, and an
#: error — in setup, in teardown, or collecting the file. Judged alike; counted
#: apart, as pytest counts them.
RED = ("failed", "error")

#: The counts each run's totals carry, in the order they are written.
TOTALS = ("passed", "failed", "errors", "skipped")

#: What a suite that held no failure on the candidate says in place of its
#: `stable` totals.
NOT_RE_READ = "stable not re-read: nothing failed on the candidate"

#: pytest's default `python_files`.
TEST_FILES = ("test_*.py", "*_test.py")

#: Never searched. `.bureau-pipeline` above all: the candidate is checked out
#: there, and bureau-pipeline's own suite names that directory everywhere.
SKIP_DIRS = (PIPELINE_DIRNAME, ".git", "node_modules", ".venv", "venv",
             "__pycache__", ".mirror-venv")

#: The requirement files the install reads, at agent-bureau's root and in every
#: directory between it and a discovered test.
REQUIREMENTS = "requirements*.txt"

#: The check's own test tools (DRE-6622): this repository's pinned manifest,
#: at the workspace root, installed after agent-bureau's files. agent-bureau's
#: requirements declare no pytest — its CI installs its test tools inline, in
#: its own workflow jobs — and a build run here cannot read agent-bureau at
#: all. pytest is the tool this check invokes, so its pin is this check's own,
#: and pytest-cov beside it is what makes `--no-cov` a switch pytest accepts.
#: pytest-asyncio is the third (DRE-6657): without it every agent-bureau async
#: test fails, on `stable` and the candidate alike.
TOOLS_MANIFEST = "requirements-dev.txt"

#: Appended to every pytest run, the candidate's and `stable`'s (DRE-6622). agent-bureau's pytest settings
#: enforce a coverage floor, and a run of the mirror tests alone reads ~16%
#: against it, so pytest exits 1 with no failing test and the check could only
#: ever say `blocked`. `--no-cov` switches coverage, and the floor with it,
#: off; every other setting agent-bureau keeps in `addopts` stays in force,
#: which is why it is not `-o addopts=`.
NO_COVERAGE = "--no-cov"

#: What a failing test that names no candidate file reads in place of a file.
NOT_READ = "not read from the test"

#: The step's own `timeout-minutes` is 15; the check stops itself a minute
#: earlier so it can still write a `blocked` result rather than be killed with
#: nothing written (which `promote_channel.py` reads as blocked anyway).
BUDGET_SECONDS = 14 * 60

#: The card a refusal files. A fleet agent builds it, so neither `automation`
#: nor the CEO's `hand-built` mark.
CARD_REPO = "agent-bureau"
CARD_LABELS = (f"repo:{CARD_REPO}", "agent:engineer", "initiative:bureau", "Bug")

#: The longest card title the check writes (DRE-6657). Linear's `issueCreate`
#: takes 1–255 characters; run 38072065598's title named 23 files in 716 and
#: was refused, so the card was owed. 200 sits safely under the limit.
TITLE_MAX = 200

#: The plugin pytest loads to report every test's outcome by node id. Written
#: to a fresh directory of its own, so nothing of this repo's `scripts/` is put
#: on agent-bureau's import path.
_PLUGIN = "_bureau_mirror_report"
_PLUGIN_SOURCE = '''
import json
import os

_RESULTS = {}
_TOTALS = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
_RED = ("failed", "error")
_CONFIG = []


def pytest_configure(config):
    _CONFIG.append(config)


def _key(nodeid):
    config = _CONFIG[0]
    path, sep, rest = nodeid.partition("::")
    try:
        full = os.path.join(str(config.rootpath), path)
        path = os.path.relpath(full, str(config.invocation_params.dir))
    except ValueError:
        pass
    return path.replace(os.sep, "/") + sep + rest


def pytest_collectreport(report):
    if report.failed:
        _RESULTS[_key(report.nodeid)] = "error"
        _TOTALS["errors"] += 1


def pytest_runtest_logreport(report):
    # pytest's own categories: a failure in the test body is `failed`, one in
    # setup or teardown is an `error`, a skip in any phase is `skipped`, and
    # an xfail or xpass is none of the four.
    key = _key(report.nodeid)
    counted = not hasattr(report, "wasxfail")
    if report.failed:
        outcome = "failed" if report.when == "call" else "error"
        if counted:
            _TOTALS["failed" if outcome == "failed" else "errors"] += 1
        if _RESULTS.get(key) not in _RED:
            _RESULTS[key] = outcome
        return
    if counted and report.skipped:
        _TOTALS["skipped"] += 1
    elif counted and report.when == "call":
        _TOTALS["passed"] += 1
    if _RESULTS.get(key) not in _RED:
        _RESULTS[key] = report.outcome


def pytest_sessionfinish(session, exitstatus):
    with open(os.environ["BUREAU_MIRROR_REPORT"], "w") as fh:
        json.dump({"exitstatus": int(exitstatus), "results": _RESULTS,
                   "totals": _TOTALS}, fh)
'''


# --------------------------------------------------------------------------- #
# 1. Which tests                                                              #
# --------------------------------------------------------------------------- #


def _names_the_pipeline(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(marker in text for marker in MARKERS)


def discover(root: Path) -> list[str]:
    """Every agent-bureau test that reads bureau-pipeline, root-relative and
    sorted. Discovered, never typed: the gate keeps no list."""
    root = Path(root)
    found = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if not any(fnmatch.fnmatch(name, p) for p in TEST_FILES):
                continue
            path = Path(directory) / name
            if _names_the_pipeline(path):
                found.append(path.relative_to(root).as_posix())
    return sorted(found)


def suite_of(test: str) -> str:
    """A test's suite: the first component of its root-relative path —
    `cloud` for `cloud/relay/test_x.py`, `.` for a test at agent-bureau's
    root (DRE-6670)."""
    parts = PurePosixPath(test).parts
    return parts[0] if len(parts) > 1 else ROOT_SUITE


def suites(tests: list[str]) -> dict[str, list[str]]:
    """The discovered tests grouped by suite, suites and files sorted. Read off
    the file list alone: nothing in agent-bureau's workflows is read, because
    a build run here cannot read agent-bureau, and a rule it cannot test is a
    guess. It is not agent-bureau's job layout and does not need to be — it
    keeps the relay out of the console's process, the one cross-talk seen."""
    grouped: dict[str, list[str]] = {}
    for test in sorted(tests):
        grouped.setdefault(suite_of(test), []).append(test)
    return {name: grouped[name] for name in sorted(grouped)}


def requirement_files(root: Path, tests: list[str]) -> list[str]:
    """agent-bureau's own dependencies: `requirements*.txt` at its root and in
    every directory from there down to each discovered test."""
    root = Path(root)
    directories = {Path(".")}
    for test in tests:
        parent = Path(test).parent
        while parent != Path("."):
            directories.add(parent)
            parent = parent.parent
    found = []
    for directory in directories:
        for path in (root / directory).glob(REQUIREMENTS):
            if path.is_file():
                found.append(path.relative_to(root).as_posix())
    return sorted(found)


# --------------------------------------------------------------------------- #
# 2. Which file a test mirrors                                                #
# --------------------------------------------------------------------------- #


def _string(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _div_chain(node) -> list:
    """`a / "config" / "x.json"` flattened to its operands, left to right."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _div_chain(node.left) + _div_chain(node.right)
    return [node]


def _joined_runs(parts: list) -> list[str]:
    """Every run of consecutive string literals in `parts`, joined by `/` —
    `Path(x, "config", "holds.json")` and `x / "config" / "holds.json"` both
    name `config/holds.json`, which neither literal does alone."""
    runs, current = [], []
    for part in parts + [None]:
        text = _string(part) if part is not None else None
        if text is not None:
            current.append(text.strip("/"))
            continue
        for start in range(len(current)):
            runs.append("/".join(current[start:]))
        current = []
    return runs


def _literals(tree) -> list[str]:
    found = []
    for node in ast.walk(tree):
        text = _string(node)
        if text is not None:
            found.append(text)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            found.extend(_joined_runs(_div_chain(node)))
        if isinstance(node, ast.Call):
            found.extend(_joined_runs(list(node.args)))
    return found


def mirrored_files(test_file: Path, candidate: Path) -> list[str]:
    """The bureau-pipeline files this test mirrors, read from the test itself:
    every literal path that names a FILE in the candidate checkout. An empty
    list is an honest answer — the caller says `not read from the test`."""
    candidate = Path(candidate).resolve()
    try:
        tree = ast.parse(Path(test_file).read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return []
    found = set()
    for text in _literals(tree):
        text = text.strip()
        if not text or "\n" in text or len(text) > 300 or text.startswith("/"):
            continue
        if text.startswith(PIPELINE_DIRNAME + "/"):
            text = text[len(PIPELINE_DIRNAME) + 1:]
        try:
            path = (candidate / text).resolve()
        except (OSError, ValueError):
            continue
        if candidate not in path.parents or not path.is_file():
            continue
        found.add(path.relative_to(candidate).as_posix())
    return sorted(found)


# --------------------------------------------------------------------------- #
# 3. The run                                                                  #
# --------------------------------------------------------------------------- #


class _Blocked(Exception):
    """The check could not run. Never a failing candidate."""


def pytest_starts(python: str, *, timeout: float) -> None:
    """Prove pytest starts before any test runs (DRE-6622): `<python> -m
    pytest --version`, from a directory of its own so agent-bureau's settings
    are not read. Raises `_Blocked` quoting the interpreter's last line —
    `No module named pytest` was the whole cause on 2026-10-10, and it read
    as `pytest wrote no report`."""
    with tempfile.TemporaryDirectory(prefix="mirror-check-") as tmp:
        try:
            proc = subprocess.run([python, "-m", "pytest", "--version"],
                                  cwd=tmp, timeout=timeout,
                                  capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            raise _Blocked(f"pytest does not start in the check's environment: "
                           f"`pytest --version` ran past {int(timeout)}s")
        except OSError as exc:
            raise _Blocked(f"pytest does not start in the check's environment: "
                           f"{exc}")
    if proc.returncode != 0:
        lines = (proc.stderr + proc.stdout).strip().splitlines()
        last = lines[-1].strip() if lines else f"exit {proc.returncode}"
        raise _Blocked(f"pytest does not start in the check's environment: "
                       f"{last[:400]}")


def run_pytest(root: Path, targets: list[str], *, python: str,
               timeout: float) -> dict:
    """pytest from agent-bureau's root over `targets`, with the checkout at
    `<root>/.bureau-pipeline` also named by `BUREAU_PIPELINE_DIR`. Returns
    `{"results": {node id: outcome}, "totals": {passed, failed, errors,
    skipped}}`; raises `_Blocked` when there is no honest answer."""
    root = Path(root)
    with tempfile.TemporaryDirectory(prefix="mirror-check-") as tmp:
        plugin_dir = Path(tmp) / "plugin"
        plugin_dir.mkdir()
        (plugin_dir / f"{_PLUGIN}.py").write_text(_PLUGIN_SOURCE)
        report = Path(tmp) / "report.json"
        env = dict(os.environ)
        env[PIPELINE_ENV] = str((root / PIPELINE_DIRNAME).resolve())
        env["BUREAU_MIRROR_REPORT"] = str(report)
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(plugin_dir), env.get("PYTHONPATH", "")) if p)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        command = [python, "-m", "pytest", "-p", _PLUGIN, "-p", "no:cacheprovider",
                   "--continue-on-collection-errors", "-q", NO_COVERAGE,
                   *targets]
        try:
            proc = subprocess.run(command, cwd=root, env=env, timeout=timeout,
                                  capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            raise _Blocked(f"pytest ran past its time limit of {int(timeout)}s")
        except OSError as exc:
            raise _Blocked(f"pytest could not start ({exc})")
        tail = " ".join((proc.stdout + proc.stderr).split()[-40:])
        print(proc.stdout[-6000:], end="")
        print(proc.stderr[-3000:], end="", file=sys.stderr)
        if proc.returncode not in (0, 1):
            raise _Blocked(f"pytest could not run the tests (exit "
                           f"{proc.returncode}): {tail[:400]}")
        try:
            data = json.loads(report.read_text())
        except (OSError, ValueError):
            raise _Blocked(f"pytest wrote no report (exit {proc.returncode}): "
                           f"{tail[:400]}")
        results = data.get("results") or {}
        if proc.returncode == 1 and not _red(results):
            raise _Blocked("pytest reported a failure it did not attribute "
                           "to any test")
        totals = data.get("totals") or {}
        return {"results": results,
                "totals": {k: int(totals.get(k) or 0) for k in TOTALS}}


def _red(results: dict) -> list[str]:
    return sorted(k for k, v in results.items() if v in RED)


def _file_of(nodeid: str) -> str:
    return nodeid.partition("::")[0]


def _swap(a: Path, b: Path) -> None:
    """Exchange two directories' places on disk."""
    spare = a.with_name(a.name + ".mirror-swap")
    os.rename(a, spare)
    os.rename(b, a)
    os.rename(spare, b)


def _entry(root: Path, nodeid: str) -> dict:
    mirrors = mirrored_files(root / _file_of(nodeid), root / PIPELINE_DIRNAME)
    return {"test": nodeid, "mirrors": mirrors}


def check(root: Path, stable: Path, *, candidate: str, stable_sha: str,
          agent_bureau_sha: str, python: str = sys.executable,
          budget_seconds: float = BUDGET_SECONDS,
          setup: dict | None = None) -> dict:
    """The whole check. Returns the result `promote_channel.py` reads.

    One pytest process per suite (DRE-6670), inside the one budget. Every
    suite with a red test on the candidate then runs again, whole — same
    files, same order, same flags — with `stable` in the candidate's place,
    and each red test is judged by its outcome in the two identical runs. A
    suite that cannot run is `blocked`, and blocks the check; the others still
    run and are still reported, the blocked ones first."""
    started = time.monotonic()
    root, stable = Path(root), Path(stable)
    result = {
        "status": MIRROR_BLOCKED, "why": "", "candidate": candidate,
        "stable": stable_sha, "agent_bureau_sha": agent_bureau_sha,
        "tests": 0, "failing": [], "already_red": [], "suites": [],
        "seconds": 0,
    }

    def left(what: str) -> float:
        seconds = budget_seconds - (time.monotonic() - started)
        if seconds <= 0:
            raise _Blocked(f"the check's time limit was spent before {what}")
        return seconds

    try:
        for name, outcome in (setup or {}).items():
            if outcome != "success":
                raise _Blocked(f"the {name} step concluded {outcome or 'nothing'}")
        if not root.is_dir():
            raise _Blocked("there is no agent-bureau checkout")
        if not (root / PIPELINE_DIRNAME).is_dir():
            raise _Blocked(f"there is no candidate checkout at "
                           f"agent-bureau/{PIPELINE_DIRNAME}")
        discovered = discover(root)
        result["tests"] = len(discovered)
        if not discovered:
            raise _Blocked(f"no test in the agent-bureau checkout names "
                           f"{PIPELINE_ENV} or {PIPELINE_DIRNAME} — nothing "
                           f"was checked, so nothing is proven")
        pytest_starts(python, timeout=min(120.0, budget_seconds))

        entries = result["suites"]
        outcomes: dict[str, dict] = {}
        for name, files in suites(discovered).items():
            entry = {"name": name, "files": files, "status": MIRROR_PASSED,
                     "re_read": False,
                     "totals": {"candidate": None, "stable": None}}
            entries.append(entry)
            try:
                run = run_pytest(root, files, python=python,
                                 timeout=left("this suite ran"))
            except _Blocked as exc:
                entry.update(status=MIRROR_BLOCKED, why=str(exc))
                continue
            entry["totals"]["candidate"] = run["totals"]
            outcomes[name] = {"candidate": run["results"], "stable": {}}

        again = [e for e in entries if e["status"] != MIRROR_BLOCKED
                 and _red(outcomes[e["name"]]["candidate"])]
        if again:
            if not stable.is_dir():
                raise _Blocked("there is no stable checkout to re-read the "
                               "failures against, so none of them can be laid "
                               "at the candidate")
            here = root / PIPELINE_DIRNAME
            _swap(here, stable)
            try:
                for entry in again:
                    try:
                        run = run_pytest(
                            root, entry["files"], python=python,
                            timeout=left("this suite's re-read against stable"))
                    except _Blocked as exc:
                        entry.update(status=MIRROR_BLOCKED,
                                     why=f"the re-read against stable: {exc}")
                        continue
                    entry["re_read"] = True
                    entry["totals"]["stable"] = run["totals"]
                    outcomes[entry["name"]]["stable"] = run["results"]
            finally:
                _swap(here, stable)

        failing, already_red = [], []
        for entry in entries:
            if entry["status"] == MIRROR_BLOCKED:
                continue
            seen = outcomes[entry["name"]]
            for nodeid in _red(seen["candidate"]):
                if seen["stable"].get(nodeid) in RED:
                    already_red.append(nodeid)
                else:
                    failing.append(nodeid)
                    entry["status"] = MIRROR_FAILED
        entries.sort(key=lambda e: e["status"] != MIRROR_BLOCKED)
        result["already_red"] = [_entry(root, n) for n in sorted(already_red)]
        blocked = [e for e in entries if e["status"] == MIRROR_BLOCKED]
        if blocked:
            raise _Blocked("; ".join(f"suite {e['name']}: {e['why']}"
                                     for e in blocked))
        result["failing"] = [_entry(root, n) for n in sorted(failing)]
        result["status"] = MIRROR_FAILED if result["failing"] else MIRROR_PASSED
    except _Blocked as exc:
        result["status"] = MIRROR_BLOCKED
        result["why"] = str(exc)
        result["failing"] = []
    result["seconds"] = int(time.monotonic() - started)
    return result


def summary_line(result: dict) -> str:
    """The run summary's duration line — the harness's `⏱` shape, so the real
    budget is measured on live runs rather than assumed."""
    minutes = int(result.get("seconds") or 0) // 60
    sha = (result.get("agent_bureau_sha") or "unknown")[:7]
    return f"⏱ mirror check: {minutes}m ({result.get('tests', 0)} tests, agent-bureau {sha})"


def _counts(totals: dict) -> str:
    return ", ".join(f"{int(totals.get(k) or 0)} {k}" for k in TOTALS)


def suite_line(suite: dict) -> str:
    """Both runs' totals for one suite, side by side (DRE-6670) — the shape
    the card, the run summary and the step's own output all carry:
    `suite <name>: candidate <counts> · stable <counts>`."""
    totals = suite.get("totals") or {}
    mine, theirs = totals.get("candidate"), totals.get("stable")
    first = f"candidate {_counts(mine)}" if mine else "candidate not run"
    if theirs:
        second = f"stable {_counts(theirs)}"
    elif suite.get("status") == MIRROR_BLOCKED:
        second = "stable not re-read: the suite is blocked"
    else:
        second = NOT_RE_READ
    return f"suite {suite.get('name')}: {first} · {second}"


def suite_lines(result: dict) -> list[str]:
    """One line per suite, blocked suites first with their reason, then the
    totals of every suite whose candidate run gave any."""
    lines = []
    for suite in result.get("suites") or []:
        if suite.get("status") == MIRROR_BLOCKED:
            lines.append(f"suite {suite.get('name')}: blocked — {suite.get('why')}")
        if (suite.get("totals") or {}).get("candidate"):
            lines.append(suite_line(suite))
    return lines


def describe(entries: list[dict]) -> str:
    """`<test> (mirrors <files>)`, one per entry, for a receipt line."""
    return "; ".join(
        f"{e['test']} (mirrors {', '.join(e.get('mirrors') or []) or NOT_READ})"
        for e in entries)


# --------------------------------------------------------------------------- #
# 4. The card                                                                 #
# --------------------------------------------------------------------------- #


def card_title(result: dict) -> str:
    """Deterministic, and the key the open card is found again by: the sorted
    mirrored files, or the failing tests' own names when none were read. The
    sha is never in it — the next candidate's refusal must find this card."""
    failing = result.get("failing") or []
    names = sorted({m for e in failing for m in (e.get("mirrors") or [])})
    if not names:
        names = sorted({_file_of(e["test"]) for e in failing})
    title = f"{CARD_REPO}: mirrors of {', '.join(names)} must follow bureau-pipeline"
    if len(title) <= TITLE_MAX:
        return title
    # Too long for Linear (DRE-6657): the leading files that fit, then how
    # many more. Still deterministic, and the body names every one.
    lead = f"{CARD_REPO}: mirrors must follow bureau-pipeline: "
    for shown in range(len(names) - 1, 0, -1):
        title = f"{lead}{', '.join(names[:shown])} and {len(names) - shown} more"
        if len(title) <= TITLE_MAX:
            return title
    return f"{lead}{len(names)} files"


def card_labels() -> list[str]:
    return list(CARD_LABELS)


def card_body(result: dict, run_url: str) -> str:
    """The card, in plain English, with everything the fix needs."""
    rows = [
        f"- `{e['test']}` — mirrors "
        + (", ".join(f"`{m}`" for m in e.get("mirrors") or []) or f"_{NOT_READ}_")
        for e in result.get("failing") or []
    ]
    red = [f"- `{e['test']}`" for e in result.get("already_red") or []]
    lines = [
        f"**Repo:** {CARD_REPO}",
        "",
        "## What happened",
        "",
        "bureau-pipeline's channel promotion ran agent-bureau's mirror tests "
        "against the next `stable` candidate, and these tests pass against "
        "bureau-pipeline at `stable` and fail against the candidate. So "
        "`stable` did not move: agent-bureau's mirror has to follow first.",
        "",
        *rows,
    ]
    per_suite = suite_lines(result)
    if per_suite:
        lines += ["", "Both runs' totals, per suite — the candidate's, then the "
                      "identical run with `stable` in its place:", "",
                  *(f"- {line}" for line in per_suite)]
    lines += [
        "",
        f"- bureau-pipeline candidate: `{result.get('candidate') or 'unknown'}`",
        f"- agent-bureau tested at: `{result.get('agent_bureau_sha') or 'unknown'}`",
        f"- `stable` stands at: `{result.get('stable') or 'unknown'}`",
        f"- the promote-channel run: {run_url or 'unknown'}",
    ]
    if red:
        lines += ["", "Already red on `stable` as well, so not this change's "
                      "doing and not part of this card:", "", *red]
    lines += [
        "",
        "## What to do",
        "",
        "- Update each mirror above to the candidate's copy of the file it "
        "mirrors.",
        "- Once that merges, the next harness run on bureau-pipeline's `main`, "
        "or an ordinary by-hand promote, re-evaluates and moves `stable`.",
        "- Until agent-bureau's drift tests accept a mirror that matches "
        "bureau-pipeline's `main` as well as `stable`, the fix pull request "
        "fails its own drift test. The operator's `force` on a by-hand promote "
        "is the way past that deadlock.",
        "",
        "## Acceptance criteria",
        "",
        "- [ ] Every test listed above passes against the candidate named here.",
        "- [ ] bureau-pipeline's next promote-channel run is not refused on "
        "these mirrors.",
    ]
    return "\n".join(lines) + "\n"


def comment_note(result: dict, run_url: str) -> str:
    """What the open card is told when a later candidate is refused on the
    same mirrors. Names the candidate, which is what keeps it from repeating."""
    return (
        f"🔁 Still refused on these mirrors: candidate "
        f"`{result.get('candidate') or 'unknown'}`, agent-bureau tested at "
        f"`{result.get('agent_bureau_sha') or 'unknown'}`, `stable` at "
        f"`{result.get('stable') or 'unknown'}`. Run: {run_url or 'unknown'}"
    )


class _Ops:
    """The Linear seam, in one object so a test can hand a fake in."""

    def find_open(self, title):
        import linear_ops

        return linear_ops.find_open(title)

    def comment_bodies(self, identifier):
        import linear_ops

        return linear_ops.comment_bodies(identifier, whole_thread=True)

    def oneoff(self, title, body, labels):
        import linear_ops

        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write(body)
        try:
            flags = [tok for label in labels for tok in ("--label", label)]
            return linear_ops.cmd_oneoff(title, fh.name, *flags)
        finally:
            os.unlink(fh.name)

    def cmd_comment(self, identifier, body):
        import linear_ops

        return linear_ops.cmd_comment(identifier, body)


def file_card(result: dict, run_url: str, ops=None) -> dict:
    """File the refusal's card, or comment on the open one. NEVER raises:
    Linear never changes the verdict. Returns `{card, owed, note}`."""
    ops = ops or _Ops()
    title = card_title(result)
    candidate = result.get("candidate") or ""
    try:
        existing = ops.find_open(title)
        if existing:
            said = any(candidate and candidate in (body or "")
                       for body in ops.comment_bodies(existing) or [])
            if not said:
                ops.cmd_comment(existing, comment_note(result, run_url))
            return {"card": existing, "owed": False, "note": ""}
        issue = ops.oneoff(title, card_body(result, run_url), card_labels())
        return {"card": (issue or {}).get("identifier") or "filed",
                "owed": False, "note": ""}
    except Exception as exc:  # noqa: BLE001 — Linear is never worth the verdict
        note = " ".join(str(exc).split())[:300]
        print(f"mirror card: NOT filed ({note})", file=sys.stderr)
        return {"card": "", "owed": True, "note": note}


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def _output(lines: dict) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with open(out, "a") as fh:
        for key, value in lines.items():
            if "\n" in value:
                fh.write(f"{key}<<MIRROR_EOF\n{value}\nMIRROR_EOF\n")
            else:
                fh.write(f"{key}={value}\n")


def _git_head(root: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True,
                              timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _cmd_discover(args) -> int:
    root = Path(args.agent_bureau)
    tests = discover(root) if root.is_dir() else []
    requirements = requirement_files(root, tests) if tests else []
    prefix = args.prefix.rstrip("/") + "/" if args.prefix else ""
    version_file = root / ".python-version"
    version = "3.12"
    if version_file.is_file():
        version = version_file.read_text().strip().splitlines()[0].strip() or version
    print(f"mirror check: {len(tests)} tests read bureau-pipeline; "
          f"{len(requirements)} requirement file(s), and the check's own "
          f"{TOOLS_MANIFEST}")
    # agent-bureau's files first, under the checkout's prefix; then this
    # repository's pinned test tools, at the workspace root (DRE-6622).
    manifests = [prefix + r for r in requirements] + [TOOLS_MANIFEST]
    _output({
        "count": str(len(tests)),
        "requirements": "\n".join(manifests),
        "python_version": version,
    })
    return 0


def _cmd_run(args) -> int:
    setup = {}
    for pair in args.setup or []:
        name, _, outcome = pair.partition("=")
        setup[name.strip()] = outcome.strip()
    root = Path(args.agent_bureau)
    result = check(
        root, Path(args.stable), candidate=args.candidate,
        stable_sha=args.stable_sha,
        agent_bureau_sha=args.agent_bureau_sha or _git_head(root),
        python=args.python, budget_seconds=args.budget_seconds, setup=setup,
    )
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    line = summary_line(result)
    per_suite = suite_lines(result)
    print(f"mirror check: {result['status']} — {line}")
    for each in per_suite:
        print(f"mirror check: {each}")
    if result["why"]:
        print(f"mirror check: {result['why']}")
    for entry in result["failing"]:
        print(f"mirror check: fails on the candidate: {describe([entry])}")
    for entry in result["already_red"]:
        print(f"mirror check: already red on stable: {describe([entry])}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(line + "\n\n")
            if per_suite:
                fh.write("".join(f"- {each}\n" for each in per_suite) + "\n")
    return 0


def _cmd_card(args, ops=None) -> int:
    try:
        result = json.loads(Path(args.result).read_text())
    except (OSError, ValueError) as exc:
        print(f"mirror card: no result to file from ({exc})")
        return 0
    if result.get("status") != MIRROR_FAILED or not result.get("failing"):
        print("mirror card: the check did not fail — nothing to file")
        return 0
    got = file_card(result, args.run_url, ops=ops)
    if got["owed"]:
        print(f"::warning title=Mirror card owed::the mirror check refused "
              f"{(result.get('candidate') or '')[:7]} and its agent-bureau card "
              f"could not be filed ({got['note']}) — card owed; the refusal stands")
        _output({"card": "card owed"})
    else:
        print(f"mirror card: {got['card']} — {card_title(result)}")
        _output({"card": got["card"]})
    return 0


def main(argv: list[str] | None = None, ops=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    discover_p = sub.add_parser("discover")
    discover_p.add_argument("--agent-bureau", required=True)
    discover_p.add_argument("--prefix", default="",
                            help="the agent-bureau checkout's path from the "
                                 "workspace root, put in front of each "
                                 "requirement file for the install step")

    run_p = sub.add_parser("run")
    run_p.add_argument("--agent-bureau", required=True)
    run_p.add_argument("--stable", required=True,
                       help="the checkout of the current channel head")
    run_p.add_argument("--candidate", required=True)
    run_p.add_argument("--stable-sha", default="")
    run_p.add_argument("--agent-bureau-sha", default="")
    run_p.add_argument("--out", required=True)
    run_p.add_argument("--python", default=sys.executable)
    run_p.add_argument("--budget-seconds", type=float, default=BUDGET_SECONDS)
    run_p.add_argument("--setup", action="append", metavar="NAME=OUTCOME",
                       help="a setup step's outcome; anything but success "
                            "blocks the check")

    card_p = sub.add_parser("card")
    card_p.add_argument("--result", required=True)
    card_p.add_argument("--run-url", default="")

    args = parser.parse_args(argv)
    if args.command == "discover":
        return _cmd_discover(args)
    if args.command == "run":
        return _cmd_run(args)
    return _cmd_card(args, ops=ops)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
