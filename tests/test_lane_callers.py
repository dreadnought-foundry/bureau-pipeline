"""`lane_callers.callers_of` names every caller of a lane-writing function (DRE-5345).

A function that writes a lane is reached from wherever calls it, so a check
that reads write sites alone attributes a borrowed write to the wrong owner.
DRE-5282 reconciles the CALLERS against the lane contract; this is the
discovery it reads, and these tests prove the discovery rather than list what
it finds.

Every proof below runs on a THROWAWAY COPY of the repository's `scripts/` and
`.github/workflows/`, passed to `callers_of` as `root`. The reference
(`tests/test_no_unplanned_ready_lane_writer.py`) stages new files into the real
tree; these tests also EDIT existing files — delete the `park(...)` line,
rewrite a CLI map, add a step to `plan.yml` — and an edit to the real tree is
one crashed test away from a corrupted checkout. The copy is read exactly the
way the real tree is: same relative paths, same globs, only the root differs.
"""

import ast
import dataclasses
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lane_callers  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "lane_callers.py"

ESCALATION = "scripts/planning_escalation.py"
ROUTE = "scripts/planning_route.py"
HOLD = "scripts/code_owner_hold.py"

#: The units the card's contract names over `main` as it leaves this card.
ESCALATE_CALLERS = {
    "scripts/planning_escalation.py#_cmd_escalate",
    "scripts/planning_route.py#_cmd_exit",
    ".github/workflows/plan.yml#Classification refused — park the card for the CEO",
    ".github/workflows/plan.yml#One-off critic — escalate",
    ".github/workflows/plan.yml#Planner escalation — hand-planning parks for the CEO",
    ".github/workflows/plan.yml#Roll-up route — check the split",
}
PARK_CALLERS = {
    "scripts/code_owner_hold.py#_cmd_hold",
    ".github/workflows/merge-gate.yml#Evaluate and merge",
}
EXIT_CALLERS = {
    ".github/workflows/plan.yml#Roll-up route — hand off",
    ".github/workflows/plan.yml#One-off route — checked on the way out",
}


class _Copy:
    """A throwaway copy of the tree `callers_of` reads, removed afterwards."""

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(prefix="lane-callers-")
        root = Path(self._tmp.name)
        shutil.copytree(ROOT / "scripts", root / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / ".github" / "workflows", root / ".github" / "workflows")
        return root

    def __exit__(self, *exc):
        self._tmp.cleanup()
        return False


def _edit(path: Path, old: str, new: str) -> None:
    """Replace one exact passage, and fail loudly if it is not there — an edit
    that silently matched nothing would leave the test proving the original."""
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"{path.name}: expected exactly one {old!r}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def _insert_step_before(workflow: Path, step_name: str, block: str) -> None:
    """Add a step to a workflow ahead of a named one, at that step's indent."""
    lines = workflow.read_text(encoding="utf-8").splitlines(keepends=True)
    anchor = f"- name: {step_name}"
    index = next(i for i, line in enumerate(lines) if line.strip() == anchor)
    indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
    new = [indent + line if line.strip() else line
           for line in textwrap.dedent(block).lstrip().splitlines(keepends=True)]
    workflow.write_text("".join(lines[:index] + new + lines[index:]), encoding="utf-8")


class TheReportShape(unittest.TestCase):
    def test_the_report_is_a_frozen_dataclass_of_two_frozensets(self):
        report = lane_callers.callers_of(HOLD, "park", root=str(ROOT))
        self.assertIsInstance(report.callers, frozenset)
        self.assertIsInstance(report.unread, frozenset)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            report.callers = frozenset()

    def test_an_unknown_module_or_function_is_refused_by_name(self):
        with self.assertRaises(LookupError) as caught:
            lane_callers.callers_of(HOLD, "no_such_function", root=str(ROOT))
        self.assertIn("no_such_function", str(caught.exception))
        with self.assertRaises(LookupError) as caught:
            lane_callers.callers_of("scripts/no_such_module.py", "park", root=str(ROOT))
        self.assertIn("no_such_module.py", str(caught.exception))


class OverThisRepository(unittest.TestCase):
    """The contract DRE-5282 reads, over the repository as it stands."""

    def test_escalate_is_reached_by_the_units_the_contract_names(self):
        report = lane_callers.callers_of(ESCALATION, "escalate", root=str(ROOT))
        self.assertLessEqual(ESCALATE_CALLERS, report.callers, sorted(report.callers))
        self.assertEqual(report.unread, frozenset())

    def test_discovery_is_direct_so_the_exit_steps_are_not_escalate_callers(self):
        # `planning_route.py exit` reaches `escalate` through `_cmd_exit`, a
        # function in ANOTHER module. That is `_cmd_exit`'s record (the one-hop
        # rule is DRE-5282's to apply), never `escalate`'s.
        report = lane_callers.callers_of(ESCALATION, "escalate", root=str(ROOT))
        self.assertFalse(EXIT_CALLERS & report.callers, sorted(report.callers))

    def test_park_is_reached_by_the_hold_handler_and_the_merge_gate(self):
        report = lane_callers.callers_of(HOLD, "park", root=str(ROOT))
        self.assertLessEqual(PARK_CALLERS, report.callers, sorted(report.callers))
        self.assertEqual(report.unread, frozenset())

    def test_cmd_exit_is_reached_by_the_two_route_steps(self):
        report = lane_callers.callers_of(ROUTE, "_cmd_exit", root=str(ROOT))
        self.assertLessEqual(EXIT_CALLERS, report.callers, sorted(report.callers))
        self.assertEqual(report.unread, frozenset())

    def test_the_defining_modules_main_is_never_a_caller(self):
        for module, function in ((ESCALATION, "escalate"), (HOLD, "park"),
                                 (ROUTE, "_cmd_exit")):
            report = lane_callers.callers_of(module, function, root=str(ROOT))
            self.assertNotIn("scripts/planning_route.py#main", report.callers)
            self.assertFalse(
                [c for c in report.callers if c.endswith("#main")], sorted(report.callers)
            )

    def test_the_dict_form_map_resolves_a_linear_ops_step(self):
        # `linear_ops.py` has no `main`: its map is the `{"<name>": <handler>}`
        # dict in its `__main__` block. `comment` maps to `cmd_comment`, and the
        # merge gate runs `linear_ops.py comment`.
        report = lane_callers.callers_of("scripts/linear_ops.py", "cmd_comment",
                                         root=str(ROOT))
        self.assertIn(".github/workflows/merge-gate.yml#Evaluate and merge",
                      report.callers)
        self.assertEqual(report.unread, frozenset())

    def test_the_sweeps_main_is_a_cmd_state_caller(self):
        # `reconcile.py`'s `main` moves cards to Done, Todo and Backlog through
        # `linear_ops.cmd_state`. It is ANOTHER module's `main`, not the
        # defining module's dispatch, so it is a caller like any other def.
        report = lane_callers.callers_of("scripts/linear_ops.py", "cmd_state",
                                         root=str(ROOT))
        self.assertIn("scripts/reconcile.py#main", report.callers)
        self.assertEqual(report.unread, frozenset())


class BareInModuleCalls(unittest.TestCase):
    def test_the_unedited_copy_reports_both_bare_callers(self):
        with _Copy() as root:
            park = lane_callers.callers_of(HOLD, "park", root=str(root))
            escalate = lane_callers.callers_of(ESCALATION, "escalate", root=str(root))
        self.assertIn("scripts/code_owner_hold.py#_cmd_hold", park.callers)
        self.assertIn("scripts/planning_escalation.py#_cmd_escalate", escalate.callers)

    def test_deleting_the_park_line_removes_the_hold_handler_and_its_step(self):
        with _Copy() as root:
            path = root / HOLD
            # `pass`, not an empty line: the call is the only statement of a
            # `try:` body, and a copy that no longer parses would stop
            # reporting `_cmd_hold` for the wrong reason.
            _edit(path, "        park(args.card, args.pr, sentence)\n", "        pass\n")
            ast.parse(path.read_text(encoding="utf-8"))
            report = lane_callers.callers_of(HOLD, "park", root=str(root))
        self.assertNotIn("scripts/code_owner_hold.py#_cmd_hold", report.callers)
        # `hold` mapped to `_cmd_hold`, an in-module caller of `park`. With the
        # call gone, the step that runs `hold` reaches `park` no more either —
        # which proves the step was found THROUGH the map, not by name.
        self.assertNotIn(".github/workflows/merge-gate.yml#Evaluate and merge",
                         report.callers)


class CallsFromOtherModules(unittest.TestCase):
    def test_a_module_dot_function_call_added_to_reconcile_is_reported(self):
        with _Copy() as root:
            path = root / "scripts" / "reconcile.py"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(textwrap.dedent('''

                    def probe_sweep_escalation(card):
                        return planning_escalation.escalate(None, card, "probe")
                '''))
            report = lane_callers.callers_of(ESCALATION, "escalate", root=str(root))
        self.assertIn("scripts/reconcile.py#probe_sweep_escalation", report.callers)

    def test_a_from_import_call_is_reported_and_an_alias_follows_the_name(self):
        with _Copy() as root:
            _write(root / "scripts" / "zz_probe_imports.py", '''
                import code_owner_hold as coh
                from code_owner_hold import park
                from code_owner_hold import park as park_card


                def by_name(card):
                    park(card, "1", "why")


                def by_alias(card):
                    park_card(card, "1", "why")


                def by_module_alias(card):
                    coh.park(card, "1", "why")


                def not_a_caller(card):
                    return card


                park("DRE-1", "1", "at import")
            ''')
            report = lane_callers.callers_of(HOLD, "park", root=str(root))
        mine = {c for c in report.callers if c.startswith("scripts/zz_probe_imports.py#")}
        self.assertEqual(mine, {
            "scripts/zz_probe_imports.py#by_name",
            "scripts/zz_probe_imports.py#by_alias",
            "scripts/zz_probe_imports.py#by_module_alias",
            "scripts/zz_probe_imports.py#<module>",
        })

    def test_a_same_named_function_of_another_module_is_not_a_caller(self):
        # `dead_run.py` has its own `park`. A bare `park(` there is dead_run's.
        report = lane_callers.callers_of(HOLD, "park", root=str(ROOT))
        self.assertFalse([c for c in report.callers if c.startswith("scripts/dead_run.py#")])

    def test_a_call_from_another_modules_main_or_main_block_is_reported(self):
        # Only the DEFINING module's `main` is dispatch, answered by the steps
        # that run its subcommands. A call from ANOTHER module's `main` or its
        # `__main__` block is a real caller, reported as `<file>#main`
        # (operator decision on PR #599, finding 1(b)).
        with _Copy() as root:
            _write(root / "scripts" / "zz_probe_main.py", '''
                import sys

                from code_owner_hold import park


                def helper(card):
                    park(card, "1", "why")


                def main(argv=None):
                    park("DRE-1", "1", "why")
                    return 0


                if __name__ == "__main__":
                    park("DRE-1", "1", "why")
                    sys.exit(main())
            ''')
            report = lane_callers.callers_of(HOLD, "park", root=str(root))
        mine = {c for c in report.callers if c.startswith("scripts/zz_probe_main.py#")}
        self.assertEqual(mine, {
            "scripts/zz_probe_main.py#helper",
            "scripts/zz_probe_main.py#main",
        })

    def test_another_modules_main_block_alone_is_reported_as_main(self):
        with _Copy() as root:
            _write(root / "scripts" / "zz_probe_main_block.py", '''
                from code_owner_hold import park

                if __name__ == "__main__":
                    park("DRE-1", "1", "why")
            ''')
            report = lane_callers.callers_of(HOLD, "park", root=str(root))
        mine = {c for c in report.callers if c.startswith("scripts/zz_probe_main_block.py#")}
        self.assertEqual(mine, {"scripts/zz_probe_main_block.py#main"})

    def test_reconciles_main_calling_the_target_is_reported_and_own_dispatch_is_not(self):
        with _Copy() as root:
            path = root / "scripts" / "reconcile.py"
            before = lane_callers.callers_of(ESCALATION, "escalate", root=str(root))
            text = path.read_text(encoding="utf-8")
            main = next(n for n in ast.parse(text).body
                        if isinstance(n, ast.FunctionDef) and n.name == "main")
            first = main.body[1] if ast.get_docstring(main) else main.body[0]
            lines = text.splitlines(keepends=True)
            indent = " " * first.col_offset
            lines.insert(first.lineno - 1,
                         f'{indent}planning_escalation.escalate(None, "DRE-1", "probe")\n')
            path.write_text("".join(lines), encoding="utf-8")
            ast.parse(path.read_text(encoding="utf-8"))
            after = lane_callers.callers_of(ESCALATION, "escalate", root=str(root))
            exits = lane_callers.callers_of(ROUTE, "_cmd_exit", root=str(root))
        self.assertNotIn("scripts/reconcile.py#main", before.callers)
        self.assertIn("scripts/reconcile.py#main", after.callers)
        # planning_route's own `main` dispatching its own `_cmd_exit` is still
        # a subcommand's dispatch: the route steps are the callers.
        self.assertNotIn("scripts/planning_route.py#main", exits.callers)
        self.assertLessEqual(EXIT_CALLERS, exits.callers)


class WorkflowSteps(unittest.TestCase):
    def test_a_fifth_escalation_step_in_plan_yml_is_reported_by_name(self):
        name = "Probe — a fifth escalation parks the card"
        with _Copy() as root:
            before = lane_callers.callers_of(ESCALATION, "escalate", root=str(root))
            _insert_step_before(
                root / ".github" / "workflows" / "plan.yml",
                "Classification refused — park the card for the CEO",
                f'''
                - name: {name}
                  run: |
                    # a comment naming scripts/planning_escalation.py escalate is not a call
                    python3 .bureau-pipeline/scripts/planning_escalation.py escalate "$CARD" \\
                      --why "probe"
                ''',
            )
            after = lane_callers.callers_of(ESCALATION, "escalate", root=str(root))
        unit = f".github/workflows/plan.yml#{name}"
        self.assertNotIn(unit, before.callers)
        self.assertIn(unit, after.callers)
        self.assertEqual(after.callers - before.callers, {unit})


class TheThreeMapForms(unittest.TestCase):
    """Each of the three forms a CLI map takes on `main`, read off a module
    written in that form alone, resolves the step that runs its subcommand."""

    MODULES = {
        "zz_form_chain.py": '''
            import argparse
            import sys


            def target(card):
                return card


            def _cmd_go(args):
                return target(args.card)


            def _cmd_other(args):
                return 0


            def main(argv=None):
                parser = argparse.ArgumentParser()
                sub = parser.add_subparsers(dest="command")
                go = sub.add_parser("go")
                go.add_argument("card")
                sub.add_parser("other")
                args = parser.parse_args(argv)
                command = args.command
                if command == "go":
                    return _cmd_go(args)
                if command == "other":
                    return _cmd_other(args)
                return 2


            if __name__ == "__main__":
                sys.exit(main())
        ''',
        "zz_form_parser.py": '''
            import argparse
            import sys


            def target(card):
                return card


            def _cmd_go(args):
                return target(args.card)


            def _cmd_other(args):
                return 0


            def main(argv=None):
                parser = argparse.ArgumentParser()
                sub = parser.add_subparsers(dest="cmd", required=True)
                p = sub.add_parser("other")
                p.set_defaults(fn=_cmd_other)
                p = sub.add_parser("go")
                p.add_argument("card")
                p.set_defaults(fn=_cmd_go)
                args = parser.parse_args(argv)
                return args.fn(args)


            if __name__ == "__main__":
                sys.exit(main())
        ''',
        "zz_form_dict.py": '''
            import sys


            def target(card):
                return card


            def other(card):
                return card


            if __name__ == "__main__":
                cmd, *args = sys.argv[1:]
                {
                    "go": target,
                    "other": other,
                }[cmd](*args)
        ''',
        "zz_form_inline.py": '''
            import argparse
            import sys


            def target(card):
                return card


            def main(argv=None):
                parser = argparse.ArgumentParser()
                sub = parser.add_subparsers(dest="command")
                go = sub.add_parser("go")
                go.add_argument("card")
                sub.add_parser("other")
                args = parser.parse_args(argv)
                command = args.command
                if command == "go":
                    target(args.card)
                    return 0
                if command == "other":
                    return 0
                return 2


            if __name__ == "__main__":
                sys.exit(main())
        ''',
    }

    MODULES["zz_form_inline_via.py"] = '''
        import argparse
        import sys


        def target(card):
            return card


        def _cmd_go(args):
            return target(args.card)


        def main(argv=None):
            parser = argparse.ArgumentParser()
            sub = parser.add_subparsers(dest="command")
            sub.add_parser("via").add_argument("card")
            args = parser.parse_args(argv)
            command = args.command
            if command == "via":
                _cmd_go(args)
                return 0
            return 2


        if __name__ == "__main__":
            sys.exit(main())
    '''
    MODULES["zz_form_inline_unknown.py"] = '''
        import argparse
        import sys


        def target(card):
            return card


        HANDLERS = {"go": target}


        def main(argv=None):
            parser = argparse.ArgumentParser()
            sub = parser.add_subparsers(dest="command")
            sub.add_parser("go").add_argument("card")
            args = parser.parse_args(argv)
            command = args.command
            if command == "go":
                HANDLERS[command](args.card)
                return 0
            return 2


        if __name__ == "__main__":
            sys.exit(main())
    '''

    WORKFLOW = '''
        name: probe
        on: workflow_dispatch
        jobs:
          probe:
            runs-on: ubuntu-latest
            steps:
              - name: Chain step
                run: python3 .bureau-pipeline/scripts/zz_form_chain.py go "$CARD"
              - name: Chain other step
                run: python3 .bureau-pipeline/scripts/zz_form_chain.py other
              - name: Chain quoted step
                run: python3 "$PIPELINE_DIR/scripts/zz_form_chain.py" go "$CARD"
              - name: Inline step
                run: python3 .bureau-pipeline/scripts/zz_form_inline.py go "$CARD"
              - name: Inline other step
                run: python3 .bureau-pipeline/scripts/zz_form_inline.py other
              - name: Inline via step
                run: python3 .bureau-pipeline/scripts/zz_form_inline_via.py via "$CARD"
              - name: Inline unknown step
                run: python3 .bureau-pipeline/scripts/zz_form_inline_unknown.py go "$CARD"
              - name: Parser step
                run: |
                  python3 "$PIPELINE_DIR"/scripts/zz_form_parser.py \\
                    go "$CARD"
              - name: Parser other step
                run: python3 scripts/zz_form_parser.py other
              - name: Dict step
                run: OUT=$(python3 scripts/zz_form_dict.py go "$CARD")
              - name: Dict other step
                run: python3 .bureau-pipeline/scripts/zz_form_dict.py other "$CARD"
    '''

    def _report(self, module: str):
        with _Copy() as root:
            for name, text in self.MODULES.items():
                _write(root / "scripts" / name, text)
            _write(root / ".github" / "workflows" / "zz-probe.yml", self.WORKFLOW)
            return lane_callers.callers_of(f"scripts/{module}", "target", root=str(root))

    def _steps(self, report):
        return {c for c in report.callers if c.startswith(".github/workflows/zz-probe.yml#")}

    def test_an_if_command_chain_is_read(self):
        # The quoted step runs `"$PIPELINE_DIR/scripts/…py" go`: the closing
        # quote belongs to the path, and `go` is still its subcommand.
        report = self._report("zz_form_chain.py")
        self.assertEqual(self._steps(report), {
            ".github/workflows/zz-probe.yml#Chain step",
            ".github/workflows/zz-probe.yml#Chain quoted step",
        })
        self.assertIn("scripts/zz_form_chain.py#_cmd_go", report.callers)
        self.assertEqual(report.unread, frozenset())

    def test_a_chain_branch_that_calls_the_function_inline_is_read(self):
        # `go` has no handler: its branch calls `target` itself. The step that
        # runs `go` is the caller; the step that runs `other` is not.
        report = self._report("zz_form_inline.py")
        self.assertEqual(self._steps(report), {".github/workflows/zz-probe.yml#Inline step"})
        self.assertNotIn("scripts/zz_form_inline.py#main", report.callers)
        self.assertEqual(report.unread, frozenset())

    def test_an_inline_branch_calling_an_in_module_caller_is_read(self):
        # `via` calls `_cmd_go`, which calls `target`, in place: the step is
        # the caller even with no `return <handler>(…)`.
        report = self._report("zz_form_inline_via.py")
        self.assertEqual(self._steps(report),
                         {".github/workflows/zz-probe.yml#Inline via step"})
        self.assertEqual(report.unread, frozenset())

    def test_an_inline_branch_that_cannot_be_resolved_is_unread(self):
        # `HANDLERS[command](…)` names no callee: whether `go` reaches the
        # target cannot be read, so the module is unread, never dropped.
        report = self._report("zz_form_inline_unknown.py")
        self.assertEqual(self._steps(report), set())
        self.assertEqual(report.unread, frozenset({"scripts/zz_form_inline_unknown.py"}))

    def test_add_parser_with_set_defaults_is_read(self):
        report = self._report("zz_form_parser.py")
        self.assertEqual(self._steps(report), {".github/workflows/zz-probe.yml#Parser step"})
        self.assertEqual(report.unread, frozenset())

    def test_a_subcommand_dict_is_read(self):
        report = self._report("zz_form_dict.py")
        self.assertEqual(self._steps(report), {".github/workflows/zz-probe.yml#Dict step"})
        self.assertEqual(report.unread, frozenset())


class AnUnknownMapForm(unittest.TestCase):
    def test_a_fourth_form_reports_the_module_unread_and_none_of_its_steps(self):
        with _Copy() as root:
            path = root / ESCALATION
            _edit(
                path,
                '    if command == "escalate":\n        return _cmd_escalate(args)\n',
                '    match command:\n        case "escalate":\n'
                '            return _cmd_escalate(args)\n',
            )
            ast.parse(path.read_text(encoding="utf-8"))
            report = lane_callers.callers_of(ESCALATION, "escalate", root=str(root))
        self.assertEqual(report.unread, frozenset({ESCALATION}))
        self.assertFalse([c for c in report.callers if c.startswith(".github/")],
                         sorted(report.callers))
        # Script callers do not go through the map, so they are still read.
        self.assertIn("scripts/planning_escalation.py#_cmd_escalate", report.callers)
        self.assertIn("scripts/planning_route.py#_cmd_exit", report.callers)

    def test_a_dispatch_with_no_known_form_is_unread_when_a_step_names_a_subcommand(self):
        with _Copy() as root:
            _write(root / "scripts" / "zz_form_unknown.py", '''
                import sys


                def cmd_go(card):
                    return card


                if __name__ == "__main__":
                    cmd, *args = sys.argv[1:]
                    globals()["cmd_" + cmd.replace("-", "_")](*args)
            ''')
            _write(root / ".github" / "workflows" / "zz-unknown.yml", '''
                name: probe
                on: workflow_dispatch
                jobs:
                  probe:
                    runs-on: ubuntu-latest
                    steps:
                      - name: Unknown step
                        run: python3 .bureau-pipeline/scripts/zz_form_unknown.py go "$CARD"
            ''')
            report = lane_callers.callers_of("scripts/zz_form_unknown.py", "cmd_go",
                                             root=str(root))
        self.assertEqual(report.unread, frozenset({"scripts/zz_form_unknown.py"}))
        self.assertFalse([c for c in report.callers if c.startswith(".github/")])

    def test_main_calling_the_function_outside_any_branch_is_unread(self):
        # The call is in `main`, so a step is its caller, but it sits in no
        # `if command == …` branch: which subcommand reaches it cannot be read.
        with _Copy() as root:
            _write(root / "scripts" / "zz_form_unplaced.py", '''
                import argparse
                import sys


                def target(card):
                    return card


                def main(argv=None):
                    parser = argparse.ArgumentParser()
                    sub = parser.add_subparsers(dest="command")
                    go = sub.add_parser("go")
                    go.add_argument("card")
                    args = parser.parse_args(argv)
                    if args.command == "go":
                        print(args.card)
                    target(args.card)
                    return 0


                if __name__ == "__main__":
                    sys.exit(main())
            ''')
            _write(root / ".github" / "workflows" / "zz-unplaced.yml", '''
                name: probe
                on: workflow_dispatch
                jobs:
                  probe:
                    runs-on: ubuntu-latest
                    steps:
                      - name: Unplaced step
                        run: python3 .bureau-pipeline/scripts/zz_form_unplaced.py go "$CARD"
            ''')
            report = lane_callers.callers_of("scripts/zz_form_unplaced.py", "target",
                                             root=str(root))
        self.assertEqual(report.unread, frozenset({"scripts/zz_form_unplaced.py"}))
        self.assertFalse([c for c in report.callers if c.startswith(".github/")])


class WhatCouldNotBeRead(unittest.TestCase):
    """A file discovery needed and could not read is reported, never passed:
    a script that does not parse hides whatever calls it makes, and a workflow
    that is not YAML hides its steps."""

    def test_a_script_that_does_not_parse_is_unread(self):
        with _Copy() as root:
            _write(root / "scripts" / "zz_broken.py", """
                from code_owner_hold import park
                def broken(:
                    park("DRE-1", "1", "why")
            """)
            report = lane_callers.callers_of(HOLD, "park", root=str(root))
        self.assertIn("scripts/zz_broken.py", report.unread)
        self.assertLessEqual(PARK_CALLERS, report.callers)

    def test_a_workflow_that_is_not_yaml_is_unread(self):
        with _Copy() as root:
            (root / ".github" / "workflows" / "zz-bad.yml").write_text(
                "jobs:\n  probe:\n    steps: [\n", encoding="utf-8")
            report = lane_callers.callers_of(HOLD, "park", root=str(root))
        self.assertIn(".github/workflows/zz-bad.yml", report.unread)
        self.assertLessEqual(PARK_CALLERS, report.callers)


class TheCommandLine(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT,
                              capture_output=True, text=True, timeout=120)

    def test_callers_prints_the_two_park_units_one_per_line(self):
        result = self._run("callers", HOLD, "park")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), sorted(PARK_CALLERS))

    def test_an_unknown_function_exits_two_naming_it(self):
        result = self._run("callers", HOLD, "no_such_function")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no_such_function", result.stderr + result.stdout)

    def test_an_unknown_module_exits_two_naming_it(self):
        result = self._run("callers", "scripts/no_such_module.py", "park")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no_such_module.py", result.stderr + result.stdout)

    def test_an_unread_module_is_printed_after_the_callers(self):
        with _Copy() as root:
            _edit(
                root / ESCALATION,
                '    if command == "escalate":\n        return _cmd_escalate(args)\n',
                '    match command:\n        case "escalate":\n'
                '            return _cmd_escalate(args)\n',
            )
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "callers", ESCALATION, "escalate"],
                cwd=root, capture_output=True, text=True, timeout=120,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[-1], f"UNREAD {ESCALATION}")
        self.assertEqual(lines[:-1], sorted(lines[:-1]))


if __name__ == "__main__":
    unittest.main()
