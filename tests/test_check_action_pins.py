"""RED-first tests for DRE-3418 — every third-party action pinned to a SHA.

2026-09-08 15:15–16:27 PT: Anthropic's `claude-code-action` floating `v1` tag
moved to v1.0.218, whose Claude Code installer leaves no launcher, and every
Claude-running job in the fleet died for 72 minutes (DRE-3416, upstream
anthropics/claude-code-action#1817). Nothing stood between the vendor's release
and production because the reusables floated on major tags — and a floating
major gives Dependabot nothing to bump, since a patch release moves the tag
silently.

A 40-char SHA pin turns every vendor release into a Dependabot PR that the
critic and the harness prove. `scripts/check_action_pins.py` is what stops a
floating tag from coming back, so this suite pins BOTH halves:

  * the checker's behaviour, against synthetic workflows written per violation
    class (a `@vN` tag, a SHA with no version comment, a short/non-hex ref) —
    each one must be named with its file and LINE, because the operator fixing
    it needs the line and nothing else;
  * the LIVE tree — the checker exits 0 over this repo's real workflows and
    composite actions, `.github/dependabot.yml` carries the DRE-2064 house
    major-ignore shape on its `github-actions` entry, and tests.yml runs the
    checker so a floating tag cannot return unnoticed.

The `dreadnought-foundry/*` exemption is deliberate and is asserted here by
name: those are our OWN reusable workflows riding our own `@stable`/`@main`
release channel (docs/self-hosting.md), which the harness already proves.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_action_pins  # noqa: E402

CHECKER = ROOT / "scripts" / "check_action_pins.py"
WORKFLOWS = ROOT / ".github" / "workflows"
ACTIONS = ROOT / ".github" / "actions"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
TESTS_WORKFLOW = WORKFLOWS / "tests.yml"

MAJOR_IGNORE_TYPE = "version-update:semver-major"

# A real 40-char lowercase-hex commit sha, and a version comment in the shape
# Dependabot both reads and rewrites.
SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"


def write_workflow(tmp: Path, name: str, body: str) -> Path:
    path = tmp / name
    path.write_text(textwrap.dedent(body).lstrip("\n"))
    return path


class ScanTest(unittest.TestCase):
    """The line-level reader: which `uses:` lines does the checker see?"""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_a_floating_major_tag_is_a_violation_naming_the_line(self):
        path = write_workflow(self.tmp, "w.yml", """
            jobs:
              a:
                steps:
                  - uses: actions/checkout@v7
        """)
        violations = check_action_pins.check_paths([path])
        self.assertEqual(len(violations), 1, violations)
        self.assertIn("w.yml:4", violations[0])
        self.assertIn("actions/checkout@v7", violations[0])

    def test_a_sha_pin_with_a_version_comment_is_clean(self):
        path = write_workflow(self.tmp, "w.yml", f"""
            jobs:
              a:
                steps:
                  - uses: actions/checkout@{SHA} # v7.0.1
        """)
        self.assertEqual(check_action_pins.check_paths([path]), [])

    def test_a_sha_pin_without_a_version_comment_is_a_violation(self):
        """Dependabot reads the version comment to know what to bump FROM; a
        bare SHA is pinned but frozen, and nothing proposes its successor."""
        path = write_workflow(self.tmp, "w.yml", f"""
            jobs:
              a:
                steps:
                  - uses: actions/checkout@{SHA}
        """)
        violations = check_action_pins.check_paths([path])
        self.assertEqual(len(violations), 1, violations)
        self.assertIn("w.yml:4", violations[0])
        self.assertIn("version comment", violations[0])

    def test_a_bare_major_in_the_comment_is_not_a_version(self):
        """`# v7` is the floating tag written down — it names no release."""
        path = write_workflow(self.tmp, "w.yml", f"""
            jobs:
              a:
                steps:
                  - uses: actions/checkout@{SHA} # v7
        """)
        self.assertEqual(len(check_action_pins.check_paths([path])), 1)

    def test_a_version_comment_may_carry_trailing_prose(self):
        """Offline, the checker reads only the leading version, so prose after
        it passes here. That is not a claim the prose is safe: Dependabot
        rewrites the comment only when nothing follows the version (#452), and
        it is `--resolve` (ResolveTest) that catches the disagreement left
        behind."""
        path = write_workflow(self.tmp, "w.yml", f"""
            jobs:
              a:
                steps:
                  - uses: anthropics/claude-code-action@{SHA} # v1.0.217 — pinned DRE-3416
        """)
        self.assertEqual(check_action_pins.check_paths([path]), [])

    def test_a_short_or_non_hex_ref_is_a_violation(self):
        for ref in ("3d3c42e", "main", "v7.0.1", "z" * 40):
            with self.subTest(ref=ref):
                path = write_workflow(self.tmp, "w.yml", f"""
                    jobs:
                      a:
                        steps:
                          - uses: actions/checkout@{ref} # v7.0.1
                """)
                self.assertEqual(len(check_action_pins.check_paths([path])), 1)

    def test_our_own_reusable_workflows_are_exempt_by_name(self):
        """dreadnought-foundry/* rides OUR release channel (@stable/@main,
        docs/self-hosting.md) — the harness proves it, so it is not a vendor."""
        path = write_workflow(self.tmp, "w.yml", """
            jobs:
              a:
                uses: dreadnought-foundry/bureau-pipeline/.github/workflows/plan.yml@main
              b:
                uses: dreadnought-foundry/bureau-pipeline/.github/actions/x@stable
        """)
        self.assertEqual(check_action_pins.check_paths([path]), [])
        self.assertEqual(check_action_pins.EXEMPT_OWNER, "dreadnought-foundry")

    def test_local_path_references_are_not_third_party(self):
        path = write_workflow(self.tmp, "w.yml", """
            jobs:
              a:
                steps:
                  - uses: ./.github/actions/setup-python-cached
                  - uses: ./.bureau-pipeline/.github/actions/setup-node-cached
        """)
        self.assertEqual(check_action_pins.check_paths([path]), [])

    def test_commented_out_and_prose_uses_lines_are_ignored(self):
        """`tdd-commit-check/action.yml` documents a caller snippet in a
        comment block, and half the reusables discuss `uses:` in prose. Neither
        is a step, and flagging them would make the checker unfixable."""
        path = write_workflow(self.tmp, "w.yml", """
            # HOW CALLERS PIN THIS:
            #       - uses: actions/checkout@v5
            jobs:
              a:
                steps:
                  # a caller must pass the same ref it pins in `uses:`
                  - name: prose mentioning uses: actions/checkout@v7
                    run: echo hi
        """)
        self.assertEqual(check_action_pins.check_paths([path]), [])

    def test_a_subpath_action_is_pinned_at_its_repository_sha(self):
        """`actions/cache/restore` is a subdirectory of the actions/cache
        repository; the pin is that repository's commit."""
        path = write_workflow(self.tmp, "w.yml", f"""
            jobs:
              a:
                steps:
                  - uses: actions/cache/restore@{SHA} # v6.1.0
                  - uses: actions/cache/save@v6
        """)
        violations = check_action_pins.check_paths([path])
        self.assertEqual(len(violations), 1, violations)
        self.assertIn("actions/cache/save@v6", violations[0])


class CliTest(unittest.TestCase):
    """The exit codes the acceptance criteria are written against."""

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(CHECKER), *args],
            capture_output=True, text=True,
        )

    def test_exits_zero_on_this_branch(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_exits_one_naming_the_line_when_a_floating_tag_comes_back(self):
        """Put a single `@vN` back and the checker fails, naming the file and
        line. This is the acceptance criterion, exercised end to end."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            write_workflow(tmp, "regressed.yml", f"""
                jobs:
                  a:
                    steps:
                      - uses: actions/checkout@{SHA} # v7.0.1
                      - uses: actions/create-github-app-token@v3
            """)
            proc = self._run(str(tmp))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("regressed.yml:5", proc.stdout)
        self.assertIn("actions/create-github-app-token@v3", proc.stdout)

    def test_a_run_that_inspects_nothing_fails_rather_than_passing(self):
        """A vacuous run is the failure mode a pin checker cannot afford: point
        it at the wrong directory and silence must not read as a green."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = self._run(tmpdir)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("no third-party", proc.stdout.lower())


# The #452 shape (DRE-4337): the sha of one claude-code-action release under the
# version comment of another. v1.0.217 and v1.0.236 are real releases; v1.0.236
# is an ANNOTATED tag, so its ref names a tag object that names the commit.
OLD_SHA = "9c5ddab2e6d17b83ea679153b31f1d5f023cf636"   # v1.0.217
NEW_SHA = "8ce9314fa9a404564fa7e954cd84f25bcba2b829"   # v1.0.236
TAG_OBJECT = "20f0b248c5003db4b9ca43c45e17949bcdc36d2d"  # v1.0.236's tag object
VENDOR = "anthropics/claude-code-action"


def lightweight(tag, sha):
    """A `git/ref/tags/<tag>` body for a tag that names its commit directly."""
    return {"ref": f"refs/tags/{tag}", "object": {"type": "commit", "sha": sha}}


class StubRead:
    """The tag read, stubbed: `api path -> parsed JSON`, or a raised failure.
    Records every path asked, so a test can say what was read and how often."""

    def __init__(self, bodies=None, fail=None):
        self.bodies = bodies or {}
        self.fail = fail
        self.asked = []

    def __call__(self, path):
        self.asked.append(path)
        if self.fail is not None:
            raise check_action_pins.Unresolved(self.fail)
        if path not in self.bodies:
            raise check_action_pins.Unresolved(f"gh: Not Found (HTTP 404) {path}")
        return self.bodies[path]


class ResolveTest(unittest.TestCase):
    """DRE-4337 — `--resolve`: the version comment must name the release the
    sha IS, not merely some release.

    On #452 (2026-09-19) Dependabot moved the launcher's sha to v1.0.226 in 22
    places and left every comment saying v1.0.217, and the offline check — which
    asks only that SOME `v<x>.<y>` lead the comment — reported all clear. The
    comment is what an operator reads during an outage to decide whether a
    vendor release is the cause.
    """

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def _pin(self, sha, version, action=VENDOR, name="w.yml"):
        return write_workflow(self.tmp, name, f"""
            jobs:
              a:
                steps:
                  - uses: {action}@{sha} # {version}
        """)

    def _main(self, *args, read):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check_action_pins.main(["check_action_pins.py", *args], read=read)
        return code, out.getvalue()

    def test_a_sha_that_is_not_the_release_its_comment_names_fails(self):
        """The #452 shape: v1.0.217's sha under `# v1.0.236`. The tag names
        another commit, so the line fails, naming its file and line and both
        shas — the operator needs to know which half moved."""
        path = self._pin(OLD_SHA, "v1.0.236")
        read = StubRead({
            f"repos/{VENDOR}/git/ref/tags/v1.0.236": lightweight("v1.0.236", NEW_SHA),
        })
        code, out = self._main("--resolve", str(path), read=read)
        self.assertEqual(code, 1, out)
        fails = [line for line in out.splitlines() if line.startswith("FAIL ")]
        self.assertEqual(len(fails), 1, out)
        self.assertIn("w.yml:4", fails[0])
        self.assertIn("v1.0.236", fails[0])
        self.assertIn(OLD_SHA, fails[0])
        self.assertIn(NEW_SHA, fails[0])
        self.assertNotIn("UNKNOWN", out)

    def test_a_sha_that_is_the_release_its_comment_names_passes(self):
        path = self._pin(NEW_SHA, "v1.0.236")
        read = StubRead({
            f"repos/{VENDOR}/git/ref/tags/v1.0.236": lightweight("v1.0.236", NEW_SHA),
        })
        code, out = self._main("--resolve", str(path), read=read)
        self.assertEqual(code, 0, out)
        self.assertNotIn("FAIL", out)
        self.assertNotIn("UNKNOWN", out)
        self.assertIn("1 agree", out)

    def test_an_annotated_tag_is_dereferenced_to_its_commit(self):
        """v1.0.236 is annotated: its ref names a tag OBJECT, whose sha is not
        the commit. Comparing that sha to the pin would fail every annotated
        release; the checker follows it to the commit."""
        path = self._pin(NEW_SHA, "v1.0.236")
        read = StubRead({
            f"repos/{VENDOR}/git/ref/tags/v1.0.236": {
                "ref": "refs/tags/v1.0.236",
                "object": {"type": "tag", "sha": TAG_OBJECT},
            },
            f"repos/{VENDOR}/git/tags/{TAG_OBJECT}": {
                "sha": TAG_OBJECT,
                "object": {"type": "commit", "sha": NEW_SHA},
            },
        })
        code, out = self._main("--resolve", str(path), read=read)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(read.asked), 2, read.asked)

    def test_a_tag_read_that_fails_is_unknown_never_a_pass(self):
        """No token, no network, a 404, a body that will not parse: each one
        leaves the line UNKNOWN — printed, not counted as agreeing, and the run
        exits non-zero. A check that cannot see must not read as green."""
        failures = {
            "no token": "gh: To use GitHub CLI in a GitHub Actions workflow, set the GH_TOKEN environment variable",
            "no network": "error connecting to api.github.com",
            "404": "gh: Not Found (HTTP 404)",
            "unparseable": "gh api returned a body that is not JSON",
        }
        path = self._pin(NEW_SHA, "v1.0.236")
        for label, reason in failures.items():
            with self.subTest(label):
                code, out = self._main("--resolve", str(path), read=StubRead(fail=reason))
                self.assertNotEqual(code, 0, out)
                self.assertEqual(code, 2, out)
                unknown = [line for line in out.splitlines() if line.startswith("UNKNOWN ")]
                self.assertEqual(len(unknown), 1, out)
                self.assertIn("w.yml:4", unknown[0])
                self.assertIn(reason, unknown[0])
                self.assertIn("0 agree", out)
                self.assertNotIn("ok:", out)

    def test_a_body_of_the_wrong_shape_is_unknown(self):
        """A list (the plural refs endpoint's prefix match), a ref for another
        tag, an object that is neither commit nor tag, a sha that is not one —
        none of them is an answer, so none of them may agree or disagree."""
        tag_path = f"repos/{VENDOR}/git/ref/tags/v1.0.236"
        bodies = {
            "list": [lightweight("v1.0.236", NEW_SHA)],
            "another tag": lightweight("v1.0.2366", NEW_SHA),
            "a tree": {"ref": "refs/tags/v1.0.236",
                       "object": {"type": "tree", "sha": NEW_SHA}},
            "short sha": lightweight("v1.0.236", NEW_SHA[:7]),
            "no object": {"ref": "refs/tags/v1.0.236"},
        }
        path = self._pin(NEW_SHA, "v1.0.236")
        for label, body in bodies.items():
            with self.subTest(label):
                code, out = self._main(
                    "--resolve", str(path), read=StubRead({tag_path: body}))
                self.assertEqual(code, 2, out)
                self.assertIn("UNKNOWN ", out)
                self.assertNotIn("FAIL ", out)

    def test_a_tag_loop_is_unknown_not_a_hang(self):
        path = self._pin(NEW_SHA, "v1.0.236")
        loop = {"sha": TAG_OBJECT, "object": {"type": "tag", "sha": TAG_OBJECT}}
        read = StubRead({
            f"repos/{VENDOR}/git/ref/tags/v1.0.236": {
                "ref": "refs/tags/v1.0.236", "object": loop["object"]},
            f"repos/{VENDOR}/git/tags/{TAG_OBJECT}": loop,
        })
        code, out = self._main("--resolve", str(path), read=read)
        self.assertEqual(code, 2, out)
        self.assertIn("UNKNOWN ", out)

    def test_a_disagreement_outranks_an_unknown_in_the_exit_code(self):
        """Exit 1 says a comment is wrong; exit 2 says only that some could
        not be read. A run with both is a run with a wrong comment."""
        path = write_workflow(self.tmp, "w.yml", f"""
            jobs:
              a:
                steps:
                  - uses: {VENDOR}@{OLD_SHA} # v1.0.236
                  - uses: actions/checkout@{SHA} # v7.0.1
        """)
        read = StubRead({
            f"repos/{VENDOR}/git/ref/tags/v1.0.236": lightweight("v1.0.236", NEW_SHA),
        })
        code, out = self._main("--resolve", str(path), read=read)
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL w.yml:4", out)
        self.assertIn("UNKNOWN w.yml:5", out)

    def test_one_read_per_release_however_many_lines_pin_it(self):
        """The live tree pins the launcher in 28 places; that is one release
        and one question. A subpath action is read at its repository."""
        path = write_workflow(self.tmp, "w.yml", f"""
            jobs:
              a:
                steps:
                  - uses: {VENDOR}@{NEW_SHA} # v1.0.236
                  - uses: {VENDOR}@{NEW_SHA} # v1.0.236
                  - uses: {VENDOR}@{NEW_SHA} # v1.0.236
                  - uses: actions/cache/restore@{SHA} # v6.1.0
                  - uses: actions/cache/save@{SHA} # v6.1.0
        """)
        read = StubRead({
            f"repos/{VENDOR}/git/ref/tags/v1.0.236": lightweight("v1.0.236", NEW_SHA),
            "repos/actions/cache/git/ref/tags/v6.1.0": lightweight("v6.1.0", SHA),
        })
        code, out = self._main("--resolve", str(path), read=read)
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(read.asked), sorted(set(read.asked)), read.asked)
        self.assertEqual(len(read.asked), 2, read.asked)
        self.assertIn("5 agree", out)

    def test_a_line_that_already_fails_offline_is_not_resolved(self):
        """A floating tag or a missing comment is already a FAIL with its own
        message; there is no version to resolve, and asking would be noise."""
        path = write_workflow(self.tmp, "w.yml", f"""
            jobs:
              a:
                steps:
                  - uses: actions/checkout@v7
                  - uses: actions/checkout@{SHA}
        """)
        read = StubRead()
        code, out = self._main("--resolve", str(path), read=read)
        self.assertEqual(code, 1, out)
        self.assertEqual(read.asked, [])

    def test_offline_the_checker_never_reads_and_says_what_it_said_before(self):
        """No flag, no read, and the same two lines today's checker prints —
        even over a sha/version disagreement, which only `--resolve` can see."""
        path = self._pin(OLD_SHA, "v1.0.236")
        read = StubRead(fail="offline must not read")
        code, out = self._main(str(path), read=read)
        self.assertEqual(code, 0, out)
        self.assertEqual(read.asked, [])
        self.assertEqual(out, (
            "checked 1 files: 1 third-party action references, 0 "
            "dreadnought-foundry/* (exempt — our own release channel), 0 local "
            "paths\n"
            "ok: every third-party action is pinned to a commit sha with its "
            "version in a comment\n"
        ))


class ResolveCliTest(unittest.TestCase):
    """`--resolve` end to end, through the real `gh api` boundary.

    A fake `gh` on PATH stands in for GitHub, so the subprocess call, its exit
    code and its JSON are all exercised without the network.
    """

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()

    def _fake_gh(self, script):
        gh = self.bin / "gh"
        gh.write_text("#!/bin/sh\n" + textwrap.dedent(script))
        gh.chmod(0o755)

    def _run(self, *args):
        env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
        env["PATH"] = str(self.bin)
        return subprocess.run(
            [sys.executable, str(CHECKER), *args],
            capture_output=True, text=True, env=env,
        )

    def _workflow(self, sha):
        return write_workflow(self.tmp, "w.yml", f"""
            jobs:
              a:
                steps:
                  - uses: {VENDOR}@{sha} # v1.0.236
        """)

    def test_a_disagreement_read_through_gh_fails_naming_the_line(self):
        body = json.dumps(lightweight("v1.0.236", NEW_SHA))
        self._fake_gh(f"""
            [ "$1" = api ] && [ "$2" = "repos/{VENDOR}/git/ref/tags/v1.0.236" ] \\
              && echo '{body}' && exit 0
            echo "gh: Not Found (HTTP 404)" >&2; exit 1
        """)
        proc = self._run("--resolve", str(self._workflow(OLD_SHA)))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("FAIL w.yml:4", proc.stdout)

    def test_an_agreement_read_through_gh_passes(self):
        body = json.dumps(lightweight("v1.0.236", NEW_SHA))
        self._fake_gh(f"""
            [ "$1" = api ] && [ "$2" = "repos/{VENDOR}/git/ref/tags/v1.0.236" ] \\
              && echo '{body}' && exit 0
            exit 1
        """)
        proc = self._run("--resolve", str(self._workflow(NEW_SHA)))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_no_token_is_unknown_and_exits_non_zero(self):
        self._fake_gh("""
            echo "gh: To use GitHub CLI in a GitHub Actions workflow, set the GH_TOKEN environment variable." >&2
            exit 4
        """)
        proc = self._run("--resolve", str(self._workflow(NEW_SHA)))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("UNKNOWN w.yml:4", proc.stdout)
        self.assertIn("GH_TOKEN", proc.stdout)

    def test_a_body_that_is_not_json_is_unknown(self):
        self._fake_gh("""
            echo "<html>rate limited</html>"; exit 0
        """)
        proc = self._run("--resolve", str(self._workflow(NEW_SHA)))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("UNKNOWN w.yml:4", proc.stdout)

    def test_no_gh_at_all_is_unknown_not_a_crash(self):
        proc = self._run("--resolve", str(self._workflow(NEW_SHA)))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("UNKNOWN w.yml:4", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)


class LiveTreeTest(unittest.TestCase):
    """The tree itself — the acceptance criteria, read off the real files."""

    def _yaml_files(self):
        return sorted(WORKFLOWS.glob("*.yml")) + sorted(ACTIONS.glob("*/action.yml"))

    def test_no_floating_tag_survives_anywhere_we_call_an_action(self):
        """The card's grep, as a test: only `dreadnought-foundry/*` may carry a
        symbolic ref. Composite actions under .github/actions are included —
        `actions/setup-python@v7` inside setup-python-cached floats exactly the
        way a workflow-level one does, and the same tag move reaches it."""
        offenders = []
        for path in self._yaml_files():
            for ref in check_action_pins.references(path):
                if ref.exempt or ref.local:
                    continue
                if not check_action_pins.SHA_RE.fullmatch(ref.ref):
                    offenders.append(f"{path.name}:{ref.line} {ref.uses}")
        self.assertEqual(offenders, [], f"floating action refs remain: {offenders}")

    def test_every_pinned_line_carries_its_version_comment(self):
        pinned = 0
        missing = []
        for path in self._yaml_files():
            for ref in check_action_pins.references(path):
                if ref.exempt or ref.local:
                    continue
                pinned += 1
                if not check_action_pins.VERSION_COMMENT_RE.match(ref.comment or ""):
                    missing.append(f"{path.name}:{ref.line} {ref.uses}")
        self.assertGreater(pinned, 100, "the live scan went vacuous")
        self.assertEqual(missing, [], f"pins with no version comment: {missing}")

    def test_the_claude_code_action_pin_is_the_dre_5116_release(self):
        """DRE-3416 held the vendor at v1.0.217 (Claude Code 2.1.263); DRE-3417
        moved it to v1.0.234 (Claude Code 2.1.282) on 2026-09-25, because the
        API refuses Claude Opus 5.5 from anything older than 2.1.280 (DRE-4852).
        DRE-5116 moved it to v1.0.236 (Claude Code 2.1.284) on 2026-09-28: the
        first Claude Code whose model registry knows `claude-sonnet-5-5`, which
        2.1.282 ran with its unknown-model defaults (a 200K context window and a
        32K output ceiling, against the model's 1M and 128K).
        One sha, everywhere: a workflow left on the old one would run a vendor
        action built for a different Claude Code than the one installed."""
        found = set()
        for path in self._yaml_files():
            for ref in check_action_pins.references(path):
                if ref.action == "anthropics/claude-code-action":
                    found.add(ref.ref)
        self.assertEqual(found, {"8ce9314fa9a404564fa7e954cd84f25bcba2b829"})

    #: What each vendor sha this repo has pinned installs, read from that
    #: release's `base-action/action.yml` (`CLAUDE_CODE_VERSION=`). Recorded,
    #: not fetched: CI here has no business reading another repo at test time.
    VENDOR_INSTALLS = {
        "9c5ddab2e6d17b83ea679153b31f1d5f023cf636": "2.1.263",  # v1.0.217
        "9171db3e57d6a3140a37ddc2ba92788584e0ead6": "2.1.282",  # v1.0.234
        "8ce9314fa9a404564fa7e954cd84f25bcba2b829": "2.1.284",  # v1.0.236
    }

    def test_the_installer_default_is_what_the_pinned_vendor_installs(self):
        """The pair the installer's own comment promises is held here: its
        `version` default decides which Claude Code the fleet runs (the vendor
        skips its install when handed the proved path), so it must be exactly
        what the pinned vendor release would have installed — moved together,
        never one without the other."""
        action = yaml.safe_load(
            (ROOT / ".github" / "actions" / "install-claude-code" / "action.yml").read_text())
        default = action["inputs"]["version"]["default"]
        shas = set()
        for path in self._yaml_files():
            for ref in check_action_pins.references(path):
                if ref.action == "anthropics/claude-code-action":
                    shas.add(ref.ref)
        self.assertEqual(len(shas), 1, f"more than one vendor sha pinned: {shas}")
        (sha,) = shas
        self.assertIn(sha, self.VENDOR_INSTALLS,
                      f"record what vendor sha {sha} installs in VENDOR_INSTALLS")
        self.assertEqual(default, self.VENDOR_INSTALLS[sha])

    def test_tests_yml_runs_the_checker(self):
        """A guard nobody runs is a guard that has already failed."""
        doc = yaml.safe_load(TESTS_WORKFLOW.read_text())
        runs = [
            step.get("run", "")
            for job in doc["jobs"].values()
            for step in (job.get("steps") or [])
        ]
        self.assertTrue(
            any("check_action_pins.py" in r for r in runs),
            "tests.yml runs no action-pin check",
        )

    def test_tests_yml_resolves_every_pin_with_the_run_token(self):
        """DRE-4337: CI runs the online mode, so a sha that is not the release
        its comment names turns the required check red. Every pinned repository
        is public, so the default read token — read-only on a
        Dependabot-triggered run — is all the read needs."""
        doc = yaml.safe_load(TESTS_WORKFLOW.read_text())
        steps = [
            step
            for job in doc["jobs"].values()
            for step in (job.get("steps") or [])
            if "check_action_pins.py" in step.get("run", "")
        ]
        self.assertEqual(len(steps), 1, steps)
        (step,) = steps
        self.assertIn("--resolve", step["run"].split())
        self.assertEqual((step.get("env") or {}).get("GH_TOKEN"), "${{ github.token }}")


class DependabotBehaviorWordingTest(unittest.TestCase):
    """DRE-4337: what Dependabot was observed to do on #452, said once.

    It moves the sha always, and rewrites the version comment only when nothing
    follows the version. Both the dependabot.yml comment and the checker said
    otherwise — "rewrites both", "trailing prose is fine" — and the comment
    pointed at this card as unbuilt.
    """

    def _github_actions_comment(self):
        lines = DEPENDABOT.read_text().splitlines()
        end = next(i for i, line in enumerate(lines)
                   if 'package-ecosystem: "github-actions"' in line)
        start = end
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        return " ".join(line.strip().lstrip("#").strip() for line in lines[start:end])

    def test_the_dependabot_comment_states_the_observed_behavior_once(self):
        comment = self._github_actions_comment()
        self.assertNotIn("rewrites both", comment)
        self.assertNotIn("carded as DRE-4337", comment)
        self.assertNotIn("does not catch", comment)
        self.assertIn("--resolve", comment)

    def test_the_checker_no_longer_calls_trailing_prose_fine(self):
        source = " ".join(CHECKER.read_text().split())
        for stale in ("Trailing prose is fine",
                      "and rewrites when it proposes the next one"):
            self.assertFalse(stale in source, f"the checker still says {stale!r}")
        self.assertTrue("only when nothing follows the version" in source,
                        "the checker does not state when Dependabot rewrites the comment")

    def test_the_checker_is_referenced_in_the_release_channel_doc(self):
        """A change that contradicts a document updates that document: the
        release-channel doc now states how vendor actions reach the fleet."""
        doc = (ROOT / "docs" / "self-hosting.md").read_text()
        self.assertIn("check_action_pins.py", doc)
        # Whitespace-tolerant: the doc is hard-wrapped, and where the line
        # breaks inside the sentence is not the contract.
        self.assertRegex(
            doc, r"Dependabot\s+proposes,\s+the\s+harness\s+proves,\s+the\s+channel\s+carries"
        )


class DependabotMajorIgnoreTest(unittest.TestCase):
    """DRE-2064's house shape, copied onto the github-actions entry.

    Not a new policy: agent-bureau's `.github/dependabot.yml` already runs it
    and this repo's `docs/dependabot-major-rejection.md` describes the template.
    A SHA pin makes every vendor release a PR, so without this a major arrives
    every sweep and burns a critic review per rung of the walk-down. The sweep
    is monthly since DRE-3829, the fleet's one Dependabot shape.
    """

    def _github_actions_entry(self):
        doc = yaml.safe_load(DEPENDABOT.read_text())
        entries = {u["package-ecosystem"]: u for u in doc["updates"]}
        self.assertIn("github-actions", entries)
        return entries["github-actions"]

    def test_majors_are_ignored_for_every_dependency(self):
        entry = self._github_actions_entry()
        ignore = entry.get("ignore")
        self.assertIsInstance(ignore, list, "github-actions entry carries no ignore:")
        self.assertIn(
            {"dependency-name": "*", "update-types": [MAJOR_IGNORE_TYPE]},
            [dict(rule) for rule in ignore],
            f"the DRE-2064 house major-ignore shape is missing: {ignore}",
        )

    def test_the_entry_stays_monthly_and_grouped_minor_plus_patch(self):
        entry = self._github_actions_entry()
        self.assertEqual(entry.get("schedule", {}).get("interval"), "monthly")
        groups = entry.get("groups") or {}
        self.assertEqual(len(groups), 1, groups)
        (group,) = groups.values()
        self.assertEqual(group.get("patterns"), ["*"])
        self.assertEqual(sorted(group.get("update-types") or []), ["minor", "patch"])


class PinLedgerTest(unittest.TestCase):
    """Every distinct action resolves to exactly ONE sha across the tree.

    Two shas for one action is the drift a grouped Dependabot bump leaves when
    it misses a file — and the missed one is the one still running last week's
    vendor code.
    """

    def test_one_sha_per_action(self):
        seen = {}
        for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(ACTIONS.glob("*/action.yml")):
            for ref in check_action_pins.references(path):
                if ref.exempt or ref.local:
                    continue
                seen.setdefault(ref.action, set()).add(ref.ref)
        drifted = {a: sorted(s) for a, s in seen.items() if len(s) > 1}
        self.assertEqual(drifted, {}, f"one action pinned at two shas: {drifted}")

    def test_the_version_comment_agrees_across_every_copy_of_an_action(self):
        seen = {}
        for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(ACTIONS.glob("*/action.yml")):
            for ref in check_action_pins.references(path):
                if ref.exempt or ref.local:
                    continue
                version = check_action_pins.VERSION_COMMENT_RE.match(ref.comment or "")
                if version:
                    seen.setdefault(ref.action, set()).add(version.group(1))
        drifted = {a: sorted(s) for a, s in seen.items() if len(s) > 1}
        self.assertEqual(drifted, {}, f"one action, two version comments: {drifted}")


# --------------------------------------------------------------------------
# DRE-6770 — one file, carried in two repositories.
#
# agent-bureau carries a file of the same name (the two-copies rule, DRE-3440)
# and its fleet sweep imports a different surface from it: `UsesRef`,
# `uses_refs`, `floating`, `problems`, `scan(root)`, `--root`, `--list`, the
# `docker://` exemption and the scaffold directory. The classes below pin that
# half, so the file agent-bureau copies byte for byte carries what its callers
# read. The contract is spelled out on DRE-6770, shared with DRE-6769.
# --------------------------------------------------------------------------

CONTRACT_NAMES = (
    "UsesRef", "uses_refs", "is_third_party", "is_pinned", "floating",
    "problems", "problems_for", "SCANNED_DIRS", "DEFAULT_ROOT",
    "EXEMPT_PREFIXES", "scanned_files", "scan", "references", "iter_files",
    "DEFAULT_PATHS", "check_paths", "resolve_tag", "resolve_paths",
    "Unresolved", "main", "VERSION_COMMENT_RE", "SHA_RE", "EXEMPT_OWNER",
)

SITES = textwrap.dedent(f"""
    jobs:
      a:
        steps:
          # - uses: actions/checkout@v5
          - uses: actions/checkout@{SHA} # v7.0.1
          - uses: "actions/setup-python@v7"
          - uses: './.github/actions/setup-python-cached'
          - uses: docker://alpine:3.20
      b:
        uses: dreadnought-foundry/bureau-pipeline/.github/workflows/plan.yml@stable
""").lstrip("\n")


class SurfaceTest(unittest.TestCase):
    """Every name both repositories' callers import, present and shaped."""

    def test_the_whole_contract_is_importable(self):
        missing = [n for n in CONTRACT_NAMES if not hasattr(check_action_pins, n)]
        self.assertEqual(missing, [])

    def test_the_constants_are_the_contract(self):
        self.assertEqual(check_action_pins.SCANNED_DIRS, (
            ".github/workflows", ".github/actions",
            "scaffold/customer-repo/.github/workflows"))
        self.assertEqual(check_action_pins.EXEMPT_PREFIXES,
                         ("./", "docker://", "dreadnought-foundry/"))
        self.assertEqual(check_action_pins.DEFAULT_ROOT, ROOT)
        self.assertEqual(check_action_pins.REPO_ROOT, ROOT)

    def test_default_paths_are_derived_from_the_scanned_dirs(self):
        """One list, never two: the positional default is the scanned set."""
        self.assertEqual(
            tuple(check_action_pins.DEFAULT_PATHS),
            tuple(ROOT / d for d in check_action_pins.SCANNED_DIRS))

    def test_uses_ref_is_a_frozen_dataclass_with_the_contract_fields(self):
        import dataclasses
        self.assertEqual(
            [f.name for f in dataclasses.fields(check_action_pins.UsesRef)],
            ["path", "line", "uses", "ref", "comment"])
        site = check_action_pins.UsesRef("w.yml", 1, "a/b@v1", "v1", "")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            site.line = 2

    def test_the_file_imports_only_the_standard_library(self):
        import ast
        tree = ast.parse(CHECKER.read_text())
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add((node.module or "").split(".")[0])
        self.assertEqual(sorted(modules - set(sys.stdlib_module_names)), [])

    def test_the_docstring_says_it_is_one_file_in_two_repositories(self):
        doc = " ".join((check_action_pins.__doc__ or "").split())
        for phrase in ("one file", "two repositories", "agent-bureau",
                       "parity test", "scripts/mirror_check.py", "byte for byte"):
            self.assertIn(phrase, doc)


class UsesRefsTest(unittest.TestCase):
    """`uses_refs(text, path)` — every site in one file's text, in line order."""

    def test_a_site_records_path_line_uses_ref_and_comment(self):
        sites = check_action_pins.uses_refs(SITES, "w.yml")
        self.assertEqual(sites[0], check_action_pins.UsesRef(
            path="w.yml", line=5, uses=f"actions/checkout@{SHA}", ref=SHA,
            comment="# v7.0.1"))

    def test_every_site_exempt_ones_included_in_line_order(self):
        sites = check_action_pins.uses_refs(SITES, "w.yml")
        self.assertEqual([(s.line, s.uses) for s in sites], [
            (5, f"actions/checkout@{SHA}"),
            (6, "actions/setup-python@v7"),
            (7, "./.github/actions/setup-python-cached"),
            (8, "docker://alpine:3.20"),
            (10, "dreadnought-foundry/bureau-pipeline/.github/workflows/plan.yml@stable"),
        ])

    def test_a_commented_out_line_is_not_a_site(self):
        sites = check_action_pins.uses_refs(SITES)
        self.assertNotIn(4, [s.line for s in sites])
        self.assertEqual(check_action_pins.uses_refs(
            "    # - uses: actions/checkout@v5\n#uses: a/b@v1\n"), [])

    def test_a_quoted_value_is_read_without_quotes(self):
        sites = check_action_pins.uses_refs(SITES)
        self.assertEqual(sites[1].uses, "actions/setup-python@v7")
        self.assertEqual(sites[1].ref, "v7")
        self.assertEqual(sites[2].uses, "./.github/actions/setup-python-cached")

    def test_an_absent_comment_is_empty_and_the_path_defaults_empty(self):
        (site,) = check_action_pins.uses_refs("- uses: actions/checkout@v7\n")
        self.assertEqual(site.comment, "")
        self.assertEqual(site.path, "")

    def test_the_ref_is_the_text_after_the_last_at(self):
        (site,) = check_action_pins.uses_refs(
            "- uses: docker://alpine@sha256:0123abcd\n")
        self.assertEqual(site.ref, "sha256:0123abcd")
        (site,) = check_action_pins.uses_refs(f"- uses: a/b@c@{SHA}\n")
        self.assertEqual(site.ref, SHA)

    def test_references_carry_the_same_sites(self):
        """The two readers are one: `references(path)` is `uses_refs` on the
        file's text, with this repository's extra fields."""
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = tmp / "w.yml"
        path.write_text(SITES)
        refs = check_action_pins.references(path)
        sites = check_action_pins.uses_refs(SITES, str(path))
        self.assertEqual([(r.line, r.uses, r.ref) for r in refs],
                         [(s.line, s.uses, s.ref) for s in sites])


class ExemptionTest(unittest.TestCase):
    """One rule: third-party unless `./`, `docker://` or `dreadnought-foundry/`."""

    def test_the_three_exempt_prefixes_are_not_third_party(self):
        for uses in ("./x", "./.github/actions/setup-python-cached",
                     "docker://alpine:3.20",
                     "dreadnought-foundry/bureau-pipeline/.github/workflows/plan.yml@stable"):
            with self.subTest(uses=uses):
                self.assertFalse(check_action_pins.is_third_party(uses))

    def test_everything_else_is_third_party(self):
        for uses in ("actions/checkout@v7", "dreadnought-foundryx/a@v1",
                     "evil/dreadnought-foundry@v1", "../x", "docker/login-action@v3"):
            with self.subTest(uses=uses):
                self.assertTrue(check_action_pins.is_third_party(uses))

    def test_a_docker_reference_is_exempt_in_this_repository_too(self):
        """`Reference.exempt` / `.local` derive from the same rule, so the
        checker this repository runs exempts `docker://` like agent-bureau's."""
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = write_workflow(tmp, "w.yml", f"""
            jobs:
              a:
                steps:
                  - uses: docker://alpine:3.20
                  - uses: ./x
                  - uses: actions/checkout@{SHA} # v7.0.1
        """)
        refs = check_action_pins.references(path)
        self.assertEqual([(r.local, r.exempt) for r in refs],
                         [(False, True), (True, False), (False, False)])
        self.assertEqual(check_action_pins.check_paths([path]), [])


class PinnedAndFloatingTest(unittest.TestCase):

    def test_is_pinned_is_exactly_forty_lowercase_hex(self):
        self.assertTrue(check_action_pins.is_pinned(SHA))
        for ref in (SHA.upper(), SHA[:7], SHA + "0", "z" * 40, "v7", ""):
            with self.subTest(ref=ref):
                self.assertFalse(check_action_pins.is_pinned(ref))

    def test_floating_is_third_party_sites_whose_ref_is_not_a_sha(self):
        found = check_action_pins.floating(SITES, "w.yml")
        self.assertEqual([(s.line, s.uses) for s in found],
                         [(6, "actions/setup-python@v7")])

    def test_floating_never_reads_the_comment(self):
        """A bare sha with no comment is not floating, and a floating tag under
        a perfect comment — even one that names a sha — still is."""
        text = textwrap.dedent(f"""
            - uses: actions/checkout@{SHA}
            - uses: actions/cache@v6 # v6.1.0
            - uses: actions/setup-node@v4 # {SHA}
        """)
        self.assertEqual([s.line for s in check_action_pins.floating(text)], [3, 4])


class ProblemsTest(unittest.TestCase):
    """`problems()` — agent-bureau's line shape, `<path>:<line>: <uses> — <reason>`."""

    def _one(self, line, path="w.yml"):
        found = check_action_pins.problems(f"- uses: {line}\n", path)
        self.assertEqual(len(found), 1, found)
        return found[0]

    def test_a_floating_tag_names_its_site_and_says_floating(self):
        problem = self._one("actions/checkout@v7")
        self.assertTrue(problem.startswith("w.yml:1: actions/checkout@v7 — "), problem)
        self.assertIn("floating", problem)

    def test_a_short_or_uppercase_sha_says_so(self):
        for ref in (SHA[:7], SHA.upper()):
            with self.subTest(ref=ref):
                problem = self._one(f"actions/checkout@{ref} # v7.0.1")
                self.assertIn("short or uppercase SHA", problem)

    def test_a_pin_without_a_version_comment_says_version_comment(self):
        self.assertIn("version comment", self._one(f"actions/checkout@{SHA}"))

    def test_a_two_component_version_comment_is_a_problem(self):
        """The stricter rule of the two copies: exactly three components."""
        self.assertIn("version comment", self._one(f"actions/checkout@{SHA} # v7.1"))

    def test_a_clean_pin_and_every_exempt_site_have_none(self):
        text = SITES.replace('"actions/setup-python@v7"',
                             f'"actions/setup-python@{SHA}" # v7.0.1')
        self.assertEqual(check_action_pins.problems(text, "w.yml"), [])

    def test_problems_is_problems_for_over_every_site(self):
        text = textwrap.dedent(f"""
            - uses: actions/checkout@v7
            - uses: actions/cache@{SHA}
            - uses: docker://alpine:3.20
        """)
        expected = [p for s in check_action_pins.uses_refs(text, "w.yml")
                    for p in check_action_pins.problems_for(s)]
        self.assertEqual(len(expected), 2)
        self.assertEqual(check_action_pins.problems(text, "w.yml"), expected)


class VersionCommentRuleTest(unittest.TestCase):
    """One rule for the version comment: exactly three numeric components."""

    def test_the_release_is_capture_group_one(self):
        match = check_action_pins.VERSION_COMMENT_RE.match("# v1.2.3 (DRE-3416)")
        self.assertEqual(match.group(1), "v1.2.3")

    def test_two_components_name_no_release(self):
        self.assertIsNone(check_action_pins.VERSION_COMMENT_RE.match("# v1.2"))

    def test_this_repositorys_check_fails_a_two_component_comment(self):
        """Before DRE-6770 this repository's copy accepted `# v7.1`; the two
        rules are one now, and a pin it accepts agent-bureau accepts."""
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = write_workflow(tmp, "w.yml", f"""
            jobs:
              a:
                steps:
                  - uses: actions/checkout@{SHA} # v7.1
        """)
        violations = check_action_pins.check_paths([path])
        self.assertEqual(len(violations), 1, violations)
        self.assertIn("w.yml:4", violations[0])


def make_tree(root: Path, scaffold=True) -> Path:
    """A scratch repository with a site in every scanned directory."""
    files = {
        ".github/workflows/ci.yml": f"""
            jobs:
              a:
                steps:
                  - uses: actions/checkout@{SHA} # v7.0.1
                  - uses: ./.github/actions/local
        """,
        ".github/actions/local/action.yml": """
            runs:
              steps:
                - uses: docker://alpine:3.20
        """,
        ".github/actions/group/nested/action.yaml": f"""
            runs:
              steps:
                - uses: actions/cache@{SHA} # v6.1.0
        """,
    }
    if scaffold:
        files["scaffold/customer-repo/.github/workflows/stub.yml"] = """
            jobs:
              a:
                uses: dreadnought-foundry/bureau-pipeline/.github/workflows/plan.yml@stable
        """
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body).lstrip("\n"))
    return root


class ScanRootTest(unittest.TestCase):
    """`scan(root)` / `scanned_files(root)` — the scanned set, read off a root."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_scan_reads_all_three_scanned_dirs_relative_and_posix(self):
        root = make_tree(self.tmp)
        sites = check_action_pins.scan(root)
        self.assertEqual(sorted((s.path, s.line) for s in sites), [
            (".github/actions/group/nested/action.yaml", 3),
            (".github/actions/local/action.yml", 3),
            (".github/workflows/ci.yml", 4),
            (".github/workflows/ci.yml", 5),
            ("scaffold/customer-repo/.github/workflows/stub.yml", 3),
        ])
        self.assertTrue(all(isinstance(s, check_action_pins.UsesRef) for s in sites))

    def test_scanned_files_walks_the_actions_directory_recursively(self):
        root = make_tree(self.tmp)
        files = check_action_pins.scanned_files(root)
        self.assertIn(root / ".github/actions/group/nested/action.yaml", files)
        self.assertEqual(len(files), 4, files)

    def test_an_absent_scaffold_directory_is_skipped_silently(self):
        root = make_tree(self.tmp, scaffold=False)
        sites = check_action_pins.scan(root)
        self.assertEqual(len(sites), 4)
        self.assertFalse(any(s.path.startswith("scaffold/") for s in sites))


class RootAndListCliTest(unittest.TestCase):
    """`--root DIR` and `--list`, end to end through the real CLI."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(CHECKER), *args],
            capture_output=True, text=True, cwd=self.tmp,
        )

    def _dirty(self, root):
        path = root / ".github/workflows/dirty.yml"
        path.write_text("jobs:\n  a:\n    steps:\n      - uses: actions/setup-node@v4\n")
        return path

    def test_root_scans_another_tree(self):
        root = make_tree(self.tmp / "repo")
        proc = self._run("--root", str(root))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("checked 4 files: 2 third-party", proc.stdout)
        self._dirty(root)
        proc = self._run("--root", str(root))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("FAIL dirty.yml:4", proc.stdout)

    def test_positional_paths_win_over_root(self):
        clean = make_tree(self.tmp / "clean")
        dirty = self._dirty(make_tree(self.tmp / "other"))
        proc = self._run("--root", str(clean), str(dirty))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("checked 1 files", proc.stdout)

    def test_list_labels_every_site_and_names_the_exempt_prefixes(self):
        root = make_tree(self.tmp / "repo")
        self._dirty(root)
        proc = self._run("--root", str(root), "--list")
        lines = proc.stdout.splitlines()
        for prefix in ("./", "docker://", "dreadnought-foundry/"):
            self.assertIn(prefix, lines[0])
        for expected in (
            f"pinned .github/workflows/ci.yml:4: actions/checkout@{SHA}",
            "exempt .github/workflows/ci.yml:5: ./.github/actions/local",
            "floating .github/workflows/dirty.yml:4: actions/setup-node@v4",
            "exempt .github/actions/local/action.yml:3: docker://alpine:3.20",
            f"pinned .github/actions/group/nested/action.yaml:3: actions/cache@{SHA}",
            "exempt scaffold/customer-repo/.github/workflows/stub.yml:3: "
            "dreadnought-foundry/bureau-pipeline/.github/workflows/plan.yml@stable",
        ):
            self.assertIn(expected, lines)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)

    def test_list_over_a_clean_tree_exits_zero(self):
        root = make_tree(self.tmp / "repo")
        proc = self._run("--root", str(root), "--list")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        labels = [line.split()[0] for line in proc.stdout.splitlines()[1:6]]
        self.assertEqual(sorted(labels),
                         ["exempt", "exempt", "exempt", "pinned", "pinned"])

    def test_an_unknown_option_is_a_defect_not_an_unknown(self):
        """Exit 2 means only `--resolve` unknowns; a typo is exit 1."""
        proc = self._run("--resovle")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
