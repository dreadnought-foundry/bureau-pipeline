"""What the release-decision document says GitHub DELIVERS (DRE-5119).

`docs/release-decision-proof-2026-09.md` (DRE-4773) found two sentences in
`scripts/release_decision.py`'s own text that say more than GitHub does:

  1. the Record's App does not receive every `deployment_status`. GitHub never
     delivered an `inactive` status — none of 39 on 2026-09-26 — and a held
     or no-op decision is written `inactive`, so it reaches the Record only as
     the `deployment` message, whose payload carries the whole decision;
  2. `re_arm_at` is the next releasable minute, set whether or not a re-arm
     was dispatched — never "the minute the run re-armed itself for".

The document is the render (`tests/test_release_decision.py` pins that), so
these read the script's own text and the render, never the committed file.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_decision  # noqa: E402


def means(name: str) -> str:
    return {field.name: field.means for field in release_decision.FIELDS}[name]


def flat(text: str) -> str:
    return " ".join(text.split())


def test_re_arm_at_is_the_next_releasable_minute_whether_or_not_re_armed():
    said = means("re_arm_at")
    assert "next releasable minute" in said, said
    assert "whether or not a re-arm was dispatched" in said, said
    assert "re-armed itself" not in said, said


def test_the_act_field_says_held_and_no_op_are_read_from_the_deployment():
    said = means("act")
    assert "never delivers an `inactive` status" in said, said
    assert "`deployment` message" in said, said


def test_the_render_no_longer_claims_every_status_is_delivered():
    rendered = flat(release_decision.render())
    assert "receives every `deployment` and `deployment_status`" not in rendered
    assert "re-armed itself" not in rendered


def test_the_render_says_inactive_statuses_are_never_delivered():
    rendered = flat(release_decision.render())
    assert "GitHub never delivers an `inactive` status" in rendered
    # Where a hold and a no-op are mapped to `inactive`, and where a consumer
    # is told what to filter on — both say where the decision is read from.
    for heading in ("## The state follows the act", "## What a consumer filters on"):
        section = release_decision.render().split(heading, 1)[1].split("\n## ", 1)[0]
        assert "`deployment` message" in flat(section), heading
        assert "`inactive`" in section, heading
