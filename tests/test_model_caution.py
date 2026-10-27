"""A choice cautions, it is not refused (DRE-6786).

The CEO, 2026-10-10: any model with a declared price may be chosen for any
group, and each choice carries one plain-English `caution` sentence with both
prices where there is something to say. The words live once, in
`scripts/model_choice.py`; the one refusal left is a model the pinned Claude
Code cannot run.

The exact sentences, the nine cautioned pairs and the set-aside dates are read
off the frozen fixture (`tests/model_fixture.py`), a byte copy of the files of
2026-10-10 (DRE-6861). The generator published every choice with its caution
in `config/model-choices.json` until that file went (DRE-8020), so nothing
here reads a published page.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_caution.py -v
"""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "models.yaml"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import claude_code_pin as ccp  # noqa: E402
import model_choice as mc  # noqa: E402
import model_fallback as mf  # noqa: E402
import model_fixture  # noqa: E402
import test_model_cli_support as cli_support  # noqa: E402

# The ids the card's sentences are about, read on the frozen fixture.
OPUS_55 = "claude-opus-5-5"
OPUS_5 = "claude-opus-5"
SONNET_55 = "claude-sonnet-5-5"
SONNET_46 = "claude-sonnet-4-6"
HAIKU_55 = "claude-haiku-5-5"
FABLE_51 = "claude-fable-5-1"
FABLE_5 = "claude-fable-5"


def _raw() -> dict:
    return yaml.safe_load(CONFIG.read_text())


def _fixture() -> tuple[dict, dict]:
    return model_fixture.fixture_config(), model_fixture.fixture_prices()


# --------------------------------------------------------------------------- #
# 1. The words                                                                 #
# --------------------------------------------------------------------------- #

def test_model_label_is_family_and_version():
    assert mc.model_label(OPUS_55) == "Opus 5.5"
    assert mc.model_label(OPUS_5) == "Opus 5"
    assert mc.model_label("not-a-claude-id") == "not-a-claude-id"


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
    # And on the committed file, whatever it holds — a save may have added
    # `product-design` itself since DRE-6834, so the name is one it does not.
    committed = _raw()
    name, n = "product-design", 1
    while name in committed["kinds"]:
        n += 1
        name = f"product-design-{n}"
    with pytest.raises(KeyError, match=name):
        mc.caution_for(committed, mf.declared_prices(), name, OPUS_55)


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
# 3. The one refusal left: a model the pinned Claude Code cannot run           #
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


# --------------------------------------------------------------------------- #
# 4. The drift gate passes on the committed tree                               #
# --------------------------------------------------------------------------- #

def test_check_passes_on_the_committed_tree():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sync_model_config.py"), "--check"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr

