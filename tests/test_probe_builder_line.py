"""The critic reads a cardless probe's builder line from its description — in the harness sandbox only (DRE-7618).

A probe pull request in `dreadnought-foundry/bureau-harness` has no card, so
`build-model` has no thread to read and the critic's Select model step passed
`--built-on-unknown`, which fails closed onto Opus 5.5. The harness driver
writes the build line into the probe's own description instead, and
`model_fallback.py probe-build-model` reads it — only in `SANDBOX_REPO`, only
from the worker App, in either spelling the API prints its login in.

What this file pins:

* `probe-build-model` prints the line's model for the worker App's two login
  spellings, and nothing for any other author, any repo in
  `config/repo-map.json`, or an unreadable file — always exiting 0;
* the critic then takes the known-builder path on the real config: an Opus
  5.5 build is reviewed on Sonnet 5.5, a Sonnet 5.5 build is not, and two
  builders keep both off;
* the Select model step of `qa-review.yml`, RUN with a stubbed `gh`: the REST
  author read, a body-only fallback read, the `probe-builder:` log line and
  `--built-on`, and a pull request with a card never reading its description;
* Haiku 5.5 stays excluded, on no ladder, and is never selected.

Run: python3 -m pytest tests/test_probe_builder_line.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"
REPO_MAP = ROOT / "config" / "repo-map.json"
MODELS = ROOT / "config" / "models.yaml"

sys.path.insert(0, str(SCRIPTS))

import model_fallback  # noqa: E402

SANDBOX = "dreadnought-foundry/bureau-harness"
OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"
REST_LOGIN = "agent-bureau-bot[bot]"
GRAPHQL_LOGIN = "app/agent-bureau-bot"
NOT_THE_APP = ["agent-bureau-bot", "app/agent-bureau-qa-bot",
               "agent-bureau-qa-bot[bot]", "agent-bureau-bot-3[bot]", ""]
HOME_NAMES = ("BUREAU_MODEL_HOME_TOKEN", "BUREAU_MODEL_HOME_FILE")
BODY_PATH = "/tmp/cardctx-pr-body.txt"


def contract_line(model: str) -> str:
    """The line the harness fixture writes, word for word (the shared contract)."""
    return (f"🧠 model-attempt: {model} — harness agent starting (a fixture: no "
            "agent built this probe; it names the model a real build runs on)")


def description(*models: str) -> str:
    return "\n".join(["Probe 17 of today's batch.", "",
                      *(contract_line(m) for m in models), "",
                      "Nothing below is a build record."]) + "\n"


def _env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in HOME_NAMES}
    env.update(BUREAU_FAKE_AVAILABLE="{}", **extra)
    return env


def probe_cli(repo: str, author: str, body: str | None) -> subprocess.CompletedProcess:
    """`probe-build-model` over a body file — or a path that is not there."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "body.txt"
        if body is not None:
            path.write_text(body, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "model_fallback.py"), "probe-build-model",
             "--repo", repo, "--author", author, str(path)],
            capture_output=True, text=True, env=_env(), timeout=120)


def select_critic(*flags: str, repo: str = SANDBOX) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "model_fallback.py"), "select", "critic",
         "--repo", repo, *flags],
        capture_output=True, text=True, env=_env(), timeout=120)


def review_from_description(body: str, author: str = REST_LOGIN,
                            repo: str = SANDBOX) -> subprocess.CompletedProcess:
    """What the step does with a cardless pull request: the probe read, then
    `--built-on` what it printed or `--built-on-unknown`."""
    built_on = probe_cli(repo, author, body).stdout.strip()
    flags = ["--built-on", built_on] if built_on else ["--built-on-unknown"]
    return select_critic(*flags, repo=repo)


# --------------------------------------------------------------------------- #
# 1. probe-build-model                                                         #
# --------------------------------------------------------------------------- #

class ProbeBuildModelTest(unittest.TestCase):
    def test_the_constants(self):
        self.assertEqual(SANDBOX, model_fallback.SANDBOX_REPO)
        self.assertEqual((REST_LOGIN, GRAPHQL_LOGIN), model_fallback.PROBE_AUTHOR_LOGINS)

    def test_the_worker_app_in_either_spelling_names_the_builder(self):
        for author in (REST_LOGIN, GRAPHQL_LOGIN):
            with self.subTest(author=author):
                proc = probe_cli(SANDBOX, author, description(OPUS))
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertEqual(OPUS, proc.stdout.strip())

    def test_the_repo_is_compared_case_insensitively(self):
        proc = probe_cli("Dreadnought-Foundry/Bureau-Harness", REST_LOGIN, description(OPUS))
        self.assertEqual(OPUS, proc.stdout.strip())

    def test_the_line_names_the_first_workhorse_rung(self):
        """The fixture names what a real build runs on; today that is Opus 5.5."""
        self.assertEqual(OPUS, model_fallback.ladder_for("engineer")[0])
        self.assertEqual([OPUS], model_fallback.build_models([description(OPUS)]),
                         "`harness` is an authoring role with no change to build_models")

    def test_any_other_author_prints_nothing(self):
        for author in NOT_THE_APP + ["someone", "dependabot[bot]"]:
            with self.subTest(author=author):
                proc = probe_cli(SANDBOX, author, description(OPUS))
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertEqual("", proc.stdout.strip())

    def test_no_real_repo_reads_the_description(self):
        repos = json.loads(REPO_MAP.read_text(encoding="utf-8"))
        self.assertTrue(repos)
        for slug, full in repos.items():
            for repo in (slug, full):
                with self.subTest(repo=repo):
                    proc = probe_cli(repo, REST_LOGIN, description(OPUS))
                    self.assertEqual(0, proc.returncode, proc.stderr)
                    self.assertEqual("", proc.stdout.strip())

    def test_a_real_repo_still_reviews_as_an_unknown_builder(self):
        for full in json.loads(REPO_MAP.read_text(encoding="utf-8")).values():
            with self.subTest(repo=full):
                proc = review_from_description(description(OPUS), repo=full)
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertIn("this build's model could not be read", proc.stderr)

    def test_an_unreadable_file_prints_nothing(self):
        proc = probe_cli(SANDBOX, REST_LOGIN, None)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("", proc.stdout.strip())

    def test_a_description_with_no_line_prints_nothing(self):
        proc = probe_cli(SANDBOX, REST_LOGIN, "Probe 17.\n\nNo line here.\n")
        self.assertEqual(("", 0), (proc.stdout.strip(), proc.returncode))

    def test_missing_arguments_print_nothing_and_exit_zero(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "model_fallback.py"), "probe-build-model"],
            capture_output=True, text=True, env=_env(), timeout=120)
        self.assertEqual(("", 0), (proc.stdout.strip(), proc.returncode))


# --------------------------------------------------------------------------- #
# 2. The critic on the known-builder path                                      #
# --------------------------------------------------------------------------- #

class CriticSeparationTest(unittest.TestCase):
    def test_an_opus_built_probe_is_reviewed_on_sonnet(self):
        proc = select_critic("--built-on", OPUS)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(SONNET, proc.stdout.strip())
        self.assertIn(f"this build ran on {OPUS}, so the reviewer does not", proc.stderr)

    def test_the_description_drives_the_same_selection(self):
        proc = review_from_description(description(OPUS))
        self.assertEqual(SONNET, proc.stdout.strip(), proc.stderr)
        self.assertIn(f"this build ran on {OPUS}, so the reviewer does not", proc.stderr)

    def test_a_sonnet_built_probe_is_never_reviewed_on_sonnet(self):
        proc = review_from_description(description(SONNET))
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertNotEqual(SONNET, proc.stdout.strip())
        self.assertTrue(proc.stdout.strip())

    def test_two_builders_keep_both_off(self):
        self.assertEqual(f"{OPUS},{SONNET}",
                         probe_cli(SANDBOX, REST_LOGIN, description(OPUS, SONNET)).stdout.strip())
        proc = review_from_description(description(OPUS, SONNET))
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertNotIn(proc.stdout.strip(), (OPUS, SONNET, ""))


# --------------------------------------------------------------------------- #
# 3. The Select model step, run                                                #
# --------------------------------------------------------------------------- #

FAKE_GH = textwrap.dedent("""\
    #!/usr/bin/env python3
    import os, sys
    with open(os.environ["FAKE_GH_LOG"], "a") as log:
        log.write(" ".join(sys.argv[1:]) + "\\n")
    argv = sys.argv[1:]
    if argv[:1] == ["api"] and argv[1].endswith("/pulls/" + os.environ["PR"]):
        if os.environ.get("FAKE_GH_AUTHOR") is None:
            sys.stderr.write("gh: Server Error (HTTP 502)\\n")
            sys.exit(1)
        print(os.environ["FAKE_GH_AUTHOR"])
        sys.exit(0)
    if argv[:2] == ["pr", "view"]:
        sys.stdout.write(open(os.environ["FAKE_GH_BODY"], encoding="utf-8").read())
        sys.exit(0)
    sys.exit("fake gh: unexpected call " + " ".join(argv))
    """)

# Each pipeline script the step calls, logged and handed to the real one.
WRAPPER = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, subprocess, sys
    with open(os.environ["SCRIPT_LOG"], "a") as log:
        log.write(json.dumps([os.path.basename(__file__), *sys.argv[1:]]) + "\\n")
    sys.exit(subprocess.call([sys.executable, {real!r}, *sys.argv[1:]]))
    """)


def _step() -> dict:
    steps = yaml.safe_load(QA_REVIEW.read_text(encoding="utf-8"))["jobs"]["review"]["steps"]
    return next(s for s in steps if s.get("id") == "model")


class SelectModelStepTextTest(unittest.TestCase):
    def test_the_step_carries_the_context_steps_two_env_lines(self):
        env = _step()["env"]
        self.assertEqual("${{ steps.reader.outputs.token }}", env["GH_TOKEN"])
        self.assertEqual("${{ steps.pr.outputs.number }}", env["PR"])
        self.assertEqual("${{ steps.pr.outputs.card }}", env["CARD"])

    def test_the_author_is_read_from_the_rest_api(self):
        run = _step()["run"]
        self.assertIn('AUTHOR=$(gh api "repos/$GITHUB_REPOSITORY/pulls/$PR" --jq .user.login', run)

    def test_the_fallback_body_read_asks_for_the_body_only(self):
        run = _step()["run"]
        views = [ln.strip() for ln in run.splitlines() if "gh pr view" in ln]
        self.assertTrue(views, "no fallback body read")
        for ln in views:
            self.assertIn('gh pr view "$PR" --json body --jq .body', ln)
            self.assertNotIn("author", ln)
        self.assertIn(BODY_PATH, run)

    def test_the_probe_read_is_asked_with_the_repo_and_the_author(self):
        run = _step()["run"]
        self.assertIn("model_fallback.py probe-build-model", run)
        self.assertIn('--repo "$GITHUB_REPOSITORY"', run)
        self.assertIn('--author "$AUTHOR"', run)


class SelectModelStepRunTest(unittest.TestCase):
    """The step's `run:` under Actions' own `bash -eo pipefail`, with `gh`
    stubbed and each pipeline script logged before the real one runs. The
    description file the context step writes is moved into this test's own
    directory, the one substitution made to the block."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)
        scripts = self.dir / "pipeline" / "scripts"
        scripts.mkdir(parents=True)
        for name in ("model_fallback.py", "card_snapshot.py"):
            (scripts / name).write_text(WRAPPER.format(real=str(SCRIPTS / name)),
                                        encoding="utf-8")
        self.runner_temp = self.dir / "runner"
        self.runner_temp.mkdir()
        self.body_file = self.dir / "cardctx-pr-body.txt"
        self.gh_body = self.dir / "gh-body.txt"
        self.gh_log = self.dir / "gh.log"
        self.script_log = self.dir / "scripts.log"
        self.output = self.dir / "output"
        self.summary = self.dir / "summary"
        for f in (self.gh_log, self.script_log, self.output, self.summary):
            f.write_text("", encoding="utf-8")
        self.path = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    def tearDown(self):
        self._td.cleanup()

    def run_step(self, *, card: str = "", repo: str = SANDBOX, author: str | None = REST_LOGIN,
                 body: str | None = None, gh_body: str | None = None, thread=None):
        if body is not None:
            self.body_file.write_text(body, encoding="utf-8")
        self.gh_body.write_text(gh_body or "", encoding="utf-8")
        if thread is not None:
            (self.runner_temp / "card-snapshot.json").write_text(
                json.dumps({"description": "", "comments": thread}), encoding="utf-8")
        run = _step()["run"]
        self.assertIn(BODY_PATH, run)
        script = self.dir / "step.sh"
        script.write_text(run.replace(BODY_PATH, str(self.body_file)), encoding="utf-8")
        extra = dict(
            PATH=self.path, PIPELINE_DIR=str(self.dir / "pipeline"),
            RUNNER_TEMP=str(self.runner_temp), GITHUB_OUTPUT=str(self.output),
            GITHUB_STEP_SUMMARY=str(self.summary), GITHUB_REPOSITORY=repo,
            PR="4903", CARD=card, GH_TOKEN="t", FAKE_GH_LOG=str(self.gh_log),
            FAKE_GH_BODY=str(self.gh_body), SCRIPT_LOG=str(self.script_log))
        env = _env(**extra)
        env.pop("FAKE_GH_AUTHOR", None)
        if author is not None:
            env["FAKE_GH_AUTHOR"] = author
        proc = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
                              capture_output=True, text=True, env=env, timeout=300)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        return proc

    def gh_calls(self) -> list[str]:
        return [ln for ln in self.gh_log.read_text(encoding="utf-8").splitlines() if ln]

    def script_calls(self) -> list[list[str]]:
        return [json.loads(ln) for ln in self.script_log.read_text(encoding="utf-8").splitlines()
                if ln]

    def select_flags(self) -> list[str]:
        calls = [c for c in self.script_calls() if c[:3] == ["model_fallback.py", "select", "critic"]]
        self.assertEqual(1, len(calls), self.script_calls())
        return calls[0]

    def outputs(self) -> dict:
        return dict(ln.split("=", 1) for ln in self.output.read_text(encoding="utf-8").splitlines()
                    if "=" in ln)

    def test_a_cardless_sandbox_probe_passes_its_builder(self):
        proc = self.run_step(body=description(OPUS))
        self.assertIn(f"probe-builder: {OPUS} read from the pull request description",
                      proc.stdout.splitlines())
        flags = self.select_flags()
        self.assertIn("--built-on", flags)
        self.assertEqual(OPUS, flags[flags.index("--built-on") + 1])
        self.assertNotIn("--built-on-unknown", flags)
        self.assertIn(f"api repos/{SANDBOX}/pulls/4903 --jq .user.login", self.gh_calls())
        self.assertEqual(SONNET, self.outputs()["model"])
        probe = [c for c in self.script_calls() if c[1:2] == ["probe-build-model"]]
        self.assertEqual([["model_fallback.py", "probe-build-model", "--repo", SANDBOX,
                           "--author", REST_LOGIN, str(self.body_file)]], probe)

    def test_with_no_context_file_the_body_is_read_once_and_only_the_body(self):
        proc = self.run_step(gh_body=description(OPUS))
        self.assertIn(f"probe-builder: {OPUS} read from the pull request description",
                      proc.stdout.splitlines())
        views = [c for c in self.gh_calls() if c.startswith("pr view")]
        self.assertEqual(["pr view 4903 --json body --jq .body"], views)
        flags = self.select_flags()
        self.assertEqual(OPUS, flags[flags.index("--built-on") + 1])

    def test_a_failed_author_read_keeps_the_unknown_builder(self):
        proc = self.run_step(author=None, body=description(OPUS))
        self.assertIn("--built-on-unknown", self.select_flags())
        self.assertNotIn("probe-builder:", proc.stdout)

    def test_another_author_keeps_the_unknown_builder(self):
        proc = self.run_step(author="agent-bureau-qa-bot[bot]", body=description(OPUS))
        self.assertIn("--built-on-unknown", self.select_flags())
        self.assertNotIn("probe-builder:", proc.stdout)

    def test_a_real_repo_keeps_the_unknown_builder(self):
        proc = self.run_step(repo="dreadnought-foundry/portico", body=description(OPUS))
        self.assertIn("--built-on-unknown", self.select_flags())
        self.assertNotIn("probe-builder:", proc.stdout)

    def test_a_pull_request_with_a_card_reads_its_thread_and_never_its_description(self):
        thread = [f"🧠 model-attempt: {SONNET} — engineer agent starting (turns=400)"]
        for card in ("DRE-7618", "DRE-1"):
            with self.subTest(card=card):
                for f in (self.gh_log, self.script_log, self.output):
                    f.write_text("", encoding="utf-8")
                proc = self.run_step(card=card, body=description(OPUS),
                                     gh_body=description(OPUS), thread=thread)
                flags = self.select_flags()
                self.assertEqual(SONNET, flags[flags.index("--built-on") + 1])
                self.assertEqual([], self.gh_calls(), "a carded pull request read GitHub")
                self.assertFalse([c for c in self.script_calls()
                                  if c[1:2] == ["probe-build-model"]])
                self.assertNotIn("probe-builder:", proc.stdout)

    def test_a_card_whose_thread_cannot_be_read_still_never_reads_the_description(self):
        proc = self.run_step(card="DRE-7618", body=description(OPUS), gh_body=description(OPUS))
        self.assertIn("--built-on-unknown", self.select_flags())
        self.assertEqual([], self.gh_calls())
        self.assertNotIn("probe-builder:", proc.stdout)

    def test_the_same_block_run_with_card_empty_does_read_the_description(self):
        """The other half of the pair above: the read is gated on CARD alone."""
        thread = [f"🧠 model-attempt: {SONNET} — engineer agent starting (turns=400)"]
        proc = self.run_step(card="", body=description(OPUS), thread=thread)
        flags = self.select_flags()
        self.assertEqual(OPUS, flags[flags.index("--built-on") + 1])
        self.assertIn("probe-builder:", proc.stdout)


# --------------------------------------------------------------------------- #
# 4. Haiku 5.5 stays out                                                       #
# --------------------------------------------------------------------------- #

def _ids(entries) -> set[str]:
    return {e["model"] if isinstance(e, dict) else e for e in entries or []}


class HaikuStaysExcludedTest(unittest.TestCase):
    def test_it_is_excluded_on_no_ladder_and_in_no_project(self):
        cfg = yaml.safe_load(MODELS.read_text(encoding="utf-8"))
        self.assertIn(HAIKU, _ids(cfg["excluded"]))
        for name, rungs in cfg["ladders"].items():
            with self.subTest(ladder=name):
                self.assertNotIn(HAIKU, _ids(rungs))
        self.assertNotIn(HAIKU, json.dumps(cfg.get("projects") or {}))

    def test_no_role_in_any_repo_selects_it(self):
        cfg = yaml.safe_load(MODELS.read_text(encoding="utf-8"))
        roles = list(cfg["agents"]) + list(cfg.get("session_agents") or {})
        repos = [SANDBOX, *json.loads(REPO_MAP.read_text(encoding="utf-8")).values()]
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in HOME_NAMES:
                os.environ.pop(name, None)
            for role in roles:
                for repo in repos:
                    for flags in ({}, {"built_on_unknown": True}, {"built_on": [OPUS]}):
                        with self.subTest(role=role, repo=repo, flags=flags):
                            try:
                                got = model_fallback.select_with_reasons(
                                    role, repo=repo, probe=lambda m: True, **flags)
                            except model_fallback.NoReviewerLeft:
                                continue
                            self.assertNotEqual(HAIKU, got["model"])
                            self.assertNotIn(HAIKU, got["ladder"])


if __name__ == "__main__":
    unittest.main()
