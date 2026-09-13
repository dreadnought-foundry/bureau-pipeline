"""The planner's rerun reads its own death marker and moves down the ladder (DRE-3824).

What broke (2026-09-12/13, DRE-3693): every planner run on `claude-fable-5-1`
died at once — 1 turn, $0, `is_error`, one run's text "You've hit your monthly
spend limit". plan.yml's "Record is_error death" step stamped
`model-error: claude-fable-5-1` and promised "the medic rerun will switch to the
alternate model". Nothing read the marker: the Select-model step asks only the
availability probe, and in subscription mode the probe gets 429 for every model
and calls that available. The 15:46 PT rerun on 2026-09-12 chose Fable again,
71 seconds after the marker.

The Select-model step now asks one more question: did the planner attempt that
started LAST (its `planner agent starting` heartbeat) die on the model we just
chose? If so it selects again with `--avoid <that model>`. Scoped to the last
attempt, so a later plan tries the top of the ladder again — the planner stays
on Fable whenever Fable works.

These tests run the step's REAL shell against the real selector, with the
Linear read stubbed, so the wiring is proven by execution rather than by grep.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WF = os.path.join(ROOT, ".github", "workflows", "plan.yml")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import model_fallback as mf  # noqa: E402

FABLE51 = "claude-fable-5-1"
OPUS = "claude-opus-5"
SONNET = "claude-sonnet-4-6"

HEARTBEAT_NEEDLE = "planner agent starting"


def steps() -> list[dict]:
    doc = yaml.safe_load(open(WF).read())
    return [s for job in doc["jobs"].values() for s in job.get("steps") or []]


def step(name: str) -> dict:
    for s in steps():
        if (s.get("name") or "") == name:
            return s
    raise AssertionError(f"plan.yml has no step named {name!r}")


# A stand-in for linear_ops.py. `count-comments <card> <needle> --since <m>`
# answers from a JSON thread (oldest→newest) with the real function's rule:
# only comments after the most recent one containing <m> count. Exit 1 when
# asked to, the way a Linear outage surfaces.
STUB_LINEAR_OPS = r'''
import json, os, sys
if os.environ.get("STUB_LINEAR_FAIL"):
    print("linear down", file=sys.stderr); sys.exit(1)
args = sys.argv[1:]
assert args[0] == "count-comments", args
card, needle = args[1], args[2]
since = args[args.index("--since") + 1] if "--since" in args else None
with open(os.environ["STUB_LINEAR_LOG"], "a") as fh:
    fh.write(json.dumps({"card": card, "needle": needle, "since": since}) + "\n")
bodies = json.loads(os.environ["STUB_THREAD"])
if since:
    for i in range(len(bodies) - 1, -1, -1):
        if since in bodies[i]:
            bodies = bodies[i + 1:]
            break
print(sum(1 for b in bodies if needle in b))
'''


class SelectModelStepRunsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        scripts = os.path.join(self.tmp, ".bureau-pipeline", "scripts")
        os.makedirs(scripts)
        shutil.copy(os.path.join(ROOT, "scripts", "model_fallback.py"), scripts)
        shutil.copytree(os.path.join(ROOT, "config"),
                        os.path.join(self.tmp, ".bureau-pipeline", "config"))
        with open(os.path.join(scripts, "linear_ops.py"), "w") as fh:
            fh.write(STUB_LINEAR_OPS)
        self.log = os.path.join(self.tmp, "linear.log")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_step(self, thread, *, linear_fails=False, available=None):
        run = step("Select model")["run"].replace(
            "${{ github.event.client_payload.identifier }}", "DRE-3693"
        )
        out_file = os.path.join(self.tmp, "gh_output")
        summary = os.path.join(self.tmp, "summary")
        env = dict(os.environ)
        env.update({
            "GITHUB_OUTPUT": out_file,
            "GITHUB_STEP_SUMMARY": summary,
            "RUNNER_TEMP": self.tmp,
            "LINEAR_API_KEY": "stub",
            "STUB_THREAD": json.dumps(thread),
            "STUB_LINEAR_LOG": self.log,
            "BUREAU_FAKE_AVAILABLE": json.dumps(
                available or {FABLE51: True, OPUS: True, SONNET: True}
            ),
        })
        if linear_fails:
            env["STUB_LINEAR_FAIL"] = "1"
        proc = subprocess.run(["bash", "-e", "-c", run], cwd=self.tmp,
                              env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        outputs = dict(
            line.split("=", 1) for line in open(out_file).read().splitlines()
            if "=" in line
        )
        return outputs

    def heartbeat(self, model):
        return f"🧠 {mf.attempt_marker(model)} — {HEARTBEAT_NEEDLE}. model-policy: …"

    def death(self, model):
        return ("🪦 planner died with API/model error (is_error) on "
                f"{model}\n{mf.error_marker(model)}")

    def test_a_death_on_fable_in_the_last_attempt_moves_the_planner_to_opus(self):
        # DRE-3693's thread at 14:28 PT on 2026-09-13, in shape.
        out = self.run_step([self.heartbeat(FABLE51), self.death(FABLE51)])
        self.assertEqual(out["model"], OPUS)
        self.assertTrue(out["why"].startswith("DEGRADED"), out["why"])

    def test_a_card_with_no_death_stays_on_fable(self):
        out = self.run_step([self.heartbeat(FABLE51)])
        self.assertEqual(out["model"], FABLE51)

    def test_an_old_fable_death_before_a_later_attempt_does_not_hold_fable_off(self):
        # Fable died once, Opus planned after it: the next plan tries Fable again.
        out = self.run_step([
            self.heartbeat(FABLE51), self.death(FABLE51), self.heartbeat(OPUS),
        ])
        self.assertEqual(out["model"], FABLE51)

    def test_a_linear_failure_keeps_the_first_choice(self):
        out = self.run_step([self.heartbeat(FABLE51), self.death(FABLE51)],
                            linear_fails=True)
        self.assertEqual(out["model"], FABLE51)

    def test_the_read_asks_for_the_chosen_model_since_the_last_heartbeat(self):
        self.run_step([])
        calls = [json.loads(l) for l in open(self.log).read().splitlines()]
        self.assertEqual(calls, [{
            "card": "DRE-3693",
            "needle": mf.error_marker(FABLE51),
            "since": HEARTBEAT_NEEDLE,
        }])


class TheTwoHalvesAgreeTest(unittest.TestCase):
    """The step scopes on a phrase another step writes; pin both ends."""

    def test_the_heartbeat_writes_the_phrase_the_select_step_scopes_on(self):
        self.assertIn(HEARTBEAT_NEEDLE, step("Plan heartbeat")["run"])
        self.assertIn(f'--since "{HEARTBEAT_NEEDLE}"', step("Select model")["run"])

    def test_the_select_step_can_read_linear(self):
        self.assertIn("LINEAR_API_KEY", step("Select model").get("env") or {})

    def test_the_death_step_writes_the_marker_the_select_step_reads(self):
        record = step("Record is_error death")["run"]
        self.assertIn("error_marker", record)
        self.assertEqual(mf.error_marker(OPUS), f"model-error: {OPUS}")
        self.assertIn('"model-error: $MODEL"', step("Select model")["run"])


if __name__ == "__main__":
    unittest.main()
