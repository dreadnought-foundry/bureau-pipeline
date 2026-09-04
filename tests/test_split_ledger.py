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
        [], executions=[{"subtype": "error_max_turns", "num_turns": 151,
                         "total_cost_usd": 18.40}]
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
    assert not split_ledger.cites("see DRE-3022 for background", "DRE-3022")
    assert not split_ledger.cites("piece 1 of 3 of DRE-3022", "DRE-2719")


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
