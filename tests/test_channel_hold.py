"""A held `stable` channel, diagnosed and raised as one console alert (DRE-8653).

`stable` was held 32 times in October 2026, for 78 hours in all, and the
console's channel-health monitor said only that the channel was quiet. On
10-27 the answer was "a re-run of one canceled test job", and it stood for five
hours. `scripts/channel_hold.py` works out whether the channel is held and since
when, names the failing job and what it needs next — a re-run, a fix or a
person — and raises that through the notice door at 2 hours and before the
06:30 PT briefing.

What these pin:

* the 10-27 replay: a fixture built from GitHub's own records of that morning
  (run 40502923604, Promote Channel runs 40448885207 to 40512121894) answers
  `--at` 7:20 AM PT with a raise naming the walk shard 5 of 8 job and
  `next=re-run`, 6:10 AM PT with a morning raise, and 5:30 AM PT with nothing;
* the 11-02 replay (DRE-8778): a merge train kept the hold past 2 hours while
  the newest run was 15 minutes old, and the pass names no job, says no
  "Failing job" and asks for no re-run of failed jobs;
* the next step: attempt 2 is a person's, a failed test is a fix, a hold by
  hand is a person's with the `CHANNEL_HOLD` text quoted;
* Promote Channel's reason, and every unread read, go into `Needs:` whole
  (DRE-8797): the 446-character by-hand `no-harness-stamp` refusal, clipped at
  300, had cut the last proven sha in half;
* a running or not-started job is named only once the job itself has gone 2
  hours, and as a `Stuck job:`, never a failing one;
* every `OUTCOME_*` of `promote_channel`, read off the module, maps to one next
  step or is declared not a hold;
* the raise windows, against a stubbed `pipeline_notice` and no network;
* without `--apply`, and with `--at`, the door is never called;
* a read that leaves the hold unknown raises nothing and resolves nothing.

No test here reaches GitHub or the console: `FakeGitHub` answers the paths the
module reads, the way GitHub answers them today, and the notice client is a
stub.
"""
from __future__ import annotations

import inspect
import io
import os
import re
import sys
import urllib.parse
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import channel_hold  # noqa: E402
import pipeline_notice  # noqa: E402
import promote_channel  # noqa: E402

REPO = "dreadnought-foundry/bureau-pipeline"
SITE = f"https://github.com/{REPO}"

# ── the 10-27 records, as GitHub holds them ────────────────────────────────

STABLE = "1b5ecc91ef3806e5e671ceb1b4568afbcd4c7b24"
MERGE = "69b518ceb0872bb5a962c598f0f30884401d1327"
#: The bot branch's own commit. Dated the evening before, so a hold read off
#: the oldest commit `stable` lacks, rather than the oldest FIRST-PARENT one,
#: would start on 10-26.
BRANCH = "d9b1aff4900000000000000000000000000000b1"
#: A later merge, after every moment replayed here, so a replay that read
#: `main` as it stands today would see it.
LATER = "f00dfeed00000000000000000000000000000001"
LATER_BRANCH = "f00dfeed00000000000000000000000000000002"
UNIT_RUN = 40502923604
HARNESS_RUN = 40502923498
SHARD = ("scripts unit tests (walk tests/test_console_project_model_keeps_"
         "the_suite_green.py shard 5 of 8)")
ROLLUP = "scripts unit tests"

PROMOTING_LINE = (f"harness-passed-promoting: promoting stable to {STABLE} — "
                  "harness green, strictly ahead. Mirror check: passed — 53 "
                  "agent-bureau tests at 7555ef3 green; 0 failing, 0 already "
                  "red on stable.")
NOT_AHEAD_LINE = (f"not-ahead-of-channel: stable is already at {STABLE} — "
                  "nothing to do.")
NOT_FINISHED_LINE = (f"unit-suite-not-finished: not promoting {MERGE}: its own "
                     f"unit suite on main, Pipeline Tests run {UNIT_RUN}, has "
                     "not finished (status=in_progress). stable waits for it.")
UNPROVEN_LINE = (f"unit-suite-unproven: not promoting {MERGE}: its own unit "
                 f"suite on main proves nothing either way — Pipeline Tests "
                 f"run {UNIT_RUN} reads status='completed', "
                 "conclusion='cancelled'. Failing closed.")
HOLD_TEXT = "DRE-9999 the sandbox is being rebuilt"
HELD_LINE = (f"channel-held: channel HELD — not promoting {MERGE}. Reason on "
             f"record: {HOLD_TEXT}. Clear the hold variable to resume.")


def commit(sha, date, *parents):
    return {"sha": sha, "date": date, "parents": list(parents)}


def job(id_, name, conclusion, started, completed, *steps, status="completed"):
    return {"id": id_, "name": name, "status": status,
            "conclusion": conclusion if status == "completed" else None,
            "started_at": started, "completed_at": completed,
            "steps": [{"name": n, "conclusion": c} for n, c in steps]}


def attempt(started, updated, conclusion, jobs, status="completed"):
    return {"run_started_at": started, "updated_at": updated, "status": status,
            "conclusion": conclusion if status == "completed" else None,
            "jobs": jobs}


def run(id_, workflow, name, head, created, attempts, event="push"):
    return {"id": id_, "workflow": workflow, "name": name, "head_sha": head,
            "event": event, "created_at": created, "attempts": attempts}


def promotion(id_, created, updated, line):
    return {"id": id_, "created_at": created, "updated_at": updated, "line": line}


def unit_jobs(shard_conclusion="cancelled", shard_step="cancelled",
              shard_done="2026-10-27T12:40:43Z", rollup_done="2026-10-27T12:42:29Z"):
    """Attempt 1 of run 40502923604, its jobs as GitHub lists them (a subset)."""
    return [
        job(122184869842, "act registry consumers", "success",
            "2026-10-27T12:20:03Z", "2026-10-27T12:20:11Z"),
        job(122184870103, "scripts unit tests (part 1)", "success",
            "2026-10-27T12:20:08Z", "2026-10-27T12:40:24Z"),
        job(122184991657, SHARD.replace("shard 5", "shard 6"), "success",
            "2026-10-27T12:20:20Z", "2026-10-27T12:26:23Z"),
        job(122184991649, SHARD, shard_conclusion, "2026-10-27T12:20:22Z",
            shard_done, ("Set up job", "success"),
            ("Console-change walk", shard_step)),
        job(122194652860, ROLLUP, "failure", "2026-10-27T12:41:49Z",
            rollup_done, ("Every part passed", "failure")),
        job(122184870418, "console change keeps the suite green (part 2)",
            "success", "2026-10-27T12:20:04Z", "2026-10-27T13:26:44Z"),
        job(122184871729, "TDD commit discipline", "skipped",
            "2026-10-27T12:20:02Z", "2026-10-27T12:20:01Z"),
    ]


def oct27(*, attempts=None, promotions=None, jobs=None):
    """The 10-27 state: `stable` at 1b5ecc91e, merge 69b518ceb at 5:19:57 AM
    PT, run 40502923604 on attempt 1 `cancelled`, the decision
    `unit-suite-unproven`."""
    first = attempt("2026-10-27T12:20:01Z", "2026-10-27T13:26:58Z", "cancelled",
                    jobs or unit_jobs())
    return FakeGitHub(
        stable=LATER, main=LATER,
        commits=[
            commit(STABLE, "2026-10-27T03:09:44Z", "1275d736c0000000000000000000000000000000"),
            commit(BRANCH, "2026-10-26T20:00:00Z", STABLE),
            commit(MERGE, "2026-10-27T12:19:57Z", STABLE, BRANCH),
            commit(LATER_BRANCH, "2026-10-27T17:00:00Z", MERGE),
            commit(LATER, "2026-10-27T18:05:00Z", MERGE, LATER_BRANCH),
        ],
        runs=[
            run(UNIT_RUN, "tests.yml", "Pipeline Tests", MERGE,
                "2026-10-27T12:20:01Z", attempts or [first]),
            run(HARNESS_RUN, "harness.yml", "Integration Harness", MERGE,
                "2026-10-27T12:20:01Z", [attempt(
                    "2026-10-27T12:20:01Z", "2026-10-27T12:29:02Z", "success",
                    [job(122185000001, "harness", "success",
                         "2026-10-27T12:20:05Z", "2026-10-27T12:29:00Z",
                         ("Run the scenarios", "success"))])]),
        ],
        promotions=promotions or [
            promotion(40448885207, "2026-10-27T04:17:06Z", "2026-10-27T04:21:45Z",
                      PROMOTING_LINE),
            promotion(40500024548, "2026-10-27T11:57:27Z", "2026-10-27T11:57:43Z",
                      NOT_AHEAD_LINE),
            promotion(40504101723, "2026-10-27T12:29:05Z", "2026-10-27T12:29:24Z",
                      NOT_FINISHED_LINE),
            promotion(40512121894, "2026-10-27T13:27:00Z", "2026-10-27T13:27:40Z",
                      UNPROVEN_LINE),
        ],
    )


# ── the fake GitHub ─────────────────────────────────────────────────────────


class FakeGitHub:
    """Answers the REST paths `channel_hold` reads, as GitHub answers them
    today — every attempt and job as it ended, never as it stood at a past
    moment. Seeing the past is the module's job."""

    def __init__(self, *, stable, main, commits, runs=(), promotions=(),
                 broken=()):
        self.stable, self.main = stable, main
        self.commits = {c["sha"]: c for c in commits}
        self.runs = list(runs)
        self.promotions = list(promotions)
        self.broken = tuple(broken)
        self.calls = []

    # the reader interface
    def read_json(self, path):
        self._check(path)
        return self._route(path)

    def read_text(self, path):
        self._check(path)
        m = re.fullmatch(rf"repos/{REPO}/actions/jobs/(\d+)/logs", path)
        assert m, f"unexpected text read {path}"
        for p in self.promotions:
            if p["id"] * 10 == int(m.group(1)):
                stamp = p["updated_at"].replace("Z", ".1234567Z")
                return "\n".join([
                    f"{p['created_at'].replace('Z', '.0000000Z')} \x1b[36;1m##[group]Run python3 scripts/promote_channel.py\x1b[0m",
                    f"{stamp}   OUTCOME: {p['line'].split(':', 1)[0]}",
                    f"{stamp} {p['line']}",
                    f"{stamp} pipeline-channel: state=behind",
                ])
        raise AssertionError(f"no log for {path}")

    def _check(self, path):
        self.calls.append(path)
        for broken in self.broken:
            if broken in path:
                raise channel_hold.Unreadable(f"gh api {path} failed rc=1: HTTP 502")

    def _ancestors(self, sha):
        seen, todo = set(), [sha]
        while todo:
            s = todo.pop()
            if s in seen or s not in self.commits:
                continue
            seen.add(s)
            todo.extend(self.commits[s]["parents"])
        return seen

    def _attempt_meta(self, r, n):
        a = r["attempts"][n - 1]
        return {"id": r["id"], "name": r["name"], "head_sha": r["head_sha"],
                "head_branch": "main", "event": r["event"],
                "html_url": f"{SITE}/actions/runs/{r['id']}",
                "created_at": r["created_at"], "run_attempt": n,
                "run_started_at": a["run_started_at"],
                "updated_at": a["updated_at"], "status": a["status"],
                "conclusion": a["conclusion"]}

    def _route(self, path):
        bare, _, query = path.partition("?")
        q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
        base = f"repos/{REPO}/"
        assert bare.startswith(base), path
        rest = bare[len(base):]
        if rest == "git/ref/tags/stable":
            return {"ref": "refs/tags/stable",
                    "object": {"type": "commit", "sha": self.stable}}
        if rest == "git/ref/heads/main":
            return {"ref": "refs/heads/main",
                    "object": {"type": "commit", "sha": self.main}}
        m = re.fullmatch(r"compare/([0-9a-f]+)\.\.\.([0-9a-f]+)", rest)
        if m:
            return self._compare(m.group(1), m.group(2), q)
        m = re.fullmatch(r"actions/workflows/([\w.-]+)/runs", rest)
        if m:
            return self._runs(m.group(1), q)
        m = re.fullmatch(r"actions/runs/(\d+)/attempts/(\d+)(/jobs)?", rest)
        if m:
            r = next(r for r in self.runs if r["id"] == int(m.group(1)))
            n = int(m.group(2))
            if m.group(3):
                jobs = r["attempts"][n - 1]["jobs"]
                return {"total_count": len(jobs), "jobs": jobs}
            return self._attempt_meta(r, n)
        m = re.fullmatch(r"actions/runs/(\d+)/jobs", rest)
        if m:
            p = next(p for p in self.promotions if p["id"] == int(m.group(1)))
            return {"total_count": 1, "jobs": [{
                "id": p["id"] * 10, "name": "promote", "status": "completed",
                "conclusion": "success"}]}
        raise AssertionError(f"unexpected read {path}")

    def _compare(self, base, head, q):
        carried, reach = self._ancestors(base), self._ancestors(head)
        if base == head:
            status = "identical"
        elif head in carried:
            status = "behind"
        elif base in reach:
            status = "ahead"
        else:
            status = "diverged"
        lacking = sorted((self.commits[s] for s in reach - carried),
                         key=lambda c: c["date"])
        listed = [{"sha": c["sha"], "parents": [{"sha": p} for p in c["parents"]],
                   "commit": {"committer": {"date": c["date"]}}} for c in lacking]
        page = int(q.get("page", "1"))
        per = int(q.get("per_page", "250"))
        return {"status": status, "ahead_by": len(listed), "behind_by": 0,
                "total_commits": len(listed),
                "commits": listed[(page - 1) * per: page * per]}

    def _runs(self, workflow, q):
        if workflow == "promote-channel.yml":
            listed = [{"id": p["id"], "name": "Promote Channel",
                       "html_url": f"{SITE}/actions/runs/{p['id']}",
                       "event": "workflow_run", "status": "completed",
                       "conclusion": "success", "created_at": p["created_at"],
                       "updated_at": p["updated_at"], "run_attempt": 1}
                      for p in self.promotions]
        else:
            listed = [self._attempt_meta(r, len(r["attempts"])) for r in self.runs
                      if r["workflow"] == workflow]
        if "head_sha" in q:
            listed = [r for r in listed if r.get("head_sha") == q["head_sha"]]
        if "event" in q:
            listed = [r for r in listed if r["event"] == q["event"]]
        if "status" in q:
            listed = [r for r in listed if r["status"] == q["status"]]
        if "created" in q:
            assert q["created"].startswith("<="), q["created"]
            listed = [r for r in listed if r["created_at"] <= q["created"][2:]]
        listed.sort(key=lambda r: r["created_at"], reverse=True)
        return {"total_count": len(listed), "workflow_runs": listed}


# ── helpers ─────────────────────────────────────────────────────────────────


def stub_door(raise_outcome="raised"):
    return SimpleNamespace(
        raise_notice=mock.MagicMock(return_value=raise_outcome),
        resolve_notice=mock.MagicMock(return_value="resolved"),
    )


def run_main(reader, *flags, now=None, hold="", door=None):
    """`channel_hold.py watch` with `reader` for GitHub and `door` for the
    notice client; returns (exit code, stdout, stderr, door)."""
    door = door or stub_door()
    out, err = io.StringIO(), io.StringIO()
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(channel_hold, "pipeline_notice", door))
        stack.enter_context(mock.patch.dict(os.environ, {"CHANNEL_HOLD": hold}))
        if now is not None:
            stack.enter_context(
                mock.patch.object(channel_hold, "_now", return_value=now))
        stack.enter_context(redirect_stdout(out))
        stack.enter_context(redirect_stderr(err))
        code = channel_hold.main(["watch", "--repo", REPO, *flags], reader=reader)
    return code, out.getvalue(), err.getvalue(), door


def replay(reader, at, *, hold=""):
    """One `--at` pass through `watch`, the function `main` prints."""
    with mock.patch.dict(os.environ, {"CHANNEL_HOLD": hold}):
        return channel_hold.watch(reader, REPO, interval=10,
                                  at=channel_hold.parse_at(at), apply=False,
                                  hold_text=hold)


TITLE_SHAPE = re.compile(
    r"^stable held since \d{1,2}:\d{2} (AM|PM) PT — needs "
    r"(a re-run|a fix|a person): \S.*$")


# ── the 10-27 replay ────────────────────────────────────────────────────────


class TestTheTwentySeventh:
    def test_seven_twenty_raises_a_re_run_of_the_walk_shard(self):
        code, out, _err, door = run_main(
            oct27(), "--interval-minutes", "10", "--at", "2026-10-27T14:20:00Z")
        assert code == 0
        lines = out.strip().splitlines()
        assert lines == [
            f'channel-hold: held since 5:19 AM PT age=120m next=re-run '
            f'job="{SHARD}" run={SITE}/actions/runs/{UNIT_RUN} — would-raise'
        ]
        door.raise_notice.assert_not_called()
        door.resolve_notice.assert_not_called()

    def test_the_title_it_would_send_has_the_contract_shape(self):
        seen = replay(oct27(), "2026-10-27T14:20:00Z")
        assert seen.action == "raise"
        assert TITLE_SHAPE.match(seen.title), seen.title
        assert seen.title == f"stable held since 5:19 AM PT — needs a re-run: {SHARD}"
        assert len(seen.title) <= pipeline_notice.TITLE_LIMIT

    def test_the_body_it_would_send_is_the_four_contract_lines(self):
        seen = replay(oct27(), "2026-10-27T14:20:00Z")
        lines = seen.body.splitlines()
        assert len(lines) == 4
        assert lines[0] == f"Failing job: {SHARD} ({SITE}/actions/runs/{UNIT_RUN})"
        assert lines[1].startswith("Needs: a re-run — ")
        assert "cancel" in lines[1].lower()
        assert lines[2] == "Held since: 2026-10-27T12:19:57Z (5:19 AM PT)"
        assert lines[3] == "Waiting on stable: 1 commit(s), oldest 69b518c"

    def test_six_ten_is_a_morning_raise(self):
        code, out, _err, door = run_main(
            oct27(), "--interval-minutes", "10", "--at", "2026-10-27T13:10:00Z")
        assert code == 0
        line = out.strip()
        assert line.startswith("channel-hold: held since 5:19 AM PT age=50m ")
        assert line.endswith(" — would-morning-raise")
        assert f'job="{SHARD}"' in line
        door.raise_notice.assert_not_called()

    def test_five_thirty_raises_nothing(self):
        code, out, _err, door = run_main(
            oct27(), "--interval-minutes", "10", "--at", "2026-10-27T12:30:00Z")
        assert code == 0
        line = out.strip()
        assert line.startswith("channel-hold: held since 5:19 AM PT age=10m ")
        assert line.endswith(" — quiet")
        assert "would-" not in line
        door.raise_notice.assert_not_called()

    def test_the_replay_reads_stable_from_the_promoting_run_not_the_tag(self):
        # The tag stands at LATER today; read as of 7:20 AM, stable was at
        # 1b5ecc91e, which only Promote Channel run 40448885207 says.
        fake = oct27()
        seen = replay(fake, "2026-10-27T14:20:00Z")
        assert seen.action == "raise"
        assert not any(p.endswith("git/ref/tags/stable") for p in fake.calls)
        assert any(f"compare/{STABLE}..." in p for p in fake.calls)

    def test_the_replay_reads_the_attempt_as_it_stood_then(self):
        # Today the run is on attempt 2, a success started at 10:47 AM PT. At
        # 7:20 AM it was attempt 1, canceled.
        fake = oct27(attempts=[
            attempt("2026-10-27T12:20:01Z", "2026-10-27T13:26:58Z", "cancelled",
                    unit_jobs()),
            attempt("2026-10-27T17:47:48Z", "2026-10-27T17:54:27Z", "success",
                    [job(1, SHARD, "success", "2026-10-27T17:48:00Z",
                         "2026-10-27T17:53:00Z")]),
        ])
        seen = replay(fake, "2026-10-27T14:20:00Z")
        assert seen.step == channel_hold.RERUN
        assert seen.job == SHARD
        assert any(p.startswith(f"repos/{REPO}/actions/runs/{UNIT_RUN}/attempts/1/jobs")
                   for p in fake.calls)

    def test_the_hold_starts_at_the_oldest_first_parent_commit(self):
        # BRANCH is older than the merge and stable lacks it too, but it never
        # landed on main by itself.
        seen = replay(oct27(), "2026-10-27T14:20:00Z")
        assert "held since 5:19 AM PT" in seen.line
        assert "BRANCH" not in seen.body and "d9b1aff" not in seen.body


# ── what is needed next ─────────────────────────────────────────────────────


class TestNextStep:
    def test_attempt_two_still_canceled_needs_a_person(self):
        second = unit_jobs(shard_done="2026-10-27T14:04:30Z",
                           rollup_done="2026-10-27T14:05:00Z")
        for j in second:
            j["started_at"] = j["started_at"].replace("T12:", "T13:")
        fake = oct27(attempts=[
            attempt("2026-10-27T12:20:01Z", "2026-10-27T13:26:58Z", "cancelled",
                    unit_jobs()),
            attempt("2026-10-27T13:50:00Z", "2026-10-27T14:05:00Z", "cancelled",
                    second),
        ])
        seen = replay(fake, "2026-10-27T14:20:00Z")
        assert "next=person" in seen.line
        assert seen.step == channel_hold.PERSON
        assert "attempt 2" in seen.body

    def test_a_failed_test_needs_a_fix(self):
        fake = oct27(jobs=unit_jobs(shard_conclusion="failure",
                                    shard_step="failure"))
        seen = replay(fake, "2026-10-27T14:20:00Z")
        assert "next=fix" in seen.line
        assert seen.step == channel_hold.FIX
        assert "Console-change walk" in seen.body

    def test_a_failure_with_no_failed_step_lost_its_runner(self):
        fake = oct27(jobs=unit_jobs(shard_conclusion="failure",
                                    shard_step="in_progress"))
        seen = replay(fake, "2026-10-27T14:20:00Z")
        assert seen.step == channel_hold.RERUN
        assert "runner" in seen.body

    def test_a_hold_by_hand_needs_a_person_and_quotes_the_hold(self):
        fake = oct27(promotions=[
            promotion(40448885207, "2026-10-27T04:17:06Z",
                      "2026-10-27T04:21:45Z", PROMOTING_LINE),
            promotion(40512121894, "2026-10-27T13:27:00Z",
                      "2026-10-27T13:27:40Z", HELD_LINE),
        ])
        seen = replay(fake, "2026-10-27T14:20:00Z", hold=HOLD_TEXT)
        assert "next=person" in seen.line
        assert HOLD_TEXT in seen.body
        assert seen.title.endswith(": no failing job — paused by hand")
        assert TITLE_SHAPE.match(seen.title)

    def test_mirror_tests_failed_needs_a_fix(self):
        fake = oct27(jobs=[j for j in unit_jobs() if j["conclusion"] == "success"],
                     promotions=[
            promotion(40448885207, "2026-10-27T04:17:06Z",
                      "2026-10-27T04:21:45Z", PROMOTING_LINE),
            promotion(40512121894, "2026-10-27T13:27:00Z", "2026-10-27T13:27:40Z",
                      f"mirror-tests-failed: not promoting {MERGE}: agent-bureau's "
                      "mirror tests pass on stable and fail on this candidate."),
        ])
        seen = replay(fake, "2026-10-27T14:20:00Z")
        assert seen.step == channel_hold.FIX

    def test_mirror_check_blocked_needs_a_person(self):
        fake = oct27(promotions=[
            promotion(40448885207, "2026-10-27T04:17:06Z",
                      "2026-10-27T04:21:45Z", PROMOTING_LINE),
            promotion(40512121894, "2026-10-27T13:27:00Z", "2026-10-27T13:27:40Z",
                      f"mirror-check-blocked: not promoting {MERGE}: the "
                      "agent-bureau mirror check could not run."),
        ])
        seen = replay(fake, "2026-10-27T14:20:00Z")
        assert seen.step == channel_hold.PERSON

    def test_an_unread_run_list_needs_a_person_and_names_it(self):
        fake = oct27()
        fake.broken = ("actions/workflows/tests.yml",)
        seen = replay(fake, "2026-10-27T14:20:00Z")
        assert seen.step == channel_hold.PERSON
        assert "could not read" in seen.body

    def test_the_roll_up_is_passed_over_for_a_part_that_failed_with_it(self):
        # The same second, and the roll-up listed first: its own failure only
        # sums the shard's.
        jobs = unit_jobs(shard_done="2026-10-27T12:42:29Z")
        jobs.sort(key=lambda j: j["name"] != ROLLUP)
        fake = oct27(jobs=jobs)
        seen = replay(fake, "2026-10-27T14:20:00Z")
        assert seen.job == SHARD

    def test_the_roll_up_is_named_when_no_part_failed(self):
        fake = oct27(jobs=[j for j in unit_jobs() if j["name"] != SHARD])
        seen = replay(fake, "2026-10-27T14:20:00Z")
        assert seen.job == ROLLUP
        assert seen.step == channel_hold.FIX

    def test_a_run_still_going_past_two_hours_names_its_running_job(self):
        running = [job(9, SHARD, None, "2026-10-27T12:20:22Z", None,
                       status="in_progress")]
        fake = oct27(attempts=[attempt("2026-10-27T12:20:01Z",
                                       "2026-10-27T14:00:00Z", None, running,
                                       status="in_progress")])
        seen = replay(fake, "2026-10-27T14:25:00Z")
        assert seen.job == SHARD
        assert seen.step == channel_hold.RERUN
        assert "still running" in seen.body


# ── the 11-02 hold: a merge train, not a broken test (DRE-8778) ─────────────

NOV_STABLE = "4c0ffee000000000000000000000000000000000"
NOV_CHAIN = (   # first-parent, oldest first, as `main` took them
    ("5595f7d000000000000000000000000000000000", "2026-11-02T01:54:01Z", 41380000001),
    ("95d0c8c500000000000000000000000000000000", "2026-11-02T02:31:10Z", 41380000002),
    ("8a4a4aea00000000000000000000000000000000", "2026-11-02T03:02:44Z", 41380000003),
    ("875a8b4200000000000000000000000000000000", "2026-11-02T03:21:30Z", 41380000004),
    ("6bb1715900000000000000000000000000000000", "2026-11-02T03:39:10Z", 41384267119),
)
NOV_NEWEST, NOV_RUN = NOV_CHAIN[-1][0], NOV_CHAIN[-1][2]
#: The pass: 2 hours after the oldest commit `stable` lacks, 15 minutes after
#: the newest commit's `Pipeline Tests` run was created.
NOV_PASS = "2026-11-02T03:54:30Z"
NOV_CANCELLED_LINE = (
    "harness-cancelled-by-newer-push: not promoting "
    f"{NOV_CHAIN[-2][0]}: its Integration Harness run was cancelled by a newer "
    "push — a merge train, queued behind, not lost.")


def nov02_jobs(state):
    """A `Pipeline Tests` attempt's jobs: every part `state`, the roll-up
    waiting on them."""
    if state == "success":
        return [job(1000 + n, f"{ROLLUP} (part {n})", "success",
                    "2026-11-02T00:00:00Z", "2026-11-02T00:00:00Z")
                for n in range(1, 5)]
    started = {2: "2026-11-02T03:39:20Z", 4: "2026-11-02T03:39:31Z",
               1: "2026-11-02T03:39:44Z", 3: "2026-11-02T03:39:52Z"}
    return [job(2000 + n, f"{ROLLUP} (part {n})", None, started[n], None,
                status="in_progress") for n in range(1, 5)] + [
        job(2100, ROLLUP, None, None, None, status="queued")]


def nov02():
    """The 11-02 state: five commits `stable` lacks, the four older ones' unit
    suites green, the newest's 15 minutes in with every part running, and the
    newest decision `harness-cancelled-by-newer-push`."""
    commits = [commit(NOV_STABLE, "2026-11-01T22:00:00Z",
                      "1111111000000000000000000000000000000000")]
    runs, parent = [], NOV_STABLE
    for sha, date, run_id in NOV_CHAIN:
        commits.append(commit(sha, date, parent))
        parent = sha
        # A commit's run is created a few seconds after it lands.
        created = date[:-3] + f"{int(date[-3:-1]) + 4:02d}Z"
        newest = sha == NOV_NEWEST
        runs.append(run(run_id, "tests.yml", "Pipeline Tests", sha, created, [
            attempt(created, NOV_PASS if newest else created,
                    None if newest else "success",
                    nov02_jobs("in_progress" if newest else "success"),
                    status="in_progress" if newest else "completed")]))
    return FakeGitHub(
        stable=NOV_STABLE, main=NOV_NEWEST, commits=commits, runs=runs,
        promotions=[
            promotion(41370000001, "2026-11-01T22:40:00Z", "2026-11-01T22:44:00Z",
                      f"harness-passed-promoting: promoting stable to {NOV_STABLE} "
                      "— harness green, strictly ahead."),
            promotion(41383000002, "2026-11-02T03:30:00Z", "2026-11-02T03:30:40Z",
                      NOV_CANCELLED_LINE),
        ])


class TestTheSecondOfNovember:
    @pytest.mark.parametrize("how", ["replay", "live"])
    def test_a_young_run_under_an_old_hold_names_no_job(self, how):
        if how == "replay":
            seen = replay(nov02(), NOV_PASS)
        else:
            seen = channel_hold.watch(nov02(), REPO, interval=10,
                                      now=channel_hold.parse_at(NOV_PASS))
        assert "age=120m" in seen.line, seen.line
        assert seen.job == "no failing job — harness-cancelled-by-newer-push"
        assert seen.step == channel_hold.RERUN
        assert "Failing job: scripts unit tests" not in seen.body
        assert "re-running its failed jobs" not in seen.body
        assert "Stuck job:" not in seen.body
        assert "harness-cancelled-by-newer-push" in seen.body.splitlines()[1]
        assert seen.body.splitlines()[3] == "Waiting on stable: 5 commit(s), oldest 5595f7d"


# ── the 11-02 morning: a unit suite still running is a wait (DRE-8863) ──────

WAIT_STABLE = "7e57ab1e00000000000000000000000000000000"
WAIT_CHAIN = (   # first-parent, oldest first, as `main` took them
    ("305d940000000000000000000000000000000000", "2026-11-02T13:10:52Z", 41450000001),
    ("884986d200000000000000000000000000000000", "2026-11-02T13:41:20Z", 41450000002),
    ("e78f89b000000000000000000000000000000000", "2026-11-02T14:03:08Z", 41458314664),
)
WAIT_NEWEST, WAIT_RUN = WAIT_CHAIN[-1][0], WAIT_CHAIN[-1][2]
WAIT_CREATED = "2026-11-02T14:03:14Z"
#: The pass that raised the notice: 6:10 AM PT, seven minutes after the
#: newest commit's `Pipeline Tests` run was created.
WAIT_PASS = "2026-11-02T14:10:00Z"
WAIT_LINE = (
    f"unit-suite-not-finished: not promoting {WAIT_NEWEST}: its own unit suite "
    f"on main, Pipeline Tests run {WAIT_RUN}, has not finished "
    "(status=in_progress). stable waits for it.")


def nov02_morning():
    """The 11-02 6:10 AM state: three commits `stable` lacks, the oldest at
    5:10 AM PT, the two older ones' unit suites green, the newest's run
    created at 14:03:14Z with every job running, and the newest decision
    `unit-suite-not-finished`."""
    commits = [commit(WAIT_STABLE, "2026-11-02T12:30:00Z",
                      "2222222000000000000000000000000000000000")]
    runs, parent = [], WAIT_STABLE
    for sha, date, run_id in WAIT_CHAIN:
        commits.append(commit(sha, date, parent))
        parent = sha
        if sha == WAIT_NEWEST:
            jobs = [job(3000 + n, f"{ROLLUP} (part {n})", None,
                        f"2026-11-02T14:03:{20 + n:02d}Z", None,
                        status="in_progress") for n in range(1, 5)]
            runs.append(run(run_id, "tests.yml", "Pipeline Tests", sha,
                            WAIT_CREATED, [attempt(WAIT_CREATED, WAIT_PASS, None,
                                                   jobs, status="in_progress")]))
        else:
            created = date[:-3] + f"{int(date[-3:-1]) + 4:02d}Z"
            runs.append(run(run_id, "tests.yml", "Pipeline Tests", sha, created, [
                attempt(created, created, "success", nov02_jobs("success"))]))
    return FakeGitHub(
        stable=WAIT_STABLE, main=WAIT_NEWEST, commits=commits, runs=runs,
        promotions=[
            promotion(41440000001, "2026-11-02T12:40:00Z", "2026-11-02T12:44:00Z",
                      f"harness-passed-promoting: promoting stable to {WAIT_STABLE} "
                      "— harness green, strictly ahead."),
            promotion(41459893595, "2026-11-02T14:05:00Z", "2026-11-02T14:05:40Z",
                      WAIT_LINE),
        ])


class TestAUnitSuiteStillRunning:
    def test_the_six_ten_pass_says_wait_and_asks_nobody_to_act(self):
        seen = replay(nov02_morning(), WAIT_PASS)
        assert seen.step == channel_hold.WAIT
        assert seen.action == "morning-raise"
        assert "next=wait" in seen.line, seen.line
        assert seen.job == "no failing job — unit-suite-not-finished"
        lines = seen.body.splitlines()
        assert lines[1].startswith("Needs: a wait — "), lines[1]
        assert "nobody needs to act" in lines[1]
        assert "re-run" not in lines[1]
        assert WAIT_LINE.split(": ", 1)[1] in lines[1]
        assert lines[2] == "Held since: 2026-11-02T13:10:52Z (5:10 AM PT)"
        assert lines[3] == "Waiting on stable: 3 commit(s), oldest 305d940"

    def test_a_job_running_two_hours_is_still_a_stuck_job_to_re_run(self):
        # The same suite, its first part started at 14:03:21Z: two hours by
        # its own clock at 16:03:21Z.
        seen = replay(nov02_morning(), "2026-11-02T16:03:21Z")
        assert seen.step == channel_hold.RERUN
        assert seen.job == f"{ROLLUP} (part 1)"
        assert seen.body.startswith(f"Stuck job: {ROLLUP} (part 1) (")
        assert "next=re-run" in seen.line

    def test_one_second_short_of_two_hours_is_still_a_wait(self):
        seen = replay(nov02_morning(), "2026-11-02T16:03:20Z")
        assert seen.step == channel_hold.WAIT
        assert "Stuck job" not in seen.body

    def test_the_docstring_names_the_wait(self):
        doc = channel_hold.__doc__
        assert "* a wait —" in doc
        assert "next=<re-run|fix|person|wait>" in doc


# ── the reason, whole (DRE-8797) ────────────────────────────────────────────

BY_HAND_SHA = "83ee19d0c80f5b5a85b9cfb861ca7ff4e07a142f"
PROVEN_SHA = "798973bec1eeca4df993193568175d546da85318"
#: Promote Channel's by-hand `no-harness-stamp` refusal of run 41390182086,
#: verbatim. Clipped at 300 characters, it ended inside PROVEN_SHA.
BY_HAND_UNPROVEN = (
    f"refusing to promote {BY_HAND_SHA} by hand: its integration-harness "
    "status reports pending. The by-hand path promotes a commit the harness "
    "has ALREADY proved — from any run, not only the newest — and this one is "
    f"not proved. The last proven sha is {PROVEN_SHA}, where stable stands "
    f"now. Re-run the harness on {BY_HAND_SHA}, or dispatch again with force "
    "and a reason.")


def held(decision=None, unread=()):
    return channel_hold.Reading(held=True, carried="", chain=[], decision=decision,
                                unread=tuple(unread))


class TestTheReasonWhole:
    def test_the_by_hand_refusal_is_the_one_replayed(self):
        assert len(BY_HAND_UNPROVEN) == 446
        assert PROVEN_SHA not in BY_HAND_UNPROVEN[:300]

    def test_a_no_harness_stamp_reason_goes_in_whole(self):
        step, why = channel_hold.next_step(held(channel_hold.Decision(
            promote_channel.OUTCOME_UNPROVEN, BY_HAND_UNPROVEN, "")))
        assert step == channel_hold.RERUN
        assert PROVEN_SHA in why
        assert why.endswith("dispatch again with force and a reason.")
        assert BY_HAND_UNPROVEN in why

    def test_a_person_outcome_reason_goes_in_whole(self):
        reason = (f"not promoting {BY_HAND_SHA} by hand: could not establish "
                  "that it is on the trunk (compare='diverged'). " * 4).strip()
        assert len(reason) > 300
        assert promote_channel.OUTCOME_NOT_ON_TRUNK in channel_hold.PERSON_OUTCOMES
        step, why = channel_hold.next_step(held(channel_hold.Decision(
            promote_channel.OUTCOME_NOT_ON_TRUNK, reason, "")))
        assert step == channel_hold.PERSON
        assert reason in why

    def test_every_unread_entry_goes_in_whole(self):
        unread = [f"gh api repos/{REPO}/actions/workflows/tests.yml/runs?"
                  f"head_sha={sha} failed rc=1: HTTP 502"
                  for sha in (BY_HAND_SHA, PROVEN_SHA, MERGE, STABLE)]
        assert len("; ".join(unread)) > 400
        step, why = channel_hold.next_step(held(unread=unread))
        assert step == channel_hold.PERSON
        for entry in unread:
            assert entry in why

    def test_the_watch_body_carries_the_reason_whole(self):
        fake = oct27(jobs=[j for j in unit_jobs() if j["conclusion"] == "success"],
                     promotions=[
            promotion(40448885207, "2026-10-27T04:17:06Z",
                      "2026-10-27T04:21:45Z", PROMOTING_LINE),
            promotion(40512121894, "2026-10-27T13:27:00Z", "2026-10-27T13:27:40Z",
                      f"{promote_channel.OUTCOME_UNPROVEN}: {BY_HAND_UNPROVEN}"),
        ])
        seen = replay(fake, "2026-10-27T14:20:00Z")
        assert seen.action == "raise"
        lines = seen.body.splitlines()
        assert len(lines) == 4
        assert lines[1].startswith("Needs: a re-run — ")
        assert lines[1].endswith(BY_HAND_UNPROVEN)

    def test_next_step_clips_nothing(self):
        source = inspect.getsource(channel_hold.next_step)
        assert "[:300]" not in source
        assert "[:400]" not in source


class TestStuckJob:
    def running(self, started, created="2026-10-27T12:20:01Z"):
        return oct27(attempts=[attempt(created, "2026-10-27T14:00:00Z", None, [
            job(9, SHARD, None, started, None, status="in_progress")],
            status="in_progress")])

    def queued(self, created):
        fake = oct27(attempts=[attempt(created, created, None, [
            job(9, SHARD, None, None, None, status="queued")],
            status="queued")])
        fake.runs[0]["created_at"] = created
        return fake

    def test_a_job_running_two_hours_is_a_stuck_job_to_cancel_and_re_run(self):
        seen = replay(self.running("2026-10-27T12:20:22Z"), "2026-10-27T14:25:00Z")
        assert seen.job == SHARD
        assert seen.step == channel_hold.RERUN
        lines = seen.body.splitlines()
        assert lines[0] == f"Stuck job: {SHARD} ({SITE}/actions/runs/{UNIT_RUN})"
        assert lines[1].startswith("Needs: a re-run — ")
        assert "cancel" in lines[1] and "re-run" in lines[1]
        assert "failed jobs" not in seen.body
        assert "Failing job" not in seen.body

    def test_two_hours_runs_from_the_jobs_own_start(self):
        # The hold is 125 minutes old; the job started 119 minutes ago, then
        # 120.
        fake = self.running("2026-10-27T12:26:00Z")
        assert replay(fake, "2026-10-27T14:25:59Z").job != SHARD
        seen = replay(self.running("2026-10-27T12:26:00Z"), "2026-10-27T14:26:00Z")
        assert seen.job == SHARD
        assert seen.body.startswith("Stuck job: ")

    def test_a_job_not_started_two_hours_after_its_run_was_created_is_stuck(self):
        seen = replay(self.queued("2026-10-27T12:20:01Z"), "2026-10-27T14:25:00Z")
        assert seen.job == SHARD
        assert seen.step == channel_hold.RERUN
        assert seen.body.startswith(f"Stuck job: {SHARD} (")
        assert "has still not started" in seen.body
        assert "failed jobs" not in seen.body

    def test_a_job_not_started_under_two_hours_after_its_run_was_created_is_not(self):
        # The hold is 125 minutes old; the run was created 85 minutes ago.
        seen = replay(self.queued("2026-10-27T13:00:00Z"), "2026-10-27T14:25:00Z")
        assert seen.job == "no failing job — unit-suite-unproven"
        assert "Stuck job" not in seen.body
        assert SHARD not in seen.body

    def rerun_queued(self):
        """Attempt 1 canceled; attempt 2 began at 14:20, its job not started.
        The run itself was created at 12:20:01, two hours before."""
        first = attempt("2026-10-27T12:20:01Z", "2026-10-27T13:26:58Z",
                        "cancelled", unit_jobs())
        second = attempt("2026-10-27T14:20:00Z", "2026-10-27T14:20:00Z", None,
                         [job(9, SHARD, None, None, None, status="queued")],
                         status="queued")
        return oct27(attempts=[first, second])

    def watch_as(self, how, fake, at):
        if how == "replay":
            return replay(fake, at)
        fake.stable = STABLE   # live reads the tag as it stands, not as it stood
        return channel_hold.watch(fake, REPO, interval=10,
                                  now=channel_hold.parse_at(at))

    @pytest.mark.parametrize("how", ["replay", "live"])
    def test_a_re_run_not_started_is_timed_from_its_own_attempt(self, how):
        # The run was created 125 minutes ago; its attempt 2 began 5 minutes
        # ago.
        seen = self.watch_as(how, self.rerun_queued(), "2026-10-27T14:25:00Z")
        assert seen.job != SHARD, seen.body
        assert "Stuck job" not in seen.body
        assert "a re-run has not cleared it" not in seen.body

    @pytest.mark.parametrize("how", ["replay", "live"])
    def test_a_re_run_not_started_two_hours_after_its_attempt_began_is_stuck(self, how):
        seen = self.watch_as(how, self.rerun_queued(), "2026-10-27T16:20:00Z")
        assert seen.job == SHARD
        assert seen.body.startswith(f"Stuck job: {SHARD} (")
        assert seen.step == channel_hold.PERSON

    @pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
    def test_a_concluded_job_is_still_a_failing_job(self, conclusion):
        fake = oct27(jobs=unit_jobs(shard_conclusion=conclusion,
                                    shard_step=conclusion))
        seen = replay(fake, "2026-10-27T14:20:00Z")
        assert seen.job == SHARD
        assert seen.body.splitlines()[0] == (
            f"Failing job: {SHARD} ({SITE}/actions/runs/{UNIT_RUN})")

    def test_the_docstring_counts_the_jobs_own_two_hours(self):
        doc = channel_hold.__doc__
        section = " ".join(doc[doc.index("THE DIAGNOSIS"):doc.index("WHEN IT RAISES")].split())
        assert "only once the job itself has run 2 hours" in section
        assert "never from the hold's age" in section
        assert "past 2 hours of hold" not in section


class TestEveryOutcome:
    def test_every_outcome_maps_to_one_next_step_or_is_not_a_hold(self):
        outcomes = {getattr(promote_channel, name) for name in dir(promote_channel)
                    if name.startswith("OUTCOME_")}
        assert len(outcomes) >= 19
        for outcome in outcomes:
            mapped = outcome in channel_hold.OUTCOME_NEXT
            not_hold = outcome in channel_hold.NOT_A_HOLD
            assert mapped != not_hold, outcome
            if mapped:
                assert channel_hold.OUTCOME_NEXT[outcome] in channel_hold.NEXT_STEPS
        assert set(channel_hold.OUTCOME_NEXT) | set(channel_hold.NOT_A_HOLD) == outcomes

    def test_the_declared_not_a_hold_outcomes(self):
        assert set(channel_hold.NOT_A_HOLD) == {
            *promote_channel.PROMOTING_OUTCOMES,
            promote_channel.OUTCOME_NOT_MAIN, promote_channel.OUTCOME_NOT_AHEAD}

    def test_an_unfinished_unit_suite_is_a_wait_and_nothing_else_moved(self):
        pc, ch = promote_channel, channel_hold
        assert ch.WAIT == "a wait"
        assert ch.OUTCOME_NEXT == {
            pc.OUTCOME_CANCELLED: ch.RERUN,
            pc.OUTCOME_FAILED: ch.FIX,
            pc.OUTCOME_HELD: ch.PERSON,
            pc.OUTCOME_BLOCKED: ch.RERUN,
            pc.OUTCOME_QUEUE_STALL: ch.RERUN,
            pc.OUTCOME_UNPROVEN: ch.RERUN,
            pc.OUTCOME_FORCE_NEEDS_REASON: ch.PERSON,
            pc.OUTCOME_FORCE_NOT_OPERATOR: ch.PERSON,
            pc.OUTCOME_NOT_ON_TRUNK: ch.PERSON,
            pc.OUTCOME_MIRROR_FAILED: ch.FIX,
            pc.OUTCOME_MIRROR_BLOCKED: ch.PERSON,
            pc.OUTCOME_UNIT_RED: ch.FIX,
            pc.OUTCOME_UNIT_NOT_FINISHED: ch.WAIT,
            pc.OUTCOME_UNIT_UNPROVEN: ch.RERUN,
        }

    def test_the_contract_constants(self):
        assert channel_hold.NEXT_STEPS == ("a re-run", "a fix", "a person", "a wait")
        assert channel_hold.TAG == "channel-hold:"
        assert channel_hold.NOTICE_KEY == "channel_hold:stable"
        assert pipeline_notice.KEY_PATTERN.match(channel_hold.NOTICE_KEY)
        assert channel_hold.ALERT_AFTER_MINUTES == 120
        assert channel_hold.MORNING_START == "06:00"
        assert channel_hold.MORNING_END == "06:29"
        assert channel_hold.MORNING_FLOOR_MINUTES == 45

    @pytest.mark.parametrize("workflow,name", [
        ("tests.yml", "Pipeline Tests"),
        ("harness.yml", "Integration Harness"),
        ("promote-channel.yml", "Promote Channel"),
    ])
    def test_the_workflow_files_it_reads_carry_the_names_it_means(self, workflow, name):
        text = (ROOT / ".github" / "workflows" / workflow).read_text()
        assert re.search(rf"^name: {re.escape(name)}$", text, re.M)
        assert workflow in (channel_hold.UNIT_SUITE_FILE, channel_hold.HARNESS_FILE,
                            channel_hold.PROMOTE_FILE)


# ── the raise windows, against a stubbed door ───────────────────────────────

S0 = "a0" * 20
M1 = "b1" * 20


def live(now, age_minutes, *, held=True):
    """A one-merge hold `age_minutes` old at `now`, its suite's one job
    canceled on attempt 1 and the decision `unit-suite-unproven`."""
    landed = now - timedelta(minutes=age_minutes)
    iso = lambda when: when.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    done = landed + timedelta(minutes=10)
    return FakeGitHub(
        stable=S0, main=M1 if held else S0,
        commits=[commit(S0, iso(now - timedelta(days=1)), "c" * 40),
                 commit(M1, iso(landed), S0)],
        runs=[run(41, "tests.yml", "Pipeline Tests", M1, iso(landed), [
            attempt(iso(landed), iso(done), "cancelled", [
                job(410, "scripts unit tests (part 2)", "cancelled",
                    iso(landed), iso(done), ("Run tests", "cancelled"))])])],
        promotions=[promotion(42, iso(done), iso(done + timedelta(minutes=1)),
                              f"unit-suite-unproven: not promoting {M1}: run 41 "
                              "reads status='completed', conclusion='cancelled'.")],
    )


NOON_PT = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)      # 12:00 PST
SIX_OH_FIVE_PST = datetime(2026, 11, 2, 14, 5, tzinfo=timezone.utc)
SIX_OH_FIVE_PDT = datetime(2026, 10, 27, 13, 5, tzinfo=timezone.utc)


class TestRaiseWindows:
    @pytest.mark.parametrize("age,raises", [
        (120, True), (130, False), (185, True), (195, False),
        (119, False), (240, True), (249, True), (250, False),
    ])
    def test_two_hours_then_once_an_hour(self, age, raises):
        code, out, _err, door = run_main(
            live(NOON_PT, age), "--interval-minutes", "10", "--apply", now=NOON_PT)
        assert code == 0
        assert door.raise_notice.called is raises, out
        door.resolve_notice.assert_not_called()
        assert out.splitlines()[0].endswith(" — raise" if raises else " — quiet")

    @pytest.mark.parametrize("now", [SIX_OH_FIVE_PST, SIX_OH_FIVE_PDT])
    @pytest.mark.parametrize("age,raises", [(50, True), (30, False), (45, True)])
    def test_before_the_briefing(self, now, age, raises):
        code, out, _err, door = run_main(
            live(now, age), "--interval-minutes", "10", "--apply", now=now)
        assert code == 0
        assert door.raise_notice.called is raises, out
        if raises:
            assert out.splitlines()[0].endswith(" — morning-raise")

    def test_the_morning_window_ends_at_six_twenty_nine(self):
        at = datetime(2026, 11, 2, 14, 30, tzinfo=timezone.utc)   # 6:30 PST
        _code, out, _err, door = run_main(
            live(at, 50), "--interval-minutes", "10", "--apply", now=at)
        door.raise_notice.assert_not_called()
        assert out.strip().endswith(" — quiet")

    def test_the_raise_is_the_contract_notice(self):
        _code, out, _err, door = run_main(
            live(NOON_PT, 120), "--interval-minutes", "10", "--apply", now=NOON_PT)
        door.raise_notice.assert_called_once()
        args, kwargs = door.raise_notice.call_args
        assert args == (channel_hold.NOTICE_KEY,)
        assert kwargs["severity"] == "WARN"
        assert TITLE_SHAPE.match(kwargs["title"])
        assert kwargs["title"] == ("stable held since 10:00 AM PT — needs a "
                                   "re-run: scripts unit tests (part 2)")
        assert kwargs["body"].splitlines()[0].startswith(
            "Failing job: scripts unit tests (part 2) (")
        fallback = kwargs["fallback"]
        assert fallback.title == "bureau-pipeline: the stable channel is held"
        assert fallback.lane in pipeline_notice.FALLBACK_LANES

    def test_a_pass_with_no_hold_sends_a_resolve(self):
        code, out, _err, door = run_main(
            live(NOON_PT, 120, held=False), "--interval-minutes", "10", "--apply",
            now=NOON_PT)
        assert code == 0
        assert out.strip() == f"channel-hold: none — stable carries main {S0[:7]}"
        door.resolve_notice.assert_called_once_with(channel_hold.NOTICE_KEY)
        door.raise_notice.assert_not_called()

    def test_a_lost_raise_exits_one(self):
        code, _out, _err, door = run_main(
            live(NOON_PT, 120), "--interval-minutes", "10", "--apply",
            now=NOON_PT, door=stub_door("lost"))
        assert code == 1
        door.raise_notice.assert_called_once()

    def test_a_fallback_card_exits_zero(self):
        code, *_ = run_main(live(NOON_PT, 120), "--interval-minutes", "10",
                            "--apply", now=NOON_PT, door=stub_door("fallback-card"))
        assert code == 0


class TestTheDoorIsNotCalled:
    def test_without_apply(self):
        code, out, _err, door = run_main(
            live(NOON_PT, 120), "--interval-minutes", "10", now=NOON_PT)
        assert code == 0
        assert out.strip().endswith(" — would-raise")
        door.raise_notice.assert_not_called()
        door.resolve_notice.assert_not_called()

    def test_without_apply_and_no_hold(self):
        _code, _out, _err, door = run_main(
            live(NOON_PT, 120, held=False), "--interval-minutes", "10", now=NOON_PT)
        door.resolve_notice.assert_not_called()

    def test_with_at_even_with_apply(self):
        code, out, _err, door = run_main(
            oct27(), "--interval-minutes", "10", "--apply",
            "--at", "2026-10-27T14:20:00Z")
        assert code == 0
        assert out.strip().endswith(" — would-raise")
        door.raise_notice.assert_not_called()
        door.resolve_notice.assert_not_called()

    def test_with_at_and_no_hold(self):
        _code, out, _err, door = run_main(
            oct27(), "--interval-minutes", "10", "--apply",
            "--at", "2026-10-27T12:00:00Z")
        assert out.strip() == f"channel-hold: none — stable carries main {STABLE[:7]}"
        door.resolve_notice.assert_not_called()


class TestUnknown:
    @pytest.mark.parametrize("broken,named", [
        ("git/ref/tags/stable", "stable"),
        ("git/ref/heads/main", "main"),
        ("/compare/", "stable...main"),
    ])
    def test_a_failed_read_raises_nothing_and_resolves_nothing(self, broken, named):
        fake = live(NOON_PT, 120)
        fake.broken = (broken,)
        code, out, _err, door = run_main(
            fake, "--interval-minutes", "10", "--apply", now=NOON_PT)
        assert code == 0
        line = out.strip()
        assert line.startswith("channel-hold: unknown ")
        assert named in line
        assert len(out.strip().splitlines()) == 1
        door.raise_notice.assert_not_called()
        door.resolve_notice.assert_not_called()

    def test_a_failed_read_with_no_hold_resolves_nothing(self):
        fake = live(NOON_PT, 0, held=False)
        fake.broken = ("/compare/",)
        _code, out, _err, door = run_main(
            fake, "--interval-minutes", "10", "--apply", now=NOON_PT)
        assert out.startswith("channel-hold: unknown ")
        door.resolve_notice.assert_not_called()

    def test_a_replay_with_no_promoting_run_is_unknown(self):
        fake = oct27(promotions=[promotion(
            40512121894, "2026-10-27T13:27:00Z", "2026-10-27T13:27:40Z",
            UNPROVEN_LINE)])
        code, out, _err, _door = run_main(
            fake, "--interval-minutes", "10", "--at", "2026-10-27T14:20:00Z")
        assert code == 0
        assert out.startswith("channel-hold: unknown ")
