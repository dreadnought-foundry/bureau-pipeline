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
  * a pending check is waited for and refused after 30 minutes, named;
  * a script that exits 0 printing `deferred: …` is a no-op, no tag, no
    failure;
  * a surface whose `paths` are untouched since its newest tag is a no-op;
  * the two scheduled firings decide by the `America/Los_Angeles` clock — the
    06:00 PST one names the window, the 08:00 PDT one is an ordinary run.

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
        repo_root=repo, sha=_head(repo), now=pt(2026, 7, 15, 10, 0),
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
# The addendum: pending checks are waited for, then refused, named.
# --------------------------------------------------------------------------

def _pending(name="console image build"):
    return [
        {"name": "ci", "status": "completed", "conclusion": "success"},
        {"name": name, "status": "in_progress", "conclusion": None},
    ]


def test_a_pending_check_is_waited_for_and_then_green():
    clock = iter([0, 5, 10])
    answers = iter([_pending(), _pending(),
                    [{"name": "ci", "status": "completed",
                      "conclusion": "success"},
                     {"name": "console image build", "status": "completed",
                      "conclusion": "success"}]])
    slept = []
    checks = release_train.poll_checks(
        lambda: next(answers), elapsed=lambda: next(clock), sleep=slept.append
    )
    assert checks.state == "green"
    assert slept, "a pending check is polled, not read once"


def test_a_pending_check_is_refused_after_thirty_minutes_with_the_check_named():
    clock = iter([0, 10, 20, 30])
    checks = release_train.poll_checks(
        lambda: _pending(), elapsed=lambda: next(clock), sleep=lambda s: None
    )
    assert checks.state == "pending"
    assert "console image build" in checks.detail
    assert release_train.CHECK_WAIT_MINUTES == 30

    refused = release_train.decide(
        surface(), pt(2026, 7, 15, 10, 0), None, "behind", checks, None
    )
    assert refused.act == release_train.REFUSE
    assert "console image build" in refused.reason
    assert "30" in refused.reason


def test_a_failed_check_is_never_waited_for():
    calls = []

    def fetch():
        calls.append(1)
        return [{"name": "ci", "status": "completed", "conclusion": "failure"}]

    checks = release_train.poll_checks(
        fetch, elapsed=lambda: 0, sleep=lambda s: pytest.fail("slept on a red check")
    )
    assert checks.state == "red"
    assert len(calls) == 1


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


def test_a_surface_with_nothing_to_release_never_waits_for_a_check(tmp_path):
    """A job that queued behind another release and now reads current must not
    spend thirty minutes on the checks API to learn the same thing."""
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
        data, repo_root=repo, sha=sha, now=pt(2026, 7, 15, 10, 0), brake=None
    )
    assert [name.name for name, _ in plan] == ["demo"]
    assert release_train.matrix(plan) == ["demo"]

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


def test_the_stub_carries_the_two_cron_lines_and_no_paths_filter():
    on = _on(_reference_stub())
    assert on["push"]["branches"] == ["main"]
    assert "paths" not in on["push"], (
        "YAML cannot read the data — path filtering is the train's job"
    )
    crons = [entry["cron"] for entry in on["schedule"]]
    assert crons == ["0 15 * * *", "0 14 * * *"]
    assert "surface" in on["workflow_dispatch"]["inputs"]


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
