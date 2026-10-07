"""The fleet's default window is round the clock, declared once (DRE-4450,
DRE-5266) — and the fleet wake-up is retired (DRE-6052).

DRE-4450 moved the train's opening into ONE place, `release_train.FLEET_WINDOW`:
a surface's `release.json` may omit `window` and inherit it, the schema check
accepts the omission, and a surface that declares its own keeps it, with every
line the train prints about that window saying it OVERRIDES the fleet default.
DRE-5266 (the CEO, 2026-09-29: "They should all default to the
round-the-clock") made that default `always`, and a declared window that
merely equals the default is never called an override.

The same card once also carried a fleet wake-up — one workflow in this repo
that dispatched every roster repo's train stub at 05:00 PT. The CEO retired it
on 2026-10-06 because the fleet now runs round the clock, and DRE-6052
removed it: every CI completion on the default branch, the train's own
re-arm and each stub's own 05:00 PT crons still wake a train. What survives
here is the default window and the roster readers the groomer and Nightly
Watch still use (`fleet_roster`, `fleet_owners`, `wake-owners`); the last
section pins that the wake-up stays gone.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_train  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"
MEDIC = WORKFLOWS / "self-medic.yml"
REPO_MAP = ROOT / "config" / "repo-map.json"
STANDARD = ROOT / "standards" / "release-train.md"
DOC = ROOT / "docs" / "release-train.md"


def _on(doc: dict) -> dict:
    # YAML 1.1 parses the bare key `on` as boolean True.
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


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

def test_the_fleet_default_window_is_round_the_clock():
    """The CEO, 2026-09-29: "They should all default to the round-the-clock.
    … They can change it if they want but they should default to round the
    clock." (DRE-5266). One constant, in the train, is the whole of that
    decision."""
    assert release_train.FLEET_WINDOW == release_train.WINDOW_ALWAYS == "always"
    assert release_train._window_ok(release_train.FLEET_WINDOW)


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


@pytest.mark.parametrize("hour", [3, 14])
def test_a_surface_that_omits_window_releases_round_the_clock(hour):
    """Inheritance is load-bearing, not cosmetic: a surface that declares no
    window is open in the small hours and in the afternoon alike."""
    data = _surface_data()
    data.pop("window")
    entry = release_train.surface("console", data)
    decision = release_train.decide(
        entry, pt(2026, 7, 15, hour, 0), None, "behind", green(), None)
    assert decision.act == release_train.RELEASE, decision.reason


def test_an_inherited_window_line_names_the_fleet_default():
    """A surface that inherits `always` never gets a window no-op, so its
    release line is the one line that can say where its window came from."""
    data = _surface_data()
    data.pop("window")
    entry = release_train.surface("console", data)
    decision = release_train.decide(
        entry, pt(2026, 7, 15, 3, 0), None, "behind", green(), None)
    assert decision.act == release_train.RELEASE
    assert release_train.FLEET_WINDOW in decision.reason
    assert "fleet default" in decision.reason, decision.reason
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


def test_a_declared_bounded_window_still_no_ops_before_it_opens():
    """A surface can still narrow its hours: `07:00-21:00 PT` no-ops at 04:30
    PT, and the line says it overrides the fleet default."""
    entry = release_train.surface("console", _surface_data(window="07:00-21:00 PT"))
    decision = release_train.decide(
        entry, pt(2026, 7, 15, 4, 30), None, "behind", green(), None)
    assert decision.act == release_train.NO_OP
    assert decision.code == "window"
    assert "overriding the fleet default" in decision.reason, decision.reason


def test_a_declared_window_equal_to_the_default_is_not_called_an_override():
    """agent-bureau, Portico and bureau-pipeline all declare `always`
    explicitly. Once the default is `always` too, "overriding the fleet
    default always" would be false on every one of their lines."""
    entry = release_train.surface("console", _surface_data(window="always"))
    assert entry.declared_window is True
    note = release_train.window_note(entry)
    assert "overrid" not in note, note
    assert "fleet default" in note, note

    decision = release_train.decide(
        entry, pt(2026, 7, 15, 3, 0), None, "behind", green(), None)
    assert decision.act == release_train.RELEASE
    assert "overrid" not in decision.reason, decision.reason
    assert "fleet default" in decision.reason, decision.reason


# --------------------------------------------------------------------------
# 3. The roster is the live map, and the owners come out of it — the readers
#    the groomer (`wake-owners`) and Nightly Watch still use.
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
# 4. The documents that describe the default.
# --------------------------------------------------------------------------

#: Every document that states the fleet default (DRE-5266). Each names it
#: as round the clock and none still publishes the old bounded hours.
DEFAULT_PROSE = (
    STANDARD,
    DOC,
    ROOT / "README.md",
    ROOT / "standards" / "engineering.md",
)


def test_the_standard_carries_the_default_window():
    text = STANDARD.read_text(encoding="utf-8")
    assert "FLEET_WINDOW" in text
    assert "07:00 PT" not in text.replace("07:00-21:00 PT", ""), (
        "the standard must not still publish 07:00 PT as the opening"
    )


@pytest.mark.parametrize("path", DEFAULT_PROSE, ids=lambda p: p.name)
def test_the_prose_names_the_default_as_round_the_clock(path):
    text = " ".join(path.read_text(encoding="utf-8").split())
    assert "round the clock" in text, (
        f"{path.relative_to(ROOT)} must name the fleet default as round the clock"
    )
    assert "05:00-21:00 PT" not in text, (
        f"{path.relative_to(ROOT)} still states 05:00-21:00 PT as the fleet's hours"
    )


def test_the_rendered_document_carries_the_fleet_default():
    assert DOC.read_text(encoding="utf-8") == release_train.render_markdown(), (
        "docs/release-train.md is generated — run "
        "`python3 scripts/release_train.py render`"
    )
    assert f"`{release_train.FLEET_WINDOW}`" in DOC.read_text(encoding="utf-8")


def test_the_schema_table_says_window_may_be_omitted():
    window = next(f for f in release_train.SCHEMA if f.name == "window")
    assert window.optional is True
    assert "window" not in release_train.REQUIRED_FIELDS
    assert release_train.FLEET_WINDOW in window.means


# --------------------------------------------------------------------------
# 5. The fleet wake-up is retired (DRE-6052) and stays retired.
# --------------------------------------------------------------------------

#: What the wake-up was made of in `release_train.py` that a name sweep for
#: "wake" would not catch. None had a caller outside that module, its
#: workflow and its tests.
RETIRED_NAMES = (
    "CRON_ANCHOR_YEAR", "WOKEN", "SKIPPED", "FAILED", "fetch_stub",
    "dispatch_train",
)

#: The only names in `release_train.py` that may still say "wake": the
#: groomer's roster command and the re-armed run's own wake-up (DRE-3791).
STILL_WAKING = {"_cmd_wake_owners", "wake_head"}


def _workflows() -> dict:
    return {path.name: path.read_text(encoding="utf-8")
            for path in sorted(WORKFLOWS.glob("*.yml"))}


def test_no_workflow_dispatches_the_fleets_trains_any_more():
    """The wake-up was the one workflow that ran `release_train.py wake` to
    dispatch every roster repo's stub. A leftover file is one re-enable away
    from waking every train twice again."""
    for name, text in _workflows().items():
        assert not re.search(r"release_train\.py wake(?!-)", text), (
            f"{name} still runs the retired fleet wake-up (DRE-6052)"
        )


def test_the_wake_up_code_is_gone_from_the_train():
    waking = {n for n in dir(release_train) if "wake" in n.lower()}
    assert waking == STILL_WAKING, (
        f"release_train still carries the retired wake-up: "
        f"{sorted(waking - STILL_WAKING)}"
    )
    left = [n for n in RETIRED_NAMES if hasattr(release_train, n)]
    assert left == [], f"release_train still carries the retired wake-up: {left}"


def test_the_wake_subcommand_is_gone(capsys):
    with pytest.raises(SystemExit) as refused:
        release_train.main(["wake"])
    assert refused.value.code != 0
    assert "invalid choice" in capsys.readouterr().err


def test_the_owners_command_no_longer_calls_itself_the_wake_ups_matrix():
    assert "wake-up" not in (release_train._cmd_wake_owners.__doc__ or "")
    assert "wake-up" not in (release_train.fleet_owners.__doc__ or "")
    assert "wake-up" not in (release_train.fleet_roster.__doc__ or "")


def test_the_medic_watches_only_workflows_that_exist():
    """Deleting a workflow and leaving its name in the medic's list is the
    leftover this guards: every name watched is a workflow this repo has."""
    watched = _on(yaml.safe_load(MEDIC.read_text(encoding="utf-8")))[
        "workflow_run"]["workflows"]
    names = {yaml.safe_load(text).get("name") for text in _workflows().values()}
    assert [w for w in watched if w not in names] == []


def test_the_rendered_document_no_longer_carries_the_wake_up():
    rendered = release_train.render_markdown()
    assert "wakes the fleet" not in rendered
    assert "fleet wake-up" not in rendered
    assert "repo-map.json" not in rendered, (
        "the train's document no longer describes a roster sweep"
    )


def test_the_standard_no_longer_describes_the_wake_up():
    text = STANDARD.read_text(encoding="utf-8")
    assert set(re.findall(r"\bFLEET_[A-Z]+", text)) == {"FLEET_WINDOW"}
    assert not re.search(r"release_train\.py wake(?!-)", text)
    assert "repo-map.json" not in text


def test_the_re_arm_bound_names_what_still_wakes_the_train():
    """Past the bound the no-op says what wakes the train instead: the next
    CI completion or the stub's own morning cron — never the retired
    wake-up."""
    entry = release_train.surface("console", _surface_data())
    now = pt(2026, 7, 15, 23, 0)
    late = release_train.decide(entry, now, None, "behind", green(), None)
    outcome = release_train.re_arm_plan([(entry, late)], now=now,
                                        bound_minutes=32)
    assert outcome.dispatch is False
    assert outcome.note.startswith("not re-armed:"), outcome.note
    assert "next CI completion" in outcome.note, outcome.note
    assert "morning cron" in outcome.note, outcome.note
    assert "fleet wake-up" not in outcome.note, outcome.note

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
