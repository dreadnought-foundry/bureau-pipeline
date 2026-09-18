"""RED-first tests: the fix loop survives a throttled comment read (DRE-4157).

THE INCIDENT, replicated here with the answer GitHub actually gave. On
2026-09-17 → 18 the shared App installation (123249480) was throttled six
times while agent-fix.yml's "Resolve PR, mode, and attempt budget" step was
reading a pull request's comment thread (portico PRs #582, #597, #598, #600,
#603, #605 — run 35252920674 first). `gh api` printed GitHub's error body on
STDOUT and exited 1; the step piped that straight into `jq`, which read the
error object as the comment record and died on it —
`jq: error (at <stdin>:5): Cannot index string with string "user"`, exit 5 —
and the run was red for a reason that had nothing to do with the branch.

The card's rule, from the CEO's signed answer: notice the refusal, wait and
retry the way the sweep does under DRE-4109, and if it still cannot read, stop
with a plain-English reason instead of crashing. The repo's house rule sits
under it: an unreadable answer is UNKNOWN — a check that could not read must
never report a clean result.

WHAT THESE TESTS RUN. The shipped bash, for real (the
tests/test_hand_dispatch_no_work.py harness), against a `gh` stub that answers
the comments endpoint the way GitHub did that day for the first N calls and
then answers with the fixture. `BUREAU_GH_READ_BACKOFF=0,0` is the module's
documented test hook, so the real retry runs without the real minute.

  - a blip (refused once) → the step succeeds and decides normally, on the
    SECOND answer; nothing on the thread was guessed;
  - a refusal that persists → the step STOPS: non-zero, the reason on stderr
    names the rate limit, there is no jq error, and `go=true` is never
    written (every later step in the job is gated on it);
  - a clean thread → the record is read ONCE. It was read four times — the
    halt count, the halt receipt, the routing reads and the budget each
    fetched the same record — and DRE-4139's "ONE read of the WHOLE record"
    comment sat above the last two. Four reads per run on a bucket that was
    already empty is part of how the day went;
  - the sibling sites where a refused answer was parsed as data — the
    unfixable-check receipt, the inherited-failure receipt, and the critic
    verdict fetch (`--jq … | tail -1`, whose last line on a refusal was a
    piece of the error body handed to `base64 -d`) — go through the same seam.

Run: cd bureau-pipeline && python3 -m pytest tests/test_agent_fix_rate_limited_read.py -v
"""
from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404 — fixed argv, this repo's own scripts
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "agent-fix.yml"
sys.path.insert(0, str(ROOT / "scripts"))

REPO = "dreadnought-foundry/portico"
PR = "582"
SHA = "d9f2c1ab" + "0" * 32
WORKER = "agent-bureau-bot[bot]"
QA = "agent-bureau-qa-bot[bot]"
COMMENTS_ENDPOINT = f"repos/{REPO}/issues/{PR}/comments?per_page=100"

#: GitHub's answer on run 35252920674, both channels, verbatim from the card.
ERROR_LINE = (
    "gh: HTTP 403: API rate limit exceeded for installation ID 123249480 "
    f"(https://api.github.com/{COMMENTS_ENDPOINT})"
)
ERROR_BODY = json.dumps({
    "message": "API rate limit exceeded for installation ID 123249480.",
    "documentation_url": "https://docs.github.com/rest/overview/rate-limits-for-the-rest-api",
    "status": "403",
}, indent=2)
#: The jq death the card quotes — the anti-vacuity needle: a fixed step never
#: prints it, and the unfixed step prints exactly it.
JQ_DEATH = 'Cannot index string with string "user"'

VERDICT = (
    f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{SHA}\n\n"
    "1. the test asserts nothing\n"
)


def wf_src() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def steps() -> list:
    return yaml.safe_load(wf_src())["jobs"]["fix"]["steps"]


def step_named(name: str) -> dict:
    for step in steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"step {name!r} not found in agent-fix.yml")


def substitute(run: str, values: dict) -> str:
    def repl(m):
        key = m.group(1).strip()
        if key not in values:
            raise AssertionError(f"harness has no value for ${{{{ {key} }}}}")
        return values[key]

    out = re.sub(r"\$\{\{([^}]*)\}\}", repl, run)
    assert "${{" not in out
    return out


GH_STUB = '''#!/usr/bin/env python3
"""Stand-in for `gh`. Serves the PR view from a fixture; answers the comments
endpoint the way GitHub did on 2026-09-17 for the first GH_REFUSE_FIRST calls
(error body on stdout, gh's line on stderr, exit 1) and from the fixture after
that; records every call and every write."""
import json, os, subprocess, sys

args = sys.argv[1:]
open(os.environ["GH_CALLS"], "a").write(json.dumps(args) + "\\n")
if args[:2] == ["pr", "view"]:
    print(open(os.environ["GH_PR_INFO"]).read())
elif args[:2] == ["pr", "comment"]:
    open(os.environ["GH_LOG"], "a").write(json.dumps(args) + "\\n")
elif args[0] == "api":
    n = sum(1 for line in open(os.environ["GH_CALLS"]) if '"api"' in line)
    if n <= int(os.environ.get("GH_REFUSE_FIRST", "0")):
        # `--slurp` wraps even the refused page in its array — which is why
        # `add[]` then iterates the error's STRING values and jq dies with
        # `Cannot index string with string "user"` rather than anything
        # naming the rate limit.
        body = json.loads(os.environ["GH_ERROR_BODY"])
        sys.stdout.write(json.dumps([body] if "--slurp" in args else body, indent=2) + "\\n")
        sys.stderr.write(os.environ["GH_ERROR_LINE"] + "\\n")
        sys.exit(1)
    comments = json.load(open(os.environ["GH_COMMENTS"]))
    payload = [comments] if "--slurp" in args else comments
    if "--jq" in args:
        out = subprocess.run(["jq", "-r", args[args.index("--jq") + 1]],
                             input=json.dumps(payload), capture_output=True, text=True)
        sys.stderr.write(out.stderr)
        sys.stdout.write(out.stdout)
        sys.exit(out.returncode)
    print(json.dumps(payload))
else:
    sys.stderr.write("unexpected gh call: %r\\n" % (args,))
    sys.exit(2)
'''


class Harness:
    """One temp dir, one stubbed `gh`, the real pipeline scripts."""

    def __init__(self, td: str, thread: list, refuse_first: int):
        self.td = td
        os.makedirs(os.path.join(td, "bin"))
        stub = os.path.join(td, "bin", "gh")
        with open(stub, "w") as f:
            f.write(GH_STUB)
        os.chmod(stub, 0o755)
        # A real directory, not a symlink to the repo: the verdict step writes
        # into `.bureau-pipeline/`, and that must land here, not in the tree.
        os.makedirs(os.path.join(td, ".bureau-pipeline"))
        os.symlink(ROOT / "scripts", os.path.join(td, ".bureau-pipeline", "scripts"))
        self.info = os.path.join(td, "pr-info.json")
        with open(self.info, "w") as f:
            json.dump({"state": "OPEN", "headRefName": "agent/DRE-4157-x",
                       "headRefOid": SHA, "mergeStateStatus": "CLEAN"}, f)
        self.comments = os.path.join(td, "comments.json")
        with open(self.comments, "w") as f:
            json.dump(thread, f)
        self.calls = os.path.join(td, "gh-calls.jsonl")
        self.out_file = os.path.join(td, "step-output")
        open(self.out_file, "w").close()
        self.refuse_first = refuse_first

    def run_step(self, name: str, values: dict) -> subprocess.CompletedProcess:
        body = substitute(step_named(name)["run"], values)
        script = os.path.join(self.td, "step.sh")
        with open(script, "w") as f:
            # `bash -e`, as Actions runs a `run:` block with no `shell:` —
            # NOT pipefail, which is exactly why a dead `gh` inside a pipe
            # was never noticed.
            f.write("set -e\n" + body)
        return subprocess.run(  # nosec B603 — fixed argv, our own scripts
            ["bash", script],
            cwd=self.td,
            env=dict(
                os.environ,
                PATH=f"{self.td}/bin:{os.environ['PATH']}",
                GITHUB_OUTPUT=self.out_file,
                RUNNER_TEMP=self.td,
                GH_PR_INFO=self.info,
                GH_COMMENTS=self.comments,
                GH_CALLS=self.calls,
                GH_LOG=os.path.join(self.td, "gh-writes.jsonl"),
                GH_REFUSE_FIRST=str(self.refuse_first),
                GH_ERROR_BODY=ERROR_BODY,
                GH_ERROR_LINE=ERROR_LINE,
                GH_TOKEN="test",
                WORKER_LOGIN=WORKER,
                EVENT_NAME="workflow_dispatch",
                TRIGGERING_ACTOR="github-actions",
                BUREAU_GH_READ_BACKOFF="0,0",
            ),
            capture_output=True,
            text=True,
        )

    def outputs(self) -> dict:
        out = {}
        for line in open(self.out_file).read().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                out[key] = value
        return out

    def comment_reads(self) -> int:
        return sum(
            1 for line in open(self.calls).read().splitlines()
            if "issues/" in line and "/comments" in line
        )


def resolve(thread: list, refuse_first: int):
    with tempfile.TemporaryDirectory() as td:
        h = Harness(td, thread, refuse_first)
        proc = h.run_step("Resolve PR, mode, and attempt budget", {
            "github.event.issue.number || github.event.inputs.pr_number": PR,
            "github.repository": REPO,
        })
        return proc, h.outputs(), h.comment_reads()


def _comment(login: str, body: str) -> dict:
    return {"user": {"login": login}, "body": body}


# --------------------------------------------------------------------------
# The Resolve step: refused once, refused for good, and the control
# --------------------------------------------------------------------------
class ResolveStepSurvivesAThrottledReadTest(unittest.TestCase):

    def test_a_refusal_that_clears_on_the_retry_decides_normally(self):
        """REPLICATION of run 35252920674 with a blip: on the unfixed step
        jq dies on the error body and the run is red. Fixed, the step waits,
        reads again, and decides on the real thread."""
        proc, outputs, reads = resolve([], refuse_first=1)
        self.assertNotIn(JQ_DEATH, proc.stderr)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(outputs.get("go"), "true", outputs)
        self.assertEqual(reads, 2, "one refusal, one answer — and no third read")
        self.assertIn("attempt 1 of 3", proc.stderr + proc.stdout,
                      "the retry is visible in the run log, DRE-4109's way")

    def test_a_refusal_that_persists_stops_with_a_reason_and_never_says_go(self):
        """The other half of the card. Three attempts, then a plain-English
        stop: an unreadable thread is UNKNOWN, so no halt is counted, no
        budget is judged, and nothing downstream runs."""
        proc, outputs, reads = resolve([], refuse_first=99)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn(JQ_DEATH, proc.stderr, "a stop, not the crash")
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("rate limit", proc.stderr)
        self.assertEqual(reads, 3, "three attempts in all, then it stops")
        self.assertNotIn("go", outputs, "a step that could not read decides nothing")

    def test_a_clean_thread_is_read_once(self):
        """DRE-4139 said ONE read of the whole record; four reads of the same
        endpoint per run were part of how the bucket emptied."""
        thread = [_comment(QA, VERDICT)]
        proc, outputs, reads = resolve(thread, refuse_first=0)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(outputs.get("go"), "true", outputs)
        self.assertEqual(reads, 1, "the comment record is fetched exactly once")

    def test_the_halt_still_counts_on_the_thread_it_read(self):
        """Anti-vacuity for the consolidation: the halt gate still reads the
        record it now shares — two no-progress markers on this sha halt."""
        no_progress = (
            f"🛑 Fix attempt 1 pushed no new commit (branch still at `{SHA[:8]}`) — "
            "the reviewer will not re-run and the last verdict stands."
        )
        thread = [_comment(WORKER, no_progress), _comment(WORKER, no_progress)]
        proc, outputs, reads = resolve(thread, refuse_first=0)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(outputs.get("go"), "false", outputs)
        self.assertEqual(reads, 1)


# --------------------------------------------------------------------------
# The critic verdict fetch: the same defect in a `--jq | tail -1` coat
# --------------------------------------------------------------------------
class VerdictFetchSurvivesAThrottledReadTest(unittest.TestCase):
    STEP = "Fetch critic verdict (qa-bot authored only)"
    VALUES = {
        "github.repository": REPO,
        "steps.pr.outputs.number": PR,
        "steps.pr.outputs.head_sha": SHA,
    }

    def test_a_blip_still_delivers_the_verdict(self):
        with tempfile.TemporaryDirectory() as td:
            h = Harness(td, [_comment(QA, VERDICT)], refuse_first=1)
            proc = h.run_step(self.STEP, self.VALUES)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            fetched = Path(td, ".bureau-pipeline", "critic-verdict.md").read_text()
            self.assertEqual(fetched, VERDICT)
            self.assertEqual(h.comment_reads(), 2)

    def test_a_persisting_refusal_stops_rather_than_starting_with_no_spec(self):
        """There is no honest substitute for the spec: an empty verdict file
        would start the agent as if this were a conflict round."""
        with tempfile.TemporaryDirectory() as td:
            h = Harness(td, [_comment(QA, VERDICT)], refuse_first=99)
            proc = h.run_step(self.STEP, self.VALUES)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("rate limit", proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            self.assertFalse(
                Path(td, ".bureau-pipeline", "critic-verdict.md").exists(),
                "no verdict file at all — never an empty one that reads as 'no verdict'",
            )


# --------------------------------------------------------------------------
# Source pins: every comment read in this workflow goes through the seam
# --------------------------------------------------------------------------
def logical_lines(text: str):
    """Backslash continuations joined (test_comment_record_pagination's)."""
    out, buf = [], ""
    for line in text.splitlines():
        stripped = line.strip()
        buf = stripped if not buf else buf + " " + stripped
        if buf.endswith("\\"):
            buf = buf[:-1]
            continue
        out.append(buf)
        buf = ""
    if buf:
        out.append(buf)
    return out


_COMMENT_ENDPOINT = re.compile(r"""issues/[^"']+?/comments""")
SEAM = "gh_read_retry.py"


class NoCommentReadIsPipedStraightIntoJqTest(unittest.TestCase):

    def _comment_reads(self):
        return [ln for ln in logical_lines(wf_src())
                if "gh api" in ln and _COMMENT_ENDPOINT.search(ln)
                and "-F body=" not in ln]

    def test_no_read_of_the_comment_record_is_piped_into_jq_unchecked(self):
        offenders = [ln for ln in self._comment_reads()
                     if ("| jq" in ln or "--jq" in ln) and SEAM not in ln]
        self.assertEqual(offenders, [], (
            "these reads hand whatever gh printed — an error body included — "
            "to jq as if it were the record:\n" + "\n".join(offenders)
        ))

    def test_the_reads_are_actually_found(self):
        self.assertGreaterEqual(len(self._comment_reads()), 5, self._comment_reads())

    def test_the_resolve_step_reads_the_record_once_through_the_seam(self):
        body = step_named("Resolve PR, mode, and attempt budget")["run"]
        reads = [ln for ln in logical_lines(body)
                 if "gh api" in ln and _COMMENT_ENDPOINT.search(ln)]
        self.assertEqual(len(reads), 1, reads)
        self.assertIn(SEAM, reads[0])
        self.assertIn("--paginate", reads[0])
        self.assertIn("--slurp", reads[0])
        self.assertNotIn("|| echo '[]'", reads[0], (
            "no `[]` substitute here: an unreadable thread must not read as a "
            "fresh budget, no standing hold and no halt"
        ))

    def test_the_receipt_reads_tolerate_the_absent_substitute(self):
        """The unfixable-check and inherited-failure receipts keep their
        documented rule — an unreadable receipt counts as ABSENT — which means
        the `[]` substitute must actually be readable by their jq: `add[]` on
        `[]` is `null[]`, a jq death of its own."""
        src = wf_src()
        for key in ("HOLD_KEY", "NOTICE_KEY"):
            programs = re.findall(r'jq --arg key "\$%s" \'([^\']*)\'' % key, src)
            self.assertEqual(len(programs), 1, (key, programs))
            proc = subprocess.run(  # nosec B603 — fixed argv, jq
                ["jq", "--arg", "key", "x", programs[0]],
                input="[]", capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, f"{key}: {proc.stderr}")
            self.assertEqual(proc.stdout.strip(), "0", key)

    def test_the_receipt_reads_go_through_the_seam_and_substitute_absent(self):
        for needle in ("unfixable-hold", "inherited-notice"):
            lines = [ln for ln in self._comment_reads() if needle in ln]
            self.assertEqual(len(lines), 1, (needle, self._comment_reads()))
            self.assertIn(SEAM, lines[0])
            self.assertIn("|| echo '[]'", lines[0], needle)


if __name__ == "__main__":
    unittest.main()
