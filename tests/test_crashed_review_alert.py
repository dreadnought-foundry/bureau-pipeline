"""A review stuck at its retry cap raises a console alert naming its card
(DRE-8690).

On 2026-11-01 two reviews that died behind the Claude limit stayed stuck for
two hours after the limit lifted. The sweep wrote "the reviewer is down and a
human must fix it" into its log every fifteen minutes, and the only console
alert, the fleet's `reviewer_outage:fleet`, names no card — so the open-alerts
card rule gave it `card: null` and nothing could act on it. Each stuck review
is now its own WARN alert, keyed by its card. What these pin:

* the raise: the key, WARN, no fallback card, a title that opens with the
  card, the PR and the short head (the open-alerts read serves the title, and
  `settle` reads the PR and head back from it), and a body naming the PR, the
  crashed run and the operator's `gh run rerun … --failed`;
* the resolve, only on positive evidence the review is no longer stuck — the
  PR closed or merged, its head moved, or a verdict bound to that head — and
  never because a head is merely absent from this pass's capped list;
* another repo's alerts, a failed PR read, a pull request this pass could not
  read whole, and an unreadable open-alerts read all resolve nothing.

No test here reaches the console or GitHub: the notice client and the
open-alerts read are replaced at the module boundary, and the pull requests
are dictionaries.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import bureau_read  # noqa: E402
import crashed_review_alert as cra  # noqa: E402
import pipeline_notice  # noqa: E402

REPO = "dreadnought-foundry/agent-bureau"
SLUG = "agent-bureau"
HEAD_4472 = "1cd61cfb49817d1bb420487e8434e76ed6389275"
HEAD_4462 = "887e486b17fdef87ed3f4d8d312ce6e41fd4fa9d"
STUCK_4472 = cra.Capped(card="DRE-8553", pr=4472, head=HEAD_4472, run_id="41349671195")
STUCK_4462 = cra.Capped(card="DRE-8667", pr=4462, head=HEAD_4462, run_id="41346471815")


class Door:
    """The notice client and the open-alerts read, as `settle` sees them."""

    def __init__(self, *, open_alerts=(), outcome="raised", unreadable=None,
                 raise_fails=None):
        self.raised: list[dict] = []
        self.resolved: list[str] = []
        self.outcome = outcome
        self.alerts = [dict(a) for a in open_alerts]
        self.unreadable = unreadable
        self.raise_fails = raise_fails

    def raise_notice(self, notice, **kw):
        if self.raise_fails is not None:
            raise self.raise_fails
        self.raised.append({"notice": notice, **kw})
        return self.outcome

    def resolve_notice(self, notice, **kw):
        self.resolved.append(notice)
        return "resolved"

    def open_alerts(self):
        if self.unreadable is not None:
            raise self.unreadable
        return {"schema": "bureau-read/1", "read_at": "2026-11-01T21:30:00Z",
                "alerts": self.alerts}


@pytest.fixture
def door(monkeypatch):
    def make(**kw):
        fake = Door(**kw)
        monkeypatch.setattr(pipeline_notice, "raise_notice", fake.raise_notice)
        monkeypatch.setattr(pipeline_notice, "resolve_notice", fake.resolve_notice)
        monkeypatch.setattr(bureau_read, "open_alerts", fake.open_alerts)
        return fake
    return make


def alert(stuck: cra.Capped, slug: str = SLUG) -> dict:
    """The open alert as the door serves it: the title `settle` raised."""
    return {"kind": f"pipeline_notice:reviewer-down:{slug}:{stuck.card}",
            "severity": "WARN", "title": cra.title(stuck),
            "raised_at": "2026-11-01T20:18:00Z", "card": stuck.card}


def pr(number, head, *, comments=(), state="OPEN"):
    return {"number": number, "headRefName": f"agent/DRE-{number}-x",
            "headRefOid": head, "state": state, "comments": list(comments)}


def settle(capped, *, prs=(), read=None, bound=lambda p: False, unread=()):
    reads: list[int] = []

    def read_pr(number):
        reads.append(number)
        if read is None:
            raise AssertionError(f"PR #{number} read, but it is in the listing")
        answer = read(number)
        if isinstance(answer, Exception):
            raise answer
        return answer

    lines = cra.settle(capped, SLUG, unread, repo=REPO, prs=list(prs),
                       read_pr=read_pr, bound=bound)
    return lines, reads


# ── the raise ───────────────────────────────────────────────────────────────


def test_a_capped_head_raises_its_cards_alert_at_warn_with_no_fallback(door):
    fake = door()
    settle([STUCK_4472], prs=[pr(4472, HEAD_4472)])
    assert len(fake.raised) == 1
    raised = fake.raised[0]
    assert raised["notice"] == "reviewer-down:agent-bureau:DRE-8553"
    assert raised["severity"] == "WARN"
    assert "fallback" in raised and raised["fallback"] is None
    assert raised["title"].startswith("DRE-8553: PR #4472 @1cd61cfb —")
    assert raised["action_url"] == "https://github.com/dreadnought-foundry/agent-bureau/pull/4472"


def test_the_body_names_the_pr_the_run_and_the_operators_remedy(door):
    fake = door()
    settle([STUCK_4472], prs=[pr(4472, HEAD_4472)])
    body = fake.raised[0]["body"]
    assert "https://github.com/dreadnought-foundry/agent-bureau/pull/4472" in body
    assert "1cd61cfb" in body
    assert ("https://github.com/dreadnought-foundry/agent-bureau/actions/runs/41349671195"
            in body)
    assert ("gh run rerun 41349671195 --failed --repo dreadnought-foundry/agent-bureau"
            in body)
    assert "not a code rejection" in body


def test_a_run_nobody_could_read_is_said_not_guessed(door):
    fake = door()
    settle([cra.Capped("DRE-8553", 4472, HEAD_4472, "")], prs=[pr(4472, HEAD_4472)])
    body = fake.raised[0]["body"]
    assert "/actions/runs/" not in body
    assert "could not be read" in body
    assert "--failed --repo dreadnought-foundry/agent-bureau" in body


def test_one_alert_per_card_each_pass(door):
    fake = door()
    other = cra.Capped("DRE-8553", 4480, "d" * 40, "1")
    settle([STUCK_4472, other, STUCK_4462],
           prs=[pr(4472, HEAD_4472), pr(4480, "d" * 40), pr(4462, HEAD_4462)])
    assert [r["notice"] for r in fake.raised] == [
        "reviewer-down:agent-bureau:DRE-8553", "reviewer-down:agent-bureau:DRE-8667"]


def test_a_raise_the_door_cannot_take_is_logged_and_never_raised_past(door, capsys):
    door(raise_fails=RuntimeError("the door is down"))
    lines, _ = settle([STUCK_4472], prs=[pr(4472, HEAD_4472)])
    assert any("DRE-8553" in line and "the door is down" in line for line in lines)


def test_a_lost_raise_is_said(door):
    door(outcome="lost")
    lines, _ = settle([STUCK_4472], prs=[pr(4472, HEAD_4472)])
    assert f"{cra.TAG} lost card=DRE-8553" in lines


# ── the resolve: positive evidence only ─────────────────────────────────────


def test_a_merged_pr_resolves_its_alert(door):
    fake = door(open_alerts=[alert(STUCK_4472)])
    _, reads = settle([], prs=[], read=lambda n: pr(n, HEAD_4472, state="MERGED"))
    assert reads == [4472], "a PR missing from the listing is read once"
    assert fake.resolved == ["reviewer-down:agent-bureau:DRE-8553"]


def test_a_closed_pr_resolves_its_alert(door):
    fake = door(open_alerts=[alert(STUCK_4472)])
    settle([], prs=[], read=lambda n: pr(n, HEAD_4472, state="CLOSED"))
    assert fake.resolved == ["reviewer-down:agent-bureau:DRE-8553"]


def test_a_head_that_moved_resolves_its_alert(door):
    fake = door(open_alerts=[alert(STUCK_4472)])
    _, reads = settle([], prs=[pr(4472, "e" * 40)])
    assert reads == [], "a PR in the listing costs no read"
    assert fake.resolved == ["reviewer-down:agent-bureau:DRE-8553"]


def test_a_verdict_bound_to_the_head_resolves_its_alert(door):
    fake = door(open_alerts=[alert(STUCK_4472)])
    asked = []
    settle([], prs=[pr(4472, HEAD_4472)],
           bound=lambda p: asked.append(p["number"]) or True)
    assert asked == [4472]
    assert fake.resolved == ["reviewer-down:agent-bureau:DRE-8553"]


def test_skipped_is_not_recovered(door):
    """MUTATION CHECK: resolve every alert whose head is off this pass's
    capped list and this goes red. The head is absent because a review
    dispatch was in flight — still open, same head, no verdict — so the alert
    stands, and the Alarm Repair's two-hour clock is not restarted."""
    fake = door(open_alerts=[alert(STUCK_4472)])
    lines, _ = settle([], prs=[pr(4472, HEAD_4472)])
    assert fake.resolved == []
    assert any("still stuck" in line for line in lines)


def test_a_card_on_the_capped_list_is_never_resolved(door):
    """The same card, a new head at the cap: the raise refreshes the alert,
    and the old title's head having moved does not resolve it."""
    fake = door(open_alerts=[alert(cra.Capped("DRE-8553", 4472, "e" * 40, "1"))])
    settle([STUCK_4472], prs=[pr(4472, HEAD_4472)])
    assert fake.resolved == []
    assert len(fake.raised) == 1


def test_another_repos_alerts_are_left_alone(door):
    fake = door(open_alerts=[alert(STUCK_4472, slug="portico")])
    settle([], prs=[], read=lambda n: pr(n, HEAD_4472, state="MERGED"))
    assert fake.resolved == []


def test_a_failed_pr_read_resolves_nothing_for_that_alert(door):
    fake = door(open_alerts=[alert(STUCK_4472), alert(STUCK_4462)])
    lines, _ = settle([], prs=[pr(4462, "f" * 40)],
                      read=lambda n: RuntimeError("HTTP 502: Bad Gateway"))
    assert fake.resolved == ["reviewer-down:agent-bureau:DRE-8667"], (
        "the readable alert is still resolved; the unreadable one is not")
    assert any("DRE-8553" in line and "unreadable" in line for line in lines)


def test_a_verdict_read_that_fails_resolves_nothing(door):
    def bound(p):
        raise RuntimeError("compare read refused")

    fake = door(open_alerts=[alert(STUCK_4472)])
    settle([], prs=[pr(4472, HEAD_4472)], bound=bound)
    assert fake.resolved == []


def test_a_pr_this_pass_could_not_read_whole_resolves_nothing(door):
    fake = door(open_alerts=[alert(STUCK_4472)])
    settle([], prs=[pr(4472, "e" * 40)], unread=[4472])
    assert fake.resolved == []


def test_unreadable_open_alerts_resolve_nothing(door):
    fake = door(open_alerts=[alert(STUCK_4472)],
                unreadable=bureau_read.ReadUnknown("door answered 503"))
    lines, _ = settle([STUCK_4462], prs=[pr(4462, HEAD_4462)])
    assert fake.resolved == []
    assert len(fake.raised) == 1, "the raise half still runs"
    assert any("open alerts unreadable" in line for line in lines)


def test_a_title_that_names_no_pr_resolves_nothing(door):
    row = alert(STUCK_4472)
    row["title"] = "DRE-8553 something else entirely"
    fake = door(open_alerts=[row])
    settle([], prs=[])
    assert fake.resolved == []


def test_the_title_reads_back_its_pr_and_head():
    assert cra.parse_title(cra.title(STUCK_4472)) == (4472, "1cd61cfb")
    assert cra.parse_title("DRE-8553 is keeping the sweep red") is None
