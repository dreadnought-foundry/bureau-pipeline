"""No place the pipeline writes a label can write `hand-built` (DRE-6362).

`hand-built` is the CEO's mark (DRE-6225): a person builds the card and nothing
is dispatched at it, and his rule of 2026-10-07 is that nothing automatic
applies it. DRE-6228 switched the three writers that did. What is left to prove
is an ABSENCE — that the set of places the pipeline writes a label is still the
set somebody checked — and an absence cannot be confirmed by listing writers,
because the writer nobody remembered is exactly the one still open.

So `scripts/label_writers.py` DISCOVERS them, in the shape of
`ready_lane_writers.py`: it reads the label seam out of the write layer, finds
every write through it — Python call sites, the write layer's command line in
the workflows and scripts, the vocabularies' `marks` — resolves each label
statically, and fails on any that can be `hand-built` and on any it cannot
read. Every test below that proves the check works does it by ADDING a writer
where the check looks and watching it be named.

## WHAT THE MECHANISM CANNOT SEE

* **A hand write in Linear.** The CEO applies his own mark by hand; nothing in
  this repository can see that, and nothing should stop it.
* **A label computed from data at run time.** Resolution is static. A label no
  rule can read is REPORTED as UNREAD rather than passed —
  `test_a_label_it_cannot_read_is_reported_unread` is that guarantee.
"""

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import label_writers as lw  # noqa: E402
import routing_verdict  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "label_writers.py"


class _Staged:
    """A file that exists in the real tree for the length of one test — the
    new writer is added where the check actually looks, not in a copy of the
    tree the check might read differently."""

    def __init__(self, relpath: str, text: str):
        self.path = ROOT / relpath
        self.text = text

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.text, encoding="utf-8")
        return self.path

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)
        return False


def _check() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "check"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )


def _lines_naming(output: str, needle: str) -> list:
    return [line for line in output.splitlines() if needle in line]


def _named(problems, needle: str) -> bool:
    return any(needle in p for p in problems)


class TheRepositoryIsClean(unittest.TestCase):
    def test_the_check_exits_0_against_this_repository(self):
        result = _check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_write_here_can_produce_the_mark_and_none_is_unread(self):
        found = lw.problems()
        # Each problem opens with the file:line it was found at, so a failure
        # here is actionable without opening this test.
        self.assertEqual(found, [], "\n".join(found))


class TheDiscoveryActuallyFindsSomething(unittest.TestCase):
    """Guards the guard: every assertion above passes trivially over an empty
    sweep, which is the failure mode a discovery check dies of."""

    def test_the_sweep_finds_writes_of_every_kind(self):
        kinds = {w.how for w in lw.writes()}
        for kind in ("python", "workflow", "config"):
            self.assertIn(kind, kinds)

    def test_the_writes_it_finds_include_the_ones_we_know_are_there(self):
        labels = {label for w in lw.writes() for label in w.labels}
        for expected in (
            "needs-human",     # the hold, through dead_run's injected writer
            "epic-queued",     # plan.yml's add-label on a queued epic
            "groom-queued",    # the groomer's queued card
            "operator-step",   # the vocabulary's OPERATOR mark, and Triage's
            "agent:devops",    # model-adoption.yml's create --label
            "break-glass:used",
        ):
            self.assertIn(expected, labels)

    def test_the_seam_is_read_out_of_the_write_layer(self):
        seam = lw.seam_functions()
        for name in ("add_label", "_create_card", "create_card", "_team_label_ids"):
            self.assertIn(name, seam)
        # remove_label touches `labelIds` too, but no label NAME reaches a
        # lookup through it — it is not a way to put a label on.
        self.assertNotIn("remove_label", lw.label_parameters())

    def test_the_label_arguments_of_each_seam_function_are_derived(self):
        params = lw.label_parameters()
        self.assertIn("label_name", params["add_label"].names)
        self.assertIn("labels", params["create_card"].names)
        for flagged in ("cmd_subissue", "cmd_oneoff", "cmd_create"):
            self.assertTrue(params[flagged].flags, flagged)


class TheMarkIsReadNeverDeclared(unittest.TestCase):
    def test_the_check_reads_the_mark_from_the_routing_api(self):
        # Re-point the mark and the check follows it: the hold's writes become
        # the findings. A check that spelled the mark itself would not move.
        with mock.patch.object(routing_verdict, "HAND_BUILT_LABEL", "needs-human"):
            found = lw.problems()
        self.assertTrue(_named(found, "needs-human"), found)

    def test_the_module_declares_no_label_string(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn('"hand-built"', source)
        self.assertNotIn("'hand-built'", source)

    def test_the_check_lists_no_writer_by_name(self):
        # Discovered, never listed: the only file under scripts/ the check may
        # name is the write layer it reads the seam out of.
        source = SCRIPT.read_text(encoding="utf-8")
        named = set(re.findall(r"scripts/[\w./-]+\.(?:py|sh)\b", source))
        self.assertLessEqual(named, {"scripts/linear_ops.py"}, named)

    def test_the_docstring_says_what_it_cannot_see(self):
        doc = lw.__doc__ or ""
        self.assertIn("CANNOT SEE", doc)
        self.assertIn("hand write in Linear", doc)
        self.assertIn("run time", doc)


class ANewWriterOfTheMarkIsNamed(unittest.TestCase):
    """The card's three cases, each run through the command line: a throwaway
    module added where the check looks turns the exit code to 1 and is named."""

    def _assert_named(self, rogue: str, source: str, needle: str):
        with _Staged(f"scripts/{rogue}", source):
            result = _check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        lines = _lines_naming(result.stdout, rogue)
        self.assertTrue(lines, result.stdout)
        self.assertTrue(any(needle in line for line in lines), lines)

    def test_a_literal_mark_is_named(self):
        self._assert_named(
            "zz_label_probe_literal.py",
            "import linear_ops\n\n"
            "def mark(x):\n"
            "    linear_ops.add_label(x, \"hand-built\")\n",
            "[FAIL]",
        )

    def test_the_mark_through_a_module_constant_is_named(self):
        self._assert_named(
            "zz_label_probe_constant.py",
            "import linear_ops\n"
            "import reconcile\n\n"
            "def mark(x):\n"
            "    linear_ops.add_label(x, reconcile.HAND_BUILT_LABEL)\n",
            "[FAIL]",
        )

    def test_a_label_it_cannot_read_is_reported_unread(self):
        self._assert_named(
            "zz_label_probe_unread.py",
            "import os\n"
            "import linear_ops\n\n"
            "def mark(x):\n"
            "    linear_ops.add_label(x, os.environ['WHICH_LABEL'])\n",
            "[UNREAD]",
        )


class EveryDoorIsWatched(unittest.TestCase):
    """The other ways a label reaches the seam, each pinned the same way."""

    def _problems_with(self, relpath: str, source: str) -> list:
        with _Staged(relpath, source):
            return lw.problems()

    def test_a_card_created_carrying_the_mark_is_named(self):
        rogue = "zz_label_probe_create.py"
        found = self._problems_with(
            f"scripts/{rogue}",
            "import linear_ops\n"
            "import routing_verdict\n\n"
            "def file(title, body):\n"
            "    linear_ops.create_card(title, body, repo_slug='bureau-pipeline',\n"
            "                           labels=(routing_verdict.HAND_BUILT_LABEL,))\n",
        )
        self.assertTrue(_named(found, rogue), found)

    def test_a_one_off_flagged_with_the_mark_is_named(self):
        rogue = "zz_label_probe_oneoff.py"
        found = self._problems_with(
            f"scripts/{rogue}",
            "import linear_ops\n\n"
            "def file(title, path):\n"
            "    linear_ops.cmd_oneoff(title, path, '--label', 'repo:x',\n"
            "                          '--label', 'hand-built')\n",
        )
        self.assertTrue(_named(found, rogue), found)

    def test_a_planned_hygiene_write_of_the_mark_is_named(self):
        rogue = "zz_label_probe_hygiene.py"
        found = self._problems_with(
            f"scripts/{rogue}",
            "import hygiene\n"
            "import routing_verdict\n\n"
            "def plan(card):\n"
            "    return [hygiene.linear_label(card, routing_verdict.HAND_BUILT_LABEL, True)]\n",
        )
        self.assertTrue(_named(found, rogue), found)

    def test_a_planned_hygiene_removal_is_not_a_write(self):
        rogue = "zz_label_probe_hygiene_off.py"
        found = self._problems_with(
            f"scripts/{rogue}",
            "import hygiene\n\n"
            "def plan(card):\n"
            "    return [hygiene.linear_label(card, 'hand-built', add=False)]\n",
        )
        self.assertFalse(_named(found, rogue), found)

    def test_the_mark_handed_through_a_helper_is_named_at_the_caller(self):
        # A helper that forwards its argument is part of the door: the write
        # is wherever the helper is called, and that is the line named.
        rogue = "zz_label_probe_helper.py"
        found = self._problems_with(
            f"scripts/{rogue}",
            "import linear_ops\n\n"
            "def tag(card, label):\n"
            "    linear_ops.add_label(card, label)\n\n"
            "def go(card):\n"
            "    tag(card, 'hand-built')\n",
        )
        self.assertTrue(_named(found, f"handed in at scripts/{rogue}:7"), found)

    def test_the_seam_imported_under_another_name_is_still_the_seam(self):
        rogue = "zz_label_probe_alias.py"
        found = self._problems_with(
            f"scripts/{rogue}",
            "from linear_ops import add_label as tag\n\n"
            "def go(card):\n"
            "    tag(card, 'hand-built')\n",
        )
        self.assertTrue(_named(found, f"scripts/{rogue}:4"), found)

    def test_the_seam_reached_by_getattr_is_reported_unread(self):
        rogue = "zz_label_probe_getattr.py"
        found = self._problems_with(
            f"scripts/{rogue}",
            "import linear_ops\n\n"
            "def go(card, which):\n"
            "    getattr(linear_ops, which)(card, 'x')\n",
        )
        self.assertTrue(_named(found, f"scripts/{rogue}:4"), found)

    def test_python_running_the_write_layers_command_line_is_named(self):
        rogue = "zz_label_probe_argv.py"
        found = self._problems_with(
            f"scripts/{rogue}",
            "import os\n"
            "import subprocess\n"
            "import sys\n\n"
            "_OPS = os.path.join(os.path.dirname(__file__), 'linear_ops.py')\n\n"
            "def go(card):\n"
            "    subprocess.run([sys.executable, _OPS, 'add-label', card, 'hand-built'])\n",
        )
        self.assertTrue(_named(found, f"scripts/{rogue}:8"), found)

    def test_a_helper_whose_label_is_safe_everywhere_passes(self):
        rogue = "zz_label_probe_helper_ok.py"
        found = self._problems_with(
            f"scripts/{rogue}",
            "import linear_ops\n\n"
            "QUEUED = 'probe-queued'\n\n"
            "def tag(card, label):\n"
            "    linear_ops.add_label(card, label)\n\n"
            "def go(card):\n"
            "    for label in (QUEUED, f'repo:{card}'):\n"
            "        tag(card, label)\n",
        )
        self.assertFalse(_named(found, rogue), found)

    def test_a_shell_script_that_adds_the_mark_is_named(self):
        rogue = "zz_label_probe.sh"
        found = self._problems_with(
            f"scripts/{rogue}",
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "python3 scripts/linear_ops.py add-label \"$CARD\" hand-built\n",
        )
        self.assertTrue(_named(found, rogue), found)

    def test_a_workflow_that_creates_a_card_with_the_mark_is_named(self):
        rogue = "zz-label-probe.yml"
        found = self._problems_with(
            f".github/workflows/{rogue}",
            "name: Probe\n"
            "on: workflow_dispatch\n"
            "jobs:\n"
            "  go:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: File\n"
            "        run: |\n"
            "          python3 scripts/linear_ops.py create \"$T\" body.md \\\n"
            "            --repo bureau-pipeline --label hand-built\n",
        )
        self.assertTrue(_named(found, rogue), found)

    def test_a_vocabulary_mark_of_hand_built_is_named(self):
        doc = {"verdicts": [{"name": "WORKBENCH", "marks": ["hand-built"]},
                            {"name": "FLEET", "marks": []}]}
        found = lw.config_problems({"config/routing-verdicts.json": doc})
        self.assertTrue(_named(found, "config/routing-verdicts.json"), found)
        self.assertTrue(_named(found, "WORKBENCH"), found)

    def test_the_real_vocabularies_are_read(self):
        writes = [w for w in lw.writes() if w.how == "config"]
        self.assertTrue(any("operator-step" in w.labels for w in writes), writes)
        self.assertTrue(any("agent:planner" in w.labels for w in writes), writes)


class TheSeamIsTheOnlyWayToWriteALabel(unittest.TestCase):
    def test_nothing_outside_the_write_layer_builds_its_own_label_mutation(self):
        self.assertEqual(lw.seam_problems(), [])

    def test_a_module_that_builds_its_own_label_mutation_is_named(self):
        rogue = "zz_label_probe_mutation.py"
        source = (
            "MUT = '''mutation($id: String!, $input: IssueUpdateInput!) {\n"
            "  issueUpdate(id: $id, input: $input) { success } }'''\n"
            "def go(gql, issue_id, ids):\n"
            "    gql(MUT, {'id': issue_id, 'input': {'labelIds': ids}})\n"
        )
        with _Staged(f"scripts/{rogue}", source):
            found = lw.seam_problems()
            whole = lw.problems()
        self.assertTrue(_named(found, rogue), found)
        self.assertTrue(_named(whole, rogue), whole)


class TheMarkIsSpelledOnce(unittest.TestCase):
    def test_the_literal_lives_only_at_its_definition_and_the_board_fixture(self):
        # `grep -rn '"hand-built"' scripts/`, as a test.
        hits = []
        for path in sorted((ROOT / "scripts").rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if '"hand-built"' in line:
                    hits.append((path.relative_to(ROOT).as_posix(), number, line.strip()))
        allowed = [
            h for h in hits
            if (h[0] == "scripts/routing_verdict.py"
                and h[2].startswith("HAND_BUILT_LABEL = "))
            or h[0] == "scripts/board_snapshot.py"
        ]
        self.assertEqual(hits, allowed, hits)
        self.assertEqual(len([h for h in hits if h[0] == "scripts/routing_verdict.py"]), 1)


if __name__ == "__main__":
    unittest.main()
