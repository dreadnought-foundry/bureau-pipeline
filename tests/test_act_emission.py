"""RED-first tests: every act emits its trailer, and every receipt body is
byte-identical (DRE-2826).

DRE-2825 landed the vocabulary and the writer and deliberately changed no
behaviour — `test_nothing_changes_behaviour_yet` in `tests/test_pipeline_acts.py`
said so, and said the emission card deletes it. This is that card.

WHAT "BYTE-IDENTICAL" IS PROVEN AGAINST HERE, and why it is not an assertion.

`tests/fixtures/act-receipt-bodies.json` is a capture of the LIVE wording: each
body was rendered by running the real emitting function on the code as it stood
before this card, with nothing stubbed but its I/O. Every test below drives that
same real function and asserts the posted comment is

    <the frozen live body>  +  "\\n\\n"  +  pipeline_act.trailer(<act>)

so a single reworded character anywhere in a receipt goes red, and the only way
to make it green is to change the frozen capture — a deliberate act with a diff
on it. Asserting "the trailer is appended" would prove nothing about the body,
and the body is the half that carries every idempotency key and per-sha budget
counter in the pipeline (`_worker_receipt_count`, `tag in body`).

The four acts emitted from workflow YAML have no Python function to drive. They
are pinned the same way at the source: the exact multi-line shell literal, as it
stood before this card, must still appear exactly once in the workflow file —
and the file must route it through the receipt writer rather than posting it
raw.

Run: cd bureau-pipeline && python3 -m pytest tests/test_act_emission.py -v
"""
from __future__ import annotations

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
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import pipeline_act  # noqa: E402
import reconcile  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "act-receipt-bodies.json"

# site id -> the driver that makes the real code post that site's receipt.
# Registered rather than listed so a new site is one decorator, and the
# coverage test below reads the registry rather than a second hand-kept list.
SITES: dict[str, tuple[str, object]] = {}


def site(site_id: str, act: str):
    def register(fn):
        SITES[site_id] = (act, fn)
        return fn
    return register


class _Posted(BaseException):
    """Raised by a recorder to stop the driver at the moment of the write.

    The receipt is the thing under test; whatever the sweep does afterwards
    (a label, a state move, a dispatch) is another card's business and would
    only add stubs that can drift.
    """

    def __init__(self, body: str):
        super().__init__(body)
        self.body = body


def _card_recorder(mp):
    def record(_identifier, body):
        raise _Posted(body)
    mp.setattr(reconcile.linear_ops, "cmd_comment", record)


def _pr_recorder(mp):
    def record(argv, **_kwargs):
        if argv[:3] == ["gh", "pr", "comment"]:
            raise _Posted(argv[argv.index("--body") + 1])
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    mp.setattr(reconcile.subprocess, "run", record)


def _drive(site_id: str) -> str:
    """Run one site's driver and return the body it posted."""
    act, driver = SITES[site_id]
    with pytest.MonkeyPatch.context() as mp:
        try:
            driver(mp)
        except _Posted as posted:
            return posted.body
    raise AssertionError(f"{site_id}: the driver posted nothing")


# --------------------------------------------------------------------------- #
# the drivers — one per receipt site, driving the real function                #
# --------------------------------------------------------------------------- #


@site("blocker-reference-broken", "blocker-reference-broken")
def _drive_bad_reference(mp):
    mp.setattr(reconcile.linear_ops, "count_comments", lambda *_a, **_k: 0)
    _card_recorder(mp)
    reconcile._card_skips.clear()
    reconcile.skip_bad_reference("DRE-1", RuntimeError("no such issue"))


def _watchdog_card(state: str) -> dict:
    return {
        "id": "uuid-1",
        "identifier": "DRE-1",
        "title": "a card",
        "description": "work",
        "state": {"name": state},
        "labels": {"nodes": [{"name": "agent:engineer"}]},
        "updatedAt": "2026-01-01T00:00:00Z",
    }


@site("card-stranded/no-run", "card-stranded")
def _drive_stranded_no_run(mp):
    mp.setattr(reconcile, "active_cards", lambda *_a, **_k: [_watchdog_card("Todo")])
    mp.setattr(reconcile, "held", lambda _c: False)
    mp.setattr(reconcile, "hand_built", lambda _c: False)
    mp.setattr(reconcile, "card_repo", lambda _c: reconcile.REPO_SLUG)
    mp.setattr(reconcile.validate_card, "VALID_SLUGS", {reconcile.REPO_SLUG})
    mp.setattr(reconcile.linear_ops, "comment_bodies", lambda *_a, **_k: [])
    mp.setattr(reconcile, "flag_stalled_planning", lambda: set())
    _card_recorder(mp)
    reconcile.flag_stranded()


@site("card-stranded/planning", "card-stranded")
def _drive_stranded_planning(mp):
    mp.setattr(reconcile, "active_cards", lambda *_a, **_k: [_watchdog_card("Planning")])
    mp.setattr(reconcile, "held", lambda _c: False)
    mp.setattr(reconcile, "hand_built", lambda _c: False)
    mp.setattr(reconcile.linear_ops, "comment_bodies", lambda *_a, **_k: [])
    _card_recorder(mp)
    reconcile.flag_stalled_planning()


@site("intake-overdue", "intake-overdue")
def _drive_intake_overdue(mp):
    card = _watchdog_card("Intake")
    mp.setattr(reconcile, "active_cards", lambda *_a, **_k: [card])
    mp.setattr(reconcile, "hand_built", lambda _c: False)
    mp.setattr(reconcile, "age_minutes", lambda *_a, **_k: 4320.0)
    mp.setattr(reconcile.linear_ops, "comment_bodies", lambda *_a, **_k: [])
    _card_recorder(mp)
    reconcile.escalate_aged_intake()


@site("pr-without-checks", "pr-without-checks")
def _drive_no_checks(mp):
    def fake_gh(*args):
        if args[1].endswith("/check-runs"):
            return "[]"
        return json.dumps({"committer": {"date": "2026-01-01T00:00:00Z"}})
    mp.setattr(reconcile, "gh", fake_gh)
    mp.setattr(reconcile, "card_branch", lambda _r: True)
    mp.setattr(reconcile, "branch_card", lambda _r: "DRE-1")
    mp.setattr(reconcile, "age_minutes", lambda *_a, **_k: 999.0)
    mp.setattr(reconcile, "card_parked_for_human", lambda _c: True)
    mp.setattr(reconcile.linear_ops, "comment_bodies", lambda *_a, **_k: [])
    _card_recorder(mp)
    reconcile._flag_one_silent_pr({
        "number": 7,
        "headRefName": "agent/DRE-1-slug",
        "headRefOid": "d34db33fcafe1234",
        "mergeStateStatus": "DIRTY",
        "isDraft": False,
    })


@site("work-never-landed/branch", "work-never-landed")
def _drive_unlanded_branch(mp):
    def fake_gh(*args):
        if args[1].endswith("/pulls"):
            return "[]"
        return json.dumps({"ahead": 3, "last": "2026-01-01T00:00:00Z"})
    mp.setattr(reconcile, "gh", fake_gh)
    mp.setattr(reconcile, "default_branch", lambda: "main")
    mp.setattr(reconcile, "branch_card", lambda _r: "DRE-1")
    mp.setattr(reconcile, "card_state", lambda _c: "In Progress")
    mp.setattr(reconcile, "age_minutes", lambda *_a, **_k: 240.0)
    mp.setattr(reconcile.linear_ops, "comment_bodies", lambda *_a, **_k: [])
    _card_recorder(mp)
    reconcile._flag_one_unlanded_branch(
        {"name": "agent/DRE-1-slug", "sha": "d34db33fcafe1234"}, set()
    )


@site("work-never-landed/no-branch", "work-never-landed")
def _drive_unlanded_no_branch(mp):
    card = _watchdog_card("Todo")
    mp.setattr(reconcile, "active_cards", lambda *_a, **_k: [card])
    mp.setattr(reconcile, "hand_built", lambda _c: True)
    mp.setattr(reconcile, "held", lambda _c: False)
    mp.setattr(reconcile, "card_repo", lambda _c: reconcile.REPO_SLUG)
    mp.setattr(reconcile, "age_minutes", lambda *_a, **_k: 240.0)
    mp.setattr(reconcile.linear_ops, "comment_bodies", lambda *_a, **_k: [])
    _card_recorder(mp)
    reconcile._flag_hand_built_idle([], set())


def _restart_driver(mp, merge_state: str):
    mp.setattr(reconcile, "_actions_runs_busy", lambda _w: False)
    mp.setattr(reconcile, "gh", lambda *_a: json.dumps([{
        "number": 7,
        "headRefName": "agent/DRE-1-slug",
        "mergeStateStatus": merge_state,
        "comments": [],
    }]))
    mp.setattr(reconcile, "card_branch", lambda _r: True)
    mp.setattr(reconcile, "_thread_worth_fetching", lambda _p: True)
    mp.setattr(reconcile, "_pr_thread", lambda _n: [])
    mp.setattr(reconcile.fix_context, "operator_decision", lambda *_a, **_k: {"id": 1})
    mp.setattr(reconcile.fix_context, "decision_consumed", lambda *_a, **_k: False)
    mp.setattr(reconcile, "_release_card", lambda *_a, **_k: None)
    mp.setattr(reconcile, "gh_dispatch", lambda *_a, **_k: None)

    def record(_number, body):
        raise _Posted(body)
    mp.setattr(reconcile, "_post_pr_note", record)
    reconcile.restart_answered_blockers()


@site("fix-loop-restarted/conflicted", "fix-loop-restarted")
def _drive_restart_conflicted(mp):
    _restart_driver(mp, "DIRTY")


@site("fix-loop-restarted/dispatched", "fix-loop-restarted")
def _drive_restart_dispatched(mp):
    _restart_driver(mp, "CLEAN")


@site("dependabot-review-forced", "dependabot-review-forced")
def _drive_dependabot_receipt(mp):
    _pr_recorder(mp)
    reconcile._post_dependabot_receipt(
        {"number": 7, "headRefOid": "d34db33fcafe1234"}
    )


@site("review-retried-after-crash", "review-retried-after-crash")
def _drive_rereview_receipt(mp):
    mp.setattr(reconcile, "review_workflow", lambda: "qa-review.yml")
    _pr_recorder(mp)
    reconcile._post_rereview_receipt(
        {"number": 7, "headRefOid": "d34db33fcafe1234"}
    )


@site("reviewer-unavailable", "reviewer-unavailable")
def _drive_reviewer_down(mp):
    mp.setattr(reconcile, "branch_card", lambda _r: "DRE-1")
    mp.setattr(reconcile, "review_workflow", lambda: "qa-review.yml")
    mp.setattr(reconcile.linear_ops, "comment_bodies", lambda *_a, **_k: [])
    _card_recorder(mp)
    reconcile._report_reviewer_down(
        {"number": 7, "headRefName": "agent/DRE-1-slug",
         "headRefOid": "d34db33fcafe1234"}, 1
    )


def _stale_verdict_driver(mp, verdicts):
    mp.setattr(reconcile, "branch_card", lambda _r: "DRE-1")
    mp.setattr(reconcile.linear_ops, "comment_bodies", lambda *_a, **_k: [])
    mp.setattr(reconcile, "critic_comment_bodies", lambda _p: verdicts)
    _card_recorder(mp)
    reconcile._report_stale_verdict(
        {"number": 7, "headRefName": "agent/DRE-1-slug",
         "headRefOid": "d34db33fcafe1234"}
    )


@site("verdict-left-behind/known-sha", "verdict-left-behind")
def _drive_stale_verdict_known(mp):
    _stale_verdict_driver(mp, [
        "VERDICT: APPROVE @" + "0badc0de" * 5
    ])


@site("verdict-left-behind/unknown-sha", "verdict-left-behind")
def _drive_stale_verdict_unknown(mp):
    """Both halves of the one sentence that varies: the reviewed sha is named
    when it can be read and elided when it cannot. A capture of only one of
    them leaves the other free to drift."""
    _stale_verdict_driver(mp, [])


def _retry_declined_driver(mp, decision):
    """The medic's refusal to retry (DRE-2954), driven through the real
    composer. The Linear write is the recorder's; everything before it — the
    rule, the wording, the trailer — is `medic_retry.py`'s own."""
    import medic_retry

    _card_recorder(mp)
    medic_retry.post_declined(
        "DRE-2937",
        decision,
        run_url="https://github.com/dreadnought-foundry/agent-bureau/actions/runs/33568177277",
    )


@site("retry-declined/card-parked", "retry-declined")
def _drive_retry_declined_parked(mp):
    import medic_retry

    _retry_declined_driver(mp, medic_retry.decide(
        parked_because=(
            "it was moved to Backlog at 2026-09-01T23:36:00.000Z by the "
            "pipeline's own hold, after this run started"
        )
    ))


@site("retry-declined/turn-exhaustion", "retry-declined")
def _drive_retry_declined_turns(mp):
    """The second rule's wording. Both are captured because the refusal names
    WHICH rule it applied, and a capture of only one leaves the other free to
    drift into saying nothing."""
    import medic_retry

    _retry_declined_driver(mp, medic_retry.decide(execution={
        "is_error": True,
        "subtype": "error_max_turns",
        "num_turns": 151,
        "total_cost_usd": 16.79,
        "duration_ms": 1_320_000,
        "result": "Reached maximum number of turns (150)",
    }))


# --------------------------------------------------------------------------- #
# 1. the body survives byte-identical, and the trailer is appended             #
# --------------------------------------------------------------------------- #


def _frozen() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestEveryPythonSiteEmitsItsTrailer:
    def test_the_capture_exists(self):
        assert FIXTURE.exists(), (
            "the byte-identical proof is a capture of the live wording, not an "
            "assertion about it"
        )

    @pytest.mark.parametrize("site_id", sorted(SITES))
    def test_the_body_is_byte_identical_and_the_trailer_is_appended(self, site_id):
        act, _ = SITES[site_id]
        expected = _frozen()["python"][site_id]
        posted = _drive(site_id)
        trailer = pipeline_act.trailer(act)
        assert posted == f"{expected}\n\n{trailer}", (
            f"{site_id}: the live wording changed. The body a receipt carries "
            "is its idempotency key and its budget counter — rewording one "
            "makes every in-flight receipt invisible."
        )
        assert posted.startswith(expected)
        assert len(posted) == len(expected) + 2 + len(trailer)

    @pytest.mark.parametrize("site_id", sorted(SITES))
    def test_the_trailer_names_this_site_s_act(self, site_id):
        act, _ = SITES[site_id]
        fields = pipeline_act.read_trailer(_drive(site_id))
        assert fields is not None, f"{site_id} posts no trailer at all"
        assert fields["act"] == act
        assert fields["tag"] == pipeline_act.tag(act)

    def test_every_python_emitted_act_has_a_site(self):
        """No act declared as emitted from a `scripts/` file may go undriven —
        otherwise the guard proves the call site is wrapped and nothing proves
        what it posts."""
        driven = {act for act, _ in SITES.values()}
        for name in pipeline_act.acts():
            emitter = pipeline_act.record(name)["emits"]["file"]
            if emitter.startswith("scripts/"):
                assert name in driven, f"{name} is emitted from {emitter} and never driven"


# --------------------------------------------------------------------------- #
# 2. the four acts emitted from workflow YAML                                  #
# --------------------------------------------------------------------------- #


# site id -> the act it emits. Two of these acts have a second wording (the
# conflict-mode variant of the push marker), and a capture of only one of them
# leaves the other free to drift.
WORKFLOW_SITES = {
    "conflict-agent-dispatched": "conflict-agent-dispatched",
    "fix-attempt-landed/fix": "fix-attempt-landed",
    "fix-attempt-landed/conflict": "fix-attempt-landed",
    "fix-attempt-disputed": "fix-attempt-disputed",
    "run-failure-diagnosed": "run-failure-diagnosed",
}


class TestEveryWorkflowSiteEmitsItsTrailer:
    @pytest.mark.parametrize("site_id", sorted(WORKFLOW_SITES))
    def test_the_shell_body_is_still_the_live_wording(self, site_id):
        """Frozen at the source: the exact literal the workflow posted before
        this card must still be there, exactly once."""
        act = WORKFLOW_SITES[site_id]
        literal = _frozen()["workflow"][site_id]
        text = (ROOT / pipeline_act.record(act)["emits"]["file"]).read_text("utf-8")
        assert text.count(literal) == 1, (
            f"{site_id}: the shell body changed. It is byte-identical or it is "
            "a different receipt."
        )

    @pytest.mark.parametrize("act", sorted(set(WORKFLOW_SITES.values())))
    def test_the_workflow_composes_it_through_the_writer(self, act):
        text = (ROOT / pipeline_act.record(act)["emits"]["file"]).read_text("utf-8")
        assert f"receipt {act}" in text or f"--act={act}" in text or f"--act {act}" in text, (
            f"{act} is posted without composing through pipeline_act.receipt()"
        )

    def test_the_receipt_cli_appends_the_trailer_and_nothing_else(self, tmp_path):
        """The seam the workflows use. A shell body reaches the writer through
        this CLI, so what it does to the body is the same question the Python
        sites answer — and the answer must be the same one."""
        body = "  \U0001f6a8 leading space, inner  gap, no trailing newline  "
        out = tmp_path / "receipt.md"
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "pipeline_act.py"), "receipt",
             "fix-attempt-landed", "--body", body, "--out", str(out)],
            capture_output=True, text=True, cwd=str(ROOT), check=False,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        written = out.read_text(encoding="utf-8")
        assert written == f"{body}\n\n{pipeline_act.trailer('fix-attempt-landed')}"

    def test_the_receipt_cli_refuses_an_unknown_act(self, tmp_path):
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "pipeline_act.py"), "receipt",
             "no-such-act", "--body", "x", "--out", str(tmp_path / "o.md")],
            capture_output=True, text=True, cwd=str(ROOT), check=False,
        )
        assert r.returncode != 0
        assert not (tmp_path / "o.md").exists(), (
            "a refused receipt must write nothing — a half-written file would "
            "be posted as if it were composed"
        )

    def test_linear_ops_comment_composes_when_an_act_is_named(self):
        """The card-side seam, used by the conflict sweep and the medic."""
        import linear_ops

        sent = {}
        original = linear_ops.gql

        def fake_gql(query, variables=None):
            if "commentCreate" in query:
                sent["body"] = variables["input"]["body"]
                return {"commentCreate": {"success": True}}
            return original(query, variables)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(linear_ops, "gql", fake_gql)
            mp.setattr(linear_ops, "get_issue", lambda _i: {"id": "uuid"})
            linear_ops.cmd_comment("DRE-1", "a body", "--act=fix-attempt-landed")
        assert sent["body"] == (
            "a body\n\n" + pipeline_act.trailer("fix-attempt-landed")
        )

    def test_linear_ops_comment_is_unchanged_without_an_act(self):
        """Nothing existing is deleted, and nothing existing changes shape: a
        comment that names no act is posted exactly as it always was."""
        import linear_ops

        sent = {}
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(linear_ops, "gql", lambda q, v=None: sent.update(
                body=v["input"]["body"]) or {"commentCreate": {"success": True}})
            mp.setattr(linear_ops, "get_issue", lambda _i: {"id": "uuid"})
            linear_ops.cmd_comment("DRE-1", "a body")
        assert sent["body"] == "a body"
