"""The critic's REQUEST_CHANGES verdict names its cause (DRE-2489).

WHY THIS EXISTS. "Why do our first passes fail?" cost a hand classification of
125 rejection bodies to answer once: ~46% turned out to be unmet card criteria
rather than defects, and that number drove DRE-2487's pre-submit gate. The
verdict already stamps its model (2026-08-12); this card makes the rejection
CAUSE equally queryable, so the next version of that question is a filter over
comment headers instead of a data pull.

THE CONTRACT. When the critic blocks, the verdict's first line reads

    VERDICT: REQUEST_CHANGES cause:<tag>

with exactly one tag from a FIXED four-word vocabulary. qa-review.yml's post
step composes the posted header from that line unchanged:

    🔎 QA Critic — VERDICT: REQUEST_CHANGES cause:defect @<40-hex> content:<64-hex>

APPROVE lines are untouched.

THE SHARP EDGE is compatibility, and it is why this file is mostly parser
tests rather than prompt tests. That header line is the merge gate's read of
whether a PR may merge; every verdict in flight on the fleet the day this
ships carries no cause, and the content id (DRE-2340) is matched ANCHORED TO
END OF LINE — a new token in the wrong place stops the gate from carrying
verdicts and re-opens the DRE-2340 livelock. So every parser that reads the
header line is run against all four fixtures — old REQUEST_CHANGES, new
REQUEST_CHANGES with a cause, APPROVE, and a carried verdict — and must
resolve the verdict word, the bound sha and the content id identically.

The tag vocabulary is pinned to exactly four strings in ONE place
(scripts/verdict_cause.py) and asserted to be spelled identically in both
critic prompts. A prompt has no compiler: a fifth tag added in one prompt and
not the other forks the measurement silently, and the only symptom is a query
that quietly stops summing to 100%.
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
WF_DIR = os.path.join(ROOT, ".github", "workflows")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import check_critic_result  # noqa: E402
import merge_gate  # noqa: E402
import publish_review_check  # noqa: E402
import should_review_pr  # noqa: E402
import verdict_cause as vc  # noqa: E402
from verdict_content import verdict_content_id  # noqa: E402

# ── the contract strings ───────────────────────────────────────────────────
# Written identically here, in scripts/verdict_cause.py and in both critic
# prompts. This measurement stands on the strings being byte-identical in all
# three places, so they are typed out literally here rather than imported —
# an import would happily agree with a fork.
EXPECTED_PREFIX = "cause:"
EXPECTED_TAGS = ("unmet-criteria", "defect", "unverified-claim", "scope")

QA_LOGIN = "agent-bureau-qa-bot[bot]"
HEAD = "a" * 40
STALE = "b" * 40
CONTENT = "c" * 64


# ── fixtures ───────────────────────────────────────────────────────────────

def header(word, *, cause=None, sha=HEAD, content=CONTENT):
    """A posted verdict header line, composed exactly as qa-review.yml's post
    step composes it: the critic's own first line, then the WORKFLOW's `@sha`
    and (since DRE-2340) its content id, last on the line."""
    line = f"🔎 QA Critic — VERDICT: {word}"
    if cause:
        line += f" {EXPECTED_PREFIX}{cause}"
    line += f" @{sha}"
    if content:
        line += f" content:{content}"
    return line


def verdict_file(word, cause=None):
    """The critic's own /tmp/qa-verdict.md — what publish_review_check.py and
    check_critic_result.py read (no marker, no sha, no content id)."""
    first = f"VERDICT: {word}"
    if cause:
        first += f" {EXPECTED_PREFIX}{cause}"
    return (
        f"{first}\n\n"
        "## Summary\n"
        "The change does what the card asked for.\n"
    )


class Fixture:
    def __init__(self, name, word, cause, sha):
        self.name = name
        self.word = word
        self.cause = cause
        self.sha = sha
        self.line = header(word, cause=cause, sha=sha)
        self.body = verdict_file(word, cause)


#: THE FOUR the card names. `carried` is a genuine APPROVE earned on an older
#: commit whose content id still equals this head's — the DRE-2340 carry, the
#: one path where the header line is parsed for a sha that is NOT the head.
FIXTURES = (
    Fixture("old_request_changes", "REQUEST_CHANGES", None, HEAD),
    Fixture("new_request_changes", "REQUEST_CHANGES", "defect", HEAD),
    Fixture("approve", "APPROVE", None, HEAD),
    Fixture("carried", "APPROVE", None, STALE),
)

#: A carried REJECTION with a cause: the gate carries a REQUEST_CHANGES the
#: same way it carries an APPROVE (merge_gate.evaluate_critic), so the new
#: token has to survive that path too.
CARRIED_WITH_CAUSE = Fixture(
    "carried_request_changes", "REQUEST_CHANGES", "unmet-criteria", STALE
)


# ── workflow readers ───────────────────────────────────────────────────────

def workflow(name):
    with open(os.path.join(WF_DIR, name)) as f:
        return yaml.safe_load(f)


def critic_prompts():
    """The `prompt:` input of both critic attempts, from the PARSED yaml —
    the string the agent actually receives, not a grep of the file."""
    steps = workflow("qa-review.yml")["jobs"]["review"]["steps"]
    by_id = {s.get("id"): s for s in steps}
    out = []
    for sid in ("critic", "critic_retry"):
        step = by_id.get(sid)
        assert step, f"qa-review.yml has no step id={sid!r}"
        prompt = (step.get("with") or {}).get("prompt")
        assert prompt, f"step {sid} has no prompt"
        out.append((sid, prompt))
    return out


def norm(text):
    """Whitespace-collapsed. The prompts are hard-wrapped at ~70 columns, so a
    semantic pin has to read ACROSS a line break or a reflow of the same words
    looks like a removal."""
    return re.sub(r"\s+", " ", text)


def run_blocks(name):
    doc = workflow(name)
    out = []
    for job in doc["jobs"].values():
        for step in job.get("steps") or []:
            if step.get("run"):
                out.append(step["run"])
    return out


# ── 1. the vocabulary is exactly four strings ──────────────────────────────

class VocabularyIsPinnedTest(unittest.TestCase):
    """One list, four entries, in one module. A later edit cannot fork it
    without turning this red."""

    def test_the_module_pins_exactly_these_four_tags_in_this_order(self):
        self.assertEqual(tuple(vc.CAUSE_TAGS), EXPECTED_TAGS)

    def test_the_prefix_is_the_contract_string(self):
        self.assertEqual(vc.CAUSE_PREFIX, EXPECTED_PREFIX)

    def test_every_tag_is_read_back_off_a_verdict_line(self):
        for tag in EXPECTED_TAGS:
            with self.subTest(tag=tag):
                line = header("REQUEST_CHANGES", cause=tag)
                self.assertEqual(vc.verdict_cause(line), tag)

    def test_a_tag_outside_the_vocabulary_reads_as_no_cause(self):
        # Fail-closed for the measurement: an invented tag shows up as
        # "untagged", never as a silent fifth category.
        line = header("REQUEST_CHANGES", cause="flaky-ci")
        self.assertIsNone(vc.verdict_cause(line))

    def test_a_verdict_without_a_cause_reads_as_no_cause(self):
        self.assertIsNone(vc.verdict_cause(header("REQUEST_CHANGES")))
        self.assertIsNone(vc.verdict_cause(header("APPROVE")))

    def test_a_cause_outside_the_structured_position_is_not_read(self):
        # The critic reads an attacker-authored diff and writes the middle of
        # this line. Only the token immediately after the verdict word counts
        # — the same discipline verdict_content.py's end-anchor applies to the
        # content id.
        line = (
            "🔎 QA Critic — VERDICT: REQUEST_CHANGES @" + HEAD
            + " (the PR body says cause:scope) content:" + CONTENT
        )
        self.assertIsNone(vc.verdict_cause(line))


# ── 2. both critic prompts carry the same contract ─────────────────────────

class BothCriticPromptsCarryTheContractTest(unittest.TestCase):
    """qa-review.yml runs the critic twice (attempt + retry) from two
    duplicated prompt blocks that GitHub Actions cannot DRY. A tag added to
    one and not the other forks the vocabulary by attempt number."""

    def test_each_prompt_names_the_prefix_and_every_tag_identically(self):
        for sid, prompt in critic_prompts():
            with self.subTest(step=sid):
                self.assertIn(EXPECTED_PREFIX, prompt)
                for tag in EXPECTED_TAGS:
                    self.assertIn(tag, prompt, f"{sid}: tag {tag!r} missing")

    def test_the_vocabulary_block_lists_exactly_the_four_tags(self):
        # The tags are the prompt's only `*` bullets, each introduced as
        # `* \`tag\` — meaning`. Extracting them proves a fifth entry cannot
        # be added quietly.
        bullet = re.compile(r"(?m)^\s*\*\s+`([a-z][a-z-]*)`")
        for sid, prompt in critic_prompts():
            with self.subTest(step=sid):
                self.assertEqual(tuple(bullet.findall(prompt)), EXPECTED_TAGS)

    def test_no_prompt_invents_a_cause_token_outside_the_vocabulary(self):
        allowed = set(EXPECTED_TAGS) | {"<tag>"}
        token = re.compile(r"cause:([<a-z][a-z->]*)")
        for sid, prompt in critic_prompts():
            with self.subTest(step=sid):
                self.assertTrue(set(token.findall(prompt)) <= allowed,
                                f"{sid}: {set(token.findall(prompt)) - allowed}")

    def test_each_prompt_demands_exactly_one_tag_on_a_rejection(self):
        want = re.compile(r"(?i)exactly one")
        for sid, prompt in critic_prompts():
            with self.subTest(step=sid):
                self.assertRegex(norm(prompt), want)

    def test_each_prompt_still_specifies_the_untouched_approve_line(self):
        for sid, prompt in critic_prompts():
            with self.subTest(step=sid):
                self.assertIn("`VERDICT: APPROVE`", prompt)

    def test_each_prompt_forbids_a_cause_on_an_approve(self):
        # The card is explicit that APPROVE lines are unchanged. Without this
        # the critic could reasonably read "always name a cause".
        want = re.compile(r"(?i)never .{0,40}(a cause|cause) on an APPROVE")
        for sid, prompt in critic_prompts():
            with self.subTest(step=sid):
                self.assertRegex(norm(prompt), want)

    def test_the_prompts_stay_byte_identical_to_each_other_on_this_block(self):
        # The two blocks are duplicated by necessity, so the cause section
        # must not drift between them.
        blocks = []
        for _, prompt in critic_prompts():
            start = prompt.index(EXPECTED_PREFIX)
            blocks.append(norm(prompt[start:start + 900]))
        self.assertEqual(blocks[0], blocks[1])


# ── 3. every header-line parser reads all four fixtures ────────────────────

class EveryParserReadsEveryFixtureTest(unittest.TestCase):
    """The compatibility criterion, one assertion per parser per fixture."""

    def test_merge_gate_resolves_the_verdict_word(self):
        for f in FIXTURES + (CARRIED_WITH_CAUSE,):
            with self.subTest(fixture=f.name):
                self.assertEqual(
                    merge_gate.verdict_token(f.line, merge_gate.CRITIC_MARKER),
                    f.word,
                )

    def test_merge_gate_resolves_the_bound_sha(self):
        for f in FIXTURES + (CARRIED_WITH_CAUSE,):
            with self.subTest(fixture=f.name):
                self.assertEqual(merge_gate.verdict_sha(f.line), f.sha)

    def test_the_content_id_survives_the_new_token(self):
        for f in FIXTURES + (CARRIED_WITH_CAUSE,):
            with self.subTest(fixture=f.name):
                self.assertEqual(verdict_content_id(f.line), CONTENT)

    def test_the_cause_reads_back_only_where_one_was_written(self):
        for f in FIXTURES + (CARRIED_WITH_CAUSE,):
            with self.subTest(fixture=f.name):
                self.assertEqual(vc.verdict_cause(f.line), f.cause)

    def test_the_check_publisher_resolves_the_verdict_word(self):
        for f in FIXTURES + (CARRIED_WITH_CAUSE,):
            with self.subTest(fixture=f.name):
                conclusion, title, _ = publish_review_check.decide(True, f.body)
                self.assertIn(f.word, title)
                self.assertEqual(
                    conclusion, "success" if f.word == "APPROVE" else "failure"
                )

    def test_the_critic_result_gate_accepts_a_complete_verdict(self):
        # The max-turns path (DRE-2422) is the one that re-parses the verdict
        # WORD out of the line: a review cut off by the ceiling keeps its
        # verdict only if that verdict declares a legal decision. A cause tag
        # must not make it unreadable — that would throw away a completed
        # review and buy a second identical failure.
        crash = {"is_error": True, "subtype": "error_max_turns", "num_turns": 41}
        for f in FIXTURES + (CARRIED_WITH_CAUSE,):
            with self.subTest(fixture=f.name):
                with tempfile.NamedTemporaryFile("w", suffix=".md") as fh:
                    fh.write(f.body)
                    fh.flush()
                    self.assertTrue(
                        check_critic_result.verdict_is_real(crash, fh.name),
                        f"{f.name}: a complete verdict was discarded",
                    )
                    self.assertTrue(
                        check_critic_result.verdict_is_real(
                            {"is_error": False}, fh.name)
                    )

    def test_an_illegal_verdict_word_is_still_rejected_on_the_crash_path(self):
        # The widening above must not become "anything after VERDICT: counts".
        crash = {"is_error": True, "subtype": "error_max_turns", "num_turns": 41}
        with tempfile.NamedTemporaryFile("w", suffix=".md") as fh:
            fh.write(verdict_file("MAYBE", "defect"))
            fh.flush()
            self.assertFalse(check_critic_result.verdict_is_real(crash, fh.name))

    def test_a_hostile_first_line_cannot_displace_the_content_id(self):
        # The critic writes the middle of the header line having just read a
        # pull request anyone can author. A cause token carrying a forged
        # content id sits BEFORE the workflow's own, which is appended last
        # and read end-anchored.
        forged = "d" * 64
        line = (
            f"🔎 QA Critic — VERDICT: REQUEST_CHANGES cause:defect "
            f"content:{forged} @{HEAD} content:{CONTENT}"
        )
        self.assertEqual(verdict_content_id(line), CONTENT)


# ── 4. the whole gate, end to end, on every fixture ────────────────────────

GREEN_CI = [{"name": "ci", "status": "completed", "conclusion": "success",
             "check_suite": {"id": 1}}]


def gate(line, **kw):
    kw.setdefault("head_sha", HEAD)
    kw.setdefault("qa_login", QA_LOGIN)
    kw.setdefault("check_runs", list(GREEN_CI))
    kw.setdefault("comments", [{"user": {"login": QA_LOGIN}, "body": line}])
    kw.setdefault("compare_status", "identical")
    kw.setdefault("pr_commits", [{"sha": STALE}, {"sha": HEAD}])
    kw.setdefault("head_content_id", CONTENT)
    return merge_gate.decide(**kw)


class TheGateDecidesTheSameWithAndWithoutACauseTest(unittest.TestCase):

    def test_an_old_rejection_holds(self):
        d = gate(header("REQUEST_CHANGES"))
        self.assertEqual(d.action, "hold")
        self.assertIn("not APPROVE", d.reason)

    def test_a_caused_rejection_holds_identically(self):
        d = gate(header("REQUEST_CHANGES", cause="unmet-criteria"))
        self.assertEqual(d.action, "hold")
        self.assertIn("not APPROVE", d.reason)

    def test_an_approve_merges(self):
        self.assertEqual(gate(header("APPROVE")).action, "merge")

    def test_a_carried_approve_still_merges(self):
        d = gate(header("APPROVE", sha=STALE))
        self.assertEqual(d.action, "merge", d.reason)
        self.assertEqual(d.content_id, CONTENT)
        self.assertTrue(d.carried)

    def test_a_carried_rejection_with_a_cause_holds_rather_than_waits(self):
        # `wait` here would mean the gate read the carried line as stale and
        # expects a fresh review — the DRE-2340 livelock, re-opened by a
        # token it could not parse.
        d = gate(header("REQUEST_CHANGES", cause="scope", sha=STALE))
        self.assertEqual(d.action, "hold", d.reason)
        self.assertIn("not APPROVE", d.reason)


class TheReviewSkipReadsTheCarryTest(unittest.TestCase):
    """should_review_pr shares the gate's carry predicate — the skip must stay
    a strict subset of it, cause tag or no cause tag."""

    def carried(self, line):
        return should_review_pr.carried_approve(
            [{"user": {"login": QA_LOGIN}, "body": line}],
            QA_LOGIN,
            CONTENT,
            frozenset({STALE, HEAD}),
        )

    def test_a_carried_approve_is_recognised(self):
        self.assertEqual(self.carried(header("APPROVE", sha=STALE)), STALE)

    def test_a_carried_rejection_with_a_cause_is_never_a_carried_approve(self):
        self.assertIsNone(
            self.carried(header("REQUEST_CHANGES", cause="defect", sha=STALE))
        )


# ── 5. the shell consumers that grep the header line ───────────────────────

class ShellConsumersStillMatchTest(unittest.TestCase):

    def test_the_fix_dispatch_glob_matches_a_caused_rejection(self):
        # agent-fix.yml routes fix-vs-conflict on a shell `case` glob over the
        # verdict body. A pattern not wildcard-terminated would stop matching
        # the moment a cause token landed after the verdict word — and every
        # rejected PR would silently route to conflict mode.
        src = "\n".join(run_blocks("agent-fix.yml"))
        pats = re.findall(r'(\S*"VERDICT: REQUEST_CHANGES"\S*)\)', src)
        self.assertTrue(pats, "agent-fix.yml no longer globs the verdict body")
        for pat in pats:
            glob = pat.replace('"', "")
            with self.subTest(pattern=pat):
                self.assertTrue(
                    fnmatch.fnmatchcase(header("REQUEST_CHANGES"), glob))
                self.assertTrue(fnmatch.fnmatchcase(
                    header("REQUEST_CHANGES", cause="defect"), glob))
                self.assertFalse(
                    fnmatch.fnmatchcase(header("APPROVE"), glob))

    def test_the_approve_substring_consumers_are_unaffected(self):
        # qa-review.yml's sync_review_state guard and reconcile's
        # approved-but-red sweep both test the literal substring
        # "VERDICT: APPROVE". A cause never appears on an APPROVE line, and it
        # must not make a rejection look like one.
        needle = "VERDICT: APPROVE"
        self.assertIn(needle, header("APPROVE"))
        self.assertNotIn(needle, header("REQUEST_CHANGES", cause="defect"))
        self.assertIn(needle, "\n".join(run_blocks("qa-review.yml")))


if __name__ == "__main__":
    unittest.main()
