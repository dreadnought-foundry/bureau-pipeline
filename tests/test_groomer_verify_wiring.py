"""The groomer workflow wires the verify agent's three new inputs (DRE-7242).

The verify agent reads three things its workflow did not yet run or
authorize:

* the read door's search, from `groom_verify_agent.py targets` — which needs
  the door's three settings and an OIDC token. `groomer.py propose` shares the
  Groom step with it and must run exactly as before, so the settings are
  scoped to the `targets` command alone, and the stub grants `id-token: write`;
* the routing reader, `groom_routing.py`, in its own per-owner job beside the
  lookups, on a token holding `actions: read` and nothing else, folded into the
  targets after the lookups' fold;
* the replay, `groom_replay.py`, behind a `replay` input: the Groom step reads
  the stored proposal instead of proposing, a drain refuses, and the post job
  posts to no card and reports.

And each verdict artifact keeps the folded targets beside the verdict, for
the proof. The `run:` blocks are executed here against stub scripts, so what
the shell actually does is proved, not only what the YAML says.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))

import groom_routing  # noqa: E402
import groom_verify_agent  # noqa: E402

PIPELINE = "dreadnought-foundry/bureau-pipeline"
RUNS_ON = "fromJSON(vars.BUREAU_RUNS_ON || '[\"ubuntu-latest\"]')"

#: The door's three settings, and the expression each must carry — the one
#: every other door-reading workflow here carries (reconcile.yml, verify.yml).
DOOR = {
    "BUREAU_READ": "vars.BUREAU_READ || 'off'",
    "BUREAU_READ_URL": "vars.BUREAU_READ_URL",
    "BUREAU_READ_AUDIENCE": "vars.BUREAU_READ_AUDIENCE",
}

#: sha256 of the `lookup` job's text on main before this card, from its
#: `  lookup:` line to the next line at job indent. Its nine minutes are sized
#: against `groom_lookups.MAX_SECONDS` alone (tests/test_groomer_wiring.py),
#: so the routing read is a job of its own and this one is never edited.
LOOKUP_SHA256 = "eb628ff408d7b7eea6092bfa603475ab41b035058dd27c0f43fd4b71bf18b572"

GROOM_STEP = "Groom"
POST_STEP = "Post the verified proposal"
RECEIPT_STEP = "Judgement receipt — the model that answered"
LOOKUP_FOLD_STEP = "Fold the lookups into the targets"
ROUTING_FOLD_STEP = "Fold the routing into the targets"
ROUTING_DOWNLOAD_STEP = "Download every owner's routing"
ROUTING_STEP = "Read this owner's routing"
KEEP_VERDICT_STEP = "Keep the verdict"


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _load(name: str) -> dict:
    return yaml.safe_load(_text(name))


def _on(doc: dict) -> dict:
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _expression(value) -> str:
    text = " ".join(str(value or "").split())
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2]
    return text.strip()


def _one_line(run) -> str:
    return " ".join(str(run or "").replace("\\\n", " ").split())


def _needs(job: dict) -> list:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def _steps(doc: dict) -> list:
    out = []
    for job in (doc.get("jobs") or {}).values():
        out.extend(job.get("steps") or [])
    return out


def _named(job: dict, name: str) -> dict:
    for step in job.get("steps") or []:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in the job")


def _by_id(job: dict, step_id: str) -> dict:
    for step in job.get("steps") or []:
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"no step with id {step_id!r} in the job")


def _uses(job: dict, action: str) -> list:
    return [s for s in job.get("steps") or []
            if str(s.get("uses") or "").startswith(action)]


def _flag(run: str, flag: str) -> str:
    words = shlex.split(_one_line(run))
    return words[words.index(flag) + 1]


def _job_text(text: str, name: str) -> str:
    """A job's own lines, from `  <name>:` to the next line at job indent."""
    lines = text.splitlines(keepends=True)
    start = lines.index(f"  {name}:\n")
    end = start + 1
    while end < len(lines) and not re.match(r"^  \S", lines[end]):
        end += 1
    return "".join(lines[start:end]).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# The stub scripts the `run:` blocks are executed against                      #
# --------------------------------------------------------------------------- #

#: One stub for every script a step here runs. It records its argv and the
#: door's settings as its own process sees them, then writes whatever file
#: `--out` / `--matrix-out` name, so the step's next line has its input.
#: `groomer.py` is also imported by the post step's no-card branch for
#: `render_proposal`, so it is a module too.
STUB = '''\
import json, os, sys

DOOR = ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE")


def render_proposal(proposal):
    return "THE PAGE"


def _record():
    with open(os.environ["CALLS"], "a") as f:
        f.write(json.dumps({
            "script": os.path.basename(sys.argv[0]), "argv": sys.argv[1:],
            "door": {k: os.environ[k] for k in DOOR if k in os.environ},
        }) + "\\n")
    args = sys.argv[1:]
    for flag, body in (("--out", "{}"), ("--matrix-out", "[]")):
        if flag in args:
            with open(args[args.index(flag) + 1], "w") as f:
                f.write(body)
    if os.path.basename(sys.argv[0]) == "groom_replay.py":
        print("groom-replay: stub")


if __name__ == "__main__":
    _record()
'''

STUBBED = ("groomer.py", "groom_replay.py", "groom_verify_agent.py",
           "groomer_receipt.py", "linear_ops.py")


class _RunsSteps(unittest.TestCase):
    """A workspace with a stubbed `.bureau-pipeline/scripts/`, and one step's
    `run:` executed in it under its own `env:` names."""

    def setUp(self):
        self.doc = _load("groomer.yml")
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        scripts = self.ws / ".bureau-pipeline" / "scripts"
        scripts.mkdir(parents=True)
        for name in STUBBED:
            (scripts / name).write_text(STUB)
        self.calls = self.ws / "calls.jsonl"
        self.output = self.ws / "github-output"
        self.summary = self.ws / "step-summary"

    def tearDown(self):
        self._tmp.cleanup()

    def run_step(self, step: dict, env: dict) -> subprocess.CompletedProcess:
        base = {k: v for k, v in os.environ.items()
                if not k.startswith("BUREAU_READ")}
        return subprocess.run(
            ["bash", "-e", "-c", step["run"]], cwd=self.ws,
            env={**base, "CALLS": str(self.calls),
                 "GITHUB_OUTPUT": str(self.output),
                 "GITHUB_STEP_SUMMARY": str(self.summary), **env},
            capture_output=True, text=True)

    def recorded(self) -> list[dict]:
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def scripts_run(self) -> list[tuple[str, str]]:
        return [(c["script"], (c["argv"] or [""])[0]) for c in self.recorded()]


def _groom_env(**overrides) -> dict:
    env = {"MODE": "propose", "LANE": "Intake", "CAPACITY": "20",
           "BATCH_CYCLES": "1", "PRIORITY": "portico", "CARD": "DRE-1",
           "INTAKE_HOLD": "", "JUDGEMENT": "on", "REPLAY": "",
           "DOOR_READ": "on", "DOOR_READ_URL": "https://door.example",
           "DOOR_READ_AUDIENCE": "door-audience"}
    env.update(overrides)
    return env


# --------------------------------------------------------------------------- #
# The read door, for the targets read only                                     #
# --------------------------------------------------------------------------- #

class TheDoorReachesTheTargetsReadOnlyTest(_RunsSteps):
    """`targets` reads through the door; `propose` runs exactly as today. The
    two share the Groom step — tests/test_groomer_wiring.py pins both there,
    and the job's `verify_matrix` output off that step's id — so the door's
    settings ride in the step's `env:` under names nothing reads, and are
    handed to the `targets` command alone, as the door's own names."""

    def setUp(self):
        super().setUp()
        self.groom = _named(self.doc["jobs"]["groom"], GROOM_STEP)
        self.env = self.groom.get("env") or {}

    def test_the_targets_command_carries_the_doors_three_settings(self):
        words = shlex.split(_one_line(self.groom["run"]))
        at = next(i for i, w in enumerate(words)
                  if w.endswith("groom_verify_agent.py") and words[i + 1] == "targets")
        assert words[at - 1] == "python3", words[at - 1]
        prefix = {}
        i = at - 2
        while i >= 0 and re.match(r"^[A-Z_]+=", words[i]):
            key, _, value = words[i].partition("=")
            prefix[key] = value
            i -= 1
        self.assertEqual(sorted(prefix), sorted(DOOR),
                         "the targets command is not handed the door's settings")
        for key, value in prefix.items():
            self.assertTrue(value.startswith("$"), f"{key} is not read from env")
            self.assertEqual(_expression(self.env.get(value.lstrip("$"))),
                             DOOR[key], f"{key} reads the wrong variable")

    def test_no_step_running_propose_holds_the_doors_settings(self):
        found = 0
        for step in _steps(self.doc):
            if "groomer.py propose" not in str(step.get("run") or ""):
                continue
            found += 1
            for key in DOOR:
                self.assertNotIn(key, step.get("env") or {},
                                 f"{step.get('name')!r} hands propose {key}")
        self.assertEqual(found, 1, "propose runs in exactly one step")

    def test_propose_runs_without_the_door_and_targets_with_it(self):
        proc = self.run_step(self.groom, _groom_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = {(c["script"], c["argv"][0]): c["door"] for c in self.recorded()}
        self.assertEqual(calls[("groomer.py", "propose")], {},
                         "propose sees the door's settings")
        self.assertEqual(calls[("groom_verify_agent.py", "targets")], {
            "BUREAU_READ": "on", "BUREAU_READ_URL": "https://door.example",
            "BUREAU_READ_AUDIENCE": "door-audience"})

    def test_the_stub_grants_id_token_as_self_reconcile_does(self):
        stub = _text("self-groomer.yml")
        perms = _load("self-groomer.yml").get("permissions") or {}
        self.assertEqual(perms.get("id-token"), "write")
        self.assertEqual(perms.get("contents"), "read")
        line = next(l for l in _text("self-reconcile.yml").splitlines()
                    if l.strip().startswith("id-token:"))
        self.assertIn(line, stub.splitlines(),
                      "the grant carries self-reconcile.yml's comment")


# --------------------------------------------------------------------------- #
# Routing, in its own job beside the lookups                                   #
# --------------------------------------------------------------------------- #

class RoutingJobTest(unittest.TestCase):
    def setUp(self):
        self.doc = _load("groomer.yml")
        self.jobs = self.doc["jobs"]
        self.job = self.jobs.get("routing") or {}
        self.steps = self.job.get("steps") or []

    def test_the_lookup_job_is_byte_identical_to_main(self):
        block = _job_text(_text("groomer.yml"), "lookup")
        self.assertEqual(hashlib.sha256(block.encode()).hexdigest(),
                         LOOKUP_SHA256, "the lookup job was edited")

    def test_it_runs_beside_lookup_on_its_needs_if_and_matrix(self):
        lookup = self.jobs["lookup"]
        self.assertTrue(self.job, "there is no routing job")
        self.assertEqual(_needs(self.job), ["groom"])
        self.assertEqual(_expression(self.job.get("if")),
                         _expression(lookup.get("if")))
        self.assertEqual(_expression(self.job.get("runs-on")), RUNS_ON)
        strategy = self.job.get("strategy") or {}
        self.assertIs(strategy.get("fail-fast"), False)
        self.assertEqual(
            _expression((strategy.get("matrix") or {}).get("owner")),
            "fromJSON(needs.groom.outputs.lookup_owners)")

    def test_it_checks_out_the_pipeline_at_the_callers_ref(self):
        checkout = self.steps[0]
        self.assertTrue(str(checkout.get("uses")).startswith("actions/checkout@"))
        with_ = checkout.get("with") or {}
        self.assertEqual(with_.get("repository"), PIPELINE)
        self.assertEqual(_expression(with_.get("ref")), "inputs.pipeline_ref || 'main'")
        self.assertEqual(with_.get("path"), ".bureau-pipeline")

    def test_it_mints_actions_read_and_nothing_else(self):
        mints = _uses(self.job, "actions/create-github-app-token@")
        self.assertEqual(len(mints), 1)
        mint = mints[0]
        lookup_mint = _uses(self.jobs["lookup"], "actions/create-github-app-token@")[0]
        self.assertEqual(mint.get("uses"), lookup_mint.get("uses"))
        with_ = mint.get("with") or {}
        self.assertEqual(_expression(with_.get("owner")), "matrix.owner")
        self.assertEqual(_expression(with_.get("app-id")), "secrets.BUREAU_APP_ID")
        self.assertEqual(_expression(with_.get("private-key")),
                         "secrets.BUREAU_APP_PRIVATE_KEY")
        self.assertEqual(
            {k: v for k, v in with_.items() if k.startswith("permission-")},
            {"permission-actions": "read"})
        self.assertIs(mint.get("continue-on-error"), True)

    def test_the_routing_step_always_runs_with_inputs_through_env(self):
        step = _named(self.job, ROUTING_STEP)
        self.assertNotIn("if", step, "an empty token must still write its document")
        run = _one_line(step.get("run"))
        self.assertIn('python3 .bureau-pipeline/scripts/groom_routing.py owner '
                      '--owner "$OWNER" --out "routing-$OWNER.json"', run)
        self.assertNotIn("${{", run)
        env = step.get("env") or {}
        mint_id = _uses(self.job, "actions/create-github-app-token@")[0]["id"]
        self.assertEqual(_expression(env.get("GH_TOKEN")),
                         f"steps.{mint_id}.outputs.token")
        self.assertEqual(_expression(env.get("OWNER")), "matrix.owner")
        for name in ("LINEAR_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
            self.assertNotIn(name, str(self.job))

    def test_it_uploads_its_document_under_the_owner(self):
        uploads = _uses(self.job, "actions/upload-artifact@")
        self.assertEqual(len(uploads), 1)
        upload = uploads[0]
        with_ = upload.get("with") or {}
        self.assertEqual(with_.get("name"), "groom-routing-${{ matrix.owner }}")
        self.assertEqual(with_.get("path"), "routing-${{ matrix.owner }}.json")
        self.assertIs(with_.get("overwrite"), True)
        self.assertEqual(with_.get("retention-days"), 30)
        self.assertEqual(_expression(upload.get("if")), "always()")
        order = [self.steps[0], _uses(self.job, "actions/create-github-app-token@")[0],
                 _named(self.job, ROUTING_STEP), upload]
        at = [self.steps.index(s) for s in order]
        self.assertEqual(at, sorted(at))

    def test_its_clock_covers_the_scripts_own_stop(self):
        """Both sides read: the leg stops asking at `MAX_SECONDS`, one request
        holds it past that by `REQUEST_TIMEOUT`, and two minutes cover the
        checkout, the mint and the upload around it."""
        minutes = self.job.get("timeout-minutes")
        self.assertEqual(minutes, 5)
        self.assertGreaterEqual(
            int(minutes) * 60,
            groom_routing.MAX_SECONDS + groom_routing.REQUEST_TIMEOUT + 120)


class VerifyFoldsTheRoutingTest(unittest.TestCase):
    def setUp(self):
        self.doc = _load("groomer.yml")
        self.job = self.doc["jobs"]["verify"]
        self.steps = self.job.get("steps") or []

    def test_verify_needs_routing_and_still_opens_always(self):
        self.assertEqual(_needs(self.job), ["groom", "lookup", "routing"])
        self.assertTrue(_expression(self.job.get("if")).startswith("always()"))

    def test_the_download_is_unmerged_and_lands_where_fold_reads(self):
        download = _named(self.job, ROUTING_DOWNLOAD_STEP)
        with_ = download.get("with") or {}
        self.assertEqual(with_.get("pattern"), "groom-routing-*")
        self.assertEqual(with_.get("path"), "routing")
        self.assertIs(with_.get("merge-multiple"), False)
        self.assertIs(download.get("continue-on-error"), True)
        fold = _named(self.job, ROUTING_FOLD_STEP)
        self.assertEqual(_flag(fold["run"], "--dir"), "routing")

    def test_the_fold_makes_its_directory_then_folds(self):
        run = _one_line(_named(self.job, ROUTING_FOLD_STEP)["run"])
        mkdir = "mkdir -p routing"
        command = ("python3 .bureau-pipeline/scripts/groom_routing.py fold "
                   "--targets verify-targets.json --dir routing "
                   "--out verify-targets.json")
        self.assertIn(mkdir, run)
        self.assertIn(command, run)
        self.assertLess(run.index(mkdir), run.index(command))

    def test_it_folds_after_the_lookups_and_before_prepare(self):
        order = [_named(self.job, ROUTING_DOWNLOAD_STEP),
                 _named(self.job, LOOKUP_FOLD_STEP),
                 _named(self.job, ROUTING_FOLD_STEP),
                 _by_id(self.job, "prepare")]
        at = [self.steps.index(s) for s in order]
        self.assertEqual(at, sorted(at))

    def test_a_morning_no_leg_uploaded_still_folds(self):
        """The fold step as written, over the real script, with no `routing`
        directory at all: every row says it has no routing facts."""
        fold = _named(self.job, ROUTING_FOLD_STEP)
        with tempfile.TemporaryDirectory() as raw:
            ws = Path(raw)
            (ws / ".bureau-pipeline").symlink_to(ROOT)
            (ws / "verify-targets.json").write_text(json.dumps(
                [{"card": "DRE-901", "repository": PIPELINE, "list": "planning"}]))
            proc = subprocess.run(["bash", "-e", "-c", fold["run"]], cwd=ws,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            rows = json.loads((ws / "verify-targets.json").read_text())
        self.assertIn("routing", rows[0])
        self.assertIs(rows[0]["routing"]["read"], False)
        self.assertEqual(rows[0]["routing"]["facts"], [])


# --------------------------------------------------------------------------- #
# The folded targets are kept with each verdict                                #
# --------------------------------------------------------------------------- #

class TheFoldedTargetsAreKeptTest(unittest.TestCase):
    def setUp(self):
        self.doc = _load("groomer.yml")
        self.job = self.doc["jobs"]["verify"]
        self.steps = self.job.get("steps") or []
        self.keep = _named(self.job, KEEP_VERDICT_STEP)

    def test_the_verdict_artifact_names_both_files(self):
        with_ = self.keep.get("with") or {}
        self.assertEqual(with_.get("name"), "groom-verdict-${{ matrix.card }}")
        self.assertEqual(str(with_.get("path")).split(),
                         ["verdict.json", "verify-targets.json"])
        self.assertEqual(_expression(self.keep.get("if")), "always()")
        self.assertIs(with_.get("overwrite"), True)

    def test_the_targets_it_keeps_are_the_ones_both_folds_wrote(self):
        folds = [_named(self.job, LOOKUP_FOLD_STEP), _named(self.job, ROUTING_FOLD_STEP)]
        for fold in folds:
            self.assertEqual(_flag(fold["run"], "--out"), "verify-targets.json")
        last_fold = self.steps.index(folds[-1])
        self.assertLess(last_fold, self.steps.index(self.keep))
        # Nothing between the routing fold and the upload writes the file again.
        for step in self.steps[last_fold + 1:self.steps.index(self.keep)]:
            run = _one_line(step.get("run"))
            self.assertNotIn("--out verify-targets.json", run, step.get("name"))
            self.assertNotIn("> verify-targets.json", run, step.get("name"))

    def test_read_verdicts_reads_only_the_verdict(self):
        with tempfile.TemporaryDirectory() as raw:
            leg = Path(raw) / "groom-verdict-DRE-1"
            leg.mkdir()
            verdict = {"card": "DRE-1", "verdict": "still-needed"}
            (leg / "verdict.json").write_text(json.dumps(verdict))
            # Shaped like a verdict on purpose: a reader that took every JSON
            # file would find a second card here.
            (leg / "verify-targets.json").write_text(
                json.dumps({"card": "DRE-2", "verdict": "obsolete"}))
            found = groom_verify_agent.read_verdicts(raw)
        self.assertEqual(found, {"DRE-1": verdict})


# --------------------------------------------------------------------------- #
# The replay                                                                   #
# --------------------------------------------------------------------------- #

class TheReplayInputTest(unittest.TestCase):
    def setUp(self):
        self.doc = _load("groomer.yml")
        self.stub = _load("self-groomer.yml")

    def test_the_reusable_takes_a_replay_string_empty_by_default(self):
        spec = (_on(self.doc)["workflow_call"].get("inputs") or {}).get("replay")
        self.assertIsNotNone(spec, "the reusable takes no replay input")
        self.assertEqual(spec.get("type"), "string")
        self.assertEqual(spec.get("default"), "")
        self.assertIs(spec.get("required"), False)

    def test_the_hand_dispatch_offers_it_and_passes_it_through(self):
        spec = (_on(self.stub)["workflow_dispatch"].get("inputs") or {}).get("replay")
        self.assertIsNotNone(spec, "the stub offers no replay")
        self.assertEqual(spec.get("type"), "string")
        self.assertEqual(spec.get("default"), "")
        self.assertEqual(
            _expression((self.stub["jobs"]["call"].get("with") or {}).get("replay")),
            "inputs.replay")

    def test_the_schedule_and_the_drain_never_set_it(self):
        for name in ("schedule", "drain"):
            self.assertNotIn("replay", self.stub["jobs"][name].get("with") or {},
                             f"the {name} job passes replay")

    def test_every_step_reads_it_through_env(self):
        for job_name, step_name in (("groom", GROOM_STEP), ("post", POST_STEP),
                                    ("post", RECEIPT_STEP)):
            step = _named(self.doc["jobs"][job_name], step_name)
            self.assertEqual(_expression((step.get("env") or {}).get("REPLAY")),
                             "inputs.replay", step_name)
            self.assertNotIn("${{", step["run"], step_name)


class TheReplayGroomsTest(_RunsSteps):
    def setUp(self):
        super().setUp()
        self.groom = _named(self.doc["jobs"]["groom"], GROOM_STEP)

    def test_a_replay_reads_the_stored_proposal_and_never_proposes(self):
        proc = self.run_step(self.groom, _groom_env(
            REPLAY="tests/fixtures/groom-page-ab09ece4ddee.json"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        runs = self.scripts_run()
        self.assertNotIn(("groomer.py", "propose"), runs)
        base = next(c for c in self.recorded()
                    if c["script"] == "groom_replay.py")
        self.assertEqual(base["argv"], [
            "base", "--stored", "tests/fixtures/groom-page-ab09ece4ddee.json",
            "--out", "proposal.json"])
        self.assertIn(("groom_verify_agent.py", "targets"), runs)
        self.assertIn("verify_matrix=[]", self.output.read_text())

    def test_without_a_replay_it_proposes_as_today(self):
        proc = self.run_step(self.groom, _groom_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        runs = self.scripts_run()
        self.assertIn(("groomer.py", "propose"), runs)
        self.assertNotIn("groom_replay.py", [s for s, _ in runs])

    def test_a_drain_with_a_replay_refuses_before_any_write(self):
        proc = self.run_step(self.groom, _groom_env(MODE="drain", REPLAY="x.json"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.recorded(), [], "a script ran")
        self.assertFalse((self.ws / "proposal.txt").exists())
        self.assertFalse(self.output.exists() and self.output.read_text())

    def test_a_drain_without_a_replay_still_drains(self):
        proc = self.run_step(self.groom, _groom_env(MODE="drain"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.scripts_run(), [("groomer.py", "drain")])


class TheReplayPostsNothingTest(_RunsSteps):
    def setUp(self):
        super().setUp()
        post = self.doc["jobs"]["post"]
        self.post = _named(post, POST_STEP)
        self.receipt = _named(post, RECEIPT_STEP)
        (self.ws / "proposal-verified.json").write_text("{}")

    def _post(self, replay: str) -> subprocess.CompletedProcess:
        return self.run_step(self.post, {"CARD": "DRE-1", "DRY_RUN": "",
                                         "REPLAY": replay})

    def test_a_replay_posts_to_no_card_and_reports(self):
        proc = self._post("stored.json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        runs = self.scripts_run()
        self.assertNotIn(("groomer.py", "post"), runs)
        self.assertEqual(runs, [("groom_replay.py", "report")])
        report = self.recorded()[0]
        self.assertEqual(report["argv"],
                         ["report", "--proposal", "proposal-verified.json"])
        self.assertIn("THE PAGE", proc.stdout)
        self.assertIn("THE PAGE", self.summary.read_text())
        self.assertIn("groom-replay:", proc.stdout)

    def test_without_a_replay_the_card_is_posted_and_nothing_reported(self):
        proc = self._post("")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.scripts_run(), [("groomer.py", "post")])
        self.assertIn("--card", self.recorded()[0]["argv"])

    def test_a_replay_writes_no_receipt(self):
        env = {"CARD": "DRE-1", "LINEAR_API_KEY": "k"}
        proc = self.run_step(self.receipt, {**env, "REPLAY": "stored.json"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.recorded(), [], "a replay ran the receipt")

    def test_without_a_replay_the_receipt_is_written(self):
        proc = self.run_step(self.receipt, {"CARD": "DRE-1", "LINEAR_API_KEY": "k",
                                            "REPLAY": ""})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(("groomer_receipt.py", "--proposal"), self.scripts_run())


if __name__ == "__main__":
    unittest.main()
