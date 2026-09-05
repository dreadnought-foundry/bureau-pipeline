#!/usr/bin/env python3
"""A PR red on a fault `main` has since fixed (DRE-3138, stdlib only).

Origin (live, 2026-09-02 00:00–00:23 PT). agent-bureau PRs #2240 and #2241
both went red on `Console backend (pytest)` because of a fault on `main`
(DRE-2962). The fix merged to `main` at 00:02. Both pull requests stayed red
anyway: their CI had run against a merge ref computed BEFORE the fix, and
nothing in the pipeline recomputes a merge ref when `main` moves. `gh run
rerun` does not help — it re-runs the same jobs against the SAME merge commit.
Only a new head, an `update-branch` merge of `main` into the branch, gets a
fresh merge ref.

This module is the decision behind that refresh and nothing else: pure
functions over GitHub payloads, no I/O, and a CLI for humans, in the shape
`inherited_failures.py` and `red_main_repair.py` already carry. The reconcile
sweep is the caller; it lives in another card.

Three facts make a refresh safe, and each one rules something out:

  * `main` has moved past the merge base (`behind_by > 0`) — otherwise there
    is nothing a refresh could change;
  * every failing check on the head ALSO fails on the merge base — otherwise
    the pull request has its own defect and the fix loop owns it. That
    comparison is `inherited_failures.inherited()`, not a second derivation
    of it;
  * every one of them is GREEN on the `main` TIP — otherwise `main` is still
    red, the Red-Main Repair loop owns it, and refreshing would only
    re-inherit the failure.

Deliberately narrow, and deliberately never a pass:

  * a payload that cannot be read reports UNEVALUATED. "We could not look" is
    not "your fault" and is not "safe to refresh";
  * so does a check with no COMPLETED run on the `main` tip — main's CI still
    running is an unfinished sentence, not a green light;
  * review-named checks (`name.endswith("review")`) never enter the failing
    set, exactly as `reconcile.fix_approved_but_red` excludes them: a critic
    verdict check is a review outcome, not a CI result;
  * at most one refresh per `main` commit (the marker below), and a per-PR
    lifetime cap whose zero is the operator's off switch.

CLI:

    python3 stale_merge_ref.py decide --compare-file <compare json> \\
        --checks-file <head check-runs json> \\
        --base-checks-file <merge-base check-runs json> \\
        --main-checks-file <main-tip check-runs json> \\
        [--receipts-file <json list of comment bodies>] [--cap N]

stdout line 1 is the action; the one-line reason goes to stderr. Exit 0 on
every decision; exit 2 ONLY when the HEAD payload cannot be read — the same
discipline `inherited_failures.py` applies, because the head's own red checks
are the subject and a silent answer there would steer the sweep on a guess.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# "Fails on both sides" already exists, already proven against every payload
# shape `gh api` emits, and already carries the conclusion vocabulary. One
# derivation of it, in one place (DRE-2820).
from inherited_failures import inherited as _inherited  # noqa: E402
from unfixable_checks import FAILED_CONCLUSIONS, _check_runs  # noqa: E402
from unfixable_checks import failed_check_names  # noqa: E402

# The act's idempotency key. Written as a literal on purpose: the act registry
# reads `X_TAG = "..."` constants straight off the emitter file
# (pipeline_act._TAG_CONSTANT), so a computed value would be invisible to it.
REFRESH_TAG = "stale-merge-ref-refresh"

# The phrase config/pipeline-acts.json pins as this emitter's anchor. It must
# appear exactly once in this file — an absent anchor pins nothing and an
# ambiguous one pins the wrong thing — so it is named here and USED once, in
# receipt_detail().
ANCHOR_PHRASE = (
    "refreshed the merge ref: the fault was on main, not in this pull request"
)

REFRESH = "refresh"
CURRENT = "current"
NO_FAILURE = "no-failure"
OWN = "own"
MAIN_STILL_RED = "main-still-red"
UNEVALUATED = "unevaluated"
ALREADY_REFRESHED = "already-refreshed"
CAP_SPENT = "cap-spent"

ACTIONS = (
    REFRESH, CURRENT, NO_FAILURE, OWN, MAIN_STILL_RED, UNEVALUATED,
    ALREADY_REFRESHED, CAP_SPENT,
)

# Two, matching the house shape for a per-head receipt budget
# (reconcile.DEPENDABOT_RECEIPT_CAP, fix_dead_run.RETRY_CAP). A branch that has
# been refreshed onto two different `main` commits and is still red is not
# waiting on a stale merge ref.
DEFAULT_CAP = 2

# GitHub's check-run suffix for a review outcome. `reconcile.fix_approved_but_red`
# filters on exactly this with `.name | endswith("review")`.
_REVIEW_SUFFIX = "review"


def marker(main_sha: str) -> str:
    """Binds a refresh to the `main` commit it moved the branch onto.

    The full 40-hex sha, never an abbreviation: the marker is an idempotency
    key read with `marker in body`, and an 8-char prefix would collide across
    commits far sooner than a reviewer would expect.
    """
    return f"{REFRESH_TAG} @{main_sha}"


@dataclass(frozen=True)
class Decision:
    """One action, the one-line reason a sweep log carries, and the evidence.

    `inherited` is the head's failing set F, in the head's order and the
    head's spelling. On a `refresh` those names are by construction exactly
    the inherited ones — which is where the field's name comes from — and it
    is that list `receipt_detail()` is handed.
    """

    action: str
    reason: str
    inherited: list = field(default_factory=list)
    base_sha: str = ""
    main_sha: str = ""
    behind_by: int = 0


def _normalize(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def _is_review_check(name: str) -> bool:
    return _normalize(name).endswith(_REVIEW_SUFFIX)


def failing_set(head_checks) -> list:
    """F — the head's failing check names, review checks excluded, deduped,
    in the head's order and the head's spelling.

    Raises ValueError if the payload cannot be read as check runs.
    """
    names, seen = [], set()
    for name in failed_check_names(head_checks):
        key = _normalize(name)
        if not key or key in seen or _is_review_check(name):
            continue
        seen.add(key)
        names.append(name)
    return names


def _read_compare(compare):
    """(behind_by, merge-base sha, main-tip sha), or None if unreadable.

    `compare/{base}...{head}` carries all three in one read: `behind_by`,
    `merge_base_commit.sha`, and `base_commit.sha` — the tip of the branch we
    compared against, which for this sweep is `main`.
    """
    if not isinstance(compare, dict):
        return None
    behind = compare.get("behind_by")
    if isinstance(behind, bool) or not isinstance(behind, int) or behind < 0:
        return None
    base = (compare.get("merge_base_commit") or {})
    tip = (compare.get("base_commit") or {})
    if not isinstance(base, dict) or not isinstance(tip, dict):
        return None
    base_sha, main_sha = base.get("sha") or "", tip.get("sha") or ""
    if not base_sha or not main_sha:
        return None
    return behind, base_sha, main_sha


def _read_receipts(receipts):
    """The receipt BODIES, or None if the input cannot be read.

    A list of strings is the shape the sweep hands us; a list of `gh` comment
    objects is accepted too, so a caller need not unwrap them first.
    """
    if isinstance(receipts, (str, bytes)) or receipts is None:
        return None
    try:
        items = list(receipts)
    except TypeError:
        return None
    bodies = []
    for item in items:
        if isinstance(item, str):
            bodies.append(item)
        elif isinstance(item, dict):
            bodies.append(item.get("body") or "")
        else:
            return None
    return bodies


def _main_tip_verdict(main_checks, names):
    """('red', name) · ('unfinished', name) · ('green', None) for F on the
    `main` tip. Raises ValueError if the payload cannot be read.

    A definite red is reported ahead of an unfinished run: both stop the
    refresh, and "main is still red on X" is the line that tells a reader
    which loop owns it.
    """
    runs = _check_runs(main_checks)
    red, complete_success = set(), set()
    for run in runs:
        key = _normalize(run.get("name") or "")
        conclusion = (run.get("conclusion") or "")
        if conclusion in FAILED_CONCLUSIONS:
            red.add(key)
        elif conclusion == "success" and _is_completed(run):
            complete_success.add(key)
    for name in names:
        if _normalize(name) in red:
            return "red", name
    for name in names:
        if _normalize(name) not in complete_success:
            return "unfinished", name
    return "green", None


def _is_completed(run) -> bool:
    """A run GitHub has finished. `status` is authoritative; a payload that
    omits it (a hand-rolled fixture, a trimmed `--jq` projection) is read off
    the conclusion, which is null until the run completes."""
    status = (run.get("status") or "").casefold()
    return status == "completed" if status else bool(run.get("conclusion"))


def decide(*, compare, head_checks, base_checks, main_checks, receipts, cap) -> Decision:
    """Is this pull request red only on a fault `main` has since fixed?

    Every argument is a raw GitHub payload — nothing here reads the network.
    The order below is the order the answers get cheaper to be wrong about:
    an unreadable input first, then the budget (the operator's off switch),
    then the geometry, and only then the three check-run payloads.
    """
    bodies = _read_receipts(receipts)
    if bodies is None:
        return Decision(UNEVALUATED,
                        "the receipts could not be read — we cannot tell "
                        "whether this branch was already refreshed")

    read = _read_compare(compare)
    if read is None:
        return Decision(UNEVALUATED,
                        "the compare payload could not be read — no merge "
                        "base, no `main` tip, no decision")
    behind_by, base_sha, main_sha = read

    def answer(action: str, reason: str, names=None) -> Decision:
        return Decision(action, reason, list(names or []), base_sha, main_sha,
                        behind_by)

    used = sum(1 for body in bodies if REFRESH_TAG in body)
    if cap <= 0:
        return answer(CAP_SPENT,
                      "the refresh cap is 0 — the operator's off switch")
    if used >= cap:
        return answer(CAP_SPENT,
                      f"{used} refresh(es) already spent on this pull request "
                      f"of a lifetime cap of {cap}")
    if any(marker(main_sha) in body for body in bodies):
        return answer(ALREADY_REFRESHED,
                      f"already refreshed onto `main` {main_sha[:8]} — at most "
                      f"one refresh per `main` commit")

    if behind_by == 0:
        return answer(CURRENT,
                      "`main` has not moved past the merge base — a refresh "
                      "would change nothing")

    try:
        names = failing_set(head_checks)
    except ValueError as exc:
        return answer(UNEVALUATED,
                      f"the head's check runs could not be read: {exc}")
    if not names:
        return answer(NO_FAILURE, "no failing checks on this head")

    try:
        # The head is re-expressed as a check-runs payload so the ONE
        # implementation of "fails on both sides" sees the filtered set.
        carried = _inherited(
            {"check_runs": [{"name": n, "conclusion": "failure"} for n in names]},
            base_checks,
        )
    except ValueError as exc:
        return answer(UNEVALUATED,
                      f"the merge base's check runs could not be read: {exc}",
                      names)
    if len(carried) != len(names):
        own = [n for n in names
               if _normalize(n) not in {_normalize(c) for c in carried}]
        return answer(OWN,
                      f"{', '.join(own)} is green on the merge base — this "
                      f"pull request's own defect, and the fix loop owns it",
                      names)

    try:
        verdict, culprit = _main_tip_verdict(main_checks, names)
    except ValueError as exc:
        return answer(UNEVALUATED,
                      f"the `main` tip's check runs could not be read: {exc}",
                      names)
    if verdict == "red":
        return answer(MAIN_STILL_RED,
                      f"{culprit} still fails on `main` {main_sha[:8]} — the "
                      f"Red-Main Repair loop owns it, and a refresh would "
                      f"re-inherit it",
                      names)
    if verdict == "unfinished":
        return answer(UNEVALUATED,
                      f"{culprit} has no completed successful run on `main` "
                      f"{main_sha[:8]} yet — try again next sweep",
                      names)

    return answer(REFRESH,
                  f"{', '.join(names)} red on this head and on merge base "
                  f"{base_sha[:8]}, green on `main` {main_sha[:8]} "
                  f"({behind_by} commit(s) ahead) — the fault was main-side "
                  f"and `main` has fixed it",
                  names)


def receipt_detail(*, pr_number, head_sha, main_sha, base_sha, inherited,
                   used, cap) -> str:
    """The plain-English body the sweep hands to `pipeline_act.receipt()`.

    Opens with the marker so the act is idempotent per `main` commit, names
    the evidence, and states the cost honestly rather than selling the refresh
    as free. Not blocker-shaped (a bot comment whose first line opens with 🛑
    is read as a prior fix-loop blocker by fix_context.py) and carrying no
    verdict marker (standards/untrusted-content.md — those are an approval
    credential and only the critic writes one).
    """
    names = list(inherited)
    lines = [
        marker(main_sha),
        "",
        f"Pull request #{pr_number} was red on {len(names)} check(s) that were "
        f"red on its merge base (`{base_sha[:8]}`) too, and are green on "
        f"`main` at `{main_sha[:8]}`:",
        "",
        *[f"- **{name}** — red on `{head_sha[:8]}` and on the merge base, "
          f"green on `main`." for name in names],
        "",
        f"`main` carried the fix in `{main_sha[:8]}`, and this branch's CI had "
        f"run against a merge ref computed before it. Re-running the same jobs "
        f"would re-run them against the same merge commit, so we "
        f"{ANCHOR_PHRASE}. The branch was refreshed with `update-branch` once "
        f"for this `main` commit ({used}/{cap} refreshes used on this pull "
        f"request).",
        "",
        "What it costs, plainly: the new head is a merge of `main` into this "
        "branch. If the diff against `main` is unchanged, the standing critic "
        "verdict CARRIES (content binding, DRE-2340, `verdict_content.py`) and "
        "the critic skips the re-review. If `main`'s fix touched a file this "
        "branch also touches, the diff changed, the verdict is discharged and "
        "a fresh review runs.",
    ]
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Decide whether a PR is red only on a fault `main` has "
                    "since fixed"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decide")
    d.add_argument("--compare-file", required=True,
                   help="JSON from `gh api repos/{repo}/compare/{base}...{head}`")
    d.add_argument("--checks-file", required=True,
                   help="JSON from `gh api repos/{repo}/commits/{head}/check-runs`")
    d.add_argument("--base-checks-file", required=True,
                   help="the same, for the merge-base commit")
    d.add_argument("--main-checks-file", required=True,
                   help="the same, for the tip of `main`")
    d.add_argument("--receipts-file",
                   help="JSON list of the worker-bot comment bodies already "
                        "on the pull request")
    d.add_argument("--cap", type=int, default=DEFAULT_CAP,
                   help="the per-PR lifetime refresh cap (0 turns the act off)")
    args = parser.parse_args(argv)

    try:
        head_checks = _load(args.checks_file)
        failing_set(head_checks)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        # Loud, for the reason inherited_failures.py is loud here: the head's
        # own red checks are the subject, and a silent answer would send the
        # sweep to update a branch on nothing.
        print(f"stale_merge_ref: cannot read head check runs: {exc}",
              file=sys.stderr)
        return 2

    def _or_unreadable(path, default):
        """An unreadable side is passed through as a sentinel decide() will
        report UNEVALUATED for, rather than being guessed at here."""
        if path is None:
            return default
        try:
            return _load(path)
        except (OSError, ValueError, json.JSONDecodeError):
            return _Unreadable()

    decision = decide(
        compare=_or_unreadable(args.compare_file, None),
        head_checks=head_checks,
        base_checks=_or_unreadable(args.base_checks_file, None),
        main_checks=_or_unreadable(args.main_checks_file, None),
        receipts=(_or_unreadable(args.receipts_file, [])
                  if args.receipts_file else []),
        cap=args.cap,
    )
    print(decision.action)
    print(f"stale_merge_ref: {decision.action} — {decision.reason}",
          file=sys.stderr)
    return 0


class _Unreadable:
    """A file that would not parse. Nothing accepts it, so every reader in
    decide() answers UNEVALUATED for it — one unreadable-input rule, written
    once."""


if __name__ == "__main__":
    sys.exit(main())
