"""RED-first tests for DRE-3895 — the adoption RULE, and only the rule.

WHAT THIS MODULE IS. `scripts/model_adoption.py` takes the live catalog (the
`/v1/models` shape `model_catalog.py` already parses), the ladders in
`config/models.yaml` and the declared prices in `config/model-prices.yaml`, and
sorts every model id the pipeline has never configured into exactly one of
three rules the CEO stated on 2026-09-14:

  * **adopt**  — a newer version of a family already on a ladder, at the same
                 or a lower declared price;
  * **ignore** — older than, or superseded by, what we run;
  * **ask**    — a new family, a price above the rung it would join, or no
                 declared price at all.

Its output is one Decision record per candidate, and that record is the
contract DRE-3903 (`apply`/`render`/`open-*`) and DRE-3898 consume.

WHAT IT DELIBERATELY IS NOT. It edits no config, renders no card or PR text,
touches Linear never, and its CLI exposes `classify` and nothing else. Adoption
stays a deliberate human edit of `config/models.yaml` — this module only says
which of the three buckets a candidate falls in, and why.

THE TRAPS THESE TESTS PIN.

  * **Price is never guessed.** `config/model-prices.yaml` is the only source.
    An id absent from it has no price, and a candidate with no price can never
    be `adopt` — absence is a valid, fail-closed answer, not a reason to infer
    one from a sibling model.
  * **A higher price on EITHER side is `ask`.** Cheaper input does not buy a
    dearer output. Fable at $10/$50 against Opus at $5/$25 is the incident this
    whole area exists for.
  * **Model ids DO NOT SORT.** `"claude-opus-10" < "claude-opus-5"` is True in
    Python. Every ordering here goes through the catalog's `created_at`, via
    `model_catalog`'s own helpers — a second implementation is a second thing
    to drift.
  * **A known id is not a candidate**, and neither is a dated snapshot of one
    (`claude-opus-5-20260801` is the same model as `claude-opus-5`).
  * **Every `ask` reason opens with one of three fixed phrases**, because
    DRE-3903 picks its question title from it.
  * Zero network in this suite: the autouse fixture makes the real fetch raise.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_adoption.py -q
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import model_adoption as ma  # noqa: E402
import model_catalog as mc  # noqa: E402
import model_fallback as mf  # noqa: E402

MODULE = ROOT / "scripts" / "model_adoption.py"
CONFIG_PATH = ROOT / "config" / "models.yaml"
PRICES_PATH = ROOT / "config" / "model-prices.yaml"

OPUS = "claude-opus-5"
SONNET46 = "claude-sonnet-4-6"
SONNET5 = "claude-sonnet-5"
FABLE51 = "claude-fable-5-1"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def model(id_, created_at=None, display_name=None):
    """One entry as `model_catalog.parse_catalog` exposes it."""
    return {"id": id_, "display_name": display_name, "created_at": created_at}


def payload(*entries):
    """A /v1/models-shaped response envelope."""
    return {"data": [{"type": "model", **e} for e in entries], "has_more": False}


def ladders(**named):
    """A models.yaml-shaped config from `name=[ids]` — the `[{model:, reason:}]`
    rung shape the real file uses."""
    return {
        "ladders": {
            name: [{"model": m, "reason": "test rung"} for m in ids]
            for name, ids in named.items()
        }
    }


def prices(**by_id):
    """`claude_sonnet_5=(2, 10)` → the declared-price mapping. Underscores in
    the keyword become dashes, so the ids read as ids."""
    return {
        name.replace("_", "-"): {"input": float(i), "output": float(o)}
        for name, (i, o) in by_id.items()
    }


def by_candidate(decisions):
    return {d["candidate"]: d for d in decisions}


# The fixture catalog the first acceptance criterion names: the four ids on a
# ladder today, five older models, a newer same-family Sonnet at the same
# declared price, a newer Opus with NO declared price, and a family no ladder
# runs that is newer than Opus 5.
FIXTURE_CATALOG = [
    model(OPUS, "2026-08-01T00:00:00Z", "Claude Opus 5"),
    model(SONNET46, "2026-05-01T00:00:00Z", "Claude Sonnet 4.6"),
    model(SONNET5, "2026-06-29T00:00:00Z", "Claude Sonnet 5"),
    model(FABLE51, "2026-09-01T00:00:00Z", "Claude Fable 5.1"),
    model("claude-opus-4-5", "2026-02-01T00:00:00Z", "Claude Opus 4.5"),
    model("claude-opus-4-6", "2026-03-01T00:00:00Z", "Claude Opus 4.6"),
    model("claude-opus-4-7", "2026-04-01T00:00:00Z", "Claude Opus 4.7"),
    model("claude-sonnet-4-5", "2026-02-15T00:00:00Z", "Claude Sonnet 4.5"),
    model("claude-haiku-4-5", "2026-02-20T00:00:00Z", "Claude Haiku 4.5"),
    model("claude-sonnet-6", "2026-11-01T00:00:00Z", "Claude Sonnet 6"),
    model("claude-opus-6", "2026-11-05T00:00:00Z", "Claude Opus 6"),
    model("claude-corvid-1", "2026-11-10T00:00:00Z", "Claude Corvid 1"),
]

# Sonnet 6 declared at the same price as the newest Sonnet rung; Opus 6
# deliberately absent — an id with no declared price can never be adopted.
FIXTURE_PRICES = prices(
    claude_opus_5=(5.0, 25.0),
    claude_sonnet_4_6=(3.0, 15.0),
    claude_sonnet_5=(2.0, 10.0),
    claude_fable_5_1=(10.0, 50.0),
    claude_sonnet_6=(2.0, 10.0),
)

FIXTURE_OLD_IDS = [
    "claude-opus-4-5",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Zero network calls: the real fetch raises if any test reaches it."""

    def _boom():
        raise AssertionError("a test reached the live /v1/models endpoint")

    monkeypatch.setattr(mc, "_fetch_real", _boom)
    mc.clear_catalog_cache()
    yield
    mc.clear_catalog_cache()


@pytest.fixture
def live_config():
    return yaml.safe_load(CONFIG_PATH.read_text())


def run_cli(*args, env=None):
    proc_env = dict(os.environ)
    proc_env.update(env or {})
    return subprocess.run(
        [sys.executable, str(MODULE), *args],
        capture_output=True,
        text=True,
        env=proc_env,
        cwd=str(ROOT),
    )


# --------------------------------------------------------------------------- #
# 1. The fixture catalog, end to end through the CLI                           #
# --------------------------------------------------------------------------- #

def _write_fixture(tmp_path):
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps(payload(*FIXTURE_CATALOG)))
    prices_file = tmp_path / "prices.yaml"
    prices_file.write_text(
        yaml.safe_dump(
            {
                "prices": {
                    mid: {**p, "source": "https://example.invalid/pricing"}
                    for mid, p in FIXTURE_PRICES.items()
                }
            }
        )
    )
    return catalog_file, prices_file


def _classify_fixture(tmp_path):
    catalog_file, prices_file = _write_fixture(tmp_path)
    proc = run_cli(
        "classify",
        "--snapshot",
        str(catalog_file),
        "--config",
        str(CONFIG_PATH),
        "--prices",
        str(prices_file),
    )
    assert proc.returncode == 0, f"classify failed: {proc.stdout}\n{proc.stderr}"
    return by_candidate(json.loads(proc.stdout))


def test_the_five_old_ids_are_ignored(tmp_path):
    decided = _classify_fixture(tmp_path)
    for old in FIXTURE_OLD_IDS:
        assert decided[old]["rule"] == "ignore", decided[old]["reason"]
        assert decided[old]["replaces"] == [], "an ignore replaces nothing"


def test_the_newer_same_priced_sonnet_is_adopted_on_every_ladder(tmp_path):
    decided = _classify_fixture(tmp_path)
    sonnet6 = decided["claude-sonnet-6"]
    assert sonnet6["rule"] == "adopt"
    assert sonnet6["family"] == "sonnet"
    assert sonnet6["display_name"] == "Claude Sonnet 6"
    assert sonnet6["price"] == {"input": 2.0, "output": 10.0}
    # Every ladder carrying a Sonnet rung, and on each the family's NEWEST one:
    # the advisory ladder runs Sonnet 5, the workhorse one Sonnet 4.6. The
    # judgement ladder is Opus 5 alone since DRE-3969 (hotfix), so it carries
    # no Sonnet rung to replace.
    assert {(r["ladder"], r["model"]) for r in sonnet6["replaces"]} == {
        ("workhorse", SONNET46),
        ("advisory", SONNET5),
    }
    for rung in sonnet6["replaces"]:
        assert rung["created_at"], "a replaced rung carries the date it was ranked on"
        assert set(rung["price"]) == {"input", "output"}


def test_the_newer_opus_with_no_declared_price_is_ask(tmp_path):
    decided = _classify_fixture(tmp_path)
    opus6 = decided["claude-opus-6"]
    assert opus6["rule"] == "ask"
    assert opus6["price"] is None, "an undeclared price is null, never a guess"
    assert opus6["reason"].startswith("no declared price")
    assert {r["model"] for r in opus6["replaces"]} == {OPUS}


def test_a_new_family_newer_than_a_rung_is_ask_and_replaces_nothing(tmp_path):
    decided = _classify_fixture(tmp_path)
    corvid = decided["claude-corvid-1"]
    assert corvid["rule"] == "ask"
    assert corvid["reason"].startswith("new model family")
    assert corvid["replaces"] == [], "a new family takes no rung"


def test_every_ask_reason_opens_with_one_of_the_three_phrases(tmp_path):
    decided = _classify_fixture(tmp_path)
    asks = [d for d in decided.values() if d["rule"] == "ask"]
    assert asks, "the fixture has asks in it"
    for decision in asks:
        assert any(
            decision["reason"].startswith(phrase) for phrase in ma.ASK_PHRASES
        ), f"{decision['candidate']}: {decision['reason']!r}"


def test_known_ids_and_their_dated_snapshots_are_never_candidates(tmp_path):
    catalog_file, prices_file = _write_fixture(tmp_path)
    catalog = FIXTURE_CATALOG + [
        model(f"{mid}-20260801", "2026-12-01T00:00:00Z")
        for mid in sorted(mf.KNOWN_MODELS)
    ]
    catalog_file.write_text(json.dumps(payload(*catalog)))
    proc = run_cli(
        "classify", "--snapshot", str(catalog_file), "--prices", str(prices_file)
    )
    assert proc.returncode == 0, proc.stderr
    decided = by_candidate(json.loads(proc.stdout))
    for known in mf.KNOWN_MODELS:
        assert known not in decided, f"{known} is configured — not a candidate"
        assert f"{known}-20260801" not in decided, "a dated snapshot is the same model"


def test_the_record_carries_exactly_the_contract_keys(tmp_path):
    decided = _classify_fixture(tmp_path)
    for decision in decided.values():
        assert set(decision) == {
            "candidate",
            "display_name",
            "created_at",
            "family",
            "rule",
            "replaces",
            "price",
            "reason",
        }
        assert decision["rule"] in {"adopt", "ignore", "ask"}
        assert decision["reason"].strip(), "every record says why"


def test_output_is_ordered_newest_first(tmp_path):
    catalog_file, prices_file = _write_fixture(tmp_path)
    proc = run_cli(
        "classify", "--snapshot", str(catalog_file), "--prices", str(prices_file)
    )
    ids = [d["candidate"] for d in json.loads(proc.stdout)]
    assert ids[:3] == ["claude-corvid-1", "claude-opus-6", "claude-sonnet-6"]


# --------------------------------------------------------------------------- #
# 2. The price rule — a dearer side on EITHER axis is ask, never adopt         #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "candidate_price",
    [(2.5, 10.0), (2.0, 10.5), (2.5, 12.0)],
    ids=["dearer-input", "dearer-output", "dearer-both"],
)
def test_a_dearer_side_is_ask_not_adopt(candidate_price):
    catalog = [
        model(SONNET5, "2026-06-29T00:00:00Z"),
        model("claude-sonnet-6", "2026-11-01T00:00:00Z"),
    ]
    decided = by_candidate(
        ma.classify_catalog(
            catalog,
            ladders(advisory=[SONNET5]),
            {
                SONNET5: {"input": 2.0, "output": 10.0},
                "claude-sonnet-6": {
                    "input": candidate_price[0],
                    "output": candidate_price[1],
                },
            },
        )
    )
    decision = decided["claude-sonnet-6"]
    assert decision["rule"] == "ask"
    assert decision["reason"].startswith(f"priced above {SONNET5}")
    # It still says which rung it would take — DRE-3903 renders that.
    assert [r["model"] for r in decision["replaces"]] == [SONNET5]


def test_the_same_price_on_both_sides_is_adopt():
    decided = by_candidate(
        ma.classify_catalog(
            [
                model(SONNET5, "2026-06-29T00:00:00Z"),
                model("claude-sonnet-6", "2026-11-01T00:00:00Z"),
            ],
            ladders(advisory=[SONNET5]),
            prices(claude_sonnet_5=(2.0, 10.0), claude_sonnet_6=(2.0, 10.0)),
        )
    )
    assert decided["claude-sonnet-6"]["rule"] == "adopt"


def test_a_cheaper_but_older_candidate_is_ignored():
    # Cheap does not buy newness. Ranking is by created_at, never by price and
    # never by the id.
    decided = by_candidate(
        ma.classify_catalog(
            [
                model(SONNET5, "2026-06-29T00:00:00Z"),
                model("claude-sonnet-4-4", "2026-01-01T00:00:00Z"),
            ],
            ladders(advisory=[SONNET5]),
            prices(claude_sonnet_5=(2.0, 10.0), claude_sonnet_4_4=(0.5, 1.0)),
        )
    )
    decision = decided["claude-sonnet-4-4"]
    assert decision["rule"] == "ignore"
    assert decision["replaces"] == []


def test_an_id_that_sorts_later_but_is_older_is_still_ignored():
    # `"claude-opus-10" < "claude-opus-5"` is True in Python. A lexical ranker
    # would call Opus 10 the older model; here Opus 10 is genuinely older and a
    # lexical ranker would call it NEWER and adopt it.
    decided = by_candidate(
        ma.classify_catalog(
            [
                model(OPUS, "2026-08-01T00:00:00Z"),
                model("claude-opus-10", "2026-01-01T00:00:00Z"),
            ],
            ladders(workhorse=[OPUS]),
            prices(claude_opus_5=(5.0, 25.0), claude_opus_10=(1.0, 1.0)),
        )
    )
    assert decided["claude-opus-10"]["rule"] == "ignore"


def test_a_candidate_with_no_declared_price_is_never_adopt():
    decided = by_candidate(
        ma.classify_catalog(
            [
                model(SONNET5, "2026-06-29T00:00:00Z"),
                model("claude-sonnet-6", "2026-11-01T00:00:00Z"),
            ],
            ladders(advisory=[SONNET5]),
            prices(claude_sonnet_5=(2.0, 10.0)),
        )
    )
    decision = decided["claude-sonnet-6"]
    assert decision["rule"] == "ask"
    assert decision["price"] is None
    assert decision["reason"].startswith("no declared price")


def test_an_undeclared_rung_price_is_ask_not_adopt():
    # Fail closed from the other side too: we cannot say "the same or lower"
    # against a rung whose price nobody declared.
    decided = by_candidate(
        ma.classify_catalog(
            [
                model(SONNET5, "2026-06-29T00:00:00Z"),
                model("claude-sonnet-6", "2026-11-01T00:00:00Z"),
            ],
            ladders(advisory=[SONNET5]),
            prices(claude_sonnet_6=(1.0, 1.0)),
        )
    )
    decision = decided["claude-sonnet-6"]
    assert decision["rule"] == "ask"
    assert decision["reason"].startswith("no declared price")


# --------------------------------------------------------------------------- #
# 3. The ignore rules                                                          #
# --------------------------------------------------------------------------- #

def test_no_created_at_is_ignored():
    # Never rank on missing data — an entry the catalog gives no date for
    # cannot be shown to be newer than anything.
    decided = by_candidate(
        ma.classify_catalog(
            [model(SONNET5, "2026-06-29T00:00:00Z"), model("claude-sonnet-6", None)],
            ladders(advisory=[SONNET5]),
            prices(claude_sonnet_5=(2.0, 10.0), claude_sonnet_6=(1.0, 1.0)),
        )
    )
    assert decided["claude-sonnet-6"]["rule"] == "ignore"
    assert decided["claude-sonnet-6"]["created_at"] is None


def test_a_same_family_version_not_newer_than_the_newest_rung_is_ignored():
    # Newer than the workhorse Sonnet rung, older than the advisory one: the
    # family's NEWEST rung is what decides, across every ladder.
    decided = by_candidate(
        ma.classify_catalog(
            [
                model(SONNET46, "2026-05-01T00:00:00Z"),
                model(SONNET5, "2026-06-29T00:00:00Z"),
                model("claude-sonnet-4-9", "2026-06-01T00:00:00Z"),
            ],
            ladders(workhorse=[SONNET46], advisory=[SONNET5]),
            prices(
                claude_sonnet_4_6=(3.0, 15.0),
                claude_sonnet_5=(2.0, 10.0),
                claude_sonnet_4_9=(1.0, 1.0),
            ),
        )
    )
    assert decided["claude-sonnet-4-9"]["rule"] == "ignore"


def test_a_same_family_id_created_at_the_same_moment_is_not_newer():
    # The boundary the rule is written on: `ignore` is "not NEWER than the
    # family's newest rung", so a tie is an ignore. Reading a tie as newer
    # adopts a rebadge of the model we already run.
    decided = by_candidate(
        ma.classify_catalog(
            [
                model(SONNET5, "2026-06-29T00:00:00Z"),
                model("claude-sonnet-5-turbo", "2026-06-29T00:00:00Z"),
            ],
            ladders(advisory=[SONNET5]),
            prices(claude_sonnet_5=(2.0, 10.0), claude_sonnet_5_turbo=(1.0, 1.0)),
        )
    )
    assert decided["claude-sonnet-5-turbo"]["rule"] == "ignore"


def test_a_new_family_as_old_as_the_oldest_rung_is_still_asked_about():
    # The other side of the same boundary: `ignore` for a new family is "OLDER
    # than every rung", so a tie is a question, not a silent drop.
    decided = by_candidate(
        ma.classify_catalog(
            [
                model(OPUS, "2026-08-01T00:00:00Z"),
                model("claude-corvid-1", "2026-08-01T00:00:00Z"),
            ],
            ladders(workhorse=[OPUS]),
            prices(claude_opus_5=(5.0, 25.0)),
        )
    )
    decision = decided["claude-corvid-1"]
    assert decision["rule"] == "ask"
    assert decision["reason"].startswith("new model family")


def test_a_new_family_older_than_every_rung_is_ignored():
    decided = by_candidate(
        ma.classify_catalog(
            [
                model(OPUS, "2026-08-01T00:00:00Z"),
                model(SONNET5, "2026-06-29T00:00:00Z"),
                model("claude-corvid-1", "2026-01-01T00:00:00Z"),
            ],
            ladders(workhorse=[OPUS], advisory=[SONNET5]),
            prices(claude_opus_5=(5.0, 25.0), claude_sonnet_5=(2.0, 10.0)),
        )
    )
    assert decided["claude-corvid-1"]["rule"] == "ignore"


def test_a_ladder_pinned_to_an_alias_resolves_through_its_dated_snapshot():
    # The ladder pins `claude-sonnet-5`; the API may list only the dated form.
    # `model_catalog._resolve_pin` is what answers that, and reusing it is why
    # this case works at all.
    decided = by_candidate(
        ma.classify_catalog(
            [
                model("claude-sonnet-5-20260629", "2026-06-29T00:00:00Z"),
                model("claude-sonnet-6", "2026-11-01T00:00:00Z"),
            ],
            ladders(advisory=[SONNET5]),
            prices(claude_sonnet_5=(2.0, 10.0), claude_sonnet_6=(2.0, 10.0)),
        )
    )
    assert "claude-sonnet-5-20260629" not in decided, "a dated snapshot of a rung"
    assert decided["claude-sonnet-6"]["rule"] == "adopt"
    assert [r["model"] for r in decided["claude-sonnet-6"]["replaces"]] == [SONNET5]


# --------------------------------------------------------------------------- #
# 4. config/model-prices.yaml — the one price source                           #
# --------------------------------------------------------------------------- #

def test_every_ladder_id_has_a_declared_price_with_a_source(live_config):
    # Read the ladders LIVE: a rung added tomorrow fails this build rather than
    # silently becoming un-adoptable.
    declared = ma.load_prices(PRICES_PATH)
    ladder_ids = {
        rung["model"] if isinstance(rung, dict) else rung
        for rungs in live_config["ladders"].values()
        for rung in rungs
    }
    missing = sorted(ladder_ids - set(declared))
    assert not missing, f"config/model-prices.yaml declares no price for {missing}"
    for model_id in ladder_ids:
        entry = declared[model_id]
        assert isinstance(entry["input"], (int, float))
        assert isinstance(entry["output"], (int, float))
        assert str(entry.get("source", "")).strip(), f"{model_id} has no source"


def test_an_extra_entry_for_a_retired_id_is_allowed(live_config):
    # DRE-3880 may move Sonnet 4.6 to `retired:` before or after this lands. A
    # retired id may keep its price — the rule is that every LADDER id has one,
    # never that the two sets are equal.
    declared = ma.load_prices(PRICES_PATH)
    ladder_ids = {
        rung["model"] if isinstance(rung, dict) else rung
        for rungs in live_config["ladders"].values()
        for rung in rungs
    }
    assert set(declared) >= ladder_ids
    assert SONNET46 in declared, "kept whichever way DRE-3880 merges"


def test_the_header_says_absence_is_a_valid_answer():
    header = PRICES_PATH.read_text().split("prices:")[0]
    lowered = header.lower()
    assert "absent" in lowered, "the header must say an unsourced price stays absent"
    assert "fail-closed" in lowered or "fail closed" in lowered


@pytest.mark.parametrize("missing", ["input", "output"])
def test_load_prices_rejects_an_entry_missing_a_side(tmp_path, missing):
    entry = {"input": 2.0, "output": 10.0, "source": "https://example.invalid"}
    entry.pop(missing)
    path = tmp_path / "prices.yaml"
    path.write_text(yaml.safe_dump({"prices": {SONNET5: entry}}))
    with pytest.raises(ValueError) as excinfo:
        ma.load_prices(path)
    assert SONNET5 in str(excinfo.value)
    assert missing in str(excinfo.value)


def test_load_prices_reads_the_committed_file():
    declared = ma.load_prices(PRICES_PATH)
    assert declared[OPUS]["input"] == 5.0
    assert declared[OPUS]["output"] == 25.0
    assert declared[FABLE51]["input"] == 10.0


# --------------------------------------------------------------------------- #
# 5. The CLI surface — classify, and nothing else                             #
# --------------------------------------------------------------------------- #

def test_the_cli_exposes_only_classify():
    for forbidden in ("apply", "render", "open-record-card", "open-question-card"):
        proc = run_cli(forbidden)
        assert proc.returncode != 0, f"{forbidden} must not be a command here"


def test_the_module_imports_no_linear_or_verdict_seam():
    source = MODULE.read_text()
    assert "linear_ops" not in source, "this card never touches Linear"
    assert "routing_verdict" not in source


def test_an_empty_catalog_is_an_empty_list_at_exit_zero(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"data": []}))
    proc = run_cli("classify", "--snapshot", str(path))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == []
    assert ma.classify_catalog([], {"ladders": {}}, {}) == []


def test_classify_reads_the_fake_catalog_env_with_no_network(tmp_path):
    # The same hook `model_catalog.py snapshot` reads: no network, no
    # credential, and no `--snapshot` — this is the path the workflow exercises.
    _, prices_file = _write_fixture(tmp_path)
    proc = run_cli(
        "classify",
        "--prices",
        str(prices_file),
        env={
            "BUREAU_FAKE_CATALOG": json.dumps(payload(*FIXTURE_CATALOG)),
            "ANTHROPIC_API_KEY": "",
            "CLAUDE_CODE_OAUTH_TOKEN": "",
        },
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    decided = by_candidate(json.loads(proc.stdout))
    assert decided["claude-sonnet-6"]["rule"] == "adopt"
    assert decided["claude-corvid-1"]["rule"] == "ask"


def test_classify_fetches_the_live_catalog_once(monkeypatch, live_config):
    # The library form takes a catalog; the CLI is the ONE place that fetches,
    # and it fetches once.
    calls = []

    def _fake_fetch(*, fetch=None, clock=None):
        calls.append(1)
        return list(FIXTURE_CATALOG)

    monkeypatch.setattr(ma.model_catalog, "fetch_catalog", _fake_fetch)
    monkeypatch.setattr(ma, "load_prices", lambda path: dict(FIXTURE_PRICES))
    assert ma.main(["classify"]) == 0
    assert calls == [1], "one catalog fetch per classify run"


def test_classify_catalog_is_importable_for_one_id(live_config):
    # DRE-3903 classifies a single id without shelling out.
    decisions = ma.classify_catalog(
        [model(OPUS, "2026-08-01T00:00:00Z"), model("claude-opus-6", "2026-11-05T00:00:00Z")],
        live_config,
        dict(FIXTURE_PRICES),
    )
    assert [d["candidate"] for d in decisions] == ["claude-opus-6"]
