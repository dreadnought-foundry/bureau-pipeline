"""Shadow Replay picks past unknown-builder reviews, and reports the two bars (DRE-7304).

The model-switch epic waits on two numbers, both read from Shadow Replay:
how many of Opus 5.5's acted-on rejections the replay rejects again, and how
many recorded verifier FAILs the replay turns into PASS. Both are about the
reviews the selector ran while this build's model could not be read — the
`unknown-builder` reviews.

What this file pins:

* the unknown-builder clause is read off `model_fallback.selection_note`,
  never retyped, and an empty `recorded_model` means the contract's model;
* `pick --reviewer critic --unknown-builder`: only the copies carrying the
  clause on that model count, a clause copy on another model is skipped and
  counted, a pull request rejected, fixed and approved is picked once at its
  newest acted-on rejection, the acted-on records come first, and a short
  batch fails naming the count;
* `acted_on`: a `🔧 Fix attempt … pushed` comment by the worker bot between
  the rejection and the next critic verdict, and nothing else;
* `pick --reviewer verifier --unknown-builder`: the verifier stub's runs,
  each kept only on a `Select model` note naming the model with the clause,
  paired with its verdict comment, every skipped run named;
* the verifier pick (DRE-7507) stops at its time budget naming how many runs
  it read, prints its progress every 50 runs and once when it stops, and
  reads its logs on several readers while applying them in listing order —
  the same pick and the same lines on one reader as on eight;
* only an unreadable log past the 90 days a log is kept counts toward the
  twenty in a row that stop the listing (DRE-7590): one inside retention is
  read once more and, still unreadable, skipped, and every progress line
  counts the logs skipped unreadable — each test pins the date it starts on;
* `pick --from-pick <file> --acted-on-only`: exactly the acted-on records of
  an earlier pick, byte for byte;
* `compare`'s two new lines, word for word, after today's lines, which stay
  byte-identical, and the report's lists of the pull requests behind them;
* the workflow's three new inputs, passed straight to `pick`, and the
  `from_run` download;
* with none of it asked for, `pick` behaves exactly as today.

Run: python3 -m pytest tests/test_shadow_replay_unknown_builder.py -v
"""

from __future__ import annotations

import collections
import concurrent.futures
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "shadow-replay.yml"

sys.path.insert(0, str(ROOT / "scripts"))

import model_fallback  # noqa: E402

REPO = "dreadnought-foundry/bureau-pipeline"
QA = "agent-bureau-qa-bot[bot]"
WORKER = "agent-bureau-bot[bot]"
# Opaque to the code under test, and on no ladder.
REPLAY_MODEL = "replay-model"
OTHER_MODEL = "recorded-elsewhere"
VERIFY_WF = "verify.yml"


def _sr():
    import shadow_replay  # noqa: PLC0415 — the module under test
    return shadow_replay


def _opus() -> str:
    return _sr().UNKNOWN_BUILDER_MODEL


def _sha(n: int) -> str:
    return f"{n:040x}"


def _at(day: int, hour: int = 0) -> str:
    return f"2026-10-{day:02d}T{hour:02d}:00:00Z"


def note(model: str, role: str, *, unknown: bool = True) -> str:
    """The selector's own note, so the clause is never typed here either."""
    return model_fallback.selection_note({
        "model": model, "role": role, "kind": "advisory",
        "separated": ["a-build-model"], "built_on_unknown": unknown})


def card_copy(token: str, sha: str, model: str | None = None, *, unknown: bool = True) -> str:
    """The card's copy `post_verdict.sh` writes: the verdict, then
    `🧠 model: <id> — <the selector's note>`."""
    model = model or _opus()
    return (f"🔎 QA Critic — VERDICT: {token} @{sha}\n\nfindings…\n\n"
            f"🧠 model: {model} — {note(model, 'critic', unknown=unknown)}\n")


def node(body: str, created: str, label: str = "repo:bureau-pipeline") -> dict:
    return {"createdAt": created, "body": body,
            "issue": {"identifier": "DRE-1", "labels": {"nodes": [{"name": label}]}}}


def comment(body: str, created: str, login: str = QA) -> dict:
    return {"user": {"login": login}, "body": body, "created_at": created}


def critic_verdict(token: str, sha: str, created: str) -> dict:
    return comment(f"🔎 QA Critic — VERDICT: {token} @{sha}\n\nfindings…", created)


def fix_push(n: int, created: str, login: str = WORKER) -> dict:
    return comment(f"🔧 Fix attempt {n} pushed — CI and critic review re-running.",
                   created, login)


def conflict_push(n: int, created: str) -> dict:
    return comment(f"🔀 Conflict resolution round {n} pushed — CI and critic review "
                   "re-running.", created, WORKER)


def verifier_verdict(token: str, sha: str, created: str) -> dict:
    return comment(f"🧪 QA Verifier — VERDICT: {token} @{sha}\n\nevidence…", created)


def job_log(model: str, *, unknown: bool = True) -> str:
    """A `gh run view --log` capture of a verifier run: the echoed script,
    then the selector's note on stderr, behind the job/step/time prefix."""
    pre = "verify\tSelect model\t2026-10-10T00:00:01.0000000Z "
    return "\n".join([
        pre + "##[group]Run WHY_FILE=\"$RUNNER_TEMP/model-why.txt\"",
        pre + "MODEL=$(python3 .bureau-pipeline/scripts/model_fallback.py select verifier \\",
        pre + note(model, "verifier", unknown=unknown),
        pre + f"selected model: {model} (agent: verifier)",
        "verify\tRun verifier\t2026-10-10T00:05:00.0000000Z the verifier ran",
    ]) + "\n"


FAKE_GH = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, re, sys
    fx = json.load(open(os.environ["FAKE_GH_FIXTURE"]))
    with open(os.environ["FAKE_GH_LOG"], "a") as log:
        log.write(" ".join(sys.argv[1:]) + "\\n")
    argv = sys.argv[1:]
    if argv[:2] == ["run", "view"]:
        run_id = argv[2]
        with open(os.environ["FAKE_GH_LOG"]) as log:
            reads = sum(ln.split()[:3] == ["run", "view", run_id] for ln in log)
        if run_id not in fx["logs"] or (run_id in fx["flaky"] and reads == 1):
            sys.stderr.write("log not found: the log has expired (HTTP 410)\\n")
            sys.exit(1)
        sys.stdout.write(fx["logs"][run_id])
        sys.exit(0)
    args = [a for a in argv if not a.startswith("--")]
    if not args or args[0] != "api":
        sys.exit("fake gh: unexpected call " + " ".join(argv))
    path = args[1]
    m = re.fullmatch(r"repos/[^/]+/[^/]+/commits/([0-9a-f]{40})/pulls", path)
    if m:
        print(json.dumps([{"number": n} for n in fx["commits"].get(m.group(1), [])]))
        sys.exit(0)
    m = re.fullmatch(r"repos/[^/]+/[^/]+/commits/([0-9a-f]{40})", path)
    if m:
        if m.group(1) not in fx["commits"]:
            sys.stderr.write("gh: No commit found for SHA (HTTP 422)\\n")
            sys.exit(1)
        print(json.dumps({"sha": m.group(1)}))
        sys.exit(0)
    m = re.fullmatch(r"repos/[^/]+/[^/]+/issues/(\\d+)/comments", path)
    if m:
        print(json.dumps([fx["comments"].get(m.group(1), [])]))
        sys.exit(0)
    m = re.fullmatch(r"repos/dreadnought-foundry/bureau-pipeline/commits\\?(.*)", path)
    if m:
        sys.stderr.write("gh: Server Error (HTTP 502)\\n")
        sys.exit(1)
    m = re.fullmatch(r"repos/[^/]+/[^/]+/actions/workflows/([^/?]+)/runs\\?(.*)", path)
    if m:
        query = dict(part.split("=", 1) for part in m.group(2).split("&"))
        runs = fx["runs"].get(m.group(1), [])
        page, per = int(query.get("page", "1")), int(query.get("per_page", "30"))
        print(json.dumps({"total_count": len(runs),
                          "workflow_runs": runs[(page - 1) * per:page * per]}))
        sys.exit(0)
    sys.exit("fake gh: unexpected call " + path)
    """)


class FakeGh:
    """A `gh` on PATH that answers from a fixture and logs every call. A run
    in `flaky` fails its first `gh run view` and reads on the second."""

    def __init__(self, *, commits=None, comments=None, runs=None, logs=None, flaky=()):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        gh = d / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)
        fixture = d / "fixture.json"
        fixture.write_text(json.dumps({
            "commits": commits or {}, "comments": comments or {},
            "runs": runs or {}, "logs": {str(k): v for k, v in (logs or {}).items()},
            "flaky": [str(k) for k in flaky]}),
            encoding="utf-8")
        self.log = d / "calls.log"
        self.log.write_text("", encoding="utf-8")
        self.env = {"PATH": f"{d}{os.pathsep}{os.environ.get('PATH', '')}",
                    "FAKE_GH_FIXTURE": str(fixture), "FAKE_GH_LOG": str(self.log)}

    def __enter__(self):
        self._patch = mock.patch.dict(os.environ, self.env)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        self.tmp.cleanup()

    def calls(self) -> list:
        return [ln for ln in self.log.read_text(encoding="utf-8").splitlines() if ln]


def _main(*argv) -> tuple:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = _sr().main(list(argv))
    return code, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------- #
# 1. The clause and the default model                                          #
# --------------------------------------------------------------------------- #

class ClauseTest(unittest.TestCase):
    def test_the_clause_is_the_selectors_own_words(self):
        clause = _sr().UNKNOWN_BUILDER_CLAUSE
        self.assertEqual("this build's model could not be read", clause)
        self.assertIn(clause, note("m", "critic", unknown=True))
        self.assertNotIn(clause, note("m", "critic", unknown=False))

    def test_the_clause_follows_the_selector_when_its_words_change(self):
        """Read off the note, never retyped: a reworded note moves it."""
        real = model_fallback.selection_note

        def reworded(decision):
            return real(decision).replace("this build's model could not be read",
                                          "the builder is unknown")
        with mock.patch.object(model_fallback, "selection_note", reworded):
            self.assertEqual("the builder is unknown", _sr().unknown_builder_clause())

    def test_an_empty_recorded_model_means_opus_5_5(self):
        self.assertEqual("claude-opus-5-5", _sr().UNKNOWN_BUILDER_MODEL)


# --------------------------------------------------------------------------- #
# 2. acted_on                                                                  #
# --------------------------------------------------------------------------- #

class ActedOnTest(unittest.TestCase):
    REJECTED = _at(5, 10)

    def _acted(self, comments) -> bool:
        return _sr().acted_on(comments, self.REJECTED)

    def test_a_fix_push_between_the_rejection_and_the_next_verdict_is_acted_on(self):
        self.assertIs(True, self._acted([
            critic_verdict("REQUEST_CHANGES", _sha(1), self.REJECTED),
            fix_push(1, _at(5, 11)),
            critic_verdict("APPROVE", _sha(2), _at(5, 12))]))

    def test_a_fix_push_with_no_later_verdict_is_acted_on(self):
        self.assertIs(True, self._acted([fix_push(1, _at(5, 11))]))

    def test_a_conflict_round_push_is_not(self):
        self.assertIs(False, self._acted([
            critic_verdict("REQUEST_CHANGES", _sha(1), self.REJECTED),
            conflict_push(1, _at(5, 11)),
            critic_verdict("APPROVE", _sha(2), _at(5, 12))]))

    def test_a_push_before_the_verdict_is_not(self):
        self.assertIs(False, self._acted([
            fix_push(1, _at(5, 9)),
            critic_verdict("REQUEST_CHANGES", _sha(1), self.REJECTED)]))

    def test_nothing_pushed_is_not(self):
        self.assertIs(False, self._acted([
            critic_verdict("REQUEST_CHANGES", _sha(1), self.REJECTED),
            comment("a person's comment", _at(5, 11), "someone")]))
        self.assertIs(False, self._acted([]))

    def test_a_push_after_the_next_critic_verdict_is_not(self):
        self.assertIs(False, self._acted([
            critic_verdict("REQUEST_CHANGES", _sha(1), self.REJECTED),
            critic_verdict("REQUEST_CHANGES", _sha(1), _at(5, 11)),
            fix_push(1, _at(5, 12))]))

    def test_a_could_not_run_notice_is_not_the_next_verdict(self):
        self.assertIs(True, self._acted([
            comment("🔎 QA Critic could not run (infra error) — re-review needed, "
                    "this is NOT a code rejection.", _at(5, 11)),
            fix_push(1, _at(5, 12))]))

    def test_a_push_marker_from_anyone_else_or_without_the_fix_opening_is_not(self):
        self.assertIs(False, self._acted([fix_push(1, _at(5, 11), login="someone")]))
        self.assertIs(False, self._acted([comment(
            "🛑 Fix attempt 1 pushed — CI and critic review re-running.", _at(5, 11),
            WORKER)]))


# --------------------------------------------------------------------------- #
# 3. pick --reviewer critic --unknown-builder                                  #
# --------------------------------------------------------------------------- #

class CriticUnknownBuilderPickTest(unittest.TestCase):
    """Five pull requests on unknown-builder copies, and the copies that must
    never count:

    * 501 rejected at two heads, each fixed, then approved — picked once, at
      its newest acted-on rejection (sha 52);
    * 502 approved, the newest review of all;
    * 503 rejected and never fixed;
    * 504 rejected and fixed, the oldest;
    * 505 a known-builder copy — no clause, never kept;
    * 506 a clause copy recorded on another model — skipped and counted.
    """

    def setUp(self):
        self.comments = [
            node(card_copy("REQUEST_CHANGES", _sha(51)), _at(2, 1)),
            node(card_copy("REQUEST_CHANGES", _sha(52)), _at(3, 1)),
            node(card_copy("APPROVE", _sha(53)), _at(4, 1)),
            node(card_copy("APPROVE", _sha(54)), _at(9, 1)),
            node(card_copy("REQUEST_CHANGES", _sha(55)), _at(6, 1)),
            node(card_copy("REQUEST_CHANGES", _sha(56)), _at(1, 1)),
            node(card_copy("APPROVE", _sha(57), unknown=False), _at(8, 1)),
            node(card_copy("APPROVE", _sha(58), OTHER_MODEL), _at(7, 1)),
        ]
        self.commits = {_sha(51): [501], _sha(52): [501], _sha(53): [501],
                        _sha(54): [502], _sha(55): [503], _sha(56): [504],
                        _sha(57): [505], _sha(58): [506]}
        self.pr_comments = {
            "501": [critic_verdict("REQUEST_CHANGES", _sha(51), _at(2)),
                    fix_push(1, _at(2, 3)),
                    critic_verdict("REQUEST_CHANGES", _sha(52), _at(3)),
                    fix_push(2, _at(3, 3)),
                    critic_verdict("APPROVE", _sha(53), _at(4))],
            "502": [critic_verdict("APPROVE", _sha(54), _at(9))],
            "503": [critic_verdict("REQUEST_CHANGES", _sha(55), _at(6))],
            "504": [critic_verdict("REQUEST_CHANGES", _sha(56), _at(1)),
                    fix_push(1, _at(1, 4))],
        }

    def _pick(self, count: int):
        err = io.StringIO()
        with FakeGh(commits=self.commits, comments=self.pr_comments), redirect_stderr(err):
            got = _sr().pick_unknown_builder_critic(
                self.comments, recorded_model=_opus(), count=count, repo=REPO)
        return got, err.getvalue()

    def test_acted_on_first_newest_first_then_the_rest_newest_first(self):
        got, _ = self._pick(4)
        self.assertEqual(
            [(501, _sha(52), "REQUEST_CHANGES", True),
             (504, _sha(56), "REQUEST_CHANGES", True),
             (502, _sha(54), "APPROVE", False),
             (503, _sha(55), "REQUEST_CHANGES", False)],
            [(r["pr"], r["sha"], r["recorded"], r["acted_on"]) for r in got])

    def test_a_rejected_fixed_and_approved_pull_request_is_picked_once_at_the_rejected_head(self):
        got, _ = self._pick(4)
        mine = [r for r in got if r["pr"] == 501]
        self.assertEqual(1, len(mine))
        self.assertEqual(f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{_sha(52)}",
                         mine[0]["recorded_line"])
        self.assertNotIn(_sha(53), [r["sha"] for r in got], "its approval is never picked")

    def test_only_clause_copies_on_the_model_count_and_the_others_are_counted(self):
        got, err = self._pick(4)
        self.assertNotIn(505, [r["pr"] for r in got])
        self.assertNotIn(506, [r["pr"] for r in got])
        skipped = [ln for ln in err.splitlines() if "another model" in ln]
        self.assertEqual(1, len(skipped), err)
        self.assertTrue(skipped[0].startswith("pick: skipped 1 unknown-builder "), skipped[0])

    def test_a_short_batch_fails_naming_the_count(self):
        with self.assertRaises(_sr().PickError) as caught:
            self._pick(5)
        self.assertIn("found 4", str(caught.exception))
        self.assertIn("5", str(caught.exception))

    def test_the_batch_is_cut_to_count_after_the_acted_on_records(self):
        got, err = self._pick(3)
        self.assertEqual([501, 504, 502], [r["pr"] for r in got])
        self.assertIn("pick: acted-on rejections 2 of 3", err)

    def test_every_record_carries_rules_ref(self):
        got, err = self._pick(4)
        self.assertTrue(all("rules_ref" in r for r in got))
        self.assertEqual(_at(3, 1), got[0]["verdict_at"],
                         "the rules are dated by the picked rejection's own copy")
        self.assertIn("pr=501 rules_ref unreadable", err)

    def test_a_card_copy_made_after_the_fix_push_is_still_acted_on(self):
        """The copy is dated after the worker pushed, so acted_on reads from
        the rejection's own time on the pull request, not the copy's."""
        self.comments = [node(card_copy("REQUEST_CHANGES", _sha(71)), _at(5, 12))]
        self.commits = {_sha(71): [507]}
        self.pr_comments = {"507": [critic_verdict("REQUEST_CHANGES", _sha(71), _at(5, 10)),
                                    fix_push(1, _at(5, 11))]}
        got, err = self._pick(1)
        self.assertEqual([(507, _sha(71), True)],
                         [(r["pr"], r["sha"], r["acted_on"]) for r in got])
        self.assertIn("pick: acted-on rejections 1 of 1", err)

    def test_the_cli_defaults_an_empty_recorded_model_and_writes_acted_on(self):
        seen = {}

        def fake_paged(query, variables=None, *, connection="issues"):
            seen.update(variables or {})
            return self.comments

        with tempfile.TemporaryDirectory() as tmp, \
                FakeGh(commits=self.commits, comments=self.pr_comments), \
                mock.patch.object(_sr().linear_ops, "gql_paged", fake_paged):
            pick = Path(tmp) / "pick.json"
            gh_out = Path(tmp) / "out"
            code, out, err = _main(
                "pick", "--reviewer", "critic", "--unknown-builder", "--recorded-model", "",
                "--count", "4", "--prs", "", "--repo", REPO, "--out", str(pick),
                "--github-output", str(gh_out))
            records = json.loads(pick.read_text(encoding="utf-8"))
            line = gh_out.read_text(encoding="utf-8").strip()
        self.assertEqual(0, code, out + err)
        self.assertEqual("🧠 model: " + _opus(), seen["model"])
        self.assertEqual([True, True, False, False], [r["acted_on"] for r in records])
        self.assertEqual(
            {"pr", "sha", "recorded", "rules_ref"},
            set(json.loads(line[len("matrix="):])[0]))
        self.assertIn("pick: acted-on rejections 2 of 4", out + err)


# --------------------------------------------------------------------------- #
# 4. pick --reviewer verifier --unknown-builder                                #
# --------------------------------------------------------------------------- #

def run(run_id: int, pr: int, created: str, updated: str, head: str) -> dict:
    return {"id": run_id, "created_at": created, "updated_at": updated, "head_sha": head,
            "status": "completed", "pull_requests": [{"number": pr}]}


class VerifierUnknownBuilderPickTest(unittest.TestCase):
    """Newest run first:

    * 909 (PR 601) on the model with the clause — kept, at its own verdict;
    * 908 (PR 602) with the clause on another model — skipped and counted;
    * 907 (PR 603) whose log has expired — skipped and named;
    * 906 (PR 604) on the model with the clause, no verdict comment — named;
    * 905 (PR 601) an older run of a pull request already picked — named;
    * 904 (PR 605) on the model, a known builder — named;
    * 903 (PR 606) on the model with the clause — kept.
    """

    def setUp(self):
        self.runs = {VERIFY_WF: [
            run(909, 601, _at(9), _at(9, 1), _sha(61)),
            run(908, 602, _at(8), _at(8, 1), _sha(62)),
            run(907, 603, _at(7), _at(7, 1), _sha(63)),
            run(906, 604, _at(6), _at(6, 1), _sha(64)),
            run(905, 601, _at(5), _at(5, 1), _sha(60)),
            run(904, 605, _at(4), _at(4, 1), _sha(65)),
            run(903, 606, _at(3), _at(3, 1), _sha(66)),
        ]}
        self.logs = {909: job_log(_opus()), 908: job_log(OTHER_MODEL),
                     906: job_log(_opus()), 905: job_log(_opus()),
                     904: job_log(_opus(), unknown=False), 903: job_log(_opus())}
        self.comments = {
            "601": [verifier_verdict("PASS", _sha(60), "2026-10-05T00:30:00Z"),
                    verifier_verdict("FAIL", _sha(61), "2026-10-09T00:30:00Z"),
                    critic_verdict("APPROVE", _sha(61), "2026-10-09T00:40:00Z")],
            "602": [verifier_verdict("FAIL", _sha(62), "2026-10-08T00:30:00Z")],
            "603": [verifier_verdict("FAIL", _sha(63), "2026-10-07T00:30:00Z")],
            "604": [verifier_verdict("FAIL", _sha(64), "2026-10-01T00:30:00Z")],
            "605": [verifier_verdict("FAIL", _sha(65), "2026-10-04T00:30:00Z")],
            "606": [verifier_verdict("PASS", _sha(66), "2026-10-03T00:30:00Z")],
        }

    def _pick(self, count: int):
        err = io.StringIO()
        with FakeGh(runs=self.runs, logs=self.logs, comments=self.comments) as gh, \
                redirect_stderr(err):
            try:
                got = _sr().pick_unknown_builder_verifier(
                    recorded_model=_opus(), count=count, repo=REPO, workflow=VERIFY_WF)
            finally:
                self.calls = gh.calls()
        return got, err.getvalue()

    def test_keeps_only_runs_on_the_model_with_the_clause_paired_with_their_verdict(self):
        got, _ = self._pick(2)
        self.assertEqual(
            [{"pr": 601, "sha": _sha(61), "recorded": "FAIL", "rules_ref": ""},
             {"pr": 606, "sha": _sha(66), "recorded": "PASS", "rules_ref": ""}],
            _sr().matrix(got))
        self.assertEqual(f"🧪 QA Verifier — VERDICT: FAIL @{_sha(61)}", got[0]["recorded_line"])

    def test_every_skipped_run_is_named(self):
        _, err = self._pick(2)
        for run_id in (908, 907, 906, 905, 904):
            self.assertTrue(any(f"run {run_id}" in ln for ln in err.splitlines()),
                            f"run {run_id} is not named:\n{err}")
        self.assertTrue(any("907" in ln and "log" in ln for ln in err.splitlines()))
        self.assertTrue(any("906" in ln and "verdict" in ln for ln in err.splitlines()))
        counted = [ln for ln in err.splitlines() if "another model" in ln
                   and ln.startswith("pick: skipped ")]
        self.assertEqual(1, len(counted), err)
        self.assertTrue(counted[0].startswith("pick: skipped 1 unknown-builder "), counted[0])

    def test_the_runs_are_the_named_stubs_and_logs_are_read_through_gh(self):
        self._pick(2)
        self.assertTrue(any(f"actions/workflows/{VERIFY_WF}/runs" in c for c in self.calls))
        self.assertIn(f"run view 909 --repo {REPO} --log", self.calls)

    def test_a_short_batch_fails_naming_the_count(self):
        with self.assertRaises(_sr().PickError) as caught:
            self._pick(3)
        self.assertIn("found 2", str(caught.exception))
        self.assertIn("3", str(caught.exception))

    def test_the_cli_needs_the_verify_workflow_and_defaults_the_model(self):
        with tempfile.TemporaryDirectory() as tmp, \
                FakeGh(runs=self.runs, logs=self.logs, comments=self.comments):
            pick = Path(tmp) / "pick.json"
            code, out, err = _main(
                "pick", "--reviewer", "verifier", "--unknown-builder", "--recorded-model", "",
                "--verify-workflow", VERIFY_WF, "--count", "2", "--prs", "", "--repo", REPO,
                "--out", str(pick))
            self.assertEqual(0, code, out + err)
            self.assertEqual([601, 606], [r["pr"] for r in json.loads(pick.read_text())])
            code, out, err = _main(
                "pick", "--reviewer", "verifier", "--unknown-builder", "--count", "2",
                "--prs", "", "--repo", REPO, "--out", str(pick))
        self.assertEqual(1, code)
        self.assertIn("verify_workflow", out + err)


def _readers(test: unittest.TestCase, n: int) -> None:
    """Run `test` with the verifier pick's log reads on `n` readers."""
    patch = mock.patch.object(_sr(), "VERIFIER_LOG_READERS", n)
    patch.start()
    test.addCleanup(patch.stop)


class VerifierUnknownBuilderPickOneReaderTest(VerifierUnknownBuilderPickTest):
    """The same pick, word for word, on one reader — today's sequence (DRE-7507)."""

    def setUp(self):
        super().setUp()
        _readers(self, 1)


# The dates the pick is pinned to start on (DRE-7590). The fixtures' runs are
# dated 2026-10-01 and 2026-10-02: three weeks old on the first, past the 90
# days a run's log is kept on the second.
INSIDE_RETENTION = "2026-10-18T19:50:00+00:00"
PAST_RETENTION = "2027-01-15T19:50:00+00:00"


def _today(test: unittest.TestCase, iso: str) -> None:
    """Run `test` with the verifier pick started at `iso`."""
    patch = mock.patch.object(_sr(), "now", lambda: datetime.fromisoformat(iso))
    patch.start()
    test.addCleanup(patch.stop)


class LogRetentionTest(unittest.TestCase):
    def test_ninety_days_as_the_workflow_keeps_its_own_artifacts(self):
        self.assertEqual(90, _sr().LOG_RETENTION_DAYS)
        days = {int(n) for n in re.findall(r"retention-days:\s*(\d+)",
                                           WORKFLOW.read_text(encoding="utf-8"))}
        self.assertEqual({_sr().LOG_RETENTION_DAYS}, days)

    def test_the_date_is_read_off_its_own_function_never_the_budget_clock(self):
        got = _sr().now()
        self.assertIsNotNone(got.tzinfo)
        self.assertLess(abs((datetime.now(timezone.utc) - got).total_seconds()), 60)
        self.assertIsNot(_sr().now, _sr().clock)

    def test_past_retention_is_more_than_ninety_days_before_the_start(self):
        started = datetime.fromisoformat("2026-12-31T00:00:00+00:00")
        past = _sr().past_retention
        self.assertFalse(past({"created_at": "2026-10-02T00:00:00Z"}, started),
                         "exactly 90 days is inside retention")
        self.assertTrue(past({"created_at": "2026-10-01T23:59:59Z"}, started))
        self.assertFalse(past({"created_at": "2026-12-30T00:00:00Z"}, started))


class UnreadableLogsInARowTest(unittest.TestCase):
    """Runs 1001 onward, newest first, each on its own pull request (700 +
    the run's place), dated 2026-10-02. A run whose id has no log in `logs`
    fails `gh run view`, and one in `flaky` fails its first; the last run,
    dated 2026-10-01, is on the model with the clause, with its verdict.

    Each test pins the date the pick starts on (DRE-7590): only an
    unreadable log past retention counts toward the twenty in a row.

    Pinned to one reader (DRE-7507), where the logs are read in exactly
    today's order, so the ordered list of reads holds word for word."""

    def setUp(self):
        _readers(self, 1)

    def _pick(self, logs: dict, total: int, *, flaky=()):
        good = 1000 + total
        runs = {VERIFY_WF: [run(1000 + i, 700 + i, _at(2), _at(2, 1), _sha(700 + i))
                            for i in range(1, total)]
                + [run(good, 700 + total, _at(1), _at(1, 1), _sha(700 + total))]}
        logs = {**logs, good: job_log(_opus())}
        comments = {str(700 + total): [
            verifier_verdict("FAIL", _sha(700 + total), "2026-10-01T00:30:00Z")]}
        err = io.StringIO()
        with FakeGh(runs=runs, logs=logs, comments=comments, flaky=flaky) as gh, \
                redirect_stderr(err):
            try:
                got = _sr().pick_unknown_builder_verifier(
                    recorded_model=_opus(), count=1, repo=REPO, workflow=VERIFY_WF)
            except _sr().PickError as exc:
                got = exc
            finally:
                self.calls = gh.calls()
        return got, err.getvalue()

    def _log_reads(self) -> list:
        return [int(c.split()[2]) for c in self.calls if c.startswith("run view ")]

    def test_twenty_unreadable_logs_past_retention_in_a_row_stop_the_listing(self):
        _today(self, PAST_RETENTION)
        self.assertEqual(20, _sr().UNREADABLE_LOGS_IN_A_ROW)
        got, err = self._pick({}, 21)
        self.assertIsInstance(got, _sr().PickError)
        self.assertIn("found 0", str(got))
        self.assertIn("pick: 20 run logs in a row could not be read — the older runs' logs "
                      "have expired too, so the listing stops here", err.splitlines())
        self.assertEqual(list(range(1001, 1021)), self._log_reads(),
                         "each log past retention is read once, and the run past the "
                         "twentieth is never read")

    def test_twenty_unreadable_logs_inside_retention_do_not_stop_the_listing(self):
        """A burst of failed reads on logs days old — throttling, a timeout —
        is not expiry: each is read twice, skipped, and the pick reads on."""
        _today(self, INSIDE_RETENTION)
        got, err = self._pick({}, 21)
        self.assertNotIsInstance(got, _sr().PickError, err)
        self.assertEqual([721], [r["pr"] for r in got])
        self.assertNotIn("the listing stops here", err)
        for run_id in range(1001, 1021):
            self.assertIn(f"pick: run {run_id} skipped — its log could not be read",
                          err.splitlines())
        self.assertEqual([n for n in range(1001, 1021) for _ in (1, 2)] + [1021],
                         self._log_reads())
        self.assertEqual("pick: read 21 run(s), picked 1 of 1, 20 log(s) unreadable",
                         _read_lines(err)[-1])

    def test_a_log_read_on_its_second_try_is_picked_and_not_counted(self):
        _today(self, INSIDE_RETENTION)
        got, err = self._pick({}, 2, flaky=(1002,))
        self.assertNotIsInstance(got, _sr().PickError, err)
        self.assertEqual([702], [r["pr"] for r in got])
        self.assertNotIn("pick: run 1002 skipped", err)
        self.assertEqual([1001, 1001, 1002, 1002], self._log_reads())
        self.assertEqual("pick: read 2 run(s), picked 1 of 1, 1 log(s) unreadable",
                         _read_lines(err)[-1])

    def test_a_readable_log_between_resets_the_count(self):
        _today(self, PAST_RETENTION)
        got, err = self._pick({1020: job_log(_opus(), unknown=False)}, 40)
        self.assertNotIsInstance(got, _sr().PickError, err)
        self.assertEqual([740], [r["pr"] for r in got])
        self.assertNotIn("the listing stops here", err)
        self.assertEqual(list(range(1001, 1041)), self._log_reads())

    def test_an_unreadable_log_is_not_claimed_expired(self):
        """Any failing `gh run view` lands here — a rate limit as much as an
        expired log — so the skip names the failure without guessing why."""
        _today(self, INSIDE_RETENTION)
        _, err = self._pick({}, 2)
        skipped = [ln for ln in err.splitlines() if ln.startswith("pick: run 1001 skipped")]
        self.assertEqual(["pick: run 1001 skipped — its log could not be read"], skipped, err)


class UnreadableLogsInARowEightReadersTest(UnreadableLogsInARowTest):
    """The same fixtures on eight readers (DRE-7507): the same result and
    every line the same, in the same order, as on one reader. The logs read
    are compared as a set — every log one reader needed, plus at most seven
    runs listed after the last one applied, none of them named in a skip
    line or picked."""

    def setUp(self):
        _readers(self, 8)

    def _same_as_one_reader(self, logs: dict, total: int, *, flaky=()):
        with mock.patch.object(_sr(), "VERIFIER_LOG_READERS", 1):
            want, want_err = self._pick(logs, total, flaky=flaky)
        needed = self._log_reads()
        got, err = self._pick(logs, total, flaky=flaky)
        if isinstance(want, Exception):
            self.assertIsInstance(got, _sr().PickError)
            self.assertEqual(str(want), str(got))
        else:
            self.assertEqual(want, got)
        self.assertEqual(want_err.splitlines(), err.splitlines())
        reads = set(self._log_reads())
        self.assertLessEqual(set(needed), reads, "a log one reader needed was not read")
        last = 1000 + len([ln for ln in err.splitlines() if ln.startswith("pick: run ")]) + (
            0 if isinstance(got, Exception) else len(got))
        ahead = reads - set(needed)
        self.assertTrue(ahead <= set(range(last + 1, last + 8)),
                        f"read past the last run applied ({last}): {sorted(ahead)}")
        for run_id in ahead:
            self.assertNotIn(f"run {run_id} ", err)
            if not isinstance(got, Exception):
                self.assertNotIn(run_id - 300, [r["pr"] for r in got])
        return got, err

    def _times_read(self, run_ids) -> dict:
        reads = collections.Counter(self._log_reads())
        return {run_id: reads[run_id] for run_id in run_ids}

    def test_twenty_unreadable_logs_past_retention_in_a_row_stop_the_listing(self):
        _today(self, PAST_RETENTION)
        got, err = self._same_as_one_reader({}, 21)
        self.assertIsInstance(got, _sr().PickError)
        self.assertIn("found 0", str(got))
        self.assertIn("pick: 20 run logs in a row could not be read — the older runs' logs "
                      "have expired too, so the listing stops here", err.splitlines())
        self.assertIn("pick: run 1020 skipped — its log could not be read", err)
        self.assertNotIn("run 1021 ", err)
        self.assertEqual({n: 1 for n in range(1001, 1021)}, self._times_read(range(1001, 1021)))

    def test_twenty_unreadable_logs_inside_retention_do_not_stop_the_listing(self):
        _today(self, INSIDE_RETENTION)
        got, err = self._same_as_one_reader({}, 21)
        self.assertNotIsInstance(got, _sr().PickError, err)
        self.assertEqual([721], [r["pr"] for r in got])
        self.assertNotIn("the listing stops here", err)
        self.assertEqual({n: 2 for n in range(1001, 1021)}, self._times_read(range(1001, 1021)))

    def test_a_log_read_on_its_second_try_is_picked_and_not_counted(self):
        _today(self, INSIDE_RETENTION)
        got, err = self._same_as_one_reader({}, 2, flaky=(1002,))
        self.assertEqual([702], [r["pr"] for r in got])
        self.assertNotIn("pick: run 1002 skipped", err)

    def test_a_readable_log_between_resets_the_count(self):
        _today(self, PAST_RETENTION)
        got, err = self._same_as_one_reader({1020: job_log(_opus(), unknown=False)}, 40)
        self.assertNotIsInstance(got, _sr().PickError, err)
        self.assertEqual([740], [r["pr"] for r in got])
        self.assertNotIn("the listing stops here", err)
        self.assertEqual(set(range(1001, 1041)), set(self._log_reads()))

    def test_an_unreadable_log_is_not_claimed_expired(self):
        _today(self, INSIDE_RETENTION)
        _, err = self._same_as_one_reader({}, 2)
        skipped = [ln for ln in err.splitlines() if ln.startswith("pick: run 1001 skipped")]
        self.assertEqual(["pick: run 1001 skipped — its log could not be read"], skipped, err)


# --------------------------------------------------------------------------- #
# 4b. The verifier pick's budget, progress and parallel reads (DRE-7507)       #
# --------------------------------------------------------------------------- #

def no_pr_run(run_id: int) -> dict:
    """A run that names no pull request, and no head to find one from."""
    return {"id": run_id, "created_at": _at(2), "updated_at": _at(2, 1), "head_sha": "",
            "status": "completed", "pull_requests": []}


class ReverseReaders:
    """Stands in for the verifier pick's reader pool: it holds every read it
    is handed, and when the pick waits on one it finishes every held read in
    the reverse of the order they were handed over — the reverse of listing
    order. `handed` is the order the reads were handed over in, `finished`
    the order they finished in."""

    def __init__(self, max_workers: int):
        self.max_workers = max_workers
        self.held, self.handed, self.finished = [], [], []
        ReverseReaders.last = self

    def submit(self, fn, *args):
        future = _HeldRead(self)
        self.held.append((future, fn, args))
        self.handed.append(args[-1])
        return future

    def finish_held(self):
        held, self.held = self.held, []
        for future, fn, args in reversed(held):
            if not future.set_running_or_notify_cancel():
                continue
            self.finished.append(args[-1])
            try:
                future.set_result(fn(*args))
            except Exception as exc:  # noqa: BLE001 — handed to the pick, as a pool does
                future.set_exception(exc)

    def shutdown(self, wait=True, *, cancel_futures=False):
        for future, _fn, _args in self.held:
            future.cancel()
        self.held = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.shutdown()


class _HeldRead(concurrent.futures.Future):
    def __init__(self, readers: ReverseReaders):
        super().__init__()
        self._readers = readers

    def result(self, timeout=None):
        self._readers.finish_held()
        return super().result(timeout)


class StandIn:
    """The verifier pick with its listing, its log reads and its pull request
    comments answered from a fixture rather than `gh`. `reads` is every log
    read, in the order the reads started."""

    def __init__(self, runs, logs, comments):
        self.runs, self.logs, self.comments = runs, logs, comments
        self.reads = []

    def run_log(self, repo, run_id):
        self.reads.append(run_id)
        return self.logs.get(run_id)

    def pick(self, count: int, *, pool=None):
        err = io.StringIO()
        sr = _sr()
        patches = [
            mock.patch.object(sr, "verifier_runs", lambda repo, workflow: iter(self.runs)),
            mock.patch.object(sr, "run_log", self.run_log),
            mock.patch.object(sr, "_pr_comments",
                              lambda repo, pr: self.comments.get(pr, [])),
        ]
        if pool is not None:
            patches.append(mock.patch.object(sr, "ThreadPoolExecutor", pool))
        for patch in patches:
            patch.start()
        try:
            with redirect_stderr(err):
                try:
                    got = sr.pick_unknown_builder_verifier(
                        recorded_model=_opus(), count=count, repo=REPO, workflow=VERIFY_WF)
                except sr.PickError as exc:
                    got = exc
        finally:
            for patch in reversed(patches):
                patch.stop()
        return got, err.getvalue()


def _read_lines(err: str) -> list:
    return [ln for ln in err.splitlines() if ln.startswith("pick: read ")]


class VerifierPickConstantsTest(unittest.TestCase):
    def test_ten_minutes_and_eight_readers(self):
        self.assertEqual(600, _sr().VERIFIER_PICK_BUDGET_SECONDS)
        self.assertEqual(8, _sr().VERIFIER_LOG_READERS)

    def test_the_budget_leaves_five_minutes_of_the_pick_jobs_limit(self):
        """The job's checkout, Python setup and upload run inside its limit too."""
        job = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["pick"]
        self.assertGreaterEqual(
            int(job["timeout-minutes"]) * 60 - _sr().VERIFIER_PICK_BUDGET_SECONDS, 300)


class SpendingClock:
    """Reads a fixed time until `fresh` reads have gone by, then the budget
    spent. At its first spent read it notes how many runs had been applied,
    and it notes the thread of every read. `events` gets a `clock` for each
    read, with the runs applied at that moment."""

    def __init__(self, fresh: int, applied, events: list):
        self.fresh, self.applied, self.events = fresh, applied, events
        self.reads, self.applied_when_spent, self.threads = 0, None, set()

    def __call__(self) -> float:
        self.reads += 1
        self.threads.add(threading.current_thread())
        self.events.append(("clock", self.applied()))
        if self.reads <= self.fresh:
            return 1000.0
        if self.applied_when_spent is None:
            self.applied_when_spent = self.applied()
        return 1000.0 + _sr().VERIFIER_PICK_BUDGET_SECONDS + 1


class VerifierPickBudgetTest(unittest.TestCase):
    """Sixty runs on their own pull requests, 1200 + the run's place, newest
    first, by place: on the model with the clause and its verdict, on another
    model, an unreadable log, no pull request, a known builder — over and
    over. The clock reads the budget spent partway through; `count` is 40, so
    the pick is short."""

    READERS = 1
    TOTAL = 60

    def setUp(self):
        _readers(self, self.READERS)
        self.runs, logs, comments = [], {}, {}
        for place in range(1, self.TOTAL + 1):
            run_id, pr, kind = 2000 + place, 1200 + place, place % 5
            if kind == 3:
                self.runs.append(no_pr_run(run_id))
                continue
            self.runs.append(run(run_id, pr, _at(2), _at(2, 1), _sha(pr)))
            if kind == 1:
                logs[run_id] = job_log(_opus())
                comments[str(pr)] = [verifier_verdict("FAIL", _sha(pr), "2026-10-02T00:30:00Z")]
            elif kind == 2:
                logs[run_id] = job_log(OTHER_MODEL)
            elif kind == 4:
                logs[run_id] = job_log(_opus(), unknown=False)
        self.logs, self.comments = logs, comments

    def test_a_spent_budget_stops_the_pick_and_names_how_many_it_read(self):
        err = io.StringIO()
        with FakeGh(runs={VERIFY_WF: self.runs}, logs=self.logs,
                    comments=self.comments) as gh, redirect_stderr(err):

            def applied() -> int:
                skips = [ln for ln in err.getvalue().splitlines() if ln.startswith("pick: run ")]
                picks = [c for c in gh.calls() if "/comments" in c]
                return len(skips) + len(picks)

            events = []

            class Readers(concurrent.futures.ThreadPoolExecutor):
                def submit(self, fn, *args):
                    events.append(("read", None))
                    return super().submit(fn, *args)

            clock = SpendingClock(25, applied, events)
            with mock.patch.object(_sr(), "clock", clock), \
                    mock.patch.object(_sr(), "ThreadPoolExecutor", Readers), \
                    self.assertRaises(_sr().PickError) as caught:
                _sr().pick_unknown_builder_verifier(
                    recorded_model=_opus(), count=40, repo=REPO, workflow=VERIFY_WF)
            calls = gh.calls()
        message, lines = str(caught.exception), err.getvalue().splitlines()

        found = int(re.search(r"found (\d+) eligible", message).group(1))
        skips = [ln for ln in lines if ln.startswith("pick: run ")]
        read = len(skips) + found
        self.assertIn("nothing is replayed short", message)
        self.assertIn("600-second time budget", message)
        self.assertIn(f"after reading {read} run(s)", message)
        self.assertTrue(0 < read < self.TOTAL, message)
        self.assertEqual(read, clock.applied_when_spent,
                         "a run was applied after the clock read the budget spent")
        self.assertEqual({threading.current_thread()}, clock.threads,
                         "the clock is read on the pick's own thread only")
        self.assertLessEqual(set(range(read + 1)),
                             {n for kind, n in events if kind == "clock"},
                             "a run was applied with no clock read before it")
        for at, (kind, _) in enumerate(events):
            if kind == "read":
                self.assertEqual("clock", events[at - 1][0],
                                 "a read was dispatched with no clock read just before it")

        applied = {r["id"] for r in self.runs[:read]}
        self.assertEqual({int(ln.split()[2]) for ln in skips},
                         applied - {2000 + p for p in range(1, read + 1) if p % 5 == 1})
        reads = {int(c.split()[2]) for c in calls if c.startswith("run view ")}
        outstanding = reads - applied
        self.assertLessEqual(len(outstanding), self.READERS, sorted(outstanding))
        self.assertTrue(outstanding <= {r["id"] for r in self.runs[read:]})
        for run_id in outstanding:
            self.assertNotIn(f"run {run_id} ", err.getvalue())

        unreadable = len([ln for ln in skips if ln.endswith("its log could not be read")])
        self.assertGreater(unreadable, 0)
        final = f"pick: read {read} run(s), picked {found} of 40, {unreadable} log(s) unreadable"
        self.assertEqual(final, _read_lines(err.getvalue())[-1])
        skipped_line = next(i for i, ln in enumerate(lines) if ln.startswith("pick: skipped "))
        self.assertLess(lines.index(final), skipped_line)


class VerifierPickBudgetEightReadersTest(VerifierPickBudgetTest):
    READERS = 8


class VerifierPickProgressTest(unittest.TestCase):
    """120 runs on their own pull requests (1300 + the run's place), each a
    known builder but for three that name no pull request among the first
    50, and the runs `eligible` names, on the model with the clause and with
    their verdict."""

    def _stand_in(self, total: int = 120, eligible=(110, 115), no_pr=(3, 7, 11),
                  readable: bool = True) -> StandIn:
        runs, logs, comments = [], {}, {}
        for place in range(1, total + 1):
            run_id, pr = 3000 + place, 1300 + place
            if place in no_pr:
                runs.append(no_pr_run(run_id))
                continue
            runs.append(run(run_id, pr, _at(2), _at(2, 1), _sha(pr)))
            if place in eligible:
                logs[run_id] = job_log(_opus())
                comments[pr] = [verifier_verdict("PASS", _sha(pr), "2026-10-02T00:30:00Z")]
            elif readable:
                logs[run_id] = job_log(_opus(), unknown=False)
        return StandIn(runs, logs, comments)

    def test_every_fiftieth_run_then_once_when_the_count_is_reached(self):
        stand_in = self._stand_in()
        got, err = stand_in.pick(2)
        self.assertNotIsInstance(got, _sr().PickError, err)
        self.assertEqual(["pick: read 50 run(s), picked 0 of 2, 0 log(s) unreadable",
                          "pick: read 100 run(s), picked 0 of 2, 0 log(s) unreadable",
                          "pick: read 115 run(s), picked 2 of 2, 0 log(s) unreadable"],
                         _read_lines(err))
        lines = err.splitlines()
        self.assertLess(lines.index("pick: read 115 run(s), picked 2 of 2, 0 log(s) unreadable"),
                        next(i for i, ln in enumerate(lines) if ln.startswith("pick: skipped ")))

    def test_a_short_batch_prints_the_final_line_too(self):
        got, err = self._stand_in().pick(3)
        self.assertIsInstance(got, _sr().PickError)
        self.assertEqual(["pick: read 50 run(s), picked 0 of 3, 0 log(s) unreadable",
                          "pick: read 100 run(s), picked 0 of 3, 0 log(s) unreadable",
                          "pick: read 120 run(s), picked 2 of 3, 0 log(s) unreadable"],
                         _read_lines(err))
        self.assertNotIn("time budget", str(got))

    def test_runs_skipped_without_a_download_are_read(self):
        stand_in = self._stand_in()
        _, err = stand_in.pick(2)
        lines = err.splitlines()
        at = lines.index("pick: read 50 run(s), picked 0 of 2, 0 log(s) unreadable")
        self.assertTrue(lines[at - 1].startswith("pick: run 3050 skipped — "), lines[at - 1])
        for place in (3, 7, 11):
            self.assertIn(f"pick: run {3000 + place} skipped — it names no pull request", lines)
            self.assertNotIn(3000 + place, stand_in.reads)

    def test_a_stop_on_the_fiftieth_run_prints_its_line_twice(self):
        _, err = self._stand_in(eligible=(50,)).pick(1)
        self.assertEqual(["pick: read 50 run(s), picked 1 of 1, 0 log(s) unreadable"] * 2,
                         _read_lines(err))

    def test_an_empty_listing(self):
        got, err = StandIn([], {}, {}).pick(3)
        self.assertIsInstance(got, _sr().PickError)
        self.assertEqual(["pick: read 0 run(s), picked 0 of 3, 0 log(s) unreadable"],
                         _read_lines(err))

    def test_after_the_unreadable_logs_line_and_before_the_other_model_count(self):
        _today(self, PAST_RETENTION)
        _, err = self._stand_in(total=21, eligible=(), no_pr=(), readable=False).pick(1)
        lines = err.splitlines()
        stop = next(i for i, ln in enumerate(lines) if "run logs in a row" in ln)
        self.assertEqual("pick: read 20 run(s), picked 0 of 1, 20 log(s) unreadable",
                         lines[stop + 1])
        self.assertTrue(lines[stop + 2].startswith("pick: skipped 0 "), lines[stop + 2])


class VerifierPickEquivalenceTest(unittest.TestCase):
    """The same listing on one reader and on eight, its logs finishing in the
    reverse of listing order, gives the same pick and the same lines.

    * 950 (PR 901) on the model with the clause — kept;
    * 949 (PR 902) on another model;
    * 948 (PR 901) eligible too, read ahead — already picked at 950;
    * 947 (PR 903) an unreadable log;
    * 946 (PR 904) on the model with the clause, no verdict comment;
    * 945 names no pull request;
    * 944 (PR 905) a known builder;
    * 943 (PR 906) no Select model note;
    * 942 (PR 907) on the model with the clause — kept;
    * 941 (PR 901) already picked before its log could be read;
    * 940 (PR 908) on the model with the clause — kept.
    """

    def _stand_in(self) -> StandIn:
        runs = [run(950, 901, _at(9), _at(9, 1), _sha(950)),
                run(949, 902, _at(9), _at(9, 1), _sha(949)),
                run(948, 901, _at(8), _at(8, 1), _sha(948)),
                run(947, 903, _at(8), _at(8, 1), _sha(947)),
                run(946, 904, _at(7), _at(7, 1), _sha(946)),
                no_pr_run(945),
                run(944, 905, _at(6), _at(6, 1), _sha(944)),
                run(943, 906, _at(6), _at(6, 1), _sha(943)),
                run(942, 907, _at(5), _at(5, 1), _sha(942)),
                run(941, 901, _at(4), _at(4, 1), _sha(941)),
                run(940, 908, _at(3), _at(3, 1), _sha(940))]
        logs = {950: job_log(_opus()), 949: job_log(OTHER_MODEL), 948: job_log(_opus()),
                946: job_log(_opus()), 944: job_log(_opus(), unknown=False),
                943: "verify\tRun verifier\t2026-10-10T00:05:00.0000000Z the verifier ran\n",
                942: job_log(_opus()), 941: job_log(_opus()), 940: job_log(_opus())}
        comments = {
            901: [verifier_verdict("FAIL", _sha(948), "2026-10-08T00:30:00Z"),
                  verifier_verdict("PASS", _sha(950), "2026-10-09T00:30:00Z")],
            904: [verifier_verdict("FAIL", _sha(946), "2026-10-01T00:30:00Z")],
            907: [verifier_verdict("FAIL", _sha(942), "2026-10-05T00:30:00Z")],
            908: [verifier_verdict("PASS", _sha(940), "2026-10-03T00:30:00Z")],
        }
        return StandIn(runs, logs, comments)

    def _both(self, count: int):
        picks = []
        for readers in (1, 8):
            with mock.patch.object(_sr(), "VERIFIER_LOG_READERS", readers):
                stand_in = self._stand_in()
                got, err = stand_in.pick(count, pool=ReverseReaders)
                readers_used = ReverseReaders.last
                picks.append((got, err, readers_used.handed, readers_used.finished))
        return picks

    def _same(self, one, eight):
        (_, one_err, *_), (_, eight_err, *_) = one, eight
        self.assertEqual(one_err.splitlines(), eight_err.splitlines())
        skips = [ln for ln in eight_err.splitlines() if ln.startswith("pick: run ")]
        for run_id, why in ((949, "chosen on"), (948, "already picked"), (947, "log"),
                            (946, "verdict"), (945, "no pull request"),
                            (944, "builder's model was read"), (943, "Select model note"),
                            (941, "already picked")):
            self.assertTrue(any(ln.startswith(f"pick: run {run_id} skipped") and why in ln
                                for ln in skips), f"{run_id}: {skips}")
        self.assertIn("pick: skipped 1 unknown-builder", eight_err)

    def test_the_same_pick_whatever_order_the_logs_finish_in(self):
        one, eight = self._both(3)
        self.assertEqual([901, 907, 908], [r["pr"] for r in eight[0]])
        self.assertEqual(one[0], eight[0])
        self.assertEqual(_sha(950), eight[0][0]["sha"])
        self._same(one, eight)
        handed, finished = eight[2], eight[3]
        self.assertEqual(950, handed[0])
        first = finished.index(950) + 1
        self.assertGreater(first, 1, "the logs did not finish out of listing order")
        self.assertEqual(list(reversed(handed[:first])), finished[:first],
                         "the logs read ahead of 950 finished in the reverse of listing order")
        self.assertIn(948, handed, "948's log was read ahead, then set aside")
        self.assertEqual(one[2], one[3])
        for _got, _err, handed, _finished in (one, eight):
            self.assertNotIn(945, handed)
            self.assertNotIn(941, handed)

    def test_the_same_short_batch(self):
        one, eight = self._both(4)
        self.assertIsInstance(one[0], _sr().PickError)
        self.assertIsInstance(eight[0], _sr().PickError)
        self.assertEqual(str(one[0]), str(eight[0]))
        self._same(one, eight)

    def test_a_run_naming_no_pull_request_is_never_read_on_eight_readers(self):
        err = io.StringIO()
        with mock.patch.object(_sr(), "VERIFIER_LOG_READERS", 8), \
                FakeGh(runs={VERIFY_WF: [no_pr_run(961), run(960, 909, _at(9), _at(9, 1),
                                                                _sha(960))]},
                       logs={960: job_log(_opus())}) as gh, redirect_stderr(err):
            with self.assertRaises(_sr().PickError):
                _sr().pick_unknown_builder_verifier(
                    recorded_model=_opus(), count=1, repo=REPO, workflow=VERIFY_WF)
            calls = gh.calls()
        self.assertIn("pick: run 961 skipped — it names no pull request", err.getvalue())
        self.assertEqual([f"run view 960 --repo {REPO} --log"],
                         [c for c in calls if c.startswith("run view ")])


# --------------------------------------------------------------------------- #
# 5. pick --from-pick <file> --acted-on-only                                   #
# --------------------------------------------------------------------------- #

class FromPickTest(unittest.TestCase):
    RECORDS = [
        {"pr": 501, "sha": _sha(52), "recorded": "REQUEST_CHANGES",
         "recorded_line": f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{_sha(52)}",
         "verdict_at": _at(3, 1), "rules_ref": "a" * 40, "acted_on": True},
        {"pr": 502, "sha": _sha(54), "recorded": "APPROVE",
         "recorded_line": f"🔎 QA Critic — VERDICT: APPROVE @{_sha(54)}",
         "verdict_at": _at(9, 1), "rules_ref": "b" * 40, "acted_on": False},
        {"pr": 504, "sha": _sha(56), "recorded": "REQUEST_CHANGES",
         "recorded_line": f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{_sha(56)}",
         "verdict_at": _at(1, 1), "rules_ref": "", "acted_on": True},
    ]

    def test_re_emits_exactly_the_acted_on_records_and_reads_nothing_else(self):
        with tempfile.TemporaryDirectory() as tmp, FakeGh() as gh:
            earlier = Path(tmp) / "shadow-replay-pick.json"
            earlier.write_text(json.dumps(self.RECORDS, ensure_ascii=False), encoding="utf-8")
            pick, gh_out = Path(tmp) / "pick.json", Path(tmp) / "out"
            with mock.patch.object(_sr().linear_ops, "gql_paged",
                                   side_effect=AssertionError("Linear is not read")):
                code, out, err = _main(
                    "pick", "--reviewer", "critic", "--from-pick", str(earlier),
                    "--acted-on-only", "--unknown-builder", "--count", "1", "--prs", "",
                    "--repo", REPO, "--out", str(pick), "--github-output", str(gh_out))
            records = json.loads(pick.read_text(encoding="utf-8"))
            line = gh_out.read_text(encoding="utf-8").strip()
            calls = gh.calls()
        self.assertEqual(0, code, out + err)
        self.assertEqual([self.RECORDS[0], self.RECORDS[2]], records)
        self.assertEqual(
            [{"pr": 501, "sha": _sha(52), "recorded": "REQUEST_CHANGES", "rules_ref": "a" * 40},
             {"pr": 504, "sha": _sha(56), "recorded": "REQUEST_CHANGES", "rules_ref": ""}],
            json.loads(line[len("matrix="):]))
        self.assertEqual([], calls, "nothing else is read")

    def test_a_file_with_no_acted_on_record_fails_naming_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            earlier = Path(tmp) / "shadow-replay-pick.json"
            earlier.write_text(json.dumps([self.RECORDS[1]]), encoding="utf-8")
            code, out, err = _main(
                "pick", "--reviewer", "critic", "--from-pick", str(earlier),
                "--acted-on-only", "--count", "20", "--prs", "", "--repo", REPO,
                "--out", str(Path(tmp) / "pick.json"))
        self.assertEqual(1, code)
        self.assertIn(str(earlier), out + err)

    def test_an_unreadable_file_fails_naming_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "absent.json"
            code, out, err = _main(
                "pick", "--reviewer", "critic", "--from-pick", str(missing),
                "--acted-on-only", "--prs", "", "--repo", REPO,
                "--out", str(Path(tmp) / "pick.json"))
        self.assertEqual(1, code)
        self.assertIn(str(missing), out + err)


# --------------------------------------------------------------------------- #
# 6. compare — the two bars                                                    #
# --------------------------------------------------------------------------- #

def write_artifact(root: Path, role: str, pr: int, sha: str, verdict) -> None:
    d = root / f"shadow-verdict-{role}-{pr}"
    d.mkdir(parents=True)
    (d / "shadow-verdict.json").write_text(json.dumps({
        "role": role, "repo": REPO, "pr": pr, "sha": sha, "model": REPLAY_MODEL,
        "verdict": verdict, "real": verdict is not None,
        "run_url": f"https://github.com/{REPO}/actions/runs/{pr}"}), encoding="utf-8")
    (d / "shadow-verdict.md").write_text(f"VERDICT: {verdict}\n" if verdict else "",
                                         encoding="utf-8")


def rec(pr: int, recorded: str, acted_on=None) -> dict:
    r = {"pr": pr, "sha": _sha(pr), "recorded": recorded, "rules_ref": "",
         "recorded_line": f"VERDICT: {recorded} @{_sha(pr)}"}
    if acted_on is not None:
        r["acted_on"] = acted_on
    return r


class CompareBarsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _compare(self, reviewer: str, records, verdicts: dict, tag: str = "") -> tuple:
        arts = self.root / f"artifacts-{reviewer}{tag}"
        arts.mkdir()
        for pr, verdict in verdicts.items():
            write_artifact(arts, reviewer, pr, _sha(pr), verdict)
        pick = self.root / f"pick-{reviewer}{tag}.json"
        pick.write_text(json.dumps(records), encoding="utf-8")
        out_dir = self.root / f"report-{reviewer}{tag}"
        code, out, _ = _main("compare", "--reviewer", reviewer, "--model", REPLAY_MODEL,
                             "--recorded-model", "", "--pick", str(pick),
                             "--artifacts", str(arts), "--out-dir", str(out_dir))
        self.assertEqual(0, code)
        lines = [ln for ln in out.splitlines() if ln.startswith("shadow-replay:")]
        report = json.loads((out_dir / "shadow-replay-report.json").read_text(encoding="utf-8"))
        md = (out_dir / "shadow-replay-report.md").read_text(encoding="utf-8")
        return lines, report, md

    def _critic(self):
        records = [rec(1, "REQUEST_CHANGES", True), rec(2, "REQUEST_CHANGES", True),
                   rec(3, "REQUEST_CHANGES", True), rec(4, "APPROVE", False)]
        # PR 3 left no artifact: not rejected again, and named apart.
        return self._compare("critic", records, {1: "REQUEST_CHANGES", 2: "APPROVE",
                                                 4: "APPROVE"})

    def test_the_critic_line_word_for_word_after_todays_lines(self):
        lines, _, _ = self._critic()
        self.assertEqual([
            f"shadow-replay: pr=1 sha={_sha(1)} recorded=REQUEST_CHANGES "
            "replayed=REQUEST_CHANGES match=yes",
            f"shadow-replay: pr=2 sha={_sha(2)} recorded=REQUEST_CHANGES replayed=APPROVE match=no",
            f"shadow-replay: pr=3 sha={_sha(3)} recorded=REQUEST_CHANGES replayed=none match=no",
            f"shadow-replay: pr=4 sha={_sha(4)} recorded=APPROVE replayed=APPROVE match=yes",
            f"shadow-replay: matched 2 of 4 (50%) reviewer=critic model={REPLAY_MODEL} "
            "recorded_model=none",
            "shadow-replay: acted-on rejections 1 of 3 rejected again (33%) "
            f"reviewer=critic model={REPLAY_MODEL}",
        ], lines)

    def test_the_critic_report_carries_acted_on_and_lists_the_pull_requests(self):
        _, report, md = self._critic()
        self.assertEqual({1: True, 2: True, 3: True, 4: False},
                         {r["pr"]: r["acted_on"] for r in report["replays"]})
        bar = report["acted_on_rejections"]
        self.assertEqual((1, 3, 33), (bar["rejected_again"], bar["of"], bar["pct"]))
        self.assertEqual([1], bar["prs_rejected_again"])
        self.assertEqual([2], bar["prs_not_rejected_again"])
        self.assertEqual([3], bar["prs_no_verdict"])
        self.assertIn("acted-on rejections 1 of 3 rejected again (33%)", md)
        section = md.split("## Acted-on rejections", 1)[1]
        self.assertIn("PR 1", section)
        self.assertIn("PR 2", section)
        self.assertIn("PR 3", section)
        self.assertIn("no verdict", section)
        self.assertNotIn("PR 4", section)

    def _verifier(self):
        records = [rec(1, "FAIL"), rec(2, "FAIL"), rec(3, "FAIL"), rec(4, "PASS"),
                   rec(5, "none")]
        return self._compare("verifier", records, {1: "PASS", 2: "FAIL", 4: "PASS",
                                                   5: "PASS"})

    def test_the_verifier_line_counts_only_recorded_fails(self):
        lines, report, md = self._verifier()
        self.assertEqual(
            f"shadow-replay: matched 2 of 4 (50%) reviewer=verifier model={REPLAY_MODEL} "
            "recorded_model=none", lines[-2])
        self.assertEqual(
            f"shadow-replay: recorded FAIL replayed PASS 1 of 3 reviewer=verifier "
            f"model={REPLAY_MODEL}", lines[-1])
        self.assertEqual(7, len(lines))
        bar = report["recorded_fail_replayed_pass"]
        self.assertEqual((1, 3), (bar["replayed_pass"], bar["of"]))
        self.assertEqual([1], bar["prs_replayed_pass"])
        self.assertEqual([2], bar["prs_not_pass"])
        self.assertEqual([3], bar["prs_no_verdict"])
        self.assertNotIn("acted_on", report["replays"][0])
        section = md.split("## Recorded FAIL replayed PASS", 1)[1]
        self.assertIn("PR 1", section)
        self.assertIn("PR 3", section)
        self.assertNotIn("PR 4", section)

    def test_the_percentage_rounds_down_and_an_empty_bar_is_zero(self):
        self.assertEqual(
            f"shadow-replay: acted-on rejections 2 of 3 rejected again (66%) "
            f"reviewer=critic model={REPLAY_MODEL}",
            _sr().acted_on_line(2, 3, model=REPLAY_MODEL))
        self.assertIn("0 of 0 rejected again (0%)", _sr().acted_on_line(0, 0, model="m"))
        self.assertEqual(
            "shadow-replay: recorded FAIL replayed PASS 0 of 0 reviewer=verifier model=m",
            _sr().fail_to_pass_line(0, 0, model="m"))

    def test_todays_lines_are_byte_identical_with_and_without_acted_on(self):
        """The same records and replays, with and without `acted_on`, print
        today's five lines byte for byte; only the new last line differs."""
        verdicts = {1: "REQUEST_CHANGES", 2: "APPROVE", 4: "APPROVE"}
        with_flag, _, _ = self._critic()
        records = [rec(1, "REQUEST_CHANGES"), rec(2, "REQUEST_CHANGES"),
                   rec(3, "REQUEST_CHANGES"), rec(4, "APPROVE")]
        without, _, _ = self._compare("critic", records, verdicts, tag="plain")
        self.assertEqual(with_flag[:5], without[:5])
        self.assertEqual(6, len(without))
        self.assertEqual("shadow-replay: acted-on rejections 0 of 0 rejected again (0%) "
                         f"reviewer=critic model={REPLAY_MODEL}", without[-1])


# --------------------------------------------------------------------------- #
# 7. With none of it asked for, pick is today's                                #
# --------------------------------------------------------------------------- #

class TodaysPickTest(unittest.TestCase):
    def test_recorded_model_without_unknown_builder_is_todays_pick(self):
        """The newest review per pull request, the approval included, every
        copy on the model whatever its clause, and no `acted_on`."""
        comments = [node(card_copy("REQUEST_CHANGES", _sha(51)), _at(2, 1)),
                    node(card_copy("APPROVE", _sha(53)), _at(4, 1)),
                    node(card_copy("APPROVE", _sha(57), unknown=False), _at(8, 1))]
        commits = {_sha(51): [501], _sha(53): [501], _sha(57): [505]}
        with tempfile.TemporaryDirectory() as tmp, \
                FakeGh(commits=commits, comments={"501": [fix_push(1, _at(2, 3))]}) as gh, \
                mock.patch.object(_sr().linear_ops, "gql_paged", lambda *a, **k: comments):
            pick = Path(tmp) / "pick.json"
            code, out, err = _main(
                "pick", "--reviewer", "critic", "--recorded-model", _opus(),
                "--verify-workflow", "", "--count", "2", "--prs", "", "--repo", REPO,
                "--out", str(pick))
            records = json.loads(pick.read_text(encoding="utf-8"))
            calls = gh.calls()
        self.assertEqual(0, code, out + err)
        self.assertEqual([(505, _sha(57)), (501, _sha(53))],
                         [(r["pr"], r["sha"]) for r in records])
        self.assertTrue(all("acted_on" not in r for r in records))
        self.assertFalse(any("/comments" in c for c in calls), "no pull request is read")
        self.assertNotIn("acted-on", out + err)

    def test_the_verifier_without_unknown_builder_is_still_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = _main(
                "pick", "--reviewer", "verifier", "--recorded-model", "",
                "--verify-workflow", VERIFY_WF, "--count", "20", "--prs", "",
                "--repo", REPO, "--out", str(Path(tmp) / "pick.json"))
        self.assertEqual(1, code)
        self.assertIn("prs", out + err)


# --------------------------------------------------------------------------- #
# 8. The workflow                                                              #
# --------------------------------------------------------------------------- #

class WorkflowInputsTest(unittest.TestCase):
    def setUp(self):
        self.doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        on = self.doc.get("on", self.doc.get(True))
        self.inputs = on["workflow_dispatch"]["inputs"]
        self.steps = self.doc["jobs"]["pick"]["steps"]

    def test_the_three_inputs(self):
        self.assertEqual("boolean", self.inputs["unknown_builder"]["type"])
        self.assertIs(False, self.inputs["unknown_builder"]["default"])
        for name in ("verify_workflow", "from_run"):
            self.assertEqual("string", self.inputs[name]["type"])
            self.assertEqual("", self.inputs[name]["default"])

    def test_from_run_downloads_that_runs_pick_on_the_runs_own_token(self):
        download = next(s for s in self.steps if "gh run download" in s.get("run", ""))
        self.assertEqual("inputs.from_run != ''", download["if"])
        self.assertEqual("${{ github.token }}", download["env"]["GH_TOKEN"])
        self.assertEqual("${{ inputs.from_run }}", download["env"]["FROM_RUN"])
        self.assertIn('gh run download "$FROM_RUN" --repo "$GITHUB_REPOSITORY" '
                      "-n shadow-replay-pick", download["run"])
        self.assertNotIn("${{", download["run"], "the input reaches the shell as env only")
        names = [s.get("name") for s in self.steps]
        self.assertLess(self.steps.index(download), names.index("Pick the reviews to replay"))

    def _pick_argv(self, **env) -> tuple:
        """Run the pick step's script with a `python3` that records its argv."""
        step = next(s for s in self.steps if s.get("id") == "pick")
        for name, value in step["env"].items():
            self.assertTrue(str(value).startswith("${{"), name)
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            stub = d / "python3"
            stub.write_text("#!/bin/sh\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; done "
                            f"> {d / 'argv'}\n", encoding="utf-8")
            stub.chmod(0o755)
            base = {"PATH": f"{d}{os.pathsep}{os.environ['PATH']}", "RUNNER_TEMP": str(d),
                    "GITHUB_REPOSITORY": REPO, "GITHUB_OUTPUT": str(d / "out"),
                    "REVIEWER": "critic", "RECORDED_MODEL": "", "COUNT": "20", "PRS": "",
                    "UNKNOWN_BUILDER": "false", "VERIFY_WORKFLOW": "", "FROM_RUN": ""}
            base.update(env)
            subprocess.run(["bash", "-e", "-c", step["run"]], env=base, check=True)
            return (d / "argv").read_text(encoding="utf-8").splitlines(), str(d)

    def test_the_inputs_reach_pick_as_flags(self):
        step = next(s for s in self.steps if s.get("id") == "pick")
        self.assertEqual("${{ inputs.unknown_builder }}", step["env"]["UNKNOWN_BUILDER"])
        self.assertEqual("${{ inputs.verify_workflow }}", step["env"]["VERIFY_WORKFLOW"])
        self.assertEqual("${{ inputs.from_run }}", step["env"]["FROM_RUN"])
        argv, tmp = self._pick_argv(UNKNOWN_BUILDER="true", VERIFY_WORKFLOW=VERIFY_WF,
                                    FROM_RUN="123")
        self.assertIn("--unknown-builder", argv)
        self.assertEqual(VERIFY_WF, argv[argv.index("--verify-workflow") + 1])
        self.assertIn("--acted-on-only", argv)
        self.assertEqual(f"{tmp}/from-pick/shadow-replay-pick.json",
                         argv[argv.index("--from-pick") + 1])

    def test_with_the_defaults_pick_is_called_as_today(self):
        argv, tmp = self._pick_argv()
        self.assertEqual([
            ".bureau-pipeline/scripts/shadow_replay.py", "pick", "--reviewer", "critic",
            "--recorded-model", "", "--count", "20", "--prs", "", "--repo", REPO,
            "--repo-label", "repo:bureau-pipeline", "--out", f"{tmp}/shadow-replay-pick/shadow-replay-pick.json",
            "--github-output", f"{tmp}/out", "--verify-workflow", ""], argv)


if __name__ == "__main__":
    unittest.main()
