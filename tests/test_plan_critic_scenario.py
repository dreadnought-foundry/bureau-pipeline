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
  9. ...and authorship alone is not the credential either. The planner posts
     its plan write-up to this same thread with this same key; a marker or a
     boundary quoted INSIDE that prose records nothing, because the run writes
     every record as a comment of its own and reads back nothing else.
 10. A post-approval review that DIES re-runs itself once, at a higher
     ceiling, with no lane move — and only a SECOND death parks the epic for
     an operator, naming both dead runs (DRE-3289).
 11. A post-approval SEND-BACK whose re-plan changed no card SET re-runs the
     review itself too; only a re-plan that ADDED or REMOVED a card parks in
     Green Light, naming the card (DRE-3291) — and the whole DRE-3257 shape
     (send-back → re-plan → death → retry → pass) reaches activation without
     one human lane move after the first approval.
 12. A round that found THREE things reports all three in one pass (DRE-3251):
     one marker carrying the first line, a note carrying every finding with
     its cards, and a step output the re-plan's prompt reads as a list.

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
import plan_seam  # noqa: E402  — DRE-3395's reader, for the seam walk's input

SEAM_FIXTURE = os.path.join(ROOT, "tests", "fixtures",
                            "dre-3164-children-2026-09-05.json")

EPIC = "DRE-2721"
OTHER_EPIC = "DRE-2700"

# Linear's own stamp on every comment the stub records, and the one the
# previous round's charter clock is read off (DRE-4115). Passed to the stub
# through the environment so the walk and the thread it reads agree on it.
STUB_NOW = "2026-09-15T17:43:00.000Z"

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
#
# It is also IMPORTED, not only executed: `review_rerun.py dispatch` reads the
# epic through `linear_ops.gql` before it asks for the run (DRE-3289), so the
# stub has to survive `import linear_ops` — which means the command switch sits
# behind `__main__` and `gql` answers `plan_run.CARD_QUERY` the way Linear does.
LINEAR_STUB = '''#!/usr/bin/env python3
import json, os, sys

EPIC = "DRE-2721"
# Linear's own stamp on a comment, which `dump-comments --with-authors` carries
# (DRE-3754) and the previous round's charter clock is read off (DRE-4115). One
# fixed value: what these walks check is that it ARRIVES, not what it says.
STUB_NOW = os.environ.get("STUB_NOW") or "2026-09-15T17:43:00.000Z"


def thread():
    try:
        with open(os.environ["STUB_THREAD"]) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def log(line):
    with open(os.environ["STUB_LOG"], "a") as f:
        f.write(line + "\\n")


def gql(query, variables=None):
    """The one read `plan_run.CARD_QUERY` makes — an epic, with the planner
    label that decides the dispatch event."""
    log("gql " + json.dumps(variables or {}, sort_keys=True))
    return {"issue": {
        "id": "uuid-" + EPIC,
        "identifier": EPIC,
        "title": "Two critics",
        "description": "the approved plan",
        "labels": {"nodes": [{"name": "agent:planner"}, {"name": "repo:bureau-pipeline"}]},
        "children": {"nodes": [{"identifier": "DRE-9001"}]},
    }}


def main():
    cmd, *args = sys.argv[1:]
    if cmd == "dump-comments":
        records = thread()
        if "--with-authors" in args:
            print(json.dumps(records))
        else:
            print(json.dumps([r["body"] for r in records]))
    elif cmd == "comment":
        records = thread() + [{"body": args[1], "authored_by_pipeline": True,
                               "created_at": STUB_NOW}]
        with open(os.environ["STUB_THREAD"], "w") as f:
            json.dump(records, f)
        log("comment " + args[1].replace("\\n", " | "))
    elif cmd == "state":
        log("state " + " ".join(args[1:]))
    elif cmd == "add-label":
        log("add-label " + args[1])
    elif cmd == "children":
        print(os.environ.get("STUB_KIDS", "4"))
    elif cmd == "children-json":
        # The identifier set the card-set diff is computed over. STUB_CARDS is
        # a comma-separated list so a walk can add or remove one card between
        # the before- and after-snapshots the re-plan sits between.
        cards = os.environ.get("STUB_CARDS", "DRE-9001,DRE-9002").split(",")
        print(json.dumps([{"identifier": c, "body": "", "labels": [],
                           "parent": EPIC} for c in cards if c]))
    elif cmd == "children-detail":
        # The fuller record the routing-verdict stamper reads (DRE-4593). Same
        # card set as `children-json`, with the three fields that read pulls.
        cards = os.environ.get("STUB_CARDS", "DRE-9001,DRE-9002").split(",")
        print(json.dumps([{"identifier": c, "title": c, "body": "",
                           "labels": [], "blocked_by": []} for c in cards if c]))
    elif cmd == "epics-in-flight":
        print(os.environ.get("STUB_EPICS", "[]"))
    else:
        sys.exit("stub linear_ops: unhandled command " + cmd)


if __name__ == "__main__":
    main()
'''

RECONCILE_STUB = '''#!/usr/bin/env python3
import os, sys
with open(os.environ["STUB_LOG"], "a") as f:
    f.write("promote " + " ".join(sys.argv[1:]) + "\\n")
'''

# The routing-verdict stamper (DRE-4593), stubbed for the same reason reconcile
# is: these walks are about the two critics and what they gate, and the stamper
# is exercised by tests/test_planner_stamps_children.py against a fake Linear.
# What the walk has to see is that it RAN, and that it ran before the promotion
# below it — a verdict written after the promoter has already read the card
# would be a verdict that arrived too late.
CHILD_VERDICT_STUB = '''#!/usr/bin/env python3
import os, sys
sys.stdin.read()
with open(os.environ["STUB_LOG"], "a") as f:
    f.write("stamp-verdicts " + " ".join(sys.argv[1:]) + "\\n")
'''

# `gh`, on PATH, for the ONE vendor call this walk makes: the
# `repos/<owner>/<name>/dispatches` POST `plan_run.fire` shells out to. It
# records the payload verbatim so the walk can read the two keys the ACTIVATE
# route turns on, and honours STUB_GH_RC so a 403'd dispatch is walkable too.
GH_STUB = '''#!/usr/bin/env python3
import os, sys

args = sys.argv[1:]
payload = ""
if "--input" in args:
    with open(args[args.index("--input") + 1]) as f:
        payload = f.read()
rc = int(os.environ.get("STUB_GH_RC", "0"))
with open(os.environ["STUB_LOG"], "a") as f:
    f.write("gh " + " ".join(a for a in args if not a.startswith("/")) + "\\n")
    # Only an rc=0 call is a dispatch that happened. A 403 leaves the attempt
    # in the log and nothing the walk can read as a run on its way.
    f.write(("dispatch " if rc == 0 else "dispatch-failed ") + payload + "\\n")
if rc:
    sys.stderr.write("HTTP 403: Resource not accessible by integration\\n")
sys.exit(rc)
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
        # plan_footprint.py: the declared-footprint parser plan_critic imports
        # (DRE-3040) — the walk runs the real module, so it needs the real
        # dependency beside it.
        # checkbox_marks.py: the one table of criterion marks both of those
        # read (DRE-3147) — same reason, one module further down.
        # review_rerun.py / plan_run.py: the retry contract the dead-review
        # step now runs (DRE-3286, wired by DRE-3289) and the dispatcher it
        # fires through — real modules, because what this walk is checking is
        # the two payload keys they send.
        # sanitize_untrusted.py: the heredoc writer a multi-finding round's
        # list goes out through (DRE-3251) — imported late, so only walk 12
        # reaches it, and it reaches the REAL one because what that walk
        # checks is the block GitHub would have to parse.
        # plan_seam_gate.py: the seam gate both pre-stage decision steps now run
        # before `decide` (DRE-3398) — the real module, because what these walks
        # check is that a round with no seam decides exactly as it always did.
        for name in ("plan_critic.py", "design_parity.py", "plan_footprint.py",
                     "checkbox_marks.py", "execution_result.py",
                     "review_rerun.py", "plan_run.py", "sanitize_untrusted.py",
                     "plan_seam_gate.py"):
            shutil.copy(os.path.join(SCRIPTS, name),
                        os.path.join(self.pipeline, "scripts", name))
        self._stub("linear_ops.py", LINEAR_STUB)
        self._stub("reconcile.py", RECONCILE_STUB)
        self._stub("plan_child_verdicts.py", CHILD_VERDICT_STUB)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        gh = os.path.join(self.bin, "gh")
        with open(gh, "w") as f:
            f.write(GH_STUB)
        os.chmod(gh, 0o755)
        self.thread_path = os.path.join(self.tmp, "thread.json")
        self.log_path = os.path.join(self.tmp, "log.txt")
        self.gho = os.path.join(self.tmp, "step-output")
        # The mechanical step writes this before either decision reads it, and
        # the gate REFUSES an absent one — a seam check that never ran is not
        # the same fact as no seam. Empty is the ordinary case these walks are
        # about: a plan with no seam in it.
        self.structural = os.path.join(self.tmp, "plan-structural.txt")
        for path, seed in ((self.thread_path, "[]"), (self.log_path, ""),
                           (self.gho, ""), (self.structural, "")):
            with open(path, "w") as f:
                f.write(seed)

    def _stub(self, name, body):
        path = os.path.join(self.pipeline, "scripts", name)
        with open(path, "w") as f:
            f.write(body)
        os.chmod(path, 0o755)

    # --- the seams --------------------------------------------------------

    def _shell(self, fragment: str, subs: dict | None = None, expect_rc: int = 0,
               **env_extra):
        """Run a plan.yml step's shell with the run's expressions resolved.

        `expect_rc` for the one branch that is SUPPOSED to leave the step red:
        a re-review dispatch that did not go through hands the run to the medic
        rather than leaving the epic with nothing scheduled (DRE-3291)."""
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
            PATH=self.bin + os.pathsep + os.environ["PATH"],
            STUB_THREAD=self.thread_path,
            STUB_LOG=self.log_path,
            STUB_EPICS=json.dumps(EPICS_IN_FLIGHT),
            STUB_NOW=STUB_NOW,
            GITHUB_OUTPUT=self.gho,
            GITHUB_REPOSITORY="dreadnought-foundry/bureau-pipeline",
            MAX_WIP="8",
            LINEAR_API_KEY="test-key",
        )
        env.update(env_extra)
        out = subprocess.run(["bash", "-e", "-c", script], cwd=self.tmp,
                             capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, expect_rc, out.stdout + out.stderr)
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

    def _note(self):
        """The CEO-facing half of the last round. Each round posts TWO comments
        — this one, then the record alone — so a comment carrying prose can
        never also carry the credential the bound is counted from."""
        return self._thread()[-2]

    def _record(self):
        """The machine half: nothing but the marker line."""
        return self._thread()[-1]

    def _pipeline_comment(self, body: str):
        """A comment the PIPELINE wrote that is not a round record — the
        planner's plan write-up is the real one, posted to the same epic
        through the same `linear_ops.py comment` call and the same key."""
        records = self._records() + [{"body": body, "authored_by_pipeline": True}]
        with open(self.thread_path, "w") as f:
            json.dump(records, f)

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

    # --- 3: the seam a round cannot pass over (DRE-3398) -------------------

    def test_a_pass_over_a_seam_is_a_send_back_at_the_first_critic(self):
        """The whole chain through the real step shell: the structural file the
        mechanical step leaves behind, the gate that rewrites the result file,
        and `decide` — unedited — turning the critic's PASS into the round's
        send-back with the seam as its reason.

        The sentence is DRE-3395's reading of DRE-3395's fixture and never a
        literal here, so the two cards cannot silently disagree about it.
        """
        with open(SEAM_FIXTURE) as f:
            lines = plan_seam.seam_findings(json.load(f))
        self.assertEqual(len(lines), 1, "the fixture stopped yielding one seam")
        with open(self.structural, "w") as f:
            f.write(lines[0] + "\n")

        self._critic_writes("pre", pc.PASS)
        self._shell("first critic — round 1 decision")
        out = self._outputs()
        self.assertEqual(out["action"], "hold",
                         "a two-epic plan passed the round the critic passed")
        self.assertEqual(out["result"], pc.SEND_BACK)
        self.assertIn(lines[0], out["reason"])
        # The CEO reads it, and the bound counts it — the record is whatever
        # `decide` writes for any send-back.
        self.assertIn("🛑", self._note())
        self.assertIn(lines[0], self._note())
        self.assertIn(lines[0], self._record())
        self.assertEqual(pc.send_backs(self._thread(), pc.STAGE_PRE), 1)

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
        self.assertIn("still do not sum", self._note(),
                      "the stated reason was not attached")
        self.assertIn("two failed rounds", self._note().lower())
        self.assertIn("still do not sum", self._record())

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
        # DRE-4593: the children's routing verdicts are written on the way
        # through, before anything is promoted. A child reaching the promoter
        # with no verdict is refused and sits — five of DRE-4425's for about 35
        # hours, all seven of DRE-4467's until a person stamped them by hand.
        self.assertIn("stamp-verdicts stamp --epic DRE-2721", log)
        self.assertLess(log.index("stamp-verdicts"), log.index("promote "))
        self.assertLess(log.index("stamp-verdicts"), log.index("state In Progress"))

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

    def test_a_send_back_after_approval_asks_for_re_approval_in_progress(self):
        """DRE-3088: the receipt names the approval move — In Progress — and
        never Todo, where an epic dispatches nothing (DRE-2725). And it says
        what the re-plan changed, because that is what the CEO reads before
        approving the revised plan."""
        self._critic_writes("post", pc.SEND_BACK, "no card manufactures the operator step")
        self._shell("second critic — decision")
        out = self._outputs()
        self.assertEqual(out["action"], "hold")
        self.assertEqual(out["bound"], "false")
        with open(os.path.join(self.tmp, "post-replan-summary.md"), "w") as f:
            f.write("Added DRE-9004, the operator step for the migration.")
        self._shell("second critic sent the plan back", BOUND="false",
                    FINDING=out["reason"], REPLAN_OUTCOME="success")
        receipt = self._thread()[-1]
        self.assertIn("state Green Light", self._log())
        self.assertNotIn("add-label", self._log(), "round 1 is a revision, not a park")
        self.assertIn("In Progress", receipt)
        self.assertNotIn("Todo", receipt)
        self.assertIn("Added DRE-9004", receipt)
        self.assertIn("no card manufactures the operator step", receipt)

    def test_a_dead_re_plan_still_parks_the_epic_in_green_light(self):
        """The lane move never depends on the re-plan finishing."""
        self._critic_writes("post", pc.SEND_BACK, "no card manufactures the operator step")
        self._shell("second critic — decision")
        self._shell("second critic sent the plan back", BOUND="false",
                    FINDING="no card manufactures the operator step", REPLAN_OUTCOME="failure")
        self.assertIn("state Green Light", self._log())
        self.assertIn("did not finish", self._thread()[-1])

    # --- DRE-3291: a re-plan that changed no card set re-reviews itself -----

    def _replan(self, before: str, after: str) -> dict:
        """The two snapshots the re-plan sits between, and the diff over them.

        `before` and `after` are the child identifier sets, comma-separated —
        the only thing this branch turns on."""
        self._shell("children before the re-plan", STUB_CARDS=before)
        self._shell("re-plan — did the card set change?", STUB_CARDS=after)
        return self._outputs()

    def _sent_back(self, cards: dict, expect_rc: int = 0, **env_extra):
        """The park/re-review step, fed the outputs the two steps above wrote."""
        env = dict(BOUND="false", REPLAN_OUTCOME="success",
                   SET_CHANGED=cards.get("changed", ""),
                   ADDED=cards.get("added", ""),
                   REMOVED=cards.get("removed", ""))
        env.update(env_extra)
        return self._shell("second critic sent the plan back",
                           expect_rc=expect_rc, **env)

    def _hold(self, reason: str) -> dict:
        self._critic_writes("post", pc.SEND_BACK, reason)
        self._shell("second critic — decision")
        out = self._outputs()
        self.assertEqual(out["action"], "hold")
        return out

    def _summary(self, text: str):
        with open(os.path.join(self.tmp, "post-replan-summary.md"), "w") as f:
            f.write(text)

    def test_a_re_plan_that_kept_the_card_set_re_runs_the_review_itself(self):
        """The whole point of DRE-3291. The re-plan rewrote a card the CEO has
        already read; there is no new shape and no decision, so the epic does
        not move and the run asks for the review itself."""
        out = self._hold("DRE-9002 migrates a table but no card manufactures "
                         "the operator step")
        cards = self._replan("DRE-9001,DRE-9002", "DRE-9001,DRE-9002")
        self.assertEqual(cards["changed"], "false")
        self._summary("Rewrote DRE-9002 so it names who runs the migration.")
        self._sent_back(cards, FINDING=out["reason"])

        log = self._log()
        self.assertNotIn("state ", log, "the CEO was asked to approve again")
        self.assertNotIn("add-label", log)
        self.assertNotIn("promote", log)

        sent = self._dispatches()
        self.assertEqual(len(sent), 1, log)
        payload = sent[0]["client_payload"]
        self.assertEqual(payload["trigger_state"], "in progress")
        self.assertEqual(payload["reason"], "re-review")
        self.assertEqual(payload["identifier"], EPIC)

        notice = self._thread()[-1]
        self.assertIn("round 2", notice)
        self.assertNotIn("of 2", notice, "round N of 2 is the contradiction DRE-4115 cites")
        self.assertIn("names who runs the migration", notice)
        self.assertIn("no card manufactures the operator step", notice)
        self.assertNotIn(pc.REAPPROVE_HOW, notice)
        self.assertNotIn(pc.APPROVAL_LANE, notice,
                         "nothing here is the CEO's to move")

    def test_a_re_plan_that_added_a_card_parks_in_green_light_naming_it(self):
        out = self._hold("no card manufactures the operator step")
        cards = self._replan("DRE-9001,DRE-9002", "DRE-9001,DRE-9002,DRE-9004")
        self.assertEqual(cards["changed"], "true")
        self.assertEqual(cards["added"], "DRE-9004")
        self.assertEqual(cards["removed"], "")
        self._summary("Added DRE-9004, the operator step for the migration.")
        self._sent_back(cards, FINDING=out["reason"])

        log = self._log()
        self.assertIn("state Green Light", log)
        self.assertNotIn("add-label", log, "round 1 is a revision, not a park")
        self.assertEqual(self._dispatches(), [],
                         "a shape the CEO has not seen is his to read")
        notice = self._thread()[-1]
        self.assertIn("DRE-9004", notice)
        self.assertIn("yours to decide", notice)
        self.assertIn(pc.APPROVAL_LANE, notice)
        self.assertNotIn("Todo", notice)

    def test_a_re_plan_that_removed_a_card_parks_in_green_light_naming_it(self):
        out = self._hold("DRE-9002 duplicates work DRE-9001 already does")
        cards = self._replan("DRE-9001,DRE-9002", "DRE-9001")
        self.assertEqual(cards["changed"], "true")
        self.assertEqual(cards["removed"], "DRE-9002")
        self.assertEqual(cards["added"], "")
        self._summary("Dropped DRE-9002 — DRE-9001 already covers it.")
        self._sent_back(cards, FINDING=out["reason"])

        self.assertIn("state Green Light", self._log())
        self.assertEqual(self._dispatches(), [])
        notice = self._thread()[-1]
        self.assertIn("DRE-9002", notice)
        self.assertIn("removed", notice)

    def test_a_missing_snapshot_reads_as_changed_and_parks(self):
        """Unknown is unknown (console-honesty rule 2). The before-snapshot is
        best-effort, and a re-plan whose shape we cannot report is one the CEO
        reads rather than one the pipeline re-reviews behind him."""
        out = self._hold("no card manufactures the operator step")
        # No `children before the re-plan` step ran at all.
        self._shell("re-plan — did the card set change?",
                    STUB_CARDS="DRE-9001,DRE-9002")
        cards = self._outputs()
        self.assertEqual(cards["changed"], "true")
        self._sent_back(cards, FINDING=out["reason"])
        self.assertIn("state Green Light", self._log())
        self.assertEqual(self._dispatches(), [])

    def test_a_re_plan_that_did_not_finish_still_parks_in_green_light(self):
        """The lane move never depends on the re-plan finishing — even when
        the card set is unchanged BECAUSE the re-plan never edited anything."""
        out = self._hold("no card manufactures the operator step")
        cards = self._replan("DRE-9001,DRE-9002", "DRE-9001,DRE-9002")
        self.assertEqual(cards["changed"], "false")
        self._sent_back(cards, FINDING=out["reason"], REPLAN_OUTCOME="failure")
        self.assertIn("state Green Light", self._log())
        self.assertEqual(self._dispatches(), [],
                         "a plan nothing revised must not be re-reviewed as revised")
        self.assertIn("did not finish", self._thread()[-1])

    def test_a_failed_re_review_dispatch_is_said_and_leaves_the_run_red(self):
        """DRE-2034 at this seam. A 403'd dispatch must not leave the epic
        reading as though a run were on its way — and because no lane was
        written, the step goes red so the medic picks the run up rather than
        the epic sitting In Progress with nothing scheduled."""
        out = self._hold("no card manufactures the operator step")
        cards = self._replan("DRE-9001,DRE-9002", "DRE-9001,DRE-9002")
        self._sent_back(cards, FINDING=out["reason"], expect_rc=1,
                        STUB_GH_RC="1")
        self.assertEqual(self._dispatches(), [])
        self.assertIn("could NOT", self._thread()[-1])
        self.assertNotIn("state ", self._log())
        for body in self._thread():
            self.assertNotIn("🔁", body, self._thread())
            self.assertNotIn("being run again", body, self._thread())

    def test_the_bound_parks_even_when_the_card_set_is_unchanged(self):
        """The bound is read FIRST. Two real send-backs park for a person
        whatever the re-plan did to the cards — "the bound still parks" is out
        of this card's scope and must stay true."""
        self._critic_writes("post", pc.SEND_BACK, "no card manufactures the operator step")
        self._shell("second critic — decision")
        self._critic_writes("post", pc.SEND_BACK,
                            "still no card manufactures the operator step",
                            extra="still-open: 1\n")
        self._shell("second critic — decision")
        self.assertEqual(self._outputs()["bound"], "true")
        cards = self._replan("DRE-9001,DRE-9002", "DRE-9001,DRE-9002")
        self.assertEqual(cards["changed"], "false")
        self._sent_back(cards, BOUND="true", FINDING="still no card")
        log = self._log()
        self.assertIn("state Green Light", log)
        self.assertIn("add-label needs-human", log)
        self.assertEqual(self._dispatches(), [],
                         "the bound bought a third review")

    # --- DRE-4115: an answered round stops counting against the next --------
    #
    # The defect, live on DRE-3778 and DRE-4198 (2026-09-19): the post-approval
    # bound counted SEND_BACK markers since a `plan-cycle:` boundary that only
    # the PLAN route ever wrote, and never asked whether the re-plan between two
    # rounds had answered the first one. Once a plan had been sent back twice
    # in its life, every later approval parked it on arithmetic alone — "round
    # 6 of 2" — with the park note telling the operator to take the very act
    # that re-parks it.

    def _critic_says(self, reason: str, further: list[str] = (),
                     still_open: str | None = None):
        """A post-stage SEND_BACK carrying every finding of the round and,
        when the critic was shown a previous round, its reading of which of
        THOSE the revision left open — `still-open: none` or the numbers."""
        extra = "\n".join(f"{i}. {f}" for i, f in enumerate(further, 2))
        if still_open is not None:
            extra += f"\n\nstill-open: {still_open}\n"
        self._critic_writes("post", pc.SEND_BACK, reason, extra)
        self._shell("second critic — decision")
        out = self._outputs()
        self.assertEqual(out["action"], "hold")
        return out

    def _re_review(self, out: dict) -> dict:
        """The same-cards branch after a hold: the re-plan kept the card set,
        the run asks for the review itself. Returns the decision outputs."""
        cards = self._replan("DRE-9001,DRE-9002", "DRE-9001,DRE-9002")
        self.assertEqual(cards["changed"], "false")
        self._summary("Rewrote the cards the critic named.")
        self._sent_back(cards, FINDING=out["reason"], BOUND=out["bound"],
                        NOTE=out["note"])
        return out

    def test_a_revision_that_answered_every_finding_is_not_parked_on_arithmetic(self):
        """THE REGRESSION (DRE-4115 AC4). Round 1 sends the plan back with two
        findings; the re-plan answers both; round 2 finds one NEW gap and says
        so. That is one open finding on a revised plan — round 1 of the
        revision's own life, not "two failed rounds" — so the epic is NOT
        parked: the plan is revised again and the review re-runs itself, and
        no human is asked to approve a plan whose cards they already read."""
        first = self._critic_says("DRE-9002 migrates a table but no card manufactures the operator step",
                                  ["DRE-9001 carries no acceptance criteria"])
        self.assertEqual(first["bound"], "false")
        self._re_review(first)
        self.assertEqual([p["client_payload"]["reason"] for p in self._dispatches()],
                         ["re-review"])

        # Round 2, on the revised plan: a NEW finding, and every finding of
        # round 1 answered.
        second = self._critic_says("DRE-9001 references a config key nothing creates",
                                   still_open="none")
        self.assertEqual(second["bound"], "false",
                         "an answered round was counted against the next one")
        self.assertNotIn("the bound", second["note"].lower())
        self.assertIn("answered", second["note"].lower())
        self.assertNotIn("of 2", self._note(),
                         "round N of 2 is the contradiction the card cites")

        self._re_review(second)
        log = self._log()
        self.assertNotIn("add-label needs-human", log, "parked on arithmetic alone")
        self.assertNotIn("state Green Light", log)
        self.assertEqual([p["client_payload"]["reason"] for p in self._dispatches()],
                         ["re-review", "re-review"],
                         "the revised plan gets its own review")
        # ...and the sweep's gate holds the children exactly as before — a
        # send-back the revision has not yet been reviewed for is not a release.
        state, _ = pc.post_release(self._thread(), EPIC)
        self.assertEqual(state, pc.POST_HELD)

    def test_a_revision_that_left_a_finding_open_parks_and_names_it(self):
        """DRE-4115 AC2, the other half: sent back twice and STILL not fixed
        is the bound, and the park note says WHICH findings remain open rather
        than reporting a count."""
        first = self._critic_says("no card manufactures the operator step",
                                  ["DRE-9001 carries no acceptance criteria"])
        self._re_review(first)
        second = self._critic_says("DRE-9002 still names nobody to run the migration",
                                   still_open="1")
        self.assertEqual(second["bound"], "true")
        self.assertIn("no card manufactures the operator step", second["open"])
        self.assertNotIn("carries no acceptance criteria", second["open"])
        self.assertIn("still open", second["note"].lower())
        self.assertIn("no card manufactures the operator step", second["note"])

        self._re_review(second)
        log = self._log()
        self.assertIn("state Green Light", log)
        self.assertIn("add-label needs-human", log)
        self.assertEqual(len(self._dispatches()), 1, "the bound bought a third review")
        receipt = self._thread()[-1]
        self.assertIn("no card manufactures the operator step", receipt)
        self.assertIn("still open", receipt.lower())
        self.assertIn(pc.REAPPROVE_HOW, receipt)
        # The act the note names buys a real budget, and the note says so.
        self.assertIn("fresh planning attempt", receipt)
        self.assertNotIn("Todo", receipt)
        state, detail = pc.post_release(self._thread(), EPIC)
        self.assertEqual(state, pc.POST_HELD)
        self.assertIn("needs-human", detail)
        self.assertIn("no card manufactures the operator step", detail)

    def test_a_plan_that_keeps_growing_new_gaps_still_reaches_a_person(self):
        """The gate is not weakened. A plan revised MAX_ROUNDS times, each
        revision answering everything the critic named, that STILL comes back
        with new findings is not converging — a person reads it rather than
        the pipeline paying for a fourth review. Two answered rounds is the
        budget; the third send-back parks whatever it found."""
        first = self._critic_says("no card manufactures the operator step")
        self._re_review(first)
        second = self._critic_says("DRE-9001 references a config key nothing creates",
                                   still_open="none")
        self.assertEqual(second["bound"], "false")
        self._re_review(second)
        third = self._critic_says("DRE-9002 assumes a route DRE-9001 does not add",
                                  still_open="none")
        self.assertEqual(third["bound"], "true", "a plan can circle forever")
        self.assertIn("new gaps", third["note"].lower())
        self.assertIn("DRE-9002 assumes a route", third["note"])
        self._re_review(third)
        log = self._log()
        self.assertIn("add-label needs-human", log)
        self.assertEqual(len(self._dispatches()), 2)
        state, detail = pc.post_release(self._thread(), EPIC)
        self.assertEqual(state, pc.POST_HELD)
        self.assertIn("needs-human", detail)

    def test_a_human_re_run_at_the_bound_opens_a_fresh_planning_attempt(self):
        """DRE-4115 AC1, the half that is safe: an operator who clears
        needs-human and re-runs the review — the act, or the approval move
        from Green Light — has settled the plan the park asked about, so the
        review that follows judges the settled plan on its own rounds. Every
        reset costs a person's act, so the loop still ends. On today's code
        the ACTIVATE route wrote no boundary, so DRE-3778 came back at round
        5, then 6, after five approvals."""
        first = self._critic_says("no card manufactures the operator step")
        self._re_review(first)
        second = self._critic_says("DRE-9002 still names nobody to run the migration",
                                   still_open="1")
        self.assertEqual(second["bound"], "true")
        self._re_review(second)
        self.assertIn("add-label needs-human", self._log())
        self.assertTrue(pc.post_bound_reached(self._thread(), EPIC))

        # The operator clears needs-human and posts the act; the relay
        # dispatches the ACTIVATE route with `reason: re-run`.
        self._shell("Route — plan or activate",
                    subs={"${{ github.event.client_payload.trigger_state }}": "in progress"},
                    REASON="re-run")
        self.assertEqual(self._outputs()["mode"], "activate")
        self.assertNotIn("state Planning", self._log(), "the cards were not re-planned")
        self.assertEqual(self._record(), pc.cycle_marker(EPIC),
                         "the human re-run opened no fresh attempt")
        self.assertFalse(pc.post_bound_reached(self._thread(), EPIC))

        # Round 1 of the fresh attempt: a send-back holds for a revision and
        # says round 1, not round 3 — and a PASS activates.
        third = self._critic_says("DRE-9001 references a config key nothing creates")
        self.assertEqual((third["round"], third["bound"]), ("1", "false"))
        self.assertIn("round 1", self._note())

    def test_the_ceo_approving_a_parked_epic_opens_a_fresh_attempt_too(self):
        """The other trigger the relay has: the approval move INTO In Progress,
        which carries no `reason` at all. Same act by a person, same fresh
        attempt."""
        first = self._critic_says("no card manufactures the operator step")
        self._re_review(first)
        second = self._critic_says("DRE-9002 still names nobody to run the migration",
                                   still_open="1")
        self._re_review(second)
        self._shell("Route — plan or activate",
                    subs={"${{ github.event.client_payload.trigger_state }}": "in progress"},
                    REASON="")
        self.assertEqual(self._record(), pc.cycle_marker(EPIC))

    def test_the_pipelines_own_re_review_opens_no_attempt(self):
        """The pipeline asking for its OWN re-review, or retrying a dead
        review, is not a person settling anything — a boundary there would
        let a plan circle forever, and would hide the tombstone the retry
        ceiling is sized from. Round 1's send-back stands, the re-review is
        round 2, and the answered/open reading is what decides it."""
        first = self._critic_says("no card manufactures the operator step")
        self._re_review(first)
        before = self._thread()
        for reason in ("re-review", "review-retry"):
            self._shell("Route — plan or activate",
                        subs={"${{ github.event.client_payload.trigger_state }}": "in progress"},
                        REASON=reason)
            self.assertEqual(self._outputs()["mode"], "activate")
            self.assertEqual(self._thread(), before, f"{reason} wrote a boundary")

    def test_a_first_approval_opens_no_attempt_either(self):
        """An approval with no post round behind it has nothing to reset: the
        plan route's boundary is the attempt, and a second one would cut the
        first critic's rounds out of the rate the console reads."""
        self._shell("Route — plan or activate",
                    subs={"${{ github.event.client_payload.trigger_state }}": "in progress"},
                    REASON="")
        self.assertEqual(self._outputs()["mode"], "activate")
        self.assertEqual(self._thread(), [])

    def test_the_second_critic_is_shown_the_previous_rounds_findings(self):
        """The reading the bound now turns on is the critic's, so the critic
        has to be SHOWN what the previous round found — numbered, so
        `still-open: 1, 3` means something — and told to write the line. A
        first round is shown nothing and asked for nothing."""
        self._shell("second critic — the previous round")
        prior = open(os.path.join(self.tmp, "plan-critic-prior.md")).read()
        self.assertEqual(prior.strip(), "")

        first = self._critic_says("no card manufactures the operator step",
                                  ["DRE-9001 carries no acceptance criteria"])
        self._re_review(first)
        self._shell("second critic — the previous round")
        prior = open(os.path.join(self.tmp, "plan-critic-prior.md")).read()
        self.assertIn("1. no card manufactures the operator step", prior)
        self.assertIn("2. DRE-9001 carries no acceptance criteria", prior)
        self.assertIn("still-open:", prior)
        # ...and it says WHEN that round ran, off the record's own stamp. The
        # other half of the reading: a plan is older than the estate it is read
        # against, and a change that landed since is a note for the planner
        # rather than a strike. Asserted here, on the file the step actually
        # writes, because the block's own unit test is handed the clock
        # directly and cannot see a caller that never supplies one.
        self.assertIn("That round ran at", prior)
        self.assertIn(pc._pt_clock(STUB_NOW), prior)
        # ...and it reaches the charter the critic reads first.
        charter = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "plan_critic.py"), "charter", "post",
             "--prior-file", os.path.join(self.tmp, "plan-critic-prior.md")],
            capture_output=True, text=True, check=True).stdout
        self.assertIn("1. no card manufactures the operator step", charter)

    def test_the_dre_3257_walk_reaches_activation_with_no_human_move(self):
        """The shape DRE-3257 actually had, walked end to end: approval →
        SEND_BACK → a re-plan that changed no card set → the review re-runs
        itself → THAT review dies → the retry at 120 → PASS → activation.

        The CEO does nothing after the first approval, and not one
        `state Green Light` is written anywhere in the walk."""
        out = self._hold("DRE-9002 migrates a table but no card manufactures "
                         "the operator step")
        self.assertEqual(out["round"], "1")

        cards = self._replan("DRE-9001,DRE-9002", "DRE-9001,DRE-9002")
        self.assertEqual(cards["changed"], "false")
        self._summary("Rewrote DRE-9002 to name the operator who runs it.")
        self._sent_back(cards, FINDING=out["reason"])
        self.assertEqual([p["client_payload"]["reason"] for p in self._dispatches()],
                         ["re-review"])

        # The re-review run sizes itself off the thread, and DIES at 100.
        self._shell("second critic — turn ceiling", STUB_KIDS="15")
        self.assertEqual(self._outputs()["max_turns"], "100")
        self._died_at("100", "111")
        self.assertEqual([p["client_payload"]["reason"] for p in self._dispatches()],
                         ["re-review", "review-retry"])

        # The retry reads the tombstone and runs with headroom.
        self._shell("second critic — turn ceiling", STUB_KIDS="15")
        self.assertEqual(self._outputs()["max_turns"], "150")

        # ...and passes. Round 2 of 2 — the death was never a round.
        self._critic_writes("post", pc.PASS)
        self._shell("second critic — decision")
        final = self._outputs()
        self.assertEqual((final["action"], final["round"]), ("proceed", "2"))
        self._shell("Activate the approved epic")

        log = self._log()
        self.assertIn("state In Progress", log)
        self.assertIn("promote --promote-only", log)
        self.assertNotIn("state Green Light", log,
                         "the CEO was asked to approve again inside the walk")
        self.assertNotIn("add-label", log)

    # --- DRE-3241: the review itself dies -----------------------------------

    def test_the_review_ceiling_is_sized_from_the_children(self):
        """The activate route counts the children fresh and hands the review
        a ceiling sized for them — fifteen cards get 100, a three-card plan
        gets the floor of 60. Both numbers moved up with DRE-2785's web-tool
        grant and the base again with DRE-3498; the shape did not."""
        self._shell("second critic — turn ceiling", STUB_KIDS="15")
        self.assertEqual(self._outputs()["max_turns"], "100")
        self._shell("second critic — turn ceiling", STUB_KIDS="3")
        self.assertEqual(self._outputs()["max_turns"], "60")

    def test_a_dead_review_leaves_a_tombstone_and_the_sweep_holds_on_it(self):
        """2026-09-05 on DRE-3164, walked. Round 1 sends the plan back; the
        CEO approves the revision; round 2 DIES at its ceiling. Before this
        card the thread's newest record was round 1's send-back and every
        sweep quoted it. Now the death is on the epic, the sweep reads it as
        "died — not a rejection", nothing promotes, and the round the CEO's
        re-approval buys is the one that counts."""
        self._critic_writes("post", pc.SEND_BACK, "no card manufactures the operator step")
        self._shell("second critic — decision")
        self.assertEqual(pc.send_backs(self._thread(), pc.STAGE_POST), 1)

        # The action's own record of the death, as claude-code-action writes
        # it — the SAME file DRE-2924's QA gate reads.
        exec_path = os.path.join(self.tmp, "claude-execution-output.json")
        with open(exec_path, "w") as f:
            json.dump([
                {"type": "system", "subtype": "init"},
                {"type": "result", "subtype": "error_max_turns", "is_error": True,
                 "num_turns": 41, "total_cost_usd": 1.73, "duration_ms": 368298,
                 "env": {"ANTHROPIC_API_KEY": "never-in-a-comment"}},
            ], f)
        self._shell("second critic — the review died", {
            "${{ steps.posta.outputs.execution_file }}": exec_path,
            "${{ steps.postturns.outputs.max_turns }}": "40",
            "${{ github.run_id }}": "34008698027",
            "${{ github.run_attempt }}": "2",
        }, expect_rc=1)

        # Two comments: the note the CEO reads, then the tombstone alone. The
        # retry line DRE-3289 adds lands after both of them, so the pair is
        # still the last two things written before anything acts on the death.
        note, record = self._thread()[-3], self._thread()[-2]
        self.assertIn("did not finish", note)
        self.assertIn("not a rejection", note)
        self.assertNotIn(pc.REAPPROVE_HOW, note,
                         "the review re-runs itself; the CEO is asked nothing")
        self.assertEqual(record, pc.death_marker(
            "post", "34008698027", 2, "posta", "error_max_turns", 41, 40))
        self.assertNotIn("never-in-a-comment", note + record)
        # Nothing moved and nothing promoted: the job is red, the decision and
        # the activation were never reached.
        log = self._log()
        self.assertNotIn("promote", log)
        self.assertNotIn("state ", log)

        # The sweep reads the same thread: died, not round 1's send-back.
        state, detail = pc.post_release(self._thread(), EPIC)
        self.assertEqual(state, pc.POST_DIED)
        self.assertIn("34008698027", detail)
        refusal = pc.promotion_refusal("DRE-9001", EPIC, "2026-09-10T12:04:00.000Z",
                                       self._records())
        self.assertEqual(pc.refusal_tag(refusal), pc.POST_DIED_TAG)
        self.assertNotIn("operator step", refusal)
        # ...and the dead round spent nothing of the bound.
        self.assertEqual(pc.send_backs(self._thread(), pc.STAGE_POST), 1)

        # The CEO moves the epic to Green Light and approves it; the review
        # runs again and passes. It is round 2 — the death was never a round.
        self._critic_writes("post", pc.PASS)
        self._shell("second critic — decision")
        out = self._outputs()
        self.assertEqual((out["action"], out["round"]), ("proceed", "2"))
        self.assertEqual(pc.post_release(self._thread(), EPIC)[0], pc.POST_RELEASED)
        self.assertIsNone(pc.promotion_refusal(
            "DRE-9001", EPIC, "2026-09-10T12:04:00.000Z", self._records()))

    def test_a_dead_review_with_no_execution_file_still_leaves_a_tombstone(self):
        """The action moved its output file once already (execution_result.py):
        an unreadable file is unknown turns, not a second failure."""
        self._shell("second critic — the review died", {
            "${{ steps.posta.outputs.execution_file }}": "",
            "${{ steps.postturns.outputs.max_turns }}": "90",
            "${{ github.run_id }}": "1",
            "${{ github.run_attempt }}": "1",
        }, expect_rc=1)
        record = self._thread()[-1]
        self.assertIn("turns=?", record)
        self.assertIn("ceiling=90", record)
        self.assertEqual(pc.post_release(self._thread(), EPIC)[0], pc.POST_DIED)

    # --- DRE-3289: the review re-runs itself once, then parks ---------------

    def _died_at(self, ceiling: str, run: str,
                 subtype: str = "error_max_turns", turns: int | None = None,
                 **env_extra):
        """The dead-review step, walked for a death at `ceiling` in run `run`.

        The execution file is the one claude-code-action writes and the one
        DRE-2924's QA gate reads. What decides retry-or-leave is the subtype
        in it OR the turns it spent against the ceiling (DRE-3501), so `turns`
        is a knob here: it defaults to one PAST the ceiling, which is what a
        run cut off there spends, and a death that is genuinely the medic's is
        one that stopped short of it.

        `expect_rc=1`: the step ends by failing the job itself, because the
        review step above it no longer does (DRE-3501)."""
        exec_path = os.path.join(self.tmp, f"execution-{run}.json")
        with open(exec_path, "w") as f:
            json.dump([
                {"type": "system", "subtype": "init"},
                {"type": "result", "subtype": subtype, "is_error": True,
                 "num_turns": int(ceiling) + 1 if turns is None else turns,
                 "total_cost_usd": 2.10, "duration_ms": 900000},
            ], f)
        return self._shell("second critic — the review died", {
            "${{ steps.posta.outputs.execution_file }}": exec_path,
            "${{ steps.postturns.outputs.max_turns }}": ceiling,
            "${{ github.run_id }}": run,
            "${{ github.run_attempt }}": "1",
        }, expect_rc=1, **env_extra)

    def _dispatches(self) -> list[dict]:
        """Every `repository_dispatch` payload GitHub actually accepted."""
        return [json.loads(line[len("dispatch "):])
                for line in self._log().splitlines()
                if line.startswith("dispatch ")]

    def test_a_dead_review_re_runs_itself_once_and_a_second_death_parks(self):
        """The whole of DRE-3289, walked. A fifteen-card plan's review dies at
        100; the run re-dispatches ITSELF at the higher ceiling with no lane
        move, the next run reads 150 off the thread, and when that one dies too
        the epic parks with needs-human and a note naming both runs."""
        self._shell("second critic — turn ceiling", STUB_KIDS="15")
        self.assertEqual(self._outputs()["max_turns"], "100")

        self._died_at("100", "111")

        # ONE dispatch, on the ACTIVATE route, saying why it was asked for.
        sent = self._dispatches()
        self.assertEqual(len(sent), 1, self._log())
        payload = sent[0]["client_payload"]
        self.assertEqual(payload["trigger_state"], "in progress")
        self.assertEqual(payload["reason"], "review-retry")
        self.assertEqual(payload["identifier"], EPIC)

        # ...and the epic's lane is not written. The epic is already In
        # Progress and stays there; nothing is asked of the CEO.
        log = self._log()
        self.assertNotIn("state ", log)
        self.assertNotIn("add-label", log)
        self.assertNotIn("promote", log)
        self.assertIn("higher", self._thread()[-1])

        # ...and that receipt was written AFTER the dispatch landed, not ahead
        # of it. "The review is being run again" is a claim about something
        # that has already happened (DRE-2034).
        order = [line.split(" ")[0] for line in log.splitlines()
                 if line.startswith("dispatch ") or line.startswith("comment 🔁")]
        self.assertEqual(order, ["dispatch", "comment"], log)

        # The retry run sizes itself from the thread the dead one left.
        self._shell("second critic — turn ceiling", STUB_KIDS="15")
        self.assertEqual(self._outputs()["max_turns"], "150",
                         "the retry ran into the same wall it just died at")

        # And it dies too. Two deaths is the bound: park for an operator.
        self._died_at("150", "222")
        self.assertEqual(len(self._dispatches()), 1,
                         "a second death must not buy a third attempt")
        log = self._log()
        self.assertIn("add-label needs-human", log)
        self.assertIn("state Green Light", log)
        park = self._thread()[-1]
        self.assertIn("111", park)
        self.assertIn("222", park)
        self.assertIn("150", park, "the ceiling the second one still could not finish under")

    def test_a_non_turn_death_dispatches_nothing_and_writes_no_lane(self):
        """The medic owns every other death and retries it once already
        (`medic_retry.RULE_TURN_EXHAUSTION` is the only one it refuses). Two
        automatic retries of one run is the DRE-2937 failure, at ~$16 a go."""
        self._died_at("90", "333", subtype="error_during_execution", turns=20)
        self.assertEqual(self._dispatches(), [])
        log = self._log()
        self.assertNotIn("state ", log)
        self.assertNotIn("add-label", log)
        # Still exactly the two comments a death always wrote.
        self.assertEqual(log.count("comment "), 2, log)
        self.assertEqual(pc.post_release(self._thread(), EPIC)[0], pc.POST_DIED)

    # --- DRE-3501: the result file decides, not the step outcome -----------

    def test_a_review_cut_off_after_writing_its_verdict_is_an_ordinary_round(self):
        """agent-bureau run 34144302622, walked. The review FINISHED at turn 51
        of a 48-turn ceiling — `"subtype": "success"` — and the action marked
        its step failed anyway. The rail read the step: tombstone, no decision,
        and a `PLAN-CRITIC: PASS` nobody ever opened. Now the verdict step
        reads the FILE, the tombstone's own gate needs NO_RESULT, and the round
        is ordinary."""
        self._critic_writes("post", pc.PASS)
        self._shell("second critic — verdict or death?")
        self.assertEqual(self._outputs()["verdict"], pc.PASS,
                         "the tombstone's gate is `== NO_RESULT`")
        self._shell("second critic — decision")
        out = self._outputs()
        self.assertEqual((out["action"], out["result"]), ("proceed", pc.PASS))
        self.assertEqual(pc.post_release(self._thread(), EPIC)[0], pc.POST_RELEASED)
        for body in self._thread():
            self.assertNotIn("🪦", body, "no tombstone is composed for a round")

    def test_a_review_that_wrote_nothing_still_reaches_the_tombstone(self):
        """The other side of the same gate, and the whole of what keeps the
        DRE-3241 trap closed: no result file is NO_RESULT, NO_RESULT is the
        tombstone's gate, and the over-ceiling finish is read as the turn cap
        — so the review is asked for again instead of being left to a medic
        that refuses turn caps."""
        self._shell("second critic — verdict or death?")
        self.assertEqual(self._outputs()["verdict"], pc.NO_RESULT)
        self._died_at("48", "34144302622", subtype="success", turns=51)
        note = self._thread()[-3]
        self.assertIn("ran out of turns — 51 of its 48-turn ceiling", note)
        self.assertNotIn("(success)", note)
        self.assertEqual(len(self._dispatches()), 1, self._log())

    def test_a_failed_dispatch_is_said_and_never_claimed_as_started(self):
        """DRE-2034's rule at this seam: a 403'd dispatch must not leave the
        epic reading as though a run were on its way — not as the last comment,
        and not ANYWHERE above it either. A retraction underneath a false claim
        does not unwrite the claim: the thread is the record a person reads
        back, and it would say "it is being run again" forever."""
        self._died_at("90", "444", STUB_GH_RC="1")
        self.assertEqual(self._dispatches(), [])
        self.assertIn("could NOT", self._thread()[-1])
        self.assertNotIn("state ", self._log())
        for body in self._thread():
            self.assertNotIn("🔁", body, self._thread())
            self.assertNotIn("being run again", body, self._thread())

    def test_two_failed_rounds_after_approval_park_with_needs_human(self):
        """DRE-3088: the bound after approval PARKS. The old rail activated the
        epic here — "two failed rounds and the work proceeds regardless" — and
        built a plan the critic had held twice."""
        self._critic_writes("post", pc.SEND_BACK, "no card manufactures the operator step")
        self._shell("second critic — decision")
        # ...and held twice ON THE SAME GAP (DRE-4115): round 2 says round 1's
        # finding is still open.
        self._critic_writes("post", pc.SEND_BACK,
                            "still no card manufactures the operator step",
                            extra="still-open: 1\n")
        self._shell("second critic — decision")
        out = self._outputs()
        self.assertEqual(out["action"], "hold")
        self.assertEqual(out["bound"], "true")
        self.assertIn("still no card", self._note())
        self.assertIn("two failed rounds", self._note().lower())
        self._shell("second critic sent the plan back", BOUND="true",
                    FINDING=out["reason"], NOTE=out["note"], REPLAN_OUTCOME="success")
        log = self._log()
        self.assertIn("state Green Light", log)
        self.assertIn("add-label needs-human", log)
        self.assertNotIn("promote", log)
        self.assertNotIn("state In Progress", log)
        receipt = self._thread()[-1]
        self.assertIn("Parked for you", receipt)
        self.assertIn("no card manufactures the operator step", receipt)
        self.assertNotIn("Todo", receipt)
        # ...and the sweep's gate reads the same thread the same way.
        state, _ = pc.post_release(self._thread(), EPIC)
        self.assertEqual(state, pc.POST_HELD)

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
        self.assertIn("round 1 of 2", self._note())

        # ...and it is still bounded: the second send-back of the NEW attempt
        # reaches the CEO with the reason attached.
        self._critic_writes("pre", pc.SEND_BACK, "DRE-9005 still carries none")
        self._shell("first critic — round 2 decision")
        self.assertEqual(self._outputs()["action"], "proceed")
        self.assertIn("two failed rounds", self._note().lower())

    # --- 7b: only the approval move activates (DRE-3100) --------------------

    def test_a_planning_entry_with_children_is_a_re_plan_not_an_activation(self):
        """DRE-3060, 2026-09-04: the CEO moved an epic with nine children
        through Planning to say "plan this again"; the route read "not Triage,
        and children exist" as approval, the post-approval critic held the plan
        twice more, and the rail activated it anyway. A Planning entry is a
        planner's verb whatever the child count."""
        self._shell(
            "Route — plan or activate",
            subs={"${{ github.event.client_payload.trigger_state }}": "planning"},
            STUB_KIDS="9",
        )
        out = self._outputs()
        self.assertEqual(out["mode"], "plan")
        self.assertEqual(out["kids"], "9")
        log = self._log()
        self.assertIn("state Planning", log, "a re-plan opens a fresh planning attempt")
        self.assertNotIn("state In Progress", log)
        self.assertNotIn("promote", log)

    def test_a_todo_entry_with_children_does_not_activate_either(self):
        """An epic in Todo dispatches nothing at the relay (DRE-2725); if one
        ever reached this step it must still not be read as approved."""
        self._shell(
            "Route — plan or activate",
            subs={"${{ github.event.client_payload.trigger_state }}": "todo"},
            STUB_KIDS="3",
        )
        self.assertEqual(self._outputs()["mode"], "plan")

    def test_only_the_in_progress_entry_activates(self):
        self._shell(
            "Route — plan or activate",
            subs={"${{ github.event.client_payload.trigger_state }}": "in progress"},
            STUB_KIDS="9",
        )
        self.assertEqual(self._outputs()["mode"], "activate")
        self.assertNotIn("state Planning", self._log())

    def test_an_approved_epic_that_was_never_planned_is_planned_first(self):
        """The forgiving fallback, unchanged: In Progress with no children."""
        self._shell(
            "Route — plan or activate",
            subs={"${{ github.event.client_payload.trigger_state }}": "in progress"},
            STUB_KIDS="0",
        )
        self.assertEqual(self._outputs()["mode"], "plan")

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
        self._critic_writes("post", pc.SEND_BACK, "no card manufactures the operator step")
        self._shell("second critic — decision")
        # Round 2 finds round 1's gap still open (DRE-4115: that, not the
        # count alone, is what reaches the bound).
        self._critic_writes("post", pc.SEND_BACK,
                            "still no card manufactures the operator step",
                            extra="still-open: 1\n")
        self._shell("second critic — decision")
        # The bound is reached: after approval that is a park, not a release
        # (DRE-3088), and `bound=true` is the signal the park step reads.
        self.assertEqual(self._outputs()["bound"], "true")

        self._stray_comment(pc.cycle_marker(EPIC))
        self._critic_writes("post", pc.SEND_BACK, "and still none")
        self._shell("second critic — decision")
        self.assertEqual(self._outputs()["bound"], "true",
                         "a stray boundary reopened a loop the bound had closed")
        self.assertNotEqual(self._outputs()["round"], "1",
                            "a stray boundary handed the plan a fresh budget")

    # --- 8b: a record is a comment that says nothing else -------------------

    def _planner_write_up(self, record: str) -> str:
        """What the planner posts to this same epic, in the same thread, with
        the same key — plain English for the CEO, written by an LLM that has
        just read the epic's untrusted description and been told to explain
        this gate. Quoting the worked example is the obvious way to do that."""
        return (
            "Plan for DRE-2721 — two plan critics.\n\n"
            "We will review every plan twice: once before you read it, once "
            "after you approve it, and both reviews give up after two rounds "
            "so a plan can never circle forever. Each round is recorded on "
            "this card as a line like\n\n"
            f"{record}\n\n"
            "To start the build: move this epic to Todo again."
        )

    def test_a_planner_write_up_quoting_a_marker_forges_no_round(self):
        """Authorship alone was not narrow enough: the planner posts to this
        same thread through the same call and the same key. One quoted line in
        its write-up made the second critic's real, current rejection — a
        migration card with nobody to run the migration — read as "the budget
        is already spent", and the children promoted to build anyway."""
        self._pipeline_comment(self._planner_write_up(
            pc.marker(pc.STAGE_POST, 1, pc.SEND_BACK, "an earlier round")))

        self._critic_writes(
            "post", pc.SEND_BACK,
            "DRE-9003 migrates a table but no card manufactures the operator step",
        )
        self._shell("second critic — decision")
        out = self._outputs()
        self.assertEqual(out["action"], "hold",
                         "a quoted marker overrode the critic's real rejection")
        self.assertEqual(out["round"], "1", "a quoted marker was counted as a round run")

        self._shell("second critic sent the plan back")
        log = self._log()
        self.assertNotIn("promote", log, "children promoted on a quoted round count")
        self.assertIn("operator step", log)

    def test_a_planner_write_up_quoting_the_boundary_refunds_nothing(self):
        self._critic_writes("post", pc.SEND_BACK, "no card manufactures the operator step")
        self._shell("second critic — decision")
        self._critic_writes("post", pc.SEND_BACK,
                            "still no card manufactures the operator step",
                            extra="still-open: 1\n")
        self._shell("second critic — decision")
        self.assertEqual(self._outputs()["bound"], "true")

        self._pipeline_comment(self._planner_write_up(pc.cycle_marker(EPIC)))
        self._critic_writes("post", pc.SEND_BACK, "and still none")
        self._shell("second critic — decision")
        self.assertEqual(self._outputs()["bound"], "true",
                         "a quoted boundary reopened a loop the bound had closed")

    def test_the_run_posts_its_records_as_comments_of_their_own(self):
        """The narrowing only holds because the run stopped writing records
        into prose. Both halves land on the epic, and only the bare one is
        read back as a round."""
        self._shell(
            "Route — plan or activate",
            subs={"${{ github.event.client_payload.trigger_state }}": "triage"},
        )
        self.assertEqual(self._thread()[-1], pc.cycle_marker(EPIC))
        self.assertNotIn(pc.CYCLE_PREFIX, self._thread()[-2])

        self._critic_writes("pre", pc.SEND_BACK, "DRE-9001 carries no acceptance criteria")
        self._shell("first critic — round 1 decision")
        self.assertEqual(
            self._record(),
            pc.marker(pc.STAGE_PRE, 1, pc.SEND_BACK,
                      "DRE-9001 carries no acceptance criteria"))
        self.assertNotIn(pc.MARKER_PREFIX, self._note())
        self.assertEqual(pc.send_backs(self._thread(), pc.STAGE_PRE), 1)

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

        self.assertIn(OTHER_EPIC, self._note())
        self.assertIn("reconcile.py", self._note())

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

    # --- 12: one round, every finding (DRE-3251) --------------------------

    def test_a_round_with_three_findings_reports_all_three_in_one_pass(self):
        """The whole of DRE-3251, walked through plan.yml's own shell.

        DRE-3164 spent four rounds, three parks and four CEO approvals on four
        findings the critic had all seen in round 1. Here the critic writes
        three in one result file, and the run must produce ONE marker carrying
        the first line, a note carrying all three, and a step output the
        re-plan's prompt can interpolate as a list.
        """
        self._critic_writes(
            "post", pc.SEND_BACK,
            "the deploy-lag cards collide with One River's DRE-3116/3117",
            extra=("1. DRE-3210 is already shipped — a Done child read as work\n"
                   "2. three cards cite a 07:00 PT precedent that does not exist "
                   "(DRE-3212, DRE-3214)\n"),
        )
        self._shell("second critic — decision")

        # The record is one line and carries the FIRST finding only — the
        # marker, the bound and the sweep all read that line and nothing else.
        self.assertEqual(len(self._record().strip().splitlines()), 1)
        self.assertEqual(pc.parse_markers([self._record()])[0]["reason"],
                         "the deploy-lag cards collide with One River's DRE-3116/3117")
        self.assertNotIn("DRE-3210", self._record())

        # The note beside it carries every one of them, with their cards.
        note = self._note()
        for card in ("DRE-3116/3117", "DRE-3210", "DRE-3212", "DRE-3214"):
            self.assertIn(card, note)

        # ...and so does the step output the re-plan's prompt reads. It is the
        # ONE multi-line output here, so it travels as a heredoc block — read
        # the raw file, because `_outputs` only models `key=value` lines.
        raw = open(self.gho).read()
        self.assertIn("findings_count=3", raw)
        self.assertRegex(raw, r"(?m)^findings<<EOF-[0-9a-f]{32}$")
        for finding in ("1. the deploy-lag cards collide",
                        "2. DRE-3210 is already shipped",
                        "3. three cards cite a 07:00 PT precedent"):
            self.assertIn(finding, raw)
        # The delimiter closes the block and nothing else is defined after it.
        delim = re.search(r"(?m)^findings<<(EOF-[0-9a-f]{32})$", raw).group(1)
        self.assertEqual(raw.count(delim), 2)
        self.assertTrue(raw.rstrip().endswith(delim))

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
