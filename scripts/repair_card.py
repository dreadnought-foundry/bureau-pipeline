#!/usr/bin/env python3
"""The card a Red-Main Repair files before it starts (DRE-3533, stdlib only).

Everything the board tracks has a card. Repair pull requests did not: the loop
opened its PR on `repair/<failing-sha>`, a ref with no card id — and the card id
in the head ref is what the rest of the pipeline reads. `linear-sync` closes a
card on merge by finding `DRE-<n>` there; the console shows a pull request's
card the same way. So a repair PR showed no card, closed no card, and nothing on
the board recorded that the repair had happened (PR #340, 2026-09-09: a real fix
for a real outage, invisible to the board, its bookkeeping filed by hand).

`red-main-repair.yml` calls this between the dispatch decision and the agent:

    repair_card.py open --repo <slug> --head-sha <sha> --workflow-name <name> \\
        --run-url <url> --attempt <n> --fallback-branch <repair/<sha>>

It prints `card=`, `card_url=`, `branch=` and `card_owed=` for `$GITHUB_OUTPUT`,
and the branch it prints is the one the agent creates and the Report step looks
for.

Three properties, in the order they matter:

  * **A Linear failure never blocks a repair.** Main is red; the bookkeeping is
    worth less than the fix. Every failure path here returns the CALLER's
    fallback branch with `card_owed=true` and exit 0 — the PR opens on the old
    cardless shape and says the card is owed. Nothing in this file raises.
  * **One card per failing commit.** The title is anchored on the workflow and
    the abbreviated sha — never the run id, because attempt 2 is a DIFFERENT
    failed run at the SAME commit and must find the first attempt's card
    instead of minting a second.
  * **The lane is a working lane, never Todo.** A card entering Todo is
    dispatched by the relay, which would put a second agent on work an agent is
    already doing. The repair is in flight before this card exists, so the card
    is filed where the work actually is, and the routing verdict — stamped
    through `routing_verdict.stamp_card`, the ONE writer of that vocabulary —
    says the fleet has nothing to dispatch for it.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import red_main_repair  # noqa: E402

#: Where the card lands. Never Todo — see the module docstring.
LANE = "In Progress"

#: The verdict the fleet's promoter reads: nothing to dispatch here. The
#: repair's OWN run is the work, and it is already running.
VERDICT = "WORKBENCH"

#: Marks and filters every repair card carries beside its `repo:` label and its
#: role. `hand-built` is what tells the stranded-card watchdog that no
#: dispatched run is coming for this card (the repair run is not one of its
#: dispatches), and `Bug` is what a red main is.
FIXED_LABELS = ("initiative:bureau", "Bug", "hand-built")

#: The repairer is an engineer-tier fixer (config/models.yaml puts it on the
#: workhorse ladder for exactly that reason), so the card carries the engineer
#: role wherever it is filed.
ROLE_LABEL = "agent:engineer"


class _Ops:
    """The Linear seam, in one object so the caller can hand a fake in.

    Imported lazily: this module's pure half (title, labels, body, branch) is
    read by tests and by the workflow's own dry paths without a Linear key.
    """

    def find_open(self, title):
        import linear_ops

        return linear_ops.find_open(title)

    def create_card(self, title, description, *, repo_slug, labels=(),
                    lane="Planning"):
        import linear_ops

        return linear_ops.create_card(title, description, repo_slug=repo_slug,
                                      labels=labels, lane=lane)

    def cmd_comment(self, identifier, body, *flags):
        import linear_ops

        return linear_ops.cmd_comment(identifier, body, *flags)

    def stamp_card(self, identifier, name, why):
        import routing_verdict

        return routing_verdict.stamp_card(identifier, name, why)


def card_title(workflow_name: str, head_sha: str) -> str:
    """The card's title — the workflow that went red and the commit it went red
    at, abbreviated. Deliberately NOT the run id: attempt 2 arrives from a
    different failed run at the same commit and finds this card by exact
    title."""
    return (
        f"Red main repaired — {workflow_name or 'CI'} failed at "
        f"{head_sha[:red_main_repair.SHA_CHARS]}"
    )


def card_labels(repo_slug: str) -> list[str]:
    """Every label the card carries, `repo:<slug>` first — the whole answer in
    one place, so a reader never has to know that `create_card` prepends the
    repo label itself and deduplicates by name."""
    return [f"repo:{repo_slug}", *FIXED_LABELS, ROLE_LABEL]


def card_body(*, workflow_name: str, run_url: str, head_sha: str,
              repo_slug: str, attempt: int) -> str:
    """The card, in the plain English a person reads on the board."""
    return "\n".join([
        f"**Repo:** {repo_slug}",
        "",
        "## What happened",
        "",
        f"CI went red on this repo's main branch: **{workflow_name or 'CI'}** "
        f"failed at commit `{head_sha}`. The Red-Main Repair loop is fixing it "
        "forward on its own branch, through the ordinary pull request the "
        "critic reviews and the merge gate merges — nobody merges it by hand "
        "(adr-red-main-auto-repair).",
        "",
        f"- The failed run: {run_url}",
        f"- Repair attempt {attempt} of 2 for this commit.",
        "",
        "This card exists so the repair is on the board like everything else: "
        "it opens with the work, carries the pull request, and closes when that "
        "pull request merges.",
        "",
        "## Acceptance criteria",
        "",
        "- [ ] The repair pull request merges and main is green again.",
        "- [ ] Closed by the merge, through the card id in the branch name.",
        "",
        "## Not in this card",
        "",
        "- Any other failure at any other commit — each red main gets its own "
        "card.",
        "- A third repair attempt: after two, the loop stops and raises a card "
        "asking for a human.",
    ]) + "\n"


def verdict_why(run_url: str, attempt: int) -> str:
    """Why this card is not the fleet's to dispatch, in one sentence."""
    return (
        f"Filed by the Red-Main Repair loop (run {run_url}) for repair attempt "
        f"{attempt}. The repair agent is ALREADY running and opens the pull "
        "request itself, so there is nothing here to dispatch — the card is "
        "the board's record of a repair in flight."
    )


def attempt_note(run_url: str, attempt: int) -> str:
    """What a reused card is told when a later attempt starts."""
    return (
        f"🔁 Repair attempt {attempt} of 2 at this commit has started — the "
        f"previous attempt's pull request did not merge. Failed run: {run_url}"
    )


def open_card(*, repo_slug: str, workflow_name: str, head_sha: str,
              run_url: str, attempt: int, fallback_branch: str,
              ops=None) -> dict:
    """File (or find) the repair's card and name its branch. NEVER raises.

    Returns `{card, card_url, branch, card_owed, note}`. `card_owed` is the
    honest half: with it true the repair still happens, on `fallback_branch`,
    and the pull request says the card is owed.
    """
    ops = ops or _Ops()
    title = card_title(workflow_name, head_sha)

    try:
        existing = ops.find_open(title)
    except Exception as exc:  # noqa: BLE001 — Linear is never worth a red main
        existing = None
        print(f"repair card: could not search for an existing card ({exc})",
              file=sys.stderr)

    if existing:
        try:
            ops.cmd_comment(existing, attempt_note(run_url, attempt))
        except Exception as exc:  # noqa: BLE001
            print(f"repair card: could not comment on {existing} ({exc})",
                  file=sys.stderr)
        return _found(existing, "", head_sha, attempt)

    try:
        issue = ops.create_card(
            title,
            card_body(workflow_name=workflow_name, run_url=run_url,
                      head_sha=head_sha, repo_slug=repo_slug, attempt=attempt),
            repo_slug=repo_slug,
            labels=card_labels(repo_slug),
            lane=LANE,
        )
    except Exception as exc:  # noqa: BLE001 — the ONE thing that must not fail
        print(f"repair card: NOT filed ({exc}) — the repair proceeds on "
              f"{fallback_branch} and the pull request says the card is owed",
              file=sys.stderr)
        return {"card": "", "card_url": "", "branch": fallback_branch,
                "card_owed": True, "note": str(exc).replace("\n", " ")[:300]}

    card = issue["identifier"]
    try:
        # The one writer of the routing vocabulary — it posts the verdict
        # comment AND applies the marks the verdict declares. A failure here
        # loses a comment, not the card, so the card still names the branch.
        ops.stamp_card(card, VERDICT, verdict_why(run_url, attempt))
    except Exception as exc:  # noqa: BLE001
        print(f"repair card: {card} filed but its routing verdict did not "
              f"stamp ({exc})", file=sys.stderr)
    return _found(card, issue.get("url") or "", head_sha, attempt)


def _found(card: str, url: str, head_sha: str, attempt: int) -> dict:
    return {
        "card": card,
        "card_url": url,
        "branch": red_main_repair.repair_branch(head_sha, attempt, card=card),
        "card_owed": False,
        "note": "",
    }


def outputs(result: dict) -> str:
    """`$GITHUB_OUTPUT` lines. Single-line values only: a multi-line value
    would let the failure note leak into another key."""
    def one(text: str) -> str:
        return " ".join(str(text or "").split())

    return "\n".join([
        f"card={one(result['card'])}",
        f"card_url={one(result['card_url'])}",
        f"branch={one(result['branch'])}",
        f"card_owed={'true' if result['card_owed'] else 'false'}",
        f"card_note={one(result.get('note'))}",
    ]) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("open")
    o.add_argument("--repo", required=True)
    o.add_argument("--head-sha", required=True)
    o.add_argument("--run-url", required=True)
    o.add_argument("--fallback-branch", required=True)
    o.add_argument("--workflow-name", default="")
    o.add_argument("--attempt", type=int, default=1)
    args = parser.parse_args(argv)

    result = open_card(
        repo_slug=args.repo, workflow_name=args.workflow_name,
        head_sha=args.head_sha, run_url=args.run_url, attempt=args.attempt,
        fallback_branch=args.fallback_branch,
    )
    sys.stdout.write(outputs(result))
    print(
        f"repair card: {result['card'] or 'NOT FILED'} → branch "
        f"{result['branch']}", file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
