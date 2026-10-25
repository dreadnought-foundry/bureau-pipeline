#!/usr/bin/env python3
"""The judge of a change to the verify step (DRE-7972).

`groom_replay.py` (DRE-7239) replays a stored proposal through the verify
leg, and `groom_replay_cases.py` (DRE-7971) keeps every removal a person
recorded as a case. Nothing said whether a pull request reached the verify
step, or whether a case that passes on `main` failed on it. This module says
both, and writes each case's `main` block. It runs no model and makes no
Linear or door read: it reads `git diff`, the cases, and the verified
proposal a replay leaves behind.

  * `reach --base <sha> --head <sha> [--gate-set …]` — true when a path the
    diff changes is in `VERIFY_STEP_PATHS`, and true with the reason when
    `git diff --name-only --no-renames <base>...<head>` or the gate set
    cannot be read (fail open, the CI rule in `standards/engineering.md`).
    A rename names its old path, so renaming a listed file away reaches.
    `cards` is the number of distinct cards in the gate set, 0 when it cannot
    be read. Under GitHub Actions it writes `reached=` and `cases=` to
    `$GITHUB_OUTPUT`.

        groom-replay-gate: reach <true|false> cards=<n> — <why>

  * `judge --proposal <proposal-verified.json> [--targets …] [--cases …]
    [--out …]` — one line per case, then a count line; exit 1 when a case
    whose `main.passes` is true fails its `expect` on the change.

        groom-replay-gate: <case> expect=<expect> main=<pass|fail|none> head=<ended|none> search=<door|linear|none> <pass|fail|not judged>[ — REGRESSED| — now passes]
        groom-replay-gate: <n> cases — <p> pass, <f> fail, <x> not judged, <r> regressed

  * `record-main --proposal <proposal-verified.json> --at <ISO> [--cases …]`
    — the same lines, and each case's `main` block rewritten from a replay of
    `main`; never exits 1. It is the only writer of `main`. A card the replay
    did not judge gets `passes: null` and its earlier block is not kept, as
    the card says: its ratchet waits for the next replay that judges it.

## The ratchet

A case that has never passed on `main` exists precisely because the verify
step got that card wrong, so it cannot block every change to the step. Only
a case whose `main.passes` is true can fail a run — it is `regressed` when
it ends, on the change, where it fails its `expect`. Every other case is
reported as pass, fail or not judged, and one that passes is `now passes`:
the next Sunday's replay on `main` records it once the change merges.

A case card is judged when its row in the verified proposal carries a
`verify` record whose verdict is not `excluded`, and it ends somewhere that
says something (`groom_replay_cases.passes`). Anything else — a crashed leg,
a card the window did not reach, a card left out of the gate set — is `not
judged`, never a pass.

`--targets` names the `verify-targets.json` of the same run, and each case's
line says which search its card was judged on: the row's
`matched_work.source`, `door` or `linear`, else `none`. It is reported and
never changes a verdict.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess  # nosec B404 — a fixed-arg `git diff`, argv list, never a shell
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import groom_replay  # noqa: E402 — where a card ended
import groom_replay_cases  # noqa: E402 — the cases, `passes`, the gate set

ROOT = Path(__file__).resolve().parent.parent

TAG = "groom-replay-gate:"

#: The files a replay exercises: the verify agent, its lookups, routing and
#: brief, the replay itself, the workflow that runs them — and the two modules
#: whose code the fold calls to decide where a card ends. `groomer` holds the
#: Cancel refusals, `proposal_id`, `assert_disjoint`, `proof_line`,
#: `defang_reason`, `open_children` and `_FILE_REF`; `groom_verify` holds
#: `SPARE` and `SOURCE_NAMES`, which decide the cards the window judges.
VERIFY_STEP_PATHS = (
    "scripts/groom_verify_agent.py", "scripts/groom_lookups.py",
    "scripts/groom_routing.py", "scripts/groom_replay.py",
    "briefs/groom-verify.md", ".github/workflows/groomer.yml",
    "scripts/groomer.py", "scripts/groom_verify.py")

#: Every other `scripts/groom*.py` module a listed script imports, and why a
#: replay does not exercise it. A replay runs `groom_replay base` in place of
#: `groomer.py propose`, then the verify legs, then renders the page.
#: `unlisted_imports` names any import in neither list.
NOT_REPLAYED = {
    "scripts/groom_context.py": (
        "the verify agent reads only `SECTIONS`, the names in a count-failed "
        "message, and `groomer` only to name an empty pack on the page; "
        "`groom_verify` calls its GitHub readers from `check`, which "
        "`propose` runs and a replay skips"),
    "scripts/groom_judgement.py": (
        "the ranked read, which a replay turns off and never calls, since it "
        "skips `propose`; `groomer` reads its reason labels only to word the "
        "page"),
    "scripts/groom_memory.py": (
        "read by `propose` (`read_memory`), which a replay skips; `groomer` "
        "reads its words only for the page"),
    "scripts/groom_priority.py": (
        "read by `propose`, which a replay skips; `groomer` reads its date "
        "parser only for the page's stale-priority lines"),
    "scripts/groom_readiness.py": (
        "read by `propose`, which a replay skips"),
}

#: The import walk's family: shared plumbing outside it (`linear_ops`,
#: `sanitize_untrusted`, …) is not walked, or most pull requests here would
#: replay the set.
FAMILY = "groom"

(PASS, FAIL, NOT_JUDGED) = ("pass", "fail", "not judged")
REGRESSED = "REGRESSED"
NOW_PASSES = "now passes"
SEARCHES = ("door", "linear")
NO_SEARCH = "none"
NO_PATH = "no verify-step path changed"


class GateError(Exception):
    """An input the gate cannot act on — said, and exit 2."""


# --------------------------------------------------------------------------- #
# the import walk                                                              #
# --------------------------------------------------------------------------- #

def groom_imports(path: Path) -> set[str]:
    """`scripts/<name>.py` for every `groom*` module the file imports,
    anywhere in it."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return {f"scripts/{name}.py" for name in names if name.startswith(FAMILY)}


def unlisted_imports(root: Path = ROOT) -> list[str]:
    """Every `groom*` module a listed `.py` file under `root` imports that is
    in neither `VERIFY_STEP_PATHS` nor `NOT_REPLAYED`, sorted."""
    found: set[str] = set()
    for path in VERIFY_STEP_PATHS:
        if path.endswith(".py") and (Path(root) / path).is_file():
            found |= groom_imports(Path(root) / path)
    return sorted(found - set(VERIFY_STEP_PATHS) - set(NOT_REPLAYED))


# --------------------------------------------------------------------------- #
# reach                                                                        #
# --------------------------------------------------------------------------- #

def changed_paths(base: str, head: str) -> list[str]:
    """The paths `git diff --name-only --no-renames <base>...<head>` names,
    in the current directory's repository, or GateError with why they could
    not be read. `--no-renames` names a rename's old path as a deletion,
    where rename detection would name only the new one."""
    argv = ["git", "diff", "--name-only", "--no-renames", f"{base}...{head}"]
    try:
        done = subprocess.run(argv, capture_output=True, text=True,  # nosec B603
                              timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        raise GateError(f"`git diff {base}...{head}` could not run: {e}")
    if done.returncode != 0:
        said = (done.stderr or "").strip().splitlines()
        raise GateError(f"`git diff {base}...{head}` exited {done.returncode}"
                        + (f": {said[0]}" if said else ""))
    return [p for p in done.stdout.splitlines() if p.strip()]


def gate_cases(path: Path) -> int:
    """The number of distinct cards the gate set replays."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise GateError(f"the gate set {path} could not be read: {e}")
    if not groom_replay._is_proposal(doc):
        raise GateError(f"the gate set {path} is not a proposal")
    return len({row.get("identifier") for row in groom_replay._rows(doc)
                if row.get("identifier")})


def reach(base: str, head: str, gate_set: Path) -> tuple[bool, int, str]:
    """`(reached, cases, the line)`. An unreadable gate set or diff is true,
    and says why."""
    unread = []
    try:
        cases = gate_cases(gate_set)
    except GateError as e:
        cases = 0
        unread.append(str(e))
    try:
        changed = changed_paths(base, head)
    except GateError as e:
        changed = []
        unread.append(str(e))
    matched = [p for p in changed if p in VERIFY_STEP_PATHS]
    if unread:
        reached, why = True, f"failed open: {'; '.join(unread)}"
    else:
        reached, why = bool(matched), ", ".join(matched) or NO_PATH
    word = "true" if reached else "false"
    return reached, cases, f"{TAG} reach {word} cards={cases} — {why}"


def _github_output(pairs: dict) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as fh:
        for key, value in pairs.items():
            fh.write(f"{key}={value}\n")


# --------------------------------------------------------------------------- #
# judge                                                                        #
# --------------------------------------------------------------------------- #

def _on_proposal(proposal: dict, card: str) -> bool:
    return any(row.get("identifier") == card
               for row in groom_replay._rows(proposal))


def judged(proposal: dict, card: str) -> bool:
    """The card's row carries a verify record whose verdict is not
    `excluded`."""
    mark = groom_replay._records(proposal).get(card)
    return mark is not None and mark.get("verdict") != groom_replay.EXCLUDED


def searches(rows) -> dict[str, str]:
    """`{card: door|linear}` off a `verify-targets.json`'s rows."""
    out = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("card"), str):
            continue
        work = row.get("matched_work")
        source = work.get("source") if isinstance(work, dict) else None
        if source in SEARCHES:
            out.setdefault(row["card"], source)
    return out


def _main_word(passes) -> str:
    return {True: PASS, False: FAIL}.get(passes, NO_SEARCH)


def judge_case(case: dict, proposal: dict, search: dict[str, str]) -> dict:
    """One case, judged on the change."""
    card = case["card"]
    head = groom_replay.ended(proposal, card) if _on_proposal(proposal, card) else None
    passes = groom_replay_cases.passes(case, head, judged=judged(proposal, card))
    result = {True: PASS, False: FAIL}.get(passes, NOT_JUDGED)
    on_main = (case.get("main") or {}).get("passes")
    return {"case": case["case"], "card": card, "expect": case["expect"],
            "main": _main_word(on_main), "head": head,
            "search": search.get(card, NO_SEARCH), "result": result,
            "passes": passes,
            "regressed": on_main is True and passes is False,
            "now_passes": on_main is not True and passes is True}


def case_line(judged_case: dict) -> str:
    j = judged_case
    tail = (f" — {REGRESSED}" if j["regressed"]
            else f" — {NOW_PASSES}" if j["now_passes"] else "")
    return (f"{TAG} {j['case']} expect={j['expect']} main={j['main']} "
            f"head={j['head'] or NO_SEARCH} search={j['search']} "
            f"{j['result']}{tail}")


def judge(cases: list[dict], proposal: dict,
          search: dict[str, str] | None = None) -> tuple[list[str], dict]:
    """`(lines, the --out document)`."""
    done = [judge_case(case, proposal, search or {}) for case in cases]
    count = {word: sum(1 for j in done if j["result"] == word)
             for word in (PASS, FAIL, NOT_JUDGED)}
    regressed = [j["case"] for j in done if j["regressed"]]
    lines = [case_line(j) for j in done]
    lines.append(f"{TAG} {len(done)} cases — {count[PASS]} pass, "
                 f"{count[FAIL]} fail, {count[NOT_JUDGED]} not judged, "
                 f"{len(regressed)} regressed")
    doc = {"cases": [{k: v for k, v in j.items() if k != "passes"} for j in done],
           "regressed": regressed,
           "now_passes": [j["case"] for j in done if j["now_passes"]],
           "not_judged": [j["case"] for j in done if j["result"] == NOT_JUDGED]}
    return lines, doc


# --------------------------------------------------------------------------- #
# record-main                                                                  #
# --------------------------------------------------------------------------- #

def record_main(cases: list[dict], proposal: dict, at: str) -> list[str]:
    """Rewrite each case's `main` block, in place, from a replay of `main`,
    and return the same lines `judge` prints — judged against the block it
    replaces."""
    lines, doc = judge(cases, proposal)
    for case, judged_case in zip(cases, doc["cases"]):
        case["main"] = {"ended": judged_case["head"], "at": at,
                        "passes": {PASS: True, FAIL: False}.get(judged_case["result"])}
    return lines


# --------------------------------------------------------------------------- #
# the command line                                                             #
# --------------------------------------------------------------------------- #

def _load_json(path: str, what: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise GateError(f"{what} {path} could not be read: {e}")


def _proposal(path: str) -> dict:
    doc = _load_json(path, "the proposal")
    if not groom_replay._is_proposal(doc):
        raise GateError(f"the proposal {path} is not a proposal")
    return doc


def _cases(path: str) -> dict:
    try:
        return groom_replay_cases._load_cases(Path(path))
    except groom_replay_cases.CasesError as e:
        raise GateError(str(e))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    cases = str(ROOT / groom_replay_cases.CASES_PATH)
    p = sub.add_parser("reach", help="does the change touch the verify step")
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--gate-set", default=str(ROOT / groom_replay_cases.GATE_SET_PATH))
    p = sub.add_parser("judge", help="did a case that passes on main fail")
    p.add_argument("--proposal", required=True)
    p.add_argument("--targets")
    p.add_argument("--cases", default=cases)
    p.add_argument("--out")
    p = sub.add_parser("record-main", help="write each case's main block")
    p.add_argument("--proposal", required=True)
    p.add_argument("--at", required=True)
    p.add_argument("--cases", default=cases)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "reach":
            reached, cases, said = reach(args.base, args.head, Path(args.gate_set))
            print(said)
            _github_output({"reached": "true" if reached else "false",
                            "cases": cases})
            return 0
        proposal = _proposal(args.proposal)
        doc = _cases(args.cases)
        if args.command == "record-main":
            lines = record_main(doc["cases"], proposal, args.at)
            groom_replay_cases._write(Path(args.cases), doc)
            print("\n".join(lines))
            return 0
        search = {}
        if args.targets:
            # Reported, never a verdict: an unreadable file is every case
            # `search=none`, and the run is judged all the same.
            try:
                search = searches(_load_json(args.targets, "the targets file"))
            except GateError as e:
                print(f"{TAG} {e} — every case is search={NO_SEARCH}",
                      file=sys.stderr)
        lines, out = judge(doc["cases"], proposal, search)
        print("\n".join(lines))
        if args.out:
            try:
                with open(args.out, "w", encoding="utf-8") as fh:
                    json.dump(out, fh, indent=2)
                    fh.write("\n")
            except OSError as e:
                raise GateError(f"{args.out} could not be written: {e}")
        return 1 if out["regressed"] else 0
    except GateError as e:
        print(f"{TAG} {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
