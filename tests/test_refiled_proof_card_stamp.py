"""A proof card re-filed during a plan revision gets its routing verdict on the
review route too (DRE-6604).

What happened. Epic DRE-6022's planner revised its plan a third time at the
second critic's bound (2026-10-06 17:46 PT), canceled the old proof card
DRE-6028 — retitled `Canceled, re-filed as DRE-6048: …`, so `is_proof` no
longer matches it — and filed DRE-6048 as the new one. A revision at the bound
skips `Re-check the revised plan — review mode` (gated `bound != 'true'`), and
the two review-route steps that stamp children after that point, `Epic → Green
Light — both critics passed` and `Activate the approved epic`, ran
`plan_child_verdicts.py stamp` alone — which leaves a `PROOF:` child to
`proof_and_demo.py` by title, on purpose. Nothing on that route ran
`proof_and_demo.py`, so DRE-6048 reached Backlog with no verdict, the sweep
refused it on `routing-no-verdict`, and Portico's sweep went red for two and a
half hours until an operator ran the proof check by hand.

This walks the ACTUAL `run:` blocks of both steps out of plan.yml, the way
`tests/test_proof_and_demo_scenario.py` walks the plan route's gate: the run's
expressions substituted, against a stubbed `linear_ops.py` that serves
DRE-6022's seven children as they stood at 2026-10-06 21:57 PT
(`tests/fixtures/dre-6022-children-2026-10-06.json`) and records every write.

  replay    the Green Light step stamps DRE-6048 OPERATOR with `no-code`,
            writes nothing on its six siblings but the child stamp's own
            adoption move, and moves the epic to Green Light.
  refused   the same walk with the proof card's closing line removed: the
            bounce note on the epic, no verdict, no lane, a red step.
  activate  the activation step runs the same check over the same children
            file before the child stamp, and a refusal writes neither
            `In Progress` nor the activation comment and promotes nothing.

Run: cd bureau-pipeline && python3 -m pytest tests/test_refiled_proof_card_stamp.py -v
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
FIXTURE = os.path.join(REPO, "tests", "fixtures", "dre-6022-children-2026-10-06.json")
sys.path.insert(0, SCRIPTS)

import plan_critic as pc  # noqa: E402
import proof_and_demo  # noqa: E402
import routing_verdict  # noqa: E402

EPIC = "DRE-6022"
PROOF_ID = "DRE-6048"
CANCELED_ID = "DRE-6028"
BUILD_IDS = ["DRE-6024", "DRE-6025", "DRE-6026", "DRE-6027", "DRE-6047"]

GREEN_LIGHT = "Epic → Green Light — both critics passed"
ACTIVATE = "Activate the approved epic"

#: What each card's thread held at 21:57 PT: the FLEET verdict the Green Light
#: step's child stamp had just written on each build child, and the OPERATOR
#: verdict the plan route's proof gate wrote on DRE-6028 at 16:51, before the
#: revision canceled it. DRE-6048 has nothing on it.
VERDICTS = {
    **{i: [routing_verdict.verdict_comment(
        "FLEET", "the epic's build child, stamped at planning exit")]
       for i in BUILD_IDS},
    CANCELED_ID: [routing_verdict.verdict_comment(
        "OPERATOR", "the epic's proof card, routed by the proof-and-demo check")],
    PROOF_ID: [],
}


def children(*, closing_line=True):
    cards = json.load(open(FIXTURE))
    if not closing_line:
        for card in cards:
            if card["identifier"] == PROOF_ID:
                card["body"] = card["body"].replace(
                    proof_and_demo.CLOSING_LINE + "\n", "")
    return cards


def released_thread():
    """The epic's thread as the review route left it at 21:57: an attempt the
    first critic and the second critic both passed, every record the
    pipeline's own."""
    return [{"body": body, "authored_by_pipeline": True} for body in (
        pc.cycle_marker(EPIC),
        pc.marker(pc.STAGE_PRE, 1, pc.PASS),
        pc.marker(pc.STAGE_POST, 1, pc.PASS),
    )]


def wf_steps():
    doc = yaml.safe_load(open(WF).read())
    return [s for job in doc["jobs"].values() for s in job.get("steps") or []]


def step_named(name: str) -> dict:
    for step in wf_steps():
        if (step.get("name") or "") == name:
            return step
    raise AssertionError(f"no step named {name!r} in plan.yml")


# The Linear client the walk's checkout carries instead of the real one. Same
# argv contract and the same import contract `proof_and_demo` and
# `plan_child_verdicts` write through, and STATEFUL: a comment the walk posts
# is a comment `comment_bodies` reads back, so a second pass is refused by the
# real write path rather than by the stub.
STUB = '''#!/usr/bin/env python3
import json, os, sys

NO_CODE_LABEL = "no-code"


def _log(*row):
    with open(os.environ["STUB_LOG"], "a") as fh:
        fh.write(json.dumps(list(row)) + "\\n")


def _written():
    if not os.path.exists(os.environ["STUB_LOG"]):
        return []
    return [json.loads(l) for l in open(os.environ["STUB_LOG"]) if l.strip()]


def comment_bodies(identifier):
    before = json.load(open(os.environ["STUB_VERDICTS"])).get(identifier, [])
    return list(before) + [w[2] for w in _written()
                           if w[0] == "comment" and w[1] == identifier]


def cmd_comment(identifier, body):
    _log("comment", identifier, body)


def add_label(identifier, label):
    _log("add-label", identifier, label)


def cmd_advance(identifier, to, frm=None):
    _log("advance", identifier, to, frm)


def get_issue(identifier, **_):
    cards = json.load(open(os.environ["STUB_CHILDREN"]))
    return next(c for c in cards if c["identifier"] == identifier)


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    if cmd == "children-detail":
        sys.stdout.write(open(os.environ["STUB_CHILDREN"]).read())
        sys.exit(0)
    if cmd == "children":
        print(len(json.load(open(os.environ["STUB_CHILDREN"]))))
        sys.exit(0)
    if cmd == "dump-comments":
        sys.stdout.write(open(os.environ["STUB_THREAD"]).read())
        sys.exit(0)
    _log(cmd, *args)
'''

# The promoter, for the activation walk: it reads the live board, so the walk
# records that it was called and nothing more.
PROMOTER_STUB = '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STUB_LOG"], "a") as fh:
    fh.write(json.dumps(["promote"] + sys.argv[1:]) + "\\n")
'''


class _Walk(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        pipeline = os.path.join(self.tmp, ".bureau-pipeline")
        os.makedirs(pipeline)
        shutil.copytree(SCRIPTS, os.path.join(pipeline, "scripts"))
        shutil.copytree(os.path.join(REPO, "config"),
                        os.path.join(pipeline, "config"))
        shutil.copy(os.path.join(REPO, "agents.yaml"),
                    os.path.join(pipeline, "agents.yaml"))
        with open(os.path.join(pipeline, "scripts", "linear_ops.py"), "w") as fh:
            fh.write(STUB)
        with open(os.path.join(pipeline, "scripts", "reconcile.py"), "w") as fh:
            fh.write(PROMOTER_STUB)
        self.log = os.path.join(self.tmp, "writes.log")
        self.children = os.path.join(self.tmp, "children.json")
        self.verdicts = os.path.join(self.tmp, "verdicts.json")
        self.thread = os.path.join(self.tmp, "thread.json")
        self.outputs = os.path.join(self.tmp, "github-output")
        with open(self.verdicts, "w") as fh:
            json.dump(VERDICTS, fh)
        with open(self.thread, "w") as fh:
            json.dump(released_thread(), fh)

    def _walk(self, name, cards):
        with open(self.children, "w") as fh:
            json.dump(cards, fh)
        script = step_named(name)["run"]
        script = script.replace("${{ runner.temp }}", self.tmp)
        script = script.replace(
            "${{ github.event.client_payload.identifier }}", EPIC)
        leftover = re.findall(r"\$\{\{[^}]*\}\}", script)
        self.assertEqual(leftover, [], f"unmodelled expressions: {leftover}")
        env = dict(os.environ,
                   RUNNER_TEMP=self.tmp,
                   GITHUB_OUTPUT=self.outputs,
                   GITHUB_REPOSITORY="dreadnought-foundry/portico",
                   GH_TOKEN="t",
                   MAX_WIP="12",
                   STUB_CHILDREN=self.children,
                   STUB_VERDICTS=self.verdicts,
                   STUB_THREAD=self.thread,
                   STUB_LOG=self.log)
        # Actions' default shell for a step with no `shell:` — `bash -e`,
        # without `pipefail` — so the walk fails exactly where the run does.
        return subprocess.run(["bash", "-e", "-c", script],
                              cwd=self.tmp, capture_output=True, text=True,
                              env=env)

    def _writes(self):
        if not os.path.exists(self.log):
            return []
        return [json.loads(l) for l in open(self.log) if l.strip()]

    def _on(self, identifier):
        return [w for w in self._writes() if w[0] != "promote" and w[1] == identifier]

    def _verdicts_on(self, identifier):
        return [w[2] for w in self._on(identifier)
                if w[0] == "comment" and "🧭 routing-verdict:" in w[2]]


# ===========================================================================
# The replay: the Green Light step at 21:57 PT
# ===========================================================================
class TheGreenLightStepStampsTheProofCardTest(_Walk):

    def test_the_fixture_is_the_epic_s_seven_children_as_they_stood(self):
        cards = children()
        self.assertEqual([c["identifier"] for c in cards],
                         BUILD_IDS[:4] + [CANCELED_ID, BUILD_IDS[4], PROOF_ID])
        self.assertEqual([c["created_at"] for c in cards],
                         sorted(c["created_at"] for c in cards))
        for card in cards:
            self.assertEqual(sorted(card),
                             ["blocked_by", "body", "created_at", "identifier",
                              "labels", "title"], card["identifier"])
        proof = cards[-1]
        self.assertNotIn("no-code", proof["labels"])
        self.assertIn(proof_and_demo.CLOSING_LINE, proof["body"].splitlines())
        # Why the check passes on this board: the canceled card was retitled,
        # so it is not a second proof, and the new card is blocked by it.
        self.assertFalse(proof_and_demo.is_proof(cards[4]["title"]))
        self.assertEqual(proof_and_demo.findings(cards), [])
        # ...and the thread is one the second critic released.
        self.assertEqual(pc.post_release(released_thread(), EPIC)[0],
                         pc.POST_RELEASED)

    def test_dre_6048_leaves_the_step_carrying_its_operator_verdict(self):
        """The defect. On the plan.yml this card was filed against, the step
        ran the child stamp alone, which leaves a `PROOF:` child to the proof
        check by title — and nothing on the review route ran the proof
        check."""
        r = self._walk(GREEN_LIGHT, children())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        verdicts = self._verdicts_on(PROOF_ID)
        self.assertEqual(len(verdicts), 1, self._on(PROOF_ID))
        self.assertEqual(routing_verdict.verdict_on(verdicts), "OPERATOR")
        self.assertEqual(
            [w[2] for w in self._on(PROOF_ID) if w[0] == "add-label"],
            ["no-code"])

    def test_its_six_siblings_are_written_to_by_nothing_but_the_child_stamp(self):
        """Every sibling already carried its verdict, so the child stamp's
        write path refuses each one; what it still does is its adoption move,
        which takes a card out of Intake and out of nothing else. No comment,
        no label."""
        r = self._walk(GREEN_LIGHT, children())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for identifier in BUILD_IDS + [CANCELED_ID]:
            kinds = {w[0] for w in self._on(identifier)}
            self.assertLessEqual(kinds, {"advance"}, (identifier, self._on(identifier)))

    def test_the_epic_is_moved_to_green_light(self):
        r = self._walk(GREEN_LIGHT, children())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(["state", EPIC, "Green Light"], self._writes())
        notes = [w[2] for w in self._on(EPIC) if w[0] == "comment"]
        self.assertTrue(any(n.startswith("✅ Ready for you in Green Light")
                            for n in notes), notes)
        self.assertFalse(any(proof_and_demo.BOUNCE_TAG in n for n in notes))

    def test_the_proof_card_is_stamped_before_the_child_stamp_runs(self):
        """The proof check first, as on the plan route: the child stamp then
        reads a proof card already carrying its verdict."""
        self._walk(GREEN_LIGHT, children())
        writes = self._writes()
        verdict_at = next(n for n, w in enumerate(writes)
                          if w[:2] == ["comment", PROOF_ID])
        advances = [n for n, w in enumerate(writes) if w[0] == "advance"]
        self.assertTrue(advances, writes)
        self.assertLess(verdict_at, min(advances))

    def test_a_second_pass_writes_no_second_verdict(self):
        """The stamp refuses a card already carrying one."""
        self._walk(GREEN_LIGHT, children())
        r = self._walk(GREEN_LIGHT, children())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self._verdicts_on(PROOF_ID)), 1)
        self.assertEqual(
            [w[2] for w in self._on(PROOF_ID) if w[0] == "add-label"],
            ["no-code"])


# ===========================================================================
# The refusal: the same walk, the proof card's closing line removed
# ===========================================================================
class ARefusedProofCheckStopsTheGreenLightStepTest(_Walk):

    def setUp(self):
        super().setUp()
        self.r = self._walk(GREEN_LIGHT, children(closing_line=False))

    def test_the_step_exits_non_zero(self):
        self.assertNotEqual(self.r.returncode, 0, self.r.stdout + self.r.stderr)

    def test_the_bounce_note_is_posted_on_the_epic(self):
        notes = [w[2] for w in self._on(EPIC) if w[0] == "comment"]
        self.assertEqual(len(notes), 1, notes)
        self.assertIn(f"🧾 {proof_and_demo.BOUNCE_TAG}", notes[0])
        self.assertIn("closing line", notes[0])

    def test_no_verdict_is_written_on_dre_6048(self):
        self.assertEqual(self._on(PROOF_ID), [])

    def test_no_lane_is_written(self):
        self.assertEqual([w for w in self._writes() if w[0] in ("state", "advance")], [])

    def test_the_child_stamp_does_not_run(self):
        """An epic the proof check refused is not an epic whose cards get
        routing decisions or adoption moves written on them."""
        for identifier in BUILD_IDS + [CANCELED_ID]:
            self.assertEqual(self._on(identifier), [], identifier)


# ===========================================================================
# The activation step: the same check, the same children file, first
# ===========================================================================
class TheActivationStepRunsTheProofCheckTest(_Walk):

    def _run(self):
        return step_named(ACTIVATE)["run"]

    def test_it_runs_the_proof_check_with_its_stamp_before_the_child_stamp(self):
        run = self._run()
        check = run.index("proof_and_demo.py check")
        self.assertLess(run.index('if [ "$POST" = "$RELEASED" ]; then'), check)
        self.assertLess(check, run.index("plan_child_verdicts.py stamp"))
        self.assertNotIn("--no-stamp", run)

    def test_over_the_same_children_file(self):
        run = self._run()
        self.assertEqual(run.count("children-detail"), 1)
        line = next(l for l in run.splitlines() if "children-detail" in l)
        self.assertIn('> "$CHILDREN"', line)
        check = run[run.index("proof_and_demo.py check"):]
        self.assertIn('< "$CHILDREN"', check.split("\n", 2)[0] + check.split("\n", 2)[1])
        stamp = run[run.index("plan_child_verdicts.py stamp"):]
        self.assertIn('< "$CHILDREN"', stamp.split("\n", 2)[0] + stamp.split("\n", 2)[1])

    def test_a_clean_check_stamps_the_proof_card_and_activates(self):
        r = self._walk(ACTIVATE, children())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self._verdicts_on(PROOF_ID)), 1)
        writes = self._writes()
        verdict_at = writes.index(next(w for w in writes if w[:2] == ["comment", PROOF_ID]))
        active_at = writes.index(["state", EPIC, "In Progress"])
        self.assertLess(verdict_at, active_at)
        self.assertEqual(writes[-1][0], "promote")

    def test_a_refused_check_writes_neither_in_progress_nor_the_activation_comment(self):
        r = self._walk(ACTIVATE, children(closing_line=False))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        writes = self._writes()
        self.assertNotIn(["state", EPIC, "In Progress"], writes)
        notes = [w[2] for w in self._on(EPIC) if w[0] == "comment"]
        self.assertFalse(any("Epic activated" in n for n in notes), notes)
        self.assertNotIn("promote", [w[0] for w in writes])
        self.assertEqual(self._on(PROOF_ID), [])
        # ...and the note says why, without claiming a lane it did not write.
        self.assertEqual(len(notes), 1, notes)
        self.assertIn(f"🧾 {proof_and_demo.BOUNCE_TAG}", notes[0])
        self.assertIn("has not moved", notes[0])


# ===========================================================================
# The comment above the Green Light step says what the step does now
# ===========================================================================
class TheGreenLightStepIsWiredLikeTheRecheckTest(unittest.TestCase):

    def test_the_proof_check_runs_before_the_child_stamp(self):
        run = step_named(GREEN_LIGHT)["run"]
        check = run.index("proof_and_demo.py check")
        self.assertLess(check, run.index("plan_child_verdicts.py stamp"))
        self.assertLess(check, run.index('state "$EPIC" "Green Light"'))
        self.assertNotIn("--no-stamp", run)

    def test_the_comment_no_longer_calls_the_child_stamp_the_whole_backstop(self):
        text = open(WF, encoding="utf-8").read()
        above = text[:text.index(f"- name: {GREEN_LIGHT}")]
        block = above[above.rindex("# PASS, and the first critic's last word"):]
        self.assertIn("proof_and_demo.py check", block)
        self.assertIn("DRE-6604", block)


if __name__ == "__main__":
    unittest.main()
