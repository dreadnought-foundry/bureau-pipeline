"""Every Green Light row is a declared arrival (DRE-5282, epic DRE-5268).

`scripts/green_light_rows.py check` proves, the way `ready_lane_writers.py
check` proves its absence for the ready-work lanes, that every write the
pipeline makes into Green Light is one of the arrivals the lane contract
declares on that lane's entrance: a plan both critics passed, the planner's
business question, a build's escalation to a person, or (while the CEO's
acceptance of kind (c) stands) an approved epic queued under the cap.

Discovery is not re-proved here. `ready_lane_writers.writes()` finds the write
sites and `lane_callers.callers_of()` finds the callers of a lane-writing
function; their own suites prove them (`tests/test_no_unplanned_ready_lane_writer.py`,
`tests/test_lane_callers.py`). What this file proves is the reconciliation:

* the site rules on THROWAWAY COPIES of the repository (scripts, workflows and
  config), so a new writer, a weakened gate or a reconcile.py write is added
  where the check actually reads and watched being named;
* the caller rules with `lane_callers.callers_of` STUBBED, so a borrowed write
  (the DRE-4124 stall exit reached Green Light through
  `planning_escalation.escalate`) is named by its caller;
* and the repository itself, which passes, with the callers found exactly the
  ones the contract declares on all three function records.

Run: cd bureau-pipeline && python3 -m pytest tests/test_green_light_rows.py -v
"""
from __future__ import annotations

import copy
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
# `reconcile` reads these at import, and `ready_lane_writers` imports it to read
# its published `destinations()`.
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import green_light_rows as grl  # noqa: E402
import lane_callers  # noqa: E402
import lane_contract  # noqa: E402
import ready_lane_writers as rlw  # noqa: E402
import step_shell  # noqa: E402

#: The six `reconcile.py` sites whose destination `writes()` could not read at
#: 9edf221, before DRE-5286 published `reconcile.destinations()`. Named so a
#: regression says which came back.
UNREAD_AT_9EDF221 = (
    "scripts/reconcile.py:1534 (a retiring lane's replaced_by)",
    "scripts/reconcile.py:6151 (REVIEW_LANE)",
    "scripts/reconcile.py:6599 (REVIEW_LANE)",
    "scripts/reconcile.py:7934 (_fleet_outage_state's lane)",
    "scripts/reconcile.py:8740 (the move handed to limit_recovery.recover)",
    "scripts/reconcile.py:9480 (REVIEW_LANE)",
)

PASSED_STEP = "Epic → Green Light — both critics passed"
PASSED_IF = (
    "if: steps.route.outputs.mode == 'review' && steps.post1.outputs.result == 'PASS' "
    "&& steps.post1.outputs.pre_passed == 'true'"
)


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


def _contract() -> dict:
    """A private copy of the lane contract: `load()` is cached and shared."""
    return copy.deepcopy(lane_contract.load())


def _entrance(contract: dict) -> dict:
    for lane in contract["lanes"]:
        if lane.get("name") == grl.lane_name(contract):
            return lane["clauses"]["entrance"]
    raise AssertionError("no Green Light lane in the contract")


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
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _workflow(root: Path, name: str, steps: str) -> None:
    (root / ".github" / "workflows" / name).write_text(
        "name: throwaway\n"
        "on: workflow_dispatch\n"
        "jobs:\n"
        "  go:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n" + steps,
        encoding="utf-8",
    )


#: The comment that opens the step after the passed-plan step in plan.yml: a
#: step inserted before it sits straight after a declared arrival.
AFTER_PASSED_STEP = "      # PASS, but the first critic has not passed the plan: its last record is"

#: A shell line a sneaked-in step carries, so the test can find its step.
SNEAK = "echo ZZ-SNEAK"


def _sneak(opening: str) -> str:
    """An ungated step that writes Green Light, opening with `opening`."""
    return (
        f"      {opening}\n"
        "        run: |\n"
        f"          {SNEAK}\n"
        "          python3 .bureau-pipeline/scripts/linear_ops.py state \"$EPIC\" "
        "\"Green Light\"\n\n"
    )


def _job_index_of_sneak(root: Path, name: str) -> str:
    """`<job>[<index>]` of the step carrying SNEAK, read off the parsed workflow."""
    path = root / ".github" / "workflows" / name
    doc = yaml.safe_load(step_shell.workflow_source(path.resolve(), root))
    for job, body in doc["jobs"].items():
        for index, step in enumerate(body.get("steps") or []):
            if SNEAK in str(step.get("run") or ""):
                return f"{job}[{index}]"
    raise AssertionError(f"no step carries {SNEAK} in {name}")


def _named(problems, *needles) -> list:
    return [p for p in problems if all(n in p for n in needles)]


def _full(unit: str) -> str:
    """A contract caller unit (`plan.yml#Step`) in `callers_of`'s grammar."""
    file, _, rest = unit.partition("#")
    folder = ".github/workflows" if file.endswith(".yml") else "scripts"
    return f"{folder}/{file}#{rest}"


def _declared(contract: dict) -> dict:
    """(module path, function) → the callers its arrival record declares."""
    out = {}
    for record in _entrance(contract)["arrivals"]:
        file, _, function = record["where"].partition("#")
        if file.endswith(".py"):
            out[(f"scripts/{file}", function)] = list(record.get("callers") or [])
    return out


def _stub_callers(monkeypatch, contract, *, extra=None, missing=None, unread=None):
    """Replace `lane_callers.callers_of` with an answer built off the declared
    callers, plus `extra`, less `missing`, with `unread` — each keyed by
    (module path, function)."""
    declared = _declared(contract)
    asked = []

    def fake(module_path, function, root="."):
        key = (module_path, function)
        asked.append(key)
        units = [_full(u) for u in declared.get(key, [])]
        units += list((extra or {}).get(key, []))
        units = [u for u in units if u not in set((missing or {}).get(key, []))]
        return lane_callers.CallerReport(
            callers=frozenset(units),
            unread=frozenset((unread or {}).get(key, [])),
        )

    monkeypatch.setattr(lane_callers, "callers_of", fake)
    return asked


@pytest.fixture
def declared_callers(monkeypatch):
    """Caller discovery answers exactly what the contract declares, so a
    site-rule test reads the site rule alone. The callers themselves are proved
    by `TestCallers` and over the real repository."""
    _stub_callers(monkeypatch, _contract())


ESCALATE = ("scripts/planning_escalation.py", "escalate")
CMD_EXIT = ("scripts/planning_route.py", "_cmd_exit")
PARK = ("scripts/code_owner_hold.py", "park")


# --------------------------------------------------------------------------- #
# the repository passes                                                        #
# --------------------------------------------------------------------------- #


class TestTheRepositoryPasses:
    def test_the_check_finds_no_problem(self):
        assert grl.problems() == []

    def test_the_cli_exits_zero_and_lists_every_write_with_its_kind(self):
        lines = []
        assert grl.run_check(out=lines.append) == 0
        found = grl.green_light_writes()
        assert found, "the discovery found no Green Light write at all"
        kinds = {r["where"]: r["kind"] for r in grl.arrivals()}
        for write, unit in found:
            assert any(unit in line and kinds[unit] in line and write.where in line
                       for line in lines), (unit, lines)
        summary = [line for line in lines if "write(s) into Green Light discovered" in line]
        assert len(summary) == 1, lines
        assert f"{len(found)} write(s) into Green Light discovered" in summary[0]
        assert f"{len(grl.arrivals())} arrival(s) declared" in summary[0]
        assert summary[0].rstrip().endswith("0 problem(s)")

    def test_the_lane_is_read_off_the_contract(self):
        # The one live lane whose entrance declares its arrivals.
        assert grl.lane_name() == "Green Light"
        contract = _contract()
        for lane in contract["lanes"]:
            if lane.get("name") == "Green Light":
                lane["name"] = "Decisions"
        assert grl.lane_name(contract) == "Decisions"

    def test_every_discovered_write_sits_at_a_declared_arrival(self):
        wheres = {r["where"] for r in grl.arrivals()}
        units = {unit for _, unit in grl.green_light_writes()}
        assert units <= wheres, units - wheres
        # The five sites the contract declares, each found as a write.
        assert units == wheres

    def test_a_kind_with_no_record_and_no_write_is_not_a_problem(self):
        # `queued-epic` is in the vocabulary while the CEO's acceptance of kind
        # (c) stands; DRE-5136 declares its record with the write it lands.
        assert "queued-epic" in grl.kinds()
        assert not [r for r in grl.arrivals() if r["kind"] == "queued-epic"]
        assert grl.problems() == []

    def test_the_callers_found_are_exactly_the_ones_declared(self):
        contract = _contract()
        declared = _declared(contract)
        assert set(declared) == {ESCALATE, CMD_EXIT, PARK}
        counts = {ESCALATE: 6, CMD_EXIT: 2, PARK: 2}
        for (module, function), callers in declared.items():
            report = lane_callers.callers_of(module, function, str(ROOT))
            assert report.unread == frozenset(), (module, report.unread)
            found = {grl.short_unit(c) for c in report.callers}
            assert found == set(callers), (module, function, found ^ set(callers))
            assert len(found) == counts[(module, function)]

    def test_no_destination_is_unread(self):
        unread = [w for w in rlw.writes() if w.lane is None]
        assert unread == [], (
            "ready_lane_writers.writes() could not read these destinations, and "
            "any one of them could be Green Light. At 9edf221, before DRE-5286 "
            "published reconcile.destinations(), the unread sites were "
            + "; ".join(UNREAD_AT_9EDF221)
            + f". Found now: {unread}"
        )

    def test_the_bare_cli_exits_zero_from_a_terminal_with_no_repo_set(self):
        # `reconcile` reads REPO at import; the CLI supplies a placeholder so
        # its pure `destinations()` is read rather than six sites coming back
        # unread from a bare terminal.
        env = {k: v for k, v in os.environ.items() if k != "REPO"}
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "green_light_rows.py"), "check"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=300,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        assert "0 problem(s)" in done.stdout

    def test_the_unseen_writers_are_reported_as_the_sibling_reads_them(self, monkeypatch):
        monkeypatch.setattr(rlw, "unseen_writers", lambda contract=None: ("operator", "relay"))
        lines = []
        grl.run_check(out=lines.append)
        assert any(line.strip() == "not checkable from here: operator, relay"
                   for line in lines), lines


# --------------------------------------------------------------------------- #
# a new writer is named by location                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("declared_callers")
class TestANewWriterIsNamed:
    def test_a_script_that_writes_green_light_is_named_by_file_and_function(self, tmp_path):
        root = _copy_repo(tmp_path)
        (root / "scripts" / "zz_new_writer.py").write_text(
            "import linear_ops\n\n\n"
            "def put_it_in_front_of_the_ceo(card):\n"
            f"    linear_ops.cmd_state(card, {grl.lane_name()!r})\n",
            encoding="utf-8",
        )
        found = grl.problems(str(root))
        assert _named(found, "zz_new_writer.py#put_it_in_front_of_the_ceo", "no arrival"), found

    def test_a_workflow_step_that_writes_green_light_is_named_by_file_and_step(self, tmp_path):
        root = _copy_repo(tmp_path)
        _workflow(root, "zz-new.yml", (
            "      - name: Ask the CEO to look again\n"
            "        run: |\n"
            "          python3 .bureau-pipeline/scripts/linear_ops.py state \"$CARD\" "
            f"\"{grl.lane_name()}\"\n"
        ))
        found = grl.problems(str(root))
        assert _named(found, "zz-new.yml#Ask the CEO to look again", "no arrival"), found
        assert grl.run_check(str(root), out=lambda _l: None) == 1

    @pytest.mark.parametrize("opening, unit", [
        ("- id: sneak\n        name: Look again — straight to the CEO",
         "Look again — straight to the CEO"),
        ("- if: always()\n        name: Look again", "Look again"),
        ("- env:\n          X: y\n        name: Look again", "Look again"),
        ("- id: sneak", "sneak"),
    ], ids=["id-first", "if-first", "env-first", "id-only"])
    def test_a_step_not_opened_by_its_name_is_its_own_unit_not_the_step_above(
            self, tmp_path, opening, unit):
        # Straight after the passed-plan step: read upward to the nearest
        # `- name:`, this write was that declared, gated step's.
        root = _copy_repo(tmp_path)
        _edit(root / ".github" / "workflows" / "plan.yml", AFTER_PASSED_STEP,
              _sneak(opening) + AFTER_PASSED_STEP)
        found = grl.problems(str(root))
        assert _named(found, f"plan.yml#{unit} (", "no arrival"), found
        units = {u for _, u in grl.green_light_writes(str(root))}
        assert f"plan.yml#{unit}" in units, units

    def test_an_unnamed_step_is_named_as_lane_callers_names_it(self, tmp_path):
        # `name or id or <job>[<index>]` — the grammar the contract's callers use.
        root = _copy_repo(tmp_path)
        path = root / ".github" / "workflows" / "agent-task.yml"
        text = path.read_text(encoding="utf-8")
        start = text.index("      - name: Report result to Linear\n")
        after = text.index("\n      - ", start + 1) + 1
        path.write_text(text[:after] + _sneak("- if: always()") + text[after:],
                        encoding="utf-8")
        unit = f"agent-task.yml#{_job_index_of_sneak(root, 'agent-task.yml')}"
        found = grl.problems(str(root))
        assert _named(found, f"{unit} (", "no arrival"), found
        assert not _named(found, "agent-task.yml#Report result to Linear"), found

    def test_a_reconcile_write_into_green_light_is_named_by_location(self, tmp_path):
        root = _copy_repo(tmp_path)
        path = root / "scripts" / "reconcile.py"
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\n\ndef _stalled_planning_to_the_ceo(card):\n"
            + f"    linear_ops.cmd_state(card, {grl.lane_name()!r})\n",
            encoding="utf-8",
        )
        found = grl.problems(str(root))
        assert _named(found, "reconcile.py#_stalled_planning_to_the_ceo", "no arrival"), found


# --------------------------------------------------------------------------- #
# each site's own gate is read, not trusted                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("declared_callers")
class TestThePassedPlanGate:
    @pytest.mark.parametrize("weakened", [
        "if: steps.route.outputs.mode == 'review' && steps.post1.outputs.action == 'proceed'",
        "if: steps.route.outputs.mode == 'review' && steps.post1.outputs.result == 'PASS'",
        "if: steps.route.outputs.mode == 'review' && steps.post1.outputs.pre_passed == 'true'",
        "if: steps.route.outputs.mode == 'review' && steps.post1.outputs.result == 'PASS' "
        "|| steps.post1.outputs.pre_passed == 'true'",
        "if: steps.route.outputs.mode == 'review' && "
        "(steps.post1.outputs.result == 'PASS' || steps.post1.outputs.pre_passed == 'true')",
        "if: steps.route.outputs.mode == 'review' && !(steps.post1.outputs.result == 'PASS') "
        "&& steps.post1.outputs.pre_passed == 'true'",
        "if: steps.route.outputs.mode == 'review' && steps.post1.outputs.result == 'PASS' "
        "&& !(steps.post1.outputs.pre_passed == 'true')",
        "if: steps.route.outputs.mode == 'review' && steps.post1.outputs.result == 'PASS' "
        "&& steps.post1.outputs.pre_passed == 'true' || always()",
    ], ids=["proceed-alone", "no-pre-passed", "no-result-pass", "either-pass",
            "either-pass-in-parens", "result-negated", "pre-passed-negated",
            "or-always"])
    def test_a_step_missing_either_critic_s_pass_fails_naming_the_step(self, tmp_path, weakened):
        root = _copy_repo(tmp_path)
        _edit(root / ".github" / "workflows" / "plan.yml", PASSED_IF, weakened)
        found = grl.problems(str(root))
        assert _named(found, f"plan.yml#{PASSED_STEP}", "passed-plan"), found

    def test_the_step_as_it_stands_carries_both(self, tmp_path):
        root = _copy_repo(tmp_path)
        assert not _named(grl.problems(str(root)), "passed-plan")

    @pytest.mark.parametrize("same", [
        "if: ${{ steps.route.outputs.mode == 'review' && steps.post1.outputs.result == 'PASS' "
        "&& steps.post1.outputs.pre_passed == 'true' }}",
        "if: steps.route.outputs.mode == 'review' && (steps.post1.outputs.result == 'PASS' "
        "&& steps.post1.outputs.pre_passed == 'true')",
    ], ids=["expression-syntax", "redundant-parens"])
    def test_the_same_gate_written_another_way_still_carries_both(self, tmp_path, same):
        root = _copy_repo(tmp_path)
        _edit(root / ".github" / "workflows" / "plan.yml", PASSED_IF, same)
        assert not _named(grl.problems(str(root)), "passed-plan")


def _queued_contract(where: str) -> dict:
    contract = _contract()
    _entrance(contract)["arrivals"].append({
        "kind": "queued-epic",
        "writer": where.partition("#")[0],
        "where": where,
        "evidence": "the epic-queued label, added before the move",
        "card": "DRE-5136",
    })
    return contract


@pytest.mark.usefixtures("declared_callers")
class TestTheQueuedEpicGate:
    WHERE = "zz-queue.yml#Approved at the cap — wait in line"

    def _steps(self, *lines):
        return (
            "      - name: Approved at the cap — wait in line\n"
            "        run: |\n" + "".join(f"          {line}\n" for line in lines)
        )

    def test_a_step_that_labels_before_it_writes_passes(self, tmp_path):
        root = _copy_repo(tmp_path)
        _workflow(root, "zz-queue.yml", self._steps(
            "python3 .bureau-pipeline/scripts/linear_ops.py add-label \"$EPIC\" epic-queued",
            f"python3 .bureau-pipeline/scripts/linear_ops.py state \"$EPIC\" \"{grl.lane_name()}\"",
        ))
        assert grl.problems(str(root), _queued_contract(self.WHERE)) == []

    def test_a_step_that_writes_without_the_label_fails_naming_the_step(self, tmp_path):
        root = _copy_repo(tmp_path)
        _workflow(root, "zz-queue.yml", self._steps(
            f"python3 .bureau-pipeline/scripts/linear_ops.py state \"$EPIC\" \"{grl.lane_name()}\"",
        ))
        found = grl.problems(str(root), _queued_contract(self.WHERE))
        assert _named(found, self.WHERE, "epic-queued"), found

    def test_a_label_added_after_the_write_fails_too(self, tmp_path):
        root = _copy_repo(tmp_path)
        _workflow(root, "zz-queue.yml", self._steps(
            f"python3 .bureau-pipeline/scripts/linear_ops.py state \"$EPIC\" \"{grl.lane_name()}\"",
            "python3 .bureau-pipeline/scripts/linear_ops.py add-label \"$EPIC\" epic-queued",
        ))
        found = grl.problems(str(root), _queued_contract(self.WHERE))
        assert _named(found, self.WHERE, "epic-queued"), found

    def test_a_label_wrapped_onto_a_continuation_line_passes(self, tmp_path):
        root = _copy_repo(tmp_path)
        _workflow(root, "zz-queue.yml", self._steps(
            "python3 .bureau-pipeline/scripts/linear_ops.py add-label \"$EPIC\" \\",
            "  epic-queued",
            f"python3 .bureau-pipeline/scripts/linear_ops.py state \"$EPIC\" \"{grl.lane_name()}\"",
        ))
        assert grl.problems(str(root), _queued_contract(self.WHERE)) == []

    @pytest.mark.parametrize("comment", [
        "# python3 .bureau-pipeline/scripts/linear_ops.py add-label \"$EPIC\" epic-queued",
        "true  # python3 .bureau-pipeline/scripts/linear_ops.py add-label \"$EPIC\" epic-queued",
    ], ids=["whole-line", "trailing"])
    def test_a_commented_out_label_fails(self, tmp_path, comment):
        root = _copy_repo(tmp_path)
        _workflow(root, "zz-queue.yml", self._steps(
            comment,
            f"python3 .bureau-pipeline/scripts/linear_ops.py state \"$EPIC\" \"{grl.lane_name()}\"",
        ))
        found = grl.problems(str(root), _queued_contract(self.WHERE))
        assert _named(found, self.WHERE, "epic-queued"), found

    def test_a_label_added_in_the_step_above_is_not_this_step_s(self, tmp_path):
        # The write's step opens with `- if:`; its text starts there, not at
        # the `- name:` of the step above that labels.
        root = _copy_repo(tmp_path)
        _workflow(root, "zz-queue.yml", (
            "      - name: Label it\n"
            "        run: |\n"
            "          python3 .bureau-pipeline/scripts/linear_ops.py add-label \"$EPIC\" epic-queued\n"
            "      - if: always()\n"
            "        name: Approved at the cap — wait in line\n"
            "        run: |\n"
            f"          python3 .bureau-pipeline/scripts/linear_ops.py state \"$EPIC\" \"{grl.lane_name()}\"\n"
        ))
        found = grl.problems(str(root), _queued_contract(self.WHERE))
        assert _named(found, self.WHERE, "epic-queued"), found

    def test_the_vocabulary_is_read_off_the_contract(self, tmp_path):
        # Take `queued-epic` out of the vocabulary and the same, correctly
        # gated record fails by word: the count is never hardcoded.
        root = _copy_repo(tmp_path)
        _workflow(root, "zz-queue.yml", self._steps(
            "python3 .bureau-pipeline/scripts/linear_ops.py add-label \"$EPIC\" epic-queued",
            f"python3 .bureau-pipeline/scripts/linear_ops.py state \"$EPIC\" \"{grl.lane_name()}\"",
        ))
        contract = _queued_contract(self.WHERE)
        _entrance(contract)["kinds"].remove("queued-epic")
        found = grl.problems(str(root), contract)
        assert _named(found, "'queued-epic'", self.WHERE, "vocabulary"), found


@pytest.mark.usefixtures("declared_callers")
class TestTheAgentEscalationGate:
    #: Where `agent-task.yml#Report result to Linear`'s shell lives since
    #: DRE-5223: the step is one delegation line, and the check reads the
    #: script through it, so a weakened gate is planted here.
    REPORT_SCRIPT = Path("scripts") / "report_agent_result.sh"

    def test_the_build_run_s_escalation_must_post_the_question_first(self, tmp_path):
        import planner_score

        root = _copy_repo(tmp_path)
        _edit(root / self.REPORT_SCRIPT,
              planner_score.ESCALATION_RECEIPT_PREFIX, "The agent stopped")
        found = grl.problems(str(root))
        assert _named(found, "agent-task.yml#Report result to Linear",
                      "agent-escalation"), found

    RECEIPT_LINE = (
        'echo "🙋 The agent paused for a decision before building — it judged '
        'this needs your call rather than a guess."'
    )

    def test_a_receipt_left_only_as_a_whole_line_comment_fails(self, tmp_path):
        import planner_score

        root = _copy_repo(tmp_path)
        _edit(root / self.REPORT_SCRIPT, self.RECEIPT_LINE,
              f'# {planner_score.ESCALATION_RECEIPT_PREFIX}\n'
              '    echo "Status update"')
        found = grl.problems(str(root))
        assert _named(found, "agent-task.yml#Report result to Linear",
                      "agent-escalation"), found

    def test_a_receipt_left_only_as_a_trailing_comment_fails(self, tmp_path):
        import planner_score

        root = _copy_repo(tmp_path)
        _edit(root / self.REPORT_SCRIPT, self.RECEIPT_LINE,
              f'echo "Status update"  # {planner_score.ESCALATION_RECEIPT_PREFIX}')
        found = grl.problems(str(root))
        assert _named(found, "agent-task.yml#Report result to Linear",
                      "agent-escalation"), found

    def test_a_receipt_written_and_never_posted_fails(self, tmp_path):
        root = _copy_repo(tmp_path)
        _edit(root / self.REPORT_SCRIPT,
              'python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \\\n'
              '    "$(cat /tmp/escalation-comment.md)"',
              'cat /tmp/escalation-comment.md')
        found = grl.problems(str(root))
        assert _named(found, "agent-task.yml#Report result to Linear",
                      "agent-escalation"), found

    def test_an_agent_escalation_anywhere_else_fails_by_word(self, tmp_path):
        root = _copy_repo(tmp_path)
        _workflow(root, "zz-esc.yml", (
            "      - name: Escalate\n"
            "        run: |\n"
            "          python3 .bureau-pipeline/scripts/linear_ops.py state \"$CARD\" "
            f"\"{grl.lane_name()}\"\n"
        ))
        contract = _contract()
        _entrance(contract)["arrivals"].append({
            "kind": "agent-escalation", "writer": "zz-esc.yml",
            "where": "zz-esc.yml#Escalate", "evidence": "x", "card": "DRE-0",
        })
        found = grl.problems(str(root), contract)
        assert _named(found, "zz-esc.yml#Escalate", "agent-escalation"), found


# --------------------------------------------------------------------------- #
# the records themselves                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("declared_callers")
class TestTheRecords:
    def test_an_arrival_whose_site_is_gone_fails_by_name(self):
        contract = _contract()
        _entrance(contract)["arrivals"].append({
            "kind": "passed-plan", "writer": "plan.yml",
            "where": "plan.yml#A step nobody wrote", "evidence": "x", "card": "DRE-0",
        })
        found = grl.problems(contract=contract)
        # Named once, by name: a gate read off a step that is not there would
        # only repeat it.
        assert len(_named(found, "plan.yml#A step nobody wrote")) == 1, found

    def test_a_kind_outside_the_vocabulary_fails_by_word(self):
        contract = _contract()
        for record in _entrance(contract)["arrivals"]:
            if record["where"] == f"plan.yml#{PASSED_STEP}":
                record["kind"] = "look-again"
        found = grl.problems(contract=contract)
        assert _named(found, "'look-again'", f"plan.yml#{PASSED_STEP}", "vocabulary"), found

    def test_a_question_record_that_is_not_the_planner_s_escalate_fails_by_word(self):
        contract = _contract()
        _entrance(contract)["arrivals"].append({
            "kind": "question", "writer": "reconcile.py",
            "where": "reconcile.py#flag_stalled_planning", "evidence": "x", "card": "DRE-4124",
        })
        found = grl.problems(contract=contract)
        assert _named(found, "reconcile.py#flag_stalled_planning", "question",
                      "planning_escalation.py#escalate"), found


# --------------------------------------------------------------------------- #
# unreadable destinations are problems, never passed                           #
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("declared_callers")
class TestUnreadDestinations:
    def test_without_reconcile_s_destinations_every_unread_site_is_a_problem(
            self, tmp_path, monkeypatch):
        import reconcile

        root = _copy_repo(tmp_path)
        path = root / "scripts" / "reconcile.py"
        text = path.read_text(encoding="utf-8")
        assert "\ndef destinations(" in text
        path.write_text(text.replace("\ndef destinations(", "\ndef _was_destinations(", 1),
                        encoding="utf-8")
        # `writes()` reads the hook off the imported module, so the import must
        # say what the copy says.
        monkeypatch.delattr(reconcile, rlw.DESTINATIONS_HOOK)
        unread = sorted({w.where for w in rlw.writes(str(root)) if w.lane is None})
        assert len(unread) >= 6, unread
        assert all(w.startswith("scripts/reconcile.py:") for w in unread), unread
        found = grl.problems(str(root))
        for where in unread:
            assert len(_named(found, where, "destinations()")) == 1, (where, found)
        assert grl.run_check(str(root), out=lambda _l: None) == 1

    def test_an_unread_write_from_a_writer_permitted_everywhere_is_still_reported(
            self, tmp_path):
        # agent-task.yml is permitted in the fleet's ready-work lanes (Backlog,
        # Todo), which is exactly the writer the sibling check skips when it
        # cannot read the destination. (Hand-work joined the ready lanes with
        # DRE-5321 and is a person's lane, so the build run is not its writer.)
        # Green Light is not a ready-work lane, so that escape does not carry
        # here.
        assert "agent-task.yml" in set.intersection(
            *[set(lane_contract.lane_writers(n)) for n in ("Backlog", "Todo")])
        root = _copy_repo(tmp_path)
        _edit(root / ".github" / "workflows" / "agent-task.yml",
              "      - name: Report result to Linear\n",
              "      - name: Park somewhere computed\n"
              "        run: |\n"
              "          python3 .bureau-pipeline/scripts/linear_ops.py state \"$CARD\" \"$WHERE\"\n"
              "      - name: Report result to Linear\n")
        found = grl.problems(str(root))
        assert _named(found, "agent-task.yml", "$WHERE", "destinations()"), found


# --------------------------------------------------------------------------- #
# callers: a borrowed write is attributed to its caller                        #
# --------------------------------------------------------------------------- #


class TestCallers:
    def test_the_stubbed_declared_answers_pass_and_all_three_are_asked(self, monkeypatch):
        contract = _contract()
        asked = _stub_callers(monkeypatch, contract)
        assert grl.problems(contract=contract) == []
        assert {ESCALATE, CMD_EXIT, PARK} <= set(asked)

    def test_the_sweep_calling_escalate_fails_by_caller(self, monkeypatch):
        contract = _contract()
        _stub_callers(monkeypatch, contract, extra={
            ESCALATE: ["scripts/reconcile.py#escalate_out_of_planning"]})
        found = grl.problems(contract=contract)
        assert _named(found, "reconcile.py#escalate_out_of_planning",
                      "planning_escalation.py#escalate", "does not declare"), found
        lines = []
        assert grl.run_check(contract=contract, out=lines.append) == 1

    def test_an_undeclared_workflow_step_fails_naming_the_step(self, monkeypatch):
        contract = _contract()
        _stub_callers(monkeypatch, contract, extra={
            ESCALATE: [".github/workflows/plan.yml#Look again — re-run the critic"]})
        found = grl.problems(contract=contract)
        assert _named(found, "plan.yml#Look again — re-run the critic",
                      "planning_escalation.py#escalate"), found

    def test_a_declared_caller_that_is_gone_fails_by_name(self, monkeypatch):
        contract = _contract()
        _stub_callers(monkeypatch, contract, missing={
            PARK: ["scripts/code_owner_hold.py#_cmd_hold"]})
        found = grl.problems(contract=contract)
        assert _named(found, "code_owner_hold.py#_cmd_hold", "code_owner_hold.py#park",
                      "no longer"), found

    def test_an_unread_module_is_a_problem_naming_it(self, monkeypatch):
        contract = _contract()
        _stub_callers(monkeypatch, contract, unread={
            ESCALATE: ["scripts/planning_escalation.py"]})
        found = grl.problems(contract=contract)
        assert _named(found, "scripts/planning_escalation.py", "could not"), found

    def test_one_hop_a_handler_s_steps_are_never_on_escalate_s_record(self, monkeypatch):
        # A step running `planning_route.py exit` reaches escalate through
        # _cmd_exit. Put it on escalate's answer and it is an undeclared caller
        # THERE: the hop is applied, never flattened.
        contract = _contract()
        _stub_callers(monkeypatch, contract, extra={
            ESCALATE: [".github/workflows/plan.yml#Roll-up route — hand off"]})
        found = grl.problems(contract=contract)
        assert _named(found, "plan.yml#Roll-up route — hand off",
                      "planning_escalation.py#escalate", "does not declare"), found

    def test_one_hop_a_handler_s_steps_are_reconciled_on_its_own_record(self, monkeypatch):
        contract = _contract()
        _stub_callers(monkeypatch, contract, missing={
            CMD_EXIT: [".github/workflows/plan.yml#One-off route — checked on the way out"]})
        found = grl.problems(contract=contract)
        assert _named(found, "plan.yml#One-off route — checked on the way out",
                      "planning_route.py#_cmd_exit", "no longer"), found
        assert not _named(found, "planning_escalation.py#escalate")
