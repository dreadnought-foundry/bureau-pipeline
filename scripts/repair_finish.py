#!/usr/bin/env python3
"""Open the repair pull request the agent pushed and never opened (DRE-6525, stdlib only).

On 2026-10-09 at 16:23 PT the Report step of Red-Main Repair run 38003951327
found no pull request and no escalation note for
`repair/DRE-6511-bcb36adf6037` and failed the run: `repair agent produced
neither a PR nor an escalation`. The branch was on the remote, one commit ahead
of `main`, carrying the right fix. The operator opened pull request #883 from it
by hand 75 minutes later. The work was done; only the last step was missing.

`red-main-repair.yml`'s Report step calls this when the agent left neither a
pull request nor an escalation note, before it fails the run for the medic:

    repair_finish.py finish --repo <owner/name> --branch <repair/...> \\
        --default-branch <main> --failed-run-url <url> \\
        --workflow-name <name> [--card-url <url>]

It is written to be called from a second place too — the reconcile sweep's
`finish-unlanded` decision (DRE-6520's next child) — so the flags, the outputs
and the exit codes below are the contract, and both callers hold to them.

Three reads, through `ops`: the branch's head ref (a 404 is "no branch"), the
compare of the default branch with it, and the pull requests of ANY state on
it. `decide_finish` turns them into one answer:

  * `opened-needed` — the branch is there, ahead of the default branch, and no
    pull request of any state was ever opened on it. The only answer that
    writes: one `gh pr create`, then one card comment.
  * `already-open` — a pull request of any state is on the branch. Its URL is
    reported and nothing is written. This is what makes a re-run safe: the
    second run finds the first run's pull request and stops.
  * `no-branch`, `nothing-ahead` — nothing to open. `gh pr create` would refuse
    a head with no commits ahead anyway; this turns it away before any write.
  * `unreadable` — a read answered None, raised, or a compare carried no
    integer `ahead_by` (or no commits to name the pull request from). An
    unreadable answer is never a yes.

Outputs go to stdout as `$GITHUB_OUTPUT` lines through `github_output.render`:
`finish=<opened|already-open|no-branch|nothing-ahead|unreadable>`, `pr_url=`
and `card=`. Human text goes to stderr. Exit 0 for `opened` and
`already-open`, exit 2 for the rest, so a calling step can fall through to
its own error line.

A Linear failure never undoes or fails the open: main is red, the pull request
is the fix, and the card comment is a note to a person. A crash between the
open and the comment leaves the pull request and loses the comment — the next
run answers `already-open` rather than opening a second one.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404 — the gh CLI, fixed args, shell=False
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import github_output  # noqa: E402
import red_main_repair  # noqa: E402

#: The five answers, as `finish=` spells them — `OPENED_NEEDED` is the
#: decision, `OPENED` the outcome once the pull request is open.
OPENED_NEEDED = "opened-needed"
OPENED = "opened"
ALREADY_OPEN = "already-open"
NO_BRANCH = "no-branch"
NOTHING_AHEAD = "nothing-ahead"
UNREADABLE = "unreadable"

#: The answers a calling step treats as done.
FINISHED = (OPENED, ALREADY_OPEN)

#: The first words of the card comment.
FINISH_MARKER = "📬 Pull request opened by the repair workflow"

#: The title every repair pull request carries, the agent's included.
TITLE_PREFIX = "fix(red-main): "

#: The body's last line: who opened it and why, and that it is judged like
#: any other repair. Never verdict-shaped.
OPENED_LINE = (
    "Opened by the Red-Main Repair workflow: the agent pushed this branch and "
    "stopped before writing its own pull request description. The critic and "
    "the merge gate judge it as they judge any repair."
)

#: What the body says when the branch carries no card (the cardless fallback
#: `repair_card.open_card` sends a repair to when Linear is down).
CARD_OWED_LINE = ("The repair card is owed for this repair: Linear could not "
                  "be reached when it started.")

#: `repair/DRE-<n>-<sha12>`, suffixed `-N` from attempt 2 — the shape
#: `red_main_repair.repair_branch` writes when a card was filed.
_CARD_BRANCH = re.compile(
    rf"^repair/(DRE-\d+)-[0-9a-f]{{{red_main_repair.SHA_CHARS}}}(?:-\d+)?$")


def branch_card(branch: str) -> str:
    """The card id a `repair/DRE-<n>-<sha12>` branch carries, or ""."""
    found = _CARD_BRANCH.match(branch or "")
    return found.group(1) if found else ""


def _ahead_by(compare) -> int | None:
    ahead = compare.get("ahead_by") if isinstance(compare, dict) else None
    if not isinstance(ahead, int) or isinstance(ahead, bool):
        return None
    return ahead


def _commits(compare) -> list:
    commits = compare.get("commits") if isinstance(compare, dict) else None
    return [c for c in commits if isinstance(c, dict)] if isinstance(commits, list) else []


def decide_finish(ref, compare, pulls) -> str:
    """One answer from the three reads. Pure.

    `ref` is the branch's ref payload, False when GitHub answered 404, None
    when it could not be read. `compare` is GitHub's compare payload for
    `<default>...<branch>` or None. `pulls` is the pull requests of any state
    on the branch, or None.

    The pull requests are asked first: one on the branch is the answer however
    the branch looks now — a merged repair whose branch was deleted is
    `already-open`, never `no-branch`.
    """
    if not isinstance(pulls, list):
        return UNREADABLE
    if pulls:
        return ALREADY_OPEN
    if ref is False:
        return NO_BRANCH
    if not isinstance(ref, dict):
        return UNREADABLE
    ahead = _ahead_by(compare)
    if ahead is None:
        return UNREADABLE
    if ahead <= 0:
        return NOTHING_AHEAD
    if not _commits(compare):
        return UNREADABLE
    return OPENED_NEEDED


def newest_pull_url(pulls) -> str:
    """The URL of the newest pull request on the branch (highest number)."""
    best = max(
        (p for p in pulls or () if isinstance(p, dict)),
        key=lambda p: p.get("number") if isinstance(p.get("number"), int) else -1,
        default=None,
    )
    return (best or {}).get("url") or ""


def _subject(commit: dict) -> str:
    message = (commit.get("commit") or {}).get("message") or ""
    return message.split("\n", 1)[0].strip()


def pr_title(compare: dict) -> str:
    """`fix(red-main): ` and the newest commit's subject, never prefixed twice."""
    subject = _subject(_commits(compare)[-1])
    if subject.startswith(TITLE_PREFIX):
        return subject
    return TITLE_PREFIX + subject


def pr_body(*, compare: dict, failed_run_url: str, card_url: str,
            card: str) -> str:
    """The pull request's description — the run, the card, the commits, and
    who opened it."""
    if card_url:
        card_line = f"Card: {card_url}"
    elif card:
        card_line = f"Card: {card}"
    else:
        card_line = CARD_OWED_LINE
    commits = [
        f"- {(c.get('sha') or '')[:red_main_repair.SHA_CHARS]} {_subject(c)}"
        for c in _commits(compare)
    ]
    return "\n".join([
        f"Failed run: {failed_run_url}",
        "",
        card_line,
        "",
        "Commits:",
        "",
        *commits,
        "",
        OPENED_LINE,
    ]) + "\n"


def finish_note(pr_url: str, branch: str, workflow_name: str) -> str:
    """The ONE card comment an open gets. A note to a person; nothing reads it."""
    return (
        f"{FINISH_MARKER}: {pr_url}\n\n"
        f"The repair agent pushed its fix for **{workflow_name or 'CI'}** to "
        f"`{branch}` and stopped before opening the pull request, so the "
        "Red-Main Repair workflow opened it. The critic and the merge gate "
        "judge it as they judge any repair, and its merge closes this card."
    )


class _Ops:
    """The GitHub and Linear seams, in one object so a caller hands a fake in."""

    def branch_ref(self, repo: str, branch: str):
        """The ref payload, False on a 404, None when it cannot be read."""
        done = self._gh("api", f"repos/{repo}/git/ref/heads/{branch}")
        if done.returncode != 0:
            said = f"{done.stderr}\n{done.stdout}"
            if "HTTP 404" in said or "Not Found" in said:
                return False
            return None
        return self._parse(done.stdout, dict)

    def compare(self, repo: str, base: str, head: str):
        done = self._gh("api", f"repos/{repo}/compare/{base}...{head}")
        return self._parse(done.stdout, dict) if done.returncode == 0 else None

    def pulls_for_head(self, repo: str, branch: str):
        done = self._gh("pr", "list", "--repo", repo, "--head", branch,
                        "--state", "all", "--json", "number,url,state")
        return self._parse(done.stdout, list) if done.returncode == 0 else None

    def create_pr(self, repo: str, *, base: str, head: str, title: str,
                  body: str) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".md",
                                         delete=False) as fh:
            fh.write(body)
            path = fh.name
        try:
            done = self._gh("pr", "create", "--repo", repo, "--base", base,
                            "--head", head, "--title", title,
                            "--body-file", path)
        finally:
            os.unlink(path)
        if done.returncode != 0:
            raise RuntimeError(
                f"gh pr create failed rc={done.returncode}: "
                f"{done.stderr.strip()[:300]}")
        # gh prints the new pull request's URL as its last line.
        lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
        return lines[-1] if lines else ""

    # Named for the seam it IS, as `repair_card._Ops.cmd_comment` is: the
    # completeness guard (scripts/check_act_receipts.py) reads its CALLERS,
    # which is where the body is composed.
    def cmd_comment(self, identifier: str, body: str) -> None:
        import linear_ops

        linear_ops.cmd_comment(identifier, body)

    @staticmethod
    def _gh(*args: str) -> subprocess.CompletedProcess:
        # B603/B607: program-constructed args, shell=False, gh resolves via
        # PATH by design — the same call shape repair_card.py makes.
        return subprocess.run(  # nosec B603 B607
            ["gh", *args], capture_output=True, text=True, check=False)

    @staticmethod
    def _parse(text: str, kind: type):
        try:
            payload = json.loads(text or "")
        except ValueError:
            return None
        return payload if isinstance(payload, kind) else None


def _read(label: str, read):
    """One read, with a raise turned into None and said on stderr."""
    try:
        return read()
    except Exception as exc:  # noqa: BLE001 — an unreadable read is an UNKNOWN
        print(f"repair finish: {label} could not be read ({exc})", file=sys.stderr)
        return None


def finish(*, repo: str, branch: str, default_branch: str, failed_run_url: str,
           workflow_name: str, card_url: str = "", ops=None) -> dict:
    """Open the pull request when one is owed. Returns `{finish, pr_url, card}`."""
    ops = ops or _Ops()
    card = branch_card(branch)
    ref = _read(f"the head of {branch}", lambda: ops.branch_ref(repo, branch))
    compare = _read(f"{default_branch}...{branch}",
                    lambda: ops.compare(repo, default_branch, branch))
    pulls = _read(f"the pull requests on {branch}",
                  lambda: ops.pulls_for_head(repo, branch))

    decision = decide_finish(ref, compare, pulls)
    if decision == ALREADY_OPEN:
        return {"finish": ALREADY_OPEN, "pr_url": newest_pull_url(pulls),
                "card": card}
    if decision != OPENED_NEEDED:
        return {"finish": decision, "pr_url": "", "card": card}

    try:
        pr_url = ops.create_pr(
            repo, base=default_branch, head=branch, title=pr_title(compare),
            body=pr_body(compare=compare, failed_run_url=failed_run_url,
                         card_url=card_url, card=card),
        )
    except Exception as exc:  # noqa: BLE001 — the caller falls through to its error
        print(f"repair finish: the pull request was not opened ({exc})",
              file=sys.stderr)
        # A pull request somebody else opened in the meantime is still the
        # answer; anything else is the caller's red.
        again = _read(f"the pull requests on {branch}",
                      lambda: ops.pulls_for_head(repo, branch))
        if isinstance(again, list) and again:
            return {"finish": ALREADY_OPEN, "pr_url": newest_pull_url(again),
                    "card": card}
        return {"finish": UNREADABLE, "pr_url": "", "card": card}

    if card:
        try:
            ops.cmd_comment(card, finish_note(pr_url, branch, workflow_name))
        except Exception as exc:  # noqa: BLE001 — Linear never undoes the open
            print(f"repair finish: {pr_url} is open but {card} was not told "
                  f"({exc})", file=sys.stderr)
    return {"finish": OPENED, "pr_url": pr_url, "card": card}


def outputs(result: dict) -> str:
    """`$GITHUB_OUTPUT` lines, through the writer that survives a newline."""
    return github_output.render([
        ("finish", result["finish"]),
        ("pr_url", result["pr_url"]),
        ("card", result["card"]),
    ])


def main(argv: list[str], ops=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("finish")
    f.add_argument("--repo", required=True, help="owner/name")
    f.add_argument("--branch", required=True)
    f.add_argument("--default-branch", required=True)
    f.add_argument("--failed-run-url", required=True)
    f.add_argument("--workflow-name", required=True)
    f.add_argument("--card-url", default="")
    args = parser.parse_args(argv)

    # Stdout is the output block; `linear_ops.cmd_comment` talks back on it.
    with github_output.only_outputs():
        result = finish(
            repo=args.repo, branch=args.branch,
            default_branch=args.default_branch,
            failed_run_url=args.failed_run_url,
            workflow_name=args.workflow_name, card_url=args.card_url or "",
            ops=ops,
        )
    sys.stdout.write(outputs(result))
    print(f"repair finish: {args.branch} → {result['finish']}"
          f"{' ' + result['pr_url'] if result['pr_url'] else ''}",
          file=sys.stderr)
    return 0 if result["finish"] in FINISHED else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
