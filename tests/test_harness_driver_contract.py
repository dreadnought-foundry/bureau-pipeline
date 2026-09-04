"""RED-first tests: harness.yml refuses a driver that predates the
shared-sandbox isolation contract.

Observed on run 33903196184, red main. DRE-3075 made main's run and a PR's
proving run share the sandbox safely by scoping every sweep to its own
namespace — and that isolation is implemented ENTIRELY in the driver, which
`harness.yml` checks out at `github.event.pull_request.head.sha`: the pull
request's own head, whatever vintage it is. So the isolation is only as
strong as the OLDEST driver in flight, and nothing checked that.

The sequence, from the runs' own logs:

  18:32  main's `gate_paths` opens probe PR #957 and waits for the critic
  18:57  run 33907766668 (a PR lane, driver post-DRE-3075) sweeps and logs
         `sweep: PR #957 (dependabot/harness-main-…) belongs to another
         run — left` — #957 is open and correctly untouched
  19:22  run 33910802510 starts for PR #260, whose head (350aec9) forks
         from a7bfa52 and predates DRE-3075. Its sweep is the UNSCOPED
         one: it closes every open harness PR it finds, #957 among them
  19:42  main's wait ends `timed out after 4200s waiting for a critic
         comment on PR #957 (the second gate wake)`, and gate_paths'
         cleanup logs no `closed named PR #957` — the PR was already gone

Seventy of that run's seventy-six minutes were spent polling a closed PR,
and the failure named the sandbox's critic, which was healthy throughout.

`framework.probe_pr` (the previous repair) makes that wait END, quickly and
honestly. It does not stop the closure: main still fails, just sooner. The
cause is upstream of the wait — a driver too old to honour the isolation
must not be allowed at the shared sandbox at all.

Nothing in the checkout can enforce that, because the whole checkout is the
stale thing. `harness.yml` is the one exception: on a `pull_request` event
GitHub runs the workflow file from the merge ref, so the workflow is always
current even when the driver it checks out is months old. The guard
therefore lives in the workflow, reads the contract version out of the
checked-out driver, and fails the run BEFORE a sandbox credential is minted.

These tests must FAIL against a harness.yml with no guard step, and PASS
after.

Run: python3 -m pytest tests/test_harness_driver_contract.py -v
"""

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from harness import framework  # noqa: E402

WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "harness.yml"
)

#: Where the driver declares which isolation contract it implements. The
#: guard reads this path out of the checkout, so the tests drive it the
#: same way.
DRIVER_SOURCE = Path("scripts") / "harness" / "framework.py"

#: What a driver older than DRE-3075 looks like: the module is there, the
#: declaration is not.
PRE_ISOLATION_DRIVER = textwrap.dedent(
    '''
    """A driver from before the namespaced sweep."""

    STALE_LEFTOVER_SECONDS = 6 * 60 * 60
    '''
).lstrip()


def _doc():
    assert WORKFLOW.is_file(), f"missing {WORKFLOW.name}"
    return yaml.safe_load(WORKFLOW.read_text())


def _steps(doc):
    return doc["jobs"]["harness"].get("steps") or []


def _guard_step(doc):
    """The step whose `run:` reads the driver's contract declaration."""
    for step in _steps(doc):
        if "SANDBOX_ISOLATION_CONTRACT" in (step.get("run") or ""):
            return step
    raise AssertionError(
        "no step in harness.yml checks the driver's sandbox-isolation "
        "contract — an unscoped sweep from a stale PR head can still close "
        "main's live probe PRs (run 33903196184)"
    )


def _index(doc, predicate):
    for i, step in enumerate(_steps(doc)):
        if predicate(step):
            return i
    return None


def _run_guard(driver_source, required=None):
    """Execute the workflow's own guard script against a driver tree we
    build, and return the CompletedProcess. This is the step verbatim — not
    a re-implementation — so the tests cannot drift from what CI runs."""
    step = _guard_step(_doc())
    env = dict(os.environ)
    env.update({k: str(v) for k, v in (step.get("env") or {}).items()})
    if required is not None:
        env["REQUIRED_CONTRACT"] = str(required)

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / DRIVER_SOURCE
        if driver_source is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(driver_source)
        return subprocess.run(
            ["bash", "-e", "-c", step["run"]],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
        )


class DriverContractDeclarationTest(unittest.TestCase):
    """The driver says which isolation it implements, in one place."""

    def test_the_driver_declares_its_isolation_contract(self):
        self.assertIsInstance(
            getattr(framework, "SANDBOX_ISOLATION_CONTRACT", None),
            int,
            "the driver must declare the isolation contract it implements, "
            "or the workflow has nothing to check it against",
        )

    def test_the_shipped_driver_satisfies_the_workflows_own_requirement(self):
        # Bumping the workflow's floor without bumping the driver would make
        # every run of this repo's own main refuse itself. Caught here, in
        # this build, rather than on the next merge to main.
        required = int((_guard_step(_doc()).get("env") or {})["REQUIRED_CONTRACT"])
        self.assertGreaterEqual(framework.SANDBOX_ISOLATION_CONTRACT, required)


class GuardPlacementTest(unittest.TestCase):
    """Where the guard has to sit for it to be worth having."""

    def test_the_guard_runs_before_any_sandbox_credential_is_minted(self):
        # A driver that will not be allowed to touch the sandbox must never
        # be handed a token for it.
        doc = _doc()
        guard = _index(doc, lambda s: s is _guard_step(doc))
        mint = _index(
            doc, lambda s: "create-github-app-token" in (s.get("uses") or "")
        )
        self.assertIsNotNone(mint, "no App-token mint step")
        self.assertLess(guard, mint)

    def test_the_guard_runs_after_the_checkout_it_judges(self):
        doc = _doc()
        guard = _index(doc, lambda s: s is _guard_step(doc))
        checkout = _index(
            doc, lambda s: (s.get("uses") or "").startswith("actions/checkout")
        )
        self.assertIsNotNone(checkout, "no checkout step")
        self.assertLess(checkout, guard)

    def test_the_guard_runs_before_the_driver_does(self):
        doc = _doc()
        guard = _index(doc, lambda s: s is _guard_step(doc))
        driver = _index(doc, lambda s: "python3 -m harness" in (s.get("run") or ""))
        self.assertIsNotNone(driver, "no step runs the harness driver")
        self.assertLess(guard, driver)


class GuardBehaviourTest(unittest.TestCase):
    """The guard's own logic, executed exactly as the workflow runs it."""

    def test_the_shipped_driver_passes_its_own_guard(self):
        result = _run_guard(
            (Path(__file__).resolve().parents[1] / DRIVER_SOURCE).read_text()
        )
        self.assertEqual(
            result.returncode, 0, f"{result.stdout}\n{result.stderr}"
        )

    def test_a_driver_from_before_the_namespaced_sweep_is_refused(self):
        # PR #260's head, 2026-09-04: a driver whose sweep closes every
        # harness PR it finds, including a concurrent run's live probes.
        result = _run_guard(PRE_ISOLATION_DRIVER)
        self.assertNotEqual(
            result.returncode, 0,
            "a pre-DRE-3075 driver was allowed at the shared sandbox",
        )
        self.assertIn("rebase", (result.stdout + result.stderr).lower())

    def test_a_driver_below_the_required_contract_is_refused(self):
        # The forward case: a future isolation change bumps the floor, and
        # every in-flight branch below it is turned away rather than let
        # loose on the shared sandbox.
        result = _run_guard(
            (Path(__file__).resolve().parents[1] / DRIVER_SOURCE).read_text(),
            required=framework.SANDBOX_ISOLATION_CONTRACT + 1,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_a_missing_driver_is_refused_rather_than_assumed_current(self):
        # Fail closed: "I could not read the driver" is not "the driver is
        # fine". The sandbox is shared, and the cost of guessing wrong is
        # another run's live fixtures.
        self.assertNotEqual(_run_guard(None).returncode, 0)

    def test_the_refusal_names_the_run_it_would_break(self):
        # The message is read by whoever has to rebase, and the reason it
        # matters is not local to their PR.
        out = _run_guard(PRE_ISOLATION_DRIVER)
        self.assertIn("sandbox", (out.stdout + out.stderr).lower())


if __name__ == "__main__":
    unittest.main()
