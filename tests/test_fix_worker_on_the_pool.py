"""Every model step in the three workflows that BUILD, REVIEW and FIX spends a
token minted from the SELECTED dispatch-pool app (DRE-4412).

THE INCIDENT (2026-09-20, dreadnought-foundry/portico). Agent Fix died twice
within three minutes on two unrelated pull requests — #620 (DRE-4394) and #623
(DRE-4410, run 35535838508) — inside `claude-code-action`'s prepare step,
before any model turn:

    ##[error]Action failed with error: API rate limit exceeded for
    installation ID 123249480

Installation 123249480 is the FIRST worker bot's. `agent-fix.yml` already
probed all four pool bots and selected the one with headroom (DRE-4282,
`Select dispatch-pool app`), and already minted a READ token from that
selection — and then handed its one expensive, write-capable step the job-start
token of bot 1, unconditionally. The pool protected the run's reads and left
the model on the single busiest identity. #623 merged later the same afternoon
once the hour rolled over; nothing was lost but time, and it recurs every time
that one bot's hour is spent.

THE RULE, stated generally and applied to all three at once:

  in `agent-task.yml`, `agent-fix.yml` and `qa-review.yml`, no
  `anthropics/claude-code-action` step is handed a `github_token` minted from
  anything but the selected pool app.

Two of the three already complied on the day this was written — `agent-task.yml`
through `steps.worker`, `qa-review.yml` through `steps.reader` — so this suite
passes for them and a later regression in either is caught by the same
assertions rather than by a second card. That is the point of stating it over
the set: the fix workflow was moved onto the pool half way, and nothing said so.

"Minted from the selected pool app" is checked as a property of the MINT the
token comes from, never as a step id: the mint reads `steps.pool.outputs.n`,
maps every advertised slot to that slot's own secret pair, and falls through to
the workflow's OWN original pair — the qa-bot App for `qa-review.yml`
(DRE-1921's separate bucket stays its slot 1), the worker App for the other
two. The fall-through is EVALUATED over the real `${{ }}` chain rather than
matched by shape, so "a pool selection that failed or chose slot 1 behaves
exactly as today" is a computed answer.

What this suite deliberately does NOT touch, because each is a different
question with its own pin:

  * WHO MAY SET OFF a fix run — `allowed_bots: "agent-bureau-qa-bot,
    github-actions"` and the job-level comment-author gate (DRE-1988). That
    lock is about the TRIGGERING actor; this rule is about which credential the
    run's model step USES once it has been triggered. The two are independent.
    `tests/test_worker_pool_allowed_bots.py` and
    `tests/test_agent_fix_identity_gate.py` own the lock, unmodified.
  * The re-mint before `Report` (DRE-4320) and the identity the fix loop writes
    its attempt markers as — `tests/test_agent_fix_token_remint.py` and
    `tests/test_readers_on_the_pool.py` own those. The identity the fix commit
    is PUSHED under is owned by nobody and cannot be: it is
    claude-code-action's runtime behaviour, not a declared input, and it has
    been observed both ways on build runs with this same arrangement. It may
    be any pool member — accepted, see test_agent_fix_token_remint.py.

These read the LIVE workflow YAML (the test_readers_on_the_pool.py pattern — no
copied fixtures) and must FAIL on the tree before the wiring lands.

Run: python3 -m pytest tests/test_fix_worker_on_the_pool.py -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))

import dispatch_pool  # noqa: E402

MINT = "actions/create-github-app-token"
MODEL = "anthropics/claude-code-action"
POOL_SLOTS = ("2", "3", "4")
SELECTOR = "dispatch_pool.py select"

#: The three workflows that run a model on the volume: build, review, fix.
#: Each maps to its job and to the App pair that is its OWN pool slot 1 — the
#: identity every pool mint in that file falls through to.
MODEL_WORKFLOWS = {
    "agent-task.yml": ("execute", ("BUREAU_APP_ID", "BUREAU_APP_PRIVATE_KEY")),
    "agent-fix.yml": ("fix", ("BUREAU_APP_ID", "BUREAU_APP_PRIVATE_KEY")),
    # DRE-1921 gave the critic its own bucket and that bucket stays the
    # fallback; the pool is three more on top of it (DRE-4282).
    "qa-review.yml": ("review", ("BUREAU_QA_APP_ID", "BUREAU_QA_APP_PRIVATE_KEY")),
}

#: The build workflow's worker mint is the shape the fix workflow copies.
REFERENCE = "agent-task.yml"

_TOKEN = re.compile(r"^\$\{\{\s*steps\.([A-Za-z0-9_-]+)\.outputs\.token\s*\}\}$")
_CLAUSE = re.compile(r"steps\.pool\.outputs\.n\s*==\s*'([^']*)'\s*&&\s*(\S+)")


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def steps(name: str) -> list[dict]:
    return load(name)["jobs"][MODEL_WORKFLOWS[name][0]]["steps"]


def action(step: dict) -> str:
    return str(step.get("uses") or "").split("@")[0]


def label(i: int, step: dict) -> str:
    return f"step {i} {step.get('name') or step.get('id') or step.get('uses')!r}"


def model_steps(name: str) -> list[tuple[int, dict]]:
    return [(i, s) for i, s in enumerate(steps(name)) if action(s) == MODEL]


def index_of_id(name: str, step_id: str) -> int:
    for i, step in enumerate(steps(name)):
        if step.get("id") == step_id:
            return i
    raise AssertionError(f"{name} has no step with id {step_id!r}")


def selector_index(name: str) -> int:
    for i, step in enumerate(steps(name)):
        if SELECTOR in (step.get("run") or ""):
            return i
    raise AssertionError(f"{name} never runs the dispatch-pool selector")


def model_token(step: dict) -> str:
    return str((step.get("with") or {}).get("github_token", ""))


def minting_step(name: str, step: dict) -> tuple[int, dict]:
    """The mint the model step's `github_token` comes from."""
    m = _TOKEN.fullmatch(model_token(step).strip())
    assert m, (
        f"{name}: a model step's github_token must be one step's minted token, "
        f"got {model_token(step)!r}"
    )
    i = index_of_id(name, m.group(1))
    return i, steps(name)[i]


def render_chain(expression: str, n: str) -> str:
    """What GitHub hands the mint for input `expression` when the selector
    wrote `n` — or when it never ran (`n == ''`, a skipped step's output).

    The chain is `cond && value || cond && value || fallback`: `&&` binds
    tighter than `||`, a false `cond` makes its clause '' (falsy), and the
    first truthy clause wins. Evaluated rather than pattern-matched, so the
    test says what the RENDERED input is (test_readers_on_the_pool.py's
    helper, applied here to the mints that feed model steps).
    """
    body = expression.strip()
    assert body.startswith("${{") and body.endswith("}}"), expression
    clauses = [c.strip() for c in body[3:-2].strip().split("||")]
    for clause in clauses[:-1]:
        m = _CLAUSE.fullmatch(clause)
        assert m, f"unrecognised clause {clause!r} in {expression!r}"
        if m.group(1) == n:
            return m.group(2)
    return clauses[-1]


def flat(text: str) -> str:
    """A `${{ }}` chain with its folding whitespace normalised away."""
    return " ".join(str(text).split())


class EveryModelStepMintsFromTheSelectedPoolApp(unittest.TestCase):
    """The rule, over the three workflows at once."""

    def test_each_workflow_actually_runs_a_model(self):
        # The rule below is vacuous if extraction finds nothing — a renamed
        # job or a renamed vendor action would make it pass by finding no
        # model steps at all. Counted rather than pinned: today it is three in
        # agent-task (the build agent and its two retries), two in qa-review
        # (the critic and its retry) and one in agent-fix, and a workflow that
        # grows another is covered by the same assertions without editing this.
        found = {name: len(model_steps(name)) for name in MODEL_WORKFLOWS}
        self.assertTrue(all(n >= 1 for n in found.values()), found)

    def test_no_model_step_is_handed_a_token_from_outside_the_pool(self):
        # THE regression pin. On the tree before this card, agent-fix.yml's
        # `Fix` names `steps.app` — the boot mint, installation 123249480,
        # unconditionally — and this names it.
        offenders = []
        for name in MODEL_WORKFLOWS:
            for i, step in model_steps(name):
                mint_i, mint = minting_step(name, step)
                with_ = mint.get("with") or {}
                if "steps.pool.outputs.n" not in flat(yaml.safe_dump(with_)):
                    offenders.append(
                        f"{name}: {label(i, step)} spends "
                        f"`{mint.get('id')}`, minted without asking the pool"
                    )
        self.assertEqual(
            offenders, [],
            "a model step on a fixed identity dies whenever that one bot's "
            "hour is spent, whatever room the rest of the pool has:\n"
            + "\n".join(offenders),
        )

    def test_that_mint_is_a_github_app_token_mint_that_runs_in_between(self):
        for name in MODEL_WORKFLOWS:
            for i, step in model_steps(name):
                with self.subTest(workflow=name, step=step.get("name")):
                    mint_i, mint = minting_step(name, step)
                    self.assertEqual(action(mint), MINT, mint.get("id"))
                    self.assertGreater(mint_i, selector_index(name), "mint before selection")
                    self.assertLess(mint_i, i, "the model step reads a later mint")
                    self.assertFalse(
                        mint.get("continue-on-error"),
                        "a model credential that cannot mint should go red AT the mint",
                    )

    def test_the_mint_maps_every_advertised_slot_to_that_slots_own_pair(self):
        for name in MODEL_WORKFLOWS:
            for _i, step in model_steps(name):
                with self.subTest(workflow=name, step=step.get("name")):
                    _mint_i, mint = minting_step(name, step)
                    with_ = mint.get("with") or {}
                    for n in POOL_SLOTS:
                        self.assertEqual(
                            render_chain(with_["app-id"], n), f"secrets.BUREAU_APP_ID_{n}"
                        )
                        self.assertEqual(
                            render_chain(with_["private-key"], n),
                            f"secrets.BUREAU_APP_PRIVATE_KEY_{n}",
                        )


class TheFallThroughIsExactlyTodayTest(unittest.TestCase):
    """When the selection fails or selects slot 1, the run behaves exactly as
    it does now — DRE-2013's graceful degradation, evaluated over the real
    chain rather than asserted from its shape."""

    def test_no_pool_secrets_still_selects_slot_one(self):
        env = {"BUREAU_APP_ID": "3350400"}
        for n in POOL_SLOTS:
            env[f"BUREAU_APP_ID_{n}"] = ""  # an unset secret renders ''
        self.assertEqual(dispatch_pool.select(env), (1, "single-app"))

    def test_slot_one_and_a_selector_that_never_ran_render_the_original_pair(self):
        for name, (_job, (app_id, private_key)) in MODEL_WORKFLOWS.items():
            for _i, step in model_steps(name):
                with self.subTest(workflow=name, step=step.get("name")):
                    _mint_i, mint = minting_step(name, step)
                    with_ = mint["with"]
                    # '1' is the selector's own answer for the original app;
                    # '' is a skipped selector's empty output.
                    for n in ("1", ""):
                        self.assertEqual(
                            (
                                render_chain(with_["app-id"], n),
                                render_chain(with_["private-key"], n),
                            ),
                            (f"secrets.{app_id}", f"secrets.{private_key}"),
                            f"{name}: {mint.get('id')} with n={n!r}",
                        )

    def test_the_fall_through_is_the_last_clause_and_nothing_follows_it(self):
        # The `||` tail IS the degradation path: a chain that ended in a pool
        # slot would hand a repo with no pool secrets an empty app-id.
        for name, (_job, (app_id, private_key)) in MODEL_WORKFLOWS.items():
            for _i, step in model_steps(name):
                with self.subTest(workflow=name, step=step.get("name")):
                    _mint_i, mint = minting_step(name, step)
                    with_ = mint["with"]
                    self.assertRegex(
                        flat(with_["app-id"]), rf"\|\| secrets\.{app_id} \}}\}}$"
                    )
                    self.assertRegex(
                        flat(with_["private-key"]),
                        rf"\|\| secrets\.{private_key} \}}\}}$",
                    )


class TheFixWorkerIsTheBuildWorkersShapeTest(unittest.TestCase):
    """agent-fix.yml copies agent-task.yml's `Mint worker token (selected pool
    app)` rather than inventing a second dialect: the same per-slot map, the
    same fall-through to the original pair, the same pinned action sha. Both
    files' slot 1 is the worker App, so the inputs are the same text."""

    def _worker_mints(self) -> dict[str, dict]:
        mints = {}
        for name in ("agent-task.yml", "agent-fix.yml"):
            model = model_steps(name)[0][1]
            _i, mint = minting_step(name, model)
            mints[name] = mint
        return mints

    def test_same_inputs_as_the_build_workflows_worker_mint(self):
        mints = self._worker_mints()
        reference = {k: flat(v) for k, v in mints[REFERENCE]["with"].items()}
        self.assertEqual(
            {k: flat(v) for k, v in mints["agent-fix.yml"]["with"].items()}, reference
        )

    def test_pinned_to_the_same_action_sha(self):
        mints = self._worker_mints()
        self.assertEqual(
            mints["agent-fix.yml"].get("uses"), mints[REFERENCE].get("uses")
        )
        self.assertIn("@", str(mints[REFERENCE].get("uses")), "a floating tag is not a pin")

    def test_every_pool_mint_in_the_fix_workflow_shares_that_pin(self):
        # DRE-3418's rule, locally: the new mint must not introduce a second
        # version of the action alongside the probes and the reader.
        pins = {
            s.get("uses") for s in steps("agent-fix.yml") if action(s) == MINT
        }
        self.assertEqual(len(pins), 1, pins)


if __name__ == "__main__":
    unittest.main()
