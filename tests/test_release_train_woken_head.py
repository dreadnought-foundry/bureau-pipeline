"""A re-armed train run walks from the branch's head when it WAKES (DRE-3791).

THE BUG, 2026-09-13 (dreadnought-foundry/agent-bureau). DRE-3559's re-arm
worked — a spacing hold dispatched a waiting run, and it fired on the minute.
It released the wrong commit:

  * 09:34 PT — a spacing hold dispatched re-arm run 34768967919
    (`workflow_dispatch`, `not_before` 09:59 PT). A dispatched run's
    `GITHUB_SHA` is fixed at dispatch: 8559db0, #2523's merge.
  * 09:43 and 09:55 PT — #2527 and #2528 merged; main's CI was green on them
    at 09:47 and 09:58 PT.
  * 09:58 PT — run 34770159698 (#2528's CI completion) walked from the new
    head 635617a, held on the spacing, and deferred: "re-armed for 09:59 PT
    (already waiting: …/34768967919)".
  * 10:00 PT — the waiter's plan job checked out the caller with no `ref:`.
    actions/checkout fetched every branch (origin/main = 635617a), then
    force-fetched `+8559db0…:refs/remotes/origin/main` and checked THAT out —
    its default for a triggering repo is the event's sha. The plan walked from
    8559db0 and released agent-bureau-console-v1.6.61 without #2527 or #2528,
    and nothing re-armed after it.

THE FIX, pinned here: the plan is handed the re-arm's `not_before`, and a run
that carries one re-reads the default branch's tip from `origin` when it
wakes, moves its checkout there, and walks from it — the ordinary green-at-SHA
walk, nothing else changed. The invariant: a merge that is green when the train
fires is in the release. A merge still checking is stepped past exactly as
before; a run with nothing newer releases what it would have; every run that
was not re-armed walks from the head it was handed, as ever. A tip that cannot
be re-read is a warning that names the dispatch-time commit it walks from
instead — never a silent fall-back to the bug.

Run: python3 -m pytest tests/test_release_train_woken_head.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import release_train  # noqa: E402
from test_release_train_re_arm import (  # noqa: E402
    REPO, STUB_PATH, _Actions, _caller, _git, green, pending, pt)

WORKFLOW = ROOT / ".github" / "workflows" / "release-train.yml"

#: The re-arm run 34768967919 carried: 09:59 PT on 2026-09-13.
NOT_BEFORE = "2026-09-13T16:59:00Z"
#: agent-bureau-console-v1.6.60 — "released 29 minutes ago" at 09:58 PT, and
#: the spacing's first whole minute is 09:59 PT.
TAG_AT = "2026-09-13T09:28:30-07:00"


# --------------------------------------------------------------------------
# The caller's origin, and a runner checkout pinned the way actions/checkout
# pins a dispatched run's.
# --------------------------------------------------------------------------

def _origin(tmp_path, caller):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(caller), str(origin))
    return origin


def _merge(caller, origin, content):
    """One more commit on main that the console is behind on, pushed — a
    merge landing while the waiter sleeps."""
    (caller / "demo" / "app.txt").write_text(f"{content}\n")
    _git(caller, "commit", "-qam", f"merge: {content}")
    _git(caller, "push", "-q", str(origin), "main")
    return _git(caller, "rev-parse", "HEAD")


def _runner(tmp_path, origin, event_sha, name):
    """The plan job's checkout, as actions/checkout leaves it with no `ref:`
    (run 34768967919, plan job, 16:59:07Z): every branch fetched, then
    `origin/main` forced back to the event's sha and checked out."""
    runner = tmp_path / name
    _git(tmp_path, "clone", "-q", str(origin), str(runner))
    _git(runner, "update-ref", "refs/remotes/origin/main", event_sha)
    _git(runner, "checkout", "-q", "--force", "-B", "main",
         "refs/remotes/origin/main")
    return runner


def _plan(runner, monkeypatch, *, now, head, not_before="", green_shas=(),
          actions=None):
    """The plan CLI exactly as the plan step runs it, with the two GitHub
    seams and the check reader faked. Returns (exit code, outputs)."""
    actions = actions if actions is not None else _Actions()
    monkeypatch.setattr(release_train, "_now", lambda: now)
    monkeypatch.setattr(release_train, "fetch_armed_runs", actions.runs)
    monkeypatch.setattr(release_train, "dispatch_re_arm", actions.dispatch)
    monkeypatch.setattr(
        release_train, "fetch_checks",
        lambda repo, sha: green() if sha in green_shas else pending())
    output = runner.parent / f"{runner.name}.github_output"
    output.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    code = release_train.main([
        "--repo", REPO, "--repo-root", str(runner),
        "--file", str(runner / ".github" / "bureau" / "release.json"),
        "plan", "--head", head, "--surface", "", "--re-arm",
        "--workflow", STUB_PATH, "--default-branch", "main",
        "--not-before", not_before])
    outputs = {}
    for line in output.read_text().splitlines():
        key, _, value = line.partition("=")
        outputs[key] = value
    return code, outputs


def _demo_line(out):
    return next(l for l in out.splitlines()
                if l.startswith(f"{release_train.TAG}: ") and f"{REPO} demo" in l)


# --------------------------------------------------------------------------
# The production sequence, end to end.
# --------------------------------------------------------------------------

def test_the_2026_09_13_sequence_the_waiter_releases_what_went_green_while_it_slept(
        tmp_path, monkeypatch, capsys):
    caller = _caller(tmp_path, tag_at=TAG_AT)
    dispatched_at = _git(caller, "rev-parse", "HEAD")        # 8559db0, #2523
    origin = _origin(tmp_path, caller)
    actions = _Actions()

    # 09:34 PT — walking from 8559db0, the train holds on the spacing and
    # re-arms for 09:59 PT: one dispatch, pinned at 8559db0 by GitHub.
    code, _ = _plan(_runner(tmp_path, origin, dispatched_at, "run-0934"),
                    monkeypatch, now=pt(2026, 9, 13, 9, 34), head=dispatched_at,
                    green_shas={dispatched_at}, actions=actions)
    assert code == 0
    assert actions.dispatched == [(REPO, STUB_PATH, "main", NOT_BEFORE)]
    capsys.readouterr()

    # 09:43 and 09:55 PT — #2527 and #2528 merge; green at 09:47 and 09:58.
    pr2527 = _merge(caller, origin, "v2")
    pr2528 = _merge(caller, origin, "v3")
    all_green = {dispatched_at, pr2527, pr2528}

    # 09:58 PT — #2528's CI completion walks from 635617a, holds on the
    # spacing, and defers to the run already waiting. No second dispatch.
    code, _ = _plan(_runner(tmp_path, origin, pr2528, "run-0958"), monkeypatch,
                    now=pt(2026, 9, 13, 9, 58), head=pr2528,
                    green_shas=all_green, actions=actions)
    assert code == 0
    assert len(actions.dispatched) == 1
    assert "already waiting" in _demo_line(capsys.readouterr().out)

    # 09:59 PT — the waiter wakes. Its checkout is pinned at the commit it
    # was dispatched at, exactly as run 34768967919's was.
    waiter = _runner(tmp_path, origin, dispatched_at, "run-0934-woken")
    code, outputs = _plan(waiter, monkeypatch, now=pt(2026, 9, 13, 9, 59, 5),
                          head=dispatched_at, not_before=NOT_BEFORE,
                          green_shas=all_green, actions=actions)
    out = capsys.readouterr().out
    assert code == 0, out
    # The release carries #2527 and #2528: it is cut at #2528's merge.
    assert json.loads(outputs["matrix"]) == [{"surface": "demo", "sha": pr2528}]
    assert outputs["head"] == pr2528
    # The checkout moved too, so release.json and the stub are read at the
    # commit the walk starts from, not the dispatch-time tree.
    assert _git(waiter, "rev-parse", "HEAD") == pr2528
    # And the run says what it did, naming both commits.
    woke = next(l for l in out.splitlines() if "woke" in l)
    assert pr2528[:7] in woke and dispatched_at[:7] in woke
    assert f"releases {pr2528[:7]}" in _demo_line(out)


# --------------------------------------------------------------------------
# The walk from the woken head is the ordinary walk.
# --------------------------------------------------------------------------

def test_a_merge_still_checking_when_the_waiter_wakes_is_stepped_past(
        tmp_path, monkeypatch, capsys):
    """Unchanged behaviour, from the new head: a merge whose CI has not
    finished is stepped past and named, the newest green commit is released,
    and the pending merge's own CI completion fires the run that takes it."""
    caller = _caller(tmp_path, tag_at=TAG_AT)
    dispatched_at = _git(caller, "rev-parse", "HEAD")
    origin = _origin(tmp_path, caller)
    newer = _merge(caller, origin, "v2")

    waiter = _runner(tmp_path, origin, dispatched_at, "waiter")
    code, outputs = _plan(waiter, monkeypatch, now=pt(2026, 9, 13, 9, 59, 5),
                          head=dispatched_at, not_before=NOT_BEFORE,
                          green_shas={dispatched_at})
    out = capsys.readouterr().out
    assert code == 0, out
    assert json.loads(outputs["matrix"]) == [
        {"surface": "demo", "sha": dispatched_at}]
    assert outputs["head"] == newer
    line = _demo_line(out)
    assert f"releases {dispatched_at[:7]}" in line
    assert f"stepped past {newer[:7]}" in line


def test_a_waiter_with_nothing_newer_releases_what_it_always_did(
        tmp_path, monkeypatch, capsys):
    caller = _caller(tmp_path, tag_at=TAG_AT)
    dispatched_at = _git(caller, "rev-parse", "HEAD")
    origin = _origin(tmp_path, caller)

    woken = _runner(tmp_path, origin, dispatched_at, "woken")
    code, outputs = _plan(woken, monkeypatch, now=pt(2026, 9, 13, 9, 59, 5),
                          head=dispatched_at, not_before=NOT_BEFORE,
                          green_shas={dispatched_at})
    out = capsys.readouterr().out
    assert code == 0, out
    assert json.loads(outputs["matrix"]) == [
        {"surface": "demo", "sha": dispatched_at}]
    assert outputs["head"] == dispatched_at
    assert "still" in next(l for l in out.splitlines() if "woke" in l)

    # …and exactly what a run that was never re-armed releases from there.
    plain = _runner(tmp_path, origin, dispatched_at, "plain")
    code, plain_outputs = _plan(plain, monkeypatch,
                                now=pt(2026, 9, 13, 9, 59, 5),
                                head=dispatched_at, green_shas={dispatched_at})
    assert code == 0
    assert plain_outputs["matrix"] == outputs["matrix"]


def test_a_run_that_was_not_re_armed_walks_from_the_head_it_was_handed(
        tmp_path, monkeypatch, capsys):
    """A CI completion, the schedule and a hand dispatch carry no
    `not_before`: the plan never fetches, and walks from `--head` exactly as
    before (DRE-3266). Only a run that slept re-reads the branch."""
    caller = _caller(tmp_path, tag_at=TAG_AT)
    handed = _git(caller, "rev-parse", "HEAD")
    origin = _origin(tmp_path, caller)
    newer = _merge(caller, origin, "v2")

    runner = _runner(tmp_path, origin, handed, "ci-completion")
    code, outputs = _plan(runner, monkeypatch, now=pt(2026, 9, 13, 10, 0),
                          head=handed, green_shas={handed, newer})
    out = capsys.readouterr().out
    assert code == 0, out
    assert json.loads(outputs["matrix"]) == [{"surface": "demo", "sha": handed}]
    assert outputs["head"] == handed
    assert _git(runner, "rev-parse", "HEAD") == handed
    assert "woke" not in out


def test_a_tip_that_cannot_be_re_read_is_a_warning_never_a_silent_stale_walk(
        tmp_path, monkeypatch, capsys):
    """Falling back without a word would reproduce this card's bug and look
    healthy. So the fall-back is LOUD — a `::warning::` annotation naming the
    dispatch-time commit it walks from — and the train is never stopped: the
    green commit it has is still released."""
    caller = _caller(tmp_path, tag_at=TAG_AT)
    dispatched_at = _git(caller, "rev-parse", "HEAD")
    origin = _origin(tmp_path, caller)
    _merge(caller, origin, "v2")

    waiter = _runner(tmp_path, origin, dispatched_at, "waiter")
    _git(waiter, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    code, outputs = _plan(waiter, monkeypatch, now=pt(2026, 9, 13, 9, 59, 5),
                          head=dispatched_at, not_before=NOT_BEFORE,
                          green_shas={dispatched_at})
    out = capsys.readouterr().out
    assert code == 0, out
    warning = next(l for l in out.splitlines() if l.startswith("::warning"))
    assert dispatched_at[:7] in warning
    assert "dispatched" in warning
    assert json.loads(outputs["matrix"]) == [
        {"surface": "demo", "sha": dispatched_at}]
    assert outputs["head"] == dispatched_at


# --------------------------------------------------------------------------
# The workflow hands the plan the minute.
# --------------------------------------------------------------------------

def _plan_step():
    doc = yaml.safe_load(WORKFLOW.read_text())
    return next(s for s in doc["jobs"]["plan"]["steps"] if s.get("id") == "plan")


def test_the_plan_step_hands_the_script_the_re_arms_minute_through_env():
    step = _plan_step()
    assert step["env"]["NOT_BEFORE"] == "${{ github.event.inputs.not_before }}"
    assert '--not-before "$NOT_BEFORE"' in step["run"]
    # Never interpolated into the shell line (the tests.yml rule).
    assert "${{" not in step["run"]
    # The branch the woken run re-reads is the one the re-arm dispatches on.
    assert "github.event.repository.default_branch" in step["env"]["DEFAULT_BRANCH"]


def test_the_plan_checkout_is_still_the_events_head_on_every_event():
    """The re-read is the SCRIPT's, on the one kind of run that slept — so
    the checkout keeps DRE-3266's no-`ref:` rule for every event and the
    woken run moves itself, where a test can hold it."""
    doc = yaml.safe_load(WORKFLOW.read_text())
    caller_checkout = next(
        s for s in doc["jobs"]["plan"]["steps"]
        if "actions/checkout@" in (s.get("uses") or "")
        and "repository" not in (s.get("with") or {}))
    assert "ref" not in caller_checkout.get("with", {})
    assert caller_checkout["with"]["fetch-depth"] == 0
