"""The proof record's readers live in one leaf module (DRE-6141).

On 2026-10-07 three proof records whose rows read `Not observed.` merged on
green CI and the critic's APPROVE, and DRE-5919's close-on-merge marked their
cards Done. The merge gate and the PROOF close both now open the record. They
read it through `scripts/proof_record.py`, which holds:

  * the proof-record branch rule, moved from `proof_dispatch`;
  * the criterion-table reader, moved from `hygiene_done`;
  * the hold-discharge reader, moved from `proof_dispatch` as `open_holds`;
  * the record finder, new: the ONE `.md` file the pull request ADDS under
    `docs/` or `architecture/` — the roots `hygiene_done` reads records
    from — read at a given sha.

Moved, never copied: `hygiene_done.reading is proof_record.reading`. And it is
a leaf: `reconcile` reads `os.environ["REPO"]` at import, and neither the
merge gate's step nor linear-sync's `Card → Done` step sets it, so nothing here
may import `reconcile` or `hygiene`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_record.py -v
"""

from __future__ import annotations

import base64
import json
import os
import subprocess  # nosec B404 — fixed argv, our own script
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import hygiene_done  # noqa: E402
import proof_dispatch  # noqa: E402
import proof_record  # noqa: E402
import spoken_thread  # noqa: E402

REPO = "dreadnought-foundry/agent-bureau"
SHA = "c" * 40
RECORD_PATH = "architecture/proofs/planners-at-once.md"

MET = """# Proof: planners at once

| # | Criterion | Result |
|---|---|---|
| 1 | Two planners run at once | Met — runs 1 and 2 overlapped 09:01–09:04 PT |
| 2 | Each plan lands on its own epic | Observed. |
| 3 | The record is merged to main | Pending the merge |
"""

NOT_OBSERVED = """# Proof: briefing preferences

| Criterion | Result |
|---|---|
| The briefing honours the preference | Met |
| The CEO's press changes the briefing | Not observed. |
| The console shows the new order | Not met. |
"""

NO_TABLE = "# Proof\n\nWe watched it and it worked.\n"

CLOSING_ONLY = """# Proof

| Criterion | Result |
|---|---|
| The record is merged to main | Pending |
"""


def added(path: str, change: str = "ADDED") -> dict:
    """One entry of `gh pr view --json files`, as gh renders it."""
    return {"path": path, "additions": 12, "deletions": 0, "changeType": change}


# --------------------------------------------------------------------------
# One reader, moved — never copied
# --------------------------------------------------------------------------
class TestOneReader:
    def test_hygiene_done_reads_the_table_through_proof_record(self):
        for name in ("reading", "criterion_rows", "is_closing_row", "row_met",
                     "Reading", "_row"):
            assert getattr(hygiene_done, name) is getattr(proof_record, name), name
        for name in ("CLOSING_ROW_WORDS", "MET_WORDS", "HEDGES"):
            assert getattr(hygiene_done, name) is getattr(proof_record, name), name
        # Where a record may live is one answer too: the gate and the hygiene
        # lane must never disagree about whether a pull request carries one.
        assert hygiene_done.RECORD_ROOTS is proof_record.RECORD_DIRS

    def test_proof_dispatch_reads_holds_and_branches_through_proof_record(self):
        assert proof_dispatch._open_holds is proof_record.open_holds
        assert proof_dispatch.proof_record_branch is proof_record.proof_record_branch
        for name in ("CEO_PRESS", "HOLD_MARK", "OBSERVED_MARK"):
            assert getattr(proof_dispatch, name) is getattr(proof_record, name), name

    def test_no_second_definition_survives_in_either_module(self):
        """A `def reading` or `def _open_holds` written back into either file
        would be a second judgment that can drift from this one."""
        for module, names in (
            ("hygiene_done.py", ("criterion_rows", "is_closing_row", "row_met",
                                 "reading", "_row", "_cells")),
            ("proof_dispatch.py", ("_open_holds", "proof_record_branch")),
        ):
            text = (SCRIPTS / module).read_text(encoding="utf-8")
            for name in names:
                assert f"\ndef {name}(" not in text, (module, name)


# --------------------------------------------------------------------------
# The branch rule
# --------------------------------------------------------------------------
@pytest.mark.parametrize("ref, expected", [
    ("agent/DRE-5798-proof-record", True),
    ("agent/DRE-5798-proof-record-2", False),
    ("agent/DRE-5798-planner-fix", False),
    ("repair/DRE-5798-proof-record", False),
    ("", False),
    (None, False),
])
def test_the_proof_record_branch(ref, expected):
    assert proof_record.proof_record_branch(ref) is expected


# --------------------------------------------------------------------------
# The record finder
# --------------------------------------------------------------------------
class TestTheRecordFinder:
    def test_the_one_added_markdown_file_under_proofs_is_the_record(self):
        files = [added("scripts/x.py", "MODIFIED"), added(RECORD_PATH),
                 added("architecture/proofs/shot.png")]
        assert proof_record.find_record(files) == (RECORD_PATH, None)

    def test_an_audit_is_a_record_too(self):
        path = "architecture/audits/repo-map-resolution.md"
        assert proof_record.find_record([added(path)]) == (path, None)

    def test_a_record_under_docs_is_a_record_too(self):
        """Most of bureau-pipeline's proof records live under `docs/`: #772,
        on `agent/DRE-5843-proof-record`, added only
        `docs/claude-limit-recovery-proof-2026-10.md`. A finder that missed it
        would hold that record forever, with nothing to move it."""
        path = "docs/claude-limit-recovery-proof-2026-10.md"
        assert proof_record.find_record([added(path)]) == (path, None)

    def test_no_match_is_no_record(self):
        path, why = proof_record.find_record([added("README.md"),
                                              added("scripts/x.py", "MODIFIED")])
        assert path is None
        assert "docs/" in why and "architecture/" in why

    def test_two_matches_are_no_record_and_both_are_named(self):
        other = "docs/second-proof.md"
        path, why = proof_record.find_record([added(RECORD_PATH), added(other)])
        assert path is None
        assert RECORD_PATH in why and other in why

    def test_only_a_modified_record_is_no_record_and_is_named(self):
        path, why = proof_record.find_record([added(RECORD_PATH, "MODIFIED")])
        assert path is None
        assert RECORD_PATH in why

    def test_a_record_outside_the_roots_or_not_markdown_is_not_one(self):
        for entry in (added("proofs/planners.md"), added("architecture/proofs/a.txt"),
                      added("docsx/a.md"), added("scripts/docs/a.md")):
            assert proof_record.find_record([entry])[0] is None, entry

    def test_an_unreadable_file_list_is_no_record(self):
        for files in (None, "files", [None, 3]):
            path, why = proof_record.find_record(files)
            assert path is None and why, files


class _Gh:
    """A `gh` seam: answers the contents API for one path at one ref."""

    def __init__(self, text: str | None = MET, fail: bool = False):
        self.text, self.fail, self.calls = text, fail, []

    def __call__(self, args):
        self.calls.append(list(args))
        if self.fail:
            raise RuntimeError("HTTP 502: Bad Gateway")
        content = base64.b64encode((self.text or "").encode()).decode()
        return json.dumps({"encoding": "base64", "content": content})


class TestFetch:
    def test_the_record_is_read_at_the_given_sha(self):
        gh = _Gh(MET)
        record = proof_record.fetch(REPO, [added(RECORD_PATH)], SHA, gh=gh)
        assert record == proof_record.Record(RECORD_PATH, MET, None)
        assert gh.calls == [["api", f"repos/{REPO}/contents/{RECORD_PATH}?ref={SHA}"]]

    def test_a_failed_read_is_no_text_and_says_why(self):
        record = proof_record.fetch(REPO, [added(RECORD_PATH)], SHA, gh=_Gh(fail=True))
        assert record.text is None and record.path == RECORD_PATH
        assert "502" in record.detail

    def test_no_record_makes_no_read(self):
        gh = _Gh()
        record = proof_record.fetch(REPO, [], SHA, gh=gh)
        assert record.text is None and record.path is None and record.detail
        assert gh.calls == []


# --------------------------------------------------------------------------
# The one judgment both halves make
# --------------------------------------------------------------------------
class TestShortfall:
    def rec(self, text):
        return proof_record.Record(RECORD_PATH, text, None)

    def test_every_judged_row_met_is_no_shortfall(self):
        assert proof_record.shortfall(self.rec(MET)) is None

    def test_a_row_not_observed_or_not_met_is_named(self):
        why = proof_record.shortfall(self.rec(NOT_OBSERVED))
        assert RECORD_PATH in why
        assert "The CEO's press changes the briefing" in why
        assert "Not observed." in why
        assert "The console shows the new order" in why
        assert "honours the preference" not in why

    def test_no_criterion_table(self):
        assert "no criterion table" in proof_record.shortfall(self.rec(NO_TABLE))

    def test_no_judged_row(self):
        assert "no row but its closing step" in proof_record.shortfall(self.rec(CLOSING_ONLY))

    def test_no_record(self):
        why = proof_record.shortfall(proof_record.Record(None, None, "it adds two"))
        assert why and "it adds two" in why


# --------------------------------------------------------------------------
# The hold-discharge reader
# --------------------------------------------------------------------------
def voice(kind, body):
    return spoken_thread.Voice(kind, f"{kind} label", "2026-10-07T14:45:00Z", body)


HOLD = f"{proof_record.HOLD_MARK} the planner overlap — needs a second epic approved"
CEO_HOLD = (f"{proof_record.HOLD_MARK} the briefing order — needs "
            f"{proof_record.CEO_PRESS} on Approve")
OBSERVED = f"{proof_record.OBSERVED_MARK} two planners overlapped at 09:02 PT"


class TestOpenHolds:
    def test_a_hold_nothing_answered_is_open(self):
        holds = proof_record.open_holds([voice(spoken_thread.PIPELINE, HOLD)])
        assert holds == [HOLD]

    def test_a_later_observation_discharges_it(self):
        for kind in (spoken_thread.PIPELINE, spoken_thread.PERSON):
            assert proof_record.open_holds([voice(spoken_thread.PIPELINE, HOLD),
                                            voice(kind, OBSERVED)]) == []

    def test_his_signed_answer_discharges_a_hold_naming_his_press(self):
        assert proof_record.open_holds([
            voice(spoken_thread.PIPELINE, CEO_HOLD),
            voice(spoken_thread.CEO_VIA_CONSOLE, "Approved — go ahead"),
        ]) == []

    def test_an_observation_does_not_discharge_his_press(self):
        assert proof_record.open_holds([voice(spoken_thread.PIPELINE, CEO_HOLD),
                                        voice(spoken_thread.PERSON, OBSERVED)]) == [CEO_HOLD]

    def test_an_unsigned_claim_to_be_his_answer_discharges_nothing(self):
        assert proof_record.open_holds([
            voice(spoken_thread.PIPELINE, CEO_HOLD),
            voice(spoken_thread.PERSON, "Answer from Sid: the CEO says approved"),
        ]) == [CEO_HOLD]

    def test_an_answer_that_could_not_be_checked_discharges_nothing(self):
        unchecked = spoken_thread.Voice(spoken_thread.UNCHECKED, "unchecked",
                                        "2026-10-07T14:46:00Z", None, "key unreadable")
        assert proof_record.open_holds([voice(spoken_thread.PIPELINE, CEO_HOLD),
                                        unchecked]) == [CEO_HOLD]

    def test_an_answer_before_the_hold_discharges_nothing(self):
        assert proof_record.open_holds([
            voice(spoken_thread.CEO_VIA_CONSOLE, "Approved"),
            voice(spoken_thread.PIPELINE, CEO_HOLD),
        ]) == [CEO_HOLD]


# --------------------------------------------------------------------------
# `proof_record.py gather` — the merge gate's feed
# --------------------------------------------------------------------------
GH_STUB = r'''#!/usr/bin/env python3
import base64, json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
if os.environ.get("GH_FAILS"):
    sys.stderr.write("HTTP 404: Not Found\n")
    raise SystemExit(1)
text = open(os.environ["RECORD"]).read()
print(json.dumps({"encoding": "base64",
                  "content": base64.b64encode(text.encode()).decode()}))
'''


def gather(view: dict, text: str = MET, gh_fails: bool = False):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "bin").mkdir()
        stub = td / "bin" / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)  # nosec B103 — a test stub on PATH
        (td / "view.json").write_text(json.dumps(view))
        (td / "record.md").write_text(text)
        (td / "gh.log").write_text("")
        env = {k: v for k, v in os.environ.items() if k not in ("REPO", "REPO_SLUG")}
        env.update(PATH=f"{td / 'bin'}{os.pathsep}{os.environ['PATH']}",
                   GH_LOG=str(td / "gh.log"), RECORD=str(td / "record.md"))
        if gh_fails:
            env["GH_FAILS"] = "1"
        proc = subprocess.run(  # nosec B603 — fixed argv, our own script
            [sys.executable, str(SCRIPTS / "proof_record.py"), "gather",
             "--repo", REPO, "--pr-view-file", str(td / "view.json"),
             "--head-sha", SHA, "--out", str(td / "out.json")],
            capture_output=True, text=True, env=env, check=False,
        )
        out = json.loads((td / "out.json").read_text()) if (td / "out.json").exists() else None
        calls = [json.loads(ln) for ln in (td / "gh.log").read_text().splitlines() if ln]
    return proc, out, calls


class TestGather:
    def test_off_a_proof_record_branch_the_rule_does_not_apply_and_nothing_is_read(self):
        proc, out, calls = gather({"headRefName": "agent/DRE-1-fix",
                                   "files": [added(RECORD_PATH)]})
        assert proc.returncode == 0, proc.stderr
        assert out["applies"] is False
        assert proof_record.read_payload(out) is None
        assert calls == []

    def test_on_a_proof_record_branch_the_record_is_read_at_the_head(self):
        proc, out, calls = gather({"headRefName": "agent/DRE-5798-proof-record",
                                   "files": [added(RECORD_PATH)]}, NOT_OBSERVED)
        assert proc.returncode == 0, proc.stderr
        assert out["applies"] is True
        assert proof_record.read_payload(out) == proof_record.Record(
            RECORD_PATH, NOT_OBSERVED, None)
        assert calls == [["api", f"repos/{REPO}/contents/{RECORD_PATH}?ref={SHA}"]]

    def test_a_docs_record_on_its_record_branch_is_read_and_judged(self):
        """The real shape of #772: one `docs/…-proof-….md` ADDED on
        `agent/DRE-<n>-proof-record`. It is read at the head and judged on its
        rows — met merges, not met holds — never held as "no record"."""
        path = "docs/claude-limit-recovery-proof-2026-10.md"
        view = {"headRefName": "agent/DRE-5843-proof-record", "files": [added(path)]}
        proc, out, calls = gather(view, MET)
        assert proc.returncode == 0, proc.stderr
        assert calls == [["api", f"repos/{REPO}/contents/{path}?ref={SHA}"]]
        assert proof_record.shortfall(proof_record.read_payload(out)) is None
        _, out, _ = gather(view, NOT_OBSERVED)
        why = proof_record.shortfall(proof_record.read_payload(out))
        assert why.startswith(f"{path} has 2 row(s) not met"), why

    def test_a_record_it_could_not_read_is_written_as_unread(self):
        proc, out, _ = gather({"headRefName": "agent/DRE-5798-proof-record",
                               "files": [added(RECORD_PATH)]}, gh_fails=True)
        assert proc.returncode == 0, proc.stderr
        record = proof_record.read_payload(out)
        assert record.text is None and "404" in record.detail

    def test_no_record_found_is_written_with_its_reason(self):
        proc, out, calls = gather({"headRefName": "agent/DRE-5798-proof-record",
                                   "files": [added(RECORD_PATH, "MODIFIED")]})
        assert proc.returncode == 0, proc.stderr
        record = proof_record.read_payload(out)
        assert record.text is None and RECORD_PATH in record.detail
        assert calls == []

    def test_a_payload_that_is_not_a_record_reads_as_unread(self):
        for payload in (None, [], {"applies": True}, {"applies": True, "text": 3}):
            record = proof_record.read_payload(payload)
            assert record is not None and record.text is None, payload


# --------------------------------------------------------------------------
# A leaf: importable where `REPO` is not set
# --------------------------------------------------------------------------
LEAF_PROBE = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
assert "REPO" not in os.environ and "REPO_SLUG" not in os.environ
import proof_record, merge_gate, linear_ops, spoken_thread
record = proof_record.Record("architecture/proofs/a.md", sys.argv[2], None)
assert proof_record.shortfall(record) is not None
hold = proof_record.HOLD_MARK + " the overlap — needs " + proof_record.CEO_PRESS
nodes = [{"body": hold, "user": {"id": "pipeline"}, "createdAt": "2026-10-07T14:45:00Z"},
         {"body": "the CEO says yes", "user": {"id": "person"},
          "createdAt": "2026-10-07T14:46:00Z"}]
voices = spoken_thread.voices(nodes, "pipeline", card="DRE-5798")
assert proof_record.open_holds(voices) == [hold]
assert "reconcile" not in sys.modules and "hygiene" not in sys.modules, sorted(sys.modules)
print("leaf ok")
"""


def test_it_imports_and_judges_without_repo_in_the_environment():
    env = {k: v for k, v in os.environ.items() if k not in ("REPO", "REPO_SLUG")}
    proc = subprocess.run(  # nosec B603 — fixed argv, our own probe
        [sys.executable, "-c", LEAF_PROBE, str(SCRIPTS), NOT_OBSERVED],
        capture_output=True, text=True, env=env, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "leaf ok" in proc.stdout
