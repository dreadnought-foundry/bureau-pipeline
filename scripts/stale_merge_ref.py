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
  * every failing check on the head was ALSO red on `main` — on the merge
    base itself, or on one of `main`'s own merges after it — otherwise the
    pull request has its own defect and the fix loop owns it. The merge-base
    half is `inherited_failures.inherited()`, not a second derivation of it;
  * every one of them is GREEN on `main` again, read on the newest of those
    merges that has a finished run of it — otherwise `main` is still red, the
    Red-Main Repair loop owns it, and refreshing would only re-inherit the
    failure.

"`main` itself" is `main`'s first-parent commits since the merge base, at most
MAIN_WINDOW of them, tip first (DRE-6513). Origin (live, 2026-10-09): four
agent-bureau pull requests sat red on failures `main` had already fixed, and
the rule called three of them their own. #3457's merge base was green on
`Console backend shard 1` because the fault landed one merge LATER; #3428's
merge base never ran the backend checks at all; and #3444's tip was still
running, as a busy `main`'s tip nearly always is. A merge commit's sha was
never a pull request head, so a check run on one is `main`'s and never a
pull request's own — a pull request that broke the check itself leaves no
red run of it on `main`, and still answers OWN.

Deliberately narrow, and deliberately never a pass:

  * a payload that cannot be read reports UNEVALUATED. "We could not look" is
    not "your fault" and is not "safe to refresh";
  * so does a check the merge base is red on and no window commit has a
    COMPLETED run of — main's CI still running is an unfinished sentence,
    not a green light;
  * review-named checks (`name.endswith("review")`) never enter the failing
    set, exactly as `reconcile.fix_approved_but_red` excludes them: a critic
    verdict check is a review outcome, not a CI result;
  * at most one refresh per `main` commit (the marker below), and a per-PR
    lifetime cap whose zero is the operator's off switch.

CLI:

    python3 stale_merge_ref.py decide --compare-file <compare json> \\
        --checks-file <head check-runs json> \\
        --base-checks-file <merge-base check-runs json> \\
        (--main-checks-file <main-tip check-runs json> |
         --main-window-file <json list of {"sha", "check_runs"}, tip first>) \\
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
from unfixable_checks import failed_check_names  # noqa: E402

# `_check_runs` because `main`'s window is the ONE side we ask a green question
# of, and `failed_check_names` only answers the red one. Borrowing the private
# reader keeps the payload-shape tolerance (bare object · `--paginate --slurp`
# pages · flat list) and the "unreadable is not all-green" ValueError in the
# single place that owns them; re-deriving either here is how two readers come
# to disagree about the same payload. This card owns two files, so it borrows
# rather than widening unfixable_checks' surface.
from unfixable_checks import FAILED_CONCLUSIONS, _check_runs  # noqa: E402

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

# How many of `main`'s own merges since the merge base are read, tip first
# (DRE-6513). Measured on 2026-10-09: one 100-commit listing held 25
# first-parent commits on agent-bureau and 27 on bureau-pipeline, so one page
# covers twelve; the reds that mattered that afternoon were seven and eight
# merges back. A red older than the window is not read, and the rule then
# answers as it did before.
MAIN_WINDOW = 12

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

    `evidence` is filled on `refresh` only: one dict per name in `inherited`,
    `{"check", "base_red", "red_sha", "green_sha"}`. `red_sha` is the merge
    base when `base_red`, and otherwise the newest window commit red on the
    check; `green_sha` is the newest window commit with a finished run of it.
    """

    action: str
    reason: str
    inherited: list = field(default_factory=list)
    base_sha: str = ""
    main_sha: str = ""
    behind_by: int = 0
    evidence: list = field(default_factory=list)


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


def first_parent_window(listing, *, tip_sha, base_sha, limit=MAIN_WINDOW) -> list:
    """`main`'s own merges since the merge base, tip first, as shas.

    `listing` is the payload of `repos/{repo}/commits?sha={tip}&per_page=100`.
    The walk starts at the tip and follows `parents[0]` — the merge commits
    GitHub wrote as each pull request landed — so the commits a pull request
    brought in, which arrive through the second parent, are never met. It
    stops before the merge base, at `limit` shas, or where the next first
    parent is not in the listing. A listing that is not a list, or does not
    hold the tip, is an empty window.
    """
    if not isinstance(listing, list):
        return []
    by_sha = {}
    for item in listing:
        if isinstance(item, dict) and isinstance(item.get("sha"), str):
            by_sha.setdefault(item["sha"], item)
    window, sha = [], tip_sha
    while sha and sha in by_sha and sha != base_sha and len(window) < limit:
        window.append(sha)
        parents = by_sha[sha].get("parents")
        first = parents[0] if isinstance(parents, list) and parents else None
        sha = first.get("sha") if isinstance(first, dict) else None
    return window


def _read_window(main_window, main_sha):
    """[(sha, runs)] tip first, or None when the window cannot be used: not a
    list, empty, an entry that is not a (sha, payload) pair, a payload that
    cannot be read, or a first sha that is not the compare's `main` tip."""
    if not isinstance(main_window, list) or not main_window:
        return None
    commits = []
    for entry in main_window:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            return None
        sha, payload = entry
        if not isinstance(sha, str) or not sha:
            return None
        try:
            commits.append((sha, _check_runs(payload)))
        except ValueError:
            return None
    if commits[0][0] != main_sha:
        return None
    return commits


def _commit_state(runs, key):
    """'red' · 'green' · None (silent) for one check on one commit.

    A run counts when it is completed and green or red; `cancelled`,
    `skipped`, `neutral` and in-flight runs do not. Any counted red makes the
    commit red, the way `failed_check_names` reads the merge base."""
    green = False
    for run in runs:
        if _normalize(run.get("name") or "") != key or not _is_completed(run):
            continue
        conclusion = run.get("conclusion") or ""
        if conclusion in FAILED_CONCLUSIONS:
            return "red"
        if conclusion == "success":
            green = True
    return "green" if green else None


def _window_verdict(commits, name, base_red):
    """(verdict, red_sha, green_sha) for one failing check, by the DRE-6513
    table. The verdict is 'fixed', MAIN_STILL_RED, UNEVALUATED or OWN."""
    key = _normalize(name)
    newest, newest_red = None, None
    for sha, runs in commits:
        state = _commit_state(runs, key)
        if state and newest is None:
            newest = (sha, state)
        if state == "red" and newest_red is None:
            newest_red = sha
    if newest and newest[1] == "red":
        return MAIN_STILL_RED, newest[0], None
    if base_red:
        if newest is None:
            return UNEVALUATED, None, None
        return "fixed", None, newest[0]
    if newest_red is None:
        return OWN, None, None
    return "fixed", newest_red, newest[0]


def _is_completed(run) -> bool:
    """A run GitHub has finished. `status` is authoritative; a payload that
    omits it (a hand-rolled fixture, a trimmed `--jq` projection) is read off
    the conclusion, which is null until the run completes."""
    status = (run.get("status") or "").casefold()
    return status == "completed" if status else bool(run.get("conclusion"))


def decide(*, compare, head_checks, base_checks, main_window, receipts,
           cap) -> Decision:
    """Is this pull request red only on a fault `main` has since fixed?

    Every argument is a raw GitHub payload — nothing here reads the network.
    `main_window` is a list, tip first, of `(sha, check-runs payload)` pairs:
    `main`'s own merges since the merge base (`first_parent_window`). A window
    of the tip alone answers exactly as the tip-only rule did.

    The order below is the order the answers get cheaper to be wrong about:
    an unreadable input first, then the budget (the operator's off switch),
    then the geometry, and only then the check-run payloads.
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
    base_red = {_normalize(c) for c in carried}

    commits = _read_window(main_window, main_sha)
    if commits is None:
        return answer(UNEVALUATED,
                      f"`main`'s commits since merge base {base_sha[:8]} could "
                      f"not be read as a window starting at {main_sha[:8]}",
                      names)

    verdicts = []
    for name in names:
        is_base_red = _normalize(name) in base_red
        verdict, red_sha, green_sha = _window_verdict(commits, name,
                                                      is_base_red)
        verdicts.append((name, verdict, is_base_red, red_sha, green_sha))

    def named(action):
        return [v for v in verdicts if v[1] == action]

    if named(OWN):
        return answer(OWN,
                      f"{', '.join(v[0] for v in named(OWN))} not red on merge "
                      f"base `{base_sha[:8]}` and never red on the "
                      f"{len(commits)} `main` commits since — this pull "
                      f"request's own defect, and the fix loop owns it",
                      names)
    if named(MAIN_STILL_RED):
        culprit, _, _, red_sha, _ = named(MAIN_STILL_RED)[0]
        return answer(MAIN_STILL_RED,
                      f"{culprit} still fails on `main` {red_sha[:8]} — the "
                      f"Red-Main Repair loop owns it, and a refresh would "
                      f"re-inherit it",
                      names)
    if named(UNEVALUATED):
        culprit = named(UNEVALUATED)[0][0]
        return answer(UNEVALUATED,
                      f"{culprit} has no completed successful run on `main` "
                      f"{main_sha[:8]} or the {len(commits) - 1} `main` "
                      f"commit(s) behind it yet — try again next sweep",
                      names)

    evidence = [
        {"check": name, "base_red": is_base_red,
         "red_sha": base_sha if is_base_red else red_sha,
         "green_sha": green_sha}
        for name, _verdict, is_base_red, red_sha, green_sha in verdicts
    ]
    return Decision(
        REFRESH,
        f"{', '.join(names)} red on this head and red on `main` at or after "
        f"merge base {base_sha[:8]}, green on `main` since "
        f"({behind_by} commit(s) ahead) — the fault was main-side and `main` "
        f"has fixed it",
        list(names), base_sha, main_sha, behind_by, evidence,
    )


def receipt_detail(*, pr_number, head_sha, main_sha, base_sha, inherited,
                   used, cap, evidence=None) -> str:
    """The plain-English body the sweep hands to `pipeline_act.receipt()`.

    Opens with the marker so the act is idempotent per `main` commit, names
    the evidence, and states the cost honestly rather than selling the refresh
    as free. Not blocker-shaped (a bot comment whose first line opens with 🛑
    is read as a prior fix-loop blocker by fix_context.py) and carrying no
    verdict marker (standards/untrusted-content.md — those are an approval
    credential and only the critic writes one).

    `evidence` is `Decision.evidence`. Without it (None or empty — a
    Decision built with no evidence) the body is the merge-base wording this
    act was written with, byte for byte; with it, each bullet says what was
    read — the merge base, or the `main` merge that went red after it — and
    where `main` is green again (DRE-6513).
    """
    names = list(inherited)
    if not evidence:
        opening = [
            f"Pull request #{pr_number} was red on {len(names)} check(s) that "
            f"were red on its merge base (`{base_sha[:8]}`) too, and are green "
            f"on `main` at `{main_sha[:8]}`:",
            "",
            *[f"- **{name}** — red on `{head_sha[:8]}` and on the merge base, "
              f"green on `main`." for name in names],
        ]
    else:
        opening = [
            f"Pull request #{pr_number} was red on {len(evidence)} check(s) "
            f"that `main` was red on too and is green on now:",
            "",
            *[_evidence_bullet(item, base_sha) for item in evidence],
        ]
    lines = [
        marker(main_sha),
        "",
        *opening,
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


def _evidence_bullet(item, base_sha) -> str:
    name = item.get("check") or ""
    green = (item.get("green_sha") or "")[:8]
    if item.get("base_red"):
        return (f"- **{name}** — red on the merge base `{base_sha[:8]}`, "
                f"green on `main` at `{green}`.")
    red = (item.get("red_sha") or "")[:8]
    return (f"- **{name}** — not red on the merge base `{base_sha[:8]}`, but "
            f"`main` went red on it at `{red}` and is green on it at "
            f"`{green}`.")


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
    main_side = d.add_mutually_exclusive_group(required=True)
    main_side.add_argument("--main-checks-file",
                           help="the same, for the tip of `main` — read as a "
                                "window of the tip alone")
    main_side.add_argument("--main-window-file",
                           help='JSON list of {"sha", "check_runs"} objects, '
                                "one per `main` first-parent commit since the "
                                "merge base, tip first")
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

    compare = _or_unreadable(args.compare_file, None)
    if args.main_window_file:
        main_window = _window_file(_or_unreadable(args.main_window_file, None))
    else:
        # The tip alone: its sha is the compare's, and an unreadable compare
        # is answered before the window is looked at.
        tip_sha = (_read_compare(compare) or ("", "", ""))[2]
        main_window = [(tip_sha,
                        _or_unreadable(args.main_checks_file, None))]

    decision = decide(
        compare=compare,
        head_checks=head_checks,
        base_checks=_or_unreadable(args.base_checks_file, None),
        main_window=main_window,
        receipts=(_or_unreadable(args.receipts_file, [])
                  if args.receipts_file else []),
        cap=args.cap,
    )
    print(decision.action)
    print(f"stale_merge_ref: {decision.action} — {decision.reason}",
          file=sys.stderr)
    return 0


def _window_file(data):
    """The `--main-window-file` list as decide()'s `(sha, payload)` pairs. A
    file that is not that shape is passed on as _Unreadable, which decide()
    answers UNEVALUATED for."""
    if not isinstance(data, list):
        return _Unreadable()
    window = []
    for item in data:
        if not isinstance(item, dict) or "check_runs" not in item:
            return _Unreadable()
        window.append((item.get("sha"), {"check_runs": item["check_runs"]}))
    return window


class _Unreadable:
    """A file that would not parse. Nothing accepts it, so every reader in
    decide() answers UNEVALUATED for it — one unreadable-input rule, written
    once."""


if __name__ == "__main__":
    sys.exit(main())
