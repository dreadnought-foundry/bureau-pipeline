"""The six cards of 2026-10-10, replayed: none lands in Green Light (DRE-6655).

On 2026-10-10 six cards arrived in Green Light, the CEO's "needs you" queue,
and not one of them was his to decide. Each build card under the epic
DRE-6639 recorded the card it answers into
`tests/fixtures/green-light-2026-10-10/` and proved its one rule on it. This
module asks the epic's own question of every file there: as the card stood
when the rule read it, does that rule still put it in Green Light, and does
what it writes instead name who the card was really for.

The six, by their arrival in Green Light (PT):

  01:54  DRE-6042  review-cap    a PROOF record the critic approved and the
                                 gate declined as not proven, parked on the
                                 spent review cap — DRE-6650's rule.
  02:28  DRE-6364  review-cap    the same, with the APPROVE carried to the
                                 head across a branch update — DRE-6650's rule.
  02:58  DRE-6589  blocked-epic  an epic in Planning whose formal blocker
                                 DRE-6587 was In Progress, asked "wait, or
                                 plan now?" — DRE-6651's rule.
  03:08  DRE-6590  plan-bound    a one-off at the plan critic's second
                                 send-back, every finding a card defect,
                                 asked as a question — DRE-6649's rule.
  04:50  DRE-6617  plan-bound    the same bound on a second one-off —
                                 DRE-6649's rule.
  09:54  DRE-6636  switch-card   the switch-reason alarm for portico, read by
                                 the classifier as a decision — DRE-6652's
                                 filing and DRE-6654's classifier rule.

One card arrived later and is replayed beside them (DRE-7491). DRE-6673's
proof record found DRE-2498 in Green Light on 2026-10-13, from the one-off
plan critic's empty result on round 2 (run 38424453807): the CEO was asked to
re-run the review or settle the card, and the next read passed it. Its
arrival is `one-off-no-result`, and it was for the critic, which now reads
the card again on its own before anything goes to the CEO.

Every fixture is driven through the mechanism its `arrival` names, with the
siblings' own harnesses, and over every one of them no stubbed lane write —
`linear_ops.cmd_state` or `cmd_advance` — names `Green Light`. What the rule
writes instead is read off the mechanism, never the fixture, and must name
the fixture's `for` the way `NAMES` spells it: the bound's rewrite receipt
names the planner, the epic's wait reason says it is planned when its
blockers are Done, the re-run's receipt is the proof run's own, and the
switch card's routing verdict names the operator as its actor. DRE-6042 is
the one whose answer is not a fresh write: its proof run had spent its
re-runs, so what stands is that run's own `🔬 proof-waiting` hold, which
hands the reading to an operator. The test id is the card id, so a failure
reads as the card.

This module writes no fixture and edits no sibling's test file: it reads the
directory by glob, and the contract it checks each file against is the one
the epic's cards share.

Run: cd bureau-pipeline && python3 -m pytest tests/test_green_light_false_arrivals_2026_10_10.py -v
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import hold  # noqa: E402
import linear_ops  # noqa: E402
import plan_bound  # noqa: E402
import planning_classify  # noqa: E402
import planning_escalation  # noqa: E402
import planning_route  # noqa: E402
import planning_shape  # noqa: E402
import plan_critic  # noqa: E402
import proof_dispatch  # noqa: E402
import proof_record  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402
import spoken_thread  # noqa: E402
import test_planning_classify as classify_tests  # noqa: E402
import test_planning_route as route_tests  # noqa: E402
import test_proof_dispatch as dispatch_tests  # noqa: E402
import test_review_lane_nudge_cap as nudge_cap  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "green-light-2026-10-10"
GREEN_LIGHT = "Green Light"

#: The six cards the epic answers, and the arrival each one was.
SIX = {
    "DRE-6042": "review-cap",
    "DRE-6364": "review-cap",
    "DRE-6589": "blocked-epic",
    "DRE-6590": "plan-bound",
    "DRE-6617": "plan-bound",
    "DRE-6636": "switch-card",
}
#: A card from DRE-6673's proof record, not the night of 2026-10-10 (DRE-7491).
LATER = {"DRE-2498": "one-off-no-result"}

#: The shared schema: every key a fixture carries, and its type.
REQUIRED = {
    "card": str, "arrival": str, "for": str, "as_of": str,
    "lane": str, "labels": list, "comments": list,
}
COMMENT_KEYS = {"body", "authored_by_pipeline", "created_at"}
#: The two vocabularies, and who each arrival's card was really for.
ARRIVALS = {
    "plan-bound": "planner",
    "review-cap": "proof-run",
    "blocked-epic": "planner",
    "switch-card": "operator",
    "one-off-no-result": "critic",
}
FOR = {"planner", "proof-run", "operator", "critic"}
#: The per-arrival extras the contract names.
EXTRAS = {
    "review-cap": ("pr", "rebuilt"),
    "blocked-epic": ("relations",),
    "switch-card": ("filed",),
    "one-off-no-result": ("result", "run_id"),
}

#: How the text a rule writes instead of Green Light names each recipient,
#: in the words the pipeline itself writes: the planner, by name in the
#: rewrite receipt and as "planned" in the epic's wait; the proof run, by its
#: own `🔬 proof-` voice; the operator, as the actor of the routing verdict.
NAMES = {
    "planner": re.compile(r"\bplann(?:er|ed)\b"),
    "proof-run": re.compile(rf"^{linear_ops.PROOF_MARK} proof-(?:run|waiting):", re.M),
    "operator": re.compile(r"\*\*Who handles it there:\*\* operator\."),
    # Kept in step with the `rerun` note `plan_critic._cmd_decide` posts.
    "critic": re.compile(r"\bthe critic reads (?:this|the) card again\b"),
}

#: A row the gate's decline note quotes: “criterion…” reads “result…”.
QUOTED_ROW = re.compile(r"“([^”]*)” reads “([^”]*)”")


def _paths() -> list[Path]:
    return sorted(FIXTURES.glob("*.json"))


def _card_id(path: Path) -> str:
    return path.stem.upper()


# --------------------------------------------------------------------------- #
# the shared schema                                                            #
# --------------------------------------------------------------------------- #


def _shape_problems(path: Path, doc: dict) -> list[str]:
    """Every way `doc` departs from the shared contract, each naming the key."""
    problems = []
    for key, kind in REQUIRED.items():
        if key not in doc:
            problems.append(f"missing required key {key!r}")
        elif not isinstance(doc[key], kind):
            problems.append(f"{key!r} is {type(doc[key]).__name__}, not {kind.__name__}")
    if problems:
        return problems
    if doc["card"] != _card_id(path):
        problems.append(f"'card' is {doc['card']!r} in {path.name}")
    if doc["arrival"] not in ARRIVALS:
        problems.append(f"'arrival' {doc['arrival']!r} is outside {sorted(ARRIVALS)}")
    if doc["for"] not in FOR:
        problems.append(f"'for' {doc['for']!r} is outside {sorted(FOR)}")
    elif doc["arrival"] in ARRIVALS and doc["for"] != ARRIVALS[doc["arrival"]]:
        problems.append(f"'for' is {doc['for']!r}; a {doc['arrival']} card is "
                        f"for {ARRIVALS[doc['arrival']]!r}")
    if not doc["as_of"].endswith("Z"):
        problems.append(f"'as_of' {doc['as_of']!r} does not end in Z")
    if not all(isinstance(label, str) for label in doc["labels"]):
        problems.append("'labels' holds a label that is not a string")
    for n, row in enumerate(doc["comments"]):
        if not isinstance(row, dict) or set(row) != COMMENT_KEYS:
            problems.append(f"'comments'[{n}] is not a {sorted(COMMENT_KEYS)} row")
    for key in EXTRAS.get(doc["arrival"], ()):
        if key not in doc:
            problems.append(f"missing {doc['arrival']} key {key!r}")
    return problems


# --------------------------------------------------------------------------- #
# the lane writes, recorded                                                    #
# --------------------------------------------------------------------------- #


class _Lanes:
    """Every lane write a replay makes, as (call, card, destination).

    `linear_ops.cmd_state` and `cmd_advance` are stubbed for the whole test.
    A sibling harness that stubs them again inside its own context — the
    review sweep's `_Board`, the planning route's `_Card`, the classifier's
    `_Lops` — records into its own list, and each driver adds those here."""

    def __init__(self, monkeypatch):
        self.writes: list[tuple] = []
        monkeypatch.setattr(
            linear_ops, "cmd_state",
            lambda card, lane, *a, **k: self.writes.append(("cmd_state", card, lane)))
        monkeypatch.setattr(
            linear_ops, "cmd_advance",
            lambda card, to, *a, **k: self.writes.append(("cmd_advance", card, to)))

    def to_green_light(self) -> list[tuple]:
        return [w for w in self.writes if w[2] == GREEN_LIGHT]


# --------------------------------------------------------------------------- #
# plan-bound — DRE-6649                                                        #
# --------------------------------------------------------------------------- #


def _plan_bound(monkeypatch, doc: dict, lanes: _Lanes) -> str:
    """The bound's exit over the thread; returns the receipt it posts."""
    card, comments = doc["card"], doc["comments"]
    found = plan_bound.findings(comments, card, "one-off")
    assert found, f"{card}: the bound read no finding off its own thread"
    outcome, why = plan_bound.decide(found, plan_bound.granted(comments), "one-off")
    # The planner's own rewrite: neither the question to the CEO nor a park
    # in Triage for an operator.
    assert outcome == plan_bound.REWRITE, (
        f"{card}: the one-off bound answered {outcome!r} — {why}")
    assert plan_bound.decision_findings(found, "one-off") == []

    # The exit itself over the same thread. The escalation is stubbed to park
    # where the real one parks — the lane `planning_escalation.destination()`
    # names. That write goes through the stub alone, never `linear_ops`, so on
    # this path the guard is `asked == []`, not `to_green_light()`.
    asked: list = []

    def escalate(lops, ident, reason, *args, **kwargs):
        asked.append(ident)
        lops.cmd_state(ident, planning_escalation.destination())
        return planning_escalation.Outcome(parked=True, posted=True, stood_down=None)

    dispatched: list = []
    posted: list[str] = []
    monkeypatch.setattr(plan_bound.linear_ops, "comment_records",
                        lambda ident, *, whole_thread=False: list(comments))
    monkeypatch.setattr(plan_bound.linear_ops, "cmd_comment",
                        lambda ident, body, *flags: posted.append(body))
    monkeypatch.setattr(plan_bound.planning_escalation, "escalate", escalate)
    monkeypatch.setattr(plan_bound.review_rerun, "_cmd_dispatch",
                        lambda args: dispatched.append((args.epic, args.route)) or 0)
    plan_bound.run_exit(card, "one-off", "dreadnought-foundry/" + _repo_label(doc))
    assert asked == [], f"{card}: the bound asked the CEO"
    assert dispatched == [(card, plan_bound.DISPATCH_ROUTE)], (
        "the rewrite went back to the planner")
    receipts = [b for b in posted if b.startswith(plan_bound.RECEIPT_KEY)]
    assert len(receipts) == 1, f"{card}: the rewrite posted {posted!r}"
    return receipts[0]


def _repo_label(doc: dict) -> str:
    return next(label.split(":", 1)[1] for label in doc["labels"]
                if label.startswith("repo:"))


# --------------------------------------------------------------------------- #
# review-cap — DRE-6650                                                        #
# --------------------------------------------------------------------------- #


class _Board(nudge_cap._Board):
    """`test_review_lane_nudge_cap`'s board, remembering every lane its
    `cmd_advance` stub moved the card to — the stub is a closure inside
    `sweep`, and the lane it sets is the one trace it leaves."""

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, lane: str) -> None:
        if hasattr(self, "_state"):
            self.moved.append(lane)
        else:
            self.moved: list[str] = []
        self._state = lane


def _rows_the_note_names(pr: dict) -> list[tuple[str, str]]:
    """The rows the gate's newest decline note at the head quotes, as
    (criterion, result), keeping the ones that read `Not met.` — the rows a
    re-run re-observes. DRE-6364's note also quotes a row still waiting on its
    event; DRE-6488's wait owns that one, and `test_proof_dispatch` replays it."""
    note = f"⏸️ {reconcile.GATE_HOLD_NOTE_MARKER} @{pr['headRefOid']} "
    body = next(c["body"] for c in reversed(pr["comments"])
                if c["body"].startswith(note))
    rows = QUOTED_ROW.findall(body.split("\n", 1)[0])
    return [row for row in rows if row[1].startswith("Not met.")]


def _sweep(monkeypatch, doc: dict, lanes: _Lanes) -> None:
    """The review-lane branch of `reconcile.main()` over the card as the
    sweep read it at `as_of`: its labels, its thread, In Review, and its
    record pull request, with `verdict_bound` stubbed True."""
    pr = copy.deepcopy(doc["pr"])
    monkeypatch.setattr(nudge_cap, "IDENT", doc["card"])
    monkeypatch.setattr(reconcile, "REPO_SLUG", pr["url"].split("/")[4])
    monkeypatch.setattr(reconcile, "_door_sourced", set())
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    board = _Board()
    board.labels = list(doc["labels"])
    board.comments = [c["body"] for c in doc["comments"]]
    applied = MagicMock(wraps=hold.apply)
    with patch.object(reconcile, "verdict_bound", return_value=True), \
            patch.object(hold, "apply", applied):
        tag, _, _ = reconcile.review_standing(pr)
        spent = reconcile.review_nudges_spent(board.card(), tag, pr["headRefOid"])
        assert spent >= reconcile.REVIEW_NUDGE_CAP, (
            f"{doc['card']}: the replay is not at the spent cap ({spent})")
        nudge, cmd_state, posted = board.sweep(pr)
    lanes.writes += [("cmd_advance", doc["card"], lane) for lane in board.moved]
    lanes.writes += [("cmd_state",) + tuple(c.args[:2]) for c in cmd_state.call_args_list]
    applied.assert_not_called()
    nudge.assert_not_called()
    assert [b for b in posted if reconcile.REVIEW_NUDGE_CAP_KEY in b] == []
    assert board.state == reconcile.REVIEW_LANE
    assert reconcile.HOLD_LABEL not in board.labels


def _gate_note_timed(pr: dict, as_of: str) -> dict:
    """`pr` with a time on the gate's decline note at the head. DRE-6042's
    note is rebuilt from the review-cap question that quoted it, so its time
    is unknown (null) — and `rerunning` refuses an untimed decline rather than
    guess. The sweep read the note at `as_of`, so `as_of` is its upper bound;
    no proof-run receipt falls between the head's APPROVE and it, so any time
    in that window reads the same."""
    note = f"⏸️ {reconcile.GATE_HOLD_NOTE_MARKER} @{pr['headRefOid']} "
    for comment in pr["comments"]:
        if comment["body"].startswith(note) and not comment.get("createdAt"):
            comment["createdAt"] = as_of
    return pr


def _rerun(monkeypatch, doc: dict) -> str:
    """`ProofDispatch.rerunning` over the same pull request, its record the
    rows the gate's note names: the proof run's re-run, never "nothing to
    answer" — and the receipt its dispatch posts, which this returns. On
    DRE-6042 the proof run had already spent its two re-runs on 2026-10-09
    and posted its own `🔬 proof-waiting` hold, so there the answer is that
    hold, returned as it stands — still the proof run's, still not the
    CEO's; it names an operator as the one to read it next."""
    pr = _gate_note_timed(copy.deepcopy(doc["pr"]), doc["as_of"])
    head = pr["headRefOid"]
    repo = "/".join(pr["url"].split("/")[3:5])
    rows = _rows_the_note_names(pr)
    assert rows, f"{doc['card']}: the gate's note names no row not met"

    def fetch(repo_, files, ref, gh=None):
        return proof_record.Record(files[0]["path"], dispatch_tests.record_md(*rows), None)

    def voices(nodes, viewer, *, card):
        return [spoken_thread.Voice(
            spoken_thread.PIPELINE if n["authored_by_pipeline"] else spoken_thread.PERSON,
            "fixture", n["created_at"], n["body"]) for n in nodes]

    class Thread:
        def thread(self, ident):
            assert ident == doc["card"]
            return list(doc["comments"]), dispatch_tests.VIEWER

    holds: list = []
    posted: list[str] = []
    fired: list = []
    monkeypatch.setattr(proof_record, "fetch", fetch)
    monkeypatch.setattr(reconcile, "verdict_bound", lambda pr_: True)
    monkeypatch.setattr(linear_ops, "cmd_proof_waiting", lambda *args: holds.append(args))
    monkeypatch.setattr(linear_ops, "cmd_comment",
                        lambda ident, body, *flags: posted.append(body))
    one = proof_dispatch._Pass(
        repo, repo.split("/")[1], live=True, linear=Thread(),
        read=lambda path: None, find_pr=None,
        run_state=lambda *a, **k: dispatch_tests.state("finished", dispatches=1),
        release=None,
        fire=lambda card_, repo_, **kw: fired.append(kw) or (True, None),
        voices=voices,
        now=datetime.fromisoformat(doc["as_of"].replace("Z", "+00:00")),
        find_record=lambda ident: pr,
        read_record=lambda pr_, h: proof_dispatch.record_at(repo, pr_, h))
    card = {"identifier": doc["card"], "state": {"name": doc["lane"]},
            "labels": {"nodes": [{"name": n} for n in doc["labels"]]}}
    standing = proof_record.open_holds(
        voices(doc["comments"], dispatch_tests.VIEWER, card=doc["card"]))
    try:
        reason, count = one.rerunning(card)
    except proof_dispatch._Refused as refused:
        assert "nothing to answer" not in str(refused), refused
        assert standing, (
            f"{doc['card']}: the proof dispatch refused the re-run — {refused}")
        assert refused.bucket == "held"
        assert standing[-1] in str(refused)
        assert standing[-1].startswith("🔬 proof-waiting:")
        written = standing[-1]
    else:
        assert not standing
        assert reason == proof_dispatch.GATE_RERUN_REASON.format(sha7=head[:7])
        one.dispatch(card, reason, count)
        assert [kw["reason"] for kw in fired] == [reason]
        assert len(posted) == 1, f"{doc['card']}: the re-run posted {posted!r}"
        written = posted[0]
    assert holds == []
    return written


def _review_cap(monkeypatch, doc: dict, lanes: _Lanes) -> str:
    assert set(doc["rebuilt"]) <= set(doc["pr"]), (
        f"{doc['card']}: 'rebuilt' names a field the pr does not carry")
    _sweep(monkeypatch, doc, lanes)
    return _rerun(monkeypatch, doc)


# --------------------------------------------------------------------------- #
# blocked-epic — DRE-6651                                                      #
# --------------------------------------------------------------------------- #


def _blocked_epic(monkeypatch, doc: dict, lanes: _Lanes, tmp_path: Path,
                  capsys) -> str:
    """The planning route's decision; returns the reason it gives the wait."""
    relations = doc["relations"]
    assert relations["blocked_by"], f"{doc['card']}: no blocker to wait on"
    card = route_tests._Card(
        [c["body"] for c in doc["comments"]],
        labels=doc["labels"], lane=doc["lane"],
        blocked_by=relations["blocked_by"],
        has_children=relations["has_children"],
        history=relations["history"])
    out = tmp_path / "out.txt"
    capsys.readouterr()
    assert card.run(lambda: planning_route._cmd_decide(doc["card"], str(out))) == 0
    lanes.writes += [("cmd_state", ident, lane) for ident, lane in card.states]
    written = dict(line.split("=", 1) for line in out.read_text().splitlines() if line)
    assert written["route"] == "epic"
    assert written["wait"] == "true"
    for row in relations["blocked_by"]:
        assert f"{row['identifier']} ({row['state']})" in written["waits_on"], row
    printed = capsys.readouterr().out
    decided = json.loads(printed[printed.index("{\n"):])
    assert decided["wait"] is True
    return decided["wait_reason"]


# --------------------------------------------------------------------------- #
# switch-card — DRE-6652 and DRE-6654                                          #
# --------------------------------------------------------------------------- #


def _switch_card(monkeypatch, doc: dict, lanes: _Lanes) -> str:
    """The classifier's stamp and the routing verdict it implies; returns
    both, as the card would carry them."""
    now = doc["filed"]["now"]
    probe = {"card": doc["card"], "title": now["title"], "body": now["body"],
             "labels": list(now["labels"])}
    lops = classify_tests._Lops(probe)
    asked: list = []

    def call(model, prompt):
        asked.append(model)
        raise AssertionError("the model was asked about a label-routed card")

    decision = planning_classify.run(lops, doc["card"], call=call,
                                     model=classify_tests.MODEL)
    lanes.writes += [("cmd_state", doc["card"], lane) for lane in lops.states]
    assert asked == [], f"{doc['card']}: the classifier asked a model"
    assert decision.answered is False
    assert decision.shape == "one-off"
    assert not decision.escalates and not decision.requeue
    assert planning_shape.shape_on(lops.bodies) == "one-off"

    routed = routing_verdict.route(now["title"], now["body"], now["labels"])
    assert routed.verdict == "OPERATOR"
    assert routed.source == "label"
    return "\n\n".join(lops.bodies + [
        routing_verdict.verdict_comment(routed.verdict, routed.reason)])


# --------------------------------------------------------------------------- #
# one-off-no-result — DRE-7491                                                 #
# --------------------------------------------------------------------------- #


def _one_off_no_result(doc: dict, tmp_path: Path) -> str:
    """`plan_critic.py decide --stage one-off` as plan.yml's decision step runs
    it: the thread on stdin, the result file, this run's id and an escalation
    path. Returns the note it posts."""
    paths = {name: tmp_path / f"one-off-{name}.txt"
             for name in ("result", "note", "record", "escalation", "out")}
    paths["result"].write_text(doc["result"], encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "plan_critic.py"), "decide",
         "--stage", plan_critic.STAGE_ONE_OFF, "--epic", doc["card"],
         "--result-file", str(paths["result"]),
         "--github-output", str(paths["out"]),
         "--note-file", str(paths["note"]),
         "--record-file", str(paths["record"]),
         "--escalation-file", str(paths["escalation"]),
         "--run-id", doc["run_id"]],
        input=json.dumps(doc["comments"]), capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    outputs = dict(line.split("=", 1) for line
                   in paths["out"].read_text(encoding="utf-8").splitlines()
                   if "=" in line and not line.startswith(" "))
    assert outputs["action"] == plan_critic.RERUN, (
        f"{doc['card']}: the one-off decision answered {outputs['action']!r}")
    assert not paths["escalation"].exists(), (
        f"{doc['card']}: a re-run wrote a reason for the CEO")
    assert f"run={doc['run_id']}" in paths["record"].read_text(encoding="utf-8")
    return paths["note"].read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# the replay                                                                   #
# --------------------------------------------------------------------------- #


def test_the_directory_holds_the_six_cards():
    found = {_card_id(path) for path in _paths()}
    assert len(found) >= 6, (
        f"{FIXTURES.relative_to(ROOT)} holds {len(found)} fixture(s); "
        "the six cards of 2026-10-10 are owed")
    assert set(SIX) <= found, f"missing: {sorted(set(SIX) - found)}"
    assert set(LATER) <= found, f"missing: {sorted(set(LATER) - found)}"


@pytest.mark.parametrize("path", _paths(), ids=_card_id)
def test_the_card_does_not_land_in_green_light(monkeypatch, tmp_path, capsys, path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    problems = _shape_problems(path, doc)
    assert problems == [], f"{path.name}: " + "; ".join(problems)
    if doc["card"] in SIX:
        assert doc["arrival"] == SIX[doc["card"]]
    if doc["card"] in LATER:
        assert doc["arrival"] == LATER[doc["card"]]

    lanes = _Lanes(monkeypatch)
    arrival = doc["arrival"]
    if arrival == "plan-bound":
        written = _plan_bound(monkeypatch, doc, lanes)
    elif arrival == "review-cap":
        written = _review_cap(monkeypatch, doc, lanes)
    elif arrival == "blocked-epic":
        written = _blocked_epic(monkeypatch, doc, lanes, tmp_path, capsys)
    elif arrival == "one-off-no-result":
        written = _one_off_no_result(doc, tmp_path)
    else:
        written = _switch_card(monkeypatch, doc, lanes)

    assert lanes.to_green_light() == [], (
        f"{doc['card']} was written to Green Light: {lanes.to_green_light()}")
    assert NAMES[doc["for"]].search(written), (
        f"{doc['card']}: what the rule writes instead does not name the "
        f"{doc['for']} — {written!r}")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
