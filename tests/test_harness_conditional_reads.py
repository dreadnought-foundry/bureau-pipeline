"""RED-first tests for the harness driver's conditional reads (DRE-4132).

On 2026-09-17 the worker App's installation (123249480) ran out of its 5,000
GitHub requests an hour, near the end of every hour, from 05:38 PT on — and
while it was out, every job in the fleet holding that App's token was refused.
The Integration Harness was both the loudest casualty and the largest spender:
its driver WAITS on the sandbox by asking the same question again and again —
`gate_paths`' stale leg asks two (`get_pr` + `list_comments`) every FIVE
seconds for as long as the critic takes, which is 24 requests a minute per
live run, and every one of them is billed although the answer has not changed
since the last time. With four harness runs live the installation measured
~150 requests a minute; with two, ~60.

GitHub does not bill a conditional request answered `304 Not Modified`
(measured on 2026-09-17: 20 `If-None-Match` reads moved `x-ratelimit-used` by
0, the same 20 unconditional moved it by 20). So the driver's client
remembers each GET's `ETag` and the body that came with it, sends
`If-None-Match` the next time it asks the same URL, and answers from memory
on a 304. The 5-second race in `gate_paths` keeps its cadence — shortening
the question, not asking it less often — and every other wait gets the same
saving for free.

What these pin:

  * a repeated GET is conditional, and a 304 returns the remembered answer;
  * a CHANGED answer (200, new ETag) replaces the memory — the driver can
    never be served something older than GitHub's own reply;
  * each answer is a fresh object — a scenario that mutates what it was handed
    cannot poison the next poll;
  * writes and the raw log archive are never conditional;
  * a fake opener written to the old `(status, body)` contract still works —
    it just never becomes conditional;
  * the client keeps a ledger of billed vs free requests, and the driver
    prints it per identity at the end of every run, so the next person asking
    "what does a harness run cost?" reads a number instead of a model.

These tests must FAIL before the support exists, and PASS after.

Run: python3 -m pytest tests/test_harness_conditional_reads.py -v
"""

import contextlib
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from harness import __main__ as harness_main  # noqa: E402
from harness import framework, github_api  # noqa: E402
from harness.github_api import GitHub, GitHubError  # noqa: E402

REPO = "dreadnought-foundry/bureau-harness"
COMMENTS = f"/repos/{REPO}/issues/7/comments?per_page=100"


def _not_modified():
    # urllib raises on a 304 — it is not a 2xx — so that is what a faithful
    # fake does too.
    return urllib.error.HTTPError(
        "https://api.github.com/x", 304, "Not Modified", None, io.BytesIO(b"")
    )


class FakeGitHub:
    """An opener that behaves like api.github.com for ONE resource per URL:
    it answers 200 with the current body and ETag, or 304 when the request's
    `If-None-Match` names the current ETag. Records every request it saw."""

    def __init__(self):
        self.resources = {}
        self.seen = []

    def publish(self, url_suffix, body, etag):
        self.resources[url_suffix] = (json.dumps(body).encode(), etag)

    def __call__(self, req):
        self.seen.append(req)
        suffix = req.full_url.replace("https://api.github.com", "")
        payload, etag = self.resources[suffix]
        # urllib title-cases header names it stores: "If-none-match".
        sent = req.headers.get("If-none-match")
        if req.get_method() == "GET" and sent is not None and sent == etag:
            raise _not_modified()
        return 200, payload, {"ETag": etag}


class ConditionalReadTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()
        self.gh = GitHub("tok", opener=self.api)

    def test_the_first_read_of_a_url_is_unconditional(self):
        self.api.publish(COMMENTS, [{"id": 1}], 'W/"v1"')
        self.gh.list_comments(REPO, 7)
        self.assertIsNone(self.api.seen[0].headers.get("If-none-match"))

    def test_a_repeated_read_sends_the_etag_it_was_given(self):
        self.api.publish(COMMENTS, [{"id": 1}], 'W/"v1"')
        self.gh.list_comments(REPO, 7)
        self.gh.list_comments(REPO, 7)
        self.assertEqual(
            self.api.seen[1].headers.get("If-none-match"),
            'W/"v1"',
            "the second poll of an unchanged URL was billed in full — the "
            "driver did not make it conditional",
        )

    def test_a_304_returns_the_remembered_answer(self):
        self.api.publish(COMMENTS, [{"id": 1, "body": "QA Critic"}], 'W/"v1"')
        first = self.gh.list_comments(REPO, 7)
        second = self.gh.list_comments(REPO, 7)
        self.assertEqual(second, first)
        self.assertEqual(second, [{"id": 1, "body": "QA Critic"}])

    def test_a_changed_answer_replaces_the_memory(self):
        self.api.publish(COMMENTS, [{"id": 1}], 'W/"v1"')
        self.gh.list_comments(REPO, 7)
        self.api.publish(COMMENTS, [{"id": 1}, {"id": 2}], 'W/"v2"')
        self.assertEqual(
            self.gh.list_comments(REPO, 7),
            [{"id": 1}, {"id": 2}],
            "the verdict comment arrived and the driver kept answering from "
            "memory — a wait on it would run out its whole budget",
        )
        # …and the NEXT poll is conditional on the new tag, and free again.
        self.assertEqual(self.gh.list_comments(REPO, 7), [{"id": 1}, {"id": 2}])
        self.assertEqual(self.api.seen[-1].headers.get("If-none-match"), 'W/"v2"')

    def test_each_answer_is_a_fresh_object(self):
        self.api.publish(COMMENTS, [{"id": 1}], 'W/"v1"')
        first = self.gh.list_comments(REPO, 7)
        first.append({"id": "scribbled by a scenario"})
        first[0]["id"] = 999
        self.assertEqual(self.gh.list_comments(REPO, 7), [{"id": 1}])

    def test_urls_are_remembered_separately(self):
        pr = f"/repos/{REPO}/pulls/7"
        self.api.publish(COMMENTS, [{"id": 1}], 'W/"c1"')
        self.api.publish(pr, {"number": 7, "merged": False}, 'W/"p1"')
        self.gh.list_comments(REPO, 7)
        self.gh.get_pr(REPO, 7)
        self.assertEqual(self.gh.get_pr(REPO, 7), {"number": 7, "merged": False})
        self.assertEqual(self.gh.list_comments(REPO, 7), [{"id": 1}])
        self.assertEqual(
            [r.headers.get("If-none-match") for r in self.api.seen],
            [None, None, 'W/"p1"', 'W/"c1"'],
        )

    def test_a_write_is_never_conditional(self):
        self.api.publish(COMMENTS.split("?")[0], {"id": 5}, 'W/"w1"')
        # Same URL read first, so there IS a tag a careless client could send.
        self.gh.request("GET", COMMENTS.split("?")[0])
        self.gh.create_comment(REPO, 7, "hello")
        post = self.api.seen[-1]
        self.assertEqual(post.get_method(), "POST")
        self.assertIsNone(post.headers.get("If-none-match"))

    def test_an_answer_without_an_etag_is_not_remembered(self):
        def opener(req):
            opener.seen.append(req)
            return 200, b'{"n": 1}', {}

        opener.seen = []
        gh = GitHub("tok", opener=opener)
        gh.request("GET", "/x")
        gh.request("GET", "/x")
        self.assertIsNone(opener.seen[1].headers.get("If-none-match"))

    def test_the_old_two_value_opener_contract_still_works(self):
        """Every fake opener in this suite predates this change and returns
        `(status, body)`. They must keep working unedited — and with no
        headers to read, such a client is simply never conditional."""

        def opener(req):
            opener.seen.append(req)
            return 200, b'{"n": 1}'

        opener.seen = []
        gh = GitHub("tok", opener=opener)
        self.assertEqual(gh.request("GET", "/x"), {"n": 1})
        self.assertEqual(gh.request("GET", "/x"), {"n": 1})
        self.assertIsNone(opener.seen[1].headers.get("If-none-match"))

    def test_a_304_nobody_asked_for_is_an_error_not_an_empty_answer(self):
        def opener(req):
            raise _not_modified()

        gh = GitHub("tok", opener=opener)
        with self.assertRaises(GitHubError) as caught:
            gh.request("GET", "/x")
        self.assertEqual(caught.exception.status, 304)

    def test_the_raw_log_archive_is_never_conditional(self):
        def opener(req):
            opener.seen.append(req)
            return 200, b"plain log text", {"ETag": '"log1"'}

        opener.seen = []
        gh = GitHub("tok", opener=opener)
        gh.request_bytes("GET", "/repos/x/y/actions/runs/1/logs")
        gh.request_bytes("GET", "/repos/x/y/actions/runs/1/logs")
        self.assertIsNone(opener.seen[1].headers.get("If-none-match"))

    def test_conditional_reads_can_be_switched_off(self):
        self.api.publish(COMMENTS, [{"id": 1}], 'W/"v1"')
        gh = GitHub("tok", opener=self.api, conditional=False)
        gh.list_comments(REPO, 7)
        gh.list_comments(REPO, 7)
        self.assertIsNone(self.api.seen[1].headers.get("If-none-match"))

    def test_the_memory_is_bounded(self):
        """A long run reads thousands of distinct URLs (every probe file,
        every run record). The memory keeps the most recent
        ETAG_CACHE_ENTRIES and forgets the oldest — forgetting costs one
        billed read, never a wrong answer."""
        cap = github_api.ETAG_CACHE_ENTRIES
        for i in range(cap + 5):
            self.api.publish(f"/r/{i}", {"i": i}, f'"t{i}"')
            self.gh.request("GET", f"/r/{i}")
        self.gh.request("GET", "/r/0")  # forgotten → asked in full again
        self.assertIsNone(self.api.seen[-1].headers.get("If-none-match"))
        self.gh.request("GET", f"/r/{cap + 4}")  # recent → still conditional
        self.assertEqual(
            self.api.seen[-1].headers.get("If-none-match"), f'"t{cap + 4}"'
        )


class RealTransportTest(unittest.TestCase):
    """The production opener has to hand the response HEADERS back, or the
    client never learns a tag and nothing above happens outside a test."""

    def test_the_real_opener_returns_the_response_headers(self):
        class _Resp:
            status = 200
            headers = {"ETag": 'W/"live"'}

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch.object(
            github_api.urllib.request, "urlopen", lambda req, timeout: _Resp()
        ):
            answer = GitHub._urlopen(object())
        self.assertEqual(len(answer), 3, "(status, body, headers) expected")
        self.assertEqual(answer[2].get("ETag"), 'W/"live"')


class SpendLedgerTest(unittest.TestCase):
    """billed = GitHub charged the installation's hour for it; free = a 304."""

    def test_the_ledger_separates_billed_from_free(self):
        api = FakeGitHub()
        api.publish(COMMENTS, [{"id": 1}], 'W/"v1"')
        gh = GitHub("tok", opener=api)
        for _ in range(10):
            gh.list_comments(REPO, 7)
        self.assertEqual(gh.spend(), {"billed": 1, "free": 9})

    def test_a_write_is_billed(self):
        api = FakeGitHub()
        api.publish(f"/repos/{REPO}/issues/7/comments", {"id": 5}, '"w"')
        gh = GitHub("tok", opener=api)
        gh.create_comment(REPO, 7, "x")
        self.assertEqual(gh.spend(), {"billed": 1, "free": 0})

    def test_a_refused_request_is_still_billed(self):
        def opener(req):
            raise urllib.error.HTTPError(
                "https://api.github.com/x", 404, "nf", None, io.BytesIO(b"{}")
            )

        gh = GitHub("tok", opener=opener)
        with self.assertRaises(GitHubError):
            gh.request("GET", "/x")
        self.assertEqual(gh.spend(), {"billed": 1, "free": 0})


class _PollingScenario(framework.Scenario):
    """Asks the sandbox the same question five times — what a wait does."""

    name = "polls_five_times"

    def setup(self, ctx):
        pass

    def exercise(self, ctx):
        for _ in range(5):
            ctx.gh.list_comments(ctx.repo, 7)

    def verify(self, ctx):
        pass

    def cleanup(self, ctx):
        pass


class DriverReportsItsSpendTest(unittest.TestCase):
    def test_the_run_ends_with_one_spend_line_per_identity(self):
        api = FakeGitHub()
        api.publish(COMMENTS, [{"id": 1}], 'W/"v1"')

        def client(token, **kwargs):
            return github_api.GitHub(token, opener=api, **kwargs)

        env = {
            "HARNESS_WORKER_TOKEN": "ghs-worker",
            "HARNESS_QA_TOKEN": "ghs-qa",
            "HARNESS_QA_LOGIN": "agent-bureau-qa-bot[bot]",
            "HARNESS_WAIT_DEADLINE_MINUTES": "0",
        }
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            harness_main, "discover",
            lambda: {_PollingScenario.name: _PollingScenario()},
        ), mock.patch.object(
            harness_main, "GitHub", client
        ), contextlib.redirect_stdout(out):
            code = harness_main.main(
                ["--repo", REPO, "--scenarios", _PollingScenario.name,
                 "--run-id", "gha-1-1"]
            )
        self.assertEqual(code, 0, out.getvalue())
        lines = [
            ln for ln in out.getvalue().splitlines()
            if ln.startswith("github-spend:")
        ]
        self.assertIn(
            "github-spend: worker 1 billed, 4 free (304 Not Modified)", lines
        )
        self.assertIn(
            "github-spend: qa 0 billed, 0 free (304 Not Modified)", lines
        )


if __name__ == "__main__":
    unittest.main()
