"""DRE-6992 — the reviewer and planner rules move into a `rules:` block of
config/models.yaml, and a model the home names stays attributable on a run's
marker.

THE RULES THE PAGE OFFERS BY
----------------------------
Which agents must stay Reviewers (`BACKSTOP_ROLES`, rule 3) and which may sit
with the Planners (`JUDGEMENT_ROLES`, rule 10) were constants in the code. The
console computes what its page may offer from config/models.yaml, so the two
lists are read from that file's top-level `rules:` block when the module
loads: `reviewers_only` and `planners_may_hold`. A file with no block — the
frozen fixture's (DRE-6859), and every tree built on it — reads today's values.
Each tree is judged in a fresh process, because the constants are read once,
at import.

A MODEL THE HOME NAMES STAYS ATTRIBUTABLE
-----------------------------------------
`last_attempt_model` and `last_error_model` count a marker's id when
`attributable(model)` says so: an id in `KNOWN_MODELS`, or one with a declared
price. `KNOWN_MODELS` itself is unchanged — `model_adoption._known_ids` reads it
as "already adopted" — and the prices are read only when a marker names an id
outside it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_rules_and_markers.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

import model_fallback as mf  # noqa: E402
import model_fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Today's values, as they stood in the code before the block (DRE-6992).
TODAY_BACKSTOP = ("critic", "verifier")
TODAY_JUDGEMENT = ("planner",)

# Rule 3 and rule 10 in the words `policy_errors` used on `main` before the
# move. The constants they read moved; the sentences did not.
RULE_3_CRITIC = (
    "agents.critic: must be 'advisory' (it gates unattended merges), "
    "got 'workhorse'"
)
RULE_10_ENGINEER = (
    "agents.engineer: 'judgement' is the planner's kind — it admits a role "
    "that runs once per epic, and engineer runs per card; volume on the "
    "strongest model is the 2026-08-09 incident. A build or fix role is "
    "'workhorse', a reviewer is 'advisory'"
)

# An id no ladder, project, `retired` or `excluded` names, in any config here.
UNLADDERED = "claude-test-home-only-1"


# --------------------------------------------------------------------------- #
# The rules block                                                              #
# --------------------------------------------------------------------------- #

def test_the_committed_config_carries_the_rules_block():
    raw = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())
    assert raw["rules"] == {
        "reviewers_only": ["critic", "verifier"],
        "planners_may_hold": ["planner"],
    }
    # A different key from the map under `review_separation`, which stays.
    assert "rules" in raw["review_separation"]


def test_the_constants_are_todays_values_and_keep_their_type():
    assert mf.BACKSTOP_ROLES == TODAY_BACKSTOP
    assert mf.JUDGEMENT_ROLES == TODAY_JUDGEMENT
    assert isinstance(mf.BACKSTOP_ROLES, tuple)
    assert isinstance(mf.JUDGEMENT_ROLES, tuple)


def test_rule_3_and_rule_10_keep_their_words():
    critic = model_fixture.fixture_config()
    critic["agents"]["critic"] = "workhorse"
    assert RULE_3_CRITIC in mf.policy_errors(critic)

    engineer = model_fixture.fixture_config()
    engineer["agents"]["engineer"] = "judgement"
    assert RULE_10_ENGINEER in mf.policy_errors(engineer)


_PROBE = textwrap.dedent("""
    import json, sys
    from pathlib import Path
    import yaml
    tree = Path(sys.argv[1])
    sys.path.insert(0, str(tree / "scripts"))
    import model_fallback as mf
    cfg = yaml.safe_load((tree / "config" / "models.yaml").read_text())
    cfg.get("agents", {})[sys.argv[2]] = sys.argv[3]
    print(json.dumps({
        "backstop": list(mf.BACKSTOP_ROLES),
        "backstop_is_tuple": isinstance(mf.BACKSTOP_ROLES, tuple),
        "judgement": list(mf.JUDGEMENT_ROLES),
        "judgement_is_tuple": isinstance(mf.JUDGEMENT_ROLES, tuple),
        "errors": mf.policy_errors(cfg),
    }))
""")


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    """A tree on the frozen fixture, whose models.yaml has no `rules:` block."""
    return model_fixture.fixture_tree(tmp_path_factory.mktemp("rules") / "tree")


def _with_rules(tree: Path, block: str | None) -> None:
    """The frozen models.yaml, with `block` appended when one is given."""
    text = model_fixture.fixture_text()
    if block is not None:
        text = text.rstrip("\n") + "\n\n" + textwrap.dedent(block).strip() + "\n"
    (tree / "config" / "models.yaml").write_text(text)


def _probe(tree: Path, agent: str = "critic", group: str = "advisory") -> dict:
    """The tree's constants, and `policy_errors` on its config with `agent`
    in `group`, read in a FRESH process — the constants load once."""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, str(tree), agent, group],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return {**json.loads(proc.stdout), "stderr": proc.stderr}


def test_the_frozen_fixture_has_no_rules_block():
    assert "rules" not in model_fixture.fixture_config()


def test_a_tree_with_no_rules_block_reads_todays_values(tree):
    _with_rules(tree, None)
    seen = _probe(tree)
    assert tuple(seen["backstop"]) == TODAY_BACKSTOP
    assert tuple(seen["judgement"]) == TODAY_JUDGEMENT
    assert seen["backstop_is_tuple"] and seen["judgement_is_tuple"]


def test_a_reviewer_the_block_adds_is_held_to_rule_3(tree):
    _with_rules(tree, """
        rules:
          reviewers_only: [critic, verifier, groomer]
          planners_may_hold: [planner]
    """)
    seen = _probe(tree, "groomer", "workhorse")
    assert seen["backstop"] == ["critic", "verifier", "groomer"]
    assert seen["backstop_is_tuple"]
    assert tuple(seen["judgement"]) == TODAY_JUDGEMENT
    assert ("agents.groomer: must be 'advisory' (it gates unattended merges), "
            "got 'workhorse'") in seen["errors"]
    assert "REFUSING" not in seen["stderr"]


def test_a_planner_the_block_admits_may_hold_the_judgement_kind(tree):
    _with_rules(tree, """
        rules:
          reviewers_only: [critic, verifier]
          planners_may_hold: [planner, medic]
    """)
    seen = _probe(tree, "medic", "judgement")
    assert seen["judgement"] == ["planner", "medic"]
    assert not any(e.startswith("agents.medic:") for e in seen["errors"])


@pytest.mark.parametrize("block", [
    "rules: [critic, verifier]",
    "rules:\n  reviewers_only: critic\n  planners_may_hold: [planner]",
    "rules:\n  reviewers_only: []\n  planners_may_hold: [planner]",
    "rules:\n  reviewers_only: [critic, 7]\n  planners_may_hold: [planner]",
])
def test_a_malformed_rules_block_reads_todays_values_loudly(tree, block):
    _with_rules(tree, block)
    seen = _probe(tree)
    assert tuple(seen["backstop"]) == TODAY_BACKSTOP
    assert tuple(seen["judgement"]) == TODAY_JUDGEMENT
    assert "rules" in seen["stderr"]


# --------------------------------------------------------------------------- #
# A model the home names stays attributable                                    #
# --------------------------------------------------------------------------- #

@pytest.fixture
def priced(tmp_path, monkeypatch):
    """A prices file declaring `UNLADDERED` beside today's prices, read through
    the real reader from an empty cache."""
    prices = yaml.safe_load((ROOT / "config" / "model-prices.yaml").read_text())
    prices["prices"][UNLADDERED] = {"input": 1.0, "output": 5.0}
    path = tmp_path / "model-prices.yaml"
    path.write_text(yaml.safe_dump(prices))
    monkeypatch.setattr(mf, "_PRICES_PATH", path)
    monkeypatch.setattr(mf, "_PRICES_CACHE", None)
    return path


def _attempt(model: str) -> list[str]:
    return ["older comment", mf.attempt_marker(model)]


def _death(model: str) -> list[str]:
    return ["older comment", mf.error_marker(model)]


def test_the_unladdered_id_is_outside_every_known_set():
    assert UNLADDERED not in mf.KNOWN_MODELS
    assert UNLADDERED not in mf.declared_prices(ROOT / "config" / "model-prices.yaml")


def test_a_priced_model_on_no_ladder_is_attributable(priced):
    assert mf.attributable(UNLADDERED)
    assert mf.last_attempt_model(_attempt(UNLADDERED)) == UNLADDERED
    assert mf.last_error_model(_death(UNLADDERED)) == UNLADDERED


def test_an_unpriced_unknown_model_reads_as_none(priced):
    unknown = "claude-test-nobody-priced"
    assert not mf.attributable(unknown)
    assert mf.last_attempt_model(_attempt(unknown)) is None
    assert mf.last_error_model(_death(unknown)) is None


def test_every_known_model_is_attributable():
    for model in mf.KNOWN_MODELS:
        assert mf.attributable(model), model


def test_the_prices_are_read_at_most_once_per_process(priced):
    assert mf.last_attempt_model(_attempt(UNLADDERED)) == UNLADDERED
    # Gone from the file now; the process keeps what it read.
    priced.write_text(yaml.safe_dump({"prices": {}}))
    assert mf.last_error_model(_death(UNLADDERED)) == UNLADDERED


def test_known_model_is_unchanged_by_a_priced_id(priced):
    """`KNOWN_MODELS` keeps its definition: the defaults' ladders, `retired`,
    `excluded` and the defaults' projects — never every priced id, or the
    daily adoption would read every adoptable model as already adopted."""
    defaults = mf.code_defaults()
    raw = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())
    ids = lambda rungs: {r["model"] if isinstance(r, dict) else r for r in rungs or []}  # noqa: E731
    expected = (
        {m for rungs in defaults["ladders"].values() for m in rungs}
        | ids(raw.get("retired")) | ids(raw.get("excluded"))
        | mf._project_ids(defaults)
    )
    assert mf.KNOWN_MODELS == expected
    assert mf.attributable(UNLADDERED)
    assert UNLADDERED not in mf.KNOWN_MODELS


def test_a_known_marker_never_reads_the_prices(monkeypatch):
    calls = []
    monkeypatch.setattr(mf, "declared_prices", lambda *a, **k: calls.append(a) or {})
    for model in sorted(mf.KNOWN_MODELS):
        assert mf.last_attempt_model(_attempt(model)) == model
        assert mf.last_error_model(_death(model)) == model
        assert mf.attributable(model)
    assert calls == []


def test_an_unknown_marker_reads_the_prices(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mf, "declared_prices",
        lambda *a, **k: calls.append(a) or {UNLADDERED: {"input": 1.0, "output": 5.0}})
    assert mf.last_attempt_model(_attempt(UNLADDERED)) == UNLADDERED
    assert calls


_IMPORT_COUNT = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, sys.argv[1])
    callers = []
    def profile(frame, event, arg):
        if event == "call" and frame.f_code.co_name == "declared_prices" \\
                and frame.f_code.co_filename.endswith("model_fallback.py"):
            callers.append(frame.f_back.f_code.co_name)
    sys.setprofile(profile)
    import model_fallback as mf
    sys.setprofile(None)
    print(json.dumps({"callers": callers, "known": sorted(mf.KNOWN_MODELS)}))
""")


def test_importing_adds_no_price_read_of_its_own():
    """Counted in a fresh process. The one call the import makes is the
    load-time policy check's (rule 4b), which `main` made before this card;
    building `KNOWN_MODELS` and the rules block add none."""
    proc = subprocess.run(
        [sys.executable, "-c", _IMPORT_COUNT, str(ROOT / "scripts")],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    seen = json.loads(proc.stdout)
    assert seen["callers"] == ["policy_errors"]
    assert set(seen["known"]) == mf.KNOWN_MODELS
