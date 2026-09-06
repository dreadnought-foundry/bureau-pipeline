#!/usr/bin/env python3
"""Read the node version a CONSUMER repo's web project declares (DRE-3248).

qa-review.yml's visual stage installs and runs the consumer's screenshot
harness in `console/web`. That project declares which node it needs; this
pipeline must not carry a second copy of the number.

Before this existed the render step had no `setup-node` at all, so `npm ci`
ran on whatever the hosted runner shipped. When the runner default fell to
v22 under a project requiring `>=24 <25`, every render died at EBADENGINE,
the stage degraded, and the design-fidelity verdict became a skip wearing
green — One River's Phase 2 proof (DRE-3066) watched five deliberately-red
screens go uncompared.

Where the declaration is read from, in order:

  1. `<web>/.nvmrc` — handed to `actions/setup-node` as a **version file**,
     so the value is never copied out of it. That is the shared node
     action's own documented preference (DRE-2550): a file cannot drift
     from the runtime the way a second copy of the number can.
  2. `engines.node` in `<web>/package.json` — a RANGE (`>=24 <25`, `^22`),
     so it is resolved to its major, which is what setup-node pins.

Neither present, unreadable, or declaring nothing parseable → this DECLINES
(exit 3). It never guesses a version: a wrong guess renders on the wrong
node and calls it a comparison.

CLI:

    python3 consumer_node.py [console/web]

Prints `key=value` lines for `$GITHUB_OUTPUT`, always, on both paths:

    setup=true|false          may the node setup step run at all
    version=24                the declared version, for the log
    source=console/web/.nvmrc where it was read from, for the log
    node-version=             literal for actions/setup-node (empty when a
    node-version-file=…       version file is used, and vice versa)

Exit 0 when a version was found, 3 when the project declares none. The
reason goes to stderr — the caller logs it, and it reaches the verdict as
the degraded note.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DEFAULT_WEB_DIR = "console/web"
NVMRC = ".nvmrc"
PACKAGE_JSON = "package.json"

# Everything printed is derived from files in the CONSUMER's repository, i.e.
# from text this pipeline does not control, and it is written into
# $GITHUB_OUTPUT and then read back by later steps. A newline is a second
# output line of the consumer's choosing; a quote, a `$` or a backtick is an
# expansion in whatever shell line ends up carrying the value. So every value
# goes through _one_line, which keeps only the characters a version range
# needs and drops the rest.
_SAFE = re.compile(r"[^A-Za-z0-9 ._/*<>=^~+,:-]")
_MAX_VALUE = 60

# The first integer in an engines range: ">=24 <25" → 24, "^22.11.0" → 22.
_MAJOR = re.compile(r"(\d+)")


def _one_line(value: str, limit: int = _MAX_VALUE) -> str:
    """A single printable line, short enough to read in a log."""
    collapsed = " ".join(_SAFE.sub(" ", value).split())
    return collapsed[:limit].strip()


def read_nvmrc(web: Path) -> str:
    """The `.nvmrc` version as written, or "" when there is nothing usable.

    Returned as-is (minus a leading `v`): setup-node reads the file itself,
    so anything it accepts — `24`, `v24.3.0`, `lts/*` — stays valid.
    """
    try:
        raw = (web / NVMRC).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""
    return _one_line(raw.strip().lstrip("vV"), limit=32)


def read_engines(web: Path) -> tuple[str, str]:
    """`(major, spec)` from `engines.node`, or `("", "")`.

    The spec is a RANGE, and setup-node pins a version — so the major is
    what carries over. `>=24 <25` and `^24.1.0` both mean 24.
    """
    try:
        with open(web / PACKAGE_JSON, encoding="utf-8") as f:
            pkg = json.load(f)
    except (OSError, ValueError):
        return "", ""
    if not isinstance(pkg, dict):
        return "", ""
    spec = ((pkg.get("engines") or {}) if isinstance(pkg.get("engines"), dict) else {}).get("node")
    if not isinstance(spec, str):
        return "", ""
    match = _MAJOR.search(spec)
    if not match:
        return "", ""
    return match.group(1), _one_line(spec, limit=40)


def resolve(web_dir: str) -> tuple[dict[str, str], str]:
    """`(outputs, reason)` for one consumer web project.

    `outputs` is always the full set of keys — the caller appends them to
    `$GITHUB_OUTPUT` unconditionally, so a declining run still turns the
    setup step off explicitly rather than leaving the output unset.
    `reason` is empty on success and names the gap otherwise.
    """
    web = Path(web_dir)
    out = {
        "setup": "false",
        "version": "",
        "source": "",
        "node-version": "",
        "node-version-file": "",
    }

    nvmrc = read_nvmrc(web)
    if nvmrc:
        out.update({
            "setup": "true",
            "version": nvmrc,
            "source": f"{web_dir}/{NVMRC}",
            # The FILE, not the value: setup-node reads it, so there is no
            # second copy of the number anywhere in this pipeline.
            "node-version-file": f"{web_dir}/{NVMRC}",
        })
        return out, ""

    major, spec = read_engines(web)
    if major:
        out.update({
            "setup": "true",
            "version": major,
            "source": f"{web_dir}/{PACKAGE_JSON} engines.node {spec}",
            "node-version": major,
        })
        return out, ""

    return out, (
        f"{web_dir} declares no node version — no {NVMRC} and no engines.node "
        f"in {PACKAGE_JSON}. The screenshot harness will run on the runner's "
        f"default node, so the visual comparison is reported as not run "
        f"rather than guessed at."
    )


def main(argv: list[str]) -> int:
    web_dir = argv[0] if argv else DEFAULT_WEB_DIR
    out, reason = resolve(web_dir)
    for key, value in out.items():
        print(f"{key}={value}")
    if reason:
        print(reason, file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
