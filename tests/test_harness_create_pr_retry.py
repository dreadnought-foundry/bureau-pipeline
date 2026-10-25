"""RED-first tests: opening a PR survives the client's own retry (DRE-5294).

Harness run 36667726633 went red on a docs-only commit. `bot_pr_flow` asked
the sandbox to open its probe PR, and GitHub DID open it — #2727, authored by
the worker bot at 04:12:34 — but the answer the client got was a transient
failure (a 5xx or a dropped connection; the run log does not say which,
because the client's retries are silent). The client retries those by
sending the same request again, and for a `POST /pulls` the same request is
a second PR on the same branch, which GitHub refuses:

    GitHub API 422: … "A pull request already exists for
    dreadnought-foundry:agent/harness-main-gha-36667726633-1-bot_pr_flow."

Every harness branch is named for its own run and scenario, so an open PR on
that head can only be the one this client just opened. `create_pr` therefore
answers that refusal by reading the PR back and returning it. Any other 422 —
and the same refusal when no open PR can be found — still raises.

Run: python3 -m pytest tests/test_harness_create_pr_retry.py -v
"""

import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from harness import github_api  # noqa: E402
from harness.github_api import GitHub, GitHubError  # noqa: E402

REPO = "dreadnought-foundry/bureau-harness"
BRANCH = "agent/harness-main-gha-36667726633-1-bot_pr_flow"
ALREADY_EXISTS = json.dumps({
    "message": "Validation Failed",
    "errors": [{
        "resource": "PullRequest",
        "code": "custom",
        "message": f"A pull request already exists for dreadnought-foundry:{BRANCH}.",
    }],
    "status": "422",
})
NO_COMMITS = json.dumps({
    "message": "Validation Failed",
    "errors": [{
        "resource": "PullRequest",
        "code": "custom",
        "message": f"No commits between main and {BRANCH}",
    }],
    "status": "422",
})
OPENED = {"number": 2727, "head": {"ref": BRANCH}, "state": "open"}


def _http_error(status, body):
    return urllib.error.HTTPError(
        "https://api.github.com/x", status, "error", {}, io.BytesIO(body.encode())
    )


class Sandbox:
    """An opener scripted per request: `posts` is what each POST /pulls gets
    in turn (an exception to raise, or a payload), `open_prs` is what the
    open-PR read answers. Records every request it saw."""

    def __init__(self, posts, open_prs):
        self.posts = list(posts)
        self.open_prs = open_prs
        self.seen = []

    def __call__(self, req):
        self.seen.append(req)
        if req.get_method() == "POST":
            step = self.posts.pop(0)
            if isinstance(step, BaseException):
                raise step
            return 201, json.dumps(step).encode(), {}
        return 200, json.dumps(self.open_prs).encode(), {}


class CreatePrRetryTest(unittest.TestCase):
    def setUp(self):
        # The client's transient-failure backoff sleeps between attempts.
        patcher = mock.patch.object(github_api.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _create(self, sandbox):
        gh = GitHub("tok", opener=sandbox, log=lambda _line: None)
        return gh.create_pr(REPO, head=BRANCH, base="main", title="t", body="b")

    def test_a_5xx_then_already_exists_returns_the_pr_it_opened(self):
        """Run 36667726633, replayed: the first POST opened the PR and was
        answered with a server error; the retry was told the PR already
        exists."""
        sandbox = Sandbox(
            posts=[_http_error(502, "Bad Gateway"), _http_error(422, ALREADY_EXISTS)],
            open_prs=[OPENED],
        )
        pr = self._create(sandbox)
        self.assertEqual(
            pr["number"], 2727,
            "the PR this client opened was refused as a duplicate of itself",
        )

    def test_a_dropped_connection_then_already_exists_returns_the_pr_it_opened(self):
        sandbox = Sandbox(
            posts=[
                urllib.error.URLError(ConnectionResetError("Connection reset by peer")),
                _http_error(422, ALREADY_EXISTS),
            ],
            open_prs=[OPENED],
        )
        self.assertEqual(self._create(sandbox)["number"], 2727)

    def test_the_read_back_asks_for_this_branch_on_this_base(self):
        sandbox = Sandbox(posts=[_http_error(422, ALREADY_EXISTS)], open_prs=[OPENED])
        self._create(sandbox)
        read = sandbox.seen[-1]
        self.assertEqual(read.get_method(), "GET")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(read.full_url).query)
        self.assertEqual(query["head"], [f"dreadnought-foundry:{BRANCH}"])
        self.assertEqual(query["base"], ["main"])
        self.assertEqual(query["state"], ["open"])

    def test_already_exists_with_no_open_pr_to_read_back_still_raises(self):
        sandbox = Sandbox(posts=[_http_error(422, ALREADY_EXISTS)], open_prs=[])
        with self.assertRaises(GitHubError) as caught:
            self._create(sandbox)
        self.assertEqual(caught.exception.status, 422)
        self.assertIn("already exists", str(caught.exception))

    def test_any_other_422_still_raises(self):
        sandbox = Sandbox(posts=[_http_error(422, NO_COMMITS)], open_prs=[OPENED])
        with self.assertRaises(GitHubError) as caught:
            self._create(sandbox)
        self.assertEqual(caught.exception.status, 422)
        self.assertIn("No commits between", str(caught.exception))
        self.assertEqual(
            [r.get_method() for r in sandbox.seen], ["POST"],
            "a refusal that is not about a duplicate must not be read back",
        )

    def test_a_clean_create_is_one_post_and_one_read_back(self):
        sandbox = Sandbox(posts=[OPENED], open_prs=[])
        self.assertEqual(self._create(sandbox)["number"], 2727)
        self.assertEqual([r.get_method() for r in sandbox.seen], ["POST", "GET"])
        self.assertTrue(
            sandbox.seen[-1].full_url.endswith(f"/repos/{REPO}/pulls/2727"),
            sandbox.seen[-1].full_url,
        )


class CreatePrReadsBackTest(unittest.TestCase):
    """Run 38149749969 went red on a commit that touched no harness code.
    `bot_pr_flow` opened probe PR #4268 as the worker bot at 15:11:52, and
    the first read of it — `probe_pr`, sent as the dispatch-pool READER
    (DRE-4282) — answered:

        GitHub API 404: {"message":"Not Found", … "#get-a-pull-request"}

    Cleanup read the same PR two seconds later and closed it. A PR GitHub
    has just opened is not yet visible to every identity that asks, so
    `create_pr` returns only once the PR it opened reads back, through the
    same reader every later read goes out as."""

    def _client(self, opener, naps):
        return GitHub("tok", opener=opener, sleeper=naps.append, log=lambda _line: None)

    def test_a_pr_that_404s_on_its_first_read_is_waited_for(self):
        naps = []
        reads = [_http_error(404, '{"message":"Not Found"}'), OPENED]

        def opener(req):
            if req.get_method() == "POST":
                return 201, json.dumps(OPENED).encode(), {}
            step = reads.pop(0)
            if isinstance(step, BaseException):
                raise step
            return 200, json.dumps(step).encode(), {}

        pr = self._client(opener, naps).create_pr(
            REPO, head=BRANCH, base="main", title="t", body="b"
        )
        self.assertEqual(pr["number"], 2727)
        self.assertEqual(reads, [], "the PR was never read back after the 404")
        self.assertEqual(len(naps), 1, naps)

    def test_the_read_back_goes_out_as_the_reader(self):
        naps, writer_methods, reader_methods = [], [], []

        def writer(req):
            writer_methods.append(req.get_method())
            return 201, json.dumps(OPENED).encode(), {}

        lagging = [_http_error(404, '{"message":"Not Found"}')]

        def reader(req):
            reader_methods.append(req.get_method())
            if lagging:
                raise lagging.pop(0)
            return 200, json.dumps(OPENED).encode(), {}

        gh = GitHub(
            "worker", opener=writer, sleeper=naps.append, log=lambda _line: None,
            reader=GitHub("reader", opener=reader, log=lambda _line: None),
        )
        self.assertEqual(
            gh.create_pr(REPO, head=BRANCH, base="main", title="t", body="b")["number"],
            2727,
        )
        self.assertEqual(writer_methods, ["POST"])
        self.assertEqual(reader_methods, ["GET", "GET"])

    def test_a_pr_that_never_reads_back_raises_its_404(self):
        naps = []

        def opener(req):
            if req.get_method() == "POST":
                return 201, json.dumps(OPENED).encode(), {}
            raise _http_error(404, '{"message":"Not Found"}')

        with self.assertRaises(GitHubError) as caught:
            self._client(opener, naps).create_pr(
                REPO, head=BRANCH, base="main", title="t", body="b"
            )
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(len(naps), github_api.PR_READBACK_ATTEMPTS - 1)

    def test_any_other_read_back_refusal_raises_at_once(self):
        naps = []

        def opener(req):
            if req.get_method() == "POST":
                return 201, json.dumps(OPENED).encode(), {}
            raise _http_error(403, '{"message":"Resource not accessible by integration"}')

        with self.assertRaises(GitHubError) as caught:
            self._client(opener, naps).create_pr(
                REPO, head=BRANCH, base="main", title="t", body="b"
            )
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(naps, [])


if __name__ == "__main__":
    unittest.main()
