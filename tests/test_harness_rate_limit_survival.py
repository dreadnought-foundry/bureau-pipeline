"""RED-first: the harness survives a rate-limited refusal (DRE-4575).

THE DEFECT. Integration Harness run 35664409350 (main @ eabb37da, 2026-09-21)
died four times in a row — jobs 106558913864, 106559259639, 106563014898,
106574268460 — each about forty seconds in, and `stable` stopped moving. The
pool probe read slot 1 at 4,454 / 4,415 / 3,606 / 4,432 remaining and
`spread` hashed `BUREAU_POOL_KEY=harness:<run_id>` to slot 1 every time (the
key is identical across re-run attempts). Ten to twelve seconds after each
attempt's `repository_dispatch`, every call — reader and worker alike — was
refused with `API rate limit exceeded for installation ID 123249480`.

WHY THE PROBE DISAGREED WITH THE CALLS. GitHub meters that installation on at
least two independent 5,000/h counters and charges each request to whichever
it lands on: one token, one endpoint, twenty calls at 20:29–20:31 PT answered
~60% `used ~60, reset 21:28 PT` and ~40% `used ~3,385, reset 21:04 PT`. The
second counter fills at ~8,000/h and is dry from about :40 to :04 every hour.
A single probe reading proves nothing about the next call.

WHAT THE DRIVER DID WITH THE REFUSAL. `github_api.GitHub._attempt` retries
only 5xx; a 403 raised at once, `agent_task_parses` failed at verify, and
every later scenario died in setup and cleanup. The response headers —
`x-ratelimit-reset`, the one fact that says how long the refusal lasts —
were discarded: only the body was read.

WHICH HEADER SAYS HOW LONG (review round 1). GitHub has two ways of saying
slow down and sends two "come back at" times in the same breath: a SECONDARY
limit answers `retry-after` (~60 s) while the same response still carries the
PRIMARY window's `x-ratelimit-reset`, up to ~59 minutes out. `retry-after`
first is GitHub's documented order; the other way round reads the common
one-minute refusal as an hour, judges it past the cap and gives up on the
spot — the exact death this card exists to end.

WHAT THIS SUITE PINS.
  * A 403/429 whose body names a rate limit raises `RateLimited`, a
    `GitHubError` carrying the reset read from the headers — `retry-after`
    first, `x-ratelimit-reset` only when it is absent. A permission 403
    is still a plain `GitHubError`, and is not retried.
  * A READER built with `fallback_suppliers` answers a refusal by minting from
    the next pool slot, retrying the request once as that identity, and
    keeping that supplier as its re-mint source — so the 50-minute re-mint
    cannot put it back on the refused slot.
  * A fallback whose MINT raises is skipped, not fatal: the client stays where
    it is, the next slot is tried, and when every one is spent the exception
    that propagates is the original `RateLimited` — never the mint's error.
  * A client with NO fallback (the worker — WHICH identity acts is the thing
    under test) waits for the reset through an injectable sleeper, capped by
    a named constant that leaves the sweep's own budget room, and retries once.
  * The driver builds the reader's fallbacks from HARNESS_POOL_APP_ID_<n> /
    HARNESS_POOL_APP_PRIVATE_KEY_<n>, ordered by the probe's headroom with the
    worker App last; the `github-spend:` lines name every slot the reads rode
    and what each one's own hour paid.
  * harness.yml hands the scenario step every slot's pair and its headroom,
    and salts the pool key with the run attempt.

Run: python3 -m pytest tests/test_harness_rate_limit_survival.py -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from harness import __main__ as harness_main  # noqa: E402
from harness import framework, github_api  # noqa: E402
from harness.github_api import GitHub, GitHubError  # noqa: E402

REPO = "dreadnought-foundry/bureau-harness"
HARNESS_YML = ROOT / ".github" / "workflows" / "harness.yml"

REFUSAL = (
    b'{"message": "API rate limit exceeded for installation ID 123249480. '
    b'If you reach out to GitHub Support for help, please include the request '
    b'ID 6402:2134C1:CBF3F9:29A1A37:6AB1CFD7"}'
)
FORBIDDEN = b'{"message": "Resource not accessible by integration"}'


def _refusal(status=403, body=REFUSAL, headers=None):
    """What urllib raises on a refused call: an HTTPError whose headers carry
    GitHub's meter and whose body says why."""
    return urllib.error.HTTPError(
        "https://api.github.com/repos/x", status, "refused",
        headers if headers is not None else {}, io.BytesIO(body),
    )


class ScriptedOpener:
    """Answers each request from a script — a (status, bytes, headers) tuple
    or an exception to raise — and records the bearer token every request
    carried, so a test can say WHO asked, in what order."""

    def __init__(self, answers=()):
        self.answers = list(answers)
        self.tokens = []
        self.methods = []

    def __call__(self, req):
        self.tokens.append(req.headers.get("Authorization"))
        self.methods.append(req.get_method())
        answer = self.answers.pop(0) if self.answers else (200, b"[]", {})
        if isinstance(answer, BaseException):
            raise answer
        return answer


# --------------------------------------------------------------------------- #
# The refusal is named, with its reset                                         #
# --------------------------------------------------------------------------- #

class RateLimitedIsNamedTest(unittest.TestCase):

    def test_a_rate_limit_403_raises_RateLimited_carrying_the_reset(self):
        # Refused, waited out once (the sleeper is injected — nothing here
        # sleeps), refused again: the refusal stands, and it is the named kind.
        naps = []
        api = ScriptedOpener([
            _refusal(headers={"x-ratelimit-reset": "1790049858"}),
            _refusal(headers={"x-ratelimit-reset": "1790049858"}),
        ])
        gh = GitHub("ghs_x", opener=api, wall_clock=lambda: 1790049800.0, sleeper=naps.append)
        with self.assertRaises(github_api.RateLimited) as caught:
            gh.list_open_prs(REPO)
        self.assertIsInstance(caught.exception, GitHubError)
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(caught.exception.reset, 1790049858)
        self.assertEqual(len(naps), 1, "one wait, never a loop")
        self.assertEqual(len(api.tokens), 2, "one retry after the wait, then it stands")

    def test_a_secondary_limit_is_read_off_retry_after_not_the_primary_window(self):
        # The review-round-1 defect, end to end. GitHub's secondary-limit
        # refusal carries BOTH headers: `retry-after: 60` (this refusal) and
        # the primary window's `x-ratelimit-reset`, 55 minutes out. Reading
        # the primary one made the client conclude it had been asked to wait
        # the best part of an hour, judge that past the cap, and give up on a
        # refusal it only had to sit out — with no fallback identity, which
        # is the worker's shape by design.
        naps, log = [], []
        secondary = (
            b'{"message": "You have exceeded a secondary rate limit and have '
            b'been temporarily blocked from content creation. Please retry '
            b'your request again later."}'
        )
        now = 1_000_000.0
        api = ScriptedOpener([
            _refusal(
                body=secondary,
                headers={
                    "retry-after": "60",
                    "x-ratelimit-reset": str(int(now + 55 * 60)),
                },
            ),
            (201, b'{"id": 9}', {}),
        ])
        worker = GitHub(
            "ghs_worker", opener=api, wall_clock=lambda: now,
            sleeper=naps.append, log=log.append, identity="worker",
        )
        self.assertEqual(worker.create_comment(REPO, 1, "x"), {"id": 9})
        self.assertEqual(len(naps), 1, f"the one-minute pause must be waited out: {log}")
        self.assertGreaterEqual(naps[0], 60)
        self.assertLessEqual(naps[0], 65, "~60s, not the primary window's 55 minutes")
        self.assertEqual(api.tokens, ["Bearer ghs_worker", "Bearer ghs_worker"])

    def test_retry_after_wins_over_x_ratelimit_reset_in_the_header_read(self):
        # The unit under the test above: whichever order the mapping happens
        # to iterate in, `retry-after` is the one about THIS refusal.
        self.assertEqual(
            github_api._reset_from_headers(
                {"retry-after": "60", "x-ratelimit-reset": "1790000000"}, 1000.0
            ),
            1060,
        )
        # Absent, the primary window is still better than nothing.
        self.assertEqual(
            github_api._reset_from_headers({"x-ratelimit-reset": "1790000000"}, 1000.0),
            1790000000,
        )
        # Unparseable `retry-after` falls through rather than swallowing the
        # other header (GitHub may send an HTTP-date form).
        self.assertEqual(
            github_api._reset_from_headers(
                {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT",
                 "x-ratelimit-reset": "1790000000"},
                1000.0,
            ),
            1790000000,
        )

    def test_a_429_with_retry_after_is_RateLimited_too(self):
        naps = []
        api = ScriptedOpener([
            _refusal(status=429, headers={"Retry-After": "60"}),
            _refusal(status=429, headers={"Retry-After": "60"}),
        ])
        gh = GitHub("ghs_x", opener=api, wall_clock=lambda: 1000.0, sleeper=naps.append)
        with self.assertRaises(github_api.RateLimited) as caught:
            gh.list_open_prs(REPO)
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.reset, 1060)
        self.assertEqual(len(naps), 1)

    def test_a_refusal_with_no_reset_header_carries_none(self):
        naps = []
        api = ScriptedOpener([_refusal()])
        with self.assertRaises(github_api.RateLimited) as caught:
            GitHub("ghs_x", opener=api, sleeper=naps.append).list_open_prs(REPO)
        self.assertIsNone(caught.exception.reset)
        self.assertEqual(naps, [], "nothing to wait for")
        self.assertEqual(len(api.tokens), 1)

    def test_a_permission_403_is_still_a_plain_GitHubError(self):
        # `Resource not accessible by integration` on cancelling a sandbox
        # run (agent_task_parses cleanup) is a permissions gap, not a meter.
        api = ScriptedOpener([_refusal(body=FORBIDDEN)])
        with self.assertRaises(GitHubError) as caught:
            GitHub("ghs_x", opener=api).cancel_workflow_run(REPO, 1)
        self.assertNotIsInstance(caught.exception, github_api.RateLimited)
        self.assertEqual(len(api.tokens), 1)


# --------------------------------------------------------------------------- #
# The reader moves to the next pool slot                                        #
# --------------------------------------------------------------------------- #

class ReaderFallsBackTest(unittest.TestCase):

    def test_a_refused_get_is_retried_once_as_the_next_pool_slot(self):
        api = ScriptedOpener([
            _refusal(headers={"x-ratelimit-reset": "1790049858"}),
            (200, b'[{"number": 1}]', {}),
        ])
        minted, log = [], []

        def slot2():
            minted.append("slot2")
            return "ghs_slot2"

        reader = GitHub(
            "ghs_slot1", opener=api, identity="pool slot 1",
            fallback_suppliers=[("pool slot 2", slot2)], log=log.append,
        )
        self.assertEqual(reader.list_open_prs(REPO), [{"number": 1}])
        self.assertEqual(api.tokens, ["Bearer ghs_slot1", "Bearer ghs_slot2"])
        self.assertEqual(minted, ["slot2"])
        self.assertEqual(reader.identity, "pool slot 2")
        self.assertEqual(reader.identity_trail, ["pool slot 1", "pool slot 2"])
        self.assertTrue(
            any("pool slot 1" in line and "pool slot 2" in line for line in log),
            f"the switch must be said in the run log, got {log}",
        )

    def test_the_switched_reader_reminits_from_the_slot_it_moved_to(self):
        # The 50-minute proactive re-mint (TOKEN_REFRESH_SECONDS) must mint
        # from the slot the reader moved TO — a re-mint from the original
        # supplier would put the late scenarios straight back on the refused
        # slot.
        clock = [0.0]
        original, moved = [], []

        def slot1():
            original.append(1)
            return "ghs_slot1_again"

        def slot2():
            moved.append(1)
            return f"ghs_slot2_{len(moved)}"

        api = ScriptedOpener([_refusal(), (200, b"[]", {}), (200, b"[]", {})])
        reader = GitHub(
            "ghs_slot1", opener=api, clock=lambda: clock[0], token_supplier=slot1,
            fallback_suppliers=[("pool slot 2", slot2)], log=lambda *_: None,
        )
        reader.list_open_prs(REPO)  # refused, moved to slot 2, retried
        clock[0] += github_api.TOKEN_REFRESH_SECONDS + 1
        reader.list_open_prs(REPO)  # past the refresh window: re-mint
        self.assertEqual(original, [], "the refused slot must never be re-minted")
        self.assertEqual(moved, [1, 1])
        self.assertEqual(api.tokens[-1], "Bearer ghs_slot2_2")
        self.assertEqual(reader.current_token(), "ghs_slot2_2")

    def test_fallbacks_are_spent_in_order_and_then_the_refusal_stands(self):
        api = ScriptedOpener([_refusal(), _refusal(), (200, b'{"ok": 1}', {})])
        reader = GitHub(
            "ghs_slot3", opener=api, identity="pool slot 3", log=lambda *_: None,
            fallback_suppliers=[("pool slot 4", lambda: "ghs_slot4"),
                                ("pool slot 1", lambda: "ghs_slot1")],
        )
        self.assertEqual(reader.request("GET", "/repos/x"), {"ok": 1})
        self.assertEqual(api.tokens, ["Bearer ghs_slot3", "Bearer ghs_slot4", "Bearer ghs_slot1"])
        self.assertEqual(reader.identity, "pool slot 1")
        # Every slot refused: the refusal stands, and it is the named kind.
        api2 = ScriptedOpener([_refusal(), _refusal()])
        alone = GitHub(
            "ghs_slot3", opener=api2, log=lambda *_: None,
            fallback_suppliers=[("pool slot 4", lambda: "ghs_slot4")],
        )
        with self.assertRaises(github_api.RateLimited):
            alone.request("GET", "/repos/x")
        self.assertEqual(len(api2.tokens), 2)

    def test_the_raw_log_archive_read_falls_back_the_same_way(self):
        api = ScriptedOpener([_refusal(), (200, b"PK\x03\x04", {})])
        reader = GitHub(
            "ghs_slot1", opener=api, log=lambda *_: None,
            fallback_suppliers=[("pool slot 2", lambda: "ghs_slot2")],
        )
        self.assertEqual(reader.request_bytes("GET", "/repos/x/actions/runs/1/logs"), b"PK\x03\x04")
        self.assertEqual(api.tokens, ["Bearer ghs_slot1", "Bearer ghs_slot2"])

    def test_a_fallback_whose_mint_raises_is_skipped_for_the_next_slot(self):
        # A supply() is two live REST calls behind an openssl signature, and
        # it can raise — 404 when the App is not installed on the sandbox,
        # 401, 5xx, an openssl failure. Unguarded that killed the run,
        # abandoned every healthy slot still in the list, and replaced a
        # recoverable refusal with an unrelated error.
        api = ScriptedOpener([_refusal(), (200, b'{"ok": 1}', {})])
        log = []

        def dead():
            raise GitHubError(404, '{"message": "Not Found"}')

        reader = GitHub(
            "ghs_slot1", opener=api, identity="pool slot 1", log=log.append,
            fallback_suppliers=[("pool slot 2", dead),
                                ("pool slot 3", lambda: "ghs_slot3")],
        )
        self.assertEqual(reader.request("GET", "/repos/x"), {"ok": 1})
        self.assertEqual(api.tokens, ["Bearer ghs_slot1", "Bearer ghs_slot3"])
        self.assertEqual(reader.identity, "pool slot 3")
        self.assertEqual(reader.identity_trail, ["pool slot 1", "pool slot 3"])
        self.assertTrue(
            any("pool slot 2" in line and "mint" in line for line in log),
            f"the dead slot must be said in the run log, got {log}",
        )

    def test_when_every_fallback_fails_to_mint_the_original_refusal_stands(self):
        # Not the mint's 404: `_send`'s recovery loop and every caller that
        # branches on `RateLimited` have to still see the refusal, and the
        # wait path has to get its turn.
        naps = []
        api = ScriptedOpener([
            _refusal(headers={"x-ratelimit-reset": "1300"}),
            _refusal(headers={"x-ratelimit-reset": "1300"}),
        ])

        def dead(which):
            def supply():
                raise RuntimeError(f"openssl RS256 signing failed ({which})")
            return supply

        reader = GitHub(
            "ghs_slot1", opener=api, identity="pool slot 1",
            wall_clock=lambda: 1000.0, sleeper=naps.append, log=lambda *_: None,
            fallback_suppliers=[("pool slot 2", dead(2)), ("pool slot 3", dead(3))],
        )
        with self.assertRaises(github_api.RateLimited) as caught:
            reader.request("GET", "/repos/x")
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(caught.exception.reset, 1300)
        # Still on the slot it started on, and the wait it could not move
        # away from was taken instead.
        self.assertEqual(reader.identity, "pool slot 1")
        self.assertEqual(reader.identity_trail, ["pool slot 1"])
        self.assertEqual(len(naps), 1)
        self.assertEqual(len(api.tokens), 2)

    def test_a_failed_mint_leaves_the_re_mint_source_alone(self):
        # The poisoning half: `_supplier` used to be assigned before the mint
        # was attempted, so one bad slot broke every LATER proactive re-mint
        # — `current_token()` included — for the rest of the run.
        clock = [0.0]
        healthy = []

        def slot1():
            healthy.append(1)
            return f"ghs_slot1_{len(healthy)}"

        def dead():
            raise GitHubError(401, '{"message": "Bad credentials"}')

        api = ScriptedOpener([
            _refusal(headers={"x-ratelimit-reset": "100"}),
            (200, b"[]", {}),
            (200, b"[]", {}),
        ])
        reader = GitHub(
            "ghs_slot1", opener=api, identity="pool slot 1",
            clock=lambda: clock[0], token_supplier=slot1,
            sleeper=lambda _: None, wall_clock=lambda: 0.0, log=lambda *_: None,
            fallback_suppliers=[("pool slot 2", dead)],
        )
        # Refused, slot 2 will not mint, the wait recovers it on slot 1.
        self.assertEqual(reader.list_open_prs(REPO), [])
        self.assertEqual(reader.identity, "pool slot 1")
        # Past the refresh window the client still re-mints, from its own key.
        clock[0] += github_api.TOKEN_REFRESH_SECONDS + 1
        self.assertEqual(reader.current_token(), "ghs_slot1_1")
        reader.list_open_prs(REPO)
        self.assertEqual(api.tokens[-1], "Bearer ghs_slot1_1")

    def test_without_fallbacks_and_without_a_reset_the_client_is_exactly_the_old_one(self):
        api = ScriptedOpener([_refusal()])
        naps = []
        with self.assertRaises(github_api.RateLimited):
            GitHub("ghs_x", opener=api, sleeper=naps.append).list_open_prs(REPO)
        self.assertEqual(naps, [])
        self.assertEqual(len(api.tokens), 1)


# --------------------------------------------------------------------------- #
# The worker waits for the reset                                                #
# --------------------------------------------------------------------------- #

class WorkerWaitsForTheResetTest(unittest.TestCase):

    def test_a_refused_write_waits_for_the_reset_and_retries_once(self):
        naps, log = [], []
        api = ScriptedOpener([
            _refusal(headers={"x-ratelimit-reset": "1300"}),
            (201, b'{"number": 5}', {}),
        ])
        worker = GitHub(
            "ghs_worker", opener=api, wall_clock=lambda: 1000.0,
            sleeper=naps.append, log=log.append, identity="worker",
        )
        self.assertEqual(worker.create_pr(REPO, "h", "b", "t", "body")["number"], 5)
        self.assertEqual(len(naps), 1)
        self.assertGreaterEqual(naps[0], 300)
        self.assertLessEqual(naps[0], 310)
        self.assertEqual(api.tokens, ["Bearer ghs_worker", "Bearer ghs_worker"])
        self.assertEqual(api.methods, ["POST", "POST"])
        self.assertTrue(any("worker" in line and "reset" in line for line in log), log)

    def test_a_reset_past_the_cap_is_not_waited_for(self):
        naps = []
        far = 1000 + github_api.RATE_LIMIT_WAIT_CAP_SECONDS + 60
        api = ScriptedOpener([_refusal(headers={"x-ratelimit-reset": str(far)})])
        worker = GitHub("ghs_worker", opener=api, wall_clock=lambda: 1000.0, sleeper=naps.append)
        with self.assertRaises(github_api.RateLimited):
            worker.create_comment(REPO, 1, "x")
        self.assertEqual(naps, [])

    def test_the_cap_leaves_the_sweeps_own_budget_room(self):
        # READ the budget out of harness.yml rather than restating it: a cap
        # pinned against a second copy of the number drifts silently when
        # either moves. And the relation is STRICTLY less — the receipt
        # annotates past BUDGET_MINUTES, and a cap AT the budget makes every
        # waited run trip a warning whose own text says such a run "holds the
        # sandbox, holds every queued run behind it, and holds the release
        # channel with them".
        doc = yaml.safe_load(HARNESS_YML.read_text(encoding="utf-8"))
        steps = {s.get("name") or s.get("uses"): s for s in doc["jobs"]["harness"]["steps"]}
        budget = int(steps["Scenario duration receipt"]["env"]["BUDGET_MINUTES"])
        self.assertGreater(github_api.RATE_LIMIT_WAIT_CAP_SECONDS, 0)
        self.assertLess(
            github_api.RATE_LIMIT_WAIT_CAP_SECONDS, budget * 60,
            f"the wait cap must leave room inside the {budget}m scenario budget",
        )
        # And still long enough for the real refusal: the counter that turned
        # away run 35664409350 resets at ~:04, so a refusal at :40 waits ~25.
        self.assertGreaterEqual(github_api.RATE_LIMIT_WAIT_CAP_SECONDS, 24 * 60)

    def test_a_401_after_a_remint_still_reaches_the_rate_limit_recovery(self):
        # The 401 arm used to `return attempt()` — outside the `while`, so a
        # refusal on the post-re-mint attempt was neither moved nor waited
        # out, and the whole of this card was bypassed by one expired token.
        naps = []
        api = ScriptedOpener([
            _refusal(status=401, body=b'{"message": "Bad credentials"}'),
            _refusal(headers={"x-ratelimit-reset": "1300"}),
            (200, b'{"ok": 1}', {}),
        ])
        worker = GitHub(
            "ghs_stale", opener=api, token_supplier=lambda: "ghs_fresh",
            wall_clock=lambda: 1000.0, sleeper=naps.append, log=lambda *_: None,
        )
        self.assertEqual(worker.request("POST", "/repos/x", {"a": 1}), {"ok": 1})
        self.assertEqual(
            api.tokens,
            ["Bearer ghs_stale", "Bearer ghs_fresh", "Bearer ghs_fresh"],
        )
        self.assertEqual(len(naps), 1, "the refusal after the re-mint is waited out")

    def test_a_persistent_401_still_surfaces_after_exactly_one_remint(self):
        api = ScriptedOpener([
            _refusal(status=401, body=b'{"message": "Bad credentials"}'),
            _refusal(status=401, body=b'{"message": "Bad credentials"}'),
        ])
        worker = GitHub(
            "ghs_stale", opener=api, log=lambda *_: None,
            token_supplier=lambda: "ghs_fresh",
        )
        with self.assertRaises(GitHubError) as caught:
            worker.request("GET", "/repos/x")
        self.assertEqual(caught.exception.status, 401)
        self.assertNotIsInstance(caught.exception, github_api.RateLimited)
        self.assertEqual(len(api.tokens), 2, "one re-mint, never a loop")

    def test_a_reset_already_behind_us_retries_at_once(self):
        naps = []
        api = ScriptedOpener([_refusal(headers={"x-ratelimit-reset": "900"}), (200, b"{}", {})])
        worker = GitHub("ghs_worker", opener=api, wall_clock=lambda: 1000.0, sleeper=naps.append)
        self.assertEqual(worker.request("POST", "/repos/x", {"a": 1}), {})
        self.assertEqual(len(naps), 1)
        self.assertLessEqual(naps[0], 5)


# --------------------------------------------------------------------------- #
# The driver builds the fallbacks and reports where the reads ended            #
# --------------------------------------------------------------------------- #

POOL_ENV = {
    "HARNESS_POOL_APP_ID_1": "3350400", "HARNESS_POOL_APP_PRIVATE_KEY_1": "PEM1",
    "HARNESS_POOL_APP_ID_2": "4266537", "HARNESS_POOL_APP_PRIVATE_KEY_2": "PEM2",
    "HARNESS_POOL_APP_ID_3": "4266538", "HARNESS_POOL_APP_PRIVATE_KEY_3": "PEM3",
    "HARNESS_POOL_APP_ID_4": "4266539", "HARNESS_POOL_APP_PRIVATE_KEY_4": "PEM4",
}

BASE_ENV = {
    "HARNESS_WORKER_TOKEN": "ghs-worker",
    "HARNESS_QA_TOKEN": "ghs-qa",
    "HARNESS_QA_LOGIN": "agent-bureau-qa-bot[bot]",
    "HARNESS_WAIT_DEADLINE_MINUTES": "0",
}


class DriverBuildsTheFallbacksTest(unittest.TestCase):

    def test_fallbacks_are_ordered_by_the_probes_headroom(self):
        # The probe read every slot's meter in this same job, and it chose
        # slot 3 because 3 read best. Walking slot numbers threw those
        # readings away and sent the first move to whichever slot happened to
        # come next; roomiest-first sends it where there is actually room.
        minted = []

        def fake_mint(app_id, private_key_pem, repo, **kwargs):
            minted.append((app_id, private_key_pem, repo))
            return f"ghs_{app_id}"

        env = {**POOL_ENV, "HARNESS_POOL_HEADROOM": "1:4900,2:120,3:4990,4:3100"}
        fallbacks = harness_main.pool_fallbacks(
            env, "3", REPO, mint=fake_mint, log=lambda *_: None
        )
        # 4 (3,100) before 2 (120), and slot 1 last however well it reads —
        # it is the WORKER App, whose hour the worker's own writes ride.
        self.assertEqual(
            [name for name, _ in fallbacks],
            ["pool slot 4", "pool slot 2", "pool slot 1"],
        )
        self.assertEqual(fallbacks[0][1](), "ghs_4266539")
        self.assertEqual(minted, [("4266539", "PEM4", REPO)])

    def test_unread_and_refused_slots_sort_behind_the_readable_ones(self):
        env = {**POOL_ENV, "HARNESS_POOL_HEADROOM": "1:4900,2:refused,3:10,4:unreadable"}
        fallbacks = harness_main.pool_fallbacks(
            env, "3", REPO, mint=lambda *a, **k: "t", log=lambda *_: None
        )
        # Nothing readable but slot 1 (last by rule), so: unreadable, then
        # the slot the probe was itself refused on, then the worker App.
        self.assertEqual(
            [name for name, _ in fallbacks],
            ["pool slot 4", "pool slot 2", "pool slot 1"],
        )

    def test_without_headroom_the_order_falls_back_to_slot_number(self):
        # A local run, or a selector that was not asked for the readings.
        fallbacks = harness_main.pool_fallbacks(
            POOL_ENV, "3", REPO, mint=lambda *a, **k: "t", log=lambda *_: None
        )
        self.assertEqual(
            [name for name, _ in fallbacks],
            ["pool slot 2", "pool slot 4", "pool slot 1"],
        )
        # Garbage is no readings, never a crash.
        self.assertEqual(
            [
                name
                for name, _ in harness_main.pool_fallbacks(
                    {**POOL_ENV, "HARNESS_POOL_HEADROOM": "not-a-reading"},
                    "3", REPO, mint=lambda *a, **k: "t", log=lambda *_: None,
                )
            ],
            ["pool slot 2", "pool slot 4", "pool slot 1"],
        )

    def test_pool_headroom_parses_the_selectors_line(self):
        self.assertEqual(
            harness_main.pool_headroom("1:4812,2:refused,3:unreadable,4:4990"),
            {1: 4812, 2: "refused", 3: None, 4: 4990},
        )
        self.assertEqual(harness_main.pool_headroom(""), {})
        self.assertEqual(harness_main.pool_headroom(None), {})

    def test_a_slot_missing_its_pair_is_skipped_and_an_empty_pool_is_empty(self):
        env = {k: v for k, v in POOL_ENV.items() if k.endswith(("_1", "_3"))}
        fallbacks = harness_main.pool_fallbacks(env, "1", REPO, mint=lambda *a, **k: "t", log=lambda *_: None)
        self.assertEqual([name for name, _ in fallbacks], ["pool slot 3"])
        half = {"HARNESS_POOL_APP_ID_2": "4266537"}  # id without its key
        self.assertEqual(harness_main.pool_fallbacks(half, "1", REPO, mint=lambda *a, **k: "t", log=lambda *_: None), [])
        self.assertEqual(harness_main.pool_fallbacks({}, "1", REPO, mint=lambda *a, **k: "t", log=lambda *_: None), [])

    def test_the_spend_lines_name_every_slot_and_what_each_one_paid(self):
        # Five polls as the reader: the first is refused on slot 3, the reader
        # moves to slot 4 and the remaining reads ride it. The worker's one
        # write is untouched.
        #
        # ONE LINE PER SLOT. Merging the two into `reader (pool slot 3 → pool
        # slot 4) 6 billed` put two installations' billing behind one label,
        # which is exactly the question DRE-4132's ledger exists to answer the
        # next time a slot runs dry.
        refused = []

        def opener(req):
            token = req.headers.get("Authorization")
            if token == "Bearer ghs-reader" and not refused:
                refused.append(1)
                raise _refusal(headers={"x-ratelimit-reset": "1790049858"})
            return 200, b"[]", {"ETag": '"x"'}

        minted = []

        def fake_mint(app_id, private_key_pem, repo, **kwargs):
            minted.append(app_id)
            return f"ghs_fresh_{app_id}"

        env = {
            **BASE_ENV, **POOL_ENV,
            "HARNESS_READER_TOKEN": "ghs-reader", "HARNESS_POOL_SLOT": "3",
            "HARNESS_READER_APP_ID": "4266538", "HARNESS_READER_APP_PRIVATE_KEY": "PEM3",
            "HARNESS_POOL_HEADROOM": "1:4900,2:120,3:4990,4:3100",
        }
        code, out = _drive(env, opener, fake_mint)
        self.assertEqual(code, 0, out)
        self.assertEqual(minted, ["4266539"], "the roomiest slot after 3 is 4")
        lines = [ln for ln in out.splitlines() if ln.startswith("github-spend:")]
        self.assertIn(
            "github-spend: reader (pool slot 3) 1 billed, 0 free (304 Not Modified)",
            lines,
        )
        self.assertIn(
            "github-spend: reader (pool slot 4) 5 billed, 0 free (304 Not Modified)",
            lines,
        )
        self.assertNotIn(
            "github-spend: reader (pool slot 3 → pool slot 4) 6 billed, 0 free (304 Not Modified)",
            lines,
        )
        self.assertIn("github-spend: worker 1 billed, 0 free (304 Not Modified)", lines)
        self.assertIn("github-spend: qa 0 billed, 0 free (304 Not Modified)", lines)

    def test_without_a_refusal_the_spend_line_is_exactly_the_old_one(self):
        env = {**BASE_ENV, **POOL_ENV, "HARNESS_READER_TOKEN": "ghs-reader", "HARNESS_POOL_SLOT": "3"}
        code, out = _drive(env, lambda req: (200, b"[]", {"ETag": '"x"'}), lambda *_: "unused")
        self.assertEqual(code, 0, out)
        self.assertIn("github-spend: reader (pool slot 3) 5 billed, 0 free (304 Not Modified)", out)

    def test_a_rate_limited_mint_waits_on_the_callers_sleeper(self):
        # `mint_installation_token` builds its OWN client for the two
        # JWT-authed calls, and that client's default sleeper is the real
        # `time.sleep`. Refused there, it would nap for real INSIDE the outer
        # client's recovery, unreachable by the sleeper the caller injected —
        # so the sleeper is threaded all the way down.
        naps = []
        calls = []

        def opener(req):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise _refusal(headers={"retry-after": "30"})
            if req.full_url.endswith("/installation"):
                return 200, b'{"id": 4242}', {}
            return 201, b'{"token": "ghs_minted"}', {}

        supply = harness_main.token_supplier(
            "reader (pool slot 4)", "4266539", "PEM4", REPO,
            mint=lambda *a, **kw: harness_main.app_token.mint_installation_token(
                *a, opener=opener, **kw
            ),
            log=lambda *_: None,
            sleeper=naps.append,
        )
        with mock.patch.object(harness_main.app_token, "app_jwt", lambda *a: "jwt"):
            self.assertEqual(supply(), "ghs_minted")
        self.assertEqual(len(naps), 1, "the mint's own refusal was waited out")
        self.assertGreaterEqual(naps[0], 30)
        self.assertLessEqual(naps[0], 35)


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


def _drive(env, opener, mint):
    """Run the driver's main() once over `env` with every GitHub client built
    on `opener` and every re-mint on `mint`; returns (exit code, stdout)."""

    def client(token, **kwargs):
        return github_api.GitHub(token, opener=opener, **kwargs)

    out = io.StringIO()
    with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
        harness_main, "discover",
        lambda: {_PollingScenario.name: _PollingScenario()},
    ), mock.patch.object(harness_main, "GitHub", client), mock.patch.object(
        harness_main.app_token, "mint_installation_token", mint
    ), contextlib.redirect_stdout(out):
        code = harness_main.main(
            ["--repo", REPO, "--scenarios", _PollingScenario.name, "--run-id", "gha-1-1"]
        )
    return code, out.getvalue()


# --------------------------------------------------------------------------- #
# harness.yml hands the driver every slot, and a re-run lands elsewhere        #
# --------------------------------------------------------------------------- #

class HarnessWorkflowWiringTest(unittest.TestCase):

    def setUp(self):
        doc = yaml.safe_load(HARNESS_YML.read_text(encoding="utf-8"))
        self.steps = {s.get("name") or s.get("uses"): s for s in doc["jobs"]["harness"]["steps"]}

    def test_the_scenario_step_carries_every_slots_credentials(self):
        env = self.steps["Run harness scenarios"]["env"]
        self.assertIn("secrets.BUREAU_APP_ID }}", env["HARNESS_POOL_APP_ID_1"])
        self.assertIn("secrets.BUREAU_APP_PRIVATE_KEY }}", env["HARNESS_POOL_APP_PRIVATE_KEY_1"])
        for n in (2, 3, 4):
            self.assertIn(f"secrets.BUREAU_APP_ID_{n}", env[f"HARNESS_POOL_APP_ID_{n}"])
            self.assertIn(f"secrets.BUREAU_APP_PRIVATE_KEY_{n}", env[f"HARNESS_POOL_APP_PRIVATE_KEY_{n}"])

    def test_a_rerun_hashes_to_a_different_pool_key(self):
        key = self.steps["Select dispatch-pool app"]["env"]["BUREAU_POOL_KEY"]
        self.assertIn("github.run_id", key)
        self.assertIn("github.run_attempt", key)

    def test_the_selector_is_asked_for_the_headroom_and_it_reaches_the_driver(self):
        # The readings the probe already paid for, carried from the step that
        # took them to the step that needs them.
        self.assertIn(
            "--with-headroom",
            self.steps["Select dispatch-pool app"]["run"],
        )
        env = self.steps["Run harness scenarios"]["env"]
        self.assertIn("steps.pool.outputs.headroom", env["HARNESS_POOL_HEADROOM"])

    def test_the_selector_still_prints_only_output_lines(self):
        # stdout is appended VERBATIM to $GITHUB_OUTPUT — a `headroom=` line
        # has to be one key=value with no spaces, and must carry no app id
        # and no token.
        env = {
            "BUREAU_APP_ID": "3350400", "BUREAU_APP_ID_2": "4266537",
            "BUREAU_APP_ID_3": "4266538", "BUREAU_APP_ID_4": "4266539",
            "BUREAU_APP_PRIVATE_KEY": "PRIVATE-KEY-MATERIAL",
            "BUREAU_POOL_KEY": "harness:1-1",
            "GITHUB_REPOSITORY": "dreadnought-foundry/bureau-harness",
            "BUREAU_FAKE_POOL_PROBES": json.dumps({
                "1": {"status": 200, "x-ratelimit-remaining": "4900"},
                "2": {"status": 403},
                "3": None,
                "4": {"status": 200, "x-ratelimit-remaining": "3100"},
            }),
        }
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "dispatch_pool.py"),
             "select", "--with-headroom"],
            capture_output=True, text=True, env={**os.environ, **env}, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for line in result.stdout.splitlines():
            self.assertRegex(line, r"^(n|reason|headroom)=\S+$")
        self.assertIn(
            "headroom=1:4900,2:refused,3:unreadable,4:3100",
            result.stdout.splitlines(),
        )
        self.assertNotIn("PRIVATE-KEY-MATERIAL", result.stdout + result.stderr)
        # And without the flag the six other consumers' shape is untouched.
        plain = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "dispatch_pool.py"), "select"],
            capture_output=True, text=True, env={**os.environ, **env}, check=False,
        )
        self.assertEqual(plain.returncode, 0, plain.stderr)
        for line in plain.stdout.splitlines():
            self.assertRegex(line, r"^(n|reason)=\S+$")


if __name__ == "__main__":
    unittest.main()
