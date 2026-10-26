# The Sunday job — `groom-week.yml`

Once a week, before the 06:30 PT Sunday briefing, one workflow scores the
groomer's week and grows the replay set the verify step is checked against
(DRE-7975, epic DRE-6648). It is the only scheduled runner of
`groom_scorecard.py score` (DRE-7970), `groom_replay_cases.py build`
(DRE-7971) and `groom_replay_gate.py record-main` (DRE-7972).

## When

`schedule: - cron: "11 10 * * 0"` — Sunday **03:11 PT** in daylight time and
**02:11 PT** in standard time. GitHub's cron is UTC with no timezone field, so
the Pacific time moves an hour at each DST change; both are well before the
briefing.

The minute is chosen, not left to chance (`standards/engineering.md`, CI
rule 2): off the hour, more than an hour clear of the train stubs' 12:00 and
13:00 UTC crons, and on a minute no other workflow in this repo fires on at
any hour. `tests/test_groom_week_wiring.py` expands every other workflow's
cron minute field and fails, naming the file, if one of them lands on 11.

## What it does, and what it writes where

Three jobs:

1. **`count`** reads `tests/fixtures/groom-replay-cases/cases.json` on `main`
   and outputs how many cases it holds, and the instant the replay starts.
   The shipped seed case means the set is never empty, so the replay runs from
   the first Sunday.
2. **`replay-main`** calls `groomer.yml` with `pipeline_ref: main`,
   `mode: propose`, `judgement: "off"`, `capacity: "20"`, the replay set
   (`replay-set.json`) and no `card`. One verify leg per case card checks
   where each case ends on `main`. With `replay` set the groomer queues in the
   run's own replay groups (DRE-7974), so it never waits behind the 06:00 PT
   morning run.
3. **`week`** runs whatever `replay-main` did (`if: always()`):
   - **the scorecard** — `groom_scorecard.py score --card
     "${{ vars.GROOM_PROPOSAL_CARD }}" --post` posts the week's
     `📊 groom-scorecard: <week_start>..<week_end>` comment on the standing
     groom card. It reads the console's record of decisions through the read
     door with this run's OIDC token (`BUREAU_READ_URL`,
     `BUREAU_READ_AUDIENCE`, `BUREAU_PIPELINE_REF`), and from the card's
     thread alone, saying so, when the door does not answer.
   - **the results on `main`** — only when `replay-main` succeeded: the run's
     `groom-proposal-verified` artifact goes to `groom_replay_gate.py
     record-main`, which rewrites each case's `main` block in `cases.json`.
     When the replay failed, the log says `groom-replay-gate: main replay did
     not finish — results not recorded` and nothing is recorded.
   - **the new cases** — `groom_replay_cases.py build` adds the week's
     recorded removals to `cases.json` and rewrites `replay-set.json` and
     `gate-set.json` from the `main` blocks just recorded.
   - **the pull request** — when any file under
     `tests/fixtures/groom-replay-cases/` changed, one pull request to `main`
     from `agent/groom-week-<week_end>`, titled `groom-week: replay cases for the
     week ending <week_end> — <k> new, <n> recorded on main`. Its body lists
     each new case with its card, `expect` and reason, and each case whose
     result on `main` changed.

So it writes in three places: one comment on the standing groom card, one
branch, and one pull request. Nothing is pushed to `main` — the replay set
grows by review, and the critic and the merge gate read the pull request like
any other. The commit holds only test paths, so the TDD check asks nothing of
it, and no verify-step path, so the replay gate's `reach` is false for it.

The pull request is opened with the bureau App's token, as
`model-adoption.yml`'s pin jobs open theirs, and from a card-less `agent/`
branch as theirs are: the merge gate, the fix loop and reconcile act only on
the pipeline's own branches, so a `groom-week/` branch would never merge.
A branch a crashed run left behind is reused; a pull request already open
for the branch is updated, never duplicated.

## Re-running a week

Run **Groom Week** from Actions (`workflow_dispatch`) on `main`. Leave
`week_ending` empty for the week that ended last Saturday, or give the
Saturday a week ended on, `YYYY-MM-DD`. A value that is not a Saturday stops
the run before anything is written.

A re-run is safe: the scorecard is never posted twice for one week (`score`
reads the thread first), the branch is reused and its open pull request
updated. A crash before the score posts leaves nothing written; a crash after
it leaves the score posted and the branch, if pushed, for the next run.

## The D5 widening it runs under

The groomer runs on demand (D5, DRE-2683), with two clocks the CEO opened:
the 06:00 PT morning proposal (DRE-3586, DRE-4677) and this one. The CEO's
green light of epic DRE-6648 on 2026-10-25 widened D5 by exactly one more run:
a weekly replay of the stored set, which posts to no card, writes no receipt,
moves no card and drains nothing. `replay-main`'s `mode`, `judgement`,
`pipeline_ref` and `replay` are literals and it passes no `card`;
`tests/test_groomer_wiring.py` fails on any other scheduled call of
`groomer.yml`, and on this one if it could post or drain. The rule the other
decisions leave standing still holds: a clock may reach `propose`, and nothing
but the CEO's Approve reaches `drain`. See [groomer.md](groomer.md), "The
cadence, and its stated cost".

## Cost

One verify leg per case card a week, about $0.36 each
(`docs/groomer-verify-proof-2026-10.md`), with no ceiling: this run is what
records every case's result on `main`, the seed's included.
