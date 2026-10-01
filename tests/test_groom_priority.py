"""RED-first: an Urgent or High priority older than 21 days that nobody has
re-confirmed is ranked as Medium, and the proposal says so (DRE-5307).

On 2026-09-29 five cards marked Urgent in August — DRE-2702, DRE-2563,
DRE-2564, DRE-3681, DRE-3530, 37 to 40 days old — opened the batch every
morning ahead of that week's work, because `groomer._priority` read the Linear
field with no notion of when it was set. `groom_priority.annotate` reads, for
the few cards it could re-rank, when the priority was set and whether anybody
has said it still holds since:

  * a newer history entry setting the same priority again, or
  * a `priority-confirmed` comment by a PERSON — another Linear user's own
    account, or the CEO through the console (his answer, posted on the
    pipeline's own key, whose signature `spoken_thread.voices` checks). The
    pipeline's key without a receipt, an integration, a refused or unchecked
    receipt, and anything when the viewer is unknown do not count.

What it asks costs at most `MAX_READS` requests a morning, oldest first, and
nothing on a morning with no Urgent or High card older than three weeks. A
card it did not read keeps its band and is named.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_priority.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import console_receipt  # noqa: E402
import groom_priority  # noqa: E402
import groom_verify_agent  # noqa: E402
import groomer  # noqa: E402
import linear_ops  # noqa: E402

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
FLEET = "user-agent-bureau"
SOMEONE = "user-frederick"


def ago(days: float, *, minutes: float = 0) -> str:
    moment = NOW - timedelta(days=days, minutes=minutes)
    return moment.isoformat().replace("+00:00", "Z")


def card(identifier, *, priority=1, days=40, minutes=0):
    return {
        "identifier": identifier,
        "title": f"{identifier} does a thing",
        "description": "",
        "createdAt": ago(days, minutes=minutes),
        "priority": priority,
        "state": {"name": "Intake"},
        "labels": {"nodes": [{"name": "repo:portico"},
                             {"name": "agent:engineer"}]},
        "parent": None,
        "project": None,
        "cycle": None,
        "inverseRelations": {"nodes": []},
    }


def set_to(priority, days, *, before=0):
    return {"createdAt": ago(days), "fromPriority": before,
            "toPriority": priority}


def comment(body, days, *, by=SOMEONE):
    return {"body": body, "createdAt": ago(days),
            "user": {"id": by} if by else None, "botActor": None}


def console_answer(words, *, card_id, days):
    """The console's answer shape on the pipeline's key — the heading, his
    words and a well-formed trailer. Whether the signature holds is the fake
    verifier's to say."""
    trailer = console_receipt.answer_trailer(
        card=card_id, sha256="0" * 64, user="ceo-console-user",
        at=ago(days).replace(".000", "")[:19] + "Z", kid="0" * 16,
        sig="A" * 86)
    body = ("Answer from Sid (signed in to the console), 2026-09-26 09:00 PT:"
            f"\n\n{words}\n\n{trailer}")
    return comment(body, days, by=FLEET)


class FakeLinear:
    """The two reads `annotate` makes, counted."""

    def __init__(self, threads=None, *, viewer=FLEET, fail=None):
        self.threads = threads or {}
        self.viewer = viewer
        self.fail = fail or {}
        self.asked: list[str] = []
        self.viewer_reads = 0

    def viewer_id(self):
        self.viewer_reads += 1
        return self.viewer

    def gql(self, query, variables=None):
        identifier = (variables or {}).get("id")
        self.asked.append(identifier)
        if identifier in self.fail:
            raise RuntimeError(self.fail[identifier])
        history, comments = self.threads.get(identifier, ([], []))
        return {"issue": {"history": {"nodes": history},
                          "comments": {"nodes": comments}}}


class FakeVerifier:
    """`console_receipt.Verifier`, answering every receipt one way."""

    def __init__(self, why=None):
        self.why = why
        self.checked = 0

    def check_answer(self, body, *, card, created_at):
        self.checked += 1
        return self.why


def annotate(cards, linear, verifier=None):
    return groom_priority.annotate(cards, lops=linear, now=NOW,
                                   verifier=verifier)


def order(cards):
    return [row["identifier"] for row in groomer.sequence(cards)]


def bands(cards):
    return {row["identifier"]: row["band"] for row in groomer.sequence(cards)}


# --------------------------------------------------------------------------
# the contract
# --------------------------------------------------------------------------
def test_the_contract_constants():
    assert groom_priority.STALE_DAYS == 21
    assert groom_priority.CONFIRM_MARKER == "priority-confirmed"
    assert groom_priority.MAX_READS == 40


def test_the_unread_viewer_string_is_the_verify_runners_byte_for_byte():
    """Duplicated on purpose — `groom_priority` must not import the verify
    runner — so the two are held equal here."""
    assert (groom_priority.VIEWER_UNREAD
            == groom_verify_agent.VIEWER_UNREAD
            == "the pipeline's own Linear identity could not be read")


# --------------------------------------------------------------------------
# stale: ranked in the Medium band
# --------------------------------------------------------------------------
def test_an_urgent_set_38_days_ago_and_never_confirmed_sequences_as_medium():
    old = card("DRE-2702", priority=1, days=40)
    today = card("DRE-5400", priority=3, days=0)
    assert order([old, today]) == ["DRE-2702", "DRE-5400"], (
        "before the annotation the August Urgent opens the batch")
    linear = FakeLinear({"DRE-2702": ([set_to(1, 38)], [])})
    found = annotate([old, today], linear)
    assert found == {"stale": ["DRE-2702"], "unread": {}}
    assert old["priority_stale"] == {"priority": "Urgent",
                                     "set_at": ago(38), "days": 38}
    assert "priority_stale" not in today
    assert order([old, today]) == ["DRE-5400", "DRE-2702"]
    assert bands([old, today])["DRE-2702"] == groomer.BAND_OLDER


def test_with_no_priority_entry_the_priority_dates_from_the_cards_creation():
    old = card("DRE-2563", priority=1, days=39)
    annotate([old], FakeLinear({"DRE-2563": ([], [])}))
    assert old["priority_stale"] == {"priority": "Urgent",
                                     "set_at": ago(39), "days": 39}


def test_the_newest_entry_setting_the_current_priority_is_the_one_read():
    old = card("DRE-2564", priority=2, days=60)
    history = [set_to(1, 50), set_to(2, 30, before=1), set_to(3, 45)]
    annotate([old], FakeLinear({"DRE-2564": (history, [])}))
    assert old["priority_stale"] == {"priority": "High", "set_at": ago(30),
                                     "days": 30}


# --------------------------------------------------------------------------
# re-confirmed: keeps its band
# --------------------------------------------------------------------------
def test_set_to_urgent_again_3_days_ago_keeps_its_band():
    old = card("DRE-3681", priority=1, days=40)
    history = [set_to(1, 38), set_to(2, 10, before=1),
               set_to(1, 3, before=2)]
    found = annotate([old], FakeLinear({"DRE-3681": (history, [])}))
    assert found == {"stale": [], "unread": {}}
    assert "priority_stale" not in old
    assert bands([old])["DRE-3681"] == groomer.BAND_URGENT


def test_a_person_confirming_3_days_ago_keeps_its_band():
    old = card("DRE-3530", priority=1, days=40)
    comments = [comment("✅ Priority-Confirmed — still the first thing", 3)]
    found = annotate([old], FakeLinear(
        {"DRE-3530": ([set_to(1, 38)], comments)}))
    assert found["stale"] == []
    assert "priority_stale" not in old


def test_the_ceos_console_answer_confirming_3_days_ago_keeps_its_band():
    """His answer is posted on the pipeline's own key, so "not the viewer"
    would throw it away; the receipt is what makes it his."""
    old = card("DRE-2702", priority=1, days=40)
    answer = console_answer("priority-confirmed", card_id="DRE-2702", days=3)
    verifier = FakeVerifier(why=None)
    found = annotate([old], FakeLinear(
        {"DRE-2702": ([set_to(1, 38)], [answer])}), verifier=verifier)
    assert verifier.checked == 1, "the verifier is handed through unchanged"
    assert found["stale"] == []
    assert "priority_stale" not in old


def test_the_pipelines_key_without_a_receipt_does_not_confirm():
    old = card("DRE-2702", priority=1, days=40)
    comments = [comment("priority-confirmed", 3, by=FLEET)]
    found = annotate([old], FakeLinear(
        {"DRE-2702": ([set_to(1, 38)], comments)}))
    assert found["stale"] == ["DRE-2702"]


def test_a_receipt_the_verifier_refuses_does_not_confirm():
    old = card("DRE-2702", priority=1, days=40)
    answer = console_answer("priority-confirmed", card_id="DRE-2702", days=3)
    verifier = FakeVerifier(why="its signature does not verify")
    found = annotate([old], FakeLinear(
        {"DRE-2702": ([set_to(1, 38)], [answer])}), verifier=verifier)
    assert verifier.checked == 1
    assert found["stale"] == ["DRE-2702"]


def test_an_integration_comment_does_not_confirm():
    old = card("DRE-2702", priority=1, days=40)
    comments = [comment("priority-confirmed", 3, by=None)]
    found = annotate([old], FakeLinear(
        {"DRE-2702": ([set_to(1, 38)], comments)}))
    assert found["stale"] == ["DRE-2702"]


def test_a_confirmation_older_than_three_weeks_does_not_count():
    old = card("DRE-2702", priority=1, days=40)
    comments = [comment("priority-confirmed", 25)]
    found = annotate([old], FakeLinear(
        {"DRE-2702": ([set_to(1, 38)], comments)}))
    assert found["stale"] == ["DRE-2702"]


def test_the_marker_must_open_the_first_line():
    old = card("DRE-2702", priority=1, days=40)
    comments = [comment("Is this still urgent?\npriority-confirmed", 3),
                comment("I have not priority-confirmed this", 2)]
    found = annotate([old], FakeLinear(
        {"DRE-2702": ([set_to(1, 38)], comments)}))
    assert found["stale"] == ["DRE-2702"]


# --------------------------------------------------------------------------
# the boundary
# --------------------------------------------------------------------------
def test_a_high_set_20_days_ago_is_not_stale_and_22_days_ago_is():
    fresh = card("DRE-4001", priority=2, days=30)
    old = card("DRE-4002", priority=2, days=30)
    found = annotate([fresh, old], FakeLinear({
        "DRE-4001": ([set_to(2, 20)], []),
        "DRE-4002": ([set_to(2, 22)], []),
    }))
    assert found == {"stale": ["DRE-4002"], "unread": {}}
    assert "priority_stale" not in fresh
    assert old["priority_stale"] == {"priority": "High", "set_at": ago(22),
                                     "days": 22}


# --------------------------------------------------------------------------
# what is asked, and what is not
# --------------------------------------------------------------------------
def test_a_failed_read_keeps_the_band_and_names_the_card():
    old = card("DRE-2702", priority=1, days=40)
    linear = FakeLinear(fail={"DRE-2702": "Linear said no\nsecond line"})
    found = annotate([old], linear)
    assert found == {"stale": [], "unread": {"DRE-2702": "Linear said no"}}
    assert "priority_stale" not in old
    assert bands([old])["DRE-2702"] == groomer.BAND_URGENT


def test_no_request_for_a_medium_low_or_unset_card_or_a_young_urgent():
    cards = [card("DRE-1", priority=3), card("DRE-2", priority=4),
             card("DRE-3", priority=0), card("DRE-4", priority=None),
             card("DRE-5", priority=1, days=10),
             card("DRE-6", priority=2, days=40)]
    linear = FakeLinear()
    annotate(cards, linear)
    assert linear.asked == ["DRE-6"]


def test_no_candidate_no_request_and_no_viewer_read():
    linear = FakeLinear()
    found = annotate([card("DRE-5", priority=1, days=10),
                      card("DRE-1", priority=3, days=90)], linear)
    assert found == {"stale": [], "unread": {}}
    assert linear.asked == [] and linear.viewer_reads == 0


def test_the_viewer_is_read_once_per_morning():
    linear = FakeLinear()
    annotate([card(f"DRE-{n}", priority=1, days=30 + n) for n in range(5)],
             linear)
    assert linear.viewer_reads == 1
    assert len(linear.asked) == 5


def test_41_candidates_make_exactly_40_requests_oldest_first():
    cards = [card(f"DRE-{900 + n}", priority=1, days=30, minutes=n)
             for n in range(41)]
    # Created a minute apart: DRE-900 is the youngest, DRE-940 the oldest.
    youngest = cards[0]
    linear = FakeLinear()
    found = annotate(cards, linear)
    assert len(linear.asked) == groom_priority.MAX_READS == 40
    assert linear.asked == [f"DRE-{900 + n}" for n in range(40, 0, -1)]
    why = f"read budget of {groom_priority.MAX_READS} spent"
    assert why == "read budget of 40 spent"
    assert found["unread"] == {youngest["identifier"]: why}
    assert len(found["stale"]) == 40
    assert "priority_stale" not in youngest
    assert bands(cards)[youngest["identifier"]] == groomer.BAND_URGENT
    assert order(cards)[0] == youngest["identifier"]


def test_an_unknown_viewer_reads_nothing_and_demotes_nothing(monkeypatch):
    asked = []

    def gql(query, variables=None):
        asked.append((variables or {}).get("id"))
        raise AssertionError("no request may be made without the viewer")

    monkeypatch.setattr(linear_ops, "viewer_id", lambda: None)
    monkeypatch.setattr(linear_ops, "gql", gql)
    cards = [card(f"DRE-{n}", priority=1 + n % 2, days=40 + n)
             for n in range(3)]
    found = groom_priority.annotate(cards, lops=linear_ops, now=NOW)
    assert asked == []
    assert found == {"stale": [], "unread": {
        c["identifier"]: "the pipeline's own Linear identity could not be read"
        for c in cards}}
    assert not any("priority_stale" in c for c in cards)
    assert bands(cards) == {"DRE-0": groomer.BAND_URGENT,
                            "DRE-1": groomer.BAND_HIGH,
                            "DRE-2": groomer.BAND_URGENT}


def test_a_viewer_read_that_raises_is_an_unknown_viewer():
    class Raising(FakeLinear):
        def viewer_id(self):
            raise RuntimeError("Linear is down")

    linear = Raising()
    found = annotate([card("DRE-2702")], linear)
    assert linear.asked == []
    assert found["unread"] == {"DRE-2702": groom_priority.VIEWER_UNREAD}


def test_now_may_be_an_iso_string():
    old = card("DRE-2702", priority=1, days=40)
    found = groom_priority.annotate(
        [old], lops=FakeLinear({"DRE-2702": ([set_to(1, 38)], [])}),
        now=NOW.isoformat().replace("+00:00", "Z"))
    assert found["stale"] == ["DRE-2702"]
