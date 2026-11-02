"""Replay of 2026-11-01: two reviews behind the Claude limit come back by
themselves once a review succeeds, and stay named on the console when they
cannot (DRE-8690).

What happened. The fleet's Claude account hit its monthly spend limit at 11:34
AM PT. The medic marked both dead reviews at 13:09 and 13:10 PT —
`🪦 limit-death: kind=claude stage=review reset=2026-11-02T01:00:00Z run=<id>`
on DRE-8667 (agent-bureau PR #4462) and DRE-8553 (PR #4472). The credential
was rotated at 13:08, and PR #4470's review succeeded at 13:25. At 13:18 the
sweep, finding both heads at their retry cap with no environment evidence,
posted `🚨 reviewer-down` on both cards — and `🚨` closes a limit marker, so
limit recovery stopped watching them. Two hours later the operator re-ran
both reviews by hand.

The passes below are the two phases of a full sweep that touch this, run in
the order `main()` runs them — `recover_limit_deaths`, then
`recover_crashed_reviews` — over one world that carries each pass's writes
into the next:

  (a) before any verdict: nothing posted, nothing raised, both markers live;
  (b) after PR #4470's verdict lands in the listing: both runs re-run with
      `--failed`, both cards receipted with `🔁 limit-recovery:`;
  (c) the cards as they actually stood on 11-01, a `🚨 reviewer-down` after
      each marker: nothing re-run, no second `🚨`, both alerts raised;
  (d) the re-runs of (b) died on the wall again, leaving no new marker:
      `🚨 reviewer-down` posted and both alerts raised.

Every network seam is stubbed — GitHub through `subprocess.run`, Linear
through `linear_ops`, the console through `pipeline_notice` and
`bureau_read` — and an unexpected `gh` call fails the pass.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import limit_recovery  # noqa: E402
import reconcile  # noqa: E402

REPO = "dreadnought-foundry/agent-bureau"
#: 14:31 PT on 11-01: after the rotation and the verdict, before the reset.
NOW = datetime(2026, 11, 1, 21, 31, tzinfo=UTC)
VERDICT_AT = "2026-11-01T21:25:19Z"   # PR #4470, 13:25 PT

STRANDED = {
    4472: {"card": "DRE-8553", "head": "1cd61cfb49817d1bb420487e8434e76ed6389275",
           "run": "41349671195", "marked": "2026-11-01T20:10:00.000Z",
           "branch": "agent/DRE-8553-review-wall"},
    4462: {"card": "DRE-8667", "head": "887e486b17fdef87ed3f4d8d312ce6e41fd4fa9d",
           "run": "41346471815", "marked": "2026-11-01T20:09:00.000Z",
           "branch": "agent/DRE-8667-review-wall"},
}
VERDICT_HEAD = "4470" + "0" * 36


class _Frozen(datetime):
    """`reconcile.datetime`, with `now` pinned to NOW."""

    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)


def marker(number: int) -> str:
    return (f"🪦 limit-death: kind=claude stage=review reset=2026-11-02T01:00:00Z "
            f"run={STRANDED[number]['run']}")


def reviewer_down(number: int) -> str:
    """The 13:18 PT report, as the sweep posted it."""
    head = STRANDED[number]["head"]
    return (f"🚨 {reconcile.REVIEWER_DOWN_TAG} PR #{number} @{head}: the adversarial "
            f"reviewer is DOWN for open PR #{number}.")


class World:
    """GitHub, Linear and the console as the 11-01 sweeps saw them."""

    def __init__(self, *, extra_bodies=None):
        self.verdict = False
        self.live_runs: set[str] = set()          # re-run, not yet concluded
        self.reruns: list[str] = []
        self.gh_calls: list[tuple] = []
        self.posted: list[tuple[str, str]] = []
        self.raised: list[dict] = []
        self.resolved: list[str] = []
        self.alerts: dict[str, dict] = {}
        self.comments: dict[str, list[dict]] = {}
        for number, pr in STRANDED.items():
            nodes = [{"body": marker(number), "createdAt": pr["marked"]}]
            for body in (extra_bodies or {}).get(number, ()):
                nodes.append({"body": body, "createdAt": "2026-11-01T20:18:00.000Z"})
            self.comments[pr["card"]] = nodes

    # ── GitHub ──────────────────────────────────────────────────────────────
    def prs(self) -> list[dict]:
        listing = []
        for number, pr in STRANDED.items():
            listing.append({
                "number": number, "headRefName": pr["branch"], "headRefOid": pr["head"],
                "baseRefName": "main", "mergeStateStatus": "CLEAN", "isDraft": False,
                "comments": [{
                    "author": {"login": reconcile.WORKER_BOT_LOGIN},
                    "createdAt": "2026-11-01T20:12:00Z",
                    "body": (f"🔁 {reconcile.CRASHED_REVIEW_DISPATCH_TAG} @{pr['head']}: "
                             "the review run for this head crashed"),
                }],
            })
        verdicts = [{"author": {"login": reconcile.QA_BOT_LOGIN}, "createdAt": VERDICT_AT,
                     "body": f"🔎 QA Critic — VERDICT: APPROVE @{VERDICT_HEAD}"}]
        listing.append({
            "number": 4470, "headRefName": "agent/DRE-8600-succeeded",
            "headRefOid": VERDICT_HEAD, "baseRefName": "main",
            "mergeStateStatus": "CLEAN", "isDraft": False,
            "comments": verdicts if self.verdict else [],
        })
        return listing

    def _by_head(self, sha: str) -> dict | None:
        return next((pr for pr in STRANDED.values() if pr["head"] == sha), None)

    def run(self, argv, **kwargs):
        assert argv[0] == "gh", argv
        args = tuple(argv[1:])
        self.gh_calls.append(args)

        def ok(out=""):
            return SimpleNamespace(returncode=0, stdout=out, stderr="")

        if args[:2] == ("pr", "list"):
            return ok(json.dumps(self.prs()))
        if args[0] == "api" and any("/check-runs" in a for a in args):
            sha = next(a for a in args if "/check-runs" in a).split("/commits/")[1].split("/")[0]
            pr = self._by_head(sha)
            if pr is None:
                runs = [{"status": "completed", "conclusion": "success",
                         "name": "call / review"}]
            elif pr["run"] in self.live_runs:
                runs = [{"status": "in_progress", "conclusion": None,
                         "name": "call / review"}]
            else:
                runs = [{"status": "completed", "conclusion": "failure",
                         "name": "call / review"}]
            return ok(json.dumps([{"total_count": len(runs), "check_runs": runs}]))
        if args[0] == "api" and "/compare/" in args[1]:
            return ok("")                          # no content carry to resolve (DRE-2340)
        if args[:2] == ("run", "list") and "--commit" in args:
            pr = self._by_head(args[args.index("--commit") + 1])
            runs = [] if pr is None else [{
                "databaseId": int(pr["run"]), "createdAt": "2026-11-01T20:00:00Z",
                "status": "in_progress" if pr["run"] in self.live_runs else "completed",
                "conclusion": None if pr["run"] in self.live_runs else "failure",
            }]
            return ok(json.dumps(runs))
        if args[:2] == ("run", "list"):
            return ok("[]")                        # no workflow_dispatch review in flight
        if args[:2] == ("run", "rerun"):
            assert args[3:] == ("--failed", "--repo", REPO), args
            self.reruns.append(args[2])
            self.live_runs.add(args[2])
            return ok()
        raise AssertionError(f"unexpected gh call: {argv}")

    # ── Linear ──────────────────────────────────────────────────────────────
    def cards(self, *_lanes, **_kw) -> list[dict]:
        return [{
            "id": f"id-{pr['card']}", "identifier": pr["card"], "title": pr["card"],
            "description": "work", "updatedAt": "2026-11-01T20:00:00Z",
            "state": {"name": "In Review"},
            "labels": {"nodes": [{"name": "repo:agent-bureau"}]},
            "comments": {"nodes": list(reversed(self.comments[pr["card"]]))},
        } for pr in STRANDED.values()]

    def bodies(self, ident, **_kw) -> list[str]:
        return [n["body"] for n in self.comments[ident]]

    def comment(self, ident, body):
        self.posted.append((ident, body))
        self.comments[ident].append(
            {"body": body, "createdAt": NOW.strftime("%Y-%m-%dT%H:%M:%S.000Z")})

    # ── the console ─────────────────────────────────────────────────────────
    def raise_notice(self, notice, **kw):
        self.raised.append({"notice": notice, **kw})
        self.alerts[notice] = {"kind": f"pipeline_notice:{notice}", "severity": "WARN",
                               "title": kw["title"], "raised_at": VERDICT_AT,
                               "card": notice.rsplit(":", 1)[-1]}
        return "raised"

    def resolve_notice(self, notice, **kw):
        self.resolved.append(notice)
        self.alerts.pop(notice, None)
        return "resolved"

    def open_alerts(self):
        return {"schema": "bureau-read/1", "read_at": VERDICT_AT,
                "alerts": list(self.alerts.values())}

    # ── one full sweep's two phases ─────────────────────────────────────────
    def sweep(self):
        """`recover_limit_deaths` then `recover_crashed_reviews`, the order
        `main()` runs them, with every seam on this world. Returns what this
        pass posted, re-ran and raised."""
        before = (len(self.posted), len(self.reruns), len(self.raised))
        reconcile.reset_sweep_cards()
        with patch.object(reconcile, "datetime", _Frozen), \
             patch.object(reconcile.subprocess, "run", side_effect=self.run), \
             patch.object(reconcile, "active_cards", side_effect=self.cards), \
             patch.object(reconcile.linear_ops, "comment_bodies", side_effect=self.bodies), \
             patch.object(reconcile.linear_ops, "cmd_comment", side_effect=self.comment), \
             patch.object(reconcile.pipeline_notice, "raise_notice",
                          side_effect=self.raise_notice), \
             patch.object(reconcile.pipeline_notice, "resolve_notice",
                          side_effect=self.resolve_notice), \
             patch.object(reconcile.bureau_read, "open_alerts", side_effect=self.open_alerts):
            reconcile.recover_limit_deaths()
            reconcile.recover_crashed_reviews()
        assert reconcile._write_failures == [], reconcile._write_failures
        assert reconcile._read_failures == [], reconcile._read_failures
        return SimpleNamespace(posted=self.posted[before[0]:],
                               reruns=self.reruns[before[1]:],
                               raised=[r["notice"] for r in self.raised[before[2]:]])

    def marker_live(self, ident: str) -> bool:
        live = limit_recovery.waiting(self.bodies(ident))
        return live is not None and live["stage"] == "review"


ALERTS = ["reviewer-down:agent-bureau:DRE-8553", "reviewer-down:agent-bureau:DRE-8667"]


@pytest.fixture(autouse=True)
def _blank_credentials(monkeypatch):
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_DISPATCH_TOKEN", "GH_READ_TOKEN",
                 "CLAUDE_ACCOUNT", "BUREAU_READ_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(reconcile, "REPO", REPO)
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()


def _reported(posted) -> list[str]:
    return [ident for ident, body in posted
            if body.startswith(f"🚨 {reconcile.REVIEWER_DOWN_TAG}")]


def test_the_sweep_runs_limit_recovery_before_the_crashed_review_pass():
    """The replay drives the two phases in `main()`'s order; this pins it."""
    tree = ast.parse(inspect.getsource(reconcile.main))
    order = [node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
             and node.id in ("recover_limit_deaths", "recover_crashed_reviews")]
    assert order.index("recover_limit_deaths") < order.index("recover_crashed_reviews")


def test_replay_waits_then_comes_back_on_the_verdict_then_reports_a_second_death():
    world = World()

    # (a) 13:16 PT: the wall is up and no review has succeeded yet.
    first = world.sweep()
    assert _reported(first.posted) == [], "no 🚨 reviewer-down while limit recovery owns it"
    assert first.raised == []
    assert first.reruns == []
    assert world.marker_live("DRE-8553") and world.marker_live("DRE-8667")

    # (b) PR #4470's verdict lands at 13:25 PT: the wall is down.
    world.verdict = True
    second = world.sweep()
    assert sorted(second.reruns) == sorted(pr["run"] for pr in STRANDED.values())
    receipted = {ident for ident, body in second.posted
                 if body.startswith(limit_recovery.RECOVERY_MARK)}
    assert receipted == {"DRE-8553", "DRE-8667"}
    assert all("2026-11-01 13:25 PT" in body for _, body in second.posted
               if body.startswith(limit_recovery.RECOVERY_MARK))
    assert _reported(second.posted) == []
    assert second.raised == []

    # (d) Each re-run died on the wall again. The medic marks a run id once,
    # so there is no new marker: the `🔁` receipt is the newest pipeline word.
    world.live_runs.clear()
    third = world.sweep()
    assert third.reruns == [], "re-run on the wall-down trigger at most once per run"
    assert sorted(_reported(third.posted)) == ["DRE-8553", "DRE-8667"]
    assert sorted(third.raised) == ALERTS


def test_replay_as_the_cards_stood_on_11_01():
    """(c) The `🚨 reviewer-down` of 13:18 PT closed both markers."""
    world = World(extra_bodies={n: [reviewer_down(n)] for n in STRANDED})
    world.verdict = True
    swept = world.sweep()
    assert not world.marker_live("DRE-8553") and not world.marker_live("DRE-8667")
    assert swept.reruns == []
    assert _reported(swept.posted) == [], "the comment is once per head"
    assert sorted(swept.raised) == ALERTS
    titles = sorted(world.alerts[key]["title"] for key in ALERTS)
    assert titles[0].startswith("DRE-8553: PR #4472 @1cd61cfb —")
    assert titles[1].startswith("DRE-8667: PR #4462 @887e486b —")
