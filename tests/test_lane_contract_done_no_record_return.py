"""Done's one exit is the relay's no-record return (DRE-7327).

The Done lane's exit used to read "None. Done is terminal". Once agent-bureau's
relay lane guard returns a `no-code` card that reached Done with no record to
`Queued`, that sentence is false, and the contract is the file the lane readers
and the rendered `docs/lane-contract.md` take their word from.

The guarded write layer in this repository does not change: the relay is a
standalone Lambda that writes the state with its own GraphQL `issueUpdate` and
never calls `linear_ops.py`, so the layer keeps refusing to un-complete a card
for the pipeline's writers (tests/test_linear_ops_state_guard.py holds that).
What changes is the TEXT of two clauses — Done's exit and Queued's writers —
and nothing else: agent-bureau's `config/pipeline-vocabulary.json` snapshot and
its generated relay regions read the lanes' other fields, so these tests pin
them where they stood.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lane_contract  # noqa: E402


ROOT = os.path.join(os.path.dirname(__file__), "..")
DOC = os.path.join(ROOT, "docs", "lane-contract.md")

# The contract strings shared with the agent-bureau relay cards, spelled once.
RECEIPT_TAG = "lane-guard-returned"
DESTINATION = "Queued"
SWITCH = "NO_RECORD_RETURN_MODE"


def _text(lane, kind):
    return lane_contract.lane(lane)["clauses"][kind]["text"]


class TestDoneExitNamesTheOneException:
    def test_the_exit_no_longer_says_none(self):
        assert not _text("Done", "exit").startswith("None."), (
            "the Done exit still says it has none, but the relay's no-record "
            "return takes a card out of it"
        )

    def test_the_exit_names_the_relays_return_of_a_no_code_card_to_queued(self):
        text = _text("Done", "exit")
        assert "relay" in text
        assert "lane guard" in text
        assert "`no-code`" in text
        assert f"`{DESTINATION}`" in text
        assert "one exit" in text

    def test_the_exit_names_the_receipt_and_the_switch(self):
        text = _text("Done", "exit")
        assert f"`🚧 {RECEIPT_TAG}`" in text
        assert f"`{SWITCH}`" in text

    def test_the_exit_names_what_no_record_means(self):
        text = _text("Done", "exit")
        assert "hour" in text
        assert "comment by a person" in text
        assert "`Outcome:`" in text
        assert "`Released as <tag>`" in text

    def test_the_exit_says_the_guarded_write_layer_still_refuses(self):
        text = _text("Done", "exit")
        assert "guarded write layer" in text
        assert "un-complete" in text
        # The relay writes the state itself, never through this repo's layer.
        assert "linear_ops.py" in text

    def test_the_exit_names_the_three_out_of_scope_classes(self):
        text = _text("Done", "exit")
        assert "`PROOF:`" in text
        assert "weekly agent report" in text
        assert "epic-growth question" in text


class TestQueuedWritersNameTheReturn:
    def test_the_writers_text_names_the_relays_no_record_return(self):
        text = _text("Queued", "writers")
        assert "relay" in text
        assert "no record" in text
        assert "`no-code`" in text
        assert f"`🚧 {RECEIPT_TAG}`" in text
        assert f"`{SWITCH}`" in text

    def test_the_writers_text_keeps_what_it_already_said(self):
        text = _text("Queued", "writers")
        assert text.startswith("The planner and mid-epic discovery create here")
        assert "DRE-6230" in text
        assert "DRE-5365" in text


class TestNothingElseMoves:
    """Only the two texts change. These are the values the lanes held before
    DRE-7327, pinned so an edit that moves one turns the build red."""

    def test_done_keeps_its_fields(self):
        done = lane_contract.lane("Done")
        assert done["status"] == "live"
        assert done["segment"] == "work"
        exit_clause = done["clauses"]["exit"]
        assert exit_clause["enforced_from"] == "5"
        assert exit_clause["assertion"] is None
        assert exit_clause["pending"] == "needs the transition history Phase 5 records"
        assert lane_contract.lane_writers("Done") == (
            "linear-sync.yml",
            "reconcile.py",
            "linear_ops.py",
            "operator",
            "hygiene.py",
            "model-adoption.yml",
            "release-train.yml",
        )

    def test_queued_keeps_its_fields(self):
        queued = lane_contract.lane("Queued")
        assert queued["status"] == "live"
        assert queued["segment"] == "work"
        writers = queued["clauses"]["writers"]
        assert writers["enforced_from"] == "2"
        assert writers["assertion"] == "lane.writers_exist"
        assert lane_contract.lane_writers("Queued") == (
            "plan.yml",
            "mid_epic.py",
            "reconcile.py",
            "dead_run.py",
            "linear_ops.py",
            "agent-task.yml",
            "hygiene.py",
            "hand_work_migration.py",
        )


class TestTheRenderedDocSaysIt:
    def test_the_doc_carries_both_texts(self):
        with open(DOC, encoding="utf-8") as f:
            doc = f.read()
        assert _text("Done", "exit") in doc
        assert _text("Queued", "writers") in doc
