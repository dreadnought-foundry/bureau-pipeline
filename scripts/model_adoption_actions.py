#!/usr/bin/env python3
"""The adoption ACTIONS — what the workflow does with a Decision record (DRE-3903).

`scripts/model_adoption.py` (DRE-3895) decides: it sorts every catalog id the
pipeline has never configured into adopt / ignore / ask and prints one Decision
record per candidate —

    {"candidate", "display_name", "created_at", "family", "rule",
     "replaces": [{"ladder", "model", "created_at", "price"}, ...],
     "price", "reason"}

This module ACTS on that record, the four ways the adoption workflow (DRE-3898)
calls it, and no others:

    apply <candidate-id> [--snapshot <catalog.json>] [--config <models.yaml>]
          [--prices <model-prices.yaml>] [--check]
    render pr-title|pr-body|question-title|question-body|record-title|record-body
           --decision <decision.json> [--trial <trial.json>]
    open-record-card --decision <decision.json> --run-url <url>
    open-question-card --decision <decision.json>

It sits beside the rule the way `repair_card.py` sits beside
`red_main_repair.py`, and it never re-derives a classification: `apply` runs
the rule's own `classify_catalog` for the id it is given, and the two `open-*`
commands refuse (exit 2, Linear untouched) a record whose `rule` is not the one
they act on.

APPLY IS A TEXT EDIT, NEVER A RE-DUMP
-------------------------------------
`config/models.yaml` carries the record of every spend decision ever made in it
as comments and `reason:` blocks. A YAML round-trip drops every one of them, so
the edit is made on the text: each `- model: <old-id>` line on a ladder the
Decision names becomes `- model: <candidate-id>`, a `review_separation` rule
naming an id that has left every ladder moves with it (the DRE-3892
carry-forward the file itself describes), and each id that has left every
ladder is appended under `retired:` with a dated reason. Nothing else changes.
The result must then pass `model_fallback.policy_errors` or nothing is written —
an `effort:` level declared for the replaced id, for instance, is refused
rather than carried to a model nobody chose that level for.

THE LINEAR HALF COPIES `repair_card.py`
---------------------------------------
Linear is imported lazily behind an ops object a test replaces, a Linear
failure is never raised (the record card is owed and the workflow carries on
on a cardless branch), and the result is `$GITHUB_OUTPUT` lines. The record
card lands in `In Progress` with a `WORKBENCH` verdict, never `Todo`: a card
entering Todo is dispatched, and this work is already being done by the run
that files it.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dependabot_card  # noqa: E402
import github_output  # noqa: E402
import model_adoption as ma  # noqa: E402
import model_catalog  # noqa: E402
from model_adoption import classify_catalog, load_prices  # noqa: E402,F401
from model_fallback import policy_errors  # noqa: E402

#: The CLI, exactly as DRE-3898's workflow calls it.
COMMANDS = ("apply", "render", "open-record-card", "open-question-card")
RENDER_TARGETS = ("pr-title", "pr-body", "question-title", "question-body",
                  "record-title", "record-body")

#: Both cards are filed against this repo: the file an adoption edits lives here.
REPO_SLUG = "bureau-pipeline"

#: The record card: where the work already is, and a verdict that says the
#: fleet has nothing to dispatch for it — `repair_card.py`'s shape, for the
#: same reason, and the same `automation` mark (`dependabot_card.LABEL`):
#: automation filed it, never `hand-built`, which is the CEO's alone (DRE-6228).
RECORD_LANE = "In Progress"
RECORD_VERDICT = "WORKBENCH"
RECORD_LABELS = (f"repo:{REPO_SLUG}", "agent:devops", "initiative:bureau",
                 dependabot_card.LABEL)

#: The question card: the default create lane, where the planner reads it as a
#: decision rather than work and parks it in Green Light for the CEO.
QUESTION_LANE = "Planning"
QUESTION_LABELS = ("needs-human", "no-code", "agent:devops")

#: The ladder whose rung names the record card when the Decision replaces one.
TITLE_LADDER = "workhorse"

#: What each ladder is FOR, in the words a non-technical reader needs.
_LADDER_PURPOSE = {
    "workhorse": "the models our build agents run on",
    "advisory": "the models our reviewers run on",
    "judgement": "the model our planner runs on",
}

#: Verdict-shaped text is an approval credential (standards/untrusted-content.md)
#: and nothing this module renders may carry one, whatever a trial summary or a
#: display name says.
_VERDICT_MARKERS = (
    (re.compile(r"VERDICT\s*:", re.IGNORECASE), "verdict (quoted):"),
    (re.compile(r"QA\s+Critic", re.IGNORECASE), "critic (quoted)"),
    (re.compile(r"QA\s+Verifier", re.IGNORECASE), "verifier (quoted)"),
)


#: The first line of every pull request body this module renders. A model swap
#: changes nothing a person using a product sees (`standards/whats-new.md`,
#: DRE-5573) — and the `agent/` branches the workflow opens on owe the line.
WHATS_NEW_NONE = "What's new: none"


class Refused(ValueError):
    """A Decision (or a config edit) this module will not act on. Exit 2."""


# --------------------------------------------------------------------------- #
# Reading a Decision                                                           #
# --------------------------------------------------------------------------- #

def load_decision(path) -> dict:
    """One Decision record off disk, as DRE-3895 prints it. Raises `Refused`
    on anything that is not one — a list, a record with no candidate or rule."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Refused(f"cannot read the Decision record {path} ({exc})") from exc
    if not isinstance(data, Mapping) or not data.get("candidate") or not data.get("rule"):
        raise Refused(f"{path} is not a Decision record (no candidate or rule)")
    return dict(data)


def load_trial(path) -> dict | None:
    """The trial JSON DRE-3897's workflow produces, or None without one."""
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Refused(f"cannot read the trial result {path} ({exc})") from exc
    if not isinstance(data, Mapping):
        raise Refused(f"{path} is not a trial result")
    return dict(data)


def _require(decision, rule: str, what: str) -> None:
    if decision.get("rule") != rule:
        raise Refused(
            f"{what} acts on a `{rule}` Decision; {decision.get('candidate')} is "
            f"`{decision.get('rule')}` — {decision.get('reason') or 'no reason given'}"
        )


def _replaces(decision) -> list[dict]:
    return [r for r in decision.get("replaces") or [] if isinstance(r, Mapping)]


def _title_rung(decision) -> dict | None:
    """The `replaces` entry for the workhorse ladder when there is one, else the
    first — the one rung the titles name."""
    rungs = _replaces(decision)
    for rung in rungs:
        if rung.get("ladder") == TITLE_LADDER:
            return rung
    return rungs[0] if rungs else None


def old_id(decision) -> str:
    rung = _title_rung(decision)
    return str(rung.get("model") or "") if rung else ""


def ladders_touched(decision) -> list[str]:
    return list(dict.fromkeys(str(r.get("ladder")) for r in _replaces(decision)))


def _money(price) -> str:
    if not isinstance(price, Mapping):
        return "no declared price"
    return f"${float(price['input']):.2f} / ${float(price['output']):.2f}"


def _quiet(text: str) -> str:
    for pattern, replacement in _VERDICT_MARKERS:
        text = pattern.sub(replacement, text)
    return text


# --------------------------------------------------------------------------- #
# Titles — deterministic, so `find_open` dedupes them week to week             #
# --------------------------------------------------------------------------- #

def record_title(decision) -> str:
    old = old_id(decision)
    if not old:
        raise Refused(f"{decision.get('candidate')} replaces no rung, so there "
                      "is no adoption to record")
    return f"Model adoption: {old} → {decision['candidate']}"


def pr_title(decision) -> str:
    old = old_id(decision)
    if not old:
        raise Refused(f"{decision.get('candidate')} replaces no rung, so there "
                      "is no adoption to open a pull request for")
    return (f"Adopt {decision['candidate']} on the "
            f"{', '.join(ladders_touched(decision))} ladder (replaces {old})")


def _ask_class(decision) -> tuple[str, str | None]:
    """Which of DRE-3895's three `ask` phrases the reason opens with, and for
    `priced above` the rung it names."""
    reason = str(decision.get("reason") or "")
    if reason.startswith(ma.ASK_NEW_FAMILY):
        return ma.ASK_NEW_FAMILY, None
    if reason.startswith(ma.ASK_PRICED_ABOVE):
        rest = reason[len(ma.ASK_PRICED_ABOVE):].split()
        return ma.ASK_PRICED_ABOVE, rest[0] if rest else None
    if reason.startswith(ma.ASK_NO_PRICE):
        return ma.ASK_NO_PRICE, None
    raise Refused(f"the reason for {decision.get('candidate')} opens with none of "
                  f"{list(ma.ASK_PHRASES)}, so it is not a question this module "
                  "knows how to ask")


def question_title(decision) -> str:
    phrase, rung = _ask_class(decision)
    tail = f"{phrase} {rung}" if phrase == ma.ASK_PRICED_ABOVE and rung else phrase
    return f"Spending decision: {decision['candidate']} — {tail}"


# --------------------------------------------------------------------------- #
# The question — for a reader who does not read code                           #
# --------------------------------------------------------------------------- #

def _per_million(price) -> str:
    if not isinstance(price, Mapping):
        return "and we have no price on record for it"
    return (f"at ${float(price['input']):.2f} per million input tokens and "
            f"${float(price['output']):.2f} per million output tokens")


def _ladder_phrase(names: list[str]) -> str:
    parts = [
        f"our {name} ladder ({_LADDER_PURPOSE[name]})" if name in _LADDER_PURPOSE
        else f"our {name} ladder"
        for name in names
    ]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _new_family_rung(config, prices) -> tuple[str | None, str | None, dict | None]:
    """For a family no ladder runs: the ladder discovery lets a new model join
    (`discovery.on_new_model`) and its top rung, the nearest thing we run
    there — read from the config, never assumed."""
    config = config if config is not None else ma.load_config()
    prices = prices if prices is not None else load_prices()
    target = str(((config.get("discovery") or {}).get("on_new_model")) or "")
    rungs = model_catalog._ladder_ids((config.get("ladders") or {}).get(target))
    if not rungs:
        return None, None, None
    top = rungs[0]
    entry = prices.get(top)
    return target, top, ({"input": entry["input"], "output": entry["output"]}
                         if isinstance(entry, Mapping) else None)


def question_body(decision, config=None, prices=None) -> str:
    """The CEO's spending question. The first paragraph is the whole question:
    what Anthropic now offers and at what price, the nearest thing we run and
    its price, the ladder it would join, and that nothing is adopted until he
    answers. No file path, no command, no verdict marker anywhere."""
    phrase, named_rung = _ask_class(decision)
    candidate = decision["candidate"]
    name = decision.get("display_name") or candidate
    released = str(decision.get("created_at") or "")[:10]
    offer = f"Anthropic now offers {name} ({candidate})"
    if released:
        offer += f", released {released},"
    offer += f" {_per_million(decision.get('price'))}."

    if phrase == ma.ASK_NEW_FAMILY:
        ladder, nearest, nearest_price = _new_family_rung(config, prices)
        ladders = [ladder] if ladder else []
        ask = ("It is a new line of models, not a newer version of one we run. "
               "Should we add it?")
        why = ("Our standing rule adopts newer versions of models we already run. "
               "A new line of models is a different product, so choosing it is a "
               "decision rather than an upgrade.")
    else:
        rungs = _replaces(decision)
        chosen = next((r for r in rungs if r.get("model") == named_rung), None)
        chosen = chosen or _title_rung(decision) or {}
        nearest, nearest_price = chosen.get("model"), chosen.get("price")
        ladders = ladders_touched(decision)
        if phrase == ma.ASK_PRICED_ABOVE:
            ask = ("It costs more than the model it would replace. Should we move "
                   "to it at the higher price?")
            why = ("Our standing rule adopts a newer version on its own only when "
                   "it costs the same or less on both input and output. This one "
                   "costs more, so it is a spending decision.")
        else:
            ask = "We cannot compare what it costs. Should we adopt it anyway?"
            why = ("Our standing rule adopts a newer version on its own only when a "
                   "price on record shows it costs the same or less. We are missing "
                   "a price we need for that comparison, and we never guess one.")

    if nearest:
        near = f"The nearest model we run is {nearest}, {_per_million(nearest_price)}."
    else:
        near = "We run nothing close to it today."
    if ladders:
        joins = f"It would join {_ladder_phrase(ladders)}."
    else:
        joins = "It would not join any of our ladders unless you decide it should."

    first = " ".join([offer, near, joins, ask, "Nothing is adopted until you answer."])
    body = "\n\n".join([
        first,
        "What each answer does:\n\n"
        "- Yes: the model is added through an ordinary reviewed change, and our "
        "agents start using it once that change is merged.\n"
        "- No: nothing changes, and we keep running what we run today.",
        f"Why this came to you: {why}",
    ])
    return _quiet(body) + "\n"


# --------------------------------------------------------------------------- #
# The pull request and the record card                                         #
# --------------------------------------------------------------------------- #

def _evidence(decision) -> str:
    candidate = decision["candidate"]
    rows = [
        "| | Model | Released (`created_at`) | Declared price, input / output per MTok |",
        "|---|---|---|---|",
        f"| Candidate | `{candidate}` | {decision.get('created_at') or 'unknown'} "
        f"| {_money(decision.get('price'))} |",
    ]
    for rung in _replaces(decision):
        rows.append(
            f"| Replaces on `{rung.get('ladder')}` | `{rung.get('model')}` "
            f"| {rung.get('created_at') or 'unknown'} | {_money(rung.get('price'))} |"
        )
    return "\n".join([
        "## Classification evidence",
        "",
        *rows,
        "",
        f"- Ladders touched: {', '.join(ladders_touched(decision))}",
        f"- Rule: `{decision.get('rule')}` — {decision.get('reason') or ''}",
    ])


def _trial(trial) -> str:
    if not trial:
        return "## Trial result\n\nNo trial result was supplied to this run."
    return "\n".join([
        "## Trial result",
        "",
        f"- Model: `{trial.get('model') or ''}`",
        f"- Outcome: `{trial.get('outcome') or ''}`",
        f"- Run: {trial.get('run_url') or ''}",
        "",
        "Summary:",
        "",
        str(trial.get("summary") or ""),
    ])


_RULE_ONE = (
    "This pull request was opened by the model adoption workflow under DRE-3892 "
    "rule 1 — a newer version of a model family we already run, at the same or "
    "a lower declared price — and the critic and the merge gate are its review."
)


def _summary(decision) -> str:
    moves = ", ".join(f"`{r.get('model')}` on the {r.get('ladder')} ladder"
                      for r in _replaces(decision))
    name = decision.get("display_name") or decision["candidate"]
    return f"{name} (`{decision['candidate']}`) replaces {moves}."


def pr_body(decision, trial=None) -> str:
    body = "\n\n".join([
        WHATS_NEW_NONE,
        _summary(decision),
        _evidence(decision),
        _trial(trial),
        _RULE_ONE,
    ])
    return _quiet(body) + "\n"


def record_branch(card: str, candidate: str) -> str:
    return f"agent/{card}-adopt-{candidate}"


def fallback_branch(candidate: str) -> str:
    return f"agent/model-adoption-{candidate}"


def record_body(decision, *, run_url: str = "", branch: str = "", trial=None) -> str:
    """The board's record of the adoption: the PR body's evidence plus the
    branch and the run. `linear-sync` closes the card when the PR merges,
    through the card id in the branch name."""
    branch = branch or record_branch("<this card>", decision["candidate"])
    body = "\n\n".join([
        "## What happened",
        _summary(decision) + " The model adoption workflow is making the change "
        "under DRE-3892 rule 1, through an ordinary pull request the critic "
        "reviews and the merge gate merges. This card is the board's record of "
        "that adoption: it carries the pull request and closes when it merges.",
        f"- Workflow run: {run_url or 'not recorded'}\n- Branch: `{branch}`",
        _evidence(decision),
        _trial(trial),
        "## Acceptance criteria",
        f"- [ ] The adoption pull request on `{branch}` merges through the critic "
        "and the merge gate.\n"
        "- [ ] Closed by the merge, through the card id in the branch name.",
    ])
    return _quiet(body) + "\n"


def render(target: str, decision, trial=None, *, run_url: str = "",
           branch: str = "") -> str:
    if target == "pr-title":
        return pr_title(decision)
    if target == "pr-body":
        return pr_body(decision, trial)
    if target == "question-title":
        return question_title(decision)
    if target == "question-body":
        return question_body(decision)
    if target == "record-title":
        return record_title(decision)
    if target == "record-body":
        return record_body(decision, run_url=run_url, branch=branch, trial=trial)
    raise Refused(f"unknown render target {target!r} — one of {list(RENDER_TARGETS)}")


# --------------------------------------------------------------------------- #
# apply — config/models.yaml, edited as text                                   #
# --------------------------------------------------------------------------- #

_TOP_KEY = re.compile(r"^([A-Za-z_][\w-]*):(.*)$")
_CHILD_KEY = re.compile(r"^(\s+)([A-Za-z_][\w-]*):\s*(#.*)?$")
_RUNG = re.compile(r"^(\s*-\s+model:\s*)(['\"]?)([^'\"\s#]+)\2(\s*(?:#.*)?)$")
_SEPARATION = re.compile(
    r"^(\s*(?:-\s+)?(?:built_on|reviewers_use):\s*)(['\"]?)([^'\"\s#]+)\2(\s*(?:#.*)?)$"
)


def _split(line: str) -> tuple[str, str]:
    """A line and its ending, so every untouched line is written back whole."""
    body = line.rstrip("\r\n")
    return body, line[len(body):]


def _ladder_ids(config) -> dict[str, list[str]]:
    return {str(name): model_catalog._ladder_ids(rungs)
            for name, rungs in ((config or {}).get("ladders") or {}).items()}


def apply_text(text: str, decision, today: str) -> str:
    """`config/models.yaml` with the Decision applied, as a text edit.

    Raises `Refused` when the Decision is not `adopt`, when a rung it names is
    not where it says, or when the edit did not produce exactly the ladders it
    meant to — the parsed result is compared against the intended one, so a
    line matched by mistake is caught here rather than in production.
    """
    import yaml

    _require(decision, ma.RULE_ADOPT, "apply")
    candidate = decision["candidate"]
    wanted = {(str(r.get("ladder")), str(r.get("model"))) for r in _replaces(decision)}
    if not wanted:
        raise Refused(f"{candidate} replaces no rung, so there is nothing to apply")

    lines = text.splitlines(keepends=True)
    top = None
    ladder = None
    child_indent = None
    hit: set[tuple[str, str]] = set()
    for i, line in enumerate(lines):
        body, end = _split(line)
        if not body.strip() or body.lstrip().startswith("#"):
            continue
        found = _TOP_KEY.match(body)
        if found:
            top, ladder, child_indent = found.group(1), None, None
            continue
        if top != "ladders":
            continue
        child = _CHILD_KEY.match(body)
        indent = len(body) - len(body.lstrip())
        if child and (child_indent is None or indent == child_indent):
            child_indent, ladder = indent, child.group(2)
            continue
        rung = _RUNG.match(body)
        if rung and (ladder, rung.group(3)) in wanted:
            hit.add((ladder, rung.group(3)))
            lines[i] = (f"{rung.group(1)}{rung.group(2)}{candidate}"
                        f"{rung.group(2)}{rung.group(4)}{end}")

    missed = sorted(wanted - hit)
    if missed:
        raise Refused("the Decision names rung(s) the config does not hold: "
                      + ", ".join(f"{m} on the {l} ladder" for l, m in missed))

    before = yaml.safe_load(text) or {}
    old_ladders = _ladder_ids(before)
    staying = {m for ids in _ladder_ids(
        yaml.safe_load("".join(lines)) or {}).values() for m in ids}
    gone = [m for m in dict.fromkeys(str(r.get("model")) for r in _replaces(decision))
            if m not in staying]

    lines = _move_separation(lines, gone, candidate)
    retired = set(model_catalog._ladder_ids(before.get("retired")))
    lines = _retire(lines, [m for m in gone if m not in retired], candidate, today)
    result = "".join(lines)

    expected = {
        name: [candidate if (name, m) in wanted else m for m in ids]
        for name, ids in old_ladders.items()
    }
    after = yaml.safe_load(result) or {}
    if _ladder_ids(after) != expected:
        raise Refused("the text edit did not produce the intended ladders — "
                      "nothing written")
    return result


def _move_separation(lines: list[str], gone: list[str], candidate: str) -> list[str]:
    """A `review_separation` rule naming a rung that has left every ladder
    moves with it — the overlap it declares moved on both ladders at once."""
    top = None
    out = []
    for line in lines:
        body, end = _split(line)
        found = _TOP_KEY.match(body)
        if found:
            top = found.group(1)
        field = _SEPARATION.match(body) if top == "review_separation" else None
        if field and field.group(3) in gone:
            line = (f"{field.group(1)}{field.group(2)}{candidate}"
                    f"{field.group(2)}{field.group(4)}{end}")
        out.append(line)
    return out


def _retire(lines: list[str], ids: list[str], candidate: str, today: str) -> list[str]:
    """Append each id under `retired:`, after the block's last entry."""
    if not ids:
        return lines
    start = next((i for i, line in enumerate(lines)
                  if _split(line)[0].startswith("retired:")), None)
    if start is None:
        raise Refused("the config has no `retired:` block to record the old id in")
    if _split(lines[start])[0][len("retired:"):].split("#")[0].strip():
        raise Refused("`retired:` is written inline; this edit appends to a block")
    last = start
    item_indent = None
    for i in range(start + 1, len(lines)):
        body = _split(lines[i])[0]
        if not body.strip():
            continue
        if not body[0].isspace() and not body.startswith("-"):
            break
        last = i
        if item_indent is None and body.lstrip().startswith("- "):
            item_indent = body[: len(body) - len(body.lstrip())]
    item_indent = "  " if item_indent is None else item_indent
    ending = _split(lines[last])[1] or "\n"
    if not _split(lines[last])[1]:
        lines[last] = lines[last] + ending
    added = []
    for old in ids:
        added += [
            f"{item_indent}- model: {old}{ending}",
            f'{item_indent}  reason: "replaced by {candidate} on {today} — '
            f'automated same-family adoption (DRE-3892)"{ending}',
        ]
    return lines[: last + 1] + added + lines[last + 1:]


def _parse_apply_args(argv):
    candidate = None
    snapshot = None
    config_path = ma.CONFIG_PATH
    prices_path = ma.PRICES_PATH
    check = False
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg == "--snapshot" and rest:
            snapshot = rest.pop(0)
        elif arg == "--config" and rest:
            config_path = rest.pop(0)
        elif arg == "--prices" and rest:
            prices_path = rest.pop(0)
        elif arg == "--check":
            check = True
        elif not arg.startswith("--") and candidate is None:
            candidate = arg
        else:
            raise Refused(f"unknown or incomplete option {arg!r}")
    if not candidate:
        raise Refused("apply needs a <candidate-id>")
    return candidate, snapshot, Path(config_path), Path(prices_path), check


def _cmd_apply(argv) -> int:
    """Classify, refuse anything but `adopt`, edit, validate, then write —
    or, with `--check`, print the diff and write nothing."""
    try:
        candidate, snapshot, config_path, prices_path, check = _parse_apply_args(argv)
        if snapshot:
            catalog = ma.load_catalog_file(snapshot)
        else:
            model_catalog.clear_catalog_cache()
            catalog = model_catalog.fetch_catalog(fetch=ma._fake_catalog_from_env())
        text = config_path.read_text()
        prices = load_prices(prices_path)
        config = ma.load_config(config_path)
        decision = next((d for d in classify_catalog(catalog, config, prices)
                         if d["candidate"] == candidate), None)
        if decision is None:
            raise Refused(f"{candidate} is not a candidate — the catalog does not "
                          "list it, or it is already configured")
        _require(decision, ma.RULE_ADOPT, "apply")
        today = datetime.now(timezone.utc).date().isoformat()
        result = apply_text(text, decision, today)
    except (OSError, ValueError) as exc:
        print(f"::error::apply refused: {exc}")
        return 2

    import yaml

    violations = policy_errors(yaml.safe_load(result), prices)
    if violations:
        print(f"::error::apply refused: adopting {candidate} would leave "
              f"{config_path} in violation of policy — nothing written")
        for violation in violations:
            print(f"  - {violation}")
        return 2

    diff = "".join(difflib.unified_diff(
        text.splitlines(keepends=True), result.splitlines(keepends=True),
        fromfile=str(config_path), tofile=str(config_path)))
    if check:
        print(diff, end="")
        return 0
    config_path.write_text(result)
    print(f"applied: {candidate} replaces "
          + ", ".join(f"{r['model']} on {r['ladder']}" for r in _replaces(decision))
          + f" in {config_path}")
    return 0


# --------------------------------------------------------------------------- #
# The two cards — repair_card.py's shape                                       #
# --------------------------------------------------------------------------- #

class _Ops:
    """The Linear seam, in one object so the caller can hand a fake in.
    Imported lazily: rendering and applying need no Linear key."""

    def find_open(self, title):
        import linear_ops

        return linear_ops.find_open(title)

    def create_card(self, title, description, *, repo_slug, labels=(),
                    lane=QUESTION_LANE):
        import linear_ops

        return linear_ops.create_card(title, description, repo_slug=repo_slug,
                                      labels=labels, lane=lane)

    def stamp_card(self, identifier, name, why):
        import routing_verdict

        return routing_verdict.stamp_card(identifier, name, why)

    def set_description(self, identifier, body):
        import linear_ops

        return linear_ops.set_description(identifier, body)


def _find(ops, title, what):
    try:
        return ops.find_open(title)
    except Exception as exc:  # noqa: BLE001 — a failed search is not a failed run
        print(f"{what}: could not search for an existing card ({exc})",
              file=sys.stderr)
        return None


def record_verdict_why(decision, run_url: str) -> str:
    return (
        f"Filed by the model adoption workflow (run {run_url}) for the adoption "
        f"of {decision['candidate']}. The workflow opens the pull request itself, "
        "so there is nothing here to dispatch — the card is the board's record "
        "of an adoption in flight."
    )


def open_record_card(decision, *, run_url: str, ops=None) -> dict:
    """File (or find) the adoption's record card and name its branch.

    Raises `Refused` — before any Linear call — on a Decision that is not
    `adopt`. Otherwise NEVER raises: a Linear failure returns the fallback
    branch with `card_owed` true, and the adoption proceeds on it.
    """
    _require(decision, ma.RULE_ADOPT, "open-record-card")
    title = record_title(decision)
    candidate = decision["candidate"]
    ops = ops or _Ops()

    existing = _find(ops, title, "record card")
    if existing:
        return {"card": existing, "card_url": "",
                "branch": record_branch(existing, candidate),
                "card_owed": False, "note": ""}

    try:
        issue = ops.create_card(title, record_body(decision, run_url=run_url),
                                repo_slug=REPO_SLUG, labels=list(RECORD_LABELS),
                                lane=RECORD_LANE)
    except Exception as exc:  # noqa: BLE001 — the ONE thing that must not fail
        print(f"record card: NOT filed ({exc}) — the adoption proceeds on "
              f"{fallback_branch(candidate)} and the pull request says the card "
              "is owed", file=sys.stderr)
        return {"card": "", "card_url": "", "branch": fallback_branch(candidate),
                "card_owed": True, "note": str(exc).replace("\n", " ")[:300]}

    card = issue["identifier"]
    branch = record_branch(card, candidate)
    try:
        ops.stamp_card(card, RECORD_VERDICT, record_verdict_why(decision, run_url))
    except Exception as exc:  # noqa: BLE001
        print(f"record card: {card} filed but its routing verdict did not stamp "
              f"({exc})", file=sys.stderr)
    try:
        # The branch is named after the card, so it is written in once the
        # card has a number.
        ops.set_description(card, record_body(decision, run_url=run_url,
                                              branch=branch))
    except Exception as exc:  # noqa: BLE001
        print(f"record card: {card} filed but its branch was not written in "
              f"({exc})", file=sys.stderr)
    return {"card": card, "card_url": issue.get("url") or "", "branch": branch,
            "card_owed": False, "note": ""}


def open_question_card(decision, *, ops=None) -> dict:
    """File (or find) the CEO's spending question. Raises `Refused` — before
    any Linear call — on a Decision that is not `ask`; otherwise never raises."""
    _require(decision, ma.RULE_ASK, "open-question-card")
    title = question_title(decision)
    body = question_body(decision)
    ops = ops or _Ops()

    existing = _find(ops, title, "question card")
    if existing:
        return {"card": existing, "card_url": "", "card_owed": False, "note": ""}
    try:
        issue = ops.create_card(title, body, repo_slug=REPO_SLUG,
                                labels=list(QUESTION_LABELS), lane=QUESTION_LANE)
    except Exception as exc:  # noqa: BLE001
        print(f"question card: NOT filed ({exc}) — the card is owed",
              file=sys.stderr)
        return {"card": "", "card_url": "", "card_owed": True,
                "note": str(exc).replace("\n", " ")[:300]}
    return {"card": issue["identifier"], "card_url": issue.get("url") or "",
            "card_owed": False, "note": ""}


def outputs(result: dict) -> str:
    """`$GITHUB_OUTPUT` lines, through the writer that survives a newline."""
    pairs = [("card", result["card"]), ("card_url", result["card_url"])]
    if "branch" in result:
        pairs.append(("branch", result["branch"]))
    pairs += [("card_owed", "true" if result["card_owed"] else "false"),
              ("card_note", result.get("note"))]
    return github_output.render(pairs)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _options(argv, allowed) -> tuple[list[str], dict]:
    positional, opts = [], {}
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg in allowed:
            if not rest:
                raise Refused(f"{arg} needs a value")
            opts[arg] = rest.pop(0)
        elif arg.startswith("--"):
            raise Refused(f"unknown option {arg!r}")
        else:
            positional.append(arg)
    return positional, opts


def _cmd_render(argv) -> int:
    try:
        positional, opts = _options(
            argv, ("--decision", "--trial", "--run-url", "--branch"))
        if len(positional) != 1 or positional[0] not in RENDER_TARGETS:
            raise Refused(f"render needs one target of {list(RENDER_TARGETS)}")
        if "--decision" not in opts:
            raise Refused("render needs --decision <decision.json>")
        text = render(positional[0], load_decision(opts["--decision"]),
                      load_trial(opts.get("--trial")),
                      run_url=opts.get("--run-url", ""),
                      branch=opts.get("--branch", ""))
    except (OSError, ValueError) as exc:
        print(f"::error::render refused: {exc}", file=sys.stderr)
        return 2
    print(text.rstrip("\n"))
    return 0


def _cmd_open(cmd, argv, ops) -> int:
    try:
        allowed = ("--decision", "--run-url") if cmd == "open-record-card" else (
            "--decision",)
        positional, opts = _options(argv, allowed)
        if positional or "--decision" not in opts:
            raise Refused(f"{cmd} takes --decision <decision.json> only")
        if cmd == "open-record-card" and "--run-url" not in opts:
            raise Refused("open-record-card needs --run-url <url>")
        decision = load_decision(opts["--decision"])
        # Refusals come before the output channel is shut, and before Linear.
        if cmd == "open-record-card":
            _require(decision, ma.RULE_ADOPT, cmd)
            record_title(decision)
        else:
            _require(decision, ma.RULE_ASK, cmd)
            question_title(decision)
    except (OSError, ValueError) as exc:
        print(f"::error::{cmd} refused: {exc}", file=sys.stderr)
        return 2

    # Stdout is the output FILE for this step, and the Linear seam talks back
    # on stdout — human text belongs on stderr whoever wrote it (DRE-4202).
    with github_output.only_outputs():
        if cmd == "open-record-card":
            result = open_record_card(decision, run_url=opts["--run-url"], ops=ops)
        else:
            result = open_question_card(decision, ops=ops)
    sys.stdout.write(outputs(result))
    print(f"{cmd}: {result['card'] or 'NOT FILED'}", file=sys.stderr)
    return 0


def main(argv: list[str], ops=None) -> int:
    """CLI for the adoption actions — see the module docstring for the four
    commands. Exit 0 on success (and on a Linear failure, which owes the card
    instead), exit 2 on a refusal or a malformed call."""
    if not argv or argv[0] not in COMMANDS:
        print(f"usage: model_adoption_actions.py {{{'|'.join(COMMANDS)}}} …",
              file=sys.stderr)
        return 2
    cmd, *rest = argv
    if cmd == "apply":
        return _cmd_apply(rest)
    if cmd == "render":
        return _cmd_render(rest)
    return _cmd_open(cmd, rest, ops)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
