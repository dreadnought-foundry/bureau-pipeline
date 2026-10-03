"""No reusable workflow quietly takes away the caller's `id-token: write` (Stage 2 review item 31, S23).

A called workflow inherits its caller's token permissions, and GitHub lets a
called workflow only LOWER them. Declaring a `permissions:` block also sets
every permission it does not name to `none` — so a block that grants anything
at all without naming `id-token: write` removes the OIDC token from that job,
even though the calling stub granted it. Nothing fails. The run just goes
without the token: the agent log upload records a gap (DRE-4269), and once the
read door is live, the sweep quietly falls back to Linear for the rest of the
run (Stage 2 §3). This is the reason tests/test_agent_log_upload.py forbids a
`permissions:` block on the six uploading jobs. This test widens that rule to
every job in every reusable workflow, at both levels — workflow and job.

`write-all` carries the grant and `read-all` drops it. A job with no block
inherits the caller's grant, which is the normal case.

The one exception is written down below with its reason and checked against the
live file, so it cannot go stale silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

# Reusable workflows today. A floor, so a broken extractor cannot pass by
# finding nothing.
REUSABLE_FLOOR = 15

DOCUMENTED_EXCEPTIONS = {
    ("model-trial.yml", "workflow"): (
        "Deliberately credential-free (DRE-3897): the trial runs a fixed "
        "ten-turn task on one file to prove a candidate model, reads no card, "
        "writes nothing, uploads no working log and never calls the read door. "
        "`contents: read` is its whole permission set, so dropping `id-token` "
        "is the point: the run holds no token it does not use."
    ),
}


def _on(doc: dict) -> dict:
    # YAML 1.1 reads a bare `on:` key as boolean True.
    on = doc.get("on", doc.get(True)) or {}
    return on if isinstance(on, dict) else {}


def drops(permissions) -> bool:
    """True when this `permissions:` value leaves the job without
    `id-token: write`. None (no block) inherits the caller's grant."""
    if permissions is None:
        return False
    if isinstance(permissions, str):
        return permissions.strip() != "write-all"
    if isinstance(permissions, dict):
        return str(permissions.get("id-token", "none")).strip() != "write"
    return True


def id_token_drops(doc: dict) -> list[str]:
    """Every scope in a reusable workflow that drops the grant: `workflow`
    for the top-level block, or a job id."""
    if "workflow_call" not in _on(doc):
        return []
    out = []
    if drops(doc.get("permissions")):
        out.append("workflow")
    for job_id, job in (doc.get("jobs") or {}).items():
        if isinstance(job, dict) and drops(job.get("permissions")):
            out.append(str(job_id))
    return out


def _reusables() -> list[tuple[str, dict]]:
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if "workflow_call" in _on(doc):
            found.append((path.name, doc))
    return found


# ── the checker, on documents built to break it ─────────────────────────────
def _reusable(**fields) -> dict:
    return {"on": {"workflow_call": {}}, **fields}


@pytest.mark.parametrize("doc, expected", [
    (_reusable(jobs={"a": {"steps": []}}), []),
    (_reusable(jobs={"a": {"permissions": {"contents": "read"}}}), ["a"]),
    (_reusable(jobs={"a": {"permissions": {"contents": "read", "id-token": "write"}}}), []),
    (_reusable(jobs={"a": {"permissions": {"id-token": "none"}}}), ["a"]),
    (_reusable(jobs={"a": {"permissions": {}}}), ["a"]),
    (_reusable(jobs={"a": {"permissions": "read-all"}}), ["a"]),
    (_reusable(jobs={"a": {"permissions": "write-all"}}), []),
    (_reusable(permissions={"contents": "read"}, jobs={"a": {}}), ["workflow"]),
    ({"on": {"push": {}}, "jobs": {"a": {"permissions": {"contents": "read"}}}}, []),
])
def test_the_checker_finds_exactly_the_scopes_that_drop_the_grant(doc, expected):
    """This guard is green on the day it lands, because no reusable job
    drops the grant except the documented one. So the checker is proven here
    on cases that must fail. A guard that finds nothing proves nothing."""
    assert id_token_drops(doc) == expected


# ── the live files ──────────────────────────────────────────────────────────
def test_the_sweep_is_not_vacuous():
    assert len(_reusables()) >= REUSABLE_FLOOR, len(_reusables())


def test_no_reusable_job_drops_id_token():
    found = [(name, scope) for name, doc in _reusables()
             for scope in id_token_drops(doc)
             if (name, scope) not in DOCUMENTED_EXCEPTIONS]
    assert not found, (
        "these reusable scopes declare `permissions:` without `id-token: "
        "write`, which silently removes the OIDC token every calling stub "
        f"grants: {found}")


def test_every_exception_still_matches_a_live_drop():
    live = {(name, scope) for name, doc in _reusables() for scope in id_token_drops(doc)}
    stale = set(DOCUMENTED_EXCEPTIONS) - live
    assert not stale, f"exceptions that match nothing live — re-decide them: {sorted(stale)}"


def test_every_exception_states_its_reason():
    for key, reason in DOCUMENTED_EXCEPTIONS.items():
        assert len(reason) > 80, key
