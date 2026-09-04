"""Standards-injection workflow wiring (DRE-1646).

Unit tests on assemble_context.py can't catch a workflow that forgot to run the
Assemble step or an agent prompt that doesn't point at the assembled context.
These pin the YAML: every agent-bearing workflow (a) runs an Assemble step that
calls assemble_context.py, and (b) tells its agent to read the assembled
agent-context.md. That is the whole injection contract — break either half and
the standards never reach the agent.
"""

import os
import re
import unittest

import yaml

WF_DIR = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows")

ACTION = "anthropics/claude-code-action"


def src(workflow: str) -> str:
    return open(os.path.join(WF_DIR, workflow)).read()


# (workflow, role-arg regex passed to assemble_context.py). agent-task selects
# the role at runtime (engineer/frontend/devops) via the shell var "$ROLE"; the
# rest pass a literal role name.
WIRED = [
    ("agent-task.yml", r'"\$ROLE"'),
    ("plan.yml", "planner"),
    ("qa-review.yml", "critic"),
    ("verify.yml", "verifier"),
    ("agent-fix.yml", "fix"),
    ("medic.yml", "medic"),
]


class InjectionWiringTest(unittest.TestCase):
    def test_each_workflow_runs_assemble_step(self):
        for wf, role in WIRED:
            body = src(wf)
            self.assertIn(
                "assemble_context.py assemble", body,
                f"{wf} must call assemble_context.py to inject standards",
            )
            self.assertRegex(
                body,
                r"assemble_context\.py assemble " + role,
                f"{wf} must assemble the {role!r} role's context",
            )

    def test_assembled_context_written_to_pipeline_path(self):
        # Written under the git-excluded .bureau-pipeline checkout so an agent
        # `git add -A` can never commit it into the product repo.
        for wf, _ in WIRED:
            self.assertIn(
                ".bureau-pipeline/agent-context.md", src(wf),
                f"{wf} must write the context to .bureau-pipeline/agent-context.md",
            )

    def test_each_agent_prompt_reads_assembled_context(self):
        # The agent must be pointed at the assembled blob — otherwise the
        # Assemble step is dead weight and the standards never reach the model.
        for wf, _ in WIRED:
            self.assertIn(
                ".bureau-pipeline/agent-context.md", src(wf),
                f"{wf} agent prompt must read agent-context.md",
            )

    def test_assemble_runs_before_the_agent_step(self):
        # The blob must exist before the claude-code-action step that reads it.
        #
        # "That reads it" is load-bearing, and it used to be read as "the first
        # one in the file" (DRE-3074). plan.yml's first claude-code-action step
        # is now the card classifier: one bounded turn, no tools, a prompt
        # composed in Python that names no context blob and could not open one
        # if it did. Ordering the Assemble step before THAT would mean
        # assembling a planner's context for a card the run has not yet decided
        # is an epic. So the pin follows the dependency instead of the line
        # number — the first agent step whose prompt actually names the blob.
        for wf, _ in WIRED:
            doc = yaml.safe_load(src(wf))
            readers = 0
            for job_id, job in (doc.get("jobs") or {}).items():
                steps = job.get("steps") or []
                assembles = [
                    i for i, s in enumerate(steps)
                    if "assemble_context.py assemble" in str(s.get("run") or "")
                ]
                for i, step in enumerate(steps):
                    uses = str(step.get("uses") or "")
                    prompt = str((step.get("with") or {}).get("prompt") or "")
                    if not uses.startswith(ACTION) or "agent-context.md" not in prompt:
                        continue
                    readers += 1
                    self.assertTrue(
                        [at for at in assembles if at < i],
                        f"{wf}: job {job_id!r} step {step.get('name')!r} reads "
                        "agent-context.md with no Assemble step before it",
                    )
            self.assertTrue(
                readers,
                f"{wf}: no claude-code-action step points its agent at "
                "agent-context.md — the Assemble step is dead weight",
            )

    def test_agent_task_assemble_guarded_against_bounce(self):
        # A bounced card runs no agent — the Assemble step must skip too.
        body = src("agent-task.yml")
        m = re.search(r"name:\s*Assemble agent context(.*?)(?:\n      - name:|\Z)", body, re.S)
        self.assertIsNotNone(m, "Assemble agent context step not found")
        self.assertIn("steps.gate.outputs.bounced", m.group(1))


if __name__ == "__main__":
    unittest.main()
