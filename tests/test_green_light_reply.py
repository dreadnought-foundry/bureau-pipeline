"""A comment the CEO leaves on a Green Light plan is picked up (DRE-6138).

`scripts/green_light_reply.py` is the sweep phase that reads the epics waiting
in `Green Light`, finds the newest comment in the CEO's voice nothing has
answered yet, and sends the plan back for a review once per comment through
`plan_run.fire` — `trigger_state="green light"`, `reason="re-run"`,
`event="agent-plan"` — with one `💬 green-light-reply` receipt naming the
comment's `createdAt` stamp. Every Linear read and the dispatch are injected
here — no network.

Pinned here, over a fixture board:

  * the dispatch, its three payload words, and the one receipt;
  * the lane word against the real seams (`dedupe_dispatch`, `review_rerun`);
  * once per comment: the receipt and a later cycle-start both answer it;
  * whose voice counts — a verified console answer, or a `person` whose
    paired node's user id is declared — and every voice that does not;
  * the cards never dispatched: PROOF, `epic-queued`, not an epic, not ours;
  * the bound, the budget trailer, a failed dispatch, an unreadable thread;
  * the dry run, the missing `REPO` / `REPO_SLUG`;
  * the receipt's shape and its `unconverted` row in the act registry;
  * the step in `reconcile.yml` and the critic's sentence in `plan.yml`.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import console_receipt  # noqa: E402
import dedupe_dispatch  # noqa: E402
import green_light_reply as glr  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import plan_critic  # noqa: E402
import plan_run  # noqa: E402
import review_rerun  # noqa: E402
import spoken_thread  # noqa: E402
from test_plan_critic_wiring import prompt_of, steps as plan_steps  # noqa: E402

REPO = "dreadnought-foundry/bureau-pipeline"
SLUG = "bureau-pipeline"
VIEWER = "pipeline-key-user"
CEO_ID = "ceo-linear-user"
SOMEONE = "someone-else"
NOW = datetime(2026, 10, 7, 17, 0, tzinfo=timezone.utc)   # 10:00 PT
WORKFLOW = ROOT / ".github" / "workflows" / "reconcile.yml"
PLAN_WORKFLOW = ROOT / ".github" / "workflows" / "plan.yml"
STEP = "Reply to the CEO's Green Light comments"
CRITIC_STEP = "Second critic — review (before Green Light)"
#: The sentence `plan.yml`'s second critic carries, whitespace normalised.
CRITIC_SENTENCE = (
    "A comment in the CEO's voice newer than the plan's newest revision is a "
    "finding this round must answer, either by changing the plan as he asked "
    "or by a plain-English answer to his question in the revision note. A "
    "PASS that leaves his comment unanswered is not a pass.")

#: The user ids the fake `voices` labels by kind — the real reader checks a
#: signature and compares with the viewer; this suite needs only the result.
SIGNED, REFUSED, UNCHECKED, UNKNOWN = "signed", "refused", "unchecked", "unknown"


def _at(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def comment(body: str, minutes_ago: float, user: str | None = VIEWER) -> dict:
    return {"body": body, "createdAt": _at(minutes_ago),
            "user": {"id": user} if user else None}


def answer(minutes_ago: float, words: str = "Change the proof card first.") -> dict:
    """A console answer — its trailer only has to LOOK like one here: the
    fake `voices` below decides it verified, off the user id."""
    return comment(f"Answer from Sid, 2026-10-07 09:30 PT:\n{words}\n"
                   f"{console_receipt.MARK} {console_receipt.ANSWER_TAG}: v1 "
                   f"card=DRE-1 sha256=x", minutes_ago, SIGNED)


def said(minutes_ago: float, user: str | None = CEO_ID,
         words: str = "Why two cards here?") -> dict:
    return comment(words, minutes_ago, user)


def receipt_for(node: dict, minutes_ago: float, kind="ceo-via-console") -> dict:
    return comment(glr.reply_receipt(node["createdAt"], kind), minutes_ago)


def cycle_start(epic: str, minutes_ago: float, user: str | None = VIEWER) -> dict:
    return comment(plan_critic.cycle_marker(epic), minutes_ago, user)


def lane_card(ident: str, *, title: str | None = None, repo: str = SLUG,
              labels=(), window=None, children=()) -> dict:
    nodes = list(window or [])
    return {
        "id": f"uuid-{ident}", "identifier": ident,
        "title": title or f"[EPIC] bureau-pipeline: plan {ident}",
        "description": "The plan.",
        "updatedAt": _at(1),
        "state": {"name": "Green Light"},
        "labels": {"nodes": [{"name": f"repo:{repo}"}, {"name": "agent:planner"},
                             *({"name": name} for name in labels)]},
        "children": {"nodes": [{"identifier": c} for c in children]},
        "comments": {"pageInfo": {"hasNextPage": False},
                     "nodes": list(reversed(nodes))},
    }


def fake_voices(nodes, viewer, *, card):
    out = []
    for node in nodes:
        who = (node.get("user") or {}).get("id")
        kind = {SIGNED: spoken_thread.CEO_VIA_CONSOLE,
                REFUSED: spoken_thread.REFUSED,
                UNCHECKED: spoken_thread.UNCHECKED,
                UNKNOWN: spoken_thread.UNKNOWN,
                None: spoken_thread.INTEGRATION,
                viewer: spoken_thread.PIPELINE}.get(who, spoken_thread.PERSON)
        out.append(spoken_thread.Voice(kind, f"{kind} label",
                                       node.get("createdAt"), node.get("body")))
    return out


class Board:
    """The injected Linear reads, counted. A thread is the card's window
    unless the test hands it a longer one."""

    def __init__(self, cards=(), threads=None, broken=()):
        self.cards = list(cards)
        self.threads = threads or {}
        self.broken = set(broken)
        self.reads: list = []

    def lane(self, state):
        self.reads.append(("lane", state))
        return list(self.cards) if state == "Green Light" else []

    def thread(self, ident):
        self.reads.append(("thread", ident))
        if ident in self.broken:
            raise RuntimeError("linear error: HTTP 502")
        if ident in self.threads:
            return list(self.threads[ident]), VIEWER
        card = next(c for c in self.cards if c["identifier"] == ident)
        return linear_ops.window_nodes(card["comments"]), VIEWER


class Harness:
    def __init__(self, monkeypatch, board, *, ids=(CEO_ID,), fire_ok=True,
                 voices=fake_voices, comment_raises=None):
        self.board, self.ids, self.fire_ok = board, frozenset(ids), fire_ok
        self.voices = voices
        self.fired: list = []
        self.posted: list = []
        self.order: list = []
        self.comment_raises = comment_raises
        monkeypatch.setattr(linear_ops, "cmd_comment", self._comment)

    def _comment(self, ident, body, *flags):
        self.order.append(("comment", ident))
        if self.comment_raises:
            raise self.comment_raises
        self.posted.append((ident, body))

    def fire(self, card, repo, *, trigger_state=None, reason=None, event=None):
        self.order.append(("fire", card["identifier"]))
        self.fired.append((card["identifier"], repo, trigger_state, reason, event))
        return (True, "") if self.fire_ok else (False, "gh api refused: HTTP 403")

    def sweep(self, *, live=True):
        return glr.sweep(REPO, SLUG, live=live, linear=self.board,
                         fire=self.fire, voices=self.voices, ceo_ids=self.ids)


def _out(capsys):
    got = capsys.readouterr()
    return got.out.splitlines(), got.err.splitlines()


# --------------------------------------------------------------------------- #
# the dispatch and its receipt                                                 #
# --------------------------------------------------------------------------- #


def test_a_verified_console_answer_is_dispatched_once_with_one_receipt(monkeypatch, capsys):
    said_it = answer(30)
    board = Board([lane_card("DRE-7001", window=[comment("📋 the plan", 90), said_it])])
    h = Harness(monkeypatch, board)
    tally = h.sweep()
    assert h.fired == [("DRE-7001", REPO, "green light", "re-run", "agent-plan")]
    assert tally.dispatched == 1
    assert len(h.posted) == 1
    ident, body = h.posted[0]
    assert ident == "DRE-7001"
    assert body.startswith("💬 green-light-reply: ")
    assert f"comment={said_it['createdAt']}" in body
    assert "voice=ceo-via-console" in body
    assert body.endswith("→ review re-run")
    # The receipt goes up only after the dispatch was confirmed.
    assert h.order == [("fire", "DRE-7001"), ("comment", "DRE-7001")]


def test_the_dispatch_is_the_review_rerun_payload_through_plan_run(monkeypatch):
    """No fire injected: the real `plan_run.fire` builds the payload, and
    only the `gh api` call under it is stubbed."""
    sent: list = []

    def fake_run(args, **kwargs):
        sent.append(json.loads(Path(args[args.index("--input") + 1]).read_text()))
        sent[-1]["_endpoint"] = args[2]
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(plan_run.subprocess, "run", fake_run)
    posted: list = []
    monkeypatch.setattr(linear_ops, "cmd_comment",
                        lambda ident, body, *f: posted.append(body))
    board = Board([lane_card("DRE-7001", window=[answer(30)])])
    glr.sweep(REPO, SLUG, live=True, linear=board, voices=fake_voices,
              ceo_ids=frozenset())
    assert len(sent) == 1 and len(posted) == 1
    assert sent[0]["_endpoint"] == f"repos/{REPO}/dispatches"
    assert sent[0]["event_type"] == plan_run.PLAN_EVENT == "agent-plan"
    payload = sent[0]["client_payload"]
    assert payload["trigger_state"] == glr.TRIGGER_STATE_GREEN_LIGHT
    assert payload["reason"] == review_rerun.REASON_RERUN_ACT
    assert payload["identifier"] == "DRE-7001"


def test_the_lane_word_passes_the_real_seams():
    word = glr.TRIGGER_STATE_GREEN_LIGHT
    assert word == "green light" == word.lower()
    assert dedupe_dispatch.lane_left_behind(word, "Green Light") is False
    assert word not in (review_rerun.TRIGGER_STATE_ACTIVATE,
                        review_rerun.TRIGGER_STATE_REVIEW)


def test_the_reason_is_read_off_review_rerun_never_spelled():
    source = (ROOT / "scripts" / "green_light_reply.py").read_text(encoding="utf-8")
    assert "review_rerun.REASON_RERUN_ACT" in source
    assert not re.search(r"""reason\s*=\s*["']re-run["']""", source)
    assert "plan_run.PLAN_EVENT" in source
    assert not re.search(r"""["']agent-plan["']""", source)


# --------------------------------------------------------------------------- #
# once per comment                                                             #
# --------------------------------------------------------------------------- #


def test_the_next_pass_reads_the_receipt_and_does_not_dispatch_again(monkeypatch, capsys):
    said_it = answer(30)
    card = lane_card("DRE-7001", window=[said_it, receipt_for(said_it, 15)])
    h = Harness(monkeypatch, Board([card]))
    tally = h.sweep()
    assert h.fired == [] and h.posted == []
    assert tally.dispatched == 0
    out, _ = _out(capsys)
    assert any("DRE-7001" in line and "answered" in line for line in out)


def test_a_newer_comment_after_the_receipt_is_dispatched_once_more(monkeypatch):
    first = answer(60)
    second = said(10, words="Still unclear on the proof card.")
    card = lane_card("DRE-7001", window=[first, receipt_for(first, 45), second])
    h = Harness(monkeypatch, Board([card]))
    h.sweep()
    assert [f[0] for f in h.fired] == ["DRE-7001"]
    body = h.posted[0][1]
    assert f"comment={second['createdAt']}" in body
    assert "voice=person" in body


def test_a_pass_that_dispatched_then_reads_its_own_receipt(monkeypatch):
    """Two passes over one board: the receipt the first posts is what the
    second reads back."""
    said_it = answer(30)
    window = [said_it]
    h = Harness(monkeypatch, Board([lane_card("DRE-7001", window=window)]))
    h.sweep()
    assert len(h.fired) == 1
    window.append(comment(h.posted[0][1], 5))
    h.board.cards = [lane_card("DRE-7001", window=window)]
    h.sweep()
    assert len(h.fired) == 1


def test_a_pipeline_comment_after_his_does_not_hide_it(monkeypatch):
    said_it = answer(30)
    card = lane_card("DRE-7001", window=[said_it, comment("🔎 critic note", 20),
                                          comment("🤖 agent-actor: planner", 10)])
    h = Harness(monkeypatch, Board([card]))
    h.sweep()
    assert len(h.fired) == 1
    assert f"comment={said_it['createdAt']}" in h.posted[0][1]


def test_a_cycle_start_newer_than_his_comment_means_no_dispatch(monkeypatch, capsys):
    said_it = answer(30)
    card = lane_card("DRE-7001", window=[said_it, cycle_start("DRE-7001", 20)])
    h = Harness(monkeypatch, Board([card]))
    h.sweep()
    assert h.fired == [] and h.posted == []
    out, _ = _out(capsys)
    assert any("DRE-7001" in line and "cycle" in line for line in out)


def test_a_cycle_start_older_than_his_comment_does_not_answer_it(monkeypatch):
    said_it = answer(10)
    card = lane_card("DRE-7001", window=[cycle_start("DRE-7001", 60), said_it])
    h = Harness(monkeypatch, Board([card]))
    h.sweep()
    assert len(h.fired) == 1


def test_a_quoted_cycle_marker_in_prose_does_not_count(monkeypatch):
    said_it = answer(30)
    quoted = comment(f"The run said:\n{plan_critic.cycle_marker('DRE-7001')}\n"
                     "and then stopped.", 20)
    card = lane_card("DRE-7001", window=[said_it, quoted])
    h = Harness(monkeypatch, Board([card]))
    h.sweep()
    assert len(h.fired) == 1


def test_a_cycle_marker_a_person_posted_does_not_count(monkeypatch):
    said_it = answer(30)
    card = lane_card("DRE-7001", window=[said_it,
                                          cycle_start("DRE-7001", 20, SOMEONE)])
    h = Harness(monkeypatch, Board([card]))
    h.sweep()
    assert len(h.fired) == 1


def test_a_cycle_marker_for_another_epic_does_not_count(monkeypatch):
    said_it = answer(30)
    card = lane_card("DRE-7001", window=[said_it, cycle_start("DRE-9999", 20)])
    h = Harness(monkeypatch, Board([card]))
    h.sweep()
    assert len(h.fired) == 1


# --------------------------------------------------------------------------- #
# whose voice counts                                                           #
# --------------------------------------------------------------------------- #


def test_a_person_comment_from_a_declared_ceo_id_qualifies(monkeypatch):
    said_it = said(20)
    h = Harness(monkeypatch, Board([lane_card("DRE-7001", window=[said_it])]))
    h.sweep()
    assert len(h.fired) == 1
    assert f"comment={said_it['createdAt']}" in h.posted[0][1]
    assert "voice=person" in h.posted[0][1]


def test_a_person_comment_from_anyone_else_does_not(monkeypatch, capsys):
    h = Harness(monkeypatch, Board([lane_card("DRE-7001",
                                              window=[said(20, SOMEONE)])]))
    h.sweep()
    assert h.fired == [] and h.posted == []
    _, err = _out(capsys)
    assert [line for line in err if "DRE-7001" in line and "person" in line]


@pytest.mark.parametrize("user, kind", [
    (VIEWER, spoken_thread.PIPELINE),
    (None, spoken_thread.INTEGRATION),
    (UNKNOWN, spoken_thread.UNKNOWN),
    (REFUSED, spoken_thread.REFUSED),
    (UNCHECKED, spoken_thread.UNCHECKED),
])
def test_no_other_voice_counts(monkeypatch, capsys, user, kind):
    # Each written with the CEO's own words, so only the voice decides; the
    # window is partial so the thread is read whatever the window holds.
    node = comment("Answer from Sid: change X and then I'll approve.", 20, user)
    if user in (REFUSED, UNCHECKED):
        node = answer(20)
        node["user"] = {"id": user}
    card = lane_card("DRE-7001", window=[node])
    card["comments"]["pageInfo"]["hasNextPage"] = True
    board = Board([card])
    h = Harness(monkeypatch, board)
    h.sweep()
    assert ("thread", "DRE-7001") in board.reads
    assert h.fired == [] and h.posted == []
    _, err = _out(capsys)
    named = [line for line in err if "DRE-7001" in line]
    assert len(named) == 1, err
    assert kind in named[0]


def test_with_no_viewer_a_declared_id_reads_unknown_and_fails_closed(monkeypatch):
    """The real reader: Linear not naming the viewer makes every comment
    `unknown`, and the declared id does not reach past that."""
    board = Board([lane_card("DRE-7001", window=[said(20)])])
    board.thread = lambda ident: (linear_ops.window_nodes(
        board.cards[0]["comments"]), None)
    h = Harness(monkeypatch, board,
                voices=lambda n, v, *, card: spoken_thread.voices(n, v, card=card))
    h.sweep()
    assert h.fired == []


def test_the_real_reader_labels_a_declared_person_and_it_qualifies(monkeypatch):
    h = Harness(monkeypatch, Board([lane_card("DRE-7001", window=[said(20)])]),
                voices=lambda n, v, *, card: spoken_thread.voices(n, v, card=card))
    h.sweep()
    assert len(h.fired) == 1


def test_the_pairing_is_by_position_and_the_label_is_never_read(monkeypatch):
    nodes = [said(40, SOMEONE, "first"), said(30, CEO_ID, "middle"),
             said(20, "a-third-user", "last")]

    def voices(ns, viewer, *, card):
        kinds = [spoken_thread.PIPELINE, spoken_thread.PERSON,
                 spoken_thread.INTEGRATION]
        # Labels that would mislead any reader of them.
        labels = ["the CEO, in Linear", "the pipeline", "the CEO, via the console"]
        return [spoken_thread.Voice(k, lbl, n["createdAt"], n["body"])
                for k, lbl, n in zip(kinds, labels, ns)]

    h = Harness(monkeypatch, Board([lane_card("DRE-7001", window=nodes)]),
                voices=voices)
    h.sweep()
    assert len(h.fired) == 1
    assert f"comment={nodes[1]['createdAt']}" in h.posted[0][1]


def test_a_console_answers_poster_id_is_never_read(monkeypatch):
    """The signature is the voice: a verified answer qualifies whoever's key
    posted it, and with no ids declared at all."""
    node = answer(20)
    node["user"] = None
    h = Harness(monkeypatch, Board([lane_card("DRE-7001", window=[node])]), ids=(),
                voices=lambda ns, v, *, card: [spoken_thread.Voice(
                    spoken_thread.CEO_VIA_CONSOLE, "x", n["createdAt"], n["body"])
                    for n in ns])
    h.sweep()
    assert len(h.fired) == 1


def test_the_shipped_config_declares_no_ids_and_no_person_qualifies(monkeypatch):
    doc = json.loads((ROOT / "config" / "green-light-reply.json").read_text())
    assert doc["ceo_linear_user_ids"] == []
    assert glr.ceo_user_ids() == frozenset()
    board = Board([lane_card("DRE-7001", window=[said(20)])])
    fired: list = []
    glr.sweep(REPO, SLUG, live=True, linear=board, voices=fake_voices,
              fire=lambda *a, **k: fired.append(a) or (True, ""))
    assert fired == []


def test_a_config_with_ids_is_read_as_a_set(tmp_path):
    path = tmp_path / "green-light-reply.json"
    path.write_text(json.dumps({"ceo_linear_user_ids": [CEO_ID, "  "]}))
    assert glr.ceo_user_ids(path) == frozenset({CEO_ID})


def test_a_malformed_config_raises_rather_than_reading_as_empty(tmp_path):
    path = tmp_path / "green-light-reply.json"
    path.write_text(json.dumps({"ceo_linear_user_ids": "ceo"}))
    with pytest.raises(ValueError):
        glr.ceo_user_ids(path)


# --------------------------------------------------------------------------- #
# the cards never dispatched                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("card", [
    # An epic by its children, so only the PROOF title refuses it.
    lane_card("DRE-7001", title="PROOF: the reply is observed live",
              children=("DRE-7002",), window=[answer(20)]),
    lane_card("DRE-7001", labels=("epic-queued",), window=[answer(20)]),
    lane_card("DRE-7001", title="bureau-pipeline: a one-off", window=[answer(20)]),
    lane_card("DRE-7001", repo="portico", window=[answer(20)]),
], ids=["proof", "epic-queued", "not-an-epic", "another-repo"])
def test_a_card_that_is_not_a_candidate_is_never_dispatched_or_read(monkeypatch, card):
    board = Board([card])
    h = Harness(monkeypatch, board)
    h.sweep()
    assert h.fired == [] and h.posted == []
    assert ("thread", "DRE-7001") not in board.reads


def test_a_card_with_children_is_an_epic_by_the_sweeps_test(monkeypatch):
    card = lane_card("DRE-7001", title="bureau-pipeline: a plan",
                     children=("DRE-7002",), window=[answer(20)])
    h = Harness(monkeypatch, Board([card]))
    h.sweep()
    assert len(h.fired) == 1


# --------------------------------------------------------------------------- #
# the bound, the trailer, the failures                                         #
# --------------------------------------------------------------------------- #


def test_one_dispatch_and_at_most_the_cap_of_thread_reads_per_pass(monkeypatch, capsys):
    cards = [lane_card(f"DRE-70{n:02d}", window=[answer(10 + n)]) for n in range(6)]
    board = Board(cards)
    h = Harness(monkeypatch, board)
    tally = h.sweep()
    assert len(h.fired) == 1 and len(h.posted) == 1
    threads = [r for r in board.reads if r[0] == "thread"]
    assert len(threads) <= glr.GREEN_LIGHT_REPLY_CANDIDATES_PER_PASS
    assert tally.deferred == len(cards) - 1


def test_the_read_cap_holds_when_no_card_qualifies(monkeypatch):
    cards = [lane_card(f"DRE-70{n:02d}", window=[answer(10 + n)],
                       labels=()) for n in range(6)]
    threads = {c["identifier"]: [answer(10), cycle_start(c["identifier"], 5)]
               for c in cards}
    board = Board(cards, threads=threads)
    h = Harness(monkeypatch, board)
    h.sweep()
    assert h.fired == []
    assert len([r for r in board.reads if r[0] == "thread"]) == \
        glr.GREEN_LIGHT_REPLY_CANDIDATES_PER_PASS


def test_the_newest_unanswered_comment_is_read_first(monkeypatch):
    old = lane_card("DRE-7001", window=[answer(300)])
    new = lane_card("DRE-7002", window=[answer(5)])
    h = Harness(monkeypatch, Board([old, new]))
    h.sweep()
    assert [f[0] for f in h.fired] == ["DRE-7002"]


def test_a_card_whose_window_holds_no_comment_of_his_costs_no_read(monkeypatch):
    quiet = lane_card("DRE-7001", window=[comment("📋 the plan", 90),
                                          said(30, SOMEONE)])
    said_it = answer(30)
    answered = lane_card("DRE-7002", window=[said_it, receipt_for(said_it, 10)])
    board = Board([quiet, answered])
    Harness(monkeypatch, board).sweep()
    assert [r for r in board.reads if r[0] == "thread"] == []


def test_a_partial_window_is_read_whole(monkeypatch):
    """Past the window the lane read cannot see his comment: the thread read
    can, and it is the one asked."""
    card = lane_card("DRE-7001", window=[comment("🔎 a note", 5)])
    card["comments"]["pageInfo"]["hasNextPage"] = True
    said_it = answer(600)
    board = Board([card], threads={"DRE-7001": [said_it, comment("🔎 a note", 5)]})
    h = Harness(monkeypatch, board)
    h.sweep()
    assert ("thread", "DRE-7001") in board.reads
    assert f"comment={said_it['createdAt']}" in h.posted[0][1]


def test_the_thread_is_read_whole_through_the_shared_reader(monkeypatch):
    calls: list = []

    def fake(identifier, *needs, whole=False):
        calls.append((identifier, needs, whole))
        return [], VIEWER

    monkeypatch.setattr(linear_ops, "_thread_and_viewer", fake)
    glr.LinearReads().thread("DRE-7001")
    assert calls == [("DRE-7001", ("body", "user", "createdAt"), True)]


def test_a_failed_dispatch_posts_no_receipt_and_is_red(monkeypatch, capsys):
    h = Harness(monkeypatch, Board([lane_card("DRE-7001", window=[answer(20)])]),
                fire_ok=False)
    tally = h.sweep()
    assert h.fired and h.posted == []
    assert tally.failures
    _, err = _out(capsys)
    assert any("HTTP 403" in line for line in err)


def test_a_receipt_that_could_not_be_posted_is_red(monkeypatch):
    h = Harness(monkeypatch, Board([lane_card("DRE-7001", window=[answer(20)])]),
                comment_raises=RuntimeError("linear error: HTTP 500"))
    tally = h.sweep()
    assert len(h.fired) == 1
    assert tally.failures


def test_a_full_issue_that_refuses_the_receipt_is_red(monkeypatch):
    monkeypatch.setattr(linear_ops, "cmd_comment",
                        lambda *a: linear_ops.COMMENT_CAP_CONDITION)
    tally = glr.sweep(REPO, SLUG, live=True,
                      linear=Board([lane_card("DRE-7001", window=[answer(20)])]),
                      fire=lambda *a, **k: (True, ""), voices=fake_voices,
                      ceo_ids=frozenset())
    assert tally.failures


def test_an_unreadable_thread_skips_the_card_and_says_so(monkeypatch, capsys):
    broken = lane_card("DRE-7001", window=[answer(5)])
    fine = lane_card("DRE-7002", window=[answer(20)])
    h = Harness(monkeypatch, Board([broken, fine], broken={"DRE-7001"}))
    tally = h.sweep()
    _, err = _out(capsys)
    assert any("DRE-7001" in line and "HTTP 502" in line for line in err)
    assert [f[0] for f in h.fired] == ["DRE-7002"]
    assert tally.unreadable == 1


def test_a_reader_that_loses_a_comment_is_unreadable_not_empty(monkeypatch, capsys):
    h = Harness(monkeypatch, Board([lane_card("DRE-7001", window=[answer(20)])]),
                voices=lambda ns, v, *, card: [])
    tally = h.sweep()
    assert h.fired == [] and tally.unreadable == 1


# --------------------------------------------------------------------------- #
# the dry run, the environment, the trailer                                    #
# --------------------------------------------------------------------------- #


def test_the_dry_run_writes_nothing_and_says_would_per_qualifying_card(monkeypatch, capsys):
    cards = [lane_card("DRE-7001", window=[answer(10)]),
             lane_card("DRE-7002", window=[answer(20)])]
    h = Harness(monkeypatch, Board(cards))
    h.sweep(live=False)
    assert h.fired == [] and h.posted == []
    out, _ = _out(capsys)
    would = [line for line in out if line.startswith("would:")]
    assert [w for w in would if "DRE-7001" in w] and [w for w in would if "DRE-7002" in w]
    assert len(would) == 2


@pytest.mark.parametrize("value", [None, "", "TRUE", "yes", "1"])
def test_only_exactly_true_is_live(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(glr.LIVE_VARIABLE, raising=False)
    else:
        monkeypatch.setenv(glr.LIVE_VARIABLE, value)
    assert glr.is_live() is False
    monkeypatch.setenv(glr.LIVE_VARIABLE, "true")
    assert glr.is_live() is True


def test_main_runs_the_dry_run_when_the_variable_is_unset(monkeypatch, capsys):
    monkeypatch.setenv("REPO", REPO)
    monkeypatch.setenv("REPO_SLUG", SLUG)
    monkeypatch.delenv(glr.LIVE_VARIABLE, raising=False)
    seen: dict = {}

    def fake_sweep(repo, slug, *, live, **kw):
        seen.update(repo=repo, slug=slug, live=live)
        return glr.Tally()

    monkeypatch.setattr(glr, "sweep", fake_sweep)
    assert glr.main([]) == 0
    assert seen == {"repo": REPO, "slug": SLUG, "live": False}
    out, _ = _out(capsys)
    assert out[-1].startswith("linear-budget:")


def test_main_is_red_on_a_failure_and_still_prints_its_trailer(monkeypatch, capsys):
    monkeypatch.setenv("REPO", REPO)
    monkeypatch.setenv("REPO_SLUG", SLUG)

    def fake_sweep(repo, slug, *, live, **kw):
        tally = glr.Tally()
        tally.failures.append("DRE-7001: gh api refused")
        return tally

    monkeypatch.setattr(glr, "sweep", fake_sweep)
    assert glr.main([]) == 1
    out, err = _out(capsys)
    assert out[-1].startswith("linear-budget:")
    assert any("gh api refused" in line for line in err)


def test_main_prints_its_trailer_when_the_sweep_raises(monkeypatch, capsys):
    monkeypatch.setenv("REPO", REPO)
    monkeypatch.setenv("REPO_SLUG", SLUG)

    def boom(*a, **k):
        raise RuntimeError("linear error: HTTP 500")

    monkeypatch.setattr(glr, "sweep", boom)
    with pytest.raises(RuntimeError):
        glr.main([])
    out, _ = _out(capsys)
    assert out[-1].startswith("linear-budget:")


@pytest.mark.parametrize("unset", ["REPO", "REPO_SLUG"])
def test_without_repo_or_slug_nothing_is_read_and_it_exits_2(unset):
    """In its own process, as the step runs it: `reconcile` reads `REPO` at
    import, so a missing one must be answered before anything imports it."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("REPO", "REPO_SLUG", "LINEAR_API_KEY")}
    env.update({"REPO": REPO, "REPO_SLUG": SLUG, "LINEAR_API_KEY": "test"})
    del env[unset]
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "green_light_reply.py")],
                          env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 2, done.stderr
    assert len([line for line in done.stderr.splitlines() if line.strip()]) == 1
    assert "REPO" in done.stderr
    last = done.stdout.splitlines()[-1]
    assert last.startswith("linear-budget:")
    # Nothing was read: no rate-limit headers were ever seen.
    assert "unknown" in last, last


# --------------------------------------------------------------------------- #
# the receipt and the act registry                                             #
# --------------------------------------------------------------------------- #


CONSOLE_MARKS = ("🔁", "🔓", "🚨", "🚧", "🩺", "🔬")


def test_the_receipt_is_one_line_with_no_trailer_and_no_console_mark():
    body = glr.reply_receipt("2026-10-07T16:30:00.000Z", "ceo-via-console")
    assert "\n" not in body
    assert body.startswith("💬 green-light-reply: ")
    assert body == ("💬 green-light-reply: comment=2026-10-07T16:30:00.000Z "
                    "voice=ceo-via-console at=2026-10-07 09:30 PT → review re-run")
    assert f"{pipeline_act.TRAILER_MARK} {pipeline_act.TRAILER_TAG}:" not in body
    assert pipeline_act.read_trailer(body) is None
    for line in body.splitlines():
        assert not line.lstrip().startswith(CONSOLE_MARKS)


def test_the_posted_receipt_has_the_same_shape(monkeypatch):
    h = Harness(monkeypatch, Board([lane_card("DRE-7001", window=[answer(20)])]))
    h.sweep()
    body = h.posted[0][1]
    assert len(body.splitlines()) == 1
    assert body.startswith("💬 green-light-reply:")
    assert "📎 pipeline-act:" not in body
    assert not body.lstrip().startswith(CONSOLE_MARKS)


def test_the_receipt_is_posted_by_one_call_with_the_body_built_inside_it():
    source = (ROOT / "scripts" / "green_light_reply.py").read_text(encoding="utf-8")
    calls = re.findall(r"linear_ops\.cmd_comment\((.*)\)", source)
    assert len(calls) == 1, calls
    assert "reply_receipt(" in calls[0]


def test_the_act_registry_declares_the_receipt_as_an_undeclared_act():
    doc = json.loads((ROOT / "config" / "pipeline-acts.json").read_text())
    rows = [u for u in doc["unconverted"]
            if u["file"] == "scripts/green_light_reply.py"]
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["anchor"] == "reply_receipt("
    assert row["kind"] == "undeclared-act"
    assert row["means"].strip() and row["why"].strip()
    for act in doc["acts"]:
        assert "green-light-reply" not in act.get("name", "")
        assert "green-light-reply" not in act.get("tag", "")


# --------------------------------------------------------------------------- #
# the step in reconcile.yml                                                    #
# --------------------------------------------------------------------------- #


def _steps() -> list:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"]["sweep"]["steps"]


def _step() -> dict:
    found = [s for s in _steps() if s.get("name") == STEP]
    assert len(found) == 1, f"{STEP!r} is not one step of reconcile.yml"
    return found[0]


def test_the_step_follows_the_proof_dispatch_and_runs_only_on_a_full_pass():
    names = [s.get("name") for s in _steps()]
    assert names.index(STEP) == names.index("Dispatch proof runs") + 1
    proof = next(s for s in _steps() if s.get("name") == "Dispatch proof runs")
    assert _step()["if"] == proof["if"] == "inputs.sweep_reason == ''"


def test_the_step_carries_the_proof_steps_env_and_its_own_switch():
    proof = next(s for s in _steps() if s.get("name") == "Dispatch proof runs")
    env = _step()["env"]
    for name in ("LINEAR_API_KEY", "GH_TOKEN", "GH_READ_TOKEN", "REPO",
                 "BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
                 "BUREAU_PIPELINE_REF"):
        assert env[name] == proof["env"][name], name
    assert env["REPO"] == "${{ github.repository }}"
    assert env["GREEN_LIGHT_REPLY_LIVE"] == "${{ vars.GREEN_LIGHT_REPLY_LIVE }}"
    run = _step()["run"]
    derive = 'REPO_SLUG=$(basename "$GITHUB_REPOSITORY" | tr \'[:upper:]\' \'[:lower:]\')'
    assert derive in run and derive in proof["run"]
    assert "export REPO_SLUG" in run
    assert "python3 .bureau-pipeline/scripts/green_light_reply.py" in run


def _run_step(tmp_path, status=0):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    seen = tmp_path / "python-env"
    fake = bin_dir / "python3"
    fake.write_text(f"#!/bin/sh\necho \"$@ $REPO_SLUG\" > {seen}\n"
                    "echo 'would: dispatch DRE-7001 — fixture'\n"
                    "echo 'green-light-reply: green light 1, dispatched 0'\n"
                    "echo 'linear-budget: fixture'\n"
                    f"exit {status}\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    work = tmp_path / "work"
    work.mkdir()
    summary = tmp_path / "summary.md"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "GITHUB_REPOSITORY": "dreadnought-foundry/Portico",
           "GITHUB_STEP_SUMMARY": str(summary)}
    done = subprocess.run(["bash", "-e", "-c", _step()["run"]], cwd=work, env=env,
                          capture_output=True, text=True)
    return done, seen.read_text(encoding="utf-8"), summary


def test_the_step_runs_the_phase_in_its_own_process_and_lifts_its_lines(tmp_path):
    done, seen, summary = _run_step(tmp_path)
    assert done.returncode == 0, done.stderr
    assert seen.strip() == ".bureau-pipeline/scripts/green_light_reply.py portico"
    text = summary.read_text(encoding="utf-8")
    assert "linear-budget: fixture" in text
    assert "would: dispatch DRE-7001" in text
    assert "green-light-reply: green light 1" in text


def test_the_step_carries_the_phases_status_past_the_summary(tmp_path):
    done, _, summary = _run_step(tmp_path, status=1)
    assert done.returncode == 1
    assert "linear-budget: fixture" in summary.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# the critic's sentence in plan.yml                                            #
# --------------------------------------------------------------------------- #


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_the_second_critic_reads_an_unanswered_ceo_comment_as_a_finding():
    prompt = prompt_of(CRITIC_STEP)
    assert CRITIC_SENTENCE in _flat(prompt)
    # Directly under its Thread: line.
    lines = prompt.splitlines()
    thread = next(i for i, line in enumerate(lines)
                  if line.strip().startswith("Thread:"))
    assert _flat("\n".join(lines[thread + 1:thread + 6])).startswith(
        CRITIC_SENTENCE[:40])


def test_the_sentence_is_in_no_other_step():
    carriers = [s.get("name") for s in plan_steps()
                if CRITIC_SENTENCE[:60] in _flat(yaml.safe_dump(s, width=10**6))
                or CRITIC_SENTENCE[:60] in _flat(str((s.get("with") or {}).get("prompt") or ""))]
    assert carriers == [CRITIC_STEP]
