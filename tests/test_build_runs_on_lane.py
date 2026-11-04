"""The build runner lane: a suite-running job runs where the repo's CI runs
(DRE-4846).

On 2026-09-24 a build or fix agent that runs a product's own test suite was
SIGKILLed — exit 137, the kernel OOM killer — on a `bureau-mini-light-*` runner
(1 CPU, 2 GB, no swap) FOUR times at 17-26 minutes:

* portico DRE-4514, run 35950845844, attempts 1 AND 2, then fix run
  35954619710. The first attempt had reached `⏳ 3/5 implementation green`, and
  that work was lost; the pull request arrived carrying only its RED tests.
* agent-bureau DRE-4810, run 36093192182, attempt 1, 21:34 → 22:00 PT.

A fleet read of every QA Review run from 09-23 10:00 PT to 09-25 10:05 PT — 129
of them — put the critic in the same lane on 2026-09-25. It was killed the same
way THREE times, on both attempts each time, and all three pull requests touch
Portico `infra/`: portico #687 (run 35932479611), #699 (run 36089623608) and
#717 (run 36161433688, killed after eighteen minutes of real review). The
critic runs the product's suite itself — the DRE-3005 evidence rule makes it
paste the command and the output for any claim about a run — so it is exactly
the suite-running job this lane is for. Critics in bureau-pipeline, Atlas and
DeltaSolv, which set no runner variable and run on 7 GB hosted runners, were
never killed.

Both repos read `BUREAU_RUNS_ON` on these jobs, and in both it is the bare
`bureau-mini` set, so a suite-running agent landed on a light runner about half
the time. agent-bureau's `architecture/decisions/adr-owned-runner-fleet.md`
anticipated exactly this: "a light job over 1.5 GiB" is listed under "What
would change this record", and "The heavy class is where suites run".

The lane this file pins:

* `agent-task.yml`'s `execute`, `agent-fix.yml`'s `fix` and `qa-review.yml`'s
  `review` read `BUREAU_CI_RUNS_ON || BUREAU_RUNS_ON || '["ubuntu-latest"]'`.
  That is the long expression with ONE variable in front of it, and the
  variable is not a new one: it is each product repo's own CI variable, which
  agent-bureau's `ci.yml` already reads on every job. Read with `gh variable
  list` on 2026-09-24 at about 22:40 PT it was set on portico and agent-bureau
  (both naming `bureau-heavy`) and on agent-bureau-demo (the bare `bureau-mini`
  set, so the demo can still land on light); atlas and deltasolv set neither
  and stay on `ubuntu-latest`. Nobody has to set anything for this to take
  effect in the two repos that lost work.
* A fourth variable (`BUREAU_BUILD_RUNS_ON`) was deliberately NOT introduced.
  The console's pool fail-back (`console/backend/monitors/runner_pool.py` in
  agent-bureau) deletes exactly three variables when no runner is online —
  `BUREAU_RUNS_ON`, `BUREAU_CI_RUNS_ON`, `BUREAU_SHORT_RUNS_ON` — so a fourth
  would keep builds pinned to a dead pool. Reading the chain the fleet's CI
  already reads, byte for byte, is what makes the fail-back cover this lane.
* A repo that sets neither variable renders exactly what it renders today, and
  that is computed below rather than asserted.

The trade, stated: in agent-bureau and portico the build, fix and review jobs
(up to 120 minutes each) now share the three heavy runners with CI, so a review
can queue behind CI — Portico ran 63 reviews and agent-bureau 40 in the window
above. The ADR's own "heavy queueing" line is the trigger to revisit it, and
the lighter path for the critic is a separate card: have it cite CI's own run
instead of re-running the suite.

Verify, plan, the sweeps, the merge gate and the medic's `diagnose` keep their
chains unchanged — `tests/test_runs_on_switchable.py` and
`tests/test_short_runs_on_lane.py` hold them there.

On-demand machines (DRE-8890, epic DRE-8880). These three are the longest jobs
in the fleet, so a Spot machine AWS takes back mid-run costs the most here. The
variable cannot say so itself — it is the one each product repo's own CI reads,
and CI stays on Spot — so each job asks for it in its `runs-on`, the way
agent-bureau's `ci.yml` does on `push` (DRE-6879): when the value's first label
opens `runs-on`, RunsOn's `spot=false` ("will ensure that the runner is
launched with regular on-demand pricing", runs-on.com/configuration/job-labels/)
is added — one `/spot=false` segment on a single-string label, one
`spot=false` element on the array form. A value that already carries a `spot=`
is used as it stands, because the console's order writes `/spot=false` onto the
label during Spot pressure and a label must never carry the key twice. A value
that is not a RunsOn label (`ubuntu-latest`, the Mac pool, either variable
unset) renders exactly what it rendered before, and that is computed below
against the expression `main` carried, not asserted.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_runs_on_switchable import (  # noqa: E402
    BUILD_JOBS,
    BUILD_ON_DEMAND,
    BUILD_SWITCHABLE,
    SWITCHABLE,
    _is_reusable,
    _load,
    _runner_jobs,
    expected_runs_on,
)
from test_short_runs_on_lane import SHORT_VAR, render_runs_on  # noqa: E402

BUILD_VAR = "BUREAU_CI_RUNS_ON"

# The shape the two repos that lost work carry: `BUREAU_CI_RUNS_ON` naming the
# heavy class, `BUREAU_RUNS_ON` the bare mini set that includes the 2 GB light
# runners the four builds died on.
_BOTH_SET = {
    "BUREAU_CI_RUNS_ON": '["self-hosted", "linux", "arm64", "bureau-heavy"]',
    "BUREAU_RUNS_ON": '["self-hosted", "linux", "arm64", "bureau-mini"]',
}
_LONG_ONLY = {"BUREAU_RUNS_ON": '["self-hosted", "linux", "arm64", "bureau-mini"]'}


def _jobs(name: str) -> dict[str, str]:
    return dict(_runner_jobs(_load(WORKFLOWS / name)))


# --------------------------------------------------------------------------
# a reader for the expression, as GitHub evaluates it (DRE-8890)
# --------------------------------------------------------------------------
# `render_runs_on` reads one `fromJSON(a || b || '<json>')` chain and refuses
# anything else, which is what keeps it honest. The on-demand `runs-on` is a
# bigger expression, so it is read by this one: agent-bureau's reader from
# `scripts/test_runs_on_switchable.py` (DRE-6879), copied, with `contains`
# added. It takes the slice of GitHub's expression syntax these values use,
# with GitHub's rules where they matter here: `||` and `&&` return an operand,
# not a boolean; null, false, 0 and '' are falsy; `startsWith` and a string
# `contains` are case-insensitive; a missing property or an index past the
# end of an array is null; `format` replaces `{N}` and reads `{{`/`}}` as a
# brace; `join` joins an array's items with the separator.


def _tokens(text: str) -> list[tuple[str, object]]:
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch == "'":
            j, buf = i + 1, []
            while True:
                if text[j] == "'" and text[j + 1:j + 2] == "'":
                    buf.append("'")
                    j += 2
                elif text[j] == "'":
                    break
                else:
                    buf.append(text[j])
                    j += 1
            out.append(("str", "".join(buf)))
            i = j + 1
        elif text[i:i + 2] in ("||", "&&", "==", "!="):
            out.append(("op", text[i:i + 2]))
            i += 2
        elif ch in "()[].,!":
            out.append(("op", ch))
            i += 1
        elif ch.isdigit():
            j = i
            while j < len(text) and text[j].isdigit():
                j += 1
            out.append(("num", int(text[i:j])))
            i = j
        elif ch.isalpha() or ch == "_":
            j = i
            while j < len(text) and (text[j].isalnum() or text[j] in "_-"):
                j += 1
            out.append(("name", text[i:j]))
            i = j
        else:
            raise ValueError(f"unexpected {ch!r} at {i} in {text!r}")
    return out


def _truthy(value: object) -> bool:
    return value not in (None, False, 0, "")


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _equal(left: object, right: object) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left.casefold() == right.casefold()
    return left == right


def _call(name: str, args: list) -> object:
    name = name.lower()
    if name == "fromjson":
        return json.loads(args[0])
    if name == "startswith":
        return _text(args[0]).casefold().startswith(_text(args[1]).casefold())
    if name == "contains":
        if isinstance(args[0], list):
            return any(_equal(item, args[1]) for item in args[0])
        return _text(args[1]).casefold() in _text(args[0]).casefold()
    if name == "join":
        sep = _text(args[1]) if len(args) > 1 else ","
        return sep.join(_text(x) for x in args[0]) if isinstance(args[0], list) \
            else _text(args[0])
    if name == "format":
        fmt, out, i = args[0], [], 0
        while i < len(fmt):
            if fmt[i:i + 2] in ("{{", "}}"):
                out.append(fmt[i])
                i += 2
            elif fmt[i] == "{":
                j = fmt.index("}", i)
                out.append(_text(args[1 + int(fmt[i + 1:j])]))
                i = j + 1
            else:
                out.append(fmt[i])
                i += 1
        return "".join(out)
    raise ValueError(f"no function {name!r} in this reader")


def _evaluate(expression: str, context: dict) -> object:
    """The value GitHub gives a `${{ … }}` value under ``context``."""
    text = expression.strip()
    assert text.startswith("${{") and text.endswith("}}"), text
    toks, pos = _tokens(text[3:-2]), 0

    def peek(value=None):
        if pos < len(toks) and (value is None or toks[pos][1] == value):
            return toks[pos]
        return None

    def take(value=None):
        nonlocal pos
        tok = peek(value)
        assert tok is not None, f"expected {value!r} at token {pos} of {text!r}"
        pos += 1
        return tok

    def either():
        left = both()
        while peek("||"):
            take()
            right = both()
            left = left if _truthy(left) else right
        return left

    def both():
        left = compare()
        while peek("&&"):
            take()
            right = compare()
            left = right if _truthy(left) else left
        return left

    def compare():
        left = unary()
        while peek("==") or peek("!="):
            op = take()[1]
            right = unary()
            left = _equal(left, right) == (op == "==")
        return left

    def unary():
        if peek("!"):
            take()
            return not _truthy(unary())
        return postfix()

    def postfix():
        value = primary()
        while peek(".") or peek("["):
            if take()[1] == ".":
                key = take()[1]
                value = next((v for k, v in value.items() if k.lower() == key.lower()),
                             None) if isinstance(value, dict) else None
            else:
                index = either()
                take("]")
                if isinstance(value, list) and isinstance(index, int):
                    value = value[index] if 0 <= index < len(value) else None
                elif isinstance(value, dict) and isinstance(index, str):
                    value = value.get(index)
                else:
                    value = None
        return value

    def primary():
        kind, value = take()
        if value == "(" and kind == "op":
            inner = either()
            take(")")
            return inner
        if kind in ("str", "num"):
            return value
        if kind == "name" and peek("("):
            take("(")
            args = []
            while not peek(")"):
                args.append(either())
                if peek(","):
                    take(",")
            take(")")
            return _call(value, args)
        if kind == "name":
            return {"true": True, "false": False, "null": None}.get(
                value, context.get(value))
        raise ValueError(f"unexpected {value!r} in {text!r}")

    result = either()
    assert pos == len(toks), f"trailing tokens in {text!r}"
    return result


def _render(expr: str, variables: dict[str, str]) -> object:
    """What a job's `runs-on` renders with these repository variables set."""
    return _evaluate(expr, {"vars": dict(variables)})


def test_the_reader_reads_the_chain_as_render_runs_on_does() -> None:
    """The reader is the proof below, so it is checked first against the
    chain reader the lane tests already trust, on every value this file
    reasons about."""
    for variables in _STATES:
        assert _render(BUILD_SWITCHABLE, variables) == \
            render_runs_on(BUILD_SWITCHABLE, variables), variables
        assert _render(SWITCHABLE, variables) == \
            render_runs_on(SWITCHABLE, variables), variables
    assert _render("${{ fromJSON('[\"a\"]')[1] }}", {}) is None
    assert _render("${{ format('[\"{0}\"]{{x}}', 'b') }}", {}) == '["b"]{x}'
    assert _render("${{ contains('a/SPOT=false', 'spot=') && 'y' || 'n' }}", {}) == "y"
    assert _render("${{ !contains('a/b', 'spot=') && 'y' || 'n' }}", {}) == "y"


# --------------------------------------------------------------------------
# the lane is a named list of jobs
# --------------------------------------------------------------------------
def test_the_build_lane_is_exactly_three_jobs() -> None:
    """The scope is the card's, named job by job: the two jobs that build
    (DRE-4846's headline) and the critic the 2026-09-25 amendment added.
    Widening it again is a decision, not a refactor."""
    assert BUILD_JOBS == {
        "agent-task.yml": {"execute"},
        "agent-fix.yml": {"fix"},
        "qa-review.yml": {"review"},
    }
    # Among the reusables only: this repo's own workflows read the same
    # chains from its own variables (DRE-5948), pinned in
    # tests/test_own_runs_on_lane.py. A `CI_RUNS_ON:` env line is not a job on
    # the lane: it hands the column's value to a re-run gate (DRE-8817) and
    # runs nothing there.
    mentions = {
        path.name
        for path in sorted(WORKFLOWS.glob("*.yml"))
        if _is_reusable(_load(path)) and any(
            BUILD_VAR in line and not line.lstrip().startswith("CI_RUNS_ON:")
            for line in path.read_text().splitlines())
    }
    assert mentions == set(BUILD_JOBS), (
        f"only {sorted(BUILD_JOBS)} may name {BUILD_VAR}; found {sorted(mentions)}"
    )


@pytest.mark.parametrize("name", sorted(BUILD_JOBS))
def test_every_build_job_reads_the_ci_variable_first(name: str) -> None:
    wrong = {
        job_id: runs_on
        for job_id, runs_on in _jobs(name).items()
        if job_id in BUILD_JOBS[name] and runs_on != BUILD_ON_DEMAND
    }
    assert not wrong, (
        f"{name}: every build-lane job must read {BUILD_ON_DEMAND} so a job "
        f"running a product suite lands where that repo's CI runs, on a "
        f"machine AWS cannot take back. Wrong: {wrong}"
    )


@pytest.mark.parametrize("name", sorted(BUILD_JOBS))
def test_no_build_lane_file_reads_the_short_variable(name: str) -> None:
    """The build lane widens the chain in the other direction from the short
    lane; a suite-running job pointed at the sub-minute pool would be the
    DRE-3875 incident with the lanes swapped."""
    assert SHORT_VAR not in (WORKFLOWS / name).read_text(), (
        f"{name} runs a product test suite; it must never read {SHORT_VAR}"
    )


# --------------------------------------------------------------------------
# what each of the three variable states renders — computed, not asserted
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(BUILD_JOBS))
def test_with_no_variables_set_the_build_jobs_stay_hosted(name: str) -> None:
    """The card's first rendering criterion: a repo that sets neither variable
    — atlas and deltasolv today — renders exactly what it renders today."""
    for job_id, runs_on in _jobs(name).items():
        assert _render(runs_on, {}) == ["ubuntu-latest"], (
            f"{name}:{job_id} does not default to the hosted runner"
        )


@pytest.mark.parametrize("name", sorted(BUILD_JOBS))
def test_with_only_the_long_variable_set_nothing_moves(name: str) -> None:
    """The second: with `BUREAU_CI_RUNS_ON` unset, every job in the file — on
    the build lane or not — renders what the long expression renders, so the
    change is inert until a repo has the CI variable set."""
    today = render_runs_on(SWITCHABLE, _LONG_ONLY)
    for job_id, runs_on in _jobs(name).items():
        assert _render(runs_on, _LONG_ONLY) == today, (
            f"{name}:{job_id} changed where it runs for a repo that has not "
            f"set {BUILD_VAR}"
        )


@pytest.mark.parametrize("name", sorted(BUILD_JOBS))
def test_the_ci_variable_wins_when_both_are_set(name: str) -> None:
    """The third, and the point of the card: with both set — portico and
    agent-bureau today — the suite-running job lands on the heavy class
    instead of the mini set that includes the 2 GB light runners."""
    build = render_runs_on(BUILD_SWITCHABLE, _BOTH_SET)
    long = render_runs_on(SWITCHABLE, _BOTH_SET)
    assert build == ["self-hosted", "linux", "arm64", "bureau-heavy"]
    assert long == ["self-hosted", "linux", "arm64", "bureau-mini"]
    for job_id, runs_on in _jobs(name).items():
        want = build if job_id in BUILD_JOBS[name] else long
        assert _render(runs_on, _BOTH_SET) == want, (
            f"{name}:{job_id} rendered {_render(runs_on, _BOTH_SET)}, "
            f"expected {want} (expression {expected_runs_on(name, job_id)})"
        )


# --------------------------------------------------------------------------
# a RunsOn label asks for a machine AWS cannot take back (DRE-8890)
# --------------------------------------------------------------------------
#: The label the card names: RunsOn's single-string form with a family.
RUNS_ON_LABEL = "runs-on/runner=2cpu-linux-arm64/family=m9g"
#: The same label as the console's order writes it during Spot pressure.
RUNS_ON_ON_DEMAND = f"{RUNS_ON_LABEL}/spot=false"
#: RunsOn's array form, one label per element.
RUNS_ON_ARRAY = ["runs-on=1", "runner=2cpu-linux-arm64", "family=m9g"]
#: The two Mac pool classes the fleet's variables name.
MAC_HEAVY = ["self-hosted", "linux", "arm64", "bureau-heavy"]
MAC_BOTH = ["self-hosted", "linux", "arm64", "bureau-mini", "bureau-heavy"]

#: Every variable state this file reasons about, both unset first.
_STATES = (
    {},
    {"BUREAU_CI_RUNS_ON": json.dumps(["ubuntu-latest"])},
    {"BUREAU_CI_RUNS_ON": json.dumps(MAC_BOTH)},
    {"BUREAU_RUNS_ON": json.dumps(MAC_BOTH)},
    _BOTH_SET,
    _LONG_ONLY,
    {"BUREAU_CI_RUNS_ON": json.dumps([RUNS_ON_LABEL])},
    {"BUREAU_CI_RUNS_ON": json.dumps([RUNS_ON_ON_DEMAND])},
    {"BUREAU_RUNS_ON": json.dumps([RUNS_ON_LABEL])},
    {"BUREAU_CI_RUNS_ON": json.dumps(RUNS_ON_ARRAY)},
)


def _lane_jobs() -> list[tuple[str, str, str]]:
    """(file, job, its `runs-on` as the file carries it) for the three jobs."""
    found = [
        (name, job_id, runs_on)
        for name in sorted(BUILD_JOBS)
        for job_id, runs_on in _jobs(name).items()
        if job_id in BUILD_JOBS[name]
    ]
    assert len(found) == 3, found
    return found


_LANE = _lane_jobs()
_IDS = [f"{name}:{job_id}" for name, job_id, _ in _LANE]


@pytest.mark.parametrize(("name", "job_id", "runs_on"), _LANE, ids=_IDS)
def test_a_runs_on_label_asks_for_an_on_demand_machine(
        name: str, job_id: str, runs_on: str) -> None:
    """The card's first criterion, on the label it names."""
    got = _render(runs_on, {"BUREAU_CI_RUNS_ON": json.dumps([RUNS_ON_LABEL])})
    assert got == ["runs-on/runner=2cpu-linux-arm64/family=m9g/spot=false"], (
        f"{name}:{job_id} rendered {got}"
    )


@pytest.mark.parametrize(("name", "job_id", "runs_on"), _LANE, ids=_IDS)
def test_a_label_that_already_says_spot_is_used_as_it_stands(
        name: str, job_id: str, runs_on: str) -> None:
    """The second: the console's order has already written `/spot=false`, and
    a second `spot=` on one label is a label RunsOn has to guess at."""
    got = _render(runs_on, {"BUREAU_CI_RUNS_ON": json.dumps([RUNS_ON_ON_DEMAND])})
    assert got == [RUNS_ON_ON_DEMAND], f"{name}:{job_id} rendered {got}"
    assert "".join(got).count("spot=") == 1


@pytest.mark.parametrize(("name", "job_id", "runs_on"), _LANE, ids=_IDS)
@pytest.mark.parametrize("variables", [
    {"BUREAU_CI_RUNS_ON": json.dumps(["ubuntu-latest"])},
    {"BUREAU_CI_RUNS_ON": json.dumps(MAC_BOTH)},
    {},
], ids=["ubuntu-latest", "mac-pool", "both-unset"])
def test_a_value_that_is_not_a_runs_on_label_renders_what_main_did(
        name: str, job_id: str, runs_on: str, variables: dict) -> None:
    """The third: `ubuntu-latest`, the Mac pool classes and no variable at all
    render exactly what the chain `main` carried renders — computed by the
    chain's own reader, not written down."""
    today = render_runs_on(BUILD_SWITCHABLE, variables)
    assert _render(runs_on, variables) == today, f"{name}:{job_id}"
    assert "spot=false" not in today


@pytest.mark.parametrize(("name", "job_id", "runs_on"), _LANE, ids=_IDS)
def test_the_fleet_variable_is_read_the_same_way_when_the_ci_one_is_unset(
        name: str, job_id: str, runs_on: str) -> None:
    """The fourth: `BUREAU_CI_RUNS_ON` unset, `BUREAU_RUNS_ON` a RunsOn label."""
    got = _render(runs_on, {"BUREAU_RUNS_ON": json.dumps([RUNS_ON_LABEL])})
    assert got == [f"{RUNS_ON_LABEL}/spot=false"], f"{name}:{job_id} rendered {got}"
    got = _render(runs_on, {"BUREAU_RUNS_ON": json.dumps([RUNS_ON_LABEL]),
                            "BUREAU_CI_RUNS_ON": json.dumps(MAC_HEAVY)})
    assert got == MAC_HEAVY, "the CI variable still wins, and the Mac pool gains nothing"


@pytest.mark.parametrize(("name", "job_id", "runs_on"), _LANE, ids=_IDS)
def test_the_array_form_gains_one_element(
        name: str, job_id: str, runs_on: str) -> None:
    got = _render(runs_on, {"BUREAU_CI_RUNS_ON": json.dumps(RUNS_ON_ARRAY)})
    assert got == [*RUNS_ON_ARRAY, "spot=false"], f"{name}:{job_id} rendered {got}"


@pytest.mark.parametrize(("name", "job_id", "runs_on"), _LANE, ids=_IDS)
def test_an_array_that_already_says_spot_is_used_as_it_stands(
        name: str, job_id: str, runs_on: str) -> None:
    """The guard reads the whole value, so the array form never carries the
    key twice either."""
    value = [*RUNS_ON_ARRAY, "spot=false"]
    got = _render(runs_on, {"BUREAU_CI_RUNS_ON": json.dumps(value)})
    assert got == value, f"{name}:{job_id} rendered {got}"


@pytest.mark.parametrize(("name", "job_id", "runs_on"), _LANE, ids=_IDS)
def test_only_a_runs_on_label_changes_the_value(
        name: str, job_id: str, runs_on: str) -> None:
    """Over every state this file reasons about, the job renders what `main`'s
    chain rendered except exactly where the value is a RunsOn label with no
    `spot=` yet — so nothing else moved by accident."""
    changed = {
        json.dumps(variables, sort_keys=True)
        for variables in _STATES
        if _render(runs_on, variables) != render_runs_on(BUILD_SWITCHABLE, variables)
    }
    assert changed == {
        json.dumps(variables, sort_keys=True) for variables in (
            {"BUREAU_CI_RUNS_ON": json.dumps([RUNS_ON_LABEL])},
            {"BUREAU_RUNS_ON": json.dumps([RUNS_ON_LABEL])},
            {"BUREAU_CI_RUNS_ON": json.dumps(RUNS_ON_ARRAY)},
        )
    }, f"{name}:{job_id}"


@pytest.mark.parametrize("name", sorted(BUILD_JOBS))
def test_the_lane_comment_says_why_it_is_on_demand(name: str) -> None:
    """Each job keeps its comment on the lane, with the sentence on why it is
    on-demand beside it."""
    text = (WORKFLOWS / name).read_text()
    head = text[:text.index("    runs-on: >-\n      ${{ fromJSON(startsWith(")]
    comment = head[head.rindex("The BUILD lane (DRE-4846)"):]
    assert "DRE-8890" in comment and "spot=false" in comment, name
