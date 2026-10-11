#!/usr/bin/env python3
"""What a dispatched proof run can read, declared once (DRE-6605).

Nine proofs stood open on 2026-10-10 because a criterion asked for a reading
nothing promised: DRE-6543, DRE-6431, DRE-6442, DRE-6042, DRE-6364, DRE-5377,
DRE-5662, DRE-6204 and DRE-5409. The reach was written in four places —
`proof-task.yml`'s two mints, `briefs/proof.md`'s Identities section, the
workflow test's own copy of the permissions, and the planner's memory of all
three — and none was checked against the others. `config/proof-reach.json`
declares it once, as data, and this module is its one reader:

* `github` — the workflow, its two mint steps, the `owner` they are scoped by
  (no `repositories`), the `permissions` both ask for and the
  `optional_permissions` only the wide one asks for. Organizations are not
  listed: the token reaches the caller's own organization only, and
  `out_of_reach` derives the unreachable repositories from
  `config/repo-map.json` by owner, so the map stays the one list of repos.
* `readings` — what a criterion may ask for, each `held` (`true`, `false` or
  `"optional"`), with the case-insensitive patterns (`names`) that find it in
  ONE criterion's text, the card it was read off (`why`) and the sentence
  `briefs/proof.md` carries for it (`brief`). An optional reading is held only
  for the repos its `held_for` names; `queries_declared_by` points at the
  product's own script for what that identity may query, rather than a copy.
* `log_lines` — the tagged lines a criterion may quote, each with the script
  or workflow that prints it and, for a script, the constant spelling it.
* `causes` — the four words a sibling card writes a cause row with. Only
  `causes()` reads them; nothing restates the list.

`out_of_reach(criterion, …)` is what the plan critic and the record reader
ask. `check(root)`, behind `python3 scripts/proof_reach.py check`, holds the
workflow's mints, the brief and the log lines' printers to the declaration.

A LEAF like `proof_record.py`: standard library plus PyYAML, read only by
`check` for the workflow. Nothing here imports `reconcile`.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECLARATION = "config/proof-reach.json"
REPO_MAP = "config/repo-map.json"
ACTS = "config/pipeline-acts.json"
BRIEF = "briefs/proof.md"

#: The pipeline's own repo, the caller when none is given.
SELF = "bureau-pipeline"

KEYS = ("github", "readings", "log_lines", "causes")
GITHUB_KEYS = ("workflow", "mints", "owner", "permissions", "optional_permissions")
MINT_KEYS = ("wide", "narrow")
READING_FIELDS = ("id", "held", "held_for", "what", "names", "why", "brief",
                  "queries_declared_by")
#: Fields only an optional reading carries.
OPTIONAL_FIELDS = ("held_for", "queries_declared_by")
HELD = (True, False, "optional")
LOG_LINE_FIELDS = ("tag", "printed_by", "constant", "when")
CAUSE_FIELDS = ("word", "meaning")
CAUSE_COUNT = 4

#: The readings this module's matching depends on, by id.
OTHER_ORGANIZATION = "other-organization"
LOCAL_SCREEN = "local-screen"
REQUIRED_READINGS = ("repo-variable", "db-read", "step-summary",
                     OTHER_ORGANIZATION, LOCAL_SCREEN, "product-identity")

#: A criterion that joins a roster phrase with this file means every repo in it.
_MAP_FILE = re.compile(r"repo-map\.json", re.IGNORECASE)
#: A backticked token ending in a colon: `proof-release:`, `🔬 proof-waiting:`.
_COLON_TOKEN = re.compile(r"`([^`\n]*:)`")
#: The words that make such a token a line the record quotes.
_QUOTES = re.compile(r"\b(?:quotes|quoted|lines?|logs?)\b", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# reading the files                                                            #
# --------------------------------------------------------------------------- #

def _json(root, rel: str):
    return json.loads((Path(root) / rel).read_text(encoding="utf-8"))


def load(path=None) -> dict:
    """The declaration, from `path` or this checkout's `config/proof-reach.json`."""
    return json.loads(Path(path or ROOT / DECLARATION).read_text(encoding="utf-8"))


def causes(doc=None) -> dict:
    """The cause words, in order, each to its meaning."""
    doc = load() if doc is None else doc
    return {c["word"]: c["meaning"] for c in doc["causes"]}


def readings(doc=None) -> list:
    doc = load() if doc is None else doc
    return list(doc["readings"])


def _act_tags(root=ROOT) -> list:
    return [a["tag"] for a in _json(root, ACTS)["acts"]]


# --------------------------------------------------------------------------- #
# the file's own shape                                                         #
# --------------------------------------------------------------------------- #

def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _github_problems(github) -> list:
    if not isinstance(github, dict):
        return ["`github` must be an object"]
    out = []
    if list(github) != list(GITHUB_KEYS):
        out.append(f"`github` keys are {list(github)}; expected {list(GITHUB_KEYS)}, in order")
    if not _text(github.get("workflow")):
        out.append("`github.workflow` must name the proof workflow")
    mints = github.get("mints")
    if (not isinstance(mints, dict) or list(mints) != list(MINT_KEYS)
            or not all(_text(v) for v in mints.values())):
        out.append(f"`github.mints` must name the {' and '.join(MINT_KEYS)} mint steps")
    if not _text(github.get("owner")):
        out.append("`github.owner` must be the expression both mints are scoped by")
    for key in ("permissions", "optional_permissions"):
        perms = github.get(key)
        if not isinstance(perms, dict) or not perms:
            out.append(f"`github.{key}` must be a non-empty object of permission → `read`")
            continue
        for name, level in perms.items():
            if level != "read":
                out.append(f"`github.{key}.{name}` is {level!r}; a proof identity reads only")
    both = set(github.get("permissions") or {}) & set(github.get("optional_permissions") or {})
    if both:
        out.append(f"`github` lists {sorted(both)} as both held and optional")
    return out


def _reading_problems(r, repo_map: dict) -> list:
    if not isinstance(r, dict):
        return ["every reading must be an object"]
    rid = r.get("id")
    where = f"reading `{rid}`"
    out = []
    unknown = [k for k in r if k not in READING_FIELDS]
    if unknown:
        out.append(f"{where} carries unknown fields {unknown}")
    if not _text(rid):
        out.append("a reading has no `id`")
    held = r.get("held")
    if not any(held is h or (isinstance(h, str) and held == h) for h in HELD):
        out.append(f"{where}: `held` is {held!r}; expected true, false or \"optional\"")
    for field in ("what", "why", "brief"):
        if not _text(r.get(field)):
            out.append(f"{where}: `{field}` must be a non-empty string")
    names = r.get("names")
    if not isinstance(names, list) or not names or not all(_text(n) for n in names):
        out.append(f"{where}: `names` must be a non-empty list of patterns")
    else:
        for name in names:
            try:
                re.compile(name)
            except re.error as exc:
                out.append(f"{where}: name {name!r} does not compile ({exc})")
    if held == "optional":
        held_for = r.get("held_for")
        if not isinstance(held_for, list) or not all(isinstance(s, str) for s in held_for):
            out.append(f"{where}: an optional reading names the repos it is held for "
                       f"in `held_for`, a list of slugs")
            held_for = []
        for slug in held_for:
            if slug not in repo_map:
                out.append(f"{where}: `held_for` names {slug!r}, which is not a key of "
                           f"{REPO_MAP}")
        declared = r.get("queries_declared_by")
        if declared is not None:
            if not isinstance(declared, dict) or not all(_text(v) for v in declared.values()):
                out.append(f"{where}: `queries_declared_by` must map a slug to a script path")
            elif set(declared) != set(held_for):
                out.append(f"{where}: `queries_declared_by` names {sorted(declared)}; "
                           f"it names exactly the `held_for` repos {sorted(held_for)}")
    else:
        for field in OPTIONAL_FIELDS:
            if field in r:
                out.append(f"{where}: `{field}` is for an optional reading only")
    return out


def problems(doc, repo_map=None) -> list:
    """What is wrong with the declaration's own shape, one line each."""
    if not isinstance(doc, dict):
        return [f"{DECLARATION} must be an object"]
    repo_map = _json(ROOT, REPO_MAP) if repo_map is None else repo_map
    out = []
    if list(doc) != list(KEYS):
        out.append(f"{DECLARATION} keys are {list(doc)}; expected {list(KEYS)}, in order")
    out += _github_problems(doc.get("github"))

    rows = doc.get("readings")
    if not isinstance(rows, list) or not rows:
        out.append("`readings` must be a non-empty list")
        rows = []
    for r in rows:
        out += _reading_problems(r, repo_map)
    ids = [r.get("id") for r in rows if isinstance(r, dict)]
    for rid in sorted({i for i in ids if ids.count(i) > 1}, key=str):
        out.append(f"reading `{rid}` is declared twice")
    for rid in REQUIRED_READINGS:
        if rid not in ids:
            out.append(f"reading `{rid}` is missing")

    lines = doc.get("log_lines")
    if not isinstance(lines, list) or not lines:
        out.append("`log_lines` must be a non-empty list")
        lines = []
    tags = []
    for e in lines:
        if not isinstance(e, dict) or list(e) != list(LOG_LINE_FIELDS):
            out.append(f"a `log_lines` entry must carry {list(LOG_LINE_FIELDS)}, in order: {e!r}")
            continue
        tags.append(e["tag"])
        if not _text(e["tag"]) or not e["tag"].endswith(":"):
            out.append(f"log line {e['tag']!r}: `tag` must end with a colon")
        if not _text(e["printed_by"]) or not _text(e["when"]):
            out.append(f"log line {e['tag']!r}: `printed_by` and `when` must be non-empty")
        if e["constant"] is not None and not _text(e["constant"]):
            out.append(f"log line {e['tag']!r}: `constant` is a name or null")
    for tag in sorted({t for t in tags if tags.count(t) > 1}, key=str):
        out.append(f"log line {tag!r} is declared twice")

    words = doc.get("causes")
    if not isinstance(words, list):
        out.append("`causes` must be a list")
        words = []
    if len(words) != CAUSE_COUNT:
        out.append(f"`causes` carries {len(words)} words; exactly {CAUSE_COUNT}")
    seen = []
    for c in words:
        if not isinstance(c, dict) or list(c) != list(CAUSE_FIELDS) \
                or not _text(c.get("word")) or not _text(c.get("meaning")):
            out.append(f"a cause is a word and its meaning: {c!r}")
            continue
        if c["word"] in seen:
            out.append(f"cause {c['word']!r} is declared twice")
        seen.append(c["word"])
    return out


# --------------------------------------------------------------------------- #
# one criterion                                                                #
# --------------------------------------------------------------------------- #

def _owner(full: str) -> str:
    return full.split("/", 1)[0].casefold()


def _caller(caller_slug, repo_map: dict) -> tuple:
    """`(slug, owner)` for a caller given as a slug or as `owner/name`."""
    caller = caller_slug or SELF
    if "/" in caller:
        slug = next((s for s, full in repo_map.items()
                     if full.casefold() == caller.casefold()), None)
        return slug, _owner(caller)
    if caller not in repo_map:
        raise ValueError(f"caller {caller!r} is not a key of {REPO_MAP}")
    return caller, _owner(repo_map[caller])


def _named(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def _word(literal: str) -> str:
    """A slug, full name or page key as a whole token, never inside a longer one."""
    return rf"(?<![\w./-]){re.escape(literal)}(?![\w/-]|\.\w)"


def _other_organizations(r: dict, criterion: str, repo_map: dict, owner: str) -> list:
    far = {slug: full for slug, full in repo_map.items() if _owner(full) != owner}
    if (any(_named(n, criterion) for n in r["names"])
            and _MAP_FILE.search(criterion)):
        return list(far.values())
    return [full for slug, full in far.items()
            if _named(_word(full), criterion) or _named(_word(slug), criterion)]


def _undeclared_page(r: dict, criterion: str, local) -> bool:
    if not any(_named(n, criterion) for n in r["names"]):
        return False
    pages = (local or {}).get("pages") or {}
    return not any(re.search(_word(key), criterion) for key in pages)


def _unkept_lines(criterion: str, doc: dict, acts) -> list:
    if not _QUOTES.search(criterion):
        return []
    kept = {e["tag"].rstrip(":").strip().casefold() for e in doc["log_lines"]}
    act_tags = {t.casefold() for t in acts}
    out = []
    for token in _COLON_TOKEN.findall(criterion):
        bare = token.rstrip(":").strip()
        last = bare.split()[-1].casefold() if bare.split() else ""
        if bare.casefold() in kept or last in act_tags or token in out:
            continue
        out.append(token)
    return out


def out_of_reach(criterion: str, *, doc=None, repo_map=None, caller_slug=None,
                 local=None, acts=None) -> list:
    """One `<what> — <why>` line per reading `criterion` names that the run
    cannot make; empty when the criterion is within reach.

    `caller_slug` is the repo the proof runs for, a key of `config/repo-map.json`
    or its `owner/name` (default: this repo). `local` is that repo's
    `.github/bureau/proof-local.json`, or None when it declares none. `acts`
    are the act tags of `config/pipeline-acts.json`, a receipt kept on the card.
    """
    doc = load() if doc is None else doc
    repo_map = _json(ROOT, REPO_MAP) if repo_map is None else repo_map
    acts = _act_tags() if acts is None else acts
    slug, owner = _caller(caller_slug, repo_map)
    out = []
    for r in doc["readings"]:
        if r["id"] == OTHER_ORGANIZATION:
            far = _other_organizations(r, criterion, repo_map, owner)
            if far:
                out.append(f"{r['what']} ({', '.join(far)}) — {r['why']}")
        elif r["id"] == LOCAL_SCREEN:
            if _undeclared_page(r, criterion, local):
                out.append(f"{r['what']} — {r['why']}")
        elif r["held"] is True:
            continue
        elif r["held"] == "optional" and slug in r.get("held_for", []):
            continue
        elif any(_named(n, criterion) for n in r["names"]):
            out.append(f"{r['what']} — {r['why']}")
    for token in _unkept_lines(criterion, doc, acts):
        out.append(f"the `{token}` line, which nothing keeps — no `log_lines` entry in "
                   f"{DECLARATION} and no act in {ACTS} carries it")
    return out


# --------------------------------------------------------------------------- #
# the workflow, the brief and the printers, held to the declaration            #
# --------------------------------------------------------------------------- #

def _steps(workflow: dict) -> dict:
    found = {}
    for job in (workflow.get("jobs") or {}).values():
        for step in (job or {}).get("steps") or []:
            if isinstance(step, dict) and "name" in step:
                found.setdefault(step["name"], step)
    return found


def _mint_problems(name: str, step, owner: str, want: dict) -> list:
    if step is None:
        return [f"`{name}`: no step of that name in the workflow"]
    with_ = step.get("with") or {}
    out = []
    if with_.get("owner") != owner:
        out.append(f"`{name}`: `owner` is {with_.get('owner')!r}; the declaration "
                   f"scopes it by {owner!r}")
    if "repositories" in with_:
        out.append(f"`{name}`: `repositories` is set; the declaration scopes the mint "
                   f"by `owner` alone")
    got = {k[len("permission-"):]: v for k, v in with_.items()
           if k.startswith("permission-")}
    for perm, level in want.items():
        if perm not in got:
            out.append(f"`{name}`: `permission-{perm}: {level}` is declared and the "
                       f"step does not ask for it")
        elif got[perm] != level:
            out.append(f"`{name}`: `permission-{perm}` is {got[perm]!r}; declared {level!r}")
    for perm in got:
        if perm not in want:
            out.append(f"`{name}`: `permission-{perm}: {got[perm]}` is asked for and "
                       f"not declared")
    return out


def _constant(source: str, name: str):
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            if isinstance(node.value, ast.Constant):
                return node.value.value
    return None


def _log_line_problems(root: Path, e: dict) -> list:
    path = root / e["printed_by"]
    where = f"log line {e['tag']!r}"
    if not path.is_file():
        return [f"{where}: `{e['printed_by']}` does not exist"]
    source = path.read_text(encoding="utf-8")
    if e["constant"] is None:
        return [] if e["tag"] in source else [
            f"{where}: `{e['printed_by']}` no longer prints it"]
    value = _constant(source, e["constant"])
    if not isinstance(value, str) or value.rstrip(":") != e["tag"].rstrip(":"):
        return [f"{where}: `{e['constant']}` in `{e['printed_by']}` is {value!r}, "
                f"not the tag"]
    return []


def check(root) -> list:
    """Every way the declaration and the files it governs disagree, one line each."""
    import yaml  # the one dependency past the standard library, for the workflow

    root = Path(root)
    try:
        doc = load(root / DECLARATION)
        repo_map = _json(root, REPO_MAP)
    except (OSError, ValueError) as exc:
        return [f"{DECLARATION}: {exc}"]
    out = problems(doc, repo_map)
    if out:
        return out

    github = doc["github"]
    workflow = yaml.safe_load((root / github["workflow"]).read_text(encoding="utf-8"))
    steps = _steps(workflow or {})
    wide, narrow = github["mints"]["wide"], github["mints"]["narrow"]
    out += _mint_problems(wide, steps.get(wide), github["owner"],
                          {**github["permissions"], **github["optional_permissions"]})
    out += _mint_problems(narrow, steps.get(narrow), github["owner"],
                          github["permissions"])

    brief = " ".join((root / BRIEF).read_text(encoding="utf-8").split())
    for r in doc["readings"]:
        if " ".join(r["brief"].split()) not in brief:
            out.append(f"reading `{r['id']}`: {BRIEF} no longer carries "
                       f"{r['brief']!r}")

    for e in doc["log_lines"]:
        out += _log_line_problems(root, e)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("check", help="hold the workflow and the brief to the declaration")
    run.add_argument("--root", default=str(ROOT))
    args = parser.parse_args(argv)
    found = check(args.root)
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
