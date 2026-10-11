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
shared by both roles. `select` walks the ladder top→bottom and returns the first
AVAILABLE model, where availability is probed at runtime (a minimal
/v1/messages call): a 404 / not-found means UNAVAILABLE (skip); ANY other
response — including 429 (rate-limited) — means AVAILABLE (choose it). Results
are cached with a short TTL so we don't probe on every dispatch, and recovery
within the ladder is free: when a skipped model comes back, the next probe after
the TTL sees it available and `select` returns it again.

Fable is on no build ladder (2026-08-09). Its absence is a COST/QUOTA policy,
not an outage — see FableIsNotABuildModelTest at the foot of this file for the
incident that made it one. Availability decides how far DOWN the ladder we
walk; ladder membership is a human decision.

Since DRE-3015 the planner walks a ladder of its own (`judgement`); every role
that builds shares the workhorse one. The kinds and the policy that bounds them
are pinned in tests/test_model_policy.py — this file is about the ladder WALK.

Which model leads which ladder, and which group an agent is in, is the
console's to change (DRE-6862). So every rule of the walk runs against the
frozen fixture (`tests/model_fixture.py`) — the selector loaded from a
throwaway tree of the frozen files, or that tree's CLI — and reads every rung
it expects off the fixture's ladders. Only AgentsRegistryAlignment's `model:`
check and CommittedStateTest read the committed files, for what must hold for
any saved state.

These tests drive the ladder walk with a STUBBED availability function — no real
network. The is_error heartbeat helpers (attempt_marker/error_marker) and the
hold-cap regression (dead_run.py) are preserved and re-asserted here.
"""

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

import model_fallback as mf  # noqa: E402
import model_fixture  # noqa: E402

# Off every ladder on cost policy, and still a KNOWN model so an in-flight
# marker naming it attributes (FableIsNotABuildModelTest).
FABLE = "claude-fable-5"

ROOT = Path(__file__).resolve().parents[1]
AGENT_FIX_WF = ROOT / ".github" / "workflows" / "agent-fix.yml"

# The frozen config every rule test reads its expected rungs off.
FIXTURE = model_fixture.fixture_config()
SESSION_AGENTS = set(FIXTURE.get("session_agents") or ())

# The selector loaded from a throwaway tree of the frozen files, and that
# tree's CLI — set by setUpModule.
fx = None
FIXTURE_CLI = None
_TREE = None


def setUpModule():
    global fx, FIXTURE_CLI, _TREE
    _TREE = tempfile.TemporaryDirectory()
    tree = model_fixture.fixture_tree(Path(_TREE.name) / "tree")
    fx = model_fixture.fixture_module(tree)
    FIXTURE_CLI = str(tree / "scripts" / "model_fallback.py")


def tearDownModule():
    _TREE.cleanup()


def walk(role):
    """The rungs `role` walks in the frozen config: its group's ladder."""
    return model_fixture.ladder(FIXTURE, FIXTURE["agents"][role])


def roles_in(kind):
    """The pipeline roles the frozen config puts in group `kind`."""
    return [
        role for role, group in FIXTURE["agents"].items()
        if group == kind and role not in SESSION_AGENTS
    ]


def rung_ids(rungs):
    return [r["model"] if isinstance(r, dict) else r for r in rungs or ()]


def default_ladder():
    """The frozen config's `default_ladder`, the walk of an unknown role."""
    return rung_ids(FIXTURE["ladders"][FIXTURE["default_ladder"]])


EXCLUDED = rung_ids(FIXTURE.get("excluded"))
RETIRED = rung_ids(FIXTURE.get("retired"))
BUILD_ROLES = roles_in("workhorse")
PLANNER = roles_in("judgement")[0]


def fixed_clock(t):
    """A deterministic monotonic clock for cache-TTL tests."""
    return lambda: t[0]


def down_to(rung, ladder):
    """A probe table with every rung above `rung` unavailable and the rest up."""
    above = ladder[:ladder.index(rung)]
    return {m: m not in above for m in ladder}


def cli_select(*args, available):
    env = dict(os.environ)
    # JSON map model->bool consumed by the CLI's test hook.
    env["BUREAU_FAKE_AVAILABLE"] = json.dumps(available)
    return subprocess.run(
        [sys.executable, FIXTURE_CLI, "select", *args],
        capture_output=True, text=True, env=env,
    )


class LadderShapeTest(unittest.TestCase):
    def test_the_walk_is_the_configs_ladder_in_the_configs_order(self):
        # The ladder is the contract: what the selector walks is what the
        # config writes, in the order it writes it. Which model leads is the
        # config's decision, not this file's; each role's own walk is
        # AgentsRegistryAlignment's.
        self.assertEqual(fx.LADDER, default_ladder())
        for name, rungs in FIXTURE["ladders"].items():
            with self.subTest(ladder=name):
                self.assertEqual(fx.LADDERS[name], rung_ids(rungs))

    def test_ladder_entries_are_all_known_models(self):
        self.assertTrue(set(fx.LADDER) <= fx.KNOWN_MODELS)

    def test_retired_model_stays_recognizable_but_unselectable(self):
        # A card dispatched before the Opus 5 rotation can still carry
        # `model-error: claude-opus-4-8`. If the retired id stopped being a
        # KNOWN model, last_error_model() would silently return None and the
        # console would lose attribution for that death — so it stays known
        # while being removed from the selectable ladder.
        self.assertTrue(RETIRED, "the fixture retires no model")
        for retired in RETIRED:
            with self.subTest(model=retired):
                self.assertIn(retired, fx.KNOWN_MODELS)
                self.assertNotIn(retired, fx.LADDER)
                self.assertEqual(
                    fx.last_error_model([fx.error_marker(retired)]), retired
                )


class AvailabilityClassificationTest(unittest.TestCase):
    """A 404 means gone (skip); anything else — including 429 — means present."""

    def test_404_is_unavailable(self):
        self.assertFalse(fx.classify_available(404))
        self.assertFalse(fx.classify_available(404, "not_found_error"))

    def test_429_is_available(self):
        # Throttling != gone; the existing transient retry handles 429s.
        self.assertTrue(fx.classify_available(429))

    def test_200_is_available(self):
        self.assertTrue(fx.classify_available(200))

    def test_400_and_500_are_available(self):
        # Any non-404 status means the model exists for us.
        self.assertTrue(fx.classify_available(400))
        self.assertTrue(fx.classify_available(500))

    def test_not_found_error_string_is_unavailable_even_without_status(self):
        self.assertFalse(fx.classify_available(None, "model: not_found_error"))
        self.assertFalse(fx.classify_available(None, "model not available, use Opus"))


class SelectLadderTest(unittest.TestCase):
    """select() walks the ladder and returns the first available model, for
    every role, driven by a stubbed availability function (no network)."""

    def setUp(self):
        fx.clear_availability_cache()

    def tearDown(self):
        fx.clear_availability_cache()

    def test_top_of_ladder_available_returns_it_for_every_role(self):
        # No role hardcodes a model — each walks its group's ladder. Fable
        # probes available too (Anthropic enabled it, 2026-08-09) and is still
        # not chosen: it is not on the walk. See FableIsNotABuildModelTest.
        for role in FIXTURE["agents"]:
            if role in SESSION_AGENTS:
                continue
            with self.subTest(role=role):
                fx.clear_availability_cache()
                self.assertEqual(fx.select(role, probe=lambda m: True), walk(role)[0])

    def test_an_unavailable_rung_is_walked_past_to_the_next(self):
        # Availability only walks DOWN: a rung reported unavailable is never
        # the answer while a rung below it is up.
        ladder = walk("engineer")
        for rung in ladder[1:]:
            with self.subTest(lands_on=rung):
                fx.clear_availability_cache()
                avail = down_to(rung, ladder)
                self.assertEqual(
                    fx.select("engineer", probe=lambda m: avail[m]), rung
                )

    def test_all_unavailable_falls_through_to_last_known_good(self):
        # Degrade safely: if NOTHING probes available, never block a build —
        # fall through to the last (lowest) known-good model rather than return
        # nothing or a model just confirmed gone.
        ladder = walk("engineer")
        chosen = fx.select("engineer", probe=lambda m: False)
        self.assertEqual(chosen, ladder[-1])

    def test_probe_exception_treated_as_inconclusive_falls_through(self):
        # A probe that raises (network error/timeout) must not block the build
        # and must not return a model just confirmed 404. Here the top two
        # rungs raise (inconclusive → skip) and the third is up → the third.
        ladder = walk("engineer")
        self.assertGreaterEqual(len(ladder), 3)

        def probe(m):
            if m in ladder[:2]:
                raise TimeoutError("probe network error")
            return True
        self.assertEqual(fx.select("engineer", probe=probe), ladder[2])


class CachingTest(unittest.TestCase):
    """Availability is cached with a short TTL so we don't probe on every
    dispatch; after the TTL a higher-ranked model that recovered is re-selected
    (auto-recovery)."""

    def setUp(self):
        fx.clear_availability_cache()

    def tearDown(self):
        fx.clear_availability_cache()

    def test_probe_not_called_repeatedly_within_ttl(self):
        ladder = walk("engineer")
        peers = [r for r in roles_in(FIXTURE["agents"]["engineer"]) if r != "engineer"]
        self.assertTrue(peers, "no second role shares the engineer's ladder")
        avail = down_to(ladder[-1], ladder)
        calls = []

        def probe(m):
            calls.append(m)
            return avail[m]

        t = [1000.0]
        # First select: every rung probed, only the last one up.
        self.assertEqual(
            fx.select("engineer", probe=probe, clock=fixed_clock(t)), ladder[-1]
        )
        first = list(calls)
        self.assertEqual(first, ladder)
        # A second role on the SAME ladder within the TTL: nothing new probed —
        # served from cache.
        self.assertEqual(
            fx.select(peers[0], probe=probe, clock=fixed_clock(t)), ladder[-1]
        )
        self.assertEqual(calls, first, "probe re-called within TTL window")

    def test_auto_recovery_after_ttl_when_higher_model_returns(self):
        # Recovery is bounded by the ladder's CONTENTS: it can restore a model
        # we already chose to run on, never add one we didn't (which is how an
        # enabled Fable promoted the whole fleet on 2026-08-09).
        ladder = walk("engineer")
        top, next_rung = ladder[0], ladder[1]
        state = down_to(next_rung, ladder)

        def probe(m):
            return state[m]

        t = [1000.0]
        # The top unavailable → the next rung chosen.
        self.assertEqual(
            fx.select("engineer", probe=probe, clock=fixed_clock(t)), next_rung
        )
        # The top comes back online.
        state[top] = True
        # Still within TTL: cached "top unavailable" → still the next rung.
        self.assertEqual(
            fx.select("engineer", probe=probe, clock=fixed_clock(t)), next_rung
        )
        # Advance past the TTL: next probe sees the top available → the top.
        t[0] += fx.AVAILABILITY_TTL_SECONDS + 1
        self.assertEqual(
            fx.select("engineer", probe=probe, clock=fixed_clock(t)), top
        )

    def test_clear_cache_forces_reprobe(self):
        calls = []
        top = walk("engineer")[0]

        def probe(m):
            calls.append(m)
            return m == top  # only the top available

        t = [0.0]
        fx.select("engineer", probe=probe, clock=fixed_clock(t))
        n = len(calls)
        fx.clear_availability_cache()
        fx.select("engineer", probe=probe, clock=fixed_clock(t))
        self.assertGreater(len(calls), n, "clear_availability_cache did not reprobe")


class HeartbeatMarkersPreservedTest(unittest.TestCase):
    """The DRE-1354 'which model was used' heartbeat + is_error markers stay —
    the workflows still write attempt_marker/error_marker and read them back."""

    def test_attempt_marker_roundtrip(self):
        model = walk("engineer")[0]
        self.assertIn(model, fx.attempt_marker(model))
        self.assertTrue(fx.attempt_marker(model).startswith(fx.MARKER_PREFIX))

    def test_error_marker_roundtrip(self):
        self.assertIn(FABLE, fx.error_marker(FABLE))
        self.assertTrue(fx.error_marker(FABLE).startswith(fx.ERROR_MARKER_PREFIX))

    def test_last_error_model_reads_latest_known_marker(self):
        model = walk("engineer")[0]
        bodies = [fx.error_marker(FABLE), fx.attempt_marker(model), fx.error_marker(model)]
        self.assertEqual(fx.last_error_model(bodies), model)

    def test_unknown_model_in_marker_is_ignored(self):
        self.assertIsNone(fx.last_error_model([fx.error_marker("gpt-9")]))

    def test_role_from_labels(self):
        self.assertEqual(fx._role_from_labels(["agent:planner"]), "planner")
        self.assertEqual(fx._role_from_labels(["agent:engineer"]), "engineer")


class IsErrorHoldCapRegressionTest(unittest.TestCase):
    """DRE-1354 contract preserved: an is_error death still counts toward the
    shared hold cap (no 18× loops). This lives in dead_run.decide()."""

    def test_is_error_death_requeues_under_cap(self):
        import dead_run

        d = dead_run.decide(0, is_error=True, error_model=walk("engineer")[0])
        self.assertEqual(d.action, "requeue")

    def test_is_error_death_holds_at_cap(self):
        import dead_run

        d = dead_run.decide(
            dead_run.REQUEUE_CAP, is_error=True, error_model=walk("engineer")[0]
        )
        self.assertEqual(d.action, "hold")


class CliTest(unittest.TestCase):
    """CLI select walks the ladder using a REAL probe by default — but tests
    inject a stub probe via the BUREAU_FAKE_AVAILABLE env so no network is hit.
    Run in the fixture tree, so it walks the frozen ladders."""

    def _select(self, role, fake_available):
        return cli_select(role, available=fake_available).stdout.strip()

    def test_cli_select_skips_an_unavailable_top_of_ladder(self):
        ladder = walk("engineer")
        for rung in ladder[1:]:
            with self.subTest(lands_on=rung):
                self.assertEqual(self._select("engineer", down_to(rung, ladder)), rung)

    def test_cli_select_returns_top_of_ladder_when_available(self):
        everything = {m: True for m in [FABLE, *walk("devops"), *walk(PLANNER)]}
        self.assertEqual(self._select("devops", everything), walk("devops")[0])

    def test_cli_select_walks_the_planners_own_ladder(self):
        # The planner is `judgement` (DRE-3015): it walks that ladder, and the
        # CLI is the entry point plan.yml actually calls.
        ladder = walk(PLANNER)
        self.assertEqual(self._select(PLANNER, {m: True for m in ladder}), ladder[0])
        self.assertEqual(self._select(PLANNER, down_to(ladder[1], ladder)), ladder[1])

    def test_cli_role_of_labels(self):
        def role_of(labels):
            buf = io.StringIO()
            with redirect_stdout(buf):
                fx.main(["role-of", labels])
            return buf.getvalue().strip()

        self.assertEqual(role_of("agent:planner,repo:atlas"), "planner")
        self.assertEqual(role_of("agent:engineer,size:m"), "engineer")
        self.assertEqual(role_of("repo:atlas"), "engineer")


class AvoidAModelThatJustDiedTest(unittest.TestCase):
    """`avoid` skips a rung the card's previous attempt died on (DRE-3824).

    The probe cannot see a refusal: in subscription mode it calls raw
    /v1/messages with the OAuth token, which answers 429 for EVERY model by
    design, and 429 reads "available". So when `claude-fable-5-1` refused every
    planner run with a monthly spend limit (2026-09-12/13), the probe kept
    choosing it, and the `model-error:` marker plan.yml wrote — "the medic rerun
    will switch to the alternate model" — was read by nothing. `avoid` is the
    selector half of reading it: the workflow finds the marker, the selector
    walks past that rung, loudly."""

    ALL_UP = staticmethod(lambda m: True)

    def setUp(self):
        fx.clear_availability_cache()
        self.ladder = walk(PLANNER)

    def tearDown(self):
        fx.clear_availability_cache()

    def test_avoiding_the_top_rung_walks_to_the_next(self):
        self.assertEqual(
            fx.select(PLANNER, probe=self.ALL_UP, avoid=[self.ladder[0]]),
            self.ladder[1],
        )

    def test_without_avoid_the_top_rung_is_still_chosen(self):
        # "It stays on Fable whenever Fable works": avoid is opt-in per call.
        self.assertEqual(fx.select(PLANNER, probe=self.ALL_UP), self.ladder[0])

    def test_an_avoided_rung_is_degraded_and_named_in_the_note(self):
        top = self.ladder[0]
        decision = fx.select_with_reasons(PLANNER, probe=self.ALL_UP, avoid=[top])
        self.assertTrue(decision["degraded"])
        self.assertFalse(decision["exhausted"])
        self.assertEqual([s["model"] for s in decision["skipped"]], [top])
        note = fx.selection_note(decision)
        self.assertTrue(note.startswith("DEGRADED"), note)
        self.assertIn(top, note)
        self.assertIn("died", note)

    def test_the_note_never_carries_a_death_marker(self):
        # The note rides the planner heartbeat comment; a `model-error: <id>`
        # substring inside it would be counted as a death by the next read.
        decision = fx.select_with_reasons(
            PLANNER, probe=self.ALL_UP, avoid=[self.ladder[0]]
        )
        self.assertNotIn(fx.ERROR_MARKER_PREFIX, fx.selection_note(decision))

    def test_avoid_does_not_probe_the_avoided_model(self):
        calls = []

        def probe(m):
            calls.append(m)
            return True

        fx.select(PLANNER, probe=probe, avoid=[self.ladder[0]])
        self.assertNotIn(self.ladder[0], calls)

    def test_avoiding_every_rung_still_never_blocks(self):
        chosen = fx.select(PLANNER, probe=self.ALL_UP, avoid=list(self.ladder))
        self.assertEqual(chosen, self.ladder[-1])

    def test_an_unknown_or_empty_avoid_changes_nothing(self):
        self.assertEqual(
            fx.select(PLANNER, probe=self.ALL_UP, avoid=["", "gpt-9"]),
            self.ladder[0],
        )

    def test_cli_avoid_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            why = os.path.join(tmp, "why.txt")
            out = cli_select(
                PLANNER, "--avoid", self.ladder[0], "--explain-file", why,
                available={m: True for m in self.ladder},
            )
            self.assertEqual(out.stdout.strip(), self.ladder[1], out.stderr)
            self.assertTrue(open(why).read().startswith("DEGRADED"))


class CallerSuppliedLadderTest(unittest.TestCase):
    """`select(ladder=...)` walks a caller-supplied order instead of the
    configured one.

    Folded up from the console's vendored copy of this module, which had carried
    it as its ONE intentional local delta. The console reads the ladder out of
    bureau-pipeline/agents.yaml and needs THIS walk to drive it, so the YAML
    stays the single source of order while this function stays the single source
    of algorithm. Keeping the kwarg only downstream meant the copy could never be
    compared to upstream byte-for-byte, so drift in the copy stayed invisible —
    which is exactly how it ended up three deltas deep instead of one.

    The kwarg is additive: omitting it must behave exactly as before. The
    supplied ladder is the caller's own, so it is built here: ids the walk
    treats as opaque, none of them the top of the engineer's configured walk."""

    FIRST, SECOND, LAST = "model-first", "model-second", "model-last"
    SUPPLIED = [FIRST, SECOND, LAST]

    def setUp(self):
        fx.clear_availability_cache()

    def tearDown(self):
        fx.clear_availability_cache()

    def test_supplied_ladder_drives_the_walk(self):
        # Everything is up, so the configured top would win if the supplied
        # order were ignored.
        self.assertEqual(
            fx.select("engineer", probe=lambda m: True, ladder=self.SUPPLIED),
            self.FIRST,
        )

    def test_supplied_ladder_still_skips_a_confirmed_404(self):
        avail = {self.FIRST: False, self.SECOND: True, self.LAST: True}
        self.assertEqual(
            fx.select("engineer", probe=lambda m: avail[m], ladder=self.SUPPLIED),
            self.SECOND,
        )

    def test_supplied_ladder_falls_through_to_its_own_last_entry(self):
        # Not the configured ladder's last rung — the caller's last entry, or
        # the console would fall back to a model outside the ladder it actually
        # asked for.
        self.assertEqual(
            fx.select("engineer", probe=lambda m: False, ladder=self.SUPPLIED),
            self.LAST,
        )

    def test_omitting_the_kwarg_is_unchanged(self):
        self.assertEqual(fx.select("engineer", probe=lambda m: True), walk("engineer")[0])

    def test_empty_or_none_ladder_falls_back_to_the_configured_ladder(self):
        for empty in ([], None):
            with self.subTest(ladder=empty):
                fx.clear_availability_cache()
                self.assertEqual(
                    fx.select("engineer", probe=lambda m: True, ladder=empty),
                    walk("engineer")[0],
                )

    def test_a_bare_string_is_not_walked_character_by_character(self):
        # A YAML scalar (`ladder: claude-opus-5`) instead of a sequence. A str is
        # truthy and iterable, so an unguarded walk selects a single LETTER as
        # the model id and hands it to the run — silently. Fall back instead.
        for probe in (lambda m: True, lambda m: False):
            with self.subTest(probe=probe):
                fx.clear_availability_cache()
                got = fx.select("engineer", probe=probe, ladder=self.FIRST)
                self.assertIn(got, walk("engineer"), f"selected {got!r}, not a real model")

    def test_raw_agents_yaml_ladder_shape_is_accepted(self):
        # agents.yaml stores `ladder:` as a list of {model:, reason:} mappings.
        # The docstring points callers at that file, so this shape must work
        # rather than raise — select() is contracted never to block a build.
        raw = [{"model": self.SECOND, "reason": "x"}, {"model": self.FIRST, "reason": "y"}]
        self.assertEqual(
            fx.select("engineer", probe=lambda m: True, ladder=raw), self.SECOND
        )

    def test_unusable_entries_are_dropped_and_an_empty_result_falls_back(self):
        fx.clear_availability_cache()
        self.assertEqual(
            fx.select("engineer", probe=lambda m: True, ladder=[None, 42, {}]),
            walk("engineer")[0],
        )
        fx.clear_availability_cache()
        self.assertEqual(
            fx.select("engineer", probe=lambda m: True, ladder=[None, self.SECOND]),
            self.SECOND,
        )

    def test_a_generator_ladder_is_materialised_once(self):
        self.assertEqual(
            fx.select("engineer", probe=lambda m: True, ladder=(m for m in [self.SECOND])),
            self.SECOND,
        )


class AgentsRegistryAlignment(unittest.TestCase):
    """The ladders must agree with the CANONICAL config so the console roster
    and the runtime selection never drift (the registry contract, DRE-1335).

    DRE-2316 moved the canonical declaration OUT of agents.yaml and into
    config/models.yaml: the registry's `model:` is now a generated mirror and
    its 5x copy-pasted `ladder:` block is gone. The resolution — role → group →
    ladder — is a rule and runs on the fixture; the registry's `model:` lines
    are checked against the committed config as written. The generated-region
    mechanics (markers, regeneration, the drift gate) live in
    tests/test_model_config.py."""

    def test_every_role_walks_its_own_groups_ladder(self):
        # DRE-3015: a role is resolved through its group, not handed the build
        # ladder — the planner's group names a different ladder, and that is
        # the one it walks.
        for role in FIXTURE["agents"]:
            if role in SESSION_AGENTS:
                continue
            with self.subTest(role=role):
                self.assertEqual(fx.ladder_for(role), walk(role))
        self.assertNotEqual(walk(PLANNER), default_ladder())
        self.assertEqual(fx.ladder_for(PLANNER), walk(PLANNER))

    def test_every_registry_model_line_is_the_top_of_its_configured_ladder(self):
        """`model:` is the field the console's agent editor rewrites, so it is
        the field a bad write lands in, and the one no test looked at until an
        Opus 5 tile was found saving the retired id. It is GENERATED from the
        config, and it must be the first rung of that agent's configured ladder
        as written — read off the committed files, whatever they say."""
        cfg = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())
        agents = yaml.safe_load((ROOT / "agents.yaml").read_text())["agents"]
        declared = {
            a["name"]: a["model"] for a in agents if isinstance(a.get("model"), str)
        }
        self.assertTrue(declared, "no agent declares a bare model: field")
        for name, model in sorted(declared.items()):
            with self.subTest(agent=name):
                self.assertIn(name, cfg["agents"], f"{name}: in no group")
                top = model_fixture.ladder(cfg, cfg["agents"][name])[0]
                self.assertEqual(
                    model, top,
                    f"{name}: agents.yaml `model: {model}` is not the top of its "
                    f"configured ladder ({top})",
                )


class CommittedStateTest(unittest.TestCase):
    """What must hold for the committed files whatever the console saved."""

    def test_no_configured_ladder_references_a_retired_or_excluded_model(self):
        """EVERY ladder draws from selectable models, read off the committed
        file's own `retired` and `excluded` lists.

        Checking only engineer/planner by name is what let the Opus 5 rotation
        land with `repairer` still pinned to claude-opus-4-8: no test ever
        looked at that agent. There are no carve-outs, deliberately — an
        attempt-gated escalation is still a build path, so "this one agent may
        name an off-ladder model" would reopen exactly the door this file
        exists to shut."""
        cfg = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())
        barred = set(rung_ids(cfg.get("retired"))) | set(rung_ids(cfg.get("excluded")))
        self.assertTrue(cfg["ladders"])
        for name, rungs in sorted(cfg["ladders"].items()):
            models = rung_ids(rungs)
            with self.subTest(ladder=name):
                self.assertEqual(
                    sorted(set(models) & barred), [],
                    f"{name}: ladder names retired or excluded models",
                )
                self.assertEqual(
                    len(set(models)), len(models),
                    f"{name}: ladder {models} repeats a model",
                )

    def test_agent_fix_workflow_hardcodes_no_model(self):
        """agent-fix's attempt-3 escalation is framing, not a pricier model.

        It used to hardcode `--model claude-fable-5` in the workflow and mirror
        that id in the registry; then `--model claude-opus-5`. A capped per-PR
        escalation is smaller than the fleet-wide default that drained the
        subscription, but it is still a build path reaching for a model without
        a human in the loop. agents.yaml's header requires a workflow model
        change to update the registry in the SAME PR — so read the workflow,
        not just the registry. A pinned `--model` is drift by construction: it
        cannot be changed by editing config/models.yaml."""
        src = AGENT_FIX_WF.read_text()
        self.assertEqual(
            re.findall(r"--model (claude-[a-z0-9.-]+)", src), [],
            "agent-fix.yml must take its model from config/models.yaml",
        )
        self.assertIn("model_fallback.py select fixer", src)


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
    itself onto the build path": an excluded model is on no ladder at all, and
    no build role can reach it — nor the judgement ladder's top — even with
    every probe green. Fable stays a KNOWN model (attribution).

    The planner LEFT the build roles on 2026-09-03 (DRE-3015) — it is
    `judgement` now and walks its own ladder. That is a decision a human made in
    config/models.yaml, which is the only way up; nothing here is relaxed, and
    tests/test_model_policy.py pins the same guard from both sides. Every id
    below is read off the frozen fixture.
    """

    def setUp(self):
        fx.clear_availability_cache()

    def tearDown(self):
        fx.clear_availability_cache()

    def test_the_fixture_excludes_fable_and_has_build_roles(self):
        # Guard: the rules below need both to say anything.
        self.assertIn(FABLE, EXCLUDED)
        self.assertTrue(BUILD_ROLES)

    def test_no_ladder_contains_an_excluded_model(self):
        # Not "Fable last" — absent. Anything still in the ladder is one
        # availability flip away from being selected.
        for name, ladder in fx.LADDERS.items():
            with self.subTest(ladder=name):
                self.assertEqual(sorted(set(ladder) & set(EXCLUDED)), [])

    def test_no_build_role_selects_an_excluded_model_when_everything_is_available(self):
        # The exact incident condition: the probe reports EVERY model up.
        for role in BUILD_ROLES:
            with self.subTest(role=role):
                fx.clear_availability_cache()
                self.assertNotIn(
                    fx.select(role, probe=lambda m: True), EXCLUDED,
                    f"{role} selected an excluded model with every model available",
                )

    def test_every_build_role_selects_its_top_rung_when_everything_is_available(self):
        for role in BUILD_ROLES:
            with self.subTest(role=role):
                fx.clear_availability_cache()
                self.assertEqual(fx.select(role, probe=lambda m: True), walk(role)[0])

    def test_fable_is_not_reachable_at_any_availability(self):
        # Whatever the probe says about the ladder's own models — all up, all
        # down, any one rung down — the answer is never an excluded model nor
        # the judgement ladder's top, because neither is on the walk. The
        # fall-through case matters most: "nothing available" must land on the
        # last build rung, never escalate.
        ladder = walk("engineer")
        tables = [{m: True for m in ladder}, {m: False for m in ladder}]
        tables += [{m: m != rung for m in ladder} for rung in ladder]
        for avail in tables:
            with self.subTest(avail=avail):
                fx.clear_availability_cache()
                got = fx.select("engineer", probe=lambda m: avail.get(m, True))
                self.assertNotIn(got, EXCLUDED)
                self.assertNotEqual(got, walk(PLANNER)[0])
                self.assertIn(got, ladder)

    def test_fable_stays_known_so_in_flight_markers_still_attribute(self):
        # Same reasoning as RETIRED_MODELS: cards in flight right now carry
        # `model-error: claude-fable-5` / `model-attempt: claude-fable-5`.
        # Dropping the id would make last_error_model() return None and the
        # console would lose the attribution rather than report it. Only
        # SELECTABILITY changed.
        for model in EXCLUDED:
            with self.subTest(model=model):
                self.assertIn(model, fx.KNOWN_MODELS)
                self.assertEqual(fx.last_error_model([fx.error_marker(model)]), model)

    def test_cli_select_does_not_return_fable_with_everything_available(self):
        # The workflows call the CLI, not select() — assert the real entry
        # point. The stub probe comes from BUREAU_FAKE_AVAILABLE: no network.
        everything = {m: True for m in [*EXCLUDED, *walk(PLANNER), *default_ladder()]}
        for role in BUILD_ROLES:
            with self.subTest(role=role):
                out = cli_select(role, available=everything).stdout.strip()
                self.assertEqual(out, walk(role)[0])


if __name__ == "__main__":
    unittest.main()
