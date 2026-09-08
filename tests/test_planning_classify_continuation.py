"""The Claude Code seam reads EVERY chunk of a continued answer (DRE-3331).

The groomer's first real proposal (run 34185093277, 2026-09-07) ranked exactly
the LAST 56 rows of a 260-row census and reported no cut. The cause, captured
from the installed Claude Code 2.1.263 on 2026-09-07:

    printf 'Write the numbers 1 to 90, one per line, as "N | x". ...' |
      CLAUDE_CODE_MAX_OUTPUT_TOKENS=300 claude -p --max-turns 1 \\
        --model claude-haiku-4-5-20251001 --allowedTools "" \\
        --output-format stream-json --verbose

When a response runs past `CLAUDE_CODE_MAX_OUTPUT_TOKENS` the CLI does not
stop: it CONTINUES the same turn in a fresh API request, up to three times,
and each continuation is its own assistant message that restarts at the line
the cut fell in. Under `--output-format json` the envelope's `result` is the
LAST message's text and nothing else — lines 1–44 of a 90-line answer are
gone, `subtype` is `success`, `is_error` is false, and the exit code is 0.
Only after the third continuation is also cut does the CLI give up and write
the `output token maximum` message `cut_off` reads.

So the seam asks for `stream-json`, reads every assistant message, and joins
them — the cut line dropped from every chunk but the last, because the
continuation writes it again whole. Both fixtures are the CLI's own output.

Run: cd bureau-pipeline && python3 -m pytest tests/test_planning_classify_continuation.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import planning_classify  # noqa: E402

MODEL = "claude-opus-5"
FIXTURES = ROOT / "tests" / "fixtures"
CONTINUED_STREAM = FIXTURES / "claude_code_continuation_stream.jsonl"
CONTINUED_CUT_STREAM = FIXTURES / "claude_code_continuation_cut_stream.jsonl"


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _cli(monkeypatch, stdout="", returncode=0):
    """Stand in for the CLI: a subscription credential and a canned stdout."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token")
    calls = []

    def run(argv, **kwargs):
        calls.append({"argv": list(argv), **kwargs})
        return _Done(returncode=returncode, stdout=stdout)

    monkeypatch.setattr(planning_classify.subprocess, "run", run)
    return calls


def _events(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("{")]


def _plain_envelope(text: str, model: str = MODEL) -> str:
    return json.dumps({"type": "result", "subtype": "success", "is_error": False,
                       "num_turns": 1, "result": text,
                       "modelUsage": {model: {"outputTokens": 3}}})


# --------------------------------------------------------------------------- #
# the fixtures say what the CLI does                                           #
# --------------------------------------------------------------------------- #

def test_the_fixture_is_a_continuation_whose_result_kept_only_the_tail():
    """Pinned so a fixture edited into a one-message stream stops proving
    anything: the failure IS that `result` is not the answer."""
    events = _events(CONTINUED_STREAM)
    messages = {e["message"]["id"] for e in events if e.get("type") == "assistant"}
    assert len(messages) == 2, "two API requests inside ONE turn"
    result = next(e for e in events if e.get("type") == "result")
    assert result["subtype"] == "success" and not result["is_error"]
    assert result["num_turns"] == 1
    assert result["result"].startswith("45 | x"), (
        "`result` opens where the continuation restarted, not at line 1"
    )


def test_the_cut_fixture_gave_up_after_three_continuations():
    events = _events(CONTINUED_CUT_STREAM)
    assert planning_classify.continuations(events) == 3, (
        "the first request and three more, the CLI's own cut message not counted"
    )
    result = next(e for e in events if e.get("type") == "result")
    assert result["is_error"] is True
    assert planning_classify.cut_off(result), "the CLI's own words for the cut"


# --------------------------------------------------------------------------- #
# the seam                                                                     #
# --------------------------------------------------------------------------- #

def test_the_argv_asks_for_the_stream_not_the_envelope(monkeypatch):
    monkeypatch.delenv(planning_classify.AGENT_CLI_ENV, raising=False)
    argv = planning_classify._cli_argv(MODEL)
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv, "print mode refuses stream-json without --verbose"


def test_every_chunk_of_a_continued_answer_is_read(monkeypatch):
    _cli(monkeypatch, stdout=CONTINUED_STREAM.read_text(encoding="utf-8"))
    answer = planning_classify._call_claude_code(MODEL, "the prompt", max_tokens=300)
    lines = answer.text.splitlines()
    assert lines[0] == "1 | x", "the head of the answer survives the continuation"
    assert lines[-1] == "90 | x"
    assert len(lines) == 90, "every line once — the restarted cut line is not doubled"
    assert answer.truncated is False
    assert answer.continuations == 1, "one continuation happened, and it is reported"
    assert answer.model == "claude-haiku-4-5-20251001"


def test_the_cut_line_is_dropped_from_the_chunk_it_was_cut_in(monkeypatch):
    """Chunk one ends `44 | x⏎45` and chunk two opens `45 | x`. Joined naively
    that is `4545 | x` — a line no parser reads — or `45` on a line of its own,
    which the groomer's first-line-wins rule would then keep over the whole
    one. The fragment goes; the whole line stays."""
    _cli(monkeypatch, stdout=CONTINUED_STREAM.read_text(encoding="utf-8"))
    answer = planning_classify._call_claude_code(MODEL, "the prompt")
    assert "4545" not in answer.text
    assert answer.text.count("45 | x") == 1
    assert "\n45\n" not in "\n" + answer.text + "\n"


def test_a_continuation_that_is_itself_cut_is_still_a_cut(monkeypatch):
    """After three continuations the CLI writes the `output token maximum`
    message and `is_error`; the chunks it wrote before giving up are the part
    of the answer that came back, and they come back."""
    _cli(monkeypatch, returncode=1,
         stdout=CONTINUED_CUT_STREAM.read_text(encoding="utf-8"))
    answer = planning_classify._call_claude_code(MODEL, "the prompt", max_tokens=300)
    assert answer.truncated is True
    assert answer.continuations == 3
    lines = answer.text.splitlines()
    assert lines[0] == "1 | x", "what was written before the cut survives"
    assert "output token maximum" not in answer.text, (
        "the CLI's error sentence is not a line of the answer"
    )


def test_a_one_message_stream_reads_exactly_as_before(monkeypatch):
    """The classifier's own one-line answers: one message, no continuation,
    the text is the text."""
    stream = "\n".join([
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "assistant", "message": {
            "id": "msg_1", "role": "assistant", "stop_reason": None,
            "content": [{"type": "text", "text": "all of them"}]}}),
        _plain_envelope("all of them"),
    ])
    _cli(monkeypatch, stdout=stream)
    answer = planning_classify._call_claude_code(MODEL, "the prompt")
    assert answer.text == "all of them"
    assert answer.continuations == 0
    assert answer.model == MODEL


def test_a_bare_envelope_with_no_stream_still_reads_its_result(monkeypatch):
    """A CLI override that prints the plain envelope (the stdin stub in
    `test_planning_classify.py` does) is read off `result` as today — the
    stream is preferred, never required."""
    _cli(monkeypatch, stdout=_plain_envelope("all of them"))
    answer = planning_classify._call_claude_code(MODEL, "the prompt")
    assert answer.text == "all of them"
    assert answer.continuations == 0


def test_the_stream_reader_keeps_one_text_per_message():
    """The CLI emits each API message twice on the stream — once for its
    thinking block, once for its text — under the same message id. One chunk
    per id, in the order the ids first appeared."""
    chunks = planning_classify.assistant_chunks(_events(CONTINUED_STREAM))
    assert [c.splitlines()[0] for c in chunks] == ["1 | x", "45 | x"]
    assert len(chunks) == 2


def test_the_error_message_is_never_a_chunk():
    """The cut stream's last assistant message IS the error sentence; it is the
    CLI talking, not the model answering."""
    chunks = planning_classify.assistant_chunks(_events(CONTINUED_CUT_STREAM))
    assert len(chunks) == 3
    assert not any("output token maximum" in c for c in chunks)


def test_joining_drops_the_fragment_every_chunk_but_the_last_ends_in():
    joined = planning_classify.join_chunks(["a | x\nb | x\nc", "c | x\nd | x\ne",
                                            "e | x\nf | x"])
    assert joined.splitlines() == ["a | x", "b | x", "c | x", "d | x", "e | x", "f | x"]


def test_joining_keeps_a_last_chunk_that_ends_mid_line():
    """Only the seam knows whether the run was cut; the joiner never guesses
    and leaves the final fragment for `groom_judgement.whole_lines`."""
    joined = planning_classify.join_chunks(["a | x\nb", "b | x\nc | x\nd"])
    assert joined.splitlines() == ["a | x", "b | x", "c | x", "d"]


def test_joining_one_chunk_is_that_chunk():
    assert planning_classify.join_chunks(["a | x\nb | x"]) == "a | x\nb | x"
    assert planning_classify.join_chunks([]) == ""
