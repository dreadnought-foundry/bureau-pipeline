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
DRE-4677 moved it to 06:15 PT to have the proposal on the card before the 06:30
briefing is assembled. `self-groomer.yml` carries a `schedule:` of two UTC cron
lines, `15 13 * * *` and `15 14 * * *`; a `gate` job running
`scripts/groom_schedule_gate.py` (DRE-4688) decides which of the pair is 06:15
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

#: The two UTC cron lines of the morning proposal (DRE-4677). Every day both
#: fire and exactly one of them is 06:15 on the `America/Los_Angeles` clock;
#: the gate script decides which, never an offset written down here.
MORNING_CRONS = ["15 13 * * *", "15 14 * * *"]

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
            "Approve on the console (DRE-3337), and the 06:15 PT morning "
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
    switch is an input on both files, it reaches `--dry-run` on the step, and
    it silences the OTHER comment the run writes — the judgement receipt —
    because "no marker at all" is the whole of the promise.
    """

    def setUp(self):
        self.doc = _load("groomer.yml")
        self.groom = _step(self.doc, GROOM_STEP)
        self.env = self.groom.get("env") or {}

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
        self.assertIn("--dry-run", self.groom.get("run") or "",
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
        """The hand dispatch, the Approve-fired drain, and the 06:15 PT gated
        propose — each decision with its date, and the gate named so the
        operator knows what a quiet morning means."""
        section = self.doc.split("## The cadence", 1)[1].split("\n## ", 1)[0]
        for token in ("DRE-2683", "2026-08-23", "DRE-3337", "2026-09-08",
                      *THIRD_DECISION, "06:15 PT", "groom_schedule_gate.py",
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
