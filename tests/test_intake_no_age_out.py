"""RED-first: no card leaves Intake for being old (DRE-4141).

THE RULE IS THE CEO'S SIGNED CONSOLE ANSWER, 2026-09-17 09:57 PT, point 1: a
card is never moved out of Intake because it is old. No 48 hours, no timer.
Cards leave Intake when the groomer proposes them and the CEO approves the
batch in Green Light.

WHAT THIS REPLACES. DRE-2687 gave Intake a timer: `escalate_aged_intake` moved
any Intake card past `INTAKE_MAX_AGE_MINUTES` — the lane contract's
`stale_minutes: 2880` — into Green Light, three per sweep, and the lane
contract called that Intake's exit. In practice it was the largest single
source of noise in the CEO's queue and has been switched off by hand on every
repo since 2026-09-10, when it put about 130 cards into Green Light in one
morning. On 2026-09-17 05:33 PT one repo's hold was deleted for eight minutes
and three more cards reached his queue, two of them belonging to a repo whose
OWN hold was still set — because any one repo's sweep ages Intake cards for the
whole fleet. A safety valve held shut by four hand-set variables, that floods
the decision queue the moment any one of them is touched, is not a safety
valve.

WHAT THESE TESTS PIN:

  * **A 30-day-old Intake card, with no hold set anywhere, is still in Intake
    after the sweep** and carries no `intake-aged` comment. This is the
    criterion, and it fails against the code this card replaces.
  * **No code path in `reconcile.py` moves a card out of Intake on the grounds
    of its age.** Asserted behaviourally over a FULL sweep and structurally
    over the module's source: the sweep's only Intake write is a log line.
  * **Priority buys nothing.** A card at Urgent, of any age, is untouched —
    pinned on purpose, so the later Urgent fast-path card (the signed answer's
    point 2) has a baseline it changes deliberately rather than inherits.
  * **The report survives the move.** Every full pass prints one line saying
    how many cards are in Intake and how old the oldest is — a record, never a
    gate. Absent data renders as absent (console-honesty rule 2): an empty lane
    says there is no oldest card rather than inventing an age.
  * **The window is gone from the contract and from the code.** Intake states
    no age-based exit, `docs/lane-contract.md` is the render of that file, and
    `INTAKE_MAX_AGE_MINUTES` is not a trigger anywhere in the tree.
  * **`INTAKE_HOLD` keeps one job** — it pauses the groomer's drain. It no
    longer gates the sweep, because the sweep no longer moves anything.

Run: cd bureau-pipeline && python3 -m pytest tests/test_intake_no_age_out.py -v
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import intake_controls  # noqa: E402
import lane_contract  # noqa: E402
import pipeline_act  # noqa: E402
import reconcile  # noqa: E402

#: Thirty days, in minutes. Written as a literal on purpose: the number this
#: card deletes is the one the old tests derived their fixtures from, and a
#: fixture that asks the code how old "old" is could not fail once the code
#: stops having an opinion.
THIRTY_DAYS = 30 * 24 * 60

#: The comment the age-out used to post. Named here so the assertions read
#: against the string a live card would have carried, not against a constant
#: this card deletes.
AGED_TAG = "intake-aged"


def _iso(minutes_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _card(identifier="DRE-2687", state="Intake", labels=(),
          minutes_stale=THIRTY_DAYS, priority=0):
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "a card nothing has classified",
        "description": "work",
        "updatedAt": _iso(minutes_stale),
        "priority": priority,
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
    }


def _old_batch(n: int) -> list[dict]:
    """n Intake cards, DRE-1 the oldest through DRE-n the newest, every one of
    them far past the window this card deletes."""
    return [
        _card(identifier=f"DRE-{i}", minutes_stale=THIRTY_DAYS + (n - i) * 60)
        for i in range(1, n + 1)
    ]


def _run(cards):
    """Run the sweep's Intake phase over `cards`, with the `active_cards` stub
    honouring the lane filter the way Linear does. Returns
    (cmd_comment mock, cmd_advance mock)."""
    def by_lane(states=reconcile.SWEEP_STATES):
        return [c for c in cards if c["state"]["name"] in states]

    reconcile._write_failures.clear()
    with mock.patch.object(
        reconcile, "active_cards", side_effect=by_lane
    ), mock.patch.object(
        reconcile.linear_ops, "comment_bodies", return_value=[]
    ), mock.patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as comment, mock.patch.object(
        reconcile.linear_ops, "cmd_advance"
    ) as advanced:
        reconcile.report_intake_depth()
    return comment, advanced


def _main_mocks():
    """Every seam a full sweep touches except the lane reads themselves."""
    return [
        mock.patch.object(reconcile, name)
        for name in (
            "drain_retiring_lanes", "unstick_conflicts", "retrigger_dead_heads",
            "flag_no_checks_prs", "flag_unowned_prs", "flag_unlanded_work",
            "fix_approved_but_red", "retry_dead_fix_runs",
            "restart_answered_blockers", "review_dependabot_prs",
            "card_dependabot_prs",
            "recover_crashed_reviews", "check_dependabot_capacity",
            "promote_ready", "close_finished_epics", "report_break_glass",
            "report_fix_concurrency", "report_evicted_fix_runs",
            "report_epic_growth",
        )
    ] + [
        mock.patch.object(reconcile, "flag_stranded", return_value=set()),
        mock.patch.object(reconcile, "backlog_children", return_value=[]),
    ]


@contextlib.contextmanager
def _full_sweep(cards, promote_only=False):
    """A whole `main()` pass over `cards`, yielding the write mocks. Nothing
    stubs the Intake phase: the criterion is about what a LIVE sweep does."""
    def by_lane(states=reconcile.SWEEP_STATES):
        return [c for c in cards if c["state"]["name"] in states]

    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._stale_defects.clear()
    with contextlib.ExitStack() as stack:
        for m in _main_mocks():
            stack.enter_context(m)
        stack.enter_context(
            mock.patch.object(reconcile, "active_cards", side_effect=by_lane)
        )
        stack.enter_context(
            mock.patch.object(reconcile.linear_ops, "comment_bodies", return_value=[])
        )
        comment = stack.enter_context(
            mock.patch.object(reconcile.linear_ops, "cmd_comment")
        )
        advanced = stack.enter_context(
            mock.patch.object(reconcile.linear_ops, "cmd_advance")
        )
        reconcile.main(promote_only=promote_only)
        yield comment, advanced


# --------------------------------------------------------------------------
# 1: THE CRITERION — an old card stays where it is
# --------------------------------------------------------------------------
def test_a_thirty_day_old_intake_card_is_left_in_intake():
    """THE CRITERION. No hold set, no exemption on the card, thirty days old —
    and the sweep leaves it exactly where it is. Against the code this card
    replaces the card is in Green Light by now."""
    comment, advanced = _run([_card()])
    assert not advanced.called, (
        "a card was moved out of Intake for being old — the timer DRE-4141 "
        "deletes is still running"
    )
    assert not comment.called, "the sweep commented on a card it must not touch"


def test_the_sweep_posts_no_intake_aged_comment():
    """The second half of the criterion: not merely 'did not move' but 'said
    nothing about its age on the card'. A comment alone would still put the
    card in front of the CEO."""
    comment, _advanced = _run(_old_batch(5))
    bodies = [c.args[1] for c in comment.call_args_list if len(c.args) > 1]
    assert not any(AGED_TAG in body for body in bodies), bodies


def test_no_hold_is_needed_to_get_that_answer(monkeypatch):
    """The whole point of the change. `INTAKE_HOLD` cleared on every repo is
    the state the fleet will be in once the holds are lifted, and it is the
    state that flooded the queue on 2026-09-10."""
    monkeypatch.setattr(reconcile, "INTAKE_HOLD", None)
    _comment, advanced = _run(_old_batch(10))
    assert not advanced.called


def test_a_full_sweep_moves_nothing_out_of_intake():
    """Behavioural, over `main()` with nothing stubbing the Intake phase: the
    only Intake exit the sweep performs is none at all. The groomer's approved
    drain is a different process (`groomer.py drain`), and it is the way out."""
    with _full_sweep(_old_batch(8)) as (_comment, advanced):
        pass
    out_of_intake = [
        call.args for call in advanced.call_args_list
        if len(call.args) > 2 and call.args[2] == "Intake"
    ]
    assert out_of_intake == [], out_of_intake
    assert not advanced.called, (
        f"the sweep moved a card: {[c.args for c in advanced.call_args_list]}"
    )


def test_reconcile_never_advances_a_card_off_the_intake_lane():
    """Structural companion, read off the module's own source: `cmd_advance`
    with `Intake` as the from-lane is the exact call this card deletes, and a
    later edit that reintroduces it fails here even if it is unreachable."""
    source = (ROOT / "scripts" / "reconcile.py").read_text()
    offences = re.findall(r"cmd_advance\([^)]*\"Intake\"[^)]*\)", source)
    assert offences == [], offences


# --------------------------------------------------------------------------
# 2: priority buys nothing — the baseline the Urgent card will change
# --------------------------------------------------------------------------
@pytest.mark.parametrize("priority", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("minutes", [5, THIRTY_DAYS, 365 * 24 * 60])
def test_a_card_at_any_priority_and_any_age_is_untouched(priority, minutes):
    """Urgent is priority 1 in Linear. The signed answer's point 2 gives Urgent
    a fast path in a LATER card, and only for cards raised to Urgent after that
    rule ships — so today's answer must be 'nothing', pinned, or that card
    inherits a behaviour instead of choosing one."""
    _comment, advanced = _run(
        [_card(minutes_stale=minutes, priority=priority)]
    )
    assert not advanced.called


def test_the_twenty_four_urgent_cards_already_in_intake_stay_there():
    """Measured on the live board 2026-09-17: 24 Intake cards are already at
    Urgent, 23 of them created between 08-10 and 09-04. The signed answer is
    explicit that those go through the groomer."""
    cards = [
        _card(identifier=f"DRE-{2300 + n}", minutes_stale=THIRTY_DAYS + n,
              priority=1)
        for n in range(24)
    ]
    comment, advanced = _run(cards)
    assert not advanced.called
    assert not comment.called


# --------------------------------------------------------------------------
# 3: the report — visible, not mobile
# --------------------------------------------------------------------------
def test_every_full_pass_reports_the_count_and_the_oldest(capsys):
    """One line per pass, in the sweep's own run log. A report is a record; the
    move was the gate, and the gate is what this card removes."""
    with _full_sweep(_old_batch(7)):
        pass
    lines = [
        line for line in capsys.readouterr().out.splitlines()
        if line.startswith(f"{reconcile.INTAKE_DEPTH_TAG}:")
    ]
    assert len(lines) == 1, f"expected one Intake line per pass, got {lines}"
    assert "7 cards" in lines[0], lines[0]
    assert "oldest" in lines[0], lines[0]


def test_the_report_names_the_oldest_cards_age_in_days():
    line = reconcile.intake_depth_line(209, 38.4 * 1440)
    assert "\n" not in line, "one line per pass, so a sweep's log stays readable"
    assert line.startswith(f"{reconcile.INTAKE_DEPTH_TAG}: ")
    assert "209 cards" in line
    assert "38.4" in line, "the oldest card's age, in days, is the second number"


def test_an_empty_intake_renders_the_absent_oldest_as_absent():
    """Console-honesty rule 2: 'the query returned nothing' and 'the thing is
    in state X' get visibly different renderings. There is no oldest card in an
    empty lane, and an age of 0.0 days would be an invented one."""
    line = reconcile.intake_depth_line(0, None)
    assert "0 cards" in line
    assert "0.0" not in line, line
    assert "no cards" in line or "none" in line.lower(), line


def test_the_report_still_runs_with_the_hold_set(monkeypatch, capsys):
    """`INTAKE_HOLD` pauses the groomer's drain and nothing else now. A hold
    that silenced the count would take the one thing this phase still does."""
    monkeypatch.setattr(reconcile, "INTAKE_HOLD", "2026-09-10")
    _run(_old_batch(3))
    out = capsys.readouterr().out
    assert f"{reconcile.INTAKE_DEPTH_TAG}:" in out
    assert "3 cards" in out


def test_promote_only_mode_does_not_read_intake(capsys):
    """The event hooks run the dependency gate alone — unchanged by this card."""
    with _full_sweep(_old_batch(3), promote_only=True):
        pass
    assert f"{reconcile.INTAKE_DEPTH_TAG}:" not in capsys.readouterr().out


def test_intake_is_still_a_lane_the_full_sweep_reads():
    """The lane must stay in a set the sweep queries, or the count is a report
    about nothing — the hole DRE-2687 was filed for, rebuilt with a new name."""
    seen: set[str] = set()

    def recorder(states=reconcile.SWEEP_STATES):
        seen.update(states)
        return []

    reconcile._write_failures.clear()
    with contextlib.ExitStack() as stack:
        for m in _main_mocks():
            stack.enter_context(m)
        stack.enter_context(
            mock.patch.object(reconcile, "active_cards", side_effect=recorder)
        )
        reconcile.main()
    assert "Intake" in seen
    assert "Intake" in reconcile.SWEPT_LANES
    assert reconcile.INTAKE_LANE == ("Intake",)


def test_an_unreadable_intake_still_costs_the_sweep_nothing_else(capsys):
    """DRE-2034 is unchanged by this card: an unreadable lane is not an empty
    lane, the rest of the sweep still runs, and the run still exits red."""
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            mock.patch.object(
                reconcile,
                "report_intake_depth",
                side_effect=reconcile.linear_ops.LinearError("Linear is down"),
            )
        )
        mocks = {}
        for m in _main_mocks():
            mocks[m.attribute] = stack.enter_context(m)
        stack.enter_context(
            mock.patch.object(reconcile, "active_cards", return_value=[])
        )
        with pytest.raises(SystemExit):
            reconcile.main()
    assert mocks["promote_ready"].called, "the rest of the sweep must still run"
    assert any("intake" in f for f in reconcile._read_failures)
    reconcile._read_failures.clear()


# --------------------------------------------------------------------------
# 4: the window is gone — from the contract, the code and the registry
# --------------------------------------------------------------------------
def test_the_lane_contract_states_no_age_based_exit_for_intake():
    """THE CRITERION. Intake's exit is the groomer's approved batch, and the
    contract is the file the guard, the sweep and the harness all read."""
    exit_text = lane_contract.lane("Intake")["clauses"]["exit"]["text"].lower()
    for forbidden in ("stall window", "timer", "48", "past the lane"):
        assert forbidden not in exit_text, (
            f"Intake's exit clause still states an age-based exit: {exit_text!r}"
        )
    assert "groomer" in exit_text, (
        "the exit clause must name the one way out — the groomer's batch, "
        "approved by the CEO in Green Light"
    )


def test_intake_carries_no_stall_window_at_all():
    """A window that is not an exit condition is a number nothing reads, and a
    number nothing reads is the next document to drift."""
    assert "Intake" not in lane_contract.stale_minutes()
    assert lane_contract.lane("Intake").get("stale_minutes") is None


def test_the_rendered_contract_is_regenerated_from_the_file():
    """`docs/lane-contract.md` is RENDERED, never written — a stale render is a
    document that contradicts the enforcement it was generated from."""
    rendered = lane_contract.render_markdown()
    assert (ROOT / "docs" / "lane-contract.md").read_text() == rendered, (
        "run: python3 scripts/lane_contract.py render"
    )


def test_the_lane_contract_check_passes():
    """THE CRITERION, run as the CLI the build runs — not as a library call
    with the inputs chosen here, which would prove only what this test asked."""
    import subprocess

    done = subprocess.run(  # nosec B603 — fixed argv, no shell
        [sys.executable, str(ROOT / "scripts" / "lane_contract.py"), "check"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_window_is_not_a_trigger_anywhere_in_the_tree():
    """THE CRITERION, as a guard. `INTAKE_MAX_AGE_MINUTES` was the trigger; the
    name survives nowhere that runs, so it cannot be turned back on by setting
    a variable."""
    offenders = []
    for base in ("scripts", "config", ".github/workflows", "docs"):
        for path in sorted((ROOT / base).rglob("*")):
            if not path.is_file() or path.suffix not in (".py", ".json", ".yml", ".md"):
                continue
            if "INTAKE_MAX_AGE_MINUTES" in path.read_text():
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], offenders


def test_neither_the_window_nor_the_cap_is_an_operator_knob_any_more():
    """`intake_controls` is the ONE reading of the operator's controls on the
    lane, and with the age-out gone there is one control left."""
    assert not hasattr(intake_controls, "max_age_minutes")
    assert not hasattr(intake_controls, "escalation_cap")
    assert not hasattr(intake_controls, "ENV_MAX_AGE")
    assert not hasattr(intake_controls, "ENV_CAP")
    assert intake_controls.ENV_HOLD == "INTAKE_HOLD"


def test_the_act_registry_no_longer_declares_the_age_out():
    """An act is something the pipeline DOES and then announces. Nothing posts
    an `intake-aged` receipt any more, and a registry that kept declaring one
    would be claiming an emission it does not have."""
    acts = json.loads((ROOT / "config" / "pipeline-acts.json").read_text())["acts"]
    tags = {act["tag"] for act in acts}
    names = {act["name"] for act in acts}
    assert AGED_TAG not in tags, AGED_TAG
    assert "intake-overdue" not in names


def test_no_receipt_in_the_tree_carries_the_retired_tag():
    """Guard the guard: the registry and the code are bound to each other, so
    the tag must be absent from both or `pipeline_act.py check` is the thing
    that fails rather than this."""
    assert AGED_TAG not in {row["tag"] for row in pipeline_act.rows()}
    source = (ROOT / "scripts" / "reconcile.py").read_text()
    assert AGED_TAG not in source


# --------------------------------------------------------------------------
# 5: the documents this change contradicts
# --------------------------------------------------------------------------
def test_the_cutover_runbook_no_longer_describes_a_live_age_out():
    """A change that contradicts a document updates that document in the SAME
    PR. The runbook told the operator how to widen a window that no longer
    exists and how to hold a sweep that no longer moves anything."""
    text = (ROOT / "docs" / "backlog-cutover.md").read_text()
    assert "intake_max_age_minutes" not in text
    assert "intake_escalation_cap" not in text
    assert "48 hours of grace" not in text
    assert "DRE-4141" in text, (
        "the runbook must name the card that removed the age-out, or a reader "
        "finds a mechanism described in the past tense with no record of why"
    )


def test_the_reusable_sweep_declares_no_window_or_cap_input():
    """The knobs were `workflow_call` inputs. An input nothing reads is a dial
    wired to nothing, which is worse than no dial."""
    import yaml

    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "reconcile.yml").read_text())
    inputs = ((doc.get(True) or doc.get("on")).get("workflow_call") or {}).get("inputs") or {}
    assert "intake_max_age_minutes" not in inputs
    assert "intake_escalation_cap" not in inputs
    assert "intake_hold" in inputs, (
        "the hold is unchanged by this card — it still pauses the drain"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
