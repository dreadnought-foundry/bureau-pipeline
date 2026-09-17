"""RED-first tests: one reader says WHICH wall a run hit (DRE-4129).

A run that dies on the Claude account's wall ends `is_error: true`, one turn,
$0 — and that is the same corpse shape for four different things: the account
being rate-limited, the account being out of usage, the credential having been
revoked, and the service being briefly unreachable. All four are reported
today as "an API/model death", and all four arm a model swap that cannot help.

The explanation was never missing. `anthropics/claude-code-action@v1` writes
the raw result record to the execution output file and
`scripts/execution_result.py` already lifts a whitelist of diagnostic fields
out of it. `scripts/death_cause.py` is the one reader of those fields.

What this file pins:

  1. **The contract.** Three answer strings — `throttled`, `capped`,
     `revoked` — and `None` for everything else. Four real records, one per
     answer.
  2. **The whitelist.** `cause()` reads ONLY the fields
     `execution_result._DIAGNOSTIC_FIELDS` names, never the transcript. A
     record whose assistant turn says "hit your limit" while its result fields
     say nothing answers `None` — and the same record with those words in
     `result` answers `capped`, so the test is not passing on the absence of a
     reader.
  3. **The turn-cap veto, FIRST and POSITIVE.** The same evidence
     `dead_run.turn_cap_in_text` already reads (DRE-3499): the Claude
     signatures are ordinary English an agent writes after merely reading the
     standard that quotes them.
  4. **`overloaded_error` is not a wall.** A 529 is the service being busy: it
     clears by itself in seconds and names no reset, which is why
     `dead_run._CAPACITY_NOT_A_LIMIT_DEATH` already refuses to treat it as one.
  5. **The clock.** `reset()` — the retry-after window for `throttled`, the
     stated reset for `capped` (resolved exactly as `dead_run.limit_reset`
     resolves it), `None` for `revoked`.
  6. **The quote.** Capped at `execution_result._VALUE_CAP`, read from that
     constant, and never transcript text.

Every one of the four cause tests carries a positive assertion, so a `cause()`
edited to always return `None` fails all four rather than three.

Run: cd bureau-pipeline && python3 -m pytest tests/test_death_cause.py -v
"""

from __future__ import annotations

import ast
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dead_run  # noqa: E402 — the turn-cap veto and the reset clock
import death_cause  # noqa: E402
import execution_result  # noqa: E402 — the whitelist and the value cap
import model_fallback  # noqa: E402 — the capacity list `capped` is read off

# 11:00 PT and 14:00 PT on 2026-09-05 — before and after a 20:30 UTC reset,
# the same two clocks tests/test_limit_death_is_its_own_class.py uses.
NOON = datetime(2026, 9, 5, 18, 0, tzinfo=UTC)
EVENING = datetime(2026, 9, 5, 21, 0, tzinfo=UTC)


# --------------------------------------------------------------------------
# The three real shapes the RED commit replays, plus the two that answer None.
# --------------------------------------------------------------------------

# portico run 34924370626, 2026-09-12/13/14: `claude-fable-5-1` refused every
# planning call. One turn, no spend, and the whole explanation in `result`.
FABLE_SPEND_REFUSAL = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "num_turns": 1,
    "total_cost_usd": 0,
    "duration_ms": 842,
    "result": (
        "You've hit your monthly spend limit. Switch to another model to "
        "continue."
    ),
}

# The account's rate limit: a 429 whose body names the window and whose error
# record carries the `retry-after` the wall clears in.
ACCOUNT_RATE_LIMIT = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "num_turns": 1,
    "total_cost_usd": 0,
    "duration_ms": 613,
    "api_error_status": 429,
    "result": (
        'API Error: 429 {"type":"error","error":{"type":"rate_limit_error",'
        '"message":"This request would exceed your account\'s rate limit. '
        'Please try again later."}}'
    ),
    "errors": [{"status": 429, "headers": {"retry-after": "60"}}],
}

# The credential is dead. Nothing resets it; a new credential is the only
# remedy — which is why it must never be reported as the wall above.
CREDENTIAL_REVOKED = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "num_turns": 1,
    "total_cost_usd": 0,
    "duration_ms": 405,
    "api_error_status": 401,
    "result": (
        'API Error: 401 {"type":"error","error":{"type":'
        '"authentication_error","message":"OAuth token has been revoked"}}'
    ),
}

# A 529. The service is busy, it clears by itself in seconds, it names no
# reset — not a wall, and `dead_run` already refuses to call it one.
SERVICE_OVERLOADED = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "num_turns": 1,
    "total_cost_usd": 0,
    "api_error_status": 529,
    "result": (
        'API Error: 529 {"type":"error","error":{"type":"overloaded_error",'
        '"message":"Overloaded"}}'
    ),
}

# The DRE-3499 shape: a run that did real work, hit the ceiling, and whose own
# closing message quotes the standard it had read.
TURN_CAP_QUOTING_THE_STANDARD = {
    "type": "result",
    "subtype": "error_max_turns",
    "is_error": True,
    "num_turns": 48,
    "total_cost_usd": 3.11,
    "duration_ms": 2_160_000,
    "result": (
        "Reached maximum number of turns (48). I was reading the standard "
        "that quotes `429 rate_limit_error` and \"You've hit your monthly "
        "spend limit\" when I ran out of steps."
    ),
}

# The same run, one field different: no ceiling, and the words are the
# provider's rather than the agent's. The control for the veto.
SPEND_REFUSAL_WITHOUT_THE_CEILING = dict(
    TURN_CAP_QUOTING_THE_STANDARD,
    subtype="error_during_execution",
    result="You've hit your monthly spend limit. Switch to another model to continue.",
)

# The transcript says the words; the result fields say nothing. `error` and
# `env` are on the record and are NOT on the whitelist — `error` is what
# check_agent_result reads and execution_result deliberately does not.
TRANSCRIPT_SAYS_IT = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "num_turns": 9,
    "total_cost_usd": 1.23,
    "error": "429 rate_limit_error",
    "env": {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-REDACTED"},
    "messages": [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Reading standards/engineering.md: a 429 "
                        "rate_limit_error means you've hit your limit, and "
                        "you've hit your monthly spend limit is the other one."
                    ),
                }
            ],
        }
    ],
}


# --------------------------------------------------------------------------
# 1. the contract
# --------------------------------------------------------------------------
def test_the_three_answer_strings_are_the_contract():
    """Four sibling cards cite these; they are declared once, here."""
    assert death_cause.THROTTLED == "throttled"
    assert death_cause.CAPPED == "capped"
    assert death_cause.REVOKED == "revoked"


@pytest.mark.parametrize(
    "record, answer",
    [
        (ACCOUNT_RATE_LIMIT, "throttled"),
        (FABLE_SPEND_REFUSAL, "capped"),
        (CREDENTIAL_REVOKED, "revoked"),
        (SERVICE_OVERLOADED, None),
    ],
    ids=["account-429", "fable-monthly-spend", "401-authentication", "529-overloaded"],
)
def test_one_real_record_for_each_of_the_four_answers(record, answer):
    assert death_cause.cause(record) == answer


@pytest.mark.parametrize("record", [None, {}, [], "not a record", {"is_error": True}])
def test_a_record_the_module_cannot_read_answers_none(record):
    """`None` leaves every caller behaving exactly as it does today, so an
    unreadable record is the safe answer rather than a guess."""
    assert death_cause.cause(record) is None
    assert death_cause.quote(record) == ""
    assert death_cause.reset(record, NOON) is None


# --------------------------------------------------------------------------
# 2. the whitelist — the result record only, never the transcript
# --------------------------------------------------------------------------
def test_the_transcript_is_never_read():
    """An assistant turn saying "hit your limit" in a record whose result
    fields say nothing answers None — and the control proves it is the
    whitelist doing that, not a reader that answers None to everything."""
    assert death_cause.cause(TRANSCRIPT_SAYS_IT) is None
    assert death_cause.quote(TRANSCRIPT_SAYS_IT) == ""
    spoken_by_the_provider = dict(
        TRANSCRIPT_SAYS_IT,
        result="You've hit your monthly spend limit. Switch to another model to continue.",
    )
    assert death_cause.cause(spoken_by_the_provider) == death_cause.CAPPED


def test_the_transcript_is_never_read_through_the_real_loader(tmp_path):
    """The same thing one seam out: the execution file as the action writes
    it, loaded by `execution_result.load_execution`, is what callers hold."""
    path = tmp_path / "claude-execution-output.json"
    path.write_text(
        json.dumps(
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "you've hit your limit"}
                        ]
                    },
                },
                TRANSCRIPT_SAYS_IT,
            ]
        ),
        encoding="utf-8",
    )
    record = execution_result.load_execution(str(path))
    assert record is not None
    assert death_cause.cause(record) is None


def test_every_field_the_module_reads_is_on_the_whitelist():
    """Read off the SOURCE, not off behaviour: a field read by name anywhere
    in the module is a field that can carry transcript or environment
    content, and the whitelist is the one list that has been audited for
    that (`execution_result._DIAGNOSTIC_FIELDS`)."""
    tree = ast.parse((SCRIPTS / "death_cause.py").read_text(encoding="utf-8"))
    named = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            named.add(node.args[0].value)
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            named.add(node.slice.value)
    outside = named - set(execution_result._DIAGNOSTIC_FIELDS)
    assert not outside, f"death_cause reads fields off the whitelist: {sorted(outside)}"


def test_the_fields_it_names_are_the_whitelist_itself():
    """The status field and the body fields are members of the shared
    whitelist, not a second list that can drift out of it."""
    assert death_cause._STATUS_FIELD in execution_result._DIAGNOSTIC_FIELDS
    for field in death_cause._BODY_FIELDS:
        assert field in execution_result._DIAGNOSTIC_FIELDS


# --------------------------------------------------------------------------
# 3. the turn-cap veto — checked FIRST, and positively
# --------------------------------------------------------------------------
def test_a_turn_cap_answers_none_whatever_words_its_text_carries():
    """DRE-3499, one module over: a run that hit the ceiling did not hit the
    account's wall, and the Claude signatures are words an agent writes about
    itself after reading the standard that quotes them."""
    assert death_cause.cause(TURN_CAP_QUOTING_THE_STANDARD) is None
    # The control: the SAME record without the ceiling answers positively, so
    # the veto is what produced the None above.
    assert death_cause.cause(SPEND_REFUSAL_WITHOUT_THE_CEILING) == death_cause.CAPPED


def test_the_veto_reads_the_evidence_dead_run_already_reads():
    """Not an inference from absence: `dead_run.turn_cap_in_text` says yes to
    this record's own diagnostic text, and that is the same answer the veto
    acts on."""
    text = death_cause._diagnostic_text(TURN_CAP_QUOTING_THE_STANDARD)
    assert dead_run.turn_cap_in_text(text) is True
    assert dead_run.turn_cap_in_text(
        death_cause._diagnostic_text(SPEND_REFUSAL_WITHOUT_THE_CEILING)
    ) is False


@pytest.mark.parametrize(
    "record",
    [
        {"subtype": "error_max_turns", "result": "You've hit your monthly spend limit."},
        {
            "subtype": "error_during_execution",
            "stop_reason": "max_turns",
            "result": "You've hit your monthly spend limit.",
        },
        {
            "subtype": "error_during_execution",
            "terminal_reason": "max_turns",
            "result": "rate_limit_error",
            "api_error_status": 429,
        },
    ],
    ids=["subtype", "stop_reason", "terminal_reason"],
)
def test_every_turn_cap_field_the_action_sets_vetoes(record):
    """The observed payloads do not all set the same field (portico PR #170,
    agent-bureau run 32791846359), so every one the action is known to set is
    evidence here."""
    assert death_cause.cause(record) is None


# --------------------------------------------------------------------------
# 4. overloaded_error is not a wall
# --------------------------------------------------------------------------
def test_a_record_carrying_only_overloaded_error_answers_none():
    only_overloaded = {"subtype": "error_during_execution", "result": "overloaded_error"}
    assert death_cause.cause(only_overloaded) is None
    # Control: the same record with a real capacity signature answers capped.
    assert (
        death_cause.cause(dict(only_overloaded, result="out of usage credits"))
        == death_cause.CAPPED
    )


def test_capped_is_the_capacity_list_where_it_lives():
    """`capped` is `model_fallback.CAPACITY_SIGNATURES` minus the rate limit
    (which is `throttled`) and minus the transient overload — derived, never
    retyped, so a new vendor sentence is one edit in one file."""
    assert death_cause._CAPPED_SIGNATURES == tuple(
        sig
        for sig in model_fallback.CAPACITY_SIGNATURES
        if sig not in ("rate_limit_error",) + dead_run._CAPACITY_NOT_A_LIMIT_DEATH
    )
    assert "overloaded_error" not in death_cause._CAPPED_SIGNATURES
    assert "rate_limit_error" not in death_cause._CAPPED_SIGNATURES
    assert "monthly spend limit" in death_cause._CAPPED_SIGNATURES


@pytest.mark.parametrize("signature", ["monthly spend limit", "out of usage credits",
                                       "usage limit reached", "fable limit"])
def test_each_capacity_sentence_answers_capped(signature):
    assert (
        death_cause.cause({"subtype": "error_during_execution", "result": signature})
        == death_cause.CAPPED
    )


def test_an_account_out_of_usage_reads_capped_even_when_the_status_is_429():
    """The subscription's usage wall arrives AS a 429. Reported as
    `throttled` it would say "clears in minutes" about a window that resets
    tonight, so the spend/usage sentences are read before the status."""
    usage_wall = {
        "subtype": "error_during_execution",
        "api_error_status": 429,
        "result": "You've hit your limit · resets 8:30pm (UTC)",
    }
    assert death_cause.cause(usage_wall) == death_cause.CAPPED


# --------------------------------------------------------------------------
# 5. the clock
# --------------------------------------------------------------------------
def test_throttled_resets_after_the_retry_after_window():
    assert death_cause.reset(ACCOUNT_RATE_LIMIT, NOON) == NOON + timedelta(seconds=60)


def test_throttled_with_no_retry_after_says_it_does_not_know():
    """A window nobody stated is unknown, not zero — the honest answer is the
    one `dead_run.limit_reset` already gives for a reset a text omits."""
    silent = dict(ACCOUNT_RATE_LIMIT)
    silent.pop("errors")
    assert death_cause.cause(silent) == death_cause.THROTTLED
    assert death_cause.reset(silent, NOON) is None


@pytest.mark.parametrize(
    "now, expected",
    [
        (NOON, datetime(2026, 9, 5, 20, 30, tzinfo=UTC)),
        (EVENING, datetime(2026, 9, 6, 20, 30, tzinfo=UTC)),
    ],
    ids=["still-today", "past-today-so-tomorrow"],
)
def test_capped_resets_when_the_record_says_it_does(now, expected):
    """`resets 8:30pm (UTC)` resolves the way dead_run.limit_reset resolves
    it — today, or tomorrow when today's has passed."""
    capped = {
        "subtype": "error_during_execution",
        "result": "You've hit your limit · resets 8:30pm (UTC)",
    }
    assert death_cause.reset(capped, now) == expected
    assert death_cause.reset(capped, now) == dead_run.limit_reset(
        death_cause._diagnostic_text(capped), "claude", now
    )


def test_capped_with_no_stated_reset_is_unknown():
    assert death_cause.cause(FABLE_SPEND_REFUSAL) == death_cause.CAPPED
    assert death_cause.reset(FABLE_SPEND_REFUSAL, NOON) is None


def test_nothing_resets_a_revoked_credential():
    """A new credential is the only remedy, so there is no window to wait
    for — and a sweep handed one would wait forever."""
    assert death_cause.cause(CREDENTIAL_REVOKED) == death_cause.REVOKED
    assert death_cause.reset(CREDENTIAL_REVOKED, NOON) is None


def test_a_naive_clock_is_read_as_utc():
    """Callers pass `datetime.now(UTC)`; a naive one must not be read as the
    runner's local time — that is how a reset lands hours out."""
    naive = NOON.replace(tzinfo=None)
    assert death_cause.reset(ACCOUNT_RATE_LIMIT, naive) == NOON + timedelta(seconds=60)


# --------------------------------------------------------------------------
# 6. the quote
# --------------------------------------------------------------------------
def test_quote_is_the_error_body():
    assert "monthly spend limit" in death_cause.quote(FABLE_SPEND_REFUSAL)
    assert "rate_limit_error" in death_cause.quote(ACCOUNT_RATE_LIMIT)


def test_quote_caps_at_the_value_cap_the_printer_already_uses():
    """A provider death can hand back a page of HTML; the cap is
    `execution_result._VALUE_CAP`, read from that constant."""
    huge = {
        "subtype": "error_during_execution",
        "result": "You've hit your monthly spend limit. " + "x" * 4000,
    }
    quoted = death_cause.quote(huge)
    assert len(quoted) == execution_result._VALUE_CAP
    assert quoted == huge["result"][: execution_result._VALUE_CAP]


def test_quote_never_returns_transcript_text():
    assert death_cause.quote(TRANSCRIPT_SAYS_IT) == ""
    assert death_cause.quote(None) == ""


# --------------------------------------------------------------------------
# 7. the module's own shape — pure, stdlib, no I/O
# --------------------------------------------------------------------------
def test_the_module_does_its_own_no_io():
    """`death_cause` reads a record a caller already loaded — it opens no
    file and reaches no network, the same house style dead_run.py and
    model_fallback.py are written in."""
    tree = ast.parse((SCRIPTS / "death_cause.py").read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "open" not in called
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not {"requests", "urllib.request", "subprocess"} & imported
