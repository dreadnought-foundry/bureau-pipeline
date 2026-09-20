#!/usr/bin/env python3
"""What a credential LOOKS like — declared once (DRE-4268, stdlib only).

The pipeline had exactly one token pattern, private to `deliver_rescue.py`, and
the agent-log scrub needs the same question answered over a whole transcript.
Two lists would drift: a token shape added to one and not the other is a
credential quoted onto a card, or kept for six months in a log. So the shapes
live here, and both readers import them:

  * `deliver_rescue.redact`     — `SHAPES["github-token"]`, unchanged;
  * `scrub_agent_log` (pass 2)  — every entry.

This is the by-SHAPE net under the by-VALUE pass, not a replacement for it: it
catches a credential nobody handed the job (one an agent minted, read from a
file, or was echoed by a tool). Each pattern is anchored on a vendor's fixed
prefix or framing, so an ordinary sha, uuid or base64 blob never matches — the
scrub's counter-test pins that.

Adding a shape: add it here with a test in `tests/test_scrub_agent_log.py` that
plants one and asserts it is gone. Never widen a pattern until it matches
ordinary text — a scrub that mangles logs is one people switch off.
"""

from __future__ import annotations

import re

#: name → compiled pattern. The name is what the scrub's summary counts under
#: and what the redaction marker carries, so it is part of the output contract.
SHAPES: dict[str, re.Pattern[str]] = {
    # GitHub: classic/OAuth/user/server/refresh tokens and fine-grained PATs.
    # Byte-for-byte the pattern `deliver_rescue.py` has always used.
    "github-token": re.compile(
        r"\b(gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    # Anthropic: API keys (`sk-ant-api03-…`) and OAuth tokens (`sk-ant-oat01-…`,
    # `sk-ant-ort01-…`).
    "anthropic-key": re.compile(r"\bsk-ant-[A-Za-z0-9]{3,8}-[A-Za-z0-9_-]{20,}"),
    # AWS access key ids, long-lived and temporary.
    "aws-access-key-id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    # Linear personal API keys.
    "linear-key": re.compile(r"\blin_api_[A-Za-z0-9]{20,}\b"),
    # Slack tokens.
    "slack-token": re.compile(r"\bxox[abeprs]-[A-Za-z0-9-]{10,}"),
    # A JSON Web Token: three base64url parts, the first two each opening with
    # `eyJ` (`{"`). GitHub App JWTs and the runner's OIDC token have this shape.
    "jwt": re.compile(
        r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    # A PEM private key, framing included. DOTALL, and non-greedy to the first
    # END line: inside a JSON transcript the newlines are the two characters
    # `\n`, which `.` covers as readily as a real newline.
    "pem-private-key": re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL),
}
