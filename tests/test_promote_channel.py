"""RED-first tests for automatic channel promotion (DRE-2551, Wave 1 Step 2).

The release channel already exists and was abandoned: `release-gate.yml` is
written, tested, and correct, and it has run **once in its life** (2026-07-21)
because cutting the tag was a human ritual. `main` is 174 commits past `v5`.

So this is not a new channel. It is the missing half — **the thing that
pushes**. The harness already stamps `integration-harness` on the sha it truly
checked out; this turns that stamp into a tag move, so the channel is a record
of what has been proven rather than a ceremony someone performs.

Two halves, the `release_gate.py` shape:

  * scripts/promote_channel.py — the decision. Given the candidate's combined
    commit status, the hold switch, and the candidate's ancestry against the
    current channel head: move, or do not, with a reason a human can act on.
  * .github/workflows/promote-channel.yml — the thin caller.

FOUR failure modes are pinned here because each produces a mechanism that
LOOKS right and is not — this wave's entire subject:

  1. **Fail closed.** No stamp, a red stamp, a pending stamp, or a `{}` fetch
     blip must never promote. Same contract as release_gate.
  2. **The hold is a control, not a habit.** Held means refuse, and say so out
     loud — an un-alarmed silent hold is the July failure wearing a hat.
  3. **The channel never moves backwards.** Two harness runs finishing out of
     order must not regress `stable`. Anything that is not a strict descendant
     is refused; unknown ancestry fails closed.
  4. **The mover must not be `github.token`.** GitHub does not trigger
     workflows from events created by the default token, so a tag moved with it
     would never fire `release-gate.yml` — the validation would be silently
     skipped while every test still passed. The move uses the bot App token,
     and `release-gate.yml` must actually match the ref being moved.
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
RELEASE_GATE = ROOT / ".github" / "workflows" / "release-gate.yml"

SHA = "b" * 40
CONTEXT = promote_channel.STATUS_CONTEXT


def _combined(*statuses):
    return {"state": "irrelevant", "sha": SHA, "statuses": list(statuses)}


def _status(state, context=CONTEXT):
    return {"context": context, "state": state}


def _decide(combined=None, *, hold=None, ancestry="ahead"):
    # `[:2]` because the decision grew a third member in DRE-3070 (the
    # machine-readable outcome the since-retired staleness alarm counted,
    # DRE-6053); these tests are about the first two.
    return promote_channel.evaluate(
        _combined(_status("success")) if combined is None else combined,
        SHA,
        hold=hold,
        ancestry=ancestry,
    )[:2]


class EvaluateTest(unittest.TestCase):
    def test_green_stamp_ahead_and_no_hold_promotes(self):
        ok, reason = _decide()
        self.assertTrue(ok)
        self.assertIn(SHA, reason)

    # --- 1. fail closed -------------------------------------------------- #

    def test_missing_stamp_does_not_promote(self):
        ok, reason = _decide(_combined())
        self.assertFalse(ok)
        self.assertIn("integration-harness", reason)

    def test_red_stamp_does_not_promote(self):
        ok, _ = _decide(_combined(_status("failure")))
        self.assertFalse(ok)

    def test_pending_stamp_does_not_promote(self):
        """A harness still running has proved nothing."""
        ok, _ = _decide(_combined(_status("pending")))
        self.assertFalse(ok)

    def test_fetch_blip_does_not_promote(self):
        """`{}` is the substitute the caller writes when the API call fails.
        Never promote on unverifiable data (merge_gate's compare-blip rule)."""
        ok, _ = _decide({})
        self.assertFalse(ok)

    def test_another_contexts_green_does_not_count(self):
        """Some other check being green is not the harness's verdict."""
        ok, _ = _decide(_combined(_status("success", context="ci/other")))
        self.assertFalse(ok)

    # --- 2. the hold is a control ---------------------------------------- #

    def test_hold_refuses_and_names_itself(self):
        ok, reason = _decide(hold="paused for the DRE-2534 sandbox rehearsal")
        self.assertFalse(ok)
        self.assertIn("hold", reason.lower())
        # The REASON the operator typed must survive into the output, or the
        # hold is indistinguishable from a breakage.
        self.assertIn("DRE-2534", reason)

    def test_blank_hold_is_not_a_hold(self):
        """Autonomy by default: an unset or whitespace variable must not
        silently stop the channel. A hold has to be a deliberate act."""
        for blank in (None, "", "   "):
            ok, _ = _decide(hold=blank)
            self.assertTrue(ok, f"{blank!r} should not hold the channel")

    # --- 3. never move backwards ----------------------------------------- #

    def test_identical_is_a_noop_not_a_move(self):
        ok, reason = _decide(ancestry="identical")
        self.assertFalse(ok)
        self.assertIn("already", reason.lower())

    def test_behind_is_refused(self):
        """An out-of-order green run must not regress the channel."""
        ok, reason = _decide(ancestry="behind")
        self.assertFalse(ok)
        self.assertIn("backwards", reason.lower())

    def test_diverged_is_refused(self):
        ok, _ = _decide(ancestry="diverged")
        self.assertFalse(ok)

    def test_unknown_ancestry_fails_closed(self):
        ok, _ = _decide(ancestry=None)
        self.assertFalse(ok)

    def test_first_ever_promotion_is_allowed(self):
        """No channel ref yet: there is nothing to move backwards from."""
        ok, _ = _decide(ancestry=promote_channel.NO_CHANNEL_YET)
        self.assertTrue(ok)


class SkipReceiptTest(unittest.TestCase):
    """DRE-3070. A no-op promotion is ordinary — but "nothing happened" and
    "the run proving this commit was killed by the next merge" are different
    facts, and on 2026-09-03 the channel reported neither. The receipt names
    which of THREE things happened, machine-readably, so a watcher can count
    merge trains instead of saying `unknown` (the staleness alarm did until
    DRE-6053 retired it)."""

    def _decide(self, conclusion, combined=None, **kw):
        return promote_channel.evaluate(
            _combined(_status("success")) if combined is None else combined,
            SHA, ancestry="ahead", conclusion=conclusion, **kw
        )

    def test_a_pr_run_says_it_was_never_a_candidate(self):
        """The fourth reason. A PR-head harness run proves a commit that is
        not on the trunk, so it was rightly skipped — but the run said only
        `skipped`, and on the incident evening it took reading four of them to
        learn that nothing was wrong."""
        d = self._decide("success", branch="agent/DRE-3042-conflict-sweep")
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_NOT_MAIN)
        self.assertIn("main", d.reason.lower())

    def test_a_pr_run_is_not_reported_as_a_hold(self):
        """Branch is read before the hold: a hold is a statement about the
        CHANNEL, and a PR run never approaches the channel."""
        d = self._decide("success", branch="agent/x", hold="who=Ada rehearsal")
        self.assertEqual(d.outcome, promote_channel.OUTCOME_NOT_MAIN)

    def test_a_main_run_is_unaffected_by_naming_its_branch(self):
        d = self._decide("success", branch=promote_channel.TRUNK)
        self.assertTrue(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_PROMOTING)

    def test_an_unstated_branch_is_not_a_pr_run(self):
        """Back-compat, and fail-open only in the direction that is safe: with
        nobody saying, the stamp and the ancestry remain the authorities."""
        self.assertTrue(self._decide("success").promote)

    def test_the_four_reasons_are_distinct(self):
        outcomes = {
            self._decide("success", branch="agent/x").outcome,
            self._decide("cancelled").outcome,
            self._decide("failure").outcome,
            self._decide("success").outcome,
        }
        self.assertEqual(len(outcomes), 4)

    def test_a_cancelled_run_is_named_as_a_newer_push_not_a_failure(self):
        d = self._decide("cancelled")
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_CANCELLED)
        self.assertIn("cancelled", d.reason.lower())
        self.assertIn("newer", d.reason.lower())
        self.assertNotIn("failed", d.reason.lower())

    def test_a_failed_run_is_named_as_a_failure_not_a_merge_train(self):
        d = self._decide("failure")
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_FAILED)
        self.assertIn("fail", d.reason.lower())

    def test_a_timed_out_run_is_a_failure_not_a_merge_train(self):
        """Anything that is neither green nor cancelled is the failure arm —
        a run that died is never reported as a queue effect."""
        self.assertEqual(self._decide("timed_out").outcome,
                         promote_channel.OUTCOME_FAILED)

    def test_a_green_run_says_it_is_promoting(self):
        d = self._decide("success")
        self.assertTrue(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_PROMOTING)
        self.assertIn("promoting", d.reason.lower())

    def test_the_three_reasons_are_distinct(self):
        outcomes = {self._decide(c).outcome
                    for c in ("cancelled", "failure", "success")}
        self.assertEqual(len(outcomes), 3)

    def test_a_cancelled_run_is_never_promoted_even_with_a_green_stamp(self):
        """The stamp on a cancelled run's sha can only be an older run's. Fail
        closed: the conclusion is read before the status."""
        self.assertFalse(self._decide("cancelled").promote)

    def test_the_hold_still_outranks_everything(self):
        """Order is unchanged (promote_channel's own rule): a deliberately
        paused channel reads as paused, never as a merge train."""
        d = self._decide("cancelled", hold="who=Ada paused for the rehearsal")
        self.assertEqual(d.outcome, promote_channel.OUTCOME_HELD)

    def test_an_unstated_conclusion_still_falls_through_to_the_stamp(self):
        """Back-compat: the stamp remains the authority when nobody says what
        the run concluded."""
        d = promote_channel.evaluate(
            _combined(_status("success")), SHA, ancestry="ahead"
        )
        self.assertTrue(d.promote)

    def test_a_refusal_on_the_stamp_is_still_named(self):
        d = self._decide("success", _combined())
        self.assertEqual(d.outcome, promote_channel.OUTCOME_UNPROVEN)

    def test_a_channel_already_there_is_named_not_confused_with_a_skip(self):
        d = promote_channel.evaluate(
            _combined(_status("success")), SHA,
            ancestry="identical", conclusion="success",
        )
        self.assertEqual(d.outcome, promote_channel.OUTCOME_NOT_AHEAD)


class WorkflowWiringTest(unittest.TestCase):
    def setUp(self):
        self.wf = yaml.safe_load(WORKFLOW.read_text())
        # PyYAML parses the bare key `on:` as the boolean True.
        self.on = self.wf.get("on", self.wf.get(True))

    def test_runs_after_the_harness(self):
        run_after = self.on["workflow_run"]
        self.assertIn("Integration Harness", run_after["workflows"])
        self.assertIn("completed", run_after["types"])

    def test_serialised_so_two_runs_cannot_race_the_tag(self):
        self.assertIn("concurrency", self.wf)

    def test_moves_the_tag_with_the_app_token_not_github_token(self):
        """The trap: GitHub does not trigger workflows on events created with
        the default token, so a tag moved with `github.token` would never fire
        release-gate.yml. Everything would look green and nothing would be
        validated."""
        text = WORKFLOW.read_text()
        self.assertIn("create-github-app-token", text)
        job = self.wf["jobs"]["promote"]
        step_texts = [str(s) for s in job["steps"]]
        move = [s for s in step_texts if "refs/tags" in s or "git/refs" in s]
        self.assertTrue(move, "no step moves the channel ref")
        for step in move:
            self.assertNotIn("secrets.GITHUB_TOKEN", step)
            self.assertNotIn("github.token", step)

    def test_a_skipped_promotion_still_runs_and_leaves_a_receipt(self):
        """DRE-3070: the job used to require a GREEN harness run, so the whole
        merge-train case — every displaced run on the incident evening —
        produced no promote-channel run at all and therefore no record. A
        channel that goes quiet must say which of the three things happened."""
        condition = str(self.wf["jobs"]["promote"].get("if", "") or "")
        self.assertNotIn(
            "conclusion == 'success'", condition,
            "a non-green harness run must still reach the decision, or a "
            "skipped promotion leaves no receipt naming why",
        )
        self.assertNotIn(
            "head_branch", condition,
            "a PR-head run must reach the decision too — it is rightly not "
            "promoted, and on 2026-09-03 it took reading four `skipped` runs "
            "to learn that nothing was wrong",
        )

    def test_the_harness_conclusion_and_branch_reach_the_decision(self):
        text = WORKFLOW.read_text()
        self.assertIn("--conclusion", text)
        self.assertIn("workflow_run.conclusion", text)
        self.assertIn("--branch", text)
        self.assertIn("workflow_run.head_branch", text)

    def test_only_a_main_candidate_spends_api_calls(self):
        """Every harness run now reaches the receipt; only a green one on the
        trunk is worth two API calls and a token mint.

        DRE-4111 adds a third admissible gate: a `workflow_dispatch` is a
        person asking for this promotion by hand, so it is never one of the
        PR-head runs this guard exists to keep cheap — and it has no
        `workflow_run` context to read a branch out of.
        """
        for step in self.wf["jobs"]["promote"]["steps"]:
            if "gh api" not in str(step.get("run", "")) \
                    and "app-token" not in str(step.get("uses", "")):
                continue
            cond = str(step.get("if", ""))
            # Either it is gated on the trunk directly, or on a hand dispatch,
            # or on the decision — which cannot be `promote` for anything but a
            # green trunk run or a by-hand one.
            self.assertTrue(
                "head_branch == 'main'" in cond
                or "workflow_dispatch" in cond
                or "steps.decide.outputs.promote" in cond,
                f"step {step.get('name')!r} spends API calls on PR runs",
            )

    def test_release_gate_actually_matches_the_channel_ref(self):
        """`tags: ["v*"]` does not match `stable`. Without this the gate is
        wired to a ref that is never pushed — validation by coincidence."""
        gate = yaml.safe_load(RELEASE_GATE.read_text())
        on = gate.get("on", gate.get(True))
        patterns = on["push"]["tags"]
        self.assertTrue(
            any(promote_channel.matches(p, promote_channel.CHANNEL) for p in patterns),
            f"release-gate.yml triggers on {patterns}, which never matches "
            f"{promote_channel.CHANNEL!r}",
        )


# --------------------------------------------------------------------------- #
# DRE-6496 — agent-bureau's mirror tests are part of the proof                 #
# --------------------------------------------------------------------------- #

OPERATOR = "sidmohan"
MIRROR_TEST = ("console/backend/tests/test_routing_verdicts_mirror.py"
               "::test_the_mirror_matches_bureau_pipeline")
RED_TEST = "cloud/relay/test_old.py::test_red"
AB_SHA = "a" * 40


def _mirror(status, *, failing=(), already_red=(), why=""):
    return {
        "status": status, "why": why, "candidate": SHA, "stable": "5" * 40,
        "agent_bureau_sha": AB_SHA, "tests": 103, "seconds": 300,
        "failing": [{"test": t, "mirrors": ["config/routing-verdicts.json"]}
                    for t in failing],
        "already_red": [{"test": t, "mirrors": []} for t in already_red],
    }


MIRROR_PASSED = _mirror("passed")
MIRROR_FAILED = _mirror("failed", failing=[MIRROR_TEST])
MIRROR_BLOCKED = _mirror("blocked", why="the install step concluded failure")


def _with_mirror(mirror, *, manual=False, force=False, combined=None, **kw):
    if manual:
        kw.setdefault("trunk", "behind")
        kw.setdefault("actor", OPERATOR)
        kw.setdefault("reason", "who=sid testing")
    base = promote_channel.evaluate(
        _combined(_status("success")) if combined is None else combined,
        SHA, ancestry=kw.pop("ancestry", "ahead"), manual=manual, force=force,
        **kw)
    return promote_channel.with_mirror(base, mirror, sha=SHA,
                                       force=manual and force,
                                       actor=kw.get("actor"),
                                       reason=kw.get("reason"))


class MirrorDecisionTest(unittest.TestCase):
    def test_a_green_check_promotes_exactly_as_before(self):
        d = _with_mirror(MIRROR_PASSED)
        self.assertTrue(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_PROMOTING)
        self.assertIn(AB_SHA[:7], d.reason)

    def test_a_candidate_that_breaks_a_mirror_does_not_become_stable(self):
        d = _with_mirror(MIRROR_FAILED)
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_MIRROR_FAILED)
        self.assertIn(MIRROR_TEST, d.reason)
        self.assertIn("config/routing-verdicts.json", d.reason)
        self.assertIn(AB_SHA[:7], d.reason)

    def test_a_test_already_red_on_stable_is_named_and_does_not_refuse(self):
        d = _with_mirror(_mirror("passed", already_red=[RED_TEST]))
        self.assertTrue(d.promote)
        self.assertIn("already red on stable", d.reason)
        self.assertIn(RED_TEST, d.reason)

    def test_a_check_that_could_not_run_moves_nothing_and_blames_nobody(self):
        d = _with_mirror(MIRROR_BLOCKED)
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_MIRROR_BLOCKED)
        self.assertIn("install", d.reason)
        self.assertNotIn("fail on the candidate", d.reason)

    def test_an_absent_result_on_a_promoting_route_is_a_blocked_check(self):
        """Absent means the check did not run — legal only on a route that
        never reaches promotion, or on a forced promote."""
        d = _with_mirror(None)
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_MIRROR_BLOCKED)

    def test_a_refusal_before_the_check_is_unchanged_by_it(self):
        for kw, outcome in (
            ({"hold": "who=Ada paused"}, promote_channel.OUTCOME_HELD),
            ({"conclusion": "cancelled"}, promote_channel.OUTCOME_CANCELLED),
            ({"branch": "agent/x"}, promote_channel.OUTCOME_NOT_MAIN),
            ({"ancestry": "identical"}, promote_channel.OUTCOME_NOT_AHEAD),
        ):
            for mirror in (None, MIRROR_FAILED):
                with self.subTest(kw=kw, mirror=bool(mirror)):
                    self.assertEqual(_with_mirror(mirror, **kw).outcome, outcome)

    def test_an_ordinary_by_hand_promote_still_runs_the_check(self):
        d = _with_mirror(MIRROR_FAILED, manual=True)
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_MIRROR_FAILED)
        self.assertEqual(_with_mirror(MIRROR_PASSED, manual=True).outcome,
                         promote_channel.OUTCOME_BY_HAND)

    def test_force_promotes_past_a_failed_or_blocked_check_and_names_it(self):
        for mirror, word in ((MIRROR_FAILED, promote_channel.OUTCOME_MIRROR_FAILED),
                             (MIRROR_BLOCKED, promote_channel.OUTCOME_MIRROR_BLOCKED)):
            with self.subTest(word=word):
                d = _with_mirror(mirror, manual=True, force=True)
                self.assertTrue(d.promote)
                self.assertEqual(d.outcome, promote_channel.OUTCOME_BY_HAND_FORCED)
                self.assertIn(word, d.reason)
                self.assertIn(OPERATOR, d.reason)

    def test_force_past_a_red_harness_also_names_the_mirror_it_overrode(self):
        d = _with_mirror(MIRROR_FAILED, manual=True, force=True,
                         combined=_combined(_status("failure")))
        self.assertTrue(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_BY_HAND_FORCED)
        self.assertIn("failure", d.reason)
        self.assertIn(promote_channel.OUTCOME_MIRROR_FAILED, d.reason)

    def test_force_overrides_the_mirror_and_nothing_else(self):
        held = _with_mirror(MIRROR_FAILED, manual=True, force=True,
                            hold="who=Ada paused")
        self.assertEqual(held.outcome, promote_channel.OUTCOME_HELD)
        bot = _with_mirror(MIRROR_FAILED, manual=True, force=True,
                           actor="agent-bureau-bot[bot]")
        self.assertEqual(bot.outcome, promote_channel.OUTCOME_FORCE_NOT_OPERATOR)
        off = _with_mirror(MIRROR_FAILED, manual=True, force=True, trunk="diverged")
        self.assertEqual(off.outcome, promote_channel.OUTCOME_NOT_ON_TRUNK)
        back = _with_mirror(MIRROR_FAILED, manual=True, force=True, ancestry="behind")
        self.assertFalse(back.promote)

    def test_a_stray_force_on_a_harness_run_overrides_nothing(self):
        d = _with_mirror(MIRROR_FAILED, force=True)
        self.assertFalse(d.promote)
        self.assertEqual(d.outcome, promote_channel.OUTCOME_MIRROR_FAILED)


class MirrorCliTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _run(self, mirror, extra=()):
        import contextlib
        import io
        import json

        statuses = self.tmp / "s.json"
        statuses.write_text(json.dumps(_combined(_status("success"))))
        result = self.tmp / "mirror_result.json"
        if mirror is not None:
            result.write_text(json.dumps(mirror))
        out = self.tmp / "gh_output"
        out.write_text("")
        os.environ["GITHUB_OUTPUT"] = str(out)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = promote_channel.main(
                    ["--sha", SHA, "--statuses-file", str(statuses),
                     "--ancestry", "ahead", "--mirror-result", str(result),
                     *extra])
        finally:
            os.environ.pop("GITHUB_OUTPUT", None)
        self.assertEqual(rc, 0)
        return out.read_text()

    def test_a_failed_check_writes_the_refusal(self):
        written = self._run(MIRROR_FAILED)
        self.assertIn("promote=false", written)
        self.assertIn(f"outcome={promote_channel.OUTCOME_MIRROR_FAILED}", written)
        self.assertIn("mirror=", written)

    def test_a_missing_result_file_is_a_blocked_check(self):
        written = self._run(None)
        self.assertIn("promote=false", written)
        self.assertIn(f"outcome={promote_channel.OUTCOME_MIRROR_BLOCKED}", written)

    def test_a_green_check_writes_the_promotion(self):
        written = self._run(MIRROR_PASSED)
        self.assertIn("promote=true", written)
        self.assertIn(f"outcome={promote_channel.OUTCOME_PROMOTING}", written)

    def test_every_output_is_one_line(self):
        written = self._run(MIRROR_FAILED)
        keys = [l.split("=", 1)[0] for l in written.strip().splitlines()]
        self.assertEqual(sorted(keys), ["mirror", "outcome", "promote", "reason"])


class MirrorWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.wf = yaml.safe_load(WORKFLOW.read_text())
        self.steps = self.wf["jobs"]["promote"]["steps"]

    def _index(self, pred):
        for i, step in enumerate(self.steps):
            if pred(step):
                return i
        self.fail("no such step")

    def _mirror_steps(self):
        return [s for s in self.steps
                if "agent-bureau" in str(s) and "mirror_check.py card" not in str(s)]

    def test_the_proof_is_decided_before_the_check_and_the_verdict_after(self):
        proof = self._index(lambda s: s.get("id") == "proof")
        run = self._index(lambda s: "mirror_check.py run" in str(s.get("run", "")))
        decide = self._index(lambda s: s.get("id") == "decide")
        move = self._index(lambda s: s.get("name") == "Move the channel")
        self.assertLess(proof, run)
        self.assertLess(run, decide)
        self.assertLess(decide, move)
        self.assertIn("--mirror-result", self.steps[decide]["run"])

    def test_a_run_that_does_not_reach_promotion_never_clones_agent_bureau(self):
        steps = self._mirror_steps()
        self.assertTrue(steps)
        for step in steps:
            self.assertIn("steps.proof.outputs.promote == 'true'",
                          str(step.get("if", "")), step.get("name"))

    def test_agent_bureau_is_read_with_the_bureau_app_scoped_to_it(self):
        mint = [s for s in self.steps
                if "create-github-app-token" in str(s.get("uses", ""))
                and s.get("with", {}).get("repositories") == "agent-bureau"]
        self.assertEqual(len(mint), 1)
        self.assertEqual(mint[0]["with"]["owner"], "dreadnought-foundry")
        self.assertIn("BUREAU_APP_ID", str(mint[0]["with"]))
        checkout = [s for s in self.steps
                    if s.get("with", {}).get("repository")
                    == "dreadnought-foundry/agent-bureau"]
        self.assertEqual(len(checkout), 1)
        self.assertNotIn("ref", checkout[0]["with"],
                         "the head of agent-bureau's default branch, nothing else")
        self.assertIs(checkout[0]["with"].get("persist-credentials"), False)

    def test_the_candidate_sits_where_agent_bureaus_tests_look(self):
        paths = [s.get("with", {}).get("path") for s in self.steps]
        self.assertIn("agent-bureau/.bureau-pipeline", paths)

    def test_the_install_uses_the_shared_action_and_the_discovered_files(self):
        install = [s for s in self.steps
                   if "setup-python-cached" in str(s.get("uses", ""))]
        self.assertEqual(len(install), 1)
        self.assertIn("steps.discover.outputs.requirements",
                      str(install[0]["with"]["requirements"]))

    def test_the_check_has_its_own_time_limit_and_the_job_room_for_it(self):
        run = self.steps[self._index(
            lambda s: "mirror_check.py run" in str(s.get("run", "")))]
        self.assertEqual(run.get("timeout-minutes"), 15)
        self.assertGreater(self.wf["jobs"]["promote"]["timeout-minutes"], 15 + 10)

    def test_a_setup_failure_reaches_the_check_as_blocked(self):
        for step in self._mirror_steps():
            if "mirror_check.py run" in str(step.get("run", "")):
                continue
            self.assertTrue(step.get("continue-on-error"), step.get("name"))
        run = self.steps[self._index(
            lambda s: "mirror_check.py run" in str(s.get("run", "")))]
        self.assertIn("--setup", run["run"])
        self.assertTrue(run.get("continue-on-error"))

    def test_the_refusal_files_its_card_with_this_repos_linear_key(self):
        card = self.steps[self._index(
            lambda s: "mirror_check.py card" in str(s.get("run", "")))]
        self.assertIn(promote_channel.OUTCOME_MIRROR_FAILED, str(card.get("if", "")))
        self.assertIn("secrets.LINEAR_API_KEY", str(card.get("env", {})))
        self.assertTrue(card.get("continue-on-error"))

    def test_the_receipt_says_card_owed_and_the_forced_warning_names_the_mirror(self):
        say = self.steps[self._index(lambda s: s.get("name") == "Say what happened")]
        env = str(say.get("env", {}))
        self.assertIn("steps.decide.outputs.mirror", env)
        self.assertIn("steps.mirror_card.outputs.card", env)
        forced = [l for l in say["run"].splitlines()
                  if "::warning title=Forced channel promotion" in l]
        self.assertEqual(len(forced), 1)
        self.assertIn("MIRROR", forced[0])


class MirrorDocsTest(unittest.TestCase):
    def test_the_receipt_table_carries_both_outcomes(self):
        doc = (ROOT / "docs" / "self-hosting.md").read_text()
        for outcome in (promote_channel.OUTCOME_MIRROR_FAILED,
                        promote_channel.OUTCOME_MIRROR_BLOCKED):
            self.assertIn(f"| `{outcome}` |", doc)
        self.assertIn("DRE-6496", doc)

    def test_the_docs_say_what_force_now_overrides(self):
        doc = (ROOT / "docs" / "self-hosting.md").read_text()
        row = [l for l in doc.splitlines()
               if l.startswith(f"| `{promote_channel.OUTCOME_BY_HAND_FORCED}`")]
        self.assertEqual(len(row), 1)
        self.assertIn("mirror", row[0])
        readme = (ROOT / "README.md").read_text()
        self.assertIn("DRE-6496", readme)
        self.assertIn("mirror", readme)


if __name__ == "__main__":
    unittest.main()
