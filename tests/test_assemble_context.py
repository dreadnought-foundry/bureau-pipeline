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
        # engineer/frontend/critic. plan-artifact.md (DRE-2720) goes to the
        # roles that handle the epic's CEO-facing artifact — the planner
        # writes it, the code critic judges one, and both plan critics read
        # the artifact the CEO green-lights (DRE-2721) — and to nobody else.
        # verdict-evidence.md (DRE-3005) goes to the critic
        # alone, for the mirror reason: it is the only role that writes a
        # verdict, and a verdict asserting what a command did carries the run.
        # design-system.md (DRE-3938) goes to the two roles that produce and
        # gate design work — the frontend build agent and the critic whose
        # checklist says whether a design is really done. whats-new.md
        # (DRE-5510) goes, last, to every role that writes or judges a pull
        # request body — the four build roles, the critic, and the fix agent
        # that adds a missing line when the critic sends a pull request back.
        expected = {
            "engineer": ["comms.md", "untrusted-content.md", "engineering.md", "architecture.md", "card-quality.md", "vendor-boundaries.md", "console-honesty.md", "whats-new.md"],
            "frontend": ["comms.md", "untrusted-content.md", "engineering.md", "architecture.md", "card-quality.md", "design.md", "design-system.md", "vendor-boundaries.md", "console-honesty.md", "whats-new.md"],
            "devops": ["comms.md", "untrusted-content.md", "engineering.md", "architecture.md", "card-quality.md", "vendor-boundaries.md", "whats-new.md"],
            "database-architect": ["comms.md", "untrusted-content.md", "engineering.md", "architecture.md", "card-quality.md", "vendor-boundaries.md", "whats-new.md"],
            "planner": ["comms.md", "untrusted-content.md", "card-quality.md", "engineering.md", "vendor-boundaries.md", "design-parity.md", "plan-artifact.md"],
            "critic": ["comms.md", "untrusted-content.md", "engineering.md", "architecture.md", "vendor-boundaries.md", "console-honesty.md", "design-parity.md", "design-system.md", "plan-artifact.md", "verdict-evidence.md", "whats-new.md"],
            "verifier": ["comms.md", "untrusted-content.md", "design.md", "design-parity.md"],
            # The two plan critics (DRE-2721). Different questions, so
            # different context: the pre stage judges the SHAPE of a plan that
            # is still moving and is deliberately NOT given the engineering
            # floor or the system shape; the post stage reads the frozen text
            # as an agent would and gets everything the build roles are held
            # to, plus the vendor premortem that catches a false claim.
            "plan-critic-pre": ["comms.md", "untrusted-content.md", "card-quality.md", "design-parity.md", "plan-artifact.md", "plan-critic.md"],
            "plan-critic-post": ["comms.md", "untrusted-content.md", "card-quality.md", "engineering.md", "architecture.md", "vendor-boundaries.md", "plan-artifact.md", "plan-critic.md"],
            "fix": ["comms.md", "untrusted-content.md", "engineering.md", "whats-new.md"],
            "medic": ["comms.md", "untrusted-content.md", "engineering.md"],
            # The proof runner (DRE-5921) writes a record against a proof
            # card's criteria (card-quality) to the engineering floor.
            "proof": ["comms.md", "untrusted-content.md", "card-quality.md", "engineering.md"],
        }
        self.assertEqual(set(expected), set(ac.ROLE_STANDARDS))
        for role, want in expected.items():
            self.assertEqual(ac.standards_for(role), want, f"role {role}")

    def test_vendor_boundaries_reaches_the_boundary_roles_only(self):
        # DRE-2105: the vendor-behavior premortem checklist must reach every
        # role that authors or gates boundary-touching work. verifier/fix/
        # medic run INSIDE an already-designed flow and don't design new
        # vendor interactions — keeping their context lean is deliberate.
        for role in ("planner", "engineer", "frontend", "devops", "critic", "database-architect"):
            self.assertIn(
                "vendor-boundaries.md", ac.standards_for(role),
                f"{role} must receive the vendor-boundaries standard",
            )
        for role in ("verifier", "fix", "medic"):
            self.assertNotIn(
                "vendor-boundaries.md", ac.standards_for(role),
                f"{role} must not carry the vendor-boundaries standard",
            )

    def test_whats_new_reaches_the_pr_body_roles_only(self):
        # DRE-5510: the What's New line lives in the pull request body, so the
        # standard must reach every role that writes one or judges one. The
        # fix agent reads no brief, so this rail entry is how it learns the
        # grammar it writes when the critic sends a pull request back. The
        # planner, verifier, medic and both plan critics never write or judge
        # a pull request body — keeping their context lean is deliberate.
        # Appended LAST so no existing entry's position moves.
        for role in ("engineer", "frontend", "devops", "database-architect",
                     "critic", "fix"):
            self.assertEqual(
                ac.standards_for(role)[-1], "whats-new.md",
                f"{role} must receive the whats-new standard as its last entry",
            )
        for role in ("planner", "verifier", "medic", "plan-critic-pre",
                     "plan-critic-post"):
            self.assertNotIn(
                "whats-new.md", ac.standards_for(role),
                f"{role} must not carry the whats-new standard",
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
        for role in ("devops", "planner", "verifier", "fix", "medic", "database-architect"):
            self.assertNotIn(
                "console-honesty.md", ac.standards_for(role),
                f"{role} must not carry the console-honesty standard",
            )

    def test_design_system_reaches_the_design_roles_only(self):
        # DRE-3938: one `design/` standard and one design critic, fleet-wide.
        # It must reach the role that PRODUCES design work and the role whose
        # checklist gates it done. engineer/devops/database-architect author
        # backend/infra, the planner's design obligation is design-parity.md,
        # and the fixer/medic work inside an already-reviewed diff — keeping
        # their context lean is deliberate. (The verifier is left out on
        # purpose: it proves a shipped surface against the design ref, which is
        # design.md + design-parity.md, not the folder-and-lock contract.)
        for role in ("frontend", "critic"):
            self.assertIn(
                "design-system.md", ac.standards_for(role),
                f"{role} must receive the design-system standard",
            )
        for role in ("engineer", "devops", "planner", "verifier", "fix",
                     "medic", "database-architect"):
            self.assertNotIn(
                "design-system.md", ac.standards_for(role),
                f"{role} must not carry the design-system standard",
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
        # engineer has a brief → it is the LAST path; the fixer has none → no
        # brief. (The critic gained one in DRE-3084; the brief-less roles are
        # read off the map rather than remembered, so this stays true for
        # whichever they are.)
        eng = ac.context_paths("engineer", root="R")
        self.assertTrue(eng[-1].endswith(os.path.join("briefs", "engineer.md")))
        self.assertTrue(all(os.sep + "standards" + os.sep in p for p in eng[:-1]))
        briefless = [r for r, b in ac.ROLE_BRIEF.items() if b is None]
        self.assertTrue(briefless, "no brief-less role left to prove the branch")
        for role in briefless:
            paths = ac.context_paths(role, root="R")
            self.assertFalse(any("briefs" in p for p in paths), role)


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
            "console-honesty.md", "whats-new.md", "engineer.md",
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

    def test_every_rostered_agent_with_a_brief_is_wired(self):
        # The REVERSE of the check above, and the hole database-architect fell
        # through: that test walks ROLE_BRIEF -> agents.yaml, so an agent
        # rostered in agents.yaml with a briefPath but MISSING from the helper
        # maps was invisible to it. assemble_context would then raise KeyError
        # the moment a route sent that role through -- a latent break that only
        # surfaces when someone wires the dispatch. Walk roster -> helper too.
        import yaml

        reg = yaml.safe_load(open(os.path.join(REPO, "agents.yaml")))
        alias = {"fixer": "fix"}
        for entry in reg["agents"]:
            if not entry.get("briefPath"):
                continue  # brief-less roles (critic/medic) are standards-only
            role = alias.get(entry["name"], entry["name"])
            self.assertIn(
                role, ac.ROLE_BRIEF,
                f"agents.yaml rosters {entry['name']!r} with a brief but "
                f"ROLE_BRIEF has no {role!r} key -- assemble_context would KeyError",
            )
            self.assertIn(
                role, ac.ROLE_STANDARDS,
                f"agents.yaml rosters {entry['name']!r} but ROLE_STANDARDS has "
                f"no {role!r} key -- standards_for() would KeyError",
            )

    def test_cli_assemble_engineer_carries_the_whats_new_standard(self):
        # DRE-5510, end to end through the CLI the workflows call: the
        # engineer's assembled context fences the What's New standard in.
        import subprocess

        run = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "assemble_context.py"),
             "assemble", "engineer", "--root", REPO],
            capture_output=True, text=True, check=True,
        )
        self.assertIn("===== BEGIN standards/whats-new.md =====", run.stdout)
        self.assertIn("What's New standard", run.stdout)


def _read_repo(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


class ProofRoleTest(unittest.TestCase):
    """DRE-5921: the proof runner is a role the assembler knows, so the
    workflow that dispatches it can `assemble proof` and get its brief last."""

    def _cli(self, *args):
        import subprocess

        return subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "assemble_context.py"), *args],
            capture_output=True, text=True, check=True,
        ).stdout

    def test_roles_lists_proof(self):
        self.assertIn("proof", self._cli("roles").split())

    def test_standards_are_comms_untrusted_card_quality_engineering(self):
        self.assertEqual(
            ac.standards_for("proof"),
            ["comms.md", "untrusted-content.md", "card-quality.md", "engineering.md"],
        )

    def test_context_paths_end_with_the_proof_brief(self):
        paths = ac.context_paths("proof", root="R")
        self.assertEqual(paths[-1], os.path.join("R", "briefs", "proof.md"))

    def test_cli_assemble_proof_ends_in_the_brief(self):
        blob = self._cli("assemble", "proof", "--root", REPO)
        sections = [line for line in blob.splitlines()
                    if line.startswith("===== BEGIN ")]
        self.assertEqual(sections[-1], "===== BEGIN briefs/proof.md =====")
        self.assertTrue(blob.rstrip("\n").endswith("===== END briefs/proof.md ====="))
        self.assertIn("# Proof runner", blob)


class ProofBriefTest(unittest.TestCase):
    """DRE-5921: briefs/proof.md carries the contract its siblings build to —
    the workflow (proof-task.yml), the sweep that dispatches it, the run-state
    reader and the return of the CEO's answer read these exact strings."""

    def setUp(self):
        self.text = _read_repo("briefs", "proof.md")
        self.flat = " ".join(self.text.split())

    def test_the_identities_and_what_each_may_do(self):
        for name in ("GH_READ_TOKEN", "GH_TOKEN", "LINEAR_API_KEY"):
            self.assertIn(f"`{name}`", self.text, name)
        self.assertIn("GH_TOKEN=$GH_READ_TOKEN gh api", self.text)
        self.assertIn("aws: none", self.text)
        self.assertIn("## Identities", self.text)
        self.assertIn("no card yet", self.flat)
        self.assertIn("a dispatched run that signs in is outside this epic and "
                      "has no card yet", self.flat.lower())

    def test_the_branch_title_and_first_line(self):
        for needle in ("agent/DRE-<n>-proof-record", "PROOF record: <card title>",
                       "Proof record for DRE-<n>"):
            self.assertIn(needle, self.text, needle)

    def test_the_result_vocabulary(self):
        for needle in ("`Met.`", "`Not met.`", "`Not observed.`",
                       "Not observed. needs the CEO's press: <the press>",
                       'Met. by the CEO\'s press, his words at <PT>: "',
                       "Dropped by the CEO at <PT>",
                       "Not observed. needs a browser on a local run of the "
                       "released commit",
                       "The CEO closes this card after reading the record | "
                       "Open: the CEO's step",
                       "**Status: PASS | PARTIAL | FAIL.**",
                       "| Criterion | Result |"):
            self.assertIn(needle, self.text, needle)

    def test_the_escalation_resume_and_answer(self):
        for needle in ("/tmp/agent-escalation.txt", "spoken_thread.py",
                       "re-run after the CEO's answer", "second dispatch",
                       "merges the default branch", "any dispatch"):
            self.assertIn(needle, self.flat, needle)

    def test_the_heartbeats(self):
        for line in ("⏳ 1/5 read", "⏳ 2/5 observing", "⏳ 3/5 record written",
                     "⏳ 4/5 local checks", "⏳ 5/5 PR opened"):
            self.assertIn(line, self.text, line)

    def test_it_emits_no_verdict_marker(self):
        # The brief names the rule; it never spells a marker an agent could
        # copy into a record (standards/untrusted-content.md).
        for marker in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(marker, self.text, marker)


def _bullets(text):
    """Split markdown into top-level blocks: each `- ` bullet with its
    continuation lines, and each plain paragraph."""
    blocks, cur = [], []
    for line in text.splitlines():
        if line.startswith("- ") or not line.strip() or line.startswith("#"):
            if cur:
                blocks.append(" ".join(cur))
            cur = [line.strip()] if line.strip() and not line.startswith("#") else []
        else:
            cur.append(line.strip())
    if cur:
        blocks.append(" ".join(cur))
    return blocks


class WhatsNewBriefsTest(unittest.TestCase):
    """DRE-5510: the two briefs that carry a pull-request-body recipe name the
    `What's new:` line in it, point at the standard rather than restating its
    grammar, and tell the builder how a held pull request is answered."""

    BRIEFS = ("engineer.md", "devops.md")

    def _recipe(self, brief):
        hits = [b for b in _bullets(_read_repo("briefs", brief))
                if b.startswith("- **One PR per card**")]
        self.assertEqual(len(hits), 1, f"{brief}: one 'One PR per card' bullet")
        return hits[0]

    def test_the_pr_body_recipe_names_the_line_and_points_at_the_standard(self):
        for brief in self.BRIEFS:
            recipe = self._recipe(brief)
            self.assertIn("What's new:", recipe, brief)
            self.assertIn("standards/whats-new.md", recipe, brief)

    def test_the_briefs_do_not_restate_the_grammar(self):
        # The standards README rule: state a rule once, have the briefs point
        # to it. The kinds and audiences are the grammar's vocabulary.
        for brief in self.BRIEFS:
            text = _read_repo("briefs", brief)
            for word in ("<kind>", "<audience>", "moderators", "admins",
                         "`improved`", "(open:"):
                self.assertNotIn(word, text, f"{brief} restates {word!r}")

    def test_a_held_pull_request_is_answered_in_the_body_plus_an_empty_commit(self):
        # Operator pre-review, 2026-10-01: a merge-gate hold or a critic
        # finding about the line is answered in the pull request body, then
        # one empty commit on the same branch — never a diff change — and the
        # brief points at the standard for why (the critic reviews only a new
        # head).
        for brief in self.BRIEFS:
            blocks = [b for b in _bullets(_read_repo("briefs", brief))
                      if "gh pr edit" in b]
            self.assertEqual(len(blocks), 1, f"{brief}: one held-PR answer")
            block = blocks[0]
            for needle in ("What's new:", "hold", "critic", "--body-file",
                           "empty commit", "same branch", "never",
                           "standards/whats-new.md"):
                self.assertIn(needle, block, f"{brief}: held-PR answer lacks {needle!r}")


class WhatsNewReadmeTest(unittest.TestCase):
    """DRE-5510: standards/README.md says who receives what, and that table
    must agree with ROLE_STANDARDS — the What's New rows included."""

    def _per_role_table(self):
        rows = {}
        text = _read_repo("standards", "README.md")
        section = text.split("The per-role mapping:", 1)[1].split("\n## ", 1)[0]
        for line in section.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != 2 or cells[0] in ("Role", "---"):
                continue
            for role in cells[0].split(" / "):
                rows[role.strip()] = [s.strip() + ".md"
                                      for s in cells[1].split(",")]
        return rows

    def test_the_per_role_table_matches_role_standards(self):
        self.assertEqual(self._per_role_table(), ac.ROLE_STANDARDS)

    def test_the_standards_table_lists_whats_new_as_injected(self):
        rows = [line for line in _read_repo("standards", "README.md").splitlines()
                if line.startswith("| `whats-new.md` |")]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("not injected", rows[0])


if __name__ == "__main__":
    unittest.main()
