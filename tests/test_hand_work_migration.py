"""RED-first: the hand-built migration — every automatically applied
`hand-built` handled once, dry run first (DRE-6230, rewriting DRE-5323's
script in place).

WHAT IS UNDER TEST, over a stubbed board that records every write — nothing
here reaches Linear:
  * ORIGIN first. The actor is the one named on the history entry that added
    `hand-built`, or the card's creator when no entry did. Automatic ⇔ that
    actor is one of the non-human identities `config/linear-identities.json`
    declares, or an account named by `--include-actor`. A person's mark is
    kept, a mark with no actor is left alone, and `--include` treats one named
    card as automatic.
  * Then the CLASS, in order: an epic and an operator step swap `hand-built`
    for `operator-step` in place; a proof loses the label and nothing else; a
    code card loses it and then goes by lane — with a critic pass on record it
    is restamped FLEET (its live verdicts retired first) and a Hand-work one
    moves to Backlog; without one, Hand-work and Backlog move to Planning;
    Intake, In Progress and In Review only lose the label.
  * `run` without `--apply` writes nothing; `--apply` re-reads each card's lane
    and refuses one that moved; an unreadable Linear exits non-zero before any
    write; no write names Todo; no card id appears in the script.
  * Its comment sites are in the act registry, and its writes are ones the
    lane contract permits.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hand_work_migration.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import backlog_cutover  # noqa: E402 — `card_ids_in_code`, the "no allowlist" check
import hand_work_migration as hwm  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import plan_critic  # noqa: E402
import ready_lane_writers  # noqa: E402
import routing_verdict  # noqa: E402

HAND_BUILT = routing_verdict.HAND_BUILT_LABEL
OPERATOR_STEP = routing_verdict.OPERATOR_STEP_LABEL
NO_CODE = linear_ops.NO_CODE_LABEL
CREATED = "2026-09-01T00:00:00.000Z"
MARKED = "2026-09-10T00:00:00.000Z"


def _identities():
    with open(ROOT / "config" / "linear-identities.json", encoding="utf-8") as fh:
        rows = json.load(fh)["identities"]
    return {row["name"]: row["display_name"] for row in rows}


IDENTITIES = _identities()
FLEET = IDENTITIES["fleet"]
TOOLS = IDENTITIES["operator-tools"]
PERSON = "Dana Lee"


def _card(identifier, *, title="a card", labels=(HAND_BUILT,), lane="Hand-work",
          comments=(), children=0, parent=None, added_by=FLEET, creator=FLEET,
          history=None, history_partial=False):
    """A card as the population query returns it. `comments` are
    (body, createdAt, author) oldest→newest; the API hands them back newest
    first. `added_by` is the actor of the history entry adding `hand-built`;
    None writes no such entry (the label was set at creation)."""
    nodes = [{"body": b, "createdAt": at, "user": ({"id": f"u-{who}", "name": who}
                                                   if who else None)}
             for b, at, who in comments]
    if history is None:
        history = ([{"createdAt": MARKED, "actor": {"name": added_by},
                     "addedLabels": [{"name": HAND_BUILT}]}]
                   if added_by else [])
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": title,
        "createdAt": CREATED,
        "state": {"name": lane},
        "creator": {"name": creator} if creator else None,
        "labels": {"nodes": [{"name": n} for n in labels]},
        "parent": parent,
        "children": {"nodes": [{"id": f"kid-{i}"} for i in range(children)]},
        "history": {"pageInfo": {"hasNextPage": history_partial, "endCursor": None},
                    "nodes": list(history)},
        "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                     "nodes": list(reversed(nodes))},
    }


class FakeOps:
    """The write layer's surface the script touches, recording every write.
    `threads` answers the whole-thread read of a parent epic: id → comments
    (body, createdAt, author) oldest→newest. `lanes` overrides the lane the
    pre-write re-read sees, for a card that moved since the census."""

    def __init__(self, cards, threads=None, unreadable=False):
        self.cards = list(cards)
        self.threads = dict(threads or {})
        self.unreadable = unreadable
        self.lanes: dict[str, str] = {}
        self.states: list[tuple] = []
        self.comments: list[tuple] = []
        self.removed: list[tuple] = []
        self.added: list[tuple] = []
        self.log: list[tuple] = []  # every write, in the order it was made
        self.state_result = True
        self.state_error: Exception | None = None

    def writes(self):
        return self.states + self.comments + self.removed + self.added

    def gql_paged(self, query, variables=None, **kw):
        if self.unreadable:
            raise linear_ops.LinearError("linear error: 502")
        return list(self.cards)

    def gql(self, query, variables=None):
        if self.unreadable:
            raise linear_ops.LinearError("linear error: 502")
        ident = variables["id"]
        if "comments" in query:
            nodes = [{"body": b, "createdAt": at, "user": {"name": who} if who else None}
                     for b, at, who in self.threads.get(ident, [])]
            return {"issue": {"comments": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": list(reversed(nodes))}}}
        lane = self.lanes.get(ident)
        if lane is None:
            lane = next(c["state"]["name"] for c in self.cards if c["identifier"] == ident)
        return {"issue": {"state": {"name": lane}}}

    def cmd_state(self, identifier, state, *flags, **kw):
        if self.state_error is not None:
            raise self.state_error
        self.states.append((identifier, state, kw))
        self.log.append(("state", identifier, state))
        return self.state_result

    def cmd_comment(self, identifier, body, *flags):
        self.comments.append((identifier, body))
        self.log.append(("comment", identifier, body))
        return None

    def remove_label(self, identifier, label):
        self.removed.append((identifier, label))
        self.log.append(("remove", identifier, label))

    def add_label(self, identifier, label):
        # The real seam refuses the CEO's mark (DRE-6225); so does this one.
        assert label != HAND_BUILT, "the migration may never apply hand-built"
        self.added.append((identifier, label))
        self.log.append(("add", identifier, label))


class Stamps:
    """`routing_verdict.stamp_card`, recorded rather than written."""

    def __init__(self, result=0):
        self.calls: list[tuple] = []
        self.result = result

    def __call__(self, identifier, name, why, *, title=None):
        self.calls.append((identifier, name, why))
        return self.result


def _verdict(name, at="2026-09-02T00:00:00.000Z"):
    return (routing_verdict.verdict_comment(name, "a fixture reason"), at, FLEET)


def _marker(stage, result, at="2026-09-03T00:00:00.000Z", who=FLEET):
    return (plan_critic.marker(stage, 1, result), at, who)


# --------------------------------------------------------------------------
# The stubbed board: one of each, as the acceptance criteria name them.
# --------------------------------------------------------------------------
EPIC = _card("DRE-1", title="[EPIC] a plan", lane="Backlog", children=2)
PROOF = _card("DRE-2", title="PROOF: the thing works live",
              labels=(HAND_BUILT, NO_CODE, "agent:ops"), comments=[_verdict("OPERATOR")])
OP_NO_CODE = _card("DRE-3", title="rotate the key", labels=(HAND_BUILT, NO_CODE),
                   comments=[_verdict("OPERATOR")])
OP_TITLED = _card("DRE-4", title="[OPERATOR] run the backfill", lane="Backlog")
ONE_OFF_PASSED = _card("DRE-5", title="add a flag to the sweep",
                       comments=[_marker(plan_critic.STAGE_ONE_OFF, plan_critic.PASS),
                                 _verdict("WORKBENCH")])
CHILD_RELEASED = _card("DRE-6", title="a child of a released epic", lane="Backlog",
                       parent={"identifier": "DRE-900", "state": {"name": "In Progress"}},
                       comments=[_verdict("WORKBENCH")])
CODE_UNPASSED = _card("DRE-7", title="a code card nobody reviewed", added_by=TOOLS)
CODE_INTAKE = _card("DRE-8", title="a code card in intake", lane="Intake")
CODE_REVIEW = _card("DRE-9", title="a code card under review", lane="In Review")
PERSONS = _card("DRE-10", title="a card the person marked", lane="Intake",
                added_by=PERSON, creator=PERSON)
CREATED_WITH = _card("DRE-11", title="filed with the label", lane="In Progress",
                     added_by=None, creator=FLEET)
NO_ACTOR = _card("DRE-12", title="nobody's mark", lane="Backlog",
                 history=[{"createdAt": MARKED, "actor": None,
                           "addedLabels": [{"name": HAND_BUILT}]}])

BOARD = [EPIC, PROOF, OP_NO_CODE, OP_TITLED, ONE_OFF_PASSED, CHILD_RELEASED,
         CODE_UNPASSED, CODE_INTAKE, CODE_REVIEW, PERSONS, CREATED_WITH, NO_ACTOR]
THREADS = {"DRE-900": [("plan-cycle: start epic=DRE-900", "2026-09-01T00:00:00.000Z", FLEET),
                       _marker(plan_critic.STAGE_POST, plan_critic.PASS)]}


def _rows(cards=BOARD, threads=THREADS, **kw):
    ops = FakeOps(cards, threads)
    return hwm.census(cards, ops, **kw), ops


def _by_id(rows):
    return {r["identifier"]: r for r in rows}


def _apply(cards=BOARD, threads=THREADS, stamps=None, **kw):
    rows, ops = _rows(cards, threads, **kw)
    stamps = stamps or Stamps()
    with mock.patch.object(hwm.routing_verdict, "stamp_card", stamps):
        result = hwm.run(ops, rows, apply=True)
    return ops, stamps, result


def _touched(ops, stamps):
    return ({i for i, *_ in ops.writes()} | {i for i, *_ in stamps.calls})


# --------------------------------------------------------------------------
# 1: origin first
# --------------------------------------------------------------------------
class TestOrigin:
    def test_the_non_human_identities_are_read_off_the_declaration(self):
        assert hwm.non_human_actors() == frozenset(IDENTITIES.values())

    def test_a_history_entry_names_the_actor(self):
        got = _by_id(_rows()[0])
        assert got["DRE-7"]["actor"] == TOOLS and got["DRE-7"]["origin"] == "history"
        assert got["DRE-7"]["automatic"] is True

    def test_a_label_set_at_creation_is_the_creators(self):
        row = _by_id(_rows()[0])["DRE-11"]
        assert row["actor"] == FLEET and row["origin"] == "creation"
        assert row["automatic"] is True

    def test_a_persons_mark_is_kept(self):
        row = _by_id(_rows()[0])["DRE-10"]
        assert row["automatic"] is False
        assert row["action"] == hwm.KEEP
        assert f"kept — applied by hand by {PERSON}" in row["summary"]

    def test_no_actor_at_all_is_left_alone(self):
        row = _by_id(_rows()[0])["DRE-12"]
        assert row["automatic"] is None
        assert row["action"] == hwm.UNTOLD
        assert "could not tell — left alone" in row["summary"]

    def test_the_newest_entry_adding_the_label_wins(self):
        card = _card("DRE-20", lane="Intake", history=[
            {"createdAt": "2026-09-01T00:00:00.000Z", "actor": {"name": PERSON},
             "addedLabels": [{"name": HAND_BUILT}]},
            {"createdAt": "2026-09-05T00:00:00.000Z", "actor": {"name": FLEET},
             "addedLabels": [{"name": "Hand-Built"}]},
        ])
        row = _rows([card])[0][0]
        assert row["actor"] == FLEET and row["automatic"] is True

    def test_a_partial_history_with_no_entry_could_not_tell(self):
        """Beyond the window the label may have been added by anyone: the
        creator is the answer only when the whole history was read."""
        card = _card("DRE-21", added_by=None, creator=FLEET, history_partial=True)
        row = _rows([card])[0][0]
        assert row["action"] == hwm.UNTOLD

    def test_a_card_with_no_creator_and_no_entry_could_not_tell(self):
        card = _card("DRE-22", added_by=None, creator=None)
        assert _rows([card])[0][0]["action"] == hwm.UNTOLD


# --------------------------------------------------------------------------
# 2: the class, and the action it earns
# --------------------------------------------------------------------------
class TestTheCensus:
    def test_each_card_gets_the_class_and_action_the_card_names(self):
        got = {r["identifier"]: (r["class"], r["action"], r["move_to"])
               for r in _rows()[0]}
        assert got == {
            "DRE-1": (hwm.EPIC, hwm.SWAP, None),
            "DRE-2": (hwm.PROOF, hwm.REMOVE, None),
            "DRE-3": (hwm.OPERATOR, hwm.SWAP, None),
            "DRE-4": (hwm.OPERATOR, hwm.SWAP, None),
            "DRE-5": (hwm.CODE, hwm.RESTAMP, "Backlog"),
            "DRE-6": (hwm.CODE, hwm.RESTAMP, None),
            "DRE-7": (hwm.CODE, hwm.REPLAN, "Planning"),
            "DRE-8": (hwm.CODE, hwm.REMOVE, None),
            "DRE-9": (hwm.CODE, hwm.REMOVE, None),
            "DRE-10": (None, hwm.KEEP, None),
            "DRE-11": (hwm.CODE, hwm.REMOVE, None),
            "DRE-12": (None, hwm.UNTOLD, None),
        }

    def test_the_epic_comes_before_the_proof_and_the_operator_step(self):
        card = _card("DRE-23", title="[EPIC] PROOF: odd", labels=(HAND_BUILT, NO_CODE))
        assert _rows([card])[0][0]["class"] == hwm.EPIC

    def test_a_proof_keeps_no_code(self):
        """A proof is classed before an operator step, though it carries
        `no-code`: it loses the mark and nothing else."""
        assert _by_id(_rows()[0])["DRE-2"]["class"] == hwm.PROOF

    @pytest.mark.parametrize("title,labels", [
        ("plain title", (HAND_BUILT, "needs-human", NO_CODE)),
        ("OPERATOR: rotate the key", (HAND_BUILT,)),
        ("  [operator] rotate", (HAND_BUILT,)),
        ("SIGN-OFF (OPERATOR): the deploy", (HAND_BUILT,)),
    ])
    def test_what_makes_an_operator_step(self, title, labels):
        card = _card("DRE-24", title=title, labels=labels)
        assert _rows([card])[0][0]["class"] == hwm.OPERATOR

    def test_an_operator_word_mid_title_is_not_anchored(self):
        card = _card("DRE-25", title="Document the [OPERATOR] runbook", lane="Intake")
        assert _rows([card])[0][0]["class"] == hwm.CODE

    def test_the_standing_cards_that_only_receive_posts_are_operator_steps(self):
        hygiene = _card("DRE-26", title="hourly hygiene summary",
                        labels=(HAND_BUILT, NO_CODE), lane="Backlog")
        groomer = _card("DRE-27", title="[OPERATOR] daily groomer proposals",
                        labels=(HAND_BUILT,), lane="Backlog")
        got = _by_id(_rows([hygiene, groomer])[0])
        assert got["DRE-26"]["action"] == got["DRE-27"]["action"] == hwm.SWAP

    def test_the_rendering_groups_by_action_with_counts(self):
        text = hwm.render(_rows()[0])
        for heading in ("label swapped for operator-step (3):", "label removed (4):",
                        "restamped FLEET (2):", "moved to Planning (1):",
                        "kept — applied by hand (1):", "could not tell — left alone (1):"):
            assert heading in text, heading
        assert "DRE-7" in text and f"by {TOOLS}" in text
        assert "12 card(s)" in text


# --------------------------------------------------------------------------
# 3: a critic pass on record, read with plan_critic's own reader
# --------------------------------------------------------------------------
class TestCriticPass:
    def test_a_one_off_pass_on_the_card_counts(self):
        assert _by_id(_rows()[0])["DRE-5"]["passed"]

    def test_a_newer_send_back_cancels_the_pass(self):
        card = _card("DRE-30", comments=[
            _marker(plan_critic.STAGE_ONE_OFF, plan_critic.PASS, "2026-09-03T00:00:00.000Z"),
            _marker(plan_critic.STAGE_ONE_OFF, plan_critic.SEND_BACK, "2026-09-04T00:00:00.000Z")])
        row = _rows([card])[0][0]
        assert row["passed"] is None and row["action"] == hwm.REPLAN

    def test_a_marker_a_person_wrote_is_no_pass(self):
        card = _card("DRE-31", comments=[
            _marker(plan_critic.STAGE_ONE_OFF, plan_critic.PASS, who=PERSON)])
        assert _rows([card])[0][0]["action"] == hwm.REPLAN

    def test_a_marker_the_operator_tools_wrote_is_no_pass(self):
        """Only the fleet's own records are the critic's — whatever key the
        operator runs this under."""
        card = _card("DRE-32", comments=[
            _marker(plan_critic.STAGE_ONE_OFF, plan_critic.PASS, who=TOOLS)])
        assert _rows([card])[0][0]["action"] == hwm.REPLAN

    def test_a_child_reads_its_epics_release_not_its_own_thread(self):
        child = _card("DRE-33", lane="Backlog",
                      parent={"identifier": "DRE-901", "state": {"name": "In Progress"}},
                      comments=[_marker(plan_critic.STAGE_ONE_OFF, plan_critic.PASS)])
        threads = {"DRE-901": [_marker(plan_critic.STAGE_POST, plan_critic.SEND_BACK)]}
        row = _rows([child], threads)[0][0]
        assert row["passed"] is None and row["action"] == hwm.REPLAN
        assert row["move_to"] == "Planning"

    def test_a_released_epic_passes_its_child(self):
        assert _by_id(_rows()[0])["DRE-6"]["passed"]


# --------------------------------------------------------------------------
# 4: dry by default; --apply makes exactly the writes stated
# --------------------------------------------------------------------------
class TestTheRun:
    def test_a_dry_run_writes_nothing(self):
        rows, ops = _rows()
        stamps = Stamps()
        with mock.patch.object(hwm.routing_verdict, "stamp_card", stamps):
            result = hwm.run(ops, rows, apply=False)
        assert ops.writes() == [] and stamps.calls == []
        assert result["applied"] is False

    def test_apply_touches_exactly_the_automatic_cards(self):
        ops, stamps, _ = _apply()
        assert _touched(ops, stamps) == {f"DRE-{n}" for n in range(1, 12)} - {"DRE-10"}

    def test_every_automatic_card_loses_hand_built_once(self):
        ops, _, _ = _apply()
        assert sorted(ops.removed) == sorted(
            (f"DRE-{n}", HAND_BUILT) for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 11))

    def test_the_epic_and_both_operator_steps_swap_in_place(self):
        ops, stamps, _ = _apply()
        assert sorted(ops.added) == [("DRE-1", OPERATOR_STEP), ("DRE-3", OPERATOR_STEP),
                                     ("DRE-4", OPERATOR_STEP)]
        moved = {i for i, *_ in ops.states}
        assert not moved & {"DRE-1", "DRE-3", "DRE-4"}

    def test_the_proof_loses_the_label_only(self):
        ops, stamps, _ = _apply()
        assert [w for w in ops.removed if w[0] == "DRE-2"] == [("DRE-2", HAND_BUILT)]
        assert not [w for w in ops.added + ops.states if w[0] == "DRE-2"]
        assert not [c for c in stamps.calls if c[0] == "DRE-2"]

    def test_the_passed_code_cards_are_retired_and_restamped_fleet(self):
        ops, stamps, _ = _apply()
        assert sorted(c[:2] for c in stamps.calls) == [("DRE-5", "FLEET"), ("DRE-6", "FLEET")]
        for ident in ("DRE-5", "DRE-6"):
            notes = [b for i, b in ops.comments
                     if i == ident and b.startswith("🪦 verdict-retired:")]
            assert len(notes) == 1, ident
            assert "WORKBENCH" in notes[0]
            old = next(c for c in BOARD if c["identifier"] == ident)
            bodies = [n["body"] for n in linear_ops.window_nodes(old["comments"])]
            # The old verdict no longer routes the card once the note stands.
            assert routing_verdict.verdicts_on(bodies + notes) == ()

    def test_the_retirement_note_says_the_migration_retired_it_not_planning(self):
        # The planning exit's note says the card re-entered Planning and that
        # Planning's exit retired the verdict; neither happened here.
        ops, _, _ = _apply([ONE_OFF_PASSED])
        note = next(b for _, b in ops.comments if b.startswith("🪦 verdict-retired:"))
        assert "hand-built migration" in note
        assert "re-entered Planning" not in note
        assert "Planning's exit" not in note
        assert "the verdict Planning writes next" not in note
        assert "the one-off critic passed it" in note
        old = linear_ops.window_nodes(ONE_OFF_PASSED["comments"])[-1]["body"]
        assert f"`retired:{routing_verdict.fingerprint(old)}`" in note
        # Nothing comes off a card whose retired verdict put nothing on, so the
        # note says nothing came off.
        assert OPERATOR_STEP not in note

    def test_a_retired_operator_verdicts_operator_step_comes_off_before_the_note(self):
        # A code card the sweep once marked OPERATOR: left with `operator-step`
        # after the FLEET stamp, the sweep would read it as a person's and
        # nothing would build it.
        card = _card("DRE-13", title="wire the new flag", labels=(HAND_BUILT, OPERATOR_STEP),
                     comments=[_marker(plan_critic.STAGE_ONE_OFF, plan_critic.PASS),
                               _verdict("OPERATOR")])
        rows, _ = _rows([card])
        assert rows[0]["class"] == "code" and rows[0]["action"] == "restamp"
        ops, stamps, _ = _apply([card])
        assert ops.removed == [("DRE-13", HAND_BUILT), ("DRE-13", OPERATOR_STEP)]
        kinds = [(kind, body if kind != "comment" else body.split(":")[0])
                 for kind, _, body in ops.log]
        assert kinds.index(("remove", OPERATOR_STEP)) < kinds.index(
            ("comment", "🪦 verdict-retired"))
        note = next(b for _, b in ops.comments if b.startswith("🪦 verdict-retired:"))
        assert f"`{OPERATOR_STEP}` the old verdict put on came off" in note
        migration = ops.comments[-1][1]
        assert f"label removed (`{OPERATOR_STEP}`)" in migration
        assert stamps.calls[0][:2] == ("DRE-13", "FLEET")

    def test_a_label_the_card_does_not_carry_is_not_removed(self):
        card = _card("DRE-14", title="wire the other flag",
                     comments=[_marker(plan_critic.STAGE_ONE_OFF, plan_critic.PASS),
                               _verdict("OPERATOR")])
        ops, _, _ = _apply([card])
        assert ops.removed == [("DRE-14", HAND_BUILT)]

    def test_a_failed_write_says_what_was_already_written(self, capsys):
        rows, ops = _rows([CODE_UNPASSED])
        ops.state_error = linear_ops.LinearError("linear error: 502")
        result = hwm.run(ops, rows, apply=True)
        assert result["failed"] == ["DRE-7"]
        err = capsys.readouterr().err
        assert "FAILED DRE-7" in err
        assert f"already written: label removed (`{HAND_BUILT}`)" in err
        assert "a re-run will not find it" in err

    def test_the_fleet_why_names_the_migration_and_the_pass(self):
        _, stamps, _ = _apply()
        whys = {i: why for i, _, why in stamps.calls}
        for ident in ("DRE-5", "DRE-6"):
            assert whys[ident].startswith("hand-built migration:")
        assert "one-off" in whys["DRE-5"]
        assert "DRE-900" in whys["DRE-6"]

    def test_only_the_hand_work_passed_card_moves_to_backlog(self):
        ops, _, _ = _apply()
        assert ("DRE-5", "Backlog", {"expect": ("Hand-work",)}) in ops.states
        assert not [s for s in ops.states if s[0] == "DRE-6"]

    def test_the_unpassed_hand_work_card_moves_to_planning(self):
        ops, stamps, _ = _apply()
        assert ("DRE-7", "Planning", {"expect": ("Hand-work",)}) in ops.states
        assert not [c for c in stamps.calls if c[0] == "DRE-7"]

    def test_the_only_moves_are_those_two(self):
        ops, _, _ = _apply()
        assert sorted((i, s) for i, s, _ in ops.states) == [("DRE-5", "Backlog"),
                                                             ("DRE-7", "Planning")]

    def test_no_write_names_todo(self):
        ops, _, _ = _apply()
        for _, state, kw in ops.states:
            assert state != "Todo"
            assert "Todo" not in (kw.get("expect") or ())

    def test_the_person_and_no_actor_cards_are_untouched(self):
        ops, stamps, _ = _apply()
        assert not _touched(ops, stamps) & {"DRE-10", "DRE-12"}

    def test_every_changed_card_carries_one_migration_comment(self):
        ops, _, result = _apply()
        notes = [(i, b) for i, b in ops.comments if b.startswith("🧳 hand-built-migration:")]
        assert sorted(i for i, _ in notes) == sorted(
            f"DRE-{n}" for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 11))
        for _, body in notes:
            assert "Build it and let everyone know when i say built it" in body
            assert "label removed" in body
        by_id = dict(notes)
        assert "label added" in by_id["DRE-1"] and "label added" in by_id["DRE-3"]
        assert "verdict restamped" in by_id["DRE-5"] and "moved to Backlog" in by_id["DRE-5"]
        assert "verdict restamped" in by_id["DRE-6"] and "moved to Backlog" not in by_id["DRE-6"]
        assert "moved to Planning" in by_id["DRE-7"]
        assert sorted(result["changed"]) == sorted(i for i, _ in notes)

    def test_the_migration_comment_comes_last(self):
        ops, _, _ = _apply([ONE_OFF_PASSED])
        assert ops.comments[-1][1].startswith("🧳 hand-built-migration:")

    def test_a_card_that_moved_since_the_read_is_refused_untouched(self):
        rows, ops = _rows([CODE_UNPASSED, CODE_INTAKE])
        ops.lanes["DRE-7"] = "In Progress"
        with mock.patch.object(hwm.routing_verdict, "stamp_card", Stamps()):
            result = hwm.run(ops, rows, apply=True)
        assert result["refused"] == ["DRE-7"]
        assert not [w for w in ops.writes() if w[0] == "DRE-7"]
        assert ("DRE-8", HAND_BUILT) in ops.removed

    def test_a_refused_stamp_moves_nothing(self):
        ops, _, result = _apply([ONE_OFF_PASSED], stamps=Stamps(result=1))
        assert ops.states == []
        assert "DRE-5" in result["refused"]


# --------------------------------------------------------------------------
# 5: the switches, and the refusals
# --------------------------------------------------------------------------
class TestTheSwitches:
    def _main(self, argv, cards=BOARD, threads=THREADS, unreadable=False):
        ops = FakeOps(cards, threads, unreadable=unreadable)
        stamps = Stamps()
        with mock.patch.object(hwm, "linear_ops", ops), \
                mock.patch.object(hwm.routing_verdict, "stamp_card", stamps):
            code = hwm.main(argv)
        return code, ops, stamps

    def test_run_without_apply_prints_every_card_and_writes_nothing(self, capsys):
        code, ops, stamps = self._main(["run"])
        out = capsys.readouterr().out
        assert code == 0 and ops.writes() == [] and stamps.calls == []
        for n in range(1, 13):
            assert f"DRE-{n} " in out, n
        assert "dry run — nothing was written" in out
        assert "restamped FLEET" in out and "moved to Planning" in out

    def test_census_writes_nothing(self):
        code, ops, stamps = self._main(["census"])
        assert code == 0 and ops.writes() == [] and stamps.calls == []

    def test_include_migrates_a_named_persons_card(self):
        code, ops, _ = self._main(["run", "--apply", "--include", "dre-10"])
        assert code == 0
        assert ("DRE-10", HAND_BUILT) in ops.removed

    def test_include_actor_migrates_every_card_that_account_applied(self):
        other = _card("DRE-13", title="another of theirs", lane="In Review",
                      added_by=PERSON, creator=PERSON)
        code, ops, _ = self._main(["run", "--apply", "--include-actor", PERSON],
                                  cards=BOARD + [other])
        assert code == 0
        assert {("DRE-10", HAND_BUILT), ("DRE-13", HAND_BUILT)} <= set(ops.removed)
        assert not [w for w in ops.writes() if w[0] == "DRE-12"]

    def test_operator_step_classes_a_named_code_card(self):
        code, ops, _ = self._main(["run", "--apply", "--operator-step", "DRE-7"])
        assert code == 0
        assert ("DRE-7", OPERATOR_STEP) in ops.added
        assert not [s for s in ops.states if s[0] == "DRE-7"]

    def test_there_is_no_switch_that_keeps_an_automatic_card_out(self):
        with pytest.raises(SystemExit):
            self._main(["run", "--exclude", "DRE-5"])

    def test_unreadable_linear_exits_non_zero_before_any_write(self, capsys):
        code, ops, stamps = self._main(["run", "--apply"], unreadable=True)
        assert code != 0
        assert ops.writes() == [] and stamps.calls == []
        assert "nothing was written" in capsys.readouterr().err

    def test_an_unreadable_epic_thread_exits_before_any_write(self, capsys):
        class Flaky(FakeOps):
            def gql(self, query, variables=None):
                if "comments" in query:
                    raise linear_ops.LinearError("linear error: 502")
                return super().gql(query, variables)

        ops = Flaky(BOARD, THREADS)
        with mock.patch.object(hwm, "linear_ops", ops), \
                mock.patch.object(hwm.routing_verdict, "stamp_card", Stamps()):
            assert hwm.main(["run", "--apply"]) != 0
        assert ops.writes() == []

    def test_a_typo_is_refused_before_anything_is_read(self):
        code, ops, _ = self._main(["run", "--include", "1234"], unreadable=True)
        assert code == 2

    def test_a_named_card_not_in_the_population_is_said(self, capsys):
        self._main(["run", "--include", "DRE-99"])
        assert "DRE-99" in capsys.readouterr().out


# --------------------------------------------------------------------------
# 6: declared, registered, and no allowlist
# --------------------------------------------------------------------------
SOURCE = (ROOT / "scripts" / "hand_work_migration.py").read_text()


class TestDeclared:
    def test_no_card_id_appears_in_its_code(self):
        assert backlog_cutover.card_ids_in_code(SOURCE) == []

    def test_the_labels_come_from_the_vocabulary(self):
        assert '"hand-built"' not in SOURCE and '"operator-step"' not in SOURCE
        assert hwm.HAND_BUILT == HAND_BUILT and hwm.OPERATOR_STEP == OPERATOR_STEP

    def test_its_writes_are_backlog_and_planning_and_never_todo(self):
        mine = [w for w in ready_lane_writers.writes()
                if w.writer == "hand_work_migration.py"]
        assert {w.lane for w in mine} == {"Backlog", "Planning"}
        assert ready_lane_writers.writer_problems() == []

    def test_its_comment_sites_are_in_the_act_registry(self):
        assert pipeline_act.main(["check"]) == 0
        rows = [r for r in pipeline_act.load()["unconverted"]
                if r.get("file") == "scripts/hand_work_migration.py"]
        assert len(rows) == 2
        assert {r["kind"] for r in rows} == {"not-an-act"}
        for row in rows:
            assert SOURCE.count(row["anchor"]) == 1, row["anchor"]
        assert any("🧳 hand-built-migration:" in r["means"] for r in rows)

    def test_the_opener_is_the_contracts(self):
        assert hwm.OPENER == "🧳 hand-built-migration:"


# --------------------------------------------------------------------------
# 7: the record
# --------------------------------------------------------------------------
class TestTheDoc:
    DOC = ROOT / "docs" / "hand-built-migration.md"
    OLD = ROOT / "docs" / "hand-work-migration.md"

    def test_it_says_how_to_run_it_and_what_each_class_gets(self):
        text = self.DOC.read_text()
        for phrase in ("hand_work_migration.py census", "hand_work_migration.py run",
                       "run --apply", "--include DRE-N", "--include-actor NAME",
                       "--operator-step DRE-N", "🧳 hand-built-migration:",
                       "operator-step", "config/linear-identities.json",
                       "🪦 verdict-retired", "Planning", "Backlog", "2026-10-07"):
            assert phrase in text, phrase

    def test_the_first_migrations_record_points_at_the_rewrite(self):
        text = self.OLD.read_text()
        assert "hand-built-migration.md" in text.split("## Before the run", 1)[0]
        for phrase in ("DRE-3621", "15:24 PT", "DRE-5349"):
            assert phrase in text, phrase
