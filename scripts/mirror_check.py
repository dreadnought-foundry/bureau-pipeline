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
            which pins the test tools the check runs (DRE-6622).
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
  3. pytest from agent-bureau's root over exactly those files, once it is
     proven to start, and with `--no-cov`: agent-bureau's coverage floor
     cannot be met by its mirror tests alone (DRE-6622).
  4. Every failure is re-read against `stable`: the failing files run once more
     with `stable` in the candidate's place. A test red on both is agent-bureau's
     own red, named `already red on stable`, and refuses nothing. Only a test
     that does not fail on `stable` and fails on the candidate refuses.
  5. The bureau-pipeline file a failing test mirrors is read off the test's own
     string literals — a literal, or a chain of them joined by `/` or a call,
     that names a file in the candidate checkout. None found reads `mirrors:
     not read from the test`; it is never guessed.

A check that COULD NOT RUN — a token that did not mint, a checkout or an
install that failed, pytest that would not start, a run past its time limit —
is `blocked`, never `failed`. That is the `harness-blocked-by-sandbox`
distinction (DRE-3076): nothing was proven either way, and a reader sent to the
candidate's diff would be looking at code nobody judged.
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
from pathlib import Path

#: The check's three answers. `promote_channel.with_mirror` reads them.
MIRROR_PASSED = "passed"
MIRROR_FAILED = "failed"
MIRROR_BLOCKED = "blocked"

#: The two ways an agent-bureau test finds bureau-pipeline. A test whose text
#: names either is a mirror test; the candidate is put behind both.
PIPELINE_ENV = "BUREAU_PIPELINE_DIR"
PIPELINE_DIRNAME = ".bureau-pipeline"
MARKERS = (PIPELINE_ENV, PIPELINE_DIRNAME)

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
TOOLS_MANIFEST = "requirements-dev.txt"

#: Appended to both pytest runs (DRE-6622). agent-bureau's pytest settings
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

#: The plugin pytest loads to report every test's outcome by node id. Written
#: to a fresh directory of its own, so nothing of this repo's `scripts/` is put
#: on agent-bureau's import path.
_PLUGIN = "_bureau_mirror_report"
_PLUGIN_SOURCE = '''
import json
import os

_RESULTS = {}
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
        _RESULTS[_key(report.nodeid)] = "failed"


def pytest_runtest_logreport(report):
    key = _key(report.nodeid)
    if report.failed:
        _RESULTS[key] = "failed"
    elif _RESULTS.get(key) != "failed":
        _RESULTS[key] = report.outcome


def pytest_sessionfinish(session, exitstatus):
    with open(os.environ["BUREAU_MIRROR_REPORT"], "w") as fh:
        json.dump({"exitstatus": int(exitstatus), "results": _RESULTS}, fh)
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
    `{node id: outcome}`; raises `_Blocked` when there is no honest answer."""
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
        failed = [k for k, v in results.items() if v == "failed"]
        if proc.returncode == 1 and not failed:
            raise _Blocked("pytest reported a failure it did not attribute "
                           "to any test")
        return results


def _failed(results: dict) -> list[str]:
    return sorted(k for k, v in results.items() if v == "failed")


def _file_of(nodeid: str) -> str:
    return nodeid.partition("::")[0]


def _swap(a: Path, b: Path) -> None:
    """Exchange two directories' places on disk."""
    spare = a.with_name(a.name + ".mirror-swap")
    os.rename(a, spare)
    os.rename(b, a)
    os.rename(spare, b)


def _re_read_on_stable(root: Path, stable: Path, failing: list[str], *,
                       discovered: list[str], python: str,
                       timeout: float) -> dict:
    """Run the failing tests' files again with `stable` where the candidate
    was. Whole files, not node ids: a test parametrized over the pipeline's
    own data may not even exist on `stable`, and that is not a red there."""
    if not stable.is_dir():
        raise _Blocked("there is no stable checkout to re-read the failures "
                       "against, so none of them can be laid at the candidate")
    files = sorted({_file_of(n) for n in failing})
    if not all(f.endswith(".py") for f in files):
        files = discovered
    candidate = root / PIPELINE_DIRNAME
    _swap(candidate, stable)
    try:
        return run_pytest(root, files, python=python, timeout=timeout)
    finally:
        _swap(candidate, stable)


def _entry(root: Path, nodeid: str) -> dict:
    mirrors = mirrored_files(root / _file_of(nodeid), root / PIPELINE_DIRNAME)
    return {"test": nodeid, "mirrors": mirrors}


def check(root: Path, stable: Path, *, candidate: str, stable_sha: str,
          agent_bureau_sha: str, python: str = sys.executable,
          budget_seconds: float = BUDGET_SECONDS,
          setup: dict | None = None) -> dict:
    """The whole check. Returns the result `promote_channel.py` reads."""
    started = time.monotonic()
    root, stable = Path(root), Path(stable)
    result = {
        "status": MIRROR_BLOCKED, "why": "", "candidate": candidate,
        "stable": stable_sha, "agent_bureau_sha": agent_bureau_sha,
        "tests": 0, "failing": [], "already_red": [], "seconds": 0,
    }
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
        results = run_pytest(root, discovered, python=python,
                             timeout=budget_seconds)
        failing = _failed(results)
        red_on_stable: set[str] = set()
        if failing:
            left = budget_seconds - (time.monotonic() - started)
            if left <= 0:
                raise _Blocked("the check ran past its time limit before the "
                               "re-read against stable")
            again = _re_read_on_stable(root, stable, failing,
                                       discovered=discovered, python=python,
                                       timeout=left)
            red_on_stable = {n for n in failing if again.get(n) == "failed"}
        result["failing"] = [_entry(root, n) for n in failing
                             if n not in red_on_stable]
        result["already_red"] = [_entry(root, n) for n in failing
                                 if n in red_on_stable]
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
    return f"{CARD_REPO}: mirrors of {', '.join(names)} must follow bureau-pipeline"


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
    print(f"mirror check: {result['status']} — {line}")
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
