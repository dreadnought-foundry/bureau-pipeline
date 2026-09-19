"""RED-first: the dispatch pool ranks bots by the meter the runner is charged
against (DRE-4290).

THE DEFECT. In the first hour after DRE-4282 (#447) put the critic, the sweeps
and the harness on the pool — 2026-09-18, 17:46 to 18:50 PT — 184 fleet runs
every one of which logged `dispatch-pool: 4 candidate apps, selected slot 1
(max-remaining)`. The three spare Apps' meters read 1 to 3 requests used for
the hour. At 18:38 PT portico's Reconcile (run 35413054438) was refused on
slot 1 with `API rate limit exceeded for installation ID 123249480` — the App
the pool had just chosen as the one with the most room.

WHY. `scripts/dispatch_pool.py` ranked candidates by `resources.core.remaining`
out of `GET /rate_limit`, and that endpoint reports a counter the runners'
calls are not charged against: on 2026-09-17 09:39 PT it read `used 0` in the
same second a real call's `x-ratelimit-used` header read 127 on a different
reset clock, and on 2026-09-18 15:37 PT two harness runs were refused from
GitHub-hosted runners while the same installation's `/rate_limit`, read from
the operator's machine, showed 3,000+ remaining. Slot 1's `/rate_limit` reading
was therefore almost always the largest, `max-remaining` always answered slot 1,
and DRE-2013's proof — written against a fake that injected the per-slot
number directly — could not see it.

WHAT THIS SUITE PINS.
  * The ranking reads `x-ratelimit-remaining` off ONE real, counted call per
    candidate (`GET /repos/{owner}/{repo}` on the repo the run is in), and the
    `/rate_limit` body is never consulted. The first test below feeds four
    candidates whose `/rate_limit` bodies all say 5,000 while their real-call
    headers say 5,000 / 4,900 / 200 / 4,950, through a fake `urlopen` that
    serves BOTH shapes, so it is red on the `/rate_limit` code and green on the
    header code without knowing which the module calls.
  * The spread: candidates at or above SPREAD_BAND remaining are equally
    eligible and the deterministic hash picks among them; below the band the
    roomiest wins; a refused probe (403/429) is out for the run; nothing
    readable falls back to the hash.
  * The `dispatch-pool:` line names every slot's reading and the rule.
  * The fake hook is header-shaped and cannot express a `/rate_limit` body.
  * The harness probes the sandbox repo its tokens are scoped to; every other
    consumer probes the repo it runs in, and all eight still pass the selector's
    choice downstream.
  * DRE-2013's degradation is intact: no pool secrets, slot 1, no request.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))

import dispatch_pool  # noqa: E402

SCRIPT = ROOT / "scripts" / "dispatch_pool.py"
REPO = "dreadnought-foundry/portico"

#: Every workflow that consults the pool, and the step whose token is slot 1's
#: probe there (test_readers_on_the_pool.py's CONSUMERS plus the three workers).
CONSUMERS = (
    "agent-task.yml",
    "verify.yml",
    "red-main-repair.yml",
    "qa-review.yml",
    "reconcile.yml",
    "agent-fix.yml",
    "plan.yml",
    "harness.yml",
)


def pool_env(**overrides) -> dict:
    env = {
        "GITHUB_REPOSITORY": REPO,
        "BUREAU_APP_ID": "3350400",
        "BUREAU_POOL_TOKEN": "ghs_slot1",
        "BUREAU_APP_ID_2": "4266537",
        "BUREAU_POOL_TOKEN_2": "ghs_slot2",
        "BUREAU_APP_ID_3": "4266538",
        "BUREAU_POOL_TOKEN_3": "ghs_slot3",
        "BUREAU_APP_ID_4": "4266539",
        "BUREAU_POOL_TOKEN_4": "ghs_slot4",
    }
    env.update(overrides)
    return env


# --------------------------------------------------------------------------- #
# A fake GitHub that serves both meters                                         #
# --------------------------------------------------------------------------- #

class _Response(io.BytesIO):
    """What `urlopen` yields: a context manager with `.status`, `.headers` and
    `.read()`. Headers are a plain mapping, matched case-insensitively by
    the module (HTTPMessage does the same for real)."""

    def __init__(self, status: int, headers: dict, body: bytes):
        super().__init__(body)
        self.status = status
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def two_meter_github(rate_limit_remaining: dict, header_remaining: dict,
                     refused: set = frozenset(), calls: list | None = None):
    """A `urlopen` that answers `/rate_limit` from one table and every real
    call from another, keyed by the bearer token's slot. `calls` records
    (slot, url) so a test can say what was asked."""

    def slot_of(req) -> int:
        auth = req.get_header("Authorization") or ""
        m = re.search(r"ghs_slot(\d+)$", auth)
        assert m, f"unexpected probe credential {auth!r}"
        return int(m.group(1))

    def fake_urlopen(req, timeout=None):
        slot = slot_of(req)
        url = req.full_url
        if calls is not None:
            calls.append((slot, url))
        if url.endswith("/rate_limit"):
            body = json.dumps(
                {"resources": {"core": {"limit": 5000,
                                        "remaining": rate_limit_remaining[slot],
                                        "used": 0, "reset": 1758250000}}}
            ).encode()
            return _Response(200, {"Content-Type": "application/json"}, body)
        headers = {
            "X-RateLimit-Limit": "5000",
            "X-RateLimit-Remaining": str(header_remaining[slot]),
            "X-RateLimit-Reset": "1758250000",
            "X-RateLimit-Resource": "core",
        }
        if slot in refused:
            headers["X-RateLimit-Remaining"] = "0"
            raise urllib.error.HTTPError(
                url, 403, "rate limit exceeded", headers,
                io.BytesIO(b'{"message":"API rate limit exceeded for installation ID 123249480"}'),
            )
        return _Response(200, headers, b'{"full_name": "%s"}' % REPO.encode())

    return fake_urlopen


def select_under(fake, env):
    with mock.patch.object(urllib.request, "urlopen", fake):
        return dispatch_pool.select(env)


# --------------------------------------------------------------------------- #
# AC 1 — the ranking reads the header meter, not /rate_limit                    #
# --------------------------------------------------------------------------- #

class RanksByTheChargedMeterTest(unittest.TestCase):

    def test_the_drained_slot_is_never_chosen_and_slot_one_is_not_always(self):
        # Card AC 1: four candidates whose /rate_limit bodies all say 5,000
        # while their real-call headers say 5,000 / 4,900 / 200 / 4,950.
        # Today's code reads four 5,000s, ties, and hashes across all four —
        # so slot 3 appears within fifty keys. The header code never picks
        # the 200 slot and spreads the rest.
        fake = two_meter_github(
            {1: 5000, 2: 5000, 3: 5000, 4: 5000},
            {1: 5000, 2: 4900, 3: 200, 4: 4950},
        )
        picks = set()
        for i in range(50):
            slot, reason = select_under(fake, pool_env(BUREAU_POOL_KEY=f"DRE-{i}"))
            self.assertNotEqual(slot, 3, f"key DRE-{i} chose the 200-remaining slot")
            picks.add(slot)
        self.assertNotEqual(picks, {1}, "the pool must not always answer slot 1")
        self.assertEqual(picks, {1, 2, 4})

    def test_rate_limit_is_never_consulted_and_each_candidate_costs_one_call(self):
        # One real, counted call per candidate — on the repo the run is in.
        calls: list = []
        fake = two_meter_github(
            {1: 5000, 2: 5000, 3: 5000, 4: 5000},
            {1: 4000, 2: 4000, 3: 4000, 4: 4000},
            calls=calls,
        )
        select_under(fake, pool_env(BUREAU_POOL_KEY="DRE-4290"))
        self.assertEqual(sorted(s for s, _ in calls), [1, 2, 3, 4])
        for _, url in calls:
            self.assertEqual(url, f"https://api.github.com/repos/{REPO}")

    def test_the_probe_repo_can_be_named_when_the_tokens_are_scoped_elsewhere(self):
        # harness.yml runs in bureau-pipeline but every token in its pool
        # block is minted `repositories: bureau-harness`; a probe on the
        # workflow's own repo would be a 404, not a reading.
        calls: list = []
        fake = two_meter_github(
            {1: 5000, 2: 5000, 3: 5000, 4: 5000},
            {1: 4000, 2: 4000, 3: 4000, 4: 4000},
            calls=calls,
        )
        select_under(fake, pool_env(
            BUREAU_POOL_KEY="harness:1",
            BUREAU_POOL_PROBE_REPO="dreadnought-foundry/bureau-harness",
        ))
        self.assertTrue(calls)
        for _, url in calls:
            self.assertEqual(
                url, "https://api.github.com/repos/dreadnought-foundry/bureau-harness"
            )

    def test_no_repo_to_probe_makes_no_request_and_falls_back(self):
        calls: list = []
        fake = two_meter_github({}, {}, calls=calls)
        env = pool_env(BUREAU_POOL_KEY="DRE-4290")
        del env["GITHUB_REPOSITORY"]
        slot, reason = select_under(fake, env)
        self.assertEqual(calls, [], "nothing to call without a repo")
        self.assertEqual(reason, "fallback")
        self.assertEqual(slot, dispatch_pool.hash_pick("DRE-4290", [1, 2, 3, 4]))


# --------------------------------------------------------------------------- #
# AC 2 — a refused probe is out for the run                                     #
# --------------------------------------------------------------------------- #

class RefusedProbeIsExcludedTest(unittest.TestCase):

    def test_a_refused_candidate_is_never_chosen_this_run(self):
        # Slot 1 answers 403 `API rate limit exceeded` — portico's Reconcile
        # at 18:38 PT. Its /rate_limit body would still say 5,000.
        fake = two_meter_github(
            {1: 5000, 2: 5000, 3: 5000, 4: 5000},
            {1: 5000, 2: 4900, 3: 4800, 4: 4950},
            refused={1},
        )
        picks = set()
        for i in range(50):
            slot, reason = select_under(fake, pool_env(BUREAU_POOL_KEY=f"DRE-{i}"))
            self.assertNotEqual(slot, 1, f"key DRE-{i} chose the refused slot")
            self.assertEqual(reason, "spread")
            picks.add(slot)
        self.assertEqual(picks, {2, 3, 4})

    def test_a_refusal_is_not_an_unreadable_slot(self):
        # The exclusion must survive urllib's habit of RAISING on 403/429: a
        # refusal swallowed as "unreadable" would be hashed back into the
        # pool by DRE-2013's partial-failure rule.
        for status in (403, 429):
            with self.subTest(status=status):
                reading = dispatch_pool.parse_probe(
                    status, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1758250000"}
                )
                self.assertTrue(reading.refused)
                self.assertIsNone(reading.remaining)
                self.assertEqual(reading.reset, 1758250000)

    def test_every_candidate_refused_falls_back_across_all_configured(self):
        readings = {s: dispatch_pool.parse_probe(429, {}) for s in (1, 2, 3)}
        slot, reason = dispatch_pool.choose(readings, key="DRE-4290")
        self.assertEqual(reason, "fallback")
        self.assertEqual(slot, dispatch_pool.hash_pick("DRE-4290", [1, 2, 3]))

    def test_refused_plus_unreadable_falls_back_across_the_unrefused(self):
        readings = {
            1: dispatch_pool.parse_probe(403, {}),
            2: None,
            3: None,
        }
        picks = {
            dispatch_pool.choose(readings, key=f"DRE-{i}")[0] for i in range(50)
        }
        self.assertEqual(picks, {2, 3}, "a refused slot is out even of the blind hash")


# --------------------------------------------------------------------------- #
# The band and the spread                                                       #
# --------------------------------------------------------------------------- #

class SpreadBandTest(unittest.TestCase):

    def test_the_band_is_two_thousand_remaining(self):
        # Pinned: the docstring justifies this number and a change to it is a
        # decision, not a drive-by.
        self.assertEqual(dispatch_pool.SPREAD_BAND, 2000)

    def test_candidates_at_or_above_the_band_share_the_load_by_hash(self):
        readings = {1: 4999, 2: 2000, 3: 3500, 4: 1999}
        picks = {
            dispatch_pool.choose(readings, key=f"DRE-{i}")[0] for i in range(60)
        }
        self.assertEqual(picks, {1, 2, 3}, "2,000 is in the band; 1,999 is not")
        for i in range(5):
            slot, reason = dispatch_pool.choose(readings, key=f"DRE-{i}")
            self.assertEqual(reason, "spread")
            self.assertEqual(slot, dispatch_pool.hash_pick(f"DRE-{i}", [1, 2, 3]))

    def test_the_spread_is_deterministic_per_key(self):
        readings = {1: 4999, 2: 4998, 3: 4997, 4: 4996}
        first = dispatch_pool.choose(readings, key="review:447")[0]
        for _ in range(5):
            self.assertEqual(dispatch_pool.choose(readings, key="review:447")[0], first)

    def test_one_candidate_in_the_band_is_the_roomiest_not_a_spread(self):
        slot, reason = dispatch_pool.choose({1: 1999, 2: 2000, 3: 10, 4: 10}, key="x")
        self.assertEqual((slot, reason), (2, "max-remaining"))

    def test_below_the_band_the_roomiest_wins(self):
        slot, reason = dispatch_pool.choose({1: 1999, 2: 1500, 3: 10}, key="x")
        self.assertEqual((slot, reason), (1, "max-remaining"))

    def test_below_the_band_a_tie_is_hashed(self):
        readings = {1: 1500, 2: 1500, 3: 10}
        picks = {dispatch_pool.choose(readings, key=f"DRE-{i}")[0] for i in range(50)}
        self.assertEqual(picks, {1, 2})

    def test_a_single_readable_candidate_is_the_only_readable(self):
        readings = {1: None, 2: 100, 3: dispatch_pool.parse_probe(403, {})}
        self.assertEqual(dispatch_pool.choose(readings, key="x"), (2, "only-readable"))

    def test_an_unreadable_slot_is_never_picked_while_a_readable_one_exists(self):
        # DRE-2013's rule kept, now with real numbers: the readable ones are
        # RANKED, not hashed — hashing {5,000, 100} blind picks the drained
        # slot half the time, which is this card's defect in another costume.
        readings = {1: 5000, 2: None, 3: 100}
        for i in range(20):
            slot, reason = dispatch_pool.choose(readings, key=f"DRE-{i}")
            self.assertEqual((slot, reason), (1, "max-remaining"))


# --------------------------------------------------------------------------- #
# The header parser and the fake hook                                           #
# --------------------------------------------------------------------------- #

class ProbeParserTest(unittest.TestCase):

    def test_reads_remaining_and_reset_case_insensitively(self):
        reading = dispatch_pool.parse_probe(
            200, {"x-ratelimit-remaining": "4812", "X-RateLimit-Reset": "1758250000"}
        )
        self.assertEqual((reading.remaining, reading.reset, reading.refused),
                         (4812, 1758250000, False))

    def test_a_response_without_the_header_is_unreadable(self):
        reading = dispatch_pool.parse_probe(200, {"Content-Type": "application/json"})
        self.assertIsNone(reading.remaining)
        self.assertFalse(reading.refused)

    def test_a_non_numeric_header_is_unreadable(self):
        self.assertIsNone(dispatch_pool.parse_probe(200, {"X-RateLimit-Remaining": "lots"}).remaining)

    def test_a_404_still_carries_a_reading(self):
        # GitHub charges a 404 and stamps the meter on it; only a refusal is
        # a refusal.
        reading = dispatch_pool.parse_probe(404, {"X-RateLimit-Remaining": "4000"})
        self.assertEqual(reading.remaining, 4000)
        self.assertFalse(reading.refused)

    def test_the_fake_hook_is_header_shaped(self):
        env = pool_env(
            BUREAU_POOL_KEY="DRE-4290",
            BUREAU_FAKE_POOL_PROBES=json.dumps({
                "1": {"status": 200, "x-ratelimit-remaining": "5000"},
                "2": {"status": 200, "x-ratelimit-remaining": "100"},
                "3": {"status": 403, "x-ratelimit-remaining": "0"},
                "4": None,
            }),
        )

        def boom(*_a, **_k):  # pragma: no cover - fails the test if reached
            raise AssertionError("the fake hook must replace the network")

        with mock.patch.object(urllib.request, "urlopen", boom):
            slot, reason = dispatch_pool.select(env)
        self.assertEqual((slot, reason), (1, "max-remaining"))

    def test_the_fake_hook_cannot_express_a_rate_limit_body(self):
        # A /rate_limit body handed to the fake is not a reading: the hook
        # goes through the same header parser the real probe uses.
        env = pool_env(
            BUREAU_POOL_KEY="DRE-4290",
            BUREAU_FAKE_POOL_PROBES=json.dumps({
                "1": {"resources": {"core": {"remaining": 5000}}},
                "2": {"resources": {"core": {"remaining": 5000}}},
                "3": {"resources": {"core": {"remaining": 5000}}},
                "4": {"resources": {"core": {"remaining": 5000}}},
            }),
        )
        with mock.patch.object(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network"))):
            slot, reason = dispatch_pool.select(env)
        self.assertEqual(reason, "fallback")

    def test_the_old_per_slot_number_hook_is_dead(self):
        # BUREAU_FAKE_RATE_LIMITS injected the number DRE-2013 ranked on; it
        # is the fake that could not see this defect, and it does nothing now.
        self.assertFalse(hasattr(dispatch_pool, "parse_remaining"))
        self.assertNotIn("BUREAU_FAKE_RATE_LIMITS", SCRIPT.read_text(encoding="utf-8"))
        fake = two_meter_github(
            {1: 5000, 2: 5000, 3: 5000, 4: 5000},
            {1: 100, 2: 4900, 3: 100, 4: 100},
        )
        env = pool_env(
            BUREAU_POOL_KEY="DRE-4290",
            BUREAU_FAKE_RATE_LIMITS=json.dumps({"1": 5000, "2": 1, "3": 1, "4": 1}),
        )
        self.assertEqual(select_under(fake, env), (2, "max-remaining"))


# --------------------------------------------------------------------------- #
# AC 3 — DRE-2013's degradation, untouched                                      #
# --------------------------------------------------------------------------- #

class NoPoolSecretsTest(unittest.TestCase):

    def test_no_pool_secrets_is_slot_one_with_no_request(self):
        def boom(*_a, **_k):  # pragma: no cover - fails the test if reached
            raise AssertionError("no pool, no probe")

        env = {"GITHUB_REPOSITORY": REPO, "BUREAU_APP_ID": "3350400",
               "BUREAU_POOL_TOKEN": "ghs_slot1", "BUREAU_APP_ID_2": "",
               "BUREAU_APP_ID_3": "", "BUREAU_APP_ID_4": ""}
        with mock.patch.object(urllib.request, "urlopen", boom):
            self.assertEqual(dispatch_pool.select(env), (1, "single-app"))
            self.assertEqual(dispatch_pool.select({"GITHUB_REPOSITORY": REPO}), (1, "no-pool"))


# --------------------------------------------------------------------------- #
# AC 4 — the log line, and every consumer still passes the choice downstream    #
# --------------------------------------------------------------------------- #

def run_cli(env: dict) -> subprocess.CompletedProcess:
    full_env = {"PATH": os.environ.get("PATH", "")}
    full_env.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "select"],
        env=full_env, capture_output=True, text=True, timeout=30,
    )


class LogLineTest(unittest.TestCase):

    LINE = re.compile(
        r"^dispatch-pool: slot1=(\d[\d,]*|refused\S*|unreadable) "
        r"slot2=(\d[\d,]*|refused\S*|unreadable) "
        r"slot3=(\d[\d,]*|refused\S*|unreadable) "
        r"slot4=(\d[\d,]*|refused\S*|unreadable) "
        r"→ selected slot (\d+) \((spread|max-remaining|only-readable|fallback)\)$"
    )

    def _line(self, result):
        lines = [l for l in result.stderr.splitlines() if l.startswith("dispatch-pool:")]
        self.assertEqual(len(lines), 1, result.stderr)
        return lines[0]

    def test_the_line_names_every_slots_reading_and_the_rule(self):
        result = run_cli(pool_env(
            BUREAU_POOL_KEY="reconcile:35413054438",
            BUREAU_FAKE_POOL_PROBES=json.dumps({
                "1": {"status": 200, "x-ratelimit-remaining": "4812"},
                "2": {"status": 200, "x-ratelimit-remaining": "4997"},
                "3": {"status": 403, "x-ratelimit-remaining": "0",
                      "x-ratelimit-reset": "1758250000"},
                "4": {"status": 200, "x-ratelimit-remaining": "4990"},
            }),
        ))
        self.assertEqual(result.returncode, 0, result.stderr)
        line = self._line(result)
        m = self.LINE.match(line)
        self.assertIsNotNone(m, line)
        self.assertEqual(m.group(1), "4,812")
        self.assertEqual(m.group(2), "4,997")
        self.assertTrue(m.group(3).startswith("refused"), line)
        self.assertEqual(m.group(4), "4,990")
        self.assertEqual(m.group(6), "spread")
        chosen = int(m.group(5))
        self.assertIn(chosen, (1, 2, 4))
        self.assertIn(f"n={chosen}", result.stdout.splitlines())
        self.assertIn("reason=spread", result.stdout.splitlines())

    def test_an_unreadable_slot_is_named_as_such(self):
        result = run_cli(pool_env(
            BUREAU_POOL_KEY="DRE-4290",
            BUREAU_FAKE_POOL_PROBES=json.dumps({
                "1": {"status": 200, "x-ratelimit-remaining": "100"},
                "2": None,
                "3": {"status": 200, "x-ratelimit-remaining": "300"},
                "4": {"status": 200, "x-ratelimit-remaining": "200"},
            }),
        ))
        line = self._line(result)
        self.assertIn("slot2=unreadable", line)
        self.assertTrue(line.endswith("→ selected slot 3 (max-remaining)"), line)

    def test_stdout_stays_output_lines_only(self):
        result = run_cli(pool_env(
            BUREAU_POOL_KEY="DRE-4290",
            BUREAU_FAKE_POOL_PROBES=json.dumps({
                "1": {"status": 200, "x-ratelimit-remaining": "4000"},
                "2": {"status": 200, "x-ratelimit-remaining": "4000"},
                "3": {"status": 200, "x-ratelimit-remaining": "4000"},
                "4": {"status": 200, "x-ratelimit-remaining": "4000"},
            }),
        ))
        for line in result.stdout.splitlines():
            self.assertRegex(line, r"^(n|reason)=\S+$")
        for token in ("ghs_slot1", "ghs_slot2", "ghs_slot3", "ghs_slot4"):
            self.assertNotIn(token, result.stdout + result.stderr, "never print a token")


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def all_steps(name: str) -> list[dict]:
    out = []
    for job in load(name)["jobs"].values():
        out.extend(job.get("steps") or [])
    return out


class EveryConsumerStillPassesTheChoiceDownstreamTest(unittest.TestCase):

    def _selector(self, name):
        for i, s in enumerate(all_steps(name)):
            if "dispatch_pool.py select" in (s.get("run") or ""):
                return i, s
        raise AssertionError(f"{name}: no dispatch_pool.py select step")

    def test_all_eight_consumers_select_and_feed_a_mint_from_the_choice(self):
        for name in CONSUMERS:
            with self.subTest(workflow=name):
                sel_i, sel = self._selector(name)
                self.assertEqual(sel.get("id"), "pool")
                env = sel.get("env") or {}
                for n in (1, 2, 3, 4):
                    self.assertIn(dispatch_pool.token_env_name(n), env)
                self.assertIn("BUREAU_POOL_KEY", env)
                downstream = [
                    s for s in all_steps(name)[sel_i + 1:]
                    if "create-github-app-token" in (s.get("uses") or "")
                    and "steps.pool.outputs.n" in yaml.safe_dump(s.get("with") or {})
                ]
                self.assertTrue(downstream, f"{name}: no mint reads steps.pool.outputs.n")

    def test_the_harness_probes_the_sandbox_its_tokens_are_scoped_to(self):
        _, sel = self._selector("harness.yml")
        self.assertEqual(
            (sel.get("env") or {}).get("BUREAU_POOL_PROBE_REPO"),
            "dreadnought-foundry/bureau-harness",
        )
        driver = next(
            s for s in all_steps("harness.yml") if s.get("name") == "Run harness scenarios"
        )
        self.assertEqual(
            (driver.get("env") or {}).get("HARNESS_POOL_SLOT"), "${{ steps.pool.outputs.n }}"
        )

    def test_every_other_consumer_probes_the_repo_it_runs_in(self):
        # Their tokens are minted for the repository the workflow runs in
        # (create-github-app-token's default), so GITHUB_REPOSITORY is the
        # repo to probe and nothing overrides it.
        for name in CONSUMERS:
            if name == "harness.yml":
                continue
            with self.subTest(workflow=name):
                _, sel = self._selector(name)
                self.assertNotIn("BUREAU_POOL_PROBE_REPO", sel.get("env") or {})


class TheRecordTest(unittest.TestCase):
    """The two-meter observation is written into the module so nobody restores
    the /rate_limit ranking as a simplification (standards/engineering.md: a
    change that contradicts a document updates it in the SAME PR)."""

    def test_the_docstring_carries_both_dated_readings_and_the_probe_cost(self):
        doc = dispatch_pool.__doc__ or ""
        self.assertIn("2026-09-17", doc)
        self.assertIn("2026-09-18", doc)
        self.assertIn("x-ratelimit-remaining", doc)
        self.assertIn("one", doc.lower())
        self.assertIn("per candidate per run", doc)
        self.assertNotIn("quota-exempt, so probing costs nothing", doc)

    def test_no_document_still_calls_the_probe_quota_exempt(self):
        texts = {
            "docs/vendor-boundary-audit-2026-07.md": ROOT / "docs" / "vendor-boundary-audit-2026-07.md",
            "scripts/harness/README.md": ROOT / "scripts" / "harness" / "README.md",
        }
        for name in CONSUMERS:
            texts[name] = WORKFLOWS / name
        for label, path in texts.items():
            with self.subTest(doc=label):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("quota-exempt", text)
                self.assertNotIn("/rate_limit", text)


if __name__ == "__main__":
    unittest.main()
