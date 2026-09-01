#!/usr/bin/env python3
"""The pipeline's acts (DRE-2825) — one registry, one writer.

An ACT is something the pipeline does on its own and then announces: a
refusal, a recovery, or a hold. `config/pipeline-acts.json` declares every one
of them — its tag, its kind, the state it leaves the work in, the next actor,
what it discharges, and the workflow expected to act on it. This module is the
only reader and the only writer, exactly as `routing_verdict.py` is for the
routing vocabulary.

## Why a registry at all

Before this file the vocabulary was remembered in six places: ten tag
constants scattered through `reconcile.py`, the console pattern-matching
separate string markers, a sweep deciding "was that the bot" from an emoji
prefix. Every reader scraped the prose independently, so every reader could be
wrong on its own — and none of them could be checked against the others.

## The trailer is ADDITIVE, and that is the whole safety argument

The tags in `reconcile.py` are **not prose**. They are idempotency keys and
per-sha budget counters: the sweep suppresses a repeat notice with `tag in
body`, and `reconcile._worker_receipt_count` enforces
`CRASHED_REVIEW_RETRY_CAP` and `DEPENDABOT_RECEIPT_CAP` by counting the
receipts that carry one.

**Change the grammar and every in-flight pull request's existing receipts
become invisible: budgets re-arm from zero and suppressed comments re-post.**
So the registry ADOPTS those tags as data rather than replacing them, and
`receipt()` returns the caller's body BYTE-IDENTICALLY with a trailer appended
after it. It cannot normalise, strip, reflow or re-order — the tests pin that,
because the console tests pin some of the live wording verbatim.

Nothing is deleted by this module either. `reconcile._AGENT_COMMENT_PREFIXES`
decides *"a human spoke after the blocker, treat it as resolved"*; deleting it
would let any non-adopting machine commenter silently clear an open blocker. It
stays until every commenter emits a trailer, and its removal is a separate,
later card.

## Names are not tags, and that is load-bearing

A trailer carries its own act's name and the name of whatever it discharges.
If a name contained another act's tag, an idempotency check keyed on that tag
would read this receipt as the other act's and suppress it forever — the
additive trailer would stop being additive, by the same mechanism the warning
above describes. `problems()` refuses it, and refuses a tag that is a substring
of another tag for the same reason.

## Who emits (DRE-2826)

Every declared act now composes its receipt here. `reconcile.py` calls
`receipt()` directly; `agent-fix.yml` and `medic.yml` reach the same writer
through the two CLI seams below (`pipeline_act.py receipt … --out` for a PR
comment, `linear_ops.py comment … --act=` for a card comment). Which of them
still does not is not a matter of memory:
`scripts/check_act_receipts.py` reads every `gh pr comment`, `gh issue comment`
and `_post_pr_note` in `scripts/` and `.github/workflows/` and fails CI on any
whose body is not composed here — unless the registry's `unconverted` block
names it, with the reason. That block is the countable record of the receipts
this repo posts with no trailer, and a row in it must match exactly one real
site or the check fails on the row itself.

## What this module does NOT do

The refusal signal, the discharge sweep and the console read are each their own
card. This one lands the vocabulary and the writer.

`subscriber` is DECLARED here and RESOLVED elsewhere: asserting that it names a
workflow which exists and accepts that trigger is DRE-2827, and it has to be an
extension of the shipped `check_workflow_watchers.py`, because `workflow_call`
reusables run under the CALLER's workflow name. A second, independent
derivation of the same fact beside the shipped one is the hazard this epic
exists for.

CLI:

    python3 scripts/pipeline_act.py check          # validate the file
    python3 scripts/pipeline_act.py list           # print the acts as JSON
    python3 scripts/pipeline_act.py receipt <act> --body <body> --out <file>

The third is the SHELL seam (DRE-2826). A workflow step composes its body here
and posts the file, rather than assembling a trailer in bash — one writer for
Python and shell alike, because two writers is how the grammar drifts. It
writes nothing on an unknown act: a half-written file would be posted as if it
had been composed.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lane_contract  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
CONFIG_PATH = os.path.join(ROOT, "config", "pipeline-acts.json")

# The trailer's own marker. A NEW one on purpose: every existing mark already
# means something the pipeline reads (🚨 a watchdog notice, 🔁 a re-dispatch
# receipt, 🧭 a routing verdict, 🧾 a proof-and-demo bounce), and reusing one
# would make the trailer answer a question it was not asked.
TRAILER_MARK = "📎"
TRAILER_TAG = "pipeline-act"

# Anchored at the start of its own line, never a substring search — the same
# discipline `routing_verdict._VERDICT_LINE` carries, and for the same reason:
# a bare substring match over prose deadlocked a live epic for five days
# (DRE-2670).
_TRAILER_LINE = re.compile(rf"^\s*{TRAILER_MARK}\s*{TRAILER_TAG}:\s*(.+?)\s*$")

# `NAME_TAG = "literal"` at module level. Aliases (`DEAD_TAG =
# dead_run.DEAD_TAG`) are deliberately out of scope: the string lives once, in
# the module that owns it, and that module is not an emitter declared here.
_TAG_CONSTANT = re.compile(r'(?m)^([A-Z][A-Z0-9_]*_TAG)\s*=\s*"([^"]+)"')

# Where a not-yet-emitted tag must NOT appear. The corpus is every place the
# pipeline writes a receipt from; this module is excluded because the tags live
# in the JSON, not here, and tests/ is excluded because a test naming a tag is
# not an emission.
_SCAN_GLOBS = ("scripts/*.py", ".github/workflows/*.yml")

# A slug, like every other machine-readable key in this pipeline.
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_SEPARATOR = " · "
_NOTHING = "nothing"


class ActError(RuntimeError):
    """The registry is malformed, or a receipt was asked for with no body.

    Raised rather than defaulted: an act that silently loses its body is an act
    whose idempotency key no longer covers anything.
    """


class UnknownAct(Exception):
    """An act name the registry does not carry.

    Raised rather than guessed. Defaulting an unknown act to some other act's
    tag is precisely the collision this module exists to make impossible.
    """


# --------------------------------------------------------------------------- #
# loading                                                                      #
# --------------------------------------------------------------------------- #

_CACHE: dict = {}


def load(path: str | None = None) -> dict:
    """Parse the registry. Cached per path — a sweep reads it per card, and a
    file read per card is a file read per card."""
    path = path or CONFIG_PATH
    if path not in _CACHE:
        try:
            with open(path, encoding="utf-8") as fh:
                _CACHE[path] = json.load(fh)
        except (OSError, ValueError) as e:
            raise ActError(f"cannot read the act registry at {path}: {e}") from e
    return _CACHE[path]


def _records(doc: dict | None = None) -> tuple:
    return tuple((doc or load())["acts"])


def acts(doc: dict | None = None) -> tuple:
    """The act names, in the file's own order."""
    return tuple(entry["name"] for entry in _records(doc))


def record(name: str, doc: dict | None = None) -> dict:
    for candidate in _records(doc):
        if candidate["name"] == name:
            return candidate
    raise UnknownAct(
        f"{name!r} is not a pipeline act — the registry declares "
        f"{', '.join(acts(doc))}"
    )


def tag(name: str, doc: dict | None = None) -> str:
    """The act's idempotency key. For an adopted act this is the EXISTING
    constant's value and may never be reworded — see the module docstring."""
    return record(name, doc)["tag"]


def kind(name: str, doc: dict | None = None) -> str:
    """refusal · recovery · hold."""
    return record(name, doc)["kind"]


def state(name: str, doc: dict | None = None) -> str:
    """The state the act leaves the WORK in — never the state of the run."""
    return record(name, doc)["state"]


def next_actor(name: str, doc: dict | None = None) -> str:
    """Who acts next. A permitted writer in the lane contract's glossary, so an
    act cannot hand the work to somebody the contract has never heard of."""
    return record(name, doc)["next_actor"]


def discharges(name: str, doc: dict | None = None) -> str | None:
    """The prior obligation this act answers, as an ACT NAME — never a tag, so
    a trailer can never carry another act's live key."""
    return record(name, doc).get("discharges")


def subscriber(name: str, doc: dict | None = None) -> str:
    """The workflow expected to act on this receipt. Declared here; resolving
    it against the shipped watcher check is DRE-2827."""
    return record(name, doc)["subscriber"]


def kinds(doc: dict | None = None) -> tuple:
    return _vocabulary("kinds", doc)


def states(doc: dict | None = None) -> tuple:
    return _vocabulary("states", doc)


def _vocabulary(block: str, doc: dict | None = None) -> tuple:
    return tuple(k for k in (doc or load())[block] if not k.startswith("_"))


# --------------------------------------------------------------------------- #
# writing a receipt                                                            #
# --------------------------------------------------------------------------- #


def trailer(name: str, doc: dict | None = None) -> str:
    """The machine-readable line an act's receipt ends with.

    One line, on purpose: every reader that scrapes today already reads line by
    line, and a block would have to be reassembled by each of them — which is
    the defect, one layer down.
    """
    entry = record(name, doc)
    return _SEPARATOR.join([
        f"{TRAILER_MARK} {TRAILER_TAG}: {entry['name']}",
        f"kind: {entry['kind']}",
        f"state: {entry['state']}",
        f"next: {entry['next_actor']}",
        f"discharges: {entry.get('discharges') or _NOTHING}",
        f"subscriber: {entry['subscriber']}",
        f"tag: {entry['tag']}",
    ])


def receipt(act: str, detail: str, doc: dict | None = None) -> str:
    """`detail` byte-identical, then the trailer. Nothing else, ever.

    `detail` is the body the call site already writes. It is returned
    UNTOUCHED — not stripped, not reflowed, not re-ordered — because every one
    of these bodies carries a live idempotency key and some of them are pinned
    verbatim by the console's tests. The trailer is the only addition, and it
    goes last so nothing that reads a prefix can see it.
    """
    record(act, doc)  # raises UnknownAct before anything is written
    if not (detail or "").strip():
        raise ActError(
            f"a {act} receipt must carry a body. An empty receipt still burns "
            "the act's idempotency key, so the next sweep would suppress the "
            "real notice and the reader would never learn what happened."
        )
    return f"{detail}\n\n{trailer(act, doc)}"


def read_trailer(body: str) -> dict | None:
    """The trailer's fields, or None if `body` carries none.

    The counterpart to `trailer()`, so a reader never re-derives the grammar.
    `discharges: nothing` reads back as None, which is the same absence the
    registry writes as `null`.
    """
    for line in (body or "").splitlines():
        match = _TRAILER_LINE.match(line)
        if not match:
            continue
        parts = match.group(1).split(_SEPARATOR)
        fields = {"act": parts[0].strip()}
        for part in parts[1:]:
            key, _, value = part.partition(":")
            fields[key.strip()] = value.strip()
        if fields.get("discharges") == _NOTHING:
            fields["discharges"] = None
        return fields
    return None


# --------------------------------------------------------------------------- #
# the file checks itself                                                       #
# --------------------------------------------------------------------------- #


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _corpus() -> dict:
    """Every file the pipeline writes a receipt from, by repo-relative path."""
    out: dict[str, str] = {}
    for pattern in _SCAN_GLOBS:
        for path in sorted(glob.glob(os.path.join(ROOT, pattern))):
            relative = os.path.relpath(path, ROOT)
            if relative == os.path.join("scripts", "pipeline_act.py"):
                continue  # the tags live in the JSON, not in the reader
            text = _read(path)
            if text is not None:
                out[relative] = text
    return out


def problems(doc: dict | None = None) -> list:
    """Everything wrong with the registry, or an empty list.

    Three families, and each one exists because its absence is a live failure
    mode rather than an untidiness:

      * the vocabulary binds — kind, state and next actor are drawn from
        declared sets, and the actor from the LANE CONTRACT's writer glossary,
        so an act cannot hand work to somebody nothing knows about;
      * the tags bind BOTH WAYS — a declared tag no code emits fails, and a tag
        the code emits that nothing declares fails;
      * the trailer stays additive — no act's name may contain any act's tag,
        and no tag may be a substring of another, because `tag in body` is how
        every one of these is counted.
    """
    doc = doc if doc is not None else load()
    out: list[str] = []
    try:
        writers = set(lane_contract.writers())
    except Exception as e:  # noqa: BLE001 — an unreadable contract is a problem
        return [f"the lane contract could not be read, so no act can be bound to it: {e}"]

    entries = _records(doc)
    if not entries:
        return ["the registry declares no acts at all"]

    names = [e.get("name") or "" for e in entries]
    tags = [e.get("tag") or "" for e in entries]
    known_kinds, known_states = kinds(doc), states(doc)

    for entry in entries:
        out.extend(_entry_problems(entry, doc, writers, known_kinds, known_states, names))

    for value, what in ((names, "name"), (tags, "tag")):
        for duplicate in sorted({v for v in value if value.count(v) > 1}):
            out.append(
                f"the {what} {duplicate!r} is declared twice — an act's {what} "
                "is a key, and two acts sharing one makes each invisible to "
                "whichever reader finds the other first"
            )

    out.extend(_collision_problems(entries, tags))
    out.extend(_binding_problems(entries, tags))
    return out


def _entry_problems(entry, doc, writers, known_kinds, known_states, names) -> list:
    out: list[str] = []
    name = entry.get("name") or "<unnamed>"

    for key in ("name", "tag", "kind", "state", "next_actor", "subscriber", "means", "why"):
        if not (entry.get(key) or "").strip():
            out.append(f"act {name!r} says nothing for {key!r}")
    if "discharges" not in entry:
        out.append(
            f"act {name!r} does not say what it discharges — an act that "
            "answers no prior obligation says so with null, never by omission"
        )
    if not isinstance(entry.get("adopted"), bool):
        out.append(
            f"act {name!r} does not say whether its tag is ADOPTED (an existing "
            "idempotency key that may never be reworded) or newly declared"
        )
    for key in ("name", "tag"):
        value = entry.get(key) or ""
        if value and not _SLUG.match(value):
            out.append(f"act {name!r} has a {key} {value!r} that is not a slug")

    if entry.get("kind") not in known_kinds:
        out.append(
            f"act {name!r} is of kind {entry.get('kind')!r}, which the registry "
            f"does not declare — the kinds are {', '.join(known_kinds)}"
        )
    if entry.get("state") not in known_states:
        out.append(
            f"act {name!r} leaves the work in {entry.get('state')!r}, which the "
            f"registry does not declare — the states are {', '.join(known_states)}"
        )
    if entry.get("next_actor") not in writers:
        out.append(
            f"act {name!r} hands the work to {entry.get('next_actor')!r}, which "
            "is not in the lane contract's writer glossary — an act with no "
            "next actor is work nobody is coming for"
        )
    discharged = entry.get("discharges")
    if discharged is not None and discharged not in names:
        out.append(
            f"act {name!r} discharges {discharged!r}, which is not a declared "
            "act — an obligation nothing declares cannot be answered"
        )
    if discharged == entry.get("name"):
        out.append(f"act {name!r} discharges itself")

    out.extend(_emitter_problems(entry))
    return out


def _emitter_problems(entry) -> list:
    """Does the code this act claims to be emitted from actually say so?"""
    out: list[str] = []
    name = entry.get("name") or "<unnamed>"
    emits = entry.get("emits") or {}
    relative, anchor = emits.get("file") or "", emits.get("anchor") or ""
    if not relative or not anchor:
        return [
            f"act {name!r} does not say where it is emitted — a registry that "
            "cannot be checked against the code is a description, not a registry"
        ]
    text = _read(os.path.join(ROOT, relative))
    if text is None:
        return [f"act {name!r} names the emitter {relative!r}, which does not exist"]
    found = text.count(anchor)
    if found != 1:
        out.append(
            f"act {name!r} pins the anchor {anchor!r} in {relative}, which "
            f"carries it {found} time(s) — an anchor that is absent pins "
            "nothing, and an ambiguous one pins the wrong thing"
        )
    return out


def _collision_problems(entries, tags) -> list:
    """The half that keeps the trailer additive.

    `tag in body` is how every one of these receipts is suppressed and
    counted, so any tag that can appear in a body it does not belong to is a
    budget counter reading the wrong receipts.
    """
    out: list[str] = []
    for entry in entries:
        name = entry.get("name") or ""
        for other in tags:
            if other and other in name:
                out.append(
                    f"the act name {name!r} contains the tag {other!r} — a "
                    "trailer carries act NAMES, so this name would put a live "
                    "idempotency key into receipts it does not belong to and "
                    "suppress them forever"
                )
    for outer in tags:
        for inner in tags:
            if inner and outer and inner != outer and inner in outer:
                out.append(
                    f"the tag {inner!r} is contained in the tag {outer!r} — "
                    "`tag in body` would count one act's receipts as the "
                    "other's, which is the budget-counter failure with the "
                    "grammar left untouched"
                )
    return out


def _binding_problems(entries, tags) -> list:
    """Both directions of the tag binding, over the real files."""
    out: list[str] = []
    corpus = _corpus()

    for entry in entries:
        name, value = entry.get("name") or "", entry.get("tag") or ""
        relative = (entry.get("emits") or {}).get("file") or ""
        if not value:
            continue
        if entry.get("adopted"):
            text = corpus.get(relative)
            if text is None or value not in text:
                out.append(
                    f"act {name!r} declares the ADOPTED tag {value!r}, which "
                    f"{relative or 'no file'} does not emit — an adopted tag is "
                    "an existing idempotency key read off the code, never a new "
                    "string this file invented"
                )
        else:
            emitted_in = sorted(p for p, text in corpus.items() if value in text)
            if emitted_in:
                out.append(
                    f"act {name!r} declares the tag {value!r} as not yet "
                    f"emitted, but {', '.join(emitted_in)} already carries it — "
                    "the registry may not claim an emission it does not have, "
                    "and it may not shadow one it does"
                )

    declared = set(tags)
    for relative, text in corpus.items():
        if not relative.endswith(".py"):
            continue
        if relative not in {(e.get("emits") or {}).get("file") for e in entries}:
            continue
        for constant, value in _TAG_CONSTANT.findall(text):
            if value not in declared:
                out.append(
                    f"{relative} emits {constant} = {value!r} and the registry "
                    "does not declare it — an act the pipeline can take with no "
                    "row is the scattered vocabulary this file replaces"
                )

    for entry in entries:
        body = trailer(entry["name"], {"acts": entries})
        for other in entries:
            if other["name"] == entry["name"]:
                continue
            if (other.get("tag") or "") and other["tag"] in body:
                out.append(
                    f"the trailer for {entry['name']!r} carries {other['name']!r}'s "
                    f"live key {other['tag']!r} — an idempotency check keyed on "
                    "that tag would read this receipt as the other act's"
                )
    return out


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check")
    sub.add_parser("list")
    compose = sub.add_parser("receipt")
    compose.add_argument("act")
    compose.add_argument("--body", required=True)
    compose.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    command = args.command or "check"

    if command == "receipt":
        # Composed BEFORE the file is touched. receipt() raises on an unknown
        # act and on an empty body, and a caller that posts --body-file must
        # never find a partial file where a composed one should be.
        try:
            composed = receipt(args.act, args.body)
        except (UnknownAct, ActError) as e:
            print(f"pipeline_act: {e}", file=sys.stderr)
            return 2
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(composed)
        return 0

    if command == "check":
        found = problems()
        for problem in found:
            print(f"  [FAIL] {problem}")
        print(
            f"{len(acts())} act(s) checked against the code that emits them, "
            f"{len(found)} problem(s)"
        )
        return 1 if found else 0

    if command == "list":
        print(json.dumps([
            {
                "act": name,
                "tag": tag(name),
                "kind": kind(name),
                "state": state(name),
                "next_actor": next_actor(name),
                "discharges": discharges(name),
                "subscriber": subscriber(name),
                "trailer": trailer(name),
            }
            for name in acts()
        ], indent=2, ensure_ascii=False))
        return 0

    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
