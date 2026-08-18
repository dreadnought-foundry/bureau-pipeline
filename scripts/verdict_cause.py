#!/usr/bin/env python3
"""The CAUSE a REQUEST_CHANGES verdict names — one vocabulary, one reader.

DRE-2489. Answering "why do our first passes fail?" took a hand
classification of 125 rejection bodies: ~46% turned out to be unmet card
criteria rather than defects, and that number drove DRE-2487's pre-submit
gate. The verdict already stamps its model (2026-08-12); this module makes
the rejection CAUSE equally queryable, so the next version of that question
is a filter over comment headers rather than a data pull.

THE LINE. When the critic blocks, its verdict file's first line reads

    VERDICT: REQUEST_CHANGES cause:<tag>

and qa-review.yml's post step composes the posted header from it unchanged:

    🔎 QA Critic — VERDICT: REQUEST_CHANGES cause:defect @<40-hex> content:<64-hex>

`VERDICT: APPROVE` lines are untouched — an approval has no blocking reason
to name, and every consumer that greps the literal `VERDICT: APPROVE`
substring (reconcile's approved-but-red sweep, qa-review's own
sync_review_state guard) keeps matching byte for byte.

WHERE THE TOKEN SITS, and why it sits there. Between the verdict word and
the workflow's `@<sha>`. That position is the only one that is safe on both
sides: the content id (DRE-2340) is matched ANCHORED TO END OF LINE, so
nothing may be appended after it, and the merge gate's own verdict regex
reads `VERDICT:\\s*([A-Z_]+)` and stops at the word, so nothing before the
sha disturbs it either.

READ ONLY IN THE STRUCTURED POSITION. `verdict_cause()` matches the tag
immediately after the verdict word and nowhere else. The middle of that line
is written by the critic AGENT, which has just read a diff anyone can author
— the same reason verdict_content.py end-anchors its own field. A `cause:`
appearing later in prose is data, not a tag.

FIXED VOCABULARY, FAIL-CLOSED. A token outside CAUSE_TAGS reads as NO cause
rather than as a fifth category: a fork shows up in the measurement as
untagged verdicts (visible, countable) instead of as a new bucket nobody
declared. tests/test_verdict_cause_tag.py pins the four strings and asserts
both critic prompts spell them identically — a prompt has no compiler, and a
tag that drifts in one of the two duplicated prompt blocks would fork the
measurement by attempt number.

`verdict_decision()` is the other half: the verdict WORD off a line that may
now carry a cause. check_critic_result.py reads it on the max-turns path,
where a completed review keeps its verdict only if that verdict declares a
legal decision — a cause tag must never make that unreadable.
"""

from __future__ import annotations

import re

#: The four blocking reasons a rejection may name, spelled exactly as both
#: critic prompts in qa-review.yml spell them. THE contract of this card: the
#: strings are the measurement. Adding a fifth means editing this tuple, both
#: prompts and the test in one commit — which is the point.
CAUSE_TAGS = ("unmet-criteria", "defect", "unverified-claim", "scope")

#: The field name on the verdict line. Written identically here, in both
#: prompts and in the tests.
CAUSE_PREFIX = "cause:"

#: The structured position: the verdict word, then optionally the cause. The
#: verdict word is captured the way merge_gate._verdict_re captures it, so
#: the two readers agree about where the word ends.
_LINE_RE = re.compile(
    r"\bVERDICT:\s*([A-Za-z_]+)(?:\s+" + re.escape(CAUSE_PREFIX) + r"([a-z][a-z-]*))?"
)


def verdict_decision(line: str | None) -> str | None:
    """The verdict WORD on a verdict line, or None when there is none.

    Reads the word alone, so `VERDICT: REQUEST_CHANGES cause:defect` and the
    pre-DRE-2489 `VERDICT: REQUEST_CHANGES` resolve identically. Callers
    decide which words are legal; this only says what was written.
    """
    if not line:
        return None
    m = _LINE_RE.search(line)
    return m.group(1) if m else None


def verdict_cause(line: str | None) -> str | None:
    """The cause tag a verdict line carries, or None when it carries none.

    None is the pre-DRE-2489 format — every verdict in flight on the fleet
    the day this ships — and it is also the answer for an APPROVE (which
    never carries one) and for a tag outside CAUSE_TAGS (see the
    fail-closed note in the module docstring).
    """
    if not line:
        return None
    m = _LINE_RE.search(line)
    if not m:
        return None
    tag = m.group(2)
    return tag if tag in CAUSE_TAGS else None
