"""RED-first: the Operator Agent's executor for a `do` card (DRE-8924, epic DRE-8738).

`scripts/operator_do.py` carries out the step a `do` card names, or in a dry
run prints it. Five commands:

    read <CARD> --out <dir>                    the card file the model reads
    check <CARD> --plan --card --out           operator_do_ops.check over the plan
    run <CARD> --plan --out [--dry-run]        each op, one credential, no shell
    probe                                      the credentials' grants, read only
    linear project-status "<project>" <status> the linear-admin op's one command

Nothing here reaches Linear or GitHub: `subprocess.run`, `linear_ops.gql`,
`linear_ops.comment_records` and the App's REST opener are stubs each test
builds, and one test puts a stub `gh` first on PATH.

Run: cd bureau-pipeline && python3 -m pytest tests/test_operator_do.py -v
"""
from __future__ import annotations

import io
import json
import os
import re
import stat
import subprocess
import sys
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import linear_ops  # noqa: E402
import operator_agent  # noqa: E402
import operator_decide  # noqa: E402
import operator_do  # noqa: E402
import operator_do_ops  # noqa: E402

MODULE_PATH = ROOT / "scripts" / "operator_do.py"
FIXTURE = ROOT / "tests" / "fixtures" / "operator-do-cards.json"
REPO_MAP = json.loads((ROOT / "config" / "repo-map.json").read_text(encoding="utf-8"))

TAG = operator_decide.LOG_TAG
CARD = "DRE-9001"
REPO = "dreadnought-foundry/agent-bureau"
SHA = "0123456789abcdef0123456789abcdef01234567"
PR_URL = "https://github.com/dreadnought-foundry/agent-bureau/pull/4242"
GH_SECRET = "ghs_thisIsTheTestsOwnInstallationToken0001"
LINEAR_SECRET = "lin_api_thisIsTheTestsOwnLinearKey0001"


@pytest.fixture(autouse=True)
def _sealed(monkeypatch):
    """Whatever the runner's own tokens are, no test reads them, and no test
    reaches Linear unless it stubs the read it means."""
    for name in ("GH_TOKEN", "LINEAR_API_KEY", "BUREAU_APP_ID", "BUREAU_APP_PRIVATE_KEY"):
        monkeypatch.delenv(name, raising=False)

    def no_linear(*args, **kwargs):
        raise AssertionError("a test reached Linear without stubbing the read")
    monkeypatch.setattr(linear_ops, "gql", no_linear)
    monkeypatch.setattr(linear_ops, "comment_records", no_linear)


def _plan(kind, *ops, card=CARD):
    return {"card": card, "kind": kind, "step": "one step.", "reason": "one reason.",
            "ops": list(ops), "continues": None}


VARIABLE_PLAN = _plan(
    "variable",
    {"op": "variable-set", "repo": REPO, "name": "ALARM_REPAIR_LIVE", "value": "true"},
    {"op": "variable-list", "repo": REPO},
    {"op": "variable-delete", "repo": REPO, "name": "OLD_FLAG"},
)
LINEAR_PLAN = _plan(
    "linear-admin",
    {"op": "project-status", "project": "onboard-sandbox: Onboard Sandbox", "status": "Completed"},
)
BOT_PR_PLAN = _plan(
    "bot-pr",
    {"op": "branch-create", "repo": REPO, "branch": f"agent/{CARD}-canary", "from": SHA},
    {"op": "file-put", "repo": REPO, "branch": f"agent/{CARD}-canary", "path": "notes/canary.md",
     "content": "a canary\n", "message": "add the canary"},
    {"op": "pr-open", "repo": REPO, "branch": f"agent/{CARD}-canary", "title": "the canary",
     "body": "one canary pull request", "draft": False},
)


def _write(path: Path, data) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _tag_lines(text: str) -> list:
    return [line for line in text.splitlines() if line.startswith(TAG)]


class Recorder:
    """A `subprocess.run` stand-in: records each call and answers from a list."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = list(answers or [])

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": argv, **kwargs})
        code, out, err = self.answers.pop(0) if self.answers else (0, "", "")
        return subprocess.CompletedProcess(argv, code, out, err)


def _run(tmp_path, plan, *flags, card=CARD):
    plan_path = _write(tmp_path / f"{card}.plan.json", plan)
    out = tmp_path / "transcript.json"
    code = operator_do.main(["run", card, "--plan", str(plan_path), "--out", str(out), *flags])
    return code, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else None)


# --------------------------------------------------------------------------- #
# run --dry-run                                                                #
# --------------------------------------------------------------------------- #


def test_a_dry_run_prints_one_would_run_line_per_op_and_runs_none(tmp_path, monkeypatch,
                                                                  capsys):
    def never(*args, **kwargs):
        raise AssertionError("a dry run called subprocess.run")
    monkeypatch.setattr(subprocess, "run", never)
    monkeypatch.setattr(operator_do.subprocess, "run", never)
    code, transcript = _run(tmp_path, VARIABLE_PLAN, "--dry-run")
    assert code == 0
    lines = _tag_lines(capsys.readouterr().out)
    expected = [operator_do_ops.command_line(op) for op in VARIABLE_PLAN["ops"]]
    assert lines == [f"{TAG} {CARD} would run: {line}" for line in expected]
    assert transcript["dry_run"] is True
    assert transcript["failed"] is False
    assert [c["command"] for c in transcript["commands"]] == expected
    assert all("exit" not in c for c in transcript["commands"])
    assert [c["writes"] for c in transcript["commands"]] == [True, False, True]


# --------------------------------------------------------------------------- #
# run, live                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("plan, credential, other", [
    (VARIABLE_PLAN, "GH_TOKEN", "LINEAR_API_KEY"),
    (LINEAR_PLAN, "LINEAR_API_KEY", "GH_TOKEN"),
])
def test_a_live_run_gives_each_command_an_argv_no_shell_and_one_credential(
        tmp_path, monkeypatch, plan, credential, other):
    monkeypatch.setenv("GH_TOKEN", GH_SECRET)
    monkeypatch.setenv("LINEAR_API_KEY", LINEAR_SECRET)
    monkeypatch.setenv("AWS_PROFILE", "dreadnought")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLEEXAMPLE00")
    monkeypatch.setenv("BUREAU_APP_PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----")
    recorder = Recorder()
    monkeypatch.setattr(subprocess, "run", recorder)
    code, transcript = _run(tmp_path, plan)
    assert code == 0
    assert len(recorder.calls) == len(plan["ops"])
    for call, op in zip(recorder.calls, plan["ops"]):
        assert isinstance(call["argv"], list)
        assert call["argv"] == operator_do_ops.render(op)
        assert call.get("shell", False) is False
        env = call["env"]
        assert set(env) == {"PATH", "HOME", credential}
        assert env[credential] == os.environ[credential]
        assert env["PATH"] == os.environ["PATH"]
        for absent in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", other, "BUREAU_APP_PRIVATE_KEY"):
            assert absent not in env
    assert transcript["dry_run"] is False
    assert transcript["failed"] is False


def test_each_command_prints_its_ran_line_and_records_writes_exit_and_output(
        tmp_path, monkeypatch, capsys):
    recorder = Recorder([(0, "set it\n", ""), (0, "ALARM_REPAIR_LIVE true\n", ""),
                         (0, "", "deleted\n")])
    monkeypatch.setattr(subprocess, "run", recorder)
    code, transcript = _run(tmp_path, VARIABLE_PLAN)
    assert code == 0
    lines = [operator_do_ops.command_line(op) for op in VARIABLE_PLAN["ops"]]
    assert _tag_lines(capsys.readouterr().out) == [
        f"{TAG} {CARD} ran: {line} — exit 0" for line in lines]
    assert transcript["card"] == CARD
    assert transcript["kind"] == "variable"
    assert [(c["command"], c["writes"], c["exit"]) for c in transcript["commands"]] == [
        (lines[0], True, 0), (lines[1], False, 0), (lines[2], True, 0)]
    assert "set it" in transcript["commands"][0]["output"]
    assert "deleted" in transcript["commands"][2]["output"]
    assert transcript["pull_request"] is None


def test_a_command_that_exits_non_zero_stops_the_run(tmp_path, monkeypatch, capsys):
    recorder = Recorder([(0, "set it", ""), (1, "", "HTTP 403: Resource not accessible")])
    monkeypatch.setattr(subprocess, "run", recorder)
    code, transcript = _run(tmp_path, VARIABLE_PLAN)
    assert code == 0
    assert len(recorder.calls) == 2  # the third op never ran
    commands = transcript["commands"]
    assert len(commands) == 2
    assert transcript["failed"] is True
    assert commands[0]["writes"] is True and commands[0]["exit"] == 0
    assert commands[1]["exit"] == 1
    assert commands[1]["writes"] is False
    assert "HTTP 403: Resource not accessible" in commands[1]["output"]
    assert _tag_lines(capsys.readouterr().out)[-1].endswith(" — exit 1")


def test_a_failed_transcript_is_one_the_receipt_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", Recorder([(0, "set it", ""), (2, "", "refused")]))
    _code, transcript = _run(tmp_path, VARIABLE_PLAN)
    decision = operator_decide.Decision(
        card=CARD, repo=REPO, lane="Hand-work", outcome="do", reason="the model read the card",
        step="set the variable", act="", deploy=False, evidence=[], host_cannot=[], finding="",
        question="", recommendation="", choices=None, held="", fingerprint="0123456789ab")
    text = operator_decide.do_receipt_text(decision, transcript, {"ending": "failed"},
                                           datetime(2026, 11, 3, 17, 0, tzinfo=UTC))
    assert f"left changed: `{transcript['commands'][0]['command']}`" in text
    assert f"failed: `{transcript['commands'][1]['command']}` — exit 2" in text


def test_a_command_that_cannot_start_is_a_failed_command_not_a_crash(tmp_path, monkeypatch):
    def missing(argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", argv[0])
    monkeypatch.setattr(subprocess, "run", missing)
    code, transcript = _run(tmp_path, VARIABLE_PLAN)
    assert code == 0
    assert transcript["failed"] is True
    assert len(transcript["commands"]) == 1
    assert transcript["commands"][0]["exit"] != 0
    assert "No such file" in transcript["commands"][0]["output"]


def test_run_exits_2_only_when_it_could_not_write_a_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", Recorder())
    plan_path = _write(tmp_path / f"{CARD}.plan.json", VARIABLE_PLAN)
    out = tmp_path / "no-such-dir" / "transcript.json"
    assert operator_do.main(["run", CARD, "--plan", str(plan_path), "--out", str(out)]) == 2


def test_a_plan_that_will_not_render_runs_nothing_and_writes_a_failed_transcript(
        tmp_path, monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("an unrenderable plan ran a command")
    monkeypatch.setattr(subprocess, "run", never)
    plan = _plan("variable", {"op": "variable-list", "repo": REPO}, {"op": "workflow-run"})
    code, transcript = _run(tmp_path, plan)
    assert code == 0
    assert transcript["failed"] is True
    assert [(c["command"], c["exit"]) for c in transcript["commands"]] == [("run", 1)]


def test_a_plan_for_another_card_runs_nothing(tmp_path, monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("another card's plan ran a command")
    monkeypatch.setattr(subprocess, "run", never)
    code, transcript = _run(tmp_path, _plan("variable", {"op": "variable-list", "repo": REPO},
                                            card="DRE-9002"))
    assert code == 0
    assert transcript["failed"] is True
    assert transcript["card"] == CARD


# --------------------------------------------------------------------------- #
# the stubbed gh: pull_request, and no token in the transcript                 #
# --------------------------------------------------------------------------- #

_STUB_GH = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["HOME"] + "/gh-calls.jsonl", "a") as fh:
    fh.write(json.dumps(args) + "\\n")
if "repos/dreadnought-foundry/agent-bureau/pulls" in args:
    print(json.dumps({"number": 4242, "html_url": "%s", "state": "open",
                      "body": "x" * 4000}))
elif args[:2] == ["variable", "list"]:
    print("the token I hold is " + os.environ.get("GH_TOKEN", ""))
else:
    print(json.dumps({"ok": True}))
''' % PR_URL


def _stub_gh(tmp_path, monkeypatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(_STUB_GH, encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(home))
    return home


def test_a_bot_pr_plans_pr_open_names_the_pull_request(tmp_path, monkeypatch):
    home = _stub_gh(tmp_path, monkeypatch)
    monkeypatch.setenv("GH_TOKEN", GH_SECRET)
    code, transcript = _run(tmp_path, BOT_PR_PLAN)
    assert code == 0
    assert transcript["failed"] is False
    assert transcript["pull_request"] == PR_URL
    calls = [json.loads(line) for line in (home / "gh-calls.jsonl").read_text().splitlines()]
    assert calls == [operator_do_ops.render(op)[1:] for op in BOT_PR_PLAN["ops"]]
    # The output is cut, and the pull request is named all the same.
    output = transcript["commands"][2]["output"]
    assert re.search(r"\n… cut, \d+ characters$", output)


def test_the_transcript_holds_no_token_when_a_command_echoes_it(tmp_path, monkeypatch):
    _stub_gh(tmp_path, monkeypatch)
    monkeypatch.setenv("GH_TOKEN", GH_SECRET)
    plan = _plan("variable", {"op": "variable-list", "repo": REPO})
    out = tmp_path / "transcript.json"
    _code, transcript = _run(tmp_path, plan)
    assert transcript["commands"][0]["output"].strip() == "the token I hold is [token]"
    assert GH_SECRET not in out.read_text(encoding="utf-8")


def test_an_output_past_1500_characters_is_cut_and_says_how_much():
    text = "a" * 1500 + "b" * 700
    assert operator_do.clean(text) == "a" * 1500 + "\n… cut, 700 characters"
    assert operator_do.clean("short") == "short"


def test_clean_reads_either_credential_as_token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", GH_SECRET)
    monkeypatch.setenv("LINEAR_API_KEY", LINEAR_SECRET)
    said = operator_do.clean(f"gh {GH_SECRET} linear {LINEAR_SECRET}")
    assert said == "gh [token] linear [token]"


# --------------------------------------------------------------------------- #
# check                                                                        #
# --------------------------------------------------------------------------- #


def _fixture_row(card: str) -> dict:
    return next(r for r in json.loads(FIXTURE.read_text(encoding="utf-8")) if r["card"] == card)


def _card_md(tmp_path, card: str, comments=(), description=None) -> Path:
    """The card file `read` writes, through part 1's own writer."""
    row = _fixture_row(card)
    labels = list(row["labels"])
    slug = next(l.split(":", 1)[1] for l in labels if l.startswith("repo:"))
    if description is None:
        description = "\n\n".join(e["text"] for e in row["excerpts"]
                                  if e["from"] == "description")
    file_row = {"card": card, "title": row["title"], "lane": row["lane"], "labels": labels,
                "repo": REPO_MAP[slug], "description": description, "action": "decide"}
    records = [{"body": body, "authored_by_pipeline": False, "created_at": None}
               for body in comments]
    path = tmp_path / f"{card}.md"
    path.write_text(operator_agent.card_file(file_row, records), encoding="utf-8")
    return path


def _check_cli(tmp_path, card, plan, card_md):
    plan_path = _write(tmp_path / f"{card}.plan.json", plan)
    out = tmp_path / f"{card}.check.json"
    code = operator_do.main(["check", card, "--plan", str(plan_path), "--card", str(card_md),
                             "--out", str(out)])
    return code, json.loads(out.read_text(encoding="utf-8"))


ALARM_PLAN = _plan(
    "variable",
    {"op": "variable-set", "repo": "dreadnought-foundry/bureau-pipeline",
     "name": "ALARM_REPAIR_LIVE", "value": "true"},
    {"op": "variable-list", "repo": "dreadnought-foundry/bureau-pipeline"},
    card="DRE-8637",
)


def test_check_writes_the_plans_one_repository_and_the_claims_command_lines(tmp_path,
                                                                            capsys):
    code, checked = _check_cli(tmp_path, "DRE-8637", ALARM_PLAN, _card_md(tmp_path, "DRE-8637"))
    assert code == 0
    assert checked == {
        "ok": True, "kind": "variable", "repo": "dreadnought-foundry/bureau-pipeline",
        "command_lines": [operator_do_ops.command_line(op) for op in ALARM_PLAN["ops"]]}
    assert not _tag_lines(capsys.readouterr().out)
    assert not (tmp_path / "transcript.json").exists()


def test_check_gives_a_linear_admin_plan_no_repository(tmp_path):
    plan = _plan("linear-admin", {"op": "project-status",
                                  "project": "onboard-sandbox: Onboard Sandbox",
                                  "status": "Completed"}, card="DRE-7579")
    code, checked = _check_cli(tmp_path, "DRE-7579", plan, _card_md(tmp_path, "DRE-7579"))
    assert code == 0
    assert checked["ok"] is True and checked["repo"] is None
    assert checked["kind"] == "linear-admin"


def test_a_refused_plan_runs_nothing_and_writes_the_refusal(tmp_path, monkeypatch, capsys):
    def never(*args, **kwargs):
        raise AssertionError("check ran a command")
    monkeypatch.setattr(subprocess, "run", never)
    plan = _plan("variable", {"op": "variable-set", "repo": "dreadnought-foundry/bureau-pipeline",
                              "name": "INVENTED_FLAG", "value": "true"}, card="DRE-8637")
    code, checked = _check_cli(tmp_path, "DRE-8637", plan, _card_md(tmp_path, "DRE-8637"))
    assert code == 1
    assert checked["ok"] is False
    assert checked["kind"] == "variable"
    assert checked["repo"] == "dreadnought-foundry/bureau-pipeline"
    assert checked["command_lines"] == []
    lines = _tag_lines(capsys.readouterr().out)
    assert lines == [f"{TAG} DRE-8637 do refused — op 1 variable-set · "
                     "`INVENTED_FLAG` is not quoted in the card's text"]
    transcript = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    assert transcript["failed"] is True
    assert transcript["dry_run"] is False
    assert transcript["pull_request"] is None
    assert len(transcript["commands"]) == 1
    entry = transcript["commands"][0]
    assert (entry["command"], entry["writes"], entry["exit"]) == ("check", False, 1)
    assert "INVENTED_FLAG" in entry["output"]


def test_check_reads_the_cards_title_and_description_and_never_its_comments(tmp_path):
    """A name a comment quotes is not one the card quotes: anyone can comment."""
    plan = _plan("variable", {"op": "variable-set", "repo": "dreadnought-foundry/bureau-pipeline",
                              "name": "COMMENTED_FLAG", "value": "true"}, card="DRE-8637")
    card_md = _card_md(tmp_path, "DRE-8637", comments=["please also set COMMENTED_FLAG"])
    code, checked = _check_cli(tmp_path, "DRE-8637", plan, card_md)
    assert code == 1 and checked["ok"] is False


def test_check_reads_the_repo_label_as_the_card_file_lists_it(tmp_path):
    """The repository the label names passes unquoted; another fleet repository
    the text does not quote is refused."""
    other = _plan("variable", {"op": "variable-list", "repo": "dreadnought-foundry/portico"},
                  card="DRE-8637")
    code, checked = _check_cli(tmp_path, "DRE-8637", other, _card_md(tmp_path, "DRE-8637"))
    assert code == 1 and checked["ok"] is False


def test_check_refuses_a_plan_written_for_another_card(tmp_path, capsys):
    code, checked = _check_cli(tmp_path, "DRE-8623", ALARM_PLAN, _card_md(tmp_path, "DRE-8637"))
    assert code == 1 and checked["ok"] is False
    assert any("do refused — plan ·" in line for line in _tag_lines(capsys.readouterr().out))


def test_check_refuses_a_plan_that_is_not_json(tmp_path):
    plan_path = tmp_path / "DRE-8637.plan.json"
    plan_path.write_text("not json", encoding="utf-8")
    out = tmp_path / "DRE-8637.check.json"
    code = operator_do.main(["check", "DRE-8637", "--plan", str(plan_path),
                             "--card", str(_card_md(tmp_path, "DRE-8637")), "--out", str(out)])
    assert code == 1
    assert json.loads(out.read_text(encoding="utf-8"))["ok"] is False


# --------------------------------------------------------------------------- #
# read                                                                         #
# --------------------------------------------------------------------------- #


ISSUE = {"identifier": CARD, "title": "agent-bureau: set the variable OLD_FLAG",
         "description": "Delete `OLD_FLAG` from dreadnought-foundry/agent-bureau.",
         "state": {"name": "Hand-work"},
         "labels": {"nodes": [{"name": "repo:agent-bureau"}, {"name": "operator-step"}]}}


def _linear_issue(monkeypatch, records, issue=ISSUE):
    asked = []

    def gql(query, variables=None, **kwargs):
        asked.append(query)
        assert "mutation" not in query
        return {"issue": issue}

    def comment_records(ident, *, whole_thread=False):
        asked.append(("thread", ident, whole_thread))
        return records
    monkeypatch.setattr(linear_ops, "gql", gql)
    monkeypatch.setattr(linear_ops, "comment_records", comment_records)
    return asked


def test_read_writes_the_card_file_with_part_1s_writer(tmp_path, monkeypatch):
    records = [{"body": f"note {n}", "authored_by_pipeline": False, "created_at": None}
               for n in range(30)]
    asked = _linear_issue(monkeypatch, records)
    out = tmp_path / "cards"
    assert operator_do.main(["read", CARD, "--out", str(out)]) == 0
    text = (out / f"{CARD}.md").read_text(encoding="utf-8")
    assert text.startswith(operator_agent.BEGIN)
    assert text.rstrip().endswith(operator_agent.END)
    assert f"Title: {ISSUE['title']}" in text
    assert "Labels: repo:agent-bureau, operator-step" in text
    assert f"Repo: {REPO}" in text
    assert "note 29" in text and "note 10" in text and "note 9\n" not in text
    assert asked[-1] == ("thread", CARD, True)
    assert len([a for a in asked if isinstance(a, str)]) == 1


def test_read_keeps_a_continue_cards_newest_do_receipt_whole(tmp_path, monkeypatch):
    row = {"title": ISSUE["title"], "description": ISSUE["description"]}
    decision = operator_decide.Decision(
        card=CARD, repo=REPO, lane="Hand-work", outcome="do", reason="the model read the card",
        step="delete the variable", act="", deploy=False, evidence=[], host_cannot=[],
        finding="", question="", recommendation="", choices=None, held="",
        fingerprint=operator_decide.fingerprint(row))
    transcript = {"card": CARD, "kind": "variable", "dry_run": False, "pull_request": None,
                  "failed": False, "commands": [{"command": "gh variable list -R x",
                                                 "writes": False, "exit": 0,
                                                 "output": "THE-RECEIPT-OUTPUT"}]}
    ending = {"ending": "continues", "after": datetime(2026, 1, 1, tzinfo=UTC),
              "remains": "delete the old variable"}
    receipt = operator_decide.do_receipt_text(decision, transcript, ending,
                                              datetime(2025, 12, 31, tzinfo=UTC))
    records = [{"body": receipt, "authored_by_pipeline": True,
                "created_at": "2025-12-31T00:00:00.000Z"}]
    records += [{"body": f"a person's note {n}", "authored_by_pipeline": False,
                 "created_at": "2026-01-02T00:00:00.000Z"} for n in range(25)]
    _linear_issue(monkeypatch, records)
    out = tmp_path / "cards"
    assert operator_do.main(["read", CARD, "--out", str(out)]) == 0
    text = (out / f"{CARD}.md").read_text(encoding="utf-8")
    assert "THE-RECEIPT-OUTPUT" in text
    assert "a person's note 24" in text


def test_read_refuses_a_directory_inside_this_repository(monkeypatch):
    _linear_issue(monkeypatch, [])
    assert operator_do.main(["read", CARD, "--out", str(ROOT / "tmp-cards")]) == 3
    assert not (ROOT / "tmp-cards").exists()


# --------------------------------------------------------------------------- #
# linear project-status                                                        #
# --------------------------------------------------------------------------- #


STATUSES = {"nodes": [{"id": "st-started", "name": "In Progress", "type": "started"},
                      {"id": "st-done", "name": "Completed", "type": "completed"},
                      {"id": "st-canceled", "name": "Canceled", "type": "canceled"}]}
PROJECT = {"id": "p-1", "name": "onboard-sandbox: Onboard Sandbox",
           "status": {"name": "In Progress"}}


class StubLinear:
    """`linear_ops.gql` answering the name lookup, the update and the read-back."""

    def __init__(self, projects, refuse=None):
        self.projects = projects
        self.refuse = refuse
        self.calls = []
        self.status = {p["id"]: p["status"]["name"] for p in projects}

    def __call__(self, query, variables=None, **kwargs):
        variables = variables or {}
        self.calls.append((query, variables))
        if query.lstrip().startswith("mutation"):
            assert "projectUpdate" in query
            if self.refuse:
                raise linear_ops.LinearError(self.refuse)
            name = next(s["name"] for s in STATUSES["nodes"] if s["id"] == variables["statusId"])
            self.status[variables["id"]] = name
            return {"projectUpdate": {"success": True}}
        if "projects(" in query:
            return {"projects": {"nodes": self.projects}, "projectStatuses": STATUSES}
        if "project(" in query:
            pid = variables["id"]
            return {"project": {"id": pid, "name": "x", "status": {"name": self.status[pid]}}}
        raise AssertionError(f"an unexpected Linear request: {query}")

    def mutations(self):
        return [c for c in self.calls if c[0].lstrip().startswith("mutation")]

    def reads(self):
        return [c for c in self.calls if not c[0].lstrip().startswith("mutation")]


def _project_status(monkeypatch, stub, name=PROJECT["name"], status="Completed"):
    monkeypatch.setenv("LINEAR_API_KEY", LINEAR_SECRET)
    monkeypatch.setattr(linear_ops, "gql", stub)
    return operator_do.main(["linear", "project-status", name, status])


def test_project_status_sets_the_named_project_and_prints_what_it_read_back(monkeypatch,
                                                                           capsys):
    stub = StubLinear([dict(PROJECT)])
    assert _project_status(monkeypatch, stub) == 0
    (query, variables), = stub.mutations()
    assert variables == {"id": "p-1", "statusId": "st-done"}
    assert len(stub.reads()) == 2  # the name lookup and the read-back
    assert stub.reads()[0][1] == {"name": PROJECT["name"]}
    out = capsys.readouterr().out
    assert "Completed" in out.splitlines()[-1]
    assert PROJECT["name"] in out


def test_project_status_refuses_a_name_that_matches_none(monkeypatch, capsys):
    near = dict(PROJECT, id="p-2", name=PROJECT["name"].upper())
    stub = StubLinear([near])
    assert _project_status(monkeypatch, stub) == 1
    assert stub.mutations() == []
    assert "no project" in capsys.readouterr().out


def test_project_status_refuses_a_name_that_matches_two(monkeypatch, capsys):
    stub = StubLinear([dict(PROJECT), dict(PROJECT, id="p-2")])
    assert _project_status(monkeypatch, stub) == 1
    assert stub.mutations() == []
    assert "2 projects" in capsys.readouterr().out


def test_project_status_exits_1_with_linears_message_when_the_update_is_refused(monkeypatch,
                                                                               capsys):
    message = "Linear API returned 400: 'You do not have permission to update this project'"
    stub = StubLinear([dict(PROJECT)], refuse=message)
    assert _project_status(monkeypatch, stub) == 1
    assert message in capsys.readouterr().out


def test_project_status_refuses_a_status_off_the_ops_choices(monkeypatch):
    stub = StubLinear([dict(PROJECT)])
    assert _project_status(monkeypatch, stub, status="In Progress") == 1
    assert stub.calls == []


def test_project_status_without_a_linear_key_sends_nothing(monkeypatch, capsys):
    stub = StubLinear([dict(PROJECT)])
    monkeypatch.setattr(linear_ops, "gql", stub)
    assert operator_do.main(["linear", "project-status", PROJECT["name"], "Completed"]) == 1
    assert stub.calls == []


# --------------------------------------------------------------------------- #
# probe                                                                        #
# --------------------------------------------------------------------------- #


FLEET = list(dict.fromkeys(v for v in REPO_MAP.values() if v.startswith("dreadnought-foundry/")))
INSTALLATION = {"id": 4242, "app_slug": "agent-bureau-bot",
                "permissions": {"actions_variables": "write", "contents": "write",
                                "pull_requests": "write", "metadata": "read"}}


class StubGitHub:
    """The App client's opener: one answer per repository's installation read."""

    def __init__(self, answers):
        self.answers = answers
        self.requests = []

    def __call__(self, req):
        self.requests.append((req.get_method(), req.full_url, req.get_header("Authorization")))
        repo = re.search(r"/repos/([^/]+/[^/]+)/installation$", req.full_url).group(1)
        answer = self.answers(repo)
        if isinstance(answer, int):
            raise urllib.error.HTTPError(req.full_url, answer, "refused", {},
                                         io.BytesIO(b'{"message": "Not Found"}'))
        return 200, json.dumps(answer).encode(), {}


def _probe(monkeypatch, answers, viewer=None, viewer_error=None):
    monkeypatch.setenv("BUREAU_APP_ID", "3350400")
    monkeypatch.setenv("BUREAU_APP_PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\nx\n")
    monkeypatch.setenv("LINEAR_API_KEY", LINEAR_SECRET)
    monkeypatch.setattr(operator_do.app_token, "app_jwt", lambda *a, **k: "the-app-jwt")
    github = StubGitHub(answers)
    monkeypatch.setattr(operator_do, "_github_opener", github)
    asked = []

    def gql(query, variables=None, **kwargs):
        asked.append(query)
        if viewer_error:
            raise linear_ops.LinearError(viewer_error)
        return {"viewer": viewer or {"id": "u-1", "admin": False, "guest": False}}
    monkeypatch.setattr(linear_ops, "gql", gql)
    code = operator_do.main(["probe"])
    return code, github, asked


def _probe_lines(capsys):
    return [line for line in _tag_lines(capsys.readouterr().out) if " credential " in line]


def test_probe_prints_one_line_per_fleet_repository_and_one_for_linear(monkeypatch, capsys):
    code, github, asked = _probe(monkeypatch, lambda repo: INSTALLATION)
    assert code == 0
    lines = _probe_lines(capsys)
    assert lines == [f"{TAG} credential {repo} — actions_variables=write pull_requests=write "
                     "contents=write" for repo in FLEET] + [
        f"{TAG} credential linear — admin=false guest=false"]
    assert not any("EveryBite" in line or "DeltaSolv" in line for line in lines)
    assert [r[1] for r in github.requests] == [
        f"https://api.github.com/repos/{repo}/installation" for repo in FLEET]
    assert {r[2] for r in github.requests} == {"Bearer the-app-jwt"}
    assert len(asked) == 1 and "viewer" in asked[0] and "mutation" not in asked[0]


def test_probe_makes_no_write_request(monkeypatch):
    _code, github, asked = _probe(monkeypatch, lambda repo: INSTALLATION)
    assert {method for method, _url, _auth in github.requests} == {"GET"}
    assert all(not q.lstrip().startswith("mutation") for q in asked)


def test_probe_reads_none_for_a_permission_the_installation_lacks(monkeypatch, capsys):
    lacking = {**INSTALLATION, "permissions": {k: v for k, v in INSTALLATION["permissions"].items()
                                               if k != "actions_variables"}}
    code, _github, _asked = _probe(monkeypatch, lambda repo: lacking)
    assert code == 0
    assert _probe_lines(capsys)[0] == (f"{TAG} credential {FLEET[0]} — actions_variables=none "
                                       "pull_requests=write contents=write")


def test_probe_never_reads_a_variables_key_as_actions_variables(monkeypatch, capsys):
    """`variables` is not GitHub's key for the repository-variables grant."""
    only = {**INSTALLATION, "permissions": {"variables": "write"}}
    code, _github, _asked = _probe(monkeypatch, lambda repo: only)
    assert code == 0
    assert _probe_lines(capsys)[0] == (f"{TAG} credential {FLEET[0]} — actions_variables=none "
                                       "pull_requests=none contents=none")


def test_probe_reads_unread_with_the_status_for_a_refused_read(monkeypatch, capsys):
    refused = FLEET[1]
    code, _github, _asked = _probe(
        monkeypatch, lambda repo: 404 if repo == refused else INSTALLATION)
    assert code == 0
    lines = _probe_lines(capsys)
    assert f"{TAG} credential {refused} — unread 404" in lines
    assert len(lines) == len(FLEET) + 1


def test_probe_reads_unread_for_a_refused_linear_read_and_still_exits_0(monkeypatch, capsys):
    code, _github, _asked = _probe(monkeypatch, lambda repo: INSTALLATION,
                                   viewer_error="Linear API returned 401: AUTHENTICATION_ERROR")
    assert code == 0
    last = _probe_lines(capsys)[-1]
    assert last.startswith(f"{TAG} credential linear — unread ")
    assert "401" in last


def test_probe_prints_the_linear_keys_admin_and_guest(monkeypatch, capsys):
    _probe(monkeypatch, lambda repo: INSTALLATION,
           viewer={"id": "u-1", "admin": True, "guest": False})
    assert _probe_lines(capsys)[-1] == f"{TAG} credential linear — admin=true guest=false"


def test_probe_never_prints_a_token(monkeypatch, capsys):
    _probe(monkeypatch, lambda repo: INSTALLATION)
    out = capsys.readouterr()
    for secret in ("the-app-jwt", LINEAR_SECRET, "BEGIN RSA"):
        assert secret not in out.out and secret not in out.err


def test_probe_without_the_app_key_reads_unread_and_exits_0(monkeypatch, capsys):
    monkeypatch.setenv("LINEAR_API_KEY", LINEAR_SECRET)
    monkeypatch.setattr(linear_ops, "gql", lambda q, v=None, **k: {
        "viewer": {"id": "u", "admin": False, "guest": True}})
    assert operator_do.main(["probe"]) == 0
    lines = _probe_lines(capsys)
    assert len(lines) == len(FLEET) + 1
    assert all(" — unread " in line for line in lines[:-1])
    assert lines[-1] == f"{TAG} credential linear — admin=false guest=true"


# --------------------------------------------------------------------------- #
# the module                                                                   #
# --------------------------------------------------------------------------- #


def test_the_docstring_names_every_linear_read_and_its_bound():
    doc = operator_do.__doc__
    assert "## The reads, named" in doc
    for said in ("one card read", "one whole-thread read", "max_do_per_pass",
                 "two reads per `project-status` op", "one read of the key's user",
                 "linear-calls:", "linear-budget:", "DRE-8789", "DRE-8790", "DRE-8928",
                 "id-token: write"):
        assert said in doc, said


def test_the_module_names_no_email_and_no_sign_in_and_says_the_ceo():
    text = MODULE_PATH.read_text(encoding="utf-8")
    assert not re.search(r"[\w.+-]+@[\w-]+\.\w+", text)
    assert not re.search(r"\bsign[- ]?in\b", text, re.IGNORECASE)
    assert "the CEO" in text


def test_every_write_the_executor_holds_is_the_project_status_update():
    """The claim, the receipt and the close stay in operator_agent_apply.py."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert source.count("mutation") == 1
    assert "projectUpdate" in source
    for call in ("commentCreate", "issueUpdate", "cmd_comment", "add_label", "cmd_state"):
        assert call not in source, call
