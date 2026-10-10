#!/usr/bin/env python3
"""Third-party action pin check (DRE-3418).

2026-09-08 15:15–16:27 PT, Anthropic moved the floating `claude-code-action`
`v1` tag to v1.0.218, whose Claude Code installer leaves no launcher. Every
Claude-running job in the fleet died for 72 minutes (DRE-3416, upstream
anthropics/claude-code-action#1817). Nothing stood between the vendor's release
and production: the reusables here floated on major tags, and a floating major
gives Dependabot nothing to bump — a patch release moves the tag silently, so
the weekly sweep sees no change while production runs new vendor code.

A 40-char commit SHA turns every vendor release into a Dependabot PR that the
critic reviews and the harness proves before `stable` carries it — the same
discipline this repo already applies to its own code. This checker is what
stops a floating tag from coming back.

It FAILS (exit 1) when any third-party `uses:` reference is not

    uses: <owner>/<action>[/<subpath>]@<40-char lowercase-hex sha> # v<version>

Both halves are load-bearing. The SHA is the pin. The version comment is what
Dependabot READS to know which release the SHA is — a bare SHA is pinned but
frozen, and nothing proposes its successor. When Dependabot proposes the next
release it moves the sha always, and rewrites the comment only when nothing
follows the version: on #452 (2026-09-19) it moved 22 shas under
`# v1.0.217 — pinned DRE-3416: …` and left every comment naming the old
release, and this check, reading only that SOME version led the comment,
reported all clear.

## `--resolve`: the comment must name the release the sha IS (DRE-4337)

Offline, the checker can see only that a version is written down, not that it
is the right one. `--resolve` asks GitHub: for each pinned line it reads
`gh api repos/<owner>/<repo>/git/ref/tags/<version>`, follows an annotated tag
to its commit, and FAILS when that commit is not the pinned sha — whatever put
the halves out of step: a comment Dependabot would not rewrite, a hand edit, a
bump script that moved one half. One read per (repository, version), however
many lines pin it. Every pinned repository is public, so any token that can
read public refs will do — the read-only `github.token` of a
Dependabot-triggered run included.

A read that fails — no `gh`, no token, no network, a 404, a body that is not
the shape of a ref — leaves that line UNKNOWN: printed, never counted as a
pass. Exit codes: 0 every line agrees; 1 a line fails (offline, or its sha is
not its release); 2 nothing fails but some line is UNKNOWN. A tag the vendor
moves or deletes changes the answer to FAIL or UNKNOWN, never to a pass.
Nothing is cached between runs and nothing is written anywhere.

## What is scanned, and what is exempt

Scanned: `.github/workflows/*.yml` and the composite actions under
`.github/actions/*/action.yml`. The composite actions are included because
`actions/setup-python@v7` inside `setup-python-cached` floats exactly the way a
workflow-level reference does, and the same tag move reaches it — a guard that
stopped at the workflow directory would leave the hole this card exists to
close. (Whether Dependabot proposes bumps for nested composite actions is the
vendor's business; a stale pin is strictly safer than a moving tag either way.)

EXEMPT, by name: `dreadnought-foundry/*`. Those are OUR OWN reusable workflows
and actions riding OUR OWN release channel — `@main` on the canaries, `@vN` and
`@stable` for the fleet, each already gated by the integration harness and by
human release promotion (docs/self-hosting.md). Pinning them to a SHA would
defeat the channel, which is the mechanism this card is arguing for, not
against. Local `./…` path references are the repository's own files at the
checked-out sha and are not a supply-chain edge at all.

Deterministic, stdlib-only, line-based on purpose: YAML parsing discards the
`# v<version>` comment, and a violation has to be reported with the LINE the
operator must edit. Run from anywhere:

    python3 scripts/check_action_pins.py [--resolve] [dir_or_file ...]

tests/test_check_action_pins.py exercises it against synthetic workflows (one
per violation class) and against the LIVE tree, so a diff that reintroduces a
floating tag turns Pipeline Tests red.
"""

import json
import re
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATHS = (
    REPO_ROOT / ".github" / "workflows",
    REPO_ROOT / ".github" / "actions",
)

# Our own org — see the module docstring for why this exemption exists.
EXEMPT_OWNER = "dreadnought-foundry"

# A `uses:` STEP key, not the word in prose or in a comment: only whitespace
# and an optional list dash may precede it. `jobs.<id>.uses` (a reusable
# workflow call) matches the same shape.
USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<value>\S+)\s*(?P<comment>#.*)?$")

# A commit sha as GitHub writes one: 40 lowercase hex characters.
SHA_RE = re.compile(r"[0-9a-f]{40}")

# The version the SHA is, leading the comment. At least two numeric components:
# `# v7` is the floating tag written down and names no release, which is
# exactly the thing the pin replaces. Only the leading version is read, so
# prose after it passes here — but Dependabot rewrites the comment only when
# nothing follows the version (#452), so prose leaves a stale comment behind
# the sha it moves, and it is `--resolve` that catches the disagreement.
VERSION_COMMENT_RE = re.compile(r"#\s*(v\d+(?:\.\d+)+)")

Reference = namedtuple(
    "Reference", "path line uses action ref comment local exempt"
)


def parse_uses(line):
    """(action, ref, comment) for a `uses:` step line, or None.

    `action` is the owner/repo[/subpath] half, `ref` whatever follows `@`
    (empty for a local path reference), `comment` the trailing `# …` or None.
    """
    match = USES_RE.match(line)
    if not match:
        return None
    value = match.group("value").strip("\"'")
    action, _, ref = value.partition("@")
    return action, ref, match.group("comment")


def references(path):
    """Every `uses:` reference in one YAML file, in file order.

    Comment lines are dropped first: `tdd-commit-check/action.yml` documents a
    caller snippet inside a comment block, and a checker that flagged it would
    be unfixable without deleting the documentation.
    """
    found = []
    path = Path(path)
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        parsed = parse_uses(line)
        if parsed is None:
            continue
        action, ref, comment = parsed
        local = action.startswith(".")
        found.append(Reference(
            path=path,
            line=number,
            uses=f"{action}@{ref}" if ref else action,
            action=action,
            ref=ref,
            comment=comment,
            local=local,
            exempt=not local and action.split("/")[0] == EXEMPT_OWNER,
        ))
    return found


def check_file(path):
    """Violation strings for one file. Empty list == clean."""
    violations = []
    for ref in references(path):
        if ref.local or ref.exempt:
            continue
        where = f"{ref.path.name}:{ref.line}"
        if not SHA_RE.fullmatch(ref.ref):
            owner_repo = "/".join(ref.action.split("/")[:2])
            violations.append(
                f"{where}: `uses: {ref.uses}` floats — pin it to the 40-char "
                f"commit sha of the release the tag resolves to, with "
                f"`# v<version>` after it (gh api repos/{owner_repo}"
                f"/git/ref/tags/{ref.ref or '<tag>'})"
            )
        elif not VERSION_COMMENT_RE.match(ref.comment or ""):
            violations.append(
                f"{where}: `uses: {ref.uses}` is pinned but carries no "
                f"`# v<version>` version comment — Dependabot reads that "
                f"comment to know what to bump from, so without it the pin "
                f"is frozen rather than proposed"
            )
    return violations


def iter_files(paths):
    """The YAML files under each given path, deduplicated and sorted."""
    seen = []
    for path in paths:
        path = Path(path)
        if path.is_dir():
            candidates = sorted(path.glob("*.yml")) + sorted(path.glob("*.yaml"))
            candidates += sorted(path.glob("*/action.yml"))
            candidates += sorted(path.glob("*/action.yaml"))
        elif path.exists():
            candidates = [path]
        else:
            candidates = []
        for candidate in candidates:
            if candidate not in seen:
                seen.append(candidate)
    return sorted(seen)


def check_paths(paths):
    """Every violation across every YAML file under `paths`."""
    violations = []
    for path in iter_files(paths):
        violations.extend(check_file(path))
    return violations


def scan(paths):
    """(violations, stats). stats guards against a silently vacuous run: a pin
    checker that inspected nothing must not report green."""
    files = iter_files(paths)
    stats = {"files": len(files), "third_party": 0, "exempt": 0, "local": 0}
    violations = []
    for path in files:
        for ref in references(path):
            if ref.local:
                stats["local"] += 1
            elif ref.exempt:
                stats["exempt"] += 1
            else:
                stats["third_party"] += 1
        violations.extend(check_file(path))
    return violations, stats


class Unresolved(Exception):
    """A tag read that produced no answer. Its message is the reason, printed
    on the UNKNOWN line."""


#: An annotated tag names a tag object that names the commit; a tag of a tag
#: adds a hop. Real releases take one. Past this, the chain is a loop.
MAX_TAG_HOPS = 4

#: Seconds one `gh api` read may take before it is UNKNOWN rather than a hang.
READ_TIMEOUT = 60


def gh_read(path):
    """`gh api <path>` as parsed JSON. Every way it can fail is `Unresolved`."""
    try:
        done = subprocess.run(
            ["gh", "api", path], capture_output=True, text=True,
            timeout=READ_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Unresolved(f"gh api {path}: {error}") from error
    if done.returncode != 0:
        detail = " ".join(done.stderr.split()) or f"exited {done.returncode}"
        raise Unresolved(f"gh api {path}: {detail}")
    try:
        return json.loads(done.stdout)
    except ValueError as error:
        raise Unresolved(f"gh api {path}: body is not JSON ({error})") from error


def _object(body, what):
    """The `object` of a ref or tag body as (type, sha), or Unresolved."""
    obj = body.get("object") if isinstance(body, dict) else None
    if not isinstance(obj, dict):
        raise Unresolved(f"{what}: no object in the response")
    kind, sha = obj.get("type"), obj.get("sha")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise Unresolved(f"{what}: {sha!r} is not a commit sha")
    return kind, sha


def resolve_tag(owner_repo, tag, read=gh_read):
    """The commit `owner_repo`'s tag `tag` names, following annotated tags.
    Raises Unresolved for anything that is not exactly that."""
    what = f"{owner_repo} tag {tag}"
    body = read(f"repos/{owner_repo}/git/ref/tags/{tag}")
    if not isinstance(body, dict) or body.get("ref") != f"refs/tags/{tag}":
        raise Unresolved(f"{what}: the response is not that tag's ref")
    kind, sha = _object(body, what)
    for _ in range(MAX_TAG_HOPS):
        if kind != "tag":
            break
        kind, sha = _object(read(f"repos/{owner_repo}/git/tags/{sha}"), what)
    if kind != "commit":
        raise Unresolved(f"{what}: resolves to a {kind!r}, not a commit")
    return sha


def resolve_paths(paths, read=gh_read):
    """(fails, unknowns, agreed) for every pinned line under `paths`.

    Only lines that pass offline are resolved — a floating tag or a missing
    comment is already a FAIL with its own message and has no version to ask
    about. One read per (repository, version)."""
    answers = {}
    fails, unknowns, agreed = [], [], 0
    for path in iter_files(paths):
        for ref in references(path):
            if ref.local or ref.exempt or not SHA_RE.fullmatch(ref.ref):
                continue
            version = VERSION_COMMENT_RE.match(ref.comment or "")
            if not version:
                continue
            owner_repo = "/".join(ref.action.split("/")[:2])
            key = (owner_repo, version.group(1))
            if key not in answers:
                try:
                    answers[key] = (resolve_tag(*key, read=read), None)
                except Unresolved as error:
                    answers[key] = (None, str(error))
            commit, reason = answers[key]
            where = f"{ref.path.name}:{ref.line}"
            if commit is None:
                unknowns.append(
                    f"{where}: `uses: {ref.uses}` — could not read which "
                    f"commit {key[1]} is ({reason}); not counted as a pass"
                )
            elif commit != ref.ref:
                fails.append(
                    f"{where}: `uses: {ref.uses}` says `# {key[1]}`, but "
                    f"{owner_repo} {key[1]} is commit {commit}, not {ref.ref} "
                    f"— the comment names a release the sha is not (DRE-4337); "
                    f"move the sha and the comment together"
                )
            else:
                agreed += 1
    return fails, unknowns, agreed


def main(argv, read=gh_read):
    args = argv[1:]
    online = "--resolve" in args
    paths = [Path(a) for a in args if a != "--resolve"] or list(DEFAULT_PATHS)
    violations, stats = scan(paths)
    print(
        f"checked {stats['files']} files: {stats['third_party']} third-party "
        f"action references, {stats['exempt']} {EXEMPT_OWNER}/* (exempt — our "
        f"own release channel), {stats['local']} local paths"
    )
    if stats["third_party"] == 0:
        print("ERROR: found no third-party action references — wrong "
              "directory, or the checker went vacuous")
        return 1
    if violations:
        for violation in violations:
            print(f"FAIL {violation}")
        print(f"\n{len(violations)} floating or uncommented action "
              f"reference(s): a vendor release would reach production without "
              f"a PR, a critic or a harness run (DRE-3416)")
    if not online:
        if violations:
            return 1
        print("ok: every third-party action is pinned to a commit sha with its "
              "version in a comment")
        return 0
    fails, unknowns, agreed = resolve_paths(paths, read=read)
    for fail in fails:
        print(f"FAIL {fail}")
    for unknown in unknowns:
        print(f"UNKNOWN {unknown}")
    print(f"resolved {agreed + len(fails) + len(unknowns)} pinned "
          f"reference(s): {agreed} agree, {len(fails)} name a release the sha "
          f"is not, {len(unknowns)} unknown")
    if violations or fails:
        return 1
    if unknowns:
        print(f"\nnot green: {len(unknowns)} version comment(s) could not be "
              f"checked against GitHub, and an unchecked comment is never a pass")
        return 2
    print("ok: every third-party action is pinned to a commit sha, and every "
          "version comment names the release that sha is")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
