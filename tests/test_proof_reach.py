"""What a proof run can read, declared once (DRE-6605).

Nine proofs stood open on 2026-10-10 because a criterion asked for a reading
the proof identity could not make, and the reach was written in four places
none of which was checked against the others. `config/proof-reach.json`
declares it once, as data, and `scripts/proof_reach.py` is its one reader: the
plan critic and the record reader ask `out_of_reach` whether a criterion names
a reading the run cannot make, and `check` holds the proof workflow's two
mints and `briefs/proof.md` to the declaration.

Every `check` test runs on a throwaway copy of the files it edits, so the
workflow and the brief on disk are never touched. The reader is a LEAF: it
imports nothing that imports `reconcile`, and one test runs it with `REPO`
unset.
"""

from __future__ import annotations

import ast
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import proof_reach  # noqa: E402

SCRIPT = ROOT / "scripts" / "proof_reach.py"
DECLARATION = ROOT / "config" / "proof-reach.json"
WORKFLOW = ".github/workflows/proof-task.yml"
BRIEF = "briefs/proof.md"
WIDE = "Mint the read-only proof identity"
NARROW = "Mint the read-only proof identity without variables"
CAUSES = ("out-of-reach", "state-gone", "misdescribed", "operator-step-unblocked")
REPO_MAP = json.loads((ROOT / "config" / "repo-map.json").read_text())


def _doc() -> dict:
    return json.loads(DECLARATION.read_text())


def _reading(doc: dict, rid: str) -> dict:
    return next(r for r in doc["readings"] if r["id"] == rid)


class _Copy:
    """A throwaway root holding copies of every file `check` reads."""

    FILES = ("config/proof-reach.json", "config/repo-map.json", WORKFLOW, BRIEF)

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        doc = _doc()
        paths = list(self.FILES) + [e["printed_by"] for e in doc["log_lines"]]
        for rel in paths:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / rel, root / rel)
        return root

    def __exit__(self, *exc) -> None:
        self._tmp.cleanup()


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, (path, old)
    path.write_text(text.replace(old, new, 1))


class DeclarationTest(unittest.TestCase):

    def test_check_is_empty_on_main(self):
        self.assertEqual([], proof_reach.check(ROOT))

    def test_the_cli_exits_zero_on_main(self):
        done = subprocess.run([sys.executable, str(SCRIPT), "check"], cwd=ROOT,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_the_cli_exits_one_on_a_problem(self):
        with _Copy() as root:
            _edit(root / WORKFLOW, "          permission-actions: read\n", "")
            done = subprocess.run([sys.executable, str(SCRIPT), "check", "--root", str(root)],
                                  capture_output=True, text=True, timeout=60)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("permission-actions", done.stdout)

    def test_the_four_keys_in_order(self):
        self.assertEqual(["github", "readings", "log_lines", "causes"], list(_doc()))
        self.assertEqual(proof_reach.DECLARATION, "config/proof-reach.json")
        self.assertEqual(_doc(), proof_reach.load())

    def test_the_github_block(self):
        github = _doc()["github"]
        self.assertEqual(WORKFLOW, github["workflow"])
        self.assertEqual({"wide": WIDE, "narrow": NARROW}, github["mints"])
        self.assertEqual("${{ github.repository_owner }}", github["owner"])
        self.assertNotIn("repositories", github)
        self.assertEqual({"contents": "read", "actions": "read",
                          "pull-requests": "read", "metadata": "read"},
                         github["permissions"])
        self.assertEqual({"variables": "read"}, github["optional_permissions"])

    def test_the_readings_carry_the_fields_and_held_values(self):
        doc = _doc()
        held = {r["id"]: r["held"] for r in doc["readings"]}
        self.assertEqual({"repo-variable": "optional", "db-read": False,
                          "step-summary": False, "other-organization": False,
                          "local-screen": True, "product-identity": "optional"},
                         {k: held[k] for k in proof_reach.REQUIRED_READINGS})
        self.assertEqual([], _reading(doc, "repo-variable")["held_for"])
        self.assertEqual(["portico"], _reading(doc, "product-identity")["held_for"])
        self.assertEqual({"portico": "infra/scripts/proof-reader.ts"},
                         _reading(doc, "product-identity")["queries_declared_by"])
        for r in doc["readings"]:
            for field in ("id", "held", "what", "names", "why", "brief"):
                self.assertIn(field, r, r["id"])
        self.assertIn("DRE-6543", _reading(doc, "repo-variable")["why"])
        self.assertIn("DRE-6478", _reading(doc, "repo-variable")["why"])
        self.assertIn("DRE-6431", _reading(doc, "other-organization")["why"])
        self.assertIn("DRE-6442", _reading(doc, "other-organization")["why"])
        self.assertEqual(doc["readings"], proof_reach.readings())

    def test_the_proof_reader_phrase_is_not_a_name(self):
        for r in _doc()["readings"]:
            for name in r["names"]:
                self.assertNotRegex("as the proof-reader identity", f"(?i){name}", r["id"])

    def test_the_log_lines(self):
        tags = {e["tag"]: e for e in _doc()["log_lines"]}
        self.assertEqual(("scripts/proof_release.py", "TAG"),
                         (tags["proof-release:"]["printed_by"], tags["proof-release:"]["constant"]))
        self.assertEqual(("scripts/proof_dispatch.py", "PREFIX"),
                         (tags["proof-dispatch:"]["printed_by"], tags["proof-dispatch:"]["constant"]))
        self.assertEqual(WORKFLOW, tags["proof identity:"]["printed_by"])
        self.assertIn("proof local run:", tags)
        for entry in tags.values():
            self.assertEqual(["tag", "printed_by", "constant", "when"], list(entry))

    def test_causes_are_the_four_words_in_order(self):
        got = proof_reach.causes()
        self.assertEqual(list(CAUSES), list(got))
        for word, meaning in got.items():
            self.assertTrue(meaning.strip(), word)

    def test_a_fifth_or_a_missing_cause_fails(self):
        doc = _doc()
        fifth = copy.deepcopy(doc)
        fifth["causes"].append({"word": "flaky", "meaning": "it was flaky"})
        self.assertTrue(proof_reach.problems(fifth))
        missing = copy.deepcopy(doc)
        missing["causes"].pop()
        self.assertTrue(proof_reach.problems(missing))
        self.assertEqual([], proof_reach.problems(doc))

    def test_a_held_for_slug_outside_the_map_fails(self):
        doc = _doc()
        _reading(doc, "product-identity")["held_for"].append("nowhere")
        found = proof_reach.problems(doc)
        self.assertTrue(any("nowhere" in p for p in found), found)

    def test_queries_declared_by_must_follow_held_for(self):
        doc = _doc()
        _reading(doc, "product-identity")["queries_declared_by"]["agent-bureau"] = "x.ts"
        self.assertTrue(proof_reach.problems(doc))
        doc = _doc()
        _reading(doc, "db-read")["queries_declared_by"] = {}
        self.assertTrue(proof_reach.problems(doc))

    def test_a_held_value_outside_the_three_fails(self):
        doc = _doc()
        _reading(doc, "db-read")["held"] = "maybe"
        self.assertTrue(proof_reach.problems(doc))

    def test_a_name_that_does_not_compile_fails(self):
        doc = _doc()
        _reading(doc, "db-read")["names"].append("(unclosed")
        self.assertTrue(proof_reach.problems(doc))

    def test_a_missing_reading_fails(self):
        doc = _doc()
        doc["readings"] = [r for r in doc["readings"] if r["id"] != "step-summary"]
        found = proof_reach.problems(doc)
        self.assertTrue(any("step-summary" in p for p in found), found)


class WorkflowHeldToTheDeclarationTest(unittest.TestCase):

    def test_the_wide_mint_dropping_a_permission_is_named(self):
        with _Copy() as root:
            text = (root / WORKFLOW).read_text()
            start = text.index(f"      - name: {WIDE}\n")
            end = text.index(f"      - name: {NARROW}\n")
            block = text[start:end]
            (root / WORKFLOW).write_text(
                text[:start] + block.replace("          permission-actions: read\n", "")
                + text[end:])
            found = proof_reach.check(root)
        self.assertTrue(any(WIDE in p and "permission-actions" in p
                            and NARROW not in p for p in found), found)

    def test_a_mint_gaining_repositories_is_named(self):
        with _Copy() as root:
            text = (root / WORKFLOW).read_text()
            start = text.index(f"      - name: {NARROW}\n")
            owner = "          owner: ${{ github.repository_owner }}\n"
            at = text.index(owner, start) + len(owner)
            (root / WORKFLOW).write_text(
                text[:at] + "          repositories: bureau-pipeline\n" + text[at:])
            found = proof_reach.check(root)
        self.assertTrue(any(NARROW in p and "repositories" in p for p in found), found)

    def test_a_mint_with_another_owner_is_named(self):
        with _Copy() as root:
            text = (root / WORKFLOW).read_text()
            start = text.index(f"      - name: {WIDE}\n")
            at = text.index("owner: ${{ github.repository_owner }}", start)
            (root / WORKFLOW).write_text(
                text[:at] + "owner: dreadnought-foundry"
                + text[at + len("owner: ${{ github.repository_owner }}"):])
            found = proof_reach.check(root)
        self.assertTrue(any(WIDE in p and "owner" in p for p in found), found)

    def test_the_narrow_mint_asking_for_variables_is_named(self):
        with _Copy() as root:
            text = (root / WORKFLOW).read_text()
            start = text.index(f"      - name: {NARROW}\n")
            meta = "          permission-metadata: read\n"
            at = text.index(meta, start) + len(meta)
            (root / WORKFLOW).write_text(
                text[:at] + "          permission-variables: read\n" + text[at:])
            found = proof_reach.check(root)
        self.assertTrue(any(NARROW in p and "permission-variables" in p for p in found), found)

    def test_a_missing_mint_is_named(self):
        with _Copy() as root:
            _edit(root / WORKFLOW, f"      - name: {NARROW}\n", "      - name: Renamed\n")
            found = proof_reach.check(root)
        self.assertTrue(any(NARROW in p for p in found), found)


class BriefHeldToTheDeclarationTest(unittest.TestCase):

    def test_every_brief_phrase_is_in_the_brief_today(self):
        brief = " ".join((ROOT / BRIEF).read_text().split())
        for r in _doc()["readings"]:
            self.assertIn(" ".join(r["brief"].split()), brief, r["id"])

    def test_a_removed_phrase_names_the_reading(self):
        for r in _doc()["readings"]:
            with self.subTest(r["id"]), _Copy() as root:
                path = root / BRIEF
                flat = " ".join(path.read_text().split())
                path.write_text(flat.replace(" ".join(r["brief"].split()), "REMOVED"))
                found = proof_reach.check(root)
                self.assertTrue(any(r["id"] in p for p in found), found)


class LogLinesHeldToTheirPrintersTest(unittest.TestCase):

    def test_a_constant_with_another_value_is_named(self):
        with _Copy() as root:
            _edit(root / "scripts" / "proof_release.py",
                  'TAG = "proof-release"', 'TAG = "proof-released"')
            found = proof_reach.check(root)
        self.assertTrue(any("proof-release:" in p and "TAG" in p for p in found), found)

    def test_a_workflow_line_no_longer_printed_is_named(self):
        with _Copy() as root:
            path = root / WORKFLOW
            path.write_text(path.read_text().replace("proof identity:", "proof id:"))
            found = proof_reach.check(root)
        self.assertTrue(any("proof identity:" in p for p in found), found)


class OutOfReachTest(unittest.TestCase):

    def reach(self, criterion: str, **kw) -> list[str]:
        kw.setdefault("caller_slug", "bureau-pipeline")
        return proof_reach.out_of_reach(criterion, **kw)

    def test_a_repo_variable_is_held_for_nobody(self):
        criterion = "`gh variable get BUREAU_PROOF_RUNS_ON` reads `ubuntu-latest`."
        for caller in ("bureau-pipeline", "portico"):
            got = self.reach(criterion, caller_slug=caller)
            self.assertEqual(1, len(got), (caller, got))
            self.assertIn("DRE-6543", got[0])

    def test_db_read(self):
        got = self.reach("The console's rows, read through `make db-read`, show the hold.")
        self.assertEqual(1, len(got), got)
        self.assertIn(" — ", got[0])

    def test_a_step_summary(self):
        self.assertEqual(1, len(self.reach("The sweep run's step summary lists the card.")))

    def test_every_repo_in_the_map_names_the_other_organizations(self):
        criterion = ("For every repo in `config/repo-map.json`, the release run on main "
                     "concluded `success`.")
        got = self.reach(criterion)
        self.assertEqual(1, len(got), got)
        self.assertIn("EveryBite/atlas", got[0])
        self.assertIn("DeltaSolv/deltasolv", got[0])
        self.assertNotIn("dreadnought-foundry", got[0])

    def test_the_phrase_alone_without_the_map_is_within_reach(self):
        self.assertEqual([], self.reach("Every repo in the organization has a stub."))

    def test_a_named_repo_in_another_organization(self):
        got = self.reach("The tag on `EveryBite/atlas` points at the merge.")
        self.assertEqual(1, len(got), got)
        self.assertIn("EveryBite/atlas", got[0])
        self.assertNotIn("DeltaSolv", got[0])
        self.assertEqual([], self.reach("The tag on `EveryBite/atlas` points at the merge.",
                                        caller_slug="atlas"))

    def test_a_caller_in_another_organization_cannot_reach_ours(self):
        got = self.reach("The run on `dreadnought-foundry/portico` concluded `success`.",
                         caller_slug="atlas")
        self.assertEqual(1, len(got), got)
        self.assertIn("dreadnought-foundry/portico", got[0])

    def test_a_local_screen_page_absent_from_the_declaration(self):
        local = {"pages": {"board": "/", "documents": "/documents"}}
        self.assertEqual(1, len(self.reach("Local screen: page `settings` shows the toggle.",
                                           local=local)))
        self.assertEqual([], self.reach("Local screen: page `documents` lists the upload.",
                                        local=local))
        self.assertEqual(1, len(self.reach("Local screen: page `documents` lists the upload.",
                                           local=None)))

    def test_the_product_identity_is_held_for_portico_only(self):
        criterion = ("Live request: `infra/scripts/proof-reader.ts query` answers the "
                     "document count as the proof-reader identity.")
        got = self.reach(criterion, caller_slug="bureau-pipeline")
        self.assertEqual(1, len(got), got)
        self.assertEqual([], self.reach(criterion, caller_slug="portico"))

    def test_as_the_proof_reader_identity_is_within_reach_for_every_caller(self):
        criterion = ("The card's comments, read as the proof-reader identity, carry one "
                     "`🔬 proof-observed` line.")
        for caller in REPO_MAP:
            self.assertEqual([], self.reach(criterion, caller_slug=caller), caller)

    def test_a_quoted_tagged_line_nothing_keeps(self):
        doc = _doc()
        doc["log_lines"] = [e for e in doc["log_lines"] if e["tag"] != "proof-release:"]
        criterion = ("The record quotes the sweep's `proof-release:` line naming the "
                     "released tag.")
        got = self.reach(criterion, doc=doc)
        self.assertEqual(1, len(got), got)
        self.assertIn("proof-release:", got[0])
        # The real declaration keeps it.
        self.assertEqual([], self.reach(criterion))

    def test_an_act_tag_is_a_receipt_kept_on_the_card(self):
        criterion = "The record quotes the card's `🔬 proof-waiting:` line."
        self.assertEqual([], self.reach(criterion))
        self.assertEqual(1, len(self.reach(criterion, acts=[])))

    def test_a_backticked_colon_token_not_called_a_line(self):
        self.assertEqual([], self.reach(
            "The sweep writes every open operator card and no `PROOF:` title."))

    def test_github_and_linear_reads_are_within_reach(self):
        self.assertEqual([], self.reach(
            "Read through `gh api repos/dreadnought-foundry/<repo>/actions/runs/<id>`, "
            "the run's status is `completed`."))
        self.assertEqual([], self.reach(
            "The Linear card's comments carry the `🔬 proof-observed` receipt."))

    def test_a_reading_is_flagged_once_however_many_names_it_carries(self):
        got = self.reach("`make db-read`, the read-only door, answers the row.")
        self.assertEqual(1, len(got), got)

    def test_the_caller_may_be_an_owner_and_name(self):
        criterion = "Live request: `PROOF_ROLE_ARN` is set and `mcp-token` signs in."
        self.assertEqual([], self.reach(criterion,
                                        caller_slug="dreadnought-foundry/portico"))
        self.assertEqual(1, len(self.reach(criterion,
                                           caller_slug="dreadnought-foundry/agent-bureau")))


class LeafTest(unittest.TestCase):

    def test_it_imports_the_standard_library_and_pyyaml_only(self):
        tree = ast.parse(SCRIPT.read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        local = {p.stem for p in (ROOT / "scripts").glob("*.py")}
        self.assertEqual(set(), names & local, "a leaf imports no pipeline module")
        self.assertLessEqual(names - set(sys.stdlib_module_names), {"yaml"})

    def test_it_runs_with_repo_unset(self):
        env = {k: v for k, v in os.environ.items() if k != "REPO"}
        done = subprocess.run([sys.executable, str(SCRIPT), "check"], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)


class ReadmeTest(unittest.TestCase):

    def test_config_readme_carries_the_row(self):
        self.assertIn("- **`proof-reach.json`**", (ROOT / "config" / "README.md").read_text())


if __name__ == "__main__":
    unittest.main()
