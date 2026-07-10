"""Decision-table PARITY suite for scripts/merge_gate.py (DRE-1992).

The merge decision — the fleet's single highest-privilege call — moved from
inline shell in merge-gate.yml into scripts/merge_gate.py. This suite proves
the extraction is faithful: every row below is executed against BOTH
implementations and the outcomes are compared case-for-case.

  • NEW: merge_gate.decide(...) — the extracted Python decision.
  • OLD: the pre-extraction shell exactly as it stood on main at ba4305d
    (post-#57 authorship / post-#60 SHA binding / post-#61 SKIP arm),
    committed VERBATIM at tests/fixtures/merge-gate.ba4305d.yml. The two
    jq verdict-selection filters, the check-runs jq, and the two DRE-1990
    marker blocks are extracted from that fixture at test time and executed
    with real jq/bash — the same live-extraction harness that
    tests/test_verdict_sha_binding.py ran against the live workflow before
    this extraction. (The fixture is byte-identical to
    `git show ba4305d:.github/workflows/merge-gate.yml`.)

Rows where old and new AGREE prove parity. Rows marked delta=... are the
TWO sanctioned behavior changes — (a) STRUCTURED/ANCHORED verdict parsing
(scope note 2026-07-09: a comment merely QUOTING a verdict marker must not
count as a verdict) and (b) VERIFIED-ORIGIN review-run exclusion (DRE-1994:
the old `endswith("review")` name test let a PR-authored failing job named
`sneaky-review` vanish from the all-green rule; exclusion now requires the
check run to sit in a check suite produced by a known review WORKFLOW FILE,
per GitHub's own workflow-runs record). Those rows assert BOTH the old and
the new outcome explicitly, so the delta is documented in executable form,
not prose.

Every row cites its artifact: the fixture line range holding the old shell
that produced the expected outcome, and (where one exists) the pre-existing
test that pinned it. No row is extrapolated.

Fixture line map (tests/fixtures/merge-gate.ba4305d.yml):
  L117-129  condition 1 — CI check runs: `endswith("review")` exclusion,
            total==0 → wait, any not-green → wait
  L131-143  condition 2 selection — critic authorship jq (#57, DRE-1987)
  L144-173  condition 2 binding — critic SHA binding block (DRE-1990)
  L175-189  condition 3 selection — verifier authorship jq (#57)
  L190-223  condition 3 binding — verifier SHA binding block + SKIP arm
            (DRE-1990 asymmetry, DRE-1991 SKIP)
  L225-230  merge tail (stays in the workflow; not part of the decision)
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "merge-gate.ba4305d.yml"

sys.path.insert(0, str(ROOT / "scripts"))

import merge_gate  # noqa: E402  (RED until DRE-1992 lands the script)

# ── shared vocabulary ────────────────────────────────────────────────────
HEAD = "aa11" * 10  # the PR's current headRefOid
STALE = "bb22" * 10  # an older commit a verdict may still name
QA_LOGIN = "agent-bureau-qa-bot[bot]"
WORKER_LOGIN = "agent-bureau-bot[bot]"  # authors PRs — must never count

CRITIC_NEUTRAL = (
    "🔎 QA Critic could not run (infra error) — re-review needed, "
    "this is NOT a code rejection."
)
VERIFIER_NEUTRAL = (
    "🧪 QA Verifier could not run (infra error) — re-verify needed, "
    "this is NOT a feature rejection."
)


def critic(verdict, sha=None, tail=""):
    base = f"🔎 QA Critic — VERDICT: {verdict}{tail}"
    return f"{base} @{sha}" if sha else base


def verifier(verdict, sha=None, tail=""):
    base = f"🧪 QA Verifier — VERDICT: {verdict}{tail}"
    return f"{base} @{sha}" if sha else base


def comment(login, body):
    """GitHub REST issue-comment shape; login=None models a deleted user."""
    if login is None:
        user = None
    else:
        user = {"login": login, "type": "Bot" if login.endswith("[bot]") else "User"}
    return {"user": user, "body": body}


# Check-suite ids as GitHub assigns them: ONE suite per workflow RUN, so a
# check run is tied to the workflow FILE that produced it via the suite id
# (verified live on agent-bureau PR #1899, head 62b73729: check run
# "call / review" id 86251660121 sits in suite 78612234136, which the
# workflow-runs listing maps to path .github/workflows/qa-review.yml; the
# CI run sits in its own suite 78612233475; every run's app.id is 15368 —
# github-actions — so the app can NOT discriminate, the suite→path can).
CI_SUITE = 78612233475  # the repo's CI workflow run
REVIEW_SUITE = 78612234136  # the qa-review.yml (critic) workflow run
EVIL_SUITE = 78612239999  # a PR-authored workflow's own run — never review
REVIEW_SUITES = frozenset({REVIEW_SUITE})  # the verified-origin record


def run(name="unit", status="completed", conclusion="success", suite=CI_SUITE):
    """A GitHub check-run as the REST check-runs API returns it. Every
    Actions-created run carries its workflow run's check_suite id."""
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "app": {"id": 15368, "slug": "github-actions"},
        "check_suite": {"id": suite},
    }


GREEN_CI = [run("unit"), run("Workflow YAML parses")]
CRITIC_OK = comment(QA_LOGIN, critic("APPROVE", HEAD))


# ── the decision table ───────────────────────────────────────────────────
@dataclass
class Row:
    id: str
    comments: list
    expect: str  # merge | wait | hold — the NEW (and, unless delta, OLD) outcome
    reason: str  # substring the NEW reason line must carry
    cite: str  # fixture lines + pre-existing test that pinned this row
    checks: list = field(default_factory=lambda: list(GREEN_CI))
    review_suites: frozenset = REVIEW_SUITES  # verified review-run origins
    old_expect: str = None  # only set on delta rows
    delta: str = None  # the sanctioned behavior change this row documents

    def __post_init__(self):
        if self.old_expect is None:
            self.old_expect = self.expect


ROWS = [
    # ── condition 1: CI check runs (fixture L117-129) ────────────────────
    Row("ci_no_runs", [CRITIC_OK], "wait", "no checks reported yet",
        "fixture L124-128 (TOTAL=0 → wait)", checks=[]),
    Row("ci_pending_run", [CRITIC_OK], "wait", "not green",
        "fixture L125,129 (status != completed → not green)",
        checks=[run("unit"), run("deploy", status="in_progress", conclusion=None)]),
    Row("ci_red_run", [CRITIC_OK], "wait", "not green",
        "fixture L125,129 (conclusion failure → not green)",
        checks=[run("unit"), run("lint", conclusion="failure")]),
    Row("ci_neutral_and_skipped_are_green", [CRITIC_OK], "merge",
        "critic APPROVE",
        "fixture L125 (conclusion IN success,skipped,neutral)",
        checks=[run("a"), run("b", conclusion="skipped"), run("c", conclusion="neutral")]),
    Row("ci_verified_review_run_crashed_still_excluded", [CRITIC_OK], "merge",
        "critic APPROVE",
        "fixture L121-125 rationale preserved under DRE-1994 (a review run "
        "killed by an API blip must not deadlock the merge — the verdict "
        "COMMENT is the review's source of truth); exclusion now by verified "
        "origin: the run sits in the qa-review workflow's suite",
        checks=[run("unit"),
                run("call / review", conclusion="failure", suite=REVIEW_SUITE)]),
    Row("ci_verified_review_pending_no_deadlock", [CRITIC_OK], "merge",
        "critic APPROVE",
        "DRE-1994 requirement: the critic posts its verdict BEFORE its own "
        "job completes, so when the issue_comment leg wakes the gate the "
        "genuine review run is still in_progress — verified origin keeps it "
        "excluded (old fixture L125 excluded it by name)",
        checks=[run("unit"),
                run("call / review", status="in_progress", conclusion=None,
                    suite=REVIEW_SUITE)]),
    Row("ci_capitalised_Review_is_NOT_excluded", [CRITIC_OK], "wait", "not green",
        'OLD: fixture L125 jq endswith is case-sensitive ("QA Review" does '
        'not end with "review"). NEW: same outcome by a different route — '
        "the run is not in a verified review suite, so it counts",
        checks=[run("unit"), run("QA Review", conclusion="failure", suite=EVIL_SUITE)]),
    Row("ci_only_verified_review_run_is_no_checks", [CRITIC_OK], "wait",
        "no checks reported yet",
        "fixture L124-128 (exclusion runs BEFORE the total — a PR whose only "
        "run is the review reports TOTAL=0) — pinned as-is under DRE-1994",
        checks=[run("call / review", suite=REVIEW_SUITE)]),

    # ── condition 1, DRE-1994: exclusion by VERIFIED ORIGIN, not name ────
    Row("ci_sneaky_review_failure_blocks", [CRITIC_OK], "wait", "not green",
        "DRE-1994 (child of DRE-1978): check names come from PR-authored "
        "workflow files, so the old name test was attacker-nameable — a "
        "failing job named 'sneaky-review' was invisible to the all-green "
        "rule and MERGED red code. Its run has its own check suite (GitHub "
        "gives every workflow run one), never the review workflow's → it "
        "counts and blocks.",
        checks=[run("unit"), run("sneaky-review", conclusion="failure",
                                 suite=EVIL_SUITE)],
        old_expect="merge",
        delta="verified-origin exclusion: a *review NAME no longer exempts "
              "a check from the all-green rule"),
    Row("ci_sneaky_review_pending_blocks", [CRITIC_OK], "wait", "not green",
        "DRE-1994: the pending flavor — an attacker-named run cannot park "
        "itself outside the all-green rule while incomplete either",
        checks=[run("unit"), run("sneaky-review", status="in_progress",
                                 conclusion=None, suite=EVIL_SUITE)],
        old_expect="merge",
        delta="verified-origin exclusion: a *review NAME no longer exempts "
              "a check from the all-green rule"),
    Row("ci_unverified_green_review_named_run_counts_as_ci", [CRITIC_OK],
        "merge", "critic APPROVE",
        "DRE-1994: a review-NAMED run outside the verified review suite is "
        "just a check like any other — green here, so both old (excluded by "
        "name) and new (counted, green) merge; the routes differ, the "
        "outcome agrees",
        checks=[run("unit"), run("review", suite=EVIL_SUITE)]),
    Row("ci_origin_beats_name_nonreview_job_in_review_suite_excluded",
        [CRITIC_OK], "merge", "critic APPROVE",
        "DRE-1994: origin REPLACES the name test entirely — any job of the "
        "pipeline-owned review workflow (here one not named *review) shares "
        "the crash-must-not-deadlock rationale and is excluded by its suite. "
        "OLD counted it by name → wait.",
        checks=[run("unit"), run("call / prepare", conclusion="failure",
                                 suite=REVIEW_SUITE)],
        old_expect="wait",
        delta="verified-origin exclusion: suite membership, not name, "
              "decides — review-workflow jobs are excluded whatever their name"),
    Row("ci_no_origin_record_fails_closed", [CRITIC_OK], "wait", "not green",
        "DRE-1994 fail-closed direction: if the workflow-runs listing is "
        "unavailable (API blip → the workflow substitutes an empty record), "
        "NOTHING is excluded — the genuine review run counts and the gate "
        "waits; it never merges past an unverifiable check. OLD had no "
        "origin record at all and excluded by name → merge.",
        checks=[run("unit"), run("call / review", status="in_progress",
                                 conclusion=None, suite=REVIEW_SUITE)],
        review_suites=frozenset(),
        old_expect="merge",
        delta="verified-origin exclusion: no origin record → no exclusion "
              "(wait), never fail open"),

    # ── condition 2: critic verdict (fixture L131-173) ───────────────────
    Row("critic_no_comments", [], "wait", "no critic verdict yet",
        "fixture L157-159; test_verdict_sha_binding CriticShaBindingTest.test_07"),
    Row("critic_approve_at_head", [CRITIC_OK], "merge", "critic APPROVE",
        "fixture L144-173 fall-through; test_verdict_sha_binding test_01; "
        "test_merge_gate_authorship test_01 (PR #57/#60)"),
    Row("critic_approve_stale_sha",
        [comment(QA_LOGIN, critic("APPROVE", STALE))], "wait", "stale",
        "fixture L165-168; test_verdict_sha_binding test_02 (PR #60 — THE "
        "hole DRE-1990 closed: code pushed after a genuine APPROVE)"),
    Row("critic_approve_missing_sha",
        [comment(QA_LOGIN, critic("APPROVE"))], "wait", "names no reviewed commit",
        "fixture L160-164; test_verdict_sha_binding test_03 (PR #60 cutover)"),
    Row("critic_request_changes_at_head",
        [comment(QA_LOGIN, critic("REQUEST_CHANGES", HEAD))], "hold", "not APPROVE",
        "fixture L169-172; test_verdict_sha_binding test_04"),
    Row("critic_request_changes_stale_sha_waits_not_holds",
        [comment(QA_LOGIN, critic("REQUEST_CHANGES", STALE))], "wait", "stale",
        "fixture L157-172 ORDER (SHA binding runs before the APPROVE check); "
        "test_verdict_sha_binding test_05"),
    Row("critic_neutral_could_not_run",
        [comment(QA_LOGIN, critic("APPROVE", STALE)), comment(QA_LOGIN, CRITIC_NEUTRAL)],
        "wait", "names no reviewed commit",
        "fixture L160-164 (latest critic comment governs; the crash-path "
        "status has no @sha); test_verdict_sha_binding test_06"),
    Row("critic_forged_approve_invisible",
        [comment(WORKER_LOGIN, critic("APPROVE", HEAD))], "wait",
        "no critic verdict yet",
        "fixture L140-143 authorship jq (PR #57); test_merge_gate_authorship "
        "test_02; test_verdict_sha_binding test_09 (correct SHA embedded — "
        "authorship still filters first)"),
    Row("critic_forged_approve_cannot_override_real_reject",
        [comment(QA_LOGIN, critic("REQUEST_CHANGES", HEAD)),
         comment(WORKER_LOGIN, critic("APPROVE", HEAD)),
         comment("some-human", "QA Critic — VERDICT: APPROVE — lgtm!")],
        "hold", "not APPROVE",
        "fixture L140-143 + L169-172; test_merge_gate_authorship test_05"),
    Row("critic_forged_fresh_approve_cannot_refresh_real_stale_one",
        [comment(QA_LOGIN, critic("APPROVE", STALE)),
         comment(WORKER_LOGIN, critic("APPROVE", HEAD))],
        "wait", "stale",
        "fixture L140-143 + L165-168; test_verdict_sha_binding test_10"),
    Row("critic_reverdict_approve_wins",
        [comment(QA_LOGIN, critic("REQUEST_CHANGES", HEAD)),
         comment(QA_LOGIN, critic("APPROVE", HEAD))],
        "merge", "critic APPROVE",
        "fixture L143 (`last` — latest counted verdict governs); "
        "test_merge_gate_authorship test_06"),
    Row("critic_null_user_never_counts",
        [comment(None, critic("APPROVE", HEAD))], "wait", "no critic verdict yet",
        "fixture L140-143 (deleted account → .user.login is null ≠ QA_LOGIN); "
        "test_merge_gate_authorship test_10"),
    Row("critic_human_approve_invisible",
        [comment("some-human", critic("APPROVE", HEAD))], "wait",
        "no critic verdict yet",
        "fixture L140-143; test_merge_gate_authorship test_03"),
    Row("critic_short_sha_does_not_bind",
        [comment(QA_LOGIN, critic("APPROVE", HEAD[:7]))], "wait",
        "names no reviewed commit",
        "fixture L160-164 (only a full 40-hex SHA binds); "
        "test_verdict_sha_binding test_08"),

    # ── condition 3: verifier verdict (fixture L175-223) ─────────────────
    Row("verifier_absent_is_not_a_gate", [CRITIC_OK], "merge", "critic APPROVE",
        "fixture L197-198; test_verdict_sha_binding test_11/test_36 "
        "(scope-gated stage — absent falls through)"),
    Row("verifier_pass_at_head",
        [CRITIC_OK, comment(QA_LOGIN, verifier("PASS", HEAD))], "merge",
        "critic APPROVE",
        "fixture L209-210; test_verdict_sha_binding test_12"),
    Row("verifier_pass_stale_sha_holds",
        [CRITIC_OK, comment(QA_LOGIN, verifier("PASS", STALE))], "hold", "stale",
        "fixture L205-208 (PRESENT-but-stale must HOLD — the DRE-1990 "
        "asymmetry: treating it as absent would fail OPEN); "
        "test_verdict_sha_binding test_13"),
    Row("verifier_pass_missing_sha_holds",
        [CRITIC_OK, comment(QA_LOGIN, verifier("PASS"))], "hold",
        "names no verified commit",
        "fixture L201-204; test_verdict_sha_binding test_14"),
    Row("verifier_fail_at_head_holds",
        [CRITIC_OK, comment(QA_LOGIN, verifier("FAIL", HEAD))], "hold", "not PASS",
        "fixture L220; test_verdict_sha_binding test_15"),
    Row("verifier_neutral_present_holds",
        [CRITIC_OK, comment(QA_LOGIN, VERIFIER_NEUTRAL)], "hold",
        "names no verified commit",
        "fixture L201-204; test_verdict_sha_binding test_16"),
    Row("verifier_skip_at_head_is_advisory",
        [CRITIC_OK, comment(QA_LOGIN, verifier("SKIP", HEAD))], "merge",
        "critic APPROVE",
        "fixture L219 (PR #61, DRE-1991 — the brief promises a SKIP never "
        "blocks); test_verdict_sha_binding test_33"),
    Row("verifier_skip_stale_sha_holds",
        [CRITIC_OK, comment(QA_LOGIN, verifier("SKIP", STALE))], "hold", "stale",
        "fixture L205-208 (SHA checks run before the SKIP arm — L216-218); "
        "test_verdict_sha_binding test_34"),
    Row("verifier_skip_missing_sha_holds",
        [CRITIC_OK, comment(QA_LOGIN, verifier("SKIP"))], "hold",
        "names no verified commit",
        "fixture L201-204; test_verdict_sha_binding test_35"),
    Row("verifier_skip_with_reason_prose_still_advisory",
        [CRITIC_OK, comment(QA_LOGIN, verifier(
            "SKIP", HEAD, tail=" — single-system doc change, nothing to run"))],
        "merge", "critic APPROVE",
        "fixture L219 (glob match tolerates trailing prose before @sha); "
        "test_verdict_sha_binding test_37"),
    Row("verifier_forged_fail_is_invisible",
        [CRITIC_OK, comment(WORKER_LOGIN, verifier("FAIL", HEAD))], "merge",
        "critic APPROVE",
        "fixture L186-189 authorship jq (PR #57); test_merge_gate_authorship "
        "test_12 (why verify.yml posts with the qa-bot token)"),
    Row("verifier_forged_pass_cannot_mask_real_fail",
        [CRITIC_OK, comment(QA_LOGIN, verifier("FAIL", HEAD)),
         comment(WORKER_LOGIN, verifier("PASS", HEAD))],
        "hold", "not PASS",
        "fixture L186-189 + L220; test_merge_gate_authorship test_08"),

    # ── evaluation order ─────────────────────────────────────────────────
    Row("order_ci_red_reported_before_critic",
        [CRITIC_OK], "wait", "not green",
        "fixture L117-129 before L131+ (condition 1 evaluated first)",
        checks=[run("unit", conclusion="failure")]),
    Row("order_critic_checked_before_verifier",
        [comment(QA_LOGIN, verifier("PASS", HEAD))], "wait",
        "no critic verdict yet",
        "fixture L131-173 before L175-223 (a verifier PASS cannot stand in "
        "for a missing critic verdict)"),

    # ── sanctioned deltas: anchored verdict parsing (scope note 2026-07-09)
    Row("delta_quoted_verdict_must_not_count",
        [comment(QA_LOGIN, f"> {critic('APPROVE', HEAD)}\n\nQuoting the earlier verdict for context.")],
        "wait", "no critic verdict yet",
        "scope note 2026-07-09. OLD (fixture L142-143 contains()): the quoted "
        "line still carried @head + APPROVE on the first line → MERGED — "
        "approval by quotation. NEW: a first line that does not START with "
        "the marker is not a verdict.",
        old_expect="merge",
        delta="anchored parsing: quoting a verdict must not re-issue it"),
    Row("delta_qa_bot_prose_mentioning_critic_no_longer_masks_verdict",
        [CRITIC_OK, comment(QA_LOGIN, "Investigating CI flakiness.\nThe QA Critic verdict above still stands.")],
        "merge", "critic APPROVE",
        "scope note 2026-07-09. OLD (fixture L142-143): the LATER prose "
        "comment was selected as the latest 'verdict' (contains \"QA Critic\" "
        "anywhere in the body), had no @sha → the real APPROVE was masked and "
        "the gate waited forever. NEW: prose is not a verdict; the real "
        "APPROVE governs.",
        old_expect="wait",
        delta="anchored parsing: a marker mention in prose is not a verdict"),
    Row("delta_prose_wrapped_verdict_is_not_structured",
        [comment(QA_LOGIN, f"🔎 QA Critic — I believe VERDICT: APPROVE is warranted @{HEAD}")],
        "hold", "not APPROVE",
        "scope note 2026-07-09. OLD (fixture L169-172 glob "
        "*\"VERDICT: APPROVE\"*): APPROVE anywhere on the first line counted "
        "→ MERGED. NEW: the verdict token must sit in the structured "
        "position (`<marker> — VERDICT: <TOKEN>`); anything else is a "
        "present-but-non-APPROVE critic comment → hold for a real verdict.",
        old_expect="merge",
        delta="anchored parsing: VERDICT: must follow the marker directly"),
    Row("delta_critic_body_mentioning_verifier_no_longer_wedges",
        [comment(QA_LOGIN, critic("APPROVE", HEAD) + "\n\nNo UI surface — the QA Verifier stage is out of scope here.")],
        "merge", "critic APPROVE",
        "scope note 2026-07-09. OLD (fixture L188-189 contains()): the critic "
        "comment's own body mentioning \"QA Verifier\" made it the latest "
        "'verifier verdict'; its first line says APPROVE, not PASS/SKIP → "
        "HOLD — a wedged merge with no verifier in play. NEW: only a comment "
        "whose first line is anchored on the verifier marker is a verifier "
        "verdict.",
        old_expect="hold",
        delta="anchored parsing: cross-marker mentions in bodies do not count"),
]


# ── OLD implementation: the ba4305d shell, extracted and executed ────────
CRITIC_BEGIN = "# ── DRE-1990 critic-verdict SHA binding"
CRITIC_END = "# ── DRE-1990 end critic binding"
VERIFIER_BEGIN = "# ── DRE-1990 verifier-verdict SHA binding"
VERIFIER_END = "# ── DRE-1990 end verifier binding"


def extract_jq(marker):
    text = FIXTURE.read_text()
    exprs = [e for e in re.findall(r"--jq '([^']*)'", text) if marker in e]
    if len(exprs) != 1:
        raise AssertionError(
            f"expected exactly one --jq expression containing {marker!r} "
            f"in the ba4305d fixture, found {len(exprs)}"
        )
    return exprs[0]


def extract_block(begin, end):
    text = FIXTURE.read_text()
    m = re.search(
        re.escape(begin) + r"[^\n]*\n(.*?)^\s*" + re.escape(end), text, re.S | re.M
    )
    if not m:
        raise AssertionError(f"marker block {begin!r} not found in the fixture")
    lines = m.group(1).splitlines()
    pad = min(len(ln) - len(ln.lstrip()) for ln in lines if ln.strip())
    return "\n".join(ln[pad:] for ln in lines)


def jq(expr, data, env=None):
    proc = subprocess.run(
        ["jq", expr],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    if proc.returncode != 0:
        raise AssertionError(f"jq failed: {proc.stderr}")
    return json.loads(proc.stdout)


def run_block(block, variables):
    script = "set -euo pipefail\n"
    for name, value in variables.items():
        script += f"{name}={shlex.quote(value)}\n"
    script += block + "\necho GATE_FALLTHROUGH\n"
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(f"shell block errored: {proc.stderr}")
    return proc.stdout


def old_shell_decide(head, comments, checks):
    """Run the ba4305d decision end-to-end: check-runs jq + the two TOTAL /
    NOT_GREEN rules (fixture L124-129), then the critic selection jq +
    binding block (L140-173), then the verifier pair (L186-223). Outcome
    mapping mirrors the shell's own messages: '…wait' → wait,
    '…holding' → hold, full fall-through → merge."""
    ci = jq(extract_jq("check_runs"), {"check_runs": checks})
    if ci["total"] == 0:
        return "wait"  # L128: no checks reported yet — wait
    if ci["not_green"] != 0:
        return "wait"  # L129: N of M check runs not green — wait

    env = {"QA_LOGIN": QA_LOGIN}
    body = jq(extract_jq("QA Critic"), comments, env)
    verdict = (body or "").splitlines()[0] if body else ""
    out = run_block(extract_block(CRITIC_BEGIN, CRITIC_END), {"VERDICT": verdict, "SHA": head})
    if "GATE_FALLTHROUGH" not in out:
        return "hold" if "holding" in out else "wait"

    vbody = jq(extract_jq("QA Verifier"), comments, env)
    vverdict = (vbody or "").splitlines()[0] if vbody else ""
    out = run_block(
        extract_block(VERIFIER_BEGIN, VERIFIER_END), {"VVERDICT": vverdict, "SHA": head}
    )
    if "GATE_FALLTHROUGH" not in out:
        return "hold" if "holding" in out else "wait"
    return "merge"


# ── the tests ────────────────────────────────────────────────────────────
class FixtureIntegrityTest(unittest.TestCase):
    """The fixture must still carry every artifact the parity driver
    extracts — a truncated or re-generated fixture fails loudly here, not
    as a confusing jq error inside a row."""

    def test_fixture_present_and_complete(self):
        self.assertTrue(FIXTURE.exists(), "ba4305d fixture missing")
        text = FIXTURE.read_text()
        for needle in (CRITIC_BEGIN, CRITIC_END, VERIFIER_BEGIN, VERIFIER_END,
                       'endswith("review")', "env.QA_LOGIN"):
            self.assertIn(needle, text)

    def test_tooling_present(self):
        self.assertIsNotNone(shutil.which("jq"), "jq required")
        self.assertIsNotNone(shutil.which("bash"), "bash required")


@pytest.mark.parametrize("row", ROWS, ids=[r.id for r in ROWS])
def test_new_decision(row):
    """The extracted Python produces the expected action AND reason."""
    decision = merge_gate.decide(
        head_sha=HEAD,
        qa_login=QA_LOGIN,
        check_runs=row.checks,
        comments=row.comments,
        review_suites=row.review_suites,
    )
    assert decision.action == row.expect, (
        f"[{row.id}] expected {row.expect}, got {decision.action} "
        f"({decision.reason}) — cite: {row.cite}"
    )
    assert row.reason in decision.reason, (
        f"[{row.id}] reason {decision.reason!r} lacks {row.reason!r}"
    )


@pytest.mark.parametrize("row", ROWS, ids=[r.id for r in ROWS])
def test_old_shell_parity(row):
    """The ba4305d shell, run verbatim on the same inputs, reaches the same
    decision — except on the sanctioned delta rows, where its OLD outcome is
    asserted explicitly so the delta stays documented and deliberate."""
    got = old_shell_decide(HEAD, row.comments, row.checks)
    assert got == row.old_expect, (
        f"[{row.id}] ba4305d shell decided {got}, expected {row.old_expect}"
        + (f" (delta: {row.delta})" if row.delta else "")
        + f" — cite: {row.cite}"
    )
    if row.delta is None:
        assert row.old_expect == row.expect, f"[{row.id}] table row inconsistent"


def test_every_delta_row_names_its_sanction():
    """A delta row without a rationale is an unexplained behavior change —
    refuse it."""
    for row in ROWS:
        if row.old_expect != row.expect:
            assert row.delta, f"[{row.id}] old≠new but no delta rationale"
            assert any(
                key in row.delta for key in ("anchored", "quot", "origin")
            ), f"[{row.id}] delta rationale names no sanctioned change"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
