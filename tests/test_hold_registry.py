"""RED-first tests: one registry of every `needs-human` writer (DRE-6173).

The label is written from fifteen sites in six files, and before this card
nothing listed them: the bare label was the whole record, so nothing could tell
a review cap from a dead-run cap, and nothing knew what would lift either.
`config/holds.json` is the list, `scripts/hold.py` is the one module that reads
and writes it, and this file is what keeps the list true.

WHAT THESE TESTS PIN.

  * The DISCOVERY is the test, not a list. `hold.discover()` reads the tree —
    Python by AST, workflows and shell by text, a moved workflow step read
    through `step_shell.workflow_source` — and every site it finds must match
    exactly one row by file, scope and anchor. Each widening of what it can
    see is proved by a fixture tree that holds the form, because "0 problems"
    is a fact about the scanner until a test shows the scanner can see it.
  * The ANCHOR is never the label-write line, so swapping `add_label` for
    `hold.apply` at a site — what the later cards of DRE-6172 do — changes no
    row. Proved on a copy of the real tree with every Python write swapped.
  * The VOCABULARY and the contract's lift kind per reason are fixed, and a
    row that names another lift kind is red by name.
  * The STAMP grammar, the spent-stamp rule and the lift reading are pure and
    pinned here with no network, so the hygiene card only consumes them.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hold_registry.py -v
"""
from __future__ import annotations

import copy
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import hold  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402

SHA = "a" * 40
OTHER_SHA = "b" * 40

REASONS = (
    "stranded-no-run", "no-route", "review-cap-spent", "dead-run-cap",
    "turn-cap-park", "epic-rereview-twice", "plan-critic-bound", "fix-dispute",
    "unfixable-check", "operator-step", "manual",
)
LIFT_KINDS = ("run-started", "repo-on-rail", "new-head", "unpark-marker",
              "blockers-terminal", "manual")
READERS = ("sweep", "fix-dispatch", "medic", "limit-recovery")
CONTRACT = {
    "stranded-no-run": "run-started",
    "no-route": "repo-on-rail",
    "review-cap-spent": "new-head",
    "fix-dispute": "new-head",
    "unfixable-check": "new-head",
    "dead-run-cap": "unpark-marker",
    "turn-cap-park": "unpark-marker",
    "epic-rereview-twice": "manual",
    "plan-critic-bound": "manual",
    "operator-step": "blockers-terminal",
    "manual": "manual",
}
NEW_HEAD = ("review-cap-spent", "fix-dispute", "unfixable-check")
NONE_QUALIFIED = tuple(r for r in REASONS if r not in NEW_HEAD and r != "no-route")
MANUAL_LIFT = ("manual", "epic-rereview-twice", "plan-critic-bound")
#: Reasons in the vocabulary whose writers land with later cards: the
#: operator step's create seam and its one-time pass (DRE-6429).
AWAITING_WRITERS = {"operator-step"}


def _real_doc() -> dict:
    with open(ROOT / "config" / "holds.json", encoding="utf-8") as fh:
        return json.load(fh)


def _stamp(reason: str, at: str = "none", by: str = "scripts/reconcile.py") -> str:
    return hold.stamp_line(reason, at, by)


# --------------------------------------------------------------------------- #
# the real tree                                                                #
# --------------------------------------------------------------------------- #


class TestTheRealTree:
    def test_the_registry_parses_and_check_is_clean(self):
        assert hold.problems() == []

    def test_check_exits_zero_from_the_command_line(self):
        out = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "hold.py"), "check"],
            capture_output=True, text=True, cwd=ROOT,
        )
        assert out.returncode == 0, out.stdout + out.stderr

    def test_fourteen_writer_sites_each_match_exactly_one_row(self):
        # Fifteen until DRE-6186 folded main()'s two dead-run caps into
        # `hand_dead_run_to_planner`, whose one `hold.apply` serves both.
        doc = _real_doc()
        sites = hold.discover()
        assert len(sites) == 14, [s.where for s in sites]
        for site in sites:
            hits = [r for r in doc["sites"] if hold.row_matches(r, site)]
            assert len(hits) == 1, f"{site.where} ({site.scope}) matches {len(hits)} rows"
        for row in doc["sites"]:
            hits = [s for s in sites if hold.row_matches(row, s)]
            assert len(hits) == 1, f"row {row['anchor']!r} matches {len(hits)} sites"

    def test_the_six_files_the_card_names_are_the_files_discovered(self):
        files = {s.file for s in hold.discover()}
        assert files == {
            "scripts/reconcile.py", "scripts/dead_run.py",
            "scripts/rereview_watch.py", ".github/workflows/plan.yml",
            ".github/workflows/agent-fix.yml",
            "scripts/model_adoption_actions.py",
        }

    def test_every_row_carries_reasons_lifts_readers_and_tried_first(self):
        for row in _real_doc()["sites"]:
            assert row["reasons"], row
            assert row["readers"], row
            for entry in row["reasons"]:
                assert entry["reason"] in REASONS
                assert entry["lifts"] == CONTRACT[entry["reason"]]
                tried = entry["tried_first"]
                if isinstance(tried, str):
                    assert tried.startswith("none — ")
                else:
                    assert tried["step"].strip() and tried["receipt"].strip()
            assert set(row["readers"]) <= set(READERS)

    def test_every_reason_in_the_vocabulary_has_a_writer(self):
        written = {e["reason"] for r in _real_doc()["sites"] for e in r["reasons"]}
        assert written <= set(REASONS)
        assert set(REASONS) - written <= AWAITING_WRITERS

    def test_the_vocabulary_is_the_contracts(self):
        assert tuple(hold.reasons()) == REASONS
        assert tuple(hold.lift_kinds()) == LIFT_KINDS
        assert tuple(hold.readers()) == READERS
        assert hold.CONTRACT_LIFTS == CONTRACT

    def test_no_anchor_is_a_label_write_line(self):
        for row in _real_doc()["sites"]:
            for word in ("add_label", "add-label", "hold.apply", "hold.py apply"):
                assert word not in row["anchor"], row

    def test_every_tried_first_receipt_occurs_in_the_pipeline(self):
        corpus = hold._receipt_corpus(str(ROOT))
        for row in _real_doc()["sites"]:
            for entry in row["reasons"]:
                tried = entry["tried_first"]
                if isinstance(tried, dict):
                    assert tried["receipt"] in corpus, tried

    def test_the_dead_split_mark(self):
        assert hold.DEAD_SPLIT_MARK == "✂️ dead-run-cap → Planning:"


class TestSwappingTheWriteKeepsTheRow:
    """Each Python site's write swapped for `hold.apply(…)` on a copy of the
    tree still matches the same row — what the later cards of DRE-6172 do."""

    def test_swap_every_python_write_for_hold_apply(self, tmp_path):
        shutil.copytree(ROOT / "scripts", tmp_path / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / ".github" / "workflows",
                        tmp_path / ".github" / "workflows")
        doc = _real_doc()
        real = hold.discover()
        before = {
            row["anchor"]: next(s for s in real if hold.row_matches(row, s))
            for row in doc["sites"]
        }
        calls = [s for s in real if s.file.endswith(".py") and s.kind == "call"]
        assert len(calls) == 5, [s.where for s in calls]  # 6 before DRE-6186
        by_file: dict = {}
        for site in calls:
            by_file.setdefault(site.file, []).append(site)
        for path, sites in by_file.items():
            raw = (tmp_path / path).read_bytes()
            lines = raw.splitlines(keepends=True)
            starts = [0]
            for line in lines:
                starts.append(starts[-1] + len(line))
            for site in sorted(sites, key=lambda s: (s.line, s.col), reverse=True):
                begin = starts[site.line - 1] + site.col
                end = starts[site.end_line - 1] + site.end_col
                raw = raw[:begin] + b'hold.apply(ident, "manual", "none", "x.py")' + raw[end:]
            (tmp_path / path).write_bytes(raw)
        assert hold.problems(doc, root=str(tmp_path)) == []
        swapped = hold.discover(root=str(tmp_path))
        assert sum(1 for s in swapped if "hold.apply(" in s.source) == 5
        for row in doc["sites"]:
            hits = [s for s in swapped if hold.row_matches(row, s)]
            assert len(hits) == 1
            assert (hits[0].file, hits[0].scope) == (
                before[row["anchor"]].file, before[row["anchor"]].scope)


# --------------------------------------------------------------------------- #
# fixture trees                                                                #
# --------------------------------------------------------------------------- #


def _tree(root: Path, files: dict) -> str:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / "scripts").mkdir(exist_ok=True)
    (root / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    return str(root)


def _doc(rows: list) -> dict:
    doc = copy.deepcopy(_real_doc())
    doc["sites"] = rows
    return doc


def _row(file: str, scope: str, anchor: str, reason: str = "manual",
         lifts: str | None = "manual", receipt: str | None = None) -> dict:
    entry = {"reason": reason}
    if lifts is not None:
        entry["lifts"] = lifts
    entry["tried_first"] = (
        {"step": "the pipeline tries first", "receipt": receipt}
        if receipt else "none — a fixture"
    )
    return {"file": file, "scope": scope, "anchor": anchor,
            "reasons": [entry], "readers": list(READERS)}


ONE_WRITER = '''
import linear_ops
HOLD_LABEL = "needs-human"

def stall(ident):
    print("a stall receipt for", ident)
    linear_ops.add_label(ident, HOLD_LABEL)
'''


class TestTheDiscoverySeesEveryForm:
    def _sites(self, tmp_path, files):
        return hold.discover(root=_tree(tmp_path, files))

    def test_add_label_with_each_label_spelling(self, tmp_path):
        sites = self._sites(tmp_path, {"scripts/a.py": '''
import linear_ops, dead_run
def one(i): linear_ops.add_label(i, HOLD_LABEL)
def two(i): add_label(i, dead_run.HOLD_LABEL)
def three(i): linear_ops.add_label(i, _HOLD_LABEL)
def four(i): linear_ops.add_label(i, NEEDS_HUMAN)
def five(i): linear_ops.add_label(i, "needs-human")
def other(i): linear_ops.add_label(i, "hand-built")
'''})
        assert sorted(s.scope for s in sites) == ["five", "four", "one", "three", "two"]

    def test_a_park_call_a_tuple_and_hold_apply(self, tmp_path):
        sites = self._sites(tmp_path, {
            "scripts/a.py": '''
import dead_run, hold
def parks(i): dead_run.park(i, has_label=None)
LABELS = ("needs-human", "no-code")
def lst(): return ["x", "needs-human"]
def applies(i): hold.apply(i, "manual", "none", "a.py")
''',
            "scripts/dead_run.py": '''
def park(identifier, **kw): return True
def _cmd_park(i): return park(i)
''',
        })
        assert sorted((s.file, s.scope) for s in sites) == [
            ("scripts/a.py", "<module>"), ("scripts/a.py", "applies"),
            ("scripts/a.py", "lst"), ("scripts/a.py", "parks"),
            ("scripts/dead_run.py", "_cmd_park"),
        ]

    def test_hold_py_itself_is_the_seam_not_a_site(self, tmp_path):
        sites = self._sites(tmp_path, {"scripts/hold.py": '''
import linear_ops
HOLD_LABEL = "needs-human"
def apply(card, reason, at, by):
    linear_ops.add_label(card, HOLD_LABEL)
'''})
        assert sites == []

    def test_workflow_and_shell_forms(self, tmp_path):
        sites = self._sites(tmp_path, {
            ".github/workflows/w.yml": '''jobs:
  j:
    steps:
      - name: Park it
        run: |
          # add-label "$EPIC" needs-human is only prose here
          python3 .bureau-pipeline/scripts/linear_ops.py add-label "$EPIC" needs-human
      - name: Create it held
        run: |
          python3 .bureau-pipeline/scripts/linear_ops.py create --label needs-human
      - name: Through the seam
        run: |
          python3 .bureau-pipeline/scripts/hold.py apply "$CARD" --reason manual --by w.yml
''',
            "scripts/loose.sh": '''#!/usr/bin/env bash
set -e
park_it() {
  python3 .bureau-pipeline/scripts/linear_ops.py add-label "$CARD" needs-human || true
}
''',
        })
        assert sorted((s.file, s.scope) for s in sites) == [
            (".github/workflows/w.yml", "Create it held"),
            (".github/workflows/w.yml", "Park it"),
            (".github/workflows/w.yml", "Through the seam"),
            ("scripts/loose.sh", "park_it"),
        ]

    def test_a_moved_step_is_read_through_its_workflow(self, tmp_path):
        sites = self._sites(tmp_path, {
            ".github/workflows/w.yml": '''jobs:
  j:
    steps:
      - name: Report
        run: bash .bureau-pipeline/scripts/report_x.sh
''',
            "scripts/report_x.sh": '''#!/usr/bin/env bash
set -e
park_for_human() {
  python3 .bureau-pipeline/scripts/linear_ops.py add-label "$CARD" needs-human || true
}
''',
        })
        assert [(s.file, s.scope) for s in sites] == [
            (".github/workflows/w.yml", "Report")]


class TestCheckGoesRed:
    def test_a_writer_with_no_row(self, tmp_path):
        root = _tree(tmp_path, {"scripts/a.py": ONE_WRITER})
        found = hold.problems(_doc([]), root=root)
        assert any("scripts/a.py:7" in p and "no row" in p for p in found), found

    def test_green_with_its_row(self, tmp_path):
        root = _tree(tmp_path, {"scripts/a.py": ONE_WRITER})
        assert hold.problems(_doc([_row("scripts/a.py", "stall", "a stall receipt")]),
                             root=root) == []

    def test_an_anchor_matching_nothing(self, tmp_path):
        root = _tree(tmp_path, {"scripts/a.py": ONE_WRITER})
        found = hold.problems(_doc([_row("scripts/a.py", "stall", "no such words")]),
                              root=root)
        assert any("'no such words'" in p and "matches 0" in p for p in found), found

    def test_an_anchor_matching_two_sites_in_one_scope(self, tmp_path):
        root = _tree(tmp_path, {"scripts/a.py": '''
import linear_ops
def stall(ident):
    linear_ops.add_label(ident, HOLD_LABEL)
    print("a stall receipt")
    linear_ops.add_label(ident, HOLD_LABEL)
'''})
        found = hold.problems(_doc([_row("scripts/a.py", "stall", "a stall receipt")]),
                              root=root)
        assert any("'a stall receipt'" in p and "matches 2" in p for p in found), found

    def test_an_anchor_matching_two_scopes_of_one_name(self, tmp_path):
        root = _tree(tmp_path, {"scripts/a.py": '''
import linear_ops
class A:
    def go(self, ident):
        print("held")
        linear_ops.add_label(ident, HOLD_LABEL)
class B:
    def go(self, ident):
        print("held")
        linear_ops.add_label(ident, HOLD_LABEL)
'''})
        found = hold.problems(_doc([_row("scripts/a.py", "go", "held")]), root=root)
        assert any("'held'" in p and "matches 2" in p for p in found), found

    def test_a_row_whose_site_no_longer_exists(self, tmp_path):
        root = _tree(tmp_path, {"scripts/a.py": ONE_WRITER})
        found = hold.problems(_doc([
            _row("scripts/a.py", "stall", "a stall receipt"),
            _row("scripts/gone.py", "vanished", "anything"),
        ]), root=root)
        assert any("scripts/gone.py" in p and "'anything'" in p for p in found), found

    def test_a_row_that_omits_its_lift_kind(self, tmp_path):
        root = _tree(tmp_path, {"scripts/a.py": ONE_WRITER})
        found = hold.problems(_doc([
            _row("scripts/a.py", "stall", "a stall receipt", lifts=None)]), root=root)
        assert any("'a stall receipt'" in p and "lift" in p for p in found), found

    @pytest.mark.parametrize("reason,wrong", [
        ("fix-dispute", "manual"), ("plan-critic-bound", "new-head")])
    def test_a_row_naming_another_lift_kind_than_the_contract(self, tmp_path, reason, wrong):
        root = _tree(tmp_path, {"scripts/a.py": ONE_WRITER, "scripts/r.py": "X = 'r'"})
        found = hold.problems(_doc([
            _row("scripts/a.py", "stall", "a stall receipt", reason=reason,
                 lifts=wrong, receipt="X = 'r'")]), root=root)
        assert any("'a stall receipt'" in p and reason in p and wrong in p
                   for p in found), found

    def test_a_tried_first_receipt_nothing_records(self, tmp_path):
        root = _tree(tmp_path, {"scripts/a.py": ONE_WRITER})
        found = hold.problems(_doc([
            _row("scripts/a.py", "stall", "a stall receipt",
                 receipt="a phrase nothing posts")]), root=root)
        assert any("a phrase nothing posts" in p for p in found), found

    def test_a_tried_first_receipt_in_a_workflow_counts(self, tmp_path):
        root = _tree(tmp_path, {
            "scripts/a.py": ONE_WRITER,
            ".github/workflows/w.yml": "x: 'a workflow phrase'\n",
        })
        assert hold.problems(_doc([
            _row("scripts/a.py", "stall", "a stall receipt",
                 receipt="a workflow phrase")]), root=root) == []

    def test_a_reason_reader_or_lift_outside_the_vocabulary(self, tmp_path):
        root = _tree(tmp_path, {"scripts/a.py": ONE_WRITER})
        row = _row("scripts/a.py", "stall", "a stall receipt")
        row["readers"] = ["sweep", "nobody"]
        row["reasons"].append({"reason": "whim", "lifts": "manual",
                               "tried_first": "none — x"})
        found = hold.problems(_doc([row]), root=root)
        assert any("'nobody'" in p for p in found), found
        assert any("'whim'" in p for p in found), found

    def test_a_row_with_no_readers_or_no_reasons(self, tmp_path):
        root = _tree(tmp_path, {"scripts/a.py": ONE_WRITER})
        row = _row("scripts/a.py", "stall", "a stall receipt")
        row["readers"] = []
        row["reasons"] = []
        found = hold.problems(_doc([row]), root=root)
        assert any("reader" in p for p in found), found
        assert any("reason" in p for p in found), found

    def test_an_anchor_that_is_the_label_write(self, tmp_path):
        root = _tree(tmp_path, {"scripts/a.py": ONE_WRITER})
        found = hold.problems(_doc([
            _row("scripts/a.py", "stall", "add_label(ident, HOLD_LABEL)")]), root=root)
        assert any("label-write" in p for p in found), found

    def test_a_bad_tried_first_string(self, tmp_path):
        root = _tree(tmp_path, {"scripts/a.py": ONE_WRITER})
        row = _row("scripts/a.py", "stall", "a stall receipt")
        row["reasons"][0]["tried_first"] = "nothing"
        found = hold.problems(_doc([row]), root=root)
        assert any("tried_first" in p for p in found), found

    def test_the_command_names_the_row(self, tmp_path, capsys):
        root = _tree(tmp_path, {"scripts/a.py": ONE_WRITER})
        doc = _doc([_row("scripts/a.py", "stall", "a stall receipt",
                         reason="fix-dispute", lifts="manual",
                         receipt="a stall receipt")])
        with mock.patch.object(hold, "load", return_value=doc), \
                mock.patch.object(hold, "ROOT", root):
            assert hold.main(["check"]) == 1
        assert "a stall receipt" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# the stamp                                                                    #
# --------------------------------------------------------------------------- #


class TestStampLine:
    def test_the_grammar_byte_for_byte(self):
        assert hold.stamp_line("review-cap-spent", SHA, "scripts/reconcile.py") == (
            f"🔒 hold: reason=review-cap-spent at={SHA} lifts=new-head "
            "by=scripts/reconcile.py"
        )
        assert hold.stamp_line("no-route", "repo:portico", "scripts/reconcile.py") == (
            "🔒 hold: reason=no-route at=repo:portico lifts=repo-on-rail "
            "by=scripts/reconcile.py"
        )
        assert hold.stamp_line("manual", "none", ".github/workflows/plan.yml") == (
            "🔒 hold: reason=manual at=none lifts=manual "
            "by=.github/workflows/plan.yml"
        )

    def test_the_lift_line_byte_for_byte(self):
        assert hold.lift_line("fix-dispute", "new-head", "scripts/hygiene.py") == (
            "🔓 hold lifted: reason=fix-dispute because=new-head by=scripts/hygiene.py"
        )

    @pytest.mark.parametrize("reason", NEW_HEAD)
    def test_a_new_head_reason_refuses_none(self, reason):
        with pytest.raises(ValueError):
            hold.stamp_line(reason, "none", "scripts/reconcile.py")
        with pytest.raises(ValueError):
            hold.stamp_line(reason, "abc1234", "scripts/reconcile.py")

    @pytest.mark.parametrize("at", ["none", SHA, "portico", "repo:"])
    def test_no_route_refuses_anything_but_a_repo_slug(self, at):
        with pytest.raises(ValueError):
            hold.stamp_line("no-route", at, "scripts/reconcile.py")

    def test_no_route_takes_repo_none(self):
        assert "at=repo:none" in hold.stamp_line("no-route", "repo:none", "scripts/r.py")

    @pytest.mark.parametrize("reason", NONE_QUALIFIED)
    def test_the_other_seven_refuse_anything_but_none(self, reason):
        assert len(NONE_QUALIFIED) == 7
        for at in (SHA, "repo:portico"):
            with pytest.raises(ValueError):
                hold.stamp_line(reason, at, "scripts/reconcile.py")

    def test_an_unknown_reason_is_refused(self):
        with pytest.raises(ValueError):
            hold.stamp_line("whim", "none", "scripts/reconcile.py")

    def test_neither_line_carries_a_key_some_reader_counts(self):
        doc = pipeline_act.load()
        keys = list(hold.FORBIDDEN) + [pipeline_act.tag(a, doc) for a in pipeline_act.acts(doc)]
        for need in ("budget exhausted", "holding for a human", "held-for-human",
                     "needs-human", "dead-run-requeue", "turn-exhaustion-requeue"):
            assert need in hold.FORBIDDEN
        writers = {r["file"] for r in _real_doc()["sites"]}
        for reason in REASONS:
            at = SHA if reason in NEW_HEAD else ("repo:portico" if reason == "no-route" else "none")
            for by in writers:
                line = hold.stamp_line(reason, at, by)
                for because in LIFT_KINDS + ("card-closed", "operator"):
                    lifted = hold.lift_line(reason, because, by)
                    for key in keys:
                        assert key not in line and key not in lifted, (key, line)

    def test_a_writer_file_that_smuggles_a_key_is_refused(self):
        with pytest.raises(ValueError):
            hold.stamp_line("manual", "none", "scripts/needs-human.py")


def _lifted(reason="manual", because="operator"):
    return hold.lift_line(reason, because, "scripts/linear_ops.py")


HYG_CLEARED = "🧹 the hold came off\n\n`hyg-hold-cleared` reason=x because=y"
RESET = "♻️ dead-run-budget-reset: un-parked by a human"


class TestReadStampSpentRule:
    @pytest.mark.parametrize("retiring", [_lifted(), HYG_CLEARED, RESET],
                             ids=["lift-line", "hyg-hold-cleared", "budget-reset"])
    def test_a_newer_retiring_line_spends_the_stamp(self, retiring):
        bodies = ["hello", _stamp("dead-run-cap"), "a person talks", retiring]
        assert hold.read_stamp(bodies) is None
        labels = ["needs-human", "repo:portico"]
        assert hold.reason_of(labels, bodies) == "manual"
        for reader in READERS:
            assert hold.respects(labels, bodies, reader) is True

    @pytest.mark.parametrize("retiring", [_lifted(), HYG_CLEARED, RESET],
                             ids=["lift-line", "hyg-hold-cleared", "budget-reset"])
    def test_an_older_retiring_line_leaves_it_live(self, retiring):
        bodies = [retiring, "talk", _stamp("dead-run-cap"), "later talk"]
        stamp = hold.read_stamp(bodies)
        assert stamp is not None and stamp["reason"] == "dead-run-cap"

    def test_a_fresh_stamp_after_the_retiring_line_is_live_again(self):
        bodies = [_stamp("review-cap-spent", SHA), HYG_CLEARED,
                  _stamp("review-cap-spent", OTHER_SHA)]
        stamp = hold.read_stamp(bodies)
        assert stamp == {
            "reason": "review-cap-spent", "at": OTHER_SHA, "lifts": "new-head",
            "by": "scripts/reconcile.py",
            "line": _stamp("review-cap-spent", OTHER_SHA),
        }

    def test_the_newest_stamp_wins(self):
        bodies = [_stamp("dead-run-cap"), _stamp("turn-cap-park")]
        assert hold.read_stamp(bodies)["reason"] == "turn-cap-park"

    def test_a_stamp_must_open_its_own_comment(self):
        quoted = "A person quoting it: " + _stamp("dead-run-cap")
        assert hold.read_stamp([quoted]) is None

    def test_no_comments(self):
        assert hold.read_stamp([]) is None
        assert hold.read_stamp(None) is None


class TestReasonOfAndRespects:
    def test_no_label_reads_none_and_is_respected_by_nobody(self):
        bodies = [_stamp("dead-run-cap")]
        assert hold.reason_of(["repo:portico"], bodies) is None
        for reader in READERS:
            assert hold.respects(["repo:portico"], bodies, reader) is False

    def test_a_label_with_no_stamp_is_manual_and_every_reader_holds(self):
        assert hold.reason_of(["needs-human"], ["talk"]) == "manual"
        for reader in READERS:
            assert hold.respects(["needs-human"], ["talk"], reader) is True

    def test_the_label_is_read_in_any_case_and_from_label_nodes(self):
        assert hold.reason_of(["Needs-Human"], []) == "manual"
        assert hold.reason_of([{"name": "needs-human"}], []) == "manual"

    def test_a_live_stamp_names_its_reason(self):
        bodies = [_stamp("fix-dispute", SHA)]
        assert hold.reason_of(["needs-human"], bodies) == "fix-dispute"

    def test_a_reason_outside_the_registry_fails_closed(self):
        bodies = ["🔒 hold: reason=whim at=none lifts=manual by=scripts/x.py"]
        assert hold.reason_of(["needs-human"], bodies) == "whim"
        for reader in READERS:
            assert hold.respects(["needs-human"], bodies, reader) is True

    def test_a_known_reason_answers_with_its_rows_readers(self):
        doc = _doc([_row("scripts/a.py", "stall", "x", reason="dead-run-cap",
                         lifts="unpark-marker")])
        doc["sites"][0]["readers"] = ["sweep"]
        bodies = [_stamp("dead-run-cap")]
        assert hold.respects(["needs-human"], bodies, "sweep", doc=doc) is True
        assert hold.respects(["needs-human"], bodies, "medic", doc=doc) is False

    def test_a_manual_hold_holds_a_reader_its_row_does_not_name(self):
        doc = _doc([_row("scripts/a.py", "stall", "x")])
        doc["sites"][0]["readers"] = ["sweep"]
        assert hold.respects(["needs-human"], ["talk"], "medic", doc=doc) is True

    def test_today_every_row_names_every_reader(self):
        for row in _real_doc()["sites"]:
            assert sorted(row["readers"]) == sorted(READERS), row


# --------------------------------------------------------------------------- #
# lift_due                                                                     #
# --------------------------------------------------------------------------- #


def _due(reason, at="none", *, lane="Backlog", labels=("needs-human",),
         pr_head=None, rail=("portico", "agent-bureau"), after=()):
    line = _stamp(reason, at)
    bodies = ["older", line, *after]
    return hold.lift_due(hold.read_stamp([line]), lane=lane, labels=list(labels),
                         pr_head=pr_head, rail_slugs=set(rail), bodies=bodies)


class TestLiftDue:
    @pytest.mark.parametrize("reason", NEW_HEAD)
    def test_new_head_only_when_the_head_moved(self, reason):
        assert _due(reason, SHA, pr_head=OTHER_SHA) == "new-head"
        assert _due(reason, SHA, pr_head=SHA) is None
        assert _due(reason, SHA, pr_head=None) is None
        assert _due(reason, SHA, pr_head="") is None

    def test_repo_on_rail_label_corrected(self):
        assert _due("no-route", "repo:legacy-site",
                    labels=("needs-human", "repo:portico")) == "repo-on-rail"

    def test_repo_on_rail_label_added_to_a_repo_none_card(self):
        assert _due("no-route", "repo:none",
                    labels=("needs-human", "repo:portico")) == "repo-on-rail"

    def test_repo_on_rail_label_unchanged_does_not_lift(self):
        assert _due("no-route", "repo:legacy-site",
                    labels=("needs-human", "repo:legacy-site")) is None

    def test_a_stamped_slug_now_on_the_rail_does_not_lift_an_off_rail_label(self):
        assert _due("no-route", "repo:portico",
                    labels=("needs-human", "repo:elsewhere")) is None

    def test_repo_on_rail_with_no_repo_label_does_not_lift(self):
        assert _due("no-route", "repo:portico", labels=("needs-human",)) is None

    def test_repo_on_rail_with_one_of_two_repo_labels_off_the_rail_does_not_lift(self):
        assert _due("no-route", "repo:elsewhere",
                    labels=("needs-human", "repo:portico", "repo:elsewhere")) is None

    def test_repo_on_rail_with_both_repo_labels_on_the_rail_lifts(self):
        assert _due("no-route", "repo:legacy-site",
                    labels=("needs-human", "repo:portico", "repo:agent-bureau")
                    ) == "repo-on-rail"

    @pytest.mark.parametrize("lane", ["Done", "Canceled"])
    @pytest.mark.parametrize("reason", REASONS)
    def test_card_closed_for_any_stamp(self, reason, lane):
        at = SHA if reason in NEW_HEAD else ("repo:x" if reason == "no-route" else "none")
        assert _due(reason, at, lane=lane) == "card-closed"

    def test_card_closed_with_no_stamp(self):
        assert hold.lift_due(None, lane="Done", labels=["needs-human"],
                             pr_head=None, rail_slugs=set(), bodies=[]) == "card-closed"
        assert hold.lift_due(None, lane="Todo", labels=["needs-human"],
                             pr_head=None, rail_slugs=set(), bodies=[]) is None

    @pytest.mark.parametrize("reason", ["dead-run-cap", "turn-cap-park"])
    def test_unpark_marker_only_when_newer_than_the_stamp(self, reason):
        assert _due(reason, after=[RESET]) == "unpark-marker"
        assert _due(reason, after=["talk"]) is None
        line = _stamp(reason)
        bodies = [RESET, line]
        assert hold.lift_due(hold.read_stamp([line]), lane="Backlog",
                             labels=["needs-human"], pr_head=None,
                             rail_slugs=set(), bodies=bodies) is None

    def test_run_started_by_a_newer_run_receipt(self):
        assert _due("stranded-no-run", after=["🧠 model-attempt: x"]) == "run-started"
        assert _due("stranded-no-run", after=["⏳ 1/5 plan"]) == "run-started"
        assert _due("stranded-no-run", after=["talk"]) is None
        line = _stamp("stranded-no-run")
        assert hold.lift_due(hold.read_stamp([line]), lane="Todo",
                             labels=["needs-human"], pr_head=None, rail_slugs=set(),
                             bodies=["🧠 model-attempt: old", line]) is None

    @pytest.mark.parametrize("reason", MANUAL_LIFT)
    def test_a_manual_hold_never_lifts_outside_done_or_canceled(self, reason):
        for lane in ("Triage", "Backlog", "In Review", "Planning", "Green Light"):
            assert _due(reason, lane=lane, pr_head=OTHER_SHA,
                        labels=("needs-human", "repo:portico"),
                        after=[RESET, "🧠 model-attempt", "⏳ 3/5"]) is None

    def test_an_unknown_reason_never_lifts_outside_done_or_canceled(self):
        stamp = {"reason": "whim", "at": "none", "lifts": "manual", "by": "x",
                 "line": "🔒 hold: reason=whim at=none lifts=manual by=x"}
        assert hold.lift_due(stamp, lane="Backlog", labels=["needs-human"],
                             pr_head=OTHER_SHA, rail_slugs={"x"},
                             bodies=[stamp["line"], RESET]) is None


# --------------------------------------------------------------------------- #
# operator-step: a person's step, lifted when its blockers are terminal        #
# --------------------------------------------------------------------------- #

OPERATOR_STAMP = ("🔒 hold: reason=operator-step at=none lifts=blockers-terminal "
                  "by=linear_ops.py")

OPERATOR_WRITER = '''
import hold

def file_step(ident):
    print("an operator step for", ident)
    hold.apply(ident, hold.OPERATOR_STEP_REASON, None, "linear_ops.py")
'''


class TestOperatorStep:
    """DRE-6426: the reason a planner-filed operator step carries, and its
    lift — every blocker terminal, read by the sweep's promotion gate."""

    def test_the_constants(self):
        assert hold.OPERATOR_STEP_REASON == "operator-step"
        assert hold.BLOCKERS_TERMINAL == "blockers-terminal"
        assert hold.OPERATOR_STEP_REASON in hold.REASONS
        assert hold.BLOCKERS_TERMINAL in hold.LIFT_KINDS
        assert hold.CONTRACT_LIFTS["operator-step"] == "blockers-terminal"
        assert hold.LIFTERS == {"operator-step": "sweep"}

    def test_the_stamp_byte_for_byte(self):
        assert hold.stamp_line("operator-step", None, "linear_ops.py") == OPERATOR_STAMP
        assert hold.stamp_line("operator-step", "none", "linear_ops.py") == OPERATOR_STAMP

    @pytest.mark.parametrize("at", ["abc", SHA, "repo:portico"])
    def test_the_stamp_refuses_any_qualifier_but_none(self, at):
        with pytest.raises(ValueError):
            hold.stamp_line("operator-step", at, "linear_ops.py")

    def test_the_lift_line_byte_for_byte(self):
        assert hold.lift_line(hold.OPERATOR_STEP_REASON, hold.BLOCKERS_TERMINAL,
                              "linear_ops.py") == (
            "🔓 hold lifted: reason=operator-step because=blockers-terminal "
            "by=linear_ops.py"
        )

    def test_reason_of_reads_the_stamp_and_manual_without_it(self):
        labels = ["needs-human", "no-code"]
        assert hold.reason_of(labels, ["talk", OPERATOR_STAMP]) == "operator-step"
        assert hold.reason_of(labels, ["talk"]) == "manual"

    def test_lift_due_never_lifts_it_outside_done_or_canceled(self):
        # The sweep's promotion gate is this reason's only lifter (DRE-6427):
        # it reads the blockers off the card's relations and calls hold.lift
        # itself. Neither caller of lift_due passes blockers, so lift_due
        # meeting every other kind's fact still answers None.
        stamp = hold.read_stamp([OPERATOR_STAMP])
        for lane in ("Backlog", "Todo", "Triage", "Hand-work", "In Review"):
            assert hold.lift_due(
                stamp, lane=lane, labels=["needs-human", "repo:portico"],
                pr_head=OTHER_SHA, rail_slugs={"portico"},
                bodies=[OPERATOR_STAMP, RESET, "🧠 model-attempt", "⏳ 1/5 plan"],
            ) is None

    @pytest.mark.parametrize("lane", ["Done", "Canceled"])
    def test_lift_due_answers_card_closed_in_a_closed_lane(self, lane):
        stamp = hold.read_stamp([OPERATOR_STAMP])
        assert hold.lift_due(stamp, lane=lane, labels=["needs-human"], pr_head=None,
                             rail_slugs=set(), bodies=[OPERATOR_STAMP]) == "card-closed"

    def test_lift_due_keeps_its_signature(self):
        params = inspect.signature(hold.lift_due).parameters
        assert [(p.name, p.kind) for p in params.values()] == [
            ("stamp", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            ("lane", inspect.Parameter.KEYWORD_ONLY),
            ("labels", inspect.Parameter.KEYWORD_ONLY),
            ("pr_head", inspect.Parameter.KEYWORD_ONLY),
            ("rail_slugs", inspect.Parameter.KEYWORD_ONLY),
            ("bodies", inspect.Parameter.KEYWORD_ONLY),
        ]
        assert all(p.default is inspect.Parameter.empty for p in params.values())

    def test_the_lift_due_docstring_says_the_sweep_lifts_it(self):
        doc = " ".join(hold.lift_due.__doc__.split())
        assert "blockers-terminal" in doc and "sweep" in doc

    def test_the_hygiene_lane_does_not_ask_lift_due_about_it(self):
        import hygiene_holds

        assert "blockers-terminal" not in hygiene_holds.OPEN_LIFTS

    def test_a_row_naming_the_sweep_as_a_reader_is_red_by_row(self, tmp_path):
        root = _tree(tmp_path, {"scripts/a.py": OPERATOR_WRITER})
        row = _row("scripts/a.py", "file_step", "an operator step",
                   reason="operator-step", lifts="blockers-terminal")
        found = hold.problems(_doc([row]), root=root)
        assert any("'an operator step'" in p and "sweep" in p for p in found), found

    def test_the_same_row_without_the_sweep_is_clean(self, tmp_path):
        root = _tree(tmp_path, {"scripts/a.py": OPERATOR_WRITER})
        row = _row("scripts/a.py", "file_step", "an operator step",
                   reason="operator-step", lifts="blockers-terminal")
        row["readers"] = ["fix-dispatch", "medic", "limit-recovery"]
        doc = _doc([row])
        assert hold.problems(doc, root=root) == []
        assert [s.scope for s in hold.discover(root=root)] == ["file_step"]
        bodies = [OPERATOR_STAMP]
        assert hold.respects(["needs-human"], bodies, "sweep", doc=doc) is False
        for reader in ("fix-dispatch", "medic", "limit-recovery"):
            assert hold.respects(["needs-human"], bodies, reader, doc=doc) is True

    def test_the_registry_file_mirrors_it(self):
        doc = _real_doc()
        entry = doc["reasons"]["operator-step"]
        assert entry["lifts"] == "blockers-terminal"
        assert entry["why"].strip()
        assert entry["lift_sends"] == "Backlog to Hand-work"
        kind = doc["lift_kinds"]["blockers-terminal"]
        for phrase in ("blockedBy", "Done", "Canceled", "Duplicate", "promotion gate"):
            assert phrase in kind, phrase
        assert "operator-step" in doc["readers"]["sweep"]
        assert "lifts" in doc["readers"]["sweep"]

    def test_the_page_lists_it(self):
        text = (ROOT / "docs" / "holds.md").read_text(encoding="utf-8")
        vocabulary = text.split("## The vocabulary", 1)[1].split("\n## ", 1)[0]
        reasons_item = vocabulary.split("- **Reasons:**", 1)[1].split("- **", 1)[0]
        kinds_item = vocabulary.split("- **Lift kinds:**", 1)[1].split("- **", 1)[0]
        assert "`operator-step`" in reasons_item
        assert "`blockers-terminal`" in kinds_item
        rows = [line for line in text.splitlines()
                if line.startswith("| `operator-step` | `blockers-terminal` |")]
        assert len(rows) == 1 and rows[0].rstrip(" |").endswith("Backlog → Hand-work"), rows
        assert "the sweep lifts `operator-step`" in " ".join(text.split())

    def test_the_page_counts_the_vocabulary_off_the_file(self):
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                 "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
                 "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15}
        text = (ROOT / "docs" / "holds.md").read_text(encoding="utf-8")
        match = re.search(r"(\w+) reasons, (\w+) lift kinds and (\w+) readers", text)
        assert match, "the vocabulary-count sentence is gone"
        counts = [words[w.lower()] for w in match.groups()]
        assert counts == [len(hold.reasons()), len(hold.lift_kinds()), len(hold.readers())]


# --------------------------------------------------------------------------- #
# the writes                                                                   #
# --------------------------------------------------------------------------- #


def _issue(labels, bodies):
    """The one read `hold` makes, shaped as Linear answers it (newest first)."""
    return {"issue": {
        "labels": {"nodes": [{"name": n} for n in labels]},
        "comments": {"pageInfo": {"hasNextPage": False},
                     "nodes": [{"body": b} for b in reversed(bodies)]},
    }}


class TestApplyAndLift:
    def test_apply_writes_the_label_then_the_stamp(self):
        calls = mock.Mock()
        with mock.patch.object(linear_ops, "add_label", calls.add_label), \
                mock.patch.object(linear_ops, "cmd_comment", calls.cmd_comment):
            hold.apply("DRE-1", "review-cap-spent", SHA, "scripts/reconcile.py")
        assert calls.mock_calls == [
            mock.call.add_label("DRE-1", "needs-human"),
            mock.call.cmd_comment(
                "DRE-1", f"🔒 hold: reason=review-cap-spent at={SHA} "
                "lifts=new-head by=scripts/reconcile.py"),
        ]

    def test_apply_refuses_a_bad_pairing_before_any_write(self):
        calls = mock.Mock()
        with mock.patch.object(linear_ops, "add_label", calls.add_label), \
                mock.patch.object(linear_ops, "cmd_comment", calls.cmd_comment):
            with pytest.raises(ValueError):
                hold.apply("DRE-1", "review-cap-spent", "none", "scripts/r.py")
        assert calls.mock_calls == []

    def test_lift_removes_the_label_then_posts_the_lift_line(self):
        calls = mock.Mock()
        calls.gql.return_value = _issue(["needs-human"], [_stamp("fix-dispute", SHA)])
        with mock.patch.object(linear_ops, "gql", calls.gql), \
                mock.patch.object(linear_ops, "remove_label", calls.remove_label), \
                mock.patch.object(linear_ops, "cmd_comment", calls.cmd_comment):
            hold.lift("DRE-1", "new-head", "scripts/hygiene.py")
        writes = [c for c in calls.mock_calls if c[0] != "gql"]
        assert writes == [
            mock.call.remove_label("DRE-1", "needs-human"),
            mock.call.cmd_comment(
                "DRE-1", "🔓 hold lifted: reason=fix-dispute because=new-head "
                "by=scripts/hygiene.py"),
        ]

    def test_lift_of_a_label_with_no_stamp_names_manual(self):
        calls = mock.Mock()
        calls.gql.return_value = _issue(["needs-human"], ["talk"])
        with mock.patch.object(linear_ops, "gql", calls.gql), \
                mock.patch.object(linear_ops, "remove_label", calls.remove_label), \
                mock.patch.object(linear_ops, "cmd_comment", calls.cmd_comment):
            hold.lift("DRE-1", "operator", "scripts/linear_ops.py")
        assert calls.cmd_comment.call_args == mock.call(
            "DRE-1", "🔓 hold lifted: reason=manual because=operator "
            "by=scripts/linear_ops.py")

    def test_lift_refuses_an_unknown_because(self):
        with pytest.raises(ValueError):
            hold.lift("DRE-1", "whim", "scripts/x.py", reason="manual")


class TestTheCommandLine:
    @pytest.mark.parametrize("argv", [
        ["--reason", "review-cap-spent", "--at", "none"],
        ["--reason", "fix-dispute"],
        ["--reason", "no-route", "--at", "portico"],
        ["--reason", "manual", "--at", SHA],
    ], ids=["new-head-none", "new-head-default", "no-route-bare", "manual-sha"])
    def test_apply_exits_2_before_any_write(self, argv):
        calls = mock.Mock()
        with mock.patch.object(linear_ops, "add_label", calls.add_label), \
                mock.patch.object(linear_ops, "cmd_comment", calls.cmd_comment):
            rc = hold.main(["apply", "DRE-1", *argv, "--by", "scripts/r.py"])
        assert rc == 2
        assert calls.mock_calls == []

    def test_apply_writes_on_good_input(self):
        calls = mock.Mock()
        with mock.patch.object(linear_ops, "add_label", calls.add_label), \
                mock.patch.object(linear_ops, "cmd_comment", calls.cmd_comment):
            rc = hold.main(["apply", "DRE-1", "--reason", "manual",
                            "--by", ".github/workflows/plan.yml"])
        assert rc == 0
        assert [c[0] for c in calls.mock_calls] == ["add_label", "cmd_comment"]

    def _reason(self, capsys, response=None, error=None):
        with mock.patch.object(linear_ops, "gql",
                               side_effect=error, return_value=response):
            rc = hold.main(["reason", "DRE-1"])
        return rc, capsys.readouterr().out

    def test_reason_prints_a_live_stamps_reason(self, capsys):
        rc, out = self._reason(capsys, _issue(
            ["needs-human"], [_stamp("review-cap-spent", SHA)]))
        assert (rc, out) == (0, "review-cap-spent\n")

    def test_reason_prints_manual_for_no_stamp(self, capsys):
        rc, out = self._reason(capsys, _issue(["needs-human"], ["talk"]))
        assert (rc, out) == (0, "manual\n")

    def test_reason_prints_manual_over_a_spent_stamp(self, capsys):
        rc, out = self._reason(capsys, _issue(
            ["needs-human"], [_stamp("review-cap-spent", SHA), HYG_CLEARED]))
        assert (rc, out) == (0, "manual\n")

    def test_reason_prints_nothing_with_the_label_off(self, capsys):
        rc, out = self._reason(capsys, _issue(
            ["repo:portico"], [_stamp("review-cap-spent", SHA)]))
        assert (rc, out) == (0, "")

    def test_reason_prints_nothing_when_the_read_fails(self, capsys):
        rc, out = self._reason(capsys, error=linear_ops.LinearError("down"))
        assert (rc, out) == (0, "")
        rc, out = self._reason(capsys, error=RuntimeError("anything"))
        assert (rc, out) == (0, "")

    def test_reason_reads_the_card_once(self):
        with mock.patch.object(linear_ops, "gql",
                               return_value=_issue(["needs-human"], [])) as gql:
            hold.main(["reason", "DRE-1"])
        assert gql.call_count == 1


# --------------------------------------------------------------------------- #
# the decisions this card holds                                                #
# --------------------------------------------------------------------------- #


class TestTheMergeGateNeverReadsTheHold:
    """The CEO's 2026-10-02 decision: `needs-human` does not block merging."""

    @pytest.mark.parametrize("name", ["merge_gate.py", "merge_sweep_gate.py"])
    def test_no_read_of_the_label(self, name):
        text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "HOLD_LABEL" not in text
        assert "needs-human" not in text


class TestTheRecordAroundIt:
    def test_hold_py_is_import_safe(self):
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'scripts'); import hold; "
             "print('linear_ops' in sys.modules)"],
            capture_output=True, text=True, cwd=ROOT,
            env={**os.environ, "LINEAR_API_KEY": ""},
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "False"

    def test_both_comment_writes_are_declared_not_an_act(self):
        rows = [d for d in pipeline_act.load()["unconverted"]
                if d.get("file") == "scripts/hold.py"]
        assert len(rows) == 2
        assert all(d["kind"] == "not-an-act" for d in rows)
        import check_act_receipts
        assert check_act_receipts.problems() == []

    def test_the_doc_names_every_site_and_reason(self):
        text = (ROOT / "docs" / "holds.md").read_text(encoding="utf-8")
        for row in _real_doc()["sites"]:
            assert row["scope"] in text and row["file"] in text, row
        for reason in REASONS:
            assert f"`{reason}`" in text
        for kind in LIFT_KINDS:
            assert f"`{kind}`" in text
        for phrase in ("config/repo-map.json", "hyg-hold-cleared",
                       "dead-run-budget-reset", "🔓 hold lifted:", "anchor"):
            assert phrase in text, phrase

    def test_the_config_readme_names_the_file(self):
        text = (ROOT / "config" / "README.md").read_text(encoding="utf-8")
        assert "holds.json" in text
