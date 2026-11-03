"""A provisional part becomes permanent after 14 days with no rejection, and a
rejected part gets one replacement card (DRE-8941).

`scripts/parts_provisional.py scan` reads, for one repo, the provisional
approvals the design critic recorded on merged pull requests (the `🪴
part-provisional:` lines of the verdict the merge gate merged on), decides
where each part stands on `--now`, writes the outcome as one new comment on
the part's own pull request, and on a CEO rejection files one Intake card.

The stand-ins here are GitHub as `gh_read_retry.read` and the comment POST
reach it, Linear's create seam and its read (`linear_ops.create_card`,
`linear_ops.gql`), and the standing card's thread as `spoken_thread.read`
returns it. Every part's reasoning is `ZZ-REASON-1`, which must never leave
the pull request.

Run: cd bureau-pipeline && python3 -m pytest tests/test_parts_provisional.py -v
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import gh_read_retry  # noqa: E402
import linear_ops  # noqa: E402
import notice_writers  # noqa: E402
import parts_provisional  # noqa: E402
import spoken_thread  # noqa: E402

SLUG = "agent-bureau"
FULL = "dreadnought-foundry/agent-bureau"
QA = "agent-bureau-qa-bot[bot]"
WRITER = "agent-bureau-bot[bot]"
CARD = "DRE-9000"
AGENT = "agent:frontend"
INITIATIVE = "initiative:agent-bureau"
REASON = "ZZ-REASON-1"
SHA = "a" * 40

NOW = datetime(2027, 3, 1, 20, 0, tzinfo=UTC)
BADGE = "ui/atoms/Badge.tsx"
BADGE_TWO = "ui/molecules/Badge.tsx"
PILL = "ui/atoms/Pill.tsx"
TABLE = "ui/atoms/Table.tsx"

DECLARATION = ('{"parts": {"atoms": ["ui/atoms"], "molecules": ["ui/molecules"], '
               '"organisms": [], "templates": []}}\n')
SOURCES = {
    BADGE: ("interface BadgeProps { tone: string; label: string }\n"
            "export function Badge({ tone, label }: BadgeProps) { return null }\n"),
    PILL: ("interface PillProps { tone: string; size: string; label: string }\n"
           "export function Pill({ tone, size, label }: PillProps) { return null }\n"),
    TABLE: ("interface TableProps { rows: string[] }\n"
            "export function Table({ rows }: TableProps) { return null }\n"),
}


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(days: float, hours: float = 0) -> datetime:
    return NOW - timedelta(days=days, hours=hours)


def pacific(moment: datetime) -> str:
    from zoneinfo import ZoneInfo

    return moment.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


def verdict(at: datetime, parts=(("Badge", BADGE),), token="APPROVE", login=QA,
            reasoning=REASON) -> dict:
    lines = [f"🔎 QA Critic — VERDICT: {token} @{SHA}", "", "The change is sound."]
    lines += [f"🪴 part-provisional: `{name}` at `{path}` — reasoning: {reasoning}"
              for name, path in parts]
    return {"user": {"login": login}, "body": "\n".join(lines), "created_at": iso(at)}


def record(kind: str, name: str, path: str, day: str, at: datetime,
           login=WRITER) -> dict:
    mark = "🪴 part-permanent:" if kind == "permanent" else "🪴 part-rejected:"
    return {"user": {"login": login}, "body": f"{mark} {name} at {path} — {day}",
            "created_at": iso(at)}


def ceo(words: str, signed: datetime, card=CARD) -> spoken_thread.Voice:
    body = (f"Answer from Sid (signed in to the console), {pacific(signed)} 09:00 PT:\n"
            f"{words}\n"
            f"🔏 console-answer: v1 card={card} sha256={'b' * 64} user=sid "
            f"at={iso(signed)} kid={'c' * 16} sig={'d' * 86}")
    return spoken_thread.Voice(spoken_thread.CEO_VIA_CONSOLE, "the CEO", iso(signed),
                               body)


def voice(kind: str, body, at: datetime, why=None) -> spoken_thread.Voice:
    return spoken_thread.Voice(kind, kind, iso(at), body, why)


class GitHub:
    """GitHub, as far as the scan reaches it: the closed pull requests, one
    pull request, its issue comments, and a comment POST."""

    def __init__(self):
        self.pulls = {}
        self.comment_reads = []
        self.posted = []
        self.refuse = False

    def add(self, number: int, merged: datetime | None, comments=()):
        self.pulls[number] = {
            "number": number,
            "merged_at": iso(merged) if merged else None,
            "updated_at": iso(merged or NOW - timedelta(days=1)),
            "html_url": f"https://github.com/{FULL}/pull/{number}",
            "comments": list(comments),
        }
        return self.pulls[number]

    def _public(self, pull: dict) -> dict:
        return {k: v for k, v in pull.items() if k != "comments"}

    def read(self, args, **_):
        args = list(args)
        assert args[0] == "api", args
        path = args[-1]
        listing = re.fullmatch(rf"repos/{FULL}/pulls\?(.+)", path)
        if listing:
            query = dict(part.split("=", 1) for part in listing[1].split("&"))
            assert query["state"] == "closed", query
            assert (query["sort"], query["direction"]) == ("updated", "desc"), query
            size, page = int(query["per_page"]), int(query["page"])
            rows = sorted(self.pulls.values(), key=lambda p: p["updated_at"], reverse=True)
            return json.dumps([self._public(p) for p in
                               rows[(page - 1) * size:page * size]])
        one = re.fullmatch(rf"repos/{FULL}/pulls/(\d+)", path)
        if one:
            return json.dumps(self._public(self.pulls[int(one[1])]))
        thread = re.fullmatch(rf"repos/{FULL}/issues/(\d+)/comments\?per_page=100", path)
        if thread:
            assert "--paginate" in args and "--slurp" in args, args
            self.comment_reads.append(int(thread[1]))
            return json.dumps([self.pulls[int(thread[1])]["comments"]])
        raise AssertionError(f"an unexpected read: {args}")

    def run(self, argv, **kwargs):
        argv = list(argv)
        assert argv[:4] == ["gh", "api", "-X", "POST"], argv
        number = int(re.fullmatch(rf"repos/{FULL}/issues/(\d+)/comments", argv[4])[1])
        body = json.loads(kwargs["input"])["body"]
        if self.refuse:
            return subprocess.CompletedProcess(
                argv, 1, "", "gh: Resource not accessible by integration (HTTP 403)")
        self.posted.append((number, body))
        self.pulls[number]["comments"].append(
            {"user": {"login": WRITER}, "body": body, "created_at": iso(NOW)})
        return subprocess.CompletedProcess(argv, 0, "{}", "")


class Board:
    """Linear's create seam and its read, by the query's own filter."""

    def __init__(self):
        self.cards = []
        self.created = []
        self.fail = False

    def add(self, title, description, *, state="Backlog", state_type="backlog",
            labels=(), lane="Intake"):
        number = 9100 + len(self.cards)
        card = {"identifier": f"DRE-{number}", "title": title, "description": description,
                "labels": list(labels), "lane": lane, "state": state,
                "state_type": state_type,
                "createdAt": f"2027-02-01T00:{len(self.cards):02d}:00Z",
                "url": f"https://linear.app/dreadnoughtfoundry/issue/DRE-{number}/x"}
        self.cards.append(card)
        return card

    def create_card(self, title, description, *, repo_slug, labels=(), lane="Planning"):
        names = list(dict.fromkeys([f"repo:{repo_slug}", *labels]))
        card = self.add(title, description, labels=names, lane=lane, state=lane,
                        state_type="triage")
        self.created.append(card)
        return {"id": f"id-{card['identifier']}", "identifier": card["identifier"],
                "url": card["url"]}

    def gql(self, query, variables=None, **_):
        if self.fail:
            raise linear_ops.LinearError("Linear answered 500")
        variables = dict(variables or {})
        if "issueCreate" in query or "Update" in query:
            raise AssertionError(f"a write outside the create seam: {query}")
        needle = next(v for k, v in variables.items() if k != "after" and isinstance(v, str))
        open_only = bool(re.search(r"state\s*:", query))
        nodes = [{"identifier": c["identifier"], "createdAt": c["createdAt"],
                  "description": c["description"]}
                 for c in self.cards
                 if needle.lower() in c["description"].lower()
                 and not (open_only and c["state_type"] in ("completed", "canceled"))]
        return {"issues": {"nodes": nodes,
                           "pageInfo": {"hasNextPage": False, "endCursor": None}}}


class Run:
    def __init__(self, code, out, err, doc, raw):
        self.code, self.out, self.err, self.doc, self.raw = code, out, err, doc, raw
        self.out_lines = out.splitlines()
        self.err_lines = err.splitlines()

    def part(self, name, path=None):
        found = [p for p in self.doc["parts"]
                 if p["name"] == name and (path is None or p["path"] == path)]
        assert len(found) == 1, self.doc
        return found[0]


class ScanCase(unittest.TestCase):

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        self.write(".github/bureau/design-system.json", DECLARATION)
        for path, text in SOURCES.items():
            self.write(path, text)
        self.gh = GitHub()
        self.board = Board()
        self.voices = [voice(spoken_thread.PIPELINE, "⏳ the standing parts card",
                             ago(90))]
        self.json_path = Path(self.tmp.name) / "out.json"

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def thread(self, card):
        assert card == CARD, card
        return list(self.voices)

    def scan(self, *extra, card=CARD, now=NOW):
        if self.json_path.exists():
            self.json_path.unlink()
        argv = ["scan", "--repo", SLUG, "--full-name", FULL, "--repo-root", str(self.root),
                "--qa-login", QA, "--writer-login", WRITER, "--card", card,
                "--agent", AGENT, "--initiative", INITIATIVE,
                "--json-out", str(self.json_path), "--now", iso(now), *extra]
        out, err = io.StringIO(), io.StringIO()
        with patch.object(gh_read_retry, "read", self.gh.read), \
                patch.object(subprocess, "run", self.gh.run), \
                patch.object(spoken_thread, "read", self.thread), \
                patch.object(linear_ops, "create_card", self.board.create_card), \
                patch.object(linear_ops, "gql", self.board.gql), \
                redirect_stdout(out), redirect_stderr(err):
            code = parts_provisional.main(argv)
        raw = self.json_path.read_text(encoding="utf-8") if self.json_path.exists() else ""
        return Run(code, out.getvalue(), err.getvalue(), json.loads(raw) if raw else None,
                   raw)

    def approve(self, number, days, parts=(("Badge", BADGE),), extra=()):
        """A pull request merged an hour after an APPROVE `days` before NOW."""
        approved = ago(days)
        return self.gh.add(number, approved + timedelta(hours=1),
                           [verdict(approved, parts), *extra])

    def assert_unknown(self, run, *words):
        self.assertEqual(1, run.code, run.err)
        unknown = [line for line in run.err_lines
                   if line.startswith(f"parts-provisional: {SLUG} — UNKNOWN — ")]
        self.assertEqual(1, len(unknown), run.err)
        for word in words:
            self.assertIn(word, unknown[0])
        self.assertEqual([], self.gh.posted)
        self.assertEqual([], self.board.created)


class ClockTest(ScanCase):

    def test_thirteen_days_is_provisional_and_writes_nothing(self):
        self.approve(4601, 13)
        run = self.scan()
        self.assertEqual(0, run.code, run.err)
        badge = run.part("Badge")
        self.assertEqual("provisional", badge["status"])
        self.assertIsNone(badge["permanent_on"])
        self.assertEqual([], self.gh.posted)
        self.assertIn(f"parts-provisional: {SLUG} Badge — provisional, 13 day(s) of 14",
                      run.out_lines)

    def test_fourteen_days_is_permanent_with_one_record_comment(self):
        approved = ago(14)
        self.approve(4601, 14)
        run = self.scan()
        self.assertEqual(0, run.code, run.err)
        badge = run.part("Badge")
        day = pacific(approved + timedelta(days=14))
        self.assertEqual("permanent", badge["status"])
        self.assertEqual(day, badge["permanent_on"])
        self.assertEqual([(4601, f"🪴 part-permanent: Badge at {BADGE} — {day}")],
                         self.gh.posted)
        self.assertIn(f"parts-provisional: {SLUG} Badge — permanent, recorded on PR #4601",
                      run.out_lines)
        self.assertEqual(iso(approved), badge["approved_at"])
        self.assertEqual(4601, badge["pr"])
        self.assertEqual(f"https://github.com/{FULL}/pull/4601", badge["pr_url"])

    def test_the_marks(self):
        self.assertEqual("🪴 part-permanent:", parts_provisional.PERMANENT_MARK)
        self.assertEqual("🪴 part-rejected:", parts_provisional.REJECTED_MARK)
        self.assertEqual("parts-rejected:", parts_provisional.IDENTITY_MARK)
        self.assertEqual("parts-provisional:", parts_provisional.TAG)
        self.assertEqual(14, parts_provisional.WINDOW_DAYS)
        self.assertEqual(35, parts_provisional.LOOKBACK_DAYS)

    def test_provisional_since_is_the_pacific_day_the_line_first_reached_main(self):
        # 2ee57fed (DRE-8719), committed 2026-11-02T05:27:03Z: 21:27 PST on the 1st.
        self.assertEqual("2026-11-01", parts_provisional.PROVISIONAL_SINCE)

    def test_a_pull_request_that_never_merged_approved_nothing(self):
        approved = ago(20)
        self.gh.add(4601, None, [verdict(approved)])
        run = self.scan()
        self.assertEqual([], run.doc["parts"])
        self.assertEqual([], self.gh.posted)

    def test_the_summary_line(self):
        self.approve(4601, 13)
        self.approve(4602, 20, parts=(("Chip", "ui/atoms/Chip.tsx"),))
        run = self.scan()
        self.assertEqual(f"parts-provisional: {SLUG} — 2 approval(s) since "
                         f"{parts_provisional.PROVISIONAL_SINCE}: 1 provisional, "
                         f"1 permanent, 0 rejected", run.err_lines[-1])

    def test_parts_are_sorted_by_approval_then_name(self):
        self.approve(4601, 13, parts=(("Zed", "ui/atoms/Zed.tsx"),
                                      ("Alpha", "ui/atoms/Alpha.tsx")))
        self.approve(4602, 20, parts=(("Mid", "ui/atoms/Mid.tsx"),))
        run = self.scan()
        self.assertEqual(["Mid", "Alpha", "Zed"], [p["name"] for p in run.doc["parts"]])

    def test_dry_run_writes_nothing_and_says_would(self):
        self.approve(4601, 14)
        self.approve(4602, 10, parts=(("Chip", "ui/atoms/Chip.tsx"),))
        self.voices.append(ceo("reject agent-bureau/Chip", ago(8)))
        run = self.scan("--dry-run")
        self.assertEqual(0, run.code, run.err)
        self.assertEqual([], self.gh.posted)
        self.assertEqual([], self.board.created)
        self.assertIn(f"parts-provisional: {SLUG} Badge — permanent, would record on PR #4601",
                      run.out_lines)
        self.assertTrue(any(line.startswith(f"parts-provisional: {SLUG} Chip — rejected, ")
                            and "would file" in line for line in run.out_lines), run.out)


class RejectionTest(ScanCase):

    def test_a_console_rejection_inside_the_window_files_one_card_and_one_record(self):
        approved = ago(10)
        signed = approved + timedelta(days=5)
        self.approve(4601, 10)
        self.voices.append(ceo("Reject agent-bureau/Badge — Pill already covers it",
                               signed))
        run = self.scan()
        self.assertEqual(0, run.code, run.err)
        badge = run.part("Badge")
        self.assertEqual("rejected", badge["status"])
        self.assertEqual(pacific(signed), badge["rejected_on"])
        self.assertEqual(1, len(self.board.created))
        card = self.board.created[0]
        self.assertEqual("Intake", card["lane"])
        self.assertEqual(f"{SLUG}: remove the rejected part Badge and replace its uses",
                         card["title"])
        self.assertIn(f"parts-rejected: {SLUG} | Badge | {BADGE}",
                      card["description"].splitlines())
        self.assertEqual({f"repo:{SLUG}", AGENT, INITIATIVE}, set(card["labels"]))
        self.assertEqual(card["identifier"], badge["replacement_card"])
        self.assertEqual([(4601, f"🪴 part-rejected: Badge at {BADGE} — {pacific(signed)}")],
                         self.gh.posted)
        self.assertIn(f"parts-provisional: {SLUG} Badge — rejected, replacement card "
                      f"{card['identifier']} filed", run.out_lines)
        self.assertEqual([{"at": iso(signed), "counted": True, "part": f"{SLUG}/Badge",
                           "why": None}], run.doc["rejections"])
        self.assertIn(f"parts-provisional: {SLUG} — rejections read on {CARD}: 1 counted, "
                      f"0 not counted", run.err_lines)

    def test_the_replacement_card_says_what_where_and_the_fix(self):
        self.approve(4601, 10)
        self.voices.append(ceo("reject agent-bureau/Badge", ago(5)))
        self.scan()
        body = self.board.created[0]["description"]
        self.assertIn("Badge", body)
        self.assertIn(BADGE, body)
        self.assertIn("PT", body)
        self.assertIn("#4601", body)
        self.assertIn("delete", body.lower())
        self.assertIn("existing shared part", body)
        self.assertIn("record", body)
        self.assertNotIn(REASON, body)

    def test_written_in_linear_is_not_counted(self):
        self.approve(4601, 10)
        self.voices.append(voice(spoken_thread.PERSON, "reject agent-bureau/Badge", ago(5)))
        run = self.scan()
        self.assertEqual("provisional", run.part("Badge")["status"])
        [entry] = run.doc["rejections"]
        self.assertFalse(entry["counted"])
        self.assertEqual(f"{SLUG}/Badge", entry["part"])
        self.assertIn("Linear", entry["why"])
        self.assertIn("console", entry["why"])
        self.assertEqual([], self.board.created)

    def test_a_name_without_the_slug_is_not_counted(self):
        self.approve(4601, 10)
        self.voices.append(ceo("reject Badge", ago(5)))
        run = self.scan()
        self.assertEqual("provisional", run.part("Badge")["status"])
        [entry] = run.doc["rejections"]
        self.assertFalse(entry["counted"])
        self.assertIsNone(entry["part"])
        self.assertIn("no provisional part", entry["why"])

    def test_another_repos_part_is_not_counted_here(self):
        self.approve(4601, 10)
        self.voices.append(ceo("reject portico/Badge", ago(5)))
        run = self.scan()
        [entry] = run.doc["rejections"]
        self.assertFalse(entry["counted"])
        self.assertEqual("portico/Badge", entry["part"])
        self.assertIn("no provisional part", entry["why"])

    def test_signed_after_the_window_is_not_counted(self):
        approved = ago(20)
        self.approve(4601, 20)
        self.voices.append(ceo("reject agent-bureau/Badge", approved + timedelta(days=15)))
        run = self.scan()
        self.assertEqual("permanent", run.part("Badge")["status"])
        [entry] = run.doc["rejections"]
        self.assertFalse(entry["counted"])
        self.assertIn("after", entry["why"])
        self.assertEqual([], self.board.created)

    def test_a_shared_name_needs_the_path(self):
        self.approve(4601, 10)
        self.approve(4602, 9, parts=(("Badge", BADGE_TWO),))
        self.voices.append(ceo("reject agent-bureau/Badge", ago(5)))
        run = self.scan()
        [entry] = run.doc["rejections"]
        self.assertFalse(entry["counted"])
        self.assertIn("path", entry["why"])
        self.assertEqual({"provisional"}, {p["status"] for p in run.doc["parts"]})

    def test_a_shared_name_with_the_path_counts(self):
        self.approve(4601, 10)
        self.approve(4602, 9, parts=(("Badge", BADGE_TWO),))
        self.voices.append(ceo(f"reject agent-bureau/Badge at {BADGE_TWO}", ago(5)))
        run = self.scan()
        self.assertEqual("provisional", run.part("Badge", BADGE)["status"])
        self.assertEqual("rejected", run.part("Badge", BADGE_TWO)["status"])
        self.assertEqual(1, len(self.board.created))

    def test_a_refused_message_is_listed_with_no_part(self):
        self.approve(4601, 10)
        self.voices.append(voice(spoken_thread.REFUSED, None, ago(5),
                                 "the signature does not verify"))
        run = self.scan()
        self.assertEqual("provisional", run.part("Badge")["status"])
        [entry] = run.doc["rejections"]
        self.assertFalse(entry["counted"])
        self.assertIsNone(entry["part"])
        self.assertIn("refused", entry["why"])

    def test_an_unchecked_message_is_unknown_even_for_a_part_twenty_days_old(self):
        self.approve(4601, 20)
        self.voices.append(voice(spoken_thread.UNCHECKED, None, ago(5),
                                 "the console's key could not be read"))
        run = self.scan()
        self.assert_unknown(run)
        self.assertIsNone(run.doc)

    def test_a_console_answer_that_is_not_a_rejection_is_not_listed(self):
        self.approve(4601, 10)
        self.voices.append(ceo("keep going with agent-bureau/Badge", ago(5)))
        run = self.scan()
        self.assertEqual([], run.doc["rejections"])

    def test_silence_keeps_the_part(self):
        self.approve(4601, 30)
        run = self.scan()
        self.assertEqual("permanent", run.part("Badge")["status"])
        self.assertEqual([], run.doc["rejections"])


class AgainTest(ScanCase):

    def test_a_second_scan_writes_nothing_and_says_already_recorded(self):
        self.approve(4601, 20)
        self.approve(4602, 10, parts=(("Chip", "ui/atoms/Chip.tsx"),))
        self.voices.append(ceo("reject agent-bureau/Chip", ago(6)))
        first = self.scan()
        self.assertEqual(0, first.code, first.err)
        self.assertEqual(2, len(self.gh.posted))
        self.assertEqual(1, len(self.board.created))
        second = self.scan()
        self.assertEqual(0, second.code, second.err)
        self.assertEqual(2, len(self.gh.posted))
        self.assertEqual(1, len(self.board.created))
        self.assertIn(f"parts-provisional: {SLUG} Badge — permanent, already recorded",
                      second.out_lines)
        self.assertIn(f"parts-provisional: {SLUG} Chip — rejected, already recorded",
                      second.out_lines)
        self.assertFalse(second.part("Badge")["recorded_now"])
        self.assertEqual(first.part("Badge")["permanent_on"],
                         second.part("Badge")["permanent_on"])
        self.assertEqual(self.board.created[0]["identifier"],
                         second.part("Chip")["replacement_card"])

    def test_a_replacement_card_in_a_closed_state_counts_as_filed(self):
        self.approve(4601, 10)
        signed = ago(6)
        self.voices.append(ceo("reject agent-bureau/Badge", signed))
        old = self.board.add("agent-bureau: remove the rejected part Badge and replace its uses",
                             f"Done long ago.\n\nparts-rejected: {SLUG} | Badge | {BADGE}\n",
                             state="Canceled", state_type="canceled")
        run = self.scan()
        self.assertEqual(0, run.code, run.err)
        self.assertEqual([], self.board.created)
        self.assertEqual(old["identifier"], run.part("Badge")["replacement_card"])
        self.assertEqual([(4601, f"🪴 part-rejected: Badge at {BADGE} — {pacific(signed)}")],
                         self.gh.posted)

    def test_a_record_by_another_login_is_not_a_record(self):
        approved = ago(20)
        self.approve(4601, 20, extra=[record("permanent", "Badge", BADGE, "2027-01-01",
                                             approved + timedelta(days=15),
                                             login="somebody")])
        run = self.scan()
        self.assertTrue(run.part("Badge")["recorded_now"])
        self.assertEqual(1, len(self.gh.posted))

    def test_a_rejected_record_keeps_its_status(self):
        approved = ago(30)
        self.approve(4601, 30, extra=[record("rejected", "Badge", BADGE, "2027-02-05",
                                             approved + timedelta(days=16))])
        run = self.scan()
        badge = run.part("Badge")
        self.assertEqual("rejected", badge["status"])
        self.assertEqual("2027-02-05", badge["rejected_on"])
        self.assertEqual([], self.gh.posted)


class VerdictTest(ScanCase):

    def test_an_earlier_and_the_final_approve_both_listing_badge_is_one_part(self):
        first, final = ago(16), ago(12)
        self.gh.add(4601, final + timedelta(hours=1), [verdict(first), verdict(final)])
        run = self.scan()
        self.assertEqual(1, len(run.doc["parts"]))
        self.assertEqual(iso(final), run.part("Badge")["approved_at"])
        self.assertEqual("provisional", run.part("Badge")["status"])

    def test_a_final_verdict_that_is_not_an_approve_approves_nothing(self):
        first, final = ago(20), ago(19)
        self.gh.add(4601, final + timedelta(hours=1),
                    [verdict(first), verdict(final, parts=(), token="REQUEST_CHANGES")])
        run = self.scan()
        self.assertEqual([], run.doc["parts"])
        self.assertIn(f"parts-provisional: {SLUG} — PR #4601 merged on a verdict that is "
                      f"not an APPROVE, no part approved", run.err_lines)
        self.assertEqual([], self.gh.posted)

    def test_a_final_approve_that_drops_a_part_does_not_approve_it(self):
        first, final = ago(20), ago(19)
        self.gh.add(4601, final + timedelta(hours=1),
                    [verdict(first, parts=(("Badge", BADGE), ("Chip", "ui/atoms/Chip.tsx"))),
                     verdict(final, parts=(("Chip", "ui/atoms/Chip.tsx"),))])
        run = self.scan()
        self.assertEqual(["Chip"], [p["name"] for p in run.doc["parts"]])

    def test_two_identical_lines_are_one_part(self):
        self.approve(4601, 13, parts=(("Badge", BADGE), ("Badge", BADGE)))
        run = self.scan()
        self.assertEqual(1, len(run.doc["parts"]))

    def test_a_verdict_after_the_merge_is_ignored(self):
        approved = ago(13)
        merged = approved + timedelta(hours=1)
        self.gh.add(4601, merged, [verdict(approved),
                                   verdict(merged + timedelta(hours=1),
                                           parts=(("Chip", "ui/atoms/Chip.tsx"),))])
        run = self.scan()
        self.assertEqual(["Badge"], [p["name"] for p in run.doc["parts"]])

    def test_a_verdict_by_another_login_is_never_a_verdict(self):
        approved = ago(13)
        merged = approved + timedelta(hours=2)
        self.gh.add(4601, merged, [verdict(approved),
                                   verdict(approved + timedelta(hours=1),
                                           parts=(("Chip", "ui/atoms/Chip.tsx"),),
                                           login="somebody")])
        run = self.scan()
        self.assertEqual(["Badge"], [p["name"] for p in run.doc["parts"]])

    def test_two_pull_requests_approving_the_same_part_count_the_earlier(self):
        self.approve(4601, 13)
        self.approve(4602, 11)
        run = self.scan()
        self.assertEqual(1, len(run.doc["parts"]))
        self.assertEqual(4601, run.part("Badge")["pr"])
        self.assertIn(f"parts-provisional: {SLUG} Badge — duplicate of PR #4601, not counted",
                      run.out_lines + run.err_lines)

    def test_final_approval_returns_the_records_the_scan_used(self):
        first, final = ago(16), ago(12)
        self.gh.add(4601, final + timedelta(hours=1),
                    [verdict(first, parts=(("Chip", "ui/atoms/Chip.tsx"),)),
                     verdict(final, parts=(("Badge", BADGE), ("Badge", BADGE)))])
        run = self.scan()
        with patch.object(gh_read_retry, "read", self.gh.read):
            found = parts_provisional.final_approval(FULL, 4601, QA)
        self.assertEqual([("Badge", BADGE, REASON)], [tuple(r) for r in found])
        self.assertEqual([(p["name"], p["path"]) for p in run.doc["parts"]],
                         [(name, path) for name, path, _ in found])

    def test_final_approval_is_empty_for_a_pull_request_that_never_merged(self):
        self.gh.add(4601, None, [verdict(ago(5))])
        with patch.object(gh_read_retry, "read", self.gh.read):
            self.assertEqual([], parts_provisional.final_approval(FULL, 4601, QA))


class SinceTest(ScanCase):

    def test_a_weekly_post_seven_days_ago_reads_from_thirty_five_days_before_it(self):
        posted = ago(7)
        self.voices.append(voice(spoken_thread.PIPELINE,
                                 "🪴 parts-weekly: 3 part(s) this week", posted))
        self.approve(4601, 45)
        self.approve(4602, 40, parts=(("Chip", "ui/atoms/Chip.tsx"),))
        run = self.scan()
        since = pacific(posted - timedelta(days=35))
        self.assertEqual(since, run.doc["since"])
        self.assertEqual(["Chip"], [p["name"] for p in run.doc["parts"]])
        self.assertNotIn(4601, self.gh.comment_reads)

    def test_a_weekly_line_from_a_person_does_not_move_since(self):
        self.voices.append(voice(spoken_thread.PERSON, "🪴 parts-weekly: mine", ago(7)))
        run = self.scan()
        self.assertEqual(parts_provisional.PROVISIONAL_SINCE, run.doc["since"])

    def test_no_weekly_post_reads_from_provisional_since_and_records_late(self):
        approved = ago(60)
        self.approve(4601, 60)
        run = self.scan()
        self.assertEqual(parts_provisional.PROVISIONAL_SINCE, run.doc["since"])
        badge = run.part("Badge")
        self.assertEqual("permanent", badge["status"])
        self.assertTrue(badge["recorded_now"])
        self.assertEqual(pacific(approved + timedelta(days=14)), badge["permanent_on"])
        self.assertEqual(1, len(self.gh.posted))

    def test_since_is_never_before_provisional_since(self):
        self.voices.append(voice(spoken_thread.PIPELINE, "🪴 parts-weekly: early",
                                 datetime(2026, 11, 20, tzinfo=UTC)))
        run = self.scan()
        self.assertEqual(parts_provisional.PROVISIONAL_SINCE, run.doc["since"])

    def test_a_full_thread_newer_than_the_earliest_open_window_is_unknown(self):
        self.approve(4601, 20)
        self.voices = [voice(spoken_thread.PIPELINE, f"⏳ {n}", ago(10, hours=-n / 10))
                       for n in range(linear_ops.COMMENT_WINDOW)]
        run = self.scan()
        self.assert_unknown(run)

    def test_a_three_comment_thread_is_not_cut_short(self):
        self.approve(4601, 20)
        self.voices = [voice(spoken_thread.PIPELINE, f"⏳ {n}", ago(10 - n)) for n in range(3)]
        run = self.scan()
        self.assertEqual(0, run.code, run.err)
        self.assertEqual("permanent", run.part("Badge")["status"])

    def test_a_full_thread_older_than_every_open_window_is_whole_enough(self):
        self.approve(4601, 20)
        self.voices = [voice(spoken_thread.PIPELINE, f"⏳ {n}", ago(30, hours=-n))
                       for n in range(linear_ops.COMMENT_WINDOW)]
        run = self.scan()
        self.assertEqual(0, run.code, run.err)


class NoCardTest(ScanCase):

    def test_no_card_keeps_a_twenty_day_part_provisional_and_writes_nothing(self):
        self.approve(4601, 20)
        run = self.scan(card="")
        self.assertEqual(0, run.code, run.err)
        self.assertEqual("provisional", run.part("Badge")["status"])
        self.assertEqual([], self.gh.posted)
        self.assertEqual([], self.board.created)
        self.assertIn(f"parts-provisional: {SLUG} — no card set, so no rejection can be "
                      f"read; nothing is made permanent", run.err_lines)
        self.assertIsNone(run.doc["card"])
        self.assertEqual(pacific(NOW - timedelta(days=35)), run.doc["since"])
        self.assertEqual([], run.doc["rejections"])


class ClosestTest(ScanCase):

    def test_badge_is_closest_to_pill(self):
        self.approve(4601, 5)
        run = self.scan()
        self.assertEqual({"name": "Pill", "path": PILL}, run.part("Badge")["closest"])

    def test_the_only_part_has_no_closest(self):
        (self.root / PILL).unlink()
        (self.root / TABLE).unlink()
        self.approve(4601, 5)
        run = self.scan()
        self.assertIsNone(run.part("Badge")["closest"])

    def test_a_part_whose_file_is_not_in_the_checkout_has_no_closest(self):
        self.approve(4601, 5, parts=(("Gone", "ui/atoms/Gone.tsx"),))
        run = self.scan()
        self.assertIsNone(run.part("Gone")["closest"])

    def test_with_nothing_near_the_closest_is_still_named(self):
        self.write("ui/atoms/Odd.tsx", "export function Odd({ zzz }) { return null }\n")
        self.approve(4601, 5, parts=(("Odd", "ui/atoms/Odd.tsx"),))
        run = self.scan()
        # Every score is 0 and no prop is shared: the name breaks the tie.
        self.assertEqual({"name": "Badge", "path": BADGE}, run.part("Odd")["closest"])


class RefusedWriteTest(ScanCase):

    def test_a_refused_comment_is_unknown_naming_the_pull_request(self):
        self.approve(4601, 14)
        self.gh.refuse = True
        run = self.scan()
        self.assert_unknown(run, "PR #4601")

    def test_a_failing_duplicate_search_files_nothing(self):
        self.approve(4601, 10)
        self.voices.append(ceo("reject agent-bureau/Badge", ago(5)))
        self.board.fail = True
        run = self.scan()
        self.assert_unknown(run)

    def test_an_unreadable_thread_is_unknown(self):
        self.approve(4601, 14)

        def broken(card):
            raise linear_ops.LinearError("Linear answered 500")

        self.thread = broken
        run = self.scan()
        self.assert_unknown(run, CARD)


class NoReasoningTest(ScanCase):

    def _no_reason(self, run):
        for line in run.out_lines + run.err_lines:
            self.assertNotIn(REASON, line)
        self.assertNotIn(REASON, run.raw)
        if run.doc is not None:
            self.assertNotIn('"reasoning"', run.raw)

    def test_no_outcome_writes_the_reasoning(self):
        self.approve(4601, 13)                                            # provisional
        self.approve(4602, 20, parts=(("Chip", "ui/atoms/Chip.tsx"),))    # permanent
        self.approve(4603, 10, parts=(("Card", "ui/atoms/Card.tsx"),))    # rejected
        self.approve(4604, 9)                                             # duplicate
        final = ago(18)
        self.gh.add(4605, final + timedelta(hours=1),                     # not an APPROVE
                    [verdict(ago(19), parts=(("Tag", "ui/atoms/Tag.tsx"),)),
                     verdict(final, parts=(), token="REQUEST_CHANGES")])
        self.voices.append(ceo("reject agent-bureau/Card", ago(4)))
        runs = [self.scan()]
        runs.append(self.scan())                                          # already recorded
        runs.append(self.scan(card=""))                                   # no card set
        self.gh.posted.clear()
        self.board.created.clear()
        self.approve(4606, 15, parts=(("Dot", "ui/atoms/Dot.tsx"),))
        self.gh.refuse = True
        runs.append(self.scan())                                          # UNKNOWN
        joined = "\n".join(r.out + r.err for r in runs)
        for words in ("provisional, 13 day(s) of 14", "permanent, recorded on PR #4602",
                      "rejected, replacement card", "already recorded", "duplicate of PR #",
                      "not an APPROVE", "no card set", "UNKNOWN"):
            self.assertIn(words, joined)
        for run in runs:
            self._no_reason(run)
        posted_cards = "\n".join(c["description"] for c in self.board.cards)
        self.assertNotIn(REASON, posted_cards)


class FilesTest(unittest.TestCase):

    def test_the_notice_writers_row_is_kept_with_the_title(self):
        rows = json.loads(notice_writers.CONFIG.read_text(encoding="utf-8"))
        rows = rows.get("writers", rows) if isinstance(rows, dict) else rows
        mine = [r for r in rows if r.get("file") == "scripts/parts_provisional.py"]
        self.assertEqual(1, len(mine))
        self.assertEqual("kept", mine[0]["disposition"])
        self.assertEqual("<repo>: remove the rejected part <Name> and replace its uses",
                         mine[0]["title"])
        self.assertTrue(mine[0]["reason"].startswith("work:"), mine[0]["reason"])

    def test_the_notice_writers_check_stays_green(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = notice_writers.main(["check"])
        self.assertEqual(0, code, out.getvalue() + err.getvalue())

    def test_the_doc_states_the_rules(self):
        doc = (ROOT / "docs" / "parts-provisional.md").read_text(encoding="utf-8")
        for words in ("🪴 part-permanent:", "🪴 part-rejected:", "parts-rejected:",
                      "LOOKBACK_DAYS", "PROVISIONAL_SINCE", "merged_at", "closest",
                      "refused", "unchecked", "Intake", "--json-out", "reasoning",
                      "public", "duplicate of PR #", "not an APPROVE", "no card set",
                      "already recorded", "UNKNOWN", "final_approval"):
            self.assertIn(words, doc)


if __name__ == "__main__":
    unittest.main()
