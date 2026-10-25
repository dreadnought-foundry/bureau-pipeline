"""A proof run is told how long each of its sign-ins lives (DRE-7020).

Every sign-in a proof run holds is made when the job starts. On 2026-10-11 the
first proof run of DRE-6931 held four console pages for 60 minutes, and its
AWS session and both of its GitHub tokens expired about a minute before the
hold ended: its record opened with four rows `Not observed.` and a second run
with fresh sign-ins read the logs. The brief called the session
"short-lived" and said nothing else, so the run worked the lifetimes out by
meeting the errors.

`proof-task.yml` now hands each agent step the AWS session's length, read off
the caller's repository variable, and `briefs/proof.md` states the lifetimes
and what to do when a wait will outlast one. These tests hold the brief to the
card's four sentences, word for word and inside the AWS bullet of its
identities section, and hold the env name the brief tells the run to read to
the one the workflow actually sets — each read from its own file.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import assemble_context  # noqa: E402

PROOF_BRIEF = ROOT / "briefs" / "proof.md"
PROOF_TASK = ROOT / ".github" / "workflows" / "proof-task.yml"
ACTION = "anthropics/claude-code-action"

#: The caller's variable the session's length is read from, with the action's
#: own one-hour default as the agent steps' fallback.
SESSION_VALUE = "${{ vars.PROOF_ROLE_DURATION_SECONDS || '3600' }}"

#: The rule, sentence by sentence, as the card words it.
RULE = (
    "Every sign-in you hold was made when the job started: `GH_READ_TOKEN` and "
    "`GH_TOKEN` live one hour and nothing in the run can renew them, and the "
    "AWS session lives `PROOF_SESSION_SECONDS` seconds.",
    "Make every GitHub read, and the one refused write, before anything that "
    "waits; a push made after the first hour is refused, so commit the record "
    "on the branch and leave the push and the pull request to the workflow's "
    "push-rescue step.",
    "Before a hold or any other wait, add up the wait, the window the card "
    "reads after it and the reads themselves; if that ends after the AWS "
    "session does, make the hold anyway, write each window's start and end in "
    "PT into the record, and write every row you could not read as "
    "`Not observed.` with that reason, so the next run on the branch reads "
    "those windows.",
    "Never run a hold a second time: the job ends at 120 minutes; when a "
    "release or a backend start lands inside a hold, quote it, read each row "
    "as the card words it, and write a count the card's window can no longer "
    "reach as `Not met.` with the lines you saw.",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _loose(needle: str) -> re.Pattern:
    """`needle` with every run of whitespace made elastic — the brief
    hard-wraps, and a sentence falling across a line break is not a brief
    that stopped saying it."""
    return re.compile(r"[\s>]+".join(re.escape(w) for w in needle.split()))


def _identities() -> str:
    body = _read(PROOF_BRIEF)
    start = body.index("\n## Identities")
    end = body.index("\n## ", start + 1)
    return body[start:end]


def _aws_bullet() -> str:
    """The AWS bullet of the identities section: from its own marker to the
    next top-level bullet."""
    section = _identities()
    start = section.index("\n- **AWS:")
    end = section.index("\n- ", start + 1)
    return section[start:end]


@pytest.mark.parametrize("sentence", RULE, ids=range(len(RULE)))
def test_the_aws_bullet_carries_the_rule_word_for_word(sentence):
    assert _loose(sentence).search(_aws_bullet()), (
        f"briefs/proof.md's AWS identity bullet does not carry the session "
        f"rule sentence {sentence!r} (DRE-7020)"
    )


def test_the_rule_names_the_card():
    assert "DRE-7020" in _aws_bullet()


def _brief_session_name() -> str:
    m = re.search(r"the\s+AWS\s+session\s+lives\s+`(\w+)`\s+seconds", _aws_bullet())
    assert m, "the brief's first session sentence names no env variable"
    return m.group(1)


def _workflow_session_names() -> set[str]:
    steps = next(iter(yaml.safe_load(_read(PROOF_TASK))["jobs"].values()))["steps"]
    agents = [s for s in steps if str(s.get("uses") or "").split("@")[0] == ACTION]
    assert len(agents) == 3, "the agent step and its two retries"
    names = set()
    for step in agents:
        found = {k for k, v in (step.get("env") or {}).items() if v == SESSION_VALUE}
        assert len(found) == 1, f"{step['name']} carries the session length as {found}"
        names |= found
    return names


def test_the_brief_and_the_workflow_name_the_same_env_variable():
    """Read from both files, typed in neither test: a rename on one side
    alone sends the run to read a variable it was never given."""
    assert {_brief_session_name()} == _workflow_session_names()


def test_the_brief_s_job_length_is_the_workflow_s():
    job = next(iter(yaml.safe_load(_read(PROOF_TASK))["jobs"].values()))
    m = re.search(r"the\s+job\s+ends\s+at\s+(\d+)\s+minutes", _aws_bullet())
    assert m, "the brief does not say when the job ends"
    assert int(m.group(1)) == job["timeout-minutes"]


def test_the_brief_reaches_the_proof_run():
    assert assemble_context.ROLE_BRIEF["proof"] == "proof.md"
