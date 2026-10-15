"""Shadow Replay can be called from a product repo (DRE-7306).

bureau-pipeline has no verifier stub, so the verifier replays the
model-switch bar asks for can only come from a product repo's own pull
requests. The product repo's stub calls `shadow-replay.yml`, which must then
run its scripts out of a bureau-pipeline checkout rather than the caller's
tree, read the caller's pull requests and cards, and hand its `pipeline_ref`
down to the two reviewers it nests.

What this file pins:

* `workflow_call` declares every dispatch input, name for name, plus a
  required `pipeline_ref` with no default;
* `pick` and `compare` run `shadow_replay.py` and the Python setup from the
  `.bureau-pipeline` checkout at `pipeline_ref`, and nothing from the
  caller's `scripts/` or `./.github/actions/`;
* the nested `qa-review.yml` and `verify.yml` calls pass `pipeline_ref` and
  stay `shadow: true`;
* a bureau-pipeline dispatch with today's inputs resolves to today's pick
  and compare commands, the script now read from the checkout;
* `pick` reads the caller's cards under its `repo:<slug>` label, the slug
  derived the way red-main-repair.yml derives it;
* with `from_run`, `pick` downloads `shadow-replay-pick` from that run in the
  calling repository and passes it as `--from-pick` with `--acted-on-only`.

Run: python3 -m pytest tests/test_shadow_replay_callable.py -v
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "shadow-replay.yml"

PIPELINE_REPO = "dreadnought-foundry/bureau-pipeline"
PIPELINE_REF = "${{ inputs.pipeline_ref || 'main' }}"
REPO = "dreadnought-foundry/bureau-pipeline"
PRODUCT_REPO = "dreadnought-foundry/Portico"
SCRIPT = ".bureau-pipeline/scripts/shadow_replay.py"


def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _on(doc: dict) -> dict:
    return doc.get("on", doc.get(True))


def _run_recording(script: str, env: dict, *commands: str) -> tuple[dict, str]:
    """Run a step's script with each of `commands` stubbed to record its argv."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for name in commands:
            stub = d / name
            stub.write_text("#!/bin/sh\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; done "
                            f"> {d / (name + '.argv')}\n", encoding="utf-8")
            stub.chmod(0o755)
        base = {"PATH": f"{d}{os.pathsep}{os.environ['PATH']}", "RUNNER_TEMP": str(d),
                "GITHUB_OUTPUT": str(d / "out")}
        base.update(env)
        subprocess.run(["bash", "-e", "-c", script], env=base, check=True)
        argv = {name: (d / (name + ".argv")).read_text(encoding="utf-8").splitlines()
                for name in commands if (d / (name + ".argv")).exists()}
        return argv, str(d)


class CallInputsTest(unittest.TestCase):
    def setUp(self):
        on = _on(_doc())
        self.dispatch = on["workflow_dispatch"]["inputs"]
        self.call = (on.get("workflow_call") or {}).get("inputs") or {}

    def test_every_dispatch_input_plus_pipeline_ref_name_for_name(self):
        self.assertEqual(sorted(set(self.dispatch) | {"pipeline_ref"}), sorted(self.call))
        self.assertNotIn("pipeline_ref", self.dispatch,
                         "a dispatch here resolves pipeline_ref to main")

    def test_pipeline_ref_is_required_with_no_default(self):
        spec = self.call["pipeline_ref"]
        self.assertEqual("string", spec["type"])
        self.assertIs(True, spec["required"])
        self.assertNotIn("default", spec)

    def test_each_shared_input_keeps_its_dispatch_default_and_requiredness(self):
        for name, spec in self.dispatch.items():
            with self.subTest(name=name):
                call = self.call[name]
                self.assertEqual(spec.get("default"), call.get("default"))
                self.assertEqual(bool(spec.get("required")), bool(call.get("required")))
                # `choice` exists only under dispatch; a call passes a string.
                want = "string" if spec["type"] == "choice" else spec["type"]
                self.assertEqual(want, call["type"])


class PipelineCheckoutTest(unittest.TestCase):
    """Under workflow_call the caller's tree has no `scripts/` and no actions."""

    def setUp(self):
        self.jobs = _doc()["jobs"]

    def _pipeline_checkout(self, steps: list) -> int:
        found = [i for i, s in enumerate(steps)
                 if str(s.get("uses", "")).startswith("actions/checkout")
                 and (s.get("with") or {}).get("repository") == PIPELINE_REPO]
        self.assertEqual(1, len(found))
        with_ = steps[found[0]]["with"]
        self.assertEqual(PIPELINE_REF, with_["ref"])
        self.assertEqual(".bureau-pipeline", with_["path"])
        return found[0]

    def test_pick_and_compare_run_everything_from_the_pipeline_checkout(self):
        bare_scripts = re.compile(r"(?<![\w./-])(?:\./)?scripts/")
        for job in ("pick", "compare"):
            steps = self.jobs[job]["steps"]
            at = self._pipeline_checkout(steps)
            ran_script = False
            for i, step in enumerate(steps):
                with self.subTest(job=job, step=step.get("name") or step.get("uses")):
                    uses = str(step.get("uses", ""))
                    run = str(step.get("run", ""))
                    if uses.startswith("./"):
                        self.assertTrue(uses.startswith("./.bureau-pipeline/.github/actions/"),
                                        uses)
                        self.assertGreater(i, at)
                        for line in str((step.get("with") or {}).get("requirements", "")).split():
                            self.assertTrue(line.startswith(".bureau-pipeline/"), line)
                    self.assertIsNone(bare_scripts.search(run), run)
                    if ".bureau-pipeline/scripts/" in run:
                        ran_script = True
                        self.assertGreater(i, at)
                    if uses.startswith("actions/checkout") and i > at:
                        self.assertIn("path", step.get("with") or {},
                                      "a bare checkout after it would wipe .bureau-pipeline")
            self.assertTrue(ran_script, f"{job} runs shadow_replay.py from the checkout")

    def test_the_python_setup_is_the_pipelines_own(self):
        for job in ("pick", "compare"):
            setup = [s for s in self.jobs[job]["steps"]
                     if "setup-python-cached" in str(s.get("uses", ""))]
            self.assertEqual(1, len(setup), job)
            self.assertEqual("./.bureau-pipeline/.github/actions/setup-python-cached",
                             setup[0]["uses"])
            self.assertEqual([".bureau-pipeline/requirements-dev.txt"],
                             setup[0]["with"]["requirements"].split())


class NestedReviewersTest(unittest.TestCase):
    def test_both_reviewers_get_pipeline_ref_and_stay_in_shadow(self):
        jobs = _doc()["jobs"]
        for job, reusable in (("replay-critic", "qa-review.yml"),
                              ("replay-verifier", "verify.yml")):
            with self.subTest(job=job):
                self.assertIn(f"/.github/workflows/{reusable}@", jobs[job]["uses"])
                self.assertEqual(PIPELINE_REF, jobs[job]["with"]["pipeline_ref"])
                self.assertIs(True, jobs[job]["with"]["shadow"])
                self.assertEqual("inherit", jobs[job]["secrets"])


class TodaysCommandsTest(unittest.TestCase):
    """A bureau-pipeline dispatch with today's inputs picks and compares as before."""

    def setUp(self):
        self.jobs = _doc()["jobs"]

    def _pick(self, **env) -> tuple[dict, str]:
        step = next(s for s in self.jobs["pick"]["steps"] if s.get("id") == "pick")
        base = {"GITHUB_REPOSITORY": REPO, "REVIEWER": "critic", "RECORDED_MODEL": "",
                "COUNT": "20", "PRS": "", "UNKNOWN_BUILDER": "false",
                "VERIFY_WORKFLOW": "", "FROM_RUN": ""}
        base.update(env)
        return _run_recording(step["run"], base, "python3")

    def test_pick_is_todays_command_from_the_checkout(self):
        argv, tmp = self._pick()
        argv = argv["python3"]
        self.assertEqual(SCRIPT, argv[0])
        label = argv.index("--repo-label")
        # Today's default label, `repo:` and the repository's name, spelled out.
        self.assertEqual("repo:" + REPO.rsplit("/", 1)[-1], argv[label + 1])
        del argv[label:label + 2]
        self.assertEqual([
            "pick", "--reviewer", "critic",
            "--recorded-model", "", "--count", "20", "--prs", "", "--repo", REPO,
            "--out", f"{tmp}/shadow-replay-pick/shadow-replay-pick.json",
            "--github-output", f"{tmp}/out", "--verify-workflow", ""], argv[1:])

    def test_a_product_repo_reads_its_own_pull_requests_and_cards(self):
        argv, _ = self._pick(GITHUB_REPOSITORY=PRODUCT_REPO)
        argv = argv["python3"]
        self.assertEqual(PRODUCT_REPO, argv[argv.index("--repo") + 1])
        # red-main-repair.yml's slug: the name, lowercased.
        self.assertEqual("repo:portico", argv[argv.index("--repo-label") + 1])

    def test_compare_is_todays_command_from_the_checkout(self):
        step = next(s for s in self.jobs["compare"]["steps"]
                    if s.get("name") == "Compare the replays with the recorded verdicts")
        argv, tmp = _run_recording(
            step["run"], {"REVIEWER": "critic", "MODEL": "m", "RECORDED_MODEL": "r"},
            "python3")
        self.assertEqual([
            SCRIPT, "compare", "--reviewer", "critic", "--model", "m",
            "--recorded-model", "r",
            "--pick", f"{tmp}/shadow-replay-pick/shadow-replay-pick.json",
            "--artifacts", f"{tmp}/shadow-verdicts",
            "--out-dir", f"{tmp}/shadow-replay-report"], argv["python3"])

    def test_a_dispatch_checks_the_pipeline_out_at_main(self):
        # Under workflow_dispatch `inputs.pipeline_ref` is empty, so the
        # expression falls back to main.
        for job in ("pick", "compare"):
            refs = [(s.get("with") or {}).get("ref") for s in self.jobs[job]["steps"]
                    if (s.get("with") or {}).get("repository") == PIPELINE_REPO]
            self.assertEqual([PIPELINE_REF], refs, job)


class FromRunUnderCallTest(unittest.TestCase):
    def setUp(self):
        doc = _doc()
        self.call = (_on(doc).get("workflow_call") or {}).get("inputs") or {}
        self.steps = doc["jobs"]["pick"]["steps"]

    def test_from_run_is_a_call_input(self):
        self.assertEqual("string", self.call["from_run"]["type"])
        self.assertEqual("", self.call["from_run"]["default"])

    def test_the_earlier_pick_comes_from_the_calling_repository(self):
        download = next(s for s in self.steps if "gh run download" in s.get("run", ""))
        self.assertEqual("inputs.from_run != ''", download["if"])
        self.assertEqual("${{ inputs.from_run }}", download["env"]["FROM_RUN"])
        # In a called workflow GITHUB_REPOSITORY is the caller.
        argv, _ = _run_recording(download["run"],
                                 {"GITHUB_REPOSITORY": PRODUCT_REPO, "FROM_RUN": "123"}, "gh")
        gh = argv["gh"]
        self.assertEqual(["run", "download", "123"], gh[:3])
        self.assertEqual(PRODUCT_REPO, gh[gh.index("--repo") + 1])
        self.assertEqual("shadow-replay-pick", gh[gh.index("-n") + 1])

    def test_pick_replays_it_acted_on_only(self):
        step = next(s for s in self.steps if s.get("id") == "pick")
        argv, tmp = _run_recording(step["run"], {
            "GITHUB_REPOSITORY": PRODUCT_REPO, "REVIEWER": "verifier", "RECORDED_MODEL": "",
            "COUNT": "20", "PRS": "", "UNKNOWN_BUILDER": "false", "VERIFY_WORKFLOW": "",
            "FROM_RUN": "123"}, "python3")
        argv = argv["python3"]
        self.assertEqual(SCRIPT, argv[0])
        self.assertEqual(f"{tmp}/from-pick/shadow-replay-pick.json",
                         argv[argv.index("--from-pick") + 1])
        self.assertIn("--acted-on-only", argv)
        self.assertEqual(PRODUCT_REPO, argv[argv.index("--repo") + 1])


if __name__ == "__main__":
    unittest.main()
