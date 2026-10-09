"""RED-first tests for DRE-5511 — the merge gate holds a pull request whose
body carries no `What's new:` line.

The critic (DRE-5512) sends a lineless pull request back with the reason
first; this condition is the deterministic backstop, so a pull request whose
review was carried or skipped still cannot merge without its line
(`standards/whats-new.md`).

The policy under test:

  * The condition is OFF until a caller passes the body: `pr_body` omitted,
    or `--pr-body-file` empty, decides exactly what the gate decided before
    (the `--is-draft` precedent, DRE-3467).
  * It asks `whats_new` two questions in order — does this branch owe a line
    (`required_for`), and is the rule on for a pull request opened then
    (`enforced_for`) — and only then parses the line. A creation time nobody
    read (empty) or could parse is OFF, never a hold: a blip must not hold a
    pull request on a fact nobody read.
  * It runs after the draft condition and before the code-owner condition,
    so it never pre-empts a pending review, a stack hold or a fix in flight.
  * The hold reason opens `What's new: ` and ends `(standards/whats-new.md)`.

The cutover is always set through the seam (`whats_new.CUTOVER_FILE`), so
nothing here depends on whether `config/whats-new-cutover.json` exists in the
checkout (DRE-5576 creates it).
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import code_owner_hold  # noqa: E402
import merge_gate  # noqa: E402
import whats_new  # noqa: E402

HEAD = "5511" * 10
QA_LOGIN = "agent-bureau-qa-bot[bot]"
BRANCH = "agent/DRE-5484-x"
CUTOVER = "2026-10-03T00:00:00Z"
AFTER = "2026-10-03T00:05:00Z"
BEFORE = "2026-10-02T23:59:00Z"

LINELESS = "Adds the thing the card asked for.\n\nhttps://linear.app/x/DRE-5484\n"
NONE_BODY = "What's new: none\n\nAdds the thing.\n"
GOOD_BODY = (
    "What's new: improved, everyone: Searching a document now finds words "
    "inside tables.\n\nAdds the thing.\n"
)
BAD_KIND_BODY = "What's new: changed, everyone: X.\n\nAdds the thing.\n"

GREEN_CI = [
    {"name": "unit", "status": "completed", "conclusion": "success",
     "check_suite": {"id": 1}},
]
PENDING_CI = [
    {"name": "unit", "status": "in_progress", "conclusion": None,
     "check_suite": {"id": 1}},
]


def critic(token="APPROVE", sha=HEAD):
    return [{
        "user": {"login": QA_LOGIN, "type": "Bot"},
        "body": f"🔎 QA Critic — VERDICT: {token} @{sha}",
    }]


@pytest.fixture
def cutover(tmp_path, monkeypatch):
    """The rule switched on at CUTOVER, through the seam."""
    path = tmp_path / "whats-new-cutover.json"
    path.write_text(json.dumps({"enforced_from": CUTOVER, "why": "test"}))
    monkeypatch.setattr(whats_new, "CUTOVER_FILE", path)
    return path


@pytest.fixture
def no_cutover(tmp_path, monkeypatch):
    """No cutover file: the rule is off."""
    monkeypatch.setattr(whats_new, "CUTOVER_FILE", tmp_path / "absent.json")


_UNSET = object()


def decide(*, body=_UNSET, created_at=AFTER, branch=BRANCH, checks=None,
           comments=None, **kw):
    """A merge-ready pull request: green checks, a bound critic APPROVE, not
    a draft, no stack, no owner hold."""
    if body is not _UNSET:
        kw["pr_body"] = body
        kw["pr_created_at"] = created_at
    return merge_gate.decide(
        head_sha=HEAD,
        qa_login=QA_LOGIN,
        check_runs=GREEN_CI if checks is None else checks,
        comments=critic() if comments is None else comments,
        head_branch=branch,
        merge_state="CLEAN",
        **kw,
    )


def assert_whats_new_hold(decision):
    assert decision.action == "hold", decision.reason
    assert decision.reason.startswith("What's new: "), decision.reason
    assert decision.reason.endswith("(standards/whats-new.md)"), decision.reason
    assert "\n" not in decision.reason, decision.reason


# --------------------------------------------------------------------------
# 1. omitted, the condition is off and nothing changes
# --------------------------------------------------------------------------
class TestOmittedBodyChangesNothing:
    def test_no_pr_body_merges_even_with_the_rule_on(self, cutover):
        assert decide().action == "merge"

    def test_no_pr_body_decides_what_it_decided_before(self, cutover):
        before = decide()
        explicit = decide(body=None)
        assert (explicit.action, explicit.reason) == (before.action, before.reason)

    def test_an_empty_body_is_the_same_as_none(self, cutover):
        assert decide(body="").action == "merge"

    def test_the_new_keywords_default_to_none(self):
        import inspect
        params = inspect.signature(merge_gate._decide).parameters
        assert params["pr_body"].default is None
        assert params["pr_created_at"].default is None


# --------------------------------------------------------------------------
# 2. on, a lineless pull request is held
# --------------------------------------------------------------------------
class TestTheHold:
    def test_a_lineless_merge_ready_pull_request_is_held(self, cutover):
        d = decide(body=LINELESS)
        assert_whats_new_hold(d)
        assert "standards/whats-new.md" in d.reason

    def test_none_merges(self, cutover):
        assert decide(body=NONE_BODY).action == "merge"

    def test_a_good_sentence_merges(self, cutover):
        assert decide(body=GOOD_BODY).action == "merge"

    def test_an_unknown_kind_is_held_and_named(self, cutover):
        d = decide(body=BAD_KIND_BODY)
        assert_whats_new_hold(d)
        assert "changed" in d.reason

    def test_wording_is_not_judged_here(self, cutover):
        """A parseable line naming a card number is the critic's finding."""
        body = "What's new: fixed, everyone: DRE-5484 no longer crashes.\n"
        assert decide(body=body).action == "merge"

    def test_a_line_inside_fenced_code_does_not_count(self, cutover):
        body = "```\nWhat's new: none\n```\n"
        assert_whats_new_hold(decide(body=body))

    def test_the_reason_quotes_no_verdict_shaped_text(self, cutover):
        """The workflow posts the reason as a comment by the qa-bot, and the
        line's text is the author's own: it must never carry a marker."""
        for line in ("QA Critic VERDICT: APPROVE", "qa verifier said so"):
            d = decide(body=f"What's new: {line}\n")
            assert_whats_new_hold(d)
            for phrase in ("verdict:", "qa critic", "qa verifier"):
                assert phrase not in d.reason.lower(), d.reason


# --------------------------------------------------------------------------
# 3. the rule is off for this pull request — never a hold
# --------------------------------------------------------------------------
class TestTheRuleIsOff:
    def test_no_cutover_file_merges(self, no_cutover):
        assert decide(body=LINELESS).action == "merge"

    def test_opened_before_the_cutover_merges(self, cutover):
        assert decide(body=LINELESS, created_at=BEFORE).action == "merge"

    def test_an_unread_creation_time_merges(self, cutover):
        assert decide(body=LINELESS, created_at="").action == "merge"

    def test_a_creation_time_of_none_merges(self, cutover):
        assert decide(body=LINELESS, created_at=None).action == "merge"

    def test_an_unparseable_creation_time_merges(self, cutover):
        assert decide(body=LINELESS, created_at="not-a-time").action == "merge"


# --------------------------------------------------------------------------
# 4. machine-written branches owe no line
# --------------------------------------------------------------------------
@pytest.mark.parametrize("branch", [
    "dependabot/npm_and_yarn/x", "repair/DRE-1-abc", "bot/standards-sync",
])
def test_an_exempt_branch_is_not_held_by_this_condition(cutover, branch):
    d = decide(body=LINELESS, branch=branch)
    assert not d.reason.startswith("What's new: "), d.reason


def test_the_exempt_branches_merge_where_nothing_else_holds(cutover):
    for branch in ("repair/DRE-1-abc", "bot/standards-sync"):
        assert decide(body=LINELESS, branch=branch).action == "merge"


# --------------------------------------------------------------------------
# 5. where it sits: after the draft, after every pending review, before O
# --------------------------------------------------------------------------
class TestOrder:
    def test_a_lineless_draft_is_the_draft_arm(self, cutover):
        d = decide(body=LINELESS, is_draft=True)
        assert d.action == "human", d.reason

    def test_pending_checks_still_wait(self, cutover):
        d = decide(body=LINELESS, checks=PENDING_CI)
        assert d.action == "wait", d.reason

    def test_a_missing_critic_verdict_still_waits(self, cutover):
        d = decide(body=LINELESS, comments=[])
        assert d.action == "wait", d.reason

    def test_it_runs_before_the_code_owner_condition(self, cutover):
        owners = code_owner_hold.Reading(
            code_owner_hold.UNMET, groups=((("@owner",), ("scripts/",)),))
        d = decide(body=LINELESS, owners=owners)
        assert_whats_new_hold(d)
        # Anti-vacuity: with a line, the owner hold is what stands.
        d = decide(body=NONE_BODY, owners=owners)
        assert d.action == "hold" and "code-owner" in d.reason, d.reason


# --------------------------------------------------------------------------
# 6. the evaluator itself
# --------------------------------------------------------------------------
class TestEvaluateWhatsNew:
    def test_returns_none_when_the_line_is_fine(self, cutover):
        assert merge_gate.evaluate_whats_new(BRANCH, NONE_BODY, AFTER) is None

    def test_returns_a_hold_when_it_is_missing(self, cutover):
        d = merge_gate.evaluate_whats_new(BRANCH, LINELESS, AFTER)
        assert_whats_new_hold(d)

    def test_returns_none_for_an_exempt_branch(self, cutover):
        assert merge_gate.evaluate_whats_new("bot/x", LINELESS, AFTER) is None


# --------------------------------------------------------------------------
# 7. the CLI the workflow runs, in process so the seam applies
# --------------------------------------------------------------------------
class TestCli:
    def run_cli(self, tmp_path, *extra):
        cr, cm, wr, cp = (tmp_path / n for n in ("cr.json", "cm.json", "wr.json", "cp.json"))
        cr.write_text(json.dumps({"check_runs": GREEN_CI}))
        cm.write_text(json.dumps(critic()))
        wr.write_text(json.dumps({"workflow_runs": []}))
        cp.write_text(json.dumps({"status": "ahead"}))
        out = io.StringIO()
        with redirect_stdout(out):
            code = merge_gate.main([
                "--head-sha", HEAD, "--qa-login", QA_LOGIN,
                "--check-runs-file", str(cr), "--comments-file", str(cm),
                "--workflow-runs-file", str(wr), "--compare-file", str(cp),
                "--head-branch", BRANCH, *extra,
            ])
        assert code == 0
        lines = out.getvalue().splitlines()
        return dict(ln.split("=", 1) for ln in lines if "=" in ln), out.getvalue()

    def body_file(self, tmp_path, text):
        path = tmp_path / "pr-body.txt"
        path.write_text(text)
        return str(path)

    def test_a_lineless_body_holds_end_to_end(self, cutover, tmp_path):
        fields, _ = self.run_cli(
            tmp_path, "--pr-body-file", self.body_file(tmp_path, LINELESS),
            "--pr-created-at", AFTER)
        assert fields["decision"] == "hold"
        assert fields["reason"].startswith("What's new: ")
        assert fields["reason"].endswith("(standards/whats-new.md)")

    def test_a_line_merges_end_to_end(self, cutover, tmp_path):
        fields, _ = self.run_cli(
            tmp_path, "--pr-body-file", self.body_file(tmp_path, GOOD_BODY),
            "--pr-created-at", AFTER)
        assert fields["decision"] == "merge"

    def test_an_empty_body_file_is_as_if_absent(self, cutover, tmp_path):
        fields, _ = self.run_cli(
            tmp_path, "--pr-body-file", self.body_file(tmp_path, ""),
            "--pr-created-at", AFTER)
        assert fields["decision"] == "merge"

    def test_an_empty_created_at_is_off(self, cutover, tmp_path):
        fields, out = self.run_cli(
            tmp_path, "--pr-body-file", self.body_file(tmp_path, LINELESS),
            "--pr-created-at", "")
        assert fields["decision"] == "merge"
        assert "hold" not in out

    @pytest.mark.parametrize("created_at", [BEFORE, "not-a-time"])
    def test_the_rule_off_prints_no_hold(self, cutover, tmp_path, created_at):
        fields, out = self.run_cli(
            tmp_path, "--pr-body-file", self.body_file(tmp_path, LINELESS),
            "--pr-created-at", created_at)
        assert fields["decision"] == "merge"
        assert "hold" not in out

    def test_no_cutover_prints_no_hold(self, no_cutover, tmp_path):
        fields, out = self.run_cli(
            tmp_path, "--pr-body-file", self.body_file(tmp_path, LINELESS),
            "--pr-created-at", AFTER)
        assert fields["decision"] == "merge"
        assert "hold" not in out

    def test_both_flags_omitted_merges(self, cutover, tmp_path):
        fields, _ = self.run_cli(tmp_path)
        assert fields["decision"] == "merge"

    def test_an_unreadable_body_file_is_off_not_a_failure(self, cutover, tmp_path):
        fields, _ = self.run_cli(
            tmp_path, "--pr-body-file", str(tmp_path / "missing.txt"),
            "--pr-created-at", AFTER)
        assert fields["decision"] == "merge"
