"""agents.yaml is the consumer contract for the console roster (DRE-1335):
every agent workflow has an entry, and budgets match the workflow text they
describe — drift here means the console lies about the fleet.

The MODEL half of that contract moved to config/models.yaml (DRE-2316): the
registry's `model:` is a generated mirror of it, and the workflow no longer
carries the id at all, so the check here is that the workflow SELECTS from the
config rather than pinning a string.

DRE-2729 closed the rest of the contract. `tools:` used to be a comment asking
people to remember, and it had drifted on two of ten entries — the planner
(missing `Edit, Write`) and, worse, the critic that gates every unattended
merge (missing `Write`). It is now checked the same way `maxTurns` is. Two
fields were added at the same time, because locating an agent's invocation is
the expensive part and this file was already doing it: `credentials:` (the
secret NAMES the agent step is handed, never values) and `repoScope:` (the
repositories its job checks out).

Both new checks are scoped to the AGENT STEP, never to the workflow file.
That is DRE-2696 asserted: `LINEAR_API_KEY` appears seven times in
agent-fix.yml, was declared at `workflow_call` and used by sibling steps in
the same job all along, and the ONE place it was missing was the env block of
the step that runs the agent. A file-level scan would have reported that
agent as Linear-capable throughout the entire period it was not — a confident
green over the precise gap the check exists to find."""

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
# The sanctioned-omission list lives with the check that owns it. A roster
# entry that declares no Linear key must point at that list rather than start
# a second one.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pr_size_strategy as pss  # noqa: E402
from test_agent_linear_key import DOCUMENTED_EXCEPTIONS  # noqa: E402

# `--max-turns 80`, or the size-step output qa-review.yml selects (DRE-2466).
_TURNS_RE = re.compile(
    r"--max-turns\s+(?:(\d+)|\$\{\{\s*steps\.size\.outputs\.(\w+)\s*\}\})"
)
_SIZE_OUTPUTS = ("max_turns", "retry_max_turns")

# `--allowedTools "Bash,Edit,Write,Read,Glob,Grep"` — the one place a workflow
# says what an agent may use.
_TOOLS_RE = re.compile(r'--allowedTools\s+"([^"]*)"')

# Every credential reaches a step as a `secrets.NAME` expression, whether it
# arrives through `env:` or through the action's own inputs.
_SECRET_RE = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)")

ACTION = "anthropics/claude-code-action"

# The implicit `actions/checkout` — no `repository:` — is whichever product
# repo dispatched the run, so it cannot be named. It gets a stable token the
# roster can declare.
CALLER = "caller"


def load():
    with open(os.path.join(ROOT, "agents.yaml")) as f:
        return yaml.safe_load(f)["agents"]


def _line_of(src, offset):
    return src.count("\n", 0, offset) + 1


def agent_steps(doc):
    """(job_id, job, step, name) for every claude-code-action step, in file
    order — the same extractor shape as tests/test_agent_linear_key.py."""
    found = []
    for job_id, job in (doc.get("jobs") or {}).items():
        for i, step in enumerate(job.get("steps") or []):
            if str(step.get("uses") or "").split("@")[0] == ACTION:
                name = step.get("name") or step.get("id") or f"steps[{i}]"
                found.append((job_id, job, step, name))
    return found


def step_credentials(doc, job, step):
    """The secret NAMES this one step is handed.

    Two routes, both step-scoped: the env chain GitHub actually builds for the
    step (workflow `env:`, then job `env:`, then step `env:`) and the action's
    own `with:` inputs, which is how the Anthropic credential arrives. NOT a
    file scan — see the module docstring for why that answer is worse than no
    answer at all (DRE-2696)."""
    names = set()
    for block in (doc.get("env"), job.get("env"), step.get("env")):
        for value in (block or {}).values():
            names |= set(_SECRET_RE.findall(str(value)))
    for value in (step.get("with") or {}).values():
        names |= set(_SECRET_RE.findall(str(value)))
    return names


def job_repos(job):
    """The repositories checked out into the agent's workspace.

    The job is the unit here, not the step: steps in one job share one
    workspace, so a repo any step checks out is a repo the agent can read."""
    repos = set()
    for step in job.get("steps") or []:
        if str(step.get("uses") or "").split("@")[0] == "actions/checkout":
            repos.add((step.get("with") or {}).get("repository") or CALLER)
    return repos


def tools_drift(entry, path):
    """Every way an entry's `tools:` can be wrong about its workflow, as
    reader-facing lines. Empty list means the roster matches.

    Multi-invocation workflows (qa-review.yml, verify.yml) are handled the way
    `test_turns_match_workflow_text` handles them — the declared value is
    checked against every invocation — plus one finding the turns test does
    not need: turn budgets differ between an attempt and its retry BY DESIGN,
    tools do not, so invocations that disagree with each other are themselves
    the drift."""
    path = Path(path)
    src = path.read_text()
    doc = yaml.safe_load(src) or {}
    steps = agent_steps(doc)
    hits = list(_TOOLS_RE.finditer(src))
    if len(hits) != len(steps):
        return [
            f"{entry['name']}: {path.name} has {len(steps)} agent "
            f"invocation(s) but {len(hits)} --allowedTools declaration(s) — "
            f"one of them says nothing about what the agent may use"
        ]
    declared = set(entry["tools"])
    problems, seen = [], []
    for (_job_id, _job, step, name), m in zip(steps, hits):
        line = _line_of(src, m.start())
        args = str((step.get("with") or {}).get("claude_args") or "")
        if m.group(0) not in args:
            problems.append(
                f"{entry['name']}: {path.name}:{line} --allowedTools is not "
                f"inside the claude_args of agent step {name!r} — the "
                f"extractor and the invocations are out of step"
            )
            continue
        passed = {t.strip() for t in m.group(1).split(",") if t.strip()}
        seen.append((line, passed))
        if passed != declared:
            problems.append(
                f"{entry['name']}: roster tools {sorted(declared)} != "
                f"{sorted(passed)} passed at {path.name}:{line} "
                f"(agent step {name!r})"
            )
    if len({frozenset(p) for _line, p in seen}) > 1:
        problems.append(
            f"{entry['name']}: the agent invocations in {path.name} disagree "
            f"on tools — "
            + "; ".join(f"{path.name}:{line} {sorted(p)}" for line, p in seen)
        )
    return problems


def credential_drift(entry, path):
    """Every way an entry's `credentials:` can be wrong about the secrets its
    agent STEP receives."""
    path = Path(path)
    doc = yaml.safe_load(path.read_text()) or {}
    declared = set(entry["credentials"])
    problems = []
    for _job_id, job, step, name in agent_steps(doc):
        actual = step_credentials(doc, job, step)
        if actual != declared:
            problems.append(
                f"{entry['name']}: roster credentials {sorted(declared)} != "
                f"{sorted(actual)} reaching agent step {name!r} in "
                f"{path.name} — declared but not handed to the step: "
                f"{sorted(declared - actual)}; handed to the step but "
                f"undeclared: {sorted(actual - declared)}"
            )
    return problems


def repo_scope_drift(entry, path):
    """Every way an entry's `repoScope:` can be wrong about the repositories
    its agent's job checks out."""
    path = Path(path)
    doc = yaml.safe_load(path.read_text()) or {}
    declared = set(entry["repoScope"])
    problems = []
    for _job_id, job, _step, name in agent_steps(doc):
        actual = job_repos(job)
        if actual != declared:
            problems.append(
                f"{entry['name']}: roster repoScope {sorted(declared)} != "
                f"{sorted(actual)} checked out around agent step {name!r} in "
                f"{path.name} — declared but never checked out: "
                f"{sorted(declared - actual)}; reachable but undeclared: "
                f"{sorted(actual - declared)}"
            )
    return problems


class AgentsRegistryTest(unittest.TestCase):
    def test_every_agent_workflow_has_an_entry(self):
        covered = {a["workflow"].split("/")[-1] for a in load()}
        agent_workflows = {"agent-task.yml", "agent-fix.yml", "qa-review.yml",
                           "plan.yml", "medic.yml"}
        self.assertEqual(agent_workflows, covered & agent_workflows)

    def test_turns_match_workflow_text(self):
        """The roster's budget must be one the workflow actually runs with.

        DRE-2466: qa-review.yml no longer carries its ceiling as a literal —
        it sizes the diff first and interpolates the budget the strategy
        selected. An expression is RESOLVED here through the same table the
        workflow reads (standard strategy, the path a review normally takes),
        so the console still cannot drift from the workflow; it just can no
        longer be checked by grepping for a number."""
        for a in load():
            src = open(os.path.join(ROOT, a["workflow"])).read()
            declared = [
                int(literal) if literal else pss.turn_budget("standard")[
                    _SIZE_OUTPUTS.index(name)
                ]
                for literal, name in _TURNS_RE.findall(src)
            ]
            self.assertIn(a["maxTurns"], declared,
                          f"{a['name']}: maxTurns {a['maxTurns']} is not a "
                          f"ceiling {a['workflow']} runs with ({declared})")

    def test_tools_match_workflow_text(self):
        """`tools:` must be what the workflow actually passes (DRE-2729).

        RED before this card: the planner declared `Bash, Read, Glob, Grep`
        while plan.yml passed `Bash, Edit, Write, Read, Glob, Grep`, and the
        critic declared the same four while qa-review.yml passed a fifth,
        `Write`. Nothing checked it, so the only machine-readable record of
        what the merge-gating agent can do was wrong about it."""
        for a in load():
            problems = tools_drift(a, os.path.join(ROOT, a["workflow"]))
            self.assertEqual([], problems, "\n".join(problems))

    def test_credentials_match_the_agent_step(self):
        """`credentials:` must be the secret names the AGENT STEP is handed.

        Step-scoped on purpose: DRE-2696 was a key present seven times in the
        file and absent from the one env block that mattered."""
        for a in load():
            problems = credential_drift(a, os.path.join(ROOT, a["workflow"]))
            self.assertEqual([], problems, "\n".join(problems))

    def test_repo_scope_matches_the_agent_workspace(self):
        """`repoScope:` must be the repositories the agent's job checks out.

        Before DRE-2729 nothing said which repos an agent may act in; the
        answer was whichever repo happened to dispatch it, which is an
        accident of the trigger rather than a stated permission."""
        for a in load():
            problems = repo_scope_drift(a, os.path.join(ROOT, a["workflow"]))
            self.assertEqual([], problems, "\n".join(problems))

    def test_credentials_are_names_never_values(self):
        """The roster records what an agent can reach, never how to reach it.

        agents.yaml is world-readable through the console's contents-API
        fetch, so a value here would be a published secret."""
        for a in load():
            self.assertIn("credentials", a, f"{a['name']}: missing credentials")
            for cred in a["credentials"]:
                self.assertRegex(
                    cred, r"^[A-Z][A-Z0-9_]*$",
                    f"{a['name']}: credential {cred!r} is not a bare secret "
                    f"NAME — the roster must never carry a value")
        raw = open(os.path.join(ROOT, "agents.yaml")).read()
        for marker in ("${{", "secrets.", "lin_api_", "ghp_", "github_pat_",
                       "sk-ant-"):
            self.assertNotIn(
                marker, raw,
                f"agents.yaml contains {marker!r} — the roster declares "
                f"credential names only, never values or the expressions "
                f"that resolve to them")

    def test_an_agent_without_a_credential_is_a_sanctioned_exception(self):
        """A missing credential is either drift or a recorded decision, and
        the roster alone cannot tell them apart.

        The critic declares no LINEAR_API_KEY. That is deliberate (DRE-2052 +
        DRE-2696: card material reaches it through one sanitising stage), and
        the reason is written out in tests/test_agent_linear_key.py. This
        binds the two: an entry may omit the key only where that list already
        sanctions the omission, step by step."""
        for a in load():
            if "LINEAR_API_KEY" in a["credentials"]:
                continue
            wf = a["workflow"].split("/")[-1]
            doc = yaml.safe_load(open(os.path.join(ROOT, a["workflow"])).read())
            for _job_id, _job, _step, name in agent_steps(doc):
                self.assertIn(
                    (wf, name), DOCUMENTED_EXCEPTIONS,
                    f"{a['name']}: declares no LINEAR_API_KEY but "
                    f"{wf} step {name!r} is not in "
                    f"tests/test_agent_linear_key.py's DOCUMENTED_EXCEPTIONS "
                    f"— an omission has to be a decision with a reason, not a "
                    f"blank")

    def test_live_credential_surface_is_the_one_dre_2729_recorded(self):
        """The surface read on 2026-08-26, pinned so the next change to it is
        visible: LINEAR_API_KEY reaches the agent step in six of the seven
        agent workflows (qa-review withholds it deliberately), and the
        dispatch pool reaches agent-task, red-main-repair and verify.

        Note for the reader: the pool that reaches the agent step is
        BUREAU_APP_ID_2..4, three names, not four — the first App's id is
        consumed by the token-mint step's `with:` and never enters the agent
        step's environment."""
        pool = {"BUREAU_APP_ID_2", "BUREAU_APP_ID_3", "BUREAU_APP_ID_4"}
        with_linear, with_pool = set(), set()
        workflows = {a["workflow"] for a in load()}
        self.assertEqual(7, len(workflows), "the fleet is no longer seven "
                                            "agent workflows — re-read the "
                                            "surface before editing this")
        for wf in sorted(workflows):
            doc = yaml.safe_load(open(os.path.join(ROOT, wf)).read())
            for _job_id, job, step, _name in agent_steps(doc):
                creds = step_credentials(doc, job, step)
                if "LINEAR_API_KEY" in creds:
                    with_linear.add(wf.split("/")[-1])
                if pool <= creds:
                    with_pool.add(wf.split("/")[-1])
        self.assertEqual(
            {"agent-task.yml", "agent-fix.yml", "plan.yml", "medic.yml",
             "verify.yml", "red-main-repair.yml"}, with_linear)
        self.assertEqual(
            {"agent-task.yml", "red-main-repair.yml", "verify.yml"}, with_pool)

    def test_header_says_which_fields_are_enforced_and_by_what(self):
        """The next person must not have to discover the enforcement the way
        DRE-2729 did — by auditing ten entries by hand."""
        raw = open(os.path.join(ROOT, "agents.yaml")).read()
        header = raw.split("\nagents:", 1)[0]
        for token in ("tests/test_agents_registry.py", "config/models.yaml",
                      "maxTurns", "tools", "credentials", "repoScope"):
            self.assertIn(token, header,
                          f"agents.yaml's header does not mention {token!r}")

    def test_workflows_resolve_the_model_through_the_config(self):
        """The roster's `model:` is a GENERATED mirror of config/models.yaml
        (DRE-2316) — so it is no longer a literal to grep for in the workflow.

        The old assertion (`model` appears verbatim in the workflow source) is
        exactly what a hardcoded `--model claude-sonnet-4-6` satisfied, which is
        how the critic/verifier/medic bypassed selection while this test stayed
        green. The contract now: the workflow SELECTS, and pins nothing."""
        for a in load():
            src = open(os.path.join(ROOT, a["workflow"])).read()
            self.assertIn("model_fallback.py select", src,
                          f"{a['name']}: {a['workflow']} does not select a model")
            self.assertNotRegex(
                src, r"--model\s+claude-",
                f"{a['name']}: {a['workflow']} hardcodes a model id")

    def test_every_agent_has_a_valid_category(self):
        # category groups the roster in the console by business function
        # (product/development/operations/marketing/sales);
        # purely additive/display — no dispatch impact.
        allowed = {"product", "development", "operations", "marketing", "sales"}
        for a in load():
            self.assertIn("category", a, f"{a['name']}: missing category")
            self.assertIn(a["category"], allowed,
                          f"{a['name']}: category {a['category']!r} not in {allowed}")

    def test_brief_paths_exist_when_set(self):
        for a in load():
            if a.get("briefPath"):
                self.assertTrue(os.path.isfile(os.path.join(ROOT, a["briefPath"])),
                                f"{a['name']}: missing {a['briefPath']}")



# A workflow shaped exactly like a real one: the pipeline checked out beside
# the caller's repo, an ordinary step holding LINEAR_API_KEY, and the agent
# step last. Variants below break ONE thing each, so a green result on the
# live files is not a green result on everything.
_FIXTURE = """
name: fixture
on:
  workflow_call:
    secrets:
      LINEAR_API_KEY:
        required: true
jobs:
  execute:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/checkout@v7
        with:
          repository: dreadnought-foundry/bureau-pipeline
          path: .bureau-pipeline
      - name: Report
        env:
          LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}
        run: python3 .bureau-pipeline/scripts/linear_ops.py comment DRE-1 hi
      - name: Implement card
        uses: anthropics/claude-code-action@v1
        env:
          LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}
        with:
          claude_args: |
            --max-turns 150
            --allowedTools "Bash,Edit,Write,Read,Glob,Grep"
"""

_ENTRY = {
    "name": "fixture",
    "tools": ["Bash", "Edit", "Write", "Read", "Glob", "Grep"],
    "credentials": ["LINEAR_API_KEY"],
    "repoScope": [CALLER, "dreadnought-foundry/bureau-pipeline"],
    "workflow": ".github/workflows/fixture.yml",
}


class DriftFixtureTest(unittest.TestCase):
    """The checks above pass on the corrected live files. These prove they
    are capable of failing — a drift check that cannot go red is the comment
    it replaced."""

    def _write(self, text):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "fixture.yml"
        path.write_text(text)
        return path

    def test_the_fixture_itself_is_clean(self):
        """The control. Without it, every assertion below could be passing
        for the wrong reason."""
        path = self._write(_FIXTURE)
        self.assertEqual([], tools_drift(_ENTRY, path))
        self.assertEqual([], credential_drift(_ENTRY, path))
        self.assertEqual([], repo_scope_drift(_ENTRY, path))

    def test_a_deliberate_tools_mismatch_fails(self):
        """The planner drift, rebuilt: the roster claims four tools and the
        workflow hands over six."""
        path = self._write(_FIXTURE)
        entry = dict(_ENTRY, tools=["Bash", "Read", "Glob", "Grep"])
        problems = tools_drift(entry, path)
        self.assertTrue(problems, "a 4-vs-6 tool mismatch passed")
        self.assertIn("fixture", problems[0])
        self.assertIn("'Edit'", problems[0])
        self.assertIn("'Write'", problems[0])
        self.assertIn("fixture.yml:", problems[0])

    def test_invocations_that_disagree_on_tools_are_themselves_the_finding(self):
        """qa-review.yml and verify.yml each run the agent twice. A turn
        budget legitimately differs between an attempt and its retry; a tool
        list does not, so `assertIn` alone would wave through a retry that
        quietly gained a capability."""
        retry = _FIXTURE + """      - name: Implement card (retry)
        uses: anthropics/claude-code-action@v1
        env:
          LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}
        with:
          claude_args: |
            --max-turns 150
            --allowedTools "Bash,Edit,Write,Read,Glob,Grep,WebFetch"
"""
        problems = tools_drift(_ENTRY, self._write(retry))
        self.assertTrue(any("disagree on tools" in p for p in problems),
                        f"disagreeing invocations passed: {problems}")

    def test_a_credential_in_another_step_but_not_the_agent_step_fails(self):
        """DRE-2696, asserted. The key is declared at workflow_call and used
        by a sibling step in the same job; only the agent step lacks it. A
        file-level scan says the agent has Linear. It does not."""
        blinded = _FIXTURE.replace(
            """        uses: anthropics/claude-code-action@v1
        env:
          LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}
""",
            "        uses: anthropics/claude-code-action@v1\n",
        )
        self.assertNotIn("action@v1\n        env:", blinded,
                         "the fixture edit did not remove the agent env block")
        # The blindness being guarded against: the naive answer is still yes.
        self.assertIn("LINEAR_API_KEY", blinded)
        problems = credential_drift(_ENTRY, self._write(blinded))
        self.assertTrue(problems, "a credential missing from the agent step "
                                  "passed because the file still mentions it")
        self.assertIn("LINEAR_API_KEY", problems[0])
        self.assertIn("Implement card", problems[0])

    def test_an_undeclared_credential_fails(self):
        """Drift runs both ways: a workflow that gains a credential without
        the roster gaining it is the case DRE-2712 is about to create."""
        widened = _FIXTURE.replace(
            "          LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}\n"
            "        with:",
            "          LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}\n"
            "          BUREAU_APP_ID_2: ${{ secrets.BUREAU_APP_ID_2 }}\n"
            "        with:",
        )
        problems = credential_drift(_ENTRY, self._write(widened))
        self.assertTrue(problems, "an undeclared credential passed")
        self.assertIn("BUREAU_APP_ID_2", problems[0])

    def test_an_undeclared_repo_fails(self):
        """A third repo checked out into the agent's workspace is reach the
        roster never granted."""
        widened = _FIXTURE.replace(
            "      - name: Report",
            "      - uses: actions/checkout@v7\n"
            "        with:\n"
            "          repository: dreadnought-foundry/agent-bureau\n"
            "          path: .agent-bureau\n"
            "      - name: Report",
        )
        problems = repo_scope_drift(_ENTRY, self._write(widened))
        self.assertTrue(problems, "an undeclared repo checkout passed")
        self.assertIn("dreadnought-foundry/agent-bureau", problems[0])

    def test_an_invocation_that_declares_no_tools_fails(self):
        """Silence is not a tool list. Deleting the flag must not read as
        'this agent uses nothing'."""
        silent = _FIXTURE.replace(
            '            --allowedTools "Bash,Edit,Write,Read,Glob,Grep"\n', "")
        problems = tools_drift(_ENTRY, self._write(silent))
        self.assertTrue(problems, "an invocation with no --allowedTools passed")
        self.assertIn("--allowedTools", problems[0])


if __name__ == "__main__":
    unittest.main()
