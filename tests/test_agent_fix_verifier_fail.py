"""DRE-5229 — the fix loop admits the Verifier's FAIL and reads its findings.

The fix loop was woken by the critic's vocabulary only: the job gate admitted a
qa-bot comment carrying `VERDICT: REQUEST_CHANGES`, and the Verifier posts
`🧪 QA Verifier — VERDICT: FAIL @<sha>`. A FAIL could never match, so the
Verifier's `## For the fixing agent` section — written for exactly this reader
— was never read by it. The FIXER learns the Verifier's word; the Verifier's
vocabulary stays PASS/FAIL/SKIP because merge_gate.py and verify.yml read it.

The gate itself is pinned beside the DRE-1988 identity pins
(tests/test_agent_fix_identity_gate.py). This file pins the rest:

  * the "Fetch critic verdict" step, EXECUTED — its real bash and its real jq,
    against fixture threads, with only the two pipeline scripts it calls
    stubbed (the retrying read hands back the fixture; the handoff stamp is a
    no-op). `.bureau-pipeline/verifier-verdict.md` carries a standing FAIL on
    the current head in full and is empty for everything else, while
    `critic-verdict.md` is exactly what it always was;
  * the step agrees with merge_gate — `latest_verdict_comment`,
    `verdict_token`, `verdict_sha` — on what a standing Verifier FAIL is (the
    contract shared with DRE-5230's sweep);
  * the prompt names the file and its section as spec, and the header comment
    says the loop admits the Verifier's FAIL.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

import merge_gate  # noqa: E402
from test_agent_fix_identity_gate import QA_BOT, WORKER_BOT, comment, workflow_src  # noqa: E402

HEAD = "1" * 40
OLD = "2" * 40
CONTENT = "c" * 64

CRITIC_APPROVE = (
    f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}\n\n"
    "Plain-language summary: the change does what the card asks.\n"
)
CRITIC_REQUEST_CHANGES = (
    f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{HEAD}\n\n"
    "## For the fixing agent\n- the critic's finding\n"
)


def verifier(first_line_tail: str, sha: str = HEAD) -> str:
    """A Verifier comment exactly as verify.yml composes it: the verdict line,
    a blank line, then the Verifier's own body."""
    return (
        f"🧪 QA Verifier — {first_line_tail} @{sha}\n\n"
        "The signup page rejects a valid email, so a user cannot register.\n\n"
        "## For the fixing agent\n"
        "- `POST /signup` returns 400 for `a+b@example.com`; expected 201.\n"
        "- Evidence: run log line 212.\n"
    )


FAIL = verifier("VERDICT: FAIL")
NEUTRAL = (
    "🧪 QA Verifier could not run (infra error) — re-verify needed, this is "
    "NOT a feature rejection.\n\nThe behavioral verifier crashed twice.\n"
)


def fetch_step() -> dict:
    steps = yaml.safe_load(workflow_src())["jobs"]["fix"]["steps"]
    found = [s for s in steps if (s.get("name") or "").startswith("Fetch critic verdict")]
    if len(found) != 1:
        raise AssertionError(f"expected one 'Fetch critic verdict' step, found {len(found)}")
    return found[0]


#: The only expressions the step may interpolate, and what they stand for in
#: the harness. An expression this map does not know fails the harness loudly
#: rather than being blanked into a false green.
EXPRESSIONS = {
    "github.repository": "o/r",
    "steps.pr.outputs.number": "7",
    "steps.pr.outputs.head_sha": HEAD,
    "steps.reader.outputs.token": "t",
}


def substitute(text: str) -> str:
    def one(m):
        expr = m.group(1).strip()
        if expr not in EXPRESSIONS:
            raise AssertionError(f"harness does not know the expression {expr!r}")
        return EXPRESSIONS[expr]

    return re.sub(r"\$\{\{(.*?)\}\}", one, text)


# The retry seam, stubbed: the answer to `--out FILE`, or to stdout — the shape
# the read-once seam (Stage 2 fix #21) calls it in, keeping the thread it
# shares with the fix-loop step.
READ_STUB = textwrap.dedent("""\
    import os, shutil, sys
    if "--out" in sys.argv:
        shutil.copy(os.environ["FIXTURE_THREAD"], sys.argv[sys.argv.index("--out") + 1])
    else:
        sys.stdout.write(open(os.environ["FIXTURE_THREAD"]).read())
""")


def run_fetch(thread, *, page_size: int = 100) -> tuple:
    """Execute the live step over `thread`; return (critic, verifier) file
    contents, or None for a file the step did not write."""
    step = fetch_step()
    with tempfile.TemporaryDirectory() as work:
        scripts = os.path.join(work, ".bureau-pipeline", "scripts")
        os.makedirs(scripts)
        with open(os.path.join(scripts, "gh_read_retry.py"), "w") as f:
            f.write(READ_STUB)
        with open(os.path.join(scripts, "fix_handoff.py"), "w") as f:
            f.write("")
        shutil.copy(os.path.join(os.path.dirname(__file__), "..", "scripts", "read_once.py"),
                    scripts)
        pages = [thread[i:i + page_size] for i in range(0, len(thread), page_size)]
        fixture = os.path.join(work, "thread.json")
        with open(fixture, "w") as f:
            json.dump(pages, f)
        runner_temp = os.path.join(work, "rt")
        os.makedirs(runner_temp)
        script = os.path.join(work, "step.sh")
        with open(script, "w") as f:
            f.write(substitute(step["run"]))
        env = {
            "PATH": os.environ["PATH"],
            "RUNNER_TEMP": runner_temp,
            "FIXTURE_THREAD": fixture,
            **{k: substitute(str(v)) for k, v in (step.get("env") or {}).items()},
        }
        # GitHub's own invocation of a `run:` block.
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", script],
            cwd=work, env=env, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise AssertionError(f"fetch step failed:\n{proc.stdout}\n{proc.stderr}")

        def read(name):
            path = os.path.join(work, ".bureau-pipeline", name)
            return open(path, encoding="utf-8").read() if os.path.exists(path) else None

        return read("critic-verdict.md"), read("verifier-verdict.md")


class VerifierVerdictFileTest(unittest.TestCase):
    """The six cases of the acceptance criteria, and the edges around them."""

    def assert_fetch(self, thread, *, critic: str, verifier_file: str, **kw):
        got_critic, got_verifier = run_fetch(thread, **kw)
        self.assertEqual(got_verifier, verifier_file)
        self.assertEqual(got_critic, critic)

    def test_a_qa_bot_fail_on_the_head_lands_in_full(self):
        self.assert_fetch(
            [comment(QA_BOT, CRITIC_APPROVE), comment(QA_BOT, FAIL)],
            critic=CRITIC_APPROVE, verifier_file=FAIL,
        )

    def test_a_fail_carrying_a_content_id_lands(self):
        # DRE-2340 appends ` content:<64-hex>` after the sha.
        body = f"🧪 QA Verifier — VERDICT: FAIL @{HEAD} content:{CONTENT}\n\nfindings\n"
        self.assert_fetch(
            [comment(QA_BOT, CRITIC_APPROVE), comment(QA_BOT, body)],
            critic=CRITIC_APPROVE, verifier_file=body,
        )

    def test_a_pass_leaves_it_empty(self):
        self.assert_fetch(
            [comment(QA_BOT, CRITIC_APPROVE), comment(QA_BOT, verifier("VERDICT: PASS"))],
            critic=CRITIC_APPROVE, verifier_file="",
        )

    def test_a_skip_leaves_it_empty(self):
        self.assert_fetch(
            [comment(QA_BOT, CRITIC_APPROVE), comment(QA_BOT, verifier("VERDICT: SKIP"))],
            critic=CRITIC_APPROVE, verifier_file="",
        )

    def test_a_fail_bound_to_an_older_head_leaves_it_empty(self):
        self.assert_fetch(
            [comment(QA_BOT, CRITIC_APPROVE), comment(QA_BOT, verifier("VERDICT: FAIL", OLD))],
            critic=CRITIC_APPROVE, verifier_file="",
        )

    def test_a_fail_with_no_sha_leaves_it_empty(self):
        body = "🧪 QA Verifier — VERDICT: FAIL\n\n## For the fixing agent\n- x\n"
        self.assert_fetch(
            [comment(QA_BOT, CRITIC_APPROVE), comment(QA_BOT, body)],
            critic=CRITIC_APPROVE, verifier_file="",
        )

    def test_the_neutral_could_not_run_notice_leaves_it_empty(self):
        self.assert_fetch(
            [comment(QA_BOT, CRITIC_APPROVE), comment(QA_BOT, NEUTRAL)],
            critic=CRITIC_APPROVE, verifier_file="",
        )

    def test_a_fail_posted_by_another_login_leaves_it_empty(self):
        for login in ("mallory", WORKER_BOT, "dependabot[bot]"):
            with self.subTest(login=login):
                self.assert_fetch(
                    [comment(QA_BOT, CRITIC_APPROVE), comment(login, FAIL)],
                    critic=CRITIC_APPROVE, verifier_file="",
                )

    def test_a_forged_fail_after_a_genuine_pass_does_not_stand(self):
        self.assert_fetch(
            [comment(QA_BOT, CRITIC_APPROVE),
             comment(QA_BOT, verifier("VERDICT: PASS")),
             comment("mallory", FAIL)],
            critic=CRITIC_APPROVE, verifier_file="",
        )

    def test_no_comment_at_all_leaves_both_empty(self):
        self.assert_fetch([], critic="", verifier_file="")

    def test_a_fail_superseded_by_a_newer_verifier_comment_does_not_stand(self):
        # A standing FAIL is the NEWEST Verifier comment — a re-verify that
        # passed, or could not run, retires the FAIL before it.
        for newer in (verifier("VERDICT: PASS"), NEUTRAL):
            with self.subTest(newer=newer.splitlines()[0]):
                self.assert_fetch(
                    [comment(QA_BOT, CRITIC_APPROVE), comment(QA_BOT, FAIL), comment(QA_BOT, newer)],
                    critic=CRITIC_APPROVE, verifier_file="",
                )

    def test_a_newer_fail_on_the_head_replaces_an_older_one(self):
        older = verifier("VERDICT: FAIL", OLD)
        self.assert_fetch(
            [comment(QA_BOT, older), comment(QA_BOT, CRITIC_APPROVE), comment(QA_BOT, FAIL)],
            critic=CRITIC_APPROVE, verifier_file=FAIL,
        )

    def test_a_qa_bot_comment_that_only_quotes_a_fail_is_inert(self):
        # The merge gate's notes are qa-bot-authored too; one that MENTIONS
        # the Verifier's line is not the Verifier's verdict (opens_with_marker).
        quoting = f"Merge gate: holding on 🧪 QA Verifier — VERDICT: FAIL @{HEAD}\n"
        self.assert_fetch(
            [comment(QA_BOT, CRITIC_APPROVE), comment(QA_BOT, quoting)],
            critic=CRITIC_APPROVE, verifier_file="",
        )

    def test_a_critic_request_changes_and_a_verifier_fail_fill_both_files(self):
        self.assert_fetch(
            [comment(QA_BOT, CRITIC_REQUEST_CHANGES), comment(QA_BOT, FAIL)],
            critic=CRITIC_REQUEST_CHANGES, verifier_file=FAIL,
        )

    def test_the_fail_is_found_on_a_later_page(self):
        thread = [comment("someone", f"chatter {n}") for n in range(25)]
        thread[3] = comment(QA_BOT, CRITIC_APPROVE)
        thread[22] = comment(QA_BOT, FAIL)
        self.assert_fetch(thread, critic=CRITIC_APPROVE, verifier_file=FAIL, page_size=10)


#: First lines around the edges of the verdict grammar. Each is checked
#: against merge_gate's reading, so the step and the gate cannot disagree on
#: what a standing Verifier FAIL is.
EDGE_FIRST_LINES = (
    f"🧪 QA Verifier — VERDICT: FAIL @{HEAD}",
    f"QA Verifier — VERDICT: FAIL @{HEAD}",
    f"  🧪 QA Verifier — VERDICT: FAIL @{HEAD} content:{CONTENT}",
    f"🧪 QA Verifier — VERDICT: FAILED @{HEAD}",
    f"🧪 QA Verifier — VERDICT: FAIL @{OLD}",
    f"🧪 QA Verifier — VERDICT: FAIL @{HEAD[:12]}",
    f"> 🧪 QA Verifier — VERDICT: FAIL @{HEAD}",
    f"🧪 QA Verifiers — VERDICT: FAIL @{HEAD}",
    f"🧪 QA Verifier: VERDICT: FAIL @{HEAD}",
    f"🧪 QA Verifier — VERDICT: PASS @{HEAD}",
    "🧪 QA Verifier could not run (infra error) — re-verify needed",
)


def gate_reading(thread) -> str:
    """The contract, read the way merge_gate reads it."""
    body = merge_gate.latest_verdict_comment(thread, QA_BOT, merge_gate.VERIFIER_MARKER)
    if body is None:
        return ""
    line = merge_gate.first_line(body)
    standing = (
        merge_gate.verdict_token(line, merge_gate.VERIFIER_MARKER) == "FAIL"
        and merge_gate.verdict_sha(line) == HEAD
    )
    return body if standing else ""


class AgreesWithMergeGateTest(unittest.TestCase):
    def test_every_edge_reads_the_way_merge_gate_reads_it(self):
        for first in EDGE_FIRST_LINES:
            body = f"{first}\n\n## For the fixing agent\n- finding\n"
            thread = [comment(QA_BOT, CRITIC_APPROVE), comment(QA_BOT, body)]
            with self.subTest(first=first):
                _critic, got = run_fetch(thread)
                self.assertEqual(got, gate_reading(thread))

    def test_the_edges_are_not_all_one_answer(self):
        # Non-vacuous: the agreement above would hold for a step that always
        # wrote nothing if every edge were a non-FAIL.
        answers = {
            bool(gate_reading([comment(QA_BOT, f"{first}\n")])) for first in EDGE_FIRST_LINES
        }
        self.assertEqual(answers, {True, False})


def collapsed(text: str) -> str:
    return " ".join(text.split())


def fix_prompt() -> str:
    steps = yaml.safe_load(workflow_src())["jobs"]["fix"]["steps"]
    prompts = [
        (s.get("with") or {}).get("prompt") for s in steps if s.get("name") == "Fix"
    ]
    if len(prompts) != 1 or not prompts[0]:
        raise AssertionError("the Fix step's prompt was not found")
    return collapsed(prompts[0])


class PromptTest(unittest.TestCase):
    def test_the_prompt_names_the_verifier_file(self):
        self.assertIn(".bureau-pipeline/verifier-verdict.md", fix_prompt())

    def test_its_fixing_agent_section_is_spec(self):
        prompt = fix_prompt()
        start = prompt.find(".bureau-pipeline/verifier-verdict.md")
        step_1b = prompt.find("1b. Read .bureau-pipeline/fix-thread.md")
        self.assertGreater(start, 0)
        self.assertGreater(step_1b, start, "the verifier file belongs to step 1")
        step_1 = prompt[prompt.find("1. Read .bureau-pipeline/critic-verdict.md"):step_1b]
        self.assertIn("verifier-verdict.md", step_1)
        self.assertIn('"For the fixing agent"', step_1[step_1.find("verifier-verdict.md"):])
        self.assertIn("spec", step_1[step_1.find("verifier-verdict.md"):])

    def test_an_approve_is_said_to_carry_no_findings(self):
        # On the usual Verifier-triggered run the critic file holds the
        # critic's APPROVE; the agent must not hunt it for a spec.
        self.assertRegex(fix_prompt(), r"APPROVE[^.]*no findings")

    def test_both_files_non_empty_means_fix_both(self):
        self.assertRegex(fix_prompt(), r"[Bb]oth (files|verdicts)[^.]*fix both")

    def test_the_pre_push_gate_also_walks_the_verifier_findings(self):
        prompt = fix_prompt()
        gate = prompt[prompt.find("4b. PRE-PUSH GATE"):]
        self.assertIn("verifier-verdict.md", gate[:600])


class HeaderCommentTest(unittest.TestCase):
    def header(self) -> str:
        lines = []
        for line in workflow_src().splitlines():
            if not line.startswith("#"):
                break
            lines.append(line.lstrip("# "))
        return collapsed(" ".join(lines))

    def test_the_header_says_the_loop_admits_the_verifiers_fail(self):
        header = self.header()
        self.assertIn("VERDICT: FAIL", header)
        self.assertIn("Verifier", header)
        self.assertIn("VERDICT: REQUEST_CHANGES", header)


if __name__ == "__main__":
    unittest.main()
