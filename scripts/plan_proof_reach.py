#!/usr/bin/env python3
"""A PROOF criterion the proof run cannot read, or one reading an operator step
it is not blocked by, found before the critic reads the plan (DRE-6606).

The plan critic read a PROOF card's criteria for whether they are observable at
all, and nothing read them against what a dispatched proof run can actually
observe. DRE-6543 asked for `gh variable get` reads the token answers 403 to;
DRE-6431 row 6 asked for every repo in `config/repo-map.json`, two of which sit
in organizations the read token cannot reach; DRE-6042 asked the record to
quote the sweep's `proof-release:` line on a ready release, a line the sweep did
not print until DRE-6516; and DRE-6543 rows 3 and 4 read agent-bureau's
`BUREAU_DOCKER_RUNS_ON` as set while setting it was the open operator card
DRE-6478, with no `blockedBy` between the two. Every one was readable off the
plan before the CEO approved it.

So both readings are STRUCTURAL, in the seam's shape (`plan_seam.py`,
`plan_seam_gate.py`, DRE-3395/DRE-3398): computed before the critic reads,
appended to the structural file the gate forwards into the result, so the round
is a `SEND_BACK` naming the criterion whatever the model wrote.

## The two rules, each a fixed sentence

1. **Reach.** `proof_reach.out_of_reach` names a reading the run cannot make —
   `FINDING_REACH`, once per reading named.
2. **Operator step.** A criterion that names an operator card's id, or shares a
   backticked token with that card's TITLE, where the PROOF card is not
   `blocked_by` it — `FINDING_OPERATOR`.

## What an operator card is

Open (`plan_critic.shipped_work_is_a_finding`), not a PROOF title
(`proof_and_demo.is_proof`), not the PROOF card being read, and either wearing
the hold label or the `operator-step` mark, or routed `OPERATOR` by the routing
vocabulary's own reads — `routing_verdict.label_verdict` on its labels,
`routing_verdict.title_verdict` on its title. The PROOF exclusion is the point:
every proof card wears the labels that route `OPERATOR`, so without it a proof
card is its own operator card, and every other open proof is one too.

The match reads the operator card's TITLE, never its body: a title names the
one thing the step sets, and a body quotes half the pipeline's tags. A title
token is a backticked span, or a bare `LIKE_THIS` name — DRE-6478 as filed
writes `BUREAU_DOCKER_RUNS_ON` with no backticks, and a bare common word
(`main`, `stable`) is never one.

The candidates are the epic's siblings and every record `operator-cards --out`
wrote: every open card on the board wearing one of the operator labels, PROOF
titles and epics dropped, so a cross-epic operator card (DRE-6478 was one) is
seen and no proof card ever is.

CLI:
  findings --detail-file F [--operator-cards-file F] [--structural-file F]
           [--note-file F] [--caller SLUG] [--proof-local F]
      `children-json` records on stdin, `children-detail` records in F. Prints
      the finding lines (or `no criterion out of reach — …`), APPENDS them to
      the structural file (never truncates it: `plan_seam.py findings` writes
      it first and the gate reads both), appends a block to an EXISTING note
      file, and always exits 0.
  operator-cards --out F
      Every open operator card, as `{identifier, title, body, labels, state}`
      records. A failed read writes `[]` and says so on stderr; exits 0.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import checkbox_marks  # noqa: E402
import dead_run  # noqa: E402
import plan_critic  # noqa: E402
import plan_seam  # noqa: E402
import proof_and_demo  # noqa: E402
import proof_reach  # noqa: E402
import proof_record  # noqa: E402
import routing_verdict  # noqa: E402

#: The reach finding, one per reading named. `tests/test_plan_proof_reach.py`
#: pins the words and `standards/plan-critic.md` quotes them.
FINDING_REACH = ("{proof}: criterion “{criterion}” asks for a reading the proof "
                 "run cannot make — {what} ({why}); write it as what the run can "
                 "observe, or drop it.")

#: The operator-step finding, one per operator card a criterion reads.
FINDING_OPERATOR = ("{proof}: criterion “{criterion}” reads the result of "
                    "operator step {operator} (“{title}”) and is not blocked by "
                    "it — add the blockedBy relation, or reword the criterion.")

#: The heading the block opens with when it is appended to the mechanical note.
NOTE_HEADING = ("Proof reach (structural — a send-back at the first critic, "
                "not advice):")

#: How much of a criterion a finding quotes, cut as `proof_record` cuts a row.
CUT = 70

#: The verdict the routing vocabulary gives a person's no-code step.
OPERATOR = "OPERATOR"

#: The repo's own declaration of the pages a local run opens, read from the
#: product checkout the plan workflow runs in.
PROOF_LOCAL = ".github/bureau/proof-local.json"

_BACKTICKED = re.compile(r"`([^`\n]+)`")
#: A bare `LIKE_THIS` name in a title: at least one underscore, so no plain word.
_BARE_NAME = re.compile(r"(?<![\w-])[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+(?![\w-])")

Reading = namedtuple("Reading", "lines criteria proofs")


# --- The labels, derived ----------------------------------------------------

def operator_labels(doc: dict | None = None) -> tuple:
    """The labels that make a card an operator card: the hold label, the
    `operator-step` mark, and every label the routing vocabulary routes
    `OPERATOR` — in that order, without repeats."""
    out = [dead_run.HOLD_LABEL, routing_verdict.OPERATOR_STEP_LABEL]
    for label, verdict in routing_verdict.label_map(doc).items():
        if verdict == OPERATOR and label not in out:
            out.append(label)
    return tuple(out)


def _labels(record: dict) -> list[str]:
    return [str(l).strip().lower() for l in record.get("labels") or []]


def is_operator_card(record: dict, proof_id: str = "", doc: dict | None = None) -> bool:
    title = record.get("title") or ""
    if not record.get("identifier") or record.get("identifier") == proof_id:
        return False
    if not plan_critic.shipped_work_is_a_finding(record) or proof_and_demo.is_proof(title):
        return False
    labels = _labels(record)
    if {dead_run.HOLD_LABEL, routing_verdict.OPERATOR_STEP_LABEL} & set(labels):
        return True
    return OPERATOR in (routing_verdict.label_verdict(labels, doc),
                        routing_verdict.title_verdict(title, doc))


# --- One PROOF card ---------------------------------------------------------

def criteria(body: str) -> list[str]:
    """The checkbox items under the acceptance heading, closing rows dropped."""
    out = []
    for line in routing_verdict.acceptance_section(body or "") or []:
        item = checkbox_marks.ITEM.match(line)
        if item and not proof_record.is_closing_row(item.group("text")):
            out.append(item.group("text"))
    return out


def proof_cards(records) -> list[dict]:
    """The PROOF children still to be proven, in record order."""
    return [r for r in records or []
            if proof_and_demo.is_proof(r.get("title") or "")
            and plan_critic.shipped_work_is_a_finding(r)]


def _title_tokens(title: str) -> set:
    """Backticked or bare, a title token is a variable-style name: one with
    an underscore. A plain word — `main`, `stable`, `v4` — never counts."""
    spans = {t.strip() for t in _BACKTICKED.findall(title or "")}
    return ({t for t in spans if "_" in t}
            | set(_BARE_NAME.findall(title or "")))


def _names(criterion: str, ident: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(ident)}(?!\w)", criterion) is not None


def reads_operator_step(criterion: str, operator: dict) -> bool:
    tokens = {t.strip() for t in _BACKTICKED.findall(criterion)}
    return (_names(criterion, operator["identifier"])
            or bool(tokens & _title_tokens(operator.get("title") or "")))


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _reach_lines(proof: dict, criterion: str, reach: dict) -> list[str]:
    out = []
    for said in proof_reach.out_of_reach(criterion, **reach):
        what, _, why = said.partition(" — ")
        out.append(_one_line(FINDING_REACH.format(
            proof=proof["identifier"], criterion=proof_record._cut(criterion, CUT),
            what=what, why=why)))
    return out


def _operator_lines(proof: dict, criterion: str, operators: list[dict]) -> list[str]:
    blocked = set(proof.get("blocked_by") or [])
    return [_one_line(FINDING_OPERATOR.format(
                proof=proof["identifier"], criterion=proof_record._cut(criterion, CUT),
                operator=op["identifier"], title=op.get("title") or ""))
            for op in operators
            if op["identifier"] not in blocked and reads_operator_step(criterion, op)]


def findings(cards, details, operator_cards=(), *, doc=None, repo_map=None,
             caller_slug=None, local=None, acts=None, routing=None) -> Reading:
    """Every finding over the epic's PROOF children, in record order.

    `cards` and `details` are the two board reads `plan_seam.join` merges;
    `operator_cards` are `operator-cards --out` records. The reach keywords go
    to `proof_reach.out_of_reach` as they are. A caller the repo map does not
    carry cannot be read for reach, so the reach rule is skipped and the
    operator rule still runs — the CLI says so on stderr.
    """
    records = plan_seam.join(cards, details)
    reach = {"doc": doc, "repo_map": repo_map, "caller_slug": caller_slug,
             "local": local, "acts": acts}
    candidates, seen = [], set()
    for rec in list(records) + [r for r in operator_cards or [] if isinstance(r, dict)]:
        ident = rec.get("identifier")
        if ident and ident not in seen:
            seen.add(ident)
            candidates.append(rec)
    lines, read, proofs = [], 0, proof_cards(records)
    for proof in proofs:
        operators = [c for c in candidates
                     if is_operator_card(c, proof["identifier"], routing)]
        for criterion in criteria(proof.get("body") or ""):
            read += 1
            if caller_slug is not False:
                lines += _reach_lines(proof, criterion, reach)
            lines += _operator_lines(proof, criterion, operators)
    return Reading(lines, read, len(proofs))


# --- The operator cards, read off the board ---------------------------------

#: Every open card wearing one of the labels, a page at a time. An epic is
#: read for its children so it can be dropped: a parent is never a blocker.
OPERATOR_CARDS_QUERY = """query($labels: [String!], $after: String) {
  issues(first: 50, after: $after, filter: {
    labels: {name: {in: $labels}},
    state: {type: {nin: ["completed", "canceled"]}}
  }) {
    nodes {
      identifier title description
      state { name }
      labels { nodes { name } }
      children(first: 1) { nodes { id } }
    }
    pageInfo { hasNextPage endCursor }
  }
}"""


def operator_cards(doc: dict | None = None) -> list[dict]:
    """Every open operator card on the board, PROOF titles and epics dropped."""
    import linear_ops  # the network, imported only by the command that uses it

    nodes = linear_ops.gql_paged(OPERATOR_CARDS_QUERY,
                                 {"labels": list(operator_labels(doc))})
    out, seen = [], set()
    for node in nodes:
        ident = node.get("identifier")
        if not ident or ident in seen or ((node.get("children") or {}).get("nodes")):
            continue
        record = {
            "identifier": ident,
            "title": node.get("title") or "",
            "body": node.get("description") or "",
            "labels": [l.get("name") for l in (node.get("labels") or {}).get("nodes") or []
                       if l.get("name")],
            "state": (node.get("state") or {}).get("name") or "",
        }
        if is_operator_card(record, doc=doc):
            seen.add(ident)
            out.append(record)
    return out


# --- CLI --------------------------------------------------------------------

def _json_file(path: str | None, what: str):
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"plan proof reach: could not read {what} {path} ({exc}) — "
              "reading none", file=sys.stderr)
        return None


def _records(value) -> list:
    return value if isinstance(value, list) else []


def _local(path: str | None):
    if not path or not os.path.isfile(path):
        return None
    loaded = _json_file(path, "the proof-local declaration")
    return loaded if isinstance(loaded, dict) else None


def _caller(slug: str | None):
    """The caller for `out_of_reach`, or False when the map does not carry it.

    An empty slug falls back to the run's own `owner/name`, which
    `out_of_reach` reads as well, and only then to its default."""
    caller = slug or os.environ.get("GITHUB_REPOSITORY") or None
    try:
        proof_reach.out_of_reach("", caller_slug=caller)
    except (ValueError, OSError) as exc:
        print(f"plan proof reach: {exc} — the reach rule is not read, the "
              "operator-step rule is", file=sys.stderr)
        return False
    return caller


def _append_structural(path: str | None, lines: list[str]) -> None:
    """Appended, never written: the seam wrote the file first and the gate
    reads both. Nothing to say opens nothing, so an absent file stays absent."""
    if not path or not lines:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
    except OSError as exc:
        print(f"plan proof reach: could not append to {path}: {exc}", file=sys.stderr)


def _append_note(path: str | None, lines: list[str]) -> None:
    if not path or not lines or not os.path.isfile(path):
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n" + NOTE_HEADING + "\n")
            for line in lines:
                fh.write(f"- {line}\n")
    except OSError as exc:
        print(f"plan proof reach: could not append to {path}: {exc}", file=sys.stderr)


def _cmd_findings(args) -> int:
    cards = _records(plan_seam._stdin_json([]))
    details = _records(_json_file(args.detail_file, "the children detail"))
    operators = _records(_json_file(args.operator_cards_file, "the operator cards"))
    try:
        reading = findings(cards, details, operators, caller_slug=_caller(args.caller),
                           local=_local(args.proof_local))
    except Exception as exc:  # noqa: BLE001 — input to a critic, never a crash
        print(f"plan proof reach: could not read the plan ({exc!r}) — no "
              "finding is written", file=sys.stderr)
        reading = Reading([], 0, 0)
    _append_structural(args.structural_file, reading.lines)
    _append_note(args.note_file, reading.lines)
    if not reading.lines:
        print(f"no criterion out of reach — {reading.criteria} criteria read on "
              f"{reading.proofs} proof card(s)")
    for line in reading.lines:
        print(line)
    return 0


def _cmd_operator_cards(args) -> int:
    try:
        cards = operator_cards()
    except Exception as exc:  # noqa: BLE001 — an empty list under-reports
        print(f"plan proof reach: could not read the operator cards ({exc}) — "
              "writing none, so only the epic's own are read", file=sys.stderr)
        cards = []
    try:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(cards, fh, ensure_ascii=False, indent=1)
    except OSError as exc:
        print(f"plan proof reach: could not write {args.out}: {exc}", file=sys.stderr)
    print(f"{len(cards)} open operator card(s) written to {args.out}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("findings", help="proof-reach findings; children-json on stdin")
    f.add_argument("--detail-file", required=True,
                   help="children-detail records, for titles and blocked_by")
    f.add_argument("--operator-cards-file", default=None,
                   help="operator-cards --out records, for cross-epic operator cards")
    f.add_argument("--structural-file", default=None,
                   help="the seam's structural file, appended to")
    f.add_argument("--note-file", default=None,
                   help="an existing mechanical note to append the findings to")
    f.add_argument("--caller", default=None,
                   help="the epic's repo: label slug (default: $GITHUB_REPOSITORY, "
                        f"then {proof_reach.SELF})")
    f.add_argument("--proof-local", default=PROOF_LOCAL,
                   help="the repo's proof-local declaration, read when present")
    f.set_defaults(fn=_cmd_findings)

    o = sub.add_parser("operator-cards", help="every open operator card, as JSON")
    o.add_argument("--out", required=True, help="where the records are written")
    o.set_defaults(fn=_cmd_operator_cards)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
