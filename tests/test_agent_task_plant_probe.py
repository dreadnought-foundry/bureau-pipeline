"""The build agent's shell carries a throwaway BUREAU_PLANT_PROBE (DRE-7513).

The agent-log scrub (DRE-4268) has two passes: every secret value the run
holds (by value), then anything shaped like a key (by shape). The by-shape
pass has fired on real transcripts; the by-value pass has never had to act,
so nothing shows it works. The DRE-5357 proof record leaves its planted-secret
observation open for exactly that reason: a new repo secret does not reach the
agent — `claude-code-action` is handed a curated env — so an echo of it prints
nothing, the transcript holds nothing to replace, and the run reports
`by_value: {}`, which reads exactly like success.

Route B of that record, built here: a throwaway secret with nothing real at
stake, wired three ways in agent-task.yml.

  1. Declared under `on.workflow_call.secrets` as `{required: false}` — a
     reusable workflow that reads a secret it never declared reads `''`
     (tests/test_agent_linear_key.py), and optional keeps every caller as it
     is.
  2. In the `env:` of each of the three `anthropics/claude-code-action` build
     steps, never `with:` — step `env:` reaches the agent's shell, and the
     three `with:` blocks are held identical by
     tests/test_agent_task_rate_limit_retry.py.
  3. Handed to the scrub as `SCRUB_BUREAU_PLANT_PROBE` on the upload step,
     because the scrub reads each secret one by one and picks up nothing by
     itself.

Where the secret is not set it reads as empty, and the scrub drops empty
values, so nothing changes for any repo but the one the operator plants on.

Run: python3 -m pytest tests/test_agent_task_plant_probe.py -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / ".github" / "workflows" / "agent-task.yml"

PROBE = "BUREAU_PLANT_PROBE"
PROBE_REF = "${{ secrets.BUREAU_PLANT_PROBE }}"
SCRUB_PROBE = "SCRUB_BUREAU_PLANT_PROBE"
AGENT_ACTION = "anthropics/claude-code-action"
UPLOAD_SCRIPT = "upload_agent_log.py"

AGENT_STEPS = ("Implement card", "Implement card (retry 1)", "Implement card (retry 2)")
# Every secret the agent's shell may read: the two Linear keys its heartbeats
# use, and the probe. Anything else added to an agent step's env fails here.
AGENT_ENV_SECRETS = {"LINEAR_API_KEY", "LINEAR_PLANNER_KEY", PROBE}

SECRET_RE = re.compile(r"\bsecrets\.([A-Za-z0-9_]+)")


def load(text: str | None = None) -> dict:
    return yaml.safe_load(TASK.read_text(encoding="utf-8") if text is None else text)


def on_block(doc: dict) -> dict:
    # PyYAML reads the bare key `on` as the boolean True.
    return doc.get("on") or doc.get(True) or {}


def execute_steps(doc: dict) -> list[dict]:
    return doc["jobs"]["execute"].get("steps") or []


def agent_steps(doc: dict) -> list[dict]:
    return [s for s in execute_steps(doc)
            if str(s.get("uses") or "").startswith(AGENT_ACTION + "@")]


def upload_step(doc: dict) -> dict:
    found = [s for s in execute_steps(doc) if UPLOAD_SCRIPT in str(s.get("run") or "")]
    assert len(found) == 1, f"expected exactly one {UPLOAD_SCRIPT} step in job execute"
    return found[0]


def steps_missing_probe(doc: dict) -> list[str]:
    """The agent steps whose env does not hand the probe to the agent."""
    return [str(s.get("name")) for s in agent_steps(doc)
            if (s.get("env") or {}).get(PROBE) != PROBE_REF]


class ThePlantProbeReachesTheAgent(unittest.TestCase):

    def setUp(self):
        self.doc = load()

    def test_there_are_exactly_the_three_agent_steps(self):
        # So the per-step checks below cannot pass by finding none.
        names = [s.get("name") for s in agent_steps(self.doc)]
        self.assertEqual(names, list(AGENT_STEPS))

    def test_each_agent_step_hands_the_probe_to_the_agent_in_env(self):
        for step in agent_steps(self.doc):
            name = step.get("name")
            with self.subTest(step=name):
                env = step.get("env") or {}
                self.assertEqual(
                    env.get(PROBE), PROBE_REF,
                    f"step {name!r} does not carry {PROBE}: {PROBE_REF} in its env:")

    def test_no_agent_step_passes_the_probe_through_with(self):
        for step in agent_steps(self.doc):
            with self.subTest(step=step.get("name")):
                self.assertNotIn(PROBE, step.get("with") or {})

    def test_the_agent_env_reads_no_other_secret(self):
        for step in agent_steps(self.doc):
            name = step.get("name")
            with self.subTest(step=name):
                read = set()
                for value in (step.get("env") or {}).values():
                    read.update(SECRET_RE.findall(str(value)))
                self.assertEqual(read, AGENT_ENV_SECRETS,
                                 f"step {name!r} env reads {sorted(read)}")

    def test_dropping_the_line_from_one_step_names_that_step(self):
        # Non-vacuous: strip the probe from retry 1 only and the finder
        # reports retry 1 and nothing else.
        raw = TASK.read_text(encoding="utf-8")
        marker = "      - name: Implement card (retry 1)\n"
        head, tail = raw.split(marker, 1)
        tail = tail.replace(f"          {PROBE}: {PROBE_REF}\n", "", 1)
        self.assertEqual(steps_missing_probe(load(head + marker + tail)),
                         ["Implement card (retry 1)"])


class ThePlantProbeReachesTheScrub(unittest.TestCase):

    def setUp(self):
        self.doc = load()

    def test_the_upload_step_hands_the_probe_to_the_scrub(self):
        env = upload_step(self.doc).get("env") or {}
        self.assertEqual(
            env.get(SCRUB_PROBE), PROBE_REF,
            f"the upload step does not carry {SCRUB_PROBE}: {PROBE_REF} in its env:")


class ThePlantProbeIsDeclared(unittest.TestCase):

    def test_the_secret_is_declared_optional(self):
        secrets = on_block(load())["workflow_call"]["secrets"]
        self.assertIn(PROBE, secrets)
        self.assertEqual(secrets[PROBE], {"required": False})


if __name__ == "__main__":
    unittest.main()
