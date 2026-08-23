"""Every agent step that may need Linear actually gets the key (DRE-2696).

THE BUG (portico PR #341, 2026-08-23). The fix agent was asked to compute a
critical-path depth from the dependency links on its card. It could not
reach Linear, correctly refused to guess, and the PR stopped dead.

Nothing was wrong with the credential. `LINEAR_API_KEY` is declared as a
`workflow_call` secret in agent-fix.yml (and in every other agent workflow),
and sibling STEPS in the very same job use it — agent-fix's own "Report"
step reads it to post the fix-loop comment. The one place it was missing was
the `env:` block of the step that runs the agent, so the process that needed
it was the only process in the job without it.

A sweep of the eight `anthropics/claude-code-action` sites on 2026-08-23
found five threaded and three not:

    HAS      agent-task.yml       "Implement card"
    HAS      plan.yml             "Plan epic"
    HAS      medic.yml            "Diagnose"
    HAS      verify.yml           (two agent steps)
    MISSING  agent-fix.yml        "Fix"                 ← the live blocker
    MISSING  red-main-repair.yml  "Repair agent"
    OMITTED  qa-review.yml        "Critic review" ×2    ← deliberate, below

The agents run with `--allowedTools "Bash,..."` and reach Linear by running
`scripts/linear_ops.py`, which reads `LINEAR_API_KEY` from the environment.
No env block means no Linear — and worse than silently: `briefs/engineer.md`,
which the agent-fix and red-main-repair prompts BOTH point their agent at,
says in as many words "(LINEAR_API_KEY is in your env)" and then hands it a
`linear_ops.py comment` command for its progress heartbeats. Producer and
consumer had drifted apart, and the brief was the half that lied.

WHAT THIS PINS. Every `anthropics/claude-code-action` step in every workflow
either sets `LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}` in its `env:`, or
appears in DOCUMENTED_EXCEPTIONS below with the reason written out. The
exception list is not an escape hatch the next builder can widen quietly: an
entry that no longer matches a live step fails too, so a renamed or deleted
step forces the decision to be re-made rather than inherited.

Live-extraction pattern, following tests/test_pipeline_ref_threading.py: the
assertions parse the ACTUAL workflow files, so a NEW agent step added
without the key turns this suite red on the commit that adds it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

ACTION = "anthropics/claude-code-action"

# Verbatim, like test_pipeline_ref_threading's REQUIRED_REF_EXPR: the point
# is that the value reaches the process, so a step that sets the name to
# something else (a hardcoded string, a different secret) is not compliant.
REQUIRED_ENV_EXPR = "${{ secrets.LINEAR_API_KEY }}"

# Steps that deliberately run WITHOUT Linear, keyed (workflow file, step
# name), each with the reason it is not an oversight.
DOCUMENTED_EXCEPTIONS = {
    (
        "qa-review.yml",
        "Critic review",
    ): (
        "The critic is denied Linear ON PURPOSE (DRE-2052 + DRE-2696). Card "
        "material reaches it through ONE deterministic stage — the 'Build "
        "card review context' step running scripts/review_card_context.py, "
        "which tells the critic to judge 'the card description quoted in the "
        "PR body' and, for a cardless PR, fences and size-caps the body as "
        "untrusted data. A live key would let the critic fetch card prose "
        "around that sanitizer, which is the discipline the stage exists to "
        "enforce. The Linear WORK of this workflow is done by ordinary "
        "steps that do hold the key (Plan visual QA, Post verdict). And "
        "unlike the fix and repair agents, the critic is handed no brief "
        "claiming the key is in its env: assemble_context.py maps role "
        "'critic' to standards only, briefs['critic'] is None."
    ),
    (
        "qa-review.yml",
        "Critic review (retry)",
    ): (
        "Same deliberate omission as attempt 1. The two critic blocks are "
        "kept in sync except the turn budget (DRE-2422, pinned by "
        "tests/test_critic_turn_budget.py); a retry that quietly gained a "
        "credential attempt 1 never had would be exactly that drift."
    ),
}

# The eight agent steps counted in the DRE-2696 sweep. More is fine (new
# steps are swept automatically); fewer means the extractor broke or steps
# vanished — either way a human should look, because a sweep that finds
# nothing passes vacuously.
KNOWN_AGENT_STEP_FLOOR = 8


def _on_block(doc):
    """`on:` survives yaml.safe_load as the boolean True key (YAML 1.1)."""
    return doc.get("on", doc.get(True)) or {}


def agent_steps():
    """Every claude-code-action step in the fleet: (file, name, step, doc)."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for job_id, job in (doc.get("jobs") or {}).items():
            for i, step in enumerate(job.get("steps") or []):
                uses = str(step.get("uses") or "")
                if uses.split("@")[0] == ACTION:
                    name = step.get("name") or step.get("id") or f"steps[{i}]"
                    found.append((path.name, name, step, doc))
    return found


def declares_secret(doc) -> bool:
    """The workflow declares LINEAR_API_KEY as a workflow_call secret — so
    `secrets.LINEAR_API_KEY` in a step resolves to a value instead of the
    empty string."""
    call = _on_block(doc).get("workflow_call") or {}
    return "LINEAR_API_KEY" in (call.get("secrets") or {})


class TestAgentLinearKey:
    def test_sweep_is_not_vacuous(self):
        """The extractor actually saw the fleet."""
        steps = agent_steps()
        assert len(steps) >= KNOWN_AGENT_STEP_FLOOR, (
            f"found only {len(steps)} {ACTION} steps; the DRE-2696 sweep "
            f"counted {KNOWN_AGENT_STEP_FLOOR}. A sweep that finds nothing "
            f"passes vacuously — check the glob and the `uses:` match."
        )

    def test_every_agent_step_can_reach_linear(self):
        """THE gate. RED before DRE-2696: agent-fix.yml 'Fix' and
        red-main-repair.yml 'Repair agent' set no env at all."""
        missing = []
        for wf_name, step_name, step, _doc in agent_steps():
            if (wf_name, step_name) in DOCUMENTED_EXCEPTIONS:
                continue
            value = (step.get("env") or {}).get("LINEAR_API_KEY")
            if value != REQUIRED_ENV_EXPR:
                missing.append(
                    f"{wf_name}: step {step_name!r} has "
                    f"LINEAR_API_KEY={value!r}, expected "
                    f"{REQUIRED_ENV_EXPR!r} — the agent runs linear_ops.py "
                    f"from Bash and reads the key from its environment"
                )
        assert not missing, "\n".join(
            ["DRE-2696: agent steps that cannot reach Linear:"] + missing
        )

    def test_the_secret_the_step_reads_is_actually_declared(self):
        """Threading `secrets.LINEAR_API_KEY` into a reusable workflow that
        never declared the secret yields an empty string — a credential that
        looks wired and is not."""
        for wf_name, step_name, step, doc in agent_steps():
            if (step.get("env") or {}).get("LINEAR_API_KEY") is None:
                continue
            assert declares_secret(doc), (
                f"{wf_name}: step {step_name!r} reads "
                f"secrets.LINEAR_API_KEY but the workflow declares no such "
                f"workflow_call secret — it would resolve to ''"
            )

    def test_every_exception_matches_a_live_step(self):
        """A stale exception is a failure. If a step is renamed or removed,
        the omission must be re-decided, not inherited silently."""
        live = {(wf, name) for wf, name, _s, _d in agent_steps()}
        stale = set(DOCUMENTED_EXCEPTIONS) - live
        assert not stale, (
            f"DOCUMENTED_EXCEPTIONS entries match no live agent step: "
            f"{sorted(stale)} — re-decide whether that agent needs Linear "
            f"instead of carrying a dead waiver"
        )

    def test_every_exception_states_its_reason(self):
        """The waiver has to say WHY, here and in the workflow itself. The
        defect DRE-2696 fixed was not only the missing key: nothing anywhere
        recorded which omissions were deliberate, so every reader had to
        guess."""
        for key, reason in DOCUMENTED_EXCEPTIONS.items():
            wf_name, step_name = key
            assert len(reason) > 80, f"{key}: reason is too thin to be one"
            text = (WORKFLOWS / wf_name).read_text()
            assert "DRE-2696" in text, (
                f"{wf_name} omits LINEAR_API_KEY from step {step_name!r} but "
                f"the file itself does not mention DRE-2696 — a reader of "
                f"the workflow must be able to see the omission is "
                f"deliberate without finding this test"
            )
