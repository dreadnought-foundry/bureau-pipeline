"""One blocker-prose parser, and a door that refuses a blocker it cannot resolve
(DRE-2922).

Three functions parsed "blocked by" out of card text and they did not agree:

  * `linear_ops.parse_blocked_by` — the PRODUCER. It materialises prose into
    real `blocks` relations at card creation, and its grammar was `blocked by`
    only. A card written `Depends on DRE-N` got no relation at all.
  * `reconcile.blockers_of` — the promotion gate. Full anchored grammar:
    `blocked by` / `depends on` / `serialize after`, list markers included.
    (Since DRE-2676 that consumer is `prose_blockers.prose_claims`, and it is
    the sweep's DEFECT DETECTOR rather than its gate: prose no longer adds a
    blocker, it is checked against the relations the board actually holds. The
    grammar it reads is unchanged, which is why it is still driven through this
    corpus — a detector that disagreed with the producer would refuse cards the
    door had just minted relations for.)
  * `groomer.blockers_of` — imported the NARROW producer, so a third answer.

The disagreement is the machine that manufactures a prose-only blocker: the
sweep honours a sentence the door never turned into a relation. Widening the
producer to the sweep's grammar is what closes it, so the grammar has to live
in ONE place — `scripts/blocker_prose.py` — with ONE fixture corpus that all
three consumers are driven through. A test per consumer cannot catch drift
between them; this shape can, and that is the point (the same shape the CLI and
the console panel are kept honest with).

The surviving grammar is `reconcile._BLOCKER_LINE`, moved verbatim. Every
clause in it was paid for: line-start anchoring is DRE-2670 (epic DRE-2492
froze five children for five days on "neither depends on the other"), and the
ordered-list clause is there because dropping it failed UNSAFE. The DRE-2492
sentences ride along in the shared corpus as must-not-match fixtures, so the
anchoring cannot weaken by accident during the move.

The second half is the door: `_add_blocked_by` printed to stderr and CONTINUED
when a blocker id did not resolve, so the card was created, its description
said "Blocked by DRE-N", no relation existed, and the create reported success —
a prose-only card minted at the door, silently. It now raises, and the create
is refused through the existing `_reject_unless_creatable` contract so no
half-created card exists.

Run: cd bureau-pipeline && python3 -m pytest tests/test_one_blocker_prose_parser.py -v
"""
from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import blocker_prose  # noqa: E402
import groomer  # noqa: E402
import linear_ops  # noqa: E402
import prose_blockers  # noqa: E402

from test_subissue_valid_children import FakeLinear  # noqa: E402


# An identifier no fixture names, so the self-id and parent-id strips in the
# consumers never eat a fixture's expected answer.
FIXTURE_CARD_ID = "DRE-7777"


def _card(text: str) -> dict:
    """The one card shape both consumer paths read: a description, no formal
    relations (so only the prose parse can produce an id), no parent."""
    return {
        "identifier": FIXTURE_CARD_ID,
        "parent": None,
        "description": text,
        "inverseRelations": {"nodes": []},
    }


def _through_producer(text: str) -> set[str]:
    return set(linear_ops.parse_blocked_by(text))


def _through_the_sweep(text: str) -> set[str]:
    return prose_blockers.prose_claims(_card(text))


def _through_groomer(text: str) -> set[str]:
    return groomer.blockers_of(_card(text))


CONSUMERS = {
    "linear_ops.parse_blocked_by": _through_producer,
    "prose_blockers.prose_claims": _through_the_sweep,
    "groomer.blockers_of": _through_groomer,
}


# ---------------------------------------------------------------------------
# One fixture set, all three consumers — the test that catches the drift
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", blocker_prose.FIXTURES)
@pytest.mark.parametrize("name", list(CONSUMERS))
def test_every_consumer_reads_the_shared_corpus_identically(name, text, expected):
    """The table test: every string the anchored grammar accepts (and every
    string it must reject) produces the SAME id set through the producer and
    through each consumer path."""
    assert CONSUMERS[name](text) == set(expected), (
        f"{name} disagrees with the shared grammar on {text!r}"
    )


def test_no_two_consumers_disagree_on_any_fixture():
    """Stated as its own assertion, because 'they agree with the fixture' and
    'they agree with each other' are different failures: a fixture updated
    without a consumer moving shows up here as a named disagreement."""
    disagreements = []
    for text, expected in blocker_prose.FIXTURES:
        answers = {name: fn(text) for name, fn in CONSUMERS.items()}
        if len({frozenset(a) for a in answers.values()}) != 1:
            disagreements.append((text, answers))
    assert disagreements == [], f"consumers disagree: {disagreements}"


def test_the_producer_accepts_the_full_anchored_grammar():
    """The widening that closes the gap: `Depends on` / `Serialize after`
    declarations, and list-marked ones, mint relations at creation now."""
    for line in (
        "Depends on DRE-9",
        "**Depends on:** DRE-9",
        "Serialize after: DRE-9",
        "Serialize after DRE-9",
        "1. Blocked by: DRE-9",
        "2) Depends on: DRE-9",
        "10) Serialize after: DRE-9",
        "- **Blocked by:** DRE-9",
        "> **Blocked by:** DRE-9",
    ):
        assert linear_ops.parse_blocked_by(line) == ["DRE-9"], line


def test_the_producer_still_declines_a_mention():
    """Widening the producer must not widen it past a declaration."""
    for line in (
        "DRE-2496 lands first. B3 is formally blocked by it, per the plan.",
        "Both depend on DRE-2494 only - neither depends on the other.",
        "1. Ship the rail. B3 is formally blocked by DRE-2496, per the plan.",
    ):
        assert linear_ops.parse_blocked_by(line) == [], line


def test_the_producer_preserves_order_and_dedupes():
    """The producer's contract is a list, not a set — relations are created in
    the order the card declares them."""
    body = "**Blocked by:** DRE-5, DRE-5, DRE-3"
    assert linear_ops.parse_blocked_by(body) == ["DRE-5", "DRE-3"]


# ---------------------------------------------------------------------------
# The DRE-2492 sentences are carried into the shared corpus, not left behind
# ---------------------------------------------------------------------------
DRE_2492_SENTENCES = (
    "Both depend on DRE-2494 only - neither depends on the other, and neither "
    "blocks anything in wave 4.",
    "DRE-2496 lands first. B3 is formally blocked by it, so it cannot start "
    "before the rail exists.",
)


@pytest.mark.parametrize("sentence", DRE_2492_SENTENCES)
def test_the_dre_2492_sentences_are_must_not_match_fixtures(sentence):
    """The five-day freeze must not be re-derivable. The sentences live in the
    shared module's corpus with an EMPTY expected id set, so any weakening of
    the anchoring during the move fails the table test above."""
    corpus = dict(blocker_prose.FIXTURES)
    assert sentence in corpus, "the DRE-2492 prose left the shared corpus"
    assert tuple(corpus[sentence]) == ()


def test_the_shared_corpus_covers_all_three_declaring_phrases():
    """A corpus that only ever says "blocked by" would let the producer's old
    narrow grammar pass this whole file."""
    declaring = " ".join(t for t, ids in blocker_prose.FIXTURES if ids).lower()
    for phrase in ("blocked by", "depends on", "serialize after"):
        assert phrase in declaring, phrase


# ---------------------------------------------------------------------------
# One module owns the pattern — nobody keeps a private copy
# ---------------------------------------------------------------------------
def test_only_the_shared_module_compiles_the_blocker_grammar():
    """The grammar's phrase alternation appears in exactly one file. A second
    copy anywhere is the defect this card exists to end."""
    owners = sorted(
        p.name
        for p in SCRIPTS.rglob("*.py")
        if "blocked by|serialize after|depends on" in p.read_text()
    )
    assert owners == ["blocker_prose.py"], owners


def test_no_module_keeps_a_private_blocked_by_pattern():
    """`linear_ops._BLOCKED_BY_RE` was the narrow producer copy. It is gone,
    and no equivalent came back."""
    for p in SCRIPTS.rglob("*.py"):
        if p.name == "blocker_prose.py":
            continue
        src = p.read_text()
        assert "blocked\\s*by" not in src, p.name
        assert "_BLOCKED_BY_RE = re.compile" not in src, p.name
        assert "_BLOCKER_LINE = re.compile" not in src, p.name


@pytest.mark.parametrize("module", [linear_ops, prose_blockers, groomer])
def test_all_three_read_the_shared_module(module):
    src = Path(module.__file__).read_text()
    assert "import blocker_prose" in src


# ---------------------------------------------------------------------------
# The door: an unresolvable blocker id is refused, never swallowed
# ---------------------------------------------------------------------------
UNRESOLVABLE = "DRE-4242"  # FakeLinear knows DRE-EPIC and DRE-100 only


def test_add_blocked_by_raises_on_an_unresolvable_id():
    """It printed to stderr and CONTINUED — the create reported success with no
    relation, which is exactly how a prose-only card gets minted."""
    with patch.object(linear_ops, "get_issue", return_value=None):
        with pytest.raises(linear_ops.LinearError) as exc:
            linear_ops._add_blocked_by("child-uuid", [UNRESOLVABLE])
    assert UNRESOLVABLE in str(exc.value)


def test_add_blocked_by_still_creates_the_relation_for_a_resolvable_id():
    """Control: the raise must not cost the happy path."""
    fake = FakeLinear()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        assert linear_ops._add_blocked_by("child-uuid", ["DRE-100"]) == ["DRE-100"]
    assert fake.relations == [("blk-100", "child-uuid")]


def _subissue(fake, tmp_path, body, *flags):
    f = tmp_path / "card.md"
    f.write_text(body)
    buf = io.StringIO()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with redirect_stdout(buf):
            linear_ops.cmd_subissue("DRE-EPIC", "Build a widget", str(f), *flags)
    return buf.getvalue()


GOOD_BODY = "**Repo:** atlas\n\nBuild it.\n\n## Acceptance criteria\n- [ ] done"


def test_subissue_with_an_unresolvable_blocker_creates_no_card(tmp_path):
    """The scenario test on the create seam: the id does not resolve, so the
    create is REFUSED and no card exists afterwards — not a card carrying prose
    that no relation backs."""
    fake = FakeLinear()
    with pytest.raises(linear_ops.LinearError) as exc:
        _subissue(fake, tmp_path, GOOD_BODY + f"\n\n**Blocked by:** {UNRESOLVABLE}")
    assert UNRESOLVABLE in str(exc.value)
    assert fake.created is None
    assert fake.relations == []


def test_oneoff_with_an_unresolvable_blocker_creates_no_card(tmp_path):
    """The other create seam, held to the same contract."""
    fake = FakeLinear()
    f = tmp_path / "card.md"
    f.write_text(GOOD_BODY + f"\n\nDepends on {UNRESOLVABLE}")
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with pytest.raises(linear_ops.LinearError) as exc:
            with redirect_stdout(io.StringIO()):
                linear_ops.cmd_oneoff(
                    "Build a widget", str(f),
                    "--label", "repo:atlas",
                    "--label", "initiative:bureau",
                    "--label", "agent:engineer",
                )
    assert UNRESOLVABLE in str(exc.value)
    assert fake.created is None


def test_a_resolvable_blocker_still_creates_the_card_and_the_relation(tmp_path):
    """Control on the seam: refusal is for the unresolvable id only."""
    fake = FakeLinear()
    out = _subissue(fake, tmp_path, GOOD_BODY + "\n\n**Blocked by:** DRE-100")
    assert fake.created is not None
    assert fake.relations == [("blk-100", "child-uuid")]
    assert "blockedBy=DRE-100" in out


def test_the_widened_producer_mints_a_relation_from_depends_on(tmp_path):
    """The gap, closed at the door: a card written `Depends on DRE-N` is what
    used to get no relation while the sweep honoured the sentence anyway."""
    fake = FakeLinear()
    out = _subissue(fake, tmp_path, GOOD_BODY + "\n\nDepends on: DRE-100")
    assert fake.relations == [("blk-100", "child-uuid")]
    assert "blockedBy=DRE-100" in out
