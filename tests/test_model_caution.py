"""A choice cautions, it is not refused (DRE-6786).

The CEO, 2026-10-10: any model with a declared price may be chosen for any
group. `config/model-choices.json` says so — every priced model is allowed for
every group — and each choice carries one plain-English `caution` sentence
with both prices where there is something to say. The words live once, in
`scripts/model_choice.py`, so the generator and the console's change-writer
say the same thing; the one refusal left is a model the pinned Claude Code
cannot run.

Two shapes of test, as `tests/test_model_choices.py` explains (DRE-6861):

  * The exact sentences, the nine cautioned pairs and the set-aside dates are
    read off the frozen fixture (`tests/model_fixture.py`), a byte copy of the
    files of 2026-10-10. A console save changes which model leads a group, so
    a sentence pinned to the committed file would turn red on a legitimate save.
    The ids those sentences name are read off the fixture too, never spelled.
  * What must hold for any saved state — every choice allowed, the cautions in
    the file being exactly what `caution_for` derives, every sentence in plain
    words — is read off the committed file, and every expected value is derived
    from it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_caution.py -v
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHOICES = ROOT / "config" / "model-choices.json"
CONFIG = ROOT / "config" / "models.yaml"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import claude_code_pin as ccp  # noqa: E402
import model_choice as mc  # noqa: E402
import model_fallback as mf  # noqa: E402
import model_fixture  # noqa: E402
import sync_model_config  # noqa: E402
import test_model_cli_support as cli_support  # noqa: E402

#: The ids the card's sentences are about, read off the frozen fixture's
#: ladders and its set-aside list, never spelled (DRE-6962): a console save
#: can put any of them on a ladder or take it off one, and the guard in
#: tests/test_no_test_pins_a_console_choice.py refuses a spelled one.
FROZEN = model_fixture.fixture_config()
OPUS_55 = model_fixture.ladder(FROZEN, "workhorse")[0]     # the Builders' top
OPUS_5 = model_fixture.ladder(FROZEN, "workhorse")[1]      # the Builders' fallback
SONNET_46 = model_fixture.ladder(FROZEN, "judgement")[-1]  # the Planners' last rung
FABLE_51 = model_fixture.ladder(FROZEN, "judgement")[0]    # the Planners' top
HAIKU_55, FABLE_5 = (e["model"] for e in FROZEN["excluded"])  # the two set aside

REFUSAL = "Our pinned Claude Code cannot run {label} yet."


def _raw() -> dict:
    return yaml.safe_load(CONFIG.read_text())


def _choices() -> dict:
    return json.loads(CHOICES.read_text(encoding="utf-8"))


def _fixture() -> tuple[dict, dict]:
    return model_fixture.fixture_config(), model_fixture.fixture_prices()


def _excluded(raw: dict) -> set[str]:
    return {e["model"] if isinstance(e, dict) else e for e in raw.get("excluded") or []}


def _render(raw: dict, prices: dict, **kwargs) -> dict:
    """The page the generator writes for the committed `raw` and `prices`."""
    cfg = sync_model_config.load_config()
    return json.loads(sync_model_config.render_choices(raw, cfg, prices, **kwargs))


# --------------------------------------------------------------------------- #
# 1. The words                                                                 #
# --------------------------------------------------------------------------- #

def test_model_label_moved_and_the_generator_imports_it():
    assert mc.model_label(OPUS_55) == "Opus 5.5"
    assert mc.model_label(OPUS_5) == "Opus 5"
    assert mc.model_label("not-a-claude-id") == "not-a-claude-id"
    assert sync_model_config.model_label is mc.model_label
    assert not hasattr(sync_model_config, "_model_label")


@pytest.mark.parametrize("price, text", [
    ({"input": 10.0, "output": 50.0}, "$10/$50"),
    ({"input": 4, "output": 20}, "$4/$20"),
    ({"input": 0.1, "output": 0.5}, "$0.10/$0.50"),
    ({"input": 2.5, "output": 10.0}, "$2.50/$10"),
])
def test_price_text_is_input_then_output(price, text):
    assert mc.price_text(price) == text


@pytest.mark.parametrize("text", [
    "keep it off the ladder",
    "the Ladders say no",
    "a lower rung",
    "rungs below it",
    "a kind of model",
    "the Kind it runs as",
    "breaks rule 4b",
    "breaks Rule 7 on the walk",
    "see config/models.yaml",
    "written by scripts/sync_model_config.py",
    "read docs/model-policy.md first",
    "the .yaml file",
])
def test_plain_words_flags_the_words_the_ceo_never_reads(text):
    assert mc.plain_words_problems(text), text


@pytest.mark.parametrize("text", [
    "Fable 5.1 costs more than Opus 5.5: $10/$50 against $4/$20 per million "
    "tokens, and Builders is the busiest group.",
    "Haiku 5.5 is a model we set aside; choosing it brings it back for "
    "Planners at $0.10/$0.50 per million tokens, against Fable 5.1 at $10/$50.",
    "Our pinned Claude Code cannot run Opus 5 yet.",
    "a kindly rule that applies",
])
def test_plain_words_passes_a_plain_sentence(text):
    assert mc.plain_words_problems(text) == [], text


def test_plain_words_names_every_problem_it_finds():
    problems = mc.plain_words_problems("the ladder rung of this kind, rule 4b, config/models.yaml")
    assert len(problems) == 5, problems


def test_set_aside_date_is_the_first_date_in_the_excluded_reason():
    raw, _ = _fixture()
    assert mc.set_aside_date(raw, HAIKU_55) == "2026-10-08"
    assert mc.set_aside_date(raw, FABLE_5) == "2026-08-12"
    assert mc.set_aside_date(raw, OPUS_55) is None
    undated = copy.deepcopy(raw)
    for entry in undated["excluded"]:
        entry["reason"] = "off every ladder on cost policy"
    assert mc.set_aside_date(undated, FABLE_5) is None


# --------------------------------------------------------------------------- #
# 2. The caution rules, on the files of 2026-10-10                             #
# --------------------------------------------------------------------------- #

def test_the_dearest_model_for_builders_names_the_busiest_group():
    raw, prices = _fixture()
    assert mc.caution_for(raw, prices, "workhorse", FABLE_51) == (
        "Fable 5.1 costs more than Opus 5.5: $10/$50 against $4/$20 per million "
        "tokens, and Builders is the busiest group.")


def test_a_dearer_model_names_both_prices():
    raw, prices = _fixture()
    assert mc.caution_for(raw, prices, "advisory", OPUS_55) == (
        "Opus 5.5 costs more than Sonnet 5.5: $4/$20 against $2/$10 per million "
        "tokens.")
    # Dearer on both sides per token, though Opus 5 is a fallback here.
    assert mc.caution_for(raw, prices, "workhorse", OPUS_5) == (
        "Opus 5 costs more than Opus 5.5: $5/$25 against $4/$20 per million tokens.")
    # The dearest model, but not for Builders: no busiest-group clause.
    assert mc.caution_for(raw, prices, "advisory", FABLE_51) == (
        "Fable 5.1 costs more than Sonnet 5.5: $10/$50 against $2/$10 per million "
        "tokens.")


def test_a_set_aside_model_says_it_comes_back():
    raw, prices = _fixture()
    assert mc.caution_for(raw, prices, "workhorse", HAIKU_55) == (
        "Haiku 5.5 is a model we set aside; choosing it brings it back for "
        "Builders at $0.10/$0.50 per million tokens, against Opus 5.5 at $4/$20.")


def test_the_current_model_and_a_cheaper_one_carry_no_caution():
    raw, prices = _fixture()
    assert mc.caution_for(raw, prices, "judgement", OPUS_55) is None
    assert mc.caution_for(raw, prices, "workhorse", OPUS_55) is None
    assert mc.caution_for(raw, prices, "workhorse", SONNET_46) is None


def test_a_set_aside_and_dearer_model_is_one_sentence():
    raw, prices = _fixture()
    prices = copy.deepcopy(prices)
    prices[HAIKU_55] = {"input": 20.0, "output": 100.0}
    assert mc.caution_for(raw, prices, "workhorse", HAIKU_55) == (
        "Haiku 5.5 is a model we set aside; choosing it brings it back for "
        "Builders, and it costs more than Opus 5.5: $20/$100 against $4/$20 per "
        "million tokens.")


def test_dearer_on_one_side_is_dearer():
    raw, prices = _fixture()
    prices = copy.deepcopy(prices)
    prices[SONNET_46] = {"input": 3.0, "output": 25.0}
    assert mc.caution_for(raw, prices, "workhorse", SONNET_46) == (
        "Sonnet 4.6 costs more than Opus 5.5: $3/$25 against $4/$20 per million "
        "tokens.")


def test_a_standing_exception_is_current_when_given():
    raw, prices = _fixture()
    assert mc.caution_for(raw, prices, "workhorse", OPUS_55, current=SONNET_46) == (
        "Opus 5.5 costs more than Sonnet 4.6: $4/$20 against $3/$15 per million "
        "tokens.")
    assert mc.caution_for(raw, prices, "workhorse", SONNET_46, current=SONNET_46) is None


def test_an_undeclared_group_is_a_key_error_naming_it():
    raw, prices = _fixture()
    with pytest.raises(KeyError, match="product-design"):
        mc.caution_for(raw, prices, "product-design", OPUS_55)
    # And on the committed file, whatever it holds.
    with pytest.raises(KeyError, match="product-design"):
        mc.caution_for(_raw(), mf.declared_prices(), "product-design", OPUS_55)


def test_the_nine_cautioned_pairs_of_2026_10_10():
    raw, prices = _fixture()
    cautioned = {
        (kind, model)
        for kind in raw["kinds"]
        for model in prices
        if mc.caution_for(raw, prices, kind, model) is not None
    }
    assert cautioned == {
        ("workhorse", OPUS_5), ("workhorse", FABLE_51), ("workhorse", HAIKU_55),
        ("advisory", OPUS_55), ("advisory", OPUS_5), ("advisory", SONNET_46),
        ("advisory", FABLE_51), ("advisory", HAIKU_55),
        ("judgement", HAIKU_55),
    }


# --------------------------------------------------------------------------- #
# 3. The committed file, in any state the console can save                     #
# --------------------------------------------------------------------------- #

def _current(raw: dict, kind: str) -> str:
    spec = raw["kinds"][kind]
    rung = raw["ladders"][spec["ladder"] if isinstance(spec, dict) else spec][0]
    return rung["model"] if isinstance(rung, dict) else rung


def _dearer(prices: dict, model: str, than: str) -> bool:
    return (prices[model]["input"] > prices[than]["input"]
            or prices[model]["output"] > prices[than]["output"])


def test_every_priced_model_is_allowed_for_every_group():
    doc = _choices()
    priced = list(mf.declared_prices())
    assert [m["id"] for m in doc["models"]] == priced
    for group in doc["groups"]:
        assert [c["model"] for c in group["choices"]] == priced, group["key"]
        for choice in group["choices"]:
            assert list(choice) == ["model", "allowed", "reason", "caution"]
            assert choice["allowed"] is True, (group["key"], choice["model"])
            assert choice["reason"] is None, (group["key"], choice["model"])


def test_the_cautions_in_the_file_are_what_the_rules_derive():
    raw, prices, doc = _raw(), mf.declared_prices(), _choices()
    expected = {
        (kind, model): mc.caution_for(raw, prices, kind, model)
        for kind in raw["kinds"]
        for model in prices
    }
    derived = {pair for pair, text in expected.items() if text is not None}
    published = {
        (group["key"], choice["model"])
        for group in doc["groups"]
        for choice in group["choices"]
        if choice["caution"] is not None
    }
    assert published == derived
    assert derived, "the files of any saved state caution some choice"
    for group in doc["groups"]:
        for choice in group["choices"]:
            assert choice["caution"] == expected[(group["key"], choice["model"])]
    # Every cautioned pair is one of the three rules, and every pair a rule
    # covers is cautioned.
    excluded = _excluded(raw)
    for kind in raw["kinds"]:
        current = _current(raw, kind)
        for model in prices:
            covered = model != current and (
                model in excluded or _dearer(prices, model, current))
            assert ((kind, model) in derived) is covered, (kind, model)


def test_every_published_sentence_is_in_plain_words():
    raw, prices, doc = _raw(), mf.declared_prices(), _choices()
    for group in doc["groups"]:
        for choice in group["choices"]:
            for key in ("reason", "caution"):
                if choice[key] is not None:
                    assert mc.plain_words_problems(choice[key]) == [], choice
    for kind in raw["kinds"]:
        for model in prices:
            text = mc.caution_for(raw, prices, kind, model)
            if text is not None:
                assert mc.plain_words_problems(text) == [], text
                assert text.endswith(".") and text.count(". ") == 0, text


def test_the_old_shape_is_still_there_and_caution_is_the_only_addition():
    doc = _choices()
    assert list(doc) == ["generated_from", "models", "groups", "agents", "projects"]
    for model in doc["models"]:
        assert list(model) == ["id", "label", "input", "output", "excluded"]
    for group in doc["groups"]:
        assert list(group) == [
            "key", "label", "purpose", "default_model", "fallbacks", "choices"]
        for choice in group["choices"]:
            # A reader of the old shape reads the first three keys as before.
            assert list(choice)[:3] == ["model", "allowed", "reason"]
            assert list(choice)[3:] == ["caution"]
            assert isinstance(choice["allowed"], bool)
            assert choice["reason"] is None or isinstance(choice["reason"], str)
            assert choice["caution"] is None or isinstance(choice["caution"], str)
    for agent in doc["agents"]:
        assert list(agent) == ["name", "label", "about", "group", "runs_in", "moves"]
        for move in agent["moves"]:
            assert list(move) == ["group", "allowed", "reason"]
    for project in doc["projects"]:
        assert list(project) == ["slug", "exceptions"]


def test_the_moves_do_not_depend_on_the_choices():
    # `agents[].moves` are the policy's own words and not this card's: a page
    # rendered with every model refused by the pin carries the same agents,
    # byte for byte, as the committed file.
    raw, prices = _raw(), mf.declared_prices()
    floors = {model: "999.0.0" for model in prices}
    page = _render(raw, prices, floors=floors, installed="2.1.284")
    assert all(not c["allowed"] for g in page["groups"] for c in g["choices"])
    committed = _choices()
    assert json.dumps(page["agents"], indent=2, ensure_ascii=False) == json.dumps(
        committed["agents"], indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 4. The one refusal left: a model the pinned Claude Code cannot run           #
# --------------------------------------------------------------------------- #

def test_the_floors_are_the_two_tables_the_ladder_check_reads():
    assert ccp.model_floors(ROOT) == cli_support._minimums()
    assert ccp.installed_claude_code(ROOT) == cli_support._pinned_version()


def test_the_floor_reader_takes_the_higher_of_two_rows(tmp_path):
    support = tmp_path / "tests" / "test_model_cli_support.py"
    support.parent.mkdir(parents=True)
    support.write_text(
        'MINIMUM_CLAUDE_CODE = {\n    # a comment\n    "claude-a": "2.1.280",\n'
        '    "claude-b": "2.1.290",\n}\n\nAHEAD_OF_ADOPTION = {\n'
        '    "claude-a": "2.1.300",\n    "claude-b": "2.1.285",\n}\n')
    assert ccp.model_floors(tmp_path) == {"claude-a": "2.1.300", "claude-b": "2.1.290"}


def test_a_tree_with_no_floor_tables_has_no_floors(tmp_path):
    assert ccp.model_floors(tmp_path) == {}


def test_a_model_above_the_pin_is_refused_in_every_group():
    raw, prices = _raw(), mf.declared_prices()
    installed = ccp.installed_claude_code(ROOT)
    major, minor, patch = ccp.version_key(installed)
    model = next(iter(prices))
    floors = {**ccp.model_floors(ROOT), model: f"{major}.{minor}.{patch + 1}"}
    page = _render(raw, prices, floors=floors, installed=installed)
    label = mc.model_label(model)
    for group in page["groups"]:
        for choice in group["choices"]:
            if choice["model"] == model:
                assert choice == {
                    "model": model,
                    "allowed": False,
                    "reason": REFUSAL.format(label=label),
                    "caution": None,
                }, group["key"]
            else:
                assert choice["allowed"] is True, (group["key"], choice["model"])
                assert choice["reason"] is None


def test_a_floor_the_pin_meets_refuses_nothing():
    raw, prices = _raw(), mf.declared_prices()
    installed = ccp.installed_claude_code(ROOT)
    floors = {model: installed for model in prices}
    page = _render(raw, prices, floors=floors, installed=installed)
    assert all(c["allowed"] for g in page["groups"] for c in g["choices"])


def test_with_todays_tables_no_choice_is_refused():
    raw, prices = _raw(), mf.declared_prices()
    page = _render(raw, prices)
    assert all(c["allowed"] for g in page["groups"] for c in g["choices"])
    assert page == _choices()


# --------------------------------------------------------------------------- #
# 5. The drift gate covers the cautions                                        #
# --------------------------------------------------------------------------- #

def test_check_passes_on_the_committed_tree():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sync_model_config.py"), "--check"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_check_fails_when_only_a_caution_is_edited_by_hand(tmp_path):
    tree = model_fixture.fixture_tree(tmp_path / "tree")
    path = tree / "config" / "model-choices.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    choice = next(c for g in doc["groups"] for c in g["choices"] if c["caution"])
    choice["caution"] = choice["caution"].replace("costs more", "costs a little more")
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "scripts/sync_model_config.py", "--check"],
        cwd=tree, capture_output=True, text=True)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "STALE: config/model-choices.json" in proc.stderr
    assert "ok:    scripts/model_fallback.py" in proc.stdout
    assert "ok:    agents.yaml" in proc.stdout
