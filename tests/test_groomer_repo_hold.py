"""RED-first: a repo the CEO switches off is left out of every proposal
(DRE-3403).

The CEO said it three times: the Bureau repos — agent-bureau and
bureau-pipeline — only, until the groomer is solid. atlas cards were proposed
anyway, approved as part of a batch, drained, and one was mid-planning before
it was pulled back by hand. Saying "not that repo" three times and having it
happen anyway is not a communication problem — it is a missing switch.

So the switch exists, as a comment marker on the proposal card beside the
decision markers DRE-3370 declares:

    🧺 groom-hold-repo: atlas          switches a repo off
    🧺 groom-release-repo: atlas       switches it back on

What this file pins, and each item exists because its absence is the bug the
card was raised for:

  * **The marker is NOT bound to a proposal id.** A hold outlives the proposal
    it was written on — that is the whole point of a switch — so it names a
    slug and nothing else, and the newest marker per slug wins.
  * **Authorship is the gate**, exactly as it is for the approval (DRE-2721): a
    hold or a release written by the pipeline's own Linear identity is ignored
    and named in the run log. The release direction matters most — a proposer
    that could switch a repo back on could undo the CEO's own answer.
  * **`propose` removes the held cards BEFORE anything looks at them.** Never
    sent to the model, never ranked, never given a cycle, never counted against
    `--capacity`, never half of a collision pair or a pull-forward. The
    population and the census stay the whole lane, so a held repo's cards are
    still visible as existing.
  * **The drain reads the holds at DRAIN time**, so a hold written after the
    proposal was posted still holds those cards back — `held back`, Why
    `repo held: <slug>` — and the rest of the batch moves.
  * **Parity:** a proposal with no hold renders byte for byte as today, and a
    release restores the repo byte for byte.

This is distinct from the per-repo work-in-progress cap, which stops BUILDS
after classification. This stops the cards being proposed at all.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_repo_hold.py -v
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import groom_context  # noqa: E402
import groom_judgement  # noqa: E402
import groomer  # noqa: E402

from test_groomer import CYCLES, GOLDEN, NOW, PACK, card, judged, ranked  # noqa: E402
from test_groomer_decisions import FakeOps, PROPOSAL_CARD  # noqa: E402

HELD = "atlas"
DOC = ROOT / "docs" / "groomer.md"
#: The rules-only page as `main` rendered it BEFORE this card — written by that
#: groomer, so the parity test below compares against yesterday's bytes rather
#: than against today's code agreeing with itself.
GOLDEN_RENDER = ROOT / "tests" / "fixtures" / "groom_rules_only_render.md"
HELD_HEADING = "## Held repos — switched off by you"


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
def switch(tag, slug, *, by_pipeline=False):
    """One repo-switch comment, hand-written the way a CEO writes it.

    The marker carries no proposal id, so there is no writer to go through:
    the shape IS `🧺 <tag>: <slug>`, and typing it here is the same act the
    console's writer performs.
    """
    return {"body": f"{groomer.MARK} {tag}: {slug}",
            "authored_by_pipeline": by_pipeline}


def held(slug=HELD, **kw):
    return switch(groomer.REPO_HOLD_TAG, slug, **kw)


def released(slug=HELD, **kw):
    return switch(groomer.REPO_RELEASE_TAG, slug, **kw)


def lane(*, atlas=12, portico=4, repo_days=1):
    """A lane holding `atlas` cards in the held repo and `portico` elsewhere.

    Atlas cards are the OLDER ones, so a groomer that failed to drop them would
    still batch some of them — a fixture where the held repo is newest would
    let a capacity bug pass for a hold.
    """
    cards = [card(f"DRE-{n:03d}", repo=HELD, days=repo_days)
             for n in range(atlas)]
    cards += [card(f"DRE-{200 + n:03d}", repo="portico", days=repo_days)
              for n in range(portico)]
    return cards


def proposal(cards, *, holds=(), capacity=20, **kw):
    return groomer.propose(cards, cycles=CYCLES, capacity=capacity, now=NOW,
                           held_repos=holds, **kw)


def batched(built):
    return [row["identifier"] for row in
            sorted(built["outcomes"]["now"], key=lambda r: r["position"])]


def everything_sequenced(built):
    return ({row["identifier"] for row in built["sequence"]}
            | {row["identifier"] for rows in built["outcomes"].values()
               for row in rows})


def section(text, heading):
    """One `##` section of a rendered proposal, heading excluded."""
    lines = text.splitlines()
    start = lines.index(heading)
    for n in range(start + 1, len(lines)):
        if lines[n].startswith("## "):
            return "\n".join(lines[start + 1:n])
    return "\n".join(lines[start + 1:])


# --------------------------------------------------------------------------
# the vocabulary — beside the decision markers, and naming a slug
# --------------------------------------------------------------------------
def test_the_two_markers_sit_beside_the_approval_in_the_vocabulary():
    assert groomer.REPO_HOLD_TAG == "groom-hold-repo"
    assert groomer.REPO_RELEASE_TAG == "groom-release-repo"
    assert groomer.REPO_TAGS == (groomer.REPO_HOLD_TAG,
                                 groomer.REPO_RELEASE_TAG)
    # They are markers this module READS off a Linear comment, so they belong
    # in the set an untrusted reason is defanged against (DRE-3373).
    for tag in groomer.REPO_TAGS:
        assert tag in groomer.ALL_MARKERS
    # And they are NOT decisions about a batch: a reader looking for the
    # approval's id shape must not find one here.
    assert groomer.REPO_HOLD_TAG not in groomer.DECISION_TAGS
    assert groomer.REPO_RELEASE_TAG not in groomer.DECISION_TAGS


@pytest.mark.parametrize("tag", ["groom-hold-repo", "groom-release-repo"])
def test_a_repo_switch_is_anchored_and_the_emoji_is_optional(tag):
    """The same anchoring every other marker has, for the same reason: a
    reader that matched the marker anywhere would read a sentence ABOUT
    holding a repo as a hold."""
    assert groomer.repo_switch_match(tag, f"{groomer.MARK} {tag}: atlas")
    assert groomer.repo_switch_match(tag, f"{tag}: atlas"), (
        "the emoji must be optional, exactly as it is on the approval"
    )
    assert groomer.repo_switch_match(
        tag, f"I might {tag}: atlas once Ana has read it") is None


@pytest.mark.parametrize("tag", ["groom-hold-repo", "groom-release-repo"])
def test_a_repo_switch_names_a_slug_and_not_a_proposal_id(tag):
    """`[a-z0-9][a-z0-9-]*` — the shape of a `repo:` label's slug, which is
    what the switch is about. A hold outlives the proposal it was written on,
    so there is no id to name and nothing that reads one."""
    match = groomer.repo_switch_match(tag, f"{groomer.MARK} {tag}: bureau-pipeline")
    assert match and match.group(1) == "bureau-pipeline"
    # A reason under the marker is fine; the slug still ends where it ends.
    multi = groomer.repo_switch_match(
        tag, f"{groomer.MARK} {tag}: atlas\n\nuntil the groomer is solid")
    assert multi and multi.group(1) == "atlas"
    for bad in ("Atlas", "", "atlas.beta", "_atlas"):
        assert groomer.repo_switch_match(
            tag, f"{groomer.MARK} {tag}: {bad}") is None, \
            f"{bad!r} read as a slug"


# --------------------------------------------------------------------------
# held_repos — the current set, off the whole thread
# --------------------------------------------------------------------------
def test_a_hold_switches_a_repo_off():
    assert groomer.held_repos([held()]) == [HELD]


def test_the_newest_marker_per_slug_wins():
    assert groomer.held_repos([held(), released()]) == []
    assert groomer.held_repos([held(), released(), held()]) == [HELD]
    assert groomer.held_repos([released(), held()]) == [HELD]


def test_two_repos_are_switched_off_independently():
    thread = [held("atlas"), held("deltasolv"), released("atlas")]
    assert groomer.held_repos(thread) == ["deltasolv"]


def test_a_release_for_a_repo_that_was_never_held_holds_nothing():
    assert groomer.held_repos([released("atlas")]) == []


def test_a_hold_is_not_bound_to_the_proposal_it_was_written_on():
    """A hold outlives its proposal — that is the whole point of a switch. A
    marker written before two later proposals is still the current answer."""
    old = proposal(lane(atlas=0), holds=())
    thread = [held(), {"body": groomer.proposal_comment(old),
                       "authored_by_pipeline": True}]
    assert groomer.held_repos(thread) == [HELD]


def test_a_hold_written_by_the_pipeline_is_ignored_and_named_in_the_log(capsys):
    """The authorship gate the whole vocabulary is read under (DRE-2721): a
    marker the proposer can write is a credential the proposer can mint."""
    assert groomer.held_repos([held(by_pipeline=True)]) == []
    log = capsys.readouterr().err
    assert groomer.REPO_HOLD_TAG in log and HELD in log
    assert "ignored" in log.lower(), "the run log does not say it was ignored"


def test_a_release_written_by_the_pipeline_cannot_switch_a_repo_back_on():
    """The direction that matters most: a proposer that could release a repo
    could undo the CEO's own answer and propose the cards anyway."""
    assert groomer.held_repos([held(), released(by_pipeline=True)]) == [HELD]


# --------------------------------------------------------------------------
# propose — never ranked, never counted, listed once as held
# --------------------------------------------------------------------------
def test_a_held_repos_cards_are_left_out_of_the_batch_entirely():
    cards = lane(atlas=12, portico=4)
    built = proposal(cards, holds=[HELD], capacity=20)
    for row in built["sequence"]:
        assert not row["identifier"].startswith("DRE-0"), (
            f"{row['identifier']} is in a held repo and was sequenced anyway"
        )
    assert everything_sequenced(built) == {f"DRE-{200 + n:03d}"
                                          for n in range(4)}
    assert len(batched(built)) == 4


def test_the_population_and_the_census_stay_the_whole_lane():
    """A held repo's cards are still visible as EXISTING. The offer shrinks;
    the lane does not."""
    cards = lane(atlas=12, portico=4)
    built = proposal(cards, holds=[HELD])
    assert built["population"] == 16
    assert built["census"] == {HELD: 12, "portico": 4}
    assert built["offered"] == 4


def test_the_json_names_every_held_repo_with_its_card_count():
    built = proposal(lane(atlas=12, portico=4), holds=[HELD])
    assert built["held_repos"] == [{"repo": HELD, "cards": 12}]


def test_a_held_slug_with_no_cards_in_the_lane_still_gets_a_row():
    """The CEO switched that repo off and the page has to say so — a hold on a
    quiet repo that rendered nothing reads as a hold nobody honoured."""
    built = proposal(lane(atlas=0, portico=3), holds=["deltasolv"])
    assert built["held_repos"] == [{"repo": "deltasolv", "cards": 0}]
    assert built["offered"] == built["population"] == 3
    assert "- held: deltasolv · 0 cards" in groomer.render_proposal(built)


def test_the_held_rows_are_sorted_by_cards_descending_then_slug():
    cards = (lane(atlas=2, portico=1)
             + [card("DRE-400", repo="zulu"), card("DRE-401", repo="zulu"),
                card("DRE-402", repo="alpha")])
    built = proposal(cards, holds=["zulu", "alpha", HELD])
    assert built["held_repos"] == [{"repo": HELD, "cards": 2},
                                  {"repo": "zulu", "cards": 2},
                                  {"repo": "alpha", "cards": 1}]


def test_a_held_card_never_reaches_the_model(monkeypatch):
    """The marker on the card, end to end: the cards are gone BEFORE
    `groom_judgement.census` is built, so a card the model never sees cannot be
    ranked, and cannot be ranked `now` into a batch."""
    cards = lane(atlas=12, portico=4)
    seen: list = []
    # BEFORE the patch below, or the canned judgement would build through the
    # spy it is standing in for.
    answer = judged(cards[12:], ranked([f"DRE-{200 + n:03d}" for n in range(4)]))

    def spy(rows, pack):
        seen.append([row["identifier"] for row in rows])
        return answer

    ops = FakeOps([held()])
    monkeypatch.setattr(groomer.linear_ops, "comment_records",
                        ops.comment_records)
    monkeypatch.setattr(groomer, "read_population", lambda lops, l: cards)
    monkeypatch.setattr(groomer, "read_cycles", lambda lops: CYCLES)
    monkeypatch.setattr(groom_context, "read_pack", lambda lops: PACK)
    monkeypatch.setattr(groom_judgement, "run", spy)

    built = groomer._build(argparse.Namespace(
        lane="Intake", capacity=20, batch_cycles=1, judgement=True,
        window_days=groomer.WINDOW_DAYS, keep_answer=None,
        post=PROPOSAL_CARD, hold_repo=[],
        priority=",".join(groomer.REPO_PRIORITY)))
    assert seen, "no census was built at all"
    assert seen[0] == [f"DRE-{200 + n:03d}" for n in range(4)]
    assert built["held_repos"] == [{"repo": HELD, "cards": 12}]
    assert built["offered"] == built["population"] - 12
    assert batched(built) == [f"DRE-{200 + n:03d}" for n in range(4)]
    assert "- held: atlas · 12 cards" in groomer.render_proposal(built)


def test_a_held_card_is_never_counted_against_the_capacity():
    """Twelve held cards and four others, capacity four: the batch is the four
    others. A groomer that counted the held ones would batch none of them and
    still report a full batch."""
    built = proposal(lane(atlas=12, portico=4), holds=[HELD], capacity=4)
    assert batched(built) == [f"DRE-{200 + n:03d}" for n in range(4)]
    assert built["batch"]["cards"] == 4


def test_a_held_card_is_never_half_of_a_collision_pair():
    cards = [card("DRE-1", repo=HELD, days=2, description="edits `thread.tsx`"),
             card("DRE-2", repo=HELD, days=1, description="edits `thread.tsx`"),
             card("DRE-3", repo="portico", days=1)]
    unheld = proposal(cards, holds=())
    assert unheld["collisions"]["pairs"], "the fixture collides with nothing"
    built = proposal(cards, holds=[HELD])
    assert built["collisions"]["pairs"] == []


def test_a_held_card_is_never_pulled_forward_by_a_collision():
    """The two things that pull an old card into a batch are a collision with a
    batched card and being a blocker of one. Neither may reach into a held
    repo — a repo switched off has no cards in the batch, however old."""
    cards = [card("DRE-1", repo=HELD, days=90, description="edits `same.ts`"),
             card("DRE-2", repo=HELD, days=1, description="edits `same.ts`")]
    unheld = proposal(cards, holds=())
    assert "DRE-1" in batched(unheld), "the fixture never pulled it forward"
    built = proposal(cards, holds=[HELD])
    assert batched(built) == []
    assert everything_sequenced(built) == set()


# --------------------------------------------------------------------------
# propose — the one section it renders
# --------------------------------------------------------------------------
def test_the_render_carries_the_held_section_in_the_fixed_grammar():
    built = proposal(lane(atlas=12, portico=4), holds=[HELD])
    body = section(groomer.render_proposal(built), HELD_HEADING)
    assert "- held: atlas · 12 cards" in body
    assert groomer.REPO_RELEASE_TAG in body, (
        "the section never names the marker that switches the repo back on"
    )


def test_one_card_is_singular():
    built = proposal(lane(atlas=1, portico=2), holds=[HELD])
    assert "- held: atlas · 1 card" in groomer.render_proposal(built)


def test_the_section_sits_directly_before_on_cycles():
    built = proposal(lane(atlas=12, portico=4), holds=[HELD])
    lines = groomer.render_proposal(built).splitlines()
    before = lines[:lines.index("## On cycles")]
    last = max(n for n, line in enumerate(before) if line.startswith("## "))
    assert before[last] == HELD_HEADING, (
        "another section sits between the held repos and '## On cycles'"
    )


def test_the_section_is_absent_when_nothing_is_held():
    built = proposal(lane(atlas=12, portico=4), holds=())
    text = groomer.render_proposal(built)
    assert HELD_HEADING not in text
    assert "- held:" not in text


# --------------------------------------------------------------------------
# a batched card blocked by a held repo is named, never dropped
# --------------------------------------------------------------------------
def test_a_card_blocked_by_a_held_repo_stays_in_the_batch_and_is_named():
    """The hold takes the BLOCKER out of the offer, which takes the ordering
    constraint with it. The blocked card is named rather than dropped: the
    dependency gate holds it later, and the groomer does not silently drop it.
    """
    blocked = card("DRE-2", repo="portico", days=1,
                   description="Blocked by: DRE-1")
    built = proposal([card("DRE-1", repo=HELD, days=2), blocked], holds=[HELD])
    assert batched(built) == ["DRE-2"]
    assert built["held_blockers"] == [
        {"identifier": "DRE-2", "blocked_by": "DRE-1", "repo": HELD}]
    body = section(groomer.render_proposal(built),
                   "## Collisions, and what the order does about them")
    assert "blocked by a held repo" in body
    assert "DRE-2" in body and "DRE-1" in body


def test_nothing_is_named_when_no_blocker_is_held():
    built = proposal([card("DRE-1", repo="portico", days=2),
                      card("DRE-2", repo="portico", days=1,
                           description="Blocked by: DRE-1")], holds=())
    assert built["held_blockers"] == []
    assert "blocked by a held repo" not in groomer.render_proposal(built)


# --------------------------------------------------------------------------
# release restores the repo, byte for byte
# --------------------------------------------------------------------------
def test_a_release_restores_the_repo_byte_for_byte():
    cards = lane(atlas=12, portico=4)
    thread = [held(), released()]
    assert groomer.held_repos(thread) == []
    restored = proposal(cards, holds=groomer.held_repos(thread))
    never_held = proposal(cards, holds=())
    assert restored == never_held
    assert groomer.render_proposal(restored) == \
        groomer.render_proposal(never_held)


def test_a_hold_changes_the_batch_and_therefore_the_id():
    """`proposal_id` is unchanged — it digests the batch, and a hold changes
    the batch. So the id moves for the right reason and the digest itself did
    not have to learn about holds."""
    cards = lane(atlas=12, portico=4)
    with_hold = proposal(cards, holds=[HELD])
    without = proposal(cards, holds=())
    assert with_hold["id"] != without["id"]
    assert groomer.proposal_id(with_hold) == with_hold["id"]


# --------------------------------------------------------------------------
# golden parity — no hold renders exactly as today
# --------------------------------------------------------------------------
def _fixture(**kw):
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return groomer.propose(
        golden["cards"], cycles=golden["cycles"], capacity=golden["capacity"],
        batch_cycles=golden["batch_cycles"], now=golden["now"],
        judgement=None, **kw)


def test_a_proposal_with_no_hold_renders_byte_for_byte_as_today():
    """The parity that keeps the switch cheap to leave in place.

    `groom_rules_only_render.md` is the page this fixture rendered on `main`
    BEFORE this card, written by that groomer and not by this one — so this is
    a comparison against yesterday's bytes rather than against today's code
    agreeing with itself.
    """
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    built = _fixture()
    assert built["held_repos"] == []
    assert built["offered"] == built["population"]
    assert built["id"] == golden["proposal"]["id"], (
        "an empty hold moved the proposal id"
    )
    text = groomer.render_proposal(built)
    assert HELD_HEADING not in text
    assert text == GOLDEN_RENDER.read_text(encoding="utf-8"), (
        "a proposal with no hold no longer renders as it did before DRE-3403"
    )
    # …and an empty hold is the same thing as no hold at all.
    assert groomer.render_proposal(_fixture(held_repos=())) == text


# --------------------------------------------------------------------------
# the drain reads the holds at drain time
# --------------------------------------------------------------------------
def _drain_fixture(*, holds=(), by_pipeline=False):
    """An approved batch of two atlas cards and two others, with the hold
    written AFTER the proposal was posted."""
    cards = [card("DRE-001", repo=HELD, days=1),
             card("DRE-002", repo=HELD, days=1),
             card("DRE-101", repo="portico", days=2),
             card("DRE-102", repo="portico", days=2)]
    built = proposal(cards, holds=(), capacity=4)
    thread = [{"body": groomer.proposal_comment(built),
               "authored_by_pipeline": True},
              {"body": groomer.approval_comment(built["id"]),
               "authored_by_pipeline": False}]
    thread += [held(slug, by_pipeline=by_pipeline) for slug in holds]
    lanes = {c["identifier"]: "Intake" for c in cards}
    return built, FakeOps(thread, lanes=lanes)


def _record_body(ops):
    assert len(ops.written) == 1, f"the drain wrote {len(ops.written)} records"
    return ops.written[0][1]


def test_the_drain_holds_back_a_batch_card_whose_repo_is_held():
    built, ops = _drain_fixture(holds=[HELD])
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["moved"] == ["DRE-101", "DRE-102"], (
        "the rest of the batch did not move"
    )
    assert result["held_back"] == ["DRE-001", "DRE-002"]
    assert [i for i, _ in ops.state_writes] == ["DRE-101", "DRE-102"]
    body = _record_body(ops)
    for identifier in ("DRE-001", "DRE-002"):
        row = next(line for line in body.splitlines()
                   if f"| {identifier} |" in line)
        assert "held back" in row
        assert f"repo held: {HELD}" in row, (
            f"the Why on {identifier} is not the contract's own text: {row}"
        )
    assert "moved: 2 · held back: 2" in body
    assert built["id"] in body


def test_the_drain_reads_the_whole_thread_for_the_holds():
    """The approval is the OLDEST comment on a proposal card, and a hold is
    older still — a drain reading the newest fifty would miss both."""
    _, ops = _drain_fixture(holds=[HELD])
    groomer.drain(ops, card=PROPOSAL_CARD)
    assert any(ops.whole_thread_asked), (
        "the drain read a window rather than the whole thread"
    )


def test_a_released_repo_drains_normally():
    built, ops = _drain_fixture()
    ops.comments += [held(), released()]
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["moved"] == ["DRE-001", "DRE-002", "DRE-101", "DRE-102"]
    assert result["held_back"] == []


def test_a_hold_written_by_the_pipeline_holds_nothing_back_at_drain():
    _, ops = _drain_fixture(holds=[HELD], by_pipeline=True)
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["held_back"] == []
    assert result["moved"] == ["DRE-001", "DRE-002", "DRE-101", "DRE-102"]


def test_the_self_approval_refusal_is_unchanged_by_the_switch():
    """The gate the whole vocabulary hangs off. A hold on the card changes
    nothing about it: an approval the pipeline wrote is still no approval."""
    built, ops = _drain_fixture(holds=[HELD])
    ops.comments[1]["authored_by_pipeline"] = True
    with pytest.raises(groomer.NotApproved):
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == []


# --------------------------------------------------------------------------
# the flag, for a dry run with no card
# --------------------------------------------------------------------------
def _cli_build(monkeypatch, cards, **extra):
    monkeypatch.setattr(groomer, "read_population", lambda lops, l: cards)
    monkeypatch.setattr(groomer, "read_cycles", lambda lops: CYCLES)
    args = dict(lane="Intake", capacity=20, batch_cycles=1, judgement=False,
                window_days=groomer.WINDOW_DAYS, keep_answer=None, post=None,
                hold_repo=[], priority=",".join(groomer.REPO_PRIORITY))
    args.update(extra)
    return groomer._build(argparse.Namespace(**args))


def test_the_hold_repo_flag_behaves_exactly_as_the_marker_does(monkeypatch):
    cards = lane(atlas=12, portico=4)
    by_flag = _cli_build(monkeypatch, cards, hold_repo=[HELD])
    by_marker = proposal(cards, holds=[HELD])
    assert by_flag["held_repos"] == by_marker["held_repos"]
    assert by_flag["offered"] == by_marker["offered"]
    assert batched(by_flag) == batched(by_marker)
    assert by_flag["id"] == by_marker["id"]


def test_the_flag_is_repeatable(monkeypatch):
    cards = lane(atlas=2, portico=2) + [card("DRE-500", repo="deltasolv")]
    built = _cli_build(monkeypatch, cards, hold_repo=[HELD, "deltasolv"])
    assert [row["repo"] for row in built["held_repos"]] == [HELD, "deltasolv"]
    assert built["offered"] == 2


def test_with_a_card_the_holds_come_off_the_card(monkeypatch):
    cards = lane(atlas=12, portico=4)
    ops = FakeOps([held()])
    monkeypatch.setattr(groomer.linear_ops, "comment_records",
                        ops.comment_records)
    built = _cli_build(monkeypatch, cards, post=PROPOSAL_CARD)
    assert built["held_repos"] == [{"repo": HELD, "cards": 12}]
    assert ops.whole_thread_asked and all(ops.whole_thread_asked), (
        "a hold is not bound to a proposal and can be the oldest comment on "
        "the card — the whole thread has to be read"
    )


def test_the_cli_documents_the_flag():
    import contextlib
    import io
    assert "--hold-repo" in (groomer.__doc__ or ""), (
        "the flag is not documented where the module documents its own CLI"
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.suppress(SystemExit):
        groomer.main(["propose", "--help"])
    assert "--hold-repo" in buf.getvalue()


# --------------------------------------------------------------------------
# the doc the contract is mirrored from
# --------------------------------------------------------------------------
def test_the_doc_carries_the_two_markers_and_the_behaviour():
    text = DOC.read_text(encoding="utf-8")
    for marker in (f"{groomer.MARK} {groomer.REPO_HOLD_TAG}: <slug>",
                   f"{groomer.MARK} {groomer.REPO_RELEASE_TAG}: <slug>"):
        assert marker in text, f"docs/groomer.md never shows `{marker}`"
    assert "| `🧺 groom-hold-repo: <slug>` |" in text, (
        "the two markers are not in the vocabulary table the console mirrors"
    )
    assert "DRE-3403" in text
    assert f"repo held: {HELD}" in text or "repo held: <slug>" in text, (
        "the drain's Why text for a held-back card is not documented"
    )
    assert HELD_HEADING in text
