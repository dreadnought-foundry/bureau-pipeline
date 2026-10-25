"""RED-first: the check before posting can read every card on offer for
dead-card evidence, not only the Planning list and the ten spares behind it
(DRE-7969).

On 2026-10-23, 24 and 25 the lane held 79, 78 and 68 cards, 63 to 74 of them
on offer, and the check read at most 30. The rest sat on the `not-now`,
`not-ready` and `rewrite` lists with nothing looking at them for evidence that
they were already done — 82 dead Intake cards were closed by hand over
thirteen mornings (DRE-6643).

What this file holds:

  * `sweep_window` is every card on those three lists outside the window and
    off the Cancel list, `not-now` in sequence order, then `not-ready` and
    `rewrite` by position, each card once;
  * `check(..., sweep=True)` reads them with the same three reads, and a swept
    card with evidence goes to `cancel` holding no slot — the Planning walk
    is unchanged;
  * a swept source that could not be read is `unread`, never `clean`, and an
    unread swept card is never cancelled;
  * with `sweep=False`, the default, the check answers byte for byte what it
    answered before this card (`tests/fixtures/groom-verify-sweep-off.json`,
    written by the check as it stood on `main` before the change);
  * `settle` writes `swept` and `swept_to_cancel`, and `unread_result` marks
    every swept card unread when the check failed outright.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_verify_sweep.py -v
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groom_context  # noqa: E402
import groom_verify  # noqa: E402
import groomer  # noqa: E402

from test_groom_verify import (  # noqa: E402
    FLEET, LANE, OWNERS, SUPERSEDED, FakeGh, FakeLinear, _lane_with_cancel,
    comment, other, owner_token, pr)
from test_groomer import CYCLES, NOW  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "groom-verdict-rows-3381b8d562a8.json"
GOLDEN = ROOT / "tests" / "fixtures" / "groom-verify-sweep-off.json"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    for owner in FLEET:
        monkeypatch.setenv(groom_context.token_env(owner), owner_token(owner))


def stored():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["proposal"]


def lane_proposal(*, not_ready=(), rewrite=()):
    """The fifteen-card lane at a capacity of three: DRE-101..103 on the
    Planning list, DRE-104..113 the ten spares, DRE-114 and DRE-115 past
    them — plus any `not-ready` and `rewrite` rows named, by position."""
    p = groomer.propose(LANE, cycles=CYCLES, capacity=3, now=NOW)
    p["outcomes"]["not-ready"] = [
        {"identifier": i, "title": f"{i} work", "rule": "a-rule",
         "position": n} for n, i in enumerate(not_ready, 1)]
    p["outcomes"]["rewrite"] = [
        {"identifier": i, "title": f"{i} work", "position": n}
        for n, i in enumerate(rewrite, 1)]
    return p


def row(result, identifier):
    rows = [r for r in result["cards"] if r["identifier"] == identifier]
    assert len(rows) == 1, f"{identifier} has {len(rows)} rows"
    return rows[0]


def walked(result):
    """The Planning-and-spares walk's own cancels."""
    swept = set(result.get("swept") or ())
    return {i: why for i, why in result["cancel"].items() if i not in swept}


# --------------------------------------------------------------------------
# the window the sweep reads
# --------------------------------------------------------------------------
def test_the_sweep_window_over_the_stored_proposal_is_every_card_left_on_offer():
    """The 2026-10-13 proposal: twelve on the Planning list, six spares, three
    on Cancel. Everything else on its three other lists is swept — twelve
    `not-now` cards with no cycle, fifty-seven `not-ready` cards (sixty-seven
    rows, a card per rule it trips) and two `rewrite` cards."""
    p = stored()
    got = groom_verify.sweep_window(p)

    assert len(got) == 71
    assert got[0] == "DRE-7134"
    assert got[-1] == sorted(p["outcomes"]["rewrite"],
                             key=lambda r: r["position"])[-1]["identifier"]
    assert len(set(got)) == len(got), "a card was swept twice"

    planning, spares = groom_verify.window(p)
    dead = {r["identifier"] for r in p["outcomes"]["dead"]}
    assert not set(got) & (set(planning) | set(spares) | dead)

    outcomes = p["outcomes"]
    seq = [r["identifier"] for r in p["sequence"]]
    not_now = sorted((r["identifier"] for r in outcomes["not-now"]
                      if r["identifier"] not in spares), key=seq.index)
    expected = []
    for i in (not_now
              + [r["identifier"] for r in sorted(outcomes["not-ready"],
                                                 key=lambda r: r["position"])]
              + [r["identifier"] for r in sorted(outcomes["rewrite"],
                                                 key=lambda r: r["position"])]):
        if i not in expected:
            expected.append(i)
    assert got == expected


def test_the_sweep_window_skips_the_spares_and_takes_the_rest_in_order():
    p = lane_proposal(not_ready=["DRE-121", "DRE-120", "DRE-121"],
                      rewrite=["DRE-130", "DRE-120"])
    assert groom_verify.sweep_window(p) == [
        "DRE-114", "DRE-115", "DRE-121", "DRE-120", "DRE-130"]
    # A smaller window leaves more behind it to sweep.
    assert groom_verify.sweep_window(p, spare=8)[:4] == [
        "DRE-112", "DRE-113", "DRE-114", "DRE-115"]


def test_a_card_already_on_cancel_is_not_swept():
    p = lane_proposal(not_ready=["DRE-120"])
    p["outcomes"]["dead"].append({"identifier": "DRE-120", "position": 1,
                                  "source": "check", "reason": "done"})
    assert "DRE-120" not in groom_verify.sweep_window(p)


# --------------------------------------------------------------------------
# a swept card with evidence goes to Cancel and holds no slot
# --------------------------------------------------------------------------
def _sweep_lane():
    """DRE-102 on the Planning list declared superseded; DRE-110, a spare no
    slot reaches, the same; DRE-120 `not-ready` and declared superseded; and
    DRE-114, a `not-now` card past the tenth spare, closed by a merged PR."""
    proposal = lane_proposal(not_ready=["DRE-120"])
    linear = FakeLinear({c: {"comments": [comment(SUPERSEDED)]}
                         for c in ("DRE-102", "DRE-110", "DRE-120")})
    gh = FakeGh([pr(77, body="Closes DRE-114")])
    return proposal, linear, gh


def test_a_swept_card_with_evidence_is_cancelled_and_holds_no_slot():
    proposal, linear, gh = _sweep_lane()
    on = groom_verify.check(proposal, lops=linear, run=gh, owners=OWNERS,
                            sweep=True)
    proposal, linear, gh = _sweep_lane()
    off = groom_verify.check(proposal, lops=linear, run=gh, owners=OWNERS)

    assert "Do not build from this card." in on["cancel"]["DRE-120"]
    assert ("https://github.com/dreadnought-foundry/portico/pull/77"
            in on["cancel"]["DRE-114"])
    for identifier, source in (("DRE-120", "comments"),
                               ("DRE-114", "merged_prs")):
        r = row(on, identifier)
        assert r["list"] == "sweep"
        assert r["verdict"] == "cancel" and r["source"] == source
        assert r["acted"] is True
        assert on["cancel"][identifier] == r["evidence"][0]["text"]

    # The walk is the one it was: DRE-102 cancelled, DRE-110 recorded and
    # waiting its turn, and no swept card counted against a slot.
    assert walked(on) == off["cancel"] == {
        "DRE-102": row(off, "DRE-102")["evidence"][0]["text"]}
    assert row(on, "DRE-110")["acted"] is False
    assert "DRE-110" not in on["cancel"]
    assert list(on["cancel"]) == ["DRE-102", "DRE-114", "DRE-120"]


def test_a_swept_card_is_acted_on_even_when_every_slot_is_still_open():
    """Every Planning card and spare fails: the walk runs off its rows with
    slots left. A swept card is still read after them, not in the walk."""
    bad = {f"DRE-{n}": {"comments": [comment(SUPERSEDED)]}
           for n in range(101, 114)}
    p = lane_proposal()
    on = groom_verify.check(p, lops=FakeLinear(bad), run=FakeGh(),
                            owners=OWNERS, sweep=True)
    off = groom_verify.check(lane_proposal(), lops=FakeLinear(bad),
                             run=FakeGh(), owners=OWNERS)
    assert walked(on) == off["cancel"]
    assert row(on, "DRE-114")["verdict"] == "clean"
    assert row(on, "DRE-114")["acted"] is False
    assert "DRE-114" not in on["cancel"]


def test_a_swept_card_another_card_covers_is_cancelled():
    p = lane_proposal(rewrite=["DRE-130"])
    linear = FakeLinear({"DRE-130": {"search": [
        other("DRE-300", "Done", "Supersedes DRE-130.")]}})
    got = groom_verify.check(p, lops=linear, run=FakeGh(), owners=OWNERS,
                             sweep=True)
    assert row(got, "DRE-130")["source"] == "other_cards"
    assert "DRE-300" in got["cancel"]["DRE-130"]


def test_the_sweep_reads_the_swept_cards_after_the_spares_in_one_search():
    proposal, linear, gh = _sweep_lane()
    got = groom_verify.check(proposal, lops=linear, run=gh, owners=OWNERS,
                             sweep=True)
    window = [f"DRE-{n}" for n in range(101, 114)]
    assert linear.asked == window + ["DRE-114", "DRE-115", "DRE-120"]
    assert got["swept"] == groom_verify.sweep_window(proposal)
    assert got["checked"][:len(window) + 3] == window + got["swept"]
    # The same chunks of SEARCH_CHUNK, the swept cards joining at the end.
    searches = [q for q in gh.queries if "search/issues" in q]
    assert len(searches) == -(-16 // groom_verify.SEARCH_CHUNK)
    assert '"DRE-120"' in searches[-1] and '"DRE-114"' in searches[-2]


def test_the_sweep_over_the_stored_proposal_reads_every_card_on_offer():
    p = stored()
    linear = FakeLinear()
    got = groom_verify.check(p, lops=linear, run=FakeGh(), owners=OWNERS,
                             sweep=True)
    planning, spares = groom_verify.window(p)
    swept = groom_verify.sweep_window(p)
    assert linear.asked[:len(planning) + len(spares) + len(swept)] == (
        planning + spares + swept)
    assert got["swept"] == swept
    assert all(row(got, i)["verdict"] == "clean" for i in swept)
    assert got["cancel"] == {}


# --------------------------------------------------------------------------
# a swept source that was not read is said, and never cancels
# --------------------------------------------------------------------------
def test_a_swept_card_linear_would_not_answer_for_is_unread_and_not_cancelled():
    p = lane_proposal(not_ready=["DRE-120"])
    got = groom_verify.check(p, lops=FakeLinear(fail={"DRE-120"}),
                             run=FakeGh(), owners=OWNERS, sweep=True)
    r = row(got, "DRE-120")
    assert r["list"] == "sweep"
    assert r["verdict"] == "unread"
    assert r["unread"] == ["comments", "other_cards"]
    assert r["acted"] is False
    for source in ("comments", "other_cards"):
        assert "DRE-120" in got["unread"][source]["cards"]
    assert "DRE-120" not in got["cancel"]


def test_a_swept_card_whose_pull_request_search_failed_is_unread_on_merged_prs():
    p = lane_proposal(not_ready=["DRE-120"])
    got = groom_verify.check(p, lops=FakeLinear(),
                             run=FakeGh(fail_search=True), owners=OWNERS,
                             sweep=True)
    r = row(got, "DRE-120")
    assert r["verdict"] == "unread"
    assert r["unread"] == ["merged_prs"]
    assert "DRE-120" in got["unread"]["merged_prs"]["cards"]
    assert "DRE-120" not in got["cancel"]


def test_a_swept_card_with_every_source_read_and_nothing_found_is_clean():
    p = lane_proposal(not_ready=["DRE-120"])
    got = groom_verify.check(p, lops=FakeLinear(), run=FakeGh(),
                             owners=OWNERS, sweep=True)
    r = row(got, "DRE-120")
    assert (r["verdict"], r["unread"], r["acted"]) == ("clean", [], False)


# --------------------------------------------------------------------------
# off by default — byte for byte what it was
# --------------------------------------------------------------------------
def _scenarios():
    """Every fixture shape `tests/test_groom_verify.py` checks a proposal
    with, by name: `(proposal, linear, gh, owners)`."""
    def lane(cards=LANE):
        return groomer.propose(cards, cycles=CYCLES, capacity=3, now=NOW)

    bad = {f"DRE-{n}": {"comments": [comment(SUPERSEDED)]}
           for n in range(101, 114)}
    mention = pr(88, body="DRE-104 is mentioned in passing")
    branch_gh = FakeGh([mention])
    branch_gh.heads[mention["html_url"]] = "agent/DRE-104-x"
    return {
        "clean": lambda: (lane(), FakeLinear(), FakeGh()),
        "comment-on-planning": lambda: (lane(), FakeLinear(
            {"DRE-102": {"comments": [comment(SUPERSEDED)]}}), FakeGh()),
        "comment-on-unreached-spare": lambda: (lane(), FakeLinear(
            {"DRE-110": {"comments": [comment(SUPERSEDED)]}}), FakeGh()),
        "every-card-fails": lambda: (lane(), FakeLinear(bad), FakeGh()),
        "search-failed": lambda: (lane(), FakeLinear(),
                                  FakeGh(fail_search=True)),
        "linear-failed": lambda: (lane(), FakeLinear(fail={"DRE-101"}),
                                  FakeGh()),
        "merged-pr-closes": lambda: (lane(), FakeLinear(),
                                     FakeGh([pr(431, body="Closes DRE-103")])),
        "merged-pr-by-branch": lambda: (lane(), FakeLinear(), branch_gh),
        "pull-unread": lambda: (lane(), FakeLinear(),
                                FakeGh([mention], fail_pulls=True)),
        "other-card-covers": lambda: (lane(), FakeLinear({"DRE-105": {
            "search": [other("DRE-300", "Done", "Supersedes DRE-105.")]}}),
            FakeGh()),
        "cancel-intake": lambda: (lane(_lane_with_cancel("DRE-900")),
                                  FakeLinear(lanes={"DRE-900": "Intake"}),
                                  FakeGh()),
        "cancel-done": lambda: (lane(_lane_with_cancel("DRE-900")),
                                FakeLinear(lanes={"DRE-900": "Done"}),
                                FakeGh()),
        "cancel-unread": lambda: (lane(_lane_with_cancel("DRE-900")),
                                  FakeLinear(fail={"DRE-900"}), FakeGh()),
        "stored-3381b8d562a8": lambda: (stored(), FakeLinear(), FakeGh()),
    }


def answer(name, **kw):
    """One scenario's check, as text: the result and every read it spent."""
    proposal, linear, gh = _scenarios()[name]()
    result = groom_verify.check(proposal, lops=linear, run=gh, owners=OWNERS,
                                **kw)
    result = dict(result)
    result.pop("swept", None)
    return {"result": json.dumps(result, ensure_ascii=False),
            "asked": linear.asked, "queries": gh.queries, "pulls": gh.pulls}


def test_the_golden_holds_every_scenario():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["answers"]
    assert sorted(golden) == sorted(_scenarios())


@pytest.mark.parametrize("name", sorted(_scenarios()))
@pytest.mark.parametrize("kw", [{}, {"sweep": False}], ids=["default", "off"])
def test_with_the_sweep_off_the_check_answers_byte_for_byte_as_before(name, kw):
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["answers"]
    assert answer(name, **kw) == golden[name]


@pytest.mark.parametrize("name", sorted(_scenarios()))
def test_with_the_sweep_off_nothing_is_swept(name):
    proposal, linear, gh = _scenarios()[name]()
    result = groom_verify.check(proposal, lops=linear, run=gh, owners=OWNERS)
    assert result["swept"] == []
    assert all(r["list"] != "sweep" for r in result["cards"])


# --------------------------------------------------------------------------
# the record, and a check that failed outright
# --------------------------------------------------------------------------
def test_settle_writes_the_swept_cards_and_those_moved_to_cancel():
    proposal, linear, gh = _sweep_lane()
    result = groom_verify.check(proposal, lops=linear, run=gh, owners=OWNERS,
                                sweep=True)
    final = copy.deepcopy(proposal)
    final["cancels_refused"] = [{"identifier": "DRE-114"}]
    groom_verify.settle(final, result)
    v = final["verification"]
    assert v["swept"] == result["swept"] == ["DRE-114", "DRE-115", "DRE-120"]
    assert v["swept_to_cancel"] == ["DRE-120"]
    assert "DRE-114" not in v["moved_to_cancel"]


def test_settle_with_the_sweep_off_writes_empty_sweep_lists():
    proposal, linear, gh = _sweep_lane()
    result = groom_verify.check(proposal, lops=linear, run=gh, owners=OWNERS)
    groom_verify.settle(proposal, result)
    assert proposal["verification"]["swept"] == []
    assert proposal["verification"]["swept_to_cancel"] == []


def test_a_failed_check_with_the_sweep_on_marks_every_swept_card_unread():
    p = lane_proposal(not_ready=["DRE-120"], rewrite=["DRE-130"])
    got = groom_verify.unread_result(p, "the check failed this run",
                                     sweep=True)
    swept = groom_verify.sweep_window(p)
    assert got["swept"] == swept == ["DRE-114", "DRE-115", "DRE-120",
                                     "DRE-130"]
    for identifier in swept:
        r = row(got, identifier)
        assert r["list"] == "sweep"
        assert r["verdict"] == "unread"
        assert r["unread"] == list(groom_verify.SOURCES)
        assert r["acted"] is False
    for source in groom_verify.SOURCES:
        assert set(swept) <= set(got["unread"][source]["cards"])
    assert got["cancel"] == {} and set(swept) <= set(got["checked"])


def test_a_failed_check_with_the_sweep_off_sweeps_nothing():
    p = lane_proposal(not_ready=["DRE-120"])
    got = groom_verify.unread_result(p, "the check failed this run")
    assert got["swept"] == []
    assert "DRE-120" not in got["checked"]
