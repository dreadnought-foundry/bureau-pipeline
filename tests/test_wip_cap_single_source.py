"""One WIP cap across every promotion path (DRE-2529, continues DRE-2527).

Origin: `reconcile.yml` has taken a `max_wip` workflow_call input since it was
written (agent-bureau's stub passes `"12"`), but the two EVENT-DRIVEN
promotion paths — `plan.yml`'s epic-activate route and `linear-sync.yml`'s
merge handler — each hardcoded `MAX_WIP=8` inline and accepted no input at
all. Both call `reconcile.py --promote-only`, and both exist purely to
promote work NOW instead of up to ~80 minutes later (GitHub delivers the
`*/15` cron 78-100 min apart in practice; DRE-1260 activated 9s after a
sweep and waited ~80 minutes).

So the live behaviour was not "two caps". It was: **between 8 and 11 cards in
flight on a repo whose cap is 12, the anti-stall fast path refuses to promote
and the card waits for the slow cron sweep — which then promotes it anyway,
at 12.** The optimisation switched itself off precisely in the WIP band it
was built for, and the eventual promotion proved the refusal was wrong.

Contract under test (enforced live by scripts/check_wip_cap.py, which runs as
a Pipeline Tests step and whose functions these tests exercise):

  * EVERY reusable workflow with a step that runs `reconcile.py` in a
    PROMOTING mode (the full sweep, or `--promote-only`) declares a
    `max_wip` workflow_call input, type: string, defaulted to the ONE
    canonical cap.
  * That cap is single-sourced: the workflows' declared default equals
    `reconcile.DEFAULT_MAX_WIP`, the value the script itself falls back to
    when `MAX_WIP` is unset or empty. A stub that passes nothing therefore
    inherits a default that is correct on its own.
  * NO workflow assigns `MAX_WIP` a literal — not in a step/job/workflow
    `env:` block, not inline in a `run:` line. The only accepted value is
    the caller's input, threaded verbatim.

`effective_cap_by_workflow` resolves what each promotion path would ACTUALLY
export, given the inputs a calling stub passes. That is the red-first
assertion: before this card, a stub passing `max_wip: "12"` got 12 from
reconcile.yml and 8 from the other two.
"""

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_wip_cap as cwc  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"

# The three promotion paths audited on 2026-08-19. New reusable workflows that
# promote are covered automatically by the directory sweep; this floor guards
# against the sweep going vacuous (a path typo finding zero files).
KNOWN_PROMOTERS = {"plan.yml", "linear-sync.yml", "reconcile.yml"}


def wf(text):
    return yaml.safe_load(text)


class LiveWorkflowsTest(unittest.TestCase):
    """Parse the real .github/workflows/*.yml — the shipping contract."""

    @classmethod
    def setUpClass(cls):
        cls.violations, cls.stats = cwc.check_dir(WORKFLOWS)
        cls.docs = {
            p.name: yaml.safe_load(p.read_text())
            for p in sorted(WORKFLOWS.glob("*.yml"))
        }

    def test_no_violations_in_live_workflows(self):
        """THE gate: every promoting reusable workflow takes the caller's cap.
        (RED before DRE-2529: plan.yml and linear-sync.yml each hardcoded
        `MAX_WIP=8` inline and declared no input.)"""
        self.assertEqual(
            [], self.violations, "\n".join(["WIP-cap violations:"] + self.violations)
        )

    def test_sweep_is_not_vacuous(self):
        """A checker that inspects nothing passes everything."""
        self.assertGreaterEqual(self.stats["workflows"], 20)
        self.assertGreaterEqual(self.stats["promotion_steps"], 3)

    def test_the_three_known_promoters_are_all_found(self):
        """plan.yml / linear-sync.yml / reconcile.yml are the audited paths —
        if the extractor stops seeing one, the guard has rotted."""
        found = {
            name for name, doc in self.docs.items() if cwc.promotion_steps(doc)
        }
        self.assertTrue(
            KNOWN_PROMOTERS <= found,
            f"promotion paths not detected: {sorted(KNOWN_PROMOTERS - found)}",
        )

    def test_all_promotion_paths_agree_on_the_callers_cap(self):
        """The defect, stated as a test: for ONE repo passing ONE value, every
        promotion path must export that value. Before this card, a stub
        passing `max_wip: "12"` got 12 from reconcile.yml and 8 from the two
        event-driven paths — so between 8 and 11 in flight the fast path
        refused and the cron promoted the same card anyway, at 12."""
        caps = cwc.effective_cap_by_workflow(self.docs, {"max_wip": "12"})
        for name in sorted(KNOWN_PROMOTERS):
            self.assertIn(name, caps, f"{name} exposes no promotion cap")
        self.assertEqual(
            {"12"},
            set(caps.values()),
            f"promotion paths disagree on the caller's cap: {caps}",
        )

    def test_portico_style_lower_override_is_a_real_override(self):
        """A repo that deliberately runs BELOW the default must actually get
        its lower cap on every path — not have it flattened back to the
        default by a path that ignores the input."""
        caps = cwc.effective_cap_by_workflow(self.docs, {"max_wip": "3"})
        self.assertEqual({"3"}, set(caps.values()), caps)

    def test_stub_passing_nothing_inherits_the_one_default(self):
        """A consumer stub that passes no input inherits the declared default,
        and that default is the same on every path — so the fan-out to the
        fleet can land repo by repo without any window of disagreement."""
        caps = cwc.effective_cap_by_workflow(self.docs, {})
        self.assertEqual(
            {str(cwc.canonical_default())}, set(caps.values()), caps
        )

    def test_default_is_single_sourced_with_the_script(self):
        """The workflows' default and reconcile.py's own fallback are ONE
        value. A divergent script fallback is a fifth cap hiding behind an
        unset env var."""
        self.assertEqual(cwc.script_default(), cwc.canonical_default())
        for name in sorted(KNOWN_PROMOTERS):
            spec = cwc.max_wip_input(self.docs[name])
            self.assertIsNotNone(spec, f"{name} declares no max_wip input")
            self.assertEqual("string", spec.get("type"), name)
            self.assertEqual(str(cwc.canonical_default()), spec.get("default"), name)

    def test_no_literal_cap_anywhere_in_the_workflow_dir(self):
        """Acceptance: no `MAX_WIP` literal remains in any pipeline workflow."""
        offenders = []
        for name, doc in self.docs.items():
            for where, value in cwc.max_wip_assignments(doc, name):
                if not cwc.is_input_expression(value):
                    offenders.append(f"{where} = {value!r}")
        self.assertEqual([], offenders, "\n".join(["literal caps:"] + offenders))


class SelfHostStubsTest(unittest.TestCase):
    """This repo is itself a consumer (DRE-1929 self-hosting). Its stubs are
    the first repo of the fleet fan-out — they must be internally consistent."""

    @classmethod
    def setUpClass(cls):
        cls.stubs = {
            p.name: yaml.safe_load(p.read_text())
            for p in sorted(WORKFLOWS.glob("self-*.yml"))
        }

    def test_stub_caps_are_strings_and_agree_with_each_other(self):
        """A stub may override the cap, but only with a quoted string (an
        unquoted `12` is a YAML int and GitHub rejects it against a
        `type: string` input), and all three of this repo's promotion stubs
        must carry the SAME value — a stub set is a repo's one cap."""
        caps = {}
        for name, doc in self.stubs.items():
            for job in (doc.get("jobs") or {}).values():
                if "max_wip" in ((job or {}).get("with") or {}):
                    value = job["with"]["max_wip"]
                    self.assertIsInstance(value, str, f"{name}: max_wip must be quoted")
                    caps[name] = value
        self.assertLessEqual(
            len(set(caps.values())), 1, f"self-host stubs disagree on the cap: {caps}"
        )


class ScriptFallbackTest(unittest.TestCase):
    """reconcile.py's own read of MAX_WIP. Threading a workflow_call input
    into the env introduces a failure mode the old literal did not have: on
    any event where the `inputs` context is empty the interpolation yields
    the EMPTY STRING, and `int("")` would crash the promotion step outright
    (a stall dressed up as a red run). Empty must mean "the one default"."""

    def test_unset_and_empty_both_resolve_to_the_one_default(self):
        import reconcile

        self.assertEqual(reconcile.DEFAULT_MAX_WIP, reconcile.resolve_max_wip(None))
        self.assertEqual(reconcile.DEFAULT_MAX_WIP, reconcile.resolve_max_wip(""))
        self.assertEqual(reconcile.DEFAULT_MAX_WIP, reconcile.resolve_max_wip("   "))

    def test_a_real_value_still_wins(self):
        import reconcile

        self.assertEqual(12, reconcile.resolve_max_wip("12"))
        self.assertEqual(3, reconcile.resolve_max_wip(" 3 "))

    def test_garbage_falls_back_loudly_rather_than_crashing_the_sweep(self):
        import reconcile

        self.assertEqual(reconcile.DEFAULT_MAX_WIP, reconcile.resolve_max_wip("lots"))


class SyntheticViolationsTest(unittest.TestCase):
    """Each violation class the checker must catch, pinned so the checker
    itself cannot rot into a no-op."""

    PROMOTE_RUN = "python3 .bureau-pipeline/scripts/reconcile.py --promote-only\n"

    def _reusable(self, *, inputs_yaml, step_env, run):
        return wf(
            "name: T\n"
            "on:\n"
            "  workflow_call:\n"
            "    inputs:\n"
            f"{inputs_yaml}"
            "jobs:\n"
            "  j:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: s\n"
            f"{step_env}"
            "        run: |\n"
            f"          {run}"
        )

    GOOD_INPUTS = (
        "      pipeline_ref:\n"
        "        type: string\n"
        "        default: main\n"
        "      max_wip:\n"
        "        type: string\n"
        "        default: \"8\"\n"
    )
    GOOD_ENV = "        env:\n          MAX_WIP: ${{ inputs.max_wip }}\n"

    def test_clean_workflow_passes(self):
        doc = self._reusable(
            inputs_yaml=self.GOOD_INPUTS, step_env=self.GOOD_ENV, run=self.PROMOTE_RUN
        )
        self.assertEqual([], cwc.check_workflow(doc, "t.yml"))

    def test_literal_in_env_block_is_caught(self):
        doc = self._reusable(
            inputs_yaml=self.GOOD_INPUTS,
            step_env="        env:\n          MAX_WIP: \"8\"\n",
            run=self.PROMOTE_RUN,
        )
        self.assertTrue(any("literal" in v for v in cwc.check_workflow(doc, "t.yml")))

    def test_literal_inline_in_run_line_is_caught(self):
        """The exact shape this card removes: `MAX_WIP=8 \\` prefixed onto the
        reconcile.py invocation."""
        doc = self._reusable(
            inputs_yaml=self.GOOD_INPUTS,
            step_env="",
            run="MAX_WIP=8 python3 .bureau-pipeline/scripts/reconcile.py --promote-only\n",
        )
        self.assertTrue(any("literal" in v for v in cwc.check_workflow(doc, "t.yml")))

    def test_missing_input_declaration_is_caught(self):
        doc = self._reusable(
            inputs_yaml="      pipeline_ref:\n        type: string\n        default: main\n",
            step_env=self.GOOD_ENV,
            run=self.PROMOTE_RUN,
        )
        self.assertTrue(
            any("max_wip" in v and "input" in v for v in cwc.check_workflow(doc, "t.yml"))
        )

    def test_wrong_default_is_caught(self):
        doc = self._reusable(
            inputs_yaml=(
                "      pipeline_ref:\n"
                "        type: string\n"
                "        default: main\n"
                "      max_wip:\n"
                "        type: string\n"
                "        default: \"12\"\n"
            ),
            step_env=self.GOOD_ENV,
            run=self.PROMOTE_RUN,
        )
        self.assertTrue(any("default" in v for v in cwc.check_workflow(doc, "t.yml")))

    def test_promotion_step_with_no_cap_in_scope_is_caught(self):
        """No MAX_WIP anywhere in scope means the step silently falls through
        to the script's own fallback instead of taking the caller's value."""
        doc = self._reusable(
            inputs_yaml=self.GOOD_INPUTS, step_env="", run=self.PROMOTE_RUN
        )
        self.assertTrue(
            any("no MAX_WIP" in v for v in cwc.check_workflow(doc, "t.yml"))
        )

    def test_non_promoting_invocations_are_not_required_to_set_the_cap(self):
        """`--close-epics` and `--conflicts-only` promote nothing; demanding a
        cap there would be noise."""
        for flag in ("--close-epics", "--conflicts-only"):
            doc = self._reusable(
                inputs_yaml="      pipeline_ref:\n        type: string\n        default: main\n",
                step_env="",
                run=f"python3 .bureau-pipeline/scripts/reconcile.py {flag}\n",
            )
            self.assertEqual([], cwc.promotion_steps(doc), flag)
            self.assertEqual([], cwc.check_workflow(doc, "t.yml"), flag)

    def test_non_reusable_workflow_with_a_literal_is_still_caught(self):
        """A trigger stub (no workflow_call) cannot declare an input, but it
        must not smuggle a literal cap in either."""
        doc = wf(
            "name: T\n"
            "on:\n"
            "  schedule:\n"
            "    - cron: \"*/15 * * * *\"\n"
            "jobs:\n"
            "  j:\n"
            "    runs-on: ubuntu-latest\n"
            "    env:\n"
            "      MAX_WIP: \"8\"\n"
            "    steps:\n"
            "      - run: echo hi\n"
        )
        self.assertTrue(any("literal" in v for v in cwc.check_workflow(doc, "stub.yml")))

    def test_effective_cap_reports_the_literal_a_broken_workflow_would_use(self):
        """The resolver reports what the workflow ACTUALLY exports, so the
        live disagreement test above fails loudly rather than erroring out."""
        docs = {
            "good.yml": self._reusable(
                inputs_yaml=self.GOOD_INPUTS,
                step_env=self.GOOD_ENV,
                run=self.PROMOTE_RUN,
            ),
            "bad.yml": self._reusable(
                inputs_yaml=self.GOOD_INPUTS,
                step_env="",
                run="MAX_WIP=8 python3 .bureau-pipeline/scripts/reconcile.py --promote-only\n",
            ),
        }
        caps = cwc.effective_cap_by_workflow(docs, {"max_wip": "12"})
        self.assertEqual({"good.yml": "12", "bad.yml": "8"}, caps)


if __name__ == "__main__":
    unittest.main()
