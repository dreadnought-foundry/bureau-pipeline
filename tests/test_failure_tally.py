"""A failure already filed as a card adds a count to that card (DRE-8635).

The title "a fix commit is stranded on the merged branch" was filed eight times
from 09-20, and three of those cards — DRE-4828, DRE-4831 and DRE-4861 — are
one failure, portico's `agent/DRE-4518-mcp-form-read-tools`, filed three
times. Both writers looked only for an OPEN card with the exact title, so a
card a person canceled was filed again on the next sweep.
`scripts/failure_tally.py` is the rule that replaces that look-up, and these
tests replay the DRE-4828 shape on a fake Linear.

Run: python3 -m pytest tests/test_failure_tally.py -v
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import check_act_receipts  # noqa: E402
import failure_tally  # noqa: E402
import notice_writers  # noqa: E402
import pipeline_act  # noqa: E402
import stranded_fix  # noqa: E402

NOW = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
BRANCH = "agent/DRE-4518-mcp-form-read-tools"
TITLE = f"portico: a fix commit is stranded on the merged branch {BRANCH}"
SHA_1 = "4828aaaa" + "0" * 32
SHA_2 = "4831bbbb" + "0" * 32
SHA_3 = "4861cccc" + "0" * 32
LABELS = ["repo:portico", "agent:engineer"]
BODY = "A fix landed on the branch after its pull request merged.\n"


def occ(sha: str, branch: str = BRANCH) -> str:
    return f"{branch}@{sha[:8]}"


def stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class FakeLinear:
    """Linear as `failure_tally` reads and writes it: the repo-scoped title
    search, a card's thread, one comment and one card create."""

    def __init__(self, cards=(), *, search_error=None, now=NOW):
        self.cards = [dict(c) for c in cards]
        self.threads: dict[str, list[dict]] = {}
        self.filed: list[tuple] = []
        self.posted: list[tuple] = []
        self.searches: list[tuple] = []
        self.search_error = search_error
        self.now = now
        self._next = 9000

    def card(self, identifier, *, title=TITLE, state="Planning", description="",
             created=None, completed="", labels=LABELS):
        self.cards.append({
            "identifier": identifier, "title": title, "state": state,
            "description": description,
            "createdAt": stamp(created or self.now - timedelta(days=1)),
            "completedAt": completed, "labels": list(labels)})
        return self

    # --- the four seams ---------------------------------------------------

    def repo_cards_by_title_prefix(self, prefix, *, repo_slug, since):
        self.searches.append((prefix, repo_slug, since))
        if self.search_error is not None:
            raise self.search_error
        out = []
        for c in self.cards:
            created = datetime.fromisoformat(c["createdAt"].replace("Z", "+00:00"))
            if (c["title"].startswith(prefix) and f"repo:{repo_slug}" in c["labels"]
                    and created >= since):
                out.append({k: c[k] for k in ("identifier", "title", "description",
                                              "state", "createdAt", "completedAt")})
        return out

    def comment_records(self, identifier, *, whole_thread=False):
        return list(self.threads.get(identifier, []))

    def cmd_comment(self, identifier, body, *flags):
        self.posted.append((identifier, body))
        self.threads.setdefault(identifier, []).append(
            {"body": body, "authored_by_pipeline": True})
        return None

    def cmd_oneoff(self, title, description_file, *flags):
        body = Path(description_file).read_text(encoding="utf-8")
        labels = [flags[i + 1] for i, f in enumerate(flags) if f == "--label"]
        self._next += 1
        identifier = f"DRE-{self._next}"
        self.filed.append((title, body, list(flags)))
        self.card(identifier, title=title, description=body, created=self.now,
                  labels=labels)
        return {"id": f"uuid-{self._next}", "identifier": identifier,
                "url": f"https://linear.app/x/issue/{identifier}"}

    def patched(self):
        return mock.patch.multiple(
            failure_tally.linear_ops,
            repo_cards_by_title_prefix=self.repo_cards_by_title_prefix,
            comment_records=self.comment_records,
            cmd_comment=self.cmd_comment,
            cmd_oneoff=self.cmd_oneoff,
        )


def run(fake: FakeLinear, occurrence: str, *, title=TITLE, body=BODY):
    out = io.StringIO()
    with fake.patched(), redirect_stdout(out):
        tally = failure_tally.file_or_tally(
            title, body, occurrence=occurrence, repo_slug="portico",
            labels=LABELS, now=fake.now)
    return tally, out.getvalue()


def first_line(body: str) -> str:
    return body.strip().splitlines()[0]


# --------------------------------------------------------------------------- #
# the DRE-4828 replay                                                          #
# --------------------------------------------------------------------------- #


class TheCanceledCardGainsTheCount(unittest.TestCase):
    """DRE-4828 was canceled by a person, and the same branch was found again."""

    def fake(self) -> FakeLinear:
        return FakeLinear().card(
            "DRE-4828", state="Canceled",
            description=BODY + f"\nOccurrence: {occ(SHA_1)}\n",
            created=NOW - timedelta(days=12))

    def test_the_same_branch_and_commit_found_again_files_nothing(self):
        fake = self.fake()
        tally, printed = run(fake, occ(SHA_1))
        self.assertEqual(failure_tally.ALREADY_TALLIED, tally.outcome)
        self.assertEqual("DRE-4828", tally.card)
        self.assertEqual([], fake.filed)
        self.assertEqual([], fake.posted)
        self.assertIn(f"failure-tally: already-tallied · {TITLE} · card=DRE-4828",
                      printed)

    def test_a_new_stranded_commit_counts_two_on_the_canceled_card(self):
        fake = self.fake()
        tally, printed = run(fake, occ(SHA_2))
        self.assertEqual(failure_tally.TALLIED, tally.outcome)
        self.assertEqual("DRE-4828", tally.card)
        self.assertEqual([], fake.filed)
        self.assertEqual(1, len(fake.posted))
        card, body = fake.posted[0]
        self.assertEqual("DRE-4828", card)
        self.assertEqual(f"🔢 failure-tally: 2 · {occ(SHA_2)}", first_line(body))
        self.assertIn(f"failure-tally: tallied · {TITLE} · card=DRE-4828", printed)

    def test_the_receipt_is_composed_through_the_act_registry(self):
        fake = self.fake()
        run(fake, occ(SHA_2))
        fields = pipeline_act.read_trailer(fake.posted[0][1])
        self.assertIsNotNone(fields)
        self.assertEqual("failure-tally", fields["act"])

    def test_the_receipt_says_what_is_new_and_keeps_the_decision(self):
        fake = self.fake()
        run(fake, occ(SHA_2))
        body = fake.posted[0][1]
        self.assertIn(occ(SHA_2), body.split("\n", 1)[1])
        self.assertIn("Canceled", body)

    def test_a_third_new_commit_counts_three(self):
        fake = self.fake()
        run(fake, occ(SHA_2))
        tally, _ = run(fake, occ(SHA_3))
        self.assertEqual(failure_tally.TALLIED, tally.outcome)
        self.assertEqual([], fake.filed)
        self.assertEqual(
            [f"🔢 failure-tally: 2 · {occ(SHA_2)}", f"🔢 failure-tally: 3 · {occ(SHA_3)}"],
            [first_line(b) for _, b in fake.posted])

    def test_every_later_sweep_over_a_counted_commit_answers_already_tallied(self):
        fake = self.fake()
        run(fake, occ(SHA_2))
        for _ in range(3):
            tally, _ = run(fake, occ(SHA_2))
            self.assertEqual(failure_tally.ALREADY_TALLIED, tally.outcome)
        self.assertEqual(1, len(fake.posted))
        self.assertEqual([], fake.filed)

    def test_a_receipt_somebody_else_wrote_is_not_a_count(self):
        """Only the pipeline's own receipts count, so a pasted line neither
        silences a new occurrence nor moves the number."""
        fake = self.fake()
        fake.threads["DRE-4828"] = [{
            "body": f"🔢 failure-tally: 2 · {occ(SHA_2)}",
            "authored_by_pipeline": False}]
        tally, _ = run(fake, occ(SHA_2))
        self.assertEqual(failure_tally.TALLIED, tally.outcome)
        self.assertEqual(f"🔢 failure-tally: 2 · {occ(SHA_2)}",
                         first_line(fake.posted[0][1]))

    def test_a_duplicate_card_and_an_open_card_count_the_same_way(self):
        for state in ("Duplicate", "Planning", "In Progress"):
            with self.subTest(state=state):
                fake = FakeLinear().card(
                    "DRE-4831", state=state,
                    description=BODY + f"\nOccurrence: {occ(SHA_1)}\n")
                tally, _ = run(fake, occ(SHA_2))
                self.assertEqual(failure_tally.TALLIED, tally.outcome)
                self.assertEqual("DRE-4831", fake.posted[0][0])
                self.assertEqual([], fake.filed)

    def test_the_newest_match_is_the_card_counted_on(self):
        fake = (FakeLinear()
                .card("DRE-4828", state="Canceled", created=NOW - timedelta(days=20),
                      description=f"Occurrence: {occ(SHA_1)}")
                .card("DRE-4861", state="Duplicate", created=NOW - timedelta(days=3),
                      description=f"Occurrence: {occ(SHA_2)}"))
        tally, _ = run(fake, occ(SHA_3))
        self.assertEqual("DRE-4861", tally.card)
        self.assertEqual("DRE-4861", fake.posted[0][0])


# --------------------------------------------------------------------------- #
# nothing found, Done, and the unread search                                  #
# --------------------------------------------------------------------------- #


class NothingFoundFilesOneCard(unittest.TestCase):

    def test_it_files_one_card_whose_body_ends_with_the_occurrence(self):
        fake = FakeLinear()
        tally, printed = run(fake, occ(SHA_1))
        self.assertEqual(failure_tally.FILED, tally.outcome)
        self.assertEqual(1, len(fake.filed))
        title, body, flags = fake.filed[0]
        self.assertEqual(TITLE, title)
        self.assertTrue(body.startswith(BODY.rstrip("\n")))
        self.assertEqual(f"Occurrence: {occ(SHA_1)}", body.rstrip("\n").splitlines()[-1])
        self.assertEqual(["--label", "repo:portico", "--label", "agent:engineer"], flags)
        self.assertEqual([], fake.posted)
        self.assertEqual(tally.card, fake.cards[-1]["identifier"])
        self.assertIn(f"failure-tally: filed · {TITLE} · card={tally.card}", printed)

    def test_the_search_reads_thirty_days_of_this_repo_by_the_title(self):
        fake = FakeLinear()
        run(fake, occ(SHA_1))
        self.assertEqual(
            [(TITLE, "portico", NOW - timedelta(days=failure_tally.FAILURE_TALLY_WINDOW_DAYS))],
            fake.searches)
        self.assertEqual(30, failure_tally.FAILURE_TALLY_WINDOW_DAYS)

    def test_a_card_older_than_the_window_is_not_a_match(self):
        fake = FakeLinear().card(
            "DRE-4000", state="Canceled", created=NOW - timedelta(days=31),
            description=f"Occurrence: {occ(SHA_1)}")
        tally, _ = run(fake, occ(SHA_2))
        self.assertEqual(failure_tally.FILED, tally.outcome)
        self.assertEqual(1, len(fake.filed))

    def test_a_title_that_only_starts_the_same_is_not_a_match(self):
        fake = FakeLinear().card(
            "DRE-4100", title=TITLE + "-v2", state="Canceled",
            description=f"Occurrence: {occ(SHA_1, BRANCH + '-v2')}")
        tally, _ = run(fake, occ(SHA_1))
        self.assertEqual(failure_tally.FILED, tally.outcome)
        self.assertEqual([], fake.posted)

    def test_the_filed_card_is_found_by_the_next_sweep(self):
        fake = FakeLinear()
        run(fake, occ(SHA_1))
        tally, _ = run(fake, occ(SHA_1))
        self.assertEqual(failure_tally.ALREADY_TALLIED, tally.outcome)
        self.assertEqual(1, len(fake.filed))
        self.assertEqual([], fake.posted)


class ADoneCardMeansTheFixDidNotHold(unittest.TestCase):

    def fake(self, receipts=0) -> FakeLinear:
        fake = FakeLinear().card(
            "DRE-4828", state="Done", created=NOW - timedelta(days=12),
            completed="2026-10-25T16:40:00.000Z",
            description=BODY + f"\nOccurrence: {occ(SHA_1)}\n")
        fake.threads["DRE-4828"] = [
            {"body": f"🔢 failure-tally: {n + 2} · {occ(f'{n:08d}')}",
             "authored_by_pipeline": True}
            for n in range(receipts)]
        return fake

    def test_a_new_occurrence_files_one_new_card_naming_the_done_card(self):
        fake = self.fake()
        tally, _ = run(fake, occ(SHA_2))
        self.assertEqual(failure_tally.FILED, tally.outcome)
        self.assertEqual([], fake.posted)
        self.assertEqual(1, len(fake.filed))
        _, body, _ = fake.filed[0]
        self.assertIn("This is occurrence 2 of this failure; DRE-4828 closed it as "
                      "Done on 2026-10-25.", body)
        self.assertEqual(f"Occurrence: {occ(SHA_2)}", body.rstrip("\n").splitlines()[-1])
        self.assertNotEqual("DRE-4828", tally.card)

    def test_the_occurrence_number_counts_the_done_card_s_receipts(self):
        fake = self.fake(receipts=2)
        run(fake, occ(SHA_2))
        self.assertIn("This is occurrence 4 of this failure; DRE-4828",
                      fake.filed[0][1])

    def test_the_occurrence_the_done_card_already_names_files_nothing(self):
        fake = self.fake()
        tally, _ = run(fake, occ(SHA_1))
        self.assertEqual(failure_tally.ALREADY_TALLIED, tally.outcome)
        self.assertEqual([], fake.filed)


class AnUnreadSearchFilesNothing(unittest.TestCase):

    def test_a_search_that_raises_files_nothing_and_answers_unread(self):
        fake = FakeLinear(search_error=RuntimeError("Linear answered 502"))
        tally, printed = run(fake, occ(SHA_1))
        self.assertEqual(failure_tally.UNREAD, tally.outcome)
        self.assertIsNone(tally.card)
        self.assertEqual([], fake.filed)
        self.assertEqual([], fake.posted)
        self.assertIn(f"failure-tally: unread · {TITLE} · card=none", printed)

    def test_an_unread_thread_counts_nothing_and_files_nothing(self):
        fake = FakeLinear().card("DRE-4828", state="Canceled",
                                 description=f"Occurrence: {occ(SHA_1)}")

        def unread(*_a, **_k):
            raise RuntimeError("Linear answered 502")

        fake.comment_records = unread
        tally, _ = run(fake, occ(SHA_2))
        self.assertEqual(failure_tally.UNREAD, tally.outcome)
        self.assertEqual([], fake.filed)
        self.assertEqual([], fake.posted)


# --------------------------------------------------------------------------- #
# the occurrence the stranded-fix class carries                               #
# --------------------------------------------------------------------------- #


def commit(sha: str, date: str) -> dict:
    return {"sha": sha, "commit": {"message": "fix: x", "committer": {"date": date}}}


def found(*commits, branch=BRANCH):
    return stranded_fix.Stranded(612, branch, "main", "2026-09-20T10:00:00Z",
                                 tuple(commits), "")


class TheStrandedFixOccurrence(unittest.TestCase):

    def test_it_is_the_branch_at_the_newest_stranded_commit(self):
        self.assertEqual(
            occ(SHA_2),
            stranded_fix.occurrence(found(commit(SHA_1, "2026-09-20T11:00:00Z"),
                                          commit(SHA_2, "2026-09-20T12:00:00Z"))))

    def test_newest_is_read_off_the_commit_date_not_the_order(self):
        self.assertEqual(
            occ(SHA_1),
            stranded_fix.occurrence(found(commit(SHA_1, "2026-09-20T13:00:00Z"),
                                          commit(SHA_2, "2026-09-20T12:00:00Z"))))

    def test_route_writes_the_occurrence_beside_the_title(self):
        pr = {"number": 612, "headRefName": BRANCH, "baseRefName": "main",
              "mergedAt": "2026-09-20T10:00:00Z", "url": ""}
        with tempfile.TemporaryDirectory() as td:
            compare = os.path.join(td, "compare.json")
            Path(compare).write_text(json.dumps({
                "ahead_by": 1, "commits": [commit(SHA_1, "2026-09-20T11:00:00Z")]}))
            out = {k: os.path.join(td, k) for k in ("title", "body", "occurrence")}
            with mock.patch.object(stranded_fix, "_gh",
                                   return_value=(json.dumps(pr), "")):
                rc = stranded_fix.main([
                    "route", "--repo", "dreadnought-foundry/portico", "--pr", "612",
                    "--compare-file", compare, "--out-title", out["title"],
                    "--out-body", out["body"], "--out-occurrence", out["occurrence"]])
            self.assertEqual(0, rc)
            self.assertEqual(TITLE, Path(out["title"]).read_text())
            self.assertEqual(occ(SHA_1), Path(out["occurrence"]).read_text())

    def test_the_title_stays_the_per_branch_key(self):
        self.assertEqual(TITLE, stranded_fix.card_title("portico", BRANCH))


# --------------------------------------------------------------------------- #
# writer 1: the reconcile sweep                                                #
# --------------------------------------------------------------------------- #


class TheSweepFilesThroughTheTally(unittest.TestCase):

    def test_twice_over_the_same_finding_is_one_card_and_no_receipt(self):
        import reconcile

        fake = FakeLinear(now=datetime.now(UTC))
        stranded = found(commit(SHA_1, "2026-09-20T11:00:00Z"))
        with fake.patched(), mock.patch.object(
                reconcile, "REPO", "dreadnought-foundry/portico"), \
                redirect_stdout(io.StringIO()):
            reconcile._file_stranded_fix_card(stranded, "DRE-4518")
            reconcile._file_stranded_fix_card(stranded, "DRE-4518")
        self.assertEqual(1, len(fake.filed))
        self.assertEqual([], fake.posted)
        title, body, flags = fake.filed[0]
        self.assertEqual(TITLE, title)
        self.assertEqual(f"Occurrence: {occ(SHA_1)}", body.rstrip("\n").splitlines()[-1])
        self.assertIn("repo:portico", flags)
        self.assertIn("agent:engineer", flags)

    def test_a_new_commit_on_a_canceled_card_counts_rather_than_files(self):
        import reconcile

        fake = FakeLinear(now=datetime.now(UTC)).card(
            "DRE-4828", state="Canceled", description=f"Occurrence: {occ(SHA_1)}")
        stranded = found(commit(SHA_2, "2026-09-20T11:00:00Z"))
        with fake.patched(), mock.patch.object(
                reconcile, "REPO", "dreadnought-foundry/portico"), \
                redirect_stdout(io.StringIO()):
            reconcile._file_stranded_fix_card(stranded, "DRE-4518")
        self.assertEqual([], fake.filed)
        self.assertEqual(f"🔢 failure-tally: 2 · {occ(SHA_2)}",
                         first_line(fake.posted[0][1]))

    def test_the_sweep_no_longer_looks_for_an_open_card_only(self):
        import inspect

        import reconcile

        source = inspect.getsource(reconcile._file_stranded_fix_card)
        self.assertIn("failure_tally.file_or_tally(", source)
        self.assertNotIn("find_open", source)
        self.assertNotIn("cmd_oneoff", source)


# --------------------------------------------------------------------------- #
# writer 2: the fix run's report, driven as a file                             #
# --------------------------------------------------------------------------- #


def _executable(path: str, body: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, 0o755)


class TheFixReportFilesThroughTheTally(unittest.TestCase):
    """scripts/report_fix_result.sh's merged-while-working block with `gh`,
    `git`, `linear_ops.py` and `failure_tally.py` stubbed to record their
    argv. `stranded_fix.py` and `fix_handoff.py` are the real ones."""

    #: The harness keys this run's handoff to its own repo, so the scenario
    #: runs as bureau-pipeline and the title carries that slug.
    TITLE = stranded_fix.card_title("bureau-pipeline", BRANCH)

    def run_block(self):
        from test_act_emission_scenario import (  # noqa: PLC0415
            REPO,
            ROOT as _ROOT,
            _open_handoff,
            _report_env,
        )

        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, True)
        base = os.path.join(td, ".bureau-pipeline")
        shutil.copytree(os.path.join(_ROOT, "scripts"), os.path.join(base, "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(os.path.join(_ROOT, "config"), os.path.join(base, "config"))
        log = os.path.join(td, "calls.jsonl")
        recorder = ("#!/usr/bin/env python3\nimport json, os, sys\n"
                    f"open({log!r}, 'a').write(json.dumps("
                    "[os.path.basename(sys.argv[0])] + sys.argv[1:]) + '\\n')\n")
        _executable(os.path.join(base, "scripts", "linear_ops.py"), recorder)
        _executable(os.path.join(base, "scripts", "failure_tally.py"), recorder)
        binary = os.path.join(td, "bin")
        os.makedirs(binary)
        pr = {"number": 612, "headRefName": BRANCH, "baseRefName": "main",
              "mergedAt": "2026-09-20T10:00:00Z",
              "url": f"https://github.com/{REPO}/pull/612"}
        _executable(os.path.join(binary, "gh"), f"""#!/usr/bin/env python3
import json, sys
argv = sys.argv[1:]
pr = {json.dumps(pr)}
if argv[:2] == ["pr", "view"]:
    if "--jq" in argv:
        field = argv[argv.index("--jq") + 1].lstrip(".")
        print("MERGED" if field == "state" else pr[field])
    else:
        print(json.dumps(pr))
    sys.exit(0)
sys.exit(0)
""")
        _executable(os.path.join(binary, "git"), f"""#!/usr/bin/env python3
import sys
argv = sys.argv[1:]
if argv[:1] == ["log"]:
    print("{SHA_1}\\x1f2026-09-20T11:00:00Z\\x1ffix: the late commit")
    sys.exit(0)
if argv[:1] == ["fetch"]:
    sys.exit(1)
if argv[:1] == ["rev-parse"]:
    print("{'e' * 40}")
sys.exit(0)
""")
        _open_handoff(td, blocked=False)
        env = {k: v for k, v in os.environ.items()
               if k not in ("CARD", "PR", "REPO", "PRE_SHA")}
        env.update(PATH=binary + os.pathsep + os.environ["PATH"],
                   CLASSIFICATION="", DISPATCH_TOKEN="test",
                   **_report_env(td, "fix"))
        proc = subprocess.run(
            ["bash", os.path.join(base, "scripts", "report_fix_result.sh")],
            cwd=td, env=env, capture_output=True, text=True)
        calls = [json.loads(line) for line in
                 (open(log).read().splitlines() if os.path.exists(log) else [])]
        return proc, calls

    def test_it_calls_failure_tally_file_and_never_oneoff(self):
        proc, calls = self.run_block()
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        tallies = [c for c in calls if c[0] == "failure_tally.py"]
        self.assertEqual(1, len(tallies), calls)
        argv = tallies[0][1:]
        self.assertEqual("file", argv[0])

        def value(flag):
            return argv[argv.index(flag) + 1]

        self.assertEqual(self.TITLE, value("--title"))
        self.assertEqual(occ(SHA_1), value("--occurrence"))
        self.assertEqual("bureau-pipeline", value("--repo"))
        self.assertEqual(["repo:bureau-pipeline", "agent:engineer"],
                         [argv[i + 1] for i, a in enumerate(argv) if a == "--label"])
        self.assertIn(SHA_1[:8], Path(value("--body-file")).read_text())
        linear = [c for c in calls if c[0] == "linear_ops.py"]
        self.assertEqual([], [c for c in linear if c[1:2] in (["oneoff"], ["find-open"])],
                         calls)

    def test_the_script_holds_no_oneoff_and_no_find_open(self):
        text = (ROOT / "scripts" / "report_fix_result.sh").read_text()
        code = "\n".join(line for line in text.splitlines()
                         if not line.lstrip().startswith("#"))
        self.assertNotIn("linear_ops.py oneoff", code)
        self.assertNotIn("find-open", code)
        self.assertIn("failure_tally.py file", code)


# --------------------------------------------------------------------------- #
# the CLI                                                                      #
# --------------------------------------------------------------------------- #


class TheCli(unittest.TestCase):

    def test_file_reads_the_body_file_and_passes_every_label(self):
        fake = FakeLinear(now=datetime.now(UTC))
        with tempfile.TemporaryDirectory() as td:
            body = os.path.join(td, "body.md")
            Path(body).write_text(BODY, encoding="utf-8")
            with fake.patched(), redirect_stdout(io.StringIO()):
                rc = failure_tally.main([
                    "file", "--title", TITLE, "--body-file", body,
                    "--occurrence", occ(SHA_1), "--repo", "portico",
                    "--label", "repo:portico", "--label", "agent:engineer"])
        self.assertEqual(0, rc)
        self.assertEqual(1, len(fake.filed))
        self.assertEqual(["--label", "repo:portico", "--label", "agent:engineer"],
                         fake.filed[0][2])

    def test_an_unread_search_exits_non_zero(self):
        fake = FakeLinear(search_error=RuntimeError("502"))
        with tempfile.TemporaryDirectory() as td:
            body = os.path.join(td, "body.md")
            Path(body).write_text(BODY, encoding="utf-8")
            with fake.patched(), redirect_stdout(io.StringIO()):
                rc = failure_tally.main([
                    "file", "--title", TITLE, "--body-file", body,
                    "--occurrence", occ(SHA_1), "--repo", "portico"])
        self.assertNotEqual(0, rc)
        self.assertEqual([], fake.filed)


# --------------------------------------------------------------------------- #
# the rows                                                                     #
# --------------------------------------------------------------------------- #


class TheRows(unittest.TestCase):

    def test_the_act_row_is_declared_and_anchored(self):
        entry = pipeline_act.record("failure-tally")
        self.assertEqual("scripts/failure_tally.py", entry["emits"]["file"])
        self.assertEqual('pipeline_act.receipt("failure-tally"', entry["emits"]["anchor"])

    def test_the_registry_and_the_receipt_guard_pass_with_the_row(self):
        self.assertEqual([], pipeline_act.problems())
        self.assertEqual([], check_act_receipts.problems())

    def test_both_fail_with_the_row_removed(self):
        doc = pipeline_act.load()
        stripped = {**doc, "acts": [a for a in doc["acts"]
                                    if a.get("name") != "failure-tally"]}
        self.assertTrue(any("failure_tally.py" in p or "FAILURE_TALLY_TAG" in p
                            for p in pipeline_act.problems(stripped)),
                        pipeline_act.problems(stripped))
        self.assertTrue(any("failure_tally.py" in p
                            for p in check_act_receipts.problems(stripped)),
                        check_act_receipts.problems(stripped))

    def test_the_module_is_a_declared_card_writer(self):
        rows = notice_writers.load()
        mine = [r for r in rows if r.get("file") == "scripts/failure_tally.py"]
        self.assertEqual(1, len(mine))
        self.assertEqual("kept", mine[0]["disposition"])
        self.assertTrue(mine[0]["reason"].startswith("work"))
        self.assertEqual([], notice_writers.row_problems(rows))

    def test_the_two_stranded_fix_rows_name_it(self):
        rows = {r["id"]: r for r in notice_writers.load()}
        for wid in ("stranded-fix", "fix-result-stranded"):
            with self.subTest(row=wid):
                self.assertIn("failure_tally", rows[wid]["reason"])

    def test_the_notice_writer_check_passes(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, notice_writers.check(None))


if __name__ == "__main__":
    unittest.main()
