"""RED-first: the sweep reads each card once per pass, and merge-sync reads
the merged card and its epic once (DRE-3236; absorbs DRE-3175).

THE MEASUREMENT (DRE-3202's budget line, the first full hour after the
2026-09-05 restart): two consumers owned the fleet's 2,500-requests-an-hour
Linear quota. The reconcile sweep cost 93-143 requests per pass on a busy
repo, and one merge's linear-sync cost 110. Planners, critics, the gate and
the medic were single digits. A quiet hour fit; a busy one — six repos
sweeping, five merges — crossed the line, twice.

WHERE THE SWEEP SPENT IT. The board reads already carried every card's
comments inline (`comments(last: 50)`, DRE-2929), and then the pass went back
to Linear for the SAME comments, one request per card, wherever a helper took
an identifier instead of the card: `linear_ops.comment_bodies(ident)`,
`count_comments(ident, tag)` (behind every `_surface_once` refusal — one per
refused Backlog card per sweep), `first_comment_at`, `comment_records(epic)`
and `agent_run_alive`'s own timeline read.

WHERE MERGE-SYNC SPENT IT. Every board-wide pass the merge event justified
(`--promote-only`, `--close-epics`) re-read the whole board and the whole
Backlog, then walked every active epic, to act on the one card this merge
could have changed and its one parent.

WHAT IS UNDER TEST:
  * A per-PASS read cache in `linear_ops`: the board reads remember every
    card's inline comment window, and every comment reader that takes an
    identifier is served from it. A card whose inline window is exhausted
    (more than the window's 50 comments) costs ONE paged read, cached for the
    pass. A comment the pass posts invalidates that card's cached thread.
  * The cache carries AUTHORSHIP: `comment_records` — the plan-critic gate's
    credential (DRE-2721) — reads `authored_by_pipeline` off the cached thread
    exactly as it did off its own query.
  * A busy-repo sweep (260 Backlog cards, 30 open PRs, 5 epics) spends at most
    SWEEP_BUDGET Linear READ requests, and ZERO per-card comment reads.
  * One merge-sync — card-done, the merge-sweep gate, and the two event-driven
    passes it can justify — spends at most MERGE_SYNC_BUDGET READ requests
    over the same board, because the event-driven passes are SCOPED to the
    merged card's own relations: its dependents, and its parent epic.

Writes are stubbed and NOT counted, the convention `test_sweep_request_budget`
set: a write is work the pass did (a promotion, an epic close, a receipt) and
scales with what happened, never with the board. A promotion costs about
seven requests, an epic close about six, a receipt two.

None of these tests may change WHICH cards the sweep promotes, holds or
comments on. The one deliberate behaviour change is on the MERGE path: the
event-driven passes consider the merged card's own dependents and parent —
everything else waits for the 15-minute cron, which `merge_sweep_gate` already
named as the backstop for what the gate declines.

Run: cd bureau-pipeline && python3 -m pytest tests/test_sweep_request_cuts.py -v
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import linear_ops  # noqa: E402
import merge_sweep_gate  # noqa: E402
import reconcile  # noqa: E402
import validate_card  # noqa: E402

#: What one busy-repo sweep may spend on Linear READS, over the fixture board
#: below. The card's target. Two paged reads of the active lanes and the
#: Backlog (three pages at 260 cards), two reads per active epic (its relations
#: and its green-light history), one `viewer` read for authorship, and one
#: children read per epic for the epic close — nothing per card.
SWEEP_BUDGET = 30

#: What one merge-sync may spend on Linear READS: card-done's one read of the
#: card, the gate's one read, and the two scoped passes it justifies.
MERGE_SYNC_BUDGET = 15

FLEET = "user-the-fleet-key"
SOMEONE_ELSE = "user-somebody-else"
REPO_LABEL = "repo:agent-bureau"


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


@pytest.fixture(autouse=True)
def _pin_valid_slugs(monkeypatch):
    monkeypatch.setattr(
        validate_card, "VALID_SLUGS", {"agent-bureau", "atlas", "bureau-pipeline"}
    )


@pytest.fixture(autouse=True)
def _pin_live_snapshot(monkeypatch):
    monkeypatch.setattr(
        reconcile,
        "live_rail_slugs",
        lambda: frozenset({"agent-bureau", "atlas", "bureau-pipeline"}),
        raising=False,
    )


@pytest.fixture(autouse=True)
def _clean_ledgers(monkeypatch):
    monkeypatch.delenv("MERGED_CARD", raising=False)
    for ledger in (reconcile._write_failures, reconcile._read_failures,
                   reconcile._stale_defects, reconcile._card_skips):
        ledger.clear()
    yield
    for ledger in (reconcile._write_failures, reconcile._read_failures,
                   reconcile._stale_defects, reconcile._card_skips):
        ledger.clear()


def _iso(minutes_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _comment(body: str, *, by: str | None = FLEET, minutes_ago: float = 60.0) -> dict:
    """One comment node in the shape the board reads select it."""
    return {
        "body": body,
        "createdAt": _iso(minutes_ago),
        "user": {"id": by} if by else None,
    }


def _comments(nodes=(), *, exhausted: bool = False) -> dict:
    return {
        "pageInfo": {"hasPreviousPage": exhausted, "startCursor": "cursor-0"},
        "nodes": list(nodes),
    }


def _card(
    identifier: str,
    state: str = "Backlog",
    *,
    title: str = "a card the pass must read once",
    labels=(REPO_LABEL,),
    minutes_stale: float = 600.0,
    comments=None,
    parent: dict | None = None,
    children: int = 0,
    blocks=(),
) -> dict:
    """A card in the shape BOTH board reads return it — comments inline."""
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": title,
        "description": "work",
        "createdAt": _iso(minutes_stale + 1440),
        "updatedAt": _iso(minutes_stale),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "parent": parent,
        "children": {"nodes": [{"id": f"kid-{identifier}-{n}"} for n in range(children)]},
        "comments": comments if comments is not None else _comments(),
        "inverseRelations": {"nodes": []},
        "relations": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {"type": "blocks", "issue": {"identifier": identifier},
                 "relatedIssue": {"identifier": dep}}
                for dep in blocks
            ],
        },
    }


def _epic(identifier: str, state: str = "In Progress", **kw) -> dict:
    return _card(identifier, state, title=f"[EPIC] {identifier}", children=1, **kw)


def _parent_ref(epic: dict) -> dict:
    return {"identifier": epic["identifier"], "state": epic["state"]}


# --------------------------------------------------------------------------
# The fake Linear: answers every read the sweep and the merge path make, off
# one board, and counts them. It pages the way Linear does (100 per page) so
# a 260-card Backlog costs three requests here as it does live.
# --------------------------------------------------------------------------
_BOARD_READ = "state: {name: {in: $states}}"
_BACKLOG_READ = 'state: {name: {eq: "Backlog"}}'
_PAGE = 100


class FakeLinear:
    def __init__(self, cards=()):
        self.cards: dict[str, dict] = {c["identifier"]: c for c in cards}
        self.queries: list[tuple[str, dict]] = []

    # -- counters -----------------------------------------------------------
    @property
    def requests(self) -> int:
        return len(self.queries)

    def _single_issue(self, q: str) -> bool:
        return "issue(id: $id)" in q

    @property
    def per_card_comment_reads(self) -> int:
        """A single-issue query that selects the card's comments — the read
        the board already made, bought again."""
        return sum(
            1 for q, _ in self.queries if self._single_issue(q) and "comments(" in q
        )

    @property
    def board_reads(self) -> int:
        return sum(1 for q, _ in self.queries if _BOARD_READ in q)

    @property
    def whole_backlog_reads(self) -> int:
        """Backlog pages read with no identifier scope — a board-wide walk."""
        return sum(
            1 for q, v in self.queries
            if _BACKLOG_READ in q and not (v or {}).get("numbers")
        )

    # -- helpers ------------------------------------------------------------
    def _page(self, nodes: list[dict], after: str | None) -> dict:
        start = int(after or 0)
        chunk = nodes[start:start + _PAGE]
        more = start + _PAGE < len(nodes)
        return {
            "issues": {
                "nodes": chunk,
                "pageInfo": {"hasNextPage": more, "endCursor": str(start + _PAGE)},
            }
        }

    def _find(self, ident: str) -> dict | None:
        return self.cards.get(ident)

    # -- the seam -----------------------------------------------------------
    def gql(self, query, variables=None):
        v = variables or {}
        self.queries.append((query, v))
        q = " ".join(query.split())

        if _BOARD_READ in q:
            wanted = set(v.get("states") or ())
            nodes = [c for c in self.cards.values() if c["state"]["name"] in wanted]
            return self._page(nodes, v.get("after"))
        if _BACKLOG_READ in q:
            nodes = [c for c in self.cards.values() if c["state"]["name"] == "Backlog"]
            if v.get("numbers"):
                keep = {int(n) for n in v["numbers"]}
                nodes = [c for c in nodes if int(c["identifier"].split("-")[1]) in keep]
            return self._page(nodes, v.get("after"))
        if "mutation" in q:
            return {"issueUpdate": {"success": True}, "commentCreate": {"success": True}}
        if "workflowStates" in q:
            return {"workflowStates": {"nodes": [
                {"id": f"state-{n.lower().replace(' ', '-')}", "name": n, "type": t}
                for n, t in (("Backlog", "backlog"), ("Todo", "unstarted"),
                             ("In Progress", "started"), ("In Review", "started"),
                             ("Done", "completed"), ("Planning", "unstarted"))
            ]}}
        if q.startswith("query { viewer") or q == "query { viewer { id } }":
            return {"viewer": {"id": FLEET}}

        card = self._find(v.get("id", ""))
        if card is None:
            return {"issue": None, "viewer": {"id": FLEET}}

        if "relations(first: 50)" in q and "parent {" in q:
            # merge_sweep_gate.QUERY — the merged card, its dependents, its parent.
            parent = card.get("parent")
            if parent:
                epic = self._find(parent["identifier"]) or {}
                kids = [
                    {"identifier": c["identifier"], "state": c["state"]}
                    for c in self.cards.values()
                    if (c.get("parent") or {}).get("identifier") == parent["identifier"]
                ]
                parent = dict(parent, children={"pageInfo": {"hasNextPage": False},
                                                "nodes": kids})
                parent.setdefault("state", epic.get("state"))
            relations = {
                "pageInfo": {"hasNextPage": False},
                "nodes": [
                    {"type": "blocks", "issue": {"identifier": card["identifier"]},
                     "relatedIssue": {"identifier": r["relatedIssue"]["identifier"],
                                      "state": (self._find(r["relatedIssue"]["identifier"]) or {}).get("state")}}
                    for r in card["relations"]["nodes"]
                ],
            }
            return {"issue": {"identifier": card["identifier"], "state": card["state"],
                              "relations": relations, "parent": parent}}
        if "history(last: 50)" in q and "children(first: 250)" in q:
            # mid_epic._EPIC_QUERY — the epic's green light.
            kids = [
                {"identifier": c["identifier"], "createdAt": c["createdAt"]}
                for c in self.cards.values()
                if (c.get("parent") or {}).get("identifier") == card["identifier"]
            ]
            return {"issue": {
                "identifier": card["identifier"], "description": "",
                "state": card["state"],
                "children": {"nodes": kids},
                "history": {"nodes": [
                    {"createdAt": _iso(1440), "toState": {"name": "In Progress"}}
                ]},
            }}
        if "inverseRelations" in q:
            return {"issue": {"identifier": card["identifier"], "description": "",
                              "inverseRelations": {"nodes": []}}}
        if "relations(first: 20)" in q:
            return {"issue": {"relations": {"nodes": []}}}
        if "children { nodes { state { name } } }" in q:
            kids = [
                {"state": c["state"]} for c in self.cards.values()
                if (c.get("parent") or {}).get("identifier") == card["identifier"]
            ]
            return {"issue": {"children": {"nodes": kids}}}
        if "comments(" in q:
            conn = card["comments"]
            if v.get("before"):
                # The older page of an exhausted window: everything the inline
                # window did not carry, oldest first.
                older = card.get("older_comments") or []
                return {"viewer": {"id": FLEET}, "issue": {"comments": {
                    "pageInfo": {"hasPreviousPage": False, "startCursor": "cursor-older"},
                    "nodes": older,
                }}}
            return {"viewer": {"id": FLEET}, "issue": {"comments": conn}}
        if "history(last: 10)" in q:
            return {"issue": {"history": {"nodes": []}}}
        if "id identifier title team" in q or "state { name type }" in q:
            return {"issue": {
                "id": card["id"], "identifier": card["identifier"],
                "title": card["title"], "team": {"id": "team-1"},
                "state": {"name": card["state"]["name"], "type": "started"},
                "labels": card["labels"], "children": card["children"],
            }}
        if "state { name }" in q:
            return {"issue": {"state": card["state"]}}
        raise AssertionError(f"unexpected Linear query: {query}")


@contextlib.contextmanager
def _linear(fake: FakeLinear):
    """Point every Linear read at `fake` and stub every write."""
    with patch.object(linear_ops, "gql", side_effect=fake.gql), \
            patch.object(linear_ops, "cmd_comment"), \
            patch.object(linear_ops, "cmd_advance"), \
            patch.object(linear_ops, "cmd_state"), \
            patch.object(linear_ops, "add_label"), \
            patch.object(linear_ops, "remove_label"):
        reconcile.reset_sweep_cards()
        yield


# --------------------------------------------------------------------------
# The busy repo: 260 Backlog cards under 5 active epics, 30 open PRs.
# --------------------------------------------------------------------------
def _busy_board(*, merged: str | None = None):
    """The card's fixture. When `merged` is given, that card sits Done under
    the first epic — whose every other child is Done too — and blocks one
    Backlog card under the second epic, so a merge of it justifies BOTH
    event-driven passes."""
    epics = [_epic(f"DRE-{100 + n}") for n in range(5)]

    def parent_of(n: int) -> dict:
        # Under epics 2..5 when a merge fixture is asked for: the merged
        # card's epic must be finished by the merge, so nothing else of it
        # may still be open.
        return _parent_ref(epics[n % 5] if merged is None else epics[1 + n % 4])

    review = [
        _card(f"DRE-{200 + n}", reconcile.REVIEW_LANE,
              minutes_stale=reconcile.STALE_MINUTES[reconcile.REVIEW_LANE] + 60,
              parent=parent_of(n))
        for n in range(30)
    ]
    backlog = [
        _card(f"DRE-{1000 + n}", "Backlog", parent=parent_of(n)) for n in range(260)
    ]
    cards = epics + review + backlog
    if merged is not None:
        dependent = backlog[0]["identifier"]
        cards.append(_card(merged, "Done", parent=_parent_ref(epics[0]),
                           blocks=(dependent,)))
        cards.append(_card("DRE-1999", "Done", parent=_parent_ref(epics[0])))
    return cards


def _open_pr(identifier: str) -> dict:
    n = int(identifier.split("-")[1])
    return {
        "number": n, "url": f"https://github.com/o/r/pull/{n}",
        "headRefName": f"agent/{identifier}-work", "state": "OPEN",
        "comments": [], "headRefOid": "0" * 40, "baseRefName": "main",
    }


def _gh_read(*args):
    """`pr_for`'s two listings: the head-branch search answers with the
    card's own open PR; anything else is empty."""
    if "--search" in args:
        needle = args[args.index("--search") + 1]  # head:agent/DRE-N
        return json.dumps([_open_pr(needle.split("/")[-1])])
    return "[]"


def _sweep_mocks():
    """Every seam a full sweep touches that would reach GitHub. The Linear
    reads under test stay REAL: both watchdogs, the Intake gate, the nudge
    loop's own reads, the promotion gate and — unlike test_sweep_request_budget,
    which stubs it — the epic close, which reads Linear once per epic."""
    return [
        mock.patch.object(reconcile, name)
        for name in (
            "drain_retiring_lanes", "unstick_conflicts", "refresh_stale_merge_refs",
            "retrigger_dead_heads", "flag_no_checks_prs", "flag_unowned_prs",
            "flag_unlanded_work", "fix_approved_but_red", "retry_dead_fix_runs",
            "redispatch_standing_verdicts", "recover_limit_deaths",
            "restart_answered_blockers", "review_dependabot_prs",
            "recover_crashed_reviews", "check_dependabot_capacity",
            "report_break_glass", "report_fix_concurrency",
            "report_evicted_fix_runs", "report_epic_growth",
        )
    ]


def _run_sweep(fake: FakeLinear, **main_kw) -> FakeLinear:
    with contextlib.ExitStack() as stack:
        for m in _sweep_mocks():
            stack.enter_context(m)
        stack.enter_context(mock.patch.object(reconcile, "MAX_WIP", 1000))
        stack.enter_context(mock.patch.object(reconcile, "gh_read", side_effect=_gh_read))
        stack.enter_context(mock.patch.object(reconcile, "verdict_bound", return_value=True))
        stack.enter_context(mock.patch.object(reconcile, "_nudge", return_value=False))
        stack.enter_context(_linear(fake))
        reconcile.main(**main_kw)
    return fake


# --------------------------------------------------------------------------
# 1: the busy sweep
# --------------------------------------------------------------------------
def test_a_busy_sweep_never_reads_a_cards_comments_per_card():
    """THE SPEND: the board read carried every card's comments, and the pass
    bought them again one request per card. Every reader that takes an
    identifier is served from the pass's own read now."""
    fake = _run_sweep(FakeLinear(_busy_board()))
    assert fake.per_card_comment_reads == 0, (
        f"{fake.per_card_comment_reads} per-card comment read(s) in one sweep "
        "— a reader is going back to Linear for comments the board read "
        "already carried"
    )


def test_a_busy_sweep_stays_inside_the_request_budget():
    fake = _run_sweep(FakeLinear(_busy_board()))
    assert fake.requests <= SWEEP_BUDGET, (
        f"one busy-repo sweep spent {fake.requests} Linear read requests, "
        f"budget {SWEEP_BUDGET} ({fake.per_card_comment_reads} of them per-card "
        "comment reads)"
    )


def test_the_sweep_still_refuses_every_unreleased_child_out_loud():
    """Guard the guard: the budget must not be met by a sweep that stopped
    evaluating the Backlog. Every child of an epic the second critic has not
    passed is still refused, and the refusal still reaches the card once."""
    fake = FakeLinear(_busy_board())
    with contextlib.ExitStack() as stack:
        for m in _sweep_mocks():
            stack.enter_context(m)
        stack.enter_context(mock.patch.object(reconcile, "MAX_WIP", 1000))
        stack.enter_context(mock.patch.object(reconcile, "gh_read", side_effect=_gh_read))
        stack.enter_context(mock.patch.object(reconcile, "verdict_bound", return_value=True))
        stack.enter_context(mock.patch.object(reconcile, "_nudge", return_value=False))
        stack.enter_context(_linear(fake))
        comment = stack.enter_context(patch.object(linear_ops, "cmd_comment"))
        reconcile.main()
    refused = {c.args[0] for c in comment.call_args_list
               if c.args[0].startswith("DRE-1") and len(c.args[0]) == 8}
    assert len(refused) == 260, (
        f"{len(refused)} of 260 Backlog cards were told why they are held — "
        "the cheaper sweep must reach every card the expensive one reached"
    )


# --------------------------------------------------------------------------
# 2: the cache itself
# --------------------------------------------------------------------------
def test_an_exhausted_inline_window_costs_one_paged_read_per_pass():
    """A card with more comments than the inline window carries gets ONE
    paged read — the full thread, oldest first — and every later reader in
    the pass is served from it."""
    newest = [_comment(f"newest {n}") for n in range(linear_ops.COMMENT_WINDOW)]
    busy = _card("DRE-50", reconcile.REVIEW_LANE, comments=_comments(newest, exhausted=True))
    busy["older_comments"] = [_comment("the oldest"), _comment("second oldest")]
    fake = FakeLinear([busy])
    with _linear(fake):
        linear_ops.open_pass()
        reconcile.active_cards(reconcile.WATCHDOG_LANES)
        first = linear_ops.comment_bodies("DRE-50")
        again = linear_ops.comment_bodies("DRE-50")
        count = linear_ops.count_comments("DRE-50", "oldest")
    assert first[:2] == ["the oldest", "second oldest"], "the older page leads"
    assert first[-1] == f"newest {linear_ops.COMMENT_WINDOW - 1}"
    assert again == first
    assert count == 2
    paged = [q for q, _ in fake.queries if "issue(id: $id)" in q and "comments(" in q]
    assert len(paged) == 2, (
        f"{len(paged)} comment page(s) read for one busy card — expected the "
        "window read plus its one older page, once for the whole pass"
    )


def test_the_cached_thread_still_says_who_wrote_each_comment():
    """The plan-critic gate's credential (DRE-2721): a round marker counts only
    when the fleet user wrote it. Served from the board read, the record must
    carry the same fact the dedicated query carried — and cost the one
    `viewer` read, never a per-card read."""
    epic = _epic("DRE-100", comments=_comments([
        _comment("the fleet's own round marker", by=FLEET),
        _comment("a teammate's note", by=SOMEONE_ELSE),
        _comment("an integration's post", by=None),
    ]))
    fake = FakeLinear([epic])
    with _linear(fake):
        linear_ops.open_pass()
        reconcile.active_cards(reconcile.WATCHDOG_LANES)
        records = linear_ops.comment_records("DRE-100")
    assert [r["authored_by_pipeline"] for r in records] == [True, False, False]
    assert [r["body"] for r in records] == [
        "the fleet's own round marker", "a teammate's note", "an integration's post",
    ]
    assert fake.per_card_comment_reads == 0


def test_a_comment_the_pass_posts_is_seen_by_the_next_read():
    """The cache must not outlive the write: a receipt this pass posted is on
    the card when the pass next counts receipts."""
    card = _card("DRE-7", "Todo")
    fake = FakeLinear([card])
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        reconcile.reset_sweep_cards()
        linear_ops.open_pass()
        reconcile.active_cards(reconcile.WATCHDOG_LANES)
        assert linear_ops.count_comments("DRE-7", "receipt") == 0
        card["comments"]["nodes"].append(_comment("a receipt"))
        linear_ops.cmd_comment("DRE-7", "a receipt")
        assert linear_ops.count_comments("DRE-7", "receipt") == 1


def test_a_board_read_selects_what_every_reader_needs():
    """Bodies alone cannot answer `first_comment_at` (createdAt) or
    `comment_records` (user), so the inline selection carries all three — a
    thread cached without a field a reader needs would silently answer
    wrong, which is worse than a request."""
    fake = FakeLinear()
    with _linear(fake):
        reconcile.active_cards(reconcile.WATCHDOG_LANES)
        reconcile.backlog_children()
    for query, _ in fake.queries:
        assert "comments(last: 50)" in query
        assert "createdAt" in query.split("comments(last: 50)")[1]
        assert "user { id }" in query.split("comments(last: 50)")[1]


def test_outside_a_pass_the_readers_are_unchanged():
    """The cache is a PASS's: `count-comments`, `dump-comments` and every other
    caller outside a sweep still make exactly the request they made before."""
    card = _card("DRE-8", "Todo", comments=_comments([_comment("x")]))
    fake = FakeLinear([card])
    with _linear(fake):
        reconcile.active_cards(reconcile.WATCHDOG_LANES)  # no open_pass()
        assert linear_ops.comment_bodies("DRE-8") == ["x"]
        assert linear_ops.comment_bodies("DRE-8") == ["x"]
    assert fake.per_card_comment_reads == 2


# --------------------------------------------------------------------------
# 3: merge-sync
# --------------------------------------------------------------------------
def _run_merge_sync(fake: FakeLinear, merged: str) -> list[str]:
    """One merge, the way linear-sync.yml runs it: card-done, the gate, then
    one reconcile process per flag the gate emitted — each a fresh process,
    so each starts with a fresh pass."""
    with contextlib.ExitStack() as stack:
        for m in _sweep_mocks():
            stack.enter_context(m)
        stack.enter_context(mock.patch.object(reconcile, "MAX_WIP", 1000))
        stack.enter_context(_linear(fake))
        linear_ops.cmd_card_done(merged, "https://github.com/o/r/pull/9")
        flags = merge_sweep_gate.decide(merged)
        for flag in flags:
            reconcile.reset_sweep_cards()
            with mock.patch.dict(os.environ, {"MERGED_CARD": merged}):
                reconcile.main(
                    promote_only=flag == merge_sweep_gate.PROMOTE,
                    close_only=flag == merge_sweep_gate.CLOSE_EPICS,
                )
    return flags


def test_the_merge_fixture_justifies_both_passes():
    """Guard the guard: the budget below is measured on a merge that DID
    unblock a card and DID finish its epic, so both passes really run."""
    fake = FakeLinear(_busy_board(merged="DRE-1998"))
    flags = _run_merge_sync(fake, "DRE-1998")
    assert flags == [merge_sweep_gate.PROMOTE, merge_sweep_gate.CLOSE_EPICS]


def test_one_merge_sync_stays_inside_the_request_budget():
    fake = FakeLinear(_busy_board(merged="DRE-1998"))
    _run_merge_sync(fake, "DRE-1998")
    assert fake.requests <= MERGE_SYNC_BUDGET, (
        f"one merge-sync spent {fake.requests} Linear read requests, budget "
        f"{MERGE_SYNC_BUDGET} ({fake.whole_backlog_reads} whole-Backlog page(s), "
        f"{fake.per_card_comment_reads} per-card comment read(s))"
    )


def test_merge_sync_never_walks_the_whole_backlog():
    """The merge changed ONE card's dependents and ONE parent. The pass reads
    those — by identifier — and nothing else."""
    fake = FakeLinear(_busy_board(merged="DRE-1998"))
    _run_merge_sync(fake, "DRE-1998")
    assert fake.whole_backlog_reads == 0, (
        f"{fake.whole_backlog_reads} whole-Backlog page(s) read on a merge — "
        "the event-driven promotion must be scoped to the merged card's dependents"
    )


def test_merge_sync_still_acts_on_the_dependent_and_the_epic():
    """Scoping must not lose the work: the unblocked dependent is still
    evaluated (and told why it is held, or promoted), and the finished epic
    still closes."""
    fake = FakeLinear(_busy_board(merged="DRE-1998"))
    with contextlib.ExitStack() as stack:
        for m in _sweep_mocks():
            stack.enter_context(m)
        stack.enter_context(mock.patch.object(reconcile, "MAX_WIP", 1000))
        stack.enter_context(_linear(fake))
        state = stack.enter_context(patch.object(linear_ops, "cmd_state"))
        comment = stack.enter_context(patch.object(linear_ops, "cmd_comment"))
        linear_ops.cmd_card_done("DRE-1998", "https://github.com/o/r/pull/9")
        for flag in merge_sweep_gate.decide("DRE-1998"):
            reconcile.reset_sweep_cards()
            with mock.patch.dict(os.environ, {"MERGED_CARD": "DRE-1998"}):
                reconcile.main(
                    promote_only=flag == merge_sweep_gate.PROMOTE,
                    close_only=flag == merge_sweep_gate.CLOSE_EPICS,
                )
    spoken_to = [c.args[0] for c in comment.call_args_list]
    assert "DRE-1000" in spoken_to, "the unblocked dependent was never evaluated"
    assert ("DRE-100", "Done") in [c.args for c in state.call_args_list], (
        "the merged card's finished epic was not closed"
    )
    # The 259 other Backlog cards are the cron's business, not this merge's.
    assert not any(ident.startswith("DRE-10") and ident not in ("DRE-100", "DRE-1000")
                   for ident in spoken_to)


def test_an_unreadable_merge_scope_falls_open_to_the_full_pass():
    """"We could not look" is not "nothing to do" — the gate's own rule. A
    scope read that fails runs the pass it always ran."""
    fake = FakeLinear(_busy_board(merged="DRE-1998"))
    real = fake.gql

    def flaky(query, variables=None):
        if "relations(first: 50)" in query and (variables or {}).get("id") == "DRE-1998":
            raise linear_ops.LinearError("scope read exploded")
        return real(query, variables)

    with contextlib.ExitStack() as stack:
        for m in _sweep_mocks():
            stack.enter_context(m)
        stack.enter_context(mock.patch.object(reconcile, "MAX_WIP", 1000))
        stack.enter_context(_linear(fake))
        stack.enter_context(patch.object(linear_ops, "gql", side_effect=flaky))
        with mock.patch.dict(os.environ, {"MERGED_CARD": "DRE-1998"}):
            reconcile.main(promote_only=True)
    assert fake.whole_backlog_reads >= 1


def test_the_merge_step_hands_reconcile_the_merged_card():
    """The wiring: linear-sync.yml's gate block passes MERGED_CARD to every
    reconcile invocation it makes, or the scoping above never engages live."""
    import test_merge_sweep_gate as gate_tests

    line = gate_tests.reconcile_invocations(gate_tests.merge_step_run())[0]
    assert 'MERGED_CARD="$CARD"' in line, line
