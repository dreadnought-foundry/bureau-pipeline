"""RED-first: the plan's channel row says WHERE the channel is (DRE-3568).

The engine's own release surface — the `stable` channel — was the one deploy
in the fleet the release train reported as "another train's business" and
never said anything about. On 2026-09-10 `stable` sat at b860c24 (21:41 PT
the night before) for the whole working day, 39 commits behind `main`, while
every `promote-channel.yml` run concluded `success`: the promotion gate
(`harness.yml` on `main`) failed on every push and nothing named that on any
run, any page or any card. A run that did not ship read green — the DRE-3526
shape, for the channel.

The train already declares the channel as a surface and already produces a
decision for it. These tests make that decision carry the channel's actual
state — `channel-current`, `channel-advancing`, `channel-blocked`, and (by
console-honesty rule 1, an unreadable answer is UNKNOWN, never a green)
`channel-unknown` — and make the promotion run say when it declined.

Every GitHub read is injectable: the pure reader is driven from a fixture of
the 2026-09-10 reads, and the CLI leg puts a fake `gh` on PATH, the way
`test_release_train.py` already drives `fetch_checks`. Nothing here touches
the network.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_train  # noqa: E402

OWN_DATA = ROOT / ".github" / "bureau" / "release.json"
DOC = ROOT / "docs" / "release-train.md"
TRAIN_WORKFLOW = ROOT / ".github" / "workflows" / "release-train.yml"
PROMOTE_WORKFLOW = ROOT / ".github" / "workflows" / "promote-channel.yml"
HARNESS_WORKFLOW = ROOT / ".github" / "workflows" / "harness.yml"
FIXTURE = ROOT / "tests" / "fixtures" / "pipeline-channel-2026-09-10.json"

REPO = "dreadnought-foundry/bureau-pipeline"
UTC = timezone.utc

# The card's three named scenarios. The real run (34483381786) failed five
# at setup; the card's list is a subset of it, and the test asserts the subset.
CARD_SCENARIOS = {"agent_task_parses", "dependabot_flow", "gate_paths"}

# The console's deferral regex, RESTATED from agent-bureau's
# `console/backend/monitors/release_train_health.py` (`_DEFERRED_RECEIPT`).
# The console cannot import this repo and this repo cannot import the console,
# so the contract is pinned by copying the pattern here: if the existing
# receipt line ever stops matching it, this test goes red before the console's
# monitor goes quiet.
CONSOLE_DEFERRED_RECEIPT = re.compile(
    r"release-train: no-op \S+ (?P<surface>\S+) — deferred:\s*(?P<reason>.+?)\s*$")


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def pt(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=release_train.PT)


def _channel_surface():
    return release_train.surfaces(release_train.load(OWN_DATA))["pipeline-channel"]


def _channel(**over) -> "release_train.Channel":
    """A channel row, blocked by default — the incident's shape."""
    fields = dict(
        state=release_train.CHANNEL_BLOCKED,
        behind=39,
        since=datetime(2026, 9, 10, 4, 41, 16, tzinfo=UTC),
        tag_sha="b860c24144b66e918117a09d17e7bcac08cfcf7d",
        head_sha="b7e9e2c978302a60fb424312477171853629f216",
        gate_url="https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34483381786",
        scenarios=("agent_task_parses", "bot_pr_flow", "dependabot_flow",
                   "gate_paths", "lane_contract"),
        detail="the gate's latest run on main failed",
    )
    fields.update(over)
    return release_train.Channel(**fields)


def _decide(channel, now=None, **kw):
    return release_train.decide(
        _channel_surface(), now or pt(2026, 9, 10, 8, 41), None, "behind",
        release_train.ASSUMED_GREEN, None, channel=channel, **kw)


# --------------------------------------------------------------------------
# 1. The three states, from `decide()`.
# --------------------------------------------------------------------------

def test_a_channel_behind_with_a_failed_gate_is_blocked_and_names_run_scenarios_and_hours():
    """Acceptance 1: `stable` behind, the latest harness run on main failed —
    the decision is `channel-blocked` and its sentence names the run URL, the
    failing scenarios and how long the channel has been behind."""
    decision = _decide(_channel(), now=datetime(2026, 9, 10, 15, 41, 16, tzinfo=UTC))
    assert decision.act == release_train.NO_OP
    assert decision.code == "channel-blocked"
    assert "actions/runs/34483381786" in decision.reason
    for name in CARD_SCENARIOS:
        assert name in decision.reason, name
    assert "39 commits" in decision.reason
    assert "11 hours" in decision.reason, decision.reason
    # The train still never RUNS the channel — reporting, not promotion.
    assert "never runs" in decision.reason and "channel" in decision.reason
    assert decision.channel is not None and decision.channel.state == "blocked"


def test_a_channel_behind_with_the_gate_running_is_advancing_and_never_warns():
    running = _channel(state=release_train.CHANNEL_ADVANCING, scenarios=(),
                       detail="the gate is in_progress on b7e9e2c")
    decision = _decide(running)
    assert decision.act == release_train.NO_OP
    assert decision.code == "channel-advancing"
    assert "39 commits" in decision.reason
    assert "actions/runs/34483381786" in decision.reason
    assert not release_train.channel_warns(decision)


def test_a_channel_behind_with_the_gate_green_is_advancing():
    green = _channel(state=release_train.CHANNEL_ADVANCING, scenarios=(),
                     detail="the gate concluded success on 51fe2b4")
    assert _decide(green).code == "channel-advancing"


def test_a_channel_at_the_head_is_current_and_never_warns():
    current = _channel(state=release_train.CHANNEL_CURRENT, behind=0, since=None,
                       tag_sha="b7e9e2c978302a60fb424312477171853629f216",
                       gate_url=None, scenarios=(), detail="stable is at the head")
    decision = _decide(current)
    assert decision.act == release_train.NO_OP
    assert decision.code == "channel-current"
    assert "never runs" in decision.reason
    assert not release_train.channel_warns(decision)


def test_a_blocked_channel_warns_and_an_unknown_one_warns():
    assert release_train.channel_warns(_decide(_channel()))
    unknown = _channel(state=release_train.CHANNEL_UNKNOWN, behind=None,
                       since=None, tag_sha=None, gate_url=None, scenarios=(),
                       detail="gh could not read git/ref/tags/stable")
    decision = _decide(unknown)
    assert decision.code == "channel-unknown"
    assert release_train.channel_warns(decision)


def test_a_channel_row_nobody_read_is_unknown_never_current():
    """Console-honesty rule 1: a number never read is not 0, and an answer
    nobody fetched is not `current`. `decide()` without a channel state is
    `channel-unknown`, which warns."""
    decision = _decide(None)
    assert decision.act == release_train.NO_OP
    assert decision.code == "channel-unknown"
    assert "never runs" in decision.reason


def test_the_brake_still_outranks_the_channel_row():
    decision = _decide(_channel(), brake="2026-09-10 who=Ada")
    assert decision.act == release_train.HELD


# --------------------------------------------------------------------------
# 2. The ONE receipt line the console parses — byte-stable.
# --------------------------------------------------------------------------

def test_the_channel_receipt_line_is_byte_stable():
    """The sibling card (agent-bureau, DRE-3569) parses exactly this."""
    assert _channel().receipt() == (
        "pipeline-channel: state=blocked behind=39 since=2026-09-10T04:41:16Z "
        "tag=b860c24 head=b7e9e2c "
        "gate=https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34483381786 "
        "scenarios=agent_task_parses,bot_pr_flow,dependabot_flow,gate_paths,lane_contract"
    )


def test_a_current_channel_receipt_says_none_where_there_is_nothing():
    current = _channel(state=release_train.CHANNEL_CURRENT, behind=0, since=None,
                       tag_sha="b7e9e2c978302a60fb424312477171853629f216",
                       gate_url=None, scenarios=())
    assert current.receipt() == (
        "pipeline-channel: state=current behind=0 since=none tag=b7e9e2c "
        "head=b7e9e2c gate=none scenarios=none")


def test_an_unknown_channel_receipt_never_prints_a_number_it_did_not_read():
    """Rule 2: `behind=0` would be a lie; the receipt says `unknown`."""
    unknown = _channel(state=release_train.CHANNEL_UNKNOWN, behind=None,
                       since=None, tag_sha=None, gate_url=None, scenarios=())
    line = unknown.receipt()
    assert line.startswith("pipeline-channel: state=unknown behind=unknown since=none tag=none ")
    assert "behind=0" not in line


def test_the_receipt_is_one_line_with_no_spaces_inside_a_value():
    line = _channel(detail="a detail with spaces, and a comma").receipt()
    assert "\n" not in line
    for token in line.split(" ")[1:]:
        assert "=" in token, token


def test_the_existing_deferred_receipt_still_matches_the_consoles_regex():
    """The console's `release_train_health.py` parses
    `release-train: no-op <repo> <surface> — deferred: <reason>`. That line is
    unchanged by this card and this test says so — the channel row adds a
    SECOND line, it never rewrites the first."""
    deferred = release_train.Decision(
        release_train.NO_OP, "deferred", "deferred: the deployment is owed to Ada")
    line = deferred.receipt(REPO, "console")
    match = CONSOLE_DEFERRED_RECEIPT.search(line)
    assert match, line
    assert match.group("surface") == "console"
    assert match.group("reason") == "the deployment is owed to Ada"
    # And the channel's own no-op line keeps the same `<TAG>: no-op <repo>
    # <surface> — <reason>` shape the console's other patterns search.
    channel = _decide(_channel())
    assert channel.receipt(REPO, "pipeline-channel").startswith(
        "release-train: no-op dreadnought-foundry/bureau-pipeline pipeline-channel — ")


# --------------------------------------------------------------------------
# 3. The reader, over the 2026-09-10 reads. Fails on main today.
# --------------------------------------------------------------------------

def test_the_2026_09_10_fixture_replays_blocked_39_behind_with_the_scenarios_named():
    fixture = _fixture()
    channel = release_train.read_channel(
        head=fixture["head"], ref=fixture["ref"], compare=fixture["compare"],
        run=fixture["run"], log_text=fixture["log_failed"])
    assert channel.state == release_train.CHANNEL_BLOCKED
    assert channel.behind == 39
    assert channel.since == datetime(2026, 9, 10, 4, 41, 16, tzinfo=UTC)
    assert channel.tag_sha.startswith("b860c24")
    assert channel.head_sha.startswith("b7e9e2c")
    assert channel.gate_url.endswith("/actions/runs/34483381786")
    assert CARD_SCENARIOS <= set(channel.scenarios)
    assert channel.receipt() == (
        "pipeline-channel: state=blocked behind=39 since=2026-09-10T04:41:16Z "
        "tag=b860c24 head=b7e9e2c "
        "gate=https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34483381786 "
        "scenarios=agent_task_parses,bot_pr_flow,dependabot_flow,gate_paths,lane_contract"
    )


def test_the_plan_carries_the_fixture_as_channel_blocked_on_this_repos_own_declaration():
    fixture = _fixture()
    read = release_train.read_channel(
        head=fixture["head"], ref=fixture["ref"], compare=fixture["compare"],
        run=fixture["run"], log_text=fixture["log_failed"])
    planned = release_train.plan(
        release_train.load(OWN_DATA), repo_root=ROOT, head=fixture["head"],
        now=datetime(2026, 9, 10, 16, 0, tzinfo=UTC), brake=None,
        checks_for=lambda _sha: release_train.ASSUMED_GREEN,
        channel_for=lambda _surface, _head: read, repo=REPO)
    [(entry, decision)] = planned
    assert entry.name == "pipeline-channel"
    assert decision.code == "channel-blocked"
    assert decision.channel == read
    assert release_train.matrix(planned) == []


def test_harness_failures_are_read_from_the_summary_block_only():
    """`gh run view --log-failed` prefixes every line with `job<TAB>step<TAB>
    timestamp`; the scenario lines are found by search, not anchor, and only
    after `== harness summary ==` — an error line quoting a scenario's name
    is not a verdict."""
    fixture = _fixture()
    assert release_train.harness_failures(fixture["log_failed"]) == (
        ("agent_task_parses", "FAIL at setup"),
        ("bot_pr_flow", "FAIL at setup"),
        ("dependabot_flow", "FAIL at setup"),
        ("gate_paths", "FAIL at setup"),
        ("lane_contract", "FAIL at setup"),
    )
    # A sandbox-blocked run (DRE-3076) names its scenario the same way.
    blocked = ("== harness summary ==\n  bot_pr_flow: PASS\n"
               "  gate_paths: BLOCKED at verify\n")
    assert release_train.harness_failures(blocked) == (("gate_paths", "BLOCKED at verify"),)
    assert release_train.harness_failures("no summary here: FAIL at x\n") == ()
    assert release_train.harness_failures(None) == ()


def test_a_tag_at_the_head_reads_current_without_reading_the_gate():
    head = "b7e9e2c978302a60fb424312477171853629f216"
    channel = release_train.read_channel(
        head=head, ref={"sha": head, "type": "commit"},
        compare={"status": "identical", "ahead_by": 0, "behind_by": 0,
                 "base_sha": head, "base_date": "2026-09-10T18:00:00Z"},
        run=None, log_text=None)
    assert channel.state == release_train.CHANNEL_CURRENT
    assert channel.behind == 0
    assert channel.since is None
    assert channel.gate_url is None


@pytest.mark.parametrize("status,conclusion", [
    ("in_progress", None), ("queued", None), ("completed", "success"),
])
def test_a_gate_running_or_green_past_the_channel_reads_advancing(status, conclusion):
    fixture = _fixture()
    run = dict(fixture["run"], status=status, conclusion=conclusion)
    channel = release_train.read_channel(
        head=fixture["head"], ref=fixture["ref"], compare=fixture["compare"],
        run=run, log_text=None)
    assert channel.state == release_train.CHANNEL_ADVANCING
    assert channel.scenarios == ()
    assert channel.gate_url == run["html_url"]


def test_a_green_gate_run_on_the_channels_own_commit_is_not_advancing():
    """The Actions-budget-block shape: the newest harness run on main proved
    the commit `stable` already sits on, and nothing has judged the 39 commits
    since. Promotion is not coming, and `advancing` would be the lie."""
    fixture = _fixture()
    run = dict(fixture["run"], status="completed", conclusion="success",
               head_sha=fixture["ref"]["sha"])
    channel = release_train.read_channel(
        head=fixture["head"], ref=fixture["ref"], compare=fixture["compare"],
        run=run, log_text=None)
    assert channel.state == release_train.CHANNEL_BLOCKED
    assert "no harness run" in channel.detail.lower() or "since" in channel.detail.lower()


def test_a_cancelled_latest_gate_run_is_blocked_with_no_scenarios():
    fixture = _fixture()
    run = dict(fixture["run"], conclusion="cancelled")
    channel = release_train.read_channel(
        head=fixture["head"], ref=fixture["ref"], compare=fixture["compare"],
        run=run, log_text=None)
    assert channel.state == release_train.CHANNEL_BLOCKED
    assert channel.scenarios == ()
    assert "cancelled" in channel.detail


def test_no_gate_run_on_the_branch_at_all_is_blocked_not_advancing():
    fixture = _fixture()
    channel = release_train.read_channel(
        head=fixture["head"], ref=fixture["ref"], compare=fixture["compare"],
        run=None, log_text=None)
    assert channel.state == release_train.CHANNEL_BLOCKED
    assert channel.gate_url is None


def test_no_channel_ref_at_all_is_unknown_not_current():
    fixture = _fixture()
    channel = release_train.read_channel(
        head=fixture["head"], ref=None, compare=None, run=None, log_text=None)
    assert channel.state == release_train.CHANNEL_UNKNOWN
    assert channel.behind is None


# --------------------------------------------------------------------------
# 4. The CLI: a fake `gh` on PATH, the way `fetch_checks` is driven.
# --------------------------------------------------------------------------

def _fake_gh(tmp_path, monkeypatch, *, run_override=None, fail_on=None):
    """A `gh` that answers the four reads from the fixture and logs each call.

    `run_override` replaces the fixture's run; `fail_on` makes the call whose
    arguments contain that substring exit non-zero, the way a real 5xx does.
    """
    fixture = _fixture()
    if run_override is not None:
        fixture["run"] = run_override
    data = tmp_path / "fixture.json"
    data.write_text(json.dumps(fixture))
    calls = tmp_path / "calls.log"
    fake = tmp_path / "bin"
    fake.mkdir()
    fail = f'  *"{fail_on}"*) echo "boom" >&2; exit 1 ;;\n' if fail_on else ""
    (fake / "gh").write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$*\" >> {calls}\n"
        "case \"$*\" in\n"
        f"{fail}"
        "  *git/ref/tags/stable*) python3 -c 'import json; "
        f"print(json.dumps(json.load(open(\"{data}\"))[\"ref\"]))' ;;\n"
        "  *compare/*) python3 -c 'import json; "
        f"print(json.dumps(json.load(open(\"{data}\"))[\"compare\"]))' ;;\n"
        "  *workflows/harness.yml/runs*) python3 -c 'import json; "
        f"r=json.load(open(\"{data}\"))[\"run\"]; print(json.dumps(r) if r else \"\")' ;;\n"
        "  *actions/runs/*) python3 -c 'import json; "
        f"print(json.dumps(json.load(open(\"{data}\"))[\"run\"]))' ;;\n"
        "  *--log-failed*) python3 -c 'import json; "
        f"print(json.load(open(\"{data}\"))[\"log_failed\"], end=\"\")' ;;\n"
        "  *) echo unexpected \"$*\" >&2; exit 9 ;;\n"
        "esac\n"
    )
    (fake / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    return calls


def test_the_plan_cli_prints_the_receipt_warns_and_writes_a_blocked_summary(
        tmp_path, monkeypatch, capsys):
    """Acceptance 1, end to end: the run shows a `::warning::` and a
    `## Blocked` step summary, and the receipt line is on stdout."""
    calls = _fake_gh(tmp_path, monkeypatch)
    summary = tmp_path / "summary.md"
    output = tmp_path / "output.txt"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.delenv("RELEASE_HOLD", raising=False)
    head = _fixture()["head"]

    code = release_train.main([
        "--repo", REPO, "--repo-root", str(ROOT), "--file", str(OWN_DATA),
        "plan", "--head", head,
    ])
    out = capsys.readouterr().out
    assert code == 0, out
    assert ("release-train: no-op dreadnought-foundry/bureau-pipeline "
            "pipeline-channel — ") in out
    assert "pipeline-channel: state=blocked behind=39 since=2026-09-10T04:41:16Z" in out
    assert "::warning" in out
    assert "## Blocked" in summary.read_text()
    assert "actions/runs/34483381786" in summary.read_text()
    assert "matrix=[]" in output.read_text()

    logged = calls.read_text().splitlines()
    assert any("git/ref/tags/stable" in line for line in logged)
    assert any("compare/stable..." in line for line in logged)
    assert any("workflows/harness.yml/runs" in line and "branch=main" in line
               for line in logged)
    assert any("--log-failed" in line for line in logged)
    # Read-only, and a few reads: the ref, the compare, the run and its log.
    assert len(logged) == 4, logged
    assert not any("--method" in line for line in logged)


def test_the_plan_cli_reads_no_log_when_the_gate_is_green_or_running(
        tmp_path, monkeypatch, capsys):
    fixture = _fixture()
    running = dict(fixture["run"], status="in_progress", conclusion=None)
    calls = _fake_gh(tmp_path, monkeypatch, run_override=running)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.delenv("RELEASE_HOLD", raising=False)

    code = release_train.main([
        "--repo", REPO, "--repo-root", str(ROOT), "--file", str(OWN_DATA),
        "plan", "--head", fixture["head"],
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "pipeline-channel: state=advancing behind=39" in out
    assert "::warning" not in out
    assert not summary.exists() or "## Blocked" not in summary.read_text()
    assert not any("--log-failed" in line for line in calls.read_text().splitlines())


def test_a_channel_read_that_fails_is_unknown_warns_and_does_not_redden_the_train(
        tmp_path, monkeypatch, capsys):
    """Rule 1: unreadable is UNKNOWN. It is reported loudly (a warning and a
    summary) but the plan exits 0 — the row releases nothing, and a red train
    on an API blip would be a second kind of noise."""
    _fake_gh(tmp_path, monkeypatch, fail_on="compare/")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.delenv("RELEASE_HOLD", raising=False)

    code = release_train.main([
        "--repo", REPO, "--repo-root", str(ROOT), "--file", str(OWN_DATA),
        "plan", "--head", _fixture()["head"],
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "pipeline-channel: state=unknown behind=unknown" in out
    assert "state=current" not in out and "state=advancing" not in out
    assert "::warning" in out
    assert "## Unknown" in summary.read_text()


def test_the_channel_subcommand_prints_the_same_receipt_for_promote_channel(
        tmp_path, monkeypatch, capsys):
    """`promote-channel.yml` prints the SAME line through the same code, so
    the console never needs a second format. `--gate-run` names the harness
    run that triggered the promotion attempt instead of listing runs."""
    calls = _fake_gh(tmp_path, monkeypatch)
    output = tmp_path / "output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    fixture = _fixture()

    code = release_train.main([
        "--repo", REPO, "channel", "--head", fixture["head"],
        "--gate-run", str(fixture["run"]["id"]),
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert out.splitlines()[0] == (
        "pipeline-channel: state=blocked behind=39 since=2026-09-10T04:41:16Z "
        "tag=b860c24 head=b7e9e2c "
        "gate=https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34483381786 "
        "scenarios=agent_task_parses,bot_pr_flow,dependabot_flow,gate_paths,lane_contract"
    )
    written = output.read_text()
    assert "state=blocked\n" in written
    assert "scenarios=agent_task_parses,bot_pr_flow,dependabot_flow,gate_paths,lane_contract\n" in written
    logged = calls.read_text().splitlines()
    assert any("actions/runs/34483381786" in line for line in logged)
    assert not any("workflows/harness.yml/runs" in line for line in logged)


# --------------------------------------------------------------------------
# 5. The gate is what promote-channel listens to — one name for one fact.
# --------------------------------------------------------------------------

def test_the_channel_gate_is_the_workflow_promote_channel_listens_to():
    harness = yaml.safe_load(HARNESS_WORKFLOW.read_text())
    promote = yaml.safe_load(PROMOTE_WORKFLOW.read_text())
    on = promote.get("on", promote.get(True))
    assert release_train.CHANNEL_GATE == "harness.yml"
    assert harness["name"] in on["workflow_run"]["workflows"]


# --------------------------------------------------------------------------
# 6. promote-channel.yml says when it does not advance.
# --------------------------------------------------------------------------

def _promote():
    return yaml.safe_load(PROMOTE_WORKFLOW.read_text())


def test_promote_channel_can_read_the_gating_runs_log():
    perms = _promote()["permissions"]
    assert perms.get("actions") == "read", perms
    assert perms.get("contents") == "read"


def test_promote_channel_prints_the_channel_receipt_through_release_train():
    steps = _promote()["jobs"]["promote"]["steps"]
    channel = [s for s in steps if "release_train.py" in str(s.get("run", ""))]
    assert len(channel) == 1, "one step prints the channel receipt"
    step = channel[0]
    assert " channel " in step["run"] or step["run"].rstrip().endswith("channel") \
        or "channel \\" in step["run"]
    assert "--gate-run" in step["run"]
    assert "head_branch == 'main'" in str(step.get("if", "")), (
        "a PR-head run is not about the channel and spends no reads")
    assert "GH_TOKEN" in step.get("env", {})


def test_promote_channel_warns_and_writes_did_not_promote_when_it_declines():
    """Acceptance 3: on a main run that does not move the tag, one
    `::warning::` and a `## Did not promote` summary naming the candidate
    sha, the gating run and the scenarios — and the job still exits 0."""
    text = PROMOTE_WORKFLOW.read_text()
    assert "## Did not promote" in text
    assert "::warning" in text
    say = [s for s in _promote()["jobs"]["promote"]["steps"]
           if "Did not promote" in str(s.get("run", ""))]
    assert len(say) == 1
    run = say[0]["run"]
    env = say[0].get("env", {})
    assert "workflow_run.head_sha" in str(env) or "CANDIDATE" in run
    assert "HARNESS_RUN" in run
    assert "SCENARIOS" in run or "scenarios" in run
    assert "exit 1" not in run
    # Declined = the decision's own outcome, not a guess from the conclusion.
    assert "steps.decide.outputs.outcome" in str(env) or "OUTCOME" in run


# --------------------------------------------------------------------------
# 7. The document.
# --------------------------------------------------------------------------

def test_the_render_names_the_channel_states_and_still_says_the_train_never_runs_one():
    rendered = release_train.render_markdown()
    for code in ("channel-current", "channel-advancing", "channel-blocked",
                 "channel-unknown"):
        assert code in rendered, code
    assert "never runs" in rendered
    assert DOC.read_text() == rendered, (
        "docs/release-train.md is stale — `python3 scripts/release_train.py render`")


def test_the_train_workflow_comment_no_longer_claims_a_channel_surface_reads_nothing():
    """The plan step's comment said a surface with nothing to release reads
    no check — true of `tag` surfaces, and now false of the channel row,
    which reads the ref, the compare, the gate's run and (when red) its
    log. A comment that contradicts the code is updated in the same PR."""
    text = TRAIN_WORKFLOW.read_text()
    assert "channel" in text.lower()
    assert "git/ref/tags" in text or "harness" in text.lower()
