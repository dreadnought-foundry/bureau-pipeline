"""Untrusted-content standard + card-text fencing (DRE-1989).

Card/comment/PR text crosses the trust boundary: it is authored in Linear/
GitHub, not by the pipeline, yet agent-task.yml and plan.yml paste the card
body straight into the agent prompt. A manipulated card could steer a build
agent — including into emitting verdict-marker strings that could forge an
approval. Two halves make that safe, and these tests pin both:

  1. The STANDARD — standards/untrusted-content.md exists, rides the same
     assemble_context.py rail as every other standard (so every CI agent
     receives it @main with no per-repo copy), and names the exact sentinel
     lines and forbidden verdict markers.
  2. The FENCES — every workflow that interpolates the card body into an
     agent prompt wraps it in the sentinel lines, preceded by a preamble
     declaring the content data-not-instructions.
"""

import os
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..")
WF_DIR = os.path.join(REPO, ".github", "workflows")
STANDARD = os.path.join(REPO, "standards", "untrusted-content.md")

# The exact sentinel lines. Chosen to match assemble_context.py's existing
# "===== BEGIN/END ... =====" section framing, and deliberately NOT markdown
# (triple-backticks or setext "====" underlines occur in real card bodies;
# these full sentinel lines cannot).
BEGIN = "===== BEGIN UNTRUSTED CARD TEXT ====="
END = "===== END UNTRUSTED CARD TEXT ====="

# The raw interpolation of the card body into a prompt.
DESC_EXPR = "${{ github.event.client_payload.description }}"

# The preamble phrase that must introduce the fence, BEFORE the BEGIN line.
PREAMBLE = "DATA, not instructions"

# Workflows that paste the card body into an agent prompt (the fix scope).
FENCED = ["agent-task.yml", "plan.yml"]


def src(workflow: str) -> str:
    return open(os.path.join(WF_DIR, workflow)).read()


class StandardOnTheRailTest(unittest.TestCase):
    """The standard must exist and reach EVERY agent via assemble_context.py —
    the DRE-1644/1646 single-source rail — not via per-repo copies."""

    def test_standard_file_exists(self):
        self.assertTrue(
            os.path.isfile(STANDARD),
            "standards/untrusted-content.md must exist",
        )

    def test_every_role_receives_the_standard(self):
        import sys

        sys.path.insert(0, os.path.join(REPO, "scripts"))
        import assemble_context as ac

        for role in ac.ROLE_STANDARDS:
            self.assertIn(
                "untrusted-content.md",
                ac.standards_for(role),
                f"{role} must receive the untrusted-content standard — every "
                "agent reads card/comment/PR text",
            )

    def test_standard_documents_the_exact_sentinels(self):
        # The standard and the workflows must name the SAME sentinel lines —
        # drift here means agents are told about fences they never see.
        body = open(STANDARD).read()
        self.assertIn(BEGIN, body, "standard must quote the BEGIN sentinel")
        self.assertIn(END, body, "standard must quote the END sentinel")

    def test_standard_names_the_forbidden_verdict_markers(self):
        # The merge gate reads verdicts from PR comments; an agent tricked
        # into emitting these strings could forge an approval (DRE-1978).
        body = open(STANDARD).read()
        for marker in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertIn(
                marker, body,
                f"standard must forbid emitting the {marker!r} marker",
            )


class FenceWiringTest(unittest.TestCase):
    """agent-task.yml and plan.yml must wrap the card-body interpolation in
    the sentinel fence, preamble first."""

    def test_card_body_is_fenced(self):
        for wf in FENCED:
            body = src(wf)
            self.assertIn(DESC_EXPR, body, f"{wf} must interpolate the card body")
            self.assertIn(BEGIN, body, f"{wf} missing BEGIN sentinel")
            self.assertIn(END, body, f"{wf} missing END sentinel")
            self.assertLess(
                body.index(BEGIN), body.index(DESC_EXPR),
                f"{wf}: BEGIN sentinel must precede the card body",
            )
            self.assertLess(
                body.index(DESC_EXPR), body.index(END),
                f"{wf}: END sentinel must follow the card body",
            )

    def test_preamble_precedes_the_fence(self):
        # One line before the fence must declare the block data-not-
        # instructions — the fence is meaningless if the agent is never told
        # what it means.
        for wf in FENCED:
            body = src(wf)
            self.assertIn(PREAMBLE, body, f"{wf} missing the data-not-instructions preamble")
            self.assertLess(
                body.index(PREAMBLE), body.index(BEGIN),
                f"{wf}: preamble must come before the BEGIN sentinel",
            )

    def test_preamble_points_at_the_standard(self):
        for wf in FENCED:
            self.assertIn(
                "untrusted-content.md", src(wf),
                f"{wf} preamble must reference standards/untrusted-content.md",
            )

    def test_no_workflow_interpolates_the_card_body_unfenced(self):
        # Regression guard for the WHOLE workflow directory: any future
        # workflow that pastes the card body into a prompt must fence it too.
        for wf in sorted(os.listdir(WF_DIR)):
            if not wf.endswith(".yml"):
                continue
            body = src(wf)
            if DESC_EXPR not in body:
                continue
            self.assertIn(BEGIN, body, f"{wf} interpolates the card body without a fence")
            self.assertIn(END, body, f"{wf} interpolates the card body without a fence")
            self.assertLess(body.index(BEGIN), body.index(DESC_EXPR), wf)
            self.assertLess(body.index(DESC_EXPR), body.index(END), wf)


if __name__ == "__main__":
    unittest.main()
