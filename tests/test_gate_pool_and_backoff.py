"""The two gate workflows and the API-quota bucket they run on (DRE-2427).

Live evidence (agent-bureau PR #2045, Verify run 31659904468):

    2026-08-13T02:27:07Z GET /users/agent-bureau-bot%5Bbot%5D - 403
    ##[error]Action failed with error: API rate limit exceeded for
    installation ID 123249480
    2026-08-13T02:27:08Z GET /users/agent-bureau-bot%5Bbot%5D - 403
    ##[error]API rate limit exceeded for installation ID 123249480

Two distinct defects, one symptom:

  1. verify.yml minted the agent's `github_token` from BUREAU_APP_ID — pool
     slot 1, installation 123249480 — so the Verifier died in
     claude-code-action's PREFLIGHT, before the model was ever reached. The
     4-app dispatch pool (DRE-2013) existed but only agent-task.yml and
     red-main-repair.yml used it. dispatch_pool.py picks the app with the
     MOST remaining core quota, so a pooled verify would have stepped off
     the drained bucket on its own.

  2. The two 403s above are 1.7 SECONDS apart: attempt 1 and its retry.
     A rate-limit 403 is a DETERMINISTIC failure for the rest of the hourly
     window — an immediate re-run with byte-identical parameters cannot
     succeed. It is pure waste, and it happened four times on that one PR.

qa-review.yml is deliberately NOT pooled — see
QaReviewKeepsItsOwnQuotaBucketTest. It already mints from the SEPARATE
qa-bot App (DRE-1921); moving it onto the pool would take the critic OFF a
healthy dedicated bucket and ONTO the contended one. Every QA Review run on
PR #2045 succeeded while Verify died, which is that split working.

These read the LIVE workflow YAML (same live-extraction pattern as
test_dispatch_pool_wiring.py — no copied fixtures).
"""

import os
import re
import unittest

import yaml

WORKFLOWS_DIR = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows"
)

# The pool as provisioned today: original + agent-bureau-bot-2/3/4.
POOL_SLOTS = [2, 3, 4]

# A retry that fires faster than this is not a retry, it is a second copy of
# the same failed call. 60s is the floor the card sets; verify.yml uses more
# (see BACKOFF_SECONDS in the workflow comment).
MIN_BACKOFF_SECONDS = 60

_SLEEP_RE = re.compile(r"\bsleep\s+(\d+)\b")


def load(filename):
    with open(os.path.join(WORKFLOWS_DIR, filename)) as f:
        return yaml.safe_load(f)


def steps(filename, job):
    return load(filename)["jobs"][job]["steps"]


def step_index(filename, job, pred, label):
    for i, s in enumerate(steps(filename, job)):
        if pred(s):
            return i, s
    raise AssertionError(f"{filename}: no step matching {label}")


def workflow_call_secrets(filename):
    wf = load(filename)
    # pyyaml parses a bare `on:` key as the boolean True.
    on = wf.get("on") or wf.get(True)
    return on["workflow_call"]["secrets"]


def sleep_seconds(step):
    """The longest `sleep N` in a step's run block, or None."""
    found = [int(n) for n in _SLEEP_RE.findall(step.get("run") or "")]
    return max(found) if found else None


class VerifyIsPoolAwareTest(unittest.TestCase):
    """verify.yml must select from the dispatch pool, exactly as
    agent-task.yml does, and the VERIFIER must consume the selected token —
    a pool nothing draws from is dead weight."""

    WF, JOB = "verify.yml", "verify"

    def selector(self):
        return step_index(
            self.WF, self.JOB,
            lambda s: "dispatch_pool.py select" in (s.get("run") or ""),
            "selector (dispatch_pool.py select)",
        )

    def worker_mint(self):
        return step_index(
            self.WF, self.JOB,
            lambda s: s.get("id") == "worker"
            and "create-github-app-token" in (s.get("uses") or ""),
            "worker mint (id: worker)",
        )

    def advertised_slots(self):
        _, sel = self.selector()
        env = sel.get("env") or {}
        slots = set()
        for name in env:
            m = re.match(r"^BUREAU_(?:APP_ID|POOL_TOKEN)_([0-9]+)$", name)
            if m:
                slots.add(int(m.group(1)))
        return sorted(slots)

    def test_pool_slots_are_advertised(self):
        self.assertEqual(self.advertised_slots(), POOL_SLOTS)

    def test_selector_runs_after_the_pipeline_checkout(self):
        # dispatch_pool.py lives in the .bureau-pipeline checkout.
        co_i, _ = step_index(
            self.WF, self.JOB,
            lambda s: (s.get("with") or {}).get("path") == ".bureau-pipeline",
            "bureau-pipeline checkout",
        )
        sel_i, _ = self.selector()
        self.assertGreater(sel_i, co_i)

    def test_selector_runs_before_the_worker_mint(self):
        sel_i, _ = self.selector()
        mint_i, _ = self.worker_mint()
        self.assertLess(sel_i, mint_i)

    def test_selector_gets_tokens_ids_and_a_hash_key(self):
        _, sel = self.selector()
        env = sel.get("env") or {}
        # Slot 1 = the original app, probed with the already-minted boot token.
        self.assertIn("BUREAU_APP_ID", env)
        self.assertIn("BUREAU_POOL_TOKEN", env)
        self.assertIn("BUREAU_POOL_KEY", env)
        for n in self.advertised_slots():
            self.assertIn(f"BUREAU_APP_ID_{n}", env)
            self.assertIn(
                f"steps.probe_{n}.outputs.token", env[f"BUREAU_POOL_TOKEN_{n}"]
            )

    def test_every_advertised_slot_has_a_guarded_probe_mint(self):
        # Absent secrets must SKIP the probe (the pool shrinks), and a broken
        # pool app must never fail the build.
        sel_i, _ = self.selector()
        for n in self.advertised_slots():
            i, probe = step_index(
                self.WF, self.JOB,
                lambda s, n=n: s.get("id") == f"probe_{n}", f"probe_{n}",
            )
            self.assertLess(i, sel_i, f"probe_{n} must run before the selector")
            self.assertIn("create-github-app-token", probe.get("uses") or "")
            self.assertIn(f"BUREAU_APP_ID_{n}", str(probe.get("if")))
            self.assertTrue(
                probe.get("continue-on-error"),
                f"probe_{n} must not fail the build on a broken pool app",
            )
            with_ = probe.get("with") or {}
            self.assertIn(f"BUREAU_APP_ID_{n}", with_.get("app-id", ""))
            self.assertIn(
                f"BUREAU_APP_PRIVATE_KEY_{n}", with_.get("private-key", "")
            )

    def test_worker_mint_maps_every_advertised_slot(self):
        # Actions cannot index secrets dynamically — each N needs an explicit
        # clause or that slot silently routes to the wrong app.
        _, mint = self.worker_mint()
        with_ = mint.get("with") or {}
        for n in self.advertised_slots():
            self.assertIn(f"secrets.BUREAU_APP_ID_{n}", with_.get("app-id", ""))
            self.assertIn(
                f"secrets.BUREAU_APP_PRIVATE_KEY_{n}",
                with_.get("private-key", ""),
            )
        self.assertIn("steps.pool.outputs.n", with_.get("app-id", ""))
        self.assertIn("steps.pool.outputs.n", with_.get("private-key", ""))

    def test_worker_mint_falls_back_to_the_original_app(self):
        # Graceful degradation: a repo with no pool secrets behaves exactly
        # as it does today.
        _, mint = self.worker_mint()
        with_ = mint.get("with") or {}
        self.assertRegex(
            with_.get("app-id", ""), r"\|\|\s*secrets\.BUREAU_APP_ID\s*}}\s*$"
        )
        self.assertRegex(
            with_.get("private-key", ""),
            r"\|\|\s*secrets\.BUREAU_APP_PRIVATE_KEY\s*}}\s*$",
        )

    def test_pool_secrets_declared_optional_on_workflow_call(self):
        # A stub that does not pass the new secrets must stay valid.
        declared = workflow_call_secrets(self.WF)
        for n in POOL_SLOTS:
            for name in (f"BUREAU_APP_ID_{n}", f"BUREAU_APP_PRIVATE_KEY_{n}"):
                self.assertIn(name, declared, f"{name} must be declared")
                self.assertFalse(
                    (declared[name] or {}).get("required", False),
                    f"{name} must be optional (graceful degradation)",
                )

    def test_both_verifier_runs_use_the_selected_worker_token(self):
        # THE regression pin. claude-code-action's preflight authenticates
        # with github_token; on the boot token that is installation
        # 123249480, the bucket that 403'd four times on PR #2045. Both the
        # first attempt and the retry must draw from the SELECTED app.
        agent_steps = [
            s for s in steps(self.WF, self.JOB)
            if "claude-code-action" in (s.get("uses") or "")
        ]
        self.assertEqual(
            len(agent_steps), 2,
            "verify.yml should have exactly two claude-code-action steps "
            f"(attempt + retry); found {[s.get('id') for s in agent_steps]}",
        )
        for s in agent_steps:
            self.assertEqual(
                (s.get("with") or {}).get("github_token"),
                "${{ steps.worker.outputs.token }}",
                f"verify.yml step {s.get('id')!r} still mints its agent token "
                "from the single primary bucket (installation 123249480)",
            )

    def test_job_env_mirrors_pool_ids_for_the_probe_guards(self):
        # The secrets context is unreadable in a step-level `if:`, so the
        # probe presence checks read job-env mirrors (app ids only).
        env = load(self.WF)["jobs"][self.JOB].get("env") or {}
        for n in POOL_SLOTS:
            self.assertIn(f"BUREAU_APP_ID_{n}", env)
            self.assertNotIn(
                f"BUREAU_APP_PRIVATE_KEY_{n}", env,
                "private keys must never be mirrored into job env",
            )


class RetryBacksOffTest(unittest.TestCase):
    """A retry fired 1.7s after a deterministic failure is not a retry.

    Both gates run their agent, gate the result, and re-run on a crash. The
    re-run must be separated from the failed attempt by a real backoff, or a
    rate-limited / still-degraded upstream simply fails twice.
    """

    def assert_backoff_between(self, filename, job, gate_id, retry_id):
        gate_i, _ = step_index(
            filename, job, lambda s: s.get("id") == gate_id, f"id: {gate_id}"
        )
        retry_i, retry = step_index(
            filename, job, lambda s: s.get("id") == retry_id, f"id: {retry_id}"
        )
        backoffs = [
            (i, s, sleep_seconds(s))
            for i, s in enumerate(steps(filename, job))
            if gate_i < i < retry_i and sleep_seconds(s) is not None
        ]
        self.assertTrue(
            backoffs,
            f"{filename}: no backoff step between {gate_id!r} and "
            f"{retry_id!r} — the retry re-runs immediately against a failure "
            "that has not had time to clear",
        )
        i, step, seconds = backoffs[0]
        self.assertGreaterEqual(
            seconds, MIN_BACKOFF_SECONDS,
            f"{filename}: backoff before {retry_id!r} is {seconds}s, "
            f"below the {MIN_BACKOFF_SECONDS}s floor",
        )
        # It must be gated exactly like the retry it guards, or it sleeps on
        # every healthy run and burns the job's timeout budget for nothing.
        self.assertEqual(
            str(step.get("if")), str(retry.get("if")),
            f"{filename}: the backoff before {retry_id!r} must carry the "
            "same `if` as the retry — otherwise every successful run sleeps",
        )

    def test_verify_backs_off_before_the_verifier_retry(self):
        self.assert_backoff_between(
            "verify.yml", "verify", "gate1", "verifier_retry"
        )

    def test_qa_review_backs_off_before_the_critic_retry(self):
        self.assert_backoff_between(
            "qa-review.yml", "review", "gate1", "critic_retry"
        )


class QaReviewKeepsItsOwnQuotaBucketTest(unittest.TestCase):
    """DRE-1921, re-pinned. qa-review.yml mints its agent token from the
    qa-bot App, whose installation has its OWN 5,000 req/hr bucket. That is
    why every QA Review run on agent-bureau PR #2045 succeeded while Verify
    died on installation 123249480.

    Pooling this workflow would be a regression, not a fix: it would move
    the critic off a healthy dedicated bucket onto the contended one. This
    test exists so the next person reading 'the pool is wired into only a
    few workflows' does not 'finish the job' here.
    """

    def test_critic_token_is_minted_from_the_qa_app(self):
        _, app = step_index(
            "qa-review.yml", "review",
            lambda s: s.get("id") == "app", "id: app",
        )
        self.assertIn(
            "BUREAU_QA_APP_ID", (app.get("with") or {}).get("app-id", ""),
            "qa-review's agent token must stay on the qa-bot App's separate "
            "quota bucket (DRE-1921)",
        )

    def test_both_critic_runs_use_the_qa_bucket_token(self):
        agent_steps = [
            s for s in steps("qa-review.yml", "review")
            if "claude-code-action" in (s.get("uses") or "")
        ]
        self.assertEqual(len(agent_steps), 2)
        for s in agent_steps:
            self.assertEqual(
                (s.get("with") or {}).get("github_token"),
                "${{ steps.app.outputs.token }}",
            )

    def test_qa_review_declares_no_dispatch_pool_secrets(self):
        declared = workflow_call_secrets("qa-review.yml")
        for n in POOL_SLOTS:
            self.assertNotIn(
                f"BUREAU_APP_ID_{n}", declared,
                "qa-review.yml must not join the dispatch pool — it has its "
                "own bucket (DRE-1921)",
            )


if __name__ == "__main__":
    unittest.main()
