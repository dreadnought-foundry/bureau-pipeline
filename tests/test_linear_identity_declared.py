"""The fleet consumers DECLARE whose Linear budget they spend (DRE-3321).

Since DRE-3172 the workspace has two non-human Linear users and Linear's
2,500-requests-per-hour limit is PER USER, so "the quota is exhausted" names
one of two buckets and a reader has to know which. The run says so itself: a
job-level `LINEAR_IDENTITY` beside the job that reads `secrets.LINEAR_API_KEY`,
inherited by every step in it, and printed as the last part of the two lines
`linear_ops` writes when the bucket runs dry.

DECLARED, not verified: the label cannot be checked against Linear from inside
a run without spending a request from the very budget in question
(`standards/vendor-boundaries.md` Q4). `scripts/check_linear_identities.py
check` remains the live proof that the keys are who the labels say; this file
is the proof that the fleet's three spenders say it at all.

Job level, not step level, on purpose: the spend table's three workflows carry
thirty-odd steps that read the key between them, and a per-step declaration is
thirty places for the next step to be added without one.
"""

import contextlib
import io
import json
import os
import subprocess  # nosec B404 — one fixed-arg call to the repo's own checker
import sys
import unittest
import urllib.error
from unittest import mock

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("REPO_SLUG", "test")
os.environ.setdefault("GH_TOKEN", "test")

import linear_ops  # noqa: E402
import reconcile  # noqa: E402

WORKFLOWS = os.path.join(ROOT, ".github", "workflows")
IDENTITIES = os.path.join(ROOT, "config", "linear-identities.json")

# The epic's spend table: the three fleet consumers that read the fleet key.
# Named here because the card names them — other workflows print `undeclared`
# until they declare, which is honest rather than wrong.
SPENDERS = ("reconcile.yml", "linear-sync.yml", "plan.yml")

KEY = "secrets.LINEAR_API_KEY"


def _workflow(name: str) -> dict:
    with open(os.path.join(WORKFLOWS, name), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _jobs_reading_the_key(doc: dict) -> list[tuple[str, dict]]:
    """Every job in `doc` whose body reads `secrets.LINEAR_API_KEY`, however
    deep — the question is which JOB spends the budget, not which step."""
    out = []
    for job_id, job in (doc.get("jobs") or {}).items():
        if KEY in yaml.safe_dump(job):
            out.append((job_id, job))
    return out


class DeclaredInTheWorkflowsTest(unittest.TestCase):
    def test_every_job_that_reads_the_fleet_key_declares_fleet(self):
        seen = 0
        for name in SPENDERS:
            doc = _workflow(name)
            jobs = _jobs_reading_the_key(doc)
            self.assertTrue(jobs, f"{name} reads no Linear key — the list is stale")
            for job_id, job in jobs:
                seen += 1
                env = job.get("env") or {}
                self.assertEqual(
                    env.get("LINEAR_IDENTITY"),
                    "fleet",
                    f"{name}:{job_id} spends the fleet's budget without saying so",
                )
        # Five jobs today: reconcile's sweep, linear-sync's card-done and
        # conflict-sweep, plan's plan and publish. Asserted as a floor so a
        # renamed job cannot make this test pass by finding nothing.
        self.assertGreaterEqual(seen, 5)

    def test_the_declaration_sits_on_the_job_so_no_step_carries_it(self):
        """No step is edited: the value is inherited. A step-level copy is the
        one that goes missing when the next step is added."""
        for name in SPENDERS:
            doc = _workflow(name)
            for job_id, job in (doc.get("jobs") or {}).items():
                for step in job.get("steps") or []:
                    self.assertNotIn(
                        "LINEAR_IDENTITY",
                        step.get("env") or {},
                        f"{name}:{job_id} declares the identity on a step",
                    )

    def test_the_word_is_one_the_declaration_carries(self):
        """`fleet` is not a string this pipeline invented — it is the `name`
        of an identity in config/linear-identities.json, and the seam accepts
        exactly the names that file declares."""
        with open(IDENTITIES, encoding="utf-8") as f:
            names = [row["name"] for row in json.load(f)["identities"]]
        self.assertIn("fleet", names)
        self.assertEqual(sorted(linear_ops.declared_identity_names()), sorted(names))

    def test_the_reconcile_call_sites_still_pass_their_arguments(self):
        """DRE-3042's guard, run as the build runs it: a job-level `env:` block
        is a new place a reconcile.py argument can come from, and this is the
        reader that would notice if it broke."""
        p = subprocess.run(  # nosec B603 — fixed args, no shell, repo-local script
            [sys.executable, os.path.join(ROOT, "scripts", "check_reconcile_env.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


RATELIMIT_BODY = (
    b'{"errors":[{"message":"Rate limit exceeded. Only 2500 requests are '
    b'allowed per 1 hour and you have made 2500 requests in the last hour.",'
    b'"extensions":{"type":"ratelimited","code":"RATELIMITED",'
    b'"statusCode":429,"userError":true}}]}'
)


class RateLimitedSweepNamesTheUserTest(unittest.TestCase):
    """The epic's fourth criterion, end to end: a sweep that receives
    RATELIMITED exits with one plain-English line naming the user whose budget
    is spent and when the window rolls."""

    def _sweep(self, identity: str | None):
        def _boom(*_a, **_k):
            raise urllib.error.HTTPError(
                linear_ops.API,
                400,
                "Bad Request",
                {"x-ratelimit-requests-reset": "1788651120000"},
                io.BytesIO(RATELIMIT_BODY),
            )

        env = {} if identity is None else {"LINEAR_IDENTITY": identity}
        err = io.StringIO()
        with mock.patch.dict(os.environ, env):
            if identity is None:
                os.environ.pop("LINEAR_IDENTITY", None)
            with mock.patch.object(linear_ops.urllib.request, "urlopen", _boom):
                with contextlib.redirect_stderr(err):
                    with self.assertRaises(SystemExit) as caught:
                        reconcile.run([])
        linear_ops._reset_budget_state()
        return caught.exception.code, err.getvalue()

    def test_a_rate_limited_sweep_exits_75_naming_the_user(self):
        code, err = self._sweep("fleet")
        self.assertEqual(code, reconcile.RATE_LIMITED_EXIT)
        self.assertIn("the fleet user's Linear quota is exhausted", err)
        # Still the plain-English "this is not a defect" the medic and the
        # operator read, and still the window's clock.
        self.assertIn("not a defect", err.lower())
        self.assertIn("16:32 PT", err)

    def test_an_undeclared_sweep_says_undeclared_rather_than_guessing(self):
        code, err = self._sweep(None)
        self.assertEqual(code, reconcile.RATE_LIMITED_EXIT)
        self.assertIn("the undeclared user's Linear quota is exhausted", err)
        self.assertNotIn("fleet", err)


if __name__ == "__main__":
    unittest.main()
