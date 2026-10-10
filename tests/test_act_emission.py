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
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import check_act_receipts  # noqa: E402
import medic_classify  # noqa: E402 — the neutral could-not-run receipt's marker
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
    # DRE-5743: the stamp names the run lookup it was read off. Hermetic, and
    # pinned to one workflow name whichever repo the session imported first.
    mp.setattr(reconcile, "build_workflow", lambda: "agent-task.yml")
    mp.setattr(reconcile, "_actions_read", lambda args: ("[]", None))
    _card_recorder(mp)
    reconcile.flag_stranded()


# `card-stranded/planning` was the second site of this act and is GONE
# (DRE-4124). A Planning card that stalls is no longer held in place with a
# receipt — since DRE-5286 it is parked in Triage, the operator's queue, under
# the sweep's own stall-park note, which is declared `unconverted` and carries
# no trailer. The act keeps its one remaining site above, in flag_stranded.


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


@site("hand-work-overdue", "hand-work-overdue")
def _drive_hand_work_overdue(mp):
    card = _watchdog_card("Hand-work")
    card["labels"]["nodes"].append({"name": "operator-step"})
    card["comments"] = {"nodes": [{
        "body": "🧹 Auto-promoted Backlog → Hand-work: routed **OPERATOR**",
        "createdAt": "2026-01-01T00:00:00Z",
    }]}
    mp.setattr(reconcile, "active_cards", lambda *_a, **_k: [card])
    mp.setattr(reconcile, "held", lambda _c: False)
    mp.setattr(reconcile, "card_repo", lambda _c: reconcile.REPO_SLUG)
    mp.setattr(reconcile, "age_minutes", lambda *_a, **_k: 1530.0)
    _card_recorder(mp)
    reconcile.flag_stranded()


def _restart_driver(mp, merge_state: str):
    mp.setattr(reconcile, "_actions_runs_busy", lambda _w: False)
    # This repo has its fix stub: the no-fix-agent hold (DRE-4378) is a
    # different act with a driver of its own, below.
    mp.setattr(reconcile, "fix_agent_absent", lambda: False)
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


@site("repo-has-no-fix-agent", "repo-has-no-fix-agent")
def _drive_fix_agent_absent(mp):
    # The incident's own repo, pinned: this body names the repo and the stub
    # it lacks, so it would otherwise read differently under every REPO the
    # suite is run with.
    mp.setattr(reconcile, "REPO", "dreadnought-foundry/bureau-harness")
    mp.setattr(reconcile, "REPO_SLUG", "bureau-harness")
    # The real absence decision, off a real listing: this repo's workflows
    # directory is read, parsed, and does not carry the fix stub (DRE-4378).
    mp.setattr(reconcile, "gh", lambda *_a: json.dumps(
        [{"name": "agent-task.yml", "type": "file"},
         {"name": "qa-review.yml", "type": "file"}]))
    _pr_recorder(mp)
    reconcile.fix_agent_absent_hold(
        {"number": 7, "headRefOid": "d34db33fcafe1234", "comments": []}
    )


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


@site("merge-ref-refreshed", "merge-ref-refreshed")
def _drive_merge_ref_refresh(mp):
    """DRE-3144. The body is `stale_merge_ref.receipt_detail()` — this drives
    the sweep's write path, so the capture covers the PUT succeeding first and
    the receipt being composed from the decision the module returned."""
    mp.setattr(reconcile, "STALE_MERGE_REFRESH_CAP", 3)
    _pr_recorder(mp)
    reconcile._refresh_one_merge_ref(
        {"number": 7, "headRefName": "agent/DRE-1-slug",
         "headRefOid": "d34db33fcafe1234", "comments": []},
        reconcile.stale_merge_ref.Decision(
            action=reconcile.stale_merge_ref.REFRESH,
            reason="red on this head and on the merge base, green on `main`",
            inherited=["Console backend (pytest)"],
            base_sha="0badc0de" * 5,
            main_sha="f00dface" * 5,
            behind_by=2,
        ),
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


@site("reviewer-environment-hold", "reviewer-environment-hold")
def _drive_reviewer_environment_hold(mp):
    """The runner-environment hold (DRE-3428). `post_hold` composes through
    `pipeline_act.receipt()` and posts the PULL REQUEST first — the sha-bound
    counter the sweep reads — so the recorder stops it there, and the card
    mirror the console reads carries the same bytes."""
    import reviewer_environment

    def record(argv, **_kwargs):
        if argv[:3] == ["gh", "pr", "comment"]:
            raise _Posted(argv[argv.index("--body") + 1])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    mp.setattr(reviewer_environment.subprocess, "run", record)
    reviewer_environment.post_hold(
        repo="dreadnought-foundry/bureau-pipeline",
        pr_number=7,
        card="DRE-1",
        body=reviewer_environment.hold_receipt(
            reviewer_environment.by_slug("native-binary-missing"),
            "d34db33fcafe1234d34db33fcafe1234d34db33f",
            2,
        ),
    )


@site("reviewer-outage-fleet-wide", "reviewer-outage-fleet-wide")
def _drive_fleet_reviewer_outage(mp):
    """The fleet-wide outage card (DRE-3433's decision, DRE-3435's wiring).

    Drives the real sweep backstop: three could-not-run receipts on open pull
    requests, no open card, so the decision is `file` and the receipt is what
    `report_fleet_reviewer_outage` posts on the card it just created.

    The CLOCK is frozen, which the other captures do not need. Every line of
    this body is derived from the outcomes' own timestamps — the title's `since
    15:19 PT`, the run and repo counts, the `@<first_at>` idempotency key — so
    a wall clock would make the capture unfreezable rather than merely fragile.
    """
    from datetime import UTC as _UTC  # noqa: PLC0415 — the frozen clock, below
    from datetime import datetime as _datetime  # noqa: PLC0415

    frozen = _datetime(2026, 9, 8, 22, 40, tzinfo=_UTC)

    class _Clock:
        @staticmethod
        def now(_tz=None):
            return frozen

    def _pr(number, minute):
        return {
            "number": number,
            "headRefName": f"agent/DRE-{number}-widget",
            "headRefOid": "d34db33fcafe1234d34db33fcafe1234d34db33f",
            "baseRefName": "main",
            "mergeStateStatus": "CLEAN",
            "isDraft": False,
            "comments": [{
                "body": f"{medic_classify.CRITIC_NEUTRAL_MARKER} — the run died",
                "createdAt": f"2026-09-08T22:{minute}:00Z",
                "url": f"https://github.com/x/y/pull/{number}#issuecomment-{number}",
            }],
        }

    listing = json.dumps([_pr(141, 19), _pr(142, 24), _pr(143, 31)])
    mp.setattr(reconcile, "datetime", _Clock)
    mp.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
    mp.setattr(reconcile, "gh", lambda *a: (
        listing if a[:2] == ("pr", "list")
        else "https://github.com/x/y/actions/runs/34512000001/job/9"
    ))
    mp.setattr(reconcile, "gh_actions_read", lambda *a: (
        "job\tstep\t2026-09-08T22:19:03.1234567Z ReferenceError: Claude Code "
        "native binary not found at /home/runner/.local/bin/claude"
    ))
    mp.setattr(reconcile, "active_cards", lambda *_a, **_k: [])
    mp.setattr(reconcile.linear_ops, "find_open_prefix", lambda _p: None)
    mp.setattr(reconcile.linear_ops, "create_card",
               lambda *_a, **_k: {"identifier": "DRE-1", "url": "u"})
    _card_recorder(mp)
    reconcile.reset_sweep_cards()
    reconcile.report_fleet_reviewer_outage()


@site("proof-observation-pending", "proof-observation-pending")
def _drive_proof_waiting(mp):
    """The proof hold (DRE-3275). The only capture here whose wording was not
    read off code that already existed: the line is a grammar agreed with the
    console card that parses it, so it is frozen from the contract instead of
    from history — and freezing it is what stops the two repos drifting."""
    _card_recorder(mp)
    reconcile.linear_ops.cmd_proof_waiting(
        "DRE-1",
        "a failing agent PR in a console repo",
        "one open agent/* PR with a red check",
    )


@site("roll-up-activated", "roll-up-activated")
def _drive_roll_up_split(mp):
    """The roll-up's split record (DRE-4717). Drives `epic_split.activate`
    over a two-child split — the second child blocked on the first — with the
    `linear_ops` module it is handed standing in for Linear. Frozen from the
    first render of the receipt, like the proof hold above: there was no
    earlier wording to read it off."""
    import epic_split  # noqa: PLC0415 — only this driver needs it

    body = ("The engine ships first and ends at a watched release. More "
            "follows.\n\n## Acceptance criteria\n\n- [ ] planned on its own\n")
    children = [
        {"identifier": "DRE-2", "title": "[EPIC] bureau-pipeline: the engine",
         "body": body, "labels": ["agent:planner"], "blocked_by": []},
        {"identifier": "DRE-3", "title": "[EPIC] bureau-pipeline: the fleet",
         "body": body.replace("The engine ships first", "The fleet follows"),
         "labels": ["agent:planner"], "blocked_by": ["DRE-2"]},
    ]

    def posted(_identifier, text, *_flags):
        raise _Posted(text)

    epic_split.activate(SimpleNamespace(
        get_issue=lambda _i, **_k: {"state": {"name": "Planning"}},
        cmd_children_detail=lambda _i: print(json.dumps(children)),
        count_comments=lambda *_a, **_k: 0,
        cmd_comment=posted,
        cmd_advance=lambda *_a, **_k: None,
    ), "DRE-1")


def _queued_epic(state: str) -> dict:
    return {
        "id": "uuid-1", "identifier": "DRE-1", "title": "[EPIC] an epic",
        "description": "the plan", "state": {"name": state},
        "labels": {"nodes": [{"name": "repo:bureau-pipeline"},
                             {"name": "epic-queued"}]},
    }


@site("epic-queue-started", "epic-queue-started")
def _drive_epic_started(mp):
    """The start of the next waiting epic (DRE-5152): one slot free, one epic
    waiting, the owner's sweep. Frozen from the first render, like the proof
    hold above: the body is `epic_cap.started_receipt`, new with DRE-5134."""
    import epic_cap  # noqa: PLC0415 — only these drivers need it

    mp.setattr(reconcile, "REPO_SLUG", epic_cap.START_OWNER_SLUG)
    mp.setattr(epic_cap, "waiting_line", lambda: [_queued_epic("Green Light")])
    mp.setattr(epic_cap, "fleet_state", lambda: {
        "cap": 15, "count_rollup_parents": False,
        "in_motion": [{"identifier": f"DRE-{n}"} for n in range(100, 114)],
        "waiting": [_queued_epic("Green Light")],
    })
    mp.setattr(reconcile.linear_ops, "get_issue",
               lambda *_a, **_k: _queued_epic("Green Light"))
    mp.setattr(reconcile.linear_ops, "cmd_advance", lambda *_a, **_k: None)
    mp.setattr(reconcile, "card_state", lambda _i: "In Progress")
    _card_recorder(mp)
    reconcile.start_queued_epics()


@site("epic-start-redispatched", "epic-start-redispatched")
def _drive_epic_start_redispatched(mp):
    """The one re-dispatch of a start the relay never activated (DRE-5152):
    started two hours before a frozen clock, nothing posted since, and the
    epic's own repo sweeping. Frozen from the first render."""
    import epic_cap  # noqa: PLC0415
    import plan_run  # noqa: PLC0415

    now = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    started = pipeline_act.receipt(
        epic_cap.STARTED_ACT, epic_cap.started_receipt(1, 15, 15))
    mp.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
    mp.setattr(epic_cap, "labeled_elsewhere",
               lambda: [{"identifier": "DRE-1", "state": {"name": "In Progress"}}])
    mp.setattr(reconcile.linear_ops, "get_issue",
               lambda *_a, **_k: _queued_epic("In Progress"))
    mp.setattr(reconcile.linear_ops, "comment_records", lambda *_a, **_k: [{
        "body": started, "authored_by_pipeline": True,
        "created_at": "2026-10-06T10:00:00Z",
    }])
    mp.setattr(reconcile.linear_ops, "gql",
               lambda *_a, **_k: {"issue": _queued_epic("In Progress")})
    mp.setattr(plan_run, "fire", lambda *_a, **_k: (True, ""))
    _card_recorder(mp)
    reconcile.tend_epic_queue(now)


@site("proof-run-dispatched", "proof-run-dispatched")
def _drive_proof_run_dispatched(mp):
    """The sweep's proof-run dispatch (DRE-5926): one eligible PROOF card, a
    confirmed dispatch, and the receipt it posts after it — at a fixed clock."""
    import proof_dispatch  # noqa: PLC0415 — only this driver needs it
    import proof_release  # noqa: PLC0415
    import proof_run_state  # noqa: PLC0415

    card = {"id": "uuid-1", "identifier": "DRE-1", "title": "PROOF: it works",
            "description": "", "updatedAt": "2026-10-06T16:00:00Z",
            "state": {"name": "Hand-work"},
            "labels": {"nodes": [{"name": "repo:bureau-pipeline"}]},
            "comments": {"nodes": []}}

    class Board:
        def lane(self, state):
            return [card] if state == "Hand-work" else []

        def card(self, _identifier):
            return {"inverseRelations": {"nodes": []},
                    "parent": {"identifier": "DRE-2", "state": {"name": "In Progress"},
                               "children": {"nodes": []}}}

        def thread(self, _identifier):
            return [], "viewer"

    _card_recorder(mp)
    proof_dispatch.sweep(
        "dreadnought-foundry/bureau-pipeline", "bureau-pipeline", live=True,
        linear=Board(), read=lambda _path: None, find_pr=lambda _ident: None,
        run_state=lambda *_a, **_k: proof_run_state.State(
            "none", ["none"], 0, None, [], None),
        release=lambda *_a, **_k: proof_release.Reading("ready", ["ready"]),
        fire=lambda *_a, **_k: (True, ""), voices=lambda *_a, **_k: [],
        now=datetime(2026, 10, 6, 17, 0, tzinfo=UTC))


@site("switch-reason-cleared", "switch-reason-cleared")
def _drive_switch_reason_cleared(mp):
    """The sweep's switch receipt (DRE-6437): a switch off, every card its
    companion names Done, no receipt yet on the first, at a fixed clock."""
    import switch_reason  # noqa: PLC0415 — only this driver needs it

    def gql(_query, variables=None):
        return {"issues": {"nodes": [
            {"identifier": f"DRE-{n}", "state": {"name": "Done"}}
            for n in (variables or {}).get("numbers") or ()]}}

    mp.setattr(reconcile.linear_ops, "_thread_and_viewer",
               lambda *_a, **_k: ([], "viewer"))
    _card_recorder(mp)
    switch_reason.read_switches(
        {"PROOF_DISPATCH_LIVE_OFF_UNTIL": "DRE-1, DRE-2",
         "REPO": "dreadnought-foundry/bureau-pipeline",
         "REPO_SLUG": "bureau-pipeline"},
        gql=gql, now=datetime(2026, 10, 9, 12, 0, tzinfo=UTC),
        linear=switch_reason.LinearWrites(reconcile.linear_ops), live=True)


@site("plan-bound-rewrite", "plan-bound-rewrite")
def _drive_plan_bound_rewrite(mp):
    """The plan-critic bound's one rewrite (DRE-6452): two second-critic
    send-backs on the current attempt, no rewrite granted yet, a confirmed
    dispatch, and the receipt posted after it. Frozen from its first render:
    there was no earlier wording to read it off."""
    import plan_bound  # noqa: PLC0415 — only this driver needs it
    import plan_critic  # noqa: PLC0415

    def record(body):
        return {"body": body, "authored_by_pipeline": True,
                "created_at": "2026-10-09T03:22:00Z"}

    thread = [record(plan_critic.cycle_marker("DRE-1"))] + [
        record(plan_critic.marker("post", n, plan_critic.SEND_BACK, finding))
        for n, finding in enumerate((
            "The card doesn't say which of the two lanes the card lands in.",
            "The plan names a step that nothing in the repository runs.",
        ), 1)]
    mp.setattr(plan_bound.linear_ops, "comment_records",
               lambda *_a, **_k: thread)
    mp.setattr(plan_bound.review_rerun, "_cmd_dispatch", lambda _args: 0)
    _card_recorder(mp)
    plan_bound.run_exit("DRE-1", "post", "dreadnought-foundry/bureau-pipeline")


@site("promotion-stalled", "promotion-stalled")
def _drive_promotion_stalled(mp):
    """The promotion stall clock (DRE-4210): a parentless card refused for
    carrying no verdict, its refusal receipt three hours old at a fixed
    clock. The refusal is already on the card, so the first comment the sweep
    posts is the stall receipt. Frozen from its first render, like the proof
    hold above: there was no earlier wording to read it off."""
    frozen = datetime(2026, 10, 9, 18, 0, tzinfo=UTC)

    class _Clock:
        @staticmethod
        def now(_tz=None):
            return frozen

    card = {"id": "uuid-1", "identifier": "DRE-1", "title": "a one-off",
            "description": "work", "createdAt": "2026-10-01T00:00:00Z",
            "parent": None,
            "labels": {"nodes": [{"name": "repo:bureau-pipeline"}]},
            "comments": {"nodes": []},
            "inverseRelations": {"nodes": []}}
    mp.setattr(reconcile, "datetime", _Clock)
    mp.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
    mp.setattr(reconcile, "MAX_WIP", 8)  # the body names the cap
    mp.setattr(reconcile, "backlog_children", lambda *_a, **_k: [card])
    mp.setattr(reconcile.linear_ops, "count_comments",
               lambda _i, needle, **_k: 0 if needle == "promotion-stalled" else 1)
    mp.setattr(reconcile.linear_ops, "first_comment_at",
               lambda *_a: "2026-10-09T15:00:00.000Z")
    _card_recorder(mp)
    reconcile.promote_ready(active_count=0)


@site("agent-blocker-resolved", "agent-blocker-resolved")
def _drive_agent_blocker_resolved(mp):
    """The blocker resolver's receipt (DRE-6508): a `nothing-to-change`
    marker whose class module, stubbed here, canceled the card. Frozen from
    its first render: there was no earlier wording to read it off."""
    import blocker_class  # noqa: PLC0415 — only this driver needs it
    import blocker_resolve  # noqa: PLC0415

    reason = "- [x] The cap file says 15. — config/epic-cap.json holds 15 on main"
    mp.setitem(sys.modules, "blocker_nothing_to_change", SimpleNamespace(
        resolve=lambda *_a, **_k: ("canceled", "every criterion attested")))
    card = {"identifier": "DRE-1", "title": "a one-off", "description": "work",
            "labels": {"nodes": [{"name": "repo:bureau-pipeline"}]},
            "comments": {"nodes": []}}
    _card_recorder(mp)
    blocker_resolve.resolve_blocker(
        card, blocker_class.Blocker("nothing-to-change", reason,
                                    f"🛑 Agent blocked: class=nothing-to-change · {reason}"),
        repo="dreadnought-foundry/bureau-pipeline")


#: The hygiene agent's fourteen acts (DRE-5368, DRE-6180, DRE-6451). The core composes every one of
#: them through `hygiene.receipt` and posts it through its one comment seam,
#: `hygiene.send`; WHICH act a pass takes is a lane module's decision, and the
#: lanes are sibling cards. So each driver hands the real seam the receipt a
#: lane would, at a fixed cause, evidence and clock, and the capture pins the
#: wording the core gives it — first rendered here, like the proof hold above.
HYGIENE_ACTS = (
    "hygiene-gate-redispatch", "hygiene-branch-refresh", "hygiene-check-rerun",
    "hygiene-decision-needed", "hygiene-pr-close", "hygiene-resend-to-planning",
    "hygiene-card-close", "hygiene-proof-close", "hygiene-triage-return",
    "hygiene-review-move", "hygiene-card-cancel", "hygiene-cause-name",
    "hygiene-hold-clear", "hygiene-triage-alarm",
)


def _hygiene_driver(act: str):
    def drive(mp):
        import hygiene  # noqa: PLC0415 — only these drivers need it

        def posted(_identifier, text, *_flags):
            raise _Posted(text)

        mp.setattr(hygiene.linear_ops, "cmd_comment", posted)
        card = {"id": "uuid-1", "identifier": "DRE-1",
                "labels": {"nodes": []}, "children": {"nodes": []}}
        now = datetime(2026, 9, 30, 21, 5, tzinfo=UTC)
        body = hygiene.receipt(act, "the cause a lane read", ["run 1", "PR #7"], now)
        hygiene.send(hygiene.linear_comment(card, body), None)
    return drive


for _act in HYGIENE_ACTS:
    site(f"hygiene/{_act}", _act)(_hygiene_driver(_act))


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
        what it posts.

        The one exception is an act nothing POSTS yet: a pure decision module
        that composes a body and hands it back, whose wiring into the sweep is
        a sibling card. There is no write to record, so there is nothing to
        drive. It is `check_act_receipts.pending_acts()` — derived from "no
        file that posts has heard of this act", never a list here — so the
        exemption closes by itself when the wiring lands (DRE-3433).
        """
        pending = check_act_receipts.pending_acts()
        driven = {act for act, _ in SITES.values()}
        for name in pipeline_act.acts():
            emitter = pipeline_act.record(name)["emits"]["file"]
            if emitter.startswith("scripts/") and name not in pending:
                assert name in driven, f"{name} is emitted from {emitter} and never driven"

    def test_nothing_driven_here_is_exempt(self):
        """The two halves cannot both be true of one act, and saying so out
        loud is what stops the exemption quietly swallowing a real site: an act
        with a driver is an act something posts."""
        pending = check_act_receipts.pending_acts()
        for act, _ in SITES.values():
            assert act not in pending, (
                f"{act} has a driver here and is exempt as posted-by-nothing — "
                "one of the two is wrong"
            )


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
        text = pipeline_act._source(pipeline_act.record(act)["emits"]["file"])
        assert text.count(literal) == 1, (
            f"{site_id}: the shell body changed. It is byte-identical or it is "
            "a different receipt."
        )

    @pytest.mark.parametrize("act", sorted(set(WORKFLOW_SITES.values())))
    def test_the_workflow_composes_it_through_the_writer(self, act):
        text = pipeline_act._source(pipeline_act.record(act)["emits"]["file"])
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
