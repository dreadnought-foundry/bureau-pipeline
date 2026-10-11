"""bureau-pipeline's own merge-recheck declaration (DRE-6875, after DRE-6872).

DRE-6871 gave the merge gate's fork-refresh decision a second rule, switched
on per repo by `"recheck_new_tests": true` in `.github/bureau/merge-recheck.json`:
when `main` gained a new test since the pull request's last test run began
and that test names a file the pull request changes, the gate refreshes the
branch from `main` once. Measured over 3.2 days it fires on 15% of
bureau-pipeline merges, and it would have caught the #871 clash.

bureau-pipeline has no order-sensitive paths, so its declaration carries the
switch and a `$schema_note` and nothing else — `order_sensitive_paths` absent
reads as empty once the switch is on.

The file is read here through `order_sensitive_refresh._declaration`, the
loader the decision's CLI uses in the merge gate, so a declaration the gate
could not read fails here first. `check_declaration` is the whole assertion,
and MutationTest runs it against copies of the shipped file with the key
misspelled or its value anything but the JSON boolean `true`, to prove it
goes red on each.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DECLARATION = ROOT / ".github" / "bureau" / "merge-recheck.json"

sys.path.insert(0, str(ROOT / "scripts"))

import order_sensitive_refresh as osr  # noqa: E402

TIP = "8775c0de5ea1b2c3d4e5f60718293a4b5c6d7e8f"
MERGE_BASE = "86fb0115a3b1e0f2c3d4e5f60718293a4b5c6d7e"

# A head two commits behind `main`, changing one source file.
BEHIND_COMPARE = {
    "behind_by": 2,
    "ahead_by": 1,
    "base_commit": {"sha": TIP},
    "merge_base_commit": {"sha": MERGE_BASE},
    "files": [{"filename": "scripts/planning_escalation.py",
               "status": "modified",
               "patch": "@@ -1 +1 @@\n-x = 1\n+x = 2\n"}],
}
# What `main` gained since the merge base: nothing under any prefix.
BASE_ADVANCE = {"files": [{"filename": "scripts/merge_gate.py",
                           "status": "modified"}]}
# What `main` gained since the head's last test run began: a source change
# and a modified test — no NEW test, so nothing for rule 6 to refresh for.
SINCE_RUN_NO_NEW_TEST = {"files": [
    {"filename": "scripts/merge_gate.py", "status": "modified"},
    {"filename": "tests/test_planning_escalation.py", "status": "modified",
     "patch": "@@ -1 +1 @@\n-import planning_escalation\n"
              "+import planning_escalation  # touched\n"},
]}


def check_declaration(path) -> None:
    """Raise AssertionError unless `path` is bureau-pipeline's declaration as
    the gate must read it: the new-test rule on, no order-sensitive prefix,
    and a behind head with no new test on `main` deciding `proceed`."""
    declaration = osr._declaration(str(path))
    assert declaration is not osr.NO_DECLARATION, f"{path} does not exist"
    assert declaration is not osr.UNREADABLE, f"{path} does not parse"
    assert isinstance(declaration, dict), f"{path} is not a JSON object"

    assert osr._read_switch(declaration) is True, (
        f"{path} does not set `{osr.RECHECK_KEY}` to the JSON boolean true")
    assert osr.DECLARATION_KEY not in declaration, (
        f"{path} declares `{osr.DECLARATION_KEY}`; bureau-pipeline has none")
    assert osr._read_declaration(declaration, True) == [], (
        f"{path} declares an order-sensitive prefix")

    note = declaration.get("$schema_note")
    assert isinstance(note, str) and note.strip(), (
        f"{path} carries no `$schema_note`")

    decision = osr.decide(compare=BEHIND_COMPARE,
                          base_advance=BASE_ADVANCE,
                          declaration=declaration,
                          receipts=[],
                          since_run=SINCE_RUN_NO_NEW_TEST)
    assert decision.decision == osr.PROCEED, (
        f"decide answered {decision.decision}: {decision.reason}")


class ShippedDeclarationTest(unittest.TestCase):

    def test_the_shipped_declaration_switches_the_new_test_rule_on(self):
        check_declaration(DECLARATION)

    def test_the_note_says_what_the_switch_does(self):
        note = json.loads(DECLARATION.read_text())["$schema_note"]
        for word in ("main", "once", "new test", "pull request"):
            self.assertIn(word, note)

    def test_the_rule_is_reached_not_short_circuited(self):
        """The `proceed` above comes from rule 6 reading the since-run
        payload: the same declaration with a since-run read that blipped
        waits, which only rule 6 can answer."""
        declaration = osr._declaration(str(DECLARATION))
        decision = osr.decide(compare=BEHIND_COMPARE,
                              base_advance=BASE_ADVANCE,
                              declaration=declaration,
                              receipts=[],
                              since_run={})
        self.assertEqual(decision.decision, osr.WAIT, decision.reason)


class MutationTest(unittest.TestCase):
    """check_declaration goes red on each way the switch can be wrong."""

    def _mutated(self, mutate):
        declaration = json.loads(DECLARATION.read_text())
        mutate(declaration)
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8")
        self.addCleanup(Path(tmp.name).unlink)
        with tmp:
            json.dump(declaration, tmp)
        return tmp.name

    def test_a_misspelled_key_fails(self):
        def misspell(declaration):
            declaration["recheck_new_test"] = declaration.pop(osr.RECHECK_KEY)
        with self.assertRaises(AssertionError):
            check_declaration(self._mutated(misspell))

    def test_a_value_other_than_json_true_fails(self):
        for value in (False, "true", 1, None, [True], {"on": True}):
            with self.subTest(value=value):
                def set_value(declaration, value=value):
                    declaration[osr.RECHECK_KEY] = value
                with self.assertRaises(AssertionError):
                    check_declaration(self._mutated(set_value))

    def test_a_declared_order_sensitive_prefix_fails(self):
        def declare(declaration):
            declaration[osr.DECLARATION_KEY] = ["console/alembic/versions/"]
        with self.assertRaises(AssertionError):
            check_declaration(self._mutated(declare))


if __name__ == "__main__":
    unittest.main()
