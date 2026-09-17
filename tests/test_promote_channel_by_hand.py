"""RED-first tests for the by-hand channel promote (DRE-4111).

`promote-channel.yml` was triggered by exactly one thing — a `workflow_run` on
the Integration Harness — so when the harness failed for a reason that had
nothing to do with the code, `stable` froze and the only available move was to
re-run the harness and hope.

**2026-09-16.** `stable` sat at `8b54d629a` while `main` reached `34807775b`,
five merge commits and six merged pull requests behind. The harness on main
died at 01:00Z and 01:05Z with

    GitHub API 403: "API rate limit exceeded for installation ID 123249480"

in four of its five scenarios; the fifth, read-only, passed 15 of 15. Nothing
was wrong with any merged commit — the worker App's hourly bucket was empty and
the harness needs it to drive the sandbox. Meanwhile every `promote-channel`
run reported SUCCESS, because every step is guarded on the harness conclusion
and a failed harness skips them all. A row of green runs that promoted nothing
read exactly like a channel that was up to date.

THE SAFETY LINE, which is the whole design and what most of this file pins.
The harness is what PROVES a commit, and `bureau-harness` is deliberately the
one repo kept off the channel so promotion can never validate itself. A by-hand
promote must not become "skip the proof":

  1. **The ordinary by-hand promote is still proof-gated.** It re-reads the
     candidate's combined commit status and requires a green
     `integration-harness` there — from ANY run, not only the newest. That is
     the whole point: the proof passed an hour ago and the channel is behind.
  2. **A refusal names what was missing and where the channel actually is**, so
     the operator does not have to guess which of the two facts to go fix.
  3. **Forcing past a red or absent status is a separate, louder act** — its
     own input, never the default, operator-only, and refused without a stated
     reason. It overrides the PROOF and nothing else.
  4. **The rails hold for everybody.** Force does not move the channel
     backwards, does not lift the hold, and does not promote a commit that is
     not on the trunk. A PR head carries a green harness stamp of its own, so
     without the trunk check a by-hand promote could put `stable` on a commit
     that never merged — proof present, code unshipped.
  5. **The mover is recorded** — who, when, which sha, and why by hand.
"""

import os
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import promote_channel  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "promote-channel.yml"

SHA = "c" * 40
CHANNEL_HEAD = "8b54d629a" + "0" * 31
CONTEXT = promote_channel.STATUS_CONTEXT
OPERATOR = "sid"


def _combined(*statuses):
    return {"state": "irrelevant", "sha": SHA, "statuses": list(statuses)}


def _status(state, context=CONTEXT, description=""):
    return {"context": context, "state": state, "description": description}


def _by_hand(combined=None, **kw):
    """A by-hand promote of a PROVEN commit that is on main and ahead of the
    channel — the ordinary case, which every test below varies one fact of."""
    kw.setdefault("ancestry", promote_channel.AHEAD)
    kw.setdefault("trunk", promote_channel.BEHIND)
    kw.setdefault("actor", OPERATOR)
    kw.setdefault("channel_head", CHANNEL_HEAD)
    kw.setdefault("reason", "harness was rate-limited; DRE-4111")
    return promote_channel.evaluate(
        _combined(_status("success")) if combined is None else combined,
        SHA,
        manual=True,
        **kw,
    )


class ProvenButNotNewestTest(unittest.TestCase):
    """AC: a by-hand promote verifies the candidate's combined commit status
    and the `integration-harness` status is success for that sha, FROM ANY RUN
    — not only the triggering one."""

    def test_a_proven_commit_promotes_by_hand(self):
        d = _by_hand()
        self.assertTrue(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_BY_HAND)
        self.assertIn(SHA, d.reason)

    def test_no_triggering_run_is_needed(self):
        """The automatic path reads the triggering run's own conclusion. A
        by-hand promote has no triggering run, and an absent conclusion must
        not be read as a failure — the commit STATUS is the authority."""
        d = _by_hand(conclusion=None)
        self.assertTrue(d.promote)

    def test_an_older_failed_run_does_not_taint_a_green_status(self):
        """The 2026-09-16 shape exactly: the newest harness run on main went
        red on a 403, and the commit's own status still carries the green
        stamp an earlier run left. That stamp is what the channel promotes
        on."""
        d = _by_hand(conclusion="failure")
        self.assertTrue(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_BY_HAND)

    def test_the_by_hand_outcome_is_distinct_from_the_automatic_one(self):
        """`harness-passed-promoting` means the harness moved the channel. A
        person moving it is a different fact and the receipt has to say so."""
        self.assertNotEqual(promote_channel.OUTCOME_BY_HAND,
                            promote_channel.OUTCOME_PROMOTING)

    def test_the_reason_records_who_moved_it_and_why(self):
        d = _by_hand(reason="harness 403 on the shared App bucket")
        self.assertIn(OPERATOR, d.reason)
        self.assertIn("harness 403 on the shared App bucket", d.reason)


class UnprovenIsRefusedTest(unittest.TestCase):
    """AC: a candidate whose harness status is `failure` or absent is refused
    by the ordinary path, naming which status was missing and what the last
    proven sha was."""

    def test_an_absent_stamp_is_refused(self):
        d = _by_hand(_combined())
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_UNPROVEN)

    def test_a_red_stamp_is_refused(self):
        d = _by_hand(_combined(_status("failure")))
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_UNPROVEN)

    def test_a_pending_stamp_is_refused(self):
        """A harness still running has proved nothing — the same rule the
        automatic path holds."""
        self.assertFalse(_by_hand(_combined(_status("pending"))).promote)

    def test_another_contexts_green_is_not_the_harness(self):
        self.assertFalse(
            _by_hand(_combined(_status("success", context="ci/other"))).promote
        )

    def test_an_unreadable_status_fetch_is_refused(self):
        """`{}` is what the caller writes when the API call fails. Never
        promote on unverifiable data."""
        self.assertFalse(_by_hand({}).promote)

    def test_a_sandbox_blocked_stamp_is_not_a_proof(self):
        blocked = promote_channel.BLOCKED_MARKER + " sandbox reconcile red"
        d = _by_hand(_combined(_status("success", description=blocked)))
        self.assertFalse(d.promote)

    def test_the_refusal_names_the_status_that_was_missing(self):
        d = _by_hand(_combined())
        self.assertIn(CONTEXT, d.reason)

    def test_the_refusal_names_the_last_proven_sha(self):
        """"Which status, and where is the channel actually" are the two facts
        the operator needs, and on 2026-09-16 reading the 403 gave neither."""
        d = _by_hand(_combined(_status("failure")))
        self.assertIn(CHANNEL_HEAD, d.reason)

    def test_the_refusal_names_the_force_route(self):
        """A refusal that does not say what the next move is sends the reader
        back to the workflow file."""
        self.assertIn("force", _by_hand(_combined()).reason.lower())


class ForcingIsASeparateLouderActTest(unittest.TestCase):
    """AC: forcing past a refused status is a distinct, explicit input, is
    recorded on the run with a required reason, and is not the default."""

    def test_force_promotes_past_a_red_stamp_and_says_so(self):
        d = _by_hand(_combined(_status("failure")), force=True,
                     reason="harness 403, bucket empty; DRE-4111")
        self.assertTrue(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_BY_HAND_FORCED)

    def test_a_forced_promote_records_the_reason_verbatim(self):
        d = _by_hand(_combined(), force=True, reason="bucket empty, re-proving")
        self.assertIn("bucket empty, re-proving", d.reason)

    def test_a_forced_promote_records_the_mover(self):
        d = _by_hand(_combined(), force=True, reason="why", actor="ada")
        self.assertIn("ada", d.reason)

    def test_a_forced_promote_is_named_distinctly(self):
        """Loud is the point — a forced promote must never be filed under the
        same receipt as an ordinary one."""
        self.assertNotEqual(promote_channel.OUTCOME_BY_HAND_FORCED,
                            promote_channel.OUTCOME_BY_HAND)
        self.assertNotEqual(promote_channel.OUTCOME_BY_HAND_FORCED,
                            promote_channel.OUTCOME_PROMOTING)

    def test_force_without_a_reason_is_refused(self):
        for blank in (None, "", "   "):
            d = _by_hand(_combined(), force=True, reason=blank)
            self.assertFalse(d.promote, f"{blank!r} must not force a promote")
            self.assertEqual(d.outcome,
                             promote_channel.OUTCOME_FORCE_NEEDS_REASON)

    def test_force_is_refused_for_a_machine_actor(self):
        """Operator-only. Agents in this fleet hold tokens with
        `actions: write` and can dispatch workflows, so "a person ticked the
        box" has to be checked rather than assumed."""
        for bot in ("github-actions", "agent-bureau-bot[bot]",
                    "agent-bureau-qa-bot[bot]", "dependabot[bot]"):
            d = _by_hand(_combined(), force=True, reason="why", actor=bot)
            self.assertFalse(d.promote, f"{bot} must not force a promote")
            self.assertEqual(d.outcome,
                             promote_channel.OUTCOME_FORCE_NOT_OPERATOR)

    def test_force_with_no_actor_named_fails_closed(self):
        d = _by_hand(_combined(), force=True, reason="why", actor=None)
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_FORCE_NOT_OPERATOR)

    def test_force_is_not_the_default(self):
        """The ordinary by-hand promote of an unproven sha refuses. Force has
        to be asked for."""
        self.assertFalse(_by_hand(_combined()).promote)

    def test_force_on_an_already_proven_commit_is_an_ordinary_promote(self):
        """Ticking a box that was not needed must not file an ordinary
        promotion under the loud receipt."""
        d = _by_hand(force=True, reason="belt and braces")
        self.assertTrue(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_BY_HAND)

    def test_the_automatic_path_can_never_force(self):
        """`force` is a dispatch input. The workflow_run path must not be able
        to reach the forced arm even if the flag arrives set."""
        d = promote_channel.evaluate(
            _combined(_status("failure")), SHA, ancestry=promote_channel.AHEAD,
            conclusion="success", branch="main", force=True, reason="x",
            actor=OPERATOR,
        )
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_UNPROVEN)


class TheRailsHoldForABy_HandPromoteTest(unittest.TestCase):
    """AC: the ancestry check still holds — a by-hand promote can never move
    `stable` backwards. Plus the two other rails that are not the proof and
    that force therefore does not lift."""

    def test_a_backwards_move_is_refused(self):
        d = _by_hand(ancestry=promote_channel.BEHIND)
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_NOT_AHEAD)
        self.assertIn("backwards", d.reason.lower())

    def test_force_does_not_move_the_channel_backwards(self):
        """The loudest thing an operator can ask for still does not get to
        ship older code than the channel already carries."""
        d = _by_hand(_combined(_status("failure")), force=True,
                     reason="I know what I am doing",
                     ancestry=promote_channel.BEHIND)
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_NOT_AHEAD)

    def test_a_diverged_candidate_is_refused(self):
        self.assertFalse(_by_hand(ancestry=promote_channel.DIVERGED).promote)

    def test_an_unknown_ancestry_fails_closed(self):
        self.assertFalse(_by_hand(ancestry=None).promote)

    def test_a_channel_already_there_is_a_noop(self):
        d = _by_hand(ancestry=promote_channel.IDENTICAL)
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_NOT_AHEAD)
        self.assertIn("already", d.reason.lower())

    def test_a_first_ever_promotion_is_allowed_by_hand(self):
        d = _by_hand(ancestry=promote_channel.NO_CHANNEL_YET, channel_head=None)
        self.assertTrue(d.promote)

    def test_a_candidate_that_is_not_on_the_trunk_is_refused(self):
        """A PR head carries a green `integration-harness` stamp of its own
        (harness.yml runs on pull_request). Without this the by-hand path
        would happily put `stable` on a commit that never merged — the proof
        present, the code unshipped."""
        for off_trunk in (promote_channel.AHEAD, promote_channel.DIVERGED, None, ""):
            d = _by_hand(trunk=off_trunk)
            self.assertFalse(d.promote, f"trunk={off_trunk!r} must not promote")
            self.assertEqual(d.outcome, promote_channel.OUTCOME_NOT_ON_TRUNK)

    def test_force_does_not_promote_a_commit_that_is_not_on_the_trunk(self):
        d = _by_hand(_combined(_status("failure")), force=True,
                     reason="why", trunk=promote_channel.DIVERGED)
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_NOT_ON_TRUNK)

    def test_the_trunk_head_itself_is_on_the_trunk(self):
        d = _by_hand(trunk=promote_channel.IDENTICAL)
        self.assertTrue(d.promote)

    def test_the_hold_still_outranks_a_by_hand_promote(self):
        d = _by_hand(hold="who=Ada since=2026-09-16 sandbox rehearsal")
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_HELD)

    def test_force_does_not_lift_the_hold(self):
        """The hold is a deliberate instruction not to advance the channel.
        Force is about the PROOF and nothing else."""
        d = _by_hand(_combined(_status("failure")), force=True,
                     reason="why", hold="who=Ada since=2026-09-16 rehearsal")
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_HELD)


class CliTest(unittest.TestCase):
    """The workflow's only interface to the decision."""

    def _run(self, tmp, argv, combined):
        import json

        path = tmp / "combined.json"
        path.write_text(json.dumps(combined))
        out = tmp / "gh_output"
        out.write_text("")
        os.environ["GITHUB_OUTPUT"] = str(out)
        try:
            rc = promote_channel.main(
                ["--sha", SHA, "--statuses-file", str(path)] + argv
            )
        finally:
            os.environ.pop("GITHUB_OUTPUT", None)
        return rc, out.read_text()

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_by_hand_promote_writes_promote_true(self):
        rc, written = self._run(
            self.tmp,
            ["--manual", "--ancestry", "ahead", "--trunk", "behind",
             "--actor", OPERATOR, "--channel-head", CHANNEL_HEAD,
             "--reason", "harness 403"],
            _combined(_status("success")),
        )
        self.assertEqual(rc, 0)
        self.assertIn("promote=true", written)
        self.assertIn(f"outcome={promote_channel.OUTCOME_BY_HAND}", written)

    def test_an_unproven_by_hand_promote_writes_promote_false(self):
        rc, written = self._run(
            self.tmp,
            ["--manual", "--ancestry", "ahead", "--trunk", "behind",
             "--actor", OPERATOR, "--channel-head", CHANNEL_HEAD,
             "--reason", "harness 403"],
            _combined(_status("failure")),
        )
        self.assertEqual(rc, 0)
        self.assertIn("promote=false", written)
        self.assertIn(f"outcome={promote_channel.OUTCOME_UNPROVEN}", written)

    def test_force_is_a_flag_and_off_unless_given(self):
        rc, written = self._run(
            self.tmp,
            ["--manual", "--force", "--ancestry", "ahead", "--trunk", "behind",
             "--actor", OPERATOR, "--channel-head", CHANNEL_HEAD,
             "--reason", "bucket empty"],
            _combined(_status("failure")),
        )
        self.assertEqual(rc, 0)
        self.assertIn("promote=true", written)
        self.assertIn(f"outcome={promote_channel.OUTCOME_BY_HAND_FORCED}",
                      written)

    def test_the_reason_stays_on_one_line(self):
        """`$GITHUB_OUTPUT` is a key=value file — a newline in the value is a
        second KEY, and the operator's typed reason is not ours to trust."""
        rc, written = self._run(
            self.tmp,
            ["--manual", "--force", "--ancestry", "ahead", "--trunk", "behind",
             "--actor", OPERATOR, "--channel-head", CHANNEL_HEAD,
             "--reason", "line one\nline two"],
            _combined(_status("failure")),
        )
        self.assertEqual(rc, 0)
        reasons = [l for l in written.splitlines() if l.startswith("reason=")]
        self.assertEqual(len(reasons), 1)
        self.assertIn("line one line two", reasons[0])

    def test_a_manual_run_is_not_a_failure_even_when_it_refuses(self):
        rc, _ = self._run(
            self.tmp,
            ["--manual", "--ancestry", "behind", "--trunk", "behind",
             "--actor", OPERATOR, "--channel-head", CHANNEL_HEAD,
             "--reason", "x"],
            _combined(_status("success")),
        )
        self.assertEqual(rc, 0)


class WorkflowWiringTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()
        self.wf = yaml.safe_load(self.text)
        # PyYAML parses the bare key `on:` as the boolean True.
        self.on = self.wf.get("on", self.wf.get(True))
        self.steps = self.wf["jobs"]["promote"]["steps"]

    def _step(self, needle):
        for step in self.steps:
            if needle in str(step.get("run", "")) or needle in str(step):
                return step
        self.fail(f"no step matching {needle!r}")

    # --- the dispatch itself --------------------------------------------- #

    def test_a_person_can_dispatch_the_promotion(self):
        self.assertIn("workflow_dispatch", self.on)

    def test_the_dispatch_names_a_candidate_sha_defaulting_to_mains_head(self):
        inputs = self.on["workflow_dispatch"]["inputs"]
        self.assertIn("sha", inputs)
        self.assertEqual(inputs["sha"].get("default", ""), "")
        self.assertIn("main", inputs["sha"]["description"].lower())
        self.assertIn("default_branch", self.text)

    def test_force_is_its_own_input_and_off_by_default(self):
        force = self.on["workflow_dispatch"]["inputs"]["force"]
        self.assertEqual(force["type"], "boolean")
        self.assertIs(force.get("default"), False)

    def test_the_reason_is_a_required_input(self):
        reason = self.on["workflow_dispatch"]["inputs"]["reason"]
        self.assertTrue(reason.get("required"))

    # --- the safety line -------------------------------------------------- #

    def test_the_decision_receives_every_by_hand_fact(self):
        decide = self._step("promote_channel.py")["run"]
        for flag in ("--manual", "--force", "--reason", "--actor",
                     "--trunk", "--channel-head"):
            self.assertIn(flag, decide, f"the decision never sees {flag}")

    def test_the_by_hand_path_re_reads_the_commit_status(self):
        """Not the triggering run — the commit's own combined status, which
        carries a stamp from ANY run that proved the sha."""
        fetch = self._step("commits/${CANDIDATE}/status")
        cond = str(fetch.get("if", ""))
        self.assertIn("workflow_dispatch", cond)

    def test_the_by_hand_path_checks_the_candidate_is_on_the_trunk(self):
        self.assertIn("compare/${DEFAULT_BRANCH}...${CANDIDATE}", self.text)

    def test_the_by_hand_path_still_compares_against_the_channel_head(self):
        ancestry = self._step("compare/stable...${CANDIDATE}")
        self.assertIn("workflow_dispatch", str(ancestry.get("if", "")))

    def test_the_channel_head_is_read_so_a_refusal_can_name_it(self):
        self.assertIn("git/ref/tags/stable", self.text)
        self.assertIn("object.sha", self.text)

    def test_one_mover_at_a_time_covers_the_dispatch_too(self):
        """The concurrency group is declared at WORKFLOW level, so a by-hand
        promote and the automatic mover queue rather than race for the ref."""
        self.assertEqual(self.wf["concurrency"]["group"], "promote-channel")
        self.assertIs(self.wf["concurrency"]["cancel-in-progress"], False)
        self.assertNotIn("concurrency", self.wf["jobs"]["promote"])

    def test_the_app_token_still_pushes_a_by_hand_promote(self):
        """The trap this file's own header documents: a tag moved with the
        default token fires no `release-gate.yml`, so the channel would
        advance with its validation silently skipped."""
        mint = self._step("create-github-app-token")
        self.assertIn("workflow_dispatch", str(mint.get("if", "")))
        move = self._step("refs/tags/stable")
        self.assertIn("steps.app.outputs.token", str(move))
        self.assertNotIn("github.token", str(move))

    # --- the receipt ------------------------------------------------------ #

    def test_a_promote_that_moved_nothing_reads_differently(self):
        """AC: a green run that promoted nothing must not read as a green run
        that shipped. The 2026-09-16 row of green runs is the whole card."""
        summary = self._step("GITHUB_STEP_SUMMARY")["run"]
        self.assertIn("nothing moved", summary)
        self.assertIn("## Promoted", summary)
        self.assertIn("## Did not promote", summary)

    def test_a_by_hand_run_that_moved_nothing_says_so_too(self):
        """The existing loud block is gated on `BRANCH = main`, which is empty
        on a dispatch — a by-hand decline would have been silent."""
        summary = self._step("GITHUB_STEP_SUMMARY")["run"]
        self.assertIn('EVENT" = "workflow_dispatch"', summary)

    def test_the_mover_is_recorded_on_the_run(self):
        summary = self._step("GITHUB_STEP_SUMMARY")
        env = summary.get("env", {})
        self.assertIn("github.actor", str(env))
        self.assertIn("mover", summary["run"])

    def test_a_forced_promote_raises_a_warning(self):
        summary = self._step("GITHUB_STEP_SUMMARY")["run"]
        self.assertIn("::warning title=Forced channel promotion", summary)


class TheRuleIsWrittenDownTest(unittest.TestCase):
    """A change that contradicts a document updates that document in the SAME
    PR. Both operator-facing docs said the channel moves with no operator
    involved and named no way for a person to move it."""

    def setUp(self):
        self.docs = {
            "docs/self-hosting.md": (ROOT / "docs" / "self-hosting.md").read_text(),
            "README.md": (ROOT / "README.md").read_text(),
        }

    def test_both_docs_describe_the_by_hand_path(self):
        for name, text in self.docs.items():
            self.assertIn("DRE-4111", text, f"{name} never cites the card")
            self.assertIn("gh workflow run promote-channel.yml", text,
                          f"{name} does not give the by-hand command")

    def test_the_receipt_vocabulary_is_written_down(self):
        doc = self.docs["docs/self-hosting.md"]
        for outcome in (promote_channel.OUTCOME_BY_HAND,
                        promote_channel.OUTCOME_BY_HAND_FORCED,
                        promote_channel.OUTCOME_NOT_ON_TRUNK,
                        promote_channel.OUTCOME_FORCE_NEEDS_REASON,
                        promote_channel.OUTCOME_FORCE_NOT_OPERATOR):
            self.assertIn(outcome, doc,
                          "a receipt vocabulary nobody wrote down is one "
                          "nobody can read at 2am")

    def test_the_doc_says_force_does_not_skip_the_proof_for_free(self):
        doc = self.docs["docs/self-hosting.md"]
        self.assertIn("force", doc.lower())
        self.assertIn("bureau-harness", doc)


if __name__ == "__main__":
    unittest.main()
