"""The build report asks the wait reader before it spends a strike (DRE-7802).

An API-error death used to go straight to `dead_run.py decide --is-error
--error-model <model>`: one of the card's strikes spent and the model
switched. Two of those deaths are not the card's fault — the 401 our own
token renewal causes, and the model service refusing for a moment — and
`vendor_wait.py kind` (DRE-7795) names them. This card is the switch: the
`api_death` branch of `scripts/report_agent_result.sh` asks the reader, and
on `rotation` or `vendor-transient` passes `--wait-kind <kind> --run-id`
(DRE-7800) instead, which answers `limit` — one marker, nothing moved,
through the receipt-only path below the decision.

What this file pins, driving the script by its path:

  1. **The wait.** The reader's `rotation` and `vendor-transient` reach
     `decide` as `--wait-kind <kind> --run-id <run>`, never with `--is-error`
     or `--error-model`, beside the same `--comments-file` (DRE-7801's cap).
  2. **Today's call, byte for byte.** `none`, a reader that fails, a word
     outside the contract, and a failed call that printed a kind all reach
     `decide` with exactly the argument list it had before this card.
  3. **Only an API-error death asks.** A silent death never runs the reader.
  4. **End to end.** With the real reader and the real decision, a 529 posts
     the wait marker and moves nothing; a 403 still spends a strike, with its
     `model-error:` marker, and requeues.

The harness copies tests/test_report_agent_result.py's: `linear_ops.py`
records, `card_pr.py` finds no PR, `git` answers nothing, and every other
script in the checkout is the real one unless the test stubs it.

Run: python3 -m pytest tests/test_report_agent_result_wait.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "report_agent_result.sh")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import dead_run  # noqa: E402, RUF100
import lane_contract  # noqa: E402, RUF100

CARD = "DRE-7802"
REPO = "dreadnought-foundry/bureau-pipeline"
RUN_ID = "34000000002"
RUN_URL = f"https://github.com/{REPO}/actions/runs/{RUN_ID}"
MODEL = "claude-opus-5"

#: The step's `env:` keys, scrubbed from the inherited environment.
ENV_KEYS = (
    "LINEAR_API_KEY", "GH_TOKEN", "CARD", "RESUME_BRANCH", "RESUME_SHA",
    "RESUME_SNAPSHOT", "MODEL_USED", "CLAUDE_OUTCOME", "DEDUPE_OUTCOME",
    "MODEL_OUTCOME", "CTX_OUTCOME", "SANITIZE_OUTCOME", "INPROGRESS_OUTCOME",
    "PRE_AGENT_LOG", "RESCUE_PUSH_STATUS", "RESCUE_PATCH", "RESCUE_ARTIFACT",
    "RESCUE_PUSHED", "RESCUE_ERROR", "GH_DISPATCH_TOKEN", "BUREAU_SERVER_URL",
    "BUREAU_REPOSITORY", "BUREAU_RUN_ID", "CLAUDE_EXECUTION_FILE",
    "RESCUE_LOCAL_WORK", "RESCUE_PR_OPENED", "RESCUE_PR_URL", "AGENT_NOTES_DIR",
)

#: The card's thread, as `dump-comments` prints it: a bare JSON array of bodies.
THREAD = ["⏳ 1/5 plan", "🤖 something the pipeline said"]

LINEAR_STUB = '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"tool": "linear_ops", "args": sys.argv[1:]}) + "\\n")
if sys.argv[1] == "count-comments":
    print("0")
if sys.argv[1] == "dump-comments":
    print(os.environ["STUB_THREAD"])
'''

CARD_PR_NO_PR_STUB = '''#!/usr/bin/env python3
print("\\t")
'''

#: The reader, answering what the test picks: `STUB_KIND` on stdout, and
#: `STUB_KIND_EXIT` as its exit status.
VENDOR_WAIT_STUB = '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"tool": "vendor_wait", "args": sys.argv[1:]}) + "\\n")
if os.environ.get("STUB_KIND"):
    print(os.environ["STUB_KIND"])
sys.exit(int(os.environ.get("STUB_KIND_EXIT", "0")))
'''

#: The decision, recording its argument list and the thread it was handed.
DEAD_RUN_STUB = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
entry = {"tool": "dead_run", "args": args}
if "--comments-file" in args:
    with open(args[args.index("--comments-file") + 1], encoding="utf-8") as fh:
        entry["thread"] = fh.read()
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(entry) + "\\n")
if args[0] == "decide":
    print(os.environ.get("STUB_ACTION", "limit"))
    print()
    print("the body")
'''


def _record(**fields) -> dict:
    """A result record as the action writes it: the one-turn, $0 death."""
    base = {"type": "result", "subtype": "success", "is_error": True,
            "num_turns": 1, "total_cost_usd": 0}
    base.update(fields)
    return base


#: A 529 `overloaded_error`: `classify` says api_death, the reader vendor-transient.
OVERLOADED = _record(
    api_error_status=529,
    result=('API Error: 529 {"type":"error","error":'
            '{"type":"overloaded_error","message":"Overloaded"}}'))
#: A 403: `classify` says api_death, the reader none.
FORBIDDEN = _record(
    api_error_status=403,
    result=('API Error: 403 {"type":"error","error":'
            '{"type":"permission_error","message":"Forbidden"}}'))
#: A run that ended cleanly with no PR: a silent death, not an api_death.
FINISHED = {"type": "result", "subtype": "success", "is_error": False,
            "num_turns": 120, "total_cost_usd": 9.40, "result": "done"}


def _executable(path: str, body: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, 0o755)


class Run:
    """One run of the script from a runner-shaped directory."""

    def __init__(self, tc, *, record=OVERLOADED, reader="stub",
                 stub_decide=True, **env_extra):
        self.td = tempfile.mkdtemp()
        tc.addCleanup(shutil.rmtree, self.td, ignore_errors=True)
        base = os.path.join(self.td, ".bureau-pipeline")
        scripts = os.path.join(base, "scripts")
        shutil.copytree(os.path.join(ROOT, "scripts"), scripts,
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(os.path.join(ROOT, "config"), os.path.join(base, "config"))
        _executable(os.path.join(scripts, "linear_ops.py"), LINEAR_STUB)
        _executable(os.path.join(scripts, "card_pr.py"), CARD_PR_NO_PR_STUB)
        # `reader`: "stub" answers what the test picks, "real" is the
        # checkout's own, "missing" is no reader on the disk at all.
        if reader == "stub":
            _executable(os.path.join(scripts, "vendor_wait.py"), VENDOR_WAIT_STUB)
        elif reader == "missing":
            os.remove(os.path.join(scripts, "vendor_wait.py"))
        if stub_decide:
            _executable(os.path.join(scripts, "dead_run.py"), DEAD_RUN_STUB)
        binary = os.path.join(self.td, "bin")
        os.makedirs(binary)
        _executable(os.path.join(binary, "git"), "#!/bin/sh\nexit 0\n")
        self.exec_file = os.path.join(self.td, "claude-execution-output.json")
        with open(self.exec_file, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        self.notes = os.path.join(self.td, "notes")
        os.makedirs(self.notes)
        log = os.path.join(self.td, "stub.jsonl")
        env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
        env.update(
            PATH=binary + os.pathsep + os.environ["PATH"],
            RUNNER_TEMP=self.td,
            LINEAR_API_KEY="test-key",
            GH_TOKEN="test",
            CARD=CARD,
            RESUME_SNAPSHOT=os.path.join(self.td, "resume-branches.json"),
            MODEL_USED=MODEL,
            CLAUDE_OUTCOME="failure",
            DEDUPE_OUTCOME="success",
            MODEL_OUTCOME="success",
            CTX_OUTCOME="success",
            SANITIZE_OUTCOME="success",
            INPROGRESS_OUTCOME="success",
            PRE_AGENT_LOG=os.path.join(self.td, "preagent.log"),
            BUREAU_SERVER_URL="https://github.com",
            BUREAU_REPOSITORY=REPO,
            BUREAU_RUN_ID=RUN_ID,
            CLAUDE_EXECUTION_FILE=self.exec_file,
            RESCUE_LOCAL_WORK="false",
            AGENT_NOTES_DIR=self.notes,
            STUB_LOG=log,
            STUB_THREAD=json.dumps(THREAD),
        )
        env.update(env_extra)
        self.proc = subprocess.run(["bash", SCRIPT], check=False, cwd=self.td, env=env,
                                   capture_output=True, text=True)
        tc.assertEqual(0, self.proc.returncode, self.proc.stderr + self.proc.stdout)
        self.journal = []
        if os.path.exists(log):
            with open(log, encoding="utf-8") as fh:
                self.journal = [json.loads(line) for line in fh.read().splitlines()]

    def calls(self, tool: str, op: str | None = None) -> list[dict]:
        return [e for e in self.journal if e["tool"] == tool
                and (op is None or e["args"][0] == op)]

    def decide(self, tc) -> dict:
        found = self.calls("dead_run", "decide")
        tc.assertEqual(1, len(found), self.journal)
        return found[0]

    def comments_file(self) -> str:
        return os.path.join(self.td, "card-comments.json")

    def handback_file(self) -> str:
        return os.path.join(self.notes, "agent-handback.txt")

    def todays_call(self) -> list[str]:
        """`decide`'s argument list for an api_death on `main` before this card."""
        return ["decide", "0", "--comments-file", self.comments_file(),
                "--is-error", "--error-model", MODEL,
                "--failed-step", "", "--run-url", RUN_URL,
                "--handback-file", self.handback_file()]

    def wait_call(self, kind: str) -> list[str]:
        return ["decide", "0", "--comments-file", self.comments_file(),
                "--wait-kind", kind, "--run-id", RUN_ID,
                "--failed-step", "", "--run-url", RUN_URL,
                "--handback-file", self.handback_file()]


class AWaitReachesTheDecisionAsAWait(unittest.TestCase):

    def _assert_waits(self, kind: str):
        run = Run(self, STUB_KIND=kind)
        decide = run.decide(self)
        self.assertEqual(run.wait_call(kind), decide["args"])
        self.assertNotIn("--is-error", decide["args"])
        self.assertNotIn("--error-model", decide["args"])
        # DRE-7801's cap reads the same thread, as a bare JSON array.
        self.assertEqual(THREAD, json.loads(decide["thread"]))

    def test_a_rotation_passes_its_kind_and_the_run(self):
        self._assert_waits("rotation")

    def test_a_vendor_transient_passes_its_kind_and_the_run(self):
        self._assert_waits("vendor-transient")

    def test_the_reader_is_asked_about_this_runs_record(self):
        run = Run(self, STUB_KIND="rotation")
        (asked,) = run.calls("vendor_wait")
        self.assertEqual(["kind", run.exec_file], asked["args"])

    def test_the_limit_answer_posts_the_body_and_moves_nothing(self):
        run = Run(self, STUB_KIND="rotation", STUB_ACTION="limit")
        posted = [e["args"] for e in run.calls("linear_ops", "comment")]
        self.assertEqual([["comment", CARD, "the body"]], posted)
        self.assertEqual([], run.calls("linear_ops", "state"))
        self.assertEqual([], run.calls("linear_ops", "advance"))
        self.assertEqual([], run.calls("dead_run", "park"))


class EveryOtherAnswerIsTodaysCall(unittest.TestCase):

    def _assert_today(self, **env):
        run = Run(self, **env)
        decide = run.decide(self)
        self.assertEqual(run.todays_call(), decide["args"])
        self.assertNotIn("--wait-kind", decide["args"])
        self.assertNotIn("--run-id", decide["args"])
        self.assertEqual(THREAD, json.loads(decide["thread"]))

    def test_none_is_todays_call(self):
        self._assert_today(STUB_KIND="none")

    def test_a_reader_that_fails_is_todays_call(self):
        self._assert_today(STUB_KIND="", STUB_KIND_EXIT="1")

    def test_a_failed_call_that_printed_a_kind_is_todays_call(self):
        self._assert_today(STUB_KIND="rotation", STUB_KIND_EXIT="1")

    def test_a_word_outside_the_contract_is_todays_call(self):
        self._assert_today(STUB_KIND="claude")

    def test_a_reader_that_is_not_there_is_todays_call(self):
        self._assert_today(reader="missing")


class OnlyAnApiErrorDeathAsks(unittest.TestCase):

    def test_a_silent_death_never_runs_the_reader(self):
        run = Run(self, record=FINISHED, STUB_KIND="rotation", STUB_ACTION="requeue")
        self.assertEqual([], run.calls("vendor_wait"))
        decide = run.decide(self)
        self.assertNotIn("--wait-kind", decide["args"])
        self.assertNotIn("--is-error", decide["args"])

    def test_the_script_names_the_reader_once(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            text = fh.read()
        code = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
        self.assertEqual(1, sum("vendor_wait.py kind" in line for line in code))


class EndToEndWithTheRealReaderAndDecision(unittest.TestCase):

    def _moves(self, run: Run) -> list[list[str]]:
        return [e["args"] for e in run.journal
                if e["tool"] == "linear_ops" and e["args"][0] in ("state", "advance")]

    def _posted(self, run: Run) -> list[str]:
        return [e["args"][2] for e in run.calls("linear_ops", "comment")]

    def test_a_529_posts_the_wait_marker_and_moves_nothing(self):
        run = Run(self, record=OVERLOADED, reader="real", stub_decide=False)
        posted = self._posted(run)
        self.assertEqual(1, len(posted), posted)
        first = posted[0].split("\n", 1)[0]
        self.assertTrue(first.startswith(dead_run.LIMIT_MARK), first)
        self.assertIn("kind=vendor-transient stage=build", first)
        self.assertIn(f"run={RUN_ID}", first)
        self.assertNotIn("model-error:", posted[0])
        self.assertNotIn(dead_run.DEAD_TAG, first)
        self.assertEqual([], self._moves(run))

    def test_a_403_still_spends_a_strike_and_switches_the_model(self):
        run = Run(self, record=FORBIDDEN, reader="real", stub_decide=False)
        posted = self._posted(run)
        self.assertEqual(1, len(posted), posted)
        self.assertIn(dead_run.DEAD_TAG, posted[0].split("\n", 1)[0])
        self.assertIn(f"model-error: {MODEL}", posted[0])
        self.assertFalse(posted[0].startswith(dead_run.LIMIT_MARK), posted[0])
        self.assertEqual([["state", CARD, lane_contract.QUEUE_LANE, "--park"]],
                         self._moves(run))


if __name__ == "__main__":
    unittest.main()
