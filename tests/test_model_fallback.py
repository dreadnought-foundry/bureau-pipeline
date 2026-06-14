"""Tests for model selection: ordered preference ladder with availability
detection (DRE-1490), and the preserved is_error heartbeat (DRE-1354).

Background
----------
DRE-1354 pinned ONE model per role as a fixed 2-tuple (engineer Opus→Fable,
planner Fable→Opus) and switched to the alternate on an is_error death. But the
fallback target was a DEAD model: `claude-fable-5` returns HTTP 404 on all our
subscriptions (2026-06-14), so failures routed INTO a 404 wall — the planner's
primary IS Fable (404 on the first attempt) and the engineer's error-retry
bounced to Fable→404→dead. Good cards hit needs-human holds.

DRE-1490 replaces the per-role pair with a SINGLE ordered preference ladder
(best→worst: Fable, Opus, Sonnet) shared by both roles. `select` walks the
ladder top→bottom and returns the first AVAILABLE model, where availability is
probed at runtime (a minimal /v1/messages call): a 404 / not-found means
UNAVAILABLE (skip); ANY other response — including 429 (rate-limited) — means
AVAILABLE (choose it). Results are cached with a short TTL so we don't probe on
every dispatch, and auto-recovery is free: when Fable stops 404ing the next
probe after the TTL sees it available and `select` returns it again.

These tests drive the ladder walk with a STUBBED availability function — no real
network. The is_error heartbeat helpers (attempt_marker/error_marker) and the
hold-cap regression (dead_run.py) are preserved and re-asserted here.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import model_fallback as mf  # noqa: E402

FABLE = "claude-fable-5"
OPUS = "claude-opus-4-8"
SONNET = "claude-sonnet-4-6"


def fixed_clock(t):
    """A deterministic monotonic clock for cache-TTL tests."""
    return lambda: t[0]


class LadderShapeTest(unittest.TestCase):
    def test_ladder_is_best_first(self):
        # Best → worst. The ladder is the contract; both roles share it.
        self.assertEqual(mf.LADDER, [FABLE, OPUS, SONNET])

    def test_ladder_entries_are_all_known_models(self):
        self.assertTrue(set(mf.LADDER) <= mf.KNOWN_MODELS)


class AvailabilityClassificationTest(unittest.TestCase):
    """A 404 means gone (skip); anything else — including 429 — means present."""

    def test_404_is_unavailable(self):
        self.assertFalse(mf.classify_available(404))
        self.assertFalse(mf.classify_available(404, "not_found_error"))

    def test_429_is_available(self):
        # Throttling != gone; the existing transient retry handles 429s.
        self.assertTrue(mf.classify_available(429))

    def test_200_is_available(self):
        self.assertTrue(mf.classify_available(200))

    def test_400_and_500_are_available(self):
        # Any non-404 status means the model exists for us.
        self.assertTrue(mf.classify_available(400))
        self.assertTrue(mf.classify_available(500))

    def test_not_found_error_string_is_unavailable_even_without_status(self):
        self.assertFalse(mf.classify_available(None, "model: not_found_error"))
        self.assertFalse(mf.classify_available(None, "model not available, use Opus"))


class SelectLadderTest(unittest.TestCase):
    """select() walks the ladder and returns the first available model, for BOTH
    roles, driven by a stubbed availability function (no network)."""

    def setUp(self):
        mf.clear_availability_cache()

    def tearDown(self):
        mf.clear_availability_cache()

    def test_fable_unavailable_returns_opus_for_engineer(self):
        avail = {FABLE: False, OPUS: True, SONNET: True}
        self.assertEqual(
            mf.select("engineer", probe=lambda m: avail[m]), OPUS
        )

    def test_fable_unavailable_returns_opus_for_planner(self):
        # No role hardcodes a model — both walk the same ladder.
        avail = {FABLE: False, OPUS: True, SONNET: True}
        self.assertEqual(
            mf.select("planner", probe=lambda m: avail[m]), OPUS
        )

    def test_all_available_returns_fable_best_first(self):
        avail = {FABLE: True, OPUS: True, SONNET: True}
        self.assertEqual(mf.select("engineer", probe=lambda m: avail[m]), FABLE)
        self.assertEqual(mf.select("planner", probe=lambda m: avail[m]), FABLE)

    def test_fable_and_opus_unavailable_returns_sonnet(self):
        avail = {FABLE: False, OPUS: False, SONNET: True}
        self.assertEqual(mf.select("engineer", probe=lambda m: avail[m]), SONNET)

    def test_fable_never_returned_while_it_404s(self):
        avail = {FABLE: False, OPUS: True, SONNET: True}
        for role in ("engineer", "planner"):
            self.assertNotEqual(mf.select(role, probe=lambda m: avail[m]), FABLE)

    def test_all_unavailable_falls_through_to_last_known_good(self):
        # Degrade safely: if NOTHING probes available, never block a build —
        # fall through to the last (lowest) known-good model rather than return
        # nothing or a model just confirmed gone.
        avail = {FABLE: False, OPUS: False, SONNET: False}
        chosen = mf.select("engineer", probe=lambda m: avail[m])
        self.assertEqual(chosen, SONNET)
        self.assertIn(chosen, mf.LADDER)

    def test_probe_exception_treated_as_inconclusive_falls_through(self):
        # A probe that raises (network error/timeout) must not block the build
        # and must not return a model just confirmed 404. Here Fable raises
        # (inconclusive → skip) and Opus is available → Opus.
        def probe(m):
            if m == FABLE:
                raise TimeoutError("probe network error")
            return True
        self.assertEqual(mf.select("engineer", probe=probe), OPUS)


class CachingTest(unittest.TestCase):
    """Availability is cached with a short TTL so we don't probe on every
    dispatch; after the TTL a higher-ranked model that recovered is re-selected
    (auto-recovery)."""

    def setUp(self):
        mf.clear_availability_cache()

    def tearDown(self):
        mf.clear_availability_cache()

    def test_probe_not_called_repeatedly_within_ttl(self):
        calls = []

        def probe(m):
            calls.append(m)
            return {FABLE: False, OPUS: True, SONNET: True}[m]

        t = [1000.0]
        # First select: Fable probed (False) then Opus probed (True) → 2 calls.
        self.assertEqual(
            mf.select("engineer", probe=probe, clock=fixed_clock(t)), OPUS
        )
        first = list(calls)
        self.assertIn(FABLE, first)
        self.assertIn(OPUS, first)
        # Second select within the TTL: nothing new probed — served from cache.
        self.assertEqual(
            mf.select("planner", probe=probe, clock=fixed_clock(t)), OPUS
        )
        self.assertEqual(calls, first, "probe re-called within TTL window")

    def test_auto_recovery_after_ttl_when_higher_model_returns(self):
        state = {FABLE: False, OPUS: True, SONNET: True}

        def probe(m):
            return state[m]

        t = [1000.0]
        # Fable 404s → Opus chosen.
        self.assertEqual(
            mf.select("engineer", probe=probe, clock=fixed_clock(t)), OPUS
        )
        # Fable comes back online.
        state[FABLE] = True
        # Still within TTL: cached "Fable unavailable" → still Opus.
        self.assertEqual(
            mf.select("engineer", probe=probe, clock=fixed_clock(t)), OPUS
        )
        # Advance past the TTL: next probe sees Fable available → Fable.
        t[0] += mf.AVAILABILITY_TTL_SECONDS + 1
        self.assertEqual(
            mf.select("engineer", probe=probe, clock=fixed_clock(t)), FABLE
        )

    def test_clear_cache_forces_reprobe(self):
        calls = []

        def probe(m):
            calls.append(m)
            return m == OPUS  # only Opus available

        t = [0.0]
        mf.select("engineer", probe=probe, clock=fixed_clock(t))
        n = len(calls)
        mf.clear_availability_cache()
        mf.select("engineer", probe=probe, clock=fixed_clock(t))
        self.assertGreater(len(calls), n, "clear_availability_cache did not reprobe")


class HeartbeatMarkersPreservedTest(unittest.TestCase):
    """The DRE-1354 'which model was used' heartbeat + is_error markers stay —
    the workflows still write attempt_marker/error_marker and read them back."""

    def test_attempt_marker_roundtrip(self):
        self.assertIn(OPUS, mf.attempt_marker(OPUS))
        self.assertTrue(mf.attempt_marker(OPUS).startswith(mf.MARKER_PREFIX))

    def test_error_marker_roundtrip(self):
        self.assertIn(FABLE, mf.error_marker(FABLE))
        self.assertTrue(mf.error_marker(FABLE).startswith(mf.ERROR_MARKER_PREFIX))

    def test_last_error_model_reads_latest_known_marker(self):
        bodies = [mf.error_marker(FABLE), mf.attempt_marker(OPUS), mf.error_marker(OPUS)]
        self.assertEqual(mf.last_error_model(bodies), OPUS)

    def test_unknown_model_in_marker_is_ignored(self):
        self.assertIsNone(mf.last_error_model([mf.error_marker("gpt-9")]))

    def test_role_from_labels(self):
        self.assertEqual(mf._role_from_labels(["agent:planner"]), "planner")
        self.assertEqual(mf._role_from_labels(["agent:engineer"]), "engineer")


class IsErrorHoldCapRegressionTest(unittest.TestCase):
    """DRE-1354 contract preserved: an is_error death still counts toward the
    shared hold cap (no 18× loops). This lives in dead_run.decide()."""

    def test_is_error_death_requeues_under_cap(self):
        import dead_run

        d = dead_run.decide(0, is_error=True, error_model=OPUS)
        self.assertEqual(d.action, "requeue")

    def test_is_error_death_holds_at_cap(self):
        import dead_run

        d = dead_run.decide(dead_run.REQUEUE_CAP, is_error=True, error_model=OPUS)
        self.assertEqual(d.action, "hold")


class CliTest(unittest.TestCase):
    """CLI select walks the ladder using a REAL probe by default — but tests
    inject a stub probe via the BUREAU_FAKE_AVAILABLE env so no network is hit."""

    SCRIPT = os.path.join(
        os.path.dirname(__file__), "..", "scripts", "model_fallback.py"
    )

    def _select(self, role, fake_available):
        env = dict(os.environ)
        # JSON map model->bool consumed by the CLI's test hook.
        env["BUREAU_FAKE_AVAILABLE"] = json.dumps(fake_available)
        return subprocess.run(
            [sys.executable, self.SCRIPT, "select", role],
            capture_output=True, text=True, env=env,
        ).stdout.strip()

    def test_cli_select_skips_unavailable_fable(self):
        self.assertEqual(
            self._select("engineer", {FABLE: False, OPUS: True, SONNET: True}),
            OPUS,
        )

    def test_cli_select_returns_fable_when_available(self):
        self.assertEqual(
            self._select("planner", {FABLE: True, OPUS: True, SONNET: True}),
            FABLE,
        )

    def test_cli_role_of_labels(self):
        def role_of(labels):
            buf = io.StringIO()
            with redirect_stdout(buf):
                mf.main(["role-of", labels])
            return buf.getvalue().strip()

        self.assertEqual(role_of("agent:planner,repo:atlas"), "planner")
        self.assertEqual(role_of("agent:engineer,size:m"), "engineer")
        self.assertEqual(role_of("repo:atlas"), "engineer")


class AgentsRegistryAlignment(unittest.TestCase):
    """The ladder must agree with agents.yaml so the console roster and the
    runtime selection never drift (the registry contract test, DRE-1335)."""

    def _agents(self):
        import yaml

        root = os.path.join(os.path.dirname(__file__), "..")
        with open(os.path.join(root, "agents.yaml")) as f:
            return {a["name"]: a for a in yaml.safe_load(f)["agents"]}

    def test_engineer_and_planner_ladder_matches_registry(self):
        agents = self._agents()
        for role in ("engineer", "planner"):
            ladder_models = [step["model"] for step in agents[role].get("ladder", [])]
            self.assertEqual(
                ladder_models, mf.LADDER,
                f"{role}: agents.yaml ladder {ladder_models} != mf.LADDER {mf.LADDER}",
            )


if __name__ == "__main__":
    unittest.main()
