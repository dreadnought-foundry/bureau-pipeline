"""When the groomer runs — three decisions, in order, and the one rule they
leave standing: a clock may reach `propose`, and nothing but an approval
reaches `drain`.

**D5 (DRE-2683, approved by the operator on 2026-08-23):** on demand, until the
groomer's judgement has been audited. A groomer running unattended over two
hundred cards before anyone has checked its calls is the same mistake as
trusting a critic's verdicts before comparing them to a held-back set.

**The amendment (DRE-3337, green-lit 2026-09-08):** the DRAIN may also be fired
by the CEO's Approve on the console, as a `repository_dispatch` of type
`groom-drain` — "no hand dispatch, no operator script". That trigger is one
person's Approve, not a clock, and it reaches DRAIN ONLY: `mode` is a literal on
that event, so nothing in the payload can reach it, and a drain makes no model
call (DRE-3338).

**The morning proposal (DRE-3586's signed answer, 2026-09-21, absorbed by
DRE-4677):** D5 is reopened for exactly one thing — "a scheduled 06:30 PT run of
propose only. It writes one proposal comment and moves nothing; the drain still
needs my Approve. The rest of D5 stands." The run takes about eight minutes, so
DRE-4677 moved it earlier to have the proposal on the card before the 06:30
briefing is assembled, and DRE-4969 (2026-09-27) moved it to 06:00 PT after the
2026-09-26 proposal posted at 06:31. `self-groomer.yml` carries a `schedule:`
of two UTC cron lines, `0 13 * * *` and `0 14 * * *`; a `gate` job running
`scripts/groom_schedule_gate.py` (DRE-4688) decides which of the pair is 06:00
on the PT clock and whether the standing card in `GROOM_PROPOSAL_CARD` is open;
and a `schedule` job keyed on the gate's `go` calls the reusable with `mode` the
literal `propose`.

So the trigger shape is part of the contract and is asserted here against the
LIVE workflow files (the pattern tests/test_self_host_stubs.py uses). A fourth
trigger, a third cron line, a `mode` on the scheduled job that could be anything
but `propose`, or ANY other scheduled job anywhere in this repo calling
`groomer.yml` turns this red: a clock that could reach `drain` is the CEO's
Approve being bypassed, and a `propose` reachable from the approval dispatch is
an unaudited model call on a trigger nobody watches.

The rest is the wiring every workflow in this repo owes: the reusable threads
`pipeline_ref` (DRE-2026/DRE-2689), the runnable stub is watched by the medic
(DRE-2036 — an unwatched red run is a safety net that dies silently), and the
lane the drain writes into declares the groomer as one of its writers, because
the lane contract is what the harness asserts the live board against.

And since DRE-3153 the judged read (DRE-3150) is REACHABLE from here: a
reusable workflow sees only the secrets it declares, so the two model
credentials are declared and set the way `plan.yml`'s classify step sets them,
the `judgement` switch reaches the step, the job's clock covers the call's own
ceiling, and none of it touches the line naming the Linear secret.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_wiring.py -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groom_judgement  # noqa: E402
import groom_lookups  # noqa: E402
import groomer  # noqa: E402

PIPELINE = "dreadnought-foundry/bureau-pipeline"

#: The two credentials a model call needs, and which auth mode each belongs to.
MODEL_SECRETS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")

#: The step in `plan.yml` whose expressions are the ONE definition of "how this
#: repo hands a workflow step a Claude credential". Read, never restated.
PLAN_CLASSIFY_STEP = "Classify the card — one-off, epic or wave"

#: The two steps this card's assertions are about.
GROOM_STEP = "Groom"
RECEIPT_STEP = "Judgement receipt — the model that answered"

#: DRE-4972: the step of the `post` job that posts the verified record.
POST_STEP = "Post the verified proposal"

#: The drain branch of the Groom step, verbatim as it stood before DRE-4972
#: split the propose branch into compute, verify and post. The split touches
#: `propose` only; a drain that changed with it would be a change nobody
#: reviewed as one.
DRAIN_BRANCH = (
    'if [ "$MODE" = "drain" ]; then\n'
    '  if [ -z "$CARD" ]; then\n'
    '    echo "drain needs the card the approval lives on" >&2\n'
    '    exit 1\n'
    '  fi\n'
    '  # NO shaping flags (DRE-3338): the batch a drain moves is READ off\n'
    '  # the approved proposal comment on the card, so there is nothing\n'
    '  # for --capacity/--batch-cycles/--priority to shape. `--lane` is\n'
    '  # only the fallback for a record whose own lane line is unreadable.\n'
    '  python3 .bureau-pipeline/scripts/groomer.py drain \\\n'
    '    --card "$CARD" --lane "$LANE" | tee proposal.txt\n'
)

#: The Judgement receipt's condition, unchanged by its move into `post`.
RECEIPT_IF = ("inputs.mode != 'drain' && inputs.card != '' "
              "&& inputs.dry_run != 'true'")

#: The `runs-on` every reusable job here carries (tests/test_runs_on_switchable.py).
RUNS_ON = "fromJSON(vars.BUREAU_RUNS_ON || '[\"ubuntu-latest\"]')"

#: The two UTC cron lines of the morning proposal (DRE-4677, moved to 06:00 by
#: DRE-4969). Every day both fire and exactly one of them is 06:00 on the
#: `America/Los_Angeles` clock; the gate script decides which, never an offset
#: written down here.
MORNING_CRONS = ["0 13 * * *", "0 14 * * *"]

#: The gate's contract with DRE-4688, verbatim: the command the gate step runs.
GATE_COMMAND = (
    'python3 .bureau-pipeline/scripts/groom_schedule_gate.py '
    '--card "${{ vars.GROOM_PROPOSAL_CARD }}"'
)

#: Every input the scheduled job passes, and each one a literal or a repository
#: variable — nothing a person or a payload can set. `lane`, `batch_cycles` and
#: `dry_run` are ABSENT on purpose, so the reusable's own defaults apply, the
#: way the `drain` job already omits them.
SCHEDULE_WITH = {
    "pipeline_ref": "main",
    "mode": "propose",
    "judgement": "on",
    "capacity": "20",
    "priority": "agent-bureau,bureau-pipeline,portico",
    "card": "${{ vars.GROOM_PROPOSAL_CARD }}",
    "intake_hold": "${{ vars.INTAKE_HOLD }}",
}

#: The jobs of the stub that call the reusable, and the one that does not.
CALLING_JOBS = {"call", "drain", "schedule"}
STEP_JOBS = {"gate"}

#: What the three superseded records said, in the spellings they used. None of
#: them may survive anywhere the cadence is recorded (DRE-4724).
RETIRED_CADENCE = (
    "never on a schedule",
    "never a schedule",
    "there is still no `schedule:`",
    "nothing here runs on a clock",
)

#: The third decision, in every place the first two are recorded.
THIRD_DECISION = ("DRE-3586", "2026-09-21", "DRE-4677", "schedule")


def _load(name: str) -> dict:
    path = WORKFLOWS / name
    assert path.is_file(), f"missing workflow {name}"
    return yaml.safe_load(path.read_text())


def _on(doc: dict) -> dict:
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _jobs_on(doc: dict, event: str) -> dict:
    """The jobs of a stub that can run on `event`, read off their guards.

    A job with no `if` runs on every trigger the file carries; a job gated
    `github.event_name == '<event>'` runs on that one. The GUARD is what GitHub
    obeys, so it is what these assertions read — a job named `drain` proves
    nothing, and an ungated job on a two-trigger file reaches both.
    """
    out = {}
    for name, job in (doc.get("jobs") or {}).items():
        cond = " ".join(str(job.get("if") or "").split())
        if not cond or f"github.event_name == '{event}'" in cond:
            out[name] = job
    return out


def _header(name: str) -> str:
    """The comment block above a workflow's `name:` — where its decisions are
    recorded, and the half of the file a reader meets first."""
    return (WORKFLOWS / name).read_text(encoding="utf-8").split("\nname:")[0]


def _steps(doc: dict) -> list:
    out = []
    for job in (doc.get("jobs") or {}).values():
        out.extend(job.get("steps") or [])
    return out


def _step(doc: dict, name: str) -> dict:
    for step in _steps(doc):
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r}")


def _expression(value: str) -> str:
    """The inside of a `${{ … }}`, whitespace-normalised.

    So a comparison is against what the expression SAYS, not against where the
    braces and spaces landed.
    """
    text = " ".join(str(value or "").split())
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2]
    return text.strip()


def _calling_jobs(doc: dict) -> dict:
    """The jobs that carry `uses:` — the stub's calls to the reusable. The
    `gate` job has steps and no `uses:`, and is asserted on its own."""
    return {name: job for name, job in (doc.get("jobs") or {}).items()
            if "uses" in job}


def _reaches_schedule(job: dict) -> bool:
    """Can this job run on a `schedule` event?

    Only a guard that is EXACTLY `github.event_name == '<another event>'` keeps
    a job off the clock. Anything else — no guard, a guard on `needs`, a guard
    with an `||` in it — is treated as reachable, because a reading that
    guesses in the job's favor is how a scheduled drain would get through.
    """
    cond = _expression(job.get("if"))
    for event in ("workflow_dispatch", "repository_dispatch", "push",
                  "pull_request", "workflow_run", "workflow_call",
                  "issue_comment"):
        if cond == f"github.event_name == '{event}'":
            return False
    return True


def _crons(on: dict) -> list:
    return [entry.get("cron") for entry in (on.get("schedule") or [])]


class TriggerContractTest(unittest.TestCase):
    """Three triggers, and which job each of them can reach."""

    def test_the_stub_takes_exactly_three_triggers(self):
        on = _on(_load("self-groomer.yml"))
        self.assertEqual(
            set(on), {"workflow_dispatch", "repository_dispatch", "schedule"},
            "three triggers and no more: a person dispatching it, the CEO's "
            "Approve on the console (DRE-3337), and the 06:00 PT morning "
            "proposal (DRE-3586, absorbed by DRE-4677)",
        )
        self.assertEqual(
            (on["repository_dispatch"] or {}).get("types"), ["groom-drain"],
            "the contract with the console's approve mutation is ONE event "
            "type — a wider `types:` is a wider trigger than D5 was amended for",
        )

    def test_the_schedule_is_exactly_the_two_morning_crons(self):
        """Two UTC lines because GitHub's cron has no timezone field. A third
        line is a second groom a day nobody decided on."""
        self.assertEqual(_crons(_on(_load("self-groomer.yml"))), MORNING_CRONS)

    def test_the_call_and_drain_jobs_keep_their_event_guards(self):
        """Their guards are what keep the clock off them: an unguarded `call`
        would run on the schedule with every `inputs.*` empty, and an
        unguarded `drain` would put the approval's move on a cron."""
        jobs = _load("self-groomer.yml")["jobs"]
        self.assertEqual(_expression(jobs["call"].get("if")),
                         "github.event_name == 'workflow_dispatch'")
        self.assertEqual(_expression(jobs["drain"].get("if")),
                         "github.event_name == 'repository_dispatch'")
        for name in ("call", "drain"):
            self.assertFalse(_reaches_schedule(jobs[name]),
                             f"job {name!r} can run on the schedule")


class ScheduleGateTest(unittest.TestCase):
    """The `gate` job: the one job in the stub with steps of its own, because
    a job with `uses:` has none, and the two questions a scheduled groom must
    answer first — is it 06:xx PT, is the standing card open — have to be
    asked somewhere (DRE-4688)."""

    def setUp(self):
        self.job = _load("self-groomer.yml")["jobs"].get("gate")
        self.assertIsNotNone(self.job, "the stub has no `gate` job")
        self.steps = self.job.get("steps") or []

    def _gate_step(self) -> dict:
        for step in self.steps:
            if "groom_schedule_gate.py" in str(step.get("run") or ""):
                return step
        raise AssertionError("no step of the gate job runs groom_schedule_gate.py")

    def test_the_gate_runs_only_on_the_schedule(self):
        self.assertEqual(_expression(self.job.get("if")),
                         "github.event_name == 'schedule'")

    def test_the_gate_is_steps_and_calls_nothing(self):
        self.assertNotIn("uses", self.job,
                         "the gate calls a workflow — it is a script and a clock")
        self.assertNotIn("secrets", self.job,
                         "`secrets: inherit` belongs to a job with `uses:`")
        self.assertTrue(self.steps, "the gate job has no steps")

    def test_the_gate_runs_the_contract_command_with_the_standing_card(self):
        step = self._gate_step()
        self.assertIn(GATE_COMMAND, step.get("run") or "",
                      "the command is the contract shared with DRE-4688")
        self.assertTrue((ROOT / "scripts" / "groom_schedule_gate.py").is_file())

    def test_the_gate_step_holds_the_linear_key_and_no_model_credential(self):
        env = self._gate_step().get("env") or {}
        self.assertEqual(_expression(env.get("LINEAR_API_KEY")),
                         "secrets.LINEAR_API_KEY")
        for name in MODEL_SECRETS:
            self.assertNotIn(name, env, "the gate makes no model call")

    def test_the_pipeline_is_checked_out_where_the_command_looks(self):
        """This repo IS the pipeline, so the checkout is placed at
        `.bureau-pipeline` exactly as a product-repo stub would place it."""
        paths = [(s.get("with") or {}).get("path") for s in self.steps
                 if str(s.get("uses") or "").startswith("actions/checkout@")]
        self.assertIn(".bureau-pipeline", paths)

    def test_the_job_outputs_are_the_ones_the_script_writes(self):
        """READ off the script, not restated: run it outside the 06:xx hour
        (no Linear read happens there) and compare the keys it writes with
        the outputs the job declares."""
        import tempfile

        import groom_schedule_gate

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            code = groom_schedule_gate.main(
                ["--card", "", "--now", "2026-09-25T20:00:00Z",
                 "--github-output", str(out)])
            written = {line.split("=", 1)[0]
                       for line in out.read_text().splitlines()}
        self.assertEqual(code, 0, "the off-hour run must stay green")
        outputs = self.job.get("outputs") or {}
        self.assertEqual(set(outputs), written)
        step_id = self._gate_step().get("id")
        self.assertTrue(step_id, "the gate step needs an `id` to read from")
        for key in written:
            self.assertEqual(_expression(outputs[key]),
                             f"steps.{step_id}.outputs.{key}")


class ScheduledProposeTest(unittest.TestCase):
    """The `schedule` job: the morning proposal, and nothing a clock could
    turn into a drain."""

    def setUp(self):
        self.job = _load("self-groomer.yml")["jobs"].get("schedule")
        self.assertIsNotNone(self.job, "the stub has no `schedule` job")
        self.with_ = self.job.get("with") or {}

    def test_it_needs_the_gate_and_keys_on_go_and_nothing_else(self):
        needs = self.job.get("needs")
        self.assertIn(needs, ("gate", ["gate"]))
        self.assertEqual(_expression(self.job.get("if")),
                         "needs.gate.outputs.go == 'true'")

    def test_its_mode_is_the_literal_propose(self):
        mode = self.with_.get("mode")
        self.assertEqual(mode, "propose")
        self.assertNotIn(
            "${{", str(mode),
            "a computed `mode` on a clock is a route to `drain` — the "
            "decision the CEO kept for his Approve",
        )

    def test_it_passes_the_literals_and_nothing_else(self):
        """QUOTED `on` and `20`: YAML 1.1 reads a bare `on` as the boolean
        True, and the reusable compares against the string."""
        self.assertEqual(self.with_, SCHEDULE_WITH)
        for key in ("judgement", "capacity"):
            self.assertIsInstance(self.with_[key], str, f"{key} is not quoted")

    def test_it_calls_the_reusable_at_main_with_the_secrets(self):
        self.assertEqual(self.job.get("uses"),
                         f"{PIPELINE}/.github/workflows/groomer.yml@main")
        self.assertEqual(self.job.get("secrets"), "inherit")


class OnDemandTriggersTest(unittest.TestCase):
    """The two on-demand triggers, unchanged by the schedule."""

    def test_the_approval_dispatch_can_only_reach_the_drain(self):
        """The narrowing D5 was amended on. Whatever the payload carries,
        `mode` on this event is the literal `drain`."""
        jobs = _jobs_on(_load("self-groomer.yml"), "repository_dispatch")
        self.assertTrue(jobs, "nothing in the stub runs on the groom-drain event")
        for name, job in jobs.items():
            mode = (job.get("with") or {}).get("mode")
            self.assertEqual(
                mode, "drain",
                f"job {name!r} runs on the groom-drain dispatch in mode "
                f"{mode!r} — this trigger reaches the drain and nothing else",
            )
            self.assertNotIn(
                "${{", str(mode),
                f"job {name!r} computes `mode` — an expression on this event "
                f"is a route for the payload to reach it, and the card asked "
                f"for `drain` regardless of any input",
            )

    def test_the_approval_dispatch_never_proposes(self):
        """`propose` makes the model call. It is reachable from a person at
        Actions and from the morning clock behind the gate — never from the
        approval dispatch, which exists to move an approved batch."""
        doc = _load("self-groomer.yml")
        for name, job in _jobs_on(doc, "repository_dispatch").items():
            self.assertNotIn(
                "propose", str((job.get("with") or {}).get("mode")),
                f"job {name!r} can propose off the approval dispatch",
            )
        offers_propose = doc["jobs"]["call"]
        self.assertEqual(
            _expression(offers_propose.get("if")),
            "github.event_name == 'workflow_dispatch'",
            "the job carrying the propose/drain choice must be guarded off "
            "the dispatch event — ungated it runs there too, with every "
            "`inputs.*` rendering empty",
        )

    def test_the_payload_card_is_what_the_drain_is_pointed_at(self):
        job = _load("self-groomer.yml")["jobs"]["drain"]
        self.assertEqual(
            _expression((job.get("with") or {}).get("card")),
            "github.event.client_payload.card",
            "the contract is `client_payload: {\"card\": \"DRE-N\", …}` and "
            "`card` is the only field the workflow reads",
        )

    def test_a_dispatch_with_no_card_falls_to_the_drains_own_refusal(self):
        """No `|| 'DRE-…'` and no second default: a payload without a card
        reaches the reusable empty, and the drain's own one-sentence refusal
        fires. A default invented here would point a live drain at whichever
        card the fallback named."""
        card = str((_load("self-groomer.yml")["jobs"]["drain"].get("with") or {})
                   .get("card"))
        self.assertNotIn(
            "||", card,
            "the stub supplies a fallback card — a silent default on the one "
            "input that decides which batch moves",
        )
        reusable = _load("groomer.yml")
        spec = (_on(reusable)["workflow_call"].get("inputs") or {})["card"]
        self.assertEqual(spec.get("default"), "",
                         "the reusable's own `card` default is no longer empty")
        self.assertIn(
            "drain needs the card", _step(reusable, GROOM_STEP).get("run") or "",
            "the refusal this path relies on is gone from the Groom step",
        )

    def test_the_drain_dispatch_asks_for_no_judgement(self):
        judgement = (_load("self-groomer.yml")["jobs"]["drain"].get("with")
                     or {}).get("judgement")
        self.assertEqual(
            judgement, "off",
            "QUOTED in the YAML, both here and there: YAML 1.1 reads a bare "
            "`off` as the boolean False, which is not the string the reusable "
            "compares against",
        )

    def test_the_drain_dispatch_leaves_every_other_input_at_its_default(self):
        """Passing an input is not the same as omitting it — a reusable
        workflow applies its default only to an input the caller left out, so
        an `${{ inputs.lane }}` on this event would hand it the empty string
        rather than `Intake`."""
        job = _load("self-groomer.yml")["jobs"]["drain"]
        self.assertEqual(
            set(job.get("with") or {}),
            {"pipeline_ref", "mode", "card", "judgement", "intake_hold"},
        )

    def test_the_pen_switch_is_read_on_every_trigger(self):
        """DRE-3035/DRE-3285: the hold is a repository variable, and a drain
        that cannot see it is a pen with a hole in it — on any trigger. Every
        job that carries `uses:`; the gate job calls nothing, so it has no
        `with:` to carry it."""
        jobs = _calling_jobs(_load("self-groomer.yml"))
        self.assertEqual(set(jobs), CALLING_JOBS)
        for name, job in jobs.items():
            self.assertEqual(
                _expression((job.get("with") or {}).get("intake_hold")),
                "vars.INTAKE_HOLD",
                f"job {name!r} does not read the pen's switch",
            )

    def test_the_only_scheduled_groomer_call_is_this_stubs_propose(self):
        """EVERY scheduled workflow in the repo, and EVERY job in each — not
        only the first. A job that can run on a `schedule` event and calls
        `groomer.yml` is allowed in exactly one place: this stub's `schedule`
        job, in mode `propose`, as a literal. A clock that could reach `drain`
        is the CEO's Approve bypassed."""
        found = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            doc = yaml.safe_load(path.read_text())
            if not isinstance(doc, dict) or "schedule" not in _on(doc):
                continue
            for name, job in (doc.get("jobs") or {}).items():
                if not isinstance(job, dict):
                    continue
                if "groomer.yml" not in str(job.get("uses") or ""):
                    continue
                if not _reaches_schedule(job):
                    continue
                found.append((path.name, name))
                mode = (job.get("with") or {}).get("mode")
                self.assertEqual(
                    mode, "propose",
                    f"{path.name}:{name} runs the groomer on a schedule in "
                    f"mode {mode!r}",
                )
                self.assertNotIn("${{", str(mode),
                                 f"{path.name}:{name} computes its mode")
        self.assertEqual(
            found, [("self-groomer.yml", "schedule")],
            "the morning proposal is the only scheduled groom there is",
        )

    def test_every_calling_job_calls_the_reusable_at_the_qualified_ref(self):
        """One calling job per trigger, all three the same reusable at the same
        ref with the same secrets. The `gate` is the one job with steps, and
        it is the only one allowed to have them."""
        doc = _load("self-groomer.yml")
        jobs = _calling_jobs(doc)
        self.assertEqual(set(jobs), CALLING_JOBS)
        self.assertEqual(set(doc["jobs"]) - set(jobs), STEP_JOBS,
                         "a job that neither calls the reusable nor is the gate")
        for name, job in jobs.items():
            self.assertEqual(
                job.get("uses"),
                f"{PIPELINE}/.github/workflows/groomer.yml@main",
                f"job {name!r} calls something else",
            )
            self.assertEqual(job.get("secrets"), "inherit", f"job {name!r}")
            self.assertEqual(
                _expression((job.get("with") or {}).get("pipeline_ref")), "main",
                f"job {name!r} does not thread pipeline_ref (DRE-2689)",
            )

    def test_the_stub_is_named_groomer_and_the_medic_watches_it(self):
        self.assertEqual(_load("self-groomer.yml").get("name"), "Groomer")
        watched = (_on(_load("self-medic.yml")).get("workflow_run") or {}).get(
            "workflows") or []
        self.assertIn(
            "Groomer", watched,
            "DRE-2036: every workflow that runs under its own name is watched, "
            "or its red runs go undiagnosed",
        )

    def test_the_reusable_threads_pipeline_ref(self):
        doc = _load("groomer.yml")
        on = _on(doc)
        self.assertIn("workflow_call", on)
        spec = (on["workflow_call"].get("inputs") or {}).get("pipeline_ref")
        self.assertIsNotNone(spec, "DRE-2689: the reusable must take pipeline_ref")
        self.assertTrue(spec.get("required"))
        self.assertNotIn("default", spec)

    def test_the_reusable_defaults_to_proposing_never_draining(self):
        """A dispatch with nothing filled in must be the read-only one. The
        drain is the step that moves cards, and it is opt-in by name."""
        inputs = (_on(_load("groomer.yml"))["workflow_call"].get("inputs") or {})
        self.assertEqual(inputs["mode"]["default"], "propose")
        stub_inputs = (_on(_load("self-groomer.yml"))["workflow_dispatch"]
                       .get("inputs") or {})
        self.assertEqual(stub_inputs["mode"]["default"], "propose")


class DecisionRecordTest(unittest.TestCase):
    """A header that contradicts the code is fixed in the PR that changes the
    code (`standards/engineering.md`). All three decisions, each with its date,
    in every place the cadence is recorded — and none of the retired sentences
    left behind to contradict them."""

    DECISIONS = ("DRE-2683", "2026-08-23", "DRE-3337", "2026-09-08",
                 *THIRD_DECISION)

    def _records(self) -> dict:
        return {
            "self-groomer.yml header": _header("self-groomer.yml"),
            "groomer.yml header": _header("groomer.yml"),
            "the wiring test's docstring": __doc__ or "",
            "scripts/groomer.py's docstring": groomer.__doc__ or "",
        }

    def test_the_stub_header_no_longer_claims_one_trigger(self):
        self.assertNotIn(
            "AND NOTHING ELSE", _header("self-groomer.yml"),
            "the header records a trigger set the file no longer carries",
        )

    def test_the_stub_header_records_all_three_decisions_with_their_dates(self):
        header = _header("self-groomer.yml")
        for token in (*self.DECISIONS, "repository_dispatch"):
            self.assertIn(token, header, f"the header never names {token}")

    def test_the_reusables_header_no_longer_describes_the_old_stub(self):
        header = _header("groomer.yml")
        self.assertNotIn(
            "workflow_dispatch and nothing else", header,
            "groomer.yml still describes its caller as single-trigger",
        )
        for token in self.DECISIONS:
            self.assertIn(token, header, f"groomer.yml's header never names {token}")

    def test_this_modules_docstring_says_the_same(self):
        for token in (*self.DECISIONS, "groom-drain"):
            self.assertIn(token, __doc__ or "",
                          f"the wiring test's docstring never names {token}")

    def test_the_groomers_own_docstring_records_the_cadence(self):
        for token in self.DECISIONS:
            self.assertIn(token, groomer.__doc__ or "",
                          f"scripts/groomer.py's docstring never names {token}")

    def test_no_record_still_says_the_groomer_never_runs_on_a_clock(self):
        for where, text in self._records().items():
            folded = " ".join(text.split()).lower()
            for sentence in RETIRED_CADENCE:
                self.assertNotIn(
                    sentence.lower(), folded,
                    f"{where} still says {sentence!r} — the morning proposal "
                    "runs on a schedule since DRE-4677",
                )


class DryRunSwitchTest(unittest.TestCase):
    """DRE-3712: a dry run posts no marker at all, and it is reachable.

    The 2026-09-04 demonstration was a real `workflow_dispatch`, so a dry run
    that exists only as a CLI flag is a dry run nobody would have taken. The
    switch is an input on both files, it reaches `--dry-run` on the step that
    posts — since DRE-4972 the `post` job's, because the Groom step posts
    nothing — and it silences the OTHER comment the run writes, the judgement
    receipt, because "no marker at all" is the whole of the promise.
    """

    def setUp(self):
        self.doc = _load("groomer.yml")
        self.post = _step(self.doc, POST_STEP)
        self.env = self.post.get("env") or {}

    def test_the_switch_exists_on_both_files_and_is_off_by_default(self):
        spec = (_on(self.doc)["workflow_call"].get("inputs") or {}).get("dry_run")
        self.assertIsNotNone(spec, "the reusable takes no dry_run input")
        self.assertEqual(spec.get("type"), "string")
        self.assertEqual(spec.get("default"), "")

        stub = _on(_load("self-groomer.yml"))["workflow_dispatch"]
        spec = (stub.get("inputs") or {}).get("dry_run")
        self.assertIsNotNone(spec, "the stub offers no dry-run choice")
        self.assertEqual(spec.get("type"), "boolean")
        self.assertIs(spec.get("default"), False)

    def test_the_stub_threads_the_switch_to_the_reusable(self):
        job = _load("self-groomer.yml")["jobs"]["call"]
        self.assertEqual(
            _expression((job.get("with") or {}).get("dry_run")),
            "inputs.dry_run")

    def test_the_switch_reaches_the_step_and_turns_the_flag_on(self):
        self.assertEqual(_expression(self.env.get("DRY_RUN")), "inputs.dry_run")
        self.assertIn("--dry-run", self.post.get("run") or "",
                      "the reusable never passes the flag the CLI reads")

    def test_a_dry_run_posts_no_judgement_receipt_either(self):
        condition = _expression(_step(self.doc, RECEIPT_STEP).get("if"))
        self.assertIn("inputs.dry_run != 'true'", condition,
                      "a dry run would still write the 🧠 receipt comment")


class JudgedReadReachableTest(unittest.TestCase):
    """DRE-3153: the judged read is unreachable from the workflow that runs the
    groomer until the reusable DECLARES the credentials it needs.

    `secrets: inherit` on the stub is not enough — a reusable workflow sees
    only the secrets named in its own `workflow_call.secrets` block, which is
    why `groomer.yml` handed its one step `LINEAR_API_KEY` and nothing else and
    DRE-3150's one call could never have been made from Actions.
    """

    def setUp(self):
        self.doc = _load("groomer.yml")
        self.groom = _step(self.doc, GROOM_STEP)
        self.env = self.groom.get("env") or {}

    def test_the_reusable_declares_both_model_secrets(self):
        declared = (_on(self.doc)["workflow_call"].get("secrets") or {})
        for name in MODEL_SECRETS:
            self.assertIn(
                name, declared,
                f"a reusable workflow only sees the secrets it declares — "
                f"without {name} the judged read cannot be made from Actions",
            )
            self.assertFalse(
                (declared[name] or {}).get("required"),
                f"{name} is optional: which of the two is set is decided by "
                "CLAUDE_AUTH_MODE, so requiring both would refuse every repo",
            )

    def test_the_credentials_are_gated_the_way_plan_yml_gates_them(self):
        """Read off `plan.yml`, never restated here. One definition of which
        credential a run holds; a second copy is free to drift, and the drift
        is a 429 on every call (DRE-3074)."""
        plan = _step(_load("plan.yml"), PLAN_CLASSIFY_STEP).get("env") or {}
        for name in MODEL_SECRETS:
            self.assertIn(name, plan, f"plan.yml no longer sets {name}")
            self.assertIn(
                _expression(plan[name]), _expression(self.env.get(name)),
                f"the Groom step's {name} expression must carry plan.yml's "
                "own CLAUDE_AUTH_MODE gate verbatim",
            )

    def test_the_drain_branch_receives_no_model_credential(self):
        """The drain reads and moves; it does not judge. The two branches share
        one step, so the guard has to live in the expression."""
        for name in MODEL_SECRETS:
            self.assertIn(
                "inputs.mode != 'drain'", _expression(self.env.get(name)),
                f"{name} reaches the drain branch — a credential handed to a "
                "step that never calls a model",
            )

    def test_the_judgement_switch_exists_on_both_files(self):
        spec = (_on(self.doc)["workflow_call"].get("inputs") or {}).get(
            "judgement")
        self.assertIsNotNone(spec, "the reusable takes no judgement input")
        self.assertEqual(spec.get("type"), "string")
        self.assertEqual(spec.get("default"), "on")

        stub = _on(_load("self-groomer.yml"))["workflow_dispatch"]
        spec = (stub.get("inputs") or {}).get("judgement")
        self.assertIsNotNone(spec, "the stub offers no judgement choice")
        self.assertEqual(spec.get("type"), "choice")
        self.assertEqual(sorted(spec.get("options") or []), ["off", "on"])
        self.assertEqual(spec.get("default"), "on")

    def test_the_stub_threads_the_switch_to_the_reusable(self):
        job = _load("self-groomer.yml")["jobs"]["call"]
        self.assertEqual(
            _expression((job.get("with") or {}).get("judgement")),
            "inputs.judgement")

    def test_the_switch_reaches_the_step_and_turns_the_flag_on(self):
        self.assertEqual(_expression(self.env.get("JUDGEMENT")),
                         "inputs.judgement")
        self.assertIn(
            "--no-judgement", self.groom.get("run") or "",
            "anything but `on` runs the rules alone — DRE-3150's flag, "
            "unchanged",
        )

    def test_the_linear_credential_line_is_untouched(self):
        """This card's own refusal to drift the groomer's Linear identity.
        Moving the groomer to another identity is DRE-3168's work, under its
        own card, and it edits this file AFTER this one."""
        self.assertEqual(_expression(self.env.get("LINEAR_API_KEY")),
                         "secrets.LINEAR_API_KEY")
        self.assertNotIn("LINEAR_IDENTITY", self.env)
        self.assertNotIn("LINEAR_IDENTITY", self.groom.get("run") or "")

    def test_the_job_clock_covers_the_judged_reads_own_ceiling(self):
        """Both numbers are READ — the YAML's and the constant's. A later card
        that raises `OUTPUT_CEILING` turns this red rather than leaving the job
        killed mid-call."""
        job = self.doc["jobs"]["groom"]
        self.assertGreaterEqual(
            int(job["timeout-minutes"]) * 60,
            groom_judgement.MAX_WALL_CLOCK_SECONDS + 300,
            "the Groom job's clock must cover the widest one call plus five "
            "minutes for the non-model work the step does",
        )


class MergedPrTokenTest(unittest.TestCase):
    """DRE-4964: the context pack's merged-PR read has a token to read with.

    The Groom step set no `GH_TOKEN`, so every `gh` call in it exited rc=4 and
    the proposal said the merged-PR section "could not be read" on every run
    since at least 2026-09-15. `github.token` would not do either — it cannot
    see the fleet's private repositories — so the step is handed the Bureau
    App's token, minted the way `reconcile.yml` mints it, under the env name
    the per-card read reads as well.
    """

    APP_SECRETS = ("BUREAU_APP_ID", "BUREAU_APP_PRIVATE_KEY")
    ACTION = "actions/create-github-app-token@"

    def setUp(self):
        self.doc = _load("groomer.yml")
        self.steps = self.doc["jobs"]["groom"]["steps"]
        self.groom = _step(self.doc, GROOM_STEP)

    def _mint(self) -> dict:
        found = [s for s in self.steps
                 if str(s.get("uses") or "").startswith(self.ACTION)]
        self.assertEqual(len(found), 1, "the groom job mints exactly one App token")
        return found[0]

    @staticmethod
    def _pin_line(path: Path) -> set:
        """Every `uses: actions/create-github-app-token@<sha> # <version>` line
        of a workflow, whitespace-normalised — the sha AND its version comment,
        because the comment is what Dependabot reads."""
        return {" ".join(line.split()) for line in path.read_text().splitlines()
                if "uses: actions/create-github-app-token@" in line}

    def test_the_reusable_declares_both_app_secrets_as_optional(self):
        declared = (_on(self.doc)["workflow_call"].get("secrets") or {})
        for name in self.APP_SECRETS:
            self.assertIn(name, declared,
                          f"a reusable workflow only sees the secrets it "
                          f"declares — without {name} the mint gets nothing")
            self.assertFalse((declared[name] or {}).get("required"),
                             f"{name} must be optional")

    def test_the_mint_uses_the_app_secrets(self):
        with_ = self._mint().get("with") or {}
        self.assertEqual(_expression(with_.get("app-id")), "secrets.BUREAU_APP_ID")
        self.assertEqual(_expression(with_.get("private-key")),
                         "secrets.BUREAU_APP_PRIVATE_KEY")

    def test_the_mint_is_pinned_exactly_as_reconcile_pins_it(self):
        ours = self._pin_line(WORKFLOWS / "groomer.yml")
        theirs = self._pin_line(WORKFLOWS / "reconcile.yml")
        self.assertEqual(len(ours), 1)
        self.assertTrue(theirs, "reconcile.yml no longer mints an App token")
        self.assertLessEqual(ours, theirs,
                             "the groomer's pin — sha and version comment — "
                             "differs from reconcile.yml's")

    def test_the_groom_step_carries_the_minted_token_as_gh_token(self):
        mint = self._mint()
        self.assertTrue(mint.get("id"), "the mint step needs an id to read from")
        env = self.groom.get("env") or {}
        self.assertEqual(_expression(env.get("GH_TOKEN")),
                         f"steps.{mint['id']}.outputs.token",
                         "GH_TOKEN is the contract the merged-PR read reads")
        self.assertNotIn("github.token", str(env.get("GH_TOKEN")))
        self.assertLess(self.steps.index(mint), self.steps.index(self.groom),
                        "the token is minted after the step that spends it")

    def test_a_failed_mint_degrades_to_an_unread_section_not_a_red_run(self):
        """A mint that fails renders an empty token; the read then names the
        section unread with the reason. The proposal is still the morning's
        work, and a missing context signal must not take it down."""
        self.assertTrue(self._mint().get("continue-on-error"))

    def test_the_drain_mints_no_token(self):
        """The drain reads the approval and moves cards; it builds no pack."""
        self.assertIn("inputs.mode != 'drain'", _expression(self._mint().get("if")))


class JudgementReceiptWiringTest(unittest.TestCase):
    """The one `🧠 model-attempt:` comment a judged run posts."""

    def setUp(self):
        self.doc = _load("groomer.yml")
        self.step = _step(self.doc, RECEIPT_STEP)
        self.run = self.step.get("run") or ""

    def test_the_line_is_composed_in_python_never_in_yaml(self):
        self.assertIn("groomer_receipt.py", self.run)
        self.assertNotIn(
            "model-attempt", self.run,
            "a receipt assembled in a shell string is a second answer to "
            "'which model answered' that nothing tests",
        )

    def test_it_posts_to_the_card_through_linear_ops(self):
        self.assertIn("linear_ops.py comment", self.run)
        self.assertEqual(_expression((self.step.get("env") or {}).get("CARD")),
                         "inputs.card")
        self.assertEqual(
            _expression((self.step.get("env") or {}).get("LINEAR_API_KEY")),
            "secrets.LINEAR_API_KEY")

    def test_it_runs_only_after_a_propose_that_had_a_card(self):
        condition = _expression(self.step.get("if"))
        self.assertIn("inputs.mode != 'drain'", condition)
        self.assertIn("inputs.card != ''", condition)

    def test_the_same_line_goes_into_the_step_summary(self):
        self.assertIn("GITHUB_STEP_SUMMARY", self.run)

    def test_the_receipt_reader_is_declared_to_the_act_registry(self):
        """`check_act_receipts.py` refuses a comment write it cannot account
        for. A proposal receipt is not an act — nothing was refused, recovered
        or held — so it is declared as one that posts no trailer."""
        block = json.loads(
            (ROOT / "config" / "pipeline-acts.json").read_text())["unconverted"]
        self.assertTrue(
            any(e.get("file") == ".github/workflows/groomer.yml"
                and e.get("step") == RECEIPT_STEP for e in block),
            "the receipt step posts a comment and the registry does not name it",
        )


def _verify_step(job: dict, step_id: str) -> dict:
    for step in job.get("steps") or []:
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"the verify job has no step with id {step_id!r}")


def _needs(job: dict) -> list:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


class ComputeVerifyPostTest(unittest.TestCase):
    """DRE-4972: three jobs, and the proposal posted only after the verify
    matrix finishes. `groom` computes without posting, `verify` runs one
    read-only agent per Planning and spare card, and `post` applies the
    verdicts and posts."""

    def setUp(self):
        self.doc = _load("groomer.yml")
        self.jobs = self.doc["jobs"]
        self.groom = _step(self.doc, GROOM_STEP)
        self.run = self.groom.get("run") or ""

    def test_the_jobs_are_groom_lookup_verify_and_post(self):
        """DRE-5429 put the per-owner lookups between the groom and the
        verify matrix."""
        self.assertEqual(list(self.jobs), ["groom", "lookup", "verify", "post"])

    def test_post_needs_both_and_runs_whatever_verify_did(self):
        post = self.jobs["post"]
        self.assertEqual(set(_needs(post)), {"groom", "verify"})
        condition = _expression(post.get("if"))
        for clause in ("always()", "inputs.mode != 'drain'",
                       "needs.groom.result == 'success'"):
            self.assertIn(clause, condition,
                          f"the post job's `if` lacks {clause!r}")

    def test_the_groom_step_posts_nothing(self):
        self.assertNotIn("--post", self.run,
                         "the Groom step still posts — the proposal would "
                         "reach the card before the verify matrix ran")
        self.assertIn('--card "$CARD"', self.run)
        self.assertIn("--out proposal.json", self.run)

    def test_the_post_job_is_what_posts(self):
        runs = " ".join(str(s.get("run") or "")
                        for s in self.jobs["post"].get("steps") or [])
        self.assertIn("groomer.py post", runs)
        self.assertIn('--card "$CARD" --proposal proposal-verified.json', runs)
        for name, job in self.jobs.items():
            if name == "post":
                continue
            for step in job.get("steps") or []:
                self.assertNotIn("groomer.py post", str(step.get("run") or ""),
                                 f"job {name!r} posts the proposal")

    def test_the_groom_step_writes_the_targets_and_the_matrix(self):
        self.assertIn(
            "groom_verify_agent.py targets --proposal proposal.json "
            "--out verify-targets.json --matrix-out matrix.json",
            " ".join(self.run.replace("\\\n", " ").split()))
        self.assertEqual(_expression((self.groom.get("env") or {})
                                     .get("LINEAR_API_KEY")),
                         "secrets.LINEAR_API_KEY")
        outputs = self.jobs["groom"].get("outputs") or {}
        self.assertEqual(_expression(outputs.get("verify_matrix")),
                         f"steps.{self.groom['id']}.outputs.verify_matrix")
        self.assertIn("verify_matrix=", self.run)

    def test_the_targets_file_joins_the_proposal_artifact(self):
        keep = _step(self.doc, "Keep the proposal")
        self.assertEqual((keep.get("with") or {}).get("name"), "groom-proposal")
        self.assertIn("verify-targets.json", (keep.get("with") or {}).get("path"))

    def test_the_verify_job_fans_out_over_the_groom_output(self):
        verify = self.jobs["verify"]
        self.assertEqual(set(_needs(verify)), {"groom", "lookup"})
        condition = _expression(verify.get("if"))
        for clause in ("inputs.mode != 'drain'",
                       "needs.groom.outputs.verify_matrix != ''",
                       "needs.groom.outputs.verify_matrix != '[]'"):
            self.assertIn(clause, condition)
        strategy = verify.get("strategy") or {}
        self.assertIs(strategy.get("fail-fast"), False,
                      "a dead card must never cancel its siblings")
        self.assertEqual(
            _expression((strategy.get("matrix") or {}).get("include")),
            "fromJSON(needs.groom.outputs.verify_matrix)")
        self.assertEqual(_expression(verify.get("runs-on")), RUNS_ON)
        self.assertEqual(verify.get("timeout-minutes"), 15)

    def test_the_new_jobs_are_skipped_on_a_drain(self):
        for name in ("lookup", "verify", "post"):
            self.assertIn("inputs.mode != 'drain'",
                          _expression(self.jobs[name].get("if")),
                          f"the {name} job runs on a drain")

    def test_the_drain_branch_of_the_groom_step_is_unchanged(self):
        self.assertIn(DRAIN_BRANCH, self.run)

    def test_the_judgement_receipt_lives_in_post_with_its_condition(self):
        names = {name: [s.get("name") for s in job.get("steps") or []]
                 for name, job in self.jobs.items()}
        self.assertIn(RECEIPT_STEP, names["post"])
        self.assertNotIn(RECEIPT_STEP, names["groom"])
        receipt = _step(self.doc, RECEIPT_STEP)
        self.assertEqual(_expression(receipt.get("if")), RECEIPT_IF)
        self.assertIn("--proposal proposal-verified.json", receipt.get("run"))
        self.assertLess(names["post"].index(POST_STEP),
                        names["post"].index(RECEIPT_STEP),
                        "the receipt lands before the proposal it describes")


class VerifyJobHoldsNoWriteTokenTest(unittest.TestCase):
    """The verify job reads the card's repo and writes one file. It holds no
    token that can write code and no Linear key at all — the epic's rule that
    the job holding a model over untrusted card text holds no write token."""

    def setUp(self):
        self.doc = _load("groomer.yml")
        self.job = self.doc["jobs"]["verify"]
        self.steps = self.job.get("steps") or []

    def test_no_step_carries_the_linear_key(self):
        self.assertNotIn("LINEAR_API_KEY", str(self.job.get("env") or {}))
        for step in self.steps:
            for block in ("env", "with"):
                self.assertNotIn(
                    "LINEAR_API_KEY", str(step.get(block) or {}),
                    f"step {step.get('name') or step.get('id')!r} of the "
                    f"verify job carries the Linear key in `{block}`")

    def test_the_app_token_is_read_only_and_scoped_to_the_cards_repo(self):
        mint = _verify_step(self.job, "target_token")
        self.assertTrue(str(mint.get("uses")).startswith(
            "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"))
        with_ = mint.get("with") or {}
        self.assertEqual(with_.get("permission-contents"), "read")
        self.assertEqual(_expression(with_.get("app-id")), "secrets.BUREAU_APP_ID")
        self.assertEqual(_expression(with_.get("private-key")),
                         "secrets.BUREAU_APP_PRIVATE_KEY")
        self.assertTrue(with_.get("owner"), "the token is not scoped to an owner")
        self.assertTrue(with_.get("repositories"),
                        "the token is not scoped to the card's repository")
        self.assertIs(mint.get("continue-on-error"), True)

    def test_the_target_checkout_keeps_no_credential(self):
        checkout = _verify_step(self.job, "target")
        with_ = checkout.get("with") or {}
        self.assertIs(with_.get("persist-credentials"), False)
        self.assertEqual(with_.get("path"), "target")
        self.assertEqual(_expression(with_.get("repository")), "matrix.repository")
        self.assertEqual(_expression(with_.get("token")),
                         "steps.target_token.outputs.token")
        self.assertNotIn("ref", with_, "the card is checked against the "
                                       "repo's default branch")
        self.assertIs(checkout.get("continue-on-error"), True)

    def test_the_agent_reads_and_writes_one_file_and_nothing_else(self):
        agent = _verify_step(self.job, "claude")
        args = str((agent.get("with") or {}).get("claude_args"))
        self.assertIn('--allowedTools "Read,Glob,Grep,Write"', args)
        self.assertEqual(args.count("--allowedTools"), 1)
        self.assertIn("--max-turns 40", args)
        self.assertIs(agent.get("continue-on-error"), True)
        self.assertTrue(str(agent.get("uses")).startswith(
            "anthropics/claude-code-action@8ce9314fa9a404564fa7e954cd84f25bcba2b829"))

    def test_the_prompt_is_fixed_text(self):
        prompt = str((_verify_step(self.job, "claude").get("with") or {})
                     .get("prompt"))
        self.assertNotIn("${{", prompt, "nothing is interpolated into the prompt")
        self.assertIn("verify-input.md", prompt)
        self.assertIn("target/", prompt)

    def test_the_bots_are_the_fleet_list_plus_the_qa_bot(self):
        """The fleet list verbatim, plus the qa-bot a scheduled morning
        initiates as (DRE-5123, tests/test_scheduled_claude_allowed_bots.py).
        Read off agent-task.yml's `Implement card` step — the model trial now
        carries the qa-bot too, so it is no longer the fleet list verbatim."""
        ours = (_verify_step(self.job, "claude").get("with") or {}).get(
            "allowed_bots")
        implement = _step(_load("agent-task.yml"), "Implement card")
        fleet = str((implement.get("with") or {}).get("allowed_bots")).split(",")
        self.assertEqual(sorted(str(ours).split(",")),
                         sorted(fleet + ["agent-bureau-qa-bot"]))

    def test_the_agent_takes_the_credential_the_way_plan_yml_gates_it(self):
        """Read off plan.yml's classify step, never restated (DRE-3074)."""
        plan = _step(_load("plan.yml"), PLAN_CLASSIFY_STEP).get("env") or {}
        with_ = _verify_step(self.job, "claude").get("with") or {}
        for name, key in (("ANTHROPIC_API_KEY", "anthropic_api_key"),
                          ("CLAUDE_CODE_OAUTH_TOKEN", "claude_code_oauth_token")):
            self.assertIn(_expression(plan[name]), _expression(with_.get(key)),
                          f"the verify agent's {key} is not gated on "
                          "CLAUDE_AUTH_MODE the way plan.yml gates it")


class UnmappedRepoReadsNoCodeTest(unittest.TestCase):
    """A card whose repo is not in `config/repo-map.json` reaches the verify
    job with `matrix.repository == ''`. On that leg no token is minted, no
    code is checked out and no model is called — and the leg still uploads a
    `verdict.json` saying `unverified`."""

    SKIPPED = ("target_token", "target", "install_claude", "claude")

    def setUp(self):
        self.job = _load("groomer.yml")["jobs"]["verify"]
        self.ids = [s.get("id") for s in self.job.get("steps") or []]

    def test_the_four_code_and_model_steps_skip_an_unmapped_repo(self):
        for step_id in self.SKIPPED:
            self.assertIn("matrix.repository != ''",
                          _expression(_verify_step(self.job, step_id).get("if")),
                          f"step {step_id!r} runs on an unmapped repo")

    def test_prepare_always_runs(self):
        prepare = _verify_step(self.job, "prepare")
        self.assertNotIn("if", prepare)
        run = " ".join(str(prepare.get("run")).replace("\\\n", " ").split())
        self.assertIn('groom_verify_agent.py prepare --targets verify-targets.json '
                      '--card "$CARD" --out verify-input.md '
                      '--started-at-out started.txt', run)

    def test_the_verdict_is_written_always_from_the_targets(self):
        verdict = _verify_step(self.job, "verdict")
        self.assertEqual(_expression(verdict.get("if")), "always()")
        run = str(verdict.get("run"))
        self.assertIn("--targets verify-targets.json", run)
        self.assertNotIn("${{", run, "every expression goes through env:")
        env = verdict.get("env") or {}
        self.assertEqual(_expression(env.get("OUTCOME")), "steps.claude.outcome")
        self.assertIn('--step-outcome "$OUTCOME"', run)

    def test_the_steps_run_in_the_contract_order(self):
        order = ["prepare", *self.SKIPPED, "verdict", "death"]
        self.assertEqual([i for i in self.ids if i in order], order)

    def test_every_leg_uploads_its_verdict_under_its_own_name(self):
        steps = self.job.get("steps") or []
        verdict_at = self.ids.index("verdict")
        upload = steps[verdict_at + 1]
        with_ = upload.get("with") or {}
        self.assertEqual(with_.get("name"), "groom-verdict-${{ matrix.card }}")
        self.assertEqual(with_.get("path"), "verdict.json")
        self.assertIs(with_.get("overwrite"), True)
        self.assertIn("always()", str(upload.get("if")))

    def test_the_death_receipt_is_named_per_leg(self):
        steps = self.job.get("steps") or []
        upload = steps[self.ids.index("death") + 1]
        self.assertEqual(
            (upload.get("with") or {}).get("name"),
            "death-receipt-verify-${{ matrix.card }}-attempt${{ github.run_attempt }}")


class PostJobTest(unittest.TestCase):
    """`post`: the verdicts applied to the record, then the record posted."""

    def setUp(self):
        self.doc = _load("groomer.yml")
        self.job = self.doc["jobs"]["post"]
        self.steps = self.job.get("steps") or []

    def _downloads(self) -> list:
        return [s.get("with") or {} for s in self.steps
                if str(s.get("uses") or "").startswith("actions/download-artifact@")]

    def test_it_downloads_the_proposal_and_every_verdict(self):
        downloads = self._downloads()
        self.assertIn("groom-proposal", [d.get("name") for d in downloads])
        verdicts = [d for d in downloads if d.get("pattern") == "groom-verdict-*"]
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0].get("path"), "verdicts")
        # Every leg's file is `verdict.json`; merged into one directory they
        # would overwrite one another, and `apply` would see one card.
        self.assertIs(verdicts[0].get("merge-multiple"), False)

    def test_it_applies_the_verdicts_before_posting(self):
        runs = [" ".join(str(s.get("run") or "").replace("\\\n", " ").split())
                for s in self.steps]
        apply_at = next(i for i, r in enumerate(runs)
                        if "groom_verify_agent.py apply" in r)
        self.assertIn("--proposal proposal.json --verdicts verdicts "
                      "--out proposal-verified.json", runs[apply_at])
        post_at = next(i for i, r in enumerate(runs) if "groomer.py post" in r)
        self.assertLess(apply_at, post_at)

    def test_it_keeps_the_verified_record(self):
        uploads = [s.get("with") or {} for s in self.steps
                   if str(s.get("uses") or "").startswith("actions/upload-artifact@")]
        kept = [u for u in uploads if u.get("name") == "groom-proposal-verified"]
        self.assertEqual(len(kept), 1)
        self.assertIn("proposal-verified.json", kept[0].get("path"))

    def test_the_post_step_holds_the_linear_key_and_no_model_credential(self):
        env = _step(self.doc, POST_STEP).get("env") or {}
        self.assertEqual(_expression(env.get("LINEAR_API_KEY")),
                         "secrets.LINEAR_API_KEY")
        for step in self.steps:
            for name in MODEL_SECRETS:
                self.assertNotIn(name, step.get("env") or {},
                                 "the post job calls no model")


class TheLegsAndThePostRunAsWrittenTest(unittest.TestCase):
    """The workflow's OWN `run:` blocks, executed: `prepare` and `verdict` on
    three matrix legs laid out as separate runners, each leg's `verdict.json`
    placed where an unmerged `download-artifact` puts it, then the post job's
    `apply`. So the commands, the file names between jobs and the artifact
    layout are proved together, not only read."""

    def _run(self, step: dict, cwd: Path, env: dict) -> None:
        import subprocess
        proc = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=cwd,
                              env={**os.environ, **env}, capture_output=True,
                              text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_three_legs_and_the_post(self):
        import shutil
        import tempfile

        from test_groom_verify_agent import PROOF_LINE, build_targets, proposal

        doc = _load("groomer.yml")
        verify, post = doc["jobs"]["verify"], doc["jobs"]["post"]
        prepare, verdict = (_verify_step(verify, "prepare"),
                            _verify_step(verify, "verdict"))
        apply = next(s for s in post["steps"]
                     if "groom_verify_agent.py apply" in str(s.get("run")))
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            pfile, targets, matrix = build_targets(
                tmp, proposal(unmapped=("DRE-102",)))
            rows = json.loads(matrix.read_text())
            outcomes = {"DRE-101": "success", "DRE-102": "skipped",
                        "DRE-103": "failure"}
            post_ws = tmp / "post"
            for row in rows:
                if row["card"] not in outcomes:
                    continue            # a leg whose artifact never arrived
                ws = tmp / "leg" / row["card"]
                ws.mkdir(parents=True)
                (ws / ".bureau-pipeline").symlink_to(ROOT)
                shutil.copy(targets, ws / "verify-targets.json")
                env = {"CARD": row["card"], "REPOSITORY": row["repository"],
                       "GITHUB_OUTPUT": str(ws / "output")}
                self._run(prepare, ws, env)
                if row["card"] == "DRE-101":
                    (ws / "verify-verdict.json").write_text(json.dumps({
                        "card": "DRE-101", "verdict": "obsolete",
                        "summary": "The roster migration already exists.",
                        "proof": [PROOF_LINE]}))
                    owner, name = row["repository"].split("/")
                    self.assertIn(f"owner={owner}\nname={name}\n",
                                  (ws / "output").read_text())
                self._run(verdict, ws, {
                    **env, "OUTCOME": outcomes[row["card"]],
                    "EXECUTION_FILE": str(ws / "no-execution-file.json")})
                dest = post_ws / "verdicts" / f"groom-verdict-{row['card']}"
                dest.mkdir(parents=True)
                shutil.copy(ws / "verdict.json", dest / "verdict.json")
            (post_ws / ".bureau-pipeline").symlink_to(ROOT)
            shutil.copy(pfile, post_ws / "proposal.json")
            self._run(apply, post_ws, {})
            verified = json.loads(
                (post_ws / "proposal-verified.json").read_text())

        self.assertEqual(rows[1], {"card": "DRE-102", "repository": ""})
        block = verified["verify"]
        self.assertEqual(block["cards"], len(rows))
        self.assertEqual(block["counts"]["obsolete"], 1)
        self.assertEqual(len(block["unverified"]), len(rows) - 1)
        self.assertIn("DRE-101", [r["identifier"]
                                  for r in verified["outcomes"]["dead"]])
        marks = {r["identifier"]: r.get("verify")
                 for r in verified["sequence"]}
        self.assertEqual(marks["DRE-102"]["reason"],
                         "repo not in config/repo-map.json: widgets")
        self.assertEqual(marks["DRE-103"]["reason"], "agent step failed")
        self.assertEqual(marks["DRE-104"]["reason"], "no verdict artifact")


#: DRE-5429: the per-owner lookup job, and the steps that bind it to the
#: groom job before it and the verify legs after it.
LOOKUP_JOB = "lookup"
OWNERS_STEP = "Owners in the roster"
LOOKUP_STEP = "Look up this owner's repos"
LOOKUP_DOWNLOAD_STEP = "Download every owner's lookups"
FOLD_STEP = "Fold the lookups into the targets"
VERIFY_PROPOSAL_STEP = "Download the proposal and its targets"

#: The `lookup_budget` input's description, verbatim, on both files.
LOOKUP_BUDGET_DESCRIPTION = (
    "empty = sized from the token's own bucket; 0 = spend nothing; a whole "
    "number = the cap on requests per owner")

#: The only permissions a lookup leg's token is minted with: it reads commits
#: and the pull requests they belong to, and nothing else.
LOOKUP_PERMISSIONS = {"permission-contents": "read",
                      "permission-pull-requests": "read"}

#: The credentials no lookup leg may hold: it writes no card and calls no model.
LOOKUP_FORBIDDEN = ("LINEAR_API_KEY", *MODEL_SECRETS)


def _one_line(run) -> str:
    return " ".join(str(run or "").replace("\\\n", " ").split())


def _uses(job: dict, action: str) -> list:
    return [s for s in job.get("steps") or []
            if str(s.get("uses") or "").startswith(action)]


def _named(job: dict, name: str) -> dict:
    for step in job.get("steps") or []:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in the job")


def _flag(run: str, flag: str) -> str:
    """The value a shell line passes `flag`, read the way bash reads it."""
    import shlex
    words = shlex.split(_one_line(run))
    return words[words.index(flag) + 1]


class LookupJobTest(unittest.TestCase):
    """DRE-5429: the GitHub half of the lookups runs once per owner, each leg
    on that owner's own App token. An installation token is scoped to one
    installation and the roster spans three owners, so the groom job's one
    token — minted with no `owner:` — was refused for every repo outside
    dreadnought-foundry every morning. `fleet-wake.yml` mints per owner for
    the same reason, and the matrix reads the same roster reader."""

    def setUp(self):
        self.doc = _load("groomer.yml")
        self.jobs = self.doc["jobs"]
        self.job = self.jobs[LOOKUP_JOB]
        self.steps = self.job.get("steps") or []
        self.script = _named(self.job, LOOKUP_STEP)

    def _mint(self) -> dict:
        found = _uses(self.job, "actions/create-github-app-token@")
        self.assertEqual(len(found), 1, "each leg mints exactly one token")
        return found[0]

    def test_it_needs_groom_and_keys_on_the_verify_matrix(self):
        self.assertEqual(_needs(self.job), ["groom"])
        condition = _expression(self.job.get("if"))
        for clause in ("inputs.mode != 'drain'",
                       "needs.groom.outputs.verify_matrix != ''",
                       "needs.groom.outputs.verify_matrix != '[]'"):
            self.assertIn(clause, condition,
                          f"the lookup job's `if` lacks {clause!r}")
        self.assertEqual(_expression(self.job.get("runs-on")), RUNS_ON)

    def test_one_leg_per_owner_and_one_dead_leg_cancels_none(self):
        strategy = self.job.get("strategy") or {}
        self.assertIs(strategy.get("fail-fast"), False)
        self.assertEqual(
            _expression((strategy.get("matrix") or {}).get("owner")),
            "fromJSON(needs.groom.outputs.lookup_owners)")

    def test_the_token_is_minted_for_the_legs_owner_read_only(self):
        mint = self._mint()
        verify_mint = _verify_step(self.jobs["verify"], "target_token")
        self.assertEqual(mint.get("uses"), verify_mint.get("uses"),
                         "the lookup mint is not at the verify mint's pin")
        with_ = mint.get("with") or {}
        self.assertEqual(_expression(with_.get("owner")), "matrix.owner")
        self.assertEqual(_expression(with_.get("app-id")), "secrets.BUREAU_APP_ID")
        self.assertEqual(_expression(with_.get("private-key")),
                         "secrets.BUREAU_APP_PRIVATE_KEY")
        self.assertEqual(
            {k: v for k, v in with_.items() if k.startswith("permission-")},
            LOOKUP_PERMISSIONS,
            "the lookup token reads contents and pull requests and nothing else")
        self.assertIs(mint.get("continue-on-error"), True,
                      "a failed mint must leave the leg alive to say so")

    def test_the_jobs_clock_never_kills_the_leg_before_its_own_stop(self):
        """Both sides READ: the YAML's minutes and the script's constants. The
        leg stops asking at `MAX_SECONDS`, one call can hold it past that by
        `REQUEST_TIMEOUT`, and three minutes cover the mint, the download and
        the upload. A change to any of the three that lets the job kill the
        leg first — so it uploads nothing — fails here."""
        self.assertEqual(self.job.get("timeout-minutes"), 9)
        self.assertGreaterEqual(
            int(self.job["timeout-minutes"]) * 60,
            groom_lookups.MAX_SECONDS + groom_lookups.REQUEST_TIMEOUT + 180)

    def test_the_legs_clock_fits_the_mornings_headroom(self):
        """The morning's latency budget. The scheduled run is the 06:00 PT
        cron, and DRE-4968 needs the proposal on the standing card by 06:30
        PT. The two most recent scheduled mornings, runs 36719580735
        (2026-09-30) and 36866353792 (2026-10-01), posted at 06:16 and 06:18
        PT — twelve to fourteen minutes before the deadline. The lookup stage
        runs between groom and verify, its legs side by side, so it adds the
        slowest leg: at most `MAX_SECONDS + REQUEST_TIMEOUT`. Six minutes is
        that measured headroom with half kept for the groom job's own
        variance; a leg clock past it has to argue for the deadline first."""
        self.assertLessEqual(
            groom_lookups.MAX_SECONDS + groom_lookups.REQUEST_TIMEOUT, 360)

    def test_the_steps_run_in_the_contract_order(self):
        checkout = self.steps[0]
        self.assertTrue(str(checkout.get("uses")).startswith("actions/checkout@"))
        self.assertEqual((checkout.get("with") or {}).get("path"), ".bureau-pipeline")
        self.assertEqual((checkout.get("with") or {}).get("repository"), PIPELINE)
        downloads = _uses(self.job, "actions/download-artifact@")
        self.assertEqual([(d.get("with") or {}).get("name") for d in downloads],
                         ["groom-proposal"])
        upload = _uses(self.job, "actions/upload-artifact@")
        self.assertEqual(len(upload), 1)
        order = [checkout, downloads[0], self._mint(), self.script, upload[0]]
        self.assertEqual([self.steps.index(s) for s in order],
                         sorted(self.steps.index(s) for s in order))

    def test_the_leg_runs_the_owner_command_with_every_input_through_env(self):
        run = _one_line(self.script.get("run"))
        self.assertIn(
            'python3 .bureau-pipeline/scripts/groom_lookups.py owner '
            '--owner "$OWNER" --targets verify-targets.json '
            '--out "lookups-$OWNER.json" --budget "$LOOKUP_BUDGET"', run)
        self.assertNotIn("${{", run, "every input goes through env:")
        env = self.script.get("env") or {}
        self.assertEqual(_expression(env.get("OWNER")), "matrix.owner")
        self.assertEqual(_expression(env.get("LOOKUP_BUDGET")),
                         "inputs.lookup_budget")
        self.assertEqual(_expression(env.get("GH_TOKEN")),
                         f"steps.{self._mint()['id']}.outputs.token")

    def test_a_leg_whose_mint_failed_still_runs_and_says_so(self):
        """DRE-5308's `no token for <owner>` contract: the script records the
        owner unread with the reason, so the verify legs learn what was not
        consulted. Gated on the mint, the leg would upload nothing instead."""
        self.assertNotIn("if", self.script)

    def test_the_leg_holds_no_linear_key_and_no_model_credential(self):
        for name in LOOKUP_FORBIDDEN:
            self.assertNotIn(name, str(self.job),
                             f"the lookup job carries {name}")

    def test_each_leg_uploads_its_document_under_its_owner(self):
        upload = _uses(self.job, "actions/upload-artifact@")[0]
        with_ = upload.get("with") or {}
        self.assertEqual(with_.get("name"), "groom-lookups-${{ matrix.owner }}")
        self.assertEqual(with_.get("path"), "lookups-${{ matrix.owner }}.json")
        self.assertIs(with_.get("overwrite"), True,
                      "a re-run leg must re-upload its artifact")


class LookupOwnersAndBudgetTest(unittest.TestCase):
    """The matrix's owners, read off the roster, and the per-owner request
    budget: an input on both files whose ordinary value is the empty string."""

    def setUp(self):
        self.doc = _load("groomer.yml")
        self.stub = _load("self-groomer.yml")

    def _reusable_input(self) -> dict:
        spec = (_on(self.doc)["workflow_call"].get("inputs") or {}).get(
            "lookup_budget")
        self.assertIsNotNone(spec, "the reusable takes no lookup_budget input")
        return spec

    def _stub_input(self) -> dict:
        spec = (_on(self.stub)["workflow_dispatch"].get("inputs") or {}).get(
            "lookup_budget")
        self.assertIsNotNone(spec, "the stub offers no lookup_budget input")
        return spec

    def test_the_owners_are_the_roster_readers_output(self):
        groom = self.doc["jobs"]["groom"]
        step = _named(groom, OWNERS_STEP)
        self.assertEqual(_one_line(step.get("run")),
                         "python3 .bureau-pipeline/scripts/release_train.py "
                         "wake-owners")
        self.assertEqual(
            _expression((groom.get("outputs") or {}).get("lookup_owners")),
            f"steps.{step['id']}.outputs.owners")

    def test_the_input_exists_on_both_files_and_is_empty_by_default(self):
        for spec in (self._reusable_input(), self._stub_input()):
            self.assertEqual(spec.get("type"), "string")
            self.assertEqual(spec.get("default"), "")
            self.assertEqual(" ".join(str(spec.get("description")).split()),
                             LOOKUP_BUDGET_DESCRIPTION)
        self.assertFalse(self._reusable_input().get("required"))

    def test_both_defaults_read_as_no_budget_given(self):
        """The flag rides every run, so the ordinary morning hands the script
        whatever the defaults are. Read through DRE-5308's own reader: a
        default it refused would turn every scheduled leg red."""
        for spec in (self._reusable_input(), self._stub_input()):
            self.assertIsNone(groom_lookups.parse_budget(spec.get("default")))

    def test_only_the_hand_dispatch_passes_it(self):
        jobs = self.stub["jobs"]
        self.assertEqual(
            _expression((jobs["call"].get("with") or {}).get("lookup_budget")),
            "inputs.lookup_budget")
        for name in ("drain", "schedule"):
            self.assertNotIn("lookup_budget", jobs[name].get("with") or {},
                             f"the {name} job passes lookup_budget — omitted, "
                             "the reusable's default applies")


class VerifyReadsTheLookupsTest(unittest.TestCase):
    """The verify legs read every owner's document. Downloaded unmerged, each
    artifact lands in its own subdirectory, and `fold` walks the directory at
    every depth — so the download's `path` and the fold's `--lookups-dir` are
    one value, asserted as one."""

    def setUp(self):
        self.doc = _load("groomer.yml")
        self.job = self.doc["jobs"]["verify"]
        self.steps = self.job.get("steps") or []
        self.download = _named(self.job, LOOKUP_DOWNLOAD_STEP)
        self.fold = _named(self.job, FOLD_STEP)

    def test_a_dead_lookup_leg_never_stops_the_verify_matrix(self):
        self.assertEqual(set(_needs(self.job)), {"groom", "lookup"})
        condition = _expression(self.job.get("if"))
        self.assertTrue(condition.startswith("always()"),
                        "verify's `if` must open with always()")
        for clause in ("needs.groom.result == 'success'",
                       "inputs.mode != 'drain'",
                       "needs.groom.outputs.verify_matrix != ''",
                       "needs.groom.outputs.verify_matrix != '[]'"):
            self.assertIn(clause, condition)

    def test_the_download_is_unmerged_and_lands_where_fold_reads(self):
        with_ = self.download.get("with") or {}
        self.assertEqual(with_.get("pattern"), "groom-lookups-*")
        self.assertIs(with_.get("merge-multiple"), False)
        self.assertIs(self.download.get("continue-on-error"), True)
        self.assertEqual(with_.get("path"),
                         _flag(self.fold.get("run"), "--lookups-dir"))

    def test_the_fold_makes_its_directory_then_folds_the_targets(self):
        run = _one_line(self.fold.get("run"))
        directory = (self.download.get("with") or {}).get("path")
        mkdir = f"mkdir -p {directory}"
        self.assertIn(mkdir, run)
        command = ("python3 .bureau-pipeline/scripts/groom_lookups.py fold "
                   f"--targets verify-targets.json --lookups-dir {directory} "
                   "--out verify-targets.json")
        self.assertIn(command, run)
        self.assertLess(run.index(mkdir), run.index(command))

    def test_the_fold_runs_between_the_downloads_and_prepare(self):
        order = [_named(self.job, VERIFY_PROPOSAL_STEP), self.download,
                 self.fold, _verify_step(self.job, "prepare")]
        at = [self.steps.index(s) for s in order]
        self.assertEqual(at, sorted(at),
                         "the fold must run after both downloads and before "
                         "`prepare` fences the evidence")


class TheLookupLegAndTheFoldRunAsWrittenTest(unittest.TestCase):
    """The workflow's OWN `run:` blocks, executed against one layout: a lookup
    leg run as written over a `gh` that answers, its document placed where the
    unmerged download puts it — the path built from the upload's and the
    download's own `with:` values — then the verify leg's fold step. So the
    wiring and both commands are proven against one answered card."""

    CARD = "DRE-901"
    HOME = "dreadnought-foundry/bureau-pipeline"
    OWNER = "dreadnought-foundry"

    #: `gh api -i`, answering: a status line, the rate-limit header the leg
    #: sizes itself from, and an empty commits list.
    GH = ("#!/bin/sh\n"
          "printf 'HTTP/2.0 200 OK\\nx-ratelimit-remaining: 4000\\n\\n[]'\n")

    def _run(self, step: dict, cwd: Path, env: dict) -> str:
        import subprocess
        proc = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=cwd,
                              env={**os.environ, **env}, capture_output=True,
                              text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def _targets(self) -> list:
        return [{"card": self.CARD, "repository": self.HOME, "list": "planning",
                 "context": {"created_at": "2026-09-01T00:00:00Z"},
                 "lookups": {"looked_up": True, "ok": None, "why": None,
                             "paths": ["scripts/groomer.py"],
                             "paths_left_out": 0, "newer_cards": [],
                             "newer_cards_why": None, "merged_prs": [],
                             "cut": [], "owners": {}}}]

    def _workspace(self, tmp: Path, name: str) -> Path:
        ws = tmp / name
        ws.mkdir()
        (ws / ".bureau-pipeline").symlink_to(ROOT)
        (ws / "verify-targets.json").write_text(json.dumps(self._targets()))
        return ws

    def test_an_answered_leg_folds_to_an_ok_card(self):
        import shutil
        import tempfile

        doc = self._doc()
        lookup, verify = doc["jobs"][LOOKUP_JOB], doc["jobs"]["verify"]
        leg = _named(lookup, LOOKUP_STEP)
        upload = (_uses(lookup, "actions/upload-artifact@")[0].get("with") or {})
        download = _named(verify, LOOKUP_DOWNLOAD_STEP).get("with") or {}
        fold = _named(verify, FOLD_STEP)

        def owned(text: str) -> str:
            return text.replace("${{ matrix.owner }}", self.OWNER)

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            (bin_dir / "gh").write_text(self.GH)
            (bin_dir / "gh").chmod(0o755)

            leg_ws = self._workspace(tmp, "leg")
            self._run(leg, leg_ws, {
                "OWNER": self.OWNER, "GH_TOKEN": "a-token",
                "LOOKUP_BUDGET": "",
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
            written = leg_ws / owned(upload["path"])
            self.assertTrue(written.is_file(), "the leg wrote no document")
            self.assertTrue(json.loads(written.read_text())["cards"][self.CARD]
                            ["read"])

            verify_ws = self._workspace(tmp, "verify")
            placed = (Path(download["path"]) / owned(upload["name"])
                      / owned(upload["path"]))
            self.assertEqual(
                placed.as_posix(),
                "lookups/groom-lookups-dreadnought-foundry/"
                "lookups-dreadnought-foundry.json")
            (verify_ws / placed).parent.mkdir(parents=True)
            shutil.copy(written, verify_ws / placed)
            self._run(fold, verify_ws, {})
            rows = json.loads((verify_ws / "verify-targets.json").read_text())

        look = rows[0]["lookups"]
        self.assertIs(look["ok"], True, look)
        self.assertIsNone(look["why"])
        self.assertEqual(look["owners"][self.OWNER], {"read": True, "why": None})

    def test_a_morning_no_leg_uploaded_still_folds(self):
        """The download is `continue-on-error`, so on a morning no leg
        uploaded there is no directory at all — the fold's own `mkdir -p` is
        what keeps the step green, and the card comes out failed, named."""
        import tempfile

        fold = _named(self._doc()["jobs"]["verify"], FOLD_STEP)
        with tempfile.TemporaryDirectory() as raw:
            ws = self._workspace(Path(raw), "verify")
            self._run(fold, ws, {})
            look = json.loads((ws / "verify-targets.json").read_text())[0][
                "lookups"]
        self.assertIs(look["ok"], False)
        self.assertIn(groom_lookups.NO_RECORD.format(owner=self.OWNER),
                      look["why"])

    def test_a_leg_with_no_token_writes_its_document_unread(self):
        """A failed mint renders an empty token; the leg still runs, on an
        empty budget, and says the owner was not read."""
        import tempfile

        leg = _named(self._doc()["jobs"][LOOKUP_JOB], LOOKUP_STEP)
        with tempfile.TemporaryDirectory() as raw:
            ws = self._workspace(Path(raw), "leg")
            self._run(leg, ws, {"OWNER": self.OWNER, "GH_TOKEN": "",
                                "LOOKUP_BUDGET": ""})
            written = json.loads(
                (ws / f"lookups-{self.OWNER}.json").read_text())
        self.assertIs(written["read"], False)
        self.assertEqual(written["why"],
                         groom_lookups.NO_TOKEN.format(owner=self.OWNER))

    @staticmethod
    def _doc() -> dict:
        return _load("groomer.yml")


class LaneContractTest(unittest.TestCase):
    """The drain moves a card into Planning, so the groomer is a writer of
    Planning. A writer the contract does not name is a write the harness
    cannot account for."""

    def setUp(self):
        self.contract = json.loads(
            (ROOT / "config" / "lane-contract.json").read_text())

    def _lane(self, name):
        return next(l for l in self.contract["lanes"] if l.get("name") == name)

    def test_the_groomer_is_a_declared_writer(self):
        writers = self.contract["writers"]
        self.assertIn("groomer.py", writers)
        path = writers["groomer.py"]["path"]
        self.assertTrue((ROOT / path).is_file(), f"{path} does not exist")

    def test_the_groomer_writes_the_lane_it_drains_into(self):
        who = self._lane(groomer.DRAIN_TO)["clauses"]["writers"]["who"]
        self.assertIn("groomer.py", who)

    def test_the_groomer_is_not_a_writer_of_intake(self):
        """Intake's writers are 'anything that creates a card, and nothing
        that moves one onward'. The groomer only ever moves cards OUT."""
        who = self._lane("Intake")["clauses"]["writers"]["who"]
        self.assertNotIn("groomer.py", who)

    def test_the_rendered_contract_document_is_current(self):
        import lane_contract
        doc = (ROOT / "docs" / "lane-contract.md").read_text(encoding="utf-8")
        self.assertEqual(
            doc, lane_contract.render_markdown(),
            "docs/lane-contract.md is stale — regenerate it with "
            "`python3 scripts/lane_contract.py render`",
        )


class DocumentationTest(unittest.TestCase):
    def setUp(self):
        self.doc = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")

    def test_the_doc_states_the_approval_gate(self):
        self.assertIn(groomer.APPROVAL_TAG, self.doc)
        self.assertIn("Intake", self.doc)

    def test_the_doc_records_the_cadence_the_code_actually_carries(self):
        """The cadence section said "a manual `workflow_dispatch`, never a
        schedule". Half of that is now false, and a runbook sentence that is
        half false is the one an operator acts on."""
        self.assertNotIn("a manual `workflow_dispatch`, never a schedule",
                         self.doc)
        for token in ("DRE-3337", "groom-drain"):
            self.assertIn(token, self.doc)

    def test_the_cadence_section_records_all_three_triggers(self):
        """The hand dispatch, the Approve-fired drain, and the 06:00 PT gated
        propose — each decision with its date, and the gate named so the
        operator knows what a quiet morning means."""
        section = self.doc.split("## The cadence", 1)[1].split("\n## ", 1)[0]
        for token in ("DRE-2683", "2026-08-23", "DRE-3337", "2026-09-08",
                      *THIRD_DECISION, "06:00 PT", "DRE-4969", "2026-09-27",
                      "groom_schedule_gate.py",
                      "GROOM_PROPOSAL_CARD", "workflow_dispatch",
                      "repository_dispatch"):
            self.assertIn(token, section, f"the cadence section never names {token}")

    def test_the_doc_no_longer_says_the_groomer_never_runs_on_a_clock(self):
        folded = " ".join(self.doc.split()).lower()
        for sentence in (*RETIRED_CADENCE, "on demand, never a schedule",
                         "it still means it runs when someone remembers"):
            self.assertNotIn(
                sentence.lower(), folded,
                f"docs/groomer.md still says {sentence!r}",
            )

    def test_what_one_run_does_names_the_three_jobs(self):
        """DRE-4972: the run is three jobs, an unmapped repo is `unverified`
        with no code read, and the verify total is on the proposal page."""
        section = self.doc.split("## What one run does", 1)[1].split(
            "\n## ", 1)[0]
        folded = " ".join(section.split())
        for token in ("`groom`", "`verify`", "`post`", "DRE-4972",
                      "config/repo-map.json", "`unverified`", "no code is read",
                      "the verify total is on the proposal page"):
            self.assertIn(token, folded,
                          f"`## What one run does` never says {token}")

    def test_the_doc_says_why_a_cycle_is_not_sprint_planning(self):
        self.assertIn(groomer.CYCLE_IS_NOT_SPRINT_PLANNING, self.doc)

    def test_the_doc_records_the_comparison_against_the_forms_review(self):
        """Proof in production: one real batch groomed, its ordering compared
        against the collisions DRE-2649 found. Agreement and disagreement are
        both results, and both are written down."""
        proof = (ROOT / "docs" / "groomer-first-batch.md").read_text(encoding="utf-8")
        self.assertIn("DRE-2649", proof)
        for heading in ("## Agreement", "## Disagreement"):
            self.assertIn(heading, proof)


class JudgedBatchRecordTest(unittest.TestCase):
    """The judged groom's record (DRE-3260), and the half of it that was owed.

    `docs/groomer-judged-batch.md` landed on 2026-09-10 naming three criteria it
    could not meet, the first of which was the scorer's report: DRE-3155 was not
    on `main`, so `groomer_score.py` could not be run over DRE-3151's audit table
    and the agreement rate had nobody to compute it. Both halves exist now — the
    scorer is in `scripts/` and the CEO's calls are on DRE-3151 — so the record
    owes the report rather than the note saying why it is missing.

    These assertions are on the RECORD, not on the groomer: a proof document
    whose findings nothing reads is a document that can be quietly emptied.
    """

    def setUp(self):
        self.doc = (ROOT / "docs" / "groomer-judged-batch.md").read_text(
            encoding="utf-8")

    def test_the_record_pastes_the_scorers_report_over_the_hand_audit(self):
        """The report verbatim, both halves, named as the scorer's own output.

        `render_report` always prints Agreement AND Disagreement; a record that
        pasted only the agreeing half would be the marketing document
        `groomer_score.py`'s own docstring refuses to be.
        """
        self.assertIn("groomer_score.py score", self.doc)
        self.assertIn("# The groomer's judgement, scored against DRE-3151",
                      self.doc)
        for heading in ("## Agreement", "## Disagreement", "## Unranked",
                        "## Excluded as contaminated"):
            self.assertIn(heading, self.doc)

    def test_the_record_carries_the_three_numbers_the_scorer_computed(self):
        """Agreement rate, false-done and unranked, as the scorer printed them.

        The card pins these three; quoting the rate without the two counts
        beside it is how a false done stops being the expensive miss and becomes
        a rounding error in a percentage.
        """
        self.assertIn("agreement: 15 of 25 scored row(s) (60.0%)", self.doc)
        self.assertIn("false-done: 1 (the audit declared 1)", self.doc)
        self.assertIn("unranked: 1 (the audit declared 1)", self.doc)

    def test_the_record_no_longer_claims_the_likely_dones_were_all_good(self):
        """`false-done: 0` was the worksheet's count, and the audit says 2.

        The first pass read the six `likely-done` calls against the board and
        found none contradicted. The CEO's own audit contradicts two of them
        (DRE-2598, DRE-2657), so the record's own headline number was wrong and
        a record that keeps it is worse than no record.
        """
        self.assertNotIn("**false-done: 0.**", self.doc)
        for card in ("DRE-2598", "DRE-2657"):
            self.assertIn(card, self.doc)

    def test_the_record_names_the_cards_the_two_findings_were_filed_as(self):
        """A finding a record reports and nobody owns is a note in a file.

        DRE-3544 owns the declined card that was in the batch anyway; DRE-3737
        owns the batch being mostly not the model's picks. Both are named here
        so the record says who carries each finding forward.
        """
        for card in ("DRE-3544", "DRE-3737"):
            self.assertIn(card, self.doc)

    def test_the_record_still_says_nothing_was_moved_in_either_pass(self):
        """The one thing a PROOF of a read-only mechanism must keep saying.

        Asserted for the second pass as well as the first: the record now spans
        two observation sittings, and the second one read a live board and ran a
        scorer over a live comment thread. A read-only claim that covers only the
        first sitting covers the half nobody was going to doubt.
        """
        self.assertIn("The drain was not run", self.doc)
        self.assertIn("2026-09-12", self.doc)


if __name__ == "__main__":                      # pragma: no cover
    unittest.main()
