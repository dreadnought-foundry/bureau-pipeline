#!/usr/bin/env python3
"""What a credential LOOKS like — declared once (DRE-4268, stdlib only).

The pipeline had exactly one token pattern, private to `deliver_rescue.py`, and
the agent-log scrub needs the same question answered over a whole transcript.
Two lists would drift: a token shape added to one and not the other is a
credential quoted onto a card, or kept for six months in a log. So the shapes
live here, and both readers import them:

  * `deliver_rescue.redact`     — `SHAPES["github-token"]`;
  * `scrub_agent_log` (pass 2)  — every entry.

This is the by-SHAPE net under the by-VALUE pass, not a replacement for it: it
catches a credential nobody handed the job (one an agent minted, read from a
file, or was echoed by a tool). Each pattern is anchored on a vendor's fixed
prefix or framing, so an ordinary sha, uuid or base64 blob never matches — the
scrub's counter-test pins that.

Adding a shape: add it here with a test in `tests/test_scrub_agent_log.py` that
plants one and asserts it is gone — raw, AND at the start of a line inside a
JSON transcript, which is where `_LEAD` below earns its keep. Never widen a
pattern until it matches ordinary text — a scrub that mangles logs is one people
switch off.
"""

from __future__ import annotations

import re

#: Where a token may START. Not `\b`, and the difference is a leak. Inside a JSON
#: transcript a token at the start of a line is preceded by the two characters
#: `\n`, and `n` is a word character — so `\bghp_…` does not match it, at exactly
#: the position a tool prints a credential (`export TOKEN=` on one line, the
#: value on the next). Measured before this existed: the same token matched raw
#: and was MISSED JSON-escaped and double-escaped. So a token starts after
#: anything that is not a letter or digit, OR after an escaped newline, carriage
#: return or tab — however many backslashes deep, since the lookbehind needs only
#: the last one.
_LEAD = r"(?:(?<![A-Za-z0-9])|(?<=\\[nrt]))"

#: A line break: a real one, or `\n` / `\r` as a JSON transcript spells it — with
#: ANY number of backslashes, because a key printed from inside a JSON file
#: (`cat app-key.json`) arrives escaped twice.
_NL = r"(?:\r?\n|(?:\\+r)?\\+n|\\+r)"

#: name → compiled pattern. The name is what the scrub's summary counts under
#: and what the redaction marker carries, so it is part of the output contract.
SHAPES: dict[str, re.Pattern[str]] = {
    # GitHub: classic/OAuth/user/server/refresh tokens and fine-grained PATs. The
    # token grammar `deliver_rescue.py` has always used, behind `_LEAD`.
    "github-token": re.compile(
        _LEAD + r"(gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    # Anthropic: API keys (`sk-ant-api03-…`) and OAuth tokens (`sk-ant-oat01-…`,
    # `sk-ant-ort01-…`).
    "anthropic-key": re.compile(_LEAD + r"sk-ant-[A-Za-z0-9]{3,8}-[A-Za-z0-9_-]{20,}"),
    # AWS access key ids, long-lived and temporary.
    "aws-access-key-id": re.compile(_LEAD + r"(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    # Linear personal API keys.
    "linear-key": re.compile(_LEAD + r"lin_api_[A-Za-z0-9]{20,}\b"),
    # Slack tokens.
    "slack-token": re.compile(_LEAD + r"xox[abeprs]-[A-Za-z0-9-]{10,}"),
    # A JSON Web Token: three base64url parts, the first two each opening with
    # `eyJ` (`{"`). GitHub App JWTs and the runner's OIDC token have this shape.
    "jwt": re.compile(
        _LEAD + r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    # A PEM private key — LINE by LINE, never "everything up to an END".
    #
    # Two earlier spellings each destroyed log text and reported success
    # (DRE-4268 review): `BEGIN.*?END` under DOTALL swallows the whole log
    # between an unterminated key and a LATER key's END line; and a body class
    # that admitted whitespace (`[A-Za-z0-9+/=\s]*`) is letters, digits and
    # spaces — which is most prose — so it ate every line after a clipped key
    # until it met punctuation. A key whose END line is missing is the COMMON
    # case, because a tool's output is clipped long before a log is.
    #
    # So the pattern takes only what a key is made of:
    #   the BEGIN line;
    #   any RFC 1421 header lines (`Proc-Type: 4,ENCRYPTED`, `DEK-Info: …`) and
    #     the blank line after them;
    #   every following line that OPENS with 16+ base64 characters — no space can
    #     be part of one, so prose stops it. Matched from the line's start
    #     without requiring the line to end there: a body line with
    #     `…[truncated]` appended still loses its key material;
    #   and, only when an END line follows, the short last body line and the END
    #     line itself.
    # What this gives up: a final body line shorter than 16 characters survives
    # when the END line was clipped (under 12 bytes of a 1,200-byte key), and an
    # unbroken 16+ character alphanumeric line sitting directly under a clipped
    # key is taken as key material, which it cannot be told apart from.
    "pem-private-key": re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
        rf"(?:{_NL}[A-Za-z][A-Za-z0-9-]*: [^\r\n\\\"]*)*"
        rf"(?:{_NL}(?={_NL}))?"
        rf"(?:{_NL}[A-Za-z0-9+/=]{{16,}})*"
        rf"(?:(?:{_NL}[A-Za-z0-9+/=]{{1,15}})?{_NL}-----END [A-Z ]*PRIVATE KEY-----)?"),
}
