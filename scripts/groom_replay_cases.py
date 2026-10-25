#!/usr/bin/env python3
"""Every removal a person records with a reason, kept as a replay case — and
the two stored proposals the replay runs the cases through (DRE-7971).

A removal taught nothing to the next morning's check. The memory (DRE-7824,
DRE-7826) keeps a removed card off later proposals, but the verify step never
saw what it got wrong, and `groom_replay.py` (DRE-7239) had one stored
morning to re-run it over. This module builds the rest from what people
decided:

  * `cases.json` — one case per proposal and card a person removed it from,
    with the reason they gave;
  * `replay-set.json` — every case card, as a proposal `groom_replay.py base`
    accepts, which the Sunday job replays on `main`;
  * `gate-set.json` — only the cards of cases that pass on `main`, newest
    first and at most `GATE_MAX_CARDS`, which the replay gate replays on a
    pull request.

## What a case is

`groom_memory.all_decisions` gives every decision, uncollapsed — the door's
rows and every standing card's markers, the memory's reading of both. A
decision is a case when it names a proposal whose comment is on a standing
card's thread, the card was on that proposal's Planning or Cancel list and
the decision is a removal from it (`REMOVALS`), and it carries a reason
(`reason_label` is not empty). A Planning card a person took off is
`off-planning`; a Cancel card a person kept is `off-cancel`; a Cancel card a
person also canceled agreed with the groomer and is no case. A removal with
no reason is counted on the `build` line as `no reason` and is never a case.

The case id is `<proposal_id>-<card>`: a card removed on two proposals is two
cases, whatever was decided between or after them; two removals of one card
on one proposal are that proposal's newer one.

## Merging

`build` merges into the existing `cases.json`. A case is never dropped, and
its `main` block — where the card ended on `main`, and whether that passes —
is never changed here: only the gate's `record-main` writes it. `SEED_CASES`
is in every build, never rewritten: a known-good case, so the set is never
empty and a broken verify step has a case to regress. `built_at` is the
newest decision the build read, so the same input writes the same bytes.

  python3 scripts/groom_replay_cases.py build [--from <file>] [--cases …]
      [--set …] [--gate-set …]

`--from` reads the door's answer and the threads from a JSON file instead of
the door and Linear, for tests: `{"removals": {"reasons", "decisions"}` or
`"door_problem": "<why>"`, `"threads": {<standing card>: [<comment records>]}}`.
It prints one line, opening `BUILD_TAG`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bureau_read  # noqa: E402 — the console's record of every decision
import groom_memory  # noqa: E402 — all_decisions, the standing cards
import groom_replay  # noqa: E402 — the lists a card ends on
import groomer  # noqa: E402 — the thread readers and the proposal parser

ROOT = Path(__file__).resolve().parent.parent

SCHEMA = "groom-replay-cases/1"
CASES_PATH = "tests/fixtures/groom-replay-cases/cases.json"
SET_PATH = "tests/fixtures/groom-replay-cases/replay-set.json"
GATE_SET_PATH = "tests/fixtures/groom-replay-cases/gate-set.json"

#: The most distinct cards one gated pull request replays — about $18 at the
#: 2026-10 per-card measurement of $0.36. The oldest passing cases past it
#: stay in `cases.json` and are replayed every Sunday on `main`.
GATE_MAX_CARDS = 50

SEED_SOURCE = "seed"
SEEDS = ("DRE-7239",)
EXPECTS = ("off-planning", "off-cancel")

#: The pairing: a decision on a list's row is a removal only when its kind is
#: in that list's tuple. A Cancel-list `cancel` agreed with the groomer.
REMOVALS = {"planning": ("not-doing", "hold", "cancel"),
            "cancel": ("not-doing", "hold")}
EXPECT_OF = {"planning": "off-planning", "cancel": "off-cancel"}

BUILD_TAG = "groom-replay-cases:"

#: A case's keys, in the order the file writes them.
CASE_KEYS = ("case", "card", "proposal_id", "standing_card", "list",
             "decision", "reason", "reason_label", "recorded_at", "source",
             "expect", "title", "repo", "main")


def _unjudged() -> dict:
    return {"ended": None, "at": None, "passes": None}


#: DRE-7239's own work, `scripts/groom_replay.py`, is merged on `main` with
#: its pull request, and it has no children and no hand mark — so a correct
#: verify step answers `done-elsewhere` and puts it on the Cancel list.
SEED_CASES = ({
    "case": "seed-DRE-7239", "card": "DRE-7239", "proposal_id": None,
    "standing_card": None, "list": "planning", "decision": None,
    "reason": None,
    "reason_label": ("seed: known-good — the card's work is merged, so the "
                     "verify step takes it off the Planning list"),
    "recorded_at": "2026-10-25T00:00:00Z", "source": SEED_SOURCE,
    "expect": "off-planning",
    "title": ("bureau-pipeline: groom_replay.py turns a stored groom proposal "
              "into a run's input and reports where each card ended"),
    "repo": "bureau-pipeline", "main": _unjudged()},)

#: Where a card ended that says nothing about the verify step's answer.
_UNJUDGED_ENDS = (None, groom_replay.EXCLUDED, groom_replay.UNPLACED)


class CasesError(Exception):
    """An input `build` cannot act on — said, and exit 2."""


# --------------------------------------------------------------------------- #
# passes                                                                       #
# --------------------------------------------------------------------------- #

def passes(case: dict, ended: str | None, *, judged: bool) -> bool | None:
    """Did the replay take the card where the person did? None when nothing
    judged it — `judged` False, or it ended nowhere, excluded or unplaced."""
    if not judged or ended in _UNJUDGED_ENDS:
        return None
    if case["expect"] == "off-planning":
        return ended != groom_replay.PLANNING
    if case["expect"] == "off-cancel":
        return ended != groom_replay.CANCEL
    raise ValueError(f"{case.get('case')}: expect {case['expect']!r} is not one "
                     f"of {EXPECTS}")


# --------------------------------------------------------------------------- #
# the stored sets                                                              #
# --------------------------------------------------------------------------- #

def _set(cards: list[dict], name: str) -> dict:
    """A proposal in the shape `groomer.py propose` writes, holding `cards`
    on the Planning list in order and nothing else — so the verify window is
    exactly those cards, each judged as a Planning card, and the page and the
    report after the legs read it as any morning."""
    rows = []
    for position, card in enumerate(cards, 1):
        rows.append({"identifier": card["card"], "title": card["title"],
                     "position": position, "unit": card["card"], "epic": None,
                     "repo": card["repo"], "band": None, "cycle": None,
                     "cycle_id": None, "projected": False, "open_children": 0,
                     "reason": "", "trigger": None, "evidence": None,
                     "judged": False, "reasons": {}})
    census: dict[str, int] = {}
    for row in rows:
        census[row["repo"]] = census.get(row["repo"], 0) + 1
    return {
        "id": name, "generated_at": None, "lane": "Intake",
        "population": len(rows), "census": census, "held_repos": [],
        "offered": len(rows), "held_blockers": [], "capacity": len(rows),
        "cycle_days": 14, "window_days": 14,
        "older_than_window": {"days": 14, "cards": 0, "line": ""},
        "batch": {"cycles": [], "cards": len(rows)},
        "repo_order": list(census),
        "sequence": [dict(row) for row in rows],
        "outcomes": {"now": rows, "not-now": [], "dead": []},
        "collisions": {"pairs": [], "boilerplate": {}, "unreadable": []},
        "unhonoured_constraints": [], "unstated_supersessions": [],
        "cancels_refused": [],
    }


def _distinct(ordered, cases) -> list[dict]:
    """One entry per card, in the order `ordered` first names it, carrying
    the title and repo of its newest case in `cases`."""
    newest = {case["card"]: case for case in _sorted(cases)}
    cards = dict.fromkeys(case["card"] for case in ordered)
    return [{"card": card, "title": newest[card]["title"],
             "repo": newest[card]["repo"]} for card in cards]


def replay_set(cases: list[dict]) -> dict:
    """What `replay-set.json` holds: every case card, once, in case order."""
    return _set(_distinct(_sorted(cases), cases), "groom-replay-set")


def _passing_newest_first(cases: list[dict]) -> list[dict]:
    passing = [c for c in _sorted(cases) if (c.get("main") or {}).get("passes") is True]
    return _distinct(reversed(passing), cases)


def gate_set(cases: list[dict], *, limit=GATE_MAX_CARDS) -> dict:
    """What `gate-set.json` holds: the cards of cases whose `main.passes` is
    true — the only cases that can hold a pull request — newest `recorded_at`
    first, at most `limit` cards."""
    return _set(_passing_newest_first(cases)[:limit], "groom-replay-gate-set")


# --------------------------------------------------------------------------- #
# the cases                                                                    #
# --------------------------------------------------------------------------- #

def _sort_key(case: dict):
    return (groom_memory._instant(case["recorded_at"]),
            groom_memory._card_sort_key(case["card"]), case["case"])


def _sorted(cases) -> list[dict]:
    return sorted(cases, key=_sort_key)


def _cancel_titles(body: str) -> dict[str, str]:
    """`{card: title}` off the Cancel table, the sixth cell —
    `parse_proposal_comment` reads the cells around it and not that one."""
    out = {}
    for line in groomer._section(body, groomer.CANCEL_HEADING):
        if not groomer._BATCH_ROW.match(line.strip()):
            continue
        cells = [c.strip() for c in groomer._UNESCAPED_PIPE.split(line.strip())[1:-1]]
        if len(cells) > 5:
            out[cells[1]] = cells[5].replace("\\|", "|")
    return out


def proposals(threads: dict[str, list[dict]]) -> dict[str, dict]:
    """`{proposal id: {"planning": {card: row}, "cancel": {card: row}}}` off
    every proposal comment on the threads, each row `{"title", "repo"}`. The
    first comment carrying an id is that proposal."""
    out: dict[str, dict] = {}
    for records in threads.values():
        for record in records:
            body = record.get("body") or ""
            parsed = groomer.parse_proposal_comment(body)
            if parsed is None or parsed["id"] in out:
                continue
            titles = _cancel_titles(body)
            out[parsed["id"]] = {
                "planning": {r["identifier"]: {"title": r.get("title") or "",
                                               "repo": r.get("repo") or ""}
                             for r in parsed["batch"]},
                "cancel": {r["identifier"]: {"title": titles.get(r["identifier"], ""),
                                             "repo": r.get("repo") or ""}
                           for r in parsed["cancel"]},
            }
    return out


def cases_from(decisions: list[dict], posted: dict[str, dict]) -> tuple[list[dict], int]:
    """`(cases, no reason)` — one case per proposal and card, the newer
    removal on one proposal winning, and how many removals carried no
    reason. `decisions` are `all_decisions`', oldest first."""
    made: dict[str, dict] = {}
    no_reason = 0
    for decision in decisions:
        proposal = posted.get(decision.get("proposal_id"))
        if proposal is None:
            continue
        card = decision["card"]
        on = next((name for name in ("planning", "cancel")
                   if card in proposal[name]), None)
        if on is None or decision["decision"] not in REMOVALS[on]:
            continue
        if not decision.get("reason_label"):
            no_reason += 1
            continue
        row = proposal[on][card]
        case_id = f"{decision['proposal_id']}-{card}"
        made[case_id] = {
            "case": case_id, "card": card,
            "proposal_id": decision["proposal_id"],
            "standing_card": decision["standing_card"], "list": on,
            "decision": decision["decision"], "reason": decision["reason"],
            "reason_label": decision["reason_label"],
            "recorded_at": decision["recorded_at"],
            "source": decision["source"], "expect": EXPECT_OF[on],
            "title": row["title"], "repo": row["repo"], "main": _unjudged()}
    return list(made.values()), no_reason


def merge(existing: list[dict], made: list[dict]) -> tuple[list[dict], int]:
    """`(cases, new)` — every existing case kept, its `main` block as it was;
    a seed never rewritten, and put back when it is missing."""
    by_id = {c["case"]: c for c in existing}
    new = 0
    for seed in SEED_CASES:
        by_id.setdefault(seed["case"], json.loads(json.dumps(seed)))
    seeds = {seed["case"] for seed in SEED_CASES}
    for case in made:
        old = by_id.get(case["case"])
        if old is None:
            new += 1
            by_id[case["case"]] = case
        elif case["case"] not in seeds:
            by_id[case["case"]] = {**case, "main": old.get("main") or _unjudged()}
    return _sorted(by_id.values()), new


# --------------------------------------------------------------------------- #
# reading                                                                      #
# --------------------------------------------------------------------------- #

class _Threads:
    """`linear_ops.comment_records` over the `--from` file's threads."""

    def __init__(self, threads: dict[str, list[dict]]):
        self.threads = threads

    def comment_records(self, identifier, *, whole_thread=False):
        return [dict(r) for r in self.threads.get(identifier, [])]


def _read(lops, standing, rows, reasons, door_problem) -> dict:
    threads = {card: groomer.decision_records(lops, card, whole_thread=True)
               for card in standing}
    markers = [marker for card in standing
               for marker in groomer.per_card_markers(threads[card], card)]
    return {"rows": rows, "reasons": reasons, "door_problem": door_problem,
            "threads": threads, "markers": markers}


def read_live(lops, current: str | None) -> dict:
    """The inputs `groomer.read_memory` reads, never through it — its answer
    is the newest decision per card, and an older removal is a case too."""
    try:
        answer = bureau_read.groom_removals()
        rows, reasons, door_problem = answer["decisions"], answer["reasons"], None
    except bureau_read.ReadUnknown as e:
        rows, reasons = [], None
        door_problem = f"{e.reason}: {e.detail}" if e.detail else e.reason
    return _read(lops, groom_memory.standing_cards(current), rows, reasons,
                 door_problem)


def read_from(path: str) -> dict:
    """The same inputs, from a `--from` file."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise CasesError(f"{path} could not be read: {e}")
    threads = doc.get("threads") or {}
    if not isinstance(threads, dict):
        raise CasesError(f"{path}: threads is not an object")
    door_problem = doc.get("door_problem") or None
    removals = doc.get("removals") or {}
    rows = [] if door_problem else list(removals.get("decisions") or [])
    reasons = None if door_problem else removals.get("reasons")
    return _read(_Threads(threads), list(threads), rows, reasons, door_problem)


# --------------------------------------------------------------------------- #
# build                                                                        #
# --------------------------------------------------------------------------- #

def _load_cases(path: Path) -> dict:
    if not path.exists():
        return {"schema": SCHEMA, "built_at": None, "cases": []}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise CasesError(f"{path} could not be read: {e}")
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA \
            or not isinstance(doc.get("cases"), list):
        raise CasesError(f"{path} is not a {SCHEMA} file")
    return doc


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def _newest(stamps) -> str | None:
    stamps = [s for s in stamps if s]
    return max(stamps, key=groom_memory._instant) if stamps else None


def build(read: dict, *, cases_path: Path, set_path: Path,
          gate_path: Path) -> str:
    """Merge the cases `read` gives into `cases_path`, write all three files,
    and return the one line."""
    decisions = groom_memory.all_decisions(
        read["rows"], read["markers"], reasons=read["reasons"],
        door_problem=read["door_problem"])
    made, no_reason = cases_from(decisions, proposals(read["threads"]))
    doc = _load_cases(cases_path)
    cases, new = merge(doc["cases"], made)
    built_at = _newest([doc.get("built_at")]
                       + [d["recorded_at"] for d in decisions])
    _write(cases_path, {"schema": SCHEMA, "built_at": built_at, "cases": cases})
    _write(set_path, replay_set(cases))
    limit = GATE_MAX_CARDS
    gate = gate_set(cases, limit=limit)
    _write(gate_path, gate)
    over = len(_passing_newest_first(cases)) - len(gate["outcomes"]["now"])
    source = "fallback" if read["door_problem"] else "record"
    return (f"{BUILD_TAG} build {len(cases)} cases ({new} new) — {no_reason} no "
            f"reason — gate {len(gate['outcomes']['now'])} cards ({over} over "
            f"the ceiling) — source={source}")


# --------------------------------------------------------------------------- #
# the command line                                                             #
# --------------------------------------------------------------------------- #

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build", help="merge the recorded removals into the cases")
    p.add_argument("--from", dest="source",
                   help="read the door's answer and the threads from this file")
    p.add_argument("--cases", default=str(ROOT / CASES_PATH))
    p.add_argument("--set", dest="replay_set", default=str(ROOT / SET_PATH))
    p.add_argument("--gate-set", default=str(ROOT / GATE_SET_PATH))
    return parser


def main(argv=None, *, lops=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.source:
            read = read_from(args.source)
        else:
            if lops is None:
                import linear_ops as lops
            read = read_live(lops, os.environ.get("GROOM_PROPOSAL_CARD") or None)
        print(build(read, cases_path=Path(args.cases),
                    set_path=Path(args.replay_set),
                    gate_path=Path(args.gate_set)))
        return 0
    except CasesError as e:
        print(f"{BUILD_TAG} {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
