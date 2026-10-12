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

    apply <candidate-id> --expect-written-at <written_at>
          [--snapshot <catalog.json>] [--prices <model-prices.yaml>]
          [--result <file>] [--check]
    render pr-title|question-title|question-body|record-title|record-body
           --decision <decision.json> [--trial <trial.json>] [--applied <file>]
    open-record-card --decision <decision.json> --run-url <url>
                     [--trial <trial.json>] [--applied <file>]
    open-question-card --decision <decision.json>

It sits beside the rule the way `repair_card.py` sits beside
`red_main_repair.py`, and it never re-derives a classification: `apply` runs
the rule's own `classify_catalog` for the id it is given, and the two `open-*`
commands refuse (exit 2, Linear untouched) a record whose `rule` is not the one
they act on.

APPLY WRITES THE HOME, AND NO FILE (DRE-6943)
---------------------------------------------
The choices live in the model home — the repository variable
`BUREAU_MODEL_CHOICES`, read through `model_home` — and the daily adoption is
the one writer of it besides the console. `apply` reads the home AGAIN right
before it writes. When that read's `written_at` differs from the one `classify`
recorded (`--expect-written-at`), the home changed while the trial ran — a
console save, most likely, which this writer's lock does not cover — and
nothing is written: the run ends green and says so in one sentence, and
tomorrow's run sorts again against the new document. Otherwise the new document
is built from the fresh read — the candidate where each replaced rung stood in
its group, and in every project choice that names a replaced id;
`written_by: daily-adoption`; `written_at` now — judged by
`model_home.home_errors`, and written with ONE `PATCH` using the token in
`BUREAU_MODEL_HOME_WRITE_TOKEN`. `--check` judges the document it would write
and writes nothing.

It edits no file. `config/models.yaml` still carries today's route's ladders
until part 3 takes the choices out of it (DRE-6983), and
`model_fallback.policy_errors` refuses a `retired:` id one of them still names
— so the old id stays priced and unretired, and when it has left every group
and project of the home the record card says so in one sentence. Retiring it
is a person's code change, never this workflow's edit.

THE LINEAR HALF COPIES `repair_card.py`
---------------------------------------
Linear is imported lazily behind an ops object a test replaces, a Linear
failure is never raised (the record card is owed and the run carries on), and
the result is `$GITHUB_OUTPUT` lines. The record card lands in `Done` with a
`WORKBENCH` verdict, never `Todo`: a card entering Todo is dispatched, and the
write it records is already made by the run that files it. A run that wrote
nothing files its record in `Canceled` instead: Done would claim a change
nobody made, and the next daily run sorts the candidate again.
"""

from __future__ import annotations

import copy
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
import model_fallback as mf  # noqa: E402
import model_home as mh  # noqa: E402
from model_adoption import classify_catalog, load_prices  # noqa: E402,F401

#: Every command of this CLI reads the home (DRE-6939's contract, checked by
#: DRE-6940's `check_model_home_scope.py`): `apply` and the question body read
#: it, and the workflow's rule is one token for every step that runs this file.
READS_MODEL_HOME = ("*",)

#: The CLI, exactly as DRE-3898's workflow calls it.
COMMANDS = ("apply", "render", "open-record-card", "open-question-card")
#: `pr-title` stays because the in-flight check matches an adoption pull
#: request opened before DRE-6943 on it; an adoption opens none now.
RENDER_TARGETS = ("pr-title", "question-title", "question-body",
                  "record-title", "record-body")

#: The name the adoption writes the home under — a role word, never a person.
WRITER = "daily-adoption"
#: The token `apply` writes the home with: the bureau App's installation on
#: dreadnought-foundry, `variables: write`, minted in the same job.
WRITE_TOKEN_ENV = "BUREAU_MODEL_HOME_WRITE_TOKEN"

#: Both cards are filed against this repo: the pipeline whose choices move.
REPO_SLUG = "bureau-pipeline"

#: The record card: Done, because the write it records is made — no pull
#: request will ever close it — and a verdict that says the fleet has nothing
#: to dispatch for it, `repair_card.py`'s, with the same `automation` mark
#: (`dependabot_card.LABEL`): automation filed it, never `hand-built`, which is
#: the CEO's alone (DRE-6228).
RECORD_LANE = "Done"
#: A run that wrote nothing made no change for Done to claim, and there is no
#: work left on it — the next daily run sorts the candidate again — so its
#: record is filed Canceled. `config/lane-contract.json` admits this workflow
#: to both lanes, for the record card alone.
UNWRITTEN_LANE = "Canceled"
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


#: The first line of every pull request body the adoption workflow renders —
#: since DRE-6943 only the pin raise's, which `claude_code_pin.py` imports from
#: here. A Claude Code pin changes nothing a person using a product sees
#: (`standards/whats-new.md`, DRE-5573), and its `agent/` branch owes the line.
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


def _home_config() -> dict | None:
    """The home's ladders in `classify_catalog`'s shape, or None when the home
    is not read — the read prints the home's tagged line to stderr."""
    outcome = mh.read()
    if outcome.get("state") != mh.READ:
        return None
    return ma.home_config(outcome["document"])


def _new_family_rung(config, prices) -> tuple[str | None, str | None, dict | None]:
    """For a family no ladder runs: the ladder discovery lets a new model join
    (`discovery.on_new_model`, a code-side fact) and its top rung, the nearest
    thing we run there — read from the HOME (DRE-6943), never assumed. With no
    home read the ladder is named and its top is None."""
    if config is None:
        config = _home_config()
        if config is None:
            target = str((mf.CONFIG.get("discovery") or {}).get("on_new_model") or "")
            return (target or None), None, None
    prices = prices if prices is not None else load_prices()
    target = str(((config.get("discovery") or {}).get("on_new_model")) or "")
    rungs = model_catalog._ladder_ids((config.get("ladders") or {}).get(target))
    if not rungs:
        return (target or None), "", None
    top = rungs[0]
    entry = prices.get(top)
    return target, top, ({"input": entry["input"], "output": entry["output"]}
                         if isinstance(entry, Mapping) else None)


#: Where the CEO answers an `ask` himself: the console page that edits the
#: home, which offers every priced model (CEO decision, 2026-10-10).
SET_IT_YOURSELF = ("You can set it yourself on the Settings › Agents & models page, "
                   "where it is already offered the day it is priced.")


def question_body(decision, config=None, prices=None) -> str:
    """The CEO's spending question. The first paragraph is the whole question:
    what Anthropic now offers and at what price, the nearest thing we run and
    its price, the ladder it would join — read from the home — and that
    nothing is adopted until he answers; then where he can set it himself. No
    file path, no command, no verdict marker anywhere."""
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
    elif nearest is None and phrase == ma.ASK_NEW_FAMILY:
        near = ("We could not read today which model leads the ladder it would "
                "join.")
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
        f"- Yes: {SET_IT_YOURSELF} Our agents use it from their next run.\n"
        "- No: nothing changes, and we keep running what we run today.",
        f"Why this came to you: {why}",
    ])
    return _quiet(body) + "\n"


# --------------------------------------------------------------------------- #
# The record card                                                              #
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


def _summary(decision) -> str:
    moves = ", ".join(f"`{r.get('model')}` on the {r.get('ladder')} ladder"
                      for r in _replaces(decision))
    name = decision.get("display_name") or decision["candidate"]
    return f"{name} (`{decision['candidate']}`) replaces {moves}."


#: Where the change is made, in the record's words.
_THE_HOME = ("the home — the one place the model choices live, the ones the "
             "Settings › Agents & models page edits")


def record_branch(card: str, candidate: str) -> str:
    """The head an adoption pull request opened before DRE-6943 sat on — the
    in-flight check still matches it, and an adoption opens none now."""
    return f"agent/{card}-adopt-{candidate}"


def fallback_branch(candidate: str) -> str:
    """The card-owed head of such a pull request, matched the same way."""
    return f"agent/model-adoption-{candidate}"


def _what_happened(decision, applied) -> str:
    outcome = (applied or {}).get("outcome")
    if outcome == "written":
        return (f"{_summary(decision)} The daily model adoption made that change "
                f"in {_THE_HOME}. It wrote the home under its own name, "
                f"`{WRITER}`, at {(applied or {}).get('written_at') or 'an unrecorded time'}, "
                "after the trial below passed, under DRE-3892 rule 1, and the "
                "console's history shows the write. This card is the board's "
                "record of that adoption.")
    if outcome:
        return (f"{_summary(decision)} That is the change the daily model "
                f"adoption would make in {_THE_HOME}, and this run made none of "
                "it. This card is the board's record of why.")
    return (f"{_summary(decision)} The daily model adoption makes that change in "
            f"{_THE_HOME}, once the trial below passes, under DRE-3892 rule 1.")


def record_body(decision, *, run_url: str = "", trial=None, applied=None) -> str:
    """The board's record of the adoption: what moved where, the home as the
    place the ladder changed, every sentence `apply` said (`applied`, the file
    its `--result` wrote), the run, the evidence and the trial."""
    said = [str(line) for line in (applied or {}).get("sentences") or []]
    written = (applied or {}).get("outcome") == "written"
    candidate = decision["candidate"]
    olds = ", ".join(f"`{m}`" for m in dict.fromkeys(
        str(r.get("model")) for r in _replaces(decision)))
    body = "\n\n".join(part for part in [
        "## What happened",
        _what_happened(decision, applied),
        "\n".join(f"- {line}" for line in said),
        f"- Workflow run: {run_url or 'not recorded'}",
        _evidence(decision),
        _trial(trial),
        "## Acceptance criteria",
        (f"- [x] The home names `{candidate}` where {olds} stood, written by "
         f"`{WRITER}`." if written else
         f"- [ ] The next daily run sorts `{candidate}` again against the home "
         "as it stands then."),
    ] if part)
    return _quiet(body) + "\n"


def render(target: str, decision, trial=None, *, run_url: str = "",
           applied=None) -> str:
    if target == "pr-title":
        return pr_title(decision)
    if target == "question-title":
        return question_title(decision)
    if target == "question-body":
        return question_body(decision)
    if target == "record-title":
        return record_title(decision)
    if target == "record-body":
        return record_body(decision, run_url=run_url, trial=trial, applied=applied)
    raise Refused(f"unknown render target {target!r} — one of {list(RENDER_TARGETS)}")


# --------------------------------------------------------------------------- #
# apply — the home, read again and written once                                #
# --------------------------------------------------------------------------- #

def home_changed_sentence(found: str) -> str:
    """What `apply` says when the home moved while the trial ran."""
    return (f"the home changed at {found} while the trial ran; nothing written; "
            "tomorrow's run sorts again")


def unread_sentence(reason: str) -> str:
    """What `apply` says when the home cannot be read again."""
    return (f"the home could not be read again ({reason}); nothing written; "
            "tomorrow's run sorts again")


def already_sentence(candidate: str, found: str) -> str:
    """What a rerun says when its own earlier write is the change it finds."""
    return (f"the home already names {candidate}, written by the daily adoption "
            f"at {found}; nothing written")


def not_retired_sentence(old: str) -> str:
    """What the record card says of an old id the adoption took off every
    group and project of the home: it stays priced and unretired, and its
    retirement is a person's decision, later."""
    return (f"{old} is on no group and no project of the home and is not "
            "retired; retiring it is a code change a person makes once part 3 "
            "has taken the choices out of config/models.yaml")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def adopted_document(doc, decision, written_at: str) -> dict:
    """The home `doc` with the Decision applied: in each group the Decision
    names, the candidate where the rung it replaces stood; in every project
    choice naming a replaced id, the candidate; `written_by: daily-adoption`
    and `written_at`. Everything else is the document as it was read."""
    _require(decision, ma.RULE_ADOPT, "apply")
    candidate = decision["candidate"]
    wanted = {(str(r.get("ladder")), str(r.get("model"))) for r in _replaces(decision)}
    if not wanted:
        raise Refused(f"{candidate} replaces no rung, so there is nothing to apply")
    olds = {model for _, model in wanted}
    new = copy.deepcopy(dict(doc))
    groups = new.get("groups") or {}
    missed = sorted((group, model) for group, model in wanted
                    if model not in ((groups.get(group) or {}).get("models") or []))
    if missed:
        raise Refused("the Decision names rung(s) the home does not hold: "
                      + ", ".join(f"{m} in the {g} group" for g, m in missed))
    for key, group in groups.items():
        group["models"] = list(dict.fromkeys(
            candidate if (str(key), m) in wanted else m for m in group["models"]))
    for entry in (new.get("projects") or {}).values():
        for group, model in list(entry.items()):
            if model in olds:
                entry[group] = candidate
    new["written_by"] = WRITER
    new["written_at"] = written_at
    return new


def left_the_home(doc, decision) -> list[str]:
    """The replaced ids `doc` names in no group and no project."""
    named = {m for group in (doc.get("groups") or {}).values()
             for m in group.get("models") or []}
    named |= {m for entry in (doc.get("projects") or {}).values()
              for m in entry.values()}
    return [m for m in dict.fromkeys(str(r.get("model")) for r in _replaces(decision))
            if m not in named]


def _read_home() -> tuple[dict | None, dict]:
    """`(document as written, outcome)`: the home read again, judged as a
    reader judges it, its tagged line on stderr. The document is the one in
    the variable, nothing set aside — what a writer builds on, so an entry
    this checkout cannot judge is refused by `home_errors`, never dropped."""
    try:
        text = mh._source(None, None)
        outcome = mh._outcome(mh.NOT_SET) if text is None else mh.judge(text)
    except mh._Unreadable as exc:
        text, outcome = None, mh._outcome(mh.UNREADABLE, str(exc))
    except Exception as exc:  # noqa: BLE001 — the line says it; nothing is written
        text, outcome = None, mh._outcome(
            mh.UNREADABLE, f"the reader failed ({type(exc).__name__}: {exc})")
    print(mh.status_line(outcome), file=sys.stderr)
    if outcome.get("state") != mh.READ:
        return None, outcome
    return json.loads(text), outcome


def _http_patch(url: str, headers: Mapping, body: str, timeout: float) -> tuple[int, str]:
    """`(status, body)` of one PATCH. An HTTP error status is an answer, not
    a raise; a network failure or a timeout raises."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, data=body.encode("utf-8"),
                                     headers=dict(headers), method="PATCH")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            text = ""
        return exc.code, text


def write_home(text: str, token: str) -> None:
    """ONE `PATCH` of the variable with `{"value": text}`, no retry. Raises
    `Refused` on anything but a 2xx answer."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "bureau-pipeline-model-adoption",
    }
    try:
        status, answer = _http_patch(mh.API_URL, headers, json.dumps({"value": text}),
                                     mh.TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001
        raise Refused(f"the write to the home did not reach GitHub "
                      f"({mh.one_line(exc)})") from exc
    if not 200 <= status < 300:
        raise Refused(f"GitHub answered HTTP {status} to the write of the home "
                      f"({mh.one_line(answer)})")


def _parse_apply_args(argv):
    candidate = None
    snapshot = None
    prices_path = ma.PRICES_PATH
    expect = None
    result = None
    check = False
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg == "--snapshot" and rest:
            snapshot = rest.pop(0)
        elif arg == "--prices" and rest:
            prices_path = rest.pop(0)
        elif arg == "--expect-written-at" and rest:
            expect = rest.pop(0)
        elif arg == "--result" and rest:
            result = rest.pop(0)
        elif arg == "--check":
            check = True
        elif not arg.startswith("--") and candidate is None:
            candidate = arg
        else:
            raise Refused(f"unknown or incomplete option {arg!r}")
    if not candidate:
        raise Refused("apply needs a <candidate-id>")
    if not expect and not check:
        raise Refused("apply needs --expect-written-at <written_at>, the home "
                      "classify read — without it a change made during the "
                      "trial could be overwritten")
    return candidate, snapshot, Path(prices_path), expect, result, check


def _say(outcome: str, written_at, sentences: list[str], result) -> None:
    """Each sentence on stdout and in the step summary, and the result file
    the record card reads."""
    for line in sentences:
        print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and sentences:
        with open(summary, "a") as fh:
            fh.write("\n" + "\n\n".join(sentences) + "\n")
    if result:
        Path(result).write_text(json.dumps(
            {"outcome": outcome, "written_at": written_at, "sentences": sentences}) + "\n")


def _cmd_apply(argv) -> int:
    """Read the home again; if it moved since `classify`, write nothing and
    say so (exit 0). Else classify against it, refuse anything but `adopt`,
    build the document, judge it with `home_errors`, and write it with one
    `PATCH` — or, with `--check`, print what would change and write nothing."""
    try:
        candidate, snapshot, prices_path, expect, result, check = _parse_apply_args(argv)
    except Refused as exc:
        print(f"::error::apply refused: {exc}")
        return 2

    doc, outcome = _read_home()
    if doc is None:
        reason = outcome.get("reason") or "no home is named to this step"
        _say("unread", None, [unread_sentence(reason)], result)
        return 0
    found = doc.get("written_at")
    if expect and found != expect:
        shown = mh.one_line(found)
        named = any(candidate in (g.get("models") or [])
                    for g in (doc.get("groups") or {}).values())
        if doc.get("written_by") == WRITER and named:
            _say("already", found, [already_sentence(candidate, shown)], result)
        else:
            _say("home-changed", found, [home_changed_sentence(shown)], result)
        return 0

    try:
        if snapshot:
            catalog = ma.load_catalog_file(snapshot)
        else:
            model_catalog.clear_catalog_cache()
            catalog = model_catalog.fetch_catalog(fetch=ma._fake_catalog_from_env())
        prices = load_prices(prices_path)
        decision = next((d for d in classify_catalog(catalog, ma.home_config(doc), prices)
                         if d["candidate"] == candidate), None)
        if decision is None:
            raise Refused(f"{candidate} is not a candidate — the catalog does not "
                          "list it, or the home already runs it")
        _require(decision, ma.RULE_ADOPT, "apply")
        new = adopted_document(doc, decision, _now())
    except (OSError, ValueError) as exc:
        print(f"::error::apply refused: {exc}")
        return 2

    text = json.dumps(new, indent=2) + "\n"
    violations = mh.home_errors(text, mf.declared_prices(prices_path), mh.code_side(),
                                mf.declared_repo_slugs())
    if violations:
        print(f"::error::apply refused: adopting {candidate} would leave the home "
              "breaking its rules — nothing written")
        for violation in violations:
            print(f"  - {violation}")
        return 2

    retired = set(mh.code_side().get("retired") or ())
    sentences = [not_retired_sentence(old) for old in left_the_home(new, decision)
                 if old not in retired]
    if check:
        print("".join(difflib.unified_diff(
            json.dumps(doc, indent=2).splitlines(keepends=True), text.splitlines(keepends=True),
            fromfile="the home as read", tofile="the home apply would write")), end="")
        for line in sentences:
            print(line)
        return 0

    token = os.environ.get(WRITE_TOKEN_ENV) or ""
    if not token:
        print(f"::error::apply refused: {WRITE_TOKEN_ENV} is empty, so the write "
              "token mint failed — nothing written")
        return 2
    try:
        write_home(text, token)
    except Refused as exc:
        print(f"::error::apply refused: {exc} — nothing written")
        return 2
    print(f"applied: {candidate} replaces "
          + ", ".join(f"{r['model']} in the {r['ladder']} group" for r in _replaces(decision))
          + f" in the home, written by {WRITER} at {new['written_at']}")
    _say("written", new["written_at"], sentences, result)
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
        f"of {decision['candidate']}. The workflow writes the home itself, so "
        "there is nothing here to dispatch — the card is the board's record of "
        "what the write did."
    )


def open_record_card(decision, *, run_url: str, ops=None, trial=None,
                     applied=None) -> dict:
    """File (or find) the adoption's record card, its body saying what
    `apply` did (`applied`, the file its `--result` wrote): in Done when it
    wrote the home, in Canceled when it wrote nothing.

    Raises `Refused` — before any Linear call — on a Decision that is not
    `adopt`. Otherwise NEVER raises: a Linear failure returns `card_owed`
    true, and the run carries on.
    """
    _require(decision, ma.RULE_ADOPT, "open-record-card")
    title = record_title(decision)
    ops = ops or _Ops()

    existing = _find(ops, title, "record card")
    if existing:
        return {"card": existing, "card_url": "", "card_owed": False, "note": ""}

    body = record_body(decision, run_url=run_url, trial=trial, applied=applied)
    written = (applied or {}).get("outcome") == "written"
    try:
        issue = ops.create_card(title, body, repo_slug=REPO_SLUG,
                                labels=list(RECORD_LABELS),
                                lane=RECORD_LANE if written else UNWRITTEN_LANE)
    except Exception as exc:  # noqa: BLE001 — the ONE thing that must not fail
        print(f"record card: NOT filed ({exc}) — the card is owed",
              file=sys.stderr)
        return {"card": "", "card_url": "", "card_owed": True,
                "note": str(exc).replace("\n", " ")[:300]}

    card = issue["identifier"]
    try:
        ops.stamp_card(card, RECORD_VERDICT, record_verdict_why(decision, run_url))
    except Exception as exc:  # noqa: BLE001
        print(f"record card: {card} filed but its routing verdict did not stamp "
              f"({exc})", file=sys.stderr)
    return {"card": card, "card_url": issue.get("url") or "",
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


def load_applied(path) -> dict | None:
    """What `apply --result` wrote, or None without one. A file that cannot
    be read records that it could not, rather than stopping the record."""
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {"outcome": "unknown", "written_at": None,
                "sentences": ["what the write did was not recorded by this run"]}
    return dict(data) if isinstance(data, Mapping) else None


def _cmd_render(argv) -> int:
    try:
        positional, opts = _options(
            argv, ("--decision", "--trial", "--run-url", "--applied"))
        if len(positional) != 1 or positional[0] not in RENDER_TARGETS:
            raise Refused(f"render needs one target of {list(RENDER_TARGETS)}")
        if "--decision" not in opts:
            raise Refused("render needs --decision <decision.json>")
        text = render(positional[0], load_decision(opts["--decision"]),
                      load_trial(opts.get("--trial")),
                      run_url=opts.get("--run-url", ""),
                      applied=load_applied(opts.get("--applied")))
    except (OSError, ValueError) as exc:
        print(f"::error::render refused: {exc}", file=sys.stderr)
        return 2
    print(text.rstrip("\n"))
    return 0


def _cmd_open(cmd, argv, ops) -> int:
    try:
        allowed = ("--decision", "--run-url", "--trial", "--applied") \
            if cmd == "open-record-card" else ("--decision",)
        positional, opts = _options(argv, allowed)
        if positional or "--decision" not in opts:
            raise Refused(f"{cmd} takes --decision <decision.json> only")
        if cmd == "open-record-card" and "--run-url" not in opts:
            raise Refused("open-record-card needs --run-url <url>")
        decision = load_decision(opts["--decision"])
        trial = load_trial(opts.get("--trial"))
        applied = load_applied(opts.get("--applied"))
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
            result = open_record_card(decision, run_url=opts["--run-url"], ops=ops,
                                      trial=trial, applied=applied)
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
