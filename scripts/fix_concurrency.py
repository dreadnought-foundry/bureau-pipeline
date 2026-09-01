#!/usr/bin/env python3
"""The fix loop's concurrency grouping, and the runs it evicted (DRE-2810).

A pull request with a standing REQUEST_CHANGES sat 24 minutes with no fix agent
working it, and every run in the sequence reported an honest conclusion.
Observed live on PR #199 (DRE-2721), 2026-08-29:

    03:02:25  Agent Fix starts from a REQUEST_CHANGES verdict
    03:17:19  it pushes 47ef2102 (attempt 2)
    03:26:44  the critic reviews 47ef2102 -> REQUEST_CHANGES
    03:26:47  Agent Fix queued from that verdict — PENDING, because the
              03:02 run is still finishing
    03:32:14  the 03:02 run posts "🔧 Fix attempt 2 pushed" and succeeds
    03:32:16  that comment queues another Agent Fix run
    03:32:17  the 03:26 run is CANCELLED — evicted by the run one second
              younger
    03:32:18  the evicting run SKIPS: wrong author, no verdict

Two vendor facts collide (standards/vendor-boundaries.md Q1 and Q3):

  * **The fix agent's own notice is a trigger.** `gh pr comment` fires
    `issue_comment: created`, and the agent-fix stub triggers on exactly that
    — so the loop's success notice queues an Agent Fix run on the same PR.
  * **GitHub keeps at most ONE pending run per concurrency group.** With
    `cancel-in-progress: false` a newly queued run does not touch the run
    that is RUNNING; it cancels the one that is PENDING. The `if:` that
    skips the notice run is evaluated at the JOB level, long after the run
    has claimed the group's single pending slot.

So a run that will do nothing evicts the one that would have done the work,
and nothing fails: `success` for the fix, `cancelled` for the evicted trigger,
`skipped` for the evictor. No medic, no red-main repair, no alarm.

The fix is to give a run that will do nothing its own group — key the group on
the COMMENTER as well as the PR (`CANONICAL_GROUP` below). A qa-bot verdict
and a bot notice then queue separately, two verdicts on one PR still serialize
behind each other, and a hand `workflow_dispatch` (the documented recovery)
cannot drop a queued verdict either.

This module is the decision half, with no I/O:

  * `audit` / `audit_workflows_dir` — does a stub still evict its own next
    attempt? The group is written in EACH consumer repo's stub, so this runs
    where the stub lives: reconcile calls it every sweep, in every repo, and
    it is a CLI for anyone auditing a repo by hand.
  * `group` / `evicts` / `reaches_fix_agent` — evaluate the shipped group
    expression and the reusable workflow's job gate the way GitHub evaluates
    them, so a test can drive the live sequence above rather than a copy of it.
  * `trigger_kind` / `evictor_of` / `eviction_report` — read GitHub's run
    records and say which cancelled Agent Fix run dropped a verdict on the
    floor. A `cancelled` fix run is also the ordinary signature of a duplicate
    dispatch; the actor that triggered it is what tells the two apart.

CLI:

    python3 scripts/fix_concurrency.py audit [<path> ...]

with no argument it audits `.github/workflows` in the current repo.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: The identities that comment on a PR in the fix loop. Literal, and for the
#: same reason agent-fix.yml's job gate hardcodes the qa login: nothing here
#: mints a token just to learn a bot's own slug, so an App rename fails
#: LOUD (an unrecognised actor is reported, never silently reclassified).
#: The rename procedure in agent-fix.yml covers this file too.
QA_BOT_LOGIN = "agent-bureau-qa-bot[bot]"
WORKER_BOT_LOGIN = "agent-bureau-bot[bot]"

#: The context key every agent-fix stub's concurrency group MUST carry — the
#: whole fix in one string.
COMMENTER_KEY = "github.event.comment.user.login"

#: And the PR key it must keep: without it every PR in the repo shares one
#: group and a verdict on #199 evicts a verdict on #197.
PR_KEY = "github.event.issue.number"

#: The canonical group: two lanes per PR. Everything that can DO work — a
#: qa-bot verdict, and every workflow_dispatch (merge-gate's conflict route,
#: reconcile's backstops, the hand recovery) — shares the `work` lane, and
#: every other commenter gets a lane of their own. Product repos name their
#: stub agent-fix.yml and bureau-pipeline names its own self-agent-fix.yml;
#: both carry this.
CANONICAL_GROUP = (
    "agent-fix-${{ github.event.issue.number || inputs.pr_number }}"
    "-${{ (github.event_name != 'issue_comment' || github.event.comment.user.login"
    " == 'agent-bureau-qa-bot[bot]') && 'work' || github.event.comment.user.login }}"
)

#: How a caller recognises an agent-fix stub without knowing its filename.
_REUSABLE = "/agent-fix.yml@"


# ---------------------------------------------------------------------------
# Just enough GitHub expression to evaluate a concurrency group and a job gate
# ---------------------------------------------------------------------------
# Not a general implementation and not trying to be: it covers the operators
# the two shipped expressions actually use (`||`, `&&`, `==`, `!=`, `!`,
# `contains()`, parentheses, single-quoted literals, dotted context paths), so
# a test can put the live PR #199 events through the live YAML. Anything it
# cannot parse raises rather than guessing — a silently mis-evaluated gate
# would be a worse answer than no answer.

_TOKEN = re.compile(
    r"\|\||&&|==|!=|!|\(|\)|,|'(?:[^']|'')*'|[A-Za-z_][A-Za-z0-9_.]*|[0-9]+"
)


def _tokens(expr: str) -> list[str]:
    out, pos = [], 0
    while pos < len(expr):
        if expr[pos].isspace():
            pos += 1
            continue
        m = _TOKEN.match(expr, pos)
        if not m:
            raise ValueError(f"cannot tokenize GitHub expression at {expr[pos:pos + 30]!r}")
        out.append(m.group(0))
        pos = m.end()
    return out


def _truthy(value) -> bool:
    """GitHub's truthiness: null, false, 0 and the empty string are falsy;
    an object or array is truthy whether or not it has members."""
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        return value != ""
    return True


def _lookup(path: str, ctx: dict):
    node = ctx
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


class _Parser:
    def __init__(self, tokens: list[str], ctx: dict):
        self.toks, self.i, self.ctx = tokens, 0, ctx

    def peek(self) -> str | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> str:
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def parse(self):
        value = self.or_()
        if self.peek() is not None:
            raise ValueError(f"trailing tokens in expression: {self.toks[self.i:]}")
        return value

    def or_(self):
        value = self.and_()
        while self.peek() == "||":
            self.take()
            rhs = self.and_()
            if not _truthy(value):
                value = rhs
        return value

    def and_(self):
        value = self.eq()
        while self.peek() == "&&":
            self.take()
            rhs = self.eq()
            if _truthy(value):
                value = rhs
        return value

    def eq(self):
        value = self.unary()
        while self.peek() in ("==", "!="):
            op = self.take()
            rhs = self.unary()
            same = _loose_eq(value, rhs)
            value = same if op == "==" else not same
        return value

    def unary(self):
        if self.peek() == "!":
            self.take()
            return not _truthy(self.unary())
        return self.primary()

    def primary(self):
        tok = self.take()
        if tok == "(":
            value = self.or_()
            if self.take() != ")":
                raise ValueError("unbalanced parentheses in expression")
            return value
        if tok.startswith("'"):
            return tok[1:-1].replace("''", "'")
        if tok.isdigit():
            return int(tok)
        if tok in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[tok]
        if self.peek() == "(":  # a function call
            self.take()
            args = []
            while self.peek() != ")":
                args.append(self.or_())
                if self.peek() == ",":
                    self.take()
            self.take()
            return _call(tok, args)
        return _lookup(tok, self.ctx)


def _loose_eq(a, b) -> bool:
    """GitHub coerces across types when comparing; null equals the empty
    string, and numbers compare by value against their string form."""
    if a is None:
        a = "" if isinstance(b, str) else a
    if b is None:
        b = "" if isinstance(a, str) else b
    if isinstance(a, str) and isinstance(b, (int, float)):
        b = str(b)
    if isinstance(b, str) and isinstance(a, (int, float)):
        a = str(a)
    if isinstance(a, str) and isinstance(b, str):
        return a.lower() == b.lower()
    return a == b


def _call(name: str, args: list):
    if name == "contains":
        haystack, needle = args[0], args[1]
        if isinstance(haystack, (list, tuple)):
            return any(_loose_eq(item, needle) for item in haystack)
        return str(needle).lower() in str(haystack or "").lower()
    raise ValueError(f"unsupported function in expression: {name}()")


def evaluate(expr: str, ctx: dict):
    """Evaluate a bare GitHub expression (a job `if:`) against a context."""
    return _Parser(_tokens(expr), ctx).parse()


def _to_str(value) -> str:
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def interpolate(template: str, ctx: dict) -> str:
    """Resolve every `${{ ... }}` in a template string (a concurrency group)."""
    return re.sub(
        r"\$\{\{(.+?)\}\}",
        lambda m: _to_str(evaluate(m.group(1), ctx)),
        template,
    )


# ---------------------------------------------------------------------------
# The events the fix loop actually sees
# ---------------------------------------------------------------------------


def comment_event(pr_number: int, login: str, body: str = "") -> dict:
    """The context a stub sees for `issue_comment: created` on a PR."""
    return {
        "github": {
            "event_name": "issue_comment",
            "event": {
                "issue": {
                    "number": pr_number,
                    # Present iff the comment is on a PR — the gate's first
                    # clause reads its truthiness, not its contents.
                    "pull_request": {"url": f"https://api.github.com/pulls/{pr_number}"},
                },
                "comment": {"user": {"login": login}, "body": body},
            },
        },
        "inputs": {},
    }


def dispatch_event(pr_number: int) -> dict:
    """The context for the hand recovery: `gh workflow run ... -f pr_number=N`."""
    return {
        "github": {
            "event_name": "workflow_dispatch",
            "event": {"inputs": {"pr_number": str(pr_number)}},
        },
        "inputs": {"pr_number": str(pr_number)},
    }


# ---------------------------------------------------------------------------
# The stub's concurrency group
# ---------------------------------------------------------------------------


def group_of(doc: dict) -> str | None:
    """The workflow-level concurrency group expression, or None."""
    conc = (doc or {}).get("concurrency")
    if isinstance(conc, str):
        return conc
    if isinstance(conc, dict):
        return conc.get("group")
    return None


def cancel_in_progress(doc: dict) -> bool:
    conc = (doc or {}).get("concurrency")
    return bool(conc.get("cancel-in-progress")) if isinstance(conc, dict) else False


def resolve_group(expr: str, event: dict) -> str:
    return interpolate(expr, event)


def group(doc: dict, event: dict) -> str:
    expr = group_of(doc)
    if expr is None:
        raise ValueError("workflow has no concurrency group")
    return resolve_group(expr, event)


def evicts(doc: dict, pending: dict, arriving: dict) -> bool:
    """Would a run queued by `arriving` cancel a run left PENDING by `pending`?

    GitHub keeps at most one pending run per concurrency group, so the answer
    is exactly "do they resolve to the same group". This is the DRE-2810
    question, asked of a stub's real YAML.
    """
    return group(doc, pending) == group(doc, arriving)


def reaches_fix_agent(reusable_doc: dict, event: dict) -> bool:
    """Does this event get past agent-fix.yml's job gate to the fix agent?

    Surviving the concurrency slot is only half the requirement — the run has
    to still do the work. Read off the shipped `if:`, so the identity gate
    (DRE-1988) and this grouping are proved together.
    """
    job = ((reusable_doc or {}).get("jobs") or {}).get("fix") or {}
    condition = job.get("if")
    if not condition:
        raise ValueError("agent-fix.yml's fix job has no `if:` gate")
    return _truthy(evaluate(str(condition), event))


#: The PRs the audit probes with. Any two distinct numbers would do; these are
#: the two the live incident ran on.
_PROBE_PR, _OTHER_PR = 199, 197


def audit(doc: dict, name: str = "agent-fix stub") -> list[str]:
    """Everything wrong with this stub's grouping, in plain sentences.

    Empty list = the stub cannot evict its own next attempt, and cannot put
    two fix agents on one branch either.

    The audit EVALUATES the stub's real expression against the events the fix
    loop actually produces rather than matching its text, so a stub that
    reaches the right lanes by a different route passes and a stub that spells
    the keys but lands them wrong does not.
    """
    problems: list[str] = []
    expr = group_of(doc)
    if expr is None:
        return [
            f"{name} has no workflow-level concurrency group — every Agent Fix "
            "run for every PR competes in GitHub's default (per-workflow) "
            "queue."
        ]
    try:
        verdict = resolve_group(expr, comment_event(
            _PROBE_PR, QA_BOT_LOGIN, "VERDICT: REQUEST_CHANGES"))
        notice = resolve_group(expr, comment_event(
            _PROBE_PR, WORKER_BOT_LOGIN,
            "🔧 Fix attempt 2 pushed — CI and critic review re-running."))
        dispatch = resolve_group(expr, dispatch_event(_PROBE_PR))
        other_pr = resolve_group(expr, comment_event(
            _OTHER_PR, QA_BOT_LOGIN, "VERDICT: REQUEST_CHANGES"))
    except Exception as exc:  # noqa: BLE001 — an unevaluable group is a finding
        return [
            f"{name}'s concurrency group could not be evaluated ({exc}), so "
            "nothing here can say which runs it queues together."
        ]
    if verdict == notice:
        problems.append(
            f"{name}'s concurrency group puts a bot notice and a qa-bot "
            f"REQUEST_CHANGES verdict in the same group ({verdict!r}): the fix "
            "loop's own '🔧 Fix attempt N pushed' comment queues a run there "
            "and GitHub cancels the pending verdict trigger before it starts "
            f"(DRE-2810). Key the group on {COMMENTER_KEY} — use: group: "
            f"{CANONICAL_GROUP}"
        )
    if verdict != dispatch:
        problems.append(
            f"{name}'s concurrency group separates a qa-bot verdict "
            f"({verdict!r}) from a workflow_dispatch ({dispatch!r}), so "
            "merge-gate's conflict route can start a second fix agent on a "
            "branch one is already working (DRE-2810). Both are work; they "
            "belong in one lane."
        )
    if verdict == other_pr:
        problems.append(
            f"{name}'s concurrency group does not key on {PR_KEY} — one PR's "
            "fix run would evict another PR's."
        )
    if cancel_in_progress(doc):
        problems.append(
            f"{name} sets cancel-in-progress: true — a second verdict would "
            "kill a fix agent mid-work instead of queueing behind it."
        )
    return problems


def calls_agent_fix(doc: dict) -> bool:
    """Is this workflow an agent-fix stub? Read off the reusable it calls, so
    the audit does not depend on a filename (product repos say agent-fix.yml,
    bureau-pipeline says self-agent-fix.yml)."""
    for job in ((doc or {}).get("jobs") or {}).values():
        if isinstance(job, dict) and _REUSABLE in str(job.get("uses") or ""):
            return True
    return False


def _load(path: Path) -> dict | None:
    # PyYAML ships in the runner image, and the sweep installs nothing — the
    # model_fallback.py precedent. An import that fails is reported by the
    # caller as unreadable, never as a pass.
    import yaml  # noqa: PLC0415

    doc = yaml.safe_load(path.read_text())
    return doc if isinstance(doc, dict) else None


#: The two filenames a fix stub can have — product repos say agent-fix.yml,
#: bureau-pipeline says self-agent-fix.yml (that name IS the reusable there;
#: reconcile.fix_workflow() makes the same resolution). Used only to decide
#: whether an UNPARSEABLE file might have been the stub.
_STUB_NAMES = ("agent-fix.yml", "self-agent-fix.yml")


def audit_workflows_dir(directory: Path | str) -> dict[str, list[str]]:
    """Audit every agent-fix stub in a workflows directory.

    Returns {filename: problems}; an empty dict means the repo has no
    agent-fix stub at all, which is a legitimate answer (bureau-harness has
    none — DRE-2525) and never a finding.

    Raises if YAML itself is unavailable — an audit that cannot read anything
    must say so once, not report every workflow in the repo as broken.
    """
    import yaml  # noqa: F401, PLC0415 — fail once, here, not per file

    found: dict[str, list[str]] = {}
    for path in sorted(Path(directory).glob("*.yml")):
        try:
            doc = _load(path)
        except Exception as exc:  # noqa: BLE001 — one bad file, keep going
            # Only a file that could BE the stub is worth reporting: any other
            # unparseable workflow is a different problem, and the repo's own
            # YAML check owns it.
            if path.name in _STUB_NAMES:
                found[path.name] = [f"{path.name} could not be read: {exc}"]
            continue
        if doc and calls_agent_fix(doc):
            found[path.name] = audit(doc, name=path.name)
    return found


# ---------------------------------------------------------------------------
# The runs the grouping evicted
# ---------------------------------------------------------------------------

#: The projection the sweep asks GitHub for. One home for the shape, so the
#: tests read the same fields the sweep does.
RUNS_JQ = (
    "[.workflow_runs[] | {id, event, status, conclusion, created_at, "
    "updated_at, actor: .actor.login, display_title, html_url}]"
)

#: How a run says which pull request it is working (DRE-2908). The Actions
#: run listing carries NO PR attribution for a `workflow_dispatch` run —
#: measured on this repo, `display_title` is the bare workflow name "Agent
#: Fix" and `pull_requests` is empty — so the ONE place the number survives
#: into the API is the job name, which the reusable agent-fix workflow builds
#: from the same expression the concurrency group uses. Producer (the YAML)
#: and consumer (this parser) are pinned together by
#: tests/test_conflict_sweep_per_pr_busy.py.
JOB_PR_PREFIX = "fix PR #"
_JOB_PR = re.compile(re.escape(JOB_PR_PREFIX) + r"(\d+)")


def pr_of_job_names(names) -> int | None:
    """The pull request an Agent Fix run is working, read from its job names.

    None means UNATTRIBUTED, never "no PR": GitHub lists zero jobs for a run
    still pending on its concurrency group (see `never_started`), and a repo
    pinned to a release tag older than DRE-2908 names its job without the
    number. Callers must treat None as "could be any PR" and bound it with a
    cap rather than blocking on it — blocking on it is the bug this exists to
    end.
    """
    for name in names or []:
        match = _JOB_PR.search(name or "")
        if match:
            return int(match.group(1))
    return None


TRIGGER_VERDICT = "verdict"   # a qa-bot REQUEST_CHANGES comment
TRIGGER_NOTICE = "notice"     # the fix loop's own bookkeeping comment
TRIGGER_DISPATCH = "dispatch"  # `gh workflow run` / the hand recovery
TRIGGER_OTHER = "other"

#: How far after a run's cancellation the evicting run may have been created.
#: On the live sequence the gap was one second in both directions (the pending
#: run's updated_at is the evictor's created_at, ±1s); five seconds is slack,
#: not a guess about GitHub's scheduler.
_EVICTOR_SLACK_SEC = 5


def is_cancelled(run: dict) -> bool:
    return (run or {}).get("conclusion") == "cancelled"


def never_started(job_count: int | None) -> bool:
    """Did this run get cancelled before a single job started?

    GitHub lists ZERO jobs for a run cancelled while PENDING (verified on
    runs 33231413617, 33230448792, 33230096718 and 33229394509) and one job
    for a run that reached its gate — including one that only skipped. An
    UNREADABLE count is not "never started": it is not a fact, so it answers
    False and the caller says so out loud (the DRE-2034 discipline).
    """
    return job_count == 0


def trigger_kind(run: dict) -> str:
    """What queued this run — the field that tells a lost verdict apart from a
    harmless duplicate dispatch. `actor` on an issue_comment run is the
    commenter, so it is the verdict's author when there was one."""
    actor = (run or {}).get("actor") or ""
    if (run or {}).get("event") == "workflow_dispatch":
        return TRIGGER_DISPATCH
    if actor == QA_BOT_LOGIN:
        return TRIGGER_VERDICT
    if actor == WORKER_BOT_LOGIN:
        return TRIGGER_NOTICE
    return TRIGGER_OTHER


def _seconds(iso: str) -> float:
    from datetime import datetime  # noqa: PLC0415 — stdlib, kept local like reconcile's

    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def evictor_of(run: dict, runs: list[dict]) -> dict | None:
    """The run that took the pending slot: the youngest run created after this
    one and no later than a breath after it was cancelled."""
    try:
        born, died = _seconds(run["created_at"]), _seconds(run["updated_at"])
    except (KeyError, ValueError):
        return None
    best = None
    for other in runs:
        if other.get("id") == run.get("id"):
            continue
        try:
            when = _seconds(other["created_at"])
        except (KeyError, ValueError, TypeError):
            continue
        if born < when <= died + _EVICTOR_SLACK_SEC:
            if best is None or when > _seconds(best["created_at"]):
                best = other
    return best


def eviction_report(run: dict, evictor: dict | None) -> str:
    """The sweep's line for a REQUEST_CHANGES trigger that never ran."""
    took_the_slot = (
        f"run {evictor['id']} ({evictor.get('actor')}, {evictor.get('conclusion')}) "
        "took the slot"
        if evictor
        else "no successor run was identifiable in the listing"
    )
    return (
        f"evicted-fix-run: Agent Fix run {run['id']} carried a REQUEST_CHANGES "
        f"verdict from {run.get('actor')} on \"{run.get('display_title')}\" and "
        f"was CANCELLED at {run.get('updated_at')} without starting a single "
        f"job — {took_the_slot}. GitHub keeps one pending run per concurrency "
        "group, so this is a verdict that never reached the fix agent, not the "
        f"harmless duplicate-dispatch cancel it looks like (DRE-2810). "
        f"{run.get('html_url')}"
    )


def _cli(argv: list[str]) -> int:
    if not argv or argv[0] != "audit":
        print("usage: fix_concurrency.py audit [<path> ...]", file=sys.stderr)
        return 2
    targets = argv[1:] or [".github/workflows"]
    problems: dict[str, list[str]] = {}
    for target in targets:
        path = Path(target)
        if path.is_dir():
            problems.update(audit_workflows_dir(path))
        else:
            doc = _load(path)
            if doc and calls_agent_fix(doc):
                problems[path.name] = audit(doc, name=path.name)
    if not problems:
        print("no agent-fix stub found — nothing to audit")
        return 0
    bad = 0
    for name, found in sorted(problems.items()):
        if found:
            bad += 1
            for line in found:
                print(f"{name}: {line}")
        else:
            print(f"{name}: groups by PR and commenter — a bot notice cannot "
                  "evict a pending verdict run")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
