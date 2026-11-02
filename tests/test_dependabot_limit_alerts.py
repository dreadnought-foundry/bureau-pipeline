"""RED-first tests for DRE-2119 — parked dependabot majors must not starve
the minor/patch group at open-pull-requests-limit SILENTLY.

THE GAP (found by the DRE-2110 vendor-boundary backfill audit, checklist
Q3/Q5): dependabot.yml caps each ecosystem at open-pull-requests-limit: 5.
Majors are deliberately excluded from the minor/patch groups, arrive as
single PRs, and the merge gate parks them as `human` — where they sit open
indefinitely, each occupying one of the 5 slots. Vendor behavior at the
bound: Dependabot silently stops opening NEW version-update PRs — including
the weekly grouped minor/patch (security-relevant) PR — once 5 are open.
No signal anywhere. The bureau's no-silent-killers rule: every hard limit
gets a WARN at ~80% and a loud CRITICAL when it bites.

FIX UNDER TEST — reconcile.check_dependabot_capacity(), a new read-only
sweep backstop wired into main()'s backstop tuple:
  * reads the limit per ecosystem from the TARGET repo's checked-out
    .github/dependabot.yml (stdlib parse — the sweep runs on bare python3,
    no pip install), falling back to Dependabot's documented default (5)
    when the key is omitted; NEVER hardcoded;
  * counts open dependabot-authored PRs per ecosystem by the branch token
    (dependabot/<token>/..., e.g. github-actions -> github_actions);
  * WARNs (console-only print) at ceil(80% of the limit); at the limit it
    records a CRITICAL on the fail-loudly rail (_write_failures -> red run
    -> medic), naming the parked human-lane PRs (the ones NOT carrying a
    configured group's name) so an operator decision — merge or a config
    `ignore` rule — is forced before the patch stream starves.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LIVE_CONFIG = ROOT / ".github" / "dependabot.yml"


@pytest.fixture(autouse=True)
def _selfhost_repo(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/bureau-pipeline")
    monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()


# A two-ecosystem config mirroring the live file's shape: limit 5, one
# minor/patch group each.
PIP_ACTIONS_CONFIG = """\
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 5
    groups:
      pip-minor-patch:
        patterns: ["*"]
        update-types: ["minor", "patch"]

  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 5
    groups:
      actions-minor-patch:
        patterns: ["*"]
        update-types: ["minor", "patch"]
"""


def _pr(number, branch, title="Bump something from 1.0 to 2.0", author="dependabot"):
    return {
        "number": number,
        "title": title,
        "headRefName": branch,
        "author": {"login": author},
    }


def _pip_singles(n):
    """n single-dependency (human-lane / parked-major shaped) pip PRs."""
    return [
        _pr(100 + i, f"dependabot/pip/dep{i}-{i + 2}.0.0", title=f"Bump dep{i}")
        for i in range(n)
    ]


def _run_factory(state):
    """subprocess.run stub covering exactly the gh calls this check makes:
    pr list (the scan) — and nothing else (the check is read-only)."""

    def fake_run(argv, **kwargs):
        assert argv[0] == "gh", f"unexpected call: {argv}"
        if argv[1] == "pr" and argv[2] == "list":
            rc = state.get("list_rc", 0)
            return SimpleNamespace(
                returncode=rc,
                stdout="" if rc else json.dumps(state["prs"]),
                stderr="HTTP 403: rate limited" if rc else "",
            )
        raise AssertionError(f"unexpected gh call: {argv}")

    return fake_run


def _check(tmp_path, monkeypatch, prs, config=PIP_ACTIONS_CONFIG, list_rc=0):
    """Write the config into a fake repo root, chdir there (the sweep runs
    at the TARGET repo's checkout root), and run the capacity check."""
    if config is not None:
        cfg = tmp_path / ".github" / "dependabot.yml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(config)
    monkeypatch.chdir(tmp_path)
    state = {"prs": prs, "list_rc": list_rc}
    with patch.object(reconcile.subprocess, "run", side_effect=_run_factory(state)):
        reconcile.check_dependabot_capacity()
    return state


# Foundry's shape (DRE-8570): six npm entries, one per manifest folder, the
# limit left at Dependabot's default. Group names follow Foundry's own.
FOUNDRY_NPM_DIRS = {
    "/templates/company/web": "slipway-web-npm-minor-patch",
    "/templates/company/mobile": "slipway-mobile-npm-minor-patch",
    "/templates/company/infra": "slipway-infra-npm-minor-patch",
    "/infra/keel-registry": "keel-registry-npm-minor-patch",
    "/libraries/js/design-system": "keel-design-system-npm-minor-patch",
    "/libraries/js/offline": "keel-offline-npm-minor-patch",
}


def _npm_config(dirs):
    """One npm entry per {directory: group name}, default limit, in order."""
    entries = "".join(
        f"""\
  - package-ecosystem: "npm"
    directory: "{directory}"
    schedule:
      interval: "monthly"
    groups:
      {group}:
        patterns: ["*"]
        update-types: ["minor", "patch"]

"""
        for directory, group in dirs.items()
    )
    return "version: 2\nupdates:\n" + entries


FOUNDRY_NPM_CONFIG = _npm_config(FOUNDRY_NPM_DIRS)


def _npm_branch(directory, name):
    return f"dependabot/npm_and_yarn/{directory.strip('/')}/{name}"


# --------------------------------------------------------------------------
# parse_dependabot_entries(): one entry per update, the limit from the config
# --------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    pytest.param(LIVE_CONFIG.read_text(), id="live-config"),
    pytest.param(FOUNDRY_NPM_CONFIG, id="foundry-six-npm"),
])
def test_parses_the_live_config_like_yaml_does(text):
    """The stdlib parser must agree with a real YAML parse, entry by entry
    and in file order, on ecosystem, directory, limit and group names — for
    THIS repo's dependabot.yml and for Foundry's six-npm shape. Parser drift
    would make the check silently blind, and keying by ecosystem collapsed
    Foundry's six npm entries into one (DRE-8570)."""
    parsed = reconcile.parse_dependabot_entries(text)
    doc = yaml.safe_load(text)
    expected = [
        (u["package-ecosystem"], u.get("directory"),
         u.get("open-pull-requests-limit"), list(u.get("groups") or {}))
        for u in doc["updates"]
    ]
    got = [
        (e["ecosystem"], e["directory"], e["limit"], e["groups"])
        for e in parsed
    ]
    assert got == expected, "every update entry must parse, in order"


def test_old_ecosystem_keyed_reader_is_gone():
    """The ecosystem-keyed reader collapsed same-ecosystem entries; leaving it
    beside the new one leaves the bug for its next caller (DRE-8570)."""
    assert not hasattr(reconcile, "parse_dependabot_limits")


LIMIT_7_CONFIG = """\
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 7
    groups:
      pip-minor-patch:
        patterns: ["*"]
        update-types: ["minor", "patch"]
"""


def test_limit_is_read_from_config_not_hardcoded(tmp_path, monkeypatch, capsys):
    """With open-pull-requests-limit: 7, five open PRs are NOT critical
    (they would be at a hardcoded 5) and six is only the ~80% WARN."""
    _check(tmp_path, monkeypatch, _pip_singles(5), config=LIMIT_7_CONFIG)
    out = capsys.readouterr()
    assert "WARNING" not in out.out and "CRITICAL" not in out.err, (
        "5 open PRs against a configured limit of 7 is quiet — a hardcoded "
        "5 would have fired CRITICAL here"
    )
    assert reconcile._write_failures == []

    _check(tmp_path, monkeypatch, _pip_singles(6), config=LIMIT_7_CONFIG)
    out = capsys.readouterr()
    assert "WARNING" in out.out and "6/7" in out.out, (
        "6/7 is the ~80% threshold of the CONFIGURED limit"
    )
    assert reconcile._write_failures == []


def test_omitted_limit_uses_dependabot_default_of_five(tmp_path, monkeypatch):
    """Dependabot's documented default when the key is omitted is 5 open
    PRs — the check must apply the vendor default, not skip the ecosystem."""
    config = PIP_ACTIONS_CONFIG.replace("    open-pull-requests-limit: 5\n", "")
    assert "open-pull-requests-limit" not in config
    _check(tmp_path, monkeypatch, _pip_singles(5), config=config)
    assert any("CRITICAL" in f for f in reconcile._write_failures), (
        "5 open PRs with the key omitted IS at the vendor-default limit"
    )


# --------------------------------------------------------------------------
# under limit / WARN / CRITICAL — the three alerting states
# --------------------------------------------------------------------------
def test_under_limit_is_silent(tmp_path, monkeypatch, capsys):
    _check(tmp_path, monkeypatch, _pip_singles(3))
    out = capsys.readouterr()
    assert "WARNING" not in out.out and "CRITICAL" not in (out.out + out.err), (
        "3/5 is healthy — no alert noise below the ~80% threshold"
    )
    assert reconcile._write_failures == [] and reconcile._read_failures == []


def test_warn_at_80_percent_console_only(tmp_path, monkeypatch, capsys):
    """4/5 slots used -> a WARNING line in the sweep log, but the run stays
    GREEN (WARN is console-only; only CRITICAL rides the fail-loudly rail)."""
    _check(tmp_path, monkeypatch, _pip_singles(4))
    out = capsys.readouterr()
    assert "WARNING" in out.out and "4/5" in out.out and "pip" in out.out, (
        "approaching the limit must WARN, naming the ecosystem and count"
    )
    assert reconcile._write_failures == [], "WARN must NOT turn the sweep red"


def test_critical_at_limit_is_loud_and_names_the_parked_prs(
    tmp_path, monkeypatch, capsys
):
    """5/5 slots used -> CRITICAL on the fail-loudly rail (_write_failures
    -> red run -> medic), naming each parked human-lane PR (the singles, not
    the grouped minor/patch PR) so the operator knows exactly what to merge
    or config-ignore."""
    grouped = _pr(
        99,
        "dependabot/pip/pip-minor-patch-0f5a1b2c3d",
        title="Bump the pip-minor-patch group",
    )
    singles = _pip_singles(4)
    _check(tmp_path, monkeypatch, [grouped] + singles)
    assert len(reconcile._write_failures) == 1, (
        "at the limit the check must record exactly one write failure so "
        "the sweep run goes red and medic picks it up"
    )
    msg = reconcile._write_failures[0]
    assert "CRITICAL" in msg and "5/5" in msg and "pip" in msg
    for pr in singles:
        assert f"#{pr['number']}" in msg, (
            f"the parked human-lane PR #{pr['number']} must be named — the "
            "operator decision (merge or config-ignore) needs the list"
        )
    assert "#99" not in msg, (
        "the grouped minor/patch PR is not a parked major — the CRITICAL "
        "names only the human-lane singles"
    )
    out = capsys.readouterr()
    assert "CRITICAL" in out.err, "CRITICAL must be printed loudly to stderr"


def test_ecosystems_are_counted_independently(tmp_path, monkeypatch, capsys):
    """4 pip + 1 actions must WARN for pip only — one ecosystem's pressure
    never bleeds into another's count."""
    prs = _pip_singles(4) + [
        _pr(200, "dependabot/github_actions/actions/checkout-8")
    ]
    _check(tmp_path, monkeypatch, prs)
    out = capsys.readouterr()
    warn_lines = [ln for ln in out.out.splitlines() if "WARNING" in ln]
    assert any("pip" in ln for ln in warn_lines)
    assert not any("github-actions" in ln for ln in warn_lines), (
        "1/5 github-actions is healthy — no WARN for it"
    )
    assert reconcile._write_failures == []


def test_github_actions_branch_token_maps_to_the_config_name(
    tmp_path, monkeypatch
):
    """Dependabot's branches spell the ecosystem github_actions while the
    config says github-actions — the check must bridge the vendor's naming
    or the actions count is silently always zero."""
    prs = [
        _pr(300 + i, f"dependabot/github_actions/actions/tool{i}-{i + 2}")
        for i in range(5)
    ]
    _check(tmp_path, monkeypatch, prs)
    assert any(
        "CRITICAL" in f and "github-actions" in f for f in reconcile._write_failures
    ), "5/5 github_actions branches must CRITICAL the github-actions ecosystem"


def test_zero_limit_ecosystem_is_skipped(tmp_path, monkeypatch, capsys):
    """open-pull-requests-limit: 0 disables version updates for the
    ecosystem — nothing can starve, so no alert."""
    config = PIP_ACTIONS_CONFIG.replace(
        "open-pull-requests-limit: 5", "open-pull-requests-limit: 0"
    )
    _check(tmp_path, monkeypatch, _pip_singles(2), config=config)
    out = capsys.readouterr()
    assert "WARNING" not in out.out and "CRITICAL" not in (out.out + out.err)
    assert reconcile._write_failures == []


def test_impostor_authored_dependabot_branches_do_not_count(
    tmp_path, monkeypatch, capsys
):
    """A human's PR on a dependabot-named branch does not occupy a
    Dependabot slot — counting it would fake pressure (same author
    discipline as is_dependabot_pr / merge-gate condition D)."""
    prs = [
        _pr(400 + i, f"dependabot/pip/dep{i}-2.0.0", author="mallory")
        for i in range(5)
    ]
    _check(tmp_path, monkeypatch, prs)
    out = capsys.readouterr()
    assert "WARNING" not in out.out and "CRITICAL" not in (out.out + out.err)
    assert reconcile._write_failures == []


# --------------------------------------------------------------------------
# fail-safe: missing config, unparseable config, unreadable listing
# --------------------------------------------------------------------------
def test_repo_without_dependabot_config_is_a_silent_noop(
    tmp_path, monkeypatch, capsys
):
    """Product repos without dependabot.yml have no limit to starve — the
    check must do nothing (and make no gh calls: _run_factory would raise)."""
    monkeypatch.chdir(tmp_path)
    with patch.object(
        reconcile.subprocess,
        "run",
        side_effect=AssertionError("no gh call expected without a config"),
    ):
        reconcile.check_dependabot_capacity()
    out = capsys.readouterr()
    assert "WARNING" not in out.out and "CRITICAL" not in (out.out + out.err)
    assert reconcile._write_failures == [] and reconcile._read_failures == []


def test_unparseable_config_fails_loud_not_blind(tmp_path, monkeypatch):
    """dependabot.yml EXISTS but yields no update entries -> the check is
    blind, which must be a recorded read failure (red run), never silence
    (DRE-2034 read discipline: fabricated emptiness is the silent killer)."""
    _check(tmp_path, monkeypatch, [], config="just: nonsense\n")
    assert reconcile._read_failures, (
        "an existing-but-unparseable config must surface as a read failure"
    )


def test_unreadable_pr_listing_fails_loud_not_quiet(tmp_path, monkeypatch, capsys):
    """A 403 on the PR listing is NOT '0 open PRs — all quiet': record the
    read failure (red run) and raise no verdict on fabricated emptiness."""
    _check(tmp_path, monkeypatch, _pip_singles(5), list_rc=1)
    assert reconcile._read_failures, "the failed listing must be recorded"
    out = capsys.readouterr()
    assert "WARNING" not in out.out and "CRITICAL" not in (out.out + out.err), (
        "no WARN/CRITICAL may be raised off an unreadable listing"
    )
    assert reconcile._write_failures == []


# --------------------------------------------------------------------------
# wiring: main() must run the check — the suite goes RED if it is removed
# --------------------------------------------------------------------------
def test_main_sweep_runs_the_capacity_check():
    """The check is a backstop in main()'s tuple: removing the function or
    dropping it from the tuple must turn this red (the acceptance criterion
    'tests fail if the check is removed')."""
    mocks = {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "retry_dead_fix_runs": MagicMock(),
        "review_dependabot_prs": MagicMock(),
        "card_dependabot_prs": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
        "close_finished_epics": MagicMock(),
        "flag_stranded": MagicMock(return_value=set()),
        "active_cards": MagicMock(return_value=[]),
        "promote_ready": MagicMock(),
    }
    with patch.multiple(reconcile, **mocks):
        reconcile.main()
    assert mocks["check_dependabot_capacity"].call_count == 1, (
        "every full sweep must evaluate dependabot slot capacity — the "
        "no-silent-killers monitor for open-pull-requests-limit"
    )


# --------------------------------------------------------------------------
# DRE-8570: slots are counted per update ENTRY, not per ecosystem
# --------------------------------------------------------------------------
# Dependabot applies open-pull-requests-limit to each `updates` entry. Foundry
# has six npm entries, one per folder; keyed by ecosystem, the last entry
# overwrote the other five and six grouped PRs (one per folder, 1/5 each) were
# counted against one limit of 5 — a false CRITICAL on run 41271938398.
def test_foundry_six_npm_entries_one_grouped_pr_each_is_quiet(
    tmp_path, monkeypatch, capsys
):
    hashes = ["ad441a7b4e", "1c2d3e4f5a", "9b8a7c6d5e", "0a1b2c3d4e",
              "5f6e7d8c9b", "e4d3c2b1a0"]
    prs = [
        _pr(131 + i, _npm_branch(directory, f"{group}-{hashes[i]}"),
            title=f"Bump the {group} group")
        for i, (directory, group) in enumerate(FOUNDRY_NPM_DIRS.items())
    ]
    assert prs[0]["headRefName"] == (
        "dependabot/npm_and_yarn/templates/company/web/"
        "slipway-web-npm-minor-patch-ad441a7b4e"
    )
    _check(tmp_path, monkeypatch, prs, config=FOUNDRY_NPM_CONFIG)
    out = capsys.readouterr()
    assert "CRITICAL" not in (out.out + out.err), (
        "each folder holds 1 of its own 5 slots — nothing is at a limit"
    )
    assert "WARNING" not in out.out
    assert reconcile._write_failures == [] and reconcile._read_failures == []


def test_full_entry_fires_alone_and_names_its_directory(
    tmp_path, monkeypatch, capsys
):
    """5 open in /templates/company/web, 1 in /templates/company/mobile, both
    at the default limit: exactly one failure, naming the full entry."""
    config = _npm_config({
        "/templates/company/web": "slipway-web-npm-minor-patch",
        "/templates/company/mobile": "slipway-mobile-npm-minor-patch",
    })
    web = [
        _pr(500 + i, _npm_branch("/templates/company/web", f"dep{i}-{i + 2}.0.0"),
            title=f"Bump dep{i}")
        for i in range(5)
    ]
    mobile = [_pr(600, _npm_branch("/templates/company/mobile", "react-19.0.0"))]
    _check(tmp_path, monkeypatch, web + mobile, config=config)
    assert len(reconcile._write_failures) == 1
    msg = reconcile._write_failures[0]
    assert "CRITICAL" in msg and "5/5" in msg
    assert "dependabot npm /templates/company/web" in msg, (
        "the line names the ecosystem and the entry's directory"
    )
    assert "mobile" not in msg
    out = capsys.readouterr()
    assert "/templates/company/mobile" not in (out.out + out.err), (
        "1/5 in the mobile folder is healthy — that entry stays silent"
    )


def test_critical_line_is_an_error_line(tmp_path, monkeypatch, capsys):
    """The summary says "see ERROR lines above", so the slot check's critical
    line must be one: stderr `ERROR: CRITICAL: dependabot ...`, while the
    recorded failure keeps the word CRITICAL."""
    _check(tmp_path, monkeypatch, _pip_singles(5))
    assert "CRITICAL" in reconcile._write_failures[0]
    err_lines = [ln for ln in capsys.readouterr().err.splitlines() if "CRITICAL" in ln]
    assert err_lines, "the critical line goes to stderr"
    assert all(ln.startswith("ERROR: ") for ln in err_lines), err_lines
    assert err_lines[0].startswith("ERROR: CRITICAL: dependabot pip / is at its")


def test_grouped_pr_of_an_earlier_entry_is_never_parked(tmp_path, monkeypatch):
    """Group names come from the PR's OWN entry: the first entry's grouped PR
    is not a parked single just because the last entry's group survived."""
    config = _npm_config({
        "/templates/company/web": "slipway-web-npm-minor-patch",
        "/libraries/js/offline": "keel-offline-npm-minor-patch",
    })
    grouped = _pr(
        131,
        _npm_branch("/templates/company/web",
                    "slipway-web-npm-minor-patch-ad441a7b4e"),
        title="Bump the slipway-web-npm-minor-patch group",
    )
    singles = [
        _pr(140 + i, _npm_branch("/templates/company/web", f"dep{i}-{i + 2}.0.0"),
            title=f"Bump dep{i}")
        for i in range(4)
    ]
    _check(tmp_path, monkeypatch, [grouped] + singles, config=config)
    assert len(reconcile._write_failures) == 1
    msg = reconcile._write_failures[0]
    for pr in singles:
        assert f"#{pr['number']}" in msg
    assert "#131" not in msg, "a grouped PR is never listed as parked"


def test_unmatched_branch_warns_no_configured_entry(tmp_path, monkeypatch, capsys):
    config = _npm_config({"/templates/company/web": "slipway-web-npm-minor-patch"})
    prs = [_pr(700, _npm_branch("/somewhere/else", "lodash-4.17.21"))]
    _check(tmp_path, monkeypatch, prs, config=config)
    out = capsys.readouterr()
    assert "#700" in out.out and "matches no configured entry" in out.out


FOUNDRY_PIP_CONFIG = """\
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "monthly"
    groups:
      foundry-pip-minor-patch:
        patterns: ["*"]
  - package-ecosystem: "pip"
    directory: "/libraries/python/keel-cli"
    schedule:
      interval: "monthly"
    groups:
      keel-cli-pip-minor-patch:
        patterns: ["*"]
  - package-ecosystem: "pip"
    directory: "/libraries/python/keel-core"
    schedule:
      interval: "monthly"
"""


def test_branch_matches_its_entry_root_and_nested():
    """A root `/` entry gets only what no nested entry claims; a nested
    branch goes to its folder's entry."""
    entries = reconcile.parse_dependabot_entries(FOUNDRY_PIP_CONFIG)
    root = reconcile.dependabot_entry_for(
        "dependabot/pip/foundry-pip-minor-patch-0f5a1b2c3d", entries)
    assert root is not None and root["directory"] == "/"
    nested = reconcile.dependabot_entry_for(
        "dependabot/pip/libraries/python/keel-cli/setuptools-gte-84.2.1", entries)
    assert nested is not None
    assert nested["directory"] == "/libraries/python/keel-cli"
    # Another ecosystem's token is never a candidate.
    assert reconcile.dependabot_entry_for(
        "dependabot/npm_and_yarn/libraries/python/keel-cli/x-1.0", entries) is None


def test_longest_directory_wins_on_whole_segments():
    config = _npm_config({
        "/libraries/js": "js-npm-minor-patch",
        "/libraries/js/offline": "keel-offline-npm-minor-patch",
    })
    entries = reconcile.parse_dependabot_entries(config)
    hit = reconcile.dependabot_entry_for(
        "dependabot/npm_and_yarn/libraries/js/offline/idb-8.0.1", entries)
    assert hit is not None and hit["directory"] == "/libraries/js/offline"
    hit = reconcile.dependabot_entry_for(
        "dependabot/npm_and_yarn/libraries/js/react-19.0.0", entries)
    assert hit is not None and hit["directory"] == "/libraries/js"
    # Whole segments only: /libraries/js never claims /libraries/jsx/...
    assert reconcile.dependabot_entry_for(
        "dependabot/npm_and_yarn/libraries/jsx/react-19.0.0", entries) is None


# --------------------------------------------------------------------------
# DRE-8570: every recorded failure is printed at the red exit
# --------------------------------------------------------------------------
def _main_mocks():
    return {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "retry_dead_fix_runs": MagicMock(),
        "review_dependabot_prs": MagicMock(),
        "card_dependabot_prs": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
        "close_finished_epics": MagicMock(),
        "flag_stranded": MagicMock(return_value=set()),
        "active_cards": MagicMock(return_value=[]),
        "promote_ready": MagicMock(),
    }


def test_red_exit_prints_every_recorded_failure(capsys):
    """A failure recorded without its own log line (the crashed-review hold
    post, say) must still reach the log the summary points at."""
    write = "crashed-review hold: PR #9 post failed (HTTP 502)"
    read = "epic queue DRE-1: Linear returned no such card"

    def record_quietly():
        reconcile._write_failures.append(write)
        reconcile._read_failures.append(read)

    mocks = _main_mocks()
    mocks["check_dependabot_capacity"] = MagicMock(side_effect=record_quietly)
    with patch.multiple(reconcile, **mocks), pytest.raises(SystemExit) as exc:
        reconcile.main()
    assert str(exc.value.code) == (
        "reconcile: 1 write / 1 read failure(s), 0 unfixed card defect(s) — "
        "see ERROR lines above"
    ), "the summary text is quoted by the medic and stays unchanged"
    err = capsys.readouterr().err
    assert f"ERROR: write failure: {write}" in err
    assert f"ERROR: read failure: {read}" in err


def test_promote_only_red_exit_prints_every_recorded_failure(capsys):
    write = "DRE-2 advance/comment: HTTP 500"
    read = "epic start: Linear unreadable"

    def record_quietly(*a, **k):
        reconcile._write_failures.append(write)
        reconcile._read_failures.append(read)

    mocks = _main_mocks()
    mocks["promote_ready"] = MagicMock(side_effect=record_quietly)
    with patch.multiple(reconcile, **mocks), pytest.raises(SystemExit) as exc:
        reconcile.main(promote_only=True)
    assert "see ERROR lines above" in str(exc.value.code)
    err = capsys.readouterr().err
    assert f"ERROR: write failure: {write}" in err
    assert f"ERROR: read failure: {read}" in err
