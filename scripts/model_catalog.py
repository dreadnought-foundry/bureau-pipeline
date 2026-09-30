#!/usr/bin/env python3
"""Model catalog: discover models from the live API and snapshot them as data
(DRE-2236, stdlib only — matches model_fallback.py's style).

WHY THIS EXISTS
---------------
We hardcoded model ids and display labels in several places and they drifted
silently. On 2026-07-28 the console showed "Opus 4.8" on every board card while
the pipeline was being moved to Opus 5 — the label was a hardcoded string on
the tier, so it would have kept naming 4.8 while dispatching 5. We found it
from a screenshot. The same rotation missed `repairer` in agents.yaml and CI
stayed green, because the ladder test enumerated `engineer`/`planner` BY NAME.
A guard that enumerates by hand always misses the thing added after it.

So: read the model list from the API, snapshot it as data, and rank what it
offers against what we pin. That is the whole job. This module is a LIBRARY and
a snapshot writer; it decides nothing and files nothing.

WHERE ADOPTION IS DECIDED (the CEO's rule of 2026-09-14)
--------------------------------------------------------
The CEO gave three rules for a model the catalog offers that the pipeline has
never configured, and they replaced "a human decides every one" (DRE-3899):

  * a newer version of a family already on a ladder, at the same or a lower
    declared price, is ADOPTED — trialled for real, then proposed as an
    ordinary pull request to config/models.yaml that the critic and the merge
    gate review;
  * a model older than, or superseded by, what we run is IGNORED — no card;
  * a new family, a higher price, or no declared price at all is ASKED — one
    plain-English question for the CEO.

The classification lives in `scripts/model_adoption.py`, which reads this
module's ranking helpers (`family`, `created_at`, `_is_dated_snapshot_of`,
`_resolve_pin`, `new_models`, `fetch_catalog`, `snapshot_catalog`,
`load_snapshot`), and price lives in `config/model-prices.yaml` — the only
source of it; a price is never guessed. `.github/workflows/model-adoption.yml`
runs the rule. The weekly `.github/workflows/model-drift.yml` only refreshes
models.json. Nothing in this module writes config/models.yaml or a ladder, and
nothing in it renders a Linear card: the drift and new-model cards it used to
write (DRE-3880, DRE-3881) were exactly the cards the rule says should not
exist.

What the rule still guards against, and why it reads a price and runs a trial
rather than taking "newest" on faith:
  * A new model can ship BREAKING API changes. Opus 5 alone changed two things
    vs 4.8: thinking is ON by default (so `max_tokens` caps thinking + response
    together and tightly-sized requests truncate mid-answer), and disabling
    thinking returns 400 above `high` effort.
  * COST. Fable is $10/$50 per MTok vs Opus $5/$25 — "newest and best" can
    double the bill with nobody deciding to. That is exactly the incident that
    took Fable off the workhorse ladder on 2026-08-09, and its absence from the
    build path is a validated invariant. Where the price DOES buy something is
    bounded, low-volume work at a decision point: since DRE-3015 that is the
    JUDGEMENT ladder, walked by the planner alone, once per epic.

DISCOVERY (DRE-2317)
--------------------
`new_models()` reports ids the catalog offers that config/models.yaml has never
named; the adoption rule sorts them. `discovery.on_new_model: workhorse` is not
an expressible policy, and neither is `judgement`: schema validation rejects
both, so a new family can reach the advisory ladder at most (see
`discovery_policy()`).

RANKING: `created_at`, NEVER THE ID
-----------------------------------
Model ids do not sort. `"claude-opus-10" < "claude-opus-5"` is True in Python —
a lexical sort ranks Opus 10 as OLDER than Opus 5 and would report "no drift"
on the day the successor ships. Every comparison here goes through the API's
`created_at`. The only thing the id is used for is GROUPING (which model line a
tier belongs to) and recognizing a dated snapshot of an alias — never ordering.

DEGRADE LIKE THE PROBE
----------------------
Any network/parse failure returns an EMPTY catalog. An empty catalog reports no
drift and callers keep resolving models off the static ladder: a catalog outage
must never block a dispatch. Results are cached in-process with the same TTL
pattern and injectable clock `model_fallback` uses, so the whole module is
unit-tested with fakes and makes NO network call in tests.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime, timezone

# The ONE auth seam (extracted from model_fallback's probe by this card): both
# the availability probe and this catalog build their headers here, so a change
# to how we authenticate can never apply to only half the calls. DISCOVERY is
# the policy for a model id we SEE but do not configure (DRE-2317): advisory,
# or nothing — never the build path.
from model_fallback import DISCOVERY, KNOWN_MODELS, auth_headers  # noqa: F401

CATALOG_URL = "https://api.anthropic.com/v1/models"

# Same TTL pattern as the availability cache: long enough that a burst of calls
# probes once, short enough that a newly released model shows up the same day.
CATALOG_TTL_SECONDS = 12 * 60

# Test hook, mirroring model_fallback's BUREAU_FAKE_AVAILABLE: a JSON
# /v1/models payload here makes the CLI exercisable with no network and no
# credential.
FAKE_CATALOG_ENV = "BUREAU_FAKE_CATALOG"

# Sorts before every real timestamp, so an entry with an unparseable/missing
# `created_at` is never mistaken for the newest model.
_UNKNOWN_TIME = datetime.min.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Fetch + parse                                                                #
# --------------------------------------------------------------------------- #

def parse_catalog(payload) -> list[dict]:
    """A /v1/models response → `[{id, display_name, created_at}, ...]`.

    Tolerant by design: junk, an error envelope, or a missing field yields an
    empty catalog / None values rather than an exception. This runs on a
    schedule against a vendor we do not control; a shape surprise degrades to
    "we learned nothing this week", never to a crash.
    """
    if not isinstance(payload, Mapping):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for entry in data:
        if not isinstance(entry, Mapping):
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        display = entry.get("display_name")
        created = entry.get("created_at")
        out.append(
            {
                "id": model_id,
                "display_name": display if isinstance(display, str) else None,
                "created_at": created if isinstance(created, str) else None,
            }
        )
    return out


def _fetch_real():
    """GET /v1/models with the shared auth headers. Returns the decoded payload,
    or `{}` when there is no credential (no request is attempted).

    Raises on a network/HTTP/JSON failure — `fetch_catalog` is the one place
    that decides what a failure means (an empty catalog), so the error is not
    swallowed twice.

    An HTTP failure carries the ENDPOINT and the response BODY (DRE-2923).
    urllib's own message is `HTTP Error 401: Unauthorized` and nothing else,
    and `fetch_catalog` turns any failure into an empty catalog — so this
    exception's own message is the only place the server's reason can survive.

    stdlib only (urllib); matches model_fallback.py's no-dependency style.
    """
    import urllib.error
    import urllib.request

    headers = auth_headers()
    if headers is None:
        return {}

    req = urllib.request.Request(CATALOG_URL, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310 - fixed https URL
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # pragma: no cover - defensive
            body = "<body unreadable>"
        raise RuntimeError(
            f"model catalog returned {exc.code} from {CATALOG_URL}: {body[:500]!r}"
        ) from exc


# In-process catalog cache: (catalog, expires_at). Mirrors the availability
# cache in model_fallback so both modules behave the same under a burst.
_CATALOG_CACHE: tuple[list[dict], float] | None = None


def clear_catalog_cache() -> None:
    """Drop the cached catalog (test/dispatch hook)."""
    global _CATALOG_CACHE
    _CATALOG_CACHE = None


def fetch_catalog(*, fetch=None, clock=None) -> list[dict]:
    """The live model catalog, cached for `CATALOG_TTL_SECONDS`.

    `fetch() -> payload` and `clock() -> float` are injectable, so tests drive
    this with a fake and make NO network call. Any failure — network, HTTP,
    JSON, or a shape we did not expect — returns an EMPTY catalog, and an
    empty result is deliberately NOT cached: an outage must not blind us for a
    whole TTL window, and callers fall back to the static ladder anyway.
    """
    global _CATALOG_CACHE
    if fetch is None:
        fetch = _fetch_real
    if clock is None:
        import time

        clock = time.monotonic

    now = clock()
    if _CATALOG_CACHE is not None and _CATALOG_CACHE[1] > now:
        return _CATALOG_CACHE[0]

    try:
        catalog = parse_catalog(fetch())
    except Exception:
        return []
    if not catalog:
        return []
    _CATALOG_CACHE = (catalog, now + CATALOG_TTL_SECONDS)
    return catalog


# --------------------------------------------------------------------------- #
# Ranking — by created_at, never by the id                                     #
# --------------------------------------------------------------------------- #

def created_at(entry) -> datetime:
    """One catalog entry's `created_at` as a comparable UTC datetime, or the
    unknown sentinel (older than everything) when it is missing or unparseable.

    The API sends ISO-8601 (`2026-08-01T00:00:00Z`); a bare date and an
    explicit offset both parse. A naive timestamp is read as UTC so it stays
    comparable with the rest.
    """
    raw = entry.get("created_at") if isinstance(entry, Mapping) else None
    if not isinstance(raw, str) or not raw.strip():
        return _UNKNOWN_TIME
    text = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return _UNKNOWN_TIME
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _known_created_at(entry) -> datetime | None:
    """`created_at`, or None when we genuinely do not know it. The distinction
    matters: we never raise an alert off missing data."""
    value = created_at(entry)
    return None if value == _UNKNOWN_TIME else value


def family(model_id: str) -> str:
    """The model LINE an id belongs to — `claude-opus-5` → `opus`,
    `claude-3-5-sonnet-20241022` → `sonnet`.

    Grouping only. Ordering is always by `created_at`; this just answers "is
    the newer thing a successor to THIS tier, or a different product line?" so
    the Sonnet fallback tier is not reported as stale every week merely because
    Opus exists.
    """
    tokens = [t for t in (model_id or "").lower().split("-") if t]
    if tokens and tokens[0] == "claude":
        tokens = tokens[1:]
    for token in tokens:
        if not token.isdigit():
            return token
    return model_id or ""


def _is_dated_snapshot_of(candidate_id: str, alias: str) -> bool:
    """True when `candidate_id` is the dated form of `alias`
    (`claude-opus-5-20260915` for `claude-opus-5`).

    A dated snapshot is the SAME model, so it is not drift. A point release
    that merely shares the prefix (`claude-opus-5-1`) is a different model and
    must still be reported — hence the 8-digit date test rather than a bare
    `startswith`.
    """
    if not candidate_id.startswith(alias + "-"):
        return False
    suffix = candidate_id[len(alias) + 1:]
    return len(suffix) == 8 and suffix.isdigit()


def _ladder_ids(ladder) -> list[str]:
    """Coerce a ladder into model-id strings — accepts the plain list
    model_fallback's LADDER uses AND agents.yaml's `[{model:, reason:}]` shape,
    the same two shapes model_fallback._normalize_ladder accepts."""
    if ladder is None or isinstance(ladder, (str, bytes)):
        return []
    out: list[str] = []
    for entry in ladder:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, Mapping) and isinstance(entry.get("model"), str):
            out.append(entry["model"])
    return out


def stale_ladder(ladder, catalog) -> list[dict]:
    """Every tier of `ladder` for which the catalog offers something NEWER.

    Returns `[{pinned, pinned_created_at, newer: [entry, ...]}, ...]` in ladder
    order, `newer` newest-first. Empty list = the ladder is current, the
    catalog is empty, or we cannot tell — this function never guesses.

    Rules, each one a trap we would otherwise fall into:
      * newer is decided by `created_at`. `claude-opus-10` sorts BEFORE
        `claude-opus-5` as a string; ranking on the id reports "current" on the
        exact day a successor ships.
      * a tier is compared within its own model line (`family`), so the Sonnet
        fallback tier is not "stale" merely because Opus exists — that would
        fire every week forever and train the alert into noise.
      * a pin we cannot find in the catalog, or an entry with no `created_at`,
        is NOT reported. Never raise on unknown/missing data.
      * the dated snapshot of a pinned alias is the same model, not drift.
    """
    findings: list[dict] = []
    for pinned in _ladder_ids(ladder):
        pinned_at = _resolve_pin(pinned, catalog)
        if pinned_at is None:
            continue
        newer = [
            entry
            for entry in catalog
            if family(entry["id"]) == family(pinned)
            and entry["id"] != pinned
            and not _is_dated_snapshot_of(entry["id"], pinned)
            and (_known_created_at(entry) or _UNKNOWN_TIME) > pinned_at
        ]
        if not newer:
            continue
        newer.sort(key=created_at, reverse=True)
        findings.append(
            {
                "pinned": pinned,
                "pinned_created_at": pinned_at.isoformat().replace("+00:00", "Z"),
                "newer": newer,
            }
        )
    return findings


def _resolve_pin(pinned: str, catalog) -> datetime | None:
    """When the pinned model was created, or None if the catalog cannot say.

    The ladder pins ALIASES (`claude-opus-5`); the API may list only the dated
    form (`claude-opus-5-20260801`). An exact id match wins; otherwise the
    newest dated snapshot of that alias stands in for it.
    """
    exact = [e for e in catalog if e["id"] == pinned]
    if exact:
        return _known_created_at(exact[0])
    dated = [e for e in catalog if _is_dated_snapshot_of(e["id"], pinned)]
    stamps = [t for t in (_known_created_at(e) for e in dated) if t is not None]
    return max(stamps) if stamps else None


# --------------------------------------------------------------------------- #
# Discovery — a model we have never configured (DRE-2317)                      #
# --------------------------------------------------------------------------- #

def discovery_policy() -> dict:
    """What may happen to a model id the system sees that config/models.yaml
    does not name: ``{"on_new_model": "advisory"|"none", "alert": bool}``.

    Read from the config, never decided here. Under the CEO's rule of
    2026-09-14, `on_new_model: advisory` means a NEW FAMILY may join the
    advisory ladder at most, and only after the CEO answers the spending
    question the adoption workflow files — `scripts/model_adoption.py`
    classifies it `ask`, and the price it asks about is the one declared in
    `config/model-prices.yaml`. A newer version of a family we already run is
    that rule's `adopt` case, not a discovery this policy governs.

    `workhorse` is not an expressible value — schema validation rejects it
    (`model_fallback.policy_errors`), because a newly available model joining
    the BUILD path with no human deciding is exactly the 2026-08-09 outage —
    and `judgement` is rejected with it.
    """
    return dict(DISCOVERY)


def new_models(catalog, known_ids=None) -> list[dict]:
    """Catalog entries whose id this pipeline has never been configured with,
    newest first.

    "Known" is the full read set — every ladder plus the retired and excluded
    ids — so a model we deliberately rotated out is not rediscovered every
    week. A DATED SNAPSHOT of a known alias (`claude-opus-5-20260801`) is the
    same model, not a discovery.

    Data only: this reports, it never adopts. `scripts/model_adoption.py`
    reads the list and applies the CEO's rule of 2026-09-14 to it.
    """
    if known_ids is None:
        known_ids = KNOWN_MODELS
    known = set(known_ids)
    found = [
        entry
        for entry in catalog or []
        if isinstance(entry, Mapping)
        and isinstance(entry.get("id"), str)
        and entry["id"] not in known
        and not any(_is_dated_snapshot_of(entry["id"], alias) for alias in known)
    ]
    found.sort(key=created_at, reverse=True)
    return found


# --------------------------------------------------------------------------- #
# models.json — the committed snapshot the console reads                       #
# --------------------------------------------------------------------------- #

def build_snapshot(catalog, previous=None, known_ids=None) -> dict:
    """The generated catalog snapshot: every model we have ever seen or can
    still read a marker for, newest first.

    DATA ONLY. This touches nothing selectable — no LADDER, no agents.yaml — so
    regenerating it can never adopt a model. It exists so agent-bureau's
    console can render a real display label with NO Anthropic credential and no
    second transport: it reads this file.

    `previous` is the snapshot on disk. Ids in it that the API no longer lists
    are KEPT, with `in_catalog: false` and the label they last had — a card
    stamped `model-error: claude-opus-4-8` must still render as "Claude Opus
    4.8" after the id rotates out. `known_ids` (default: `KNOWN_MODELS`) seeds
    ids the pipeline can still stamp but has never observed live, so the file
    is complete from its first write.
    """
    if known_ids is None:
        known_ids = KNOWN_MODELS

    merged: dict[str, dict] = {}
    for entry in (previous or {}).get("models", []) if isinstance(previous, Mapping) else []:
        if isinstance(entry, Mapping) and isinstance(entry.get("id"), str):
            merged[entry["id"]] = {
                "id": entry["id"],
                "display_name": entry.get("display_name"),
                "created_at": entry.get("created_at"),
                "in_catalog": False,  # until this fetch says otherwise
            }
    for model_id in known_ids:
        merged.setdefault(
            model_id,
            {
                "id": model_id,
                "display_name": None,
                "created_at": None,
                "in_catalog": False,
            },
        )
    for entry in catalog:
        merged[entry["id"]] = {
            "id": entry["id"],
            "display_name": entry.get("display_name"),
            "created_at": entry.get("created_at"),
            "in_catalog": True,
        }

    # Deterministic order — newest first, ties broken by id — so an unchanged
    # catalog produces a byte-identical file and therefore no commit.
    models = sorted(merged.values(), key=lambda m: m["id"])
    models.sort(key=created_at, reverse=True)
    return {"source": CATALOG_URL, "models": models}


def load_snapshot(path) -> dict:
    """The snapshot on disk, or an empty one when it is missing/unreadable."""
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"source": CATALOG_URL, "models": []}
    if not isinstance(data, Mapping) or not isinstance(data.get("models"), list):
        return {"source": CATALOG_URL, "models": []}
    return data


def snapshot_catalog(snapshot) -> list[dict]:
    """The snapshot read back AS a catalog — only the models the API still
    lists. A retired entry is kept for labelling, never offered as an upgrade.
    """
    return [
        {
            "id": m["id"],
            "display_name": m.get("display_name"),
            "created_at": m.get("created_at"),
        }
        for m in snapshot.get("models", [])
        if isinstance(m, Mapping) and isinstance(m.get("id"), str) and m.get("in_catalog")
    ]


def write_snapshot(path, snapshot) -> bool:
    """Write the snapshot; return True iff the file's bytes changed.

    An unchanged catalog is a no-op, which is what keeps the weekly workflow
    from committing an empty diff every Monday.
    """
    text = json.dumps(snapshot, indent=2, sort_keys=False) + "\n"
    try:
        with open(path) as fh:
            if fh.read() == text:
                return False
    except OSError:
        pass
    with open(path, "w") as fh:
        fh.write(text)
    return True


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _fake_catalog_from_env():
    """Test hook: `BUREAU_FAKE_CATALOG` (a JSON /v1/models payload) replaces
    the network fetch, mirroring model_fallback's BUREAU_FAKE_AVAILABLE."""
    raw = os.environ.get(FAKE_CATALOG_ENV, "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return lambda: payload


def _cmd_snapshot(path: str) -> int:
    """Refresh `path` from the live catalog. Never fails the run on an outage,
    and never blanks a good file with an empty result."""
    clear_catalog_cache()
    catalog = fetch_catalog(fetch=_fake_catalog_from_env())
    if not catalog:
        print(
            "::warning::catalog fetch returned nothing — leaving the existing "
            "snapshot alone"
        )
        return 0
    previous = load_snapshot(path)
    changed = write_snapshot(path, build_snapshot(catalog, previous=previous))
    print(f"{'wrote' if changed else 'unchanged'} {path} ({len(catalog)} models)")
    return 0


def main(argv: list[str]) -> int:
    """CLI for the snapshot workflow.

      snapshot [<path>]                 refresh the committed catalog snapshot
                                        (default models.json) from /v1/models

    One command. `check-drift` and `check-new` opened Linear cards and were
    removed by DRE-3899; they are unknown commands now (exit 2). No command
    touches a ladder, config/models.yaml or agents.yaml — the adoption rule
    lives in scripts/model_adoption.py.
    """
    if not argv:
        print("usage: model_catalog.py snapshot [<path>]")
        return 2
    cmd, *rest = argv
    if cmd == "snapshot":
        return _cmd_snapshot(rest[0] if rest else "models.json")
    print(f"unknown command {cmd!r}")
    return 2


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
