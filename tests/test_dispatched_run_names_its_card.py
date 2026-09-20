"""RED-first: a run started by dispatch or by a comment names its card in its
own log, so the medic can find the card for a run recorded on main (DRE-4407).

When the pipeline re-runs a crashed review, or starts a fix off a verdict
comment, GitHub records that run on the DEFAULT branch — not on the pull
request's branch. `medic_retry.card_for_run` asks the head branch first and the
run's own log second, so for every such run both halves answered nothing and
the medic had no card at all: no evidence note, no hold of its own, no way to
ask the card whether a retry is allowed. Replaying the 2026-09-08 incident for
the DRE-3432 proof, the second and third crashes — both on re-dispatched run
34287604200, recorded on `main` — resolved to None, and the real medic run
after the second crash (34287882336) was skipped entirely.

`plan.yml` already solved the same problem for the planner (DRE-3223) by
echoing one `bureau-card: DRE-n` line; this pins the same line onto every
reusable workflow that can run for a pull request WITHOUT being on that pull
request's branch.

WHICH WORKFLOWS IS DISCOVERED, NOT LISTED. The population is every reusable
(`workflow_call`) workflow whose text reads a dispatched or comment-borne pull
request number — `github.event.inputs.pr_number` or
`github.event.issue.number`. Today that is qa-review, agent-fix, verify and
merge-gate; tomorrow's fifth workflow is covered the day it is added rather
than the day somebody remembers this file.

The card line is not read off the YAML alone: for every discovered workflow the
card-naming fragment of the printing step is EXECUTED, under the workflow's own
shell flags, with `gh pr view` shimmed to answer a head ref — and its stdout is
re-rendered the way `gh run view --log` renders it and handed to the real
`medic_retry.card_for_run`. What the medic parses is what the workflow prints.

Run: cd bureau-pipeline && python3 -m pytest tests/test_dispatched_run_names_its_card.py -v
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))

import medic_retry  # noqa: E402 — the one card-resolution rule, run for real

# A pull request number that arrives from OUTSIDE the pull request's own event:
# a `workflow_dispatch` input, or the issue a comment was left on. Either way
# GitHub records the run on the default branch, so `card_from_branch` answers
# nothing and the log is the only place left for the card to be.
PR_NUMBER_READS = re.compile(
    r"github\.event\.inputs\.pr_number|github\.event\.issue\.number"
)

# The marker, exactly as plan.yml echoes it (DRE-3223) and exactly as
# `medic_retry._LOG_CARD` matches it.
CARD_ECHO = 'echo "bureau-card: $CARD"'

CARD_REF = "agent/DRE-1234-x"
CARD = "DRE-1234"
# Names no card, and still passes the agent-branch guards agent-fix.yml and
# merge-gate.yml apply before they resolve one — so the run reaches the card
# line and declines to print it, rather than never reaching it.
CARDLESS_REF = "agent/no-card-here"

# The values GitHub would substitute for a re-dispatched run on PR #4407. An
# `A || B` expression takes the first named value that is non-empty, as Actions
# does; a `steps.`/`secrets.`/`vars.`/`needs.` read this harness does not model
# renders empty, and an unmodelled `github.` read is an error rather than a
# silent blank.
PAYLOAD = {
    "github.event.pull_request.number": "",
    "github.event.inputs.pr_number": "4407",
    "github.event.issue.number": "",
    "github.event.workflow_run.pull_requests[0].number": "",
    "github.repository": "dreadnought-foundry/bureau-pipeline",
    "github.event_name": "workflow_dispatch",
    "github.triggering_actor": "github-actions[bot]",
}

# `gh pr view` and nothing else: answers the requested `--json` fields about a
# pull request whose head ref is FAKE_BRANCH, honouring a `--jq .field`.
GH_SHIM = r'''#!/usr/bin/env python3
import json, os, sys

argv = sys.argv[1:]
fields, jq = [], None
for i, a in enumerate(argv):
    if a == "--json" and i + 1 < len(argv):
        fields = argv[i + 1].split(",")
    if a == "--jq" and i + 1 < len(argv):
        jq = argv[i + 1]
record = {
    "headRefName": os.environ["FAKE_BRANCH"],
    "state": "OPEN",
    "headRefOid": "0" * 40,
    "mergeStateStatus": "CLEAN",
    "baseRefName": "main",
    "isDraft": False,
}
missing = [f for f in fields if f not in record]
if missing:
    sys.exit("this shim does not model %s" % ",".join(missing))
out = {f: record[f] for f in fields} if fields else record
print(out[jq.lstrip(".")] if jq else json.dumps(out))
'''


def _doc(name: str) -> dict:
    with open(WORKFLOWS / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def discovered() -> dict[str, list[str]]:
    """Every reusable workflow that can run for a pull request it is not on.

    `{filename: [the pull-request-number reads it makes]}` — the population
    this guard holds to the card line. Discovered from the workflow files, so
    a workflow added tomorrow is covered tomorrow.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        triggers = _doc(path.name).get("on", _doc(path.name).get(True))
        if not isinstance(triggers, dict) or "workflow_call" not in triggers:
            continue
        reads = sorted(set(PR_NUMBER_READS.findall(text)))
        if reads:
            found[path.name] = reads
    return found


def printing_step(name: str) -> tuple[str, dict] | tuple[None, None]:
    """The `(job id, step)` that says the card, or `(None, None)`."""
    for job_id, job in (_doc(name).get("jobs") or {}).items():
        for step in job.get("steps") or []:
            if "bureau-card:" in (step.get("run") or ""):
                return job_id, step
    return None, None


def _render(text: str) -> str:
    def sub(match: re.Match) -> str:
        for part in (p.strip() for p in match.group(1).split("||")):
            if part in PAYLOAD:
                if PAYLOAD[part]:
                    return PAYLOAD[part]
                continue
            if part.split(".")[0] in ("steps", "secrets", "vars", "needs", "inputs", "env"):
                continue
            raise AssertionError(f"this harness does not model {part!r}")
        return ""

    return re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", sub, text)


def run_card_fragment(name: str, head_ref: str) -> str:
    """Execute the printing step up to and including its card line, and return
    its stdout.

    The fragment is the step's own `run:` block truncated at the card line —
    everything the workflow does to learn the head ref and read a card out of
    it, run under the workflow's shell flags with `gh pr view` shimmed.
    """
    job_id, step = printing_step(name)
    assert step is not None, f"{name} prints no `bureau-card:` line"
    lines = step["run"].splitlines()
    cut = next(i for i, line in enumerate(lines) if "bureau-card:" in line)
    script = _render("\n".join(lines[: cut + 1]))
    env = {k: _render(str(v)) for k, v in (step.get("env") or {}).items()}
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "gh").write_text(GH_SHIM)
        os.chmod(td / "gh", 0o755)
        out = td / "github_output"
        out.touch()
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
            cwd=td, capture_output=True, text=True, check=False,
            env={
                **os.environ, **env,
                "PATH": f"{td}:{os.environ['PATH']}",
                "GITHUB_OUTPUT": str(out),
                "GITHUB_REPOSITORY": PAYLOAD["github.repository"],
                "FAKE_BRANCH": head_ref,
            },
        )
        assert proc.returncode == 0, f"{name}: {proc.stdout}{proc.stderr}"
        return proc.stdout


def as_run_log(name: str, stdout: str) -> str:
    """Re-render a step's stdout the way `gh run view --log` renders it.

    One line per log line: the job name (a reusable workflow's job is reported
    under `<caller job> / <called job>`), the step name, the timestamp, then
    the text — the exact three-field shape `medic_retry._LOG_CARD` matches in.
    """
    job_id, step = printing_step(name)
    return "".join(
        f"call / {job_id}\t{step['name']}\t2026-09-20T09:14:0{i % 10}.1234567Z {line}\n"
        for i, line in enumerate(stdout.splitlines())
    )


class DiscoveryTest(unittest.TestCase):
    def test_the_population_is_not_empty(self):
        """A guard that discovers nothing passes for the wrong reason."""
        self.assertTrue(
            discovered(),
            "no reusable workflow reads a dispatched or comment-borne pull "
            "request number — either the pipeline changed shape or the "
            "discovery above stopped working",
        )

    def test_a_workflow_on_the_pull_requests_own_branch_is_not_in_scope(self):
        """The blind spot is a run recorded on the DEFAULT branch. agent-task
        runs on the card's own branch and resolves through `card_from_branch`
        exactly as it always has, so it owes nothing here."""
        self.assertNotIn("agent-task.yml", discovered())


class EveryDiscoveredWorkflowSaysItsCardTest(unittest.TestCase):
    """The card line lives in the REUSABLE workflow, never in the per-repo
    stub: the stubs are copies and this fix must reach every repo through the
    channel."""

    def test_each_one_prints_the_card_line(self):
        silent = [name for name in discovered() if printing_step(name)[1] is None]
        self.assertEqual(
            [], silent,
            "these reusable workflows can run for a pull request they are not "
            "on the branch of, and print no `bureau-card:` line — the medic "
            f"resolves no card for any of their runs: {silent}",
        )

    def test_the_card_is_read_off_the_head_ref_and_never_typed(self):
        """The same reading `linear-sync` and `card_from_branch` already do —
        a typed card would name whatever was typed."""
        for name in discovered():
            with self.subTest(workflow=name):
                _, step = printing_step(name)
                self.assertIsNotNone(step, f"{name} prints no card line")
                self.assertIn("headRefName", step["run"])
                self.assertIn("grep -oiE 'DRE-[0-9]+'", step["run"])

    def test_the_line_is_echoed_and_no_check_run_name_carries_it(self):
        """plan.yml's OTHER half — the job NAME — is deliberately not copied.
        Check-run names are load-bearing for the merge gate, the reconcile
        sweep and branch protection; renaming a job here renames the check."""
        for name in discovered():
            with self.subTest(workflow=name):
                _, step = printing_step(name)
                self.assertIsNotNone(step, f"{name} prints no card line")
                self.assertIn(CARD_ECHO, step["run"])
                for job_id, job in (_doc(name).get("jobs") or {}).items():
                    self.assertNotIn(
                        "bureau-card:", str(job.get("name") or ""),
                        f"{name}:{job_id} would rename its check run",
                    )


class TheMedicReadsWhatTheWorkflowPrintsTest(unittest.TestCase):
    """The executed half: the fragment runs, and the real resolver reads it."""

    def test_a_dispatched_run_on_a_card_branch_resolves_its_card(self):
        for name in discovered():
            with self.subTest(workflow=name):
                log = as_run_log(name, run_card_fragment(name, CARD_REF))
                self.assertIn("bureau-card: DRE-1234", log)
                self.assertEqual(
                    CARD, medic_retry.card_for_run("main", log),
                    f"{name} prints a line the medic cannot parse:\n{log}",
                )

    def test_a_head_ref_that_names_no_card_prints_no_line(self):
        """Absent stays absent — never a guessed card."""
        for name in discovered():
            with self.subTest(workflow=name):
                log = as_run_log(name, run_card_fragment(name, CARDLESS_REF))
                self.assertNotIn("bureau-card:", log)
                self.assertIsNone(medic_retry.card_for_run("main", log))


if __name__ == "__main__":
    unittest.main()
