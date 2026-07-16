"""assemble_context.py — the per-role standards injection (DRE-1646).

The build agents run headless and cannot load Skills, so the standards/*.md
layer reaches them only because the workflows inject it. These tests pin the
two halves of that contract:

  1. The PURE mapping/assembler — which standards a role gets, in what order,
     comms-always, brief-last — tested with a stub reader (no files).
  2. The REAL files — every standard/brief the mapping names actually exists in
     the repo, so a run-time `assemble` can never reference a missing file.
  3. PROPAGATION — assemble() reflects the live file contents, so a `@main`
     edit to a standard changes the assembled context with no code change.

The workflow-wiring half (each agent reads agent-context.md, each workflow has
an Assemble step) lives in test_assemble_context_wiring.py.
"""

import os
import unittest

import sys

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS)
import assemble_context as ac  # noqa: E402

REPO = os.path.join(os.path.dirname(__file__), "..")


class MappingTest(unittest.TestCase):
    def test_comms_is_first_for_every_role(self):
        for role in ac.ROLE_STANDARDS:
            self.assertEqual(
                ac.standards_for(role)[0],
                "comms.md",
                f"{role} must read comms.md first",
            )

    def test_untrusted_content_is_second_for_every_role(self):
        # Every agent reads card/comment/PR text, so every role gets the
        # untrusted-content standard (DRE-1989), right after comms.
        for role in ac.ROLE_STANDARDS:
            self.assertEqual(
                ac.standards_for(role)[1],
                "untrusted-content.md",
                f"{role} must read untrusted-content.md second",
            )

    def test_card_spec_per_role_mapping(self):
        # The exact per-role set from DRE-1646 (comms + untrusted-content are
        # added to all; the lists below are the role-specific additions,
        # order-significant). vendor-boundaries.md (DRE-2105) goes to every
        # role that plans, builds, or reviews work touching an external
        # trigger/event/command — planner/engineer/frontend/devops/critic.
        # console-honesty.md (DRE-2107) goes to the roles that build or
        # review console surfaces rendering pipeline state —
        # engineer/frontend/critic.
        expected = {
            "engineer": ["comms.md", "untrusted-content.md", "engineering.md", "architecture.md", "card-quality.md", "vendor-boundaries.md", "console-honesty.md"],
            "frontend": ["comms.md", "untrusted-content.md", "engineering.md", "architecture.md", "card-quality.md", "design.md", "vendor-boundaries.md", "console-honesty.md"],
            "devops": ["comms.md", "untrusted-content.md", "engineering.md", "architecture.md", "card-quality.md", "vendor-boundaries.md"],
            "planner": ["comms.md", "untrusted-content.md", "card-quality.md", "engineering.md", "vendor-boundaries.md"],
            "critic": ["comms.md", "untrusted-content.md", "engineering.md", "architecture.md", "vendor-boundaries.md", "console-honesty.md"],
            "verifier": ["comms.md", "untrusted-content.md", "design.md"],
            "fix": ["comms.md", "untrusted-content.md", "engineering.md"],
            "medic": ["comms.md", "untrusted-content.md", "engineering.md"],
        }
        self.assertEqual(set(expected), set(ac.ROLE_STANDARDS))
        for role, want in expected.items():
            self.assertEqual(ac.standards_for(role), want, f"role {role}")

    def test_vendor_boundaries_reaches_the_boundary_roles_only(self):
        # DRE-2105: the vendor-behavior premortem checklist must reach every
        # role that authors or gates boundary-touching work. verifier/fix/
        # medic run INSIDE an already-designed flow and don't design new
        # vendor interactions — keeping their context lean is deliberate.
        for role in ("planner", "engineer", "frontend", "devops", "critic"):
            self.assertIn(
                "vendor-boundaries.md", ac.standards_for(role),
                f"{role} must receive the vendor-boundaries standard",
            )
        for role in ("verifier", "fix", "medic"):
            self.assertNotIn(
                "vendor-boundaries.md", ac.standards_for(role),
                f"{role} must not carry the vendor-boundaries standard",
            )

    def test_console_honesty_reaches_the_console_roles_only(self):
        # DRE-2107: badges derive from what actually happened — the standard
        # must reach every role that builds or reviews console surfaces
        # rendering pipeline state. devops/planner/verifier/fix/medic don't
        # author console state elements — keeping their context lean is
        # deliberate.
        for role in ("engineer", "frontend", "critic"):
            self.assertIn(
                "console-honesty.md", ac.standards_for(role),
                f"{role} must receive the console-honesty standard",
            )
        for role in ("devops", "planner", "verifier", "fix", "medic"):
            self.assertNotIn(
                "console-honesty.md", ac.standards_for(role),
                f"{role} must not carry the console-honesty standard",
            )

    def test_frontend_alone_gets_design(self):
        # design.md is the frontend/verifier signal — engineer/devops must not
        # carry it (it would be noise for backend/infra work).
        self.assertIn("design.md", ac.standards_for("frontend"))
        self.assertNotIn("design.md", ac.standards_for("engineer"))
        self.assertNotIn("design.md", ac.standards_for("devops"))

    def test_unknown_role_raises(self):
        with self.assertRaises(KeyError):
            ac.standards_for("nope")

    def test_context_paths_brief_last_and_only_when_present(self):
        # engineer has a brief → it is the LAST path; critic has none → no brief.
        eng = ac.context_paths("engineer", root="R")
        self.assertTrue(eng[-1].endswith(os.path.join("briefs", "engineer.md")))
        self.assertTrue(all(os.sep + "standards" + os.sep in p for p in eng[:-1]))
        critic = ac.context_paths("critic", root="R")
        self.assertFalse(any("briefs" in p for p in critic))


class AssembleTest(unittest.TestCase):
    def test_assemble_is_ordered_and_includes_all_sections(self):
        seen = []

        def stub(path):
            seen.append(path)
            return f"BODY OF {os.path.basename(path)}"

        blob = ac.assemble("engineer", stub)
        # comms first, brief last, in the mapping order.
        expected = [
            "comms.md", "untrusted-content.md", "engineering.md",
            "architecture.md", "card-quality.md", "vendor-boundaries.md",
            "console-honesty.md", "engineer.md",
        ]
        self.assertEqual([os.path.basename(p) for p in seen], expected)
        for name in expected:
            self.assertIn(f"BODY OF {name}", blob)
        # Sections are fenced + labeled so the agent can tell them apart.
        self.assertIn("===== BEGIN standards/comms.md =====", blob)
        self.assertIn("===== BEGIN briefs/engineer.md =====", blob)

    def test_assemble_reflects_live_contents(self):
        # PROPAGATION: assemble() reads through the reader, so changing what a
        # standard returns changes the blob — proving a `@main` standards edit
        # propagates to the assembled context with no code change.
        def stub(path):
            if path.endswith("comms.md"):
                return "SENTINEL-PROPAGATION-LINE"
            return "x"

        blob = ac.assemble("critic", stub)
        self.assertIn("SENTINEL-PROPAGATION-LINE", blob)


class RealFilesTest(unittest.TestCase):
    """Every file the mapping names must exist in the repo for the run-time
    `assemble` to read — a typo'd standard name would otherwise 404 only in CI."""

    def test_every_standard_file_exists(self):
        for role in ac.ROLE_STANDARDS:
            for path in ac.context_paths(role, root=REPO):
                self.assertTrue(
                    os.path.isfile(path), f"{role} references missing file {path}"
                )

    def test_assemble_against_real_repo_includes_standards_and_brief(self):
        # End-to-end over the real files: the frontend blob carries the design
        # standard's heading AND the frontend brief's heading.
        paths = ac.context_paths("frontend", root=REPO)
        by_label = {"/".join(p.split(os.sep)[-2:]): p for p in paths}

        def read(rel):
            label = "/".join(rel.split(os.sep)[-2:])
            with open(by_label[label], encoding="utf-8") as f:
                return f.read()

        blob = ac.assemble("frontend", read)
        self.assertIn("Design standard", blob)  # standards/design.md H1
        self.assertIn("Frontend", blob)          # briefs/frontend.md content

    def test_brief_paths_match_agents_yaml(self):
        # The helper's brief map must agree with agents.yaml's briefPath, the
        # console's source of truth — drift would point an agent at the wrong brief.
        import yaml

        reg = yaml.safe_load(open(os.path.join(REPO, "agents.yaml")))
        by_name = {a["name"]: a for a in reg["agents"]}
        # agents.yaml names the fixer "fixer"; the helper role key is "fix".
        alias = {"fix": "fixer"}
        for role, brief in ac.ROLE_BRIEF.items():
            entry = by_name.get(alias.get(role, role))
            if entry is None:
                continue  # roles without an agents.yaml entry are fine
            yaml_brief = entry.get("briefPath")
            if brief is None:
                self.assertIn(yaml_brief, (None, "null"), f"{role} brief mismatch")
            else:
                self.assertTrue(
                    (yaml_brief or "").endswith(brief),
                    f"{role}: helper brief {brief!r} vs agents.yaml {yaml_brief!r}",
                )


if __name__ == "__main__":
    unittest.main()
