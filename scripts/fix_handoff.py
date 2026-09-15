#!/usr/bin/env python3
"""The fix loop's handoff, keyed to the repo, PR and head it was dispatched for.

THE FAULT (DRE-3951, 2026-09-14, two sightings in two repos):

  * portico #501 (DRE-3947). The critic returned REQUEST_CHANGES at 15:11 PT.
    Two minutes later the fix run posted "PR #2556 was already approved and
    merged before this run started. DRE-3863 is Done" — agent-bureau #2556's
    history, on another repository's pull request.
  * agent-bureau #2553 (DRE-3891), holding an APPROVE and a verifier PASS,
    collected four fix-agent comments about PR #2516's card and history.

The carrier was the handoff file. `agent-fix.yml` told the fixing agent to
write its escalation to the FIXED path `/tmp/fix-blocker.txt`, and the Report
step read that path back with `[ -f ... ]`. Nothing clears `/tmp` between jobs
on the reused self-hosted minis, so one card's escalation was posted on the
next card's pull request — #2556's run found #2516's file dated 09-13 06:56,
and #2567's run then posted #2556's.

THE ANSWER, in three parts:

  1. **One keyed directory per run.** `handoff_dir()` names a directory from
     (repo, PR, head sha) under the job's own `RUNNER_TEMP`, so two runs on one
     machine cannot share a path even when they are the same PR number in
     different repositories.
  2. **Opened at the top of the job.** `open_handoff()` deletes the legacy
     fixed paths, every other run's keyed handoff it can see, and its own last
     attempt's, then stamps the directory with what this run is for.
  3. **A read that refuses rather than guesses.** `read_handoff()` returns a
     body only when the stamp proves the directory is this run's and nothing
     unattributable has appeared since it was opened. Absence is not a refusal
     — a run with nothing to escalate still owes its pull request a report —
     but a file whose provenance cannot be established is never posted.

`attribution()` is the fourth part and the one a human reads: every comment the
fix loop posts names the repo, PR, card and verdict sha it is answering, so a
repeat of this fault is visible at a glance instead of two days later.

Tests: tests/test_fix_handoff.py (this module),
tests/test_fix_answers_its_own_pr.py (the workflow wiring, executed).

CLI (the seam agent-fix.yml uses):

    fix_handoff.py open  --base "$RUNNER_TEMP" --repo R --pr N --sha S \\
                         --legacy-dir /tmp          # key=value lines on stdout
    fix_handoff.py read  --kind blocker ...         # body; exit 3 absent, 4 foreign
    fix_handoff.py check ...                        # exit 4 on a foreign verdict
    fix_handoff.py attribution --card DRE-N ...     # one line, never fails
    fix_handoff.py stamp-verdict --verdict-file F ...
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from urllib.parse import quote, unquote

# What a read found. PRESENT and ABSENT are both healthy; MISMATCH is the
# refusal — something is there and it cannot be proved to be this run's.
PRESENT = "present"
ABSENT = "absent"
MISMATCH = "mismatch"

# The CLI's exit codes. 0 = a body on stdout; these two print nothing there,
# because stdout IS the body and a caller that ignores the code must not
# inherit somebody else's text.
EXIT_ABSENT = 3
EXIT_MISMATCH = 4

STAMP = "handoff.stamp"
VERDICT_STAMP = "verdict.stamp"
DIR_PREFIX = "fix-handoff-"

# The two escalations the fixing agent can write. Named here once: the open
# step clears both, the Report step reads both, and neither knows the spelling.
KINDS = ("blocker", "refutation")

# The machine-wide directory the fault travelled through. Cleared on open and
# never read as a handoff afterwards.
LEGACY_DIR = "/tmp"

# The one-line trailer every fix-loop comment carries. Deliberately NOT
# verdict-shaped (standards/untrusted-content.md): the merge gate reads verdicts
# off PR comments, so verdict-shaped text is an approval credential.
ATTRIBUTION_TAG = "🧷 answers:"

# Clock slack for the "did this appear after we opened?" comparison. Filesystem
# mtime granularity is coarser than time.time() on some of the fleet's volumes,
# and a stray file must not read as litter because of a rounding.
LITTER_GRACE = 5.0


# ── the key ────────────────────────────────────────────────────────────────


def key(repo: str, pr: str, sha: str) -> str:
    """What this run is for, as one string: repo, PR and head, all three.

    All three, because the live sighting crossed repositories: portico #501 and
    agent-bureau #501 are different pull requests, and the same PR re-dispatched
    on a new head is a different round with a different verdict.
    """
    return f"{repo}#{pr}@{(sha or '')[:8]}"


def handoff_dir(base: str, repo: str, pr: str, sha: str) -> str:
    """The keyed directory under ``base`` (the job's RUNNER_TEMP).

    Percent-encoded, so the slash in a repo slug is a character rather than a
    directory level — and so the key can be read back off a foreign directory's
    name, which is how a refusal manages to say WHOSE handoff it refused.
    """
    return os.path.join(base, DIR_PREFIX + quote(key(repo, pr, sha), safe=""))


def _key_of_dir(name: str) -> str:
    """The key a keyed directory's name encodes ('' if it is not one)."""
    if not name.startswith(DIR_PREFIX):
        return ""
    return unquote(name[len(DIR_PREFIX):])


def _kind_file(kind: str) -> str:
    return f"fix-{kind}.txt"


# ── stamps ─────────────────────────────────────────────────────────────────


def _read_kv(path: str) -> dict:
    out: dict = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if "=" in line:
                    name, value = line.rstrip("\n").split("=", 1)
                    out[name] = value
    except OSError:
        return {}
    return out


def _write_kv(path: str, fields: dict) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        for name, value in fields.items():
            fh.write(f"{name}={value}\n")
    return path


def read_stamp(directory: str) -> dict:
    """What the open step recorded about this handoff ({} if it never ran)."""
    return _read_kv(os.path.join(directory, STAMP))


def _opened_at(directory: str, stamp: dict) -> float:
    """When this handoff was opened. A stamp written by hand (or by an older
    pipeline) may carry no timestamp — fall back to the file's own mtime."""
    try:
        return float(stamp["opened"])
    except (KeyError, TypeError, ValueError):
        pass
    try:
        return os.path.getmtime(os.path.join(directory, STAMP))
    except OSError:
        return 0.0


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


# ── opening ────────────────────────────────────────────────────────────────


def open_handoff(base: str, repo: str, pr: str, sha: str,
                 legacy_dir: str = LEGACY_DIR) -> dict:
    """Clear what the last job left, then stamp this run's own directory.

    Returns the paths the workflow publishes as step outputs, plus `cleared` —
    every handoff file this removed, so the job log says what was there.
    """
    directory = handoff_dir(base, repo, pr, sha)
    cleared: list = []

    # Every keyed handoff under this base: another run's, and our own previous
    # attempt at the same key (a re-dispatch on the same head must not inherit
    # its own last blocker — the same fault with a shorter reach).
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            if not name.startswith(DIR_PREFIX) or not os.path.isdir(path):
                continue
            for kind in KINDS:
                candidate = os.path.join(path, _kind_file(kind))
                if os.path.isfile(candidate):
                    cleared.append(candidate)
            shutil.rmtree(path, ignore_errors=True)

    # THE live carrier: the fixed paths, on a machine that never clears them.
    for kind in KINDS:
        stale = os.path.join(legacy_dir, _kind_file(kind))
        if os.path.isfile(stale):
            cleared.append(stale)
            try:
                os.remove(stale)
            except OSError:
                pass

    os.makedirs(directory, exist_ok=True)
    _write_kv(os.path.join(directory, STAMP), {
        "key": key(repo, pr, sha),
        "repo": repo,
        "pr": pr,
        "head": sha,
        "legacy": legacy_dir,
        "opened": repr(time.time()),
    })
    paths = {
        "dir": directory,
        "key": key(repo, pr, sha),
        "stamp": os.path.join(directory, STAMP),
        "cleared": cleared,
    }
    for kind in KINDS:
        paths[kind] = os.path.join(directory, _kind_file(kind))
    return paths


# ── reading ────────────────────────────────────────────────────────────────


def _unattributable(base: str, name: str) -> list:
    """Handoff files under ``base`` when NOTHING stamped this run.

    Only the job's own temp directory is scanned, never the machine-wide one:
    with no stamp there is no opened-at to compare against, and a `/tmp` read
    here would be the original fault in a new spelling.
    """
    hits = []
    if os.path.isdir(base):
        for entry in sorted(os.listdir(base)):
            if not entry.startswith(DIR_PREFIX):
                continue
            candidate = os.path.join(base, entry, name)
            if os.path.isfile(candidate):
                hits.append((candidate, _key_of_dir(entry)))
    return hits


def _strays(base: str, legacy: str, name: str, ours: str, opened: float) -> list:
    """Handoff files that appeared AFTER this run opened its own.

    `/tmp` is shared machine-wide on the minis, so a file that turns up there
    mid-run may belong to any job on the box — its provenance cannot be
    established, and unattributable is refused. Litter that PREDATES this run
    (something the open step could not remove) is somebody else's rubbish, not
    a handoff of ours: ignored, never read, and never a refusal.
    """
    found = []
    legacy_path = os.path.join(legacy, name)
    if _appeared_since(legacy_path, opened):
        found.append((legacy_path, ""))
    if os.path.isdir(base):
        for entry in sorted(os.listdir(base)):
            path = os.path.join(base, entry)
            if not entry.startswith(DIR_PREFIX) or path == ours:
                continue
            candidate = os.path.join(path, name)
            if _appeared_since(candidate, opened):
                found.append((candidate, _key_of_dir(entry)))
    return found


def _appeared_since(path: str, opened: float) -> bool:
    try:
        return os.path.getmtime(path) >= opened - LITTER_GRACE
    except OSError:
        return False


def _foreign(path: str, owner: str, mine: str) -> str:
    """The reason line for a refusal. Names the path and, when the directory
    encodes one, whose run it belongs to — never one byte of the body itself
    (DRE-1996: a foreign body is attacker-writable text)."""
    whose = f" and belongs to {owner}" if owner else ""
    return (f"{path} appeared after this run opened its handoff{whose} — it "
            f"cannot be attributed to {mine}, so nothing is posted")


def read_handoff(base: str, repo: str, pr: str, sha: str, kind: str):
    """(status, body, reason) for one kind of handoff.

    PRESENT with a body only when the stamp proves this directory is this run's
    and nothing unattributable has appeared since. ABSENT is healthy — there
    was nothing to escalate. MISMATCH is the refusal, and its reason is safe to
    print.
    """
    mine = key(repo, pr, sha)
    directory = handoff_dir(base, repo, pr, sha)
    stamp = read_stamp(directory)
    name = _kind_file(kind)

    if not stamp:
        # No stamp: nothing on disk can be proved to be this run's. A run that
        # never reached the open step must still be able to report a push, so
        # an empty base is ABSENT — but a file sitting there is refused.
        hits = _unattributable(base, name)
        if hits:
            path, owner = hits[0]
            whose = f" (it belongs to {owner})" if owner else ""
            return MISMATCH, "", (
                f"no handoff was opened for this run, so {path}{whose} cannot "
                f"be attributed to {mine}"
            )
        return ABSENT, "", ""

    if stamp.get("key") != mine:
        return MISMATCH, "", (
            f"this directory is stamped for {stamp.get('repo', '?')}"
            f"#{stamp.get('pr', '?')} @{(stamp.get('head') or '')[:8]}, "
            f"not {mine}"
        )

    strays = _strays(base, stamp.get("legacy") or LEGACY_DIR, name, directory,
                     _opened_at(directory, stamp))
    if strays:
        path, owner = strays[0]
        return MISMATCH, "", _foreign(path, owner, mine)

    own = os.path.join(directory, name)
    if os.path.isfile(own):
        return PRESENT, _read_text(own), ""
    return ABSENT, "", ""


# ── the verdict this run is answering ──────────────────────────────────────


def _verdict_sha(body: str) -> str:
    match = re.search(r"@([0-9a-fA-F]{7,40})\b", body or "")
    return match.group(1) if match else ""


def stamp_verdict(base: str, repo: str, pr: str, sha: str, verdict_file: str,
                  _dir: str = "") -> str:
    """Record WHICH verdict this run fetched, and what it was fetched for.

    `_dir` exists for the tests that reproduce the cross-repo sighting at the
    verdict: this run's directory, holding another pull request's verdict.
    """
    directory = _dir or handoff_dir(base, repo, pr, sha)
    os.makedirs(directory, exist_ok=True)
    return _write_kv(os.path.join(directory, VERDICT_STAMP), {
        "key": key(repo, pr, sha),
        "repo": repo,
        "pr": pr,
        "head": sha,
        # Empty is a real answer: conflict mode and hand dispatches have no
        # critic verdict at all. That is an absence, and it is named as one.
        "verdict": _verdict_sha(_read_text(verdict_file)),
        "source": verdict_file,
    })


def verdict_sha(base: str, repo: str, pr: str, sha: str):
    """The sha of the verdict this run read, or None if there was none."""
    stamp = _read_kv(os.path.join(handoff_dir(base, repo, pr, sha), VERDICT_STAMP))
    return stamp.get("verdict") or None


def check_context(base: str, repo: str, pr: str, sha: str):
    """(ok, reason) — is everything in this handoff about THIS pull request?

    False only on a positive mismatch. A missing stamp is reported, not
    refused: a run whose verdict fetch never happened still owes the pull
    request its report, and the report says the verdict is unstamped rather
    than naming one it cannot prove.
    """
    mine = key(repo, pr, sha)
    directory = handoff_dir(base, repo, pr, sha)

    stamp = read_stamp(directory)
    if stamp and stamp.get("key") != mine:
        return False, (
            f"the handoff is stamped for {stamp.get('repo', '?')}"
            f"#{stamp.get('pr', '?')}, not {mine}"
        )

    verdict = _read_kv(os.path.join(directory, VERDICT_STAMP))
    if not verdict:
        return True, f"{mine}: the critic verdict is unstamped"
    if verdict.get("key") != mine:
        return False, (
            f"the critic verdict in this handoff is stamped for "
            f"{verdict.get('repo', '?')}#{verdict.get('pr', '?')} "
            f"@{(verdict.get('head') or '')[:8]}, not {mine}"
        )
    if not verdict.get("verdict"):
        return True, f"{mine}: no critic verdict this round"
    return True, f"{mine}: answering verdict @{verdict['verdict'][:8]}"


def attribution(base: str, repo: str, pr: str, sha: str, card: str) -> str:
    """The one line every comment the fix loop posts carries.

    Never fails and never dies on a missing stamp: it is appended to comments
    the loop MUST post, so a missing stamp degrades to naming less, never to
    losing the comment.
    """
    verdict = _read_kv(os.path.join(handoff_dir(base, repo, pr, sha), VERDICT_STAMP))
    if not verdict:
        answering = "unstamped"
    else:
        answering = (verdict.get("verdict") or "")[:8] or "none"
    return (f"{ATTRIBUTION_TAG} {repo}#{pr} · card {card or 'none'} · "
            f"verdict {answering}")


# ── CLI ────────────────────────────────────────────────────────────────────


def _context_args(parser) -> None:
    parser.add_argument("--base", required=True, help="the job's RUNNER_TEMP")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True)
    parser.add_argument("--sha", required=True, help="the head sha at dispatch")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    opener = sub.add_parser("open", help="clear stale handoffs and stamp this run's")
    _context_args(opener)
    opener.add_argument("--legacy-dir", default=LEGACY_DIR)

    reader = sub.add_parser("read", help="this run's handoff, or a refusal")
    _context_args(reader)
    reader.add_argument("--kind", choices=KINDS, required=True)

    checker = sub.add_parser("check", help="is this handoff about this PR?")
    _context_args(checker)

    liner = sub.add_parser("attribution", help="the one-line comment trailer")
    _context_args(liner)
    liner.add_argument("--card", default="")

    stamper = sub.add_parser("stamp-verdict", help="record the verdict read")
    _context_args(stamper)
    stamper.add_argument("--verdict-file", required=True)

    args = parser.parse_args(argv)

    if args.command == "open":
        paths = open_handoff(args.base, args.repo, args.pr, args.sha,
                             legacy_dir=args.legacy_dir)
        # stdout is $GITHUB_OUTPUT here — key=value lines and nothing else.
        for field in ("dir", "key", *KINDS):
            print(f"{field}={paths[field]}")
        print(f"cleared={len(paths['cleared'])}")
        for path in paths["cleared"]:
            sys.stderr.write(f"cleared a stale handoff: {path}\n")
        return 0

    if args.command == "read":
        status, body, reason = read_handoff(args.base, args.repo, args.pr,
                                            args.sha, args.kind)
        if status == PRESENT:
            sys.stdout.write(body if body.endswith("\n") else body + "\n")
            return 0
        if reason:
            sys.stderr.write(reason + "\n")
        return EXIT_ABSENT if status == ABSENT else EXIT_MISMATCH

    if args.command == "check":
        ok, reason = check_context(args.base, args.repo, args.pr, args.sha)
        sys.stderr.write(reason + "\n")
        return 0 if ok else EXIT_MISMATCH

    if args.command == "attribution":
        print(attribution(args.base, args.repo, args.pr, args.sha, args.card))
        return 0

    stamp_verdict(args.base, args.repo, args.pr, args.sha, args.verdict_file)
    sys.stderr.write(
        f"stamped {args.verdict_file} for {key(args.repo, args.pr, args.sha)}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
