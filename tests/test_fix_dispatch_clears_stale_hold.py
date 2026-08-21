"""RED-first tests for DRE-2601 — a fix attempt that dispatches on an open PR
clears the stale `needs-human` hold.

THE BUG (live): `needs-human` sat on DRE-2509 from 2026-08-19 04:09 UTC through
its merge at 16:40:54 PT. The card was parked once, work resumed by direct
dispatch, the PR was fixed and merged — and nothing ever took the label off. The
label is read by the Needs-you queue, the board's hold badge and
`operator_complete`'s gate, so a card the pipeline was actively fixing read as
parked in all three. (The console half — the "Pipeline gave up" alert deriving
from the give-up receipt instead of the label — landed in DRE-2555; the label
itself is written and cleared here.)

THE RULE: getting past the Resolve step with `go=true` means a fix attempt is
actually STARTING on an open PR — the pipeline owns the card again, so the "a
human must act" claim the label makes is false and must be cleared. Every path
that REFUSES to run (fix budget exhausted, conflict budget exhausted,
convergence halt, closed PR, non-agent branch) sets `go=false` and therefore
never reaches the clear: a card genuinely parked at a cap keeps its label.

The tests EXECUTE the real workflow shell rather than grepping it (the
tests/test_critic_size_strategy.py OversizedPostStepScenarioTest pattern):

  * the Announce step's own `run:` body, against a stubbed linear_ops.py that
    records every subcommand it is handed — proves the clear actually FIRES,
    in both fix and conflict mode, and that a Linear failure cannot fail the run
  * the Resolve step's own `run:` body, against a stubbed `gh` (real jq filters)
    — proves the retry cap really does produce `go=false`, so the guarded clear
    cannot reach a genuinely parked card

Run: python3 -m pytest tests/test_fix_dispatch_clears_stale_hold.py -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "agent-fix.yml")

HOLD_LABEL = "needs-human"
CARD = "DRE-2509"
BRANCH = f"agent/{CARD}-mobile-capture"
REPO = "dreadnought-foundry/agent-bureau"
PR = "120"
SHA = "d9f2c1ab" + "0" * 32

WORKER_BOT = "agent-bureau-bot[bot]"
FIX_PUSHED = "🔧 Fix attempt {n} pushed — CI and critic review re-running."

# The one guard the clear hides behind. Pinned as a literal: if the Announce
# step's condition is ever widened, the retry-cap proof below stops holding and
# this harness must be revisited rather than silently keep passing.
GO_GUARD = "steps.pr.outputs.go == 'true'"


def steps() -> list:
    return yaml.safe_load(open(WORKFLOW))["jobs"]["fix"]["steps"]


def step_named(name: str) -> dict:
    for step in steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"step {name!r} not found in agent-fix.yml")


def substitute(run: str, values: dict) -> str:
    """Apply the `${{ ... }}` substitutions Actions would make, and prove none
    survive — an unsubstituted expression is a hole in the harness, not a pass."""
    def repl(m):
        key = m.group(1).strip()
        if key not in values:
            raise AssertionError(f"harness has no value for ${{{{ {key} }}}}")
        return values[key]

    out = re.sub(r"\$\{\{([^}]*)\}\}", repl, run)
    assert "${{" not in out
    return out


def write_exec(path: str, body: str) -> None:
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, 0o755)


# ── the Announce step, executed for real ───────────────────────────────────


def run_announce(td: str, mode: str, card: str = CARD, linear_exit: int = 0):
    """Execute the real 'Announce fix attempt' run block. Returns (proc, calls),
    where calls is the list of linear_ops.py argv lists it made."""
    os.makedirs(os.path.join(td, ".bureau-pipeline", "scripts"), exist_ok=True)
    log = os.path.join(td, "linear-calls.jsonl")
    write_exec(
        os.path.join(td, ".bureau-pipeline", "scripts", "linear_ops.py"),
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({log!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"sys.exit({linear_exit})\n",
    )
    run = substitute(
        step_named("Announce fix attempt")["run"],
        {
            "steps.pr.outputs.mode": mode,
            "steps.pr.outputs.attempt": "2",
            "steps.pr.outputs.number": PR,
            "steps.pr.outputs.card": card,
        },
    )
    script = os.path.join(td, "announce.sh")
    # The runner's own shell flags (bash --noprofile --norc -eo pipefail).
    with open(script, "w") as f:
        f.write("set -eo pipefail\n" + run)
    proc = subprocess.run(
        ["bash", script],
        cwd=td,
        env=dict(os.environ, LINEAR_API_KEY="test-key", GH_TOKEN="test"),
        capture_output=True,
        text=True,
    )
    calls = [
        json.loads(line)
        for line in (open(log).read().splitlines() if os.path.exists(log) else [])
    ]
    return proc, calls


def clears(calls: list) -> list:
    return [c for c in calls if c[:1] == ["remove-label"]]


class AnnounceClearsStaleHoldTest(unittest.TestCase):
    """The dispatch path clears the label — executed, not grepped."""

    def test_fix_attempt_dispatch_clears_needs_human(self):
        with tempfile.TemporaryDirectory() as td:
            proc, calls = run_announce(td, "fix")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(clears(calls), [["remove-label", CARD, HOLD_LABEL]])

    def test_conflict_round_dispatch_clears_needs_human(self):
        # A conflict round is a fix attempt too: an agent is working the branch,
        # so the card is not waiting on a human either.
        with tempfile.TemporaryDirectory() as td:
            proc, calls = run_announce(td, "conflict")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(clears(calls), [["remove-label", CARD, HOLD_LABEL]])

    def test_announce_comment_still_posted(self):
        # Non-vacuous guard: the clear is an addition, not a replacement.
        with tempfile.TemporaryDirectory() as td:
            proc, calls = run_announce(td, "fix")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        comments = [c for c in calls if c[:1] == ["comment"]]
        self.assertEqual(len(comments), 1)
        self.assertIn("🔧 Fix agent dispatched", comments[0][2])

    def test_cardless_branch_clears_nothing(self):
        # repair/* branches carry no DRE-N; there is no card to unpark and the
        # step must not shell out with an empty identifier.
        with tempfile.TemporaryDirectory() as td:
            proc, calls = run_announce(td, "fix", card="")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(calls, [])

    def test_linear_failure_while_clearing_does_not_fail_the_run(self):
        # A Linear outage must never cost the fix run — the label is
        # bookkeeping, the fix is the work.
        with tempfile.TemporaryDirectory() as td:
            proc, calls = run_announce(td, "fix", linear_exit=1)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(clears(calls), [["remove-label", CARD, HOLD_LABEL]])

    def test_clear_uses_the_idempotent_remove_label_verb(self):
        # remove-label is a documented no-op on a card that does not carry the
        # label — clearing an absent label must not be an error. (unpark would
        # ALSO move the card to Todo and reset the death budget, which would
        # re-dispatch a whole new build on top of the live PR.)
        # Executable lines only — the step's comments explain the choice and
        # name `unpark` while doing no such thing.
        code = "\n".join(
            ln for ln in step_named("Announce fix attempt")["run"].splitlines()
            if not ln.lstrip().startswith("#")
        )
        self.assertIn("remove-label", code)
        self.assertNotIn("unpark", code)


class RemoveLabelIsIdempotentTest(unittest.TestCase):
    """The verb the workflow leans on: absent label = no-op, not an error."""

    def test_absent_label_is_a_no_op(self):
        import sys

        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        os.environ.setdefault("LINEAR_API_KEY", "test-key")
        import linear_ops
        from unittest import mock

        payload = {"issue": {"id": "iss", "labels": {"nodes": [{"id": "1", "name": "repo:x"}]}}}
        with mock.patch.object(linear_ops, "gql", return_value=payload) as gql:
            linear_ops.remove_label(CARD, HOLD_LABEL)
        # One read, no mutation: nothing to detach.
        self.assertEqual(gql.call_count, 1)


# ── the Resolve step, executed for real ────────────────────────────────────


GH_STUB = '''#!/usr/bin/env python3
"""Stand-in for `gh`: serves the PR view and the comments API from fixtures,
runs the workflow's REAL jq filters, and records writes."""
import json, os, subprocess, sys

args = sys.argv[1:]
info = json.load(open(os.environ["GH_PR_INFO"]))
comments = json.load(open(os.environ["GH_COMMENTS"]))
log = os.environ["GH_LOG"]


def record():
    open(log, "a").write(json.dumps(args) + "\\n")


if args[:2] == ["pr", "view"]:
    print(json.dumps(info))
elif args[:2] == ["pr", "comment"]:
    record()
elif args[0] == "api":
    payload = [comments] if "--slurp" in args else comments
    if "--jq" in args:
        expr = args[args.index("--jq") + 1]
        out = subprocess.run(["jq", "-r", expr], input=json.dumps(payload),
                             capture_output=True, text=True)
        sys.stderr.write(out.stderr)
        sys.exit(out.returncode) if out.returncode else sys.stdout.write(out.stdout)
    else:
        print(json.dumps(payload))
else:
    sys.stderr.write("unexpected gh call: %r\\n" % (args,))
    sys.exit(2)
'''


def comment(login: str, body: str) -> dict:
    return {"user": {"login": login}, "body": body}


def run_resolve(td: str, comments: list, merge_state: str = "CLEAN"):
    """Execute the real 'Resolve PR, mode, and attempt budget' run block.
    Returns the step outputs (last write wins, as Actions does)."""
    os.makedirs(os.path.join(td, "bin"), exist_ok=True)
    write_exec(os.path.join(td, "bin", "gh"), GH_STUB)
    info = os.path.join(td, "pr-info.json")
    with open(info, "w") as f:
        json.dump(
            {
                "state": "OPEN",
                "headRefName": BRANCH,
                "headRefOid": SHA,
                "mergeStateStatus": merge_state,
            },
            f,
        )
    comments_file = os.path.join(td, "comments.json")
    with open(comments_file, "w") as f:
        json.dump(comments, f)
    out_file = os.path.join(td, "step-output")
    open(out_file, "w").close()

    run = substitute(
        step_named("Resolve PR, mode, and attempt budget")["run"],
        {
            "github.event.issue.number || github.event.inputs.pr_number": PR,
            "github.repository": REPO,
        },
    )
    script = os.path.join(td, "resolve.sh")
    with open(script, "w") as f:
        f.write("set -eo pipefail\n" + run)
    proc = subprocess.run(
        ["bash", script],
        cwd=td,
        env=dict(
            os.environ,
            PATH=f"{td}/bin:{os.environ['PATH']}",
            GITHUB_OUTPUT=out_file,
            GH_PR_INFO=info,
            GH_COMMENTS=comments_file,
            GH_LOG=os.path.join(td, "gh-calls.jsonl"),
            GH_TOKEN="test",
        ),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    outputs = {}
    for line in open(out_file).read().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            outputs[key] = value
    return outputs


def announce_would_run(outputs: dict) -> bool:
    """Would Actions run the Announce step (and therefore the clear) given
    these Resolve outputs? Pins the guard rather than assuming it."""
    guard = (step_named("Announce fix attempt").get("if") or "").strip()
    assert guard == GO_GUARD, f"Announce guard changed to {guard!r} — revisit"
    return outputs.get("go") == "true"


class ParkedAtTheCapKeepsItsLabelTest(unittest.TestCase):
    """The hold that is REAL survives: at the fix-attempt cap the workflow
    refuses to dispatch, so the clear never runs."""

    def test_fix_budget_exhausted_holds_and_never_reaches_the_clear(self):
        thread = [comment(WORKER_BOT, FIX_PUSHED.format(n=n)) for n in (1, 2, 3)]
        with tempfile.TemporaryDirectory() as td:
            outputs = run_resolve(td, thread)
        self.assertEqual(outputs["go"], "false")
        self.assertEqual(outputs["held"], "true")
        self.assertFalse(announce_would_run(outputs))

    def test_conflict_budget_exhausted_holds_and_never_reaches_the_clear(self):
        thread = [
            comment(WORKER_BOT, f"🔀 Conflict resolution round {n} pushed")
            for n in range(1, 6)
        ]
        with tempfile.TemporaryDirectory() as td:
            outputs = run_resolve(td, thread, merge_state="DIRTY")
        self.assertEqual(outputs["mode"], "conflict")
        self.assertEqual(outputs["go"], "false")
        self.assertEqual(outputs["held"], "true")
        self.assertFalse(announce_would_run(outputs))

    def test_convergence_halt_never_reaches_the_clear(self):
        # Two no-push runs on this exact head: the loop is not converging and
        # the halt refuses. A refusal is not a resumption — the label stands.
        body = (
            f"🛑 Fix attempt 1 pushed no new commit (branch still at `{SHA[:8]}`) — "
            "the reviewer will not re-run and the last verdict stands."
        )
        thread = [comment(WORKER_BOT, body), comment(WORKER_BOT, body)]
        with tempfile.TemporaryDirectory() as td:
            outputs = run_resolve(td, thread)
        self.assertEqual(outputs["go"], "false")
        self.assertFalse(announce_would_run(outputs))

    def test_an_ordinary_attempt_does_reach_the_clear(self):
        # Non-vacuous twin: the same harness, one attempt short of the cap, must
        # dispatch — otherwise the three tests above prove nothing.
        thread = [comment(WORKER_BOT, FIX_PUSHED.format(n=1))]
        with tempfile.TemporaryDirectory() as td:
            outputs = run_resolve(td, thread)
        self.assertEqual(outputs["go"], "true")
        self.assertEqual(outputs["attempt"], "2")
        self.assertTrue(announce_would_run(outputs))


if __name__ == "__main__":
    unittest.main()
