"""The merge gate's fork-refresh decision (DRE-5066).

Origin (live, 2026-09-25, DRE-4912): two agent-bureau pull requests each added
a `0062_*` alembic migration on `0061_poll_sample`. Each passed the repo's
migration-head gate, because each was checked against `main` as it stood when
its own CI ran. Nothing re-checked when `main` moved, the merge gate merged the
second at 13:06 PT, and `main` forked.

`scripts/order_sensitive_refresh.py` is the pure decision behind the remedy, in
the shape `stale_merge_ref.py` (DRE-3138) already carries: pure functions over
GitHub payloads, no I/O, a CLI for the workflow. It answers one question — has
`main` added a file under a path the repo declares order-sensitive since this
branch's merge base, while the pull request adds one under the same path? — and
says `refresh` only then, so nothing else pays a CI run. Branch currency on its
own is still not a gate (DRE-2416).

Every rule's test is written so removing that rule turns it red.
"""

import contextlib
import io
import json
import posixpath
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import order_sensitive_refresh as osr  # noqa: E402

MODULE = ROOT / "scripts" / "order_sensitive_refresh.py"

BASE_SHA = "b" * 40    # the merge base
TIP_SHA = "c" * 40     # the tip of main now
OLD_TIP = "d" * 40     # a tip of main the branch was refreshed onto earlier
VERSIONS = "console/backend/alembic/versions/"
OURS = VERSIONS + "0062_workflow_run_actor.py"
THEIRS = VERSIONS + "0062_runner_ledger.py"


def compare(files=None, behind_by=3, tip=TIP_SHA, base=BASE_SHA):
    """The merge gate's `/tmp/compare.json`: GET compare/{base}...{head_sha}.
    `files[]` is the pull request's own contribution."""
    payload = {
        "status": "diverged",
        "ahead_by": 2,
        "merge_base_commit": {"sha": base},
        "base_commit": {"sha": tip},
        "files": files if files is not None else [added(OURS)],
    }
    if behind_by is not None:
        payload["behind_by"] = behind_by
    return payload


def advance(files=None):
    """GET compare/{merge_base_sha}...{base} — what `main` gained since the
    merge base."""
    return {"files": files if files is not None else [added(THEIRS)]}


def added(name):
    return {"filename": name, "status": "added"}


def declaration(*paths):
    return {"$schema_note": "prefixes whose files must land in order",
            "order_sensitive_paths": list(paths or (VERSIONS,))}


class Untouchable(dict):
    """A payload decide() must not consult. Any read fails the test."""

    def _touched(self, *_a, **_k):
        raise AssertionError("the base-advance payload was read")

    get = __getitem__ = __contains__ = __iter__ = __len__ = keys = items = \
        values = _touched


def decide(**overrides):
    kwargs = {
        "compare": compare(),
        "base_advance": advance(),
        "declaration": declaration(),
        "receipts": None,
    }
    kwargs.update(overrides)
    return osr.decide(**kwargs)


class ContractTest(unittest.TestCase):
    def test_the_three_decisions_are_the_agreed_words(self):
        self.assertEqual(("refresh", "proceed", "wait"),
                         (osr.REFRESH, osr.PROCEED, osr.WAIT))

    def test_the_declaration_path_is_the_agreed_file(self):
        self.assertEqual(".github/bureau/merge-recheck.json",
                         osr.DECLARATION_PATH)

    def test_the_receipt_marker_is_the_agreed_wording(self):
        self.assertEqual(f"Merge gate: refreshed onto {TIP_SHA}",
                         osr.receipt_marker(TIP_SHA))

    def test_the_module_docstring_records_the_incident_and_the_line(self):
        doc = osr.__doc__ or ""
        self.assertIn("DRE-4912", doc)
        self.assertIn("2026-09-25", doc)
        self.assertIn("DRE-2416", doc)
        self.assertIn("currency is still not a gate", doc)


class TheForkTest(unittest.TestCase):
    """2026-09-25, reproduced: both sides added a 0062 migration."""

    def test_the_incident_shape_is_a_refresh_naming_the_prefix(self):
        got = decide()
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertEqual(VERSIONS, got.prefix)
        self.assertIn(OURS, got.reason)
        self.assertIn(THEIRS, got.reason)

    def test_a_renumbered_migration_arrives_as_a_rename_and_counts(self):
        rename = {"filename": OURS, "previous_filename": VERSIONS + "0061_x.py",
                  "status": "renamed"}
        self.assertEqual(osr.REFRESH, decide(compare=compare([rename])).decision)
        self.assertEqual(
            osr.REFRESH,
            decide(base_advance=advance([dict(rename, filename=THEIRS)])).decision)

    def test_the_first_declared_prefix_that_matches_is_named(self):
        other = "infra/order/"
        got = decide(
            compare=compare([added(OURS), added(other + "a.txt")]),
            base_advance=advance([added(THEIRS), added(other + "b.txt")]),
            declaration=declaration(other, VERSIONS),
        )
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertEqual(other, got.prefix)

    def test_a_prefix_is_matched_with_startswith(self):
        # `…/versions/` must not match a sibling directory that merely shares
        # the stem.
        got = decide(base_advance=advance(
            [added("console/backend/alembic/versions_old/0062_x.py")]))
        self.assertEqual(osr.PROCEED, got.decision)


class NoForkTest(unittest.TestCase):
    def test_main_advanced_with_nothing_under_the_prefix_proceeds(self):
        got = decide(base_advance=advance([added("console/web/src/app.tsx")]))
        self.assertEqual(osr.PROCEED, got.decision)
        self.assertEqual("", got.prefix)

    def test_the_pr_adds_nothing_under_the_prefix_proceeds(self):
        got = decide(compare=compare([added("console/web/src/app.tsx")]))
        self.assertEqual(osr.PROCEED, got.decision)

    def test_a_modified_file_does_not_count_on_the_prs_side(self):
        got = decide(compare=compare(
            [{"filename": OURS, "status": "modified"}]))
        self.assertEqual(osr.PROCEED, got.decision)

    def test_a_modified_or_removed_file_does_not_count_on_mains_side(self):
        for status in ("modified", "removed", "changed", "copied"):
            with self.subTest(status=status):
                got = decide(base_advance=advance(
                    [{"filename": THEIRS, "status": status}]))
                self.assertEqual(osr.PROCEED, got.decision)

    def test_the_two_sides_must_meet_under_the_same_prefix(self):
        other = "infra/order/"
        got = decide(
            compare=compare([added(OURS)]),
            base_advance=advance([added(other + "b.txt")]),
            declaration=declaration(VERSIONS, other),
        )
        self.assertEqual(osr.PROCEED, got.decision)


class CurrencyIsNotAGateTest(unittest.TestCase):
    """Rule 2 — DRE-2416 stands: a branch that is current has nothing a
    refresh could change, and the base-advance payload is not consulted."""

    def test_behind_by_zero_proceeds_without_reading_the_base_advance(self):
        got = decide(compare=compare(behind_by=0), base_advance=Untouchable())
        self.assertEqual(osr.PROCEED, got.decision)

    def test_behind_by_missing_proceeds_without_reading_the_base_advance(self):
        got = decide(compare=compare(behind_by=None),
                     base_advance=Untouchable())
        self.assertEqual(osr.PROCEED, got.decision)

    def test_the_merge_gates_blip_payload_proceeds(self):
        # merge-gate.yml writes `{}` to /tmp/compare.json when the read fails.
        got = decide(compare={}, base_advance=Untouchable())
        self.assertEqual(osr.PROCEED, got.decision)


class DeclarationTest(unittest.TestCase):
    """Rule 1 — no file is an opt-out; a broken file fails closed."""

    def test_no_declaration_file_proceeds_on_the_fork_itself(self):
        got = decide(declaration=osr.NO_DECLARATION)
        self.assertEqual(osr.PROCEED, got.decision)

    def test_an_unparseable_declaration_waits_naming_the_file(self):
        got = decide(declaration=osr.UNREADABLE)
        self.assertEqual(osr.WAIT, got.decision)
        self.assertIn(osr.DECLARATION_PATH, got.reason)

    def test_the_named_file_is_the_one_the_caller_read(self):
        got = decide(declaration=osr.UNREADABLE,
                     declaration_path="/tmp/checkout/merge-recheck.json")
        self.assertIn("/tmp/checkout/merge-recheck.json", got.reason)

    def test_malformed_shapes_wait_naming_the_file(self):
        shapes = {
            "null": None,
            "a list": [VERSIONS],
            "no key": {"paths": [VERSIONS]},
            "a string": {"order_sensitive_paths": VERSIONS},
            "a non-string entry": {"order_sensitive_paths": [VERSIONS, 7]},
            "no trailing slash": {"order_sensitive_paths": [VERSIONS.rstrip("/")]},
            "an empty prefix": {"order_sensitive_paths": [""]},
            "a newline": {"order_sensitive_paths": ["a\nb/"]},
        }
        for label, shape in shapes.items():
            with self.subTest(label):
                got = decide(declaration=shape)
                self.assertEqual(osr.WAIT, got.decision)
                self.assertIn(osr.DECLARATION_PATH, got.reason)

    def test_a_malformed_declaration_waits_even_when_current(self):
        # Rule 1 comes before rule 2.
        got = decide(declaration=osr.UNREADABLE,
                     compare=compare(behind_by=0))
        self.assertEqual(osr.WAIT, got.decision)

    def test_an_empty_list_declares_nothing_and_proceeds(self):
        got = decide(declaration={"order_sensitive_paths": []})
        self.assertEqual(osr.PROCEED, got.decision)


class BaseAdvanceBlipTest(unittest.TestCase):
    """Rule 3 — a blip is not "safe to merge"."""

    def test_an_unreadable_base_advance_while_behind_waits(self):
        for label, payload in {
            "empty object": {},
            "unreadable": osr.UNREADABLE,
            "files not a list": {"files": "x"},
            "a list": [added(THEIRS)],
            "null": None,
        }.items():
            with self.subTest(label):
                got = decide(base_advance=payload)
                self.assertEqual(osr.WAIT, got.decision)

    def test_a_readable_base_advance_with_no_files_proceeds(self):
        got = decide(base_advance={"files": []})
        self.assertEqual(osr.PROCEED, got.decision)


class ReceiptTest(unittest.TestCase):
    """Rule 5 — at most one refresh per `main` commit."""

    def test_a_receipt_for_the_current_tip_waits(self):
        got = decide(receipts=[f"✅ {osr.receipt_marker(TIP_SHA)}\n\nwhy"])
        self.assertEqual(osr.WAIT, got.decision)
        self.assertEqual("", got.prefix)

    def test_a_receipt_for_an_older_tip_is_ignored(self):
        got = decide(receipts=[osr.receipt_marker(OLD_TIP)])
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertEqual(VERSIONS, got.prefix)

    def test_a_receipt_for_the_current_tip_never_proceeds_while_behind(self):
        # The pull request no longer adds under the prefix, but a refresh was
        # already requested at this tip and has not landed: wait for it.
        got = decide(compare=compare([added("README.md")]),
                     receipts=[osr.receipt_marker(TIP_SHA)])
        self.assertEqual(osr.WAIT, got.decision)

    def test_a_refresh_that_landed_proceeds(self):
        # After update-branch the head carries the tip: behind_by is 0, and
        # the receipt must not hold the pull request until `main` moves again.
        got = decide(compare=compare(behind_by=0),
                     receipts=[osr.receipt_marker(TIP_SHA)])
        self.assertEqual(osr.PROCEED, got.decision)

    def test_comment_objects_are_read_as_well_as_bodies(self):
        got = decide(receipts=[{"body": osr.receipt_marker(TIP_SHA)}])
        self.assertEqual(osr.WAIT, got.decision)

    def test_unreadable_receipts_wait(self):
        got = decide(receipts=osr.UNREADABLE)
        self.assertEqual(osr.WAIT, got.decision)

    def test_a_receipt_with_no_readable_tip_waits(self):
        got = decide(compare=compare(tip=""),
                     receipts=[osr.receipt_marker(OLD_TIP)])
        self.assertEqual(osr.WAIT, got.decision)

    def test_no_receipts_given_is_no_receipt(self):
        self.assertEqual(osr.REFRESH, decide(receipts=None).decision)
        self.assertEqual(osr.REFRESH, decide(receipts=[]).decision)


class ReasonTest(unittest.TestCase):
    def test_every_reason_is_one_line(self):
        hostile = VERSIONS + "0062_a\nprefix=/evil/.py"
        for got in (
            decide(),
            decide(compare=compare([added(hostile)])),
            decide(base_advance=advance([added(hostile)])),
            decide(declaration=osr.UNREADABLE,
                   declaration_path="x\ndecision=proceed"),
            decide(base_advance={}),
            decide(receipts=[osr.receipt_marker(TIP_SHA)]),
            decide(compare=compare(behind_by=0)),
        ):
            with self.subTest(got.decision):
                self.assertNotIn("\n", got.reason)
                self.assertNotIn("\r", got.reason)
                self.assertTrue(got.reason)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def _file(self, name, payload=None, raw=None):
        path = self.tmp / name
        path.write_text(raw if raw is not None else json.dumps(payload))
        return str(path)

    def _run(self, *, compare_file=None, advance_file=None, decl_file=None,
             extra=()):
        argv = [
            "decide",
            "--compare-file", compare_file or self._file("compare.json",
                                                         compare()),
            "--base-advance-file", advance_file or self._file("advance.json",
                                                              advance()),
            "--declaration-file", decl_file or self._file("decl.json",
                                                          declaration()),
            *extra,
        ]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = osr.main(argv)
        return code, out.getvalue().splitlines(), err.getvalue()

    def test_refresh_prints_decision_reason_and_prefix(self):
        code, lines, _ = self._run()
        self.assertEqual(0, code)
        self.assertEqual(3, len(lines), lines)
        self.assertEqual("decision=refresh", lines[0])
        self.assertTrue(lines[1].startswith("reason="))
        self.assertEqual(f"prefix={VERSIONS}", lines[2])

    def test_proceed_prints_two_lines(self):
        code, lines, _ = self._run(advance_file=self._file(
            "advance.json", advance([added("README.md")])))
        self.assertEqual(0, code)
        self.assertEqual(["decision=proceed"], lines[:1])
        self.assertEqual(2, len(lines), lines)

    def test_behind_by_zero_proceeds_with_an_unreadable_base_advance(self):
        for advance_file in (self._file("broken.json", raw="{not json"),
                             str(self.tmp / "does-not-exist.json")):
            with self.subTest(advance_file):
                code, lines, _ = self._run(
                    compare_file=self._file("c.json", compare(behind_by=0)),
                    advance_file=advance_file)
                self.assertEqual(0, code)
                self.assertEqual("decision=proceed", lines[0])

    def test_an_unreadable_base_advance_while_behind_waits_at_exit_zero(self):
        code, lines, _ = self._run(
            advance_file=self._file("broken.json", raw="{not json"))
        self.assertEqual(0, code)
        self.assertEqual("decision=wait", lines[0])

    def test_a_missing_declaration_file_proceeds(self):
        code, lines, _ = self._run(decl_file=str(self.tmp / "absent.json"))
        self.assertEqual(0, code)
        self.assertEqual("decision=proceed", lines[0])

    def test_a_malformed_declaration_file_waits_naming_it(self):
        decl = self._file("merge-recheck.json", raw="{not json")
        code, lines, _ = self._run(decl_file=decl)
        self.assertEqual(0, code)
        self.assertEqual("decision=wait", lines[0])
        self.assertIn(decl, lines[1])

    def test_the_receipts_file_is_read(self):
        receipts = self._file("receipts.json", [osr.receipt_marker(TIP_SHA)])
        code, lines, _ = self._run(extra=["--receipts-file", receipts])
        self.assertEqual(0, code)
        self.assertEqual("decision=wait", lines[0])

    def test_an_older_receipt_in_the_file_still_refreshes(self):
        receipts = self._file("receipts.json", [osr.receipt_marker(OLD_TIP)])
        code, lines, _ = self._run(extra=["--receipts-file", receipts])
        self.assertEqual("decision=refresh", lines[0])

    def test_an_unreadable_receipts_file_waits(self):
        receipts = self._file("receipts.json", raw="{not json")
        code, lines, _ = self._run(extra=["--receipts-file", receipts])
        self.assertEqual(0, code)
        self.assertEqual("decision=wait", lines[0])

    def test_an_unreadable_compare_file_exits_two(self):
        for label, path in {
            "not json": self._file("c.json", raw="{not json"),
            "absent": str(self.tmp / "absent.json"),
            "a list": self._file("l.json", [1, 2]),
            "null": self._file("n.json", raw="null"),
        }.items():
            with self.subTest(label):
                code, lines, err = self._run(compare_file=path)
                self.assertEqual(2, code)
                self.assertEqual([], lines)
                self.assertIn("compare", err.lower())

    def test_every_decision_exits_zero(self):
        cases = {
            "refresh": {},
            "proceed": {"decl_file": str(self.tmp / "absent.json")},
            "wait": {"advance_file": self._file("b.json", raw="[")},
        }
        for word, kwargs in cases.items():
            with self.subTest(word):
                code, lines, _ = self._run(**kwargs)
                self.assertEqual(0, code)
                self.assertEqual(f"decision={word}", lines[0])


# --------------------------------------------------------------------------- #
# Rule 6 — main gained a test that names a file the pull request changes       #
# (DRE-6871)                                                                   #
# --------------------------------------------------------------------------- #

FIXTURES = ROOT / "tests" / "fixtures" / "new-test-recheck"
SOURCE = "scripts/planning_escalation.py"
NEW_TEST = "tests/test_planning_escalation_prior_answer.py"
NAMING_PATCH = ("@@ -0,0 +1,3 @@\n+import sys\n"
                "+import planning_escalation  # noqa: E402\n+")
NO_TEST_RUN = ("no test run of this head exists, so there is nothing a "
               "refresh would re-test against — merging as today")


def recheck(**extra):
    """A declaration with the new-tests switch on and no order-sensitive
    paths."""
    return {"recheck_new_tests": True, **extra}


def added_test(name=NEW_TEST, patch=NAMING_PATCH, status="added"):
    entry = {"filename": name, "status": status}
    if patch is not None:
        entry["patch"] = patch
    return entry


def changed(name=SOURCE, status="modified"):
    return {"filename": name, "status": status}


def since(files=None):
    """The since-run payload: compare/<main's tip when the anchor run was
    created>...<main>."""
    return {"files": files if files is not None else [added_test()]}


def new_tests(**overrides):
    """The switch on, `main` having gained a test that names a source file the
    pull request changes, and nothing under an order-sensitive path."""
    kwargs = {
        "compare": compare([changed()]),
        "base_advance": advance([added_test()]),
        "declaration": recheck(),
        "since_run": since(),
    }
    kwargs.update(overrides)
    return decide(**kwargs)


class NewTestsContractTest(unittest.TestCase):
    def test_the_switch_is_the_agreed_key(self):
        self.assertEqual("recheck_new_tests", osr.RECHECK_KEY)

    def test_the_new_tests_receipt_marker_is_the_agreed_wording(self):
        self.assertEqual(f"Merge gate: refreshed for new tests onto {TIP_SHA}",
                         osr.new_tests_receipt_marker(TIP_SHA))

    def test_the_rule_names_are_the_agreed_words(self):
        self.assertEqual(("order-sensitive", "new-tests"),
                         (osr.RULE_ORDER_SENSITIVE, osr.RULE_NEW_TESTS))

    def test_since_run_is_a_keyword_defaulting_to_an_empty_object(self):
        import inspect
        param = inspect.signature(osr.decide).parameters["since_run"]
        self.assertEqual(inspect.Parameter.KEYWORD_ONLY, param.kind)
        self.assertEqual({}, param.default)

    def test_the_new_tests_receipt_is_not_read_as_a_migrations_receipt(self):
        got = decide(receipts=[osr.new_tests_receipt_marker(TIP_SHA)])
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertEqual(osr.RULE_ORDER_SENSITIVE, got.rule)

    def test_the_module_docstring_states_rule_six_after_rule_five(self):
        doc = " ".join((osr.__doc__ or "").split())
        self.assertIn("DRE-6871", doc)
        self.assertIn('"recheck_new_tests": true', doc)
        self.assertLess(doc.index(" 5. "), doc.index(" 6. "))
        self.assertLess(doc.index(" 6. "), doc.index("Known limit"))

    def test_the_module_docstring_names_the_anchor_run_in_the_contracts_words(
            self):
        doc = " ".join((osr.__doc__ or "").split())
        self.assertIn(
            "the newest run of the head by `created_at` whose event is "
            "`pull_request` and whose path is not in "
            "`merge_gate.DEFAULT_REVIEW_WORKFLOWS`", doc)
        self.assertIn("Never a `push` run, never a review run.", doc)

    def test_the_module_docstring_quotes_the_measured_firing_rate(self):
        doc = " ".join((osr.__doc__ or "").split())
        self.assertIn("12% of agent-bureau merges", doc)
        self.assertIn("15% of bureau-pipeline merges", doc)

    def test_the_module_docstring_states_lines_three_and_four(self):
        doc = " ".join((osr.__doc__ or "").split())
        self.assertIn("line 3 `prefix=<matched prefix>` (order-sensitive) or "
                      "`prefix=` with an empty value (new-tests), line 4 "
                      "`rule=order-sensitive` or `rule=new-tests`", doc)


class NewTestsFireTest(unittest.TestCase):
    def test_main_added_a_test_naming_a_file_this_pull_request_changes(self):
        got = new_tests()
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertEqual(osr.RULE_NEW_TESTS, got.rule)
        self.assertEqual("", got.prefix)
        self.assertEqual(f"main added {NEW_TEST}, which names {SOURCE}, "
                         f"changed by this pull request", got.reason)

    def test_the_mirror_this_pull_request_adds_a_test_naming_mains_file(self):
        got = new_tests(compare=compare([added_test()]),
                        since_run=since([changed()]))
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertEqual(osr.RULE_NEW_TESTS, got.rule)
        self.assertEqual(f"this pull request adds {NEW_TEST}, which names "
                         f"{SOURCE}, changed on main", got.reason)

    def test_the_since_run_payload_decides_not_the_base_advance(self):
        # The base advance holds the new test too, but it reaches back to the
        # merge base: a test `main` gained before the last test run began was
        # in that run, and must not fire.
        got = new_tests(since_run=since([]))
        self.assertEqual(osr.PROCEED, got.decision)
        got = new_tests(base_advance=advance([]))
        self.assertEqual(osr.REFRESH, got.decision)

    def test_a_renamed_test_is_new(self):
        got = new_tests(since_run=since([added_test(status="renamed")]))
        self.assertEqual(osr.REFRESH, got.decision)

    def test_a_modified_or_removed_test_is_not_new(self):
        for status in ("modified", "removed", "changed", "copied"):
            with self.subTest(status=status):
                got = new_tests(since_run=since([added_test(status=status)]))
                self.assertEqual(osr.PROCEED, got.decision)

    def test_a_source_file_of_any_status_is_changed(self):
        for status in ("added", "modified", "removed", "renamed", "copied",
                       "changed"):
            with self.subTest(status=status):
                got = new_tests(compare=compare([changed(status=status)]))
                self.assertEqual(osr.REFRESH, got.decision)
                got = new_tests(compare=compare([added_test()]),
                                since_run=since([changed(status=status)]))
                self.assertEqual(osr.REFRESH, got.decision)

    def test_every_test_file_shape_counts_as_a_test(self):
        for name in ("tests/check.py", "pkg/test/check.js",
                     "web/src/__tests__/check.ts", "pkg/test_check.py",
                     "pkg/check_test.py", "web/RiverRow.fold.test.ts",
                     "web/check.spec.tsx", "test_check.py"):
            with self.subTest(name):
                got = new_tests(since_run=since([added_test(name)]))
                self.assertEqual(osr.REFRESH, got.decision)
                self.assertIn(name, got.reason)

    def test_a_file_of_no_test_shape_is_a_source_and_names_nothing(self):
        for name in ("scripts/check.py", "src/testing/check.py",
                     "src/latest/check.py", "pkg/contest_check.py",
                     "pkg/check_tests.py", "web/check.test",
                     "web/test.ts", "docs/tests.md"):
            with self.subTest(name):
                got = new_tests(since_run=since([added_test(name)]))
                self.assertEqual(osr.PROCEED, got.decision)

    def test_a_changed_test_file_is_not_a_source_it_can_name(self):
        # The pull request changes a file named planning_escalation under
        # tests/: a test file, so the new test naming it does not fire.
        got = new_tests(compare=compare(
            [changed("tests/planning_escalation.py")]))
        self.assertEqual(osr.PROCEED, got.decision)

    def test_the_name_is_the_basename_up_to_the_first_dot(self):
        patch = "+import { greenLightGrid } from './greenLightGrid';"
        got = new_tests(
            compare=compare([changed("web/src/greenLightGrid.module.ts")]),
            since_run=since([added_test("web/src/RiverRow.fold.test.ts",
                                        patch)]))
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertIn("web/src/greenLightGrid.module.ts", got.reason)

    def test_a_name_of_exactly_six_characters_counts(self):
        got = new_tests(compare=compare([changed("scripts/config.py")]),
                        since_run=since([added_test(patch="+import config")]))
        self.assertEqual(osr.REFRESH, got.decision)


class NewTestsNoFireTest(unittest.TestCase):
    def test_a_name_under_six_characters_is_ignored(self):
        got = new_tests(compare=compare([changed("scripts/gate.py")]),
                        since_run=since([added_test(patch="+import gate")]))
        self.assertEqual(osr.PROCEED, got.decision)

    def test_a_name_only_inside_a_longer_word_does_not_count(self):
        patch = ("+import planning_escalation_v2\n"
                 "+from test_planning_escalation_prior import x\n"
                 "+xplanning_escalation = 1")
        got = new_tests(since_run=since([added_test(patch=patch)]))
        self.assertEqual(osr.PROCEED, got.decision)

    def test_the_match_is_case_sensitive(self):
        got = new_tests(since_run=since(
            [added_test(patch="+import Planning_Escalation")]))
        self.assertEqual(osr.PROCEED, got.decision)

    def test_a_name_in_the_tests_own_filename_alone_does_not_count(self):
        got = new_tests(since_run=since([added_test(patch="+import os")]))
        self.assertEqual(osr.PROCEED, got.decision)

    def test_a_new_test_with_no_patch_names_nothing_and_is_named(self):
        got = new_tests(since_run=since([added_test(patch=None)]))
        self.assertEqual(osr.PROCEED, got.decision)
        self.assertIn(NEW_TEST, got.reason)

    def test_the_mirrors_unreadable_test_is_named_too(self):
        got = new_tests(compare=compare([added_test(patch=None)]),
                        since_run=since([changed()]))
        self.assertEqual(osr.PROCEED, got.decision)
        self.assertIn(NEW_TEST, got.reason)

    def test_an_unreadable_test_does_not_hide_a_readable_one(self):
        other = "tests/test_other.py"
        got = new_tests(since_run=since(
            [added_test(other, patch=None), added_test()]))
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertIn(NEW_TEST, got.reason)


class NewTestsSwitchTest(unittest.TestCase):
    """The switch, the declaration around it, and when `since_run` is read."""

    def test_no_key_decides_as_today_without_reading_since_run(self):
        got = decide(compare=compare([changed()]),
                     base_advance=advance([added_test()]),
                     since_run=Untouchable())
        self.assertEqual(osr.PROCEED, got.decision)
        self.assertEqual("", got.rule)

    def test_the_key_false_decides_as_today_without_reading_since_run(self):
        decl = dict(declaration(), recheck_new_tests=False)
        got = decide(compare=compare([changed()]),
                     base_advance=advance([added_test()]),
                     declaration=decl, since_run=Untouchable())
        self.assertEqual(osr.PROCEED, got.decision)
        got = decide(declaration=decl, since_run=Untouchable())
        self.assertEqual(osr.REFRESH, got.decision)

    def test_the_key_false_still_needs_its_paths(self):
        got = decide(declaration={"recheck_new_tests": False})
        self.assertEqual(osr.WAIT, got.decision)
        self.assertIn(osr.DECLARATION_PATH, got.reason)

    def test_the_key_alone_is_a_valid_declaration(self):
        got = new_tests(declaration={"recheck_new_tests": True},
                        since_run=since([]))
        self.assertEqual(osr.PROCEED, got.decision)
        self.assertEqual("", got.rule)

    def test_any_other_value_for_the_key_waits_naming_the_file(self):
        for value in ("yes", "true", 1, 0, None, [], {}):
            with self.subTest(value=value):
                got = new_tests(declaration={"recheck_new_tests": value},
                                since_run=Untouchable())
                self.assertEqual(osr.WAIT, got.decision)
                self.assertIn(osr.DECLARATION_PATH, got.reason)
                self.assertIn("recheck_new_tests", got.reason)

    def test_a_bad_key_waits_even_when_current(self):
        got = decide(declaration={"recheck_new_tests": "yes"},
                     compare=compare(behind_by=0))
        self.assertEqual(osr.WAIT, got.decision)

    def test_malformed_paths_beside_the_key_wait_naming_the_file(self):
        for paths in (VERSIONS, [7], ["no-slash"], None):
            with self.subTest(paths=paths):
                got = new_tests(
                    declaration=recheck(order_sensitive_paths=paths))
                self.assertEqual(osr.WAIT, got.decision)
                self.assertIn(osr.DECLARATION_PATH, got.reason)

    def test_not_behind_proceeds_without_reading_since_run(self):
        for behind_by in (0, None):
            with self.subTest(behind_by=behind_by):
                got = new_tests(compare=compare([changed()],
                                                behind_by=behind_by),
                                since_run=Untouchable())
                self.assertEqual(osr.PROCEED, got.decision)

    def test_no_test_run_of_this_head_proceeds(self):
        got = new_tests(since_run={"no_test_run": True})
        self.assertEqual(osr.PROCEED, got.decision)
        self.assertEqual(NO_TEST_RUN, got.reason)
        self.assertIn("no test run of this head exists", got.reason)

    def test_a_since_run_read_that_failed_waits_naming_the_payload(self):
        for label, payload in {
            "empty object": {},
            "a list": [added_test()],
            "no files": {"ahead_by": 3},
            "files not a list": {"files": "x"},
            "no_test_run false": {"no_test_run": False},
            "no_test_run a string": {"no_test_run": "true"},
            "unreadable": osr.UNREADABLE,
            "null": None,
        }.items():
            with self.subTest(label):
                got = new_tests(since_run=payload)
                self.assertEqual(osr.WAIT, got.decision)
                self.assertIn("since-run payload", got.reason)

    def test_since_run_defaults_to_a_failed_read(self):
        kwargs = {"compare": compare([changed()]),
                  "base_advance": advance([added_test()]),
                  "declaration": recheck()}
        self.assertEqual(osr.WAIT, osr.decide(**kwargs).decision)


class NewTestsReceiptTest(unittest.TestCase):
    """One refresh per pull request for this rule, whatever the tip."""

    def test_a_receipt_for_any_tip_proceeds_whatever_since_run_holds(self):
        for tip in (TIP_SHA, OLD_TIP):
            for label, payload in {"fires": since(), "a failed read": {},
                                   "unread": Untouchable()}.items():
                with self.subTest(tip=tip[:1], since_run=label):
                    got = new_tests(
                        receipts=[f"♻️ {osr.new_tests_receipt_marker(tip)} — "
                                  f"why"],
                        since_run=payload)
                    self.assertEqual(osr.PROCEED, got.decision)
                    self.assertEqual("", got.rule)

    def test_a_receipt_as_a_comment_object_is_read(self):
        got = new_tests(receipts=[
            {"body": osr.new_tests_receipt_marker(OLD_TIP)}])
        self.assertEqual(osr.PROCEED, got.decision)

    def test_a_migrations_receipt_for_an_older_tip_does_not_stop_this_rule(
            self):
        got = new_tests(receipts=[osr.receipt_marker(OLD_TIP)])
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertEqual(osr.RULE_NEW_TESTS, got.rule)

    def test_the_migrations_rule_still_decides_as_today_after_one(self):
        receipt = osr.new_tests_receipt_marker(OLD_TIP)
        decl = recheck(order_sensitive_paths=[VERSIONS])
        both = compare([added(OURS), changed()])
        got = new_tests(compare=both, base_advance=advance(),
                        declaration=decl, receipts=[receipt])
        today = decide(compare=both)
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertEqual(osr.RULE_ORDER_SENSITIVE, got.rule)
        self.assertEqual(VERSIONS, got.prefix)
        self.assertEqual(today.reason, got.reason)
        got = new_tests(compare=both, base_advance=advance(),
                        declaration=decl,
                        receipts=[receipt, osr.receipt_marker(TIP_SHA)])
        self.assertEqual(osr.WAIT, got.decision)


class BothRulesTest(unittest.TestCase):
    def test_the_migrations_rule_wins_with_todays_reason(self):
        both = compare([added(OURS), changed()])
        got = decide(compare=both, base_advance=advance([added(THEIRS)]),
                     declaration=recheck(order_sensitive_paths=[VERSIONS]),
                     since_run=since())
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertEqual(osr.RULE_ORDER_SENSITIVE, got.rule)
        self.assertEqual(VERSIONS, got.prefix)
        self.assertEqual(decide(compare=both).reason, got.reason)

    def test_an_order_sensitive_refresh_names_its_rule(self):
        self.assertEqual(osr.RULE_ORDER_SENSITIVE, decide().rule)

    def test_rule_is_empty_on_proceed_and_wait(self):
        for got in (decide(compare=compare(behind_by=0)),
                    decide(base_advance={}),
                    new_tests(since_run={}),
                    new_tests(since_run={"no_test_run": True})):
            with self.subTest(got.reason):
                self.assertEqual("", got.rule)


class NewTestsFixtureTest(unittest.TestCase):
    """The two recorded clashes the rule would have caught."""

    def _decide(self, name):
        fixture = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        got = osr.decide(compare=fixture["compare"],
                         base_advance=fixture["base_advance"],
                         declaration=fixture["declaration"],
                         receipts=fixture["receipts"],
                         since_run=fixture["since_run"])
        return fixture, got

    def test_agent_bureau_3604_refreshes(self):
        fixture, got = self._decide("agent-bureau-3604.json")
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertEqual(osr.RULE_NEW_TESTS, got.rule)
        self.assertIn("RiverRow.fold.test.ts", got.reason)
        self.assertIn("greenLightGrid.ts", got.reason)
        self.assertEqual(
            f"main added {fixture['expect']['test_file']}, which names "
            f"{fixture['expect']['source_file']}, changed by this pull "
            f"request", got.reason)

    def test_bureau_pipeline_871_refreshes(self):
        fixture, got = self._decide("bureau-pipeline-871.json")
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertEqual(osr.RULE_NEW_TESTS, got.rule)
        self.assertEqual(
            "main added tests/test_planning_escalation_prior_answer.py, which "
            "names scripts/planning_escalation.py, changed by this pull "
            "request", got.reason)

    def _decide_1030(self, *, exchanged=False, **overrides):
        fixture = json.loads((FIXTURES / "bureau-pipeline-1030.json")
                             .read_text(encoding="utf-8"))
        ours = fixture["compare"]["files"]
        theirs = fixture["since_run"]["files"]
        if exchanged:
            ours, theirs = theirs, ours
        kwargs = {"compare": dict(fixture["compare"], files=ours),
                  "base_advance": fixture["base_advance"],
                  "declaration": fixture["declaration"],
                  "receipts": fixture["receipts"],
                  "since_run": dict(fixture["since_run"], files=theirs)}
        kwargs.update(overrides)
        return fixture, osr.decide(**kwargs)

    def test_bureau_pipeline_1030_refreshes(self):
        # DRE-6967: #1030's guard polices other tests, and main gained #1031's
        # test file while #1030's tests were running.
        fixture, got = self._decide_1030()
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertEqual(osr.RULE_NEW_TESTS, got.rule)
        opens = ("this pull request adds "
                 "tests/test_no_test_pins_a_console_choice.py, which polices "
                 "other tests (it names ")
        ends = (" of them), and main added tests/test_model_caution.py since "
                "this head's last test run")
        self.assertTrue(got.reason.startswith(opens), got.reason)
        self.assertTrue(got.reason.endswith(ends), got.reason)
        count = int(got.reason[len(opens):-len(ends)])
        self.assertGreaterEqual(count, 3)
        self.assertEqual(fixture["expect"]["named"], count)
        self.assertEqual(28, count)

    def test_bureau_pipeline_1030_with_the_sides_exchanged_refreshes(self):
        _fixture, got = self._decide_1030(exchanged=True)
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertEqual(osr.RULE_NEW_TESTS, got.rule)
        self.assertTrue(got.reason.startswith(
            "main added tests/test_no_test_pins_a_console_choice.py, which "
            "polices other tests"), got.reason)
        self.assertEqual(
            "main added tests/test_no_test_pins_a_console_choice.py, which "
            "polices other tests (it names 28 of them), and this pull request "
            "adds tests/test_model_caution.py", got.reason)

    def test_bureau_pipeline_1030_with_the_switch_off_proceeds(self):
        _fixture, got = self._decide_1030(
            declaration={"recheck_new_tests": False,
                         "order_sensitive_paths": []},
            since_run=Untouchable())
        self.assertEqual(osr.PROCEED, got.decision)
        self.assertEqual("", got.rule)

    def test_bureau_pipeline_1030_after_one_refresh_proceeds(self):
        _fixture, got = self._decide_1030(receipts=[
            f"♻️ {osr.new_tests_receipt_marker(OLD_TIP)} — why"])
        self.assertEqual(osr.PROCEED, got.decision)
        self.assertEqual(
            "behind `main` by 12, but no declared prefix was added to on both "
            "sides, and this pull request was already refreshed once for new "
            "tests — never a second time", got.reason)

    def test_each_fixture_is_quiet_with_the_switch_off(self):
        # The clash is caught by the new rule alone, never by the old one.
        for name in ("agent-bureau-3604.json", "bureau-pipeline-871.json"):
            with self.subTest(name):
                fixture = json.loads(
                    (FIXTURES / name).read_text(encoding="utf-8"))
                decl = dict(fixture["declaration"], recheck_new_tests=False)
                decl.setdefault("order_sensitive_paths", [])
                got = osr.decide(compare=fixture["compare"],
                                 base_advance=fixture["base_advance"],
                                 declaration=decl,
                                 receipts=fixture["receipts"],
                                 since_run=Untouchable())
                self.assertEqual(osr.PROCEED, got.decision)


GUARD = "tests/test_guard_reads_the_folder.py"
OTHER_TEST = "tests/test_other_side.py"
UNNAMED_SOURCE = "scripts/unrelated_module.py"


def policing_patch(count, *, own=None, added=True):
    """A patch whose added lines name `count` other test files, the way an
    allow-list does."""
    mark = "+" if added else "-"
    lines = ["@@ -0,0 +1,9 @@", f"{mark}ALLOWED = {{"]
    lines += [f'{mark}    "tests/test_allowed_{n}.py",' for n in range(count)]
    if own:
        lines += [f"{mark}# {own} {own} tests/{own}"]
    lines.append(f"{mark}}}")
    return "\n".join(lines)


def policing(**overrides):
    """The switch on, a guard on one side and a new test file on the other;
    by default the pull request adds the guard."""
    kwargs = {
        "compare": compare([added_test(GUARD, policing_patch(3)),
                            changed("tests/test_touched.py")]),
        "base_advance": advance([added_test(OTHER_TEST, "+import os")]),
        "declaration": recheck(),
        "since_run": since([added_test(OTHER_TEST, "+import os"),
                            changed(UNNAMED_SOURCE)]),
    }
    kwargs.update(overrides)
    return decide(**kwargs)


class NewTestsPolicingTest(unittest.TestCase):
    """Rule 6f (DRE-6967): a new test that polices other tests meets a new
    test file from the other side — #1030 and #1031."""

    def test_the_threshold_is_one_named_constant_of_three(self):
        self.assertEqual(3, osr.POLICING_MIN_NAMED)

    def test_a_guard_in_this_pull_request_meets_a_new_test_on_main(self):
        got = policing()
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertEqual(osr.RULE_NEW_TESTS, got.rule)
        self.assertEqual("", got.prefix)
        self.assertEqual(
            f"this pull request adds {GUARD}, which polices other tests (it "
            f"names 3 of them), and main added {OTHER_TEST} since this head's "
            f"last test run", got.reason)

    def test_a_guard_on_main_meets_a_new_test_in_this_pull_request(self):
        got = policing(compare=compare([added_test(OTHER_TEST, "+import os"),
                                        changed(UNNAMED_SOURCE)]),
                       since_run=since([added_test(GUARD, policing_patch(4))]))
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertEqual(osr.RULE_NEW_TESTS, got.rule)
        self.assertEqual(
            f"main added {GUARD}, which polices other tests (it names 4 of "
            f"them), and this pull request adds {OTHER_TEST}", got.reason)

    def test_naming_two_other_tests_proceeds_and_three_refreshes(self):
        for count, word in ((0, osr.PROCEED), (2, osr.PROCEED),
                            (3, osr.REFRESH), (28, osr.REFRESH)):
            with self.subTest(count=count):
                got = policing(compare=compare(
                    [added_test(GUARD, policing_patch(count))]))
                self.assertEqual(word, got.decision)
                got = policing(
                    compare=compare([added_test(OTHER_TEST, "+import os")]),
                    since_run=since([added_test(GUARD,
                                                policing_patch(count))]))
                self.assertEqual(word, got.decision)

    def test_a_guard_with_no_new_test_file_on_the_other_side_proceeds(self):
        for label, others in {
            "nothing": [],
            "a source": [changed(UNNAMED_SOURCE)],
            "a modified test": [changed(OTHER_TEST)],
            "a removed test": [changed(OTHER_TEST, status="removed")],
            "a new source": [changed(UNNAMED_SOURCE, status="added")],
        }.items():
            with self.subTest(label):
                got = policing(since_run=since(others))
                self.assertEqual(osr.PROCEED, got.decision)
                got = policing(compare=compare(others),
                               since_run=since([added_test(
                                   GUARD, policing_patch(5))]))
                self.assertEqual(osr.PROCEED, got.decision)

    def test_a_renamed_test_file_on_the_other_side_counts(self):
        got = policing(since_run=since([added_test(OTHER_TEST, "+import os",
                                                   status="renamed")]))
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertIn(OTHER_TEST, got.reason)

    def test_the_tests_own_basename_does_not_count(self):
        own = posixpath.basename(GUARD)
        got = policing(compare=compare(
            [added_test(GUARD, policing_patch(2, own=own))]))
        self.assertEqual(osr.PROCEED, got.decision)
        got = policing(compare=compare(
            [added_test(GUARD, policing_patch(3, own=own))]))
        self.assertIn("(it names 3 of them)", got.reason)

    def test_each_distinct_name_counts_once(self):
        patch = "+" + " ".join(["tests/test_one.py"] * 3 + ["test_two.py"] * 2)
        got = policing(compare=compare([added_test(GUARD, patch)]))
        self.assertEqual(osr.PROCEED, got.decision)

    def test_only_the_lines_the_patch_adds_count(self):
        removed = policing_patch(5, added=False)
        context = "\n".join(" " + line[1:] for line in
                            policing_patch(5).splitlines()[1:])
        for patch in (removed, "@@ -1,7 +1,7 @@\n" + context):
            with self.subTest(patch=patch[:20]):
                got = policing(compare=compare([added_test(GUARD, patch)]))
                self.assertEqual(osr.PROCEED, got.decision)

    def test_a_name_is_a_whole_run_with_trailing_dots_dropped(self):
        got = policing(compare=compare([added_test(
            GUARD, "+see test_a.py, b_test.py and RiverRow.fold.test.ts.")]))
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertIn("(it names 3 of them)", got.reason)
        # Inside a longer run, a test-shaped piece is not a name.
        got = policing(compare=compare([added_test(
            GUARD, "+test_a.pyc test_b.py-old xtest_c.py test_d.pyz")]))
        self.assertEqual(osr.PROCEED, got.decision)

    def test_a_guard_with_no_patch_proceeds_and_is_named(self):
        got = policing(compare=compare([added_test(GUARD, patch=None)]))
        self.assertEqual(osr.PROCEED, got.decision)
        self.assertIn(f"could not read {GUARD}", got.reason)
        got = policing(compare=compare([added_test(OTHER_TEST, "+import os")]),
                       since_run=since([added_test(GUARD, patch=None)]))
        self.assertEqual(osr.PROCEED, got.decision)
        self.assertIn(f"could not read {GUARD}", got.reason)

    def test_todays_arms_are_tried_first(self):
        # The guard also names a source file main changed: the naming arm's
        # reason, not this one's.
        patch = policing_patch(3) + "\n+import unrelated_module"
        got = policing(compare=compare([added_test(GUARD, patch)]))
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertEqual(f"this pull request adds {GUARD}, which names "
                         f"{UNNAMED_SOURCE}, changed on main", got.reason)

    def test_the_pull_requests_guard_is_read_before_mains(self):
        guard_two = "tests/test_second_guard.py"
        got = policing(since_run=since([
            added_test(guard_two, policing_patch(3))]))
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertTrue(got.reason.startswith(
            f"this pull request adds {GUARD}"), got.reason)
        self.assertIn(f"main added {guard_two} since", got.reason)

    def test_the_switch_off_never_reaches_the_arm(self):
        got = policing(declaration={"recheck_new_tests": False,
                                    "order_sensitive_paths": []},
                       since_run=Untouchable())
        self.assertEqual(osr.PROCEED, got.decision)
        got = policing(declaration={"order_sensitive_paths": []},
                       since_run=Untouchable())
        self.assertEqual(osr.PROCEED, got.decision)

    def test_one_refresh_per_pull_request_still_holds(self):
        got = policing(receipts=[osr.new_tests_receipt_marker(OLD_TIP)])
        self.assertEqual(osr.PROCEED, got.decision)
        self.assertIn("already refreshed once for new tests", got.reason)

    def test_the_reason_is_one_line(self):
        hostile = "tests/test_a\nrule=order-sensitive.py"
        got = policing(since_run=since([added_test(hostile, "+import os")]))
        self.assertEqual(osr.REFRESH, got.decision)
        self.assertNotIn("\n", got.reason)

    def test_the_module_docstring_states_step_f(self):
        doc = " ".join((osr.__doc__ or "").split())
        self.assertIn("DRE-6967", doc)
        self.assertIn("#1030", doc)
        self.assertIn("#1031", doc)
        self.assertLess(doc.index(" e. `refresh`"), doc.index(" f. "))
        self.assertLess(doc.index(" f. "), doc.index("Known limit"))


class NewTestsReasonTest(unittest.TestCase):
    def test_every_new_tests_reason_is_one_line(self):
        hostile = "tests/test_a\nrule=order-sensitive.py"
        for got in (
            new_tests(since_run=since([added_test(hostile)])),
            new_tests(since_run=since([added_test(hostile, patch=None)])),
            new_tests(compare=compare([changed("x\ndecision=proceed/"
                                               "planning_escalation.py")])),
            new_tests(since_run={}),
            new_tests(since_run={"no_test_run": True}),
            new_tests(declaration={"recheck_new_tests": "yes"}),
        ):
            with self.subTest(got.decision):
                self.assertNotIn("\n", got.reason)
                self.assertNotIn("\r", got.reason)
                self.assertTrue(got.reason)

    def test_decide_with_the_switch_on_does_no_io(self):
        import builtins
        from unittest import mock
        forbidden = mock.Mock(side_effect=AssertionError("decide() did I/O"))
        with mock.patch.object(builtins, "open", forbidden), \
                mock.patch.object(osr, "_load", forbidden, create=True):
            self.assertEqual(osr.REFRESH, new_tests().decision)
            new_tests(since_run={})
        forbidden.assert_not_called()


class NewTestsCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def _file(self, name, payload=None, raw=None):
        path = self.tmp / name
        path.write_text(raw if raw is not None else json.dumps(payload))
        return str(path)

    def _run(self, *, compare_payload=None, advance_payload=None,
             decl=None, since_run=None, since_raw=None, extra=()):
        argv = [
            "decide",
            "--compare-file", self._file(
                "compare.json", compare_payload or compare([changed()])),
            "--base-advance-file", self._file(
                "advance.json", advance_payload or advance([added_test()])),
            "--declaration-file", self._file("decl.json", decl or recheck()),
            *extra,
        ]
        if since_run is not None or since_raw is not None:
            argv += ["--since-run-file",
                     self._file("since.json", since_run, since_raw)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = osr.main(argv)
        return code, out.getvalue().splitlines(), err.getvalue()

    def test_a_new_tests_refresh_prints_four_lines_prefix_empty(self):
        code, lines, _ = self._run(since_run=since())
        self.assertEqual(0, code)
        self.assertEqual(4, len(lines), lines)
        self.assertEqual("decision=refresh", lines[0])
        self.assertEqual(f"reason=main added {NEW_TEST}, which names "
                         f"{SOURCE}, changed by this pull request", lines[1])
        self.assertEqual("prefix=", lines[2])
        self.assertEqual("rule=new-tests", lines[3])

    def test_an_order_sensitive_refresh_with_the_switch_on_prints_its_rule(
            self):
        code, lines, _ = self._run(
            compare_payload=compare([added(OURS), changed()]),
            advance_payload=advance([added(THEIRS)]),
            decl=recheck(order_sensitive_paths=[VERSIONS]),
            since_run=since())
        self.assertEqual(0, code)
        self.assertEqual(4, len(lines), lines)
        self.assertEqual("decision=refresh", lines[0])
        self.assertEqual(f"prefix={VERSIONS}", lines[2])
        self.assertEqual("rule=order-sensitive", lines[3])

    def test_proceed_and_wait_print_no_prefix_or_rule_line(self):
        for label, kwargs in {
            "proceed: nothing fires": {"since_run": since([])},
            "proceed: no test run": {"since_run": {"no_test_run": True}},
            "wait: a failed read": {"since_run": {}},
            "wait: a bad switch": {"decl": {"recheck_new_tests": "yes"},
                                   "since_run": since()},
        }.items():
            with self.subTest(label):
                code, lines, _ = self._run(**kwargs)
                self.assertEqual(0, code)
                self.assertEqual(2, len(lines), lines)
                self.assertEqual(f"decision={label.split(':')[0]}", lines[0])
                self.assertFalse(any(line.startswith(("prefix=", "rule="))
                                     for line in lines))

    def test_an_absent_since_run_file_reads_as_an_empty_object(self):
        code, lines, _ = self._run()
        self.assertEqual(0, code)
        self.assertEqual("decision=wait", lines[0])
        self.assertIn("since-run payload", lines[1])

    def test_an_unreadable_since_run_file_waits(self):
        code, lines, _ = self._run(since_raw="{not json")
        self.assertEqual(0, code)
        self.assertEqual("decision=wait", lines[0])

    def test_the_since_run_file_is_ignored_with_the_switch_off(self):
        code, lines, _ = self._run(decl=declaration(), since_raw="{not json")
        self.assertEqual(0, code)
        self.assertEqual("decision=proceed", lines[0])

    def test_a_new_tests_receipt_in_the_file_proceeds(self):
        receipts = self._file("receipts.json",
                              [osr.new_tests_receipt_marker(OLD_TIP)])
        code, lines, _ = self._run(since_run=since(),
                                   extra=["--receipts-file", receipts])
        self.assertEqual(0, code)
        self.assertEqual("decision=proceed", lines[0])


class PurityTest(unittest.TestCase):
    """Standard library only, and decide() does no I/O."""

    def test_the_module_imports_only_the_standard_library(self):
        import ast
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(0, node.level, "no relative imports")
                names.add(node.module.split(".")[0])
        stdlib = set(sys.stdlib_module_names)
        self.assertEqual(set(), names - stdlib, "non-stdlib imports")
        for leaves in ("subprocess", "urllib", "socket", "http", "requests"):
            self.assertNotIn(leaves, names)

    def test_decide_does_no_io(self):
        import builtins
        from unittest import mock
        forbidden = mock.Mock(side_effect=AssertionError("decide() did I/O"))
        with mock.patch.object(builtins, "open", forbidden), \
                mock.patch.object(osr, "_load", forbidden, create=True):
            self.assertEqual(osr.REFRESH, decide().decision)
            decide(declaration=osr.UNREADABLE)
            decide(base_advance={})
            decide(receipts=[osr.receipt_marker(TIP_SHA)])
        forbidden.assert_not_called()

    def test_the_module_emits_no_verdict_marker(self):
        source = MODULE.read_text(encoding="utf-8")
        for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
