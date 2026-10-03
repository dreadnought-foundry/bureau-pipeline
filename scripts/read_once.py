#!/usr/bin/env python3
"""One GitHub read per record per job (Stage 2 fix #21).

The critic (`qa-review.yml`) read the same pull request again and again within
one run: its head commit, base branch, branch name and body in five separate
`gh pr view` calls across five steps, the compare record and the comment
thread twice each, the changed-file list twice. The fix agent read its base
branch, its head's check runs and its comment thread twice each. Every one of
those is the same answer read again seconds later, on a bucket the whole
fleet's reviews share. This is the seam that reads each record once.

    read_once.py once <key> [--out FILE] -- <command ...>
    read_once.py pr <number> --fields a,b,c [--field F | --pick a,b | --read-at]

`once` runs the command the first time a job asks for `<key>` and keeps its
stdout; every later ask in the same job gets that stdout back, byte for byte,
with no call. The KEY names the data, so it carries everything the answer
depends on (`compare-$BASE...$SHA`, `check-runs-$HEAD_SHA`): a different sha is
a different read, never a stale one.

`pr` reads the union of the record's fields a job needs, ONE `gh pr view`, and
hands each step its field the way `gh pr view --json F --jq .F` printed it (a
string raw, with its newline); `--pick` writes the subset a reader's file
expects; `--read-at` is when the record was read — the moment a step that
quotes the body actually read it (DRE-3005 rule 3).

## What is never changed

* A FAILED read is never kept. The caller sees the command's own exit code,
  stdout and stderr, exactly as it did calling the command itself, and the next
  step that asks reads again. So a step whose read was soft (`|| true`) stays
  soft, a step that failed loud on a failed read still does, and a blip in one
  step is retried by the next — as it was when each step read on its own.
* A record missing a field it was asked for is a failed read (and not kept).
* The cache lives in `$RUNNER_TEMP/read-once`, which the runner empties at the
  start and end of every job: one job's answer is never another's, even on a
  self-hosted machine. With no `RUNNER_TEMP` (outside a job) there is no
  cache, and every call reads.

## What is deliberately NOT read once

A read whose ANSWER the job itself changes, or that must see the record as it
is at that moment: the fix agent's Resolve step (the thread it decides the
halt and the mode on), the verdict it answers (read after minutes of setup),
the hold and notice threads it posts into, and everything after an agent has
run and pushed. Those keep their own reads.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _cache_dir() -> Path | None:
    base = os.environ.get("RUNNER_TEMP")
    return Path(base) / "read-once" if base else None


def _paths(key: str) -> tuple[Path, Path] | None:
    root = _cache_dir()
    if root is None:
        return None
    name = hashlib.sha256(key.encode()).hexdigest()[:32]
    return root / f"{name}.out", root / f"{name}.at"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _cached(key: str) -> tuple[bytes, str] | None:
    paths = _paths(key)
    if paths is None:
        return None
    out, at = paths
    try:
        return out.read_bytes(), at.read_text().strip()
    except OSError:
        return None


def _keep(key: str, data: bytes, read_at: str) -> None:
    paths = _paths(key)
    if paths is None:
        return
    out, at = paths
    out.parent.mkdir(parents=True, exist_ok=True)
    for path, payload in ((out, data), (at, read_at.encode())):
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(payload)
        tmp.replace(path)


def _run(command: list[str]) -> subprocess.CompletedProcess:
    # stderr passes straight through: the caller's own `2>/dev/null` (or not)
    # decides what a failed read says, as it did with the command itself.
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=None)


def read(key: str, command: list[str], *, valid=None) -> tuple[int, bytes, str]:
    """`(exit code, stdout, read_at)` — from this job's earlier read of `key`
    when there was one, else from running `command` (kept only on success, and
    only when `valid(stdout)` agrees)."""
    hit = _cached(key)
    if hit is not None:
        return 0, hit[0], hit[1]
    read_at = _now()
    proc = _run(command)
    if proc.returncode == 0 and (valid is None or valid(proc.stdout)):
        _keep(key, proc.stdout, read_at)
        return 0, proc.stdout, read_at
    return (proc.returncode or 1), proc.stdout, read_at


def _record_validator(fields: list[str]):
    def valid(data: bytes) -> bool:
        try:
            record = json.loads(data.decode())
        except ValueError:
            return False
        return isinstance(record, dict) and all(f in record for f in fields)
    return valid


def _as_jq_raw(value) -> str:
    """What `--jq .F` prints: a string raw, anything else as JSON."""
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _write(data: bytes, out: str | None) -> None:
    if out:
        tmp = Path(out + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(out)
    else:
        sys.stdout.buffer.write(data)
        sys.stdout.flush()


USAGE = __doc__.split("\n\n")[1]


def _once(argv: list[str]) -> int:
    if "--" not in argv:
        print(USAGE, file=sys.stderr)
        return 2
    head, command = argv[:argv.index("--")], argv[argv.index("--") + 1:]
    out = None
    if len(head) == 3 and head[1] == "--out":
        out = head[2]
    elif len(head) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    if not command:
        print(USAGE, file=sys.stderr)
        return 2
    code, data, _ = read(head[0], command)
    if code == 0:
        _write(data, out)
    elif not out:
        _write(data, None)  # a failed read's stdout reaches the caller as before
    return code


def _pr(argv: list[str]) -> int:
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    number, opts = argv[0], argv[1:]

    def opt(name: str) -> str | None:
        if name in opts:
            i = opts.index(name)
            return opts[i + 1] if i + 1 < len(opts) else None
        return None

    fields_raw = opt("--fields")
    if not fields_raw:
        print("read_once.py pr: --fields is required", file=sys.stderr)
        return 2
    fields = [f for f in fields_raw.split(",") if f]
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    command = ["gh", "pr", "view", number, *(["--repo", repo] if repo else []),
               "--json", ",".join(fields)]
    key = f"pr|{repo}|{number}|{','.join(fields)}"
    code, data, read_at = read(key, command, valid=_record_validator(fields))
    if code != 0:
        return code
    if "--read-at" in opts:
        print(read_at)
        return 0
    record = json.loads(data.decode())
    field = opt("--field")
    if field is not None:
        if field not in record:
            print(f"read_once.py pr: {field!r} is not one of --fields", file=sys.stderr)
            return 2
        sys.stdout.write(_as_jq_raw(record[field]) + "\n")
        return 0
    pick = opt("--pick")
    if pick is not None:
        wanted = [f for f in pick.split(",") if f]
        missing = [f for f in wanted if f not in record]
        if missing:
            print(f"read_once.py pr: {missing} are not in --fields", file=sys.stderr)
            return 2
        sys.stdout.write(json.dumps({f: record[f] for f in wanted}) + "\n")
        return 0
    sys.stdout.write(json.dumps(record) + "\n")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "once":
        return _once(argv[1:])
    if argv and argv[0] == "pr":
        return _pr(argv[1:])
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
