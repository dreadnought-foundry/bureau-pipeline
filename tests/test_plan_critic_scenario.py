"""The two critics, walked end to end with plan.yml's own shell (DRE-2721).

Unit-green is not live-working. This feature spans two agent stages, a Linear
comment thread that carries the round history between runs, and the promotion
that lets agents start building — so this walk executes the ACTUAL `run:`
blocks read out of plan.yml, with the run's expressions substituted and
`linear_ops.py` / `reconcile.py` replaced by recording stubs. A step that stops
matching the script turns this red rather than failing live on the first epic.

The walk, one method per observable the card asks for:

  1. A plan is SENT BACK by the first critic, with a stated reason, and the
     epic does not reach the CEO on that round.
  2. A revised plan PASSES the first critic and the epic reaches Green Light.
  3. The second critic runs AFTER approval, against the approved text, and
     nothing promotes until it has.
  4. Two failed rounds and the plan reaches the CEO anyway, with the critic's
     stated reason attached — at both critics.
  5. The send-back rate of the second critic is readable out of the thread the
     run wrote, over as many rounds as the epic has had.
  6. A real collision between two epics in flight is caught here and read from
     the critic's own output — and counted apart from one found later.
  7. An epic re-planned from Triage after a previous attempt spent its whole
     budget gets its own revision round, rather than being pushed straight to
     the CEO on the first send-back of the new plan.
  8. The round history the bound is counted from is the PIPELINE's own writes.
     A comment left by anyone else on the epic — the marker line, or the cycle
     boundary — neither spends a budget nor refunds one.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_critic_scenario.py -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
WF = os.path.join(ROOT, ".github", "workflows", "plan.yml")
sys.path.insert(0, SCRIPTS)

import plan_critic as pc  # noqa: E402

EPIC = "DRE-2721"
OTHER_EPIC = "DRE-2700"

# The epics in flight the sight step reads. DRE-2700 is the collision partner:
# it is mid-build on the same file the plan under review hands to a new card.
EPICS_IN_FLIGHT = [
    {"identifier": OTHER_EPIC, "title": "The intake gate", "state": "In Progress"},
    {"identifier": EPIC, "title": "Two critics", "state": "Todo"},
]

# A stub that answers the four linear_ops verbs plan.yml uses on these paths and
# records every write. `comment` appends to the thread, so the NEXT round reads
# the round history the previous one wrote — which is the mechanism the bound
# is built on.
#
# The thread is stored as RECORDS, because who wrote a comment is part of what
# the rail reads: the stub's own writes are the pipeline's, and a comment any
# other person on the epic left is not. `dump-comments` serves bodies or
# records depending on the flag, exactly like the real client.
LINEAR_STUB = '''#!/usr/bin/env python3
import json, os, sys

cmd, *args = sys.argv[1:]
thread_path = os.environ["STUB_THREAD"]
log_path = os.environ["STUB_LOG"]


def thread():
    try:
        with open(thread_path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def log(line):
    with open(log_path, "a") as f:
        f.write(line + "\\n")


if cmd == "dump-comments":
    records = thread()
    if "--with-authors" in args:
        print(json.dumps(records))
    else:
        print(json.dumps([r["body"] for r in records]))
elif cmd == "comment":
    records = thread() + [{"body": args[1], "authored_by_pipeline": True}]
    with open(thread_path, "w") as f:
        json.dump(records, f)
    log("comment " + args[1].replace("\\n", " | "))
elif cmd == "state":
    log("state " + args[1])
elif cmd == "children":
    print(os.environ.get("STUB_KIDS", "4"))
elif cmd == "epics-in-flight":
    print(os.environ.get("STUB_EPICS", "[]"))
else:
    sys.exit("stub linear_ops: unhandled command " + cmd)
'''

RECONCILE_STUB = '''#!/usr/bin/env python3
import os, sys
with open(os.environ["STUB_LOG"], "a") as f:
    f.write("promote " + " ".join(sys.argv[1:]) + "\\n")
'''


def step(fragment: str) -> dict:
    doc = yaml.safe_load(open(WF).read())
    for job in doc["jobs"].values():
        for s in job.get("steps") or []:
            if fragment.lower() in (s.get("name") or "").lower():
                return s
    raise AssertionError(f"no step named like {fragment!r} in plan.yml")


class CriticWalk(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.pipeline = os.path.join(self.tmp, ".bureau-pipeline")
        os.makedirs(os.path.join(self.pipeline, "scripts"))
        for name in ("plan_critic.py", "design_parity.py"):
            shutil.copy(os.path.join(SCRIPTS, name),
                        os.path.join(self.pipeline, "scripts", name))
        self._stub("linear_ops.py", LINEAR_STUB)
        self._stub("reconcile.py", RECONCILE_STUB)
        self.thread_path = os.path.join(self.tmp, "thread.json")
        self.log_path = os.path.join(self.tmp, "log.txt")
        self.gho = os.path.join(self.tmp, "step-output")
        for path, seed in ((self.thread_path, "[]"), (self.log_path, ""), (self.gho, "")):
            with open(path, "w") as f:
                f.write(seed)

    def _stub(self, name, body):
        path = os.path.join(self.pipeline, "scripts", name)
        with open(path, "w") as f:
            f.write(body)
        os.chmod(path, 0o755)

    # --- the seams --------------------------------------------------------

    def _shell(self, fragment: str, subs: dict | None = None, **env_extra):
        """Run a plan.yml step's shell with the run's expressions resolved."""
        script = step(fragment)["run"]
        script = script.replace("${{ runner.temp }}", self.tmp)
        script = script.replace("${{ github.event.client_payload.identifier }}", EPIC)
        script = script.replace("${{ github.repository }}", "dreadnought-foundry/bureau-pipeline")
        for expression, value in (subs or {}).items():
            script = script.replace(expression, value)
        leftover = re.findall(r"\$\{\{[^}]*\}\}", script)
        self.assertEqual(leftover, [], f"unmodelled expressions in {fragment!r}")
        env = dict(
            os.environ,
            STUB_THREAD=self.thread_path,
            STUB_LOG=self.log_path,
            STUB_EPICS=json.dumps(EPICS_IN_FLIGHT),
            GITHUB_OUTPUT=self.gho,
            GITHUB_REPOSITORY="dreadnought-foundry/bureau-pipeline",
            MAX_WIP="8",
            LINEAR_API_KEY="test-key",
        )
        env.update(env_extra)
        out = subprocess.run(["bash", "-e", "-c", script], cwd=self.tmp,
                             capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out

    def _critic_writes(self, stage: str, result: str, reason: str = "", extra: str = ""):
        """Stand in for the agent step: the critic's own result file."""
        path = os.path.join(self.tmp, f"plan-critic-{stage}.md")
        with open(path, "w") as f:
            f.write(pc.result_line(result, reason) + "\n\n" + extra)
        return path

    def _outputs(self):
        """The step outputs the workflow's `if:` conditions read."""
        out = {}
        for line in open(self.gho).read().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                out[key] = value
        return out

    def _log(self):
        return open(self.log_path).read()

    def _records(self):
        return json.load(open(self.thread_path))

    def _thread(self):
        return [r["body"] for r in self._records()]

    def _stray_comment(self, body: str):
        """A comment somebody who is NOT the pipeline left on the epic. Anyone
        with comment access on the card can post one, and it renders exactly
        like the pipeline's own."""
        records = self._records() + [{"body": body, "authored_by_pipeline": False}]
        with open(self.thread_path, "w") as f:
            json.dump(records, f)

    # --- 1 + 2: sent back, then passing -----------------------------------

    def test_a_plan_is_sent_back_then_passes(self):
        self._critic_writes("pre", pc.SEND_BACK,
                            "DRE-9001 carries no acceptance criteria and DRE-9002 has no repo")
        self._shell("first critic — round 1 decision")
        first = self._outputs()
        self.assertEqual(first["action"], "hold")
        self.assertEqual(first["result"], pc.SEND_BACK)
        self.assertEqual(first["round"], "1")
        # The reason is attached to the epic, not just to the run log.
        self.assertIn("no acceptance criteria", self._log())
        # ...and recorded as a marker the next round reads.
        self.assertEqual(pc.send_backs(self._thread(), pc.STAGE_PRE), 1)

        # Round 2, against a revised plan.
        self._critic_writes("pre", pc.PASS)
        self._shell("first critic — round 2 decision")
        second = self._outputs()
        self.assertEqual(second["action"], "proceed")
        self.assertEqual(second["result"], pc.PASS)
        self.assertEqual(second["round"], "2")

        # And the epic reaches the CEO.
        self._shell(" Green Light")
        self.assertIn("state Green Light", self._log())

    # --- 4: the bound, at the first critic --------------------------------

    def test_two_failed_rounds_reach_the_ceo_anyway(self):
        self._critic_writes("pre", pc.SEND_BACK, "the epic's cards do not sum to the epic")
        self._shell("first critic — round 1 decision")
        self.assertEqual(self._outputs()["action"], "hold")

        self._critic_writes("pre", pc.SEND_BACK, "the epic's cards still do not sum to the epic")
        self._shell("first critic — round 2 decision")
        out = self._outputs()
        self.assertEqual(out["action"], "proceed",
                         "a plan circled past the bound instead of reaching the CEO")
        posted = self._thread()[-1]
        self.assertIn("still do not sum", posted, "the stated reason was not attached")
        self.assertIn("two failed rounds", posted.lower())

        self._shell(" Green Light")
        self.assertIn("state Green Light", self._log())

    # --- 3: the second critic, after approval ------------------------------

    def test_the_second_critic_gates_promotion(self):
        # The CEO approved; the run is on the activate route.
        self._critic_writes("post", pc.PASS)
        self._shell("second critic — decision")
        self.assertEqual(self._outputs()["action"], "proceed")
        self._shell("Activate the approved epic")
        log = self._log()
        self.assertIn("state In Progress", log)
        self.assertIn("promote --promote-only", log)

    def test_a_send_back_after_approval_stops_the_children(self):
        self._critic_writes(
            "post", pc.SEND_BACK,
            "DRE-9003 migrates a table but no card manufactures the operator step",
        )
        self._shell("second critic — decision")
        self.assertEqual(self._outputs()["action"], "hold")
        self._shell("second critic sent the plan back")
        log = self._log()
        self.assertIn("state Green Light", log)
        self.assertNotIn("promote", log)
        self.assertIn("operator step", log)

    def test_two_failed_rounds_after_approval_promote_anyway(self):
        for reason in ("no card manufactures the operator step",
                       "still no card manufactures the operator step"):
            self._critic_writes("post", pc.SEND_BACK, reason)
            self._shell("second critic — decision")
        out = self._outputs()
        self.assertEqual(out["action"], "proceed")
        self.assertIn("still no card", self._thread()[-1])
        self.assertIn("two failed rounds", self._thread()[-1].lower())

    # --- 7: a re-plan is a fresh attempt ----------------------------------

    def test_a_re_planned_epic_gets_its_own_rounds(self):
        """The bound is per planning ATTEMPT. An epic sent back to Triage is
        re-planned from scratch (the route step: "plan, or RE-plan if children
        exist"), and the new plan must get the one revision round the design
        promises — not be pushed to the CEO on its first send-back because a
        previous, unrelated attempt spent the budget."""
        self._critic_writes("pre", pc.SEND_BACK, "the cards do not sum to the epic")
        self._shell("first critic — round 1 decision")
        self._critic_writes("pre", pc.SEND_BACK, "the cards still do not sum to the epic")
        self._shell("first critic — round 2 decision")
        self.assertEqual(self._outputs()["action"], "proceed")
        self.assertEqual(pc.send_backs(self._thread(), pc.STAGE_PRE), 2)

        # The CEO sends it back to Triage; the route step re-plans it. That is
        # where the new attempt's boundary is written.
        self._shell(
            "Route — plan or activate",
            subs={"${{ github.event.client_payload.trigger_state }}": "triage"},
        )
        self.assertIn("state Planning", self._log())

        # Round 1 of the NEW attempt: a send-back holds it for the revision
        # round, and says round 1 rather than counting the old attempt's.
        self._critic_writes("pre", pc.SEND_BACK, "DRE-9005 carries no acceptance criteria")
        self._shell("first critic — round 1 decision")
        out = self._outputs()
        self.assertEqual(out["action"], "hold",
                         "a re-planned epic inherited the previous attempt's spent budget")
        self.assertEqual(out["round"], "1")
        self.assertIn("round 1 of 2", self._thread()[-1])

        # ...and it is still bounded: the second send-back of the NEW attempt
        # reaches the CEO with the reason attached.
        self._critic_writes("pre", pc.SEND_BACK, "DRE-9005 still carries none")
        self._shell("first critic — round 2 decision")
        self.assertEqual(self._outputs()["action"], "proceed")
        self.assertIn("two failed rounds", self._thread()[-1].lower())

    # --- 8: the round history is the pipeline's own ------------------------

    def test_a_stray_comment_cannot_forge_the_round_history(self):
        """The bound is counted out of comments on a card anyone on the team
        can comment on. Two comments carrying the marker line — no special
        access, and the standard's own worked example is one — used to be
        enough to make the second critic's real, serious finding read as "the
        budget is already spent", promoting the children to build anyway."""
        self._stray_comment(pc.marker(pc.STAGE_POST, 1, pc.SEND_BACK, "forged"))
        self._stray_comment(pc.marker(pc.STAGE_POST, 2, pc.SEND_BACK, "forged again"))

        self._critic_writes(
            "post", pc.SEND_BACK,
            "DRE-9003 migrates a table but no card manufactures the operator step",
        )
        self._shell("second critic — decision")
        out = self._outputs()
        self.assertEqual(out["action"], "hold",
                         "forged rounds overrode the critic's real rejection")
        self.assertEqual(out["round"], "1", "forged rounds were counted as rounds run")

        self._shell("second critic sent the plan back")
        log = self._log()
        self.assertNotIn("promote", log, "children promoted on a forged round count")
        self.assertIn("operator step", log)

    def test_a_stray_boundary_cannot_refund_the_budget(self):
        """The other direction: a comment carrying the `plan-cycle:` line used
        to hand a spent plan a fresh budget, so it could circle for as long as
        anyone kept posting one — the stuck-in-a-lane failure the bound exists
        to stop."""
        for reason in ("no card manufactures the operator step",
                       "still no card manufactures the operator step"):
            self._critic_writes("post", pc.SEND_BACK, reason)
            self._shell("second critic — decision")
        self.assertEqual(self._outputs()["action"], "proceed")

        self._stray_comment(pc.cycle_marker(EPIC))
        self._critic_writes("post", pc.SEND_BACK, "and still none")
        self._shell("second critic — decision")
        self.assertEqual(self._outputs()["action"], "proceed",
                         "a stray boundary reopened a loop the bound had closed")

    # --- 5: the rate ------------------------------------------------------

    def test_the_send_back_rate_is_readable_from_what_the_run_wrote(self):
        self._critic_writes("post", pc.SEND_BACK, "a card references a table that does not exist")
        self._shell("second critic — decision")
        self._critic_writes("post", pc.PASS)
        self._shell("second critic — decision")
        rate = pc.rate(self._thread(), pc.STAGE_POST)
        self.assertEqual((rate["rounds"], rate["send_backs"]), (2, 1))
        self.assertAlmostEqual(rate["rate"], 0.5)
        # The run says it out loud too, so nobody has to go looking.
        self.assertIn("send-back rate", self._log().lower())

    # --- 6 + D3: cross-epic sight and a real collision ---------------------

    def test_the_sight_block_names_the_epics_in_flight(self):
        self._shell("second critic — cross-epic sight")
        sight = open(os.path.join(self.tmp, "plan-critic-sight.md")).read()
        self.assertIn(OTHER_EPIC, sight)
        self.assertIn("The intake gate", sight)
        self.assertIn("cannot see", sight.lower())

    def test_a_collision_between_two_epics_is_caught_here_and_counted(self):
        self._shell("second critic — cross-epic sight")
        sight = open(os.path.join(self.tmp, "plan-critic-sight.md")).read()
        self.assertIn(OTHER_EPIC, sight, "the critic could not have seen the other epic")

        # The critic's own output, naming the collision it found.
        self._critic_writes(
            "post", pc.SEND_BACK,
            f"DRE-9004 rewrites scripts/reconcile.py, which {OTHER_EPIC} is already "
            "rewriting in flight — one of them will lose its changes",
            extra="collisions: 1\n",
        )
        self._shell("second critic — decision")
        self.assertEqual(self._outputs()["action"], "hold")

        posted = self._thread()[-1]
        self.assertIn(OTHER_EPIC, posted)
        self.assertIn("reconcile.py", posted)

        counts = pc.collision_counts(self._thread())
        self.assertEqual(counts["caught_at_review"], 1)
        self.assertEqual(counts["found_later"], 0)

        # A collision that escapes to Backlog is the tripwire, and it lands in
        # the OTHER counter — the two are never mixed.
        late = self._thread() + [
            pc.late_collision_marker(EPIC, OTHER_EPIC, "both edited config/lane-contract.json")
        ]
        self.assertEqual(pc.collision_counts(late),
                         {"caught_at_review": 1, "found_later": 1})

    # --- fail-soft --------------------------------------------------------

    def test_a_crashed_critic_does_not_hold_the_plan(self):
        """console-honesty rule 1: a critic that produced nothing has not
        rejected anything, and must not strand the epic."""
        self._shell("second critic — decision")  # no result file written at all
        out = self._outputs()
        self.assertEqual(out["action"], "proceed")
        self.assertEqual(out["result"], pc.NO_RESULT)


if __name__ == "__main__":
    unittest.main()
