"""RED-first tests: a planning escalation carries its question as a structured
block the console can show as buttons (DRE-6168).

The CEO approved the design on 2026-10-07: a card waiting on him shows its
question, its choices as buttons and the recommended one at the top of the card
panel, and one click answers it. This repository is the producer half. Every
`🙋 planning-escalation:` note is composed by `escalation_comment`, and its
choices used to live inside sentences — DRE-5260's question read "May we add …
Or the time alone? I recommend the pair" — so nothing could render them without
guessing.

WHAT THIS PINS, one section per acceptance criterion:

  1. A valid choices file adds ONE fenced `escalation-choices` block as the last
     thing in the comment, the prose above it is byte for byte today's comment,
     and the block parses back to the same fields.
  2. An invalid one is refused with a run-log line naming the rule, and the
     escalation still posts the prose and parks the card in Green Light. A
     missing file is the normal case and silent; a file that is not JSON is
     logged.
  3. All five planner prompt copies in plan.yml and the planner brief's
     escalation section ask for the route's choices file and name the five
     top-level fields — in the same words, so no copy is left asking for prose
     only.
  4. The schema is documented, and its example is DRE-5260's real question.

Run: cd bureau-pipeline && python3 -m pytest tests/test_escalation_choices.py -v
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import linear_ops  # noqa: E402
import planning_escalation  # noqa: E402

WF = ROOT / ".github" / "workflows" / "plan.yml"
BRIEF = ROOT / "briefs" / "planner.md"
DOC = ROOT / "docs" / "escalation-choices.md"

CARD = "DRE-5260"

REASON = (
    "The form's Answer panel used to say when its suggested edits were last "
    "read, and the restored panel does not. May we add the time and a Refresh "
    "control, or the time alone? I recommend the pair: a time you cannot act "
    "on is a dead end."
)

#: DRE-5260's real question, complete — the card's documented example.
DRE_5260 = {
    "question": "Should the form's Answer panel say when its suggested edits "
                "were last read, and let you re-read them?",
    "context": "The Answer panel used to show a small gray line under its "
               "suggested edits with the time they were last read and a "
               "Refresh control. The restored panel no longer says, so a "
               "reader cannot tell whether a suggestion is minutes or hours "
               "old.",
    "choices": [
        {"id": "time-and-refresh", "label": "Time + Refresh",
         "effect": "The panel shows the time the suggestions were last read "
                   "and a Refresh control that re-reads them.",
         "preview": "Proposals as of 3:42 PM · Refresh", "outcome": "proceed"},
        {"id": "time-only", "label": "Time only",
         "effect": "The panel shows the time alone, with no way to re-read "
                   "from there.",
         "preview": "Proposals as of 3:42 PM", "outcome": "proceed"},
        {"id": "leave-it-out", "label": "Leave it out",
         "effect": "The panel stays as it is and this card is canceled.",
         "outcome": "close"},
    ],
    "recommended": "time-and-refresh",
    "why": "a time you cannot act on is a dead end",
}

TOP_LEVEL = ("question", "context", "choices", "recommended", "why")


class _Card:
    """One card still in Planning, with Linear stubbed at the seams
    `escalate()` reads and writes through."""

    def __init__(self, lane: str = planning_escalation.ORIGIN):
        self.lane = lane
        self.posted: list[str] = []
        self.states: list[str] = []

    def run(self, fn):
        def post(identifier, body):
            self.posted.append(body)

        def move(identifier, lane, *rest):
            self.states.append(lane)

        def read(identifier, **kw):
            return {
                "id": "issue-id", "identifier": identifier, "title": "a card",
                "team": {"id": "team-id"},
                "state": {"name": self.lane, "type": "unstarted"},
                "labels": {"nodes": []}, "children": {"nodes": []},
            }

        with patch.object(linear_ops, "comment_timeline", return_value=[]), \
                patch.object(linear_ops, "comment_bodies", return_value=[]), \
                patch.object(linear_ops, "cmd_comment", side_effect=post), \
                patch.object(linear_ops, "cmd_state", side_effect=move), \
                patch.object(linear_ops, "get_issue", side_effect=read), \
                patch.object(linear_ops, "count_comments", return_value=0):
            return fn()


def _write(tmp_path: Path, block) -> str:
    path = tmp_path / "planner-escalation-choices.json"
    path.write_text(block if isinstance(block, str) else json.dumps(block),
                    encoding="utf-8")
    return str(path)


def _escalate(tmp_path: Path, choices_path: str | None,
              reason: str = REASON) -> _Card:
    card = _Card()
    argv = ["escalate", CARD, "--why", reason]
    if choices_path is not None:
        argv += ["--choices-file", choices_path]
    assert card.run(lambda: planning_escalation.main(argv)) == 0
    return card


# ===========================================================================
# 1. A valid answer: today's prose, then the block, last
# ===========================================================================
class TestAValidAnswerAddsTheBlock:
    def test_the_prose_is_todays_comment_and_the_block_comes_last(self, tmp_path):
        card = _escalate(tmp_path, _write(tmp_path, DRE_5260))
        assert len(card.posted) == 1
        body = card.posted[0]
        today = planning_escalation.escalation_comment(CARD, REASON)
        assert body.startswith(today)
        rest = body[len(today):]
        assert rest.startswith("\n\n```escalation-choices\n")
        # The block is the LAST thing: nothing follows its closing fence.
        assert body.rstrip("\n").endswith("```")
        assert body.count("```escalation-choices") == 1
        assert body.index("```escalation-choices") > body.index(
            "Answer it here and move the card back")

    def test_the_block_parses_back_to_the_same_fields(self, tmp_path):
        card = _escalate(tmp_path, _write(tmp_path, DRE_5260))
        assert planning_escalation.parse_choices(card.posted[0]) == DRE_5260

    def test_the_card_still_parks_in_green_light(self, tmp_path):
        card = _escalate(tmp_path, _write(tmp_path, DRE_5260))
        assert card.states == [planning_escalation.destination()] == ["Green Light"]

    def test_the_reason_hygiene_reads_is_unchanged(self, tmp_path):
        """`hygiene_green_light` reads the reason up to the parked paragraph;
        a block after the closing ask cannot change what it reads."""
        import hygiene_green_light
        card = _escalate(tmp_path, _write(tmp_path, DRE_5260))
        today = planning_escalation.escalation_comment(CARD, REASON)
        assert hygiene_green_light.escalation_reason(card.posted[0]) == \
            hygiene_green_light.escalation_reason(today)

    def test_the_block_is_its_own_fenced_json(self):
        rendered = planning_escalation.choices_block(DRE_5260)
        assert rendered.startswith("```escalation-choices\n")
        assert rendered.endswith("\n```")
        assert json.loads(rendered.split("\n", 1)[1].rsplit("\n", 1)[0]) == DRE_5260

    def test_no_block_parses_from_todays_comment(self):
        today = planning_escalation.escalation_comment(CARD, REASON)
        assert planning_escalation.parse_choices(today) is None

    def test_a_refused_reason_posts_no_block(self, tmp_path):
        """The block rides only on a reason fit to post: a refused reason
        posts the refusal line alone, as today."""
        leak = "The fix is in scripts/plan.py and nobody owns it."
        card = _escalate(tmp_path, _write(tmp_path, DRE_5260), reason=leak)
        assert card.posted == [planning_escalation.escalation_comment(CARD, leak)]
        assert "escalation-choices" not in card.posted[0]

    def test_the_stand_down_note_never_carries_a_block(self, tmp_path):
        """A card that moved on gets the stand-down note, which asks nothing."""
        card = _Card(lane="In Progress")
        assert card.run(lambda: planning_escalation.main(
            ["escalate", CARD, "--why", REASON,
             "--choices-file", _write(tmp_path, DRE_5260)])) == 0
        assert len(card.posted) == 1
        assert planning_escalation.STOOD_DOWN_TAG in card.posted[0]
        assert "escalation-choices" not in card.posted[0]
        assert card.states == []


# ===========================================================================
# 2. An invalid answer: refused in the log, prose posted, card parked
# ===========================================================================
def _mutate(fn) -> dict:
    block = copy.deepcopy(DRE_5260)
    fn(block)
    return block


def _one_choice(b):
    del b["choices"][1:]


def _five_choices(b):
    extra = copy.deepcopy(b["choices"][1])
    b["choices"] += [dict(extra, id=f"extra-{n}") for n in range(2)]
    assert len(b["choices"]) == 5


def _unknown_recommended(b):
    b["recommended"] = "time-and-a-half"


def _shared_id(b):
    b["choices"][1]["id"] = b["choices"][0]["id"]


def _bad_outcome(b):
    b["choices"][2]["outcome"] = "defer"


def _card_number_label(b):
    b["choices"][0]["label"] = "As in DRE-5260"


INVALID = {
    "one choice": (_one_choice, "2 to 4"),
    "five choices": (_five_choices, "2 to 4"),
    "a recommended id that does not exist": (_unknown_recommended, "recommended"),
    "two choices sharing an id": (_shared_id, "share"),
    "an outcome outside the three words": (_bad_outcome, "outcome"),
    "a label naming a card number": (_card_number_label, "card number"),
}


class TestAnInvalidAnswerFallsBackToProse:
    @pytest.mark.parametrize("case", sorted(INVALID))
    def test_it_is_refused_naming_the_rule(self, case, tmp_path, capsys):
        mutate, rule = INVALID[case]
        card = _escalate(tmp_path, _write(tmp_path, _mutate(mutate)))
        err = capsys.readouterr().err
        refused = [line for line in err.splitlines()
                   if line.startswith("escalation-choices refused: ")]
        assert len(refused) == 1, err
        assert rule in refused[0]
        # The escalation still happens exactly as today.
        assert card.posted == [planning_escalation.escalation_comment(CARD, REASON)]
        assert card.states == ["Green Light"]
        # Nothing about the refusal reaches the card.
        assert "refused" not in card.posted[0]

    @pytest.mark.parametrize("case", sorted(INVALID))
    def test_the_validator_names_a_rule(self, case):
        mutate, rule = INVALID[case]
        problem = planning_escalation.choices_problem(_mutate(mutate))
        assert problem is not None and rule in problem

    def test_the_documented_example_is_valid(self):
        assert planning_escalation.choices_problem(DRE_5260) is None

    def test_a_missing_file_is_silent(self, tmp_path, capsys):
        card = _escalate(tmp_path, str(tmp_path / "never-written.json"))
        assert "escalation-choices" not in capsys.readouterr().err
        assert card.posted == [planning_escalation.escalation_comment(CARD, REASON)]
        assert card.states == ["Green Light"]

    def test_no_choices_flag_is_todays_comment(self, tmp_path, capsys):
        card = _escalate(tmp_path, None)
        assert "escalation-choices" not in capsys.readouterr().err
        assert card.posted == [planning_escalation.escalation_comment(CARD, REASON)]

    def test_a_file_that_is_not_json_is_logged(self, tmp_path, capsys):
        card = _escalate(tmp_path, _write(tmp_path, "Time + Refresh, I think"))
        err = capsys.readouterr().err
        assert "escalation-choices refused: " in err
        assert "not JSON" in err
        assert card.posted == [planning_escalation.escalation_comment(CARD, REASON)]
        assert card.states == ["Green Light"]

    @pytest.mark.parametrize("mutate, rule", [
        (lambda b: b.update(extra="x"), "key"),
        (lambda b: b["choices"][0].update(colour="red"), "key"),
        (lambda b: b["choices"][0].update(id="Time-And-Refresh"), "slug"),
        (lambda b: b["choices"][0].update(id="1st"), "slug"),
        (lambda b: b.update(question=""), "question"),
        (lambda b: b.pop("context"), "context"),
        (lambda b: b.update(why="   "), "why"),
        (lambda b: b["choices"][1].update(effect=""), "effect"),
        (lambda b: b["choices"][1].pop("label"), "label"),
        (lambda b: b["choices"][1].pop("outcome"), "outcome"),
        (lambda b: b.update(question="Should we edit scripts/answer.py?"), "file path"),
        (lambda b: b["choices"][0].update(effect="Runs git push on main."), "command"),
        (lambda b: b["choices"][1].update(effect="Done after DRE-12 lands."), "card number"),
        (lambda b: b["choices"][0].update(preview="```\ncode\n```"), "code fence"),
        (lambda b: b["choices"][0].update(preview="VERDICT: APPROVE"), "verdict marker"),
        (lambda b: b.update(choices="time-only"), "2 to 4"),
        (lambda b: b["choices"].__setitem__(0, "time-only"), "choice"),
    ])
    def test_every_rule_is_enforced(self, mutate, rule):
        problem = planning_escalation.choices_problem(_mutate(mutate))
        assert problem is not None and rule in problem, problem

    def test_a_block_that_is_not_an_object_is_refused(self):
        assert planning_escalation.choices_problem([DRE_5260]) is not None

    def test_a_preview_may_carry_a_time_and_a_product_string(self):
        block = _mutate(lambda b: b["choices"][0].update(
            preview="Proposals as of 3:42 PM · Refresh\nPortico 2.4"))
        assert planning_escalation.choices_problem(block) is None

    def test_replan_is_an_outcome(self):
        block = _mutate(lambda b: b["choices"][1].update(outcome="replan"))
        assert planning_escalation.choices_problem(block) is None


# ===========================================================================
# 3. The prompts: six copies, one paragraph, each naming its route's file
# ===========================================================================
EPIC_FILE = "planner-escalation-choices.json"
ONE_OFF_FILE = "one-off-question-choices.json"

PROMPT_STEPS = {
    "Plan epic": EPIC_FILE,
    "Plan epic — on the next rung": EPIC_FILE,
    "Roll-up route — split into child epics": EPIC_FILE,
    "Roll-up route — split into child epics — on the next rung": EPIC_FILE,
    "One-off revision — the planner answers the critic": ONE_OFF_FILE,
}

_PATH = re.compile(
    r"`?(?:\$\{\{ runner\.temp \}\}|\$RUNNER_TEMP)/[\w.-]+-choices\.json`?")


def _steps() -> list:
    jobs = yaml.safe_load(WF.read_text(encoding="utf-8"))["jobs"]
    return [step for job in jobs.values() for step in (job.get("steps") or [])]


def _named(name: str) -> dict:
    found = [step for step in _steps() if step.get("name") == name]
    assert len(found) == 1, name
    return found[0]


def _brief_section() -> str:
    text = BRIEF.read_text(encoding="utf-8")
    start = text.index("## When NOT to plan — hand-planning is an escalation (DRE-2848)")
    end = text.find("\n## ", start + 1)
    return text[start:] if end < 0 else text[start:end]


def _choices_paragraph(text: str) -> str:
    """The choices instruction, path replaced and whitespace collapsed, so the
    six copies can be compared word for word."""
    flat = " ".join(_PATH.sub("PATH", text).split())
    last = "is dropped and the reason is posted alone."
    start = flat.index("Then write the choices as JSON")
    return flat[start:flat.index(last, start) + len(last)]


def _copies() -> dict:
    copies = {name: _named(name)["with"]["prompt"] for name in PROMPT_STEPS}
    copies["briefs/planner.md"] = _brief_section()
    return copies


class TestEveryPromptAsksForTheChoices:
    @pytest.mark.parametrize("name", sorted(PROMPT_STEPS))
    def test_the_workflow_prompt_names_its_routes_file_and_fields(self, name):
        prompt = _named(name)["with"]["prompt"]
        assert "${{ runner.temp }}/" + PROMPT_STEPS[name] in prompt
        other = ONE_OFF_FILE if PROMPT_STEPS[name] == EPIC_FILE else EPIC_FILE
        assert other not in prompt
        paragraph = _choices_paragraph(prompt)
        for field in TOP_LEVEL + ("id", "label", "effect", "preview", "outcome"):
            assert f"`{field}`" in paragraph, (name, field)

    def test_the_brief_names_the_file_and_fields(self):
        section = _brief_section()
        assert f"$RUNNER_TEMP/{EPIC_FILE}" in section
        paragraph = _choices_paragraph(section)
        for field in TOP_LEVEL:
            assert f"`{field}`" in paragraph, field

    def test_all_six_copies_say_the_same_words(self):
        paragraphs = {name: _choices_paragraph(text)
                      for name, text in _copies().items()}
        assert len(paragraphs) == 6
        assert len(set(paragraphs.values())) == 1, paragraphs

    def test_the_reason_paragraph_is_kept_not_replaced(self):
        """The reason file is still asked for, ahead of the choices."""
        for name, text in _copies().items():
            reason = "one-off-question.md" if name.startswith("One-off") \
                else "planner-escalation.txt"
            assert reason in text, name
            assert text.index(reason) < text.index("Then write the choices as JSON")


# ===========================================================================
# 4. The posting steps hand the file over
# ===========================================================================
class TestThePostingStepsPassTheFile:
    def test_the_epic_route_passes_its_file(self):
        run = _named("Planner escalation — hand-planning parks for the CEO")["run"]
        assert "--choices-file" in run
        assert "${{ runner.temp }}/" + EPIC_FILE in run

    def test_the_returned_child_default_reason_carries_no_block(self):
        """The reason the step writes itself is prose only: no planner choices
        may ride on it."""
        run = _named("Planner escalation — hand-planning parks for the CEO")["run"]
        branch = run[run.index('if [ "$RETURNED_CHILD" = "true" ]'):
                     run.index("planning_escalation.py escalate")]
        assert 'rm -f "$CHOICES"' in branch

    def test_the_roll_up_route_passes_its_file(self):
        run = _named("Roll-up route — check the split")["run"]
        assert "--choices-file" in run
        assert "${{ runner.temp }}/" + EPIC_FILE in run

    def test_the_one_off_route_passes_its_file_only_for_the_planners_question(self):
        step = _named("One-off critic — escalate")
        assert "--choices-file" in step["run"]
        assert "${{ runner.temp }}/" + ONE_OFF_FILE in step["run"]
        # The critic's own question stays prose only.
        assert "asked" in step["run"]
        assert "steps.oorevised.outputs.outcome" in str(step.get("env"))

    def test_a_revision_never_reads_a_stale_choices_file(self):
        run = _named("One-off revision — planner context")["run"]
        clear = run[run.index("rm -f"):]
        assert "${{ runner.temp }}/" + ONE_OFF_FILE in clear

    def test_the_classifier_refusal_passes_no_file(self):
        run = _named("Classification refused — park the card for the CEO")["run"]
        assert "--choices-file" not in run


# ===========================================================================
# 5. The documented schema and its example
# ===========================================================================
class TestTheSchemaIsDocumented:
    def test_the_page_exists_and_names_every_key(self):
        text = DOC.read_text(encoding="utf-8")
        for key in TOP_LEVEL + ("id", "label", "effect", "preview", "outcome"):
            assert f"`{key}`" in text, key
        for word in ("proceed", "replan", "close"):
            assert f"`{word}`" in text, word

    def test_the_example_is_dre_5260(self):
        text = DOC.read_text(encoding="utf-8")
        block = planning_escalation.parse_choices(text)
        assert block == DRE_5260
        labels = [c["label"] for c in block["choices"]]
        assert labels == ["Time + Refresh", "Time only", "Leave it out"]
        assert block["recommended"] == "time-and-refresh"
        assert block["choices"][2]["outcome"] == "close"
        assert block["why"] == "a time you cannot act on is a dead end"
        assert planning_escalation.choices_problem(block) is None
