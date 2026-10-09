"""RED-first tests: a switch catalog, and one reader of a switch's off-reason (DRE-6434).

A pipeline switch is a repository variable a reusable workflow reads from
`vars.*` and treats as live only when it is exactly `true`. Before this card a
switch that was off carried no reason, so nothing could say when the reason was
over. `config/switches.json` is the list of switches as data, and
`scripts/switch_reason.py` is the one pure module that reads a switch's
declared off-reason and composes the line every reader prints.

WHAT THESE TESTS PIN.

  * The CATALOG is held to the tree, both ways: every `vars.<NAME>_LIVE` read
    under `.github/workflows/` is a row, and every row's reader file reads
    `vars.<name>` inside the row's step. Proved on copies of the real tree —
    one with a fourth read added, one with a row's read removed — because
    "0 problems" is a fact about the scanner until a test shows it can see.
  * The CONTRACT strings shared with the sibling cards, verbatim: the companion
    name, the five `off_line` forms, and the two `reading` forms.
  * Only the exact word `true` is on, in the shape of proof_dispatch's
    `test_only_the_exact_word_true_is_live`.
  * An unread card is never terminal, so it never clears a reason.

Run: cd bureau-pipeline && python3 -m pytest tests/test_switch_reason.py -v
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import prose_blockers  # noqa: E402
import switch_reason  # noqa: E402

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
SWITCH = "PROOF_DISPATCH_LIVE"
COMPANION = "PROOF_DISPATCH_LIVE_OFF_UNTIL"
THREE = "DRE-6141, DRE-6142, DRE-6143"

#: The switches on `main` today, each with the file and step that reads it.
ON_MAIN = {
    "PROOF_DISPATCH_LIVE": (".github/workflows/reconcile.yml", "Dispatch proof runs"),
    "GREEN_LIGHT_REPLY_LIVE": (".github/workflows/reconcile.yml",
                               "Reply to the CEO's Green Light comments"),
    "HYGIENE_LIVE": (".github/workflows/hygiene.yml", "Decide dry run"),
}


def _copy_tree(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / ".github" / "workflows", tmp_path / ".github" / "workflows")
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "config" / "switches.json", tmp_path / "config" / "switches.json")
    return tmp_path


# --------------------------------------------------------------------------- #
# the catalog                                                                  #
# --------------------------------------------------------------------------- #


class TestCatalog:
    def test_the_file_has_the_holds_json_shape(self):
        doc = json.loads((ROOT / "config" / "switches.json").read_text())
        assert doc["_readme"].strip()
        assert doc["version"] == 1
        assert doc["companion_suffix"] == "_OFF_UNTIL"
        assert doc["alarm_after_hours"] == 12

    def test_it_declares_exactly_the_three_switches_on_main(self):
        rows = switch_reason.load()["switches"]
        assert {r["name"]: (r["reader"], r["step"]) for r in rows} == ON_MAIN
        assert len(rows) == 3
        for row in rows:
            assert set(row) == {"name", "reader", "step", "means"}
            assert row["means"].strip().endswith(".")

    def test_load_reads_the_named_path(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text(json.dumps({"version": 1, "switches": []}))
        assert switch_reason.load(str(path)) == {"version": 1, "switches": []}

    def test_the_check_passes_on_main(self):
        assert switch_reason.problems() == []

    def test_the_cli_check_exits_zero_on_main(self):
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "switch_reason.py"), "check"],
            capture_output=True, text=True, cwd=ROOT)
        assert done.returncode == 0, done.stdout + done.stderr
        assert "3 switch read(s), 3 row(s), 0 problem(s)" in done.stdout

    def test_reads_are_discovered_from_the_workflows(self):
        reads = switch_reason.discover(str(ROOT))
        assert {(r.name, r.file, r.step) for r in reads} == {
            (name, reader, step) for name, (reader, step) in ON_MAIN.items()}

    def test_a_fourth_read_with_no_row_fails_naming_it(self, tmp_path):
        root = _copy_tree(tmp_path)
        workflow = root / ".github" / "workflows" / "hygiene.yml"
        text = workflow.read_text()
        anchor = "          HYGIENE_LIVE: ${{ vars.HYGIENE_LIVE }}\n"
        assert anchor in text
        workflow.write_text(text.replace(
            anchor, anchor + "          FOO_LIVE: ${{ vars.FOO_LIVE }}\n"))
        found = switch_reason.problems(root=str(root))
        assert len(found) == 1
        assert "FOO_LIVE" in found[0]
        assert "hygiene.yml" in found[0] and "Decide dry run" in found[0]
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "switch_reason.py"), "check",
             "--root", str(root)],
            capture_output=True, text=True)
        assert done.returncode == 1
        assert "FOO_LIVE" in done.stdout

    def test_a_row_whose_reader_lost_its_read_fails_naming_the_row(self, tmp_path):
        root = _copy_tree(tmp_path)
        workflow = root / ".github" / "workflows" / "reconcile.yml"
        lines = workflow.read_text().splitlines(keepends=True)
        kept = [l for l in lines if "vars.GREEN_LIGHT_REPLY_LIVE" not in l]
        assert len(kept) == len(lines) - 1
        workflow.write_text("".join(kept))
        found = switch_reason.problems(root=str(root))
        assert len(found) == 1
        assert "GREEN_LIGHT_REPLY_LIVE" in found[0]
        assert "Reply to the CEO's Green Light comments" in found[0]

    def test_a_read_moved_to_another_step_fails_naming_the_row(self, tmp_path):
        root = _copy_tree(tmp_path)
        doc = json.loads((root / "config" / "switches.json").read_text())
        for row in doc["switches"]:
            if row["name"] == "HYGIENE_LIVE":
                row["step"] = "Read the board and apply the budget floor"
        found = switch_reason.problems(doc, root=str(root))
        assert len(found) == 1
        assert "HYGIENE_LIVE" in found[0]
        assert "Read the board and apply the budget floor" in found[0]

    def test_a_commented_read_is_not_a_read(self, tmp_path):
        root = tmp_path
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / ".github" / "workflows" / "a.yml").write_text(
            "jobs:\n  j:\n    steps:\n"
            "      # BAR_LIVE: ${{ vars.BAR_LIVE }}\n"
            "      - name: Real\n"
            "        env:\n"
            "          BAZ_LIVE: ${{ vars.BAZ_LIVE }}\n")
        assert [(r.name, r.step) for r in switch_reason.discover(str(root))] == [
            ("BAZ_LIVE", "Real")]

    def test_a_non_switch_variable_is_not_a_read(self, tmp_path):
        root = tmp_path
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / ".github" / "workflows" / "a.yml").write_text(
            "jobs:\n  j:\n    steps:\n"
            "      - name: Real\n"
            "        env:\n"
            "          BUREAU_READ: ${{ vars.BUREAU_READ || 'off' }}\n"
            "          X: ${{ vars.LIVE_THING }}\n")
        assert switch_reason.discover(str(root)) == []

    def test_a_quoted_step_name_reads_unquoted(self, tmp_path):
        root = tmp_path
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / ".github" / "workflows" / "a.yml").write_text(
            "jobs:\n  j:\n    steps:\n"
            "      - name: \"Quoted step\"\n"
            "        env:\n"
            "          QUX_LIVE: ${{ vars.QUX_LIVE }}\n")
        assert [(r.name, r.step) for r in switch_reason.discover(str(root))] == [
            ("QUX_LIVE", "Quoted step")]

    def test_a_malformed_row_and_a_duplicate_row_fail_by_name(self):
        doc = switch_reason.load()
        doc["switches"].append(dict(doc["switches"][0]))
        doc["switches"].append({"name": "HALF_LIVE"})
        found = switch_reason.problems(doc, root=str(ROOT))
        assert any("PROOF_DISPATCH_LIVE" in p and "more than one row" in p for p in found)
        assert any("HALF_LIVE" in p and "reader" in p for p in found)

    def test_the_catalog_header_is_held_to_the_contract(self):
        doc = switch_reason.load()
        doc["companion_suffix"] = "_UNTIL"
        doc["version"] = 2
        found = switch_reason.problems(doc, root=str(ROOT))
        assert any("companion_suffix" in p for p in found)
        assert any("version" in p for p in found)


# --------------------------------------------------------------------------- #
# the contract                                                                 #
# --------------------------------------------------------------------------- #


class TestContract:
    def test_the_companion_name(self):
        assert switch_reason.companion(SWITCH) == COMPANION
        assert switch_reason.COMPANION_SUFFIX == "_OFF_UNTIL"

    @pytest.mark.parametrize("value", [None, "", "false", "TRUE", "yes", "1", " true"])
    def test_only_the_exact_word_true_is_on(self, value):
        env = {} if value is None else {SWITCH: value}
        assert switch_reason.is_on(SWITCH, env) is False
        line = switch_reason.off_line(SWITCH, env)
        assert line == f"{SWITCH} is off — no reason given"
        assert switch_reason.is_on(SWITCH, {SWITCH: "true"}) is True

    def test_parse_reason_keeps_the_text_and_the_ids_in_order(self):
        reason = switch_reason.parse_reason(
            "waiting on DRE-6142 then DRE-6141,DRE-6142 DRE-7 (see thread)")
        assert reason == switch_reason.Reason(
            "waiting on DRE-6142 then DRE-6141,DRE-6142 DRE-7 (see thread)",
            ("DRE-6142", "DRE-6141", "DRE-7"))

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_parse_reason_of_nothing_is_none(self, value):
        assert switch_reason.parse_reason(value) is None

    def test_parse_reason_with_no_card(self):
        assert switch_reason.parse_reason("until the CEO says so") == \
            switch_reason.Reason("until the CEO says so", ())

    def test_parse_reason_does_not_read_a_longer_word(self):
        assert switch_reason.parse_reason("XDRE-12 DRE-12a").cards == ()

    def test_off_until_three_cards(self):
        env = {COMPANION: THREE}
        assert switch_reason.off_line(SWITCH, env) == \
            f"{SWITCH} is off — until DRE-6141, DRE-6142, DRE-6143 land"

    def test_three_done_cards_clear_the_reason(self):
        env = {COMPANION: THREE}
        states = {"DRE-6141": "Done", "DRE-6142": "Done", "DRE-6143": "Done"}
        got = switch_reason.reading(SWITCH, env, states, NOW)
        assert got.cleared is True
        assert got.off is True
        assert got.cards == ("DRE-6141", "DRE-6142", "DRE-6143")
        assert got.unread == ()
        assert got.line == (
            f"{SWITCH} is off — its reason cleared: DRE-6141 Done, "
            "DRE-6142 Done, DRE-6143 Done; the switch may be turned on")

    def test_every_terminal_state_clears(self):
        env = {COMPANION: THREE}
        states = dict(zip(("DRE-6141", "DRE-6142", "DRE-6143"), prose_blockers.TERMINAL))
        assert switch_reason.reading(SWITCH, env, states, NOW).cleared is True

    def test_no_reason_given_never_says_on_and_never_clears(self):
        line = switch_reason.off_line(SWITCH, {})
        assert line == "PROOF_DISPATCH_LIVE is off — no reason given"
        assert not re.search(r"\bon\b", line)
        got = switch_reason.reading(SWITCH, {}, {}, NOW)
        assert got.cleared is False
        assert got.off is True
        assert got.line == line

    def test_on_with_a_companion_is_stale(self):
        env = {SWITCH: "true", COMPANION: THREE}
        line = f"{SWITCH} is on — its off-reason is stale, delete {COMPANION}"
        assert switch_reason.off_line(SWITCH, env) == line
        got = switch_reason.reading(SWITCH, env, {}, NOW)
        assert got.line == line
        assert got.off is False and got.cleared is False

    def test_on_with_no_companion(self):
        env = {SWITCH: "true"}
        assert switch_reason.off_line(SWITCH, env) == f"{SWITCH} is on"
        got = switch_reason.reading(SWITCH, env, {}, NOW)
        assert got.line == f"{SWITCH} is on"
        assert got.off is False and got.cleared is False

    def test_on_with_an_empty_companion_is_plain_on(self):
        assert switch_reason.off_line(SWITCH, {SWITCH: "true", COMPANION: ""}) == \
            f"{SWITCH} is on"

    def test_a_reason_that_names_no_card(self):
        env = {COMPANION: "until the CEO says so"}
        line = f"{SWITCH} is off — its reason names no card: until the CEO says so"
        assert switch_reason.off_line(SWITCH, env) == line
        got = switch_reason.reading(SWITCH, env, {}, NOW)
        assert got.line == line
        assert got.cleared is False and got.cards == ()

    def test_not_yet_cleared_names_each_state(self):
        env = {COMPANION: THREE}
        states = {"DRE-6141": "Done", "DRE-6142": "In Review"}
        got = switch_reason.reading(SWITCH, env, states, NOW)
        assert got.cleared is False
        assert got.unread == ("DRE-6143",)
        assert got.line == (
            f"{SWITCH} is off — until DRE-6141, DRE-6142, DRE-6143 land: "
            "DRE-6141 Done, DRE-6142 In Review, DRE-6143 unread")

    def test_an_unread_card_never_clears_even_when_the_rest_are_done(self):
        env = {COMPANION: THREE}
        states = {"DRE-6141": "Done", "DRE-6143": "Done"}
        got = switch_reason.reading(SWITCH, env, states, NOW)
        assert got.cleared is False
        assert got.unread == ("DRE-6142",)
        assert "DRE-6142 unread" in got.line
        assert "may be turned on" not in got.line

    def test_a_state_of_none_is_unread(self):
        env = {COMPANION: "DRE-1"}
        got = switch_reason.reading(SWITCH, env, {"DRE-1": None}, NOW)
        assert got.cleared is False and got.unread == ("DRE-1",)
        assert got.line == f"{SWITCH} is off — until DRE-1 land: DRE-1 unread"

    def test_the_reading_is_deterministic(self):
        env = {COMPANION: THREE}
        states = {"DRE-6141": "Done"}
        assert switch_reason.reading(SWITCH, env, states, NOW) == \
            switch_reason.reading(SWITCH, env, dict(states), NOW)
