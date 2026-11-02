"""RED-first: the judge of a change to the verify step (DRE-7972).

`scripts/groom_replay_gate.py` answers three questions over the cases
DRE-7971 writes and the verified proposal a replay leaves behind:

  * `reach` — does a pull request touch the verify step? It reads the changed
    paths with `git diff --name-only <base>...<head>`, and answers true when
    it cannot (fail open);
  * `judge` — where did each case end on the change, and did any case that
    passes on `main` fail? That case, `regressed`, fails the run — and so,
    failing closed (DRE-8741), does a case that passes on `main` and that the
    replay did not check, and a run that checked no case at all;
  * `record-main` — the only writer of each case's `main` block.

The failures this file holds shut: a change to the Cancel refusals in
`groomer.py` not replayed; a new `groom_*` import nobody decided about; a case
that never passed on `main` blocking a pull request; a card with no verify
record read as a pass; a crashed verify leg's `unverified` record read as a
pass (DRE-8741, bureau-pipeline #1362); a run that checked nothing exiting 0;
the search a case was judged on changing a verdict; `record-main` touching any
byte of a case but its `main` block.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_replay_gate.py -q
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groom_replay  # noqa: E402
import groom_replay_cases as grc  # noqa: E402
import groom_replay_gate as gate  # noqa: E402
import groom_verify_agent as gva  # noqa: E402

TAG = "groom-replay-gate:"
AT = "2026-11-01T15:00:00Z"


# --------------------------------------------------------------------------
# the contract
# --------------------------------------------------------------------------
def test_the_contract_constants():
    assert gate.TAG == TAG
    assert gate.VERIFY_STEP_PATHS == (
        "scripts/groom_verify_agent.py", "scripts/groom_lookups.py",
        "scripts/groom_routing.py", "scripts/groom_replay.py",
        "briefs/groom-verify.md", ".github/workflows/groomer.yml",
        "scripts/groomer.py", "scripts/groom_verify.py")
    for path in gate.VERIFY_STEP_PATHS:
        assert (ROOT / path).is_file(), f"{path} is listed and does not exist"


def test_naming_groomer_as_a_path_is_not_running_it():
    """`VERIFY_STEP_PATHS` names `groomer.py` and never runs it, so the gate
    reads no model home and must not be held to carry its token (DRE-6940's
    `check_model_home_scope.py` reads a list or tuple literal holding a
    declaring script's `.py` name as a command that runs it)."""
    import check_model_home_scope as scope
    found, _ = scope.readers(ROOT / "scripts")
    assert "groom_replay_gate.py" not in found, found.get("groom_replay_gate.py")
    assert not hasattr(gate, "READS_MODEL_HOME")


def test_the_expected_modules_are_not_replayed_each_with_a_reason():
    for name in ("groom_context", "groom_judgement", "groom_memory",
                 "groom_priority", "groom_readiness"):
        assert f"scripts/{name}.py" in gate.NOT_REPLAYED
    for path, why in gate.NOT_REPLAYED.items():
        assert path.startswith("scripts/groom") and path.endswith(".py"), path
        assert path not in gate.VERIFY_STEP_PATHS, path
        assert isinstance(why, str) and why.strip(), f"{path} has no reason"


# --------------------------------------------------------------------------
# the import walk — a new `groom*` import is decided, never drifted into
# --------------------------------------------------------------------------
def _groom_imports(path: Path) -> set[str]:
    """Every `groom*` module the file imports, read with `ast` here too — so
    this test does not trust the module's own reader."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
    return {f"scripts/{name}.py" for name in found if name.startswith("groom")}


def test_every_groom_import_of_a_listed_file_is_listed_or_not_replayed():
    listed = [p for p in gate.VERIFY_STEP_PATHS if p.endswith(".py")]
    assert listed
    walked = set()
    for path in listed:
        walked |= _groom_imports(ROOT / path)
    assert walked, "the walk found no groom* import at all"
    loose = sorted(walked - set(gate.VERIFY_STEP_PATHS) - set(gate.NOT_REPLAYED))
    assert loose == [], f"imported and in neither list: {loose}"
    assert gate.unlisted_imports(ROOT) == []


def test_every_not_replayed_entry_is_imported_by_a_listed_file():
    """A stale entry is a decision nobody needs any more."""
    walked = set()
    for path in gate.VERIFY_STEP_PATHS:
        if path.endswith(".py"):
            walked |= _groom_imports(ROOT / path)
    assert set(gate.NOT_REPLAYED) <= walked


def test_an_unlisted_groom_import_fails_the_check_naming_the_module(tmp_path):
    for path in gate.VERIFY_STEP_PATHS:
        if path.endswith(".py"):
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / path, tmp_path / path)
    assert gate.unlisted_imports(tmp_path) == []
    target = tmp_path / "scripts" / "groom_replay.py"
    text = target.read_text(encoding="utf-8")
    target.write_text(text.replace(
        "import groomer  #", "import groom_newcomer  # noqa: E402\nimport groomer  #", 1),
        encoding="utf-8")
    assert gate.unlisted_imports(tmp_path) == ["scripts/groom_newcomer.py"]


def test_a_from_import_inside_a_function_is_walked_too(tmp_path):
    target = tmp_path / "scripts" / "groom_verify.py"
    target.parent.mkdir(parents=True)
    target.write_text("def f():\n    from groom_later import x\n    return x\n",
                      encoding="utf-8")
    assert gate.unlisted_imports(tmp_path) == ["scripts/groom_later.py"]


def test_the_gate_reads_neither_linear_nor_the_door():
    tree = ast.parse((ROOT / "scripts" / "groom_replay_gate.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert "linear_ops" not in names
    assert "bureau_read" not in names


# --------------------------------------------------------------------------
# reach — against a throwaway repository
# --------------------------------------------------------------------------
def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env=env).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _change(repo: Path, paths) -> tuple[str, str]:
    base = _git(repo, "rev-parse", "HEAD")
    for path in paths:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"changed {path}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    return base, _git(repo, "rev-parse", "HEAD")


def _gate_set(tmp_path, cards) -> Path:
    rows = [{"identifier": c, "title": c, "position": n, "repo": "bureau-pipeline"}
            for n, c in enumerate(cards, 1)]
    doc = {"id": "groom-replay-gate-set", "outcomes": {"now": rows, "not-now": [], "dead": []},
           "sequence": [dict(r) for r in rows]}
    path = tmp_path / "gate-set.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _reach(repo, base, head, gate_set, monkeypatch, capsys, tmp_path):
    out = tmp_path / "github-output"
    out.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.chdir(repo)
    code = gate.main(["reach", "--base", base, "--head", head,
                      "--gate-set", str(gate_set)])
    written = dict(line.split("=", 1)
                   for line in out.read_text(encoding="utf-8").splitlines() if line)
    return code, written, capsys.readouterr().out.strip()


@pytest.mark.parametrize("path", gate.VERIFY_STEP_PATHS)
def test_reach_is_true_for_each_verify_step_path(path, repo, tmp_path, monkeypatch, capsys):
    base, head = _change(repo, [path, "docs/other.md"])
    code, written, said = _reach(repo, base, head, _gate_set(tmp_path, ["DRE-1"]),
                                 monkeypatch, capsys, tmp_path)
    assert code == 0
    assert written == {"reached": "true", "cases": "1"}
    assert said.startswith(f"{TAG} reach true cards=1 — ")
    assert path in said and "docs/other.md" not in said


def test_reach_is_true_for_the_cancel_refusals_and_the_spare_count(
        repo, tmp_path, monkeypatch, capsys):
    base, head = _change(repo, ["scripts/groomer.py", "scripts/groom_verify.py"])
    code, written, said = _reach(repo, base, head, _gate_set(tmp_path, ["DRE-1"]),
                                 monkeypatch, capsys, tmp_path)
    assert written["reached"] == "true"
    assert "scripts/groomer.py" in said and "scripts/groom_verify.py" in said


def test_reach_is_false_for_the_scorecard_the_doc_and_the_cases(
        repo, tmp_path, monkeypatch, capsys):
    base, head = _change(repo, [
        "scripts/groom_scorecard.py", "docs/groomer.md",
        "tests/fixtures/groom-replay-cases/cases.json",
        "tests/fixtures/groom-replay-cases/replay-set.json",
        "tests/fixtures/groom-replay-cases/gate-set.json"])
    code, written, said = _reach(repo, base, head,
                                 _gate_set(tmp_path, ["DRE-1", "DRE-2"]),
                                 monkeypatch, capsys, tmp_path)
    assert code == 0
    assert written == {"reached": "false", "cases": "2"}
    assert said == f"{TAG} reach false cards=2 — no verify-step path changed"


def test_reach_fails_open_with_a_reason_when_git_diff_fails(
        repo, tmp_path, monkeypatch, capsys):
    head = _git(repo, "rev-parse", "HEAD")
    code, written, said = _reach(repo, "0" * 40, head, _gate_set(tmp_path, []),
                                 monkeypatch, capsys, tmp_path)
    assert code == 0
    assert written == {"reached": "true", "cases": "0"}
    assert said.startswith(f"{TAG} reach true cards=0 — ")
    assert "git diff" in said and "failed open" in said


@pytest.mark.parametrize("path", ["scripts/groomer.py", "scripts/groom_verify_agent.py"])
def test_reach_is_true_when_a_verify_step_path_is_renamed_away(
        path, repo, tmp_path, monkeypatch, capsys):
    _change(repo, [path])
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "mv", path, path.replace(".py", "_old.py"))
    _git(repo, "commit", "-q", "-m", "rename")
    head = _git(repo, "rev-parse", "HEAD")
    code, written, said = _reach(repo, base, head, _gate_set(tmp_path, ["DRE-1"]),
                                 monkeypatch, capsys, tmp_path)
    assert code == 0
    assert written == {"reached": "true", "cases": "1"}
    assert said.startswith(f"{TAG} reach true cards=1 — ")
    assert path in said


def _corrupt(tmp_path) -> Path:
    path = tmp_path / "gate-set.json"
    path.write_text("{not json", encoding="utf-8")
    return path


def _not_a_proposal(tmp_path) -> Path:
    path = tmp_path / "gate-set.json"
    path.write_text(json.dumps({"cases": []}), encoding="utf-8")
    return path


@pytest.mark.parametrize("gate_set", [
    lambda tmp_path: tmp_path / "missing" / "gate-set.json", _corrupt, _not_a_proposal],
    ids=["missing", "corrupt", "not-a-proposal"])
def test_reach_fails_open_with_a_reason_when_the_gate_set_is_unreadable(
        gate_set, repo, tmp_path, monkeypatch, capsys):
    base, head = _change(repo, ["docs/groomer.md"])
    code, written, said = _reach(repo, base, head, gate_set(tmp_path),
                                 monkeypatch, capsys, tmp_path)
    assert code == 0
    assert written == {"reached": "true", "cases": "0"}
    assert said.startswith(f"{TAG} reach true cards=0 — failed open: ")
    assert "the gate set" in said


def test_cases_counts_distinct_cards_in_the_gate_set(repo, tmp_path, monkeypatch, capsys):
    base, head = _change(repo, ["docs/groomer.md"])
    gate_set = _gate_set(tmp_path, ["DRE-1", "DRE-2", "DRE-3"])
    doc = json.loads(gate_set.read_text(encoding="utf-8"))
    doc["outcomes"]["now"].append(dict(doc["outcomes"]["now"][0]))
    gate_set.write_text(json.dumps(doc), encoding="utf-8")
    _, written, _ = _reach(repo, base, head, gate_set, monkeypatch, capsys, tmp_path)
    assert written["cases"] == "3"


def test_reach_counts_the_real_gate_set_the_cases_file_builds(tmp_path):
    cases = [dict(grc.SEED_CASES[0], main={"ended": "cancel", "at": AT, "passes": True})]
    path = tmp_path / "gate-set.json"
    path.write_text(json.dumps(grc.gate_set(cases)), encoding="utf-8")
    assert gate.gate_cases(path) == 1


def test_reach_writes_no_output_without_github_output(repo, tmp_path, monkeypatch, capsys):
    base, head = _change(repo, ["scripts/groomer.py"])
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.chdir(repo)
    assert gate.main(["reach", "--base", base, "--head", head,
                      "--gate-set", str(_gate_set(tmp_path, ["DRE-1"]))]) == 0
    assert capsys.readouterr().out.startswith(f"{TAG} reach true cards=1")


# --------------------------------------------------------------------------
# judge — a verified proposal and a three-case cases.json
# --------------------------------------------------------------------------
def _case(case_id, card, expect, passes):
    return {"case": case_id, "card": card, "proposal_id": "aaaa11112222",
            "standing_card": "DRE-4541", "list": expect.split("-", 1)[1],
            "decision": "not-doing", "reason": "already-done",
            "reason_label": "Already done", "recorded_at": "2026-10-20T13:00:00Z",
            "source": "door", "expect": expect, "title": f"the {card} card",
            "repo": "bureau-pipeline",
            "main": {"ended": None, "at": None, "passes": passes}}


def _cases_file(tmp_path, cases=None) -> Path:
    cases = cases or [_case("c-planning", "DRE-101", "off-planning", True),
                      _case("c-cancel", "DRE-102", "off-cancel", False),
                      _case("c-none", "DRE-103", "off-planning", None)]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"schema": grc.SCHEMA, "built_at": None,
                                "cases": cases}, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def _row(card, verdict, reason=None):
    row = {"identifier": card, "title": card, "position": 1, "repo": "bureau-pipeline",
           "reason": ""}
    if verdict is not None:
        row["verify"] = {"verdict": verdict, "summary": "s", "proof": [], "rested_on": [],
                         "reason": reason}
    return row


VERDICT_OF = {groom_replay.PLANNING: gva.STILL_NEEDED,
              groom_replay.CANCEL: gva.DONE_ELSEWHERE,
              groom_replay.REWRITE: gva.PARTLY_SOLVED,
              groom_replay.NOT_VERIFIED: gva.UNVERIFIED,
              groom_replay.EXCLUDED: gva.EXCLUDED}


#: The reason `apply` writes on each unproved verdict's record here.
REASON_OF = {groom_replay.NOT_VERIFIED: gva.NO_PROOF,
             groom_replay.EXCLUDED: "hand-built"}


def _proposal(tmp_path, ends: dict, *, no_record=(), reasons=None) -> Path:
    """A verified proposal: `ends` is `{card: where it ended}`; a card in
    `no_record` carries no verify record; `reasons` is `{card: the reason an
    unproved verdict's record carries}`, else `REASON_OF`'s."""
    outcomes = {"now": [], "not-now": [], "dead": [], gva.REWRITE: []}
    seq = []
    for card, where in ends.items():
        reason = (reasons or {}).get(card, REASON_OF.get(where))
        row = _row(card, None if card in no_record else VERDICT_OF[where], reason)
        if where == groom_replay.PLANNING:
            outcomes["now"].append(row)
        elif where == groom_replay.CANCEL:
            outcomes["dead"].append(row)
        elif where == groom_replay.REWRITE:
            outcomes[gva.REWRITE].append(row)
        elif where == groom_replay.NOT_VERIFIED:
            row["reason"] = gva.UNVERIFIED_PREFIX + reason
            outcomes["not-now"].append(row)
        elif where == groom_replay.EXCLUDED:
            row["reason"] = gva.EXCLUDED_PREFIX + reason
            outcomes["not-now"].append(row)
        seq.append(dict(row))
    path = tmp_path / "proposal-verified.json"
    path.write_text(json.dumps({"id": "replayed", "outcomes": outcomes,
                                "sequence": seq}), encoding="utf-8")
    for card, where in ends.items():
        assert groom_replay.ended(json.loads(path.read_text()), card) == where
    return path


def _targets(tmp_path, sources: dict) -> Path:
    rows = []
    for card, source in sources.items():
        rows.append({"card": card, "list": "planning",
                     "matched_work": {"searched": source is not None, "source": source,
                                      "why": None, "terms": [], "cards": []}})
    path = tmp_path / "verify-targets.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def _judge(tmp_path, proposal, capsys, *extra):
    out = tmp_path / "judged.json"
    code = gate.main(["judge", "--proposal", str(proposal),
                      "--cases", str(_cases_file(tmp_path)), "--out", str(out), *extra])
    lines = capsys.readouterr().out.strip().splitlines()
    return code, lines, json.loads(out.read_text(encoding="utf-8"))


def _line(lines, case_id):
    return next(line for line in lines if line.startswith(f"{TAG} {case_id} "))


def test_a_passing_case_that_ends_on_planning_is_regressed(tmp_path, capsys):
    proposal = _proposal(tmp_path, {"DRE-101": "planning", "DRE-102": "planning"},
                         no_record=())
    code, lines, doc = _judge(tmp_path, proposal, capsys)
    assert code == 1
    assert _line(lines, "c-planning") == (
        f"{TAG} c-planning expect=off-planning main=pass head=planning "
        "search=none fail — REGRESSED")
    assert lines[-1] == f"{TAG} 3 cases — 1 pass, 1 fail, 1 not judged, 1 regressed"
    assert doc["regressed"] == ["c-planning"]


@pytest.mark.parametrize("where", ["cancel", "rewrite"])
def test_a_passing_case_off_planning_on_a_real_verdict_passes(where, tmp_path, capsys):
    """A genuine answer is unchanged by failing closed (DRE-8741)."""
    proposal = _proposal(tmp_path, {"DRE-101": where, "DRE-102": "planning"})
    code, lines, doc = _judge(tmp_path, proposal, capsys)
    assert code == 0
    assert _line(lines, "c-planning") == (
        f"{TAG} c-planning expect=off-planning main=pass head={where} "
        "search=none pass")
    assert doc["regressed"] == []


def test_a_case_that_never_passed_on_main_never_fails_the_run(tmp_path, capsys):
    proposal = _proposal(tmp_path, {"DRE-101": "cancel", "DRE-102": "cancel",
                                    "DRE-103": "planning"})
    code, lines, doc = _judge(tmp_path, proposal, capsys)
    assert code == 0
    assert _line(lines, "c-cancel") == (
        f"{TAG} c-cancel expect=off-cancel main=fail head=cancel search=none fail")
    assert _line(lines, "c-none") == (
        f"{TAG} c-none expect=off-planning main=none head=planning search=none fail")
    assert doc["regressed"] == [] and doc["now_passes"] == []


def test_a_case_that_failed_on_main_and_passes_now_says_so(tmp_path, capsys):
    proposal = _proposal(tmp_path, {"DRE-101": "cancel", "DRE-102": "planning",
                                    "DRE-103": "rewrite"})
    code, lines, doc = _judge(tmp_path, proposal, capsys)
    assert code == 0
    assert _line(lines, "c-cancel") == (
        f"{TAG} c-cancel expect=off-cancel main=fail head=planning search=none "
        "pass — now passes")
    assert _line(lines, "c-none").endswith("pass — now passes")
    assert doc["now_passes"] == ["c-cancel", "c-none"]
    assert lines[-1] == f"{TAG} 3 cases — 3 pass, 0 fail, 0 not judged, 0 regressed"


# --------------------------------------------------------------------------
# failing closed (DRE-8741) — a case the replay did not check fails the gate
# --------------------------------------------------------------------------
@pytest.mark.parametrize("reason", [gva.STEP_FAILED, gva.STEP_SKIPPED, gva.NO_FILE,
                                    gva.NO_PROOF])
def test_a_passing_case_whose_record_is_unverified_fails_closed_naming_the_reason(
        reason, tmp_path, capsys):
    """Gate run 41370789518: the leg crashed on startup, `verdict` wrote
    `unverified (agent step failed)`, the card ended `not-verified`, and the
    judge read `not-verified != planning` as a pass."""
    proposal = _proposal(tmp_path, {"DRE-101": "not-verified", "DRE-102": "cancel"},
                         reasons={"DRE-101": reason})
    code, lines, doc = _judge(tmp_path, proposal, capsys)
    assert code == 1
    assert _line(lines, "c-planning") == (
        f"{TAG} c-planning expect=off-planning main=pass head=not-verified "
        f"search=none not judged — UNCHECKED: unverified ({reason})")
    assert lines[-2] == f"{TAG} 3 cases — 0 pass, 1 fail, 2 not judged, 0 regressed"
    assert lines[-1] == (f"{TAG} failed closed — 1 case passes on main and was "
                         f"not checked: c-planning (DRE-101, unverified ({reason}))")
    assert doc["unchecked"] == ["c-planning"] and doc["regressed"] == []
    assert doc["cases"][0]["result"] == "not judged"
    assert doc["cases"][0]["unchecked"] is True
    assert doc["cases"][0]["why"] == f"unverified ({reason})"
    # A crash is no rate limit (DRE-8859): nothing calls it infrastructure.
    assert doc["rate_limited"] == []
    assert not any("rate-limited" in line for line in lines)


# --------------------------------------------------------------------------
# a case left unchecked by a rate-limited lookup is named infrastructure
# (DRE-8859) — and still fails closed
# --------------------------------------------------------------------------
LIMITED = ("lookup failed: dreadnought-foundry/bureau-pipeline did not answer — "
           "rate limited: gh: API rate limit exceeded for installation ID 42 (HTTP 403)")
RERUN = ("not a finding about this change — re-run all jobs: re-running only "
         "the failed job re-reads the same lookup")


def test_a_passing_case_unchecked_on_a_rate_limit_is_named_infrastructure(
        tmp_path, capsys):
    """bureau-pipeline #1396, gate run 41439594582: the lookup leg was
    throttled, the seed case's card was recorded `unverified (lookup failed:
    … rate limited: …)`, and the judge failed closed with nothing saying the
    cause was GitHub's and a full re-run the cure."""
    proposal = _proposal(tmp_path, {"DRE-101": "not-verified", "DRE-102": "cancel"},
                         reasons={"DRE-101": LIMITED})
    code, lines, doc = _judge(tmp_path, proposal, capsys)
    assert code == 1
    case = _line(lines, "c-planning")
    assert case == (f"{TAG} c-planning expect=off-planning main=pass "
                    f"head=not-verified search=none not judged — UNCHECKED: "
                    f"unverified ({LIMITED})")
    # The result is `not judged`, never `fail`, and never REGRESSED.
    assert case.split(" search=none ")[1].startswith("not judged — UNCHECKED: ")
    assert "REGRESSED" not in case
    limited = (f"{TAG} failed closed — rate-limited: could not read "
               f"dreadnought-foundry/bureau-pipeline for c-planning (DRE-101), {RERUN}")
    assert limited in lines
    # Before the existing failed-closed lines, which are unchanged.
    assert lines[-2] == limited
    assert lines[-1] == (f"{TAG} failed closed — 1 case passes on main and was "
                         f"not checked: c-planning (DRE-101, unverified ({LIMITED}))")
    assert doc["rate_limited"] == ["c-planning"]
    assert doc["unchecked"] == ["c-planning"] and doc["regressed"] == []


def test_a_rate_limit_whose_reason_names_no_repo_says_the_cards_repo(tmp_path, capsys):
    reason = ("lookup failed: no repo answered — dreadnought-foundry: rate limited: "
              "gh: API rate limit exceeded for installation ID 42 (HTTP 403)")
    proposal = _proposal(tmp_path, {"DRE-101": "not-verified", "DRE-102": "cancel"},
                         reasons={"DRE-101": reason})
    code, lines, doc = _judge(tmp_path, proposal, capsys)
    assert code == 1
    assert (f"{TAG} failed closed — rate-limited: could not read the card's repo "
            f"for c-planning (DRE-101), {RERUN}") in lines
    assert doc["rate_limited"] == ["c-planning"]


@pytest.mark.parametrize("reason", [
    "board context unread: Linear said 429: rate limited",
    "lookup failed: dreadnought-foundry/bureau-pipeline did not answer — "
    "time budget of 300 s spent",
], ids=["rate-limit-not-a-lookup", "lookup-not-a-rate-limit"])
def test_only_a_rate_limited_lookup_is_named_infrastructure(reason, tmp_path, capsys):
    proposal = _proposal(tmp_path, {"DRE-101": "not-verified", "DRE-102": "cancel"},
                         reasons={"DRE-101": reason})
    code, lines, doc = _judge(tmp_path, proposal, capsys)
    assert code == 1
    assert doc["rate_limited"] == []
    assert not any("rate-limited" in line for line in lines)


def test_a_rate_limited_case_that_never_passed_on_main_is_not_named(tmp_path, capsys):
    """Only an unchecked case — one that passes on `main` — is listed."""
    proposal = _proposal(tmp_path, {"DRE-101": "cancel", "DRE-102": "not-verified"},
                         reasons={"DRE-102": LIMITED})
    code, lines, doc = _judge(tmp_path, proposal, capsys)
    assert code == 0, lines
    assert doc["rate_limited"] == []
    assert not any("rate-limited" in line for line in lines)


def test_an_unverified_record_on_a_case_that_never_passed_fails_nothing(tmp_path, capsys):
    """The ratchet holds: only a case that passes on `main` fails closed."""
    proposal = _proposal(tmp_path, {"DRE-101": "cancel", "DRE-102": "not-verified",
                                    "DRE-103": "not-verified"},
                         reasons={"DRE-102": gva.STEP_FAILED, "DRE-103": gva.STEP_FAILED})
    code, lines, doc = _judge(tmp_path, proposal, capsys)
    assert code == 0, lines
    assert _line(lines, "c-cancel").endswith(" not judged")
    assert _line(lines, "c-none").endswith(" not judged")
    assert lines[-1] == f"{TAG} 3 cases — 1 pass, 0 fail, 2 not judged, 0 regressed"
    assert doc["unchecked"] == []


@pytest.mark.parametrize("how,why", [
    ("no-record", "no verify record"),
    ("excluded", "excluded (hand-built)"),
    ("missing", "not on the replayed proposal"),
])
def test_a_passing_case_left_unjudged_any_other_way_fails_closed_too(
        how, why, tmp_path, capsys):
    """Whatever left it unjudged — never a pass, never left out silently."""
    ends = {"DRE-102": "cancel"}
    if how == "no-record":
        ends["DRE-101"] = "planning"
    elif how == "excluded":
        ends["DRE-101"] = "excluded"
    proposal = _proposal(tmp_path, ends,
                         no_record=("DRE-101",) if how == "no-record" else ())
    code, lines, doc = _judge(tmp_path, proposal, capsys)
    assert code == 1
    assert _line(lines, "c-planning").endswith(f" not judged — UNCHECKED: {why}")
    assert lines[-1] == (f"{TAG} failed closed — 1 case passes on main and was "
                         f"not checked: c-planning (DRE-101, {why})")
    assert doc["unchecked"] == ["c-planning"]


def test_an_excluded_verdict_fails_closed_wherever_the_card_ended(tmp_path, capsys):
    """The verdict decides, not the list: an `excluded` record on a card the
    proposal left on Planning is not judged, so never regressed — and, since
    the case passes on `main`, it fails the gate unchecked."""
    proposal = _proposal(tmp_path, {"DRE-101": "planning", "DRE-102": "cancel"})
    doc = json.loads(proposal.read_text())
    for row in doc["outcomes"]["now"] + doc["sequence"]:
        if row["identifier"] == "DRE-101":
            row["verify"].update(verdict=gva.EXCLUDED, reason="hand-built")
    proposal.write_text(json.dumps(doc), encoding="utf-8")
    code, lines, out = _judge(tmp_path, proposal, capsys)
    assert code == 1
    assert _line(lines, "c-planning") == (
        f"{TAG} c-planning expect=off-planning main=pass head=planning "
        "search=none not judged — UNCHECKED: excluded (hand-built)")
    assert out["regressed"] == [] and out["unchecked"] == ["c-planning"]


@pytest.mark.parametrize("ends,no_record", [
    ({}, ()),
    ({"DRE-101": "planning", "DRE-102": "planning", "DRE-103": "planning"},
     ("DRE-101", "DRE-102", "DRE-103")),
    ({"DRE-101": "not-verified", "DRE-102": "not-verified", "DRE-103": "excluded"}, ()),
], ids=["empty-proposal", "no-record-at-all", "every-leg-unverified-or-excluded"])
def test_a_run_that_checked_no_case_fails_saying_so(ends, no_record, tmp_path, capsys):
    """A verify run that exited before checking anything is not a pass."""
    proposal = _proposal(tmp_path, ends, no_record=no_record)
    code, lines, doc = _judge(tmp_path, proposal, capsys)
    assert code == 1
    for case_id in ("c-planning", "c-cancel", "c-none"):
        assert " not judged" in _line(lines, case_id)
    assert lines[-3] == f"{TAG} 3 cases — 0 pass, 0 fail, 3 not judged, 0 regressed"
    assert lines[-2].startswith(f"{TAG} failed closed — 1 case passes on main and "
                                "was not checked: c-planning (DRE-101, ")
    assert lines[-1] == (f"{TAG} failed closed — no case was checked: the replay "
                         "judged none of the 3 cases")
    assert doc["checked"] == 0 and doc["unchecked"] == ["c-planning"]


def test_no_case_checked_fails_even_when_no_case_passes_on_main(tmp_path, capsys):
    cases = [_case("c-cancel", "DRE-102", "off-cancel", False),
             _case("c-none", "DRE-103", "off-planning", None)]
    proposal = _proposal(tmp_path, {"DRE-102": "not-verified"},
                         reasons={"DRE-102": gva.STEP_FAILED})
    out = tmp_path / "judged.json"
    code = gate.main(["judge", "--proposal", str(proposal),
                      "--cases", str(_cases_file(tmp_path, cases)), "--out", str(out)])
    lines = capsys.readouterr().out.strip().splitlines()
    assert code == 1
    assert lines[-1] == (f"{TAG} failed closed — no case was checked: the replay "
                         "judged none of the 2 cases")
    assert json.loads(out.read_text())["unchecked"] == []


def test_one_checked_case_beside_an_unchecked_passing_one_still_fails(tmp_path, capsys):
    cases = [_case("c-planning", "DRE-101", "off-planning", True),
             _case("c-kept", "DRE-104", "off-planning", True)]
    proposal = _proposal(tmp_path, {"DRE-101": "cancel", "DRE-104": "not-verified"},
                         reasons={"DRE-104": gva.STEP_FAILED})
    out = tmp_path / "judged.json"
    code = gate.main(["judge", "--proposal", str(proposal),
                      "--cases", str(_cases_file(tmp_path, cases)), "--out", str(out)])
    lines = capsys.readouterr().out.strip().splitlines()
    assert code == 1
    assert _line(lines, "c-planning").endswith(" pass")
    assert lines[-1] == (f"{TAG} failed closed — 1 case passes on main and was not "
                         "checked: c-kept (DRE-104, unverified (agent step failed))")
    doc = json.loads(out.read_text())
    assert doc["checked"] == 1 and doc["unchecked"] == ["c-kept"]


def test_a_regressed_case_beside_an_unchecked_one_says_both(tmp_path, capsys):
    cases = [_case("c-planning", "DRE-101", "off-planning", True),
             _case("c-kept", "DRE-104", "off-planning", True)]
    proposal = _proposal(tmp_path, {"DRE-101": "planning", "DRE-104": "not-verified"},
                         reasons={"DRE-104": gva.STEP_FAILED})
    out = tmp_path / "judged.json"
    code = gate.main(["judge", "--proposal", str(proposal),
                      "--cases", str(_cases_file(tmp_path, cases)), "--out", str(out)])
    lines = capsys.readouterr().out.strip().splitlines()
    assert code == 1
    assert _line(lines, "c-planning").endswith(" fail — REGRESSED")
    assert lines[-1].startswith(f"{TAG} failed closed — 1 case passes on main")
    doc = json.loads(out.read_text())
    assert doc["regressed"] == ["c-planning"] and doc["unchecked"] == ["c-kept"]


def test_the_gate_fails_closed_over_the_cases_file_as_it_ships(tmp_path, capsys):
    """The run's own shape: the shipped cases, of which only the seed passes
    on `main`, and a replay whose one leg crashed on startup."""
    shipped = json.loads((ROOT / grc.CASES_PATH).read_text(encoding="utf-8"))
    seed = next(c for c in shipped["cases"] if c["case"] == "seed-DRE-7239")
    seed["main"] = {"ended": "cancel", "at": AT, "passes": True}
    for case in shipped["cases"]:
        if case is not seed:
            case["main"] = {"ended": None, "at": None, "passes": None}
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(shipped), encoding="utf-8")
    proposal = _proposal(tmp_path, {"DRE-7239": "not-verified"},
                         reasons={"DRE-7239": gva.STEP_FAILED})
    code = gate.main(["judge", "--proposal", str(proposal), "--cases", str(path)])
    said = capsys.readouterr().out
    assert code == 1
    assert (f"{TAG} seed-DRE-7239 expect=off-planning main=pass head=not-verified "
            "search=none not judged — UNCHECKED: unverified (agent step failed)") in said
    assert "seed-DRE-7239 (DRE-7239, unverified (agent step failed))" in said
    assert "no case was checked" in said
    assert "rate-limited" not in said


def test_the_throttled_run_over_the_cases_file_as_it_ships(tmp_path, capsys):
    """Gate run 41439594582's shape: the seed case's card unverified because
    the lookup leg was rate limited (DRE-8859)."""
    shipped = json.loads((ROOT / grc.CASES_PATH).read_text(encoding="utf-8"))
    for case in shipped["cases"]:
        case["main"] = {"ended": None, "at": None, "passes": None}
        if case["case"] == "seed-DRE-7239":
            case["main"] = {"ended": "cancel", "at": AT, "passes": True}
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(shipped), encoding="utf-8")
    proposal = _proposal(tmp_path, {"DRE-7239": "not-verified"},
                         reasons={"DRE-7239": LIMITED})
    out = tmp_path / "judged.json"
    code = gate.main(["judge", "--proposal", str(proposal), "--cases", str(path),
                      "--out", str(out)])
    said = capsys.readouterr().out
    assert code == 1
    assert (f"{TAG} failed closed — rate-limited: could not read "
            f"dreadnought-foundry/bureau-pipeline for seed-DRE-7239 (DRE-7239), "
            f"{RERUN}") in said
    assert "no case was checked" in said
    assert json.loads(out.read_text())["rate_limited"] == ["seed-DRE-7239"]


def test_a_card_missing_from_the_proposal_is_head_none(tmp_path, capsys):
    proposal = _proposal(tmp_path, {"DRE-101": "cancel"})
    _, lines, _ = _judge(tmp_path, proposal, capsys)
    assert _line(lines, "c-none") == (
        f"{TAG} c-none expect=off-planning main=none head=none search=none not judged")


def test_the_out_file_carries_every_case(tmp_path, capsys):
    proposal = _proposal(tmp_path, {"DRE-101": "planning", "DRE-102": "planning"})
    _, _, doc = _judge(tmp_path, proposal, capsys)
    assert set(doc) == {"cases", "regressed", "now_passes", "not_judged",
                        "unchecked", "rate_limited", "checked"}
    assert [c["case"] for c in doc["cases"]] == ["c-planning", "c-cancel", "c-none"]
    first = doc["cases"][0]
    assert first["card"] == "DRE-101" and first["expect"] == "off-planning"
    assert first["main"] == "pass" and first["head"] == "planning"
    assert first["result"] == "fail" and first["regressed"] is True
    assert first["search"] == "none"
    assert doc["cases"][2]["head"] is None and doc["cases"][2]["result"] == "not judged"


# --------------------------------------------------------------------------
# judge --targets — which search each case was judged on
# --------------------------------------------------------------------------
@pytest.mark.parametrize("ends", [
    {"DRE-101": "planning", "DRE-102": "cancel", "DRE-103": "planning"},
    {"DRE-101": "cancel", "DRE-102": "planning", "DRE-103": "rewrite"},
])
def test_the_search_is_reported_and_never_changes_a_verdict(ends, tmp_path, capsys):
    proposal = _proposal(tmp_path, ends)
    without = _judge(tmp_path, proposal, capsys)
    targets = _targets(tmp_path, {"DRE-101": "linear", "DRE-102": "door"})
    with_ = _judge(tmp_path, proposal, capsys, "--targets", str(targets))

    assert with_[0] == without[0]
    for case_id, source in (("c-planning", "linear"), ("c-cancel", "door"),
                            ("c-none", "none")):
        assert f" search={source} " in _line(with_[1], case_id)
        assert " search=none " in _line(without[1], case_id)
        assert (_line(with_[1], case_id).replace(f"search={source}", "search=none")
                == _line(without[1], case_id))
    assert [c["search"] for c in with_[2]["cases"]] == ["linear", "door", "none"]
    assert [c["search"] for c in without[2]["cases"]] == ["none", "none", "none"]
    for key in ("regressed", "now_passes", "not_judged"):
        assert with_[2][key] == without[2][key]
    assert with_[1][-1] == without[1][-1]


def test_an_unsearched_row_is_search_none(tmp_path, capsys):
    proposal = _proposal(tmp_path, {"DRE-101": "cancel", "DRE-102": "cancel"})
    targets = _targets(tmp_path, {"DRE-101": None})
    _, lines, _ = _judge(tmp_path, proposal, capsys, "--targets", str(targets))
    assert " search=none " in _line(lines, "c-planning")


def test_an_unreadable_targets_file_is_search_none_and_the_same_verdict(tmp_path, capsys):
    proposal = _proposal(tmp_path, {"DRE-101": "planning", "DRE-102": "planning"})
    bad = tmp_path / "verify-targets.json"
    bad.write_text("{", encoding="utf-8")
    out = tmp_path / "judged.json"
    code = gate.main(["judge", "--proposal", str(proposal), "--targets", str(bad),
                      "--cases", str(_cases_file(tmp_path)), "--out", str(out)])
    said = capsys.readouterr()
    assert code == 1
    assert "could not be read" in said.err and said.err.startswith(TAG)
    assert [c["search"] for c in json.loads(out.read_text())["cases"]] == ["none"] * 3


def test_an_unreadable_proposal_is_exit_2(tmp_path, capsys):
    bad = tmp_path / "proposal-verified.json"
    bad.write_text("{", encoding="utf-8")
    assert gate.main(["judge", "--proposal", str(bad),
                      "--cases", str(_cases_file(tmp_path))]) == 2
    assert capsys.readouterr().err.startswith(TAG)


# --------------------------------------------------------------------------
# record-main — the only writer of `main`
# --------------------------------------------------------------------------
def test_record_main_writes_main_and_nothing_else(tmp_path, capsys):
    cases = [_case("c-planning", "DRE-101", "off-planning", None),
             _case("c-cancel", "DRE-102", "off-cancel", True),
             _case("c-none", "DRE-103", "off-planning", True)]
    cases[2]["main"] = {"ended": "cancel", "at": "2026-10-25T15:00:00Z", "passes": True}
    path = _cases_file(tmp_path, cases)
    before = json.loads(path.read_text(encoding="utf-8"))
    proposal = _proposal(tmp_path, {"DRE-101": "cancel", "DRE-102": "cancel",
                                    "DRE-103": "planning"}, no_record=("DRE-103",))

    code = gate.main(["record-main", "--proposal", str(proposal), "--at", AT,
                      "--cases", str(path)])
    lines = capsys.readouterr().out.strip().splitlines()
    assert code == 0
    after = json.loads(path.read_text(encoding="utf-8"))

    assert [c["main"] for c in after["cases"]] == [
        {"ended": "cancel", "at": AT, "passes": True},
        {"ended": "cancel", "at": AT, "passes": False},
        {"ended": "planning", "at": AT, "passes": None},
    ]
    for old, new in zip(before["cases"], after["cases"]):
        assert list(new) == list(old)
        assert {k: v for k, v in new.items() if k != "main"} == \
               {k: v for k, v in old.items() if k != "main"}
    assert {k: v for k, v in after.items() if k != "cases"} == \
           {k: v for k, v in before.items() if k != "cases"}

    assert lines[-1].startswith(f"{TAG} 3 cases — ")
    assert len(lines) == 4


def test_record_main_leaves_the_written_file_byte_for_byte_but_main(tmp_path):
    cases = [_case("c-planning", "DRE-101", "off-planning", None)]
    path = _cases_file(tmp_path, cases)
    original = path.read_text(encoding="utf-8")
    proposal = _proposal(tmp_path, {"DRE-101": "cancel"})
    assert gate.main(["record-main", "--proposal", str(proposal), "--at", AT,
                      "--cases", str(path)]) == 0
    written = path.read_text(encoding="utf-8")
    expected = original.replace(
        '"main": {\n        "ended": null,\n        "at": null,\n        "passes": null\n      }',
        f'"main": {{\n        "ended": "cancel",\n        "at": "{AT}",\n'
        '        "passes": true\n      }')
    assert expected != original
    assert written == expected


def test_record_main_never_exits_1_even_on_a_regression(tmp_path, capsys):
    cases = [_case("c-planning", "DRE-101", "off-planning", True)]
    path = _cases_file(tmp_path, cases)
    proposal = _proposal(tmp_path, {"DRE-101": "planning"})
    assert gate.main(["record-main", "--proposal", str(proposal), "--at", AT,
                      "--cases", str(path)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].endswith("REGRESSED")
    assert json.loads(path.read_text())["cases"][0]["main"]["passes"] is False


def test_record_main_on_an_unjudged_card_keeps_no_earlier_block(tmp_path):
    cases = [_case("c-planning", "DRE-101", "off-planning", True)]
    cases[0]["main"] = {"ended": "cancel", "at": "2026-10-18T15:00:00Z", "passes": True}
    path = _cases_file(tmp_path, cases)
    proposal = _proposal(tmp_path, {"DRE-101": "excluded"})
    assert gate.main(["record-main", "--proposal", str(proposal), "--at", AT,
                      "--cases", str(path)]) == 0
    assert json.loads(path.read_text())["cases"][0]["main"] == {
        "ended": "excluded", "at": AT, "passes": None}
