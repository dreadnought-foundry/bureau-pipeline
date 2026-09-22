"""The release train's opening time lives in ONE place (DRE-4450).

Today the opening is written twice in every repo that has a train: the
`window` in `.github/bureau/release.json` (when it MAY release) and two
`schedule:` crons in that repo's `release-train.yml` stub (when it WAKES).
DRE-4357 moved agent-bureau to the CEO's 05:00 PT opening and not Portico, and
on 2026-09-21 Portico's train slept until 07:03 PT. This file pins the single
place, in two halves:

  * THE FLEET OPENING IS DECLARED ONCE, in `release_train.FLEET_WINDOW`. A
    surface's `release.json` may omit `window` and inherit it; the schema
    check accepts the omission; a surface that declares its own keeps it, and
    every line the train prints about that window says it OVERRIDES the fleet
    default.
  * THE FLEET WAKES ITSELF, from one workflow in this repo. GitHub runs a
    `schedule:` only from a workflow file on the default branch of the repo
    that holds it, and the reusable train is `workflow_call`, so the schedule
    cannot live in the train. It lives in `.github/workflows/fleet-wake.yml`,
    whose two crons are DERIVED from the declared opening with `zoneinfo` —
    never a restated UTC offset — and which dispatches `release-train.yml` in
    every `config/repo-map.json` repo that carries a caller stub, naming each
    repo it skipped and why.

Until the per-repo follow-up cards land, the stubs keep their own crons, so a
repo may be woken twice at the opening. The last test here is the evidence
that the second wake-up is a harmless no-op: the per-surface concurrency lane
holds it behind the first and it reads `current` (or the spacing), never a
second release.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_train  # noqa: E402

WAKE = ROOT / ".github" / "workflows" / "fleet-wake.yml"
TRAIN = ROOT / ".github" / "workflows" / "release-train.yml"
MEDIC = ROOT / ".github" / "workflows" / "self-medic.yml"
REPO_MAP = ROOT / "config" / "repo-map.json"
STANDARD = ROOT / "standards" / "release-train.md"
DOC = ROOT / "docs" / "release-train.md"

UTC = timezone.utc

#: A caller's stub, as `standards/release-train.md` publishes it — data plus
#: one `uses:` line, and a `workflow_dispatch` the fleet wake-up can reach.
CALLER_STUB = """name: Release Train
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
jobs:
  call:
    uses: dreadnought-foundry/bureau-pipeline/.github/workflows/release-train.yml@stable
"""


def _on(doc: dict) -> dict:
    # YAML 1.1 parses the bare key `on` as boolean True.
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _wake() -> dict:
    assert WAKE.is_file(), f"the fleet wake-up workflow is missing: {WAKE}"
    return yaml.safe_load(WAKE.read_text(encoding="utf-8"))


def _file_crons() -> list:
    return [entry["cron"] for entry in _on(_wake())["schedule"]]


def _surface_data(**over) -> dict:
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


def pt(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=release_train.PT)


def green():
    return release_train.read_checks(
        [{"name": "ci", "status": "completed", "conclusion": "success"}]
    )


# --------------------------------------------------------------------------
# 1. The fleet opening, declared once — and inherited by omission.
# --------------------------------------------------------------------------

def test_the_fleet_opening_is_declared_once_in_the_train():
    """The CEO, 2026-09-20: "The train should start at 5 am." One constant,
    in the train, is the whole of that decision."""
    window = release_train.FLEET_WINDOW
    assert release_train._window_ok(window), window
    assert release_train.window_bounds(window)[0] == "05:00", (
        "the fleet opens at 05:00 PT (DRE-4357, the CEO's 2026-09-20 rule)"
    )


def test_a_surface_that_omits_window_inherits_the_fleet_default():
    data = _surface_data()
    data.pop("window")
    entry = release_train.surface("console", data)
    assert entry.window == release_train.FLEET_WINDOW
    assert entry.declared_window is False


def test_the_schema_accepts_a_surface_that_omits_window():
    data = _surface_data()
    data.pop("window")
    assert release_train.check_schema({"surfaces": {"console": data}}) == []


def test_a_declared_window_is_still_refused_when_it_is_malformed():
    """Optional is not unchecked: a surface that DOES declare one still owes
    the shape, or the omission becomes a way to smuggle nonsense in."""
    data = _surface_data(window="five in the morning")
    problems = release_train.check_schema({"surfaces": {"console": data}})
    assert any("window" in p for p in problems), problems


def test_the_inherited_window_actually_gates_the_train():
    """Inheritance is load-bearing, not cosmetic: the same surface no-ops
    before the fleet opening and releases after it."""
    data = _surface_data()
    data.pop("window")
    entry = release_train.surface("console", data)

    early = release_train.decide(
        entry, pt(2026, 7, 15, 4, 30), None, "behind", green(), None)
    assert early.act == release_train.NO_OP
    assert early.code == "window"

    opened = release_train.decide(
        entry, pt(2026, 7, 15, 6, 0), None, "behind", green(), None)
    assert opened.act == release_train.RELEASE, opened.reason


def test_an_inherited_window_line_names_the_fleet_default():
    data = _surface_data()
    data.pop("window")
    entry = release_train.surface("console", data)
    decision = release_train.decide(
        entry, pt(2026, 7, 15, 4, 30), None, "behind", green(), None)
    assert release_train.FLEET_WINDOW in decision.reason
    assert "fleet default" in decision.reason
    assert "overrid" not in decision.reason, (
        "a surface that declares nothing overrides nothing"
    )


# --------------------------------------------------------------------------
# 2. A surface that declares its own window keeps it — and the line says so.
# --------------------------------------------------------------------------

def test_a_declared_window_is_kept_and_the_no_op_line_says_it_overrides():
    entry = release_train.surface("console", _surface_data(window="07:00-21:00 PT"))
    assert entry.window == "07:00-21:00 PT"
    assert entry.declared_window is True

    decision = release_train.decide(
        entry, pt(2026, 7, 15, 6, 0), None, "behind", green(), None)
    assert decision.act == release_train.NO_OP
    assert decision.code == "window"
    assert "07:00-21:00 PT" in decision.reason
    assert "overrid" in decision.reason, decision.reason
    assert release_train.FLEET_WINDOW in decision.reason, decision.reason


def test_the_release_line_of_an_overriding_surface_says_so_too():
    """A surface whose own window is OPEN never no-ops on it, so the release
    line is the only line that can carry the override."""
    entry = release_train.surface("console", _surface_data(window="07:00-21:00 PT"))
    decision = release_train.decide(
        entry, pt(2026, 7, 15, 10, 0), None, "behind", green(), None)
    assert decision.act == release_train.RELEASE
    assert "overrid" in decision.reason, decision.reason
    assert release_train.FLEET_WINDOW in decision.reason, decision.reason


def test_a_surface_inheriting_the_default_keeps_its_release_line_plain():
    data = _surface_data()
    data.pop("window")
    decision = release_train.decide(
        release_train.surface("console", data),
        pt(2026, 7, 15, 10, 0), None, "behind", green(), None)
    assert decision.act == release_train.RELEASE
    assert "overrid" not in decision.reason, decision.reason


# --------------------------------------------------------------------------
# 3. The wake-up crons, derived from the declared opening with zoneinfo.
# --------------------------------------------------------------------------

def test_the_wake_up_crons_are_derived_from_the_declared_default():
    """The whole point of the card: one edit. The crons in the workflow file
    are exactly the ones the declared opening derives, so moving one of them
    by hand — or moving the window without the file — turns this red."""
    assert _file_crons() == list(release_train.wake_crons()), (
        "fleet-wake.yml's schedule must be `release_train.wake_crons()` — "
        "the fleet opening is declared once, in release_train.FLEET_WINDOW"
    )


def test_the_derivation_reads_the_zone_and_not_a_restated_offset():
    """Pinned against the pair the fleet ran on all summer: the 07:00 PT
    opening derived `0 15 * * *` (PST) and `0 14 * * *` (PDT), which is what
    `standards/release-train.md` published. A derivation that guessed an
    offset gets this wrong."""
    assert release_train.wake_crons("07:00-21:00 PT") == (
        "0 15 * * *", "0 14 * * *")
    assert release_train.wake_crons("05:00-21:00 PT") == (
        "0 13 * * *", "0 12 * * *")
    assert release_train.wake_crons("05:30-21:00 PT") == (
        "30 13 * * *", "30 12 * * *")


def test_one_cron_moving_alone_is_a_failure():
    """The non-vacuous half of the test above: the comparison it makes does
    reject a pair with one line moved."""
    crons = list(release_train.wake_crons())
    moved = ["0 9 * * *", crons[1]]
    assert moved != list(release_train.wake_crons())


def test_the_fleet_wakes_at_the_opening_under_both_pst_and_pdt():
    """GitHub's `schedule:` takes UTC only and has no timezone field, so two
    lines are the only way to hit one local hour all year. Each is checked on
    the America/Los_Angeles clock, on a winter date and a summer one."""
    opening = release_train.window_bounds(release_train.FLEET_WINDOW)[0]
    winter, summer = set(), set()
    for cron in release_train.wake_crons():
        minute, hour = cron.split()[0:2]
        for month, seen in ((1, winter), (7, summer)):
            fires = datetime(2027, month, 15, int(hour), int(minute), tzinfo=UTC)
            seen.add(f"{fires.astimezone(release_train.PT):%H:%M}")
    assert opening in winter, f"no cron fires at {opening} PST: {winter}"
    assert opening in summer, f"no cron fires at {opening} PDT: {summer}"


# --------------------------------------------------------------------------
# 4. The fleet wake-up: who it wakes, who it skips, and what it says.
# --------------------------------------------------------------------------

def _roster():
    return {
        "agent-bureau": "dreadnought-foundry/agent-bureau",
        "portico": "dreadnought-foundry/portico",
        "bureau-pipeline": "dreadnought-foundry/bureau-pipeline",
        "demo": "dreadnought-foundry/agent-bureau-demo",
    }


def _stubs(**over):
    stubs = {
        "dreadnought-foundry/agent-bureau": CALLER_STUB,
        "dreadnought-foundry/portico": CALLER_STUB,
        # This repo's `release-train.yml` IS the reusable definition.
        "dreadnought-foundry/bureau-pipeline": TRAIN.read_text(encoding="utf-8"),
        # No train at all.
        "dreadnought-foundry/agent-bureau-demo": None,
    }
    stubs.update(over)
    return stubs


def _wake_fleet(roster=None, stubs=None, dispatched=None, raises=()):
    stubs = _stubs() if stubs is None else stubs
    dispatched = [] if dispatched is None else dispatched

    def read(repo):
        value = stubs.get(repo, None)
        if isinstance(value, Exception):
            raise value
        return value

    def dispatch(repo):
        if repo in raises:
            raise RuntimeError(raises[repo])
        dispatched.append(repo)

    return release_train.wake_fleet(_roster() if roster is None else roster,
                                   read_stub=read, dispatch=dispatch), dispatched


def test_every_roster_repo_with_a_stub_is_woken():
    results, dispatched = _wake_fleet()
    assert dispatched == ["dreadnought-foundry/agent-bureau",
                          "dreadnought-foundry/portico"]
    woken = {w.repo for w in results if w.act == release_train.WOKEN}
    assert woken == set(dispatched)


def test_a_repo_with_no_stub_is_skipped_and_named_never_failed():
    results, _ = _wake_fleet()
    demo = next(w for w in results if w.slug == "demo")
    assert demo.act == release_train.SKIPPED
    assert "release-train.yml" in demo.reason
    assert release_train.wake_exit(results) == 0


def test_the_reusable_definition_is_not_a_stub_and_the_skip_says_why():
    """`gh workflow run release-train.yml -R dreadnought-foundry/bureau-pipeline`
    answers 422: that filename here IS the `workflow_call` reusable, the same
    resolution `reconcile.review_workflow()` makes for the critic stub."""
    results, dispatched = _wake_fleet()
    assert "dreadnought-foundry/bureau-pipeline" not in dispatched
    own = next(w for w in results if w.slug == "bureau-pipeline")
    assert own.act == release_train.SKIPPED
    assert "workflow_call" in own.reason or "reusable" in own.reason


def test_a_stub_with_no_workflow_dispatch_is_skipped_and_named():
    no_dispatch = CALLER_STUB.replace("  workflow_dispatch:\n", "")
    stubs = _stubs(**{"dreadnought-foundry/portico": no_dispatch})
    results, dispatched = _wake_fleet(stubs=stubs)
    assert "dreadnought-foundry/portico" not in dispatched
    portico = next(w for w in results if w.slug == "portico")
    assert portico.act == release_train.SKIPPED
    assert "workflow_dispatch" in portico.reason


def test_an_unreadable_stub_is_a_failure_never_an_absent_one():
    """Console-honesty rule 1: an unreadable answer is not a green one. A
    repo whose stub could not be read may still have a train, and reporting
    it as "no train" is how a repo sleeps until 07:03 PT unnoticed."""
    stubs = _stubs(**{"dreadnought-foundry/portico": RuntimeError("HTTP 500")})
    results, dispatched = _wake_fleet(stubs=stubs)
    assert "dreadnought-foundry/portico" not in dispatched
    portico = next(w for w in results if w.slug == "portico")
    assert portico.act == release_train.FAILED
    assert "HTTP 500" in portico.reason
    assert release_train.wake_exit(results) == 1


def test_a_refused_dispatch_is_loud():
    """The 403 this rail has met before (DRE-1254): the bot App needs
    Actions: write, and a dispatch that was refused must never read as a
    woken train."""
    results, dispatched = _wake_fleet(
        raises={"dreadnought-foundry/portico":
                "HTTP 403: Resource not accessible by integration"})
    assert dispatched == ["dreadnought-foundry/agent-bureau"]
    portico = next(w for w in results if w.slug == "portico")
    assert portico.act == release_train.FAILED
    assert "403" in portico.reason
    assert release_train.wake_exit(results) == 1


def test_every_result_is_one_greppable_line_naming_the_repo():
    results, _ = _wake_fleet()
    for entry in results:
        line = release_train.wake_line(entry)
        assert line.startswith(f"{release_train.FLEET_TAG}: ")
        assert entry.repo in line
        assert entry.act in line


# --------------------------------------------------------------------------
# 5. The roster is the live map, and the owners come out of it.
# --------------------------------------------------------------------------

def test_the_roster_is_the_live_repo_map():
    assert release_train.fleet_roster() == json.loads(
        REPO_MAP.read_text(encoding="utf-8"))


def test_the_roster_can_be_narrowed_to_one_owner():
    roster = release_train.fleet_roster(owner="dreadnought-foundry")
    assert roster
    assert all(repo.startswith("dreadnought-foundry/")
               for repo in roster.values())
    assert "atlas" not in roster


def test_the_owners_are_derived_from_the_map_never_restated():
    owners = release_train.fleet_owners(release_train.fleet_roster())
    expected = sorted({repo.split("/")[0] for repo in
                       json.loads(REPO_MAP.read_text(encoding="utf-8")).values()})
    assert owners == expected


def test_the_cli_wakes_the_owner_it_is_given(tmp_path, monkeypatch, capsys):
    mapping = {"portico": "dreadnought-foundry/portico",
               "atlas": "EveryBite/atlas"}
    path = tmp_path / "repo-map.json"
    path.write_text(json.dumps(mapping), encoding="utf-8")
    dispatched = []
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(release_train, "fetch_stub", lambda repo: CALLER_STUB)
    monkeypatch.setattr(release_train, "dispatch_train", dispatched.append)

    code = release_train.main(
        ["wake", "--map", str(path), "--owner", "dreadnought-foundry"])
    out = capsys.readouterr().out
    assert code == 0
    assert dispatched == ["dreadnought-foundry/portico"]
    assert "EveryBite/atlas" not in out


def test_the_cli_exits_non_zero_when_a_train_could_not_be_woken(tmp_path,
                                                                monkeypatch,
                                                                capsys):
    path = tmp_path / "repo-map.json"
    path.write_text(json.dumps({"portico": "dreadnought-foundry/portico"}),
                    encoding="utf-8")

    def refuse(repo):
        raise RuntimeError("HTTP 403: Resource not accessible by integration")

    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(release_train, "fetch_stub", lambda repo: CALLER_STUB)
    monkeypatch.setattr(release_train, "dispatch_train", refuse)

    code = release_train.main(["wake", "--map", str(path)])
    out = capsys.readouterr().out
    assert code == 1
    assert "::warning" in out
    assert "403" in out


def test_the_cli_refuses_to_report_a_wake_it_could_not_make(tmp_path,
                                                            monkeypatch,
                                                            capsys):
    """No token is not an empty fleet: the run says which owner's
    installation is missing and fails, rather than reporting nothing to do."""
    path = tmp_path / "repo-map.json"
    path.write_text(json.dumps({"portico": "dreadnought-foundry/portico"}),
                    encoding="utf-8")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    code = release_train.main(
        ["wake", "--map", str(path), "--owner", "dreadnought-foundry"])
    out = capsys.readouterr().out
    assert code == 1
    assert "dreadnought-foundry" in out


def test_the_owners_command_emits_the_matrix(tmp_path, monkeypatch, capsys):
    path = tmp_path / "repo-map.json"
    path.write_text(json.dumps({"portico": "dreadnought-foundry/portico",
                                "atlas": "EveryBite/atlas"}), encoding="utf-8")
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert release_train.main(["wake-owners", "--map", str(path)]) == 0
    written = output.read_text(encoding="utf-8")
    assert written.startswith("owners=")
    assert json.loads(written.split("=", 1)[1]) == ["EveryBite",
                                                    "dreadnought-foundry"]


# --------------------------------------------------------------------------
# 6. The workflow itself — and the watcher that reads its red runs.
# --------------------------------------------------------------------------

def test_the_wake_up_workflow_runs_on_the_schedule_and_by_hand():
    on = _on(_wake())
    assert set(on) == {"schedule", "workflow_dispatch"}
    assert len(on["schedule"]) == 2


def test_the_wake_up_writes_nothing_to_this_repo():
    """It dispatches other repos' trains and nothing else: no `contents:
    write`, no push, no ref move — the channel-watch guarantee."""
    perms = _wake()["permissions"]
    assert perms.get("contents") == "read"
    assert "write" not in json.dumps(perms).replace('"contents": "read"', "")


def test_the_wake_up_fans_out_over_the_owners_it_derived():
    jobs = _wake()["jobs"]
    owners = next(job for name, job in jobs.items() if "owners" in job.get("outputs", {}))
    assert "wake-owners" in yaml.dump(owners), (
        "the matrix must come from config/repo-map.json, never a list typed "
        "into the workflow"
    )
    wake = next(job for job in jobs.values() if job.get("strategy"))
    matrix = wake["strategy"]["matrix"]
    assert "fromJSON" in str(matrix.get("owner"))
    assert wake["strategy"]["fail-fast"] is False, (
        "one owner's missing installation must not stop the other owners"
    )


def test_the_wake_up_mints_a_token_for_the_owner_it_is_waking():
    """A `GITHUB_TOKEN` cannot reach another repository at all, and an App
    installation token is scoped to ONE installation — so the token is minted
    per owner (premortem Q1/Q2)."""
    text = WAKE.read_text(encoding="utf-8")
    assert "actions/create-github-app-token@" in text
    assert "owner: ${{ matrix.owner }}" in text
    assert "BUREAU_APP_ID" in text and "BUREAU_APP_PRIVATE_KEY" in text


def test_the_wake_up_never_interpolates_the_matrix_into_a_shell_line():
    text = WAKE.read_text(encoding="utf-8")
    run_lines = [line for line in text.splitlines()
                 if "release_train.py wake" in line]
    assert run_lines
    for line in run_lines:
        assert "${{" not in line, line


def test_the_medic_watches_the_fleet_wake_up():
    """DRE-2036: every workflow that runs under its own name is watched, or
    its red runs go undiagnosed."""
    watched = yaml.safe_load(MEDIC.read_text(encoding="utf-8"))
    names = _on(watched)["workflow_run"]["workflows"]
    assert _wake()["name"] in names


# --------------------------------------------------------------------------
# 7. The second wake-up, while the stubs still carry their own crons.
# --------------------------------------------------------------------------

def test_a_second_wake_up_at_the_opening_releases_nothing_twice():
    """The card's evidence. Until the per-repo cards remove the stubs' own
    crons, a repo may be woken twice at the opening — once by this fleet
    wake-up and once by its own schedule. The second is a no-op: inside the
    spacing it names the spacing, and past it the surface reads `current`
    because the first run's tag already contains the commit."""
    data = _surface_data(spacing_minutes=30)
    data.pop("window")
    entry = release_train.surface("console", data)
    first = pt(2026, 7, 15, 5, 0)

    released = release_train.decide(entry, first, None, "behind", green(), None)
    assert released.act == release_train.RELEASE

    inside = release_train.decide(entry, first, first, "behind", green(), None)
    assert inside.act == release_train.NO_OP
    assert inside.code == "spacing"

    after = release_train.decide(entry, first, first, "current", green(), None)
    assert after.act == release_train.NO_OP
    assert after.code == "current"


def test_the_per_surface_lane_is_what_serialises_the_two_wake_ups():
    """The collapse rule is the concurrency lane, not luck: the second run's
    surface job waits for the first rather than running beside it."""
    release = yaml.safe_load(TRAIN.read_text(encoding="utf-8"))["jobs"]["release"]
    assert release["concurrency"]["cancel-in-progress"] is False
    group = release["concurrency"]["group"]
    assert "github.repository" in group and "matrix.surface" in group


# --------------------------------------------------------------------------
# 8. The documents that describe all of this.
# --------------------------------------------------------------------------

def test_the_standard_carries_the_fleet_wake_up_and_the_default_window():
    text = STANDARD.read_text(encoding="utf-8")
    assert "fleet-wake.yml" in text
    assert release_train.FLEET_WINDOW in text
    assert "07:00 PT" not in text.replace("07:00-21:00 PT", ""), (
        "the standard must not still publish 07:00 PT as the opening"
    )


def test_the_rendered_document_carries_the_fleet_default():
    assert DOC.read_text(encoding="utf-8") == release_train.render_markdown(), (
        "docs/release-train.md is generated — run "
        "`python3 scripts/release_train.py render`"
    )
    assert release_train.FLEET_WINDOW in DOC.read_text(encoding="utf-8")


def test_the_schema_table_says_window_may_be_omitted():
    window = next(f for f in release_train.SCHEMA if f.name == "window")
    assert window.optional is True
    assert "window" not in release_train.REQUIRED_FIELDS
    assert release_train.FLEET_WINDOW in window.means


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
