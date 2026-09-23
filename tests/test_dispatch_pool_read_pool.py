"""RED-first: the main App leaves the read pool, and one probe call is not a
reading (DRE-4576).

THE DEFECT. Every write the fleet makes on GitHub — merges, verdicts,
receipts, the agents' own `gh` calls — has to come from the main App
(installation 123249480), because that identity is the one the gates
recognise. Today that same App is slot 1 of the read pool, so the sweeps, the
critics and the harness spend its hour on polling too.

  * Harness run 35664409350 (`eabb37da`), attempts 1-4: the pool probe read
    slot 1 at 4,454 / 4,415 / 3,606 / 4,432 remaining and chose it (`spread`);
    every call the harness then made was refused with `API rate limit exceeded
    for installation ID 123249480`. Runner probes elsewhere the same evening
    read the same slot `refused/reset-in-16m` (bureau-harness Reconcile
    35669089551, 23:47Z) and `refused/reset-in-5m` (35674045821, 00:58Z).
  * One token, one endpoint, 20 calls in a row from the operator's machine at
    20:29-20:31 PT answered from two different counters: ~60% `used ~60, reset
    21:28 PT`, ~40% `used ~3,385, reset 21:04 PT`. A single probe call reports
    the healthy counter roughly half the time whatever the state of the other.

WHAT THIS SUITE PINS.
  * `BUREAU_POOL_READ_ONLY=1` makes `choose()` hold slot 1 back: it is never
    returned while any other slot is readable, and it is returned only when
    nothing else is — a last resort, never an exclusion that fails a run.
  * The `dispatch-pool:` log line says `(read pool, slot 1 held back)` on that
    path, and the `reason=` output stays the same space-free rule vocabulary
    seven workflows append to `$GITHUB_OUTPUT`.
  * Without the flag every rule is exactly today's.
  * `_probe_real` samples each candidate `BUREAU_POOL_PROBE_SAMPLES` times
    (default `PROBE_SAMPLES` = 2): any refused sample marks the slot refused,
    otherwise the SMALLEST `x-ratelimit-remaining` is the reading.
  * The flag is set on the three selector sites whose token only ever reads,
    and the check that it is nowhere else is DISCOVERED from the workflows —
    a site whose pool pick also feeds a write-capable mint fails this suite if
    it carries the flag, whatever anybody remembers about it.

THE SEVEN READER-MINT SITES, in or out:

  | workflow             | flag | why                                          |
  | -------------------- | ---- | -------------------------------------------- |
  | harness.yml          | yes  | the pick feeds `reader` only; the driver's    |
  |                      |      | writes ride `HARNESS_WORKER_TOKEN`            |
  | qa-review.yml        | yes  | the pick feeds `reader` only; the verdict     |
  |                      |      | comment and the check run keep the qa-bot     |
  | reconcile.yml        | yes  | the pick feeds `reader` only; the sweep's     |
  |                      |      | receipts stay on `GH_TOKEN`                   |
  | agent-fix.yml        | no   | the same pick also mints `worker`, the fix    |
  |                      |      | agent's write-capable model token (DRE-4412)  |
  | plan.yml             | no   | the same pick is re-minted for every planner  |
  |                      |      | and critic model step (DRE-3940)              |
  | verify.yml           | no   | no separate reading step — the pick IS the    |
  |                      |      | verifier's worker token                       |
  | red-main-repair.yml  | no   | no separate reading step — the pick IS the    |
  |                      |      | repair worker's token                         |

(`agent-task.yml` is the eighth consumer and the original worker mint; it is
covered here only by the "nothing else carries the flag" direction.)
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

#: Every workflow that consults the selector (test_dispatch_pool_real_meter's
#: CONSUMERS).
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

#: The three whose `Select dispatch-pool app` pick is used for reads alone.
READ_ONLY_SITES = ("harness.yml", "qa-review.yml", "reconcile.yml")

MINT = "actions/create-github-app-token"

#: The id every consumer gives the mint whose token only ever READS. Any OTHER
#: mint fed by the same pick is write-capable until somebody proves otherwise —
#: `worker` (verify, red-main-repair, agent-fix) and plan's `app_*` re-mints
#: all author or push. Fail-closed on purpose: an unknown new mint id on a
#: flagged site is a finding, not a pass.
READ_MINT_IDS = frozenset({"reader"})


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


def refused(reset: int | None = None) -> dispatch_pool.Reading:
    return dispatch_pool.Reading(None, reset, refused=True)


# --------------------------------------------------------------------------- #
# AC 1 — the read pool holds slot 1 back                                        #
# --------------------------------------------------------------------------- #

class ReadPoolHoldsSlotOneBackTest(unittest.TestCase):

    def test_the_roomiest_slot_is_never_slot_one_while_a_spare_is_readable(self):
        # Harness run 35664409350: slot 1 read ~4,4xx and was chosen every
        # attempt. With the flag the spares take every one of those picks.
        readings = {1: 4454, 2: 100, 3: 90, 4: 80}
        for i in range(50):
            slot, _ = dispatch_pool.choose(
                readings, key=f"harness:{i}", read_only=True
            )
            self.assertNotEqual(slot, 1, f"key harness:{i} spent the main App's hour")

    def test_the_spares_still_share_the_load_among_themselves(self):
        readings = {1: 5000, 2: 4900, 3: 4800, 4: 4700}
        picks = set()
        for i in range(60):
            slot, reason = dispatch_pool.choose(
                readings, key=f"DRE-{i}", read_only=True
            )
            self.assertEqual(reason, "spread")
            picks.add(slot)
        self.assertEqual(picks, {2, 3, 4})

    def test_the_drained_spare_is_still_never_chosen(self):
        # Holding slot 1 back must not disable the ranking underneath it.
        slot, reason = dispatch_pool.choose(
            {1: 5000, 2: 200, 3: 1500, 4: 100}, key="x", read_only=True
        )
        self.assertEqual((slot, reason), (3, "max-remaining"))

    def test_a_refused_spare_is_out_and_slot_one_still_does_not_take_its_place(self):
        readings = {1: 5000, 2: refused(), 3: 4800, 4: 4700}
        picks = {
            dispatch_pool.choose(readings, key=f"DRE-{i}", read_only=True)[0]
            for i in range(50)
        }
        self.assertEqual(picks, {3, 4})

    def test_one_readable_spare_is_the_only_readable_even_beside_a_roomier_slot_one(self):
        readings = {1: 5000, 2: refused(), 3: None, 4: 12}
        self.assertEqual(
            dispatch_pool.choose(readings, key="x", read_only=True),
            (4, "only-readable"),
        )

    def test_slot_one_is_the_last_resort_when_no_spare_is_readable(self):
        # Every other slot refused or unreadable: the run must still get a
        # token, and the only thing known to work is slot 1.
        readings = {1: 4500, 2: refused(), 3: None, 4: refused()}
        self.assertEqual(
            dispatch_pool.choose(readings, key="x", read_only=True),
            (1, "only-readable"),
        )

    def test_nothing_readable_anywhere_still_falls_back_across_the_unrefused(self):
        readings = {1: None, 2: refused(), 3: None, 4: refused()}
        picks = {
            dispatch_pool.choose(readings, key=f"DRE-{i}", read_only=True)[0]
            for i in range(50)
        }
        self.assertEqual(picks, {1, 3}, "the blind hash keeps slot 1 as a last resort")

    def test_a_pool_of_two_does_not_read_as_a_single_app_once_slot_one_is_held(self):
        # Holding slot 1 back leaves ONE candidate — that is `only-readable`,
        # not the `single-app` short-circuit, which means "no pool at all".
        self.assertEqual(
            dispatch_pool.choose({1: 5000, 2: 4000}, key="x", read_only=True),
            (2, "only-readable"),
        )

    def test_a_pool_that_is_only_slot_one_is_untouched(self):
        self.assertEqual(
            dispatch_pool.choose({1: 4000}, key="x", read_only=True), (1, "single-app")
        )

    def test_without_the_flag_every_rule_is_exactly_todays(self):
        cases = [
            {1: 5000, 2: 4900, 3: 4800, 4: 4700},
            {1: 4454, 2: 100, 3: 90, 4: 80},
            {1: 5000, 2: refused(), 3: None, 4: 12},
            {1: None, 2: refused(), 3: None, 4: refused()},
            {1: 5000, 2: 4000},
        ]
        for readings in cases:
            for i in range(10):
                key = f"DRE-{i}"
                with self.subTest(readings=sorted(readings), key=key):
                    self.assertEqual(
                        dispatch_pool.choose(readings, key=key, read_only=False),
                        dispatch_pool.choose(readings, key=key),
                    )

    def test_the_flag_is_read_off_the_env_as_exactly_one(self):
        self.assertTrue(dispatch_pool.read_only_pool({"BUREAU_POOL_READ_ONLY": "1"}))
        for value in ("", "0", "false", "no", " "):
            with self.subTest(value=value):
                self.assertFalse(
                    dispatch_pool.read_only_pool({"BUREAU_POOL_READ_ONLY": value})
                )
        self.assertFalse(dispatch_pool.read_only_pool({}))

    def test_select_honours_the_flag_end_to_end(self):
        env = pool_env(
            BUREAU_POOL_KEY="reconcile:35669089551",
            BUREAU_POOL_READ_ONLY="1",
            BUREAU_FAKE_POOL_PROBES=json.dumps({
                "1": {"status": 200, "x-ratelimit-remaining": "4454"},
                "2": {"status": 200, "x-ratelimit-remaining": "4997"},
                "3": {"status": 200, "x-ratelimit-remaining": "4990"},
                "4": {"status": 200, "x-ratelimit-remaining": "4980"},
            }),
        )
        slot, reason = dispatch_pool.select(env)
        self.assertIn(slot, (2, 3, 4))
        self.assertEqual(reason, "spread")
        # …and the same probe readings without the flag can answer slot 1.
        del env["BUREAU_POOL_READ_ONLY"]
        picks = set()
        for i in range(30):
            picks.add(dispatch_pool.select({**env, "BUREAU_POOL_KEY": f"DRE-{i}"})[0])
        self.assertIn(1, picks, "unflagged, slot 1 is an ordinary candidate")


# --------------------------------------------------------------------------- #
# AC 1 (cont.) — the log line, and the output contract it must not break        #
# --------------------------------------------------------------------------- #

def run_cli(env: dict, *args: str) -> subprocess.CompletedProcess:
    full_env = {"PATH": os.environ.get("PATH", "")}
    full_env.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "select", *args],
        env=full_env, capture_output=True, text=True, timeout=30,
    )


HELD_BACK = "(read pool, slot 1 held back)"


class LogLineSaysTheSlotWasHeldBackTest(unittest.TestCase):

    PROBES = json.dumps({
        "1": {"status": 200, "x-ratelimit-remaining": "4454"},
        "2": {"status": 200, "x-ratelimit-remaining": "4997"},
        "3": {"status": 200, "x-ratelimit-remaining": "4990"},
        "4": {"status": 200, "x-ratelimit-remaining": "4980"},
    })

    def _line(self, result):
        lines = [l for l in result.stderr.splitlines() if l.startswith("dispatch-pool:")]
        self.assertEqual(len(lines), 1, result.stderr)
        return lines[0]

    def test_the_line_says_it_and_still_names_every_reading_and_the_rule(self):
        result = run_cli(pool_env(
            BUREAU_POOL_KEY="harness:35664409350-1",
            BUREAU_POOL_READ_ONLY="1",
            BUREAU_FAKE_POOL_PROBES=self.PROBES,
        ))
        self.assertEqual(result.returncode, 0, result.stderr)
        line = self._line(result)
        self.assertIn(HELD_BACK, line)
        self.assertIn("slot1=4,454", line)
        self.assertIn("slot2=4,997", line)
        self.assertRegex(line, r"→ selected slot [234] \(spread\) ")

    def test_the_output_lines_keep_the_space_free_shape_seven_workflows_append(self):
        result = run_cli(pool_env(
            BUREAU_POOL_KEY="harness:35664409350-1",
            BUREAU_POOL_READ_ONLY="1",
            BUREAU_FAKE_POOL_PROBES=self.PROBES,
        ), "--with-headroom")
        self.assertEqual(result.returncode, 0, result.stderr)
        for line in result.stdout.splitlines():
            self.assertRegex(line, r"^(n|reason|headroom)=\S+$")
        self.assertIn("reason=spread", result.stdout.splitlines())

    def test_the_line_does_not_say_it_when_slot_one_was_never_a_candidate(self):
        # Nothing was held back: slot 1 is the only readable slot and the
        # last resort took it. Saying otherwise would be a lie in the log.
        result = run_cli(pool_env(
            BUREAU_POOL_KEY="DRE-4576",
            BUREAU_POOL_READ_ONLY="1",
            BUREAU_FAKE_POOL_PROBES=json.dumps({
                "1": {"status": 200, "x-ratelimit-remaining": "4454"},
                "2": {"status": 403},
                "3": None,
                "4": {"status": 429},
            }),
        ))
        line = self._line(result)
        self.assertNotIn(HELD_BACK, line)
        self.assertTrue(line.endswith("→ selected slot 1 (only-readable)"), line)

    def test_an_unflagged_run_never_says_it(self):
        result = run_cli(pool_env(
            BUREAU_POOL_KEY="harness:35664409350-1",
            BUREAU_FAKE_POOL_PROBES=self.PROBES,
        ))
        self.assertNotIn(HELD_BACK, self._line(result))


# --------------------------------------------------------------------------- #
# AC 3 — the probe samples each candidate twice                                 #
# --------------------------------------------------------------------------- #

class _Response(io.BytesIO):
    def __init__(self, status: int, headers: dict):
        super().__init__(b"{}")
        self.status = status
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def scripted(*responses, calls: list | None = None):
    """A `urlopen` that answers the given per-call meter readings in order.

    Each entry is an `x-ratelimit-remaining` value, or a refusal status
    (403/429) — the two counters GitHub answers one token from.
    """
    queue = list(responses)

    def fake_urlopen(req, timeout=None):
        if calls is not None:
            calls.append(req.full_url)
        answer = queue.pop(0) if queue else queue
        headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1758250000"}
        if answer in (403, 429):
            raise urllib.error.HTTPError(
                req.full_url, answer, "rate limit exceeded", headers, io.BytesIO(b"{}")
            )
        if answer is None:
            raise OSError("connection reset")
        return _Response(200, {"X-RateLimit-Remaining": str(answer),
                               "X-RateLimit-Reset": "1758250000"})

    return fake_urlopen


class ProbeSamplesTwiceTest(unittest.TestCase):

    def test_the_sample_count_is_a_named_constant_defaulting_to_two(self):
        self.assertEqual(dispatch_pool.PROBE_SAMPLES, 2)
        self.assertEqual(dispatch_pool.probe_samples({}), 2)

    def test_two_calls_per_candidate_and_the_smaller_reading_wins(self):
        # The operator's 20-call measurement: the same token answered ~60%
        # `used ~60` and ~40% `used ~3,385`. One sample reports the healthy
        # counter half the time; the pessimistic sample is the one that counts.
        calls: list = []
        with mock.patch.object(urllib.request, "urlopen",
                               scripted(4940, 1615, calls=calls)):
            reading = dispatch_pool._probe_real("ghs_slot1", REPO)
        self.assertEqual(len(calls), 2)
        for url in calls:
            self.assertEqual(url, f"https://api.github.com/repos/{REPO}")
        self.assertEqual(reading.remaining, 1615)
        self.assertFalse(reading.refused)

    def test_the_order_of_the_two_counters_does_not_matter(self):
        with mock.patch.object(urllib.request, "urlopen", scripted(1615, 4940)):
            self.assertEqual(dispatch_pool._probe_real("ghs_slot1", REPO).remaining, 1615)

    def test_any_refused_sample_marks_the_slot_refused(self):
        for answers in ((403, 4940), (4940, 429)):
            with self.subTest(answers=answers):
                with mock.patch.object(urllib.request, "urlopen", scripted(*answers)):
                    reading = dispatch_pool._probe_real("ghs_slot1", REPO)
                self.assertTrue(reading.refused)
                self.assertIsNone(reading.remaining)
                self.assertEqual(reading.reset, 1758250000)

    def test_a_refusal_on_the_first_sample_spends_no_second_call(self):
        calls: list = []
        with mock.patch.object(urllib.request, "urlopen",
                               scripted(403, 4940, calls=calls)):
            dispatch_pool._probe_real("ghs_slot1", REPO)
        self.assertEqual(len(calls), 1, "a refused bucket is out; stop paying")

    def test_one_unreadable_sample_does_not_throw_the_other_away(self):
        with mock.patch.object(urllib.request, "urlopen", scripted(None, 3606)):
            self.assertEqual(dispatch_pool._probe_real("ghs_slot1", REPO).remaining, 3606)

    def test_every_sample_unreadable_is_an_unreadable_slot(self):
        with mock.patch.object(urllib.request, "urlopen", scripted(None, None)):
            reading = dispatch_pool._probe_real("ghs_slot1", REPO)
        self.assertIsNone(reading.remaining)
        self.assertFalse(reading.refused)

    def test_the_count_is_tunable_by_env(self):
        self.assertEqual(
            dispatch_pool.probe_samples({"BUREAU_POOL_PROBE_SAMPLES": "3"}), 3
        )
        calls: list = []
        with mock.patch.object(urllib.request, "urlopen",
                               scripted(4940, 3606, 1615, calls=calls)):
            with mock.patch.dict(os.environ, {"BUREAU_POOL_PROBE_SAMPLES": "3"}):
                reading = dispatch_pool._probe_real("ghs_slot1", REPO)
        self.assertEqual(len(calls), 3)
        self.assertEqual(reading.remaining, 1615)

    def test_a_nonsense_or_absent_count_falls_back_to_the_constant(self):
        for value in ("", "lots", "0", "-1"):
            with self.subTest(value=value):
                self.assertEqual(
                    dispatch_pool.probe_samples({"BUREAU_POOL_PROBE_SAMPLES": value}),
                    dispatch_pool.PROBE_SAMPLES,
                )

    def test_the_docstring_states_the_new_per_run_cost(self):
        # standards/engineering.md: a change that contradicts a document
        # updates that document in the SAME PR. The cost line is the document.
        doc = dispatch_pool.__doc__ or ""
        self.assertIn("per candidate per run", doc)
        self.assertNotIn("one request per candidate per run", doc)
        self.assertIn("two requests per candidate per run", doc)
        self.assertIn("BUREAU_POOL_PROBE_SAMPLES", doc)
        self.assertIn("BUREAU_POOL_READ_ONLY", doc)

    def test_no_document_still_calls_the_probe_one_call_per_candidate(self):
        for label in ("docs/vendor-boundary-audit-2026-07.md",
                      "scripts/harness/README.md"):
            with self.subTest(doc=label):
                text = (ROOT / label).read_text(encoding="utf-8")
                self.assertNotIn("one real, counted call per candidate", text)
                self.assertNotIn("with one real call", text)


# --------------------------------------------------------------------------- #
# AC 4 — the flag is on the three read-only sites and nowhere else              #
# --------------------------------------------------------------------------- #

def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def all_steps(name: str) -> list[dict]:
    out = []
    for job in load(name)["jobs"].values():
        out.extend(job.get("steps") or [])
    return out


def selector(name: str) -> dict:
    for step in all_steps(name):
        if "dispatch_pool.py select" in (step.get("run") or ""):
            return step
    raise AssertionError(f"{name}: no dispatch_pool.py select step")


def flagged(name: str) -> bool:
    return str((selector(name).get("env") or {}).get("BUREAU_POOL_READ_ONLY", "")) == "1"


def pool_fed_mints(name: str) -> dict[str, str]:
    """`id -> step name` for every App-token mint whose inputs interpolate the
    selector's choice. Mints only: harness's scenario step also carries the
    slot, but it is the driver's re-mint map, not an identity minted here."""
    found = {}
    for step in all_steps(name):
        if MINT not in str(step.get("uses") or ""):
            continue
        rendered = yaml.safe_dump(step.get("with") or {})
        if "steps.pool.outputs.n" in rendered:
            found[str(step.get("id"))] = str(step.get("name"))
    return found


def write_capable_mints(name: str) -> dict[str, str]:
    return {i: n for i, n in pool_fed_mints(name).items() if i not in READ_MINT_IDS}


class TheSevenSitesTest(unittest.TestCase):

    def test_exactly_the_three_read_only_sites_carry_the_flag(self):
        for name in CONSUMERS:
            with self.subTest(workflow=name):
                self.assertEqual(
                    flagged(name), name in READ_ONLY_SITES,
                    f"{name}: BUREAU_POOL_READ_ONLY is "
                    f"{'set' if flagged(name) else 'unset'} and should not be",
                )

    def test_the_flag_is_set_on_the_selector_step_and_nowhere_else(self):
        for name in READ_ONLY_SITES:
            with self.subTest(workflow=name):
                carriers = [
                    step.get("name")
                    for step in all_steps(name)
                    if "BUREAU_POOL_READ_ONLY" in (step.get("env") or {})
                ]
                self.assertEqual(carriers, ["Select dispatch-pool app"])

    def test_a_site_whose_pick_also_mints_a_write_capable_identity_is_never_flagged(self):
        # DISCOVERED, not remembered: adding the flag to a workflow whose
        # selector also feeds `worker` or a `Re-mint bot token` step fails
        # here, and so does adding such a mint to a flagged workflow.
        for name in CONSUMERS:
            with self.subTest(workflow=name):
                writers = write_capable_mints(name)
                if writers:
                    self.assertFalse(
                        flagged(name),
                        f"{name}: the pool pick also mints {sorted(writers)} — "
                        "that token writes, so the read pool may not hold slot 1 back",
                    )

    def test_each_flagged_site_feeds_a_read_mint_and_only_a_read_mint(self):
        for name in READ_ONLY_SITES:
            with self.subTest(workflow=name):
                self.assertEqual(sorted(pool_fed_mints(name)), ["reader"])

    def test_the_four_excluded_sites_are_excluded_for_the_reason_the_card_gives(self):
        # verify and red-main-repair have no separate reading step at all:
        # the pick IS the worker token.
        for name in ("verify.yml", "red-main-repair.yml"):
            with self.subTest(workflow=name):
                self.assertEqual(sorted(pool_fed_mints(name)), ["worker"])
        # agent-fix and plan reuse the reading pick for a write.
        for name in ("agent-fix.yml", "plan.yml"):
            with self.subTest(workflow=name):
                mints = pool_fed_mints(name)
                self.assertIn("reader", mints)
                self.assertTrue(write_capable_mints(name), mints)

    def test_the_flag_reaches_the_selector_as_a_string_one(self):
        # `env:` values are strings to Actions; a YAML bare 1 would render as
        # "1" too, but the quoted form is what the module's `== "1"` reads.
        for name in READ_ONLY_SITES:
            with self.subTest(workflow=name):
                raw = (WORKFLOWS / name).read_text(encoding="utf-8")
                self.assertIn('BUREAU_POOL_READ_ONLY: "1"', raw)


if __name__ == "__main__":
    unittest.main()
