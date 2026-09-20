#!/usr/bin/env python3
"""Scrub an agent's working log before it leaves the runner (DRE-4268, stdlib only).

Epic DRE-4267 keeps every agent run's transcript for six months in our own
storage. Several of the repos those runs happen in are public, and a transcript
is a record of everything a tool printed — so this script is the one piece
where a mistake leaks a credential. It is a script with its own tests, built
before the workflow step that calls it (DRE-4269), because that step's whole
contract is: **no successful scrub, no upload.**

    printf '%s' "$SECRETS_JSON" | python3 scrub_agent_log.py \\
        --out-dir "$RUNNER_TEMP/agent-log" claude-execution-output.json step.log

INPUT
  * the files to scrub, as arguments;
  * on STDIN, a JSON object `{"NAME": "value", …}` of every secret the job was
    given. Never as arguments or a flag — arguments reach the process table,
    which any process on a shared self-hosted runner can read. There is
    deliberately no option that accepts a value.

PASS 1 — BY VALUE. Every supplied value is replaced with `«redacted:NAME»`, in
each form a tool can print it, because the raw value is often the form that does
NOT appear:
  * raw, and inside a URL's userinfo (`https://x-access-token:VALUE@github…`
    contains the raw value, so the raw replacement covers it);
  * JSON-escaped — the transcript is JSON, so a value holding a newline, a
    quote or a backslash appears there in its escaped spelling;
  * URL-encoded (`quote` and `quote_plus`, each with upper- AND lower-case
    percent escapes — `%2F` and `%2f`);
  * base64, standard and URL-safe, **at all three byte alignments**. `git`
    sends `base64("x-access-token:" + TOKEN)`, which contains no substring equal
    to `base64(TOKEN)` unless the prefix length happens to be a multiple of
    three. For each alignment the characters that depend only on the value's own
    bytes are computed and replaced, so the value cannot be recovered from what
    remains whatever preceded or followed it.
Longest form first, so one value that contains another is not half-replaced.
NOT covered, deliberately: hex and other ad-hoc encodings of a value. No tool in
the pipeline prints a credential that way, every added form is another
replacement pass over a large file, and pass 2 remains underneath. Considered
and left out (DRE-4268 review), not overlooked.

THE MINIMUM-LENGTH RULE. A by-value replacement needs `MIN_VALUE_LENGTH` (8)
characters. A variable set to `true`, or a secret that is the word `main`, would
otherwise redact an ordinary word everywhere and make the log unreadable — and a
scrub that ruins logs gets switched off. A shorter value is SKIPPED and reported
in the summary by NAME (a name is not a secret). Pass 2 still applies to it.

PASS 2 — BY SHAPE. Every pattern in `secret_shapes.SHAPES` — the pipeline's one
declaration of what a credential looks like, shared with `deliver_rescue` —
replaced with `«redacted-shape:NAME»`. This is the net under pass 1: a
credential nobody handed the job.

REFUSAL — SILENCE IS NEVER SUCCESS. A file that is missing, is not valid UTF-8,
or is larger than `--max-bytes` makes the whole run exit non-zero and write
NOTHING, for any file. So does malformed stdin. And after scrubbing, the output
is checked again for every form of every value and every shape; anything still
found is a refusal too. The caller uploads only on exit 0.

OUTPUT. Each scrubbed file under `--out-dir`, by its own basename, written only
once every file has been scrubbed and verified. One summary line of JSON on
stdout — counts per secret NAME, counts per shape, the names skipped as too
short. It never contains any part of a value, and nothing else this script
prints does either: refusals name the file and the reason, never the content.

Exit 0 scrubbed and written · 2 refused (nothing written).
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import urllib.parse
from pathlib import Path

from secret_shapes import SHAPES

#: See "THE MINIMUM-LENGTH RULE" above.
MIN_VALUE_LENGTH = 8

#: The default cap. A transcript is a few MB; a turn-capped one with large tool
#: outputs is tens. Past this the file is not read into memory and not uploaded.
DEFAULT_MAX_BYTES = 64 * 1024 * 1024

#: A base64 core shorter than this is too ordinary a string to replace safely
#: (the same reasoning as MIN_VALUE_LENGTH, in base64's 4-for-3 terms).
_MIN_CORE_LENGTH = 10

REFUSED = 2


class Refused(Exception):
    """The run must write nothing. The message names a file and a reason and
    never quotes content."""


def _base64_cores(value: bytes) -> set[str]:
    """For each of the three byte alignments, the base64 characters that depend
    ONLY on `value`'s bytes — what must appear in any base64 text whose input
    contained `value` at that alignment, whatever surrounded it."""
    cores: set[str] = set()
    for encode in (base64.b64encode, base64.urlsafe_b64encode):
        for offset in range(3):
            encoded = encode(b"\0" * offset + value).decode().rstrip("=")
            # Characters before `start` mix in the padding bytes; the last
            # character(s) mix in whatever FOLLOWS the value in a longer input.
            start = (offset * 8 + 5) // 6
            tail_bits = ((offset + len(value)) * 8) % 6
            end = len(encoded) - (1 if tail_bits else 0)
            core = encoded[start:end]
            if len(core) >= _MIN_CORE_LENGTH:
                cores.add(core)
    return cores


_PERCENT_ESCAPE = re.compile(r"%[0-9A-F]{2}")
_SECRET_NAME = re.compile(r"[A-Za-z0-9_]+")


def _lower_percent_escapes(quoted: str) -> str:
    """`%2F` → `%2f`, leaving every other character of the value alone."""
    return _PERCENT_ESCAPE.sub(lambda m: m.group(0).lower(), quoted)


def forms(value: str) -> list[str]:
    """Every spelling of `value` a log can carry, longest first."""
    found = {value}
    for ensure_ascii in (True, False):
        found.add(json.dumps(value, ensure_ascii=ensure_ascii)[1:-1])
    for quoted in (urllib.parse.quote(value, safe=""), urllib.parse.quote_plus(value)):
        # `quote` writes `%2F`; plenty of tools write `%2f`. Both spellings.
        found.add(quoted)
        found.add(_lower_percent_escapes(quoted))
    found |= _base64_cores(value.encode())
    return sorted((f for f in found if f), key=len, reverse=True)


def read_secrets(stream) -> dict[str, str]:
    try:
        parsed = json.loads(stream.read() or "{}")
    except ValueError as bad:
        raise Refused("stdin is not JSON — expected an object of NAME: value") from bad
    if not isinstance(parsed, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()):
        raise Refused("stdin is not a JSON object of string values")
    # The NAME is the one piece of caller-supplied text that reaches the output
    # (inside the marker). A quote or a newline in it would break the JSON of a
    # scrubbed transcript, so a name is an identifier or the run is refused.
    if not all(_SECRET_NAME.fullmatch(name) for name in parsed):
        raise Refused("a secret NAME on stdin is not an identifier ([A-Za-z0-9_]+)")
    return parsed


def read_file(path: Path, max_bytes: int) -> str:
    try:
        size = path.stat().st_size
    except OSError as missing:
        raise Refused(f"{path.name}: cannot be read ({type(missing).__name__})") from missing
    if size > max_bytes:
        raise Refused(f"{path.name}: {size} bytes is over the {max_bytes}-byte cap")
    try:
        raw = path.read_bytes()
    except OSError as unreadable:
        # It stats but will not open: a directory, or — the real case on a
        # self-hosted runner — a log a container wrote as another uid. Still a
        # refusal, by name and reason, never a traceback carrying the full path.
        raise Refused(
            f"{path.name}: cannot be read ({type(unreadable).__name__})") from unreadable
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as bad:
        raise Refused(f"{path.name}: is not valid UTF-8, so it cannot be scanned") from bad


def scrub(text: str, plan: list[tuple[str, list[str]]],
          by_value: dict[str, int], by_shape: dict[str, int]) -> str:
    for name, spellings in plan:
        marker = f"«redacted:{name}»"
        for spelling in spellings:
            count = text.count(spelling)
            if count:
                by_value[name] = by_value.get(name, 0) + count
                text = text.replace(spelling, marker)
    for shape, pattern in SHAPES.items():
        text, count = pattern.subn(f"«redacted-shape:{shape}»", text)
        if count:
            by_shape[shape] = by_shape.get(shape, 0) + count
    return text


def verify(name: str, text: str, plan: list[tuple[str, list[str]]]) -> None:
    """The scrubbed text, checked as if it were a stranger's. A replacement can
    in principle splice two fragments into a new match; this is what makes that
    a refusal rather than a leak."""
    for secret, spellings in plan:
        if any(spelling in text for spelling in spellings):
            raise Refused(f"{name}: a form of {secret} survived the scrub")
    for shape, pattern in SHAPES.items():
        if pattern.search(text):
            raise Refused(f"{name}: a {shape} shape survived the scrub")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrub agent logs; secret values are read from stdin as a JSON object.")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("files", nargs="+", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        secrets = read_secrets(sys.stdin)
        skipped = sorted(n for n, v in secrets.items() if len(v) < MIN_VALUE_LENGTH)
        # Longest VALUE first across secrets too: a token that contains another
        # secret as a substring must be replaced whole.
        plan = [(name, forms(value)) for name, value in sorted(
            secrets.items(), key=lambda item: len(item[1]), reverse=True)
            if len(value) >= MIN_VALUE_LENGTH]
        names = [path.name for path in args.files]
        if len(set(names)) != len(names):
            raise Refused("two input files share a basename; they would overwrite each other")

        by_value: dict[str, int] = {}
        by_shape: dict[str, int] = {}
        scrubbed: dict[str, str] = {}
        for path in args.files:
            text = scrub(read_file(path, args.max_bytes), plan, by_value, by_shape)
            verify(path.name, text, plan)
            scrubbed[path.name] = text
    except Refused as refusal:
        print(f"scrub_agent_log: REFUSED, nothing written — {refusal}", file=sys.stderr)
        return REFUSED

    # Only now, with every file scrubbed and verified, does anything reach disk.
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in scrubbed.items():
        (args.out_dir / name).write_bytes(text.encode("utf-8"))
    print(json.dumps({"files": len(scrubbed), "by_value": by_value,
                      "by_shape": by_shape, "skipped_short": skipped}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
