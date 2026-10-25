"""The medic's verdict step finds the card's open pull request by its head
branch first, and never reads an empty lookup on a pull-request run as "no
open pull request" (DRE-7925).

On 2026-10-24 the medic asked the CEO about two portico cards in Green Light
although each had an open pull request when it decided:

  * DRE-7910, medic run 40148899969 — PR #1044 on
    `agent/DRE-7910-fake-time-poll`, open and not a draft;
  * DRE-7847, medic runs 40131553026 and 40136114230 — PR #1040 on
    `agent/DRE-7847-people-invite-lock`, open and not a draft.

Both were classed `bad-code`, whose step is `fix-path` unless the card has no
open pull request. `Read the diagnosis verdict` asked `card_pr.py find` with
the card alone, so the lookup was the search index's `head:agent/DRE-N`, and
the search came back empty. `find` exited 0 printing nothing, and the step
read that as "no open pull request" and fell back to `ask-person`.

The fix, in that one step: it passes the run's head branch, so `find` asks
for the exact `--head` first; it prints what `find` printed; and on a
`pull_request` run on an `agent/*` head, an empty answer warns and is not
taken as "no open pull request" — a pull-request run always has a pull
request.

The step's script runs here as the job runs it, with `card_pr.py` and
`medic_next_step.py` the real scripts and `gh` a stub that answers the exact
head lookup and the search separately, as GitHub did that day.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MEDIC = os.path.join(ROOT, ".github", "workflows", "medic.yml")
VERDICT = ".bureau-pipeline/medic-verdict.txt"
REPORT = ".bureau-pipeline/medic-report.md"
PORTICO = "dreadnought-foundry/portico"


def _step() -> dict:
    with open(MEDIC, encoding="utf-8") as f:
        steps = yaml.safe_load(f)["jobs"]["diagnose"]["steps"]
    return next(s for s in steps if s.get("id") == "verdict")


def _pr(number: int, head: str, state: str = "OPEN") -> dict:
    return {"number": number, "url": f"https://github.com/{PORTICO}/pull/{number}",
            "headRefName": head, "state": state}


STUB_LOG = """
import json, os, sys
with open(os.environ["CALLS"], "a", encoding="utf-8") as f:
    f.write(json.dumps([NAME, *sys.argv[1:]]) + "\\n")
"""

# `gh pr list`: the exact `--head` lookup and the `--search` lookup answer
# from separate fakes, because that day they gave different answers.
STUB_GH = "#!/usr/bin/env python3" + STUB_LOG.replace("NAME", '"gh"') + """
argv = sys.argv[1:]
if argv[:2] != ["pr", "list"]:
    sys.exit(2)
if "--head" in argv:
    print(os.environ.get("FAKE_HEAD_JSON", "[]"))
elif "--search" in argv:
    print(os.environ.get("FAKE_SEARCH_JSON", "[]"))
else:
    sys.exit(2)
sys.exit(int(os.environ.get("FAKE_GH_RC", "0")))
"""

# The real script, run from its own place so it finds its own config.
STUB_REAL = "#!/usr/bin/env python3" + STUB_LOG.replace("NAME", "os.path.basename(__file__)") + """
import subprocess
real = os.path.join(os.environ["REAL_SCRIPTS"], os.path.basename(__file__))
sys.exit(subprocess.call([sys.executable, real, *sys.argv[1:]]))
"""

STUB_LINEAR_OPS = "#!/usr/bin/env python3" + STUB_LOG.replace("NAME", '"linear_ops.py"') + """
sys.stdout.write("[]")
"""


class Run:
    def __init__(self, proc, outputs, calls):
        self.proc, self.outputs, self.calls = proc, outputs, calls
        self.log = proc.stdout

    @property
    def decide(self) -> list:
        return next(c for c in self.calls
                    if c[0] == "medic_next_step.py" and c[1:2] == ["decide"])

    @property
    def gh(self) -> list:
        return [c for c in self.calls if c[0] == "gh"]


def _run_verdict(*, target, head_branch, event="pull_request", verdict="bad-code",
                 head_prs=(), search_prs=(), gh_rc=0, repo=PORTICO,
                 attempt="1") -> Run:
    step = _step()
    values = {
        "steps.claude.outcome": "success",
        "steps.target.outputs.kind": "card",
        "steps.target.outputs.target": target,
        "github.event.workflow_run.run_attempt": attempt,
        "github.event.workflow_run.head_branch": head_branch,
        "github.event.workflow_run.event": event,
        "needs.classify.outputs.retry": "true",
        "github.token": "test-token",
        "github.repository": repo,
        "secrets.LINEAR_API_KEY": "test-linear-key",
    }

    def render(text):
        def sub(m):
            key = m.group(1).strip()
            if key not in values:
                raise AssertionError(f"the step reads {key!r}, which this test does not fake")
            return values[key]
        return re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", sub, text)

    env = {k: render(str(v)) for k, v in (step.get("env") or {}).items()}
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        scripts, bin_ = td / ".bureau-pipeline" / "scripts", td / "bin"
        scripts.mkdir(parents=True)
        bin_.mkdir()
        for name, body in (("card_pr.py", STUB_REAL), ("medic_next_step.py", STUB_REAL),
                           ("linear_ops.py", STUB_LINEAR_OPS)):
            (scripts / name).write_text(body, encoding="utf-8")
            os.chmod(scripts / name, 0o755)
        (bin_ / "gh").write_text(STUB_GH, encoding="utf-8")
        os.chmod(bin_ / "gh", 0o755)
        (td / VERDICT).write_text(verdict, encoding="utf-8")
        (td / REPORT).write_text("The tests fail on the branch.\n", encoding="utf-8")
        calls, output = td / "calls.log", td / "output"
        for p in (calls, output):
            p.touch()
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
            cwd=td, capture_output=True, text=True, check=False,
            env={**os.environ, **env,
                 "FAKE_HEAD_JSON": json.dumps(list(head_prs)),
                 "FAKE_SEARCH_JSON": json.dumps(list(search_prs)),
                 "FAKE_GH_RC": str(gh_rc),
                 "PATH": f"{bin_}:{os.path.dirname(sys.executable)}:{os.environ['PATH']}",
                 "PYTHONIOENCODING": "utf-8",
                 "REAL_SCRIPTS": os.path.join(ROOT, "scripts"),
                 "CALLS": str(calls), "GITHUB_OUTPUT": str(output),
                 "RUNNER_TEMP": str(td)})
        if proc.returncode != 0:
            raise AssertionError(f"the verdict step exited {proc.returncode}:\n"
                                 f"{proc.stdout}\n{proc.stderr}")
        outputs = dict(ln.split("=", 1)
                       for ln in output.read_text(encoding="utf-8").splitlines() if "=" in ln)
        parsed = [json.loads(ln) for ln in calls.read_text(encoding="utf-8").splitlines() if ln]
        return Run(proc, outputs, parsed)


# ── 1. the two real cases, replayed ─────────────────────────────────────────
REPLAYS = (
    ("DRE-7910", "agent/DRE-7910-fake-time-poll", 1044),
    ("DRE-7847", "agent/DRE-7847-people-invite-lock", 1040),
)


class TheReplayTest(unittest.TestCase):
    """The search answers nothing; the exact head lookup answers the PR."""

    def _replay(self, card, head, number):
        return _run_verdict(target=card, head_branch=head,
                            head_prs=[_pr(number, head)], search_prs=[])

    def test_each_open_pull_request_decides_fix_path(self):
        for card, head, number in REPLAYS:
            with self.subTest(card=card):
                run = self._replay(card, head, number)
                self.assertEqual("bad-code", run.outputs["class"])
                self.assertEqual("fix-path", run.outputs["step"], run.log)
                self.assertIn("--has-open-pr", run.decide)

    def test_the_exact_head_lookup_is_what_finds_it(self):
        for card, head, number in REPLAYS:
            with self.subTest(card=card):
                run = self._replay(card, head, number)
                heads = [c for c in run.gh if "--head" in c]
                self.assertEqual(1, len(heads), run.gh)
                self.assertEqual(head, heads[0][heads[0].index("--head") + 1])
                self.assertIn(PORTICO, heads[0])
                # found on the exact lookup, so nothing is warned
                self.assertNotIn("::warning", run.log)

    def test_finds_raw_output_is_in_the_log(self):
        for card, head, number in REPLAYS:
            with self.subTest(card=card):
                run = self._replay(card, head, number)
                self.assertIn(f"OPEN\thttps://github.com/{PORTICO}/pull/{number}", run.log)


# ── 2. an empty lookup on a pull-request run is not "no open PR" ────────────
class TheEmptyLookupOnAPullRequestRunTest(unittest.TestCase):
    HEAD = "agent/DRE-7910-fake-time-poll"

    def _empty(self, **kw):
        kw.setdefault("target", "DRE-7910")
        kw.setdefault("head_branch", self.HEAD)
        return _run_verdict(head_prs=[], search_prs=[], **kw)

    def test_it_is_never_decided_as_no_open_pr(self):
        run = self._empty()
        self.assertIn("--has-open-pr", run.decide)
        self.assertEqual("fix-path", run.outputs["step"])
        self.assertNotIn("no open pull request for the fix loop", run.log)

    def test_it_does_not_ask_a_person_on_that_ground(self):
        run = self._empty()
        self.assertNotEqual("ask-person", run.outputs["step"])

    def test_it_warns_naming_the_card_and_the_branch(self):
        run = self._empty()
        warnings = [ln for ln in run.log.splitlines() if ln.startswith("::warning")]
        self.assertEqual(1, len(warnings), run.log)
        self.assertIn("DRE-7910", warnings[0])
        self.assertIn(self.HEAD, warnings[0])

    def test_the_empty_answer_is_in_the_log(self):
        run = self._empty()
        line = next((ln for ln in run.log.splitlines() if "card_pr.py find" in ln
                     and not ln.startswith("::")), None)
        self.assertIsNotNone(line, run.log)
        self.assertIn("exited 0", line)

    def test_a_class_without_the_pr_condition_keeps_its_step(self):
        run = self._empty(verdict="platform-fault")
        self.assertEqual("ask-person", run.outputs["step"])

    # ── the controls: where an empty answer still means "no open PR" ───────
    def test_a_run_not_started_by_a_pull_request_still_falls_back(self):
        for event in ("push", "workflow_dispatch", "repository_dispatch"):
            with self.subTest(event=event):
                run = self._empty(event=event)
                self.assertNotIn("--has-open-pr", run.decide)
                self.assertEqual("ask-person", run.outputs["step"])
                self.assertNotIn("::warning", run.log)

    def test_a_pull_request_run_off_an_agent_head_still_falls_back(self):
        for head in ("dependabot/pip/requests-2.33", "feature/agent/DRE-7910", ""):
            with self.subTest(head=head):
                run = self._empty(head_branch=head)
                self.assertNotIn("--has-open-pr", run.decide)
                self.assertEqual("ask-person", run.outputs["step"])

    def test_a_merged_pull_request_is_a_readable_answer(self):
        run = _run_verdict(target="DRE-7910", head_branch=self.HEAD,
                           head_prs=[_pr(1044, self.HEAD, "MERGED")])
        self.assertNotIn("--has-open-pr", run.decide)
        self.assertEqual("ask-person", run.outputs["step"])
        self.assertIn(f"MERGED\thttps://github.com/{PORTICO}/pull/1044", run.log)

    def test_a_lookup_that_fails_still_warns_with_its_exit_code(self):
        run = self._empty(gh_rc=1)
        self.assertRegex(run.log, r"::warning title=Pull request not read::"
                                  r"card_pr\.py find exited 3 ")


# ── 3. the step's wiring ────────────────────────────────────────────────────
class TheStepWiringTest(unittest.TestCase):
    def setUp(self):
        self.step = _step()
        self.env = self.step.get("env") or {}

    def test_it_reads_the_runs_head_branch_and_event(self):
        self.assertEqual("${{ github.event.workflow_run.head_branch }}",
                         self.env.get("HEAD_BRANCH"))
        self.assertEqual("${{ github.event.workflow_run.event }}",
                         self.env.get("TRIGGER_EVENT"))

    def test_find_is_given_the_head_branch(self):
        flat = " ".join(self.step["run"].split())
        self.assertIn('card_pr.py" find "$TARGET" --repo "$REPO" --branch "$HEAD_BRANCH"', flat)

    def test_no_run_text_on_a_shell_line(self):
        self.assertNotIn("${{", self.step["run"])


if __name__ == "__main__":
    unittest.main()
