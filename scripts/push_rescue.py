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
     BEFORE any credentialed read (`git ls-remote` is one). And since
     2026-09-09 (DRE-3513) the header checkout leaves is not IN `.git/config`
     at all: it lives in `$RUNNER_TEMP/git-credentials-<uuid>.config` and is
     reached through `includeIf.gitdir:…` entries, which `--unset-all` cannot
     see through — so those include KEYS are unset too, and the file itself is
     never touched.
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

AND THE ARTIFACT IS NOT THE END OF IT (DRE-3262). Preserving the work is not
delivering it: on 2026-09-06 (agent-bureau run 34045232203, DRE-3165) both of
the mints above were refused with 400, this module wrote the patch exactly as
designed, and the branch never appeared — a person found the artifact by hand
five hours later. So `push_status` and `error` are outputs, not just log lines,
and `deliver_rescue.py` spends them: the run's last step names the artifact on
the card and dispatches a `deliver-rescue` follow-up that replays the patch with
a credential of its own. What is preserved here is what that job delivers.

AND A FIX RUN IS A DIFFERENT DELIVERY (DRE-6348). A fix run pushes onto a pull
request that already exists, on a branch the run names, and two optional flags
say so — the build workflow's call passes neither and is unchanged:

  * `--branch <name>` delivers that branch instead of the `agent/<CARD>-*`
    glob, and the stop notes are not read at all: in the fix job the handoff
    files are the Report's to read (DRE-6351), and a committed fix outranks a
    note written beside it — the DRE-4883 run ended on a blocker written AFTER
    its push answered 401. The commits beyond `--base` (the head the run
    started from, DRE-6350) decide whether there is work, and the patch is
    written with `format-patch --always` so the fix loop's one sanctioned
    empty commit (DRE-5632) survives into it.
  * `--existing-pr <number>` never opens a pull request, and reads that one's
    state with the fresh credential before any push. `MERGED`, `CLOSED` and an
    unreadable answer all refuse it: the pre-push hook the fix workflow
    installs holds the job-start token and fails open, so by the time this
    step runs it allows everything — and a push onto a merged pull request's
    deleted branch recreates it (DRE-4486). Fail closed here instead; the
    patch keeps the work and the delivery follow-up asks again (DRE-6349).

Whenever `--branch` was given and the push was not made, `rescue-<CARD>.target.json`
is written beside the patch, naming the branch the patch belongs on.

AND AN EDIT NOBODY COMMITTED IS SAVED TOO (DRE-6959). On 2026-10-11 (run
38164304144, card DRE-6950) the build agent committed its failing test, made
the change in its working tree, started the web suite in the background and
ended its turn — which in a headless run ends the session. This step delivered
the one commit and opened a pull request holding a failing test and nothing
else; the change was in no commit, so nothing here looked at it and it died
with the runner. `--uncommitted <path>` is the opt-in that looks: before any
early return, the working tree is read against HEAD, and anything git does not
ignore is written to `<path>` as ONE `git diff --binary` — built on a throwaway
index, so the agent's own index and tree are left exactly as they were — whose
first line is `# applies onto <HEAD sha>`. It is never the `--patch` file: that
one is a `format-patch` series `deliver_rescue.py` replays with `git am`, and
loose edits would break the replay. Nor does it move any existing output —
`local_work` still means commits GitHub does not have, because reading it as
true turns the run into a credential expiry. The file is named in the body of
a pull request this step opens, or in one comment on the one that exists; with
no pull request, the workflow's card comment is the only place it is said.

AND A `branch-without-pr` NOTE IS THIS STEP'S JOB, NOT ITS EXIT (DRE-6719).
Since DRE-6443 the brief tells the agent to write `/tmp/agent-blocker.txt`
stamped `blocker-class: branch-without-pr` when its work is committed and the
pull request could not be opened — which is the very case this step exists
for, and any note used to make it stand aside. On 2026-10-10 five runs did
exactly that: each pushed its branch, met HTTP 401 on `gh pr create` because
the run's credential had expired, wrote the note, and this step printed "the
agent chose a different exit" while holding two fresh tokens. Each card went to
Backlog and waited for the sweep to open its pull request.

So a note whose FIRST line stamps that class, written alone, is delivered: the
branch is pushed if GitHub does not have it and the pull request opened if the
card has none, each tried on both tokens. The stamp, not the wording: a note
read as this class by its wording alone may describe a branch held on purpose
(DRE-6056, docs/blocker-classes.md). Any other class, a stamp-less note, or an
escalation or hand-back beside it is still a total no-op. The note itself is
left where the agent wrote it: the Report step reads `pr_opened` and `pr_url`
and reports the pull request as an ordinary build result, and when neither
token delivers, the note is posted as before and the sweep is the backstop.

CLI (the form agent-task.yml calls; outputs on stdout, logs on stderr, so the
whole thing can be appended to `$GITHUB_OUTPUT`):

    PUSH_TOKEN=<fresh> PUSH_TOKEN_RETRY=<another fresh> \\
    python3 push_rescue.py rescue \\
        --card DRE-3043 --repo owner/name --base main \\
        --card-url <url> --card-title <title> \\
        --uncommitted "$RUNNER_TEMP/rescue-DRE-3043.uncommitted.diff"

and the fix run's form:

    python3 push_rescue.py rescue --card DRE-4911 --repo owner/name \\
        --base <the sha the run started from> \\
        --branch agent/DRE-4911-x --existing-pr 7
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

# A `--branch` value becomes an argv element and a refspec. It must not open
# with `-` (an option) or carry `:` (a second refspec half), so it is held to
# the shape the pipeline's own branches have rather than trusted.
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

# The pull request states that refuse a fix-run push (DRE-6348). The words are
# the `error=` output, verbatim — DRE-6349 and the Report read them.
PR_MERGED = "pull request is MERGED"
PR_CLOSED = "pull request is CLOSED"
PR_UNREADABLE = "pull request state unreadable"

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

# The one exit that is NOT a decision against a pull request (DRE-6719): a
# blocker note stamped with this class says the work is committed and only the
# pull request is missing — the case this module exists for. Matched by the
# note's file name, so a test's scratch copy of the path counts the same.
BLOCKER_NOTE_NAME = "agent-blocker.txt"
BRANCH_WITHOUT_PR = "branch-without-pr"

# The heading of the paragraph a pull request gets when the build ended with
# edits that are in no commit (DRE-6959).
UNCOMMITTED_HEADING = "The build ended with uncommitted work."

# How much of a note is read for its stamp. The stamp is the first line; the
# rest is the agent's prose and none of this module's business.
_NOTE_READ_LIMIT = 65536

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
        # The fix run's two (DRE-6348): the `--branch` it named, and the
        # sidecar saying which branch the patch belongs on.
        self.target_branch = ""
        self.sidecar = ""
        # The working tree's edits that are in no commit (DRE-6959): the
        # file they were saved to, "" when none were or nothing was asked.
        self.uncommitted = ""

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


def one_line(text: str, limit: int = 300) -> str:
    """`text` as a single short line, safe for `$GITHUB_OUTPUT`.

    That file is `key=value` per LINE: a multi-line value silently swallows
    every output after it, so git's stderr is collapsed before it leaves this
    module (DRE-3262 — until then the refusal existed only in the run's log,
    which is where the cause of the DRE-3165 400 still is).
    """
    collapsed = " ".join((text or "").split())
    return collapsed[:limit]


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
        # GitHub's OWN words for the refusal (DRE-3262). The status alone sends
        # a reader to three suspects — a rejected credential, a branch
        # protection, the multi-value extraheader trap — and this line is what
        # tells them apart. It reaches the card, so it is collapsed to one line
        # and truncated; the run's log keeps the rest.
        f"error={one_line(outcome.error)}",
        # Appended after the ten above, which keep their names and order
        # (DRE-6348). Both empty on the build path.
        f"target_branch={outcome.target_branch}",
        f"sidecar={outcome.sidecar}",
        # After all twelve (DRE-6959). Empty unless `--uncommitted` was given
        # and the working tree held edits that were saved.
        f"uncommitted={outcome.uncommitted}",
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


def write_patch(branch: str, base: str, path: str, *, run, workdir: str = ".",
                always: bool = False) -> str:
    """Write `branch`'s own commits to `path`. Returns the path, or "".

    `format-patch` first: it carries the commit messages and authorship, so
    `git am` replays the agent's history rather than one squashed blob. A
    plain diff is the fallback for the cases format-patch refuses (a root
    commit with no merge base, a shallow clone). The base is tried as
    `origin/<base>` and then `<base>`, exactly as `_commits_ahead` does — a
    runner's checkout may carry either.

    `always` keeps EMPTY commits, which format-patch otherwise drops: the fix
    loop sanctions one (the What's new resubmission, DRE-5632), and without
    it that run's patch would be empty (DRE-6348).
    """
    if not path:
        return ""
    format_patch = ["format-patch", "--stdout"] + (["--always"] if always else [])
    for ref in (f"origin/{base}", base):
        for sub in ([*format_patch, f"{ref}..{branch}"],
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


def target_sidecar_path(card: str, patch_path: str) -> str:
    """Where `rescue-<CARD>.target.json` goes: beside the patch (DRE-6348).

    With no patch path, beside the default one — the same `RUNNER_TEMP` rule
    `default_patch_path` uses — so the artifact upload that takes the patch
    finds the sidecar in the same directory.
    """
    directory = os.path.dirname(patch_path or default_patch_path(card))
    return os.path.join(directory, f"rescue-{card}.target.json")


def write_target_sidecar(card: str, outcome, patch_path: str) -> str:
    """Write which branch the patch belongs on. Returns the path, or "".

    A fix run's patch belongs on the pull request's EXISTING branch, which the
    delivery follow-up cannot derive from the card (DRE-6349): `head` is what
    the push would have delivered, `remote_head` what the remote held before
    it ("" when that could not be read), so the follow-up can tell a branch
    that moved on from one that did not. Exactly these four keys.
    """
    path = target_sidecar_path(card, patch_path)
    body = {
        "card": card,
        "branch": outcome.branch,
        "head": outcome.local_sha,
        "remote_head": outcome.remote_sha,
    }
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(body, fh, indent=2)
            fh.write("\n")
    except OSError:
        return ""
    return path


def write_uncommitted(path: str, *, run, workdir: str = ".", log=_log) -> str:
    """Save the working tree's edits that are in no commit. Returns the path, or "".

    Counted first with `git status`, which honors `.gitignore` and
    `.git/info/exclude` — where the workflow hides `.bureau-pipeline/` — and
    runs with `--no-optional-locks`, so even git's own opportunistic index
    refresh is not written. A clean tree writes nothing.

    The diff is built on a THROWAWAY index (`GIT_INDEX_FILE`): `read-tree
    HEAD`, `add -A`, `diff --cached --binary HEAD`. That is what lets one
    diff carry the untracked files beside the modified and deleted ones while
    the agent's own index — whatever it had staged — is never touched. git
    writes the diff straight to a file, so a source file that is not UTF-8
    survives byte for byte; the `# applies onto <sha>` line goes above it,
    where `git apply` skips it as preamble.

    Any failure is logged and answers "": like everything in this module, a
    capture that cannot capture must never fail the job.
    """
    if not path:
        return ""
    try:
        code, out, err = run(["git", "--no-optional-locks", "-C", workdir,
                              "status", "--porcelain", "--untracked-files=all"])
        if code != 0:
            log(f"push rescue: could not read the working tree for uncommitted "
                f"work — {one_line(err)}")
            return ""
        changed = [line for line in out.splitlines() if line.strip()]
        if not changed:
            return ""
        head = _rev_parse("HEAD", run=run, workdir=workdir)
        if not head:
            log("push rescue: the working tree has uncommitted work but HEAD "
                "is unreadable — not saved")
            return ""
        with tempfile.TemporaryDirectory() as scratch:
            env = dict(os.environ)
            env["GIT_INDEX_FILE"] = os.path.join(scratch, "index")
            raw = os.path.join(scratch, "uncommitted.diff")
            for argv in (["read-tree", "HEAD"], ["add", "-A"],
                         ["diff", "--cached", "--binary", f"--output={raw}",
                          "HEAD"]):
                code, _, err = run(["git", "-C", workdir, *argv], env=env)
                if code != 0:
                    log(f"push rescue: could not save the uncommitted work "
                        f"(git {argv[0]}) — {one_line(err)}")
                    return ""
            with open(raw, "rb") as fh:
                diff = fh.read()
        if not diff.strip():
            return ""
        with open(path, "wb") as fh:
            fh.write(f"# applies onto {head}\n".encode())
            fh.write(diff)
    except Exception as exc:  # never fail the job over a rescue
        log(f"push rescue: could not save the uncommitted work — {exc}")
        return ""
    log(f"push rescue: the working tree held {len(changed)} uncommitted "
        f"file(s) — saved to {path}, which applies onto {head}")
    return path


def basic_auth_header(token: str) -> str:
    """The header value `actions/checkout` writes, with a different token."""
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"AUTHORIZATION: basic {encoded}"


def repoint_git_credential(token: str, *, run, workdir: str = ".",
                           log=_log) -> None:
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

    AND THE HEADER IS NO LONGER WHERE `--unset-all` LOOKS (DRE-3513). On
    2026-09-09 (agent-bureau run 34416564915, four cards) every rescue push got
    400 and the log said why:

        push rescue: git is sending 2 auth header(s), from:
          file:/home/runner/work/_temp/git-credentials-<uuid>.config,
          file:.git/config

    Today's `actions/checkout` writes its token into that `$RUNNER_TEMP` file
    and reaches it with `includeIf.gitdir:<gitdir>.path = <file>` entries in
    `.git/config` (four of them: the host gitdir, its worktrees, and container
    twins under `/github/workspace`). An included value is not a LOCAL value,
    so the unset above left it in place, the write ADDED a second header, and
    GitHub refused the pair. Every `includeIf.*.path` whose target is a
    `git-credentials-*.config` is therefore unset here — the KEY, in
    `.git/config`; the file is checkout's own to remove in its post step, and
    an include that points anywhere else is left alone.

    After the write the origins are counted once more and logged. A header
    the re-point cannot reach (`$HOME/.gitconfig` is outside `--local`) is
    named there and the push goes ahead anyway: a raise at this point would
    skip the patch and the receipt, and the whole reason the rescue exists is
    that the runner must not be the last copy.
    """
    run(["git", "-C", workdir, "config", "--local", "--unset-all",
         GITHUB_EXTRAHEADER])
    for key in credential_includes(run=run, workdir=workdir):
        run(["git", "-C", workdir, "config", "--local", "--unset", key])
    code, _, err = run(["git", "-C", workdir, "config", "--local",
                        GITHUB_EXTRAHEADER, basic_auth_header(token)])
    if code != 0:
        raise RuntimeError(f"could not re-point the git credential: {err.strip()}")
    log(_auth_header_line(credential_origins(run=run, workdir=workdir)))


def _auth_header_line(origins: list[str]) -> str:
    """The one log line that tells a 400-as-malformed-request apart from a
    400-as-rejected-credential (DRE-3262): how many AUTHORIZATION headers git
    will send, and from which config files. Origins only, never values."""
    return (f"push rescue: git is sending {len(origins)} auth header(s), "
            f"from: {', '.join(origins) or 'no config file'}")


# The file `actions/checkout` keeps its token in, as the include's target is
# spelled on the host (`$RUNNER_TEMP/git-credentials-<uuid>.config`) and in a
# container (`/github/runner_temp/git-credentials-<uuid>.config`).
_CREDENTIALS_FILE_RE = re.compile(r"git-credentials-.*\.config$")


def credential_includes(*, run, workdir: str = ".") -> list[str]:
    """The `includeIf.*.path` keys in `.git/config` that pull in checkout's
    credentials file (DRE-3513). Keys only — what `--unset` takes.

    `--get-regexp` exits 1 when nothing matches, which is the normal case on
    a runner that checked out with `persist-credentials: false` or over SSH.
    """
    code, out, _ = run([
        "git", "-C", workdir, "config", "--local", "--get-regexp",
        r"^includeif\..*\.path$",
    ])
    if code != 0 or not out.strip():
        return []
    keys = []
    for line in out.splitlines():
        # `<key> <value>`, split on the first whitespace: the key has dots and
        # a slash-laden subsection, the value is a path.
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and _CREDENTIALS_FILE_RE.search(parts[1].strip()):
            keys.append(parts[0])
    return keys


def credential_origins(*, run, workdir: str = ".") -> list[str]:
    """WHICH CONFIG FILES still hold a GitHub auth header (DRE-3262).

    Origins only, never values — the values are credentials.

    This exists because the DRE-3165 400 could not be diagnosed after the fact.
    A `git push` refused with HTTP 400 has three candidate causes and the run's
    log distinguished none of them: a rejected credential (DRE-3098's reading),
    a branch-protection refusal, or MORE THAN ONE `AUTHORIZATION` header on the
    request, which GitHub answers as a malformed request rather than an
    unauthorized one. `repoint_git_credential` unsets the LOCAL values before it
    writes, and `--local` is the only scope it can reach — a header left in
    `$HOME/.gitconfig`, in a worktree config or by a submodule pass is still
    sent, and still makes two. So a failed push prints the origins it found and
    the next occurrence is diagnosable from the log alone.

    It was this line that diagnosed DRE-3513: the second origin it named was
    checkout's `git-credentials-<uuid>.config`, reached through an include,
    which is why `repoint_git_credential` now unsets those includes too and
    prints this same line right after every re-point, delivered or not.
    """
    code, out, _ = run([
        "git", "-C", workdir, "config", "--show-origin", "--get-all",
        GITHUB_EXTRAHEADER,
    ])
    if code != 0 or not out.strip():
        return []
    # `--show-origin` prints `file:/path/to/config\t<value>` — the tab splits
    # the origin from the secret, and only the origin is kept.
    return [line.split("\t", 1)[0].strip() for line in out.splitlines()
            if line.strip()]


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


def pr_state_refusal(number: str, repo: str, token: str, *, run,
                     workdir: str) -> str:
    """"" when pull request `number` is open; otherwise why not to push.

    Read with the credential the push is about to spend, never the job-start
    one (DRE-6348). FAIL CLOSED: every answer that is not plainly an open,
    unmerged pull request refuses — the pre-push hook behind this fails open
    on an unreadable read, and nothing after this step asks again before the
    push lands. A `mergedAt` beside any state is a merge.
    """
    code, out, _ = run(
        ["gh", "pr", "view", number, "--repo", repo, "--json",
         "state,mergedAt"],
        cwd=workdir, env=_gh_env(token),
    )
    if code != 0:
        return PR_UNREADABLE
    try:
        row = json.loads(out or "")
    except ValueError:
        return PR_UNREADABLE
    if not isinstance(row, dict):
        return PR_UNREADABLE
    state = str(row.get("state") or "").upper()
    if state == "MERGED" or row.get("mergedAt"):
        return PR_MERGED
    if state == "CLOSED":
        return PR_CLOSED
    if state == "OPEN":
        return ""
    return PR_UNREADABLE


def delivered_note(written: list[str]) -> str:
    """The class of the one stop note this step delivers instead of obeying,
    or "" when it must stand aside (DRE-6719).

    Delivered only when the agent wrote the blocker note ALONE and its first
    line stamps `branch-without-pr`. An escalation or a hand-back beside it is
    a decision against a pull request, and the stamp is required because a
    note read as this class by its wording may describe a branch held on
    purpose. Anything this cannot read — the note, the vocabulary — answers
    "": standing aside is today's behavior, and the sweep's backstop.
    """
    if len(written) != 1 or os.path.basename(written[0]) != BLOCKER_NOTE_NAME:
        return ""
    try:
        with open(written[0], encoding="utf-8", errors="replace") as fh:
            text = fh.read(_NOTE_READ_LIMIT)
        import blocker_class  # the one reader of the stamp (DRE-6438)
        stamped = blocker_class.stamp_class(text)
    except Exception:  # never fail the job over a rescue
        return ""
    return stamped if stamped == BRANCH_WITHOUT_PR else ""


def uncommitted_paragraph(path: str) -> str:
    """What a pull request is told about edits that are in no commit (DRE-6959).

    It names the artifact by the file's base name, which is the name the
    workflow uploads it under, and says the edits are NOT in the pull request
    — a reviewer reading the diff must not take it for the whole change.
    """
    name = os.path.basename(path)
    return (
        f"**{UNCOMMITTED_HEADING}** The agent's working tree held changes that "
        f"are in no commit, so they are not in this pull request. They are "
        f"saved as the run artifact `{name}`: `git apply` onto the commit its "
        f"first line names reproduces them (DRE-6959).\n"
    )


def _pr_body(card: str, card_url: str, note: str = "",
             uncommitted: str = "") -> str:
    """The body for a PR the AGENT did not open.

    It says exactly that, and nothing about whether the work is complete —
    this step knows only that commits existed on the runner and had not
    reached GitHub, or that the agent's own note said the pull request was
    missing. Claiming the card is done would be a claim nothing here can
    support, and the critic reads this body. Edits that never reached a
    commit are named in a paragraph of their own (DRE-6959).
    """
    left = f"\n{uncommitted_paragraph(uncommitted)}" if uncommitted else ""
    because = (
        f"The agent's blocker note was stamped `{note}`: it said the work is "
        f"committed on this branch and the pull request could not be opened. "
        f"This step opened it with a freshly minted token instead of leaving "
        f"the card for the sweep (DRE-6719).\n\n"
        if note else ""
    )
    return (
        f"{card_url}\n\n"
        f"**Opened by the run's push-rescue step, not by the agent** "
        f"(DRE-3043). This card's commits were on the runner and had not "
        f"reached GitHub, and the credential the run started with had expired "
        f"— a GitHub App installation token lives one hour — so the branch was "
        f"delivered with a freshly minted one.\n\n"
        f"{because}"
        f"The commits are the agent's; only the push and this pull request are "
        f"not. Nothing here asserts the card is finished: read the diff and the "
        f"checks, and expect no author-written summary.\n"
        f"{left}"
    )


def comment_uncommitted(pr_url: str, repo: str, token: str, path: str, *,
                        run, workdir: str, log=_log) -> bool:
    """Name the saved edits in ONE comment on a pull request that exists.

    The token is the one whose lookup just found the pull request. A refusal
    is logged and answered False — the card comment still says it.
    """
    try:
        code, _, err = run(
            ["gh", "pr", "comment", pr_url, "--repo", repo,
             "--body", uncommitted_paragraph(path)],
            cwd=workdir, env=_gh_env(token),
        )
    except Exception as exc:  # never fail the job over a rescue
        code, err = 1, str(exc)
    if code != 0:
        log(f"push rescue: could not comment the uncommitted work on "
            f"{pr_url} — {one_line(err)}")
        return False
    log(f"push rescue: named the uncommitted work on {pr_url}")
    return True


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
    branch: str = "",
    existing_pr: str = "",
    uncommitted_path: str = "",
) -> Outcome:
    """Deliver the card's work with a credential that is still alive.

    `branch` and `existing_pr` are the fix run's (DRE-6348); with neither,
    this is the build run's rescue exactly as it was. `uncommitted_path` is
    the build run's opt-in (DRE-6959): without it no new git command runs.
    """
    if not _CARD_RE.match(card or ""):
        raise ValueError(f"refusing an unrecognisable card reference: {card!r}")
    if branch and (not _BRANCH_RE.match(branch) or ".." in branch):
        raise ValueError(f"refusing an unrecognisable branch name: {branch!r}")
    existing_pr = str(existing_pr or "").strip()
    if existing_pr and not existing_pr.isdigit():
        raise ValueError(f"refusing an unrecognisable pull request number: "
                         f"{existing_pr!r}")
    run = run or _subprocess_run
    out = Outcome()
    out.target_branch = branch
    # BEFORE every early return below (DRE-6959): a stop note, no branch and
    # no commits all leave the edits on the runner just the same, and the
    # runner must not be their last copy either.
    out.uncommitted = write_uncommitted(uncommitted_path, run=run,
                                        workdir=workdir, log=log)

    def preserve():
        # The work that is not being pushed: the patch always, and on the fix
        # path the sidecar naming the branch it belongs on.
        out.patch = write_patch(out.branch, base, patch_path, run=run,
                                workdir=workdir, always=bool(branch))
        if branch:
            out.sidecar = write_target_sidecar(card, out, patch_path)
        log(_preserved(out))

    note = ""
    if branch:
        # The stop notes are the BUILD agent's exits. On the fix path the
        # handoff files are the Report's to read (DRE-6351), and a committed
        # fix outranks a note written beside it (DRE-4883): the commits below
        # decide, and nothing else does.
        out.branch = branch
        if not _rev_parse(f"refs/heads/{branch}", run=run, workdir=workdir):
            log(f"push rescue: no {branch} branch on the runner — nothing to "
                f"deliver")
            return out
    else:
        wrote = [p for p in (stop_notes or ())
                 if os.path.isfile(p) and os.path.getsize(p)]
        note = delivered_note(wrote)
        if wrote and not note:
            log(f"push rescue: the agent chose a different exit ({wrote[0]}) — "
                f"nothing to deliver")
            return out
        if note:
            log(f"push rescue: the agent's note ({wrote[0]}) is stamped "
                f"{note} — the work is committed and only the pull request is "
                f"missing, so this step delivers it with its own token "
                f"(DRE-6719)")

        out.branch = card_branch(card, run=run, workdir=workdir)
        if not out.branch:
            log(f"push rescue: no agent/{card}-* branch on the runner — nothing "
                f"to deliver")
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
        log(f"push rescue: no push credential was minted — {out.branch} cannot "
            f"be delivered")
        preserve()
        return out

    # BEFORE any credentialed call: `git ls-remote` below authenticates too,
    # and under two headers it answers 400 — so a branch the agent HAD pushed
    # read as absent, the rescue pushed it again, got 400 again, and wrote a
    # false "needs a human" receipt (three of DRE-3513's four cases).
    active_token = credentials[0][0]
    repoint_git_credential(active_token, run=run, workdir=workdir, log=log)

    out.remote_sha = _remote_sha(out.branch, run=run, workdir=workdir)
    out.local_work = out.local_sha != out.remote_sha

    if out.local_work:
        for attempt, (candidate, source) in enumerate(credentials, start=1):
            if attempt > 1:
                # A DIFFERENT credential, minted by the rescue's own second
                # mint — not the one GitHub just refused, and not the one the
                # agent step held (DRE-3098).
                log(f"push rescue: retrying the push with {source}")
                repoint_git_credential(candidate, run=run, workdir=workdir,
                                       log=log)
                active_token = candidate
            out.attempts = attempt
            if existing_pr:
                # Asked per credential, after the re-point and before its
                # push, so the read and the push spend one token (DRE-6348).
                # MERGED or CLOSED refuses outright; "unreadable" is this
                # credential's failure, so the next mint asks again — and
                # when none can read it, unreadable refuses.
                refusal = pr_state_refusal(existing_pr, repo, candidate,
                                           run=run, workdir=workdir)
                if refusal:
                    out.error = refusal
                    out.push_status = ""
                    log(f"push rescue: not pushing {out.branch} — {refusal} "
                        f"(#{existing_pr}), credential: {source} (attempt "
                        f"{attempt} of {len(credentials)})")
                    if refusal != PR_UNREADABLE:
                        break
                    continue
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
            # DRE-3262: the one thing the DRE-3165 log did not say. A 400 with
            # more than one origin here is a malformed request (two
            # AUTHORIZATION headers), not a rejected credential — and those two
            # readings send a reader to opposite ends of the system.
            log(_auth_header_line(credential_origins(run=run, workdir=workdir)))
        if not out.pushed:
            if out.error not in (PR_MERGED, PR_CLOSED):
                log("push rescue: the work is still on the runner; the Report "
                    "step records this as a credential expiry, not a dead "
                    "agent")
            preserve()
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

    if existing_pr:
        # The caller's own pull request: never a second one, and its url is
        # the caller's to know (DRE-6348).
        return out

    # `active_token` first, not the one the step started with: after a retry
    # that is the credential GitHub actually accepted, and handing `gh` the one
    # that was just refused re-runs the incident one call further on. The
    # other mint is the second try (DRE-6719): a pull request GitHub refused on
    # one token is asked again on the other before the card is left to the
    # sweep — and the lookup is repeated on it, so a create that landed despite
    # its error is found rather than opened twice.
    title = (
        f"feat({card}): {card_title.strip()}" if card_title.strip()
        else f"feat({card}): delivered by push rescue"
    )
    tries = [active_token] + [c for c, _ in credentials if c != active_token]
    unreadable = False
    for candidate in tries:
        existing = _existing_pr(out.branch, repo, candidate, run=run,
                                workdir=workdir)
        if existing == "unreadable":
            unreadable = True
            continue
        if existing:
            out.pr_url = existing
            out.error = ""
            log(f"push rescue: {out.branch} already has a pull request")
            if out.uncommitted:
                comment_uncommitted(existing, repo, candidate, out.uncommitted,
                                    run=run, workdir=workdir, log=log)
            return out
        code, created, err = run(
            ["gh", "pr", "create", "--repo", repo, "--base", base,
             "--head", out.branch, "--title", title,
             "--body", _pr_body(card, card_url, note, out.uncommitted)],
            cwd=workdir, env=_gh_env(candidate),
        )
        if code == 0:
            out.pr_opened = True
            out.error = ""
            out.pr_url = (created.strip().splitlines()[-1]
                          if (created or "").strip() else "")
            log(f"push rescue: opened {out.pr_url or 'the pull request'}")
            if note:
                log(f"push rescue: the agent's {note} note is answered — the "
                    f"Report step reports this pull request, not the blocker")
            return out
        out.error = (err.strip().splitlines() or ["pr create refused"])[-1]
        log(f"push rescue: opening the pull request failed — {out.error}")
    if unreadable and not out.error:
        out.error = "could not read this branch's PR state"
        log("push rescue: GitHub would not say whether a PR exists — not "
            "opening one, because 'could not tell' is not 'no PR'")
    if note:
        log(f"push rescue: no pull request was opened for the agent's {note} "
            f"note — the blocker stands, and the sweep opens it")
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
    r.add_argument("--uncommitted", default="",
                   help="where to save the working tree's edits that are in "
                        "no commit, as one diff against HEAD; never the "
                        "--patch file (DRE-6959). Without it the tree is not "
                        "read")
    r.add_argument("--branch", default="",
                   help="deliver this existing branch instead of the "
                        "agent/<card>-* lookup; the stop notes are not read "
                        "(the fix run, DRE-6348)")
    r.add_argument("--existing-pr", default="",
                   help="the pull request the branch belongs to: never open "
                        "one, and push only while it is OPEN")
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
            branch=args.branch, existing_pr=args.existing_pr,
            uncommitted_path=args.uncommitted,
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
