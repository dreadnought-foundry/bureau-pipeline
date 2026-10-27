"""The planner's escalation re-reads the epic's formal blockers before it asks
(DRE-8001, DRE-7283).

An epic whose formal blocker is open waits in Queued rather than asking the
CEO (DRE-6651). `planning_route.py decide` makes that reading once, before the
planner starts — and the planner can add the blocker itself. Plan run
40219766888, 2026-10-25, all times UTC:

  18:08:21  `decide` reads DRE-7283's relations: only DRE-7282 (Done), so
            `wait=false` and the planner starts
  18:11:58  the planner's own run adds DRE-7290 (Hand-work) as a `blocks`
            relation on DRE-7283
  18:12:46  `🙋 planning-escalation` posts, re-asking the question the CEO
            answered at 18:07
  18:12:47  Planning → Green Light, 49 seconds after the blocker was added

Twelve minutes later the CEO answered again, the next run's `decide` read the
same blocker, and the epic waited in Queued. The only difference was WHEN the
blocker was read. So the escalation step re-reads it live, with the one
reading `decide` makes (`planning_route._read_epic_wait`), and holds:

  1. the replay: the step posts one `⏸️ epic-waits:` receipt naming DRE-7290
     (Hand-work) through `--act=epic-waits-on-blocker`, moves the epic to
     Queued, and asks nothing and writes no Green Light;
  2. no open blocker: the escalation runs exactly as today;
  3. children, or an earlier move to In Progress: escalates as today
     (DRE-6407 re-plans) — the guard is `epic_wait`'s own;
  4. a relation or history read that fails: escalates as today (fails open);
  5. a card whose route is not `epic` never reads its blockers.

The step's shell is run as Actions runs it (`bash -e`), with Linear faked at
the `linear_ops` module through a `sitecustomize` on PYTHONPATH, so the real
`planning_route.py` and `planning_escalation.py` make every decision and only
the board is a stand-in.

The fixture is built from lane-history rows, relation rows and times only.
bureau-pipeline is public, and DRE-7283's comment bodies name a person, so it
carries no comment body, no name and no email.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_wait_before_escalation.py -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import linear_ops  # noqa: E402
import planning_escalation  # noqa: E402
import planning_route  # noqa: E402

WF = ROOT / ".github" / "workflows" / "plan.yml"
ESCALATION_STEP = "Planner escalation — hand-planning parks for the CEO"
WAIT_STEP = "Epic waits on its blocker — Queued, no planner"
WAIT_ACT = "epic-waits-on-blocker"
ASKED = f"{planning_escalation.ESCALATION_MARK} {planning_escalation.ESCALATION_TAG}"
HELD = "⏸️ epic-waits:"
GREEN_LIGHT = planning_escalation.destination()
QUEUED = "Queued"


def _moves(*rows) -> list[dict]:
    """Lane-history rows, newest first, in `linear_ops.lane_history`'s shape."""
    return [{"createdAt": at, "fromState": None, "toState": {"name": lane}}
            for at, lane in rows]


#: DRE-7283 as the board held it, read back from Linear on 2026-10-25: its
#: two `blocks` relations with the time each was made, no children, and its
#: lane history up to the escalation, newest first. Lanes, identifiers and
#: times only.
REPLAY = {
    "card": "DRE-7283",
    "run": "40219766888",
    "decide_at": "2026-10-25T18:08:21Z",
    "escalate_at": "2026-10-25T18:12:46Z",
    "children": [],
    "relations": [
        {"type": "blocks", "identifier": "DRE-7282", "state": "Done",
         "created_at": "2026-10-14T21:08:54.812Z"},
        {"type": "blocks", "identifier": "DRE-7290", "state": "Hand-work",
         "created_at": "2026-10-25T18:11:58.611Z"},
    ],
    "history": _moves(
        ("2026-10-25T18:07:44.932Z", "Planning"),
        ("2026-10-25T17:10:31.999Z", "Green Light"),
        ("2026-10-25T17:05:44.241Z", "Planning"),
        ("2026-10-25T16:50:31.372Z", "Queued"),
        ("2026-10-25T16:49:46.621Z", "Planning"),
        ("2026-10-25T16:38:04.007Z", "Queued"),
        ("2026-10-25T16:37:25.434Z", "Planning"),
        ("2026-10-25T16:26:16.059Z", "Queued"),
        ("2026-10-25T16:25:34.959Z", "Planning"),
        ("2026-10-25T16:06:35.363Z", "Queued"),
        ("2026-10-25T16:06:06.503Z", "Planning"),
        ("2026-10-25T15:49:00.124Z", "Queued"),
        ("2026-10-25T15:48:28.705Z", "Planning"),
        ("2026-10-25T15:37:43.294Z", "Queued"),
        ("2026-10-25T15:36:06.106Z", "Planning"),
        ("2026-10-25T15:24:17.456Z", "Queued"),
        ("2026-10-25T15:23:41.563Z", "Planning"),
        ("2026-10-25T15:08:19.670Z", "Queued"),
        ("2026-10-25T15:05:27.240Z", "Planning"),
        ("2026-10-25T14:50:17.881Z", "Queued"),
        ("2026-10-25T14:49:45.211Z", "Planning"),
        ("2026-10-25T14:49:12.380Z", "Queued"),
        ("2026-10-25T14:48:03.859Z", "Planning"),
        ("2026-10-25T14:36:15.079Z", "Queued"),
        ("2026-10-25T14:35:25.158Z", "Planning"),
        ("2026-10-25T14:24:22.835Z", "Queued"),
        ("2026-10-25T14:23:39.897Z", "Planning"),
        ("2026-10-25T14:06:31.276Z", "Queued"),
        ("2026-10-25T14:05:42.761Z", "Planning"),
        ("2026-10-25T13:48:33.696Z", "Queued"),
        ("2026-10-25T13:47:52.572Z", "Planning"),
        ("2026-10-25T13:35:33.582Z", "Queued"),
        ("2026-10-25T13:34:59.506Z", "Planning"),
        ("2026-10-25T13:23:08.140Z", "Queued"),
        ("2026-10-25T13:22:28.177Z", "Planning"),
        ("2026-10-25T13:06:19.508Z", "Queued"),
        ("2026-10-25T13:05:36.438Z", "Planning"),
        ("2026-10-25T12:53:56.873Z", "Queued"),
        ("2026-10-25T12:53:17.437Z", "Planning"),
        ("2026-10-25T12:33:09.434Z", "Queued"),
        ("2026-10-25T12:32:26.726Z", "Planning"),
        ("2026-10-25T12:30:17.931Z", "Queued"),
        ("2026-10-25T12:29:22.509Z", "Planning"),
    ),
}

#: What the planner wrote as its reason, in the run's own words' place: a
#: plain question with no name in it. Any reason will do — the planner wrote
#: one, and that is what sends the run down the escalation step.
REASON = (
    "The epic's own question was answered, but the planner could not tell "
    "whether the answer still holds. Should we wait for the full counts or "
    "plan now?"
)


def _visible(relations, at: str) -> list[dict]:
    """The relation rows the board held at `at` — a row exists from the time
    it was made."""
    return [row for row in relations if row["created_at"] <= at]


def _issue(card: str, relations, children) -> dict:
    """`_WAIT_QUERY`'s answer for these rows."""
    return {"issue": {
        "identifier": card,
        "children": {"nodes": [{"id": c} for c in children]},
        "inverseRelations": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [{"type": row["type"], "issue": {
                "identifier": row["identifier"],
                "state": {"name": row["state"]}}} for row in relations],
        },
    }}


def _blocker_card(identifier: str, relations) -> dict:
    """The sweep's title-and-parent read of an open blocker (DRE-7962):
    every blocker here is an ordinary card with no parent, so it waits for
    Done."""
    state = next(r["state"] for r in relations if r["identifier"] == identifier)
    return {"issue": {"identifier": identifier, "title": "a card",
                      "state": {"name": state}, "parent": None}}


class _Board:
    """The epic's relations and history as one reader sees them at a time."""

    def __init__(self, at: str, **overrides):
        doc = {**REPLAY, **overrides}
        self.card = doc["card"]
        self.relations = _visible(doc["relations"], at)
        self.children = doc["children"]
        self.history = doc["history"]

    def gql(self, query, variables=None, **_kw):
        if "inverseRelations" not in query and "parent {" in query:
            return _blocker_card(variables["id"], self.relations)
        assert "inverseRelations" in query, f"an unstubbed read: {query[:80]!r}"
        return _issue(self.card, self.relations, self.children)

    def lane_history(self, identifier, **_kw):
        return list(self.history)


# --------------------------------------------------------------------------- #
# the step, run as Actions runs it                                             #
# --------------------------------------------------------------------------- #

#: Linear, faked at the `linear_ops` module: the real module is imported and
#: its board reads and writes are replaced, so every module the step runs
#: decides for real. Every read and write is logged to STUB_LOG in order.
_SITECUSTOMIZE = r'''
import json, os, sys

import linear_ops
import pipeline_act

SCENARIO = os.environ["STUB_SCENARIO"]
LOG = os.environ["STUB_LOG"]


def _doc():
    with open(SCENARIO) as fh:
        return json.load(fh)


def _save(doc):
    with open(SCENARIO, "w") as fh:
        json.dump(doc, fh)


def _log(**entry):
    with open(LOG, "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def gql(query, variables=None, **kw):
    doc = _doc()
    if "inverseRelations" not in query and "parent {" in query:
        # The sweep's title-and-parent read of an open blocker (DRE-7962):
        # an ordinary card with no parent, which waits for Done.
        _log(op="read", what="blocker")
        ident = (variables or {}).get("id")
        state = next(r["state"] for r in doc["relations"] if r["identifier"] == ident)
        return {"issue": {"identifier": ident, "title": "a card",
                          "state": {"name": state}, "parent": None}}
    if "inverseRelations" not in query:
        _log(op="unstubbed", query=query[:120])
        raise AssertionError("an unstubbed Linear read: " + query[:80])
    _log(op="read", what="relations")
    if doc.get("relations_fail"):
        raise linear_ops.LinearError("Linear is down")
    at = doc["at"]
    rows = [r for r in doc["relations"] if r["created_at"] <= at]
    return {"issue": {
        "identifier": doc["card"],
        "children": {"nodes": [{"id": c} for c in doc["children"]]},
        "inverseRelations": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [{"type": r["type"], "issue": {
                "identifier": r["identifier"], "state": {"name": r["state"]}}}
                for r in rows],
        },
    }}


def lane_history(identifier, first=50):
    doc = _doc()
    _log(op="read", what="history")
    if doc.get("history_fail"):
        raise linear_ops.LinearError("Linear is down")
    return list(doc["history"])


def get_issue(identifier, *, fresh=False):
    doc = _doc()
    return {"id": "uuid-" + identifier, "identifier": identifier,
            "title": "an epic", "team": {"id": "team-uuid", "key": "DRE"},
            "state": {"name": doc["lane"], "type": "unstarted"},
            "labels": {"nodes": []}, "children": {"nodes": []}}


def comment_timeline(identifier, *, whole_thread=False):
    return [{"body": b, "createdAt": _doc()["at"]} for b in _doc()["posted"]]


def comment_bodies(identifier, *, whole_thread=False):
    return list(_doc()["posted"])


def count_comments(identifier, needle, *, since=None):
    return sum(1 for b in _doc()["posted"] if needle in b)


def cmd_comment(identifier, body, *flags):
    # The real command's one composition: `--act` makes the body that act's
    # receipt through the one writer.
    for flag in flags:
        if flag.startswith("--act="):
            body = pipeline_act.receipt(flag.split("=", 1)[1], body)
            _log(op="act", act=flag.split("=", 1)[1])
    doc = _doc()
    doc["posted"].append(body)
    _save(doc)
    _log(op="comment", body=body)
    return None


def cmd_state(identifier, state_name, *flags, **kw):
    doc = _doc()
    doc["lane"] = state_name
    _save(doc)
    _log(op="state", lane=state_name)
    return True


def cmd_state_of(identifier):
    print(_doc()["lane"])


for _name, _fn in list(globals().items()):
    if _name in ("gql", "lane_history", "get_issue", "comment_timeline",
                 "comment_bodies", "count_comments", "cmd_comment",
                 "cmd_state", "cmd_state_of"):
        setattr(linear_ops, _name, _fn)
'''

#: The shell's `linear_ops.py` commands, through the patched module — the
#: real CLI dispatches each command to the function of the same name.
_CLI = r'''
import sys

import linear_ops

cmd, *args = sys.argv[1:]
{"comment": linear_ops.cmd_comment, "state": linear_ops.cmd_state,
 "state-of": linear_ops.cmd_state_of}[cmd](*args)
'''


def _plan_steps() -> list[dict]:
    with open(WF, encoding="utf-8") as fh:
        return yaml.safe_load(fh)["jobs"]["plan"]["steps"]


def _step(name: str) -> dict:
    found = [s for s in _plan_steps() if s.get("name") == name]
    assert len(found) == 1, f"no single step named {name!r} in plan.yml"
    return found[0]


def _expressions(values: dict):
    """A `${{ … }}` resolver: secrets are a test key, everything else must be
    named in `values` — an expression nobody named is a test that has not
    caught up with the step, and it fails loudly rather than reading ''."""
    def resolve(match: re.Match) -> str:
        expr = " ".join(match.group(1).split())
        if expr.startswith("secrets."):
            return "lin_api_test"
        if expr not in values:
            raise AssertionError(f"the step reads {expr!r}, which this test does not set")
        return values[expr]
    return lambda text: re.sub(r"\$\{\{\s*(.+?)\s*\}\}", resolve, str(text))


def run_escalation(tmp_path, *, route="epic", at=REPLAY["escalate_at"],
                   reason=REASON, **overrides) -> tuple:
    """Run the escalation step's shell for DRE-7283 at `at`, on the route
    `decide` wrote. Answers (process, log entries, the lane after)."""
    runner = tmp_path / "runner"
    runner.mkdir()
    if reason is not None:
        (runner / "planner-escalation.txt").write_text(reason, encoding="utf-8")
    scenario = {
        "card": REPLAY["card"], "lane": "Planning", "at": at, "posted": [],
        "relations": REPLAY["relations"], "children": REPLAY["children"],
        "history": REPLAY["history"], **overrides,
    }
    (tmp_path / "scenario.json").write_text(json.dumps(scenario))
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "sitecustomize.py").write_text(_SITECUSTOMIZE)
    cli = tmp_path / "linear_ops_cli.py"
    cli.write_text(_CLI)
    work = tmp_path / "work"
    work.mkdir()
    (work / ".bureau-pipeline").symlink_to(ROOT)
    log = tmp_path / "log.jsonl"
    log.touch()
    summary = tmp_path / "summary.md"
    summary.touch()

    resolve = _expressions({
        "github.event.client_payload.identifier": REPLAY["card"],
        "runner.temp": str(runner),
        "steps.shape.outputs.route": route,
        "steps.shape.outputs.returned_child": "false",
        "steps.shape.outputs.parent": "",
        "steps.plan_done.outputs.execution_file || steps.claude.outputs.execution_file": "",
    })
    step = _step(ESCALATION_STEP)
    script = resolve(step["run"]).replace(
        "python3 .bureau-pipeline/scripts/linear_ops.py", f"python3 {cli}")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "scripts")]),
        "RUNNER_TEMP": str(runner),
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_OUTPUT": str(tmp_path / "github-output"),
        "LINEAR_API_KEY": "lin_api_test",
        "STUB_SCENARIO": str(tmp_path / "scenario.json"),
        "STUB_LOG": str(log),
    }
    env.update({k: resolve(v) for k, v in (step.get("env") or {}).items()})
    proc = subprocess.run(  # nosec B603 B607 — this repo's own step, fixed argv
        ["bash", "-e", "-c", script], cwd=work, env=env,
        capture_output=True, text=True, timeout=120, check=False,
    )
    entries = [json.loads(line) for line in log.read_text().splitlines() if line]
    lane = json.loads((tmp_path / "scenario.json").read_text())["lane"]
    return proc, entries, lane


def _comments(entries) -> list[str]:
    return [e["body"] for e in entries if e["op"] == "comment"]


def _states(entries) -> list[str]:
    return [e["lane"] for e in entries if e["op"] == "state"]


def _assert_escalated_as_today(proc, entries, lane):
    said = proc.stdout + proc.stderr
    assert proc.returncode == 0, said
    assert not [e for e in entries if e["op"] == "unstubbed"], entries
    comments = _comments(entries)
    assert len(comments) == 1, comments
    assert comments[0].startswith(ASKED), comments[0][:120]
    assert not [c for c in comments if HELD in c]
    assert _states(entries) == [GREEN_LIGHT]
    ops = [e["op"] for e in entries if e["op"] in ("comment", "state")]
    assert ops == ["comment", "state"], "the question lands before the move"
    assert lane == GREEN_LIGHT


# --------------------------------------------------------------------------- #
# the fixture                                                                  #
# --------------------------------------------------------------------------- #


class TestTheReplayFixture:
    def test_it_holds_no_name_no_email_and_no_comment_body(self):
        text = json.dumps(REPLAY)
        assert "@" not in text, "an email in a public repo's fixture"
        keys = set()

        def walk(node):
            if isinstance(node, dict):
                keys.update(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(REPLAY)
        for forbidden in ("body", "author", "user", "actor", "creator", "name_of",
                          "email", "displayName"):
            assert forbidden not in keys, forbidden
        # Lane names are the only names it carries.
        names = {row["toState"]["name"] for row in REPLAY["history"]}
        assert names <= {"Planning", "Green Light", "Queued"}

    def test_the_decide_reading_saw_only_the_done_blocker(self):
        board = _Board(REPLAY["decide_at"])
        assert [(r["identifier"], r["state"]) for r in board.relations] == [
            ("DRE-7282", "Done")]
        waiting = planning_route._read_epic_wait(board, REPLAY["card"])
        assert waiting.wait is False
        assert waiting.waits_on == ""

    def test_the_escalation_reading_sees_the_planners_blocker(self):
        board = _Board(REPLAY["escalate_at"])
        added = next(r for r in REPLAY["relations"] if r["identifier"] == "DRE-7290")
        assert REPLAY["decide_at"] < added["created_at"] < REPLAY["escalate_at"]
        assert board.children == []
        assert not [row for row in board.history
                    if row["toState"]["name"] == "In Progress"]
        # The sweep's blocker read goes through `linear_ops` (DRE-7962).
        with patch.object(linear_ops, "gql", side_effect=board.gql):
            waiting = planning_route._read_epic_wait(board, REPLAY["card"])
        assert waiting.wait is True
        assert waiting.waits_on == "DRE-7290 (Hand-work)"


# --------------------------------------------------------------------------- #
# 1: the replay                                                                #
# --------------------------------------------------------------------------- #


class TestDre7283WaitsInsteadOfAsking:
    def test_the_escalation_holds_the_epic_in_queued_on_dre_7290(self, tmp_path):
        proc, entries, lane = run_escalation(tmp_path)
        said = proc.stdout + proc.stderr
        assert proc.returncode == 0, said
        comments = _comments(entries)
        assert not [c for c in comments if ASKED in c], (
            "the planner's escalation asked a question the board already answered"
        )
        assert GREEN_LIGHT not in _states(entries), "no Green Light write"
        held = [c for c in comments if c.startswith(HELD)]
        assert len(held) == 1, comments
        assert "DRE-7290 (Hand-work)" in held[0]
        assert "DRE-7282" not in held[0], "a Done blocker is not waited on"
        assert [e["act"] for e in entries if e["op"] == "act"] == [WAIT_ACT]
        assert WAIT_ACT in held[0], "the receipt carries its act's trailer"
        assert comments == held
        # DRE-7962: DRE-7290 is read the sweep's way, and holds as an ordinary card.
        assert {"op": "read", "what": "blocker"} in entries
        assert not [e for e in entries if e["op"] == "unstubbed"], entries
        assert _states(entries) == [QUEUED]
        ops = [e["op"] for e in entries if e["op"] in ("comment", "state")]
        assert ops == ["comment", "state"], "the receipt lands before the move"
        assert lane == QUEUED

    def test_the_receipt_is_the_wait_steps_own_sentence(self, tmp_path):
        """One receipt, two writers: the words the escalation posts are the
        ones the wait step posts for the same epic and the same blocker."""
        _, entries, _ = run_escalation(tmp_path)
        held = next(c for c in _comments(entries) if c.startswith(HELD))
        line = re.search(r'WAITS="([^"]+)"', _step(WAIT_STEP)["run"]).group(1)
        expected = line.replace("$EPIC", REPLAY["card"]).replace(
            "$WAITS_ON", "DRE-7290 (Hand-work)")
        assert held.startswith(expected)


# --------------------------------------------------------------------------- #
# 2-5: everything else escalates exactly as today                              #
# --------------------------------------------------------------------------- #


class TestEveryOtherEpicEscalatesAsToday:
    def test_no_open_blocker_escalates(self, tmp_path):
        relations = [r for r in REPLAY["relations"] if r["identifier"] == "DRE-7282"]
        proc, entries, lane = run_escalation(tmp_path, relations=relations)
        _assert_escalated_as_today(proc, entries, lane)
        assert {"op": "read", "what": "relations"} in entries, (
            "an epic's blockers are read at the escalation"
        )

    def test_the_decide_time_reading_escalates(self, tmp_path):
        """The board as `decide` saw it: the blocker not yet made."""
        proc, entries, lane = run_escalation(tmp_path, at=REPLAY["decide_at"])
        _assert_escalated_as_today(proc, entries, lane)

    def test_an_epic_with_children_is_re_planned_not_held(self, tmp_path):
        proc, entries, lane = run_escalation(tmp_path, children=["child-uuid"])
        _assert_escalated_as_today(proc, entries, lane)
        assert {"op": "read", "what": "relations"} in entries

    def test_an_epic_with_an_earlier_green_light_is_re_planned_not_held(self, tmp_path):
        history = _moves(("2026-10-25T18:07:44.932Z", "Planning"),
                         ("2026-10-24T09:00:00.000Z", "In Progress"),
                         ("2026-10-24T08:00:00.000Z", "Green Light"))
        proc, entries, lane = run_escalation(tmp_path, history=history)
        _assert_escalated_as_today(proc, entries, lane)
        assert {"op": "read", "what": "history"} in entries

    @pytest.mark.parametrize("failing", ["relations_fail", "history_fail"])
    def test_a_read_that_fails_escalates(self, tmp_path, failing):
        proc, entries, lane = run_escalation(tmp_path, **{failing: True})
        _assert_escalated_as_today(proc, entries, lane)
        assert "Linear is down" in proc.stdout + proc.stderr

    @pytest.mark.parametrize("route", ["one-off", "roll-up", ""])
    def test_a_card_not_on_the_epic_route_never_reads_its_blockers(self, tmp_path, route):
        proc, entries, lane = run_escalation(tmp_path, route=route)
        _assert_escalated_as_today(proc, entries, lane)
        assert {"op": "read", "what": "relations"} not in entries, (
            f"a {route or 'route-less'} card read its blockers"
        )

    def test_the_step_keeps_its_gate(self):
        assert " ".join(str(_step(ESCALATION_STEP).get("if")).split()) == (
            "steps.route.outputs.mode == 'plan' && "
            "(steps.escalated.outputs.escalated == 'true' || steps.kids.outputs.count == '0')"
        )
