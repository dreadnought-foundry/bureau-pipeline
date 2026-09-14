"""RED-first: the board snapshot — the real board, in the shape the sweep
reads (DRE-3638) — and, since DRE-3918, a SYNTHETIC board in that shape.

DRE-3918: THE REAL BOARD IS NEVER COMMITTED HERE. This repository is public.
DRE-3638 committed the real 2026-09-12 board (every card's text, customer names
and, in one commit, real addresses) and it had to be taken back out. The tool
still reads the real board for local use, but refuses to write it anywhere in
this repository, and the tests below run on `board_snapshot.synthetic()`: the
same contract and the same shapes, and no real words. The guard in
`tests/test_no_real_board_in_repo.py` fails on any board snapshot or any real
address under `tests/fixtures/`.

WHY THIS EXISTS. The CI ceiling on the sweep's Linear spend passes at 30 while
a live pass costs 65 on bureau-pipeline and 92 on agent-bureau, because the
fixture the ceiling runs over is not a board: `test_sweep_request_cuts.py`
hand-builds 5 epics and 260 Backlog cards and mocks 18 of the sweep's phases
out entirely. A hand-built board answers the questions its author thought of.

So `scripts/board_snapshot.py take --out <path>` reads the REAL board once —
every DRE card in the lanes the sweep reads — scrubs it, and writes it in the
shape the sweep's own queries return, for the replay test (a sibling card) to
run every phase over.

WHAT IS UNDER TEST:
  * The read: the sweep's own lane set, the sweep's own comment window, one
    paged read through `linear_ops.gql_paged`, under REQUEST_CEILING requests,
    and never a mutation.
  * The scrub, as a PURE function: comment bodies cut to their first 200
    characters (every marker this pipeline reads is anchored at the start of a
    body), descriptions kept whole (growth records, wave-commitment blocks and
    blocker lines live anywhere in them), `user` reduced to its opaque `id`,
    and a card carrying exactly the contract keys — a key the sweep never reads
    is not in the file.
  * The redaction: an email address is taken out of every free-text field
    before it is written, and before the 200-character cut. This repository is
    PUBLIC, a committed snapshot is permanent, and the board's prose quotes
    real customer and personal addresses that no structural scrub can reach.
  * The refusal: `take --out` inside this repository exits 3 before reading.
  * The synthetic board: deterministic, under 4 MB, no real words and no
    address, every card validating against the contract keys spelled out
    below, and carrying the shapes a real board has.

The contract keys are spelled out HERE, literally, rather than imported from
the script: a test that reads the writer's own constant proves the writer
agrees with itself. The sibling replay test reads `board_snapshot.synthetic()`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_board_snapshot.py -v
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")  # reconcile

import board_snapshot  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402

#: Where a real snapshot must NEVER be written (DRE-3918): anywhere inside this
#: repository, which is public. The 2026-09-12 real board was committed here by
#: DRE-3638 and had to be taken back out.
REPO_ROOT = ROOT

#: The contract, spelled out. Every card in the snapshot carries exactly these
#: keys, in the shape the sweep's board reads return them.
CARD_KEYS = {
    "id", "identifier", "title", "description", "createdAt", "updatedAt",
    "state", "labels", "parent", "children", "comments", "relations",
    "inverseRelations", "history",
}
TOP_LEVEL_KEYS = {"taken_at", "team", "lanes", "cards"}


# ---------------------------------------------------------------------------
# a fake board, and the reader that pages over it the way Linear does
# ---------------------------------------------------------------------------
_PAGE = 100


def _raw_card(identifier: str, lane: str = "Backlog", **over) -> dict:
    """One card as the snapshot's own query returns it, before the scrub.

    `lane` names the state; anything in `over` REPLACES a key outright, which
    is how the shape tests hand it a node carrying more than the contract."""
    card = {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "a card",
        "description": "work",
        "createdAt": "2026-09-01T00:00:00.000Z",
        "updatedAt": "2026-09-12T00:00:00.000Z",
        "state": {"name": lane},
        "labels": {"nodes": [{"name": "repo:bureau-pipeline"}]},
        "parent": None,
        "children": {"nodes": []},
        "comments": {"pageInfo": {"hasNextPage": False, "endCursor": "c-end"},
                     "nodes": []},
        "relations": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        "inverseRelations": {"nodes": []},
        "history": {"nodes": []},
    }
    card.update(over)
    return card


class FakeLinear:
    """Answers the snapshot's one query, pages of 100, and records what it was
    asked — so a test can count requests and read every query sent."""

    def __init__(self, cards=()):
        self.cards = list(cards)
        self.queries: list[tuple[str, dict]] = []

    @property
    def requests(self) -> int:
        return len(self.queries)

    def gql(self, query, variables=None):
        v = variables or {}
        self.queries.append((query, v))
        wanted = set(v.get("states") or ())
        nodes = [c for c in self.cards if c["state"]["name"] in wanted]
        start = int(v.get("after") or 0)
        chunk = nodes[start:start + _PAGE]
        more = start + _PAGE < len(nodes)
        return {"issues": {"nodes": chunk, "pageInfo": {
            "hasNextPage": more, "endCursor": str(start + _PAGE)}}}


def _board(n: int, state: str = "Backlog") -> FakeLinear:
    return FakeLinear([_raw_card(f"DRE-{i}", state) for i in range(n)])


# ---------------------------------------------------------------------------
# the read: whose lanes, whose window, how many requests
# ---------------------------------------------------------------------------
def test_the_lanes_are_the_sweeps_own_plus_backlog():
    """The sweep decides which lanes it reads; the snapshot follows it rather
    than keeping a second list that can drift."""
    for lane in reconcile.SWEPT_LANES:
        assert lane in board_snapshot.LANES
    for lane in ("Backlog", "Intake", "Planning"):
        assert lane in board_snapshot.LANES
    assert len(board_snapshot.LANES) == len(set(board_snapshot.LANES))


def test_the_query_asks_the_sweeps_own_comment_window():
    """ONE definition of which fifty comments and which way round (DRE-3250) —
    interpolated, never re-spelled here."""
    assert linear_ops.COMMENT_WINDOW_GQL in board_snapshot.CARD_QUERY


def test_the_query_can_page():
    """`gql_paged` refuses a query that cannot paginate; this one can."""
    assert "$after" in board_snapshot.CARD_QUERY
    assert "pageInfo { hasNextPage endCursor }" in board_snapshot.CARD_QUERY


def test_taking_the_snapshot_is_one_paged_read_of_the_board():
    fake = _board(250)
    with patch.object(linear_ops, "gql", fake.gql):
        snapshot = board_snapshot.take()
    assert len(snapshot["cards"]) == 250
    assert fake.requests == 3  # 100 + 100 + 50, one page each
    assert fake.requests < board_snapshot.REQUEST_CEILING
    # every page asked for the same lanes, and nothing else
    assert {tuple(v["states"]) for _, v in fake.queries} == {board_snapshot.LANES}


def test_the_snapshot_carries_its_provenance():
    fake = _board(3)
    with patch.object(linear_ops, "gql", fake.gql):
        snapshot = board_snapshot.take()
    assert set(snapshot) == TOP_LEVEL_KEYS
    assert snapshot["team"] == "DRE"
    assert tuple(snapshot["lanes"]) == board_snapshot.LANES
    assert snapshot["taken_at"].endswith("Z")
    datetime.fromisoformat(snapshot["taken_at"].replace("Z", "+00:00"))


def test_a_read_over_the_request_ceiling_is_reported_not_silently_taken(tmp_path):
    """The ceiling is the card's own: taking the snapshot costs under 20
    requests. A board that outgrows it must say so rather than pass."""
    fake = _board(250)
    out = tmp_path / "x.json"
    with patch.object(linear_ops, "gql", fake.gql), \
         patch.object(board_snapshot, "REQUEST_CEILING", 2):
        code = board_snapshot.main(["take", "--out", str(out)])
    assert code != 0
    assert out.exists()  # the read was paid for; the file is still written


def test_the_script_never_sends_a_mutation():
    fake = _board(120)
    with patch.object(linear_ops, "gql", fake.gql):
        board_snapshot.take()
    assert fake.requests >= 2
    for query, _ in fake.queries:
        assert not query.lstrip().startswith("mutation")
        assert "mutation" not in query


def test_a_mutation_is_refused_at_the_seam_not_only_in_a_test():
    """Read-only is enforced where the request is sent, so it holds for any
    caller — not only for the test above."""
    board_snapshot.assert_read_only("query($after: String) { issues { nodes { id } } }")
    with pytest.raises(board_snapshot.SnapshotError):
        board_snapshot.assert_read_only("mutation { issueUpdate { success } }")
    with pytest.raises(board_snapshot.SnapshotError):
        board_snapshot.assert_read_only("\n  mutation Foo { commentCreate { success } }")


# ---------------------------------------------------------------------------
# the scrub, as a pure function
# ---------------------------------------------------------------------------
def _comment(body="hello", *, user=None, at="2026-09-12T00:00:00.000Z") -> dict:
    return {"body": body, "createdAt": at, "user": user}


def test_a_body_over_two_hundred_characters_is_cut_to_two_hundred():
    body = "🧭 routing-verdict: FLEET " + ("x" * 500)
    scrubbed = board_snapshot.scrub_comment(_comment(body))
    assert scrubbed["body"] == body[:200]
    assert len(scrubbed["body"]) == 200


def test_a_body_at_or_under_the_limit_is_untouched():
    for length in (0, 1, 199, 200):
        body = "y" * length
        assert board_snapshot.scrub_comment(_comment(body))["body"] == body


def test_a_missing_body_reads_as_empty_not_as_a_crash():
    assert board_snapshot.scrub_comment({"createdAt": "t"})["body"] == ""


def test_a_description_is_kept_whole():
    """The sweep reads growth records, wave-commitment blocks and blocker lines
    ANYWHERE in a description — truncating one would change what the replay
    sees."""
    description = "**Blocked by:** DRE-1\n" + ("z" * 5000)
    card = board_snapshot.scrub_card(_raw_card("DRE-1", description=description))
    assert card["description"] == description


def test_a_null_description_stays_null():
    assert board_snapshot.scrub_card(_raw_card("DRE-1", description=None))[
        "description"] is None


# ---------------------------------------------------------------------------
# the redaction: free text is where the addresses actually are
# ---------------------------------------------------------------------------
def test_an_address_in_a_description_is_redacted():
    """The `user` scrub reaches a name and an address Linear hands over as a
    FIELD. An address a human typed into a description is prose, and this
    repository is public — so it is redacted too."""
    card = board_snapshot.scrub_card(_raw_card(
        "DRE-1",
        description="ask ada@customer.example.com, cc bo@customer.example.com for the export",
    ))
    assert "@customer.example.com" not in card["description"]
    assert "ada" not in card["description"]
    assert card["description"] == (
        f"ask {board_snapshot.REDACTED_EMAIL}, cc {board_snapshot.REDACTED_EMAIL} "
        "for the export"
    )


def test_an_address_in_a_title_is_redacted():
    card = board_snapshot.scrub_card(_raw_card("DRE-1", title="chase cy@vendor.example.org"))
    assert "vendor.example.org" not in card["title"]
    assert card["title"] == f"chase {board_snapshot.REDACTED_EMAIL}"


def test_an_address_in_a_comment_body_is_redacted():
    scrubbed = board_snapshot.scrub_comment(_comment("mail dee.person@example.net back"))
    assert scrubbed["body"] == f"mail {board_snapshot.REDACTED_EMAIL} back"


def test_an_address_is_redacted_before_the_cut_not_after():
    """Cutting first would leave the front half of an address sitting at the
    200-character boundary, and half an address is still the person."""
    body = "word " * 38 + "eli@partner.example.com " + "y" * 50  # address straddles 200
    assert body.index("eli@") < 200 < body.index("eli@") + len("eli@partner.example.com")
    scrubbed = board_snapshot.scrub_comment(_comment(body))
    assert "eli@" not in scrubbed["body"]
    assert "@partner" not in scrubbed["body"]
    # what lands on the boundary is the placeholder, cut — never the address
    assert scrubbed["body"].endswith("<redacted-")
    assert len(scrubbed["body"]) == 200


def test_redaction_leaves_the_at_signs_the_board_is_full_of_alone():
    """`name@ref` is how this board spells a plugin, a channel and a tag. None
    of them is a person, and eating them would change what the replay reads."""
    kept = (
        "claude plugin update dreadnought-standards@dreadnought",
        "the fleet rides bureau-pipeline@main, never a tag",
        "pin agent-bureau@v3 and @mention the operator",
        "🧭 routing-verdict: FLEET",
    )
    for text in kept:
        assert board_snapshot.redact_emails(text) == text


def test_redaction_keeps_none_as_none_and_empty_as_empty():
    """A card with no description is a different thing from one with an empty
    description, and the contract writes the difference down."""
    assert board_snapshot.redact_emails(None) is None
    assert board_snapshot.redact_emails("") == ""


def test_a_comment_user_keeps_only_its_id():
    """The id is the authorship credential and it is opaque; the display name
    and the email are neither, and are not written."""
    scrubbed = board_snapshot.scrub_comment(_comment(user={
        "id": "user-the-fleet-key",
        "name": "Someone Real",
        "displayName": "someone",
        "email": "someone@example.com",
    }))
    assert scrubbed["user"] == {"id": "user-the-fleet-key"}
    rendered = json.dumps(scrubbed)
    assert "Someone Real" not in rendered
    assert "someone@example.com" not in rendered


def test_an_unauthored_comment_keeps_its_null_user():
    assert board_snapshot.scrub_comment(_comment(user=None))["user"] is None


def test_a_comment_carries_exactly_body_created_at_and_user():
    scrubbed = board_snapshot.scrub_comment(
        {"body": "b", "createdAt": "t", "user": {"id": "u"}, "id": "comment-uuid",
         "url": "https://linear.app/…"}
    )
    assert set(scrubbed) == {"body", "createdAt", "user"}


def test_a_partial_comment_window_keeps_has_next_page():
    """`hasNextPage` on a newest-first window means OLDER comments lie beyond
    it — the sweep's own cache reads it to decide a window is not the thread."""
    card = board_snapshot.scrub_card(_raw_card("DRE-1", comments={
        "pageInfo": {"hasNextPage": True, "endCursor": "cursor-50"},
        "nodes": [_comment("newest"), _comment("older")],
    }))
    assert card["comments"]["pageInfo"] == {"hasNextPage": True,
                                            "endCursor": "cursor-50"}
    assert linear_ops.window_is_partial(card["comments"]) is True


def test_comments_are_stored_newest_first_exactly_as_linear_answers():
    card = board_snapshot.scrub_card(_raw_card("DRE-1", comments={
        "pageInfo": {"hasNextPage": False, "endCursor": "c"},
        "nodes": [_comment("newest"), _comment("middle"), _comment("oldest")],
    }))
    assert [n["body"] for n in card["comments"]["nodes"]] == [
        "newest", "middle", "oldest"]
    # …and the sweep's own reader turns that into oldest→newest, unchanged.
    assert [n["body"] for n in linear_ops.window_nodes(card["comments"])] == [
        "oldest", "middle", "newest"]


def test_a_card_carries_exactly_the_contract_keys():
    """A key the sweep never reads is not in the file — so a field Linear adds,
    or one a future query selects by accident, cannot ride along."""
    raw = _raw_card("DRE-1")
    raw.update({"url": "https://linear.app/…", "priority": 2,
                "branchName": "agent/DRE-1", "creator": {"name": "Someone Real"}})
    card = board_snapshot.scrub_card(raw)
    assert set(card) == CARD_KEYS
    assert "Someone Real" not in json.dumps(card)


def test_the_nested_shapes_are_the_ones_the_sweep_reads():
    raw = _raw_card(
        "DRE-1",
        state={"name": "Todo", "type": "unstarted", "id": "state-todo"},
        labels={"nodes": [{"name": "repo:atlas", "id": "label-uuid"}]},
        parent={"identifier": "DRE-9", "state": {"name": "In Progress"},
                "title": "[EPIC] the parent"},
        children={"nodes": [{"id": "kid-uuid", "identifier": "DRE-2",
                             "createdAt": "2026-09-02T00:00:00.000Z",
                             "state": {"name": "Done"}, "title": "a child"}]},
        relations={"pageInfo": {"hasNextPage": True, "endCursor": "r-end"},
                   "nodes": [{"type": "blocks", "issue": {"identifier": "DRE-1"},
                              "relatedIssue": {"identifier": "DRE-3",
                                               "state": {"name": "Todo"}}}]},
        inverseRelations={"nodes": [{"type": "blocks", "id": "rel-uuid",
                                     "issue": {"identifier": "DRE-4",
                                               "state": {"name": "Done"}}}]},
        history={"nodes": [{"createdAt": "2026-09-03T00:00:00.000Z",
                            "toState": {"name": "In Progress", "id": "s"},
                            "fromState": {"name": "Backlog"}}]},
    )
    card = board_snapshot.scrub_card(raw)
    assert card["state"] == {"name": "Todo"}
    assert card["labels"] == {"nodes": [{"name": "repo:atlas"}]}
    assert card["parent"] == {"identifier": "DRE-9", "state": {"name": "In Progress"}}
    assert card["children"] == {"nodes": [{
        "id": "kid-uuid", "identifier": "DRE-2",
        "createdAt": "2026-09-02T00:00:00.000Z", "state": {"name": "Done"}}]}
    # the merge-sweep gate reads `hasNextPage` to know it did not see them all
    assert card["relations"] == {"pageInfo": {"hasNextPage": True}, "nodes": [
        {"type": "blocks", "issue": {"identifier": "DRE-1"},
         "relatedIssue": {"identifier": "DRE-3"}}]}
    assert card["inverseRelations"] == {"nodes": [
        {"type": "blocks", "issue": {"identifier": "DRE-4",
                                     "state": {"name": "Done"}}}]}
    assert card["history"] == {"nodes": [
        {"createdAt": "2026-09-03T00:00:00.000Z",
         "toState": {"name": "In Progress"}}]}


def test_a_cardless_parent_and_a_stateless_history_entry_survive():
    """Linear answers `null` for a card with no parent, and for the `toState`
    of a history entry that changed something other than the lane."""
    card = board_snapshot.scrub_card(_raw_card(
        "DRE-1", parent=None,
        history={"nodes": [{"createdAt": "t", "toState": None}]},
    ))
    assert card["parent"] is None
    assert card["history"]["nodes"] == [{"createdAt": "t", "toState": None}]


def test_the_scrub_does_not_mutate_the_card_it_was_given():
    raw = _raw_card("DRE-1", comments={
        "pageInfo": {"hasNextPage": False, "endCursor": "c"},
        "nodes": [_comment("q" * 500, user={"id": "u", "email": "e@example.com"})],
    })
    before = json.dumps(raw)
    board_snapshot.scrub_card(raw)
    assert json.dumps(raw) == before


# ---------------------------------------------------------------------------
# the CLI
# ---------------------------------------------------------------------------
def test_take_writes_the_snapshot_to_the_path_it_was_given(tmp_path):
    fake = _board(5)
    out = tmp_path / "board.json"
    with patch.object(linear_ops, "gql", fake.gql):
        code = board_snapshot.main(["take", "--out", str(out)])
    assert code == 0
    written = json.loads(out.read_text())
    assert set(written) == TOP_LEVEL_KEYS
    assert len(written["cards"]) == 5
    assert all(set(card) == CARD_KEYS for card in written["cards"])


def test_a_board_that_cannot_be_read_is_not_written_as_an_empty_snapshot(tmp_path):
    """An unreadable board is not an empty board (DRE-2034)."""
    out = tmp_path / "board.json"

    def boom(query, variables=None):
        raise linear_ops.LinearError("linear error from https://api.linear.app: nope")

    with patch.object(linear_ops, "gql", boom):
        code = board_snapshot.main(["take", "--out", str(out)])
    assert code == 2
    assert not out.exists()


def test_take_refuses_to_write_inside_the_repository(tmp_path):
    """DRE-3918. This repository is PUBLIC. A real board — every card's text,
    customer names included — was committed to it once, and a commit here is
    permanent. So the one tool that reads the real board refuses an output path
    anywhere inside the repo, BEFORE it reads anything, and says why."""
    fake = _board(3)
    out = REPO_ROOT / "tests" / "fixtures" / "refused-board-snapshot.json"
    try:
        with patch.object(linear_ops, "gql", fake.gql):
            code = board_snapshot.main(["take", "--out", str(out)])
        assert code == 3
        assert not out.exists()
        assert fake.requests == 0, "the board must not be read at all"
    finally:
        out.unlink(missing_ok=True)


def test_take_refuses_a_relative_path_that_resolves_inside_the_repository(
        monkeypatch):
    fake = _board(3)
    monkeypatch.chdir(REPO_ROOT)
    out = REPO_ROOT / "refused-relative.json"
    try:
        with patch.object(linear_ops, "gql", fake.gql):
            code = board_snapshot.main(["take", "--out", "refused-relative.json"])
        assert code == 3
        assert not out.exists()
    finally:
        out.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# the synthetic board: the shape of a real one, none of its words (DRE-3918)
# ---------------------------------------------------------------------------
def _valid_ref(node, keys) -> bool:
    return isinstance(node, dict) and set(node) == set(keys)


@pytest.fixture(scope="module")
def snapshot() -> dict:
    return board_snapshot.synthetic()


def test_the_synthetic_board_is_deterministic():
    assert board_snapshot.synthetic() == board_snapshot.synthetic()
    assert board_snapshot.render(board_snapshot.synthetic()) == board_snapshot.render(
        board_snapshot.synthetic())


def test_the_synthetic_board_carries_no_real_words(snapshot):
    """Every free-text field is visibly synthetic: no title or description a
    real card could have written, and no address of any kind."""
    email_like = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    for card in snapshot["cards"]:
        assert card["title"].startswith("synthetic "), card["identifier"]
        assert (card["description"] or "synthetic ").startswith("synthetic "), \
            card["identifier"]
        texts = [card["title"], card["description"] or ""] + [
            n["body"] for n in card["comments"]["nodes"]]
        assert not any(email_like.search(t) for t in texts), card["identifier"]


def test_the_synthetic_board_renders_under_four_megabytes(snapshot):
    assert len(board_snapshot.render(snapshot).encode("utf-8")) < 4 * 1024 * 1024


def test_render_writes_one_card_per_line():
    """A reviewer still reads a card out of the file and a diff still shows
    which card changed, but the file is as many lines as the board has cards.
    Indented, the 2026-09-13 snapshot was 71,423 lines: past the critic's
    `pr_size_strategy.OVERSIZED_LINES`, so the pull request that committed it
    could not be reviewed at all."""
    snap = {
        "taken_at": "2026-09-13T18:43:55Z", "team": "DRE", "lanes": ["Todo"],
        "cards": [
            {"id": "a", "identifier": "DRE-1", "title": "café"},
            {"id": "b", "identifier": "DRE-2", "title": "two\nlines"},
        ],
    }
    text = board_snapshot.render(snap)
    assert json.loads(text) == snap
    assert text.endswith("\n")
    lines = text.splitlines()
    for card in snap["cards"]:
        own = json.dumps(card, ensure_ascii=False)
        on_own_line = [ln.strip().rstrip(",") for ln in lines]
        assert on_own_line.count(own) == 1, card
    assert len(lines) <= len(snap["cards"]) + 10
    assert "café" in text  # written as the character, not an escape


def test_the_synthetic_board_round_trips_through_render(snapshot):
    """What render writes reads back as the same board, and stays inside the
    size a critic can review."""
    import pr_size_strategy

    text = board_snapshot.render(snapshot)
    assert json.loads(text) == snapshot
    assert len(text.splitlines()) < pr_size_strategy.OVERSIZED_LINES


def test_the_fixture_says_when_it_was_taken_and_of_what(snapshot):
    assert set(snapshot) == TOP_LEVEL_KEYS
    assert snapshot["team"] == "DRE"
    assert tuple(snapshot["lanes"]) == board_snapshot.LANES
    taken = datetime.fromisoformat(snapshot["taken_at"].replace("Z", "+00:00"))
    assert taken.tzinfo is not None
    assert taken.utcoffset().total_seconds() == 0


def test_the_synthetic_board_has_a_real_boards_shapes(snapshot):
    """The shapes a board carries that nobody hand-builds five of: epics with
    children, relations both ways, exhausted comment and relation windows,
    history, every lane the sweep reads."""
    cards = snapshot["cards"]
    assert len(cards) > 100
    assert {c["state"]["name"] for c in cards} == set(snapshot["lanes"])
    assert any(len(c["children"]["nodes"]) >= 10 for c in cards)
    assert any(c["comments"]["nodes"] for c in cards)
    assert any(c["comments"]["pageInfo"]["hasNextPage"] for c in cards)
    assert any(c["relations"]["nodes"] for c in cards)
    assert any(c["relations"]["pageInfo"]["hasNextPage"] for c in cards)
    assert any(c["inverseRelations"]["nodes"] for c in cards)
    assert any(c["history"]["nodes"] for c in cards)
    assert any(c["parent"] for c in cards)
    assert any(c["description"] is None for c in cards)


def test_every_card_in_the_fixture_validates_against_the_contract(snapshot):
    for card in snapshot["cards"]:
        where = card.get("identifier")
        assert set(card) == CARD_KEYS, where
        assert isinstance(card["id"], str) and card["id"], where
        assert isinstance(card["identifier"], str), where
        assert isinstance(card["title"], str), where
        assert card["description"] is None or isinstance(card["description"], str)
        assert isinstance(card["createdAt"], str), where
        assert isinstance(card["updatedAt"], str), where
        assert _valid_ref(card["state"], {"name"}), where
        assert set(card["labels"]) == {"nodes"}, where
        assert all(_valid_ref(n, {"name"}) for n in card["labels"]["nodes"]), where

        parent = card["parent"]
        assert parent is None or (
            _valid_ref(parent, {"identifier", "state"})
            and _valid_ref(parent["state"], {"name"})
        ), where

        assert set(card["children"]) == {"nodes"}, where
        for kid in card["children"]["nodes"]:
            assert _valid_ref(kid, {"id", "identifier", "createdAt", "state"}), where
            assert _valid_ref(kid["state"], {"name"}), where

        comments = card["comments"]
        assert set(comments) == {"pageInfo", "nodes"}, where
        assert _valid_ref(comments["pageInfo"], {"hasNextPage", "endCursor"}), where
        assert isinstance(comments["pageInfo"]["hasNextPage"], bool), where
        for node in comments["nodes"]:
            assert _valid_ref(node, {"body", "createdAt", "user"}), where
            assert len(node["body"]) <= 200, where
            assert node["user"] is None or _valid_ref(node["user"], {"id"}), where

        relations = card["relations"]
        assert set(relations) == {"pageInfo", "nodes"}, where
        assert _valid_ref(relations["pageInfo"], {"hasNextPage"}), where
        for rel in relations["nodes"]:
            assert _valid_ref(rel, {"type", "issue", "relatedIssue"}), where
            assert rel["issue"] is None or _valid_ref(rel["issue"], {"identifier"})
            assert rel["relatedIssue"] is None or _valid_ref(
                rel["relatedIssue"], {"identifier"})

        assert set(card["inverseRelations"]) == {"nodes"}, where
        for rel in card["inverseRelations"]["nodes"]:
            assert _valid_ref(rel, {"type", "issue"}), where
            assert rel["issue"] is None or (
                _valid_ref(rel["issue"], {"identifier", "state"})
                and _valid_ref(rel["issue"]["state"], {"name"})
            ), where

        assert set(card["history"]) == {"nodes"}, where
        for node in card["history"]["nodes"]:
            assert _valid_ref(node, {"createdAt", "toState"}), where
            assert node["toState"] is None or _valid_ref(node["toState"], {"name"})


def test_the_fixture_gives_a_comment_author_no_field_but_its_id(snapshot):
    """Structural: the only thing written about a comment's author is the
    opaque id, so there is no FIELD a name or an address could be in.

    Named for what it checks, and no more. It used to be called "carries no
    display name and no email", which is what a reader would ask of it — and
    it passed green over a fixture holding eighteen real addresses, because an
    address in a description is prose, not a field. The string search that
    actually answers that question is the test below."""
    for card in snapshot["cards"]:
        for node in card["comments"]["nodes"]:
            assert node["user"] is None or list(node["user"]) == ["id"]


def test_the_fixture_carries_no_email_address_in_any_free_text(snapshot):
    """The one that has to be a string search. This repository is PUBLIC and a
    committed snapshot is permanent: an address the board's prose quotes is a
    real customer's, and the structural test above cannot see it.

    Scanned independently of `EMAIL_RE` — a bug in the writer's own pattern
    would otherwise make both the redaction and its proof blind in the same
    place."""
    email_like = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    found = []
    for card in snapshot["cards"]:
        for field in ("title", "description"):
            found += [(card["identifier"], field, hit)
                      for hit in email_like.findall(card.get(field) or "")]
        for node in card["comments"]["nodes"]:
            found += [(card["identifier"], "comment", hit)
                      for hit in email_like.findall(node.get("body") or "")]
    assert not found, f"email addresses in the synthetic board: {found[:10]}"
