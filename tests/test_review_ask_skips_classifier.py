"""A review the pipeline asked for runs the second critic, never the planning
classifier, and a shape stamp is read off the card's whole thread (DRE-5644).

What happened, on DRE-3698 on 2026-10-02 (agent-bureau, plan stub on @stable):

  * 12:13 PT — the second critic sent the plan back (`stage=post round=1
    result=SEND_BACK`). The planner revised it, and at 12:30 PT run
    37051538137 dispatched the automatic re-review: reason `re-review`,
    trigger state `planning`.
  * 12:32 PT — that re-review, run 37054561328, ran `planning_classify.py
    classify` instead of the second critic. The classifier's self-check failed
    ("did not say which of our tests for an oversized card it applied"), and at
    12:33 PT the run filed a `🙋 planning-escalation` and moved the epic
    Planning → Green Light as a CEO decision.

Two halves, both pinned here:

  * plan.yml's classify step was gated only on the gate, the duplicate guard
    and the planner slot, so it ran on every admitted dispatch, the pipeline's
    own review asks included. The route step that knows a run is a review
    comes after it.
  * `planning_classify.run` reads the shape stamp to decide whether the card
    is classified already, and it read it off `comment_bodies`, the fifty
    newest comments. DRE-3698's `🧩 planning-shape: epic` stamp was the 51st
    newest at 12:32 PT, so the classifier saw an unstamped card and read it
    again. `planning_route.py decide` reads the stamp through the same window.

The fixture is DRE-3698's thread verbatim, cut at the moment the classifier
read it.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
WF = os.path.join(ROOT, ".github", "workflows", "plan.yml")
SCRIPTS = os.path.join(ROOT, "scripts")
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "dre5644_rereview_thread.json")
sys.path.insert(0, SCRIPTS)

import linear_ops  # noqa: E402
import planning_classify  # noqa: E402
import planning_route  # noqa: E402
import planning_shape  # noqa: E402
import review_rerun as rr  # noqa: E402

EPIC = "DRE-3698"

ASK = "Dispatch reason — a review the pipeline asked for?"
CLASSIFY = "Classify the card — one-off, epic or wave"
REFUSED = "Classification refused — park the card for the CEO"
SHAPE = "Planning shape — which route this card takes"
ROUTE = "Route — plan or activate"
SECOND_CRITIC = "Second critic — review (before Green Light)"
GREEN_LIGHT = "Epic → Green Light — both critics passed"


def _thread() -> list[str]:
    with open(FIXTURE, encoding="utf-8") as fh:
        return [row["body"] for row in json.load(fh)["thread"]]


# --------------------------------------------------------------------------- #
# The predicate: which dispatches are the pipeline's own review asks           #
# --------------------------------------------------------------------------- #


class TheReviewAskIsOneDefinition(unittest.TestCase):
    """The three reason words the pipeline sends when it asks for the second
    critic's review. `review_rerun` owns them; the workflow reads them through
    it, never as a second list."""

    def test_the_review_asks_are_the_three_pipeline_reasons(self):
        self.assertEqual(
            set(rr.REVIEW_ASKS),
            {rr.REASON_REVIEW, rr.REASON_RE_REVIEW, rr.REASON_REVIEW_RETRY},
        )

    def test_each_review_reason_is_a_review_ask(self):
        for reason in (rr.REASON_REVIEW, rr.REASON_RE_REVIEW, rr.REASON_REVIEW_RETRY):
            with self.subTest(reason=reason):
                self.assertTrue(rr.is_review_ask(reason))

    def test_nothing_else_is(self):
        """A fresh entry (no reason), a person's re-run act, and the one-off
        re-read all keep the classifier's path, exactly as before."""
        for reason in ("", None, rr.REASON_RERUN_ACT, rr.REASON_ONE_OFF_REVISE,
                       "Review", " review", "plan"):
            with self.subTest(reason=reason):
                self.assertFalse(rr.is_review_ask(reason))

    def test_the_cli_writes_the_step_output(self):
        for reason, want in ((rr.REASON_RE_REVIEW, "true"), ("", "false"),
                             (rr.REASON_RERUN_ACT, "false")):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as raw:
                out = os.path.join(raw, "out")
                self.assertEqual(
                    rr.main(["review-ask", "--reason", reason, "--github-output", out]),
                    0)
                self.assertEqual(open(out).read().strip(), f"review={want}")


# --------------------------------------------------------------------------- #
# The rail: the steps' own gates and shells, walked for one dispatch           #
# --------------------------------------------------------------------------- #

_CLAUSE = re.compile(
    r"^steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)\s*(==|!=)\s*'([^']*)'$")


def _steps() -> list[dict]:
    doc = yaml.safe_load(open(WF, encoding="utf-8").read())
    return doc["jobs"]["plan"]["steps"]


def _exact(name: str) -> dict:
    found = [s for s in _steps() if s.get("name") == name]
    if len(found) != 1:
        raise AssertionError(f"{len(found)} steps named exactly {name!r}")
    return found[0]


def _holds(step: dict, outputs: dict) -> bool:
    """Evaluate a step's `if:` the way the runner would, for the one grammar
    these steps use: `&&`-joined `steps.X.outputs.Y ==/!= 'v'`. A step that has
    not run has no outputs, and an unset output reads as ''. Any other clause
    is a refusal to guess, never a silent pass."""
    gate = str(step.get("if") or "").strip()
    if gate.startswith("${{") and gate.endswith("}}"):
        gate = gate[3:-2].strip()
    if not gate:
        return True
    for clause in (c.strip() for c in gate.split("&&")):
        m = _CLAUSE.match(clause)
        if not m:
            raise AssertionError(f"the walker cannot read {clause!r} in {gate!r}")
        value = outputs.get(m.group(1), {}).get(m.group(2), "")
        if (value == m.group(4)) != (m.group(3) == "=="):
            return False
    return True


def _read_outputs(path: str) -> dict:
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8").read().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                out[key] = value
    return out


def _run_shell(step: dict, raw: str, *, trigger: str, reason: str,
               stubs: dict) -> dict:
    """Render the step's shell as the runner would and run it. `stubs` maps a
    `.bureau-pipeline/scripts/<name>` path to the file that replaces it; every
    other script is the real one."""
    run = str(step.get("run") or "")
    for expr, value in (("github.event.client_payload.identifier", EPIC),
                        ("github.event.client_payload.trigger_state", trigger),
                        ("runner.temp", raw)):
        run = re.sub(r"\$\{\{\s*" + re.escape(expr) + r"\s*\}\}", value, run)
    if "${{" in run:
        raise AssertionError(f"an unresolved expression reached the shell: {run}")
    for name, path in stubs.items():
        run = run.replace(f".bureau-pipeline/scripts/{name}", path)
    run = run.replace(".bureau-pipeline/scripts/", SCRIPTS + os.sep)
    out = os.path.join(raw, f"out-{step.get('id')}")
    script = os.path.join(raw, f"{step.get('id')}.sh")
    with open(script, "w") as fh:
        fh.write("set -e\n" + run)
    env = dict(os.environ, GITHUB_OUTPUT=out, REASON=reason, MAX_WIP="3",
               RUNNER_TEMP=raw, GITHUB_STEP_SUMMARY=os.devnull)
    proc = subprocess.run(["bash", script], capture_output=True, text=True,
                          cwd=raw, env=env)
    if proc.returncode != 0:
        raise AssertionError(f"{step.get('name')} failed: {proc.stderr}")
    return _read_outputs(out)


def _stub_scripts(raw: str) -> dict:
    """The Linear-facing scripts the route step calls, answering as a
    planned epic with eight children whose critic thread is unremarkable."""
    lops = os.path.join(raw, "linear_ops_stub.py")
    with open(lops, "w") as fh:
        fh.write(
            "import sys\n"
            "cmd = sys.argv[1]\n"
            "if cmd == 'children':\n"
            "    print(8)\n"
            "elif cmd == 'dump-comments':\n"
            "    print('[]')\n"
            f"open({os.path.join(raw, 'linear.log')!r}, 'a')"
            ".write(' '.join(sys.argv[1:]) + '\\n')\n"
        )
    critic = os.path.join(raw, "plan_critic_stub.py")
    with open(critic, "w") as fh:
        fh.write(
            "import sys\n"
            "if sys.argv[1] == 'activate-cycle':\n"
            "    print('keep')\n"
            "else:\n"
            "    print('plan-critic-cycle: stub')\n"
        )
    # The epic cap (DRE-5136), asked where the route step would answer
    # `activate`: room in the fleet, so the activate row still activates.
    cap = os.path.join(raw, "epic_cap_stub.py")
    with open(cap, "w") as fh:
        fh.write("print('start')\n")
    return {"linear_ops.py": lops, "plan_critic.py": critic, "epic_cap.py": cap}


def walk(trigger: str, reason: str) -> tuple[list[str], dict]:
    """The steps that decide a plan run's route, for one dispatch, in order.

    The card is an epic already stamped and planned: the classifier, when it
    runs, finds the stamp, and the shape step routes it `epic`. What is under
    test is which of those steps run and where the route step sends it."""
    outputs = {"gate": {"bounced": "false"}, "dedupe": {"skip": "false"},
               "slot": {"admitted": "true"}}
    ran: list[str] = []
    with tempfile.TemporaryDirectory() as raw:
        stubs = _stub_scripts(raw)
        ask = _exact(ASK)
        if _holds(ask, outputs):
            ran.append(ASK)
            outputs[ask["id"]] = _run_shell(ask, raw, trigger=trigger,
                                            reason=reason, stubs=stubs)
        classify = _exact(CLASSIFY)
        if _holds(classify, outputs):
            ran.append(CLASSIFY)
            outputs[classify["id"]] = {"escalate": "false", "requeue": "false",
                                       "shape": "epic", "already": "true",
                                       "answered": "false"}
        if _holds(_exact(REFUSED), outputs):
            ran.append(REFUSED)
        shape = _exact(SHAPE)
        if _holds(shape, outputs):
            ran.append(SHAPE)
            outputs[shape["id"]] = {"refused": "false", "route": "epic"}
        route = _exact(ROUTE)
        if _holds(route, outputs):
            ran.append(ROUTE)
            outputs[route["id"]] = _run_shell(route, raw, trigger=trigger,
                                              reason=reason, stubs=stubs)
        for name in (SECOND_CRITIC, GREEN_LIGHT):
            if _holds(_exact(name), outputs):
                ran.append(name)
    return ran, outputs


class AReviewAskNeverRunsTheClassifier(unittest.TestCase):
    """The route table (DRE-5280) says a review ask from any lane but In
    Progress is the REVIEW route. The classifier is a front-door step: it
    belongs to a card arriving at Planning, never to a plan being re-read."""

    def test_the_dre3698_dispatch_runs_the_second_critic_and_not_the_classifier(self):
        """(planning, re-review): run 37054561328's exact payload."""
        ran, outputs = walk(rr.TRIGGER_STATE_REVIEW, rr.REASON_RE_REVIEW)
        self.assertNotIn(CLASSIFY, ran)
        self.assertNotIn(REFUSED, ran)
        self.assertEqual(outputs["route"].get("mode"), "review")
        self.assertIn(SECOND_CRITIC, ran)
        self.assertNotIn(GREEN_LIGHT, ran,
                         "nothing but the second critic's PASS writes Green Light")

    def test_the_plan_routes_hand_off_does_the_same(self):
        """(planning, review): the plan route handing a passed plan over."""
        ran, outputs = walk(rr.TRIGGER_STATE_REVIEW, rr.REASON_REVIEW)
        self.assertNotIn(CLASSIFY, ran)
        self.assertEqual(outputs["route"].get("mode"), "review")
        self.assertIn(SECOND_CRITIC, ran)

    def test_a_review_retry_does_the_same(self):
        ran, outputs = walk(rr.TRIGGER_STATE_REVIEW, rr.REASON_REVIEW_RETRY)
        self.assertNotIn(CLASSIFY, ran)
        self.assertEqual(outputs["route"].get("mode"), "review")
        self.assertIn(SECOND_CRITIC, ran)

    def test_a_review_ask_on_an_approved_epic_skips_it_and_still_activates(self):
        """(in progress, re-review): the table keeps this on the activate
        route, and the classifier has nothing to read there either."""
        ran, outputs = walk(rr.TRIGGER_STATE_ACTIVATE, rr.REASON_RE_REVIEW)
        self.assertNotIn(CLASSIFY, ran)
        self.assertEqual(outputs["route"].get("mode"), "activate")
        self.assertNotIn(SECOND_CRITIC, ran)

    def test_a_fresh_planning_entry_still_classifies(self):
        """(planning, no reason): a card arriving at the front door."""
        ran, outputs = walk(rr.TRIGGER_STATE_PLANNING, "")
        self.assertIn(CLASSIFY, ran)
        self.assertEqual(outputs["route"].get("mode"), "plan")
        self.assertNotIn(SECOND_CRITIC, ran)

    def test_the_approval_move_still_classifies(self):
        """(in progress, no reason): the CEO's approval, unchanged."""
        ran, outputs = walk(rr.TRIGGER_STATE_ACTIVATE, "")
        self.assertIn(CLASSIFY, ran)
        self.assertEqual(outputs["route"].get("mode"), "activate")

    def test_the_ask_runs_before_the_classifier(self):
        names = [s.get("name") for s in _steps()]
        self.assertLess(names.index(ASK), names.index(CLASSIFY))

    def test_the_ask_reads_the_reason_through_env_and_the_owning_module(self):
        step = _exact(ASK)
        self.assertEqual(str((step.get("env") or {}).get("REASON") or "").strip(),
                         "${{ github.event.client_payload.reason }}")
        run = str(step.get("run") or "")
        self.assertIn("review_rerun.py review-ask", run)
        self.assertNotIn("client_payload", run, "a payload field never reaches "
                         "the shell line itself")
        for word in rr.REVIEW_ASKS:
            self.assertNotIn(word, run.replace("review-ask", "").replace(
                "review_rerun", ""), "the reason words live in review_rerun")


# --------------------------------------------------------------------------- #
# The stamp: read off the whole thread                                         #
# --------------------------------------------------------------------------- #


class TheFixtureIsTheLiveFailure(unittest.TestCase):
    def test_the_window_has_no_stamp_and_the_thread_has_one(self):
        bodies = _thread()
        self.assertEqual(len(bodies), 121)
        self.assertIsNone(planning_shape.shape_on(bodies[-linear_ops.COMMENT_WINDOW:]))
        self.assertEqual(planning_shape.shape_on(bodies), "epic")


class _WindowedLops:
    """Linear as `linear_ops` serves it: the fifty newest comments unless the
    caller asks for the whole thread."""

    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.posted: list[str] = []
        self.labels: list[str] = []
        self.reads: list[bool] = []

    def comment_bodies(self, identifier, *, whole_thread=False):
        self.reads.append(bool(whole_thread))
        if whole_thread:
            return list(self.bodies)
        return list(self.bodies[-linear_ops.COMMENT_WINDOW:])

    def count_comments(self, identifier, needle, **kwargs):
        return sum(1 for body in self.bodies if needle in body)

    def cmd_comment(self, identifier, body, *flags):
        self.posted.append(body)

    def add_label(self, identifier, label):
        self.labels.append(label)

    def gql(self, query, variables=None):  # pragma: no cover - asserted unreached
        raise AssertionError("the card was read again — the stamp was not seen")


def _never_called(model, prompt, **kwargs):  # pragma: no cover
    raise AssertionError("the classifier called a model on a card already stamped")


class TheStampIsReadOffTheWholeThread(unittest.TestCase):
    def test_the_classifier_leaves_dre3698_alone(self):
        lops = _WindowedLops(_thread())
        decision = planning_classify.run(lops, EPIC, call=_never_called,
                                         model="claude-test")
        self.assertTrue(decision.already)
        self.assertEqual(decision.shape, "epic")
        self.assertFalse(decision.escalates)
        self.assertEqual(lops.posted, [])

    def test_no_second_stamp_is_written_over_one_past_the_window(self):
        lops = _WindowedLops(_thread())
        refusal = planning_shape.stamp(lops, EPIC, "epic", "a second read",
                                       by=planning_shape.BY_PLANNER,
                                       model="claude-test")
        self.assertIsNotNone(refusal)
        self.assertEqual(lops.posted, [])
        self.assertEqual(lops.labels, [])

    def test_the_shape_step_routes_dre3698_as_an_epic(self):
        lops = _WindowedLops(_thread())
        with tempfile.TemporaryDirectory() as raw:
            out = os.path.join(raw, "out")
            with mock.patch.object(linear_ops, "comment_bodies",
                                   side_effect=lops.comment_bodies), \
                 mock.patch.object(linear_ops, "count_comments",
                                   side_effect=lops.count_comments), \
                 mock.patch.object(linear_ops, "cmd_comment",
                                   side_effect=lops.cmd_comment):
                self.assertEqual(planning_route._cmd_decide(EPIC, out), 0)
            got = _read_outputs(out)
        self.assertEqual(got.get("refused"), "false")
        self.assertEqual(got.get("route"), "epic")
        self.assertEqual(lops.posted, [], "a stamped card is told nothing")

    def test_every_reader_of_a_stamp_asks_for_the_whole_thread(self):
        """The shape CLI's read takes the same reading as the three above; a
        window here is the same bug. `planning_route._cmd_exit` is left on the
        window on purpose: its list also feeds the routing-verdict readers,
        which are not scoped to the newest return receipt (see its comment)."""
        for fn in (planning_classify.run, planning_shape.stamp,
                   planning_shape._cmd_read, planning_route._cmd_decide):
            with self.subTest(reader=fn.__qualname__):
                src = inspect.getsource(fn)
                self.assertIn("comment_bodies(", src)
                for call in re.findall(r"comment_bodies\(([^)]*)\)", src):
                    self.assertIn("whole_thread=True", call, f"{fn.__qualname__}: "
                                  f"comment_bodies({call}) reads the window")


class _PagedLinear:
    """Linear's own order: newest first, `first:` windows, `after:` toward the
    older."""

    def __init__(self, bodies):
        self.newest_first = list(reversed(bodies))
        self.queries = 0

    def gql(self, query, variables=None):
        self.queries += 1
        v = variables or {}
        start = int(v["after"]) + 1 if v.get("after") else 0
        size = (linear_ops.COMMENT_WINDOW if not v.get("after") else 100)
        nodes = [{"body": b} for b in self.newest_first[start:start + size]]
        return {"viewer": {"id": "fleet"}, "issue": {"comments": {
            "pageInfo": {"hasNextPage": start + size < len(self.newest_first),
                         "endCursor": str(start + len(nodes) - 1)},
            "nodes": nodes,
        }}}


class CommentBodiesCanReadTheWholeThread(unittest.TestCase):
    def test_whole_thread_pages_past_the_window_and_the_default_does_not(self):
        bodies = _thread()
        fake = _PagedLinear(bodies)
        with mock.patch.object(linear_ops, "gql", side_effect=fake.gql):
            self.assertEqual(linear_ops.comment_bodies(EPIC, whole_thread=True), bodies)
            self.assertEqual(fake.queries, 2, "the window, then one page of 100")
            self.assertEqual(linear_ops.comment_bodies(EPIC),
                             bodies[-linear_ops.COMMENT_WINDOW:])


if __name__ == "__main__":
    unittest.main()
