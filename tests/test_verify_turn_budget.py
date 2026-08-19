"""RED-first tests for the VERIFIER's turn budget (measured 2026-08-19).

The third sibling of ``test_critic_turn_budget.py`` (DRE-2422) and
``test_fix_turn_budget.py`` (DRE-2533). Same defect, third agent.

WHAT WAS MEASURED, not inferred. agent-bureau PR #2064's verify run:

    attempt 1: "subtype": "error_max_turns", "is_error": true,
               "num_turns": 81, "total_cost_usd": 3.898     -> NO VERDICT
    backoff  : 120s
    retry    : "subtype": "success", "num_turns": 77,
               "total_cost_usd": 2.921                      -> PASS

`verify.yml` gives attempt 1 **80** turns and the retry **120**. So attempt 1
was cut off ONE TURN past its ceiling, while doing nothing wrong: no rate
limit, no infra fault, no hang. It simply needed 81 and was allowed 80.

**The retry then finished in 77 — BELOW the ceiling that denied attempt 1.**
The work was never too big for 80-ish turns; the spread between two runs on
byte-identical content (77 vs 81, the content hash was `92d4e99b` both times)
is about as wide as the margin the ceiling leaves.

THE DISTRIBUTION IS THE ARGUMENT. First-attempt turn counts, agent-bureau,
2026-08-19:

    7, 61, 65, 81        ceiling: 80

The ceiling sits INSIDE the range of real work. Three passed — two of them at
61 and 65, i.e. with 15 and 19 turns of headroom. By the standard DRE-2422
set ("a run that SUCCEEDS at 41 has exactly zero margin left"), that is
already too tight. The 81 is the one that got caught; the 65 was one
unlucky exploration from the same fate.

Three of four passing first try is exactly why this survived unnoticed: not
broken every time, just often enough to be expensive and rarely enough to
look like bad luck.

THE COST, per PR, when the ceiling bites:

    $3.90 and 14.1 minutes of inference, discarded, for $2.92 of useful work.

And the wall-clock consequence measured on #2064: APPROVE -> merge took
**48m22s**, of which verify was ~47 — two GitHub job attempts, each running a
doomed 80-turn attempt before its 120-turn retry. With attempt 1 able to
finish, the same merge is ~11 minutes. CI was 3m55s and never the problem.

HONEST LIMIT OF THIS EVIDENCE: four data points, one repo. portico, vericorr
and atlas returned no verify runs carrying turn counts in the sampled window.
That is enough to prove the ceiling sits inside the distribution; it is NOT
enough to tune a precise new number. So the assertions below demand real
headroom over the observed maximum rather than a fitted value — deliberately
generous, because the sample is thin and a ceiling costs nothing when unused.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"

# The highest FIRST-ATTEMPT turn count observed (agent-bureau PR #2064). It
# died at this number against a ceiling of 80.
OBSERVED_MAX_FIRST_ATTEMPT = 81

# The turn count the RETRY needed for the same content, once allowed 120. Proof
# the work fits comfortably above the old ceiling rather than being unbounded.
OBSERVED_RETRY_SUCCESS = 77

_TURNS_RE = re.compile(r"--max-turns\s+(\d+)")


def _step(workflow: str, job: str, step_id: str) -> dict:
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text())
    for step in doc["jobs"][job]["steps"]:
        if step.get("id") == step_id:
            return step
    raise AssertionError(
        f"no step id={step_id!r} in {workflow} job {job!r} — if the step was "
        f"renamed, update this test rather than deleting it; the budget it "
        f"pins is what stops a doomed first attempt being paid for on every PR"
    )


def _max_turns(workflow: str, job: str, step_id: str) -> int:
    args = _step(workflow, job, step_id)["with"]["claude_args"]
    m = _TURNS_RE.search(args)
    assert m, f"{workflow}:{step_id} declares no --max-turns: {args!r}"
    return int(m.group(1))


def _timeout_minutes(workflow: str, job: str) -> int:
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text())
    t = doc["jobs"][job].get("timeout-minutes")
    assert t is not None, (
        f"{workflow} job {job!r} declares no timeout-minutes — GitHub then "
        f"applies its 360-minute default, which is not a budget anyone chose"
    )
    return int(t)


def test_the_first_attempt_clears_the_observed_maximum_with_headroom():
    """THE defect. Attempt 1 ran at 80 while real work reached 81.

    A first attempt that cannot finish is not a cheap probe — it is a full
    agent run charged in money and wall clock, producing nothing. It must
    clear the observed maximum with margin, not sit inside the distribution.
    """
    first = _max_turns("verify.yml", "verify", "verifier")
    assert first > OBSERVED_MAX_FIRST_ATTEMPT, (
        f"verify attempt 1 runs with --max-turns {first}, but a real first "
        f"attempt reached {OBSERVED_MAX_FIRST_ATTEMPT} turns and died one turn "
        f"past its ceiling (PR #2064: $3.90 and 14.1 minutes, no verdict). "
        f"Observed first attempts: 7, 61, 65, 81 — the ceiling must sit ABOVE "
        f"that range, not inside it."
    )


def test_the_first_attempt_has_real_margin_over_a_known_good_run():
    """Not just above the maximum — above it with room.

    The retry finished the SAME content in 77 turns. Two runs on identical
    input differed by 4 turns, so a ceiling within a few turns of observed
    work is a coin flip rather than a limit. DRE-2422's standard: zero margin
    is not a budget.
    """
    first = _max_turns("verify.yml", "verify", "verifier")
    assert first >= OBSERVED_RETRY_SUCCESS * 1.5, (
        f"verify attempt 1 runs with {first} turns against known-good work at "
        f"{OBSERVED_RETRY_SUCCESS}. Run-to-run spread on byte-identical "
        f"content was 77 vs 81, so the margin must exceed that spread by a "
        f"wide margin — a ceiling is free when unused."
    )


def test_the_retry_stays_strictly_higher_than_the_first_attempt():
    """DRE-2429's invariant, preserved. A retry that exists to recover from
    exhausting a resource must come back with more of it — otherwise it
    re-runs the same review, on the same diff, into the same wall, and
    error_max_turns is DETERMINISTIC."""
    first = _max_turns("verify.yml", "verify", "verifier")
    retry = _max_turns("verify.yml", "verify", "verifier_retry")
    assert retry > first, (
        f"verify retry has {retry} turns and attempt 1 has {first}. The retry "
        f"must be STRICTLY higher or it cannot change the outcome."
    )


def test_the_job_has_the_wall_clock_to_spend_both_budgets():
    """A turn ceiling the clock cannot reach is a raise on paper only.

    The job dies at whichever cap comes first. #2064 measured 14.1m for an
    81-turn attempt — roughly 10s/turn — so attempt 1 + a 120s backoff + the
    retry must all fit inside the timeout, or the fix converts
    error_max_turns into a job TIMEOUT. That is strictly worse: a timeout
    cancels the `if: always()` neutral-status post, so the PR strands with no
    verdict AND no explanation (verify.yml's own comment says so).
    """
    first = _max_turns("verify.yml", "verify", "verifier")
    retry = _max_turns("verify.yml", "verify", "verifier_retry")
    wall = _timeout_minutes("verify.yml", "verify")
    # ~10s/turn measured (14.1 min / 81 turns), plus the 2-minute backoff.
    needed = (first + retry) * 10 / 60 + 2
    assert wall >= needed, (
        f"verify's job dies at {wall} minutes but budgets {first}+{retry} "
        f"turns, which at the measured ~10s/turn needs about {needed:.0f} "
        f"minutes including the 120s backoff. Raise the clock with the turns "
        f"or the crash just changes its name."
    )
