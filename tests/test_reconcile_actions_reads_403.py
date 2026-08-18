"""Every Actions-API READ in the sweep runs under a token that can read it.

DRE-1254 (2026-06-12) found `gh workflow run` executing under the minted App
token, which lacks Actions:write: GitHub answers "HTTP 403: Resource not
accessible by integration", the silent gh() helper discards it, and the sweep
reports success while nothing ran. It fixed ONE call site — the dispatch.

The App token cannot read the Actions API either. Reproduced live 2026-08-17
against DeltaSolv/deltasolv with a real minted bot token:

    gh run list --repo DeltaSolv/deltasolv --workflow qa-review.yml \
       --event workflow_dispatch --limit 50 --json status
    -> HTTP 403: Resource not accessible by integration

Both the workflow-scoped and the repo-wide runs endpoints 403, so it is the
token, not the query shape. SIX call sites read that API, and one root cause
produces three DIFFERENT wrong answers, none of them visible:

  * ``_review_dispatch_in_flight``  -> json.loads("") raises, the handler
    answers "in flight" — permanently. Dependabot PRs #211/#212/#213 waited a
    full day behind a green sweep logging "waiting, never double-dispatching".
  * ``unstick_conflicts``, ``fix_approved_but_red``, ``retry_dead_fix_runs``,
    ``restart_answered_blockers`` -> ``json.loads(gh(...) or "[]")`` makes the
    403 an empty list, so the busy-guard reads "nothing running" and dispatches
    ANYWAY. This is FAIL-OPEN, and it is the very backoff that exists because
    of the 2026-06-28 App-quota burn.
  * ``agent_run_alive`` -> the status read is "" which is never "completed", so
    the run looks alive forever and the dead-run retry never fires.

Unit tests could not catch any of it: all 2140 of them stub subprocess.run and
return rc=0, so they exercise logic while this is a PERMISSION FACT about a
real token. The guard therefore has to be structural (does the call use the
right helper?) plus adversarial (what does each guard answer when the read
403s?) — the house lesson that a fix must audit ALL call sites with an AST
sweep, not one sentinel.

DESIRED behavior (these tests express it; they FAIL on the unfixed code):

  1. No Actions-API read goes through the silent gh() helper — they all use
     gh_actions_read, which swaps in GH_DISPATCH_TOKEN and records failures.
  2. gh_actions_read runs under GH_DISPATCH_TOKEN and returns None (recorded
     in _read_failures) when the read fails.
  3. A 403 on a busy-guard read must NOT be read as "nothing running" — the
     guard fails CLOSED and does not dispatch.

Run: cd bureau-pipeline && python3 -m pytest tests/test_reconcile_actions_reads_403.py -v
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

os.environ.setdefault("REPO", "DeltaSolv/deltasolv")
os.environ.setdefault("REPO_SLUG", "deltasolv")

import reconcile  # noqa: E402

_SOURCE = (_SCRIPTS / "reconcile.py").read_text()
_REAL_403 = (
    "HTTP 403: Resource not accessible by integration "
    "(https://api.github.com/repos/DeltaSolv/deltasolv/actions/"
    "workflows/qa-review.yml)"
)

# The six call sites found on 2026-08-17, by the function that owns them.
_KNOWN_ACTIONS_READERS = {
    "agent_run_alive",
    "unstick_conflicts",
    "fix_approved_but_red",
    "retry_dead_fix_runs",
    "restart_answered_blockers",
    "_review_dispatch_in_flight",
}


def _literal(node) -> str | None:
    """The string value of a Constant/JoinedStr arg, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value for v in node.values
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
    return None


def _is_actions_read(call: ast.Call) -> bool:
    """Does this call read the GitHub Actions API?

    Two shapes in this file: ("run", "list", ...) and ("api", ".../actions/...").
    """
    args = [_literal(a) for a in call.args]
    if not args:
        return False
    if args[0] == "run" and len(args) > 1 and args[1] == "list":
        return True
    return bool(args[0] == "api" and len(args) > 1 and args[1] and "/actions/" in args[1])


def _silent_actions_reads() -> list[tuple[str, int]]:
    """[(owner function, line)] for Actions reads still on the silent gh()."""
    tree = ast.parse(_SOURCE)
    funcs = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    def owner_of(lineno: int) -> str:
        best = None
        for f in funcs:
            if f.lineno <= lineno <= (f.end_lineno or f.lineno):
                if best is None or f.lineno > best.lineno:
                    best = f
        return best.name if best else "<module>"

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "gh"):
            continue
        if _is_actions_read(node):
            found.append((owner_of(node.lineno), node.lineno))
    return found


def test_no_actions_read_uses_the_silent_gh_helper():
    """THE class guard: one AST sweep over every Actions read in the sweep.

    A single fixed call site is whack-a-mole — DRE-1254 fixed the dispatch and
    left five reads behind, and they stayed broken for two months.
    """
    offenders = _silent_actions_reads()
    assert not offenders, (
        "these Actions-API reads still use the silent gh() helper, which runs "
        "under the App token (403 on this API) and discards the error:\n"
        + "\n".join(f"    {fn}()  line {ln}" for fn, ln in offenders)
        + "\nUse gh_actions_read: it swaps in GH_DISPATCH_TOKEN and records "
        "unreadable listings so the sweep goes red instead of silently "
        "answering from fabricated data."
    )


def test_the_ast_guard_can_actually_see_a_violation():
    """Guard the guard: a detector that matches nothing would pass forever."""
    tree = ast.parse(
        'gh("run", "list", "--repo", REPO, "--limit", "10", "--json", "status")\n'
        'gh("api", f"repos/{REPO}/actions/runs/{rid}", "--jq", ".status")\n'
        'gh("pr", "list", "--repo", REPO)\n'
    )
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert [_is_actions_read(c) for c in calls] == [True, True, False]


def test_every_known_actions_reader_is_still_covered():
    """If a reader is renamed or removed, this test says so rather than
    quietly shrinking the audited surface."""
    missing = {fn for fn in _KNOWN_ACTIONS_READERS if f"def {fn}(" not in _SOURCE}
    assert not missing, (
        f"these Actions-reading functions no longer exist: {sorted(missing)} — "
        "update _KNOWN_ACTIONS_READERS deliberately, never by deletion"
    )


def _run_stub(returncode: int, stdout: str, calls: list):
    def fake_run(argv, **kwargs):
        calls.append({"argv": list(argv), "env": kwargs.get("env")})
        return SimpleNamespace(
            returncode=returncode,
            stdout=stdout,
            stderr="" if returncode == 0 else _REAL_403,
        )

    return fake_run


def test_gh_actions_read_uses_the_dispatch_token():
    """The read must run under github.token (actions:write ⊃ read), not the App
    token — the same swap gh_dispatch has had since DRE-1254."""
    calls: list = []
    with patch.dict(
        os.environ, {"GH_DISPATCH_TOKEN": "ghs_dispatch", "GH_TOKEN": "ghs_app"}
    ):
        with patch.object(
            reconcile.subprocess, "run", side_effect=_run_stub(0, "[]", calls)
        ):
            reconcile.gh_actions_read("run", "list", "--repo", "x")
    assert calls and calls[0]["env"] is not None
    assert calls[0]["env"].get("GH_TOKEN") == "ghs_dispatch"


def test_gh_actions_read_returns_none_and_records_on_403():
    """Unreadable is None — never "" and never [] — so no caller can mistake it
    for a real, empty answer. The failure is recorded so the sweep exits 1."""
    before = list(reconcile._read_failures)
    try:
        with patch.dict(os.environ, {"GH_DISPATCH_TOKEN": "ghs_dispatch"}), \
                patch.object(
                    reconcile.subprocess, "run", side_effect=_run_stub(1, "", [])
                ):
            assert reconcile.gh_actions_read("run", "list", "--repo", "x") is None
        new = reconcile._read_failures[len(before):]
        assert new and any("403" in f or "rc=1" in f for f in new), (
            f"the 403 must be recorded, got {new}"
        )
    finally:
        del reconcile._read_failures[len(before):]


@pytest.mark.parametrize(
    "func_name",
    ["unstick_conflicts", "fix_approved_but_red", "retry_dead_fix_runs",
     "restart_answered_blockers"],
)
def test_busy_guard_fails_closed_when_the_read_403s(func_name):
    """A 403 on the busy read must NOT become "nothing is running".

    Today ``json.loads(gh(...) or "[]")`` turns the 403 into [], the guard sees
    no busy run and dispatches anyway — fail-OPEN, in the exact backoff that
    exists because of the 2026-06-28 quota burn. The safe answer is "assume
    busy": defer one sweep, never burst.
    """
    calls: list = []

    def fake_run(argv, **kwargs):
        joined = " ".join(argv)
        calls.append({"argv": list(argv), "env": kwargs.get("env")})
        if argv[1] == "run" and argv[2] == "list":
            return SimpleNamespace(returncode=1, stdout="", stderr=_REAL_403)
        if argv[1] == "pr" and argv[2] == "list":
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    '[{"number": 48, '
                    '"headRefName": "agent/DRE-1254-uncertainty-disclosure", '
                    '"headRefOid": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef", '
                    '"mergeStateStatus": "DIRTY", "comments": []}]'
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    before = list(reconcile._read_failures)
    try:
        with patch.dict(os.environ, {"GH_DISPATCH_TOKEN": "ghs_dispatch"}), \
                patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
                patch.object(reconcile, "card_parked_for_human", return_value=False):
            try:
                getattr(reconcile, func_name)()
            except Exception:
                pass  # unrelated downstream shape; the dispatch assertion is the point
        busy_reads = [
            c for c in calls
            if len(c["argv"]) > 2 and c["argv"][1] == "run" and c["argv"][2] == "list"
        ]
        assert busy_reads, (
            f"{func_name} never reached its busy read under this stub, so the "
            "dispatch assertion below would pass vacuously — fix the stub shape"
        )
        dispatched = [
            c for c in calls
            if len(c["argv"]) > 2 and c["argv"][1] == "workflow" and c["argv"][2] == "run"
        ]
        assert not dispatched, (
            f"{func_name} dispatched while its busy read was unreadable (403). "
            "An unreadable busy listing must fail CLOSED — assume a run is in "
            "flight and defer — not be read as an empty list."
        )
    finally:
        del reconcile._read_failures[len(before):]
