"""DRE-4149: the Integration Harness proves `main`, not every pull request.

Until this card `harness.yml` ran twice for the same change: once on the pull
request (DRE-2103, boundary paths) and once on `main` after the merge
(DRE-2551), where its verdict is what `promote-channel.yml` moves `stable`
on. The pull request's run was largely a re-proof of `main` — the sandbox's
stubs ride `@main` and GitHub resolves that ref at dispatch, which the
harness logged on every PR run ("agent-task.yml parses at <main sha> … which
is NOT the commit under test"), and DRE-3101 had already handed a PR run
main's own sweep and probe rules.

It was not free. On 2026-09-17 the worker App's hourly GitHub allowance ran
out near the end of every hour (DRE-4132). Every harness run that needed
GitHub in that stretch was refused, so six approved pull requests sat behind
a red harness that said nothing about their code, and `stable` fell twelve
merges behind — while the PR runs themselves were among the heaviest
spenders of the allowance that had run out.

So the pull-request run is gone, and what REPLACES it is pinned here:

  * **the triggers** — `push` to `main` and a by-hand `workflow_dispatch`
    carrying `pipeline_ref`, and nothing else. The PR trigger cannot come
    back quietly: not as `pull_request`, not as `pull_request_target`, and
    not as a leftover `if:` / expression arm that only a PR event reaches.
  * **one lane** — every run the workflow can now start resolves to
    `integration-harness-main` and to the `main` sandbox namespace, a run in
    progress is never cancelled, and a superseded PENDING run is dropped by
    GitHub itself (one pending run per group). That is "runs on main collapse
    to the newest commit" with no machinery added;
    tests/test_merge_train.py simulates it.
  * **the safety line did not move** — `promote-channel.yml` still listens
    to this workflow by name and still acts only on a success on `main`
    (tests/test_promote_channel.py proves the decision, unedited), and
    Red-Main Repair still watches it
    (tests/test_red_main_watch_coverage.py, unedited).

The merge gate's half — a pull request whose head carries no harness check
merges on its other checks — is the decision table in
tests/test_harness_gate_evidence.py.

The accepted cost is written down where the harness's role is described
(scripts/harness/README.md, docs/self-hosting.md): a change that breaks the
pipeline end to end is found on `main` after it merges rather than on its
pull request before; `stable` does not move onto it, so no product repo sees
it.

Run: python3 -m pytest tests/test_harness_proves_main.py -v
"""

import os
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import fix_concurrency  # noqa: E402
from harness import framework  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
HARNESS = WORKFLOWS / "harness.yml"
PROMOTE = WORKFLOWS / "promote-channel.yml"

MAIN_GROUP = "integration-harness-main"
SHA = "a" * 40


def _doc(path=HARNESS):
    assert path.is_file(), f"missing {path.name}"
    return yaml.safe_load(path.read_text())


def _on(doc):
    # YAML 1.1 parses the bare key `on` as boolean True.
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _job(doc):
    return doc["jobs"]["harness"]


def _driver_env(doc):
    for step in _job(doc).get("steps") or []:
        if "python3 -m harness" in (step.get("run") or ""):
            return step.get("env") or {}
    raise AssertionError("no step runs the harness driver")


def _strings(node):
    """Every string VALUE and KEY in the parsed workflow — what GitHub reads.
    Comments are not in here, on purpose: the history of the PR run is
    allowed to be told, the behaviour is not allowed to survive."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                yield key
            yield from _strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)
    elif isinstance(node, str):
        yield node


def push_to_main_event():
    return {
        "github": {
            "event_name": "push",
            "ref": "refs/heads/main",
            "sha": SHA,
            "event": {"ref": "refs/heads/main"},
        },
        "inputs": {},
    }


def dispatch_event(pipeline_ref="agent/DRE-1-a-risky-branch"):
    return {
        "github": {
            "event_name": "workflow_dispatch",
            "ref": "refs/heads/main",
            "event": {"inputs": {"pipeline_ref": pipeline_ref}},
        },
        "inputs": {"pipeline_ref": pipeline_ref, "scenarios": ""},
    }


EVERY_EVENT = (("push to main", push_to_main_event),
               ("by-hand dispatch", dispatch_event))


class TheTriggersTest(unittest.TestCase):
    def test_no_pull_request_trigger_of_either_kind(self):
        on = _on(_doc())
        for trigger in ("pull_request", "pull_request_target"):
            self.assertNotIn(
                trigger, on,
                f"DRE-4149: {trigger} is back on harness.yml. The harness "
                f"proves main once; a PR run re-proves main's workflows, "
                f"spends the worker App's hourly allowance, and a refused run "
                f"holds an approved PR behind a red check that says nothing "
                f"about its code (2026-09-17).",
            )

    def test_the_triggers_are_exactly_main_and_by_hand(self):
        # An exact set, so a NEW way to start the harness is a decision
        # somebody made in a test and not a line somebody added.
        self.assertEqual(set(_on(_doc())), {"push", "workflow_dispatch"})

    def test_every_merge_to_main_still_runs_it(self):
        push = _on(_doc()).get("push")
        self.assertIsInstance(push, dict, "push trigger required")
        self.assertEqual(list(push.get("branches") or []), ["main"])
        # No paths filter: consumers pin the whole repo (DRE-2551).
        self.assertNotIn("paths", push)
        self.assertNotIn("paths-ignore", push)

    def test_a_person_can_still_prove_a_risky_branch_by_hand(self):
        inputs = _on(_doc()).get("workflow_dispatch", {}).get("inputs") or {}
        self.assertIn(
            "pipeline_ref", inputs,
            "the by-hand route is what replaces the PR run for a change "
            "someone wants proved BEFORE it merges",
        )
        self.assertEqual(inputs["pipeline_ref"].get("default"), "main")


class NoPullRequestArmSurvivesTest(unittest.TestCase):
    """Removing the trigger and leaving the arms behind would leave code no
    event can reach — and tests pinning it that prove nothing."""

    def test_nothing_github_reads_mentions_a_pull_request_event(self):
        offenders = sorted(
            {s.strip()[:120] for s in _strings(_doc()) if "pull_request" in s}
        )
        self.assertEqual(
            offenders, [],
            "harness.yml still carries an expression or condition only a "
            "pull_request event can satisfy",
        )

    def test_the_checkout_is_the_by_hand_ref_or_the_pushed_commit(self):
        checkouts = [
            s for s in _job(_doc()).get("steps") or []
            if (s.get("uses") or "").startswith("actions/checkout")
        ]
        self.assertTrue(checkouts, "no checkout step")
        self.assertEqual(
            (checkouts[0].get("with") or {}).get("ref"),
            "${{ inputs.pipeline_ref || github.sha }}",
            "pipeline_ref on a dispatch, else the immutable commit that "
            "triggered the push run (DRE-2551) — never a branch name",
        )

    def test_the_job_carries_no_dependabot_guard(self):
        # The guard existed because dependabot's pull_request events get an
        # EMPTY secrets store (DRE-2047/2067). Dependabot does not push to
        # main and cannot dispatch, so with the trigger gone the guard can
        # only ever be true — and a job-level `if` that is always true is a
        # place for the next condition to hide.
        self.assertNotIn("if", _job(_doc()))

    def test_the_workflow_no_longer_asks_to_read_pull_requests(self):
        # `pull-requests: read` was there for one step: asking which files a
        # pull request changed (DRE-3101). Least privilege — it goes with it.
        self.assertNotIn("pull-requests", _doc().get("permissions") or {})


class OneLaneTest(unittest.TestCase):
    def test_every_run_the_workflow_can_start_shares_mains_lane(self):
        doc = _doc()
        for name, event in EVERY_EVENT:
            with self.subTest(event=name):
                self.assertEqual(fix_concurrency.group(doc, event()), MAIN_GROUP)

    def test_every_run_drives_the_main_sandbox_namespace(self):
        expr = _driver_env(_doc())["HARNESS_NAMESPACE"]
        for name, event in EVERY_EVENT:
            with self.subTest(event=name):
                resolved = fix_concurrency.interpolate(expr, event())
                self.assertEqual(framework.validate_namespace(resolved), "main")

    def test_a_newer_main_run_supersedes_a_pending_one(self):
        # "Runs on main collapse to the newest commit" is GitHub's own
        # behaviour for ONE group: at most one pending run, and a newer
        # arrival replaces it. No cancel step, no sweep — just the group.
        doc = _doc()
        self.assertTrue(
            fix_concurrency.evicts(
                doc, pending=push_to_main_event(), arriving=push_to_main_event()
            )
        )

    def test_a_running_proof_is_never_cancelled(self):
        # DRE-3070/3075: `stable` moves only on a run that FINISHED. With
        # cancel-in-progress a merge train never finishes one.
        self.assertIs(_doc()["concurrency"].get("cancel-in-progress"), False)


class TheSafetyLineDidNotMoveTest(unittest.TestCase):
    """What stops a bad pipeline reaching a product repo was never the PR
    run. It is this wiring, and DRE-4149 leaves it exactly where it was."""

    def test_the_release_channel_still_listens_to_this_workflow_by_name(self):
        listened = (_on(_doc(PROMOTE)).get("workflow_run") or {}).get("workflows")
        self.assertIn(_doc()["name"], listened or [])

    def test_the_channel_still_moves_only_on_a_success_on_main(self):
        text = PROMOTE.read_text()
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", text)


if __name__ == "__main__":
    unittest.main()
