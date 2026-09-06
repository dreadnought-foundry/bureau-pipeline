"""The release train's rules, pinned without GitHub (DRE-3167).

`scripts/release_train.py` is the decision; `.github/workflows/release-train.yml`
is the thin caller that gathers GitHub's records and acts on the verdict — the
`promote_channel.py` / `release_gate.py` shape. So every rule the train has is
testable here, in Python, and none of it needs a token, a runner or a tag push.

What this file pins, in the order the card lists it:

  1. two triggers inside the spacing produce ONE release;
  2. a trigger at 23:00 PT defers to 07:00;
  3. a surface that reads current is a no-op that SAYS so;
  4. a red SHA is a refusal that says so;
  5. the brake (`RELEASE_HOLD`) stops every surface;
  6. a malformed `release.json` is refused with the FIELD named.

and the 2026-09-05 addendum:

  * a hand dispatch runs an `auto: false` surface outside its window, and
    still refuses on a red SHA and on the brake;
  * a script that exits 0 printing `deferred: …` is a no-op, no tag, no
    failure;
  * a surface whose `paths` are untouched since its newest tag is a no-op;
  * the two scheduled firings decide by the `America/Los_Angeles` clock — the
    06:00 PST one names the window, the 08:00 PDT one is an ordinary run.

and DRE-3263, the CEO's rule of 2026-09-06 — THE TRAIN IS NEVER STOPPED:

  * only the checks that gate a merge are checks on the commit — the set is
    the merge gate's, read from one place; a fix agent, the medic, the sweep
    and the train's own runs on the SHA are ignored by verified origin;
  * a pending gating check is NOT waited for: the surface is a no-op that
    names it, and the next train (CI completing on main) picks the commit
    up. The thirty-minute wait and its refusal are gone;
  * a RED gating check still refuses, and a SHA with no gating check at all
    still refuses — never a wait;
  * the no-op and refuse lines name what was read and what was ignored;
  * the stub fires on CI completion, and the reusable workflow reads the SHA
    from that event.

The end-to-end leg builds a REAL git repository in a temp directory, with a
real surface script that cuts a real annotated tag, and drives the train's own
entry points over it: the stub is data plus one `uses:` line, and the receipt
is the tag.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_train  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "release-train.yml"
STANDARD = ROOT / "standards" / "release-train.md"
OWN_DATA = ROOT / ".github" / "bureau" / "release.json"
DOC = ROOT / "docs" / "release-train.md"

UTC = timezone.utc


def surface(**over):
    """A well-formed surface, the card's contract shape, with overrides."""
    data = {
        "tag_series": ["console/v*", "console-v*"],
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


def pt(year, month, day, hour, minute=0):
    """A wall-clock moment in America/Los_Angeles, as the train reads it."""
    return datetime(year, month, day, hour, minute, tzinfo=release_train.PT)


def green():
    return release_train.read_checks(
        [{"name": "ci", "status": "completed", "conclusion": "success"}]
    )


# --------------------------------------------------------------------------
# 1. Two triggers inside the spacing produce one release.
# --------------------------------------------------------------------------

def test_two_triggers_inside_the_spacing_produce_one_release():
    console = surface(spacing_minutes=30)
    first = pt(2026, 7, 15, 10, 0)

    opened = release_train.decide(
        console, first, first - timedelta(hours=6), "behind", green(), None
    )
    assert opened.act == release_train.RELEASE, opened.reason

    # The first release cut a tag AT `first`; the second trigger five minutes
    # later reads that tag as the newest in the series.
    second = release_train.decide(
        console, first + timedelta(minutes=5), first, "behind", green(), None
    )
    assert second.act == release_train.NO_OP
    assert second.code == "spacing"
    assert "30" in second.reason and "spacing" in second.reason.lower()


def test_the_spacing_is_measured_from_the_newest_tag_not_the_clock():
    console = surface(spacing_minutes=30)
    now = pt(2026, 7, 15, 10, 0)
    just_elapsed = release_train.decide(
        console, now, now - timedelta(minutes=31), "behind", green(), None
    )
    assert just_elapsed.act == release_train.RELEASE


# --------------------------------------------------------------------------
# 2. A trigger at 23:00 PT defers to 07:00.
# --------------------------------------------------------------------------

def test_a_trigger_at_2300_pt_defers_to_the_window():
    late = release_train.decide(
        surface(), pt(2026, 7, 15, 23, 0), None, "behind", green(), None
    )
    assert late.act == release_train.NO_OP
    assert late.code == "window"
    assert "07:00" in late.reason, late.reason
    assert "23:00" in late.reason, late.reason


def test_a_window_of_always_never_defers():
    anytime = release_train.decide(
        surface(window="always"), pt(2026, 7, 15, 3, 0), None, "behind",
        green(), None,
    )
    assert anytime.act == release_train.RELEASE


# --------------------------------------------------------------------------
# 3. A current surface is a no-op that says so.
# --------------------------------------------------------------------------

def test_a_current_surface_is_a_no_op_that_says_so():
    current = release_train.decide(
        surface(), pt(2026, 7, 15, 10, 0), pt(2026, 7, 15, 8, 0), "current",
        green(), None,
    )
    assert current.act == release_train.NO_OP
    assert current.code == "current"
    assert "current" in current.reason.lower()
    assert current.tag is None


# --------------------------------------------------------------------------
# 4. A red SHA is a refusal that says so.
# --------------------------------------------------------------------------

def test_a_red_sha_is_a_refusal_that_says_so():
    red = release_train.read_checks(
        [
            {"name": "ci", "status": "completed", "conclusion": "success"},
            {"name": "console image", "status": "completed",
             "conclusion": "failure"},
        ]
    )
    refused = release_train.decide(
        surface(), pt(2026, 7, 15, 10, 0), None, "behind", red, None
    )
    assert refused.act == release_train.REFUSE
    assert "console image" in refused.reason
    assert refused.ok is False


def test_a_sha_with_no_checks_at_all_is_a_refusal_never_a_wait():
    absent = release_train.read_checks([])
    assert absent.state == "absent"
    refused = release_train.decide(
        surface(), pt(2026, 7, 15, 10, 0), None, "behind", absent, None
    )
    assert refused.act == release_train.REFUSE
    assert "no check" in refused.reason.lower()


def test_a_skipped_check_is_green_the_way_the_merge_gate_reads_it():
    import merge_gate

    assert release_train.GREEN_CONCLUSIONS is merge_gate.GREEN_CONCLUSIONS
    checks = release_train.read_checks(
        [{"name": "acts", "status": "completed", "conclusion": "skipped"}]
    )
    assert checks.state == "green"


# --------------------------------------------------------------------------
# 5. The brake stops every surface.
# --------------------------------------------------------------------------

def test_the_brake_stops_every_surface():
    held = [
        release_train.decide(
            surface(), pt(2026, 7, 15, 10, 0), None, "behind", green(),
            "2026-09-05",
        ),
        release_train.decide(
            surface(auto=False), pt(2026, 7, 15, 23, 0), None, "current",
            green(), "2026-09-05", dispatched=True,
        ),
    ]
    for decision in held:
        assert decision.act == release_train.HELD, decision.reason
        assert "RELEASE_HOLD" in decision.reason
        assert "2026-09-05" in decision.reason


def test_the_brake_reads_the_way_intake_hold_reads(monkeypatch):
    """The empty string is the load-bearing case: an unset variable must not
    read as a closed brake, or the fleet stops on every trigger."""
    for raw in ("", "false", "0", "no", "off"):
        monkeypatch.setenv(release_train.ENV_HOLD, raw)
        assert release_train.brake() is None, raw
    monkeypatch.delenv(release_train.ENV_HOLD, raising=False)
    assert release_train.brake() is None
    monkeypatch.setenv(release_train.ENV_HOLD, "true")
    assert release_train.brake() == ""
    monkeypatch.setenv(release_train.ENV_HOLD, "2026-09-05")
    assert release_train.brake() == "2026-09-05"


def test_the_brake_is_one_line_per_surface_and_the_matrix_is_empty(tmp_path):
    repo = _fake_repo(tmp_path)
    plan = release_train.plan(
        release_train.load(repo / ".github" / "bureau" / "release.json"),
        repo_root=repo, head=_head(repo), now=pt(2026, 7, 15, 10, 0),
        brake="2026-09-05",
    )
    assert [d.act for _, d in plan] == [release_train.HELD] * len(plan)
    assert release_train.matrix(plan) == []


# --------------------------------------------------------------------------
# 6. A malformed release.json is refused with the field named.
# --------------------------------------------------------------------------

def test_a_malformed_release_json_is_refused_with_the_field_named():
    bad = {
        "surfaces": {
            "console": {
                "tag_series": ["console/v*"],
                "paths": ["console/"],
                "script": "infra/release-console.sh",
                "rollback": "make rollback-console VERSION=<tag>",
                "spacing_minutes": 30,
                # `window` missing
                "auto": True,
                "identity": "bureau-console-release",
            }
        }
    }
    problems = release_train.check_schema(bad)
    assert problems, "a surface with no window must be refused"
    assert any("window" in p for p in problems), problems


@pytest.mark.parametrize(
    "field,value,needle",
    [
        ("tag_series", [], "tag_series"),
        ("tag_series", "console/v*", "tag_series"),
        ("paths", "console/", "paths"),
        ("spacing_minutes", "thirty", "spacing_minutes"),
        ("spacing_minutes", -1, "spacing_minutes"),
        ("window", "07:00-21:00", "window"),
        ("window", "25:00-26:00 PT", "window"),
        ("auto", "true", "auto"),
        ("identity", "", "identity"),
        ("record", "receipt", "record"),
    ],
)
def test_every_malformed_field_is_named_in_the_refusal(field, value, needle):
    data = {"surfaces": {"console": _good_surface_data(**{field: value})}}
    problems = release_train.check_schema(data)
    assert problems, f"{field}={value!r} must be refused"
    assert any(needle in p for p in problems), problems
    assert all("console" in p for p in problems), problems


def test_an_unknown_field_is_named_rather_than_ignored():
    data = {"surfaces": {"console": _good_surface_data(regions=["us-west-2"])}}
    problems = release_train.check_schema(data)
    assert any("regions" in p for p in problems), problems


def test_a_missing_surfaces_key_is_refused_by_name():
    problems = release_train.check_schema({"surface": {}})
    assert any("surfaces" in p for p in problems), problems


def test_the_schema_check_reads_the_caller_for_the_script_it_names(tmp_path):
    data = {"surfaces": {"console": _good_surface_data()}}
    (tmp_path / "infra").mkdir()
    assert any(
        "infra/release-console.sh" in p
        for p in release_train.check_schema(data, repo_root=tmp_path)
    ), "a script the caller does not carry must be named"
    (tmp_path / "infra" / "release-console.sh").write_text("#!/usr/bin/env bash\n")
    assert release_train.check_schema(data, repo_root=tmp_path) == []


# --------------------------------------------------------------------------
# The addendum: hand dispatch.
# --------------------------------------------------------------------------

def test_a_hand_dispatch_runs_an_auto_false_surface_outside_its_window():
    manual = surface(auto=False)
    at_2300 = pt(2026, 7, 15, 23, 0)

    unattended = release_train.decide(
        manual, at_2300, None, "behind", green(), None
    )
    assert unattended.act == release_train.NO_OP
    assert unattended.code == "auto-false"

    dispatched = release_train.decide(
        manual, at_2300, None, "behind", green(), None, dispatched=True
    )
    assert dispatched.act == release_train.RELEASE, dispatched.reason


def test_a_hand_dispatch_still_refuses_on_a_red_sha_and_on_the_brake():
    manual = surface(auto=False)
    at_2300 = pt(2026, 7, 15, 23, 0)
    red = release_train.read_checks(
        [{"name": "ci", "status": "completed", "conclusion": "failure"}]
    )

    refused = release_train.decide(
        manual, at_2300, None, "behind", red, None, dispatched=True
    )
    assert refused.act == release_train.REFUSE
    assert "ci" in refused.reason

    held = release_train.decide(
        manual, at_2300, None, "behind", green(), "2026-09-05", dispatched=True
    )
    assert held.act == release_train.HELD


def test_a_hand_dispatch_still_honours_the_spacing():
    now = pt(2026, 7, 15, 10, 0)
    decision = release_train.decide(
        surface(auto=False), now, now - timedelta(minutes=5), "behind",
        green(), None, dispatched=True,
    )
    assert decision.act == release_train.NO_OP
    assert decision.code == "spacing"


# --------------------------------------------------------------------------
# DRE-3263: the train is never stopped. Only the checks that gate a merge
# are checks; a pending one is not waited for — the surface is a no-op that
# names it, and the next train picks the commit up.
# --------------------------------------------------------------------------

FIXTURE = ROOT / "tests" / "fixtures" / "release-train-3fd03b083-2026-09-06.json"

CI_PATH = ".github/workflows/ci.yml"
FIX_PATH = ".github/workflows/agent-fix.yml"
TRAIN_PATH = ".github/workflows/release-train.yml"


def _run(suite, path, event="push", **over):
    """One entry of GET actions/runs?head_sha=…, the way GitHub records it."""
    run = {"id": suite + 1, "name": path.rsplit("/", 1)[-1], "path": path,
           "event": event, "status": "completed", "conclusion": "success",
           "check_suite_id": suite}
    run.update(over)
    return run


def _check(name, suite, status="completed", conclusion="success"):
    return {"name": name, "status": status, "conclusion": conclusion,
            "check_suite": {"id": suite}}


def _fixture():
    return json.loads(FIXTURE.read_text())


def test_the_11_36_pt_fixture_releases_past_two_in_progress_fix_agents():
    """The live record: all of CI green on main's head, two `call / fix PR #…`
    Agent Fix runs still in progress beside it, and the console's first
    supervised release sat in `Run the surface` waiting on them."""
    fixture = _fixture()
    in_progress = [c["name"] for c in fixture["check_runs"]
                   if c["status"] != "completed"]
    assert "call / fix PR #2325" in in_progress
    assert "call / fix PR #2328" in in_progress

    checks = release_train.read_checks(fixture["check_runs"],
                                       fixture["workflow_runs"])
    assert checks.state == "green", checks.detail
    assert "Console backend (pytest)" in checks.read
    assert not any("fix PR" in name for name in checks.read)
    assert any("fix PR #2325" in name for name, _ in checks.ignored)

    decision = release_train.decide(
        surface(), pt(2026, 9, 6, 11, 36), None, "behind", checks, None,
        dispatched=True,
    )
    assert decision.act == release_train.RELEASE, decision.reason


def test_a_pending_gating_check_is_a_no_op_that_names_it_and_the_next_run_takes_it():
    checks = release_train.read_checks(
        [_check("Console backend (pytest)", 1, status="in_progress",
                conclusion=None),
         _check("Toolkit (pytest)", 1)],
        [_run(1, CI_PATH)],
    )
    assert checks.state == "pending"

    decision = release_train.decide(
        surface(), pt(2026, 7, 15, 10, 0), None, "behind", checks, None
    )
    assert decision.act == release_train.NO_OP, decision.reason
    assert decision.code == "ci-pending"
    assert decision.ok is True
    assert "Console backend (pytest)" in decision.reason
    assert "next" in decision.reason.lower()
    assert "30" not in decision.reason, "no thirty-minute ceiling survives"


def test_a_red_gating_check_still_refuses_even_beside_in_progress_fix_agents():
    checks = release_train.read_checks(
        [_check("Console backend (pytest)", 1, conclusion="failure"),
         _check("call / fix PR #2325", 2, status="in_progress",
                conclusion=None)],
        [_run(1, CI_PATH), _run(2, FIX_PATH, event="workflow_dispatch")],
    )
    assert checks.state == "red"
    refused = release_train.decide(
        surface(), pt(2026, 7, 15, 10, 0), None, "behind", checks, None
    )
    assert refused.act == release_train.REFUSE
    assert "Console backend (pytest)" in refused.reason


def test_a_sha_whose_only_check_runs_are_ignored_ones_is_absent_and_refuses():
    """Fix agents and the medic on a SHA prove nothing about it."""
    checks = release_train.read_checks(
        [_check("call / fix PR #2325", 2, status="in_progress", conclusion=None),
         _check("call / diagnose", 3, conclusion="skipped")],
        [_run(2, FIX_PATH, event="workflow_dispatch"),
         _run(3, ".github/workflows/medic.yml", event="workflow_run")],
    )
    assert checks.state == "absent"
    refused = release_train.decide(
        surface(), pt(2026, 7, 15, 10, 0), None, "behind", checks, None
    )
    assert refused.act == release_train.REFUSE
    assert "no gating check" in refused.reason.lower()


def test_the_train_ignores_its_own_runs_on_the_sha():
    """A push-triggered train run sits on the same SHA it is releasing; its
    own in-progress `Release <surface>` job must not read as pending."""
    checks = release_train.read_checks(
        [_check("Toolkit (pytest)", 1),
         _check("call / Plan the surfaces", 4),
         _check("call / Release console", 4, status="in_progress",
                conclusion=None)],
        [_run(1, CI_PATH), _run(4, TRAIN_PATH, event="push")],
    )
    assert checks.state == "green", checks.detail
    assert checks.read == ("Toolkit (pytest)",)


@pytest.mark.parametrize(
    "event", ["workflow_dispatch", "schedule", "workflow_run", "push"]
)
def test_the_trains_own_in_progress_check_run_on_the_sha_is_not_a_check(event):
    """agent-bureau run 34052227934 (workflow_dispatch, surface=console)
    refused at 2026-09-06 12:06 PT with "a check on the head SHA was still
    pending after 30 minutes: `call / Release console` is still in_progress"
    — the two fix runs had finished, and the last pending check run on
    main's head was the TRAIN'S OWN job. Every train run — dispatched,
    scheduled, CI-completion or push — attaches its own check runs to the
    SHA it is releasing, so under the old rule it waited on itself until the
    ceiling. Its own runs are ignored by the stub's path, whatever the event."""
    checks = release_train.read_checks(
        [_check("Toolkit (pytest)", 1),
         _check("call / Plan the surfaces", 4),
         _check("call / Release console", 4, status="in_progress",
                conclusion=None)],
        [_run(1, CI_PATH), _run(4, TRAIN_PATH, event=event, name="Release train",
                                status="in_progress", conclusion=None)],
    )
    assert checks.state == "green", checks.detail
    assert checks.read == ("Toolkit (pytest)",)
    assert sorted(name for name, _ in checks.ignored) == [
        "call / Plan the surfaces", "call / Release console"]
    assert all(path == TRAIN_PATH for _, path in checks.ignored)

    decision = release_train.decide(
        surface(auto=False), pt(2026, 9, 6, 11, 36), None, "behind", checks,
        None, dispatched=True,
    )
    assert decision.act == release_train.RELEASE, decision.reason


def test_a_check_run_with_no_recorded_origin_is_counted_fail_closed():
    """The merge gate's rule: an empty origin record excludes nothing."""
    checks = release_train.read_checks(
        [_check("Toolkit (pytest)", 1),
         _check("something external", 99, status="in_progress",
                conclusion=None)],
        [_run(1, CI_PATH)],
    )
    assert checks.state == "pending"
    assert "something external" in checks.detail

    blind = release_train.read_checks(
        [_check("call / fix PR #2325", 2, status="in_progress",
                conclusion=None)],
        [],
    )
    assert blind.state == "pending", "no origin record → nothing is ignored"


def test_the_no_op_and_refuse_lines_name_what_was_read_and_what_was_ignored():
    fixture = _fixture()
    checks = release_train.read_checks(fixture["check_runs"],
                                       fixture["workflow_runs"])
    said = checks.describe()
    assert "Console backend (pytest)" in said
    assert "agent-fix.yml" in said and "medic.yml" in said
    assert "ignored" in said.lower() and "read" in said.lower()

    decision = release_train.decide(
        surface(), pt(2026, 9, 6, 11, 36), None, "behind", checks, None,
        dispatched=True,
    )
    assert "agent-fix.yml" in decision.reason, decision.reason

    pending = release_train.read_checks(
        [_check("Console backend (pytest)", 1, status="in_progress",
                conclusion=None),
         _check("call / fix PR #2325", 2, status="in_progress",
                conclusion=None)],
        [_run(1, CI_PATH), _run(2, FIX_PATH, event="workflow_dispatch")],
    )
    no_op = release_train.decide(
        surface(), pt(2026, 7, 15, 10, 0), None, "behind", pending, None
    )
    assert no_op.act == release_train.NO_OP
    assert "Console backend (pytest)" in no_op.reason
    assert "agent-fix.yml" in no_op.reason

    red = release_train.read_checks(
        [_check("Console backend (pytest)", 1, conclusion="failure"),
         _check("call / fix PR #2325", 2, status="in_progress",
                conclusion=None)],
        [_run(1, CI_PATH), _run(2, FIX_PATH, event="workflow_dispatch")],
    )
    refused = release_train.decide(
        surface(), pt(2026, 7, 15, 10, 0), None, "behind", red, None
    )
    assert "agent-fix.yml" in refused.reason


def test_the_gating_set_is_the_merge_gates_from_one_place():
    import merge_gate

    assert release_train.read_checks is not None
    # The classifier is the merge gate's; the train adds only itself.
    assert release_train.gating_check_runs is merge_gate.gating_check_runs
    assert set(merge_gate.DEFAULT_REVIEW_WORKFLOWS) <= set(
        release_train.IGNORED_WORKFLOWS)
    assert TRAIN_PATH in release_train.IGNORED_WORKFLOWS
    assert {"push", "pull_request", "pull_request_target"} <= set(
        merge_gate.COMMIT_EVENTS)
    for never in ("workflow_run", "issue_comment", "workflow_dispatch",
                  "schedule", "repository_dispatch"):
        assert never not in merge_gate.COMMIT_EVENTS


def test_the_train_never_polls_a_check():
    for gone in ("poll_checks", "CHECK_WAIT_MINUTES", "POLL_SECONDS"):
        assert not hasattr(release_train, gone), gone


def test_a_failed_check_is_never_waited_for():
    """`fetch_checks` reads GitHub once and answers; there is no loop."""
    checks = release_train.read_checks(
        [_check("Toolkit (pytest)", 1, conclusion="failure")], [_run(1, CI_PATH)]
    )
    assert checks.state == "red"


def test_fetch_checks_reads_both_records_once_through_gh(tmp_path, monkeypatch):
    """The seam the live bug hid in: the two `gh api` reads, streamed one
    object per line, land in `read_checks` in the shapes the classifier
    expects — and are read exactly once each."""
    fixture = _fixture()
    calls = tmp_path / "calls.log"
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "gh").write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$*\" >> {calls}\n"
        "case \"$*\" in\n"
        "  *check-runs*) python3 -c 'import json,sys; "
        f"[print(json.dumps(c)) for c in json.load(open(\"{FIXTURE}\"))[\"check_runs\"]]' ;;\n"
        "  *actions/runs*) python3 -c 'import json,sys; "
        f"[print(json.dumps(r)) for r in json.load(open(\"{FIXTURE}\"))[\"workflow_runs\"]]' ;;\n"
        "  *) echo unexpected >&2; exit 9 ;;\n"
        "esac\n"
    )
    (fake / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")

    checks = release_train.fetch_checks("dreadnought-foundry/agent-bureau",
                                        fixture["sha"])
    assert checks.state == "green", checks.detail
    assert len(checks.read) == 11
    assert len(checks.ignored) == len(fixture["check_runs"]) - 11
    logged = calls.read_text().splitlines()
    assert len(logged) == 2, logged
    assert all("--paginate" in line for line in logged)
    assert "check_suite" in logged[0] and "head_sha=" in logged[1]


def test_the_cli_exits_0_on_a_pending_gating_check_and_cuts_no_tag(tmp_path,
                                                                    monkeypatch,
                                                                    capsys):
    repo = _fake_repo(tmp_path)
    pending = release_train.read_checks(
        [_check("Toolkit (pytest)", 1, status="in_progress", conclusion=None)],
        [_run(1, CI_PATH)],
    )
    monkeypatch.setattr(release_train, "fetch_checks", lambda repo, sha: pending)
    code = release_train.main([
        "--repo", "dreadnought-foundry/demo", "--repo-root", str(repo),
        "--file", str(repo / ".github" / "bureau" / "release.json"),
        "release", "--sha", _head(repo), "--surface", "demo",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert f"{release_train.TAG}: no-op" in out
    assert "Toolkit (pytest)" in out
    assert _git(repo, "tag", "-l") == ""


# --------------------------------------------------------------------------
# The addendum: the two scheduled firings, read on the PT clock.
# --------------------------------------------------------------------------

def test_the_two_scheduled_firings_decide_by_the_pacific_clock():
    console = surface()
    # `- cron: "0 14 * * *"` in January: 14:00 UTC is 06:00 PST.
    winter = datetime(2026, 1, 15, 14, 0, tzinfo=UTC)
    early = release_train.decide(console, winter, None, "behind", green(), None)
    assert early.act == release_train.NO_OP
    assert early.code == "window"
    assert "07:00-21:00 PT" in early.reason
    assert "06:00" in early.reason

    # `- cron: "0 15 * * *"` in July: 15:00 UTC is 08:00 PDT.
    summer = datetime(2026, 7, 15, 15, 0, tzinfo=UTC)
    ordinary = release_train.decide(
        console, summer, None, "behind", green(), None
    )
    assert ordinary.act == release_train.RELEASE, ordinary.reason


def test_an_08_00_pdt_firing_on_a_current_surface_is_still_a_no_op():
    summer = datetime(2026, 7, 15, 15, 0, tzinfo=UTC)
    decision = release_train.decide(
        surface(), summer, summer - timedelta(days=1), "current", green(), None
    )
    assert decision.act == release_train.NO_OP
    assert decision.code == "current"


def test_the_clock_is_read_in_america_los_angeles():
    assert str(release_train.PT) == "America/Los_Angeles"
    # A naive datetime is a bug waiting to happen — the train refuses one.
    with pytest.raises(ValueError):
        release_train.decide(
            surface(), datetime(2026, 7, 15, 10, 0), None, "behind", green(), None
        )


# --------------------------------------------------------------------------
# The addendum: `record: channel`, and this repo's own declaration.
# --------------------------------------------------------------------------

def test_this_repo_declares_pipeline_channel_as_a_channel_record():
    data = release_train.load(OWN_DATA)
    assert release_train.check_schema(data, repo_root=ROOT) == []
    channel = release_train.surfaces(data)["pipeline-channel"]
    assert channel.record == "channel"
    assert channel.tag_series == ["stable"]
    assert channel.paths == []
    assert channel.script is None
    assert channel.auto is False


def test_the_train_never_runs_a_channel_surface():
    channel = release_train.surface(
        "pipeline-channel", _good_surface_data(
            record="channel", script=None, rollback=None, paths=[],
            auto=False, tag_series=["stable"],
        ),
    )
    decision = release_train.decide(
        channel, pt(2026, 7, 15, 10, 0), None, "behind", green(), None,
        dispatched=True,
    )
    assert decision.act == release_train.NO_OP
    assert decision.code == "channel"
    assert "channel" in decision.reason


def test_a_channel_surface_that_names_a_script_is_refused():
    data = {"surfaces": {"pipeline-channel": _good_surface_data(
        record="channel", script="infra/release-channel.sh", rollback=None,
        auto=False,
    )}}
    problems = release_train.check_schema(data)
    assert any("script" in p and "channel" in p for p in problems), problems


@pytest.mark.parametrize("field", ["script", "rollback"])
def test_auto_true_with_a_null_script_or_rollback_is_refused(field):
    data = {"surfaces": {"console": _good_surface_data(auto=True, **{field: None})}}
    problems = release_train.check_schema(data)
    assert any(field in p and "auto" in p for p in problems), problems


def test_null_script_and_rollback_are_allowed_while_auto_is_false():
    data = {"surfaces": {"console": _good_surface_data(
        auto=False, script=None, rollback=None)}}
    assert release_train.check_schema(data) == []


def test_a_surface_with_no_script_is_a_no_op_that_says_so():
    decision = release_train.decide(
        release_train.surface("website", _good_surface_data(
            auto=False, script=None, rollback=None)),
        pt(2026, 7, 15, 10, 0), None, "behind", green(), None, dispatched=True,
    )
    assert decision.act == release_train.NO_OP
    assert decision.code == "no-script"


# --------------------------------------------------------------------------
# The addendum: paths untouched since the newest tag; a deferral is not a
# failure; the receipt is the tag. Driven over a REAL git repository.
# --------------------------------------------------------------------------

def _git(repo, *args, **kw):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        text=True, **kw,
    ).stdout.strip()


def _head(repo):
    return _git(repo, "rev-parse", "HEAD")


SCRIPT = """#!/usr/bin/env bash
set -euo pipefail
test "$1" = "--surface"
test -n "${RELEASE_SHA:-}"
echo "releasing $2 at $RELEASE_SHA"
git tag -a "demo/v1" -m "demo release of $RELEASE_SHA" "$RELEASE_SHA"
"""

DEFERRING_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail
echo "deferred: the console's first release is supervised by a person"
"""

TAGLESS_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail
echo "rolled out and forgot to tag"
"""

FAILING_SCRIPT = """#!/usr/bin/env bash
echo "the migration failed" >&2
exit 3
"""

LIGHTWEIGHT_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail
git tag "demo/v1" "$RELEASE_SHA"
"""

ELSEWHERE_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail
git commit -q --allow-empty -m "a commit the train never asked for"
git tag -a "demo/v1" -m "at the wrong commit" HEAD
"""


def _fake_repo(tmp_path, script=SCRIPT, paths=("demo/",)):
    repo = tmp_path / "caller"
    (repo / "infra").mkdir(parents=True)
    (repo / "demo").mkdir()
    (repo / ".github" / "bureau").mkdir(parents=True)
    (repo / "demo" / "app.txt").write_text("v1\n")
    (repo / "infra" / "release-demo.sh").write_text(script)
    (repo / "infra" / "release-demo.sh").chmod(0o755)
    (repo / ".github" / "bureau" / "release.json").write_text(json.dumps({
        "surfaces": {
            "demo": {
                "tag_series": ["demo/v*"],
                "paths": list(paths),
                "script": "infra/release-demo.sh",
                "rollback": "make rollback-demo VERSION=<tag>",
                "spacing_minutes": 30,
                "window": "always",
                "auto": True,
                "identity": "demo-release",
                "record": "tag",
            }
        }
    }, indent=2) + "\n")
    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "train@example.com")
    _git(repo, "config", "user.name", "Release Train")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the caller, as it stands")
    return repo


def _release(repo, *, sha=None, now=None, checks=None, brake=None,
             surface_name="demo", dispatched=False):
    data = release_train.load(repo / ".github" / "bureau" / "release.json")
    return release_train.release(
        release_train.surfaces(data)[surface_name],
        repo="dreadnought-foundry/demo",
        repo_root=repo,
        sha=sha or _head(repo),
        now=now or pt(2026, 7, 15, 10, 0),
        checks=checks or green(),
        brake=brake,
        dispatched=dispatched,
    )


def test_a_stub_run_produces_one_release_one_tag_and_one_receipt_line(tmp_path,
                                                                     capsys):
    repo = _fake_repo(tmp_path)
    stub = _reference_stub()
    call = next(iter(stub["jobs"].values()))
    assert call["uses"] == (
        "dreadnought-foundry/bureau-pipeline/.github/workflows/"
        "release-train.yml@stable"
    )
    assert call["with"]["pipeline_ref"] == "stable"

    sha = _head(repo)
    decision = _release(repo, sha=sha)
    assert decision.act == release_train.RELEASE, decision.reason
    assert decision.tag == "demo/v1"

    # One tag, cut by the script, annotated, at the released sha.
    assert _git(repo, "tag", "-l").splitlines() == ["demo/v1"]
    assert _git(repo, "cat-file", "-t", "demo/v1") == "tag"
    assert _git(repo, "rev-list", "-n", "1", "demo/v1") == sha

    # One receipt line, and it names the tag.
    lines = [l for l in capsys.readouterr().out.splitlines()
             if l.startswith(f"{release_train.TAG}: released")]
    assert len(lines) == 1, lines
    assert "demo/v1" in lines[0] and "demo" in lines[0]


def test_a_second_trigger_after_the_release_is_current_and_says_so(tmp_path):
    repo = _fake_repo(tmp_path)
    assert _release(repo).act == release_train.RELEASE

    later = _release(repo, now=pt(2026, 7, 15, 12, 0))
    assert later.act == release_train.NO_OP
    assert later.code == "current"
    assert "demo/" in later.reason


def test_a_surface_whose_paths_are_untouched_since_its_tag_is_a_no_op(tmp_path):
    repo = _fake_repo(tmp_path)
    assert _release(repo).act == release_train.RELEASE

    # A commit that touches nothing under `demo/`.
    (repo / "README.md").write_text("unrelated\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "docs only")

    decision = _release(repo, now=pt(2026, 7, 15, 12, 0))
    assert decision.act == release_train.NO_OP
    assert decision.code == "current"

    # …and a commit that DOES touch them is behind again.
    (repo / "demo" / "app.txt").write_text("v2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a change to the surface")
    assert release_train.lag_state(
        repo, "demo/v1", _head(repo), ["demo/"]
    ) == "behind"


def test_a_sha_the_newest_tag_already_contains_reads_current(tmp_path):
    """DRE-3263: the CI-completion trigger hands the train the SHA CI ran
    on, which can be OLDER than the newest tag when a slow CI on X finishes
    after Y was released. `diff Y..X` is non-empty in the reverse direction
    and would read X as behind — and tag backwards."""
    repo = _fake_repo(tmp_path)
    older = _head(repo)
    (repo / "demo" / "app.txt").write_text("v2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a newer change to the surface")
    assert _release(repo).act == release_train.RELEASE  # tags the newer head

    assert release_train.lag_state(repo, "demo/v1", older, ["demo/"]) == "current"
    late = _release(repo, sha=older, now=pt(2026, 7, 15, 12, 0))
    assert late.act == release_train.NO_OP
    assert late.code == "current"
    assert _git(repo, "tag", "-l").splitlines() == ["demo/v1"]


def test_a_surface_with_nothing_to_release_never_waits_for_a_check(tmp_path):
    """A job that queued behind another release and now reads current must not
    spend two API reads to learn the same thing."""
    repo = _fake_repo(tmp_path)
    assert _release(repo).act == release_train.RELEASE

    def never():
        pytest.fail("the checks API was asked about a surface that is current")

    data = release_train.load(repo / ".github" / "bureau" / "release.json")
    decision = release_train.release(
        release_train.surfaces(data)["demo"], repo="dreadnought-foundry/demo",
        repo_root=repo, sha=_head(repo), now=pt(2026, 7, 15, 12, 0),
        checks=never, brake=None,
    )
    assert decision.code == "current"


def test_a_script_that_defers_is_a_no_op_with_no_tag_and_no_failure(tmp_path):
    repo = _fake_repo(tmp_path, script=DEFERRING_SCRIPT)
    decision = _release(repo)
    assert decision.act == release_train.NO_OP
    assert decision.code == "deferred"
    assert decision.tag is None
    assert decision.ok is True
    assert "the console's first release is supervised by a person" in decision.reason
    assert _git(repo, "tag", "-l") == ""


def test_a_script_that_cuts_no_tag_is_a_failure(tmp_path):
    repo = _fake_repo(tmp_path, script=TAGLESS_SCRIPT)
    decision = _release(repo)
    assert decision.act == release_train.REFUSE
    assert decision.ok is False
    assert "tag" in decision.reason


def test_a_lightweight_tag_is_refused__the_record_carries_the_annotation(tmp_path):
    repo = _fake_repo(tmp_path, script=LIGHTWEIGHT_SCRIPT)
    decision = _release(repo)
    assert decision.act == release_train.REFUSE
    assert decision.code == "tag-not-annotated"


def test_a_tag_that_points_somewhere_else_is_refused(tmp_path):
    repo = _fake_repo(tmp_path, script=ELSEWHERE_SCRIPT)
    decision = _release(repo)
    assert decision.act == release_train.REFUSE
    assert decision.code == "tag-elsewhere"


def test_a_script_that_exits_non_zero_is_a_failure_that_names_the_code(tmp_path):
    repo = _fake_repo(tmp_path, script=FAILING_SCRIPT)
    decision = _release(repo)
    assert decision.act == release_train.REFUSE
    assert "3" in decision.reason


def test_the_script_is_called_as_bash_script_surface_name(tmp_path):
    repo = _fake_repo(tmp_path, script=SCRIPT)
    # The fake script asserts its own argv (`--surface demo`) and that
    # RELEASE_SHA is in its environment; a wrong call fails it.
    assert _release(repo).act == release_train.RELEASE


def test_the_plan_reads_the_head_of_the_branch_at_start(tmp_path):
    repo = _fake_repo(tmp_path)
    data = release_train.load(repo / ".github" / "bureau" / "release.json")
    sha = _head(repo)
    plan = release_train.plan(
        data, repo_root=repo, head=sha, now=pt(2026, 7, 15, 10, 0), brake=None,
        checks_for=lambda _sha: green(),
    )
    assert [name.name for name, _ in plan] == ["demo"]
    assert release_train.matrix(plan) == [{"surface": "demo", "sha": sha}]

    # A later commit does not change the plan already made — the collapse rule
    # is "head of the branch at start".
    (repo / "demo" / "app.txt").write_text("v3\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a push that lands mid-run")
    assert _release(repo, sha=sha).tag == "demo/v1"
    assert _git(repo, "rev-list", "-n", "1", "demo/v1") == sha


def test_a_checks_api_that_cannot_answer_is_a_refusal_not_a_release(tmp_path,
                                                                    monkeypatch,
                                                                    capsys):
    """UNKNOWN is never clear — the rule `check_train_in_flight.py` states."""
    repo = _fake_repo(tmp_path)
    monkeypatch.setattr(release_train, "fetch_checks", _cannot_answer)
    code = release_train.main([
        "--repo", "dreadnought-foundry/demo", "--repo-root", str(repo),
        "--file", str(repo / ".github" / "bureau" / "release.json"),
        "release", "--sha", _head(repo), "--surface", "demo",
    ])
    assert code == 1
    assert f"{release_train.TAG}: refuse" in capsys.readouterr().out
    assert _git(repo, "tag", "-l") == ""


def _cannot_answer(repo, sha):
    raise RuntimeError("gh could not read the checks")


def test_the_cli_refuses_a_malformed_file_and_names_the_field(tmp_path, capsys):
    bad = tmp_path / "release.json"
    bad.write_text(json.dumps({"surfaces": {"console": {"auto": True}}}))
    assert release_train.main(["--file", str(bad), "schema"]) == 1
    assert "console.window" in capsys.readouterr().out


# --------------------------------------------------------------------------
# The workflow and the stub — data plus one `uses:` line.
# --------------------------------------------------------------------------

def _workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def _on(doc):
    # YAML 1.1 parses the bare key `on` as boolean True.
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _reference_stub():
    """The stub every caller carries, published in `standards/release-train.md`
    so DRE-3211 copies it rather than re-deriving it."""
    blocks = re.findall(r"```yaml\n(.*?)```", STANDARD.read_text(), re.S)
    for block in blocks:
        if "release-train.yml@stable" in block:
            return yaml.safe_load(block)
    raise AssertionError("standards/release-train.md carries no stub block")


def test_the_train_is_a_reusable_workflow_in_the_shape_of_the_other_eight():
    on = _on(_workflow())
    assert set(on) == {"workflow_call"}
    inputs = on["workflow_call"]["inputs"]
    assert inputs["pipeline_ref"]["required"] is True
    assert inputs["pipeline_ref"]["type"] == "string"
    assert "default" not in inputs["pipeline_ref"]
    assert inputs["surface"]["required"] is False
    secrets = on["workflow_call"]["secrets"]
    assert secrets["RELEASE_ROLE_ARN"]["required"] is True
    assert secrets["LINEAR_RELEASE_KEY"]["required"] is False
    assert secrets["LINEAR_API_KEY"]["required"] is False


def test_the_per_surface_job_runs_in_its_own_lane():
    jobs = _workflow()["jobs"]
    release = jobs["release"]
    group = release["concurrency"]["group"]
    assert "release:" in group
    assert "github.repository" in group
    assert "matrix.surface" in group
    assert release["concurrency"]["cancel-in-progress"] is False


def test_the_brake_is_read_before_any_surface_runs():
    jobs = _workflow()["jobs"]
    assert "release" in jobs and jobs["release"]["needs"] == "plan"
    body = yaml.dump(jobs["plan"])
    assert "vars.RELEASE_HOLD" in body, (
        "the fleet brake must be read in the job that builds the matrix, "
        "before any surface job exists"
    )


def test_the_train_checks_out_the_pipeline_at_the_ref_the_caller_pinned():
    text = WORKFLOW.read_text()
    assert "${{ inputs.pipeline_ref || 'main' }}" in text
    assert text.count("dreadnought-foundry/bureau-pipeline") >= 2


def test_the_stub_is_data_plus_one_uses_line():
    stub = _reference_stub()
    assert len(stub["jobs"]) == 1
    job = next(iter(stub["jobs"].values()))
    assert set(job) <= {"uses", "with", "secrets", "permissions"}
    assert job["secrets"]["RELEASE_ROLE_ARN"]
    assert job["with"]["surface"] == "${{ inputs.surface }}"


def test_the_stub_fires_on_ci_completion_the_schedule_and_a_dispatch():
    """DRE-3263: a push fires BEFORE that commit's CI starts, so under the
    never-stopped rule every push-run would find CI pending and no-op. The
    trigger that makes the rule true is CI completing on main."""
    on = _on(_reference_stub())
    assert "push" not in on, (
        "a push-triggered train always finds its own commit's CI pending"
    )
    assert on["workflow_run"] == {
        "workflows": ["CI"], "types": ["completed"], "branches": ["main"],
    }
    crons = [entry["cron"] for entry in on["schedule"]]
    assert crons == ["0 15 * * *", "0 14 * * *"]
    assert "surface" in on["workflow_dispatch"]["inputs"]
    assert set(on) == {"workflow_run", "schedule", "workflow_dispatch"}


def test_the_stub_carries_no_paths_filter():
    on = _on(_reference_stub())
    assert "paths" not in on["workflow_run"], (
        "YAML cannot read the data — path filtering is the train's job"
    )


def test_the_train_reads_the_sha_and_the_conclusion_from_the_workflow_run_event():
    """The reusable workflow accepts the CI-completion event: it releases
    the SHA that CI ran on, and only when CI concluded success."""
    text = WORKFLOW.read_text()
    assert "github.event.workflow_run.head_sha" in text
    assert "github.event.workflow_run.conclusion" in text
    plan = yaml.dump(_workflow()["jobs"]["plan"])
    assert "workflow_run" in plan
    assert "30 minutes" not in text and "thirty minutes" not in text.lower()


def test_the_stub_grants_the_three_permissions_the_train_needs():
    perms = _reference_stub()["permissions"]
    assert perms["id-token"] == "write"
    assert perms["contents"] == "write"
    assert perms["actions"] == "read"


# --------------------------------------------------------------------------
# The document renders from the schema, the way `lane_contract.py render` does.
# --------------------------------------------------------------------------

def test_the_committed_document_is_the_render():
    assert DOC.read_text() == release_train.render_markdown(), (
        "docs/release-train.md is stale — regenerate it with "
        "`python3 scripts/release_train.py render`"
    )


def test_the_render_names_every_field_of_the_schema():
    rendered = release_train.render_markdown()
    for field in release_train.SCHEMA:
        assert f"`{field.name}`" in rendered, field.name
        assert field.means.split(".")[0][:40] in rendered, field.name


def test_the_render_states_the_trigger_the_stub_must_declare():
    """DRE-3263: the callers' stubs are not in this repo, so the document
    says exactly what a stub must declare and what the train reads."""
    rendered = release_train.render_markdown()
    for needle in ("workflow_run", 'workflows: ["CI"]', "types: [completed]",
                   "branches: [main]", "github.event.workflow_run.head_sha",
                   "success"):
        assert needle in rendered, needle
    assert "ci-pending" in rendered
    assert "30 minutes" not in rendered
    # The decision table now says a pending gating check is a no-op.
    row = next(line for line in rendered.splitlines() if "`ci-pending`" in line)
    assert "`no-op`" in row, row


def test_the_standard_states_what_the_stub_declares():
    body = STANDARD.read_text()
    assert "workflow_run" in body
    assert "never stopped" in body.lower()
    assert "30 minutes" not in body and "thirty minutes" not in body.lower()


def test_the_standard_states_what_a_surface_script_owes():
    body = STANDARD.read_text().lower()
    for phrase in ("migrations", "rollout", "verify", "annotat", "non-zero",
                   "deferred:"):
        assert phrase in body, f"standards/release-train.md never says {phrase!r}"


def _good_surface_data(**over):
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
    return data


def test_the_environment_the_script_is_handed_carries_the_release_sha(tmp_path):
    repo = _fake_repo(tmp_path)
    env = release_train.script_env(
        {"PATH": os.environ["PATH"]}, sha="a" * 40, surface_name="demo"
    )
    assert env["RELEASE_SHA"] == "a" * 40
    assert env["RELEASE_SURFACE"] == "demo"


# --------------------------------------------------------------------------
# DRE-3266: ready is a COMMIT, not the head. The train releases the newest
# commit on the default branch whose gating checks are all green and which is
# newer than the surface's deployed tag; a still-checking head is stepped
# past and rides the next train. The live fixture is agent-bureau run
# 34058913763 at 13:44 PT on 2026-09-06.
# --------------------------------------------------------------------------

WALK_FIXTURE = ROOT / "tests" / "fixtures" / "release-train-43c86b4a9-2026-09-06.json"


def _walk_fixture():
    return json.loads(WALK_FIXTURE.read_text())


class _Reader:
    """`checks_for(sha)` that answers from a table and records every read —
    the read-call bound is part of the contract."""

    def __init__(self, by_sha):
        self.by_sha = by_sha
        self.reads = []

    def __call__(self, sha):
        self.reads.append(sha)
        answer = self.by_sha[sha]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _pending(name="Console backend (pytest)"):
    return release_train.read_checks(
        [_check(name, 1, status="in_progress", conclusion=None)],
        [_run(1, CI_PATH)],
    )


def _red(name="Console backend (pytest)"):
    return release_train.read_checks(
        [_check(name, 1, conclusion="failure")], [_run(1, CI_PATH)],
    )


def _commit(repo, path="demo/app.txt", text=None, message="a change"):
    """One more commit on main touching `path`; returns its sha."""
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text or f"{message}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _head(repo)


def _tag_head(repo, name="demo/v0"):
    """The deployed tag, dated an hour before the tests' 10:00 PT clock so
    the surface's spacing has elapsed and the walk is what decides."""
    _git(repo, "tag", "-a", name, "-m", "released",
         env={**os.environ, "GIT_COMMITTER_DATE": "2026-07-15T09:00:00-07:00"})


def _plan(repo, reader, *, head=None, now=None, bound=None, brake=None,
          dispatched_surface=None):
    kwargs = {}
    if bound is not None:
        kwargs["bound"] = bound
    return release_train.plan(
        release_train.load(repo / ".github" / "bureau" / "release.json"),
        repo_root=repo, head=head or _head(repo),
        now=now or pt(2026, 7, 15, 10, 0), brake=brake,
        dispatched_surface=dispatched_surface, checks_for=reader, **kwargs,
    )


def _only(plan):
    [(entry, decision)] = plan
    return decision


def test_the_13_44_pt_fixture_releases_the_green_parent_behind_a_still_checking_head(
        tmp_path, monkeypatch):
    """The run the CEO watched: main's head 43c86b4a9 merged a minute earlier
    with its CI still running, its first parent 6b52674d7 green since 12:31
    PT, and the deployed tag twenty first-parent commits back. The train said
    `not ready` and left empty. Under the amended rule it releases the
    parent and names the head as stepped past."""
    fixture = _walk_fixture()
    head, parent = fixture["head"], fixture["parent"]
    assert head.startswith("43c86b4a9") and parent.startswith("6b52674d7")
    assert fixture["first_parent_distance"] <= release_train.WALK_BOUND, (
        "the live case must sit inside the walk bound or the fix is theory"
    )
    reader = _Reader({
        sha: release_train.read_checks(rec["check_runs"], rec["workflow_runs"])
        for sha, rec in fixture["checks"].items()
    })
    assert reader.by_sha[head].state == "pending"
    assert reader.by_sha[parent].state == "green"

    # The candidate walk is the caller's git; here the real shas stand in
    # for it so the decision names the commits the CEO watched.
    monkeypatch.setattr(release_train, "candidates",
                        lambda repo_root, head_, tag, bound: ([head, parent], False))
    monkeypatch.setattr(release_train, "lag_state",
                        lambda repo_root, tag, sha, paths: "behind")
    repo = _fake_repo(tmp_path)
    data = {"surfaces": {"console": _good_surface_data(auto=False)}}
    plan = release_train.plan(
        data, repo_root=repo, head=head, now=pt(2026, 9, 6, 13, 44),
        brake=None, dispatched_surface="console", checks_for=reader,
    )
    decision = _only(plan)
    assert decision.act == release_train.RELEASE, decision.reason
    assert decision.sha == parent
    assert reader.reads == [head, parent]
    assert parent[:7] in decision.reason
    assert head[:7] in decision.reason
    assert "Console backend (pytest)" in decision.reason
    assert "in_progress" in decision.reason
    assert release_train.matrix(plan) == [{"surface": "console", "sha": parent}]


def test_a_green_head_releases_the_head_and_reads_one_commit(tmp_path):
    repo = _fake_repo(tmp_path)
    _tag_head(repo)
    older = _commit(repo, message="older, green")
    head = _commit(repo, message="head, green")
    reader = _Reader({head: green(), older: green()})
    decision = _only(_plan(repo, reader))
    assert decision.act == release_train.RELEASE
    assert decision.sha == head
    assert reader.reads == [head], "the walk stops at the first green commit"


def test_a_pending_head_is_stepped_past_and_the_green_parent_is_released(tmp_path):
    repo = _fake_repo(tmp_path)
    _tag_head(repo)
    parent = _commit(repo, message="parent, green")
    head = _commit(repo, message="head, still checking")
    reader = _Reader({head: _pending(), parent: green()})
    decision = _only(_plan(repo, reader))
    assert decision.act == release_train.RELEASE, decision.reason
    assert decision.sha == parent
    assert reader.reads == [head, parent]
    assert head[:7] in decision.reason and "in_progress" in decision.reason


def test_a_red_head_is_stepped_past_and_named(tmp_path):
    repo = _fake_repo(tmp_path)
    _tag_head(repo)
    parent = _commit(repo, message="parent, green")
    head = _commit(repo, message="head, red")
    reader = _Reader({head: _red("Toolkit (pytest)"), parent: green()})
    decision = _only(_plan(repo, reader))
    assert decision.act == release_train.RELEASE, decision.reason
    assert decision.sha == parent
    assert head[:7] in decision.reason
    assert "Toolkit (pytest)" in decision.reason and "failure" in decision.reason


def test_everything_pending_is_a_no_op_naming_the_newest(tmp_path):
    repo = _fake_repo(tmp_path)
    _tag_head(repo)
    a = _commit(repo, message="a")
    b = _commit(repo, message="b")
    c = _commit(repo, message="c, the newest")
    reader = _Reader({a: _pending("A"), b: _pending("B"), c: _pending("C")})
    plan = _plan(repo, reader)
    decision = _only(plan)
    assert decision.act == release_train.NO_OP, decision.reason
    assert decision.code == "ci-pending"
    assert decision.ok is True
    assert decision.sha is None
    assert c[:7] in decision.reason and "`C`" in decision.reason
    assert "next" in decision.reason.lower()
    assert reader.reads == [c, b, a], "every candidate is read, once"
    assert release_train.matrix(plan) == []


def test_a_red_only_candidate_set_refuses_by_name(tmp_path):
    repo = _fake_repo(tmp_path)
    _tag_head(repo)
    a = _commit(repo, message="a, red")
    b = _commit(repo, message="b, red")
    reader = _Reader({a: _red("A"), b: _red("B")})
    decision = _only(_plan(repo, reader))
    assert decision.act == release_train.REFUSE
    assert decision.code == "ci-red"
    assert a[:7] in decision.reason and b[:7] in decision.reason
    assert "`A`" in decision.reason and "`B`" in decision.reason


def test_a_red_head_over_a_pending_parent_is_a_no_op_that_names_both(tmp_path):
    """A red commit is never released and the line says so; a pending one
    behind it is what the next train takes — so this is not a refusal."""
    repo = _fake_repo(tmp_path)
    _tag_head(repo)
    parent = _commit(repo, message="parent, checking")
    head = _commit(repo, message="head, red")
    reader = _Reader({head: _red("Toolkit (pytest)"), parent: _pending("CI")})
    decision = _only(_plan(repo, reader))
    assert decision.act == release_train.NO_OP, decision.reason
    assert decision.code == "ci-pending"
    assert parent[:7] in decision.reason and "`CI`" in decision.reason
    assert head[:7] in decision.reason and "failure" in decision.reason


def test_nothing_newer_than_the_tag_is_current_and_reads_no_check(tmp_path):
    repo = _fake_repo(tmp_path)
    _tag_head(repo)
    reader = _Reader({})
    decision = _only(_plan(repo, reader))
    assert decision.act == release_train.NO_OP
    assert decision.code == "current"
    assert reader.reads == []


def test_the_walk_bound_exceeded_refuses_by_name(tmp_path):
    """The bound caps commits EVALUATED, newest first, not the distance to
    the tag: a surface whose newest thirty commits hold no green one is too
    far behind to walk, and the refusal says so by name."""
    assert release_train.WALK_BOUND == 30
    repo = _fake_repo(tmp_path)
    _tag_head(repo)
    shas = [_commit(repo, message=f"commit {i}") for i in range(5)]
    head = shas[-1]
    reader = _Reader({sha: _pending("CI") for sha in shas})
    decision = _only(_plan(repo, reader, bound=3))
    assert decision.act == release_train.REFUSE, decision.reason
    assert decision.code == "walk-bound"
    assert "too far behind" in decision.reason
    assert "3" in decision.reason and head[:7] in decision.reason
    assert reader.reads == [shas[4], shas[3], shas[2]], "bounded, newest first"


def test_a_green_commit_inside_the_bound_releases_however_far_back_the_tag_is(
        tmp_path):
    """The live tag was twenty first-parent commits back (ninety-five by
    `compare`); what matters is that a green commit sits inside the bound."""
    repo = _fake_repo(tmp_path)
    _tag_head(repo)
    shas = [_commit(repo, message=f"commit {i}") for i in range(6)]
    table = {sha: _pending("CI") for sha in shas}
    table[shas[4]] = green()
    reader = _Reader(table)
    decision = _only(_plan(repo, reader, bound=3))
    assert decision.act == release_train.RELEASE, decision.reason
    assert decision.sha == shas[4]
    assert reader.reads == [shas[5], shas[4]]


def test_the_walk_follows_the_first_parent_line(tmp_path):
    """A merge's branch commits are not candidates: the train releases what
    landed on the default branch, in the order it landed."""
    repo = _fake_repo(tmp_path)
    _tag_head(repo)
    _git(repo, "checkout", "-q", "-b", "feature")
    b1 = _commit(repo, message="branch commit 1")
    b2 = _commit(repo, message="branch commit 2")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge feature", "feature")
    merge = _head(repo)
    shas, more = release_train.candidates(repo, merge, "demo/v0")
    assert shas == [merge]
    assert more is False
    reader = _Reader({merge: green(), b1: green(), b2: green()})
    decision = _only(_plan(repo, reader))
    assert decision.sha == merge
    assert reader.reads == [merge]


def test_a_candidate_that_reads_current_ends_the_walk(tmp_path):
    """Older than a commit that touches none of the surface's paths, every
    commit reads current too — nothing below it is worth a read."""
    repo = _fake_repo(tmp_path)
    _tag_head(repo)
    untouched = _commit(repo, path="infra/notes.txt", message="not the surface")
    head = _commit(repo, message="the surface, still checking")
    reader = _Reader({head: _pending("CI"), untouched: green()})
    decision = _only(_plan(repo, reader))
    assert decision.act == release_train.NO_OP
    assert decision.code == "ci-pending"
    assert head[:7] in decision.reason
    assert reader.reads == [head]


def test_the_chosen_sha_flows_to_the_script_as_release_sha_and_the_tag_is_cut_on_it(
        tmp_path):
    """End to end on the fake caller: the plan chooses the green parent, the
    surface releases it, the tag lands on it — and the next plan compares the
    tag against the head, finds only the still-checking head newer than it,
    and leaves it for the train its CI completion fires."""
    repo = _fake_repo(tmp_path)
    parent = _head(repo)
    head = _commit(repo, message="head, still checking")
    reader = _Reader({head: _pending("Toolkit (pytest)"), parent: green()})
    plan = _plan(repo, reader)
    assert release_train.matrix(plan) == [{"surface": "demo", "sha": parent}]

    released = _release(repo, sha=parent, checks=green())
    assert released.act == release_train.RELEASE, released.reason
    assert released.tag == "demo/v1"
    assert _git(repo, "rev-list", "-n", "1", "demo/v1") == parent

    # The script cut demo/v1 on the real clock, so the next plan's clock is
    # an hour past it — the spacing has elapsed and the walk decides.
    again = _Reader({head: _pending("Toolkit (pytest)")})
    later = datetime.now(tz=release_train.PT) + timedelta(hours=1)
    decision = _only(_plan(repo, again, now=later))
    assert decision.act == release_train.NO_OP, decision.reason
    assert decision.code == "ci-pending"
    assert head[:7] in decision.reason
    assert again.reads == [head], "the released parent is behind the tag now"


def test_the_plan_reads_no_check_for_a_surface_that_would_not_release(tmp_path):
    repo = _fake_repo(tmp_path)
    _commit(repo, message="something newer")
    never = _Reader({})
    assert _only(_plan(repo, never, brake="2026-09-06")).act == release_train.HELD
    assert never.reads == []
    data = json.loads((repo / ".github" / "bureau" / "release.json").read_text())
    data["surfaces"]["demo"]["auto"] = False
    (repo / ".github" / "bureau" / "release.json").write_text(json.dumps(data))
    assert _only(_plan(repo, never)).code == "auto-false"
    assert never.reads == []


def test_a_checks_api_that_cannot_answer_mid_walk_refuses_the_surface(tmp_path):
    repo = _fake_repo(tmp_path)
    _tag_head(repo)
    parent = _commit(repo, message="parent")
    head = _commit(repo, message="head")
    reader = _Reader({head: _pending("CI"),
                      parent: RuntimeError("gh could not read the checks")})
    decision = _only(_plan(repo, reader))
    assert decision.act == release_train.REFUSE
    assert decision.code == "unreadable"
    assert "gh could not read" in decision.reason


def test_the_plan_cli_log_names_the_chosen_commit_and_every_skipped_one(
        tmp_path, monkeypatch, capsys):
    repo = _fake_repo(tmp_path)
    _tag_head(repo)
    chosen = _commit(repo, message="chosen, green")
    red = _commit(repo, message="red")
    head = _commit(repo, message="head, checking")
    table = {head: _pending("Console backend (pytest)"),
             red: _red("Toolkit (pytest)"), chosen: green()}
    monkeypatch.setattr(release_train, "fetch_checks",
                        lambda repo_, sha: table[sha])
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    code = release_train.main([
        "--repo", "dreadnought-foundry/demo", "--repo-root", str(repo),
        "--file", str(repo / ".github" / "bureau" / "release.json"),
        "plan", "--head", head,
    ])
    out = capsys.readouterr().out
    assert code == 0
    line = next(l for l in out.splitlines() if f"{release_train.TAG}: release" in l)
    assert chosen[:7] in line
    assert head[:7] in line and "Console backend (pytest)" in line
    assert red[:7] in line and "Toolkit (pytest)" in line and "failure" in line
    emitted = output.read_text()
    assert f'"sha": "{chosen}"' in emitted or f'"sha":"{chosen}"' in emitted
    assert f"head={head}" in emitted


def test_the_plan_cli_exits_non_zero_on_a_refusal(tmp_path, monkeypatch, capsys):
    repo = _fake_repo(tmp_path)
    _tag_head(repo)
    head = _commit(repo, message="head, red")
    monkeypatch.setattr(release_train, "fetch_checks",
                        lambda repo_, sha: _red("Toolkit (pytest)"))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "github_output"))
    code = release_train.main([
        "--repo", "dreadnought-foundry/demo", "--repo-root", str(repo),
        "--file", str(repo / ".github" / "bureau" / "release.json"),
        "plan", "--head", head,
    ])
    assert code == 1
    out = capsys.readouterr().out
    assert f"{release_train.TAG}: refuse" in out and head[:7] in out
    assert "matrix=[]" in (tmp_path / "github_output").read_text()


def test_the_surface_job_checks_out_the_chosen_sha_not_the_head():
    """The plan chooses per surface, so the matrix carries the sha and every
    downstream reference is `matrix.sha` — the checkout the script runs in,
    the `--sha` it is handed. The plan job reads checks now, so it carries
    the token; the head-vs-CI-sha refusal is gone because they legitimately
    differ."""
    doc = _workflow()
    text = WORKFLOW.read_text()
    release = doc["jobs"]["release"]
    assert release["strategy"]["matrix"] == {
        "include": "${{ fromJSON(needs.plan.outputs.matrix) }}"
    }
    checkout = release["steps"][0]
    assert checkout["with"]["ref"] == "${{ matrix.sha }}"
    run = next(s for s in release["steps"] if s.get("name") == "Run the surface")
    assert run["env"]["PLAN_SHA"] == "${{ matrix.sha }}"
    assert "needs.plan.outputs.sha" not in text

    plan_job = doc["jobs"]["plan"]
    decide = next(s for s in plan_job["steps"] if s.get("id") == "plan")
    assert decide["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "--head" in decide["run"]
    assert "but CI ran on" not in text
    # The plan checks out the default branch's head on every event, and the
    # CI-completion event's sha is a candidate by construction.
    assert "ref" not in (plan_job["steps"][0].get("with") or {})
    assert "github.event.workflow_run.head_sha" in text


def test_the_render_states_that_ready_is_a_commit_not_the_head():
    rendered = release_train.render_markdown()
    for needle in ("newest green", "first-parent", "DRE-3266", "walk-bound",
                   "stepped past"):
        assert needle in rendered, needle
    row = next(line for line in rendered.splitlines() if "`walk-bound`" in line)
    assert "`refuse`" in row, row
    row = next(line for line in rendered.splitlines() if "`ci-pending`" in line)
    assert "`no-op`" in row and "newest" in row, row


def test_the_standard_says_ready_is_a_commit():
    body = STANDARD.read_text()
    assert "ready is a commit" in body.lower()
    assert "DRE-3266" in body
    assert "dispatch again once CI has answered" not in body
