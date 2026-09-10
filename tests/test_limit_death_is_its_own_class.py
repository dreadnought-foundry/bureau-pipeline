"""RED-first tests: a limit death is its own class (DRE-3171).

On 2026-09-05 runs died in two ways that were NOT the card's fault: the
Claude account's usage limit (`is_error: true`, "You've hit your limit ·
resets 8:30pm (UTC)") and Linear's request budget (`LinearRateLimited: …
rate limited: 2500 requests/hour exhausted`, or a read timeout right after a
`transient network fault, retried once`). Planner runs, the classifier, critic
reviews, DRE-3062's build and two merge-syncs all died this way; the medic
retried each once straight back into the same wall, spent an attempt, and then
nothing ever came back to the card until an operator bounced it by hand.

What this file pins about `dead_run.py`:

  1. **The class.** Every signature in the card classifies as a limit death,
     and says which wall it was — `claude` or `linear`. Ordinary death text
     does not. The one token DRE-2923 line-anchors (`RATELIMITED`, which its
     own card body quotes) stays anchored here for the same reason: a quoted
     payload in an agent log is not a limit death.
  2. **The clock.** "resets 8:30pm (UTC)" becomes an ISO timestamp for today —
     or tomorrow, when that time has already passed. Linear's reset comes from
     the `x-ratelimit-requests-reset` epoch when the text carries it, and is
     `unknown` otherwise.
  3. **The stage.** The workflow the medic was woken for names the stage the
     death has to re-enter: plan (or classify), build, fix, review, sync.
  4. **The decision.** A limit death is NOT requeued and NOT held, spends no
     strike against either budget (the marker carries neither tag), writes no
     `model-error:` marker, and wins over every class below it — including a
     prior count at the cap, because the cap is the card's budget and this
     was not the card's fault.
  5. **The marker.** Exactly one comment, whose first line is exactly
     `🪦 limit-death: kind=<claude|linear> stage=<stage> reset=<ISO|unknown>
     run=<run id>` (+ `account=<label>` when known), followed by one
     plain-English paragraph — and the line round-trips through the parser the
     recovery sweep reads it with.
  6. **The turn-cap veto (DRE-3499).** A turn-cap result is never `kind=claude`.
     The classifier reads the WHOLE failed log, and the Claude signatures are
     ordinary English an agent can write about itself: on 2026-09-07 the medic
     stamped `🪦 limit-death: kind=claude stage=plan reset=unknown` on epic
     DRE-3257 for agent-bureau run 34144302622, whose post-approval review ran
     51 turns against a 48-turn ceiling and ended `"subtype": "success"`. The
     log carried `rate_limit_error` because the reviewer had READ the standard
     that quotes it. `limit_recovery.py` reads that marker and would re-enter
     the plan stage when a window it never hit "reset".

Run: cd bureau-pipeline && python3 -m pytest tests/test_limit_death_is_its_own_class.py -v
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import dead_run  # noqa: E402

RUN = "33912345678"
NOON = datetime(2026, 9, 5, 18, 0, tzinfo=UTC)      # 11:00 PT, before the reset
EVENING = datetime(2026, 9, 5, 21, 0, tzinfo=UTC)   # 14:00 PT, after the reset

CLAUDE_LIMIT = (
    '{"type":"result","is_error":true,"result":"You\'ve hit your limit · '
    'resets 8:30pm (UTC)","num_turns":3}'
)
CLAUDE_API_LIMIT = (
    'API Error: 429 {"type":"error","error":{"type":"rate_limit_error",'
    '"message":"This request would exceed your organization\'s rate limit"}}'
)
LINEAR_NAMED = (
    "linear_ops.LinearRateLimited: Linear API returned 400 from "
    "https://api.linear.app/graphql: rate limited: 2500 requests/hour "
    "exhausted — body: '{\"errors\":[{\"message\":\"Only 2500 requests are "
    "allowed per 1 hour\"}]}'"
)
LINEAR_CLASS_ONLY = (
    "Traceback (most recent call last):\n  File \"scripts/linear_ops.py\"\n"
    "linear_ops.LinearRateLimited: Linear API returned 400 from "
    "https://api.linear.app/graphql"
)
LINEAR_CODE_ON_THE_CLIENT_LINE = (
    "Linear API returned 400 from https://api.linear.app/graphql: "
    "'{\"extensions\":{\"code\":\"RATELIMITED\"}}'"
)
LINEAR_READ_TIMEOUT = (
    "transient network fault, retried once: The read operation timed out\n"
    "Traceback (most recent call last):\n"
    "TimeoutError: The read operation timed out"
)
ORDINARY_DEATH = (
    '{"type":"result","is_error":true,"result":"Error: fetch failed",'
    '"num_turns":0}'
)
QUOTED_CARD_BODY = (
    "## Context\n"
    "DRE-2923's own card quotes the payload: `{\"code\":\"RATELIMITED\"}` "
    "and the sweep died on HTTP 400. Fix the client so the log names it.\n"
    "Error: assert 0 == 1"
)

# The 2026-09-07 log, as a fixture (DRE-3499). agent-bureau run 34144302622 is
# not readable from this repo's runner (`gh run view … --log-failed` answers
# HTTP 404 for the token the build holds), so the file is SYNTHESISED from the
# fields the epic records: `--max-turns 48`, `"num_turns": 51`,
# `"subtype": "success"`, `"is_error": false`, and `rate_limit_error` quoted
# inside standards prose the reviewer had read.
TURN_CAP_OVER_CEILING = (
    Path(__file__).resolve().parent / "fixtures"
    / "medic-turn-cap-over-ceiling-log.txt"
).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 1. the class
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, kind",
    [
        (CLAUDE_LIMIT, "claude"),
        (CLAUDE_API_LIMIT, "claude"),
        (LINEAR_NAMED, "linear"),
        (LINEAR_CLASS_ONLY, "linear"),
        (LINEAR_CODE_ON_THE_CLIENT_LINE, "linear"),
        (LINEAR_READ_TIMEOUT, "linear"),
    ],
    ids=[
        "hit-your-limit", "rate_limit_error", "rate-limited-2500",
        "LinearRateLimited", "RATELIMITED-on-the-client-line", "read-timeout-pair",
    ],
)
def test_every_signature_classifies_as_a_limit_and_names_the_wall(text, kind):
    assert dead_run.limit_kind(text) == kind


def test_the_signatures_are_one_module_constant():
    """The card names the list; the list lives ONCE, as data, so the next
    signature is a one-line addition and every reader agrees on it."""
    for expected in (
        "hit your limit",
        "rate_limit_error",
        "rate limited: 2500 requests/hour exhausted",
        "LinearRateLimited",
        "RATELIMITED",
    ):
        assert expected in dead_run.LIMIT_SIGNATURES
    assert (
        "transient network fault, retried once",
        "The read operation timed out",
    ) in dead_run.LIMIT_SIGNATURE_PAIRS


@pytest.mark.parametrize("text", ["", ORDINARY_DEATH, "Error: fetch failed"])
def test_ordinary_death_text_is_not_a_limit(text):
    assert dead_run.limit_kind(text) is None


def test_a_quoted_ratelimited_payload_in_prose_does_not_classify():
    """DRE-2923's own card body quotes `RATELIMITED`, and an agent log echoes
    card text. A bare substring match would turn every real death on that card
    into a limit death — no strike, no requeue — so the bare code counts only
    on a line that also names Linear's client or its host."""
    assert dead_run.limit_kind(QUOTED_CARD_BODY) is None


def test_the_read_timeout_counts_only_as_the_pair_in_order():
    """A read timeout on its own is a network fault (DRE-3087's one retry). It
    is a Linear limit only when it FOLLOWS the retry line — the shape seen on
    2026-09-05 when the quota was gone and the socket never answered."""
    assert dead_run.limit_kind("TimeoutError: The read operation timed out") is None
    reversed_pair = (
        "TimeoutError: The read operation timed out\n"
        "transient network fault, retried once: ConnectionResetError"
    )
    assert dead_run.limit_kind(reversed_pair) is None


# --------------------------------------------------------------------------
# 2. the clock
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "now, expected",
    [
        (NOON, datetime(2026, 9, 5, 20, 30, tzinfo=UTC)),
        (EVENING, datetime(2026, 9, 6, 20, 30, tzinfo=UTC)),
    ],
    ids=["still-today", "past-today-so-tomorrow"],
)
def test_claude_reset_is_today_or_tomorrow(now, expected):
    assert dead_run.limit_reset(CLAUDE_LIMIT, "claude", now) == expected


def test_claude_reset_without_minutes():
    assert dead_run.limit_reset(
        "You've hit your limit · resets 8pm (UTC)", "claude", NOON
    ) == datetime(2026, 9, 5, 20, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "header, expected",
    [
        ("x-ratelimit-requests-reset: 1788638400000",
         datetime(2026, 9, 5, 20, 0, tzinfo=UTC)),
        ("x-ratelimit-requests-reset=1788638400",
         datetime(2026, 9, 5, 20, 0, tzinfo=UTC)),
    ],
    ids=["epoch-ms", "epoch-s"],
)
def test_linear_reset_reads_the_header_epoch(header, expected):
    assert dead_run.limit_reset(LINEAR_NAMED + "\n" + header, "linear", NOON) == expected


@pytest.mark.parametrize(
    "text, kind",
    [(LINEAR_NAMED, "linear"), (CLAUDE_API_LIMIT, "claude"), ("", "claude")],
)
def test_reset_is_unknown_when_the_text_carries_none(text, kind):
    assert dead_run.limit_reset(text, kind, NOON) is None


# --------------------------------------------------------------------------
# 3. the stage
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "workflow, failed_step, stage",
    [
        ("Agent Plan (reusable)", "", "plan"),
        ("Agent Plan", "Classify the card", "classify"),
        ("Agent Plan (reusable)", "Run the classifier", "classify"),
        ("Agent Task (reusable)", "Card → In Progress", "build"),
        ("Agent Task", "", "build"),
        ("Agent Fix (reusable)", "", "fix"),
        ("QA Review (reusable)", "", "review"),
        ("QA Review", "", "review"),
        ("Linear Sync (reusable)", "", "sync"),
        ("Reconcile (reusable)", "", None),
        ("", "", None),
    ],
)
def test_the_workflow_names_the_stage(workflow, failed_step, stage):
    assert dead_run.limit_stage(workflow, failed_step) == stage


# --------------------------------------------------------------------------
# 4. the decision
# --------------------------------------------------------------------------
def _death(**overrides):
    fields = dict(kind="claude", stage="build",
                  reset=datetime(2026, 9, 5, 20, 30, tzinfo=UTC), run_id=RUN)
    fields.update(overrides)
    return dead_run.LimitDeath(**fields)


def test_a_limit_death_is_not_requeued_and_not_held():
    d = dead_run.decide(0, limit=_death())
    assert d.action == "limit"
    assert len(d.comments) == 1


def test_a_limit_death_wins_over_every_other_class_and_the_cap():
    """A limit-hit run also ends `is_error: true`, DRE-3062's died at `Card →
    In Progress` (pre-agent, rate-limited), and a card at the cap is still not
    at fault. Every one of those inputs together must still read `limit`."""
    d = dead_run.decide(
        dead_run.REQUEUE_CAP + 3,
        is_error=True, error_model="claude-opus-5",
        pre_agent=True, failed_step="Card → In Progress", rate_limited=True,
        credential_expiry=True,
        limit=_death(kind="linear"),
    )
    assert d.action == "limit"
    assert dead_run.ERROR_MARKER_PREFIX not in d.comments[0]


def test_a_cancelled_run_still_defers_it_was_killed_not_limited():
    """DRE-2074: cancellation is the fuller account of the run — the agent was
    killed while still working — so it keeps winning."""
    assert dead_run.decide(0, cancelled=True, limit=_death()).action == "defer"


def test_the_marker_spends_neither_budget_and_names_no_model():
    body = dead_run.decide(0, limit=_death()).comments[0]
    for tag in (dead_run.DEAD_TAG, dead_run.TURN_TAG, dead_run.RESET_TAG):
        assert tag not in body, f"the marker must not contain {tag!r}"
    assert dead_run.count_of([body], dead_run.DEAD_TAG) == 0
    assert dead_run.count_of([body], dead_run.TURN_TAG) == 0
    assert dead_run.ERROR_MARKER_PREFIX not in body


def test_the_limit_tag_and_the_budget_tags_do_not_contain_each_other():
    """Counting is substring-based (linear_ops.count_comments), so the tags
    must be mutually non-overlapping — the RESET_TAG discipline, again."""
    for tag in (dead_run.DEAD_TAG, dead_run.TURN_TAG, dead_run.RESET_TAG):
        assert tag not in dead_run.LIMIT_TAG
        assert dead_run.LIMIT_TAG not in tag


# --------------------------------------------------------------------------
# 5. the marker
# --------------------------------------------------------------------------
def test_the_marker_first_line_is_exact():
    body = dead_run.decide(0, limit=_death()).comments[0]
    first, rest = body.split("\n", 1)
    assert first == (
        f"🪦 limit-death: kind=claude stage=build reset=2026-09-05T20:30:00Z run={RUN}"
    )
    assert rest.strip(), "one plain-English paragraph follows the marker line"


def test_the_marker_carries_the_account_when_known():
    body = dead_run.decide(0, limit=_death(account="main")).comments[0]
    assert body.split("\n", 1)[0] == (
        f"🪦 limit-death: kind=claude stage=build reset=2026-09-05T20:30:00Z "
        f"run={RUN} account=main"
    )


def test_the_marker_says_unknown_when_the_reset_is_unknown():
    body = dead_run.decide(0, limit=_death(kind="linear", stage="sync", reset=None)).comments[0]
    assert body.split("\n", 1)[0] == (
        f"🪦 limit-death: kind=linear stage=sync reset=unknown run={RUN}"
    )


def test_the_marker_round_trips_through_the_parser():
    body = dead_run.decide(0, limit=_death(account="main")).comments[0]
    parsed = dead_run.parse_limit_marker(body)
    assert parsed == {
        "kind": "claude",
        "stage": "build",
        "reset": datetime(2026, 9, 5, 20, 30, tzinfo=UTC),
        "run": RUN,
        "account": "main",
    }
    unknown = dead_run.decide(0, limit=_death(reset=None)).comments[0]
    assert dead_run.parse_limit_marker(unknown)["reset"] is None
    assert dead_run.parse_limit_marker(unknown)["account"] is None
    assert dead_run.parse_limit_marker("🪦 dead-run-requeue: agent died") is None
    assert dead_run.parse_limit_marker("") is None


def test_the_paragraph_reads_as_a_wait_not_a_fault():
    body = dead_run.decide(0, limit=_death()).comments[0]
    lowered = body.lower()
    assert "died" not in lowered.split("\n", 1)[0]
    assert "not this card" in lowered or "not a fault" in lowered
    assert "sweep" in lowered  # names who brings it back


# --------------------------------------------------------------------------
# 6. the turn-cap veto (DRE-3499)
# --------------------------------------------------------------------------
CEILING_LINE = "claude_args: --max-turns 48 --model claude-opus-5"

SUBTYPE_JSON = (
    '{"type":"result","subtype":"error_max_turns","is_error":true,'
    '"num_turns":48}'
)
SUBTYPE_DETAIL_LINE = (
    "agent result gate: what the agent itself reported (from the execution file):\n"
    "  subtype: error_max_turns\n"
    "  num_turns: 48\n"
)
CAP_SENTENCE = (
    "agent result gate: what the agent itself reported (from the execution file):\n"
    "  result: Reached maximum number of turns (48)\n"
)
OVER_THE_CEILING = f'{CEILING_LINE}\n{{"num_turns": 51,"is_error":false}}\n'
UNDER_THE_CEILING = f'{CEILING_LINE}\n{{"num_turns": 30,"is_error":false}}\n'


@pytest.mark.parametrize(
    "text",
    [SUBTYPE_JSON, SUBTYPE_DETAIL_LINE, CAP_SENTENCE, OVER_THE_CEILING],
    ids=["subtype-json", "subtype-failure-detail-line", "cap-sentence",
         "num_turns-at-or-over-the-ceiling"],
)
def test_the_action_s_own_turn_cap_evidence_is_recognised(text):
    """The four shapes a failed log carries the ceiling in: the JSON field,
    the line `execution_result.print_failure_detail` writes, the sentence
    `check_agent_result._TURN_CAP_TEXT` already recognises, and a result
    record that spent at least as many turns as the log says it was given."""
    assert dead_run.turn_cap_in_text(text) is True


@pytest.mark.parametrize(
    "text",
    [UNDER_THE_CEILING, ORDINARY_DEATH, CLAUDE_LIMIT, LINEAR_NAMED, ""],
    ids=["num_turns-under-the-ceiling", "ordinary-death", "claude-limit",
         "linear-limit", "empty"],
)
def test_a_log_without_turn_cap_evidence_is_not_vetoed(text):
    assert dead_run.turn_cap_in_text(text) is False


@pytest.mark.parametrize(
    "ceiling",
    ["maxTurns: 48", '"max_turns": 48', "--max-turns 48"],
    ids=["maxTurns", "max_turns-json", "--max-turns"],
)
def test_the_ceiling_is_read_in_every_spelling_the_log_uses(ceiling):
    assert dead_run.turn_cap_in_text(f'{ceiling}\n"num_turns": 51') is True
    assert dead_run.turn_cap_in_text(f'{ceiling}\n"num_turns": 30') is False


def test_a_turn_cap_result_is_never_a_claude_limit_death():
    """The 2026-09-07 log itself: 51 turns against 48, `"subtype": "success"`,
    and `rate_limit_error` in the text because the reviewer READ the standard
    that quotes it. The medic stamped kind=claude on epic DRE-3257 for it."""
    assert "rate_limit_error" in TURN_CAP_OVER_CEILING
    assert dead_run.turn_cap_in_text(TURN_CAP_OVER_CEILING) is True
    assert dead_run.limit_kind(TURN_CAP_OVER_CEILING) is None


def test_the_veto_does_not_disarm_the_claude_signatures_generally():
    """A real Claude wall still classifies — the veto is about the turn cap,
    not about the words."""
    assert dead_run.limit_kind(CLAUDE_LIMIT) == "claude"
    assert dead_run.limit_kind(CLAUDE_API_LIMIT) == "claude"


@pytest.mark.parametrize(
    "text",
    [LINEAR_NAMED, LINEAR_CLASS_ONLY, LINEAR_CODE_ON_THE_CLIENT_LINE,
     LINEAR_READ_TIMEOUT],
    ids=["rate-limited-2500", "LinearRateLimited",
         "RATELIMITED-on-the-client-line", "read-timeout-pair"],
)
def test_a_linear_wall_survives_the_veto(text):
    """A different vendor: the Linear client's refusal is not something a turn
    count can explain away, so a log carrying BOTH still reads `linear`."""
    assert dead_run.limit_kind(text) == "linear"
    assert dead_run.limit_kind(f"{text}\n{SUBTYPE_JSON}\n") == "linear"
    assert dead_run.limit_kind(f"{text}\n{OVER_THE_CEILING}") == "linear"


def test_the_signature_list_keeps_every_member():
    """The veto is a new gate in front of the list, not a deletion from it —
    nothing is removed by DRE-3499."""
    assert dead_run.LIMIT_SIGNATURES == (
        "hit your limit",
        "rate_limit_error",
        "rate limited: 2500 requests/hour exhausted",
        "LinearRateLimited",
        "RATELIMITED",
    )


def test_the_veto_reads_the_turn_cap_evidence_from_one_place():
    """`check_agent_result` already owns what the action's turn ceiling looks
    like; a second spelling here is how the two readers drift apart."""
    import check_agent_result  # noqa: PLC0415 — the import IS the assertion

    assert dead_run.turn_cap_in_text(
        f"  result: Reached {check_agent_result._TURN_CAP_TEXT} (48)"
    ) is True
    for subtype in check_agent_result._TURN_CAP_SUBTYPES:
        assert dead_run.turn_cap_in_text(f'"subtype": "{subtype}"') is True


# --------------------------------------------------------------------------
# the CLI: the workflow hands the log over and reads the action back
# --------------------------------------------------------------------------
def test_cli_decide_classifies_the_limit_log(tmp_path, capsys):
    log = tmp_path / "medic-log.txt"
    log.write_text(CLAUDE_LIMIT)
    rc = dead_run.main([
        "decide", "1", "--is-error", "--error-model", "claude-opus-5",
        "--limit-log", str(log), "--workflow", "Agent Task (reusable)",
        "--run-id", RUN, "--account", "main", "--now", "2026-09-05T18:00:00Z",
    ])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert out[0] == "limit"
    assert out[2] == (
        f"🪦 limit-death: kind=claude stage=build reset=2026-09-05T20:30:00Z "
        f"run={RUN} account=main"
    )


def test_cli_decide_on_the_turn_cap_log_is_the_ordinary_decision(tmp_path, capsys):
    """The exact call the medic makes for run 34144302622, on the fixture:
    the action is the ordinary non-limit one this log got before DRE-3171
    existed, and nothing prints a limit marker."""
    log = tmp_path / "medic-log.txt"
    log.write_text(TURN_CAP_OVER_CEILING, encoding="utf-8")
    rc = dead_run.main([
        "decide", "0", "--limit-log", str(log),
        "--workflow", "Agent Plan (reusable)", "--run-id", "34144302622",
        "--now", "2026-09-07T16:50:00Z",
    ])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert out[0] != "limit"
    assert out[0] == "requeue"
    assert dead_run.LIMIT_MARK not in "\n".join(out)


def test_cli_decide_without_a_limit_in_the_log_is_unchanged(tmp_path, capsys):
    log = tmp_path / "medic-log.txt"
    log.write_text(ORDINARY_DEATH)
    rc = dead_run.main([
        "decide", "0", "--is-error", "--error-model", "claude-opus-5",
        "--limit-log", str(log), "--workflow", "Agent Task (reusable)",
        "--run-id", RUN,
    ])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert out[0] == "requeue"
    assert dead_run.DEAD_TAG in out[2]
