"""RED-first tests: the three Green Light lines are declared once (DRE-3908).

Every decision-kind Green Light park is to carry three machine-readable lines —
Finding, Question, Recommendation — and the writers that will emit them
(DRE-3909, DRE-3910, DRE-5204, DRE-3911, DRE-6174, and DRE-6189 across the
epic line) all import ONE module rather than restating the prefixes. The pick-
one answers are not a new grammar: they are DRE-6168's `escalation-choices`
block, built from the same `Escalation` as the lines, so the Recommendation
line and the button the console highlights can never disagree.

WHAT THIS PINS, one section per acceptance criterion:

  1. The four constant strings and the separator, byte for byte; the module
     declares no fence string of its own.
  2. `render` → `parse` round-trips each fixture's lines, and
     `render_with_block` → `parse` its choices; `none given` reads back as None.
  3. The Recommendation answer is the recommended choice's label, whatever
     `recommendation` says; `problems` names a hand-written disagreement.
  4. A block `choices_problem` refuses is not rendered, and says why on stderr.
  5. `problems` names each defect, and passes every rendered fixture.
  6. `split` lifts the lines and the accepted block; a refused block stays in
     the prose, where `planning_escalation.refusal` still refuses it.
  7. `complete` leaves a declared body alone and finishes an undeclared one.
  8. The CLI, run as the shell writers will run it.
  9. The fixture file holds exactly the four synthetic cases.
 10. The module docstring says who reads the lines.

Run: cd bureau-pipeline && python3 -m pytest tests/test_console_escalation.py -v
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import console_escalation as ce  # noqa: E402
import planning_escalation  # noqa: E402

SCRIPT = ROOT / "scripts" / "console_escalation.py"
FIXTURE = ROOT / "tests" / "fixtures" / "green-light-escalations-2026-09-14.json"
RECORDS = json.loads(FIXTURE.read_text(encoding="utf-8"))
IDS = [r["card"] for r in RECORDS]


def _escalation(record: dict, with_choices: bool = True) -> ce.Escalation:
    """A fixture record as the `Escalation` a writer would build from it."""
    choices = tuple(ce.Choice(c["id"], c["label"], c["effect"], c["outcome"])
                    for c in record["choices"]) if with_choices else ()
    return ce.Escalation(
        finding=record["finding"],
        question=record["question"],
        recommendation=record["recommendation"],
        why=record["recommendation_why"],
        choices=choices,
        recommended=record["recommended"] if with_choices else "",
    )


def _choices(n: int) -> tuple:
    return tuple(ce.Choice(f"choice-{i}", f"choice {i}", f"what choice {i} does",
                           "proceed") for i in range(n))


PROSE = ("The reviewer read the card twice and could not settle it.\n\n"
         "It needs a person to pick.")


# --------------------------------------------------------------------------- #
# 1. the contract strings                                                      #
# --------------------------------------------------------------------------- #


def test_the_four_constants_and_the_separator_byte_for_byte():
    assert ce.FINDING_PREFIX.encode() == "🔎 Finding:".encode()
    assert ce.QUESTION_PREFIX.encode() == "❓ Question:".encode()
    assert ce.RECOMMENDATION_PREFIX.encode() == "💡 Recommendation:".encode()
    assert ce.NONE_GIVEN.encode() == b"none given"
    assert ce.SEPARATOR == " — "


def _code_strings() -> list[str]:
    """Every string literal in the module that is not a docstring."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def test_the_module_declares_no_fence_string_of_its_own():
    for literal in _code_strings():
        assert planning_escalation.CHOICES_FENCE not in literal, literal
        assert "```" not in literal, literal
    assert "planning_escalation.CHOICES_FENCE" in SCRIPT.read_text(
        encoding="utf-8")


def test_the_fence_is_read_from_planning_escalation_at_call_time(monkeypatch):
    esc = _escalation(RECORDS[0])
    before = ce.render_with_block(esc)
    monkeypatch.setattr(planning_escalation, "CHOICES_FENCE", "renamed-fence")
    after = ce.render_with_block(esc)
    assert "```renamed-fence\n" in after
    assert ce.parse(after).choices == esc.choices
    # A block under the old name is no longer one this module reads.
    assert ce.parse(before).choices == ()


# --------------------------------------------------------------------------- #
# 2. render and parse round-trip                                               #
# --------------------------------------------------------------------------- #


def test_render_is_three_consecutive_lines_in_order():
    esc = ce.Escalation("a finding", "a question?", "an answer", "a why")
    assert ce.render(esc) == (
        "🔎 Finding: a finding\n"
        "❓ Question: a question?\n"
        "💡 Recommendation: an answer — a why"
    )


@pytest.mark.parametrize("record", RECORDS, ids=IDS)
def test_render_then_parse_round_trips_the_three_lines(record):
    esc = _escalation(record, with_choices=False)
    got = ce.parse(ce.render(esc))
    assert got == esc


@pytest.mark.parametrize("record", RECORDS, ids=IDS)
def test_render_with_block_then_parse_round_trips_the_choices(record):
    esc = _escalation(record)
    text = ce.render_with_block(esc)
    got = ce.parse(text)
    assert got.finding == record["finding"]
    assert got.question == record["question"]
    assert got.recommendation == record["recommendation"]
    assert got.why == record["recommendation_why"]
    assert got.choices == esc.choices
    assert got.recommended == record["recommended"]
    assert text.rstrip().endswith("```")


def test_none_given_renders_and_reads_back_as_none():
    esc = ce.Escalation("a finding", "a question?", None, "the reader stalled")
    text = ce.render(esc)
    third = text.split("\n")[2]
    assert third.startswith("💡 Recommendation: none given — ")
    assert third == "💡 Recommendation: none given — the reader stalled"
    got = ce.parse(text)
    assert got.recommendation is None
    assert got.why == "the reader stalled"


def test_parse_reads_lines_with_leading_whitespace_inside_prose():
    text = (PROSE + "\n\n  🔎 Finding: f\n\t❓ Question: q?\n"
            "   💡 Recommendation: a — w\n")
    got = ce.parse(text)
    assert (got.finding, got.question, got.recommendation, got.why) == (
        "f", "q?", "a", "w")


def test_parse_is_none_unless_all_three_lines_are_present():
    full = ce.render(ce.Escalation("f", "q?", "a", "w")).split("\n")
    for missing in range(3):
        lines = [line for i, line in enumerate(full) if i != missing]
        assert ce.parse("\n".join(lines)) is None
    assert ce.parse("") is None


def test_parse_reads_the_first_line_of_each_prefix():
    text = ce.render(ce.Escalation("first", "q?", "a", "w")) + "\n" + \
        ce.render(ce.Escalation("second", "q2?", "b", "x"))
    assert ce.parse(text).finding == "first"


def test_parse_takes_the_last_accepted_block():
    esc = _escalation(RECORDS[0])
    good = planning_escalation.choices_block(ce.block(esc))
    refused = ce.block(esc).copy()
    refused["recommended"] = "nobody"
    text = (ce.render(esc) + "\n\n" + good + "\n\n"
            + planning_escalation.choices_block(refused))
    got = ce.parse(text)
    assert got.choices == esc.choices
    assert got.recommended == esc.recommended


def test_parse_carries_a_choice_preview():
    choices = (ce.Choice("yes", "yes", "it goes ahead", "proceed", "Shown at 3 PM"),
               ce.Choice("no", "no", "it stops", "close"))
    esc = ce.Escalation("f", "q?", "yes", "w", choices, "yes")
    assert ce.block(esc)["choices"][0]["preview"] == "Shown at 3 PM"
    assert "preview" not in ce.block(esc)["choices"][1]
    assert ce.parse(ce.render_with_block(esc)).choices == choices


# --------------------------------------------------------------------------- #
# 3. the Recommendation answer is the recommended choice's label               #
# --------------------------------------------------------------------------- #


def test_the_answer_is_the_recommended_choices_label_not_the_recommendation():
    choices = (ce.Choice("keep-it", "keep it", "nothing changes", "proceed"),
               ce.Choice("drop-it", "drop it", "the item goes", "proceed"))
    esc = ce.Escalation("f", "Keep it or drop it?", "something else entirely",
                        "dropping it loses nothing", choices, "drop-it")
    lines = ce.render(esc).split("\n")
    assert lines[2] == "💡 Recommendation: drop it — dropping it loses nothing"
    assert ce.block(esc)["recommended"] == "drop-it"
    assert ce.problems(ce.render_with_block(esc)) == []


def test_an_empty_recommended_means_the_first_choice():
    choices = (ce.Choice("keep-it", "keep it", "nothing changes", "proceed"),
               ce.Choice("drop-it", "drop it", "the item goes", "proceed"))
    esc = ce.Escalation("f", "Keep it or drop it?", None, "w", choices)
    assert ce.render(esc).split("\n")[2] == "💡 Recommendation: keep it — w"
    assert ce.block(esc)["recommended"] == "keep-it"
    assert ce.parse(ce.render_with_block(esc)).recommended == "keep-it"


def test_the_block_is_built_from_the_escalation():
    record = RECORDS[1]
    built = ce.block(_escalation(record))
    assert built == {
        "question": record["question"],
        "context": record["finding"],
        "choices": record["choices"],
        "recommended": record["recommended"],
        "why": record["recommendation_why"],
    }


def test_problems_names_a_hand_written_recommendation_that_differs():
    esc = _escalation(RECORDS[0])
    text = ce.render_with_block(esc).replace(
        "💡 Recommendation: open a pull request —",
        "💡 Recommendation: grant a bypass —")
    found = ce.problems(text)
    assert any("Recommendation" in p and "label" in p for p in found), found


def test_problems_names_a_block_question_that_differs():
    esc = _escalation(RECORDS[0])
    built = ce.block(esc)
    built["question"] = "Should we do something else entirely?"
    text = ce.render(esc) + "\n\n" + planning_escalation.choices_block(built)
    found = ce.problems(text)
    assert any("question" in p and "Question line" in p for p in found), found


# --------------------------------------------------------------------------- #
# 4. a refused block is not rendered                                           #
# --------------------------------------------------------------------------- #


def _refused_cases() -> dict:
    two = _choices(2)
    return {
        "one choice": ce.Escalation("f", "q?", "a", "w", _choices(1)),
        "five choices": ce.Escalation("f", "q?", "a", "w", _choices(5)),
        "card number in a label": ce.Escalation(
            "f", "q?", "a", "w",
            (ce.Choice("x", "do DRE-1234 first", "it waits", "proceed"),) + two[1:]),
        "outcome outside the three": ce.Escalation(
            "f", "q?", "a", "w",
            (ce.Choice("x", "do it", "it goes", "merge"),) + two[1:]),
    }


@pytest.mark.parametrize("case", list(_refused_cases()))
def test_block_refuses_and_reports_on_stderr(case, capsys):
    esc = _refused_cases()[case]
    assert ce.block(esc) is None
    err = capsys.readouterr().err.strip().split("\n")
    assert len(err) == 1
    rule = planning_escalation.choices_problem({
        "question": esc.question, "context": esc.finding,
        "choices": [{"id": c.id, "label": c.label, "effect": c.effect,
                     "outcome": c.outcome} for c in esc.choices],
        "recommended": esc.recommended or esc.choices[0].id, "why": esc.why})
    assert rule
    assert err[0] == f"escalation-choices refused: {rule}"


@pytest.mark.parametrize("case", list(_refused_cases()))
def test_render_with_block_of_a_refused_block_is_the_three_lines(case):
    esc = _refused_cases()[case]
    assert ce.render_with_block(esc) == ce.render(esc)


def test_block_is_none_and_silent_with_no_choices(capsys):
    esc = ce.Escalation("f", "q?", "a", "w")
    assert ce.block(esc) is None
    assert capsys.readouterr().err == ""
    assert ce.render_with_block(esc) == ce.render(esc)


# --------------------------------------------------------------------------- #
# 5. problems names each defect                                                #
# --------------------------------------------------------------------------- #

GOOD = ce.render(ce.Escalation("a finding", "a question?", "an answer", "a why"))
F, Q, R = GOOD.split("\n")


@pytest.mark.parametrize("missing, name", [(0, "Finding"), (1, "Question"),
                                           (2, "Recommendation")])
def test_problems_names_a_missing_line(missing, name):
    lines = [line for i, line in enumerate((F, Q, R)) if i != missing]
    found = ce.problems("\n".join(lines))
    assert any(name in p and "missing" in p for p in found), found


def test_problems_names_lines_out_of_order():
    found = ce.problems("\n".join((Q, F, R)))
    assert any("order" in p for p in found), found


def test_problems_names_two_lines_on_one_line():
    found = ce.problems(F + " " + Q + "\n" + R)
    assert any("Question" in p and "own line" in p for p in found), found


def test_problems_names_an_empty_value():
    found = ce.problems("🔎 Finding:\n" + Q + "\n" + R)
    assert any("Finding" in p and "empty" in p for p in found), found


def test_problems_names_a_recommendation_with_no_why():
    found = ce.problems(F + "\n" + Q + "\n💡 Recommendation: an answer")
    assert any("—" in p and "why" in p for p in found), found


def test_problems_names_none_given_with_no_why():
    for third in ("💡 Recommendation: none given",
                  "💡 Recommendation: none given — "):
        found = ce.problems(F + "\n" + Q + "\n" + third)
        assert any("none given" in p and "why" in p for p in found), found


def test_problems_names_two_blocks():
    esc = _escalation(RECORDS[0])
    text = ce.render_with_block(esc) + "\n\n" + \
        planning_escalation.choices_block(ce.block(esc))
    found = ce.problems(text)
    assert any("two" in p and "blocks" in p for p in found), found


def test_problems_names_a_refused_block_with_a_card_number_in_a_label():
    esc = _escalation(RECORDS[0])
    built = ce.block(esc)
    built["choices"][1]["label"] = "grant DRE-3879 a bypass"
    text = ce.render(esc) + "\n\n" + planning_escalation.choices_block(built)
    found = ce.problems(text)
    assert any("card number" in p for p in found), found


@pytest.mark.parametrize("record", RECORDS, ids=IDS)
def test_problems_passes_every_rendered_fixture(record):
    esc = _escalation(record)
    assert ce.problems(ce.render(esc)) == []
    assert ce.problems(ce.render_with_block(esc)) == []
    assert ce.problems(PROSE + "\n\n" + ce.render_with_block(esc)) == []


def test_problems_passes_three_lines_and_no_block():
    assert ce.problems(GOOD) == []


def test_problems_passes_none_given_with_a_why():
    assert ce.problems(ce.render(ce.Escalation("f", "q?", None, "w"))) == []


# --------------------------------------------------------------------------- #
# 6. split                                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("record", RECORDS, ids=IDS)
def test_split_lifts_the_lines_and_the_block_and_returns_the_prose(record):
    esc = _escalation(record)
    prose, got = ce.split(PROSE + "\n\n" + ce.render_with_block(esc))
    assert prose == PROSE
    assert got == ce.parse(ce.render_with_block(esc))
    assert got.choices == esc.choices
    assert planning_escalation.refusal(prose) is None


def test_split_lifts_lines_from_the_middle_of_the_prose():
    text = "Before.\n\n" + GOOD + "\n\nAfter."
    prose, got = ce.split(text)
    assert prose == "Before.\n\nAfter."
    assert got.finding == "a finding"


def test_split_leaves_a_refused_block_in_the_prose():
    esc = _escalation(RECORDS[0])
    built = ce.block(esc)
    built["choices"][1]["label"] = "grant DRE-3879 a bypass"
    refused = planning_escalation.choices_block(built)
    prose, got = ce.split(PROSE + "\n\n" + ce.render(esc) + "\n\n" + refused)
    assert prose == PROSE + "\n\n" + refused
    assert got.choices == ()
    assert "a code fence" in planning_escalation.refusal(prose)


def test_split_of_a_body_with_no_lines_lifts_nothing():
    assert ce.split(PROSE) == (PROSE, None)


# --------------------------------------------------------------------------- #
# 7. complete                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("record", RECORDS, ids=IDS)
def test_complete_leaves_a_declared_body_byte_identical(record):
    esc = _escalation(record)
    for body in (PROSE + "\n\n" + ce.render(esc),
                 PROSE + "\n\n" + ce.render_with_block(esc)):
        assert ce.complete(body, question="ignored?", who="the critic") == body


def test_complete_takes_the_last_question_line_of_the_body():
    body = ("The card names two fixes. It picks neither.\n"
            "Is the first one safe?\n"
            "Should we take the second one instead?\n"
            "That is all.")
    out = ce.complete(body, question="the argument?", who="the critic")
    assert out.startswith(body + "\n\n")
    got = ce.parse(out)
    assert got.question == "Should we take the second one instead?"
    assert got.finding == "The card names two fixes."
    assert got.recommendation is None
    assert out.split("\n")[-1] == (
        "💡 Recommendation: none given — the critic stated no recommendation")
    assert ce.problems(out) == []


def test_complete_takes_the_question_argument_when_the_body_asks_none():
    body = "The run   stopped\nbefore it\nfinished. Nothing was posted."
    out = ce.complete(body, question="Re-run it, or settle it yourself?",
                      who="the reviewer")
    got = ce.parse(out)
    assert got.question == "Re-run it, or settle it yourself?"
    assert got.finding == "The run stopped before it finished."
    assert got.why == "the reviewer stated no recommendation"


def test_complete_takes_the_finding_argument_and_caps_a_derived_one():
    out = ce.complete("x" * 500, question="q?", who="w", finding="given")
    assert ce.parse(out).finding == "given"
    derived = ce.parse(ce.complete("x" * 500, question="q?", who="w")).finding
    assert len(derived) == 300
    assert derived.endswith("…")


def test_complete_never_adds_a_block():
    body = "Something went wrong and nobody said what to do."
    out = ce.complete(body, question="Retry it?", who="the critic")
    assert "```" not in out
    assert ce.parse(out).choices == ()


# --------------------------------------------------------------------------- #
# 8. the CLI                                                                   #
# --------------------------------------------------------------------------- #


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=ROOT)


def test_cli_render_with_a_recommendation():
    r = _cli("render", "--finding", "f", "--question", "q?",
             "--recommendation", "a", "--why", "w")
    assert r.returncode == 0, r.stderr
    assert r.stdout == ce.render(ce.Escalation("f", "q?", "a", "w")) + "\n"


def test_cli_render_with_no_recommendation_renders_none_given():
    r = _cli("render", "--finding", "f", "--question", "q?")
    assert r.returncode == 0, r.stderr
    assert r.stdout.split("\n")[2] == (
        "💡 Recommendation: none given — no recommendation was stated")
    r = _cli("render", "--finding", "f", "--question", "q?",
             "--recommendation", "", "--why", "the reader stalled")
    assert r.stdout.split("\n")[2] == (
        "💡 Recommendation: none given — the reader stalled")


def test_cli_complete_appends_and_leaves_alone(tmp_path):
    bare = tmp_path / "bare.md"
    bare.write_text("Nothing was decided. Should it go ahead?\n",
                    encoding="utf-8")
    r = _cli("complete", str(bare), "--question", "Go ahead?",
             "--who", "the critic")
    assert r.returncode == 0, r.stderr
    got = ce.parse(r.stdout)
    assert got.recommendation is None
    assert got.why == "the critic stated no recommendation"

    declared = tmp_path / "declared.md"
    body = PROSE + "\n\n" + ce.render_with_block(_escalation(RECORDS[2]))
    declared.write_text(body, encoding="utf-8")
    r = _cli("complete", str(declared), "--question", "x?", "--who", "y")
    assert r.returncode == 0, r.stderr
    assert r.stdout == body + "\n"


@pytest.mark.parametrize("record", RECORDS, ids=IDS)
def test_cli_check_passes_each_rendered_fixture(record, tmp_path):
    path = tmp_path / "body.md"
    path.write_text(ce.render_with_block(_escalation(record)), encoding="utf-8")
    r = _cli("check", str(path))
    assert r.returncode == 0, r.stderr
    assert r.stderr == ""


def test_cli_check_fails_a_body_missing_a_line(tmp_path):
    path = tmp_path / "body.md"
    path.write_text(F + "\n" + Q + "\n", encoding="utf-8")
    r = _cli("check", str(path))
    assert r.returncode == 1
    assert "Recommendation" in r.stderr and "missing" in r.stderr


def test_cli_check_of_a_body_with_no_fence_imports_only_the_standard_library(
        tmp_path):
    """The shell tier runs these on a body with no fence; the console module
    must not drag `planning_escalation`'s imports in to do it."""
    path = tmp_path / "body.md"
    path.write_text(GOOD, encoding="utf-8")
    probe = (
        "import sys; sys.path.insert(0, 'scripts'); sys.argv[1:] = "
        f"['check', {str(path)!r}]; import console_escalation as c; "
        "rc = c.main(); "
        "assert 'planning_escalation' not in sys.modules, 'imported'; "
        "print(rc)"
    )
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                       text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "0"


# --------------------------------------------------------------------------- #
# 9. the fixtures                                                              #
# --------------------------------------------------------------------------- #


def test_the_fixture_file_holds_exactly_the_four_cases():
    assert ce.load_fixtures() == RECORDS
    assert IDS == ["DRE-3879", "DRE-3885", "DRE-3887", "DRE-3889"]
    keys = {"card", "route", "finding", "question", "recommendation",
            "recommendation_why", "choices", "recommended"}
    for record in RECORDS:
        assert set(record) == keys
        for choice in record["choices"]:
            assert set(choice) == {"id", "label", "effect", "outcome"}
    assert [r["route"] for r in RECORDS] == [
        "one-off-critic QUESTION", "one-off-critic QUESTION",
        "one-off-critic QUESTION", "one-off-critic NO_RESULT"]


@pytest.mark.parametrize("record", RECORDS, ids=IDS)
def test_each_fixture_recommends_one_of_its_own_choices(record):
    labels = {c["id"]: c["label"] for c in record["choices"]}
    assert record["recommendation"]
    assert record["recommendation"] == labels[record["recommended"]]
    assert 2 <= len(record["choices"]) <= 3
    built = ce.block(_escalation(record))
    assert built is not None
    assert planning_escalation.choices_problem(built) is None


# --------------------------------------------------------------------------- #
# 10. who reads the lines                                                      #
# --------------------------------------------------------------------------- #


def test_the_docstring_says_who_reads_the_lines():
    doc = " ".join((ce.__doc__ or "").split())
    for needle in ("a person", "Linear", "hygiene", "Green Light lane",
                   "DRE-6196", "DRE-3894", "2026-10-02", "canceled",
                   "re-filed", "live epic", "DRE-6168",
                   "docs/escalation-choices.md", "two repos"):
        assert needle in doc, needle
