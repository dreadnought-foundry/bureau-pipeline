#!/usr/bin/env python3
"""The cards a stacked parent's landing just put on the default branch (DRE-4650).

    python3 scripts/stacked_landing.py landed <repo> <merged-head-ref> <default-branch>

## The other half of "merged is not landed"

DRE-4647 stopped `linear-sync` closing a card whose pull request merged into
another agent's branch: the code is on no default branch, so the card keeps its
state and gets one `🪜 Merged into` receipt instead. That is the refusal. This
is the closer.

When the parent finally lands, the pull requests that merged INTO its head ref
are the ones whose code has just reached the default branch — and nothing moved
them. On 2026-09-22 at 09:57 PT agent-bureau #2690
(`agent/DRE-4534-record-reads`) merged into `main` carrying #2691 and #2692
with it; with the sibling in place DRE-4535 and DRE-4536 would have sat In
Review with a receipt and no closer, for ever.

## Keyed on GitHub, never on the receipt

The list of cards this landing closes is `gh pr list --state merged --base
<landed head>`: GitHub's own record of which pull requests were merged into the
branch that just landed. The sibling's receipt is read as DOCUMENTATION ONLY —
a card that carries it and a card that does not are closed exactly the same
way, because the comment is a human-readable trace and the merged-PR list is
the fact. (A card whose receipt was never posted — the comment failed, the run
died — is closed here regardless, which is the whole reason not to key on it.)

Each merged head names its card by the SAME anchored own-branch rule the
`Card → Done` step applies to its own head ref: `agent/DRE-<n>-` or
`repair/DRE-<n>-`, delimiter required (DRE-2027, DRE-2025 — DRE-142 can never
act for DRE-1428). A head that names no card closes nothing; a hand-named
`ops/...` branch is the operator's, and the operator closes those cards. A
stacked dependabot branch cannot exist — dependabot opens against the default
branch and names its own branch — so the DRE-3665 body-marker arm has no
meaning here and is not read.

The close itself is `linear_ops.py card-done <card> <url>`, the one seam that
already knows which cards a merge may not close: the `no-code` / `DEMO:` /
epic guard inside it still refuses those three classes and comments instead
(the six portico false closes, DRE-3119). Nothing about that decision is
re-implemented here.

## Idempotent, bounded, and fail-closed

**Idempotent.** A card already in a terminal state is skipped, with a line
saying which state, so a re-run of the merge job closes nothing twice. The
terminal set is `prose_blockers.TERMINAL` — the one definition the dependency
gate reads — and it matters that it includes `Canceled`: `card-done`'s state
write treats Done as a terminal TARGET and writes it without the pre-read
guard, so a Canceled card would be quietly resurrected by the second run.

**Bounded.** A grandchild stacked on a child that stacked on the parent landed
too, so the walk recurses one level per landed head up to `MAX_DEPTH`. Every
head is listed at most once, which terminates a cycle GitHub cannot really
make but a walk on the merge path must survive anyway.

**Fail closed.** A GitHub read that fails lists nothing, says
`could not list stacked pull requests` naming the base it could not read, and
closes nothing — no card is ever guessed Done. The same rule covers an
unparseable answer. The process exits 0 either way: this runs after the merged
card's own `card-done` on the merge path, and a closer that could not look is
not a reason to fail a merge that succeeded. The `*/15` reconcile sweep and the
next landing are the backstops.

A Linear read we could not make falls the other way, for the reason
`merge_sweep_gate` falls that way: "we could not look" is not "it is already
Done", and `card-done` carries the guard that actually decides. The one
exception is a RATE-LIMITED read — a write against an exhausted quota cannot
succeed and deepens it (DRE-1921, `standards/vendor-boundaries.md` Q5), so that
card is left for the sweep.

## Why it imports linear_ops for the read and runs it for the write

The state read is one query and belongs in-process, exactly as
`merge_sweep_gate` reads the merged card. The close is the CLI command, run per
card, so one card that cannot be closed — a Linear error, a guard refusal —
does not take the rest of the landing down with it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404 — fixed argv, no shell
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import linear_ops  # noqa: E402
import prose_blockers  # noqa: E402 — ONE definition of "terminal" (DRE-2676)

#: The own-branch rule, character for character as `linear-sync.yml`'s
#: `Card → Done` step applies it to its own head ref: anchored, either prefix,
#: and the delimiter after the number REQUIRED so DRE-142 can never act for
#: DRE-1428 (DRE-2025). Case-insensitive, normalized to upper case (DRE-2003).
_CARD_RE = re.compile(r"^(?:agent|repair)/(DRE-\d+)-", re.IGNORECASE)

#: A card in one of these states is finished and is never closed again. The one
#: definition the dependency gate reads, imported rather than restated.
TERMINAL = prose_blockers.TERMINAL

#: How many levels of stacking the walk descends. A child of the landed head is
#: level 1, its grandchild level 2. Small and fixed: each level is one GitHub
#: read per head on the merge path, and stacks deeper than this are not a shape
#: the fleet builds.
MAX_DEPTH = 5

#: How many merged pull requests are read per base. `gh pr list` defaults to 30;
#: a landed head with more children than this is not a shape that exists, and
#: the cap is here so the read is bounded rather than absent.
PR_PAGE = 100

#: The line a person greps for when a landing closed nothing. Fixed, because it
#: is what the job log is read for.
COULD_NOT_LIST = "could not list stacked pull requests"

#: The one card fact this module reads for itself.
STATE_QUERY = """query($id: String!) { issue(id: $id) {
     identifier state { name }
   } }"""

_LINEAR_OPS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linear_ops.py")

_LOG = "stacked landing:"


def card_from_branch(head_ref: str | None) -> str | None:
    """The card `head_ref` is the own branch of, upper-cased, or None."""
    match = _CARD_RE.match((head_ref or "").strip())
    return match.group(1).upper() if match else None


def merged_prs_into(repo: str, base: str) -> list[dict] | None:
    """Every MERGED pull request whose base is `base`, or None if the read
    failed. None is not an empty list and is never treated as one."""
    try:
        proc = subprocess.run(  # nosec B603 B607 — fixed argv, no shell
            ["gh", "pr", "list", "--repo", repo, "--state", "merged",
             "--base", base, "--limit", str(PR_PAGE),
             "--json", "number,headRefName,baseRefName,url"],
            capture_output=True, text=True, check=False,
        )
    except OSError as e:  # gh missing from the runner entirely
        print(f"{_LOG} {COULD_NOT_LIST} into {base!r} ({e}) — closing nothing")
        return None
    if proc.returncode != 0:
        print(
            f"{_LOG} {COULD_NOT_LIST} into {base!r} — `gh pr list` exited "
            f"{proc.returncode}: {(proc.stderr or '').strip()}. Closing nothing: "
            f"a read we could not make is not a card we may guess Done."
        )
        return None
    try:
        rows = json.loads(proc.stdout or "[]")
    except ValueError as e:
        print(f"{_LOG} {COULD_NOT_LIST} into {base!r} — unreadable answer ({e})")
        return None
    if not isinstance(rows, list):
        print(f"{_LOG} {COULD_NOT_LIST} into {base!r} — answer was not a list")
        return None
    # GitHub's own `--base` filter is not taken on trust: a row that names
    # another base did not land with this head, whatever the query said.
    kept = []
    for row in rows:
        row = row if isinstance(row, dict) else {}
        if row.get("baseRefName") == base and row.get("headRefName"):
            kept.append(row)
        else:
            print(
                f"{_LOG} #{row.get('number')} lists base "
                f"{row.get('baseRefName')!r}, not {base!r} — not part of this "
                f"landing"
            )
    return kept


def card_state(identifier: str) -> str | None:
    """The card's state name, or None when it could not be read."""
    issue = (linear_ops.gql(STATE_QUERY, {"id": identifier}) or {}).get("issue")
    return ((issue or {}).get("state") or {}).get("name")


def should_close(identifier: str) -> bool:
    """Is this card still open enough to be closed by a landing?

    Unknown falls OPEN — `card-done` carries the guard that actually decides —
    except for a rate-limited read, which falls closed (DRE-1921).
    """
    try:
        state = card_state(identifier)
    except linear_ops.LinearRateLimited as e:
        print(
            f"{_LOG} {identifier} unreadable — the Linear quota is exhausted "
            f"({e}); not closing it, because a write against an exhausted limit "
            f"cannot succeed and deepens it. The cron sweep picks it up."
        )
        return False
    except Exception as e:  # noqa: BLE001 — any other unknown falls OPEN
        print(
            f"{_LOG} could not read {identifier} ({e}) — closing it anyway; "
            f"'we could not look' is not 'it is already Done', and card-done "
            f"carries the no-code / DEMO: / epic guard."
        )
        return True
    if state in TERMINAL:
        print(
            f"{_LOG} {identifier} is already {state!r} — skipping. This landing "
            f"has already been acted on, so the re-run closes nothing twice."
        )
        return False
    return True


def close(identifier: str, pr_url: str) -> bool:
    """`linear_ops.py card-done` for one card. Never raises: one card that
    cannot be closed must not take the rest of the landing down with it."""
    proc = subprocess.run(  # nosec B603 — fixed argv, no shell
        [sys.executable, _LINEAR_OPS, "card-done", identifier, pr_url],
        check=False,
    )
    if proc.returncode != 0:
        print(
            f"{_LOG} card-done for {identifier} exited {proc.returncode} — the "
            f"card was NOT closed by this landing; the reconcile sweep is the "
            f"backstop ({pr_url})"
        )
        return False
    return True


def landed(repo: str, head_ref: str, default_branch: str) -> list[str]:
    """Close every card whose pull request merged into `head_ref`, and into
    what merged into that, down to MAX_DEPTH. Returns the cards closed."""
    if head_ref == default_branch:
        print(
            f"{_LOG} the landed head is the default branch {default_branch!r} "
            f"itself — refusing to list every merged pull request in the "
            f"repository. Nothing was stacked on it."
        )
        return []
    print(
        f"{_LOG} {head_ref!r} landed on {default_branch!r} — closing the cards "
        f"whose pull requests merged into it"
    )
    closed: list[str] = []
    seen = {head_ref}
    frontier = [head_ref]
    for depth in range(1, MAX_DEPTH + 1):
        following: list[str] = []
        for base in frontier:
            for row in merged_prs_into(repo, base) or []:
                head = row.get("headRefName") or ""
                if head in seen:
                    print(
                        f"{_LOG} {head!r} has already been walked this run — "
                        f"not walking it again"
                    )
                    continue
                seen.add(head)
                following.append(head)
                identifier = card_from_branch(head)
                if identifier is None:
                    print(
                        f"{_LOG} #{row.get('number')} merged into {base!r} on "
                        f"{head!r}, which is not a card's own branch — closing "
                        f"nothing for it"
                    )
                    continue
                if not should_close(identifier):
                    continue
                print(
                    f"{_LOG} {identifier} reached {default_branch!r} when "
                    f"{head_ref!r} landed (merged into {base!r}) — closing it on "
                    f"{row.get('url')}"
                )
                if close(identifier, str(row.get("url") or "")):
                    closed.append(identifier)
        frontier = following
        if not frontier:
            break
        if depth == MAX_DEPTH:
            print(
                f"{_LOG} depth bound {MAX_DEPTH} reached with "
                f"{len(frontier)} branch(es) still unread "
                f"({', '.join(sorted(frontier))}) — stopping. The reconcile "
                f"sweep and the next landing are the backstops."
            )
    print(
        f"{_LOG} {head_ref!r} closed {len(closed)} card(s): "
        f"{', '.join(closed) or 'none'}"
    )
    return closed


def main(argv: list[str]) -> int:
    if argv[:1] != ["landed"] or len(argv) != 4 or not all(a.strip() for a in argv):
        print(
            "usage: stacked_landing.py landed <repo> <merged-head-ref> "
            "<default-branch>",
            file=sys.stderr,
        )
        return 2
    _, repo, head_ref, default_branch = argv
    try:
        landed(repo.strip(), head_ref.strip(), default_branch.strip())
    except Exception as e:  # noqa: BLE001 — the merge path never goes red here
        print(
            f"{_LOG} {COULD_NOT_LIST} into {head_ref!r} ({e}) — closing nothing. "
            f"This runs after the merged card's own card-done; a closer that "
            f"crashed is not a reason to fail a merge that succeeded."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
