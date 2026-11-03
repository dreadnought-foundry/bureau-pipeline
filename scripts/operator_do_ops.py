#!/usr/bin/env python3
"""The steps the Operator Agent may do itself (DRE-8922, epic DRE-8738).

Part 1 of the agent decides that a card is `do` and names its step. This
module says, once, which of those steps the agent may carry out: the four
kinds, the one credential each kind's commands get, the exact command each op
becomes, which ops write, and what a plan may never touch. The rule is data,
`config/operator-do.json`; its `_readme` says what each field type means.

    load(path=None) -> dict
    render(op) -> list[str]           the argv, never a shell string
    command_line(op) -> str           the one line a claim, a receipt and a dry run print
    writes(op) -> bool
    credential(kind) -> str           `GH_TOKEN` or `LINEAR_API_KEY`
    plan_repo(plan) -> str | None     the plan's one owner/name, None for linear-admin
    check(plan, card_text, repo_map, card_repo) -> (ok, refusals)
    close_rule(title, description) -> one of CLOSE_RULES

## The plan

The model writes `<CARD>.plan.json`: `{"card", "kind", "step", "reason",
"ops": [{"op": <op>, ...its fields}], "continues": null | {"after":
"<YYYY-MM-DDTHH:MMZ>", "remains": <sentence>}}`. `check` reads it against the
card's own text — its title and description as one text, with fenced code
taken out — and refuses, naming the op (`op <n> <op>: <reason>`) or the plan
(`plan: <reason>`):

* an op not in the file, or one of a kind other than the card's — the card's
  kind is `operator_step_kind.classify` over that text, and the plan's `kind`
  must be it;
* a missing or unknown field, or a value of the wrong type;
* a `repo` that is not a value of `repo_map`, does not open the App's one
  organization, or is neither `repo_map[card_repo]` nor quoted in the text,
  and a second repository in one plan;
* a variable name, project name, branch name or workflow file the text does
  not quote word for word — the model never invents a target — and the
  default branch, quoted or not;
* a script off the file's list, or one whose arguments break its one form;
* the agent's own switches, wherever an op names them;
* more than `max_ops` ops.

## The close

`close_rule` reads the title and the description outside fenced code and
answers the first of `record-outcome` (the console's Record the outcome),
`card-done` (it names `card-done`) and `own-pr-merge` (one sentence says the
card closes on a pull request's merge), else `none`. The first and third
start from `operator_decide.CLOSES`.

Pure: it reads the file and nothing else, runs no command and writes nothing.
"""

from __future__ import annotations

import base64
import json
import re
import shlex
from pathlib import Path

import operator_decide
import operator_step_kind
from operator_rules import _outside_fences
from operator_step_kind import _APOSTROPHES

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "operator-do.json"
CONFIG_NAME = "config/operator-do.json"
MAP_NAME = "config/repo-map.json"

DO_KINDS = ("variable", "bot-pr", "linear-admin", "script")
CREDENTIALS = ("GH_TOKEN", "LINEAR_API_KEY")
FIELD_TYPES = ("repo", "variable", "project", "branch", "path", "sha", "text",
               "number", "bool", "status", "script", "args")
CLOSE_RULES = ("record-outcome", "card-done", "own-pr-merge", "none")
#: The kind part 1 sends to the studio, never to `do`.
STUDIO_KIND = "deploy"

_TOP = ("max_ops", "max_do_per_pass", "max_close_per_pass", "pr_wait_days")
_PLAN_KEYS = {"card", "kind", "step", "reason", "ops", "continues"}
_SLOT = re.compile(r"\{(\w+)(\|base64|\*)?\}")
_CARD = re.compile(r"DRE-\d+")
_AFTER = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\dZ")
_SHA = re.compile(r"[0-9a-f]{40}")
_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WORKFLOWS = ".github/workflows/"
_CARD_DONE = re.compile(r"(?<![\w-])card-done(?![\w-])", re.IGNORECASE)
_CLOSE_WORD = re.compile(r"\bclos(?:e|es|ed|ing)\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def load(path: Path | str | None = None) -> dict:
    """The file, refused when it breaks its shape: a kind outside
    `operator_step_kind.MACHINE_KINDS` or `deploy`, not exactly the four do
    kinds, a credential outside `CREDENTIALS`, an op named twice, a field of
    an unknown type, or a command naming a field its op does not declare."""
    with open(path or CONFIG_PATH, encoding="utf-8") as fh:
        doc = json.load(fh)
    for key in _TOP:
        if not isinstance(doc.get(key), int) or isinstance(doc[key], bool) or doc[key] < 1:
            raise ValueError(f"{key} must be a positive whole number")
    if not isinstance(doc.get("org"), str) or not doc["org"].endswith("/"):
        raise ValueError("org must be an organization name ending in /")
    for key in ("never", "never_branches"):
        if not isinstance(doc.get(key), list) or not all(isinstance(v, str) for v in doc[key]):
            raise ValueError(f"{key} must be a list of names")
    for script, spec in (doc.get("scripts") or {}).items():
        if not script.endswith(".py") or not isinstance(spec.get("head"), list):
            raise ValueError(f"the script {script!r} is not a .py path with a head")
        re.compile(spec["value"])
    seen = {}
    for kind, row in (doc.get("kinds") or {}).items():
        if kind == STUDIO_KIND:
            raise ValueError("deploy is never do: it needs production access and is the studio's")
        if kind not in operator_step_kind.MACHINE_KINDS:
            raise ValueError(f"{kind!r} is not a machine kind — the kinds are "
                             f"{', '.join(operator_step_kind.MACHINE_KINDS)}")
        if row.get("credential") not in CREDENTIALS:
            raise ValueError(f"the {kind} kind's credential {row.get('credential')!r} is not "
                             f"one of {', '.join(CREDENTIALS)}")
        for name, op in (row.get("ops") or {}).items():
            if name in seen:
                raise ValueError(f"the op {name!r} is under both {seen[name]} and {kind}")
            seen[name] = kind
            _load_op(name, op)
    if set(doc.get("kinds") or {}) != set(DO_KINDS):
        raise ValueError(f"the kinds must be exactly {', '.join(DO_KINDS)}")
    return doc


def _load_op(name: str, op: dict) -> None:
    fields = op.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ValueError(f"the op {name!r} declares no field")
    for field, kind in fields.items():
        if kind not in FIELD_TYPES:
            raise ValueError(f"the op {name!r} field {field!r} has the unknown type {kind!r}")
        if kind == "status" and not (op.get("choices") or {}).get(field):
            raise ValueError(f"the op {name!r} field {field!r} lists no choices")
    if not isinstance(op.get("writes"), bool):
        raise ValueError(f"the op {name!r} does not say whether it writes")
    command = op.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(a, str) for a in command):
        raise ValueError(f"the op {name!r} has no command")
    for arg in command:
        for slot in _SLOT.finditer(arg):
            if slot.group(1) not in fields:
                raise ValueError(f"the op {name!r} command names {{{slot.group(1)}}}, "
                                 "which is not one of its fields")
            if slot.group(2) == "*" and slot.group(0) != arg:
                raise ValueError(f"the op {name!r} spreads {{{slot.group(1)}*}} inside an argument")


def _ops(doc: dict) -> dict:
    """`{op name: (kind, op row)}` across every kind."""
    return {name: (kind, op) for kind, row in doc["kinds"].items() for name, op in row["ops"].items()}


def _known(op, doc: dict) -> tuple:
    name = op.get("op") if isinstance(op, dict) else None
    found = _ops(doc).get(name) if isinstance(name, str) else None
    if found is None:
        raise ValueError(f"{name!r} is not an op in {CONFIG_NAME}")
    return found


def _value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render(op: dict, doc: dict | None = None) -> list:
    """The argv `op` becomes. An op not in the file, or one missing a field
    its command names, is a `ValueError`."""
    doc = doc or load()
    _kind, row = _known(op, doc)
    argv = []
    for arg in row["command"]:
        slots = list(_SLOT.finditer(arg))
        for slot in slots:
            if slot.group(1) not in op:
                raise ValueError(f"the {op['op']} op has no {slot.group(1)}")
        if len(slots) == 1 and slots[0].group(2) == "*":
            argv.extend(_value(v) for v in op[slots[0].group(1)])
            continue

        def fill(slot):
            value = op[slot.group(1)]
            if slot.group(2) == "|base64":
                return base64.b64encode(str(value).encode("utf-8")).decode("ascii")
            return _value(value)
        argv.append(_SLOT.sub(fill, arg))
    return argv


def command_line(op: dict, doc: dict | None = None) -> str:
    """The one line a claim, a receipt and a dry run print for `op`."""
    return shlex.join(render(op, doc))


def writes(op: dict, doc: dict | None = None) -> bool:
    """True when `op` changes something, as the file declares it."""
    return _known(op, doc or load())[1]["writes"]


def credential(kind: str, doc: dict | None = None) -> str:
    """The one env name the kind's commands get."""
    doc = doc or load()
    if kind not in doc["kinds"]:
        raise ValueError(f"{kind!r} is not a kind the agent does — the kinds are "
                         f"{', '.join(DO_KINDS)}")
    return doc["kinds"][kind]["credential"]


def plan_repo(plan: dict) -> str | None:
    """The plan's one `owner/name`, or None when no op names one. Two is a
    `ValueError`: `check` refuses that plan."""
    repos = {op["repo"] for op in plan.get("ops") or [] if isinstance(op, dict) and "repo" in op}
    if len(repos) > 1:
        raise ValueError(f"the plan names {len(repos)} repositories: {', '.join(sorted(repos))}")
    return next(iter(repos), None)


# --- check ------------------------------------------------------------------


def _read(text: str | None) -> str:
    return _outside_fences(text or "").translate(_APOSTROPHES)


def _quoted(value: str, text: str) -> bool:
    """True when `text` holds `value` word for word, whitespace collapsed, and
    never as part of a longer name — `ALARM_REPAIR` is not in
    `ALARM_REPAIR_LIVE`, nor `x/y` in `x/y-z`."""
    if not value.strip():
        return False
    body = re.escape(value.translate(_APOSTROPHES)).replace("\\ ", r"\s+")
    head = r"(?<![\w/.-])" if re.match(r"[\w/.-]", value) else ""
    tail = r"(?![\w/-])" if re.search(r"[\w/-]$", value) else ""
    return re.search(f"{head}{body}{tail}", text) is not None


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


class _Reader:
    """One plan's check against one card."""

    def __init__(self, plan: dict, text: str, repo_map: dict, card_repo, doc: dict):
        self.plan, self.text, self.doc = plan, text, doc
        self.repo_map, self.card_repo = repo_map, card_repo
        self.card = plan.get("card") if isinstance(plan.get("card"), str) else ""
        self.first_repo = None
        #: Each field type's reader, but `status`, which also takes its choices.
        self.readers = {"repo": self.repo, "variable": self.variable, "project": self.project,
                        "branch": self.branch, "path": self.path, "sha": self.sha,
                        "text": self.text_, "number": self.number, "bool": self.bool_,
                        "script": self.script, "args": self.args}

    def repo(self, value, op) -> list:
        if not isinstance(value, str):
            return ["repo must be an owner/name"]
        if value not in self.repo_map.values():
            if value in self.repo_map:
                return [f"`{value}` is not a value of {MAP_NAME} — it is a slug, and its "
                        f"repository is `{self.repo_map[value]}`"]
            return [f"`{value}` is not a value of {MAP_NAME}"]
        org = self.doc["org"]
        if not value.startswith(org):
            return [f"`{value}` does not open {org} — the App reaches no other organization"]
        why = []
        if value != self.repo_map.get(self.card_repo or "") and not _quoted(value, self.text):
            why.append(f"`{value}` is neither the repo:{self.card_repo or '<none>'} label's "
                       "repository nor quoted in the card's text")
        if self.first_repo is None:
            self.first_repo = value
        elif value != self.first_repo:
            why.append(f"`{value}` is a second repository — the plan already names "
                       f"`{self.first_repo}`")
        return why

    def target(self, value, what: str) -> list:
        if not isinstance(value, str) or not value.strip():
            return [f"the {what} must be a name"]
        if not _quoted(value, self.text):
            return [f"`{value}` is not quoted in the card's text"]
        return []

    def variable(self, value, op) -> list:
        if not isinstance(value, str) or not _VARIABLE.fullmatch(value):
            return [f"`{value}` is not a variable name"]
        if value in self.doc["never"]:
            return []  # the switch scan names it
        return self.target(value, "variable name")

    def project(self, value, op) -> list:
        return self.target(value, "project name")

    def branch(self, value, op) -> list:
        if not isinstance(value, str) or not value.strip() or re.search(r"\s", value):
            return [f"`{value}` is not a branch name"]
        if value in self.doc["never_branches"]:
            return [f"`{value}` is the default branch, never written"]
        own = re.fullmatch(rf"agent/{re.escape(self.card)}-[A-Za-z0-9][A-Za-z0-9._-]*", value)
        if own or _quoted(value, self.text):
            return []
        return [f"`{value}` is not quoted in the card's text, nor `agent/{self.card or 'DRE-<n>'}-<slug>`"]

    def path(self, value, op) -> list:
        if (not isinstance(value, str) or not value.strip() or value.startswith("/")
                or ".." in value.split("/")):
            return [f"`{value}` is not a path inside the repository"]
        if value.startswith(_WORKFLOWS) and not (
                _quoted(value, self.text) or _quoted(value.rsplit("/", 1)[-1], self.text)):
            return [f"`{value}` is not quoted in the card's text — a workflow file is only "
                    "ever one the card names"]
        return []

    def sha(self, value, op) -> list:
        if not isinstance(value, str) or not _SHA.fullmatch(value):
            return [f"`{value}` is not a commit sha (40 hex) — a branch starts at a commit"]
        return []

    def text_(self, value, op) -> list:
        return [] if isinstance(value, str) else ["must be text"]

    def number(self, value, op) -> list:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return [f"`{value}` is not a pull request number"]
        return []

    def bool_(self, value, op) -> list:
        return [] if isinstance(value, bool) else [f"`{value}` is not true or false"]

    def status(self, value, op, choices) -> list:
        if value not in choices:
            return [f"`{value}` is not one of {', '.join(choices)}"]
        return []

    def script(self, value, op) -> list:
        if value not in self.doc["scripts"]:
            return [f"`{value}` is not on the script list ({', '.join(self.doc['scripts'])})"]
        return []

    def args(self, value, op) -> list:
        spec = self.doc["scripts"].get(op.get("path"))
        if spec is None:
            return []  # the script's own refusal says why
        form = (f"`{' '.join(spec['head'])} {spec['repeat']} <value> …`, with "
                f"{{repo}} the op's own repo")
        if not isinstance(value, list) or not all(isinstance(a, str) for a in value):
            return [f"its arguments are not a list of strings — the one form is {form}"]
        head = [a.replace("{repo}", str(op.get("repo"))) for a in spec["head"]]
        rest = value[len(head):]
        pairs = list(zip(rest[0::2], rest[1::2]))
        if (value[:len(head)] != head or len(rest) % 2 or len(pairs) < spec["min_repeat"]
                or any(flag != spec["repeat"] or not re.fullmatch(spec["value"], said)
                       for flag, said in pairs)):
            return [f"its arguments are not the one form {form}"]
        return []


def _plan_refusals(plan, ops, doc: dict, card_kind: tuple) -> list:
    refusals = []
    unknown = sorted(set(plan) - _PLAN_KEYS)
    if unknown:
        refusals.append(f"plan: unknown key {', '.join(unknown)}")
    if not isinstance(plan.get("card"), str) or not _CARD.fullmatch(plan["card"]):
        refusals.append(f"plan: card {plan.get('card')!r} is not a DRE-<n> identifier")
    for key in ("step", "reason"):
        if not isinstance(plan.get(key), str) or not plan[key].strip():
            refusals.append(f"plan: it has no {key}")
    continues = plan.get("continues")
    if continues is not None and not (
            isinstance(continues, dict) and set(continues) == {"after", "remains"}
            and isinstance(continues["after"], str) and _AFTER.fullmatch(continues["after"])
            and isinstance(continues["remains"], str) and continues["remains"].strip()):
        refusals.append("plan: continues is neither null nor {after: YYYY-MM-DDTHH:MMZ, remains}")
    cls, kind, evidence = card_kind
    if cls != operator_step_kind.MACHINE:
        refusals.append(f"plan: the card is a person's step ({evidence}), and the agent does none")
    elif kind not in doc["kinds"]:
        refusals.append(f"plan: the card's kind is {kind}, which the agent never does")
    if plan.get("kind") not in doc["kinds"]:
        refusals.append(f"plan: kind {plan.get('kind')!r} is not a kind the agent does")
    elif cls == operator_step_kind.MACHINE and plan["kind"] != kind:
        refusals.append(f"plan: kind {plan['kind']}, and the card's kind is {kind}")
    if not isinstance(ops, list):
        refusals.append("plan: ops is not a list")
    elif not ops:
        refusals.append("plan: it names no op")
    elif len(ops) > doc["max_ops"]:
        refusals.append(f"plan: it names {len(ops)} ops, more than max_ops {doc['max_ops']}")
    return refusals


def _op_refusals(op, reader: _Reader, doc: dict, card_kind: str) -> list:
    if not isinstance(op, dict):
        return ["not an object"]
    try:
        kind, row = _known(op, doc)
    except ValueError:
        return [f"not an op in {CONFIG_NAME}"]
    why = []
    if kind != card_kind:
        why.append(f"a {kind} op, and the card's kind is {card_kind}")
    fields = row["fields"]
    why += [f"missing field `{f}`" for f in fields if f not in op]
    why += [f"unknown field `{f}`" for f in op if f != "op" and f not in fields]
    for field, ftype in fields.items():
        if field not in op:
            continue
        if ftype == "status":
            why += reader.status(op[field], op, row["choices"][field])
        else:
            why += reader.readers[ftype](op[field], op)
    for switch in doc["never"]:
        pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(switch)}(?![A-Za-z0-9_])")
        if any(pattern.search(s) for f, v in op.items() if f != "op" for s in _strings(v)):
            why.append(f"`{switch}` is the agent's own switch")
    return why


def check(plan: dict, card_text: str | None, repo_map: dict, card_repo: str | None,
          doc: dict | None = None) -> tuple:
    """`(ok, refusals)` for one plan against its card — see the module
    docstring. `card_text` is the card's title and description as one text;
    fenced code in it is not read. `card_repo` is the slug of the card's
    `repo:` label, or None. `ok` is True exactly when `refusals` is empty."""
    doc = doc or load()
    if not isinstance(plan, dict):
        return False, ["plan: not a JSON object"]
    text = _read(card_text)
    card_kind = operator_step_kind.classify(text, "")
    ops = plan.get("ops")
    refusals = _plan_refusals(plan, ops, doc, card_kind)
    reader = _Reader(plan, text, repo_map, card_repo, doc)
    for index, op in enumerate(ops if isinstance(ops, list) else [], start=1):
        name = op.get("op") if isinstance(op, dict) else None
        label = name if isinstance(name, str) and re.fullmatch(r"[a-z][a-z-]*", name) else "unnamed"
        refusals += [f"op {index} {label}: {why}"
                     for why in _op_refusals(op, reader, doc, card_kind[1])]
    return not refusals, refusals


# --- close_rule -------------------------------------------------------------


def close_rule(title: str | None, description: str | None) -> str:
    """The card's close, one of `CLOSE_RULES` — the first in that order that
    its title or description outside fenced code names."""
    text = f"{title or ''}\n{_read(description)}".translate(_APOSTROPHES)
    record_outcome, merge = (pattern for pattern, _name in operator_decide.CLOSES)
    if record_outcome.search(text):
        return "record-outcome"
    if _CARD_DONE.search(text):
        return "card-done"
    if any(_CLOSE_WORD.search(s) and merge.search(s) for s in _SENTENCE.split(text)):
        return "own-pr-merge"
    return "none"
