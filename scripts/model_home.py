#!/usr/bin/env python3
"""The one home for model choices — its reader and its rules (DRE-6939, stdlib + PyYAML).

The CEO, 2026-10-11, on Settings › Agents & models: "These should all be
externalized and all we're doing is changing the values of the
configuration." Until this card each choice — which models a group runs on and
in what order, which group an agent is in, a project's own model — was a line
in `config/models.yaml`, read from the pipeline checkout each run makes, so a
change rode a pull request, a review, a test run and a release before it was in
force.

THE HOME. The GitHub Actions repository variable `BUREAU_MODEL_CHOICES` on
dreadnought-foundry/bureau-pipeline: one variable for the whole fleet, written
by the console through the bureau App (`PATCH` the variable's path with
`{"value": "<document>"}`), read here by one `GET` of the same path with the
installation token in `BUREAU_MODEL_HOME_TOKEN` (`variables: read`). The
document is `.value`: JSON under 48 KB —

    {"version": 1, "written_at": "2026-10-11T18:03:12Z", "written_by": "console",
     "groups": {"workhorse": {"label": …, "purpose": …, "models": [ids, best first]},
                "advisory": {…}, "judgement": {…}},
     "agents": {"engineer": "workhorse", …},
     "added_agents": {"<name>": "<one line>"},
     "projects": {"<repo-map slug>": {"<group>": "<model id>"}}}

`written_by` is a role word — `console`, `daily-adoption` or `operator` — and
no person's name, sign-in or email is ever in the document.
`BUREAU_MODEL_HOME_FILE` names a local copy, read instead of the API (tests,
launchers). A name counts as set when it is in the environment, even empty —
an empty token is a mint that failed, and is unreadable. With neither name in
the environment nobody asked for a home: the read is `not set`, nothing is
fetched or opened, and the selector runs on the defaults in the code exactly
as it did before the home.

THE RULES, IN TWO TIERS. A reader's facts are read from its OWN pipeline
checkout, and a product repository rides the `stable` channel, minutes or more
behind `main`, where every writer judges the document. So a reader refuses the
whole document only for a WHOLE-DOCUMENT rule (`structure_errors`), and sets
aside just the entries its release cannot judge (`unknown_entries`): a model it
holds no price for, or holds `excluded` or `retired`; an agent it does not
know; a project slug not in its repo map. The rest of the home is in force, and
a set-aside rung is never selected, so nothing the run's release bars reaches
its path. If setting aside leaves any of the three groups with no model, the
document is unreadable. `home_errors` is both tiers as refusals: what every
WRITER checks, on `main`, so a written document breaks no rule of either.

THE LINE. Every read prints one line to stderr tagged `model-home:` (`TAG`) —
read, unreadable with its reason, or not set — and `config/proof-reach.json`
declares it as a line a proof run may quote. Every reason, and every value
from the document printed on it, passes through `one_line` first: the note
that carries the reason rides a `$GITHUB_OUTPUT` line and a double-quoted
shell string, so a newline, a quote, a backtick or a `$` must never reach it.

    python3 scripts/model_home.py read [--out <file>]   the status line; the
                                                        accepted document to
                                                        <file> when read; exit 0
    python3 scripts/model_home.py check <file>          both tiers; exit 1 with
                                                        one line per rule broken
    python3 scripts/model_home.py render-defaults       the defaults in the code,
                                                        as a home document
"""

from __future__ import annotations

import copy
import json
import os
import string
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import model_fallback as mf  # noqa: E402 — after the path insert, by design

# The tag of the one line every read prints (config/proof-reach.json).
TAG = "model-home:"

# The commands of this CLI whose process reads the home (DRE-6940 checks it).
READS_MODEL_HOME = ("read",)

VARIABLE = "BUREAU_MODEL_CHOICES"
REPOSITORY = "dreadnought-foundry/bureau-pipeline"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/actions/variables/{VARIABLE}"
TOKEN_ENV = "BUREAU_MODEL_HOME_TOKEN"
FILE_ENV = "BUREAU_MODEL_HOME_FILE"

# One GET, ten seconds, no retry: the selector sits on the dispatch path of
# every card, and an unreadable home costs the defaults, never a stuck run.
TIMEOUT_SECONDS = 10
MAX_BYTES = 48 * 1024
VERSION = 1
WRITERS = ("console", "daily-adoption", "operator")

READ, UNREADABLE, NOT_SET = "read", "unreadable", "not set"
_ON_DEFAULTS = "running on the defaults in the code"

# `one_line`: what a printed reason or document value may hold.
_SAFE = frozenset(string.ascii_letters + string.digits + " .,:;_/()=+@-")
LINE_LIMIT = 160


def one_line(text) -> str:
    """`text` as one safe line: each run of whitespace, newlines included,
    one space; each character other than ASCII letters, digits, space and
    `. , : ; _ / ( ) = + @ -` a `?`; cut to 160 characters."""
    collapsed = " ".join(str("" if text is None else text).split())
    return "".join(c if c in _SAFE else "?" for c in collapsed)[:LINE_LIMIT]


# --------------------------------------------------------------------------- #
# The facts a reader judges by — its own checkout's                            #
# --------------------------------------------------------------------------- #

def _registry_names(path: Path | None = None) -> list[str] | None:
    """The agent names in agents.yaml, or None when it cannot be read — then
    no agent is known, which fails closed: the backstops go with them."""
    try:
        import yaml

        raw = yaml.safe_load(Path(path or ROOT / "agents.yaml").read_text())
        return [str(entry["name"]) for entry in raw["agents"]
                if isinstance(entry, Mapping) and entry.get("name")]
    except Exception as exc:  # missing / unreadable / malformed / no PyYAML
        print(f"model_home: could not read agents.yaml ({exc}); every agent the "
              "home names will be set aside", file=sys.stderr)
        return None


def code_side() -> dict:
    """The code-side facts of this checkout: `excluded`, `retired` and
    `session_agents` from config/models.yaml (through `model_fallback`, which
    reads them and nothing else from that file), and `agents`, the names in
    agents.yaml."""
    cfg = mf.CONFIG
    session = cfg.get("session_agents")
    return {
        "excluded": list(cfg.get("excluded") or []),
        "retired": list(cfg.get("retired") or []),
        "session_agents": list(session) if isinstance(session, list) else [],
        "agents": _registry_names(),
    }


# --------------------------------------------------------------------------- #
# The rules                                                                    #
# --------------------------------------------------------------------------- #

def _parse(doc):
    """`(document, refusal)`: a document given as text is held to the size
    limit and parsed; a mapping is taken as it is."""
    if isinstance(doc, (str, bytes)):
        text = doc.decode("utf-8", "replace") if isinstance(doc, bytes) else doc
        size = len(text.encode("utf-8"))
        if size >= MAX_BYTES:
            return None, (f"The document is {size} bytes, and a home is under "
                          "48 KB.")
        try:
            doc = json.loads(text)
        except ValueError as exc:
            return None, f"The document is not JSON ({exc})."
    if not isinstance(doc, Mapping):
        return None, "The document is not a JSON object."
    return doc, None


def _is_text(value) -> bool:
    return isinstance(value, str)


def structure_errors(doc, code_side: Mapping) -> list[str]:
    """The whole-document rules, each broken one a plain sentence. They do not
    depend on how new the reader's checkout is, so a reader treats a document
    breaking any of them as unreadable: the defaults, loudly.

    The value parses as JSON with `version: 1`; `workhorse`, `advisory` and
    `judgement` are present, each with at least one model; `critic` and
    `verifier` are in `advisory`; `judgement` holds only `planner` and session
    agents. Around them, the shapes the contract states: text where it says
    text, maps where it says maps, a role word in `written_by`, and every
    agent in a group the document declares.
    """
    doc, refusal = _parse(doc)
    if refusal:
        return [refusal]
    errors: list[str] = []
    version = doc.get("version")
    if version != VERSION or isinstance(version, bool):
        errors.append(f"The document says version {json.dumps(version)}, and this "
                      f"release reads version {VERSION}.")
    if doc.get("written_by") not in WRITERS:
        errors.append("written_by must be one of " + ", ".join(WRITERS) + ", a role "
                      "word: no person is ever named in the home.")
    if not _is_text(doc.get("written_at")):
        errors.append("written_at must be the time the document was written, as text.")

    groups = doc.get("groups")
    if not isinstance(groups, Mapping):
        errors.append("groups must map each group key to its label, purpose and models.")
        groups = {}
    for key in mf.ROLE_KINDS:
        models = groups.get(key, {}).get("models") if isinstance(groups.get(key), Mapping) else None
        if not isinstance(models, list) or not models:
            errors.append(f"The {key} group is missing, or has no model.")
    for key, group in groups.items():
        if key not in mf.ROLE_KINDS and not mf.ADDED_GROUP_NAME.fullmatch(str(key)):
            errors.append(f"The group {key} is not a group name: an added group is "
                          "lowercase letters, digits and dashes, starting with a letter.")
        if not isinstance(group, Mapping):
            errors.append(f"The {key} group must carry its label, purpose and models.")
            continue
        for field in ("label", "purpose"):
            if not _is_text(group.get(field)):
                errors.append(f"The {key} group must carry its {field} as text.")
        models = group.get("models")
        if isinstance(models, list) and not all(_is_text(m) and m for m in models):
            errors.append(f"The {key} group must list its models as model ids.")
        elif models is not None and not isinstance(models, list):
            errors.append(f"The {key} group must list its models as model ids.")

    agents = doc.get("agents")
    if not isinstance(agents, Mapping) or not all(_is_text(g) for g in agents.values()):
        errors.append("agents must map each agent name to its group key.")
        agents = {}
    for name, group in agents.items():
        if _is_text(group) and group not in groups:
            errors.append(f"The agent {name} is in the group {group}, which the "
                          "document does not declare.")
    for role, group in mf.misplaced_backstops(agents, required=True):
        where = "in no group" if group is None else f"in {group}"
        errors.append(f"The {role} must be in the {mf.ADVISORY_KIND} group, because it "
                      f"reviews before an unattended merge, and it is {where}.")
    session = set(code_side.get("session_agents") or ())
    owners = " and ".join(mf.JUDGEMENT_ROLES)
    for role in mf.misplaced_planners(agents, session):
        errors.append(f"Only {owners} and the session agents may be in the "
                      f"{mf.JUDGEMENT_KIND} group, and {role} is in it.")

    added = doc.get("added_agents", {})
    if not isinstance(added, Mapping) or not all(_is_text(v) for v in added.values()):
        errors.append("added_agents must map each added agent to its one-line description.")
    projects = doc.get("projects", {})
    if not isinstance(projects, Mapping) or not all(
            isinstance(entry, Mapping) and all(_is_text(m) and m for m in entry.values())
            for entry in projects.values()):
        errors.append("projects must map each project slug to the model each named "
                      "group tries first there.")
    return errors


def _model_refusal(model: str, prices: Mapping, code_side: Mapping):
    """`(reason, sentence)` when this release cannot run `model`, else None.
    Excluded and retired are named first: they are this release's decision
    about a model it knows, where a missing price may only mean it is new."""
    if model in set(code_side.get("excluded") or ()):
        return "excluded here", f"{model} is excluded, so it cannot be chosen."
    if model in set(code_side.get("retired") or ()):
        return "retired here", f"{model} is retired, so it cannot be chosen."
    if model not in prices:
        return "no price here", mf._unpriced(model)
    return None


def unknown_entries(doc, prices: Mapping, code_side: Mapping, repo_slugs) -> list[dict]:
    """Every entry naming something this checkout does not know, as
    `{"entry", "reason", "sentence", "where"}`: `entry` and `reason` are what
    the tagged line names, `sentence` is the refusal a writer prints, and
    `where` is the path `set_aside` removes. A reader sets each aside and uses
    the rest.

    Every model id has a declared price and is neither `excluded` nor
    `retired`; every agent name is in agents.yaml, `session_agents` or
    `added_agents`; every project slug is a key of config/repo-map.json and
    names groups that exist.
    """
    doc, refusal = _parse(doc)
    if refusal:
        return []
    found: list[dict] = []

    def unknown(entry, reason, sentence, *where):
        found.append({"entry": entry, "reason": reason, "sentence": sentence,
                      "where": list(where)})

    groups = doc.get("groups") if isinstance(doc.get("groups"), Mapping) else {}
    for key, group in groups.items():
        models = group.get("models") if isinstance(group, Mapping) else None
        for model in models if isinstance(models, list) else []:
            refused = _model_refusal(str(model), prices, code_side)
            if refused:
                unknown(str(model), *refused, "groups", key, model)

    added = doc.get("added_agents") if isinstance(doc.get("added_agents"), Mapping) else {}
    registry = code_side.get("agents")
    known = set(registry or ()) | set(code_side.get("session_agents") or ()) | set(added)
    agents = doc.get("agents") if isinstance(doc.get("agents"), Mapping) else {}
    for name in agents:
        if name not in known:
            unknown(f"agent {name}", "not an agent here",
                    f"{name} is not an agent: it is in neither agents.yaml, the "
                    "session agents nor added_agents.", "agents", name)

    projects = doc.get("projects") if isinstance(doc.get("projects"), Mapping) else {}
    for slug, entry in projects.items():
        if repo_slugs is None or slug not in repo_slugs:
            unknown(f"project {slug}", "not in repo-map",
                    f"{slug} is not a project in config/repo-map.json, so its "
                    "choices cannot be used.", "projects", slug)
            continue
        for group, model in (entry.items() if isinstance(entry, Mapping) else ()):
            if group not in groups:
                unknown(f"project {slug} {group}", "no such group",
                        f"Project {slug} names the group {group}, which the "
                        "document does not declare.", "projects", slug, group)
                continue
            refused = _model_refusal(str(model), prices, code_side)
            if refused:
                unknown(str(model), *refused, "projects", slug, group)
    return found


def set_aside(doc: Mapping, entries: list[dict]) -> dict:
    """A copy of `doc` without the entries `unknown_entries` named. A project
    left with no choice is dropped."""
    kept = copy.deepcopy(dict(doc))
    for entry in entries:
        where = entry["where"]
        if where[0] == "groups":
            group = kept["groups"][where[1]]
            group["models"] = [m for m in group["models"] if m != where[2]]
        elif where[0] == "agents":
            kept["agents"].pop(where[1], None)
        elif len(where) == 2:
            kept["projects"].pop(where[1], None)
        else:
            choice = kept["projects"].get(where[1])
            if isinstance(choice, dict):
                choice.pop(where[2], None)
                if not choice:
                    kept["projects"].pop(where[1], None)
    return kept


def home_errors(doc, prices: Mapping, code_side: Mapping, repo_slugs) -> list[str]:
    """Both tiers as refusals, one plain sentence each: what every writer
    checks before it writes (the `check` CLI, the operator's seeding, the
    daily adoption, the carry, the console). Empty: the document may be
    written."""
    errors = structure_errors(doc, code_side)
    sentences = [e["sentence"] for e in unknown_entries(doc, prices, code_side, repo_slugs)]
    return errors + list(dict.fromkeys(sentences))


# --------------------------------------------------------------------------- #
# The read                                                                     #
# --------------------------------------------------------------------------- #

class _Unreadable(Exception):
    """A read that cannot go on; its text is the reason."""


def _outcome(state: str, reason=None, document=None, aside=None) -> dict:
    return {"state": state, "reason": None if reason is None else one_line(reason),
            "document": document, "set_aside": list(aside or [])}


def _http_get(url: str, headers: Mapping, timeout: float) -> tuple[int, str]:
    """`(status, body)` of one GET. An HTTP error status is an answer, not a
    raise; a network failure or a timeout raises."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return exc.code, body


def _fetch(token: str) -> str:
    """The document's text from the variable: one GET, no retry."""
    import urllib.error

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "bureau-pipeline-model-home",
    }
    try:
        status, body = _http_get(API_URL, headers, TIMEOUT_SECONDS)
    except TimeoutError:
        raise _Unreadable(f"the GitHub API did not answer within {TIMEOUT_SECONDS} seconds")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise _Unreadable(f"the GitHub API did not answer within {TIMEOUT_SECONDS} seconds")
        raise _Unreadable(f"the GitHub API could not be reached ({exc.reason})")
    except Exception as exc:
        raise _Unreadable(f"the GitHub API could not be reached ({exc})")
    if status != 200:
        raise _Unreadable(f"the GitHub API answered HTTP {status} for {VARIABLE} ({body})")
    try:
        value = json.loads(body).get("value")
    except (ValueError, AttributeError):
        value = None
    if not isinstance(value, str):
        raise _Unreadable(f"the GitHub API answer for {VARIABLE} carries no value")
    return value


def _source(token, path) -> str | None:
    """The document's text, or None when nobody asked for a home."""
    if path is None and token is None:
        if FILE_ENV in os.environ:
            path = os.environ[FILE_ENV]
        elif TOKEN_ENV in os.environ:
            token = os.environ[TOKEN_ENV]
        else:
            return None
    if path is not None:
        if not path:
            raise _Unreadable(f"{FILE_ENV} is set and empty")
        if not Path(path).is_file():
            raise _Unreadable(f"{FILE_ENV} names {path}, which does not exist")
        try:
            return Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise _Unreadable(f"{FILE_ENV} names {path}, which could not be read ({exc})")
    if not token:
        raise _Unreadable(f"{TOKEN_ENV} is set and empty, so the token mint failed")
    return _fetch(token)


def judge(text, prices: Mapping | None = None, side: Mapping | None = None,
          repo_slugs=None) -> dict:
    """The outcome of reading `text` as a reader does: unreadable for a
    whole-document rule, else read with the entries this release cannot judge
    set aside — unreadable after all when that leaves a group with no model.
    The facts default to this checkout's."""
    side = code_side() if side is None else side
    errors = structure_errors(text, side)
    if errors:
        return _outcome(UNREADABLE, "; ".join(errors))
    doc, _ = _parse(text)
    if prices is None:
        prices = mf.declared_prices()
    if repo_slugs is None:
        repo_slugs = mf.declared_repo_slugs()
    entries = unknown_entries(doc, prices, side, repo_slugs)
    kept = set_aside(doc, entries)
    left = structure_errors(kept, side)
    if left:
        named = ", ".join(dict.fromkeys(e["entry"] for e in entries))
        return _outcome(UNREADABLE, f"set aside as unknown to this release ({named}), "
                        "and then " + "; ".join(left))
    return _outcome(READ, document=kept, aside=entries)


def read(token=None, path=None) -> dict:
    """Read the home, judge it, print its tagged line to stderr, and return
    the outcome: `{"state": "read" | "unreadable" | "not set", "reason",
    "document", "set_aside"}`. `document` is the one the reader will use, any
    entry it cannot judge set aside — None unless read. `reason` is one safe
    line. `path` or `token` given stand in for the two environment names.
    Never raises."""
    try:
        text = _source(token, path)
        outcome = _outcome(NOT_SET) if text is None else judge(text)
    except _Unreadable as exc:
        outcome = _outcome(UNREADABLE, str(exc))
    except Exception as exc:  # never raises: the dispatch path of every card
        outcome = _outcome(UNREADABLE, f"the reader failed ({type(exc).__name__}: {exc})")
    print(status_line(outcome), file=sys.stderr)
    return outcome


# --------------------------------------------------------------------------- #
# What a read says                                                             #
# --------------------------------------------------------------------------- #

def status_line(outcome: Mapping) -> str:
    """The tagged line a read prints to stderr, in the contract's words."""
    state = outcome.get("state")
    if state == READ:
        doc = outcome.get("document") or {}
        line = (f"{TAG} read — written {one_line(doc.get('written_at'))} "
                f"by {one_line(doc.get('written_by'))}")
        aside = [f"{one_line(e['entry'])} ({one_line(e['reason'])})"
                 for e in outcome.get("set_aside") or ()]
        if aside:
            line += "; set aside as unknown to this release: " + ", ".join(
                dict.fromkeys(aside))
        return line
    if state == UNREADABLE:
        return f"{TAG} unreadable — {one_line(outcome.get('reason'))}; {_ON_DEFAULTS}"
    return f"{TAG} not set — {_ON_DEFAULTS}"


def status_clause(outcome: Mapping | None) -> str:
    """The selection note's trailing clause: ` · model-home: read`, the
    unreadable one, or nothing for a home that is not set."""
    state = (outcome or {}).get("state")
    if state == READ:
        return f" · {TAG} read"
    if state == UNREADABLE:
        return (f" · {TAG} unreadable ({one_line(outcome.get('reason'))}), "
                f"{_ON_DEFAULTS}")
    return ""


def render_defaults(now: datetime | None = None) -> dict:
    """The document the code would run on: `model_fallback.code_defaults()`
    as a home, `written_by: operator`, `written_at` now. What the operator
    seeds the home with (DRE-6945)."""
    d = mf.code_defaults()
    when = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "version": VERSION,
        "written_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "written_by": "operator",
        "groups": {
            kind: {"label": d["groups"].get(kind, {}).get("label", kind.title()),
                   "purpose": d["groups"].get(kind, {}).get("purpose", ""),
                   "models": list(d["ladders"][ladder])}
            for kind, ladder in d["kinds"].items()
        },
        "agents": dict(d["agents"]),
        "added_agents": {},
        "projects": {slug: dict(entry) for slug, entry in d["projects"].items()},
    }


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("read", "check", "render-defaults"):
        print("usage: model_home.py read [--out <file>] | check <file> | render-defaults",
              file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "read":
        try:
            out = rest[rest.index("--out") + 1] if "--out" in rest else None
            outcome = read()
            if out and outcome["state"] == READ:
                Path(out).write_text(json.dumps(outcome["document"], indent=2) + "\n")
        except Exception as exc:  # exit 0 always: the line already said it
            print(f"{TAG} could not write the document ({one_line(exc)})", file=sys.stderr)
        return 0
    if cmd == "check":
        if not rest:
            print("usage: model_home.py check <file>", file=sys.stderr)
            return 2
        try:
            text = Path(rest[0]).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"The document {rest[0]} could not be read ({exc}).")
            return 1
        errors = home_errors(text, mf.declared_prices(), code_side(),
                             mf.declared_repo_slugs())
        for line in errors:
            print(line)
        return 1 if errors else 0
    print(json.dumps(render_defaults(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
