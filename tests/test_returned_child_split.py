"""RED-first tests: a returned child of an epic is split into SIBLINGS (DRE-5242).

A card that comes back to Planning is read afresh (DRE-4370). When the fresh
read says `epic` and the card is itself a child of an epic, the epic route used
to file the pieces UNDER the returned card — `linear_ops.py subissue <this
card>` — so the child became an epic nested inside its own in-progress parent,
got a plan artifact of its own and waited in Green Light. The card-quality
standard says the opposite: "A card has no children", and under "How to split"
the pieces are siblings under the same epic and the original is canceled, never
Done.

WHAT THIS PINS, one section per acceptance criterion:

  1. `planning_route.returned_child` reports a returned child of an epic — live
     `epic` stamp, a return receipt, an epic parent — as returned with the
     parent's identifier, and every other case as not. `decide` writes the
     answer as step outputs: `returned_child=true|false` and `parent`.
  2. Both copies of the epic-route planner prompt in `plan.yml` file the pieces
     with `mid_epic.py discovery <PARENT> --kind addition … --verdict FLEET` and
     cancel the original when the step output is true — and render byte for
     byte what they rendered before when it is false.
  3. The run does not park a split card in Green Light for want of an
     escalation reason, and the lane contract names the planner as the writer
     that records the cancel.

On stamp order. The canonical returned child carries its fresh `epic` stamp
AFTER the receipt: `plan.yml` classifies before it routes, so by the time
`decide` reads the card the classifier has already stamped it afresh — the
same thread DRE-4370's `test_a_stamp_newer_than_the_receipt_still_wins` pins.
That card is the one this route exists for, so it is reported returned. The
ordering that is NOT is an `epic` stamp from BEFORE the receipt: a planner stamp
there is void, and a hand stamp there is a person's decision made before the
return, not the return's reading.

Run: cd bureau-pipeline && python3 -m pytest tests/test_returned_child_split.py -v
"""
from __future__ import annotations

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

import lane_contract  # noqa: E402
import planning_route  # noqa: E402
import planning_shape  # noqa: E402

WF = ROOT / ".github" / "workflows" / "plan.yml"

CARD = "DRE-200"
PARENT = "DRE-100"
MODEL = "claude-fable-5-1"


# ===========================================================================
# the thread a returned card carries
# ===========================================================================
def _handback_receipt() -> str:
    """The comment agent-task.yml's hand-back branch posts, in its shape."""
    import planner_score

    return (
        f"{planner_score.HANDBACK_RECEIPT_PREFIX} this card was dispatched as "
        "one piece of work and is an epic's worth.\n\n"
        "1. the prompt names the ceiling — agent-task.yml — does not depend on "
        "piece 2\n\nRun: https://github.com/o/r/actions/runs/1"
    )


def _replan_receipt() -> str:
    """The comment `dead_run.decide` posts for a turn death before green."""
    import dead_run

    return (
        f"{dead_run.REPLAN_MARK} the agent ran out of steps — it hit 400 of "
        "400 turns — and the run stopped at ⏳ 2/5 failing tests written, "
        "before implementation green."
    )


def _planner_stamp(shape: str, why: str = "the classifier's read") -> str:
    return planning_shape.shape_comment(
        shape, why, by=planning_shape.BY_PLANNER, model=MODEL)


def _hand_stamp(shape: str, why: str = "the operator's call") -> str:
    return planning_shape.shape_comment(shape, why, by=planning_shape.BY_HAND)


def _returned(receipt=None, fresh: str = "epic") -> list:
    """A one-off, handed back, and read afresh as `fresh` — in that order."""
    return [_planner_stamp("one-off"), receipt or _handback_receipt(),
            _planner_stamp(fresh, "the split, read after the hand-back")]


def _parent(**over) -> dict:
    parent = {"identifier": PARENT, "title": "[EPIC] bureau-pipeline: the work",
              "has_children": True, "shape": "epic"}
    parent.update(over)
    return parent


RECEIPTS = pytest.mark.parametrize(
    "receipt", [_handback_receipt(), _replan_receipt()],
    ids=["hand-back", "replan"])


# ===========================================================================
# 1. the answer
# ===========================================================================
class TestAReturnedChildOfAnEpic:
    @RECEIPTS
    def test_is_reported_returned_with_its_parent(self, receipt):
        answer = planning_route.returned_child(_returned(receipt), _parent())
        assert answer.returned is True
        assert answer.parent == PARENT

    def test_the_parent_is_an_epic_by_its_children_when_it_carries_no_stamp(self):
        """An epic planned before the shape stamp existed carries none; its
        children are what make it one (`mid_epic.is_epic`)."""
        answer = planning_route.returned_child(
            _returned(), _parent(shape=None, title="the work"))
        assert answer.returned is True
        assert answer.parent == PARENT

    def test_a_person_stamping_it_epic_after_the_return_is_the_same_reading(self):
        bodies = [_planner_stamp("one-off"), _handback_receipt(), _hand_stamp("epic")]
        assert planning_route.returned_child(bodies, _parent()).returned is True


class TestEveryOtherCaseIsNot:
    def test_a_parentless_returned_card(self):
        """No parent: today's route stands — the card becomes an epic whose
        children are the pieces."""
        answer = planning_route.returned_child(_returned(), None)
        assert answer.returned is False
        assert answer.parent == ""

    def test_a_child_with_no_receipt(self):
        bodies = [_planner_stamp("epic")]
        answer = planning_route.returned_child(bodies, _parent())
        assert answer.returned is False
        assert answer.parent == ""

    def test_a_child_whose_planner_stamp_predates_the_receipt(self):
        """Void under DRE-4370: the card has not been read afresh yet, so it
        carries no live shape at all."""
        bodies = [_planner_stamp("epic"), _handback_receipt()]
        assert planning_route.returned_child(bodies, _parent()).returned is False

    def test_a_child_whose_hand_stamp_predates_the_receipt(self):
        """A hand stamp survives the receipt (DRE-4370), so the live shape is
        still `epic` — but it is a person's decision made before the card came
        back, not the return's reading, and splitting on it would overrule
        him."""
        bodies = [_hand_stamp("epic"), _handback_receipt()]
        assert planning_shape.shape_on(bodies) == "epic", "precondition"
        assert planning_route.returned_child(bodies, _parent()).returned is False

    def test_a_child_read_afresh_as_a_one_off(self):
        answer = planning_route.returned_child(_returned(fresh="one-off"), _parent())
        assert answer.returned is False

    def test_a_child_read_afresh_as_a_roll_up(self):
        answer = planning_route.returned_child(_returned(fresh="roll-up"), _parent())
        assert answer.returned is False

    def test_a_child_whose_parent_is_not_an_epic(self):
        """A parent stamped one-off is not an epic, whatever it holds."""
        answer = planning_route.returned_child(
            _returned(), _parent(shape="one-off", title="a card"))
        assert answer.returned is False

    def test_a_returned_card_that_already_has_children_of_its_own(self):
        """Its pieces were already filed under it. Canceling it would strand
        them; it is re-planned as the epic it already is."""
        answer = planning_route.returned_child(
            _returned(), _parent(), has_children=True)
        assert answer.returned is False

    def test_a_card_carrying_two_shapes(self):
        bodies = _returned() + [_planner_stamp("one-off", "a second read")]
        assert planning_route.returned_child(bodies, _parent()).returned is False

    def test_every_negative_says_why(self):
        answer = planning_route.returned_child(_returned(), None)
        assert answer.reason.strip()


# ===========================================================================
# 1b. decide writes it as step outputs
# ===========================================================================
def _family(parent_identifier=PARENT, *, title="[EPIC] bureau-pipeline: the work",
            own_children=False) -> dict:
    parent = None
    if parent_identifier:
        parent = {"identifier": parent_identifier, "title": title,
                  "children": {"nodes": [{"id": "c"}]}}
    return {"issue": {"children": {"nodes": [{"id": "k"}] if own_children else []},
                      "parent": parent}}


def _decide(tmp_path, threads: dict, family: dict):
    """Run `decide CARD --github-output F` against a faked Linear, returning
    `(outputs, gql calls)`."""
    import linear_ops

    out = tmp_path / "out.txt"
    calls: list = []

    def gql(query, variables=None):
        calls.append(variables)
        return family

    with patch.object(linear_ops, "comment_bodies",
                      side_effect=lambda i: list(threads.get(i, []))), \
         patch.object(linear_ops, "gql", side_effect=gql), \
         patch.object(linear_ops, "cmd_comment"), \
         patch.object(linear_ops, "count_comments", return_value=0):
        assert planning_route.main(["decide", CARD, "--github-output", str(out)]) == 0
    pairs = dict(line.split("=", 1) for line in out.read_text().splitlines() if line)
    return pairs, calls


class TestDecideWritesTheStepOutputs:
    def test_a_returned_child_is_true_with_its_parent(self, tmp_path):
        outputs, _ = _decide(
            tmp_path, {CARD: _returned(), PARENT: [_planner_stamp("epic")]}, _family())
        assert outputs["route"] == "epic"
        assert outputs["returned_child"] == "true"
        assert outputs["parent"] == PARENT

    def test_a_parentless_returned_card_is_false(self, tmp_path):
        outputs, _ = _decide(tmp_path, {CARD: _returned()}, _family(None))
        assert outputs["route"] == "epic"
        assert outputs["returned_child"] == "false"
        assert outputs["parent"] == ""

    def test_a_card_that_already_has_children_is_false(self, tmp_path):
        outputs, _ = _decide(
            tmp_path, {CARD: _returned(), PARENT: [_planner_stamp("epic")]},
            _family(own_children=True))
        assert outputs["returned_child"] == "false"

    def test_an_unreturned_epic_is_false_and_costs_no_family_read(self, tmp_path):
        """Every epic passes through `decide`; only a returned one pays for the
        parent read."""
        outputs, calls = _decide(tmp_path, {CARD: [_planner_stamp("epic")]}, _family())
        assert outputs["returned_child"] == "false"
        assert calls == []

    def test_a_refused_card_is_false(self, tmp_path):
        outputs, _ = _decide(tmp_path, {CARD: []}, _family())
        assert outputs["refused"] == "true"
        assert outputs["returned_child"] == "false"


# ===========================================================================
# 2. both planner prompts
# ===========================================================================
GATE = "steps.shape.outputs.returned_child == 'true' && format("
ARGS = {
    "steps.shape.outputs.parent": PARENT,
    "github.event.client_payload.identifier": CARD,
}


def _steps() -> list:
    jobs = yaml.safe_load(WF.read_text(encoding="utf-8"))["jobs"]
    return [s for job in jobs.values() for s in job.get("steps") or []]


def _step(step_id: str) -> dict:
    return next(s for s in _steps() if s.get("id") == step_id)


def _planner_prompts() -> dict:
    """The epic route's two planner prompts: the first attempt and the same
    step re-run on the next rung (DRE-3970)."""
    return {sid: _step(sid)["with"]["prompt"] for sid in ("claude", "claude_retry")}


def _gated(prompt: str) -> tuple:
    """`(start, end, expression)` of the ONE `${{ }}` the step output gates."""
    starts = [m.start() for m in re.finditer(re.escape("${{ " + GATE), prompt)]
    assert len(starts) == 1, f"expected one gated expression, found {len(starts)}"
    start = starts[0]
    end = prompt.index("}}", start) + 2
    return start, end, prompt[start + 3:end - 2].strip()


def _literal(text: str, i: int) -> tuple:
    """A GitHub-expression string literal starting at `text[i] == "'"`:
    `(value, index after it)`. A doubled quote is one quote."""
    assert text[i] == "'"
    out, i = [], i + 1
    while True:
        if text[i] == "'":
            if text[i + 1:i + 2] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), i + 1
        out.append(text[i])
        i += 1


def _render(prompt: str, returned: bool) -> str:
    """The prompt as the runner hands it over, for the gated expression only:
    `format(template, *refs)` when the output is true, '' when it is not."""
    start, end, expr = _gated(prompt)
    if not returned:
        return prompt[:start] + prompt[end:]
    assert expr.endswith("|| ''"), "the false branch must render nothing"
    body = expr[len(GATE):]
    template, i = _literal(body, body.index("'"))
    rest = body[i:body.rindex(")")]
    refs = [r.strip() for r in rest.split(",") if r.strip()]
    rendered = template
    for n, ref in enumerate(refs):
        assert ref in ARGS, f"unexpected format argument {ref!r}"
        rendered = rendered.replace("{%d}" % n, ARGS[ref])
    assert not re.search(r"\{\d\}", rendered), "an unbound placeholder"
    return prompt[:start] + rendered + prompt[end:]


PROMPTS = pytest.mark.parametrize("sid", ["claude", "claude_retry"])


class TestThePlannerPromptSplitsIntoSiblings:
    def test_there_are_exactly_two_copies(self):
        prompts = _planner_prompts()
        assert len(prompts) == 2
        for prompt in prompts.values():
            assert "linear_ops.py subissue" in prompt

    @PROMPTS
    def test_the_pieces_are_filed_as_siblings_under_the_parent(self, sid):
        text = _render(_planner_prompts()[sid], returned=True)
        assert f"mid_epic.py discovery {PARENT} --kind addition" in text
        assert "--verdict FLEET" in text
        assert "--because" in text and "return receipt" in text
        assert "--title" in text and "--body" in text

    @PROMPTS
    def test_this_card_is_never_given_sub_issues(self, sid):
        text = _render(_planner_prompts()[sid], returned=True)
        assert f"never run `linear_ops.py subissue` on {CARD}" in text

    @PROMPTS
    def test_the_original_is_canceled_with_a_comment_naming_the_siblings(self, sid):
        text = _render(_planner_prompts()[sid], returned=True)
        assert f"linear_ops.py state {CARD} Canceled" in text
        assert f"linear_ops.py comment {CARD}" in text
        assert "naming every sibling" in text
        assert "never Done" in text

    @PROMPTS
    def test_no_artifact_and_no_green_light_for_the_original(self, sid):
        text = _render(_planner_prompts()[sid], returned=True)
        assert "write no plan artifact" in text
        assert "does not go to Green Light" in text

    @PROMPTS
    def test_the_instruction_sits_beside_the_subissue_line(self, sid):
        prompt = _planner_prompts()[sid]
        _, end, _ = _gated(prompt)
        assert 0 < prompt.index("linear_ops.py subissue", end) - end < 200

    @PROMPTS
    def test_it_is_gated_on_the_step_output(self, sid):
        _, _, expr = _gated(_planner_prompts()[sid])
        assert expr.startswith(GATE)
        assert expr.endswith("|| ''")

    @PROMPTS
    def test_false_renders_the_prompt_unchanged(self, sid):
        """Nothing but the gated expression is new, and it closes an existing
        line — so the false rendering adds no text and no blank line."""
        prompt = _planner_prompts()[sid]
        start, end, _ = _gated(prompt)
        assert prompt[end] == "\n", "the expression must close an existing line"
        assert prompt[start - 1] != "\n", "the expression must not open a line"
        text = _render(prompt, returned=False)
        for word in ("mid_epic.py", "Canceled", "RETURNED CHILD", "returned_child"):
            assert word not in text, f"{word!r} leaks into the false rendering"

    def test_the_two_copies_say_the_same_thing(self):
        a, b = (_gated(p)[2] for p in _planner_prompts().values())
        assert a == b

    def test_the_expression_reads_nothing_but_step_outputs_and_the_payload(self):
        """The payload identifier is a Linear key, never card text: nothing
        untrusted is interpolated outside the fence."""
        _, _, expr = _gated(_planner_prompts()["claude"])
        refs = set(re.findall(r"\b(?:steps|github|inputs|vars|env)\.[\w.]+", expr))
        assert refs <= set(ARGS) | {"steps.shape.outputs.returned_child"}


# ===========================================================================
# 3. the rest of the run, and the record
# ===========================================================================
class TestTheSplitCardIsNotParkedForTheCEO:
    def test_the_escalation_step_reads_the_step_output(self):
        step = next(s for s in _steps()
                    if "hand-planning parks" in (s.get("name") or ""))
        assert (step.get("env") or {}).get("RETURNED_CHILD") == \
            "${{ steps.shape.outputs.returned_child }}"
        run = step["run"]
        assert '"$RETURNED_CHILD" = "true"' in run
        # Only when the planner wrote no reason: a returned child the planner
        # could not cut safely still escalates the ordinary way.
        assert "planner-escalation.txt" in run and "-s" in run
        assert run.index('"$RETURNED_CHILD"') < run.index("planning_escalation.py escalate")


_FAKE_LINEAR_OPS = """\
import os, sys
assert sys.argv[1:] == ["state-of", os.environ["EXPECT_CARD"]], sys.argv
print(os.environ.get("FAKE_STATE", ""))
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""

_FAKE_ESCALATION = """\
import json, os, sys
args = sys.argv[1:]
reason = args[args.index("--reason-file") + 1]
body = open(reason, encoding="utf-8").read() if os.path.exists(reason) else None
with open(os.environ["ESCALATION_LOG"], "w", encoding="utf-8") as f:
    json.dump({"args": args, "reason": body}, f)
"""


def _run_escalation_step(tmp_path, *, returned: bool, state: str = "",
                         reason: str | None = None, lookup_fails: bool = False):
    """Run the escalation step's own shell, with `linear_ops.py` and
    `planning_escalation.py` faked. Returns (exit code, escalation or None)."""
    import json
    import subprocess

    step = next(s for s in _steps()
                if "hand-planning parks" in (s.get("name") or ""))
    temp = tmp_path / "runner-temp"
    temp.mkdir()
    if reason is not None:
        (temp / "planner-escalation.txt").write_text(reason, encoding="utf-8")
    scripts = tmp_path / ".bureau-pipeline" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "linear_ops.py").write_text(_FAKE_LINEAR_OPS, encoding="utf-8")
    (scripts / "planning_escalation.py").write_text(_FAKE_ESCALATION, encoding="utf-8")
    script = (step["run"]
              .replace("${{ runner.temp }}", str(temp))
              .replace("${{ github.event.client_payload.identifier }}", CARD))
    assert "${{" not in script
    log = tmp_path / "escalation.json"
    env = {**os.environ, "RETURNED_CHILD": "true" if returned else "false",
           "PARENT": PARENT, "EXPECT_CARD": CARD, "FAKE_STATE": state,
           "FAKE_EXIT": "1" if lookup_fails else "0", "ESCALATION_LOG": str(log)}
    # GitHub runs a `run:` block as `bash -e {0}`.
    done = subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=60)
    escalation = json.loads(log.read_text(encoding="utf-8")) if log.exists() else None
    return done.returncode, escalation


class TestTheStepProvesTheSplitBeforeStandingDown:
    """The missing reason file is not proof of a split: the card's state in
    Linear is. A returned child left in Planning with no reason must escalate,
    not go green with nobody told."""

    def test_a_canceled_returned_child_exits_green_without_escalating(self, tmp_path):
        code, escalation = _run_escalation_step(tmp_path, returned=True, state="Canceled")
        assert code == 0
        assert escalation is None

    def test_a_returned_child_left_in_planning_escalates_with_a_default_reason(self, tmp_path):
        code, escalation = _run_escalation_step(tmp_path, returned=True, state="Planning")
        assert code == 0
        assert escalation is not None
        assert escalation["args"][:2] == ["escalate", CARD]
        assert "returned child was not split or canceled" in escalation["reason"]
        assert "Planning" in escalation["reason"] and PARENT in escalation["reason"]

    def test_an_unreadable_state_escalates_rather_than_passing(self, tmp_path):
        code, escalation = _run_escalation_step(tmp_path, returned=True, lookup_fails=True)
        assert escalation is not None
        assert "returned child was not split or canceled" in escalation["reason"]

    def test_a_returned_child_with_a_reason_escalates_with_that_reason(self, tmp_path):
        code, escalation = _run_escalation_step(
            tmp_path, returned=True, state="Planning", reason="cannot cut this safely")
        assert escalation is not None
        assert escalation["reason"] == "cannot cut this safely"

    def test_an_ordinary_card_escalates_exactly_as_before(self, tmp_path):
        code, escalation = _run_escalation_step(
            tmp_path, returned=False, reason="a question for the CEO")
        assert escalation == {
            "args": ["escalate", CARD, "--reason-file",
                     str(tmp_path / "runner-temp" / "planner-escalation.txt")],
            "reason": "a question for the CEO",
        }


class TestTheLaneContractNamesTheWriter:
    def test_the_planner_may_write_canceled(self):
        assert "plan.yml" in lane_contract.lane_writers("Canceled")

    def test_both_clauses_say_for_which_class(self):
        for kind in ("entrance", "writers"):
            text = lane_contract.lane("Canceled")["clauses"][kind]["text"]
            assert "DRE-5242" in text, kind
            assert "returned" in text.lower() and "sibling" in text.lower(), kind

    def test_the_rendered_document_carries_it(self):
        doc = (ROOT / "docs" / "lane-contract.md").read_text(encoding="utf-8")
        assert doc == lane_contract.render_markdown()
        assert "DRE-5242" in doc
