#!/usr/bin/env python3
"""Deliver the patch a refused rescue left behind (DRE-3262, stdlib only).

WHERE THIS STARTS. `push_rescue.py` closed the DRE-3043 ceiling — a run longer
than an hour cannot push with the credential it started with — and DRE-3098
gave that rescue two fresh mints of its own plus a patch artifact for the case
where GitHub refuses both. On 2026-09-06 (agent-bureau run 34045232203, card
DRE-3165) every one of those parts did its job and the work still did not ship:

  * the build ran 93 minutes and finished 5/5 green, four commits on the runner;
  * the job's start token had died at the hour, as designed;
  * the rescue pushed with its OWN fresh mint and GitHub answered
    `RESCUE_PUSH_STATUS: 400`, then again on the second mint;
  * the patch was written and uploaded — `rescue-DRE-3165.patch`, 235 KB, 4
    commits, 35 files — and NOBODY WAS TOLD. The card's last comments read "the
    push-rescue step delivers the branch and opens the PR" and then "Could not
    read this card's PR state from GitHub (API error), so this run is NOT being
    recorded as a dead agent". The same dead token had made the PR read a 401.

A person found the artifact by hand at 11:05 PT and delivered it
(`gh run download` → `git am -3` → push → PR). Without that the card would have
been rebuilt from nothing: ~90 minutes and the run's cost again. **A rescued
patch only a human knows about is the DRE-3043 loss with an extra step.**

So this module is the two halves of "the run still delivers":

  1. `handoff()` — what the run's LAST step calls. It says on the card, in one
     grammar a reader and a machine can both use, that the push failed and
     WHERE the work is, and it hands the delivery to a `deliver-rescue`
     workflow_dispatch carrying the run id. The comment is posted whatever the
     dispatch answers: the dispatch can 404 in a repo with no stub, and the card
     is the channel that always survives.
  2. `deliver()` — what that follow-up run executes. A NEW job, a NEW token:
     download the artifact, apply it on a fresh branch off the base, push, open
     the pull request, say so on the card. Nothing it spends was minted by the
     run that failed, which is the whole reason it is a separate job.

The grammar is here and nowhere else. `parse_marker()` reads exactly what
`announcement()` writes, so the reconcile sweep's re-check (reconcile.py's
In-Progress-no-PR branch) reads the ARTIFACT FACT off the card rather than
concluding "dead agent, rebuild" from a pull request that was never opened.

CLI (the two forms the workflows call):

    python3 deliver_rescue.py handoff --card DRE-3165 --repo owner/name \\
        --run-id 34045232203 --artifact rescue-DRE-3165.patch --status 400

    PUSH_TOKEN=<fresh> python3 deliver_rescue.py apply --card DRE-3165 \\
        --repo owner/name --run-id 34045232203 --patch-dir /tmp/rescue \\
        --base main --card-url <url>
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess  # nosec B404 — git and gh; argv lists, never a shell
import sys
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import push_rescue  # noqa: E402  — one definition of the git credential re-point

# The three tags this module writes, and the two the sweep reads back. Strings,
# not an enum: they cross into Linear comment bodies and into a shell.
#: The run said its push failed and named where the work is.
FAILED_TAG = "rescue-push-failed"
#: The delivery follow-up was dispatched (by the run, or by a later sweep).
DISPATCH_TAG = "rescue-delivery-dispatched"
#: The follow-up landed the work — this card needs nothing further.
DELIVERED_TAG = "rescue-delivered"

#: The reusable delivery workflow, and the stub bureau-pipeline dispatches
#: instead. Same resolution as reconcile's review_workflow()/fix_workflow()
#: family (DRE-2056): in the self-host repo the reusable filename is
#: workflow_call-only and `gh workflow run` on it answers 422.
DELIVERY_WORKFLOW = "deliver-rescue.yml"
SELF_DELIVERY_WORKFLOW = "self-deliver-rescue.yml"

#: What `announcement()` writes, read back. Anchored on the tag and on the two
#: facts the follow-up needs — the artifact and the run that holds it — so
#: ordinary prose mentioning a patch file is not mistaken for a marker.
_MARKER_RE = re.compile(
    rf"{FAILED_TAG}:.*?artifact (?P<artifact>[A-Za-z0-9][A-Za-z0-9._-]*) "
    rf"on run (?P<run_id>\d+)"
)

#: Anything shaped like a GitHub credential, removed before git's own words are
#: quoted onto a card. git does not echo the `http.extraheader` value, so this
#: has nothing to catch today — which is exactly when to put it in, because the
#: comment is public and the message it quotes is not ours to predict.
_TOKEN_RE = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})\b")

#: How much of git's refusal reaches the card. One line, and short enough that
#: the receipt stays readable — the log has the rest.
_STDERR_LIMIT = 300


class Marker(NamedTuple):
    """The artifact fact, as the card carries it."""

    run_id: str
    artifact: str


class Handoff(NamedTuple):
    """What the run's last step managed. `dispatched` gates every claim."""

    dispatched: bool
    workflow: str
    detail: str


class Delivery:
    """What the follow-up run managed."""

    def __init__(self):
        self.branch = ""
        self.pushed = False
        self.pr_opened = False
        self.pr_url = ""
        self.error = ""


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _run(argv, *, cwd=None, env=None):
    """(returncode, stdout, stderr) — push_rescue's seam, same discipline."""
    return push_rescue._subprocess_run(argv, cwd=cwd, env=env)


def redact(text: str) -> str:
    """git's own words, with anything credential-shaped removed."""
    return _TOKEN_RE.sub("<redacted>", text or "")


def delivery_workflow(repo: str) -> str:
    """The DISPATCHABLE delivery workflow's filename for `repo`."""
    slug = (repo or "").rsplit("/", 1)[-1]
    return SELF_DELIVERY_WORKFLOW if slug == "bureau-pipeline" else DELIVERY_WORKFLOW


def announcement(
    card: str,
    *,
    artifact: str,
    run_id: str,
    status: str = "",
    mints: int = 2,
) -> str:
    """The line the card gets when the rescue push failed.

    Verbatim what DRE-3262 asked for, and load-bearing in three directions: a
    human reads it, `parse_marker()` reads it, and the sweep's re-check acts on
    it. An unnamed refusal says so rather than printing a number nobody saw —
    `push_rescue.http_status` answers "" for a message it cannot name.
    """
    named = f"status {status}" if str(status).strip() else "no HTTP status"
    where = "both mints" if mints >= 2 else "its only mint"
    return (
        f"🚨 {FAILED_TAG}: {named} on {where} — the work is in artifact "
        f"{artifact} on run {run_id}; nothing will open a PR until it is "
        f"delivered"
    )


def parse_marker(body: str) -> Marker | None:
    """The artifact fact carried by one comment, or None."""
    match = _MARKER_RE.search(body or "")
    if not match:
        return None
    return Marker(run_id=match.group("run_id"), artifact=match.group("artifact"))


def pending_delivery(bodies) -> Marker | None:
    """This card's undelivered rescue, newest first, or None.

    A `rescue-delivered` receipt anywhere in the window ends it: the follow-up
    landed the work, and nothing else is owed. Read off the card's own comments
    — never off a pull-request listing, which is the read that failed in the
    incident and told the pipeline the opposite of the truth.
    """
    bodies = list(bodies or [])
    if any(DELIVERED_TAG in (b or "") for b in bodies):
        return None
    for body in reversed(bodies):
        marker = parse_marker(body)
        if marker is not None:
            return marker
    return None


def dispatch_argv(repo: str, *, run_id: str, card: str, artifact: str) -> list[str]:
    """The `gh workflow run` call that hands the delivery to a new job."""
    return [
        "gh", "workflow", "run", delivery_workflow(repo), "--repo", repo,
        "-f", f"run_id={run_id}", "-f", f"card={card}",
        "-f", f"artifact={artifact}",
    ]


def dispatch_env() -> dict | None:
    """The environment the dispatch runs in, or None to inherit.

    WHICH CREDENTIAL MAY REACH THIS ENDPOINT (vendor-boundaries Q2). A
    `workflow_dispatch` needs `actions: write`, and the bureau App token — the
    `GH_TOKEN` every other call in the Report step spends — carries no Actions
    permission at all: *"HTTP 403: Resource not accessible by integration"*
    (DRE-1254). Only the workflow's own `github.token` can hold it, and only the
    calling stub can grant it, so the stub passes it in as `GH_DISPATCH_TOKEN`
    and this swaps it in — the same mechanism, spelled the same way, as
    `reconcile.gh_dispatch`.

    A stub that has not been updated simply has no such variable: the dispatch
    then runs under the App token, 403s, and the card says so beside the
    artifact — and the reconcile sweep, whose own stub DOES grant it, dispatches
    on its next pass. Degrades, never breaks.
    """
    token = os.environ.get("GH_DISPATCH_TOKEN", "").strip()
    if not token:
        return None
    return {**os.environ, "GH_TOKEN": token, "GITHUB_TOKEN": token}


def _post_comment(card: str, body: str) -> None:
    """The card write, through the pipeline's one Linear client."""
    import linear_ops

    linear_ops.cmd_comment(card, body)


def handoff(
    card: str,
    *,
    repo: str,
    run_id: str,
    artifact: str,
    status: str = "",
    mints: int = 2,
    stderr: str = "",
    run=None,
    post=None,
) -> Handoff:
    """Say the push failed, and hand the delivery to something that can do it.

    The comment goes out WHATEVER the dispatch answers. A product repo with no
    `deliver-rescue` stub answers 404 to the dispatch, and in that repo the
    card is the only channel left — reporting the artifact there is the
    difference between a human who can recover the work in two minutes and a
    card that gets rebuilt from nothing.

    Never raises: this runs in the run's last step, after something has already
    gone wrong, and a crash here would replace a recoverable loss with a red
    step that summons the medic to re-run work that is already done.
    """
    run = run or _run
    post = post or (lambda body: _post_comment(card, body))
    line = announcement(card, artifact=artifact, run_id=run_id, status=status,
                        mints=mints)
    workflow = delivery_workflow(repo)
    argv = dispatch_argv(repo, run_id=run_id, card=card, artifact=artifact)
    code, _, err = run(argv, env=dispatch_env())
    detail = (redact(err).strip().splitlines() or [""])[-1][:_STDERR_LIMIT]
    dispatched = code == 0

    parts = [line, ""]
    if dispatched:
        parts.append(
            f"🚚 {DISPATCH_TAG}: dispatched `{workflow}` on `{repo}` with "
            f"`run_id={run_id}` — that run downloads {artifact} with its own "
            f"freshly minted token, applies it on a fresh branch and opens the "
            f"pull request. Nobody needs to do this by hand."
        )
    else:
        parts.append(
            f"⚠️ the delivery follow-up could NOT be dispatched "
            f"(`{workflow}` on `{repo}`: {detail or 'no reason given'}), so this "
            f"card needs a human: `gh run download {run_id} --name {artifact}`, "
            f"then `git am -3` it on a branch off the default branch. The "
            f"reconcile sweep re-checks this card off the artifact fact above."
        )
    if stderr.strip():
        parts += ["", f"GitHub refused the push with: `{redact(stderr).strip()[:_STDERR_LIMIT]}`"]
    body = "\n\n".join(parts)
    try:
        post(body)
    except Exception as exc:  # a reporting failure must not fail the run
        _log(f"rescue delivery: could not comment on {card} — {exc}")
    _log(
        f"rescue delivery: {'dispatched' if dispatched else 'COULD NOT dispatch'} "
        f"{workflow} for {card} (run {run_id}, artifact {artifact})"
    )
    return Handoff(dispatched=dispatched, workflow=workflow, detail=detail)


# --------------------------------------------------------------------------
# the follow-up run: the artifact becomes a branch and a pull request
# --------------------------------------------------------------------------
def patch_file(directory: str) -> str:
    """The downloaded patch, or "".

    `gh run download` unpacks the artifact into a directory, and what it holds
    is whatever `push_rescue.write_patch` wrote — a `format-patch` series or,
    on a shallow clone, a plain diff. Either way it is the one `.patch` file
    there; anything else means the download did not produce what we asked for
    and the caller must say so rather than guess.
    """
    matches = sorted(glob.glob(os.path.join(directory or ".", "**", "*.patch"),
                               recursive=True))
    return matches[0] if matches else ""


def delivery_branch(card: str) -> str:
    """The branch the delivery pushes.

    `agent/<CARD>-…`, because every gate in the pipeline reads that shape,
    anchored to the card (DRE-1343) — and named `rescued` so a reader of the
    branch list knows the commits arrived by artifact rather than by push.
    """
    return f"agent/{card}-rescued-delivery"


def _pr_body(card: str, card_url: str, *, run_id: str, artifact: str) -> str:
    """The body for a pull request neither the agent nor the run opened.

    It says exactly where the commits came from and claims nothing about
    whether the card is finished — this job knows only that a patch existed and
    applied cleanly. The critic reads this.
    """
    head = f"{card_url}\n\n" if card_url.strip() else ""
    return (
        f"{head}**Opened by the rescue-delivery follow-up, not by the agent** "
        f"(DRE-3262). The build run for {card} finished with its work committed "
        f"on the runner, and GitHub refused the push on both of the rescue "
        f"step's own freshly minted credentials — so the commits were uploaded "
        f"as `{artifact}` on run {run_id} and this job applied them with a "
        f"credential of its own.\n\n"
        f"The commits are the agent's; the branch, the patch replay and this "
        f"pull request are not. Nothing here asserts the card is complete: read "
        f"the diff and the checks, and expect no author-written summary.\n"
    )


def _gh_env(token: str) -> dict:
    env = dict(os.environ)
    if token:
        env["GH_TOKEN"] = token
        env["GITHUB_TOKEN"] = token
    return env


def deliver(
    card: str,
    *,
    repo: str,
    run_id: str,
    patch_dir: str,
    base: str = "main",
    token: str = "",
    card_url: str = "",
    artifact: str = "",
    workdir: str = ".",
    run=None,
    post=None,
) -> Delivery:
    """Turn the downloaded patch into a branch and a pull request.

    Runs in the follow-up job, with that job's OWN token: the credential the
    failed run held is not read here, and cannot be — it belongs to a run that
    has finished. Idempotent by construction, because the sweep may dispatch
    this twice: a card that already has a pull request is left completely alone
    (a second PR for one card is worse than the bug it fixes).
    """
    run = run or _run
    post = post or (lambda body: _post_comment(card, body))
    out = Delivery()
    out.branch = delivery_branch(card)
    artifact = artifact or f"rescue-{card}.patch"

    existing = push_rescue._existing_pr(out.branch, repo, token, run=run,
                                        workdir=workdir)
    if existing:
        out.pr_url = "" if existing == "unreadable" else existing
        out.error = ("could not read this branch's PR state"
                     if existing == "unreadable" else "")
        _log(f"rescue delivery: {out.branch} already has a pull request "
             f"({existing}) — nothing to deliver")
        return out

    patch = patch_file(patch_dir)
    if not patch:
        out.error = f"no .patch file in {patch_dir} — the artifact did not arrive"
        _log(f"rescue delivery: {out.error}")
        return out

    # The fresh credential must reach GIT, not only `gh` — the same
    # multi-valued `http.extraheader` trap push_rescue documents, and the same
    # one definition of the fix.
    if token:
        push_rescue.repoint_git_credential(token, run=run, workdir=workdir)
    run(["git", "-C", workdir, "fetch", "origin", base])
    code, _, err = run(["git", "-C", workdir, "checkout", "-B", out.branch,
                        f"origin/{base}"])
    if code != 0:
        out.error = f"could not branch off origin/{base}: {redact(err).strip()}"
        _log(f"rescue delivery: {out.error}")
        return out

    # `git am` first: it replays the agent's own commits, messages and
    # authorship. A plain diff is what write_patch falls back to on a shallow
    # clone, and `am` refuses it — so the fallback is applied and committed as
    # one commit rather than losing the work a second time.
    code, _, am_err = run(["git", "-C", workdir, "am", "-3", patch])
    if code != 0:
        run(["git", "-C", workdir, "am", "--abort"])
        code, _, apply_err = run(["git", "-C", workdir, "apply", "--3way", patch])
        if code != 0:
            out.error = (f"neither `git am` nor `git apply` could replay "
                         f"{os.path.basename(patch)}: "
                         f"{redact(apply_err).strip() or redact(am_err).strip()}")
            _log(f"rescue delivery: {out.error}")
            return out
        run(["git", "-C", workdir, "add", "-A"])
        code, _, err = run([
            "git", "-C", workdir, "commit", "-m",
            f"feat({card}): work rescued from run {run_id} ({artifact})",
        ])
        if code != 0:
            out.error = f"the applied patch produced no commit: {redact(err).strip()}"
            _log(f"rescue delivery: {out.error}")
            return out

    code, _, err = run(["git", "-C", workdir, "push", "origin",
                        f"{out.branch}:refs/heads/{out.branch}"])
    if code != 0:
        out.error = f"pushing {out.branch} failed: {redact(err).strip()}"
        _log(f"rescue delivery: {out.error}")
        return out
    out.pushed = True

    title = f"feat({card}): rescued delivery of run {run_id}"
    code, created, err = run(
        ["gh", "pr", "create", "--repo", repo, "--base", base,
         "--head", out.branch, "--title", title,
         "--body", _pr_body(card, card_url, run_id=run_id, artifact=artifact)],
        cwd=workdir, env=_gh_env(token),
    )
    if code != 0:
        out.error = f"opening the pull request failed: {redact(err).strip()}"
        _log(f"rescue delivery: {out.error}")
    else:
        out.pr_opened = True
        out.pr_url = (created or "").strip().splitlines()[-1] if created.strip() else ""

    try:
        post(
            f"🚚 {DELIVERED_TAG}: run {run_id}'s artifact {artifact} is now "
            f"`{out.branch}`{' → ' + out.pr_url if out.pr_url else ''}. The "
            f"commits are the build agent's; only the replay and the pull "
            f"request are this job's."
        )
    except Exception as exc:
        _log(f"rescue delivery: could not comment on {card} — {exc}")
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    h = sub.add_parser("handoff", help="say the push failed and dispatch the delivery")
    h.add_argument("--card", required=True)
    h.add_argument("--repo", required=True)
    h.add_argument("--run-id", required=True)
    h.add_argument("--artifact", default="")
    h.add_argument("--status", default="")
    h.add_argument("--mints", type=int, default=2)
    h.add_argument("--stderr", default="",
                   help="git's own refusal, quoted onto the card (redacted)")

    a = sub.add_parser("apply", help="deliver a downloaded rescue patch")
    a.add_argument("--card", required=True)
    a.add_argument("--repo", required=True)
    a.add_argument("--run-id", required=True)
    a.add_argument("--patch-dir", required=True)
    a.add_argument("--artifact", default="")
    a.add_argument("--base", default="main")
    a.add_argument("--card-url", default="")
    a.add_argument("--workdir", default=".")

    args = parser.parse_args(argv)
    if args.command == "handoff":
        handoff(
            args.card, repo=args.repo, run_id=args.run_id,
            artifact=args.artifact or f"rescue-{args.card}.patch",
            status=args.status, mints=args.mints, stderr=args.stderr,
        )
        # ALWAYS 0, for push_rescue.py's reason: a red step here summons the
        # medic to re-run a run that already did the work. The card carries
        # the outcome, and the sweep re-checks it.
        return 0

    outcome = deliver(
        args.card, repo=args.repo, run_id=args.run_id,
        patch_dir=args.patch_dir, base=args.base,
        token=os.environ.get(push_rescue.TOKEN_ENV, "").strip(),
        card_url=args.card_url,
        artifact=args.artifact or f"rescue-{args.card}.patch",
        workdir=args.workdir,
    )
    # This one DOES fail loudly: it is a job whose only purpose is the
    # delivery, so a delivery that did not happen must be red — the medic and
    # the next sweep are the retry, and a green run here would claim the work
    # shipped.
    if not outcome.pr_opened and not outcome.pr_url:
        print(f"rescue delivery FAILED: {outcome.error or 'no pull request'}")
        return 1
    print(f"rescue delivery: {outcome.pr_url or 'the pull request'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
