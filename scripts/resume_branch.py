#!/usr/bin/env python3
"""A requeued run resumes the dead run's branch (DRE-4368, stdlib only).

When a run died at the turn ceiling its branch — often carrying finished work
— was ignored, and the requeued run started from a clean checkout of the
default branch and rebuilt the card from nothing. Two subcommands close that.

## `decide` — resume, or start clean and say why

Run in the product checkout after both checkouts and before `Select model`.
It lists this card's `origin/agent/<CARD>-*` branches, picks the one with the
newest commit (naming the others), and answers `resume=true` only when BOTH
hold:

  * the branch carries at least one commit that is not on `origin/<default>`;
  * it merges cleanly onto the default — it already contains the default, or
    `git merge-tree --write-tree` reports no conflict.

Every other case is `resume=false` with a one-line `reason`. The outputs are
exactly `resume`, `branch`, `sha` (the branch tip BEFORE this run) and
`reason`; on a resume the note at `--note` lists what is already committed.
`--snapshot` records every card branch's tip, for `proof` below.

## `proof` — a pre-existing branch proves nothing until it moves

Four steps read "this card has a branch" as "this run's agent ran": both
rate-limit retry decisions, the result gate and the Report step. With the dead
run's branch already on GitHub, a run that died before its agent would look
alive. `proof` prints the card branch that counts as this run's work — one
this run created, or a pre-existing one whose tip moved past the tip recorded
before the run — and prints nothing when there is none.

FAIL-SOFT, BOTH OF THEM. A step that decides whether to resume can never fail
a build: any git error answers `resume=false`. And `proof` on an unreadable
git answers `--fallback`, the step's own legacy `git branch -r` read, so this
filter can only ever narrow what those steps saw before — never invent a
branch, never exit non-zero.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import github_output  # noqa: E402

UNREADABLE = "git unreadable — starting clean"
NO_BRANCH = "no card branch"


class GitError(Exception):
    """Any git call that did not answer."""


def _git(*args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], capture_output=True, text=True, check=False,
        )
    except OSError as e:
        raise GitError(str(e)) from e
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {args[0]} exited {proc.returncode}")
    return proc.stdout


def _card_refs(namespace: str, card: str) -> list[tuple[str, str, int]]:
    """(name, sha, committer epoch) for every `agent/<card>-*` ref under
    `namespace`, newest first. Anchored on the `-` after the card number, so
    DRE-7 never picks up DRE-77's branch (DRE-1343/DRE-2025)."""
    out = _git(
        "for-each-ref",
        "--format=%(refname)%09%(objectname)%09%(committerdate:unix)",
        f"{namespace}/agent/",
    )
    prefix = f"{namespace}/agent/{card}-"
    refs = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or not parts[0].startswith(prefix):
            continue
        name = parts[0][len(namespace) + 1:]
        refs.append((name, parts[1], int(parts[2] or 0)))
    refs.sort(key=lambda r: (-r[2], r[0]))
    return refs


def _conflicts(default_ref: str, branch_ref: str) -> list[str] | None:
    """The files a merge of `branch_ref` onto `default_ref` conflicts on;
    [] when it merges cleanly."""
    try:
        _git("merge-base", "--is-ancestor", default_ref, branch_ref)
        return []  # the branch already contains the default
    except GitError:
        pass
    proc = subprocess.run(
        ["git", "merge-tree", "--write-tree", "--name-only", "--no-messages",
         default_ref, branch_ref],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode == 0:
        return []
    if proc.returncode != 1:
        raise GitError(proc.stderr.strip() or "merge-tree failed")
    # First line is the tree; the conflicted names follow, one per line.
    return [line for line in proc.stdout.splitlines()[1:] if line.strip()]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _note(branch: str, default: str) -> str:
    default_ref, branch_ref = f"origin/{default}", f"origin/{branch}"
    log = _git("log", "--oneline", f"{default_ref}..{branch_ref}").rstrip()
    stat = _git("diff", "--stat", f"{default_ref}...{branch_ref}").rstrip()
    behind = int(_git("rev-list", "--count", f"{branch_ref}..{default_ref}").strip())
    standing = (f"It is behind {default} by {_plural(behind, 'commit')}: merge "
                f"`{default_ref}` into it before you continue."
                if behind else f"It is not behind {default}.")
    return (
        f"# Resuming `{branch}`\n\n"
        f"A previous run of this card ran out of turns and left this branch. "
        f"{standing}\n\n"
        f"## Already committed (`git log --oneline {default_ref}..{branch_ref}`)\n\n"
        f"```\n{log}\n```\n\n"
        f"## Files changed (`git diff --stat {default_ref}...{branch_ref}`)\n\n"
        f"```\n{stat}\n```\n"
    )


def decide(card: str, default: str) -> tuple[dict, str, dict | None]:
    """(outputs, note, snapshot). The note is "" unless the answer is resume;
    the snapshot is None when git could not be read."""
    outputs = {"resume": "false", "branch": "", "sha": "", "reason": UNREADABLE}
    try:
        default_ref = f"origin/{default}"
        _git("rev-parse", "--verify", "--quiet", f"{default_ref}^{{commit}}")
        refs = _card_refs("refs/remotes/origin", card)
    except (GitError, ValueError):
        return outputs, "", None
    snapshot = {name: sha for name, sha, _ in refs}
    if not refs:
        outputs["reason"] = NO_BRANCH
        return outputs, "", snapshot

    name, sha, _ = refs[0]
    outputs.update(branch=name, sha=sha)
    others = [r[0] for r in refs[1:]]
    picked = (f"newest of {len(refs)}, also {', '.join(others)}"
              if others else "")
    try:
        ahead = int(_git("rev-list", "--count",
                         f"{default_ref}..origin/{name}").strip())
        if ahead < 1:
            reason = f"branch has no commits beyond {default}"
            outputs["reason"] = f"{reason} ({picked})" if picked else reason
            return outputs, "", snapshot
        conflicts = _conflicts(default_ref, f"origin/{name}")
        if conflicts:
            reason = f"branch conflicts with {default} on {', '.join(conflicts)}"
            outputs["reason"] = f"{reason} ({picked})" if picked else reason
            return outputs, "", snapshot
        note = _note(name, default)
    except (GitError, ValueError):
        outputs.update(branch="", sha="", reason=UNREADABLE)
        return outputs, "", snapshot
    reason = f"{_plural(ahead, 'commit')} beyond {default}, merges cleanly"
    outputs.update(resume="true",
                   reason=f"{reason} ({picked})" if picked else reason)
    return outputs, note, snapshot


def proof(card: str, branch: str, sha: str, snapshot_path: str,
          fallback: str) -> str:
    """The card branch that proves THIS run worked, or ""."""
    before: dict[str, str] = {}
    if snapshot_path:
        try:
            with open(snapshot_path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                before.update({str(k): str(v) for k, v in loaded.items()})
        except (OSError, ValueError):
            pass  # no snapshot: fall back to the one branch the step named
    if branch and sha:
        before[branch] = sha
    try:
        remote = _card_refs("refs/remotes/origin", card)
        local = {name: tip for name, tip, _ in _card_refs("refs/heads", card)}
    except (GitError, ValueError):
        return fallback
    for name, tip, _ in remote:
        if name not in before:
            return name  # this run created it
        if tip != before[name] or local.get(name, tip) != before[name]:
            return name  # it moved past its pre-run tip, here or on GitHub
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decide")
    d.add_argument("--card", required=True)
    d.add_argument("--default", required=True)
    d.add_argument("--github-output", default="")
    d.add_argument("--note", default="")
    d.add_argument("--snapshot", default="")
    p = sub.add_parser("proof")
    p.add_argument("--card", required=True)
    p.add_argument("--branch", default="")
    p.add_argument("--sha", default="")
    p.add_argument("--snapshot", default="")
    p.add_argument("--fallback", default="")
    args = parser.parse_args(argv)

    if args.cmd == "proof":
        try:
            found = proof(args.card, args.branch, args.sha, args.snapshot,
                          args.fallback)
        except Exception:  # noqa: BLE001 — never fail the step it filters
            found = args.fallback
        if found:
            print(found)
        return 0

    try:
        outputs, note, snapshot = decide(args.card, args.default)
    except Exception:  # noqa: BLE001 — this step can never fail a build
        outputs = {"resume": "false", "branch": "", "sha": "", "reason": UNREADABLE}
        note, snapshot = "", None
    try:
        if args.github_output:
            with open(args.github_output, "a", encoding="utf-8") as fh:
                fh.write(github_output.render(
                    (k, outputs[k]) for k in ("resume", "branch", "sha", "reason")
                ))
        if args.note and note:
            with open(args.note, "w", encoding="utf-8") as fh:
                fh.write(note)
        if args.snapshot and snapshot is not None:
            with open(args.snapshot, "w", encoding="utf-8") as fh:
                json.dump(snapshot, fh, indent=2, sort_keys=True)
    except OSError as e:
        print(f"resume_branch: could not write an output: {e}", file=sys.stderr)
    print(f"resume={outputs['resume']} branch={outputs['branch'] or '-'} "
          f"reason={outputs['reason']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
