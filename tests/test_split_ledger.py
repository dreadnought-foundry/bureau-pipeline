"""The split ledger, derived (DRE-3077).

`scripts/split_ledger.py derive` reads every turn-cap death, split and hand-back
out of Linear and the run receipts and writes `config/split-ledger.json`, with
`docs/split-ledger.md` rendered from it. This is piece 1 of 3 of DRE-3022 and
owes nothing to the other two: nothing here injects the ledger into the planner
and nothing scores against it.

What these tests pin, and why each one exists:

  * **The tells are a pure function over a body**, with a fixture per tell. Four
    tells, four fixtures that fire exactly one each, plus a body that fires
    none — a check that answers "yes" to everything measures nothing.
  * **The receipts are the pipeline's own strings.** The turn-cap markers this
    module matches are built by `dead_run.decide` inside the test, so a reword
    of the receipt fails HERE rather than turning every row into "nothing ever
    happened" — the silent zero the ledger exists to refuse.
  * **A read that fails is UNKNOWN, never 0.** One test per field: a PR the
    token cannot read, a successor search that raised, a death whose receipt
    carries no cost figure, a card with no size or role label. Each asserts the
    literal `UNKNOWN` and asserts the field is NOT `0`/`[]`.
  * **The committed artifacts.** `config/split-ledger.json` carries at least the
    ten seed rows the card names, and `docs/split-ledger.md` IS the render of
    that file — the same discipline `docs/routing-verdicts.md` is held to.

DRE-3356 adds three things and these tests pin each of them:

  * **The population is DISCOVERED, not named.** `discover()` asks Linear for
    the cards whose own comments carry a turn-cap or hand-back receipt and for
    the cards a successor cites as the one it was cut from, inside a
    `--window-days` window — and the seeds stay in whatever the window says. A
    search that failed is NAMED, never quietly dropped: a discovery that loses
    a search reports a smaller history in exactly the same shape.
  * **Every row is dated.** `created_at` is the card's Linear `createdAt` as
    ISO-8601 UTC, or the literal `UNKNOWN` — never the empty string, which is
    what a reader sorting "the last N splits" would silently sort first.
  * **`monthly` counts the splits by month**, with `complete` saying whether
    the record covers the whole month, and `planner_children` saying `UNKNOWN`
    rather than `0` when the read failed.

And one test guards the contract DRE-3022's other children read: every field
`config/split-ledger.json` carried before this card still has its name and its
type.

Run: cd bureau-pipeline && python3 -m pytest tests/test_split_ledger.py -v
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import dead_run  # noqa: E402
import split_ledger  # noqa: E402

LEDGER = ROOT / "config" / "split-ledger.json"
DOC = ROOT / "docs" / "split-ledger.md"
CONFIG_README = ROOT / "config" / "README.md"

# The ten cards the card names as seed rows. Written out rather than read from
# the module: a constant that checks itself checks nothing.
SEEDS = (
    "DRE-3029", "DRE-3016", "DRE-3022", "DRE-2719", "DRE-2838",
    "DRE-2847", "DRE-2871", "DRE-2937", "DRE-2891", "DRE-2676",
)


# --------------------------------------------------------------------------- #
# the tells — one fixture per tell                                             #
# --------------------------------------------------------------------------- #

# A card body that trips exactly one tell each. Every one is written in the
# shape a real card is written in, because the function reads a card body.

CONTRACT_BODY = """**The planner sizes against the ledger.**

The ledger is written by one deliverable and read by the next — there is a
contract between them, and the second cannot be built until the first exists.

## Acceptance criteria

- [ ] the ledger is written
- [ ] the planner reads it
"""

TIERS_BODY = """**One backend defect and one console surface.**

## File footprint

`scripts/reconcile.py`, `console/src/pages/Board.tsx`

## Acceptance criteria

- [ ] the sweep stops guessing
- [ ] the board stops rendering the guess
"""

UNENUMERATED_BODY = """**The nine derivations move behind one reader.**

## Acceptance criteria

- [ ] all nine derivations are moved
"""

UNBOUNDED_BODY = """**Every surface rendering a work state carries its read age.**

## Acceptance criteria

- [ ] every surface says how old its read is
"""

CLEAN_BODY = """**One script learns to say UNKNOWN.**

Two call sites, both named below, both in `scripts/reconcile.py`.

## File footprint

`scripts/reconcile.py`, `tests/test_reconcile.py`

## Acceptance criteria

- [ ] `_advanced_recently` returns UNKNOWN for a missing timestamp
- [ ] `is_stale` returns UNKNOWN for a missing timestamp
"""


def test_the_four_tells_are_the_four_the_standard_names():
    assert split_ledger.TELLS == (
        split_ledger.TELL_CONTRACT,
        split_ledger.TELL_TIERS,
        split_ledger.TELL_UNENUMERATED,
        split_ledger.TELL_UNBOUNDED,
    )


def test_contract_between_pieces_fires_alone():
    assert split_ledger.tells(CONTRACT_BODY) == [split_ledger.TELL_CONTRACT]


def test_two_languages_or_tiers_fires_alone():
    assert split_ledger.tells(TIERS_BODY) == [split_ledger.TELL_TIERS]


def test_an_unenumerated_count_fires_alone():
    assert split_ledger.tells(UNENUMERATED_BODY) == [split_ledger.TELL_UNENUMERATED]


def test_an_unbounded_quantifier_fires_alone():
    assert split_ledger.tells(UNBOUNDED_BODY) == [split_ledger.TELL_UNBOUNDED]


def test_a_one_pr_card_trips_nothing():
    """The half that makes the other four mean something: a check that fires on
    every body is a label, not a measurement."""
    assert split_ledger.tells(CLEAN_BODY) == []


def test_a_count_the_body_enumerates_is_not_the_unenumerated_tell():
    """DRE-2871 named its eight sites with a file each — countable, and the
    tell that applied to it was never this one."""
    body = (
        "**Eight console surfaces stop asserting what they never read.**\n\n"
        "## Acceptance criteria\n\n"
        + "".join(
            f"- [ ] `console/src/site{n}.py` says UNKNOWN\n" for n in range(1, 9)
        )
    )
    assert split_ledger.TELL_UNENUMERATED not in split_ledger.tells(body)


def test_a_quantifier_the_body_bounds_is_not_the_unbounded_tell():
    body = (
        "**All five call sites take the same guard.**\n\n"
        "## Acceptance criteria\n\n- [ ] all five call sites are guarded\n"
    )
    assert split_ledger.TELL_UNBOUNDED not in split_ledger.tells(body)


def test_tells_is_pure_and_takes_no_argument_but_the_body():
    """A deterministic check over a card body — no Linear, no GitHub, no clock.
    Called twice on the same text it answers the same thing."""
    assert split_ledger.tells(CONTRACT_BODY) == split_ledger.tells(CONTRACT_BODY)
    assert split_ledger.tells("") == []


# --------------------------------------------------------------------------- #
# the receipts — built by the writer, never spelled twice                      #
# --------------------------------------------------------------------------- #


def _requeue_receipt(cost: str = "$20.10") -> str:
    """The turn-cap requeue receipt, from `dead_run` itself."""
    decision = dead_run.decide(
        0, turn_exhaustion=True,
        turn_facts=f"the 150-turn cap after 151 turns and {cost}",
    )
    assert decision.action == "requeue"
    return decision.comments[0]


def _hold_receipt(cost: str = "$19.12") -> str:
    decision = dead_run.decide(
        dead_run.TURN_REQUEUE_CAP, turn_exhaustion=True,
        turn_facts=f"the 150-turn cap after 151 turns and {cost}",
    )
    assert decision.action == "hold"
    return decision.comments[0]


def test_both_turn_cap_receipts_count_as_deaths():
    deaths = split_ledger.turn_cap_deaths([_requeue_receipt(), _hold_receipt()])
    assert len(deaths) == 2
    assert [d["dollars"] for d in deaths] == [20.10, 19.12]


def test_an_ordinary_dead_run_receipt_is_not_a_turn_cap_death():
    """A model death spends a different budget and says nothing about size."""
    ordinary = dead_run.decide(0).comments[0]
    assert dead_run.DEAD_TAG in ordinary
    assert split_ledger.turn_cap_deaths([ordinary]) == []


def test_an_error_max_turns_run_record_counts_as_a_death():
    """The third turn-cap signal the card names: the run's own record."""
    deaths = split_ledger.turn_cap_deaths(
        [], executions=[{"is_error": True, "subtype": "error_max_turns",
                         "num_turns": 151, "total_cost_usd": 18.40}]
    )
    assert len(deaths) == 1
    assert deaths[0]["dollars"] == 18.40


def test_dollars_are_the_sum_of_the_dead_runs():
    deaths = split_ledger.turn_cap_deaths([_requeue_receipt(), _hold_receipt()])
    assert split_ledger.dollars_spent(deaths) == pytest.approx(39.22)


def test_the_handback_receipt_is_the_string_agent_task_posts():
    """Read out of the workflow that writes it, so a reword fails here."""
    workflow = (ROOT / ".github" / "workflows" / "agent-task.yml").read_text(
        encoding="utf-8")
    assert split_ledger.HANDBACK_RECEIPT_PREFIX in workflow


def test_a_handback_receipt_is_read_off_the_card():
    body = split_ledger.HANDBACK_RECEIPT_PREFIX + " six independently shippable pieces"
    assert split_ledger.handed_back([body]) is True
    assert split_ledger.handed_back(["a comment quoting " + body]) is False


# --------------------------------------------------------------------------- #
# who is in the ledger                                                         #
# --------------------------------------------------------------------------- #


def test_a_successor_citing_the_card_is_a_split():
    assert split_ledger.cites(
        "**Split from** [DRE-3022](https://linear.app/x) by its author", "DRE-3022")
    assert split_ledger.cites("piece 1 of 3 of DRE-3022", "DRE-3022")
    # The spelling the board actually uses — DRE-2910/2911/2912 all open this way.
    assert split_ledger.cites(
        "**One of three cards splitting DRE-2871, which SIX runs could not "
        "finish.**", "DRE-2871")
    # And the two-way split — DRE-2952 and DRE-2953 both open this way.
    assert split_ledger.cites(
        "**Backend half of** [DRE-2937](https://linear.app/x)", "DRE-2937")
    assert not split_ledger.cites("see DRE-3022 for background", "DRE-3022")
    assert not split_ledger.cites(
        "[DRE-2937](https://linear.app/x) died at the turn cap twice", "DRE-2937")
    assert not split_ledger.cites("piece 1 of 3 of DRE-3022", "DRE-2719")


def test_a_citation_below_the_opening_paragraph_is_a_mention_not_a_piece():
    """DRE-2719 is named in the body of 39 cards and was cut into six. The
    anchor is the entire difference between those two numbers."""
    body = (
        "**A card about something else entirely.**\n\n"
        "Background: this was split from DRE-2719's design work last month.\n")
    assert not split_ledger.cites(body, "DRE-2719")


def test_the_three_reasons_a_card_is_in_the_ledger():
    died = {"identifier": "DRE-1", "comments": [_hold_receipt()]}
    assert split_ledger.reasons(died) == [split_ledger.REASON_TURN_CAP]

    split = {"identifier": "DRE-2", "state_type": "canceled",
             "successors": [{"identifier": "DRE-3"}]}
    assert split_ledger.reasons(split) == [split_ledger.REASON_SPLIT]

    back = {"identifier": "DRE-4",
            "comments": [split_ledger.HANDBACK_RECEIPT_PREFIX + " an epic"]}
    assert split_ledger.reasons(back) == [split_ledger.REASON_HANDBACK]


def test_a_card_that_is_none_of_the_three_has_no_reason():
    assert split_ledger.reasons({"identifier": "DRE-9", "comments": []}) == []


def test_a_seed_row_history_does_not_answer_says_so_rather_than_nothing():
    """A row with no reason at all reads as a bug in the reader. The seeds are
    in the ledger because the card named them, and the row says that."""
    seed = split_ledger.row({"identifier": split_ledger.SEED_CARDS[0],
                             "comments": [], "successors": []})
    assert seed["reasons"] == [split_ledger.REASON_SEED]


def test_successors_of_a_live_card_are_not_a_split():
    """The card names Canceled or Backlog: a card still in flight whose siblings
    cite it has not been split, it has been referenced."""
    live = {"identifier": "DRE-5", "state_type": "started",
            "successors": [{"identifier": "DRE-6"}]}
    assert split_ledger.REASON_SPLIT not in split_ledger.reasons(live)


# --------------------------------------------------------------------------- #
# a row                                                                        #
# --------------------------------------------------------------------------- #


def _record(**over) -> dict:
    record = {
        "identifier": "DRE-3022",
        "title": "The planner sizes against the ledger",
        "url": "https://linear.app/dreadnoughtfoundry/issue/DRE-3022",
        "body": (
            "**The planner sizes against the ledger.**\n\n"
            "## File footprint\n\n"
            "`scripts/planner_score.py`, `.github/workflows/plan.yml`\n\n"
            "## Acceptance criteria\n\n- [ ] it does\n"
        ),
        "labels": ["repo:bureau-pipeline", "agent:engineer", "size:M"],
        "state": "Backlog",
        "state_type": "backlog",
        "comments": [_requeue_receipt(), _hold_receipt()],
        "successors": [
            {"identifier": "DRE-3077",
             "pr": {"number": 251, "merged": True,
                    "files": ["scripts/split_ledger.py"]},
             "pr_unreadable": None},
        ],
        "successors_unreadable": None,
    }
    record.update(over)
    return record


def test_a_row_carries_every_field_the_card_asks_for():
    row = split_ledger.row(_record())
    assert row["card"] == "DRE-3022"
    assert row["size"] == "M"
    assert row["role"] == "engineer"
    assert row["declared_files"] == ["scripts/planner_score.py",
                                     ".github/workflows/plan.yml"]
    assert row["piece_files"] == ["scripts/split_ledger.py"]
    assert row["pieces"] == 1
    assert row["deaths"] == 2
    assert row["dollars"] == pytest.approx(39.22)
    assert row["tells"] == [split_ledger.TELL_TIERS]
    assert split_ledger.REASON_TURN_CAP in row["reasons"]


# --------------------------------------------------------------------------- #
# UNKNOWN, never 0                                                             #
# --------------------------------------------------------------------------- #


def test_a_pr_the_token_cannot_read_is_unknown_not_an_empty_footprint():
    row = split_ledger.row(_record(successors=[
        {"identifier": "DRE-3077", "pr": None,
         "pr_unreadable": "this token cannot read dreadnought-foundry/agent-bureau"},
    ]))
    assert row["piece_files"] == split_ledger.UNKNOWN
    assert row["piece_files"] != []
    # The count of pieces WAS readable — one unknown field does not poison the row.
    assert row["pieces"] == 1
    assert any("cannot read" in note for note in row["unreadable"])


def test_a_successor_search_that_failed_is_unknown_not_zero_pieces():
    row = split_ledger.row(_record(
        successors=None,
        successors_unreadable="Linear refused the search: RATELIMITED",
    ))
    assert row["pieces"] == split_ledger.UNKNOWN
    assert row["pieces"] != 0
    assert row["piece_files"] == split_ledger.UNKNOWN


def test_a_death_with_no_cost_figure_makes_the_dollars_unknown():
    """`turn_exhaustion_facts` degrades to "the turn cap" when the record
    carried no numbers. A partial sum printed as a total is the silent zero."""
    row = split_ledger.row(_record(comments=[
        dead_run.decide(0, turn_exhaustion=True).comments[0],
        _hold_receipt(),
    ]))
    assert row["deaths"] == 2
    assert row["dollars"] == split_ledger.UNKNOWN
    assert row["dollars"] != 0


def test_comments_that_could_not_be_read_make_the_deaths_unknown():
    row = split_ledger.row(_record(
        comments=None, comments_unreadable="Linear refused the read"))
    assert row["deaths"] == split_ledger.UNKNOWN
    assert row["deaths"] != 0
    assert row["dollars"] == split_ledger.UNKNOWN


def test_a_card_with_no_size_or_role_label_says_unknown():
    row = split_ledger.row(_record(labels=["repo:bureau-pipeline"]))
    assert row["size"] == split_ledger.UNKNOWN
    assert row["role"] == split_ledger.UNKNOWN


def test_a_card_declaring_no_files_line_says_unknown_not_no_files():
    row = split_ledger.row(_record(body="**A card with no footprint line.**\n"))
    assert row["declared_files"] == split_ledger.UNKNOWN
    assert row["declared_files"] != []
    assert row["declared_file_count"] == split_ledger.UNKNOWN


# --------------------------------------------------------------------------- #
# the ledger and its rates                                                     #
# --------------------------------------------------------------------------- #


def test_the_ledger_carries_its_generation_timestamp():
    doc = split_ledger.ledger([_record()], generated_at="2026-09-04T00:00:00Z")
    assert doc["generated_at"] == "2026-09-04T00:00:00Z"
    assert len(doc["rows"]) == 1


def test_the_rates_summarise_deaths_by_declared_footprint():
    wide = _record(identifier="DRE-A", body=(
        "**A wide card.**\n\n## File footprint\n\n"
        "`a.py`, `b.py`, `c.py`, `d.py`\n"))
    narrow = _record(identifier="DRE-B", comments=[], body=(
        "**A narrow card.**\n\n## File footprint\n\n`a.py`\n"))
    doc = split_ledger.ledger([wide, narrow], generated_at="2026-09-04T00:00:00Z")
    band = {b["more_than"]: b for b in doc["rates"]["by_declared_files"]}
    assert band[1]["of"] == 1
    assert band[1]["died"] == 1
    assert "more than 1 file" in band[1]["sentence"]


def test_a_row_with_an_unreadable_footprint_is_counted_apart_never_as_zero():
    row = _record(body="**No footprint line here.**\n")
    doc = split_ledger.ledger([row], generated_at="2026-09-04T00:00:00Z")
    assert doc["rates"]["unreadable_footprint"] == 1
    for band in doc["rates"]["by_declared_files"]:
        assert band["of"] == 0 or "DRE-3022" not in band.get("cards", [])


def test_the_rates_summarise_deaths_by_tell():
    doc = split_ledger.ledger([_record()], generated_at="2026-09-04T00:00:00Z")
    by_tell = {b["tell"]: b for b in doc["rates"]["by_tell"]}
    assert set(by_tell) == set(split_ledger.TELLS)
    assert by_tell[split_ledger.TELL_TIERS]["of"] == 1
    assert by_tell[split_ledger.TELL_TIERS]["died"] == 1


# --------------------------------------------------------------------------- #
# the render                                                                   #
# --------------------------------------------------------------------------- #


def test_the_render_shows_every_row_and_its_unknowns():
    doc = split_ledger.ledger(
        [_record(successors=None, successors_unreadable="refused")],
        generated_at="2026-09-04T00:00:00Z")
    text = split_ledger.render_markdown(doc)
    assert "DRE-3022" in text
    assert "2026-09-04T00:00:00Z" in text
    assert split_ledger.UNKNOWN in text


def test_the_render_prints_the_rate_sentences():
    doc = split_ledger.ledger([_record()], generated_at="2026-09-04T00:00:00Z")
    text = split_ledger.render_markdown(doc)
    for band in doc["rates"]["by_declared_files"]:
        assert band["sentence"] in text


# --------------------------------------------------------------------------- #
# the committed artifacts                                                      #
# --------------------------------------------------------------------------- #


def test_the_seed_cards_are_the_ten_the_card_names():
    assert set(SEEDS) <= set(split_ledger.SEED_CARDS)


def test_the_committed_ledger_carries_at_least_the_ten_seed_rows():
    doc = json.loads(LEDGER.read_text(encoding="utf-8"))
    rows = {row["card"]: row for row in doc["rows"]}
    missing = [card for card in SEEDS if card not in rows]
    assert not missing, f"config/split-ledger.json is missing {missing}"
    assert doc["generated_at"]


def test_every_committed_row_carries_every_field():
    doc = json.loads(LEDGER.read_text(encoding="utf-8"))
    for row in doc["rows"]:
        for field in ("card", "size", "role", "declared_files", "piece_files",
                      "pieces", "deaths", "dollars", "tells", "reasons"):
            assert field in row, f"{row.get('card')} has no {field}"


def test_the_committed_document_is_the_render_of_the_committed_ledger():
    doc = json.loads(LEDGER.read_text(encoding="utf-8"))
    assert DOC.read_text(encoding="utf-8") == split_ledger.render_markdown(doc), (
        "docs/split-ledger.md is stale — regenerate it with "
        "`python3 scripts/split_ledger.py derive`"
    )


def test_the_document_says_how_it_is_generated():
    head = DOC.read_text(encoding="utf-8").splitlines()[:10]
    assert any("split_ledger.py derive" in line for line in head)


# --------------------------------------------------------------------------- #
# the live seam                                                                #
# --------------------------------------------------------------------------- #


class _FakeLops:
    """Just enough Linear for `collect`, and a record of what it was asked."""

    def __init__(self, *, search_raises: bool = False):
        self.search_raises = search_raises
        self.asked: list[str] = []

    def gql(self, query: str, variables: dict) -> dict:
        if "containsIgnoreCase" in query:
            if self.search_raises:
                raise RuntimeError("Linear refused the search")
            return {"issues": {"nodes": [{
                "identifier": "DRE-3077",
                "title": "The split ledger, derived",
                "description": "**Split from** DRE-3022 by its author",
                "state": {"name": "In Progress", "type": "started"},
                "labels": {"nodes": [{"name": "repo:bureau-pipeline"}]},
            }]}}
        self.asked.append(variables["id"])
        return {"issue": {
            "identifier": variables["id"],
            "title": "The planner sizes against the ledger",
            "url": f"https://linear.app/x/issue/{variables['id']}",
            "createdAt": "2026-08-12T09:33:21.482Z",
            "description": "**A card.**\n\n**Files:** `scripts/a.py`\n",
            "state": {"name": "Backlog", "type": "backlog"},
            "labels": {"nodes": [{"name": "agent:engineer"}, {"name": "size:M"}]},
        }}

    def comment_bodies(self, identifier: str) -> list:
        return [_hold_receipt()]


def test_collect_reads_a_card_its_receipts_and_its_successors():
    lops = _FakeLops()
    out = split_ledger.collect(
        ["DRE-3022"], lops=lops,
        finder=lambda *a, **k: {"number": 251, "state": "MERGED",
                                "files": [{"path": "scripts/split_ledger.py"}]},
        readable=lambda repo, run=None: True)
    card = out["cards"][0]
    assert card["identifier"] == "DRE-3022"
    assert card["size"] == "M" or "size:M" in card["labels"]
    assert [s["identifier"] for s in card["successors"]] == ["DRE-3077"]
    assert card["successors"][0]["pr"]["files"] == ["scripts/split_ledger.py"]


def test_collect_records_why_a_successor_search_could_not_be_read():
    lops = _FakeLops(search_raises=True)
    out = split_ledger.collect(["DRE-3022"], lops=lops,
                               finder=lambda *a, **k: None,
                               readable=lambda repo, run=None: True)
    card = out["cards"][0]
    assert card["successors"] is None
    assert "refused" in card["successors_unreadable"]
    assert split_ledger.row(card)["pieces"] == split_ledger.UNKNOWN


def test_collect_never_believes_a_pr_search_in_a_repo_it_cannot_see():
    """`gh pr list` exits 0 and prints `[]` for an invisible repo — the same
    trap `planner_score.repo_is_readable` was written for."""
    lops = _FakeLops()
    out = split_ledger.collect(["DRE-3022"], lops=lops,
                               finder=lambda *a, **k: None,
                               readable=lambda repo, run=None: False)
    successor = out["cards"][0]["successors"][0]
    assert successor["pr"] is None
    assert "cannot read" in successor["pr_unreadable"]


# --------------------------------------------------------------------------- #
# DRE-3356 — the population discovers itself                                   #
# --------------------------------------------------------------------------- #

# The four successor openings `test_a_successor_citing_the_card_is_a_split`
# already pins as real, with the origin each one names. They are reused here
# because the search and the reader must agree: a needle that finds nothing
# `cites()` accepts is a spent Linear call, and a spelling `cites()` accepts
# that no needle finds is a card the ledger never hears about.
REAL_CITATIONS = (
    ("**Split from** [DRE-3022](https://linear.app/x) by its author", "DRE-3022"),
    ("**Piece 1 of 3 of DRE-3022** — the ledger itself.", "DRE-3022"),
    ("**One of three cards splitting DRE-2871, which SIX runs could not "
     "finish.**", "DRE-2871"),
    ("**Backend half of** [DRE-2937](https://linear.app/x)", "DRE-2937"),
)


class _DiscoveryLops:
    """Linear as `discover` asks it: comment searches, description searches and
    the planner-children count, each answered from a canned page.

    Every filter it is handed is recorded, so a test can assert the window is
    on the query rather than trusting that it was.
    """

    def __init__(self, *, comment_hits=None, citation_hits=None,
                 children=None, raise_on=None):
        self.comment_hits = comment_hits or {}
        self.citation_hits = citation_hits or {}
        self.children = children or {}
        self.raise_on = raise_on or ()
        self.filters: list = []
        self.calls = 0

    def gql(self, query: str, variables: dict) -> dict:
        self.calls += 1
        filter_ = (variables or {}).get("filter") or {}
        self.filters.append(filter_)
        if any(needle in json.dumps(filter_) for needle in self.raise_on):
            raise RuntimeError("Linear refused the search: RATELIMITED")
        if "parent" in filter_:
            since = (filter_.get("createdAt") or {}).get("gte") or ""
            nodes = [{"identifier": f"DRE-{n}"}
                     for n in range(self.children.get(since[:7], 0))]
        elif "comments" in filter_:
            needle = filter_["comments"]["body"]["containsIgnoreCase"]
            nodes = [{"identifier": card}
                     for card in self.comment_hits.get(needle, ())]
        else:
            needle = filter_["description"]["containsIgnoreCase"]
            nodes = [{"identifier": ident, "description": body}
                     for ident, body in self.citation_hits.get(needle, ())]
        return {"issues": {"nodes": nodes,
                           "pageInfo": {"hasNextPage": False, "endCursor": None}}}


def test_every_real_citation_is_found_by_a_needle_and_accepted_by_cites():
    """The search and the reader, pinned to each other. `containsIgnoreCase`
    takes a literal, so the needle is the fixed part of the phrase and `cites()`
    is what decides afterwards — but a phrase no needle reaches is a card the
    discovery cannot see at all."""
    for body, origin in REAL_CITATIONS:
        assert split_ledger.cites(body, origin), body
        assert any(needle.lower() in body.lower()
                   for needle in split_ledger.CITATION_NEEDLES), body


def test_the_cited_origin_is_the_card_the_successor_names():
    for body, origin in REAL_CITATIONS:
        assert split_ledger.cited_origins(body) == [origin]


def test_a_mention_below_the_opening_paragraph_names_no_origin():
    body = ("**A card about something else entirely.**\n\n"
            "Background: this was split from DRE-2719's design work.\n")
    assert split_ledger.cited_origins(body) == []


def test_discovery_finds_turn_cap_handback_and_cited_split_origins():
    lops = _DiscoveryLops(
        comment_hits={
            split_ledger.TURN_TAG: ["DRE-4001"],
            split_ledger.TURN_HOLD_MARK: ["DRE-4001", "DRE-4002"],
            split_ledger.HANDBACK_RECEIPT_PREFIX: ["DRE-4003"],
        },
        citation_hits={"split from": [
            ("DRE-4005", "**Split from** [DRE-4004](https://linear.app/x)"),
        ]},
    )
    found = split_ledger.discover(lops=lops, now="2026-09-10T00:00:00Z")
    assert set(found["cards"]) >= {"DRE-4001", "DRE-4002", "DRE-4003",
                                   "DRE-4004"}
    # The successor itself is not the origin — the card it NAMES is.
    assert "DRE-4005" not in found["cards"]
    assert split_ledger.REASON_TURN_CAP in found["found"]["DRE-4001"]
    assert split_ledger.REASON_HANDBACK in found["found"]["DRE-4003"]
    assert split_ledger.REASON_SPLIT in found["found"]["DRE-4004"]


def test_discovery_keeps_every_seed_whatever_the_window_says():
    """The seeds are in the ledger because DRE-3077 named them, and a 1-day
    window does not un-name them."""
    found = split_ledger.discover(lops=_DiscoveryLops(), window_days=1,
                                  now="2026-09-10T00:00:00Z")
    assert set(split_ledger.SEED_CARDS) <= set(found["cards"])


def test_every_discovery_search_is_bounded_by_the_window():
    lops = _DiscoveryLops()
    split_ledger.discover(lops=lops, window_days=90, now="2026-09-10T00:00:00Z")
    assert lops.filters, "discovery asked Linear nothing"
    for filter_ in lops.filters:
        assert filter_["createdAt"]["gte"] == "2026-06-12T00:00:00Z", filter_


def test_a_discovery_search_that_failed_is_named_not_silently_dropped():
    """A lost search reports a smaller history in exactly the same shape —
    the silent zero this whole module is written against."""
    lops = _DiscoveryLops(
        comment_hits={split_ledger.HANDBACK_RECEIPT_PREFIX: ["DRE-4003"]},
        raise_on=[split_ledger.TURN_TAG])
    found = split_ledger.discover(lops=lops, now="2026-09-10T00:00:00Z")
    assert found["unreadable"], "a refused search left no trace"
    assert any(split_ledger.TURN_TAG in note for note in found["unreadable"])
    # The searches that DID answer still count — one unknown does not poison
    # the population.
    assert "DRE-4003" in found["cards"]


def test_discovery_follows_every_page_it_is_offered():
    """Linear serves at most 100 nodes per page and says so only in `pageInfo`
    (DRE-2681). A discovery that reads page one is a population decided by
    Linear's default ordering."""
    pages = [
        {"issues": {"nodes": [{"identifier": "DRE-5001"}],
                    "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}},
        {"issues": {"nodes": [{"identifier": "DRE-5002"}],
                    "pageInfo": {"hasNextPage": False, "endCursor": None}}},
    ]

    class _Paged(_DiscoveryLops):
        def gql(self, query, variables):
            filter_ = (variables or {}).get("filter") or {}
            if "comments" not in filter_ or not pages:
                return super().gql(query, variables)
            return pages.pop(0)

    found = split_ledger.discover(lops=_Paged(), now="2026-09-10T00:00:00Z")
    assert {"DRE-5001", "DRE-5002"} <= set(found["cards"])


# --------------------------------------------------------------------------- #
# DRE-3356 — every row is dated                                                #
# --------------------------------------------------------------------------- #


def test_a_row_carries_the_cards_creation_date_as_iso_utc():
    row = split_ledger.row(_record(created_at="2026-08-12T09:33:21.482Z"))
    assert row["created_at"] == "2026-08-12T09:33:21Z"


def test_a_creation_date_that_could_not_be_read_is_unknown_not_empty():
    """`""` is what a reader sorting "the last N splits" silently sorts first."""
    row = split_ledger.row(_record(created_at=""))
    assert row["created_at"] == split_ledger.UNKNOWN
    assert row["created_at"] != ""
    assert any("creation date" in note for note in row["unreadable"])


def test_collect_reads_the_creation_date_off_the_card():
    lops = _FakeLops()
    out = split_ledger.collect(["DRE-3022"], lops=lops,
                               finder=lambda *a, **k: None,
                               readable=lambda repo, run=None: True)
    assert out["cards"][0]["created_at"] == "2026-08-12T09:33:21.482Z"
    assert "createdAt" in split_ledger._CARD_QUERY


# --------------------------------------------------------------------------- #
# DRE-3356 — the window and the months                                         #
# --------------------------------------------------------------------------- #


def test_the_ledger_records_the_window_it_was_derived_over():
    doc = split_ledger.ledger([_record()], generated_at="2026-09-10T00:00:00Z",
                              window_days=90)
    assert doc["window_days"] == 90
    assert isinstance(doc["window_days"], int)


def test_the_default_window_is_ninety_days():
    assert split_ledger.DEFAULT_WINDOW_DAYS == 90


def test_monthly_pins_a_complete_month_an_incomplete_one_and_an_unread_one():
    """The three shapes the card names, in one ledger.

      * `2026-07` — wholly inside the window and wholly in the past: complete,
        with a children count the read answered.
      * `2026-06` — the window opens mid-month, so the count is a partial one
        and `complete` says so rather than the number pretending otherwise.
      * `2026-08` — the children read failed: `UNKNOWN`, never `0`.
    """
    split = _record(identifier="DRE-7001", created_at="2026-07-14T00:00:00Z",
                    state_type="canceled", comments=[])
    died = _record(identifier="DRE-7002", created_at="2026-07-20T00:00:00Z",
                   successors=[])
    doc = split_ledger.ledger(
        [split, died], generated_at="2026-09-10T00:00:00Z", window_days=90,
        children_by_month={"2026-06": 41, "2026-07": 228, "2026-09": 90})

    months = {record["month"]: record for record in doc["monthly"]}
    assert [record["month"] for record in doc["monthly"]] == sorted(months)

    july = months["2026-07"]
    assert july["planner_children"] == 228
    assert july["split"] == 1
    assert july["died"] == 1
    assert july["complete"] is True

    june = months["2026-06"]
    assert june["planner_children"] == 41
    assert june["complete"] is False

    august = months["2026-08"]
    assert august["planner_children"] == split_ledger.UNKNOWN
    assert august["planner_children"] != 0
    assert august["complete"] is True

    # The month the file was generated in is not over, so it is not complete.
    assert months["2026-09"]["complete"] is False


def test_every_monthly_record_carries_the_contract_shape():
    doc = split_ledger.ledger([_record(created_at="2026-08-12T00:00:00Z")],
                              generated_at="2026-09-10T00:00:00Z")
    assert doc["monthly"], "the ledger carries no monthly block"
    for record in doc["monthly"]:
        assert set(record) == {"month", "planner_children", "split", "died",
                               "complete"}
        assert isinstance(record["month"], str)
        assert isinstance(record["complete"], bool)
        for field in ("planner_children", "split", "died"):
            assert (isinstance(record[field], int)
                    or record[field] == split_ledger.UNKNOWN)


def test_the_month_windows_clamp_to_the_window_and_say_when_they_did_not():
    windows = split_ledger.month_windows("2026-06-12T00:00:00Z",
                                         "2026-09-10T00:00:00Z")
    by_month = {w["month"]: w for w in windows}
    assert list(by_month) == ["2026-06", "2026-07", "2026-08", "2026-09"]
    assert by_month["2026-06"]["since"] == "2026-06-12T00:00:00Z"
    assert by_month["2026-06"]["complete"] is False
    assert by_month["2026-07"]["since"] == "2026-07-01T00:00:00Z"
    assert by_month["2026-07"]["until"] == "2026-08-01T00:00:00Z"
    assert by_month["2026-07"]["complete"] is True
    assert by_month["2026-09"]["until"] == "2026-09-10T00:00:00Z"


def test_the_children_count_is_a_planner_child_query_per_month():
    lops = _DiscoveryLops(children={"2026-07": 3, "2026-08": 5})
    windows = split_ledger.month_windows("2026-07-01T00:00:00Z",
                                         "2026-09-01T00:00:00Z")
    counts = split_ledger.child_counts(windows, lops=lops)
    assert counts["2026-07"] == 3
    assert counts["2026-08"] == 5
    for filter_ in lops.filters:
        assert filter_["parent"] == {"null": False}


def test_a_children_count_that_failed_is_unknown_in_the_month_never_zero():
    lops = _DiscoveryLops(children={"2026-07": 3},
                          raise_on=['"gte": "2026-08'])
    windows = split_ledger.month_windows("2026-07-01T00:00:00Z",
                                         "2026-09-01T00:00:00Z")
    notes: list = []
    counts = split_ledger.child_counts(windows, lops=lops, unreadable=notes)
    assert counts["2026-07"] == 3
    assert counts.get("2026-08") is None
    assert any("2026-08" in note for note in notes)
    record = split_ledger.monthly([], counts, since="2026-07-01T00:00:00Z",
                                  generated_at="2026-09-01T00:00:00Z")
    august = [r for r in record if r["month"] == "2026-08"][0]
    assert august["planner_children"] == split_ledger.UNKNOWN
    assert august["planner_children"] != 0


# --------------------------------------------------------------------------- #
# DRE-3356 — the render                                                        #
# --------------------------------------------------------------------------- #


def test_the_render_shows_the_by_month_table():
    doc = split_ledger.ledger(
        [_record(created_at="2026-07-14T00:00:00Z")],
        generated_at="2026-09-10T00:00:00Z", window_days=90,
        children_by_month={"2026-07": 228})
    text = split_ledger.render_markdown(doc)
    assert "## By month" in text
    assert "| 2026-07 | 228 |" in text


def test_the_render_prints_the_creation_date_of_every_row():
    doc = split_ledger.ledger(
        [_record(created_at="2026-07-14T09:00:00Z")],
        generated_at="2026-09-10T00:00:00Z")
    text = split_ledger.render_markdown(doc)
    assert "2026-07-14T09:00:00Z" in text


def test_the_render_says_the_window_it_was_derived_over():
    doc = split_ledger.ledger([_record()], generated_at="2026-09-10T00:00:00Z",
                              window_days=45)
    assert "45" in split_ledger.render_markdown(doc)


# --------------------------------------------------------------------------- #
# DRE-3356 — the committed artifacts and the contract                          #
# --------------------------------------------------------------------------- #

#: Every field `config/split-ledger.json` carried on `main` before this card,
#: with the type it carried. Written out rather than derived: this is the
#: contract DRE-3022's other children read (the context renderer DRE-3358 and
#: the plan critic's ledger check DRE-3079), and a list computed from the file
#: under test would agree with whatever the file happens to say.
CONTRACT_TOP_LEVEL = {
    "generated_at": str,
    "generated_by": str,
    "source": str,
    "seed_cards": list,
    "rows": list,
    "rates": dict,
}

CONTRACT_ROW = {
    "card": str, "title": str, "url": str, "state": str, "reasons": list,
    "size": str, "role": str, "declared_files": (list, str),
    "declared_file_count": (int, str), "piece_files": (list, str),
    "pieces": (int, str), "pieces_named": (list, str), "deaths": (int, str),
    "dollars": (int, float, str), "tells": list, "tell_evidence": dict,
    "unreadable": list,
}

CONTRACT_BAND = {"more_than": int, "of": int, "died": int, "cards": list,
                 "sentence": str}
CONTRACT_TELL_BAND = {"tell": str, "of": int, "died": int, "sentence": str}


def _committed() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def test_the_committed_ledger_keeps_every_field_name_and_type_it_had():
    doc = _committed()
    for field, kind in CONTRACT_TOP_LEVEL.items():
        assert field in doc, f"the ledger lost {field}"
        assert isinstance(doc[field], kind), f"{field} changed type"
    for row in doc["rows"]:
        for field, kind in CONTRACT_ROW.items():
            assert field in row, f"{row.get('card')} lost {field}"
            assert isinstance(row[field], kind), (
                f"{row.get('card')}.{field} changed type")
    for band in doc["rates"]["by_declared_files"]:
        for field, kind in CONTRACT_BAND.items():
            assert isinstance(band.get(field), kind), f"by_declared_files.{field}"
    for band in doc["rates"]["by_tell"]:
        for field, kind in CONTRACT_TELL_BAND.items():
            assert isinstance(band.get(field), kind), f"by_tell.{field}"


def test_the_committed_ledger_has_more_than_the_ten_seed_rows():
    """The population discovered itself. Ten rows is what `--card`-less derive
    used to produce, and it is the number this card exists to beat."""
    doc = _committed()
    assert len(doc["rows"]) > len(SEEDS), (
        f"the committed ledger still has {len(doc['rows'])} rows — derive did "
        "not discover a population")


def test_every_committed_row_is_dated():
    for row in _committed()["rows"]:
        assert "created_at" in row, f"{row.get('card')} has no created_at"
        assert isinstance(row["created_at"], str)
        assert row["created_at"] != ""


def test_the_committed_ledger_carries_its_window_and_its_months():
    doc = _committed()
    assert isinstance(doc["window_days"], int)
    assert isinstance(doc["monthly"], list)
    assert doc["monthly"], "the committed ledger has no monthly block"
    assert [r["month"] for r in doc["monthly"]] == sorted(
        r["month"] for r in doc["monthly"])
    for record in doc["monthly"]:
        assert set(record) == {"month", "planner_children", "split", "died",
                               "complete"}


def test_the_config_readme_describes_discovery_the_window_and_the_new_blocks():
    """A change that contradicts a document updates that document in the same
    PR (`standards/engineering.md`). The registry entry said the ledger reads
    the ten cards it was told about."""
    text = CONFIG_README.read_text(encoding="utf-8")
    entry = text.split("**`split-ledger.json`**", 1)[1].split("- **`", 1)[0]
    for phrase in ("discover", "window", "monthly", "created_at"):
        assert phrase in entry.lower(), (
            f"config/README.md's split-ledger entry never mentions {phrase}")


# --------------------------------------------------------------------------- #
# DRE-3356 — discovery proposes, the row's own readers dispose                  #
# --------------------------------------------------------------------------- #

#: A comment that MENTIONS the turn-cap tag without being the receipt. Both are
#: real shapes read off the board on 2026-09-09 while deriving this ledger: a
#: critic verdict quoting the tag, and a medic diagnosis naming it. Linear's
#: `containsIgnoreCase` cannot anchor, so the discovery search matches both —
#: about half the candidates it returned were exactly this.
QUOTING_COMMENT = (
    "🔎 QA Critic — VERDICT: APPROVE @6211b3b\n\n## Summary\n\nWhen an "
    f"automated comment carries {split_ledger.TURN_TAG} the sweep must not "
    "read it as a death.\n"
)


def test_a_candidate_whose_comments_only_quote_a_receipt_is_not_a_row():
    """The discovery searches are a NET, not a verdict. `_is_turn_cap_receipt`
    is anchored at the start of the comment and it is what decides."""
    candidate = _record(identifier="DRE-8001", comments=[QUOTING_COMMENT],
                        successors=[], created_at="2026-08-01T00:00:00Z")
    assert split_ledger.reasons(candidate) == []
    doc = split_ledger.ledger([candidate], generated_at="2026-09-10T00:00:00Z")
    assert [r["card"] for r in doc["rows"]] == []


def test_a_candidate_whose_comments_could_not_be_read_stays_with_its_unknowns():
    """"This card did not die" and "we could not look" are different facts.
    Dropping the second is the silent zero in its purest form."""
    unread = _record(identifier="DRE-8002", comments=None, successors=[],
                     comments_unreadable="Linear refused the read",
                     created_at="2026-08-01T00:00:00Z")
    doc = split_ledger.ledger([unread], generated_at="2026-09-10T00:00:00Z")
    assert [r["card"] for r in doc["rows"]] == ["DRE-8002"]
    assert doc["rows"][0]["deaths"] == split_ledger.UNKNOWN


def test_a_candidate_whose_successor_search_failed_stays_too():
    unread = _record(identifier="DRE-8003", comments=[], successors=None,
                     successors_unreadable="Linear refused the search",
                     created_at="2026-08-01T00:00:00Z")
    doc = split_ledger.ledger([unread], generated_at="2026-09-10T00:00:00Z")
    assert [r["card"] for r in doc["rows"]] == ["DRE-8003"]


def test_a_card_named_on_the_command_line_stays_whatever_history_says():
    """`--card` is a person saying "look at this one". A row that vanished
    because the board had nothing to say about it would look like a bug."""
    quiet = _record(identifier="DRE-8004", comments=[], successors=[],
                    created_at="2026-08-01T00:00:00Z")
    doc = split_ledger.ledger([quiet], generated_at="2026-09-10T00:00:00Z",
                              keep=["DRE-8004"])
    assert [r["card"] for r in doc["rows"]] == ["DRE-8004"]
    assert doc["rows"][0]["reasons"] == []


def test_every_committed_row_says_why_it_is_there():
    """The ledger is "every card that did not fit one run". A row that answers
    none of the three ways, and was not named as a seed, is a candidate the
    search proposed and the readers never confirmed."""
    for row in _committed()["rows"]:
        assert row["reasons"] or split_ledger.UNKNOWN in (
            row["deaths"], row["pieces"]), (
            f"{row['card']} is in the ledger for no readable reason")
