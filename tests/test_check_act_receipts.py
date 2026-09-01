"""RED-first tests: the completeness guard (DRE-2826).

Wrapping today's receipt sites is true for one day. `scripts/check_act_receipts.py`
is what makes it stay true: any `gh pr comment`, `gh issue comment` or
`_post_pr_note` call in `scripts/` or `.github/workflows/` whose body is not
composed through `pipeline_act.receipt()` fails CI.

WHAT THESE TESTS PIN, and what they deliberately do not.

They do NOT assert "the shipped tree is clean" and stop there — a guard that
finds nothing is indistinguishable from a guard that looks at nothing. Every
test below builds a small tree with a receipt site in it and asserts the guard's
answer changes with the tree: an unwrapped site is found, a composed one is not,
a declaration that no longer matches a site is found, and a declaration that
matches a site which now composes is found too.

The last one is the anti-mute rule. An excuse block is only worth having if it
cannot become the place things go to be forgotten: a declaration must match
exactly one real site, carry a reason, and disappear the moment the site it
excused starts composing.

Run: cd bureau-pipeline && python3 -m pytest tests/test_check_act_receipts.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import check_act_receipts as guard  # noqa: E402
import pipeline_act  # noqa: E402

CONFIG = ROOT / "config" / "pipeline-acts.json"
AN_ACT = "fix-attempt-landed"


def _tree(tmp_path: Path, *, workflow: str = "", script: str = "") -> str:
    """A miniature repo: one workflow, one script. The guard reads a root, so
    a scenario is a directory rather than an edit to the real tree."""
    (tmp_path / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    if workflow:
        (tmp_path / ".github" / "workflows" / "thing.yml").write_text(
            workflow, encoding="utf-8")
    if script:
        (tmp_path / "scripts" / "thing.py").write_text(script, encoding="utf-8")
    return str(tmp_path)


def _doc(unconverted=()) -> dict:
    doc = json.loads(CONFIG.read_text(encoding="utf-8"))
    doc["unconverted"] = list(unconverted)
    return doc


# --------------------------------------------------------------------------- #
# 1. an unwrapped site is FOUND — in shell and in python                       #
# --------------------------------------------------------------------------- #


class TestAnUnwrappedSiteIsFound:
    def test_a_new_shell_receipt_fails(self, tmp_path):
        root = _tree(tmp_path, workflow="""
jobs:
  x:
    steps:
      - run: |
          gh pr comment "$PR" --repo "$REPO" --body "🚑 a new recovery, in a hurry"
""")
        found = guard.problems(_doc(), root)
        assert any("thing.yml" in p for p in found), (
            "a receipt added with no trailer is the whole failure this guard "
            "exists for"
        )

    def test_a_new_python_receipt_fails(self, tmp_path):
        root = _tree(tmp_path, script='''
import subprocess


def announce(number, body):
    subprocess.run(["gh", "pr", "comment", str(number), "--body", body])
''')
        assert any("thing.py" in p for p in guard.problems(_doc(), root))

    def test_a_new_post_pr_note_caller_fails(self, tmp_path):
        root = _tree(tmp_path, script='''
def _post_pr_note(number, body):
    pass


def announce(pr):
    _post_pr_note(pr["number"], "🚑 a new recovery, in a hurry")
''')
        found = guard.problems(_doc(), root)
        assert len([p for p in found if "thing.py" in p]) == 1, (
            "the seam's own `gh pr comment` is not a second site — it takes a "
            "body it cannot know the meaning of, and its callers are the sites"
        )

    def test_a_gh_pr_listing_is_not_a_receipt_site(self, tmp_path):
        root = _tree(tmp_path, workflow="""
jobs:
  x:
    steps:
      - run: |
          gh pr list --repo "$REPO" --json comments
""")
        assert guard.sites(root) == []


# --------------------------------------------------------------------------- #
# 2. a composed site is NOT found                                              #
# --------------------------------------------------------------------------- #


class TestAComposedSiteIsAccepted:
    def test_shell_composed_through_the_receipt_cli_passes(self, tmp_path):
        root = _tree(tmp_path, workflow=f"""
jobs:
  x:
    steps:
      - run: |
          python3 scripts/pipeline_act.py receipt {AN_ACT} \\
            --body "🔧 a body" --out /tmp/act.md
          gh pr comment "$PR" --repo "$REPO" --body-file /tmp/act.md
""")
        assert guard.problems(_doc(), root) == []
        assert [s.composed_as for s in guard.sites(root)] == [AN_ACT]

    def test_shell_reading_a_file_nothing_composed_fails(self, tmp_path):
        """`--body-file` is not itself proof. The file has to be the one the
        writer produced, or the guard passes on any temp path at all."""
        root = _tree(tmp_path, workflow="""
jobs:
  x:
    steps:
      - run: |
          gh pr comment "$PR" --repo "$REPO" --body-file /tmp/somebody-elses.md
""")
        assert any("thing.yml" in p for p in guard.problems(_doc(), root))

    def test_shell_composing_AFTER_the_post_fails(self, tmp_path):
        """Order matters: a file written after the post is last run's file."""
        root = _tree(tmp_path, workflow=f"""
jobs:
  x:
    steps:
      - run: |
          gh pr comment "$PR" --repo "$REPO" --body-file /tmp/act.md
          python3 scripts/pipeline_act.py receipt {AN_ACT} \\
            --body "🔧 a body" --out /tmp/act.md
""")
        assert any("thing.yml" in p for p in guard.problems(_doc(), root))

    def test_python_composed_inline_passes(self, tmp_path):
        root = _tree(tmp_path, script=f'''
import subprocess

import pipeline_act


def announce(number):
    subprocess.run(["gh", "pr", "comment", str(number), "--body",
                    pipeline_act.receipt("{AN_ACT}", "🔧 a body")])
''')
        assert guard.problems(_doc(), root) == []

    def test_python_composed_through_a_local_passes(self, tmp_path):
        root = _tree(tmp_path, script=f'''
import subprocess

import pipeline_act


def announce(number):
    body = pipeline_act.receipt("{AN_ACT}", "🔧 a body")
    subprocess.run(["gh", "pr", "comment", str(number), "--body", body])
''')
        assert guard.problems(_doc(), root) == []

    def test_composing_an_act_the_registry_does_not_declare_fails(self, tmp_path):
        root = _tree(tmp_path, script='''
import subprocess

import pipeline_act


def announce(number):
    subprocess.run(["gh", "pr", "comment", str(number), "--body",
                    pipeline_act.receipt("no-such-act", "🔧 a body")])
''')
        assert any("no-such-act" in p for p in guard.problems(_doc(), root))

    def test_an_act_flag_naming_an_undeclared_act_fails(self, tmp_path):
        """A typo in `--act=` on a workflow line does not fail a test — it
        fails a live run, at the write, after the work is done."""
        root = _tree(tmp_path, workflow="""
jobs:
  x:
    steps:
      - run: |
          python3 scripts/linear_ops.py comment "$CARD" "$MSG" --act=no-such-act
""")
        assert any("no-such-act" in p for p in guard.problems(_doc(), root))


# --------------------------------------------------------------------------- #
# 3. the excuse block cannot become a mute button                              #
# --------------------------------------------------------------------------- #


UNWRAPPED_WORKFLOW = """
jobs:
  x:
    steps:
      - run: |
          gh pr comment "$PR" --repo "$REPO" --body "🚑 a deliberate non-act"
"""


class TestTheDeclarationIsCheckedTooo:
    def test_a_declared_site_produces_no_finding(self, tmp_path):
        root = _tree(tmp_path, workflow=UNWRAPPED_WORKFLOW)
        doc = _doc([{
            "file": ".github/workflows/thing.yml",
            "anchor": "🚑 a deliberate non-act",
            "why": "it is a judgement about the work, not something the "
                   "pipeline did on its own",
        }])
        assert guard.problems(doc, root) == []

    def test_a_declaration_matching_no_site_fails(self, tmp_path):
        """The rot case. The site was moved or reworded and the excuse outlived
        it — from then on the block describes a tree nobody has."""
        root = _tree(tmp_path, workflow=UNWRAPPED_WORKFLOW)
        doc = _doc([{
            "file": ".github/workflows/thing.yml",
            "anchor": "wording this file has never carried",
            "why": "a reason",
        }])
        assert any("matches 0 receipt site" in p for p in guard.problems(doc, root))

    def test_a_declaration_matching_two_sites_fails(self, tmp_path):
        root = _tree(tmp_path, workflow="""
jobs:
  x:
    steps:
      - run: |
          gh pr comment "$PR" --repo "$REPO" --body "🚑 one"
          gh pr comment "$PR" --repo "$REPO" --body "🚑 two"
""")
        doc = _doc([{
            "file": ".github/workflows/thing.yml",
            "anchor": "gh pr comment",
            "why": "a reason",
        }])
        assert any("matches 2 receipt site" in p for p in guard.problems(doc, root))

    def test_a_declaration_without_a_reason_fails(self, tmp_path):
        root = _tree(tmp_path, workflow=UNWRAPPED_WORKFLOW)
        doc = _doc([{
            "file": ".github/workflows/thing.yml",
            "anchor": "🚑 a deliberate non-act",
            "why": "  ",
        }])
        assert any("no reason" in p for p in guard.problems(doc, root))

    def test_a_declaration_over_a_site_that_now_composes_fails(self, tmp_path):
        """The other half of the anti-mute rule. Once a site is wrapped its
        excuse has to go, or the block keeps a row that would silently cover
        the site if it ever regressed."""
        root = _tree(tmp_path, workflow=f"""
jobs:
  x:
    steps:
      - run: |
          python3 scripts/pipeline_act.py receipt {AN_ACT} \\
            --body "🚑 a deliberate non-act" --out /tmp/act.md
          gh pr comment "$PR" --repo "$REPO" --body-file /tmp/act.md
""")
        doc = _doc([{
            "file": ".github/workflows/thing.yml",
            "anchor": "--body-file /tmp/act.md",
            "why": "a reason",
        }])
        assert any("now hides a site that is fine" in p
                   for p in guard.problems(doc, root))


# --------------------------------------------------------------------------- #
# 4. the shipped tree, and the wiring                                          #
# --------------------------------------------------------------------------- #


class TestTheShippedTree:
    def test_the_shipped_tree_is_clean(self):
        assert guard.problems() == []

    def test_the_check_exits_zero(self):
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_act_receipts.py")],
            capture_output=True, text=True, cwd=str(ROOT), check=False,
        )
        assert r.returncode == 0, r.stdout + r.stderr

    def test_every_declared_act_emitted_from_the_corpus_has_a_composed_site(self):
        """The registry says where each act is emitted; the guard says which
        sites compose. An act declared as emitted from a poster call that no
        site composes would pass both checks separately and neither together."""
        composed = {s.composed_as for s in guard.sites() if s.composed_as}
        for name in pipeline_act.acts():
            emits = pipeline_act.record(name)["emits"]
            text = (ROOT / emits["file"]).read_text(encoding="utf-8")
            anchor_line = next(
                (line for line in text.splitlines() if emits["anchor"] in line), ""
            )
            if "gh pr comment" in anchor_line:
                assert name in composed, f"{name} is posted raw"

    def test_ci_runs_the_guard(self):
        """A guard nothing runs is a file. The card's whole claim is that this
        FAILS CI."""
        ci = (ROOT / ".github" / "workflows" / "tests.yml").read_text("utf-8")
        assert "check_act_receipts.py" in ci

    def test_the_list_command_reports_every_site(self):
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_act_receipts.py"), "list"],
            capture_output=True, text=True, cwd=str(ROOT), check=False,
        )
        assert r.returncode == 0, r.stderr
        assert len(json.loads(r.stdout)) == len(guard.sites())


class TestTheUnconvertedBlockIsHonest:
    def test_every_shipped_declaration_names_a_real_site_and_a_reason(self):
        declared = guard.declarations()
        assert declared, (
            "the block is the record of what this card did NOT convert — an "
            "empty one would mean every receipt site composes, and the count "
            "in the PR body says otherwise"
        )
        found = guard.sites()
        for declaration in declared:
            hits = [s for s in found if guard._matches(declaration, s)]
            assert len(hits) == 1, declaration
            assert declaration["why"].strip()
            assert declaration["kind"] in ("not-an-act", "undeclared-act")

    def test_no_declaration_covers_a_declared_act(self):
        """An act with a registry row is converted by this card, full stop. A
        row in both blocks would let a declared act post no trailer."""
        anchors = {(d["file"], d["anchor"]) for d in guard.declarations()}
        for name in pipeline_act.acts():
            emits = pipeline_act.record(name)["emits"]
            for path, anchor in anchors:
                if path == emits["file"]:
                    assert anchor != emits["anchor"], name
