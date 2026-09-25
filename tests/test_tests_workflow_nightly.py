"""Pipeline Tests runs every suite nightly on `main` (DRE-4807).

The fleet's one CI rule (`standards/engineering.md`, "CI: narrow per change,
whole every night", the CEO's decision of 2026-09-24): each change runs the
suites it can reach, once a night `main` runs everything, a red nightly is
repaired by an agent, and a missing nightly alarms. bureau-pipeline is the
repo every other repo's run is made of, so it carries the same net.

Before this file, `tests.yml` ran on `pull_request` and on `push` to `main` and
never on a schedule — so the whole suite ran only when somebody pushed, and the
two jobs that already decide for themselves what to run (`acts-consumers`'
changed-files gate, `tdd`'s pull-request-only gate) had no run in which
everything was proved together.

The three things asserted, each read off the workflow files rather than
remembered:

  * ONE nightly cron, off the hour, apart from every other cron this repo
    schedules, and outside the release window (`release_train.FLEET_WINDOW`) —
    a nightly competing with a train for runners delays both.
  * A SCHEDULE RUN SKIPS NOTHING BUT `tdd`. Every job's `if:` is evaluated
    against a schedule event, and `tdd` — which reads a pull request's commit
    list, and a schedule run has no pull request — is asserted to be the ONLY
    job that comes out false. A second exemption turns this red, and so does an
    `if:` this file cannot read: a gate nobody can evaluate is not a gate
    anybody can trust to run in full.
  * THE RED NIGHTLY IS REPAIRED. `self-red-main-repair.yml` still watches
    `Pipeline Tests`, and neither it nor the reusable stage it calls keys on the
    event that STARTED the watched run — only on the conclusion and the head
    branch — so a failed nightly engages the repair rail exactly as a failed
    push does.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WF_DIR = ROOT / ".github" / "workflows"
TESTS = WF_DIR / "tests.yml"
REPAIR_STUB = WF_DIR / "self-red-main-repair.yml"
REPAIR_STAGE = WF_DIR / "red-main-repair.yml"

sys.path.insert(0, str(ROOT / "scripts"))

import release_train  # noqa: E402

UTC = timezone.utc

#: The card's rule: `tdd` is the one named exemption, because it checks a pull
#: request's commits for test-before-fix order and a schedule run has neither.
THE_ONE_EXEMPTION = "tdd"

#: The other nightlies in the fleet, named in the card so this cron stays away
#: from them: agent-bureau's `23 10 * * *` (DRE-3656) and Portico's 02:30 PT
#: (DRE-4804), which is 09:30 UTC in PDT.
FLEET_NIGHTLIES = ("23 10 * * *", "30 9 * * *")


def _doc(path: Path) -> dict:
    assert path.is_file(), f"missing workflow: {path}"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _on(doc: dict) -> dict:
    # YAML 1.1 parses the bare key `on` as boolean True.
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _crons(doc: dict) -> list:
    schedule = _on(doc).get("schedule") or []
    return [entry["cron"] for entry in schedule]


def _tests_crons() -> list:
    return _crons(_doc(TESTS))


# ---------------------------------------------------------------------------
# Reading a job-level `if:` the way GitHub reads it, for a schedule event.
#
# Only the shapes this repo's workflows actually write are understood; anything
# else raises, and the caller reports the job rather than assuming it runs. An
# `if:` we cannot evaluate is exactly the case that would let a second silent
# exemption in.
# ---------------------------------------------------------------------------

SCHEDULE_CONTEXT = {
    "github.event_name": "schedule",
    # A schedule run's actor is the last user to push to the default branch or
    # the app that did; it is never dependabot[bot], which is the only actor
    # this repo's job gates name.
    "github.actor": "agent-bureau-qa-bot[bot]",
    "github.ref": "refs/heads/main",
    "github.ref_name": "main",
    "github.repository": "dreadnought-foundry/bureau-pipeline",
}


class Unreadable(Exception):
    """The `if:` uses a shape this reader does not understand."""


def _split_top(text: str, op: str) -> list:
    """Split on `op` at paren depth 0, outside quotes."""
    parts, depth, quote, start, i = [], 0, None, 0, 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and text.startswith(op, i):
            parts.append(text[start:i])
            i += len(op)
            start = i
            continue
        i += 1
    parts.append(text[start:])
    return [p.strip() for p in parts]


def _value(token: str):
    token = token.strip()
    if token.startswith("'") and token.endswith("'") and len(token) >= 2:
        return token[1:-1]
    if token in ("true", "false"):
        return token == "true"
    if token in SCHEDULE_CONTEXT:
        return SCHEDULE_CONTEXT[token]
    raise Unreadable(token)


def _term(text: str) -> bool:
    text = text.strip()
    while text.startswith("(") and text.endswith(")") and _split_top(
        text[1:-1], ")"
    ) == [text[1:-1]]:
        text = text[1:-1].strip()
    if "||" in text or "&&" in text:
        return evaluate(text)
    if text.startswith("!"):
        return not _term(text[1:])
    for op, keep in (("==", True), ("!=", False)):
        sides = _split_top(text, op)
        if len(sides) == 2:
            equal = _value(sides[0]) == _value(sides[1])
            return equal if keep else not equal
    return bool(_value(text))


def evaluate(expr: str) -> bool:
    """Would GitHub run a job carrying this `if:` on a schedule event?"""
    text = str(expr).strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    ors = _split_top(text, "||")
    if len(ors) > 1:
        return any(_term(part) for part in ors)
    ands = _split_top(text, "&&")
    if len(ands) > 1:
        return all(_term(part) for part in ands)
    return _term(text)


def jobs_a_schedule_run_would_skip(doc: dict) -> set:
    """The jobs whose `if:` is false — or unreadable — for a schedule event."""
    skipped = set()
    for name, job in (doc.get("jobs") or {}).items():
        expr = job.get("if")
        if expr is None:
            continue
        try:
            if not evaluate(expr):
                skipped.add(name)
        except Unreadable as exc:
            skipped.add(f"{name} (unreadable `if:`: {exc})")
    return skipped


# ---------------------------------------------------------------------------
# 1. One nightly cron, off the hour, out of everyone else's way.
# ---------------------------------------------------------------------------


def test_pipeline_tests_declares_exactly_one_nightly_cron():
    crons = _tests_crons()
    assert len(crons) == 1, (
        "tests.yml must declare exactly one `schedule:` cron — the nightly "
        f"full run on main (DRE-4807); found {crons}"
    )


def test_the_nightly_cron_is_not_on_the_hour():
    minute = _tests_crons()[0].split()[0]
    assert minute not in ("0", "00"), (
        "crons stay off the hour, where every other repo's scheduler piles up "
        f"(standards/engineering.md rule 2); got minute {minute!r}"
    )


def test_the_nightly_cron_fires_once_a_night():
    minute, hour, dom, month, dow = _tests_crons()[0].split()
    for field, label in ((minute, "minute"), (hour, "hour")):
        assert field.isdigit(), (
            f"the {label} field must be a single fixed value, not {field!r} — "
            "once a night, not a sweep"
        )
    assert (dom, month, dow) == ("*", "*", "*"), (
        "every night, not some nights: "
        f"day-of-month/month/day-of-week were {(dom, month, dow)}"
    )


def test_the_nightly_shares_its_slot_with_no_other_cron_here():
    # Derived from the workflow files, never a remembered list: a cron added
    # later at the same minute of the same hour fails this instead of
    # silently contending for runners with the nightly.
    mine = _tests_crons()[0]
    others = [
        (path.name, cron)
        for path in sorted(WF_DIR.glob("*.yml"))
        if path != TESTS
        for cron in _crons(_doc(path))
    ]
    clashes = [(name, cron) for name, cron in others if cron == mine]
    assert clashes == [], (
        f"the nightly {mine!r} collides with another cron in this repo: "
        f"{clashes}"
    )


@pytest.mark.parametrize("elsewhere", FLEET_NIGHTLIES)
def test_the_nightly_avoids_the_other_repos_nightlies(elsewhere):
    assert _tests_crons()[0] != elsewhere, (
        "crons stay apart across the fleet — this one lands on another "
        f"repo's nightly ({elsewhere})"
    )


@pytest.mark.parametrize("month", [1, 7])  # PST and PDT
def test_the_nightly_runs_outside_the_release_window(month):
    # A nightly competing with the release train for runners delays both
    # (standards/engineering.md rule 2). The window is read from its one
    # declaration, never a restated offset.
    minute, hour = (int(f) for f in _tests_crons()[0].split()[:2])
    fires = datetime(2026, month, 15, hour, minute, tzinfo=UTC)
    assert not release_train.in_window(release_train.FLEET_WINDOW, fires), (
        f"{fires.astimezone(release_train.PT):%H:%M} PT is inside the fleet's "
        f"release window ({release_train.FLEET_WINDOW})"
    )


def test_the_schedule_block_cites_the_card_and_the_rule():
    # The next person to read this trigger needs to know why it is here; the
    # comment is the only place that can tell them.
    body = TESTS.read_text(encoding="utf-8")
    head = body.split("jobs:", 1)[0]
    assert "schedule:" in head, "the schedule trigger belongs in the `on:` block"
    assert "DRE-4807" in head, (
        "the trigger's comment cites the card that added it"
    )


def test_the_nightly_still_runs_on_main_and_stays_a_push_and_pr_workflow():
    on = _on(_doc(TESTS))
    assert "pull_request" in on, "per-change runs are not replaced by the nightly"
    assert (on["push"] or {}).get("branches") == ["main"], (
        "the push trigger on main is unchanged"
    )


# ---------------------------------------------------------------------------
# 2. A schedule run cannot skip a job — `tdd` is the one named exemption.
# ---------------------------------------------------------------------------


def test_tdd_is_the_only_job_a_schedule_run_skips():
    doc = _doc(TESTS)
    assert jobs_a_schedule_run_would_skip(doc) == {THE_ONE_EXEMPTION}, (
        "a nightly must run every suite this workflow defines, with `tdd` the "
        "one named exemption (it reads a pull request's commit list, and a "
        "schedule run has no pull request). Any other job whose `if:` is "
        "false — or unreadable — for a schedule event is a silent hole in the "
        "nightly: gate it on something a schedule run satisfies, or name it "
        "here and in the card."
    )


def test_the_exemption_is_real_and_is_gated_on_a_pull_request():
    tdd = _doc(TESTS)["jobs"][THE_ONE_EXEMPTION]
    assert "pull_request" in str(tdd["if"]), (
        "the exemption is only defensible because the job needs a pull "
        f"request: {tdd.get('if')!r}"
    )
    assert not evaluate(tdd["if"]), "tdd must not claim to run on a schedule"


def test_the_suite_job_carries_no_gate_at_all():
    # The job that runs the whole suite is the nightly's entire point.
    assert "if" not in _doc(TESTS)["jobs"]["unit"], (
        "`unit` runs the WHOLE suite — it must be reachable by every event"
    )


def test_a_second_exemption_turns_this_red():
    # The guard against the guard: if a job is ever narrowed to pull requests,
    # the assertion above must notice rather than pass on an exemption nobody
    # declared.
    doc = _doc(TESTS)
    doc["jobs"]["unit"]["if"] = "github.event_name == 'pull_request'"
    assert jobs_a_schedule_run_would_skip(doc) == {THE_ONE_EXEMPTION, "unit"}


def test_an_unreadable_gate_is_reported_not_assumed_green():
    doc = _doc(TESTS)
    doc["jobs"]["unit"]["if"] = "fromJSON(vars.WHO_KNOWS).enabled"
    skipped = jobs_a_schedule_run_would_skip(doc)
    assert any(name.startswith("unit (unreadable") for name in skipped), skipped


@pytest.mark.parametrize(
    "expr,runs",
    [
        # The live shapes in this workflow.
        ("github.event_name == 'pull_request'", False),
        (
            "github.event_name != 'pull_request' || "
            "github.actor != 'dependabot[bot]'",
            True,
        ),
        # The shapes a later narrowing would reach for.
        ("github.event_name == 'schedule'", True),
        ("github.event_name != 'schedule'", False),
        ("${{ github.event_name == 'schedule' }}", True),
        ("github.ref == 'refs/heads/main'", True),
        ("github.event_name == 'push' && github.ref_name == 'main'", False),
        (
            "github.event_name == 'schedule' && github.ref_name == 'main'",
            True,
        ),
        ("!(github.event_name == 'schedule')", False),
        ("true", True),
        ("false", False),
    ],
)
def test_the_gate_reader_judges_the_shapes_it_must_judge(expr, runs):
    assert evaluate(expr) is runs, expr


def test_the_gate_reader_refuses_what_it_cannot_judge():
    with pytest.raises(Unreadable):
        evaluate("needs.build.result == 'success'")


def test_a_narrowed_job_runs_in_full_on_a_schedule_event():
    # `acts-consumers` narrows per change by reading the changed files, and a
    # schedule event gives its compare no base sha — so the narrowing's
    # fail-closed branch is the path a nightly takes, and it runs the job in
    # full. The card's rule: a narrowing must fail open for `schedule`.
    step = next(
        s for s in _doc(TESTS)["jobs"]["acts-consumers"]["steps"]
        if s.get("id") == "actsreg"
    )
    base = str(step["env"]["BASE"])
    assert "github.event.pull_request.base.sha" in base and (
        "github.event.before" in base
    ), base
    guard = step["run"]
    fail_branch = guard.rsplit("else", 1)[-1]
    assert "touched=true" in fail_branch, (
        "a compare it cannot read must run the job in full, not skip it:\n"
        f"{guard}"
    )


# ---------------------------------------------------------------------------
# 3. A red nightly is repaired — the rail is unchanged and still admits it.
# ---------------------------------------------------------------------------


def test_the_repair_rail_still_watches_pipeline_tests():
    watched = (_on(_doc(REPAIR_STUB))["workflow_run"])["workflows"]
    assert "Pipeline Tests" in watched, watched


def test_the_repair_rail_carries_no_filter_that_excludes_a_schedule_run():
    trigger = _on(_doc(REPAIR_STUB))["workflow_run"]
    assert set(trigger) <= {"workflows", "types"}, (
        "the rail keys on the watched workflow and its completion only; a "
        f"branch filter here could exclude the nightly: {sorted(trigger)}"
    )
    assert trigger.get("types") == ["completed"], trigger.get("types")
    for path in (REPAIR_STUB, REPAIR_STAGE):
        body = path.read_text(encoding="utf-8")
        assert "workflow_run.event" not in body, (
            f"{path.name} keys on the event that STARTED the watched run, so a "
            "nightly's failure would be filtered out; the gate is the "
            "conclusion and the head branch (standards/engineering.md rule 3)"
        )


def test_the_nightlys_head_branch_is_the_branch_the_repair_gate_requires():
    # A schedule run's head_branch is the default branch, which is exactly what
    # the reusable stage's job `if` demands — so the nightly reaches the rail.
    body = REPAIR_STAGE.read_text(encoding="utf-8")
    assert "github.event.workflow_run.conclusion == 'failure'" in body
    assert (
        "github.event.workflow_run.head_branch == "
        "github.event.repository.default_branch" in body
    )
    assert _on(_doc(TESTS))["push"]["branches"] == ["main"], (
        "the nightly runs from main, where the repair gate looks"
    )
