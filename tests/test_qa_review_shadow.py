"""The critic's shadow mode — a past pull request replayed, posting nothing (DRE-7193).

The four sibling epics (DRE-7185, DRE-7187, DRE-7188, DRE-7189) measure a
switch by replaying past reviews on the model they name and comparing the
verdicts. This file holds the critic half of that tool: four `workflow_call`
inputs on `qa-review.yml`, and what each of them may and may not change.

What is pinned here, and why each one is executed or evaluated rather than
grepped:

  * the CONTRACT the verifier card (DRE-7194) and the batch card (DRE-7197)
    read — the inputs, the artifact name, the JSON keys, the log line — by
    running the step that writes them;
  * that a run with NO new input set is the review `main` ran before this
    card: the same steps, the same `if:`s, the same Select model command and
    the same `--model`, compared to a snapshot of the pre-change job taken
    from `main` at afa91e9f;
  * that a shadow run POSTS NOTHING, with the writing steps discovered by what
    their `run:` calls — never by a list of names — so a writer added later is
    held to the same rule;
  * that a bad request is refused before any agent step, by running the two
    refusing steps and walking the job after the failure;
  * that no earlier verdict reaches the replay's context, by running the card
    context step over a thread that holds one.

Every `${{ }}` is evaluated by a small three-valued reading of Actions'
expression language (`_Eval`), the one `test_planner_queue_wiring.py` walks
plan.yml with, reduced to what this job's expressions use: a value it cannot
know is a `Sym` carrying the name it was read from, so `--model` can be shown
to resolve to `steps.model.outputs.model` rather than merely "something".
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"
SCRIPTS = ROOT / "scripts"

REPO = "dreadnought-foundry/portico"
REPLAY_PR = "1081"
REPLAY_SHA = "42d305b2b6301cfd884ddef4169e64898b57c4ee"
BASE_OID = "b2a6e72656e691b2449c798fab3be65b1472cba3"
TODAY_HEAD = "9" * 40
QA_LOGIN = "agent-bureau-qa-bot[bot]"


def _doc() -> dict:
    return yaml.safe_load(QA_REVIEW.read_text())


def _on(doc: dict) -> dict:
    # PyYAML reads the bare key `on` as the boolean True.
    return doc.get("on", doc.get(True))


def _steps() -> list[dict]:
    return _doc()["jobs"]["review"]["steps"]


def _key(step: dict) -> str:
    return step.get("name") or step.get("uses") or ""


def _step(**match) -> dict:
    for s in _steps():
        if all(s.get(k) == v for k, v in match.items()):
            return s
    raise AssertionError(f"qa-review.yml has no step matching {match}")


def _index(step: dict) -> int:
    return next(i for i, s in enumerate(_steps()) if s is step or s == step)


# ── the job as it stood on main before this card (afa91e9f) ─────────────────
#
# (name, or `uses` for the two unnamed checkouts) and the step's `if:`.
PRE_CHANGE_STEPS = [
    ('Mint qa-bot token (separate quota from relay dispatch)', None),
    ('actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1', None),
    ('actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1', None),
    ("Move the pipeline checkout out of the PR's working tree", None),
    ('Decide review', None),
    ('Mint pool probe token (2)', "steps.decide.outputs.review == 'true' && env.BUREAU_APP_ID_2 != ''"),
    ('Mint pool probe token (3)', "steps.decide.outputs.review == 'true' && env.BUREAU_APP_ID_3 != ''"),
    ('Mint pool probe token (4)', "steps.decide.outputs.review == 'true' && env.BUREAU_APP_ID_4 != ''"),
    ('Select dispatch-pool app', "steps.decide.outputs.review == 'true'"),
    ('Mint read token (selected pool app)', "steps.decide.outputs.review == 'true'"),
    ('Take the GitHub-calls before points', None),
    ('Resolve PR', "steps.decide.outputs.review == 'true'"),
    ('Read the card once (read door, else one Linear read)', "steps.decide.outputs.review == 'true'"),
    ('Size the diff and pick the review strategy', "steps.decide.outputs.review == 'true'"),
    ('Assemble critic context', "steps.decide.outputs.review == 'true'"),
    ('Plan visual QA', "steps.decide.outputs.review == 'true' && steps.size.outputs.strategy != 'oversized'"),
    ("Resolve the consumer's node version", "steps.decide.outputs.review == 'true' && steps.vqplan.outputs.run == 'True'"),
    ("Set up the consumer's node", "steps.decide.outputs.review == 'true' && steps.vqnode.outputs.setup == 'true'"),
    ('Render affected screens', "steps.decide.outputs.review == 'true' && steps.vqplan.outputs.run == 'True'"),
    ('Build critic visual context', "always() && !cancelled() && steps.decide.outputs.review == 'true'"),
    ('Build repair-PR review context', "steps.decide.outputs.review == 'true'"),
    ('Does this PR change the act registry?', "steps.decide.outputs.review == 'true'"),
    ('Mint console token (act-registry receipt — best effort)', "steps.actsreg.outputs.touched == 'true'"),
    ('Build card review context', "steps.decide.outputs.review == 'true'"),
    ('Mint the model-home read token', "steps.decide.outputs.review == 'true'"),
    ('Select model', "steps.decide.outputs.review == 'true'"),
    ('Install Claude Code (asserted)', "steps.decide.outputs.review == 'true' && steps.size.outputs.strategy != 'oversized'"),
    ('Stamp critic attempt 1 start', "steps.decide.outputs.review == 'true' && steps.size.outputs.strategy != 'oversized'"),
    ('Critic review', "steps.decide.outputs.review == 'true' && steps.size.outputs.strategy != 'oversized'"),
    ('Gate critic result (attempt 1)', "always() && !cancelled() && steps.decide.outputs.review == 'true' && steps.size.outputs.strategy != 'oversized'"),
    ('Clear stale verdict before retry', "always() && !cancelled() && steps.decide.outputs.review == 'true' && steps.size.outputs.strategy != 'oversized' && steps.gate1.outputs.real != 'true'"),
    ('Back off before the critic retry', "always() && !cancelled() && steps.install_claude.outcome == 'success' && steps.decide.outputs.review == 'true' && steps.size.outputs.strategy != 'oversized' && steps.gate1.outputs.real != 'true'"),
    ('Stamp critic attempt 2 start', "always() && !cancelled() && steps.install_claude.outcome == 'success' && steps.decide.outputs.review == 'true' && steps.size.outputs.strategy != 'oversized' && steps.gate1.outputs.real != 'true'"),
    ('Critic review (retry)', "always() && !cancelled() && steps.install_claude.outcome == 'success' && steps.decide.outputs.review == 'true' && steps.size.outputs.strategy != 'oversized' && steps.gate1.outputs.real != 'true'"),
    ('Gate critic result (attempt 2)', "always() && !cancelled() && steps.decide.outputs.review == 'true' && steps.size.outputs.strategy != 'oversized' && steps.gate1.outputs.real != 'true'"),
    ("Keep the critic's Claude usage reading", "always() && !cancelled() && steps.decide.outputs.review == 'true' && steps.size.outputs.strategy != 'oversized'"),
    ('Upload the usage reading', "always() && !cancelled() && steps.decide.outputs.review == 'true' && steps.size.outputs.strategy != 'oversized' && steps.usage.outputs.path != ''"),
    ("Keep the run's death-cause receipt", 'always()'),
    ('Upload the death-cause receipt', "always() && steps.death.outputs.path != ''"),
    ('Record the GitHub calls per pool login', 'always()'),
    ("Keep the run's working log", 'always()'),
    ('Post verdict or neutral status', "always() && !cancelled() && steps.decide.outputs.review == 'true'"),
    ('Card → In Review (a pull request opened outside the dispatch)', "always() && !cancelled() && (github.event.action == 'opened' || github.event.action == 'reopened' || github.event.action == 'ready_for_review') && !github.event.pull_request.draft"),
    ('Mint dispatch-bot token (checks:write — qa-bot has checks:read)', "always() && !cancelled() && (steps.decide.outputs.review == 'true' || steps.decide.outputs.carried_sha != '')"),
    ('Publish review check on the reviewed head', "always() && !cancelled() && steps.decide.outputs.review == 'true'"),
    ('Re-publish the review check for a carried verdict', "always() && !cancelled() && steps.decide.outputs.review != 'true' && steps.decide.outputs.carried_sha != ''"),
    ('Fail if critic never really ran', "always() && !cancelled() && steps.decide.outputs.review == 'true' && steps.size.outputs.strategy != 'oversized' && steps.gate1.outputs.real != 'true' && steps.gate2.outputs.real != 'true'"),
    ('Fail fast — pull request too large to review', "always() && !cancelled() && steps.decide.outputs.review == 'true' && steps.size.outputs.strategy == 'oversized'"),
]

# The Select model step's `run:` on main at afa91e9f, byte for byte.
PRE_CHANGE_SELECT_RUN = '''\
WHY_FILE="$RUNNER_TEMP/model-why.txt"
BUILT_ON=""
if [ -n "$CARD" ] && python3 "$PIPELINE_DIR"/scripts/card_snapshot.py thread \\
     "$RUNNER_TEMP/card-snapshot.json" > "$RUNNER_TEMP/card-thread.json"; then
  BUILT_ON=$(python3 \\
    "$PIPELINE_DIR"/scripts/model_fallback.py build-model \\
    "$RUNNER_TEMP/card-thread.json")
fi
if [ -n "$BUILT_ON" ]; then
  SEPARATION_ARG="--built-on $BUILT_ON"
else
  # No card, no thread, or no heartbeat we recognise — fail CLOSED.
  SEPARATION_ARG="--built-on-unknown"
fi
# shellcheck disable=SC2086 # deliberate word split: one flag or two
EFFORT_FILE="$RUNNER_TEMP/model-effort.txt"
MODEL=$(python3 "$PIPELINE_DIR"/scripts/model_fallback.py select critic \\
  --repo "$GITHUB_REPOSITORY" \\
  $SEPARATION_ARG \\
  --explain-file "$WHY_FILE" --effort-file "$EFFORT_FILE")
WHY=$(cat "$WHY_FILE")
# DRE-4836: the CHOSEN model's declared effort level, empty when it
# declares none — see agent-task.yml's Select model step.
EFFORT=$(cat "$EFFORT_FILE" 2>/dev/null || true)
echo "model=$MODEL" >> "$GITHUB_OUTPUT"
echo "effort_arg=${EFFORT:+--effort $EFFORT}" >> "$GITHUB_OUTPUT"
echo "why=$WHY" >> "$GITHUB_OUTPUT"
echo "selected model: $MODEL (agent: critic)"
echo "$WHY" >> "$GITHUB_STEP_SUMMARY"
# Never silent: an advisory role that did not get the STRONGEST
# model is the silently-weakened critic DRE-2317 exists to prevent.
case "$WHY" in DEGRADED*) echo "::warning::$WHY" ;; esac
'''


# ── a three-valued reading of Actions expressions ───────────────────────────


class Sym:
    """A value the reading cannot know: the name it was read from, and the
    truthiness it CAN know, if any."""

    def __init__(self, name: str = "?", truth: bool | None = None):
        self.name, self.truth = name, truth

    def __repr__(self) -> str:
        return f"Sym({self.name!r})"


def _truth(value) -> bool | None:
    if isinstance(value, Sym):
        return value.truth
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value != ""
    if isinstance(value, float):
        return value != 0
    return bool(value)


_TOKEN = re.compile(r"""
    \s*(?:
      (?P<str>'(?:[^']|'')*')
     |(?P<num>-?\d+(?:\.\d+)?)
     |(?P<op>==|!=|<=|>=|&&|\|\||[!<>(),\[\]])
     |(?P<name>[A-Za-z_][A-Za-z0-9_\-]*(?:\.[A-Za-z_*][A-Za-z0-9_\-]*)*)
    )""", re.X)


class _Eval:
    """Evaluates one expression against `known` — `inputs.*`, `github.*`,
    `steps.<id>.outputs.*` — and the status of the job (`failed`). A step id
    in `skipped` reads as Actions reads a skipped step: empty outputs and the
    outcome `skipped`."""

    def __init__(self, known: dict | None = None, skipped: set | None = None,
                 failed: bool | None = False):
        self.known, self.skipped, self.failed = known or {}, skipped or set(), failed

    def __call__(self, expr):
        if expr is None:
            return True
        if isinstance(expr, bool):
            return expr
        expr = str(expr).strip()
        if expr.startswith("${{") and expr.endswith("}}"):
            expr = expr[3:-2]
        self.toks, self.at = [], 0
        pos = 0
        while expr[pos:].strip():
            m = _TOKEN.match(expr, pos)
            if not m or m.end() == pos:
                raise ValueError(f"cannot read {expr[pos:]!r}")
            self.toks.append((m.lastgroup, m.group(m.lastgroup)))
            pos = m.end()
        value = self._or()
        assert self.at == len(self.toks), f"trailing tokens in {expr!r}"
        return value

    def _peek(self):
        return self.toks[self.at] if self.at < len(self.toks) else (None, None)

    def _take(self, text=None):
        tok = self._peek()
        if text is not None and tok[1] != text:
            raise ValueError(f"expected {text!r}, found {tok[1]!r}")
        self.at += 1
        return tok

    def _or(self):
        left = self._and()
        while self._peek()[1] == "||":
            self._take()
            right = self._and()
            t = _truth(left)
            if t is False:
                left = right
            elif t is None:
                if _truth(right) is False:
                    # `x || ''` is x for any string x: keep the name.
                    left = Sym(left.name, None)
                else:
                    left = Sym("?", True if _truth(right) is True else None)
        return left

    def _and(self):
        left = self._cmp()
        while self._peek()[1] == "&&":
            self._take()
            right = self._cmp()
            t = _truth(left)
            if t is True:
                left = right
            elif t is None:
                left = Sym("?", False if _truth(right) is False else None)
        return left

    def _cmp(self):
        left = self._unary()
        while self._peek()[1] in ("==", "!=", "<", ">", "<=", ">="):
            op = self._take()[1]
            right = self._unary()
            if isinstance(left, Sym) or isinstance(right, Sym) or op not in ("==", "!="):
                left = Sym()
                continue
            a, b = (("" if v is None else v.lower() if isinstance(v, str) else v)
                    for v in (left, right))
            left = (a == b) if op == "==" else (a != b)
        return left

    def _unary(self):
        if self._peek()[1] == "!":
            self._take()
            t = _truth(self._unary())
            return Sym() if t is None else (not t)
        return self._primary()

    def _primary(self):
        kind, text = self._take()
        if text == "(":
            value = self._or()
            self._take(")")
            return value
        if kind == "str":
            return text[1:-1].replace("''", "'")
        if kind == "num":
            return float(text)
        if self._peek()[1] == "(":
            self._take()
            while self._peek()[1] != ")":
                self._or()
                if self._peek()[1] == ",":
                    self._take()
            self._take(")")
            return self._call(text)
        return self._name(text)

    def _call(self, name: str):
        if name == "always":
            return True
        if name == "cancelled":
            return False
        if name in ("success", "failure") and self.failed is not None:
            return (not self.failed) if name == "success" else self.failed
        return Sym(f"{name}()")

    def _name(self, text: str):
        if text in ("true", "false"):
            return text == "true"
        if text == "null":
            return None
        if text in self.known:
            return self.known[text]
        parts = text.split(".")
        if parts[0] == "steps" and len(parts) >= 3 and parts[1] in self.skipped:
            return "skipped" if parts[2] in ("outcome", "conclusion") else ""
        return Sym(text)


LIVE = {"inputs.shadow": False, "inputs.model": "",
        "inputs.replay_pr": "", "inputs.replay_sha": ""}


def _shadow(model: str = "claude-opus-5") -> dict:
    return {"inputs.shadow": True, "inputs.model": model,
            "inputs.replay_pr": REPLAY_PR, "inputs.replay_sha": REPLAY_SHA}


def _render(text: str, known: dict, blank_unknown: bool = False) -> str:
    """A `with:`/`env:`/`run:` value as Actions would substitute it. A value
    the reading cannot know renders as `<name>`, or as nothing where the
    caller says the context it reads is absent (an `env:` read of an event
    field a dispatched run does not carry)."""
    def one(m):
        v = _Eval(known)(m.group(1))
        if isinstance(v, Sym):
            return "" if blank_unknown else f"<{v.name}>"
        if v is None:
            return ""
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, float):
            return str(int(v)) if v == int(v) else str(v)
        return v
    return re.sub(r"\$\{\{(.*?)\}\}", one, str(text), flags=re.S)


def _gate(step: dict, known: dict, **kw):
    return _Eval(known, **kw)(step.get("if"))


AGENT_USES = "anthropics/claude-code-action@"


def _agent_steps() -> list[dict]:
    return [s for s in _steps() if str(s.get("uses", "")).startswith(AGENT_USES)]


# ── running a step's `run:` block ───────────────────────────────────────────


FAKE_GH = r'''#!/usr/bin/env python3
"""A `gh` that answers from fixtures and logs every call it is asked."""
import json, os, sys
args = sys.argv[1:]
fx = json.load(open(os.environ["FAKE_GH_FIXTURES"]))
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
joined = " ".join(args)
def out(text):
    sys.stdout.write(text)
    sys.exit(0)
if args[:2] == ["pr", "view"]:
    if "--json" in args:
        fields = args[args.index("--json") + 1].split(",")
        record = {f: fx["pr"].get(f) for f in fields}
        if "--jq" in args:
            q = args[args.index("--jq") + 1]
            value = record.get(q.lstrip("."))
            out(value if isinstance(value, str) else json.dumps(value))
        out(json.dumps(record))
if args[:2] == ["pr", "diff"]:
    out(fx.get("pr_diff_names" if "--name-only" in args else "pr_diff", ""))
if args and args[0] == "api":
    url = next((a for a in args[1:] if a.startswith("repos/")), "")
    if "/compare/" in url:
        if any("diff" in a for a in args):
            out(fx["compare_diff"])
        out(json.dumps(fx["compare"]))
    if "/issues/" in url and url.split("?")[0].endswith("/comments"):
        out(json.dumps([fx["comments"]]))
    if "/pulls/" in url and "/commits" in url:
        out("[]")
    out("{}")
sys.exit(0)
'''


# What a dispatched run's context holds: no pull_request payload, so every
# event field a block reads is empty unless a test says otherwise.
BENCH_CONTEXT = {
    "github.repository": REPO,
    "github.server_url": "https://github.com",
    "github.run_id": "4242",
    "github.event_name": "workflow_dispatch",
    "github.event.pull_request.number": "",
    "github.event.inputs.pr_number": "",
}


class _Bench:
    """A temp dir, a fake `gh` on PATH, a GITHUB_OUTPUT and a RUNNER_TEMP —
    and every `/tmp/` path in a block rewritten into the temp dir, so two
    tests never share a verdict file."""

    def __init__(self, td: Path, fixtures: dict | None = None):
        self.td = td
        self.bin = td / "bin"
        self.bin.mkdir()
        (self.bin / "gh").write_text(FAKE_GH)
        (self.bin / "gh").chmod(0o755)
        self.fixtures = td / "fixtures.json"
        self.fixtures.write_text(json.dumps(fixtures or {}))
        self.log = td / "gh.log"
        self.out = td / "github-output"
        self.summary = td / "step-summary"
        self.runner_temp = td / "runner-temp"
        self.runner_temp.mkdir()
        self.tmp = td / "tmp"
        self.tmp.mkdir()

    def run(self, step: dict, env: dict, known: dict) -> subprocess.CompletedProcess:
        known = dict(BENCH_CONTEXT, **known)
        script = _render(step["run"], known).replace("/tmp/", f"{self.tmp}/")
        leftover = re.findall(r"<(?:[\w-]+(?:\.[\w-]+)+|\?|\w+\(\))>", script)
        assert not leftover, f"unresolved expressions in the block: {leftover}"
        self.out.write_text("")
        full_env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.td),
            "PIPELINE_DIR": str(ROOT),
            "GITHUB_OUTPUT": str(self.out),
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "4242",
            "RUNNER_TEMP": str(self.runner_temp),
            "FAKE_GH_FIXTURES": str(self.fixtures),
            "FAKE_GH_LOG": str(self.log),
        }
        job_env = _doc()["jobs"]["review"].get("env") or {}
        for k, v in list(job_env.items()) + list((step.get("env") or {}).items()):
            full_env[k] = _render(v, known, blank_unknown=True).replace(
                "/tmp/", f"{self.tmp}/")
        full_env.update(env)
        return subprocess.run(["bash", "-e", "-c", script], cwd=self.td,
                              env=full_env, capture_output=True, text=True,
                              timeout=120)

    def outputs(self) -> dict:
        """GITHUB_OUTPUT read back, heredoc values included."""
        out, lines, i = {}, self.out.read_text().splitlines(), 0
        while i < len(lines):
            line = lines[i]
            m = re.match(r"^([A-Za-z_][\w-]*)<<(\S+)$", line)
            if m:
                end = lines.index(m.group(2), i + 1)
                out[m.group(1)] = "\n".join(lines[i + 1:end])
                i = end + 1
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
            i += 1
        return out

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text().splitlines()]


def _pr_fixtures(comments=None) -> dict:
    return {
        "pr": {
            "headRefName": "agent/DRE-6971-trial-pin-readers",
            "headRefOid": TODAY_HEAD,
            "baseRefName": "main",
            "baseRefOid": BASE_OID,
            "changedFiles": 8, "additions": 100, "deletions": 10,
            "isDraft": False,
            "body": "PR BODY: implements DRE-6971 as the card asked.",
            "files": [],
        },
        "compare": {"files": [{"filename": "scripts/x.py", "status": "modified",
                               "additions": 3, "deletions": 1, "changes": 4,
                               "sha": "a" * 40}]},
        "compare_diff": "diff --git a/scripts/x.py b/scripts/x.py\n+REPLAYED HUNK\n",
        "pr_diff": "diff --git a/today b/today\n+TODAY'S HEAD\n",
        "comments": comments or [],
    }


# ── 1. the contract ─────────────────────────────────────────────────────────


class InputsContractTest(unittest.TestCase):
    def test_the_four_inputs_are_declared_exactly_as_the_contract_states(self):
        inputs = _on(_doc())["workflow_call"]["inputs"]
        expected = {
            "model": ("string", ""),
            "shadow": ("boolean", False),
            "replay_pr": ("string", ""),
            "replay_sha": ("string", ""),
        }
        for name, (kind, default) in expected.items():
            with self.subTest(input=name):
                self.assertIn(name, inputs)
                self.assertEqual(inputs[name].get("type"), kind)
                self.assertIs(inputs[name].get("required"), False)
                self.assertIn("default", inputs[name])
                self.assertEqual(inputs[name]["default"], default)
                self.assertIs(type(inputs[name]["default"]), type(default))

    def test_qa_review_stays_workflow_call_only(self):
        self.assertEqual(set(_on(_doc())), {"workflow_call"})


# ── 2. no new input set: the review main ran before this card ──────────────


def _live_running_steps() -> list[dict]:
    """The steps a run with no new input set can reach: every step whose gate
    is not KNOWN false once the four inputs read their defaults."""
    return [s for s in _steps() if _truth(_gate(s, LIVE)) is not False]


def _strip_shadow(cond):
    if cond is None:
        return None
    return re.sub(r"\s*&&\s*!inputs\.shadow\b", "", str(cond))


class LiveReviewUnchangedTest(unittest.TestCase):
    def test_the_steps_that_run_and_their_conditions_match_main(self):
        running = [(_key(s), s.get("if")) for s in _live_running_steps()]
        self.assertEqual([k for k, _ in running], [k for k, _ in PRE_CHANGE_STEPS],
                         "the steps a live review runs are not main's")
        for (key, cond), (_, before) in zip(running, PRE_CHANGE_STEPS):
            with self.subTest(step=key):
                # A shadow guard is the only thing allowed onto a live gate,
                # and with `shadow` false it is `&& true`.
                self.assertEqual(_strip_shadow(cond), before)

    def test_every_added_step_is_unreachable_with_no_new_input(self):
        before = {k for k, _ in PRE_CHANGE_STEPS}
        added = [s for s in _steps() if _key(s) not in before]
        self.assertTrue(added, "the shadow steps are missing")
        for s in added:
            with self.subTest(step=_key(s)):
                self.assertIs(_truth(_gate(s, LIVE)), False)

    def test_the_select_model_command_is_unchanged(self):
        step = _step(id="model")
        self.assertEqual(step["run"], PRE_CHANGE_SELECT_RUN)
        self.assertEqual(step.get("if"), "steps.decide.outputs.review == 'true'")

    def test_the_live_critic_runs_on_the_selected_model(self):
        for step in _agent_steps():
            with self.subTest(step=step["id"]):
                args = step["with"]["claude_args"]
                m = re.search(r"--model (\$\{\{.*?\}\})", args)
                self.assertIsNotNone(m, args)
                live = _Eval(LIVE)(m.group(1))
                self.assertIsInstance(live, Sym)
                self.assertEqual(live.name, "steps.model.outputs.model")
                effort = re.search(r"\n(\$\{\{[^\n]*effort_arg[^\n]*\}\})", args)
                self.assertIsNotNone(effort, args)
                self.assertEqual(_Eval(LIVE)(effort.group(1)).name,
                                 "steps.model.outputs.effort_arg")

    def test_the_shadow_critic_runs_on_the_named_model_never_a_literal(self):
        for step in _agent_steps():
            with self.subTest(step=step["id"]):
                args = step["with"]["claude_args"]
                self.assertNotRegex(args, r"--model\s+claude-")
                m = re.search(r"--model (\$\{\{.*?\}\})", args)
                # The id the refusal step checked, handed on by that step —
                # which writes the caller's `model` input back unchanged
                # (test_a_good_request_passes_and_names_the_models_effort),
                # and never the model Select model chose.
                checked = dict(_shadow(), **{
                    "steps.shadow_model.outputs.model": "the-checked-model",
                    "steps.model.outputs.model": "the-selected-model"})
                self.assertEqual(_Eval(checked)(m.group(1)), "the-checked-model")
                effort = re.search(r"\n(\$\{\{[^\n]*effort_arg[^\n]*\}\})", args)
                self.assertEqual(_Eval(_shadow())(effort.group(1)).name,
                                 "steps.shadow_model.outputs.effort_arg")


# ── 3. the build/review separation ──────────────────────────────────────────


class SeparationKeptTest(unittest.TestCase):
    def test_select_model_still_passes_the_build_model(self):
        run = _step(id="model")["run"]
        self.assertIn("model_fallback.py build-model", run)
        self.assertIn('SEPARATION_ARG="--built-on $BUILT_ON"', run)
        self.assertIn('SEPARATION_ARG="--built-on-unknown"', run)
        self.assertIn("model_fallback.py select critic", run)

    def test_select_model_runs_in_shadow_mode_too(self):
        step = _step(id="model")
        self.assertIsNot(_truth(_gate(step, _shadow())), False)


# ── 4. a bad request is refused before any agent step ──────────────────────


def _refusers() -> list[dict]:
    return [_step(id="shadow_shape"), _step(id="shadow_model")]


def _walk_after_failure(failed: dict, known: dict) -> list[str]:
    """The steps that still run once `failed` fails: Actions skips every
    later step whose `if:` holds no status function, and evaluates the rest
    with every skipped step's outputs empty."""
    steps = _steps()
    at = next(i for i, s in enumerate(steps) if s == failed)
    skipped = {s.get("id") for s in steps[at + 1:] if s.get("id")}
    ran = []
    for s in steps[at + 1:]:
        cond = str(s.get("if") or "")
        if not re.search(r"\b(always|failure)\(\)", cond):
            continue
        if _truth(_Eval(known, skipped, failed=True)(cond)) is not False:
            ran.append(s)
            skipped.discard(s.get("id"))
    return ran


class RefusalTest(unittest.TestCase):
    def _run(self, step, known):
        with tempfile.TemporaryDirectory() as td:
            return _Bench(Path(td)).run(step, {}, known)

    def _refused(self, known: dict) -> dict:
        """Runs the refusing steps in order and returns the one that failed."""
        for step in _refusers():
            if _truth(_gate(step, known)) is False:
                continue
            res = self._run(step, known)
            if res.returncode != 0:
                self.assertIn("::error::", res.stdout + res.stderr)
                return step
        self.fail(f"nothing refused {known}")

    def _assert_refused_before_any_agent(self, known: dict):
        step = self._refused(known)
        first_agent = min(_index(s) for s in _agent_steps())
        self.assertLess(_index(step), first_agent)
        self.assertLess(_index(step), _index(_step(id="install_claude")))
        still = [_key(s) for s in _walk_after_failure(step, known)]
        for agent in _agent_steps():
            self.assertNotIn(_key(agent), still,
                             "an agent step still runs after the refusal")
        if known["inputs.shadow"]:
            for writer in _writers():
                self.assertNotIn(_key(writer), still, "a shadow refusal still writes")

    def test_a_live_review_on_a_named_model_is_refused(self):
        self._assert_refused_before_any_agent(dict(LIVE, **{"inputs.model": "claude-opus-5"}))

    def test_an_empty_replay_sha_is_refused(self):
        self._assert_refused_before_any_agent(dict(_shadow(), **{"inputs.replay_sha": ""}))

    def test_a_non_hex_replay_sha_is_refused(self):
        for sha in ("z" * 40, REPLAY_SHA.upper(), REPLAY_SHA[:12], REPLAY_SHA + "0"):
            with self.subTest(sha=sha):
                self._assert_refused_before_any_agent(dict(_shadow(), **{"inputs.replay_sha": sha}))

    def test_a_missing_model_or_pull_request_is_refused(self):
        self._assert_refused_before_any_agent(dict(_shadow(), **{"inputs.model": ""}))
        self._assert_refused_before_any_agent(dict(_shadow(), **{"inputs.replay_pr": ""}))

    def test_an_unpriced_model_is_refused(self):
        prices = yaml.safe_load((ROOT / "config" / "model-prices.yaml").read_text())["prices"]
        unpriced = "claude-nobody-priced-this-1"
        self.assertNotIn(unpriced, prices)
        self._assert_refused_before_any_agent(_shadow(unpriced))

    def test_an_excluded_or_retired_model_is_refused(self):
        models = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())
        for block in ("excluded", "retired"):
            for entry in models.get(block) or []:
                with self.subTest(block=block, model=entry["model"]):
                    self._assert_refused_before_any_agent(_shadow(entry["model"]))

    def test_a_good_request_passes_and_names_the_models_effort(self):
        models = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())
        effort = models.get("effort") or {}
        model, level = next(iter(effort.items()))
        known = _shadow(model)
        with tempfile.TemporaryDirectory() as td:
            bench = _Bench(Path(td))
            for step in _refusers():
                res = bench.run(step, {}, known)
                self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(bench.outputs().get("effort_arg"), f"--effort {level}")
            # The model the critic runs on is the one checked here, as named.
            self.assertEqual(bench.outputs().get("model"), model)

    def test_the_refusers_do_not_run_on_a_live_review(self):
        for step in _refusers():
            self.assertIs(_truth(_gate(step, LIVE)), False)

    def test_the_shape_check_is_the_first_step(self):
        self.assertEqual(_steps()[0], _refusers()[0])


# ── 5. shadow mode posts nothing ────────────────────────────────────────────


_WRITERS = re.compile(r"""
    post_verdict\.sh
  | \bgh\s+pr\s+(?:comment|edit|review)\b
  | publish_review_check
  | sync_review_state
  | linear_ops\.py["']?\s+(?:comment|advance|add-label|remove-label|set-description|state)\b
""", re.X)
_GH_API_WRITE = re.compile(
    r"\bgh\s+api\b[^\n]*(?:-X\s*|--method[\s=]+)(?:POST|PATCH|PUT|DELETE)\b", re.I)


def _writers() -> list[dict]:
    """Every step whose `run:` writes to GitHub or Linear, found by what it
    calls."""
    out = []
    for s in _steps():
        run = str(s.get("run") or "")
        flat = re.sub(r"\\\n\s*", " ", run)
        if _WRITERS.search(flat) or _GH_API_WRITE.search(flat):
            out.append(s)
    return out


class ShadowPostsNothingTest(unittest.TestCase):
    def test_discovery_finds_todays_four_writers(self):
        found = {_key(s) for s in _writers()}
        for name in ("Post verdict or neutral status",
                     "Card → In Review (a pull request opened outside the dispatch)",
                     "Publish review check on the reviewed head",
                     "Re-publish the review check for a carried verdict"):
            self.assertIn(name, found)

    def test_every_writer_is_skipped_in_shadow_mode(self):
        for s in _writers():
            with self.subTest(step=_key(s)):
                self.assertIs(_truth(_gate(s, _shadow())), False,
                              f"{_key(s)} can run in shadow mode: {s.get('if')}")

    def test_the_check_run_mint_is_skipped_in_shadow_mode(self):
        self.assertIs(_truth(_gate(_step(id="checks_app"), _shadow())), False)

    def test_the_measurements_keep_running(self):
        for name in ("Keep the critic's Claude usage reading",
                     "Keep the run's death-cause receipt",
                     "Record the GitHub calls per pool login",
                     "Keep the run's working log"):
            with self.subTest(step=name):
                self.assertIsNot(_truth(_gate(_step(name=name), _shadow())), False)

    def test_the_job_is_not_failed_for_a_missing_verdict(self):
        self.assertIs(_truth(_gate(_step(id="critic_fail"), _shadow())), False)

    def test_the_critic_token_is_minted_read_only(self):
        mint = _step(id="reader")
        w = mint["with"]
        self.assertEqual(_render(w.get("permission-contents", ""), _shadow()), "read")
        self.assertEqual(_render(w.get("permission-pull-requests", ""), _shadow()), "read")
        # A live review asks for nothing, which is the installation's own set.
        self.assertEqual(_render(w.get("permission-contents", ""), LIVE), "")
        self.assertEqual(_render(w.get("permission-pull-requests", ""), LIVE), "")
        for agent in _agent_steps():
            self.assertEqual(agent["with"]["github_token"],
                             "${{ steps.reader.outputs.token }}")


# ── 6. the reviewed head, not today's ───────────────────────────────────────


class ReviewedHeadTest(unittest.TestCase):
    def test_the_workspace_checkout_is_at_replay_sha(self):
        checkout = _steps()[_index(_step(id="shadow_shape")) + 2]
        self.assertTrue(checkout["uses"].startswith("actions/checkout@"))
        self.assertNotIn("repository", checkout.get("with") or {})
        self.assertEqual(_render(checkout["with"]["ref"], _shadow()), REPLAY_SHA)
        self.assertEqual(_render(checkout["with"]["ref"], LIVE), "")

    def test_decide_review_reviews_a_merged_pull_request_in_shadow(self):
        step = _step(id="decide")
        fx = _pr_fixtures()
        fx["pr"]["isDraft"] = True
        with tempfile.TemporaryDirectory() as td:
            bench = _Bench(Path(td), fx)
            res = bench.run(step, {}, _shadow())
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            out = bench.outputs()
        self.assertEqual(out.get("review"), "true")
        self.assertEqual(out.get("head_sha"), REPLAY_SHA)
        self.assertFalse(out.get("carried_sha"))

    def test_resolve_pr_binds_replay_pr_and_replay_sha_and_writes_the_compare_diff(self):
        step = _step(id="pr")
        with tempfile.TemporaryDirectory() as td:
            bench = _Bench(Path(td), _pr_fixtures())
            res = bench.run(step, {}, _shadow())
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            out = bench.outputs()
            diff_path = Path(str(out.get("diff_file", "")))
            diff = diff_path.read_text() if diff_path.is_file() else None
            calls = bench.calls()
        self.assertEqual(out.get("number"), REPLAY_PR)
        self.assertEqual(out.get("sha"), REPLAY_SHA)
        self.assertEqual(out.get("card"), "DRE-6971")
        self.assertEqual(diff, _pr_fixtures()["compare_diff"])
        compares = [c for c in calls if c[:1] == ["api"] and any("/compare/" in a for a in c)]
        self.assertTrue(compares)
        for c in compares:
            url = next(a for a in c if "/compare/" in a)
            self.assertTrue(url.endswith(f"/compare/{BASE_OID}...{REPLAY_SHA}"), url)
        self.assertFalse([c for c in calls if c[:2] == ["pr", "diff"]],
                         "the replay read today's diff")
        context = out.get("shadow_context", "")
        self.assertIn(str(diff_path), context)
        self.assertIn(REPLAY_SHA, context)
        self.assertRegex(context, r"(?i)do not read the pull request's comments")

    def test_resolve_pr_on_a_live_review_names_no_diff_file(self):
        step = _step(id="pr")
        known = dict(LIVE, **{"github.event.pull_request.number": float(REPLAY_PR)})
        with tempfile.TemporaryDirectory() as td:
            bench = _Bench(Path(td), _pr_fixtures())
            res = bench.run(step, {}, known)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            out = bench.outputs()
        self.assertEqual(out.get("sha"), TODAY_HEAD)
        self.assertFalse(out.get("shadow_context"))

    def test_both_critic_prompts_carry_the_shadow_context(self):
        for step in _agent_steps():
            with self.subTest(step=step["id"]):
                self.assertIn("${{ steps.pr.outputs.shadow_context }}",
                              step["with"]["prompt"])


# ── 7. no hindsight ─────────────────────────────────────────────────────────


EARLIER_CRITIC = (f"🔎 QA Critic — VERDICT: REQUEST_CHANGES cause:defect @{REPLAY_SHA}\n\n"
                  "## Summary\nHINDSIGHT-CRITIC-SUMMARY\n\n"
                  "## For the fixing agent\nHINDSIGHT-CRITIC-FINDING\n")
EARLIER_VERIFIER = (f"🧪 QA Verifier — VERDICT: FAIL @{REPLAY_SHA}\n\n"
                    "HINDSIGHT-VERIFIER-FINDING\n")
REFUTATION = (f"refuted-finding @{REPLAY_SHA[:8]}\n\nThe critic said "
              "HINDSIGHT-REFUTATION-QUOTE and it was wrong.\n")


def _thread() -> list[dict]:
    return [
        {"id": 1, "user": {"login": QA_LOGIN}, "body": EARLIER_CRITIC,
         "created_at": "2026-10-01T00:00:00Z"},
        {"id": 2, "user": {"login": QA_LOGIN}, "body": EARLIER_VERIFIER,
         "created_at": "2026-10-01T00:01:00Z"},
        {"id": 3, "user": {"login": "agent-bureau-bot[bot]"}, "body": REFUTATION,
         "created_at": "2026-10-01T00:02:00Z"},
    ]


class NoHindsightTest(unittest.TestCase):
    HINDSIGHT = ("HINDSIGHT-CRITIC-SUMMARY", "HINDSIGHT-CRITIC-FINDING",
                 "HINDSIGHT-VERIFIER-FINDING", "HINDSIGHT-REFUTATION-QUOTE",
                 "QA Critic", "QA Verifier")

    def _context(self, known: dict) -> str:
        step = _step(id="cardctx")
        known = dict(known, **{
            "steps.pr.outputs.number": REPLAY_PR,
            "steps.pr.outputs.card": "DRE-6971",
            "steps.pr.outputs.sha": REPLAY_SHA,
            "steps.console.outputs.token": "",
            "steps.reader.outputs.token": "t",
            "steps.app.outputs.app-slug": QA_LOGIN.removesuffix("[bot]"),
        })
        with tempfile.TemporaryDirectory() as td:
            bench = _Bench(Path(td), _pr_fixtures(_thread()))
            (Path(td) / "config").mkdir()
            res = bench.run(step, {}, known)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            return bench.outputs().get("context", "")

    def test_the_live_context_quotes_the_earlier_verdict(self):
        # Proves the fixture reaches the context at all — without it the
        # shadow assertion below would pass over an empty thread.
        context = self._context(LIVE)
        self.assertIn("HINDSIGHT-CRITIC-FINDING", context)
        self.assertIn("HINDSIGHT-REFUTATION-QUOTE", context)

    def test_the_shadow_context_carries_no_earlier_verdict(self):
        context = self._context(_shadow())
        self.assertTrue(context.strip())
        for text in self.HINDSIGHT:
            self.assertNotIn(text, context)
        self.assertIn("DRE-6971", context)
        self.assertIn("first review", context)


# ── 8. the verdict goes to a file ───────────────────────────────────────────


CONTRACT_KEYS = {"role", "repo", "pr", "sha", "model", "verdict", "real", "run_url"}
LINE = re.compile(r"^shadow-verdict: role=critic pr=(\d+) sha=([0-9a-f]{40}) "
                  r"model=(\S+) verdict=(APPROVE|REQUEST_CHANGES|none)$", re.M)


class ShadowVerdictFileTest(unittest.TestCase):
    def _write(self, verdict_md: str | None, gate1: str, gate2: str = ""):
        step = _step(id="shadow_verdict")
        known = dict(_shadow("claude-opus-5"), **{
            "steps.gate1.outputs.real": gate1,
            "steps.gate2.outputs.real": gate2,
            "github.server_url": "https://github.com",
            "github.repository": REPO,
            "github.run_id": "4242",
        })
        with tempfile.TemporaryDirectory() as td:
            bench = _Bench(Path(td))
            if verdict_md is not None:
                (bench.tmp / "qa-verdict.md").write_text(verdict_md)
            res = bench.run(step, {}, known)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            out = bench.outputs()
            folder = Path(out["dir"])
            data = json.loads((folder / "shadow-verdict.json").read_text())
            md = (folder / "shadow-verdict.md").read_text()
            return data, md, res.stdout

    def test_a_real_approve_is_written_with_the_eight_keys_and_one_line(self):
        body = "VERDICT: APPROVE\n\n## Summary\nFine.\n"
        data, md, log = self._write(body, "true")
        self.assertEqual(set(data), CONTRACT_KEYS)
        self.assertEqual(data["role"], "critic")
        self.assertEqual(data["repo"], REPO)
        self.assertEqual(data["pr"], int(REPLAY_PR))
        self.assertIs(type(data["pr"]), int)
        self.assertEqual(data["sha"], REPLAY_SHA)
        self.assertEqual(data["model"], "claude-opus-5")
        self.assertEqual(data["verdict"], "APPROVE")
        self.assertIs(data["real"], True)
        self.assertEqual(data["run_url"], f"https://github.com/{REPO}/actions/runs/4242")
        self.assertEqual(md, body)
        lines = LINE.findall(log)
        self.assertEqual(lines, [(REPLAY_PR, REPLAY_SHA, "claude-opus-5", "APPROVE")])
        self.assertEqual(log.count("shadow-verdict:"), 1)

    def test_a_retry_request_changes_is_read_from_the_second_gate(self):
        body = "VERDICT: REQUEST_CHANGES cause:defect\n\n## Summary\nNo.\n"
        data, _md, log = self._write(body, "false", "true")
        self.assertEqual(data["verdict"], "REQUEST_CHANGES")
        self.assertIs(data["real"], True)
        self.assertEqual(LINE.findall(log)[0][3], "REQUEST_CHANGES")

    def test_no_real_verdict_writes_null(self):
        stub = ("VERDICT: REQUEST_CHANGES\n<!-- QA-REVIEW-INCOMPLETE -->\n"
                "## Summary\nThis review has not finished.\n")
        data, md, log = self._write(stub, "false", "false")
        self.assertIsNone(data["verdict"])
        self.assertIs(data["real"], False)
        self.assertEqual(md, stub)
        self.assertEqual(LINE.findall(log)[0][3], "none")

    def test_no_verdict_file_at_all_writes_null_and_an_empty_md(self):
        data, md, log = self._write(None, "", "")
        self.assertEqual(set(data), CONTRACT_KEYS)
        self.assertIsNone(data["verdict"])
        self.assertIs(data["real"], False)
        self.assertEqual(md, "")
        self.assertEqual(len(LINE.findall(log)), 1)

    def test_the_step_runs_only_in_shadow_after_the_second_gate(self):
        step = _step(id="shadow_verdict")
        self.assertTrue(str(step["if"]).startswith("always() && !cancelled() && inputs.shadow"))
        self.assertGreater(_index(step), _index(_step(id="gate2")))
        self.assertIs(_truth(_gate(step, LIVE)), False)
        self.assertIsNot(_truth(_gate(step, _shadow(), failed=True)), False)

    def test_the_artifact_is_uploaded_under_the_contract_name(self):
        upload = _step(name="Upload the shadow verdict")
        self.assertTrue(upload["uses"].startswith("actions/upload-artifact@"))
        self.assertEqual(_render(upload["with"]["name"], _shadow()),
                         f"shadow-verdict-critic-{REPLAY_PR}")
        self.assertEqual(upload["with"]["path"], "${{ steps.shadow_verdict.outputs.dir }}")
        self.assertGreater(_index(upload), _index(_step(id="shadow_verdict")))
        self.assertIs(_truth(_gate(upload, LIVE)), False)
        self.assertIsNot(_truth(_gate(upload, _shadow(), failed=True)), False)


if __name__ == "__main__":
    unittest.main()
