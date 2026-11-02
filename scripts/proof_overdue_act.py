#!/usr/bin/env python3
"""The sweep takes each overdue proof's one action (DRE-8627).

`scripts/proof_overdue_sweep.py` (DRE-8626) posts, once a PT day, one comment
on each `PROOF:` card open more than seven days, its first line

    ⏳ proof-overdue: <YYYY-MM-DD> · class=<cls> · action=<action> · waits-on=<waits_on> · age=<age_days>d

naming the one machine action the card's class takes (`proof_overdue.ACTIONS`,
DRE-8625). It says; this step does. For each of the repo's open PROOF cards it
reads the newest such comment the pipeline wrote, and once that comment's
condition has come it dispatches ONE proof run at the card, with a reason
naming the action — so no proof waits weeks on a person. It never moves a
lane, never posts a hold, never writes a label and never touches a card in
`Green Light`: none of the five actions puts a card in front of the CEO. Its
only writes are the fire and the receipt.

## When it runs

The reconcile workflow's `Proof overdue` step runs it right after the daily
pass, whenever this file exists — adding the file is what wires it — with no
arguments, reading `REPO`, `REPO_SLUG` and `PROOF_DISPATCH_LIVE` from the
step's environment. With `PROOF_DISPATCH_LIVE` anything but exactly `true`
the step is dry: each dispatch it would make is one `would:` line, and
nothing is fired or posted.

## The five actions — the `action=` word, its condition, its reason

  * `rerun-on-release` — waits until the `waits-on` card is shipped, read as
    the dispatcher reads a shipped fix card's state: `Done`, and `Canceled`
    is not shipped. A `waits-on` that is not a card id is a release tag, and
    waits until `proof_release.reading` over the siblings' merges
    (`proof_overdue`'s own release read) reads `ready`. Reason
    `re-run after the proof-overdue release <waits-on> shipped`.
  * `rerun-on-date` — waits until the `waits-on` time has passed. Reason
    `re-run after the proof-overdue date <PT>`.
  * `rerun-on-deploy` — waits until the card's repo has a release newer than
    the comment: the newest tag of a surface its `release.json` declares,
    dated as `proof_release` dates a tag (`deployed_since`). Reason
    `re-run after the proof-overdue deploy <tag>`.
  * `sandbox-press` — at once, on a PROOF labeled `repo:agent-bureau` whose
    `waits-on` is a press on the sandbox list (`plan_proof_reach.sandbox_presses`),
    the only case DRE-8625 reads `press`; any other stands down. Reason
    `sandbox press: <mutation> (proof-overdue of <YYYY-MM-DD>)`, which
    `briefs/proof.md` tells the run to make itself.
  * `split` — at once. Reason
    `split: <n> row(s) cannot recur unattended (proof-overdue of <YYYY-MM-DD>)`,
    `<n>` read off the record (`unrecurring_rows`); `briefs/proof.md` tells
    the run to write each such row as history, so the record closes as a gap
    and its follow-up card is filed in Intake.

## The dispatch, and the bound

The dispatch is the dispatcher's own seam: `plan_run.fire(card, repo,
reason=…, event=plan_run.PROOF_EVENT)`, then — only once the fire is
confirmed — the `🔬 proof-run:` receipt through
`pipeline_act.receipt("proof-run-dispatched", …)` in the dispatcher's
wording, counted `overdue <k> of 3`. That count opens neither `re-run` nor
`timed re-run`, is none of `re-check`, `re-armed` or `chase`, and no reason
here opens `first proof run` or `second dispatch`, so it spends none of the
dispatcher's budgets (`proof_run_state` reads it as a receipt, the anchor of
the run's state, as it reads a chase).

One dispatch per `⏳ proof-overdue:` comment: an `overdue` receipt newer than
the comment says it was acted on. At most three per card in all, counted off
the pipeline's own `overdue` receipts. The third is always `split`, whatever
the comment's class, so a proof that three actions did not move leaves with
its follow-up card instead of a fourth run; with three spent there is no
fourth, and the card's line says so.

## The stand-downs, each named on the card's line

  * a card in `Green Light`, the CEO's queue — its thread is not even read;
  * the `needs-human` label;
  * no pipeline-authored `⏳ proof-overdue:` comment — a copy of the mark
    anyone else posted is never read, nor a receipt anyone else posted;
  * a `🔬 proof-run` receipt newer than the comment — the dispatcher's own
    step, which runs first, already sent one;
  * a run `running` or `unknown` (`proof_run_state.reading`);
  * the action's condition, not yet come.

## Failures

A card, thread or reading that cannot be read is named on its line and gets
nothing; the next pass reads it again. The step exits non-zero only when a
dispatch was not confirmed or its receipt did not land.

CLI (the step's own call):

    python3 .bureau-pipeline/scripts/proof_overdue_act.py
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import linear_ops  # noqa: E402 — the receipt's one write
import pipeline_act  # noqa: E402 — the receipt's composer
import plan_proof_reach  # noqa: E402 — the sandbox presses, the one copy of the list
import plan_run  # noqa: E402 — the fire
import proof_overdue  # noqa: E402 — the classes, the actions and the reads
import proof_overdue_sweep  # noqa: E402 — the comment's mark
import proof_record  # noqa: E402 — the record's rows
import proof_release  # noqa: E402 — the release record and its tags
import proof_run_state  # noqa: E402 — the receipt and the run's state
import release_train  # noqa: E402 — the surfaces a release record declares
import spoken_thread  # noqa: E402 — the PT label
import switch_reason  # noqa: E402 — the dry run's off line

TAG = proof_overdue.TAG
#: Opens every line this step prints but its `would:` lines, so the step
#: summary's `proof-overdue: (note|action step)` filter keeps them.
LINE = f"{TAG} action step"
#: The comment's mark (DRE-8626); then the PT day.
MARK = proof_overdue_sweep.MARK
LIVE_VARIABLE = proof_overdue_sweep.LIVE_VARIABLE
GREEN_LIGHT = proof_overdue_sweep.GREEN_LIGHT
NEEDS_HUMAN = "needs-human"
#: The run states that stand the step down: `proof_dispatch.IN_FLIGHT`.
IN_FLIGHT = ("running", "unknown")

#: Dispatches per card, in all; the last is always `split`.
OVERDUE_BUDGET = 3
OVERDUE_COUNT = f"overdue {{k}} of {OVERDUE_BUDGET}"
_OVERDUE = re.compile(rf"^overdue (\d+) of {OVERDUE_BUDGET}$")

RELEASE_REASON = "re-run after the proof-overdue release {waits_on} shipped"
DATE_REASON = "re-run after the proof-overdue date {at}"
DEPLOY_REASON = "re-run after the proof-overdue deploy {tag}"
PRESS_REASON = "sandbox press: {mutation} (proof-overdue of {day})"
SPLIT_REASON = "split: {n} row(s) cannot recur unattended (proof-overdue of {day})"
SPLIT = proof_overdue.ACTIONS["unrepeatable"]
#: The five actions this step takes — each `proof_overdue.ACTIONS` value.
TAKES = ("rerun-on-release", "rerun-on-date", "rerun-on-deploy", "sandbox-press", SPLIT)

_FIRST = re.compile(rf"^{re.escape(MARK)} (?P<day>\d{{4}}-\d{{2}}-\d{{2}}) · "
                    r"class=(?P<cls>\S+) · action=(?P<action>\S+) · "
                    r"waits-on=(?P<waits_on>.*) · age=(?P<age>[\d.]+)d$")
_CARD_ID = re.compile(r"DRE-\d+")

#: The card, with its `id` for the fire and its epic's children for the
#: release read (`proof_overdue._release`), in one read.
CARD_QUERY = """query($id: String!) { issue(id: $id) {
             id identifier title description state { name }
             labels { nodes { name } }
             parent { identifier children(first: 100) { nodes {
               identifier title state { name } labels { nodes { name } } } } }
           } }"""


class Told(NamedTuple):
    """A `⏳ proof-overdue:` comment's first line, and when it was posted."""
    day: str
    cls: str
    action: str
    waits_on: str
    at: datetime | None


class LinearReads(proof_overdue.LinearReads):
    """`proof_overdue`'s reads, the card read carrying what the fire sends."""

    def card(self, identifier: str) -> dict:
        issue = (linear_ops.gql(CARD_QUERY, {"id": identifier}) or {}).get("issue")
        if not issue:
            raise LookupError(f"Linear answered no card for {identifier}")
        return issue


def _dispatch():
    """`proof_dispatch`, imported on first use: it imports `reconcile`, which
    reads `REPO` when it is imported."""
    import proof_dispatch  # noqa: PLC0415

    return proof_dispatch


def is_live(env=None) -> bool:
    return (os.environ if env is None else env).get(LIVE_VARIABLE) == "true"


def _pt(when: datetime) -> str:
    return spoken_thread.pacific_label(when.isoformat())


def _why(error) -> str:
    return " ".join(str(error or "").split())[:300] or type(error).__name__


def parse(body: str, at=None) -> Told | None:
    """The comment's first line, read back — or None for any other comment."""
    found = _FIRST.match((body or "").strip().split("\n", 1)[0].strip())
    if not found:
        return None
    return Told(found["day"], found["cls"], found["action"], found["waits_on"].strip(),
                proof_overdue._time(at))


def _ours(node: dict, viewer) -> bool:
    return bool(viewer) and ((node.get("user") or {}).get("id")) == viewer


def newest_told(nodes, viewer) -> Told | None:
    """The newest `⏳ proof-overdue:` comment the pipeline wrote, or None. The
    same line from anyone else counts for nothing."""
    told = [t for n in nodes or () if _ours(n, viewer)
            and (t := parse(n.get("body"), n.get("createdAt"))) is not None]
    return told[-1] if told else None


def receipts(nodes, viewer) -> list:
    """`(time, proof_run_state.Receipt)` for every `🔬 proof-run` receipt the
    pipeline wrote, oldest first."""
    return [(proof_overdue._time(n.get("createdAt")), r) for n in nodes or ()
            if _ours(n, viewer) and (r := proof_run_state.receipt(n.get("body") or ""))]


def is_overdue(got) -> bool:
    return bool(_OVERDUE.match(got.count))


# --------------------------------------------------------------------------- #
# the default readings                                                         #
# --------------------------------------------------------------------------- #


def deployed_since(repo: str, since: datetime, *, read: Callable) -> tuple | None:
    """`(tag, time)` of the newest release in `repo` dated after `since`, or
    None: the newest tag of each surface its `release.json` declares
    (`proof_release.newest_tag`), dated as git's creator date
    (`proof_release._tag_date`). No release record, no release to read."""
    data = proof_release._release_json(repo, read)
    if data is None:
        return None
    newest = None
    for surface in release_train.surfaces(data).values():
        tag = proof_release.newest_tag(repo, surface.tag_series, read=read)
        if not tag:
            continue
        ref = read(f"repos/{repo}/git/ref/tags/{tag}") or {}
        when = proof_release._tag_date(repo, ref.get("object") or {}, read)
        if when > since and (newest is None or when > newest[1]):
            newest = (tag, when)
    return newest


def unrecurring_rows(identifier: str, issue: dict, repo: str, *, forced: bool, gh) -> int:
    """How many of the record's unmet rows the split names: those that read
    `unrepeatable` (`proof_overdue`'s own row reading) — or, for the third
    dispatch, which splits whatever the class, every unmet row. At least one:
    the comment names one thing that will not happen again by itself."""
    record = proof_overdue._record(identifier, repo, issue.get("description"), gh)
    rows = (proof_record.criterion_rows(record["text"]) or []) if record.get("text") else []
    unmet = proof_overdue._unmet(rows)
    if not forced:
        card = {"repo": proof_overdue._slug(issue) or "", "states": {}}
        unmet = [(c, r) for c, r in unmet
                 if any(read[0] == "unrepeatable" for read in proof_overdue._row_reads(c, r, card))]
    return max(1, len(unmet))


# --------------------------------------------------------------------------- #
# the pass                                                                     #
# --------------------------------------------------------------------------- #


class _Pass:
    def __init__(self, repo, slug, *, live, now, linear, run_state, fire, read,
                 released, deployed, unrecurring):
        self.repo, self.slug, self.live, self.now = repo, slug, live, now
        self.linear, self.run_state, self.fire, self.read = linear, run_state, fire, read
        self.released, self.deployed, self.unrecurring = released, deployed, unrecurring
        self.failures: list = []
        self.counts = {"dispatched": 0, "waiting": 0, "stood down": 0, "unread": 0}

    def say(self, ident: str, text: str) -> None:
        print(f"{LINE} · {ident} · {text}")

    def card(self, ident: str) -> str:
        """Read one card and take its action; the bucket it lands in."""
        try:
            issue = self.linear.card(ident)
        except Exception as error:  # noqa: BLE001 — named, never red
            self.say(ident, f"could not be read — {_why(error)}; nothing dispatched")
            return "unread"
        lane = (issue.get("state") or {}).get("name")
        if lane == GREEN_LIGHT:
            self.say(ident, f"in {GREEN_LIGHT}, the CEO's queue — never touched")
            return "stood down"
        try:
            nodes, viewer = self.linear.thread(ident)
        except Exception as error:  # noqa: BLE001
            self.say(ident, f"its thread could not be read — {_why(error)}; nothing dispatched")
            return "unread"
        told = newest_told(nodes, viewer)
        if told is None:
            self.say(ident, f"no pipeline-authored {MARK} comment — nothing to act on")
            return "stood down"
        if NEEDS_HUMAN in proof_overdue._labels(issue):
            self.say(ident, f"it carries {NEEDS_HUMAN} — stood down")
            return "stood down"
        if told.at is None:
            self.say(ident, f"the {MARK} comment of {told.day} has no time — nothing dispatched")
            return "unread"
        mine = receipts(nodes, viewer)
        spent = sum(1 for _, got in mine if is_overdue(got))
        after = [(when, got) for when, got in mine if when is None or when > told.at]
        if any(is_overdue(got) for _, got in after):
            self.say(ident, f"already dispatched for the comment of {told.day} — one "
                            "dispatch per comment")
            return "stood down"
        if spent >= OVERDUE_BUDGET:
            self.say(ident, f"overdue {spent} of {OVERDUE_BUDGET} spent — no fourth "
                            "dispatch; the third was its split")
            return "stood down"
        if after:
            self.say(ident, f"a 🔬 proof-run receipt of {after[-1][1].at} is newer than "
                            f"the comment of {told.day} — the dispatcher sent one; "
                            "stood down")
            return "stood down"
        try:
            got = self.run_state(self.repo, ident, nodes, viewer, read=self.read, now=self.now)
        except Exception as error:  # noqa: BLE001 — unread is in flight
            self.say(ident, f"its run could not be read — {_why(error)}; stood down")
            return "stood down"
        if got.state in IN_FLIGHT:
            self.say(ident, f"a run is {got.state} — {'; '.join(got.lines)}; stood down")
            return "stood down"
        forced = spent == OVERDUE_BUDGET - 1
        action = SPLIT if forced else told.action
        try:
            reason, why = self.condition(action, told, issue, ident, forced=forced)
        except Exception as error:  # noqa: BLE001
            self.say(ident, f"{action}: its condition could not be read — {_why(error)}; "
                            "nothing dispatched")
            return "unread"
        if reason is None:
            self.say(ident, f"{action} waits — {why}")
            return "waiting"
        if forced and told.action != SPLIT:
            self.say(ident, f"the third dispatch is always {SPLIT}, whatever the class "
                            f"(class={told.cls})")
        return self.dispatch(issue, reason, OVERDUE_COUNT.format(k=spent + 1))

    def condition(self, action: str, told: Told, issue: dict, ident: str, *,
                  forced: bool) -> tuple:
        """`(reason, None)` once the action's condition holds, else
        `(None, why)`."""
        waits_on = told.waits_on
        if action == "rerun-on-release":
            if _CARD_ID.fullmatch(waits_on):
                shipped = proof_overdue.SHIPPED
                state = self.linear.states([waits_on]).get(waits_on)
                if state == shipped:
                    return RELEASE_REASON.format(waits_on=waits_on), None
                return None, (f"{waits_on} is {state or 'not read'}, not {shipped} "
                              "(Canceled is not shipped)")
            got = self.released(issue, ident, proof_overdue._slug(issue), self.repo)
            if got.state == "ready":
                return RELEASE_REASON.format(waits_on=waits_on), None
            return None, f"the release of {waits_on} reads {got.state} — {'; '.join(got.lines)}"
        if action == "rerun-on-date":
            when = proof_overdue._time(waits_on)
            if when is None:
                return None, f"waits-on {waits_on} names no time"
            if self.now >= when:
                return DATE_REASON.format(at=_pt(when)), None
            return None, f"its time, {_pt(when)}, has not come"
        if action == "rerun-on-deploy":
            got = self.deployed(self.repo, told.at)
            if got:
                return DEPLOY_REASON.format(tag=got[0]), None
            return None, (f"no release in {self.repo} newer than the comment of "
                          f"{_pt(told.at)} ({waits_on})")
        if action == "sandbox-press":
            slug = proof_overdue._slug(issue)
            if slug != proof_overdue.SANDBOX_REPO:
                return None, (f"a sandbox press is made only on a PROOF labeled "
                              f"repo:{proof_overdue.SANDBOX_REPO}, and this one is "
                              f"repo:{slug} — stood down")
            ops = plan_proof_reach.sandbox_presses(waits_on)
            if not ops:
                return None, f"{waits_on} is no press on the sandbox list — stood down"
            return PRESS_REASON.format(mutation=ops[0], day=told.day), None
        if action == SPLIT:
            n = self.unrecurring(ident, issue, self.repo, forced=forced)
            return SPLIT_REASON.format(n=n, day=told.day), None
        return None, f"action={action} is none this step takes ({', '.join(TAKES)})"

    def dispatch(self, issue: dict, reason: str, count: str) -> str:
        ident = issue["identifier"]
        if not self.live:
            print(f"would: dispatch {ident} — {reason} ({count})")
            return "dispatched"
        ok, error = self.fire(issue, self.repo, reason=reason, event=plan_run.PROOF_EVENT)
        if not ok:
            self.failures.append(f"{ident}: the dispatch was not confirmed: {error}")
            self.say(ident, f"ERROR: the dispatch was not confirmed: {error}")
            return "dispatched"
        line = (f"{proof_run_state.RECEIPT_MARKER} dispatched a proof run at "
                f"{_pt(self.now)} — {reason} ({count})")
        body = pipeline_act.receipt("proof-run-dispatched", line)
        try:
            capped = linear_ops.cmd_comment(ident, body)
        except Exception as error:  # noqa: BLE001 — recorded like every failed write
            capped = _why(error)
        if capped:
            self.failures.append(f"{ident}: receipt not posted: {capped}")
            self.say(ident, f"ERROR: dispatched, and the receipt could not be posted: {capped}")
            return "dispatched"
        self.say(ident, f"dispatched: {reason} ({count})")
        return "dispatched"


def sweep(repo: str, slug: str, *, live: bool | None = None, now: datetime | None = None,
          linear=None, run_state: Callable | None = None, fire: Callable | None = None,
          read: Callable | None = None, gh: Callable | None = None,
          released: Callable | None = None, deployed: Callable | None = None,
          unrecurring: Callable | None = None) -> int:
    """One pass over the repo's open PROOF cards. 0, or 1 when a dispatch was
    not confirmed or its receipt did not land. `read` is `gh api <path>` as
    JSON (the dispatcher's `github_read`), `gh` is `gh <args>`'s stdout
    (`proof_overdue`'s), and the rest are injected in the tests."""
    live = is_live() if live is None else live
    now = now or datetime.now(timezone.utc)
    gh = gh or proof_record._gh
    read = read or (lambda path: _dispatch().github_read(path))
    one = _Pass(
        repo, slug, live=live, now=now, linear=linear or LinearReads(),
        run_state=run_state or proof_run_state.reading, fire=fire or plan_run.fire,
        read=read,
        released=released or (lambda issue, ident, s, full: _released(issue, ident, s, full, gh)),
        deployed=deployed or (lambda full, since: deployed_since(full, since, read=read)),
        unrecurring=unrecurring or (lambda ident, issue, full, *, forced:
                                    _unrecurring(ident, issue, full, forced=forced, gh=gh)))
    print(f"{LINE} · {LIVE_VARIABLE} is {'true — live' if live else 'not true — dry run'}")
    try:
        cards = one.linear.proofs(slug)
    except Exception as error:  # noqa: BLE001 — nothing known, nothing written
        print(f"{LINE} · the open PROOF cards of {slug} could not be read — {_why(error)}")
        return 0
    for ident in cards:
        bucket = one.card(ident)
        one.counts[bucket] += 1
    verb = "dispatched" if live else "would dispatch"
    print(f"{LINE} · {slug} · {one.counts['dispatched']} {verb} · "
          f"{one.counts['waiting']} waiting · {one.counts['stood down']} stood down · "
          f"{one.counts['unread']} not read · {len(one.failures)} failed")
    for failure in one.failures:
        print(f"ERROR: {LINE} {failure}", file=sys.stderr)
    return 1 if one.failures else 0


def _released(issue: dict, ident: str, slug: str, repo: str, gh) -> proof_release.Reading:
    """The release reading over the siblings' merges (`proof_overdue._release`);
    with no merge in this repo there is nothing for a release to carry."""
    got = proof_overdue._release(issue, ident, slug, repo, gh)
    if got is None:
        return proof_release.Reading("ready", [f"ready — no merge of a sibling in {repo} "
                                               "for a release to carry"])
    return proof_release.Reading(got["state"], list(got["lines"]))


def _unrecurring(ident: str, issue: dict, repo: str, *, forced: bool, gh) -> int:
    """`unrecurring_rows`, or one — the row the comment names — when the
    record cannot be read; the line says so."""
    try:
        return unrecurring_rows(ident, issue, repo, forced=forced, gh=gh)
    except Exception as error:  # noqa: BLE001
        print(f"{LINE} · {ident} · its record could not be read — {_why(error)}; the "
              "split names the one row the comment names")
        return 1


def main(argv=None) -> int:
    repo, slug = os.environ.get("REPO"), os.environ.get("REPO_SLUG")
    if not repo or not slug:
        print(f"{LINE} · REPO and REPO_SLUG must both be set — nothing read")
        return 0
    live = is_live()
    if not live:
        print(f"{LINE} · {switch_reason.off_line(LIVE_VARIABLE, os.environ)}")
    return sweep(repo, slug, live=live)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
