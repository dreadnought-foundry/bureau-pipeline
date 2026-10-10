"""The build agent stamps its blocker note with the blocker's class (DRE-6443).

`/tmp/agent-blocker.txt` used to be one undifferentiated exit — "impossible as
specified", parked in Backlog, inert until somebody fixed the card. Under the
blocker-class epic the note's FIRST line names its class (`blocker-class:
<class>`, the vocabulary in `config/blocker-classes.json`, DRE-6438): a
mechanical class the sweep acts on within one pass, a `question` asked in
Green Light. That only works if every text the agent reads about the exit says
so:

  * item 7 of the build prompt, in all three `Implement card` steps, byte for
    byte the same;
  * the four role briefs, whose blocker passages are in the agent's assembled
    context and have the final word there;
  * `standards/card-quality.md`'s one parenthesis that called Backlog "inert".

The class names are read off the repo's own `config/blocker-classes.json`,
never a copy, so renaming a class without rewriting the brief goes red here.

Run: cd bureau-pipeline && python3 -m pytest tests/test_agent_task_blocker_class.py -v
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import step_shell  # noqa: E402

WF = ROOT / ".github" / "workflows" / "agent-task.yml"
VOCABULARY = ROOT / "config" / "blocker-classes.json"
STEP_NAMES = ("Implement card", "Implement card (retry 1)", "Implement card (retry 2)")
ROLE_BRIEFS = ("engineer", "devops", "frontend", "database-architect")
MECHANICAL = ("nothing-to-change", "wrong-repo", "branch-without-pr")


def _vocabulary() -> dict:
    return json.loads(VOCABULARY.read_text(encoding="utf-8"))


def _class_names() -> list:
    return list(_vocabulary()["classes"])


def _norm(text: str) -> str:
    """Whitespace-collapsed — the prompt and the briefs are hard-wrapped, and
    a reflow of the same words must not read as a removal."""
    return re.sub(r"\s+", " ", text or "")


def _steps() -> list:
    doc = yaml.safe_load(step_shell.workflow_source(WF))
    return [s for job in doc["jobs"].values() for s in job.get("steps") or []]


def _prompt(name: str) -> str:
    found = [s for s in _steps() if s.get("name") == name]
    assert len(found) == 1, f"agent-task.yml carries exactly one {name!r} step"
    return (found[0].get("with") or {}).get("prompt") or ""


def _item_7(prompt: str) -> str:
    """Item 7 of the process block, raw: from its `7.` to the `8.` after it."""
    m = re.search(r"\n( *7\. .*?)\n *8\. HAND BACK", prompt, re.S)
    assert m, "item 7 (followed by item 8) was not found in the prompt"
    return m.group(1)


def _items() -> list:
    return [_item_7(_prompt(name)) for name in STEP_NAMES]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _three_exits(rel: str) -> str:
    """The brief's "three exits" paragraph, whitespace-collapsed."""
    m = re.search(r"\nHand-back is the THIRD exit(.*?)\n\n", _read(rel), re.S)
    assert m, f"the 'three exits' paragraph was not found in {rel}"
    return _norm(m.group(1))


def _engineer_contrast() -> str:
    """`briefs/engineer.md`'s paragraph contrasting the escalation with the
    blocker, whitespace-collapsed."""
    m = re.search(
        r"\n(`/tmp/agent-escalation\.txt` is the \*\*business-decision.*?)\n\n",
        _read("briefs/engineer.md"),
        re.S,
    )
    assert m, "the escalation/blocker paragraph was not found in briefs/engineer.md"
    return _norm(m.group(1))


# --------------------------------------------------------------------------- #
# item 7 of the build prompt                                                   #
# --------------------------------------------------------------------------- #

def test_the_vocabulary_is_the_one_the_contract_names():
    vocabulary = _vocabulary()
    assert vocabulary["stamp"] == "blocker-class: "
    assert sorted(_class_names()) == sorted(MECHANICAL + ("question",))


def test_the_three_copies_of_item_7_are_byte_identical():
    items = _items()
    assert len(items) == 3
    assert len(set(items)) == 1, "item 7 differs between the Implement card steps"


def test_item_7_is_still_numbered_7():
    for item in _items():
        assert item.lstrip().startswith("7. ")


def test_item_7_carries_the_stamp_grammar():
    for item in _items():
        text = _norm(item)
        assert "blocker-class: " in text
        assert "/tmp/agent-blocker.txt" in text
        assert "first line" in text


def test_item_7_names_every_class_from_the_vocabulary():
    stamp = _vocabulary()["stamp"]
    for item in _items():
        text = _norm(item)
        for name in _class_names():
            assert f"{stamp}{name}" in text, f"item 7 does not name {name!r}"


def test_item_7_names_the_vocabulary_file_the_agent_can_read():
    for item in _items():
        assert ".bureau-pipeline/config/blocker-classes.json" in _norm(item)


def test_nothing_to_change_carries_the_attestation_grammar():
    for item in _items():
        text = _norm(item)
        assert "- [x] <criterion> — <what on the default branch satisfies it>" in text
        assert "- [x] " in text
        assert "- [ ] " in text, "a criterion the agent could not show is unticked"
        assert "every criterion" in text
        assert "only when there is no diff to open" in text


def test_wrong_repo_names_the_repository_on_the_second_line():
    for item in _items():
        text = _norm(item)
        assert "second line" in text
        assert "repo: <slug>" in text
        assert ".bureau-pipeline/config/repo-map.json" in text
        assert "change no label yourself" in text


def test_a_question_is_asked_in_green_light_and_never_parked_silently():
    for item in _items():
        text = _norm(item)
        assert "Green Light" in text
        assert "Finding / Question / Recommendation" in text
        assert "never parked silently" in text
        assert "item 6" in text, "a choice between builds is still item 6's escalation"


def test_a_card_that_is_several_cards_is_a_hand_back():
    for item in _items():
        text = _norm(item)
        assert "really several cards is a hand-back (/tmp/agent-handback.txt)" in text


def test_a_partly_built_card_is_built_and_not_blocked():
    for item in _items():
        text = _norm(item)
        assert "partly on the default branch is ordinary work" in text
        assert "build what is missing and open the pull request" in text


def test_a_short_attestation_goes_to_the_planner_and_is_never_canceled():
    for item in _items():
        text = _norm(item)
        assert "attests fewer is sent to the planner to be re-read, never canceled" in text
        assert "the sweep cancels only a note that attests every criterion" in text


def test_a_pull_request_held_on_purpose_is_a_question():
    for item in _items():
        text = _norm(item)
        assert "HOLDING on purpose" in text
        assert "is `blocker-class: question`, never this class" in text


def test_item_7_no_longer_calls_every_blocker_impossible():
    for item in _items():
        assert "IMPOSSIBLE as specified" not in item


# --------------------------------------------------------------------------- #
# the four role briefs and the standard                                        #
# --------------------------------------------------------------------------- #

def _says_the_routing(passage: str, where: str) -> None:
    assert "/tmp/agent-blocker.txt" in passage, where
    assert "blocker-class: " in passage, f"{where} does not name the stamp grammar"
    for name in MECHANICAL:
        assert f"`{name}`" in passage, f"{where} does not name {name!r}"
    assert "the sweep acts on it within one pass" in passage, where
    assert "a `question` is asked in `Green Light`" in passage, where
    assert "never parked silently" in passage, where


def test_every_role_brief_stamps_the_blocker_in_its_three_exits_paragraph():
    for role in ROLE_BRIEFS:
        rel = f"briefs/{role}.md"
        _says_the_routing(_three_exits(rel), rel)


def test_the_four_three_exits_paragraphs_say_the_same_thing():
    assert len({_three_exits(f"briefs/{role}.md") for role in ROLE_BRIEFS}) == 1


def test_the_engineer_brief_contrasts_the_escalation_with_a_classed_blocker():
    _says_the_routing(_engineer_contrast(), "briefs/engineer.md (escalation vs blocker)")


def test_inert_until_the_card_is_gone():
    assert "inert until the card" not in _norm(_read("briefs/engineer.md"))
    assert "inert until the card" not in _norm(_read("standards/card-quality.md"))


def test_the_standard_names_the_classed_blocker():
    standard = _norm(_read("standards/card-quality.md"))
    assert (
        "(a build agent's blocker: a mechanical class the sweep acts on within "
        "one pass; a question is asked in Green Light)"
    ) in standard


def test_the_standard_keeps_the_green_light_pin():
    # tests/test_escalations_park_in_green_light.py reads this exact string.
    assert "parks the card in the **`Green Light`** lane" in _read(
        "standards/card-quality.md"
    )
