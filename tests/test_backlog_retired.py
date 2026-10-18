"""Backlog retires (DRE-6753, epic DRE-4125) — the second step of the
protocol the lane contract defines for any retirement (DRE-2726).

The code stopped writing the lane first: DRE-6742 moved the planning exit to
`Queued`, and every writer the earlier cards converted moved with it. This
card is the second step and the declaration that drives it: the contract
marks Backlog `retiring`, replaced by `Queued`, and `reconcile.
drain_retiring_lanes` moves every card still there into `Queued` on every
repo's sweep. The board step is the third (DRE-6754).

There is nothing for Linear to archive. A team keeps at least one
backlog-type status, and Backlog is the DRE team's only one, so the state
stays on the board, empty, and the entry stays with it: the drain keeps the
lane empty and `board.retiring_lane_is_empty` keeps asserting it. The alias
`Backlog → Queued` stays too, so a reader still resolves the old name.

These tests pin the entry, and then the sweep in the shape DRE-2722's
`test_green_light_rename.py` set: the old name survives in no string constant
under `scripts/`, in no non-comment line under `.github/workflows/`, and in
`config/` only on a line carrying the shim marker or inside the retiring
entry itself. `docs/`, `architecture/`, `tests/fixtures/` and comments are
history and are not swept. The needle is assembled from two fragments so this
file never matches itself.
"""
import ast
import json
import os
import subprocess  # nosec B404 — fixed-arg calls to git and this repo's CLI only
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lane_contract  # noqa: E402
import step_shell  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

#: The retiring lane name, assembled so this sweep never matches its own source.
OLD_LANE = "Back" + "log"
NEW_LANE = "Queued"
EPIC = "DRE-4125"

#: The marker that exempts a config line: the alias declares its intent on
#: the line itself, never by a path allow-list (DRE-2722).
SHIM_MARKER = "lane-rename-shim " + EPIC

SCRIPTS = "scripts/"
WORKFLOWS = ".github/workflows/"
CONFIG = "config/"


# --------------------------------------------------------------------------- #
# the readers the sweep is built from                                          #
# --------------------------------------------------------------------------- #


def tracked_files(prefix: str) -> list:
    """Every file git tracks under `prefix` — the repo's own content, never the
    runtime `.bureau-pipeline/` checkout a self-hosted run drops beside it."""
    out = subprocess.run(  # nosec B603 B607 — fixed args, no shell
        ["git", "ls-files", "-z", "--", prefix],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    files = [p for p in out.stdout.split("\0") if p]
    assert files, f"git ls-files found nothing under {prefix} — cannot sweep"
    return files


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
        return None  # binaries and submodule entries name no lane


def python_constants(source: str, needle: str) -> list:
    """Line numbers of every string constant naming `needle`, docstrings
    included. A `#` comment recording an incident is not a constant."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [0]  # unreadable is never a pass
    return sorted({
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and needle in node.value
    })


def uncommented_lines(text: str, needle: str) -> list:
    """Line numbers of every line naming `needle` that is not a comment."""
    return [
        i for i, line in enumerate(text.splitlines(), 1)
        if needle in line and not line.lstrip().startswith("#")
    ]


def entry_span(text: str, name: str) -> tuple:
    """The 1-based line range of the contract's retiring entry for `name`, read
    by decoding the JSON object that holds it — never a line count kept here.
    `(0, -1)`, an empty range, when no such entry is in the text."""
    decoder = json.JSONDecoder()
    marker = f'"name": "{name}"'
    at = text.find(marker)
    while at != -1:
        start = text.rfind("{", 0, at)
        while start != -1:
            try:
                value, end = decoder.raw_decode(text, start)
            except ValueError:
                value = None
            if isinstance(value, dict) and value.get("name") == name:
                if value.get("status") == "retiring":
                    first = text.count("\n", 0, start) + 1
                    return first, text.count("\n", 0, end) + 1
                break
            start = text.rfind("{", 0, start)
        at = text.find(marker, at + 1)
    return 0, -1


def config_offenders(rel: str, text: str, needle: str) -> list:
    """Line numbers in a config file naming `needle` that neither carry the
    shim marker nor sit inside the retiring entry."""
    first, last = entry_span(text, needle)
    return [
        i for i, line in enumerate(text.splitlines(), 1)
        if needle in line and SHIM_MARKER not in line and not first <= i <= last
    ]


def sweep(root: str = ROOT, needle: str = OLD_LANE, files=None) -> list:
    """`path:line` for every place the old name survives that this card forbids.

    `files` is the repo-relative list to read; by default every tracked file
    under the three swept trees.
    """
    if files is None:
        files = tracked_files(SCRIPTS) + tracked_files(WORKFLOWS) + tracked_files(CONFIG)
    hits = []
    for rel in files:
        path = os.path.join(root, rel)
        text = _read(path)
        if text is None or needle not in text:
            continue
        if rel.startswith(SCRIPTS):
            lines = (python_constants(text, needle) if rel.endswith(".py")
                     else uncommented_lines(text, needle))
        elif rel.startswith(WORKFLOWS):
            if rel.endswith((".yml", ".yaml")):
                text = step_shell.workflow_source(path, root=root)
            lines = uncommented_lines(text, needle)
        elif rel.startswith(CONFIG):
            lines = config_offenders(rel, text, needle)
        else:
            lines = []
        hits += [f"{rel}:{n}" for n in lines]
    return hits


# --------------------------------------------------------------------------- #
# the entry                                                                    #
# --------------------------------------------------------------------------- #


def raw_entry():
    return next(e for e in lane_contract.load()["lanes"] if e["name"] == OLD_LANE)


class TestTheContractMarksItRetiring:
    def test_the_retiring_set_is_exactly_the_backlog_entry(self):
        retiring = lane_contract.lanes("retiring")
        assert [entry["name"] for entry in retiring] == [OLD_LANE]

    def test_it_is_replaced_by_queued_and_retired_by_the_epic(self):
        entry = lane_contract.lane(OLD_LANE, status="retiring")
        assert entry["replaced_by"] == NEW_LANE
        assert entry["retired_by"] == EPIC

    def test_it_carries_a_reason_and_a_board_action(self):
        entry = raw_entry()
        assert set(entry) == {
            "name", "status", "retired_by", "replaced_by", "reason", "board_action",
        }
        reason = entry["reason"]
        assert "`backlog` type" in reason
        assert "first on the board whatever its position" in reason
        assert "cannot change a state's type after creation" in reason

    def test_the_board_action_says_the_state_stays_and_so_does_the_entry(self):
        step = raw_entry()["board_action"]
        assert "nothing to archive" in step
        assert "at least one backlog-type status per team" in step
        assert "only one" in step
        assert "Show empty groups" in step
        assert "STAYS" in step
        assert "board.retiring_lane_is_empty" in step
        assert "board.retired_entry_is_deleted" in step

    def test_the_live_lanes_no_longer_include_it(self):
        assert OLD_LANE not in lane_contract.lane_names()
        assert OLD_LANE not in lane_contract.flow_lanes()
        assert NEW_LANE in lane_contract.lane_names()

    def test_the_alias_stays_while_the_state_stays(self):
        assert lane_contract.aliases()[OLD_LANE] == NEW_LANE
        assert OLD_LANE in lane_contract.QUEUE_LANES

    def test_the_drain_reads_exactly_this_entry(self):
        # The sweep's drain keys on a retiring lane with a replacement; this is
        # the one it finds.
        draining = [e for e in lane_contract.lanes("retiring") if e.get("replaced_by")]
        assert [(e["name"], e["replaced_by"]) for e in draining] == [(OLD_LANE, NEW_LANE)]


class TestTheHarness:
    def board(self, **counts):
        board = {name: 0 for name in lane_contract.lane_names()}
        board.update(counts)
        return board

    def test_the_offline_check_passes(self):
        # Every clause the CLI can assert without the board, run as the CLI.
        # `LINEAR_API_KEY` is stripped: the board clauses are reported as
        # unreadable, never as a pass, and only those may fail here.
        env = {k: v for k, v in os.environ.items() if k != "LINEAR_API_KEY"}
        done = subprocess.run(  # nosec B603 — fixed argv, no shell
            [sys.executable, os.path.join(ROOT, "scripts", "lane_contract.py"), "check"],
            capture_output=True, text=True, cwd=ROOT, env=env,
        )
        assert done.returncode in (0, 1), done.stdout + done.stderr
        assert "Traceback" not in done.stderr, done.stderr
        offenders = [
            line for line in done.stdout.splitlines()
            if line.lstrip().startswith("[FAIL")
            and "the board could not be read" not in line
        ]
        assert offenders == [], offenders

    def test_the_vocabulary_rule_passes_on_this_checkout(self):
        report = lane_contract.check(vocabulary=lane_contract.pipeline_vocabulary())
        failed = {f.clause_id for f in report.failures()}
        assert "pipeline.vocabulary_is_contract_lanes" not in failed
        assert OLD_LANE not in lane_contract.pipeline_vocabulary()

    def test_the_rule_would_fail_on_a_script_still_naming_it(self):
        report = lane_contract.check(vocabulary={OLD_LANE})
        assert "pipeline.vocabulary_is_contract_lanes" in {
            f.clause_id for f in report.failures()
        }

    def test_an_empty_lane_on_the_board_is_clean(self):
        report = lane_contract.check(board=self.board(**{OLD_LANE: 0}), vocabulary=())
        failed = {f.clause_id for f in report.failures()}
        assert "board.retired_entry_is_deleted" not in failed
        assert "board.every_state_is_named" not in failed

    def test_a_card_left_there_is_named(self):
        # Promised from Phase 3; the assertion itself is what the board step
        # leans on, so it is exercised here directly.
        inputs = lane_contract._Inputs(
            contract=lane_contract.load(), board=self.board(**{OLD_LANE: 2}),
            console=None, vocabulary=None, root=ROOT,
        )
        problems = lane_contract.ASSERTIONS["board.retiring_lane_is_empty"](inputs)
        assert len(problems) == 1 and repr(OLD_LANE) in problems[0]

    def test_the_entry_must_go_only_once_linear_drops_the_state(self):
        board = self.board()
        assert OLD_LANE not in board
        report = lane_contract.check(board=board, vocabulary=())
        assert "board.retired_entry_is_deleted" in {
            f.clause_id for f in report.failures()
        }


class TestTheReviewStepNoLongerNamesIt:
    def test_the_pre_review_lane_list_drops_it(self):
        text = step_shell.workflow_source(
            os.path.join(ROOT, ".github", "workflows", "qa-review.yml"), root=ROOT
        )
        csv = '"Intake,Triage,Planning,Queued,Todo,Hand-work,In Progress"'
        assert csv in text
        assert uncommented_lines(text, OLD_LANE) == []


class TestTheDocument:
    def test_the_rendered_document_carries_the_retiring_section(self):
        with open(lane_contract.DOC_PATH, encoding="utf-8") as fh:
            text = fh.read()
        assert text == lane_contract.render_markdown()
        assert f"### {OLD_LANE} — retired by {EPIC}" in text
        live = text.split("\n## The lanes\n", 1)[1].split("\n## ", 1)[0]
        assert f"\n### {OLD_LANE}\n" not in live


# --------------------------------------------------------------------------- #
# the sweep                                                                    #
# --------------------------------------------------------------------------- #


class TestTheSweep:
    """The old name survives nowhere the pipeline reads it as a lane."""

    def test_no_script_workflow_or_config_line_names_the_old_lane(self):
        offenders = sweep()
        assert offenders == [], (
            f"the retiring lane name survives at: {offenders}. The queue is "
            f"{NEW_LANE!r}; a config line may keep the old name only with the "
            f"marker {SHIM_MARKER!r} or inside the retiring entry."
        )

    def test_the_only_config_survivors_are_the_alias_and_the_entry(self):
        # The exemptions are holes in the sweep, so pin their size.
        rel = "config/lane-contract.json"
        text = _read(os.path.join(ROOT, rel))
        first, last = entry_span(text, OLD_LANE)
        assert first > 0, "the retiring entry was not found"
        survivors = [
            i for i, line in enumerate(text.splitlines(), 1) if OLD_LANE in line
        ]
        shim = [i for i in survivors if not first <= i <= last]
        assert len(shim) == 1
        assert SHIM_MARKER in text.splitlines()[shim[0] - 1]
        others = [
            f for f in tracked_files(CONFIG)
            if f != rel and OLD_LANE in (_read(os.path.join(ROOT, f)) or "")
        ]
        assert others == [], others


class TestTheSweepCatchesWhatItMust:
    """The guard on the guard: a sweep that finds nothing anywhere would pass
    over a tree full of the old name."""

    def plant(self, tmp_path, rel, text):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return sweep(root=str(tmp_path), files=[rel])

    def test_a_string_constant_in_a_script_is_caught(self, tmp_path):
        hits = self.plant(tmp_path, "scripts/probe.py",
                          f'LANES = ("Planning", "{OLD_LANE}")\n')
        assert hits == ["scripts/probe.py:1"]

    def test_a_docstring_in_a_script_is_caught(self, tmp_path):
        hits = self.plant(tmp_path, "scripts/probe.py",
                          f'def f():\n    """Moves a card to {OLD_LANE}."""\n')
        assert hits == ["scripts/probe.py:2"]

    def test_a_comment_in_a_script_is_history(self, tmp_path):
        hits = self.plant(tmp_path, "scripts/probe.py",
                          f"# DRE-1572 looped through {OLD_LANE} five times\nX = 1\n")
        assert hits == []

    def test_a_shell_line_under_scripts_is_caught_and_its_comment_is_not(self, tmp_path):
        hits = self.plant(tmp_path, "scripts/probe.sh",
                          f'# parks in {OLD_LANE}\nlinear state "$CARD" "{OLD_LANE}"\n')
        assert hits == ["scripts/probe.sh:2"]

    def test_a_workflow_line_is_caught_and_its_comment_is_not(self, tmp_path):
        hits = self.plant(tmp_path, ".github/workflows/probe.yml", (
            "jobs:\n  a:\n    steps:\n      - run: |\n"
            f"          # the card sat in {OLD_LANE}\n"
            f'          linear_ops.py advance "$CARD" "In Review" "{OLD_LANE},Todo"\n'
        ))
        assert hits == [".github/workflows/probe.yml:6"]

    def test_a_config_line_without_the_marker_is_caught(self, tmp_path):
        hits = self.plant(tmp_path, "config/probe.json", json.dumps(
            {"why": f"parked in {OLD_LANE} on a date"}, indent=2))
        assert hits == ["config/probe.json:2"]

    def test_a_config_line_with_the_marker_is_exempt(self, tmp_path):
        line = f'{{"from": "{OLD_LANE}", "to": "{NEW_LANE}", "shim": "{SHIM_MARKER}"}}\n'
        assert self.plant(tmp_path, "config/probe.json", line) == []

    def test_a_marker_for_another_rename_does_not_exempt_it(self, tmp_path):
        line = f'{{"from": "{OLD_LANE}", "to": "{NEW_LANE}", "shim": "lane-rename-shim DRE-1"}}\n'
        assert self.plant(tmp_path, "config/probe.json", line) == ["config/probe.json:1"]

    def test_the_retiring_entry_is_exempt_and_a_live_one_is_not(self, tmp_path):
        retiring = {"lanes": [{"name": OLD_LANE, "status": "retiring",
                               "reason": f"{OLD_LANE} goes"}]}
        assert self.plant(tmp_path, "config/probe.json",
                          json.dumps(retiring, indent=2)) == []
        live = {"lanes": [{"name": OLD_LANE, "status": "live",
                           "reason": f"{OLD_LANE} stays"}]}
        hits = self.plant(tmp_path, "config/probe.json", json.dumps(live, indent=2))
        assert len(hits) == 2, hits

    def test_the_entry_span_is_read_off_the_shipped_file(self):
        text = _read(os.path.join(ROOT, "config", "lane-contract.json"))
        first, last = entry_span(text, OLD_LANE)
        block = "\n".join(text.splitlines()[first - 1:last])
        assert json.loads(block.rstrip().rstrip(",")) == raw_entry()
