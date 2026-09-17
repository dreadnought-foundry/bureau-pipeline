"""RED-first tests: an epic at Linear's 2,000-comment cap degrades by name, and
the sweep warns before the line (DRE-3343).

THE INCIDENT (2026-09-08 09:35 PT). The wave epic DRE-2668 reached Linear's
hard cap of 2,000 comments per issue. Four Phase 7 discovery cards were filed
with `mid_epic.py discovery DRE-2668 …`: each card was created, and then the
growth record on the epic failed with

    'An issue can have a maximum of 2000 comments. This quota is enforced to
     keep the workspace performant.'   meta: {'quota': 'max-comments-per-issue'}

`mid_epic._add`'s order was card → epic growth → sibling verdict, so the crash
landed at step two — every time, forever. No discovery card into that epic would
ever carry its `🔎 mid-epic-verdict`, which is the one thing that lets it
promote. Every other writer that comments on the epic — the sweep's receipts,
the no-verdict alarm, the epic-growth notices — raised the same way, and nothing
anywhere reported the cap: it surfaced as a stack trace in whichever job
happened to comment next.

WHAT THIS PINS, one section per acceptance criterion:

  A. The condition is CLASSIFIED, by the two facts Linear sends — a
     `QUOTA_EXCEEDED` code and `meta.quota == max-comments-per-issue`. Not by
     the status, which is the same 400 a malformed query gets, and not by the
     prose, which is the vendor's to reword. A `QUOTA_EXCEEDED` for a different
     quota is NOT this condition.
  B. `linear_ops.cmd_comment` — the one place a comment is posted — degrades:
     it names the cap on stderr and returns the condition instead of raising,
     so every receipt writer in the fleet (sweep, critic, medic) continues.
  C. `mid_epic.discovery` still posts the sibling's verdict when the epic is at
     the cap, and records the growth it could not write ON THE SIBLING. The safe
     order becomes card → sibling verdict → epic growth.
  D. The sweep's receipt path logs the cap and the sweep completes.
  E. The reconcile sweep reads how many comments the epic holds, warns at
     1,800, and names every epic above the line in its summary. The card says
     that count is `comments.totalCount`, one field on a query the sweep
     already makes. Linear has no such field — see
     `test_the_epic_query_carries_the_uuid_the_count_is_read_by` — so it is
     paged instead, bounded by the cap itself at eight requests.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_comment_cap.py -v
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import linear_ops  # noqa: E402
import mid_epic  # noqa: E402
import reconcile  # noqa: E402

GREEN_LIGHT = "2026-08-20T09:00:00.000Z"
BEFORE = "2026-08-19T12:00:00.000Z"
AFTER = "2026-08-25T14:00:00.000Z"

EPIC = "DRE-2668"

# The payload Linear actually sent on 2026-09-08, in the shape it sent it: a
# 200 with an `errors` array (it also arrives as a 400 on the wire, which
# Section A pins separately).
CAP_ERRORS = [
    {
        "message": (
            "An issue can have a maximum of 2000 comments. This quota is "
            "enforced to keep the workspace performant."
        ),
        "extensions": {
            "code": "QUOTA_EXCEEDED",
            "type": "invalid input",
            "meta": {"quota": "max-comments-per-issue"},
        },
    }
]
CAP_BODY = json.dumps({"errors": CAP_ERRORS}).encode()

# A DIFFERENT quota, same code. The classification is the pair, not the code:
# a quota this seam knows nothing about must not be swallowed as "the comment
# cap" and degraded into silence.
OTHER_QUOTA_BODY = json.dumps(
    {
        "errors": [
            {
                "message": "A team can have a maximum of 5000 issues.",
                "extensions": {
                    "code": "QUOTA_EXCEEDED",
                    "meta": {"quota": "max-issues-per-team"},
                },
            }
        ]
    }
).encode()

RATELIMIT_BODY = json.dumps(
    {
        "errors": [
            {
                "message": (
                    "Rate limit exceeded. Only 2500 requests are allowed per 1 "
                    "hour and you have made 2500 requests in the last hour."
                ),
                "extensions": {"code": "RATELIMITED", "statusCode": 429},
            }
        ]
    }
).encode()

OK_BODY = json.dumps({"data": {"issues": {"nodes": []}}}).encode()
QUERY = "query { issues { nodes { id } } }"


class _Resp:
    """What urlopen hands back on a 200 — the shape tests/test_linear_ops_budget
    already uses, so the transport double stays one pattern in this repo."""

    def __init__(self, body: bytes = OK_BODY, headers: dict | None = None):
        self._body = body
        self.headers = dict(headers or {})

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _Transport:
    """A urlopen stand-in driven by a list of outcomes, one per call."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, *_args, **_kwargs):
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(linear_ops.time, "sleep", lambda _s: None)
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    monkeypatch.delenv(linear_ops.IDENTITY_ENV, raising=False)

    def _install(*outcomes):
        t = _Transport(*outcomes)
        monkeypatch.setattr(linear_ops.urllib.request, "urlopen", t)
        return t

    return _install


# ===========================================================================
# A: the condition is classified by what Linear SAYS, not by the status
# ===========================================================================
class TestTheCapIsClassified:
    def test_the_cap_payload_is_named_by_its_condition(self):
        condition = linear_ops.comment_cap_condition(CAP_BODY.decode())
        assert condition == linear_ops.COMMENT_CAP_CONDITION
        assert "2,000" in condition or "2000" in condition

    def test_a_quota_exceeded_for_another_quota_is_not_this_condition(self):
        """The pair is the classification. `QUOTA_EXCEEDED` alone would swallow
        every future Linear quota into a degrade path written for this one."""
        assert linear_ops.comment_cap_condition(OTHER_QUOTA_BODY.decode()) is None

    def test_a_rate_limit_is_not_a_comment_cap(self):
        assert linear_ops.comment_cap_condition(RATELIMIT_BODY.decode()) is None

    def test_an_ordinary_error_is_not_a_comment_cap(self):
        assert linear_ops.comment_cap_condition('{"errors":[{"message":"nope"}]}') is None
        assert linear_ops.comment_cap_condition("") is None

    def test_gql_raises_the_cap_type_on_a_200_with_an_errors_payload(self, transport):
        transport(_Resp(body=CAP_BODY))
        with pytest.raises(linear_ops.CommentCapReached) as exc:
            linear_ops.gql(QUERY)
        assert linear_ops.COMMENT_CAP_CONDITION in str(exc.value)

    def test_gql_raises_the_cap_type_on_the_400_wire_shape(self, transport):
        transport(
            urllib.error.HTTPError(
                linear_ops.API, 400, "Bad Request", {}, io.BytesIO(CAP_BODY)
            )
        )
        with pytest.raises(linear_ops.CommentCapReached):
            linear_ops.gql(QUERY)

    def test_the_cap_is_a_linear_error_so_existing_handlers_still_isolate_it(self):
        assert issubclass(linear_ops.CommentCapReached, linear_ops.LinearError)

    def test_the_cap_does_not_arm_the_rate_limit_stop(self, transport):
        """A full thread is a fact about ONE issue. Arming the process-wide
        rate-limit stop would make the next card's receipt fail too, with a
        reason that is not true of it."""
        t = transport(_Resp(body=CAP_BODY), _Resp())
        with pytest.raises(linear_ops.CommentCapReached):
            linear_ops.gql(QUERY)
        linear_ops.gql(QUERY)  # sent, not refused
        assert t.calls == 2


# ===========================================================================
# B: the ONE place a comment is posted degrades by name
# ===========================================================================
class TestCommentWriterDegrades:
    def _issue_read(self):
        return _Resp(
            body=json.dumps(
                {"data": {"issue": {"id": "uuid-1", "identifier": EPIC}}}
            ).encode()
        )

    def test_a_comment_that_lands_reports_nothing(self, transport):
        transport(
            self._issue_read(),
            _Resp(body=json.dumps({"data": {"commentCreate": {"success": True}}}).encode()),
        )
        assert linear_ops.cmd_comment("DRE-1", "hello") is None

    def test_a_capped_comment_names_the_cap_on_stderr_and_never_raises(
        self, transport, capsys
    ):
        """The stubbed `commentCreate` of acceptance criteria 1 and 2. The
        writer logs the cap BY NAME and continues — a stack trace in whichever
        job happened to comment next is what this replaces."""
        transport(self._issue_read(), _Resp(body=CAP_BODY))
        refused = linear_ops.cmd_comment("DRE-2", "a receipt")
        assert refused == linear_ops.COMMENT_CAP_CONDITION
        err = capsys.readouterr().err
        assert "DRE-2" in err
        assert linear_ops.COMMENT_CAP_CONDITION in err

    def test_an_ordinary_linear_failure_still_raises(self, transport):
        """Guard the guard: the degrade is for the cap and nothing else. A
        broken query must not be swallowed into a log line."""
        transport(
            self._issue_read(),
            _Resp(body=b'{"errors":[{"message":"Field does not exist"}]}'),
        )
        with pytest.raises(linear_ops.LinearError):
            linear_ops.cmd_comment("DRE-3", "a receipt")


# ===========================================================================
# C: the sibling's verdict survives an epic at the cap
# ===========================================================================
class TestMidEpicDiscoveryDegrades:
    def test_the_sibling_verdict_is_posted_before_the_epic_growth(self):
        """The safe order, and the whole of criterion 1: the verdict is the one
        thing a discovery card cannot promote without."""
        ops = _FakeOps(children=[("DRE-2701", BEFORE)])
        mid_epic.discovery(
            ops, EPIC, kind=mid_epic.ADDITION,
            because="a second call site needs the same fix",
            title="fix the second call site", body="- work",
        )
        assert ops.order.index("comment:DRE-2740") < ops.order.index("description:" + EPIC)

    def test_the_verdict_lands_even_though_the_epic_is_at_the_cap(self):
        ops = _FakeOps(children=[("DRE-2701", BEFORE)], capped={EPIC})
        ident = mid_epic.discovery(
            ops, EPIC, kind=mid_epic.ADDITION,
            because="a second call site needs the same fix",
            title="fix the second call site", body="- work",
        )
        assert mid_epic.carries_verdict(ops.comments_on(ident))

    def test_the_growth_it_could_not_write_is_recorded_on_the_sibling(self, capsys):
        """`unrecorded` is what the epic owes a comment for, and a 2,000-comment
        epic has one. The record lands on the sibling instead — where it can
        still be read — and the cap is named on stderr."""
        ops = _FakeOps(
            children=[("DRE-2701", BEFORE), ("DRE-2739", AFTER)], capped={EPIC}
        )
        ident = mid_epic.discovery(
            ops, EPIC, kind=mid_epic.ADDITION,
            because="a second call site needs the same fix",
            title="fix the second call site", body="- work",
        )
        note = "\n".join(ops.comments_on(ident))
        assert mid_epic.GROWTH_CAPPED_TAG in note
        assert "epic at comment cap — growth record not written" in note
        assert linear_ops.COMMENT_CAP_CONDITION in capsys.readouterr().err

    def test_a_healthy_epic_gets_no_cap_note(self):
        """Guard the guard: the note is a report of a real refusal, not a
        sentence every discovery card now carries."""
        ops = _FakeOps(children=[("DRE-2701", BEFORE), ("DRE-2739", AFTER)])
        ident = mid_epic.discovery(
            ops, EPIC, kind=mid_epic.ADDITION,
            because="a second call site needs the same fix",
            title="fix the second call site", body="- work",
        )
        assert mid_epic.GROWTH_CAPPED_TAG not in "\n".join(ops.comments_on(ident))
        assert ops.descriptions  # the growth artifact was still written

    def test_the_growth_refresh_reports_the_cap_it_hit(self):
        ops = _FakeOps(children=[("DRE-2701", BEFORE), ("DRE-2739", AFTER)],
                       capped={EPIC})
        report = mid_epic.refresh_epic_growth(ops, EPIC)
        assert report["capped"] == linear_ops.COMMENT_CAP_CONDITION

    def test_the_epic_query_carries_the_uuid_the_count_is_read_by(self):
        """The number Section E's warning is computed from, and the one fact
        the epic read has to carry for it.

        THIS TEST WAS WRITTEN AGAINST A FIELD LINEAR DOES NOT HAVE. The card
        specifies `comments { totalCount }` — one field on a query the sweep
        already makes — and the RED half of this suite pinned that literal in
        `_EPIC_QUERY`. Run against the live API on 2026-09-13 it is a
        validation error, not a count:

            Cannot query field "totalCount" on type "CommentConnection".
            __type(name: "CommentConnection") { fields { name } }
              -> edges, nodes, pageInfo

        Shipping the literal would have made EVERY epic read fail — and
        `read_epic` is what `mid_epic.discovery` files a card through, so the
        query would have broken the exact motion this card exists to repair.
        So the count is paged (`linear_ops.comment_count`) and what the epic
        read must carry is the UUID its filter takes.
        """
        assert " id identifier" in mid_epic._EPIC_QUERY
        ops = _FakeOps(children=[("DRE-2701", BEFORE)], comment_total=1_850)
        assert mid_epic.refresh_epic_growth(ops, EPIC)["comments"] == 1_850

    def test_an_epic_read_without_a_uuid_counts_nothing_rather_than_guessing(self):
        ops = _FakeOps(children=[("DRE-2701", BEFORE)], comment_total=1_850)
        ops.epic_uuid = None
        assert mid_epic.refresh_epic_growth(ops, EPIC)["comments"] is None


# ===========================================================================
# D: the sweep's receipt path logs the cap and the sweep completes
# ===========================================================================
class TestTheSweepSurvivesTheCap:
    def test_the_epic_growth_receipt_path_logs_the_cap_and_returns(self, capsys):
        """Acceptance criterion 2, through the sweep's own epic-comment writer.
        `report_epic_growth` is the sweep phase that comments on an epic."""
        ops = _FakeOps(children=[("DRE-2701", BEFORE), ("DRE-2739", AFTER)],
                       capped={EPIC}, comment_total=2_000)
        with patch.object(reconcile, "linear_ops", ops):
            reconcile.report_epic_growth({EPIC})
        captured = capsys.readouterr()
        assert linear_ops.COMMENT_CAP_CONDITION in captured.err
        assert EPIC in captured.out


# ===========================================================================
# E: the sweep warns before the line, and names the epic in its summary
# ===========================================================================
class TestTheSweepWarnsBeforeTheLine:
    @pytest.mark.parametrize("total", [0, 1, 1_799])
    def test_below_the_line_says_nothing(self, total):
        assert linear_ops.comment_cap_warning(EPIC, total) is None

    def test_an_unknown_count_says_nothing_rather_than_guessing(self):
        """Console-honesty rule 2: absent data is not a number."""
        assert linear_ops.comment_cap_warning(EPIC, None) is None

    @pytest.mark.parametrize("total", [1_800, 1_801, 1_999, 2_000, 2_400])
    def test_at_or_above_the_line_names_the_epic_and_the_number(self, total):
        warning = linear_ops.comment_cap_warning(EPIC, total)
        assert warning is not None
        assert EPIC in warning and str(total) in warning

    def test_the_warning_line_is_1800(self):
        assert linear_ops.COMMENT_CAP_WARN == 1_800
        assert linear_ops.COMMENT_CAP == 2_000

    def test_report_epic_growth_returns_the_epics_above_the_line(self, capsys):
        ops = _FakeOps(children=[("DRE-2701", BEFORE)], comment_total=1_900)
        with patch.object(reconcile, "linear_ops", ops):
            near = reconcile.report_epic_growth({EPIC})
        assert near == [(EPIC, 1_900)]
        assert EPIC in capsys.readouterr().out

    def test_a_quiet_epic_is_not_reported(self, capsys):
        ops = _FakeOps(children=[("DRE-2701", BEFORE)], comment_total=12)
        with patch.object(reconcile, "linear_ops", ops):
            assert reconcile.report_epic_growth({EPIC}) == []

    def test_the_sweep_summary_names_the_epic_above_the_line(self, capsys):
        """Acceptance criterion 3, end to end through `main()`: DRE-2668 is in
        the summary of a sweep that swept it."""
        out = _run_sweep_over_epic(comment_total=2_000)
        assert "epic-comment-cap" in out
        assert EPIC in out.split("epic-comment-cap", 1)[1].split("\n")[0]

    def test_a_sweep_with_no_epic_near_the_line_prints_no_summary(self):
        out = _run_sweep_over_epic(comment_total=10)
        assert "epic-comment-cap" not in out


# ===========================================================================
# E (cont.): the count itself — paged, bounded by the cap, and fail-soft
# ===========================================================================
def _comment_page(n: int, *, more: bool, cursor: str = "c1") -> _Resp:
    return _Resp(
        body=json.dumps(
            {
                "data": {
                    "comments": {
                        "nodes": [{"id": f"c{i}"} for i in range(n)],
                        "pageInfo": {"hasNextPage": more, "endCursor": cursor},
                    }
                }
            }
        ).encode()
    )


class TestTheCountIsPaged:
    """`comments { totalCount }` does not exist in Linear's schema, so the
    count is the length of the paged id list — and the cap is what bounds it:
    2,000 comments at 250 a page is eight requests, worst case, by Linear's
    own limit."""

    def test_a_quiet_epic_costs_one_request(self, transport):
        t = transport(_comment_page(12, more=False))
        assert linear_ops.comment_count("uuid-1") == 12
        assert t.calls == 1

    def test_the_pages_are_followed_and_summed(self, transport):
        t = transport(
            _comment_page(250, more=True, cursor="c-a"),
            _comment_page(250, more=True, cursor="c-b"),
            _comment_page(31, more=False),
        )
        assert linear_ops.comment_count("uuid-1") == 531
        assert t.calls == 3

    def test_a_failed_read_is_unknown_rather_than_an_exception(
        self, transport, capsys
    ):
        """It is read on the path that FILES a discovery card. A count must
        never be the thing that stops a card being created."""
        transport(_Resp(body=b'{"errors":[{"message":"Field does not exist"}]}'))
        assert linear_ops.comment_count("uuid-1") is None
        assert "comment-count" in capsys.readouterr().err

    def test_the_query_can_actually_page(self):
        """`gql_paged` refuses a query with no `$after` — a count that could
        only ever read the first page would report every epic as quiet."""
        assert "$after" in linear_ops._COMMENT_COUNT_QUERY
        assert "pageInfo" in linear_ops._COMMENT_COUNT_QUERY

    def test_the_filter_takes_the_uuid_type_linear_demands(self):
        """`$id: String!` is what the epic read uses and it is REJECTED here —
        `Variable "$id" of type "String!" used in position expecting type
        "ID!"`, live, 2026-09-13. The two reads take different types for the
        same card, which is exactly the kind of fact a fake cannot teach."""
        assert "$id: ID!" in linear_ops._COMMENT_COUNT_QUERY


# ===========================================================================
# The runbook: what to do when a live epic hits the cap
# ===========================================================================
class TestTheRunbookExists:
    def test_the_runbook_says_where_the_receipts_go(self):
        """Decide and document, do not improvise per job."""
        doc = (ROOT / "docs" / "epic-comment-cap.md").read_text(encoding="utf-8")
        assert "receipts (cont.)" in doc
        assert "2,000" in doc or "2000" in doc


# ===========================================================================
# Test doubles
# ===========================================================================
class _FakeOps:
    """A stand-in for the `linear_ops` MODULE — the convention every
    Linear-touching seam here takes its I/O by (break_glass, validate_card).

    `capped` is the set of identifiers Linear refuses a comment on, and the
    fake honours the SAME contract the real `cmd_comment` does after DRE-3343:
    it names the cap on stderr and RETURNS the condition rather than raising.
    `TestCommentWriterDegrades` above pins that contract against the real seam.
    """

    def __init__(self, epic_description="The epic.", epic_state="In Progress",
                 children=(), green_lit_at=GREEN_LIGHT, capped=(),
                 comment_total=7):
        self.epic_description = epic_description
        self.epic_state = epic_state
        self.children = list(children)
        self.green_lit_at = green_lit_at
        self.capped = set(capped)
        self.comment_total = comment_total
        self.epic_uuid = "uuid-2668"
        self.created: list[dict] = []
        self.comments: list[tuple[str, str]] = []
        self.states: list[tuple[str, str]] = []
        self.descriptions: list[tuple[str, str]] = []
        self.order: list[str] = []
        self._next = 2740
        self.LinearError = linear_ops.LinearError
        self.CommentCapReached = linear_ops.CommentCapReached
        self.COMMENT_CAP = linear_ops.COMMENT_CAP
        self.COMMENT_CAP_WARN = linear_ops.COMMENT_CAP_WARN
        self.COMMENT_CAP_CONDITION = linear_ops.COMMENT_CAP_CONDITION
        self.comment_cap_warning = linear_ops.comment_cap_warning

    # --- the verbs --------------------------------------------------------
    def cmd_subissue(self, parent, title, body, *flags):
        ident = f"DRE-{self._next}"
        self._next += 1
        issue = {"id": f"uuid-{ident}", "identifier": ident,
                 "url": f"https://linear.app/x/{ident}", "state": "Backlog",
                 "parent": parent, "title": title}
        self.created.append(issue)
        self.children.append((ident, AFTER))
        self.order.append(f"subissue:{ident}")
        return issue

    def cmd_comment(self, identifier, body):
        if identifier in self.capped:
            print(
                f"comment-cap: {identifier} — {linear_ops.COMMENT_CAP_CONDITION}",
                file=sys.stderr,
            )
            return linear_ops.COMMENT_CAP_CONDITION
        self.comments.append((identifier, body))
        self.order.append(f"comment:{identifier}")
        return None

    def cmd_state(self, identifier, state, *flags):
        self.states.append((identifier, state))
        if identifier == EPIC:
            self.epic_state = state

    def set_description(self, identifier, body):
        self.descriptions.append((identifier, body))
        self.epic_description = body
        self.order.append(f"description:{identifier}")

    def comment_count(self, issue_id):
        """The paged count, by UUID — `linear_ops.comment_count`'s contract.
        Answers only for the epic it stands in for; anything else is unknown,
        which is what the real one returns when Linear cannot say."""
        return self.comment_total if issue_id == self.epic_uuid else None

    def count_comments(self, identifier, needle, **kw):
        return sum(1 for i, b in self.comments if i == identifier and needle in b)

    def comment_bodies(self, identifier):
        return self.comments_on(identifier)

    def gql(self, query, variables=None):
        history = (
            [{"createdAt": self.green_lit_at, "toState": {"name": "In Progress"}}]
            if self.green_lit_at
            else []
        )
        return {
            "issue": {
                "id": self.epic_uuid,
                "identifier": EPIC,
                "description": self.epic_description,
                "state": {"name": self.epic_state},
                "children": {
                    "nodes": [
                        {"identifier": i, "createdAt": at} for i, at in self.children
                    ]
                },
                "history": {"nodes": history},
            }
        }

    # --- assertion helpers ------------------------------------------------
    def comments_on(self, identifier):
        return [b for i, b in self.comments if i == identifier]


def _run_sweep_over_epic(comment_total: int) -> str:
    """One full `reconcile.main()` over a board holding exactly one epic, with
    every other phase stubbed — the shape tests/test_break_glass.py uses."""
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    epic = {
        "id": "uuid-2668",
        "identifier": EPIC,
        "title": "[EPIC] wave 1.5",
        "description": "The wave.",
        "updatedAt": "2026-09-01T00:00:00Z",
        "state": {"name": "In Progress"},
        "labels": {"nodes": [{"name": "repo:bureau-pipeline"}]},
        "children": {"nodes": [{"id": "uuid-2701"}]},
    }
    mocks = {
        "drain_retiring_lanes": MagicMock(),
        "unstick_conflicts": MagicMock(),
        "refresh_stale_merge_refs": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "flag_no_checks_prs": MagicMock(),
        "flag_unowned_prs": MagicMock(),
        "flag_unlanded_work": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "retry_dead_fix_runs": MagicMock(),
        "redispatch_standing_verdicts": MagicMock(),
        "recover_limit_deaths": MagicMock(),
        "restart_answered_blockers": MagicMock(),
        "card_dependabot_prs": MagicMock(),
        "review_dependabot_prs": MagicMock(),
        "recover_crashed_reviews": MagicMock(),
        "report_fleet_reviewer_outage": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
        "flag_stranded": MagicMock(return_value=set()),
        "report_intake_depth": MagicMock(),
        "close_finished_epics": MagicMock(),
        "promote_ready": MagicMock(return_value=0),
        "active_cards": MagicMock(return_value=[epic]),
        "report_break_glass": MagicMock(),
        "report_fix_concurrency": MagicMock(),
        "report_evicted_fix_runs": MagicMock(),
    }
    growth = {
        "green_lit": 1, "current": 1, "unrecorded": [], "re_approved": [],
        "capped": None, "comments": comment_total,
    }
    import io as _io
    import contextlib

    buf = _io.StringIO()
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile, "REPO_SLUG", "bureau-pipeline"
    ), patch.object(
        reconcile.mid_epic, "refresh_epic_growth", return_value=growth
    ), contextlib.redirect_stdout(buf):
        reconcile.main()
    return buf.getvalue()
