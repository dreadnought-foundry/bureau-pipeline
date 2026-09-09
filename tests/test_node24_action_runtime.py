"""RED-first tests for DRE-3265 — the release train's AWS step runs on node24.

Every release-train run annotated:

    Node.js 20 is deprecated. The following actions target Node.js 20 but are
    being forced to run on Node.js 24: aws-actions/configure-aws-credentials@v4.

It is not our code. The vendor's own manifest declared `runs.using: node20` at
the release we pinned, and GitHub now forces such an action onto 24 with that
warning on every run. The warning ends when the pin names a release whose
`action.yml` declares `node24` — nothing else removes it.

Read at the vendor's tags on 2026-09-09, `gh api
repos/aws-actions/configure-aws-credentials/contents/action.yml?ref=<tag>`:

    v4.3.1  runs.using: node20   <- what the train was pinned to
    v5.0.0  runs.using: node20
    v5.1.1  runs.using: node20
    v6.0.0  runs.using: node24   <- the FIRST node24 release
    v6.2.4  runs.using: node24   <- the newest release (published 2026-08-31)

v6.0.0's own release notes name the runtime as its breaking change: "Update
action to use node24 _Note this requires GitHub action runner version v2.327.1
or later_". So `NODE24_FLOOR` is v6.0.0 and the tree carries v6.2.4.

The floor is asserted as well as the exact pin on purpose: a later Dependabot
patch inside v6 is a bump these tests should welcome, while a revert to any
v4/v5 release is the annotation coming back and must be red. `# v<version>`
plus a 40-char sha is this repo's house pin shape (DRE-3418,
`scripts/check_action_pins.py`) and is re-read here off the live tree.
"""

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_action_pins  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"
ACTIONS = ROOT / ".github" / "actions"
RELEASE_TRAIN = WORKFLOWS / "release-train.yml"

ACTION = "aws-actions/configure-aws-credentials"

# The first release whose manifest declares node24 (see the module docstring).
NODE24_FLOOR = (6, 0, 0)

# What the tree carries today: the newest release, resolved with
# `gh api repos/aws-actions/configure-aws-credentials/git/ref/tags/v6.2.4`.
PINNED_SHA = "cbe3b392738ccf3f987d68400dafcf4b0624a56c"
PINNED_VERSION = "v6.2.4"

# The node20 pin this card replaces. Named so a revert is caught by the string
# and not only by the arithmetic.
NODE20_SHA = "7474bc4690e29a8392af63c5b98e7449536d5c3a"


def _yaml_files():
    return sorted(WORKFLOWS.glob("*.yml")) + sorted(ACTIONS.glob("*/action.yml"))


def _pins():
    """Every `uses:` of the AWS credentials action across the live tree."""
    return [
        ref
        for path in _yaml_files()
        for ref in check_action_pins.references(path)
        if ref.action == ACTION
    ]


def _version(comment):
    """(6, 2, 4) for `# v6.2.4 …`, or None when the comment names no release."""
    match = check_action_pins.VERSION_COMMENT_RE.match(comment or "")
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).lstrip("v").split("."))


def test_the_release_train_assumes_its_role_through_this_action():
    """The vacuity guard: these tests say nothing if the step is gone."""
    doc = yaml.safe_load(RELEASE_TRAIN.read_text())
    steps = [
        step
        for job in doc["jobs"].values()
        for step in (job.get("steps") or [])
        if str(step.get("uses", "")).startswith(ACTION + "@")
    ]
    assert len(steps) == 1, steps
    assert "role-to-assume" in steps[0]["with"]


def test_every_pin_of_the_action_is_at_or_above_the_first_node24_release():
    """The rule, not the snapshot: a v6 patch bump passes, v4/v5 is red."""
    pins = _pins()
    assert pins, f"no `uses:` of {ACTION} found — the live scan went vacuous"
    for ref in pins:
        version = _version(ref.comment)
        assert version is not None, f"{ref.path.name}:{ref.line} names no release"
        assert version >= NODE24_FLOOR, (
            f"{ref.path.name}:{ref.line} pins {ref.uses} at "
            f"v{'.'.join(str(n) for n in version)}, whose manifest declares "
            f"node20 — every run of it is annotated deprecated (DRE-3265)"
        )


def test_the_pin_is_the_recorded_node24_sha_and_version():
    for ref in _pins():
        assert ref.ref == PINNED_SHA, f"{ref.path.name}:{ref.line} {ref.uses}"
        assert re.match(rf"#\s*{re.escape(PINNED_VERSION)}\b", ref.comment or ""), (
            f"{ref.path.name}:{ref.line} carries {ref.comment!r}, not "
            f"`# {PINNED_VERSION}`"
        )


def test_the_pin_keeps_the_house_shape():
    """DRE-3418: a 40-char sha plus the version comment Dependabot reads."""
    for ref in _pins():
        assert check_action_pins.SHA_RE.fullmatch(ref.ref), ref.uses
        assert check_action_pins.VERSION_COMMENT_RE.match(ref.comment or ""), ref


def test_the_node20_sha_survives_nowhere_under_dot_github():
    """A revert reintroduces the string, whatever version comment rides it."""
    offenders = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in sorted((ROOT / ".github").rglob("*.yml"))
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if NODE20_SHA in line
    ]
    assert offenders == [], f"the node20 pin is back: {offenders}"
