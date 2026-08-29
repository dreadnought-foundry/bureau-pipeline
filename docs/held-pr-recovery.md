# Releasing a held PR — one recovery, not two

When the fix loop runs out of budget it stops and posts a 🛑 hold on the PR:

> 🛑 Fix budget exhausted (3 attempts, including a fresh-eyes re-derivation) —
> holding for a human decision.

There is **one** way out of that state, and it is the one the hold comment
asks for.

## The recovery

Comment on the PR, from your own account, with a first line that starts with
the words **Operator decision**:

    **Operator decision** — <your answer here>

Any casing works, bold/plain/heading all work, and your answer can continue on
that same line or below it. It has to be newer than the hold and written by a
person, not a bot.

Then stop. The reconcile sweep reads your answer on its next pass — normally
within about fifteen minutes — and dispatches the fix loop for you
(`restart_answered_blockers`, DRE-2409). Your answer buys exactly one restart;
a further answer buys another.

## `gh workflow run agent-fix.yml` is not a second way out

The runbook habit — "if a PR is stuck, dispatch the fix workflow by hand" —
is **not an independent recovery, and it never was.** Once the attempt budget
is spent, a hand dispatch has no attempt to add: it resolves the budget, finds
it spent, and stops. Doing it anyway used to make things strictly worse.

**The incident (PR #199 / DRE-2721, 2026-08-29 — DRE-2813).** The sweep arms
on a simple rule: it restarts only when NO worker-bot comment is newer than
the answer, which is what stops one answer re-dispatching every fifteen
minutes forever. A hand dispatch at 15:29 fixed nothing, concluded `success`
— and posted a fresh 🛑 hold comment as the worker bot. That hold was newer
than the 15:27 answer, so at 15:33 the sweep correctly stood down. Permanently.
A correct answer, a healthy sweep, a working restart, and a PR that never
moved, with nothing anywhere saying so. Belt-and-braces left the operator
worse off than answering alone.

**What a hand dispatch does now.** If the budget is spent and an operator
decision is standing that the loop has not acted on, the run does nothing,
posts **no hold**, and posts one notice tagged `dispatch-no-work` saying the
answer is already standing and the sweep owns the restart. That tag is the one
worker-bot comment the arming rule ignores (`fix_context.decision_consumed`),
so the notice cannot cancel your answer. Every other worker-bot comment — a
fix attempt, a restart receipt, a fresh blocker — still consumes it.

The run also stops reporting an unqualified `success`: it executes a step
named **No work done (this dispatch could not act)** and writes a
`NO WORK DONE:` line to the run's annotations and job summary.

## What the sweep's own restart does

The sweep's dispatch is a machine dispatch (`gh workflow run` under the
workflow's `github.token` initiates as `github-actions`, DRE-2053), and it is
allowed to act: an operator decision re-arms **one** fix attempt, so the
answer buys real work rather than a repeat of the hold it was written against.
If that attempt does not satisfy the critic, the loop holds again and asks for
another answer.

## Quick reference

| You see | Do |
| -- | -- |
| 🛑 hold, no answer on the PR yet | Comment **Operator decision** — … and wait ~15 min |
| 🛑 hold, your answer is on the PR | Nothing. The sweep has it. |
| 🟡 `dispatch-no-work` notice | Nothing. Your answer is standing and still armed. |
| 🔓 restart receipt | Nothing. The fix loop is running again. |
| ⚠️ `operator-decision-near-miss` notice | Your comment did not parse — re-post it in the format above |

Related: `scripts/fix_budget.py` (the decision), `scripts/fix_context.py` (the
predicates), `scripts/reconcile.py::restart_answered_blockers` (the sweep),
`tests/test_hand_dispatch_no_work.py` (the live sequence, driven end to end).
