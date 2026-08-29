#!/usr/bin/env python3
"""A failing check that also fails on the merge base is not the PR's (DRE-2820).

Origin (live, 2026-08-29). The Integration Harness went red on `main` and
stayed red. PR #199 (DRE-2721) and PR #201 (DRE-2813) both inherited the
failure: both were green on their own work, #201 was critic-APPROVED, and both
sat blocked all day. #199's fix agent spent attempts trying to clear a check
that was never its fault. Nothing on either pull request distinguished a
failure the branch CAUSED from one it INHERITED, so two fix agents and one
human could not tell them apart — and the deeper waste of that day was not the
red harness, it was those fourteen hours.

The comparison that settles it is the MERGE BASE: the commit the branch was cut
from. A check that is red on the head AND red on the merge base was red before
this branch existed. Saying that on the PR, in one comment, is the whole
mechanism.

Deliberately narrow, and deliberately not a verdict:

  * a check is INHERITED only when it FAILS on both sides. Green here, red
    there is a check the branch fixed; red here, green there is the branch's
    own defect;
  * `cancelled` on the base proves nothing and never excuses a red head — the
    same rule unfixable_checks.py already applies to the head;
  * a base whose check runs cannot be read reports UNEVALUATED, never a pass:
    "we could not look" is not "your fault" and is not "not your fault";
  * the notice is not blocker-shaped (a bot comment whose first line opens
    with 🛑 is read as a prior fix-loop blocker by fix_context.py) and carries
    no verdict marker (standards/untrusted-content.md — those are an approval
    credential and only the critic writes one).

Read as a CLI by agent-fix.yml, before it spends an attempt:

    python3 inherited_failures.py decide --checks-file <head check-runs json> \\
        --base-checks-file <merge-base check-runs json> --base-sha <sha> \\
        [--pr N] [--comment-out FILE] [--agent-note-out FILE]

stdout line 1 is the decision (`inherited`, `own`, `unevaluated`). Exit 0 =
decided; exit 2 = the HEAD payload could not be read, which is loud for the
same reason it is loud in unfixable_checks.py: a check-runs read that silently
answered anything would steer the next fix attempt on a guess.
"""

from __future__ import annotations

import argparse
import json
import sys

# The head-side reader, already proven against every shape `gh api` emits
# (bare object, --paginate --slurp pages, flat list) and already carrying the
# conclusion vocabulary. One source for "this check reported a failure".
from unfixable_checks import failed_check_names

INHERITED = "inherited"
OWN = "own"
UNEVALUATED = "unevaluated"

# Binds the notice to a head sha so a re-fired critic does not repost it (the
# fix-convergence-halt receipt pattern, DRE-2024).
INHERITED_MARKER = "inherited-failure"


def _normalize(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def inherited(head_payload, base_payload) -> list:
    """The head's failing check names that ALSO fail on the merge base, in the
    head's order and the head's spelling.

    Raises ValueError if either payload cannot be read as check runs — the
    caller decides what an unreadable side costs (see main()).
    """
    base_failed = {_normalize(n) for n in failed_check_names(base_payload)}
    base_failed.discard("")
    names, seen = [], set()
    for name in failed_check_names(head_payload):
        key = _normalize(name)
        if key and key in base_failed and key not in seen:
            seen.add(key)
            names.append(name)
    return names


def _bullets(names) -> list:
    return [f"- **{name}** — red on this head, and red on the merge base."
            for name in names]


def pr_comment(names, base_sha: str, pr=None) -> str:
    """The comment posted on the pull request. States the fact, names the
    evidence, and tells whoever reads it next what NOT to spend time on."""
    where = f" PR #{pr}." if pr is not None else ""
    parts = [
        f"🧬 {INHERITED_MARKER}: {len(names)} failing check(s) on this branch "
        f"also fail on the merge base (`{base_sha[:8]}`), so they are not this "
        f"pull request's defect.{where}",
        "",
        *_bullets(names),
        "",
        "What this means for a fix agent: **do not spend fix attempts on the "
        "check(s) above.** Nothing on this branch caused them and nothing you "
        "can push here clears them — they clear when the default branch does. "
        "Fix what this pull request actually broke; if that is nothing, say so "
        "and stop rather than editing code to chase a red check you did not "
        "cause.",
        "",
        "The broken default branch is the Red-Main Repair loop's job "
        "(adr-red-main-auto-repair), not this PR's.",
    ]
    return "\n".join(parts).rstrip() + "\n"


def agent_note(names, base_sha: str) -> str:
    """The same facts, as the file the fixing agent reads with its context."""
    parts = [
        "# Inherited check failures",
        "",
        f"These checks fail on this branch AND on its merge base "
        f"(`{base_sha[:8]}`). They were red before this branch existed, so "
        f"they are not this pull request's defect:",
        "",
        *_bullets(names),
        "",
        "Do not spend fix attempts on them, and do not weaken or skip them to "
        "go green. Address only what this pull request actually broke. If the "
        "critic's findings are all inherited failures, write that to "
        "/tmp/fix-blocker.txt and stop.",
    ]
    return "\n".join(parts).rstrip() + "\n"


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Decide whether a PR's red checks are inherited from its "
                    "merge base"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decide")
    d.add_argument("--checks-file", required=True,
                   help="JSON from `gh api repos/{repo}/commits/{head}/check-runs`")
    d.add_argument("--base-checks-file", required=True,
                   help="the same, for the merge-base commit")
    d.add_argument("--base-sha", required=True)
    d.add_argument("--pr", type=int)
    d.add_argument("--comment-out")
    d.add_argument("--agent-note-out")
    args = parser.parse_args(argv)

    try:
        head_failed = failed_check_names(_load(args.checks_file))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        # Loud: the head's own red checks are the subject. Guessing here would
        # steer the next fix attempt on nothing.
        print(f"inherited_failures: cannot read head check runs: {exc}",
              file=sys.stderr)
        return 2

    try:
        base_payload = _load(args.base_checks_file)
        names = inherited({"check_runs": [{"name": n, "conclusion": "failure"}
                                          for n in head_failed]}, base_payload)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        # Never a pass and never a verdict: we could not look at the base.
        print(UNEVALUATED)
        print(f"inherited_failures: cannot read merge-base check runs: {exc}",
              file=sys.stderr)
        return 0

    print(INHERITED if names else OWN)
    print(
        f"{len(head_failed)} failed check(s) on the head; "
        f"{len(names)} also failing on merge base {args.base_sha[:8]}"
        + (f": {', '.join(names)}" if names else ""),
        file=sys.stderr,
    )
    if names:
        if args.comment_out:
            with open(args.comment_out, "w", encoding="utf-8") as fh:
                fh.write(pr_comment(names, args.base_sha, args.pr))
        if args.agent_note_out:
            with open(args.agent_note_out, "w", encoding="utf-8") as fh:
                fh.write(agent_note(names, args.base_sha))
    return 0


if __name__ == "__main__":
    sys.exit(main())
