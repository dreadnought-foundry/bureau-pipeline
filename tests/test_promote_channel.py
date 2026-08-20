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
    return promote_channel.evaluate(
        _combined(_status("success")) if combined is None else combined,
        SHA,
        hold=hold,
        ancestry=ancestry,
    )


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


if __name__ == "__main__":
    unittest.main()
