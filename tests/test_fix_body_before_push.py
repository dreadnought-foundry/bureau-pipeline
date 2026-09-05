"""The fixing agent finalises the PR body BEFORE it pushes (DRE-3011).

The race this closes
--------------------
The critic reads the pull request body LIVE (`gh pr view "$PR" --json body`,
qa-review.yml) — that is the right design and this card does not touch it. The
defect is on the AUTHOR's side of the same clock: portico #407's fix agent
edited its description, pushed, and then self-corrected the description again
two minutes after the push had already fired the review. The verdict quoted the
text that had been gone for five minutes, asked for the correction that was
already in the body, and the PR sat blocked for eleven hours over a diff nobody
ever alleged a defect in.

The CEO's decision on this card (2026-09-05) picked exactly one of the three
candidate fixes: **the fix agent edits the pull-request body before it pushes**,
so the description the critic reads is the corrected one. The other two — a
delayed critic read and a body-snapshot timestamp on the verdict — were not to
be built here, and DRE-3005 had already shipped both on the critic's side
(`BODY SNAPSHOT` in the critic prompt, `body_read_at` on the verdict).

Why a test and not just prompt text
-----------------------------------
The same reason `tests/test_presubmit_gate_prompt.py` exists: a prompt is the
one part of this pipeline with no compiler and no runtime error, so an
obligation that quietly evaporates in a later edit of a 120-line prompt block
fails SILENTLY, and its only symptom is another eleven-hour block months later.
The pins here are structural — on the PARSED workflow input, not a grep of the
file — and they pin ORDER, because order is the whole card.

The last class of test drives the sequence end to end: it replays the fix
agent's actions in the order the live prompt numbers them, against a critic that
snapshots the body when the push fires, and asserts the verdict was composed
from the CORRECTED description. The control replays #407's actual order (push,
then the body edit) and gets the stale verdict back — so the harness is known to
tell the two sequences apart rather than passing on both.
"""

import os
import re
import unittest

import yaml

REPO = os.path.join(os.path.dirname(__file__), "..")
WF_DIR = os.path.join(REPO, ".github", "workflows")

FIX_WF = "agent-fix.yml"
QA_WF = "qa-review.yml"

# The two actions whose ORDER is this card. The body edit is located by the
# command the prompt actually hands the agent; the push by the phrase that
# names the one push it is allowed to make (`tests/test_presubmit_gate_prompt.py`
# pins the same phrase for the pre-push gate, so the two files agree on which
# step is the push).
BODY_EDIT_RE = re.compile(r"gh pr edit")
PUSH_RE = re.compile(r"(?i)push to the SAME branch")

# The obligation itself, phrased loosely enough to survive a rewording and
# tightly enough that a REMOVAL cannot hide.
ALL_EDITS_RE = re.compile(r"(?i)\bevery\b[^.]{0,40}\bedit\b[^.]{0,60}\bbody\b")
AFTER_PUSH_BAN_RE = re.compile(
    r"(?i)\b(do not|don't|never)\b[^.]{0,40}\bedit\b[^.]{0,40}\bbody\b[^.]{0,40}\bafter\b"
)
# The reason has to be IN the prompt: an agent told "edit the body first" with
# no why reorders it back the moment some other instruction reads more urgent.
LIVE_READ_REASON_RE = re.compile(r"(?i)(critic|review|reviewer)[^.]{0,140}\breads?\b[^.]{0,60}\bbody\b")

# The live read must survive this card (the card's "what must not break"):
# the critic reads the CURRENT body from the API, never the event payload's
# frozen copy.
QA_LIVE_READ_RE = re.compile(r"gh pr view \"\$PR\" --json body")
QA_PAYLOAD_BODY_RE = re.compile(r"github\.event\.pull_request\.body")

# #407's texts, in the roles they actually played.
STALE_BODY = "the non-visual e2e set is not wired into CI"
CORRECTED_BODY = "In CI, on every PR, the e2e set runs"


def workflow(name: str) -> dict:
    with open(os.path.join(WF_DIR, name)) as f:
        return yaml.safe_load(f)


def agent_prompt(name: str) -> str:
    """The `prompt:` input of the workflow's claude-code-action step, read from
    the PARSED yaml — the string the agent actually receives."""
    prompts = []
    for job in workflow(name)["jobs"].values():
        for step in job.get("steps") or []:
            if "anthropics/claude-code-action" in (step.get("uses") or ""):
                prompts.append((step.get("with") or {}).get("prompt"))
    assert len(prompts) == 1, f"{name}: expected one agent step, got {len(prompts)}"
    assert prompts[0], f"{name}: agent step has no prompt"
    return prompts[0]


def norm(text: str) -> str:
    """Whitespace-collapsed: the prompt is hard-wrapped at ~70 columns, so every
    pin has to read ACROSS a line break or a reflow looks like a removal."""
    return re.sub(r"\s+", " ", text)


def numbered_steps(prompt: str) -> list:
    """The prompt's mandatory ordered list as [(label, body)] in written order,
    including the house's inserted-step form ("4b.")."""
    items = []
    marker = re.compile(r"(?m)^\s*(\d+[a-z]?)\.\s")
    hits = list(marker.finditer(prompt))
    for i, hit in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(prompt)
        items.append((hit.group(1), prompt[hit.end() : end]))
    return items


def step_indexes(items: list, pattern) -> list:
    return [i for i, (_, body) in enumerate(items) if pattern.search(norm(body))]


def body_edit_step(items: list) -> tuple:
    """(index, body) of the step that edits the PR body, with a failure that
    says which obligation went missing rather than an IndexError."""
    hits = step_indexes(items, BODY_EDIT_RE)
    assert hits, (
        "agent-fix.yml: no numbered step tells the agent to edit the PR body — "
        "the description the critic reads is nobody's job"
    )
    return hits[0], items[hits[0]][1]


# ── The sequence harness ──────────────────────────────────────────────────
# A deliberately small model of the two clocks that collided on #407. It knows
# one fact about the critic — it reads the body when the push fires it — and
# nothing about who is right.


class PullRequest:
    def __init__(self, body):
        self.body = body


class Critic:
    """Reads the description LIVE when the push triggers the review, and writes
    its verdict later from what it read (qa-review.yml: the read happens at the
    top of the run, the verdict minutes after)."""

    def __init__(self):
        self.snapshot = None

    def push_fired(self, pr):
        self.snapshot = pr.body

    def verdict(self):
        assert self.snapshot is not None, "no review was ever triggered"
        return f"the description says: {self.snapshot}"


def replay(actions):
    """Run the fix agent's actions in order and return (verdict, final body).
    Each action is ("write_body", text) or ("push", None)."""
    pr = PullRequest("")
    critic = Critic()
    for action, text in actions:
        if action == "write_body":
            pr.body = text
        elif action == "push":
            critic.push_fired(pr)
        else:  # pragma: no cover - guards a typo'd action name
            raise AssertionError(f"unknown action {action!r}")
    return critic.verdict(), pr.body


def prompt_action_order() -> list:
    """The order the LIVE prompt puts the two actions in. Derived from the
    workflow, never restated here — that is what makes the replay below a test
    of the pipeline rather than of this file."""
    items = numbered_steps(agent_prompt(FIX_WF))
    order = []
    for _, body in items:
        flat = norm(body)
        if BODY_EDIT_RE.search(flat):
            order.append("edit_body")
        if PUSH_RE.search(flat):
            order.append("push")
    return order


def bans_late_body_edit() -> bool:
    """Does the live prompt forbid editing the description after the push?"""
    items = numbered_steps(agent_prompt(FIX_WF))
    return AFTER_PUSH_BAN_RE.search(norm(body_edit_step(items)[1])) is not None


def fix_agent_run() -> list:
    """#407's run, replayed against the LIVE prompt.

    The facts of the run are fixed and are not this card's to change: the agent
    wrote a claim into the description, and later realised that claim was wrong
    and corrected it. What the prompt decides is WHEN the correction lands — if
    the prompt requires the description to be final before the push (and says
    so), the correction goes into the pre-push edit; if it does not, the agent
    corrects the body when it notices, which on #407 was two minutes after the
    push had already fired the review.
    """
    ban = bans_late_body_edit()
    actions = []
    for step in prompt_action_order():
        if step == "edit_body":
            actions.append(("write_body", CORRECTED_BODY if ban else STALE_BODY))
        else:
            actions.append(("push", None))
    if not ban:
        actions.append(("write_body", CORRECTED_BODY))
    return actions


class BodyEditPrecedesThePushTest(unittest.TestCase):
    """The ordering pin: every PR-body edit is instructed before the push."""

    def setUp(self):
        self.prompt = agent_prompt(FIX_WF)
        self.items = numbered_steps(self.prompt)

    def test_the_body_edit_step_comes_before_the_push_step(self):
        edit_idx, _ = body_edit_step(self.items)
        push_hits = step_indexes(self.items, PUSH_RE)
        self.assertTrue(push_hits, "agent-fix.yml: no commit-and-push step found")
        self.assertLess(
            edit_idx,
            push_hits[-1],
            "the PR-body edit must be instructed BEFORE the push — the push "
            "fires the review and the critic reads the body seconds later, so "
            "a description corrected afterwards is invisible to the verdict "
            "(portico #407)",
        )

    def test_no_body_edit_is_instructed_after_the_push(self):
        push_hits = step_indexes(self.items, PUSH_RE)
        self.assertTrue(push_hits)
        late = [i for i in step_indexes(self.items, BODY_EDIT_RE) if i > push_hits[-1]]
        self.assertEqual(
            late,
            [],
            "a `gh pr edit` after the push step recreates the exact race this "
            f"card closes (steps {[self.items[i][0] for i in late]})",
        )

    def test_the_step_says_every_body_edit_happens_there(self):
        _, body = body_edit_step(self.items)
        self.assertRegex(
            norm(body),
            ALL_EDITS_RE,
            "the gate must cover EVERY edit to the description, not only the "
            "unmet-criteria section — #407's fatal edit was an ordinary "
            "self-correction",
        )

    def test_the_step_forbids_editing_the_body_after_the_push(self):
        _, body = body_edit_step(self.items)
        self.assertRegex(
            norm(body),
            AFTER_PUSH_BAN_RE,
            "'edit early' without 'never edit late' leaves the #407 sequence "
            "available: the agent pushed and THEN corrected the description",
        )

    def test_the_step_says_why_the_order_matters(self):
        _, body = body_edit_step(self.items)
        self.assertRegex(
            norm(body),
            LIVE_READ_REASON_RE,
            "the prompt must name the mechanism — the reviewer reads the body "
            "off the push — or the ordering reads as arbitrary bookkeeping and "
            "gets reshuffled by the next prompt edit",
        )

    def test_the_obligation_is_not_optional_language(self):
        soft = re.compile(r"(?i)\b(if you can|if time|consider |optionally|ideally)\b")
        _, body = body_edit_step(self.items)
        self.assertNotRegex(norm(body), soft)


class TheSequenceTest(unittest.TestCase):
    """Drive the actual sequence: body edit, push, verdict composed later."""

    def test_the_prompt_instructs_exactly_one_push(self):
        order = prompt_action_order()
        self.assertEqual(
            order.count("push"), 1, f"expected exactly one push in the prompt, got {order}"
        )
        self.assertGreaterEqual(order.count("edit_body"), 1)

    def test_the_verdict_reflects_the_current_body(self):
        verdict, final_body = replay(fix_agent_run())
        self.assertEqual(
            final_body,
            CORRECTED_BODY,
            "the agent's self-correction is a fact of the run — the replay lost it",
        )
        self.assertIn(
            CORRECTED_BODY,
            verdict,
            "the critic composed its verdict from a description the author had "
            "already corrected — the #407 race is still open",
        )
        self.assertNotIn(STALE_BODY, verdict)
        self.assertIn(
            final_body,
            verdict,
            "the verdict must quote the body as it stands when the review runs",
        )

    def test_the_407_sequence_is_what_produces_a_stale_verdict(self):
        # The control. #407's order — the claim written, the push, then the
        # self-correction — and the harness must return the STALE verdict, or
        # the assertion above would pass for a prompt in any order at all.
        verdict, final_body = replay(
            [("write_body", STALE_BODY), ("push", None), ("write_body", CORRECTED_BODY)]
        )
        self.assertIn(STALE_BODY, verdict)
        self.assertNotIn(CORRECTED_BODY, verdict)
        self.assertNotIn(final_body, verdict)


class TheLiveReadSurvivesTest(unittest.TestCase):
    """The card's "what must not break": this narrows WHEN the author writes,
    it does not move the critic back onto a cached body."""

    def test_the_critic_still_reads_the_body_from_the_api(self):
        text = open(os.path.join(WF_DIR, QA_WF)).read()
        self.assertRegex(
            text,
            QA_LIVE_READ_RE,
            "qa-review.yml must still read the CURRENT body with `gh pr view "
            "--json body` — reviewing from a cached copy is strictly worse",
        )

    def test_the_critic_never_reviews_the_event_payloads_frozen_body(self):
        text = open(os.path.join(WF_DIR, QA_WF)).read()
        self.assertNotRegex(
            text,
            QA_PAYLOAD_BODY_RE,
            "the event payload's body is frozen at trigger time — the very "
            "snapshot this card exists to stop the critic reviewing",
        )


if __name__ == "__main__":
    unittest.main()
