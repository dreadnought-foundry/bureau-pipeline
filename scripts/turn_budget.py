#!/usr/bin/env python3
"""How many turns a build run gets, and why a turn-cap park happened (DRE-3097).

THE INCIDENT. `agent-task.yml` ran every card on `--max-turns 150`, a literal.
DRE-3088 died at it three times in a row on 2026-09-04 — $19.12, $19.12,
$17.53 — and every one of those runs reached `⏳ 3/5 implementation green` and
died in the two steps after it: the commit ordering for the TDD check, then the
PR. The THIRD death was on the card *after* it had been split to XS (two edits
inside two existing steps of `plan.yml`, plus tests). Splitting did not help,
because the cost was never the size of the change — it was the size of the file
the agent must read and re-read (`plan.yml` is ~1,850 lines), and the fixed 150
turns were spent before the PR opened.

The park receipt already offered the other remedy — the card would sit there
"until a human splits it into smaller pieces (or raises the turn budget)" — and
the second option had no handle. This module is the handle, and the reader that
tells the two causes apart.

TWO QUESTIONS, ONE MODULE, because they are the same fact from both ends:

  1. **What budget does this card get?** `budget_for(labels)`. A `turns:<n>`
     label from the closed set in `config/turn-budgets.json` wins; absent that
     the `size:` label maps to a rung; absent both it is the unchanged 150.
     Called from the step that selects the model, so a run cannot read the
     card one way for its model and another for its budget.

  2. **Was the death a budget or a size problem?** `diagnose(comments)`, read
     off the card's own thread. Every dead run reached the same progress
     marker or a later one, and the last is `implementation green` or later ⇒
     the work finishes and the RUN does not: budget. Anything else ⇒ split, as
     the receipt has always said.

THE SET IS THE GUARD. A card picks a rung; it does not name a number. That is
the "cost cap stays" half of the card: the run's own guard (agent-task's
120-minute wall clock) is untouched, and the knob may only select from rungs a
human put in a reviewed config — the same rule `config/models.yaml` states for
membership of a model ladder. `turns:10000` is refused, out loud.

WHAT THE PROGRESS READER MATCHES, and what it deliberately does not. Phase
receipts are `⏳ <n>/5 <label>` and the three briefs spell the same phase
differently — "implementation green" (engineer), "code + synth green" (devops),
"green" (`standards/engineering.md`). The NUMBER is the contract; matching the
prose would make the diagnosis depend on which role happened to build the card.

CLI:
    turn_budget.py select <CARD> [--explain-file PATH]
    turn_budget.py select --labels turns:250,size:M [--explain-file PATH]
    turn_budget.py diagnose <CARD> | --comments-file PATH
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

#: The config the fleet reads. A FILE in this checkout, for the reason
#: config/README.md gives for every other file beside it.
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "turn-budgets.json"

#: The degrade path, used only when the file above is unreadable. It must stay
#: byte-equivalent to the file's own values for the default rung: a build that
#: cannot read its config gets today's behaviour, never a surprise budget.
_FALLBACK_CONFIG = {
    "default": 150,
    "allowed": [150, 250, 400],
    "label_prefix": "turns:",
    "size_label_prefix": "size:",
    "size": {"XS": 150, "S": 150, "M": 250, "L": 400, "XL": 400},
}

#: The literal that was in the YAML, kept as the module's own answer so the
#: workflow's inline fallback and the tests read one number.
DEFAULT_TURNS = _FALLBACK_CONFIG["default"]

#: `⏳ <n>/5 <label>` — the phase heartbeat every build brief tells its agent to
#: post. Anchored on the emoji so a comment merely quoting "3/5" is not a phase.
_PROGRESS_RE = re.compile(r"⏳\s*(\d+)\s*/\s*5\b")

#: The phase number that means "the implementation is green" in all three
#: briefs (1 plan · 2 RED · 3 green · 4 local checks · 5 PR). The milestone
#: DRE-3088 reached on every one of its three deaths.
IMPLEMENTATION_GREEN = 3

#: `🧠 model-attempt:` — agent-task.yml posts exactly one per run, so it is
#: where one run's phase receipts end and the next run's begin. The string is
#: dedupe_dispatch's, which reads the same heartbeat for the run id.
_RUN_MARKER = "🧠 model-attempt:"

#: The budget clause agent-task.yml writes into that heartbeat: `turns=250`.
#: This is the reader; the workflow is the writer, the same split
#: `dedupe_dispatch._RUN_ID` has for the run URL in the same string.
_RECEIPT_TURNS_RE = re.compile(r"\bturns=(\d+)\b")

_CONFIG_CACHE: dict | None = None


# --------------------------------------------------------------------------- #
# config                                                                       #
# --------------------------------------------------------------------------- #

def clear_config_cache() -> None:
    """Forget the parsed config (tests, and any long-lived process)."""
    global _CONFIG_CACHE
    _CONFIG_CACHE = None


def load_config(path: Path | str | None = None) -> dict:
    """The budget map, or the fallback when the file cannot be read.

    NEVER RAISES. A malformed config must cost a card its raise, never its
    run — the same rule `model_fallback._load_config` follows for the model
    ladder, and for the same reason: this is read on the hot path of every
    build in the fleet.
    """
    global _CONFIG_CACHE
    explicit = path is not None
    if not explicit and _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    target = Path(path) if explicit else CONFIG_PATH
    config = dict(_FALLBACK_CONFIG)
    try:
        raw = json.loads(Path(target).read_text())
        if isinstance(raw, dict):
            for key in _FALLBACK_CONFIG:
                if key in raw:
                    config[key] = raw[key]
    except Exception as exc:  # missing, unreadable, malformed — all the same
        print(f"turn_budget: {target} unreadable ({exc}) — using the built-in "
              f"default map", file=sys.stderr)
    if not explicit:
        _CONFIG_CACHE = config
    return config


def allowed_budgets(config: dict | None = None) -> tuple[int, ...]:
    """The closed, ordered set of rungs a card may select. The GUARD."""
    cfg = config or load_config()
    rungs = sorted({int(n) for n in cfg.get("allowed", []) if int(n) > 0})
    return tuple(rungs) or (DEFAULT_TURNS,)


def default_budget(config: dict | None = None) -> int:
    """What a card carrying neither label gets. Unchanged at 150."""
    cfg = config or load_config()
    try:
        return int(cfg.get("default", DEFAULT_TURNS))
    except (TypeError, ValueError):
        return DEFAULT_TURNS


def size_map(config: dict | None = None) -> dict[str, int]:
    """`size:` label value (upper-cased) → rung."""
    cfg = config or load_config()
    out: dict[str, int] = {}
    for size, turns in (cfg.get("size") or {}).items():
        try:
            out[str(size).strip().upper()] = int(turns)
        except (TypeError, ValueError):
            continue
    return out


def label_for(turns: int) -> str:
    """The label a human applies to ask for `turns`."""
    return f"{_FALLBACK_CONFIG['label_prefix']}{int(turns)}"


# --------------------------------------------------------------------------- #
# question 1 — what budget does this card get?                                 #
# --------------------------------------------------------------------------- #

def _label_value(labels, prefix: str) -> str | None:
    """The first label with `prefix`, case-insensitively, minus the prefix."""
    low = prefix.lower()
    for label in labels or []:
        text = str(label or "").strip()
        if text.lower().startswith(low):
            return text[len(prefix):].strip()
    return None


def budget_for(labels, config: dict | None = None) -> tuple[int, str]:
    """`(turns, why)` for a card carrying `labels`.

    Precedence, and the reason for it:

      1. `turns:<n>` — an explicit decision a human made about THIS card,
         usually after watching it die. It wins over an estimate.
      2. `size:<x>` — the effort estimate the card already carries. Free: no
         second label saying the same thing.
      3. the default, 150. Unchanged, and reached by every card that carries
         neither.

    A `turns:` value outside the allowed set does not silently become a
    budget and does not stop the run: it falls through to 2/3 and the returned
    note NAMES the refused value, so it shows up in the step summary and in the
    `🧠 model-attempt` receipt instead of being quietly ignored.
    """
    cfg = config or load_config()
    allowed = allowed_budgets(cfg)
    refused = ""

    raw = _label_value(labels, cfg.get("label_prefix", "turns:"))
    if raw is not None:
        try:
            asked = int(raw)
        except (TypeError, ValueError):
            refused = (f"the `turns:{raw}` label is not a number and was "
                       f"ignored. ")
        else:
            if asked in allowed:
                return asked, (f"turn budget {asked}: the card's "
                               f"`turns:{asked}` label.")
            refused = (f"the `turns:{asked}` label asks for a budget that is "
                       f"not one of {', '.join(str(n) for n in allowed)} and "
                       f"was refused — the rungs are a reviewed set, not a "
                       f"free number. ")

    size = _label_value(labels, cfg.get("size_label_prefix", "size:"))
    if size:
        mapped = size_map(cfg).get(size.upper())
        if mapped is not None:
            return mapped, (f"{refused}turn budget {mapped}: the card's "
                            f"`size:{size.upper()}` label.")

    fallback = default_budget(cfg)
    return fallback, (f"{refused}turn budget {fallback}: the default — this "
                      f"card carries no `turns:` or `size:` label.")


def next_rung(current: int, config: dict | None = None) -> int:
    """The rung above `current`, or `current` when it is already the top.

    The receipt tells a human what to label the card, so it must name a rung
    the selector will actually accept — inventing 500 would produce a label
    `budget_for` refuses and a card that changed nothing.
    """
    allowed = allowed_budgets(config)
    for rung in allowed:
        if rung > current:
            return rung
    return allowed[-1]


# --------------------------------------------------------------------------- #
# question 2 — was the death a budget problem or a size problem?               #
# --------------------------------------------------------------------------- #

def progress_of(body: str) -> int | None:
    """The phase number of a `⏳ n/5` receipt, or None if it is not one."""
    m = _PROGRESS_RE.search(body or "")
    return int(m.group(1)) if m else None


def runs_progress(comment_bodies) -> list[int]:
    """How far each RUN got, oldest→newest: the furthest `⏳ n/5` it posted.

    A run begins at its `🧠 model-attempt` heartbeat, which agent-task.yml
    posts exactly once per run before the agent starts. Comments before the
    first heartbeat belong to no run and are ignored — a stray `⏳ 4/5` in the
    card body or a human's comment is not a run that reached phase four.
    """
    runs: list[int] = []
    for body in comment_bodies or []:
        text = (body or "").lstrip()
        if text.startswith(_RUN_MARKER):
            runs.append(0)
            continue
        if not runs:
            continue
        phase = progress_of(text)
        if phase is not None:
            runs[-1] = max(runs[-1], phase)
    return runs


def current_budget(comment_bodies, config: dict | None = None) -> int:
    """The budget the card's most recent run actually had, off its own receipt.

    The record, not a re-derivation: a card whose label was already raised to
    250 must be told to raise to 400, and re-reading the labels here would
    answer 250 for a run that had already spent it.
    """
    for body in reversed(list(comment_bodies or [])):
        text = (body or "").lstrip()
        if not text.startswith(_RUN_MARKER):
            continue
        m = _RECEIPT_TURNS_RE.search(text)
        if m:
            return int(m.group(1))
    return default_budget(config)


def budget_not_size(runs) -> bool:
    """Do this card's dead runs say BUDGET rather than SIZE?

    True when **every dead run reached the same progress marker or a later
    one, and the last marker is `implementation green` or later**. That is the
    signature of a card whose work finishes and whose RUN does not: the agent
    gets to green and dies in the steps after it, run after run, and a smaller
    card does not fix a ceiling.

    The two halves each rule out a different wrong answer:

      * **non-decreasing** — a run that got LESS far than the one before it is
        variance, not a squeeze. Reading [3, 1] as a budget problem would tell
        a human to raise a ceiling the second run never came near.
      * **the last marker ≥ implementation green** — runs that all stall at
        2/5 are the DRE-2838 shape, and more turns buys more of the same.

    Fewer than two runs is never a diagnosis: the hold only fires on the
    SECOND turn-cap death, and one sample cannot support the word "every".
    """
    seq = [int(n) for n in (runs or [])]
    if len(seq) < 2:
        return False
    if any(b < a for a, b in zip(seq, seq[1:])):
        return False
    return seq[-1] >= IMPLEMENTATION_GREEN


def diagnose(comment_bodies, config: dict | None = None) -> dict:
    """The park receipt's evidence, read off the card's own thread.

    `{"budget_not_size": bool, "runs": [int], "current": int,
      "raise_to": int, "label": str}` — `label` is what a human applies to act
    on it, and it is empty when the card is already on the top rung (there is
    nothing to raise to, and naming a rung the selector refuses would be worse
    than saying nothing).
    """
    runs = runs_progress(comment_bodies)
    current = current_budget(comment_bodies, config)
    raise_to = next_rung(current, config)
    return {
        "budget_not_size": budget_not_size(runs),
        "runs": runs,
        "current": current,
        "raise_to": raise_to,
        "label": label_for(raise_to) if raise_to > current else "",
    }


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _labels_of(identifier: str) -> tuple[list[str], str]:
    """The card's labels from Linear, or `([], why)` when it cannot be read.

    FAILS OPEN. A Linear read that fails costs the card its raise, never its
    run: the caller falls back to the default and says so. Same direction the
    dedupe guard and the credential clock take on an unreadable answer.
    """
    try:
        import linear_ops

        return linear_ops._label_names(linear_ops.get_issue(identifier)), ""
    except Exception as exc:
        return [], (f"could not read {identifier}'s labels from Linear "
                    f"({type(exc).__name__}) — ")


def _split_flags(rest: list[str], flags: tuple[str, ...]) -> tuple[dict, list[str]]:
    """`(--flag → value, positionals)`. One parser, so a flag's VALUE is never
    mistaken for the card id — the bug that would make `select --labels ""`
    read `""` as a card and hit Linear for it."""
    values: dict[str, str] = {}
    positional: list[str] = []
    pending = list(rest)
    while pending:
        arg = pending.pop(0)
        if arg in flags:
            values[arg] = pending.pop(0) if pending else ""
        elif arg.startswith("--"):
            continue  # unknown flag: ignored, never treated as a card id
        else:
            positional.append(arg)
    return values, positional


def _cmd_select(rest: list[str]) -> int:
    values, positional = _split_flags(rest, ("--explain-file", "--labels"))
    explain = values.get("--explain-file")
    if "--labels" in values:
        labels = [l.strip() for l in values["--labels"].split(",") if l.strip()]
        prefix = ""
    elif positional:
        labels, prefix = _labels_of(positional[0])
    else:
        print("usage: turn_budget.py select <CARD> | --labels a,b "
              "[--explain-file PATH]", file=sys.stderr)
        return 2

    turns, why = budget_for(labels)
    note = f"{prefix}{why}"
    print(turns)
    print(note, file=sys.stderr)
    if explain:
        try:
            Path(explain).write_text(note + "\n")
        except OSError as exc:  # a note we cannot write must not kill a run
            print(f"turn_budget: could not write {explain} ({exc})",
                  file=sys.stderr)
    return 0


def _cmd_diagnose(rest: list[str]) -> int:
    values, positional = _split_flags(rest, ("--comments-file",))
    path = values.get("--comments-file")
    if path:
        try:
            bodies = json.loads(Path(path).read_text())
        except Exception as exc:
            print(f"turn_budget: {path} unreadable ({exc})", file=sys.stderr)
            bodies = []
    else:
        if not positional:
            print("usage: turn_budget.py diagnose <CARD> | --comments-file PATH",
                  file=sys.stderr)
            return 2
        try:
            import linear_ops

            bodies = linear_ops.comment_bodies(positional[0])
        except Exception as exc:
            print(f"turn_budget: could not read the thread ({exc})",
                  file=sys.stderr)
            bodies = []
    print(json.dumps(diagnose(bodies if isinstance(bodies, list) else [])))
    return 0


def main(argv: list[str]) -> int:
    """CLI for agent-task.yml.

      select <CARD> [--explain-file PATH]     the budget this card runs with
      select --labels a,b [--explain-file P]  the same, from a label list
      diagnose <CARD> | --comments-file PATH  budget-vs-size, as JSON

    STDOUT OF `select` IS ONLY THE NUMBER — the workflow does
    `TURNS=$(… select …)`. The note goes to stderr and to `--explain-file`, so
    adding to it can never corrupt the captured value. `select` NEVER exits
    non-zero for a card it could not read: it degrades to the default, which
    is exactly today's behaviour.
    """
    if not argv:
        print("usage: turn_budget.py select … | diagnose …", file=sys.stderr)
        return 2
    cmd, *rest = argv
    if cmd == "select":
        return _cmd_select(rest)
    if cmd == "diagnose":
        return _cmd_diagnose(rest)
    print(f"unknown command {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
