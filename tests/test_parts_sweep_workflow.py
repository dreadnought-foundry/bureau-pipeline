"""RED-first: a Parts Sweep workflow runs every Sunday before the briefing,
files each new copy's cleanup card once, records each provisional part's
outcome and posts the weekly parts list (DRE-8945).

`.github/workflows/parts-sweep.yml` (`Parts Sweep`) runs on `26 10 * * 0` and
on `workflow_dispatch` with an optional `repo` and a `dry_run`. Its `roster`
job reads the roster live (`parts_sweep.py roster`, DRE-8944). Its `sweep` job
runs one leg per row: a read mint for that one repository, then exactly one of
an `unread`, `failed` or `archived` record, or the count, the filer
(`parts_cleanup.py file`) for a listed repo, the record mint and the
provisional scan (`parts_provisional.py scan`, DRE-8941), and always the
upload of `parts-sweep-<slug>`. Its `weekly` job posts the week's list
(`parts_weekly.py post`, DRE-9055), and only ever a whole roster's.

What this file holds shut:

  * the triggers, the cron and its minute no other workflow fires on, the
    `parts-count` concurrency group both filers share;
  * the jobs' shape: `sweep` a matrix over `roster`'s rows one at a time,
    `weekly` after both and only on a `roster` that succeeded;
  * the four repo cases of the epic's contract 6, by evaluating every step's
    `if:` the way GitHub does over each case — which steps run, which do not;
  * the three mints' permissions, the record mint's token and login on the
    scan, no `permission-issues` anywhere;
  * the artifact's two files, and no step that would print a record into the
    public log;
  * the Linear key on the three steps that reach Linear, `LINEAR_IDENTITY`,
    `--card`, `--dry-run`, the checkouts and the pins;
  * the `# Linear reads (DRE-8783):` block naming the four reads;
  * the steps' own scripts, run as written against the real scripts or a
    recorder of their argv;
  * the three `log_lines` entries in `config/proof-reach.json`, and the
    watchers.

Run: cd bureau-pipeline && python3 -m pytest tests/test_parts_sweep_workflow.py -q
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

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "parts-sweep.yml"
PARTS_COUNT = WORKFLOWS / "parts-count.yml"
REPAIR_STUB = WORKFLOWS / "self-red-main-repair.yml"
MEDIC_STUB = WORKFLOWS / "self-medic.yml"
PROOF_REACH = ROOT / "config" / "proof-reach.json"
sys.path.insert(0, str(ROOT / "scripts"))

import check_action_pins  # noqa: E402
import check_workflow_contexts  # noqa: E402
import check_workflow_watchers  # noqa: E402
import parts_provisional  # noqa: E402
import parts_sweep  # noqa: E402
import parts_weekly  # noqa: E402

NAME = "Parts Sweep"
CRON = "26 10 * * 0"
MINUTE = 26
PIPELINE_PATH = ".bureau-pipeline"
MINT_ACTION = "actions/create-github-app-token"
CHECKOUT_ACTION = "actions/checkout"
UPLOAD_ACTION = "actions/upload-artifact"
DOWNLOAD_ACTION = "actions/download-artifact"
LINEAR_KEY = "${{ secrets.LINEAR_API_KEY }}"
CARD_VAR = "${{ vars.PARTS_WEEKLY_CARD }}"
UNREAD_REASON = "the read token could not be minted for $OWNER/$NAME"
FAILED_REASON = "the read token could not be minted for $OWNER/$NAME, inside this organization"
ARCHIVED_REASON = "archived on GitHub, so it is read-only and not swept"
GATE_MINTED = "steps.read_app.outcome == 'success'"
GATE_LIVE = "steps.archived.outputs.archived == 'false'"
GATE_COUNTED = "steps.count.outputs.counted == 'true'"
LINEAR_READS = "# Linear reads (DRE-8783):"


# --------------------------------------------------------------------------
# reading the files
# --------------------------------------------------------------------------
def _load(path: Path = WORKFLOW) -> dict:
    assert path.is_file(), f"missing workflow {path.name}"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _on(doc: dict) -> dict:
    # PyYAML parses a bare `on:` key as the boolean True.
    on = doc.get("on", doc.get(True))
    assert isinstance(on, dict), on
    return on


def _job(job_id: str) -> dict:
    jobs = _load()["jobs"]
    assert job_id in jobs, f"no `{job_id}` job: {sorted(jobs)}"
    return jobs[job_id]


def _steps(job_id: str) -> list:
    return _job(job_id)["steps"]


def _step_id(job_id: str, step_id: str) -> dict:
    found = [s for s in _steps(job_id) if s.get("id") == step_id]
    assert len(found) == 1, f"{job_id}: {len(found)} step(s) with id {step_id!r}"
    return found[0]


def _step_running(job_id: str, needle: str) -> dict:
    found = [s for s in _steps(job_id) if needle in str(s.get("run", ""))]
    assert len(found) == 1, f"{job_id}: {len(found)} step(s) running {needle!r}"
    return found[0]


def _steps_using(job_id: str, action: str) -> list:
    return [s for s in _steps(job_id) if str(s.get("uses", "")).split("@")[0] == action]


def _index(job_id: str, step: dict) -> int:
    return _steps(job_id).index(step)


def _record_step(state: str) -> dict:
    return _step_running("sweep", f"--state {state}")


def _count_step() -> dict:
    return _step_id("sweep", "count")


def _file_step() -> dict:
    return _step_running("sweep", "parts_cleanup.py file")


def _scan_step() -> dict:
    return _step_running("sweep", "parts_provisional.py scan")


def _post_step() -> dict:
    return _step_running("weekly", "parts_weekly.py post")


def _upload_step() -> dict:
    found = _steps_using("sweep", UPLOAD_ACTION)
    assert len(found) == 1, f"sweep: {len(found)} upload step(s)"
    return found[0]


def _every_step():
    for job_id, job in _load()["jobs"].items():
        for step in job["steps"]:
            yield job_id, step


def _watch_list(stub: Path) -> list:
    return _on(_load(stub))["workflow_run"]["workflows"]


# --------------------------------------------------------------------------
# GitHub's `if:`, evaluated: enough of the expression language for this file
# --------------------------------------------------------------------------
_TOKEN = re.compile(r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<op>&&|\|\||==|!=|!|\(|\))"
                    r"|(?P<call>always|success|failure|cancelled)\(\)"
                    r"|(?P<lit>true|false|null)\b|(?P<ref>[A-Za-z_][\w.-]*))")
_STATUS = re.compile(r"\b(?:always|success|failure|cancelled)\(\)")


def _norm(value):
    # GitHub compares strings case-insensitively.
    return value.casefold() if isinstance(value, str) else value


def _evaluate(expr, context: dict, failed: bool = False):
    """The value of a GitHub expression over `context`, with the implied
    `success() &&` on an `if:` that names no status function."""
    text = str(expr).strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    out, pos = [], 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        assert match and match.end() > pos, f"cannot read {text[pos:]!r} in {text!r}"
        pos = match.end()
        if match["str"] is not None:
            out.append(repr(_norm(match["str"][1:-1].replace("''", "'"))))
        elif match["op"] is not None:
            out.append({"&&": " and ", "||": " or ", "!": " not ", "==": " == ",
                        "!=": " != ", "(": "(", ")": ")"}[match["op"]])
        elif match["call"] is not None:
            out.append({"always": "True", "success": repr(not failed),
                        "failure": repr(failed), "cancelled": "False"}[match["call"]])
        elif match["lit"] is not None:
            out.append({"true": "True", "false": "False", "null": "None"}[match["lit"]])
        else:
            out.append(f"_ref({match['ref']!r})")
    return eval("".join(out), {"_ref": lambda name: _norm(context.get(name))})  # noqa: S307


def _runs(step: dict, context: dict, failed: bool) -> bool:
    condition = step.get("if")
    if condition is None:
        return not failed
    value = _evaluate(condition, context, failed)
    if not _STATUS.search(str(condition)):
        value = (not failed) and value
    return bool(value)


def _label(step: dict) -> str:
    return step.get("id") or step.get("name") or step.get("uses")


ORG = "dreadnought-foundry"


def _simulate(*, minted: bool, owner: str = ORG, archived: str = "false",
              counted: str = "true", listed: bool = True, dry_run=None) -> list:
    """The labels of the `sweep` steps that run for one repo case. A step's
    own outcome is what the case says: the `failed` record exits 1."""
    context = {"github.repository_owner": ORG, "matrix.owner": owner,
               "matrix.listed": listed, "inputs.dry_run": dry_run,
               "steps.read_app.outcome": "success" if minted else "failure"}
    ran, failed = [], False
    for step in _steps("sweep"):
        if not _runs(step, context, failed):
            continue
        ran.append(_label(step))
        if step.get("id") == "archived":
            context["steps.archived.outputs.archived"] = archived
        if step.get("id") == "count":
            context["steps.count.outputs.counted"] = counted
        if "--state failed" in str(step.get("run", "")):
            failed = True
    return ran


# --------------------------------------------------------------------------
# the triggers
# --------------------------------------------------------------------------
def test_it_is_named_parts_sweep():
    assert _load()["name"] == NAME


def test_it_runs_on_the_sunday_cron():
    assert _on(_load())["schedule"] == [{"cron": CRON}]


def test_it_runs_on_a_dispatch_with_an_optional_repo_and_a_dry_run():
    inputs = _on(_load())["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"repo", "dry_run"}
    assert inputs["repo"]["type"] == "string"
    assert inputs["repo"].get("required", False) is False
    assert inputs["repo"].get("default", "") == ""
    assert inputs["dry_run"]["type"] == "boolean"
    assert inputs["dry_run"].get("default", False) is False


def test_it_runs_on_nothing_else():
    assert set(_on(_load())) == {"schedule", "workflow_dispatch"}


def _minutes(field: str) -> set:
    """Every minute a cron minute field fires on."""
    minutes = set()
    for part in field.split(","):
        span, _, step = part.partition("/")
        if span == "*":
            low, high = 0, 59
        elif "-" in span:
            low, high = (int(x) for x in span.split("-"))
        else:
            low = high = int(span)
            if step:
                high = 59
        minutes.update(range(low, high + 1, int(step) if step else 1))
    return minutes


def test_the_minute_expansion_reads_every_form():
    assert _minutes("4-59/10") == {4, 14, 24, 34, 44, 54}
    assert _minutes("*/15") == {0, 15, 30, 45}
    assert _minutes("12,42") == {12, 42}
    assert MINUTE in _minutes("*")
    assert _minutes("6/20") == {6, 26, 46}


def test_no_other_workflow_fires_on_minute_26():
    clashes = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        if path == WORKFLOW:
            continue
        for entry in _on(_load(path)).get("schedule") or []:
            if MINUTE in _minutes(entry["cron"].split()[0]):
                clashes.append(f"{path.name}: {entry['cron']}")
    assert clashes == [], f"another workflow fires on minute {MINUTE}: {clashes}"


def test_it_reads_contents_and_nothing_else():
    doc = _load()
    assert doc["permissions"] == {"contents": "read"}
    for job_id, job in doc["jobs"].items():
        assert "permissions" not in job, f"{job_id} widens the workflow's permissions"


def test_it_shares_the_parts_count_group_with_the_one_time_count():
    for path in (WORKFLOW, PARTS_COUNT):
        concurrency = _load(path)["concurrency"]
        assert concurrency["group"] == "parts-count", path.name
        assert concurrency["cancel-in-progress"] is False, path.name


# --------------------------------------------------------------------------
# the jobs
# --------------------------------------------------------------------------
def test_it_has_the_three_jobs():
    assert set(_load()["jobs"]) == {"roster", "sweep", "weekly"}


def test_the_roster_job_hands_on_the_rows_and_the_week_ago():
    outputs = _job("roster")["outputs"]
    step = _step_running("roster", "parts_sweep.py roster")
    assert outputs["repos"] == f"${{{{ steps.{step['id']}.outputs.repos }}}}"
    assert outputs["week_ago"] == f"${{{{ steps.{step['id']}.outputs.week_ago }}}}"


def test_the_sweep_is_a_matrix_over_the_rows_one_at_a_time():
    job = _job("sweep")
    assert job["needs"] == "roster"
    strategy = job["strategy"]
    assert strategy["matrix"]["include"] == "${{ fromJSON(needs.roster.outputs.repos) }}"
    assert strategy["max-parallel"] == 1
    assert strategy["fail-fast"] is False


def test_the_sweep_is_skipped_on_a_roster_with_no_rows():
    assert "needs.roster.outputs.repos != '[]'" in _job("sweep")["if"]


def test_the_weekly_post_needs_both_and_runs_only_on_a_roster_that_succeeded():
    job = _job("weekly")
    assert job["needs"] == ["roster", "sweep"]
    assert job["if"] == "${{ always() && needs.roster.result == 'success' }}"
    for roster, sweep, runs in (("success", "success", True), ("success", "failure", True),
                                ("success", "skipped", True), ("failure", "skipped", False),
                                ("cancelled", "skipped", False)):
        context = {"needs.roster.result": roster, "needs.sweep.result": sweep}
        assert _runs(job, context, failed=sweep != "success") is runs, (roster, sweep)


def test_both_spenders_declare_the_fleet_linear_identity():
    assert _job("sweep")["env"] == {"LINEAR_IDENTITY": "fleet"}
    assert _job("weekly")["env"] == {"LINEAR_IDENTITY": "fleet"}
    assert "env" not in _job("roster")


# --------------------------------------------------------------------------
# the four repo cases (contract 6)
# --------------------------------------------------------------------------
def test_the_read_mint_is_for_the_one_repository_and_may_fail():
    mint = _step_id("sweep", "read_app")
    assert mint["uses"].split("@")[0] == MINT_ACTION
    assert mint["continue-on-error"] is True
    with_ = mint["with"]
    assert with_["app-id"] == "${{ secrets.BUREAU_APP_ID }}"
    assert with_["private-key"] == "${{ secrets.BUREAU_APP_PRIVATE_KEY }}"
    assert with_["owner"] == "${{ matrix.owner }}"
    assert with_["repositories"] == "${{ matrix.name }}"
    permissions = {k: v for k, v in with_.items() if k.startswith("permission-")}
    assert permissions == {"permission-contents": "read", "permission-pull-requests": "read"}
    assert _index("sweep", mint) == 0


def test_the_record_steps_carry_the_contracts_reasons():
    unread, failed, archived = (_record_step(s) for s in ("unread", "failed", "archived"))
    assert f'--reason "{UNREAD_REASON}"' in unread["run"]
    assert f'--reason "{FAILED_REASON}"' in failed["run"]
    assert f'--reason "{ARCHIVED_REASON}"' in archived["run"]
    for step in (unread, failed, archived):
        assert "parts_sweep.py record" in step["run"]
        assert "--json-out leg/count.json" in step["run"]
        assert step["env"]["OWNER"] == "${{ matrix.owner }}"
        assert step["env"]["NAME"] == "${{ matrix.name }}"
        assert step["env"]["SLUG"] == "${{ matrix.repo }}"
        assert "continue-on-error" not in step


def test_the_archived_read_uses_the_read_token():
    step = _step_id("sweep", "archived")
    assert step["env"]["GH_TOKEN"] == "${{ steps.read_app.outputs.token }}"
    assert "--jq .archived" in step["run"]
    assert step["if"] == GATE_MINTED


def test_the_gated_steps_name_the_mint_and_the_archive():
    for step in (_count_step(), _file_step(), _step_id("sweep", "record_app"),
                 _step_id("sweep", "qa"), _scan_step()):
        assert GATE_MINTED in step["if"], _label(step)
        assert GATE_LIVE in step["if"], _label(step)
    for step in (_file_step(), _step_id("sweep", "record_app"), _step_id("sweep", "qa"),
                 _scan_step()):
        assert GATE_COUNTED in step["if"], _label(step)


def test_the_upload_runs_always():
    assert _upload_step()["if"] == "always()"


WORK = {"count", "File the cleanup cards", "record_app", "qa",
        "Record each provisional part's outcome"}


def test_a_repo_outside_the_organization_the_token_cannot_reach_is_unread():
    ran = _simulate(minted=False, owner="EveryBite")
    assert _label(_record_step("unread")) in ran
    assert _label(_record_step("failed")) not in ran
    assert _label(_record_step("archived")) not in ran
    assert not WORK & set(ran), ran
    assert _label(_upload_step()) in ran


def test_a_repo_inside_the_organization_the_token_cannot_reach_is_failed():
    ran = _simulate(minted=False, owner="Dreadnought-Foundry")
    assert _label(_record_step("failed")) in ran
    assert _label(_record_step("unread")) not in ran
    assert not WORK & set(ran), ran
    assert _label(_upload_step()) in ran


def test_an_archived_repo_is_recorded_and_nothing_else_runs():
    ran = _simulate(minted=True, archived="true")
    assert _label(_record_step("archived")) in ran
    assert _label(_record_step("unread")) not in ran
    assert _label(_record_step("failed")) not in ran
    assert not WORK & set(ran), ran
    assert "Check out the repository" not in ran
    assert _label(_upload_step()) in ran


def test_a_repo_with_no_design_system_is_counted_and_nothing_filed():
    ran = _simulate(minted=True, counted="false")
    assert "count" in ran
    assert not (WORK - {"count"}) & set(ran), ran
    assert _label(_upload_step()) in ran


def test_a_counted_listed_repo_runs_every_step():
    ran = _simulate(minted=True)
    assert WORK <= set(ran), ran
    for state in ("unread", "failed", "archived"):
        assert _label(_record_step(state)) not in ran
    assert ran.index("count") < ran.index("File the cleanup cards") < ran.index(
        "record_app") < ran.index("Record each provisional part's outcome")


def test_a_counted_unlisted_repo_is_scanned_and_never_carded():
    ran = _simulate(minted=True, listed=False)
    assert "File the cleanup cards" not in ran
    assert {"record_app", "Record each provisional part's outcome"} <= set(ran)


def test_the_filing_step_is_gated_on_listed_and_passes_the_rows_labels():
    step = _file_step()
    assert "matrix.listed == true" in step["if"]
    env = step["env"]
    assert env["AGENT"] == "${{ matrix.agent }}"
    assert env["INITIATIVE"] == "${{ matrix.initiative }}"
    assert env["OWNED_BY"] == "${{ matrix.owned_by }}"


def test_the_repo_is_checked_out_with_full_history_and_the_pipeline_beside_it():
    checkouts = _steps_using("sweep", CHECKOUT_ACTION)
    repo = [s for s in checkouts
            if s["with"].get("repository") == "${{ matrix.owner }}/${{ matrix.name }}"]
    pipeline = [s for s in checkouts if s["with"].get("path") == PIPELINE_PATH]
    assert len(repo) == 1 and len(pipeline) == 1
    repo, pipeline = repo[0], pipeline[0]
    assert repo["with"]["token"] == "${{ steps.read_app.outputs.token }}"
    assert repo["with"]["fetch-depth"] == 0
    assert "path" not in repo["with"], "the count reads the repo at the workspace root"
    assert repo["if"] == f"{GATE_MINTED} && {GATE_LIVE}"
    assert "repository" not in pipeline["with"] and "ref" not in pipeline["with"], (
        "the pipeline is the run's own commit")
    assert "if" not in pipeline
    # A checkout at the workspace root clears it, so the repo goes first, and
    # every record step reads the pipeline's script.
    assert _index("sweep", repo) < _index("sweep", pipeline)
    for state in ("unread", "failed", "archived"):
        assert _index("sweep", pipeline) < _index("sweep", _record_step(state))
    assert _index("sweep", pipeline) < _index("sweep", _count_step())


def test_the_count_takes_the_rosters_week_ago():
    step = _count_step()
    assert step["env"]["WEEK_AGO"] == "${{ needs.roster.outputs.week_ago }}"
    assert '--week-ago "$WEEK_AGO"' in step["run"]
    assert "--count-out leg/parts-count.json" in step["run"]
    assert "--json-out leg/count.json" in step["run"]
    assert "--repo-root ." in step["run"]


# --------------------------------------------------------------------------
# the record mint and the scan
# --------------------------------------------------------------------------
def test_the_record_mint_asks_for_pull_requests_write_only():
    mint = _step_id("sweep", "record_app")
    assert mint["uses"].split("@")[0] == MINT_ACTION
    assert "continue-on-error" not in mint
    with_ = mint["with"]
    assert with_["app-id"] == "${{ secrets.BUREAU_APP_ID }}"
    assert with_["owner"] == "${{ matrix.owner }}"
    assert with_["repositories"] == "${{ matrix.name }}"
    permissions = {k: v for k, v in with_.items() if k.startswith("permission-")}
    assert permissions == {"permission-pull-requests": "write"}


def test_the_record_mint_is_skipped_under_dry_run_and_for_a_repo_not_counted():
    for dry_run in (True, "true"):
        assert "record_app" not in _simulate(minted=True, dry_run=dry_run)
    assert "record_app" not in _simulate(minted=True, counted="false")
    for dry_run in (None, False):
        assert "record_app" in _simulate(minted=True, dry_run=dry_run)


def test_the_scan_takes_the_record_mints_token_and_login():
    env = _scan_step()["env"]
    for key, output in (("GH_TOKEN", "token"), ("WRITER_LOGIN", "app-slug")):
        value = env[key]
        for dry_run, expected in ((None, "record"), (False, "record"), (True, "read")):
            context = {"inputs.dry_run": dry_run,
                       f"steps.read_app.outputs.{output}": "read",
                       f"steps.record_app.outputs.{output}": "record"}
            expression = value.removesuffix("[bot]")
            assert _evaluate(expression, context) == expected, (key, dry_run)
    assert env["WRITER_LOGIN"].endswith("}}[bot]")
    assert '--writer-login "$WRITER_LOGIN"' in _scan_step()["run"]


def test_the_scan_derives_the_qa_login_as_the_merge_gate_does():
    qa = _step_id("sweep", "qa")
    assert qa["with"]["app-id"] == "${{ secrets.BUREAU_QA_APP_ID }}"
    assert qa["with"]["private-key"] == "${{ secrets.BUREAU_QA_APP_PRIVATE_KEY }}"
    assert _scan_step()["env"]["QA_LOGIN"] == "${{ steps.qa.outputs.app-slug }}[bot]"
    assert '--qa-login "$QA_LOGIN"' in _scan_step()["run"]


def test_the_scan_runs_in_the_checkout_with_the_rows_labels():
    step = _scan_step()
    env = step["env"]
    assert env["FULL_NAME"] == "${{ matrix.owner }}/${{ matrix.name }}"
    assert env["AGENT"] == "${{ matrix.agent }}"
    assert env["INITIATIVE"] == "${{ matrix.initiative }}"
    for flag in ('--repo "$SLUG"', '--full-name "$FULL_NAME"', "--repo-root .",
                 '--agent "$AGENT"', '--initiative "$INITIATIVE"',
                 "--json-out leg/provisional.json"):
        assert flag in step["run"], flag


def test_no_step_requests_permission_issues():
    for job_id, step in _every_step():
        assert not any(k.startswith("permission-issues") for k in (step.get("with") or {})), (
            job_id, _label(step))


# --------------------------------------------------------------------------
# what is public (contract 5)
# --------------------------------------------------------------------------
def test_the_leg_uploads_its_two_records_only():
    for job_id, step in _every_step():
        if str(step.get("uses", "")).split("@")[0] != UPLOAD_ACTION:
            continue
        paths = [p.strip() for p in step["with"]["path"].splitlines() if p.strip()]
        assert paths == ["leg/count.json", "leg/provisional.json"], (job_id, paths)
    upload = _upload_step()
    assert upload["with"]["name"] == "parts-sweep-${{ matrix.repo }}"


_PRINTS_A_RECORD = re.compile(
    r"\b(?:cat|head|tail)\b[^\n|;&]*\b(?:leg|in|downloaded)/|\bjq\s+\.(?:\s|$)")


def test_no_step_prints_a_record_or_traces_or_writes_a_summary():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "GITHUB_STEP_SUMMARY" not in text
    for job_id, step in _every_step():
        run = str(step.get("run", ""))
        assert not re.search(r"\bset\s+-[a-z]*x", run), (job_id, _label(step))
        assert not _PRINTS_A_RECORD.search(run), (job_id, _label(step))


# --------------------------------------------------------------------------
# Linear, the card, --dry-run, checkouts, pins
# --------------------------------------------------------------------------
def test_the_linear_key_is_on_the_filing_scanning_and_posting_steps_only():
    doc = _load()
    keyed = [_file_step(), _scan_step(), _post_step()]
    for step in keyed:
        assert step["env"]["LINEAR_API_KEY"] == LINEAR_KEY, _label(step)
    assert "LINEAR_API_KEY" not in (doc.get("env") or {})
    for job_id, job in doc["jobs"].items():
        assert "secrets.LINEAR" not in json.dumps(job.get("env") or {}), job_id
    for job_id, step in _every_step():
        if step not in keyed:
            assert "secrets.LINEAR" not in json.dumps(step), (job_id, _label(step))
    assert WORKFLOW.read_text(encoding="utf-8").count("secrets.LINEAR") == 3


def test_the_parts_card_reaches_the_scan_and_the_post_as_card():
    for step in (_scan_step(), _post_step()):
        assert step["env"]["CARD"] == CARD_VAR, _label(step)
        assert '--card "$CARD"' in step["run"], _label(step)


def test_dry_run_reaches_the_filer_the_scan_and_the_post():
    for step in (_file_step(), _scan_step(), _post_step()):
        assert step["env"]["DRY_RUN"] == "${{ inputs.dry_run }}", _label(step)
        assert "--dry-run" in step["run"], _label(step)


def test_every_checkout_leaves_no_credential():
    checkouts = [(j, s) for j, s in _every_step()
                 if str(s.get("uses", "")).split("@")[0] == CHECKOUT_ACTION]
    assert checkouts
    for job_id, step in checkouts:
        assert step["with"]["persist-credentials"] is False, (job_id, _label(step))


def test_every_third_party_action_is_pinned_by_a_commit_sha():
    assert check_action_pins.check_file(WORKFLOW) == []
    third_party = [r for r in check_action_pins.references(WORKFLOW)
                   if not str(r).startswith("./")]
    assert third_party, "the checker read no `uses:` at all"


def test_every_context_is_one_github_provides_where_it_is_named():
    assert check_workflow_contexts.check_file(WORKFLOW) == []


# --------------------------------------------------------------------------
# the declared Linear reads (epic DRE-8783)
# --------------------------------------------------------------------------
def _linear_reads() -> list:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    assert lines[0] == LINEAR_READS, f"the file opens {lines[0]!r}"
    reads = []
    for line in lines[1:]:
        if not line.startswith("#   "):
            break
        reads.append(line[4:])
    return reads


READ_LINE = re.compile(r"^(?P<job>[a-z]+) · (?P<step>[^·]+?) · (?P<what>[^—]+?) — (?P<why>.+)$")


def test_the_file_opens_with_the_four_linear_reads_each_with_a_reason():
    reads = [READ_LINE.match(line) for line in _linear_reads()]
    assert all(reads), _linear_reads()
    found = [(m["job"], m["step"], m["what"]) for m in reads]
    assert found == [
        ("sweep", _file_step()["name"], "parts_cleanup.filed"),
        ("sweep", _scan_step()["name"], "parts_cleanup.filed, the replacement-card search"),
        ("sweep", _scan_step()["name"], "spoken_thread.read on the parts card"),
        ("weekly", _post_step()["name"], "spoken_thread.read on the parts card"),
    ]
    whys = [m["why"] for m in reads]
    assert "the read door returns no description" in whys[0]
    assert "the read door returns no description" in whys[1]
    for why in whys[2:]:
        assert "checks the CEO's console signature" in why
        assert "tells the pipeline's own comments apart" in why


# --------------------------------------------------------------------------
# the declared log lines, the watchers
# --------------------------------------------------------------------------
def test_proof_reach_declares_the_three_lines_directly_after_parts_roster():
    tags = [e["tag"] for e in json.loads(PROOF_REACH.read_text(encoding="utf-8"))["log_lines"]]
    at = tags.index("parts-roster:")
    assert tags[at + 1:at + 4] == ["parts-sweep:", "parts-provisional:", "parts-weekly:"]
    entries = {e["tag"]: e for e in json.loads(
        PROOF_REACH.read_text(encoding="utf-8"))["log_lines"]}
    for tag, script, module in (("parts-sweep:", "scripts/parts_sweep.py", parts_sweep),
                                ("parts-provisional:", "scripts/parts_provisional.py",
                                 parts_provisional),
                                ("parts-weekly:", "scripts/parts_weekly.py", parts_weekly)):
        assert entries[tag]["printed_by"] == script
        assert entries[tag]["constant"] == "TAG"
        assert module.TAG == tag
        assert entries[tag]["when"].strip()


def test_the_proof_reach_check_stays_green():
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "proof_reach.py"), "check"],
                          capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_red_main_repair_caller_watches_it_right_after_parts_roster():
    watched = _watch_list(REPAIR_STUB)
    assert watched[watched.index("Parts Roster") + 1] == NAME


def test_the_medic_watches_it_right_after_parts_roster():
    watched = _watch_list(MEDIC_STUB)
    assert watched[watched.index("Parts Roster") + 1] == NAME


def test_the_watcher_check_passes_with_it():
    violations, _ = check_workflow_watchers.check_dir()
    assert violations == []
    assert NAME in {wf.name for wf in check_workflow_watchers.load_workflows()}


# --------------------------------------------------------------------------
# the steps' own scripts, run as written
# --------------------------------------------------------------------------
def _bash(script: str, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    full = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), **env}
    return subprocess.run(["bash", "-e", "-c", script], cwd=cwd, env=full,
                          capture_output=True, text=True, timeout=120)


def _outputs(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        out[key] = value
    return out


def _roster(tmp_path: Path, requested: str = "") -> tuple:
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    step = _step_running("roster", "parts_sweep.py roster")
    assert step["env"] == {"REQUESTED": "${{ inputs.repo }}"}
    done = _bash(step["run"], ROOT, {"REQUESTED": requested, "GITHUB_OUTPUT": str(output)})
    return done, _outputs(output)


def test_the_roster_is_every_key_of_the_repo_map(tmp_path):
    done, out = _roster(tmp_path)
    assert done.returncode == 0, done.stderr
    rows = json.loads(out["repos"])
    repo_map = json.loads((ROOT / "config" / "repo-map.json").read_text(encoding="utf-8"))
    assert [r["repo"] for r in rows] == list(repo_map)
    assert rows == parts_sweep.roster(ROOT)


def test_a_dispatch_naming_a_repo_narrows_the_roster_to_it(tmp_path):
    done, out = _roster(tmp_path, "portico")
    assert done.returncode == 0, done.stderr
    assert [r["repo"] for r in json.loads(out["repos"])] == ["portico"]


def test_a_dispatch_naming_a_repo_the_map_lacks_fails_the_roster(tmp_path):
    done, _ = _roster(tmp_path, "nowhere")
    assert done.returncode == 1
    assert f"{parts_sweep.TAG} nowhere — UNKNOWN" in done.stderr


def test_the_week_ago_is_seven_days_before_the_run(tmp_path):
    done, out = _roster(tmp_path)
    assert done.returncode == 0, done.stderr
    when = datetime.fromisoformat(out["week_ago"])
    assert when.utcoffset() == timedelta(0)
    gap = datetime.now(timezone.utc) - when
    assert timedelta(days=7) - timedelta(minutes=5) < gap < timedelta(days=7, minutes=5)


def _leg(tmp_path: Path, fakes: dict | None = None) -> Path:
    """A workspace with this run's pipeline at `.bureau-pipeline`: the real
    scripts, or a recorder in place of the ones `fakes` names."""
    work = tmp_path / "work"
    scripts = work / PIPELINE_PATH / "scripts"
    scripts.mkdir(parents=True)
    for name in ("parts_sweep.py", "parts_cleanup.py", "parts_provisional.py"):
        if fakes and name in fakes:
            (scripts / name).write_text(fakes[name], encoding="utf-8")
        else:
            (scripts / name).symlink_to(ROOT / "scripts" / name)
    return work


RECORDER = '''\
import json, sys
from pathlib import Path
Path(__file__).with_name("{name}-argv.json").write_text(json.dumps(sys.argv[1:]))
print("{tag} recorded", file=sys.stderr)
'''


def _recorder(name: str, tag: str) -> str:
    return RECORDER.replace("{name}", name).replace("{tag}", tag)


def _argv(work: Path, name: str) -> list:
    return json.loads((work / PIPELINE_PATH / "scripts" / f"{name}-argv.json").read_text())


ROW = {"SLUG": "portico", "OWNER": "dreadnought-foundry", "NAME": "portico"}


@pytest.mark.parametrize("state, code, reason", [
    ("unread", 0, "the read token could not be minted for dreadnought-foundry/portico"),
    ("failed", 1, "the read token could not be minted for dreadnought-foundry/portico, "
                  "inside this organization"),
    ("archived", 0, ARCHIVED_REASON),
])
def test_each_record_step_writes_its_record_and_line(tmp_path, state, code, reason):
    work = _leg(tmp_path)
    done = _bash(_record_step(state)["run"], work, ROW)
    assert done.returncode == code, done.stderr
    record = json.loads((work / "leg" / "count.json").read_text(encoding="utf-8"))
    assert record["state"] == state and record["reason"] == reason
    assert f"{parts_sweep.TAG} portico — {state} — {reason}" in done.stderr


def test_a_repo_with_no_design_system_counts_as_skipped_and_not_counted(tmp_path):
    work = _leg(tmp_path)
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "start"], check=True)
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    done = _bash(_count_step()["run"], work, {
        "SLUG": "portico", "WEEK_AGO": "2026-10-25T10:26:00+00:00",
        "GITHUB_OUTPUT": str(output)})
    assert done.returncode == 0, done.stderr
    assert f"{parts_sweep.TAG} portico — skipped — no design system" in done.stderr
    assert json.loads((work / "leg" / "count.json").read_text())["state"] == "skipped"
    assert _outputs(output)["counted"] == "false"


FAKE_COUNT = '''\
import json, sys
from pathlib import Path
argv = sys.argv[1:]
Path(__file__).with_name("parts_sweep-argv.json").write_text(json.dumps(argv))
Path(argv[argv.index("--json-out") + 1]).write_text(json.dumps({"state": "counted"}))
Path(argv[argv.index("--count-out") + 1]).write_text(json.dumps({"copies": []}))
print("parts-sweep: portico — 0 copy(ies) now, 0 a week ago (no change)", file=sys.stderr)
'''


def test_a_counted_repo_says_counted_and_takes_the_week_ago(tmp_path):
    work = _leg(tmp_path, {"parts_sweep.py": FAKE_COUNT})
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    done = _bash(_count_step()["run"], work, {
        "SLUG": "portico", "WEEK_AGO": "2026-10-25T10:26:00+00:00",
        "GITHUB_OUTPUT": str(output)})
    assert done.returncode == 0, done.stderr
    assert _outputs(output)["counted"] == "true"
    assert _argv(work, "parts_sweep") == [
        "count", "--repo", "portico", "--repo-root", ".",
        "--week-ago", "2026-10-25T10:26:00+00:00",
        "--json-out", "leg/count.json", "--count-out", "leg/parts-count.json"]
    assert "parts-sweep: portico — 0 copy(ies) now" in done.stderr


def test_a_count_that_cannot_answer_fails_its_step(tmp_path):
    work = _leg(tmp_path, {"parts_sweep.py": "import sys\nsys.exit(1)\n"})
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    done = _bash(_count_step()["run"], work, {
        "SLUG": "portico", "WEEK_AGO": "2026-10-25T10:26:00+00:00",
        "GITHUB_OUTPUT": str(output)})
    assert done.returncode == 1


def _file(tmp_path: Path, owned_by: str, dry_run: str) -> list:
    work = _leg(tmp_path, {"parts_cleanup.py": _recorder("parts_cleanup", "parts-cleanup:")})
    done = _bash(_file_step()["run"], work, {
        "SLUG": "agent-bureau", "AGENT": "agent:frontend", "INITIATIVE": "initiative:bureau",
        "OWNED_BY": owned_by, "DRY_RUN": dry_run, "LINEAR_API_KEY": "unused"})
    assert done.returncode == 0, done.stderr
    assert "parts-cleanup: recorded" in done.stderr
    return _argv(work, "parts_cleanup")


FILE_ARGV = ["file", "--repo", "agent-bureau", "--count-json", "leg/parts-count.json",
             "--agent", "agent:frontend", "--initiative", "initiative:bureau"]


def test_the_filer_takes_the_rows_labels_and_owner(tmp_path):
    assert _file(tmp_path, "DRE-8536", "") == FILE_ARGV + ["--owned-by", "DRE-8536"]


def test_the_filer_takes_no_owner_when_the_row_has_none(tmp_path):
    assert _file(tmp_path, "", "false") == FILE_ARGV


def test_the_filer_runs_dry_under_dry_run(tmp_path):
    assert _file(tmp_path, "", "true") == FILE_ARGV + ["--dry-run"]


def _scan(tmp_path: Path, dry_run: str, card: str = "DRE-8946") -> list:
    work = _leg(tmp_path, {"parts_provisional.py": _recorder("parts_provisional",
                                                             "parts-provisional:")})
    done = _bash(_scan_step()["run"], work, {
        "SLUG": "portico", "FULL_NAME": "dreadnought-foundry/portico",
        "QA_LOGIN": "agent-bureau-qa-bot[bot]", "WRITER_LOGIN": "agent-bureau-bot[bot]",
        "CARD": card, "AGENT": "agent:frontend", "INITIATIVE": "initiative:portico",
        "DRY_RUN": dry_run, "GH_TOKEN": "unused", "LINEAR_API_KEY": "unused"})
    assert done.returncode == 0, done.stderr
    assert "parts-provisional: recorded" in done.stderr
    return _argv(work, "parts_provisional")


SCAN_ARGV = ["scan", "--repo", "portico", "--full-name", "dreadnought-foundry/portico",
             "--repo-root", ".", "--qa-login", "agent-bureau-qa-bot[bot]",
             "--writer-login", "agent-bureau-bot[bot]", "--card", "DRE-8946",
             "--agent", "agent:frontend", "--initiative", "initiative:portico",
             "--json-out", "leg/provisional.json"]


def test_the_scan_runs_as_the_contract_says(tmp_path):
    assert _scan(tmp_path, "") == SCAN_ARGV


def test_the_scan_runs_dry_under_dry_run(tmp_path):
    assert _scan(tmp_path, "true") == SCAN_ARGV + ["--dry-run"]


def test_an_empty_parts_card_reaches_the_scan_as_an_empty_card(tmp_path):
    argv = _scan(tmp_path, "false", card="")
    assert argv[argv.index("--card") + 1] == ""


def test_the_downloads_are_laid_out_one_folder_per_repo(tmp_path):
    download = _steps_using("weekly", DOWNLOAD_ACTION)
    assert len(download) == 1
    assert download[0]["with"]["pattern"] == "parts-sweep-*"
    staged = download[0]["with"]["path"]
    lay_out = _step_running("weekly", staged + "/parts-sweep-")
    for slug in ("portico", "agent-bureau"):
        (tmp_path / staged / f"parts-sweep-{slug}").mkdir(parents=True)
        (tmp_path / staged / f"parts-sweep-{slug}" / "count.json").write_text("{}")
    done = _bash(lay_out["run"], tmp_path, {})
    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in (tmp_path / "in").iterdir()) == ["agent-bureau", "portico"]
    assert (tmp_path / "in" / "portico" / "count.json").is_file()
    assert _index("weekly", download[0]) < _index("weekly", lay_out) < _index(
        "weekly", _post_step())


def test_the_lay_out_runs_with_no_downloads(tmp_path):
    staged = _steps_using("weekly", DOWNLOAD_ACTION)[0]["with"]["path"]
    done = _bash(_step_running("weekly", staged + "/parts-sweep-")["run"], tmp_path, {})
    assert done.returncode == 0, done.stderr
    assert list((tmp_path / "in").iterdir()) == []


def test_the_weekly_mint_reads_every_repo_the_installation_covers():
    mints = _steps_using("weekly", MINT_ACTION)
    read = [m for m in mints if m["with"]["app-id"] == "${{ secrets.BUREAU_APP_ID }}"]
    assert len(read) == 1
    with_ = read[0]["with"]
    assert with_["owner"] == "${{ github.repository_owner }}"
    assert "repositories" not in with_
    permissions = {k: v for k, v in with_.items() if k.startswith("permission-")}
    assert permissions == {"permission-pull-requests": "read"}
    assert _post_step()["env"]["GH_TOKEN"] == f"${{{{ steps.{read[0]['id']}.outputs.token }}}}"
    qa = [m for m in mints if m["with"]["app-id"] == "${{ secrets.BUREAU_QA_APP_ID }}"]
    assert len(qa) == 1
    assert _post_step()["env"]["QA_LOGIN"] == f"${{{{ steps.{qa[0]['id']}.outputs.app-slug }}}}[bot]"


def test_the_weekly_runs_this_runs_own_pipeline():
    checkouts = _steps_using("weekly", CHECKOUT_ACTION)
    assert len(checkouts) == 1
    assert set(checkouts[0]["with"]) == {"persist-credentials"}
    assert "python3 scripts/parts_weekly.py post" in _post_step()["run"]


def _post(tmp_path: Path, roster: str, dry_run: str = "", requested: str = "",
          real: bool = False) -> tuple:
    work = tmp_path / "work"
    (work / "scripts").mkdir(parents=True)
    if real:
        for path in (ROOT / "scripts").glob("*.py"):
            (work / "scripts" / path.name).symlink_to(path)
    else:
        (work / "scripts" / "parts_weekly.py").write_text(
            _recorder("parts_weekly", "parts-weekly:"), encoding="utf-8")
    step = _post_step()
    assert step["env"]["ROSTER"] == "${{ needs.roster.outputs.repos }}"
    assert step["env"]["REQUESTED"] == "${{ inputs.repo }}"
    done = _bash(step["run"], work, {
        "ROSTER": roster, "DRY_RUN": dry_run, "REQUESTED": requested, "CARD": "DRE-8946",
        "QA_LOGIN": "agent-bureau-qa-bot[bot]", "GH_TOKEN": "unused",
        "LINEAR_API_KEY": "unused", "LINEAR_IDENTITY": "fleet"})
    argv = None if real else json.loads((work / "scripts" / "parts_weekly-argv.json").read_text())
    return done, argv


ROSTER = json.dumps([{"repo": "portico"}, {"repo": "agent-bureau"}])
POST_ARGV = ["post", "--in", "in", "--repos", '["portico", "agent-bureau"]', "--card",
             "DRE-8946", "--qa-login", "agent-bureau-qa-bot[bot]"]


def test_the_post_takes_the_rosters_slugs(tmp_path):
    done, argv = _post(tmp_path, ROSTER)
    assert done.returncode == 0, done.stderr
    assert argv == POST_ARGV


@pytest.mark.parametrize("dry_run, requested", [("true", ""), ("true", "portico"),
                                                 ("false", "portico"), ("", "portico")])
def test_the_post_runs_dry_under_dry_run_or_a_named_repo(tmp_path, dry_run, requested):
    _, argv = _post(tmp_path, ROSTER, dry_run=dry_run, requested=requested)
    assert argv == POST_ARGV + ["--dry-run"]


@pytest.mark.parametrize("dry_run", ["", "false"])
def test_a_whole_roster_post_is_not_dry(tmp_path, dry_run):
    _, argv = _post(tmp_path, ROSTER, dry_run=dry_run)
    assert "--dry-run" not in argv


@pytest.mark.parametrize("roster", ["[]", ""])
def test_an_empty_roster_reaches_the_post_as_an_empty_list(tmp_path, roster):
    _, argv = _post(tmp_path, roster)
    assert argv[argv.index("--repos") + 1] == "[]"


def test_the_real_post_refuses_an_empty_roster(tmp_path):
    done, _ = _post(tmp_path, "[]", real=True)
    assert done.returncode == 1
    assert re.search(r"parts-weekly: not posted \d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2} — "
                     r"the roster named no repos", done.stderr), done.stderr
