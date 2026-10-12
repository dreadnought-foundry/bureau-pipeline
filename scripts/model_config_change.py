#!/usr/bin/env python3
"""A change set from the console, as ONE edit to config/models.yaml (DRE-6624).

The CEO approved Settings › Agents & models on 2026-10-10. On that page an
admin moves agents between groups, sets each group's default model and gives a
project an exception, then presses Save changes. The console never edits YAML
itself: it dispatches `.github/workflows/model-config-change.yml` with a CHANGE
SET, and this module is what that workflow runs —

    {"requested_by": "<the admin's sign-in>",
     "changes": [{"op": "move_agent", "agent": "proof", "group": "advisory"},
                 ...]}

The six operations:

    move_agent          agents.<agent> = <group>; an agent listed under
                        `ungrouped` leaves that list (a session agent stays in
                        `session_agents`)
    add_agent           a new name in `ungrouped` and `session_agents`, with its
                        `about` line
    add_group           a new `kinds` entry (`label`, `purpose`) plus a ladder
                        of the same name whose first rung is `model` — a build
                        group beside the three (DRE-6834); its name is
                        lowercase letters, digits and dashes, starting with a
                        letter, and one that is taken, empty or not that shape
                        is refused in one plain sentence
    set_default_model   that model moves to the front of the group's ladder and
                        the other rungs keep their order; a model not on the
                        ladder is inserted at the front
    set_project_model   projects.<project>.<group> = <model>
    clear_project_model removes projects.<project>.<group>

The CLI, as the workflow calls it:

    apply --changes <change-set.json> [--config <models.yaml>]
          [--registry <agents.yaml>] [--today <YYYY-MM-DD>] [--check]
    render pr-title|pr-body --changes <change-set.json> [--config <models.yaml>]
    branch --changes <change-set.json>

A TEXT EDIT, NEVER A RE-DUMP
----------------------------
`config/models.yaml` carries the record of every spend decision ever made in it
as comments and `reason:` blocks, and a YAML round-trip drops every one — the
reason `model_adoption_actions.apply_text` edits the text too. Each operation
here touches only the lines it names. The parsed result of every edit is then
compared with the same change made on the parsed document, so a line matched by
mistake is refused here rather than shipped.

REFUSED WHOLE, WITH A REASON PER CHANGE
---------------------------------------
Each change is judged against the file as the changes before it left it: first
the plain checks a change needs before it can be made at all (the agent exists,
the group exists, the model is not retired and has a declared price, the pinned
Claude Code can run it), then
`model_fallback.policy_errors` — the gate `sync_model_config.py --check` holds
every pull request to — on the result. A change is refused for exactly the
findings it ADDS, so each refusal names its own reasons. One refusal refuses the
set: nothing is written, the CLI exits 2 with one `::error::` line per reason,
and the workflow opens no branch and no pull request.

One more check is the generator's, made early: `sync_model_config.py` needs an
`agents.yaml` region for every name in `agents` that is not a session agent.
The console's interactive agents are session agents (`session_agents`,
DRE-6833) — they run in a person's session, have no region and need none — so
any of them may join any group. A name in neither the registry nor
`session_agents` is refused here, with that reason, rather than failing the
regeneration on a branch that already exists.

PLAIN WORDS FOR A REFUSAL (DRE-6833)
------------------------------------
The console shows the CEO each `::error::` line and each summary bullet of a
refusal word for word, so each is one plain sentence: the change in
`describe`'s words, then why (`plain_lines`). The findings themselves —
`policy_errors`' text, which `sync_model_config.py --check` prints for
engineers — go to the run log as ordinary output, and
`sync_model_config.py` publishes the same plain line as a refused move's
`reason` in `config/model-choices.json`.

A CHOICE IS COMPLETE (DRE-6789)
-------------------------------
The CEO, 2026-10-10: a choice from the console is complete, and he never has to
know a rule exists. So a `set_default_model`, `set_project_model` or
`add_group` (DRE-6834) naming a model under `excluded` removes that entry in
the same change (the last one gone
leaves `excluded: []`), and a `clear_project_model`, or a `set_project_model`
that replaces a standing exception, sets the model it took off aside again —
appended under `excluded` with a dated reason — when no ladder, other project,
`retired` or `excluded` names it any more. Every model the file ever named stays
under one of those four keys, the ones the known models are read from. No
`review_separation` rule is ever written: the selector keeps a reviewer off the
build model by itself (DRE-6785).

The pull request says each of these in plain words, from `model_choice` and
`model_fallback` and never restated: a choice's line ends on its
`model_choice.caution_for` sentence, read against the file just before it
(an `add_group`'s model priced against the Builders' default, DRE-6834),
and is followed by the set-aside line, the line naming where a pull request
built on a model that now builds and reviews is reviewed (a Builders or
Reviewers choice only), and the set-aside-again line, as they apply.

THE BRANCH IS THE CHANGE SET
----------------------------
`agent/model-config-<digest>`, the digest a hash of the `changes` list alone.
The same changes always name the same branch — whoever pressed Save — so a
re-run finds the pull request the first run opened. It is a cardless `agent/`
branch, the shape `model-adoption.yml` opens on when it has no record card
(`model_adoption_actions.fallback_branch`), and its body opens with the
`What's new:` line the merge gate reads.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model_choice  # noqa: E402
from model_fallback import ADDED_GROUP_NAME, ADVISORY_KIND, JUDGEMENT_KIND  # noqa: E402
from model_fallback import JUDGEMENT_ROLES, WORKHORSE_KIND, build_kinds  # noqa: E402
from model_fallback import _project_ids, _unpriced  # noqa: E402
from model_fallback import declared_prices, effective_ladders, policy_errors  # noqa: E402
from model_fallback import reviewers_for  # noqa: E402
from sync_model_config import _YAML_BEGIN as REGISTRY_REGION  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "models.yaml"
REGISTRY_PATH = ROOT / "agents.yaml"

COMMANDS = ("apply", "render", "branch")
RENDER_TARGETS = ("pr-title", "pr-body")

#: Each operation and the fields it carries, in the order a line names them.
OPS = {
    "move_agent": ("agent", "group"),
    "add_agent": ("agent", "about"),
    "add_group": ("group", "label", "purpose", "model"),
    "set_default_model": ("group", "model"),
    "set_project_model": ("project", "group", "model"),
    "clear_project_model": ("project", "group"),
}
_TEXT_FIELDS = ("about", "label", "purpose")
_NAME = re.compile(r"[a-z][a-z0-9._-]*")
_MAX_TEXT = 200

BRANCH_PREFIX = "agent/model-config-"
TITLE_PREFIX = "Model config change from the console"


class Refused(ValueError):
    """A change set (or an edit) this module will not act on. Exit 2."""


# --------------------------------------------------------------------------- #
# Reading a change set                                                         #
# --------------------------------------------------------------------------- #

def _one_line(value, where: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{where}: must be one line of text")
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        errors.append(f"{where}: must be one line of text, with no line breaks")
        return None
    text = " ".join(value.split())
    if len(text) > _MAX_TEXT:
        errors.append(f"{where}: must be one line of text of at most {_MAX_TEXT} "
                      "characters")
        return None
    return text


def _usable_name(value: str) -> bool:
    """A lowercase id that reads back as the same string key — `yes`, `null`
    and `on` are YAML booleans and nulls, never names."""
    import yaml

    if not _NAME.fullmatch(value):
        return False
    return yaml.safe_load(f"{value}: x\n") == {value: "x"}


def check_change_set(change_set) -> dict:
    """The change set with its text normalized to single spaces, or `Refused`
    naming every way it is malformed."""
    if not isinstance(change_set, Mapping):
        raise Refused("the change set must be a JSON object with `requested_by` "
                      "and `changes`")
    errors: list[str] = []
    unknown = sorted(set(change_set) - {"requested_by", "changes"})
    if unknown:
        errors.append(f"the change set has unknown key(s) {unknown}")
    requested_by = _one_line(change_set.get("requested_by"), "requested_by", errors)
    changes = change_set.get("changes")
    if not isinstance(changes, list) or not changes:
        errors.append("the change set has no changes")
        changes = []
    clean = []
    for n, change in enumerate(changes, 1):
        if not isinstance(change, Mapping):
            errors.append(f"change {n}: must be an object with an `op`")
            continue
        op = change.get("op")
        if op not in OPS:
            errors.append(f"change {n}: unknown op {op!r} — one of {list(OPS)}")
            continue
        fields = OPS[op]
        extra = sorted(set(change) - {"op", *fields})
        if extra:
            errors.append(f"change {n} ({op}): unknown field(s) {extra}")
        entry = {"op": op}
        for field in fields:
            where = f"change {n} ({op}).{field}"
            value = change.get(field)
            if field in _TEXT_FIELDS:
                entry[field] = _one_line(value, where, errors)
            elif op == "add_group" and field == "group":
                # Judged by `_problems`, so a name the console may not use is
                # refused in one plain sentence (DRE-6834).
                entry[field] = value.strip() if isinstance(value, str) else ""
            elif not isinstance(value, str) or not value:
                errors.append(f"{where}: missing")
            elif not _usable_name(value):
                errors.append(f"{where}: {value!r} is not a name — lowercase "
                              "letters, digits, '.', '_' and '-'")
            else:
                entry[field] = value
        clean.append(entry)
    if errors:
        raise Refused("\n".join(errors))
    return {"requested_by": requested_by, "changes": clean}


def load_change_set(path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Refused(f"cannot read the change set {path} ({exc})") from exc
    return check_change_set(data)


def digest(change_set) -> str:
    """Twelve hex characters of the `changes` list — never `requested_by`, so
    the same changes are the same branch whoever asked."""
    canonical = json.dumps(change_set["changes"], sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def branch_name(change_set) -> str:
    return f"{BRANCH_PREFIX}{digest(change_set)}"


# --------------------------------------------------------------------------- #
# Plain English — the pull request                                             #
# --------------------------------------------------------------------------- #

def _kind_spec(config, group):
    return ((config or {}).get("kinds") or {}).get(group)


def _label(config, group: str) -> str:
    spec = _kind_spec(config, group)
    label = spec.get("label") if isinstance(spec, Mapping) else None
    return label if isinstance(label, str) and label else group


def _ladder_name(config, group: str) -> str:
    spec = _kind_spec(config, group)
    return str(spec.get("ladder") if isinstance(spec, Mapping) else spec)


def _rung_ids(rungs) -> list[str]:
    return [r.get("model") if isinstance(r, Mapping) else r for r in rungs or []]


def describe(change: dict, config) -> str:
    """One plain-English line for one change, read against the file as it
    stood before the change set."""
    op = change["op"]
    if op == "move_agent":
        agent, group = change["agent"], change["group"]
        now = ((config or {}).get("agents") or {}).get(agent)
        where = (f"from the {_label(config, now)} group" if now
                 else "(today in no group)")
        return f"Move {agent} {where} into the {_label(config, group)} group."
    if op == "add_agent":
        return (f"Add a new agent, {change['agent']}, in no group yet: "
                f"\"{change['about']}\".")
    if op == "add_group":
        named = f" ({change['group']})" if change["group"] else ""
        return (f"Add a new group, {change['label']}{named}: "
                f"\"{change['purpose']}\", with {change['model']} as its "
                "default model.")
    if op == "set_default_model":
        label = _label(config, change["group"])
        ladder = ((config or {}).get("ladders") or {}).get(
            _ladder_name(config, change["group"]))
        rest = ("its other models keep their order"
                if change["model"] in _rung_ids(ladder)
                else "it is new to this group and goes ahead of its other models")
        return (f"Make {change['model']} the default model for the {label} "
                f"group — {rest}.")
    label = _label(config, change["group"])
    if op == "set_project_model":
        return (f"Run {change['project']}'s {label} group on {change['model']} "
                "first, ahead of the rest of that group's models.")
    return (f"Remove {change['project']}'s exception for the {label} group, so "
            f"{change['project']} uses the fleet's {label} models again.")


def pr_title(change_set) -> str:
    n = len(change_set["changes"])
    return (f"{TITLE_PREFIX}: {n} change{'' if n == 1 else 's'} "
            f"({digest(change_set)})")


def _whom(config, change: dict) -> str:
    """`Builders`, or `atlas's Builders` for a project's choice — or the
    label an `add_group` gives the group it adds."""
    label = change.get("label") or _label(config, change["group"])
    return f"{change['project']}'s {label}" if change.get("project") else label


def _separation_note(change: dict, after) -> str | None:
    """Where a pull request built on the chosen model is reviewed, when the
    choice puts it on both the build walk and the reviewers' walk of its
    scope (DRE-6789). Said, never written: the selector takes the build model
    off the reviewers' walk by itself (DRE-6785). A build walk is the
    Builders' or a group's added beside them (DRE-6834). The planning model
    neither builds nor reviews a pull request, so a Planners choice says
    nothing."""
    builds = build_kinds((after or {}).get("kinds") or {})
    if change["group"] not in (*builds, ADVISORY_KIND):
        return None
    chosen, slug = change["model"], change.get("project")
    walks = effective_ladders(after, slug)
    on_both = (chosen in walks.get(_ladder_name(after, ADVISORY_KIND), [])
               and any(chosen in walks.get(_ladder_name(after, kind), [])
                       for kind in builds))
    reviewers = reviewers_for(after, chosen, slug) if on_both else []
    if not reviewers or reviewers[0] == chosen:
        return None
    return (f"{model_choice.model_label(chosen)} now builds and reviews; a pull "
            "request built on it is reviewed on "
            f"{model_choice.model_label(reviewers[0])}.")


def _caution(change: dict, before, after, prices) -> str | None:
    """The `model_choice.caution_for` sentence a choice carries, read against
    the file just before it. An `add_group` has no model of its own to compare
    with, so its first one is priced against the Builders' default, the group
    it is built beside (DRE-6834): read on `after`, where the group and its
    label are, with `before`'s `excluded`, where the model may still be set
    aside."""
    group, model = change["group"], change["model"]
    try:
        if change["op"] == "add_group":
            builders = _rung_ids(before["ladders"][_ladder_name(before, WORKHORSE_KIND)])
            return model_choice.caution_for(
                {**after, "excluded": before.get("excluded")}, prices, group, model,
                current=builders[0])
        current = _standing(before, change) if change["op"] == "set_project_model" else None
        return model_choice.caution_for(before, prices, group, model, current=current)
    except (KeyError, IndexError):  # a change apply refuses; render stays total
        return None


def _change_lines(change: dict, before, after, prices) -> list[str]:
    """The `## What changes` lines for one change, read against the file just
    before it (`before`) and just after it (`after`): its line, ending on its
    caution when it has one, then the set-aside, separation and set-aside-again
    lines that belong to it (DRE-6789)."""
    line = describe(change, before)
    notes = []
    if change["op"] in MODEL_OPS and after is not None:
        model = change["model"]
        caution = _caution(change, before, after, prices)
        if caution:
            line = f"{line} {caution}"
        if _lifts(before, change):
            date = model_choice.set_aside_date(before, model)
            on = f" on {date}" if date else ""
            notes.append(f"{model_choice.model_label(model)} was set aside{on} and "
                         f"is brought back by this choice for {_whom(before, change)}.")
        separation = _separation_note(change, after)
        if separation:
            notes.append(separation)
    left = _leaving(before, change) if after is not None else None
    if (left is not None and left not in _rung_ids(before.get("excluded"))
            and left in _rung_ids(after.get("excluded"))):
        notes.append(f"{model_choice.model_label(left)} is set aside: no group or "
                     "project runs on it now, and it can be chosen again from the "
                     "console.")
    # The notes are the change's own bullet, continued: one `- ` line per change.
    return [f"- {line}"] + [f"  {note}" for note in notes]


def pr_body(change_set, config=None, *, prices=None, today: str | None = None) -> str:
    """Who asked and what changes, one line per change. No verdict marker,
    whatever the console sent (standards/untrusted-content.md).

    Each change is read against the file as the changes before it left it,
    replayed with `edit_data` from `config` — the file before the set."""
    # Imported here, not at the top: `sync_model_config` imports this module
    # to judge the console's moves (DRE-6833), and needs none of this.
    from model_adoption_actions import WHATS_NEW_NONE, _quiet

    if config is None:
        import yaml

        config = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    prices = declared_prices() if prices is None else prices
    today = today or datetime.now(timezone.utc).date().isoformat()
    lines, state = [], config
    for change in change_set["changes"]:
        try:
            after = edit_data(state, change, today)
        except (KeyError, TypeError, AttributeError):  # a change apply refuses
            after = None
        lines += _change_lines(change, state, after, prices)
        state = state if after is None else after
    body = "\n\n".join([
        WHATS_NEW_NONE,
        f"{change_set['requested_by']} asked for this from the console's "
        "Settings › Agents & models page.",
        "## What changes\n\n" + "\n".join(lines),
        "## How it was made\n\n"
        "The model config change workflow made these changes to "
        "`config/models.yaml` as a text edit that keeps every comment, "
        "regenerated `agents.yaml`, the fallback mirror in "
        "`scripts/model_fallback.py` and `config/model-choices.json` with "
        "`scripts/sync_model_config.py`, and held the result to "
        "`model_fallback.policy_errors` and `sync_model_config.py --check` "
        "before it opened this pull request. The critic and the merge gate are "
        "its review.",
    ])
    return _quiet(body) + "\n"


# --------------------------------------------------------------------------- #
# The edit on the parsed document — what the text edit must produce            #
# --------------------------------------------------------------------------- #

def _rung_reason(change: dict, config, today: str) -> str:
    if change["op"] == "add_group":
        return (f"the first rung of the {change['label']} group, set from the "
                f"console on {today}")
    return (f"made the {_label(config, change['group'])} group's default from "
            f"the console on {today}")


#: The two operations that choose a model for a group or a project.
CHOICE_OPS = ("set_default_model", "set_project_model")
#: Every operation that names a model, `add_group`'s first one with them
#: (DRE-6834): each is a choice, held to the pinned Claude Code and lifting
#: an exclusion the same way.
MODEL_OPS = CHOICE_OPS + ("add_group",)


def _standing(raw, change: dict) -> str | None:
    """The project's standing exception for the change's group, or None."""
    projects = (raw or {}).get("projects") or {}
    entry = projects.get(change["project"]) if isinstance(projects, Mapping) else None
    return entry.get(change["group"]) if isinstance(entry, Mapping) else None


def _lifts(raw, change: dict) -> bool:
    """A choice of a model under `excluded` brings it back (DRE-6789)."""
    return (change["op"] in MODEL_OPS
            and change["model"] in _rung_ids((raw or {}).get("excluded")))


def _leaving(raw, change: dict) -> str | None:
    """The model a clear, or a set that replaces a standing exception, takes
    off `projects.<slug>.<kind>`."""
    if change["op"] not in ("set_project_model", "clear_project_model"):
        return None
    current = _standing(raw, change)
    return current if current is not None and current != change.get("model") else None


def _named(doc) -> set[str]:
    """Every id the file names under a ladder, `projects`, `retired` or
    `excluded` — the four keys the known models are read from (DRE-6785)."""
    doc = doc or {}
    ids = {m for rungs in (doc.get("ladders") or {}).values() for m in _rung_ids(rungs)}
    return (ids | _project_ids(doc) | set(_rung_ids(doc.get("retired")))
            | set(_rung_ids(doc.get("excluded"))))


def _set_aside_reason(raw, change: dict, today: str) -> str:
    """The `excluded` reason of a model a project's choice leaves on nothing.
    Opens with the date, so `model_choice.set_aside_date` reads it, and is in
    plain words, so the console can show it as written."""
    until = ("that choice was cleared" if change["op"] == "clear_project_model"
             else f"{model_choice.model_label(change['model'])} replaced it")
    return (f"set aside on {today}: {change['project']}'s "
            f"{_label(raw, change['group'])} ran on it from the console until "
            f"{until}, and no group or project names it now.")


def edit_data(raw, change: dict, today: str) -> dict:
    doc = copy.deepcopy(raw)
    op = change["op"]
    if _lifts(raw, change):
        doc["excluded"] = [e for e in doc.get("excluded") or []
                           if _rung_ids([e]) != [change["model"]]]
    if op == "move_agent":
        agents = doc.get("agents") or {}
        if change["agent"] not in agents:
            doc["ungrouped"] = [n for n in doc.get("ungrouped") or []
                                if n != change["agent"]]
        agents[change["agent"]] = change["group"]
        doc["agents"] = agents
    elif op == "add_agent":
        doc["ungrouped"] = list(doc.get("ungrouped") or []) + [change["agent"]]
        doc["session_agents"] = (list(doc.get("session_agents") or [])
                                 + [change["agent"]])
        about = doc.get("about") or {}
        about[change["agent"]] = change["about"]
        doc["about"] = about
    elif op == "add_group":
        doc["kinds"][change["group"]] = {"ladder": change["group"],
                                         "label": change["label"],
                                         "purpose": change["purpose"]}
        doc["ladders"][change["group"]] = [
            {"model": change["model"], "reason": _rung_reason(change, raw, today)}]
    elif op == "set_default_model":
        rungs = doc["ladders"][_ladder_name(raw, change["group"])]
        ids = _rung_ids(rungs)
        if change["model"] in ids:
            rungs.insert(0, rungs.pop(ids.index(change["model"])))
        else:
            rungs.insert(0, {"model": change["model"],
                             "reason": _rung_reason(change, raw, today)})
    elif op == "set_project_model":
        projects = doc.get("projects") or {}
        projects.setdefault(change["project"], {})[change["group"]] = change["model"]
        doc["projects"] = projects
    else:
        projects = doc.get("projects") or {}
        entry = projects[change["project"]]
        del entry[change["group"]]
        if not entry:
            del projects[change["project"]]
        doc["projects"] = projects
    left = _leaving(raw, change)
    if left is not None and left not in _named(doc):
        doc["excluded"] = list(doc.get("excluded") or []) + [
            {"model": left, "reason": _set_aside_reason(raw, change, today)}]
    return doc


# --------------------------------------------------------------------------- #
# The edit on the text                                                         #
# --------------------------------------------------------------------------- #

_TOP_KEY = re.compile(r"^([A-Za-z_][\w-]*):(.*)$")
_TOP_VALUE = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*?)\s*(#.*)?$")
_ENTRY = re.compile(r"^(\s*)(['\"]?)([^'\"\s:#]+)\2(:\s*)(['\"]?)([^'\"\s#]*)\5(\s*(?:#.*)?)$")
_ITEM = re.compile(r"^(\s*)-\s+(['\"]?)([^'\"\s#]+)\2\s*(?:#.*)?$")
_RUNG = re.compile(r"^(\s*)-\s+model:")


def _body(line: str) -> str:
    return line.rstrip("\r\n")


def _ending(line: str) -> str:
    return line[len(_body(line)):] or "\n"


def _indent(line: str) -> int:
    body = _body(line)
    return len(body) - len(body.lstrip())


def _content(line: str) -> bool:
    body = _body(line).strip()
    return bool(body) and not body.startswith("#")


def _top(lines: list[str], key: str) -> int:
    for i, line in enumerate(lines):
        found = _TOP_KEY.match(_body(line))
        if found and found.group(1) == key:
            return i
    raise Refused(f"config/models.yaml has no `{key}:` block")


def _top_value(lines: list[str], at: str) -> tuple[str, str]:
    """The inline value on a top-level key's line and its trailing comment."""
    found = _TOP_VALUE.match(_body(lines[at]))
    if not found:
        raise Refused(f"cannot read the line `{_body(lines[at])}`")
    return found.group(2), (" " + found.group(3)) if found.group(3) else ""


def _block_end(lines: list[str], start: int) -> int:
    """One past the last indented line of the top-level block opened at
    `start`. Column-0 comments after it belong to the next key."""
    end = start + 1
    for i in range(start + 1, len(lines)):
        body = _body(lines[i])
        if not body.strip():
            continue
        if not body[0].isspace():
            if body.startswith("#"):
                continue
            break
        end = i + 1
    return end


def _children(lines: list[str], start: int, end: int, indent: int | None = None):
    """`(index, key)` of each content line at the block's child indent."""
    rows = [i for i in range(start + 1, end) if _content(lines[i])]
    if not rows:
        return [], indent
    indent = _indent(lines[rows[0]]) if indent is None else indent
    found = []
    for i in rows:
        if _indent(lines[i]) != indent:
            continue
        entry = _ENTRY.match(_body(lines[i]))
        item = _ITEM.match(_body(lines[i]))
        if entry:
            found.append((i, entry.group(3)))
        elif item:
            found.append((i, item.group(3)))
    return found, indent


def _block(lines: list[str], key: str, inline_empty: str):
    """`(start, end, empty)` for a top-level block, refusing an inline value
    other than the empty one this edit knows how to grow."""
    start = _top(lines, key)
    value, _ = _top_value(lines, start)
    if value and value != inline_empty:
        raise Refused(f"`{key}:` is written inline; this edit works on a block")
    return start, _block_end(lines, start), value == inline_empty


def _set_value(line: str, value: str) -> str:
    entry = _ENTRY.match(_body(line))
    if not entry:
        raise Refused(f"cannot read the line `{_body(line)}`")
    return (f"{entry.group(1)}{entry.group(2)}{entry.group(3)}{entry.group(2)}"
            f"{entry.group(4)}{entry.group(5)}{value}{entry.group(5)}"
            f"{entry.group(7)}{_ending(line)}")


def _scalar(text: str) -> str:
    """`text` as a YAML scalar that reads back as exactly `text`: plain when it
    can be, else double-quoted (a JSON string is a YAML double-quoted one)."""
    import yaml

    for rendered in (text, json.dumps(text, ensure_ascii=False)):
        try:
            if yaml.safe_load(f"k: {rendered}\n") == {"k": text}:
                return rendered
        except yaml.YAMLError:
            continue
    raise Refused(f"cannot write {text!r} as one line of YAML")


def _grow(lines: list[str], key: str, inline_empty: str, new: list[str]) -> list[str]:
    """Append `new` (bodies, no endings, at the right indent) to a top-level
    block, turning `key: {}` / `key: []` into a block first."""
    start, end, empty = _block(lines, key, inline_empty)
    ending = _ending(lines[start])
    if empty:
        _, comment = _top_value(lines, start)
        lines[start] = f"{key}:{comment}{ending}"
        end = start + 1
    return lines[:end] + [f"{body}{ending}" for body in new] + lines[end:]


def _child_indent(lines: list[str], key: str, inline_empty: str, default: int = 2) -> int:
    start, end, _ = _block(lines, key, inline_empty)
    _, indent = _children(lines, start, end)
    return default if indent is None else indent


def _move_agent(lines, change, raw):
    agent, group = change["agent"], change["group"]
    start, end, _ = _block(lines, "agents", "{}")
    rows, _ = _children(lines, start, end)
    at = next((i for i, name in rows if name == agent), None)
    if at is not None:
        lines[at] = _set_value(lines[at], group)
        return lines
    start, end, _ = _block(lines, "ungrouped", "[]")
    rows, _ = _children(lines, start, end)
    at = next((i for i, name in rows if name == agent), None)
    if at is None:
        raise Refused(f"{agent} is in neither `agents` nor `ungrouped`")
    del lines[at]
    if len(rows) == 1:
        _, comment = _top_value(lines, start)
        lines[start] = f"ungrouped: []{comment}{_ending(lines[start])}"
    pad = " " * _child_indent(lines, "agents", "{}")
    return _grow(lines, "agents", "{}", [f"{pad}{agent}: {group}"])


def _add_agent(lines, change, raw):
    pad = " " * _child_indent(lines, "ungrouped", "[]")
    lines = _grow(lines, "ungrouped", "[]", [f"{pad}- {change['agent']}"])
    pad = " " * _child_indent(lines, "session_agents", "[]")
    lines = _grow(lines, "session_agents", "[]", [f"{pad}- {change['agent']}"])
    pad = " " * _child_indent(lines, "about", "{}")
    return _grow(lines, "about", "{}",
                 [f"{pad}{change['agent']}: {_scalar(change['about'])}"])


def _first_grandchild_indent(lines, key: str, child: int) -> int:
    start, end, _ = _block(lines, key, "{}")
    for i in range(start + 1, end):
        if _content(lines[i]) and _indent(lines[i]) > child:
            return _indent(lines[i])
    return child + 2


def _add_group(lines, change, raw, today):
    group = change["group"]
    pad = _child_indent(lines, "kinds", "{}")
    inner = " " * _first_grandchild_indent(lines, "kinds", pad)
    lines = _grow(lines, "kinds", "{}", [
        "",
        f"{' ' * pad}{group}:",
        f"{inner}ladder: {group}",
        f"{inner}label: {_scalar(change['label'])}",
        f"{inner}purpose: {_scalar(change['purpose'])}",
    ])
    pad = _child_indent(lines, "ladders", "{}")
    dash = " " * _first_grandchild_indent(lines, "ladders", pad)
    return _grow(lines, "ladders", "{}", [
        "",
        f"{' ' * pad}{group}:",
        f"{dash}- model: {change['model']}",
        f"{dash}  reason: {_scalar(_rung_reason(change, raw, today))}",
    ])


def _set_default_model(lines, change, raw, today):
    ladder = _ladder_name(raw, change["group"])
    start, end, _ = _block(lines, "ladders", "{}")
    rows, _ = _children(lines, start, end)
    keys = [i for i, _ in rows]
    at = next((i for i, name in rows if name == ladder), None)
    if at is None:
        raise Refused(f"the {ladder} ladder is not written as a block")
    limit = next((i for i in keys if i > at), end)
    starts = [i for i in range(at + 1, limit) if _RUNG.match(_body(lines[i]))]
    if not starts:
        raise Refused(f"the {ladder} ladder has no `- model:` rungs to reorder")
    dash = _indent(lines[starts[0]])

    # Each rung runs from its `- model:` line through every line indented past
    # its dash; a blank or a shallower comment before the next rung travels
    # with that next rung.
    ends = []
    for k, begin in enumerate(starts):
        stop = starts[k + 1] if k + 1 < len(starts) else limit
        last = begin
        for j in range(begin + 1, stop):
            if not _body(lines[j]).strip():
                continue
            if _indent(lines[j]) <= dash:
                break
            last = j
        ends.append(last + 1)
    chunks = [lines[starts[0]:ends[0]]] + [lines[ends[k - 1]:ends[k]]
                                           for k in range(1, len(starts))]
    models = []
    for begin in starts:
        found = re.match(r"^\s*-\s+model:\s*(['\"]?)([^'\"\s#]+)\1", _body(lines[begin]))
        models.append(found.group(2) if found else None)

    model = change["model"]
    if model in models:
        t = models.index(model)
        ordered = [chunks[t]] + [c for k, c in enumerate(chunks) if k != t]
    else:
        ending = _ending(lines[starts[0]])
        pad = " " * dash
        ordered = [[f"{pad}- model: {model}{ending}",
                    f"{pad}  reason: {_scalar(_rung_reason(change, raw, today))}{ending}"],
                   *chunks]
    return lines[:starts[0]] + [line for chunk in ordered for line in chunk] + lines[ends[-1]:]


def _project_rows(lines, project: str):
    """`(start, end, slug_at, slug_end, kind_rows)` for `projects:`; slug_at is
    None when the project has no entry."""
    start, end, empty = _block(lines, "projects", "{}")
    if empty:
        return start, end, None, None, []
    rows, slug_indent = _children(lines, start, end)
    slug_at = next((i for i, name in rows if name == project), None)
    if slug_at is None:
        return start, end, None, None, []
    slug_end = next((i for i, _ in rows if i > slug_at), end)
    last = slug_at
    kinds = []
    for i in range(slug_at + 1, slug_end):
        if _content(lines[i]) and _indent(lines[i]) > slug_indent:
            last = i
            entry = _ENTRY.match(_body(lines[i]))
            if entry:
                kinds.append((i, entry.group(3)))
    return start, end, slug_at, last + 1, kinds


def _set_project_model(lines, change, raw):
    project, group, model = change["project"], change["group"], change["model"]
    start, end, slug_at, slug_end, kinds = _project_rows(lines, project)
    at = next((i for i, name in kinds if name == group), None)
    if at is not None:
        lines[at] = _set_value(lines[at], model)
        return lines
    if slug_at is not None:
        pad = " " * (_indent(lines[kinds[0][0]]) if kinds else _indent(lines[slug_at]) + 2)
        return (lines[:slug_end] + [f"{pad}{group}: {model}{_ending(lines[slug_at])}"]
                + lines[slug_end:])
    slug = _child_indent(lines, "projects", "{}")
    return _grow(lines, "projects", "{}",
                 [f"{' ' * slug}{project}:", f"{' ' * (slug + 2)}{group}: {model}"])


def _clear_project_model(lines, change, raw):
    project, group = change["project"], change["group"]
    start, _, slug_at, _, kinds = _project_rows(lines, project)
    at = next((i for i, name in kinds if name == group), None)
    if at is None:
        raise Refused(f"{project} has no {group} exception to clear")
    del lines[at]
    if len(kinds) == 1:
        del lines[slug_at]
    start, end, _ = _block(lines, "projects", "{}")
    if not any(_content(lines[i]) for i in range(start + 1, end)):
        _, comment = _top_value(lines, start)
        lines[start] = f"projects: {{}}{comment}{_ending(lines[start])}"
    return lines


_EXCLUDED_ITEM = re.compile(r"^(\s*)-\s+(?:model:\s*)?(['\"]?)([^'\"\s#]+)\2\s*(?:#.*)?$")


def _lift_excluded(lines, model: str):
    """`model`'s `excluded` entry removed — its `- model:` line and every line
    indented past its dash. The last entry gone leaves `excluded: []`, the way
    `_clear_project_model` leaves `projects: {}`."""
    start, end, _ = _block(lines, "excluded", "[]")
    rows = [i for i in range(start + 1, end) if _content(lines[i])]
    dash = _indent(lines[rows[0]]) if rows else 0
    for i in rows:
        found = _EXCLUDED_ITEM.match(_body(lines[i]))
        if _indent(lines[i]) != dash or not found or found.group(3) != model:
            continue
        last = i
        for j in range(i + 1, end):
            if not _body(lines[j]).strip():
                continue
            if _indent(lines[j]) <= dash:
                break
            last = j
        del lines[i:last + 1]
        return _lift_excluded(lines, model)
    if not any(_content(lines[i]) for i in range(start + 1, end)):
        _, comment = _top_value(lines, start)
        lines[start] = f"excluded: []{comment}{_ending(lines[start])}"
    return lines


def _set_aside(lines, model: str, reason: str):
    """`model` appended under `excluded` as its last entry, with `reason`."""
    pad = " " * _child_indent(lines, "excluded", "[]")
    return _grow(lines, "excluded", "[]",
                 [f"{pad}- model: {model}", f"{pad}  reason: {_scalar(reason)}"])


def edit_text(text: str, change: dict, today: str) -> str:
    """`text` with one change made, as a text edit of the lines it names."""
    import yaml

    raw = yaml.safe_load(text) or {}
    lines = text.splitlines(keepends=True)
    op = change["op"]
    if _lifts(raw, change):
        lines = _lift_excluded(lines, change["model"])
    if op == "move_agent":
        lines = _move_agent(lines, change, raw)
    elif op == "add_agent":
        lines = _add_agent(lines, change, raw)
    elif op == "add_group":
        lines = _add_group(lines, change, raw, today)
    elif op == "set_default_model":
        lines = _set_default_model(lines, change, raw, today)
    elif op == "set_project_model":
        lines = _set_project_model(lines, change, raw)
    elif op == "clear_project_model":
        lines = _clear_project_model(lines, change, raw)
    else:
        raise Refused(f"unknown op {op!r}")
    # Read against the file as written so far, not against `edit_data`, so
    # the comparison in `apply_change_set` still judges this half.
    left = _leaving(raw, change)
    if left is not None and left not in _named(yaml.safe_load("".join(lines))):
        lines = _set_aside(lines, left, _set_aside_reason(raw, change, today))
    return "".join(lines)


# --------------------------------------------------------------------------- #
# Judging a change                                                             #
# --------------------------------------------------------------------------- #

def registry_agents(text: str) -> set[str]:
    """Every agents.yaml entry that carries a generated model region — the
    names `sync_model_config.splice_registry` can write a model for."""
    names, agent = set(), None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- name:"):
            agent = stripped.split(":", 1)[1].strip()
        elif REGISTRY_REGION in line and agent:
            names.add(agent)
    return names


def _problems(change: dict, raw, prices, registry: set[str]) -> list[str]:
    """What stops a change before it can be made at all, in plain words."""
    op = change["op"]
    kinds = raw.get("kinds") or {}
    agents = raw.get("agents") or {}
    ungrouped = raw.get("ungrouped") or []
    session = raw.get("session_agents") or []
    retired = set(_rung_ids(raw.get("retired")))
    problems = []

    group = change.get("group")
    if op == "add_group":
        # One plain sentence for each name the console may not use (DRE-6834).
        if not group:
            problems.append("a new group needs a name")
        elif not ADDED_GROUP_NAME.fullmatch(group):
            problems.append(f"a group's name is lowercase letters, digits and "
                            f"dashes, starting with a letter, and \"{group}\" is not")
        elif not _usable_name(group):
            problems.append(f"\"{group}\" is a word the settings read as a yes, a no "
                            "or nothing, so it cannot name a group — a group's name "
                            "is lowercase letters, digits and dashes")
        elif group in kinds or group in (raw.get("ladders") or {}):
            problems.append(f"there is already a group named {group}")
    elif group is not None and group not in kinds:
        problems.append(f"there is no group named {group} — the groups are "
                        f"{', '.join(kinds)}")
        return problems

    # Retired first (DRE-6789): a retired id may keep a price or lose it, and
    # either way it is refused as what it is.
    model = change.get("model")
    if model is not None:
        if model in retired:
            problems.append(f"{model} is retired — kept readable, never selectable")
        elif model not in prices:
            problems.append(_unpriced(model))

    if op == "move_agent":
        agent = change["agent"]
        if agent not in agents and agent not in ungrouped:
            problems.append(f"there is no agent named {agent} — add it first")
        elif agents.get(agent) == group:
            problems.append(f"{agent} is already in the {group} group, so there "
                            "is nothing to change")
        elif agent not in agents and agent not in registry and agent not in session:
            problems.append(
                f"{agent} has no entry in the agent registry (agents.yaml) and "
                "is not in `session_agents`, so nothing runs it — an agent joins "
                "a group once it is defined there or listed as a session agent")
    elif op == "add_agent":
        agent = change["agent"]
        if (agent in agents or agent in ungrouped or agent in session
                or agent in (raw.get("about") or {})):
            problems.append(f"there is already an agent named {agent}")
    elif op == "set_default_model":
        ladder = (raw.get("ladders") or {}).get(_ladder_name(raw, group))
        if _rung_ids(ladder)[:1] == [model]:
            problems.append(f"{model} is already the {group} group's default "
                            "model")
    elif op in ("set_project_model", "clear_project_model"):
        projects = raw.get("projects") or {}
        entry = projects.get(change["project"]) if isinstance(projects, Mapping) else None
        current = entry.get(group) if isinstance(entry, Mapping) else None
        if op == "set_project_model" and current == model:
            problems.append(f"{change['project']}'s {group} group already runs "
                            f"{model} first")
        if op == "clear_project_model" and current is None:
            problems.append(f"{change['project']} has no {group} exception to "
                            "clear")
    return problems


_STANDING = "config/models.yaml already fails validation before any change"
_UNEDITABLE = "config/models.yaml could not be edited for it"
_UNINTENDED = "the edit to config/models.yaml did not produce the intended change"


def _cannot_run(model: str, floors, installed) -> tuple[list[str], dict, str | None]:
    """`(reasons, floors, installed)`: the generator's one sentence when the
    pinned Claude Code is older than `model`'s floor (DRE-6789), with the
    floors and the pinned version read off this tree the way the generator
    reads them when not given."""
    from sync_model_config import _cannot_run as cannot_run
    from sync_model_config import _claude_code_floors

    try:
        floors, installed = _claude_code_floors(floors, installed)
    except SystemExit as exc:
        raise Refused(str(exc)) from exc
    return cannot_run(model, floors, installed), floors, installed


def apply_change_set(text: str, change_set, *, today: str, prices=None,
                     repo_slugs=None, registry: set[str] | None = None,
                     floors: dict[str, str] | None = None,
                     installed: str | None = None):
    """`(text, refusals)`. With no refusal, `text` is the file with every change
    made; with any, it is the file unchanged and `refusals` names each refused
    change — `{"index", "change", "reasons"}` — and only those.

    `floors` (model id → the oldest Claude Code that runs it) and `installed`
    (the pinned version) are read off this tree when not given."""
    import yaml

    change_set = check_change_set(change_set)
    prices = declared_prices() if prices is None else prices
    if registry is None:
        registry = registry_agents(REGISTRY_PATH.read_text())

    state_text = text
    state = yaml.safe_load(text) or {}
    standing = set(policy_errors(state, prices, repo_slugs))
    if standing:
        return text, [{"index": 0, "change": None, "reasons": [
            f"{_STANDING}: {e}" for e in sorted(standing)]}]

    refusals = []
    for n, change in enumerate(change_set["changes"], 1):
        reasons = _problems(change, state, prices, registry)
        if not reasons and change["op"] in MODEL_OPS:
            reasons, floors, installed = _cannot_run(change["model"], floors, installed)
        if not reasons:
            try:
                after = edit_text(state_text, change, today)
                parsed = yaml.safe_load(after) or {}
            except (Refused, yaml.YAMLError) as exc:
                reasons = [f"{_UNEDITABLE} ({exc})"]
            else:
                if parsed != edit_data(state, change, today):
                    reasons = [f"{_UNINTENDED}, so nothing was written"]
                else:
                    reasons = [e for e in policy_errors(parsed, prices, repo_slugs)
                               if e not in standing]
        if reasons:
            refusals.append({"index": n, "change": change, "reasons": reasons})
        else:
            state_text, state = after, parsed
    if refusals:
        return text, refusals
    return state_text, []


# --------------------------------------------------------------------------- #
# Plain words for a refusal (DRE-6833)                                         #
# --------------------------------------------------------------------------- #

def _labels(config) -> str:
    """Every group by its label: `Builders, Reviewers and Planners`."""
    labels = [_label(config, kind) for kind in ((config or {}).get("kinds") or {})]
    if len(labels) < 2:
        return "".join(labels)
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def _why(reason: str, change: dict, config) -> str:
    """Why `change` was refused, in words for the CEO, from one finding —
    `_problems`' line, `policy_errors`' line or the apply step's own."""
    found = re.match(r"there is no group named (\S+) — ", reason)
    if found:
        return (f"there is no group called {found.group(1)} — the groups are "
                f"{_labels(config)}")
    found = re.match(r"(\S+) is already in the (\S+) group", reason)
    if found:
        return (f"{found.group(1)} is already in the "
                f"{_label(config, found.group(2))} group, so there is nothing "
                "to change")
    if reason.startswith(("there is no agent named ",
                          "there is already an agent named ")):
        return reason
    found = re.match(r"there is already a group named (\S+)$", reason)
    if found:
        taken = found.group(1)
        if taken in ((config or {}).get("kinds") or {}):
            return (f"the name {taken} is already taken by the "
                    f"{_label(config, taken)} group")
        return f"the name {taken} is already taken"
    found = re.match(r"(\S+) has no entry in the agent registry", reason)
    if found:
        return (f"{found.group(1)} is neither an agent the pipeline runs nor "
                "one that works in a person's session, so a group would mean "
                "nothing for it — an engineer has to set it up first")
    # Rules 3 and 8 say one thing about a reviewer, so they render as one line.
    found = (re.match(rf"agents\.(\S+): must be '{ADVISORY_KIND}'", reason)
             or re.match(rf"review_separation\.roles: '([^']+)' is not an "
                         rf"{ADVISORY_KIND} role", reason))
    if found:
        return (f"{found.group(1)} must stay in the "
                f"{_label(config, ADVISORY_KIND)} group — it checks work before "
                "it merges, and no person reads the change")
    found = re.match(rf"agents\.(\S+): '{JUDGEMENT_KIND}' is the ", reason)
    if found:
        owners = " and ".join(f"the {role}" for role in JUDGEMENT_ROLES)
        return (f"only {owners} may be in the {_label(config, JUDGEMENT_KIND)} "
                f"group — {owners} runs once per plan, while {found.group(1)} "
                "runs far more often, which would put many more runs on the most "
                "expensive model")
    found = re.match(r"session_agents\.(\S+): names no agent", reason)
    if found:
        return (f"{found.group(1)} is listed as an agent that works in a "
                "person's session, but there is no agent by that name")
    found = re.match(r"ungrouped\.(\S+): is also in", reason)
    if found:
        return (f"{found.group(1)} would be in a group and in no group at the "
                "same time")
    if reason.startswith(_UNEDITABLE):
        return ("the settings file is laid out in a way the console cannot edit "
                "safely, so an engineer has to make this change by hand")
    if reason.startswith(_UNINTENDED):
        return ("the edit did not come out as asked, so an engineer has to make "
                "this change by hand")
    if change["op"] in ("move_agent", "add_agent") or (
            change["op"] == "add_group" and _FINDING.match(reason)):
        return ("it would break one of the checks every change to the model "
                "settings must pass — the run log names it for an engineer")
    return reason


#: A validator finding — `kinds.<x>: …`, `ladders.<x>: …`, `retired: …` —
#: rather than a sentence this script wrote for the CEO.
_FINDING = re.compile(r"[a-z_]+(?:\.[^\s:]+)*: ")


def plain_lines(change: dict | None, reasons: list[str], config, *,
                index: int = 1, total: int = 1) -> list[str]:
    """One plain sentence per distinct reason `change` was refused: the change
    in `describe`'s words (with its place in the set when the set has more
    than one), then why. `change` None is the file that already fails before
    any change. The generator publishes these, joined, as a refused move's
    `reason` in config/model-choices.json."""
    if change is None:
        return ["Nothing was written: the model settings already have a problem "
                "that has nothing to do with this change, and an engineer has to "
                "fix it first."]
    head = describe(change, config).rstrip(".")
    if total > 1:
        head = f"{head} (change {index} of {total})"
    lines: list[str] = []
    for reason in reasons:
        line = f"{head} — refused, because {_why(reason, change, config).rstrip('.')}."
        if line not in lines:
            lines.append(line)
    return lines


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _options(argv, allowed, flags=()) -> tuple[list[str], dict]:
    positional, opts = [], {}
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg in flags:
            opts[arg] = True
        elif arg in allowed:
            if not rest:
                raise Refused(f"{arg} needs a value")
            opts[arg] = rest.pop(0)
        elif arg.startswith("--"):
            raise Refused(f"unknown option {arg!r}")
        else:
            positional.append(arg)
    if "--changes" not in opts:
        raise Refused("needs --changes <change-set.json>")
    return positional, opts


def _summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as fh:
            fh.write("\n".join(lines) + "\n")


def _short(change: dict) -> str:
    return " ".join([change["op"]] + [change[f] for f in OPS[change["op"]]
                                      if f not in _TEXT_FIELDS])


def _cmd_apply(argv) -> int:
    try:
        _, opts = _options(argv, ("--changes", "--config", "--registry", "--today"),
                           ("--check",))
        change_set = load_change_set(opts["--changes"])
        config_path = Path(opts.get("--config") or CONFIG_PATH)
        text = config_path.read_text()
        registry = registry_agents(Path(opts.get("--registry") or REGISTRY_PATH).read_text())
        today = opts.get("--today") or datetime.now(timezone.utc).date().isoformat()
        result, refusals = apply_change_set(text, change_set, today=today,
                                            registry=registry)
    except (OSError, ValueError) as exc:
        for line in str(exc).splitlines():
            print(f"::error::change set refused: {line}")
        _summary(["### Model config change refused", "", str(exc)])
        return 2

    total = len(change_set["changes"])
    if refusals:
        import yaml

        # Plain words for the CEO on every `::error::` line and summary bullet
        # (DRE-6833); the findings themselves are for an engineer, so they go
        # to the log as ordinary output and to a block below the bullets.
        config = yaml.safe_load(text) or {}
        print(f"::error::the change set from {change_set['requested_by']} was "
              "refused — nothing was written, and no branch or pull request is "
              "opened")
        report = ["### Model config change refused", "",
                  f"Requested by {change_set['requested_by']}. Nothing was "
                  "written, and no branch or pull request is opened.", ""]
        details = []
        for refusal in refusals:
            where = (f"change {refusal['index']} of {total} "
                     f"({_short(refusal['change'])})" if refusal["change"]
                     else "the change set")
            details += [f"{where}: {reason}" for reason in refusal["reasons"]]
            for line in plain_lines(refusal["change"], refusal["reasons"], config,
                                    index=refusal["index"], total=total):
                print(f"::error::{line}")
                report.append(f"- {line}")
        print("The findings, for an engineer:")
        for line in details:
            print(f"  {line}")
        report += ["", "The findings, for an engineer:", "", "```", *details, "```"]
        _summary(report)
        return 2

    if opts.get("--check"):
        import yaml

        print("".join(difflib.unified_diff(
            text.splitlines(keepends=True), result.splitlines(keepends=True),
            fromfile=str(config_path), tofile=str(config_path))), end="")
        _summary(["### Model config change", "",
                  f"Requested by {change_set['requested_by']}. Every change "
                  "passed validation:", ""]
                 + [f"- {describe(c, yaml.safe_load(text))}"
                    for c in change_set["changes"]])
        return 0
    config_path.write_text(result)
    print(f"applied: {total} change(s) from {change_set['requested_by']} to "
          f"{config_path}")
    return 0


def _cmd_render(argv) -> int:
    try:
        positional, opts = _options(argv, ("--changes", "--config"))
        if len(positional) != 1 or positional[0] not in RENDER_TARGETS:
            raise Refused(f"render needs one target of {list(RENDER_TARGETS)}")
        change_set = load_change_set(opts["--changes"])
        if positional[0] == "pr-title":
            text = pr_title(change_set)
        else:
            import yaml

            config = yaml.safe_load(Path(opts.get("--config") or CONFIG_PATH).read_text())
            text = pr_body(change_set, config or {})
    except (OSError, ValueError) as exc:
        print(f"::error::render refused: {exc}", file=sys.stderr)
        return 2
    print(text.rstrip("\n"))
    return 0


def _cmd_branch(argv) -> int:
    try:
        _, opts = _options(argv, ("--changes",))
        change_set = load_change_set(opts["--changes"])
    except (OSError, ValueError) as exc:
        for line in str(exc).splitlines():
            print(f"::error::change set refused: {line}", file=sys.stderr)
        return 2
    import github_output

    sys.stdout.write(github_output.render([("branch", branch_name(change_set)),
                                           ("digest", digest(change_set))]))
    return 0


def main(argv: list[str]) -> int:
    """Exit 0 on success, 2 on a refusal or a malformed call."""
    if not argv or argv[0] not in COMMANDS:
        print(f"usage: model_config_change.py {{{'|'.join(COMMANDS)}}} …",
              file=sys.stderr)
        return 2
    cmd, *rest = argv
    if cmd == "apply":
        return _cmd_apply(rest)
    if cmd == "render":
        return _cmd_render(rest)
    return _cmd_branch(rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
