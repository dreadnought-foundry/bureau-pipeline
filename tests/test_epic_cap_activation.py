"""RED-first: an approval at the cap waits in line (DRE-5136).

The CEO approves an epic by moving it to In Progress, and `plan.yml`'s
`Route — plan or activate` step answers `mode=activate`. DRE-5134 gave the
fleet a cap on epics in motion (`scripts/epic_cap.py`) and a command that
decides one approval: `epic_cap.py decide` prints `start` or `queue`. This
card puts that command in the route step, at the one point the step would
answer `activate`, so an approval made at the cap is recorded and waits in
line instead of starting:

  * `start` — the `epic-queued` label comes off, then `mode=activate` and the
    attempt opens exactly as before;
  * `queue` — the epic is labeled `epic-queued`, the receipt `decide` wrote is
    posted once per planning attempt with the `epic-approval-queued` act, the
    epic returns to Green Light, and the step answers `mode=queued`, which no
    later step is gated on;
  * exit 2 (the cap file cannot be read) activates — an unreadable cap never
    freezes every approval in the fleet — and exit 3 (Linear cannot be read)
    queues with the body `decide` wrote for that case.

Every branch that writes Planning also removes the label: an epic that leaves
for Planning has left the line.

The step's shell is RUN here, with `linear_ops.py`, `plan_critic.py` and
`epic_cap.py` stubbed to record their calls, and its `if:` conditions — and
every other step's — are compared against `plan.yml` before this card.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_cap_activation.py -v
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
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import epic_cap  # noqa: E402
import lane_contract  # noqa: E402
import pipeline_act  # noqa: E402
import plan_critic  # noqa: E402

WF_REL = ".github/workflows/plan.yml"
WF = ROOT / WF_REL
CONTRACT_REL = "config/lane-contract.json"
DOC_REL = "docs/lane-contract.md"
ACTS = ROOT / "config" / "pipeline-acts.json"

ROUTE = "Route — plan or activate"
EPIC = "DRE-9136"
DECIDE = ('python3 .bureau-pipeline/scripts/epic_cap.py decide --epic "$EPIC" '
          '--receipt-file "$RUNNER_TEMP/epic-cap-receipt.md"')
#: What first appears in plan.yml with this card — the history tests find the
#: commit that brought it, and compare against its parent.
INTRODUCED_BY = "epic_cap.py decide"

RECORD = {
    "kind": "queued-epic",
    "writer": "plan.yml",
    "where": "plan.yml#Route — plan or activate",
    "evidence": "the ⏸️ epic-queued: receipt carrying the epic-approval-queued "
                "act trailer, and the epic-queued label",
    "card": "DRE-5136",
}

QUEUED_BODY = ("⏸️ epic-queued: approved and waiting in line — place 2 of 3. "
               "15 of 15 epics are in motion, so the sweep starts this one when "
               "one closes.")
UNREAD_BODY = ("⏸️ epic-queued: approved — but the fleet's count of epics in "
               "motion could not be read (HTTP 503). The epic waits in line, and "
               "the sweep starts it on its next pass if there is room.")


# --------------------------------------------------------------------------- #
# the workflow, read                                                           #
# --------------------------------------------------------------------------- #

def steps(text: str | None = None) -> list[dict]:
    doc = yaml.safe_load(text if text is not None else WF.read_text(encoding="utf-8"))
    return doc["jobs"]["plan"]["steps"]


def step_named(name: str, text: str | None = None) -> dict:
    found = [s for s in steps(text) if s.get("name") == name]
    if len(found) != 1:
        raise AssertionError(f"{len(found)} steps named exactly {name!r} in plan.yml")
    return found[0]


def route_run() -> str:
    return str(step_named(ROUTE).get("run") or "")


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                             text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def before_and_after(rel: str) -> tuple[str, str] | None:
    """`rel` before and after this card: the commit that first brought
    `epic_cap.py decide` into plan.yml against its parent, or — before that
    commit exists — the working tree against its merge base with `main`."""
    log = _git("log", "--format=%H", "--reverse", "-S", INTRODUCED_BY, "--", WF_REL)
    first = (log or "").split()
    if first:
        before = _git("show", f"{first[0]}^:{rel}")
        after = _git("show", f"{first[0]}:{rel}")
        if before is not None and after is not None:
            return before, after
    base = (_git("merge-base", "HEAD", "origin/main") or "").strip()
    if base:
        before = _git("show", f"{base}:{rel}")
        if before is not None:
            return before, (ROOT / rel).read_text(encoding="utf-8")
    return None


def need_history(case: unittest.TestCase, rel: str) -> tuple[str, str]:
    pair = before_and_after(rel)
    if pair is None:
        # The unit job checks out full history and sets this, so there the
        # missing history is a failure, never a green skip.
        if os.environ.get("BUREAU_REQUIRE_GIT_HISTORY"):
            case.fail(f"no git history for {rel}, and this job requires it")
        case.skipTest(f"no git history for {rel} (a shallow checkout)")
    return pair


def _keyed(step_list: list[dict]) -> dict[tuple, dict]:
    """Steps keyed by name (or `uses:`) and occurrence, so two steps sharing a
    key are paired in order rather than collapsed into one."""
    seen: dict[str, int] = {}
    out = {}
    for s in step_list:
        base = s.get("name") or s.get("uses") or s.get("id") or ""
        n = seen.get(base, 0)
        seen[base] = n + 1
        out[(base, n)] = s
    return out


# --------------------------------------------------------------------------- #
# the stubs                                                                    #
# --------------------------------------------------------------------------- #

# Every call is one JSON line in STUB_LOG: the script, then its argv.
LINEAR_STUB = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as f:
    f.write(json.dumps(["linear_ops"] + args) + "\n")
cmd = args[0]
if cmd == "children":
    print(os.environ.get("STUB_KIDS", "4"))
elif cmd == "count-comments":
    print(os.environ.get("STUB_COUNT", "0"))
elif cmd == "dump-comments":
    print("[]")
'''

CRITIC_STUB = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as f:
    f.write(json.dumps(["plan_critic"] + args) + "\n")
if args[0] == "activate-cycle":
    sys.stdin.read()
    print(os.environ.get("STUB_CYCLE", "keep"))
elif args[0] == "cycle-start":
    if "--record" in args:
        print("plan-cycle: start epic=" + args[args.index("--epic") + 1])
    else:
        print("A new planning attempt starts.")
'''

CAP_STUB = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as f:
    f.write(json.dumps(["epic_cap"] + args) + "\n")
body = os.environ.get("STUB_BODY")
if body and "--receipt-file" in args:
    with open(args[args.index("--receipt-file") + 1], "w") as f:
        f.write(body)
if os.environ.get("STUB_ANSWER"):
    print(os.environ["STUB_ANSWER"])
if os.environ.get("STUB_WHY"):
    sys.stderr.write(os.environ["STUB_WHY"] + "\n")
sys.exit(int(os.environ.get("STUB_RC", "0")))
'''


class RouteHarness(unittest.TestCase):
    """Runs the route step's own shell with its expressions resolved."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        scripts = os.path.join(self.tmp, ".bureau-pipeline", "scripts")
        os.makedirs(scripts)
        for name, body in (("linear_ops.py", LINEAR_STUB),
                           ("plan_critic.py", CRITIC_STUB),
                           ("epic_cap.py", CAP_STUB)):
            path = os.path.join(scripts, name)
            with open(path, "w") as f:
                f.write(body)
            os.chmod(path, 0o755)
        self.runner_temp = os.path.join(self.tmp, "runner-temp")
        os.makedirs(self.runner_temp)
        self.log = os.path.join(self.tmp, "calls.jsonl")
        self.out = os.path.join(self.tmp, "github-output")
        self.summary = os.path.join(self.tmp, "step-summary")
        for path in (self.log, self.out, self.summary):
            open(path, "w").close()

    def route(self, trigger: str, reason: str = "", **stub) -> subprocess.CompletedProcess:
        script = route_run()
        for expression, value in (
                ("${{ runner.temp }}", self.runner_temp),
                ("${{ github.event.client_payload.identifier }}", EPIC),
                ("${{ github.event.client_payload.trigger_state }}", trigger)):
            script = script.replace(expression, value)
        leftover = re.findall(r"\$\{\{[^}]*\}\}", script)
        self.assertEqual(leftover, [], "unmodelled expressions in the route step")
        env = dict(os.environ, STUB_LOG=self.log, GITHUB_OUTPUT=self.out,
                   GITHUB_STEP_SUMMARY=self.summary, RUNNER_TEMP=self.runner_temp,
                   REASON=reason, MAX_WIP="8")
        env.update({f"STUB_{k.upper()}": str(v) for k, v in stub.items()})
        proc = subprocess.run(["bash", "-e", "-c", script], cwd=self.tmp,
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc

    def calls(self) -> list[list[str]]:
        with open(self.log) as f:
            return [json.loads(line) for line in f if line.strip()]

    def linear(self) -> list[list[str]]:
        """The Linear calls after the children read every branch makes."""
        return [c[1:] for c in self.calls()
                if c[0] == "linear_ops" and c[1] != "children"]

    def decided(self) -> list[list[str]]:
        return [c for c in self.calls() if c[0] == "epic_cap"]

    def mode(self) -> str:
        modes = [line.split("=", 1)[1] for line in open(self.out).read().splitlines()
                 if line.startswith("mode=")]
        self.assertEqual(len(modes), 1, modes)
        return modes[0]

    def summary_text(self) -> str:
        return open(self.summary).read()


# --------------------------------------------------------------------------- #
# AC1 — `queue`: label, receipt once, Green Light, mode=queued, no attempt     #
# --------------------------------------------------------------------------- #

class TheQueueAnswer(RouteHarness):

    def test_the_decision_runs_in_the_activate_branch_before_the_attempt_opens(self):
        run = route_run()
        self.assertIn(DECIDE, run)
        self.assertEqual(run.count(".bureau-pipeline/scripts/epic_cap.py"), 1)
        at = run.index(DECIDE)
        branch = run.rindex('elif [ "$FROM" = "in progress" ] && [ "$KIDS" -gt 0 ]; then', 0, at)
        self.assertLess(branch, at)
        self.assertLess(at, run.index("open_attempt_if_a_person_asked", at))
        self.assertIn('echo "mode=queued" >> "$GITHUB_OUTPUT"', run)

    def test_queue_labels_posts_moves_and_answers_queued(self):
        self.route("in progress", ANSWER="queue", BODY=QUEUED_BODY,
                   WHY="epic-cap: queue — rule 2: 15 of 15 in motion")
        self.assertEqual(self.decided(), [[
            "epic_cap", "decide", "--epic", EPIC, "--receipt-file",
            os.path.join(self.runner_temp, "epic-cap-receipt.md")]])
        self.assertEqual(self.linear(), [
            ["add-label", EPIC, epic_cap.QUEUED_LABEL],
            ["count-comments", EPIC, f"⏸️ {epic_cap.QUEUED_TAG}:",
             "--since", plan_critic.cycle_marker(EPIC)],
            ["comment", EPIC, QUEUED_BODY, f"--act={epic_cap.QUEUED_ACT}"],
            ["state", EPIC, "Green Light"],
        ])
        self.assertEqual(self.mode(), "queued")

    def test_queue_opens_no_attempt(self):
        self.route("in progress", ANSWER="queue", BODY=QUEUED_BODY, CYCLE="open")
        self.assertEqual([c for c in self.calls() if c[0] == "plan_critic"], [])
        self.assertNotIn(["remove-label", EPIC, epic_cap.QUEUED_LABEL], self.linear())

    def test_queue_says_so_in_the_log_and_the_summary(self):
        proc = self.route("in progress", ANSWER="queue", BODY=QUEUED_BODY)
        said = [line for line in proc.stdout.splitlines() if "queued" in line and EPIC in line]
        self.assertTrue(said, proc.stdout)
        self.assertIn(said[-1], self.summary_text())

    def test_every_approval_row_reaches_the_decision(self):
        """The gate sits where the step answers `activate`: the approval move,
        a person's re-run, and the pipeline's own re-asks on an approved epic
        — never only some of them, or the cap is a door left open."""
        for reason in ("", "re-run", "re-review", "review-retry"):
            with self.subTest(reason=reason):
                self.setUp()
                self.route("in progress", reason, ANSWER="queue", BODY=QUEUED_BODY)
                self.assertEqual(len(self.decided()), 1)
                self.assertEqual(self.mode(), "queued")


# --------------------------------------------------------------------------- #
# AC2 — `start`: label off, mode=activate, the attempt opens as today          #
# --------------------------------------------------------------------------- #

class TheStartAnswer(RouteHarness):

    def test_start_removes_the_label_and_activates(self):
        self.route("in progress", ANSWER="start")
        self.assertEqual(self.linear()[0], ["remove-label", EPIC, epic_cap.QUEUED_LABEL])
        self.assertEqual(self.mode(), "activate")
        for c in self.linear():
            self.assertNotEqual(c[0], "add-label")
            self.assertNotEqual(c[0], "state")

    def test_start_opens_the_attempt_exactly_as_today(self):
        """A person's ask at a park opens the fresh attempt, after the label."""
        self.route("in progress", "re-run", ANSWER="start", CYCLE="open")
        names = [c[0] if c[0] != "linear_ops" else c[1] for c in self.calls()]
        self.assertEqual(names, ["children", "epic_cap", "remove-label",
                                 "dump-comments", "plan_critic",
                                 "plan_critic", "comment",
                                 "plan_critic", "comment"])
        self.assertEqual(self.linear()[-1][2], plan_critic.cycle_marker(EPIC))
        self.assertEqual(self.mode(), "activate")

    def test_start_passes_an_amendments_re_approval_through_unchanged(self):
        """DRE-5134's rule 1: a running epic re-approved after an amendment
        always gets `start`, and this step makes no decision of its own."""
        self.route("in progress", ANSWER="start",
                   WHY="epic-cap: start — rule 1: DRE-9136 has a child in In Progress")
        self.assertEqual(self.mode(), "activate")
        self.assertNotIn("Green Light", json.dumps(self.linear()))

    def test_no_attempt_without_a_person_asking(self):
        self.route("in progress", ANSWER="start", CYCLE="keep")
        self.assertNotIn("comment", [c[0] for c in self.linear()])


# --------------------------------------------------------------------------- #
# AC3 — the rule that answered goes to the log and the summary                 #
# --------------------------------------------------------------------------- #

class TheRuleIsShown(RouteHarness):

    def test_both_answers_copy_the_stderr_line(self):
        for answer, why in (("start", "start: re-approval of an epic already in motion"),
                            ("queue", "queue: 15 of 15 in motion")):
            with self.subTest(answer=answer):
                self.setUp()
                proc = self.route("in progress", ANSWER=answer, WHY=why, BODY=QUEUED_BODY)
                self.assertIn(why, proc.stdout)
                self.assertIn(why, self.summary_text())


# --------------------------------------------------------------------------- #
# AC4 — plan and review write Planning, lose the label, and never decide       #
# --------------------------------------------------------------------------- #

class LeavingForPlanningLeavesTheLine(RouteHarness):

    def _left_for_planning(self, mode: str):
        self.assertEqual(self.decided(), [], "the cap was asked off the activate row")
        lane = self.linear()
        planning = lane.index(["state", EPIC, "Planning"])
        self.assertEqual(lane[planning + 1], ["remove-label", EPIC, epic_cap.QUEUED_LABEL])
        self.assertNotIn(["add-label", EPIC, epic_cap.QUEUED_LABEL], lane)
        self.assertEqual(self.mode(), mode)

    def test_the_plan_branch(self):
        for trigger, kids in (("planning", "4"), ("triage", "0"), ("in progress", "0")):
            with self.subTest(trigger=trigger, kids=kids):
                self.setUp()
                self.route(trigger, "", KIDS=kids, ANSWER="queue", BODY=QUEUED_BODY)
                self._left_for_planning("plan")

    def test_every_review_row(self):
        rows = [(lane, reason) for lane in ("planning", "triage", "green light")
                for reason in ("review", "re-review", "review-retry", "re-run")]
        rows.append(("in progress", "review"))
        for trigger, reason in rows:
            with self.subTest(trigger=trigger, reason=reason):
                self.setUp()
                self.route(trigger, reason, ANSWER="queue", BODY=QUEUED_BODY)
                self._left_for_planning("review")


# --------------------------------------------------------------------------- #
# AC5 — a re-approval of a queued epic posts no second receipt                 #
# --------------------------------------------------------------------------- #

class OneReceiptPerAttempt(RouteHarness):

    def test_a_receipt_already_on_the_attempt_is_not_posted_again(self):
        self.route("in progress", ANSWER="queue", BODY=QUEUED_BODY, COUNT="1")
        lane = self.linear()
        self.assertNotIn("comment", [c[0] for c in lane])
        self.assertIn(["add-label", EPIC, epic_cap.QUEUED_LABEL], lane)
        self.assertEqual(lane[-1], ["state", EPIC, "Green Light"])
        self.assertEqual(self.mode(), "queued")

    def test_the_count_is_scoped_to_the_planning_attempt(self):
        self.route("in progress", ANSWER="queue", BODY=QUEUED_BODY)
        counts = [c for c in self.linear() if c[0] == "count-comments"]
        self.assertEqual(len(counts), 1)
        self.assertEqual(counts[0][-2:], ["--since", f"plan-cycle: start epic={EPIC}"])
        self.assertEqual(plan_critic.cycle_marker(EPIC), f"plan-cycle: start epic={EPIC}")


# --------------------------------------------------------------------------- #
# AC6 — the two failures are not the same                                      #
# --------------------------------------------------------------------------- #

class TheTwoFailures(RouteHarness):

    def test_an_unreadable_cap_activates(self):
        why = "epic-cap: config/epic-cap.json could not be read"
        proc = self.route("in progress", RC="2", WHY=why)
        self.assertIn(why, proc.stdout)
        self.assertEqual(self.mode(), "activate")
        lane = self.linear()
        self.assertNotIn(["add-label", EPIC, epic_cap.QUEUED_LABEL], lane)
        self.assertNotIn("Green Light", json.dumps(lane))

    def test_an_unread_board_queues_with_the_body_decide_wrote(self):
        self.route("in progress", RC="3", BODY=UNREAD_BODY,
                   WHY="epic-cap: Linear could not be read: HTTP 503")
        self.assertEqual(self.linear(), [
            ["add-label", EPIC, epic_cap.QUEUED_LABEL],
            ["count-comments", EPIC, f"⏸️ {epic_cap.QUEUED_TAG}:",
             "--since", plan_critic.cycle_marker(EPIC)],
            ["comment", EPIC, UNREAD_BODY, f"--act={epic_cap.QUEUED_ACT}"],
            ["state", EPIC, "Green Light"],
        ])
        self.assertEqual(self.mode(), "queued")

    def test_a_crashed_decision_does_not_freeze_the_approval(self):
        """Neither 2 nor 3: a defect in the decision, which must not hold
        every approval in the fleet any more than an unreadable cap does."""
        self.route("in progress", RC="1", WHY="Traceback (most recent call last):")
        self.assertEqual(self.mode(), "activate")
        self.assertNotIn("Green Light", json.dumps(self.linear()))


# --------------------------------------------------------------------------- #
# AC2 — no other step's `if:`, and no shell but the route step's, moved        #
# --------------------------------------------------------------------------- #

class NothingElseMoved(unittest.TestCase):

    def test_every_if_condition_is_byte_for_byte_unchanged(self):
        before, after = need_history(self, WF_REL)
        old, new = _keyed(steps(before)), _keyed(steps(after))
        self.assertEqual(sorted(old), sorted(new), "a step was added or removed")
        for key, s in new.items():
            with self.subTest(step=key[0]):
                self.assertEqual(s.get("if"), old[key].get("if"))

    def test_no_other_step_changed(self):
        before, after = need_history(self, WF_REL)
        old, new = _keyed(steps(before)), _keyed(steps(after))
        changed = [key[0] for key, s in new.items() if s != old.get(key)]
        self.assertEqual(changed, [ROUTE])
        route_old, route_new = old[(ROUTE, 0)], new[(ROUTE, 0)]
        self.assertEqual({k: v for k, v in route_new.items() if k != "run"},
                         {k: v for k, v in route_old.items() if k != "run"})

    def test_the_gates_this_card_must_not_touch_are_in_place(self):
        gates = {s.get("name"): str(s.get("if") or "") for s in steps()}
        self.assertIn("steps.slot.outputs.admitted == 'true'", gates[ROUTE])
        self.assertTrue(any("mode == 'activate'" in g for g in gates.values()))
        self.assertFalse(any("mode == 'queued'" in g for g in gates.values()),
                         "a step gated on the queued answer — nothing runs while it waits")

    def test_the_route_shell_names_no_planner_queue(self):
        for word in ("planner_queue", "slot", "admitted"):
            self.assertNotIn(word, route_run())

    def test_the_green_light_write_follows_the_label_in_the_same_shell(self):
        run = route_run()
        write = run.index('linear_ops.py state "$EPIC" "Green Light"')
        label = run.index('linear_ops.py add-label "$EPIC" epic-queued')
        self.assertLess(label, write)
        self.assertEqual(run.count('"Green Light"'), 1)


# --------------------------------------------------------------------------- #
# AC7 — the act                                                                #
# --------------------------------------------------------------------------- #

class TheAct(unittest.TestCase):

    def test_the_act_is_registered(self):
        doc = json.loads(ACTS.read_text(encoding="utf-8"))
        rows = [a for a in doc["acts"] if a["name"] == epic_cap.QUEUED_ACT]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["tag"], epic_cap.QUEUED_TAG)
        self.assertEqual(row["kind"], "hold")
        self.assertEqual(row["state"], "held")
        self.assertEqual(row["next_actor"], "reconcile.py")
        self.assertEqual(row["subscriber"], "plan.yml")
        self.assertIsNone(row["cadence_s"])
        self.assertTrue(row["cadence_why"].strip())
        self.assertEqual(row["emits"]["file"], WF_REL)
        self.assertIn(row["emits"]["anchor"], route_run())

    def test_the_registry_checks_clean(self):
        self.assertEqual(pipeline_act.problems(), [])

    def test_the_receipt_site_is_accepted(self):
        import check_act_receipts as car
        here = [p for p in car.problems() if "plan.yml" in p]
        self.assertEqual(here, [])
        acts = [act for path, _, act in car.shell_act_flags() if path.endswith("plan.yml")]
        self.assertIn(epic_cap.QUEUED_ACT, acts)
        composed = [s.composed_as for s in car.sites()
                    if s.path.endswith("plan.yml") and s.step == ROUTE]
        self.assertIn(epic_cap.QUEUED_ACT, composed)


# --------------------------------------------------------------------------- #
# AC8 — one record in the lane contract, and its rendering                     #
# --------------------------------------------------------------------------- #

def _arrivals(contract: dict) -> list:
    green = next(lane for lane in contract["lanes"] if lane["name"] == "Green Light")
    return green["clauses"]["entrance"]["arrivals"]


class TheArrivalRecord(unittest.TestCase):

    def test_the_record_is_declared_verbatim(self):
        contract = json.loads((ROOT / CONTRACT_REL).read_text(encoding="utf-8"))
        mine = [a for a in _arrivals(contract) if a.get("kind") == "queued-epic"]
        self.assertEqual(mine, [RECORD])

    def test_the_contract_changed_by_that_record_and_nothing_else(self):
        before, after = need_history(self, CONTRACT_REL)
        old, new = json.loads(before), json.loads(after)
        self.assertEqual(_arrivals(new)[-1], RECORD)
        _arrivals(new).pop()
        self.assertEqual(new, old)

    def test_the_doc_changed_by_that_records_rendering_and_nothing_else(self):
        before, after = need_history(self, CONTRACT_REL)
        doc_before, doc_after = need_history(self, DOC_REL)
        self.assertEqual(doc_before, lane_contract.render_markdown(json.loads(before)))
        self.assertEqual(doc_after, lane_contract.render_markdown(json.loads(after)))


if __name__ == "__main__":
    unittest.main()
