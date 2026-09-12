"""A train run that no-ops on spacing or the window re-arms itself (DRE-3559).

THE BUG. The train's triggers are CI completing on `main`, the daily 07:00 PT
schedule and a hand dispatch. A run that no-ops on the spacing names the exact
minute the next release may be cut — and then nothing exists to cut it. On
2026-09-10 the One River cutover sat fifteen merges behind until the next
morning's cron; on 2026-09-12 it happened four times in an afternoon: runs
34722349258 (15:23 PT), 34722553947 (15:25 PT), 34722919705 (15:31 PT) and
34723138488 (15:35:52 PT — eight seconds before its own named minute) each
said "the next release may be cut at 15:36 PT" and the release waited for the
CEO's hand dispatch at 15:38:58 PT.

THE FIX, pinned here:

  * `decide()` carries a `re_arm_at` on a spacing no-op (the minute it names,
    rounded UP so a run that arrives on it is never early) and on a window
    no-op (the window's next open, on the America/Los_Angeles clock, DST and
    all). A hold, the brake, `auto: false`, "reads current", a still-checking
    commit, a refusal and a release re-arm nothing — a person or the next CI
    completion owns those.
  * The plan dispatches the CALLER's own stub once, `-f not_before=<UTC>`;
    the re-armed run waits (bounded by the spacing + 2 minutes) and then takes
    the ordinary decision — nothing bypassed.
  * Two no-ops inside one spacing window produce ONE waiting re-arm: the plan
    looks for an in-flight run already armed for that minute before it
    dispatches, and the wait job's concurrency group is keyed on the minute.
  * An old stub — no `not_before` input, or `actions: read` — degrades to a
    `re-arm skipped: …` clause on the line. It never fails the run.

Run: python3 -m pytest tests/test_release_train_re_arm.py -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_train  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "release-train.yml"
STANDARD = ROOT / "standards" / "release-train.md"
UTC = timezone.utc
REPO = "dreadnought-foundry/demo"
STUB_PATH = ".github/workflows/release-train.yml"


def surface(**over):
    data = {
        "tag_series": ["console/v*"],
        "paths": ["console/"],
        "script": "infra/release-console.sh",
        "rollback": "make rollback-console VERSION=<tag>",
        "spacing_minutes": 30,
        "window": "07:00-21:00 PT",
        "auto": True,
        "identity": "bureau-console-release",
        "record": "tag",
    }
    data.update(over)
    return release_train.surface("console", data)


def pt(year, month, day, hour, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second,
                    tzinfo=release_train.PT)


def green():
    return release_train.read_checks(
        [{"name": "ci", "status": "completed", "conclusion": "success"}])


def pending():
    return release_train.read_checks(
        [{"name": "ci", "status": "in_progress", "conclusion": None}])


def red():
    return release_train.read_checks(
        [{"name": "ci", "status": "completed", "conclusion": "failure"}])


# --------------------------------------------------------------------------
# The decision carries the re-arm.
# --------------------------------------------------------------------------

def test_a_spacing_no_op_re_arms_for_the_minute_it_names():
    """The 2026-09-12 afternoon: the console released at 15:06 PT, and the
    15:23 PT run no-op'd on the 30-minute spacing naming 15:36 PT."""
    decision = release_train.decide(
        surface(), pt(2026, 9, 12, 15, 23), pt(2026, 9, 12, 15, 6),
        "behind", green(), None)
    assert decision.code == "spacing"
    assert decision.re_arm_at == pt(2026, 9, 12, 15, 36)
    assert decision.re_arm_at.utcoffset() is not None
    assert "may be cut at 15:36 PT" in decision.reason


def test_the_named_minute_rounds_up_so_the_re_armed_run_is_never_early():
    """Run 34723138488 at 15:35:52 PT named 15:36 PT — but a tag cut at
    15:06:40 lets the next one be cut at 15:36:40, and a run that woke at
    15:36:00 would no-op on the spacing all over again. The named minute and
    the re-arm are the first WHOLE minute at which the spacing has elapsed."""
    tag_at = pt(2026, 9, 12, 15, 6, 40)
    decision = release_train.decide(
        surface(), pt(2026, 9, 12, 15, 35, 52), tag_at, "behind", green(), None)
    assert decision.code == "spacing"
    assert decision.re_arm_at == pt(2026, 9, 12, 15, 37)
    assert "may be cut at 15:37 PT" in decision.reason
    woke = release_train.decide(
        surface(), decision.re_arm_at, tag_at, "behind", green(), None)
    assert woke.act == release_train.RELEASE, woke.reason


def test_a_spacing_minute_past_the_window_close_re_arms_for_the_next_open():
    """Released 20:45 PT with a 30-minute spacing: 21:15 PT is outside
    07:00-21:00 PT, so re-arming for it would only buy a window no-op. The
    re-arm is the first minute a release may ACTUALLY be cut."""
    decision = release_train.decide(
        surface(), pt(2026, 9, 12, 20, 50), pt(2026, 9, 12, 20, 45),
        "behind", green(), None)
    assert decision.code == "spacing"
    assert decision.re_arm_at == pt(2026, 9, 13, 7, 0)


def test_a_window_no_op_re_arms_for_the_window_open():
    late = release_train.decide(
        surface(), pt(2026, 7, 15, 23, 0), None, "behind", green(), None)
    assert late.code == "window"
    assert late.re_arm_at == pt(2026, 7, 16, 7, 0)

    early = release_train.decide(
        surface(), pt(2026, 7, 15, 6, 0), None, "behind", green(), None)
    assert early.code == "window"
    assert early.re_arm_at == pt(2026, 7, 15, 7, 0)


@pytest.mark.parametrize("now, opens_utc", [
    # Autumn: 23:00 PDT on Oct 31; the clocks fall back at 02:00, so 07:00 PT
    # on Nov 1 is PST — 15:00 UTC, not the 14:00 UTC a fixed offset would say.
    (pt(2026, 10, 31, 23, 0), datetime(2026, 11, 1, 15, 0, tzinfo=UTC)),
    # Spring: 23:00 PST on Mar 7; the clocks spring forward at 02:00, so 07:00
    # PT on Mar 8 is PDT — 14:00 UTC, not 15:00.
    (pt(2026, 3, 7, 23, 0), datetime(2026, 3, 8, 14, 0, tzinfo=UTC)),
])
def test_the_window_open_is_read_on_the_pacific_clock_across_dst(now, opens_utc):
    decision = release_train.decide(surface(), now, None, "behind", green(), None)
    assert decision.code == "window"
    assert decision.re_arm_at == opens_utc
    assert decision.re_arm_at.astimezone(release_train.PT).hour == 7


def test_a_window_that_wraps_midnight_re_arms_for_its_evening_open():
    decision = release_train.decide(
        surface(window="21:00-07:00 PT"), pt(2026, 7, 15, 12, 0), None,
        "behind", green(), None)
    assert decision.code == "window"
    assert decision.re_arm_at == pt(2026, 7, 15, 21, 0)


def _no_re_arm_cases():
    tag_at = pt(2026, 9, 12, 15, 6)
    now = pt(2026, 9, 12, 15, 23)
    late = pt(2026, 9, 12, 23, 0)
    return [
        ("brake", dict(s=surface(), now=now, tag_at=tag_at, lag="behind",
                       ci=green(), brake="2026-09-12")),
        ("auto-false", dict(s=surface(auto=False), now=now, tag_at=tag_at,
                            lag="behind", ci=green(), brake=None)),
        ("auto-false-late", dict(s=surface(auto=False), now=late, tag_at=None,
                                 lag="behind", ci=green(), brake=None)),
        ("current", dict(s=surface(), now=now, tag_at=tag_at, lag="current",
                         ci=green(), brake=None)),
        ("no-script", dict(s=surface(script=None, auto=False), now=now,
                           tag_at=tag_at, lag="behind", ci=green(), brake=None)),
        ("ci-pending", dict(s=surface(), now=pt(2026, 9, 12, 16, 0),
                            tag_at=tag_at, lag="behind", ci=pending(),
                            brake=None)),
        ("ci-red", dict(s=surface(), now=pt(2026, 9, 12, 16, 0), tag_at=tag_at,
                        lag="behind", ci=red(), brake=None)),
        ("release", dict(s=surface(), now=pt(2026, 9, 12, 16, 0),
                         tag_at=tag_at, lag="behind", ci=green(), brake=None)),
    ]


@pytest.mark.parametrize("case, kw", _no_re_arm_cases(),
                         ids=[c for c, _ in _no_re_arm_cases()])
def test_what_a_person_or_the_next_ci_completion_owns_re_arms_nothing(case, kw):
    decision = release_train.decide(kw["s"], kw["now"], kw["tag_at"], kw["lag"],
                                    kw["ci"], kw["brake"])
    assert decision.re_arm_at is None, (case, decision)


def test_a_hand_dispatch_of_an_auto_false_surface_on_the_spacing_re_arms_nothing():
    """The re-armed run is an ordinary run, which skips an `auto: false`
    surface — so re-arming for one would buy a no-op. A person is watching a
    hand dispatch; they own it."""
    decision = release_train.decide(
        surface(auto=False), pt(2026, 9, 12, 15, 23), pt(2026, 9, 12, 15, 6),
        "behind", green(), None, dispatched=True)
    assert decision.code == "spacing"
    assert decision.re_arm_at is None


def test_a_channel_surface_re_arms_nothing():
    channel = release_train.surface("pipeline-channel", {
        "tag_series": ["stable"], "paths": [], "script": None,
        "rollback": None, "spacing_minutes": 0, "window": "always",
        "auto": False, "identity": "promote-channel", "record": "channel"})
    decision = release_train.decide(
        channel, pt(2026, 9, 12, 15, 23), None, "behind",
        release_train.ASSUMED_GREEN, None)
    assert decision.re_arm_at is None


# --------------------------------------------------------------------------
# The plan: one re-arm, for the earliest minute, inside the wait bound.
# --------------------------------------------------------------------------

def _planned(*decisions):
    return [(surface(), d) for d in decisions]


def _spacing_no_op(now, tag_at):
    return release_train.decide(surface(), now, tag_at, "behind", green(), None)


def test_the_plan_re_arms_once_for_the_earliest_minute():
    now = pt(2026, 9, 12, 15, 23)
    a = _spacing_no_op(now, pt(2026, 9, 12, 15, 6))    # 15:36
    b = _spacing_no_op(now, pt(2026, 9, 12, 15, 20))   # 15:50
    held = release_train.decide(surface(), now, None, "behind", green(), "x")
    outcome = release_train.re_arm_plan(_planned(b, a, held), now=now,
                                        bound_minutes=32)
    assert outcome.dispatch is True
    assert outcome.at == pt(2026, 9, 12, 15, 36)
    assert "re-armed for 15:36 PT" in outcome.note


def test_nothing_to_re_arm_is_no_dispatch_and_no_note():
    now = pt(2026, 9, 12, 16, 0)
    current = release_train.decide(surface(), now, None, "current", green(), None)
    outcome = release_train.re_arm_plan(_planned(current), now=now,
                                        bound_minutes=32)
    assert outcome.at is None and outcome.dispatch is False
    assert outcome.note == ""


def test_a_re_arm_beyond_the_wait_bound_is_not_dispatched_and_says_why():
    """23:00 PT's window no-op names 07:00 PT — eight hours away. A re-armed
    run would hold a runner the whole time, so it is not dispatched; the line
    says what wakes the train instead."""
    now = pt(2026, 7, 15, 23, 0)
    late = release_train.decide(surface(), now, None, "behind", green(), None)
    outcome = release_train.re_arm_plan(_planned(late), now=now, bound_minutes=32)
    assert outcome.dispatch is False
    assert outcome.at == pt(2026, 7, 16, 7, 0)
    assert outcome.note.startswith("not re-armed:")
    assert "07:00 PT" in outcome.note and "32" in outcome.note


def test_the_wait_bound_is_the_largest_spacing_plus_two_minutes():
    data = {"surfaces": {
        "console": {"spacing_minutes": 30, "auto": True, "record": "tag"},
        "website": {"spacing_minutes": 45, "auto": False, "record": "tag"},
        "relay": {"spacing_minutes": 20, "auto": True, "record": "tag"},
    }}
    assert release_train.wait_bound_minutes(data) == 32
    assert release_train.wait_bound_minutes({"surfaces": {}}) == 2
    huge = {"surfaces": {"x": {"spacing_minutes": 600, "auto": True,
                               "record": "tag"}}}
    assert (release_train.wait_bound_minutes(huge)
            == release_train.WAIT_CEILING_MINUTES)


def test_an_already_armed_run_is_found_by_the_minute_in_its_wait_job_name():
    iso = "2026-09-12T22:36:00Z"
    runs = [
        {"status": "completed", "html_url": "u0",
         "jobs": [f"call / {release_train.WAIT_JOB_PREFIX} {iso}"]},
        {"status": "in_progress", "html_url": "u1",
         "jobs": [f"call / {release_train.WAIT_JOB_PREFIX} 2026-09-12T22:50:00Z"]},
        {"status": "queued", "html_url": "u2",
         "jobs": [f"call / {release_train.WAIT_JOB_PREFIX} {iso}",
                  "call / Plan the surfaces"]},
    ]
    assert release_train.armed_run(runs, iso) == "u2"
    assert release_train.armed_run(runs[:2], iso) is None


def test_the_asking_run_is_never_the_run_already_waiting():
    """A re-armed run that has to re-arm again is still in flight, and its
    own wait job carries a minute. It must not read itself as the answer."""
    iso = "2026-09-12T22:36:00Z"
    me = {"id": 34724561814, "status": "in_progress", "html_url": "me",
          "jobs": [f"call / {release_train.WAIT_JOB_PREFIX} {iso}"]}
    assert release_train.armed_run([me], iso, own_run_id="34724561814") is None
    assert release_train.armed_run([me], iso) == "me"


def test_the_stub_is_read_for_its_not_before_input():
    assert release_train.stub_declares_not_before(NEW_STUB)
    assert not release_train.stub_declares_not_before(OLD_STUB)


def test_the_callers_stub_is_named_by_the_workflow_ref():
    ref = ("dreadnought-foundry/agent-bureau/.github/workflows/"
           "release-train.yml@refs/heads/main")
    assert (release_train.caller_workflow_path(ref)
            == ".github/workflows/release-train.yml")
    assert release_train.caller_workflow_path("") == release_train.TRAIN_WORKFLOW


@pytest.mark.parametrize("detail, says", [
    ("could not create workflow dispatch event: HTTP 403: Resource not "
     "accessible by integration", "caller stub lacks actions: write"),
    ('could not create workflow dispatch event: HTTP 422: Unexpected inputs '
     'provided: ["not_before"]', "caller stub lacks not_before"),
    ("HTTP 502: Bad Gateway", "HTTP 502: Bad Gateway"),
])
def test_a_dispatch_the_stub_refuses_is_named(detail, says):
    note = release_train.re_arm_failure(detail)
    assert note.startswith("re-arm skipped: ")
    assert says in note


# --------------------------------------------------------------------------
# The re-armed run's wait — bounded, and never a bypass.
# --------------------------------------------------------------------------

def test_the_wait_is_until_the_named_minute():
    now = pt(2026, 9, 12, 15, 26)
    seconds, line = release_train.wait_seconds(
        "2026-09-12T22:36:00Z", now, bound_minutes=32)
    assert seconds == 10 * 60
    assert "15:36 PT" in line


def test_the_wait_never_exceeds_the_bound():
    now = pt(2026, 9, 12, 15, 0)
    seconds, line = release_train.wait_seconds(
        "2026-09-12T22:36:00Z", now, bound_minutes=32)
    assert seconds == 32 * 60
    assert "32" in line


def test_a_minute_already_past_goes_straight_on():
    seconds, line = release_train.wait_seconds(
        "2026-09-12T22:36:00Z", pt(2026, 9, 12, 15, 40), bound_minutes=32)
    assert seconds == 0
    assert "straight on" in line


@pytest.mark.parametrize("raw", ["", "soon", "2026-09-12T22:36:00",
                                 "2026-13-40T99:00:00Z"])
def test_an_unreadable_not_before_goes_straight_on(raw):
    seconds, line = release_train.wait_seconds(raw, pt(2026, 9, 12, 15, 0),
                                               bound_minutes=32)
    assert seconds == 0
    assert "straight on" in line


# --------------------------------------------------------------------------
# End to end over a real caller checkout: the plan CLI, with the dispatch
# and the Actions listing as seams.
# --------------------------------------------------------------------------

NEW_STUB = """name: Release Train
on:
  workflow_run:
    workflows: ["CI"]
    types: [completed]
    branches: [main]
  workflow_dispatch:
    inputs:
      surface:
        type: string
        required: false
        default: ""
      not_before:
        type: string
        required: false
        default: ""
permissions:
  actions: write
jobs:
  call:
    uses: dreadnought-foundry/bureau-pipeline/.github/workflows/release-train.yml@stable
"""

# agent-bureau's stub as it stood on 2026-09-12: no `not_before`, and
# `actions: read`.
OLD_STUB = """name: Release train
on:
  workflow_run:
    workflows: ["CI"]
    types: [completed]
    branches: [main]
  workflow_dispatch:
    inputs:
      surface:
        description: "Run this one surface by hand"
        required: false
        default: ""
permissions:
  actions: read
jobs:
  call:
    uses: dreadnought-foundry/bureau-pipeline/.github/workflows/release-train.yml@stable
"""


def _git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True,
                          env={**os.environ, **(env or {})}).stdout.strip()


def _caller(tmp_path, *, stub=NEW_STUB, tag_at="2026-09-12T15:06:00-07:00"):
    repo = tmp_path / "caller"
    for d in ("demo", "infra", ".github/bureau", ".github/workflows"):
        (repo / d).mkdir(parents=True, exist_ok=True)
    (repo / "demo" / "app.txt").write_text("v0\n")
    (repo / "infra" / "release-demo.sh").write_text("#!/usr/bin/env bash\n")
    (repo / STUB_PATH).write_text(stub)
    (repo / ".github" / "bureau" / "release.json").write_text(json.dumps({
        "surfaces": {"demo": {
            "tag_series": ["demo/v*"], "paths": ["demo/"],
            "script": "infra/release-demo.sh",
            "rollback": "make rollback-demo VERSION=<tag>",
            "spacing_minutes": 30, "window": "07:00-21:00 PT", "auto": True,
            "identity": "demo-release", "record": "tag"}}}) + "\n")
    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "train@example.com")
    _git(repo, "config", "user.name", "Release Train")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the caller, as it stands")
    _git(repo, "tag", "-a", "demo/v0", "-m", "released",
         env={"GIT_COMMITTER_DATE": tag_at})
    (repo / "demo" / "app.txt").write_text("v1\n")
    _git(repo, "commit", "-qam", "a change the console is behind on")
    return repo


class _Actions:
    """The two GitHub seams: the in-flight listing and the dispatch. A
    dispatch lands in the listing the way a real one does — as a queued run
    whose wait job carries the minute in its name."""

    def __init__(self, dispatch_error=None, unreadable=False):
        self.dispatched = []
        self.dispatch_error = dispatch_error
        self.unreadable = unreadable

    def runs(self, repo, workflow):
        if self.unreadable:
            raise RuntimeError("gh could not read the workflow runs: HTTP 500")
        return [{"status": "queued",
                 "html_url": f"https://github.com/{repo}/actions/runs/{i}",
                 "jobs": [f"call / {release_train.WAIT_JOB_PREFIX} {nb}"]}
                for i, (_, _, _, nb) in enumerate(self.dispatched, start=1)]

    def dispatch(self, repo, workflow, ref, not_before):
        if self.dispatch_error:
            raise RuntimeError(self.dispatch_error)
        self.dispatched.append((repo, workflow, ref, not_before))


def _plan_cli(repo, monkeypatch, actions, now, *, re_arm=True):
    monkeypatch.setattr(release_train, "_now", lambda: now)
    monkeypatch.setattr(release_train, "fetch_armed_runs", actions.runs)
    monkeypatch.setattr(release_train, "dispatch_re_arm", actions.dispatch)

    def no_checks(repo_, sha):
        raise AssertionError("a spacing no-op must not read a check run")
    monkeypatch.setattr(release_train, "fetch_checks", no_checks)
    monkeypatch.setenv("GITHUB_OUTPUT", str(repo.parent / "github_output"))
    argv = ["--repo", REPO, "--repo-root", str(repo),
            "--file", str(repo / ".github" / "bureau" / "release.json"),
            "plan", "--head", _git(repo, "rev-parse", "HEAD")]
    if re_arm:
        argv += ["--re-arm", "--workflow", STUB_PATH, "--default-branch", "main"]
    return release_train.main(argv)


def _line(out):
    return next(l for l in out.splitlines()
                if l.startswith(f"{release_train.TAG}: no-op {REPO} demo"))


def test_a_spacing_no_op_dispatches_exactly_one_re_arm(tmp_path, monkeypatch,
                                                      capsys):
    repo = _caller(tmp_path)
    actions = _Actions()
    code = _plan_cli(repo, monkeypatch, actions, pt(2026, 9, 12, 15, 23))
    assert code == 0
    assert actions.dispatched == [
        (REPO, STUB_PATH, "main", "2026-09-12T22:36:00Z")]
    line = _line(capsys.readouterr().out)
    assert "may be cut at 15:36 PT — re-armed for 15:36 PT" in line
    # The console's release row (DRE-3336) still reads it as the spacing hold.
    assert "and its spacing is" in line
    assert "re_arm=2026-09-12T22:36:00Z" in (tmp_path / "github_output").read_text()


def test_two_no_ops_inside_one_spacing_window_produce_one_re_arm(tmp_path,
                                                                 monkeypatch,
                                                                 capsys):
    """15:23 PT and 15:31 PT — two of the four 2026-09-12 no-ops. The first
    dispatches; the second finds it waiting and dispatches nothing."""
    repo = _caller(tmp_path)
    actions = _Actions()
    assert _plan_cli(repo, monkeypatch, actions, pt(2026, 9, 12, 15, 23)) == 0
    capsys.readouterr()
    assert _plan_cli(repo, monkeypatch, actions, pt(2026, 9, 12, 15, 31)) == 0
    assert len(actions.dispatched) == 1
    line = _line(capsys.readouterr().out)
    assert "re-armed for 15:36 PT" in line
    assert "already waiting" in line and "/actions/runs/1" in line


def test_an_old_stub_without_not_before_skips_the_re_arm_and_never_fails(
        tmp_path, monkeypatch, capsys):
    repo = _caller(tmp_path, stub=OLD_STUB)
    actions = _Actions()
    code = _plan_cli(repo, monkeypatch, actions, pt(2026, 9, 12, 15, 23))
    assert code == 0
    assert actions.dispatched == []
    line = _line(capsys.readouterr().out)
    assert "re-arm skipped: caller stub lacks not_before" in line


def test_a_stub_without_actions_write_skips_the_re_arm_and_never_fails(
        tmp_path, monkeypatch, capsys):
    repo = _caller(tmp_path)
    actions = _Actions(dispatch_error=(
        "could not create workflow dispatch event: HTTP 403: Resource not "
        "accessible by integration"))
    code = _plan_cli(repo, monkeypatch, actions, pt(2026, 9, 12, 15, 23))
    assert code == 0
    line = _line(capsys.readouterr().out)
    assert "re-arm skipped: caller stub lacks actions: write" in line


def test_an_unreadable_listing_still_re_arms(tmp_path, monkeypatch, capsys):
    """Unreadable is never "nothing is waiting" silently — but the safe answer
    HERE is to dispatch: a duplicate is collapsed by the wait job's
    concurrency group, whereas a missing re-arm is the whole bug."""
    repo = _caller(tmp_path)
    actions = _Actions(unreadable=True)
    assert _plan_cli(repo, monkeypatch, actions, pt(2026, 9, 12, 15, 23)) == 0
    assert len(actions.dispatched) == 1
    line = _line(capsys.readouterr().out)
    assert "re-armed for 15:36 PT" in line
    assert "HTTP 500" in line


def test_without_the_re_arm_flag_the_plan_dispatches_nothing(tmp_path,
                                                             monkeypatch,
                                                             capsys):
    repo = _caller(tmp_path)
    actions = _Actions()
    assert _plan_cli(repo, monkeypatch, actions, pt(2026, 9, 12, 15, 23),
                     re_arm=False) == 0
    assert actions.dispatched == []
    assert "re-arm" not in _line(capsys.readouterr().out)


def test_the_wait_cli_sleeps_until_the_minute_bounded_by_the_callers_spacing(
        tmp_path, monkeypatch, capsys):
    repo = _caller(tmp_path)
    slept = []
    monkeypatch.setattr(release_train, "_sleep", slept.append)
    argv = ["--repo", REPO, "--repo-root", str(repo),
            "--file", str(repo / ".github" / "bureau" / "release.json"),
            "wait", "--not-before"]

    monkeypatch.setattr(release_train, "_now", lambda: pt(2026, 9, 12, 15, 26))
    assert release_train.main(argv + ["2026-09-12T22:36:00Z"]) == 0
    assert slept == [600]

    slept.clear()
    monkeypatch.setattr(release_train, "_now", lambda: pt(2026, 9, 12, 14, 0))
    assert release_train.main(argv + ["2026-09-12T22:36:00Z"]) == 0
    assert slept == [32 * 60]

    slept.clear()
    assert release_train.main(argv + ["not a time"]) == 0
    assert slept == []
    assert "straight on" in capsys.readouterr().out


# --------------------------------------------------------------------------
# The workflow and the stub.
# --------------------------------------------------------------------------

def _workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def _on(doc):
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _reference_stub():
    for block in re.findall(r"```yaml\n(.*?)```", STANDARD.read_text(), re.S):
        if "release-train.yml@stable" in block:
            return yaml.safe_load(block)
    raise AssertionError("standards/release-train.md carries no stub block")


def test_the_wait_job_runs_only_on_a_re_arm_and_collapses_by_the_minute():
    wait = _workflow()["jobs"]["wait"]
    assert "workflow_dispatch" in wait["if"]
    assert "github.event.inputs.not_before" in wait["if"]
    group = wait["concurrency"]["group"]
    assert "github.repository" in group
    assert "github.event.inputs.not_before" in group
    # A run with no minute must never share a lane with another.
    assert "github.run_id" in group
    assert wait["concurrency"]["cancel-in-progress"] is False
    # The name is how the plan finds a run already armed for the minute.
    assert wait["name"].startswith(release_train.WAIT_JOB_PREFIX)
    assert "github.event.inputs.not_before" in wait["name"]
    assert wait["timeout-minutes"] > release_train.WAIT_CEILING_MINUTES + 2


def test_the_wait_reads_the_minute_through_env_never_the_shell_line():
    wait = _workflow()["jobs"]["wait"]
    step = next(s for s in wait["steps"] if "wait" in (s.get("run") or ""))
    assert step["env"]["NOT_BEFORE"] == "${{ github.event.inputs.not_before }}"
    assert "${{" not in step["run"]
    assert "--not-before" in step["run"]


def test_the_wait_does_not_touch_the_plan_jobs_ten_minute_bound():
    """`release-ci-green`'s cadence in config/pipeline-acts.json is read off
    the plan job's timeout — so the wait is its own job, and plan keeps 10."""
    jobs = _workflow()["jobs"]
    assert jobs["plan"]["timeout-minutes"] == 10
    assert jobs["plan"]["needs"] == "wait"
    assert "!cancelled()" in jobs["plan"]["if"]
    assert "needs.wait.result" in jobs["plan"]["if"]


def test_the_release_job_survives_a_skipped_wait():
    """actions/runner#491: a job's implicit `success()` is false when ANY
    ancestor was skipped — and on every CI-completion and schedule run the
    wait is skipped. So the release job states its own status check."""
    cond = _workflow()["jobs"]["release"]["if"]
    assert "!cancelled()" in cond
    assert "needs.plan.result == 'success'" in cond
    assert "needs.plan.outputs.matrix != '[]'" in cond


def test_the_plan_step_re_arms_with_the_trains_own_token():
    plan = _workflow()["jobs"]["plan"]
    step = next(s for s in plan["steps"] if s.get("id") == "plan")
    assert "--re-arm" in step["run"]
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "github.event.repository.default_branch" in step["env"]["DEFAULT_BRANCH"]


def test_the_reference_stub_declares_not_before_and_actions_write():
    stub = _reference_stub()
    inputs = _on(stub)["workflow_dispatch"]["inputs"]
    assert inputs["not_before"]["required"] is False
    assert inputs["not_before"]["default"] == ""
    assert stub["permissions"]["actions"] == "write"


def test_the_standard_and_the_document_say_the_train_re_arms_itself():
    body = STANDARD.read_text()
    assert "DRE-3559" in body and "re-arm" in body.lower()
    rendered = release_train.render_markdown()
    assert "DRE-3559" in rendered and "re-arm" in rendered.lower()
    row = next(l for l in rendered.splitlines() if l.startswith("| 6 | `spacing`"))
    assert "re-arm" in row
