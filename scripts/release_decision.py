#!/usr/bin/env python3
"""The release train's decision, as a GitHub deployment message (DRE-4768).

The train decides something about every declared surface on every run —
released, held, current, spacing, window, ci-pending, refused — and until this
module that decision was one line in a job's log (`release_train.Decision.
receipt`). A log line is read by whoever opens the run. This module turns the
same decision into a message GitHub DELIVERS: one deployment, plus one
deployment status, whose payload is the decision as data.

WHY A DEPLOYMENT AND NOT A CHECK RUN. Two reasons, and both are about what
already exists rather than about the shape being prettier.

  * GitHub delivers a deployment as `deployment` and `deployment_status`
    webhook messages — exactly the two messages the sibling epic DRE-4761
    teaches the Record to keep. Choosing this shape rides the subscription and
    the App permission already planned there; a check run would need its own.
  * It keeps the train's decision OUT of the check-run population the train's
    own green-at-SHA classifier and the merge gate read
    (`merge_gate.gating_check_runs`). DRE-3263 spent a whole card excluding
    self-authored checks on the commit being judged; writing the train's
    verdict as a check run on the commit the train is judging would hand that
    problem straight back.

WHAT THIS MODULE OWES, AND WHAT IT NEVER DOES. `record` builds the payload,
`check_record` names every problem with one, `hand_act` says which command a
person's hand clears a decision with, and `write` sends the two posts. `write`
NEVER raises and never retries: a message that could not be sent is one line,
the run is as green as it was, and the tag stays the only record a release
depends on. The train is not made less reliable by gaining a second record —
that is the whole rule this module is written around.

THE VERSION FIELD SAYS WHAT THE TRAIN CAN KNOW. `deployed` is the surface's
newest tag BEFORE the decision — what the surface stands at. `version` is the
tag that was cut, and it is `null` on every decision that cut none: the train
cannot know the next number before the surface's own script chooses it, and
this says so rather than inventing a field.

NO IMPORT OF `release_train`. The wiring card makes the train call this module;
importing it back would be a cycle. The two strings this module shares with the
train — `RELEASE_HOLD` and the `deferred:` prefix — are written here and bound
to the train's own constants by `tests/test_release_decision.py`, which is also
where `CODES` is bound to every code the train constructs.

CLI:
    release_decision.py render     # rewrite docs/release-decision.md
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent_marker  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = ROOT / "docs" / "release-decision.md"

#: Opens every line this module prints. The clause `write` RETURNS is the
#: caller's to print, inside the train's own receipt; this tag is for the
#: detail that does not fit in it.
TAG = "release-decision"

ENVIRONMENT = "release-train"           # the deployment's `environment`
TASK = "release-train-decision"         # the deployment's `task` — what a consumer filters on
SCHEMA = "release-train-decision/1"     # `payload.schema`, and the first token of every description
DESCRIPTION_LIMIT = 140                 # GitHub's cap on a deployment / status `description`
STATES = {"release": "success", "refuse": "failure", "held": "inactive", "no-op": "inactive"}

#: The train's two passes. `plan` decides every surface and builds the matrix;
#: `release` decides again inside the surface's own concurrency lane.
PHASES = ("plan", "release")

#: The closed set of every `code` the train constructs a `Decision` with, in
#: the order the train's own decision table reads. It is not derived at import
#: time on purpose: this module must load on a bare `python3` beside a train
#: it does not import. `tests/test_release_decision.py` derives the set from
#: `scripts/release_train.py`'s source and holds it equal to this tuple, so a
#: code added to the train without a row here is a red test, never a silent
#: gap.
CODES = (
    "held",
    "channel-current", "channel-advancing", "channel-blocked", "channel-unknown",
    "no-script", "auto-false", "current", "spacing", "window",
    "ci-pending", "ci-red", "ci-absent", "walk-bound",
    "deferred", "release", "released",
    "script-failed", "no-tag", "tag-not-annotated", "tag-elsewhere",
    "unreadable",
)

#: The fleet brake, and the `deferred:` line a surface script prints. Written
#: here, bound to `release_train`'s own constants by the tests (see the module
#: docstring — importing the train would be a cycle).
ENV_HOLD = "RELEASE_HOLD"
DEFERRAL_PREFIX = "deferred:"

#: What a 403 from the deployments API means, in the words the caller can act
#: on — the `release_train.re_arm_failure` shape.
LACKS_DEPLOYMENTS = "caller stub lacks deployments: write"

#: How much of a refusal rides in the one clause. The full text goes to the
#: log through `out`, so nothing is lost and the clause stays one line.
WHY_LIMIT = 200


class Field(NamedTuple):
    """One key of the payload, and what it carries."""

    name: str
    means: str


#: The record, field by field — the table the document renders and the schema
#: check reads. Every key is present in every record; a null is explicit.
FIELDS = (
    Field("schema", f"always `{SCHEMA}`, and the first token of every "
                    f"description too, so a consumer can filter on the line as "
                    f"well as on the payload"),
    Field("repo", "`owner/name` — the caller the train ran in"),
    Field("surface", "the declared surface's name, as the caller's "
                     "`release.json` spells it"),
    Field("act", "one of `release`, `no-op`, `held`, `refuse` — `Decision.act`, "
                 "and what the status's `state` follows from"),
    Field("code", "`Decision.code`, the train's own vocabulary, never restated "
                  "here — the closed set is `CODES`"),
    Field("reason", "`Decision.reason` — the sentence the run's log line "
                    "carries, verbatim and never reworded"),
    Field("phase", "`plan` or `release`, whichever of the train's two passes "
                   "decided this"),
    Field("sha", "the commit the decision is about"),
    Field("head", "the head the plan walked from"),
    Field("deployed", "the surface's newest tag BEFORE the decision — what it "
                      "stands at; `null` when its series holds none"),
    Field("version", "the tag cut, or `null` when nothing was cut; the train "
                     "cannot know the next number before the surface script "
                     "chooses it"),
    Field("hand_act", "the one command a person's hand clears this decision "
                      "with, or `null` when nobody's hand clears it"),
    Field("re_arm_at", "the UTC minute the run re-armed itself for "
                       "(`Decision.re_arm_at`), or `null`"),
    Field("run_id", "the Actions run that decided, as a number"),
    Field("run_attempt", "which attempt of that run, as a number"),
    Field("run_url", "that run's URL, and the status's `log_url`"),
    Field("event", "the GitHub event name the run fired on"),
    Field("decided_at", "when the decision was made, UTC, `YYYY-MM-DDTHH:MM:SSZ`"),
)

#: The two values the MESSAGES carry beside the payload: the status's `state`
#: and the deployment's `description`. A consumer that has read a
#: `deployment_status` has both in hand, so `check_record` accepts them
#: alongside the record and checks that they follow from it. They are never
#: keys of the payload itself — the payload would only be restating them.
DELIVERED = ("state", "description")

#: Which codes a person's hand clears, and with what. A template, so the
#: document prints the same string the code answers with and the two cannot
#: drift. Every other code answers `None`: nobody's hand clears a spacing hold,
#: a commit still checking, or a release that worked.
_ROLLBACK = "{rollback}"
_DISPATCH = "gh workflow run release-train.yml --repo {repo} -f surface={surface}"
_HAND_ACTS = {
    # The brake resolves against the CALLER repo and the organization, and
    # which one is set is invisible from here — so the one string names both
    # routes (`standards/release-train.md`).
    "held": (f"gh variable delete {ENV_HOLD} --repo {{repo}} — or, when the "
             f"fleet-wide brake is the one that is set, "
             f"gh variable delete {ENV_HOLD} --org {{owner}}"),
    "auto-false": _DISPATCH,
    "walk-bound": _DISPATCH,
    "deferred": (f"{DEFERRAL_PREFIX} the line {{surface}}'s own script printed "
                 f"— this record's reason carries it verbatim, and it names "
                 f"the person the deployment is owed to"),
    "script-failed": _ROLLBACK,
    "no-tag": _ROLLBACK,
    "tag-not-annotated": _ROLLBACK,
    "tag-elsewhere": _ROLLBACK,
}


def _utc(when) -> str | None:
    """A moment as the record spells it, or `None`. Written here rather than
    imported from the train (see the module docstring)."""
    if not isinstance(when, datetime):
        return None
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# 1. The record                                                                #
# --------------------------------------------------------------------------- #


def hand_act(code, *, repo, surface, rollback) -> str | None:
    """The one command that clears this decision, or `None`.

    `None` is the common answer and the honest one: a spacing hold ends on a
    clock, a `ci-pending` on the next CI completion, and a release needs
    nothing. A surface that declares no `rollback` has no command to name for
    the tag and script failures, so those answer `None` too.
    """
    template = _HAND_ACTS.get(code)
    if template is None:
        return None
    owner = str(repo or "").partition("/")[0]
    filled = template.format(repo=repo or "", surface=surface or "",
                             rollback=rollback or "", owner=owner or "<org>")
    return filled.strip() or None


def record(decision, *, repo, surface, phase, sha, head, deployed, now, env) -> dict:
    """The decision as the payload the deployment carries.

    `surface` is the declared surface — the `release_train.Surface` the train
    holds, or just its name where a caller has nothing more. The name lands in
    the payload either way; the object additionally carries the `rollback` the
    four tag and script failures name as their hand act.
    """
    name = getattr(surface, "name", surface)
    rollback = getattr(surface, "rollback", None)
    code = getattr(decision, "code", None)
    return {
        "schema": SCHEMA,
        "repo": repo,
        "surface": name,
        "act": getattr(decision, "act", None),
        "code": code,
        "reason": getattr(decision, "reason", None),
        "phase": phase,
        "sha": sha,
        "head": head,
        "deployed": deployed,
        "version": getattr(decision, "tag", None),
        "hand_act": hand_act(code, repo=repo, surface=name, rollback=rollback),
        "re_arm_at": _utc(getattr(decision, "re_arm_at", None)),
        "run_id": _number(env.get("GITHUB_RUN_ID")),
        "run_attempt": _number(env.get("GITHUB_RUN_ATTEMPT")),
        "run_url": agent_marker.run_url(env) or "",
        "event": env.get("GITHUB_EVENT_NAME") or "",
        "decided_at": _utc(now) or "",
    }


def _number(raw) -> int:
    """A run id as a number — `0` outside CI, never a string that reads like
    one. The payload's two numeric fields are the only ones a consumer would
    ever compare, and a string would compare wrong."""
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
# 2. The schema check                                                          #
# --------------------------------------------------------------------------- #

#: Which fields may be `null`. Everything else is a string that is always
#: there — an absent value is `""`, never a missing key.
_NULLABLE = ("deployed", "version", "hand_act", "re_arm_at")
_NUMERIC = ("run_id", "run_attempt")


def check_record(record) -> list:
    """Every problem with a payload, each naming what is wrong — and `[]` for
    a valid one.

    A consumer may hand this the two values the messages carry beside the
    payload (`state`, `description`); when they are there they are checked
    against it, which is how a state that does not follow from the act and a
    description over GitHub's cap are caught.
    """
    if not isinstance(record, dict):
        return ["record: must be an object"]
    problems = []
    known = {field.name for field in FIELDS}
    for field in FIELDS:
        if field.name not in record:
            problems.append(f"{field.name}: missing")
    for key in record:
        if key not in known and key not in DELIVERED:
            problems.append(f"{key}: not a field of the record")

    for name in _NUMERIC:
        if name in record and not isinstance(record[name], int):
            problems.append(f"{name}: must be a number, not {record[name]!r}")
    for field in FIELDS:
        if field.name in _NUMERIC or field.name not in record:
            continue
        value = record[field.name]
        if value is None and field.name in _NULLABLE:
            continue
        if not isinstance(value, str):
            problems.append(f"{field.name}: must be a string, not {value!r}")

    if record.get("schema") not in (None, SCHEMA):
        problems.append(f"schema: {record['schema']!r} is not {SCHEMA!r}")
    act = record.get("act")
    if "act" in record and act not in STATES:
        problems.append(f"act: {act!r} is not one of "
                        f"{', '.join(repr(a) for a in STATES)}")
    if "code" in record and record["code"] not in CODES:
        problems.append(f"code: {record['code']!r} is not one of the train's "
                        f"codes (scripts/release_train.py)")
    if "phase" in record and record["phase"] not in PHASES:
        problems.append(f"phase: {record['phase']!r} is not one of "
                        f"{', '.join(repr(p) for p in PHASES)}")

    if "state" in record and act in STATES and record["state"] != STATES[act]:
        problems.append(f"state: {record['state']!r} does not follow from act "
                        f"{act!r} — that act is {STATES[act]!r}")
    described = record.get("description")
    if isinstance(described, str) and len(described) > DESCRIPTION_LIMIT:
        problems.append(f"description: {len(described)} characters, over "
                        f"GitHub's cap of {DESCRIPTION_LIMIT}")
    return problems


# --------------------------------------------------------------------------- #
# 3. The two messages                                                          #
# --------------------------------------------------------------------------- #


def description(record) -> str:
    """The one line both messages carry, cut to GitHub's cap. It opens with
    the schema so a consumer can filter the line as well as the payload."""
    record = record if isinstance(record, dict) else {}
    line = (f"{SCHEMA} {record.get('act')} {record.get('code')} "
            f"{record.get('surface')}")
    return line[:DESCRIPTION_LIMIT]


def deployment_body(record) -> dict:
    """`POST /repos/{repo}/deployments`.

    `auto_merge: false` so GitHub does not try to merge the default branch
    into the ref, and `required_contexts: []` so it does not refuse the
    deployment because a status on the commit is red — a refusal is exactly
    the decision this message exists to carry.
    """
    return {
        "ref": record.get("sha"),
        "environment": ENVIRONMENT,
        "task": TASK,
        "auto_merge": False,
        "required_contexts": [],
        "description": description(record),
        "payload": record,
    }


def status_body(record) -> dict:
    """`POST /repos/{repo}/deployments/{id}/statuses`.

    `auto_inactive: false` so no OTHER deployment of this environment is
    marked inactive — above all the surface script's own stage deployments
    (agent-bureau DRE-3519). A decision reports; it does not retire anybody
    else's record.
    """
    return {
        "state": STATES.get(record.get("act")),
        "environment": ENVIRONMENT,
        "description": description(record),
        "log_url": record.get("run_url"),
        "auto_inactive": False,
    }


# --------------------------------------------------------------------------- #
# 4. The writer that never fails a run                                         #
# --------------------------------------------------------------------------- #


def gh(path: str, body: dict) -> dict:
    """One `gh api` POST — the module-level seam a test replaces (the
    `release_linear.write` shape). A `RuntimeError` carrying GitHub's own
    answer on refusal, which `write` turns into one clause."""
    done = subprocess.run(
        ["gh", "api", path, "--method", "POST", "--input", "-"],
        input=json.dumps(body), capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip()
                           or f"gh api {path} exited {done.returncode}")
    text = done.stdout.strip()
    return json.loads(text) if text else {}


def _why(detail) -> str:
    """A refusal as the clause says it. A 403 is named for what it is, the
    `release_train.re_arm_failure` shape — the caller's stub has to grant
    `deployments: write`, and nothing else about the run is wrong."""
    text = " ".join(str(detail or "").split())
    if "not accessible by integration" in text or "HTTP 403" in text:
        return f"{LACKS_DEPLOYMENTS} (standards/release-train.md)"
    return text[:WHY_LIMIT] or "no reason given"


def _refused(why: str, out, detail: str = "") -> str:
    """The one clause for a message that was not sent, with the full text in
    the log behind it. Never a warning annotation and never an exit code: the
    decision still stands and the tag is still the record."""
    clause = f"decision not recorded: {why}"
    extra = f" — {detail}" if detail and detail not in clause else ""
    out(f"{TAG}: {clause}{extra}")
    return clause


def _post(record, *, repo, post, out) -> str:
    problems = check_record(record)
    if problems:
        return _refused(f"the payload is malformed: {'; '.join(problems)}", out)
    try:
        created = post(f"repos/{repo}/deployments", deployment_body(record))
    except Exception as error:  # noqa: BLE001 — a message never fails a run
        return _refused(_why(error), out, detail=" ".join(str(error).split()))
    identifier = created.get("id") if isinstance(created, dict) else None
    if not identifier:
        return _refused(f"GitHub's answer named no deployment ({created!r})", out)
    state = STATES[record["act"]]
    try:
        post(f"repos/{repo}/deployments/{identifier}/statuses", status_body(record))
    except Exception as error:  # noqa: BLE001 — same rule, one post later
        return _refused(f"deployment {identifier} was created and its status "
                        f"was not: {_why(error)}", out,
                        detail=" ".join(str(error).split()))
    return f"decision recorded: deployment {identifier} ({state})"


def write(record, *, repo, gh=None, out=print) -> str:
    """Post the deployment and then its status, and return the ONE clause the
    caller prints: `decision recorded: deployment <id> (<state>)`, or
    `decision not recorded: <why>`.

    It NEVER raises and never retries. A message that could not be sent is one
    line, the run is as green as it was, and the tag stays the only record a
    release depends on — so every failure, including one in this function's
    own bookkeeping, comes back as that second clause.
    """
    try:
        # `gh` the argument shadows `gh` the module-level seam, which is the
        # one a test replaces — so the default is read off the module.
        return _post(record, repo=repo, post=gh or globals()["gh"], out=out)
    except Exception as error:  # noqa: BLE001 — the writer cannot fail a run
        return f"decision not recorded: {_why(error)}"


# --------------------------------------------------------------------------- #
# The document                                                                 #
# --------------------------------------------------------------------------- #

#: What the hand-act table fills its templates with, so the document shows the
#: same string `hand_act` answers with.
_DOC_REPO = "<owner>/<name>"
_DOC_SURFACE = "<surface>"
_DOC_ROLLBACK = "<the surface's declared rollback>"


def render() -> str:
    out: list[str] = []
    w = out.append
    w("# The release train's decision, as a deployment")
    w("")
    w("<!-- GENERATED FILE — do not edit. Source: scripts/release_decision.py.")
    w("     Regenerate with `python3 scripts/release_decision.py render`. -->")
    w("")
    w(
        "The train decides something about every declared surface on every run. "
        "This page is the shape that decision is SENT in: one GitHub "
        "deployment plus one deployment status, whose payload is the decision "
        "as data. It is rendered from `scripts/release_decision.py`, so it "
        "cannot drift from what is posted."
    )
    w("")
    w("## Why a deployment")
    w("")
    w(
        "GitHub delivers a deployment as `deployment` and `deployment_status` "
        "webhook messages — the two messages the Record keeps (DRE-4761), so "
        "this shape rides a subscription and an App permission that are "
        "already planned. It also keeps the train's own verdict OUT of the "
        "check-run population the green-at-SHA classifier and the merge gate "
        "read (`merge_gate.gating_check_runs`): DRE-3263 spent a card "
        "excluding self-authored checks on the commit being judged, and a "
        "check run here would hand that problem straight back."
    )
    w("")
    w(
        "A deployment created with the run's own `GITHUB_TOKEN` **does not "
        "start any workflow** — GitHub does not fire a workflow from an event "
        "that token created, `workflow_dispatch` and `repository_dispatch` "
        "excepted. That is the rule that keeps the train from triggering "
        "itself, and it is why the decision can be written on every run."
    )
    w("")
    w("## The two messages")
    w("")
    w(f"`POST /repos/{{repo}}/deployments` — `<line>` is the description "
      f"below, `<record>` the payload below:")
    w("")
    w("```json")
    deployment = deployment_body({"sha": "<sha>"})
    deployment["description"], deployment["payload"] = "<line>", "<record>"
    w(json.dumps(deployment, indent=2))
    w("```")
    w("")
    w(f"`POST /repos/{{repo}}/deployments/{{id}}/statuses`:")
    w("")
    w("```json")
    status = status_body({"run_url": "<run_url>"})
    status["state"], status["description"] = "<state>", "<line>"
    w(json.dumps(status, indent=2))
    w("```")
    w("")
    w(
        f"`<line>` is `{SCHEMA} <act> <code> <surface>`, cut to "
        f"{DESCRIPTION_LIMIT} characters."
    )
    w("")
    w("## The record, field by field")
    w("")
    w("Every key is present in every record; a null is explicit.")
    w("")
    w("| Field | Carries |")
    w("| --- | --- |")
    for field in FIELDS:
        w(f"| `{field.name}` | {field.means} |")
    w("")
    w("## The state follows the act")
    w("")
    w("| `act` | the status's `state` |")
    w("| --- | --- |")
    for act, state in STATES.items():
        w(f"| `{act}` | `{state}` |")
    w("")
    w(
        "A hold and a no-op are the train working, so both are `inactive` — "
        "reported, not failed. Only a refusal is `failure`."
    )
    w("")
    w("## What a person's hand clears")
    w("")
    w(
        "`hand_act` answers one command for the codes a person clears, and "
        "`null` for every other — nobody's hand clears a spacing hold, a "
        "commit that is still checking, or a release that worked."
    )
    w("")
    w("| Code | The hand act |")
    w("| --- | --- |")
    for code in CODES:
        act = hand_act(code, repo=_DOC_REPO, surface=_DOC_SURFACE,
                       rollback=_DOC_ROLLBACK)
        # Backticked, so the `<placeholders>` are read as text rather than as
        # HTML by every markdown renderer that gets near this file.
        w(f"| `{code}` | " + (f"`{act}`" if act else "— nobody's hand clears it")
          + " |")
    w("")
    w(
        f"The brake is the repository variable `{ENV_HOLD}`, which resolves "
        f"against the caller repo AND the organization — which of the two is "
        f"set is invisible from the decision, so the one string names both "
        f"routes (`standards/release-train.md`)."
    )
    w("")
    w("## The vendor's limits, and the three flags")
    w("")
    w(
        f"* **`description` is capped at {DESCRIPTION_LIMIT} characters** by "
        f"GitHub, on the deployment and on the status alike. The line is cut "
        f"to it rather than refused, and the payload carries the whole "
        f"sentence in `reason`."
    )
    w(
        "* **`auto_merge: false`** so GitHub does not try to merge the default "
        "branch into the ref. A decision is about a commit; it is not a "
        "request to move one."
    )
    w(
        "* **`required_contexts: []`** so GitHub does not refuse the "
        "deployment because a status on the commit is red. A refusal is "
        "exactly the decision this message exists to carry, and a red commit "
        "is the case it matters most in."
    )
    w(
        "* **`auto_inactive: false`** so no other deployment is marked "
        "inactive by a decision — above all the surface script's own stage "
        "deployments (agent-bureau DRE-3519). A decision reports; it does not "
        "retire anybody else's record."
    )
    w("")
    w("## What a consumer filters on")
    w("")
    w(
        f"**`task`**, which is always `{TASK}`. The Record's App receives "
        f"every `deployment` and `deployment_status` message the repository "
        f"produces, the surface scripts' own rollouts among them; the task is "
        f"what tells a train decision apart from a rollout, and it is on the "
        f"deployment where every status carries it too. `environment` is "
        f"`{ENVIRONMENT}` and `payload.schema` is `{SCHEMA}` — the second is "
        f"the one to version against, and the description's first token "
        f"repeats it so a log line answers the same question."
    )
    w("")
    w(
        "`check_record` is what a consumer checks a delivered message with: "
        "hand it the payload, or the payload plus the `state` and "
        "`description` the messages carry beside it, and it names every "
        "problem."
    )
    w("")
    w("## What this never does")
    w("")
    w(
        "`write` never raises and never retries. A message that could not be "
        "sent is one clause — `decision not recorded: <why>` — the run is as "
        "green as it was, and the tag stays the only record a release depends "
        f"on. A 403 is named for what it is: `{LACKS_DEPLOYMENTS}`."
    )
    w("")
    return "\n".join(out)


def _cmd_render(args) -> int:
    rendered = render()
    with open(DOC_PATH, "w", encoding="utf-8") as fh:
        fh.write(rendered)
    print(f"wrote {DOC_PATH} ({len(rendered.splitlines())} lines)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("render", help=f"rewrite {DOC_PATH.name}")
    args = parser.parse_args(argv)
    return {"render": _cmd_render}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
