"""Shadow Replay: a batch of side-by-side replays, and how often each agreed (DRE-7197).

The critic (DRE-7193, `qa-review.yml`) and the verifier (DRE-7194,
`verify.yml`) each grew a shadow mode: replay ONE past pull request at the
head its recorded verdict was bound to, on a model the caller names, and
write the verdict to an artifact, posting nothing. `shadow-replay.yml` is the
batch: it picks the past reviews, replays each through that shadow mode, and
compares the replayed verdicts with the recorded ones. The noise-floor
reading is taken with it, and the four model-switch epics (DRE-7185,
DRE-7187, DRE-7188, DRE-7189) read their bars with it afterwards.

What this file pins:

* the workflow: `workflow_dispatch` only, its five inputs, the two reusables
  at `@main` with `shadow: true` and the replay inputs, a `permissions:`
  block of exactly four grants, and a concurrency group that never names a
  pull request;
* that neither the medic nor red-main repair watches it — a red replay is
  read by the operator from its report, and an automatic retry would spend
  another batch of reviewer runs — and that the watcher check declares that
  exemption rather than tripping on it;
* `pick`, against fixture Linear comments and a fake `gh` on PATH, and with
  `prs`, against fixture pull request comments;
* `compare`, over fixture artifacts: its lines, its counts and its reports;
* the three log lines `config/proof-reach.json` declares for a proof run.

Run: python3 -m pytest tests/test_shadow_replay.py -v
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "shadow-replay.yml"

sys.path.insert(0, str(ROOT / "scripts"))

import check_workflow_watchers as cww  # noqa: E402
import merge_gate  # noqa: E402
import proof_reach  # noqa: E402

REPO = "dreadnought-foundry/bureau-pipeline"
QA = "agent-bureau-qa-bot[bot]"
# Opaque to the code under test, and on no ladder, so no console save is
# pinned here (DRE-6866).
MODEL = "replay-model"
OTHER_MODEL = "recorded-model"


def _sr():
    import shadow_replay  # noqa: PLC0415 — the module under test
    return shadow_replay


def _sha(n: int) -> str:
    return f"{n:040x}"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _on(doc: dict) -> dict:
    return cww.on_block(doc)


# --------------------------------------------------------------------------- #
# Fixtures — the comment shapes the critic and post_verdict.sh really write     #
# --------------------------------------------------------------------------- #

def critic_card_copy(token: str, sha: str, model: str = MODEL) -> str:
    """The card's copy `post_verdict.sh` writes for a real verdict: the pull
    request's comment, then the model line."""
    return (f"🔎 QA Critic — VERDICT: {token} @{sha}\n\nfindings…\n\n"
            f"🧠 model: {model} — selected for the critic\n")


INFRA_NOTICE = (
    "🔎 QA Critic could not run (infra error) — re-review needed, this is NOT a "
    "code rejection.\n\nThe adversarial reviewer crashed twice.\n\n"
    f"🧠 model: {MODEL} — selected for the critic\n")

EVIDENCE_HOLD = (
    "🔎 QA Critic produced a verdict that asserts a result it did not show — "
    "re-review needed, this is not a code rejection.\n\nWhat was missing:\n\n"
    f"- a claim\n\n🧠 model: {MODEL} — selected for the critic\n")


def node(body: str, created: str, *, label: str = "repo:bureau-pipeline",
         card: str = "DRE-1") -> dict:
    return {"createdAt": created, "body": body,
            "issue": {"identifier": card, "labels": {"nodes": [{"name": label}]}}}


FAKE_GH = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, re, sys
    fx = json.load(open(os.environ["FAKE_GH_FIXTURE"]))
    with open(os.environ["FAKE_GH_LOG"], "a") as log:
        log.write(" ".join(sys.argv[1:]) + "\\n")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args or args[0] != "api":
        sys.exit("fake gh: only `gh api` is faked")
    path = args[1]
    m = re.fullmatch(r"repos/[^/]+/[^/]+/commits/([0-9a-f]{40})/pulls", path)
    if m:
        sha = m.group(1)
        if sha not in fx["commits"]:
            sys.stderr.write("gh: No commit found for SHA: %s (HTTP 422)\\n" % sha)
            sys.exit(1)
        print(json.dumps([{"number": n} for n in fx["commits"][sha]]))
        sys.exit(0)
    m = re.fullmatch(r"repos/[^/]+/[^/]+/commits/([0-9a-f]{40})", path)
    if m:
        if m.group(1) not in fx["commits"]:
            sys.stderr.write("gh: No commit found for SHA (HTTP 422)\\n")
            sys.exit(1)
        print(json.dumps({"sha": m.group(1)}))
        sys.exit(0)
    m = re.fullmatch(r"repos/[^/]+/[^/]+/issues/(\\d+)/comments", path)
    if m:
        print(json.dumps([fx["comments"].get(m.group(1), [])]))
        sys.exit(0)
    m = re.fullmatch(r"repos/[^/]+/[^/]+/pulls/(\\d+)", path)
    if m:
        print(json.dumps({"number": int(m.group(1)),
                          "head": {"sha": fx["heads"][m.group(1)]}}))
        sys.exit(0)
    sys.exit("fake gh: unexpected call " + path)
    """)


class FakeGh:
    """A `gh` on PATH that answers from a fixture and logs every call."""

    def __init__(self, *, commits=None, comments=None, heads=None):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        gh = d / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)
        self.fixture = d / "fixture.json"
        self.fixture.write_text(json.dumps({
            "commits": commits or {}, "comments": comments or {},
            "heads": heads or {}}), encoding="utf-8")
        self.log = d / "calls.log"
        self.log.write_text("", encoding="utf-8")
        self.env = {"PATH": f"{d}{os.pathsep}{os.environ.get('PATH', '')}",
                    "FAKE_GH_FIXTURE": str(self.fixture),
                    "FAKE_GH_LOG": str(self.log)}

    def __enter__(self):
        self._patch = mock.patch.dict(os.environ, self.env)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        self.tmp.cleanup()

    def calls(self) -> list:
        return [ln for ln in self.log.read_text(encoding="utf-8").splitlines() if ln]


# --------------------------------------------------------------------------- #
# 1. The workflow                                                              #
# --------------------------------------------------------------------------- #

class WorkflowShapeTest(unittest.TestCase):
    def setUp(self):
        self.doc = _load(WORKFLOW)
        self.jobs = self.doc.get("jobs") or {}

    def test_named_and_dispatch_only(self):
        self.assertEqual("Shadow Replay", self.doc["name"])
        self.assertEqual(["workflow_dispatch"], list(_on(self.doc)))

    def test_the_five_inputs(self):
        inputs = _on(self.doc)["workflow_dispatch"]["inputs"]
        self.assertEqual(["reviewer", "model", "recorded_model", "count", "prs"],
                         list(inputs))
        self.assertEqual("critic", inputs["reviewer"]["default"])
        self.assertEqual(["critic", "verifier"], inputs["reviewer"]["options"])
        self.assertIs(True, inputs["model"]["required"])
        self.assertEqual("", inputs["recorded_model"]["default"])
        self.assertEqual("20", inputs["count"]["default"])
        self.assertEqual("", inputs["prs"]["default"])

    def test_permissions_are_exactly_the_four_grants(self):
        """A reusable can hold no more than its caller grants, and a grant
        left out is `none`: without `contents: read` every replay would fail
        at its first checkout."""
        self.assertEqual(
            {"id-token": "write", "contents": "read", "pull-requests": "read",
             "actions": "read"},
            self.doc["permissions"])
        for name, job in self.jobs.items():
            self.assertNotIn("permissions", job,
                             f"job {name} must not widen or narrow the four grants")

    def test_concurrency_is_its_own_and_never_a_pull_requests(self):
        group = self.doc["concurrency"]["group"]
        self.assertEqual("shadow-replay-${{ github.run_id }}", group)
        self.assertNotIn("pull_request", group)
        self.assertNotIn("pr_number", group)
        self.assertNotIn("qa-review", group)
        self.assertIs(False, self.doc["concurrency"].get("cancel-in-progress", False))

    def test_pick_outputs_the_matrix(self):
        pick = self.jobs["pick"]
        self.assertIn("matrix", pick["outputs"])
        runs = "\n".join(s.get("run", "") for s in pick["steps"])
        self.assertIn("python3 scripts/shadow_replay.py pick", runs)

    def _replay_jobs(self) -> dict:
        return {name: job for name, job in self.jobs.items() if "uses" in job}

    def test_replay_calls_each_reusable_at_main_in_shadow_mode(self):
        calls = self._replay_jobs()
        by_target = {job["uses"]: (name, job) for name, job in calls.items()}
        base = "dreadnought-foundry/bureau-pipeline/.github/workflows/"
        self.assertEqual({base + "qa-review.yml@main", base + "verify.yml@main"},
                         set(by_target))
        for target, reviewer in (("qa-review.yml@main", "critic"),
                                 ("verify.yml@main", "verifier")):
            name, job = by_target[base + target]
            self.assertIn("pick", job["needs"] if isinstance(job["needs"], list)
                          else [job["needs"]])
            self.assertIn(f"inputs.reviewer == '{reviewer}'", job["if"])
            self.assertEqual(4, job["strategy"]["max-parallel"], name)
            self.assertIs(False, job["strategy"].get("fail-fast", True),
                          "one red replay must not cancel the rest of the batch")
            self.assertIn("needs.pick.outputs.matrix", str(job["strategy"]["matrix"]))
            w = job["with"]
            self.assertEqual("main", w["pipeline_ref"])
            self.assertIs(True, w["shadow"])
            self.assertEqual("${{ inputs.model }}", w["model"])
            self.assertIn("matrix.", w["replay_pr"])
            self.assertIn(".pr", w["replay_pr"])
            self.assertIn("matrix.", w["replay_sha"])
            self.assertIn(".sha", w["replay_sha"])
            self.assertEqual("inherit", job["secrets"])

    def test_compare_always_runs_and_publishes_the_report(self):
        compare = self.jobs["compare"]
        self.assertIn("always()", compare["if"])
        needs = compare["needs"]
        for name in ["pick", *self._replay_jobs()]:
            self.assertIn(name, needs)
        steps = compare["steps"]
        runs = "\n".join(s.get("run", "") for s in steps)
        self.assertIn("python3 scripts/shadow_replay.py compare", runs)
        self.assertIn("grep", runs)
        self.assertIn("shadow-replay:", runs)
        downloads = [s for s in steps if "download-artifact" in s.get("uses", "")]
        patterns = " ".join(str((s.get("with") or {}).get("pattern", "")) for s in downloads)
        self.assertIn("shadow-verdict-${{ inputs.reviewer }}-*", patterns)
        uploads = [s for s in steps if "upload-artifact" in s.get("uses", "")]
        self.assertEqual(["shadow-replay-report"], [s["with"]["name"] for s in uploads])
        self.assertIn("shadow-replay-report.md", uploads[0]["with"]["path"])
        self.assertIn("shadow-replay-report.json", uploads[0]["with"]["path"])

    def test_nothing_is_posted(self):
        """It posts nothing anywhere: no `gh pr comment`, no Linear write."""
        text = WORKFLOW.read_text(encoding="utf-8")
        for verb in ("gh pr comment", "gh pr review", "linear_ops.py comment",
                     "gh issue comment", "gh pr edit"):
            self.assertNotIn(verb, text)


class NotWatchedTest(unittest.TestCase):
    """A red replay is read by the operator from its report, and an automatic
    retry would spend another batch of reviewer runs — so neither the medic
    nor red-main repair watches Shadow Replay."""

    def _watched(self, stub: str) -> list:
        return cww.watched(_on(_load(WORKFLOWS / stub)))

    def test_absent_from_the_medic_and_red_main_repair(self):
        self.assertNotIn("Shadow Replay", self._watched("self-medic.yml"))
        self.assertNotIn("Shadow Replay", self._watched("self-red-main-repair.yml"))

    def test_the_watcher_check_declares_the_exemption_with_its_reason(self):
        self.assertIn("Shadow Replay", cww.NOT_WATCHED)
        reason = cww.NOT_WATCHED["Shadow Replay"]
        self.assertIn("report", reason)
        self.assertIn("batch", reason)
        violations, _ = cww.check_dir()
        self.assertEqual([], [v for v in violations if "Shadow Replay" in v])

    def test_the_exemption_is_not_a_blanket_one(self):
        """An unwatched workflow under any other name still fails the check."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name in ("Shadow Replay", "Something Else"):
                (d / f"{name.lower().replace(' ', '-')}.yml").write_text(
                    f"name: {name}\non:\n  workflow_dispatch:\njobs: {{}}\n",
                    encoding="utf-8")
            violations, _ = cww.check_dir(d)
        self.assertEqual(1, len(violations))
        self.assertIn("Something Else", violations[0])


# --------------------------------------------------------------------------- #
# 2. pick — the Linear path                                                    #
# --------------------------------------------------------------------------- #

class PickRecordedModelTest(unittest.TestCase):
    """Newest first: PR 105 is the newest real review; the two notices are
    newer still and must neither count nor fill a place."""

    def setUp(self):
        self.comments = [
            node(critic_card_copy("APPROVE", _sha(1)), "2026-10-01T00:00:00Z"),
            node(critic_card_copy("REQUEST_CHANGES", _sha(2)), "2026-10-02T00:00:00Z"),
            # PR 103 reviewed twice: the older review must lose.
            node(critic_card_copy("REQUEST_CHANGES", _sha(3)), "2026-10-03T00:00:00Z"),
            node(critic_card_copy("APPROVE", _sha(4)), "2026-10-04T00:00:00Z"),
            # On another model: skipped.
            node(critic_card_copy("APPROVE", _sha(9), OTHER_MODEL), "2026-10-05T00:00:00Z"),
            # A model whose id merely starts with the named one: skipped.
            node(critic_card_copy("APPROVE", _sha(9), MODEL + "-preview"),
                 "2026-10-05T01:00:00Z"),
            # A sha the repository no longer has: dropped.
            node(critic_card_copy("APPROVE", _sha(66)), "2026-10-06T00:00:00Z"),
            node(critic_card_copy("REQUEST_CHANGES", _sha(5)), "2026-10-07T00:00:00Z"),
            # On a card for another repository: skipped.
            node(critic_card_copy("APPROVE", _sha(8)), "2026-10-07T12:00:00Z",
                 label="repo:agent-bureau"),
            node(INFRA_NOTICE, "2026-10-08T00:00:00Z"),
            node(EVIDENCE_HOLD, "2026-10-09T00:00:00Z"),
        ]
        self.commits = {_sha(1): [101], _sha(2): [102], _sha(3): [103],
                        _sha(4): [103], _sha(5): [105], _sha(8): [108], _sha(9): [109]}

    def _pick(self, count: int):
        return _sr().pick_recorded_model(self.comments, recorded_model=MODEL,
                                         count=count, repo=REPO)

    def test_newest_count_one_per_pull_request_on_the_named_model(self):
        with FakeGh(commits=self.commits):
            got = self._pick(3)
        self.assertEqual(
            [{"pr": 105, "sha": _sha(5), "recorded": "REQUEST_CHANGES"},
             {"pr": 103, "sha": _sha(4), "recorded": "APPROVE"},
             {"pr": 102, "sha": _sha(2), "recorded": "REQUEST_CHANGES"}],
            _sr().matrix(got))
        self.assertEqual(f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{_sha(5)}",
                         got[0]["recorded_line"])

    def test_the_notices_are_never_counted(self):
        """Four eligible reviews (101, 102, 103, 105). The two notices carry
        the marker and the model line; counted, they would make six."""
        with FakeGh(commits=self.commits):
            got = self._pick(4)
            self.assertEqual([105, 103, 102, 101], [r["pr"] for r in got])
            with self.assertRaises(_sr().PickError) as caught:
                self._pick(5)
        self.assertIn("found 4", str(caught.exception))
        self.assertIn("5", str(caught.exception))

    def test_a_gone_sha_is_dropped_and_never_looked_up_as_a_pull_request(self):
        with FakeGh(commits=self.commits) as gh:
            got = self._pick(4)
            calls = gh.calls()
        self.assertNotIn(_sha(66), [r["sha"] for r in got])
        self.assertTrue(any(_sha(66) in c for c in calls), "the gone sha is checked")
        self.assertFalse(any(f"commits/{_sha(66)}/pulls" in c for c in calls))

    def test_the_pull_request_is_found_through_the_commits_pulls_api(self):
        with FakeGh(commits=self.commits) as gh:
            self._pick(1)
            calls = gh.calls()
        self.assertIn(f"api repos/{REPO}/commits/{_sha(5)}/pulls", calls)

    def test_a_comment_without_a_verdict_and_sha_is_skipped_by_the_merge_gate_reading(self):
        line = merge_gate.first_line(INFRA_NOTICE)
        self.assertTrue(merge_gate.opens_with_marker(INFRA_NOTICE, merge_gate.CRITIC_MARKER))
        self.assertIsNone(merge_gate.verdict_token(line, merge_gate.CRITIC_MARKER))
        self.assertIsNone(_sr().recorded_verdict(INFRA_NOTICE, merge_gate.CRITIC_MARKER))
        self.assertIsNone(_sr().recorded_verdict(EVIDENCE_HOLD, merge_gate.CRITIC_MARKER))
        self.assertEqual(("APPROVE", _sha(1)), _sr().recorded_verdict(
            critic_card_copy("APPROVE", _sha(1)), merge_gate.CRITIC_MARKER)[:2])

    def test_the_linear_read_asks_for_the_repo_label_and_the_model_line(self):
        seen = {}

        def fake_paged(query, variables=None, *, connection="issues"):
            seen.update(query=query, variables=variables, connection=connection)
            return []

        with mock.patch.object(_sr().linear_ops, "gql_paged", fake_paged):
            self.assertEqual([], _sr().linear_critic_comments(MODEL, "repo:bureau-pipeline"))
        self.assertEqual("comments", seen["connection"])
        self.assertIn("repo:bureau-pipeline", json.dumps(seen["variables"]))
        self.assertIn(f"🧠 model: {MODEL}", json.dumps(seen["variables"], ensure_ascii=False))
        self.assertIn("$after", seen["query"])


# --------------------------------------------------------------------------- #
# 3. pick — the `prs` path                                                     #
# --------------------------------------------------------------------------- #

def pr_comment(body: str, login: str = QA) -> dict:
    return {"user": {"login": login}, "body": body}


class PickPrsTest(unittest.TestCase):
    def setUp(self):
        self.comments = {
            "201": [
                pr_comment(f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{_sha(21)}\n\nx"),
                pr_comment(f"🔎 QA Critic — VERDICT: APPROVE @{_sha(22)}\n\nx"),
                # Newer than the last real verdict: passed over.
                pr_comment("🔎 QA Critic could not run (infra error) — re-review "
                           "needed, this is NOT a code rejection.\n\nx"),
                # Forged by somebody else: invisible.
                pr_comment(f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{_sha(29)}",
                           login="someone"),
            ],
            "202": [pr_comment("a person's comment", login="someone")],
        }
        self.heads = {"201": _sha(23), "202": _sha(24)}

    def test_newest_real_verdict_passing_over_a_newer_notice(self):
        with FakeGh(comments=self.comments, heads=self.heads):
            got = _sr().pick_prs([201, 202], reviewer="critic", repo=REPO)
        self.assertEqual(
            [{"pr": 201, "sha": _sha(22), "recorded": "APPROVE"},
             {"pr": 202, "sha": _sha(24), "recorded": "none"}],
            _sr().matrix(got))
        self.assertEqual("", got[1]["recorded_line"])

    def test_latest_verdict_comment_alone_would_pick_the_notice(self):
        """Why `pick` does not stop at the merge gate's own reader."""
        latest = merge_gate.latest_verdict_comment(
            self.comments["201"], QA, merge_gate.CRITIC_MARKER)
        self.assertIn("could not run", latest)

    def test_the_verifier_reads_the_verifiers_marker(self):
        comments = {"301": [
            pr_comment(f"🔎 QA Critic — VERDICT: APPROVE @{_sha(31)}"),
            pr_comment(f"🧪 QA Verifier — VERDICT: FAIL @{_sha(32)}"),
        ], "302": [pr_comment(f"🔎 QA Critic — VERDICT: APPROVE @{_sha(33)}")]}
        with FakeGh(comments=comments, heads={"301": _sha(34), "302": _sha(35)}):
            got = _sr().pick_prs([301, 302], reviewer="verifier", repo=REPO)
        self.assertEqual(
            [{"pr": 301, "sha": _sha(32), "recorded": "FAIL"},
             {"pr": 302, "sha": _sha(35), "recorded": "none"}],
            _sr().matrix(got))

    def test_parse_prs(self):
        self.assertEqual([12, 7, 30], _sr().parse_prs(" 12, 7 ,30,"))
        self.assertEqual([], _sr().parse_prs(""))
        with self.assertRaises(_sr().PickError):
            _sr().parse_prs("12, abc")


class PickCliTest(unittest.TestCase):
    def _main(self, *argv) -> tuple:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = _sr().main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_prs_writes_the_matrix_output_and_the_pick_file(self):
        comments = {"201": [pr_comment(f"🔎 QA Critic — VERDICT: APPROVE @{_sha(22)}")]}
        with tempfile.TemporaryDirectory() as tmp, \
                FakeGh(comments=comments, heads={"201": _sha(23), "202": _sha(24)}):
            gh_out = Path(tmp) / "out"
            pick = Path(tmp) / "pick.json"
            code, _, _ = self._main(
                "pick", "--reviewer", "critic", "--recorded-model", "", "--count", "20",
                "--prs", "201,202", "--repo", REPO, "--out", str(pick),
                "--github-output", str(gh_out))
            self.assertEqual(0, code)
            line = gh_out.read_text(encoding="utf-8").strip()
            self.assertTrue(line.startswith("matrix="))
            self.assertEqual(
                [{"pr": 201, "sha": _sha(22), "recorded": "APPROVE"},
                 {"pr": 202, "sha": _sha(24), "recorded": "none"}],
                json.loads(line[len("matrix="):]))
            self.assertEqual(2, len(json.loads(pick.read_text(encoding="utf-8"))))

    def test_short_batch_fails_naming_the_number_found(self):
        comments = [node(critic_card_copy("APPROVE", _sha(1)), "2026-10-01T00:00:00Z")]
        with tempfile.TemporaryDirectory() as tmp, FakeGh(commits={_sha(1): [101]}), \
                mock.patch.object(_sr().linear_ops, "gql_paged", lambda *a, **k: comments):
            code, out, err = self._main(
                "pick", "--reviewer", "critic", "--recorded-model", MODEL,
                "--count", "20", "--prs", "", "--repo", REPO,
                "--out", str(Path(tmp) / "pick.json"))
        self.assertEqual(1, code)
        self.assertIn("found 1", out + err)
        self.assertIn("20", out + err)

    def test_nothing_to_pick_from_is_refused(self):
        """No `prs` and no `recorded_model` names no batch; the verifier has
        no recorded-model pick at all."""
        for reviewer, recorded in (("critic", ""), ("verifier", MODEL)):
            with tempfile.TemporaryDirectory() as tmp:
                code, out, err = self._main(
                    "pick", "--reviewer", reviewer, "--recorded-model", recorded,
                    "--count", "20", "--prs", "", "--repo", REPO,
                    "--out", str(Path(tmp) / "pick.json"))
            self.assertEqual(1, code, reviewer)
            self.assertIn("prs", out + err)


# --------------------------------------------------------------------------- #
# 4. compare                                                                   #
# --------------------------------------------------------------------------- #

def write_artifact(root: Path, role: str, pr: int, sha: str, verdict, md: str) -> None:
    d = root / f"shadow-verdict-{role}-{pr}"
    d.mkdir(parents=True)
    (d / "shadow-verdict.json").write_text(json.dumps({
        "role": role, "repo": REPO, "pr": pr, "sha": sha, "model": MODEL,
        "verdict": verdict, "real": verdict is not None,
        "run_url": f"https://github.com/{REPO}/actions/runs/{pr}"}), encoding="utf-8")
    (d / "shadow-verdict.md").write_text(md, encoding="utf-8")


class CompareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.arts = root / "artifacts"
        self.out = root / "report"
        self.records = [
            {"pr": 1, "sha": _sha(1), "recorded": "APPROVE",
             "recorded_line": f"🔎 QA Critic — VERDICT: APPROVE @{_sha(1)}"},
            {"pr": 2, "sha": _sha(2), "recorded": "APPROVE",
             "recorded_line": f"🔎 QA Critic — VERDICT: APPROVE @{_sha(2)}"},
            {"pr": 3, "sha": _sha(3), "recorded": "REQUEST_CHANGES",
             "recorded_line": f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{_sha(3)}"},
            {"pr": 4, "sha": _sha(4), "recorded": "APPROVE",
             "recorded_line": f"🔎 QA Critic — VERDICT: APPROVE @{_sha(4)}"},
            {"pr": 5, "sha": _sha(5), "recorded": "none", "recorded_line": ""},
        ]
        write_artifact(self.arts, "critic", 1, _sha(1), "APPROVE", "VERDICT: APPROVE\n\nok\n")
        write_artifact(self.arts, "critic", 2, _sha(2), "REQUEST_CHANGES",
                       "VERDICT: REQUEST_CHANGES\n\nbad\n")
        # PR 3: no artifact at all.
        write_artifact(self.arts, "critic", 4, _sha(4), None, "")
        write_artifact(self.arts, "critic", 5, _sha(5), "APPROVE", "VERDICT: APPROVE\n")
        # Another role's artifact is never read as this role's.
        write_artifact(self.arts, "verifier", 3, _sha(3), "PASS", "VERDICT: PASS\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, recorded_model=OTHER_MODEL) -> list:
        pick = Path(self.tmp.name) / "pick.json"
        pick.write_text(json.dumps(self.records), encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            code = _sr().main([
                "compare", "--reviewer", "critic", "--model", MODEL,
                "--recorded-model", recorded_model, "--pick", str(pick),
                "--artifacts", str(self.arts), "--out-dir", str(self.out)])
        self.assertEqual(0, code)
        return [ln for ln in out.getvalue().splitlines() if ln.startswith("shadow-replay:")]

    def test_the_lines_exactly_as_the_contract_states(self):
        self.assertEqual([
            f"shadow-replay: pr=1 sha={_sha(1)} recorded=APPROVE replayed=APPROVE match=yes",
            f"shadow-replay: pr=2 sha={_sha(2)} recorded=APPROVE replayed=REQUEST_CHANGES match=no",
            f"shadow-replay: pr=3 sha={_sha(3)} recorded=REQUEST_CHANGES replayed=none match=no",
            f"shadow-replay: pr=4 sha={_sha(4)} recorded=APPROVE replayed=none match=no",
            f"shadow-replay: pr=5 sha={_sha(5)} recorded=none replayed=APPROVE match=n/a",
            f"shadow-replay: matched 1 of 4 (25%) reviewer=critic model={MODEL} "
            f"recorded_model={OTHER_MODEL}",
        ], self._run())

    def test_an_empty_recorded_model_is_none(self):
        self.assertTrue(self._run(recorded_model="")[-1].endswith("recorded_model=none"))

    def test_the_percentage_rounds_down(self):
        self.assertEqual(
            f"shadow-replay: matched 2 of 3 (66%) reviewer=verifier model={MODEL} "
            "recorded_model=none",
            _sr().summary_line(2, 3, reviewer="verifier", model=MODEL, recorded_model=""))
        self.assertIn("matched 0 of 0 (0%)", _sr().summary_line(
            0, 0, reviewer="critic", model=MODEL, recorded_model=""))

    def test_the_report_files_list_the_mismatches(self):
        self._run()
        report = json.loads((self.out / "shadow-replay-report.json").read_text(encoding="utf-8"))
        self.assertEqual((1, 4, 25), (report["matched"], report["of"], report["pct"]))
        self.assertEqual([1, 2, 3, 4, 5], [r["pr"] for r in report["replays"]])
        kinds = {m["pr"]: m["kind"] for m in report["mismatches"]}
        self.assertEqual({2: "disagreement", 3: "no verdict", 4: "no verdict"}, kinds)
        two = next(m for m in report["mismatches"] if m["pr"] == 2)
        self.assertEqual(f"🔎 QA Critic — VERDICT: APPROVE @{_sha(2)}", two["recorded_line"])
        self.assertEqual("VERDICT: REQUEST_CHANGES", two["replayed_line"])

        md = (self.out / "shadow-replay-report.md").read_text(encoding="utf-8")
        self.assertIn("matched 1 of 4 (25%)", md)
        for pr in (1, 2, 3, 4, 5):
            self.assertIn(f"| {pr} |", md)
        self.assertIn("## Mismatches", md)
        mismatches = md.split("## Mismatches", 1)[1]
        self.assertIn("VERDICT: REQUEST_CHANGES", mismatches)
        self.assertIn(f"VERDICT: APPROVE @{_sha(2)}", mismatches)
        self.assertIn("no verdict", mismatches)
        self.assertIn("disagreement", mismatches)
        self.assertNotIn("pr 5", mismatches.lower())


# --------------------------------------------------------------------------- #
# 5. The proof-reach declarations                                              #
# --------------------------------------------------------------------------- #

class ProofReachTest(unittest.TestCase):
    def test_the_three_log_lines_are_declared(self):
        tags = {e["tag"]: e for e in proof_reach.load()["log_lines"]}
        self.assertEqual((".github/workflows/qa-review.yml", None),
                         (tags["shadow-verdict:"]["printed_by"],
                          tags["shadow-verdict:"]["constant"]))
        self.assertIn("verify.yml", tags["shadow-verdict:"]["when"])
        self.assertEqual(("scripts/shadow_replay.py", "TAG"),
                         (tags["shadow-replay:"]["printed_by"],
                          tags["shadow-replay:"]["constant"]))
        self.assertEqual(("scripts/classifier_cost.py", "TAG"),
                         (tags["classifier-cost:"]["printed_by"],
                          tags["classifier-cost:"]["constant"]))
        self.assertEqual("shadow-replay:", _sr().TAG)

    def test_the_verifier_prints_the_shadow_verdict_line_too(self):
        self.assertIn("shadow-verdict:", (WORKFLOWS / "verify.yml").read_text(encoding="utf-8"))

    def test_the_check_passes(self):
        self.assertEqual([], proof_reach.check(ROOT))


if __name__ == "__main__":
    unittest.main()
