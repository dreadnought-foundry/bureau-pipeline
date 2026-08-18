"""Scenario already_live — the work already shipped (DRE-2490).

REPLAYS: **portico#222** (2026-08-18 rejection analysis). The critic caught a
PR re-doing work that had shipped earlier the same day. Nothing in the diff
was wrong; the whole PR was waste — a build round, a review round, and a merge
conflict surface, for behavior the repository already had.

THE SEED: every criterion on the card is already satisfied on the sandbox's
default branch, module and test both.

PASS: no PR is opened, and the agent says on the carrier issue that the work is
already there. In the live pipeline that comment is the escalation/blocker text
the workflow posts to the card verbatim; here it is a GitHub comment, because
the sandbox has no Linear.

FAIL: any PR in the card's namespace (re-doing shipped work), or silence, or a
comment that never says the work already exists — "working on it" is not a
finding the CEO can act on.
"""

from __future__ import annotations

from harness import agent_scenario
from harness.framework import ScenarioFailure

MODULE_NAME = "harness_normalize.py"
TEST_NAME = "test_harness_normalize.py"
MODULE_PATH = f"{agent_scenario.PROBE_DIR}/{MODULE_NAME}"
TEST_PATH = f"{agent_scenario.PROBE_DIR}/{TEST_NAME}"

# What "the agent noticed" looks like in plain English. Deliberately a small
# vocabulary of the words that carry the FINDING, not a phrase match — the
# agent writes prose and an exact-phrase gate would fail honest answers.
SHIPPED_WORDS = (
    "already",
    "exists",
    "existing",
    "shipped",
    "in place",
    "on main",
    "no change",
    "nothing to do",
)

SEED_MODULE = '''\
"""Whitespace normalization, seeded by the bureau-pipeline integration harness."""


def normalize(text):
    """Collapse runs of whitespace and strip the ends."""
    return " ".join((text or "").split())
'''

SEED_TEST = '''\
"""Coverage for normalize(), seeded by the bureau-pipeline integration harness."""

from harness_normalize import normalize


def test_collapses_internal_runs():
    assert normalize("a   b\\t\\tc") == "a b c"


def test_strips_the_ends():
    assert normalize("  padded  ") == "padded"


def test_none_is_empty():
    assert normalize(None) == ""
'''


class AlreadyLive(agent_scenario.AgentScenario):
    name = "already_live"
    incident = "portico#222"

    def seed(self, ctx):
        self.seed_on_default(
            ctx, MODULE_PATH, SEED_MODULE, "test(harness): seed shipped work"
        )
        self.seed_on_default(
            ctx, TEST_PATH, SEED_TEST, "test(harness): seed shipped coverage"
        )

    def card_title(self, ctx):
        return "Normalize whitespace in probe text"

    def card_body(self, ctx):
        return (
            "Probe text arrives with ragged whitespace.\n\n"
            "## Acceptance criteria\n"
            f"- [ ] `{MODULE_PATH}` exposes `normalize(text)` that collapses "
            "runs of whitespace and strips the ends\n"
            f"- [ ] `normalize(None)` returns an empty string\n"
            f"- [ ] `{TEST_PATH}` covers collapsing, stripping, and the "
            "`None` case\n"
            + self.house_rules(ctx)
        )

    def verify(self, ctx):
        prs = self.agent_prs(ctx)
        if prs:
            raise ScenarioFailure(
                f"a PR was opened ({[p['number'] for p in prs]}) for work that "
                f"is already on {ctx.state['base']} — re-doing shipped work is "
                f"{self.incident}; the pre-submit walk is what catches it "
                "before the PR exists"
            )

        spoken = self.declined(ctx)
        if not spoken:
            raise ScenarioFailure(
                "the agent opened no PR (correct) but left no comment on "
                f"carrier issue #{ctx.state['issue']} — an unexplained "
                "no-op is indistinguishable from a dead run"
            )
        said = " ".join(spoken).lower()
        if not any(word in said for word in SHIPPED_WORDS):
            raise ScenarioFailure(
                "the agent commented but never says the work already exists: "
                f"{spoken[0][:300]!r} — the CEO cannot act on that"
            )
        ctx.log(f"[{self.name}] no PR, and the agent said why: {spoken[0][:200]}")


SCENARIO = AlreadyLive()
