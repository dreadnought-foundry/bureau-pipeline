"""One GitHub read per record per job (Stage 2 fix #21, BP-8).

`scripts/read_once.py` is the seam the critic (`qa-review.yml`) and the fix
agent (`agent-fix.yml`) read the same pull request record through, so a record
five steps each fetched is fetched once. What these pin:

* the second read of a key is served from the job's first, byte for byte;
* a FAILED read is never kept: the caller sees exactly the command's exit code,
  stdout and stderr, and the next step that asks reads again — so a step whose
  read was soft (`|| true`) stays soft and a step that failed loud still does;
* no cache directory (outside a job) means no cache: every call reads;
* `pr` reads the union of the fields a job needs ONCE, and each step takes its
  field as `gh pr view --json X --jq .X` printed it.

Everything runs against a stub `gh` that counts its calls; nothing reaches
GitHub.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "read_once.py"

GH_STUB = r"""#!/bin/sh
# Counts every call; answers from files the test wrote.
echo "$*" >> "$GH_CALLS"
if [ -f "$GH_FAIL" ]; then echo "gh: HTTP 502" >&2; echo "partial"; exit 1; fi
case "$1 $2" in
  "pr view") cat "$GH_PR" ;;
  *) printf '[{"n":1}]\n' ;;
esac
"""

RECORD = {"headRefName": "agent/DRE-7-x", "headRefOid": "a" * 40, "baseRefName": "main",
          "body": "Implements DRE-7.\n\nSecond paragraph.", "changedFiles": 3,
          "additions": 10, "deletions": 2}
FIELDS = "headRefName,headRefOid,baseRefName,body,changedFiles,additions,deletions"


class _Job(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "bin").mkdir()
        gh = self.tmp / "bin" / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(0o755)
        (self.tmp / "pr.json").write_text(json.dumps(RECORD) + "\n")
        self.runner_temp = self.tmp / "runner-temp"
        self.runner_temp.mkdir()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_once(self, *args, runner_temp=True, repo="dreadnought-foundry/portico"):
        env = {
            "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "GH_CALLS": str(self.tmp / "calls"),
            "GH_PR": str(self.tmp / "pr.json"),
            "GH_FAIL": str(self.tmp / "fail"),
        }
        if runner_temp:
            env["RUNNER_TEMP"] = str(self.runner_temp)
        if repo:
            env["GITHUB_REPOSITORY"] = repo
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env,
                              capture_output=True, text=True, timeout=60)

    def calls(self) -> list[str]:
        path = self.tmp / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def fail_gh(self, on=True):
        path = self.tmp / "fail"
        if on:
            path.write_text("1")
        elif path.exists():
            path.unlink()


class OnceTest(_Job):
    def test_the_second_read_of_a_key_is_the_first_ones_answer(self):
        cmd = ["once", "issue-comments-7", "--", "gh", "api", "--paginate", "--slurp",
               "repos/o/r/issues/7/comments?per_page=100"]
        first = self.run_once(*cmd)
        second = self.run_once(*cmd)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, '[{"n":1}]\n')
        self.assertEqual(second.stdout, first.stdout)
        self.assertEqual(len(self.calls()), 1)

    def test_a_failed_read_is_passed_through_and_never_kept(self):
        cmd = ["once", "compare-main...abc", "--", "gh", "api", "repos/o/r/compare/main...abc"]
        self.fail_gh()
        failed = self.run_once(*cmd)
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(failed.stdout, "partial\n")
        self.assertIn("HTTP 502", failed.stderr)
        self.fail_gh(False)
        again = self.run_once(*cmd)
        self.assertEqual(again.returncode, 0)
        self.assertEqual(again.stdout, '[{"n":1}]\n')
        self.assertEqual(len(self.calls()), 2, "the failed read was not kept, so it was read again")

    def test_keys_are_independent(self):
        self.run_once("once", "a", "--", "gh", "api", "x")
        self.run_once("once", "b", "--", "gh", "api", "y")
        self.assertEqual(len(self.calls()), 2)

    def test_outside_a_job_there_is_no_cache(self):
        for _ in range(2):
            self.run_once("once", "a", "--", "gh", "api", "x", runner_temp=False)
        self.assertEqual(len(self.calls()), 2)

    def test_out_is_written_only_on_success(self):
        out = self.tmp / "thread.json"
        self.fail_gh()
        failed = self.run_once("once", "t", "--out", str(out), "--", "gh", "api", "x")
        self.assertEqual(failed.returncode, 1)
        self.assertFalse(out.exists())
        self.fail_gh(False)
        ok = self.run_once("once", "t", "--out", str(out), "--", "gh", "api", "x")
        self.assertEqual(ok.returncode, 0)
        self.assertEqual(out.read_text(), '[{"n":1}]\n')
        self.assertEqual(ok.stdout, "")


class PrRecordTest(_Job):
    def test_every_field_comes_from_one_read(self):
        got = {f: self.run_once("pr", "7", "--fields", FIELDS, "--field", f).stdout
               for f in ("headRefName", "headRefOid", "baseRefName", "body")}
        self.assertEqual(got["headRefName"], "agent/DRE-7-x\n")
        self.assertEqual(got["headRefOid"], "a" * 40 + "\n")
        self.assertEqual(got["baseRefName"], "main\n")
        self.assertEqual(got["body"], RECORD["body"] + "\n")  # `--jq .body`'s output
        self.assertEqual(self.calls(), [f"pr view 7 --repo dreadnought-foundry/portico --json {FIELDS}"])

    def test_pick_writes_the_subset_a_reader_asked_for(self):
        out = self.run_once("pr", "7", "--fields", FIELDS, "--pick",
                            "changedFiles,additions,deletions")
        self.assertEqual(json.loads(out.stdout),
                         {"changedFiles": 3, "additions": 10, "deletions": 2})

    def test_read_at_is_when_the_record_was_read(self):
        first = self.run_once("pr", "7", "--fields", FIELDS, "--read-at").stdout.strip()
        later = self.run_once("pr", "7", "--fields", FIELDS, "--read-at").stdout.strip()
        self.assertRegex(first, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(first, later)
        self.assertEqual(len(self.calls()), 1)

    def test_a_different_field_set_is_a_different_read(self):
        self.run_once("pr", "7", "--fields", FIELDS, "--field", "headRefOid")
        self.run_once("pr", "7", "--fields", "state,headRefOid", "--field", "headRefOid")
        self.assertEqual(len(self.calls()), 2)

    def test_a_failed_record_read_prints_nothing_and_is_read_again(self):
        self.fail_gh()
        failed = self.run_once("pr", "7", "--fields", FIELDS, "--field", "headRefOid")
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(failed.stdout, "")
        self.fail_gh(False)
        ok = self.run_once("pr", "7", "--fields", FIELDS, "--field", "headRefOid")
        self.assertEqual(ok.stdout, "a" * 40 + "\n")
        self.assertEqual(len(self.calls()), 2)

    def test_a_record_missing_a_field_asked_for_is_not_kept(self):
        (self.tmp / "pr.json").write_text(json.dumps({"headRefOid": "b" * 40}))
        bad = self.run_once("pr", "7", "--fields", FIELDS, "--field", "headRefOid")
        self.assertNotEqual(bad.returncode, 0)
        self.assertEqual(bad.stdout, "")
        (self.tmp / "pr.json").write_text(json.dumps(RECORD))
        self.run_once("pr", "7", "--fields", FIELDS, "--field", "headRefOid")
        self.assertEqual(len(self.calls()), 2)

    def test_no_repository_reads_the_checkout_s_own(self):
        self.run_once("pr", "7", "--fields", FIELDS, "--field", "headRefOid", repo=None)
        self.assertEqual(self.calls(), [f"pr view 7 --json {FIELDS}"])


if __name__ == "__main__":
    unittest.main()
