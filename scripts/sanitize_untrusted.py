#!/usr/bin/env python3
"""Sanitize agent-influenced text before prompt interpolation (DRE-1996).

The DRE-1989 sentinel fence ("===== BEGIN/END UNTRUSTED CARD TEXT =====")
is a convention, not a mechanism: PR #59's render proved that a hostile card
body CONTAINING its own END-sentinel line visually escapes the fence and can
address the agent "outside" it. This script makes the fence mechanical:

  * Values travel via ENV VARS only — the workflow assigns the ${{ }}
    expression to env (GitHub sets env directly, no shell expansion), and
    this script reads os.environ. Nothing attacker-writable is ever
    shell-interpolated or passed on argv.
  * body mode — any line that mimics a sentinel (contains the phrase
    "UNTRUSTED CARD TEXT", case-insensitive, equals or not) is defanged
    with a visible "[defanged] " prefix. The line stays present verbatim
    after the prefix, so a reviewer can still see the attempt; per
    standards/untrusted-content.md a [defanged] line is itself the
    strongest signal the card is hostile. All other lines pass through
    byte-for-byte. CRLF is normalized to LF.
  * line mode (titles, branch names, escalation preambles) — all whitespace
    runs including newlines collapse to single spaces, so a multi-line value
    cannot inject prompt lines; the collapsed value is then defanged the
    same way.
  * Results append to $GITHUB_OUTPUT as heredoc blocks under a random
    collision-checked delimiter, so attacker content cannot terminate the
    block early or define extra outputs.

Usage (repeatable MODE ENV_VAR OUTPUT_NAME triples, one step per workflow):

    python3 sanitize_untrusted.py \
        line RAW_TITLE title \
        body RAW_DESCRIPTION description
"""

import os
import re
import sys
import uuid

# Broad on purpose: exact-match defanging is a bypass — extra equals signs,
# different casing, or a bare "END UNTRUSTED CARD TEXT" still READS as a
# fence boundary to a model. Any line carrying the phrase gets the prefix.
# False positives (prose discussing the fence) merely gain a visible,
# harmless prefix.
SENTINEL_RE = re.compile(r"UNTRUSTED\s+CARD\s+TEXT", re.IGNORECASE)

DEFANG_PREFIX = "[defanged] "

MODES = ("body", "line")


def _defang(line: str) -> str:
    return DEFANG_PREFIX + line if SENTINEL_RE.search(line) else line


def sanitize_body(text: str) -> str:
    """Multi-line card/epic body: defang sentinel-lookalike lines, keep
    everything else verbatim (the fence declares it data; auditability
    demands the original text survive)."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(_defang(line) for line in normalized.split("\n"))


def sanitize_line(text: str) -> str:
    """Single-line field (title, branch name, escalation preamble): collapse
    ALL whitespace runs — including newlines — to single spaces so the value
    cannot span or create prompt lines, then defang."""
    return _defang(" ".join(text.split()))


def _write_output(fh, name: str, value: str) -> None:
    """Append one GITHUB_OUTPUT heredoc block. The delimiter is random and
    re-drawn on the (astronomically unlikely) collision, so attacker content
    can never close the block or smuggle extra `name=value` outputs."""
    delim = f"EOF-{uuid.uuid4().hex}"
    while delim in value:
        delim = f"EOF-{uuid.uuid4().hex}"
    fh.write(f"{name}<<{delim}\n{value}\n{delim}\n")


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args or len(args) % 3 != 0:
        print(
            "usage: sanitize_untrusted.py (body|line) ENV_VAR OUTPUT_NAME "
            "[(body|line) ENV_VAR OUTPUT_NAME ...]",
            file=sys.stderr,
        )
        return 2

    out_path = os.environ.get("GITHUB_OUTPUT")
    if not out_path:
        print("GITHUB_OUTPUT is not set", file=sys.stderr)
        return 2

    with open(out_path, "a", encoding="utf-8") as fh:
        for i in range(0, len(args), 3):
            mode, env_var, out_name = args[i : i + 3]
            if mode not in MODES:
                print(f"unknown mode {mode!r} (want body|line)", file=sys.stderr)
                return 2
            raw = os.environ.get(env_var, "")
            value = sanitize_body(raw) if mode == "body" else sanitize_line(raw)
            _write_output(fh, out_name, value)
            defanged = value.count(DEFANG_PREFIX) - raw.count(DEFANG_PREFIX)
            # Log the COUNT only — echoing hostile content into run logs is
            # exactly the amplification this script exists to prevent.
            print(
                f"{out_name}: sanitized ({mode} mode, "
                f"{max(defanged, 0)} sentinel-lookalike line(s) defanged)"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
