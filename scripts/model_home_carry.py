#!/usr/bin/env python3
"""A merged change to config/models.yaml's choices, carried into the home (DRE-6986).

Until the console writes the one home for model choices itself (DRE-6976), the
Settings › Agents & models page saves by today's route: `model-config-change.yml`
opens a pull request editing `config/models.yaml`, and the merge puts the change
in force. Once the operator seeds the home (DRE-6945) every run reads the home
and not that file, so a save merged after the seeding would be in force
nowhere. `.github/workflows/model-home-carry.yml` runs this on every push to
`main` that touches the file, and it carries the change into the home within
minutes. It is a bridge, removed with the route it serves (DRE-6981).

WHAT IS CARRIED, AND ONLY THAT. The choices at the push's `before` commit are
compared with those at its `after` commit, and the difference is turned into
the six changes the page makes (`model_config_change.OPS`), applied to a fresh
read of the home:

    set_default_model   a group's first rung changed: that model moves to the
                        front of `groups.<key>.models`, inserted if absent, the
                        other rungs in their order. Only the first rung is
                        carried, so a rung the daily adoption replaced in the
                        home (DRE-6943) is never put back
    move_agent          `agents.<name>` set to the new group, or removed when
                        the agent left every group
    add_group           `groups.<key>` with its label, purpose and the file's
                        ladder, when the home has no such group
    set_project_model   `projects.<slug>.<group>` set
    clear_project_model `projects.<slug>.<group>` removed, and an emptied
                        project with it
    add_agent           nothing: `session_agents` lists it, and that is a
                        code-side fact the home's rules read from the file

Any other change — a ladder changed below its first rung, a group removed, a
label or purpose edited, a whole choice key (`CHOICE_KEYS`) removed — is not
carried, and the run names it in one plain line and ends green. So the card
that takes the choices out of the file (DRE-6983) cannot clear the home
through this.

THE WRITE, GUARDED. The new document is held to `model_home.home_errors`; one
the rules refuse is not written, and the run fails red naming the rule.
Immediately before writing the home is read again: if its `written_at` moved
(the daily adoption wrote meanwhile) the changes are applied once more to the
newer document, and if it moved again nothing is written and the run fails
red naming both values. The write is one `PATCH` with the token in
`BUREAU_MODEL_HOME_WRITE_TOKEN` (`variables: write`), `written_at` now and
`written_by` `console` when the merged pull request's head branch is the old
route's (`agent/model-config-`, read through the commit's pulls with `gh`),
`operator` otherwise. A home holding no document yet (404, before the seeding)
is not an error: nothing is carried, and the change is in the defaults in the
code, which the seeding renders.

THE LINE. Every run prints one line tagged `model-home-carry:` (`TAG`,
declared in `config/proof-reach.json`):

    model-home-carry: carried <n> setting(s) from <short sha> — written <written_at> by <written_by>
    <setting>: <before> → <after>                        one per setting
    model-home-carry: nothing carried — <reason>
    model-home-carry: not written — <reason>             and exit 1

    python3 scripts/model_home_carry.py carry --before <sha> --after <sha>
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import model_home as mh  # noqa: E402 — after the path insert, by design
from model_config_change import BRANCH_PREFIX  # noqa: E402

# The tag of the one line every run prints (config/proof-reach.json).
TAG = "model-home-carry:"

# Every invocation reads the home (DRE-6939's contract, DRE-6940's check).
READS_MODEL_HOME = ("*",)

READ_TOKEN_ENV = mh.TOKEN_ENV
WRITE_TOKEN_ENV = "BUREAU_MODEL_HOME_WRITE_TOKEN"
CONFIG = "config/models.yaml"
# The old route's branch: a merge from it is the console's save.
CONSOLE_BRANCH = BRANCH_PREFIX
PULLS_PATH = f"repos/{mh.REPOSITORY}/commits/{{sha}}/pulls"

# The keys of config/models.yaml that are choices. One removed whole is never
# carried, so taking the choices out of the file cannot clear the home.
CHOICE_KEYS = ("ladders", "kinds", "agents", "ungrouped", "projects")

TIMEOUT_SECONDS = 20
NO_COMMIT = "0" * 40
NONE = "(none)"


class Failed(Exception):
    """A run that cannot write; its text is the reason. Exit 1."""


# --------------------------------------------------------------------------- #
# The file's choices, and what changed between two commits                    #
# --------------------------------------------------------------------------- #

def _ids(rungs) -> list[str]:
    if not isinstance(rungs, list):
        return []
    return [str(r.get("model") if isinstance(r, Mapping) else r) for r in rungs
            if (r.get("model") if isinstance(r, Mapping) else r)]


def _map(value) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def groups_of(raw: Mapping) -> dict:
    """{kind: {"label", "purpose", "models"}} — each group of `kinds` with its
    ladder's rung ids."""
    ladders = _map(raw.get("ladders"))
    found = {}
    for key, spec in _map(raw.get("kinds")).items():
        spec = spec if isinstance(spec, Mapping) else {"ladder": spec}
        found[str(key)] = {"label": spec.get("label"), "purpose": spec.get("purpose"),
                           "models": _ids(ladders.get(spec.get("ladder")))}
    return found


def _moved(models: list[str], model: str) -> list[str]:
    return [model] + [m for m in models if m != model]


def compare(before: Mapping, after: Mapping) -> tuple[list[dict], list[str], list[str]]:
    """`(changes, not_carried, nothing_to_carry)` between two parsed files:
    the changes in the page's terms, one plain sentence per change that is not
    carried, and one per change there is nothing to carry for."""
    changes: list[dict] = []
    refused: list[str] = []
    noted: list[str] = []
    gone = [k for k in CHOICE_KEYS if k in before and k not in after]
    for key in gone:
        refused.append(f"the whole `{key}` key was removed from {CONFIG}, and a "
                       "removal is never carried into the home")

    if not {"kinds", "ladders"} & set(gone):
        old, new = groups_of(before), groups_of(after)
        for key in old:
            if key not in new:
                refused.append(f"the group {key} was removed from `kinds`, and a "
                               "group is never removed from the home by a merge")
        for key, group in new.items():
            if key not in old:
                changes.append({"op": "add_group", "group": key, "label": group["label"],
                                "purpose": group["purpose"], "models": group["models"]})
                continue
            was, now = old[key]["models"], group["models"]
            if not now:
                refused.append(f"the {key} ladder was left with no model")
                continue
            if was[:1] != now[:1]:
                changes.append({"op": "set_default_model", "group": key, "model": now[0]})
            if now != (_moved(was, now[0]) if was[:1] != now[:1] else was):
                refused.append(f"the {key} ladder changed below its first rung — the "
                               "home's other rungs are the daily adoption's to change")
            for field in ("label", "purpose"):
                if old[key][field] != group[field]:
                    refused.append(f"the {key} group's {field} changed, which the page "
                                   "never changes")

    if "agents" not in gone:
        was, now = _map(before.get("agents")), _map(after.get("agents"))
        for name in list(now) + [n for n in was if n not in now]:
            if was.get(name) != now.get(name):
                changes.append({"op": "move_agent", "agent": str(name),
                                "group": None if now.get(name) is None else str(now[name])})

    if "ungrouped" not in gone:
        agents = _map(after.get("agents"))
        was = before.get("ungrouped") if isinstance(before.get("ungrouped"), list) else []
        now = after.get("ungrouped") if isinstance(after.get("ungrouped"), list) else []
        for name in now:
            if name not in was:
                noted.append(f"{name} was added as an agent, which the file's "
                             "session_agents lists and the home's rules read from it")
        for name in was:
            if name not in now and name not in agents:
                refused.append(f"{name} left `ungrouped` without joining a group")

    if "projects" not in gone:
        was, now = _map(before.get("projects")), _map(after.get("projects"))
        for slug in list(now) + [s for s in was if s not in now]:
            old_entry, new_entry = _map(was.get(slug)), _map(now.get(slug))
            for group in list(new_entry) + [g for g in old_entry if g not in new_entry]:
                if old_entry.get(group) == new_entry.get(group):
                    continue
                if new_entry.get(group) is None:
                    changes.append({"op": "clear_project_model", "project": str(slug),
                                    "group": str(group)})
                else:
                    changes.append({"op": "set_project_model", "project": str(slug),
                                    "group": str(group), "model": str(new_entry[group])})
    return changes, refused, noted


# --------------------------------------------------------------------------- #
# The changes, applied to a home document                                     #
# --------------------------------------------------------------------------- #

def _shown(value) -> str:
    if value is None:
        return NONE
    if isinstance(value, list):
        return mh.one_line(", ".join(str(v) for v in value))
    return mh.one_line(value)


def _key(part) -> str:
    return mh.one_line(part).replace(" ", "?")


def apply(doc: Mapping, changes: list[dict]) -> tuple[dict, list[str], list[str]]:
    """`(document, settings, not_carried)`: a copy of `doc` with every change
    made, one `<setting>: <before> → <after>` line per setting it changed (a
    change the home already holds is no line), and one sentence per change
    the home cannot take."""
    new = copy.deepcopy(dict(doc))
    groups = new.setdefault("groups", {})
    agents = new.setdefault("agents", {})
    projects = new.setdefault("projects", {})
    settings: list[str] = []
    refused: list[str] = []

    def setting(name, was, now):
        """`name` is built from parts each passed through `_key`."""
        if was != now:
            settings.append(f"{name}: {_shown(was)} → {_shown(now)}")

    for change in changes:
        op = change["op"]
        if op == "set_default_model":
            key, model = change["group"], change["model"]
            group = groups.get(key)
            if not isinstance(group, dict) or not isinstance(group.get("models"), list):
                refused.append(f"the home has no {key} group to set {model} first in")
                continue
            was = group["models"][0] if group["models"] else None
            group["models"] = _moved(group["models"], model)
            setting(f"groups.{_key(key)}.models[0]", was, model)
        elif op == "move_agent":
            name, key = change["agent"], change["group"]
            was = agents.get(name)
            if key is None:
                agents.pop(name, None)
            else:
                agents[name] = key
            setting(f"agents.{_key(name)}", was, key)
        elif op == "add_group":
            key = change["group"]
            if key in groups:
                continue
            groups[key] = {"label": change["label"], "purpose": change["purpose"],
                           "models": list(change["models"])}
            setting(f"groups.{_key(key)}", None, change["models"])
        elif op == "set_project_model":
            slug, key, model = change["project"], change["group"], change["model"]
            entry = projects.setdefault(slug, {})
            was = entry.get(key)
            entry[key] = model
            setting(f"projects.{_key(slug)}.{_key(key)}", was, model)
        else:
            slug, key = change["project"], change["group"]
            entry = projects.get(slug)
            was = entry.pop(key, None) if isinstance(entry, dict) else None
            if isinstance(entry, dict) and not entry:
                projects.pop(slug, None)
            setting(f"projects.{_key(slug)}.{_key(key)}", was, None)
    return new, settings, refused


# --------------------------------------------------------------------------- #
# GitHub — the home's variable, and the merged pull request                   #
# --------------------------------------------------------------------------- #

def _http(method: str, url: str, token: str, body: str | None = None) -> tuple[int, str]:
    """`(status, body)` of one request. An HTTP error status is an answer;
    a network failure raises."""
    import urllib.error
    import urllib.request

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "bureau-pipeline-model-home-carry",
    }
    data = None
    if body is not None:
        data = body.encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read().decode("utf-8", "replace")
        except Exception:
            text = ""
        return exc.code, text


def _token(name: str) -> str:
    token = os.environ.get(name, "")
    if not token:
        raise Failed(f"{name} is empty, so its token mint failed")
    return token


def read_home() -> dict | None:
    """The home's document, or None when the variable does not exist yet."""
    try:
        status, body = _http("GET", mh.API_URL, _token(READ_TOKEN_ENV))
    except Failed:
        raise
    except Exception as exc:
        raise Failed(f"the GitHub API could not be reached ({exc})")
    if status == 404:
        return None
    if status != 200:
        raise Failed(f"the GitHub API answered HTTP {status} for {mh.VARIABLE} ({body})")
    try:
        doc = json.loads(json.loads(body)["value"])
    except (ValueError, KeyError, TypeError) as exc:
        raise Failed(f"{mh.VARIABLE} does not hold a JSON document ({exc})")
    if not isinstance(doc, dict):
        raise Failed(f"{mh.VARIABLE} does not hold a JSON object")
    return doc


def write_home(doc: Mapping) -> None:
    body = json.dumps({"name": mh.VARIABLE, "value": json.dumps(doc)})
    try:
        status, text = _http("PATCH", mh.API_URL, _token(WRITE_TOKEN_ENV), body)
    except Failed:
        raise
    except Exception as exc:
        raise Failed(f"the GitHub API could not be reached to write ({exc})")
    if status not in (200, 204):
        raise Failed(f"the GitHub API answered HTTP {status} to the write ({text})")


def _pulls(sha: str) -> list:
    """The pull requests the commit belongs to, through `gh api`."""
    done = subprocess.run(["gh", "api", PULLS_PATH.format(sha=sha)],
                          capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f"gh exited {done.returncode}")
    found = json.loads(done.stdout or "[]")
    return found if isinstance(found, list) else []


def written_by(sha: str) -> str:
    """`console` when the commit came in from the old route's branch, else
    `operator` — a role word, never a person."""
    try:
        pulls = _pulls(sha)
    except Exception as exc:
        print(f"the pull request behind {sha[:7]} could not be read "
              f"({mh.one_line(exc)}); written by operator")
        return "operator"
    for pull in pulls:
        head = pull.get("head") if isinstance(pull, Mapping) else None
        ref = head.get("ref") if isinstance(head, Mapping) else None
        if isinstance(ref, str) and ref.startswith(CONSOLE_BRANCH):
            return "console"
    return "operator"


# --------------------------------------------------------------------------- #
# The carry                                                                    #
# --------------------------------------------------------------------------- #

def _facts():
    """`(prices, code_side, repo_slugs)` of this checkout — the `after`
    commit's, the one the workflow checks out."""
    import model_fallback as mf

    return mf.declared_prices(), mh.code_side(), mf.declared_repo_slugs()


def _parse(text: str | None, where: str) -> dict:
    import yaml

    if text is None:
        return {}
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise Failed(f"{CONFIG} at {where} is not YAML ({mh.one_line(exc)})")
    return raw if isinstance(raw, dict) else {}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _judged(doc: Mapping, changes: list[dict], facts) -> tuple[dict, list[str], list[str]]:
    """`apply`, and the result held to every rule a writer checks."""
    new, settings, refused = apply(doc, changes)
    if settings:
        errors = mh.home_errors(json.dumps(new), *facts)
        if errors:
            raise Failed("the home's rules refuse the carried document: "
                         + " ".join(errors))
    return new, settings, refused


def _carry(before_text, after_text, after_sha) -> tuple[str, list[str]]:
    """`(tagged line, plain lines)`, or Failed."""
    before, after = _parse(before_text, "before"), _parse(after_text, after_sha[:7])
    changes, refused, noted = compare(before, after)
    lines = [f"not carried: {r}" for r in refused] + [f"nothing to carry: {n}" for n in noted]
    if not changes:
        reason = ("the merge changed no setting the page makes"
                  if refused else
                  noted[0] if noted else
                  f"the merge changed none of the choices in {CONFIG}")
        return f"{TAG} nothing carried — {mh.one_line(reason)}", lines

    first = read_home()
    if first is None:
        return (f"{TAG} nothing carried — the home holds no document yet, so the "
                "change is in the defaults in the code, which the seeding renders"), lines
    facts = _facts()
    new, settings, missing = _judged(first, changes, facts)
    lines += [f"not carried: {r}" for r in missing]
    if not settings:
        return f"{TAG} nothing carried — the home already holds every setting the merge changed", lines

    second = read_home()
    if second is None:
        raise Failed(f"{mh.VARIABLE} was deleted between the reads")
    if second.get("written_at") != first.get("written_at"):
        print(f"the home was written at {mh.one_line(second.get('written_at'))} since "
              "the first read — the changes are applied to that document")
        new, settings, missing = _judged(second, changes, facts)
        lines = [f"not carried: {r}" for r in refused + missing] + \
            [f"nothing to carry: {n}" for n in noted]
        if not settings:
            return f"{TAG} nothing carried — the home already holds every setting the merge changed", lines
        third = read_home()
        if third is None or third.get("written_at") != second.get("written_at"):
            raise Failed(
                f"the home was written twice while this run carried — at "
                f"{mh.one_line(second.get('written_at'))} and again at "
                f"{mh.one_line((third or {}).get('written_at'))} — so nothing was "
                "written; re-run this run to carry the change")

    new["written_at"] = _now()
    new["written_by"] = written_by(after_sha)
    errors = mh.home_errors(json.dumps(new), *facts)
    if errors:
        raise Failed("the home's rules refuse the carried document: " + " ".join(errors))
    write_home(new)
    return (f"{TAG} carried {len(settings)} setting(s) from {mh.one_line(after_sha[:7])} — "
            f"written {new['written_at']} by {new['written_by']}"), settings + lines


def carry(before_text: str | None, after_text: str | None, after_sha: str) -> int:
    """Carry the change between the two texts of config/models.yaml into the
    home, print the tagged line and its plain lines, and return the exit
    code: 0 written or nothing to write, 1 not written."""
    try:
        tagged, lines = _carry(before_text, after_text, after_sha)
    except Failed as exc:
        reason = str(exc)
        print(f"{TAG} not written — {reason}")
        print(f"::error::model-home-carry: {mh.one_line(reason)}")
        return 1
    print(tagged)
    for line in lines:
        print(line)
    return 0


def _show(sha: str) -> str | None:
    """config/models.yaml at `sha`, or None when the commit has no such file.
    A commit this checkout does not hold is Failed."""
    known = subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=ROOT,
                           capture_output=True, text=True)
    if known.returncode != 0:
        raise Failed(f"the commit {mh.one_line(sha)} is not in this checkout")
    done = subprocess.run(["git", "show", f"{sha}:{CONFIG}"], cwd=ROOT,
                          capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else None


def main(argv: list[str]) -> int:
    args = dict(zip(argv[1::2], argv[2::2]))
    if (not argv or argv[0] != "carry" or len(argv) != 5
            or set(args) != {"--before", "--after"} or not all(args.values())):
        print("usage: model_home_carry.py carry --before <sha> --after <sha>",
              file=sys.stderr)
        return 2
    before, after = args["--before"], args["--after"]
    if set(before) == {"0"}:
        print(f"{TAG} nothing carried — the push has no before commit to compare with")
        return 0
    try:
        before_text, after_text = _show(before), _show(after)
    except Failed as exc:
        print(f"{TAG} not written — {exc}")
        print(f"::error::model-home-carry: {mh.one_line(exc)}")
        return 1
    if before_text is None:
        print(f"{TAG} nothing carried — {CONFIG} did not exist at {mh.one_line(before[:7])}")
        return 0
    return carry(before_text, after_text, after)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
