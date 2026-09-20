"""The self-hosting stubs grant `id-token: write` (DRE-4353).

The agent-log upload (DRE-4269) reaches our storage on a short-lived AWS login
GitHub mints per job, and a job may only ask for one when its workflow grants
`id-token: write`. A reusable workflow can never hold more permission than the
stub that calls it, so the grant has to be in the CALLER. bureau-pipeline builds
itself through its own stubs; without the grant there, an agent run on this repo
finds no token, records a gap, and — because the upload never fails the run —
looks shipped while uploading nothing.

Which callers need it is DISCOVERED from each workflow's `uses:` line, never
listed: the stub nobody remembered is exactly the one still missing the grant.

The six reusables are the ones the upload roles trust
(`BUREAU_PIPELINE_AGENT_WORKFLOWS` in agent-bureau's
`infra/lib/agent-log-stack.ts`, DRE-4343). That list lives in another repo and CI
checks out one, so it is a COPY here under the two-copies rule: change one,
change the other. `red-main-repair.yml` and `model-trial.yml` also run an agent
and are deliberately absent from both — nobody has reviewed them for upload.
"""

import unittest
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
PIPELINE = "dreadnought-foundry/bureau-pipeline"

UPLOAD_TRUSTED_REUSABLES = (
    "agent-task.yml",
    "agent-fix.yml",
    "qa-review.yml",
    "verify.yml",
    "plan.yml",
    "medic.yml",
)


def _callers() -> dict[str, str]:
    """{caller file: the trusted reusable it calls}, read off every `uses:`."""
    found: dict[str, str] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text())
        for job in (doc.get("jobs") or {}).values():
            uses = job.get("uses", "") if isinstance(job, dict) else ""
            for name in UPLOAD_TRUSTED_REUSABLES:
                if f"{PIPELINE}/.github/workflows/{name}@" in uses:
                    found[path.name] = name
    return found


class SelfStubsGrantIdTokenTest(unittest.TestCase):
    def test_the_discovery_finds_the_stubs_it_exists_for(self):
        # Guard the guard: a discovery that finds nothing passes everything.
        callers = _callers()
        self.assertIn("self-agent-task.yml", callers)
        self.assertEqual(callers.get("pr-review.yml"), "qa-review.yml")

    def test_every_caller_of_an_uploading_workflow_grants_id_token(self):
        for stub, reusable in _callers().items():
            doc = yaml.safe_load((WORKFLOWS / stub).read_text())
            with self.subTest(stub=stub):
                self.assertEqual(
                    (doc.get("permissions") or {}).get("id-token"),
                    "write",
                    f"{stub} calls {reusable}, which uploads the agent's log over "
                    f"OIDC, but does not grant `id-token: write` at the top level — "
                    f"the job can mint no token and the upload is a gap on every run",
                )

    def test_every_trusted_reusable_exists(self):
        # A rename would leave this list pointing at nothing, and the discovery
        # above would quietly stop covering that workflow's caller.
        for name in UPLOAD_TRUSTED_REUSABLES:
            with self.subTest(reusable=name):
                self.assertTrue((WORKFLOWS / name).is_file(), f"no {name} in this repo")


if __name__ == "__main__":
    unittest.main()
