"""The four readers of a hold honor its reason, not the bare label (DRE-6182).

Before this card `reconcile.held()`, `reconcile.card_parked_for_human` (which
`fix_dispatch_blocked` asks), `medic_retry.park_reason` and
`limit_recovery._held` each asked "is the label on the card" and could only
answer yes or no to the label — and the sweep's live re-read before a
promotion, `reconcile.live_promotion_refusal`, asked it a fifth time. Every
writer now stamps its reason (epic DRE-6172), so each of them asks
`hold.respects(labels, bodies, <reader>)` instead, and `config/holds.json`'s
`readers` is where a reader's exception is declared and reviewed.

WHAT THESE TESTS PIN.

  * The reader string each call site passes — `sweep`, `sweep`,
    `fix-dispatch`, `medic`, `limit-recovery` — with `hold.respects` patched.
  * That the answer comes from the registry: a fixture registry whose
    `review-cap-spent` row leaves out `fix-dispatch` lets the fix loop's
    dispatch through while the sweep still holds; the real one holds both.
  * Fail closed: a stamp whose reason the registry does not know, and the
    label with no stamp, are held for every reader — even under a registry in
    which no row names any reader.
  * A spent stamp is no stamp: one fixture per retiring line (`🔓 hold
    lifted:`, `hyg-hold-cleared`, `dead-run-budget-reset`), each held for all
    four and worded by the medic as a person's hold; a fresh stamp after the
    retiring line is read by that stamp.
  * The five label-alone reads the card leaves as they are
    (`dedupe_dispatch`, `limit_death_record`, `proof_dispatch`,
    `hygiene_triage`, `linear_ops`) keep reading the label alone.

Run: python3 -m pytest tests/test_hold_readers.py -v
"""
from __future__ import annotations

import ast
import copy
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import dead_run  # noqa: E402
import hold  # noqa: E402
import limit_recovery  # noqa: E402
import linear_ops  # noqa: E402
import medic_retry  # noqa: E402
import reconcile  # noqa: E402

LABEL = hold.HOLD_LABEL
SHA = "c" * 40
RUN_STARTED_AT = "2026-10-08T09:00:00Z"
LIVE_HOLD_REFUSAL = f"it carries '{LABEL}' now"


def _stamp(reason, at="none", by="scripts/reconcile.py"):
    return hold.stamp_line(reason, at, by)


REVIEW_CAP_STAMP = _stamp("review-cap-spent", SHA, "scripts/merge_gate.py")
UNKNOWN_STAMP = "🔒 hold: reason=not-a-reason at=none lifts=manual by=scripts/elsewhere.py"

# The three lines that spend every stamp older than them (DRE-6173).
LIFTED = hold.lift_line("dead-run-cap", "unpark-marker", "scripts/hold.py")
HYG_CLEARED = "🧹 the hold came off\n\n`hyg-hold-cleared` reason=dead-run-cap because=unpark-marker"
RESET = f"♻️ {dead_run.RESET_TAG}: un-parked by a human"
RETIRING = pytest.mark.parametrize(
    "retiring", [LIFTED, HYG_CLEARED, RESET],
    ids=["lift-line", "hyg-hold-cleared", "budget-reset"])


# --------------------------------------------------------------------------- #
# the card as each reader holds it                                             #
# --------------------------------------------------------------------------- #


def _window(bodies):
    """An inline comment window as Linear answers it: NEWEST first."""
    return {"nodes": [{"body": b, "createdAt": "2026-10-08T10:00:00Z"}
                      for b in reversed(list(bodies))]}


def _card(labels, bodies, state="In Review"):
    return {
        "identifier": "DRE-9182",
        "state": {"name": state},
        "labels": {"nodes": [{"name": name} for name in labels]},
        "comments": _window(bodies),
    }


def _sweep(labels, bodies):
    return reconcile.held(_card(labels, bodies))


def _sweep_live(labels, bodies):
    """The sweep's live re-read before a promotion, of the same card."""
    board = _card(labels, bodies, state="Backlog")
    live = copy.deepcopy(board)
    with mock.patch.object(reconcile, "_fetch_backlog_linear", return_value=[live]), \
            mock.patch.object(reconcile, "card_is_epic", return_value=False):
        _, refusal = reconcile.live_promotion_refusal(board, bodies)
    return refusal == LIVE_HOLD_REFUSAL


def _fix_dispatch(labels, bodies):
    issue = _card(labels, bodies)
    with mock.patch.object(linear_ops, "gql", return_value={"issue": issue}):
        return reconcile.card_parked_for_human("DRE-9182")


def _medic_reason(labels, bodies):
    return medic_retry.park_reason(
        state="In Review",
        labels=list(labels),
        receipts=[{"body": b, "created_at": "2026-10-08T10:00:00Z"} for b in bodies],
        run_started_at=RUN_STARTED_AT,
    )


def _medic(labels, bodies):
    return bool(_medic_reason(labels, bodies))


def _limit_recovery(labels, bodies):
    return limit_recovery._held(_card(labels, bodies))


READERS = {
    "sweep": _sweep,
    "sweep (live re-read)": _sweep_live,
    "fix-dispatch": _fix_dispatch,
    "medic": _medic,
    "limit-recovery": _limit_recovery,
}


def _answers(labels, bodies):
    return {name: ask(labels, bodies) for name, ask in READERS.items()}


def _registry_without(reader, reason):
    """The real registry, with every row carrying `reason` naming every
    reader but `reader`."""
    doc = copy.deepcopy(hold.load())
    rows = [row for row in doc["sites"]
            if any(e.get("reason") == reason for e in row.get("reasons") or [])]
    assert rows, f"the real registry has no row carrying {reason}"
    for row in rows:
        row["readers"] = [r for r in hold.READERS if r != reader]
    return doc


def _registry_naming_no_reader():
    doc = copy.deepcopy(hold.load())
    for row in doc["sites"]:
        row["readers"] = []
    return doc


def _with_registry(doc):
    return mock.patch.object(hold, "load", lambda path=None: doc)


# --------------------------------------------------------------------------- #
# 1. each reader asks hold.respects with its own name                          #
# --------------------------------------------------------------------------- #


class TestEachReaderPassesItsName:
    LABELS = ["repo:bureau-pipeline", LABEL]
    BODIES = ["🧠 a run started", REVIEW_CAP_STAMP]

    def _asked(self, ask, answer=True):
        with mock.patch.object(hold, "respects", return_value=answer) as respects:
            result = ask(self.LABELS, self.BODIES)
        assert respects.called, "the reader did not ask hold.respects"
        return result, respects.call_args_list

    @pytest.mark.parametrize("name,ask,reader", [
        ("held", _sweep, "sweep"),
        ("live_promotion_refusal", _sweep_live, "sweep"),
        ("card_parked_for_human", _fix_dispatch, "fix-dispatch"),
        ("park_reason", _medic, "medic"),
        ("_held", _limit_recovery, "limit-recovery"),
    ])
    def test_the_reader_string(self, name, ask, reader):
        _, calls = self._asked(ask)
        for call in calls:
            args = list(call.args) + [call.kwargs.get(k) for k in ("labels", "bodies", "reader")
                                      if k in call.kwargs]
            assert args[2] == reader, f"{name} asked as {args[2]!r}, not {reader!r}"
            names = hold._label_names(args[0])
            assert [n.lower() for n in names].count(LABEL) == 1, names
            assert list(args[1]) == self.BODIES, (
                f"{name} handed hold.respects {list(args[1])!r} — the card's "
                "comment window oldest→newest is what read_stamp reads")

    @pytest.mark.parametrize("ask", [_sweep, _sweep_live, _fix_dispatch, _medic,
                                     _limit_recovery])
    def test_the_answer_is_the_one_respects_gives(self, ask):
        held, _ = self._asked(ask, answer=True)
        assert held is True
        not_held, _ = self._asked(ask, answer=False)
        assert not_held is False

    def test_card_parked_for_human_reads_the_card_once(self):
        issue = _card(self.LABELS, self.BODIES)
        with mock.patch.object(linear_ops, "gql", return_value={"issue": issue}) as gql:
            reconcile.card_parked_for_human("DRE-9182")
        assert gql.call_count == 1
        assert "comments(" in gql.call_args.args[0], (
            "the comment window rides on the same read as the labels")

    def test_the_lane_half_of_the_fix_dispatch_gate_is_unchanged(self):
        for lane in reconcile.PARKED_STATES:
            issue = _card(["repo:bureau-pipeline"], [], state=lane)
            with mock.patch.object(linear_ops, "gql", return_value={"issue": issue}):
                assert reconcile.card_parked_for_human("DRE-9182") is True


# --------------------------------------------------------------------------- #
# 2. the registry decides                                                      #
# --------------------------------------------------------------------------- #


class TestTheRegistryDecides:
    LABELS = ["repo:bureau-pipeline", LABEL]
    BODIES = ["🧠 a run started", REVIEW_CAP_STAMP, "a person talks"]

    def test_a_row_without_fix_dispatch_lets_the_fix_loop_through(self):
        with _with_registry(_registry_without("fix-dispatch", "review-cap-spent")):
            answers = _answers(self.LABELS, self.BODIES)
        assert answers == {
            "sweep": True, "sweep (live re-read)": True, "fix-dispatch": False,
            "medic": True, "limit-recovery": True,
        }

    @pytest.mark.parametrize("reader", hold.READERS)
    def test_a_row_without_a_reader_lets_that_reader_through(self, reader):
        with _with_registry(_registry_without(reader, "review-cap-spent")):
            answers = _answers(self.LABELS, self.BODIES)
        for name, answer in answers.items():
            assert answer is (not name.startswith(reader)), (name, answers)

    def test_the_real_registry_holds_every_reader(self):
        assert all(_answers(self.LABELS, self.BODIES).values())

    def test_no_label_is_not_held_whatever_the_stamp_says(self):
        answers = _answers(["repo:bureau-pipeline"], self.BODIES)
        assert not any(answers.values()), answers


# --------------------------------------------------------------------------- #
# 3. fail closed                                                               #
# --------------------------------------------------------------------------- #


class TestFailClosed:
    LABELS = ["repo:bureau-pipeline", LABEL]

    @pytest.mark.parametrize("bodies", [
        ["🧠 a run started", UNKNOWN_STAMP],
        ["🧠 a run started", "a person talks"],
        [],
    ], ids=["unknown-reason", "no-stamp", "no-comments"])
    def test_held_for_all_four(self, bodies):
        assert all(_answers(self.LABELS, bodies).values())

    @pytest.mark.parametrize("bodies", [
        ["🧠 a run started", UNKNOWN_STAMP],
        ["🧠 a run started", "a person talks"],
    ], ids=["unknown-reason", "no-stamp"])
    def test_held_for_all_four_even_when_no_row_names_a_reader(self, bodies):
        with _with_registry(_registry_naming_no_reader()):
            assert all(_answers(self.LABELS, bodies).values())

    def test_the_medic_words_an_unknown_reason_as_it_is_stamped(self):
        reason = _medic_reason(self.LABELS, ["🧠 a run started", UNKNOWN_STAMP])
        assert reason == (f"the '{LABEL}' label is on it — reason not-a-reason, "
                          "lifts when manual")

    def test_the_medic_words_a_label_with_no_stamp_as_a_persons_hold(self):
        reason = _medic_reason(self.LABELS, ["a person talks"])
        assert reason == f"the '{LABEL}' label is on it — reason manual, lifts when manual"

    def test_the_medic_words_a_stamped_reason(self):
        reason = _medic_reason(self.LABELS, [REVIEW_CAP_STAMP])
        assert reason == (f"the '{LABEL}' label is on it — reason review-cap-spent, "
                          "lifts when new-head")


# --------------------------------------------------------------------------- #
# 4. a spent stamp is no stamp                                                 #
# --------------------------------------------------------------------------- #


class TestASpentStampIsNoStamp:
    LABELS = ["repo:bureau-pipeline", LABEL]
    SPENT = "dead-run-cap"

    def _spent(self, retiring):
        return ["🧠 a run started", _stamp(self.SPENT), "a person talks", retiring,
                "the label went back on by hand"]

    @RETIRING
    def test_held_for_all_four_readers(self, retiring):
        rows = [row for row in hold.load()["sites"]
                if any(e.get("reason") == self.SPENT for e in row.get("reasons") or [])]
        assert rows and all(set(row["readers"]) == set(hold.READERS) for row in rows), (
            "the stamp's row should name every reader, so only the spend can explain "
            "the answer below")
        assert all(_answers(self.LABELS, self._spent(retiring)).values())

    @RETIRING
    def test_held_even_where_the_stamped_reason_names_no_reader(self, retiring):
        """Read as the stamped reason the card would walk past every reader;
        read as the person's hold it is, it stops all four."""
        with _with_registry(_registry_naming_no_reader()):
            assert not any(_answers(self.LABELS, [_stamp(self.SPENT)]).values())
            assert all(_answers(self.LABELS, self._spent(retiring)).values())

    @RETIRING
    def test_the_medic_words_it_as_a_persons_hold(self, retiring):
        reason = _medic_reason(self.LABELS, self._spent(retiring))
        assert "reason manual" in reason
        assert self.SPENT not in reason

    @RETIRING
    def test_a_fresh_stamp_after_the_retiring_line_is_read_by_that_stamp(self, retiring):
        bodies = self._spent(retiring) + [REVIEW_CAP_STAMP]
        reason = _medic_reason(self.LABELS, bodies)
        assert "reason review-cap-spent" in reason
        assert all(_answers(self.LABELS, bodies).values())
        with _with_registry(_registry_without("fix-dispatch", "review-cap-spent")):
            answers = _answers(self.LABELS, bodies)
        assert answers["fix-dispatch"] is False
        assert answers["sweep"] is True


# --------------------------------------------------------------------------- #
# 5. the medic's rule 3 and the sweep's live read                              #
# --------------------------------------------------------------------------- #


class TestTheRestOfParkReasonStands:
    def test_a_reader_let_through_falls_to_the_lane_rules(self):
        """Rule 1 standing down is not the medic retrying: the dead-run
        hand-off to the planner still answers (DRE-6178)."""
        receipts = [{"body": f"{hold.DEAD_SPLIT_MARK} handed over",
                     "created_at": "2026-10-08T10:00:00Z"}]
        with mock.patch.object(hold, "respects", return_value=False):
            reason = medic_retry.park_reason(
                state="Planning", labels=[LABEL], receipts=receipts,
                run_started_at=RUN_STARTED_AT)
        assert "handed to the planner" in reason

    def test_the_live_read_asks_its_own_bodies(self):
        """The live re-read reads the live card's window, never the board's."""
        board = _card([LABEL], [], state="Backlog")
        live = _card([LABEL], [REVIEW_CAP_STAMP], state="Backlog")
        with mock.patch.object(reconcile, "_fetch_backlog_linear", return_value=[live]), \
                mock.patch.object(reconcile, "card_is_epic", return_value=False), \
                mock.patch.object(hold, "respects", return_value=True) as respects:
            reconcile.live_promotion_refusal(board, [])
        assert list(respects.call_args.args[1]) == [REVIEW_CAP_STAMP]


# --------------------------------------------------------------------------- #
# 6. the label-alone reads this card leaves alone                              #
# --------------------------------------------------------------------------- #

#: Each read of the bare label the card names and leaves unchanged, by file
#: and function: each refuses or reports on any hold, for every reason.
LABEL_ALONE = {
    "scripts/dedupe_dispatch.py": ("parked_for_a_person",),
    "scripts/limit_death_record.py": ("needs_a_person",),
    "scripts/proof_dispatch.py": ("first_run", "held_on_the_lane"),
    "scripts/hygiene_triage.py": ("left_for_a_person",),
    "scripts/linear_ops.py": ("cmd_state", "_held_state_move"),
}

#: `hygiene_triage` imports `hold` for its no-route row (DRE-6190, which
#: landed after this card was written): the holds lane's own reading of what
#: lifts a no-route hold, not a reader that stands down for one. The other
#: four import nothing of it.
IMPORTS_HOLD_FOR_ITS_OWN_LANE = {"scripts/hygiene_triage.py"}


def _tree(rel):
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _functions(tree, name):
    return [node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]


def _imports_hold(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == "hold" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "hold":
            return True
    return False


class TestTheLabelAloneReadsStay:
    @pytest.mark.parametrize("rel", sorted(LABEL_ALONE))
    def test_no_hold_import(self, rel):
        if rel in IMPORTS_HOLD_FOR_ITS_OWN_LANE:
            pytest.skip("imports hold for its own lane's no-route row (DRE-6190)")
        assert not _imports_hold(_tree(rel)), f"{rel} imports hold"

    @pytest.mark.parametrize("rel,name", [(rel, name) for rel, names in
                                          sorted(LABEL_ALONE.items()) for name in names])
    def test_the_function_reads_the_label_alone(self, rel, name):
        found = _functions(_tree(rel), name)
        assert found, f"{rel} has no function {name} — the list above is stale"
        for fn in found:
            asks = [node.attr for node in ast.walk(fn)
                    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == "hold"]
            assert not asks, f"{rel}:{name} asks the hold module ({asks})"
            text = ast.unparse(fn)
            assert hold.STAMP_PREFIX not in text, f"{rel}:{name} reads a stamp"
            assert "STAMP_PREFIX" not in text and "read_stamp" not in text, (
                f"{rel}:{name} reads a stamp")

    @pytest.mark.parametrize("rel", sorted(set(LABEL_ALONE) - IMPORTS_HOLD_FOR_ITS_OWN_LANE))
    def test_no_stamp_is_read_anywhere_in_the_file(self, rel):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert hold.STAMP_PREFIX not in text, f"{rel} spells the stamp prefix"
        assert "read_stamp" not in text and "hold.respects" not in text
