#!/usr/bin/env python3
"""Publish a scheduled job's generated files through ONE pull request (stdlib).

DRE-3879. Two scheduled jobs in this repo derive a file and then have to save
it: `split-ledger.yml` regenerates the split ledger daily, `model-drift.yml`
refreshes the Anthropic catalog snapshot weekly. Both ended in

    git push origin HEAD:main

and both had been failing on exactly that line. Branch protection on `main`
does not admit the bureau App for a direct push, so GitHub answered

    GH006: Protected branch update failed
    Changes must be made through a pull request
    2 of 2 required status checks are expected

every single morning for the ledger, and every Monday for the drift watch
since at least 2026-08-10. Neither job was wrong about anything else: they
derived the right file and then could not put it anywhere.

The CEO's signed answer (2026-09-14 08:14 PT) picked the remedy and ruled out
the alternative in the same sentence: **open a pull request; change no branch
protection.** `main` keeps requiring a PR and both required checks.

So this module is the other end of that line. Each job commits to ONE fixed
branch of its own and opens — or updates — ONE pull request:

  1. STAGE the generated paths BY NAME, then read the index back and refuse
     anything else. This is the guarantee that made these jobs safe to hold
     `contents: write` in the first place, and it moved here rather than being
     written out twice in shell. `model-drift.yml`'s comment says what it buys:
     a scheduled job that is *structurally* incapable of moving a model pin.
  2. NOTHING STAGED ⇒ nothing happens, and the run is GREEN. A week with no
     new models opens no pull request and files no noise.
  3. BUILD ONE COMMIT from the index, parented on the checkout's own HEAD —
     `write-tree` + `commit-tree`, so neither the working tree nor HEAD is
     touched. The branch therefore holds exactly one commit, this derivation
     against the base the run checked out, and the pull request's diff is the
     generated file and nothing else. It never drifts behind `main` into a
     conflict nobody is watching, and a re-run inside one checkout builds the
     same shape rather than stacking a second commit on the first.
  4. FORCE-PUSH that commit to the fixed branch. The content is regenerated
     wholesale every run, so yesterday's derivation is not history worth
     keeping; and because (1) proves the staged set, whatever lands there is
     the named paths or the run failed. This is the one push, and its
     destination is never the base.
  5. OPEN a pull request only if the branch has no OPEN one. Otherwise the
     push above has already updated the one that exists. A daily job that
     opened a PR per run would file 365 a year and pay for a critic review on
     each.

WHY ONE MODULE AND NOT TWO COPIES. "Does automation own this branch" was
hand-written eight times across four files and gave four different answers;
four pull requests sat ten hours as a result (DRE-2426, `reconcile.py`). Two
hand-written copies of "commit, push, open a PR if there isn't one" is the
same shape waiting to happen, and the second copy is always the one that
forgets the staged-set proof.

"COULD NOT TELL" IS NEVER "NO PULL REQUEST". An unreadable PR listing exits
non-zero rather than opening one: the work is already on the branch either
way, and a duplicate PR is the failure DRE-2034 describes. The next scheduled
run — or a `workflow_dispatch` — repeats the whole thing idempotently.

Vendor boundary (standards/vendor-boundaries.md), for the callers:
  Q1 actor — the push and the `gh pr create` are made with the bureau App
     token the job mints, so the pull request is authored by
     `agent-bureau-bot` and merged (author != merger) by the qa-bot App. Both
     branches are on the merge gate's trusted list as literal names.
  Q2 secrets — a schedule-triggered run reads the repo's normal secrets store;
     no dependabot-triggered event is anywhere in these workflows' paths.
  Q3 re-run — idempotent by construction: the same derivation re-stages the
     same paths, force-pushes the same branch, and finds its own open pull
     request rather than opening a second.
  Q4 command limits — `gh pr list --state open` is deliberately not
     `--state all`: a merged pull request is finished, and the next change
     needs a new one. Its failure is handled above rather than read as "none".
  Q5 our crash — nothing is written before the staged-set proof, and no
     receipt or marker is left behind, so a crash anywhere leaves the previous
     state in place and the next run simply repeats.

CLI (the form both workflows call):

    python3 bot_branch_pr.py publish \\
        --repo owner/name --branch bot/model-drift --base main \\
        --path models.json \\
        --title "chore(models): refresh the catalog snapshot" \\
        --body-file "$RUNNER_TEMP/pr-body.md"

Exit 0 → published, updated, or nothing to do. Exit 1 → refused (an
unexpected staged path, an unreadable pull-request listing, a failed push).
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 — git and gh; argv lists, never a shell
import sys

#: The identity every commit these jobs make is authored by — the same one
#: both workflows used to spell inline. It is the App the run mints a token
#: for, and an identity every gate in this repo already admits.
BOT_NAME = "agent-bureau-bot[bot]"
BOT_EMAIL = "agent-bureau-bot[bot]@users.noreply.github.com"


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _subprocess_run(argv, *, cwd=None, env=None):
    """(returncode, stdout, stderr). The one seam tests inject — no shell."""
    done = subprocess.run(  # nosec B603 — argv list, no shell
        argv, cwd=cwd, env=env, capture_output=True, text=True
    )
    return done.returncode, (done.stdout or ""), (done.stderr or "")


class Outcome:
    """What the publisher found and what it did about it."""

    def __init__(self):
        self.changed = False
        self.pushed = False
        self.pr_url = ""
        self.pr_opened = False
        self.error = ""

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return (f"<Outcome changed={self.changed} pushed={self.pushed} "
                f"pr_opened={self.pr_opened} pr_url={self.pr_url!r}>")


def pr_body(text: str, branch: str, base: str) -> str:
    """The pull request's body: the caller's explanation, then the sentence
    that says which job's branch this is.

    Naming the branch is an acceptance criterion of DRE-3879 and not
    decoration. These pull requests carry no card — like `dependabot/*` and
    `bot/standards-sync`, a scheduled job has no per-run card to name — so the
    branch is the only thing on the page that says which job produced it, and
    a reader who has to guess between the daily ledger and the weekly catalog
    is a reader who approves the wrong one.
    """
    return (
        f"{text.strip()}\n\n"
        f"**This pull request is on branch `{branch}`, targeting `{base}`.** "
        f"It is opened and updated by a scheduled job: one branch, one pull "
        f"request, force-pushed with each regeneration rather than reopened. "
        f"Every check and the adversarial critic run on it exactly as they do "
        f"on any other pull request, and a red check or a REQUEST_CHANGES "
        f"verdict blocks the merge (DRE-3879).\n"
    )


def unexpected_staged(staged, allowed) -> list[str]:
    """Staged paths that are not in `allowed`, in the order they were staged.

    Whole-path equality, never a prefix: `models.json.bak` is not
    `models.json`, and a prefix test would wave it through. This is the check
    that makes these jobs structurally incapable of committing anything but
    what they generate.
    """
    permitted = set(allowed)
    return [p for p in staged if p not in permitted]


def _staged_paths(run, workdir: str) -> list[str]:
    code, out, err = run(["git", "-C", workdir, "diff", "--cached",
                          "--name-only"])
    if code != 0:
        raise RuntimeError(f"could not read the index: {err.strip()}")
    return [line.strip() for line in out.splitlines() if line.strip()]


def _open_pr(repo: str, branch: str, base: str, *, run, workdir: str):
    """The branch's OPEN pull request url, "" for none, or None for unknown.

    `--state open` on purpose: a pull request that has merged is finished, and
    the next regeneration needs a new one. None (unreadable) is not "none" —
    the caller refuses rather than opening a second (DRE-2034).
    """
    code, out, err = run(
        ["gh", "pr", "list", "--repo", repo, "--head", branch,
         "--base", base, "--state", "open", "--json", "url", "--limit", "5"],
        cwd=workdir,
    )
    if code != 0:
        _log(f"bot-branch: GitHub would not list {branch}'s pull requests: "
             f"{err.strip()}")
        return None
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        _log(f"bot-branch: unreadable pull request listing for {branch}")
        return None
    if not isinstance(rows, list):
        return None
    return (rows[0].get("url") or "") if rows else ""


def publish(
    *,
    repo: str,
    branch: str,
    base: str,
    paths,
    title: str,
    body: str,
    workdir: str = ".",
    run=None,
    log=_log,
) -> Outcome:
    """Commit `paths` to `branch` and make sure it has exactly one pull request.

    Raises ValueError when asked to do the thing this module exists to stop:
    publish onto the base itself. That is the `HEAD:main` push branch
    protection refuses, and no workflow edit may reintroduce it by pointing
    `--branch` at `main`.
    """
    run = run or _subprocess_run
    paths = list(paths)
    branch = (branch or "").strip()
    base = (base or "main").strip()
    if not branch:
        raise ValueError("refusing to publish with no branch named")
    if branch == base:
        raise ValueError(
            f"refusing to publish onto {base!r} itself — these jobs reach the "
            f"default branch through a pull request, never a direct push "
            f"(DRE-3879)"
        )
    if not paths:
        raise ValueError("refusing to publish with no generated paths named")

    out = Outcome()

    code, _, err = run(["git", "-C", workdir, "add", "--", *paths])
    if code != 0:
        raise RuntimeError(f"could not stage the generated paths: {err.strip()}")

    staged = _staged_paths(run, workdir)
    if not staged:
        log(f"bot-branch: nothing changed — no commit, no pull request "
            f"({', '.join(paths)} are byte-identical)")
        return out

    unexpected = unexpected_staged(staged, paths)
    if unexpected:
        out.error = (f"refusing to commit — staged more than the generated "
                     f"paths: {' '.join(unexpected)}")
        log(f"::error::{out.error}")
        return out
    out.changed = True

    # The commit is BUILT, not made: `write-tree` turns the proved index into
    # a tree and `commit-tree` parents it on the checkout's own HEAD. Neither
    # the working tree nor HEAD moves, so the run's own checkout is left
    # exactly as the derivation left it, and a second publish in one checkout
    # produces the same single commit off the base rather than stacking one on
    # yesterday's.
    code, tree, err = run(["git", "-C", workdir, "write-tree"])
    if code != 0 or not tree.strip():
        raise RuntimeError(f"could not write the index out: {err.strip()}")
    code, parent, err = run(["git", "-C", workdir, "rev-parse", "HEAD"])
    if code != 0 or not parent.strip():
        raise RuntimeError(f"could not read HEAD: {err.strip()}")
    code, commit, err = run([
        "git", "-C", workdir,
        "-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}",
        "commit-tree", tree.strip(), "-p", parent.strip(),
        "-m", title, "-m", body,
    ])
    if code != 0 or not commit.strip():
        raise RuntimeError(f"could not build the commit: {err.strip()}")

    # The one push, and its destination is never the base. `--force`: the
    # commit is built fresh off the base every run, so this replaces the
    # previous derivation rather than building a history of them — and what it
    # replaces it with is proved by the staged-set check above.
    code, _, err = run([
        "git", "-C", workdir, "push", "--force", "origin",
        f"{commit.strip()}:refs/heads/{branch}",
    ])
    if code != 0:
        out.error = f"could not push {branch}: {err.strip()}"
        log(f"::error::{out.error}")
        return out
    out.pushed = True
    log(f"bot-branch: pushed {branch}")

    existing = _open_pr(repo, branch, base, run=run, workdir=workdir)
    if existing is None:
        out.error = (f"could not read whether {branch} has an open pull "
                     f"request — not opening one, because 'could not tell' is "
                     f"not 'there is none'")
        log(f"::error::{out.error}")
        return out
    if existing:
        out.pr_url = existing
        log(f"bot-branch: {branch} already has an open pull request "
            f"({existing}) — the push above updated it")
        return out

    code, created, err = run(
        ["gh", "pr", "create", "--repo", repo, "--base", base,
         "--head", branch, "--title", title,
         "--body", pr_body(body, branch, base)],
        cwd=workdir,
    )
    if code != 0:
        out.error = f"could not open the pull request: {err.strip()}"
        log(f"::error::{out.error}")
        return out
    out.pr_opened = True
    out.pr_url = (created or "").strip().splitlines()[-1] if created.strip() else ""
    log(f"bot-branch: opened {out.pr_url or 'the pull request'}")
    return out


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser(
        "publish",
        help="commit the generated paths to a fixed branch and open or "
             "update its one pull request",
    )
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--branch", required=True,
                   help="the job's own fixed branch, e.g. bot/model-drift")
    p.add_argument("--base", default="main",
                   help="the branch the pull request targets (default: main)")
    p.add_argument("--path", dest="paths", action="append", default=[],
                   required=True,
                   help="a generated path to commit; repeat for each one. "
                        "Anything else in the index is refused.")
    p.add_argument("--title", required=True,
                   help="the commit subject and the pull request title")
    p.add_argument("--body", default="",
                   help="the explanation, used for the commit body and the "
                        "pull request (which also names the branch)")
    p.add_argument("--body-file", default="",
                   help="read --body from this file instead")
    p.add_argument("--workdir", default=".")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    body = _read(args.body_file) if args.body_file else args.body
    try:
        outcome = publish(
            repo=args.repo, branch=args.branch, base=args.base,
            paths=args.paths, title=args.title, body=body,
            workdir=args.workdir,
        )
    except (ValueError, RuntimeError) as exc:
        _log(f"::error::bot-branch: {exc}")
        return 1
    return 1 if outcome.error else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
