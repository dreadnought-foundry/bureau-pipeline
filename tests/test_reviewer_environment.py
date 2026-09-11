"""RED-first (DRE-3428): the pipeline learns to say "the runner cannot run
Claude", once, in one place.

On 2026-09-08 (DRE-3416) all six kinds of Claude-running job in the fleet died
about thirteen seconds in with `Claude Code native binary not found`.
`medic_classify` called it a critic infra crash — but only because the neutral
marker happened to be in the same log — and the sweep's `reviewer-down` note
told the operator to check the critic's auth/token, which was the one thing
that was not wrong.

This file pins the vocabulary two siblings (the medic and the reconcile sweep)
will both read, so they cite one answer instead of inventing two:

  * the SIGNATURE TABLE — four positive signatures, each with a slug, a
    line-anchored pattern, a plain-English meaning and the check that confirms
    it. Line-anchored is the DRE-2488/2923 discipline: this card's own body
    quotes every one of those phrases, so a card body quoted into an agent log
    must not classify or a genuine failure is silently swallowed;
  * `detect()` — the first matching signature, with the credential shape read
    through `medic_retry.execution_from_log()` and
    `check_agent_result.has_service_outage_signature()` rather than
    re-derived;
  * the two BODIES — the medic's evidence note (a report, not an act) and the
    hold receipt (composed through `pipeline_act.receipt`), and the strings
    each of them may never contain, because every one of those strings is a
    live counter or a console marker somebody else reads;
  * the CLASS — `environment_crash`, checked BEFORE `critic_infra_crash`, with
    the DRE-1921 gates (`infra_crash=`) untouched.

Run: cd bureau-pipeline && python3 -m pytest tests/test_reviewer_environment.py -v
"""
from __future__ import annotations

import io
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "test")

import check_agent_result  # noqa: E402
import execution_result  # noqa: E402
import linear_ops  # noqa: E402
import medic_classify  # noqa: E402
import medic_retry  # noqa: E402
import pipeline_act  # noqa: E402
import review_rerun  # noqa: E402
import reviewer_environment as renv  # noqa: E402

MEDIC_WORKFLOW = ROOT / ".github" / "workflows" / "medic.yml"

SHA = "d34db33fcafe1234d34db33fcafe1234d34db33f"

# A GitHub Actions log line as `gh run view --log-failed` prints it:
# `job<TAB>step<TAB><ISO timestamp> <content>`.
PREFIX = "qa-review\tReview the pull request\t2026-09-08T18:03:12.1234567Z "


def _log(*content: str) -> str:
    return "".join(f"{PREFIX}{line}\n" for line in content)


# The DRE-3416 log, as the fleet's six job kinds actually died.
NATIVE_BINARY_LOG = _log(
    "Run anthropics/claude-code-action@v1",
    "Installing Claude Code...",
    "Error: Claude Code native binary not found",
    "##[error]Process completed with exit code 1.",
)

EXECUTABLE_LOG = _log(
    'Error: {"type":"executable_not_found","message":"no Claude executable"}',
)

AUTHENTICATION_LOG = _log(
    'API Error: {"type":"authentication_error","message":"invalid x-api-key"}',
)

# A run that reached Claude and was refused before its first turn. The numbers
# are the record's own, printed into the log under the gate's failure header —
# which is where `medic_retry.execution_from_log` reads them from.
CREDENTIAL_LOG = _log(
    f"agent result gate: {execution_result.FAILURE_HEADER}",
    "  subtype: error_during_execution",
    "  num_turns: 1",
    "  total_cost_usd: 0",
    "  duration_ms: 412",
    "  result: Claude AI usage limit reached",
)

# A 429 with no environment signature — the class that must still read exactly
# as it did before this card.
RATE_LIMIT_LOG = _log(
    "API rate limit exceeded for installation ID 12345",
)


# --------------------------------------------------------------------------- #
# 1. the signature table — the words ARE the contract                          #
# --------------------------------------------------------------------------- #


class TestTheSignatureTable:
    def test_the_four_slugs_in_the_contract_s_order(self):
        assert [s.slug for s in renv.SIGNATURES] == [
            "native-binary-missing",
            "executable-not-found",
            "credential-refused",
            "authentication-error",
        ]

    @pytest.mark.parametrize("signature", renv.SIGNATURES, ids=lambda s: s.slug)
    def test_every_signature_carries_its_four_fields(self, signature):
        assert len(signature) == 4, (
            "a slug, a pattern, a meaning and the check that confirms it"
        )
        assert signature.meaning.strip()
        assert "\n" not in signature.meaning, "the meaning is ONE line"
        assert signature.check.strip()
        assert "\n" not in signature.check, (
            "the check is printed as one `check=` line of the classifier's "
            "stdout, which $GITHUB_OUTPUT reads key-by-line"
        )
        assert not signature.meaning.endswith("."), (
            "the note and the receipt punctuate the meaning themselves"
        )

    def test_the_two_installation_signatures_send_the_reader_to_the_action_pin(self):
        for slug in ("native-binary-missing", "executable-not-found"):
            check = renv.by_slug(slug).check
            assert "claude-code-action@" in check
            assert "1817" in check, "anthropics/claude-code-action#1817"

    def test_the_two_credential_signatures_send_the_reader_to_cred_doctor(self):
        for slug in ("credential-refused", "authentication-error"):
            assert "cred-doctor" in renv.by_slug(slug).check

    def test_the_credential_shape_is_not_a_regex(self):
        """It is the execution RECORD, read through the two shipped readers —
        a log line saying `credential-refused` would be prose."""
        assert renv.by_slug("credential-refused").pattern is None
        for slug in ("native-binary-missing", "executable-not-found",
                     "authentication-error"):
            assert renv.by_slug(slug).pattern is not None

    def test_by_slug_refuses_a_slug_the_table_does_not_carry(self):
        with pytest.raises(KeyError):
            renv.by_slug("no-such-signature")


# --------------------------------------------------------------------------- #
# 2. detect() — all four, and nothing else                                     #
# --------------------------------------------------------------------------- #


class TestDetect:
    def test_native_binary_missing(self):
        found = renv.detect(NATIVE_BINARY_LOG)
        assert found is not None and found.slug == "native-binary-missing"

    def test_executable_not_found(self):
        found = renv.detect(EXECUTABLE_LOG)
        assert found is not None and found.slug == "executable-not-found"

    def test_authentication_error(self):
        found = renv.detect(AUTHENTICATION_LOG)
        assert found is not None and found.slug == "authentication-error"

    def test_authentication_error_needs_its_co_token_on_the_same_line(self):
        """The DRE-2488 shape: the token alone is a word, and prose carries
        words. `API Error` or `"type"` on the SAME line is what makes it a
        machine record."""
        assert renv.detect(_log("the run died of authentication_error")) is None

    def test_credential_refused_is_read_off_the_execution_record(self):
        """Built the way the contract says: through
        `medic_retry.execution_from_log()`, from a log carrying
        `execution_result.FAILURE_HEADER` — never re-derived here."""
        record = medic_retry.execution_from_log(CREDENTIAL_LOG)
        assert record is not None, "the fixture must carry a real failure block"
        assert record["is_error"] is True
        found = renv.detect(CREDENTIAL_LOG)
        assert found is not None and found.slug == "credential-refused"

    def test_the_credential_shape_is_the_shipped_outage_signature(self):
        """Not a second opinion about what a refused credential looks like:
        the record the log yields must satisfy
        `check_agent_result.has_service_outage_signature` itself."""
        record = renv.execution_record(CREDENTIAL_LOG)
        assert check_agent_result.has_service_outage_signature(record)

    def test_a_record_that_spent_turns_is_not_a_refused_credential(self):
        spent = _log(
            f"agent result gate: {execution_result.FAILURE_HEADER}",
            "  subtype: error_max_turns",
            "  num_turns: 151",
            "  total_cost_usd: 16.79",
            "  duration_ms: 1320000",
        )
        assert renv.detect(spent) is None

    def test_a_rate_limit_carries_no_environment_signature(self):
        assert renv.detect(RATE_LIMIT_LOG) is None

    def test_an_empty_log_detects_nothing(self):
        assert renv.detect("") is None
        assert renv.detect(None) is None

    def test_the_table_order_is_the_precedence(self):
        both = NATIVE_BINARY_LOG + EXECUTABLE_LOG
        assert renv.detect(both).slug == "native-binary-missing"


class TestAQuotedPhraseIsNotACrash:
    """DRE-2488/2923's discipline, and this card's own body is the adversary:
    it quotes every one of these phrases."""

    def test_a_diff_hunk_quoting_the_phrase_does_not_classify(self):
        hunk = _log(
            '+        assert "Claude Code native binary not found" in body',
            '-        assert "native binary not found" in body',
        )
        assert renv.detect(hunk) is None

    def test_this_card_s_own_body_does_not_classify(self):
        """The literal sentences from DRE-3428, backticked exactly as the card
        writes them. An agent-task run on this card that fails must not be
        read as an environment crash."""
        card = _log(
            "the fleet's six kinds of Claude-running job died about 13 seconds "
            "in with `Claude Code native binary not found`",
            "`executable-not-found` — a log line carrying `executable_not_found`",
            "a log line carrying `authentication_error` together with "
            '`API Error` or `"type"` on the same line',
        )
        assert renv.detect(card) is None

    def test_a_quoted_line_in_a_comment_thread_does_not_classify(self):
        assert renv.detect(_log("> Error: Claude Code native binary not found")) is None


# --------------------------------------------------------------------------- #
# 3. the evidence note — the phrase the fleet alarm counts                     #
# --------------------------------------------------------------------------- #


RUN_URL = "https://github.com/dreadnought-foundry/agent-bureau/actions/runs/1"


class TestTheEvidenceNote:
    def test_the_marker_is_the_phrase_the_medic_already_posts(self):
        assert renv.CRITIC_UNAVAILABLE_MARKER == (
            "\U0001f50c The code reviewer was temporarily unavailable"
        )
        assert renv.EVIDENCE_MARKER == "reviewer-environment-crash"

    def test_the_medic_workflow_still_carries_the_phrase_verbatim(self):
        """The seam DRE-3420's fleet-wide detector reads (DRE-3433): it counts
        every Linear comment STARTING with this phrase as one could-not-run
        outcome from that repository. If either side reworded it the alarm
        would go blind for exactly the crash class this epic names, so both
        sides are pinned to one string."""
        assert renv.CRITIC_UNAVAILABLE_MARKER in MEDIC_WORKFLOW.read_text("utf-8")

    def test_the_first_line_is_the_contract_s_first_line(self):
        signature = renv.by_slug("native-binary-missing")
        note = renv.evidence_note(signature, SHA, RUN_URL)
        assert note.splitlines()[0] == (
            f"{renv.CRITIC_UNAVAILABLE_MARKER} — {renv.EVIDENCE_MARKER} @{SHA}: "
            f"{signature.slug} — {signature.meaning}. The sweep retries this "
            "review once; a second identical crash holds it. "
            f"Check: {signature.check}."
        )

    def test_it_starts_with_the_marker_and_binds_the_full_head_sha(self):
        note = renv.evidence_note(renv.by_slug("credential-refused"), SHA, RUN_URL)
        assert note.startswith(renv.CRITIC_UNAVAILABLE_MARKER)
        assert f"{renv.EVIDENCE_MARKER} @{SHA}:" in note.splitlines()[0]

    def test_it_names_the_failed_run(self):
        note = renv.evidence_note(renv.by_slug("authentication-error"), SHA, RUN_URL)
        assert RUN_URL in note

    def test_a_note_with_no_run_url_still_composes(self):
        note = renv.evidence_note(renv.by_slug("authentication-error"), SHA, "")
        assert note.startswith(renv.CRITIC_UNAVAILABLE_MARKER)

    def test_evidence_for_head_returns_only_this_head_s_notes(self):
        other = "f00dface" * 5
        mine = renv.evidence_note(renv.by_slug("native-binary-missing"), SHA, RUN_URL)
        theirs = renv.evidence_note(
            renv.by_slug("native-binary-missing"), other, RUN_URL
        )
        bodies = ["⏳ 1/5 plan formed", theirs, mine, "a human said something"]
        assert renv.evidence_for_head(bodies, SHA) == [mine]

    def test_evidence_for_head_does_not_count_the_hold(self):
        hold = renv.hold_receipt(renv.by_slug("native-binary-missing"), SHA, 2)
        assert renv.evidence_for_head([hold], SHA) == []

    def test_an_unreadable_body_list_is_empty_not_an_error(self):
        assert renv.evidence_for_head([], SHA) == []
        assert renv.evidence_for_head(None, SHA) == []


# --------------------------------------------------------------------------- #
# 4. the hold receipt                                                          #
# --------------------------------------------------------------------------- #


class TestTheHoldReceipt:
    def test_the_act_and_the_tag_are_the_contract_s(self):
        assert renv.HOLD_ACT == "reviewer-environment-hold"
        assert renv.HOLD_TAG == "runner-environment-hold"

    def test_the_first_line_is_the_contract_s_first_line(self):
        signature = renv.by_slug("native-binary-missing")
        body = renv.hold_receipt(signature, SHA, 2)
        assert body.splitlines()[0] == (
            f"🛑 {renv.HOLD_TAG} @{SHA}: reviewer cannot run on this runner — "
            f"{signature.slug}: {signature.meaning} — twice on {SHA[:8]}; "
            f"holding, nothing re-dispatched. Check: {signature.check}."
        )

    def test_a_later_crash_reads_again_after_release(self):
        body = renv.hold_receipt(renv.by_slug("credential-refused"), SHA, 3)
        assert "— again after release on " in body.splitlines()[0]
        assert "twice" not in body

    def test_the_subject_is_the_workflow_name_when_it_is_not_the_critic(self):
        body = renv.hold_receipt(
            renv.by_slug("native-binary-missing"), SHA, 2, subject="Agent Task"
        )
        assert "Agent Task cannot run on this runner" in body.splitlines()[0]

    def test_the_release_rule_is_the_second_paragraph(self):
        body = renv.hold_receipt(renv.by_slug("native-binary-missing"), SHA, 2)
        assert "critic verdict posted in this repository after this hold" in body
        assert review_rerun.RERUN_REVIEW_ACT in body

    def test_the_rerun_act_string_is_not_restated_in_this_module(self):
        """`review_rerun` owns that string (DRE-3286) and the console and the
        relay mirror it. A second copy here is a second thing to reword."""
        source = (ROOT / "scripts" / "reviewer_environment.py").read_text("utf-8")
        assert review_rerun.RERUN_REVIEW_ACT not in source

    def test_is_release_act_delegates_to_review_rerun(self):
        assert renv.is_release_act(review_rerun.RERUN_REVIEW_ACT)
        assert renv.is_release_act(f"  {review_rerun.RERUN_REVIEW_ACT}  ")
        assert not renv.is_release_act(
            f"please {review_rerun.RERUN_REVIEW_ACT} when the runner is fixed"
        )
        assert not renv.is_release_act(None)

    def test_the_hold_never_carries_the_crash_phrase_the_fleet_alarm_counts(self):
        """A hold is mirrored to the card, and DRE-3420's detector counts
        CRASHES. A hold that opened with the crash phrase would be counted as
        one more outage every time the pipeline said it had stopped."""
        for count in (2, 3):
            body = renv.hold_receipt(renv.by_slug("native-binary-missing"), SHA, count)
            assert renv.CRITIC_UNAVAILABLE_MARKER not in body
            assert "The code reviewer was temporarily unavailable" not in body


# --------------------------------------------------------------------------- #
# 5. the strings neither body may carry                                        #
# --------------------------------------------------------------------------- #


# Live idempotency keys and per-sha budget counters. `tag in body` is how each
# one is suppressed and counted (pipeline_act's module docstring), so a body
# that merely MENTIONS one is read as that act's receipt.
LIVE_COUNTERS = ("runner-environment-hold", "reviewer-down",
                 "crashed-review-redispatch")


def _every_body() -> dict:
    out = {}
    for signature in renv.SIGNATURES:
        out[f"note/{signature.slug}"] = renv.evidence_note(signature, SHA, RUN_URL)
        for count in (2, 3):
            out[f"hold/{signature.slug}/{count}"] = renv.hold_receipt(
                signature, SHA, count
            )
    return out


class TestNeitherBodyCollidesWithALiveCounter:
    def test_the_console_hold_markers_are_in_neither_body(self):
        """`linear_ops.CONSOLE_HOLD_MARKERS` — what the console's
        `enrich.HOLD_MARKERS` reads as a FIX-BUDGET hold. Either sentence and
        the card renders as the wrong kind of stuck, on a surface nobody would
        think to check. Read off the shipped constant, never restated."""
        assert linear_ops.CONSOLE_HOLD_MARKERS == (
            "budget exhausted", "holding for a human"
        )
        for name, body in _every_body().items():
            for marker in linear_ops.CONSOLE_HOLD_MARKERS:
                assert marker not in body.lower(), f"{name} carries {marker!r}"

    def test_the_evidence_note_carries_no_live_counter(self):
        for name, body in _every_body().items():
            if not name.startswith("note/"):
                continue
            for counter in LIVE_COUNTERS:
                assert counter not in body, f"{name} carries {counter!r}"

    def test_the_hold_carries_only_its_own_key(self):
        for name, body in _every_body().items():
            if not name.startswith("hold/"):
                continue
            assert body.count(renv.HOLD_TAG) == 1, f"{name}: one key, once"
            for counter in LIVE_COUNTERS:
                if counter == renv.HOLD_TAG:
                    continue
                assert counter not in body, f"{name} carries {counter!r}"

    def test_no_body_forges_a_verdict_marker(self):
        """standards/untrusted-content.md: verdict-shaped text IS an approval
        credential, and both bodies are posted where the merge gate reads."""
        for name, body in _every_body().items():
            for marker in ("VERDICT:", "QA Critic", "QA Verifier"):
                assert marker not in body, f"{name} carries {marker!r}"


# --------------------------------------------------------------------------- #
# 6. post_hold — the pull request, then the card                               #
# --------------------------------------------------------------------------- #


class TestPostHold:
    def _recorders(self, mp, *, pr_rc: int = 0):
        posted: list = []

        def fake_run(argv, **_kwargs):
            posted.append(("pr", argv))
            return SimpleNamespace(
                returncode=pr_rc, stdout="", stderr="gh: boom" if pr_rc else "",
            )

        mp.setattr(renv.subprocess, "run", fake_run)
        mp.setattr(linear_ops, "cmd_comment",
                   lambda card, body, *f: posted.append(("card", card, body)))
        return posted

    def test_the_pull_request_first_then_the_card(self, monkeypatch):
        posted = self._recorders(monkeypatch)
        renv.post_hold(repo="o/r", pr_number=7, card="DRE-1", body="a body")
        assert [p[0] for p in posted] == ["pr", "card"]

    def test_both_carry_the_composed_receipt(self, monkeypatch):
        posted = self._recorders(monkeypatch)
        renv.post_hold(repo="o/r", pr_number=7, card="DRE-1", body="a body")
        composed = pipeline_act.receipt(renv.HOLD_ACT, "a body")
        assert posted[0][1][posted[0][1].index("--body") + 1] == composed
        assert posted[1][2] == composed

    def test_the_pr_comment_is_bound_to_the_repo_and_the_number(self, monkeypatch):
        posted = self._recorders(monkeypatch)
        renv.post_hold(repo="o/r", pr_number=7, card="DRE-1", body="a body")
        argv = posted[0][1]
        assert argv[:3] == ["gh", "pr", "comment"]
        assert "7" in argv and "o/r" in argv

    def test_a_failed_pr_post_is_recorded_never_raised(self, monkeypatch):
        posted = self._recorders(monkeypatch, pr_rc=1)
        renv.POST_FAILURES.clear()
        renv.post_hold(repo="o/r", pr_number=7, card="DRE-1", body="a body")
        assert renv.POST_FAILURES, "a failed post is on the record"
        assert [p[0] for p in posted] == ["pr", "card"], (
            "the card mirror is what the console and the fleet alarm read — a "
            "failed PR post must not take it down with it"
        )

    def test_a_failed_card_post_is_recorded_never_raised(self, monkeypatch):
        self._recorders(monkeypatch)
        monkeypatch.setattr(linear_ops, "cmd_comment", lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("linear is down")))
        renv.POST_FAILURES.clear()
        renv.post_hold(repo="o/r", pr_number=7, card="DRE-1", body="a body")
        assert any("linear is down" in f for f in renv.POST_FAILURES)

    def test_no_pull_request_still_mirrors_to_the_card(self, monkeypatch):
        posted = self._recorders(monkeypatch)
        renv.post_hold(repo="", pr_number=None, card="DRE-1", body="a body")
        assert [p[0] for p in posted] == ["card"]


# --------------------------------------------------------------------------- #
# 7. the class the medic reads                                                 #
# --------------------------------------------------------------------------- #


def _classify_cli(workflow: str, log_text: str, tmp_path) -> str:
    path = tmp_path / "log.txt"
    path.write_text(log_text, encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.redirect_stderr(io.StringIO()):
            rc = medic_classify.main([workflow, str(path)])
    assert rc == 0, "a classifier never fails its caller"
    return buf.getvalue()


def _lines(out: str) -> dict:
    return dict(line.split("=", 1) for line in out.strip().splitlines())


class TestTheEnvironmentCrashClass:
    def test_a_review_run_is_an_environment_crash_and_still_an_infra_crash(
            self, tmp_path):
        out = _lines(_classify_cli("QA Review (reusable)", NATIVE_BINARY_LOG,
                                   tmp_path))
        assert out["infra_crash"] == "true", (
            "the three DRE-1921 gates in medic.yml read this line and this "
            "card leaves them untouched"
        )
        assert out["class"] == "environment_crash"
        assert out["signature"] == "native-binary-missing"
        assert out["check"].strip()
        assert out["meaning"].strip()

    def test_a_non_review_run_is_the_class_without_the_gate(self, tmp_path):
        out = _lines(_classify_cli("Agent Task", NATIVE_BINARY_LOG, tmp_path))
        assert out["infra_crash"] == "false"
        assert out["class"] == "environment_crash"
        assert out["signature"] == "native-binary-missing"

    def test_the_class_wins_over_the_neutral_critic_marker(self, tmp_path):
        """The reason this card exists. DRE-3416's log carried BOTH — the
        neutral marker is posted by the same crashed review — and the critic
        class won, so the operator was sent to check a token that was fine."""
        mixed = NATIVE_BINARY_LOG + _log(medic_classify.CRITIC_NEUTRAL_MARKER)
        assert medic_classify.classify("QA Review (reusable)", mixed) == (
            "environment_crash"
        )
        out = _lines(_classify_cli("QA Review (reusable)", mixed, tmp_path))
        assert out["class"] == "environment_crash"
        assert out["infra_crash"] == "true"

    @pytest.mark.parametrize("workflow,expected", [
        ("QA Review (reusable)", "critic_infra_crash"),
        ("Agent Task", "normal"),
    ])
    def test_a_429_classifies_exactly_as_it_did_before(self, workflow, expected,
                                                       tmp_path):
        out = _lines(_classify_cli(workflow, RATE_LIMIT_LOG, tmp_path))
        assert out["class"] == expected
        assert out["signature"] == ""
        assert out["check"] == ""
        assert out["meaning"] == ""

    def test_a_quoted_card_body_does_not_classify(self, tmp_path):
        hunk = _log('+        assert "Claude Code native binary not found" in body')
        out = _lines(_classify_cli("Agent Task", hunk, tmp_path))
        assert out["class"] == "normal"
        assert out["signature"] == ""

    def test_the_other_two_classes_are_untouched(self):
        upstream = _log(
            "failed to get runs: HTTP 503: No server is currently available to "
            "service your request. (https://api.github.com/repos/x/y/runs)"
        )
        assert medic_classify.classify("Reconcile", upstream) == "upstream_5xx"
        linear = _log(
            "linear_ops: POST https://api.linear.app/graphql failed: RATELIMITED"
        )
        assert medic_classify.classify("Reconcile", linear) == "linear_ratelimited"

    def test_every_printed_value_is_one_line(self, tmp_path):
        """`medic.yml` appends this stdout straight to `$GITHUB_OUTPUT`, which
        is one key per line — a value with a newline in it writes a second key
        nobody declared."""
        out = _classify_cli("QA Review (reusable)", NATIVE_BINARY_LOG, tmp_path)
        for line in out.strip().splitlines():
            assert "=" in line and "\n" not in line


# --------------------------------------------------------------------------- #
# 8. the registry row                                                          #
# --------------------------------------------------------------------------- #


class TestTheActRegistry:
    def test_the_row_is_the_contract(self):
        row = pipeline_act.record(renv.HOLD_ACT)
        assert row["tag"] == renv.HOLD_TAG
        assert row["adopted"] is True
        assert row["kind"] == "hold"
        assert row["state"] == "unchanged"
        assert row["next_actor"] == "operator"
        assert row["discharges"] == "review-retried-after-crash"
        assert row["cadence_s"] is None
        assert row["cadence_why"].strip()

    def test_the_subscriber_is_spelled_as_the_reviewer_unavailable_row_spells_it(self):
        assert (pipeline_act.subscriber(renv.HOLD_ACT)
                == pipeline_act.subscriber("reviewer-unavailable"))

    def test_the_emitter_pins_the_one_writer(self):
        emits = pipeline_act.record(renv.HOLD_ACT)["emits"]
        assert emits["file"] == "scripts/reviewer_environment.py"
        assert emits["anchor"] == "def hold_receipt("
        source = (ROOT / emits["file"]).read_text("utf-8")
        assert source.count(emits["anchor"]) == 1

    def test_the_name_carries_no_tag_and_the_tag_collides_with_none(self):
        """`pipeline_act._collision_problems`: `tag in body` is how every one
        of these receipts is counted, so a name that contains a tag would put
        a live key into receipts it does not belong to."""
        assert renv.HOLD_TAG not in renv.HOLD_ACT
        for name in pipeline_act.acts():
            other = pipeline_act.tag(name)
            if other == renv.HOLD_TAG:
                continue
            assert other not in renv.HOLD_ACT
            assert other not in renv.HOLD_TAG
            assert renv.HOLD_TAG not in other

    def test_the_shipped_registry_is_clean(self):
        assert pipeline_act.problems() == []

    def test_the_evidence_note_is_declared_not_an_act(self):
        """It is a REPORT about the commit — it creates no obligation, hands
        the work to nobody and discharges nothing — so it is declared in the
        `unconverted` block rather than given a row."""
        rows = [
            row for row in pipeline_act.load()["unconverted"]
            if row["file"] == "scripts/reviewer_environment.py"
        ]
        assert len(rows) == 1
        assert rows[0]["kind"] == "not-an-act"
        assert rows[0]["why"].strip()

    def test_every_receipt_site_is_composed_or_declared(self):
        import check_act_receipts

        assert check_act_receipts.problems() == []


# --------------------------------------------------------------------------- #
# 9. the CLI seams                                                             #
# --------------------------------------------------------------------------- #


def _cli(*argv: str, **kwargs):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "reviewer_environment.py"), *argv],
        capture_output=True, text=True, cwd=str(ROOT), check=False, **kwargs,
    )


class TestTheCli:
    def test_detect_names_the_signature(self, tmp_path):
        path = tmp_path / "log.txt"
        path.write_text(NATIVE_BINARY_LOG, encoding="utf-8")
        result = _cli("detect", str(path))
        assert result.returncode == 0, result.stdout + result.stderr
        assert _lines(result.stdout)["signature"] == "native-binary-missing"

    def test_detect_on_a_clean_log_says_so_and_still_exits_zero(self, tmp_path):
        path = tmp_path / "log.txt"
        path.write_text(RATE_LIMIT_LOG, encoding="utf-8")
        result = _cli("detect", str(path))
        assert result.returncode == 0
        assert _lines(result.stdout)["signature"] == ""

    def test_detect_on_an_unreadable_log_is_no_signature_not_a_crash(self):
        result = _cli("detect", "/no/such/log.txt")
        assert result.returncode == 0
        assert _lines(result.stdout)["signature"] == ""

    def test_note_composes_the_evidence_note(self, monkeypatch):
        posted = {}
        monkeypatch.setattr(linear_ops, "cmd_comment",
                            lambda card, body, *f: posted.update(card=card, body=body))
        rc = renv.main(["note", "--card", "DRE-1", "--sha", SHA,
                        "--signature", "native-binary-missing",
                        "--run-url", RUN_URL])
        assert rc == 0
        assert posted["card"] == "DRE-1"
        assert posted["body"] == renv.evidence_note(
            renv.by_slug("native-binary-missing"), SHA, RUN_URL
        )

    def test_hold_composes_the_receipt_and_posts_both(self, monkeypatch):
        posted: list = []
        monkeypatch.setattr(renv.subprocess, "run", lambda argv, **k: (
            posted.append(("pr", argv))
            or SimpleNamespace(returncode=0, stdout="", stderr="")))
        monkeypatch.setattr(linear_ops, "cmd_comment",
                            lambda card, body, *f: posted.append(("card", body)))
        rc = renv.main(["hold", "--card", "DRE-1", "--sha", SHA,
                        "--signature", "credential-refused", "--count", "2",
                        "--repo", "o/r", "--pr", "7"])
        assert rc == 0
        assert [p[0] for p in posted] == ["pr", "card"]
        assert posted[1][1] == pipeline_act.receipt(
            renv.HOLD_ACT,
            renv.hold_receipt(renv.by_slug("credential-refused"), SHA, 2),
        )

    def test_an_unknown_signature_is_refused_rather_than_guessed(self):
        result = _cli("hold", "--card", "DRE-1", "--sha", SHA,
                      "--signature", "no-such-signature", "--count", "2")
        assert result.returncode != 0
        assert "no-such-signature" in (result.stdout + result.stderr)


# --------------------------------------------------------------------------- #
# 10. the files this card does not touch                                       #
# --------------------------------------------------------------------------- #


class TestTheSiblingsAreUntouched:
    @pytest.mark.parametrize("path", [
        "scripts/reconcile.py",
        ".github/workflows/qa-review.yml",
    ])
    def test_the_sweep_and_the_two_workflows_never_name_the_new_module(self, path):
        """DRE-3428 ships the vocabulary and its one writer. Wiring the medic
        and the sweep to it is the siblings' work, and a half-wire here is a
        second answer to the question this card exists to make single.

        `.github/workflows/medic.yml` was on this list until DRE-3430 wired
        it — that sibling is the one that reads the class, names the cause on
        the card and holds after the one retry, and its own wiring is pinned in
        `tests/test_medic_environment_hold.py`. The sweep (DRE-3435) is still
        unwired, so `reconcile.py` stays fenced."""
        text = (ROOT / path).read_text("utf-8")
        assert "reviewer_environment" not in text
        assert renv.HOLD_TAG not in text


def test_the_fixture_carries_the_hold_body_byte_identically():
    """`tests/test_act_emission.py` proves the posted body is the frozen one;
    this proves the frozen one is what `hold_receipt()` composes today."""
    frozen = json.loads(
        (ROOT / "tests" / "fixtures" / "act-receipt-bodies.json").read_text("utf-8")
    )
    assert frozen["python"][renv.HOLD_ACT] == renv.hold_receipt(
        renv.by_slug("native-binary-missing"), SHA, 2
    )
