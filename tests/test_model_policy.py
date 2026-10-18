"""Workhorse vs advisory vs judgement: the critic gets the advisory model, the
planner gets the strongest one, and a new model can only auto-promote to
advisory (DRE-2317, DRE-3015).

The inversion this closes
-------------------------
The QA critic gates EVERY unattended merge — it is the correctness backstop for
a pipeline where no human reads a diff — and it ran on the CHEAPEST model we
run, while the build agents walked a ladder whose top rung was the most
expensive one available. The cheapest model was judging the most expensive
one's work. A build failure is loud; a shallow review is silent, which makes
this the more dangerous half.

The rule: **availability is not permission.** On 2026-08-09 Anthropic enabled
Fable 5, the probe stopped returning 404, and a best-first ladder
promoted the whole fleet onto it with no human deciding anything. PR #134 took
Fable off the build path as DATA. This card makes it STRUCTURAL:

  * every role is classified `workhorse` (high-volume build work) or `advisory`
    (bounded consults at decision points — the critic and verifier), and a role
    may only be assigned one of those two KINDS, never an arbitrary ladder;
  * the strongest model — the top of the JUDGEMENT ladder — is unreachable from
    any workhorse ladder, pinned here at every availability, which is the
    incident condition;
  * a model the system discovers may at most join the ADVISORY ladder, and
    `on_new_model: workhorse` is REJECTED by schema validation — that value is
    precisely the incident;
  * every run records the model it used AND why anything above it was skipped,
    so a weakened advisory model can never be silent.

The third kind (DRE-3015)
-------------------------
`judgement` holds the planner alone. It runs ONCE PER EPIC, at a decision
point, and every downstream build run is shaped by its output — an
advisory-shaped cost profile that wore a workhorse label. It walks a ladder of
its own, strongest model first, and falling off that top rung is the LOUD
(DEGRADED-prefixed) fallback the advisory ladder already does.

What DID NOT change is the 08-09 guard, and it is pinned here from BOTH sides:
no build role's ladder can reach the judgement top at any availability, the
planner's can, and a newly-discovered model may never land on either the build
or the planning ladder by itself — `discovery.on_new_model: judgement` is
refused exactly as `workhorse` is. A human editing config/models.yaml is the
only way up.

The advisory/workhorse overlap (DRE-3880, CEO decision 2026-09-16)
------------------------------------------------------------------
A Sonnet model may top the advisory ladder AND back the workhorse one, so the
build/review fence is no longer bought by keeping the two lists disjoint — it
is bought at SELECTION time, per pull request, and the schema permits the
overlap only together with that rule. The tests for it live in
tests/test_review_separation.py; what stays here is everything that did not
change, above all the 2026-08-09 guard: the strongest model we run is still
unreachable from a build ladder at any availability.

Which file each test reads (DRE-6860, DRE-6988)
-----------------------------------------------
The console changes a choice on every save — which model leads a group, which
group an agent is in, which agents exist — so no test here asserts a choice
that is in force today. Since the selector selects on the home (DRE-6939), a
scenario about a CHOICE is a home document built from
`model_fixture.fixture_home()`, judged by `model_home.home_errors` and handed
to a tree's selector through `home_env` or `fixture_module(..., doc=...)`. A
rule about a CODE-SIDE fact — `discovery`, `excluded`, `retired`,
`session_agents`, `review_separation` — is proved by `policy_errors` on a copy
of the frozen `models.yaml`. Every model id and every role a test expects is
read off the frozen document through `model_fixture.ladder`, never from a
literal. A test of the COMMITTED file checks only what must hold in any saved
state, and derives whatever it expects from that file.

These tests are the pin. Every one of them fails if its behaviour is removed.
"""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "models.yaml"
AGENTS = ROOT / "agents.yaml"
MODEL_FALLBACK = ROOT / "scripts" / "model_fallback.py"
MODEL_CATALOG = ROOT / "scripts" / "model_catalog.py"
WORKFLOWS = ROOT / ".github" / "workflows"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import model_catalog as mc  # noqa: E402
import model_fallback as mf  # noqa: E402
import model_fixture  # noqa: E402
from model_fixture import fixture_home, home_env, lead_with, move_agent  # noqa: E402

# The predecessor Fable id, excluded from every ladder on cost policy
# (2026-08-12) and kept readable for attribution: the code's own name for it.
# It is on no ladder, so no console save can move it; the ids that ARE on a
# ladder are read off the frozen document below, never spelled here.
FABLE = mf.FABLE

# Two ids no catalog offers and no config names: a model the catalog has just
# shown us, and one we saw before.
NOVA = "claude-nova"
RELIC = "claude-relic"

WORKHORSE = "workhorse"
ADVISORY = "advisory"
JUDGEMENT = "judgement"

# Every workflow that selects a model, and the agent name it selects for.
SELECTORS = {
    "agent-task.yml": '"$ROLE"',
    "plan.yml": "planner",
    "qa-review.yml": "critic",
    "verify.yml": "verifier",
    "medic.yml": "medic",
    "agent-fix.yml": "fixer",
    "red-main-repair.yml": "repairer",
}


# Every file that states the role-kind rule in prose a human acts on — config,
# the selector and the catalog, the roster, and the workflows that select.
# The staleness sweeps below read all of them, so a new kind cannot leave a
# sentence behind in one of them.
KIND_DOCUMENTS = (
    CONFIG,
    ROOT / "config" / "README.md",
    MODEL_FALLBACK,
    MODEL_CATALOG,
    AGENTS,
    WORKFLOWS / "plan.yml",
    WORKFLOWS / "agent-task.yml",
)

# One fixture tree for every rule test that only reads: today's scripts on the
# frozen model files, its selector imported as `fmf` having read the frozen
# home document, and its home reader as `fmh`. A test that runs the selector
# CLI builds a tree of its own with `_copy_tree`.
_SHARED = None
fmf = None
fmh = None


def setUpModule():
    global _SHARED, fmf, fmh
    _SHARED = tempfile.TemporaryDirectory()
    tree = model_fixture.fixture_tree(Path(_SHARED.name) / "tree")
    fmf = model_fixture.fixture_module(tree, doc=fixture_home())
    fmh = model_fixture.fixture_module(tree, "model_home")


def tearDownModule():
    _SHARED.cleanup()


def _canonical() -> dict:
    """The frozen `models.yaml`, parsed afresh — a test may edit its copy to
    break a rule `policy_errors` holds about a code-side fact."""
    return model_fixture.fixture_config()


def _home_errors(doc: dict, side: dict | None = None) -> list[str]:
    """`model_home.home_errors` on `doc`, judged as every writer judges it, by
    the fixture tree's own facts: its prices, its code-side facts (or `side`)
    and its repo map."""
    return fmh.home_errors(doc, fmh.mf.declared_prices(),
                           fmh.code_side() if side is None else side,
                           fmh.mf.declared_repo_slugs())


def _committed() -> dict:
    """The committed config/models.yaml, for the invariants every saved state
    must hold. Nothing read from it is compared to a literal."""
    return yaml.safe_load(CONFIG.read_text())


def _copy_tree(tmp: Path) -> Path:
    """A throwaway tree of today's scripts on the frozen model files."""
    return model_fixture.fixture_tree(tmp / "tree")


def _all_up() -> dict:
    """Every model of every group of the frozen document, and the excluded
    Fable, reported up."""
    doc = fixture_home()
    up = {m: True for kind in doc["groups"] for m in model_fixture.ladder(doc, kind)}
    up[FABLE] = True
    return up


def _cli_run(tree: Path, agent: str, available=None, explain=False, doc=None):
    """Run the selector CLI in `tree` on the home document `doc` (the frozen
    one when None), with a STUBBED availability probe. Returns
    `(process, explanation)` — the explanation is what the workflow records."""
    env = home_env(tree, fixture_home() if doc is None else doc,
                   BUREAU_FAKE_AVAILABLE=available if available is not None else _all_up())
    argv = [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select", agent]
    why_path = tree / "why.txt"
    if explain:
        argv += ["--explain-file", str(why_path)]
    proc = subprocess.run(argv, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"select {agent} failed: {proc.stdout}\n{proc.stderr}"
    assert "model-home: read" in proc.stderr, proc.stderr
    note = why_path.read_text().strip() if explain else ""
    return proc, note


def _cli_select(tree: Path, agent: str, available=None, explain=False, doc=None):
    """`(model, explanation)` of `_cli_run`."""
    proc, note = _cli_run(tree, agent, available, explain, doc)
    return proc.stdout.strip(), note


def _cli_check(tree: Path, doc: dict) -> subprocess.CompletedProcess:
    """`model_home.py check` in `tree` on `doc`: what every writer runs before
    it writes a document."""
    path = home_env(tree, doc)["BUREAU_MODEL_HOME_FILE"]
    return subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_home.py"), "check", path],
        capture_output=True, text=True, cwd=tree)


def _advisory_ladder(doc=None) -> list:
    return model_fixture.ladder(doc or fixture_home(), ADVISORY)


def _workhorse_ladder(doc=None) -> list:
    return model_fixture.ladder(doc or fixture_home(), WORKHORSE)


def _judgement_ladder(doc=None) -> list:
    return model_fixture.ladder(doc or fixture_home(), JUDGEMENT)


def _rule_10(agent: str) -> str:
    """The sentence `home_errors` refuses `agent` in the judgement group with."""
    owners = " and ".join(fmf.JUDGEMENT_ROLES)
    return (f"Only {owners} and the session agents may be in the {JUDGEMENT} "
            f"group, and {agent} is in it.")


def _roles_of(kind: str) -> list:
    """The fixture's PIPELINE roles of `kind`, in file order — a session agent
    selects nothing, so it is never one of them."""
    return [role for role, k in fmf.AGENT_KINDS.items() if k == kind]


def _role_of(kind: str) -> str:
    roles = _roles_of(kind)
    assert roles, f"the fixture has no pipeline role of kind {kind!r}"
    return roles[0]


class RoleKindsTest(unittest.TestCase):
    """Three role kinds, and every role is classified into one of them."""

    def setUp(self):
        fmf.clear_availability_cache()

    def test_config_declares_the_three_kinds_and_any_added_group_on_its_own_ladder(self):
        # The committed file, in any saved state: the kinds the code knows,
        # each on a ladder of its own. Since DRE-6834 a save may add a group
        # beside them, and an added group walks the ladder of its own name.
        cfg = _committed()
        self.assertTrue(
            set(mf.ROLE_KINDS) <= set(cfg["kinds"]),
            "config/models.yaml declares every role kind the selector knows",
        )
        for kind in set(cfg["kinds"]) - set(mf.ROLE_KINDS):
            self.assertEqual(cfg["kinds"][kind]["ladder"], kind, kind)
        ladders = {kind: spec["ladder"] for kind, spec in cfg["kinds"].items()}
        for kind, ladder in ladders.items():
            self.assertIn(ladder, cfg["ladders"], f"kind {kind}: unknown ladder")
        self.assertEqual(
            len(set(ladders.values())), len(ladders),
            "each kind gets its OWN ladder — sharing one is how build work "
            "ends up on a model nobody chose for it",
        )

    def test_every_agent_role_is_classified(self):
        cfg = _committed()
        # A scripted entry (`model: null`, DRE-5369) runs no model and has no
        # kind to classify; test_agents_registry.py holds it to that claim.
        registry = {a["name"] for a in yaml.safe_load(AGENTS.read_text())["agents"]
                    if not ("model" in a and a["model"] is None)}
        # Every registry agent has a kind, and every name under `agents` is in
        # the registry or is a session agent — one that has a group for the
        # console and is never a pipeline role (DRE-6833).
        session = set(cfg.get("session_agents") or [])
        self.assertLessEqual(
            registry, set(cfg["agents"]),
            "every agents.yaml role needs a kind in config/models.yaml",
        )
        self.assertLessEqual(
            set(cfg["agents"]), registry | session,
            "every name under `agents` is in agents.yaml or in `session_agents`",
        )
        for role, kind in cfg["agents"].items():
            self.assertIn(kind, cfg["kinds"], f"{role}: {kind!r} is not a role kind")

    def test_the_critic_and_verifier_may_be_nothing_but_advisory(self):
        # Rule 3: the roles that gate unattended merges are advisory, and a
        # document putting either of them in any other group is refused.
        self.assertIn("critic", fmf.BACKSTOP_ROLES)
        self.assertIn("verifier", fmf.BACKSTOP_ROLES)
        for role in fmf.BACKSTOP_ROLES:
            self.assertEqual(fmf.kind_for(role), ADVISORY, role)
            for kind in (WORKHORSE, JUDGEMENT):
                with self.subTest(role=role, kind=kind):
                    errors = _home_errors(move_agent(fixture_home(), role, kind))
                    self.assertIn(
                        f"The {role} must be in the {ADVISORY} group, because it "
                        f"reviews before an unattended merge, and it is in {kind}.",
                        errors, f"{role} moved to {kind} was not refused",
                    )

    def test_the_critic_runs_the_advisory_model(self):
        # The critic resolves to the advisory ladder's top rung from config —
        # no hardcoded string anywhere.
        #
        # RENAMED 2026-08-12 (was ..._runs_the_strongest_model). The advisory
        # ladder moved off Fable on measured cost, so the advisory model is no
        # longer the strongest thing we can run, and a test asserting it is
        # would pin a premise we deliberately dropped.
        self.assertEqual(fmf.select("critic", probe=lambda m: True),
                         _advisory_ladder()[0])

    def test_the_excluded_model_is_on_no_ladder_at_all(self):
        # What survives the rename above. Fable came OFF every ladder rather
        # than moving down one, so the 2026-08-09 guarantee is now "excluded
        # means unreachable" — enforced by policy rule 7 rather than implied by
        # Fable happening to be the advisory model. Held over the COMMITTED
        # file: whatever it excludes is on none of its ladders.
        excluded = mf.CONFIG["excluded"]
        self.assertIn(FABLE, excluded)
        for model in excluded:
            for name, models in mf.CONFIG["ladders"].items():
                with self.subTest(model=model, ladder=name):
                    self.assertNotIn(model, models, "an excluded model reached a ladder")

    def test_an_unknown_role_gets_the_workhorse_kind(self):
        # select() must never block a build on a role it does not recognize —
        # and an unrecognized role must land on the CHEAP side of the fence.
        self.assertEqual(fmf.kind_for("no-such-role"), WORKHORSE)
        self.assertEqual(fmf.KINDS[WORKHORSE], fmf.CONFIG["default_ladder"])
        self.assertEqual(fmf.ladder_for("no-such-role"), _workhorse_ladder())

    def test_advisory_falls_back_onto_the_workhorse_model(self):
        # AC: exhaustion of the advisory model drops to the ladder's last rung,
        # the fixture's workhorse model — never to something weaker still, and
        # never silently (see the note tests below).
        advisory, workhorse = _advisory_ladder(), _workhorse_ladder()
        self.assertEqual(advisory[-1], workhorse[0])
        last = advisory[-1]
        decision = fmf.select_with_reasons(
            _role_of(ADVISORY), probe=lambda m: m == last)
        self.assertEqual(decision["model"], workhorse[0])
        self.assertEqual([s["model"] for s in decision["skipped"]], advisory[:-1])
        self.assertTrue(fmf.selection_note(decision).startswith("DEGRADED"))


class IncidentConditionTest(unittest.TestCase):
    """The pinned incident: every model available, and the build path STILL
    cannot reach the strongest model."""

    def setUp(self):
        fmf.clear_availability_cache()

    def tearDown(self):
        fmf.clear_availability_cache()

    def test_no_build_role_reaches_the_strongest_model_at_full_availability(self):
        # The incident condition, on the model it was actually about: the
        # STRONGEST thing we run — the judgement ladder's top rung — must be
        # unreachable from the build path however available it looks.
        #
        # RESCOPED 2026-09-16 (DRE-3880). This used to read the ADVISORY top
        # and that is no longer the same question: a Sonnet tops the advisory
        # ladder and backs the workhorse one on purpose, and the build/review
        # fence it used to imply is now enforced per pull request at selection
        # time (tests/test_review_separation.py). What the 2026-08-09 rule was
        # ever about is a model promoting ITSELF onto the hot path, and that is
        # the strongest rung, which the build path still cannot see.
        strongest = _judgement_ladder()[0]
        everything_available = lambda model: True  # noqa: E731 — the probe 404s nothing
        build_roles = _roles_of(WORKHORSE)
        self.assertTrue(build_roles, "there are build roles to check")
        for role in build_roles:
            with self.subTest(role=role):
                fmf.clear_availability_cache()
                self.assertNotIn(
                    strongest, fmf.ladder_for(role),
                    f"{role}'s ladder contains the strongest model",
                )
                self.assertNotEqual(
                    fmf.select(role, probe=everything_available), strongest,
                    f"{role} selected the strongest model — this is the "
                    "2026-08-09 incident",
                )
        # An unrecognized role walks the default ladder — same guarantee.
        self.assertNotEqual(
            fmf.select("no-such-role", probe=everything_available), strongest
        )

    def test_the_advisory_overlap_is_separated_at_selection_time(self):
        # The other half of what DRE-3880 changed. A model on BOTH the
        # advisory and a workhorse ladder is admissible, and since 2026-10-10
        # (DRE-6785) it needs no declaration: the reviewer skips the build
        # model when it is selected. Proved in full in
        # tests/test_review_separation.py and tests/test_model_choice_policy.py
        # — this is the hinge, kept beside the incident it used to be confused
        # with. The overlap is the frozen document's; the fence is a code-side
        # fact of the frozen models.yaml, judged by `policy_errors`.
        overlap = set(_advisory_ladder()[:1]) & set(_workhorse_ladder())
        self.assertTrue(overlap, "the fixture's advisory top does not overlap the build path")
        self.assertEqual(_home_errors(fixture_home()), [])
        cfg = _canonical()
        self.assertEqual(fmf.policy_errors(cfg), [])
        stripped = _canonical()
        stripped.pop("review_separation", None)
        self.assertEqual(fmf.policy_errors(stripped), [])
        top = _advisory_ladder()[0]
        self.assertNotEqual(fmf.reviewers_for(stripped, top)[0], top)

    def test_the_cli_path_holds_the_same_line(self):
        # The workflows call the CLI, not the function. Probe says EVERYTHING
        # is available; the build path still resolves to the workhorse model,
        # and the two non-build kinds each reach their own top rung — the
        # planner's being the one this file spends most of its words guarding.
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            for kind in (WORKHORSE, JUDGEMENT, ADVISORY):
                with self.subTest(kind=kind):
                    self.assertEqual(
                        _cli_select(tree, _role_of(kind))[0],
                        model_fixture.ladder(fixture_home(), kind)[0],
                    )
            self.assertNotEqual(
                _cli_select(tree, _role_of(WORKHORSE))[0], _judgement_ladder()[0])

    def test_availability_still_only_walks_down(self):
        # The other half of the rule: a probe may decide how far DOWN a ladder
        # we walk, never how far up. The top rung down and the one below it up
        # lands on the one below; the top up lands on the top, whatever is
        # below it.
        role, workhorse = _role_of(WORKHORSE), _workhorse_ladder()
        top, below = workhorse[0], workhorse[1]
        self.assertEqual(fmf.select(role, probe=lambda m: m != top), below)
        fmf.clear_availability_cache()
        self.assertEqual(fmf.select(role, probe=lambda m: m != below), top)


class JudgementKindTest(unittest.TestCase):
    """The third kind (DRE-3015): the planner runs on the strongest model, and
    the 2026-08-09 guard is pinned from BOTH sides — no build role's ladder can
    reach the judgement top at any availability, the planner's can."""

    def setUp(self):
        fmf.clear_availability_cache()

    def tearDown(self):
        fmf.clear_availability_cache()

    def test_only_a_judgement_role_holds_the_judgement_kind(self):
        # The committed file, in any saved state: of the PIPELINE-run roles
        # (DRE-6889), only one rule 10 names may be in Planners — or none, the
        # planner moving to a cheaper kind is admissible. A session-run agent
        # the CEO put in Planners selects nothing from it (rule 10 exempts it).
        cfg = _committed()
        session = set(cfg.get("session_agents") or [])
        holders = {r for r, k in cfg["agents"].items()
                   if k == JUDGEMENT and r not in session}
        self.assertLessEqual(
            holders, set(mf.JUDGEMENT_ROLES),
            "judgement is for judgement-heavy, LOW-VOLUME roles; a role that "
            "runs per card belongs on the workhorse ladder",
        )

    def test_rule_10_exempts_a_session_agent_and_nobody_else(self):
        # DRE-6833. A session agent's group selects nothing — the selector
        # answers it as an unknown name — so a session agent in Planners puts
        # no run on the strongest model, and rule 10 does not apply to it.
        # The frozen document has every session agent in no group, so the
        # move below is the one under test; which agents are session agents
        # is a code-side fact of the tree.
        side = fmh.code_side()
        self.assertTrue(side["session_agents"], "the fixture has a session agent")
        agent = side["session_agents"][0]
        self.assertNotIn(agent, fixture_home()["agents"])

        exempt = move_agent(fixture_home(), agent, JUDGEMENT)
        self.assertEqual(_home_errors(exempt), [])

        not_session = dict(side, session_agents=[
            name for name in side["session_agents"] if name != agent])
        self.assertIn(_rule_10(agent), _home_errors(exempt, side=not_session))

    def test_rule_10_still_refuses_every_pipeline_role(self):
        # Every pipeline role of the fixture that rule 10 does not name.
        others = [r for r in fmf.AGENT_KINDS if r not in fmf.JUDGEMENT_ROLES]
        self.assertTrue(others)
        for role in others:
            with self.subTest(role=role):
                errors = _home_errors(move_agent(fixture_home(), role, JUDGEMENT))
                self.assertIn(_rule_10(role), errors)
        # A name in neither JUDGEMENT_ROLES nor the session agents.
        errors = _home_errors(move_agent(fixture_home(), "stranger", JUDGEMENT))
        self.assertIn(_rule_10("stranger"), errors)

    def test_a_session_agents_entry_for_nobody_is_refused(self):
        # A code-side fact: which agents run in a person's session is the
        # frozen models.yaml's, and `policy_errors` refuses a record for
        # nobody in the words it always has.
        trial = _canonical()
        trial["session_agents"].append("nobody-at-all")
        self.assertEqual(fmf.policy_errors(trial), [
            "session_agents.nobody-at-all: names no agent in `agents` or "
            "`ungrouped` — a record for nobody is a typo or an agent that was "
            "removed"])

    def test_the_planner_runs_on_the_strongest_model_when_it_is_available(self):
        planner = _role_of(JUDGEMENT)
        self.assertIn(planner, fmf.JUDGEMENT_ROLES)
        self.assertEqual(fmf.select(planner, probe=lambda m: True),
                         _judgement_ladder()[0])

    def test_the_planner_reaches_the_strongest_model_at_every_availability_that_allows_it(self):
        # The other side of the 08-09 pin. Whatever the rest of the ladder is
        # doing, an available judgement top is the planner's model — that is
        # the whole decision, and a config edit that quietly stopped honouring
        # it would look exactly like a healthy run.
        planner = _role_of(JUDGEMENT)
        top, *below = _judgement_ladder()
        for down in range(len(below) + 1):
            avail = {top: True, **{m: i >= down for i, m in enumerate(below)}}
            with self.subTest(avail=avail):
                fmf.clear_availability_cache()
                self.assertEqual(
                    fmf.select(planner, probe=lambda m: avail.get(m, True)), top)

    def test_falling_off_the_strongest_model_is_the_loud_fallback(self):
        # Exactly as the advisory ladder does it: DEGRADED-prefixed, naming
        # what was skipped and why, on one line the workflow turns into a
        # ::warning::. A planner that quietly ran on its fallback would be
        # indistinguishable from one that got what it was promised.
        top, fallback = _judgement_ladder()[:2]
        decision = fmf.select_with_reasons(_role_of(JUDGEMENT), probe=lambda m: m != top)
        self.assertEqual(decision["model"], fallback)
        self.assertEqual(decision["kind"], JUDGEMENT)
        self.assertEqual([s["model"] for s in decision["skipped"]], [top])
        note = fmf.selection_note(decision)
        self.assertEqual(len(note.splitlines()), 1)
        self.assertTrue(note.startswith("DEGRADED"), f"the fallback is silent: {note}")
        self.assertIn(top, note)
        self.assertIn(JUDGEMENT, note)

    def test_no_workhorse_role_reaches_the_strongest_model_at_any_availability(self):
        # THE GUARD FROM 2026-08-09, unchanged. The probe reporting the
        # judgement top up is precisely the incident condition, and it still
        # buys nothing on the build path: no workhorse role's ladder contains
        # it, so no combination of availabilities can select it — not with
        # every build rung up, nor with any run of them down from the top.
        strongest = _judgement_ladder()[0]
        build_roles = _roles_of(WORKHORSE)
        self.assertTrue(build_roles)
        self.assertFalse(set(build_roles) & set(fmf.JUDGEMENT_ROLES))
        workhorse = _workhorse_ladder()
        for role in build_roles:
            for down in range(len(workhorse) + 1):
                avail = {strongest: True,
                         **{m: i >= down for i, m in enumerate(workhorse)}}
                with self.subTest(role=role, avail=avail):
                    fmf.clear_availability_cache()
                    self.assertNotIn(strongest, fmf.ladder_for(role))
                    got = fmf.select(role, probe=lambda m: avail.get(m, True))
                    self.assertNotEqual(
                        got, strongest,
                        f"{role} reached the strongest model — this is the "
                        "2026-08-09 incident",
                    )
                    self.assertIn(got, workhorse)
        # An unrecognized role walks the default (workhorse) ladder: same line.
        fmf.clear_availability_cache()
        self.assertNotEqual(fmf.select("no-such-role", probe=lambda m: True), strongest)

    def test_the_cli_holds_that_line_too(self):
        # The workflows call the CLI, not select(). Everything up, including
        # both Fable ids.
        strongest = _judgement_ladder()[0]
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            for role in _roles_of(WORKHORSE):
                with self.subTest(role=role):
                    model = _cli_select(tree, role)[0]
                    self.assertNotEqual(model, strongest)
                    self.assertEqual(model, _workhorse_ladder()[0])
            self.assertEqual(_cli_select(tree, _role_of(JUDGEMENT))[0],
                             _judgement_ladder()[0])

    def test_the_strongest_model_on_a_build_ladder_is_a_persons_choice_since_2026_10_10(self):
        # Refused by rule 4 until the CEO's decision of 2026-10-10 (DRE-6785):
        # an explicit choice of any priced model is admissible for any group.
        # What still guards the incident is that nothing AUTOMATIC climbs — see
        # test_no_build_role_reaches_the_strongest_model_at_full_availability.
        strongest = _judgement_ladder()[0]
        self.assertEqual(_home_errors(lead_with(fixture_home(), WORKHORSE, strongest)), [])

    def test_a_dearer_model_below_the_fallback_is_a_persons_choice_too(self):
        # Rule 4b refused this until 2026-10-10; what is left of it is the
        # price, and both models here have one.
        doc = fixture_home()
        build_rung, strongest = _workhorse_ladder(doc)[0], _judgement_ladder(doc)[0]
        doc["groups"][JUDGEMENT]["models"] = [build_rung, strongest]
        self.assertEqual(_home_errors(doc), [])

    def test_the_selector_honours_a_chosen_strongest_model_on_the_build_path(self):
        # The choice is admissible, so the selector reads it from the home
        # rather than running on the defaults.
        strongest = _judgement_ladder()[0]
        doc = lead_with(fixture_home(), WORKHORSE, strongest)
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            model, _ = _cli_select(tree, _role_of(WORKHORSE), doc=doc)
            self.assertEqual(model, strongest)
            # …and the frozen document, which does not lead with it, does not.
            self.assertNotEqual(_cli_select(tree, _role_of(WORKHORSE))[0], strongest)

    def test_discovery_may_not_target_the_judgement_ladder(self):
        # A newly-discovered model may never land on a BUILD **or a PLANNING**
        # ladder by itself. `judgement` is refused exactly as `workhorse` is —
        # the planner's ladder is now the strongest one we run, which makes it
        # the most attractive place for an unattended promotion to land.
        cfg = _canonical()
        cfg["discovery"]["on_new_model"] = JUDGEMENT
        errors = fmf.policy_errors(cfg)
        self.assertTrue(errors, "on_new_model: judgement must be rejected")
        self.assertTrue(
            any("on_new_model" in e for e in errors), f"unhelpful errors: {errors}"
        )
        self.assertNotIn(JUDGEMENT, fmf.DISCOVERY_TARGETS)

    def test_the_home_check_goes_red_on_a_build_role_in_the_planners_group(self):
        # What every writer runs before it writes a document: a build role in
        # the judgement group is the planning-side half of the incident, and
        # the check exits 1 naming it in a plain sentence.
        role = _role_of(WORKHORSE)
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            proc = _cli_check(tree, move_agent(fixture_home(), role, JUDGEMENT))
            self.assertEqual(proc.returncode, 1, "a writer must refuse this document")
            self.assertIn(_rule_10(role), proc.stdout.splitlines())
            self.assertEqual(_cli_check(tree, fixture_home()).returncode, 0)

    def test_the_registry_shows_the_planners_kind_and_model(self):
        # The roster is the surface a human reads to ask "what is the planner
        # running on, and why is it allowed to be the expensive one?" — the
        # frozen tree's roster says what the frozen document says.
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            roster = {a["name"]: a
                      for a in yaml.safe_load((tree / "agents.yaml").read_text())["agents"]}
            planner = roster[_role_of(JUDGEMENT)]
            self.assertEqual(planner["kind"], JUDGEMENT)
            self.assertEqual(planner["model"], _judgement_ladder()[0])

    def test_the_strongest_model_is_a_known_model_and_the_old_fable_still_is_too(self):
        # Attribution: markers in flight carry either id.
        strongest = _judgement_ladder()[0]
        self.assertIn(strongest, fmf.KNOWN_MODELS)
        self.assertIn(FABLE, fmf.KNOWN_MODELS)
        self.assertEqual(fmf.last_error_model([fmf.error_marker(strongest)]), strongest)
        self.assertEqual(fmf.last_error_model([fmf.error_marker(FABLE)]), FABLE)
        # And the predecessor is still on no ladder at all.
        for name, models in fmf.CONFIG["ladders"].items():
            with self.subTest(ladder=name):
                self.assertNotIn(FABLE, models)


class SchemaRejectsAutoPromotionTest(unittest.TestCase):
    """`on_new_model: workhorse` — the incident as a config value — is rejected
    by schema validation, and so is every other way to put the strongest model
    on the build path."""

    def test_the_committed_discovery_policy_is_admissible_and_alerts(self):
        # The committed file, in any saved state. Which admissible target it
        # names is a choice; that `workhorse` and `judgement` are refused and
        # `alert` must be true is the rule, proved on the fixture below.
        cfg = _committed()
        self.assertIn(cfg["discovery"]["on_new_model"], mf.DISCOVERY_TARGETS)
        self.assertIs(cfg["discovery"]["alert"], True)

    def test_on_new_model_workhorse_is_rejected(self):
        cfg = _canonical()
        cfg["discovery"]["on_new_model"] = WORKHORSE
        errors = fmf.policy_errors(cfg)
        self.assertTrue(errors, "on_new_model: workhorse must be rejected")
        self.assertTrue(
            any("on_new_model" in e for e in errors), f"unhelpful errors: {errors}"
        )

    def test_a_silent_discovery_is_rejected(self):
        cfg = _canonical()
        cfg["discovery"]["alert"] = False
        self.assertTrue(any(e.startswith("discovery.alert:")
                            for e in fmf.policy_errors(cfg)))

    def test_the_allowed_discovery_policies_are_accepted(self):
        for value in (ADVISORY, "none"):
            cfg = _canonical()
            cfg["discovery"]["on_new_model"] = value
            with self.subTest(policy=value):
                self.assertEqual(fmf.policy_errors(cfg), [])

    def test_todays_config_passes_validation(self):
        self.assertEqual(mf.policy_errors(_committed()), [])

    def test_the_excluded_fable_on_a_build_ladder_is_rejected(self):
        # Excluded is a code-side fact; a document leading the builders with
        # an excluded model is refused, naming it.
        self.assertIn(FABLE, fmh.code_side()["excluded"])
        errors = _home_errors(lead_with(fixture_home(), WORKHORSE, FABLE))
        self.assertEqual(errors, [f"{FABLE} is excluded, so it cannot be chosen."])

    def test_demoting_the_critic_to_a_build_kind_is_rejected(self):
        # The plain sentence a writer prints, whole: which agent, which group
        # it must be in and why, and where the document put it.
        doc = move_agent(fixture_home(), "critic", WORKHORSE)
        errors = _home_errors(doc)
        self.assertEqual(errors, [
            "The critic must be in the advisory group, because it reviews before "
            "an unattended merge, and it is in workhorse."])

    def test_a_role_cannot_be_pointed_at_a_raw_ladder(self):
        # Roles are assigned GROUPS the document declares: that is what stops
        # an edit from naming a group nobody chose models for and quietly
        # putting builds on it.
        role = _role_of(WORKHORSE)
        group = f"{ADVISORY}-copy"
        errors = _home_errors(move_agent(fixture_home(), role, group))
        self.assertIn(f"The agent {role} is in the group {group}, which the "
                      "document does not declare.", errors)

    def test_the_home_check_goes_red_on_the_critic_in_the_build_group(self):
        doc = move_agent(fixture_home(), "critic", WORKHORSE)
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            proc = _cli_check(tree, doc)
            self.assertEqual(proc.returncode, 1, "a writer must refuse this document")
            self.assertEqual(proc.stdout.splitlines(), _home_errors(doc))

    def test_the_selector_degrades_rather_than_honouring_a_rejected_config(self):
        # A home naming a model this release excludes is not obeyed on that
        # model: the reader sets it aside (and says so), and the build path
        # walks what is left instead of adopting the thing the policy exists
        # to prevent.
        doc = lead_with(fixture_home(), WORKHORSE, FABLE)
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            proc, _ = _cli_run(tree, _role_of(WORKHORSE), doc=doc)
            model = proc.stdout.strip()
            self.assertNotEqual(model, FABLE)
            self.assertEqual(
                model, _workhorse_ladder()[0],
                "an excluded model in the home must not reach the build path",
            )
            self.assertIn(f"set aside as unknown to this release: {FABLE}", proc.stderr)


class SelectionIsRecordedTest(unittest.TestCase):
    """Every run records the model it used AND why anything above it was
    skipped. A silently weakened advisory model is the failure this prevents."""

    def setUp(self):
        fmf.clear_availability_cache()

    def tearDown(self):
        fmf.clear_availability_cache()

    def test_the_decision_records_every_skipped_rung_and_its_reason(self):
        # The unavailable rung is the advisory TOP, whatever it is.
        top, below = _advisory_ladder()[:2]
        decision = fmf.select_with_reasons("critic", probe=lambda m: m != top)
        self.assertEqual(decision["model"], below)
        self.assertEqual(decision["kind"], ADVISORY)
        self.assertEqual([s["model"] for s in decision["skipped"]], [top])
        self.assertTrue(decision["skipped"][0]["reason"])
        self.assertTrue(decision["degraded"])

    def test_an_inconclusive_probe_is_recorded_differently_from_a_404(self):
        top, below = _advisory_ladder()[:2]

        def probe(model):
            if model == top:
                raise RuntimeError("probe blew up")
            return True

        decision = fmf.select_with_reasons("critic", probe=probe)
        self.assertEqual(decision["model"], below)
        self.assertIn("inconclusive", decision["skipped"][0]["reason"].lower())

    def test_the_top_rung_records_that_nothing_was_skipped(self):
        decision = fmf.select_with_reasons(_role_of(WORKHORSE), probe=lambda m: True)
        self.assertEqual(decision["model"], _workhorse_ladder()[0])
        self.assertEqual(decision["skipped"], [])
        self.assertFalse(decision["degraded"])
        self.assertIn("nothing", fmf.selection_note(decision).lower())

    def test_the_note_is_one_line_and_names_what_was_skipped_and_why(self):
        top, below = _advisory_ladder()[:2]
        decision = fmf.select_with_reasons("critic", probe=lambda m: m != top)
        note = fmf.selection_note(decision)
        self.assertEqual(len(note.splitlines()), 1, "the note is a single line")
        self.assertIn(below, note)
        self.assertIn(top, note)
        self.assertIn("skip", note.lower())

    def test_a_weakened_advisory_model_is_marked_degraded_loudly(self):
        # The alert half of the AC: an advisory role that did not get its
        # INTENDED model announces it. NOTE the meaning inverted on 2026-08-12
        # — falling to Opus is falling UP in cost, so this reads "unexpected
        # spend", not "quietly cheap". Either way the run is worth looking at,
        # which is why it stays loud — and a fall to the rung below the top,
        # one Sonnet to the next, is loud too.
        top = _advisory_ladder()[0]
        decision = fmf.select_with_reasons("critic", probe=lambda m: m != top)
        note = fmf.selection_note(decision)
        self.assertTrue(
            note.startswith("DEGRADED"),
            f"a weakened advisory model must be loud, got: {note}",
        )

    def test_the_cli_writes_the_explanation_for_the_workflow(self):
        top, below = _advisory_ladder()[:2]
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            model, note = _cli_select(
                tree, "critic", {**_all_up(), top: False}, explain=True,
            )
            # stdout stays exactly the model id — the workflows capture it.
            self.assertEqual(model, below)
            self.assertTrue(note.startswith("DEGRADED"), note)
            self.assertIn(top, note)

    def test_every_selecting_workflow_records_the_explanation(self):
        for wf, agent in SELECTORS.items():
            with self.subTest(workflow=wf):
                body = (WORKFLOWS / wf).read_text()
                self.assertIn(
                    "--explain-file", body,
                    f"{wf}: selection does not record why anything was skipped",
                )
                self.assertIn("why=$WHY", body, f"{wf}: the note is not an output")
                self.assertIn(
                    "::warning::", body, f"{wf}: a degraded selection is silent"
                )

    def test_the_card_heartbeat_carries_the_explanation(self):
        # agent-task.yml:303 already records the selection; the "why skipped"
        # half rides the same heartbeat so the board shows both.
        for wf in ("agent-task.yml", "plan.yml"):
            with self.subTest(workflow=wf):
                body = (WORKFLOWS / wf).read_text()
                heartbeat = [
                    line for line in body.splitlines() if "model-attempt:" in line
                ]
                self.assertTrue(heartbeat, f"{wf}: no model-attempt heartbeat")
                self.assertTrue(
                    any("steps.model.outputs.why" in line for line in heartbeat),
                    f"{wf}: the heartbeat records the model but not why",
                )


class DiscoveryJoinsAdvisoryOnlyTest(unittest.TestCase):
    """A model id the system sees that is absent from config is found by the
    catalog library, and the discovery policy still reads `advisory` — a NEW
    FAMILY may join the advisory ladder at most, and only after the CEO answers
    the question the adoption workflow files. No other job files a card about
    a new model (DRE-3899); the 2026-08-09 schema guard is unchanged."""

    def test_a_model_absent_from_config_is_discovered(self):
        known = _workhorse_ladder()[0]
        catalog = [
            {"id": known, "display_name": "Known", "created_at": "2026-08-01T00:00:00Z"},
            {"id": NOVA, "display_name": "Nova", "created_at": "2026-09-01T00:00:00Z"},
        ]
        found = [e["id"] for e in mc.new_models(catalog, known_ids=fmf.KNOWN_MODELS)]
        self.assertEqual(found, [NOVA])

    def test_known_ids_and_dated_snapshots_are_not_discoveries(self):
        # A rung, a retired id, an excluded id, and a dated snapshot of a rung.
        # Retired and excluded are code-side facts of the frozen models.yaml.
        cfg = _canonical()
        retired = cfg["retired"][0]
        retired = retired["model"] if isinstance(retired, dict) else retired
        excluded = cfg["excluded"][0]
        excluded = excluded["model"] if isinstance(excluded, dict) else excluded
        rung = _judgement_ladder()[-1]
        catalog = [
            {"id": model, "display_name": None, "created_at": None}
            for model in (rung, retired, excluded, FABLE,
                          f"{_workhorse_ladder()[1]}-20260801")
        ]
        self.assertEqual(mc.new_models(catalog, known_ids=fmf.KNOWN_MODELS), [])

    def test_a_model_we_have_already_seen_is_not_rediscovered(self):
        # "New" means new to US: an id already recorded in the committed
        # snapshot is not a discovery, however it got there. Without this the
        # weekly watch would re-alert on every model Anthropic offers that we
        # simply do not run — noise that trains the alert into wallpaper.
        catalog = [
            {"id": NOVA, "display_name": None, "created_at": None},
            {"id": RELIC, "display_name": None, "created_at": None},
        ]
        known = set(fmf.KNOWN_MODELS) | {RELIC}
        self.assertEqual(
            [e["id"] for e in mc.new_models(catalog, known_ids=known)],
            [NOVA],
        )

    def test_the_discovery_target_is_read_from_the_config(self):
        # The catalog decides nothing: it reports what the committed file says.
        self.assertEqual(mc.discovery_policy(), _committed()["discovery"])

    def test_the_discovery_targets_are_unchanged(self):
        # DRE-3899 removed the alert card, not the guard: a discovery may still
        # be pointed at the advisory ladder or at nothing, and never at a build
        # or a planning ladder.
        self.assertEqual(mf.DISCOVERY_TARGETS, (ADVISORY, "none"))

    def test_the_retired_check_new_command_refuses_to_run(self):
        # The adoption workflow asks the CEO about a new family. The weekly
        # snapshot job that once filed a second, contradicting card is
        # retired (DRE-6049), and the command it ran is gone with it.
        proc = subprocess.run(
            [sys.executable, str(MODEL_CATALOG), "check-new"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_discovery_never_writes_the_config(self):
        # Structural: the discovery path is data + an alert. Nothing in it can
        # edit the one file that decides spend.
        source = MODEL_CATALOG.read_text()
        self.assertNotRegex(
            source, r"open\([^)]*models\.yaml",
            "the catalog must never open the one file that decides spend",
        )
        self.assertNotRegex(
            source, r"write_text\(|\.write\([^)]*ladder",
            "the catalog writes data, never a ladder",
        )

class DocumentedPolicyTest(unittest.TestCase):
    """The rule is written down where the next well-meaning edit will read it."""

    def test_the_config_names_every_kind_and_the_rule(self):
        text = CONFIG.read_text().lower()
        for phrase in ("workhorse", "advisory", "judgement",
                       "availability is not permission"):
            self.assertIn(phrase, text, f"config/models.yaml must state: {phrase}")

    def test_the_readme_documents_the_three_kinds(self):
        readme = (ROOT / "config" / "README.md").read_text().lower()
        self.assertIn("advisory", readme)
        self.assertIn("workhorse", readme)
        self.assertIn("judgement", readme)
        self.assertIn("on_new_model", readme)

    def test_no_document_still_calls_it_a_two_kind_rule(self):
        # A change that contradicts a document updates that document in the
        # SAME PR. "Two role kinds" was true until 2026-09-03 and is now the
        # kind of sentence a reader trusts and acts on.
        stale = []
        for path in KIND_DOCUMENTS:
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"(two|2)\s+(role\s+)?kinds?\b", line, re.I):
                    stale.append(f"{path.name}:{n}: {line.strip()}")
        self.assertEqual(stale, [], "a document still says there are two kinds")

    def test_no_document_closes_the_list_of_kinds_on_a_short_one(self):
        # The other half of the same drift, and the half that slipped through
        # the sweep above: a sentence that ENUMERATES the kinds and then shuts
        # the list ("… and nothing else") without saying "two". The rule-1
        # error string in model_fallback.py read "every role is workhorse or
        # advisory, and nothing else" for the whole of the PR that ADDED the
        # third kind. A numeral is optional; naming every kind is not.
        #
        # Two judgement calls keep this from crying wolf, because a guard that
        # flags correct prose gets deleted rather than obeyed:
        #   * naming TWO kinds is what makes a line an enumeration of the set
        #     rather than a sentence about one of them — "the JUDGEMENT
        #     ladder: the planner, and nothing else" is about who walks a
        #     ladder, and is right;
        #   * prose WRAPS, so the surrounding lines count as the same
        #     sentence. A line that names `judgement` two lines below its
        #     "and nothing else" leaves no reader misled.
        stale = []
        for path in KIND_DOCUMENTS:
            lines = path.read_text().splitlines()
            for n, line in enumerate(lines, 1):
                if not re.search(r"\bnothing else\b", line, re.I):
                    continue
                named = [k for k in mf.ROLE_KINDS if k in line.lower()]
                if len(named) < 2:
                    continue
                window = " ".join(lines[max(0, n - 3):n + 2]).lower()
                missing = [k for k in mf.ROLE_KINDS if k not in window]
                if missing:
                    stale.append(f"{path.name}:{n}: omits {missing}: {line.strip()}")
        self.assertEqual(
            stale, [], "a closed list of the role kinds is missing one of them"
        )

    def test_the_rejected_kinds_error_names_every_kind(self):
        # The string a human reads when `config/models.yaml` is rejected for a
        # typo'd kind name. Assert the PROSE half (after the em-dash), not the
        # whole message: the first half interpolates `ROLE_KINDS` and so names
        # every kind however stale the sentence that follows it is. The
        # sentence is the part a reader believes — "every role is workhorse or
        # advisory" tells them `judgement` was removed, not that they
        # misspelled it.
        cfg = _canonical()
        cfg["kinds"]["judgment"] = cfg["kinds"].pop(JUDGEMENT)  # the typo itself
        errors = fmf.policy_errors(cfg)
        self.assertTrue(errors, "a misspelled kind must be rejected")
        rule = next((e for e in errors if e.startswith("kinds: must declare")), None)
        self.assertIsNotNone(rule, f"no rule-1 rejection: {errors}")
        _, _, prose = rule.partition("—")
        self.assertTrue(prose.strip(), f"the rejection carries no explanation: {rule}")
        for kind in fmf.ROLE_KINDS:
            with self.subTest(kind=kind):
                self.assertIn(
                    kind, prose, f"the explanation never names {kind!r}: {prose!r}"
                )


if __name__ == "__main__":
    unittest.main()
