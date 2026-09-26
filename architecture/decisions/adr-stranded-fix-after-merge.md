# ADR — a fix run that finishes after its pull request merged (DRE-4486)

**Status:** accepted, 2026-09-21. **Decider:** the CEO, by signed console
answer on DRE-4486 (2026-09-21 14:05 PT). **Supersedes:** nothing — this is
the first time the class has been written down as a class.

## The failure

A fix run can still be working a pull request when the merge gate merges it.
When the fix finishes it pushes to the PR's branch — which has already merged.
The fix is on `origin`, reviewed and measured, and on no path to the default
branch. The card reads Done. Nothing says otherwise.

It has happened at least four times:

| Where | PR merged | Fix pushed | Outcome |
| -- | -- | -- | -- |
| portico #611 ([DRE-4183]) | 08:34:12Z, by `agent-bureau-qa-bot`, 22s after its own APPROVE | `9f1b7542` at 08:43:39Z — **+9 min** | **a live bug**: every photo portal's sign-in page draws two stacked veils on a phone. Refiled as [DRE-4460] |
| portico #351 ([DRE-2637]) | 18:55:47Z, by `agent-bureau-qa-bot` | `72964abd` at 19:00:04Z — **+4 min** | harmless only by luck — later work redid the same fix |
| [DRE-2227] → recovered as [DRE-2989] | — | post-merge fix | *"stranded on its branch, and the card reads Done"* |
| recovered as [DRE-2591] | — | — | *"stranded a better version on a dead branch"* |

**Every one of those four was handled as a RECOVERY. None filed the
PREVENTION, which is why it recurred.** That is the finding this ADR exists
for as much as the mechanism below: the class had been diagnosed four times
and closed zero times.

## The diagnosis — which event let the PR merge while its fix run was live

**Established, from timestamps** (the card's own evidence, recorded from the
2026-09-21 stale-branch audit of `dreadnought-foundry/portico`): in both
portico cases the qa-bot merged, and a `fix(…): address review findings
(attempt 1)` commit was pushed to the branch afterwards.

**Not established, and deliberately not guessed at.** The exact sequence —
whether a re-review ran on a head that did not carry the fix, or the fix run
simply outlived the trigger that started it — could not be read off the run
logs. This work was built from `dreadnought-foundry/bureau-pipeline`, whose
token has no read access to `dreadnought-foundry/portico`, and both runs are
well past GitHub's Actions log retention in any case. The CEO's answer names
this outcome explicitly: *"write down what the portico run logs show, and if
they cannot say, say so. Do not block the fix on it."* This paragraph is that.

**What IS established, and is what the remedy needs, comes from reading the
gate rather than the logs.** `scripts/merge_gate.py` asked every question
there is to ask about a pull request's head — conflict, dependabot policy, CI
green, a SHA-bound critic APPROVE, the verifier, draft — and never asked
whether a fix run was still working on it. That is provable off the code, and
it is reproduced as a test rather than asserted:
`tests/test_stranded_fix.py::GateRefusesWhileAFixRunIsLive::
test_the_race_merges_today_without_condition_f` drives green CI and a bound
APPROVE with the lane record withheld — the pre-DRE-4486 caller — and asserts
the gate decides `merge`. It is the mutation check for everything below: if
that test ever stops merging, the rest is proving something else.

The mirror direction was already closed and is worth naming, because it is
why this looked covered. `merge-gate.yml` merges with `--match-head-commit
"$SHA"`, so a fix that pushes **before** the merge moves the head and GitHub
refuses the merge (HTTP 405). Only the **after** direction was open, and
nothing in the pipeline was watching it.

## The decision

The card offered `either` the gate refuses `or` the fix run re-routes. The
CEO's signed answer chose **both, plus the detector**:

> Prevent it at the gate: the merge gate does not merge a PR while a fix run
> is queued or running for it. If a fix run finds its PR already merged, it
> does not push; it files a card naming the stranded commits. Keep the
> reappearing-branch detector.

Three mechanisms, in the order they act:

1. **The gate does not merge while a fix run is live for the pull request.**
   `merge_gate.evaluate_fix_lane` — condition F, evaluated 0 → D → 1 → 2 → 3
   → **F** → 4. `merge-gate.yml` gathers the lane with `stranded_fix.py lane`
   and threads it as `--fix-lane-file`. This is the prevention; the other two
   are what stands when it does not hold.
2. **A fix run that finds its pull request already merged does not push
   there.** `stranded_fix.push_decision`, driven from a git `pre-push` hook
   `agent-fix.yml` installs into the PR checkout **before** the agent starts.
   `git push` is the act to refuse, so it is refused at git's own seam — a
   prompt instruction is not a mechanism. The run then files a one-off card
   naming the commits, and reports nothing on the merged pull request.
3. **A merged pull request whose head branch carries commits dated after the
   merge is reported once, naming them.** `stranded_fix.detect`, swept by
   `reconcile.flag_stranded_fixes` every ~15 minutes, filing one card per
   branch.

## Why three and not one

Condition F is not airtight by itself, and saying so is the reason the other
two exist. The gate reads the lane and merges moments later, so a fix run
queued inside that window is unseen — a narrow time-of-check/time-of-use
window that no amount of re-reading closes, because the merge is a separate
call. The guard closes it from the other end. And the detector needs no
knowledge of why the race happened at all, which is what makes it the one
piece that also covers a repo riding a release tag older than this change:
the fleet consumes tagged releases, so a merge here is not live in the fleet
until a human cuts `vN` (`docs/self-hosting.md`).

## Why the detector reads a commit date, not a reappearing branch

The audit found these by noticing merged branches that should not exist:
portico has `delete_branch_on_merge` **on**, GitHub deletes the branch at the
merge, and the late push **recreates** it. That tell is real, and the card is
right that a merged PR whose branch reappears is a stranded fix by
construction.

It is also repo-configuration-dependent — a repo with auto-delete off leaves
every merged branch standing — so the detector reads the stronger fact
underneath it: **a commit on the branch, absent from the base, committed
after the merge**. Nothing but a push after the merge produces one. An
ordinary un-deleted branch of a squash merge is ahead of its base forever and
every commit on it predates the merge, so it never alarms. The configuration
decides how visible the symptom is; it never decides whether the finding is
true.

## The fail directions, which are not the same in all three

- **The gate fails CLOSED.** An unreadable lane record, or an in-flight run
  GitHub attributes to no pull request, is `wait`. A wait costs one gate
  wake; the direction that reads a blip as "no fix run running" is the
  failure itself. Attribution is exact where GitHub gives it — the PR number
  survives into the Actions API in a stub's run-name (`Agent Fix #N`,
  `fix_concurrency.RUN_NAME_PREFIX`, DRE-4845), which is read first and names
  even a run still pending, and otherwise only in the job name
  (`fix_concurrency.JOB_PR_PREFIX`, DRE-2908) — and "unattributed" means
  *could be any pull request*, never *not this one*.
- **The hook fails OPEN.** It runs on every push of every fix run, and a hook
  that refused on an API blip would break the loop it protects. It announces
  the unproven read rather than swallowing it.
- **The detector says nothing it cannot prove.** No merge time, no commit
  date, no readable compare, no finding. It only ever ADDS a card, so a
  fabricated finding is the only way it can do harm.

## What it costs, named rather than left to be discovered

A pull request whose repo has a fix run in flight waits for it. Bounded by
`agent-fix.yml`'s own job timeout in the worst case, and by the fix run's
real duration in the ordinary one; the gate re-wakes on the fix run's own
events and on reconcile's ~15-minute nudge, so nothing is stranded by the
wait. There is no starvation across pull requests: the lane is read per PR,
the DRE-2908 reading, so a fix run on #612 says nothing about #611.

[DRE-4183]: https://linear.app/dreadnoughtfoundry/issue/DRE-4183
[DRE-4460]: https://linear.app/dreadnoughtfoundry/issue/DRE-4460
[DRE-2637]: https://linear.app/dreadnoughtfoundry/issue/DRE-2637
[DRE-2227]: https://linear.app/dreadnoughtfoundry/issue/DRE-2227
[DRE-2989]: https://linear.app/dreadnoughtfoundry/issue/DRE-2989
[DRE-2591]: https://linear.app/dreadnoughtfoundry/issue/DRE-2591
