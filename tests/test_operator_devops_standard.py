"""The "Operator does DevOps" standard names the studio-machine exception (DRE-8580).

The studio host (agent-bureau epic DRE-8486) lets a console conversation run a
command on an operator-owned machine, as that machine's own OS user holding its
own cloud role. That is one deliberate exception to "agents hold no cloud
credentials", so the standard says it, says build runs are unchanged, and links
the record that holds the names (agent-bureau's
`architecture/decisions/adr-studio-host.md`, DRE-8571).

Each assertion is content-shaped on purpose, like its neighbor
`tests/test_ceo_steps_up_front_standard.py`: a document is the only consumer of
this change, and a rule nothing reads back is a rule that gets edited away.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STANDARD = ROOT / "standards" / "architecture.md"

HEADING = "## Operator does DevOps (the load-bearing boundary)"
EXCEPTION = "**The studio-machine exception (DRE-8486).**"
RECORD = (
    "https://github.com/dreadnought-foundry/agent-bureau/blob/main/"
    "architecture/decisions/adr-studio-host.md"
)

# The section's first paragraph as it stood before DRE-8580, word for word.
FIRST_PARAGRAPH = """\
Agents have **no cloud credentials.** The DevOps agent *authors* CDK / CI /
migrations / monitors and runs only credential-less checks (`cdk synth`/`diff`,
tests); the **operator runs every `cdk deploy`, migration, and rollout**.
Enforced by a hard classifier/permission block, not just a brief."""

# The machine and account names belong to the record, not the standard.
FORBIDDEN = ("@", "Mac Studio", "studio-1", "CEO's machine", "bureau-desk")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _loose(needle: str) -> re.Pattern:
    """`needle` with every run of whitespace made elastic — the standard
    hard-wraps at 80 columns and a phrase falling across a line break is not a
    document that stopped saying it."""
    return re.compile(r"\s+".join(re.escape(w) for w in needle.split()), re.I)


def _section() -> str:
    """The section, heading to the next `## `."""
    body = _read(STANDARD)
    at = body.find(HEADING)
    assert at >= 0, f"standards/architecture.md carries no {HEADING!r} heading"
    rest = body[at:]
    end = re.compile(r"^## ", re.M).search(rest, 1)
    return rest[: end.start()] if end else rest


def _exception() -> str:
    """The exception paragraph, its heading to the next blank line."""
    section = _section()
    at = section.find(EXCEPTION)
    assert at >= 0, f"the section carries no {EXCEPTION!r} paragraph"
    rest = section[at:]
    end = rest.find("\n\n")
    return rest if end < 0 else rest[:end]


def _assert_says(text: str, *phrases: str) -> None:
    for phrase in phrases:
        assert _loose(phrase).search(text), f"the paragraph does not say {phrase!r}"


def test_first_paragraph_is_unchanged_and_comes_first():
    section = _section()
    assert section.startswith(HEADING + "\n" + FIRST_PARAGRAPH + "\n\n"), (
        "the section's first paragraph must stay word for word"
    )


def test_exception_paragraph_appears_exactly_once_after_the_first():
    section = _section()
    assert section.count(EXCEPTION) == 1
    assert section.index(EXCEPTION) > section.index(FIRST_PARAGRAPH)
    assert _exception().startswith(EXCEPTION)


def test_point_1_build_runs_are_unchanged():
    _assert_says(
        _exception(),
        "Build runs are unchanged",
        "the DevOps agent",
        "still holds no cloud credentials",
        "hard classifier/permission block still applies",
        "Nothing in this paragraph gives a build run a credential",
    )


def test_point_2_the_one_exception_is_the_machines_own_user():
    _assert_says(
        _exception(),
        "operator-owned machine",
        "machine roster",
        "separate, non-admin OS user",
        "never the operator's own account",
        "its own cloud role",
        "never a person's",
    )


def test_point_3_the_cloud_role_denies_in_the_records_words():
    _assert_says(
        _exception(),
        "assuming or passing any other role",
        "billing and account settings",
        "writing secrets and parameters",
        "reading a secret's value or an encrypted parameter's value",
        "the audit trail",
    )


def test_point_3_never_claims_a_deny_on_reading_plain_parameters():
    text = _exception()
    for match in _loose("reading").finditer(text):
        tail = text[match.end() : match.end() + 60]
        assert not re.match(r"\s+parameters", tail), (
            "the paragraph says 'reading parameters' without 'encrypted'"
        )
    _assert_says(text, "kept anywhere else", "stays readable", "a plain parameter")


def test_point_3_names_the_deploy_deny_and_what_the_denies_do_not_reach():
    _assert_says(
        _exception(),
        "driving a stack or starting code that runs under another role",
        "Until a signed Allow can hand a deploy its own credential",
        "bind the role's own calls",
        "code that already runs under another role",
    )


def test_point_3_the_source_control_identity_is_a_member_with_five_denies():
    _assert_says(
        _exception(),
        "organization member, never an owner",
        "no administration, secrets, variables, members or environments permission",
        "Those acts stay with the operator",
    )


def test_point_4_a_risky_command_needs_a_signed_allow():
    _assert_says(
        _exception(),
        "same classes file the console's Auto check reads",
        "signed, single-use, expiring Allow bound to that exact command",
        "the machine itself refuses it",
    )


def test_point_5_links_the_record():
    assert RECORD in _exception()


def test_the_five_points_come_in_order():
    text = _exception()
    order = [
        _loose(p).search(text)
        for p in (
            "Build runs are unchanged",
            "The one exception",
            "assuming or passing any other role",
            "signed, single-use, expiring Allow",
            RECORD,
        )
    ]
    assert all(order), "a point is missing"
    starts = [m.start() for m in order]
    assert starts == sorted(starts), "the five points are out of order"


def test_section_names_no_person_account_or_machine():
    section = _section()
    for name in FORBIDDEN:
        assert name not in section, f"the section names {name!r}"
