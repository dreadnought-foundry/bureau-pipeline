"""The hygiene agent's clock (DRE-5369): `.github/workflows/hygiene.yml`.

The core (DRE-5368, `scripts/hygiene.py`) is three commands; this file holds
the workflow that runs them to the command contract the two cards share. Each
test reads the LIVE workflow file, and the two pieces of shell that decide
anything — whether this pass is a dry run, and whether `--dry-run` reaches a
`hygiene.py` call — are EXECUTED here under bash with a stub `python3` on the
PATH, so the answer is what the runner would do and not what the YAML looks
like it says.

The one rule the whole file guards: the clock does not run live on merge. A
scheduled pass is a dry run until the repository variable `HYGIENE_LIVE` is
exactly `true`, and anything the decision cannot read is a dry run too.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
# reconcile reads its repository at import, as tests/test_hygiene.py sets it.
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import check_workflow_watchers  # noqa: E402
import reconcile  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "hygiene.yml"
MEDIC_STUB = ROOT / ".github" / "workflows" / "self-medic.yml"
AGENTS = ROOT / "agents.yaml"

#: Minutes another schedule already owns: the hour itself, the sweep's `*/15`
#: (0, 15, 30, 45), Nightly Watch at `:23`, and every `:41` daily cron.
TAKEN_MINUTES = {"0", "15", "23", "30", "41", "45"}

APP_TOKEN_ACTION = "actions/create-github-app-token"


def _doc():
    return yaml.safe_load(WORKFLOW.read_text())


def _on(doc):
    return check_workflow_watchers.on_block(doc)


def _steps(doc):
    """(job_id, step) for every step in the file, in order."""
    return [(job_id, step)
            for job_id, job in (doc.get("jobs") or {}).items()
            for step in job.get("steps") or []]


def _hygiene_steps(doc, command):
    return [(job_id, step) for job_id, step in _steps(doc)
            if f"hygiene.py {command}" in str(step.get("run") or "")]


def _bash(script, env, cwd):
    """Run one step's `run:` the way the runner does (`bash -e`), with a
    `python3` on the PATH that records its argv instead of running anything."""
    stub_dir = Path(cwd) / ".stub"
    stub_dir.mkdir(exist_ok=True)
    calls = Path(cwd) / ".calls"
    # Fresh per run: a test that runs one step several times in one directory
    # must read only this run's calls, never the last run's as well.
    calls.unlink(missing_ok=True)
    stub = stub_dir / "python3"
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{calls}"\n')
    stub.chmod(0o755)
    output = Path(cwd) / ".github_output"
    output.write_text("")
    full_env = {"PATH": f"{stub_dir}:{os.environ['PATH']}",
                "GITHUB_OUTPUT": str(output), **env}
    result = subprocess.run(["bash", "-e", "-c", script], cwd=cwd, env=full_env,
                            capture_output=True, text=True)
    outputs = dict(line.split("=", 1)
                   for line in output.read_text().splitlines() if "=" in line)
    recorded = calls.read_text().splitlines() if calls.exists() else []
    return result, outputs, recorded


# --------------------------------------------------------------------------- #
# 1. the clock                                                                 #
# --------------------------------------------------------------------------- #


class ClockTest(unittest.TestCase):
    def setUp(self):
        self.doc = _doc()
        self.on = _on(self.doc)

    def test_it_is_named_what_the_medic_watches(self):
        self.assertEqual(self.doc["name"], "Hygiene")

    def test_exactly_one_hourly_cron_on_a_minute_nobody_else_owns(self):
        crons = [entry["cron"] for entry in self.on["schedule"]]
        self.assertEqual(len(crons), 1, crons)
        minute, hour, dom, month, dow = crons[0].split()
        self.assertEqual((hour, dom, month, dow), ("*", "*", "*", "*"),
                         "the hygiene pass is hourly")
        self.assertTrue(minute.isdigit(), f"one minute, not a range: {minute!r}")
        self.assertNotIn(minute, TAKEN_MINUTES)
        self.assertEqual(minute, "37")

    def test_a_pass_in_flight_is_queued_behind_never_doubled(self):
        """A hand dispatch overlapping the `:37` run, or a medic retry of a red
        one, must not read the board while another pass has not yet posted —
        the idempotency key only suppresses a receipt that is already there."""
        self.assertEqual(self.doc.get("concurrency"),
                         {"group": "hygiene", "cancel-in-progress": False})
        for job_id, job in self.doc["jobs"].items():
            self.assertNotIn("concurrency", job,
                             f"{job_id}: a job-level group would let two passes overlap")

    def test_the_dispatch_input_defaults_to_a_dry_run(self):
        inputs = self.on["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"dry_run"})
        self.assertEqual(inputs["dry_run"]["type"], "boolean")
        self.assertIs(inputs["dry_run"]["default"], True)

    def test_the_header_answers_the_vendor_premortem(self):
        header = WORKFLOW.read_text().split("\nname:", 1)[0]
        for q in ("Q1", "Q2", "Q3", "Q4", "Q5"):
            self.assertIn(q, header, f"standards/vendor-boundaries.md {q} is unanswered")
        self.assertIn("HYGIENE_LIVE", header)


# --------------------------------------------------------------------------- #
# 2. one dry-run value, computed once, reaching every call                    #
# --------------------------------------------------------------------------- #


class DryRunDecisionTest(unittest.TestCase):
    """The `mode` step in `read` is the ONE place the value is computed."""

    def setUp(self):
        self.doc = _doc()
        modes = [s for _j, s in _steps(self.doc) if s.get("id") == "mode"]
        self.assertEqual(len(modes), 1, "exactly one step computes dry_run")
        self.step = modes[0]
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def decide(self, event, dispatch_input="", live=""):
        result, outputs, _calls = _bash(
            self.step["run"],
            {"EVENT": event, "DISPATCH_DRY_RUN": dispatch_input, "HYGIENE_LIVE": live},
            self.tmp.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        return outputs.get("dry_run")

    def test_it_reads_the_event_the_input_and_the_variable(self):
        self.assertEqual(self.step["env"], {
            "EVENT": "${{ github.event_name }}",
            "DISPATCH_DRY_RUN": "${{ inputs.dry_run }}",
            "HYGIENE_LIVE": "${{ vars.HYGIENE_LIVE }}",
        })

    def test_it_lives_in_the_read_job_and_is_its_output(self):
        read = self.doc["jobs"]["read"]
        self.assertIn(self.step, read["steps"])
        self.assertEqual(read["outputs"]["dry_run"], "${{ steps.mode.outputs.dry_run }}")

    def test_the_schedule_is_a_dry_run_until_the_variable_is_exactly_true(self):
        self.assertEqual(self.decide("schedule"), "true")
        self.assertEqual(self.decide("schedule", live="true"), "false")
        for near_miss in ("TRUE", "True", "1", "yes", "true ", "false"):
            self.assertEqual(self.decide("schedule", live=near_miss), "true",
                             f"HYGIENE_LIVE={near_miss!r} ran live")

    def test_the_schedule_ignores_the_dispatch_input(self):
        self.assertEqual(self.decide("schedule", dispatch_input="false"), "true")

    def test_a_dispatch_follows_its_input_and_not_the_variable(self):
        self.assertEqual(self.decide("workflow_dispatch", "true"), "true")
        self.assertEqual(self.decide("workflow_dispatch", "true", live="true"), "true")
        self.assertEqual(self.decide("workflow_dispatch", "false"), "false")
        self.assertEqual(self.decide("workflow_dispatch", "false", live=""), "false")

    def test_an_unreadable_dispatch_input_is_a_dry_run(self):
        for odd in ("", "False", "no", "0"):
            self.assertEqual(self.decide("workflow_dispatch", odd), "true",
                             f"dry_run input {odd!r} ran live")

    def test_any_other_event_is_a_dry_run(self):
        self.assertEqual(self.decide("push", live="true"), "true")
        self.assertEqual(self.decide("", live="true"), "true")


class DryRunReachesEveryCallTest(unittest.TestCase):
    """`run` and `summarize` both take `--dry-run` from the one value — and
    an empty value (a `read` that never set it) is a dry run, not a live one."""

    def setUp(self):
        self.doc = _doc()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        Path(self.tmp.name, "ledgers").mkdir()
        for owner in ("dreadnought-foundry", "sidekick-labs"):
            Path(self.tmp.name, "ledgers", f"ledger-{owner}.json").write_text("{}")

    def _calls(self, step, value):
        env = {k: v for k, v in (step.get("env") or {}).items()
               if not str(v).startswith("${{")}
        env.update({"DRY_RUN": value, "OWNER": "dreadnought-foundry"})
        result, _out, calls = _bash(step["run"], env, self.tmp.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        hygiene = [c for c in calls if c.startswith("scripts/hygiene.py")]
        self.assertEqual(len(hygiene), 1, calls)
        return hygiene[0].split()

    def test_every_writing_call_reads_the_one_value(self):
        steps = _hygiene_steps(self.doc, "run") + _hygiene_steps(self.doc, "summarize")
        self.assertEqual(len(steps), 2)
        for _job, step in steps:
            self.assertEqual(step["env"]["DRY_RUN"], "${{ needs.read.outputs.dry_run }}",
                             step.get("name"))

    def test_the_flag_follows_the_value_and_fails_closed(self):
        for command in ("run", "summarize"):
            [(_job, step)] = _hygiene_steps(self.doc, command)
            self.assertIn("--dry-run", self._calls(step, "true"), command)
            self.assertIn("--dry-run", self._calls(step, ""), command)
            self.assertIn("--dry-run", self._calls(step, "garbage"), command)
            self.assertNotIn("--dry-run", self._calls(step, "false"), command)

    def test_run_is_handed_this_owner_board_and_ledger(self):
        [(_job, step)] = _hygiene_steps(self.doc, "run")
        argv = self._calls(step, "true")
        self.assertEqual(argv[:8], ["scripts/hygiene.py", "run",
                                    "--board", "board.json",
                                    "--owner", "dreadnought-foundry",
                                    "--ledger", "ledger-dreadnought-foundry.json"])

    def test_summarize_is_handed_every_ledger(self):
        [(_job, step)] = _hygiene_steps(self.doc, "summarize")
        argv = self._calls(step, "true")
        self.assertEqual(argv[:2], ["scripts/hygiene.py", "summarize"])
        ledgers = sorted(Path(a).name for a in argv if a.endswith(".json"))
        self.assertEqual(ledgers, ["ledger-dreadnought-foundry.json",
                                   "ledger-sidekick-labs.json"])

    def test_no_second_source_of_the_value(self):
        """HYGIENE_DRY_RUN is the core's other door to a dry run; set here it
        would be a second value the decision step never computed."""
        self.assertNotIn("HYGIENE_DRY_RUN", WORKFLOW.read_text())


# --------------------------------------------------------------------------- #
# 3. three jobs, one per command                                               #
# --------------------------------------------------------------------------- #


class JobsTest(unittest.TestCase):
    def setUp(self):
        self.doc = _doc()
        self.jobs = self.doc["jobs"]

    def test_three_jobs(self):
        self.assertEqual(list(self.jobs), ["read", "keep", "summary"])

    def test_the_board_is_read_once_per_pass(self):
        reads = _hygiene_steps(self.doc, "read")
        self.assertEqual([job for job, _s in reads], ["read"])
        step = reads[0][1]
        self.assertIn("--out board.json", step["run"])
        self.assertEqual(step["env"]["LINEAR_API_KEY"], "${{ secrets.LINEAR_API_KEY }}")
        self.assertEqual(step["env"]["HYGIENE_BUDGET_FLOOR"],
                         "${{ vars.HYGIENE_BUDGET_FLOOR }}")
        read = self.jobs["read"]
        self.assertEqual(read["outputs"]["go"], f"${{{{ steps.{step['id']}.outputs.go }}}}")
        self.assertIn("nightly_watch.py owners",
                      "\n".join(str(s.get("run") or "") for s in read["steps"]))
        self.assertIn("outputs.owners", read["outputs"]["owners"])
        uploads = [s for s in read["steps"]
                   if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0]["with"]["path"], "board.json")

    def test_keep_is_a_matrix_over_the_roster_owners(self):
        keep = self.jobs["keep"]
        self.assertEqual(keep["needs"], "read")
        self.assertIn("needs.read.outputs.go == 'true'", keep["if"])
        self.assertIs(keep["strategy"]["fail-fast"], False)
        self.assertEqual(keep["strategy"]["matrix"]["owner"],
                         "${{ fromJSON(needs.read.outputs.owners) }}")

    def test_keep_mints_its_token_per_owner_and_hands_it_to_run(self):
        steps = self.jobs["keep"]["steps"]
        mints = [s for s in steps
                 if str(s.get("uses", "")).split("@")[0] == APP_TOKEN_ACTION]
        self.assertEqual(len(mints), 1)
        self.assertEqual(mints[0]["with"]["owner"], "${{ matrix.owner }}")
        [(_job, run)] = _hygiene_steps(self.doc, "run")
        self.assertEqual(run["env"]["GH_TOKEN"], f"${{{{ steps.{mints[0]['id']}.outputs.token }}}}")
        self.assertEqual(run["env"]["OWNER"], "${{ matrix.owner }}")
        self.assertEqual(run["env"]["LINEAR_API_KEY"], "${{ secrets.LINEAR_API_KEY }}")
        self.assertEqual(run["env"]["HYGIENE_CARD"], "${{ vars.HYGIENE_CARD }}")
        self.assertLess(steps.index(mints[0]), steps.index(run))

    def test_keep_uploads_its_ledger_even_when_a_write_failed(self):
        """`run` exits 1 when a guarded write failed, AFTER writing the ledger;
        the summary still has to see that row."""
        uploads = [s for s in self.jobs["keep"]["steps"]
                   if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 1)
        self.assertIn("always()", uploads[0]["if"])
        self.assertIn("matrix.owner", uploads[0]["with"]["name"])

    def test_summary_runs_always_but_only_on_a_go(self):
        summary = self.jobs["summary"]
        self.assertIn("keep", summary["needs"])
        self.assertIn("always()", summary["if"])
        self.assertIn("needs.read.outputs.go == 'true'", summary["if"])
        [(job, step)] = _hygiene_steps(self.doc, "summarize")
        self.assertEqual(job, "summary")
        self.assertEqual(step["env"]["HYGIENE_CARD"], "${{ vars.HYGIENE_CARD }}")
        self.assertEqual(step["env"]["LINEAR_API_KEY"], "${{ secrets.LINEAR_API_KEY }}")


# --------------------------------------------------------------------------- #
# 4. it can change nothing on its own token                                    #
# --------------------------------------------------------------------------- #


class PermissionsTest(unittest.TestCase):
    def test_the_workflow_token_is_read_only(self):
        doc = _doc()
        self.assertEqual(doc["permissions"], {"contents": "read", "actions": "read"})
        for job_id, job in doc["jobs"].items():
            self.assertNotIn("permissions", job, f"{job_id} widens its own grant")

    def test_no_merge_no_push_no_write_grant(self):
        text = WORKFLOW.read_text()
        for forbidden in ("pr merge", "git push", "contents: write"):
            self.assertNotIn(forbidden, text)


# --------------------------------------------------------------------------- #
# 5. watched, rostered, and its receipts are the pipeline's                    #
# --------------------------------------------------------------------------- #


class WatchedTest(unittest.TestCase):
    def test_the_medic_watches_it(self):
        medic = yaml.safe_load(MEDIC_STUB.read_text())
        watched = _on(medic)["workflow_run"]["workflows"]
        self.assertIn("Hygiene", watched)

    def test_the_watcher_check_passes(self):
        violations, stats = check_workflow_watchers.check_dir()
        self.assertEqual(violations, [], "\n".join(violations))
        self.assertGreater(stats["workflows"], 0)

    def test_a_hygiene_receipt_is_never_a_person_speaking(self):
        self.assertIn("🧹", reconcile._AGENT_COMMENT_PREFIXES)


class RosterTest(unittest.TestCase):
    def setUp(self):
        agents = yaml.safe_load(AGENTS.read_text())["agents"]
        mine = [a for a in agents if a["name"] == "hygiene"]
        self.assertEqual(len(mine), 1)
        self.entry = mine[0]

    def test_every_field_the_card_names(self):
        self.assertEqual(self.entry, {
            "name": "hygiene",
            "role": "Hygiene Agent",
            "category": "operations",
            "trigger": "schedule — hourly at :37, over every repo in config/repo-map.json",
            "workflow": ".github/workflows/hygiene.yml",
            "briefPath": None,
            "tools": [],
            "maxTurns": None,
            "maxBudget": 0,
            "kind": "scripted",
            "model": None,
            "credentials": ["BUREAU_APP_ID", "BUREAU_APP_PRIVATE_KEY", "LINEAR_API_KEY"],
            "repoScope": ["dreadnought-foundry/bureau-pipeline"],
        })

    def test_it_carries_no_generated_model_region(self):
        text = AGENTS.read_text()
        start = text.index("  - name: hygiene\n")
        nxt = text.find("\n  - name:", start + 1)
        block = text[start: nxt if nxt != -1 else len(text)]
        self.assertNotIn("BEGIN generated model", block)

    def test_the_trigger_names_the_cron_the_workflow_runs(self):
        cron = _on(_doc())["schedule"][0]["cron"]
        self.assertIn(f":{cron.split()[0]}", self.entry["trigger"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
