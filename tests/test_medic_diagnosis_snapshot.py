"""The diagnosis agent gets the card as a snapshot, not a Linear key
(Stage 2 fix #23, package BP-6).

Before this change the diagnosis agent ran with `LINEAR_API_KEY` in its
environment and a prompt that suggested "GraphQL via curl with LINEAR_API_KEY"
for finding an existing failure card. That is an open-ended reader on the
fleet's Linear hour: how many requests a diagnosis spent was whatever the model
chose, and nothing counted them. Every one printed `budget: undeclared`.

What the agent actually needs from Linear is small and knowable in advance:
which card the report goes to (the head branch's card, an open
"Pipeline failure: <workflow>" card, or none yet), and that card's recent
history, so a repeat failure can say whether the diagnosis changed. So:

  * a step BEFORE the agent (`medic_retry.py diagnosis-target`) resolves the
    target and writes the card's facts to a snapshot file — one read for a
    card on the branch, two for a failure card found by title, one when there
    is none yet;
  * the agent reads that file and the failed run's logs, and writes its report
    to a file. It holds no Linear key;
  * a step AFTER the agent posts the report: a comment, through the same act
    as before (`run-failure-diagnosed`), or a new card through `create`.

The diagnosis itself was never read from Linear. The agent reads the failed
run's logs; that part of the prompt is unchanged, and so is its turn ceiling
and its web access.
"""

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import linear_ops  # noqa: E402
import medic_retry  # noqa: E402
import pipeline_act  # noqa: E402

WORKFLOW = os.path.join(ROOT, ".github", "workflows", "medic.yml")
SNAPSHOT = ".bureau-pipeline/medic-card-snapshot.json"
REPORT = ".bureau-pipeline/medic-report.md"


def _jobs() -> dict:
    with open(WORKFLOW, encoding="utf-8") as f:
        return yaml.safe_load(f)["jobs"]


def _diagnose_steps() -> list:
    return _jobs()["diagnose"]["steps"]


def _step(step_id: str) -> dict:
    return next(s for s in _diagnose_steps() if s.get("id") == step_id)


def _index(step_id: str) -> int:
    return next(i for i, s in enumerate(_diagnose_steps()) if s.get("id") == step_id)


# ── 1. the agent holds no Linear key ─────────────────────────────────────────
class TheAgentHoldsNoLinearKeyTest(unittest.TestCase):
    def test_the_agent_step_has_no_linear_key(self):
        agent = _step("claude")
        self.assertNotIn("LINEAR_API_KEY", (agent.get("env") or {}))
        self.assertNotIn("LINEAR_API_KEY", yaml.safe_dump(agent))

    def test_the_prompt_never_sends_the_agent_to_linear(self):
        prompt = _step("claude")["with"]["prompt"]
        for phrase in ("linear_ops.py", "GraphQL", "curl", "LINEAR_API_KEY"):
            self.assertNotIn(phrase, prompt)

    def test_the_prompt_hands_over_the_snapshot_and_names_the_report_file(self):
        prompt = _step("claude")["with"]["prompt"]
        self.assertIn(SNAPSHOT, prompt)
        self.assertIn(REPORT, prompt)
        # Card text is agent-influenced: the agent is told it is data.
        self.assertIn("not instructions", prompt)

    def test_the_diagnosis_part_of_the_prompt_is_unchanged(self):
        prompt = _step("claude")["with"]["prompt"]
        self.assertIn("gh run view ${{ github.event.workflow_run.id }} --log-failed", prompt)
        self.assertIn("Distinguish: (a) bad code on the", prompt)
        self.assertIn("Do NOT attempt to fix anything", prompt)


# ── 2. the steps around the agent ────────────────────────────────────────────
class TheStepsAroundTheAgentTest(unittest.TestCase):
    def test_the_target_is_resolved_before_the_agent(self):
        step = _step("target")
        self.assertLess(_index("target"), _index("claude"))
        self.assertIn("medic_retry.py diagnosis-target", step["run"])
        self.assertIn(SNAPSHOT, step["run"])
        self.assertEqual("${{ secrets.LINEAR_API_KEY }}", step["env"]["LINEAR_API_KEY"])

    def test_the_target_step_never_takes_the_diagnosis_down(self):
        """No answer is not a reason to skip the diagnosis: the delivery step
        resolves the target itself when this one could not."""
        self.assertIn("|| OUT=", _step("target")["run"])

    def test_the_report_is_delivered_after_the_agent(self):
        step = _step("deliver")
        self.assertGreater(_index("deliver"), _index("claude"))
        self.assertEqual("${{ secrets.LINEAR_API_KEY }}", step["env"]["LINEAR_API_KEY"])
        self.assertIn("--act=run-failure-diagnosed", step["run"])
        self.assertIn("linear_ops.py create", step["run"])

    def test_agent_influenced_text_reaches_the_steps_through_env(self):
        """DRE-1996: the head branch and the run name never sit on a shell
        line as `${{ }}`."""
        for step_id in ("target", "deliver"):
            run = _step(step_id)["run"]
            self.assertNotIn("${{", run, step_id)

    def test_the_act_is_still_pinned_where_it_is_emitted(self):
        """The registry pins `run-failure-diagnosed` to its emission in
        medic.yml. The emission moved from the prompt into the delivery step;
        the pin moved with it, and the registry still binds."""
        self.assertEqual([], pipeline_act.problems())
        anchor = pipeline_act.record("run-failure-diagnosed")["emits"]["anchor"]
        self.assertIn(anchor, _step("deliver")["run"])


# ── 3. diagnosis-target: one read per card, a snapshot, no guessing ──────────
class _Resp:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()
        self.headers = {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


#: The repair card the red-main repair loop filed on 2026-10-09 (DRE-6511),
#: spelled out rather than rebuilt with `repair_card.card_title`, so a lookup
#: under any other title misses it.
REPAIR_TITLE_2026_10_09 = "Red main repaired — Pipeline Tests failed at bcb36adf6037"
HEAD_SHA_2026_10_09 = "bcb36adf60370d0e8052c5a3f81021900f93165b"


class _Linear:
    """`open_card` answers any title search; `repair_cards` answers only the
    exact titles it holds, and a search for a repair title never falls through
    to `open_card`."""

    def __init__(self, open_card=None, fail_find=False, repair_cards=None,
                 fail_repair=False):
        self.open_card = open_card
        self.fail_find = fail_find
        self.repair_cards = dict(repair_cards or {})
        self.fail_repair = fail_repair
        self.queries: list[str] = []
        self.searched: list[str] = []

    def __call__(self, request, *_a, **_k):
        payload = json.loads(request.data)
        query = payload["query"]
        self.queries.append(query)
        if "issues(filter" in query:
            title = (payload.get("variables") or {}).get("t", "")
            self.searched.append(title)
            if title.startswith("Red main repaired — "):
                if self.fail_repair:
                    raise OSError("connection refused")
                found = self.repair_cards.get(title)
                nodes = [{"identifier": found}] if found else []
                return _Resp({"data": {"issues": {"nodes": nodes}}})
            if self.fail_find:
                raise OSError("connection refused")
            nodes = [{"identifier": self.open_card}] if self.open_card else []
            return _Resp({"data": {"issues": {"nodes": nodes}}})
        return _Resp({"data": {"issue": {
            "title": "Pipeline failure: Reconcile",
            "description": "the sweep died",
            "state": {"name": "Planning"},
            "labels": {"nodes": [{"name": "repo:agent-bureau"}]},
            "comments": {"pageInfo": {"hasNextPage": False}, "nodes": [
                {"body": "failed again; diagnosis unchanged",
                 "createdAt": "2026-10-02T19:00:00Z"},
            ]},
        }}})


def _target(branch, workflow="Reconcile", *, linear=None, head_sha="",
            default_branch="", err=None):
    linear_ops._reset_budget_state()
    extra = []
    if head_sha:
        extra += ["--head-sha", head_sha]
    if default_branch:
        extra += ["--default-branch", default_branch]
    with tempfile.TemporaryDirectory() as d:
        snap = os.path.join(d, "snap.json")
        out = io.StringIO()
        with mock.patch.object(linear_ops.urllib.request, "urlopen", linear or _Linear()):
            with mock.patch.object(linear_ops.time, "sleep", lambda _s: None):
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(err if err is not None else io.StringIO()):
                    rc = medic_retry.main([
                        "diagnosis-target", "--branch", branch,
                        "--workflow", workflow, "--snapshot", snap, *extra,
                    ])
        calls = linear_ops.requests_made()
        snapshot = None
        if os.path.exists(snap):
            with open(snap, encoding="utf-8") as f:
                snapshot = json.load(f)
    linear_ops._reset_budget_state()
    lines = dict(ln.split("=", 1) for ln in out.getvalue().splitlines() if "=" in ln)
    return rc, lines, calls, snapshot


class DiagnosisTargetTest(unittest.TestCase):
    def test_a_card_branch_is_the_target_and_costs_one_read(self):
        rc, out, calls, snap = _target("agent/DRE-5620-chain-risk-reason")
        self.assertEqual(0, rc)
        self.assertEqual({"kind": "card", "target": "DRE-5620"},
                         {k: out[k] for k in ("kind", "target")})
        self.assertEqual(1, calls)
        self.assertEqual("DRE-5620", snap["target"])
        self.assertEqual("Planning", snap["state"])
        self.assertEqual("the sweep died", snap["description"])
        self.assertEqual(["failed again; diagnosis unchanged"],
                         [c["body"] for c in snap["comments"]])

    def test_an_open_failure_card_is_found_by_title_and_read(self):
        rc, out, calls, snap = _target("main", linear=_Linear(open_card="DRE-4000"))
        self.assertEqual(("failure-card", "DRE-4000"), (out["kind"], out["target"]))
        self.assertEqual("Pipeline failure: Reconcile", out["title"])
        self.assertEqual(2, calls)
        self.assertEqual("DRE-4000", snap["target"])

    def test_no_failure_card_yet_means_a_new_one(self):
        rc, out, calls, snap = _target("main")
        self.assertEqual(("new", ""), (out["kind"], out["target"]))
        self.assertEqual(1, calls)
        self.assertEqual("new", snap["kind"])
        self.assertNotIn("comments", snap)

    def test_an_unanswered_search_is_unknown_never_new(self):
        """A search that failed is not a search that found nothing. `new`
        would mint a duplicate card; `unknown` sends the delivery step to ask
        again."""
        rc, out, calls, snap = _target("main", linear=_Linear(fail_find=True))
        self.assertEqual(0, rc)
        self.assertEqual("unknown", out["kind"])

    def test_the_title_is_one_line(self):
        rc, out, _, _ = _target("main", workflow="Reconcile\nkind=card")
        self.assertEqual("new", out["kind"])
        self.assertNotIn("\n", out["title"])


# ── 3b. the red-main repair card is the target when it is open (DRE-6521) ────
def _replay(linear, branch="main", default_branch="main", err=None):
    return _target(branch, workflow="Pipeline Tests", linear=linear,
                   head_sha=HEAD_SHA_2026_10_09, default_branch=default_branch,
                   err=err)


class TheRepairCardIsTheTargetTest(unittest.TestCase):
    """2026-10-09: one failed Pipeline Tests run on `main` produced DRE-6511
    (the repair loop's) and DRE-6512 (the medic's). The medic looked under
    two titles and the repair card's was not one of them."""

    def test_replay_of_2026_10_09_answers_the_repair_card(self):
        linear = _Linear(repair_cards={REPAIR_TITLE_2026_10_09: "DRE-6511"})
        rc, out, _, snap = _replay(linear)
        self.assertEqual(0, rc)
        self.assertEqual(("card", "DRE-6511"), (out["kind"], out["target"]))
        self.assertEqual("DRE-6511", snap["target"])
        self.assertIn(REPAIR_TITLE_2026_10_09, linear.searched)
        # Nothing is created: the lookup only reads.
        self.assertEqual([], [q for q in linear.queries if "mutation" in q])

    def test_no_repair_card_open_is_new_as_today(self):
        linear = _Linear()
        rc, out, calls, _ = _replay(linear)
        self.assertEqual(("new", ""), (out["kind"], out["target"]))
        self.assertIn(REPAIR_TITLE_2026_10_09, linear.searched)
        self.assertIn("Pipeline failure: Pipeline Tests", linear.searched)
        self.assertEqual([], [q for q in linear.queries if "mutation" in q])

    def test_an_open_failure_card_is_still_the_failure_card(self):
        rc, out, _, _ = _replay(_Linear(open_card="DRE-4000"))
        self.assertEqual(("failure-card", "DRE-4000"), (out["kind"], out["target"]))

    def test_another_branch_makes_no_repair_card_lookup(self):
        """The same reads as before DRE-6521: one search, for the failure card."""
        linear = _Linear(repair_cards={REPAIR_TITLE_2026_10_09: "DRE-6511"})
        rc, out, calls, _ = _replay(linear, branch="release-candidate")
        self.assertEqual(("new", ""), (out["kind"], out["target"]))
        self.assertEqual(1, calls)
        self.assertEqual(["Pipeline failure: Pipeline Tests"], linear.searched)

    def test_a_repair_lookup_that_raises_is_unknown_and_warns(self):
        """`unknown` sends the delivery step to ask again; `new` would create."""
        err = io.StringIO()
        rc, out, _, _ = _replay(_Linear(fail_repair=True), err=err)
        self.assertEqual(0, rc)
        self.assertEqual(("unknown", ""), (out["kind"], out["target"]))
        self.assertIn("::warning", err.getvalue())


# ── 4. the delivery step, executed ───────────────────────────────────────────
SHIM = r"""#!/bin/bash
printf '%s\n' "$*" >> "$SHIM_LOG"
case "$*" in
  *linear_ops.py\ find-open*) printf '%s' "${FAKE_FOUND:-}"; exit "${FAKE_FIND_RC:-0}" ;;
  *medic_retry.py\ repair-card*) printf '%s' "${FAKE_REPAIR:-}"; exit "${FAKE_REPAIR_RC:-0}" ;;
  # The card's acceptance criteria are written by the real script.
  *medic_retry.py\ failure-card-body*) shift; exec "$REAL_PYTHON" "$SCRIPTS/medic_retry.py" "$@" ;;
  # Keep what `create` was handed, so the test reads the card's description.
  *linear_ops.py\ create*) cp "$4" "$CREATED" ;;
esac
exit 0
"""


def _render(text: str, values: dict) -> str:
    def sub(match):
        key = match.group(1).strip()
        if key not in values:
            raise AssertionError(f"the step reads {key!r}, which this test does not fake")
        return values[key]

    return re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", sub, text)


def _deliver(kind, target="", *, report="the report", found="", find_rc=0,
             repair="", repair_rc=0, workflow="Reconcile", branch="main",
             head_sha=HEAD_SHA_2026_10_09,
             run_url="https://github.com/o/r/actions/runs/1", created=None):
    step = _step("deliver")
    values = {
        "secrets.LINEAR_API_KEY": "test-key",
        "steps.target.outputs.kind": kind,
        "steps.target.outputs.target": target,
        "steps.target.outputs.title": f"Pipeline failure: {workflow}",
        "steps.repo.outputs.slug": "agent-bureau",
        "github.event.workflow_run.name": workflow,
        "github.event.workflow_run.html_url": run_url,
        "github.event.workflow_run.head_branch": branch,
        "github.event.workflow_run.head_sha": head_sha,
        "github.event.repository.default_branch": "main",
    }
    env = {k: _render(str(v), values) for k, v in (step.get("env") or {}).items()}
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "bin").mkdir()
        (td / "bin" / "python3").write_text(SHIM)
        os.chmod(td / "bin" / "python3", 0o755)
        (td / ".bureau-pipeline").mkdir()
        if report is not None:
            (td / REPORT).write_text(report)
        log = td / "calls.log"
        log.touch()
        made = td / "created.md"
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
            cwd=td, capture_output=True, text=True, check=False,
            env={**os.environ, **env, "PATH": f"{td / 'bin'}:{os.environ['PATH']}",
                 "SHIM_LOG": str(log), "FAKE_FOUND": found,
                 "FAKE_FIND_RC": str(find_rc), "FAKE_REPAIR": repair,
                 "FAKE_REPAIR_RC": str(repair_rc), "REAL_PYTHON": sys.executable,
                 "SCRIPTS": os.path.join(ROOT, "scripts"), "CREATED": str(made)},
        )
        if created is not None and made.exists():
            created.append(made.read_text(encoding="utf-8"))
        return proc, [ln for ln in log.read_text().splitlines() if ln]


def _creates(calls):
    return [c for c in calls if "linear_ops.py create" in c]


def _comments(calls):
    return [c for c in calls if "linear_ops.py comment" in c]


class TheDeliveryStepTest(unittest.TestCase):
    def test_a_card_on_the_branch_gets_the_act(self):
        proc, calls = _deliver("card", "DRE-5620")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(1, len(calls), calls)
        self.assertIn("linear_ops.py comment DRE-5620 the report --act=run-failure-diagnosed", calls[0])

    def test_an_open_failure_card_gets_the_report_as_a_comment(self):
        proc, calls = _deliver("failure-card", "DRE-4000")
        self.assertEqual(1, len(calls), calls)
        self.assertIn("linear_ops.py comment DRE-4000 the report", calls[0])

    def test_no_failure_card_yet_creates_one_with_the_repo_label(self):
        proc, calls = _deliver("new")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        creates = _creates(calls)
        self.assertEqual(1, len(creates), calls)
        self.assertIn("linear_ops.py create Pipeline failure: Reconcile", creates[0])
        self.assertIn("--repo agent-bureau", creates[0])
        self.assertEqual([], _comments(calls))

    def test_an_unknown_target_searches_before_creating(self):
        proc, calls = _deliver("unknown", found="DRE-4000")
        self.assertEqual(2, len(calls), calls)
        self.assertIn("linear_ops.py find-open Pipeline failure: Reconcile", calls[0])
        self.assertIn("linear_ops.py comment DRE-4000", calls[1])
        proc, calls = _deliver("unknown")
        self.assertIn("linear_ops.py create", calls[-1])

    def test_a_search_that_fails_again_creates_nothing(self):
        proc, calls = _deliver("unknown", find_rc=1)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], [c for c in calls if "create" in c or " comment " in c])
        self.assertIn("::warning", proc.stdout + proc.stderr)

    def test_no_report_posts_nothing_and_says_so(self):
        proc, calls = _deliver("card", "DRE-5620", report=None)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], calls)
        self.assertIn("::warning", proc.stdout + proc.stderr)

    def test_the_step_runs_after_a_failed_agent_too(self):
        """A report written before the agent died is still worth delivering;
        the step decides by the file, not by the agent's outcome."""
        self.assertIn("always()", _step("deliver").get("if", ""))


# ── 4b. the delivery step asks for the repair card again (DRE-6521) ──────────
class TheDeliveryStepAsksAgainTest(unittest.TestCase):
    """On 2026-10-09 the medic looked at 16:21:09, the repair loop filed
    DRE-6511 at 16:21:25, and the medic created DRE-6512 at 16:21:48 on the
    answer it had read 39 seconds earlier."""

    def test_replay_of_the_16_second_gap_comments_on_the_repair_card(self):
        proc, calls = _deliver("new", repair="DRE-6511", workflow="Pipeline Tests")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], _creates(calls))
        comments = _comments(calls)
        self.assertEqual(1, len(comments), calls)
        self.assertIn("linear_ops.py comment DRE-6511 the report "
                      "--act=run-failure-diagnosed", comments[0])

    def test_the_lookup_is_asked_with_this_run_s_workflow_commit_and_branch(self):
        proc, calls = _deliver("new", workflow="Pipeline Tests")
        asked = [c for c in calls if "medic_retry.py repair-card" in c]
        self.assertEqual(1, len(asked), calls)
        for part in ("--workflow Pipeline Tests", f"--head-sha {HEAD_SHA_2026_10_09}",
                     "--branch main", "--default-branch main"):
            self.assertIn(part, asked[0])
        # Asked immediately before the create, never after it.
        self.assertLess(calls.index(asked[0]), calls.index(_creates(calls)[0]))

    def test_an_unknown_target_asks_for_the_repair_card_before_creating(self):
        proc, calls = _deliver("unknown", repair="DRE-6511")
        self.assertEqual([], _creates(calls))
        self.assertIn("linear_ops.py comment DRE-6511", _comments(calls)[0])

    def test_a_repair_lookup_that_fails_creates_nothing(self):
        """At the target step it answers `unknown`; here, asked again, it fails
        again: no card, the warning, and the report in the run log."""
        report = "the medic's report, kept for whoever reads the log"
        for kind in ("new", "unknown"):
            proc, calls = _deliver(kind, report=report, repair_rc=1)
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertEqual([], _creates(calls), kind)
            self.assertEqual([], _comments(calls), kind)
            self.assertIn("::warning title=Diagnosis not delivered::", proc.stdout)
            self.assertIn(report, proc.stdout)

    def test_a_comment_target_never_asks(self):
        for kind, target in (("card", "DRE-5620"), ("failure-card", "DRE-4000")):
            proc, calls = _deliver(kind, target)
            self.assertEqual([], [c for c in calls if "repair-card" in c], kind)


# ── 4c. a failure card the medic creates carries acceptance criteria ─────────
REPORT_2026_10_09 = Path(ROOT, "tests", "fixtures",
                         "medic-report-DRE-6512-2026-10-09.md").read_text(encoding="utf-8")
RUN_2026_10_09 = "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38001512530"


def _created_2026_10_09(report=REPORT_2026_10_09):
    created: list[str] = []
    proc, calls = _deliver("new", report=report, workflow="Pipeline Tests",
                           run_url=RUN_2026_10_09, created=created)
    return proc, calls, created


class TheFailureCardCarriesCriteriaTest(unittest.TestCase):
    def test_the_created_card_ends_with_checkable_criteria(self):
        proc, calls, created = _created_2026_10_09()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(1, len(created), calls)
        tail = created[0][len(REPORT_2026_10_09):]
        self.assertIn("## Acceptance criteria", tail)
        items = [ln for ln in tail.splitlines() if ln.startswith("- [ ] ")]
        self.assertEqual(2, len(items), tail)
        self.assertEqual("", tail.split("## Acceptance criteria", 1)[1]
                         .replace("\n", "").replace("".join(items), ""))
        self.assertIn("`Pipeline Tests`", items[0])
        self.assertIn(RUN_2026_10_09, items[0])
        self.assertIn("`bcb36adf6037`", items[0])
        self.assertIn("`main`", items[0])
        self.assertIn("passes on the fixing pull request", items[0])
        self.assertIn("names this card and says what was wrong and what changed",
                      items[1])

    def test_planning_routes_it_on_its_criteria(self):
        """The 2026-10-09 card, as the medic would now file it, read by the
        one-off route Planning ran: no `NEEDS WORK` from the criteria rule."""
        import routing_verdict

        _, _, created = _created_2026_10_09()
        decision = routing_verdict.route(
            "Pipeline failure: Pipeline Tests", created[0],
            ["repo:bureau-pipeline"], shape="one-off")
        self.assertFalse(
            decision.source == "criteria" and decision.verdict == "NEEDS WORK",
            decision)
        # The same report with nothing appended is what was refused.
        bare = routing_verdict.route(
            "Pipeline failure: Pipeline Tests", REPORT_2026_10_09,
            ["repo:bureau-pipeline"], shape="one-off")
        self.assertEqual(("criteria", "NEEDS WORK"), (bare.source, bare.verdict))

    def test_the_report_is_unchanged_above_the_section(self):
        for report in (REPORT_2026_10_09, REPORT_2026_10_09 + "\n", "one line"):
            _, _, created = _created_2026_10_09(report)
            self.assertTrue(created[0].startswith(report), report)
            self.assertNotIn("## Acceptance criteria", created[0][:len(report)])

    def test_the_card_is_created_from_the_composed_body_not_the_bare_report(self):
        proc, calls, _ = _created_2026_10_09()
        self.assertNotIn(REPORT, _creates(calls)[0])

    def test_a_report_delivered_as_a_comment_carries_no_section(self):
        for kind, target, repair in (("card", "DRE-5620", ""),
                                     ("failure-card", "DRE-4000", ""),
                                     ("new", "", "DRE-6511")):
            proc, calls = _deliver(kind, target, repair=repair)
            self.assertEqual([], [c for c in calls if "failure-card-body" in c], kind)
            self.assertNotIn("Acceptance criteria", _comments(calls)[0])
            self.assertTrue(_comments(calls)[0].endswith(
                "the report --act=run-failure-diagnosed"), _comments(calls)[0])


class FailureCardBodyTest(unittest.TestCase):
    """The section, written by the script from the run's own facts."""

    def test_agent_influenced_names_stay_on_one_line(self):
        body = medic_retry.failure_card_body(
            "report", workflow="Tests\n- [ ] merged", branch="main`\n## x",
            head_sha=HEAD_SHA_2026_10_09, run_url=RUN_2026_10_09)
        tail = body[len("report"):]
        self.assertEqual(2, sum(1 for ln in tail.splitlines() if ln.startswith("- [ ]")))
        self.assertEqual(1, sum(1 for ln in tail.splitlines() if ln.startswith("#")))


def _repair_card_cli(branch, *, linear):
    linear_ops._reset_budget_state()
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(linear_ops.urllib.request, "urlopen", linear):
        with mock.patch.object(linear_ops.time, "sleep", lambda _s: None):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = medic_retry.main([
                    "repair-card", "--workflow", "Pipeline Tests",
                    "--head-sha", HEAD_SHA_2026_10_09, "--branch", branch,
                    "--default-branch", "main",
                ])
    calls = linear_ops.requests_made()
    linear_ops._reset_budget_state()
    return rc, out.getvalue(), err.getvalue(), calls


class RepairCardCommandTest(unittest.TestCase):
    """What the delivery step runs immediately before `create`."""

    def test_an_open_repair_card_is_printed(self):
        linear = _Linear(repair_cards={REPAIR_TITLE_2026_10_09: "DRE-6511"})
        rc, out, _, _ = _repair_card_cli("main", linear=linear)
        self.assertEqual((0, "DRE-6511"), (rc, out.strip()))

    def test_none_open_prints_nothing(self):
        rc, out, _, _ = _repair_card_cli("main", linear=_Linear())
        self.assertEqual((0, ""), (rc, out.strip()))

    def test_another_branch_prints_nothing_and_reads_nothing(self):
        linear = _Linear(repair_cards={REPAIR_TITLE_2026_10_09: "DRE-6511"})
        rc, out, _, calls = _repair_card_cli("agent/DRE-1-x", linear=linear)
        self.assertEqual((0, "", 0), (rc, out.strip(), calls))

    def test_a_lookup_that_raises_exits_non_zero(self):
        rc, out, err, _ = _repair_card_cli("main", linear=_Linear(fail_repair=True))
        self.assertNotEqual(0, rc)
        self.assertEqual("", out.strip())
        self.assertIn("::warning", err)


if __name__ == "__main__":
    unittest.main()
