"""The verifier's shadow mode: replay a past pull request at its verified head, post nothing (DRE-7194).

A model trial needs to know what a candidate model would have said about pull
requests the fleet has already judged. `verify.yml` grows four optional
`workflow_call` inputs for that — `model`, `shadow`, `replay_pr`,
`replay_sha` — and a run with `shadow: true` replays one pull request at the
commit its recorded verdict was bound to, on the named model, and writes the
verdict to a file and nowhere else. The batch runner that dispatches replays
(DRE-7197) and the critic's half (DRE-7193, `qa-review.yml`) read the same
contract; this file pins the verifier's side of it.

What it pins, read off the live workflow and, where a step's shell decides,
by running that shell:

* the four inputs, exactly as the contract states, and `workflow_call` only;
* with no new input set the run is today's run: the same steps, each `if:`
  deciding as it did on `main` before this change, the Select model step's
  command byte for byte, and the same `--model`;
* the guard refuses bad input before any agent step — a live verification
  never runs on a named model;
* shadow posts nothing: the writing steps are DISCOVERED by what their shell
  calls, never listed by name, and each is skipped in shadow;
* the agent replays the verified head with no Linear key, a read-only token,
  the checkout at `replay_sha` and the compare of `replay_sha` with the base
  it was built on as its diff — the merge commit's first parent once the pull
  request has merged, the base branch while it has not — and an empty diff
  fails the run rather than verifying nothing;
* no earlier critic or verifier verdict reaches the shadow prompt, and the
  card's snapshot and thread are off the runner before the agent starts;
* the shadow verdict file, its artifact and its one log line.

Run: python3 -m pytest tests/test_verify_shadow.py -v
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "verify.yml"

sys.path.insert(0, str(ROOT / "scripts"))

import fix_concurrency as fc  # noqa: E402
import merge_gate  # noqa: E402

AGENT_ACTION = "anthropics/claude-code-action"
SELECTED = "claude-selected-by-the-ladder"


def _a_selectable_priced_model() -> str:
    """A model the guard lets a replay run on, read off the config the guard
    reads: priced, and listed under neither `excluded` nor `retired`. Never a
    literal — the console changes which models those are (DRE-6859)."""
    cfg = yaml.safe_load((ROOT / "config" / "models.yaml").read_text()) or {}
    prices = yaml.safe_load((ROOT / "config" / "model-prices.yaml").read_text())["prices"]
    barred = {e.get("model") if isinstance(e, dict) else e
              for sec in ("excluded", "retired") for e in cfg.get(sec) or []}
    return next(m for m in prices if m not in barred)


NAMED = _a_selectable_priced_model()
REPLAY_PR = "4242"
REPLAY_SHA = "0123456789abcdef0123456789abcdef01234567"
TODAY_SHA = "fedcba9876543210fedcba9876543210fedcba98"
REPO = "dreadnought-foundry/portico"
SECRETS = {
    "LINEAR_API_KEY": "fleet-key",
    "LINEAR_PLANNER_KEY": "Bearer tok",
    "BUREAU_APP_ID": "1",
    "BUREAU_APP_PRIVATE_KEY": "pk",
}

# Every step on `main` before DRE-7194, in order, keyed by its name (the two
# unnamed checkouts by their `uses:` and ordinal), with the `if:` it carried
# there. Recorded from `main` at afa91e9f.
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
IN_SCOPE = "steps.scope.outputs.in_scope == 'true'"
LIVE = "always() && !cancelled() && " + IN_SCOPE
RETRY = ("always() && !cancelled() && steps.install_claude.outcome == 'success' && "
         + IN_SCOPE + " && steps.gate1.outputs.real != 'true'")
MAIN_STEPS = [
    ("Mint bot token", None),
    (CHECKOUT + "#1", None),
    (CHECKOUT + "#2", None),
    ("Resolve PR", None),
    ("Read the card once (read door, else one Linear read)", None),
    ("Decide scope (multi-system / UI?)", None),
    ("Out of scope — skip (no verdict, no block)", "steps.scope.outputs.in_scope != 'true'"),
    ("Mint pool probe token (2)", IN_SCOPE + " && env.BUREAU_APP_ID_2 != ''"),
    ("Mint pool probe token (3)", IN_SCOPE + " && env.BUREAU_APP_ID_3 != ''"),
    ("Mint pool probe token (4)", IN_SCOPE + " && env.BUREAU_APP_ID_4 != ''"),
    ("Select dispatch-pool app", IN_SCOPE),
    ("Mint worker token (selected pool app)", IN_SCOPE),
    ("Take the GitHub-calls before points", None),
    ("Product environment setup (optional)", IN_SCOPE),
    ("Assemble verifier context", IN_SCOPE),
    ("Mint the model-home read token", IN_SCOPE),
    ("Select model", IN_SCOPE),
    ("Install Claude Code (asserted)", IN_SCOPE),
    ("Verifier run", IN_SCOPE),
    ("Gate verifier result (attempt 1)", LIVE),
    ("Clear stale verdict before retry", LIVE + " && steps.gate1.outputs.real != 'true'"),
    ("Back off before the verifier retry", RETRY),
    ("Verifier run (retry)", RETRY),
    ("Gate verifier result (attempt 2)", LIVE + " && steps.gate1.outputs.real != 'true'"),
    ("Mint qa-bot token (verdicts author as qa-bot)", LIVE),
    ("Post verdict or neutral status", LIVE),
    ("Fail if verifier never really ran",
     LIVE + " && steps.gate1.outputs.real != 'true' && steps.gate2.outputs.real != 'true'"),
    ("Keep the run's death-cause receipt", "always()"),
    ("Upload the death-cause receipt", "always() && steps.death.outputs.path != ''"),
    ("Record the GitHub calls per pool login", "always()"),
    ("Keep the run's working log", "always()"),
]

# sha256 of the Select model step's `run:` and of its `env:` (json, sorted
# keys) on `main` at afa91e9f. The step "still runs, unchanged".
SELECT_MODEL_RUN_SHA256 = "e660309b8c36df1f60632015d9d6f16c0616e1e7ff34532aab1e08faf49b0d63"
SELECT_MODEL_ENV_SHA256 = "c49fa409aded3dbeb9e82d7c54a2613100f9621723b6c3de090fa7e54fa5dc51"

# The writing calls a step can make, read off its shell (continuation lines
# joined). A step is a WRITER when its `run:` matches any of them.
LINEAR_WRITES = ("comment", "advance", "add-label", "remove-label", "set-description", "state")
WRITE_CALLS = (
    re.compile(r"\bgh\s+pr\s+(?:comment|edit|review)\b"),
    re.compile(r"\blinear_ops\.py\s+(?:" + "|".join(map(re.escape, LINEAR_WRITES)) + r")\b"),
    re.compile(r"\bgh\s+api\b[^\n;|&]*?(?:-X\s*|--method[\s=]+)['\"]?(?:POST|PUT|PATCH|DELETE)\b", re.I),
    # `gh api` with a field or an input body sends a POST unless told otherwise.
    re.compile(r"\bgh\s+api\b[^\n;|&]*?\s(?:-f|-F|--field|--raw-field|--input)\b"),
)


# ── reading the workflow ────────────────────────────────────────────────────
def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _on(doc: dict) -> dict:
    return doc.get("on") or doc.get(True) or {}


def _steps() -> list[dict]:
    return _doc()["jobs"]["verify"]["steps"]


def _keyed(steps: list[dict]) -> list[tuple[str, dict]]:
    """(key, step): the name, or `uses#ordinal` for an unnamed step."""
    out, seen = [], {}
    for step in steps:
        if step.get("name"):
            out.append((step["name"], step))
            continue
        uses = step.get("uses", "?")
        seen[uses] = seen.get(uses, 0) + 1
        out.append((f"{uses}#{seen[uses]}", step))
    return out


def _step(key: str) -> dict:
    for k, step in _keyed(_steps()):
        if k == key or step.get("id") == key:
            return step
    raise AssertionError(f"verify.yml has no step {key!r}")


def _agent_steps() -> list[dict]:
    return [s for s in _steps() if str(s.get("uses", "")).split("@")[0] == AGENT_ACTION]


def _ctx(*, shadow=False, model="", replay_pr="", replay_sha="", in_scope="true",
         real1="", real2="", install="success", cancelled=False, app2="",
         death_path="", guard="success", verdict_dir="", upload="success",
         shadow_effort="--effort max") -> dict:
    return {
        "inputs": {"pipeline_ref": "main", "shadow": shadow, "model": model,
                   "replay_pr": replay_pr, "replay_sha": replay_sha},
        "steps": {
            "scope": {"outputs": {"in_scope": in_scope}},
            "gate1": {"outputs": {"real": real1}},
            "gate2": {"outputs": {"real": real2}},
            "install_claude": {"outcome": install, "outputs": {"executable": "/x/claude"}},
            "death": {"outputs": {"path": death_path}},
            "shadow_guard": {"outcome": guard},
            "model": {"outputs": {"model": SELECTED, "effort_arg": "--effort high"}},
            "shadow_model": {"outputs": {"model": model, "effort_arg": shadow_effort}
                             if shadow else {}},
            "pr": {"outputs": {"number": replay_pr or "77", "card": "DRE-1",
                               "sha": replay_sha or TODAY_SHA}},
            "worker": {"outputs": {"token": "worker-token"}},
            "shadow_verdict": {"outputs": {"dir": verdict_dir,
                                           "line": "shadow-verdict: x" if verdict_dir else ""}},
            "shadow_upload": {"outcome": upload},
        },
        "env": {"BUREAU_APP_ID_2": app2, "BUREAU_APP_ID_3": app2, "BUREAU_APP_ID_4": app2},
        "secrets": dict(SECRETS),
        "vars": {},
        "github": {"repository": REPO, "event_name": "workflow_dispatch",
                   "event": {"inputs": {}}},
        "cancelled": cancelled,
    }


def _shadow(**kw) -> dict:
    kw.setdefault("shadow", True)
    kw.setdefault("model", NAMED)
    kw.setdefault("replay_pr", REPLAY_PR)
    kw.setdefault("replay_sha", REPLAY_SHA)
    return _ctx(**kw)


def _cond(expr, ctx: dict) -> bool:
    """A step `if:` as GitHub decides it once the steps before it succeeded:
    no `if:` runs, and `always()` is true."""
    if expr is None:
        return True
    return fc._truthy(fc.evaluate(str(expr).replace("always()", "true"), ctx))


def _value(text: str, ctx: dict) -> str:
    return fc.interpolate(str(text), ctx)


def _matrix():
    """Every combination of the outputs the step `if:`s read."""
    axes = {
        "in_scope": ("true", "false", ""),
        "real1": ("true", "false", ""),
        "real2": ("true", "false", ""),
        "install": ("success", "failure", "skipped"),
        "cancelled": (False, True),
        "app2": ("", "2"),
        "death_path": ("", "/r/death.json"),
    }
    keys = list(axes)
    for combo in itertools.product(*(axes[k] for k in keys)):
        yield dict(zip(keys, combo))


def _joined(run: str) -> str:
    return re.sub(r"\\\n\s*", " ", run or "")


def _writers() -> list[str]:
    return [key for key, step in _keyed(_steps())
            if any(p.search(_joined(step.get("run", ""))) for p in WRITE_CALLS)]


# ── running a step's shell ──────────────────────────────────────────────────
FAKE_GH = textwrap.dedent(r'''
    #!/usr/bin/env python3
    import json, os, sys
    argv = sys.argv[1:]
    with open(os.environ["GH_LOG"], "a") as fh:
        fh.write(json.dumps(argv) + "\n")
    joined = " ".join(argv)
    # A merged pull request (MERGE_COMMIT set) behaves as GitHub does: its
    # head is an ancestor of the base branch today, so the compare of `main`
    # and that head is empty, and only the base it merged into
    # (BASE_AT_MERGE, the merge commit's first parent) shows its change.
    merged = os.environ.get("MERGE_COMMIT", "")
    if argv[:2] == ["pr", "view"]:
        if "--comments" in argv or "comments" in joined:
            print(os.environ["EARLIER_VERDICT"])
        elif "mergeCommit" in joined:
            if merged:
                print(merged)
        elif "headRefName" in joined:
            print("agent/DRE-1-replayed")
        elif "headRefOid" in joined:
            print(os.environ["TODAY_SHA"])
        elif "baseRefName" in joined:
            print("main")
        sys.exit(0)
    if argv[:2] == ["pr", "diff"]:
        print("diff --git a/today b/today")
        sys.exit(0)
    if argv[:1] == ["api"]:
        if "/comments" in joined:
            print(json.dumps([{"body": os.environ["EARLIER_VERDICT"]}]))
            sys.exit(0)
        if merged and f"/commits/{merged}" in joined:
            print(os.environ["BASE_AT_MERGE"])
            sys.exit(0)
        if "/compare/" in joined:
            if os.environ.get("COMPARE_FAILS"):
                print("gh: HTTP 502", file=sys.stderr)
                sys.exit(1)
            base = joined.split("/compare/", 1)[1].split("...", 1)[0]
            behind = bool(merged) and base != os.environ.get("BASE_AT_MERGE")
            empty = behind or bool(os.environ.get("EMPTY_COMPARE"))
            if "application/vnd.github.diff" in joined:
                if not empty:
                    print("diff --git a/replayed b/replayed")
                    print("+the change at the verified head")
            elif empty:
                print(json.dumps({"status": "behind", "ahead_by": 0, "files": []}))
            else:
                print(json.dumps({"files": [{"filename": "console/src/board.tsx", "patch": "+x"},
                                            {"filename": "backend/app/api.py", "patch": "+y"}]}))
        sys.exit(0)
    sys.exit(0)
''').lstrip()

FAKE_CURL = textwrap.dedent(r'''
    #!/usr/bin/env python3
    import os, shutil, sys
    argv = sys.argv[1:]
    with open(os.environ["CURL_LOG"], "a") as fh:
        fh.write(" ".join(argv) + "\n")
    out = argv[argv.index("-o") + 1]
    url = argv[-1]
    src = os.path.join(os.environ["CONFIG_DIR"], url.rsplit("/", 1)[-1])
    if not os.path.exists(src):
        sys.exit(22)
    shutil.copy(src, out)
''').lstrip()


class _Shell(unittest.TestCase):
    """Runs one step's shell in a scratch directory, `/tmp/` paths moved into
    it, with fake `gh` and `curl` on PATH and `.bureau-pipeline` pointing at
    this checkout."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, body in (("gh", FAKE_GH), ("curl", FAKE_CURL)):
            path = self.bin / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        self.work = self.tmp / "work"
        self.work.mkdir()
        (self.work / ".bureau-pipeline").symlink_to(ROOT)
        self.runner_temp = self.tmp / "runner"
        self.runner_temp.mkdir()
        self.output = self.tmp / "github-output"
        self.output.write_text("")
        self.gh_log = self.tmp / "gh.log"
        self.curl_log = self.tmp / "curl.log"

    def run_step(self, step: dict, ctx: dict, extra_env: dict | None = None):
        script = _value(step["run"], ctx).replace("/tmp/", f"{self.tmp}/")
        env = {k: _value(v, ctx) for k, v in (step.get("env") or {}).items()}
        env.update({
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_STEP_SUMMARY": str(self.tmp / "summary"),
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "555",
            "RUNNER_TEMP": str(self.runner_temp),
            "GH_LOG": str(self.gh_log),
            "CURL_LOG": str(self.curl_log),
            "CONFIG_DIR": str(ROOT / "config"),
            "TODAY_SHA": TODAY_SHA,
            "EARLIER_VERDICT": f"🔎 {merge_gate.CRITIC_MARKER} — VERDICT: APPROVE @{REPLAY_SHA}",
        })
        env.update(extra_env or {})
        return subprocess.run(["bash", "-e", "-c", script], cwd=self.work, env=env,
                              capture_output=True, text=True, timeout=120)

    def outputs(self) -> dict:
        out = {}
        for line in self.output.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
        return out

    def gh_calls(self) -> list[list[str]]:
        if not self.gh_log.exists():
            return []
        return [json.loads(line) for line in self.gh_log.read_text().splitlines()]


# ── 1. the inputs ───────────────────────────────────────────────────────────
class TheInputsTest(unittest.TestCase):
    def test_the_four_inputs_are_declared_exactly_as_the_contract_states(self):
        inputs = _on(_doc())["workflow_call"]["inputs"]
        want = {
            "model": ("string", ""),
            "shadow": ("boolean", False),
            "replay_pr": ("string", ""),
            "replay_sha": ("string", ""),
        }
        for name, (kind, default) in want.items():
            with self.subTest(input=name):
                self.assertIn(name, inputs)
                self.assertEqual(inputs[name].get("type"), kind)
                self.assertIs(inputs[name].get("required"), False)
                self.assertIn("default", inputs[name])
                self.assertEqual(inputs[name]["default"], default)
                self.assertIs(type(inputs[name]["default"]), type(default))

    def test_the_workflow_stays_workflow_call_only(self):
        self.assertEqual(set(_on(_doc())), {"workflow_call"})


# ── 2. no new input: today's run ────────────────────────────────────────────
class NoNewInputIsTodaysRunTest(unittest.TestCase):
    def test_every_step_on_main_is_still_here_in_the_same_order(self):
        keys = [k for k, _ in _keyed(_steps())]
        main_keys = [k for k, _ in MAIN_STEPS]
        self.assertEqual([k for k in keys if k in main_keys], main_keys)

    def test_every_step_on_main_decides_as_it_did(self):
        live = dict(_keyed(_steps()))
        for key, main_if in MAIN_STEPS:
            now_if = live[key].get("if")
            self.assertEqual("always()" in str(now_if or ""), "always()" in str(main_if or ""),
                             f"{key}: the status function changed")
            for axes in _matrix():
                ctx = _ctx(**axes)
                self.assertEqual(_cond(now_if, ctx), _cond(main_if, ctx), (key, axes))

    def test_a_step_added_by_this_change_never_runs_without_the_new_inputs(self):
        main_keys = {k for k, _ in MAIN_STEPS}
        added = [(k, s) for k, s in _keyed(_steps()) if k not in main_keys]
        self.assertTrue(added, "the shadow steps are missing")
        for key, step in added:
            self.assertIn("if", step, f"{key}: a new step with no `if:` always runs")
            for axes in _matrix():
                self.assertFalse(_cond(step["if"], _ctx(**axes)), (key, axes))

    def test_the_select_model_step_is_byte_for_byte_mains(self):
        step = _step("model")
        self.assertEqual(step["name"], "Select model")
        self.assertEqual(hashlib.sha256(step["run"].encode()).hexdigest(),
                         SELECT_MODEL_RUN_SHA256)
        self.assertEqual(
            hashlib.sha256(json.dumps(step["env"], sort_keys=True).encode()).hexdigest(),
            SELECT_MODEL_ENV_SHA256)

    def test_the_verifier_gets_the_selected_model_and_its_effort(self):
        agents = _agent_steps()
        self.assertEqual(len(agents), 2)
        for step in agents:
            args = _value(step["with"]["claude_args"], _ctx()).split()
            self.assertEqual(args[args.index("--model") + 1], SELECTED, step["name"])
            self.assertEqual(args[args.index("--effort") + 1], "high", step["name"])
            self.assertEqual(args.count("--effort"), 1, step["name"])

    def test_the_agent_still_holds_the_linear_key_and_the_full_worker_token(self):
        for step in _agent_steps():
            env = step["env"]
            self.assertEqual(_value(env["LINEAR_API_KEY"], _ctx()), "fleet-key")
            self.assertEqual(_value(env["LINEAR_API_KEY_FALLBACK"], _ctx()), "fleet-key")
        mint = _step("worker")["with"]
        for key, value in mint.items():
            if key.startswith("permission-"):
                # create-github-app-token skips an empty permission input, and
                # with none set the token inherits the App's installation.
                self.assertEqual(_value(value, _ctx()), "", key)

    def test_the_workspace_checkout_takes_the_events_own_ref(self):
        step = dict(_keyed(_steps()))[CHECKOUT + "#1"]
        self.assertEqual(_value((step.get("with") or {}).get("ref", ""), _ctx()), "")


class TheBuildReviewSeparationHoldsTest(unittest.TestCase):
    def test_select_model_passes_the_build_model_or_fails_closed(self):
        run = _step("model")["run"]
        self.assertIn("model_fallback.py build-model", run)
        self.assertIn('SEPARATION_ARG="--built-on $BUILT_ON"', run)
        self.assertIn('SEPARATION_ARG="--built-on-unknown"', run)
        self.assertIn("$SEPARATION_ARG", run)

    def test_select_model_runs_in_shadow_too(self):
        self.assertTrue(_cond(_step("model")["if"], _shadow()))


# ── 3. the guard ────────────────────────────────────────────────────────────
class TheGuardTest(_Shell):
    def guard(self):
        return _step("shadow_guard")

    def refused(self, ctx: dict) -> bool:
        proc = self.run_step(self.guard(), ctx)
        return proc.returncode != 0

    def test_the_guard_is_the_first_step(self):
        self.assertEqual(_steps()[0].get("id"), "shadow_guard")

    def test_the_guard_runs_when_either_shadow_or_a_model_is_set(self):
        self.assertTrue(_cond(self.guard()["if"], _shadow()))
        self.assertTrue(_cond(self.guard()["if"], _ctx(model=NAMED)))
        self.assertFalse(_cond(self.guard()["if"], _ctx()))

    def test_a_live_verification_never_runs_on_a_named_model(self):
        self.assertTrue(self.refused(_ctx(model=NAMED)))
        self.assertTrue(self.refused(_ctx(model=NAMED, replay_pr=REPLAY_PR,
                                          replay_sha=REPLAY_SHA)))

    def test_a_good_shadow_input_passes(self):
        proc = self.run_step(self.guard(), _shadow())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # It read the two files at the caller's pipeline ref.
        log = self.curl_log.read_text()
        self.assertIn("/bureau-pipeline/main/config/model-prices.yaml", log)
        self.assertIn("/bureau-pipeline/main/config/models.yaml", log)

    def test_shadow_needs_all_three(self):
        for missing in ("model", "replay_pr", "replay_sha"):
            with self.subTest(missing=missing):
                self.assertTrue(self.refused(_shadow(**{missing: ""})))

    def test_replay_sha_is_forty_lowercase_hex(self):
        for bad in (REPLAY_SHA.upper(), REPLAY_SHA[:39], REPLAY_SHA + "0",
                    REPLAY_SHA[:39] + "g", REPLAY_SHA + "\n" + REPLAY_SHA):
            with self.subTest(sha=bad):
                self.assertTrue(self.refused(_shadow(replay_sha=bad)))

    def test_replay_pr_is_a_pull_request_number(self):
        for bad in ("abc", "0", "-3", "12 ; true"):
            with self.subTest(pr=bad):
                self.assertTrue(self.refused(_shadow(replay_pr=bad)))

    def test_the_model_must_be_priced(self):
        self.assertTrue(self.refused(_shadow(model="claude-nobody-priced-1")))

    def test_an_excluded_or_retired_model_is_refused(self):
        cfg = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())
        prices = yaml.safe_load((ROOT / "config" / "model-prices.yaml").read_text())["prices"]
        barred = [e["model"] for sec in ("excluded", "retired") for e in cfg.get(sec) or []]
        self.assertTrue(barred)
        priced_and_barred = [m for m in barred if m in prices]
        self.assertTrue(priced_and_barred, "no barred model is priced — the case is vacuous")
        for model in priced_and_barred:
            with self.subTest(model=model):
                self.assertTrue(self.refused(_shadow(model=model)))

    def test_an_unreadable_config_refuses(self):
        proc = self.run_step(self.guard(), _shadow(), {"CONFIG_DIR": str(self.tmp / "nowhere")})
        self.assertNotEqual(proc.returncode, 0)

    def test_no_agent_step_can_run_after_the_guard_fails(self):
        steps = _steps()
        guard_at = next(i for i, s in enumerate(steps) if s.get("id") == "shadow_guard")
        agents_at = [i for i, s in enumerate(steps)
                     if str(s.get("uses", "")).split("@")[0] == AGENT_ACTION]
        self.assertTrue(agents_at)
        self.assertTrue(all(guard_at < i for i in agents_at))
        # After a failed step GitHub runs only the steps that ask for always();
        # with nothing before it having run, every output reads empty.
        dead = _ctx(model=NAMED, in_scope="", install="")
        for i in agents_at:
            cond = steps[i].get("if")
            if "always()" in str(cond or ""):
                self.assertFalse(_cond(cond, dead), steps[i]["name"])


# ── 4. posts nothing ────────────────────────────────────────────────────────
class ShadowPostsNothingTest(unittest.TestCase):
    def test_the_discovery_finds_todays_writer(self):
        self.assertIn("Post verdict or neutral status", _writers())

    def test_the_discovery_reads_what_a_step_calls(self):
        for run in ('gh pr comment "$PR" --body-file f',
                    "gh pr edit 3 --add-label x",
                    "gh pr review 3 --approve",
                    'python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" hi',
                    "python3 x/linear_ops.py state DRE-1 Done",
                    'gh api -X POST "repos/o/r/issues/1/comments"',
                    "gh api --method PATCH repos/o/r/pulls/1",
                    "gh api repos/o/r/issues/1/labels \\\n  -f labels[]=x"):
            with self.subTest(run=run):
                self.assertTrue(any(p.search(_joined(run)) for p in WRITE_CALLS))
        for run in ('gh pr view "$PR" --json headRefOid',
                    'gh api "repos/o/r/compare/main...$SHA"',
                    'gh api -H "Accept: application/vnd.github.diff" "repos/o/r/compare/a...b"',
                    "python3 x/linear_ops.py description DRE-1"):
            with self.subTest(run=run):
                self.assertFalse(any(p.search(_joined(run)) for p in WRITE_CALLS))

    def test_every_writer_is_skipped_in_shadow(self):
        writers = _writers()
        self.assertTrue(writers)
        for key in writers:
            cond = dict(_keyed(_steps()))[key].get("if")
            for axes in _matrix():
                self.assertFalse(_cond(cond, _shadow(**axes)), (key, axes))

    def test_the_qa_bot_mint_is_skipped_in_shadow(self):
        cond = _step("qa").get("if")
        for axes in _matrix():
            self.assertFalse(_cond(cond, _shadow(**axes)), axes)

    def test_a_crashed_shadow_run_does_not_fail_on_the_neutral_rule(self):
        cond = _step("Fail if verifier never really ran")["if"]
        self.assertTrue(_cond(cond, _ctx(real1="false", real2="false")))
        self.assertFalse(_cond(cond, _shadow(real1="false", real2="false")))

    def test_the_receipt_the_reading_and_the_log_keep_running(self):
        for key in ("Keep the run's death-cause receipt",
                    "Record the GitHub calls per pool login",
                    "Keep the run's working log"):
            with self.subTest(step=key):
                self.assertTrue(_cond(_step(key).get("if"), _shadow()))
        self.assertTrue(_cond(_step("Upload the death-cause receipt")["if"],
                              _shadow(death_path="/r/d.json")))


# ── 5. the replayed head, the key and the token ─────────────────────────────
class TheReplayedHeadTest(_Shell):
    def test_the_agent_holds_no_linear_key_in_shadow(self):
        for step in _agent_steps():
            for key, value in (step.get("env") or {}).items():
                if "LINEAR" in key and "KEY" in key:
                    self.assertEqual(_value(value, _shadow()), "", (step["name"], key))
        job_env = _doc()["jobs"]["verify"].get("env") or {}
        self.assertFalse([k for k in job_env if "LINEAR_API_KEY" in k])

    def test_the_agents_token_is_minted_read_only_in_shadow(self):
        mint = _step("worker")
        self.assertTrue(_cond(mint["if"], _shadow()))
        perms = {k: _value(v, _shadow()) for k, v in mint["with"].items()
                 if k.startswith("permission-")}
        self.assertEqual({k: v for k, v in perms.items() if v},
                         {"permission-contents": "read", "permission-pull-requests": "read"})
        for step in _agent_steps():
            self.assertEqual(step["with"]["github_token"], "${{ steps.worker.outputs.token }}")

    def test_the_checkout_is_at_replay_sha(self):
        step = dict(_keyed(_steps()))[CHECKOUT + "#1"]
        self.assertEqual(_value(step["with"]["ref"], _shadow()), REPLAY_SHA)

    def test_the_agent_runs_the_named_model(self):
        for step in _agent_steps():
            self.assertIn("--model ${{ inputs.shadow && steps.shadow_model.outputs.model "
                          "|| steps.model.outputs.model }}", step["with"]["claude_args"])
            args = _value(step["with"]["claude_args"], _shadow()).split()
            self.assertEqual(args[args.index("--model") + 1], NAMED)
            self.assertEqual(args.count("--effort"), 1)
            self.assertEqual(args[args.index("--effort") + 1], "max")

    def test_a_named_model_with_no_level_runs_with_no_effort(self):
        # Never the selected model's level: that is a level computed for a
        # model the replay does not run.
        for step in _agent_steps():
            args = _value(step["with"]["claude_args"], _shadow(shadow_effort="")).split()
            self.assertEqual(args[args.index("--model") + 1], NAMED)
            self.assertNotIn("--effort", args, step["name"])

    def test_the_named_models_effort_comes_from_the_config(self):
        step = _step("shadow_model")
        self.assertTrue(_cond(step["if"], _shadow()))
        self.assertFalse(_cond(step["if"], _shadow(in_scope="false")))
        proc = self.run_step(step, _shadow())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        want = subprocess.run([sys.executable, str(ROOT / "scripts" / "model_fallback.py"),
                               "effort", NAMED], capture_output=True, text=True).stdout.strip()
        self.assertEqual(self.outputs()["effort_arg"], f"--effort {want}" if want else "")
        self.assertEqual(self.outputs()["model"], NAMED)

    def test_resolve_pr_takes_the_number_and_the_commit_from_the_inputs(self):
        proc = self.run_step(_step("pr"), _shadow())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = self.outputs()
        self.assertEqual(out["number"], REPLAY_PR)
        self.assertEqual(out["sha"], REPLAY_SHA)
        self.assertEqual(out["card"], "DRE-1")
        calls = self.gh_calls()
        self.assertFalse([c for c in calls if "headRefOid" in " ".join(c)],
                         "shadow asked GitHub for today's head")
        self.assertTrue(all(REPLAY_PR in c for c in calls if c[:2] == ["pr", "view"]))

    def test_the_diff_file_is_the_compare_of_the_base_branch_and_replay_sha(self):
        proc = self.run_step(_step("pr"), _shadow())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        diff_calls = [c for c in self.gh_calls()
                      if c[:1] == ["api"] and "application/vnd.github.diff" in " ".join(c)]
        self.assertEqual(len(diff_calls), 1, self.gh_calls())
        self.assertIn(f"repos/{REPO}/compare/main...{REPLAY_SHA}", diff_calls[0])
        diff = (self.tmp / "shadow-replay.diff").read_text()
        self.assertIn("the change at the verified head", diff)
        self.assertFalse([c for c in self.gh_calls() if c[:2] == ["pr", "diff"]])
        # The prompt names that file, and stops the agent fetching today's diff.
        for step in _agent_steps():
            prompt = _value(step["with"]["prompt"], _shadow())
            self.assertIn("/tmp/shadow-replay.diff", prompt)
            self.assertIn("Do not run `gh pr diff`", prompt)

    MERGED = {"MERGE_COMMIT": "a" * 40, "BASE_AT_MERGE": "b" * 40}

    def test_a_merged_pull_request_is_compared_with_the_base_it_merged_into(self):
        # A replay is of a past, judged pull request, so most have merged. Its
        # head is then an ancestor of `main` today and `main...<sha>` is empty:
        # the diff and the scope gate's files come from the merge commit's
        # first parent, the base branch as it stood at the merge.
        proc = self.run_step(_step("pr"), _shadow(), self.MERGED)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        compares = [" ".join(c) for c in self.gh_calls() if c[:1] == ["api"] and "/compare/" in " ".join(c)]
        self.assertEqual(len(compares), 2, compares)
        for call in compares:
            self.assertIn(f"repos/{REPO}/compare/{'b' * 40}...{REPLAY_SHA}", call)
            self.assertNotIn(f"compare/main...", call)
        self.assertIn("the change at the verified head", (self.tmp / "shadow-replay.diff").read_text())
        proc = self.run_step(_step("scope"), _shadow())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        changed = (self.tmp / "verify" / "changed.txt").read_text().split()
        self.assertEqual(changed, ["console/src/board.tsx", "backend/app/api.py"])

    def test_an_empty_replay_diff_fails_the_step_and_leaves_no_diff_file(self):
        for env in ({"EMPTY_COMPARE": "1"}, {"COMPARE_FAILS": "1"}):
            with self.subTest(env=env):
                (self.tmp / "shadow-replay.diff").unlink(missing_ok=True)
                proc = self.run_step(_step("pr"), _shadow(), env)
                self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertFalse((self.tmp / "shadow-replay.diff").exists())

    def test_a_live_resolve_never_asks_for_the_merge_commit(self):
        proc = self.run_step(_step("pr"), _ctx(), self.MERGED)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse([c for c in self.gh_calls() if "mergeCommit" in " ".join(c)])
        compares = [" ".join(c) for c in self.gh_calls() if "/compare/" in " ".join(c)]
        self.assertEqual(len(compares), 1, compares)
        self.assertIn(f"repos/{REPO}/compare/main...{TODAY_SHA}", compares[0])

    def test_a_live_resolve_reads_todays_head_and_writes_no_diff_file(self):
        proc = self.run_step(_step("pr"), _ctx())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.outputs()["sha"], TODAY_SHA)
        self.assertFalse((self.tmp / "shadow-replay.diff").exists())

    def test_the_scope_gate_reads_the_files_of_the_replayed_head(self):
        proc = self.run_step(_step("pr"), _shadow())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.run_step(_step("scope"), _shadow())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        changed = (self.tmp / "verify" / "changed.txt").read_text().split()
        self.assertEqual(changed, ["console/src/board.tsx", "backend/app/api.py"])
        self.assertFalse([c for c in self.gh_calls() if c[:2] == ["pr", "diff"]])


# ── 6. no hindsight ─────────────────────────────────────────────────────────
class NoHindsightTest(_Shell):
    MARKERS = (merge_gate.CRITIC_MARKER, merge_gate.VERIFIER_MARKER)

    def _prompt_ctx(self) -> dict:
        """The shadow context, every output the prompt reads produced by the
        live steps' own shell against a pull request whose thread carries an
        earlier verdict."""
        self.assertEqual(self.run_step(_step("pr"), _shadow()).returncode, 0)
        ctx = _shadow()
        ctx["steps"]["pr"]["outputs"] = self.outputs()
        return ctx

    def test_no_earlier_verdict_reaches_the_shadow_prompt(self):
        ctx = self._prompt_ctx()
        for step in _agent_steps():
            prompt = _value(step["with"]["prompt"], ctx)
            for marker in self.MARKERS:
                self.assertNotIn(marker, prompt, step["name"])
        # Nothing the prompt reads came from the pull request's comments.
        self.assertFalse([c for c in self.gh_calls() if "comments" in " ".join(c)])

    def test_the_prompt_reads_only_values_that_carry_no_comment_text(self):
        allowed = re.compile(r"^(inputs\.\w+|steps\.pr\.outputs\.(number|card|sha))$")
        for step in _agent_steps():
            for expr in re.findall(r"\$\{\{(.+?)\}\}", step["with"]["prompt"]):
                for path in re.findall(r"\b(?:inputs|steps|github|env|vars|secrets)\.[\w.]+", expr):
                    self.assertRegex(path, allowed, step["name"])

    def test_the_prompt_tells_the_verifier_not_to_read_the_comments(self):
        for step in _agent_steps():
            shadow = _value(step["with"]["prompt"], _shadow())
            self.assertIn("Do not read the pull request's comments", shadow)
            live = _value(step["with"]["prompt"], _ctx())
            self.assertNotIn("Do not read the pull request's comments", live)
            self.assertNotIn("SHADOW REPLAY", live)

    def test_the_card_handed_over_is_the_description_never_the_thread(self):
        snap = self.runner_temp / "card-snapshot.json"
        snap.write_text(json.dumps({
            "description": "## Acceptance criteria\n- [ ] the board moves cards",
            "comments": [f"🧪 {merge_gate.VERIFIER_MARKER} — VERDICT: FAIL @{REPLAY_SHA}",
                         f"🔎 {merge_gate.CRITIC_MARKER} — VERDICT: APPROVE @{REPLAY_SHA}"],
            "labels": [], "source": "linear",
        }))
        step = _step("shadow_card")
        self.assertTrue(_cond(step["if"], _shadow()))
        proc = self.run_step(step, _shadow())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        card = (self.tmp / "shadow-card.md").read_text()
        self.assertIn("the board moves cards", card)
        for marker in self.MARKERS:
            self.assertNotIn(marker, card)
        for step in _agent_steps():
            self.assertIn("/tmp/shadow-card.md", _value(step["with"]["prompt"], _shadow()))

    def test_the_cards_thread_is_off_the_runner_before_the_agent_runs(self):
        # The snapshot and the thread Select model wrote carry the earlier
        # verdicts, and the agent holds Bash and Read: a shadow-only step
        # removes both, and the compare the scope gate read, before it starts.
        keys = [k for k, _ in _keyed(_steps())]
        step = _step("shadow_scrub")
        at = keys.index(step["name"])
        for before in ("Select model", "Effort for the named model (shadow)",
                       "Decide scope (multi-system / UI?)", "Hand the replay the card's description (shadow)"):
            self.assertLess(keys.index(before), at, before)
        for agent in _agent_steps():
            self.assertLess(at, keys.index(agent["name"]), agent["name"])
        self.assertTrue(_cond(step["if"], _shadow()))
        self.assertTrue(_cond(step["if"], _shadow(in_scope="false")))
        self.assertFalse(_cond(step["if"], _ctx()))
        files = [self.runner_temp / "card-snapshot.json", self.runner_temp / "card-thread.json",
                 self.tmp / "verify-compare.json"]
        for f in files:
            f.write_text(f"🔎 {merge_gate.CRITIC_MARKER} — VERDICT: APPROVE @{REPLAY_SHA}")
        proc = self.run_step(step, _shadow())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for f in files:
            self.assertFalse(f.exists(), f.name)

    def test_the_prompt_names_the_routes_to_the_comments(self):
        for step in _agent_steps():
            shadow = _value(step["with"]["prompt"], _shadow())
            self.assertIn("`gh pr view --comments`", shadow)
            self.assertIn("`gh api`", shadow)


# ── 7. the shadow verdict ───────────────────────────────────────────────────
class TheShadowVerdictTest(_Shell):
    KEYS = {"role", "repo", "pr", "sha", "model", "verdict", "real", "run_url"}

    def write(self, ctx: dict, verdict_md: str | None = None):
        if verdict_md is not None:
            (self.tmp / "verify-verdict.md").write_text(verdict_md)
        proc = self.run_step(_step("shadow_verdict"), ctx)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = self.outputs()
        folder = Path(out["dir"])
        doc = json.loads((folder / "shadow-verdict.json").read_text())
        md = (folder / "shadow-verdict.md").read_text()
        return doc, md, out, proc

    def test_it_runs_only_in_shadow(self):
        cond = _step("shadow_verdict")["if"]
        self.assertTrue(_cond(cond, _shadow(in_scope="false")))
        self.assertTrue(_cond(cond, _shadow(real1="false", real2="false")))
        self.assertFalse(_cond(cond, _shadow(guard="failure")))
        self.assertFalse(_cond(cond, _ctx()))

    def test_a_real_pass_is_written_with_every_contract_key(self):
        md = "VERDICT: PASS\n\n## Summary\nIt worked.\n"
        doc, written, out, proc = self.write(_shadow(real1="true"), md)
        self.assertEqual(set(doc) - {"reason"}, self.KEYS)
        self.assertEqual(doc["role"], "verifier")
        self.assertEqual(doc["repo"], REPO)
        self.assertEqual(doc["pr"], int(REPLAY_PR))
        self.assertIs(type(doc["pr"]), int)
        self.assertEqual(doc["sha"], REPLAY_SHA)
        self.assertEqual(doc["model"], NAMED)
        self.assertEqual(doc["verdict"], "PASS")
        self.assertIs(doc["real"], True)
        self.assertEqual(doc["run_url"], f"https://github.com/{REPO}/actions/runs/555")
        self.assertEqual(written, md)
        self.assertEqual(
            out["line"],
            f"shadow-verdict: role=verifier pr={REPLAY_PR} sha={REPLAY_SHA} "
            f"model={NAMED} verdict=PASS")
        # The line is said once, by the step after the upload — not here.
        self.assertNotIn("shadow-verdict:", proc.stdout)

    def test_a_real_fail_from_the_retry(self):
        doc, _md, out, _ = self.write(_shadow(real1="false", real2="true"),
                                      "VERDICT: FAIL\n\n## Summary\nbroken\n")
        self.assertEqual(doc["verdict"], "FAIL")
        self.assertIs(doc["real"], True)
        self.assertTrue(out["line"].endswith(" verdict=FAIL"))

    def test_out_of_scope_writes_skip(self):
        (self.tmp / "verify-verdict.md").write_text("VERDICT: PASS\nstale from another job\n")
        doc, md, out, _ = self.write(_shadow(in_scope="false"))
        self.assertEqual(doc["verdict"], "SKIP")
        self.assertIs(doc["real"], False)
        self.assertEqual(doc["reason"], "out of scope")
        self.assertEqual(md, "")
        self.assertTrue(out["line"].endswith(" verdict=SKIP"))

    def test_a_crashed_verifier_writes_null(self):
        doc, md, out, _ = self.write(_shadow(real1="false", real2="false"))
        self.assertIsNone(doc["verdict"])
        self.assertIs(doc["real"], False)
        self.assertEqual(md, "")
        self.assertTrue(out["line"].endswith(" verdict=none"))

    def test_a_run_that_never_reached_the_scope_gate_writes_null(self):
        doc, _md, out, _ = self.write(_shadow(in_scope=""))
        self.assertIsNone(doc["verdict"])
        self.assertIs(doc["real"], False)
        self.assertTrue(out["line"].endswith(" verdict=none"))

    def test_the_artifact_and_the_line(self):
        upload = _step("shadow_upload")
        self.assertTrue(upload["uses"].startswith("actions/upload-artifact@"))
        self.assertEqual(_value(upload["with"]["name"], _shadow()),
                         f"shadow-verdict-verifier-{REPLAY_PR}")
        self.assertEqual(upload["with"]["path"], "${{ steps.shadow_verdict.outputs.dir }}")
        self.assertTrue(_cond(upload["if"], _shadow(verdict_dir="/r/sv")))
        self.assertFalse(_cond(upload["if"], _shadow()))
        say = _step("shadow_line")
        self.assertTrue(_cond(say["if"], _shadow(verdict_dir="/r/sv")))
        self.assertFalse(_cond(say["if"], _shadow(verdict_dir="/r/sv", upload="failure")))
        ctx = _shadow(verdict_dir="/r/sv")
        ctx["steps"]["shadow_verdict"]["outputs"]["line"] = (
            f"shadow-verdict: role=verifier pr={REPLAY_PR} sha={REPLAY_SHA} "
            f"model={NAMED} verdict=PASS")
        proc = self.run_step(say, ctx)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("shadow-verdict:")]
        self.assertEqual(lines, [ctx["steps"]["shadow_verdict"]["outputs"]["line"]])
        steps = [s.get("id") for s in _steps()]
        self.assertLess(steps.index("shadow_verdict"), steps.index("shadow_upload"))
        self.assertLess(steps.index("shadow_upload"), steps.index("shadow_line"))


if __name__ == "__main__":
    unittest.main()
