"""A body a model wrote reaches a program through a file, never an argument
(DRE-8819).

Linux refuses any single argument or environment string over 131,072 bytes
(`MAX_ARG_STRLEN`) with E2BIG, and bash reports that as exit code 126 — the
wall that killed DRE-8698's plan run. Two steps of `plan.yml` substituted the
planner's own `post-replan-summary.md`, a file with no bound on its size, into
three `linear_ops.py comment` arguments. They now compose each comment into a
file with bash builtins and post it with `--body-file=`, and the words the CEO
reads do not change.

The walk runs each step's real `run:` script under `bash -e` (Actions' default
for a workflow with no `shell:`), with the scripts it calls stubbed, against a
200 KB summary. The step as it stood before this card is kept below, verbatim,
as the mutation check: the same walk proves it dies at the wall, and, for a
short summary, that it posts exactly what the step posts now.

Two steps read a title the repo's own scripts write — `groom-week.yml`'s pull
request title and `nightly-watch.yml`'s alarm title. Short by construction,
they are now read bounded, `$(head -c 256 …)`: 256 bytes is GitHub's own title
limit, and the form is the argv check's contract for a bounded read.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")
PLAN = os.path.join(WORKFLOWS, "plan.yml")

RE_REVIEW = "Review — the revised plan is reviewed again"
BOUND = "Review — the bound parks the plan in Triage"
EPIC = "DRE-9100"
HOW = "clear needs-human and move the epic to Planning"

#: The kernel's ceiling on one argument (`MAX_ARG_STRLEN`, 32 pages).
MAX_ARG_STRLEN = 131072
#: The summary the walk posts: well past the wall.
BIG = 200 * 1024
#: The bounded read every title site uses, and the argv check's ceiling on it.
TITLE_READ = re.compile(r"\$\(head -c (\d+) ")
BOUNDED_READ_MAX = 4096
TITLE_BOUND = 256

# The two steps as they stood on `main` before DRE-8819, verbatim — the
# mutation check. Each passes `${CHANGED}`, the whole summary, as one argument.
BEFORE = {
    RE_REVIEW: r'''EPIC="${{ github.event.client_payload.identifier }}"
SUMMARY="${{ runner.temp }}/post-replan-summary.md"
if [ -s "$SUMMARY" ]; then
  CHANGED="What the re-plan changed: $(cat "$SUMMARY")"
elif [ "$REPLAN_OUTCOME" = "success" ]; then
  CHANGED="The re-plan ran but wrote no summary."
else
  CHANGED="The re-plan did not finish (outcome: ${REPLAN_OUTCOME:-not run}), so the plan is as the critic saw it."
fi
case "$ROUND" in ''|*[!0-9]*) ROUND=1;; esac
NEXT=$((ROUND + 1))
if python3 .bureau-pipeline/scripts/review_rerun.py dispatch \
     --epic "$EPIC" \
     --repo "$GITHUB_REPOSITORY" \
     --reason re-review --trigger-state planning; then
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$EPIC" \
    "🔁 The second critic sent the plan back and the planner has revised it. The critic's finding: ${FINDING}. ${CHANGED} The review is being run again by the pipeline, on its own, as round ${NEXT} — nothing here is yours to decide, and the epic stays in Planning. If it sends the plan back again with a finding this revision did not settle, or the plan keeps growing new gaps, the epic parks in Triage with needs-human for an operator."
else
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$EPIC" \
    "🚨 The revised plan's review could NOT be asked for — the dispatch did not go through, so nothing is reading the plan. The critic's finding: ${FINDING}. ${CHANGED} This run is red, so the medic picks it up; nothing here is yours to do."
  exit 1
fi
''',
    BOUND: r'''EPIC="${{ github.event.client_payload.identifier }}"
HOW=$(PYTHONPATH=.bureau-pipeline/scripts python3 -c 'import plan_critic; print(plan_critic.REAPPROVE_HOW)')
SUMMARY="${{ runner.temp }}/post-replan-summary.md"
if [ -s "$SUMMARY" ]; then
  CHANGED="What the re-plan changed: $(cat "$SUMMARY")"
elif [ "$REPLAN_OUTCOME" = "success" ]; then
  CHANGED="The re-plan ran but wrote no summary — read the children before re-planning."
else
  CHANGED="The re-plan did not finish (outcome: ${REPLAN_OUTCOME:-not run}), so the plan is as the critic saw it."
fi
if [ -z "$NOTE" ]; then NOTE="The critic's finding this round: ${FINDING}."; fi
if python3 .bureau-pipeline/scripts/plan_bound.py exit "$EPIC" --stage post \
     --repo "$GITHUB_REPOSITORY"; then
  exit 0   # the exit asked the CEO a question or granted the planner a rewrite — nothing to park
fi
python3 .bureau-pipeline/scripts/hold.py apply "$EPIC" --reason plan-critic-bound --by plan.yml
python3 .bureau-pipeline/scripts/linear_ops.py state "$EPIC" "Triage"
python3 .bureau-pipeline/scripts/linear_ops.py comment "$EPIC" \
  "🛑 Parked in Triage with needs-human for an operator — nothing reaches Green Light until a person settles it. ${NOTE} ${CHANGED} When it is settled, ${HOW}."
''',
}

# `linear_ops.py`: every call logged; a comment's body read the way the real
# CLI reads it — `--body-file=` through the real `body_from_file`, which drops
# trailing newlines exactly as the command substitution did.
LINEAR_STUB = r'''import json, os, sys
sys.path.insert(0, os.environ["REAL_SCRIPTS"])
from linear_ops import body_from_file
args = sys.argv[1:]
entry = {"cmd": args[0], "args": args[1:]}
if args[0] == "comment":
    flat = body_from_file(args[1:])
    entry = {"cmd": "comment", "id": flat[0], "body": flat[1],
             "by_file": any(a.startswith("--body-file=") for a in args[1:])}
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(entry) + "\n")
'''

# The other three: each logs its call and exits with the code the walk names.
CALL_STUB = r'''import json, os, sys
name = os.path.basename(sys.argv[0])
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"cmd": name, "args": sys.argv[1:]}) + "\n")
sys.exit(int(os.environ.get("STUB_RC_" + name.split(".")[0].upper(), "0")))
'''

# `python3 -c 'import plan_critic; …'`: the module the bound step reads
# REAPPROVE_HOW from, on the PYTHONPATH it names.
PLAN_CRITIC_STUB = f"REAPPROVE_HOW = {HOW!r}\n"


def _steps(path):
    doc = yaml.safe_load(open(path, encoding="utf-8"))
    for job in doc["jobs"].values():
        for s in job.get("steps") or []:
            yield s


def _run(path, name):
    found = [s for s in _steps(path) if s.get("name") == name]
    assert len(found) == 1, f"{name!r}: {len(found)} steps in {path}"
    return str(found[0].get("run") or "")


def _summary(size: int) -> str:
    """A summary of `size` bytes whose first and last lines are unique."""
    head = "BEGIN: Rewrote DRE-9002 so it names who runs the migration.\n"
    tail = "END: the last line of the summary."
    line = "- Split DRE-9003 off DRE-9002 and moved its tests with it.\n"
    middle = line * ((size - len(head) - len(tail)) // len(line) + 1)
    return (head + middle)[: size - len(tail)] + tail


class _Walk(unittest.TestCase):
    """One step's `run:` script, under `bash -e`, with its callees stubbed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = os.path.join(self.tmp, "work")
        self.temp = os.path.join(self.tmp, "runner-temp")
        scripts = os.path.join(self.work, ".bureau-pipeline", "scripts")
        os.makedirs(scripts)
        os.makedirs(self.temp)
        for name, body in (("linear_ops.py", LINEAR_STUB),
                           ("review_rerun.py", CALL_STUB),
                           ("plan_bound.py", CALL_STUB),
                           ("hold.py", CALL_STUB),
                           ("plan_critic.py", PLAN_CRITIC_STUB)):
            with open(os.path.join(scripts, name), "w", encoding="utf-8") as f:
                f.write(body)
        self.log = os.path.join(self.tmp, "log.jsonl")

    def _script(self, text: str) -> str:
        values = {"github.event.client_payload.identifier": EPIC,
                  "runner.temp": self.temp}

        def expr(m):
            key = m.group(1).strip()
            self.assertIn(key, values, f"an expression the walk does not know: {key}")
            return values[key]

        return re.sub(r"\$\{\{(.*?)\}\}", expr, text)

    def _shell(self, text: str, summary=None, **env_extra):
        if os.path.exists(self.log):
            os.remove(self.log)
        path = os.path.join(self.temp, "post-replan-summary.md")
        if os.path.exists(path):
            os.remove(path)
        if summary is not None:
            with open(path, "w", encoding="utf-8") as f:
                f.write(summary)
        script = os.path.join(self.tmp, "step.sh")
        with open(script, "w", encoding="utf-8") as f:
            f.write(self._script(text))
        env = {"PATH": os.environ["PATH"], "HOME": self.tmp,
               "RUNNER_TEMP": self.temp, "GITHUB_REPOSITORY": "dreadnought-foundry/demo",
               "STUB_LOG": self.log, "REAL_SCRIPTS": SCRIPTS,
               "FINDING": "no card manufactures the operator step",
               "ROUND": "1", "NOTE": "", "REPLAN_OUTCOME": "success",
               "STUB_RC_PLAN_BOUND": "1"}
        env.update(env_extra)
        done = subprocess.run(["bash", "--noprofile", "--norc", "-e", script],
                              cwd=self.work, env=env, capture_output=True, text=True,
                              timeout=60)
        calls = []
        if os.path.exists(self.log):
            with open(self.log, encoding="utf-8") as f:
                calls = [json.loads(line) for line in f if line.strip()]
        return done, calls

    @staticmethod
    def _comments(calls):
        return [c for c in calls if c["cmd"] == "comment"]


# The three comments, as (step, the env that reaches it, the exit it ends on).
COMMENTS = (
    (RE_REVIEW, {}, 0),
    (RE_REVIEW, {"STUB_RC_REVIEW_RERUN": "1"}, 1),
    (BOUND, {}, 0),
)


class TheCommentsPostFromAFile(_Walk):
    def test_a_200_kb_summary_is_posted_whole(self):
        summary = _summary(BIG)
        self.assertGreater(len(summary.encode()), MAX_ARG_STRLEN)
        for name, env, rc in COMMENTS:
            with self.subTest(step=name, env=env):
                done, calls = self._shell(_run(PLAN, name), summary, **env)
                self.assertEqual(done.returncode, rc, done.stderr)
                self.assertNotIn("Argument list too long", done.stderr)
                [comment] = self._comments(calls)
                self.assertEqual(comment["id"], EPIC)
                self.assertTrue(comment["by_file"], "posted with --body-file=")
                self.assertIn("What the re-plan changed: " + summary, comment["body"])

    def test_the_step_before_this_card_dies_at_the_wall(self):
        """The mutation check: the same walk, against the step as it stood,
        never posts — bash reports E2BIG as 126."""
        summary = _summary(BIG)
        for name, env, _ in COMMENTS:
            with self.subTest(step=name, env=env):
                done, calls = self._shell(BEFORE[name], summary, **env)
                self.assertTrue(done.returncode == 126
                                or "Argument list too long" in done.stderr,
                                (done.returncode, done.stderr[-300:]))
                self.assertEqual(self._comments(calls), [])

    def test_the_ceo_reads_the_same_words(self):
        """For every summary the old step could post, the new one posts the
        same bytes — and the same other calls, in the same order."""
        tricky = ("Rewrote DRE-9002 — \"quoted\", 'single', $HOME, `tick`, 100% "
                  "and a back\\slash.\n\n  - an indented line\n\n\n")
        cases = (
            ("a short summary", tricky, {}),
            ("a one-line summary", "Rewrote DRE-9002.", {}),
            ("no summary, the re-plan ran", None, {}),
            ("no summary, the re-plan did not run", None, {"REPLAN_OUTCOME": ""}),
            ("an empty summary, the re-plan failed", "", {"REPLAN_OUTCOME": "failure"}),
            ("a finding with a percent", tricky,
             {"FINDING": "50% of DRE-9001's \\n criteria — \"unmet\""}),
            ("an unreadable round", tricky, {"ROUND": "two"}),
            ("a note of the bound's own", tricky, {"NOTE": "Two rounds, both held."}),
        )
        for label, summary, extra in cases:
            for name, env, rc in COMMENTS:
                with self.subTest(case=label, step=name, env=env):
                    both = dict(env, **extra)
                    old, old_calls = self._shell(BEFORE[name], summary, **both)
                    new, new_calls = self._shell(_run(PLAN, name), summary, **both)
                    self.assertEqual((old.returncode, new.returncode), (rc, rc),
                                     (old.stderr, new.stderr))
                    self.assertEqual(len(self._comments(new_calls)), 1)
                    for c in old_calls + new_calls:
                        c.pop("by_file", None)
                    self.assertEqual(new_calls, old_calls)

    def test_no_comment_line_carries_the_summary(self):
        for name in (RE_REVIEW, BOUND):
            with self.subTest(step=name):
                run = _run(PLAN, name)
                self.assertNotIn('$(cat "$SUMMARY")', run)
                self.assertNotIn("${CHANGED}", run)
                self.assertNotIn("$CHANGED", run)
                posts = re.findall(r"linear_ops\.py comment (.*)", run)
                self.assertTrue(posts)
                for args in posts:
                    self.assertRegex(args, r'^"\$EPIC" --body-file="\$\w+"$')
        self.assertEqual(sum(len(re.findall(r"linear_ops\.py comment ", _run(PLAN, n)))
                             for n in (RE_REVIEW, BOUND)), 3)


class TheTitlesAreReadBounded(unittest.TestCase):
    SITES = {
        "groom-week.yml": ("Open or update the week's pull request", '"$TITLE_FILE"'),
        "nightly-watch.yml": ("Raise the alarm on the console",
                              '"$RUNNER_TEMP/alarm-title.txt"'),
    }

    def test_each_title_is_read_with_head_c_256(self):
        for wf, (name, path) in self.SITES.items():
            with self.subTest(workflow=wf):
                run = _run(os.path.join(WORKFLOWS, wf), name)
                self.assertIn(f"$(head -c {TITLE_BOUND} {path})", run)

    def test_no_run_block_holds_an_unbounded_file_read(self):
        for wf in self.SITES:
            for s in _steps(os.path.join(WORKFLOWS, wf)):
                run = str(s.get("run") or "")
                with self.subTest(workflow=wf, step=s.get("name")):
                    self.assertNotIn("$(cat", run)
                    self.assertNotIn("$(<", run)
                    self.assertNotRegex(run, r"`\s*cat\b")
                    for n in TITLE_READ.findall(run):
                        self.assertLessEqual(int(n), BOUNDED_READ_MAX)

    def test_the_bounded_read_keeps_a_title_byte_for_byte(self):
        """`head -c 256` under a command substitution drops the trailing
        newline exactly as `cat` did, for every title these steps read."""
        title = ("groom-week: replay cases for the week ending 2026-10-24 — "
                 "1 new, 1 recorded on main")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as f:
            f.write(title + "\n")
        self.addCleanup(os.remove, f.name)
        out = subprocess.run(["bash", "-c", 'printf "%s" "$(head -c 256 "$1")"', "_", f.name],
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(out, title)


if __name__ == "__main__":
    unittest.main()
