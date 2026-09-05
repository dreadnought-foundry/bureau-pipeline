"""The two non-human Linear identities are data, and a check holds them apart (DRE-3172).

Since 2026-09-05 the workspace has three Linear users. The FLEET user
(`Agent-Bureau`) is the one every sweep, planner, merge-sync, relay and
console run as; the OPERATOR-TOOLS user (`bureau-tools`) is the one the
operator's scripts and assistant sessions run as; the CEO approves. Linear's
2,500 requests/hour limit is PER USER, so the two non-human users are two
separate budgets — that is the whole point of having two.

Nothing in code declared that before this card, so nothing could notice the
two ways it silently stops being true: a key rotated onto an admin's user (a
non-human actor that can now do anything on the board), or both keys resolving
to the SAME user (one budget again, and every sweep starves the operator's
terminal the way DRE-3060 did). `config/linear-identities.json` declares each
identity and its rules; `scripts/check_linear_identities.py check` asks Linear
who each key really is and fails, naming the rule, when the declaration and
the truth disagree.

The tests drive the check through a FAKE viewer function — no network, no
credentials — and pin the three answers it can give: OK, FAIL with the rule
named, and UNKNOWN when a key is absent or Linear did not answer. UNKNOWN is
never OK and never exit 0: a check that passes when it could not look is the
check the DRE-2859 module warns about in its own docstring. Nothing it prints
may carry a key — the error path is where one leaks, so that is where the
test aims.
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import check_linear_identities as cli  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "linear-identities.json"

FLEET_KEY = "lin_api_FLEETFLEETFLEETFLEET0001"
TOOLS_KEY = "lin_api_TOOLSTOOLSTOOLSTOOLS0002"
FLEET_ID = "cebc4c53-fad2-410f-be31-f920b6ad773f"
TOOLS_ID = "0913a8db-0000-4000-8000-000000000002"


def _env(fleet=FLEET_KEY, tools=TOOLS_KEY) -> dict:
    env = {}
    if fleet is not None:
        env["LINEAR_API_KEY_FLEET"] = fleet
    if tools is not None:
        env["LINEAR_API_KEY"] = tools
    return env


def _viewer(fleet: dict | None = None, tools: dict | None = None):
    """A fake `viewer { id name admin }` keyed on which key was presented."""
    answers = {
        FLEET_KEY: fleet or {"id": FLEET_ID, "name": "Agent-Bureau", "admin": False},
        TOOLS_KEY: tools or {"id": TOOLS_ID, "name": "bureau-tools", "admin": False},
    }

    def viewer(key: str) -> dict:
        return answers[key]

    return viewer


def _run(env: dict, viewer) -> tuple[int, str]:
    """Exit code and everything the check printed, stdout and stderr together."""
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(out):
        code = cli.run(env=env, viewer=viewer)
    return code, out.getvalue()


class TheDeclaration(unittest.TestCase):
    def test_the_shipped_file_declares_both_non_human_identities(self):
        doc = json.loads(CONFIG.read_text(encoding="utf-8"))
        names = [i["name"] for i in doc["identities"]]
        self.assertEqual(names, ["fleet", "operator-tools"])
        by_name = {i["name"]: i for i in doc["identities"]}
        self.assertEqual(by_name["fleet"]["display_name"], "Agent-Bureau")
        self.assertEqual(by_name["fleet"]["env"], "LINEAR_API_KEY_FLEET")
        self.assertEqual(by_name["operator-tools"]["display_name"], "bureau-tools")
        self.assertEqual(by_name["operator-tools"]["env"], "LINEAR_API_KEY")
        for identity in doc["identities"]:
            self.assertTrue(identity["must_not_be_admin"], identity["name"])
            self.assertTrue(identity["purpose"].strip(), identity["name"])
            self.assertTrue(identity["lives_in"].strip(), identity["name"])
        self.assertEqual(by_name["fleet"]["must_differ_from"], ["operator-tools"])
        self.assertEqual(by_name["operator-tools"]["must_differ_from"], ["fleet"])

    def test_the_shipped_file_passes_its_own_check(self):
        self.assertEqual(cli.config_problems(cli.load()), [])

    def test_a_rule_naming_an_identity_that_does_not_exist_is_a_config_problem(self):
        doc = json.loads(CONFIG.read_text(encoding="utf-8"))
        doc["identities"][0]["must_differ_from"] = ["console"]
        found = cli.config_problems(doc)
        self.assertEqual(len(found), 1, found)
        self.assertIn("console", found[0])
        self.assertIn("fleet", found[0])

    def test_two_identities_reading_one_env_var_is_a_config_problem(self):
        doc = json.loads(CONFIG.read_text(encoding="utf-8"))
        doc["identities"][1]["env"] = doc["identities"][0]["env"]
        found = cli.config_problems(doc)
        self.assertTrue(any("LINEAR_API_KEY_FLEET" in p for p in found), found)


class TheCheck(unittest.TestCase):
    def test_all_good_is_ok_and_exits_zero(self):
        code, text = _run(_env(), _viewer())
        self.assertEqual(code, 0, text)
        self.assertIn("[OK] fleet", text)
        self.assertIn("[OK] operator-tools", text)
        self.assertIn("Agent-Bureau", text)
        self.assertIn(FLEET_ID, text)
        self.assertNotIn("[FAIL]", text)
        self.assertNotIn("UNKNOWN", text)

    def test_an_admin_key_fails_naming_the_identity_and_the_rule(self):
        viewer = _viewer(fleet={"id": FLEET_ID, "name": "Agent-Bureau", "admin": True})
        code, text = _run(_env(), viewer)
        self.assertEqual(code, 1, text)
        self.assertIn("[FAIL] fleet", text)
        self.assertIn("must_not_be_admin", text)
        self.assertIn("[OK] operator-tools", text)

    def test_two_keys_on_one_user_fail_naming_both(self):
        same = {"id": FLEET_ID, "name": "Agent-Bureau", "admin": False}
        code, text = _run(_env(), _viewer(fleet=same, tools=dict(same)))
        self.assertEqual(code, 1, text)
        differ = [line for line in text.splitlines() if "must_differ_from" in line]
        self.assertEqual(len(differ), 1, text)
        self.assertIn("fleet", differ[0])
        self.assertIn("operator-tools", differ[0])
        self.assertIn(FLEET_ID, differ[0])

    def test_a_display_name_mismatch_fails(self):
        viewer = _viewer(tools={"id": TOOLS_ID, "name": "Frederick Conklin", "admin": False})
        code, text = _run(_env(), viewer)
        self.assertEqual(code, 1, text)
        self.assertIn("[FAIL] operator-tools", text)
        self.assertIn("display_name", text)
        self.assertIn("bureau-tools", text)
        self.assertIn("Frederick Conklin", text)

    def test_an_absent_key_is_unknown_never_ok_and_exits_non_zero(self):
        code, text = _run(_env(fleet=None), _viewer())
        self.assertNotEqual(code, 0, text)
        self.assertIn("[UNKNOWN] fleet", text)
        self.assertIn("LINEAR_API_KEY_FLEET", text)
        self.assertNotIn("[OK] fleet", text)
        # The identity that WAS readable is still reported on its own terms.
        self.assertIn("[OK] operator-tools", text)

    def test_an_empty_key_is_absent(self):
        code, text = _run(_env(tools=""), _viewer())
        self.assertNotEqual(code, 0, text)
        self.assertIn("[UNKNOWN] operator-tools", text)

    def test_a_viewer_that_does_not_answer_is_unknown_not_a_pass(self):
        def viewer(key: str) -> dict:
            if key == TOOLS_KEY:
                raise RuntimeError("linear error from https://api.linear.app/graphql: 401")
            return _viewer()(key)

        code, text = _run(_env(), viewer)
        self.assertNotEqual(code, 0, text)
        self.assertIn("[UNKNOWN] operator-tools", text)
        self.assertIn("401", text)

    def test_nothing_printed_carries_a_key_even_on_the_error_path(self):
        def viewer(key: str) -> dict:
            raise RuntimeError(f"unauthorized: bad key {key}")

        code, text = _run(_env(), viewer)
        self.assertNotEqual(code, 0)
        self.assertNotIn(FLEET_KEY, text)
        self.assertNotIn(TOOLS_KEY, text)
        self.assertIn("<redacted>", text)

        code, text = _run(_env(), _viewer())
        self.assertNotIn(FLEET_KEY, text)
        self.assertNotIn(TOOLS_KEY, text)

    def test_the_differ_rule_is_not_judged_against_an_unknown_identity(self):
        code, text = _run(_env(fleet=None), _viewer())
        self.assertNotIn("must_differ_from", text)


class TheCli(unittest.TestCase):
    def test_check_with_no_keys_in_the_environment_is_unknown_and_non_zero(self):
        saved = {k: os.environ.pop(k, None) for k in ("LINEAR_API_KEY", "LINEAR_API_KEY_FLEET")}
        try:
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(out):
                code = cli.main(["check"])
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
        self.assertNotEqual(code, 0)
        self.assertIn("[UNKNOWN] fleet", out.getvalue())
        self.assertIn("[UNKNOWN] operator-tools", out.getvalue())

    def test_an_unknown_subcommand_is_usage(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            try:
                code = cli.main(["frobnicate"])
            except SystemExit as exc:  # argparse's own refusal of a bad choice
                code = exc.code
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
