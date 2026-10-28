"""RED-first tests for DRE-7431 — the TDD commit check relieves onboarding's
per-repo console facts.

Since DRE-7379 the routing pull request onboarding opens in agent-bureau on
`bot/onboard` also writes three per-repo console facts, each inserted as text
after the last entry of one literal, so no other line moves:

  * `console/backend/github_oidc.py` — one `REPO_IDS` entry,
    `"<slug>": ("<owner id>", "<repo id>")`;
  * `console/backend/sources.py` — one `PROJECT_IDENTITIES` entry,
    `"<slug>": {"name": ..., "client": ...}`, written in the same commit as
    the file's regenerated repo-mirror region;
  * `console/backend/runner-routing.json` — one new repo under `repos`.

The DRE-7209 relief covered none of them, so agent-bureau #3793 failed `TDD
commit discipline` on all three commits, and no added commit clears that
(DRE-2694). The relief here is entry-only, never a whole file:
  * exactly one entry added to exactly that literal, in the shape the engine
    writes, and no other line of the file changed — a changed existing entry,
    a second entry or an edited function is code;
  * only on an onboarding routing PR — opened by agent-bureau-bot
    (GitHub-attested) on the literal branch `bot/onboard` — exactly the
    DRE-7209 conditions;
  * neither fact file is a slug mirror, so a repo-mirror region written into
    one relieves nothing.

DRE-8249 adds a fourth fact. Since DRE-8210 the same step appends one
`"<org lowercased>": "<tenant slug>"` member to `orgs` in
`console/backend/org-tenants.json` when the owner org has none, and that
commit was code with no test before it, so a new company's routing pull
request could not merge. The relief is the runner-routing one's exactness:
one new key under `orgs`, lowercased, with a string value, and the rest of
the file, `_comment` included, unchanged. Retirement never removes an entry,
so a removal stays code.

Run: cd bureau-pipeline && python3 -m pytest tests/test_check_tdd_commits_onboard_facts.py -v
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_tdd_commits as tdd  # noqa: E402
from test_check_tdd_commits_onboard import (  # noqa: E402
    AFTER, BEFORE, BEGIN, END, ONBOARD, commit,
)
from test_tdd_commit_check import BOT, GitRepoMixin  # noqa: E402

OIDC = "console/backend/github_oidc.py"
SOURCES = "console/backend/sources.py"
RUNNER_ROUTING = "console/backend/runner-routing.json"
ORG_TENANTS = "console/backend/org-tenants.json"
FACTS = (SOURCES, OIDC, RUNNER_ROUTING)

# The contract DRE-7379 writes for `foundry`, byte for byte.
OIDC_ENTRY = '    "foundry": ("226758887", "1412115043"),\n'
IDENTITY_ENTRY = ('    "foundry": {"name": "Foundry", '
                  '"client": "Dreadnought Foundry"},\n')
VARIABLES = ("BUREAU_RUNS_ON", "BUREAU_CI_RUNS_ON", "BUREAU_SHORT_RUNS_ON",
             "BUREAU_DOCKER_RUNS_ON")


def oidc(entries="", logic="repo_ids_for",
         ids=('"111"', '"222"')):
    """github_oidc.py the way agent-bureau keeps it: authored code around
    the `REPO_IDS` literal. `entries` is text inserted after its last entry,
    the way the routing step inserts one."""
    return (
        '"""GitHub OIDC claims for the console."""\n'
        "\n"
        "REPO_IDS = {\n"
        f'    "atlas": ({ids[0]}, {ids[1]}),\n'
        '    "portico": ("111", "333"),\n'
        f"{entries}"
        "}\n"
        "\n"
        "\n"
        f"def {logic}(slug):\n"
        "    return REPO_IDS[slug]\n"
    )


def sources(slugs=BEFORE, entries="", logic="project_identity",
            client="Dreadnought Foundry"):
    """sources.py: a slug mirror (its repo-mirror region holds `REPOS`) that
    also carries the `PROJECT_IDENTITIES` literal."""
    return (
        f"# {BEGIN}\nREPOS = {list(slugs)!r}\n# {END}\n"
        "\n"
        "PROJECT_IDENTITIES = {\n"
        f'    "atlas": {{"name": "Atlas", "client": "{client}"}},\n'
        f"{entries}"
        "}\n"
        "\n"
        "\n"
        f"def {logic}(slug):\n"
        "    return PROJECT_IDENTITIES.get(slug)\n"
    )


def variable(reason="Not set.", value=None):
    return {"value": value, "reason": reason, "decided": "2026-10-15"}


def runner_routing(repos=("atlas",), readme="Which runner each repo uses.",
                   **overrides):
    """runner-routing.json: every repo under `repos`, one member per runner
    variable, as `json.dumps(indent=2)` lays it out."""
    doc = {"_readme": readme, "repos": {
        slug: {v: variable() for v in VARIABLES} for slug in repos}}
    doc["repos"].update(overrides)
    return json.dumps(doc, indent=2) + "\n"


ORG_TENANTS_COMMENT = ("Which console tenant each GitHub org belongs to "
                       "(DRE-8210): the org, lowercased, to a tenant slug.")
ORGS = {"dreadnought-foundry": "dreadnought-foundry",
        "everybite": "everybite"}


def org_tenants(orgs=None, comment=ORG_TENANTS_COMMENT, **added):
    """org-tenants.json the way agent-bureau keeps it: `_comment`, then the
    `orgs` object, as `json.dumps(indent=2)` lays it out. `added` is appended
    to `orgs` after its existing members, where the routing step puts one."""
    doc = {"_comment": comment, "orgs": {**(ORGS if orgs is None else orgs),
                                         **added}}
    return json.dumps(doc, indent=2) + "\n"


def appended_as_text(text, org, tenant):
    """`text` with one member appended to `orgs` as text, the way
    `80_routing`'s `add_org_entry` writes it: after the last member, so no
    other line moves."""
    last = '"everybite": "everybite"'
    assert text.count(last) == 1
    return text.replace(last, f'{last},\n    "{org}": "{tenant}"')


def fact_commit(path, before, after):
    return commit((path,), sources={path: (before, after)})


def onboarding_commits():
    """The three commits #3793 carried, one fact each — sources.py with its
    regenerated region and its new identity in ONE write (DRE-7379)."""
    return [
        fact_commit(SOURCES, sources(),
                    sources(AFTER, entries=IDENTITY_ENTRY)),
        fact_commit(OIDC, oidc(), oidc(entries=OIDC_ENTRY)),
        fact_commit(RUNNER_ROUTING, runner_routing(),
                    runner_routing(("atlas", "foundry"))),
    ]


def code_paths(c):
    return tdd._code_paths(tdd.mark_routing_paths([c])[0])


class ConsoleFactsSetTest(unittest.TestCase):

    def test_the_fact_files_are_routing_paths(self):
        for path in (OIDC, RUNNER_ROUTING, SOURCES):
            with self.subTest(path=path):
                self.assertIn(path, tdd.ONBOARD_ROUTING_PATHS)

    def test_every_fact_file_is_code_without_the_relief(self):
        # Guards the passes below from going vacuous: if these paths were
        # docs or ops already, relieving them would prove nothing.
        for path in FACTS:
            with self.subTest(path=path):
                self.assertEqual(tdd.classify_path(path), "code")


class FactReliefTest(unittest.TestCase):
    """One added entry, in the shape the engine writes, is relieved once the
    PR is marked as the onboarding routing PR — and only then."""

    def test_each_of_the_three_commits_is_relieved_once_marked(self):
        for c in onboarding_commits():
            with self.subTest(path=c["paths"][0]):
                self.assertEqual(code_paths(c), [])
                self.assertNotIn("code", tdd.commit_categories(
                    tdd.mark_routing_paths([c])[0]))

    def test_each_of_the_three_commits_is_code_unmarked(self):
        for c in onboarding_commits():
            with self.subTest(path=c["paths"][0]):
                self.assertEqual(tdd._code_paths(c), c["paths"])

    def test_the_three_commits_pass_the_rule_once_marked(self):
        ok, reason = tdd.check_commits(
            tdd.mark_routing_paths(onboarding_commits()))
        self.assertTrue(ok, reason)

    def test_the_three_commits_fail_the_rule_unmarked(self):
        ok, reason = tdd.check_commits(onboarding_commits())
        self.assertFalse(ok)
        self.assertEqual(reason, tdd.FAILURE_MESSAGE)

    def test_an_identity_alone_without_a_region_change_is_relieved(self):
        c = fact_commit(SOURCES, sources(), sources(entries=IDENTITY_ENTRY))
        self.assertEqual(code_paths(c), [])

    def test_a_region_only_sources_change_is_still_relieved(self):
        # DRE-7209's relief for this mirror is untouched.
        c = fact_commit(SOURCES, sources(), sources(AFTER))
        self.assertEqual(code_paths(c), [])

    def test_the_entry_need_not_be_the_last(self):
        before = oidc()
        after = before.replace('    "atlas"', OIDC_ENTRY + '    "atlas"')
        self.assertEqual(code_paths(fact_commit(OIDC, before, after)), [])


class FactExactnessTest(unittest.TestCase):
    """Any other line in a fact file stays code: the relief is one entry,
    never the file."""

    def assert_code(self, path, before, after):
        self.assertEqual(code_paths(fact_commit(path, before, after)), [path])

    # github_oidc.py

    def test_a_changed_existing_repo_ids_entry_is_code(self):
        self.assert_code(OIDC, oidc(),
                         oidc(ids=('"111"', '"999"'), entries=OIDC_ENTRY))
        self.assert_code(OIDC, oidc(), oidc(ids=('"111"', '"999"')))

    def test_a_second_repo_ids_entry_is_code(self):
        self.assert_code(OIDC, oidc(), oidc(
            entries=OIDC_ENTRY + '    "harbor": ("1", "2"),\n'))

    def test_an_edited_function_beside_the_entry_is_code(self):
        self.assert_code(OIDC, oidc(),
                         oidc(entries=OIDC_ENTRY, logic="repo_ids_anywhere"))

    def test_a_repo_ids_entry_of_another_shape_is_code(self):
        for entry in (
            '    "foundry": ("226758887", "1412115043", "x"),\n',
            '    "foundry": ("226758887",),\n',
            '    "foundry": (226758887, 1412115043),\n',
            '    "foundry": ("owner", "1412115043"),\n',
            '    "foundry": lookup("foundry"),\n',
            '    "foundry": ("226758887", str(1412115043)),\n',
            '    **OTHER_IDS,\n',
            '    FOUNDRY: ("226758887", "1412115043"),\n',
        ):
            with self.subTest(entry=entry):
                self.assert_code(OIDC, oidc(), oidc(entries=entry))

    def test_a_comment_line_beside_the_entry_is_code(self):
        after = oidc(entries=OIDC_ENTRY).replace(
            "\n\ndef ", "\n# routes every repo\n\ndef ")
        self.assert_code(OIDC, oidc(), after)

    def test_code_smuggled_onto_the_entrys_own_line_is_code(self):
        # The only added line is the entry's, so the line comparison alone
        # would pass it; the closed literal and the call after it are not
        # the entry.
        self.assert_code(OIDC, oidc(), oidc(
            entries='    "foundry": ("226758887", "1412115043")}; '
                    'exfiltrate(); _ = {\n'))

    def test_an_entry_added_to_another_literal_is_code(self):
        before = oidc() + "\nOTHER_IDS = {\n}\n"
        after = oidc() + "\nOTHER_IDS = {\n" + OIDC_ENTRY + "}\n"
        self.assert_code(OIDC, before, after)

    def test_a_new_or_deleted_fact_file_is_code(self):
        for path, text in ((OIDC, oidc(entries=OIDC_ENTRY)),
                           (SOURCES, sources(entries=IDENTITY_ENTRY)),
                           (RUNNER_ROUTING, runner_routing())):
            with self.subTest(path=path):
                self.assert_code(path, None, text)
                self.assert_code(path, text, None)

    def test_python_that_will_not_parse_is_code(self):
        self.assert_code(OIDC, oidc(), oidc(entries=OIDC_ENTRY) + "def (:\n")

    def test_a_repo_mirror_region_in_a_fact_file_relieves_nothing(self):
        # github_oidc.py is not a slug mirror: no generator proves a region
        # in it, so markers written into it buy nothing.
        def marked(slugs):
            return f"# {BEGIN}\nSLUGS = {slugs!r}\n# {END}\n" + oidc()
        self.assert_code(OIDC, marked(BEFORE), marked(AFTER))
        self.assert_code(OIDC, marked(BEFORE),
                         marked(AFTER).replace("REPO_IDS = {\n",
                                               "REPO_IDS = {\n" + OIDC_ENTRY))

    # sources.py

    def test_a_second_project_identity_is_code(self):
        self.assert_code(SOURCES, sources(), sources(
            AFTER, entries=IDENTITY_ENTRY
            + '    "harbor": {"name": "Harbor", "client": "Harbor"},\n'))

    def test_a_changed_existing_project_identity_is_code(self):
        self.assert_code(SOURCES, sources(), sources(
            AFTER, entries=IDENTITY_ENTRY, client="Someone Else"))

    def test_an_edited_function_in_sources_is_code(self):
        self.assert_code(SOURCES, sources(), sources(
            AFTER, entries=IDENTITY_ENTRY, logic="project_identity_any"))

    def test_a_project_identity_of_another_shape_is_code(self):
        for entry in (
            '    "foundry": {"name": "Foundry"},\n',
            '    "foundry": {"name": "Foundry", "client": "D", "x": "y"},\n',
            '    "foundry": {"name": "Foundry", "client": tenant()},\n',
            '    "foundry": ("Foundry", "Dreadnought Foundry"),\n',
        ):
            with self.subTest(entry=entry):
                self.assert_code(SOURCES, sources(),
                                 sources(AFTER, entries=entry))

    def test_an_edited_mirror_marker_with_the_entry_is_code(self):
        self.assert_code(SOURCES, sources(), sources(
            AFTER, entries=IDENTITY_ENTRY).replace(END, END + " (moved)"))

    # runner-routing.json

    def test_a_second_runner_routing_repo_is_code(self):
        self.assert_code(RUNNER_ROUTING, runner_routing(),
                         runner_routing(("atlas", "foundry", "harbor")))

    def test_a_changed_existing_runner_routing_repo_is_code(self):
        changed = {v: variable(value="self-hosted") for v in VARIABLES}
        self.assert_code(RUNNER_ROUTING, runner_routing(),
                         runner_routing(("atlas", "foundry"), atlas=changed))
        self.assert_code(RUNNER_ROUTING, runner_routing(),
                         runner_routing(atlas=changed))

    def test_another_top_level_change_is_code(self):
        self.assert_code(RUNNER_ROUTING, runner_routing(), runner_routing(
            ("atlas", "foundry"), readme="Routing, rewritten."))
        doc = json.loads(runner_routing(("atlas", "foundry")))
        doc["default"] = "self-hosted"
        self.assert_code(RUNNER_ROUTING, runner_routing(),
                         json.dumps(doc, indent=2))

    def test_a_renamed_repo_is_code(self):
        self.assert_code(RUNNER_ROUTING, runner_routing(),
                         runner_routing(("foundry",)))

    def test_a_new_repo_that_is_not_an_object_is_code(self):
        self.assert_code(RUNNER_ROUTING, runner_routing(),
                         runner_routing(foundry="self-hosted"))

    def test_a_repeated_member_is_code(self):
        # Python's parser keeps the last of two, so the document reads as the
        # one before plus the new repo; another reader may keep the first.
        after = runner_routing(("atlas", "foundry")).replace(
            '"_readme"', '"_readme": "Routing, rewritten.",\n  "_readme"', 1)
        self.assert_code(RUNNER_ROUTING, runner_routing(), after)

    def test_json_that_will_not_parse_is_code(self):
        self.assert_code(RUNNER_ROUTING, runner_routing(),
                         runner_routing(("atlas", "foundry")) + "}")

    def test_a_rider_beside_the_facts_is_code(self):
        c = onboarding_commits()[1]
        c["paths"].append("scripts/widget.py")
        self.assertEqual(code_paths(c), ["scripts/widget.py"])


class OnboardFactsCliTest(GitRepoMixin, unittest.TestCase):
    """End-to-end over a real temp repo, replaying #3793's shape: the three
    files exist on main, and the branch writes one fact per commit."""

    def setUp(self):
        super().setUp()
        self.write(SOURCES, sources())
        self.write(OIDC, oidc())
        self.write(RUNNER_ROUTING, runner_routing())
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: seed the console facts")

    def _commit(self, rel, content, subject=None):
        self.write(rel, content)
        self.git("add", "-A")
        self.git("commit", "-q", "-m",
                 subject or f"chore(routing): onboard foundry — {rel}",
                 "--author", f"{BOT} <{BOT}@users.noreply.github.com>")

    def _replay(self, branch=ONBOARD, **swap):
        """The three commits, in #3793's order. `swap` replaces one file's
        after side, keyed by its basename without the extension."""
        self.git("checkout", "-q", "-B", branch, "main")
        after = {
            SOURCES: sources(AFTER, entries=IDENTITY_ENTRY),
            OIDC: oidc(entries=OIDC_ENTRY),
            RUNNER_ROUTING: runner_routing(("atlas", "foundry")),
        }
        for path in FACTS:
            key = Path(path).stem.replace("-", "_")
            self._commit(path, swap.get(key, after[path]))

    def test_the_three_commits_on_bot_onboard_by_the_bot_exit_0(self):
        self._replay()
        p = self.run_check(head_ref=ONBOARD, author=BOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for path in FACTS:
            with self.subTest(path=path):
                self.assertRegex(
                    p.stdout, rf"(?m)\[docs\] .*— {re.escape(path)}$")
        self.assertNotIn("[code]", p.stdout)

    def test_the_opener_can_arrive_from_the_event_payload(self):
        self._replay()
        p = self.run_check(github_head_ref=ONBOARD, event_author=BOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_any_other_line_in_a_fact_file_fails(self):
        for key, after in (
            ("github_oidc", oidc(ids=('"111"', '"999"'), entries=OIDC_ENTRY)),
            ("runner_routing", runner_routing(("atlas", "foundry", "harbor"))),
            ("sources", sources(AFTER, entries=IDENTITY_ENTRY,
                                logic="project_identity_any")),
        ):
            with self.subTest(key=key):
                self._replay(**{key: after})
                p = self.run_check(head_ref=ONBOARD, author=BOT)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn(tdd.FAILURE_MESSAGE, p.stdout)

    def test_any_other_branch_fails(self):
        for branch in ("bot/onboard-x", "agent/DRE-7431-x"):
            with self.subTest(branch=branch):
                self._replay(branch=branch)
                p = self.run_check(head_ref=branch, author=BOT)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("[code]", p.stdout)

    def test_any_other_opener_fails(self):
        self._replay()
        for opener in ("mallory", "agent-bureau-qa-bot[bot]"):
            with self.subTest(opener=opener):
                p = self.run_check(head_ref=ONBOARD, author=opener)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("[code]", p.stdout)

    def test_no_attested_opener_fails(self):
        self._replay()
        p = self.run_check(head_ref=ONBOARD)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)


# DRE-8249 — the owner org's tenant, one member of `orgs` in org-tenants.json.

NEWCO_BEFORE = org_tenants()
NEWCO_AFTER = appended_as_text(NEWCO_BEFORE, "newco", "demo")


class OrgTenantsSetTest(unittest.TestCase):

    def test_org_tenants_is_a_routing_path(self):
        self.assertIn(ORG_TENANTS, tdd.ONBOARD_ROUTING_PATHS)

    def test_org_tenants_is_code_without_the_relief(self):
        # Guards the passes below from going vacuous.
        self.assertEqual(tdd.classify_path(ORG_TENANTS), "code")

    def test_the_text_insertion_is_the_documents_new_member(self):
        # The fixture is faithful: appending as text reads as the document
        # with `newco` added last.
        self.assertEqual(json.loads(NEWCO_AFTER),
                         json.loads(org_tenants(newco="demo")))


class OrgTenantsReliefTest(unittest.TestCase):

    def test_one_new_org_is_relieved_once_marked(self):
        c = fact_commit(ORG_TENANTS, NEWCO_BEFORE, NEWCO_AFTER)
        self.assertEqual(code_paths(c), [])
        self.assertNotIn("code", tdd.commit_categories(
            tdd.mark_routing_paths([c])[0]))
        self.assertTrue(tdd.is_routing_entry_change(
            ORG_TENANTS, NEWCO_BEFORE, NEWCO_AFTER))

    def test_one_new_org_is_code_unmarked(self):
        c = fact_commit(ORG_TENANTS, NEWCO_BEFORE, NEWCO_AFTER)
        self.assertEqual(tdd._code_paths(c), [ORG_TENANTS])

    def test_one_new_org_passes_the_rule_once_marked(self):
        commits = [fact_commit(ORG_TENANTS, NEWCO_BEFORE, NEWCO_AFTER)]
        ok, reason = tdd.check_commits(tdd.mark_routing_paths(commits))
        self.assertTrue(ok, reason)
        ok, reason = tdd.check_commits(commits)
        self.assertFalse(ok)
        self.assertEqual(reason, tdd.FAILURE_MESSAGE)

    def test_a_new_org_in_the_first_empty_orgs_is_relieved(self):
        self.assertEqual(code_paths(fact_commit(
            ORG_TENANTS, org_tenants({}), org_tenants({}, newco="demo"))), [])

    def test_it_rides_with_the_other_facts(self):
        commits = onboarding_commits() + [
            fact_commit(ORG_TENANTS, NEWCO_BEFORE, NEWCO_AFTER)]
        ok, reason = tdd.check_commits(tdd.mark_routing_paths(commits))
        self.assertTrue(ok, reason)

    def test_it_is_not_a_mirror_change(self):
        # org-tenants.json is a fact file: no generator proves a region in
        # it, so repo-mirror markers relieve nothing there.
        self.assertFalse(tdd.is_routing_mirror_change(
            ORG_TENANTS, NEWCO_BEFORE, NEWCO_AFTER))


class OrgTenantsExactnessTest(unittest.TestCase):
    """Any change to org-tenants.json but one new lowercased org with a
    string tenant stays code."""

    def assert_code(self, before, after):
        self.assertEqual(
            code_paths(fact_commit(ORG_TENANTS, before, after)),
            [ORG_TENANTS])

    def test_an_existing_orgs_tenant_changed_is_code(self):
        changed = {**ORGS, "everybite": "dreadnought-foundry"}
        self.assert_code(NEWCO_BEFORE, org_tenants(changed))
        self.assert_code(NEWCO_BEFORE, org_tenants(changed, newco="demo"))

    def test_an_entry_removed_is_code(self):
        self.assert_code(NEWCO_BEFORE,
                         org_tenants({"dreadnought-foundry":
                                      "dreadnought-foundry"}))
        # The removal relief is not extended to this file either: the
        # routing step never removes an org, and retirement leaves it.
        self.assert_code(NEWCO_AFTER, NEWCO_BEFORE)
        self.assertFalse(tdd.is_routing_entry_removal(
            ORG_TENANTS, NEWCO_AFTER, NEWCO_BEFORE))

    def test_an_entry_renamed_is_code(self):
        self.assert_code(NEWCO_BEFORE, org_tenants(
            {"dreadnought-foundry": "dreadnought-foundry",
             "newco": "everybite"}))

    def test_two_entries_added_is_code(self):
        self.assert_code(NEWCO_BEFORE,
                         org_tenants(newco="demo", otherco="demo"))

    def test_the_comment_edited_is_code(self):
        self.assert_code(NEWCO_BEFORE, org_tenants(
            comment="Rewritten.", newco="demo"))
        self.assert_code(NEWCO_BEFORE, org_tenants(comment="Rewritten."))

    def test_an_org_that_is_not_lowercased_is_code(self):
        for org in ("NewCo", "NEWCO", "newCo"):
            with self.subTest(org=org):
                self.assert_code(NEWCO_BEFORE, org_tenants(**{org: "demo"}))

    def test_a_tenant_that_is_not_a_string_is_code(self):
        for tenant in ({"slug": "demo"}, ["demo"], None, 7, True):
            with self.subTest(tenant=tenant):
                self.assert_code(NEWCO_BEFORE, org_tenants(newco=tenant))

    def test_another_top_level_change_is_code(self):
        doc = json.loads(NEWCO_AFTER)
        doc["default"] = "demo"
        self.assert_code(NEWCO_BEFORE, json.dumps(doc, indent=2))
        doc = json.loads(NEWCO_AFTER)
        del doc["_comment"]
        self.assert_code(NEWCO_BEFORE, json.dumps(doc, indent=2))

    def test_the_new_org_outside_orgs_is_code(self):
        doc = json.loads(NEWCO_BEFORE)
        doc["newco"] = "demo"
        self.assert_code(NEWCO_BEFORE, json.dumps(doc, indent=2))

    def test_a_repeated_member_is_code(self):
        after = NEWCO_AFTER.replace(
            '"_comment"', '"_comment": "Rewritten.",\n  "_comment"', 1)
        self.assert_code(NEWCO_BEFORE, after)
        after = NEWCO_AFTER.replace(
            '"everybite": "everybite"',
            '"everybite": "dreadnought-foundry",\n    "everybite": "everybite"')
        self.assert_code(NEWCO_BEFORE, after)

    def test_json_that_will_not_parse_is_code(self):
        self.assert_code(NEWCO_BEFORE, NEWCO_AFTER + "}")
        self.assert_code(NEWCO_BEFORE, NEWCO_AFTER.replace(
            '"newco"', "newco"))

    def test_orgs_that_is_not_an_object_is_code(self):
        doc = {"_comment": ORG_TENANTS_COMMENT, "orgs": [["newco", "demo"]]}
        self.assert_code(json.dumps({**doc, "orgs": []}, indent=2),
                         json.dumps(doc, indent=2))

    def test_a_new_or_deleted_org_tenants_is_code(self):
        self.assert_code(None, NEWCO_AFTER)
        self.assert_code(NEWCO_AFTER, None)

    def test_a_rider_beside_the_org_is_code(self):
        c = fact_commit(ORG_TENANTS, NEWCO_BEFORE, NEWCO_AFTER)
        c["paths"].append("scripts/widget.py")
        self.assertEqual(code_paths(c), ["scripts/widget.py"])


class OrgTenantsCliTest(GitRepoMixin, unittest.TestCase):
    """End-to-end over a real temp repo, replaying the commit `80_routing`
    writes for a company new to the console: org-tenants.json exists on
    main, and the branch appends one `"newco": "demo"` member to `orgs`."""

    def setUp(self):
        super().setUp()
        self.write(ORG_TENANTS, NEWCO_BEFORE)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: seed org-tenants.json")

    def _replay(self, after=NEWCO_AFTER, branch=ONBOARD):
        self.git("checkout", "-q", "-B", branch, "main")
        self.write(ORG_TENANTS, after)
        self.git("add", "-A")
        self.git("commit", "-q", "-m",
                 f"chore(routing): onboard newco — {ORG_TENANTS}",
                 "--author", f"{BOT} <{BOT}@users.noreply.github.com>")

    def test_one_new_org_on_bot_onboard_by_the_bot_exits_0(self):
        self._replay()
        p = self.run_check(head_ref=ONBOARD, author=BOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(
            p.stdout, rf"(?m)\[docs\] .*— {re.escape(ORG_TENANTS)}$")
        self.assertNotIn("[code]", p.stdout)

    def test_the_opener_can_arrive_from_the_event_payload(self):
        self._replay()
        p = self.run_check(github_head_ref=ONBOARD, event_author=BOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_any_other_change_to_the_file_fails(self):
        for name, after in (
            ("tenant changed", org_tenants(
                {**ORGS, "everybite": "dreadnought-foundry"})),
            ("entry removed", org_tenants(
                {"dreadnought-foundry": "dreadnought-foundry"})),
            ("two entries added", org_tenants(newco="demo", otherco="demo")),
            ("comment edited", org_tenants(comment="Rewritten.",
                                           newco="demo")),
        ):
            with self.subTest(name=name):
                self._replay(after)
                p = self.run_check(head_ref=ONBOARD, author=BOT)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn(tdd.FAILURE_MESSAGE, p.stdout)
                self.assertIn("[code]", p.stdout)

    def test_any_other_branch_fails(self):
        for branch in ("bot/onboard-x", "agent/DRE-8249-x", "main-onboard"):
            with self.subTest(branch=branch):
                self._replay(branch=branch)
                p = self.run_check(head_ref=branch, author=BOT)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("[code]", p.stdout)

    def test_any_other_opener_fails(self):
        self._replay()
        for opener in ("mallory", "agent-bureau-qa-bot[bot]",
                       "agent-bureau-bot-3"):
            with self.subTest(opener=opener):
                p = self.run_check(head_ref=ONBOARD, author=opener)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("[code]", p.stdout)

    def test_no_attested_opener_fails(self):
        self._replay()
        p = self.run_check(head_ref=ONBOARD)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
