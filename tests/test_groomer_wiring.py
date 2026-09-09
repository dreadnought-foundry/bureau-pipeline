"""The groomer runs on demand, and never on a schedule — two decisions.

**D5 (DRE-2683, approved by the operator on 2026-08-23):** on demand, until the
groomer's judgement has been audited. Not on a schedule. A groomer running
unattended over two hundred cards before anyone has checked its calls is the
same mistake as trusting a critic's verdicts before comparing them to a
held-back set.

**The amendment (DRE-3337, green-lit 2026-09-08):** the DRAIN may also be fired
by the CEO's Approve on the console, as a `repository_dispatch` of type
`groom-drain` — "no hand dispatch, no operator script". D5 is narrowed, not
reversed, and the narrowing is what this file asserts: that trigger is still ON
DEMAND (one person's Approve, not a clock — there is still no `schedule:`), and
it reaches DRAIN ONLY. `mode` is a literal on that event, so nothing in the
payload can reach it; a drain makes no model call (DRE-3338), so the judgement
D5 wanted audited before it ran unattended is never run by this trigger; and
`propose` stays `workflow_dispatch`-only.

So the trigger shape is part of the contract and is asserted here against the
LIVE workflow files (the pattern tests/test_self_host_stubs.py uses): a
`schedule:` added later turns this red rather than quietly starting a sweep
nobody asked for, and a `propose` reachable from the dispatch turns it red
rather than putting an unaudited model call on a trigger nobody watches.

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


class OnDemandOnlyTest(unittest.TestCase):
    def test_the_stub_takes_exactly_the_two_on_demand_triggers(self):
        on = _on(_load("self-groomer.yml"))
        self.assertIn("workflow_dispatch", on)
        self.assertNotIn(
            "schedule", on,
            "D5: the groomer runs on demand until its judgement has been "
            "audited — a cron here is the decision being reversed silently",
        )
        self.assertEqual(
            set(on), {"workflow_dispatch", "repository_dispatch"},
            "on demand means two triggers and no more: a person dispatching "
            "it, and the CEO's Approve on the console (DRE-3337)",
        )
        self.assertEqual(
            (on["repository_dispatch"] or {}).get("types"), ["groom-drain"],
            "the contract with the console's approve mutation is ONE event "
            "type — a wider `types:` is a wider trigger than D5 was amended for",
        )

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

    def test_propose_stays_workflow_dispatch_only(self):
        """`propose` makes the model call D5 wanted audited before it ran
        unattended. It keeps the trigger a person has to open Actions for."""
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

    def test_the_pen_switch_is_read_on_both_triggers(self):
        """DRE-3035/DRE-3285: the hold is a repository variable, and a drain
        that cannot see it is a pen with a hole in it — on either trigger."""
        for name, job in (_load("self-groomer.yml").get("jobs") or {}).items():
            self.assertEqual(
                _expression((job.get("with") or {}).get("intake_hold")),
                "vars.INTAKE_HOLD",
                f"job {name!r} does not read the pen's switch",
            )

    def test_no_workflow_in_this_repo_schedules_the_groomer(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            doc = yaml.safe_load(path.read_text())
            if not isinstance(doc, dict):
                continue
            if "schedule" not in _on(doc):
                continue
            job = next(iter((doc.get("jobs") or {}).values()), {})
            self.assertNotIn(
                "groomer.yml", str(job.get("uses") or ""),
                f"{path.name} puts the groomer on a schedule",
            )

    def test_every_job_calls_the_reusable_at_the_qualified_ref(self):
        """One job per trigger, and both of them the same reusable at the same
        ref with the same secrets — the stub owns the trigger and nothing else."""
        jobs = _load("self-groomer.yml")["jobs"]
        self.assertTrue(jobs)
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
    code (`standards/engineering.md`). Both decisions, each with its date, in
    every place the old single-trigger sentence was written."""

    def test_the_stub_header_no_longer_claims_one_trigger(self):
        self.assertNotIn(
            "AND NOTHING ELSE", _header("self-groomer.yml"),
            "the header records a trigger set the file no longer carries",
        )

    def test_the_stub_header_records_both_decisions_with_their_dates(self):
        header = _header("self-groomer.yml")
        for token in ("DRE-2683", "2026-08-23", "DRE-3337", "2026-09-08",
                      "repository_dispatch"):
            self.assertIn(token, header, f"the header never names {token}")

    def test_the_reusables_header_no_longer_describes_the_old_stub(self):
        header = _header("groomer.yml")
        self.assertNotIn(
            "workflow_dispatch and nothing else", header,
            "groomer.yml still describes its caller as single-trigger",
        )
        self.assertIn("DRE-3337", header)

    def test_this_modules_docstring_says_the_same(self):
        for token in ("DRE-2683", "2026-08-23", "DRE-3337", "2026-09-08",
                      "groom-drain"):
            self.assertIn(token, __doc__ or "",
                          f"the wiring test's docstring never names {token}")


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


if __name__ == "__main__":                      # pragma: no cover
    unittest.main()
