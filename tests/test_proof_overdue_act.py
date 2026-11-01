"""The sweep takes each overdue proof's one action (DRE-8627).

DRE-8626's daily pass posts one `⏳ proof-overdue:` comment on each PROOF card
open more than seven days, naming its class and the one machine action that
class takes. These tests pin `scripts/proof_overdue_act.py`, the step that
takes it:

  * each of the five actions — no dispatch before its condition, exactly one
    `plan_run.fire` with its reason once it holds, one `🔬 proof-run:`
    receipt counted `overdue 1 of 3`, and nothing more for the same comment;
  * the bound: the third dispatch is always `split`, and there is no fourth;
  * the stand-downs, each named on the card's line, and a step that writes
    nothing but the fire and its receipt;
  * the mark read only off a pipeline-authored comment;
  * the dispatcher's own budgets, unchanged by an `overdue` receipt;
  * the dry run, the brief's two paragraphs, the docstring and the entry
    point.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_overdue_act.py -v
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import plan_run  # noqa: E402
import proof_dispatch  # noqa: E402
import proof_overdue  # noqa: E402
import proof_overdue_act as act  # noqa: E402
import proof_overdue_sweep as sweep_mod  # noqa: E402
import proof_reach  # noqa: E402
import proof_release  # noqa: E402
import proof_run_state  # noqa: E402
import test_proof_dispatch as td  # noqa: E402

REPO = "dreadnought-foundry/bureau-pipeline"
SLUG = "bureau-pipeline"
SANDBOX_REPO = "dreadnought-foundry/agent-bureau"
VIEWER = "pipeline-key-user"
PERSON = "a-person"
CARD = "DRE-6008"
#: Noon PT on 2026-11-01 (PST, after the daylight-saving change).
NOW = datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc)
DAY = "2026-11-01"
PASS = timedelta(minutes=15)


def _at(minutes_ago: float, now: datetime = NOW) -> str:
    return (now - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def comment(body: str, minutes_ago: float, user: str | None = VIEWER) -> dict:
    return {"body": body, "createdAt": _at(minutes_ago),
            "user": {"id": user} if user else None}


def told(cls: str, waits_on: str, minutes_ago: float = 60, *, day: str = DAY,
         user: str | None = VIEWER) -> dict:
    """The daily pass's comment, its first line the contract's own."""
    d = proof_overdue.Diagnosis(
        card=CARD, repo=SLUG, age_days=12.4, clock_from=NOW - timedelta(days=12.4),
        cls=cls, action=proof_overdue.ACTIONS[cls], waits_on=waits_on, not_before=None,
        by_elimination=False, evidence="")
    return comment(sweep_mod.comment(d, day), minutes_ago, user)


def receipt(minutes_ago: float, reason: str = "first proof run",
            count: str = "dispatch 1 of 2") -> dict:
    return comment(f"🔬 proof-run: dispatched a proof run at 2026-10-20 08:00 PT — "
                   f"{reason} ({count})", minutes_ago)


def overdue_receipt(minutes_ago: float, k: int,
                    reason: str = "re-run after the proof-overdue date 2026-10-25 09:00 PT"):
    return receipt(minutes_ago, reason, f"overdue {k} of 3")


def state(name: str) -> proof_run_state.State:
    return proof_run_state.State(name, [f"{name} — fixture"], 1, None, [], None)


class World:
    """The board one pass reads, and every write it makes, recorded. A
    receipt posted lands in the card's thread as the pipeline's, so the next
    pass reads it back."""

    def __init__(self, thread, *, lane="Hand-work", repo=SLUG, labels=(), states=None,
                 run="finished", release="ready", deploy=None, rows=1, fire_ok=True,
                 post_error=None, card_error=None):
        self.thread = list(thread)
        self.lane, self.repo, self.labels = lane, repo, list(labels)
        self.card_states = dict(states or {})
        self.run, self.release_state, self.deploy, self.rows = run, release, deploy, rows
        self.fire_ok, self.post_error, self.card_error = fire_ok, post_error, card_error
        self.fired, self.posted, self.reads = [], [], []
        self.now = NOW

    # proof_overdue.LinearReads, and the card's `id` for the fire
    def proofs(self, slug):
        self.reads.append(("proofs", slug))
        return [CARD]

    def card(self, ident):
        self.reads.append(("card", ident))
        if self.card_error:
            raise self.card_error
        return {"id": f"uuid-{ident}", "identifier": ident,
                "title": f"PROOF: {ident} observed live", "description": "",
                "state": {"name": self.lane},
                "labels": {"nodes": [{"name": f"repo:{self.repo}"},
                                     *({"name": n} for n in self.labels)]}}

    def thread_of(self, ident):
        self.reads.append(("thread", ident))
        return list(self.thread), VIEWER

    def states(self, ids):
        self.reads.append(("states", tuple(ids)))
        return {i: self.card_states[i] for i in ids if i in self.card_states}

    # the seams
    def run_state(self, repo, ident, comments, viewer, *, read, now):
        self.reads.append(("run_state", ident))
        return state(self.run)

    def released(self, issue, ident, slug, repo):
        self.reads.append(("release", ident))
        return proof_release.Reading(self.release_state, [f"{self.release_state} — fixture"])

    def deployed(self, repo, since):
        self.reads.append(("deploy", repo))
        return self.deploy

    def unrecurring(self, ident, issue, repo, *, forced):
        self.reads.append(("rows", ident, forced))
        return self.rows

    def fire(self, card, repo, *, reason=None, event=None):
        self.fired.append((card["identifier"], repo, reason, event))
        return (True, "") if self.fire_ok else (False, "gh api refused: HTTP 403")

    def post(self, ident, body, *flags):
        if self.post_error:
            raise self.post_error
        self.posted.append((ident, body))
        self.thread.append({"body": body, "createdAt": self.now.isoformat(),
                            "user": {"id": VIEWER}})
        return None


class _Linear:
    def __init__(self, world):
        self.world = world

    def proofs(self, slug):
        return self.world.proofs(slug)

    def card(self, ident):
        return self.world.card(ident)

    def thread(self, ident):
        return self.world.thread_of(ident)

    def states(self, ids):
        return self.world.states(ids)


def run(world: World, monkeypatch, *, live=True, now=NOW, repo=None) -> int:
    world.now = now
    monkeypatch.setattr(linear_ops, "cmd_comment", world.post)
    return act.sweep(repo or (SANDBOX_REPO if world.repo == "agent-bureau" else REPO),
                     world.repo, live=live, now=now, linear=_Linear(world),
                     run_state=world.run_state, fire=world.fire, read=lambda path: None,
                     released=world.released, deployed=world.deployed,
                     unrecurring=world.unrecurring)


def _lines(capsys) -> list:
    return capsys.readouterr().out.splitlines()


def _card_lines(lines) -> list:
    return [line for line in lines if line.startswith(f"{act.LINE} · {CARD}")]


def _first(body: str) -> str:
    return body.split("\n", 1)[0]


# --------------------------------------------------------------------------- #
# the contract                                                                 #
# --------------------------------------------------------------------------- #


def test_the_contract_is_spelled_as_the_card_spells_it():
    assert act.OVERDUE_BUDGET == 3
    assert act.OVERDUE_COUNT.format(k=1) == "overdue 1 of 3"
    assert act.MARK == "⏳ proof-overdue:" == sweep_mod.MARK
    assert act.LINE.startswith("proof-overdue: action step")
    assert act.RELEASE_REASON.format(waits_on="DRE-7001") == \
        "re-run after the proof-overdue release DRE-7001 shipped"
    assert act.DATE_REASON.format(at="2026-11-01 14:00 PT") == \
        "re-run after the proof-overdue date 2026-11-01 14:00 PT"
    assert act.DEPLOY_REASON.format(tag="v41") == "re-run after the proof-overdue deploy v41"
    assert act.PRESS_REASON.format(mutation="createTask", day=DAY) == \
        "sandbox press: createTask (proof-overdue of 2026-11-01)"
    assert act.SPLIT_REASON.format(n=2, day=DAY) == \
        "split: 2 row(s) cannot recur unattended (proof-overdue of 2026-11-01)"
    assert set(act.TAKES) == set(proof_overdue.ACTIONS.values())


def test_the_first_line_is_parsed_back():
    body = told("release", "DRE-7001")["body"]
    got = act.parse(body)
    assert (got.day, got.cls, got.action, got.waits_on) == \
        (DAY, "release", "rerun-on-release", "DRE-7001")
    assert act.parse("⏳ proof-overdue: someday · class=date") is None
    assert act.parse("a comment") is None


def test_the_overdue_count_spends_none_of_the_dispatchers_budgets():
    for k in (1, 2, 3):
        count = act.OVERDUE_COUNT.format(k=k)
        assert not count.startswith(("re-run", "timed re-run"))
        assert count not in (proof_dispatch.RECHECK_COUNT, proof_dispatch.REARM_COUNT,
                             proof_dispatch.CHASE_COUNT)
    for reason in (act.RELEASE_REASON, act.DATE_REASON, act.DEPLOY_REASON,
                   act.PRESS_REASON, act.SPLIT_REASON):
        assert not reason.startswith(proof_run_state.FIRST_RUN)


# --------------------------------------------------------------------------- #
# the five actions                                                             #
# --------------------------------------------------------------------------- #


def _dispatched_once(world, reason, k=1):
    assert world.fired == [(CARD, world_repo(world), reason, plan_run.PROOF_EVENT)]
    assert len(world.posted) == 1
    ident, body = world.posted[0]
    assert ident == CARD
    assert _first(body).startswith("🔬 proof-run: dispatched a proof run at ")
    assert _first(body).endswith(f" — {reason} (overdue {k} of 3)")
    parsed = proof_run_state.receipt(body)
    assert parsed is not None and parsed.reason == reason
    assert parsed.count == f"overdue {k} of 3"
    assert pipeline_act.read_trailer(body)["act"] == "proof-run-dispatched"


def world_repo(world) -> str:
    return SANDBOX_REPO if world.repo == "agent-bureau" else REPO


def _second_pass_adds_nothing(world, monkeypatch, capsys):
    fired, posted = list(world.fired), list(world.posted)
    capsys.readouterr()
    assert run(world, monkeypatch, now=NOW + PASS) == 0
    assert world.fired == fired and world.posted == posted
    lines = _card_lines(_lines(capsys))
    assert any("already" in line and DAY in line for line in lines), lines


def test_rerun_on_release_waits_for_the_card_to_ship(monkeypatch, capsys):
    reason = "re-run after the proof-overdue release DRE-7001 shipped"
    for before in ("In Review", "Canceled"):
        world = World([told("release", "DRE-7001")], states={"DRE-7001": before})
        assert run(world, monkeypatch) == 0
        assert world.fired == [] and world.posted == []
        lines = _card_lines(_lines(capsys))
        assert any("DRE-7001" in line and before in line for line in lines), lines
    world = World([told("release", "DRE-7001")], states={"DRE-7001": "Done"})
    assert run(world, monkeypatch) == 0
    _dispatched_once(world, reason)
    _second_pass_adds_nothing(world, monkeypatch, capsys)


def test_rerun_on_release_of_a_tag_waits_for_the_release_reading(monkeypatch, capsys):
    for before in ("waiting", "unknown"):
        world = World([told("release", "stable")], release=before)
        assert run(world, monkeypatch) == 0
        assert world.fired == [] and world.posted == []
        assert any(before in line for line in _card_lines(_lines(capsys)))
    world = World([told("release", "stable")], release="ready")
    assert run(world, monkeypatch) == 0
    _dispatched_once(world, "re-run after the proof-overdue release stable shipped")
    _second_pass_adds_nothing(world, monkeypatch, capsys)


def test_rerun_on_date_waits_for_the_time(monkeypatch, capsys):
    when = "2026-11-01T22:00:00+00:00"
    world = World([told("date", when)])
    assert run(world, monkeypatch) == 0
    assert world.fired == [] and world.posted == []
    assert any("2026-11-01 14:00 PT" in line for line in _card_lines(_lines(capsys)))
    later = NOW + timedelta(hours=2, minutes=30)
    assert run(world, monkeypatch, now=later) == 0
    _dispatched_once(world, "re-run after the proof-overdue date 2026-11-01 14:00 PT")
    fired, posted = list(world.fired), list(world.posted)
    assert run(world, monkeypatch, now=later + PASS) == 0
    assert world.fired == fired and world.posted == posted


def test_rerun_on_deploy_waits_for_a_release_newer_than_the_comment(monkeypatch, capsys):
    world = World([told("deploy", "make deploy-console")], deploy=None)
    assert run(world, monkeypatch) == 0
    assert world.fired == [] and world.posted == []
    assert _card_lines(_lines(capsys))
    world = World([told("deploy", "make deploy-console")],
                  deploy=("console-v41", NOW - timedelta(minutes=5)))
    assert run(world, monkeypatch) == 0
    _dispatched_once(world, "re-run after the proof-overdue deploy console-v41")
    _second_pass_adds_nothing(world, monkeypatch, capsys)


def test_sandbox_press_is_dispatched_at_once_on_an_agent_bureau_proof(monkeypatch, capsys):
    world = World([told("press", "createTask")], repo="agent-bureau")
    assert run(world, monkeypatch) == 0
    _dispatched_once(world, "sandbox press: createTask (proof-overdue of 2026-11-01)")
    _second_pass_adds_nothing(world, monkeypatch, capsys)


@pytest.mark.parametrize("repo, mutation", [(SLUG, "createTask"),
                                            ("agent-bureau", "deleteEverything")])
def test_sandbox_press_off_the_list_or_off_agent_bureau_stands_down(
        monkeypatch, capsys, repo, mutation):
    world = World([told("press", mutation)], repo=repo)
    assert run(world, monkeypatch) == 0
    assert world.fired == [] and world.posted == []
    assert any("press" in line for line in _card_lines(_lines(capsys)))


def test_split_is_dispatched_at_once_naming_its_rows(monkeypatch, capsys):
    world = World([told("unrepeatable", "the window had closed")], rows=2)
    assert run(world, monkeypatch) == 0
    _dispatched_once(world, "split: 2 row(s) cannot recur unattended "
                            "(proof-overdue of 2026-11-01)")
    assert ("rows", CARD, False) in world.reads
    _second_pass_adds_nothing(world, monkeypatch, capsys)


def test_the_newest_comment_is_the_one_read(monkeypatch, capsys):
    """Yesterday's comment was acted on; today's buys one more, as `overdue 2`."""
    thread = [told("unrepeatable", "x", 1500, day="2026-10-31"),
              overdue_receipt(1490, 1, reason="split: 1 row(s) cannot recur "
                                              "unattended (proof-overdue of 2026-10-31)"),
              told("press", "createTask", 60)]
    world = World(thread, repo="agent-bureau")
    assert run(world, monkeypatch) == 0
    _dispatched_once(world, "sandbox press: createTask (proof-overdue of 2026-11-01)", k=2)


# --------------------------------------------------------------------------- #
# the bound                                                                    #
# --------------------------------------------------------------------------- #


def test_the_third_is_always_split(monkeypatch, capsys):
    thread = [overdue_receipt(3000, 1), overdue_receipt(1500, 2),
              told("date", "2026-12-01T00:00:00+00:00", 60)]
    world = World(thread, rows=3)
    assert run(world, monkeypatch) == 0
    _dispatched_once(world, "split: 3 row(s) cannot recur unattended "
                            "(proof-overdue of 2026-11-01)", k=3)
    assert ("rows", CARD, True) in world.reads


def test_a_card_with_three_gets_no_fourth_and_its_line_says_so(monkeypatch, capsys):
    thread = [overdue_receipt(4500, 1), overdue_receipt(3000, 2),
              overdue_receipt(1500, 3), told("unrepeatable", "x", 60)]
    world = World(thread)
    assert run(world, monkeypatch) == 0
    assert world.fired == [] and world.posted == []
    lines = _card_lines(_lines(capsys))
    assert any("3 of 3" in line and "no fourth" in line for line in lines), lines


# --------------------------------------------------------------------------- #
# the stand-downs                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["running", "unknown"])
def test_a_run_in_flight_stands_down(monkeypatch, capsys, name):
    world = World([told("unrepeatable", "x")], run=name)
    assert run(world, monkeypatch) == 0
    assert world.fired == [] and world.posted == []
    assert any(name in line for line in _card_lines(_lines(capsys)))


def test_a_dispatcher_receipt_newer_than_the_comment_stands_down(monkeypatch, capsys):
    world = World([told("unrepeatable", "x", 60), receipt(10)])
    assert run(world, monkeypatch) == 0
    assert world.fired == [] and world.posted == []
    assert any("newer than the comment" in line for line in _card_lines(_lines(capsys)))


def test_a_dispatcher_receipt_older_than_the_comment_holds_nothing(monkeypatch, capsys):
    world = World([receipt(3000), told("unrepeatable", "x", 60)])
    assert run(world, monkeypatch) == 0
    assert len(world.fired) == 1


def test_needs_human_stands_down(monkeypatch, capsys):
    world = World([told("unrepeatable", "x")], labels=["needs-human"])
    assert run(world, monkeypatch) == 0
    assert world.fired == [] and world.posted == []
    assert any("needs-human" in line for line in _card_lines(_lines(capsys)))


def test_green_light_is_never_touched(monkeypatch, capsys):
    world = World([told("unrepeatable", "x")], lane="Green Light")
    assert run(world, monkeypatch) == 0
    assert world.fired == [] and world.posted == []
    assert any("Green Light" in line for line in _card_lines(_lines(capsys)))
    assert ("thread", CARD) not in world.reads


class Recorder:
    """`linear_ops` as the step sees it: its constants real, every call recorded
    and answered with nothing."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        real = getattr(linear_ops, name)
        if not callable(real):
            return real

        def call(*args, **kwargs):
            self.calls.append(name)
            return None
        return call


def test_it_writes_only_the_fire_and_the_receipt(monkeypatch, capsys):
    for cls, waits_on, kw in (("unrepeatable", "x", {}),
                              ("press", "createTask", {"repo": "agent-bureau"}),
                              ("release", "DRE-7001", {"states": {"DRE-7001": "Done"}})):
        world = World([told(cls, waits_on)], **kw)
        recorder = Recorder()
        monkeypatch.setattr(act, "linear_ops", recorder)
        assert act.sweep(world_repo(world), world.repo, live=True, now=NOW,
                         linear=_Linear(world), run_state=world.run_state,
                         fire=world.fire, read=lambda path: None,
                         released=world.released, deployed=world.deployed,
                         unrecurring=world.unrecurring) == 0
        assert len(world.fired) == 1
        assert recorder.calls == ["cmd_comment"]


# --------------------------------------------------------------------------- #
# whose comment is read                                                        #
# --------------------------------------------------------------------------- #


def test_a_copy_of_the_mark_from_anyone_else_is_not_read(monkeypatch, capsys):
    for user in (PERSON, None):
        world = World([told("unrepeatable", "x", user=user)])
        assert run(world, monkeypatch) == 0
        assert world.fired == [] and world.posted == []
        assert any("no pipeline-authored" in line for line in _card_lines(_lines(capsys)))


def test_a_copy_newer_than_the_pipelines_own_is_not_the_newest(monkeypatch, capsys):
    """A person's later copy names a press; the pipeline's comment, a date
    still to come, is the one read."""
    thread = [told("date", "2026-12-01T00:00:00+00:00", 120),
              told("press", "createTask", 30, user=PERSON)]
    world = World(thread, repo="agent-bureau")
    assert run(world, monkeypatch) == 0
    assert world.fired == [] and world.posted == []


def test_a_receipt_from_anyone_else_counts_for_nothing(monkeypatch, capsys):
    forged = dict(overdue_receipt(30, 1), user={"id": PERSON})
    world = World([told("unrepeatable", "x", 60), forged])
    assert run(world, monkeypatch) == 0
    _dispatched_once(world, "split: 1 row(s) cannot recur unattended "
                            "(proof-overdue of 2026-11-01)")


# --------------------------------------------------------------------------- #
# the dry run and the exit                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("cls, waits_on, kw", [
    ("release", "DRE-7001", {"states": {"DRE-7001": "Done"}}),
    ("release", "stable", {}),
    ("date", "2026-11-01T10:00:00+00:00", {}),
    ("deploy", "make deploy", {"deploy": ("v41", NOW - timedelta(minutes=5))}),
    ("press", "createTask", {"repo": "agent-bureau"}),
    ("unrepeatable", "x", {}),
])
def test_the_dry_run_is_would_lines_only(monkeypatch, capsys, cls, waits_on, kw):
    world = World([told(cls, waits_on)], **kw)
    assert run(world, monkeypatch, live=False) == 0
    assert world.fired == [] and world.posted == []
    lines = _lines(capsys)
    would = [line for line in lines if line.startswith(f"would: dispatch {CARD} — ")]
    assert len(would) == 1, lines
    assert would[0].endswith("(overdue 1 of 3)")


def test_a_failed_fire_is_red_and_posts_no_receipt(monkeypatch, capsys):
    world = World([told("unrepeatable", "x")], fire_ok=False)
    assert run(world, monkeypatch) == 1
    assert len(world.fired) == 1 and world.posted == []


def test_a_receipt_that_did_not_land_is_red(monkeypatch, capsys):
    world = World([told("unrepeatable", "x")], post_error=RuntimeError("Linear 503"))
    assert run(world, monkeypatch) == 1
    assert len(world.fired) == 1


def test_a_card_not_read_is_named_and_not_red(monkeypatch, capsys):
    world = World([told("unrepeatable", "x")], card_error=RuntimeError("Linear 503"))
    assert run(world, monkeypatch) == 0
    assert world.fired == []
    assert any("could not be read" in line for line in _card_lines(_lines(capsys)))


# --------------------------------------------------------------------------- #
# the default readings                                                         #
# --------------------------------------------------------------------------- #


def _release_reads(tag_date: datetime):
    surfaces = {"surfaces": {"console": {"tag_series": ["console-v*"], "paths": [],
                                         "record": "release"}}}
    content = base64.b64encode(json.dumps(surfaces).encode()).decode()
    sha = "a" * 40
    answers = {
        f"repos/{REPO}/contents/.github/bureau/release.json": {"content": content},
        f"repos/{REPO}/git/matching-refs/tags/console-v": [
            {"ref": "refs/tags/console-v41", "object": {"sha": sha, "type": "commit"}}],
        f"repos/{REPO}/git/ref/tags/console-v41": {"object": {"sha": sha, "type": "commit"}},
        f"repos/{REPO}/git/commits/{sha}": {
            "committer": {"date": tag_date.isoformat().replace("+00:00", "Z")}},
    }
    return lambda path: answers[path]


def test_deployed_reads_the_newest_release_tag_against_the_comment():
    since = NOW - timedelta(hours=1)
    got = act.deployed_since(REPO, since, read=_release_reads(NOW - timedelta(minutes=5)))
    assert got is not None and got[0] == "console-v41"
    assert act.deployed_since(REPO, since,
                              read=_release_reads(NOW - timedelta(hours=3))) is None


def test_deployed_reads_nothing_newer_with_no_release_record():
    def read(path):
        if path.endswith("release.json"):
            raise proof_release.Missing("HTTP 404")
        return {}
    assert act.deployed_since(REPO, NOW, read=read) is None


# --------------------------------------------------------------------------- #
# the dispatcher, reading an `overdue` receipt                                 #
# --------------------------------------------------------------------------- #


def test_the_run_state_reader_reads_an_overdue_receipt_and_spends_no_first_run():
    body = pipeline_act.receipt(
        "proof-run-dispatched",
        "🔬 proof-run: dispatched a proof run at 2026-11-01 12:00 PT — "
        "sandbox press: createTask (proof-overdue of 2026-11-01) (overdue 1 of 3)")
    got = proof_run_state.receipt(body)
    assert got is not None
    assert got.count == "overdue 1 of 3"
    assert got.reason == "sandbox press: createTask (proof-overdue of 2026-11-01)"
    thread = [td.receipt(800), td.receipt(500, reason=got.reason, count=got.count)]
    reading = proof_run_state.reading(td.REPO, "DRE-5930", thread, td.VIEWER,
                                      read=lambda path: [], now=td.NOW)
    assert reading.dispatches == 1
    assert [r.count for r in reading.receipts] == ["dispatch 1 of 2", "overdue 1 of 3"]


def test_the_dispatchers_first_run_budget_is_unchanged_by_an_overdue_receipt(
        monkeypatch, capsys):
    """One first run and one overdue run that died: the first-run budget
    still has its second dispatch. Read through DRE-5922's real reader."""
    run_id = "35799999998"
    thread = [
        td.promoted(900),
        td.receipt(800),
        td.receipt(500, reason="split: 1 row(s) cannot recur unattended "
                               "(proof-overdue of 2026-10-06)", count="overdue 1 of 3"),
        td.comment("🧠 model-attempt: claude-opus-5-5 — proof agent starting. Run: "
                   f"https://github.com/{td.REPO}/actions/runs/{run_id}", 480),
    ]
    reads = {
        f"repos/{td.REPO}/pulls?head=dreadnought-foundry:agent/DRE-5930-"
        "proof-record&state=all&per_page=100": [],
        f"repos/{td.REPO}/actions/runs/{run_id}": {
            "status": "completed", "conclusion": "failure", "updated_at": td._at(400)},
    }
    board = td.Board(hand=[td.lane_card("DRE-5930")], threads={"DRE-5930": thread})
    h = td.Harness(monkeypatch, board)
    proof_dispatch.sweep(
        td.REPO, td.SLUG, live=True, linear=board, read=lambda path: reads[path],
        find_pr=h.find_pr, run_state=proof_run_state.reading,
        release=h.release, fire=h.fire, voices=td.fake_voices, now=td.NOW)
    assert len(h.fired) == 1
    assert h.fired[0][2].startswith(f"second dispatch — run {run_id} ended failure at ")
    assert _first(h.posted[0][1]).endswith("(dispatch 2 of 2)")
    assert h.holds == []


def test_the_dispatchers_re_run_budget_is_unchanged_and_no_operator_hold(
        monkeypatch, capsys):
    """An overdue run finished on a record the critic sent back: the critic's
    findings are still unanswered by a re-run of the dispatcher's, so the
    first re-run goes — no `re-run 2`, and no hold for an operator."""
    done = (td.receipt(50, reason="re-run after the proof-overdue date "
                                  "2026-10-06 09:00 PT", count="overdue 1 of 3"),
            td.comment("⏳ 5/5 record amended", 20))
    h, tally = td._rerun_pass(monkeypatch, thread=td._sent_back(*done),
                              pr=td.record(td.verdict("REQUEST_CHANGES", 100)))
    assert h.holds == []
    assert h.fired == [("DRE-5930", td.REPO, td.RERUN, "proof-execute")]
    assert _first(h.posted[0][1]).endswith(f" — {td.RERUN} (re-run 1 of 2)")

    # Beside a spent re-run of its own, the overdue run still spends none.
    capsys.readouterr()
    thread = td._sent_back(td.rerun_receipt(1500, 1), td.comment("⏳ 5/5 amended", 1400),
                           *done)
    h, tally = td._rerun_pass(monkeypatch, thread=thread,
                              pr=td.record(td.verdict("REQUEST_CHANGES", 100)))
    assert h.holds == []
    assert _first(h.posted[0][1]).endswith(f" — {td.RERUN} (re-run 2 of 2)")


# --------------------------------------------------------------------------- #
# the brief, the docstring, the entry point                                    #
# --------------------------------------------------------------------------- #


BRIEF = (ROOT / "briefs" / "proof.md").read_text(encoding="utf-8")


def _paragraph(opening: str) -> str:
    found = [p for p in BRIEF.split("\n\n") if opening in p]
    assert len(found) == 1, f"{opening!r} is in {len(found)} paragraphs of the brief"
    return found[0]


def test_the_brief_says_what_a_sandbox_press_reason_asks():
    text = _paragraph("reason opening `sandbox press:`")
    assert "`Sandbox press:`" in text and "agent-bureau-demo" in text
    assert "standards/card-quality.md" in text
    assert "record" in text


def test_the_brief_says_what_a_split_reason_asks():
    text = _paragraph("reason opening `split:`")
    assert "`Not met. history:" in text
    assert "proof_gap.file_fix_card" in text and "Intake" in text
    assert "DRE-3075" in text


def test_the_proof_reach_check_passes_over_the_brief():
    assert proof_reach.check(ROOT) == []


def test_the_docstring_names_the_actions_the_bound_and_the_stand_downs():
    doc = act.__doc__
    for action in proof_overdue.ACTIONS.values():
        assert f"`{action}`" in doc, action
    for word in ("three", "split", "running", "unknown", "needs-human", "Green Light",
                 "newer than the comment", "PROOF_DISPATCH_LIVE", "would:"):
        assert word in doc, word


def test_main_reads_its_environment(monkeypatch):
    seen = {}

    def fake(repo, slug, *, live, **kw):
        seen.update(repo=repo, slug=slug, live=live)
        return 0
    monkeypatch.setattr(act, "sweep", fake)
    for value, live in (("true", True), ("TRUE", False), ("", False)):
        monkeypatch.setenv("REPO", "acme/widgets")
        monkeypatch.setenv("REPO_SLUG", "widgets")
        monkeypatch.setenv("PROOF_DISPATCH_LIVE", value)
        assert act.main([]) == 0
        assert seen == {"repo": "acme/widgets", "slug": "widgets", "live": live}


def test_it_runs_with_no_arguments_and_no_repo_reads_nothing():
    env = {k: v for k, v in os.environ.items()
           if k not in ("REPO", "REPO_SLUG", "LINEAR_API_KEY")}
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "proof_overdue_act.py")],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.startswith(act.LINE)
