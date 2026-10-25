"""RED-first tests for DRE-7908 — the console-change walks are skipped on a run
whose changes cannot affect them, and say why.

On 2026-10-24 PR #1119 merged one file, `docs/release-deferral-proof-2026-10.md`.
Its `Pipeline Tests` run on main, 40133214810, ran from 2:12 PM to 3:13 PM PT:
the unit parts were done by about 2:39 PM PT and the walks ran 46m09s, 60m07s
and 49m58s. The channel promotion read the run at 2:28 PM PT (run
40134364777) and refused `unit-suite-not-finished`, although nothing in that
merge could have turned a walk red.

`scripts/walk_reach.py` holds the rule. A changed path can skip only when it
is under `docs/` or `architecture/` and no walk, no
`tests/console_change_walk.py` and no file in any walk's inner run names it —
its repo path or its file name anywhere in that file's text. Every other path
runs the walks. This file is the one `NOT_SCANNED` names: it spells out the
headline document as a case, and it is in the change walk's inner run because
it spells out `config/models.yaml` and `agents.yaml` too.

Run: cd bureau-pipeline && python3 -m pytest tests/test_walk_reach.py -v
"""
from __future__ import annotations

import functools
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

import unit_test_parts as parts  # noqa: E402
import walk_reach  # noqa: E402

SCRIPT = ROOT / "scripts" / "walk_reach.py"
HELPER = "tests/console_change_walk.py"

#: PR #1119's one file, the merge this card is for.
HEADLINE = "docs/release-deferral-proof-2026-10.md"
DECISION = "architecture/decisions/x.md"

#: Named by the walks' inner runs on `main` at `dda9371` — each must run.
NAMED_ON_MAIN = ("docs/model-choice-levers.md", "docs/escalation-choices.md")

#: A walk planted in a throwaway tree: its inner run is the real helper's
#: `inner_files` over `tests/test_model_*.py`, as the real walks build theirs.
PLANTED_WALK = (
    "import sys\n"
    f"sys.path.insert(0, {str(ROOT / 'tests')!r})\n"
    "import console_change_walk as walk\n"
    "def suite_files(tree):\n"
    "    return walk.inner_files(tree, (), ('test_model_*.py',))\n"
)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _plant(tmp_path: Path, inner: dict[str, str] | None = None,
           walks: dict[str, str] | None = None) -> Path:
    """A tree holding a helper, one walk (or `walks`), the two documents and
    the inner tests `inner` maps to their text."""
    root = tmp_path / "tree"
    _write(root, HELPER, '"""the helper"""\n')
    for rel, text in (walks or {"tests/test_planted_keeps_the_suite_green.py": PLANTED_WALK}).items():
        _write(root, rel, text)
    _write(root, HEADLINE, "# proof\n")
    _write(root, DECISION, "# decision\n")
    for rel, text in {"tests/test_model_plain.py": "def test_x():\n    pass\n",
                      **(inner or {})}.items():
        _write(root, rel, text)
    return root


def _changed(tmp_path: Path, paths: list[str]) -> Path:
    path = tmp_path / "changed.txt"
    path.write_text("".join(f"{p}\n" for p in paths), encoding="utf-8")
    return path


def _decide(tmp_path: Path, paths: list[str], tree: Path) -> tuple[str, str]:
    return walk_reach.decide(_changed(tmp_path, paths), tree)


def _cli(*args: str) -> list[str]:
    proc = subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout.splitlines()


# ---------------------------------------------------------------------------
# 1. What skips.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("paths", [[HEADLINE], [DECISION], [HEADLINE, DECISION]],
                         ids=["proof-record", "architecture", "both"])
def test_a_document_no_walk_names_skips(tmp_path, paths):
    answer, why = _decide(tmp_path, paths, _plant(tmp_path))
    assert answer == "skip", why
    assert why


def test_decide_prints_skip_then_why_and_exits_0(tmp_path):
    lines = _cli("decide", "--changed", str(_changed(tmp_path, [HEADLINE])),
                 "--tree", str(_plant(tmp_path)))
    assert len(lines) == 2 and lines[0] == "skip", lines


# ---------------------------------------------------------------------------
# 2. What runs.
# ---------------------------------------------------------------------------

RUNS = [
    "config/models.yaml", "agents.yaml",
    "models.json", "requirements-dev.txt",
    ".github/workflows/tests.yml", ".github/actions/setup-python-cached/action.yml",
    "standards/console-honesty.md", "briefs/planner.md",
    HELPER, "tests/model_fixture.py",
    "scripts/model_config_change.py", "scripts/sync_model_config.py",
    "scripts/unit_test_parts.py", "scripts/walk_reach.py",
    "config/model-choices.json", "tests/fixtures/model-config-frozen/models.yaml",
    "scripts/no_longer_here.py",
]


@pytest.mark.parametrize("path", RUNS)
def test_every_other_path_runs_the_walks_and_is_named(tmp_path, path):
    answer, why = _decide(tmp_path, [path], ROOT)
    assert answer == "run", why
    assert path in why, why


@pytest.mark.parametrize("walk", parts.walks(ROOT))
def test_every_walk_on_the_tree_runs_the_walks(tmp_path, walk):
    answer, why = _decide(tmp_path, [walk], ROOT)
    assert (answer, walk in why) == ("run", True), why


def test_a_py_that_no_longer_exists_runs_even_beside_documents(tmp_path):
    gone = "tests/test_model_gone.py"
    assert not (ROOT / gone).exists()
    answer, why = _decide(tmp_path, [HEADLINE, gone], _plant(tmp_path))
    assert answer == "run" and gone in why, why


def test_a_document_beside_a_py_runs_and_names_the_py(tmp_path):
    answer, why = _decide(tmp_path, [HEADLINE, "scripts/unit_test_parts.py"],
                          _plant(tmp_path))
    assert answer == "run" and "scripts/unit_test_parts.py" in why, why


def test_a_missing_list_runs(tmp_path):
    answer, why = walk_reach.decide(tmp_path / "absent.txt", _plant(tmp_path))
    assert answer == "run" and "cannot read" in why, why


def test_an_empty_list_runs(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("")
    answer, why = walk_reach.decide(path, _plant(tmp_path))
    assert answer == "run" and "empty" in why, why


@pytest.mark.parametrize("line", ["/abs/path.md", " docs/padded.md", "docs/a\x01b.md", ""])
def test_an_unreadable_line_runs(tmp_path, line):
    path = tmp_path / "bad.txt"
    path.write_text(f"{HEADLINE}\n{line}\n{DECISION}\n")
    answer, why = walk_reach.decide(path, _plant(tmp_path))
    assert answer == "run" and "not a path" in why, why


def test_an_undecodable_list_runs(tmp_path):
    path = tmp_path / "bytes.txt"
    path.write_bytes(b"docs/\xff.md\n")
    answer, why = walk_reach.decide(path, _plant(tmp_path))
    assert answer == "run" and "cannot read" in why, why


def test_a_walk_that_cannot_be_imported_runs_and_names_the_walk(tmp_path):
    broken = "tests/test_broken_keeps_the_suite_green.py"
    root = _plant(tmp_path, walks={
        "tests/test_planted_keeps_the_suite_green.py": PLANTED_WALK,
        broken: "import no_such_module_for_dre_7908\n"})
    answer, why = _decide(tmp_path, [HEADLINE], root)
    assert answer == "run" and broken in why, why


def test_a_walk_whose_inner_run_raises_runs_and_names_the_walk(tmp_path):
    raising = "tests/test_raising_keeps_the_suite_green.py"
    root = _plant(tmp_path, walks={
        raising: "def suite_files(tree):\n    raise RuntimeError('planted')\n"})
    answer, why = _decide(tmp_path, [HEADLINE], root)
    assert answer == "run" and raising in why, why


def test_a_walk_with_no_inner_run_runs_and_names_the_walk(tmp_path):
    bare = "tests/test_bare_keeps_the_suite_green.py"
    root = _plant(tmp_path, walks={bare: "CASES = {}\n"})
    answer, why = _decide(tmp_path, [HEADLINE], root)
    assert answer == "run" and bare in why, why


def test_a_tree_with_no_walk_runs(tmp_path):
    root = _plant(tmp_path, walks={"tests/test_model_not_a_walk.py": "x = 1\n"})
    answer, why = _decide(tmp_path, [HEADLINE], root)
    assert answer == "run" and "no walk" in why, why


def test_a_scanned_file_that_cannot_be_read_runs_and_names_it(tmp_path):
    root = _plant(tmp_path)
    (root / HELPER).unlink()
    answer, why = _decide(tmp_path, [HEADLINE], root)
    assert answer == "run" and HELPER in why, why


def test_decide_prints_run_then_why_and_exits_0_when_it_cannot_read(tmp_path):
    lines = _cli("decide", "--changed", str(tmp_path / "absent.txt"),
                 "--tree", str(_plant(tmp_path)))
    assert len(lines) == 2 and lines[0] == "run", lines


# ---------------------------------------------------------------------------
# 3. A document a walk names runs — read off the walks, never listed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("named_by", ["path", "file name"])
def test_an_inner_test_that_newly_names_a_document_turns_it_to_run(tmp_path, named_by):
    root = _plant(tmp_path)
    assert _decide(tmp_path, [HEADLINE], root)[0] == "skip"
    token = HEADLINE if named_by == "path" else Path(HEADLINE).name
    _write(root, "tests/test_model_reads_it.py", f"DOC = {token!r}\n")
    answer, why = _decide(tmp_path, [HEADLINE], root)
    assert answer == "run" and HEADLINE in why, why
    assert "tests/test_model_reads_it.py" in why, why


@pytest.mark.parametrize("where", ["the walk", "the helper"])
def test_a_document_the_walk_or_the_helper_names_runs(tmp_path, where):
    root = _plant(tmp_path)
    rel = HELPER if where == "the helper" else "tests/test_planted_keeps_the_suite_green.py"
    _write(root, rel, (root / rel).read_text() + f"# {DECISION}\n")
    answer, why = _decide(tmp_path, [DECISION], root)
    assert answer == "run" and rel in why, why


def test_a_document_named_only_outside_every_inner_run_skips(tmp_path):
    root = _plant(tmp_path)
    _write(root, "tests/test_elsewhere.py", f"DOC = {HEADLINE!r}\n")
    _write(root, "scripts/reads_it.py", f"DOC = {HEADLINE!r}\n")
    assert _decide(tmp_path, [HEADLINE], root)[0] == "skip"


def test_the_name_check_holds_for_a_deleted_or_new_document(tmp_path):
    root = _plant(tmp_path, inner={"tests/test_model_reads_it.py": f"DOC = {HEADLINE!r}\n"})
    (root / HEADLINE).unlink()
    assert _decide(tmp_path, [HEADLINE], root)[0] == "run"
    assert _decide(tmp_path, ["docs/brand-new.md"], root)[0] == "skip"


def test_a_planted_test_naming_a_document_that_does_not_exist_adds_nothing(tmp_path):
    ghost = "docs/no-such-proof.md"
    root = _plant(tmp_path, inner={"tests/test_model_ghost.py": f"DOC = {ghost!r}\n"})
    assert not (root / ghost).exists()
    assert _named_documents(root) == {}
    assert _decide(tmp_path, [HEADLINE], root)[0] == "skip"


# ---------------------------------------------------------------------------
# 4. The one exemption.
# ---------------------------------------------------------------------------


def test_not_scanned_is_this_file_alone():
    assert walk_reach.NOT_SCANNED == ("tests/test_walk_reach.py",)
    assert Path(__file__).resolve().relative_to(ROOT).as_posix() in walk_reach.NOT_SCANNED


def test_this_file_is_in_a_walks_inner_run_so_the_exemption_does_work():
    inner = {rel for walk in parts.walks(ROOT)
             for rel in parts._load_walk(ROOT, walk).suite_files(ROOT)}
    assert "tests/test_walk_reach.py" in inner


def test_a_not_scanned_file_naming_a_document_leaves_it_skipped(tmp_path):
    exempt = walk_reach.NOT_SCANNED[0]
    root = _plant(tmp_path, walks={"tests/test_planted_keeps_the_suite_green.py": (
        PLANTED_WALK.replace("('test_model_*.py',)", "('test_model_*.py', 'test_walk_*.py')"))})
    _write(root, exempt, f"DOC = {HEADLINE!r}\n")
    assert exempt in parts._load_walk(root, "tests/test_planted_keeps_the_suite_green.py").suite_files(root)
    assert _decide(tmp_path, [HEADLINE], root)[0] == "skip"
    _write(root, "tests/test_model_also.py", f"DOC = {HEADLINE!r}\n")
    answer, why = _decide(tmp_path, [HEADLINE], root)
    assert answer == "run" and "tests/test_model_also.py" in why, why


# ---------------------------------------------------------------------------
# 5. The real tree.
# ---------------------------------------------------------------------------


def _scanned(root: Path) -> list[str]:
    """Every walk, the helper and every file in any walk's `suite_files`,
    less `NOT_SCANNED` — computed here, apart from the script."""
    files = set(parts.walks(root)) | {HELPER}
    for walk in parts.walks(root):
        files |= set(parts._load_walk(root, walk).suite_files(root))
    return sorted(files - set(walk_reach.NOT_SCANNED))


def _named_documents(root: Path) -> dict[str, str]:
    """Every file that EXISTS under `docs/` or `architecture/` whose repo path
    or file name is in a scanned file's text, and the first file naming it."""
    texts = {rel: (root / rel).read_text(encoding="utf-8", errors="replace")
             for rel in _scanned(root)}
    docs = sorted(p.relative_to(root).as_posix() for top in ("docs", "architecture")
                  if (root / top).is_dir() for p in (root / top).rglob("*") if p.is_file())
    out = {}
    for doc in docs:
        by = next((rel for rel, text in texts.items()
                   if doc in text or Path(doc).name in text), None)
        if by:
            out[doc] = by
    return out


@functools.cache
def _real_named() -> dict[str, str]:
    return _named_documents(ROOT)


def test_the_real_tree_names_the_documents_the_card_found():
    named = _real_named()
    for doc in NAMED_ON_MAIN:
        assert doc in named, sorted(named)
    assert HEADLINE not in named, named.get(HEADLINE)
    assert (ROOT / HEADLINE).is_file()


def test_every_document_the_real_tree_names_runs_the_walks():
    texts = walk_reach.scanned_texts(ROOT)
    assert sorted(texts) == _scanned(ROOT)
    wrong = {doc: walk_reach.naming(doc, texts) for doc in _real_named()
             if walk_reach.naming(doc, texts) is None}
    assert wrong == {}, wrong


@pytest.mark.parametrize("doc", NAMED_ON_MAIN)
def test_decide_on_the_real_tree_runs_for_a_named_document(tmp_path, doc):
    answer, why = _decide(tmp_path, [doc], ROOT)
    assert answer == "run" and doc in why, why


def test_decide_on_the_real_tree_skips_the_headline_merge(tmp_path):
    lines = _cli("decide", "--changed", str(_changed(tmp_path, [HEADLINE])),
                 "--tree", str(ROOT))
    assert lines[0] == "skip", lines


# ---------------------------------------------------------------------------
# 6. The compare base on a push to main.
# ---------------------------------------------------------------------------

P, A, B, C = (c * 40 for c in "abcd")


def _run(sha: str, conclusion: str, created: str, event: str = "push",
         branch: str = "main") -> dict:
    return {"head_sha": sha, "conclusion": conclusion, "status": "completed",
            "event": event, "head_branch": branch, "created_at": created}


def _history(*commits: tuple[str, list[str]]):
    """A planted main: commits oldest first, each with the files it changed.
    Returns the ancestry check and the files changed between two commits."""
    order = [sha for sha, _ in commits]
    files = dict(commits)

    def is_ancestor(sha: str, head: str) -> bool:
        return sha in order and order.index(sha) <= order.index(head)

    def diff(base: str, head: str) -> list[str]:
        return [f for sha in order[order.index(base) + 1:order.index(head) + 1]
                for f in files[sha]]

    return is_ancestor, diff


def _decide_push(tmp_path, runs, head, history) -> tuple[str, str]:
    is_ancestor, diff = history
    base, why = walk_reach.push_base(runs, head, lambda sha: is_ancestor(sha, head))
    if base is None:
        return "run", why
    return _decide(tmp_path, diff(base, head), _plant(tmp_path))


def test_a_doc_push_after_a_red_code_push_runs(tmp_path):
    """Against `event.before` B would diff its one document, skip, go green
    and move `stable` past A's red walks."""
    history = _history((P, []), (A, ["scripts/unit_test_parts.py"]), (B, [HEADLINE]))
    runs = [_run(A, "failure", "2026-10-24T21:10:00Z"),
            _run(P, "success", "2026-10-24T20:00:00Z")]
    assert walk_reach.push_base(runs, B, lambda sha: history[0](sha, B))[0] == P
    answer, why = _decide_push(tmp_path, runs, B, history)
    assert answer == "run" and "scripts/unit_test_parts.py" in why, why


def test_a_doc_push_after_a_green_push_skips(tmp_path):
    history = _history((P, []), (A, ["scripts/unit_test_parts.py"]), (B, [HEADLINE]))
    runs = [_run(A, "success", "2026-10-24T21:10:00Z"),
            _run(P, "success", "2026-10-24T20:00:00Z")]
    assert _decide_push(tmp_path, runs, B, history)[0] == "skip"


def test_a_green_schedule_run_is_a_base_too(tmp_path):
    history = _history((P, []), (A, ["scripts/unit_test_parts.py"]), (B, [HEADLINE]))
    runs = [_run(A, "success", "2026-10-25T10:41:00Z", event="schedule"),
            _run(A, "failure", "2026-10-24T21:10:00Z")]
    assert walk_reach.push_base(runs, B, lambda sha: True)[0] == A
    assert _decide_push(tmp_path, runs, B, history)[0] == "skip"


def test_the_newest_success_wins_whatever_order_the_list_is_in():
    runs = [_run(P, "success", "2026-10-24T20:00:00Z"),
            _run(A, "success", "2026-10-24T21:10:00Z")]
    assert walk_reach.push_base(runs, B, lambda sha: True)[0] == A


@pytest.mark.parametrize("run", [
    _run(A, "success", "2026-10-24T21:10:00Z", event="pull_request"),
    _run(A, "success", "2026-10-24T21:10:00Z", branch="agent/DRE-1-x"),
    _run(A, "cancelled", "2026-10-24T21:10:00Z"),
    _run(B, "success", "2026-10-24T21:20:00Z"),
], ids=["pull-request-run", "other-branch", "not-success", "head-itself"])
def test_only_an_earlier_main_success_is_a_base(run):
    runs = [run, _run(P, "success", "2026-10-24T20:00:00Z")]
    assert walk_reach.push_base(runs, B, lambda sha: True)[0] == P


def test_no_success_commit_runs():
    runs = [_run(A, "failure", "2026-10-24T21:10:00Z")]
    base, why = walk_reach.push_base(runs, B, lambda sha: True)
    assert base is None and why
    assert walk_reach.push_base([], B, lambda sha: True)[0] is None


def test_a_base_that_is_not_an_ancestor_runs():
    runs = [_run(A, "success", "2026-10-24T21:10:00Z")]
    base, why = walk_reach.push_base(runs, B, lambda sha: False)
    assert base is None and A in why, why


@pytest.mark.parametrize("runs", [
    None, "not a list", [{"head_sha": A}], [None],
    [_run("not-a-sha", "success", "2026-10-24T21:10:00Z")],
], ids=["none", "string", "missing-keys", "null-run", "bad-sha"])
def test_a_run_list_it_cannot_read_runs(runs):
    base, why = walk_reach.push_base(runs, B, lambda sha: True)
    assert base is None and why


def test_an_ancestry_check_that_fails_runs():
    def broken(sha):
        raise OSError("git is not here")
    runs = [_run(A, "success", "2026-10-24T21:10:00Z")]
    base, why = walk_reach.push_base(runs, B, broken)
    assert base is None and "git is not here" in why, why


def test_base_prints_none_and_why_for_a_lookup_it_cannot_read(tmp_path):
    for body in ("{not json", json.dumps({"workflow_runs": []})):
        path = tmp_path / "runs.json"
        path.write_text(body)
        lines = _cli("base", "--runs", str(path), "--head", B)
        assert len(lines) == 2 and lines[0] == "none", lines
    lines = _cli("base", "--runs", str(tmp_path / "absent.json"), "--head", B)
    assert lines[0] == "none", lines
