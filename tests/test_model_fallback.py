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
OPUS = "claude-opus-5"
SONNET = "claude-sonnet-4-6"
# Rotated OUT of the ladder (Opus 5 replaced it) but still recognizable, so a
# death marker stamped before the rotation keeps its attribution.
RETIRED_OPUS = "claude-opus-4-8"


def fixed_clock(t):
    """A deterministic monotonic clock for cache-TTL tests."""
    return lambda: t[0]


class LadderShapeTest(unittest.TestCase):
    def test_ladder_is_best_first(self):
        # Best → worst. The ladder is the contract; both roles share it.
        self.assertEqual(mf.LADDER, [FABLE, OPUS, SONNET])

    def test_ladder_entries_are_all_known_models(self):
        self.assertTrue(set(mf.LADDER) <= mf.KNOWN_MODELS)

    def test_retired_model_stays_recognizable_but_unselectable(self):
        # A card dispatched before the Opus 5 rotation can still carry
        # `model-error: claude-opus-4-8`. If the retired id stopped being a
        # KNOWN model, last_error_model() would silently return None and the
        # console would lose attribution for that death — so it stays known
        # while being removed from the selectable ladder.
        self.assertIn(RETIRED_OPUS, mf.KNOWN_MODELS)
        self.assertNotIn(RETIRED_OPUS, mf.LADDER)
        self.assertEqual(
            mf.last_error_model([mf.error_marker(RETIRED_OPUS)]), RETIRED_OPUS
        )


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


class CallerSuppliedLadderTest(unittest.TestCase):
    """`select(ladder=...)` walks a caller-supplied order instead of the module
    LADDER.

    Folded up from the console's vendored copy of this module, which had carried
    it as its ONE intentional local delta. The console reads the ladder out of
    bureau-pipeline/agents.yaml and needs THIS walk to drive it, so the YAML
    stays the single source of order while this function stays the single source
    of algorithm. Keeping the kwarg only downstream meant the copy could never be
    compared to upstream byte-for-byte, so drift in the copy stayed invisible —
    which is exactly how it ended up three deltas deep instead of one.

    The kwarg is additive: omitting it must behave exactly as before."""

    def setUp(self):
        mf.clear_availability_cache()

    def tearDown(self):
        mf.clear_availability_cache()

    def test_supplied_ladder_drives_the_walk(self):
        # Reverse order: Sonnet is best here, so it wins despite Fable being up.
        self.assertEqual(
            mf.select("engineer", probe=lambda m: True,
                      ladder=[SONNET, OPUS, FABLE]),
            SONNET,
        )

    def test_supplied_ladder_still_skips_a_confirmed_404(self):
        avail = {SONNET: False, OPUS: True, FABLE: True}
        self.assertEqual(
            mf.select("engineer", probe=lambda m: avail[m],
                      ladder=[SONNET, OPUS, FABLE]),
            OPUS,
        )

    def test_supplied_ladder_falls_through_to_its_own_last_entry(self):
        # Not LADDER[-1] — the caller's last entry, or the console would fall
        # back to a model outside the ladder it actually asked for.
        self.assertEqual(
            mf.select("engineer", probe=lambda m: False,
                      ladder=[SONNET, OPUS, FABLE]),
            FABLE,
        )

    def test_omitting_the_kwarg_is_unchanged(self):
        self.assertEqual(mf.select("engineer", probe=lambda m: True), mf.LADDER[0])

    def test_empty_or_none_ladder_falls_back_to_the_module_ladder(self):
        for empty in ([], None):
            with self.subTest(ladder=empty):
                mf.clear_availability_cache()
                self.assertEqual(
                    mf.select("engineer", probe=lambda m: True, ladder=empty),
                    mf.LADDER[0],
                )

    def test_a_bare_string_is_not_walked_character_by_character(self):
        # A YAML scalar (`ladder: claude-opus-5`) instead of a sequence. A str is
        # truthy and iterable, so an unguarded walk selects a single LETTER as
        # the model id and hands it to the run — silently. Fall back instead.
        for probe in (lambda m: True, lambda m: False):
            with self.subTest(probe=probe):
                mf.clear_availability_cache()
                got = mf.select("engineer", probe=probe, ladder=OPUS)
                self.assertIn(got, mf.LADDER, f"selected {got!r}, not a real model")

    def test_raw_agents_yaml_ladder_shape_is_accepted(self):
        # agents.yaml stores `ladder:` as a list of {model:, reason:} mappings.
        # The docstring points callers at that file, so this shape must work
        # rather than raise — select() is contracted never to block a build.
        raw = [{"model": SONNET, "reason": "x"}, {"model": OPUS, "reason": "y"}]
        self.assertEqual(
            mf.select("engineer", probe=lambda m: True, ladder=raw), SONNET
        )

    def test_unusable_entries_are_dropped_and_an_empty_result_falls_back(self):
        mf.clear_availability_cache()
        self.assertEqual(
            mf.select("engineer", probe=lambda m: True, ladder=[None, 42, {}]),
            mf.LADDER[0],
        )
        mf.clear_availability_cache()
        self.assertEqual(
            mf.select("engineer", probe=lambda m: True, ladder=[None, SONNET]),
            SONNET,
        )

    def test_a_generator_ladder_is_materialised_once(self):
        self.assertEqual(
            mf.select("engineer", probe=lambda m: True, ladder=(m for m in [SONNET])),
            SONNET,
        )


class AgentsRegistryAlignment(unittest.TestCase):
    """The ladder must agree with agents.yaml so the console roster and the
    runtime selection never drift (the registry contract test, DRE-1335)."""

    def _agents(self):
        import yaml

        root = os.path.join(os.path.dirname(__file__), "..")
        with open(os.path.join(root, "agents.yaml")) as f:
            return {a["name"]: a for a in yaml.safe_load(f)["agents"]}

    def _declared_ladders(self):
        """Every agent that declares a ladder — INCLUDING a one-entry one.

        An earlier version filtered on `len(...) > 1`, which silently excluded
        `fixer` (the only single-entry ladder) and made a retired id there
        invisible to the whole suite. A guard whose filter is narrower than the
        data it guards is the same defect it exists to prevent, one level up.
        """
        out = {}
        for name, a in self._agents().items():
            ladder = a.get("ladder")
            if not isinstance(ladder, list) or not ladder:
                continue
            models = []
            for i, step in enumerate(ladder):
                self.assertIsInstance(
                    step, dict, f"{name}: ladder[{i}] is not a mapping"
                )
                self.assertIn(
                    "model", step, f"{name}: ladder[{i}] has no `model:` key"
                )
                models.append(step["model"])
            out[name] = models
        return out

    def _declared_models(self):
        """Every agent's top-level `model:` — the field the console's agent
        editor rewrites, and the one no test looked at until an Opus 5 tile was
        found saving the retired id."""
        return {
            name: a["model"]
            for name, a in self._agents().items()
            if isinstance(a.get("model"), str)
        }

    def test_engineer_and_planner_ladder_matches_registry(self):
        declared = self._declared_ladders()
        for role in ("engineer", "planner"):
            self.assertEqual(
                declared[role], mf.LADDER,
                f"{role}: agents.yaml ladder {declared[role]} != mf.LADDER {mf.LADDER}",
            )

    def test_no_declared_ladder_references_a_retired_model(self):
        """EVERY agent that declares a ladder — not just engineer/planner — must
        draw from the CURRENT ladder, in ladder order.

        Checking only engineer/planner by name is what let the Opus 5 rotation
        land with `repairer` still pinned to claude-opus-4-8: no test ever looked
        at that agent. A subsequence check (rather than equality) is deliberate —
        an agent may legitimately declare a SHORTER ladder (database-architect
        skips Fable), but none may name a model that has rotated out."""
        declared = self._declared_ladders()
        # The single-entry ladder is the case the old filter dropped. Pin its
        # presence so the filter can never quietly narrow again.
        self.assertIn("fixer", declared, "single-entry ladders must be covered")
        for name, models in sorted(declared.items()):
            retired = [m for m in models if m not in mf.LADDER]
            self.assertEqual(
                retired, [],
                f"{name}: agents.yaml ladder names {retired}, "
                f"not in the current mf.LADDER {mf.LADDER}",
            )
            self.assertEqual(
                len(set(models)), len(models),
                f"{name}: agents.yaml ladder {models} repeats a model",
            )
            positions = [mf.LADDER.index(m) for m in models]
            self.assertEqual(
                positions, sorted(positions),
                f"{name}: agents.yaml ladder {models} is out of LADDER order",
            )

    def test_no_agent_pins_a_rotated_out_model_in_its_bare_model_field(self):
        """`model:` is a SEPARATE field from `ladder:`, and it is the one the
        console's agent editor rewrites — so it is the field a bad write lands
        in. Checking only `ladder:` left the whole mutation path unguarded."""
        declared = self._declared_models()
        self.assertTrue(declared, "no agent declares a bare model: field")
        for name, model in sorted(declared.items()):
            self.assertIn(
                model, mf.LADDER,
                f"{name}: agents.yaml `model: {model}` is not in the current "
                f"mf.LADDER {mf.LADDER}",
            )


class FableIsNotABuildModelTest(unittest.TestCase):
    """Fable is excluded from the workhorse ladder by POLICY, not availability.

    Live incident 2026-08-09: Anthropic enabled `claude-fable-5` on our
    subscription, so the availability probe stopped 404ing it and the best-first
    ladder promoted EVERY build agent in the fleet onto the priciest model (~2x
    Opus per token) with no code change and no human decision. The subscription's
    rolling usage window drained, agents began dying `is_error` mid-run, and
    three good Portico cards were requeued and parked needs-human.

    So the guard is not "skip Fable while it 404s" — that guard passed the whole
    time. It is "a stronger model becoming AVAILABLE must never silently promote
    itself onto the build path": the ladder starts at Opus and does not contain
    Fable at all, and no build role can reach it even with every probe green.
    Fable stays a KNOWN model (attribution) and is reserved for the advisory /
    reviewer role the config-driven redesign will introduce.
    """

    BUILD_ROLES = ("engineer", "planner", "devops", "frontend")

    def setUp(self):
        mf.clear_availability_cache()

    def tearDown(self):
        mf.clear_availability_cache()

    def test_ladder_starts_at_opus(self):
        # The workhorse ladder is Opus-first: the model a healthy build agent
        # gets when everything is up.
        self.assertEqual(mf.LADDER[0], OPUS)

    def test_ladder_does_not_contain_fable(self):
        # Not "Fable last" — absent. Anything still in the ladder is one
        # availability flip away from being selected.
        self.assertNotIn(FABLE, mf.LADDER)

    def test_no_build_role_selects_fable_when_everything_is_available(self):
        # The exact incident condition: the probe reports EVERY model up.
        for role in self.BUILD_ROLES:
            with self.subTest(role=role):
                mf.clear_availability_cache()
                self.assertNotEqual(
                    mf.select(role, probe=lambda m: True), FABLE,
                    f"{role} selected Fable with every model available",
                )

    def test_every_build_role_selects_opus_when_everything_is_available(self):
        for role in self.BUILD_ROLES:
            with self.subTest(role=role):
                mf.clear_availability_cache()
                self.assertEqual(mf.select(role, probe=lambda m: True), OPUS)

    def test_fable_is_not_reachable_at_any_availability(self):
        # Whatever the probe says about the ladder's own models — all up, all
        # down, only-Fable-up — the answer is never Fable, because Fable is not
        # on the walk. The fall-through case matters most: "nothing available"
        # must land on Sonnet, never escalate.
        for avail in (
            {OPUS: True, SONNET: True},
            {OPUS: False, SONNET: True},
            {OPUS: True, SONNET: False},
            {OPUS: False, SONNET: False},
        ):
            with self.subTest(avail=avail):
                mf.clear_availability_cache()
                got = mf.select("engineer", probe=lambda m: avail.get(m, True))
                self.assertNotEqual(got, FABLE)
                self.assertIn(got, mf.LADDER)

    def test_fable_stays_known_so_in_flight_markers_still_attribute(self):
        # Same reasoning as RETIRED_MODELS: cards in flight right now carry
        # `model-error: claude-fable-5` / `model-attempt: claude-fable-5`.
        # Dropping the id would make last_error_model() return None and the
        # console would lose the attribution rather than report it. Only
        # SELECTABILITY changed.
        self.assertIn(FABLE, mf.KNOWN_MODELS)
        self.assertEqual(mf.last_error_model([mf.error_marker(FABLE)]), FABLE)

    def test_cli_select_does_not_return_fable_with_everything_available(self):
        # The workflows call the CLI, not select() — assert the real entry
        # point. The stub probe comes from BUREAU_FAKE_AVAILABLE: no network.
        env = dict(os.environ)
        env["BUREAU_FAKE_AVAILABLE"] = json.dumps(
            {FABLE: True, OPUS: True, SONNET: True}
        )
        script = os.path.join(
            os.path.dirname(__file__), "..", "scripts", "model_fallback.py"
        )
        for role in self.BUILD_ROLES:
            with self.subTest(role=role):
                out = subprocess.run(
                    [sys.executable, script, "select", role],
                    capture_output=True, text=True, env=env,
                ).stdout.strip()
                self.assertEqual(out, OPUS)


if __name__ == "__main__":
    unittest.main()
