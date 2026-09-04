"""The proof/demo gate, walked with the workflow's own shell (DRE-2746).

Unit-green is not live-working: this feature spans the planner agent, the
Linear read seam and a plan.yml step. So this walks a planner's OUTPUT through
the ACTUAL `run:` block from plan.yml — read out of the workflow and executed
with the run's expressions substituted, against a stubbed `linear_ops.py` that
serves one epic's children and records every write.

Three walks:

  clean    five cards, the last two a PROOF: and a DEMO: blocked by all three
           work siblings — the step passes and writes nothing to the card.
  missing  the planner emitted no demo card — the step fails, the reason is
           posted to the epic, and the epic is put back in Planning.
  fleet    the proof card's criteria are fleet-satisfiable — same bounce, and
           the reason says so.

Plus the honesty case: a read that CRASHES posts no reason at all
(standards/console-honesty.md rule 1 — a crash is not a rejection).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

REPO = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(REPO, "scripts")
WF = os.path.join(REPO, ".github", "workflows", "plan.yml")

EPIC = "DRE-2746"
GATE = "Proof and demo cards"

WORK_BODY = "Build it.\n\n## Acceptance criteria\n\n- [ ] the gate refuses it\n"
PROOF_BODY = (
    "Record what was observed, what was read and when.\n\n"
    "## Acceptance criteria\n\n"
    "- [ ] the gate is observed refusing an epic in production\n"
)
DEMO_BODY = (
    "Show the CEO.\n\n## Acceptance criteria\n\n"
    "- [ ] the CEO is walked through the bounce by hand\n"
)
LABELS = ["repo:bureau-pipeline", "agent:engineer", "initiative:pipeline"]
# The pair wears `agent:ops` (DRE-3039) — a build role on the two cards that
# confirm the epic is the fleet picking up the proof of its own work.
PAIR_LABELS = ["repo:bureau-pipeline", "agent:ops", "initiative:pipeline"]
# No role label at all: precedence 1 answers nothing, so the acceptance
# criteria decide. What the fleet-buildable walk needs.
NO_ROLE_LABELS = ["repo:bureau-pipeline", "initiative:pipeline"]

WORK_IDS = ["DRE-9001", "DRE-9002", "DRE-9003"]

PROOF_ID = "DRE-9091"
DEMO_ID = "DRE-9092"


def _card(identifier, title, body, blocked_by=(), labels=LABELS):
    return {"identifier": identifier, "title": title, "body": body,
            "labels": list(labels), "blocked_by": list(blocked_by)}


def plan(*, demo=True, proof_body=PROOF_BODY, pair_labels=PAIR_LABELS):
    children = [_card(i, f"Build piece {n}", WORK_BODY)
                for n, i in enumerate(WORK_IDS, 1)]
    children.append(_card(PROOF_ID, "PROOF: the gate refused a real epic",
                          proof_body, WORK_IDS, labels=pair_labels))
    if demo:
        children.append(_card(DEMO_ID, "DEMO: the bounce, end to end",
                              DEMO_BODY, WORK_IDS, labels=pair_labels))
    return children


def step_run(fragment: str) -> str:
    doc = yaml.safe_load(open(WF).read())
    for job in doc["jobs"].values():
        for s in job.get("steps") or []:
            if fragment.lower() in (s.get("name") or "").lower():
                if "run" not in s:
                    raise AssertionError(f"step {fragment!r} has no run: block")
                return s["run"]
    raise AssertionError(f"no step named like {fragment!r} in plan.yml")


# A stand-in for the Linear client: `children-detail` serves the fixture,
# every other subcommand appends what it was asked to do. Same argv contract
# as the real one, so the workflow's shell is exercised unchanged — and since
# DRE-3039 the same IMPORT contract too, because the check stamps the verdict
# it computed through `routing_verdict.stamp_card`, which imports this module.
STUB = '''#!/usr/bin/env python3
import json, os, sys


def _log(*row):
    with open(os.environ["STUB_LOG"], "a") as fh:
        fh.write(json.dumps(list(row)) + "\\n")


def comment_bodies(identifier):
    """No card carries a verdict yet — the planner has only just written it."""
    return []


def cmd_comment(identifier, body):
    _log("comment", identifier, body)


def add_label(identifier, label):
    _log("add-label", identifier, label)


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    if cmd == "children-detail":
        if os.environ.get("STUB_READ_CRASHES"):
            sys.stderr.write("boom: Linear read failed\\n")
            sys.exit(70)
        sys.stdout.write(open(os.environ["STUB_CHILDREN"]).read())
        sys.exit(0)
    _log(cmd, *args)
'''


class GateWalkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.pipeline = os.path.join(self.tmp, ".bureau-pipeline")
        os.makedirs(self.pipeline)
        shutil.copytree(SCRIPTS, os.path.join(self.pipeline, "scripts"))
        # The real checkout carries the config the vocabularies are read from,
        # and the roster rule 4 reads the build roles off (DRE-3039).
        shutil.copytree(os.path.join(REPO, "config"),
                        os.path.join(self.pipeline, "config"))
        shutil.copy(os.path.join(REPO, "agents.yaml"),
                    os.path.join(self.pipeline, "agents.yaml"))
        # The stub REPLACES the real client inside the walk's checkout.
        with open(os.path.join(self.pipeline, "scripts", "linear_ops.py"), "w") as fh:
            fh.write(STUB)
        self.log = os.path.join(self.tmp, "writes.log")
        self.children = os.path.join(self.tmp, "children.json")

    def _walk(self, children, *, crash=False):
        with open(self.children, "w") as fh:
            json.dump(children, fh)
        script = step_run(GATE)
        script = script.replace("${{ runner.temp }}", self.tmp)
        script = script.replace(
            "${{ github.event.client_payload.identifier }}", EPIC)
        leftover = re.findall(r"\$\{\{[^}]*\}\}", script)
        self.assertEqual(leftover, [], f"unmodelled expressions: {leftover}")
        env = dict(os.environ,
                   RUNNER_TEMP=self.tmp,
                   STUB_CHILDREN=self.children,
                   STUB_LOG=self.log)
        if crash:
            env["STUB_READ_CRASHES"] = "1"
        return subprocess.run(["bash", "-eo", "pipefail", "-c", script],
                              cwd=self.tmp, capture_output=True, text=True,
                              env=env)

    def _writes(self):
        if not os.path.exists(self.log):
            return []
        return [json.loads(l) for l in open(self.log) if l.strip()]

    # --- clean -----------------------------------------------------------
    def test_a_well_formed_plan_passes_and_says_nothing_on_the_epic(self):
        r = self._walk(plan())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(
            [w for w in self._writes() if w[1] == EPIC], [],
            "a passing gate has nothing to tell the planner",
        )

    # --- the stamp (DRE-3039) ---------------------------------------------
    def test_the_passing_gate_stamps_the_verdict_on_the_pair(self):
        """The seam this walk exists for: the step that ACCEPTS the pair is the
        step that writes each card's verdict, so the sweep reads a decision
        rather than an absence. Before DRE-3039 it computed the verdict, printed
        it and wrote nothing at all."""
        r = self._walk(plan())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        writes = self._writes()
        for identifier in (PROOF_ID, DEMO_ID):
            comments = [w for w in writes
                        if w[0] == "comment" and w[1] == identifier]
            self.assertEqual(len(comments), 1, f"{identifier}: {writes}")
            self.assertIn("🧭 routing-verdict: **OPERATOR**", comments[0][2])
            # ...and the marks the verdict declares, which is what stops the
            # sweep dispatching a competing run.
            marks = [w[2] for w in writes
                     if w[0] == "add-label" and w[1] == identifier]
            self.assertIn("hand-built", marks)

    def test_a_bounced_plan_stamps_nothing(self):
        """An epic on its way back to Planning is not an epic whose cards get a
        routing decision written on them."""
        self._walk(plan(demo=False))
        self.assertEqual(
            [w for w in self._writes() if w[1] == PROOF_ID], [],
            "a refused pair may not be stamped",
        )

    # --- missing demo card ------------------------------------------------
    def test_a_missing_demo_card_bounces_the_epic_back_to_planning(self):
        r = self._walk(plan(demo=False))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

        writes = self._writes()
        comments = [w for w in writes if w[0] == "comment"]
        self.assertTrue(comments, f"the reason must be posted; writes={writes}")
        body = comments[0][2]
        self.assertIn(EPIC, body)
        self.assertIn("DEMO:", body)

        self.assertIn(["state", EPIC, "Planning"], writes,
                      "the epic must be returned to Planning")

    # --- a fleet-buildable proof card -------------------------------------
    def test_a_fleet_buildable_proof_card_bounces_with_that_reason(self):
        r = self._walk(plan(
            proof_body=(
                "Prove it.\n\n## Acceptance criteria\n\n"
                "- [ ] the proof page renders with the design tokens\n"),
            pair_labels=NO_ROLE_LABELS,
        ))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        body = [w for w in self._writes() if w[0] == "comment"][0][2]
        self.assertIn(PROOF_ID, body)
        self.assertIn("FLEET", body)

    # --- a build role on the pair (DRE-3039) ------------------------------
    def test_a_proof_card_wearing_a_build_role_bounces_with_that_reason(self):
        r = self._walk(plan(pair_labels=LABELS))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        body = [w for w in self._writes() if w[0] == "comment"][0][2]
        self.assertIn(PROOF_ID, body)
        self.assertIn("agent:engineer", body)
        self.assertIn("agent:ops", body)

    # --- honesty ----------------------------------------------------------
    def test_a_crashed_read_posts_no_reason(self):
        r = self._walk(plan(), crash=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(
            [w for w in self._writes() if w[0] in ("comment", "state")], [],
            "a crash decided nothing and must not bounce the plan",
        )
