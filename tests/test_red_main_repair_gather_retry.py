"""Red-main repair — the attempt-record reads wait out the call limit (DRE-6963).

On 2026-10-11 the fleet's GitHub login ran out of calls while main was red.
Red-Main Repair run 38169396180 could not read its own attempt records, said
only `attempt records unreadable`, ended GREEN having dispatched nothing, and
nothing would have started it again while main stayed red: no pull request
merges onto a red main, so no next run on main comes. The step that made the
reads threw the reason away (`2>/dev/null || echo 'FETCH-FAILED'`).

`red_main_repair.gather_reads` is the one function that now makes those
reads. These tests drive it with a fake reader and a fake clock, so nothing
here waits:

  * a failed read is said, one line per read per try: which read, the HTTP
    status, the message's first line — never the token;
  * after a failed try it reads the login's own rate limit and waits until
    the reset plus ten seconds (two minutes when the rate limit cannot be
    read), then reads again: at most three tries, at most twenty minutes;
  * a read that never succeeds leaves the marker the decide step reads as
    `records-unreadable`, and `gather-records` exits non-zero with the one
    line that says why — a failed run, never a green "nothing to do";
  * the comparisons (`gather-compares`) ride the same retry.
"""

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS)

import red_main_repair  # noqa: E402

REPO = "dreadnought-foundry/bureau-pipeline"
REFS_PATH = f"repos/{REPO}/git/matching-refs/heads/repair/"
PULLS_PATH = f"repos/{REPO}/pulls?state=all&per_page=100"
SHA = "a" * 40
RECORD = f"repair/DRE-6964-{SHA[:12]}"

NOW = 1_791_000_000
FAKE_TOKEN = "ghs_FAKEtokenDRE6963notARealValue0000"

# The answer GitHub gave the 12:50:09 PT replay on 2026-10-11, as `gh api`
# hands it over: the JSON body on stdout, its own one-liner on stderr.
LIMIT_BODY = json.dumps({
    "message": "API rate limit exceeded for installation ID 123249480. "
               "If you reach out to GitHub Support for help, please include "
               "the request ID 0000:0000:0000000:0000000:00000000.",
    "documentation_url": "https://docs.github.com/rest/overview/"
                         "rate-limits-for-the-rest-api",
    "status": "403",
})
LIMIT_ERR = ("gh: API rate limit exceeded for installation ID 123249480. "
             "(HTTP 403)\n")
LIMITED = (1, LIMIT_BODY, LIMIT_ERR)


def rate_limit_answer(reset: int, remaining: int = 0):
    return (0, json.dumps({
        "resources": {"core": {"limit": 5000, "used": 5000 - remaining,
                               "remaining": remaining, "reset": reset}},
        "rate": {"limit": 5000, "remaining": remaining, "reset": reset},
    }), "")


class Clock:
    """`now` and `sleep` for the function under test: sleeping moves time."""

    def __init__(self, start=NOW):
        self.t = start
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


class FakeReader:
    """`gh api <path>` as the function sees it: `(rc, stdout, stderr)`.

    `answers[path]` is a list consumed one per call (the last one repeats);
    `rate_limit` answers the login's own rate-limit read, given the clock.
    """

    def __init__(self, answers, rate_limit=None):
        self.answers = {k: list(v) for k, v in answers.items()}
        self.rate_limit = rate_limit
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        if path == "rate_limit":
            return self.rate_limit()
        queue = self.answers[path]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def reads_of(self, path):
        return sum(1 for c in self.calls if c == path)


def gather(reads, reader, clock, **kw):
    said = []
    result = red_main_repair.gather_reads(
        reads, reader=reader, sleep=clock.sleep, now=clock.now,
        say=said.append, **kw)
    return result, said


class RetriesAndSucceedsTest(unittest.TestCase):
    """AC 1: two rate-limited tries, then the read comes back."""

    def setUp(self):
        self.clock = Clock()
        # The limit resets 50s after whatever "now" is when it is asked.
        self.reader = FakeReader(
            {REFS_PATH: [LIMITED, LIMITED, (0, "[]", "")]},
            rate_limit=lambda: rate_limit_answer(self.clock.now() + 50))
        self.result, self.said = gather({"refs": REFS_PATH}, self.reader,
                                        self.clock)

    def test_it_returns_the_records(self):
        self.assertEqual(self.result["bodies"], {"refs": "[]"})
        self.assertEqual(self.result["failed"], {})
        self.assertEqual(self.result["tries"], 3)

    def test_it_sleeps_twice_until_the_reset_plus_ten_seconds(self):
        self.assertEqual(self.clock.sleeps, [60, 60])
        self.assertEqual(self.reader.reads_of(REFS_PATH), 3)
        self.assertEqual(self.reader.reads_of("rate_limit"), 2)

    def test_it_prints_two_reason_lines_naming_the_read(self):
        reasons = [line for line in self.said
                   if line.startswith("repair gather: read refs failed")]
        self.assertEqual(len(reasons), 2, self.said)
        for line in reasons:
            self.assertIn("HTTP 403", line)
            self.assertIn("API rate limit exceeded for installation ID "
                          "123249480", line)
        self.assertIn("try 1/3", reasons[0])
        self.assertIn("try 2/3", reasons[1])

    def test_the_reason_is_the_first_line_only(self):
        reader = FakeReader(
            {REFS_PATH: [(1, "", "gh: Bad credentials (HTTP 401)\nsecond "
                                  "line\nthird line\n"), (0, "[]", "")]},
            rate_limit=lambda: rate_limit_answer(NOW))
        _, said = gather({"refs": REFS_PATH}, reader, Clock())
        (line,) = [s for s in said if "read refs failed" in s]
        self.assertIn("HTTP 401: Bad credentials", line)
        self.assertNotIn("second line", line)


class NeverSucceedsTest(unittest.TestCase):
    """AC 2: three tries, twenty minutes at most, then records-unreadable."""

    def test_three_tries_and_never_more_than_twenty_minutes(self):
        clock = Clock()
        # A reset an hour away: waiting it out in full would blow the budget.
        reader = FakeReader({REFS_PATH: [LIMITED]},
                            rate_limit=lambda: rate_limit_answer(NOW + 3600))
        result, said = gather({"refs": REFS_PATH}, reader, clock)
        self.assertEqual(reader.reads_of(REFS_PATH), 3)
        self.assertEqual(result["tries"], 3)
        self.assertLessEqual(sum(clock.sleeps), 20 * 60)
        self.assertEqual(red_main_repair.GATHER_BUDGET_SECONDS, 20 * 60)
        self.assertEqual(red_main_repair.GATHER_TRIES, 3)
        self.assertIsNone(result["bodies"]["refs"])
        self.assertIn("refs", result["failed"])
        self.assertIn("HTTP 403", result["failed"]["refs"])

    def test_the_command_exits_non_zero_and_decide_reads_records_unreadable(self):
        clock = Clock()
        reader = FakeReader({REFS_PATH: [LIMITED], PULLS_PATH: [LIMITED]},
                            rate_limit=lambda: rate_limit_answer(NOW + 3600))
        said = []
        with tempfile.TemporaryDirectory() as td:
            refs = os.path.join(td, "refs.json")
            pulls = os.path.join(td, "pulls.json")
            rc = red_main_repair.gather_records(
                REPO, refs, pulls, reader=reader, sleep=clock.sleep,
                now=clock.now, say=said.append)
            self.assertNotEqual(rc, 0)
            self.assertLessEqual(sum(clock.sleeps), 20 * 60)
            self.assertIsNone(red_main_repair._load_records(refs, pulls))
            decided = _decide_cli(td, refs, pulls)
        self.assertEqual(decided.returncode, 0, decided.stderr)
        self.assertIn("reason=records-unreadable", decided.stdout)
        self.assertIn("go=false", decided.stdout)
        summary = [s for s in said if s.startswith("::error::")]
        self.assertEqual(len(summary), 1, said)
        self.assertIn("the repair could not read its attempt records after "
                      "3 tries: ", summary[0])
        self.assertIn("HTTP 403: API rate limit exceeded", summary[0])
        self.assertEqual(len(summary[0].splitlines()), 1)

    def test_a_read_that_comes_back_writes_the_records_and_exits_0(self):
        clock = Clock()
        pulls_body = json.dumps([{"head": {"ref": RECORD}, "state": "open",
                                  "merged_at": None}])
        reader = FakeReader(
            {REFS_PATH: [(0, json.dumps([{"ref": f"refs/heads/{RECORD}"}]),
                          "")],
             PULLS_PATH: [LIMITED, (0, pulls_body, "")]},
            rate_limit=lambda: rate_limit_answer(NOW + 30))
        said = []
        with tempfile.TemporaryDirectory() as td:
            refs = os.path.join(td, "refs.json")
            pulls = os.path.join(td, "pulls.json")
            rc = red_main_repair.gather_records(
                REPO, refs, pulls, reader=reader, sleep=clock.sleep,
                now=clock.now, say=said.append)
            loaded = red_main_repair._load_records(refs, pulls)
        self.assertEqual(rc, 0, said)
        self.assertEqual(loaded, ([RECORD], [
            {"head_ref": RECORD, "state": "open", "merged": False}]))
        # The read that worked is not made again.
        self.assertEqual(reader.reads_of(REFS_PATH), 1)
        self.assertEqual(reader.reads_of(PULLS_PATH), 2)
        self.assertEqual(clock.sleeps, [40])
        self.assertFalse([s for s in said if s.startswith("::error::")])


class RateLimitReadFailsTest(unittest.TestCase):
    """AC 3: no rate limit to read means a two-minute wait."""

    def test_each_wait_is_two_minutes(self):
        clock = Clock()
        reader = FakeReader({REFS_PATH: [LIMITED]},
                            rate_limit=lambda: LIMITED)
        gather({"refs": REFS_PATH}, reader, clock)
        self.assertEqual(clock.sleeps, [120, 120])
        self.assertEqual(red_main_repair.RATE_LIMIT_FALLBACK_SECONDS, 120)

    def test_an_unreadable_rate_limit_answer_waits_two_minutes_too(self):
        clock = Clock()
        reader = FakeReader({REFS_PATH: [LIMITED]},
                            rate_limit=lambda: (0, "not json", ""))
        gather({"refs": REFS_PATH}, reader, clock)
        self.assertEqual(clock.sleeps, [120, 120])

    def test_a_limit_with_calls_left_is_not_waited_out_to_the_reset(self):
        # A 502 or a secondary limit with the hourly meter still full: the
        # reset is up to an hour away and is not what failed.
        clock = Clock()
        reader = FakeReader(
            {REFS_PATH: [(1, "", "gh: HTTP 502: Bad Gateway (HTTP 502)\n")]},
            rate_limit=lambda: rate_limit_answer(NOW + 3000, remaining=4900))
        gather({"refs": REFS_PATH}, reader, clock)
        self.assertEqual(clock.sleeps, [120, 120])


class NoTokenPrintedTest(unittest.TestCase):
    """AC 4: the reason lines never carry the token, even when the error
    text a read hands back does."""

    LEAKY = (1, json.dumps({"message": f"Bad credentials {FAKE_TOKEN}"}),
             f"gh: Bad credentials for {FAKE_TOKEN} (HTTP 401)\n")

    def test_the_token_from_the_environment_is_never_said(self):
        clock = Clock()
        reader = FakeReader({REFS_PATH: [self.LEAKY],
                             PULLS_PATH: [self.LEAKY]},
                            rate_limit=lambda: self.LEAKY)
        said = []
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"GH_TOKEN": FAKE_TOKEN}), \
                tempfile.TemporaryDirectory() as td, redirect_stdout(out):
            rc = red_main_repair.gather_records(
                REPO, os.path.join(td, "r"), os.path.join(td, "p"),
                reader=reader, sleep=clock.sleep, now=clock.now,
                say=said.append)
        self.assertNotEqual(rc, 0)
        self.assertTrue(said)
        printed = "\n".join(said) + out.getvalue()
        self.assertNotIn(FAKE_TOKEN, printed)
        self.assertIn("Bad credentials", printed)

    def test_the_command_run_end_to_end_never_prints_the_token(self):
        # The real CLI and the real reader, over a `gh` that answers every
        # read with the limit and echoes the token it was handed into its own
        # error text. The rate limit has already reset, so nothing waits.
        with tempfile.TemporaryDirectory() as td:
            gh = os.path.join(td, "gh")
            with open(gh, "w") as fh:
                fh.write(FAKE_GH)
            os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC)
            env = {**os.environ, "PATH": f"{td}{os.pathsep}{os.environ['PATH']}",
                   "GH_TOKEN": FAKE_TOKEN, "FAKE_RESET": str(NOW)}
            refs = os.path.join(td, "refs.json")
            pulls = os.path.join(td, "pulls.json")
            done = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS, "red_main_repair.py"),
                 "gather-records", "--repo", REPO,
                 "--refs-out", refs, "--pulls-out", pulls],
                capture_output=True, text=True, env=env, timeout=60)
            self.assertIsNone(red_main_repair._load_records(refs, pulls))
        self.assertNotEqual(done.returncode, 0)
        self.assertNotIn(FAKE_TOKEN, done.stdout + done.stderr)
        self.assertIn("::error::the repair could not read its attempt records "
                      "after 3 tries: ", done.stdout)
        self.assertIn("HTTP 403", done.stdout)
        self.assertEqual(done.stdout.count("repair gather: read refs failed"), 3)


FAKE_GH = """\
#!/usr/bin/env python3
import json, os, sys
token = os.environ.get("GH_TOKEN", "")
if sys.argv[1:3] == ["api", "rate_limit"]:
    reset = int(os.environ["FAKE_RESET"])
    print(json.dumps({"resources": {"core": {"remaining": 0, "reset": reset}}}))
    sys.exit(0)
print(json.dumps({"message": "API rate limit exceeded for installation ID "
                             "123249480. token " + token}))
sys.stderr.write("gh: API rate limit exceeded for installation ID 123249480. "
                 "(" + token + ") (HTTP 403)\\n")
sys.exit(1)
"""


class ComparesRideTheSameRetryTest(unittest.TestCase):
    """The third gather: each record branch compared with the default branch."""

    COMPARE = f"repos/{REPO}/compare/main...{RECORD}"

    def _run(self, compare_answers, rate_limit):
        clock = Clock()
        reader = FakeReader({self.COMPARE: compare_answers},
                            rate_limit=rate_limit)
        said = []
        with tempfile.TemporaryDirectory() as td:
            refs = os.path.join(td, "refs.json")
            out = os.path.join(td, "compares.json")
            with open(refs, "w") as fh:
                json.dump([{"ref": f"refs/heads/{RECORD}"},
                           {"ref": "refs/heads/repair/" + "b" * 40}], fh)
            rc = red_main_repair.gather_compares(
                REPO, "main", SHA, refs, out, reader=reader,
                sleep=clock.sleep, now=clock.now, say=said.append)
            with open(out) as fh:
                doc = json.load(fh)
        return rc, doc, reader, clock, said

    def test_a_compare_that_comes_back_on_a_later_try_is_kept(self):
        payload = json.dumps({"status": "ahead", "ahead_by": 1,
                              "behind_by": 0, "files": [{"x": 1}]})
        rc, doc, reader, clock, said = self._run(
            [LIMITED, (0, payload, "")],
            rate_limit=lambda: rate_limit_answer(NOW + 20))
        self.assertEqual(rc, 0)
        self.assertEqual(doc, {RECORD: {"status": "ahead", "ahead_by": 1}})
        self.assertEqual(clock.sleeps, [30])
        self.assertEqual(reader.reads_of(self.COMPARE), 2)
        self.assertTrue([s for s in said
                         if s.startswith(f"repair gather: read {RECORD} "
                                         "failed")])
        # Only the attempt at this sha is compared.
        self.assertEqual({c for c in reader.calls if c != "rate_limit"},
                         {self.COMPARE})

    def test_a_compare_that_never_comes_back_is_null(self):
        rc, doc, reader, clock, _ = self._run(
            [LIMITED], rate_limit=lambda: LIMITED)
        self.assertEqual(rc, 0)
        self.assertEqual(doc, {RECORD: None})
        self.assertEqual(reader.reads_of(self.COMPARE), 3)
        self.assertEqual(clock.sleeps, [120, 120])

    def test_no_record_branch_makes_no_call(self):
        clock = Clock()
        reader = FakeReader({}, rate_limit=lambda: LIMITED)
        with tempfile.TemporaryDirectory() as td:
            refs = os.path.join(td, "refs.json")
            out = os.path.join(td, "compares.json")
            with open(refs, "w") as fh:
                fh.write("FETCH-FAILED\n")
            rc = red_main_repair.gather_compares(
                REPO, "main", SHA, refs, out, reader=reader,
                sleep=clock.sleep, now=clock.now, say=lambda _l: None)
            with open(out) as fh:
                doc = json.load(fh)
        self.assertEqual((rc, doc, reader.calls, clock.sleeps), (0, {}, [], []))


class DecideSaysTheRunFailedTest(unittest.TestCase):
    """The decide step's line no longer promises a retry nothing will make."""

    def test_the_unreadable_line_drops_the_next_failure_event_promise(self):
        with tempfile.TemporaryDirectory() as td:
            refs = os.path.join(td, "refs.json")
            pulls = os.path.join(td, "pulls.json")
            for path in (refs, pulls):
                with open(path, "w") as fh:
                    fh.write("FETCH-FAILED\n")
            done = _decide_cli(td, refs, pulls)
        self.assertIn("attempt records unreadable", done.stderr)
        self.assertNotIn("the next failure event retries", done.stderr)
        with open(os.path.join(SCRIPTS, "red_main_repair.py"),
                  encoding="utf-8") as fh:
            self.assertNotIn("the next failure event retries with fresh "
                             "records", fh.read())


def _decide_cli(td, refs, pulls):
    log = os.path.join(td, "log.txt")
    with open(log, "w") as fh:
        fh.write("E   assert 4 == 3\n")
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "red_main_repair.py"),
         "decide", "--conclusion", "failure", "--head-branch", "main",
         "--default-branch", "main", "--head-sha", SHA, "--log-file", log,
         "--refs-file", refs, "--pulls-file", pulls],
        capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
