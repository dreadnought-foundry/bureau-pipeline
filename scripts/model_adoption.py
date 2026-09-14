#!/usr/bin/env python3
"""The adoption RULE — and only the rule (DRE-3895).

Takes the live Anthropic catalog (the `/v1/models` shape `model_catalog.py`
already parses), the ladders in `config/models.yaml`, and the declared prices
in `config/model-prices.yaml`, and sorts every model id the pipeline has never
configured into exactly one of three rules the CEO stated on 2026-09-14:

  * **adopt**  — a newer version of a family already on a ladder, at the same
                 or a lower declared price;
  * **ignore** — older than, or superseded by, what we run;
  * **ask**    — a new family, a price above the rung it would join, or no
                 declared price at all.

The output is one Decision record per candidate, and that record is the
contract the actions module (`scripts/model_adoption_actions.py`, DRE-3903) and
the adoption workflow (DRE-3898) consume:

    {"candidate": "claude-sonnet-6", "display_name": "Claude Sonnet 6",
     "created_at": "2026-11-01T00:00:00Z", "family": "sonnet",
     "rule": "adopt",
     "replaces": [{"ladder": "workhorse", "model": "claude-sonnet-5",
                   "created_at": "2026-06-29T00:00:00Z",
                   "price": {"input": 2.0, "output": 10.0}}],
     "price": {"input": 2.0, "output": 10.0},
     "reason": "one plain-English sentence naming the rule and the evidence"}

WHAT THIS MODULE DOES NOT DO (do not "improve" it into doing them)
------------------------------------------------------------------
It edits no config, renders no card or PR text, and never touches Linear. Its
CLI is `classify` and nothing else — `apply`, `render`, `open-record-card` and
`open-question-card` live in DRE-3903's own module, which imports
`classify_catalog` and `load_prices` from here. Adoption itself stays what it
has always been: a deliberate human edit of `config/models.yaml` in a reviewed
PR. This module says which of the three buckets a candidate falls in, and why.

PRICE IS NEVER GUESSED
----------------------
`config/model-prices.yaml` is the only source. An id absent from it has no
price, and a candidate with no price can never be `adopt` — it becomes a
question for a human. `adopt` also requires the candidate to be at or below the
rung on BOTH sides: a cheaper input does not buy a dearer output. Fable at
$10/$50 against Opus at $5/$25 is the 2026-08-09 incident, and "newest and
best" doubling the bill with nobody deciding is exactly what the rule is for.

RANKING: `created_at`, NEVER THE ID
-----------------------------------
`"claude-opus-10" < "claude-opus-5"` is True in Python — a lexical ranker calls
the successor older on the day it ships. Every comparison here goes through
`model_catalog`'s own helpers (`family`, `created_at`, `_is_dated_snapshot_of`,
`_resolve_pin`, `new_models`) rather than a second implementation that can
drift from the one the drift watch already ranks on. The id is used for
grouping and for recognizing a dated snapshot of an alias — never for ordering.

NEVER DECIDE ON MISSING DATA
----------------------------
A candidate the catalog gives no `created_at` for is `ignore`: it cannot be
shown to be newer than anything. A rung the catalog cannot date is not ranked.
An empty catalog yields an empty list, exit 0 — the same degrade
`model_catalog` makes, for the same reason: a vendor outage must never turn
into an assertion about what we should run.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

import model_catalog
from model_catalog import created_at, family
from model_fallback import KNOWN_MODELS

_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = _ROOT / "config" / "models.yaml"
PRICES_PATH = _ROOT / "config" / "model-prices.yaml"

# The three rules, and nothing else may appear in a record's `rule`.
RULE_ADOPT = "adopt"
RULE_IGNORE = "ignore"
RULE_ASK = "ask"

# An `ask` reason OPENS with one of exactly these three phrases, because
# DRE-3903 chooses its question title from it. `priced above` is followed by
# the rung id the candidate would take.
ASK_NEW_FAMILY = "new model family"
ASK_PRICED_ABOVE = "priced above"
ASK_NO_PRICE = "no declared price"
ASK_PHRASES = (ASK_NEW_FAMILY, ASK_PRICED_ABOVE, ASK_NO_PRICE)


# --------------------------------------------------------------------------- #
# config/model-prices.yaml — the one price reader                              #
# --------------------------------------------------------------------------- #

def load_prices(path=PRICES_PATH) -> dict[str, dict]:
    """The declared prices, `{model_id: {input, output, source, declared}}`.

    The ONE reader — DRE-3903 imports this rather than parsing the file again,
    so there is one answer to "what does this model cost" in the pipeline.

    Raises `ValueError` on a file we cannot read or an entry missing `input` or
    `output`. A half-declared price is not a price: reading it as "free on the
    missing side" would adopt a model on a number nobody wrote down, which is
    the one thing the file exists to prevent. Leaving the entry OUT entirely is
    the supported way to say "we could not source this" — absence is a valid,
    fail-closed answer and yields `ask`.
    """
    import yaml

    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise ValueError(f"model prices: cannot read {path} ({exc})") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"model prices: {path} is not readable YAML ({exc})") from exc

    entries = raw.get("prices") if isinstance(raw, Mapping) else None
    if not isinstance(entries, Mapping):
        raise ValueError(f"model prices: {path} has no `prices:` mapping")

    out: dict[str, dict] = {}
    for model_id, entry in entries.items():
        if not isinstance(entry, Mapping):
            raise ValueError(f"model prices: {model_id} is not a mapping")
        declared = dict(entry)
        for side in ("input", "output"):
            value = entry.get(side)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"model prices: {model_id} has no numeric `{side}` — a "
                    "half-declared price is not a price; leave the entry out "
                    "instead"
                )
            declared[side] = float(value)
        out[str(model_id)] = declared
    return out


def _price_of(model_id: str, prices) -> dict | None:
    """`{"input": .., "output": ..}` for an id, or None when nothing is
    declared for it. Tolerant on purpose: `load_prices` is the validator, and a
    caller handing us a half-entry gets "no price" (fail closed), never a
    guess."""
    entry = (prices or {}).get(model_id)
    if not isinstance(entry, Mapping):
        return None
    side_in, side_out = entry.get("input"), entry.get("output")
    for value in (side_in, side_out):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
    return {"input": float(side_in), "output": float(side_out)}


def load_config(path=CONFIG_PATH) -> dict:
    """`config/models.yaml` as a mapping. The ladders are read from it live —
    a rung added tomorrow is compared tomorrow, with nothing here enumerating
    ladder names by hand."""
    import yaml

    return yaml.safe_load(Path(path).read_text()) or {}


# --------------------------------------------------------------------------- #
# The ladders, as rungs we can rank                                            #
# --------------------------------------------------------------------------- #

def _ladder_rungs(config) -> dict[str, list[str]]:
    """`{ladder_name: [model_id, ...]}` from a models.yaml mapping, in the
    file's own order — so `replaces` lists ladders the way a human reads them.
    """
    ladders = config.get("ladders") if isinstance(config, Mapping) else None
    if not isinstance(ladders, Mapping):
        return {}
    return {
        str(name): model_catalog._ladder_ids(rungs) for name, rungs in ladders.items()
    }


def _known_ids(config) -> set[str]:
    """Every id the pipeline has ALREADY been configured with: the ladders, the
    retired ids and the excluded ones.

    `model_fallback.KNOWN_MODELS` is exactly that set for the live config, and
    it is the set the card names. The config passed in is unioned with it so a
    caller classifying against a config of its own cannot accidentally offer a
    rung it runs as a discovery.
    """
    known = set(KNOWN_MODELS)
    for rungs in _ladder_rungs(config).values():
        known |= set(rungs)
    if isinstance(config, Mapping):
        for key in ("retired", "excluded"):
            known |= set(model_catalog._ladder_ids(config.get(key)))
    return known


def _iso(stamp: datetime) -> str:
    return stamp.isoformat().replace("+00:00", "Z")


def _money(price) -> str:
    """`$2.00/$10.00 per MTok`, or `no declared price` when there is none."""
    if not price:
        return ASK_NO_PRICE
    return f"${price['input']:.2f}/${price['output']:.2f} per MTok"


def _rung_index(config, catalog, prices) -> list[dict]:
    """Every rung on every ladder, with the date the catalog gives it and the
    price we declare for it.

    `created_at` is resolved through `model_catalog._resolve_pin`, so a ladder
    pinned to an ALIAS (`claude-sonnet-5`) still ranks when the API lists only
    the dated form (`claude-sonnet-5-20260629`). `at is None` means the catalog
    cannot date that rung — it is carried, never ranked.
    """
    rungs: list[dict] = []
    for ladder_name, ids in _ladder_rungs(config).items():
        for rung_id in dict.fromkeys(ids):  # a ladder never repeats an id
            rungs.append(
                {
                    "ladder": ladder_name,
                    "model": rung_id,
                    "family": family(rung_id),
                    "at": model_catalog._resolve_pin(rung_id, catalog),
                    "price": _price_of(rung_id, prices),
                }
            )
    return rungs


def _replaced_rungs(rungs, fam) -> list[dict]:
    """The rung a candidate of family `fam` would TAKE on each ladder that runs
    that family — the family's NEWEST rung on each. Opus 5 sits on all three
    ladders, so an Opus successor replaces three rungs, one per ladder.
    """
    by_ladder: dict[str, dict] = {}
    for rung in rungs:
        if rung["family"] != fam:
            continue
        current = by_ladder.get(rung["ladder"])
        if current is None or _rank(rung) > _rank(current):
            by_ladder[rung["ladder"]] = rung
    return [
        {
            "ladder": rung["ladder"],
            "model": rung["model"],
            "created_at": _iso(rung["at"]) if rung["at"] else None,
            "price": rung["price"],
        }
        for rung in by_ladder.values()
    ]


def _rank(rung) -> datetime:
    """A sortable date for a rung — an undatable one sorts oldest, so it is
    never mistaken for the newest thing we run."""
    return rung["at"] or model_catalog._UNKNOWN_TIME


def _decision(entry, fam, stamp, rule, replaces, price, reason) -> dict:
    return {
        "candidate": entry["id"],
        "display_name": entry.get("display_name"),
        "created_at": _iso(stamp) if stamp else None,
        "family": fam,
        "rule": rule,
        "replaces": replaces,
        "price": price,
        "reason": reason,
    }


# --------------------------------------------------------------------------- #
# The rule                                                                     #
# --------------------------------------------------------------------------- #

def classify_catalog(catalog, config, prices) -> list[dict]:
    """One Decision record per CANDIDATE — every catalog id the pipeline has
    never been configured with — newest first.

    The library form, so DRE-3903 can classify a single id without shelling
    out: hand it a one-entry catalog (plus the rungs it is ranked against) and
    read the record back.

    The rules, in this order:

      1. **Not a candidate at all** — an id we already run, retire or exclude,
         or a dated snapshot of one. Absent from the output entirely.
      2. **ignore** — no `created_at`; OR some rung shares its family and the
         candidate is not newer than that family's newest rung; OR no rung
         shares its family and the candidate is older than every rung.
      3. **adopt** — some rung shares its family, the candidate is newer than
         the family's newest rung, both have a declared price, and the
         candidate is at or below that rung on input AND output.
      4. **ask** — everything else.
    """
    catalog = [e for e in (catalog or []) if isinstance(e, Mapping) and e.get("id")]
    rungs = _rung_index(config, catalog, prices)
    ranked = [r for r in rungs if r["at"] is not None]
    candidates = model_catalog.new_models(catalog, known_ids=_known_ids(config))
    return [_classify_one(e, rungs, ranked, prices) for e in candidates]


def _classify_one(entry, rungs, ranked, prices) -> dict:
    model_id = entry["id"]
    fam = family(model_id)
    price = _price_of(model_id, prices)
    stamp = created_at(entry)

    # Never rank on missing data.
    if stamp == model_catalog._UNKNOWN_TIME:
        return _decision(
            entry, fam, None, RULE_IGNORE, [], price,
            f"the catalog gives no created_at for {model_id}, so it cannot be "
            "ranked against the ladders",
        )

    same_family = [r for r in ranked if r["family"] == fam]

    # --- a family no ladder runs ------------------------------------------- #
    if not same_family:
        if not ranked:
            return _decision(
                entry, fam, stamp, RULE_IGNORE, [], price,
                f"{model_id} is a {fam} model and the catalog cannot date any "
                "rung we run, so there is nothing to rank it against",
            )
        oldest = min(ranked, key=_rank)
        if stamp < oldest["at"]:
            return _decision(
                entry, fam, stamp, RULE_IGNORE, [], price,
                f"{model_id} is a {fam} model, a line no ladder runs, and it "
                f"is older than every rung on every ladder ({_iso(stamp)} "
                f"against the oldest, {oldest['model']} at {_iso(oldest['at'])})",
            )
        newest = max(ranked, key=_rank)
        return _decision(
            entry, fam, stamp, RULE_ASK, [], price,
            f"{ASK_NEW_FAMILY} — {model_id} is a {fam} model, a line no ladder "
            f"runs, and it is newer than {newest['model']} ({_iso(stamp)} "
            f"against {_iso(newest['at'])}); joining a ladder is a human's "
            "decision, not a version bump",
        )

    # --- a family we already run ------------------------------------------- #
    newest = max(same_family, key=_rank)
    if stamp <= newest["at"]:
        return _decision(
            entry, fam, stamp, RULE_IGNORE, [], price,
            f"{model_id} is not newer than {newest['model']}, the newest {fam} "
            f"rung we run ({_iso(stamp)} against {_iso(newest['at'])})",
        )

    replaces = _replaced_rungs(rungs, fam)
    rung_price = newest["price"]

    if price is None:
        return _decision(
            entry, fam, stamp, RULE_ASK, replaces, price,
            f"{ASK_NO_PRICE} — {model_id} is newer than {newest['model']} "
            f"({_iso(stamp)} against {_iso(newest['at'])}) but "
            "config/model-prices.yaml declares no price for it, and a price is "
            "never guessed",
        )
    if rung_price is None:
        return _decision(
            entry, fam, stamp, RULE_ASK, replaces, price,
            f"{ASK_NO_PRICE} — config/model-prices.yaml declares no price for "
            f"{newest['model']}, the rung {model_id} would take, so there is "
            "nothing to compare its "
            f"{_money(price)} against",
        )
    if price["input"] > rung_price["input"] or price["output"] > rung_price["output"]:
        return _decision(
            entry, fam, stamp, RULE_ASK, replaces, price,
            f"{ASK_PRICED_ABOVE} {newest['model']} — {model_id} is declared at "
            f"{_money(price)} against that rung's {_money(rung_price)}, and a "
            "dearer side is a spend decision a human makes",
        )
    return _decision(
        entry, fam, stamp, RULE_ADOPT, replaces, price,
        f"{model_id} is a newer {fam} than {newest['model']} ({_iso(stamp)} "
        f"against {_iso(newest['at'])}) at the same or a lower declared price "
        f"({_money(price)} against {_money(rung_price)})",
    )


# --------------------------------------------------------------------------- #
# CLI — `classify`, and nothing else                                           #
# --------------------------------------------------------------------------- #

def load_catalog_file(path) -> list[dict]:
    """A catalog read off disk: either a raw `/v1/models` payload or the
    committed `models.json` snapshot shape (only the entries the API still
    lists — a retired entry is kept for labelling, never offered)."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    if isinstance(data, Mapping) and isinstance(data.get("models"), list):
        return model_catalog.snapshot_catalog(data)
    return model_catalog.parse_catalog(data)


def _parse_classify_args(argv: list[str]):
    snapshot = None
    config_path = CONFIG_PATH
    prices_path = PRICES_PATH
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg == "--snapshot":
            snapshot = rest.pop(0) if rest else None
        elif arg == "--config":
            config_path = rest.pop(0) if rest else config_path
        elif arg == "--prices":
            prices_path = rest.pop(0) if rest else prices_path
        else:
            raise ValueError(f"unknown option {arg!r}")
    return snapshot, config_path, prices_path


def _cmd_classify(argv: list[str]) -> int:
    """Print the Decision records as a JSON list, exit 0.

    Without `--snapshot` the live catalog is fetched ONCE, through
    `model_catalog.fetch_catalog` — the same seam, the same degrade, the same
    `BUREAU_FAKE_CATALOG` hook, so this runs with no network and no credential
    in a test or a dry run. An empty catalog prints `[]` and exits 0: a vendor
    outage is not a finding.
    """
    try:
        snapshot, config_path, prices_path = _parse_classify_args(argv)
    except ValueError as exc:
        print(f"::error::{exc}")
        return 2

    if snapshot:
        catalog = load_catalog_file(snapshot)
    else:
        model_catalog.clear_catalog_cache()
        catalog = model_catalog.fetch_catalog(fetch=_fake_catalog_from_env())

    try:
        config = load_config(config_path)
        prices = load_prices(prices_path)
    except (OSError, ValueError) as exc:
        print(f"::error::{exc}")
        return 2

    print(json.dumps(classify_catalog(catalog, config, prices), indent=2))
    return 0


def _fake_catalog_from_env():
    """`BUREAU_FAKE_CATALOG`, read exactly the way `model_catalog.py snapshot`
    reads it — one hook, so a fixture that drives one drives the other."""
    raw = os.environ.get(model_catalog.FAKE_CATALOG_ENV, "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return lambda: payload


def main(argv: list[str]) -> int:
    """CLI for the adoption rule.

      classify [--snapshot <catalog.json>] [--config config/models.yaml]
               [--prices config/model-prices.yaml]
                    print one Decision record per candidate, as JSON, exit 0

    There is no other command, deliberately: this module decides, it never
    acts. Applying a decision, rendering it, and opening a card for it are
    DRE-3903's, in `scripts/model_adoption_actions.py`.
    """
    if not argv:
        print(
            "usage: model_adoption.py classify [--snapshot <catalog.json>] "
            "[--config <models.yaml>] [--prices <model-prices.yaml>]"
        )
        return 2
    cmd, *rest = argv
    if cmd == "classify":
        return _cmd_classify(rest)
    print(f"unknown command {cmd!r} — this module only classifies")
    return 2


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
