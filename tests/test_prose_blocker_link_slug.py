"""TDD for "a card id in a link's URL slug is not a declaration" (DRE-3161).

The prose-blocker detector reads every `DRE-\\d+` on a declaring line, case
insensitively, across the WHOLE line — markup, hrefs and URL slugs included. A
Linear issue mention renders as a markdown link whose target carries the linked
card's TITLE as a slug, so a card whose title happens to name another card
poisons every `**Blocked by:**` line that links it.

Seen live on 2026-09-04 16:16 PT on DRE-3061, the One River cutover card. Its
`**Blocked by:**` line names fourteen linked cards plus one bare id, every one
of them a real `blockedBy` relation — and the sweep bounced it to Triage with a
`🚨 prose-blocker-no-relation` receipt for "declaring a dependency on DRE-3106",
a card the line never names. DRE-3106 appears once, inside the slug of the
DRE-3109 mention:

    …/issue/DRE-3109/operator-confirm-the-design-contract-is-on-main-pr-2280-dre-3106

The detector itself is right: DRE-3111 and DRE-3112 were bounced the same
afternoon for a real reason — a card id in the PROSE of the line. Only the
tokeniser is wrong, so this narrows WHERE ids are read from and nothing else.
Case-insensitive comparison stays (`DRE-9` and `dre-9` are the same
declaration, deliberately); what changes is that the ids come from the line's
TEXT — a mention's label — never from a link target.

Reconcile governs promotion for EVERY product repo, and this defect routes
well-formed cards to the broken-card lane — hence test-first.

Run: cd bureau-pipeline && python3 -m pytest tests/test_prose_blocker_link_slug.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import blocker_prose  # noqa: E402
import prose_blockers  # noqa: E402
import reconcile  # noqa: E402


# DRE-3061's live `**Blocked by:**` line, VERBATIM as Linear's API returns it
# (read 2026-09-06). One source line per mention for readability; the string is
# byte-for-byte the card's, which is the point — a hand-simplified fixture is
# how a tokeniser bug survives its own regression test.
DRE_3061_BLOCKED_BY = (
    '**Blocked by:** [DRE-3062](https://linear.app/dreadnoughtfoundry/issue/DRE-3062/one-rivers-tabs-are-url-addressable-so-each-one-can-be-rendered-and), '
    '[DRE-3063](https://linear.app/dreadnoughtfoundry/issue/DRE-3063/register-one-rivers-three-screens-in-screensmjs-without-this-the), '
    '[DRE-3064](https://linear.app/dreadnoughtfoundry/issue/DRE-3064/the-consoles-own-suite-asserts-the-one-river-decisions-a-screenshot), '
    '[DRE-3068](https://linear.app/dreadnoughtfoundry/issue/DRE-3068/operator-prove-phase-4-the-capacity-strip-read-against-the-live), '
    '[DRE-3107](https://linear.app/dreadnoughtfoundry/issue/DRE-3107/deploylag-the-deploy-lag-number-the-cli-prints-answers-over-graphql), '
    '[DRE-3108](https://linear.app/dreadnoughtfoundry/issue/DRE-3108/activityfeed-escalations-plans-runs-and-merges-in-one-stream-the), '
    '[DRE-3109](https://linear.app/dreadnoughtfoundry/issue/DRE-3109/operator-confirm-the-design-contract-is-on-main-pr-2280-dre-3106), '
    '[DRE-3110](https://linear.app/dreadnoughtfoundry/issue/DRE-3110/one-river-shell-dashboardnext-a-temporary-sidebar-entry-the-kpi-ribbon), '
    '[DRE-3111](https://linear.app/dreadnoughtfoundry/issue/DRE-3111/wave-ranking-and-n-cards-wait-on-it-one-pure-function-over-the), '
    '[DRE-3112](https://linear.app/dreadnoughtfoundry/issue/DRE-3112/the-one-river-row-card-pr-what-is-happening-six-stage-journey-who), '
    '[DRE-3113](https://linear.app/dreadnoughtfoundry/issue/DRE-3113/overview-tab-the-river-every-card-of-every-epic-in-run-order-waves-as), '
    '[DRE-3114](https://linear.app/dreadnoughtfoundry/issue/DRE-3114/activity-tab-everything-that-moved-one-feed-header-sort-day-groups), '
    '[DRE-3115](https://linear.app/dreadnoughtfoundry/issue/DRE-3115/pull-requests-tab-every-open-pr-at-once-the-ones-that-need-you-first), '
    '[DRE-3116](https://linear.app/dreadnoughtfoundry/issue/DRE-3116/the-capacity-strip-accounts-dispatch-quota-and-deploy-lag-on-one-line), '
    '[DRE-3123](https://linear.app/dreadnoughtfoundry/issue/DRE-3123/alerts-tab-the-alerts-pages-feed-on-the-one-grammar-fourth-in-the), '
    'DRE-3160'
)

# The sixteen cards the line NAMES, in the order it names them. DRE-3106 is not
# one of them: it is the tail of DRE-3109's slug.
DRE_3061_DECLARED = [
    "DRE-3062", "DRE-3063", "DRE-3064", "DRE-3068", "DRE-3107", "DRE-3108",
    "DRE-3109", "DRE-3110", "DRE-3111", "DRE-3112", "DRE-3113", "DRE-3114",
    "DRE-3115", "DRE-3116", "DRE-3123", "DRE-3160",
]

# The id that only ever appears inside another card's slug.
SLUG_ONLY = "DRE-3106"

# The same defect in one line, small enough to ride the shared fixture corpus
# so all three consumers of the grammar are held to it.
SLUG_FIXTURE = (
    "**Blocked by:** [DRE-3109](https://linear.app/dreadnoughtfoundry/issue/"
    "DRE-3109/operator-confirm-the-design-contract-is-on-main-pr-2280-dre-3106)"
)


@pytest.fixture(autouse=True)
def _pin_and_reset(monkeypatch):
    """reconcile.REPO_SLUG is bound at import; pin it so promote_ready
    recognises these agent-bureau cards regardless of collection order."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._stale_defects.clear()
    reconcile._card_skips.clear()


def _relation(identifier: str, state: str = "Done") -> dict:
    return {"type": "blocks", "issue": {"identifier": identifier, "state": {"name": state}}}


def _card(description: str, identifier="DRE-3061", relations=()) -> dict:
    """A Backlog child eligible on every other ground, so only the dependency
    gate and the prose-defect refusal can hold it back."""
    return {
        "identifier": identifier,
        "description": "**Repo:** agent-bureau\n" + description,
        "parent": {"identifier": "DRE-3060", "state": {"name": "In Progress"}},
        "labels": {"nodes": [{"name": "size:M"}]},
        "comments": {"nodes": []},
        "inverseRelations": {"nodes": list(relations)},
    }


# ---------------------------------------------------------------------------
# 1. The parser: ids come from the line's text, not from a link target
# ---------------------------------------------------------------------------
def test_the_live_line_declares_exactly_the_cards_it_names():
    """The regression fixture: DRE-3061's real line, unedited."""
    assert blocker_prose.blocker_ids(DRE_3061_BLOCKED_BY) == DRE_3061_DECLARED


def test_the_slug_only_id_is_not_declared():
    """The bounce, stated on its own: DRE-3106 is the tail of DRE-3109's slug
    and nothing on this line declares it."""
    assert SLUG_ONLY not in blocker_prose.blocker_ids(DRE_3061_BLOCKED_BY)


def test_a_mention_declares_the_card_it_links():
    """Narrowing must not cost the declaration itself — the mention's own id is
    its label, and it is still read."""
    assert blocker_prose.blocker_ids(SLUG_FIXTURE) == ["DRE-3109"]


def test_a_bare_id_beside_mentions_is_still_declared():
    """DRE-3061's line ends with an unlinked `DRE-3160`. Plain text is the one
    thing this card never stops reading."""
    assert "DRE-3160" in blocker_prose.blocker_ids(DRE_3061_BLOCKED_BY)


def test_a_link_label_that_is_not_a_card_id_declares_nothing():
    """A prose label over an issue href: the href is not text, so it declares
    nothing. The author who meant it names the card."""
    line = (
        "**Blocked by:** [the design contract](https://linear.app/"
        "dreadnoughtfoundry/issue/DRE-3106/one-river-the-design-contract)"
    )
    assert blocker_prose.blocker_ids(line) == []


def test_a_bare_linear_url_declares_the_card_it_addresses_not_its_slug():
    """A pasted URL has no label to read. The id it ADDRESSES is text; the slug
    after it is another card's title, and dropping the whole URL would fail
    unsafe — a real declaration that mints no relation at the door."""
    line = (
        "Blocked by: https://linear.app/dreadnoughtfoundry/issue/DRE-3109/"
        "operator-confirm-the-design-contract-is-on-main-pr-2280-dre-3106"
    )
    assert blocker_prose.blocker_ids(line) == ["DRE-3109"]


def test_case_insensitive_comparison_is_untouched():
    """The rule this card explicitly does NOT change: `dre-9` and `DRE-9` are
    the same declaration, and every id comes out uppercased."""
    assert blocker_prose.blocker_ids("Blocked by: dre-9") == ["DRE-9"]
    assert blocker_prose.blocker_ids("**Blocked by:** [dre-9](https://x/y)") == ["DRE-9"]


# ---------------------------------------------------------------------------
# 2. The control: a card named in the line's PROSE still bounces
# ---------------------------------------------------------------------------
def test_an_id_in_the_prose_of_the_line_is_still_declared():
    """Why DRE-3111 and DRE-3112 were bounced correctly the same afternoon. The
    detector is right; only its tokeniser was wrong."""
    line = DRE_3061_BLOCKED_BY + " — and DRE-3106 must be on main first"
    assert blocker_prose.blocker_ids(line) == DRE_3061_DECLARED + [SLUG_ONLY]


def test_a_text_named_card_with_no_relation_is_still_an_undeclared_claim():
    card = _card("**Blocked by:** DRE-700")
    assert prose_blockers.undeclared_claims(card) == {"DRE-700"}


def test_a_text_named_card_with_no_relation_still_bounces_to_triage():
    """The check keeps working exactly as today for the defect it exists for."""
    card = _card("**Blocked by:** DRE-700", identifier="DRE-900")
    with patch.object(reconcile, "backlog_children", return_value=[card]), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 0
    advance.assert_called_once_with("DRE-900", "Triage", "Backlog")


# ---------------------------------------------------------------------------
# 3. The sweep, end to end: DRE-3061 is not a defective card
# ---------------------------------------------------------------------------
def _dre_3061() -> dict:
    """The live card: the real line, and the real relation behind every id it
    names."""
    return _card(
        DRE_3061_BLOCKED_BY,
        relations=[_relation(ident) for ident in DRE_3061_DECLARED],
    )


def test_the_live_card_has_no_undeclared_claim():
    assert prose_blockers.undeclared_claims(_dre_3061()) == set()


def test_the_live_card_is_not_bounced_and_promotes():
    """What the sweep did on 2026-09-04, and what it must do instead: every
    blocker is Done, so the card promotes — no Triage move, no receipt."""
    with patch.object(reconcile, "backlog_children", return_value=[_dre_3061()]), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 1
    advance.assert_called_once_with("DRE-3061", "Todo", "Backlog")
    assert not [
        c for c in comment.call_args_list
        if len(c.args) > 1 and prose_blockers.CARD_TAG in c.args[1]
    ]


def test_the_receipt_never_names_the_slug_only_card(capsys):
    """The failure signature itself: `🚨 prose-blocker-no-relation … declares a
    dependency on DRE-3106`. It must not be printable from this line."""
    with patch.object(reconcile, "backlog_children", return_value=[_dre_3061()]), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance"), \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.promote_ready(active_count=0)
    out = capsys.readouterr().out
    assert prose_blockers.CARD_TAG not in out
    assert SLUG_ONLY not in out


# ---------------------------------------------------------------------------
# 4. All three consumers, not just the detector
# ---------------------------------------------------------------------------
def test_the_slug_case_rides_the_shared_fixture_corpus():
    """The producer mints relations from the same lines the detector judges. A
    fix in one and not the other is the drift `blocker_prose` exists to end
    (DRE-2922), so the case lives in the shared corpus that every consumer is
    driven through by `tests/test_one_blocker_prose_parser.py`."""
    corpus = dict(blocker_prose.FIXTURES)
    assert SLUG_FIXTURE in corpus, "the link-slug case is not in the shared corpus"
    assert tuple(corpus[SLUG_FIXTURE]) == ("DRE-3109",)
