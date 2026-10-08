"""RED-first tests: the build run's dead-run cap tries the planner once before it holds (DRE-6178).

At the `dead-run-requeue` cap `dead_run.decide` answered `hold` for every class
of death, and the hold said "a human must split/fix the card". Only one of the
three classes that reach that cap says anything about the card's size: the
SILENT death, where the run concluded with no pull request and no blocker note.
A credential refusal is finished work that could not be pushed, and an
`is_error` death is a model failing mid-run; cutting the card smaller answers
neither, so both keep their hold, word for word.

WHAT THIS PINS, one section per acceptance criterion:

  1. `decide(2, split_tried=False)` hands the silent class to Planning under
     `hold.DEAD_SPLIT_MARK`; `split_tried` None or True holds exactly as on
     `main`; the footprint and the earlier deaths ride the receipt.
  2. The credential and `is_error` classes hold at the cap whatever
     `split_tried` says, with `main`'s bodies.
  3. `split_tried` and `death_lines` read one budget — the thread since the
     last `dead-run-budget-reset` marker.
  4. The receipt spends no budget: it carries none of the three tags.
  5. `dead_run.py park --reason <code>` stamps the hold after both writes land.
  6. The Report step's dead-run block hands the thread to `decide` for the
     silent and API classes, never for a credential refusal, and names the
     reason it parks for; the CLI reads the thread itself.
  7. The medic does not rerun a card the hand-off just gave the planner.
  8. Planning's readers treat the receipt as a return: the old planner stamp
     is void, the card is classified afresh, and a returned child of an epic
     splits into siblings — while the split ledger counts nothing new.

The bodies pinned as literals below are what `decide` returns on `main` at
3af8f8b; a literal is the only way a test can say "unchanged from main" without
comparing the function with itself.

Run: python3 -m pytest tests/test_dead_run_split_first.py -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORT = SCRIPTS / "report_agent_result.sh"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import dead_run  # noqa: E402
import hold  # noqa: E402
import medic_retry  # noqa: E402
import planning_classify  # noqa: E402
import planning_route  # noqa: E402
import planning_shape  # noqa: E402
import split_ledger  # noqa: E402
from test_planning_classify import (  # noqa: E402 — the classifier's own Linear stand-in
    MODEL,
    _answer,
    _caller,
    _Lops,
    _probe,
)

CAP = dead_run.REQUEUE_CAP
RUN_URL = "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/1"
OPUS = "claude-opus-5-5"

#: `decide(2)` on `main`.
MAIN_SILENT_HOLD = (
    "🚨 held-for-human (dead-run-requeue cap reached): agent died with no PR "
    "and no blocker note for the 3th time — parked in Backlog with the "
    "'needs-human' label so the relay and the reconcile sweep stop looping. A "
    "human must split/fix the card and clear the label to retry."
)
#: `decide(2, credential_expiry=True, push_status="400", artifact="rescue-DRE-1.patch")` on `main`.
MAIN_CREDENTIAL_HOLD = (
    "🚨 held-for-human (dead-run-requeue cap reached): the run's GitHub "
    "credential was refused before the branch could reach GitHub — GitHub "
    "answered HTTP 400 to the push — the agent finished the work and could not "
    "deliver it. Every git credential on the runner is one App installation "
    "token, which lives an hour and can also be rejected outright. This is a "
    "CREDENTIAL failure: not a fault in the model, the service or the card, and "
    "it has now happened 3 times. Parked in Backlog with the 'needs-human' "
    "label — the re-mint at the push is not recovering this card, so a human "
    "needs to look at the App credentials before it is retried. The work "
    "itself is not lost: this run's `rescue-DRE-1.patch` artifact holds the "
    "branch's commits."
)
#: `decide(2, is_error=True, error_model="claude-opus-5-5")` on `main`.
MAIN_IS_ERROR_HOLD = (
    "🚨 held-for-human (dead-run-requeue cap reached): agent died with "
    "API/model error (is_error) for the 3th time (last model tried: "
    "claude-opus-5-5) — parked in Backlog with the 'needs-human' label so the "
    "relay and the reconcile sweep stop looping. A human must split/fix the "
    "card and clear the label to retry.\nmodel-error: claude-opus-5-5"
)


def _silent_receipt(n: int) -> str:
    return dead_run.decide(n - 1).comments[0]


def _api_receipt(n: int) -> str:
    return dead_run.decide(n - 1, is_error=True, error_model=OPUS).comments[0]


def _credential_receipt(n: int) -> str:
    return dead_run.decide(n - 1, credential_expiry=True, push_status="401").comments[0]


def _handoff(**kw) -> str:
    return dead_run.decide(CAP, split_tried=False, **kw).comments[0]


# ===========================================================================
# 1. the silent class at the cap goes to the planner, once
# ===========================================================================
class TestTheSilentCapHandsTheCardToThePlanner:
    def test_split_untried_answers_replan_under_the_split_mark(self):
        d = dead_run.decide(CAP, split_tried=False)
        assert d.action == "replan"
        assert len(d.comments) == 1
        assert d.comments[0].startswith("✂️ dead-run-cap → Planning:")
        assert d.comments[0].startswith(hold.DEAD_SPLIT_MARK)

    @pytest.mark.parametrize("kw", [{}, {"split_tried": None}, {"split_tried": True}],
                             ids=["default", "none", "tried"])
    def test_otherwise_the_cap_holds_exactly_as_on_main(self, kw):
        d = dead_run.decide(CAP, **kw)
        assert d.action == "hold"
        assert d.comments == [MAIN_SILENT_HOLD]

    def test_below_the_cap_nothing_changes(self):
        assert dead_run.decide(0, split_tried=False) == dead_run.decide(0)
        assert dead_run.decide(1, split_tried=False) == dead_run.decide(1)

    def test_the_receipt_carries_the_count_and_the_run(self):
        body = _handoff(run_url=RUN_URL)
        assert f"{CAP + 1}" in body.splitlines()[0]
        assert RUN_URL in body

    def test_the_footprint_is_quoted_when_given(self):
        body = _handoff(footprint="**Files:** a.py, b.py")
        assert "**Files:** a.py, b.py" in body

    def test_without_a_footprint_the_planner_is_told_to_read_it_off_the_card(self):
        body = _handoff()
        assert "**Files:**" in body
        assert "read it off the card" in body

    def test_the_earlier_deaths_are_quoted_in_order(self):
        deaths = ["first: the run named the credential",
                  "second: the run named the model",
                  "third: no pull request"]
        body = _handoff(deaths=deaths)
        at = [body.index(line) for line in deaths]
        assert at == sorted(at), "the deaths are not quoted oldest first"

    def test_with_no_deaths_the_receipt_says_the_thread_held_none(self):
        body = _handoff()
        assert "held no earlier receipt" in body
        assert "held no earlier receipt" in _handoff(deaths=[])

    def test_the_planner_is_told_its_two_answers(self):
        body = _handoff()
        assert "splits the card on its file footprint" in body
        assert "sends it back as one piece" in body

    def test_quoted_receipts_lose_their_budget_tags(self):
        """A death line IS a `dead-run-requeue` receipt's first line; quoted
        whole it would make the hand-off a fourth death."""
        deaths = dead_run.death_lines([_silent_receipt(1), _api_receipt(2)])
        body = _handoff(deaths=deaths)
        assert dead_run.DEAD_TAG not in body
        assert "agent died with no PR and no blocker note" in body
        assert "agent died with API/model error (is_error)" in body


# ===========================================================================
# 2. the other two classes hold at the cap whatever split_tried says
# ===========================================================================
class TestTheOtherClassesStillHold:
    @pytest.mark.parametrize("tried", [False, True, None])
    def test_a_credential_refusal_holds_with_main_s_text(self, tried):
        d = dead_run.decide(CAP, split_tried=tried, credential_expiry=True,
                            push_status="400", artifact="rescue-DRE-1.patch")
        assert d.action == "hold"
        assert d.comments == [MAIN_CREDENTIAL_HOLD]
        assert "credential" in d.comments[0]
        assert "rescue-DRE-1.patch" in d.comments[0]
        assert "died" not in d.comments[0]
        assert not d.comments[0].startswith(hold.DEAD_SPLIT_MARK)

    @pytest.mark.parametrize("tried", [False, True, None])
    def test_an_api_death_holds_with_main_s_text(self, tried):
        d = dead_run.decide(CAP, split_tried=tried, is_error=True, error_model=OPUS)
        assert d.action == "hold"
        assert d.comments == [MAIN_IS_ERROR_HOLD]
        assert f"{dead_run.ERROR_MARKER_PREFIX} {OPUS}" in d.comments[0]
        assert not d.comments[0].startswith(hold.DEAD_SPLIT_MARK)


# ===========================================================================
# 3. one budget: split_tried and death_lines read since the last reset
# ===========================================================================
def _reset() -> str:
    return dead_run.reset_comment()


class TestOneReadingOfTheBudget:
    def test_a_split_mark_newer_than_the_reset_is_tried(self):
        bodies = [_silent_receipt(1), _reset(), _silent_receipt(1), _handoff()]
        assert dead_run.split_tried(bodies) is True

    def test_a_split_mark_older_than_the_reset_is_not(self):
        bodies = [_silent_receipt(1), _handoff(), _reset(), _silent_receipt(1)]
        assert dead_run.split_tried(bodies) is False

    def test_a_thread_with_no_mark_is_not(self):
        assert dead_run.split_tried([_silent_receipt(1), _silent_receipt(2)]) is False
        assert dead_run.split_tried([]) is False

    def test_a_comment_quoting_the_mark_mid_sentence_is_not_a_try(self):
        assert dead_run.split_tried([f"fyi {hold.DEAD_SPLIT_MARK} soon"]) is False

    def test_death_lines_read_the_budget_since_the_reset_oldest_first(self):
        api, silent = _api_receipt(1), _silent_receipt(2)
        bodies = [_credential_receipt(1), _credential_receipt(2), _reset(), api, silent]
        assert dead_run.death_lines(bodies) == [api.splitlines()[0],
                                                silent.splitlines()[0]]

    def test_each_class_says_what_it_was(self):
        lines = dead_run.death_lines(
            [_credential_receipt(1), _api_receipt(2), _silent_receipt(3)])
        assert "credential" in lines[0]
        assert "API/model error" in lines[1]
        assert "no PR" in lines[2]

    def test_a_thread_with_no_death_has_no_lines(self):
        assert dead_run.death_lines([]) == []
        assert dead_run.death_lines(["⏳ 1/5 plan", _reset()]) == []


# ===========================================================================
# 4. the receipt spends no budget
# ===========================================================================
class TestTheReceiptSpendsNothing:
    def test_it_carries_none_of_the_three_tags(self):
        deaths = dead_run.death_lines(
            [_credential_receipt(1), _api_receipt(2), _silent_receipt(3)])
        body = _handoff(deaths=deaths, footprint="**Files:** a.py", run_url=RUN_URL)
        for tag in (dead_run.DEAD_TAG, dead_run.TURN_TAG, dead_run.RESET_TAG):
            assert tag not in body

    def test_count_of_is_unchanged_by_it(self):
        thread = [_silent_receipt(1), _silent_receipt(2), _silent_receipt(3)]
        body = _handoff(deaths=dead_run.death_lines(thread))
        for tag in (dead_run.DEAD_TAG, dead_run.TURN_TAG, dead_run.RESET_TAG):
            assert dead_run.count_of(thread + [body], tag) == dead_run.count_of(thread, tag)

    def test_it_carries_no_model_error_marker(self):
        body = _handoff(deaths=dead_run.death_lines([_api_receipt(1)]))
        assert dead_run.ERROR_MARKER_PREFIX not in body


# ===========================================================================
# 4b. the turn-cap paths are untouched
# ===========================================================================
class TestTheTurnCapPathsAreUntouched:
    @pytest.mark.parametrize("tried", [False, True])
    @pytest.mark.parametrize("prior,progress,action", [
        (0, None, "replan"), (0, 2, "replan"), (0, 3, "requeue"), (1, 3, "hold"),
    ])
    def test_a_turn_cap_death_reads_as_it_always_did(self, tried, prior, progress, action):
        base = dead_run.decide(prior, turn_exhaustion=True, last_progress=progress)
        d = dead_run.decide(prior, turn_exhaustion=True, last_progress=progress,
                            split_tried=tried)
        assert d == base
        assert d.action == action
        assert not d.comments[0].startswith(hold.DEAD_SPLIT_MARK)


# ===========================================================================
# 5. park --reason stamps the hold after both writes land
# ===========================================================================
class _FakeLinear:
    def __init__(self, fail_state: bool = False):
        self.calls: list[tuple] = []
        self.fail_state = fail_state

    def get_issue(self, identifier):
        return {"labels": {"nodes": []}}

    def _label_names(self, issue):
        return [n["name"] for n in issue["labels"]["nodes"]]

    def add_label(self, identifier, name):
        self.calls.append(("add_label", identifier, name))

    def remove_label(self, identifier, name):
        self.calls.append(("remove_label", identifier, name))

    def cmd_state(self, identifier, name, *flags):
        self.calls.append(("state", identifier, name, *flags))
        if self.fail_state:
            raise RuntimeError("Linear refused the state write")

    def cmd_comment(self, identifier, body, *flags):
        self.calls.append(("comment", identifier, body))


class TestParkStampsTheReason:
    def _park(self, monkeypatch, argv, **kw):
        fake = _FakeLinear(**kw)
        monkeypatch.setitem(sys.modules, "linear_ops", fake)
        return dead_run.main(argv), fake.calls

    def test_the_stamp_follows_the_label_and_the_state(self, monkeypatch):
        rc, calls = self._park(monkeypatch, ["park", "DRE-1", "--reason", "turn-cap-park"])
        assert rc == 0
        assert calls == [
            ("add_label", "DRE-1", dead_run.HOLD_LABEL),
            ("state", "DRE-1", dead_run.PARK_STATE, "--park"),
            ("comment", "DRE-1",
             "🔒 hold: reason=turn-cap-park at=none lifts=unpark-marker by=dead_run.py"),
        ]

    def test_the_dead_run_cap_is_stamped_the_same_way(self, monkeypatch):
        rc, calls = self._park(monkeypatch, ["park", "DRE-1", "--reason", "dead-run-cap"])
        assert rc == 0
        assert calls[-1] == (
            "comment", "DRE-1",
            "🔒 hold: reason=dead-run-cap at=none lifts=unpark-marker by=dead_run.py")

    def test_an_unlanded_park_posts_no_stamp(self, monkeypatch):
        rc, calls = self._park(monkeypatch, ["park", "DRE-1", "--reason", "dead-run-cap"],
                               fail_state=True)
        assert rc == 1
        assert ("remove_label", "DRE-1", dead_run.HOLD_LABEL) in calls
        assert not [c for c in calls if c[0] == "comment"]

    def test_without_a_reason_it_exits_2_naming_the_flag(self, monkeypatch, capsys):
        rc, calls = self._park(monkeypatch, ["park", "DRE-1"])
        assert rc == 2
        assert "--reason" in capsys.readouterr().out
        assert calls == []

    def test_a_reason_the_park_does_not_write_is_refused_before_any_write(
            self, monkeypatch, capsys):
        rc, calls = self._park(monkeypatch, ["park", "DRE-1", "--reason", "no-route"])
        assert rc == 2
        assert calls == []


# ===========================================================================
# 6. the Report step's dead-run block, executed
# ===========================================================================
THREAD = [_silent_receipt(1), "⏳ 1/5 plan", _silent_receipt(2)]

LINEAR_STUB = '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"tool": "linear_ops", "args": sys.argv[1:]}) + "\\n")
if sys.argv[1] == "count-comments":
    print(os.environ.get("STUB_PRIOR", "2"))
if sys.argv[1] == "dump-comments":
    if os.environ.get("STUB_DUMP_FAILS"):
        sys.exit("linear: rate limited")
    print(os.environ["STUB_THREAD"])
'''

DEAD_RUN_STUB = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
entry = {"tool": "dead_run", "args": args}
if "--comments-file" in args:
    with open(args[args.index("--comments-file") + 1], encoding="utf-8") as fh:
        entry["thread"] = fh.read()
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(entry) + "\\n")
if args[0] == "decide":
    print(os.environ.get("STUB_ACTION", "hold"))
    print()
    print("the body")
if args[0] == "park-unlanded":
    print("unlanded")
'''


def dead_run_block() -> str:
    """The `else` arm of the Report step's routing — the dead-run block."""
    text = REPORT.read_text(encoding="utf-8")
    m = re.search(r"\nelse\n(  # The dead-run or turn-cap decision.*?)\nfi\n", text, re.S)
    assert m, "the dead-run block was not found in report_agent_result.sh"
    return m.group(1)


def run_block(td: str, *, death_class: str, action: str = "hold", **env_extra):
    scripts = os.path.join(td, ".bureau-pipeline", "scripts")
    os.makedirs(scripts)
    for name, body in (("linear_ops.py", LINEAR_STUB), ("dead_run.py", DEAD_RUN_STUB)):
        path = os.path.join(scripts, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(path, 0o755)
    log = os.path.join(td, "stub.jsonl")
    script = "set -e\nif false; then :\nelse\n" + dead_run_block() + "\nfi\n"
    env = dict(os.environ)
    env.update(
        STUB_LOG=log, STUB_THREAD=json.dumps(THREAD), STUB_ACTION=action,
        RUNNER_TEMP=td, CARD="DRE-1", AGENT_STARTED="yes", DEATH_CLASS=death_class,
        RATE_FLAGS="", EXEC_FILE=os.path.join(td, "exec.json"), MODEL_USED=OPUS,
        CLAUDE_OUTCOME="failure", FAILED_STEP="", RUN_URL=RUN_URL,
        RESCUE_PUSH_STATUS="401", RESCUE_PATCH="", RESCUE_ARTIFACT="",
    )
    env.update(env_extra)
    proc = subprocess.run(["bash", "-c", script], cwd=td, env=env,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    with open(log, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh.read().splitlines()]


def _calls(journal, tool, op):
    return [e for e in journal if e["tool"] == tool and e["args"][0] == op]


@pytest.fixture
def td():
    path = tempfile.mkdtemp()
    yield path
    shutil.rmtree(path, ignore_errors=True)


class TestTheReportStepHandsDecideTheThread:
    @pytest.mark.parametrize("death_class", ["none", "api_death"])
    def test_the_plain_branch_passes_the_dumped_thread(self, td, death_class):
        journal = run_block(td, death_class=death_class)
        (decide,) = _calls(journal, "dead_run", "decide")
        assert "--comments-file" in decide["args"]
        assert json.loads(decide["thread"]) == THREAD
        assert _calls(journal, "linear_ops", "dump-comments")
        if death_class == "api_death":
            assert "--is-error" in decide["args"]

    @pytest.mark.parametrize("death_class", ["none", "api_death"])
    def test_a_failed_dump_passes_no_thread_so_the_cap_holds(self, td, death_class):
        # `[]` would read as a budget that never tried the planner and hand
        # the card off again on every failed dump; no file reads as None.
        journal = run_block(td, death_class=death_class, STUB_DUMP_FAILS="1")
        assert _calls(journal, "linear_ops", "dump-comments")
        (decide,) = _calls(journal, "dead_run", "decide")
        assert "--comments-file" not in decide["args"]
        if death_class == "api_death":
            assert "--is-error" in decide["args"]

    def test_the_credential_branch_passes_no_thread(self, td):
        journal = run_block(td, death_class="credential_expiry")
        (decide,) = _calls(journal, "dead_run", "decide")
        assert "--credential-expiry" in decide["args"]
        assert "--comments-file" not in decide["args"]

    @pytest.mark.parametrize("death_class", ["none", "api_death", "credential_expiry"])
    def test_a_dead_run_hold_parks_for_the_dead_run_cap(self, td, death_class):
        journal = run_block(td, death_class=death_class)
        (park,) = _calls(journal, "dead_run", "park")
        assert park["args"] == ["park", "DRE-1", "--reason", "dead-run-cap"]

    def test_a_turn_cap_hold_parks_for_the_turn_cap(self, td):
        journal = run_block(td, death_class="turn_exhaustion")
        (park,) = _calls(journal, "dead_run", "park")
        assert park["args"] == ["park", "DRE-1", "--reason", "turn-cap-park"]

    def test_a_dead_run_hand_off_rides_the_replan_branch(self, td):
        journal = run_block(td, death_class="none", action="replan")
        assert not _calls(journal, "dead_run", "park")
        (advance,) = _calls(journal, "linear_ops", "advance")
        assert advance["args"] == ["advance", "DRE-1", "Planning", "In Progress,Todo"]
        (comment,) = _calls(journal, "linear_ops", "comment")
        assert comment["args"] == ["comment", "DRE-1", "the body"]

    def test_the_count_is_read_the_same_number_of_ways(self):
        text = REPORT.read_text(encoding="utf-8")
        assert text.count("count-comments") == 3


def _cli(*args, cwd=None):
    proc = subprocess.run([sys.executable, str(SCRIPTS / "dead_run.py"), *args],
                          capture_output=True, text=True, cwd=cwd)
    assert proc.returncode == 0, proc.stderr
    action, _, body = proc.stdout.partition("\n\n")
    return action.strip(), body.strip()


class TestTheCliReadsTheThreadItself:
    def _thread(self, td, bodies) -> str:
        path = os.path.join(td, "thread.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(bodies, fh)
        return path

    def test_a_thread_with_no_mark_hands_off(self, td):
        action, body = _cli("decide", str(CAP), "--comments-file", self._thread(td, THREAD))
        assert action == "replan"
        assert body.startswith(hold.DEAD_SPLIT_MARK)
        assert "agent died with no PR and no blocker note" in body
        assert dead_run.DEAD_TAG not in body

    def test_an_empty_thread_hands_off(self, td):
        # A card with no comments has a budget that never tried the planner —
        # which is why the Report step never writes `[]` for a failed dump.
        action, body = _cli("decide", str(CAP), "--comments-file", self._thread(td, []))
        assert action == "replan"
        assert body.startswith(hold.DEAD_SPLIT_MARK)
        assert "the thread held no earlier receipt" in body

    def test_a_thread_whose_budget_already_tried_the_planner_holds(self, td):
        thread = THREAD + [_handoff()]
        action, body = _cli("decide", str(CAP), "--comments-file", self._thread(td, thread))
        assert action == "hold"
        assert body == MAIN_SILENT_HOLD

    def test_an_api_death_holds_with_the_thread(self, td):
        action, _ = _cli("decide", str(CAP), "--comments-file", self._thread(td, THREAD),
                         "--is-error", "--error-model", "m")
        assert action == "hold"

    def test_a_credential_refusal_holds_with_the_thread(self, td):
        action, _ = _cli("decide", str(CAP), "--comments-file", self._thread(td, THREAD),
                         "--credential-expiry")
        assert action == "hold"

    def test_no_thread_holds_exactly_as_on_main(self):
        assert _cli("decide", str(CAP)) == ("hold", MAIN_SILENT_HOLD)

    def test_an_unreadable_thread_holds_exactly_as_on_main(self, td):
        missing = os.path.join(td, "nope.json")
        assert _cli("decide", str(CAP), "--comments-file", missing) == ("hold", MAIN_SILENT_HOLD)


# ===========================================================================
# 7. the medic leaves a card the hand-off gave the planner alone
# ===========================================================================
RUN_STARTED_AT = "2026-10-08T10:00:00.000Z"


class TestTheMedicKnowsTheMark:
    def _reason(self, posted_at: str) -> str:
        return medic_retry.park_reason(
            state="Planning", labels=[],
            receipts=[{"body": _handoff(run_url=RUN_URL), "created_at": posted_at}],
            run_started_at=RUN_STARTED_AT)

    def test_a_hand_off_newer_than_the_run_parks_it_with_the_planner(self):
        reason = self._reason("2026-10-08T10:30:00.000Z")
        assert reason
        assert "planner" in reason

    def test_a_hand_off_older_than_the_run_is_not_this_run_s(self):
        assert self._reason("2026-10-08T09:00:00.000Z") == ""


# ===========================================================================
# 8. Planning's readers treat the receipt as a return
# ===========================================================================
def _planner_stamp(shape: str, why: str = "the classifier's first read") -> str:
    return planning_shape.shape_comment(shape, why, by=planning_shape.BY_PLANNER, model=MODEL)


def _hand_stamp(shape: str, why: str = "the operator's call") -> str:
    return planning_shape.shape_comment(shape, why, by=planning_shape.BY_HAND)


def _the_handoff() -> str:
    """The receipt the Report step posts for THREAD's third death."""
    return _handoff(deaths=dead_run.death_lines(THREAD), run_url=RUN_URL)


class TestPlanningReadsTheMark:
    def test_the_mark_is_a_return_mark_read_off_hold(self):
        assert hold.DEAD_SPLIT_MARK in planning_shape.return_marks()
        for name in ("planning_shape.py", "planning_classify.py"):
            assert hold.DEAD_SPLIT_MARK not in (SCRIPTS / name).read_text(encoding="utf-8")

    def test_the_receipt_voids_the_planner_stamp_before_it(self):
        handoff = _the_handoff()
        bodies = [_planner_stamp("one-off"), handoff]
        assert planning_shape.return_receipt(bodies) is handoff
        assert _planner_stamp("one-off") not in planning_shape.live(bodies)
        assert planning_shape.shape_on(bodies) is None

    def test_a_comment_quoting_the_mark_returns_nothing(self):
        bodies = [_planner_stamp("one-off"), f"fyi, {hold.DEAD_SPLIT_MARK} may follow"]
        assert planning_shape.return_receipt(bodies) is None
        assert planning_shape.shape_on(bodies) == "one-off"

    def test_a_hand_stamp_before_the_receipt_still_wins(self):
        handoff = _the_handoff()
        bodies = [_hand_stamp("one-off"), handoff]
        assert planning_shape.shape_on(bodies) == "one-off"

    def test_the_card_is_classified_afresh_and_the_why_names_the_hand_off(self):
        handoff = _the_handoff()
        probe = _probe("DRE-3018")
        lops = _Lops(probe, bodies=[_planner_stamp("one-off"), handoff])
        call = _caller(_answer(shape="epic", tells=(1, 3),
                               why="three pieces with a contract between them"))
        decision = planning_classify.run(lops, probe["card"], call=call, model=MODEL)
        assert decision.already is False
        assert decision.why != "already classified"
        assert decision.shape == "epic"
        (stamp,) = [c for c in lops.comments if c.startswith(planning_shape.SHAPE_MARK)]
        why = next(line for line in stamp.splitlines() if line.startswith("**Why:**"))
        assert ("its builds kept dying with no pull request and the dead-run cap "
                "handed it to the planner to split") in why
        first = handoff.splitlines()[0][len(hold.DEAD_SPLIT_MARK):].strip()
        assert first[:60] in why
        assert hold.DEAD_SPLIT_MARK not in stamp
        assert "a build run handed it back" not in why
        assert "ran out of turns" not in why

    def test_returned_why_names_the_hand_off(self):
        handoff = _the_handoff()
        why = planning_classify.returned_why(handoff)
        assert "the dead-run cap handed it to the planner to split" in why
        assert hold.DEAD_SPLIT_MARK not in why


def _parent() -> dict:
    return {"identifier": "DRE-100", "title": "[EPIC] bureau-pipeline: the work",
            "has_children": True, "shape": "epic"}


class TestAReturnedChildSplitsOnTheMark:
    def test_a_fresh_epic_stamp_after_the_receipt_is_a_returned_child(self):
        handoff = _the_handoff()
        bodies = [_planner_stamp("one-off"), handoff,
                  _planner_stamp("epic", "the split, read after the hand-off")]
        answer = planning_route.returned_child(bodies, _parent())
        assert answer.returned is True
        assert answer.parent == "DRE-100"

    def test_an_epic_stamp_older_than_the_receipt_is_not(self):
        handoff = _the_handoff()
        bodies = [_hand_stamp("epic"), handoff]
        answer = planning_route.returned_child(bodies, _parent())
        assert answer.returned is False
        assert "predates the return receipt" in answer.reason


class TestTheSplitLedgerCountsNothingNew:
    def test_no_death_row_and_no_hand_back(self):
        handoff = _the_handoff()
        thread = [_planner_stamp("one-off")] + THREAD
        assert (split_ledger.turn_cap_deaths(thread + [handoff])
                == split_ledger.turn_cap_deaths(thread) == [])
        assert split_ledger.handed_back(thread + [handoff]) is False


# ===========================================================================
# the registries still match
# ===========================================================================
class TestTheRegistriesStillMatch:
    @pytest.mark.parametrize("script", ["hold.py", "check_act_receipts.py"])
    def test_the_check_is_green(self, script):
        args = ["check"] if script == "hold.py" else []
        proc = subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                              capture_output=True, text=True, cwd=str(ROOT))
        assert proc.returncode == 0, proc.stdout + proc.stderr

