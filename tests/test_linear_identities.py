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

ONE IDENTITY, SEVERAL HOMES (DRE-3334). The fleet key is not kept in one
place: the Actions secret is one copy and the relay Lambda's own copy, in
Secrets Manager `bureau/relay/linear-api-key`, is another. Until this card the
check read ONE variable per identity, so the relay's copy was read by nothing
in this repo and "the relay stayed `Agent-Bureau`" was a sentence rather than a
test. It goes wrong in a known, dated way: until agent-bureau PR #2299 the
relay's deploy copied the operator's `LINEAR_API_KEY` into that secret, and
that variable has been the `bureau-tools` key since 2026-09-05 — one routine
deploy would have put every Triage parking reason and every escalation the
relay writes on the operator's budget, silently. So an identity may declare
`homes`, each with its own variable, every home is resolved and judged, and a
new rule `one_user_per_identity` says every home of an identity is the same
user.

The tests drive the check through a FAKE viewer function — no network, no
credentials — and pin the three answers it can give: OK, FAIL with the rule
named, and UNKNOWN when a key is absent or Linear did not answer. UNKNOWN is
never OK and never exit 0: a check that passes when it could not look is the
check the DRE-2859 module warns about in its own docstring. Nothing it prints
may carry a key — the error path is where one leaks, so that is where the
test aims, and the relay's key is held to the same rule.
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
RELAY_KEY = "lin_api_RELAYRELAYRELAYRELAY0003"
FLEET_ID = "cebc4c53-fad2-410f-be31-f920b6ad773f"
TOOLS_ID = "0913a8db-0000-4000-8000-000000000002"


def _env(fleet=FLEET_KEY, tools=TOOLS_KEY, relay=RELAY_KEY) -> dict:
    env = {}
    if fleet is not None:
        env["LINEAR_API_KEY_FLEET"] = fleet
    if tools is not None:
        env["LINEAR_API_KEY"] = tools
    if relay is not None:
        env["LINEAR_API_KEY_RELAY"] = relay
    return env


def _viewer(fleet: dict | None = None, tools: dict | None = None,
            relay: dict | None = None):
    """A fake `viewer { id name admin }` keyed on which key was presented.

    The relay's key is a SECOND copy of the fleet key, so its default answer
    is the fleet user — the same id, which is exactly what
    `one_user_per_identity` asserts.
    """
    answers = {
        FLEET_KEY: fleet or {"id": FLEET_ID, "name": "Agent-Bureau", "admin": False},
        TOOLS_KEY: tools or {"id": TOOLS_ID, "name": "bureau-tools", "admin": False},
        RELAY_KEY: relay or {"id": FLEET_ID, "name": "Agent-Bureau", "admin": False},
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
        # The fleet key has a SECOND home: the relay Lambda's own copy
        # (DRE-3334). operator-tools has none.
        homes = by_name["fleet"]["homes"]
        self.assertEqual([h["name"] for h in homes], ["relay"])
        self.assertEqual(homes[0]["env"], "LINEAR_API_KEY_RELAY")
        self.assertIn("bureau/relay/linear-api-key", homes[0]["lives_in"])
        self.assertEqual(by_name["operator-tools"].get("homes", []), [])

    def test_the_shipped_file_passes_its_own_check(self):
        self.assertEqual(cli.config_problems(cli.load()), [])

    def test_a_home_env_colliding_with_another_env_is_a_config_problem(self):
        doc = json.loads(CONFIG.read_text(encoding="utf-8"))
        doc["identities"][0]["homes"].append({
            "name": "console",
            "env": "LINEAR_API_KEY",
            "lives_in": "AWS Secrets Manager bureau-console/linear-api-key",
        })
        found = cli.config_problems(doc)
        collision = [p for p in found if "one variable are one identity" in p]
        self.assertEqual(len(collision), 1, found)
        self.assertIn("fleet/console", collision[0])
        self.assertIn("operator-tools", collision[0])
        self.assertIn("LINEAR_API_KEY", collision[0])

    def test_a_home_name_repeated_in_one_identity_is_a_config_problem(self):
        doc = json.loads(CONFIG.read_text(encoding="utf-8"))
        doc["identities"][0]["homes"].append({
            "name": "relay",
            "env": "LINEAR_API_KEY_RELAY_TWO",
            "lives_in": "somewhere else entirely",
        })
        found = cli.config_problems(doc)
        dupes = [p for p in found if "more than once" in p]
        self.assertEqual(len(dupes), 1, found)
        self.assertIn("relay", dupes[0])
        self.assertIn("fleet", dupes[0])

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
        self.assertIn("[UNKNOWN] fleet:", text)
        self.assertIn("LINEAR_API_KEY_FLEET", text)
        # `fleet:` with the colon, so the fleet's OTHER home (`fleet/relay`)
        # cannot be mistaken for the identity's own line.
        self.assertNotIn("[OK] fleet:", text)
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
        self.assertNotIn(RELAY_KEY, text)
        self.assertIn("<redacted>", text)

        code, text = _run(_env(), _viewer())
        self.assertNotIn(FLEET_KEY, text)
        self.assertNotIn(TOOLS_KEY, text)
        self.assertNotIn(RELAY_KEY, text)

    def test_the_differ_rule_is_not_judged_against_an_unknown_identity(self):
        code, text = _run(_env(fleet=None, relay=None), _viewer())
        self.assertNotIn("must_differ_from", text)


class TheRelayHome(unittest.TestCase):
    """The fleet key's second home — the relay Lambda's copy (DRE-3334)."""

    def test_three_good_keys_are_three_ok_lines_and_exit_zero(self):
        code, text = _run(_env(), _viewer())
        self.assertEqual(code, 0, text)
        oks = [line for line in text.splitlines() if "[OK]" in line]
        self.assertEqual(len(oks), 3, text)
        self.assertIn("[OK] fleet:", text)
        self.assertIn("[OK] fleet/relay:", text)
        self.assertIn("[OK] operator-tools:", text)
        self.assertIn("LINEAR_API_KEY_RELAY", text)

    def test_a_relay_on_the_operators_user_breaks_one_user_per_identity(self):
        """The live way it goes wrong: a deploy copied the operator's
        `LINEAR_API_KEY` into the relay's secret, so the relay writes on the
        operator's budget while every board surface looks identical."""
        relay_is_tools = {"id": TOOLS_ID, "name": "bureau-tools", "admin": False}
        code, text = _run(_env(), _viewer(relay=relay_is_tools))
        self.assertEqual(code, 1, text)
        # ONE line, not three. A second rule restating the same fact under
        # another name is noise an operator reads mid-incident.
        broke = [line for line in text.splitlines() if "[FAIL]" in line]
        self.assertEqual(len(broke), 1, text)
        self.assertIn("[FAIL] fleet/relay", broke[0])
        self.assertIn("rule one_user_per_identity broke", broke[0])
        self.assertIn("LINEAR_API_KEY_RELAY", broke[0])
        # The two homes that ARE what they say they are still say so.
        self.assertIn("[OK] fleet:", text)
        self.assertIn("[OK] operator-tools:", text)

    def test_a_relay_on_the_operators_user_never_accuses_the_fleet_key(self):
        """Which key to rotate is the whole output. `fleet` and
        `operator-tools` did NOT land on one user here — only fleet's RELAY
        home did — so no line may say they did."""
        relay_is_tools = {"id": TOOLS_ID, "name": "bureau-tools", "admin": False}
        code, text = _run(_env(), _viewer(relay=relay_is_tools))
        self.assertEqual(code, 1, text)
        self.assertNotIn("must_differ_from", text)
        self.assertNotIn("display_name", text)
        # The fleet's own key resolves to Agent-Bureau and is reported as such.
        self.assertIn("[OK] fleet: LINEAR_API_KEY_FLEET resolves to "
                      "'Agent-Bureau'", text)
        self.assertNotIn("[FAIL] fleet:", text)
        # Every line that puts a name on the operator's user names the RELAY
        # variable as the one that resolved to it — never the fleet's own.
        for line in text.splitlines():
            if "[FAIL]" in line:
                head = line.split("resolves to")[0]
                self.assertIn("LINEAR_API_KEY_RELAY", head, text)
                self.assertNotIn("LINEAR_API_KEY_FLEET", head, text)

    def test_a_relay_key_on_an_admin_fails_must_not_be_admin(self):
        admin = {"id": FLEET_ID, "name": "Agent-Bureau", "admin": True}
        code, text = _run(_env(), _viewer(relay=admin))
        self.assertEqual(code, 1, text)
        broke = [line for line in text.splitlines() if "must_not_be_admin" in line]
        self.assertEqual(len(broke), 1, text)
        self.assertIn("[FAIL] fleet/relay", broke[0])
        self.assertIn("LINEAR_API_KEY_RELAY", broke[0])

    def test_a_relay_on_an_admin_is_judged_when_the_fleet_key_is_unreadable(self):
        """`one_user_per_identity` has nothing to hold this home against when
        the first home could not be read — so if the admin rule were asked of
        first homes only, the line would read `[OK] … not an admin` about an
        admin."""
        admin = {"id": FLEET_ID, "name": "Agent-Bureau", "admin": True}
        code, text = _run(_env(fleet=None), _viewer(relay=admin))
        self.assertNotEqual(code, 0, text)
        self.assertNotIn("[OK] fleet/relay", text)
        broke = [line for line in text.splitlines() if "must_not_be_admin" in line]
        self.assertEqual(len(broke), 1, text)
        self.assertIn("[FAIL] fleet/relay", broke[0])

    def test_the_relay_variable_unset_is_unknown_and_non_zero(self):
        """The relay unread is exactly the case this card exists for, so it
        can never be a pass."""
        code, text = _run(_env(relay=None), _viewer())
        self.assertNotEqual(code, 0, text)
        self.assertIn("[UNKNOWN] fleet/relay: LINEAR_API_KEY_RELAY is not set", text)
        self.assertIn("unknown is not a pass", text)
        self.assertIn("[OK] fleet:", text)
        self.assertIn("[OK] operator-tools:", text)

    def test_nothing_printed_carries_the_relay_key(self):
        def viewer(key: str) -> dict:
            raise RuntimeError(f"unauthorized: bad key {key}")

        code, text = _run(_env(), viewer)
        self.assertNotEqual(code, 0)
        self.assertNotIn(RELAY_KEY, text)
        self.assertIn("<redacted>", text)

        code, text = _run(_env(), _viewer())
        self.assertEqual(code, 0, text)
        self.assertNotIn(RELAY_KEY, text)


class TheCli(unittest.TestCase):
    def test_check_with_no_keys_in_the_environment_is_unknown_and_non_zero(self):
        saved = {k: os.environ.pop(k, None) for k in
                 ("LINEAR_API_KEY", "LINEAR_API_KEY_FLEET", "LINEAR_API_KEY_RELAY")}
        try:
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(out):
                code = cli.main(["check"])
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
        self.assertNotEqual(code, 0)
        self.assertIn("[UNKNOWN] fleet:", out.getvalue())
        self.assertIn("[UNKNOWN] fleet/relay:", out.getvalue())
        self.assertIn("[UNKNOWN] operator-tools:", out.getvalue())

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
