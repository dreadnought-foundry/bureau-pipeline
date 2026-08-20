"""Integration scenarios and adversarial inputs for setup-node-cached (DRE-2550).

tests/test_shared_node_action.py checks the action's parts. This file checks
what the parts DO together, because every failure mode that matters here is a
composed one:

  * a cache HIT that still runs `npm ci` — the action costs wall-clock and
    saves nothing, and looks fine in every per-step assertion
  * a cache HIT that re-uploads the cache it just restored
  * a MISS that installs and never saves — every run is a miss, forever
  * `install: false` that still demands a lockfile
  * `~/.npm` restored on a hit, where npm is never going to run

None of those is visible in one step. They live in the `if:` conditions and the
conditional `cache:` expression, which is exactly the code no YAML parse and no
linter evaluates. So this file EVALUATES them, for every scenario, and asserts
the decision table.

The evaluator is deliberately strict and tiny: it supports only the expression
subset this action uses, and RAISES on anything it does not understand rather
than guessing. An evaluator that silently returned False on an unknown token
would turn every assertion below into a vacuous pass — the Wave 0 failure
mode — so EvaluatorItself below tests the evaluator, including that it refuses
what it cannot parse.

GitHub's `&&`/`||` return an OPERAND, not a boolean ('' is falsy, any non-empty
string is truthy), which is the whole mechanism behind
`cond && 'npm' || ''`. The evaluator reproduces that, not Python's semantics.
"""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
NODE_ACTION = ROOT / ".github" / "actions" / "setup-node-cached" / "action.yml"
README = ROOT / "README.md"


# --- a strict, minimal GitHub-expression evaluator ---------------------------

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<str>'(?:[^']|'')*')
      | (?P<op>==|!=|&&|\|\||\(|\)|,)
      | (?P<path>[A-Za-z_][A-Za-z0-9_.\-]*)
    )""",
    re.X,
)


class ExpressionError(Exception):
    """Raised on anything the evaluator does not understand. Never swallowed."""


def _tokenize(src):
    pos, out = 0, []
    while pos < len(src):
        if src[pos].isspace():
            pos += 1
            continue
        m = _TOKEN.match(src, pos)
        if not m:
            raise ExpressionError(f"cannot tokenize at {src[pos:][:20]!r} in {src!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


def _truthy(value):
    # GitHub: '' and false are falsy; any other string is truthy.
    return bool(value) if not isinstance(value, str) else value != ""


class _Parser:
    def __init__(self, tokens, contexts):
        self.tokens, self.i, self.contexts = tokens, 0, contexts

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)

    def eat(self, value=None):
        kind, tok = self.peek()
        if kind is None or (value is not None and tok != value):
            raise ExpressionError(f"expected {value!r}, found {tok!r}")
        self.i += 1
        return tok

    def parse(self):
        value = self.or_()
        if self.i != len(self.tokens):
            raise ExpressionError(f"trailing tokens from {self.tokens[self.i:]}")
        return value

    def or_(self):
        left = self.and_()
        while self.peek()[1] == "||":
            self.eat()
            right = self.and_()
            left = left if _truthy(left) else right
        return left

    def and_(self):
        left = self.cmp_()
        while self.peek()[1] == "&&":
            self.eat()
            right = self.cmp_()
            left = right if _truthy(left) else left
        return left

    def cmp_(self):
        left = self.atom()
        while self.peek()[1] in ("==", "!="):
            op = self.eat()
            right = self.atom()
            left = (left == right) if op == "==" else (left != right)
        return left

    def atom(self):
        kind, tok = self.peek()
        if tok == "(":
            self.eat("(")
            value = self.or_()
            self.eat(")")
            return value
        if kind == "str":
            self.eat()
            return tok[1:-1].replace("''", "'")
        if kind == "path":
            self.eat()
            if self.peek()[1] == "(":
                return self.call(tok)
            return self.lookup(tok)
        raise ExpressionError(f"unexpected token {tok!r}")

    def call(self, name):
        if name != "format":
            raise ExpressionError(f"unsupported function {name!r}")
        self.eat("(")
        args = [self.or_()]
        while self.peek()[1] == ",":
            self.eat(",")
            args.append(self.or_())
        self.eat(")")
        template = args[0]
        for n, value in enumerate(args[1:]):
            template = template.replace("{%d}" % n, str(value))
        return template

    def lookup(self, path):
        node = self.contexts
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise ExpressionError(
                    f"unknown context {path!r} — the evaluator refuses to guess; "
                    f"add it to the scenario's contexts"
                )
            node = node[part]
        return node


def evaluate(expression, contexts):
    """Evaluate one GitHub expression (without the ${{ }} wrapper)."""
    return _Parser(_tokenize(expression), contexts).parse()


def interpolate(template, contexts):
    """Resolve every ${{ ... }} inside a string, as a workflow value would be."""
    if not isinstance(template, str):
        return template

    def sub(match):
        value = evaluate(match.group(1), contexts)
        return "" if value is False else ("true" if value is True else str(value))

    return re.sub(r"\$\{\{(.*?)\}\}", sub, template)


def step_runs(step, contexts):
    """Would GitHub run this step? A bare `if:` is an implicit expression."""
    if "if" not in step:
        return True
    condition = str(step["if"]).strip()
    if condition.startswith("${{"):
        condition = condition[3:].rstrip("}").strip()
    return _truthy(evaluate(condition, contexts))


# --- the scenarios ----------------------------------------------------------


def _contexts(install="true", cache_hit="", working_directory="console/web"):
    return {
        "inputs": {
            "install": install,
            "working-directory": working_directory,
            "node-version": "",
            "node-version-file": "console/web/.nvmrc",
        },
        "steps": {
            "restore": {"outputs": {"cache-hit": cache_hit}},
            "key": {"outputs": {"key": "node-modules-v1-Linux-X64-console-web-abc"}},
        },
        "github": {"workspace": "/home/runner/work/repo/repo"},
    }


class Scenario(unittest.TestCase):
    """Walk the action's real steps under real contexts."""

    def setUp(self):
        self.assertTrue(NODE_ACTION.is_file(), f"{NODE_ACTION} is missing")
        self.steps = yaml.safe_load(NODE_ACTION.read_text())["runs"]["steps"]

    def _ran(self, contexts):
        return {
            str(s.get("name", s.get("uses", "?"))): s
            for s in self.steps
            if step_runs(s, contexts)
        }

    def _named(self, ran, fragment):
        for name in ran:
            if fragment.lower() in name.lower():
                return ran[name]
        return None

    def _setup_node(self, contexts):
        for step in self.steps:
            if str(step.get("uses", "")).startswith("actions/setup-node"):
                return {
                    k: interpolate(v, contexts) for k, v in (step.get("with") or {}).items()
                }
        raise AssertionError("the action no longer sets up node")

    # -- scenario 1: cold cache, the first run on a branch -------------------

    def test_cold_cache_installs_and_saves(self):
        ctx = _contexts(cache_hit="")
        ran = self._ran(ctx)
        self.assertIsNotNone(self._named(ran, "compute the node_modules cache key"))
        self.assertIsNotNone(self._named(ran, "restore node_modules"))
        self.assertIsNotNone(
            self._named(ran, "install dependencies"),
            "a MISS did not install — the suite would run with no dependencies",
        )
        self.assertIsNotNone(
            self._named(ran, "save node_modules"),
            "a MISS installed and did not save — every future run is a miss too, "
            "so the action would cost wall-clock forever and save nothing",
        )

    def test_cold_cache_restores_the_npm_download_cache(self):
        # On a miss npm IS about to run, so ~/.npm is worth having.
        with_ = self._setup_node(_contexts(cache_hit=""))
        self.assertEqual(with_["cache"], "npm")
        self.assertEqual(with_["cache-dependency-path"], "console/web/package-lock.json")

    # -- scenario 2: warm cache — the point of the whole action ---------------

    def test_warm_cache_skips_the_install(self):
        ctx = _contexts(cache_hit="true")
        ran = self._ran(ctx)
        self.assertIsNone(
            self._named(ran, "install dependencies"),
            "a cache HIT still ran npm ci — this is the entire saving, and its "
            "absence is invisible in every per-step assertion",
        )

    def test_warm_cache_does_not_re_upload_the_cache(self):
        ran = self._ran(_contexts(cache_hit="true"))
        self.assertIsNone(
            self._named(ran, "save node_modules"),
            "a HIT re-saved the cache it just restored — paying upload cost on "
            "every run for a byte-identical entry",
        )

    def test_warm_cache_does_not_restore_the_npm_download_cache(self):
        # npm is never going to run, so fetching its download cache is pure
        # wall-clock — a smaller version of the waste being fixed.
        with_ = self._setup_node(_contexts(cache_hit="true"))
        self.assertEqual(with_["cache"], "")
        self.assertEqual(with_["cache-dependency-path"], "")

    def test_warm_cache_still_sets_up_node(self):
        # node itself is not cached by us. If this step were skipped on a hit,
        # the job would have node_modules and no node.
        ran = self._ran(_contexts(cache_hit="true"))
        self.assertIsNotNone(self._named(ran, "set up node"))

    # -- scenario 3: node only, no install -----------------------------------

    def test_install_false_touches_no_cache_and_needs_no_lockfile(self):
        ctx = _contexts(install="false")
        ran = self._ran(ctx)
        for fragment in (
            "compute the node_modules cache key",
            "restore node_modules",
            "install dependencies",
            "save node_modules",
        ):
            self.assertIsNone(
                self._named(ran, fragment),
                f"{fragment!r} ran with install: false — the key step REQUIRES a "
                f"lockfile, so a node-only job in a repo without one would fail",
            )
        self.assertIsNotNone(self._named(ran, "set up node"))

    # -- the paths the steps actually address --------------------------------

    def test_cache_path_is_the_callers_node_modules(self):
        ctx = _contexts(working_directory="website")
        for fragment in ("restore node_modules", "save node_modules"):
            step = self._named(self._ran(ctx), fragment)
            self.assertEqual(
                interpolate(step["with"]["path"], ctx), "website/node_modules"
            )

    def test_install_runs_workspace_anchored(self):
        # A caller's job-level defaults.run.working-directory must not be able
        # to turn `infra` into `infra/infra`. agent-bureau's infra-ci.yml sets
        # exactly such a default.
        ctx = _contexts(working_directory="infra")
        step = self._named(self._ran(ctx), "install dependencies")
        self.assertEqual(
            interpolate(step["working-directory"], ctx),
            "/home/runner/work/repo/repo/infra",
        )

    def test_the_key_script_anchors_at_the_workspace_too(self):
        step = [s for s in self.steps if s.get("id") == "key"][0]
        self.assertIn('cd "$GITHUB_WORKSPACE"', step["run"])


class EvaluatorItself(unittest.TestCase):
    """The evaluator is load-bearing, so it is tested rather than trusted.

    If it silently returned False for anything it could not parse, every
    Scenario assertion above would pass for the wrong reason.
    """

    def test_github_and_or_return_operands_not_booleans(self):
        ctx = {"a": {"b": "true"}}
        self.assertEqual(evaluate("a.b == 'true' && 'npm' || ''", ctx), "npm")
        self.assertEqual(evaluate("a.b == 'nope' && 'npm' || ''", ctx), "")

    def test_empty_string_is_falsy_and_any_other_string_is_truthy(self):
        self.assertEqual(evaluate("'' || 'fallback'", {}), "fallback")
        self.assertEqual(evaluate("'x' || 'fallback'", {}), "x")

    def test_format_substitutes_positionally(self):
        ctx = {"inputs": {"wd": "console/web"}}
        self.assertEqual(
            evaluate("format('{0}/package-lock.json', inputs.wd)", ctx),
            "console/web/package-lock.json",
        )

    def test_parentheses_bind_before_the_or(self):
        ctx = {"i": {"x": "false", "y": "true"}}
        self.assertEqual(
            evaluate("(i.x == 'true' && i.y == 'true') && 'yes' || 'no'", ctx), "no"
        )

    def test_unknown_context_raises_rather_than_guessing(self):
        with self.assertRaises(ExpressionError):
            evaluate("steps.nope.outputs.thing == 'true'", {"steps": {}})

    def test_unsupported_function_raises(self):
        with self.assertRaises(ExpressionError):
            evaluate("hashFiles('x')", {})

    def test_garbage_raises(self):
        for bad in ("a.b ===", "&&", "format(", "'unterminated"):
            with self.assertRaises(ExpressionError, msg=bad):
                evaluate(bad, {"a": {"b": "1"}})

    def test_step_runs_handles_both_if_spellings(self):
        ctx = {"inputs": {"install": "true"}}
        self.assertTrue(step_runs({"if": "inputs.install == 'true'"}, ctx))
        self.assertTrue(step_runs({"if": "${{ inputs.install == 'true' }}"}, ctx))
        self.assertFalse(step_runs({"if": "inputs.install == 'false'"}, ctx))
        self.assertTrue(step_runs({}, ctx))


# --- adversarial inputs, executed for real ----------------------------------


def _run_key(script, working_directory, node_version="", node_version_file="",
             lockfile=True, lockfile_bytes='{"lockfileVersion": 3}\n'):
    """Execute the key step in a throwaway workspace. Returns (rc, combined)."""
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "workspace"
        target = workspace / working_directory
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            workspace.mkdir(parents=True, exist_ok=True)
        if lockfile and target.is_dir():
            (target / "package-lock.json").write_text(lockfile_bytes)
        if node_version_file and target.is_dir():
            (target / Path(node_version_file).name).write_text("24\n")
            node_version_file = str(Path(working_directory) / Path(node_version_file).name)
        out = Path(tmp) / "out"
        out.touch()
        canary = Path(tmp) / "PWNED"
        proc = subprocess.run(
            ["bash", "-c", script],
            cwd=str(workspace) if workspace.is_dir() else tmp,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "GITHUB_WORKSPACE": str(workspace),
                "WORKING_DIRECTORY": working_directory,
                "NODE_VERSION": node_version,
                "NODE_VERSION_FILE": node_version_file,
                "RUNNER_OS": "Linux",
                "RUNNER_ARCH": "X64",
                "GITHUB_OUTPUT": str(out),
                "CANARY": str(canary),
            },
        )
        return proc.returncode, proc.stdout + proc.stderr + out.read_text(), canary.exists()


class AdversarialInputs(unittest.TestCase):
    """The action takes a caller-supplied string and builds a shell command
    around it. Every repo in the fleet is a caller, and Step 4 hands the same
    action to four repos this session cannot even read. So the inputs are
    treated as hostile."""

    def setUp(self):
        self.assertTrue(NODE_ACTION.is_file(), f"{NODE_ACTION} is missing")
        doc = yaml.safe_load(NODE_ACTION.read_text())
        self.script = [s for s in doc["runs"]["steps"] if s.get("id") == "key"][0]["run"]

    def test_command_substitution_in_the_directory_does_not_execute(self):
        # The value reaches the script through env, never interpolated into the
        # shell line, so this must be treated as a (nonexistent) path.
        rc, out, pwned = _run_key(self.script, '$(touch "$CANARY")', lockfile=False)
        self.assertFalse(pwned, "command substitution in working-directory EXECUTED")
        self.assertEqual(rc, 1)
        self.assertIn("no package-lock.json", out)

    def test_semicolon_injection_in_the_directory_does_not_execute(self):
        rc, out, pwned = _run_key(self.script, '; touch "$CANARY"; echo', lockfile=False)
        self.assertFalse(pwned, "a semicolon in working-directory reached the shell")
        self.assertEqual(rc, 1)

    def test_backtick_injection_in_the_node_version_does_not_execute(self):
        rc, out, pwned = _run_key(
            self.script, "web", node_version='`touch "$CANARY"`'
        )
        self.assertFalse(pwned, "backticks in node-version were evaluated")
        self.assertEqual(rc, 0, out)

    def test_a_directory_with_spaces_still_works(self):
        rc, out, _ = _run_key(self.script, "my project/web app", node_version="24")
        self.assertEqual(rc, 0, f"unquoted path handling broke on spaces:\n{out}")
        self.assertIn("key=", out)

    def test_a_missing_lockfile_fails_here_not_ninety_seconds_later(self):
        rc, out, _ = _run_key(self.script, "web", node_version="24", lockfile=False)
        self.assertEqual(rc, 1)
        self.assertIn("no package-lock.json", out)

    def test_no_node_version_at_all_is_refused(self):
        # Falling through would key on an empty node identity, so two majors
        # would share one cache entry.
        rc, out, _ = _run_key(self.script, "web")
        self.assertEqual(rc, 1)
        self.assertIn("neither node-version nor node-version-file", out)

    def test_a_ragged_nvmrc_does_not_leak_whitespace_into_the_key(self):
        rc, out, _ = _run_key(self.script, "web", node_version_file=".nvmrc")
        self.assertEqual(rc, 0, out)
        key = [ln for ln in out.splitlines() if ln.startswith("key=")][0]
        self.assertNotIn(" ", key, f"whitespace reached the cache key: {key!r}")
        self.assertEqual(key, key.strip())

    def test_the_key_is_deterministic_across_runs(self):
        # A key that varied per run would never hit, and the action would look
        # like it worked while saving nothing.
        first = _run_key(self.script, "web", node_version="24")[1]
        second = _run_key(self.script, "web", node_version="24")[1]
        get = lambda o: [ln for ln in o.splitlines() if ln.startswith("key=")][0]
        self.assertEqual(get(first), get(second))

    def test_a_changed_lockfile_changes_the_key(self):
        a = _run_key(self.script, "web", node_version="24", lockfile_bytes='{"a": 1}')[1]
        b = _run_key(self.script, "web", node_version="24", lockfile_bytes='{"a": 2}')[1]
        get = lambda o: [ln for ln in o.splitlines() if ln.startswith("key=")][0]
        self.assertNotEqual(
            get(a), get(b), "the lockfile's content does not reach the key at all"
        )

    def test_two_projects_with_identical_lockfiles_get_distinct_keys(self):
        # Same bytes, different project: the slug keeps the entries apart, so a
        # human reading the cache list can tell them apart.
        a = _run_key(self.script, "web", node_version="24")[1]
        b = _run_key(self.script, "infra", node_version="24")[1]
        get = lambda o: [ln for ln in o.splitlines() if ln.startswith("key=")][0]
        self.assertNotEqual(get(a), get(b))

    def test_the_key_fits_githubs_limit(self):
        # GitHub rejects a cache key over 512 characters, and it rejects it at
        # RUN time in the consumer repo.
        rc, out, _ = _run_key(self.script, "a/" * 60 + "web", node_version="24")
        self.assertEqual(rc, 0, out)
        key = [ln for ln in out.splitlines() if ln.startswith("key=")][0][len("key="):]
        self.assertLess(len(key), 512, f"key is {len(key)} chars: {key!r}")

    def test_the_key_holds_no_characters_github_disallows(self):
        # Commas separate restore-keys, so a comma in a key is a silent split.
        rc, out, _ = _run_key(self.script, "web space/x", node_version="24")
        key = [ln for ln in out.splitlines() if ln.startswith("key=")][0][len("key="):]
        for bad in (",", " ", "\t"):
            self.assertNotIn(bad, key, f"{bad!r} in cache key {key!r}")


class DocumentedExampleMatchesTheAction(unittest.TestCase):
    """The README block is what Step 4's operator pass copies into four repos
    this session cannot read. If it drifts from the action's real inputs, the
    error surfaces in someone else's repo, on someone else's card."""

    def setUp(self):
        self.text = README.read_text()
        self.assertIn(
            "setup-node-cached",
            self.text,
            "the README no longer documents the shared action — Step 4's "
            "operator pass has nothing to copy",
        )

    def test_every_input_in_the_readme_example_is_declared(self):
        declared = set(yaml.safe_load(NODE_ACTION.read_text())["inputs"])
        # Ref-agnostic on purpose: this test must not be the thing that pins
        # `@main` as the one correct spelling (it previously did, which is how
        # an unpinned fleet-facing snippet got documented in the first place).
        block = re.split(r"setup-node-cached@\S+", self.text, maxsplit=1)[1]
        block = block.split("```", 1)[0]
        used = set(re.findall(r"^\s{4}([a-z][a-z0-9-]*):", block, re.M))
        self.assertTrue(used, "the README example passes no inputs at all")
        unknown = used - declared
        self.assertFalse(
            unknown,
            f"the README example passes {sorted(unknown)}, which the action does "
            f"not declare — a repo copying it would silently install nothing",
        )
        self.assertIn("working-directory", used, "the example omits the required input")

    def test_the_docs_do_not_offer_main_to_the_whole_fleet(self):
        """standards/engineering.md: "the fleet consumes tagged releases (`vN`
        ...), never this repo's live `main`; only agent-bureau and
        bureau-pipeline ride `@main` as the canary channel."

        A composite action is consumed by the caller's CI on its very next run,
        so an unpinned fleet-facing snippet means a bad merge here reprograms
        that repo's CI with no canary soak and no promotion step. The first
        draft of this README documented exactly that, and the critic caught it
        on DRE-2550 — so the guard exists rather than the memory of it.
        """
        refs = set(re.findall(r"setup-node-cached@(\S+)", self.text))
        self.assertTrue(refs, "the README documents no ref at all")
        if "main" in refs:
            # Allowed, but only where it is explicitly scoped to the canary.
            self.assertRegex(
                self.text,
                r"(?i)canary",
                "the README shows setup-node-cached@main without ever naming "
                "the canary restriction — a product repo would copy it",
            )
            self.assertTrue(
                refs - {"main"},
                "the README offers ONLY @main — every product repo copying it "
                "would ride live main for its CI plumbing, which is the exact "
                "risk the release channel exists to remove",
            )

    def test_the_docs_state_that_no_current_tag_contains_this_action(self):
        # v1-v5 were cut 2026-07-11..07-21 and predate the action, so a repo
        # pinning to @v5 today gets an unresolvable ref. Anyone reading the pin
        # guidance must be told that, or Step 4 fails in four repos at once.
        # Anchored on the HEADING, not the first mention — the sentence above
        # cross-references this section by name.
        self.assertIn("## Shared CI plumbing", self.text)
        section = self.text.split("## Shared CI plumbing", 1)[1].split("\n## ", 1)[0]
        self.assertRegex(
            section,
            r"(?i)no existing tag|predate",
            "the pin guidance does not say that no released tag contains this "
            "action yet — a product repo would pin to a tag without it",
        )


if __name__ == "__main__":
    unittest.main()
