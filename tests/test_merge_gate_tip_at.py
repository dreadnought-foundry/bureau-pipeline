"""`main`'s tip when a test run began, walked on its first-parent line (DRE-6966).

The new-test rule (DRE-6871, DRE-6872) compares what `main` gained since a
pull request's last test run began. The gate used to read the tip as
`commits?sha=main&until=<created_at>&per_page=1`: the newest-DATED commit
`main` holds, which is a side branch's commit whenever a pull request that
merged later was written earlier. bureau-pipeline #1032 is the shape: its
tests began at 18:26:11Z with `main` at `367bc2ec`; #1029 merged at 18:31
as `c7a9a347`, and its branch commit `960c10c3` was written at 18:04:02Z.
The old read answered `960c10c3`, and #1029's own change to
`scripts/reconcile.py` dropped out of the comparison.

`merge_gate.tip_at` walks first parents from the listing's start and
returns the first commit dated no later than the run, and `merge_gate.py
tip-at` prints it as `sha=<40 hex>` or `sha=none`.
"""

import json
import subprocess  # nosec B404 — fixed argv, our own script
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import merge_gate  # noqa: E402

MERGE_1029 = "c7a9a347" + "0" * 32
BRANCH_1029 = "960c10c3" + "1" * 32
MERGE_1027 = "367bc2ec" + "2" * 32
PARENT_1027 = "3001aa00" + "3" * 32

RUN_1032 = "2026-10-11T18:26:11Z"


def commit(sha, date, *parents):
    """One entry of GitHub's commit listing, in the fields the walk reads
    and the author date it must never read."""
    return {"sha": sha,
            "commit": {"author": {"date": "2026-10-11T17:00:00Z"},
                       "committer": {"date": date}},
            "parents": [{"sha": p, "url": "https://api.github.com/x"}
                        for p in parents]}


def listing_1029():
    """The listing GitHub gives for `main` at `c7a9a347`, in its order."""
    return [
        commit(MERGE_1029, "2026-10-11T18:31:04Z", MERGE_1027, BRANCH_1029),
        commit(BRANCH_1029, "2026-10-11T18:04:02Z", MERGE_1027),
        commit(MERGE_1027, "2026-10-11T18:02:37Z", PARENT_1027),
    ]


class TipAtTest(unittest.TestCase):

    def test_the_merge_of_1029_is_walked_past_to_1027(self):
        tip = merge_gate.tip_at(listing_1029(), MERGE_1029, RUN_1032)
        self.assertEqual(tip, MERGE_1027)
        self.assertNotEqual(tip, BRANCH_1029)

    def test_a_run_after_the_newest_commit_is_the_start(self):
        self.assertEqual(
            merge_gate.tip_at(listing_1029(), MERGE_1029,
                              "2026-10-11T18:44:46Z"),
            MERGE_1029)

    def test_a_run_at_a_commits_own_date_is_that_commit(self):
        """Not later than the run: a commit dated the same second counts."""
        self.assertEqual(
            merge_gate.tip_at(listing_1029(), MERGE_1029,
                              "2026-10-11T18:02:37Z"),
            MERGE_1027)

    def test_a_run_before_the_oldest_first_parent_is_none(self):
        """The walk reaches `367bc2ec`, dated after the run, and its first
        parent is not in the listing."""
        self.assertIsNone(merge_gate.tip_at(listing_1029(), MERGE_1029,
                                            "2026-10-11T17:30:00Z"))

    def test_a_missing_first_parent_is_none(self):
        """The walk never steps onto the second parent to go on."""
        listing = [c for c in listing_1029() if c["sha"] != MERGE_1027]
        self.assertIsNone(merge_gate.tip_at(listing, MERGE_1029, RUN_1032))

    def test_a_start_not_in_the_listing_is_none(self):
        self.assertIsNone(merge_gate.tip_at(listing_1029(), PARENT_1027,
                                            RUN_1032))

    def test_a_commit_with_no_parents_is_none(self):
        listing = listing_1029()
        listing[0]["parents"] = []
        self.assertIsNone(merge_gate.tip_at(listing, MERGE_1029, RUN_1032))

    def test_an_unreadable_date_is_none(self):
        for date in ("yesterday", "", None, 1760206264):
            listing = listing_1029()
            listing[0]["commit"]["committer"]["date"] = date
            self.assertIsNone(
                merge_gate.tip_at(listing, MERGE_1029, RUN_1032), date)

    def test_a_commit_with_no_committer_is_none(self):
        listing = listing_1029()
        del listing[0]["commit"]["committer"]
        self.assertIsNone(merge_gate.tip_at(listing, MERGE_1029, RUN_1032))

    def test_an_unreadable_run_time_is_none(self):
        self.assertIsNone(merge_gate.tip_at(listing_1029(), MERGE_1029,
                                            "not a time"))

    def test_offsets_are_compared_as_instants(self):
        """18:26:11Z is 11:26:11-07:00: the same instant, the same tip."""
        self.assertEqual(
            merge_gate.tip_at(listing_1029(), MERGE_1029,
                              "2026-10-11T11:26:11-07:00"),
            MERGE_1027)

    def test_a_first_parent_cycle_is_none(self):
        listing = [commit(MERGE_1029, "2026-10-11T18:31:04Z", BRANCH_1029),
                   commit(BRANCH_1029, "2026-10-11T18:30:00Z", MERGE_1029)]
        self.assertIsNone(merge_gate.tip_at(listing, MERGE_1029, RUN_1032))


class TipAtCliTest(unittest.TestCase):

    def cli(self, payload, at=RUN_1032):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "commits.json"
            path.write_text(payload if isinstance(payload, str)
                            else json.dumps(payload))
            return subprocess.run(  # nosec B603 B607 — fixed argv
                [sys.executable, str(ROOT / "scripts" / "merge_gate.py"),
                 "tip-at", "--commits-file", str(path), "--at", at],
                capture_output=True, text=True)

    def test_it_prints_the_tip_walked_from_the_listings_start(self):
        proc = self.cli(listing_1029())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, f"sha={MERGE_1027}\n")

    def test_it_reads_the_pages_the_gate_collects(self):
        """An array of pages, one per read, walks as one listing."""
        listing = listing_1029()
        proc = self.cli([listing[:2], listing[2:]])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, f"sha={MERGE_1027}\n")

    def test_a_run_before_the_listing_prints_none(self):
        proc = self.cli(listing_1029(), at="2026-10-11T17:30:00Z")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "sha=none\n")

    def test_an_empty_listing_prints_none(self):
        proc = self.cli([])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "sha=none\n")

    def test_a_file_that_is_not_json_exits_2_with_no_sha_line(self):
        proc = self.cli("<html>502 Bad Gateway</html>")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertNotIn("sha=", proc.stdout)

    def test_a_listing_that_is_not_an_array_exits_2(self):
        proc = self.cli({"message": "Not Found"})
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertNotIn("sha=", proc.stdout)

    def test_a_missing_file_exits_2(self):
        proc = subprocess.run(  # nosec B603 B607 — fixed argv
            [sys.executable, str(ROOT / "scripts" / "merge_gate.py"),
             "tip-at", "--commits-file", "/nonexistent/commits.json",
             "--at", RUN_1032],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertNotIn("sha=", proc.stdout)

    def test_a_tip_that_is_not_a_full_sha_is_never_printed(self):
        """It goes into the compare's URL, so only 40 hex pass."""
        listing = [commit("main?x=1", "2026-10-11T18:00:00Z", MERGE_1027)]
        proc = self.cli(listing)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertNotIn("sha=", proc.stdout)


if __name__ == "__main__":
    unittest.main()
