"""A comment thread carries WHO wrote each comment (DRE-2721 review).

`comment_bodies()` asked Linear for `comments(last: 50) { nodes { body } }` —
bodies and nothing else. Anything reading a record out of that thread therefore
believed every commenter on the card equally, and `plan_critic.py` reads this
gate's whole round history out of it: two stray comments carrying a forged
`plan-critic: ... result=SEND_BACK` line were enough to override a real
rejection and promote an epic's children to build.

The fix is authorship at the fetch seam, where the Linear client already lives.
The pipeline's writes all go through one `LINEAR_API_KEY` that resolves to one
Linear user (README — "the relay, reconcile, the planner and every agent share
one LINEAR_API_KEY and resolve to the operator's own Linear user"), so "the
pipeline wrote this" is exactly "the key's own `viewer` wrote this". A bot
actor's comment has no `user` at all and is somebody else's integration — the
same rule README already states for break-glass labels.

Run: cd bureau-pipeline && python3 -m pytest tests/test_linear_comment_authorship.py -v
"""
from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import linear_ops  # noqa: E402

ME = "user-the-pipelines-own-key"

THREAD = {
    "viewer": {"id": ME},
    "issue": {"comments": {"nodes": [
        {"body": "the pipeline's own round marker", "user": {"id": ME}},
        {"body": "a teammate's note", "user": {"id": "user-somebody-else"}},
        {"body": "an integration's post", "user": None},
    ]}},
}


def test_only_the_keys_own_comments_are_pipeline_authored():
    with patch.object(linear_ops, "gql", return_value=THREAD):
        rows = linear_ops.comment_records("DRE-2721")
    assert [r["body"] for r in rows] == [
        "the pipeline's own round marker",
        "a teammate's note",
        "an integration's post",
    ]
    assert [r["authored_by_pipeline"] for r in rows] == [True, False, False]


def test_the_query_actually_asks_who_wrote_each_comment():
    """The whole defect was a query that never selected an author — a filter
    written against data that is not fetched silently passes everything."""
    seen = {}

    def spy(query, variables=None):
        seen["query"] = query
        return {"viewer": {"id": ME}, "issue": {"comments": {"nodes": []}}}

    with patch.object(linear_ops, "gql", spy):
        linear_ops.comment_records("DRE-2721")
    assert "viewer" in seen["query"]
    assert "user" in seen["query"]


def test_an_unknown_viewer_trusts_nobody():
    """If the key cannot say who it is, it cannot vouch for anyone. Failing to
    'nothing is the pipeline's' is the safe direction: the round history reads
    as absent rather than as whatever a stranger wrote."""
    thread = dict(THREAD, viewer=None)
    with patch.object(linear_ops, "gql", return_value=thread):
        rows = linear_ops.comment_records("DRE-2721")
    assert [r["authored_by_pipeline"] for r in rows] == [False, False, False]


def test_dump_comments_still_prints_bare_bodies_by_default():
    """model_fallback.py and reconcile.py read this shape — it must not move."""
    with patch.object(linear_ops, "gql", return_value=THREAD):
        buf = io.StringIO()
        with redirect_stdout(buf):
            linear_ops.cmd_dump_comments("DRE-2721")
    assert json.loads(buf.getvalue()) == [
        "the pipeline's own round marker",
        "a teammate's note",
        "an integration's post",
    ]


def test_dump_comments_with_authors_prints_records():
    with patch.object(linear_ops, "gql", return_value=THREAD):
        buf = io.StringIO()
        with redirect_stdout(buf):
            linear_ops.cmd_dump_comments("DRE-2721", "--with-authors")
    rows = json.loads(buf.getvalue())
    assert [r["authored_by_pipeline"] for r in rows] == [True, False, False]
    assert rows[0]["body"] == "the pipeline's own round marker"
