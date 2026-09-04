"""RED-first tests for DRE-3005 — a verdict may not assert a run it did not do.

THE INCIDENTS (2026-09-02/03, two repos, one night). Two pull requests were
blocked by critic findings that are provably false, and both findings assert
something about a RUN:

  * agent-bureau #2247 — the verdict's only remaining blocker was *"I ran
    `python3 .bureau-pipeline/scripts/check_tdd_commits.py origin/main HEAD`
    myself against the current head and it exits 1"*. Run independently at
    the same ref against the same head, it exits 0. The tell is what the
    verdict left out: the script prints a per-commit classification line for
    every commit UNCONDITIONALLY, beside the reason — the verdict reproduced
    the failure string verbatim and none of the listing. ~22h blocked, one
    fix-loop attempt spent, one operator decision.
  * portico #407 — the verdict said *"this PR's own CI checks never ran the
    new spec through that job"*. Job 100550113617 in run 33724409256 had run
    it: 194 passed. One API read away. The same verdict also quoted PR-body
    text that had been corrected five minutes before the verdict was
    written — the review snapshotted the body at 06:41:40 and posted at
    06:48:41 against an edit made at 06:43:21. ~10h blocked.

Both PRs were correct the entire time.

THE LAW THIS VIOLATES already exists in the estate — *capture the error body,
not the status* — and had only ever been applied to clients. A verdict
claiming "I ran X and got Y" is the same shape and carries the same
obligation.

WHAT THIS FILE PINS:

1. THE STANDARD — standards/verdict-evidence.md exists, states the three
   rules, and reaches the critic through the assemble_context.py rail
   (DRE-1646).
2. RULE 1, the command claim — scripts/verdict_evidence.py finds a verdict
   that cites a command and does not carry that command's actual output, and
   `check_tdd_commits.py`'s per-commit listing is the fixture: quoting the
   failure string alone is exactly the defect.
3. RULE 2, the CI-coverage claim — a finding that a job never ran something
   must cite the run id, the job id and a line proving what it ran.
4. RULE 3, the body snapshot — the verdict states the moment it read the PR
   description, and an edit landing after that moment is SAID so on the
   verdict instead of silently disputing text that no longer exists.
5. NO FALSE POSITIVES — ordinary review prose that mentions a file, a
   command name or a test is not a run claim. This gate sits in front of
   every rejection the pipeline makes; a gate that fires on judgement
   findings would block more correct work than the bug it fixes.
6. THE WIRING — both critic prompts carry the rules, both result gates run
   the check, and a defective REQUEST_CHANGES becomes a NEUTRAL hold that
   names the unevidenced claim, never a merge and never a false rejection.
7. THE TDD RULE, RECONCILED — the engineering standard now states the rule
   that is actually ENFORCED (one test commit strictly before the FIRST
   implementation commit), because the critic reads that standard and
   #2247's verdict stated a stricter rule nothing enforces.

WHAT IS DELIBERATELY NOT GATED: an APPROVE. The failure mode is a verdict
that BLOCKS correct work, and holding a clean review hostage to a citation
format would spend reviews to protect nothing. And judgement findings —
scope, design, risk — are untouched: the critic is meant to be believed
there, and only claims a command can settle are in scope.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

import yaml

REPO = os.path.join(os.path.dirname(__file__), "..")
WF_DIR = os.path.join(REPO, ".github", "workflows")
SCRIPTS = os.path.join(REPO, "scripts")
STANDARD = os.path.join(REPO, "standards", "verdict-evidence.md")
sys.path.insert(0, SCRIPTS)

import verdict_evidence as ve  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────

def standard_body() -> str:
    with open(STANDARD, encoding="utf-8") as f:
        return f.read()


def wf_steps(workflow="qa-review.yml", job="review"):
    doc = yaml.safe_load(open(os.path.join(WF_DIR, workflow)))
    return doc["jobs"][job]["steps"]


def wf_step(step_id, workflow="qa-review.yml", job="review"):
    for step in wf_steps(workflow, job):
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"no step id={step_id!r} in {workflow}:{job}")


def critic_prompts():
    return [wf_step(sid)["with"]["prompt"] for sid in ("critic", "critic_retry")]


def gate_shells():
    return [wf_step(sid)["run"] for sid in ("gate1", "gate2")]


def verdict(*body: str) -> str:
    """A REQUEST_CHANGES verdict in the shape the critic prompt mandates."""
    return "\n".join(
        ["VERDICT: REQUEST_CHANGES cause:defect", "", "## Summary", "",
         "The change does not do what the card asked for.", "",
         "## For the fixing agent", ""] + list(body)
    ) + "\n"


def check_cli(text: str, *extra: str):
    """Run `verdict_evidence.py check` over `text`; returns (rc, stdout)."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "qa-verdict.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        p = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "verdict_evidence.py"),
             "check", path, *extra],
            capture_output=True, text=True,
        )
    return p.returncode, p.stdout + p.stderr


# The two verdicts that started this card, reduced to the sentence that did
# the damage. Both are FINDINGS ABOUT A RUN, and neither carries the run.
DRE_2247_CLAIM = (
    "I ran `python3 .bureau-pipeline/scripts/check_tdd_commits.py "
    "origin/main HEAD` myself against the current head (f3f4f0dd) and it "
    "exits 1 with 'no test commit precedes the implementation — commit the "
    "RED test first'."
)
PORTICO_407_CLAIM = (
    "The spec is not covered: this PR's own CI checks never ran the new "
    "spec through that job."
)


# ── 1. the standard, and the rail that delivers it ─────────────────────────

class StandardOnTheRailTest(unittest.TestCase):
    """The standard must exist and reach the critic via assemble_context.py
    — the DRE-1646 single-source rail. A rule the reviewer never reads
    changes nothing about the reviews it is meant to change."""

    def test_standard_file_exists(self):
        self.assertTrue(
            os.path.isfile(STANDARD),
            "standards/verdict-evidence.md must exist",
        )

    def test_the_critic_receives_the_standard(self):
        import assemble_context as ac

        self.assertIn(
            "verdict-evidence.md", ac.standards_for("critic"),
            "the critic must receive the verdict-evidence standard — it is "
            "the role that writes the claims the standard governs",
        )

    def test_readme_lists_the_standard(self):
        with open(os.path.join(REPO, "standards", "README.md"),
                  encoding="utf-8") as f:
            self.assertIn("verdict-evidence.md", f.read())

    def test_it_states_the_estate_law_it_is_an_instance_of(self):
        # "Capture the error body, not the status" has only ever been
        # applied to clients. Naming it is how this reads as an existing
        # rule reaching a new surface rather than a new rule to remember.
        self.assertIn("capture the error body", standard_body().lower())

    def test_it_protects_the_critics_authority_on_judgement(self):
        text = standard_body().lower()
        self.assertIn("judgement", text)
        for word in ("scope", "design", "risk"):
            self.assertIn(word, text)

    def test_it_names_the_three_claim_kinds(self):
        text = standard_body().lower()
        self.assertIn("command", text)
        self.assertIn("job id", text)
        self.assertIn("snapshot", text)


# ── 2. rule 1 — a command claim carries the command's output ───────────────

class CommandClaimTest(unittest.TestCase):
    """A verdict that cites a command MUST include its actual output —
    enough to be re-run and compared, not a quoted fragment."""

    def test_the_2247_claim_is_a_defect(self):
        rc, out = check_cli(verdict(DRE_2247_CLAIM))
        self.assertEqual(rc, 1, out)
        self.assertIn("check_tdd_commits.py", out)

    def test_a_quoted_failure_fragment_is_not_the_output(self):
        # The exact shape of #2247: the failure STRING is reproduced
        # verbatim, in a blockquote, and the per-commit listing the script
        # prints beside it unconditionally is absent. A real run has both.
        rc, _ = check_cli(verdict(
            DRE_2247_CLAIM, "",
            "> no test commit precedes the implementation — commit the RED "
            "test first",
        ))
        self.assertEqual(rc, 1)

    def test_the_real_run_output_satisfies_it(self):
        rc, out = check_cli(verdict(
            DRE_2247_CLAIM, "",
            "```",
            "$ python3 .bureau-pipeline/scripts/check_tdd_commits.py "
            "origin/main HEAD",
            "b60c929 [test] test(DRE-2832): RED",
            "b8a801a [code] feat(DRE-2832): the planner brief learns",
            "a test commit precedes the first implementation commit",
            "```",
        ))
        self.assertEqual(rc, 0, out)

    def test_a_fence_with_the_command_and_no_output_is_still_a_defect(self):
        # Pasting the command back is not pasting the result. "Enough of it
        # to be re-run AND COMPARED" needs the second half.
        rc, _ = check_cli(verdict(
            DRE_2247_CLAIM, "",
            "```",
            "python3 .bureau-pipeline/scripts/check_tdd_commits.py "
            "origin/main HEAD",
            "```",
        ))
        self.assertEqual(rc, 1)

    def test_an_unrelated_fence_does_not_launder_the_claim(self):
        # A diff snippet elsewhere in the verdict is not this command's
        # output — the evidence has to carry the command it evidences.
        rc, _ = check_cli(verdict(
            DRE_2247_CLAIM, "",
            "```python",
            "def check_commits(commits):",
            "    return True, 'ok'",
            "```",
        ))
        self.assertEqual(rc, 1)

    def test_it_reports_every_unevidenced_claim_not_just_the_first(self):
        rc, out = check_cli(verdict(
            DRE_2247_CLAIM, "",
            "I also ran `npm run lint` and it exits 2.",
        ))
        self.assertEqual(rc, 1)
        self.assertIn("check_tdd_commits.py", out)
        self.assertIn("npm run lint", out)

    def test_exit_code_phrasing_without_a_first_person_verb_still_counts(self):
        # "it exits 1" is an assertion about a run whether or not the
        # sentence admits to having done the running.
        claims = ve.run_claims(
            "`python3 scripts/check_tdd_commits.py origin/main HEAD` "
            "exits 1 on this branch."
        )
        self.assertEqual(len(claims), 1, claims)


class NoFalsePositiveTest(unittest.TestCase):
    """This gate sits in front of every rejection the pipeline makes. A gate
    that fires on ordinary review prose would block more correct work than
    the bug it fixes, so the detection is deliberately narrow: a command
    reference AND an assertion about a run's outcome."""

    NOT_CLAIMS = (
        "`scripts/check_tdd_commits.py` has no test for the dependabot path.",
        "The card asks for a check and the diff adds `verdict_evidence.py`.",
        "Add a test that runs the classifier over a merge commit.",
        "`pr_size_strategy.choose()` returns the wrong strategy for a "
        "lockfile bump.",
        "This should be covered by `python3 -m pytest tests`.",
        "The new module never runs — nothing imports it.",
        "Scope creep: the diff also rewrites the medic's retry budget.",
        "The summary claims the suite is green; nothing in the diff proves "
        "it.",
    )

    def test_ordinary_review_prose_is_not_a_run_claim(self):
        for line in self.NOT_CLAIMS:
            with self.subTest(line=line):
                self.assertEqual(
                    ve.run_claims(line), [],
                    f"ordinary review prose read as a run claim: {line!r}",
                )

    def test_a_clean_request_changes_verdict_passes(self):
        rc, out = check_cli(verdict(*self.NOT_CLAIMS))
        self.assertEqual(rc, 0, out)

    def test_an_approve_is_never_gated(self):
        # The failure mode is a verdict that BLOCKS correct work. An APPROVE
        # blocks nothing, and spending a re-review to police its citation
        # format would protect nobody.
        text = "VERDICT: APPROVE\n\n## Summary\n\n" + DRE_2247_CLAIM + "\n"
        rc, out = check_cli(text)
        self.assertEqual(rc, 0, out)

    def test_a_verdict_reviewing_this_gate_does_not_void_itself(self):
        # An honest review OF this file quotes the rule it enforces. The
        # gate reads the FINDINGS, not the standard being quoted.
        rc, out = check_cli(verdict(
            "The standard says a verdict that cites a command must include "
            "its actual output. The new test does not cover the empty-fence "
            "case.",
        ))
        self.assertEqual(rc, 0, out)


# ── 3. rule 2 — a CI-coverage claim cites the job ──────────────────────────

class JobCoverageClaimTest(unittest.TestCase):
    """A finding about what a CI job did or did not run must cite the job:
    run id, job id, and the line proving what it ran. portico #407's
    `194 passed` was one API read away."""

    def test_the_407_claim_is_a_defect(self):
        rc, out = check_cli(verdict(PORTICO_407_CLAIM))
        self.assertEqual(rc, 1, out)
        self.assertIn("job", out.lower())

    def test_naming_the_job_alone_is_not_enough(self):
        rc, _ = check_cli(verdict(
            PORTICO_407_CLAIM, "", "The `e2e` job is defined in `ci.yml`.",
        ))
        self.assertEqual(rc, 1)

    def test_run_id_job_id_and_the_proving_line_satisfy_it(self):
        rc, out = check_cli(verdict(
            PORTICO_407_CLAIM.replace("never ran", "did not run"), "",
            "Checked run 33724409256, job 100550113617:", "",
            "```",
            "npx playwright test e2e/*.spec.ts --reporter=line",
            "194 passed (5.1m)",
            "```",
        ))
        self.assertEqual(rc, 0, out)

    def test_a_run_id_without_a_job_id_is_still_a_defect(self):
        rc, out = check_cli(verdict(
            PORTICO_407_CLAIM, "",
            "See run 33724409256.", "", "```", "194 passed", "```",
        ))
        self.assertEqual(rc, 1, out)

    def test_an_actions_url_counts_as_both_ids(self):
        # The URL GitHub itself hands you carries the run and the job.
        rc, out = check_cli(verdict(
            PORTICO_407_CLAIM, "",
            "https://github.com/o/r/actions/runs/33724409256/job/100550113617",
            "", "```", "194 passed (5.1m)", "```",
        ))
        self.assertEqual(rc, 0, out)

    def test_a_positive_coverage_claim_is_not_gated(self):
        # "The job ran it" needs no defending: the finding it supports is
        # that something ELSE is wrong. The gate is for the negative claim,
        # which is the one that blocks a PR.
        self.assertEqual(
            ve.job_claims("The e2e job ran the new spec on ubuntu-latest."),
            [],
        )


# ── 4. rule 3 — the PR-body snapshot the review read ───────────────────────

class BodySnapshotTest(unittest.TestCase):
    """portico #407: the review snapshotted the body at 06:41:40, the author
    corrected it at 06:43:21, and the verdict posted at 06:48:41 quoting the
    dead text. The correction the critic demanded was already there, five
    minutes before the verdict was written. A stale snapshot silently
    disputing a corrected body is a race with no signal — so the verdict
    says which snapshot it read, and says out loud when it went stale."""

    REVIEWED = "2026-09-03T06:41:40Z"
    EDITED = "2026-09-03T06:43:21Z"

    def test_the_footer_states_the_snapshot_it_read(self):
        footer = ve.body_snapshot_footer(self.REVIEWED, None)
        self.assertIn(self.REVIEWED, footer)

    def test_an_edit_after_the_snapshot_is_flagged(self):
        footer = ve.body_snapshot_footer(self.REVIEWED, self.EDITED)
        self.assertIn(self.REVIEWED, footer)
        self.assertIn(self.EDITED, footer)
        self.assertIn("edited", footer.lower())

    def test_an_edit_before_the_snapshot_is_not_flagged(self):
        # 06:41:33's edit is what the review READ. Flagging it would cry
        # wolf on every PR whose body was ever touched.
        footer = ve.body_snapshot_footer(self.REVIEWED, "2026-09-03T06:41:33Z")
        self.assertNotIn("edited", footer.lower())

    def test_an_unknown_edit_time_never_claims_freshness(self):
        # Console-honesty rule 2, on a verdict: unknown is shown as unknown,
        # never as the last known value.
        footer = ve.body_snapshot_footer(self.REVIEWED, "")
        self.assertIn(self.REVIEWED, footer)
        self.assertNotIn("edited at ", footer.lower())

    def test_no_snapshot_time_yields_no_footer(self):
        # Fail quiet, not loud: a workflow that could not read the body's
        # timestamps must not stamp a verdict with a time it invented.
        self.assertEqual(ve.body_snapshot_footer("", self.EDITED), "")

    def test_the_footer_is_a_single_line_block(self):
        # It rides on the verdict comment merge_gate parses. It must not
        # look like a verdict header, and it must not carry a bare `@<sha>`
        # or a `content:` field that a parser could read as a binding.
        footer = ve.body_snapshot_footer(self.REVIEWED, self.EDITED)
        self.assertNotIn("VERDICT:", footer)
        self.assertNotIn("content:", footer)

    def test_the_critic_is_told_to_state_the_snapshot(self):
        for prompt in critic_prompts():
            self.assertIn("BODY SNAPSHOT", prompt)


# ── 5. the neutral hold a defective verdict becomes ────────────────────────

class DefectiveVerdictMessageTest(unittest.TestCase):
    """A verdict the gate cannot believe must not post as REQUEST_CHANGES —
    that is the #1441/#1442 false-reject class, and it is exactly the harm
    this card is about. It becomes a NEUTRAL hold: merge waits, the fix
    agent is not dispatched to fix findings nobody proved, and the job goes
    red so the medic sees it."""

    def message(self) -> str:
        return ve.hold_message(ve.defects(verdict(DRE_2247_CLAIM)))

    def test_it_carries_the_qa_critic_marker(self):
        # merge-gate reads the latest QA Critic comment: without the marker
        # a stale APPROVE would stay the last word.
        self.assertIn("QA Critic", self.message())

    def test_it_carries_no_verdict_line_at_all(self):
        msg = self.message()
        self.assertNotIn("VERDICT: APPROVE", msg)
        self.assertNotIn("VERDICT: REQUEST_CHANGES", msg)

    def test_it_says_plainly_this_is_not_a_code_rejection(self):
        self.assertIn("not a code rejection", self.message().lower())

    def test_it_makes_no_claim_about_authentication(self):
        # DRE-2465: borrowing the crash wording sent an operator
        # credential-hunting for a day. The reviewer ran fine here.
        low = self.message().lower()
        for word in ("auth", "credential", "token", "startup"):
            self.assertNotIn(word, low)

    def test_it_names_the_unevidenced_claim(self):
        self.assertIn("check_tdd_commits.py", self.message())

    def test_no_defects_yields_no_message(self):
        self.assertEqual(ve.hold_message([]), "")


# ── 6. the wiring — the rules reach the critic and the gate runs ───────────

class CriticPromptTest(unittest.TestCase):
    """Both prompt blocks carry the rules. They are byte-duplicated in
    qa-review.yml because Actions has no YAML anchors, so every rule has to
    be asserted on BOTH or the retry quietly reviews to an older standard."""

    def test_both_prompts_demand_the_command_output(self):
        for prompt in critic_prompts():
            self.assertIn("EVIDENCE FOR A CLAIM ABOUT A RUN", prompt)

    def test_both_prompts_name_the_fenced_block_format(self):
        # The critic can only satisfy a machine check it knows the shape of.
        for prompt in critic_prompts():
            self.assertIn("fenced", prompt.lower())

    def test_both_prompts_demand_the_run_and_job_ids(self):
        for prompt in critic_prompts():
            low = prompt.lower()
            self.assertIn("run id", low)
            self.assertIn("job id", low)

    def test_both_prompts_keep_judgement_findings_out_of_scope(self):
        for prompt in critic_prompts():
            self.assertIn("judgement", prompt.lower())

    def test_the_two_prompts_carry_identical_rules(self):
        first, second = critic_prompts()
        for marker in ("EVIDENCE FOR A CLAIM ABOUT A RUN", "BODY SNAPSHOT"):
            self.assertEqual(
                first.count(marker), second.count(marker),
                f"{marker!r} has drifted between the two critic prompts",
            )


class GateWiringTest(unittest.TestCase):
    def test_both_result_gates_run_the_evidence_check(self):
        for shell in gate_shells():
            self.assertIn("verdict_evidence.py", shell)

    def test_the_gate_reports_the_evidence_outcome_as_a_step_output(self):
        for shell in gate_shells():
            self.assertIn("--github-output", shell)

    def test_the_post_step_has_an_evidence_branch(self):
        post = wf_step("post")
        self.assertIn("EVIDENCE", str(post.get("env", {})))
        self.assertIn("qa-evidence-hold.md", post["run"])

    def test_the_hold_message_is_read_from_a_file_never_an_env_string(self):
        # The message quotes the critic's own findings, and the critic read
        # an attacker-authored diff. A file is data; a double-quoted shell
        # string is code (the MODEL_WHY rule, DRE-2317 follow-on).
        post = wf_step("post")
        self.assertNotIn("EVIDENCE_MESSAGE", str(post.get("env", {})))

    def test_the_stale_hold_message_is_cleared_before_the_retry(self):
        src = open(os.path.join(WF_DIR, "qa-review.yml")).read()
        for step in wf_steps():
            if "Clear stale verdict before retry" in (step.get("name") or ""):
                self.assertIn("qa-evidence-hold.md", step["run"])
                return
        self.assertIn("qa-evidence-hold.md", src, "no clear-before-retry step")

    def test_a_defective_verdict_still_fails_the_job_loudly(self):
        # The medic's visibility rides on the job being red. An evidence
        # hold is a review that did not land, exactly like a crash.
        for step in wf_steps():
            if "Fail if critic never really ran" in (step.get("name") or ""):
                self.assertIn("gate1.outputs.real", step["if"])
                self.assertIn("gate2.outputs.real", step["if"])
                return
        raise AssertionError("the loud-fail step is gone")


# ── 7. the TDD rule, reconciled ────────────────────────────────────────────

class TddRuleReconciledTest(unittest.TestCase):
    """#2247's verdict stated the rule as *"a RED-test commit immediately
    before each implementation commit"*. `check_tdd_commits.py` enforces
    *"at least one commit touching tests/ STRICTLY BEFORE the first commit
    that changes non-test code"*. Those are different standards, only one is
    enforced, and the gap is where the false finding lived. The critic reads
    standards/engineering.md — so the standard has to state the enforced
    rule, and say that the stricter reading is not a gate."""

    def standard(self) -> str:
        with open(os.path.join(REPO, "standards", "engineering.md"),
                  encoding="utf-8") as f:
            return f.read()

    def test_the_standard_states_the_enforced_rule(self):
        text = self.standard().lower()
        self.assertIn("strictly before", text)
        self.assertIn("first commit that changes non-test code", text)

    def test_the_standard_says_one_red_commit_not_one_per_commit(self):
        text = self.standard().lower()
        self.assertIn("not one before every", text)

    def test_the_standard_names_the_enforcer(self):
        self.assertIn("check_tdd_commits.py", self.standard())

    def test_the_standard_tells_a_reviewer_not_to_block_on_the_stricter_read(self):
        text = self.standard().lower()
        self.assertIn("dre-3005", text)

    def test_the_wording_matches_the_script_that_enforces_it(self):
        # One rule, two files, and the script is the one that decides. If
        # the docstring is reworded, this test is the thing that notices.
        with open(os.path.join(SCRIPTS, "check_tdd_commits.py"),
                  encoding="utf-8") as f:
            doc = f.read().lower()
        self.assertIn("strictly", doc)
        self.assertIn("before the first commit that changes non-test", doc)
        standard = self.standard().lower()
        self.assertIn("strictly before", standard)


if __name__ == "__main__":
    unittest.main()
