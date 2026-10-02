#!/usr/bin/env python3
"""The hygiene agent's core (DRE-5368) — one board read, guarded writes, dry
run, the budget floor, and a summary posted only when something changed.

The hygiene agent is an hourly pass over the fleet that clears the mechanical
rows a person now clears by hand. This module is its core and nothing
lane-specific; a lane is a file `scripts/hygiene_<lane>.py` that a lane card
ADDS, found by the glob `LANE_GLOB` in `LANE_DIR`, and registered nowhere.

    python3 scripts/hygiene.py read --out board.json
    python3 scripts/hygiene.py run --board board.json --owner <owner> \\
        --ledger ledger-<owner>.json [--dry-run]
    python3 scripts/hygiene.py summarize ledger-*.json [--dry-run]

One command per job of the workflow (DRE-5369).

## `read` — the board, once, and the budget floor

The five lanes in `LANES` — plus `Hand-work` the day the lane contract declares
that lane (DRE-5240) — in ONE paged query through `board_snapshot.read_board`,
in the shape `board_snapshot.CARD_QUERY` returns, written to a file keyed by
lane. Then the floor: the fleet key's remaining requests off
`linear_ops._budget["last"]` — the figure `budget_line()` prints — and, below
`HYGIENE_BUDGET_FLOOR`, `go=false`, one sentence, and nothing else read or
written. That figure is `None` when no response carried the rate-limit header;
the floor is then NOT applied, because standing down on an unknown would
silence the agent for good the day Linear renamed a header, and a stand-down
naming a number nobody saw would be a false record. Exit 0 either way; a board
that could not be read at all is not an empty board (DRE-2034) — `go=false`
and exit 1, so the medic sees it.

## `run` — one owner's leg

Lists each of the owner's repos' open pull requests once, hands every
discovered lane the cards and pull requests in this leg's scope, and executes
what the lanes return through one guarded seam. A lane never writes: it returns
`Action`s (each carrying the `Write`s the constructors below built) and `Left`
rows for a person.

## Three rules, here and nowhere else

1. **The idempotency key is (tag, cause).** The seam suppresses an action whose
   target already carries a receipt with the same tag AND the same cause line —
   read off the card's comment window or the pull request's comments. A lane
   may stop harder by returning a `Left` row; nothing loosens the key.
2. **The guard reads SHAPE, never a substring.** The lane a state write moves
   to, whether the card has children, the label, the repo, the workflow a
   dispatch names, and the verb, flags and REST path of a `gh` argv. The
   pull-request recoveries dispatch a gate stub whose filename contains the
   word a substring guard would refuse, and the board's own read selects
   `mergeStateStatus`; a guard that refused a word would forbid the main case.
3. **The standing summary card is handed to no lane.** `HYGIENE_CARD` is
   dropped by identifier before any lane sees the board, and a write against
   it is refused at the seam.

## `summarize` — said once when it changes

One comment on `HYGIENE_CARD`, opening `SUMMARY_MARK <digest> · <HH:MM PT>`,
where the digest is the first 12 hex of a sha256 over the sorted left-row
targets of every leg. It posts when an action was executed or the digest
differs from the newest summary's, and posts nothing otherwise — a left row is
reported when it appears and when it goes, never every hour in between. The
summary is a report, not an act: the registry's `unconverted` block declares it
`not-an-act`.

Environment: `HYGIENE_CARD`, `HYGIENE_BUDGET_FLOOR` (default 100),
`HYGIENE_DRY_RUN` (`1` = dry run), plus `LINEAR_API_KEY`, and `GH_TOKEN` (the
owner's App token) for `run`.
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    # A lane module does `import hygiene`. Run as a script this module is
    # `__main__`, and without this the lane would load a SECOND copy whose
    # `Action` is not the class the executor checks against.
    sys.modules.setdefault("hygiene", sys.modules[__name__])

import argparse  # noqa: E402
import contextlib  # noqa: E402
import dataclasses  # noqa: E402
import glob  # noqa: E402
import hashlib  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shlex  # noqa: E402
import subprocess  # noqa: E402
from collections.abc import Callable, Iterator  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_snapshot  # noqa: E402 — the one board read, and the read-only seam
import github_output  # noqa: E402
import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import reconcile  # noqa: E402 — gate_workflow(), the gate stub's rule

ROOT = Path(__file__).resolve().parent.parent

#: The lanes the board read covers. `Hand-work` joins them when the contract
#: declares it (`board_lanes`); until then it is absent and skipped, never an
#: error.
LANES = ("Green Light", "Todo", "Triage", "In Progress", "In Review")
HAND_WORK = "Hand-work"

#: Where lane modules are discovered, and the glob that finds them.
LANE_DIR = Path(__file__).resolve().parent
LANE_GLOB = "hygiene_*.py"

#: The leg that takes the cards no `repo:` label scopes — no label at all, or a
#: slug the repo map does not hold.
HOME_OWNER = "dreadnought-foundry"

REPO_MAP_PATH = ROOT / "config" / "repo-map.json"

DEFAULT_FLOOR = 100

#: The open pull requests every lane reads, one `gh pr list` per repo.
PR_FIELDS = ("number", "title", "body", "headRefName", "headRefOid",
             "baseRefName", "mergeStateStatus", "isDraft", "updatedAt",
             "comments", "statusCheckRollup")
#: `gh pr list` answers 30 rows unless told otherwise, and a silent first page
#: is the failure DRE-2681 named.
PR_LIMIT = 1000

#: Act name → tag. The ONE table: `config/pipeline-acts.json` pins each tag's
#: literal here as its anchor, so a tag spelled twice in this file fails
#: `pipeline_act.py check`.
TAGS = {
    "hygiene-gate-redispatch": "hyg-gate-redispatched",
    "hygiene-branch-refresh": "hyg-branch-refreshed",
    "hygiene-check-rerun": "hyg-check-rerun",
    "hygiene-decision-needed": "hyg-decision-needed",
    "hygiene-pr-close": "hyg-pr-closed",
    "hygiene-resend-to-planning": "hyg-resent-to-planning",
    "hygiene-card-close": "hyg-card-closed",
    "hygiene-proof-close": "hyg-proof-closed",
    "hygiene-triage-return": "hyg-triage-returned",
    "hygiene-review-move": "hyg-moved-to-review",
    "hygiene-card-cancel": "hyg-card-canceled",
    "hygiene-cause-name": "hyg-cause-named",
}
_ACT_OF_TAG = {tag: act for act, tag in TAGS.items()}

RECEIPT_MARK = "🧹 hygiene:"
SUMMARY_MARK = f"{RECEIPT_MARK} summary"
_SEPARATOR = " · "
_EVIDENCE = "evidence: "
_PT = ZoneInfo("America/Los_Angeles")

#: A receipt's first line. The cause may carry anything but `·` (refused by
#: `receipt`), so the last ` · ` is the clock's and the line reads back whole.
_RECEIPT_LINE = re.compile(
    rf"^{re.escape(RECEIPT_MARK)} (?P<tag>[a-z0-9-]+) — (?P<cause>.+)"
    rf"{re.escape(_SEPARATOR)}(?P<clock>\d\d:\d\d PT)$"
)
_SUMMARY_LINE = re.compile(rf"^{re.escape(SUMMARY_MARK)} (?P<digest>[0-9a-f]{{12}})\b")

#: The lanes a state write may move a card to — exactly the lanes whose
#: `writers` clause in `config/lane-contract.json` names `hygiene.py`.
DESTINATIONS = ("Planning", "Backlog", "In Review", "Done", "Canceled")
#: The lanes the guard names when it refuses one: never written by this agent.
REFUSED_LANES = ("In Progress", "Todo", "Green Light", "Triage", "Intake")
#: The lanes that close a card, and so refuse a card with children (an epic).
CLOSING_LANES = ("Done", "Canceled")

#: The read-only `gh`: these verbs, and `gh api` carrying none of the flags
#: that make it a write.
READ_VERBS = (("pr", "list"), ("pr", "view"), ("pr", "checks"),
              ("run", "list"), ("run", "view"))
_API_WRITE_LONG = ("--method", "--field", "--raw-field", "--input")
_API_WRITE_SHORT = ("-X", "-f", "-F")

GH_KINDS = ("gh_dispatch", "gh_rerun", "gh_update_branch", "gh_pr_close", "gh_pr_comment")
LINEAR_KINDS = ("linear_state", "linear_comment", "linear_label", "linear_relation")


class Forbidden(RuntimeError):
    """A write, or a read, the hygiene agent may never make. Raised before
    anything is sent."""


def destinations() -> tuple:
    """The lanes this module can write — published for
    `ready_lane_writers.py`, which cannot read a destination the executor
    takes off a `Write` at run time."""
    return DESTINATIONS


def pt(now: datetime) -> str:
    """`now` as the only clock a reader of this pipeline uses: `HH:MM PT`."""
    return now.astimezone(_PT).strftime("%H:%M PT")


def _utc_iso(now: datetime) -> str:
    return now.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# the shapes a lane reads and returns                                          #
# --------------------------------------------------------------------------- #


def _labels(card: dict) -> list:
    return [n.get("name") or "" for n in ((card.get("labels") or {}).get("nodes") or [])]


def _repo_slug(card: dict) -> str | None:
    for name in _labels(card):
        if name.startswith("repo:"):
            return name[len("repo:"):].strip().lower()
    return None


def in_scope(ctx: Context, card: dict) -> bool:
    """Is this card this leg's? Its `repo:` label maps to one of `ctx.repos`;
    a card with no label, or a slug the map does not hold, is the home leg's.
    The standing summary card is nobody's."""
    if ctx.summary_card and card.get("identifier") == ctx.summary_card:
        return False
    slug = _repo_slug(card)
    repo = ctx.repo_map.get(slug) if slug else None
    if repo is None:
        return ctx.owner.lower() == HOME_OWNER
    return repo in ctx.repos


@dataclass
class Board:
    """`lanes`: cards by lane, in the `CARD_QUERY` shape. `prs`: open pull
    requests by `owner/repo`, in the `PR_FIELDS` shape."""

    lanes: dict
    prs: dict = field(default_factory=dict)

    def cards(self, ctx: Context, lane: str) -> Iterator[dict]:
        """The cards of `lane` in this leg's scope — never the summary card."""
        for card in self.lanes.get(lane) or []:
            if in_scope(ctx, card):
                yield card


@dataclass
class Context:
    owner: str
    repos: set
    repo_map: dict
    dry_run: bool
    now: datetime
    summary_card: str | None
    gh: Callable
    linear: Callable


@dataclass(frozen=True)
class Write:
    """One write. Built by the constructors below and by nothing else — a lane
    never builds a `gh` argv for a write, and the guard reads the argv's shape
    to prove it."""

    kind: str
    target: str
    card: str | None = None
    card_id: str | None = None
    children: tuple | None = None
    labels: tuple = ()
    lane: str | None = None
    park: bool = False
    label: str | None = None
    add: bool | None = None
    blocked_by: str | None = None
    repo: str | None = None
    workflow: str | None = None
    body: str | None = None
    argv: tuple | None = None

    def describe(self) -> str:
        """One line: what this write does, for the ledger and for `would:`."""
        if self.kind == "linear_state":
            return f"state {self.card} → {self.lane}" + (" --park" if self.park else "")
        if self.kind == "linear_comment":
            return f"comment {self.card}: {(self.body or '').splitlines()[0]}"
        if self.kind == "linear_label":
            return f"label {self.card} {'+' if self.add else '−'} {self.label}"
        if self.kind == "linear_relation":
            return f"relation {self.card} blocked by {self.blocked_by}"
        shown = [a.splitlines()[0] if a == self.body else a for a in self.argv or ()]
        return shlex.join(shown)


@dataclass
class Action:
    lane: str
    target: str
    act: str
    cause: str
    evidence: list
    writes: list


@dataclass
class Left:
    lane: str
    target: str
    why: str
    recommendation: str


# --------------------------------------------------------------------------- #
# receipts                                                                     #
# --------------------------------------------------------------------------- #


def _act_tag(act: str) -> str:
    if act not in TAGS:
        raise Forbidden(
            f"{act!r} is not a hygiene act — the agent writes receipts only for "
            f"the acts its table declares: {', '.join(TAGS)}"
        )
    return TAGS[act]


def _check_cause(cause: str) -> None:
    if not (cause or "").strip():
        raise ValueError("a hygiene receipt names its cause — an empty one keys nothing")
    if "\n" in cause or "\r" in cause:
        raise ValueError("a hygiene cause is one line — it is half the idempotency key")
    if "·" in cause:
        raise ValueError(
            f"the cause {cause!r} carries '·', which separates the clock on a "
            "receipt's first line — it would not read back unambiguously"
        )


def _clock(now: datetime | str) -> str:
    return now if isinstance(now, str) else pt(now)


def receipt(act: str, cause: str, evidence: list, now: datetime | str) -> str:
    """Every comment the agent writes, composed here and never by hand:

        🧹 hygiene: <tag> — <cause> · <HH:MM PT>
        evidence: <item>, <item>

    and the act's `📎 pipeline-act:` trailer, through `pipeline_act.receipt`.
    `now` is the pass's clock, or the `HH:MM PT` it already rendered to — the
    seam recomposes a receipt it read back, to prove it was composed here."""
    tag = _act_tag(act)
    _check_cause(cause)
    items = [str(e) for e in evidence or []]
    if not items or any(not e.strip() or "\n" in e for e in items):
        raise ValueError(
            f"a {act} receipt names the evidence it read — one line per item, "
            "at least one item"
        )
    detail = (f"{RECEIPT_MARK} {tag} — {cause}{_SEPARATOR}{_clock(now)}\n"
              f"{_EVIDENCE}{', '.join(items)}")
    return pipeline_act.receipt(act, detail)


def read_receipt(body: str) -> dict | None:
    """A hygiene receipt's tag, cause and clock off its first line, or None."""
    match = _RECEIPT_LINE.match((body or "").splitlines()[0] if body else "")
    if match is None or match.group("tag") not in _ACT_OF_TAG:
        return None
    return {"tag": match.group("tag"), "cause": match.group("cause"),
            "clock": match.group("clock")}


def _receipt_parts(body: str) -> tuple:
    """The `receipt()` arguments that compose exactly `body` — refused when
    nothing would: a body written by hand is not a hygiene receipt, whatever
    its first line says."""
    head = read_receipt(body)
    lines = (body or "").splitlines()
    if head is None or len(lines) < 2 or not lines[1].startswith(_EVIDENCE):
        raise Forbidden("every comment the hygiene agent writes is composed by hygiene.receipt")
    parts = (_ACT_OF_TAG[head["tag"]], head["cause"],
             lines[1][len(_EVIDENCE):].split(", "), head["clock"])
    try:
        composed = receipt(*parts)
    except ValueError as e:
        raise Forbidden(f"not a hygiene receipt: {e}") from e
    if composed != body:
        raise Forbidden("every comment the hygiene agent writes is composed by hygiene.receipt")
    return parts


# --------------------------------------------------------------------------- #
# the constructors — the only writes a lane may return                         #
# --------------------------------------------------------------------------- #


def _card_fields(card: dict) -> dict:
    if not isinstance(card, dict) or not card.get("identifier"):
        raise Forbidden(
            "a Linear write takes the card dict a lane was given (identifier, "
            "state, children) — never a bare identifier"
        )
    children = card.get("children")
    nodes = None if children is None else tuple(
        n.get("identifier") for n in (children.get("nodes") or [])
    )
    return {"target": card["identifier"], "card": card["identifier"],
            "card_id": card.get("id"), "children": nodes,
            "labels": tuple(_labels(card))}


def linear_state(card: dict, lane: str, park: bool = False) -> Write:
    return Write("linear_state", lane=lane, park=bool(park), **_card_fields(card))


def linear_comment(card: dict, body: str) -> Write:
    _receipt_parts(body)  # refuses a comment written by hand
    return Write("linear_comment", body=body, **_card_fields(card))


def linear_label(card: dict, label: str, add: bool) -> Write:
    return Write("linear_label", label=label, add=bool(add), **_card_fields(card))


def linear_relation(card: dict, blocked_by: str | dict) -> Write:
    blocker = blocked_by.get("identifier") if isinstance(blocked_by, dict) else blocked_by
    return Write("linear_relation", blocked_by=blocker, **_card_fields(card))


def _gh_write(argv: list, kind: str, repo: str, target: str,
              workflow: str | None = None, body: str | None = None) -> Write:
    return Write(kind, target=target, repo=repo, workflow=workflow, body=body,
                 argv=tuple(argv))


def gh_dispatch(repo: str, workflow: str, inputs: dict) -> Write:
    argv = ["gh", "workflow", "run", workflow, "--repo", repo]
    for key, value in (inputs or {}).items():
        argv += ["-f", f"{key}={value}"]
    return _gh_write(argv, "gh_dispatch", repo, repo, workflow=workflow)


def gh_rerun(repo: str, run_id: int | str) -> Write:
    return _gh_write(["gh", "run", "rerun", str(run_id), "--failed", "--repo", repo],
                     "gh_rerun", repo, f"{repo} run {run_id}")


def gh_update_branch(repo: str, number: int | str, expected_head_sha: str) -> Write:
    return _gh_write(["gh", "api", "-X", "PUT", f"repos/{repo}/pulls/{number}/update-branch",
                      "-f", f"expected_head_sha={expected_head_sha}"],
                     "gh_update_branch", repo, f"{repo}#{number}")


def gh_pr_close(repo: str, number: int | str, comment: str) -> Write:
    # Recomposed from its own parts, which refuses a comment written by hand.
    composed = receipt(*_receipt_parts(comment))
    return _gh_write(["gh", "pr", "close", str(number), "--repo", repo, "--comment", composed],
                     "gh_pr_close", repo, f"{repo}#{number}", body=composed)


def gh_pr_comment(repo: str, number: int | str, body: str) -> Write:
    composed = receipt(*_receipt_parts(body))
    return _gh_write(["gh", "pr", "comment", str(number), "--repo", repo, "--body", composed],
                     "gh_pr_comment", repo, f"{repo}#{number}", body=composed)


# --------------------------------------------------------------------------- #
# the guard — by shape                                                         #
# --------------------------------------------------------------------------- #

_REPO = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
_NUMBER = r"[0-9]+"
_INPUT = r"[A-Za-z_][A-Za-z0-9_]*=.*"


def _full(pattern: str, value: str) -> bool:
    return re.fullmatch(pattern, value, flags=re.DOTALL) is not None


def gh_shape(argv: tuple) -> str:
    """Which constructor built this argv — read off its verb, its flags and
    its REST path, position by position — or `Forbidden`.

    Nothing here searches for a word. `gh workflow run self-merge-gate.yml`
    is a dispatch of a gate stub whatever else its filename says, a dispatch
    of any other workflow is not a shape at all, and an argv that is not
    exactly one of the five shapes is refused whatever it says."""
    a = tuple(str(x) for x in argv or ())
    n = len(a)
    if a[:3] == ("gh", "workflow", "run") and n >= 6 and n % 2 == 0:
        if (a[3] in gate_stubs() and a[4] == "--repo" and _full(_REPO, a[5])
                and all(a[i] == "-f" and _full(_INPUT, a[i + 1]) for i in range(6, n, 2))):
            return "gh_dispatch"
    if a[:3] == ("gh", "run", "rerun") and n == 7:
        if _full(_NUMBER, a[3]) and a[4:6] == ("--failed", "--repo") and _full(_REPO, a[6]):
            return "gh_rerun"
    if a[:4] == ("gh", "api", "-X", "PUT") and n == 7:
        if (_full(rf"repos/{_REPO}/pulls/{_NUMBER}/update-branch", a[4]) and a[5] == "-f"
                and _full(r"expected_head_sha=[0-9a-fA-F]+", a[6])):
            return "gh_update_branch"
    if a[:3] == ("gh", "pr", "close") and n == 8:
        if _full(_NUMBER, a[3]) and a[4] == "--repo" and _full(_REPO, a[5]) and a[6] == "--comment":
            return "gh_pr_close"
    if a[:3] == ("gh", "pr", "comment") and n == 8:
        if _full(_NUMBER, a[3]) and a[4] == "--repo" and _full(_REPO, a[5]) and a[6] == "--body":
            return "gh_pr_comment"
    raise Forbidden(
        f"{shlex.join(a) if a else '<empty argv>'} is not a write any hygiene "
        "constructor builds — refused before it was sent"
    )


def _argv_repo(kind: str, argv: tuple) -> str:
    if kind == "gh_update_branch":
        return "/".join(argv[4].split("/")[1:3])
    return argv[argv.index("--repo") + 1]


@contextlib.contextmanager
def _sweeping(slug: str):
    """`reconcile.gate_workflow()` answers for the repo its sweep runs in;
    this asks it about `slug` instead, and puts the sweep's own back."""
    saved = reconcile.REPO_SLUG
    reconcile.REPO_SLUG = slug
    try:
        yield
    finally:
        reconcile.REPO_SLUG = saved


def gate_stub(repo: str) -> str:
    """The one workflow the agent may dispatch in `repo`: its gate stub, by
    `reconcile.gate_workflow()`'s rule rather than a second copy of it."""
    with _sweeping(repo.split("/", 1)[-1].lower()):
        return reconcile.gate_workflow()


def gate_stubs() -> set:
    """Every workflow a dispatch may name in any repo the map holds — the
    shape's half of the dispatch rule; `guard` holds a write to its own repo's
    stub."""
    return {gate_stub(repo) for repo in load_repo_map().values()}


def guard(write: Write, ctx: Context) -> None:
    """Refuse, by raising `Forbidden`, any write the agent may never send."""
    if write.kind in LINEAR_KINDS:
        _guard_linear(write, ctx)
    elif write.kind in GH_KINDS:
        _guard_gh(write, ctx)
    else:
        raise Forbidden(f"{write.kind!r} is not a hygiene write")
    if write.body is not None:
        _receipt_parts(write.body)


def _guard_linear(write: Write, ctx: Context) -> None:
    if ctx.summary_card and write.card == ctx.summary_card:
        raise Forbidden(f"{write.card} is the standing summary card — no lane acts on it")
    scoped = {"identifier": write.card,
              "labels": {"nodes": [{"name": n} for n in write.labels]}}
    if not in_scope(ctx, scoped):
        raise Forbidden(f"{write.card} is not in the {ctx.owner} leg's scope")
    if write.kind == "linear_state":
        if write.lane not in DESTINATIONS:
            raise Forbidden(
                f"the hygiene agent never moves a card to {write.lane!r} — "
                f"{', '.join(REFUSED_LANES)} are refused, and it writes only "
                f"{', '.join(DESTINATIONS)}"
            )
        if write.lane in CLOSING_LANES and write.children != ():
            raise Forbidden(
                f"{write.card} has children, or did not say whether it has any — "
                f"the hygiene agent never moves an epic to {write.lane}"
            )
    elif write.kind == "linear_label":
        refusal = linear_ops.agent_label_refusal(write.label or "")
        if refusal is not None:
            raise Forbidden(refusal)


def _guard_gh(write: Write, ctx: Context) -> None:
    kind = gh_shape(write.argv)
    if kind != write.kind:
        raise Forbidden(f"a {write.kind} write carries a {kind} argv")
    repo = _argv_repo(kind, write.argv)
    if repo != write.repo:
        raise Forbidden(f"a write for {write.repo} carries an argv for {repo}")
    if repo not in ctx.repos:
        raise Forbidden(f"{repo} is not one of the {ctx.owner} leg's repos")
    if kind == "gh_dispatch":
        workflow = write.argv[3]
        if workflow != write.workflow or workflow != gate_stub(repo):
            raise Forbidden(
                f"the hygiene agent dispatches only {repo}'s gate stub, "
                f"{gate_stub(repo)} — never {workflow}"
            )
    if kind in ("gh_pr_close", "gh_pr_comment") and write.argv[7] != write.body:
        raise Forbidden("the comment the argv carries is not the write's receipt")


# --------------------------------------------------------------------------- #
# the read-only wrappers a lane is handed                                      #
# --------------------------------------------------------------------------- #


def read_gh_refusal(argv: list) -> str | None:
    """Why this `gh` argv is not a read, or None. Reads the verb and the
    flags, never the bytes of a value."""
    a = [str(x) for x in argv or []]
    if a[:1] != ["gh"] or len(a) < 2:
        return f"{shlex.join(a)} is not a gh read"
    if tuple(a[1:3]) in READ_VERBS:
        return None
    if a[1] == "api":
        for arg in a[2:]:
            if arg in _API_WRITE_LONG or any(arg.startswith(f + "=") for f in _API_WRITE_LONG):
                return f"gh api with {arg} can write"
            if any(arg.startswith(f) for f in _API_WRITE_SHORT):
                return f"gh api with {arg} can write"
        return None
    return f"{shlex.join(a)} is not one of the read verbs"


def read_only_gh(runner: Callable) -> Callable:
    def gh(argv: list) -> str:
        refusal = read_gh_refusal(argv)
        if refusal is not None:
            raise Forbidden(f"ctx.gh is read-only: {refusal}")
        return runner(list(argv))
    return gh


def read_only_linear(gql: Callable) -> Callable:
    """`linear_ops.gql`, refusing a mutation, every call counted."""
    calls: list = []

    def linear(query: str, variables: dict | None = None) -> dict:
        board_snapshot.assert_read_only(query)
        calls.append(query)
        return gql(query, variables)

    linear.calls = calls
    return linear


def _gh_subprocess(argv: list) -> str:
    """`gh`, as this leg's App token (`GH_TOKEN` in the job's environment)."""
    done = subprocess.run(list(argv), capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"{shlex.join(argv)} exited {done.returncode}: {done.stderr.strip()}")
    return done.stdout


#: The one process boundary to `gh`, reads and writes alike. Replaced in tests.
GH_RUNNER: Callable = _gh_subprocess


def load_repo_map(path: Path | None = None) -> dict:
    with open(path or REPO_MAP_PATH, encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


def owner_repos(owner: str, repo_map: dict) -> set:
    return {repo for repo in repo_map.values() if repo.split("/")[0].lower() == owner.lower()}


def make_context(owner: str, *, gh: Callable | None = None, linear: Callable | None = None,
                 dry_run: bool = False, now: datetime | None = None,
                 summary_card: str | None = None, repo_map: dict | None = None) -> Context:
    repo_map = dict(repo_map if repo_map is not None else load_repo_map())
    return Context(
        owner=owner,
        repos=owner_repos(owner, repo_map),
        repo_map=repo_map,
        dry_run=dry_run,
        now=now or datetime.now(UTC),
        summary_card=summary_card or None,
        gh=gh if gh is not None else read_only_gh(lambda argv: GH_RUNNER(argv)),
        linear=linear if linear is not None else read_only_linear(linear_ops.gql),
    )


def pr_list_argv(repo: str) -> list:
    return ["gh", "pr", "list", "--repo", repo, "--state", "open",
            "--json", ",".join(PR_FIELDS), "--limit", str(PR_LIMIT)]


# --------------------------------------------------------------------------- #
# read                                                                         #
# --------------------------------------------------------------------------- #


def board_lanes(contract: dict | None = None) -> tuple:
    declared = lane_contract.lane_names("live", contract)
    return LANES + ((HAND_WORK,) if HAND_WORK in declared else ())


def _output(pairs: list) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(github_output.render(pairs))


def cmd_read(out: str) -> int:
    lanes = board_lanes()
    try:
        cards, _requests = board_snapshot.read_board(lanes)
    except linear_ops.LinearError as e:
        why = f"hygiene: the board could not be read — {e}"
        print(why, file=sys.stderr)
        _output([("go", "false"), ("why", why.splitlines()[0])])
        return 1
    by_lane: dict = {lane: [] for lane in lanes}
    for card in cards:
        by_lane.setdefault((card.get("state") or {}).get("name"), []).append(card)
    Path(out).write_text(json.dumps({"taken_at": _utc_iso(datetime.now(UTC)),
                                     "lanes": by_lane}), encoding="utf-8")
    floor = int(os.environ.get("HYGIENE_BUDGET_FLOOR") or DEFAULT_FLOOR)
    remaining = linear_ops._budget["last"]
    if remaining is None:
        go, why = True, "hygiene: floor not applied — no rate-limit header on the board read"
    elif remaining < floor:
        go, why = False, (f"hygiene: stood down — {remaining} request(s) left on the "
                          f"fleet key, below the floor of {floor}")
    else:
        go, why = True, (f"hygiene: {remaining} request(s) left on the fleet key, at or "
                         f"above the floor of {floor}")
    print(why)
    _output([("go", "true" if go else "false"), ("why", why)])
    return 0


# --------------------------------------------------------------------------- #
# run                                                                          #
# --------------------------------------------------------------------------- #


def discover(lane_dir: Path | str | None = None) -> list:
    """Every lane module the glob finds, loaded fresh, in filename order."""
    directory = Path(lane_dir if lane_dir is not None else LANE_DIR)
    modules = []
    for path in sorted(glob.glob(str(directory / LANE_GLOB))):
        name = Path(path).stem
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    return modules


def _comment_bodies(nodes) -> list:
    return [(n or {}).get("body") or "" for n in nodes or []]


def _receipts_on(board: Board, target: str) -> list:
    """Every comment body the target carries: the pull request's comments, or
    the card's comment window off the board read.

    A card the board does not carry — one a lane read for itself through
    `ctx.linear` — or one whose window Linear cut short (`hasNextPage`) is
    read whole instead: a receipt older than the newest fifty is still a
    receipt, and a key that could not see it would send the act again."""
    repo, _, number = target.rpartition("#")
    if repo:
        return [body for pull in board.prs.get(repo, [])
                if str(pull.get("number")) == number
                for body in _comment_bodies(pull.get("comments"))]
    cards = [c for cards in board.lanes.values() for c in cards
             if c.get("identifier") == target]
    if cards and not any(linear_ops.window_is_partial(c.get("comments")) for c in cards):
        return [body for c in cards
                for body in _comment_bodies((c.get("comments") or {}).get("nodes"))]
    return [r.get("body") or "" for r in linear_ops.comment_records(target, whole_thread=True)]


def _suppressed(board: Board, action: Action) -> bool:
    """The idempotency key: a receipt on the target with this action's tag
    AND this action's cause line."""
    tag = TAGS[action.act]
    for body in _receipts_on(board, action.target):
        head = read_receipt(body)
        if head and head["tag"] == tag and head["cause"] == action.cause:
            return True
    return False


def send(write: Write, ctx: Context) -> None:
    """THE write seam: every write the agent makes leaves through here, after
    the guard."""
    if write.kind == "linear_state":
        flags = ("--park",) if write.park else ()
        linear_ops.cmd_state(write.card, write.lane, *flags)
    elif write.kind == "linear_comment":
        composed = receipt(*_receipt_parts(write.body))
        refused = linear_ops.cmd_comment(write.card, composed)
        if refused is not None:
            raise RuntimeError(f"the comment on {write.card} did not land: {refused}")
    elif write.kind == "linear_label":
        (linear_ops.add_label if write.add else linear_ops.remove_label)(write.card, write.label)
    elif write.kind == "linear_relation":
        card_id = write.card_id or linear_ops.get_issue(write.card)["id"]
        linear_ops._add_blocked_by(card_id, [write.blocked_by])
    elif write.kind in GH_KINDS:
        GH_RUNNER(list(write.argv))
    else:  # pragma: no cover — the guard refused it already
        raise Forbidden(f"{write.kind!r} is not a hygiene write")


def _plan(board: Board, ctx: Context) -> list:
    items: list = []
    for module in discover():
        for item in module.plan(board, ctx) or []:
            if not isinstance(item, (Action, Left)):
                raise TypeError(
                    f"{module.__name__}.plan returned {type(item).__name__}; a lane "
                    "returns hygiene.Action and hygiene.Left rows only"
                )
            items.append(item)
    return items


def run_leg(board_doc: dict, ctx: Context) -> dict:
    """One owner's leg: the board in scope, every lane's plan, and the ledger
    of what was done and what was left."""
    lanes = {
        lane: [c for c in cards if not (ctx.summary_card and c.get("identifier") == ctx.summary_card)]
        for lane, cards in (board_doc.get("lanes") or {}).items()
    }
    prs = {repo: json.loads(ctx.gh(pr_list_argv(repo)) or "[]") for repo in sorted(ctx.repos)}
    board = Board(lanes=lanes, prs=prs)
    items = _plan(board, ctx)
    actions = [i for i in items if isinstance(i, Action)]
    # Every write of every action is guarded before ANY is sent: a lane that
    # proposes one forbidden write has a bug, and its other writes are not
    # trusted either.
    for action in actions:
        _act_tag(action.act)
        _check_cause(action.cause)
        for write in action.writes:
            if not isinstance(write, Write):
                raise TypeError("an action's writes are hygiene.Write values only")
            guard(write, ctx)
    ledger_actions = []
    for action in actions:
        outcome = _execute(board, action, ctx)
        ledger_actions.append({
            "lane": action.lane, "target": action.target, "act": action.act,
            "cause": action.cause, "evidence": list(action.evidence),
            "writes": [w.describe() for w in action.writes], "outcome": outcome,
        })
    return {
        "owner": ctx.owner,
        "taken_at": _utc_iso(ctx.now),
        "actions": ledger_actions,
        "left": [dataclasses.asdict(i) for i in items if isinstance(i, Left)],
    }


def _execute(board: Board, action: Action, ctx: Context) -> str:
    if _suppressed(board, action):
        print(f"suppressed: {action.target} {TAGS[action.act]} — {action.cause}")
        return "suppressed"
    if ctx.dry_run:
        for write in action.writes:
            print(f"would: {write.describe()}")
        return "would"
    for write in action.writes:
        try:
            guard(write, ctx)
            send(write, ctx)
        except Exception as e:  # noqa: BLE001 — recorded, and the leg goes red
            print(f"failed: {write.describe()} — {e}", file=sys.stderr)
            return "failed"
    return "executed"


def cmd_run(board_path: str, owner: str, ledger_path: str, dry_run: bool) -> int:
    with open(board_path, encoding="utf-8") as fh:
        board_doc = json.load(fh)
    dry_run = dry_run or os.environ.get("HYGIENE_DRY_RUN") == "1"
    ctx = make_context(owner, dry_run=dry_run,
                       summary_card=os.environ.get("HYGIENE_CARD") or None)
    try:
        ledger = run_leg(board_doc, ctx)
    except Forbidden as e:
        print(f"hygiene: refused — {e}; nothing was sent", file=sys.stderr)
        return 3
    Path(ledger_path).write_text(json.dumps(ledger, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    failed = [a for a in ledger["actions"] if a["outcome"] == "failed"]
    print(f"hygiene: {owner} — {len(ledger['actions'])} action(s), "
          f"{len(failed)} failed, {len(ledger['left'])} left")
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
# summarize                                                                    #
# --------------------------------------------------------------------------- #


def digest(targets) -> str:
    """The first 12 hex of a sha256 over the sorted left-row targets."""
    return hashlib.sha256("\n".join(sorted(set(targets))).encode()).hexdigest()[:12]


def newest_summary_digest(card: str) -> str | None:
    """One read of the standing card's comment window: the digest on its
    newest comment opening `SUMMARY_MARK`, or None when it has none."""
    data = linear_ops.gql(
        "query($id: String!) { issue(id: $id) { %s } }" % linear_ops.COMMENT_WINDOW_GQL,
        {"id": card},
    )
    nodes = ((data.get("issue") or {}).get("comments") or {}).get("nodes") or []
    for node in nodes:  # newest first, as Linear answers the window
        match = _SUMMARY_LINE.match(node.get("body") or "")
        if match:
            return match.group("digest")
    return None


def render_summary(ledgers: list, now: datetime) -> str:
    left = [row for ledger in ledgers for row in ledger.get("left") or []]
    lines = [f"{SUMMARY_MARK} {digest(r['target'] for r in left)}{_SEPARATOR}{pt(now)}"]
    by_lane: dict = {}
    for ledger in ledgers:
        when = pt(datetime.fromisoformat(ledger["taken_at"].replace("Z", "+00:00")))
        for a in ledger.get("actions") or []:
            if a["outcome"] == "executed":
                by_lane.setdefault(a["lane"], []).append(
                    f"- cleared {a['target']} — {TAGS.get(a['act'], a['act'])}: "
                    f"{a['cause']} · {when}")
            elif a["outcome"] == "failed":
                by_lane.setdefault(a["lane"], []).append(
                    f"- failed {a['target']} — {TAGS.get(a['act'], a['act'])}: "
                    f"{a['cause']} · {when} — the {ledger['owner']} leg's log says why")
    for row in left:
        by_lane.setdefault(row["lane"], []).append(
            f"- left {row['target']} — {row['why']} — recommend: {row['recommendation']}")
    for lane, rows in by_lane.items():
        lines += ["", lane, *rows]
    if not by_lane:
        lines += ["", "Nothing cleared and nothing left."]
    return "\n".join(lines)


def cmd_summarize(paths: list, dry_run: bool) -> int:
    ledgers = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            ledgers.append(json.load(fh))
    summary_body = render_summary(ledgers, datetime.now(UTC))
    card = os.environ.get("HYGIENE_CARD") or None
    if card is None:
        print(summary_body)
        print("hygiene: HYGIENE_CARD is unset — the summary was printed here and posted nowhere")
        return 0
    executed = any(a.get("outcome") == "executed"
                   for ledger in ledgers for a in ledger.get("actions") or [])
    now_digest = _SUMMARY_LINE.match(summary_body).group("digest")
    if not executed and newest_summary_digest(card) == now_digest:
        print(f"hygiene: nothing changed — no action ran and the left set is {now_digest}")
        return 0
    if dry_run:
        print(f"would: comment {card}:\n{summary_body}")
        return 0
    linear_ops.cmd_comment(card, summary_body)
    return 0


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    read = sub.add_parser("read", help="read the board once and apply the budget floor")
    read.add_argument("--out", required=True)
    run = sub.add_parser("run", help="run every lane over one owner's leg")
    run.add_argument("--board", required=True)
    run.add_argument("--owner", required=True)
    run.add_argument("--ledger", required=True)
    run.add_argument("--dry-run", action="store_true")
    summarize = sub.add_parser("summarize", help="post the summary when something changed")
    summarize.add_argument("ledgers", nargs="+")
    summarize.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "read":
        return cmd_read(args.out)
    if args.command == "run":
        return cmd_run(args.board, args.owner, args.ledger, args.dry_run)
    return cmd_summarize(args.ledgers, args.dry_run
                         or os.environ.get("HYGIENE_DRY_RUN") == "1")


if __name__ == "__main__":
    sys.exit(main())
