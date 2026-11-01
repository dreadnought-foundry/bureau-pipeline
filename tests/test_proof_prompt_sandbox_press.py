"""The proof run's own prompt gives a `Sandbox press:` to the run, as the brief does (DRE-7781).

DRE-7778 wrote the rule into `briefs/proof.md`: a press on the list, on the
sandbox project, is the run's own step through agent-bureau's
`scripts/console_proof_reader.py press`. A run reads its prompt before its
brief, so this card brings `proof-task.yml`'s three prompts into line, and
tells a shadow run it makes no sandbox press. `tests/test_proof_task_shadow.py`
pins the whole prompt byte for byte against its fixture; this file names the
sentences the card asked for, so a failure says which one went missing, and
runs the shadow step to read the bullet the fixture cannot see.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_proof_press_rule import ITEM_4, ITEM_5, SHADOW_ROW, _flat, _prompts
from test_proof_task_shadow import _run_step, _shadow, _step

STEP_2 = "2. Observe every acceptance criterion as the read-only identities"
STEP_3 = "3. Push the branch with GH_TOKEN"


def _between(prompt: str, start: str, end: str) -> str:
    a, b = prompt.find(start), prompt.find(end)
    assert 0 <= a < b, f"the prompt has no {start!r} before {end!r}"
    return _flat(prompt[a:b])


class EveryArmsItem4Test(unittest.TestCase):
    def test_a_sandbox_press_is_the_runs_own_step(self):
        for i, prompt in enumerate(_prompts()):
            item = _between(prompt, ITEM_4, ITEM_5)
            with self.subTest(arm=i):
                for phrase in ("A criterion the card writes opening `Sandbox press:` "
                               "is your own step, on the sandbox project `agent-bureau-demo`",
                               "`scripts/console_proof_reader.py press`",
                               "list the ids you made in the record, and close or list "
                               "them as briefs/proof.md says"):
                    self.assertIn(phrase, item)

    def test_no_arm_gives_a_press_on_the_sandbox_project_to_the_operator(self):
        for i, prompt in enumerate(_prompts()):
            with self.subTest(arm=i):
                self.assertNotIn("A press on the sandbox project",
                                 _between(prompt, ITEM_4, ITEM_5))

    def test_the_two_clauses_point_at_the_brief(self):
        for i, prompt in enumerate(_prompts()):
            item = _between(prompt, ITEM_4, ITEM_5)
            with self.subTest(arm=i):
                self.assertIn("A run whose checkout holds no "
                              "`scripts/console_proof_reader.py`", item)
                self.assertIn("console proof-reader bullet under Identities in "
                              "briefs/proof.md says, `Not observed. cause: out-of-reach — …`",
                              item)
                self.assertIn("A press the live console refuses is the same cause row "
                              "with the console's reason, as briefs/proof.md's "
                              "\"When the console is not ready\" says", item)

    def test_every_other_press_waits_on_no_person(self):
        """DRE-7954: a press outside the sandbox is an OPERATOR card the proof
        is blocked by, and an older card's person's press is a cause row."""
        for i, prompt in enumerate(_prompts()):
            item = _between(prompt, ITEM_4, ITEM_5)
            with self.subTest(arm=i):
                self.assertIn("a sandbox press not on the standard's list", item)
                self.assertIn("`Not observed. cause: operator-step-unblocked — <what you saw "
                              "instead>`", item)
                self.assertNotIn("waiting for the operator's press", item)


class EveryArmsStep2Test(unittest.TestCase):
    def test_the_sandbox_presses_are_the_one_exception(self):
        for i, prompt in enumerate(_prompts()):
            with self.subTest(arm=i):
                self.assertIn("the one exception is the sandbox presses briefs/proof.md names",
                              _between(prompt, STEP_2, STEP_3))


class TheShadowRunMakesNoSandboxPressTest(unittest.TestCase):
    def _text(self) -> str:
        with tempfile.TemporaryDirectory() as td:
            res, out = _run_step(_step(id="shadow_prompt"), Path(td), _shadow())
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return _flat(out.get("text", ""))

    def test_the_shadow_prompt_says_it_makes_no_sandbox_press(self):
        text = self._text()
        self.assertIn("Make no sandbox press: the real run beside you makes them, "
                      "and a second run would file a second Intake card.", text)
        self.assertIn(f"Your `Sandbox press:` rows read `{SHADOW_ROW}`.", text)


if __name__ == "__main__":
    unittest.main()
