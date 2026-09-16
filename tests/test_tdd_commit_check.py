"""RED-first tests for the TDD commit-discipline check (DRE-2022).

Origin: bureau-pipeline's own PRs are hand-built (not dispatched), so TDD was
enforced only by convention in builder prompts plus the critic's judgment.
The rail's discipline — fail tests → implementation → checks → PR → critic →
merge — needs a mechanical enforcer here too.

The fix is a cheap, deterministic script (no LLM call) run as a job in the
Pipeline Tests workflow: on the PR's ordered commit list, at least one commit
touching files under `tests/` must appear BEFORE the first commit that changes
non-test code. Docs-only and ops-only PRs are exempt, classified by changed
paths. The failure message says exactly what's missing in plain language.

These tests must FAIL before scripts/check_tdd_commits.py exists / before
tests.yml gains the job, and PASS after.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
SCRIPT = ROOT / "scripts" / "check_tdd_commits.py"

sys.path.insert(0, str(ROOT / "scripts"))

import check_tdd_commits  # noqa: E402


def commit(paths, subject="a commit"):
    """A commit record the way the checker consumes them (oldest-first list)."""
    return {"sha": "f" * 40, "subject": subject, "paths": list(paths)}


class ClassifyPathTest(unittest.TestCase):
    """Path → category. The categories drive both the ordering rule (test vs
    code) and the exemption (docs-only / ops-only PRs)."""

    def test_tests_tree_is_test(self):
        self.assertEqual(check_tdd_commits.classify_path("tests/test_x.py"), "test")

    def test_scripts_tree_is_code(self):
        self.assertEqual(check_tdd_commits.classify_path("scripts/reconcile.py"), "code")

    def test_docs_tree_is_docs(self):
        self.assertEqual(check_tdd_commits.classify_path("docs/self-hosting.md"), "docs")

    def test_any_markdown_is_docs(self):
        # README, standards/, briefs/ — prose lives in .md wherever it sits.
        self.assertEqual(check_tdd_commits.classify_path("README.md"), "docs")
        self.assertEqual(check_tdd_commits.classify_path("standards/comms.md"), "docs")
        self.assertEqual(check_tdd_commits.classify_path("briefs/engineer.md"), "docs")

    def test_github_tree_is_ops(self):
        self.assertEqual(
            check_tdd_commits.classify_path(".github/workflows/tests.yml"), "ops"
        )

    def test_config_tree_is_ops(self):
        self.assertEqual(check_tdd_commits.classify_path("config/repos.yml"), "ops")

    def test_agents_registry_is_ops(self):
        self.assertEqual(check_tdd_commits.classify_path("agents.yaml"), "ops")

    # --- the catalog snapshot is data (DRE-3879) ------------------------
    #
    # `models.json` is the snapshot `model-drift.yml` refreshes from the
    # Anthropic catalog once a week — the seam the console reads, data the
    # job derives rather than code anybody authored. It classified as `code`
    # (it is neither a docs path nor an ops one), so the weekly regeneration
    # could not satisfy a check whose finding is the ORDER of commits that
    # already exist: there is no RED test to write for a vendor's model list,
    # and DRE-2694 means no added commit clears it.
    #
    # The CEO decided it on 2026-09-16 (signed console answer): count
    # `models.json` as data, beside `config/` and `agents.yaml` — and NOTHING
    # ELSE is exempted. The tests below hold both halves of that sentence.

    def test_the_catalog_snapshot_is_ops(self):
        self.assertEqual(check_tdd_commits.classify_path("models.json"), "ops")

    def test_the_snapshot_exemption_is_that_one_path_and_no_other(self):
        # Exact-path membership, never a prefix, a suffix or a directory: a
        # `models.json` somewhere else in the tree is somebody's source file.
        for path in ("console/models.json", "scripts/models.json",
                     "models.json.bak", "my-models.json", "models.yaml"):
            with self.subTest(path=path):
                self.assertNotEqual(
                    check_tdd_commits.classify_path(path), "ops",
                    f"{path} gained an exemption nobody decided on",
                )

    def test_no_other_data_file_joined_the_list(self):
        # The whole ops list, pinned. A later edit that quietly adds a second
        # root-level data file is a new TDD exemption, and that is a decision
        # rather than a tidy-up.
        self.assertEqual(set(check_tdd_commits._OPS_FILES), {"agents.yaml",
                                                             "models.json"})
        self.assertEqual(tuple(check_tdd_commits._OPS_PREFIXES),
                         (".github/", "config/"))

    def test_a_python_change_beside_the_snapshot_still_needs_its_test_first(self):
        """The boundary the CEO drew, as the check sees it: a pull request
        that refreshes `models.json` AND edits code still owes the RED test
        first. The exemption is about which files need one, never about
        whether the rule applies."""
        ok, reason = check_tdd_commits.check_commits([
            commit(["models.json"], "chore(models): refresh the snapshot"),
            commit(["scripts/model_catalog.py"], "feat: a real behaviour change"),
        ])
        self.assertFalse(ok, reason)
        self.assertEqual(reason, check_tdd_commits.FAILURE_MESSAGE)
        # …and with the test commit first, the same pair passes.
        ok, _ = check_tdd_commits.check_commits([
            commit(["tests/test_model_catalog.py"], "RED"),
            commit(["models.json"], "chore(models): refresh the snapshot"),
            commit(["scripts/model_catalog.py"], "feat: a real behaviour change"),
        ])
        self.assertTrue(ok)

    def test_a_snapshot_only_pull_request_is_exempt(self):
        ok, reason = check_tdd_commits.check_commits([
            commit(["models.json"], "chore(models): refresh the catalog snapshot"),
        ])
        self.assertTrue(ok, reason)

    def test_the_bot_branches_get_no_identity_exemption(self):
        """The exemption the CEO chose is the PATH, not the branch. Neither
        scheduled job's branch buys its pull request the standards-sync
        identity exemption — a `.py` change riding on one is still code."""
        for branch in ("bot/split-ledger", "bot/model-drift"):
            with self.subTest(branch=branch):
                self.assertFalse(check_tdd_commits.is_standards_sync_pr(
                    branch, "agent-bureau-bot", ["agent-bureau-bot"]))

    # --- nested test trees (DRE-2741) -----------------------------------
    #
    # DRE-2022 built this check for bureau-pipeline's OWN PRs, and this repo
    # keeps its tests in a top-level `tests/`. The check then went fleet-wide
    # and the classifier did not. Every consumer that nests its tests — which
    # is all of them — had no path that could ever classify as `test`, so a
    # genuinely test-first branch still read as code-then-code and failed a
    # check DRE-2694 makes unfixable-by-another-commit. Measured on
    # agent-bureau: 0 of its 4 test trees matched.

    def test_a_nested_tests_directory_is_test(self):
        # console/backend/tests/, cloud/relay/tests/ — the common shape.
        self.assertEqual(
            check_tdd_commits.classify_path("console/backend/tests/test_auth.py"),
            "test",
        )

    def test_a_singular_test_directory_is_test(self):
        # infra/test/ — jest's convention, and agent-bureau's for CDK.
        self.assertEqual(
            check_tdd_commits.classify_path("infra/test/rollback/test_rollback.py"),
            "test",
        )

    def test_a_dotted_test_suffix_is_test(self):
        # A TS/JS test need not live in a test directory at all.
        self.assertEqual(
            check_tdd_commits.classify_path("infra/lib/backend-stack.test.ts"), "test"
        )
        self.assertEqual(
            check_tdd_commits.classify_path("web/src/App.spec.tsx"), "test"
        )

    def test_a_test_prefixed_filename_is_test(self):
        # pytest's own discovery rule, wherever the file sits.
        self.assertEqual(
            check_tdd_commits.classify_path("scripts/test_reconcile.py"), "test"
        )

    def test_a_go_style_test_suffix_is_test(self):
        self.assertEqual(
            check_tdd_commits.classify_path("internal/relay/handler_test.go"), "test"
        )

    def test_source_living_beside_tests_is_still_code(self):
        # The limit of the widening, pinned deliberately. Only a directory
        # whose NAME says it holds tests, or a filename in a test convention,
        # counts — a helper that merely sits near them does not, or the
        # discipline could be dodged by moving implementation next to a suite.
        self.assertEqual(
            check_tdd_commits.classify_path("console/backend/services/tenant.py"),
            "code",
        )
        self.assertEqual(
            check_tdd_commits.classify_path("infra/rollback-console.sh"), "code"
        )
        self.assertEqual(
            check_tdd_commits.classify_path("scripts/latest_test_results.py"), "code"
        )

    def test_a_nested_test_commit_satisfies_the_ordering_rule(self):
        # The end-to-end point of the widening: the classification change must
        # actually clear the gate, not merely relabel a path.
        ok, _ = check_tdd_commits.check_commits(
            [
                commit(["infra/test/rollback/test_annotation_digest.py"], "red"),
                commit(["infra/rollback-console.sh"], "fix"),
            ]
        )
        self.assertTrue(ok)

    def test_unknown_paths_default_to_code(self):
        # Fail-closed: anything unrecognized counts as implementation, so a
        # new source tree can't silently dodge the discipline.
        self.assertEqual(check_tdd_commits.classify_path("relay/handler.py"), "code")

    def test_plugin_manifests_stay_code(self):
        # DRE-3885, and the whole reason that card is an author/branch
        # exemption rather than a path one: `plugins/**` carries the generated
        # standards-sync manifests AND real plugin source, so moving the folder
        # out of `code` would let implementation there skip the discipline
        # forever. The manifests are exempted by WHO wrote them and WHERE,
        # never by what they are called.
        for path in (
            "plugins/.claude-plugin/marketplace.json",
            "plugins/dreadnought-standards/.claude-plugin/plugin.json",
            "plugins/dreadnought-standards/skills/card-quality/hook.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(check_tdd_commits.classify_path(path), "code")

    # --- static design records (DRE-3763) --------------------------------
    #
    # agent-bureau #2523 was one commit adding one file — the CEO-approved
    # Green Light page, `console/design/screens/web-green-light-after-2026-09-13.html`
    # — and it classified as `code`, so "no test commit precedes the
    # implementation" failed a check no added commit can clear (DRE-2694). The
    # only RED test you can write for a static page is a vacuous one, which the
    # engineering standard bans. A design record is documentation of a decision;
    # it is docs by the same reasoning as `docs/` and `*.md`.

    def test_a_design_screen_under_console_design_is_docs(self):
        # The exact path that stranded #2523.
        self.assertEqual(
            check_tdd_commits.classify_path(
                "console/design/screens/web-green-light-after-2026-09-13.html"
            ),
            "docs",
        )

    def test_every_static_design_record_extension_is_docs(self):
        for path in (
            "console/design/screens/web-one-river-header-2026-09-11.html",
            "console/design/DESIGN.md",
            "console/design/images/one-river-1280-light.png",
            "console/design/images/pulse.jpg",
            "console/design/images/pulse.jpeg",
            "console/design/brand/logomark.svg",
            "console/design/bureau-console.pen",
            "console/design/_ds_manifest.json",
        ):
            with self.subTest(path=path):
                self.assertEqual(check_tdd_commits.classify_path(path), "docs")

    def test_a_top_level_design_directory_is_docs_too(self):
        # project-template (what every new repo is built from) and deltasolv
        # keep the same record at the repo root, not under console/.
        self.assertEqual(
            check_tdd_commits.classify_path("design/screens/web-dashboard.html"),
            "docs",
        )
        self.assertEqual(
            check_tdd_commits.classify_path("design/foundations/colors.html"),
            "docs",
        )

    def test_an_uppercase_image_extension_is_still_a_record(self):
        # A screenshot saved as `.PNG` is the same record as one saved `.png`.
        self.assertEqual(
            check_tdd_commits.classify_path("console/design/images/shot.PNG"),
            "docs",
        )

    def test_source_under_a_design_directory_is_still_code(self):
        # The limit of the exemption, pinned. CSS tokens feed the app build and
        # a `.ts`/`.tsx`/`.js`/`.py` file is source wherever it sits — the
        # extension allowlist, not the directory, is what makes a file a record.
        for path in (
            "console/design/tokens.css",
            "console/design/fonts.css",
            "console/design/logomark.js",
            "console/design/components/Badge.ts",
            "console/design/components/react/StatusBadge.tsx",
            "console/design/build_manifest.py",
            "design/tokens.css",
            "design/tailwind.preset.js",
            "design/components/react/StatusBadge.tsx",
        ):
            with self.subTest(path=path):
                self.assertEqual(check_tdd_commits.classify_path(path), "code")

    def test_unlisted_extensions_under_design_stay_code(self):
        # Fail-closed: only the listed record formats move out of `code`.
        for path in (
            "console/design/agent-field.glsl",
            "console/design/specs/web-sign-up.txt",
        ):
            with self.subTest(path=path):
                self.assertEqual(check_tdd_commits.classify_path(path), "code")

    def test_a_design_directory_nested_inside_source_is_not_a_record(self):
        # Matched by PREFIX, not by directory segment the way test trees are: a
        # `design/` folder inside an application's source is part of the app, and
        # its JSON can be imported by the build.
        for path in (
            "web/src/design/theme.json",
            "console/frontend/src/design/screen.html",
            "packages/ui/design/tokens.json",
        ):
            with self.subTest(path=path):
                self.assertEqual(check_tdd_commits.classify_path(path), "code")

    def test_the_existing_docs_rules_are_unchanged(self):
        self.assertEqual(check_tdd_commits.classify_path("docs/runbook.html"), "docs")
        self.assertEqual(check_tdd_commits.classify_path("docs/self-hosting.md"), "docs")
        self.assertEqual(check_tdd_commits.classify_path("console/backend/NOTES.md"), "docs")
        # A non-.md file outside docs/ and outside a design record is still code.
        self.assertEqual(check_tdd_commits.classify_path("console/index.html"), "code")
        self.assertEqual(check_tdd_commits.classify_path("brand/logo.svg"), "code")


class CheckCommitsTest(unittest.TestCase):
    """The ordering rule on an oldest-first commit list."""

    # --- the acceptance cases, verbatim from the card -------------------

    def test_red_test_then_fix_passes(self):
        ok, _ = check_tdd_commits.check_commits([
            commit(["tests/test_widget.py"], "test(DRE-1): RED"),
            commit(["scripts/widget.py"], "fix(DRE-1): make it pass"),
        ])
        self.assertTrue(ok)

    def test_implementation_first_fails_with_plain_language_message(self):
        ok, reason = check_tdd_commits.check_commits([
            commit(["scripts/widget.py"], "fix(DRE-1): implementation"),
            commit(["tests/test_widget.py"], "test(DRE-1): after the fact"),
        ])
        self.assertFalse(ok)
        self.assertEqual(reason, check_tdd_commits.FAILURE_MESSAGE)

    def test_docs_only_pr_passes_without_a_test_commit(self):
        ok, _ = check_tdd_commits.check_commits([
            commit(["docs/self-hosting.md", "README.md"], "docs: notes"),
        ])
        self.assertTrue(ok)

    # --- exemption boundaries --------------------------------------------

    def test_ops_only_pr_passes_without_a_test_commit(self):
        ok, _ = check_tdd_commits.check_commits([
            commit([".github/workflows/reconcile.yml"], "ops: tweak schedule"),
        ])
        self.assertTrue(ok)

    def test_mixed_docs_and_ops_pr_is_still_exempt(self):
        ok, _ = check_tdd_commits.check_commits([
            commit(["docs/adr.md", ".github/workflows/medic.yml"], "chore"),
        ])
        self.assertTrue(ok)

    def test_tests_only_pr_passes(self):
        ok, _ = check_tdd_commits.check_commits([
            commit(["tests/test_more_coverage.py"], "test: backfill"),
        ])
        self.assertTrue(ok)

    def test_empty_commit_list_passes(self):
        ok, _ = check_tdd_commits.check_commits([])
        self.assertTrue(ok)

    def test_docs_beside_code_does_not_exempt(self):
        # A README edit riding along with implementation is NOT a docs-only
        # PR — the code still needs a preceding test commit.
        ok, reason = check_tdd_commits.check_commits([
            commit(["README.md", "scripts/widget.py"], "feat: with docs"),
        ])
        self.assertFalse(ok)
        self.assertEqual(reason, check_tdd_commits.FAILURE_MESSAGE)

    # --- design records (DRE-3763) ----------------------------------------

    def test_a_design_record_only_branch_passes_without_a_test_commit(self):
        # agent-bureau #2523's shape: one commit, one static design page.
        ok, _ = check_tdd_commits.check_commits([
            commit(
                ["console/design/screens/web-green-light-after-2026-09-13.html"],
                "docs(DRE-3762): the CEO-approved Green Light design page",
            ),
        ])
        self.assertTrue(ok)

    def test_a_design_record_with_its_screenshots_and_manifest_passes(self):
        ok, _ = check_tdd_commits.check_commits([
            commit([
                "console/design/screens/web-x-2026-09-13.html",
                "console/design/images/web-x-1280-light.png",
                "console/design/_ds_manifest.json",
            ], "docs: design record"),
        ])
        self.assertTrue(ok)

    def test_a_design_record_beside_code_does_not_exempt(self):
        # A mixed commit is still code — the page rides along with a real
        # change that needs its RED test first.
        ok, reason = check_tdd_commits.check_commits([
            commit([
                "console/design/screens/web-x-2026-09-13.html",
                "console/frontend/src/views/GreenLight.tsx",
            ], "feat: page and build together"),
        ])
        self.assertFalse(ok)
        self.assertEqual(reason, check_tdd_commits.FAILURE_MESSAGE)

    def test_a_token_stylesheet_under_design_still_needs_a_test(self):
        # tokens.css feeds the app build; it is code, and a branch that changes
        # only it still needs a preceding test commit.
        ok, reason = check_tdd_commits.check_commits([
            commit(["console/design/tokens.css"], "style: new accent"),
        ])
        self.assertFalse(ok)
        self.assertEqual(reason, check_tdd_commits.FAILURE_MESSAGE)

    # --- split-commit discipline: SAME commit is not "before" ------------

    def test_test_and_code_in_one_commit_fails(self):
        # The standard is split commits: history must SHOW the test existed
        # before the fix. A mixed commit proves nothing about order.
        ok, reason = check_tdd_commits.check_commits([
            commit(["tests/test_widget.py", "scripts/widget.py"], "feat: all at once"),
        ])
        self.assertFalse(ok)
        self.assertEqual(reason, check_tdd_commits.FAILURE_MESSAGE)

    def test_test_commit_after_first_code_commit_does_not_rescue(self):
        ok, _ = check_tdd_commits.check_commits([
            commit(["scripts/a.py"], "fix: impl"),
            commit(["tests/test_a.py"], "test: late"),
            commit(["scripts/b.py"], "fix: more impl"),
        ])
        self.assertFalse(ok)

    def test_docs_commits_before_the_red_test_are_harmless(self):
        ok, _ = check_tdd_commits.check_commits([
            commit(["docs/plan.md"], "docs: plan"),
            commit(["tests/test_widget.py"], "test: RED"),
            commit(["scripts/widget.py"], "fix: green"),
        ])
        self.assertTrue(ok)

    def test_failure_message_is_the_exact_plain_language_string(self):
        # The card pins the wording — a builder reading the red check must be
        # told exactly what's missing, no jargon.
        self.assertEqual(
            check_tdd_commits.FAILURE_MESSAGE,
            "no test commit precedes the implementation — commit the RED test first",
        )


class DependabotExemptionTest(unittest.TestCase):
    """DRE-2049: dependabot-authored PRs are exempt from the RED-test-first
    rule. A dependency bump has no behavior of its own to test — its proof is
    the whole suite running against the bumped pins (the `unit` job installs
    from requirements-dev.txt). Without this, every dependabot PR carries a
    permanent red check and the merge gate can never auto-merge a
    critic-APPROVEd minor (live: bp #93, 2026-07-11)."""

    def test_dependabot_author_login_shapes_all_count(self):
        # Same normalization as reconcile.is_dependabot_pr: GraphQL surfaces
        # "dependabot", REST "dependabot[bot]", gh's bot marker "app/dependabot".
        for login in ("dependabot", "dependabot[bot]", "app/dependabot"):
            self.assertTrue(
                check_tdd_commits.is_dependabot_author(login),
                f"login shape {login!r} is dependabot and must be exempt",
            )

    def test_human_and_agent_authors_are_not_exempt(self):
        for login in ("alice", "agent-bureau-bot", "", None):
            self.assertFalse(check_tdd_commits.is_dependabot_author(login))

    def test_dependabot_impersonating_substring_is_not_exempt(self):
        # The match is exact on the normalized login — a user account NAMED
        # to look like the bot must not dodge the discipline.
        for login in ("notdependabot", "dependabot-fan", "dependabot2[bot]"):
            self.assertFalse(check_tdd_commits.is_dependabot_author(login))


BOT = "agent-bureau-bot[bot]"


class StandardsSyncExemptionTest(unittest.TestCase):
    """DRE-3885: the nightly standards-sync PR is exempt, the way a dependabot
    bump is — and, like dependabot's, decided by the GitHub-ATTESTED login that
    opened the PR, never by path.

    The job (agent-bureau's `standards-sync.yml`) opens one PR a night on
    `bot/standards-sync` carrying two generated manifests and one instructions
    file — no behaviour of its own, so the only RED test it could carry is a
    vacuous one, which the engineering standard bans. It failed the gate twice
    and cannot fix itself: the finding is the commit ORDER, which no added
    commit clears (DRE-2694).

    The branch and the per-commit git author NARROW the exemption to that one
    job — the bot authors most of the fleet's PRs, and a human commit riding
    along on the branch must end it — but neither authenticates anything: a
    branch name is chosen by whoever pushes it and `git commit --author` asks
    nobody's permission. Keying the exemption on those alone let ANY PR opener
    take it (found in review of this card, reproduced below in
    `test_forged_commit_authors_do_not_exempt_a_stranger_s_pr`)."""

    def test_the_bot_name_shapes_all_count(self):
        # Same normalization as the dependabot exemption: GitHub surfaces a Bot
        # identity as "agent-bureau-bot" (GraphQL), "agent-bureau-bot[bot]"
        # (REST / the git author line a workflow commit carries) or
        # "app/agent-bureau-bot" (gh's bot marker) — one actor, three spellings.
        for name in (
            "agent-bureau-bot",
            "agent-bureau-bot[bot]",
            "app/agent-bureau-bot",
        ):
            with self.subTest(name=name):
                self.assertTrue(check_tdd_commits.names_standards_sync_bot(name))

    def test_other_identities_are_not_the_sync_bot(self):
        # The pool bots and the merging identity are DIFFERENT actors, and the
        # match is exact on the normalized login so none of them inherits the
        # exemption (the DRE-2020 lesson, read the other way round).
        for name in (
            "agent-bureau-bot-3",
            "agent-bureau-qa-bot",
            "not-agent-bureau-bot",
            "agent-bureau-bot-fan[bot]",
            "dependabot[bot]",
            "alice",
            "",
            None,
        ):
            with self.subTest(name=name):
                self.assertFalse(check_tdd_commits.names_standards_sync_bot(name))

    def test_the_attested_bot_on_the_sync_branch_is_exempt(self):
        self.assertTrue(
            check_tdd_commits.is_standards_sync_pr(
                "bot/standards-sync", BOT, [BOT] * 2
            )
        )

    def test_forged_commit_authors_do_not_exempt_a_stranger_s_pr(self):
        # THE finding this exemption was rewritten for. Both narrowing signals
        # are free text: anyone who can open a PR can name their branch
        # `bot/standards-sync` and run `git commit --author "agent-bureau-bot
        # [bot] <x>"`. With the attested opener required, the forgery buys
        # nothing — and if this assertion is inverted, untested code merges
        # behind the bot's name.
        for opener in ("mallory", "agent-bureau-bot-3", "dependabot[bot]"):
            with self.subTest(pr_author=opener):
                self.assertFalse(
                    check_tdd_commits.is_standards_sync_pr(
                        "bot/standards-sync", opener, [BOT, BOT]
                    )
                )

    def test_an_unknown_opener_is_not_exempt(self):
        # Fail-closed: nothing threaded PR_AUTHOR and no event payload was
        # readable (a pre-push local run), so there is no identity to trust.
        for opener in (None, ""):
            with self.subTest(pr_author=opener):
                self.assertFalse(
                    check_tdd_commits.is_standards_sync_pr(
                        "bot/standards-sync", opener, [BOT]
                    )
                )

    def test_one_foreign_commit_on_the_sync_branch_ends_the_exemption(self):
        # EVERY commit, not the first or the last: an operator's own commit
        # pushed into the nightly PR is work the discipline still covers.
        self.assertFalse(
            check_tdd_commits.is_standards_sync_pr(
                "bot/standards-sync", BOT, [BOT, "alice"]
            )
        )
        self.assertFalse(
            check_tdd_commits.is_standards_sync_pr(
                "bot/standards-sync", BOT, ["alice", BOT]
            )
        )

    def test_the_bot_on_any_other_branch_is_still_checked(self):
        # This bot opens nearly every PR in the fleet — the branch is what
        # narrows the exemption to the one nightly job.
        for ref in (
            "agent/DRE-3885-standards-sync-tdd-exemption",
            "bot/standards-sync-2",
            "bot/standards-sync/evil",
            "evil/bot/standards-sync",
            "main",
            "",
            None,
        ):
            with self.subTest(head_ref=ref):
                self.assertFalse(
                    check_tdd_commits.is_standards_sync_pr(ref, BOT, [BOT])
                )

    def test_no_commits_is_not_exempt(self):
        # Fail-closed: `all()` over an empty list is True, which would exempt a
        # branch whose commits could not be read at all.
        self.assertFalse(
            check_tdd_commits.is_standards_sync_pr("bot/standards-sync", BOT, [])
        )


class AttestedPrAuthorTest(unittest.TestCase):
    """DRE-3885: where the one trustworthy signal comes from. `PR_AUTHOR` is
    `github.event.pull_request.user.login` threaded by a workflow; the fallback
    is the same field read out of the event payload GitHub itself writes, so
    the exemption reaches agent-bureau — which runs this checker from
    `.bureau-pipeline/scripts/` through a workflow this repo cannot edit."""

    def _event(self, payload):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        self.addCleanup(os.unlink, f.name)
        with f:
            f.write(payload)
        return f.name

    def test_threaded_pr_author_wins(self):
        self.assertEqual(
            check_tdd_commits.attested_pr_author(
                {"PR_AUTHOR": BOT,
                 "GITHUB_EVENT_PATH": self._event(
                     '{"pull_request": {"user": {"login": "alice"}}}')}
            ),
            BOT,
        )

    def test_the_event_payload_answers_when_nothing_threads_it(self):
        path = self._event('{"pull_request": {"user": {"login": "%s"}}}' % BOT)
        self.assertEqual(
            check_tdd_commits.attested_pr_author({"GITHUB_EVENT_PATH": path}),
            BOT,
        )
        # An empty PR_AUTHOR is "not threaded", not "authored by nobody".
        self.assertEqual(
            check_tdd_commits.attested_pr_author(
                {"PR_AUTHOR": "", "GITHUB_EVENT_PATH": path}),
            BOT,
        )

    def test_unreadable_or_non_pull_request_payloads_answer_nothing(self):
        # Fail-closed, every way it can go wrong: no variable at all, a path
        # that does not exist, JSON that does not parse, a push event's payload
        # (no `pull_request` key), and a payload whose login is not a string.
        cases = {
            "nothing set": {},
            "missing file": {"GITHUB_EVENT_PATH": "/nonexistent/event.json"},
            "not json": {"GITHUB_EVENT_PATH": self._event("not json {")},
            "push event": {"GITHUB_EVENT_PATH": self._event('{"ref": "main"}')},
            "login not a string": {"GITHUB_EVENT_PATH": self._event(
                '{"pull_request": {"user": {"login": null}}}')},
        }
        for label, env in cases.items():
            with self.subTest(case=label):
                self.assertIsNone(check_tdd_commits.attested_pr_author(env))


class GitRepoMixin:
    """A throwaway git repo plus the few helpers the end-to-end tests need.
    Mixed into each TestCase that drives the real script over real commits."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.repo = Path(self._td.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.write("README.md", "seed")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: seed")

    def tearDown(self):
        self._td.cleanup()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True,
                       capture_output=True, text=True)

    def write(self, rel, content):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def add_commit(self, rel, msg, author=None):
        """One commit. `author` overrides the git author line the way a bot's
        own workflow commit carries it (DRE-3885)."""
        self.write(rel, f"content for {msg}")
        self.git("add", "-A")
        extra = ["--author", f"{author} <{author}@users.noreply.github.com>"] \
            if author else []
        self.git("commit", "-q", "-m", msg, *extra)

    def run_check(self, base="main", head="HEAD", author=None, head_ref=None,
                  github_head_ref=None, event_author=None):
        """Drive the script the way CI does. `author` threads PR_AUTHOR;
        `event_author` instead writes a `pull_request` event payload and points
        GITHUB_EVENT_PATH at it, the way the runner does for a workflow that
        threads nothing."""
        env = {**os.environ}
        # Popped, never inherited: this suite runs inside a pull_request
        # workflow, where GitHub sets GITHUB_HEAD_REF and GITHUB_EVENT_PATH for
        # real — and that payload names whoever opened THIS PR.
        for var in ("PR_AUTHOR", "HEAD_REF", "GITHUB_HEAD_REF",
                    "GITHUB_EVENT_PATH"):
            env.pop(var, None)
        if author is not None:
            env["PR_AUTHOR"] = author
        if head_ref is not None:
            env["HEAD_REF"] = head_ref
        if github_head_ref is not None:
            env["GITHUB_HEAD_REF"] = github_head_ref
        if event_author is not None:
            payload = self.repo / "event.json"
            payload.write_text(
                '{"pull_request": {"user": {"login": "%s"}}}' % event_author
            )
            env["GITHUB_EVENT_PATH"] = str(payload)
        return subprocess.run(
            [sys.executable, str(SCRIPT), base, head],
            cwd=self.repo, capture_output=True, text=True, env=env,
        )


class GitCliTest(GitRepoMixin, unittest.TestCase):
    """End-to-end against a real (temp) git repo, invoked the way the
    workflow invokes it: `check_tdd_commits.py <base> <head>`. Exit 0 = pass,
    1 = discipline violation, 2 = cannot evaluate (fail loud, never pass)."""

    def test_test_first_branch_exits_0(self):
        self.git("checkout", "-q", "-b", "agent/DRE-1-x")
        self.add_commit("tests/test_widget.py", "test(DRE-1): RED")
        self.add_commit("scripts/widget.py", "fix(DRE-1): green")
        p = self.run_check()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_implementation_first_branch_exits_1_with_message(self):
        self.git("checkout", "-q", "-b", "agent/DRE-2-x")
        self.add_commit("scripts/widget.py", "fix(DRE-2): impl first")
        self.add_commit("tests/test_widget.py", "test(DRE-2): late")
        p = self.run_check()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(
            "no test commit precedes the implementation — commit the RED test first",
            p.stdout,
        )

    def test_docs_only_branch_exits_0(self):
        self.git("checkout", "-q", "-b", "docs/DRE-3-notes")
        self.add_commit("docs/notes.md", "docs(DRE-3): notes")
        p = self.run_check()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_design_record_only_branch_exits_0(self):
        # DRE-3763: agent-bureau #2523, reproduced commit for commit — one
        # commit adding one static design page, no test anywhere.
        self.git("checkout", "-q", "-b", "agent/DRE-3762-green-light-design-record")
        self.add_commit(
            "console/design/screens/web-green-light-after-2026-09-13.html",
            "docs(DRE-3762): the CEO-approved Green Light design page",
        )
        p = self.run_check()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("[docs]", p.stdout)

    def test_design_record_with_code_and_no_test_exits_1(self):
        self.git("checkout", "-q", "-b", "agent/DRE-3763-mixed")
        self.write("console/design/screens/web-x.html", "<html></html>")
        self.write("console/frontend/src/views/X.tsx", "export const X = 1;")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "feat: page and build together")
        p = self.run_check()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(check_tdd_commits.FAILURE_MESSAGE, p.stdout)

    def test_merging_advanced_main_into_the_branch_does_not_flag(self):
        # main moves on (someone else's code-only squash-merge) while the
        # branch is open; the branch merges main back in. Those mainline
        # commits are NOT the PR's own work and must not trip the check.
        self.git("checkout", "-q", "-b", "agent/DRE-4-x")
        self.add_commit("tests/test_widget.py", "test(DRE-4): RED")
        self.add_commit("scripts/widget.py", "fix(DRE-4): green")
        self.git("checkout", "-q", "main")
        self.add_commit("scripts/other.py", "feat: unrelated mainline work")
        self.git("checkout", "-q", "agent/DRE-4-x")
        self.git("merge", "-q", "--no-edit", "main")
        p = self.run_check()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_unknown_ref_exits_2_never_passes(self):
        p = self.run_check(head="no-such-ref")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)

    # --- dependabot exemption end-to-end (DRE-2049) -----------------------

    def test_dependabot_authored_bump_exits_0_without_a_test_commit(self):
        # A dependency bump: one code-classified commit (the pinned
        # manifest), no test commit anywhere — the exact shape of bp #93.
        self.git("checkout", "-q", "-b", "dependabot/pip/pip-minor-patch-1a2b3c")
        self.add_commit("requirements-dev.txt", "build(deps): bump the pip group")
        p = self.run_check(author="dependabot[bot]")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("exempt", p.stdout)
        self.assertIn("dependabot", p.stdout)

    def test_human_author_env_does_not_exempt(self):
        # PR_AUTHOR set but not dependabot: the discipline holds unchanged.
        self.git("checkout", "-q", "-b", "agent/DRE-5-x")
        self.add_commit("scripts/widget.py", "fix(DRE-5): impl first")
        p = self.run_check(author="alice")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    # --- standards-sync exemption end-to-end (DRE-3885) --------------------

    def _standards_sync_commit(self, msg, author):
        """The nightly job's own commit, reproduced: two generated manifests
        and the instructions file, in one commit, with no test anywhere."""
        self.write("plugins/.claude-plugin/marketplace.json", '{"version": "1"}')
        self.write(
            "plugins/dreadnought-standards/.claude-plugin/plugin.json",
            '{"version": "1"}',
        )
        self.write(
            "plugins/dreadnought-standards/skills/dreadnought-card-quality/SKILL.md",
            "# card quality\n",
        )
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg,
                 "--author", f"{author} <{author}@users.noreply.github.com>")

    def test_sync_pr_opened_by_the_bot_exits_0_without_a_test_commit(self):
        # The live shape: one commit, code-classified manifests, no RED test —
        # the failure that repeated nightly and cannot fix itself.
        self.git("checkout", "-q", "-b", "bot/standards-sync")
        self._standards_sync_commit("chore: sync dreadnought standards", BOT)
        p = self.run_check(head_ref="bot/standards-sync", author=BOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("exempt", p.stdout)
        self.assertIn("bot/standards-sync", p.stdout)

    def test_a_forged_sync_branch_and_author_do_not_exempt_a_stranger_s_pr(self):
        # The review finding, reproduced end-to-end against the real script:
        # anyone can name a branch `bot/standards-sync` and pass
        # `git commit --author "agent-bureau-bot[bot] <…>"`. The only thing
        # they cannot fake is who GitHub says opened the PR, so a made-up
        # feature with no test is checked like any other.
        self.git("checkout", "-q", "-b", "bot/standards-sync")
        self.add_commit("scripts/backdoor.py", "feat: totally not a backdoor",
                        author=BOT)
        p = self.run_check(head_ref="bot/standards-sync", author="mallory")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(check_tdd_commits.FAILURE_MESSAGE, p.stdout)
        self.assertNotIn("exempt", p.stdout)

    def test_sync_branch_with_a_foreign_commit_is_still_checked(self):
        # Every commit: one by somebody else and the gate is back, even when
        # the bot did open the PR.
        self.git("checkout", "-q", "-b", "bot/standards-sync")
        self._standards_sync_commit("chore: sync dreadnought standards", BOT)
        self.add_commit("scripts/widget.py", "feat: smuggled in", author="alice")
        p = self.run_check(head_ref="bot/standards-sync", author=BOT)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(check_tdd_commits.FAILURE_MESSAGE, p.stdout)

    def test_the_bot_on_another_branch_is_still_checked(self):
        # The same opener, an ordinary agent branch: the discipline holds.
        self.git("checkout", "-q", "-b", "agent/DRE-9-x")
        self.add_commit("scripts/widget.py", "feat(DRE-9): impl first",
                        author=BOT)
        p = self.run_check(head_ref="agent/DRE-9-x", author=BOT)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(check_tdd_commits.FAILURE_MESSAGE, p.stdout)

    def test_both_signals_can_arrive_from_githubs_own_run_context(self):
        # The fleet runs this check from `.bureau-pipeline/scripts/` through
        # each product repo's OWN workflow, which this PR cannot edit. GitHub
        # sets GITHUB_HEAD_REF and writes the event payload itself on every
        # pull_request run, so the exemption reaches agent-bureau — where the
        # nightly PR is opened — with nothing threaded there.
        self.git("checkout", "-q", "-b", "bot/standards-sync")
        self._standards_sync_commit("chore: sync dreadnought standards", BOT)
        p = self.run_check(github_head_ref="bot/standards-sync",
                           event_author=BOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("exempt", p.stdout)

    def test_the_event_payload_names_the_opener_not_the_committer(self):
        # Same run context, but GitHub says someone else opened the PR — the
        # payload is the attested answer and it overrides the commit line.
        self.git("checkout", "-q", "-b", "bot/standards-sync")
        self._standards_sync_commit("chore: sync dreadnought standards", BOT)
        p = self.run_check(github_head_ref="bot/standards-sync",
                           event_author="mallory")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_the_sync_branch_with_no_run_context_at_all_is_still_checked(self):
        # Fail-closed: with nothing set (the pre-push local run) the checker
        # knows neither the branch nor the opener, so it checks.
        self.git("checkout", "-q", "-b", "bot/standards-sync")
        self._standards_sync_commit("chore: sync dreadnought standards", BOT)
        p = self.run_check()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_the_branch_alone_does_not_exempt_without_an_opener(self):
        # The branch is threaded but nothing names the opener: refused.
        self.git("checkout", "-q", "-b", "bot/standards-sync")
        self._standards_sync_commit("chore: sync dreadnought standards", BOT)
        p = self.run_check(head_ref="bot/standards-sync")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)


# --- fixtures for the AST-equivalence classifier (DRE-2409) --------------
#
# One .py file in four versions: a baseline, a prose-only rewrite, a
# prose-plus-behaviour rewrite, and a behaviour-only rewrite.

_PY_BASE = '''"""Widget.

Old prose.
"""

VALUE = 1


def widget(x):
    """Return x plus the value."""
    return x + VALUE
'''

_PY_PROSE_ONLY = '''"""Widget.

Rewritten prose, several new paragraphs, a worked example, and a note about
why the module looks the way it does.
"""

VALUE = 1


def widget(x):
    """Return x plus the value.

    Now with an explanation nobody can test.
    """
    return x + VALUE
'''

_PY_PROSE_AND_CODE = '''"""Widget.

Rewritten prose.
"""

VALUE = 2


def widget(x):
    """Return x plus the value.

    Now with an explanation nobody can test.
    """
    return x + VALUE
'''

_PY_CODE_ONLY = '''"""Widget.

Old prose.
"""

VALUE = 1


def widget(x):
    """Return x plus the value."""
    return x - VALUE
'''


class DocstringOnlyPythonChangeTest(unittest.TestCase):
    """DRE-2409: this repo keeps its architecture narrative in module
    DOCSTRINGS, not only in .md files. Classifying by path alone meant a
    seven-line prose paragraph appended to `scripts/reconcile.py` was read as
    implementation and a RED test was demanded for it (live: bp #145) — and
    the only test you can write for a paragraph is a vacuous one, which the
    engineering standard separately bans.

    The classifier therefore compares the two versions' ABSTRACT SYNTAX TREES
    with every docstring stripped. Identical trees ⇒ nothing executable
    changed ⇒ documentation. This is ungameable: any real behaviour change
    moves the AST. Everything else stays fail-closed to `code`."""

    def test_prose_only_rewrite_is_documentation(self):
        self.assertTrue(
            check_tdd_commits.is_docs_only_python_change(_PY_BASE, _PY_PROSE_ONLY)
        )

    def test_prose_plus_behaviour_is_not_documentation(self):
        # The hole that would matter: a real edit smuggled in beside a
        # docstring rewrite must still demand a RED test.
        self.assertFalse(
            check_tdd_commits.is_docs_only_python_change(_PY_BASE, _PY_PROSE_AND_CODE)
        )

    def test_behaviour_only_change_is_not_documentation(self):
        self.assertFalse(
            check_tdd_commits.is_docs_only_python_change(_PY_BASE, _PY_CODE_ONLY)
        )

    def test_comment_only_change_is_documentation(self):
        # Deliberate: `#` comments never reach the AST, so a comment-only
        # edit compares equal and lands in `docs`. That is the intended
        # reading — a comment is prose with no executable effect, exactly
        # like a docstring, and demanding a RED test for one produces the
        # same vacuous test. (Linter and type-checker pragmas ride this same
        # path; they change tooling output, never runtime behaviour, and the
        # lint job is what judges them.)
        self.assertTrue(
            check_tdd_commits.is_docs_only_python_change(
                "VALUE = 1\n", "# why VALUE is 1\nVALUE = 1\n"
            )
        )

    def test_non_docstring_string_literal_change_is_not_documentation(self):
        # A string that is not in docstring position is data the code uses —
        # a message, a path, a SQL fragment. It stays `code`.
        self.assertFalse(
            check_tdd_commits.is_docs_only_python_change(
                'MSG = "old"\n', 'MSG = "new"\n'
            )
        )

    def test_statement_reordering_is_not_documentation(self):
        self.assertFalse(
            check_tdd_commits.is_docs_only_python_change(
                "a()\nb()\n", "b()\na()\n"
            )
        )

    def test_added_file_has_no_base_version_and_is_not_documentation(self):
        # A file the PR ADDS has no `before`. New source is new behaviour.
        self.assertFalse(
            check_tdd_commits.is_docs_only_python_change(None, _PY_BASE)
        )

    def test_deleted_file_has_no_head_version_and_is_not_documentation(self):
        self.assertFalse(
            check_tdd_commits.is_docs_only_python_change(_PY_BASE, None)
        )

    def test_both_versions_missing_is_not_documentation(self):
        self.assertFalse(check_tdd_commits.is_docs_only_python_change(None, None))

    def test_unparseable_base_fails_closed_to_code(self):
        self.assertFalse(
            check_tdd_commits.is_docs_only_python_change("def (\n", _PY_BASE)
        )

    def test_unparseable_head_fails_closed_to_code(self):
        # Also the shape that matters most: a syntax error must never be
        # waved through as "no AST difference we could find".
        self.assertFalse(
            check_tdd_commits.is_docs_only_python_change(_PY_BASE, "def (\n")
        )

    def test_both_unparseable_fails_closed_to_code(self):
        self.assertFalse(
            check_tdd_commits.is_docs_only_python_change("def (\n", "def (\n")
        )


class ClassifyPathWithContentTest(unittest.TestCase):
    """`classify_path` gains optional before/after source. Without it the
    path-only rules are unchanged — that default is what every other caller
    and every pre-DRE-2409 test relies on."""

    def test_python_prose_only_change_classifies_as_docs(self):
        self.assertEqual(
            check_tdd_commits.classify_path(
                "scripts/reconcile.py", _PY_BASE, _PY_PROSE_ONLY
            ),
            "docs",
        )

    def test_python_prose_plus_code_change_stays_code(self):
        self.assertEqual(
            check_tdd_commits.classify_path(
                "scripts/reconcile.py", _PY_BASE, _PY_PROSE_AND_CODE
            ),
            "code",
        )

    def test_no_content_supplied_still_means_code(self):
        # Regression guard on the fail-closed default: a caller that cannot
        # produce the two versions gets the old, strict answer.
        self.assertEqual(
            check_tdd_commits.classify_path("scripts/reconcile.py"), "code"
        )

    def test_test_tree_python_stays_test_even_when_prose_only(self):
        # Path rules are checked first: a docstring tweak under tests/ is
        # still a test commit, not a docs commit.
        self.assertEqual(
            check_tdd_commits.classify_path(
                "tests/test_widget.py", _PY_BASE, _PY_PROSE_ONLY
            ),
            "test",
        )

    def test_non_python_file_is_never_ast_compared(self):
        # Identical content on a non-.py path must not become `docs` — only
        # Python has an AST we can prove equivalence with.
        self.assertEqual(
            check_tdd_commits.classify_path(
                "scripts/deploy.sh", "echo hi\n", "echo hi\n"
            ),
            "code",
        )


class CommitCategoriesTest(unittest.TestCase):
    """The per-commit rollup the ordering rule consumes. Commits may carry a
    `sources` map {path: (before, after)}; commits without one behave exactly
    as they did before DRE-2409."""

    def _commit(self, paths, sources=None, subject="a commit"):
        rec = {"sha": "f" * 40, "subject": subject, "paths": list(paths)}
        if sources is not None:
            rec["sources"] = sources
        return rec

    def test_prose_only_python_commit_is_docs_and_needs_no_red_test(self):
        # bp #145 exactly: one commit, one .py file, docstring only.
        ok, reason = check_tdd_commits.check_commits([
            self._commit(
                ["scripts/reconcile.py"],
                {"scripts/reconcile.py": (_PY_BASE, _PY_PROSE_ONLY)},
                "docs(DRE-2409): note the restart in the sweep's own summary",
            ),
        ])
        self.assertTrue(ok, reason)

    def test_prose_plus_code_commit_still_demands_a_red_test(self):
        ok, reason = check_tdd_commits.check_commits([
            self._commit(
                ["scripts/reconcile.py"],
                {"scripts/reconcile.py": (_PY_BASE, _PY_PROSE_AND_CODE)},
                "feat: prose and behaviour together",
            ),
        ])
        self.assertFalse(ok)
        self.assertEqual(reason, check_tdd_commits.FAILURE_MESSAGE)

    def test_prose_only_python_beside_a_real_source_file_stays_code(self):
        # A PR touching a docstring AND a real source file is code.
        ok, reason = check_tdd_commits.check_commits([
            self._commit(
                ["scripts/reconcile.py", "scripts/widget.py"],
                {
                    "scripts/reconcile.py": (_PY_BASE, _PY_PROSE_ONLY),
                    "scripts/widget.py": (_PY_BASE, _PY_CODE_ONLY),
                },
                "feat: docstring here, behaviour there",
            ),
        ])
        self.assertFalse(ok)
        self.assertEqual(reason, check_tdd_commits.FAILURE_MESSAGE)

    def test_commit_without_sources_is_unchanged(self):
        ok, _ = check_tdd_commits.check_commits([
            self._commit(["scripts/widget.py"], None, "fix: impl first"),
        ])
        self.assertFalse(ok)


class AstDocsCliTest(GitRepoMixin, unittest.TestCase):
    """DRE-2409 end-to-end, over real commits in a real repo — the classifier
    is only worth anything if `pr_commits` actually hands it both versions."""

    def seed_python(self):
        self.write("scripts/widget.py", _PY_BASE)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "feat: seed widget")

    def test_docstring_only_commit_exits_0(self):
        # The live shape of bp #145: one commit, one .py file, +7/-0 of prose.
        self.seed_python()
        self.git("checkout", "-q", "-b", "agent/DRE-2409-prose")
        self.write("scripts/widget.py", _PY_PROSE_ONLY)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "docs(DRE-2409): expand the narrative")
        p = self.run_check()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("docs", p.stdout)

    def test_docstring_plus_code_commit_exits_1(self):
        self.seed_python()
        self.git("checkout", "-q", "-b", "agent/DRE-2409-mixed")
        self.write("scripts/widget.py", _PY_PROSE_AND_CODE)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "docs: prose (and a quiet behaviour change)")
        p = self.run_check()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_added_python_file_exits_1(self):
        # No base version to compare against — new source, RED test required.
        self.git("checkout", "-q", "-b", "agent/DRE-2409-added")
        self.write("scripts/brand_new.py", _PY_BASE)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "feat: brand new module")
        p = self.run_check()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_deleted_python_file_exits_1(self):
        self.seed_python()
        self.git("checkout", "-q", "-b", "agent/DRE-2409-deleted")
        self.git("rm", "-q", "scripts/widget.py")
        self.git("commit", "-q", "-m", "chore: drop widget")
        p = self.run_check()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_unparseable_python_exits_1_not_2(self):
        # A syntax error must land as a discipline violation, never be
        # waved through as documentation.
        self.seed_python()
        self.git("checkout", "-q", "-b", "agent/DRE-2409-broken")
        self.write("scripts/widget.py", "def (\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "wip: broken")
        p = self.run_check()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_docstring_change_beside_a_real_source_change_exits_1(self):
        self.seed_python()
        self.write("scripts/other.py", _PY_BASE)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "feat: seed other")
        self.git("checkout", "-q", "-b", "agent/DRE-2409-two-files")
        self.write("scripts/widget.py", _PY_PROSE_ONLY)
        self.write("scripts/other.py", _PY_CODE_ONLY)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "docs: prose here, behaviour there")
        p = self.run_check()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_prose_commit_before_a_red_test_still_passes(self):
        # Ordering is unaffected: a docs-classified .py commit is not the
        # "first code commit", so a later test → fix pair still reads clean.
        self.seed_python()
        self.git("checkout", "-q", "-b", "agent/DRE-2409-order")
        self.write("scripts/widget.py", _PY_PROSE_ONLY)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "docs: narrative")
        self.add_commit("tests/test_widget.py", "test(DRE-2409): RED")
        self.add_commit("scripts/thing.py", "fix(DRE-2409): green")
        p = self.run_check()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


# --- fixtures for the generated-region classifier (DRE-3896) -------------
#
# One .py file carrying a GENERATED region, in the shape
# scripts/model_fallback.py carries it: prose, a marker line, a literal that
# `scripts/sync_model_config.py` renders from config/models.yaml, a closing
# marker, and ordinary code below. The variants are the five answers the rule
# owes: regenerated inside the region, regenerated plus an edit outside it, a
# marker line itself edited, a region whose END is missing, and source that
# will not parse.
#
# The PATH matters as much as the content: the exemption only exists for files
# a generator's `--check` proves, so every fixture below is exercised against
# MIRROR — one of those files — and the unproven-path case is tested on its own.

MIRROR = "scripts/model_fallback.py"          # proved by sync_model_config.py
CARD_VALIDATOR = "scripts/validate_card.py"   # proved by sync_fallback_map.py
UNPROVEN = "scripts/evil_auth.py"             # no generator; never exempt

_PY_GENERATED = '''"""Widget with a generated mirror.

The literal below mirrors config/models.yaml; `sync_model_config.py --check`
fails red if the two drift.
"""

VALUE = 1

# GENERATED REGION: do not hand-edit. Edit config/models.yaml, then run
# `python3 scripts/sync_model_config.py` to regenerate it.
# --- BEGIN generated model config (from config/models.yaml) ---
_FALLBACK = {
    "default_ladder": "workhorse",
    "ladders": {"workhorse": ["claude-opus-5"]},
}
# --- END generated model config ---


def widget(x):
    """Return x plus the value."""
    return x + VALUE
'''

# The adoption PR's shape: config/models.yaml gained a rung, the mirror was
# regenerated, nothing else moved.
_PY_GENERATED_REGENERATED = _PY_GENERATED.replace(
    '"ladders": {"workhorse": ["claude-opus-5"]},',
    '"ladders": {"workhorse": ["claude-opus-5", "claude-sonnet-4-6"]},',
)

# One line inside the region and one outside it — the hole that would matter.
_PY_GENERATED_AND_OUTSIDE = _PY_GENERATED_REGENERATED.replace(
    "VALUE = 1", "VALUE = 2"
)

# The marker itself moved: the region is no longer the one the generator wrote.
_PY_GENERATED_MARKER_EDITED = _PY_GENERATED_REGENERATED.replace(
    "# --- BEGIN generated model config (from config/models.yaml) ---",
    "# --- BEGIN generated model config (from config/other.yaml) ---",
)

# No closing marker: everything below BEGIN would otherwise be swallowed.
_PY_GENERATED_NO_END = _PY_GENERATED.replace(
    "# --- END generated model config ---\n", ""
)
_PY_GENERATED_NO_END_REGENERATED = _PY_GENERATED_REGENERATED.replace(
    "# --- END generated model config ---\n", ""
)


class GeneratedRegionChangeTest(unittest.TestCase):
    """DRE-3896: a change confined to a GENERATED region is generated, not
    authored. The adoption PR of DRE-3892 edits config/models.yaml and then
    runs `scripts/sync_model_config.py`, which rewrites the
    `_FALLBACK_MODEL_CONFIG` literal in `scripts/model_fallback.py`. That
    literal moves the AST, so the docstring rule cannot exempt it and there is
    nobody to write the RED test — a failure DRE-2694 makes unfixable by
    adding a commit.

    The exemption is safe because the region's content is proved elsewhere:
    `sync_model_config.py --check` (run by the unit suite, so red in CI) fails
    when a generated region does not match its canonical render, so code
    cannot hide there. Everything outside that proof stays fail-closed."""

    def test_regenerated_region_is_a_generated_change(self):
        self.assertTrue(
            check_tdd_commits.is_generated_region_change(
                MIRROR, _PY_GENERATED, _PY_GENERATED_REGENERATED
            )
        )

    def test_a_line_outside_the_region_is_not(self):
        self.assertFalse(
            check_tdd_commits.is_generated_region_change(
                MIRROR, _PY_GENERATED, _PY_GENERATED_AND_OUTSIDE
            )
        )

    def test_editing_a_marker_line_is_not(self):
        # The markers delimit the proof. Move one and the "generated" claim is
        # about a region the generator never wrote.
        self.assertFalse(
            check_tdd_commits.is_generated_region_change(
                MIRROR, _PY_GENERATED, _PY_GENERATED_MARKER_EDITED
            )
        )

    def test_a_region_with_no_closing_marker_is_not(self):
        self.assertFalse(
            check_tdd_commits.is_generated_region_change(
                MIRROR, _PY_GENERATED, _PY_GENERATED_NO_END
            )
        )

    def test_both_sides_missing_the_closing_marker_is_not(self):
        # An unterminated region must not swallow the rest of the file and
        # exempt every line below the BEGIN marker.
        self.assertFalse(
            check_tdd_commits.is_generated_region_change(
                MIRROR, _PY_GENERATED_NO_END, _PY_GENERATED_NO_END_REGENERATED
            )
        )

    def test_a_file_with_no_region_is_not(self):
        self.assertFalse(
            check_tdd_commits.is_generated_region_change(MIRROR, _PY_BASE, _PY_CODE_ONLY)
        )

    def test_unparseable_before_fails_closed_to_code(self):
        self.assertFalse(
            check_tdd_commits.is_generated_region_change(
                MIRROR, "def (\n", _PY_GENERATED_REGENERATED
            )
        )

    def test_unparseable_after_fails_closed_to_code(self):
        # A syntax error inside the region is still a syntax error: source
        # that will not parse is never waved through as generated.
        self.assertFalse(
            check_tdd_commits.is_generated_region_change(
                MIRROR, _PY_GENERATED,
                _PY_GENERATED.replace("_FALLBACK = {", "def ("),
            )
        )

    def test_added_file_has_no_base_version_and_is_not_generated(self):
        self.assertFalse(
            check_tdd_commits.is_generated_region_change(MIRROR, None, _PY_GENERATED)
        )

    def test_deleted_file_has_no_head_version_and_is_not_generated(self):
        self.assertFalse(
            check_tdd_commits.is_generated_region_change(MIRROR, _PY_GENERATED, None)
        )

    def test_wrapping_code_in_new_markers_is_not(self):
        # The adversarial shape: a commit that INTRODUCES markers around code
        # it wants exempted. The marker lines are themselves outside the
        # region, so adding them is a change outside it — code.
        wrapped = _PY_BASE.replace(
            "VALUE = 1",
            "# --- BEGIN generated ---\nVALUE = 2\n# --- END generated ---",
        )
        self.assertFalse(
            check_tdd_commits.is_generated_region_change(MIRROR, _PY_BASE, wrapped)
        )

    def test_deleting_the_region_is_not(self):
        # Dropping the mirror is a decision, not a render — the markers that
        # carried the proof are gone, so nothing proves what replaced them.
        without = "\n".join(
            line for line in _PY_GENERATED.splitlines()
            if "generated model config" not in line
        ) + "\n"
        self.assertFalse(
            check_tdd_commits.is_generated_region_change(MIRROR, _PY_GENERATED, without)
        )

    def test_an_unproven_path_is_never_a_generated_change(self):
        # The hole this exemption must not open: markers are comment lines,
        # so any file can carry them. If marker-shape alone were enough, one
        # ordinary reviewed commit adding them to an arbitrary file would buy
        # that file a permanent untested edit channel — every later edit
        # strictly inside the markers skipping the RED-test requirement, with
        # no generator and no `--check` anywhere near it.
        before = '''"""Totally unrelated helper."""

# --- BEGIN generated evil (from nowhere) ---
def is_authorized(user):
    return False
# --- END generated evil (from nowhere) ---
'''
        after = before.replace("return False", "return True")
        # Confined to the region by shape — and still not exempt, because
        # nothing proves what the region says.
        self.assertFalse(
            check_tdd_commits.is_generated_region_change(UNPROVEN, before, after)
        )
        self.assertEqual(
            check_tdd_commits.classify_path(UNPROVEN, before, after), "code"
        )

    def test_a_neighbour_of_a_proved_file_is_not_covered(self):
        # The proof is per-file. Sitting in the same directory as a mirror,
        # or being named like one, proves nothing.
        for path in ("scripts/model_fallback_extra.py", "scripts/model_fallbac.py",
                     "vendor/scripts/model_fallback.py"):
            with self.subTest(path=path):
                self.assertFalse(check_tdd_commits.has_generated_region_proof(path))
                self.assertEqual(
                    check_tdd_commits.classify_path(
                        path, _PY_GENERATED, _PY_GENERATED_REGENERATED
                    ),
                    "code",
                )

    def test_every_proved_path_is_a_real_file_with_a_real_generator(self):
        # A proof that does not exist is not a proof. Each entry must name a
        # live file that actually carries generated markers, and a command
        # whose script is in this repo — otherwise the allowlist drifts into
        # exempting files nothing checks.
        proofs = check_tdd_commits._GENERATED_REGION_PROOFS
        self.assertTrue(proofs, "the allowlist must not be empty")
        for path, command in proofs.items():
            with self.subTest(path=path):
                target = ROOT / path
                self.assertTrue(target.is_file(), f"{path} is not in the repo")
                self.assertIn("BEGIN generated", target.read_text())
                self.assertIn("--check", command)
                generator = command.split()[1]
                self.assertTrue(
                    (ROOT / generator).is_file(),
                    f"{path}'s proof names a generator that is not here: {generator}",
                )

    def test_the_card_validator_mirror_is_covered(self):
        # The repo's other proved mirror: sync_fallback_map.py renders
        # _FALLBACK_REPO_MAP into validate_card.py from config/repo-map.json.
        source = (ROOT / CARD_VALIDATOR).read_text()
        self.assertIn("BEGIN generated repo mirror", source)
        regenerated = source.replace(
            '"bureau-pipeline": "dreadnought-foundry/bureau-pipeline",',
            '"bureau-pipeline": "dreadnought-foundry/bureau-pipeline",\n'
            '    "newco": "dreadnought-foundry/newco",',
        )
        self.assertNotEqual(source, regenerated, "fixture no longer matches")
        self.assertTrue(
            check_tdd_commits.is_generated_region_change(
                CARD_VALIDATOR, source, regenerated
            )
        )
        self.assertEqual(
            check_tdd_commits.classify_path(CARD_VALIDATOR, source, regenerated),
            "docs",
        )

    def test_the_real_model_fallback_mirror_is_covered(self):
        # Not a fixture: the live file the adoption PR regenerates. If its
        # markers ever change shape, this exemption silently stops applying
        # to the one change it was written for.
        source = (ROOT / "scripts" / "model_fallback.py").read_text()
        self.assertIn("BEGIN generated", source)
        regenerated = source.replace(
            '"default_ladder": "workhorse",',
            '"default_ladder": "advisory",',
        )
        self.assertNotEqual(source, regenerated, "fixture no longer matches")
        self.assertTrue(
            check_tdd_commits.is_generated_region_change(MIRROR, source, regenerated)
        )
        self.assertEqual(
            check_tdd_commits.classify_path(
                "scripts/model_fallback.py", source, regenerated
            ),
            "docs",
        )


class ClassifyPathGeneratedRegionTest(unittest.TestCase):
    """The classifier reads a generated-region change as `docs`, beside the
    docstring-only exemption, and everything else stays `code`."""

    def test_generated_region_change_classifies_as_docs(self):
        self.assertEqual(
            check_tdd_commits.classify_path(
                "scripts/model_fallback.py",
                _PY_GENERATED,
                _PY_GENERATED_REGENERATED,
            ),
            "docs",
        )

    def test_region_plus_an_outside_line_stays_code(self):
        self.assertEqual(
            check_tdd_commits.classify_path(
                "scripts/model_fallback.py",
                _PY_GENERATED,
                _PY_GENERATED_AND_OUTSIDE,
            ),
            "code",
        )

    def test_marker_edit_stays_code(self):
        self.assertEqual(
            check_tdd_commits.classify_path(
                "scripts/model_fallback.py",
                _PY_GENERATED,
                _PY_GENERATED_MARKER_EDITED,
            ),
            "code",
        )

    def test_the_docstring_exemption_is_unaffected(self):
        # Both rules live on the same branch of classify_path; neither may
        # shadow the other.
        self.assertEqual(
            check_tdd_commits.classify_path(
                "scripts/reconcile.py", _PY_BASE, _PY_PROSE_ONLY
            ),
            "docs",
        )

    def test_a_path_with_no_proving_generator_stays_code(self):
        # Same content, same markers, same confinement as the exempt case
        # above — only the path differs, and the path is what carries the
        # proof. This is the whole of the scoping rule in one assertion.
        self.assertEqual(
            check_tdd_commits.classify_path(
                UNPROVEN, _PY_GENERATED, _PY_GENERATED_REGENERATED
            ),
            "code",
        )
        self.assertEqual(
            check_tdd_commits.classify_path(
                MIRROR, _PY_GENERATED, _PY_GENERATED_REGENERATED
            ),
            "docs",
        )

    def test_a_non_python_generated_region_is_never_exempt(self):
        # agents.yaml carries generated regions too, and it is already `ops`.
        # No other extension gets this reading — only .py is AST-checked.
        self.assertEqual(
            check_tdd_commits.classify_path(
                "scripts/deploy.sh",
                "# BEGIN generated\nX=1\n# END generated\n",
                "# BEGIN generated\nX=2\n# END generated\n",
            ),
            "code",
        )


class GeneratedRegionCliTest(GitRepoMixin, unittest.TestCase):
    """DRE-3896 end-to-end: the adoption branch of DRE-3892, commit for
    commit — config/models.yaml edited, the two mirrors regenerated, no test
    anywhere and nobody to write one."""

    def seed_mirror(self):
        self.write("scripts/model_fallback.py", _PY_GENERATED)
        self.write("config/models.yaml", "ladders:\n  workhorse:\n    - claude-opus-5\n")
        self.write("agents.yaml", "agents:\n  - name: engineer\n    model: claude-opus-5\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: seed the model config and its mirrors")

    def test_adoption_branch_is_exempt(self):
        self.seed_mirror()
        self.git("checkout", "-q", "-b", "agent/DRE-3892-model-adoption")
        self.write("config/models.yaml",
                   "ladders:\n  workhorse:\n    - claude-opus-5\n    - claude-sonnet-4-6\n")
        self.write("agents.yaml",
                   "agents:\n  - name: engineer\n    model: claude-sonnet-4-6\n")
        self.write("scripts/model_fallback.py", _PY_GENERATED_REGENERATED)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore(DRE-3892): adopt claude-sonnet-4-6")
        p = self.run_check()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        # The exact reason the adoption workflow's contract names.
        self.assertIn(
            "exempt: no non-test code changed (docs/ops/tests only)", p.stdout
        )
        self.assertIn("[docs,ops]", p.stdout)

    def test_a_line_outside_the_region_exits_1(self):
        self.seed_mirror()
        self.git("checkout", "-q", "-b", "agent/DRE-3896-outside")
        self.write("scripts/model_fallback.py", _PY_GENERATED_AND_OUTSIDE)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: regenerate (and one quiet edit)")
        p = self.run_check()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(check_tdd_commits.FAILURE_MESSAGE, p.stdout)

    def test_a_marker_edit_exits_1(self):
        self.seed_mirror()
        self.git("checkout", "-q", "-b", "agent/DRE-3896-marker")
        self.write("scripts/model_fallback.py", _PY_GENERATED_MARKER_EDITED)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: move the marker")
        p = self.run_check()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_the_two_commit_marker_squat_exits_1(self):
        # The exploitable shape, end to end: commit 1 (on the base branch, an
        # ordinary reviewable `code` commit) adds a file carrying the marker
        # comments; commit 2 — the only commit the gate sees — hand-edits the
        # behaviour strictly INSIDE those markers, no test, no generator.
        # Confinement alone must not clear the gate.
        squatted = '''"""An ordinary helper that happens to carry markers."""

# --- BEGIN generated evil (from nowhere) ---
def is_authorized(user):
    return False
# --- END generated evil (from nowhere) ---
'''
        self.write("scripts/evil_auth.py", squatted)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: add evil_auth with its markers")
        self.git("checkout", "-q", "-b", "agent/DRE-3896-squat")
        self.write(
            "scripts/evil_auth.py",
            squatted.replace("return False", "return True"),
        )
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: regenerate evil_auth (hand edited)")
        p = self.run_check()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(check_tdd_commits.FAILURE_MESSAGE, p.stdout)

    def test_a_region_without_its_end_marker_exits_1(self):
        self.seed_mirror()
        self.git("checkout", "-q", "-b", "agent/DRE-3896-unterminated")
        self.write("scripts/model_fallback.py", _PY_GENERATED_NO_END_REGENERATED)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: regenerate over a broken region")
        p = self.run_check()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)


class GeneratedRegionStandardTest(unittest.TestCase):
    """The standard states the rule as it is ENFORCED, so a new exemption that
    is not in that sentence is a document the code contradicts — which
    standards/engineering.md itself says must be fixed in the same PR."""

    def test_the_enforced_rule_sentence_names_the_exemption(self):
        text = (ROOT / "standards" / "engineering.md").read_text()
        marker = "The rule as it is ENFORCED, in one sentence."
        self.assertIn(marker, text, "the enforced-rule bullet is gone")
        bullet = text.split(marker, 1)[1].split("\n- **", 1)[0]
        self.assertIn("generated region", bullet)
        # …and the proof that makes the exemption safe, named.
        self.assertIn("sync_model_config.py --check", bullet)
        # …and the scope of that proof. The standard claiming an exemption
        # wider than the code grants is the document the code contradicts.
        self.assertIn("_GENERATED_REGION_PROOFS", bullet)


class WorkflowWiringTest(unittest.TestCase):
    """tests.yml must actually run the checker on PRs — the script without
    the job enforces nothing."""

    def setUp(self):
        doc = yaml.safe_load(WORKFLOW.read_text())
        jobs = [
            j for j in doc["jobs"].values()
            if any("check_tdd_commits.py" in (s.get("run") or "")
                   for s in j.get("steps", []))
        ]
        self.assertEqual(
            len(jobs), 1,
            "expected exactly one Pipeline Tests job invoking check_tdd_commits.py",
        )
        self.job = jobs[0]

    def test_job_runs_only_on_pull_requests(self):
        # The workflow also fires on push to main, where there is no PR
        # commit list to judge — the job must gate itself out there.
        self.assertIn("pull_request", self.job.get("if", ""))

    def test_checkout_fetches_full_history(self):
        # The check walks the PR's commit list; a depth-1 checkout can't.
        checkouts = [
            s for s in self.job["steps"]
            if "actions/checkout" in (s.get("uses") or "")
        ]
        self.assertTrue(checkouts, "job has no checkout step")
        self.assertEqual(checkouts[0].get("with", {}).get("fetch-depth"), 0)

    def test_base_and_head_come_from_the_pr_event_via_env(self):
        # Refs are passed through env, not interpolated into the shell line —
        # a crafted branch name must never become shell input.
        step = next(
            s for s in self.job["steps"]
            if "check_tdd_commits.py" in (s.get("run") or "")
        )
        env = step.get("env", {})
        self.assertIn(
            "github.event.pull_request.base.ref", str(env.get("BASE_REF", ""))
        )
        self.assertIn(
            "github.event.pull_request.head.sha", str(env.get("HEAD_SHA", ""))
        )
        self.assertIn('"origin/$BASE_REF"', step["run"])
        self.assertIn('"$HEAD_SHA"', step["run"])

    def test_pr_author_reaches_the_check_for_the_dependabot_exemption(self):
        # DRE-2049: the script decides the dependabot exemption off the PR's
        # author — GitHub-attested identity, not a spoofable branch name. It
        # rides env like the refs, never shell interpolation.
        step = next(
            s for s in self.job["steps"]
            if "check_tdd_commits.py" in (s.get("run") or "")
        )
        self.assertIn(
            "github.event.pull_request.user.login",
            str(step.get("env", {}).get("PR_AUTHOR", "")),
            "the job must hand the PR author to the check via PR_AUTHOR",
        )

    def test_head_ref_reaches_the_check_for_the_standards_sync_exemption(self):
        # DRE-3885: the branch is half the standards-sync exemption (the commit
        # authors are the other half, read from git). Through env like every
        # other ref here — a crafted branch name must not become shell input.
        step = next(
            s for s in self.job["steps"]
            if "check_tdd_commits.py" in (s.get("run") or "")
        )
        self.assertIn(
            "github.event.pull_request.head.ref",
            str(step.get("env", {}).get("HEAD_REF", "")),
            "the job must hand the PR's head branch to the check via HEAD_REF",
        )
        self.assertNotIn(
            "${{", step["run"],
            "no event field may be interpolated into the shell line",
        )


if __name__ == "__main__":
    unittest.main()
