"""Step outputs survive a value that grew a second line (DRE-4202).

`$GITHUB_OUTPUT` is a `key=value` file the runner reads a LINE at a time. A
value carrying a newline is not a long value there — it is a second key, and
the runner refuses the malformed line it makes: the whole step dies with
`Unable to process file command 'output' successfully`.

Red-Main Repair died that way on portico run 35314499681 (2026-09-17 23:21 PT,
head `eeac2b14`). It failed in the worst possible place: the step that writes
the outputs is the step that has already filed the repair card and spent Linear
budget, so the attempt cost a card and quota and repaired nothing — `main`
stayed red. And because the run died before the model started it wore
`is_error, 1 turn, $0`, the documented fingerprint of a dead
`CLAUDE_CODE_OAUTH_TOKEN`; DRE-4201 read it as exactly that and asked for a
fleet-wide rotation of a credential chain that was working.

What these tests pin, one class per acceptance criterion:

  * a multi-line value goes out under a HEREDOC delimiter and the block the
    step emits parses as `$GITHUB_OUTPUT`, value preserved;
  * a value carrying the delimiter text cannot close its own block;
  * a single-line value still emits as a plain `key=value` line, so every
    existing consumer keeps reading what it always read;
  * the human lines — `repair card:`, `linear-budget:`, and the
    `commented on DRE-…` line `linear_ops` prints when it posts — stay on
    stderr and never reach the output block;
  * the population of scripts whose STDOUT is redirected into the file is
    DISCOVERED from the workflows, so the next one added cannot be added
    blind.

Run: cd bureau-pipeline && python3 -m pytest tests/test_github_output_heredoc.py -v
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
WF_DIR = os.path.join(ROOT, ".github", "workflows")
sys.path.insert(0, SCRIPTS)
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import github_output  # noqa: E402
import red_main_repair  # noqa: E402
import repair_card  # noqa: E402

SHA = "eeac2b140cefb1c4a6a9d3b0f5e7c2d18a4b6039"
RUN_URL = "https://github.com/dreadnought-foundry/portico/actions/runs/35314499681"
WF_NAME = "CI"
SLUG = "portico"

#: The two-line text the dead run actually carried, ending on the line the
#: runner named in `Invalid format 'commented on DRE-4200'`.
TWO_LINE_REASON = "dispatch — attempt 2 for this commit\ncommented on DRE-4200"


# --------------------------------------------------------------------------- #
# The runner's own parser, as strict as the runner is                          #
# --------------------------------------------------------------------------- #

class OutputFileRefused(ValueError):
    """What the runner does with a line it cannot read: kill the step."""


def parse_github_output(text: str) -> dict:
    """Parse `text` the way the runner parses `$GITHUB_OUTPUT`.

    Heredoc form is recognised FIRST, exactly the ambiguity a value carrying
    `<<` would exploit, then plain `key=value`. A line that is neither raises
    `OutputFileRefused` carrying the runner's own words — which is the whole
    failure this module is about, so the tests assert against a parser that
    reproduces it rather than against a forgiving one.
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    values: dict[str, str] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line:
            continue
        key, saw_heredoc, delim = line.partition("<<")
        if saw_heredoc and key and delim:
            body: list[str] = []
            while True:
                if i >= len(lines):
                    raise OutputFileRefused(
                        f"Matching delimiter not found '{delim}'")
                if lines[i] == delim:
                    i += 1
                    break
                body.append(lines[i])
                i += 1
            values[key] = "\n".join(body)
            continue
        key, saw_eq, value = line.partition("=")
        if not saw_eq or not key:
            raise OutputFileRefused(f"Invalid format '{line}'")
        values[key] = value
    return values


def run_cli(module, argv: list[str]) -> tuple[str, str, int]:
    """`module.main(argv)` with both channels captured — stdout is the output
    file here, so the test reads exactly the bytes the runner would."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = module.main(argv)
    return out.getvalue(), err.getvalue(), code


class RunnerParserTest(unittest.TestCase):
    """The parser above is the yardstick for every test below — if it were
    lenient the RED tests would pass against the broken script."""

    def test_plain_and_heredoc_both_read(self):
        self.assertEqual(
            parse_github_output("go=true\nreason<<EOF-1\na\nb\nEOF-1\n"),
            {"go": "true", "reason": "a\nb"},
        )

    def test_a_bare_second_line_is_refused_with_the_runners_words(self):
        with self.assertRaises(OutputFileRefused) as caught:
            parse_github_output("reason=dispatch\ncommented on DRE-4200\n")
        self.assertIn("Invalid format 'commented on DRE-4200'",
                      str(caught.exception))

    def test_an_unclosed_block_is_refused(self):
        with self.assertRaises(OutputFileRefused):
            parse_github_output("reason<<EOF-1\na\n")


# --------------------------------------------------------------------------- #
# A multi-line value is heredoc-delimited and preserved                        #
# --------------------------------------------------------------------------- #

class MultiLineValueTest(unittest.TestCase):
    """The first acceptance criterion: `decide` with a reason that wraps."""

    def _empty_records(self) -> tuple[str, str]:
        """No repair branch and no repair pull request: the shape that sends
        the decision down the `dispatch` path, where the outputs matter."""
        paths = []
        for body in ("[]", "[]"):
            handle = tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False)
            handle.write(body)
            handle.close()
            self.addCleanup(os.unlink, handle.name)
            paths.append(handle.name)
        return paths[0], paths[1]

    def _decide_with(self, reason: str) -> str:
        """Run the `decide` CLI whose decision carries `reason`, and return
        the block it wrote to stdout.

        The reason is injected at the decision, not at the writer: `reason` is
        a machine-readable key today and prose tomorrow, and the point of the
        card is that the writer must be safe for whatever the decision hands
        it — by construction, not by luck.
        """
        real = red_main_repair.decide

        def decide(**kwargs):
            got = real(**kwargs)
            got["reason"] = reason
            return got

        red_main_repair.decide = decide
        self.addCleanup(setattr, red_main_repair, "decide", real)
        refs, pulls = self._empty_records()
        out, err, code = run_cli(red_main_repair, [
            "decide", "--conclusion", "failure", "--head-branch", "main",
            "--default-branch", "main", "--head-sha", SHA,
            "--log-file", "/nonexistent-log", "--refs-file", refs,
            "--pulls-file", pulls,
        ])
        self.assertEqual(code, 0, err)
        return out

    def test_a_reason_that_wraps_is_heredoc_delimited_and_preserved(self):
        block = self._decide_with(TWO_LINE_REASON)
        values = parse_github_output(block)  # today: OutputFileRefused
        self.assertEqual(values["reason"], TWO_LINE_REASON)
        self.assertIn("reason<<", block)

    def test_the_other_keys_survive_a_reason_that_wraps(self):
        # The step dying took `go` and `branch` with it, which is why the
        # repair never started. They must still be readable.
        values = parse_github_output(self._decide_with(TWO_LINE_REASON))
        self.assertEqual(values["go"], "true")
        self.assertEqual(values["branch"],
                         red_main_repair.repair_branch(SHA, 1))
        self.assertEqual(values["attempt"], "1")
        self.assertEqual(values["escalate"], "false")

    def test_a_trailing_newline_in_a_value_round_trips(self):
        values = parse_github_output(self._decide_with("dispatch\n"))
        self.assertEqual(values["reason"], "dispatch\n")


# --------------------------------------------------------------------------- #
# A value carrying the delimiter text                                          #
# --------------------------------------------------------------------------- #

class DelimiterCollisionTest(unittest.TestCase):
    """The second acceptance criterion. `reason` is composed from a failing
    run's own log in the neighbouring writers, so "the value happens to spell
    the delimiter" is content, not paranoia."""

    def test_a_value_spelling_a_delimiter_does_not_close_the_block(self):
        drawn = ["aaaa", "bbbb"]
        self.addCleanup(setattr, github_output, "_token", github_output._token)
        github_output._token = lambda: drawn.pop(0)
        value = "the log said\nEOF-aaaa\nand then stopped"
        block = github_output.render([("reason", value)])
        self.assertEqual(parse_github_output(block), {"reason": value})
        self.assertEqual(drawn, [], "the colliding delimiter must be re-drawn")

    def test_a_value_that_is_nothing_but_delimiters_still_parses(self):
        value = "EOF-x\nEOF-y\nEOF-z"
        self.assertEqual(
            parse_github_output(github_output.render([("reason", value)])),
            {"reason": value},
        )

    def test_the_delimiter_never_occurs_in_the_value(self):
        value = "line one\nline two"
        block = github_output.render([("reason", value)])
        match = re.match(r"reason<<(\S+)\n(.*)\n\1\n\Z", block, re.S)
        self.assertIsNotNone(match, f"expected a heredoc block, got {block!r}")
        self.assertNotIn(match.group(1), match.group(2))


# --------------------------------------------------------------------------- #
# Single-line values are unchanged                                             #
# --------------------------------------------------------------------------- #

class PlainValueTest(unittest.TestCase):
    """The third acceptance criterion: the existing consumers keep working.
    Every reader of these outputs reads a FILE, so the plain form has to stay
    the plain form — a heredoc for everything would be safe and would also be
    a rewrite of thirty step conditions."""

    def test_the_ordinary_decision_emits_plain_key_value_lines(self):
        # The timeout_* keys (DRE-4674) are empty on an ordinary dispatch and
        # ride the plain form exactly as an empty `branch` does — every key
        # goes through the writer, and none of them is made a heredoc by it.
        block = red_main_repair.outputs({
            "go": True, "branch": "repair/DRE-4200-eeac2b140cef",
            "attempt": 2, "escalate": False, "reason": "dispatch",
            "timeout_job": "", "timeout_step": "", "timeout_limit": "",
            "timeout_commits": "",
        })
        self.assertEqual(block.splitlines(), [
            "go=true",
            "branch=repair/DRE-4200-eeac2b140cef",
            "attempt=2",
            "escalate=false",
            "reason=dispatch",
            "timeout_job=",
            "timeout_step=",
            "timeout_limit=",
            "timeout_commits=",
        ])

    def test_render_leaves_a_single_line_value_alone(self):
        self.assertEqual(
            github_output.render([("n", 3), ("reason", "pool-headroom")]),
            "n=3\nreason=pool-headroom\n",
        )

    def test_an_empty_value_stays_a_plain_empty_line(self):
        self.assertEqual(github_output.render([("branch", "")]), "branch=\n")

    def test_a_value_carrying_a_heredoc_marker_is_not_left_plain(self):
        # `key=a<<b` is read by the runner as a heredoc opener named `key=a`.
        # It has no newline in it and still cannot ride a plain line.
        block = github_output.render([("reason", "saw a<<b in the log")])
        self.assertEqual(parse_github_output(block),
                         {"reason": "saw a<<b in the log"})


# --------------------------------------------------------------------------- #
# Human text stays on stderr                                                   #
# --------------------------------------------------------------------------- #

class TalkingOps:
    """The Linear seam as it really behaves. `linear_ops.cmd_comment` prints
    `commented on <card>` on STDOUT, and so does the budget trailer's
    neighbours — which is the actual line portico's runner refused, because
    `repair_card.py open` has its stdout redirected into the output file."""

    def __init__(self, existing: str | None = None):
        self.existing = existing
        self.comments: list[tuple] = []

    def find_open(self, title):
        return self.existing

    def create_card(self, title, description, *, repo_slug, labels=(),
                    lane="Planning"):
        print(f"created {SLUG} card DRE-4200")
        return {"identifier": "DRE-4200",
                "url": "https://linear.app/x/issue/DRE-4200"}

    def cmd_comment(self, identifier, body, *flags):
        self.comments.append((identifier, body))
        print(f"commented on {identifier}")

    def stamp_card(self, identifier, name, why):
        print(f"🧭 routing-verdict: {name} on {identifier}")


class HumanTextChannelTest(unittest.TestCase):
    """The fifth acceptance criterion, and the line the run actually died on."""

    def _open(self, existing: str | None) -> tuple[str, str]:
        ops = TalkingOps(existing)
        self.addCleanup(setattr, repair_card, "_Ops", repair_card._Ops)
        repair_card._Ops = lambda: ops
        out, err, code = run_cli(repair_card, [
            "open", "--repo", SLUG, "--head-sha", SHA,
            "--workflow-name", WF_NAME, "--run-url", RUN_URL,
            "--attempt", "2", "--fallback-branch", f"repair/{SHA}",
        ])
        self.assertEqual(code, 0, err)
        return out, err

    def test_the_comment_receipt_never_reaches_the_output_block(self):
        out, err = self._open("DRE-4200")
        values = parse_github_output(out)  # today: OutputFileRefused
        self.assertEqual(values["card"], "DRE-4200")
        self.assertNotIn("commented on", out)
        self.assertIn("commented on DRE-4200", err)

    def test_the_repair_card_line_stays_on_stderr(self):
        out, err = self._open("DRE-4200")
        self.assertNotIn("repair card:", out)
        self.assertIn("repair card: DRE-4200", err)

    def test_the_create_receipt_stays_on_stderr_too(self):
        # The card-filing path talks as well, and it is the path that spends
        # the budget the failed attempts were burning.
        out, err = self._open(None)
        self.assertNotIn("created portico card", out)
        self.assertIn("created portico card DRE-4200", err)

    def test_the_linear_budget_trailer_is_written_to_stderr(self):
        # The trailer is `linear_ops`' own atexit hook. Asserted against the
        # hook, not against a copy of the string: it is the second human line
        # the dead run printed, and moving it to stdout would put it straight
        # into the output file of every stdout-writing step.
        import linear_ops

        linear_ops._budget.update(
            {"calls": 2, "first": 2499, "last": 2497, "reported": False})
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            linear_ops._report_budget_at_exit()
        self.assertEqual(out.getvalue(), "")
        self.assertIn("linear-budget: 2499 → 2497", err.getvalue())

    def test_the_output_block_is_all_that_reaches_stdout(self):
        out, _ = self._open(None)
        self.assertEqual(sorted(parse_github_output(out)),
                         ["branch", "card", "card_note", "card_owed",
                          "card_url"])


# --------------------------------------------------------------------------- #
# The population that owes the fix is discovered, never remembered             #
# --------------------------------------------------------------------------- #

#: Every script whose STDOUT a workflow step redirects into `$GITHUB_OUTPUT`,
#: and what makes that safe. The shape is the hazard: with stdout redirected,
#: ANY line a library prints there is a line the runner must read as an
#: output — which is how `linear_ops`' `commented on DRE-4200` killed
#: portico run 35314499681. The set is DISCOVERED from the workflows below;
#: a script added with this shape and no reason fails this test rather than
#: becoming the next audit's dead run.
STDOUT_WRITERS = {
    "red_main_repair.py": "guarded",
    "repair_card.py": "guarded",
    # Ints and a fixed reason vocabulary (`log_line` and every human line go
    # to stderr already), and it imports nothing that talks.
    "dispatch_pool.py": "safe",
    # Filesystem paths and a count, written by a module that prints its one
    # human line (`cleared a stale handoff`) to stderr already.
    "fix_handoff.py": "safe",
    # Flags, a ref, a path and a URL already cut to its last line; the one
    # value that quotes GitHub's own words (`error`) is collapsed by
    # `one_line` for exactly this reason (DRE-3262), and every human line
    # goes through `_log`, which writes to stderr.
    "push_rescue.py": "safe",
}

#: The two above that render through the safe writer. Named, because "it
#: imports the module" is the only mechanical proof available here.
GUARDED = {name for name, why in STDOUT_WRITERS.items() if why == "guarded"}


#: Shell separators that end one command and start another. The redirect
#: belongs to the command it sits in, so a `… --github-output "$GITHUB_OUTPUT"
#: || echo "changed=true" >> "$GITHUB_OUTPUT"` step is the FALLBACK writing to
#: the file, not the script's stdout (plan.yml's `review_rerun.py card-set`).
_SEPARATORS = re.compile(r"\|\||&&|;|\|")

REDIRECT = '>> "$GITHUB_OUTPUT"'


def writers_in(text: str) -> set:
    """The scripts in `text` whose own stdout lands in the output file."""
    found = set()
    for line in re.sub(r"\\\n\s*", " ", text).splitlines():
        if REDIRECT not in line:
            continue
        command = _SEPARATORS.split(line[:line.index(REDIRECT)])[-1]
        found.update(re.findall(r"scripts/([a-z_0-9]+\.py)", command))
    return found


def discovered_stdout_writers() -> set:
    """Read off the workflows, never off a list somebody keeps."""
    found = set()
    for entry in sorted(os.listdir(WF_DIR)):
        if entry.endswith(".yml"):
            found |= writers_in(
                open(os.path.join(WF_DIR, entry), encoding="utf-8").read())
    return found


class StdoutWriterPopulationTest(unittest.TestCase):
    """The fourth acceptance criterion, kept from going stale."""

    def test_every_stdout_writer_is_accounted_for(self):
        self.assertEqual(discovered_stdout_writers(), set(STDOUT_WRITERS))

    def test_the_discovery_finds_a_redirected_script(self):
        # The yardstick, so "found nothing" can never read as "all clear".
        self.assertEqual(
            writers_in('          python3 .bureau-pipeline/scripts/x_y.py go \\\n'
                       '            --flag "$F" >> "$GITHUB_OUTPUT"\n'),
            {"x_y.py"},
        )

    def test_the_discovery_ignores_a_fallback_redirect(self):
        # The redirect belongs to the command it sits in. plan.yml's
        # `review_rerun.py card-set` writes through --github-output and its
        # `|| echo` fallback writes the file; the script's stdout does not.
        self.assertEqual(
            writers_in('python3 scripts/review_rerun.py card-set \\\n'
                       '  --github-output "$GITHUB_OUTPUT" \\\n'
                       '  || echo "changed=true" >> "$GITHUB_OUTPUT"\n'),
            set(),
        )

    def test_the_guarded_writers_own_their_stdout(self):
        for name in sorted(GUARDED):
            source = open(os.path.join(SCRIPTS, name), encoding="utf-8").read()
            with self.subTest(script=name):
                self.assertIn("import github_output", source)
                self.assertIn("github_output.only_outputs()", source)
                self.assertIn("github_output.render", source)

    def test_the_guard_actually_moves_stdout(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with github_output.only_outputs():
                print("a library talking")
            print("the block")
        self.assertEqual(out.getvalue(), "the block\n")
        self.assertEqual(err.getvalue(), "a library talking\n")


if __name__ == "__main__":
    unittest.main()
