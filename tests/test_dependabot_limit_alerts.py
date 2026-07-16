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


# --------------------------------------------------------------------------
# parse_dependabot_limits(): the limit comes from the config, never hardcoded
# --------------------------------------------------------------------------
def test_parses_the_live_config_like_yaml_does():
    """The stdlib parser must agree with a real YAML parse of THIS repo's
    dependabot.yml on every ecosystem, limit, and group name — parser drift
    against the live file would make the check silently blind."""
    parsed = reconcile.parse_dependabot_limits(LIVE_CONFIG.read_text())
    doc = yaml.safe_load(LIVE_CONFIG.read_text())
    expected = {u["package-ecosystem"]: u for u in doc["updates"]}
    assert set(parsed) == set(expected), "every configured ecosystem must parse"
    for eco, update in expected.items():
        assert parsed[eco]["limit"] == update.get("open-pull-requests-limit"), (
            f"{eco}: the limit must be read from dependabot.yml"
        )
        assert set(parsed[eco]["groups"]) == set(update.get("groups") or {}), (
            f"{eco}: group names identify the gate-mergeable grouped PR"
        )


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
