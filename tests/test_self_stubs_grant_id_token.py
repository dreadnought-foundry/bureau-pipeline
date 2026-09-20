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


def _callers() -> dict[str, list[str]]:
    """{caller file: the trusted reusables it calls}, read off every `uses:`.

    The value is a LIST because one stub may call two of them; keeping only the
    last would hide a caller from the check below.
    """
    found: dict[str, list[str]] = {}
    paths = [*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]
    for path in sorted(paths):
        doc = yaml.safe_load(path.read_text())
        for job in (doc.get("jobs") or {}).values():
            uses = job.get("uses", "") if isinstance(job, dict) else ""
            for name in UPLOAD_TRUSTED_REUSABLES:
                if f"{PIPELINE}/.github/workflows/{name}@" in uses:
                    found.setdefault(path.name, [])
                    if name not in found[path.name]:
                        found[path.name].append(name)
    return found


def _id_token(doc: dict) -> str | None:
    """The stub's effective `id-token` grant.

    `permissions:` is a map in every stub here, but GitHub also accepts the
    `write-all` / `read-all` scalar shorthand — read it rather than raising
    AttributeError on a shape that is legal YAML for this key.
    """
    perms = doc.get("permissions")
    if isinstance(perms, dict):
        return perms.get("id-token")
    return "write" if perms == "write-all" else None


class SelfStubsGrantIdTokenTest(unittest.TestCase):
    def test_the_discovery_finds_the_stubs_it_exists_for(self):
        # Guard the guard: a discovery that finds nothing passes everything, and
        # one that silently narrows passes almost everything. The count is a
        # tripwire, not the source of truth — a sixth caller is meant to trip it
        # so whoever adds it grants the token too.
        callers = _callers()
        self.assertIn("self-agent-task.yml", callers)
        self.assertEqual(callers.get("pr-review.yml"), ["qa-review.yml"])
        self.assertEqual(
            len(callers), 5,
            f"discovery found {sorted(callers)}; this repo has five stubs calling "
            f"a trusted reusable. A new one needs `id-token: write` too — update "
            f"this count once it has it.",
        )

    def test_every_caller_of_an_uploading_workflow_grants_id_token(self):
        for stub, reusables in _callers().items():
            doc = yaml.safe_load((WORKFLOWS / stub).read_text())
            with self.subTest(stub=stub):
                self.assertEqual(
                    _id_token(doc),
                    "write",
                    f"{stub} calls {', '.join(reusables)}, which uploads the "
                    f"agent's log over OIDC, but does not grant `id-token: write` "
                    f"at the top level — the job can mint no token and the upload "
                    f"is a gap on every run",
                )

    def test_every_trusted_reusable_exists(self):
        # A rename would leave this list pointing at nothing, and the discovery
        # above would quietly stop covering that workflow's caller.
        for name in UPLOAD_TRUSTED_REUSABLES:
            with self.subTest(reusable=name):
                self.assertTrue((WORKFLOWS / name).is_file(), f"no {name} in this repo")


if __name__ == "__main__":
    unittest.main()
