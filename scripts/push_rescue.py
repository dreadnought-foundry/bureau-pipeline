#!/usr/bin/env python3
"""Deliver work the run's start credential could not push (DRE-3043, stdlib).

THE CEILING THIS REMOVES. `agent-task.yml` mints a GitHub App installation
token at the top of the job. GitHub kills it at exactly 60 minutes, and every
git credential on the runner is that token: `actions/checkout` writes it into
`http.https://github.com/.extraheader`, and the agent gets it again as
`GH_TOKEN`. A card that takes longer than an hour therefore cannot deliver its
own work, and nothing says so until the push.

It happened on 2026-09-03 (run 33822932627, card DRE-3029). The run started at
17:43 PT, the credential died at 18:43, and the agent finished at 18:53 with a
green 5,001-test suite it could not push a byte of. The requeue rebuilt the
whole card — another ~27 minutes and ~$21 for work that already existed on a
disk nobody could read.

AND THE CREDENTIAL IS THE RESCUE'S OWN (DRE-3098). This step used to fall back
to the token the AGENT step held when its own re-mint failed. On 2026-09-04 run
33896126776 (DRE-3088, its third turn-cap death) did exactly that TWENTY-SIX
minutes in — nowhere near the hour — and GitHub answered
`The requested URL returned error: 400`, which on `git push` over HTTPS is a
rejected credential. Three runs lost their partial work that way, and no branch
ever appeared for the card. The rescue push is the one push whose whole purpose
is to run after something has already gone wrong, so it must not spend a
credential that the failure may have invalidated: the workflow mints TWO tokens
of its own for it, and a refused push is retried once on the second — naming
GitHub's status and which mint it used — before anything is given up on.

So the step that pushes mints a FRESH token first (the workflow's own
`create-github-app-token` path, the same App) and this module spends it:

  1. RE-POINT GIT, NOT ONLY `gh`. The header `actions/checkout` left behind is
     what `git push` authenticates with, and `http.extraheader` is
     MULTI-VALUED: git sends every value it finds, and `git config <key>
     <value>` REFUSES a key that already holds more than one ("error: cannot
     overwrite multiple values with a single value", exit 5), leaving every
     dead header in place. So the stale values are unset first — that is what
     makes the re-point unconditional rather than best-effort — and it happens
     BEFORE any credentialed read (`git ls-remote` is one).
  2. PUSH what GitHub does not have.
  3. OPEN THE PR if the card has none — the incident's other half is a branch
     that landed and a `gh pr create` that died.

STRICTLY A NO-OP ON THE HAPPY PATH. Almost every run pushes its own work; for
those there is nothing unpushed and a PR already exists, and this module makes
no write at all. A second PR for one card is worse than the bug it fixes.

IT NEVER FAILS THE JOB. A rescue that cannot rescue prints why and exits 0: a
red step here summons the medic to re-run a run that already did the work.
What it does instead is REPORT — `local_work=true` says the work is on the
runner and GitHub does not have it, which is what `check_agent_result.py`
reads to classify the run as a credential expiry rather than a dead agent —
and PRESERVE: the branch's commits are written to `rescue-<card>.patch`, which
the workflow uploads as a run artifact. A run whose disk is about to be
destroyed must not be the last copy of the work (DRE-3098).

CLI (the form agent-task.yml calls; outputs on stdout, logs on stderr, so the
whole thing can be appended to `$GITHUB_OUTPUT`):

    PUSH_TOKEN=<fresh> PUSH_TOKEN_RETRY=<another fresh> \\
    python3 push_rescue.py rescue \\
        --card DRE-3043 --repo owner/name --base main \\
        --card-url <url> --card-title <title>
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess  # nosec B404 — git and gh; argv lists, never a shell
import sys
import tempfile

# The credential `actions/checkout` configures, and the one `git push` reads.
# Spelled exactly as checkout writes it: the key is per-origin, so a different
# spelling sets a second, ignored config entry and the push still uses the
# corpse.
GITHUB_EXTRAHEADER = "http.https://github.com/.extraheader"

# The tokens are read from the environment, never from argv: an argv element is
# visible to every process on the runner via /proc. Two of them (DRE-3098): the
# rescue's own mint and its own re-mint, so a refused push has a genuinely
# different credential to retry with. The *_SOURCE variables are labels, not
# secrets — they are what the failure line names, so a reader can tell which
# mint GitHub refused.
TOKEN_ENV = "PUSH_TOKEN"
RETRY_TOKEN_ENV = "PUSH_TOKEN_RETRY"
TOKEN_SOURCE_ENV = "PUSH_TOKEN_SOURCE"
RETRY_TOKEN_SOURCE_ENV = "PUSH_TOKEN_RETRY_SOURCE"

# The card identifier becomes a git ref GLOB (`agent/<CARD>-*`). It arrives
# from the relay's client_payload, which is outside the trust boundary, so it
# is validated rather than trusted — a pattern, not a sanitiser, so anything
# unexpected is refused instead of silently rewritten.
_CARD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# The agent's three deliberate exits (briefs/engineer.md, agent-task.yml's
# Report step). An agent that wrote one of these chose NOT to open a pull
# request — an escalation asks the CEO a question, a blocker parks the card, a
# hand-back returns it to Planning — and a rescue that pushed anyway would
# overrule the one decision the prompt asks the agent to make for itself. So
# any of them present makes this a total no-op, branch or no branch.
STOP_NOTES = (
    "/tmp/agent-escalation.txt",  # nosec B108 — the prompt's own paths
    "/tmp/agent-blocker.txt",  # nosec B108
    "/tmp/agent-handback.txt",  # nosec B108
)

# What GitHub says when it refuses a credential, in the two shapes git and gh
# print it: `The requested URL returned error: 400` (the DRE-3098 incident) and
# `(HTTP 401)`. Anchored on the words, never on a bare three-digit run of
# characters — `git 2.43` is not a status.
_STATUS_RE = re.compile(r"(?:error|HTTP)[:\s]+(\d{3})\b", re.IGNORECASE)


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _preserved(outcome) -> str:
    """The line that says where the work went when it could not be pushed."""
    if outcome.patch:
        return (f"push rescue: the branch's commits are written to "
                f"{outcome.patch} and uploaded as a run artifact — the runner "
                f"is not the last copy")
    return ("push rescue: the branch's commits could NOT be written out — "
            "this run's disk is the only copy of the work")


def _subprocess_run(argv, *, cwd=None, env=None):
    """(returncode, stdout, stderr). The one seam every caller injects in
    tests — no shell, ever."""
    done = subprocess.run(  # nosec B603 — argv list, no shell
        argv, cwd=cwd, env=env, capture_output=True, text=True
    )
    return done.returncode, (done.stdout or ""), (done.stderr or "")


class Outcome:
    """What the rescue found and what it did about it.

    `local_work` is the one field another step reads: commits for this card
    that GitHub does not have. It stays true when the push is REFUSED — that
    is exactly the case the Report step must describe as a credential expiry
    rather than a dead agent.

    `push_status` and `patch` are the other two the workflow spends
    (DRE-3098): the HTTP status GitHub refused with, and the file holding the
    work that never reached it.
    """

    def __init__(self):
        self.branch = ""
        self.local_sha = ""
        self.remote_sha = ""
        self.local_work = False
        self.pushed = False
        self.pr_opened = False
        self.pr_url = ""
        self.error = ""
        self.push_status = ""
        self.attempts = 0
        self.patch = ""

    @property
    def rescued(self) -> bool:
        return self.pushed or self.pr_opened

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return (
            f"<Outcome branch={self.branch!r} local_work={self.local_work} "
            f"pushed={self.pushed} pr_opened={self.pr_opened}>"
        )


def _flag(value: bool) -> str:
    return "true" if value else "false"


def output_lines(outcome: Outcome) -> list[str]:
    """The `key=value` lines the workflow appends to `$GITHUB_OUTPUT`."""
    return [
        f"branch={outcome.branch}",
        f"local_work={_flag(outcome.local_work)}",
        f"pushed={_flag(outcome.pushed)}",
        f"pr_opened={_flag(outcome.pr_opened)}",
        f"rescued={_flag(outcome.rescued)}",
        f"pr_url={outcome.pr_url}",
        f"push_status={outcome.push_status}",
        f"attempts={outcome.attempts}",
        f"patch={outcome.patch}",
    ]


def http_status(text: str) -> str:
    """The HTTP status GitHub refused with, or "".

    Worth reading rather than guessing: 400 and 401 on a push are both a
    rejected credential and neither is an expiry, which is the whole reason
    DRE-3098 exists. An unrecognised message answers "" — a refusal we cannot
    name is not one we invent a number for.
    """
    match = _STATUS_RE.search(text or "")
    return match.group(1) if match else ""


def default_patch_path(card: str) -> str:
    """Where the branch's commits are written when nothing can be pushed.

    `RUNNER_TEMP` on a runner, the system temp dir anywhere else; the name is
    the artifact's name so the run page and the file agree.
    """
    directory = os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()
    return os.path.join(directory, f"rescue-{card}.patch")


def write_patch(branch: str, base: str, path: str, *, run, workdir: str = ".") -> str:
    """Write `branch`'s own commits to `path`. Returns the path, or "".

    `format-patch` first: it carries the commit messages and authorship, so
    `git am` replays the agent's history rather than one squashed blob. A
    plain diff is the fallback for the cases format-patch refuses (a root
    commit with no merge base, a shallow clone). The base is tried as
    `origin/<base>` and then `<base>`, exactly as `_commits_ahead` does — a
    runner's checkout may carry either.
    """
    if not path:
        return ""
    for ref in (f"origin/{base}", base):
        for sub in (["format-patch", "--stdout", f"{ref}..{branch}"],
                    ["diff", f"{ref}...{branch}"]):
            code, out, _ = run(["git", "-C", workdir, *sub])
            if code == 0 and out.strip():
                try:
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(out)
                except OSError:
                    return ""
                return path
    return ""


def basic_auth_header(token: str) -> str:
    """The header value `actions/checkout` writes, with a different token."""
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"AUTHORIZATION: basic {encoded}"


def repoint_git_credential(token: str, *, run, workdir: str = ".") -> None:
    """Point git at `token`, having removed whatever was there.

    `--unset-all` first is the load-bearing half, and not for the reason it
    looks like. `http.extraheader` is multi-valued, and `git config <key>
    <value>` REFUSES to write a key that already holds more than one value:

        error: cannot overwrite multiple values with a single value
               Use a regexp, --add or --replace-all …

    exit 5, both dead headers still in place. `actions/checkout` leaves more
    than one whenever it configures submodules or a second remote, so on
    exactly those repositories the re-point would fail and the rescue would
    push with the corpse. Unsetting first makes it unconditional. (And git
    sends EVERY value it finds, so `--add`-ing a live header beside a dead one
    would send two Authorization headers rather than fixing anything.)

    The unset itself is allowed to fail: unsetting a key that is not set exits
    5 too, and that is the normal case on a runner that never checked out over
    HTTPS.
    """
    run(["git", "-C", workdir, "config", "--local", "--unset-all",
         GITHUB_EXTRAHEADER])
    code, _, err = run(["git", "-C", workdir, "config", "--local",
                        GITHUB_EXTRAHEADER, basic_auth_header(token)])
    if code != 0:
        raise RuntimeError(f"could not re-point the git credential: {err.strip()}")


def card_branch(card: str, *, run, workdir: str = ".") -> str:
    """This card's local branch (`agent/<CARD>-…`), or "".

    Anchored to the card, exactly like the workflow's own `git branch -r`
    lookup: a run must never deliver another card's work (DRE-1343).
    """
    code, out, _ = run([
        "git", "-C", workdir, "for-each-ref", "--format=%(refname:short)",
        f"refs/heads/agent/{card}-*",
    ])
    if code != 0:
        return ""
    names = [line.strip() for line in out.splitlines() if line.strip()]
    return names[0] if names else ""


def _rev_parse(ref: str, *, run, workdir: str) -> str:
    code, out, _ = run(["git", "-C", workdir, "rev-parse", ref])
    return out.strip() if code == 0 else ""


def _remote_sha(branch: str, *, run, workdir: str) -> str:
    code, out, _ = run([
        "git", "-C", workdir, "ls-remote", "origin", f"refs/heads/{branch}"
    ])
    if code != 0 or not out.strip():
        return ""
    return out.split()[0].strip()


def _commits_ahead(branch: str, base: str, *, run, workdir: str) -> int:
    """How many commits the branch carries that the base does not.

    Zero means an agent that created the branch and died before committing:
    pushing it would open an empty PR for the critic to review.
    """
    for ref in (f"origin/{base}", base):
        code, out, _ = run([
            "git", "-C", workdir, "rev-list", "--count", f"{ref}..{branch}"
        ])
        if code == 0 and out.strip().isdigit():
            return int(out.strip())
    # Unreadable: assume there IS work rather than silently discarding a run.
    return 1


def _gh_env(token: str) -> dict:
    env = dict(os.environ)
    env["GH_TOKEN"] = token
    env["GITHUB_TOKEN"] = token
    return env


def _existing_pr(branch: str, repo: str, token: str, *, run, workdir: str) -> str:
    """This branch's PR url, or "". `--state all`: a PR that merged seconds
    ago is still a PR (DRE-2316)."""
    code, out, _ = run(
        ["gh", "pr", "list", "--repo", repo, "--head", branch, "--state", "all",
         "--json", "url", "--limit", "5"],
        cwd=workdir, env=_gh_env(token),
    )
    if code != 0:
        # Unreadable is NOT "no PR" (DRE-2034): opening one here would be the
        # second PR for a card that already has one.
        return "unreadable"
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        return "unreadable"
    return (rows[0].get("url") or "") if rows else ""


def _pr_body(card: str, card_url: str) -> str:
    """The body for a PR the AGENT did not open.

    It says exactly that, and nothing about whether the work is complete —
    this step knows only that commits existed on the runner and had not
    reached GitHub. Claiming the card is done would be a claim nothing here
    can support, and the critic reads this body.
    """
    return (
        f"{card_url}\n\n"
        f"**Opened by the run's push-rescue step, not by the agent** "
        f"(DRE-3043). This card's commits were on the runner and had not "
        f"reached GitHub, and the credential the run started with had expired "
        f"— a GitHub App installation token lives one hour — so the branch was "
        f"delivered with a freshly minted one.\n\n"
        f"The commits are the agent's; only the push and this pull request are "
        f"not. Nothing here asserts the card is finished: read the diff and the "
        f"checks, and expect no author-written summary.\n"
    )


def _credentials(token, retry_token, token_source, retry_token_source):
    """The credentials to try, in order, de-duplicated.

    A re-mint that quietly handed back the SAME token is not a second
    credential, and pushing with it again only doubles the failure — so the
    retry is dropped rather than performed (DRE-3098).
    """
    creds: list[tuple[str, str]] = []
    for value, source in ((token, token_source),
                          (retry_token, retry_token_source)):
        value = (value or "").strip()
        if value and value not in [c[0] for c in creds]:
            creds.append((value, (source or "").strip() or "an unnamed mint"))
    return creds


def rescue(
    card: str,
    repo: str,
    token: str,
    *,
    retry_token: str = "",
    token_source: str = "",
    retry_token_source: str = "",
    patch_path: str = "",
    base: str = "main",
    card_url: str = "",
    card_title: str = "",
    workdir: str = ".",
    run=None,
    log=_log,
    stop_notes=STOP_NOTES,
) -> Outcome:
    """Deliver the card's work with a credential that is still alive."""
    if not _CARD_RE.match(card or ""):
        raise ValueError(f"refusing an unrecognisable card reference: {card!r}")
    run = run or _subprocess_run
    out = Outcome()

    wrote = [p for p in (stop_notes or ()) if os.path.isfile(p) and os.path.getsize(p)]
    if wrote:
        log(f"push rescue: the agent chose a different exit ({wrote[0]}) — "
            f"nothing to deliver")
        return out

    out.branch = card_branch(card, run=run, workdir=workdir)
    if not out.branch:
        log(f"push rescue: no agent/{card}-* branch on the runner — nothing to "
            f"deliver")
        return out

    out.local_sha = _rev_parse(out.branch, run=run, workdir=workdir)
    ahead = _commits_ahead(out.branch, base, run=run, workdir=workdir)
    if not out.local_sha or ahead <= 0:
        log(f"push rescue: {out.branch} carries no commits of its own — nothing "
            f"to deliver")
        return out

    patch_path = patch_path or default_patch_path(card)
    credentials = _credentials(token, retry_token, token_source,
                               retry_token_source)
    if not credentials:
        # Both mints are continue-on-error, so "no credential at all" is a
        # state this step can reach — and it is precisely the state where the
        # runner must not be the last copy of the work.
        out.local_work = True
        out.patch = write_patch(out.branch, base, patch_path, run=run,
                                workdir=workdir)
        log(f"push rescue: no push credential was minted — {out.branch} cannot "
            f"be delivered")
        log(_preserved(out))
        return out

    # BEFORE any credentialed call: `git ls-remote` below authenticates too.
    active_token = credentials[0][0]
    repoint_git_credential(active_token, run=run, workdir=workdir)

    out.remote_sha = _remote_sha(out.branch, run=run, workdir=workdir)
    out.local_work = out.local_sha != out.remote_sha

    if out.local_work:
        for attempt, (candidate, source) in enumerate(credentials, start=1):
            if attempt > 1:
                # A DIFFERENT credential, minted by the rescue's own second
                # mint — not the one GitHub just refused, and not the one the
                # agent step held (DRE-3098).
                log(f"push rescue: retrying the push with {source}")
                repoint_git_credential(candidate, run=run, workdir=workdir)
                active_token = candidate
            out.attempts = attempt
            code, _, err = run([
                "git", "-C", workdir, "push", "origin",
                f"{out.branch}:refs/heads/{out.branch}",
            ])
            if code == 0:
                out.pushed = True
                out.error = ""
                out.push_status = ""
                break
            out.error = (err.strip().splitlines() or ["push refused"])[-1]
            out.push_status = http_status(err)
            named = f"HTTP {out.push_status}" if out.push_status else "no HTTP status"
            log(f"push rescue: pushing {out.branch} failed — {named}, "
                f"credential: {source} (attempt {attempt} of "
                f"{len(credentials)}) — {out.error}")
        if not out.pushed:
            out.patch = write_patch(out.branch, base, patch_path, run=run,
                                    workdir=workdir)
            log("push rescue: the work is still on the runner; the Report step "
                "records this as a credential expiry, not a dead agent")
            log(_preserved(out))
            return out
        # `git branch -r` is how the Gate and Report steps find this card's
        # branch, so the remote-tracking ref has to exist or a delivered
        # branch reads as no branch at all. Spelled as an explicit refspec:
        # `fetch origin <branch>` obeys `remote.origin.fetch`, which
        # actions/checkout narrows to the ONE ref it checked out — so the ref
        # this push created would not be written under it.
        run(["git", "-C", workdir, "fetch", "origin",
             f"+refs/heads/{out.branch}:refs/remotes/origin/{out.branch}"])
        log(f"push rescue: delivered {out.branch} with a freshly minted token")

    # `active_token`, not the one the step started with: after a retry that is
    # the credential GitHub actually accepted, and handing `gh` the one that
    # was just refused re-runs the incident one call further on.
    existing = _existing_pr(out.branch, repo, active_token, run=run,
                            workdir=workdir)
    if existing:
        out.pr_url = "" if existing == "unreadable" else existing
        if existing == "unreadable":
            out.error = out.error or "could not read this branch's PR state"
            log("push rescue: GitHub would not say whether a PR exists — not "
                "opening one, because 'could not tell' is not 'no PR'")
        else:
            log(f"push rescue: {out.branch} already has a pull request")
        return out

    title = (
        f"feat({card}): {card_title.strip()}" if card_title.strip()
        else f"feat({card}): delivered by push rescue"
    )
    code, created, err = run(
        ["gh", "pr", "create", "--repo", repo, "--base", base,
         "--head", out.branch, "--title", title,
         "--body", _pr_body(card, card_url)],
        cwd=workdir, env=_gh_env(active_token),
    )
    if code != 0:
        out.error = (err.strip().splitlines() or ["pr create refused"])[-1]
        log(f"push rescue: opening the pull request failed — {out.error}")
        return out
    out.pr_opened = True
    out.pr_url = (created or "").strip().splitlines()[-1] if created.strip() else ""
    log(f"push rescue: opened {out.pr_url or 'the pull request'}")
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("rescue", help="deliver the card's work with a fresh token")
    r.add_argument("--card", required=True)
    r.add_argument("--repo", required=True)
    r.add_argument("--base", default="main")
    r.add_argument("--card-url", default="")
    r.add_argument("--card-title", default="")
    r.add_argument("--workdir", default=".")
    r.add_argument("--patch", default="",
                   help="where to write the branch's commits when nothing can "
                        "be pushed (default: $RUNNER_TEMP/rescue-<card>.patch)")
    args = parser.parse_args(argv)

    token = os.environ.get(TOKEN_ENV, "").strip()
    retry_token = os.environ.get(RETRY_TOKEN_ENV, "").strip()
    outcome = Outcome()
    if not token and not retry_token:
        # Not "skipped" any more (DRE-3098): with no credential the push is
        # impossible, which is exactly when the work must still be written out
        # rather than destroyed with the runner. rescue() handles it.
        _log(f"push rescue: neither {TOKEN_ENV} nor {RETRY_TOKEN_ENV} is in "
             f"the environment — nothing can be pushed")
    try:
        outcome = rescue(
            args.card, args.repo, token,
            retry_token=retry_token,
            token_source=os.environ.get(TOKEN_SOURCE_ENV, "").strip(),
            retry_token_source=os.environ.get(
                RETRY_TOKEN_SOURCE_ENV, "").strip(),
            patch_path=args.patch,
            base=args.base, card_url=args.card_url,
            card_title=args.card_title, workdir=args.workdir,
        )
    except Exception as exc:  # never fail the job over a rescue
        _log(f"push rescue: skipped — {exc}")
    for line in output_lines(outcome):
        print(line)
    # ALWAYS 0. A red step here calls the medic to re-run a run that already
    # did the work; the Report step reads `local_work` and says so instead.
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
