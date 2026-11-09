"""The weekly parts list, posted once a week as one `🪴 parts-weekly:` comment
on the standing parts card (DRE-9055).

`scripts/parts_weekly.py post` reads one folder per roster repo — the sweep's
`count.json` (DRE-8944) and the provisional scan's `provisional.json`
(DRE-8941) — reads each new part's reasoning back from its own pull request
through `parts_provisional.final_approval`, and posts one comment: a first
line, plain-English lines, and one fenced `json` block the console's Sunday
briefing reads (DRE-9057).

The stand-ins here are GitHub as `gh_read_retry.read` reaches it, the
standing card's thread as `spoken_thread.read` returns it, and the comment
write as `linear_ops.cmd_comment`. The new part's reasoning is `ZZ-REASON-2`,
which may go into the posted comment and nowhere else.

Run: cd bureau-pipeline && python3 -m pytest tests/test_parts_weekly.py -v
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import gh_read_retry  # noqa: E402
import linear_ops  # noqa: E402
import parts_weekly  # noqa: E402
import spoken_thread  # noqa: E402

CARD = "DRE-9100"
QA = "agent-bureau-qa-bot[bot]"
SHA = "a" * 40
REASON = "ZZ-REASON-2"
OWNER = "dreadnought-foundry"

#: Sunday 2026-11-08 to Saturday 2026-11-14, all of it Pacific standard time.
WEEK_ENDING = "2026-11-14"
START, END = "2026-11-08", "2026-11-14"
HEADER = f"🪴 parts-weekly: {START}..{END}"
NOW = datetime(2026, 11, 15, 16, 0, tzinfo=UTC)  # 2026-11-15 08:00 PT

BADGE = "ui/atoms/Badge.tsx"
PILL = "ui/atoms/Pill.tsx"
CHIP = "ui/atoms/Chip.tsx"
TAG = "ui/atoms/Tag.tsx"
LATE = "ui/atoms/Late.tsx"
OLD = "ui/atoms/Old.tsx"

ROSTER = ["agent-bureau", "portico", "quiet", "elsewhere", "attic", "broken", "ghost"]


def url(repo: str, number: int) -> str:
    return f"https://github.com/{OWNER}/{repo}/pull/{number}"


def part(name, path, pr, approved_at, *, repo="agent-bureau", status="provisional",
         permanent_on=None, rejected_on=None, replacement_card=None, recorded_now=False,
         closest=None):
    return {"name": name, "path": path, "pr": pr, "pr_url": url(repo, pr),
            "approved_at": approved_at, "status": status, "permanent_on": permanent_on,
            "rejected_on": rejected_on, "replacement_card": replacement_card,
            "recorded_now": recorded_now, "closest": closest}


def rejection(at, counted, why=None, part_named=None):
    return {"at": at, "counted": counted, "part": part_named, "why": why}


def counted(repo, copies, last_week, *, listed=True, last_week_reason=None):
    return {"repo": repo, "state": "counted", "listed": listed, "reason": None,
            "source": ".github/bureau/design-system.json", "copies": copies,
            "last_week": last_week, "last_week_sha": None if last_week is None else SHA,
            "last_week_reason": last_week_reason}


def other(repo, state, reason, listed=None):
    return {"repo": repo, "state": state, "listed": listed, "reason": reason,
            "source": None, "copies": None, "last_week": None, "last_week_sha": None,
            "last_week_reason": None}


LINEAR_WHY = ("written in Linear rather than from the console, so nothing signed it "
              "as the CEO's")


def fixture() -> dict:
    """`{slug: (count, provisional)}` — the whole roster but `ghost`."""
    bureau_parts = [
        part("Badge", BADGE, 4601, "2026-11-10T18:00:00Z",
             closest={"name": "Pill", "path": PILL}),
        part("Chip", CHIP, 4590, "2026-10-27T17:00:00Z", status="permanent",
             permanent_on="2026-11-10", recorded_now=True),
        part("Tag", TAG, 4595, "2026-11-02T17:00:00Z", status="rejected",
             rejected_on="2026-11-12", replacement_card="DRE-9200", recorded_now=True),
        part("Late", LATE, 4580, "2026-10-20T17:00:00Z", status="permanent",
             permanent_on="2026-11-03", recorded_now=True),
        part("Old", OLD, 4570, "2026-10-14T17:00:00Z", status="permanent",
             permanent_on="2026-10-28"),
    ]
    bureau_rejections = [
        rejection("2026-11-11T17:00:00Z", False, LINEAR_WHY, "agent-bureau/Tag"),
        rejection("2026-11-12T17:00:00Z", True, None, "agent-bureau/Tag"),
    ]
    portico_rejections = [
        rejection("2026-11-11T17:00:00Z", False, LINEAR_WHY, "agent-bureau/Tag"),
        rejection("2026-11-12T17:00:00Z", False,
                  "names no provisional part of portico as portico/<Name>",
                  "agent-bureau/Tag"),
        rejection("2026-11-07T17:00:00Z", False, LINEAR_WHY, None),
    ]
    return {
        "agent-bureau": (counted("agent-bureau", 3, 4),
                         {"repo": "agent-bureau", "card": CARD, "since": "2026-11-01",
                          "parts": bureau_parts, "rejections": bureau_rejections}),
        "portico": (counted("portico", 2, None, listed=False,
                            last_week_reason="no commit on the branch is a week old"),
                    {"repo": "portico", "card": CARD, "since": "2026-11-01",
                     "parts": [], "rejections": portico_rejections}),
        "quiet": (other("quiet", "skipped", "no design system declared", listed=True),
                  None),
        "elsewhere": (other("elsewhere", "unread",
                            "the read token could not be minted for x/elsewhere"), None),
        "attic": (other("attic", "archived",
                        "archived on GitHub, so it is read-only and not swept"), None),
        "broken": (other("broken", "failed", "the read token could not be minted for "
                                             f"{OWNER}/broken, inside this organization"),
                   None),
    }


def verdict(parts, reasoning=REASON, at="2026-11-10T18:00:00Z") -> dict:
    lines = [f"🔎 QA Critic — VERDICT: APPROVE @{SHA}", "", "The change is sound."]
    lines += [f"🪴 part-provisional: `{name}` at `{path}` — reasoning: {reasoning}"
              for name, path in parts]
    return {"user": {"login": QA}, "body": "\n".join(lines), "created_at": at}


class GitHub:
    """One pull request and its issue comments per `(repo, number)`."""

    def __init__(self):
        self.pulls = {}
        self.unreadable = set()
        self.reads = []

    def add(self, repo, number, merged_at, comments):
        self.pulls[(repo, number)] = ({"number": number, "merged_at": merged_at},
                                      list(comments))

    def read(self, args, **_):
        args = list(args)
        path = args[-1]
        self.reads.append(path)
        one = re.fullmatch(rf"repos/{OWNER}/([\w.-]+)/pulls/(\d+)", path)
        thread = re.fullmatch(rf"repos/{OWNER}/([\w.-]+)/issues/(\d+)/comments\?per_page=100",
                              path)
        found = one or thread
        if not found:
            raise AssertionError(f"an unexpected read: {args}")
        key = (found[1], int(found[2]))
        if key in self.unreadable or key not in self.pulls:
            raise gh_read_retry.GhReadError("HTTP 404: Not Found")
        pull, comments = self.pulls[key]
        return json.dumps(pull if one else [comments])


class Linear:
    """The standing card's thread, and the one comment write."""

    def __init__(self):
        self.voices = []
        self.posted = []
        self.reads = []
        self.cap = False

    def read(self, card):
        self.reads.append(card)
        return list(self.voices)

    def comment(self, card, body, *flags):
        assert not flags, flags
        if self.cap:
            return linear_ops.COMMENT_CAP_CONDITION
        self.posted.append((card, body))
        return None


class Run:
    def __init__(self, code, out, err):
        self.code, self.out, self.err = code, out, err
        self.err_lines = err.splitlines()


_FENCED = re.compile(r"^```json\n(.*?)\n```$", re.S | re.M)


def block(body: str) -> dict:
    found = _FENCED.findall(body)
    assert len(found) == 1, body
    return json.loads(found[0])


class WeeklyCase(unittest.TestCase):

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "records"
        self.dir.mkdir()
        self.gh = GitHub()
        self.gh.add("agent-bureau", 4601, "2026-11-10T19:00:00Z",
                    [verdict([("Badge", BADGE)])])
        self.linear = Linear()
        self.plant(fixture())

    def plant(self, records):
        for slug, (count, provisional) in records.items():
            folder = self.dir / slug
            folder.mkdir(exist_ok=True)
            (folder / "count.json").write_text(json.dumps(count), encoding="utf-8")
            if provisional is not None:
                (folder / "provisional.json").write_text(json.dumps(provisional),
                                                         encoding="utf-8")

    def post(self, *extra, repos=None, card=CARD, week_ending=WEEK_ENDING, now=NOW):
        repos = json.dumps(ROSTER) if repos is None else repos
        argv = ["post", "--in", str(self.dir), "--repos", repos, "--card", card,
                "--qa-login", QA, *extra]
        if week_ending is not None:
            argv += ["--week-ending", week_ending]
        out, err = io.StringIO(), io.StringIO()
        with patch.object(gh_read_retry, "read", self.gh.read), \
                patch.object(spoken_thread, "read", self.linear.read), \
                patch.object(linear_ops, "cmd_comment", self.linear.comment), \
                patch.object(parts_weekly, "_now", lambda: now), \
                redirect_stdout(out), redirect_stderr(err):
            code = parts_weekly.main(argv)
        return Run(code, out.getvalue(), err.getvalue())

    def posted(self) -> tuple[str, dict]:
        self.assertEqual(1, len(self.linear.posted), self.linear.posted)
        card, body = self.linear.posted[0]
        self.assertEqual(CARD, card)
        return body, block(body)


class ContractTest(WeeklyCase):

    def test_the_marks(self):
        self.assertEqual("🪴 parts-weekly:", parts_weekly.POST_MARK)
        self.assertEqual("parts-weekly/1", parts_weekly.SCHEMA)
        self.assertEqual("parts-weekly:", parts_weekly.TAG)
        self.assertEqual(HEADER, parts_weekly.header(START, END))

    def test_the_week_is_the_groom_scorecards_imported_not_copied(self):
        import groom_scorecard

        self.assertIs(groom_scorecard.week_of, parts_weekly.week_of)
        self.assertIs(groom_scorecard.default_week_ending, parts_weekly.default_week_ending)
        run = self.post(week_ending=None)
        self.assertEqual(0, run.code, run.err)
        start, end = groom_scorecard.week_of(groom_scorecard.default_week_ending(NOW))
        self.assertEqual(("2026-11-08", "2026-11-14"), (start, end))
        body, _ = self.posted()
        self.assertEqual(f"🪴 parts-weekly: {start}..{end}", body.splitlines()[0])

    def test_one_comment_whose_block_is_the_contracts_object(self):
        run = self.post()
        self.assertEqual(0, run.code, run.err)
        body, doc = self.posted()
        self.assertEqual(HEADER, body.splitlines()[0])
        self.assertTrue(body.rstrip().endswith("```"), body)
        self.assertEqual(["schema", "week_start", "week_end", "posted_at", "card",
                          "new_parts", "became_permanent", "rejected", "not_counted",
                          "repos"], list(doc))
        self.assertEqual("parts-weekly/1", doc["schema"])
        self.assertEqual((START, END), (doc["week_start"], doc["week_end"]))
        self.assertEqual("2026-11-15 08:00 PT", doc["posted_at"])
        self.assertEqual(CARD, doc["card"])

        self.assertEqual([{
            "repo": "agent-bureau", "name": "Badge", "path": BADGE, "pr": 4601,
            "pr_url": url("agent-bureau", 4601), "approved_at": "2026-11-10 10:00 PT",
            "reasoning": REASON, "closest": {"name": "Pill", "path": PILL},
            "permanent_on": "2026-11-24", "reject_by": "2026-11-24 10:00 PT",
            "reject_with": "reject agent-bureau/Badge"}], doc["new_parts"])
        self.assertEqual([
            {"repo": "agent-bureau", "name": "Chip", "path": CHIP, "pr": 4590,
             "permanent_on": "2026-11-10", "late": False},
            {"repo": "agent-bureau", "name": "Late", "path": LATE, "pr": 4580,
             "permanent_on": "2026-11-03", "late": True},
        ], sorted(doc["became_permanent"], key=lambda r: r["name"]))
        self.assertEqual([{"repo": "agent-bureau", "name": "Tag", "path": TAG, "pr": 4595,
                           "rejected_on": "2026-11-12", "replacement_card": "DRE-9200",
                           "late": False}], doc["rejected"])
        self.assertEqual([{"at": "2026-11-11 09:00 PT", "why": LINEAR_WHY}],
                         doc["not_counted"])

        repos = {row["repo"]: row for row in doc["repos"]}
        self.assertEqual(ROSTER, [row["repo"] for row in doc["repos"]])
        self.assertEqual({"repo": "agent-bureau", "state": "counted", "carded": True,
                          "copies": 3, "last_week": 4, "change": -1, "reason": None},
                         repos["agent-bureau"])
        for slug, state in (("quiet", "skipped"), ("elsewhere", "unread"),
                            ("attic", "archived"), ("broken", "failed")):
            row = repos[slug]
            self.assertEqual(state, row["state"], row)
            self.assertFalse(row["carded"], row)
            self.assertEqual((None, None, None),
                             (row["copies"], row["last_week"], row["change"]), row)
            self.assertTrue(row["reason"], row)
        self.assertEqual({"repo": "ghost", "state": "failed", "carded": False,
                          "copies": None, "last_week": None, "change": None,
                          "reason": "its leg left no record"}, repos["ghost"])

    def test_the_words_name_the_part_how_to_reject_it_and_each_repo(self):
        self.post()
        body, _ = self.posted()
        self.assertIn(
            f'To reject Badge, write on {CARD} from the console a message that starts '
            f'with "reject" and names agent-bureau/Badge, before 2026-11-24 10:00 PT. '
            f'A message written in Linear does not count. If nobody rejects it, it '
            f'becomes permanent then.', body)
        self.assertIn("Pill", body)
        self.assertIn("DRE-9200", body)
        self.assertIn("recorded late", body)
        self.assertIn("ghost", body)
        self.assertIn("its leg left no record", body)
        self.assertNotIn("Old", body.split("```json")[0])

    def test_the_posted_line_names_no_part_and_no_reasoning(self):
        run = self.post()
        self.assertIn(f"parts-weekly: posted {START}..{END} on {CARD} — 1 new part(s), "
                      f"2 permanent, 1 rejected, 1 not counted, 5 copies across "
                      f"7 repo(s)", run.err_lines)
        for word in ("Badge", "Chip", "Tag", BADGE, REASON):
            self.assertNotIn(word, run.err + run.out)


class OnceAWeekTest(WeeklyCase):

    def test_a_second_run_over_the_pipelines_comment_posts_nothing(self):
        self.post()
        body, _ = self.posted()
        self.linear.voices = [spoken_thread.Voice(spoken_thread.PIPELINE, "the pipeline",
                                                  "2026-11-15T16:00:00Z", body)]
        self.linear.posted.clear()
        run = self.post()
        self.assertEqual(0, run.code, run.err)
        self.assertEqual([], self.linear.posted)
        self.assertIn(f"parts-weekly: already posted {START}..{END} on {CARD}",
                      run.err_lines)

    def test_the_same_first_line_by_anyone_else_is_not_posted(self):
        for kind in (spoken_thread.PERSON, spoken_thread.INTEGRATION,
                     spoken_thread.UNKNOWN, spoken_thread.CEO_VIA_CONSOLE):
            with self.subTest(kind=kind):
                self.linear.voices = [spoken_thread.Voice(
                    kind, kind, "2026-11-15T16:00:00Z", f"{HEADER}\n\nnot the list")]
                self.linear.posted.clear()
                run = self.post()
                self.assertEqual(0, run.code, run.err)
                self.posted()

    def test_an_unchecked_comment_never_counts_as_posted(self):
        """A comment whose console receipt could not be checked has its text
        withheld, so it is nobody's voice and cannot stand for the list."""
        self.linear.voices = [spoken_thread.Voice(
            spoken_thread.UNCHECKED, "unchecked", "2026-11-15T16:00:00Z", None,
            "the console's key could not be read")]
        run = self.post()
        self.assertEqual(0, run.code, run.err)
        self.posted()

    def test_another_weeks_list_does_not_count(self):
        self.linear.voices = [spoken_thread.Voice(
            spoken_thread.PIPELINE, "the pipeline", "2026-11-08T16:00:00Z",
            "🪴 parts-weekly: 2026-11-01..2026-11-07\n\nlast week")]
        self.post()
        self.posted()

    def test_a_thread_that_cannot_be_read_is_unknown_and_posts_nothing(self):
        def broken(card):
            raise linear_ops.LinearError("Linear answered 500")

        self.linear.read = broken
        run = self.post()
        self.assertEqual(1, run.code)
        self.assertEqual([], self.linear.posted)
        self.assertTrue(any(line.startswith("parts-weekly: UNKNOWN — ")
                            for line in run.err_lines), run.err)


class ReasoningTest(WeeklyCase):

    def test_the_reasoning_is_read_through_final_approval(self):
        import parts_provisional

        calls = []
        real = parts_provisional.final_approval

        def spy(full_name, pr, qa_login):
            calls.append((full_name, pr, qa_login))
            return real(full_name, pr, qa_login)

        with patch.object(parts_provisional, "final_approval", spy):
            self.post()
        self.assertEqual([(f"{OWNER}/agent-bureau", 4601, QA)], calls)
        _, doc = self.posted()
        self.assertEqual(REASON, doc["new_parts"][0]["reasoning"])

    def test_a_pull_request_that_cannot_be_read_gives_null_and_says_so(self):
        self.gh.unreadable.add(("agent-bureau", 4601))
        run = self.post()
        self.assertEqual(0, run.code, run.err)
        body, doc = self.posted()
        self.assertIsNone(doc["new_parts"][0]["reasoning"])
        self.assertIn("reasoning could not be read", body.split("```json")[0])
        self.assertIn("parts-weekly: 1 part(s) whose reasoning could not be read",
                      run.err_lines)

    def test_a_verdict_that_no_longer_names_the_part_gives_null(self):
        self.gh.add("agent-bureau", 4601, "2026-11-10T19:00:00Z",
                    [verdict([("Other", PILL)])])
        run = self.post()
        _, doc = self.posted()
        self.assertIsNone(doc["new_parts"][0]["reasoning"])
        self.assertIn("parts-weekly: 1 part(s) whose reasoning could not be read",
                      run.err_lines)

    def test_every_part_read_needs_no_count_line(self):
        run = self.post()
        self.assertFalse([line for line in run.err_lines if "could not be read" in line])

    def test_the_reasoning_goes_into_the_comment_and_nowhere_else(self):
        before = {p: p.read_bytes() for p in Path(self.tmp.name).rglob("*") if p.is_file()}
        cwd = Path.cwd()
        cwd_before = {p.name for p in cwd.iterdir()}
        run = self.post()
        self.assertEqual(0, run.code, run.err)
        body, doc = self.posted()
        self.assertIn(REASON, body)
        self.assertEqual(REASON, doc["new_parts"][0]["reasoning"])
        self.assertNotIn(REASON, run.out)
        self.assertNotIn(REASON, run.err)
        after = {p: p.read_bytes() for p in Path(self.tmp.name).rglob("*") if p.is_file()}
        self.assertEqual(before, after, "the run wrote a file")
        self.assertEqual(cwd_before, {p.name for p in cwd.iterdir()})
        for data in after.values():
            self.assertNotIn(REASON.encode(), data)

    def test_dry_run_prints_no_reasoning_either(self):
        run = self.post("--dry-run")
        self.assertNotIn(REASON, run.out + run.err)


class NotCountedTest(WeeklyCase):

    def records(self, *rows):
        """Two counted repos whose records carry `rows` — `(repo, at, counted)`."""
        records = fixture()
        for slug in ("agent-bureau", "portico"):
            records[slug][1]["rejections"] = [
                rejection(at, was_counted, None if was_counted else f"why in {slug}")
                for repo, at, was_counted in rows if repo == slug]
        self.plant(records)

    def test_one_at_in_two_records_is_one_entry(self):
        self.records(("agent-bureau", "2026-11-09T20:00:00Z", False),
                     ("portico", "2026-11-09T20:00:00Z", False))
        self.post()
        _, doc = self.posted()
        self.assertEqual(["2026-11-09 12:00 PT"], [e["at"] for e in doc["not_counted"]])

    def test_an_at_either_repo_counted_gives_none(self):
        for counting in ("agent-bureau", "portico"):
            with self.subTest(counting=counting):
                self.linear.posted.clear()
                other_repo = "portico" if counting == "agent-bureau" else "agent-bureau"
                self.records((counting, "2026-11-09T20:00:00Z", True),
                             (other_repo, "2026-11-09T20:00:00Z", False))
                self.post()
                _, doc = self.posted()
                self.assertEqual([], doc["not_counted"])

    def test_an_at_before_the_week_gives_none(self):
        self.records(("agent-bureau", "2026-11-08T07:59:00Z", False),
                     ("portico", "2026-11-15T08:00:00Z", False))
        self.post()
        _, doc = self.posted()
        self.assertEqual([], doc["not_counted"])

    def test_the_why_is_in_the_words(self):
        self.records(("portico", "2026-11-09T20:00:00Z", False))
        self.post()
        body, doc = self.posted()
        self.assertEqual([{"at": "2026-11-09 12:00 PT", "why": "why in portico"}],
                         doc["not_counted"])
        self.assertIn("why in portico", body.split("```json")[0])


class ReposTest(WeeklyCase):

    def test_no_count_a_week_ago_gives_a_null_change_with_the_reason(self):
        self.post()
        body, doc = self.posted()
        portico = next(r for r in doc["repos"] if r["repo"] == "portico")
        self.assertEqual({"repo": "portico", "state": "counted", "carded": False,
                          "copies": 2, "last_week": None, "change": None,
                          "reason": "no commit on the branch is a week old"}, portico)

    def test_a_repo_with_listed_false_is_not_carded_yet(self):
        self.post()
        body, _ = self.posted()
        words = body.split("```json")[0]
        line = next(l for l in words.splitlines() if "portico" in l and "cop" in l)
        self.assertIn("not carded yet", line)
        bureau = next(l for l in words.splitlines()
                      if l.startswith("- agent-bureau:") and "cop" in l)
        self.assertNotIn("not carded yet", bureau)

    def test_a_record_that_is_not_json_is_unknown_and_posts_nothing(self):
        (self.dir / "portico" / "count.json").write_text("{not json", encoding="utf-8")
        run = self.post()
        self.assertEqual(1, run.code)
        self.assertEqual([], self.linear.posted)
        self.assertTrue(any(line.startswith("parts-weekly: UNKNOWN — ")
                            for line in run.err_lines), run.err)


class PacificTest(WeeklyCase):

    def test_a_week_across_the_change_to_standard_time(self):
        """Sunday 2026-11-01 01:59 PDT turns 01:00 PST. The week 2026-11-01..07
        holds a part approved at 01:30 PDT on its first day and one at 23:30 PST
        on its last; one approved at 23:30 PDT on 2026-10-31 is the week before."""
        records = fixture()
        records["agent-bureau"][1]["parts"] = [
            part("First", "ui/atoms/First.tsx", 4701, "2026-11-01T08:30:00Z"),
            part("Last", "ui/atoms/Last.tsx", 4702, "2026-11-08T07:30:00Z"),
            part("Before", "ui/atoms/Before.tsx", 4703, "2026-11-01T06:30:00Z"),
        ]
        records["agent-bureau"][1]["rejections"] = []
        records["portico"][1]["rejections"] = [
            rejection("2026-11-01T06:59:00Z", False, "too early"),
            rejection("2026-11-01T09:30:00Z", False, "inside"),
        ]
        self.plant(records)
        for number, name in ((4701, "First"), (4702, "Last")):
            self.gh.add("agent-bureau", number, "2026-11-08T08:00:00Z",
                        [verdict([(name, f"ui/atoms/{name}.tsx")])])
        self.post(week_ending="2026-11-07", now=datetime(2026, 11, 8, 17, 0, tzinfo=UTC))
        body, doc = self.posted()
        self.assertEqual("🪴 parts-weekly: 2026-11-01..2026-11-07", body.splitlines()[0])
        self.assertEqual("2026-11-08 09:00 PT", doc["posted_at"])
        new = {p["name"]: p for p in doc["new_parts"]}
        self.assertEqual(["First", "Last"], sorted(new))
        self.assertEqual("2026-11-01 01:30 PT", new["First"]["approved_at"])
        self.assertEqual("2026-11-07 23:30 PT", new["Last"]["approved_at"])
        self.assertEqual("2026-11-15 00:30 PT", new["First"]["reject_by"])
        self.assertEqual("2026-11-15", new["First"]["permanent_on"])
        self.assertEqual("2026-11-21 23:30 PT", new["Last"]["reject_by"])
        self.assertEqual("2026-11-21", new["Last"]["permanent_on"])
        self.assertEqual([{"at": "2026-11-01 01:30 PT", "why": "inside"}],
                         doc["not_counted"])
        self.assertNotIn("UTC", body)
        for stamp in re.findall(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} \w+", body):
            self.assertTrue(stamp.endswith(" PT"), stamp)


class RefusalTest(WeeklyCase):

    def assert_roster_refused(self, run):
        self.assertEqual(1, run.code, run.err)
        self.assertEqual([], self.linear.posted)
        self.assertEqual([], self.linear.reads)
        self.assertEqual([], self.gh.reads)
        self.assertIn(f"parts-weekly: not posted {START}..{END} — the roster named no repos",
                      run.err_lines)

    def test_an_empty_roster_is_refused(self):
        self.assert_roster_refused(self.post(repos="[]"))

    def test_a_roster_that_is_not_a_json_list_is_refused(self):
        self.assert_roster_refused(self.post(repos="agent-bureau,portico"))

    def test_a_roster_of_things_that_are_not_slugs_is_refused(self):
        for repos in ('{"agent-bureau": 1}', '["../etc"]', '[3]', '"agent-bureau"'):
            with self.subTest(repos=repos):
                self.assert_roster_refused(self.post(repos=repos))

    def test_no_card_posts_nothing_and_exits_zero(self):
        run = self.post(card="")
        self.assertEqual(0, run.code, run.err)
        self.assertEqual([], self.linear.posted)
        self.assertIn(f"parts-weekly: not posted {START}..{END} — no card set",
                      run.err_lines)

    def test_the_comment_cap_is_a_failed_run(self):
        self.linear.cap = True
        run = self.post()
        self.assertEqual(1, run.code, run.err)
        self.assertIn(f"parts-weekly: not posted {START}..{END} — {CARD} is at Linear's "
                      f"comment limit", run.err_lines)
        self.assertFalse([line for line in run.err_lines
                          if line.startswith("parts-weekly: posted")])

    def test_dry_run_posts_nothing_and_says_what_it_would_post(self):
        run = self.post("--dry-run")
        self.assertEqual(0, run.code, run.err)
        self.assertEqual([], self.linear.posted)
        self.assertIn(f"parts-weekly: would post {START}..{END} on {CARD} — 1 new part(s)",
                      run.err_lines)

    def test_a_week_ending_that_is_not_a_saturday_is_unknown(self):
        run = self.post(week_ending="2026-11-13")
        self.assertEqual(1, run.code)
        self.assertEqual([], self.linear.posted)
        self.assertTrue(any(line.startswith("parts-weekly: UNKNOWN — ")
                            for line in run.err_lines), run.err)

    def test_a_card_that_is_not_a_card_id_is_unknown(self):
        run = self.post(card="standing parts card")
        self.assertEqual(1, run.code)
        self.assertEqual([], self.linear.posted)


class DocTest(unittest.TestCase):

    def test_the_doc_says_what_the_card_asks(self):
        text = (ROOT / "docs" / "parts-weekly.md").read_text(encoding="utf-8")
        for words in ("count.json", "provisional.json", "final_approval",
                      "never stored", "Sunday", "Saturday", "America/Los_Angeles",
                      "🪴 parts-weekly: <week_start>..<week_end>", '"parts-weekly/1"',
                      "new_parts", "became_permanent", "rejected", "not_counted",
                      "repos", "reject_with", "reject_by", "closest", "carded",
                      "already posted", "the roster named no repos",
                      "parts-weekly: posted", "parts-weekly: would post",
                      "parts-weekly: UNKNOWN", "comment limit", "no card set",
                      "could not be read", "its leg left no record"):
            self.assertIn(words, text)


if __name__ == "__main__":
    unittest.main()
