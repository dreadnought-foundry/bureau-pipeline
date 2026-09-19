"""RED-first: the harness driver READS through the dispatch pool (DRE-4282).

The driver waits on the sandbox by polling, and every poll is billed to the
identity that asks (DRE-4132: ~150 requests a minute with four runs live, all
against the worker installation's one hour). Conditional reads cut what an
unchanged answer costs; this card moves what is still billed off the worker's
bucket onto whichever pool App has the most headroom.

The rule in `github_api.GitHub`'s docstring holds: WHICH identity performs an
ACTION is the thing under test, so it stays explicit. A read is not an action
anyone attributes — no scenario asserts who asked — so the worker client is
given a READER, and every GET it would have sent goes out as the reader
instead. Every write (branch, PR, comment, merge) is still the worker's, and
`current_token()` — the credential the agent scenarios clone and push with —
is still the worker's too.

What these pin:

  * `GitHub(token, reader=other)`: GETs (json and the raw log archive) go
    through `other`, writes through the client itself; each keeps its own
    spend ledger; the ETag memory lives where the reads are;
  * the driver builds the reader from `HARNESS_READER_TOKEN` with its own
    mid-run re-mint from `HARNESS_READER_APP_ID/_PRIVATE_KEY` — the selected
    App's key, threaded by harness.yml through the same pool map as the mint;
  * the run's `github-spend:` lines name the slot the reads rode on
    (`reader (pool slot N)`), and a run WITHOUT a reader token says, in one
    line, that reads rode the worker on slot 1 — never silently;
  * without a reader token the driver behaves exactly as before this card.

Run: python3 -m pytest tests/test_harness_reader_pool.py -v
"""

import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from harness import __main__ as harness_main  # noqa: E402
from harness import framework, github_api  # noqa: E402
from harness.github_api import GitHub  # noqa: E402

REPO = "dreadnought-foundry/bureau-harness"


class RecordingOpener:
    """Answers 200 `[]` (or bytes for the raw path) and records the bearer
    token every request carried, so a test can say WHO asked."""

    def __init__(self, label):
        self.label = label
        self.tokens = []
        self.methods = []

    def __call__(self, req):
        self.tokens.append(req.headers.get("Authorization"))
        self.methods.append(req.get_method())
        return 200, b"[]", {"ETag": f'"{self.label}"'}


class ReaderDelegationTest(unittest.TestCase):
    def setUp(self):
        self.worker_api = RecordingOpener("w")
        self.reader_api = RecordingOpener("r")
        self.reader = GitHub("ghs_reader", opener=self.reader_api)
        self.worker = GitHub("ghs_worker", opener=self.worker_api, reader=self.reader)

    def test_a_get_goes_out_as_the_reader(self):
        self.worker.list_comments(REPO, 7)
        self.assertEqual(self.reader_api.tokens, ["Bearer ghs_reader"])
        self.assertEqual(self.worker_api.tokens, [])

    def test_the_raw_log_archive_read_goes_out_as_the_reader_too(self):
        self.worker.request_bytes("GET", "/repos/x/actions/runs/1/logs")
        self.assertEqual(self.reader_api.methods, ["GET"])
        self.assertEqual(self.worker_api.methods, [])

    def test_a_write_stays_the_workers(self):
        self.worker.create_comment(REPO, 7, "hello")
        self.assertEqual(self.worker_api.tokens, ["Bearer ghs_worker"])
        self.assertEqual(self.worker_api.methods, ["POST"])
        self.assertEqual(self.reader_api.tokens, [])

    def test_the_clone_and_push_credential_is_still_the_workers(self):
        # AgentScenario.live_token → current_token(): the identity that
        # authors the PR is the thing under test there.
        self.assertEqual(self.worker.current_token(), "ghs_worker")

    def test_each_identity_keeps_its_own_ledger(self):
        self.worker.list_comments(REPO, 7)
        self.worker.list_comments(REPO, 7)  # 200 again from this fake: billed
        self.worker.create_comment(REPO, 7, "x")
        self.assertEqual(self.reader.spend(), {"billed": 2, "free": 0})
        self.assertEqual(self.worker.spend(), {"billed": 1, "free": 0})

    def test_without_a_reader_the_client_is_exactly_the_old_one(self):
        alone = GitHub("ghs_worker", opener=self.worker_api)
        alone.list_comments(REPO, 7)
        self.assertEqual(self.worker_api.tokens, ["Bearer ghs_worker"])


class _PollingScenario(framework.Scenario):
    """Asks the sandbox the same question five times, then writes once."""

    name = "polls_then_writes"

    def setup(self, ctx):
        pass

    def exercise(self, ctx):
        for _ in range(5):
            ctx.gh.list_comments(ctx.repo, 7)
        ctx.gh.create_comment(ctx.repo, 7, "probe")

    def verify(self, ctx):
        pass

    def cleanup(self, ctx):
        pass


def _drive(env, opener):
    """Run the driver's main() once over `env` with every GitHub client built
    on `opener`; returns (exit code, stdout)."""

    def client(token, **kwargs):
        return github_api.GitHub(token, opener=opener, **kwargs)

    out = io.StringIO()
    with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
        harness_main, "discover",
        lambda: {_PollingScenario.name: _PollingScenario()},
    ), mock.patch.object(harness_main, "GitHub", client), contextlib.redirect_stdout(out):
        code = harness_main.main(
            ["--repo", REPO, "--scenarios", _PollingScenario.name, "--run-id", "gha-1-1"]
        )
    return code, out.getvalue()


BASE_ENV = {
    "HARNESS_WORKER_TOKEN": "ghs-worker",
    "HARNESS_QA_TOKEN": "ghs-qa",
    "HARNESS_QA_LOGIN": "agent-bureau-qa-bot[bot]",
    "HARNESS_WAIT_DEADLINE_MINUTES": "0",
}


class DriverBuildsTheReaderTest(unittest.TestCase):
    def test_reads_ride_the_reader_and_the_spend_line_names_the_slot(self):
        api = RecordingOpener("x")
        code, out = _drive(
            {**BASE_ENV, "HARNESS_READER_TOKEN": "ghs-reader", "HARNESS_POOL_SLOT": "3"},
            api,
        )
        self.assertEqual(code, 0, out)
        # Five polls as the reader, one write as the worker.
        self.assertEqual(api.tokens.count("Bearer ghs-reader"), 5)
        self.assertEqual(api.tokens.count("Bearer ghs-worker"), 1)
        lines = [ln for ln in out.splitlines() if ln.startswith("github-spend:")]
        self.assertIn(
            "github-spend: reader (pool slot 3) 5 billed, 0 free (304 Not Modified)", lines
        )
        self.assertIn("github-spend: worker 1 billed, 0 free (304 Not Modified)", lines)

    def test_without_a_reader_token_reads_ride_the_worker_and_say_so(self):
        api = RecordingOpener("x")
        code, out = _drive(dict(BASE_ENV), api)
        self.assertEqual(code, 0, out)
        self.assertEqual(api.tokens.count("Bearer ghs-worker"), 6)
        self.assertNotIn("github-spend: reader", out)
        self.assertIn("reads ride the worker identity (pool slot 1)", out)

    def test_the_qa_note_names_the_identity_its_reads_actually_ride(self):
        # Critic finding on DRE-4282, round 1 (non-blocking 2). With
        # HARNESS_QA_TOKEN unset the qa client IS the worker client, and the
        # worker client now delegates its GETs to the reader — so those
        # check-runs reads go out as the pool App, not as the worker. The
        # note has to say which, because github_api.GitHub's rule is that
        # WHICH identity acts is explicit.
        degraded = {k: v for k, v in BASE_ENV.items() if k != "HARNESS_QA_TOKEN"}
        code, out = _drive(
            {**degraded, "HARNESS_READER_TOKEN": "ghs-reader", "HARNESS_POOL_SLOT": "3"},
            RecordingOpener("x"),
        )
        self.assertEqual(code, 0, out)
        self.assertIn("HARNESS_QA_TOKEN unset — check-runs reads use the reader", out)
        # And with no reader either, the old sentence is still the true one.
        code, plain = _drive(degraded, RecordingOpener("x"))
        self.assertEqual(code, 0, plain)
        self.assertIn(
            "HARNESS_QA_TOKEN unset — check-runs reads use the worker token", plain
        )

    def test_the_reader_reminits_from_the_selected_apps_key(self):
        minted = []

        def fake_mint(app_id, private_key_pem, repo):
            minted.append((app_id, private_key_pem, repo))
            return "ghs_fresh"

        supplier = harness_main.token_supplier(
            "reader", "4266538", "PEM3", REPO, mint=fake_mint, log=lambda *_: None
        )
        self.assertEqual(supplier(), "ghs_fresh")
        self.assertEqual(minted, [("4266538", "PEM3", REPO)])

    def test_the_driver_wires_the_readers_own_supplier(self):
        # The reader is built with a supplier from ITS credentials, not the
        # worker's: a re-mint from the worker's key would silently put the
        # late scenarios' reads back on slot 1.
        asked = []
        real = harness_main.token_supplier

        def spy(role, app_id, private_key_pem, repo, **kwargs):
            asked.append((role, app_id, private_key_pem, repo))
            return real(role, app_id, private_key_pem, repo, **kwargs)

        def client(token, **kwargs):
            return github_api.GitHub(token, opener=RecordingOpener("x"), **kwargs)

        env = {
            **BASE_ENV,
            "HARNESS_READER_TOKEN": "ghs-reader",
            "HARNESS_READER_APP_ID": "4266538",
            "HARNESS_READER_APP_PRIVATE_KEY": "PEM3",
            "HARNESS_WORKER_APP_ID": "3350400",
            "HARNESS_WORKER_APP_PRIVATE_KEY": "PEM1",
        }
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            harness_main, "discover",
            lambda: {_PollingScenario.name: _PollingScenario()},
        ), mock.patch.object(harness_main, "GitHub", client), mock.patch.object(
            harness_main, "token_supplier", spy
        ), contextlib.redirect_stdout(io.StringIO()):
            harness_main.main(
                ["--repo", REPO, "--scenarios", _PollingScenario.name, "--run-id", "gha-1-1"]
            )
        self.assertIn(("reader", "4266538", "PEM3", REPO), asked)
        self.assertIn(("worker", "3350400", "PEM1", REPO), asked)


if __name__ == "__main__":
    unittest.main()
