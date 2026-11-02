"""The one-time count files one Intake cleanup card per existing copy of a
shared part, and never files the same copy twice (DRE-8843).

`scripts/parts_cleanup.py file` reads the JSON `parts_check.py count --json`
prints (DRE-8841) and files one card per copy through `linear_ops.create_card`
in `Intake`, labeled `repo:<slug>`, the `--agent` label and the `--initiative`
label. Each card's description carries the copy's identity line,
`parts-copy: <slug> | <Part> | <copy_path>`, and before filing the run reads
the team's issues in every state for a card carrying that line: one found is
counted as already filed and nothing is written.

The board here is a stand-in for Linear on the two seams the script uses: the
create seam (`linear_ops.create_card`) and the read (`linear_ops.gql`). The
read honors the query's own filter, so a search that left closed cards out
would miss the Done and Canceled cards below.

Run: cd bureau-pipeline && python3 -m pytest tests/test_parts_cleanup.py -v
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import linear_ops  # noqa: E402
import notice_writers  # noqa: E402
import parts_cleanup  # noqa: E402
import proof_reach  # noqa: E402
import ready_lane_writers  # noqa: E402

SLUG = "agent-bureau"
AGENT = "agent:frontend"
INITIATIVE = "initiative:agent-bureau"
OWNER = "DRE-8536"
OWNER_SENTENCE = (
    "This copy is in the scope of DRE-8536, which is deleting it; do not build this "
    "card on its own, and cancel it as a duplicate once the copy is gone from the "
    "default branch.")

MOLECULES = "console/web/src/components/molecules"
COPIES = [
    {"part": "Notice", "layer": "molecules", "part_path": f"{MOLECULES}/Notice.tsx",
     "part_line": 3, "copy_path": "console/web/src/pages/initiatives/InitiativesPage.tsx",
     "copy_line": 41},
    {"part": "Notice", "layer": "molecules", "part_path": f"{MOLECULES}/Notice.tsx",
     "part_line": 3, "copy_path": "console/web/src/pages/settings/SettingsPage.tsx",
     "copy_line": 12},
    {"part": "Button", "layer": "atoms",
     "part_path": "console/web/src/components/atoms/Button.tsx", "part_line": 5,
     "copy_path": "console/web/src/pages/board/BoardPage.tsx", "copy_line": 88},
]


def title_of(copy: dict) -> str:
    return f"{SLUG}: delete the copy of {copy['part']} in {copy['copy_path']}"


def identity_of(copy: dict) -> str:
    return f"parts-copy: {SLUG} | {copy['part']} | {copy['copy_path']}"


class Board:
    """Linear, as far as the script can reach it: cards created through the
    create seam, read back through `gql` by the query's own filter."""

    CLOSED = ("completed", "canceled")

    def __init__(self):
        self.cards = []
        self.created = []
        self.queries = []

    def add(self, title, description, *, state="Backlog", state_type="backlog",
            labels=(), lane="Intake"):
        number = 9100 + len(self.cards)
        card = {"identifier": f"DRE-{number}", "title": title, "description": description,
                "labels": list(labels), "lane": lane, "state": state,
                "state_type": state_type, "createdAt": f"2026-11-02T00:{len(self.cards):02d}:00Z",
                "url": f"https://linear.app/dreadnoughtfoundry/issue/DRE-{number}/x"}
        self.cards.append(card)
        return card

    def create_card(self, title, description, *, repo_slug, labels=(), lane="Planning"):
        names = list(dict.fromkeys([f"repo:{repo_slug}", *labels]))
        card = self.add(title, description, labels=names, lane=lane,
                        state=lane, state_type="triage")
        self.created.append(card)
        return {"id": f"id-{card['identifier']}", "identifier": card["identifier"],
                "url": card["url"]}

    def gql(self, query, variables=None, **_):
        variables = dict(variables or {})
        self.queries.append((query, variables))
        if "issueCreate" in query or "Update" in query:
            raise AssertionError(f"the script wrote to Linear outside the create seam: {query}")
        needle = next((v for k, v in variables.items()
                       if k != "after" and isinstance(v, str)), None)
        assert needle is not None, f"the search names nothing: {variables}"
        open_only = bool(re.search(r"state\s*:", query))
        nodes = [
            {"identifier": c["identifier"], "title": c["title"],
             "description": c["description"], "createdAt": c["createdAt"],
             "state": {"name": c["state"], "type": c["state_type"]}}
            for c in self.cards
            if needle.lower() in c["description"].lower()
            and not (open_only and c["state_type"] in self.CLOSED)
        ]
        return {"issues": {"nodes": nodes,
                           "pageInfo": {"hasNextPage": False, "endCursor": None}}}


class Run:
    def __init__(self, code, out, err):
        self.code, self.out, self.err = code, out, err
        self.out_lines = out.splitlines()
        self.err_lines = err.splitlines()


class CleanupCase(unittest.TestCase):

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.board = Board()

    def count_json(self, copies=None, *, repo=SLUG, raw=None, name="count.json"):
        path = Path(self.tmp.name) / name
        if raw is None:
            raw = json.dumps({"repo": repo, "source": ".github/bureau/design-system.json",
                              "reason": None,
                              "copies": COPIES if copies is None else copies})
        path.write_text(raw, encoding="utf-8")
        return str(path)

    def run_file(self, count_path, *extra, agent=AGENT, initiative=INITIATIVE, repo=SLUG):
        argv = ["file", "--repo", repo, "--count-json", count_path,
                "--agent", agent, "--initiative", initiative, *extra]
        out, err = io.StringIO(), io.StringIO()
        with patch.object(linear_ops, "create_card", self.board.create_card), \
                patch.object(linear_ops, "gql", self.board.gql), \
                redirect_stdout(out), redirect_stderr(err):
            code = parts_cleanup.main(argv)
        return Run(code, out.getvalue(), err.getvalue())

    def assert_unknown(self, run):
        self.assertEqual(1, run.code)
        self.assertEqual(1, len(run.err_lines), run.err)
        self.assertTrue(run.err_lines[0].startswith(f"parts-cleanup: {SLUG} — UNKNOWN — "),
                        run.err)
        self.assertEqual([], self.board.created)


class FilesOneCardPerCopyTest(CleanupCase):

    def test_three_copies_file_three_intake_cards(self):
        run = self.run_file(self.count_json())
        self.assertEqual(0, run.code, run.err)
        self.assertEqual(3, len(self.board.created))
        for copy, card in zip(COPIES, self.board.created):
            self.assertEqual("Intake", card["lane"])
            self.assertEqual(title_of(copy), card["title"])
            self.assertIn(identity_of(copy), card["description"].splitlines())
            self.assertEqual({f"repo:{SLUG}", AGENT, INITIATIVE}, set(card["labels"]))
        self.assertEqual([f"parts-cleanup: {SLUG} — 3 copy(ies), 3 filed, 0 already filed"],
                         run.err_lines)

    def test_the_card_says_what_was_copied_where_and_the_fix(self):
        self.run_file(self.count_json())
        card = self.board.created[0]
        body = card["description"]
        copy = COPIES[0]
        self.assertIn("Notice", body)
        self.assertIn(f"{copy['part_path']}:{copy['part_line']}", body)
        self.assertIn(f"{copy['copy_path']}:{copy['copy_line']}", body)
        self.assertIn("use the shared part and delete the copy", body)
        self.assertIn("deleted, not deprecated", body)
        self.assertIn("standards/design-system.md", body)
        self.assertIn("rules 2 and 3", body)

    def test_stdout_names_each_card_filed(self):
        run = self.run_file(self.count_json())
        expected = [f"parts-cleanup: filed {card['identifier']} — {title_of(copy)}"
                    for copy, card in zip(COPIES, self.board.created)]
        self.assertEqual(expected, run.out_lines)


class NeverFilesTwiceTest(CleanupCase):

    def test_a_second_run_files_nothing_and_names_the_same_cards(self):
        path = self.count_json()
        first = self.run_file(path)
        ids = [card["identifier"] for card in self.board.created]
        self.assertEqual(3, len(ids))
        second = self.run_file(path)
        self.assertEqual(0, second.code, second.err)
        self.assertEqual(3, len(self.board.created), "the second run created a card")
        self.assertEqual([f"parts-cleanup: {SLUG} — 3 copy(ies), 0 filed, 3 already filed"],
                         second.err_lines)
        self.assertEqual([f"parts-cleanup: already filed {i} — {title_of(c)}"
                          for i, c in zip(ids, COPIES)], second.out_lines)
        self.assertEqual(3, len(first.out_lines))

    def test_a_closed_card_counts_as_already_filed(self):
        for state, kind, copy in (("Done", "completed", COPIES[0]),
                                  ("Canceled", "canceled", COPIES[1])):
            self.board.add(title_of(copy), f"Some words.\n\n{identity_of(copy)}\n",
                           state=state, state_type=kind)
        found = [c["identifier"] for c in self.board.cards]
        run = self.run_file(self.count_json())
        self.assertEqual(0, run.code, run.err)
        self.assertEqual([title_of(COPIES[2])], [c["title"] for c in self.board.created])
        self.assertEqual([f"parts-cleanup: {SLUG} — 3 copy(ies), 1 filed, 2 already filed"],
                         run.err_lines)
        self.assertEqual(f"parts-cleanup: already filed {found[0]} — {title_of(COPIES[0])}",
                         run.out_lines[0])
        self.assertEqual(f"parts-cleanup: already filed {found[1]} — {title_of(COPIES[1])}",
                         run.out_lines[1])

    def test_the_search_reads_every_state(self):
        self.run_file(self.count_json())
        searches = [q for q, _ in self.board.queries]
        self.assertTrue(searches, "the run never searched the board")
        for query in searches:
            self.assertNotRegex(query, r"state\s*:")

    def test_a_longer_path_is_not_the_same_copy(self):
        copy = COPIES[0]
        self.board.add("someone else's", identity_of(copy) + ".bak\n")
        self.board.add("a mention", f"see {identity_of(copy)} above\n")
        self.run_file(self.count_json([copy]))
        self.assertEqual([title_of(copy)], [c["title"] for c in self.board.created])

    def test_an_identity_written_in_backticks_or_escaped_is_found(self):
        copy, other = COPIES[0], COPIES[1]
        self.board.add(title_of(copy), f"`{identity_of(copy)}`\n")
        self.board.add(title_of(other), identity_of(other).replace("|", "\\|") + "\n")
        run = self.run_file(self.count_json([copy, other]))
        self.assertEqual([], self.board.created)
        self.assertEqual([f"parts-cleanup: {SLUG} — 2 copy(ies), 0 filed, 2 already filed"],
                         run.err_lines)

    def test_another_repos_copy_is_not_this_one(self):
        copy = COPIES[0]
        self.board.add("portico's", identity_of(copy).replace(SLUG, "portico") + "\n")
        self.board.add("a longer slug's", identity_of(copy).replace(SLUG, SLUG + "-x") + "\n")
        self.run_file(self.count_json([copy]))
        self.assertEqual(1, len(self.board.created))


class OwnedByTest(CleanupCase):

    def test_owned_by_writes_the_owner_line_and_sentence(self):
        run = self.run_file(self.count_json(), "--owned-by", OWNER)
        self.assertEqual(0, run.code, run.err)
        self.assertEqual(3, len(self.board.created))
        for card in self.board.created:
            lines = card["description"].splitlines()
            self.assertIn(f"parts-owner: {OWNER}", lines)
            self.assertIn(OWNER_SENTENCE, lines)

    def test_without_owned_by_no_owner_line(self):
        self.run_file(self.count_json())
        for card in self.board.created:
            self.assertNotIn("parts-owner:", card["description"])

    def test_an_owner_that_is_not_a_card_id_files_nothing(self):
        for bad in ("8536", "DRE-", "dre-8536", "DRE-85x6", "the parts epic"):
            with self.subTest(bad=bad):
                self.assert_unknown(self.run_file(self.count_json(), "--owned-by", bad))
                self.assertEqual([], self.board.queries)


class EmptyAndDryRunTest(CleanupCase):

    def test_an_empty_copies_list_files_nothing(self):
        run = self.run_file(self.count_json([]))
        self.assertEqual(0, run.code, run.err)
        self.assertEqual([], self.board.created)
        self.assertEqual([], run.out_lines)
        self.assertEqual([f"parts-cleanup: {SLUG} — 0 copy(ies), 0 filed, 0 already filed"],
                         run.err_lines)

    def test_a_count_with_a_reason_and_no_copies_files_nothing(self):
        raw = json.dumps({"repo": SLUG, "source": None,
                          "reason": "the repo's design system does not exist (no LOCK)",
                          "copies": []})
        run = self.run_file(self.count_json(raw=raw))
        self.assertEqual(0, run.code, run.err)
        self.assertEqual([f"parts-cleanup: {SLUG} — 0 copy(ies), 0 filed, 0 already filed"],
                         run.err_lines)

    def test_dry_run_writes_nothing_and_says_what_it_would_file(self):
        copy = COPIES[0]
        found = self.board.add(title_of(copy), identity_of(copy) + "\n")
        run = self.run_file(self.count_json(), "--dry-run")
        self.assertEqual(0, run.code, run.err)
        self.assertEqual([], self.board.created)
        self.assertEqual([f"parts-cleanup: {SLUG} — 3 copy(ies), 2 filed, 1 already filed"],
                         run.err_lines)
        self.assertEqual([
            f"parts-cleanup: already filed {found['identifier']} — {title_of(COPIES[0])}",
            f"parts-cleanup: would file — {title_of(COPIES[1])}",
            f"parts-cleanup: would file — {title_of(COPIES[2])}",
        ], run.out_lines)


class UnknownTest(CleanupCase):

    def test_a_missing_file(self):
        self.assert_unknown(self.run_file(str(Path(self.tmp.name) / "absent.json")))

    def test_not_json(self):
        self.assert_unknown(self.run_file(self.count_json(raw="{not json")))

    def test_no_copies_key(self):
        raw = json.dumps({"repo": SLUG, "source": None, "reason": "boom"})
        self.assert_unknown(self.run_file(self.count_json(raw=raw)))

    def test_copies_not_a_list(self):
        raw = json.dumps({"repo": SLUG, "copies": {"part": "Notice"}})
        self.assert_unknown(self.run_file(self.count_json(raw=raw)))

    def test_a_copy_missing_a_field(self):
        broken = dict(COPIES[0])
        del broken["copy_path"]
        self.assert_unknown(self.run_file(self.count_json([COPIES[1], broken])))

    def test_a_count_of_another_repo(self):
        self.assert_unknown(self.run_file(self.count_json(repo="portico")))

    def test_a_repo_the_pipeline_does_not_serve(self):
        run = self.run_file(self.count_json(repo=None), repo="no-such-repo")
        self.assertEqual(1, run.code)
        self.assertEqual(1, len(run.err_lines))
        self.assertTrue(run.err_lines[0].startswith("parts-cleanup: no-such-repo — UNKNOWN — "))
        self.assertEqual([], self.board.created)

    def test_labels_of_the_wrong_kind(self):
        self.assert_unknown(self.run_file(self.count_json(), agent="frontend"))
        self.assert_unknown(self.run_file(self.count_json(), initiative="agent-bureau"))

    def test_a_board_that_cannot_be_read(self):
        def broken(*_a, **_k):
            raise linear_ops.LinearError("HTTP 500")
        out, err = io.StringIO(), io.StringIO()
        with patch.object(linear_ops, "create_card", self.board.create_card), \
                patch.object(linear_ops, "gql", broken), \
                redirect_stdout(out), redirect_stderr(err):
            code = parts_cleanup.main(["file", "--repo", SLUG, "--count-json",
                                       self.count_json(), "--agent", AGENT,
                                       "--initiative", INITIATIVE])
        self.assert_unknown(Run(code, out.getvalue(), err.getvalue()))

    def test_the_tag(self):
        self.assertEqual("parts-cleanup:", parts_cleanup.TAG)


class WritersTest(unittest.TestCase):
    """The new Intake writer is seen, and passes, by the checks that read writers."""

    def test_the_ready_lane_check_reads_it_as_an_intake_writer(self):
        found = [w for w in ready_lane_writers.writes()
                 if w.where.startswith("scripts/parts_cleanup.py:")]
        self.assertTrue(found, "the ready-lane check never saw the writer")
        self.assertEqual({"Intake"}, {w.lane for w in found})
        self.assertEqual([], [p for p in ready_lane_writers.writer_problems()
                              if "parts_cleanup" in str(p)])

    def test_the_notice_writers_check_declares_it(self):
        rows = json.loads(notice_writers.CONFIG.read_text(encoding="utf-8"))
        rows = rows.get("writers", rows) if isinstance(rows, dict) else rows
        mine = [r for r in rows if r.get("file") == "scripts/parts_cleanup.py"]
        self.assertEqual(1, len(mine))
        self.assertEqual("kept", mine[0]["disposition"])


class ProofReachTest(unittest.TestCase):

    def test_the_line_is_kept(self):
        doc = json.loads((ROOT / "config" / "proof-reach.json").read_text(encoding="utf-8"))
        tags = {e["tag"]: e for e in doc["log_lines"]}
        entry = tags["parts-cleanup:"]
        self.assertEqual(("scripts/parts_cleanup.py", "TAG"),
                         (entry["printed_by"], entry["constant"]))
        self.assertIn("DRE-8843", entry["when"])
        self.assertEqual([], proof_reach._log_line_problems(ROOT, entry))


if __name__ == "__main__":
    unittest.main()
