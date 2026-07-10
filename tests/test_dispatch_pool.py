"""Dispatch-pool worker selection (DRE-2013) — selector unit tests.

Four worker-identical GitHub Apps (agent-bureau-bot + -2/-3/-4) each carry an
independent 5,000 req/hr REST quota bucket. scripts/dispatch_pool.py picks
WHICH app the worker-token mint should use, per run, from the secrets/env
convention alone (BUREAU_APP_ID[_N]); it never sees private keys and only
outputs a slot number.

Card acceptance criteria exercised here (each test cites its row):
  * "N discovered dynamically — never hardcode the count; absent pairs just
    shrink the pool"
  * "choose max core.remaining"
  * "tie-break determinism"
  * "on read failure for some/all candidates, deterministic fallback by
    hashing a provided key (card id / run id) across the READABLE pool, else
    across all configured"
  * "a repo without the new secrets ... must behave exactly as today (original
    app only) — no failures, one log line"
  * "the script only OUTPUTS WHICH N to use (never touch or print key
    material)"

All readings are injected — no real network in tests.
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import dispatch_pool  # noqa: E402

SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "dispatch_pool.py"
)


def run_cli(env: dict) -> subprocess.CompletedProcess:
    """Run the CLI exactly as agent-task.yml does (select subcommand)."""
    full_env = {"PATH": os.environ.get("PATH", "")}
    full_env.update(env)
    return subprocess.run(
        [sys.executable, SCRIPT, "select"],
        env=full_env,
        capture_output=True,
        text=True,
        timeout=30,
    )


class PoolDiscoveryTest(unittest.TestCase):
    """Card: 'env pairs BUREAU_APP_ID[_N]/BUREAU_APP_PRIVATE_KEY[_N], N
    discovered dynamically — never hardcode the count'."""

    def test_discovers_pool_dynamically_from_env(self):
        # A gap (no _3) and an out-of-range slot (_7) must both work: the
        # pool is whatever the env advertises, not a hardcoded 1..4.
        env = {
            "BUREAU_APP_ID": "111",
            "BUREAU_APP_ID_2": "222",
            "BUREAU_APP_ID_7": "777",
            "BUREAU_APP_IDENTITY": "not-a-slot",  # must not match
            "SOME_OTHER_VAR": "x",
        }
        self.assertEqual(dispatch_pool.configured_slots(env), [1, 2, 7])

    def test_absent_pairs_shrink_the_pool(self):
        # Card: 'absent pairs just shrink the pool' — empty string counts
        # as absent (an unset secret renders as '' in workflow env).
        env = {
            "BUREAU_APP_ID": "111",
            "BUREAU_APP_ID_2": "",
            "BUREAU_APP_ID_3": "333",
        }
        self.assertEqual(dispatch_pool.configured_slots(env), [1, 3])


class ChooseTest(unittest.TestCase):
    def test_picks_max_core_remaining(self):
        # Card: 'choose max core.remaining'.
        slot, reason = dispatch_pool.choose(
            {1: 100, 2: 4500, 3: 200}, key="DRE-2013"
        )
        self.assertEqual(slot, 2)
        self.assertEqual(reason, "max-remaining")

    def test_tie_break_is_deterministic_and_spreads(self):
        # Card: 'tie-break determinism' — the same key always resolves the
        # same slot; different keys spread across the tied slots.
        readings = {1: 5000, 2: 5000, 3: 10}
        first = dispatch_pool.choose(readings, key="DRE-1")[0]
        for _ in range(5):
            self.assertEqual(dispatch_pool.choose(readings, key="DRE-1")[0], first)
        picks = {
            dispatch_pool.choose(readings, key=f"DRE-{i}")[0] for i in range(50)
        }
        self.assertEqual(picks, {1, 2}, "hash must spread over the TIED slots only")

    def test_partial_read_failure_hashes_across_readable_pool(self):
        # Card: 'on read failure for some ... candidates, deterministic
        # fallback by hashing a provided key ... across the READABLE pool'.
        readings = {1: 5000, 2: None, 3: 100}
        slot, reason = dispatch_pool.choose(readings, key="DRE-2013")
        self.assertEqual(reason, "hash-readable")
        self.assertIn(slot, (1, 3), "unreadable slot 2 must never be picked")
        self.assertEqual(slot, dispatch_pool.hash_pick("DRE-2013", [1, 3]))
        # Deterministic: repeat calls agree.
        self.assertEqual(slot, dispatch_pool.choose(readings, key="DRE-2013")[0])

    def test_all_read_failures_hash_across_all_configured(self):
        # Card: '... else across all configured'.
        readings = {1: None, 2: None, 3: None}
        slot, reason = dispatch_pool.choose(readings, key="DRE-2013")
        self.assertEqual(reason, "hash-all")
        self.assertEqual(slot, dispatch_pool.hash_pick("DRE-2013", [1, 2, 3]))
        picks = {
            dispatch_pool.choose(readings, key=f"DRE-{i}")[0] for i in range(50)
        }
        self.assertEqual(picks, {1, 2, 3}, "hash must spread across all configured")

    def test_no_key_falls_back_to_lowest_slot(self):
        # No key (missing BUREAU_POOL_KEY) must still be deterministic.
        self.assertEqual(dispatch_pool.choose({1: None, 2: None}, key="")[0], 1)
        self.assertEqual(dispatch_pool.choose({1: 5000, 2: 5000}, key=None)[0], 1)


class SingleAppPoolTest(unittest.TestCase):
    """Card AC 3: 'a repo without the new secrets (empty BUREAU_APP_ID_2 etc.)
    must behave exactly as today (original app only) — no failures, one log
    line'."""

    def test_single_app_pool_short_circuits_without_any_reads(self):
        def boom(_token):  # pragma: no cover - fails the test if reached
            raise AssertionError("single-app pool must not read /rate_limit")

        slot, reason = dispatch_pool.select(
            env={"BUREAU_APP_ID": "111", "BUREAU_APP_ID_2": ""}, reader=boom
        )
        self.assertEqual((slot, reason), (1, "single-app"))

    def test_cli_single_app_no_failures_one_log_line(self):
        result = run_cli({"BUREAU_APP_ID": "111"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("n=1", result.stdout.splitlines())
        log_lines = [
            l for l in result.stderr.splitlines() if l.startswith("dispatch-pool:")
        ]
        self.assertEqual(len(log_lines), 1, "exactly one log line on degradation")

    def test_cli_empty_env_never_crashes_the_build(self):
        # Even with NOTHING configured the selector must exit 0 and route to
        # the original app — a selector failure must never block a build.
        result = run_cli({})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("n=1", result.stdout.splitlines())


class CliOutputContractTest(unittest.TestCase):
    """Card: 'the script only OUTPUTS WHICH N to use (never touch or print key
    material)'. stdout is appended verbatim to $GITHUB_OUTPUT by the workflow,
    so it must contain ONLY key=value output lines."""

    ENV = {
        "BUREAU_APP_ID": "111",
        "BUREAU_APP_ID_2": "222",
        "BUREAU_APP_ID_3": "333",
        "BUREAU_APP_PRIVATE_KEY": "PRIVATE-KEY-MATERIAL",
        "BUREAU_APP_PRIVATE_KEY_2": "PRIVATE-KEY-MATERIAL-2",
        "BUREAU_POOL_KEY": "DRE-2013",
        # Injected readings so the CLI is exercised end-to-end w/o network.
        "BUREAU_FAKE_RATE_LIMITS": json.dumps({"1": 10, "2": 4999, "3": 20}),
    }

    def test_cli_selects_max_via_injected_readings(self):
        result = run_cli(dict(self.ENV))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("n=2", result.stdout.splitlines())

    def test_cli_prints_only_output_lines_and_no_material(self):
        result = run_cli(dict(self.ENV))
        for line in result.stdout.splitlines():
            self.assertRegex(
                line, r"^(n|reason)=\S+$",
                "stdout feeds $GITHUB_OUTPUT — output lines only",
            )
        blob = result.stdout + result.stderr
        self.assertNotIn("PRIVATE-KEY-MATERIAL", blob)
        for app_id in ("111", "222", "333"):
            self.assertNotIn(app_id, result.stdout, "never print app ids as output")

    def test_cli_all_reads_failed_hash_fallback(self):
        env = dict(self.ENV)
        env["BUREAU_FAKE_RATE_LIMITS"] = json.dumps(
            {"1": None, "2": None, "3": None}
        )
        result = run_cli(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = dispatch_pool.hash_pick("DRE-2013", [1, 2, 3])
        self.assertIn(f"n={expected}", result.stdout.splitlines())


class RateLimitParseTest(unittest.TestCase):
    def test_parse_remaining_reads_core_remaining(self):
        body = json.dumps(
            {"resources": {"core": {"limit": 5000, "remaining": 4321}}}
        )
        self.assertEqual(dispatch_pool.parse_remaining(body), 4321)

    def test_parse_remaining_garbage_is_unreadable(self):
        self.assertIsNone(dispatch_pool.parse_remaining("not json"))
        self.assertIsNone(dispatch_pool.parse_remaining(json.dumps({"x": 1})))


if __name__ == "__main__":
    unittest.main()
