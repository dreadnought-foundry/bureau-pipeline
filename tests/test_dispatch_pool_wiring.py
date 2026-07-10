"""Dispatch-pool wiring (DRE-2013): agent-task.yml must run the selector
BEFORE the worker-token mint, and explicitly map EVERY pool slot it
advertises to its secret pair.

Unit tests on dispatch_pool.py can't catch a workflow that forgot to run the
selector, minted the worker token before selection, or advertised a slot in
the selector env without mapping its secrets into the mint (Actions can't
index secrets dynamically, so each N needs an explicit clause — a missed one
silently routes that slot to the wrong app). These pin the YAML.

Card: 'a wiring test asserting agent-task.yml runs the selector before the
mint and maps every N it advertises'.
"""

import os
import re
import unittest

import yaml

WF = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "agent-task.yml"
)


def load():
    with open(WF) as f:
        return yaml.safe_load(f)


def steps():
    return load()["jobs"]["execute"]["steps"]


def step_index(pred, label):
    for i, s in enumerate(steps()):
        if pred(s):
            return i, s
    raise AssertionError(f"agent-task.yml has no step: {label}")


def selector_step():
    return step_index(
        lambda s: "dispatch_pool.py select" in (s.get("run") or ""),
        "selector (dispatch_pool.py select)",
    )


def worker_mint_step():
    return step_index(
        lambda s: s.get("id") == "worker"
        and "create-github-app-token" in (s.get("uses") or ""),
        "worker mint (id: worker, create-github-app-token)",
    )


def advertised_slots():
    """Every pool slot N >= 2 the selector step's env advertises."""
    _, sel = selector_step()
    env = sel.get("env") or {}
    slots = set()
    for name in env:
        m = re.match(r"^BUREAU_(?:APP_ID|POOL_TOKEN)_([0-9]+)$", name)
        if m:
            slots.add(int(m.group(1)))
    return sorted(slots)


class DispatchPoolWiringTest(unittest.TestCase):
    def test_pool_slots_are_advertised(self):
        # The 4-app pool = original + 2/3/4 (App IDs 4266537/4266538/4266539).
        self.assertEqual(advertised_slots(), [2, 3, 4])

    def test_selector_runs_before_the_worker_mint(self):
        # Card: 'runs the selector before the mint'.
        sel_i, _ = selector_step()
        mint_i, _ = worker_mint_step()
        self.assertLess(sel_i, mint_i)

    def test_selector_runs_after_the_pipeline_checkout(self):
        # dispatch_pool.py lives in the .bureau-pipeline checkout — the
        # selector can only run once that checkout exists.
        co_i, _ = step_index(
            lambda s: (s.get("with") or {}).get("path") == ".bureau-pipeline",
            "bureau-pipeline checkout",
        )
        sel_i, _ = selector_step()
        self.assertGreater(sel_i, co_i)

    def test_selector_gets_tokens_ids_and_hash_key(self):
        _, sel = selector_step()
        env = sel.get("env") or {}
        # Slot 1 = the original app (probe with the already-minted boot token).
        self.assertIn("BUREAU_APP_ID", env)
        self.assertIn("BUREAU_POOL_TOKEN", env)
        # Deterministic fallback key: the card id (run id when absent).
        self.assertIn("BUREAU_POOL_KEY", env)
        self.assertIn("client_payload.identifier", env["BUREAU_POOL_KEY"])
        for n in advertised_slots():
            self.assertIn(f"BUREAU_APP_ID_{n}", env)
            self.assertIn(f"BUREAU_POOL_TOKEN_{n}", env)
            self.assertIn(
                f"steps.probe_{n}.outputs.token", env[f"BUREAU_POOL_TOKEN_{n}"]
            )

    def test_every_advertised_slot_has_a_guarded_probe_mint(self):
        # Absent secrets must SKIP the probe (pool shrinks), and a broken
        # pool app must never fail the build (continue-on-error).
        sel_i, _ = selector_step()
        for n in advertised_slots():
            i, probe = step_index(
                lambda s, n=n: s.get("id") == f"probe_{n}", f"probe_{n}"
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
        # Card: 'maps every N it advertises' — Actions can't index secrets
        # dynamically, so the mint needs an explicit clause per N.
        _, mint = worker_mint_step()
        with_ = mint.get("with") or {}
        for n in advertised_slots():
            self.assertIn(f"secrets.BUREAU_APP_ID_{n}", with_.get("app-id", ""))
            self.assertIn(
                f"secrets.BUREAU_APP_PRIVATE_KEY_{n}",
                with_.get("private-key", ""),
            )
        # The map is driven by the selector's output.
        self.assertIn("steps.pool.outputs.n", with_.get("app-id", ""))
        self.assertIn("steps.pool.outputs.n", with_.get("private-key", ""))

    def test_worker_mint_falls_back_to_the_original_app(self):
        # Graceful degradation (card AC 3): with no pool secrets — or any
        # unexpected selector output — the chain must end at the original
        # pair, exactly as today.
        _, mint = worker_mint_step()
        with_ = mint.get("with") or {}
        self.assertRegex(
            with_.get("app-id", ""), r"\|\|\s*secrets\.BUREAU_APP_ID\s*}}\s*$"
        )
        self.assertRegex(
            with_.get("private-key", ""),
            r"\|\|\s*secrets\.BUREAU_APP_PRIVATE_KEY\s*}}\s*$",
        )

    def test_pool_secrets_declared_optional_on_workflow_call(self):
        # A stub that doesn't (yet) pass the new secrets must stay valid:
        # required: false for every advertised pair.
        wf = load()
        on = wf.get("on") or wf.get(True)  # pyyaml parses bare `on:` as True
        declared = on["workflow_call"]["secrets"]
        for n in advertised_slots():
            for name in (f"BUREAU_APP_ID_{n}", f"BUREAU_APP_PRIVATE_KEY_{n}"):
                self.assertIn(name, declared, f"{name} must be declared")
                self.assertFalse(
                    (declared[name] or {}).get("required", False),
                    f"{name} must be optional (graceful degradation)",
                )

    def test_work_steps_use_the_selected_worker_token(self):
        # The quota-heavy steps must consume the SELECTED app's token, or the
        # whole pool is dead weight: the agent run plus the gate/report gh
        # calls. (Checkout keeps the boot token — it runs before selection.)
        _, claude = step_index(
            lambda s: "claude-code-action" in (s.get("uses") or ""),
            "claude-code-action",
        )
        self.assertEqual(
            (claude.get("with") or {}).get("github_token"),
            "${{ steps.worker.outputs.token }}",
        )
        for name in ("Gate on agent result", "Report result to Linear"):
            _, s = step_index(lambda s, name=name: s.get("name") == name, name)
            self.assertEqual(
                (s.get("env") or {}).get("GH_TOKEN"),
                "${{ steps.worker.outputs.token }}",
            )


if __name__ == "__main__":
    unittest.main()
