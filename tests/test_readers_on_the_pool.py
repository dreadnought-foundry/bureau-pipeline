"""The heavy GitHub READERS mint through the dispatch pool (DRE-4282).

On 2026-09-18 at 15:14 PT the three spare worker Apps' secrets
(`BUREAU_APP_ID_2/3/4`, `BUREAU_APP_PRIVATE_KEY_2/3/4`) went org-level, so the
pool `scripts/dispatch_pool.py` selects from (DRE-2013) is live in every repo.
Measured fifteen minutes later: bot-1 (App 3350400) had spent 974 of its 5,000
requests twelve minutes into its hour; bots 2, 3 and 4 had spent 2, 5 and 4.
Four buckets existed and the heavy readers — the critic's context assembly and
PR reads, the reconcile sweep, agent-fix's repeated comment fetches, the
harness driver (DRE-4132's measurement) — all drew on one, because only
`agent-task.yml`, `verify.yml` and `red-main-repair.yml` consulted the pool.

What this suite pins, workflow by workflow:

  * the pool probe mints (`probe_2/3/4`, gated on the slot being configured,
    `continue-on-error`) run BEFORE the selector, the selector runs
    `dispatch_pool.py select` after the pipeline checkout and before the reader
    mint, and the reader mint (`id: reader`) maps every advertised slot to its
    secret pair and falls through to the workflow's OWN original pair — for
    qa-review that is the qa-bot App, its slot 1 (DRE-1921's bucket stays the
    fallback, the pool is capacity on top of it);
  * the heavy read steps consume `steps.reader.outputs.token`;
  * the identity-sensitive steps do NOT: the PR-authoring checkout and push,
    the worker-attributed comments the fix loop counts by login, the verdict
    comment merge-gate attributes to the qa-bot — each keeps the token it used
    before this card. `verify.yml`'s shape (DRE-2429) is the reference; none of
    it is invented here;
  * with no pool secrets the rendered mint inputs are the original pair, byte
    for byte — DRE-2013's graceful degradation, evaluated over the actual
    `${{ }}` chains rather than asserted from their shape;
  * medic.yml is deliberately NOT a consumer: every GitHub read it makes rides
    `github.token`, the workflow's own bucket, because the App deliberately
    lacks `actions:read` and a diagnosis that cannot fetch the failed run's
    logs diagnoses blind (DRE-1346 Fix 3). A pool token there would move reads
    off a free bucket onto the contended one and break them. Pinned so the
    omission reads as a decision, not an oversight.

These read the LIVE workflow YAML (the test_dispatch_pool_wiring.py pattern —
no copied fixtures) and must FAIL on the tree before the wiring lands.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))

import dispatch_pool  # noqa: E402

MINT = "actions/create-github-app-token"
MODEL = "anthropics/claude-code-action"
POOL_SLOTS = [2, 3, 4]
READER = "${{ steps.reader.outputs.token }}"
APP = "${{ steps.app.outputs.token }}"

#: Every workflow that joins the pool with this card, and the contract each one
#: signs: which job, which secret pair is its slot 1 (the boot mint's App, the
#: identity the reader mint falls back to), which step's token is slot 1's
#: probe, which steps are the heavy readers (token field -> must be the
#: reader), and which steps keep their original token because WHO acts there
#: is checked by something downstream.
CONSUMERS = {
    "qa-review.yml": {
        "job": "review",
        # Slot 1 is the qa-bot App: DRE-1921 gave the critic its own bucket and
        # that bucket stays the fallback. The pool adds three more.
        "original": ("BUREAU_QA_APP_ID", "BUREAU_QA_APP_PRIVATE_KEY"),
        "boot": APP,
        "pipeline_checkout": True,
        "readers": {
            "Resolve PR": ("env", "GH_TOKEN"),
            "Size the diff and pick the review strategy": ("env", "GH_TOKEN"),
            "Plan visual QA": ("env", "GH_TOKEN"),
            "Build repair-PR review context": ("env", "GH_TOKEN"),
            "Does this PR change the act registry?": ("env", "GH_TOKEN"),
            "Build card review context": ("env", "GH_TOKEN"),
            "Critic review": ("with", "github_token"),
            "Critic review (retry)": ("with", "github_token"),
        },
        "stays": {
            # Runs before selection and decides whether to review at all.
            "Decide review": ("env", "GH_TOKEN", APP),
            # The verdict comment is ATTRIBUTED to the qa-bot: merge_gate.py
            # accepts a verdict only from that login (DRE-1987).
            "Post verdict or neutral status": ("env", "GH_TOKEN", APP),
        },
    },
    "reconcile.yml": {
        "job": "sweep",
        "original": ("BUREAU_APP_ID", "BUREAU_APP_PRIVATE_KEY"),
        "boot": APP,
        "pipeline_checkout": True,
        # The sweep is one step. Its reads go through reconcile.gh_read under
        # GH_READ_TOKEN; its writes — the receipts `_worker_receipt_count`
        # counts against the worker login, the merge-sweep commits — stay on
        # GH_TOKEN, the original App.
        "readers": {"Sweep": ("env", "GH_READ_TOKEN")},
        "stays": {"Sweep": ("env", "GH_TOKEN", APP)},
    },
    "agent-fix.yml": {
        "job": "fix",
        "original": ("BUREAU_APP_ID", "BUREAU_APP_PRIVATE_KEY"),
        "boot": APP,
        "pipeline_checkout": True,
        "readers": {
            "Decide a comment-triggered start": ("env", "GH_TOKEN"),
            "Resolve PR, mode, and attempt budget": ("env", "GH_TOKEN"),
            "Escalate checks the loop structurally cannot fix": ("env", "GH_TOKEN"),
            "Label failures inherited from the merge base": ("env", "GH_TOKEN"),
            "Fetch critic verdict (qa-bot authored only)": ("env", "GH_TOKEN"),
            "Fetch fix-loop thread (blockers + operator decisions)": ("env", "GH_TOKEN"),
        },
        "stays": {
            # The checkout's persisted credential is what the fix agent pushes
            # with — the push is agent-bureau-bot's, like the PR it lands on.
            "Checkout PR branch": ("with", "token", APP),
            # Worker-attributed comments: fix_budget.py counts attempt markers
            # by WORKER_LOGIN, so the author must be the App that login names.
            "Announce fix attempt": ("env", "GH_TOKEN", APP),
            "Receipt the operator-decision restart": ("env", "GH_TOKEN", APP),
            "Fix": ("with", "github_token", APP),
            "Report": ("env", "GH_TOKEN", APP),
        },
    },
    "plan.yml": {
        "job": "plan",
        "original": ("BUREAU_APP_ID", "BUREAU_APP_PRIVATE_KEY"),
        "boot": APP,
        "pipeline_checkout": True,
        # The one model step that reads a token minted before selection; every
        # other model step re-mints (DRE-3940), and those re-mints are pinned to
        # the reader's own pool map below.
        "readers": {"Pre-approval critic — the one-off exit": ("with", "github_token")},
        "stays": {},
    },
    "harness.yml": {
        "job": "harness",
        "original": ("BUREAU_APP_ID", "BUREAU_APP_PRIVATE_KEY"),
        # Slot 1's probe is the sandbox-scoped worker token the job already
        # mints; /rate_limit is per installation, so the scope does not matter.
        "boot": "${{ steps.worker.outputs.token }}",
        "pipeline_checkout": False,
        "readers": {"Run harness scenarios": ("env", "HARNESS_READER_TOKEN")},
        "stays": {
            # WHICH identity performs an action is the thing under test
            # (scripts/harness/github_api.py): the worker authors, the qa-bot
            # merges. Reads are not an action anyone attributes.
            "Run harness scenarios": ("env", "HARNESS_WORKER_TOKEN",
                                      "${{ steps.worker.outputs.token }}"),
        },
    },
}

#: The one workflow the card names that does not join, and why (module doc).
NOT_A_CONSUMER = "medic.yml"


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def steps(name: str) -> list[dict]:
    return load(name)["jobs"][CONSUMERS[name]["job"]]["steps"]


def action(step: dict) -> str:
    return str(step.get("uses") or "").split("@")[0]


def index(name: str, pred, label: str) -> tuple[int, dict]:
    for i, step in enumerate(steps(name)):
        if pred(step):
            return i, step
    raise AssertionError(f"{name} has no step: {label}")


def named(name: str, step_name: str) -> tuple[int, dict]:
    return index(name, lambda s: s.get("name") == step_name, repr(step_name))


def by_id(name: str, step_id: str) -> tuple[int, dict]:
    return index(name, lambda s: s.get("id") == step_id, f"id: {step_id}")


def selector(name: str) -> tuple[int, dict]:
    return index(
        name,
        lambda s: "dispatch_pool.py select" in (s.get("run") or ""),
        "selector (dispatch_pool.py select)",
    )


def reader_mint(name: str) -> tuple[int, dict]:
    i, step = by_id(name, "reader")
    assert action(step) == MINT, f"{name}: id: reader is not a {MINT} step"
    return i, step


def token_of(step: dict, where: str, key: str) -> str:
    block = step.get(where) or {}
    return str(block.get(key, ""))


# --------------------------------------------------------------------------- #
# The `${{ a == 'N' && x || b == 'M' && y || z }}` chain, evaluated             #
# --------------------------------------------------------------------------- #

_CLAUSE = re.compile(r"steps\.pool\.outputs\.n\s*==\s*'([^']*)'\s*&&\s*(\S+)")


def render_chain(expression: str, n: str) -> str:
    """What GitHub hands the mint for input `expression` when the selector
    wrote `n` — or when it never ran (`n == ''`, a skipped step's output).

    The chain is `cond && value || cond && value || fallback`: `&&` binds
    tighter than `||`, a false `cond` makes its clause '' (falsy), and the
    first truthy clause wins. That is the whole of the grammar these mints
    use, and it is evaluated here rather than pattern-matched so the test
    says what the RENDERED input is.
    """
    body = expression.strip()
    assert body.startswith("${{") and body.endswith("}}"), expression
    inner = body[3:-2].strip()
    clauses = [c.strip() for c in inner.split("||")]
    for clause in clauses[:-1]:
        m = _CLAUSE.fullmatch(clause)
        assert m, f"unrecognised clause {clause!r} in {expression!r}"
        if m.group(1) == n:
            return m.group(2)
    return clauses[-1]


class PoolProbesAndSelectorTest(unittest.TestCase):
    """verify.yml's shape, in every consumer."""

    def test_every_consumer_has_a_guarded_probe_per_slot_before_the_selector(self):
        for name in CONSUMERS:
            with self.subTest(workflow=name):
                sel_i, _ = selector(name)
                for n in POOL_SLOTS:
                    i, probe = by_id(name, f"probe_{n}")
                    self.assertLess(i, sel_i, f"probe_{n} must precede the selector")
                    self.assertEqual(action(probe), MINT)
                    # Absent secrets SKIP the probe (pool shrinks), a broken
                    # pool app never fails the run.
                    self.assertIn(f"BUREAU_APP_ID_{n}", str(probe.get("if")))
                    self.assertTrue(probe.get("continue-on-error"), f"probe_{n}")
                    with_ = probe.get("with") or {}
                    self.assertIn(f"secrets.BUREAU_APP_ID_{n}", with_.get("app-id", ""))
                    self.assertIn(
                        f"secrets.BUREAU_APP_PRIVATE_KEY_{n}",
                        with_.get("private-key", ""),
                    )

    def test_the_probe_gate_reads_a_job_env_mirror_of_the_app_id(self):
        # The secrets context is unreadable in a step-level `if:`, so the gate
        # reads a job-env mirror — app ids only, never a key (verify.yml).
        for name, spec in CONSUMERS.items():
            with self.subTest(workflow=name):
                env = load(name)["jobs"][spec["job"]].get("env") or {}
                for n in POOL_SLOTS:
                    self.assertEqual(
                        env.get(f"BUREAU_APP_ID_{n}"),
                        f"${{{{ secrets.BUREAU_APP_ID_{n} }}}}",
                    )
                    self.assertNotIn(f"BUREAU_APP_PRIVATE_KEY_{n}", env)

    def test_selector_runs_after_the_pipeline_checkout_and_before_the_reader(self):
        for name, spec in CONSUMERS.items():
            with self.subTest(workflow=name):
                sel_i, _ = selector(name)
                reader_i, _ = reader_mint(name)
                self.assertLess(sel_i, reader_i)
                if spec["pipeline_checkout"]:
                    co_i, _ = index(
                        name,
                        lambda s: (s.get("with") or {}).get("path") == ".bureau-pipeline",
                        "bureau-pipeline checkout",
                    )
                    self.assertGreater(sel_i, co_i)

    def test_selector_sees_slot_one_as_the_workflows_own_identity(self):
        for name, spec in CONSUMERS.items():
            with self.subTest(workflow=name):
                _, sel = selector(name)
                env = sel.get("env") or {}
                original_id, _ = spec["original"]
                self.assertEqual(env.get("BUREAU_APP_ID"), f"${{{{ secrets.{original_id} }}}}")
                self.assertEqual(env.get("BUREAU_POOL_TOKEN"), spec["boot"])
                self.assertTrue(env.get("BUREAU_POOL_KEY"), "deterministic fallback key")
                for n in POOL_SLOTS:
                    self.assertEqual(
                        env.get(f"BUREAU_APP_ID_{n}"), f"${{{{ secrets.BUREAU_APP_ID_{n} }}}}"
                    )
                    self.assertEqual(
                        env.get(f"BUREAU_POOL_TOKEN_{n}"),
                        f"${{{{ steps.probe_{n}.outputs.token }}}}",
                    )

    def test_selector_never_fails_the_run_on_its_own(self):
        # dispatch_pool.py exits 0 on any error; the step must not wrap it in
        # anything that could turn a selector hiccup into a red job.
        for name in CONSUMERS:
            with self.subTest(workflow=name):
                _, sel = selector(name)
                self.assertNotIn("set -e", sel.get("run") or "")


class ReaderMintTest(unittest.TestCase):

    def test_reader_maps_every_slot_and_falls_back_to_the_original_pair(self):
        for name, spec in CONSUMERS.items():
            with self.subTest(workflow=name):
                _, reader = reader_mint(name)
                with_ = reader.get("with") or {}
                original_id, original_key = spec["original"]
                for n in POOL_SLOTS:
                    self.assertIn(f"secrets.BUREAU_APP_ID_{n}", with_.get("app-id", ""))
                    self.assertIn(
                        f"secrets.BUREAU_APP_PRIVATE_KEY_{n}", with_.get("private-key", "")
                    )
                self.assertIn("steps.pool.outputs.n", with_.get("app-id", ""))
                self.assertRegex(
                    with_.get("app-id", ""), rf"\|\|\s*secrets\.{original_id}\s*}}}}\s*$"
                )
                self.assertRegex(
                    with_.get("private-key", ""),
                    rf"\|\|\s*secrets\.{original_key}\s*}}}}\s*$",
                )
                self.assertFalse(
                    reader.get("continue-on-error"),
                    "a reader that cannot mint should go red AT the mint",
                )

    def test_every_pool_mint_renders_the_original_pair_without_pool_secrets(self):
        """DRE-2013's degradation, evaluated: with no pool secrets the probes
        are skipped, the selector answers slot 1, and every mint whose inputs
        read the selector renders the ORIGINAL pair — today's inputs exactly.
        Also for `n == ''`: a mint that runs on a path where the selector was
        skipped (qa-review's carried-verdict path) must fall through too."""
        for name, spec in CONSUMERS.items():
            with self.subTest(workflow=name):
                env = {"BUREAU_APP_ID": "3350400"}
                for n in POOL_SLOTS:
                    env[f"BUREAU_APP_ID_{n}"] = ""  # an unset secret renders ''
                slot, reason = dispatch_pool.select(env)
                self.assertEqual((slot, reason), (1, "single-app"))
                pool_mints = [
                    s for s in steps(name)
                    if action(s) == MINT
                    and "steps.pool.outputs.n" in yaml.safe_dump(s.get("with") or {})
                ]
                self.assertTrue(pool_mints, f"{name}: no mint reads the selector")
                for mint in pool_mints:
                    with_ = mint["with"]
                    for n in (str(slot), ""):
                        rendered = (
                            render_chain(with_["app-id"], n),
                            render_chain(with_["private-key"], n),
                        )
                        self.assertEqual(
                            rendered,
                            tuple(f"secrets.{s}" for s in self._fallback_of(mint, spec)),
                            f"{name}: {mint.get('name')!r} with n={n!r}",
                        )
                    # And a selected spare slot renders THAT slot's pair.
                    self.assertEqual(
                        render_chain(with_["app-id"], "3"), "secrets.BUREAU_APP_ID_3"
                    )
                    self.assertEqual(
                        render_chain(with_["private-key"], "3"),
                        "secrets.BUREAU_APP_PRIVATE_KEY_3",
                    )

    @staticmethod
    def _fallback_of(mint: dict, spec: dict) -> tuple[str, str]:
        # Every pool mint falls back to the workflow's slot 1. (qa-review's
        # check-run mint is not a pool mint at all — see the writer pin below.)
        return spec["original"]

    def test_pool_secrets_are_declared_optional_on_every_reusable(self):
        # A stub that does not pass the pairs must stay valid (harness.yml is
        # not a reusable: it reads the repo's own secrets directly).
        for name in CONSUMERS:
            doc = load(name)
            on = doc.get("on") or doc.get(True)
            if "workflow_call" not in on:
                continue
            with self.subTest(workflow=name):
                declared = on["workflow_call"]["secrets"]
                for n in POOL_SLOTS:
                    for key in (f"BUREAU_APP_ID_{n}", f"BUREAU_APP_PRIVATE_KEY_{n}"):
                        self.assertIn(key, declared)
                        self.assertFalse((declared[key] or {}).get("required", False), key)


class ReadsMoveWritesStayTest(unittest.TestCase):

    def test_heavy_reads_consume_the_reader_token(self):
        for name, spec in CONSUMERS.items():
            for step_name, (where, key) in spec["readers"].items():
                with self.subTest(workflow=name, step=step_name):
                    i, step = named(name, step_name)
                    self.assertEqual(token_of(step, where, key), READER)
                    reader_i, _ = reader_mint(name)
                    self.assertGreater(i, reader_i, "reads before the mint that feeds them")

    def test_identity_sensitive_steps_keep_their_original_token(self):
        for name, spec in CONSUMERS.items():
            for step_name, (where, key, expected) in spec["stays"].items():
                with self.subTest(workflow=name, step=step_name):
                    _, step = named(name, step_name)
                    self.assertEqual(token_of(step, where, key), expected)

    def test_the_fix_loops_own_login_is_still_the_writers(self):
        # The reader fetches the thread; the login it counts markers by is
        # the App that WRITES them (DRE-1988's app-slug derivation).
        for step_name in (
            "Decide a comment-triggered start",
            "Resolve PR, mode, and attempt budget",
            "Fetch fix-loop thread (blockers + operator decisions)",
        ):
            with self.subTest(step=step_name):
                _, step = named("agent-fix.yml", step_name)
                self.assertEqual(
                    (step.get("env") or {}).get("WORKER_LOGIN"),
                    "${{ steps.app.outputs.app-slug }}[bot]",
                )

    def test_qa_reviews_boot_mint_is_still_the_qa_app(self):
        # DRE-1921 is not undone: slot 1 IS the qa-bot's bucket.
        _, app = by_id("qa-review.yml", "app")
        self.assertEqual(
            (app.get("with") or {}).get("app-id"), "${{ secrets.BUREAU_QA_APP_ID }}"
        )

    def test_qa_reviews_check_run_writer_is_one_app_and_never_the_pool_slot(self):
        # Critic finding on DRE-4282, round 1. publish_review_check.py UPDATES
        # the head's review check run in place, and GitHub lets only the App
        # that CREATED a check run update it. A mint that followed the pool
        # slot would fail at the last step of any re-review that landed on a
        # different slot, leave the head with a stale check, and make the
        # crash-recovery loop read a working reviewer as broken. So the
        # check-run writer is ONE App — the dispatch App, which has always
        # written it — for both the publish and the carried re-publish.
        _, mint = by_id("qa-review.yml", "checks_app")
        with_ = mint.get("with") or {}
        self.assertEqual(with_.get("app-id"), "${{ secrets.BUREAU_APP_ID }}")
        self.assertEqual(with_.get("private-key"), "${{ secrets.BUREAU_APP_PRIVATE_KEY }}")
        self.assertNotIn("steps.pool", yaml.safe_dump(with_))
        writers = [
            s for s in steps("qa-review.yml")
            if "publish_review_check.py" in (s.get("run") or "")
        ]
        self.assertEqual(len(writers), 2, "the publish and the carried re-publish")
        for step in writers:
            self.assertEqual(
                token_of(step, "env", "GH_TOKEN"), "${{ steps.checks_app.outputs.token }}"
            )

    def test_plans_model_steps_all_read_a_pool_selected_token(self):
        # plan.yml re-mints before every model step (DRE-3940). Each of those
        # re-mints must be the READER mint again — same pool map, same
        # fallback — so the twelve planner/critic runs read through the slot
        # the selector chose, not through slot 1 by habit.
        _, reader = reader_mint("plan.yml")
        for i, step in enumerate(steps("plan.yml")):
            if action(step) != MODEL:
                continue
            with self.subTest(step=step.get("name")):
                token = token_of(step, "with", "github_token")
                m = re.fullmatch(r"\$\{\{ steps\.([A-Za-z0-9_]+)\.outputs\.token \}\}", token)
                self.assertIsNotNone(m, token)
                _, mint = by_id("plan.yml", m.group(1))
                self.assertEqual(mint.get("with"), reader.get("with"), step.get("name"))

    def test_plans_checkout_and_publish_job_are_untouched(self):
        # The PR-side writes: the checkout credential and the portal push.
        doc = load("plan.yml")
        _, checkout = index(
            "plan.yml",
            lambda s: action(s) == "actions/checkout" and "token" in (s.get("with") or {}),
            "checkout with token",
        )
        self.assertEqual(checkout["with"]["token"], APP)
        publish = doc["jobs"]["publish"]["steps"]
        self.assertFalse(
            any("steps.pool" in yaml.safe_dump(s) for s in publish),
            "the publish job mints and pushes as the original App",
        )


class HarnessReaderWiringTest(unittest.TestCase):

    def test_pool_mints_are_scoped_to_the_sandbox(self):
        for step_id in ("probe_2", "probe_3", "probe_4", "reader"):
            with self.subTest(step=step_id):
                _, step = by_id("harness.yml", step_id)
                with_ = step.get("with") or {}
                self.assertEqual(with_.get("owner"), "dreadnought-foundry")
                self.assertEqual(with_.get("repositories"), "bureau-harness")

    def test_the_driver_gets_the_readers_app_credentials_and_the_slot(self):
        _, run = named("harness.yml", "Run harness scenarios")
        _, reader = reader_mint("harness.yml")
        env = run.get("env") or {}
        # The same pool map the mint uses, so the driver's mid-run re-mint
        # (app_token.py, run 29795108949) comes from the SELECTED App's key.
        self.assertEqual(env.get("HARNESS_READER_APP_ID"), reader["with"]["app-id"])
        self.assertEqual(
            env.get("HARNESS_READER_APP_PRIVATE_KEY"), reader["with"]["private-key"]
        )
        self.assertEqual(env.get("HARNESS_POOL_SLOT"), "${{ steps.pool.outputs.n }}")
        # The identities under test are still the named ones.
        self.assertEqual(env.get("HARNESS_WORKER_APP_ID"), "${{ secrets.BUREAU_APP_ID }}")
        self.assertEqual(env.get("HARNESS_QA_TOKEN"), "${{ steps.qa.outputs.token }}")


class ReconcileReadTokenTest(unittest.TestCase):
    """The sweep's reads go out under GH_READ_TOKEN; its writes do not."""

    def setUp(self):
        os.environ.setdefault("LINEAR_API_KEY", "test-key")
        os.environ.setdefault("REPO", "test/test")
        os.environ.setdefault("GH_TOKEN", "test")
        import reconcile  # noqa: F401  (env above satisfies its import)
        import gh_read_retry  # noqa: F401
        self.reconcile = reconcile
        self.gh_read_retry = gh_read_retry

    def _run_capturing(self, fn, env, absent=()):
        """Run `fn` with `env` layered on the process env and `absent` removed,
        recording every gh subprocess as (argv, env passed to subprocess)."""
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs.get("env")))
            return mock.Mock(returncode=0, stdout="[]\n", stderr="")

        with mock.patch.dict(os.environ, env), mock.patch.object(
            self.gh_read_retry.subprocess, "run", fake_run
        ), mock.patch.object(self.reconcile.subprocess, "run", fake_run):
            for key in absent:
                os.environ.pop(key, None)
            fn()
        return calls

    def test_gh_read_swaps_in_the_read_token_when_it_is_set(self):
        calls = self._run_capturing(
            lambda: self.reconcile.gh_read("pr", "list"),
            {"GH_TOKEN": "ghs_app", "GH_READ_TOKEN": "ghs_reader"},
        )
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(calls[0][1], "gh_read must pass an env with the read token")
        self.assertEqual(calls[0][1]["GH_TOKEN"], "ghs_reader")

    def test_gh_read_without_a_read_token_is_exactly_today(self):
        calls = self._run_capturing(
            lambda: self.reconcile.gh_read("pr", "list"),
            {"GH_TOKEN": "ghs_app"},
            absent=("GH_READ_TOKEN",),
        )
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0][1])

    def test_writes_never_use_the_read_token(self):
        calls = self._run_capturing(
            lambda: (
                self.reconcile.gh("pr", "comment", "1", "--body", "x"),
                self.reconcile.gh_dispatch("workflow", "run", "x.yml"),
            ),
            {"GH_TOKEN": "ghs_app", "GH_READ_TOKEN": "ghs_reader"},
            absent=("GH_DISPATCH_TOKEN",),
        )
        self.assertEqual(len(calls), 2)
        for argv, env_used in calls:
            self.assertTrue(
                env_used is None or env_used.get("GH_TOKEN") == "ghs_app",
                f"{argv} went out under {env_used and env_used.get('GH_TOKEN')}",
            )

    def test_the_read_token_is_optional_at_every_call_site(self):
        # Optional by construction: linear-sync.yml's and plan.yml's reconcile
        # steps do not select a slot and must keep passing
        # check_reconcile_env.py. Absent, the sweep reads on GH_TOKEN as today.
        self.assertNotIn("GH_READ_TOKEN", self.reconcile.REQUIRED_ENV)


class MedicIsNotAConsumerTest(unittest.TestCase):

    def test_every_medic_read_rides_the_workflow_token(self):
        doc = load(NOT_A_CONSUMER)
        tokens = []
        for job in doc["jobs"].values():
            for step in job.get("steps") or []:
                for block in ("env", "with"):
                    for key in ("GH_TOKEN", "github_token"):
                        value = (step.get(block) or {}).get(key)
                        if value is not None:
                            tokens.append((step.get("name"), value))
        self.assertTrue(tokens, "medic.yml makes no GitHub calls at all?")
        for name, value in tokens:
            self.assertEqual(value, "${{ github.token }}", name)

    def test_medic_declares_no_pool_secrets_and_runs_no_selector(self):
        doc = load(NOT_A_CONSUMER)
        on = doc.get("on") or doc.get(True)
        for n in POOL_SLOTS:
            self.assertNotIn(f"BUREAU_APP_ID_{n}", on["workflow_call"]["secrets"])
        self.assertNotIn("dispatch_pool.py", (WORKFLOWS / NOT_A_CONSUMER).read_text())


if __name__ == "__main__":
    unittest.main()
