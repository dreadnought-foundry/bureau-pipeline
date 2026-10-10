"""The split ledger, derived (DRE-3077).

`scripts/split_ledger.py derive` reads every turn-cap death, split and hand-back
into one ledger document. This is piece 1 of 3 of DRE-3022 and owes nothing to
the other two: nothing here injects the ledger into the planner and nothing
scores against it. Since DRE-6056 the ledger is no longer committed: the plan
job derives it from the read door at the start of each run, and
`tests/test_split_ledger_retired.py` holds the retired job gone.

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
  * **The seed cards.** The ten cards the card names stay in the population.

DRE-3356 adds three things and these tests pin each of them:

  * **The population is DISCOVERED, not named.** Discovery finds the cards
    whose own comments carry a turn-cap or hand-back receipt and the cards a
    successor cites as the one it was cut from, inside a `--window-days`
    window — and the seeds stay in whatever the window says. Since DRE-6055 it
    runs in the console behind the read door; the needles and `cites()` it is
    built on are pinned here, and a read the door could not make is NAMED in
    the ledger's `source`, never quietly dropped.
  * **Every row is dated.** `created_at` is the card's Linear `createdAt` as
    ISO-8601 UTC, or the literal `UNKNOWN` — never the empty string, which is
    what a reader sorting "the last N splits" would silently sort first.
  * **`monthly` counts the splits by month**, with `complete` saying whether
    the record covers the whole month, and `planner_children` saying `UNKNOWN`
    rather than `0` when the read failed.

And one test guards the contract DRE-3022's other children read: every field
the ledger carried before DRE-3356 still has its name and its type, in the
ledger `derive` writes from the door today.

Run: cd bureau-pipeline && python3 -m pytest tests/test_split_ledger.py -v
"""

from __future__ import annotations

import datetime
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
import step_shell  # noqa: E402

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
    """The turn-cap requeue receipt, from `dead_run` itself: a run that died
    past implementation green (DRE-4366 — only that death is retried)."""
    decision = dead_run.decide(
        0, turn_exhaustion=True,
        turn_facts=f"the 150-turn cap after 151 turns and {cost}",
        last_progress=3,
    )
    assert decision.action == "requeue"
    return decision.comments[0]


def _hold_receipt(cost: str = "$19.12") -> str:
    decision = dead_run.decide(
        dead_run.TURN_REQUEUE_CAP, turn_exhaustion=True,
        turn_facts=f"the 150-turn cap after 151 turns and {cost}",
        last_progress=3,
    )
    assert decision.action == "hold"
    return decision.comments[0]


def _replan_receipt(cost: str = "$12.50") -> str:
    """DRE-4366: a death before implementation green sends the card to
    Planning, and that is a turn-cap death as much as the other two."""
    decision = dead_run.decide(
        0, turn_exhaustion=True,
        turn_facts=f"the 400-turn cap after 401 turns and {cost}",
        last_progress=2,
    )
    assert decision.action == "replan"
    return decision.comments[0]


def test_both_turn_cap_receipts_count_as_deaths():
    deaths = split_ledger.turn_cap_deaths([_requeue_receipt(), _hold_receipt()])
    assert len(deaths) == 2
    assert [d["dollars"] for d in deaths] == [20.10, 19.12]


def test_the_replan_receipt_counts_as_a_turn_cap_death():
    deaths = split_ledger.turn_cap_deaths([_replan_receipt()])
    assert len(deaths) == 1
    assert deaths[0]["dollars"] == 12.50


def test_the_receipts_that_used_to_be_missed_count_as_deaths():
    """The cancelled turn-cap death and the unlanded park both carry the tag
    on their first line now, and the ledger counts each once."""
    facts = "the 400-turn cap after 401 turns and $9.00"
    cancelled = dead_run.decide(0, turn_exhaustion=True, cancelled=True,
                                turn_facts=facts, last_progress=3)
    assert cancelled.action == "defer"
    unlanded = dead_run.park_unlanded_comment("https://r", dead_run.TURN_TAG)
    noted = dead_run.turn_noted_comment("escalation", facts, "https://r")
    deaths = split_ledger.turn_cap_deaths(
        [cancelled.comments[0], unlanded, noted])
    assert len(deaths) == 3


def test_a_limit_death_is_not_a_turn_cap_death():
    wall = dead_run.LimitDeath(kind="claude", stage="build", reset=None,
                               run_id="35500000000")
    decision = dead_run.decide(0, turn_exhaustion=True, last_progress=3,
                               limit=wall)
    assert decision.action == "limit"
    assert split_ledger.turn_cap_deaths(decision.comments) == []


def test_a_comment_quoting_the_replan_receipt_is_not_one():
    quoted = f"The critic notes: {_replan_receipt()}"
    assert split_ledger.turn_cap_deaths([quoted]) == []


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
    workflow = step_shell.workflow_source(
        ROOT / ".github" / "workflows" / "agent-task.yml")
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
# the seed cards                                                               #
# --------------------------------------------------------------------------- #


def test_the_seed_cards_are_the_ten_the_card_names():
    assert set(SEEDS) <= set(split_ledger.SEED_CARDS)


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


def test_a_children_count_the_door_could_not_read_is_unknown_never_zero():
    """The door serves `"UNKNOWN"` for a month it could not count (DRE-6054);
    the month says so, and a month it never mentioned says so too."""
    record = split_ledger.monthly(
        [], {"2026-07": 3, "2026-08": "UNKNOWN"},
        since="2026-07-01T00:00:00Z", generated_at="2026-09-02T00:00:00Z")
    months = {r["month"]: r for r in record}
    assert months["2026-07"]["planner_children"] == 3
    for month in ("2026-08", "2026-09"):
        assert months[month]["planner_children"] == split_ledger.UNKNOWN
        assert months[month]["planner_children"] != 0


# --------------------------------------------------------------------------- #
# DRE-3356 — the contract                                                      #
# --------------------------------------------------------------------------- #

#: Every field the committed ledger carried on `main` before DRE-3356, with
#: the type it carried. Since DRE-6056 nothing is committed, so the contract is
#: held against the ledger `derive` writes from the door (below). Written out rather than derived: this is the
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


def _holds_the_contract(doc: dict) -> None:
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
    for row in doc["rows"]:
        assert isinstance(row.get("created_at"), str), row.get("card")
        assert row["created_at"] != "", row.get("card")
    assert isinstance(doc["window_days"], int)
    assert doc["monthly"], "the ledger has no monthly block"
    assert [r["month"] for r in doc["monthly"]] == sorted(
        r["month"] for r in doc["monthly"])
    for record in doc["monthly"]:
        assert set(record) == {"month", "planner_children", "split", "died",
                               "complete"}


def test_the_config_readme_describes_discovery_the_window_and_the_new_blocks():
    """A change that contradicts a document updates that document in the same
    PR (`standards/engineering.md`). The registry entry said the ledger reads
    the ten cards it was told about; since DRE-6056 it says where the ledger
    went when it stopped being a file here."""
    text = CONFIG_README.read_text(encoding="utf-8")
    entry = text.split("- **The split ledger**", 1)[1].split("- **`", 1)[0]
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


# --------------------------------------------------------------------------- #
# DRE-6055 — the ledger is derived from the read door                          #
# --------------------------------------------------------------------------- #
#
# `derive` makes ONE `bureau_read.split_history(since)` call and builds the
# ledger from what DRE-6054's door serves: the rows the console derived with
# its own copy of the readers, plus the two fields it does not serve — `url`,
# built from the identifier, and `piece_files`, read from GitHub exactly as
# before. Linear is never asked.
#
# `tests/fixtures/split-ledger-door-parity.json` was recorded by running the
# Linear path (`collect` + `ledger`, both deleted by this card) over a fixture
# board in this card's failing-test commit, before the deletion. `door` is the
# same history as the door serves it; `linear_path` is what the old path wrote.

import ast  # noqa: E402
import re  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
import bureau_read  # noqa: E402
import card_pr  # noqa: E402
import planner_score  # noqa: E402
from bureau_read_fakes import (  # noqa: E402
    FakeDoor, FakeIssuer, door_env, split_history_body, split_history_row)

PARITY = json.loads((ROOT / "tests" / "fixtures" / "split-ledger-door-parity.json")
                    .read_text(encoding="utf-8"))


@pytest.fixture
def door(monkeypatch):
    """A fake door serving the parity fixture, and an issuer to mint its token."""
    monkeypatch.delenv("SPLIT_LEDGER_PATH", raising=False)
    bureau_read.reset_for_tests()
    with FakeIssuer() as issuer, FakeDoor() as fake:
        for key, value in door_env(door_url=fake.url, issuer=issuer).items():
            monkeypatch.setenv(key, value)
        fake.split_history = json.loads(json.dumps(PARITY["door"]))
        yield fake
    bureau_read.reset_for_tests()


@pytest.fixture
def github(monkeypatch):
    """GitHub as the fixture board recorded it: which repos this token can see
    and each piece's merged pull request. Records every PR search it answers."""
    asked: list = []

    def find(identifier, repo=None, fields=None, run=None):
        asked.append((identifier, repo))
        pr = PARITY["pull_requests"].get(identifier)
        if pr is None:
            return None
        return {"number": pr["number"], "state": pr["state"],
                "files": [{"path": path} for path in pr["files"]]}

    monkeypatch.setattr(card_pr, "find", find)
    monkeypatch.setattr(planner_score, "repo_is_readable",
                        lambda repo, run=None: PARITY["readable"][repo])
    return asked


def _derive(tmp_path, *extra) -> dict:
    out = tmp_path / "split-ledger.json"
    assert split_ledger.main(["derive", "--out", str(out), *extra]) == 0
    return json.loads(out.read_text(encoding="utf-8"))


def _without_url(rows) -> list:
    return [{k: v for k, v in row.items() if k != "url"} for row in rows]


def test_the_door_path_writes_what_the_linear_path_wrote(door, github, tmp_path):
    doc = _derive(tmp_path)
    old = PARITY["linear_path"]
    assert _without_url(doc["rows"]) == _without_url(old["rows"])
    # The same fields in the same order: the file is read by eye too.
    assert [list(row) for row in doc["rows"]] == [list(row) for row in old["rows"]]
    assert doc["monthly"] == old["monthly"]
    assert doc["rates"] == old["rates"]
    assert doc["generated_at"] == old["generated_at"]
    assert doc["window_days"] == old["window_days"]


def test_the_derived_ledger_keeps_every_field_name_and_type_it_had(door, github,
                                                                  tmp_path):
    _holds_the_contract(_derive(tmp_path))


def test_every_derived_row_says_why_it_is_there(door, github, tmp_path):
    """The ledger is "every card that did not fit one run". A row that answers
    none of the three ways, and was not named as a seed, is a candidate the
    search proposed and the readers never confirmed."""
    for row in _derive(tmp_path)["rows"]:
        assert row["reasons"] or split_ledger.UNKNOWN in (
            row["deaths"], row["pieces"]), (
            f"{row['card']} is in the ledger for no readable reason")


def test_the_url_is_built_from_the_identifier(door, github, tmp_path):
    for row in _derive(tmp_path)["rows"]:
        assert row["url"] == (
            f"https://linear.app/dreadnoughtfoundry/issue/{row['card']}")


def test_one_derive_makes_exactly_one_door_call(door, github, tmp_path):
    _derive(tmp_path)
    assert len(door.asked("/split-history")) == 1
    assert len(door.requests) == 1


def test_the_door_is_asked_for_the_window_start(door, github, tmp_path):
    # The clock is read on both sides of the call: one read after it can land
    # a second past the derive's own, and 30 days plus a second is not wrong.
    before = split_ledger._moment(split_ledger.now_iso())
    _derive(tmp_path, "--window-days", "30")
    after = split_ledger._moment(split_ledger.now_iso())
    since = door.asked("/split-history")[0]["query"]["since"]
    assert split_ledger._moment(since) is not None
    month = datetime.timedelta(days=30)
    assert before - month <= split_ledger._moment(since) <= after - month


def test_piece_files_are_read_from_github_through_the_served_repos(door, github,
                                                                  tmp_path):
    rows = {row["card"]: row for row in _derive(tmp_path)["rows"]}
    assert rows["DRE-9101"]["piece_files"] == ["scripts/a.py", "tests/test_a.py"]
    # A repo this token cannot see is never searched, and a piece with no repo
    # has nowhere to be searched — both say why.
    assert github == [("DRE-9102", "dreadnought-foundry/bureau-pipeline"),
                      ("DRE-9109", "dreadnought-foundry/bureau-pipeline")]
    assert any(note.startswith("DRE-9103: this token cannot read "
                               "dreadnought-foundry/portico")
               for note in rows["DRE-9101"]["unreadable"])


def test_a_null_piece_slug_gives_the_no_repo_note(door, github, tmp_path):
    rows = {row["card"]: row for row in _derive(tmp_path)["rows"]}
    assert ("DRE-9104: the card names no repo this rail routes, so there is "
            "nowhere to look for its pull request") in rows["DRE-9101"]["unreadable"]


def test_a_served_unknown_death_stays_unknown_with_its_reason(door, github,
                                                             tmp_path):
    rows = {row["card"]: row for row in _derive(tmp_path)["rows"]}
    assert rows["DRE-9106"]["deaths"] == split_ledger.UNKNOWN
    assert rows["DRE-9106"]["deaths"] != 0
    assert rows["DRE-9106"]["dollars"] == split_ledger.UNKNOWN
    assert ("this card's comments could not be read: the thread is not complete"
            in rows["DRE-9106"]["unreadable"])


def test_unknown_pieces_read_no_github_and_stay_unknown(door, github, tmp_path):
    door.split_history = split_history_body([split_history_row(
        "DRE-9300", pieces="UNKNOWN", pieces_named="UNKNOWN", piece_repos={},
        unreadable=["the successor search could not be read"])])
    row = _derive(tmp_path)["rows"][0]
    assert row["piece_files"] == split_ledger.UNKNOWN
    assert row["unreadable"] == ["the successor search could not be read"]
    assert github == []


def test_the_doors_unreadable_and_unknown_lists_reach_the_source_sentence(
        door, github, tmp_path):
    door.split_history["unknown"] = [
        {"identifier": "DRE-9200", "reason": "no stored creation date"}]
    source = _derive(tmp_path)["source"]
    for note in PARITY["door"]["unreadable"]:
        assert note in source
    assert "DRE-9200" in source and "no stored creation date" in source


def test_a_door_that_cannot_answer_is_a_ledger_error(door):
    door.routes["/split-history"] = (503, {"error": {"code": "DOOR_CLOSED"}})
    with pytest.raises(split_ledger.LedgerError, match="could not be read"):
        split_ledger.derive()


def test_a_door_unknown_never_falls_back_to_linear(door, monkeypatch):
    """The door's H1 rule, and the reason for the change: a Linear walk on a
    refused read would spend the bucket the door exists to spare."""
    import types

    called: list = []
    planted = types.ModuleType("linear_ops")
    planted.gql = lambda *a, **k: called.append("gql")
    planted.comment_bodies = lambda *a, **k: called.append("comment_bodies")
    monkeypatch.setitem(sys.modules, "linear_ops", planted)
    door.routes["/split-history"] = (429, {"error": {"code": "THROTTLED"}})
    with pytest.raises(split_ledger.LedgerError):
        split_ledger.derive()
    assert called == []
    assert len(door.requests) == 1


def test_the_cli_says_could_not_be_read_and_writes_nothing(door, tmp_path, capsys):
    door.routes["/split-history"] = (503, {"error": {"code": "DOOR_CLOSED"}})
    out = tmp_path / "split-ledger.json"
    assert split_ledger.main(["derive", "--out", str(out)]) != 0
    assert not out.exists()
    assert "could not be read" in capsys.readouterr().err


@pytest.mark.parametrize("days", [92, 120, 0])
def test_a_window_the_door_refuses_is_a_ledger_error_before_any_call(door, days):
    with pytest.raises(split_ledger.LedgerError):
        split_ledger.derive(window_days=days)
    assert door.requests == []


def test_derive_from_a_saved_door_response_asks_nobody(door, github, tmp_path):
    saved = tmp_path / "door.json"
    saved.write_text(json.dumps(PARITY["door"]), encoding="utf-8")
    doc = _derive(tmp_path, "--from", str(saved))
    assert door.requests == []
    assert _without_url(doc["rows"]) == _without_url(PARITY["linear_path"]["rows"])


def test_the_derive_prints_one_summary_line_naming_every_row(door, github,
                                                            tmp_path, capsys):
    doc = _derive(tmp_path)
    lines = [line for line in capsys.readouterr().out.splitlines()
             if line.startswith("split-ledger:")]
    assert len(lines) == 1, lines
    assert f"{len(doc['rows'])} row" in lines[0]
    for row in doc["rows"]:
        assert row["card"] in lines[0]


def test_derive_writes_the_ledger_and_nothing_beside_it(door, github, tmp_path):
    """No markdown render rides along any more (DRE-6056): the one file named
    by `--out` is the whole output."""
    _derive(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["split-ledger.json"]


def test_load_reads_split_ledger_path_when_it_is_set(tmp_path, monkeypatch):
    derived = tmp_path / "derived.json"
    derived.write_text(json.dumps({"rows": [], "marker": "derived"}),
                       encoding="utf-8")
    monkeypatch.setenv("SPLIT_LEDGER_PATH", str(derived))
    assert split_ledger.load()["marker"] == "derived"


def test_load_with_split_ledger_path_unset_raises_never_reads_a_stale_file(
        monkeypatch):
    """There is no committed fallback (DRE-6056): every reader renders
    "could not be read" rather than last month's history."""
    monkeypatch.delenv("SPLIT_LEDGER_PATH", raising=False)
    with pytest.raises(split_ledger.LedgerError, match="SPLIT_LEDGER_PATH"):
        split_ledger.load()
    monkeypatch.setenv("SPLIT_LEDGER_PATH", "")
    with pytest.raises(split_ledger.LedgerError, match="SPLIT_LEDGER_PATH"):
        split_ledger.load()


def test_an_explicit_path_still_wins_over_split_ledger_path(tmp_path,
                                                           monkeypatch):
    named = tmp_path / "named.json"
    named.write_text(json.dumps({"rows": [], "marker": "named"}),
                     encoding="utf-8")
    monkeypatch.setenv("SPLIT_LEDGER_PATH", str(tmp_path / "absent.json"))
    assert split_ledger.load(str(named))["marker"] == "named"


SCRIPT = ROOT / "scripts" / "split_ledger.py"
GONE = re.compile(
    r'_COMMENT_SEARCH_QUERY|_CITATION_SEARCH_QUERY|_CHILD_COUNT_QUERY|'
    r'_CARD_QUERY|_SUCCESSOR_QUERY|comment_bodies|def discover|def collect|'
    r'def child_counts|"--card"|--no-discover')


def test_the_linear_reads_are_gone():
    source = SCRIPT.read_text(encoding="utf-8")
    assert GONE.findall(source) == []
    assert not re.search(r"\blinear_ops\b|\blops\b", source)
    imported = {alias.name for node in ast.walk(ast.parse(source))
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names} | {
        node.module for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)}
    assert "linear_ops" not in imported
    assert "bureau_read" in imported


def test_the_readers_stay_and_name_their_second_copy():
    for name in ("tells", "tell_evidence", "declared_files", "cites",
                 "cited_origins", "turn_cap_deaths", "dollars_spent",
                 "handed_back", "size_of", "role_of", "reasons", "belongs",
                 "COMMENT_NEEDLES", "CITATION_NEEDLES", "SEED_CARDS"):
        assert hasattr(split_ledger, name), name
    assert "console/backend/split_history.py" in SCRIPT.read_text(encoding="utf-8")
