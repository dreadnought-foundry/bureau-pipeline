"""The build agent checks its own commit order BEFORE it pushes (DRE-2694).

WHAT IS ALREADY BUILT, AND WHAT IT DOES NOT REACH
-------------------------------------------------
`scripts/unfixable_checks.py` made the `TDD commit discipline` failure state
plainly that no commit can clear it, and made the fix loop escalate on the
FIRST attempt instead of the second. That turns a silent three-hour stall into
an immediate, visible ask — but the ask still lands on a HUMAN, because by the
time CI speaks the commits are pushed and only a force-push can reorder them.

The card's remaining remedy is the cheap rung below that one: *"Fail earlier,
where it is cheap. The discipline is knowable at the second commit. A build
agent could be told at push time rather than after a full CI and critic round
— the cost today was two review cycles plus three hours, for a rule violated
in the first thirty seconds of work."*

Before the first push the same violation costs nothing: the branch is local
and unreviewed, and reordering is an ordinary rebase the agent does itself. So
`agent-task.yml`'s prompt carries a numbered, mandatory commit-order
self-check that runs BEFORE the push — the one place in the whole loop where
this class of failure still has an automated path to green.

Why a test and not just prompt text: a prompt is the one part of this pipeline
with no compiler and no runtime error (the argument
tests/test_presubmit_gate_prompt.py makes at length). A step that evaporates
in a later edit of a 70-line prompt block fails SILENTLY, and the only symptom
is another PR that stops permanently on a rule it broke in its first minute.

WHAT THIS FILE PINS
-------------------
  * the self-check is a NUMBERED step in the build prompt, not prose;
  * it runs BEFORE the step that pushes and opens the PR;
  * the command it hands the agent is the LOCAL form — `HEAD`, against
    `origin/<default branch>` — because a command that only works on a pushed
    PR would be the CI check again, one rung too late;
  * that command, extracted from the prompt and actually RUN against a throwaway
    git repository, catches a code-first branch and passes a test-first one.
    Pinning the words without running them would let a renamed script or a
    swapped argument order ship as a green test;
  * the step names the remedy (reorder now) and the reason it is urgent (after
    the push, nothing the automation can do clears it);
  * README's onboarding list carries the fleet-wide check, so a repo onboarded
    tomorrow is guarded by construction rather than by whoever remembers.

Scope is the BUILD prompt. agent-fix.yml is deliberately out: its branch is
already pushed and already carries a review, so a local reorder is not
available to it — its remedy for this class is the first-attempt escalation
`unfixable_checks.py` already performs.

Run: cd bureau-pipeline && python3 -m pytest tests/test_presubmit_commit_order.py -v
"""

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF_DIR = os.path.join(REPO, ".github", "workflows")
TASK_WF = "agent-task.yml"

CHECKER = "check_tdd_commits.py"

# The path the checker has inside a PRODUCT repo's checkout: agent-task.yml
# clones this repo into `.bureau-pipeline/` beside the product's own tree, and
# every other script the prompts name is addressed the same way (the heartbeat
# line in briefs/engineer.md, for one). A prompt naming a bare `scripts/...`
# path would resolve to the PRODUCT repo's scripts directory — usually nothing,
# occasionally something else entirely.
PIPELINE_CHECKER_PATH = f".bureau-pipeline/scripts/{CHECKER}"

# The base the local check compares against. The expression is the caller's
# repository record, which every webhook payload carries — the same one
# red-main-repair.yml and qa-review.yml already read.
DEFAULT_BRANCH_EXPR = "${{ github.event.repository.default_branch }}"

# The submit action the self-check must precede.
PUSH_RE = re.compile(r"(?i)push the branch")

# Semantic pins: loose enough that rewording survives, tight enough that
# REMOVING the idea cannot.
REORDER_RE = re.compile(r"(?i)\b(reorder|re-order|rebase)\b")
BEFORE_PUSH_RE = re.compile(r"(?i)before (you |the )?push")
NO_LATER_FIX_RE = re.compile(r"(?i)\b(no|cannot|can't|nothing)\b")


def workflow(name):
    with open(os.path.join(WF_DIR, name)) as f:
        return yaml.safe_load(f)


def agent_prompt(name):
    """The `prompt:` input of the workflow's claude-code-action step, read from
    the PARSED yaml — the string the agent actually receives, not a grep of the
    file."""
    prompts = []
    for job in workflow(name)["jobs"].values():
        for step in job.get("steps") or []:
            if "anthropics/claude-code-action" in (step.get("uses") or ""):
                prompts.append((step.get("with") or {}).get("prompt"))
    assert len(prompts) == 1, f"{name}: expected one agent step, got {len(prompts)}"
    assert prompts[0], f"{name}: agent step has no prompt"
    return prompts[0]


def norm(text):
    """Whitespace-collapsed. The prompt is hard-wrapped at ~70 columns, so
    every semantic pin has to read ACROSS a line break — matching the raw
    string would make a reflow of the same words look like a removal."""
    return re.sub(r"\s+", " ", text)


def numbered_steps(prompt):
    """The prompt's mandatory ordered list as [(label, body)] in written order
    — the same reading tests/test_presubmit_gate_prompt.py takes, including the
    house's inserted-step form ("4b."). The self-check has to BE one of these
    items; a sentence in the surrounding prose reads as optional colour."""
    items = []
    hits = list(re.finditer(r"(?m)^\s*(\d+[a-z]?)\.\s", prompt))
    for i, hit in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(prompt)
        items.append((hit.group(1), prompt[hit.end() : end]))
    return items


def selfcheck_index(items):
    """Index of the numbered step that runs the commit-order checker."""
    for i, (_, body) in enumerate(items):
        if CHECKER in body:
            return i
    return -1


def selfcheck_body(items):
    idx = selfcheck_index(items)
    assert idx >= 0, (
        f"{TASK_WF}: no numbered step runs {CHECKER} — the build agent learns "
        "its commit order is wrong only from CI, by which time the commits are "
        "pushed and only a human can reorder them (DRE-2694)"
    )
    return items[idx][1]


def selfcheck_command(prompt):
    """The single command line the prompt hands the agent, verbatim."""
    lines = [ln.strip() for ln in prompt.splitlines() if CHECKER in ln]
    assert len(lines) == 1, (
        f"{TASK_WF}: expected exactly one {CHECKER} command line in the "
        f"prompt, found {len(lines)}"
    )
    return lines[0]


def runnable_command(prompt, base="main"):
    """The prompt's command, made runnable HERE: the product-checkout path
    rewritten to this repo's own scripts directory and the default-branch
    expression resolved. Nothing else is touched — the arguments, their order
    and the `HEAD` are the prompt's own, which is the point of running it."""
    line = selfcheck_command(prompt)
    line = line.replace(PIPELINE_CHECKER_PATH, os.path.join(REPO, "scripts", CHECKER))
    line = line.replace(DEFAULT_BRANCH_EXPR, base)
    return shlex.split(line)


class SelfCheckIsAStepTest(unittest.TestCase):
    """The self-check exists, is mandatory, and runs before the push."""

    def setUp(self):
        self.prompt = agent_prompt(TASK_WF)
        self.items = numbered_steps(self.prompt)

    def test_the_self_check_is_a_numbered_mandatory_step(self):
        self.assertGreaterEqual(
            selfcheck_index(self.items),
            0,
            f"{TASK_WF}: the commit-order self-check must be a numbered step "
            "in the mandatory process list",
        )

    def test_the_self_check_precedes_the_push(self):
        selfcheck_body(self.items)  # fail here if the step is gone at all
        check = selfcheck_index(self.items)
        push = -1
        for i, (_, body) in enumerate(self.items):
            if PUSH_RE.search(norm(body)):
                push = i
        self.assertGreaterEqual(push, 0, "no step that pushes the branch found")
        self.assertLess(
            check,
            push,
            "the commit-order check must run BEFORE the push — after it, the "
            "same finding has no automated path to green at all and costs a "
            "human a rebase (DRE-2694, PR #176: three hours)",
        )

    def test_the_step_names_the_remedy_and_why_it_is_urgent(self):
        body = norm(selfcheck_body(self.items))
        self.assertRegex(
            body,
            REORDER_RE,
            "the step must say what to DO about a red result — reorder the "
            "commits — not merely report it",
        )
        self.assertRegex(
            body,
            BEFORE_PUSH_RE,
            "the step must say the window is BEFORE the push; that is the "
            "whole reason it exists this early",
        )
        self.assertRegex(
            body,
            NO_LATER_FIX_RE,
            "the step must say that a later commit cannot clear this — an "
            "agent that thinks it can fix it afterwards will push and find out",
        )


class SelfCheckCommandTest(unittest.TestCase):
    """The command the prompt hands over is the LOCAL form, and it works."""

    def setUp(self):
        self.prompt = agent_prompt(TASK_WF)
        self.command = selfcheck_command(self.prompt)

    def test_the_command_addresses_the_pipeline_checkout(self):
        self.assertIn(
            PIPELINE_CHECKER_PATH,
            self.command,
            "the checker lives in the pipeline checkout the workflow clones "
            "into `.bureau-pipeline/`, not in the product repo's own tree",
        )
        self.assertTrue(
            os.path.exists(os.path.join(REPO, "scripts", CHECKER)),
            f"the prompt names scripts/{CHECKER}, which does not exist here",
        )

    def test_the_command_is_the_local_unpushed_form(self):
        argv = runnable_command(self.prompt)
        self.assertEqual(
            argv[-1],
            "HEAD",
            "the head argument must be HEAD — the branch as it stands "
            "locally, before any push. A pushed sha would be the CI check "
            "again, one rung too late",
        )
        self.assertEqual(
            argv[-2],
            "origin/main",
            "the base must be origin/<default branch> resolved from the "
            f"caller's repository record ({DEFAULT_BRANCH_EXPR})",
        )


class SelfCheckCommandRunsTest(unittest.TestCase):
    """Run the prompt's own command against a throwaway repository. Pinning
    the words without running them lets a renamed script, a swapped argument
    order or a stale flag ship as a green test."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.add_commit("README.md", "docs: base")
        # A local ref standing in for the fetched base branch, so the command
        # runs exactly as written against a branch that was never pushed.
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.dir, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        self.git("update-ref", "refs/remotes/origin/main", head)
        self.git("checkout", "-q", "-b", "agent/DRE-1-x")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.dir, check=True,
                       capture_output=True, text=True)

    def add_commit(self, rel, msg):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write("x\n")
        self.git("add", rel)
        self.git("commit", "-q", "-m", msg)

    def run_selfcheck(self):
        return subprocess.run(
            runnable_command(agent_prompt(TASK_WF)),
            cwd=self.dir, capture_output=True, text=True,
        )

    def test_code_first_branch_is_caught_before_the_push(self):
        self.add_commit("scripts/thing.py", "feat: the code")
        self.add_commit("tests/test_thing.py", "test: the test, too late")
        result = self.run_selfcheck()
        self.assertEqual(
            result.returncode,
            1,
            "the prompt's own command must catch a code-first branch:\n"
            f"{result.stdout}\n{result.stderr}",
        )
        self.assertIn(
            "no test commit precedes the implementation",
            result.stdout,
            "the agent has to read WHY it failed, not just a non-zero exit",
        )

    def test_test_first_branch_passes(self):
        self.add_commit("tests/test_thing.py", "test: RED")
        self.add_commit("scripts/thing.py", "feat: green")
        result = self.run_selfcheck()
        self.assertEqual(
            result.returncode,
            0,
            "a test-first branch must pass — a self-check that fires on "
            "correct work teaches the agent to ignore it:\n"
            f"{result.stdout}\n{result.stderr}",
        )


class StandardHandsWritersTheCommandTest(unittest.TestCase):
    """The cause half of DRE-2694: a dispatched agent gets the command from
    the prompt above, and every other writer — a coordinating agent, an
    operator session, a sub-agent handed a task brief — gets nothing but the
    repo. The standard is the one document all of them do read, so the
    command lives there too. Knowing the rule and being able to CHECK it
    before pushing are different things, and only the second is free."""

    def setUp(self):
        with open(os.path.join(REPO, "standards", "engineering.md")) as f:
            self.standard = f.read()

    def bullet(self):
        """The one bullet that names the checker — asserting against the whole
        file would pass on any stray "before pushing" elsewhere in it (the
        Acceptance section has one), which is a pin that proves nothing."""
        for para in re.split(r"(?m)^(?=- )", self.standard):
            if CHECKER in para:
                return norm(para)
        return None

    def test_the_build_discipline_rule_names_the_command(self):
        # assertTrue, not assertIn: a failing assertIn prints the entire
        # standard as the container and buries the message.
        self.assertTrue(
            self.bullet(),
            "standards/engineering.md tells writers the commit order is not "
            f"repairable afterwards but never names scripts/{CHECKER} — the "
            "one command that answers it while the branch is still local",
        )

    def test_the_command_is_offered_before_the_push(self):
        bullet = self.bullet()
        self.assertTrue(bullet, f"no bullet in the standard names {CHECKER}")
        self.assertRegex(
            bullet,
            BEFORE_PUSH_RE,
            "the same bullet must place the check BEFORE the push; afterwards "
            "only a human with force-push rights can act on its answer",
        )


class OnboardingCarriesTheCheckTest(unittest.TestCase):
    """The detection half of DRE-2694: the fleet sweep found the violation in
    every repo and the check in exactly one. The carrier action shipped, but
    the onboarding list — the only place that says what a new repo must have —
    never named it, so the next repo onboarded would be unguarded by
    construction."""

    def setUp(self):
        with open(os.path.join(REPO, "README.md")) as f:
            self.readme = f.read()

    def onboarding_section(self):
        match = re.search(
            r"(?ms)^## Onboarding a new product repo\n(.*?)(?=^## |\Z)",
            self.readme,
        )
        assert match, "README lost its onboarding section"
        return match.group(1)

    def test_onboarding_requires_the_tdd_commit_check(self):
        section = norm(self.onboarding_section())
        self.assertIn(
            "tdd-commit-check",
            section,
            "onboarding must name the shared TDD commit-order action — a repo "
            "onboarded without it absorbs the violation silently, which is "
            "what portico #343 did (green, mergeable, test committed after "
            "its code)",
        )

    def test_onboarding_names_the_check_run_the_fix_loop_matches(self):
        section = self.onboarding_section()
        self.assertIn(
            "TDD commit discipline",
            section,
            "the job name is load-bearing: scripts/unfixable_checks.py matches "
            "the published check-run name to escalate on the first attempt",
        )


if __name__ == "__main__":
    unittest.main()
