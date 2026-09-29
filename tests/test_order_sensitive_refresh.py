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
