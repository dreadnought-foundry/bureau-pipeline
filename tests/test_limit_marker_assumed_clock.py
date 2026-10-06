"""RED-first tests: a Claude limit death that names no reset gets an assumed
five-hour clock, and a second critic's death is marked a review (DRE-5455).

Since 2026-09-20 the medic has written 66 `🪦 limit-death: kind=claude`
markers on 46 cards, and 65 of them read `reset=unknown`. With no reset and no
account, `limit_recovery.handoff_reason()` hands every one of them to a person
on the next pass, so none of them came back by itself. The epic's own planner
died that way three times (runs 36892741551, 36967942387, 37056534934).

What this file pins about `dead_run.py`:

  1. **The assumed clock.** A Claude wall whose text names no reset is marked
     with `reset = --now + CLAUDE_RESET_ASSUMED_HOURS` and ` assumed=yes` at
     the end of the first line (after `account=` when there is one). A stated
     reset is used as it is and never assumed over. A Linear death is
     unchanged. `limit_reset()` still answers None for a text with no reset —
     `death_cause.py` relies on that.
  2. **The parser.** `parse_limit_marker()` reads `assumed` back as True for
     `yes` and False for every other marker, including the ones already on the
     board.
  3. **The paragraph** names what brings the card back, per case.
  4. **The review stage.** A death in the plan workflow's second-critic review
     re-enters `review`, not `plan`; a dead re-plan still re-enters `plan`.
     The rule is anchored at the start of the step name, and every step it
     matches in the plan workflow runs only in review mode.

Run: cd bureau-pipeline && python3 -m pytest tests/test_limit_marker_assumed_clock.py -v
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import dead_run  # noqa: E402
from test_limit_death_is_its_own_class import SPEND_LIMIT_RECORD  # noqa: E402

PLAN_WORKFLOW = ROOT / ".github" / "workflows" / "plan.yml"

NOW = "2026-10-01T16:33:00Z"
ASSUMED = datetime(2026, 10, 1, 21, 33, tzinfo=UTC)
STATED = datetime(2026, 10, 1, 20, 30, tzinfo=UTC)

# A Claude usage-limit record that names no reset — one of the CLI's own
# sentences for the account wall (DRE-3970).
NO_RESET_LOG = (
    '{"type":"result","subtype":"success","is_error":true,"num_turns":1,'
    '"result":"Claude AI usage limit reached"}'
)
STATED_RESET_LOG = (
    '{"type":"result","is_error":true,"result":"You\'ve hit your limit · '
    'resets 8:30pm (UTC)","num_turns":3}'
)
LINEAR_LOG = (
    "linear_ops.LinearRateLimited: Linear API returned 400 from "
    "https://api.linear.app/graphql: rate limited: 2500 requests/hour exhausted"
)
# The literal first line of this epic's own marker, as it stands on the board.
EPIC_MARKER = "🪦 limit-death: kind=claude stage=plan reset=unknown run=36967942387"


def _decide(tmp_path, capsys, log_text, *extra, workflow="Agent Plan"):
    log = tmp_path / "medic-log.txt"
    log.write_text(log_text, encoding="utf-8")
    rc = dead_run.main([
        "decide", "0", "--is-error", "--limit-log", str(log),
        "--workflow", workflow, "--run-id", "1", "--now", NOW, *extra,
    ])
    out = capsys.readouterr().out
    assert rc == 0
    action, _, body = out.partition("\n\n")
    assert action == "limit"
    return body.rstrip("\n")


# --------------------------------------------------------------------------
# 1. the assumed clock
# --------------------------------------------------------------------------
def test_the_assumed_window_is_five_hours():
    assert dead_run.CLAUDE_RESET_ASSUMED_HOURS == 5


def test_a_claude_wall_naming_no_reset_gets_an_assumed_clock(tmp_path, capsys):
    body = _decide(tmp_path, capsys, NO_RESET_LOG)
    first, paragraph = body.split("\n\n", 1)
    assert first == (
        "🪦 limit-death: kind=claude stage=plan reset=2026-10-01T21:33:00Z "
        "run=1 assumed=yes"
    )
    assert "assumed" in paragraph
    assert "named no reset" in paragraph
    parsed = dead_run.parse_limit_marker(body)
    assert parsed["assumed"] is True
    assert parsed["reset"] == ASSUMED


def test_the_spend_limit_wall_gets_the_same_clock(tmp_path, capsys):
    body = _decide(tmp_path, capsys, SPEND_LIMIT_RECORD)
    assert body.split("\n", 1)[0] == (
        "🪦 limit-death: kind=claude stage=plan reset=2026-10-01T21:33:00Z "
        "run=1 assumed=yes"
    )


def test_the_account_comes_before_the_assumed_field(tmp_path, capsys):
    body = _decide(tmp_path, capsys, SPEND_LIMIT_RECORD, "--account", "main")
    assert body.split("\n", 1)[0] == (
        "🪦 limit-death: kind=claude stage=plan reset=2026-10-01T21:33:00Z "
        "run=1 account=main assumed=yes"
    )
    assert dead_run.parse_limit_marker(body) == {
        "kind": "claude", "stage": "plan", "reset": ASSUMED, "run": "1",
        "account": "main", "assumed": True,
    }


def test_a_stated_reset_is_never_assumed_over(tmp_path, capsys):
    body = _decide(tmp_path, capsys, STATED_RESET_LOG)
    assert body.split("\n", 1)[0] == (
        "🪦 limit-death: kind=claude stage=plan reset=2026-10-01T20:30:00Z run=1"
    )
    assert "assumed" not in body.lower()
    parsed = dead_run.parse_limit_marker(body)
    assert parsed["assumed"] is False
    assert parsed["reset"] == STATED


def test_a_linear_wall_naming_no_reset_stays_unknown(tmp_path, capsys):
    body = _decide(tmp_path, capsys, LINEAR_LOG)
    assert body.split("\n", 1)[0] == (
        "🪦 limit-death: kind=linear stage=plan reset=unknown run=1"
    )
    assert "assumed" not in body.lower()
    assert dead_run.parse_limit_marker(body)["assumed"] is False


def test_limit_reset_still_answers_none_for_a_claude_text_with_no_reset():
    """death_cause.py reads the stated reset through limit_reset and relies on
    None for a text that names none — the assumed clock is the marker's, not
    the reader's."""
    now = datetime(2026, 10, 1, 16, 33, tzinfo=UTC)
    assert dead_run.limit_reset(NO_RESET_LOG, "claude", now) is None
    assert dead_run.limit_reset(SPEND_LIMIT_RECORD, "claude", now) is None


def test_the_limit_death_carries_the_assumption_into_its_marker():
    death = dead_run.LimitDeath(kind="claude", stage="build", reset=ASSUMED,
                                run_id="1", reset_assumed=True)
    assert death.marker().split("\n", 1)[0].endswith(" run=1 assumed=yes")
    assert dead_run.LimitDeath(kind="claude", stage="build", reset=ASSUMED,
                               run_id="1").reset_assumed is False


# --------------------------------------------------------------------------
# 2. the parser
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "body",
    [
        EPIC_MARKER,
        "🪦 limit-death: kind=claude stage=build reset=2026-09-05T20:30:00Z run=7 account=main",
        "🪦 limit-death: kind=linear stage=sync reset=unknown run=36100000001",
        "🪦 limit-death: kind=linear stage=build reset=2026-09-05T20:00:00Z run=9",
    ],
    ids=["this-epic", "claude-with-account", "linear-unknown", "linear-stated"],
)
def test_every_marker_on_the_board_reads_as_not_assumed(body):
    parsed = dead_run.parse_limit_marker(body)
    assert parsed is not None
    assert parsed["assumed"] is False


def test_the_epic_marker_still_parses_as_it_did():
    assert dead_run.parse_limit_marker(EPIC_MARKER) == {
        "kind": "claude", "stage": "plan", "reset": None, "run": "36967942387",
        "account": None, "assumed": False,
    }


def test_only_yes_reads_as_assumed():
    base = "🪦 limit-death: kind=claude stage=plan reset=2026-10-01T21:33:00Z run=1"
    assert dead_run.parse_limit_marker(base + " assumed=yes")["assumed"] is True
    assert dead_run.parse_limit_marker(base + " assumed=no")["assumed"] is False
    with_account = dead_run.parse_limit_marker(base + " account=main assumed=yes")
    assert with_account["account"] == "main"
    assert with_account["assumed"] is True


# --------------------------------------------------------------------------
# 3. the paragraph says what brings the card back
# --------------------------------------------------------------------------
def _paragraph(*args, **kwargs):
    return dead_run.limit_marker(*args, **kwargs).split("\n\n", 1)[1]


def test_paragraph_claude_stated_reset():
    text = _paragraph("claude", "build", STATED, "1")
    assert f"once the window resets (at {dead_run.pacific(STATED)})" in text
    assert "sweep" in text
    assert "assumed" not in text.lower()
    assert "sooner" not in text


def test_paragraph_claude_assumed_reset():
    text = _paragraph("claude", "plan", ASSUMED, "1", reset_assumed=True)
    assert "named no reset" in text
    assert "assumed five hours after the run ended" in text
    assert "one usage window" in text
    assert dead_run.pacific(ASSUMED) in text
    assert "first model call" in text
    assert "marked again" in text
    assert "third death on an assumed clock within a day" in text
    assert "handed to a person" in text


def test_paragraph_linear_with_a_reset():
    text = _paragraph("linear", "sync", STATED, "1")
    assert f"once the window resets (at {dead_run.pacific(STATED)})" in text
    assert "next pass" not in text
    assert "assumed" not in text.lower()


def test_paragraph_linear_without_a_reset():
    text = _paragraph("linear", "sync", None, "1")
    assert "on its next pass" in text
    assert "named no reset" in text
    assert "assumed" not in text.lower()


@pytest.mark.parametrize(
    "kind, reset, assumed",
    [("claude", STATED, False), ("claude", ASSUMED, True),
     ("linear", STATED, False), ("linear", None, False)],
    ids=["claude-stated", "claude-assumed", "linear-stated", "linear-none"],
)
def test_a_recorded_account_brings_the_card_back_sooner(kind, reset, assumed):
    text = _paragraph(kind, "build", reset, "1", "main", reset_assumed=assumed)
    assert "a change of that account brings the card back sooner" in text
    assert "main" in text
    plain = _paragraph(kind, "build", reset, "1", reset_assumed=assumed)
    assert "sooner" not in plain


@pytest.mark.parametrize(
    "kind, reset, assumed",
    [("claude", STATED, False), ("claude", ASSUMED, True),
     ("linear", STATED, False), ("linear", None, False)],
    ids=["claude-stated", "claude-assumed", "linear-stated", "linear-none"],
)
def test_every_paragraph_still_reads_as_a_wait(kind, reset, assumed):
    text = _paragraph(kind, "build", reset, "1", reset_assumed=assumed).lower()
    assert "not a fault" in text
    assert "sweep" in text


def test_a_linear_death_never_carries_an_assumed_field():
    """The assumption is the Claude window's. A Linear marker handed the flag
    by mistake still writes the plain line."""
    body = dead_run.limit_marker("linear", "sync", STATED, "1", reset_assumed=True)
    assert "assumed" not in body.lower()


# --------------------------------------------------------------------------
# 4. the review stage
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "workflow, step, stage",
    [
        ("Agent Plan (reusable)", "Review — the review died", "review"),
        ("Agent Plan", "Second critic — review (before Green Light)", "review"),
        ("Agent Plan", "Second critic — context", "review"),
        ("Agent Plan", "Re-plan after the second critic sent it back", "plan"),
        ("Agent Plan", "Re-plan after the second critic sent it back — finished?", "plan"),
        ("Agent Plan", "Re-plan after the second critic sent it back — out of capacity?", "plan"),
        ("Agent Plan", "Re-mint bot token — second critic", "plan"),
        ("Agent Plan", "Select model — second critic", "plan"),
        ("Agent Plan", "Plan epic", "plan"),
        ("Agent Plan", "Classify the card — one-off, epic or wave", "classify"),
        ("QA Review", "", "review"),
    ],
)
def test_the_second_critic_s_death_is_a_review(workflow, step, stage):
    assert dead_run.limit_stage(workflow, step) == stage


def test_the_review_rule_is_anchored_at_the_start_and_case_blind():
    assert dead_run.limit_stage("Agent Plan", "  SECOND CRITIC — context  ") == "review"
    assert dead_run.limit_stage("Agent Plan", "The review — later") == "plan"
    assert dead_run.limit_stage("Agent Task", "Second critic — context") == "build"


def _plan_steps():
    workflow = yaml.safe_load(PLAN_WORKFLOW.read_text(encoding="utf-8"))
    for job in workflow["jobs"].values():
        for step in job.get("steps") or []:
            if step.get("name"):
                yield step


def test_every_step_marked_a_review_runs_only_in_review_mode():
    """A plan-mode step marked `review` would hand a dead plan to the
    re-review watcher, and nothing would ever plan the epic. So every step the
    rule matches must be gated on the review route."""
    workflow_name = yaml.safe_load(PLAN_WORKFLOW.read_text(encoding="utf-8"))["name"]
    assert workflow_name.lower().startswith("agent plan")
    marked = [s for s in _plan_steps()
              if dead_run.limit_stage(workflow_name, s["name"]) == "review"]
    names = {s["name"] for s in marked}
    assert "Review — the review died" in names
    assert "Second critic — review (before Green Light)" in names
    for step in marked:
        assert "steps.route.outputs.mode == 'review'" in str(step.get("if") or ""), (
            f"{step['name']!r} is marked a review but is not gated on review mode"
        )
