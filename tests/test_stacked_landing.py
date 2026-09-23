"""When a stacked parent LANDS, the cards it was carrying go Done (DRE-4650).

The sibling card (DRE-4647) stopped the false Done: a pull request merged into
another agent's branch puts its code on no default branch, so `linear-sync`
leaves the card alone and posts a `🪜 Merged into` receipt. That is half the
story. The other half is the moment the parent finally lands — on 2026-09-22 at
09:57 PT agent-bureau #2690 (`agent/DRE-4534-record-reads`) merged into `main`
carrying #2691 and #2692 with it, and THEIR code reached `main` at that instant.
Nothing moved their cards. With the sibling in place DRE-4535 and DRE-4536 would
have sat In Review forever, holding a receipt and waiting on a closer that did
not exist.

The closer is keyed on GitHub, never on the receipt: `gh pr list --state merged
--base <landed head>` is the list of pull requests whose code just landed, and a
card with the receipt and a card without it are closed the same way.

What this file pins:

1. **The incident** — the two children of `agent/DRE-4534-record-reads` are
   closed with their own pull request URLs, and nothing else is.
2. **The same anchored own-branch rule the card-done step uses** — a hand-named
   `ops/...` head closes nothing, a listed base that is not the landed head
   closes nothing, and a card that is already terminal is skipped with a line
   saying so.
3. **One level of recursion per landed head** — the grandchild stacked on a
   child is closed too, the depth is bounded, and a cycle terminates.
4. **The step order** — the closer runs in the `card-done` job, only on a
   default-branch merge, after the merged card's own `card-done` and before the
   merge-sweep gate.
5. **Fail closed** — a GitHub read failure exits 0, says "could not list stacked
   pull requests", and closes nothing. No card is guessed Done.

Live-extraction style (pattern: tests/test_linear_sync_stacked_base.py and
tests/test_linear_sync_workflow.py): the scenario tests EXECUTE the shipped
script and the shipped `run:` block against stub scripts that record their
argv, so what is counted is what the workflow would really call.

Run: python3 -m pytest tests/test_stacked_landing.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
WORKFLOWS = ROOT / ".github" / "workflows"
LINEAR_SYNC = WORKFLOWS / "linear-sync.yml"

sys.path.insert(0, str(SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import stacked_landing  # noqa: E402

#: The step the closer runs inside, by its own name in the workflow.
DONE_STEP = "Card → Done"

#: The fenced region this card adds to that step, executed verbatim below.
MARKER_OPEN = "# >>> DRE-4650 stacked landing"
MARKER_CLOSE = "# <<< DRE-4650 stacked landing"

#: The 2026-09-22 landing, verbatim.
REPO = "agent-bureau"
PARENT_HEAD = "agent/DRE-4534-record-reads"
PARENT_CARD = "DRE-4534"
CHILD_A_HEAD = "agent/DRE-4535-runner-day-from-record"
CHILD_B_HEAD = "agent/DRE-4536-runner-queue-from-record"
CHILD_A_CARD = "DRE-4535"
CHILD_B_CARD = "DRE-4536"
GRANDCHILD_HEAD = "agent/DRE-4537-runner-week-from-record"
GRANDCHILD_CARD = "DRE-4537"


def pr(number: int, head: str, base: str) -> dict:
    """One row of `gh pr list --json number,headRefName,baseRefName,url`."""
    return {
        "number": number,
        "headRefName": head,
        "baseRefName": base,
        "url": f"https://github.com/dreadnought-foundry/agent-bureau/pull/{number}",
    }


#: The landing as GitHub reports it: two merged children under the parent head.
INCIDENT = {
    PARENT_HEAD: [
        pr(2691, CHILD_A_HEAD, PARENT_HEAD),
        pr(2692, CHILD_B_HEAD, PARENT_HEAD),
    ],
}

#: The same landing with #2697 stacked on #2691 — the grandchild case.
WITH_GRANDCHILD = {
    **INCIDENT,
    CHILD_A_HEAD: [pr(2697, GRANDCHILD_HEAD, CHILD_A_HEAD)],
}

#: A `gh` that answers `pr list --base <ref>` off a fixture and records argv.
#: A base named in the fixture's "fail" list exits non-zero, which is the only
#: way a live `gh` failure can be replayed.
GH_STUB = '''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["GH_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(argv) + "\\n")
fixture = json.load(open(os.environ["PR_FIXTURE"], encoding="utf-8"))
if argv[:2] != ["pr", "list"]:
    sys.stderr.write("unexpected gh call: %r\\n" % (argv,))
    sys.exit(9)
base = argv[argv.index("--base") + 1]
if base in fixture.get("fail", []):
    sys.stderr.write("gh: could not read the pull request list (HTTP 503)\\n")
    sys.exit(1)
print(json.dumps(fixture.get("merged", {}).get(base, [])))
'''

#: `linear_ops.py` as the closer meets it, in BOTH of its roles: imported for
#: the one card-state read, and executed for `card-done`. The CLI arm records
#: its argv and answers nothing, so a call is visible and inert.
FAKE_LINEAR_OPS = '''
import json, os, sys


class LinearError(Exception):
    pass


class LinearRateLimited(LinearError):
    pass


def gql(query, variables=None):
    states = json.load(open(os.environ["CARD_STATES"], encoding="utf-8"))
    identifier = (variables or {}).get("id")
    if identifier in states.get("rate_limited", []):
        raise LinearRateLimited("the Linear quota is exhausted")
    if identifier in states.get("unreadable", []):
        raise LinearError("Linear said no")
    name = states.get("states", {}).get(identifier)
    if name is None:
        return {"issue": None}
    return {"issue": {"identifier": identifier, "state": {"name": name}}}


if __name__ == "__main__":
    with open(os.environ["CALL_LOG"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(
            {"script": "linear_ops.py", "args": sys.argv[1:]}) + "\\n")
'''


def _executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


class LandingScenario:
    """One execution of the shipped `stacked_landing.py` against a fixture."""

    def __init__(self, td: str, merged: dict, *, fail=(), states=None,
                 rate_limited=(), unreadable=()):
        self.dir = Path(td)
        self.scripts = self.dir / "scripts"
        shutil.copytree(SCRIPTS, self.scripts,
                        ignore=shutil.ignore_patterns("__pycache__"))
        # The one script the closer both imports and runs, stubbed in place —
        # exactly where it sits in the live checkout, so the shipped
        # `sys.path` insert and the shipped subprocess path both find it.
        (self.scripts / "linear_ops.py").write_text(FAKE_LINEAR_OPS, encoding="utf-8")

        self.bin = self.dir / "bin"
        self.bin.mkdir()
        _executable(self.bin / "gh", GH_STUB)

        self.fixture = self.dir / "prs.json"
        self.fixture.write_text(json.dumps({"merged": merged, "fail": list(fail)}))
        self.states = self.dir / "states.json"
        self.states.write_text(json.dumps({
            "states": states or {},
            "rate_limited": list(rate_limited),
            "unreadable": list(unreadable),
        }))
        self.log = self.dir / "calls.jsonl"
        self.gh_log = self.dir / "gh.jsonl"

    def run(self, *argv: str) -> subprocess.CompletedProcess:
        self.proc = subprocess.run(  # nosec B603 — fixed argv, test-local script
            [sys.executable, str(self.scripts / "stacked_landing.py"), *argv],
            cwd=self.dir, capture_output=True, text=True,
            env={
                "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                "HOME": str(self.dir),
                "LINEAR_API_KEY": "test-key",
                "GH_TOKEN": "test",
                "PR_FIXTURE": str(self.fixture),
                "CARD_STATES": str(self.states),
                "CALL_LOG": str(self.log),
                "GH_LOG": str(self.gh_log),
            },
        )
        return self.proc

    def landed(self, head: str = PARENT_HEAD, *, repo: str = REPO,
               default_branch: str = "main") -> subprocess.CompletedProcess:
        return self.run("landed", repo, head, default_branch)

    def _read(self, path: Path) -> list:
        if not path.exists():
            return []
        return [json.loads(ln) for ln in path.read_text().splitlines() if ln]

    @property
    def closed(self) -> list[list[str]]:
        """Every `linear_ops.py card-done <card> <url>` argv, in order."""
        return [
            c["args"] for c in self._read(self.log)
            if c["args"][:1] == ["card-done"]
        ]

    @property
    def cards_closed(self) -> list[str]:
        return [args[1] for args in self.closed]

    @property
    def bases_listed(self) -> list[str]:
        """Every base `gh pr list` was asked about, in order."""
        return [argv[argv.index("--base") + 1] for argv in self._read(self.gh_log)]


# --------------------------------------------------------------------------- #
# 1. the incident                                                              #
# --------------------------------------------------------------------------- #
class TheLandingClosesTheCardsItCarried(unittest.TestCase):
    """The 2026-09-22 landing: #2690 reached `main` carrying #2691 and #2692."""

    def _landed(self, td):
        scenario = LandingScenario(td, INCIDENT)
        proc = scenario.landed()
        self.assertEqual(0, proc.returncode, proc.stderr)
        return scenario, proc

    def test_both_carried_cards_are_closed(self):
        with tempfile.TemporaryDirectory() as td:
            scenario, _ = self._landed(td)
            self.assertEqual(
                [CHILD_A_CARD, CHILD_B_CARD], sorted(scenario.cards_closed)
            )

    def test_each_card_is_closed_on_its_own_pull_request_url(self):
        with tempfile.TemporaryDirectory() as td:
            scenario, _ = self._landed(td)
            self.assertEqual(
                {
                    CHILD_A_CARD:
                        "https://github.com/dreadnought-foundry/agent-bureau/pull/2691",
                    CHILD_B_CARD:
                        "https://github.com/dreadnought-foundry/agent-bureau/pull/2692",
                },
                {args[1]: args[2] for args in scenario.closed},
            )

    def test_nothing_else_is_closed(self):
        """Above all not the parent: its own `card-done` ran in the step
        before this one, and closing it twice would comment twice."""
        with tempfile.TemporaryDirectory() as td:
            scenario, _ = self._landed(td)
            self.assertNotIn(PARENT_CARD, scenario.cards_closed)
            self.assertEqual(2, len(scenario.closed), scenario.closed)

    def test_the_repo_and_the_landed_head_reach_gh_verbatim(self):
        with tempfile.TemporaryDirectory() as td:
            scenario, _ = self._landed(td)
            first = scenario._read(scenario.gh_log)[0]
        self.assertIn("--repo", first)
        self.assertEqual(REPO, first[first.index("--repo") + 1])
        self.assertEqual(PARENT_HEAD, first[first.index("--base") + 1])
        self.assertIn("merged", first)

    def test_the_closures_are_logged(self):
        """The job log is the only place a person sees that a landing closed
        cards other than the one that merged."""
        with tempfile.TemporaryDirectory() as td:
            _, proc = self._landed(td)
        self.assertIn(CHILD_A_CARD, proc.stdout)
        self.assertIn(CHILD_B_CARD, proc.stdout)


# --------------------------------------------------------------------------- #
# 2. the same anchored own-branch rule                                         #
# --------------------------------------------------------------------------- #
class OnlyACardsOwnBranchIsClosed(unittest.TestCase):
    """The rule is the card-done step's, character for character: anchored
    `agent/DRE-<n>-` or `repair/DRE-<n>-`, delimiter required."""

    def test_the_rule_matches_the_two_prefixes(self):
        self.assertEqual("DRE-4535", stacked_landing.card_from_branch(CHILD_A_HEAD))
        self.assertEqual(
            "DRE-9", stacked_landing.card_from_branch("repair/DRE-9-abc123def456")
        )

    def test_the_delimiter_is_required(self):
        """DRE-142 can never act for DRE-1428 (DRE-2025)."""
        self.assertIsNone(stacked_landing.card_from_branch("agent/DRE-1428"))
        self.assertEqual(
            "DRE-1428", stacked_landing.card_from_branch("agent/DRE-1428-slug")
        )

    def test_the_rule_is_anchored_and_case_insensitive(self):
        self.assertIsNone(
            stacked_landing.card_from_branch("wip/agent/DRE-4535-runner-day")
        )
        self.assertEqual(
            "DRE-4535", stacked_landing.card_from_branch("Agent/dre-4535-runner-day")
        )

    def test_a_hand_named_branch_closes_nothing(self):
        """`ops/...` is the operator's, and the operator closes those cards."""
        merged = {PARENT_HEAD: [pr(2693, "ops/tidy-the-runner", PARENT_HEAD)]}
        with tempfile.TemporaryDirectory() as td:
            scenario = LandingScenario(td, merged)
            proc = scenario.landed()
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual([], scenario.closed)

    def test_a_row_whose_base_is_not_the_landed_head_closes_nothing(self):
        """GitHub's `--base` filter is not taken on trust: a row that names
        another base did not land with this head, whatever the query said."""
        merged = {
            PARENT_HEAD: [
                pr(2694, CHILD_A_HEAD, "some/other-branch"),
                pr(2692, CHILD_B_HEAD, PARENT_HEAD),
            ]
        }
        with tempfile.TemporaryDirectory() as td:
            scenario = LandingScenario(td, merged)
            proc = scenario.landed()
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual([CHILD_B_CARD], scenario.cards_closed)

    def test_a_card_already_done_is_skipped_with_a_log_line(self):
        """Idempotent: a re-run of the job must not re-close what it closed."""
        with tempfile.TemporaryDirectory() as td:
            scenario = LandingScenario(
                td, INCIDENT, states={CHILD_A_CARD: "Done"}
            )
            proc = scenario.landed()
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual([CHILD_B_CARD], scenario.cards_closed)
        self.assertIn(CHILD_A_CARD, proc.stdout)
        self.assertIn("Done", proc.stdout)

    def test_a_canceled_card_is_left_alone_too(self):
        """`card-done`'s state write treats Done as a terminal target and
        writes it unguarded, so a Canceled card would be resurrected — the
        skip is what stops that, and it is the same terminal set the
        dependency gate reads."""
        with tempfile.TemporaryDirectory() as td:
            scenario = LandingScenario(
                td, INCIDENT, states={CHILD_A_CARD: "Canceled"}
            )
            scenario.landed()
            self.assertEqual([CHILD_B_CARD], scenario.cards_closed)

    def test_the_terminal_set_is_not_restated_here(self):
        import prose_blockers

        self.assertEqual(tuple(prose_blockers.TERMINAL), tuple(stacked_landing.TERMINAL))

    def test_an_unreadable_card_is_still_closed(self):
        """"We could not look" is not "it is already Done" — and `card-done`
        carries the no-code / DEMO: / epic guard that actually decides."""
        with tempfile.TemporaryDirectory() as td:
            scenario = LandingScenario(td, INCIDENT, unreadable=[CHILD_A_CARD])
            scenario.landed()
            self.assertEqual(
                [CHILD_A_CARD, CHILD_B_CARD], sorted(scenario.cards_closed)
            )

    def test_a_rate_limited_read_closes_nothing_for_that_card(self):
        """The one unknown that falls CLOSED: a write against an exhausted
        quota cannot succeed and deepens it (DRE-1921)."""
        with tempfile.TemporaryDirectory() as td:
            scenario = LandingScenario(td, INCIDENT, rate_limited=[CHILD_A_CARD])
            proc = scenario.landed()
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual([CHILD_B_CARD], scenario.cards_closed)

    def test_the_landed_head_may_not_be_the_default_branch(self):
        """Basing the listing on `main` would name every merged pull request
        the repository has ever had."""
        with tempfile.TemporaryDirectory() as td:
            scenario = LandingScenario(td, {"main": [pr(1, CHILD_A_HEAD, "main")]})
            proc = scenario.landed(head="main")
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual([], scenario.closed)
            self.assertEqual([], scenario.bases_listed)


# --------------------------------------------------------------------------- #
# 3. one level of recursion per landed head                                    #
# --------------------------------------------------------------------------- #
class AGrandchildLandsWithItsGrandparent(unittest.TestCase):
    """#2697 was stacked on #2691, which was stacked on #2690. When #2690
    lands, all three are on the default branch."""

    def test_the_grandchild_is_closed(self):
        with tempfile.TemporaryDirectory() as td:
            scenario = LandingScenario(td, WITH_GRANDCHILD)
            proc = scenario.landed()
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual(
                [CHILD_A_CARD, CHILD_B_CARD, GRANDCHILD_CARD],
                sorted(scenario.cards_closed),
            )

    def test_every_landed_head_is_asked_about_exactly_once(self):
        with tempfile.TemporaryDirectory() as td:
            scenario = LandingScenario(td, WITH_GRANDCHILD)
            scenario.landed()
            listed = scenario.bases_listed
        self.assertEqual(sorted(listed), sorted(set(listed)), listed)
        self.assertEqual(
            {PARENT_HEAD, CHILD_A_HEAD, CHILD_B_HEAD, GRANDCHILD_HEAD}, set(listed)
        )

    def test_the_depth_is_bounded(self):
        """A chain longer than the bound stops, says so, and closes only what
        it walked — an unbounded walk is a GitHub read per level, forever."""
        chain = {}
        cards = []
        previous = PARENT_HEAD
        for i in range(stacked_landing.MAX_DEPTH + 3):
            head = f"agent/DRE-{9000 + i}-link-{i}"
            chain[previous] = [pr(3000 + i, head, previous)]
            cards.append(f"DRE-{9000 + i}")
            previous = head
        with tempfile.TemporaryDirectory() as td:
            scenario = LandingScenario(td, chain)
            proc = scenario.landed()
            closed = scenario.cards_closed
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(cards[:stacked_landing.MAX_DEPTH], closed)
        self.assertIn("depth", proc.stdout)

    def test_a_cycle_terminates(self):
        """GitHub cannot really make one, and a walk that would not survive it
        is a walk that hangs the merge path."""
        cyclic = {
            PARENT_HEAD: [pr(2691, CHILD_A_HEAD, PARENT_HEAD)],
            CHILD_A_HEAD: [pr(2690, PARENT_HEAD, CHILD_A_HEAD)],
        }
        with tempfile.TemporaryDirectory() as td:
            scenario = LandingScenario(td, cyclic)
            proc = scenario.landed()
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual([CHILD_A_CARD], scenario.cards_closed)
            self.assertEqual([PARENT_HEAD, CHILD_A_HEAD], scenario.bases_listed)

    def test_the_bound_is_small_and_fixed(self):
        self.assertIsInstance(stacked_landing.MAX_DEPTH, int)
        self.assertGreaterEqual(stacked_landing.MAX_DEPTH, 2)
        self.assertLessEqual(stacked_landing.MAX_DEPTH, 10)


# --------------------------------------------------------------------------- #
# 5. fail closed                                                               #
# --------------------------------------------------------------------------- #
class AGitHubReadFailureClosesNothing(unittest.TestCase):
    """No card is guessed Done. A read we could not make lists nothing, and
    the run says so rather than exiting red on the merge path."""

    def test_the_run_exits_zero_and_closes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            scenario = LandingScenario(td, INCIDENT, fail=[PARENT_HEAD])
            proc = scenario.landed()
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual([], scenario.closed)

    def test_it_says_so_explicitly(self):
        with tempfile.TemporaryDirectory() as td:
            scenario = LandingScenario(td, INCIDENT, fail=[PARENT_HEAD])
            proc = scenario.landed()
        said = proc.stdout + proc.stderr
        self.assertIn("could not list stacked pull requests", said)
        self.assertIn(PARENT_HEAD, said)

    def test_a_failure_deeper_in_the_walk_keeps_what_it_proved(self):
        """The children really did land; only the grandchildren are unknown."""
        with tempfile.TemporaryDirectory() as td:
            scenario = LandingScenario(td, WITH_GRANDCHILD, fail=[CHILD_A_HEAD])
            proc = scenario.landed()
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual(
                [CHILD_A_CARD, CHILD_B_CARD], sorted(scenario.cards_closed)
            )
        self.assertIn("could not list stacked pull requests", proc.stdout + proc.stderr)

    def test_the_message_is_the_module_s_own_constant(self):
        self.assertEqual(
            "could not list stacked pull requests", stacked_landing.COULD_NOT_LIST
        )


# --------------------------------------------------------------------------- #
# 4. the step: only on a landing, and in this order                            #
# --------------------------------------------------------------------------- #
#: A stand-in for every pipeline script the step can call. It records its own
#: name and argv and answers nothing, so a call is visible and inert.
RECORDING_STUB = (
    "import json, os, sys\n"
    "with open(os.environ['CALL_LOG'], 'a', encoding='utf-8') as fh:\n"
    "    fh.write(json.dumps({'script': os.path.basename(sys.argv[0]),\n"
    "                         'args': sys.argv[1:]}) + '\\n')\n"
)

#: The merge-sweep gate, stubbed to emit one flag — so the sweep that follows
#: it is visible in the same log as a `reconcile.py` call.
SWEEP_GATE_STUB = RECORDING_STUB + "print('--promote-only')\n"


def done_step() -> dict:
    for job in yaml.safe_load(LINEAR_SYNC.read_text())["jobs"].values():
        for step in job.get("steps") or []:
            if step.get("name") == DONE_STEP:
                return step
    raise AssertionError(f"{DONE_STEP!r} is gone from linear-sync.yml")


def card_done_job() -> dict:
    return yaml.safe_load(LINEAR_SYNC.read_text())["jobs"]["card-done"]


class StepScenario:
    """One execution of the shipped `Card → Done` script (the harness of
    tests/test_linear_sync_stacked_base.py, with the closer stubbed too)."""

    def __init__(self, td: str):
        self.dir = Path(td)
        self.scripts = self.dir / ".bureau-pipeline" / "scripts"
        self.scripts.mkdir(parents=True)
        for name in ("linear_ops.py", "dependabot_card.py", "reconcile.py",
                     "stacked_landing.py"):
            (self.scripts / name).write_text(RECORDING_STUB, encoding="utf-8")
        (self.scripts / "merge_sweep_gate.py").write_text(
            SWEEP_GATE_STUB, encoding="utf-8"
        )
        self.log = self.dir / "calls.jsonl"

    def run(self, *, head_ref: str, base_ref: str, default_branch: str
            ) -> subprocess.CompletedProcess:
        script = self.dir / "card-done.sh"
        script.write_text(done_step()["run"], encoding="utf-8")
        # `bash -e`, the runner's own flags for a `run:` block.
        self.proc = subprocess.run(  # nosec B603 — fixed argv, test-local script
            ["bash", "-e", str(script)],
            cwd=self.dir, capture_output=True, text=True,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(self.dir),
                "GITHUB_REPOSITORY": "dreadnought-foundry/agent-bureau",
                "LINEAR_API_KEY": "test-key",
                "GH_TOKEN": "test",
                "HEAD_REF": head_ref,
                "PR_URL": "https://github.com/dreadnought-foundry/agent-bureau/pull/2690",
                "PR_BODY": "",
                "PR_AUTHOR": "agent-bureau-bot",
                "MAX_WIP": "8",
                "BASE_REF": base_ref,
                "DEFAULT_BRANCH": default_branch,
                "CALL_LOG": str(self.log),
            },
        )
        return self.proc

    @property
    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(ln) for ln in self.log.read_text().splitlines() if ln]

    @property
    def order(self) -> list[str]:
        """The scripts the step called, in order, `linear_ops.py` qualified by
        its subcommand — the whole of what this criterion asks."""
        return [
            f"{c['script']} {c['args'][0]}" if c["script"] == "linear_ops.py"
            else c["script"]
            for c in self.calls
        ]


class TheCloserRunsOnALandingInOrder(unittest.TestCase):
    """In the `card-done` job, only on a default-branch merge, after the
    merged card's own `card-done` and before the merge-sweep gate — so
    `--promote-only` sees the whole set of cards this landing made Done."""

    def _landed(self, td):
        scenario = StepScenario(td)
        proc = scenario.run(
            head_ref=PARENT_HEAD, base_ref="main", default_branch="main"
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        return scenario

    def test_the_order_is_card_done_then_the_closer_then_the_sweep_gate(self):
        with tempfile.TemporaryDirectory() as td:
            order = self._landed(td).order
        self.assertEqual(
            ["linear_ops.py card-done", "stacked_landing.py",
             "merge_sweep_gate.py", "reconcile.py"],
            order,
        )

    def test_the_closer_is_given_the_repo_the_head_and_the_default_branch(self):
        with tempfile.TemporaryDirectory() as td:
            calls = [c for c in self._landed(td).calls
                     if c["script"] == "stacked_landing.py"]
        self.assertEqual(1, len(calls), calls)
        self.assertEqual(
            ["landed", "dreadnought-foundry/agent-bureau", PARENT_HEAD, "main"],
            calls[0]["args"],
        )

    def test_a_stacked_merge_never_reaches_the_closer(self):
        """A merge into a side branch landed nothing, so there is nothing for
        the closer to find — the DRE-4647 gate exits before it."""
        with tempfile.TemporaryDirectory() as td:
            scenario = StepScenario(td)
            proc = scenario.run(
                head_ref=CHILD_A_HEAD, base_ref=PARENT_HEAD, default_branch="main"
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual(
                [], [c for c in scenario.calls if c["script"] == "stacked_landing.py"]
            )

    def test_a_branch_no_card_owns_never_reaches_the_closer_either(self):
        """The head ref gave no card, so the step exits at the DRE-2027 arm —
        after the dependabot lookup and before everything this card added.
        A hand-named branch merged to the default branch closes nothing, and
        a landing it did not make closes nothing either."""
        with tempfile.TemporaryDirectory() as td:
            scenario = StepScenario(td)
            proc = scenario.run(
                head_ref="ops/tidy-things", base_ref="main", default_branch="main"
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual(["dependabot_card.py"], scenario.order)

    def test_the_fenced_region_is_in_the_shipped_step(self):
        run = done_step()["run"]
        self.assertIn(MARKER_OPEN, run)
        self.assertIn(MARKER_CLOSE, run)
        self.assertLess(run.index(MARKER_OPEN), run.index(MARKER_CLOSE))

    def test_the_run_block_interpolates_nothing(self):
        """A branch name is attacker-nameable: every value the closer is given
        reaches the shell through `env:`, the rule `PR_BODY` already follows."""
        self.assertNotIn("${{", done_step()["run"])


class TheCloserHasAGitHubCredential(unittest.TestCase):
    """`gh pr list` is a read of another identity's pull requests, and the
    `card-done` job minted no token at all before this card."""

    def _steps(self) -> list[dict]:
        return card_done_job()["steps"]

    def test_the_job_mints_a_token_before_the_done_step(self):
        names = [s.get("name") or s.get("uses") or "" for s in self._steps()]
        minting = [
            i for i, s in enumerate(self._steps())
            if "create-github-app-token" in str(s.get("uses") or "")
        ]
        self.assertEqual(1, len(minting), names)
        self.assertLess(minting[0], names.index(DONE_STEP), names)

    def test_the_mint_uses_the_qa_bot_app(self):
        step = next(s for s in self._steps()
                    if "create-github-app-token" in str(s.get("uses") or ""))
        with_ = step.get("with") or {}
        self.assertIn("BUREAU_QA_APP_ID", str(with_.get("app-id")))
        self.assertIn("BUREAU_QA_APP_PRIVATE_KEY", str(with_.get("private-key")))

    def test_a_missing_credential_never_fails_the_merge_path(self):
        """The card going Done must not depend on a repo having installed the
        qa-bot App: a mint that fails is survived, and the closer then fails
        closed on the `gh` read like any other unreadable listing."""
        step = next(s for s in self._steps()
                    if "create-github-app-token" in str(s.get("uses") or ""))
        self.assertIs(True, step.get("continue-on-error"))

    def test_the_done_step_reads_that_token(self):
        env = done_step().get("env") or {}
        self.assertIn("outputs.token", str(env.get("GH_TOKEN")))


if __name__ == "__main__":
    unittest.main()
