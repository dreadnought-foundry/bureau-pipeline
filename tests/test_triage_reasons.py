"""Every Triage write states its reason, or the registry says why not (DRE-7525).

`scripts/triage_reasons.py check` holds `config/triage-reasons.json` to the
tree the way `scripts/hold.py check` holds `config/holds.json` to it: every
write into `Triage` that `ready_lane_writers.writes()` discovers, located at
its unit by `green_light_rows.unit_of()`, has exactly one row — in `reasons`,
naming the unit whose text carries the reason and an anchor from that text,
or in `unreasoned`, with a `why`.

Discovery is not re-proved here: `tests/test_no_unplanned_ready_lane_writer.py`,
`tests/test_green_light_rows.py` and `tests/test_lane_callers.py` prove it.
What this file proves is the reconciliation:

* the repository itself passes, offline, the way
  `tests/test_answer_into_card_wiring.py` runs `green_light_rows.py check`;
* the registry names exactly the units the discovery finds, and the script
  names none of them itself;
* on THROWAWAY COPIES of the repository (scripts, workflows, config), or with
  the registry edited in memory, each rule is broken once and watched failing
  with the unit named.

Run: cd bureau-pipeline && python3 -m pytest tests/test_triage_reasons.py -v
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
# `reconcile` reads these at import, and `ready_lane_writers` imports it to read
# its published `destinations()`.
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import triage_reasons as tr  # noqa: E402

REGISTRY = ROOT / "config" / "triage-reasons.json"

#: The sweep's no-route watchdog: a registry row whose reason is a phrase of
#: the receipt the unit itself posts, used where a rule needs a real row.
STRANDED = "reconcile.py#flag_stranded"
STRANDED_ANCHOR = "isn't on the dispatch"
STRANDED_MOVE = 'linear_ops.cmd_advance(ident, "Triage", "Todo,In Progress")'


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


def _registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def _copy_repo(tmp_path: Path) -> Path:
    """A throwaway copy of what the check reads: scripts, workflows, config."""
    root = tmp_path / "repo"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for part in ("scripts", ".github", "config"):
        shutil.copytree(ROOT / part, root / part, ignore=ignore)
    return root


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {path}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def _named(problems, *needles) -> list:
    return [p for p in problems if all(n in p for n in needles)]


def _row(doc: dict, unit: str) -> dict:
    for row in doc["reasons"]:
        if row["unit"] == unit:
            return row
    raise AssertionError(f"no reasons row for {unit}")


#: A throwaway module with one Triage write, a caller of it that posts a
#: reason, and a function that posts the same reason without calling it.
ZZ_MODULE = (
    "import linear_ops\n\n\n"
    "def park_it(card):\n"
    "    linear_ops.cmd_state(card, \"Triage\")\n\n\n"
    "def explain_and_park(card):\n"
    "    linear_ops.cmd_comment(card, \"ZZ the card is broken\")\n"
    "    park_it(card)\n\n\n"
    "def explain_only(card):\n"
    "    linear_ops.cmd_comment(card, \"ZZ the card is broken\")\n"
)
ZZ_UNIT = "zz_triage_writer.py#park_it"


def _with_zz(tmp_path: Path) -> Path:
    root = _copy_repo(tmp_path)
    (root / "scripts" / "zz_triage_writer.py").write_text(ZZ_MODULE, encoding="utf-8")
    return root


def _zz_row(reason_at: str) -> dict:
    return {"unit": ZZ_UNIT, "reason_at": reason_at,
            "anchor": "ZZ the card is broken", "note": "a throwaway reason"}


# --------------------------------------------------------------------------- #
# the repository passes                                                        #
# --------------------------------------------------------------------------- #


class TestTheRepositoryPasses:
    def test_the_check_finds_no_problem(self):
        assert tr.problems() == []

    def test_the_cli_exits_zero_offline(self):
        # Offline, the way tests/test_answer_into_card_wiring.py runs
        # green_light_rows.py check: no Linear key, the team default declared.
        with tempfile.TemporaryDirectory() as tmp:
            workspace = os.path.join(tmp, "linear-workspace.json")
            with open(workspace, "w") as f:
                json.dump({"team": {"key": "DRE", "defaultIssueState": "Planning"}}, f)
            env = {k: v for k, v in os.environ.items()
                   if k not in ("LINEAR_API_KEY", "REPO")}
            env["LINEAR_WORKSPACE_CONFIG"] = workspace
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "triage_reasons.py"), "check"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=300,
            )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "0 problem(s)" in proc.stdout, proc.stdout

    def test_the_registry_names_each_discovered_unit_exactly_once(self):
        doc = _registry()
        named = [r["unit"] for r in doc["reasons"]] + [r["unit"] for r in doc["unreasoned"]]
        discovered = {unit for _, unit in tr.triage_writes()}
        assert discovered, "the discovery found no Triage write at all"
        assert sorted(named) == sorted(discovered)

    def test_the_registry_follows_holds_json_s_shape(self):
        doc = _registry()
        assert doc["_readme"].strip()
        assert doc["version"] == 1
        for row in doc["reasons"]:
            assert set(row) == {"unit", "reason_at", "anchor", "note"}, row
            assert all(str(v).strip() for v in row.values()), row
        for row in doc["unreasoned"]:
            assert set(row) == {"unit", "why"}, row

    def test_the_script_keeps_no_list_of_sites(self):
        source = (SCRIPTS / "triage_reasons.py").read_text(encoding="utf-8")
        doc = _registry()
        for row in doc["reasons"] + doc["unreasoned"]:
            function = row["unit"].partition("#")[2]
            assert row["unit"] not in source, row["unit"]
            assert function not in source, function

    def test_the_script_reads_the_discovery_it_is_built_on(self):
        source = (SCRIPTS / "triage_reasons.py").read_text(encoding="utf-8")
        for seam in ("ready_lane_writers.writes(", "green_light_rows.unit_of(",
                     "lane_callers.callers_of(", "hold.row_matches("):
            assert seam in source, seam


# --------------------------------------------------------------------------- #
# each rule, broken once                                                       #
# --------------------------------------------------------------------------- #


class TestANewTriageWriteWithNoRow:
    def test_a_script_write_is_named_by_file_and_function(self, tmp_path):
        root = _with_zz(tmp_path)
        found = tr.problems(root=str(root))
        assert _named(found, ZZ_UNIT, "no row"), found

    def test_a_workflow_write_is_named_by_file_and_step(self, tmp_path):
        root = _copy_repo(tmp_path)
        (root / ".github" / "workflows" / "zz-new.yml").write_text(
            "name: throwaway\n"
            "on: workflow_dispatch\n"
            "jobs:\n"
            "  go:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Park it quietly\n"
            "        run: |\n"
            "          python3 .bureau-pipeline/scripts/linear_ops.py state \"$CARD\" \"Triage\"\n",
            encoding="utf-8",
        )
        found = tr.problems(root=str(root))
        assert _named(found, "zz-new.yml#Park it quietly", "no row"), found

    def test_the_same_write_with_a_row_passes(self, tmp_path):
        root = _with_zz(tmp_path)
        doc = _registry()
        doc["reasons"].append(_zz_row(ZZ_UNIT.replace("park_it", "explain_and_park")))
        assert tr.problems(doc, root=str(root)) == []


class TestAnAnchorRemovedFromItsReasoningUnit:
    def test_the_row_fails_naming_the_unit_and_the_anchor(self, tmp_path):
        root = _copy_repo(tmp_path)
        assert _row(_registry(), STRANDED)["anchor"] == STRANDED_ANCHOR
        _edit(root / "scripts" / "reconcile.py", STRANDED_ANCHOR, "is not on the dispatch")
        found = tr.problems(root=str(root))
        assert _named(found, STRANDED, repr(STRANDED_ANCHOR), "is not in the text"), found

    def test_an_anchor_that_is_the_lane_write_line_fails(self):
        doc = _registry()
        _row(doc, STRANDED)["anchor"] = STRANDED_MOVE
        found = tr.problems(doc)
        assert _named(found, STRANDED, "lane-write line"), found

    def test_an_anchor_found_elsewhere_in_the_file_is_not_found(self, tmp_path):
        # The unit's text is its own function, never the whole file.
        root = _with_zz(tmp_path)
        doc = _registry()
        doc["reasons"].append(_zz_row(ZZ_UNIT))
        found = tr.problems(doc, root=str(root))
        assert _named(found, ZZ_UNIT, "is not in the text"), found


class TestARowWhoseUnitNoLongerWritesTriage:
    def test_a_site_moved_to_another_lane_fails_by_name(self, tmp_path):
        root = _copy_repo(tmp_path)
        _edit(root / "scripts" / "reconcile.py", STRANDED_MOVE,
              STRANDED_MOVE.replace('"Triage"', '"Planning"'))
        found = tr.problems(root=str(root))
        assert _named(found, STRANDED, "no Triage write"), found

    def test_an_unreasoned_row_for_a_unit_that_writes_nothing_fails_by_name(self):
        doc = _registry()
        doc["unreasoned"].append({"unit": "reconcile.py#zz_gone", "why": "a throwaway"})
        found = tr.problems(doc)
        assert _named(found, "reconcile.py#zz_gone", "no Triage write"), found


class TestAReasonAtThatIsNotACaller:
    def test_a_function_that_does_not_call_the_unit_fails_naming_both(self, tmp_path):
        root = _with_zz(tmp_path)
        doc = _registry()
        other = ZZ_UNIT.replace("park_it", "explain_only")
        doc["reasons"].append(_zz_row(other))
        found = tr.problems(doc, root=str(root))
        assert _named(found, ZZ_UNIT, other, "neither the unit nor a discovered caller"), found
        # The anchor IS in that function: the caller rule alone refused it.
        assert not _named(found, "is not in the text"), found

    def test_a_workflow_unit_has_no_caller_but_itself(self):
        doc = _registry()
        steps = [r for r in doc["reasons"] if r["unit"].partition("#")[0].endswith(".yml")]
        row, other = steps[0], steps[1]["unit"]
        row["reason_at"] = other
        found = tr.problems(doc)
        assert _named(found, row["unit"], other, "neither the unit nor a discovered caller"), found


class TestTheUnreasonedBlock:
    def test_an_unreasoned_row_with_an_empty_why_fails_by_name(self):
        doc = _registry()
        row = doc["reasons"].pop(0)
        doc["unreasoned"].append({"unit": row["unit"], "why": "  "})
        found = tr.problems(doc)
        assert _named(found, row["unit"], "no `why`"), found

    def test_the_same_row_with_a_why_passes(self):
        doc = _registry()
        row = doc["reasons"].pop(0)
        doc["unreasoned"].append({"unit": row["unit"], "why": "posts nothing today"})
        assert tr.problems(doc) == []

    def test_a_unit_in_both_blocks_fails_by_name(self):
        doc = _registry()
        unit = doc["reasons"][0]["unit"]
        doc["unreasoned"].append({"unit": unit, "why": "posts nothing today"})
        found = tr.problems(doc)
        assert _named(found, unit, "both"), found

    def test_a_unit_named_twice_in_one_block_fails_by_name(self):
        doc = _registry()
        doc["reasons"].append(copy.deepcopy(doc["reasons"][0]))
        found = tr.problems(doc)
        assert _named(found, doc["reasons"][0]["unit"], "2 times"), found

    def test_a_reasons_row_missing_a_field_fails(self):
        doc = _registry()
        doc["reasons"][0]["note"] = ""
        found = tr.problems(doc)
        assert _named(found, doc["reasons"][0]["unit"], "`note`"), found


class TestAnUnreadDestination:
    def test_a_write_whose_lane_cannot_be_read_fails_naming_the_site(self, tmp_path):
        root = _copy_repo(tmp_path)
        (root / "scripts" / "zz_unread.py").write_text(
            "import os\n\nimport linear_ops\n\n\n"
            "def park_somewhere(card):\n"
            "    linear_ops.cmd_state(card, os.environ[\"ZZ_LANE\"])\n",
            encoding="utf-8",
        )
        found = tr.problems(root=str(root))
        assert _named(found, "UNREAD", "scripts/zz_unread.py:7"), found
