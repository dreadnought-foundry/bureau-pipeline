"""plan.yml claims a planner slot before any model step (DRE-5179, epic DRE-5167).

THE GAP. `planner_queue.py` (DRE-5176) keeps the fleet-wide planner slot
ledger on Linear, and the sweep (DRE-5178) serves the line it keeps — but until
a planner run CLAIMS a slot, nothing counts the fleet. The reusable ran the
card-validation gate, the duplicate-dispatch guard, three pool-probe mints and
the classifier — the first model call — with nothing in between asking how
many planners were already running.

THE WIRING UNDER TEST:

  1. one step, `id: slot`, between `Duplicate-dispatch guard` and
     `Mint pool probe token (2)` — so before the route step and before every
     model step — runs `planner_queue.py claim` with every value through
     `env:` (DRE-1996), `SENT_BY_RUN` spelled exactly as the duplicate guard
     spells it and `REASON` exactly as the route step does;
  2. every step gated on the duplicate guard is gated on the slot too — the
     twelve read off the file on 2026-09-29 are CONTAINED in what the property
     finds, so a step dropped from the chain is a finding and a step a later
     card adds to it is simply a thirteenth;
  3. no `concurrency:` group implements the cap — a group keeps one pending
     run and cancels the rest, which is the "dropped" the epic forbids;
  4. a run the slot refuses reaches no model step, not the route step, and no
     step that writes `Green Light` — evaluated by walking every step's `if:`
     through the step outputs it names, never by reading step names;
  5. the slot step changed no other step's shell, and the route step's shell
     is byte-for-byte what it was, so DRE-5136's later edit inside it lands on
     the text it read.

Run: cd bureau-pipeline && python3 -m pytest tests/test_planner_queue_wiring.py -v
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import unittest

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")
# The planner's own Linear bucket first, the fleet key otherwise (DRE-5589).
PLANNER_KEY_EXPR = "${{ secrets.LINEAR_PLANNER_KEY || secrets.LINEAR_API_KEY }}"
WF = os.path.join(WORKFLOWS, "plan.yml")
WF_REL = ".github/workflows/plan.yml"
SELF_PLAN = os.path.join(WORKFLOWS, "self-plan.yml")
QUEUE = os.path.join(ROOT, "scripts", "planner_queue.py")

ACTION = "anthropics/claude-code-action"
SLOT = "slot"
DEDUPE = "Duplicate-dispatch guard"
FIRST_PROBE = "Mint pool probe token (2)"
ROUTE = "Route — plan or activate"
DEDUPE_CLAUSE = "steps.dedupe.outputs.skip != 'true'"
SLOT_CLAUSE = "steps.slot.outputs.admitted == 'true'"

# Every step whose `if:` carried the duplicate guard's clause, by `id:`, read
# off the file on 2026-09-29. Asserted CONTAINED in what the property finds.
DEDUPE_GATED = {
    "probe_2", "probe_3", "probe_4", "pool", "reader", "classify", "shape",
    "repo", "model", "route", "card", "install_claude",
}
FLAGS = ("--run-id", "--repo", "--trigger-state", "--sent-by-run", "--reason",
         "--github-output")
ENV_KEYS = ("LINEAR_API_KEY", "CARD", "TRIGGER_STATE", "SENT_BY_RUN", "REASON")
# Any of these inside a concurrency group would be a group implementing the cap.
CAP_GROUP_WORDS = ("planner-slot", "planner-queue", "planner_queue", "planner-cap",
                   "agent-plan")


def wf_src() -> str:
    with open(WF, encoding="utf-8") as fh:
        return fh.read()


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def steps(doc: dict | None = None) -> list[dict]:
    return (doc or load(WF))["jobs"]["plan"]["steps"]


def index_where(pred, doc: dict | None = None) -> int:
    for i, s in enumerate(steps(doc)):
        if pred(s):
            return i
    raise AssertionError("no such step in plan.yml")


def by_id(ident: str) -> dict:
    return steps()[index_where(lambda s: s.get("id") == ident)]


def by_name(name: str) -> dict:
    return steps()[index_where(lambda s: s.get("name") == name)]


def gate(step: dict) -> str:
    return str(step.get("if") or "")


# --------------------------------------------------------------------------- #
# A three-valued reading of an Actions `if:` expression                        #
# --------------------------------------------------------------------------- #
#
# A value is a Python str / bool / float / None when the walk KNOWS it, and an
# `Unknown` when it does not — any output of a step that may have run, any
# `env.` / `github.` / `inputs.` / `vars.` read, `success()` / `failure()`.
# A step is unreachable only when its gate is KNOWN falsy; an unknown gate
# might run. The walk is therefore conservative in the direction that matters:
# it can call a step reachable that is not, never the reverse.


class Unknown:
    """A value the walk cannot know, with the truthiness it CAN know, if any."""

    def __init__(self, truth: bool | None = None):
        self.truth = truth


def truth(value) -> bool | None:
    if isinstance(value, Unknown):
        return value.truth
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value != ""
    if isinstance(value, float):
        return value != 0
    return bool(value)


_TOKEN = re.compile(r"""
    \s*(?:
      (?P<str>'(?:[^']|'')*')
     |(?P<num>-?\d+(?:\.\d+)?)
     |(?P<op>==|!=|<=|>=|&&|\|\||[!<>(),\[\]])
     |(?P<name>[A-Za-z_][A-Za-z0-9_\-]*(?:\.[A-Za-z_*][A-Za-z0-9_\-]*)*)
    )""", re.X)


def tokenize(expr: str) -> list[tuple[str, str]]:
    expr = expr.strip()
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2]
    out, at = [], 0
    while at < len(expr):
        if expr[at:].strip() == "":
            break
        m = _TOKEN.match(expr, at)
        if not m or m.end() == at:
            raise ValueError(f"cannot read {expr[at:]!r} in {expr!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        at = m.end()
    return out


class Walk:
    """Evaluates `if:` expressions against what the walk knows so far."""

    def __init__(self, known: dict[str, object], skipped: set[str]):
        self.known = known
        self.skipped = skipped

    def evaluate(self, expr: str):
        self.toks, self.at = tokenize(expr), 0
        value = self._or()
        if self.at != len(self.toks):
            raise ValueError(f"trailing tokens in {expr!r}")
        return value

    def _peek(self):
        return self.toks[self.at] if self.at < len(self.toks) else (None, None)

    def _take(self, text: str | None = None):
        tok = self._peek()
        if text is not None and tok[1] != text:
            raise ValueError(f"expected {text!r}, found {tok[1]!r}")
        self.at += 1
        return tok

    def _or(self):
        left = self._and()
        while self._peek()[1] == "||":
            self._take()
            right = self._and()
            t = truth(left)
            if t is True:
                continue
            if t is False:
                left = right
            else:
                left = Unknown(True if truth(right) is True else None)
        return left

    def _and(self):
        left = self._cmp()
        while self._peek()[1] == "&&":
            self._take()
            right = self._cmp()
            t = truth(left)
            if t is False:
                continue
            if t is True:
                left = right
            else:
                left = Unknown(False if truth(right) is False else None)
        return left

    def _cmp(self):
        left = self._unary()
        while self._peek()[1] in ("==", "!=", "<", ">", "<=", ">="):
            op = self._take()[1]
            right = self._unary()
            if isinstance(left, Unknown) or isinstance(right, Unknown):
                left = Unknown()
                continue
            a, b = self._norm(left), self._norm(right)
            if op in ("==", "!="):
                left = (a == b) if op == "==" else (a != b)
            else:
                left = Unknown()
        return left

    @staticmethod
    def _norm(value):
        if value is None:
            return ""
        return value.lower() if isinstance(value, str) else value

    def _unary(self):
        if self._peek()[1] == "!":
            self._take()
            t = truth(self._unary())
            return Unknown() if t is None else (not t)
        return self._primary()

    def _primary(self):
        kind, text = self._take()
        if text == "(":
            value = self._or()
            self._take(")")
            return value
        if kind == "str":
            return text[1:-1].replace("''", "'")
        if kind == "num":
            return float(text)
        if kind != "name":
            raise ValueError(f"unexpected {text!r}")
        if self._peek()[1] == "(":
            self._take()
            args = []
            while self._peek()[1] != ")":
                args.append(self._or())
                if self._peek()[1] == ",":
                    self._take()
            self._take(")")
            return self._call(text, args)
        value = self._name(text)
        while self._peek()[1] == "[":
            self._take()
            self._or()
            self._take("]")
            value = Unknown()
        return value

    @staticmethod
    def _call(name: str, args: list):
        if name == "always":
            return True
        return Unknown()  # success(), failure(), cancelled(), contains(), ...

    def _name(self, text: str):
        if text in ("true", "false"):
            return text == "true"
        if text == "null":
            return None
        parts = text.split(".")
        if parts[0] == "steps" and len(parts) >= 3:
            ident = parts[1]
            if ident in self.skipped:
                return "skipped" if parts[2] in ("outcome", "conclusion") else ""
            if parts[2] == "outputs" and len(parts) == 4 and text in self.known:
                return self.known[text]
        return Unknown()


def reach(known: dict[str, object]) -> dict[int, bool | None]:
    """Walk plan.yml's job in order: step index → the truth of its gate. A step
    whose gate is known falsy is skipped, and every later read of its outputs
    is empty — the propagation the gate chain relies on."""
    walk, verdict = Walk(known, set()), {}
    for i, s in enumerate(steps()):
        expr = s.get("if")
        t = None if expr is None else truth(walk.evaluate(str(expr)))
        verdict[i] = t
        if t is False and s.get("id"):
            walk.skipped.add(s["id"])
    return verdict


def must_not_run() -> dict[int, str]:
    """The route step, every model step (found by `uses:`) and every step whose
    shell writes `Green Light` (found by grep) — never by name or count."""
    out = {}
    for i, s in enumerate(steps()):
        if s.get("name") == ROUTE:
            out[i] = "the route step"
        elif str(s.get("uses") or "").split("@")[0] == ACTION:
            out[i] = "a model step"
        elif "Green Light" in str(s.get("run") or ""):
            out[i] = "a Green Light writer"
    return out


# --------------------------------------------------------------------------- #
# 1. The slot step                                                             #
# --------------------------------------------------------------------------- #


class TheSlotStep(unittest.TestCase):
    def setUp(self):
        self.step = by_id(SLOT)
        self.run_block = str(self.step.get("run") or "")
        self.env = self.step.get("env") or {}

    def test_it_sits_after_the_duplicate_guard_and_before_every_model_step(self):
        at = index_where(lambda s: s.get("id") == SLOT)
        self.assertEqual(at, index_where(lambda s: s.get("name") == DEDUPE) + 1,
                         "the slot step must follow the duplicate guard directly")
        self.assertLess(at, index_where(lambda s: s.get("name") == FIRST_PROBE))
        self.assertLess(at, index_where(lambda s: s.get("name") == ROUTE))
        first_model = index_where(
            lambda s: str(s.get("uses") or "").split("@")[0] == ACTION)
        self.assertLess(at, first_model)

    def test_it_is_gated_on_the_two_bail_gates_above_it(self):
        self.assertIn("steps.gate.outputs.bounced != 'true'", gate(self.step))
        self.assertIn(DEDUPE_CLAUSE, gate(self.step))
        self.assertNotIn("steps.slot", gate(self.step))

    def test_it_runs_the_foundation_cards_claim_with_every_flag(self):
        self.assertRegex(self.run_block,
                         r'python3 \.bureau-pipeline/scripts/planner_queue\.py claim "\$CARD"')
        for flag in FLAGS:
            self.assertRegex(self.run_block, rf"{re.escape(flag)}\s", flag)
        self.assertIn('--run-id "$GITHUB_RUN_ID"', self.run_block)
        self.assertIn('--repo "$GITHUB_REPOSITORY"', self.run_block)
        self.assertIn('--trigger-state "$TRIGGER_STATE"', self.run_block)
        self.assertIn('--sent-by-run "$SENT_BY_RUN"', self.run_block)
        self.assertIn('--reason "$REASON"', self.run_block)
        self.assertIn('--github-output "$GITHUB_OUTPUT"', self.run_block)

    def test_the_cli_accepts_every_flag_the_step_passes(self):
        """The flags are the CLI's own, read off its `--help`, so a renamed flag
        is a red test here rather than a planner run that dies at argparse."""
        out = subprocess.run([sys.executable, QUEUE, "claim", "--help"],
                             capture_output=True, text=True, check=True).stdout
        for flag in FLAGS:
            self.assertIn(flag, out, flag)

    def test_every_value_arrives_through_env_and_none_is_interpolated(self):
        for key in ENV_KEYS:
            self.assertIn(key, self.env, key)
        self.assertNotIn("${{", self.run_block)
        # The planner's own bucket first, the fleet key otherwise (DRE-5589).
        self.assertEqual(self.env["LINEAR_API_KEY"], PLANNER_KEY_EXPR)
        self.assertEqual(self.env["CARD"], "${{ github.event.client_payload.identifier }}")

    def test_the_trigger_and_sender_are_spelled_as_the_duplicate_guard_spells_them(self):
        guard = by_name(DEDUPE).get("env") or {}
        self.assertEqual(self.env["SENT_BY_RUN"], guard["SENT_BY_RUN"])
        self.assertEqual(self.env["TRIGGER_STATE"], guard["TRIGGER_STATE"])

    def test_the_reason_is_spelled_as_the_route_step_spells_it(self):
        self.assertEqual(self.env["REASON"], (by_name(ROUTE).get("env") or {})["REASON"])

    def test_the_step_exits_zero_on_every_path(self):
        """`claim` fails open to `admitted=true` itself; a crash of the script
        before it can answer is admitted too, and said, never a red planner."""
        self.assertNotIn("continue-on-error", self.step,
                         "a failed-but-continued step would leave `admitted` unset "
                         "and skip the whole run silently")
        self.assertRegex(self.run_block, r"\|\|")
        self.assertIn("admitted=true", self.run_block)
        self.assertIn("::warning::", self.run_block)

    def test_its_comment_block_answers_the_five_questions_and_the_consequences(self):
        src = wf_src()
        end = src.index("        id: slot\n")
        start = src.rindex("      - name: " + DEDUPE, 0, end)
        start = src.index("\n\n", start)  # past the guard's own body
        block = src[start:end]
        for q in ("Q1", "Q2", "Q3", "Q4", "Q5"):
            self.assertIn(q, block, q)
        for word in ("DRE-5136", "roll-up", "--sent-by-run", "review_rerun.py dispatch",
                     "DRE-5177", "::warning::"):
            self.assertIn(word, block, word)


# --------------------------------------------------------------------------- #
# 2. The gate chain                                                            #
# --------------------------------------------------------------------------- #


class TheChain(unittest.TestCase):
    def test_every_step_the_duplicate_guard_gates_the_slot_gates_too(self):
        chained = [s for s in steps()
                   if DEDUPE_CLAUSE in gate(s) and s.get("id") != SLOT]
        self.assertTrue(chained)
        for s in chained:
            self.assertIn(SLOT_CLAUSE, gate(s), s.get("name"))

    def test_the_twelve_are_contained_in_what_the_property_finds(self):
        found = {s.get("id") for s in steps()
                 if DEDUPE_CLAUSE in gate(s) and s.get("id") != SLOT}
        self.assertLessEqual(DEDUPE_GATED, found,
                             f"dropped from the chain: {sorted(DEDUPE_GATED - found)}")


# --------------------------------------------------------------------------- #
# 3. No concurrency group implements the cap                                   #
# --------------------------------------------------------------------------- #


def _group(conc) -> str | None:
    if conc is None:
        return None
    if isinstance(conc, dict):
        return str(conc.get("group") or "")
    return str(conc)


class NoConcurrencyGroup(unittest.TestCase):
    def test_the_reusable_declares_none_at_either_level(self):
        doc = load(WF)
        self.assertNotIn("concurrency", doc)
        for name, job in doc["jobs"].items():
            self.assertNotIn("concurrency", job, name)

    def test_the_self_plan_stub_keeps_its_per_card_group(self):
        conc = load(SELF_PLAN)["concurrency"]
        self.assertEqual(conc["group"], "agent-${{ github.event.client_payload.identifier }}")
        self.assertIs(conc["cancel-in-progress"], False)

    def test_no_workflow_names_the_cap_in_a_group(self):
        files = sorted(glob.glob(os.path.join(WORKFLOWS, "*.yml")))
        self.assertTrue(files)
        for path in files:
            doc = load(path) or {}
            groups = [_group(doc.get("concurrency"))]
            groups += [_group((job or {}).get("concurrency"))
                       for job in (doc.get("jobs") or {}).values()]
            for g in filter(None, groups):
                for word in CAP_GROUP_WORDS:
                    self.assertNotIn(word, g, f"{os.path.basename(path)}: {g}")


# --------------------------------------------------------------------------- #
# 4. A refused run reaches nothing that costs a model or writes a lane          #
# --------------------------------------------------------------------------- #


class TheWalk(unittest.TestCase):
    """The evaluator itself, on the shapes plan.yml writes."""

    def test_a_skipped_steps_outputs_read_empty_downstream(self):
        w = Walk({}, {"a"})
        self.assertIs(truth(w.evaluate("steps.a.outputs.mode == 'plan'")), False)
        self.assertIs(truth(w.evaluate("steps.a.outputs.x != 'true'")), True)
        self.assertIs(truth(w.evaluate("steps.a.outcome == 'failure'")), False)

    def test_unknown_is_never_read_as_false(self):
        w = Walk({}, set())
        self.assertIsNone(truth(w.evaluate("steps.a.outputs.mode == 'plan'")))
        self.assertIsNone(truth(w.evaluate("env.X != '' && success()")))
        self.assertIs(truth(w.evaluate("always() || steps.a.outputs.y == 'z'")), True)
        self.assertIs(truth(w.evaluate("!cancelled() && ('a' == 'b')")), False)

    def test_known_values_and_kleene_logic(self):
        w = Walk({"steps.s.outputs.admitted": "false"}, set())
        self.assertIs(truth(w.evaluate("${{ steps.s.outputs.admitted == 'true' }}")), False)
        self.assertIs(truth(w.evaluate(
            "steps.g.outputs.b != 'true' && steps.s.outputs.admitted == 'true'")), False)
        self.assertIsNone(truth(w.evaluate(
            "steps.g.outputs.b != 'true' || steps.s.outputs.admitted == 'true'")))


class ARefusedRun(unittest.TestCase):
    REFUSED = {"steps.slot.outputs.admitted": "false"}
    ADMITTED = {"steps.slot.outputs.admitted": "true"}

    def test_the_targets_are_found(self):
        kinds = set(must_not_run().values())
        self.assertEqual(kinds, {"the route step", "a model step", "a Green Light writer"})

    def test_it_reaches_no_model_step_no_route_and_no_green_light_writer(self):
        verdict = reach(self.REFUSED)
        names = [s.get("name") for s in steps()]
        for i, why in must_not_run().items():
            self.assertIs(verdict[i], False,
                          f"{why} {names[i]!r} is reachable on a refused run")

    def test_an_admitted_run_can_still_reach_every_one_of_them(self):
        """The non-vacuous twin: the walk is not marking everything false."""
        verdict = reach(self.ADMITTED)
        names = [s.get("name") for s in steps()]
        for i, why in must_not_run().items():
            self.assertIsNot(verdict[i], False,
                             f"{why} {names[i]!r} is unreachable even when admitted")

    def test_the_run_ends_green_with_its_receipt_steps(self):
        """The death-cause receipt and the working log are `always()` and still
        run on a refused run — it ends GREEN, having written its receipt."""
        verdict = reach(self.REFUSED)
        kept = ("Keep the run's death-cause receipt", "Keep the run's working log")
        found = [i for i, s in enumerate(steps()) if s.get("name") in kept]
        self.assertEqual(len(found), len(kept))
        for i in found:
            self.assertIs(verdict[i], True, steps()[i].get("name"))


# --------------------------------------------------------------------------- #
# 5. No other shell changed                                                    #
# --------------------------------------------------------------------------- #


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, check=False)
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def _before_and_after() -> tuple[str, str] | None:
    """plan.yml before and after the slot step arrived: the commit that first
    added `planner_queue.py claim` against its parent, or — before that commit
    exists — the working tree against its merge base with `origin/main`."""
    log = _git("log", "--format=%H", "--reverse", "-S", "planner_queue.py claim",
               "--", WF_REL)
    first = (log or "").split()
    if first:
        before = _git("show", f"{first[0]}^:{WF_REL}")
        after = _git("show", f"{first[0]}:{WF_REL}")
        if before is not None and after is not None:
            return before, after
    base = (_git("merge-base", "HEAD", "origin/main") or "").strip()
    if base:
        before = _git("show", f"{base}:{WF_REL}")
        if before is not None:
            return before, wf_src()
    return None


def _keyed(step_list: list[dict]) -> dict[tuple, dict]:
    """Steps keyed by name — or, for an unnamed step such as the job's two
    `actions/checkout`s, by its `uses:` — and by occurrence, so two steps that
    share a key are paired in order rather than collapsed into one."""
    seen: dict[str, int] = {}
    out = {}
    for s in step_list:
        base = s.get("name") or s.get("uses") or s.get("id") or ""
        n = seen.get(base, 0)
        seen[base] = n + 1
        out[(base, n)] = s
    return out


class NoOtherShellChanged(unittest.TestCase):
    def test_the_route_steps_shell_names_no_slot(self):
        """Readable with no history: the slot clause lives in the route step's
        `if:` alone — its shell is the text DRE-5136 edits later."""
        run = str(by_name(ROUTE).get("run") or "")
        for word in ("slot", "admitted", "planner_queue"):
            self.assertNotIn(word, run)

    def test_the_slot_step_changed_no_shell_but_its_own(self):
        pair = _before_and_after()
        if pair is None:
            # The unit job checks out full history and sets this, so there the
            # missing history is a failure, never a green skip.
            if os.environ.get("BUREAU_REQUIRE_GIT_HISTORY"):
                self.fail("no git history for plan.yml, and this job requires it")
            self.skipTest("no git history for plan.yml (a shallow checkout)")
        before = _keyed(yaml.safe_load(pair[0])["jobs"]["plan"]["steps"])
        after = _keyed(yaml.safe_load(pair[1])["jobs"]["plan"]["steps"])
        added = [s for k, s in after.items() if k not in before]
        self.assertEqual([s.get("id") for s in added], [SLOT])
        for key, s in after.items():
            if s.get("id") == SLOT:
                continue
            old = before[key]
            self.assertEqual(s.get("run"), old.get("run"), s.get("name"))
            self.assertEqual(s.get("with"), old.get("with"), s.get("name"))
            if s.get("name") == ROUTE:
                self.assertIsNotNone(s.get("run"))
                self.assertEqual(s["run"], old["run"])


# =========================================================================== #
# DRE-5180 — the run releases its slot at its end and starts the next card     #
# =========================================================================== #
#
# The back half of the cap: when a planner run ends — success, failure, or a
# death the job survives to report — it releases its slot and dispatches the
# next card in line itself, without waiting for the fifteen-minute sweep. Four
# steps, immediately before `Keep the run's death-cause receipt`, which stays
# the last receipt in the job. Placement is read off NEIGHBORS, never a count:
# after every step whose `uses:` is the model action, and after every step
# whose shell runs `review_rerun.py dispatch` (the handover only works because
# the sender dispatches BEFORE it releases).

DEATH = "Keep the run's death-cause receipt"
END_IDS = ["release", "next", "next_token", "next_dispatch"]
END_RUN_IDS = ("release", "next", "next_dispatch")
RERUN_DISPATCH = "review_rerun.py dispatch"
MINT = "actions/create-github-app-token"
ALWAYS_ADMITTED = "always() && steps.slot.outputs.admitted == 'true'"
NEXT_ENV = {
    "NEXT_CARD": "${{ steps.next.outputs.card }}",
    "NEXT_REPO": "${{ steps.next.outputs.repo }}",
    "NEXT_TRIGGER": "${{ steps.next.outputs.trigger_state }}",
    "NEXT_REASON": "${{ steps.next.outputs.reason }}",
}


def uses(step: dict) -> str:
    return str(step.get("uses") or "").split("@")[0]


def end_indexes() -> list[int]:
    return [index_where(lambda s, i=i: s.get("id") == i) for i in END_IDS]


class TheEndOfRunSteps(unittest.TestCase):
    def test_the_four_sit_in_order_immediately_before_the_death_receipt(self):
        at = end_indexes()
        death = index_where(lambda s: s.get("name") == DEATH)
        self.assertEqual(at, list(range(death - 4, death)),
                         "release, next, next_token, next_dispatch must be the four "
                         "steps directly above the death-cause receipt, in that order")

    def test_they_come_after_every_model_step(self):
        first = min(end_indexes())
        models = [i for i, s in enumerate(steps()) if uses(s) == ACTION]
        self.assertTrue(models, "no model step found by `uses:`")
        for i in models:
            self.assertLess(i, first, steps()[i].get("name"))

    def test_they_come_after_every_review_rerun_dispatch(self):
        """The handover: the sender asks for its own card's next run BEFORE it
        releases, so the started run finds the sender's claim still open."""
        first = min(end_indexes())
        senders = [i for i, s in enumerate(steps())
                   if RERUN_DISPATCH in str(s.get("run") or "")]
        self.assertTrue(senders, f"no step runs `{RERUN_DISPATCH}`")
        for i in senders:
            self.assertLess(i, first, steps()[i].get("name"))

    def test_every_step_is_always_and_continue_on_error(self):
        for ident in END_IDS:
            s = by_id(ident)
            self.assertTrue(gate(s).startswith("always() && "), ident)
            self.assertIs(s.get("continue-on-error"), True, ident)
        for ident in END_RUN_IDS:
            self.assertTrue(by_id(ident).get("run"), ident)

    def test_release_and_next_are_gated_on_admission(self):
        for ident in ("release", "next"):
            self.assertEqual(gate(by_id(ident)), ALWAYS_ADMITTED, ident)

    def test_release_runs_the_foundation_cards_release(self):
        s = by_id("release")
        run = str(s["run"])
        self.assertIn('python3 .bureau-pipeline/scripts/planner_queue.py release "$CARD"',
                      run)
        self.assertIn('--run-id "$GITHUB_RUN_ID"', run)
        self.assertRegex(run, r"--because finished\b")
        env = s.get("env") or {}
        self.assertEqual(env.get("CARD"), "${{ github.event.client_payload.identifier }}")
        self.assertEqual(env.get("LINEAR_API_KEY"), PLANNER_KEY_EXPR)

    def test_next_writes_its_answer_to_the_step_outputs(self):
        s = by_id("next")
        self.assertIn("python3 .bureau-pipeline/scripts/planner_queue.py next "
                      '--github-output "$GITHUB_OUTPUT"', str(s["run"]))
        self.assertEqual((s.get("env") or {}).get("LINEAR_API_KEY"),
                         PLANNER_KEY_EXPR)

    def test_the_mint_is_for_the_next_cards_owner(self):
        s = by_id("next_token")
        self.assertEqual(uses(s), MINT)
        self.assertEqual(gate(s), "always() && steps.next.outputs.repo != ''")
        w = s.get("with") or {}
        self.assertEqual(w.get("app-id"), "${{ secrets.BUREAU_APP_ID }}")
        self.assertEqual(w.get("private-key"), "${{ secrets.BUREAU_APP_PRIVATE_KEY }}")
        self.assertEqual(w.get("owner"), "${{ steps.next.outputs.owner }}")
        self.assertEqual(w.get("repositories"), "${{ steps.next.outputs.name }}")
        # Pinned exactly as every other mint in this file is pinned.
        other = next(x for x in steps() if uses(x) == MINT and x is not s)
        self.assertEqual(s["uses"], other["uses"])

    def test_the_dispatch_carries_the_minted_token_and_the_four_values(self):
        s = by_id("next_dispatch")
        self.assertEqual(gate(s), "always() && steps.next_token.outcome == 'success'")
        env = s.get("env") or {}
        self.assertEqual(env.get("GH_TOKEN"), "${{ steps.next_token.outputs.token }}")
        for key, value in NEXT_ENV.items():
            self.assertEqual(env.get(key), value, key)
        run = str(s["run"])
        self.assertIn('python3 .bureau-pipeline/scripts/planner_queue.py dispatch '
                      '"$NEXT_CARD"', run)
        for flag in ('--run-id "$GITHUB_RUN_ID"', '--repo "$NEXT_REPO"',
                     '--trigger-state "$NEXT_TRIGGER"', '--reason "$NEXT_REASON"'):
            self.assertIn(flag, run, flag)

    def test_the_dispatch_never_sends_the_sender(self):
        """This run is not the next card's planner: the started run's guard and
        slot step judge it as any dispatch."""
        s = by_id("next_dispatch")
        text = str(s.get("run")) + " " + " ".join(f"{k}={v}" for k, v in
                                                  (s.get("env") or {}).items())
        for word in ("sent_by_run", "sent-by-run", "SENT_BY_RUN"):
            self.assertNotIn(word, text)

    def test_no_value_is_interpolated_into_a_shell_line(self):
        for ident in END_RUN_IDS:
            self.assertNotIn("${{", str(by_id(ident)["run"]), ident)

    def test_the_cli_accepts_every_flag_the_steps_pass(self):
        for cmd, flags in (("release", ("--run-id", "--because")),
                           ("next", ("--github-output",)),
                           ("dispatch", ("--run-id", "--repo", "--trigger-state",
                                         "--reason"))):
            out = subprocess.run([sys.executable, QUEUE, cmd, "--help"],
                                 capture_output=True, text=True, check=True).stdout
            for flag in flags:
                self.assertIn(flag, out, f"{cmd} {flag}")

    def test_the_comment_block_records_the_contract(self):
        src = wf_src()
        end = src.index("        id: release\n")
        start = src.rindex("\n\n", 0, end)
        block = src[start:end]
        for q in ("Q1", "Q2", "Q3", "Q4", "Q5"):
            self.assertIn(q, block, q)
        for word in ("double-finish", "duplicate", "original line entry",
                     "closes only this run's claim", "re-review", "review-retry",
                     "sent_by_run", "recorded reason", "posts nothing here",
                     "DRE-5177", "github-actions", "contents: write"):
            self.assertIn(word, block, word)


class Walk5180(Walk):
    """The walk, with the job's status known and a gate without a status
    function read as GitHub reads it: `success() && (<gate>)`."""

    STATUS = ("always(", "success(", "failure(", "cancelled(")

    def __init__(self, known, skipped, job_failed: bool):
        super().__init__(known, skipped)
        self.job_failed = job_failed

    def evaluate(self, expr: str):
        body = str(expr).strip()
        if body.startswith("${{") and body.endswith("}}"):
            body = body[3:-2]
        if not any(f in body for f in self.STATUS):
            body = f"success() && ({body})"
        return super().evaluate(body)

    def _call(self, name, args):
        if name == "success":
            return not self.job_failed
        if name == "failure":
            return self.job_failed
        if name == "cancelled":
            return False
        return Walk._call(name, args)

    def _name(self, text):
        if text in self.known:
            return self.known[text]
        return super()._name(text)


def end_reach(admitted: str, job_failed: bool) -> dict[str, bool | None]:
    """The four gates, in file order, as a job that got this far reads them:
    `next` named a card and the mint succeeded wherever they ran."""
    walk = Walk5180({"steps.slot.outputs.admitted": admitted,
                     "steps.next.outputs.repo": "dreadnought-foundry/portico",
                     "steps.next_token.outcome": "success"}, set(), job_failed)
    out = {}
    for ident in END_IDS:
        t = truth(walk.evaluate(gate(by_id(ident))))
        out[ident] = t
        if t is False:
            walk.skipped.add(ident)
            for key in [k for k in walk.known if k.startswith(f"steps.{ident}.")]:
                del walk.known[key]
    return out


class TheEndOfRunGates(unittest.TestCase):
    def test_the_walk_reads_a_bare_gate_as_success_only(self):
        """The non-vacuous twin: a gate with no `always()` IS unreachable in a
        failed job, so the reachability below is the `always()` doing it."""
        w = Walk5180({"steps.slot.outputs.admitted": "true"}, set(), job_failed=True)
        self.assertIs(truth(w.evaluate("steps.slot.outputs.admitted == 'true'")), False)
        w = Walk5180({"steps.slot.outputs.admitted": "true"}, set(), job_failed=False)
        self.assertIs(truth(w.evaluate("steps.slot.outputs.admitted == 'true'")), True)

    def test_reachable_whether_the_planner_failed_or_succeeded(self):
        for failed in (True, False):
            with self.subTest(job_failed=failed):
                self.assertEqual(end_reach("true", failed),
                                 {ident: True for ident in END_IDS})

    def test_unreachable_when_the_run_was_not_admitted(self):
        for failed in (True, False):
            with self.subTest(job_failed=failed):
                self.assertEqual(end_reach("false", failed),
                                 {ident: False for ident in END_IDS})

    def test_a_refused_run_reaches_none_of_them_in_the_whole_file(self):
        verdict = reach(ARefusedRun.REFUSED)
        for i in end_indexes():
            self.assertIs(verdict[i], False, steps()[i].get("id"))


def _end_before_and_after() -> tuple[str, str] | None:
    """plan.yml before and after the four steps arrived: the commit that first
    added `planner_queue.py next` against its parent, or — before that commit
    exists — the working tree against its merge base with `origin/main`."""
    log = _git("log", "--format=%H", "--reverse", "-S", "planner_queue.py next",
               "--", WF_REL)
    first = (log or "").split()
    if first:
        before = _git("show", f"{first[0]}^:{WF_REL}")
        after = _git("show", f"{first[0]}:{WF_REL}")
        if before is not None and after is not None:
            return before, after
    base = (_git("merge-base", "HEAD", "origin/main") or "").strip()
    if base:
        before = _git("show", f"{base}:{WF_REL}")
        if before is not None:
            return before, wf_src()
    return None


class TheEndStepsChangedNoOtherStep(unittest.TestCase):
    def test_no_pre_existing_steps_if_or_run_changed(self):
        pair = _end_before_and_after()
        if pair is None:
            if os.environ.get("BUREAU_REQUIRE_GIT_HISTORY"):
                self.fail("no git history for plan.yml, and this job requires it")
            self.skipTest("no git history for plan.yml (a shallow checkout)")
        before = _keyed(yaml.safe_load(pair[0])["jobs"]["plan"]["steps"])
        after = _keyed(yaml.safe_load(pair[1])["jobs"]["plan"]["steps"])
        added = [s.get("id") for k, s in after.items() if k not in before]
        self.assertEqual(added, END_IDS)
        self.assertEqual([k for k in before if k not in after], [])
        for key, old in before.items():
            new = after[key]
            for field in ("if", "run", "with", "uses"):
                self.assertEqual(new.get(field), old.get(field), f"{key}: {field}")
        route = [k for k in after if k[0] == ROUTE]
        self.assertTrue(route)
        self.assertEqual(after[route[0]]["run"], before[route[0]]["run"])


if __name__ == "__main__":
    unittest.main()
