"""RED-first: a verified console answer is written into the card's description
under one stable heading, and the signature stays on the comment (DRE-6357).

On DRE-3879 the CEO named the two branches in a signed answer at 13:12 PT; the
critic read the card at 13:16 PT, saw no branch names, and sent the card back
to him; a person hand-edited the card at 13:18 PT. On 2026-10-08 the operator
made that edit by hand five times on three cards. `answer_into_card.py` is that
edit, made by the planning run:

  * only a `ceo-via-console` voice is copied — a person's "Answer from Sid", a
    refused receipt and an unchecked one never are;
  * the block sits under `## Decisions from the CEO`, oldest answer first, each
    headed by its signed PT time in bold and quoted, and REPLACES a previous
    block in place;
  * the copy carries no trailer and no signature, so nothing in the
    description can pass for his voice;
  * the CLI writes only when the text changed, always exits 0, and hands the
    description it leaves behind to the next step as `description`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_answer_into_card.py -v
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import answer_into_card as A  # noqa: E402
import console_receipt  # noqa: E402
import console_receipt_vectors as V  # noqa: E402
import github_output  # noqa: E402
import linear_ops  # noqa: E402
import planning_classify  # noqa: E402
import routing_verdict  # noqa: E402
import spoken_thread  # noqa: E402
from test_github_output_heredoc import parse_github_output  # noqa: E402

OPENSSL = V.capable_openssl()
FLEET = "user-agent-bureau"
PERSON = "user-frederick"
CARD = V.ANSWER_CARD
BRIEF = ROOT / "briefs" / "planner.md"

#: DRE-3879's three signed answers, oldest first, as the console signed them.
DRE_3879 = [
    ("2026-09-14T15:14:58Z", "Keep the catalog snapshot as data."),
    ("2026-09-15T20:12:59Z",
     "Two branches: agent/DRE-3879-catalog and agent/DRE-3879-pin."),
    ("2026-09-16T14:28:29Z", "models.json needs no RED test first."),
]

CARD_BODY = """\
The console answer lands in the card.

**Files:** scripts/x.py, tests/test_x.py

## Acceptance criteria

- [ ] The first thing holds.
- [ ] The second thing holds.
"""


def head(at_pt: str = "2026-09-13 09:52") -> str:
    return f"Answer from Test Owner (signed in to the console), {at_pt} PT:"


def block_lines(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.startswith(A.HEADING)]


# --------------------------------------------------------------------------
# words — the console's heading out, his words in
# --------------------------------------------------------------------------
def test_words_drops_the_console_heading_and_the_blank_line_after_it():
    assert A.words(V.ANSWER_TEXT) == V.ANSWER_WORDS
    assert "Answer from" not in A.words(V.ANSWER_TEXT)


def test_words_keeps_his_own_line_that_begins_answer_from():
    text = (f"{head()}\n\nAnswer from the vendor was no, so keep the old key.\n"
            "Ship the rest.")
    assert A.words(text) == ("Answer from the vendor was no, so keep the old "
                             "key.\nShip the rest.")


def test_words_keeps_a_second_line_ending_in_pt():
    text = f"{head()}\nMeet at 10:00 PT:\nbring the plan."
    assert A.words(text) == "Meet at 10:00 PT:\nbring the plan."


def test_words_returns_a_body_with_no_heading_unchanged():
    text = "Go with B.\n\nAnswer from the board, 10:00 PT:\nthat is fine."
    assert A.words(text) == text


def test_words_matches_the_heading_on_the_stripped_first_line():
    text = f"{head()}  \n\nGo with B."
    assert A.words(text) == "Go with B."


def test_words_keeps_his_first_line_when_no_blank_line_follows_the_heading():
    text = f"{head()}\nGo with B.\nAnd soon."
    assert A.words(text) == "Go with B.\nAnd soon."


def test_answer_head_cannot_drift_from_spoken_threads_reader():
    """Two readers of the console's heading — this one and `newest_answer` —
    must strip the same line, or one of them reads the heading as his words."""
    assert A.ANSWER_HEAD.pattern == spoken_thread._ANSWER_HEAD.pattern


# --------------------------------------------------------------------------
# transcribe — the block, in place, oldest first
# --------------------------------------------------------------------------
def test_dre_3879s_three_answers_render_oldest_first_under_the_heading():
    shuffled = [DRE_3879[2], DRE_3879[0], DRE_3879[1]]
    out = A.transcribe(CARD_BODY, shuffled)
    assert block_lines(out) == [A.HEADING]
    block = out[out.index(A.HEADING):]
    times = ["**2026-09-14 08:14 PT**", "**2026-09-15 13:12 PT**",
             "**2026-09-16 07:28 PT**"]
    at = [block.index(t) for t in times]
    assert at == sorted(at)
    for _, said in DRE_3879:
        assert f"> {said}" in block
    assert block.index("> Keep the catalog") < block.index("> Two branches")
    assert block.index("> Two branches") < block.index("> models.json")
    lines = block.split("\n")
    assert lines[1] == A.PROVENANCE == (
        "Copied from his signed console answers; the signature stays on the "
        "comment and this copy proves nothing on its own.")


def test_every_line_of_his_words_is_quoted_and_no_line_in_the_block_is_a_heading():
    said = "First line.\n\n## Not a heading\n- [ ] not a criterion"
    out = A.transcribe("Intro.", [("2026-09-14T15:14:58Z", said)])
    block = out.split(A.HEADING, 1)[1]
    assert not any(line.startswith("## ") for line in block.split("\n"))
    assert "> First line.\n>\n> ## Not a heading\n> - [ ] not a criterion" in block


def test_the_block_is_appended_when_none_is_present_and_the_rest_is_untouched():
    out = A.transcribe(CARD_BODY, DRE_3879[:1])
    assert out.startswith(CARD_BODY)
    assert block_lines(out) == [A.HEADING]


def test_a_second_call_over_the_result_is_byte_identical():
    once = A.transcribe(CARD_BODY, DRE_3879)
    assert A.transcribe(once, DRE_3879) == once
    middle = CARD_BODY.replace("## Acceptance", "## Decisions from the CEO\n\n"
                               "hand typed\n\n## Acceptance")
    once = A.transcribe(middle, DRE_3879)
    assert A.transcribe(once, DRE_3879) == once


def test_a_new_answer_replaces_the_block_in_place_and_leaves_the_rest_alone():
    above = "Intro line.\n\n**Files:** a.py\n\n"
    below = "## Acceptance criteria\n\n- [ ] One.\n- [ ] Two.\n"
    first = A.transcribe(above + below, DRE_3879[:1])
    # The block was appended after the criteria; move it above them, as a
    # person would, so the replacement has text on both sides.
    block = first[len(above + below):].lstrip("\n")
    moved = above + block.rstrip("\n") + "\n\n" + below
    second = A.transcribe(moved, DRE_3879[:2])
    assert block_lines(second) == [A.HEADING]
    assert second.startswith(above + A.HEADING)
    assert second.endswith("\n\n" + below)
    assert "**2026-09-15 13:12 PT**" in second
    assert second.index(A.HEADING) < second.index("## Acceptance criteria")


def test_the_block_ends_at_the_next_heading_which_is_left_byte_identical():
    rest = "## Acceptance criteria\n\n- [ ] One.\n- [ ] Two.\n\nTrailing prose.\n"
    desc = (f"Intro.\n\n{A.HEADING}\n\nan old hand-typed copy\nof something\n\n"
            + rest)
    out = A.transcribe(desc, DRE_3879)
    assert out.startswith(f"Intro.\n\n{A.HEADING}\n")
    assert out.endswith("\n\n" + rest)
    assert "hand-typed" not in out


def test_no_verified_answer_removes_a_hand_typed_block():
    desc = (f"Intro.\n\n{A.HEADING}\n\nThe CEO said ship it.\n\n"
            "## Acceptance criteria\n\n- [ ] One.\n")
    out = A.transcribe(desc, [])
    assert A.HEADING not in out
    assert "ship it" not in out
    assert out == "Intro.\n\n## Acceptance criteria\n\n- [ ] One.\n"


def test_no_answer_and_no_block_is_the_description_unchanged():
    assert A.transcribe(CARD_BODY, []) == CARD_BODY
    assert A.transcribe(None, []) == ""


def test_the_written_description_carries_no_provenance_a_reader_could_trust():
    """Nothing in the description proves identity: no trailer, no receipt, and
    read as a comment it is nobody's console answer."""
    desc = A.transcribe(CARD_BODY, DRE_3879)
    assert console_receipt.has_answer_trailer(desc) is False
    assert console_receipt.parse_answer(desc) is None
    assert "sha256=" not in desc and "sig=" not in desc
    as_comment = [{"body": desc, "createdAt": "2026-09-16T14:28:35Z",
                   "user": {"id": FLEET}}]
    kinds = [v.kind for v in spoken_thread.voices(as_comment, FLEET,
                                                  card="DRE-3879")]
    assert spoken_thread.CEO_VIA_CONSOLE not in kinds


def test_checkbox_syntax_in_his_words_adds_no_acceptance_criterion():
    said = "Do it this way:\n- [ ] the old export stays\n* [x] the new one ships"
    before = routing_verdict.route("A card", CARD_BODY)
    after_desc = A.transcribe(CARD_BODY, [("2026-09-14T15:14:58Z", said)])
    after = routing_verdict.route("A card", after_desc)
    assert (len(routing_verdict.acceptance_criteria(after_desc))
            == len(routing_verdict.acceptance_criteria(CARD_BODY)) == 2)
    assert after.verdict == before.verdict


# --------------------------------------------------------------------------
# the brief
# --------------------------------------------------------------------------
REVISE_SENTENCE = (
    "A card carrying a `## Decisions from the CEO` block is carrying the "
    "pipeline's copy of his signed answers: keep that block where it is, word "
    "for word, and write the rest of the card to agree with it.")


def _revise_bullet(text: str) -> str:
    start = text.index("- **A one-off reaches the CEO only with a question.**")
    end = text.index("\n\n", start)
    return text[start:end]


def test_the_classification_prompt_says_the_decisions_block_is_settled():
    prompt = planning_classify.brief_prompt()
    assert "`## Decisions from the CEO`" in prompt
    paragraph = next(p for p in prompt.split("\n\n")
                     if "## Decisions from the CEO" in p)
    flat = " ".join(paragraph.split())
    assert "settled" in flat
    assert "`decision: true`" in flat
    full = planning_classify.prompt_for(
        {"identifier": "DRE-1", "title": "t", "description": "d"})
    assert "## Decisions from the CEO" in full


def test_the_revise_bullet_keeps_the_block_word_for_word():
    text = BRIEF.read_text(encoding="utf-8")
    bullet = _revise_bullet(text)
    assert "When the pre-approval critic sends a one-off back" in bullet
    assert REVISE_SENTENCE in " ".join(bullet.split())
    assert text.count("## Decisions from the CEO") == 2
    assert not any(line.startswith("## Decisions from the CEO")
                   for line in text.split("\n"))


# --------------------------------------------------------------------------
# the CLI, over a mocked linear_ops and a test console key
# --------------------------------------------------------------------------
@pytest.fixture
def console_key(monkeypatch):
    if OPENSSL is None:
        if os.environ.get("CI"):
            pytest.fail("no Ed25519-capable openssl on a CI runner")
        pytest.skip("no Ed25519-capable openssl on this machine")
    monkeypatch.setenv("OPENSSL_BIN", OPENSSL)
    monkeypatch.setattr(console_receipt, "_OPENSSL", None, raising=False)
    monkeypatch.setattr(spoken_thread, "_VERIFIER", console_receipt.Verifier(
        key_loader=lambda: console_receipt.PublicKey.from_b64(
            V.PUBLIC_KEY_B64)))


@pytest.fixture
def no_key(monkeypatch):
    def loader():
        raise console_receipt.KeyUnavailable("timed out")
    monkeypatch.setattr(spoken_thread, "_VERIFIER",
                        console_receipt.Verifier(key_loader=loader))


def signed(words: str, at: str, *, card: str = CARD) -> str:
    """An answer comment as the console posts it, signed with the TEST key."""
    text = f"{head()}\n\n{words}"
    digest = console_receipt.answer_sha256(text)
    sig = V.b64url(V.sign(console_receipt.answer_signed_bytes(
        card, digest, V.ANSWER_USER, at), openssl=OPENSSL))
    return f"{text}\n\n" + console_receipt.answer_trailer(
        card=card, sha256=digest, user=V.ANSWER_USER, at=at, kid=V.KID,
        sig=sig)


def node(body, *, by=FLEET, at="2026-09-13T16:52:12Z"):
    return {"body": body, "createdAt": at, "user": {"id": by}}


class FakeLinear:
    """The three Linear calls the CLI makes, recorded."""

    def __init__(self, monkeypatch, *, nodes=(), description=CARD_BODY,
                 thread_raises=None, write_raises=None):
        self.nodes = list(nodes)
        self.description = description
        self.writes: list[tuple[str, str]] = []
        self.thread_reads: list[tuple] = []
        self.thread_raises = thread_raises
        self.write_raises = write_raises
        monkeypatch.setattr(linear_ops, "_thread_and_viewer", self.thread)
        monkeypatch.setattr(linear_ops, "gql", self.gql)
        monkeypatch.setattr(linear_ops, "set_description", self.set_description)
        monkeypatch.setattr(linear_ops, "get_issues_with_comments",
                            self.list_api, raising=False)

    def thread(self, identifier, *needs, whole=False):
        self.thread_reads.append((identifier, needs, whole))
        if self.thread_raises:
            raise self.thread_raises
        return self.nodes, FLEET

    def gql(self, query, variables=None):
        assert "issue(id:" in " ".join(query.split())
        assert "description" in query
        return {"issue": {"description": self.description}}

    def set_description(self, identifier, body):
        if self.write_raises:
            raise self.write_raises
        self.writes.append((identifier, body))
        print(f"{identifier} description updated")

    def list_api(self, *a, **kw):
        raise AssertionError("the list API truncates descriptions")


def run(*argv) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out):
        code = A.main(list(argv))
    return code, out.getvalue()


ONE_ANSWER = "Go with option B.\n- [ ] keep the old export"


def test_the_whole_thread_is_read_the_way_green_light_reply_reads_it(
        monkeypatch, console_key):
    fake = FakeLinear(monkeypatch, nodes=[node(signed(ONE_ANSWER,
                                                      "2026-09-13T16:52:07Z"))])
    run("write", CARD)
    assert fake.thread_reads == [(CARD, ("body", "user", "createdAt"), True)]


def test_a_changed_card_is_written_once_and_stdout_is_one_status_line(
        monkeypatch, console_key):
    fake = FakeLinear(monkeypatch, nodes=[node(signed(ONE_ANSWER,
                                                      "2026-09-13T16:52:07Z"))])
    code, out = run("write", CARD)
    assert code == 0
    assert len(fake.writes) == 1
    written = fake.writes[0][1]
    assert written == A.transcribe(CARD_BODY,
                                   [("2026-09-13T16:52:07Z", ONE_ANSWER)])
    assert out.splitlines() == [f"{A.PREFIX} wrote 1 answer(s) into {CARD}'s "
                                "description"]
    assert "description updated" not in out


def test_an_unchanged_card_costs_no_write(monkeypatch, console_key):
    current = A.transcribe(CARD_BODY, [("2026-09-13T16:52:07Z", ONE_ANSWER)])
    fake = FakeLinear(monkeypatch, description=current, nodes=[
        node(signed(ONE_ANSWER, "2026-09-13T16:52:07Z"))])
    code, out = run("write", CARD)
    assert (code, fake.writes) == (0, [])
    assert out.strip() == f"{A.PREFIX} unchanged"


def test_dry_run_never_writes_and_prints_the_status_then_the_description(
        monkeypatch, console_key):
    fake = FakeLinear(monkeypatch, nodes=[node(signed(ONE_ANSWER,
                                                      "2026-09-13T16:52:07Z"))])
    code, out = run("write", CARD, "--dry-run")
    assert (code, fake.writes) == (0, [])
    first, rest = out.split("\n", 1)
    assert first.startswith(A.PREFIX)
    assert rest.rstrip("\n") == A.transcribe(
        CARD_BODY, [("2026-09-13T16:52:07Z", ONE_ANSWER)]).rstrip("\n")


def test_only_the_ceos_verified_voice_is_transcribed(monkeypatch, console_key):
    good = signed(ONE_ANSWER, "2026-09-13T16:52:07Z")
    edited = good.replace("option B", "option C")      # refused: words changed
    fake = FakeLinear(monkeypatch, nodes=[
        node("Answer from Sid: go with option A.", by=PERSON),
        node(edited),
        node(f"{head()}\n\nFrom the pipeline: go with option D."),
    ])
    code, out = run("write", CARD)
    assert (code, fake.writes) == (0, [])
    assert out.strip() == f"{A.PREFIX} no verified answer on this card"


def test_an_unchecked_receipt_is_never_transcribed_and_says_could_not_check(
        monkeypatch, no_key):
    hand_typed = CARD_BODY + f"\n{A.HEADING}\n\nhand typed\n"
    fake = FakeLinear(monkeypatch, description=hand_typed, nodes=[
        node(V.ANSWER_COMMENT, at="2026-09-13T16:52:12Z")])
    code, out = run("write", CARD)
    assert (code, fake.writes) == (0, [])
    assert out.startswith(f"{A.PREFIX} could not check: ")


def test_a_person_a_refused_and_an_unchecked_receipt_in_one_thread_write_nothing(
        monkeypatch, no_key):
    """The words-changed check runs before the key is fetched, so with the key
    unreadable the edited receipt is still REFUSED and the genuine one is
    UNCHECKED — all three kinds in one thread, and none of them copied."""
    edited = V.ANSWER_COMMENT.replace("option B", "option C", 1)
    nodes = [node("Answer from Sid: go with option A.", by=PERSON),
             node(edited), node(V.ANSWER_COMMENT)]
    kinds = [v.kind for v in spoken_thread.voices(nodes, FLEET, card=CARD)]
    assert kinds == [spoken_thread.PERSON, spoken_thread.REFUSED,
                     spoken_thread.UNCHECKED]
    fake = FakeLinear(monkeypatch, nodes=nodes)
    code, out = run("write", CARD)
    assert (code, fake.writes) == (0, [])
    assert out.startswith(f"{A.PREFIX} could not check: ")


def test_no_verified_answer_removes_a_hand_typed_block_with_one_write(
        monkeypatch, console_key):
    hand_typed = CARD_BODY + f"\n{A.HEADING}\n\nhand typed\n"
    fake = FakeLinear(monkeypatch, description=hand_typed, nodes=[
        node("Answer from Sid: go with option A.", by=PERSON)])
    code, out = run("write", CARD)
    assert code == 0
    assert len(fake.writes) == 1 and A.HEADING not in fake.writes[0][1]
    assert out.startswith(A.PREFIX)


def test_a_write_that_raises_is_a_printed_line_and_exit_0(monkeypatch,
                                                          console_key):
    FakeLinear(monkeypatch, nodes=[node(signed(ONE_ANSWER,
                                               "2026-09-13T16:52:07Z"))],
               write_raises=RuntimeError("Linear said 500"))
    code, out = run("write", CARD)
    assert code == 0
    assert out.strip() == (f"{A.PREFIX} could not write: RuntimeError: "
                           "Linear said 500")


def test_a_thread_that_cannot_be_read_is_a_printed_line_and_exit_0(
        monkeypatch, console_key):
    fake = FakeLinear(monkeypatch, thread_raises=RuntimeError("timeout"))
    code, out = run("write", CARD)
    assert (code, fake.writes) == (0, [])
    assert out.startswith(f"{A.PREFIX} could not check: ")


def test_help_says_it_always_exits_0_and_why():
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" /
                                               "answer_into_card.py"), "--help"],
                          capture_output=True, text=True, check=False)
    flat = " ".join(proc.stdout.split())
    assert proc.returncode == 0
    assert "always exits 0" in flat
    assert "never a red planning run" in flat


# --------------------------------------------------------------------------
# --github-output: the description, for the sibling's sanitizer
# --------------------------------------------------------------------------
def _sanitizer_parse(written: str) -> str:
    """The read `test_untrusted_content_wiring` makes of the sanitizer's
    heredoc, and the runner-strict parse beside it — both must agree."""
    m = re.match(r"description<<(\S+)\n(.*)\n\1\n", written, re.S)
    assert m is not None, f"expected heredoc output, got: {written!r}"
    assert parse_github_output(written) == {"description": m.group(2)}
    return m.group(2)


def test_github_output_carries_the_written_description(monkeypatch, tmp_path,
                                                        console_key):
    fake = FakeLinear(monkeypatch, nodes=[node(signed(ONE_ANSWER,
                                                      "2026-09-13T16:52:07Z"))])
    monkeypatch.setattr(github_output, "_token", lambda: "fixed")
    out_file = tmp_path / "out"
    run("write", CARD, "--github-output", str(out_file))
    written = out_file.read_text(encoding="utf-8")
    assert _sanitizer_parse(written) == fake.writes[0][1]
    assert written == github_output.render([("description",
                                             fake.writes[0][1])])


def test_github_output_on_dry_run_carries_what_would_be_written(
        monkeypatch, tmp_path, console_key):
    fake = FakeLinear(monkeypatch, nodes=[node(signed(ONE_ANSWER,
                                                      "2026-09-13T16:52:07Z"))])
    out_file = tmp_path / "out"
    run("write", CARD, "--dry-run", "--github-output", str(out_file))
    assert fake.writes == []
    assert _sanitizer_parse(out_file.read_text(encoding="utf-8")) == (
        A.transcribe(CARD_BODY, [("2026-09-13T16:52:07Z", ONE_ANSWER)]))


def test_github_output_on_an_unchanged_card_carries_the_current_description(
        monkeypatch, tmp_path, console_key):
    current = A.transcribe(CARD_BODY, [("2026-09-13T16:52:07Z", ONE_ANSWER)])
    FakeLinear(monkeypatch, description=current, nodes=[
        node(signed(ONE_ANSWER, "2026-09-13T16:52:07Z"))])
    out_file = tmp_path / "out"
    run("write", CARD, "--github-output", str(out_file))
    assert _sanitizer_parse(out_file.read_text(encoding="utf-8")) == current


def test_github_output_with_no_verified_answer_carries_the_current_description(
        monkeypatch, tmp_path, console_key):
    FakeLinear(monkeypatch, nodes=[])
    out_file = tmp_path / "out"
    run("write", CARD, "--github-output", str(out_file))
    assert _sanitizer_parse(out_file.read_text(encoding="utf-8")) == CARD_BODY


def test_github_output_is_untouched_when_the_thread_read_raises(
        monkeypatch, tmp_path, console_key):
    FakeLinear(monkeypatch, thread_raises=RuntimeError("timeout"))
    out_file = tmp_path / "out"
    out_file.write_text("earlier=1\n", encoding="utf-8")
    code, _ = run("write", CARD, "--github-output", str(out_file))
    assert code == 0
    assert out_file.read_text(encoding="utf-8") == "earlier=1\n"


def test_without_github_output_no_file_is_written(monkeypatch, tmp_path,
                                                  console_key):
    FakeLinear(monkeypatch, nodes=[node(signed(ONE_ANSWER,
                                               "2026-09-13T16:52:07Z"))])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "env-output"))
    run("write", CARD)
    assert list(tmp_path.iterdir()) == []
