"""RED-first tests for DRE-8849 — a card the medic parked because its repo's
`main` was red goes back to work on its own once `main` is green again.

THE BUG (live, 2026-11-01). Four foundry cards sat in Triage for four to eight
hours: DRE-8069, DRE-8064, DRE-8068 and DRE-8074. Nothing was wrong with any of
them. Each had an approved pull request that went red because foundry's own
`main` was broken. On DRE-8069 the medic's diagnosis came back `unreadable`,
its question was housekeeping, and `medic.yml#Ask a person about the
diagnosis` parked the card in Triage under a `planning-stall-park` note. A card
in Triage is human-parked to the sweep, so the fix loop and the stale-merge-ref
refresh stayed off its pull request. `main` was green again by 9:22 PM PT and
nothing moved the cards back; an operator moved each one by hand at about
12:20 AM PT on 11-02.

THE FIX UNDER TEST, in two halves that ship together:

  1. The medic holds the card with a stamp that says why. A new step in
     `medic.yml`, `Hold the card on an inherited red main` (id `redmain`),
     reads the head's and the merge base's check runs on an `ask-person` card
     with an open pull request. `scripts/inherited_red_main.py medic-read`
     answers `hold` only when every failing check on the head failed on the
     merge base too (`inherited_failures.inherited`, the fix loop's strict
     rule), and the step then holds the card `inherited-red-main` at the head
     and moves it to Triage.
  2. The sweep lifts that stamp once `main` is green.
     `reconcile.return_inherited_red_main_holds` reads this repo's held Triage
     cards, and when `stale_merge_ref.decide` answers `refresh` — or the pull
     request has a new head — lifts the hold, moves the card to In Review and
     posts one `inherited-red-main-return` note. `refresh_stale_merge_refs`,
     next in the same pass, then updates the branch with `main`.

The sweep replays run on the fakes `tests/test_reconcile_stale_merge_ref.py`
uses, with a small Linear board beside them.

Run: cd bureau-pipeline && python3 -m pytest tests/test_inherited_red_main.py -v
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "ghs_app")

import hold  # noqa: E402
import inherited_red_main  # noqa: E402
import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import medic_next_step  # noqa: E402
import pipeline_act  # noqa: E402
import reconcile  # noqa: E402
import stale_merge_ref  # noqa: E402
import test_reconcile_stale_merge_ref as smr  # noqa: E402 — its gh fakes
import triage_repair  # noqa: E402
from test_medic_acts_on_diagnosis import (  # noqa: E402
    STUB_CARD_PR,
    STUB_HEAD,
    STUB_LINEAR_OPS,
    STUB_REAL,
    _runs,
)
from triage_repair_fakes import TriageRepairBoard  # noqa: E402

#: The real read, kept before conftest stands it down for every other suite.
_REAL_HELD_CARDS = inherited_red_main.held_cards

CHECK = "Lint · Test · Build"
OTHER_CHECK = "Unit tests"
H = "1" * 40          # the head the medic read and stamped
H2 = "2" * 40         # a head pushed after the stamp
BASE = smr.BASE       # the merge base
G = smr.MAIN          # `main`'s tip, green on the check again
MID = smr.MID         # a `main` merge between the merge base and the tip
CARD = "DRE-8069"
BRANCH = f"agent/{CARD}-offline-media"
PR_NUMBER = 157
SLUG = "foundry"
REPO = f"dreadnought-foundry/{SLUG}"
MEDIC = ROOT / ".github" / "workflows" / "medic.yml"
STAMP = (f"🔒 hold: reason=inherited-red-main at={H} "
         "lifts=inherited-checks-green by=medic.yml")


# --------------------------------------------------------------------------- #
# payloads, in the `gh api --paginate --slurp` shape the medic step reads      #
# --------------------------------------------------------------------------- #


def _run(name, conclusion, suite=1, status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion,
            "check_suite": {"id": suite}}


def _pages(*runs):
    return [{"total_count": len(runs), "check_runs": list(runs)}]


#: 11-01's shape: the head's `Lint · Test · Build` failed, and so did the
#: merge base's, in a run with no cancelled check.
HEAD_1101 = _pages(_run(CHECK, "failure"), _run("Typecheck", "success"))
BASE_1101 = _pages(_run(CHECK, "failure", suite=7), _run("Typecheck", "success", suite=7))


def _medic_read(tmp_path, head, base, *extra):
    head_file, base_file = tmp_path / "head.json", tmp_path / "base.json"
    head_file.write_text(head if isinstance(head, str) else json.dumps(head),
                         encoding="utf-8")
    base_file.write_text(base if isinstance(base, str) else json.dumps(base),
                         encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "inherited_red_main.py"), "medic-read",
         "--checks-file", str(head_file), "--base-checks-file", str(base_file), *extra],
        capture_output=True, text=True, cwd=ROOT, check=False)


# --------------------------------------------------------------------------- #
# 1. the medic's read                                                          #
# --------------------------------------------------------------------------- #


class TestMedicRead:
    def test_the_1101_shape_answers_hold(self, tmp_path):
        out = _medic_read(tmp_path, HEAD_1101, BASE_1101)
        assert out.returncode == 0, out.stderr
        assert out.stdout.splitlines()[0] == "hold"
        assert len(out.stderr.strip().splitlines()) == 1, out.stderr
        assert CHECK in out.stderr

    def test_the_stderr_line_names_the_merge_base(self, tmp_path):
        out = _medic_read(tmp_path, HEAD_1101, BASE_1101, "--base-sha", BASE)
        assert out.stdout.splitlines()[0] == "hold"
        assert BASE[:8] in out.stderr and CHECK in out.stderr

    def test_a_second_failing_check_green_on_the_merge_base_answers_ask(self, tmp_path):
        head = _pages(_run(CHECK, "failure"), _run(OTHER_CHECK, "failure"))
        base = _pages(_run(CHECK, "failure", suite=7), _run(OTHER_CHECK, "success", suite=7))
        out = _medic_read(tmp_path, head, base)
        assert out.returncode == 0, out.stderr
        assert out.stdout.splitlines()[0] == "ask"
        assert OTHER_CHECK in out.stderr

    def test_an_empty_merge_base_file_answers_ask(self, tmp_path):
        out = _medic_read(tmp_path, HEAD_1101, "")
        assert out.returncode == 0, out.stderr
        assert out.stdout.splitlines()[0] == "ask"

    def test_a_head_with_no_failing_check_answers_ask(self, tmp_path):
        out = _medic_read(tmp_path, _pages(_run(CHECK, "success")), BASE_1101)
        assert out.returncode == 0, out.stderr
        assert out.stdout.splitlines()[0] == "ask"

    def test_an_unreadable_head_answers_ask(self, tmp_path):
        out = _medic_read(tmp_path, "", BASE_1101)
        assert out.returncode == 0, out.stderr
        assert out.stdout.splitlines()[0] == "ask"

    def test_a_merge_base_failure_in_a_cancelled_run_is_not_read(self, tmp_path):
        """The strict merge-base read (DRE-8838): a summary job that failed
        because its shards were cancelled tested nothing."""
        base = _pages(_run(CHECK, "failure", suite=7), _run("shard 1", "cancelled", suite=7))
        out = _medic_read(tmp_path, HEAD_1101, base)
        assert out.stdout.splitlines()[0] == "ask"

    def test_a_review_check_is_left_out_of_the_failing_set(self, tmp_path):
        head = _pages(_run(CHECK, "failure"), _run("Plan review", "failure"))
        out = _medic_read(tmp_path, head, BASE_1101)
        assert out.stdout.splitlines()[0] == "hold"

    def test_the_decision_is_the_fix_loops_own_rule(self):
        assert inherited_red_main.medic_read(HEAD_1101, BASE_1101)[0] == "hold"
        assert inherited_red_main.medic_read(
            _pages(_run(CHECK, "failure"), _run(OTHER_CHECK, "failure")),
            BASE_1101)[0] == "ask"

    def test_the_reason_names_the_repo_the_checks_and_the_merge_base(self, tmp_path):
        reason = tmp_path / "reason.txt"
        out = _medic_read(tmp_path, HEAD_1101, BASE_1101, "--base-sha", BASE,
                          "--repo", REPO, "--reason-out", str(reason))
        assert out.stdout.splitlines()[0] == "hold"
        text = reason.read_text(encoding="utf-8")
        assert "\n" not in text.strip()
        for phrase in (REPO, CHECK, BASE[:8], "sweep", "In Review", "green"):
            assert phrase in text, phrase

    def test_no_reason_is_written_on_ask(self, tmp_path):
        reason = tmp_path / "reason.txt"
        _medic_read(tmp_path, HEAD_1101, "", "--base-sha", BASE, "--repo", REPO,
                    "--reason-out", str(reason))
        assert not reason.exists() or not reason.read_text(encoding="utf-8").strip()


# --------------------------------------------------------------------------- #
# 2. the hold stamp                                                            #
# --------------------------------------------------------------------------- #


class TestTheHold:
    def test_the_stamp_byte_for_byte(self):
        assert hold.stamp_line("inherited-red-main", H, "medic.yml") == STAMP

    @pytest.mark.parametrize("at", ["none", None, "abc1234", "repo:foundry", H[:39]])
    def test_a_non_sha_at_raises(self, at):
        with pytest.raises(ValueError):
            hold.stamp_line("inherited-red-main", at, "medic.yml")

    def test_the_vocabulary(self):
        assert "inherited-red-main" in hold.REASONS
        assert "inherited-checks-green" in hold.LIFT_KINDS
        assert hold.CONTRACT_LIFTS["inherited-red-main"] == "inherited-checks-green"
        assert inherited_red_main.HOLD_REASON == "inherited-red-main"
        assert inherited_red_main.LIFT_KIND == "inherited-checks-green"

    def test_the_lift_lines(self):
        assert hold.lift_line("inherited-red-main", "inherited-checks-green",
                              "scripts/reconcile.py") == (
            "🔓 hold lifted: reason=inherited-red-main "
            "because=inherited-checks-green by=scripts/reconcile.py")

    @pytest.mark.parametrize("lane", ["Triage", "In Review", "In Progress", "Queued",
                                      "Planning", "Green Light", "Intake", "Hand-work"])
    def test_lift_due_answers_none_outside_done_and_canceled(self, lane):
        stamp = hold.read_stamp([STAMP])
        assert hold.lift_due(stamp, lane=lane, labels=["needs-human", "repo:foundry"],
                             pr_head=H2, rail_slugs={"foundry"},
                             bodies=[STAMP, "🧠 model-attempt", "⏳ 1/5 plan"]) is None

    @pytest.mark.parametrize("lane", ["Done", "Canceled"])
    def test_lift_due_answers_card_closed_in_a_closed_lane(self, lane):
        stamp = hold.read_stamp([STAMP])
        assert hold.lift_due(stamp, lane=lane, labels=["needs-human"], pr_head=H2,
                             rail_slugs=set(), bodies=[STAMP]) == "card-closed"

    def test_the_hygiene_holds_lane_never_lifts_it(self):
        import hygiene_holds

        assert "inherited-checks-green" not in hygiene_holds.OPEN_LIFTS

    def test_the_registry_row(self):
        doc = hold.load()
        entry = doc["reasons"]["inherited-red-main"]
        assert entry["lifts"] == "inherited-checks-green"
        assert entry["lift_sends"] == "Triage to In Review"
        assert entry["why"].strip()
        assert doc["lift_kinds"]["inherited-checks-green"].strip()
        rows = [r for r in doc["sites"] if r["file"] == ".github/workflows/medic.yml"
                and r["scope"] == "Hold the card on an inherited red main"]
        assert len(rows) == 1
        assert [e["reason"] for e in rows[0]["reasons"]] == ["inherited-red-main"]
        assert sorted(rows[0]["readers"]) == sorted(
            ["sweep", "fix-dispatch", "medic", "limit-recovery"])
        assert hold.problems() == []

    def test_a_held_card_is_parked_to_the_fix_loop(self):
        assert hold.respects(["needs-human"], [STAMP], "fix-dispatch") is True


# --------------------------------------------------------------------------- #
# 3. the medic step                                                            #
# --------------------------------------------------------------------------- #


def _steps() -> list:
    return yaml.safe_load(MEDIC.read_text(encoding="utf-8"))["jobs"]["diagnose"]["steps"]


def _step(step_id: str) -> dict:
    return next(s for s in _steps() if s.get("id") == step_id)


def _index(step_id: str) -> int:
    return next(i for i, s in enumerate(_steps()) if s.get("id") == step_id)


def _flat(text) -> str:
    return " ".join(str(text).split())


STUB_GH_PAYLOADS = STUB_HEAD.replace("os.path.basename(__file__)", '"gh"') + """
args = sys.argv[1:]
joined = " ".join(args)
if "/check-runs" in joined:
    sha = joined.split("/commits/")[1].split("/")[0]
    key = "FAKE_HEAD_CHECKS" if sha == os.environ.get("FAKE_HEAD_SHA") else "FAKE_BASE_CHECKS"
    if not os.environ.get(key):
        sys.exit(1)
    sys.stdout.write(os.environ[key])
elif "/compare/" in joined:
    print(os.environ.get("FAKE_MERGE_BASE", ""))
elif "/pulls/" in joined:
    print(os.environ.get("FAKE_BASE_REF", "main"))
else:
    sys.exit(1)
"""

STUBS = {"linear_ops.py": STUB_LINEAR_OPS, "card_pr.py": STUB_CARD_PR,
         "hold.py": STUB_HEAD + "\nsys.exit(int(os.environ.get('FAKE_HOLD_RC', '0')))\n",
         "inherited_red_main.py": STUB_REAL, "medic_next_step.py": STUB_REAL}

RUN_URL = "https://github.com/dreadnought-foundry/foundry/actions/runs/24680"
PR_URL = f"https://github.com/{REPO}/pull/{PR_NUMBER}"


def _ctx(**over) -> dict:
    ctx = {
        "secrets.LINEAR_API_KEY": "test-linear-key",
        "github.repository": REPO,
        "github.event.workflow_run.head_sha": H,
        "github.event.workflow_run.html_url": RUN_URL,
        "github.event.workflow_run.head_branch": BRANCH,
        "steps.target.outputs.kind": "card",
        "steps.target.outputs.target": CARD,
        "steps.verdict.outputs.class": "unreadable",
        "steps.verdict.outputs.step": "ask-person",
    }
    ctx.update(over)
    return ctx


def _run_steps(ids, ctx, *, find_out=f"OPEN\t{PR_URL}", head=HEAD_1101, base=BASE_1101,
               merge_base=BASE, hold_rc=0, advance="land"):
    """Run medic.yml's steps `ids` in file order as the job does, each `if:`
    evaluated against the outputs before it, every script a stub."""
    calls_out: list = []
    ran: dict = {}
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        scripts, bin_ = td / ".bureau-pipeline" / "scripts", td / "bin"
        scripts.mkdir(parents=True)
        bin_.mkdir()
        for name, body in STUBS.items():
            (scripts / name).write_text(body, encoding="utf-8")
            os.chmod(scripts / name, 0o755)
        (bin_ / "gh").write_text(STUB_GH_PAYLOADS, encoding="utf-8")
        os.chmod(bin_ / "gh", 0o755)
        (td / ".bureau-pipeline" / "medic-report.md").write_text(
            "The run died on a red check.\n", encoding="utf-8")
        calls, summary = td / "calls.log", td / "summary.md"
        calls.touch()
        summary.touch()
        fake = {"FAKE_FIND_OUT": find_out, "FAKE_FIND_RC": "0",
                "FAKE_HEAD_SHA": H, "FAKE_MERGE_BASE": merge_base,
                "FAKE_HEAD_CHECKS": json.dumps(head) if head is not None else "",
                "FAKE_BASE_CHECKS": json.dumps(base) if base is not None else "",
                "FAKE_HOLD_RC": str(hold_rc), "FAKE_ADVANCE": advance,
                "FAKE_STATE": "Triage", "FAKE_COMMENT_RC": "0"}

        def render(text):
            def sub(m):
                key = m.group(1).strip()
                return str(ctx.get(key) or "")
            return re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", sub, text)

        for sid in [s.get("id") for s in _steps() if s.get("id") in ids]:
            step = _step(sid)
            cond = step.get("if")
            if cond is not None and not _runs(str(cond), ctx):
                ran[sid] = False
                continue
            ran[sid] = True
            output = td / f"output-{sid}"
            output.touch()
            env = {k: render(str(v)) for k, v in (step.get("env") or {}).items()}
            proc = subprocess.run(
                ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
                cwd=td, capture_output=True, text=True, check=False,
                env={**os.environ, **env, **fake,
                     "PATH": f"{bin_}:{os.path.dirname(sys.executable)}:{os.environ['PATH']}",
                     "PYTHONIOENCODING": "utf-8",
                     "REAL_SCRIPTS": str(ROOT / "scripts"),
                     "CALLS": str(calls), "GITHUB_OUTPUT": str(output),
                     "GITHUB_STEP_SUMMARY": str(summary), "RUNNER_TEMP": str(td)})
            assert proc.returncode == 0, f"step {sid} exited {proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
            for line in output.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    ctx[f"steps.{sid}.outputs.{k}"] = v
        calls_out = [json.loads(ln) for ln in calls.read_text(encoding="utf-8").splitlines() if ln]
    return SimpleNamespace(calls=calls_out, ran=ran, ctx=ctx)


def _called(run, script, cmd=None):
    return [c for c in run.calls if c[0] == script and (cmd is None or c[1:2] == [cmd])]


class TestTheMedicStep:
    def test_the_step_sits_after_the_verdict_and_before_the_ask(self):
        step = _step("redmain")
        assert step["name"] == "Hold the card on an inherited red main"
        assert _index("verdict") < _index("redmain") < _index("ask") < _index("receipt")

    def test_it_runs_only_on_ask_person_for_a_card_that_is_not_waiting(self):
        cond = str(_step("redmain")["if"])
        assert _runs(cond, _ctx())
        assert not _runs(cond, _ctx(**{"steps.target.outputs.kind": "repair-card"}))
        assert not _runs(cond, _ctx(**{"steps.target.outputs.kind": "notice"}))
        for other in ("fix-path", "send-to-planning", "requeue-once", ""):
            assert not _runs(cond, _ctx(**{"steps.verdict.outputs.step": other})), other
        assert not _runs(cond, _ctx(**{"steps.waiting.outputs.waiting": "true"}))
        assert _runs(cond, _ctx(**{"steps.waiting.outputs.waiting": "false"}))

    def test_the_ask_steps_gate_excludes_a_held_card(self):
        cond = str(_step("ask")["if"])
        assert "steps.redmain.outputs.held != 'true'" in _flat(cond)
        assert not _runs(cond, _ctx(**{"steps.redmain.outputs.held": "true"}))
        assert _runs(cond, _ctx())

    def test_the_read_is_the_fix_loops_read(self):
        text = _step("redmain")["run"]
        for phrase in ("card_pr.py\" find", "--jq .base.ref",
                       "compare/$BASE_REF...$HEAD_SHA", "--jq .merge_base_commit.sha",
                       "--paginate --slurp", "inherited_red_main.py\" medic-read",
                       "--reason inherited-red-main", "--at \"$HEAD_SHA\"", "--by medic.yml",
                       "advance \"$TARGET\" \"Triage\" \"$FROM_LANES\""):
            assert phrase in text, phrase
        env = _step("redmain")["env"]
        assert env["HEAD_SHA"] == "${{ github.event.workflow_run.head_sha }}"
        # An unreadable merge-base read stays empty, never `[]`.
        base_read = next(line for line in text.splitlines()
                         if "--paginate --slurp" in line and "MERGE_BASE" in line)
        assert "'[]'" not in base_read and "echo []" not in base_read

    def test_on_hold_it_holds_the_card_moves_it_and_says_so(self):
        run = _run_steps(("redmain",), _ctx())
        assert _called(run, "hold.py") == [["hold.py", "apply", CARD, "--reason",
                                            "inherited-red-main", "--at", H,
                                            "--by", "medic.yml"]]
        advance = _called(run, "linear_ops.py", "advance")
        assert len(advance) == 1 and advance[0][2:4] == [CARD, "Triage"]
        assert advance[0][4] == ",".join(medic_next_step.from_lanes())
        assert run.ctx["steps.redmain.outputs.held"] == "true"
        assert run.ctx["steps.redmain.outputs.moved"] == "true"
        assert run.ctx["steps.redmain.outputs.lane"] == "Triage"
        reason = run.ctx["steps.redmain.outputs.reason"]
        for phrase in (REPO, CHECK, BASE[:8]):
            assert phrase in reason, phrase

    def test_the_hold_comes_before_the_move(self):
        run = _run_steps(("redmain",), _ctx())
        order = [c[0] + ":" + c[1] for c in run.calls if c[0] in ("hold.py", "linear_ops.py")]
        assert order == ["hold.py:apply", "linear_ops.py:advance"]

    @pytest.mark.parametrize("find_out", ["", f"MERGED\t{PR_URL}", f"CLOSED\t{PR_URL}"])
    def test_no_open_pull_request_means_no_read_and_no_hold(self, find_out):
        run = _run_steps(("redmain",), _ctx(), find_out=find_out)
        assert _called(run, "gh") == []
        assert _called(run, "hold.py") == []
        assert _called(run, "linear_ops.py", "advance") == []
        assert run.ctx.get("steps.redmain.outputs.held") != "true"

    @pytest.mark.parametrize("kw", [
        {"head": _pages(_run(CHECK, "failure"), _run(OTHER_CHECK, "failure"))},
        {"base": None},
        {"head": _pages(_run(CHECK, "success"))},
        {"merge_base": ""},
    ], ids=["own-check", "unreadable-base", "no-failure", "no-merge-base"])
    def test_on_ask_it_writes_nothing_and_the_ask_step_runs(self, kw):
        run = _run_steps(("redmain", "ask"), _ctx(), **kw)
        assert _called(run, "hold.py") == []
        assert run.ctx.get("steps.redmain.outputs.held") != "true"
        assert run.ran["ask"] is True

    def test_on_hold_the_ask_step_does_not_run(self):
        run = _run_steps(("redmain", "ask"), _ctx())
        assert run.ran["ask"] is False

    def test_a_hold_that_would_not_write_falls_back_to_asking(self):
        run = _run_steps(("redmain", "ask"), _ctx(), hold_rc=1)
        assert run.ctx.get("steps.redmain.outputs.held") != "true"
        assert run.ran["ask"] is True

    def test_a_move_that_did_not_land_is_moved_false(self):
        run = _run_steps(("redmain",), _ctx(), advance="refuse")
        assert run.ctx["steps.redmain.outputs.held"] == "true"
        assert run.ctx["steps.redmain.outputs.moved"] == "false"

    def test_the_receipt_is_the_one_receipt_for_the_hold(self):
        run = _run_steps(("redmain", "ask", "receipt"), _ctx())
        comments = _called(run, "linear_ops.py", "comment")
        assert len(comments) == 1, comments
        _, _, target, body, *flags = comments[0]
        assert target == CARD and "--act=medic-next-step" in flags
        first, rest = body.split("\n", 1)
        assert first == f"{medic_next_step.RECEIPT_MARK} class=unreadable step=ask-person run=24680"
        flat = _flat(rest)
        for phrase in (REPO, CHECK, BASE[:8], "Triage"):
            assert phrase in flat, phrase
        assert "green" in flat and "sweep" in flat
        assert "Green Light" not in flat
        assert "housekeeping" not in flat

    def test_a_held_receipt_whose_move_did_not_land_says_moved_no(self):
        run = _run_steps(("redmain", "ask", "receipt"), _ctx(), advance="refuse")
        body = _called(run, "linear_ops.py", "comment")[0][3]
        assert body.split("\n", 1)[0].endswith(" moved=no")

    def test_the_receipt_step_reads_the_hold_outputs(self):
        env = _step("receipt")["env"]
        for key in ("held", "moved", "lane", "reason"):
            assert f"steps.redmain.outputs.{key}" in " ".join(map(str, env.values())), key

    def test_the_medic_receipt_names_a_held_card_in_triage(self):
        step = medic_next_step.NextStep("unreadable", "ask-person", "main was red.")
        text = medic_next_step.receipt(step, run_url=RUN_URL, finding="x",
                                       moved_to="Triage", held_on_main=True)
        assert "Green Light" not in text and "housekeeping" not in text
        assert "Triage" in text


# --------------------------------------------------------------------------- #
# 4. the sweep                                                                 #
# --------------------------------------------------------------------------- #


class _Board:
    """The Linear side of a sweep pass: this repo's cards, every write logged
    in order as `(kind, card, detail)`."""

    def __init__(self):
        self.cards: dict = {}
        self.events: list = []
        self.fail: set = set()

    def add(self, ident, *, lane="Triage", labels=("needs-human", f"repo:{SLUG}"),
            comments=()):
        self.cards[ident] = {"lane": lane, "labels": list(labels),
                             "comments": list(comments)}

    def _node(self, ident):
        card = self.cards[ident]
        return {"id": f"id-{ident}", "identifier": ident,
                "state": {"name": card["lane"]},
                "labels": {"nodes": [{"name": n} for n in card["labels"]]},
                "comments": {"pageInfo": {"hasNextPage": False},
                             "nodes": [{"body": b, "createdAt": "2026-11-01T20:00:00.000Z"}
                                       for b in reversed(card["comments"])]}}

    def gql(self, query, variables=None, **_kw):
        variables = variables or {}
        if "issues(" in query:
            needle = (variables.get("needle") or "").lower()
            states = variables.get("states") or []
            nodes = [self._node(i) for i, c in self.cards.items()
                     if c["lane"] in states
                     and "needs-human" in c["labels"]
                     and any(needle in n.lower() for n in c["labels"])]
            return {"issues": {"nodes": nodes,
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        if "issue(id" in query:
            return {"issue": self._node(variables["id"])}
        raise AssertionError(f"unexpected Linear query: {query[:80]}")

    def _check(self, kind):
        if kind in self.fail:
            raise linear_ops.LinearError(f"the board refused {kind}")

    def remove_label(self, ident, name):
        self._check("label")
        self.cards[ident]["labels"] = [n for n in self.cards[ident]["labels"] if n != name]
        self.events.append(("label-off", ident, name))

    def add_label(self, ident, name):
        self.cards[ident]["labels"].append(name)
        self.events.append(("label-on", ident, name))

    def cmd_comment(self, ident, body, *flags):
        self._check("comment")
        self.cards[ident]["comments"].append(body)
        self.events.append(("comment", ident, body))

    def cmd_advance(self, ident, to, from_csv, *flags, **_kw):
        self._check("move")
        if self.cards[ident]["lane"] not in [s.strip() for s in from_csv.split(",")]:
            return False
        self.cards[ident]["lane"] = to
        self.events.append(("move", ident, to))
        return True


def _pr(head=H, number=PR_NUMBER, branch=BRANCH, state="OPEN"):
    pr = smr._pr(number=number, sha=head, branch=branch)
    pr.update({"state": state, "url": f"https://github.com/{REPO}/pull/{number}"})
    return pr


@pytest.fixture
def board(monkeypatch):
    monkeypatch.setattr(inherited_red_main, "held_cards", _REAL_HELD_CARDS)
    monkeypatch.setattr(reconcile, "REPO", REPO)
    monkeypatch.setattr(reconcile, "REPO_SLUG", SLUG)
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._degraded.clear()
    b = _Board()
    for name in ("gql", "remove_label", "add_label", "cmd_comment", "cmd_advance"):
        monkeypatch.setattr(linear_ops, name, getattr(b, name))
    yield b
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._degraded.clear()


def _pass(board_, state, *, refresh=True):
    """One full pass of the two backstops, in the order the sweep runs them."""
    with patch.object(reconcile.subprocess, "run", side_effect=smr._run_factory(state)):
        reconcile.return_inherited_red_main_holds()
        if refresh:
            reconcile.refresh_stale_merge_refs()
    return state


def _held_1101(board_, *, stamp=STAMP):
    board_.add(CARD, comments=[
        "🩺 the diagnosis",
        stamp,
        "🩺 medic-next-step: class=unreadable step=ask-person run=24680",
    ])


def _card_writes(board_, ident=CARD):
    return [(kind, detail) for kind, card, detail in board_.events if card == ident]


LIFT_GREEN = ("🔓 hold lifted: reason=inherited-red-main "
              "because=inherited-checks-green by=scripts/reconcile.py")
LIFT_NEW_HEAD = ("🔓 hold lifted: reason=inherited-red-main because=new-head "
                 "by=scripts/reconcile.py")


def _is_return_note(body):
    trailer = pipeline_act.read_trailer(body)
    return bool(trailer) and trailer["act"] == inherited_red_main.ACT \
        and trailer["tag"] == inherited_red_main.RETURN_TAG


class TestTheSweepReturnsTheCard:
    def test_the_1101_replay_lifts_moves_notes_and_refreshes(self, board):
        _held_1101(board)
        state = _pass(board, smr._state([_pr()]))
        writes = _card_writes(board)
        assert [k for k, _ in writes][:4] == ["label-off", "comment", "move", "comment"], writes
        assert writes[0] == ("label-off", "needs-human")
        assert writes[1] == ("comment", LIFT_GREEN)
        assert writes[2] == ("move", reconcile.REVIEW_LANE)
        note = writes[3][1]
        assert _is_return_note(note)
        assert G[:8] in note and CHECK.split(" ")[0] in note
        # The same pass's refresh: one update-branch at the head read, and
        # its own receipt on the pull request and the card.
        assert len(state["puts"]) == 1
        assert f"expected_head_sha={H}" in state["puts"][0]["argv"]
        assert f"repos/{REPO}/pulls/{PR_NUMBER}/update-branch" in state["puts"][0]["argv"]
        assert len(writes) == 5, writes
        assert stale_merge_ref.marker(G) in writes[4][1]
        assert len(state["pr_comments"]) == 1
        assert board.cards[CARD]["lane"] == "In Review"
        assert "needs-human" not in board.cards[CARD]["labels"]
        assert reconcile._write_failures == [] and reconcile._read_failures == []

    def test_exactly_one_note(self, board):
        _held_1101(board)
        _pass(board, smr._state([_pr()]))
        notes = [d for k, d in _card_writes(board) if k == "comment" and _is_return_note(d)]
        assert len(notes) == 1

    def test_a_second_pass_writes_nothing_more(self, board):
        _held_1101(board)
        _pass(board, smr._state([_pr()]))
        before = list(board.events)
        state = _pass(board, smr._state([_pr()]), refresh=False)
        assert board.events == before
        assert state["puts"] == []

    def test_a_second_pass_over_a_card_still_in_triage_reads_the_spent_stamp(self, board):
        _held_1101(board)
        _pass(board, smr._state([_pr()]), refresh=False)
        board.cards[CARD]["lane"] = "Triage"           # a person puts it back
        board.cards[CARD]["labels"].append("needs-human")
        before = list(board.events)
        _pass(board, smr._state([_pr()]), refresh=False)
        assert board.events == before


class TestTheSweepLeavesTheCardHeld:
    @pytest.mark.parametrize("kw", [
        {"main": smr._checks(smr._red(CHECK))},
        {"main": smr._checks(smr._red(CHECK)), "window": {MID: smr._checks(smr._green(CHECK))}},
        {"main": smr._checks({"name": CHECK, "status": "in_progress", "conclusion": None})},
    ], ids=["main-still-red", "green-then-red-again", "main-still-running"])
    def test_main_not_green_writes_nothing(self, board, kw):
        _held_1101(board)
        state = _pass(board, smr._state([_pr()], head=smr._checks(smr._red(CHECK)),
                                        base=smr._checks(smr._red(CHECK)), **kw))
        assert board.events == []
        assert board.cards[CARD]["lane"] == "Triage"
        assert "needs-human" in board.cards[CARD]["labels"]
        assert hold.read_stamp(board.cards[CARD]["comments"])["reason"] == "inherited-red-main"
        assert state["puts"] == [] and state["pr_comments"] == []
        assert reconcile._write_failures == []

    def test_a_spent_cap_leaves_the_card_held(self, board):
        _held_1101(board)
        state = _state_1101()
        with patch.object(reconcile, "STALE_MERGE_REFRESH_CAP", 0):
            _pass(board, state)
        assert board.events == [] and state["puts"] == []


def _state_1101(prs=None, **kw):
    kw.setdefault("head", smr._checks(smr._red(CHECK)))
    kw.setdefault("base", smr._checks(smr._red(CHECK)))
    kw.setdefault("main", smr._checks(smr._green(CHECK)))
    return smr._state(prs or [_pr()], **kw)


class TestANewHead:
    def test_a_new_head_lifts_moves_and_names_it(self, board):
        _held_1101(board)
        # H2's own CI is green so far: nothing for the refresh to act on.
        state = _pass(board, _state_1101([_pr(head=H2)], head=smr._checks(smr._green(CHECK))))
        writes = _card_writes(board)
        assert [k for k, _ in writes] == ["label-off", "comment", "move", "comment"], writes
        assert writes[1] == ("comment", LIFT_NEW_HEAD)
        assert writes[2] == ("move", reconcile.REVIEW_LANE)
        assert _is_return_note(writes[3][1])
        assert H2[:8] in writes[3][1] and H[:8] in writes[3][1]
        assert state["puts"] == []

    def test_the_new_head_path_reads_no_check_runs(self, board):
        _held_1101(board)
        state = _pass(board, _state_1101([_pr(head=H2)]), refresh=False)
        assert [c for c in state["gh"] if c["argv"][1] == "api"] == []
        assert state["puts"] == []


class TestOtherTriageCards:
    def test_cards_held_or_parked_for_any_other_reason_are_left_alone(self, board):
        board.add("DRE-1", comments=[hold.stamp_line(
            "no-route", "repo:wrong-thing", "scripts/reconcile.py")],
            labels=("needs-human", f"repo:{SLUG}"))
        board.add("DRE-2", labels=(f"repo:{SLUG}",), comments=[
            "🚨 prose-blocker-no-relation: DRE-2 claims a dependency on DRE-99999 in its description"])
        board.add("DRE-3", labels=(f"repo:{SLUG}",), comments=[
            "🧹 planning-stall-park: DRE-3 — the medic's question is housekeeping"])
        board.add("DRE-4", labels=("needs-human", f"repo:{SLUG}"), comments=[
            "🧹 planning-stall-park: DRE-4 — parked with the label and no stamp"])
        state = _pass(board, _state_1101([_pr(branch="agent/DRE-1-x")]), refresh=False)
        assert board.events == []
        assert [c for c in state["gh"]] == [], "no pull request is read for them"

    def test_a_stamped_card_in_another_lane_is_not_a_candidate(self, board):
        board.add(CARD, lane="In Review", comments=[STAMP])
        state = _pass(board, _state_1101(), refresh=False)
        assert board.events == [] and state["gh"] == []

    def test_another_repos_card_is_not_a_candidate(self, board):
        board.add(CARD, labels=("needs-human", "repo:agent-bureau"), comments=[STAMP])
        state = _pass(board, _state_1101(), refresh=False)
        assert board.events == [] and state["gh"] == []

    def test_a_card_with_no_open_pull_request_stays_held(self, board):
        _held_1101(board)
        _pass(board, _state_1101([_pr(state="MERGED")]), refresh=False)
        assert board.events == []


class TestFailures:
    def test_a_failed_write_stops_the_sequence_and_goes_red(self, board):
        _held_1101(board)
        board.fail.add("move")
        _pass(board, _state_1101(), refresh=False)
        kinds = [k for k, _ in _card_writes(board)]
        assert kinds == ["label-off", "comment"]
        assert reconcile._write_failures

    def test_a_failed_lift_moves_nothing(self, board):
        _held_1101(board)
        board.fail.add("label")
        _pass(board, _state_1101(), refresh=False)
        assert board.events == []
        assert reconcile._write_failures

    def test_a_rate_limited_board_read_degrades(self, board, monkeypatch):
        def limited(*_a, **_k):
            raise linear_ops.LinearRateLimited("RATELIMITED")
        monkeypatch.setattr(linear_ops, "gql", limited)
        _pass(board, _state_1101(), refresh=False)
        assert reconcile._read_failures == [] and reconcile._write_failures == []
        assert any("inherited-red-main" in d for d in reconcile._degraded)

    def test_another_board_read_failure_is_a_read_failure(self, board, monkeypatch):
        def broken(*_a, **_k):
            raise linear_ops.LinearError("HTTP 400")
        monkeypatch.setattr(linear_ops, "gql", broken)
        _pass(board, _state_1101(), refresh=False)
        assert reconcile._read_failures

    def test_an_unreadable_compare_is_a_read_failure_and_writes_nothing(self, board):
        _held_1101(board)
        _pass(board, _state_1101(compare=RuntimeError("502")), refresh=False)
        assert board.events == []
        assert reconcile._read_failures


class TestWhereItRuns:
    def test_it_runs_immediately_before_the_refresh(self):
        import inspect
        source = inspect.getsource(reconcile.main)
        names = re.findall(r"^\s+([a-z_]+),$", source.split("for backstop in (", 1)[1]
                           .split("):", 1)[0], re.M)
        i = names.index("return_inherited_red_main_holds")
        assert names[i + 1] == "refresh_stale_merge_refs"
        assert names[i - 1] == "unstick_conflicts"

    def test_the_merge_event_sweep_does_not_run_it(self):
        with patch.object(reconcile, "unstick_conflicts"), \
             patch.object(reconcile, "return_inherited_red_main_holds") as ret:
            reconcile.main(conflicts_only=True)
        ret.assert_not_called()

    def test_the_reads_are_one_helper_both_backstops_call(self):
        import inspect
        refresh = inspect.getsource(reconcile.refresh_stale_merge_refs)
        ours = inspect.getsource(reconcile.return_inherited_red_main_holds)
        for source in (refresh, ours):
            assert "_MergeRefReads(" in source
            assert "/compare/" not in source


# --------------------------------------------------------------------------- #
# 5. the Triage repair waits                                                   #
# --------------------------------------------------------------------------- #


class TestTriageRepairWaits:
    def _row(self):
        return next(r for r in triage_repair.load_table()["rows"]
                    if r["unit"] == "medic.yml#Hold the card on an inherited red main")

    def test_the_row(self):
        row = self._row()
        assert row["marker"] == {"hold": "inherited-red-main"}
        assert row["repair"] == "wait"
        assert "main" in row["why"] and row["why"].strip()
        assert "wait" in triage_repair.REPAIR_KINDS

    def test_select_gives_the_card_wait_and_claims_nothing(self, tmp_path, capsys,
                                                          monkeypatch):
        monkeypatch.setattr(triage_repair, "_now",
                            lambda: __import__("datetime").datetime(
                                2026, 11, 2, 8, tzinfo=__import__("datetime").UTC))
        board_ = TriageRepairBoard()
        board_.add(CARD, labels=["needs-human", "repo:agent-bureau", "agent:engineer"],
                   comments=["🩺 the diagnosis", STAMP])
        out = tmp_path / "selected.json"
        with board_.patched():
            assert triage_repair.main(["select", "--out", str(out), "--limit", "3"]) == 0
        assert json.loads(out.read_text(encoding="utf-8")) == []
        printed = capsys.readouterr().out
        assert f"triage-repair: {CARD} → wait — {self._row()['why']}" in printed
        assert board_.events == []

    def test_decide_maps_wait_to_the_wait_action_with_the_rows_why(self):
        board_ = TriageRepairBoard()
        board_.add(CARD, comments=["🩺 the diagnosis", STAMP])
        card = board_._board_node(board_.cards[CARD])
        records = [{"body": b, "authored_by_pipeline": True} for b in board_.bodies(CARD)]
        row = triage_repair.decide(card, records)
        assert row["action"] == triage_repair.WAIT
        assert row["why"] == self._row()["why"]


# --------------------------------------------------------------------------- #
# 6. the registries                                                            #
# --------------------------------------------------------------------------- #


class TestTheRegistries:
    def test_the_act(self):
        entry = pipeline_act.record(inherited_red_main.ACT)
        assert inherited_red_main.ACT == "inherited-red-main-returned"
        assert entry["tag"] == inherited_red_main.RETURN_TAG == "inherited-red-main-return"
        assert entry["emits"]["file"] == "scripts/inherited_red_main.py"
        assert entry["adopted"] is True
        assert pipeline_act.problems() == []

    def test_the_note_is_composed_through_the_act(self):
        decision = stale_merge_ref.Decision(
            stale_merge_ref.REFRESH, "fixed", [CHECK], BASE, G, 2,
            [{"check": CHECK, "base_red": True, "red_sha": BASE, "green_sha": G}])
        note = inherited_red_main.return_note(CARD, pr_number=PR_NUMBER, old_head=H,
                                              decision=decision)
        assert CHECK in note and BASE[:8] in note and G[:8] in note
        assert note.startswith(f"↩️ {inherited_red_main.RETURN_TAG}:")
        assert not note.startswith(hold.LIFT_PREFIX)
        body = pipeline_act.receipt(inherited_red_main.ACT, note)
        assert _is_return_note(body)

    def test_the_new_head_note_names_both_heads(self):
        note = inherited_red_main.return_note(CARD, pr_number=PR_NUMBER, old_head=H,
                                              new_head=H2)
        assert H[:8] in note and H2[:8] in note

    def test_the_triage_reason(self):
        doc = json.loads((ROOT / "config" / "triage-reasons.json").read_text(encoding="utf-8"))
        rows = [r for r in doc["reasons"]
                if r["unit"] == "medic.yml#Hold the card on an inherited red main"]
        assert len(rows) == 1 and rows[0]["anchor"] == "--reason inherited-red-main"
        import triage_reasons
        assert triage_reasons.problems() == []
        text = (ROOT / "docs" / "triage-reasons.md").read_text(encoding="utf-8")
        assert "`medic.yml#Hold the card on an inherited red main`" in text

    def test_the_lane_contract(self):
        triage = lane_contract.lane("Triage")["clauses"]
        review = lane_contract.lane("In Review")["clauses"]
        for text in (triage["entrance"]["text"], triage["exit"]["text"],
                     review["entrance"]["text"]):
            assert "inherited-red-main" in text
        assert "medic.yml" in lane_contract.lane("Triage")["clauses"]["writers"]["who"]
        assert "reconcile.py" in lane_contract.lane("In Review")["clauses"]["writers"]["who"]

    def test_the_lane_contract_page_is_rendered(self):
        assert lane_contract.render_markdown() == (
            ROOT / "docs" / "lane-contract.md").read_text(encoding="utf-8")

    def test_the_holds_page(self):
        text = (ROOT / "docs" / "holds.md").read_text(encoding="utf-8")
        assert "`inherited-red-main`" in text and "`inherited-checks-green`" in text
        assert "Hold the card on an inherited red main" in text
