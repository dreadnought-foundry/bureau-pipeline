"""Run a REAL build agent against the sandbox, on the SHIPPED prompt (DRE-2490).

The adversarial scenarios exist to prove the DRE-2487 pre-submit gate is
OBEYED, not merely present. That only means anything if the agent under test
receives the exact prompt agent-task.yml ships: this module reads the
`prompt:` input straight out of the workflow file in the checkout under test
and substitutes the seeded card into it. A copy of the prompt pasted in here
would prove that the harness's copy obeys the gate — worth nothing.

What is real and what is not (honest coverage limits):

  * REAL — the prompt (agent-task.yml's own text), the assembled role context
    (assemble_context.py, same call the workflow makes), the model ladder, the
    sandbox clone the agent edits, and the PR/comments it produces. The
    driver's assertions read only those GitHub-side artifacts.
  * NOT REAL — the claude-code-action wrapper. The harness invokes the same
    Claude Code CLI the action wraps, because a scenario cannot dispatch a
    workflow and still watch the run from inside its own phase. The action's
    own plumbing (App-token mint, allowed_bots) is proven live by bot_pr_flow
    and by every production run; it is not what these scenarios measure.
  * NOT REAL — Linear. The clone gets an `.bureau-pipeline/agent-context.md`
    and nothing else, so the brief's heartbeat command finds no linear_ops.py
    and no-ops. The harness's zero-Linear-writes property (see the package
    README) survives a real agent run: the seeded card lives in the sandbox,
    never in Linear.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess  # nosec B404 — the agent CLI and git; argv lists, never a shell
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

# NO module-level third-party imports. Scenario discovery imports every module
# in the package, and the DEFAULT sweep runs on a bare runner with nothing pip
# installed (harness.yml installs test tooling only for named scenarios) — an
# `import yaml` up here killed the whole default sweep before a scenario ran
# (run 32093002253). PyYAML is imported inside the one function that needs it,
# which only the agent scenarios reach; test_harness_adversarial_scenarios.py
# pins that discovery survives without it.

# The workflow whose prompt is under test, relative to the pipeline checkout.
AGENT_TASK_WORKFLOW = os.path.join(".github", "workflows", "agent-task.yml")

# The sandbox branch the prompt's expressions resolve against when a caller
# does not say. Every scenario knows its repo's real default branch (setup()
# reads it off GitHub) and passes it; this is the value for the unit suites,
# which parse the prompt without a sandbox to ask.
DEFAULT_BRANCH = "main"

# The agent CLI the action wraps. Overridable so a runner with the CLI already
# installed skips the npx fetch (and so a pin can be forced in an incident).
DEFAULT_AGENT_CLI = "npx --yes @anthropic-ai/claude-code@latest"

# Mirrors agent-task.yml's claude_args: same turn budget, same tool surface.
MAX_TURNS = 150
ALLOWED_TOOLS = "Bash,Edit,Write,Read,Glob,Grep"

# One agent run's wall-clock ceiling. Well under harness.yml's job timeout so a
# hung agent fails its scenario with a named timeout instead of killing the run.
AGENT_TIMEOUT_SECONDS = 2700.0

# The escape hatches the prompt names by absolute path. The real workflow posts
# these to the card; the harness reads them the same way, as agent OUTPUT — not
# as transcript internals (the scenarios assert on GitHub artifacts; these only
# ever ADD a way for the agent to decline in a sandbox that has no Linear).
ESCALATION_PATH = "/tmp/agent-escalation.txt"  # nosec B108 — the prompt's own path
BLOCKER_PATH = "/tmp/agent-blocker.txt"  # nosec B108 — the prompt's own path

_EXPRESSION_RE = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)


@dataclass
class SeededCard:
    """The card the agent is dispatched on. Same four fields the relay's
    client_payload carries, so the substitution below is total."""

    identifier: str
    title: str
    description: str
    url: str


@dataclass
class AgentRunResult:
    """What the run left behind. `returncode` is diagnostics only — a scenario
    NEVER passes or fails on it (an agent that declines correctly and an agent
    that crashed both exit non-zero); the assertions read GitHub."""

    returncode: int = 0
    escalation: str = ""
    blocker: str = ""
    workdir: str = ""
    seconds: float = 0.0
    notes: list = field(default_factory=list)

    @property
    def declined(self) -> bool:
        """The agent used one of the prompt's declared escape hatches. In the
        live pipeline both files are posted to the card verbatim, so this is
        the sandbox's equivalent of that comment."""
        return bool(self.escalation.strip() or self.blocker.strip())


def agent_task_prompt(workflow_path) -> str:
    """The `prompt:` input of agent-task.yml's claude-code-action step, read
    from the PARSED workflow — the same read tests/test_presubmit_gate_prompt.py
    makes, so both see the string the agent actually receives."""
    import yaml  # noqa: PLC0415 — see the import note at the top of the module

    with open(workflow_path, encoding="utf-8") as f:
        workflow = yaml.safe_load(f)
    prompts = [
        (step.get("with") or {}).get("prompt")
        for job in (workflow.get("jobs") or {}).values()
        for step in (job.get("steps") or [])
        if "anthropics/claude-code-action" in (step.get("uses") or "")
    ]
    prompts = [p for p in prompts if p]
    if len(prompts) != 1:
        raise ValueError(
            f"{workflow_path}: expected exactly one agent prompt, found "
            f"{len(prompts)} — the harness cannot guess which one ships"
        )
    return prompts[0]


def build_prompt(
    card: SeededCard,
    workflow_path=None,
    role: str = "engineer",
    default_branch: str = DEFAULT_BRANCH,
) -> str:
    """The shipped prompt with the seeded card substituted for its Actions
    expressions.

    Every expression must be known: an unrecognised one raises rather than
    reaching the agent as literal `${{ … }}` noise. That is deliberate — when
    agent-task.yml grows an interpolation, this fails loudly and someone
    decides what the harness should feed it, instead of the scenarios quietly
    testing a corrupted prompt.

    `default_branch` is the SANDBOX's own default branch — the value the live
    expression resolves to in the caller repo. The commit-order self-check
    (DRE-2694, agent-task.yml step 4b) compares against `origin/<it>`, so a
    hardcoded name here would hand the agent a ref the sandbox may not have.
    """
    workflow_path = workflow_path or AGENT_TASK_WORKFLOW
    prompt = agent_task_prompt(workflow_path)
    values = {
        "github.event.repository.default_branch": default_branch,
        "github.event.client_payload.identifier": card.identifier,
        "github.event.client_payload.title": card.title,
        "github.event.client_payload.url": card.url,
        "github.event.client_payload.description": card.description,
        "steps.card.outputs.title": card.title,
        "steps.card.outputs.description": card.description,
        "steps.gate.outputs.role || 'engineer'": role,
        "steps.gate.outputs.role": role,
    }

    def replace(match):
        expression = match.group(1).strip()
        if expression not in values:
            raise ValueError(
                f"{workflow_path}: unknown prompt expression ${{{{ "
                f"{expression} }}}} — the harness has no value for it"
            )
        return values[expression]

    return _EXPRESSION_RE.sub(replace, prompt)


def assemble_agent_context(pipeline_root: str, role: str = "engineer") -> str:
    """The role's context blob, from the pipeline checkout under test — the
    same assemble_context.py call agent-task.yml makes, so the agent reads the
    standards and brief of the ref being tested, not of main."""
    sys.path.insert(0, os.path.join(pipeline_root, "scripts"))
    import assemble_context  # noqa: PLC0415 — path-dependent by design

    prefix = ".bureau-pipeline" + os.sep

    def read(path: str) -> str:
        # assemble() addresses files as .bureau-pipeline/<...>; re-root them at
        # the checkout the harness is running from.
        relative = path[len(prefix):] if path.startswith(prefix) else path
        with open(os.path.join(pipeline_root, relative), encoding="utf-8") as f:
            return f.read()

    return assemble_context.assemble(role, read)


def _clear(path: str) -> None:
    """Remove an escape-hatch file left by an earlier scenario — a stale
    /tmp/agent-escalation.txt would read as THIS agent declining."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _read_hatch(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _select_model(pipeline_root: str, runner, log) -> str:
    """The engineer role's model, off the shared ladder (model_fallback.py).
    A failed probe yields "" — the CLI then picks its own default, which is a
    degraded but honest run; the scenarios measure the PROMPT, not the model."""
    try:
        done = runner(
            [
                sys.executable,
                os.path.join(pipeline_root, "scripts", "model_fallback.py"),
                "select",
                "engineer",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception as e:  # a selector blow-up must not fail the scenario here
        log(f"agent: model selection failed ({e}) — using the CLI default")
        return ""
    if done.returncode != 0:
        log("agent: model selection returned non-zero — using the CLI default")
        return ""
    return (done.stdout or "").strip().splitlines()[-1] if done.stdout.strip() else ""


def run_agent(
    card: SeededCard,
    repo: str,
    token: str,
    workdir: str | None = None,
    pipeline_root: str = ".",
    role: str = "engineer",
    default_branch: str = DEFAULT_BRANCH,
    runner=subprocess.run,
    env: dict | None = None,
    timeout: float = AGENT_TIMEOUT_SECONDS,
    log=print,
) -> AgentRunResult:
    """Clone the sandbox, assemble the role context, and run the shipped
    prompt through the agent CLI as the worker bot.

    The token reaches git through the clone URL and the agent through GH_TOKEN,
    and is never logged: every log line here is built from `repo`, never from
    the URL.
    """
    workdir = workdir or tempfile.mkdtemp(prefix="harness-agent-")
    if os.path.exists(workdir):
        shutil.rmtree(workdir)
    os.makedirs(os.path.dirname(workdir) or ".", exist_ok=True)

    started = time.monotonic()
    result = AgentRunResult(workdir=workdir)
    # The credential rides the clone URL (actions/checkout's own pattern) so
    # the agent can push the branch it opens its PR from. It lives only in this
    # throwaway clone's .git/config, on an ephemeral runner, and never in a log.
    clone_url = f"https://x-access-token:{token}@github.com/{repo}.git"
    log(f"agent: cloning {repo} for card {card.identifier}")
    runner(["git", "clone", "--depth", "50", clone_url, workdir], check=True)
    for key, value in (
        ("user.name", "agent-bureau-bot"),
        ("user.email", "agent-bureau-bot@users.noreply.github.com"),
    ):
        runner(["git", "-C", workdir, "config", key, value], check=True)

    context_dir = Path(workdir) / ".bureau-pipeline"
    context_dir.mkdir(parents=True, exist_ok=True)
    (context_dir / "agent-context.md").write_text(
        assemble_agent_context(pipeline_root, role), encoding="utf-8"
    )

    prompt = build_prompt(
        card,
        workflow_path=os.path.join(pipeline_root, AGENT_TASK_WORKFLOW),
        role=role,
        default_branch=default_branch,
    )

    _clear(ESCALATION_PATH)
    _clear(BLOCKER_PATH)

    cli = shlex.split(os.environ.get("HARNESS_AGENT_CLI", DEFAULT_AGENT_CLI))
    model = os.environ.get("HARNESS_AGENT_MODEL") or _select_model(
        pipeline_root, runner, log
    )
    argv = [*cli, "-p", prompt, "--max-turns", str(MAX_TURNS)]
    if model:
        argv += ["--model", model]
    argv += ["--allowedTools", ALLOWED_TOOLS]

    child_env = dict(env or os.environ)
    child_env["GH_TOKEN"] = token
    child_env["GITHUB_TOKEN"] = token

    log(f"agent: running the shipped agent-task prompt (model: {model or 'CLI default'})")
    try:
        done = runner(
            argv,
            cwd=workdir,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        result.returncode = getattr(done, "returncode", 0)
        tail = (getattr(done, "stdout", "") or "")[-2000:]
        if tail:
            result.notes.append(tail)
    except subprocess.TimeoutExpired:
        result.returncode = 124
        result.notes.append(f"agent exceeded its {timeout:.0f}s budget")
        log(f"agent: timed out after {timeout:.0f}s — the scenario still asserts "
            "on what the sandbox shows")

    result.escalation = _read_hatch(ESCALATION_PATH)
    result.blocker = _read_hatch(BLOCKER_PATH)
    result.seconds = time.monotonic() - started
    log(
        f"agent: finished in {result.seconds:.0f}s (exit {result.returncode}"
        f"{', declined' if result.declined else ''})"
    )
    return result


def run_tests(directory: str, runner=subprocess.run, timeout: float = 300.0):
    """Run pytest over `directory`; returns (returncode, output).

    The mutation evidence (scenario 5) rides on this: the shipped test is run
    against the seeded logic and again against a mutant, and only the pair of
    outcomes is evidence. Never raises — a non-zero code IS the signal.
    """
    try:
        done = runner(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", directory],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=directory,
        )
    except subprocess.TimeoutExpired:
        return 124, f"pytest exceeded {timeout:.0f}s in {directory}"
    output = (getattr(done, "stdout", "") or "") + (getattr(done, "stderr", "") or "")
    return getattr(done, "returncode", 1), output
