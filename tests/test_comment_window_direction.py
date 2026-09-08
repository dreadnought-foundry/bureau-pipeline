"""RED-first: the per-card comment window is the NEWEST fifty (DRE-3250).

WHAT LINEAR ACTUALLY DOES, measured against the live API on 2026-09-06 while
DRE-3236 was being built (DRE-3060, 47 comments): an issue's `comments` are
ordered **NEWEST FIRST**. `comments(first: 3)` answered 09-06 / 09-05 / 09-04;
`comments(last: 3)` answered the three OLDEST, ascending. So `comments(last:
50)` — the window every board read in this repo inlined — is the card's fifty
OLDEST comments, not its fifty newest.

WHY THAT IS A DEFECT. Every reader of that window takes it for the card's
recent history: the newest routing verdict, the newest critic marker, the
newest run receipt, the medic's park receipt, the stranded watchdog's "no
comment since". While no card had crossed fifty comments the window WAS the
whole thread and nothing was wrong. The day one crosses, the window is the
card's OLDEST fifty and every "latest" fact on it is stale: a verdict from
before a re-plan, a marker from a spent cycle, a receipt from a dead run.

DRE-3236's paged read walks past the window, but only inside a sweep's pass
and only for the readers that take an IDENTIFIER. The readers that take the
window at face value — every one that reads it off the card the board
returned, and every caller outside a sweep (the in-run dead-run cap, the
medic's park read, the model-fallback selector, every CLI call) — see the
window and nothing else. Those are what this file pins.

THE FIXTURE. Sixty comments, served by a fake that orders them the way the API
does rather than the way the code imagined — `first:` newest-first, `last:`
the oldest N ascending, `after:`/`before:` paging in the directions each of
those implies. The STALE facts live in the ten oldest comments and the LIVE
ones in the ten newest, so a window pointing the wrong way answers with the
stale fact rather than with nothing: no assertion below can be met by reading
no comments at all.

Run: cd bureau-pipeline && python3 -m pytest tests/test_comment_window_direction.py -v
"""
from __future__ import annotations

import inspect
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import backlog_cutover  # noqa: E402
import dead_run  # noqa: E402
import limit_recovery  # noqa: E402
import linear_ops  # noqa: E402
import medic_retry  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

CARD = "DRE-3250"
FLEET = "user-the-fleet-key"
SOMEONE_ELSE = "user-somebody-else"

#: The thread is longer than the window on purpose — 60 against a 50-comment
#: window, so ten comments fall outside it whichever end it is taken from.
THREAD = 60
OUTSIDE = THREAD - linear_ops.COMMENT_WINDOW  # the ten that must fall out

STALE_RUN = "111111"
LIVE_RUN = "999999"
SPOKEN_ONCE = "🚨 the sweep said this once"


def _iso(minutes_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _comment(body: str, *, minutes_ago: float, by: str | None = FLEET) -> dict:
    return {"body": body, "createdAt": _iso(minutes_ago),
            "user": {"id": by} if by else None}


def _thread() -> list[dict]:
    """Sixty comments OLDEST→NEWEST — the card's history as a person reading
    the card top to bottom meets it.

    Comments 1-10 are the card's spent past and 51-60 its present; the forty
    between them are filler that carries no fact and no pipeline glyph, so it
    can neither answer a question nor close a marker. Each fact appears in
    BOTH halves with a different value, so a reader pointed at the wrong end
    returns the stale value rather than nothing.
    """
    spent = 600.0   # ten hours ago: the previous cycle
    live = 30.0     # half an hour ago: this cycle
    old = [
        # The verdict the card carried before it was re-planned.
        _comment(f"{routing_verdict.VERDICT_MARK} {routing_verdict.VERDICT_TAG}: "
                 "PARKED", minutes_ago=spent + 9),
        _comment("plan-critic round 1 — REQUEST_CHANGES", minutes_ago=spent + 8),
        # The run receipt of a build that died ten hours ago.
        _comment(f"{reconcile.RUN_MARKER} sonnet · run "
                 f"https://github.com/o/r/actions/runs/{STALE_RUN}",
                 minutes_ago=spent + 7),
        _comment(f"🪦 {dead_run.DEAD_TAG}: the run ended with no PR",
                 minutes_ago=spent + 6),
        _comment(f"🪦 {dead_run.DEAD_TAG}: the run ended with no PR",
                 minutes_ago=spent + 5),
        _comment(f"{SPOKEN_ONCE}, ten hours ago", minutes_ago=spent + 4),
        # A park from a previous life of the card, since un-parked.
        _comment(f"{medic_retry.HELD_RECEIPT_MARK}a previous life of this card)",
                 minutes_ago=spent + 3),
        _comment("a teammate's note from the spent cycle", minutes_ago=spent + 2,
                 by=SOMEONE_ELSE),
        _comment("the last word of the spent cycle", minutes_ago=spent + 1),
        # LAST of the old half: a limit death nothing inside the old window
        # closes, so a window at the wrong end reports the card as waiting on
        # a usage limit that came down nine hours ago.
        _comment(dead_run.limit_marker("claude", "build", None, STALE_RUN),
                 minutes_ago=spent),
    ]
    filler = [
        # No glyph, no marker, no verdict: filler that cannot answer anything.
        _comment(f"a note from the middle of the thread ({n})",
                 minutes_ago=500.0 - n)
        for n in range(THREAD - 20)
    ]
    new = [
        # The card was re-planned, and the verdict that routes it is this one.
        _comment(f"{routing_verdict.VERDICT_MARK} {routing_verdict.VERDICT_TAG}: "
                 "FLEET", minutes_ago=live + 9),
        _comment("plan-critic round 2 — APPROVE", minutes_ago=live + 8),
        # The budget reset: every death BEFORE it is spent history.
        _comment(f"♻️ {dead_run.RESET_TAG}: a human un-parked this card",
                 minutes_ago=live + 7),
        _comment(f"🪦 {dead_run.DEAD_TAG}: the run ended with no PR",
                 minutes_ago=live + 6),
        # A pipeline receipt AFTER the limit marker — the limit is spent.
        _comment("♻️ the sweep re-entered the build stage", minutes_ago=live + 5),
        _comment("a teammate's note from this cycle", minutes_ago=live + 4,
                 by=SOMEONE_ELSE),
        _comment(f"{SPOKEN_ONCE}, half an hour ago", minutes_ago=live + 3),
        # THIS run's park receipt — the one the medic must not miss.
        _comment(f"{medic_retry.HELD_RECEIPT_MARK}the turn cap, this run)",
                 minutes_ago=live + 2),
        # The live run receipt: the run the watchdog must ask GitHub about.
        _comment(f"{reconcile.RUN_MARKER} opus · run "
                 f"https://github.com/o/r/actions/runs/{LIVE_RUN}",
                 minutes_ago=live + 1),
        _comment("⏳ 3/5 implementation green", minutes_ago=live),
    ]
    nodes = old + filler + new
    assert len(nodes) == THREAD
    return nodes


# --------------------------------------------------------------------------
# The fake Linear, ordering comments the way the API orders them.
# --------------------------------------------------------------------------
_WINDOW_ARGS = re.compile(r"comments\(([^)]*)\)")


def _window_args(query: str, variables: dict) -> dict:
    """The comment window's arguments AS THE PRODUCTION QUERY WRITES THEM.

    Read off the query text rather than declared by the test, so the direction
    under test is the one the code actually asks Linear for.
    """
    found = _WINDOW_ARGS.search(" ".join(query.split()))
    if not found:
        return {}
    args = {}
    for pair in found.group(1).split(","):
        if ":" not in pair:
            continue
        key, value = (part.strip() for part in pair.split(":", 1))
        args[key] = variables.get(value[1:]) if value.startswith("$") else value
    return args


class FakeComments:
    """Linear's own ordering, and nothing else: NEWEST FIRST.

    `first: n` — the n newest, newest first; `after:` pages on toward the
    older. `last: n` — the n OLDEST, ascending; `before:` pages on toward the
    newer. Both directions are served, so a query pointed the wrong way gets
    the wrong fifty rather than an error.
    """

    def __init__(self, thread: list[dict]):
        self.thread = thread                       # oldest → newest
        self.newest_first = list(reversed(thread))  # the connection's order
        self.windows: list[dict] = []              # every window asked for

    def serve(self, query: str, variables: dict) -> dict:
        args = _window_args(query, variables)
        self.windows.append(args)
        if "first" in args:
            start = int(args["after"]) + 1 if args.get("after") else 0
            size = int(args["first"])
            nodes = self.newest_first[start:start + size]
            return {
                "pageInfo": {
                    "hasNextPage": start + size < len(self.newest_first),
                    "hasPreviousPage": start > 0,
                    "endCursor": str(start + len(nodes) - 1),
                },
                "nodes": nodes,
            }
        size = int(args.get("last") or linear_ops.COMMENT_WINDOW)
        start = int(args["before"]) if args.get("before") else 0
        nodes = self.thread[start:start + size]
        return {
            "pageInfo": {
                "hasNextPage": start > 0,
                "hasPreviousPage": start + size < len(self.thread),
                "endCursor": str(start + len(nodes)),
            },
            "nodes": nodes,
        }


class FakeLinear:
    def __init__(self, thread: list[dict] | None = None):
        self.comments = FakeComments(thread if thread is not None else _thread())
        self.queries: list[str] = []

    def gql(self, query, variables=None):
        v = variables or {}
        self.queries.append(query)
        q = " ".join(query.split())
        if "issues(" in q:  # a board read
            return {"issues": {
                "nodes": [self.card(query, v)],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }}
        if "comments(" in q:  # one card's thread, however it was selected
            return {"viewer": {"id": FLEET}, "issue": {
                "identifier": CARD,
                "state": {"name": "Todo"},
                "labels": {"nodes": [{"name": "repo:agent-bureau"}]},
                "comments": self.comments.serve(query, v),
            }}
        if "viewer" in q:
            return {"viewer": {"id": FLEET}}
        raise AssertionError(f"unexpected Linear query: {query}")

    def card(self, query: str, variables: dict) -> dict:
        """The card as a board read returns it — comments inline."""
        return {
            "id": f"uuid-{CARD}", "identifier": CARD, "title": "a busy card",
            "description": "work", "createdAt": _iso(2000), "updatedAt": _iso(600),
            "state": {"name": "Todo"},
            "labels": {"nodes": [{"name": "repo:agent-bureau"}]},
            "parent": None, "children": {"nodes": []},
            "inverseRelations": {"nodes": []},
            "comments": self.comments.serve(query, variables),
        }


@pytest.fixture(autouse=True)
def _fresh_pass():
    reconcile.reset_sweep_cards()
    yield
    reconcile.reset_sweep_cards()


@pytest.fixture()
def fake():
    f = FakeLinear()
    with mock.patch.object(linear_ops, "gql", side_effect=f.gql):
        yield f


def _bodies(nodes) -> list[str]:
    return [n["body"] for n in nodes]


def _swept_card(fake: FakeLinear) -> dict:
    """The card as ONE sweep reads it: the board query the sweep really makes."""
    return reconcile.active_cards(("Todo",))[0]


def _open_sweep(fake: FakeLinear) -> dict:
    """A sweep's pass, from the top: an empty cache, then the board read that
    fills it — the state every in-sweep reader runs against."""
    reconcile.reset_sweep_cards()
    linear_ops.open_pass()
    return _swept_card(fake)


def _created_at(fake: FakeLinear, needle: str) -> str:
    """When the OLDEST comment carrying `needle` was posted, over the whole
    fixture thread."""
    return next(c["createdAt"] for c in fake.comments.thread if needle in c["body"])


# --------------------------------------------------------------------------
# 1: the window itself
# --------------------------------------------------------------------------
def test_the_board_reads_the_newest_fifty_oldest_first(fake):
    """The window the sweep carries is the card's fifty NEWEST comments, in
    the oldest→newest order every reader here is written against."""
    bodies = reconcile.card_comment_bodies(_swept_card(fake))
    thread = _bodies(fake.comments.thread)
    assert len(bodies) == linear_ops.COMMENT_WINDOW
    assert bodies == thread[OUTSIDE:], (
        "the sweep's window is not the newest fifty, oldest first — it read "
        f"{bodies[0]!r} … {bodies[-1]!r}"
    )
    assert bodies[-1] == thread[-1], "the newest comment is not in the window"


def test_the_board_query_asks_for_the_window_the_api_orders_first(fake):
    """One place decides the direction, and the board reads ask for it that
    way: `last:` on a newest-first connection is the OLDEST fifty."""
    _swept_card(fake)
    reconcile.backlog_children()
    for query in fake.queries:
        flat = " ".join(query.split())
        assert "comments(last:" not in flat, (
            "a board read still asks for the card's OLDEST comments: " + flat
        )
    assert fake.comments.windows and all(
        "first" in w for w in fake.comments.windows
    ), fake.comments.windows


# --------------------------------------------------------------------------
# 2: every reader of the window, on the read that has only the window —
#    off the card the board returned, or from outside a sweep's pass.
# --------------------------------------------------------------------------
def test_the_newest_routing_verdict_is_the_one_the_sweep_routes_on(fake):
    """A card re-planned from PARKED to FLEET: the window must carry the
    verdict that routes it now, not the one it carried before the re-plan."""
    bodies = reconcile.card_comment_bodies(_swept_card(fake))
    assert routing_verdict.verdict_on(bodies) == "FLEET"
    assert not routing_verdict.is_parked(bodies), (
        "the sweep read a spent PARKED verdict, and a PARKED card is never "
        "promoted and never reported as stalled"
    )


def test_the_newest_critic_marker_is_the_one_comment_records_reports(fake):
    """`comment_records` — the plan-critic gate's credential (DRE-2721) —
    reports the newest rounds, still oldest→newest, still with authorship."""
    records = linear_ops.comment_records(CARD)
    rounds = [r for r in records if "plan-critic round" in r["body"]]
    assert [r["body"] for r in rounds] == ["plan-critic round 2 — APPROVE"], rounds
    assert rounds[0]["authored_by_pipeline"] is True
    assert [r["authored_by_pipeline"] for r in records
            if "teammate" in r["body"]] == [False]


def test_the_newest_run_receipt_is_the_run_the_watchdog_asks_about(fake):
    """The liveness check reads the CURRENT attempt's run id off the newest
    🧠 receipt. Pointed at the oldest fifty it asks GitHub about a run that
    concluded ten hours ago, reads `completed`, and requeues a live card."""
    with mock.patch.object(reconcile, "gh_actions_read",
                           return_value="in_progress") as gh:
        assert reconcile.agent_run_alive(CARD) is True
    asked = [" ".join(c.args) for c in gh.call_args_list]
    assert any(LIVE_RUN in args for args in asked), asked
    assert not any(STALE_RUN in args for args in asked), (
        f"the watchdog asked about run {STALE_RUN}, which died ten hours ago"
    )


def test_the_dead_run_count_honours_a_reset_inside_the_window(fake):
    """`count_comments(..., since=RESET_TAG)` counts the deaths AFTER the most
    recent reset — the in-run cap agent-task applies, outside any sweep. Both
    the reset and the death after it are in the newest ten, so a window at the
    wrong end counts a budget a human already refunded."""
    assert linear_ops.count_comments(CARD, dead_run.DEAD_TAG,
                                     since=dead_run.RESET_TAG) == 1
    assert linear_ops.count_comments(CARD, dead_run.DEAD_TAG) == 1, (
        "the window carries the deaths inside it — the two from the spent "
        "cycle are outside the newest fifty"
    )


def test_comment_bodies_and_timeline_agree_on_the_window(fake):
    """The two shapes readers take the window in — bodies, and bodies with
    their clocks — are the same fifty comments in the same order, and the same
    fifty the board read carried."""
    bodies = linear_ops.comment_bodies(CARD)
    timeline = linear_ops.comment_timeline(CARD)
    assert bodies == [row["body"] for row in timeline]
    assert bodies == reconcile.card_comment_bodies(_swept_card(fake))
    assert bodies == _bodies(fake.comments.thread)[OUTSIDE:]
    assert all(row["createdAt"] for row in timeline)


def test_first_comment_at_reads_the_oldest_of_whatever_it_was_given(fake):
    """"How long has this stood?" is answered off the OLDEST comment carrying
    the marker. On a bare window read that is the oldest one INSIDE the
    window; inside a sweep's pass, where the whole thread is read, it is the
    true oldest. Both are pinned, because the two differ on a busy card."""
    windowed = linear_ops.first_comment_at(CARD, SPOKEN_ONCE)
    assert windowed == _created_at(fake, f"{SPOKEN_ONCE}, half an hour ago"), (
        "outside a pass the answer is the oldest occurrence in the WINDOW, "
        "and the window is the newest fifty"
    )
    _open_sweep(fake)
    assert linear_ops.first_comment_at(CARD, SPOKEN_ONCE) == _created_at(
        fake, f"{SPOKEN_ONCE}, ten hours ago"
    ), "inside a pass the paged read reaches the true oldest occurrence"


def test_the_medic_reads_this_runs_park_receipt(fake):
    """The medic refuses to retry a card the pipeline just parked, and reads
    its own card, outside any sweep. The park receipt is in the newest ten; a
    spent park from a previous life of the card is explicitly not it."""
    facts = medic_retry.card_facts(CARD)
    assert [row["body"] for row in facts["comments"]] == _bodies(
        fake.comments.thread
    )[OUTSIDE:]
    reason = medic_retry.park_reason(
        state=dead_run.PARK_STATE, labels=(), receipts=facts["comments"],
        run_started_at=_iso(60),
    )
    assert reason, (
        "the medic could not see this run's park receipt and would retry a "
        "card the pipeline had just parked"
    )


def test_limit_recovery_does_not_re_enter_a_spent_limit(fake):
    """A limit marker from the spent cycle is the LAST word of the card's
    oldest fifty. Read that window and the sweep re-enters the build stage of
    a card that came back from the limit nine hours ago."""
    bodies = reconcile.card_comment_bodies(_swept_card(fake))
    assert limit_recovery.waiting(bodies) is None


def test_the_stranded_watchdog_sees_the_live_receipt(fake):
    """The watchdog's proof-of-life read (🧠/⏳) must find the receipt of the
    run that is going NOW — the oldest fifty answer with one from a run the
    dead-run machinery closed ten hours ago."""
    bodies = reconcile.card_comment_bodies(_swept_card(fake))
    alive = [b for b in bodies if b.lstrip().startswith(reconcile._LIFE_PREFIXES)]
    assert any(LIVE_RUN in b for b in alive)
    assert not any(STALE_RUN in b for b in alive)


def test_the_cutover_read_carries_the_same_window():
    """`backlog_cutover` reads its own population, and reads run receipts off
    it to decide what is in flight — the same window, the same direction."""
    flat = " ".join(backlog_cutover.POPULATION_QUERY.split())
    assert "comments(last:" not in flat, flat
    assert linear_ops.COMMENT_WINDOW_GQL in flat


# --------------------------------------------------------------------------
# 3: the page beyond the window
# --------------------------------------------------------------------------
def test_an_exhausted_window_is_read_the_right_way_round(fake):
    """`hasNextPage` on a newest-first window means OLDER comments lie beyond
    it. Read the wrong flag and a partial window looks complete, the pass
    caches it, and the paged read DRE-3236 added never fires."""
    board = _swept_card(fake)
    assert linear_ops.window_is_partial(board["comments"]), (
        "a sixty-comment card's fifty-comment window is not being reported as "
        "partial — nothing will page the rest of it"
    )
    _open_sweep(fake)
    whole = linear_ops.comment_bodies(CARD)
    assert whole == _bodies(fake.comments.thread), (
        "the pass's paged read did not return the whole thread, oldest→newest"
    )
    assert linear_ops.comment_bodies(CARD) == whole, (
        "the paged thread is read once and cached for the pass"
    )


def test_a_window_that_holds_the_whole_thread_is_cached_whole():
    """The other side of the same flag: a short thread's window IS the thread,
    so it is served from the board read and costs no page."""
    short = _thread()[:5]
    f = FakeLinear(short)
    with mock.patch.object(linear_ops, "gql", side_effect=f.gql):
        _open_sweep(f)
        before = len(f.queries)
        assert linear_ops.comment_bodies(CARD) == _bodies(short)
        assert len(f.queries) == before, "a complete window must cost no read"


# --------------------------------------------------------------------------
# 4: the direction is decided in ONE place, and stated
# --------------------------------------------------------------------------
def test_no_reader_in_the_repo_takes_the_oldest_fifty_for_the_newest():
    """The acceptance criterion, as a check: nothing in `scripts/` asks for a
    `comments(last: N)` window. `last:` is not wrong in itself — it is wrong
    read as "the newest", which is what every reader of this window does."""
    offenders = []
    for path in sorted((ROOT / "scripts").rglob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"comments\(\s*last\s*:", line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not offenders, (
        "these comment windows read the card's OLDEST comments:\n"
        + "\n".join(offenders)
    )


def test_the_window_selection_has_one_definition():
    """One place decides the direction, and every read spells it the same
    way — `linear_ops.COMMENT_WINDOW_GQL`."""
    assert f"comments(first: {linear_ops.COMMENT_WINDOW}" in linear_ops.COMMENT_WINDOW_GQL
    assert "hasNextPage" in linear_ops.COMMENT_WINDOW_GQL
    assert linear_ops.COMMENT_FIELDS in linear_ops.COMMENT_WINDOW_GQL
    sources = {
        "reconcile._fetch_active_cards": inspect.getsource(reconcile._fetch_active_cards),
        "reconcile.backlog_children": inspect.getsource(reconcile.backlog_children),
        "medic_retry.card_facts": inspect.getsource(medic_retry.card_facts),
        "backlog_cutover.POPULATION_QUERY": backlog_cutover.POPULATION_QUERY,
        "linear_ops._THREAD_QUERY": linear_ops._THREAD_QUERY,
    }
    for name, source in sources.items():
        assert (
            "COMMENT_WINDOW_GQL" in source
            or linear_ops.COMMENT_WINDOW_GQL in " ".join(source.split())
        ), f"{name} spells its comment window out by hand"


def test_the_window_is_reversed_once_and_says_so():
    """`window_nodes` is the ONE place the API's newest-first order becomes
    the oldest→newest order every reader documents."""
    thread = _thread()
    assert linear_ops.window_nodes({"nodes": list(reversed(thread))}) == thread
    assert linear_ops.window_nodes(None) == []
    assert linear_ops.window_nodes({}) == []
