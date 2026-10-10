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
  * The SWEEP'S READ (DRE-6436): `main()` prints one `switches:` line per
    catalog row on every full pass, reads each switch's cards in ONE request
    and none when the switch is on or gives no card, and a failed read leaves
    every card `unread`. The `Read the switches` step is the sweep job's last,
    plumbs every row's variable and companion, and lifts its lines and spend
    into the step summary. `docs/switches.md` is the page a person reads.
  * The RECEIPT and the ALARM (DRE-6437): a cleared reason posts one
    `🔀 switch-cleared` receipt per switch per repo on the first named card,
    composed by `pipeline_act.receipt`; a receipt twelve hours old with the
    switch still off files one deduplicated alarm card. Nothing is read when
    the reason has not cleared, and a dry pass writes nothing.

Run: cd bureau-pipeline && python3 -m pytest tests/test_switch_reason.py -v
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import pipeline_act  # noqa: E402
import prose_blockers  # noqa: E402
import switch_reason  # noqa: E402

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
SWITCH = "PROOF_DISPATCH_LIVE"
COMPANION = "PROOF_DISPATCH_LIVE_OFF_UNTIL"
THREE = "DRE-6141, DRE-6142, DRE-6143"
READ_STEP = "Read the switches"

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
        assert "6 switch read(s), 3 row(s), 0 problem(s)" in done.stdout

    def test_reads_are_discovered_from_the_workflows(self):
        # Each switch is read where it acts, and once more by the sweep's
        # `Read the switches` step, which reports it (DRE-6436).
        reads = switch_reason.discover(str(ROOT))
        assert {(r.name, r.file, r.step) for r in reads} == {
            (name, reader, step) for name, (reader, step) in ON_MAIN.items()} | {
            (name, ".github/workflows/reconcile.yml", READ_STEP) for name in ON_MAIN}

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
        # The first read is the reader's own; `Read the switches`, the job's
        # last step, reads it again and is not the row's step.
        read = "GREEN_LIGHT_REPLY_LIVE: ${{ vars.GREEN_LIGHT_REPLY_LIVE }}"
        first = next(i for i, l in enumerate(lines) if read in l)
        kept = lines[:first] + lines[first + 1:]
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


# --------------------------------------------------------------------------- #
# the sweep's read of every switch (DRE-6436)                                  #
# --------------------------------------------------------------------------- #

WORKFLOW = ROOT / ".github" / "workflows" / "reconcile.yml"
SWEEP_KEYS = ("LINEAR_API_KEY", "GH_TOKEN", "GH_READ_TOKEN", "REPO",
              "BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
              "BUREAU_PIPELINE_REF")
NOT_YET = (f"switches: {SWITCH} is off — until DRE-6141, DRE-6142, DRE-6143 land: "
           "DRE-6141 Done, DRE-6142 Done, DRE-6143 In Review")


class FakeGql:
    """A `linear_ops.gql` stand-in: answers from `states`, records every call."""

    def __init__(self, states=None, error=None):
        self.states = states or {}
        self.error = error
        self.calls = []

    def __call__(self, query, variables=None):
        self.calls.append((query, variables or {}))
        if self.error is not None:
            raise self.error
        numbers = set((variables or {}).get("numbers") or ())
        return {"issues": {"nodes": [
            {"identifier": ident, "state": {"name": state}}
            for ident, state in self.states.items()
            if int(ident.split("-")[1]) in numbers]}}


def _main(capsys, env, gql=None):
    gql = gql if gql is not None else FakeGql()
    code = switch_reason.main([], env=env, gql=gql, now=NOW)
    out = capsys.readouterr().out
    return code, out.splitlines(), gql


def _switch_lines(lines):
    return [line for line in lines if line.startswith("switches: ")]


class TestReadStates:
    def test_one_request_for_every_card_filtered_on_their_numbers(self):
        gql = FakeGql({"DRE-6141": "Done", "DRE-6142": "In Review"})
        got = switch_reason.read_states(("DRE-6141", "DRE-6142", "DRE-6143"), gql=gql)
        assert got == {"DRE-6141": "Done", "DRE-6142": "In Review"}
        assert len(gql.calls) == 1
        query, variables = gql.calls[0]
        assert sorted(variables["numbers"]) == [6141, 6142, 6143]
        assert "number: {in: $numbers}" in query
        assert 'team: {key: {eq: "DRE"}}' in query

    def test_no_cards_is_no_request(self):
        gql = FakeGql()
        assert switch_reason.read_states((), gql=gql) == {}
        assert gql.calls == []

    def test_a_card_from_another_answer_is_not_kept(self):
        def gql(query, variables=None):
            return {"issues": {"nodes": [
                {"identifier": "DRE-1", "state": {"name": "Done"}},
                {"identifier": "DRE-2", "state": {"name": "Done"}}]}}
        assert switch_reason.read_states(("DRE-1",), gql=gql) == {"DRE-1": "Done"}

    def test_a_refusal_raises(self):
        gql = FakeGql(error=RuntimeError("Linear refused"))
        with pytest.raises(RuntimeError, match="Linear refused"):
            switch_reason.read_states(("DRE-1",), gql=gql)


class TestMain:
    def test_three_cards_not_yet_landed_is_one_request(self, capsys):
        gql = FakeGql({"DRE-6141": "Done", "DRE-6142": "Done", "DRE-6143": "In Review"})
        code, lines, gql = _main(capsys, {COMPANION: THREE}, gql)
        assert code == 0
        assert NOT_YET in lines
        assert len(gql.calls) == 1

    def test_three_done_cards_print_the_cleared_line(self, capsys):
        gql = FakeGql({"DRE-6141": "Done", "DRE-6142": "Done", "DRE-6143": "Done"})
        _, lines, _ = _main(capsys, {COMPANION: THREE}, gql)
        assert (f"switches: {SWITCH} is off — its reason cleared: DRE-6141 Done, "
                "DRE-6142 Done, DRE-6143 Done; the switch may be turned on") in lines

    def test_no_companion_is_no_reason_given_and_no_request(self, capsys):
        code, lines, gql = _main(capsys, {})
        assert code == 0
        assert f"switches: {SWITCH} is off — no reason given" in lines
        assert gql.calls == []

    def test_an_empty_companion_is_no_reason_given(self, capsys):
        _, lines, gql = _main(capsys, {SWITCH: "", COMPANION: ""})
        assert f"switches: {SWITCH} is off — no reason given" in lines
        assert gql.calls == []

    def test_an_on_switch_is_on_and_no_request(self, capsys):
        _, lines, gql = _main(capsys, {SWITCH: "true"})
        assert f"switches: {SWITCH} is on" in lines
        assert gql.calls == []

    def test_an_on_switch_with_a_stale_reason_reads_nothing(self, capsys):
        _, lines, gql = _main(capsys, {SWITCH: "true", COMPANION: THREE})
        assert (f"switches: {SWITCH} is on — its off-reason is stale, "
                f"delete {COMPANION}") in lines
        assert gql.calls == []

    def test_a_reason_naming_no_card_reads_nothing(self, capsys):
        _, lines, gql = _main(capsys, {COMPANION: "until the CEO says so"})
        assert (f"switches: {SWITCH} is off — its reason names no card: "
                "until the CEO says so") in lines
        assert gql.calls == []

    def test_a_failed_read_leaves_every_card_unread_and_never_clears(self, capsys):
        gql = FakeGql(error=RuntimeError("Linear refused the read: RATELIMITED"))
        code, lines, gql = _main(capsys, {COMPANION: THREE}, gql)
        assert code == 0
        line = next(l for l in lines if l.startswith(f"switches: {SWITCH} "))
        assert line.startswith(
            f"switches: {SWITCH} is off — until DRE-6141, DRE-6142, DRE-6143 land: "
            "DRE-6141 unread, DRE-6142 unread, DRE-6143 unread")
        assert "Linear refused the read: RATELIMITED" in line
        assert not any("cleared" in l for l in lines)
        assert lines[-1].startswith("linear-budget:")

    def test_a_failed_read_spanning_lines_stays_on_one_line(self, capsys):
        gql = FakeGql(error=RuntimeError("first\nsecond"))
        _, lines, _ = _main(capsys, {COMPANION: THREE}, gql)
        line = next(l for l in lines if l.startswith(f"switches: {SWITCH} "))
        assert "first second" in line

    def test_every_catalog_switch_is_printed_once_in_order(self, capsys):
        _, lines, _ = _main(capsys, {})
        names = [row["name"] for row in switch_reason.load()["switches"]]
        assert _switch_lines(lines) == [
            f"switches: {name} is off — no reason given" for name in names]

    def test_one_request_per_switch_however_many_cards(self, capsys):
        env = {COMPANION: THREE, "HYGIENE_LIVE_OFF_UNTIL": "DRE-1, DRE-2"}
        gql = FakeGql({"DRE-6141": "Done", "DRE-1": "Todo"})
        _, lines, gql = _main(capsys, env, gql)
        assert len(gql.calls) == 2
        assert ("switches: HYGIENE_LIVE is off — until DRE-1, DRE-2 land: "
                "DRE-1 Todo, DRE-2 unread") in lines
        assert len(_switch_lines(lines)) == 3

    def test_the_budget_trailer_is_last(self, capsys):
        _, lines, _ = _main(capsys, {COMPANION: THREE},
                            FakeGql({"DRE-6141": "Done"}))
        assert lines[-1].startswith("linear-budget:")
        assert sum(l.startswith("linear-budget:") for l in lines) == 1

    def test_the_cli_with_no_command_reads_the_switches(self):
        env = {k: v for k, v in os.environ.items()
               if not k.endswith(("_LIVE", "_OFF_UNTIL"))}
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "switch_reason.py")],
            capture_output=True, text=True, cwd=ROOT, env=env)
        assert done.returncode == 0, done.stdout + done.stderr
        lines = done.stdout.splitlines()
        assert len(_switch_lines(lines)) == 3
        assert lines[-1].startswith("linear-budget:")


# --------------------------------------------------------------------------- #
# the step in reconcile.yml                                                    #
# --------------------------------------------------------------------------- #


def _steps() -> list:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"]["sweep"]["steps"]


def _read_step() -> dict:
    found = [s for s in _steps() if s.get("name") == READ_STEP]
    assert len(found) == 1, f"{READ_STEP!r} is not one step of reconcile.yml"
    return found[0]


def _unplumbed(step: dict, rows: list) -> list:
    """Every variable a catalog row owes the step and the step does not carry."""
    env = step.get("env") or {}
    missing = []
    for row in rows:
        for name in (row["name"], switch_reason.companion(row["name"])):
            if env.get(name) != f"${{{{ vars.{name} }}}}":
                missing.append(name)
    return missing


class TestStep:
    def test_it_is_the_last_step_of_the_sweep_job_on_a_full_pass(self):
        step = _read_step()
        assert _steps()[-1]["name"] == READ_STEP
        assert step["if"] == "inputs.sweep_reason == ''"

    def test_it_carries_the_sweeps_env(self):
        sweep = next(s for s in _steps() if s.get("name") == "Sweep")
        env = _read_step()["env"]
        for name in SWEEP_KEYS:
            assert env[name] == sweep["env"][name], name

    def test_every_switch_and_its_companion_is_plumbed(self):
        assert _unplumbed(_read_step(), switch_reason.load()["switches"]) == []
        env = _read_step()["env"]
        for name in ("PROOF_DISPATCH_LIVE", "GREEN_LIGHT_REPLY_LIVE", "HYGIENE_LIVE"):
            assert env[name] == f"${{{{ vars.{name} }}}}"
            assert env[f"{name}_OFF_UNTIL"] == f"${{{{ vars.{name}_OFF_UNTIL }}}}"

    def test_a_row_with_no_plumbing_fails_by_name(self, tmp_path):
        path = tmp_path / "switches.json"
        doc = switch_reason.load()
        doc["switches"].append({"name": "FOO_LIVE", "reader": "x", "step": "y",
                                "means": "z."})
        path.write_text(json.dumps(doc))
        rows = switch_reason.load(str(path))["switches"]
        assert _unplumbed(_read_step(), rows) == ["FOO_LIVE", "FOO_LIVE_OFF_UNTIL"]

    def test_it_exports_the_slug_and_runs_the_reader(self):
        run = _read_step()["run"]
        assert "export REPO_SLUG" in run
        assert "python3 .bureau-pipeline/scripts/switch_reason.py" in run
        assert "tee switches.log" in run

    def _run(self, tmp_path, body, status=0):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "python3"
        fake.write_text(f"#!/bin/sh\n{body}\nexit {status}\n")
        fake.chmod(0o755)
        summary = tmp_path / "summary.md"
        env = {**os.environ,
               "PATH": f"{bin_dir}:{os.environ['PATH']}",
               "GITHUB_REPOSITORY": "dreadnought-foundry/Portico",
               "GITHUB_STEP_SUMMARY": str(summary)}
        work = tmp_path / "work"
        work.mkdir()
        done = subprocess.run(["bash", "-e", "-c", _read_step()["run"]], cwd=work,
                              env=env, capture_output=True, text=True)
        return done, summary.read_text(encoding="utf-8")

    def test_the_lines_and_the_spend_are_lifted_into_the_summary(self, tmp_path):
        done, text = self._run(tmp_path, (
            "echo 'switches: PROOF_DISPATCH_LIVE is on'\n"
            "echo 'switches: HYGIENE_LIVE is off — no reason given'\n"
            "echo 'noise'\n"
            "echo 'linear-budget: fixture'"))
        assert done.returncode == 0, done.stderr
        assert "### Switches — spend" in text
        assert "switches: PROOF_DISPATCH_LIVE is on" in text
        assert "switches: HYGIENE_LIVE is off — no reason given" in text
        assert "linear-budget: fixture" in text
        assert "noise" not in text
        assert "switches: PROOF_DISPATCH_LIVE is on" in done.stdout

    def test_the_phases_status_is_carried_past_the_summary(self, tmp_path):
        done, text = self._run(tmp_path, "echo 'switches: X is on'", status=3)
        assert done.returncode == 3
        assert "### Switches — spend" in text
        assert "no linear-budget: line" in text


# --------------------------------------------------------------------------- #
# the page a person reads                                                      #
# --------------------------------------------------------------------------- #


class TestDoc:
    def test_the_doc_names_the_step_the_companion_and_the_lines(self):
        text = (ROOT / "docs" / "switches.md").read_text(encoding="utf-8")
        for expected in ("_OFF_UNTIL", READ_STEP, "no reason given",
                         "### Switches — spend", "config/switches.json",
                         'gh variable set PROOF_DISPATCH_LIVE_OFF_UNTIL --body '
                         '"DRE-6141, DRE-6142, DRE-6143" -R <owner/repo>',
                         "is on — its off-reason is stale, delete",
                         "its reason names no card:", "its reason cleared:",
                         "the switch may be turned on", "unread"):
            assert expected in text, expected

    def test_the_doc_names_every_catalog_switch(self):
        text = (ROOT / "docs" / "switches.md").read_text(encoding="utf-8")
        for row in switch_reason.load()["switches"]:
            assert f"`{row['name']}`" in text, row["name"]


# --------------------------------------------------------------------------- #
# the receipt and the alarm (DRE-6437)                                         #
# --------------------------------------------------------------------------- #

ACT = "switch-reason-cleared"
TAG = "switch-cleared"
OWNER_REPO = "dreadnought-foundry/agent-bureau"
LIVE = {COMPANION: THREE, "GITHUB_ACTIONS": "true", "REPO": OWNER_REPO,
        "REPO_SLUG": "agent-bureau"}
ALL_DONE = {"DRE-6141": "Done", "DRE-6142": "Done", "DRE-6143": "Done"}
KEY = f"🔀 switch-cleared: {SWITCH} in agent-bureau"
RECEIPT_LINE = (f"{KEY} — its reason cleared at 2026-10-09 05:00 PT: "
                "DRE-6141 Done, DRE-6142 Done, DRE-6143 Done. It may be turned on: "
                f"gh variable set {SWITCH} --body true -R {OWNER_REPO}")
ALARM_TITLE = f"Switch {SWITCH} in agent-bureau is still off after its reason cleared"


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _receipt(hours_ago: float, line: str = RECEIPT_LINE) -> dict:
    """A receipt comment as Linear hands it back, `hours_ago` before NOW."""
    return {"body": pipeline_act.receipt(ACT, line),
            "createdAt": _iso(NOW - timedelta(hours=hours_ago))}


class FakeLinear:
    """The receipt phase's one read and three writes, recorded in order.

    A posted comment joins the thread, stamped NOW, so a second pass against
    the same fake reads the receipt the first one left."""

    def __init__(self, threads=None, open_card=None, thread_error=None):
        self.threads = {k: list(v) for k, v in (threads or {}).items()}
        self.open_card = open_card
        self.thread_error = thread_error
        self.calls = []
        self.comments = []
        self.created = []

    def thread(self, identifier):
        self.calls.append(("thread", identifier))
        if self.thread_error is not None:
            raise self.thread_error
        return list(self.threads.get(identifier, ()))

    def cmd_comment(self, identifier, body):
        self.calls.append(("comment", identifier))
        self.comments.append((identifier, body))
        self.threads.setdefault(identifier, []).append(
            {"body": body, "createdAt": _iso(NOW)})

    def find_open(self, title):
        self.calls.append(("find_open", title))
        return self.open_card

    def create_card(self, title, body, *, repo_slug):
        self.calls.append(("create", title))
        self.created.append((title, body, repo_slug))
        return {"identifier": "DRE-9999", "url": "https://linear.app/x/DRE-9999"}


def _pass(capsys, env=None, linear=None, states=None, argv=(), gql=None):
    linear = linear if linear is not None else FakeLinear()
    gql = gql if gql is not None else FakeGql(ALL_DONE if states is None else states)
    code = switch_reason.main(list(argv), env=LIVE if env is None else env,
                              gql=gql, now=NOW, linear=linear)
    return code, capsys.readouterr().out.splitlines(), linear


class TestReceipt:
    def test_one_pass_posts_one_receipt_on_the_first_named_card(self, capsys):
        code, lines, linear = _pass(capsys, linear=FakeLinear({"DRE-6141": []}))
        assert code == 0
        assert len(linear.comments) == 1
        card, body = linear.comments[0]
        assert card == "DRE-6141"
        assert body.splitlines()[0] == RECEIPT_LINE
        assert body == pipeline_act.receipt(ACT, RECEIPT_LINE)
        assert pipeline_act.read_trailer(body)["act"] == ACT
        assert pipeline_act.read_trailer(body)["tag"] == TAG
        assert linear.calls[0] == ("thread", "DRE-6141")
        assert [c for c in linear.calls if c[0] == "thread"] == [("thread", "DRE-6141")]
        assert linear.created == []
        assert any(l.startswith("switches: ") and "DRE-6141" in l and "posted" in l
                   for l in lines), lines

    def test_a_second_pass_posts_nothing_and_says_the_receipt_stands(self, capsys):
        linear = FakeLinear({"DRE-6141": []})
        _pass(capsys, linear=linear)
        code, lines, linear = _pass(capsys, linear=linear)
        assert code == 0
        assert len(linear.comments) == 1
        assert linear.created == []
        assert any("receipt already stands" in l and "DRE-6141" in l for l in lines), lines

    def test_another_repos_receipt_does_not_stop_this_repos(self, capsys):
        portico = RECEIPT_LINE.replace("in agent-bureau", "in portico").replace(
            OWNER_REPO, "dreadnought-foundry/Portico")
        linear = FakeLinear({"DRE-6141": [_receipt(1, portico)]})
        _pass(capsys, linear=linear)
        assert len(linear.comments) == 1
        assert linear.comments[0][1].splitlines()[0] == RECEIPT_LINE
        _pass(capsys, linear=linear)
        assert len(linear.comments) == 1

    def test_a_longer_slug_is_another_repo(self, capsys):
        other = RECEIPT_LINE.replace("in agent-bureau", "in agent-bureau-console")
        linear = FakeLinear({"DRE-6141": [_receipt(1, other)]})
        _pass(capsys, linear=linear)
        assert len(linear.comments) == 1

    def test_a_receipt_for_another_switch_does_not_count(self, capsys):
        other = RECEIPT_LINE.replace(SWITCH, "HYGIENE_LIVE")
        linear = FakeLinear({"DRE-6141": [_receipt(1, other)]})
        _pass(capsys, linear=linear)
        assert len(linear.comments) == 1

    def test_the_once_key_is_the_first_line_never_a_quote(self, capsys):
        quoted = {"body": f"As the sweep said:\n{RECEIPT_LINE}", "createdAt": _iso(NOW)}
        linear = FakeLinear({"DRE-6141": [quoted]})
        _pass(capsys, linear=linear)
        assert len(linear.comments) == 1

    def test_a_card_not_yet_terminal_reads_no_thread(self, capsys):
        states = {"DRE-6141": "Done", "DRE-6142": "Done", "DRE-6143": "In Review"}
        code, lines, linear = _pass(capsys, states=states)
        assert code == 0
        assert linear.calls == []

    def test_an_unread_card_reads_no_thread(self, capsys):
        gql = FakeGql(error=RuntimeError("Linear refused the read"))
        code, _, linear = _pass(capsys, gql=gql)
        assert code == 0
        assert linear.calls == []

    def test_an_on_switch_reads_and_writes_nothing(self, capsys):
        _, _, linear = _pass(capsys, env={**LIVE, SWITCH: "true"},
                             linear=FakeLinear({"DRE-6141": [_receipt(13)]}))
        assert linear.calls == []

    def test_a_thread_that_cannot_be_read_posts_nothing(self, capsys):
        linear = FakeLinear(thread_error=RuntimeError("Linear refused: RATELIMITED"))
        code, lines, linear = _pass(capsys, linear=linear)
        assert code == 0
        assert linear.comments == [] and linear.created == []
        assert any("RATELIMITED" in l for l in lines), lines
        assert lines[-1].startswith("linear-budget:")

    def test_no_repository_named_reads_and_writes_nothing(self, capsys):
        env = {k: v for k, v in LIVE.items() if k not in ("REPO", "REPO_SLUG")}
        code, _, linear = _pass(capsys, env=env)
        assert code == 0
        assert linear.calls == []

    def test_the_slug_falls_back_to_the_repository(self, capsys):
        env = {k: v for k, v in LIVE.items() if k != "REPO_SLUG"}
        _, _, linear = _pass(capsys, env=env, linear=FakeLinear({"DRE-6141": []}))
        assert linear.comments[0][1].splitlines()[0] == RECEIPT_LINE


class TestAlarm:
    def test_thirteen_hours_on_files_one_card_after_find_open(self, capsys):
        code, lines, linear = _pass(capsys, linear=FakeLinear({"DRE-6141": [_receipt(13)]}))
        assert code == 0
        assert linear.comments == []
        assert len(linear.created) == 1
        title, body, slug = linear.created[0]
        assert title == ALARM_TITLE
        assert slug == "agent-bureau"
        kinds = [c[0] for c in linear.calls]
        assert kinds.index("find_open") < kinds.index("create")
        assert ("find_open", ALARM_TITLE) in linear.calls
        for expected in (SWITCH, "agent-bureau", "2026-10-08 16:00 PT", "13 hours",
                         "DRE-6141", "DRE-6142", "DRE-6143",
                         f"gh variable set {SWITCH} --body true -R {OWNER_REPO}"):
            assert expected in body, expected
        assert any("DRE-9999" in l for l in lines), lines

    def test_an_open_alarm_files_nothing(self, capsys):
        _, lines, linear = _pass(capsys, linear=FakeLinear({"DRE-6141": [_receipt(13)]},
                                                           open_card="DRE-7000"))
        assert ("find_open", ALARM_TITLE) in linear.calls
        assert linear.created == []
        assert any("DRE-7000" in l for l in lines), lines

    def test_two_hours_on_files_nothing(self, capsys):
        _, _, linear = _pass(capsys, linear=FakeLinear({"DRE-6141": [_receipt(2)]}))
        assert linear.created == []
        assert linear.comments == []
        assert not any(c[0] == "find_open" for c in linear.calls)

    def test_exactly_twelve_hours_alarms(self, capsys):
        _, _, linear = _pass(capsys, linear=FakeLinear({"DRE-6141": [_receipt(12)]}))
        assert len(linear.created) == 1

    def test_the_threshold_is_read_from_the_catalog(self, capsys, monkeypatch):
        doc = switch_reason.load()
        doc["alarm_after_hours"] = 24
        monkeypatch.setattr(switch_reason, "load", lambda path=None: doc)
        _, _, linear = _pass(capsys, linear=FakeLinear({"DRE-6141": [_receipt(13)]}))
        assert linear.created == []

    def test_an_on_switch_alarms_nothing(self, capsys):
        _, _, linear = _pass(capsys, env={**LIVE, SWITCH: "true"},
                             linear=FakeLinear({"DRE-6141": [_receipt(13)]}))
        assert linear.created == [] and linear.calls == []

    def test_an_unreadable_receipt_time_alarms_nothing(self, capsys):
        receipt = {**_receipt(13), "createdAt": "not a time"}
        _, _, linear = _pass(capsys, linear=FakeLinear({"DRE-6141": [receipt]}))
        assert linear.created == [] and linear.comments == []

    def test_a_card_that_cannot_be_filed_still_exits_zero(self, capsys):
        class Refusing(FakeLinear):
            def create_card(self, title, body, *, repo_slug):
                raise RuntimeError("Linear refused the create")
        code, lines, _ = _pass(capsys, linear=Refusing({"DRE-6141": [_receipt(13)]}))
        assert code == 0
        assert any("Linear refused the create" in l for l in lines), lines


class TestDryRun:
    def test_dry_run_flag_prints_would_post_and_writes_nothing(self, capsys):
        _, lines, linear = _pass(capsys, argv=["--dry-run"],
                                 linear=FakeLinear({"DRE-6141": []}))
        assert linear.comments == []
        assert not any(c[0] in ("comment", "create") for c in linear.calls)
        assert f"would: post DRE-6141 — {RECEIPT_LINE}" in lines

    def test_dry_run_flag_prints_would_alarm_and_files_nothing(self, capsys):
        _, lines, linear = _pass(capsys, argv=["--dry-run"],
                                 linear=FakeLinear({"DRE-6141": [_receipt(13)]}))
        assert linear.created == []
        assert not any(c[0] in ("comment", "create") for c in linear.calls)
        assert any(l.startswith(f"would: alarm — {ALARM_TITLE}") for l in lines), lines

    def test_outside_actions_the_phase_is_dry(self, capsys):
        env = {k: v for k, v in LIVE.items() if k != "GITHUB_ACTIONS"}
        _, lines, linear = _pass(capsys, env=env, linear=FakeLinear({"DRE-6141": []}))
        assert linear.comments == []
        assert any(l.startswith("would: post DRE-6141") for l in lines), lines

    def test_the_cli_takes_the_flag(self):
        env = {k: v for k, v in os.environ.items()
               if not k.endswith(("_LIVE", "_OFF_UNTIL"))}
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "switch_reason.py"), "--dry-run"],
            capture_output=True, text=True, cwd=ROOT, env=env)
        assert done.returncode == 0, done.stdout + done.stderr
        assert len(_switch_lines(done.stdout.splitlines())) == 3


class TestActRow:
    def test_the_act_row_ships_with_the_module(self):
        row = pipeline_act.record(ACT)
        assert row["tag"] == TAG
        assert (row["kind"], row["state"]) == ("progress", "unchanged")
        assert row["subscriber"] == "reconcile.yml"
        assert row["discharges"] is None
        assert row["cadence_s"] == 43200
        assert row["cadence_s"] == switch_reason.load()["alarm_after_hours"] * 3600
        assert "alarm_after_hours" in row["cadence_why"]
        assert row["emits"]["file"] == "scripts/switch_reason.py"
        assert row["emits"]["anchor"] == f'pipeline_act.receipt("{ACT}"'
        source = (ROOT / "scripts" / "switch_reason.py").read_text(encoding="utf-8")
        assert source.count(row["emits"]["anchor"]) == 1
        assert pipeline_act.problems() == []

    def test_the_receipt_writer_guard_passes(self):
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_act_receipts.py")],
            capture_output=True, text=True, cwd=ROOT)
        assert done.returncode == 0, done.stdout + done.stderr

    def test_the_doc_carries_the_row(self):
        text = (ROOT / "docs" / "pipeline-acts.md").read_text(encoding="utf-8")
        for expected in (ACT, f"`{TAG}`", "_OFF_UNTIL", "scripts/switch_reason.py"):
            assert expected in text, expected
