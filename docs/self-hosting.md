# Self-hosting: bureau-pipeline on its own rail

The go-live record for DRE-1929 Option A ("agents author, human promotes,
**harness proves**" — ADR `adr-bureau-pipeline-self-host.md` in the
agent-bureau repo; the third clause added by DRE-2103).

## The facts

- **bureau-pipeline became a dispatch target on 2026-07-11.** Cards labeled
  `repo:bureau-pipeline` now ride the same rail as any product repo: the
  relay dispatches here, the `self-*` stubs (merged in PR #74) call this
  repo's own reusable workflows, and the normal build → PR → critic →
  merge-gate flow applies.
- **The fleet consumes tagged releases.** v1 = `7ff9374` (the commit the
  operator cut the annotated `v1` tag at). Product repos pin `@v1` with a
  matching `pipeline_ref: v1`.
- **agent-bureau and bureau-pipeline itself ride `@main`** — the canary
  channel. Every merge to main soaks on the canaries' real traffic before
  the fleet sees it.
- **A merge to main here changes nothing live for the fleet — it only
  stages the NEXT release.** The human gate is release promotion: the
  operator cuts or re-points the `vN` tag at the soaked sha. Agents author;
  a human promotes. **DRE-2551 did not change this** — it added a second,
  automatic ref that nothing pins yet; see the last fact below.
- **The harness proves every release (DRE-2103).** The operator cuts `vN`
  only after a green integration-harness run against the candidate sha —
  the `pipeline_ref` input on `harness.yml` is how a candidate is tested
  pre-tag:

  ```bash
  gh workflow run harness.yml --repo dreadnought-foundry/bureau-pipeline \
    -f pipeline_ref=<candidate-sha>
  ```

  A green run stamps a success `integration-harness` commit status on the
  tested sha; `release-gate.yml` fires on every `v*` tag push (and, since
  DRE-2551, on `stable` — see below) and goes
  loudly red when the tagged commit lacks that stamp
  (`scripts/release_gate.py`, fail-closed). Since DRE-2551 the harness runs
  on every push to `main`, so trunk commits carry a stamp of their own. It
  does NOT run on pull requests (DRE-4149 — it did, on the boundary paths,
  from DRE-2103 until 2026-09-17; see "The harness proves main" below).
  Every attempt OPENS the same context `pending`
  before it runs a scenario (DRE-3515): a commit status stands on the sha
  until something posts over it, so before this a re-run spent its whole
  length under the previous attempt's verdict — on 2026-09-09 PR #332 read
  `blocked` on the console while the harness was re-proving the very check it
  was reporting. Pending is still RED to `release_gate.py`; nothing promotes
  on a run that has not finished.
- **`stable` moves itself; `vN` is still cut by hand (DRE-2551).**
  `promote-channel.yml` keeps one moving tag, `stable`, on the newest
  commit on `main` carrying a green `integration-harness` stamp — and, since
  DRE-6496, one that agent-bureau's mirror tests accept (see "agent-bureau's
  mirror tests are part of the proof" below). No
  operator is involved in the ordinary case (there is a by-hand route since
  DRE-4111 — see below), the repository variable `CHANNEL_HOLD` pauses it
  (unset means run), and `release-gate.yml` fires on `stable` too, so an
  automatic move is validated exactly like a hand-cut tag. Two limits are
  the point of this entry:

  - **Nothing consumes it.** No product repo pins `@stable`; this change
    re-pointed no stub. The fleet is on `vN` with a matching
    `pipeline_ref: vN`, unchanged.
  - **The `vN` flow stayed manual and operator-only** — cutting or
    re-pointing the tag, then re-pointing the stubs, exactly as the README
    describes. That is still the human gate between a merge here and
    anything the fleet runs — the "agents author; a human promotes" fact
    above holds in full.

  - **A quiet channel says so.** The console's channel-health monitor
    (agent-bureau, `pipeline_channel_health`) raises the alarm when `stable`
    has stopped advancing, around the clock since DRE-6050. It replaced the
    daily job this repo ran for the same silence (DRE-2552), retired by
    DRE-6053. Still set `CHANNEL_HOLD` to `who=<name> since=<ISO date>
    <why>`: GitHub will not say who set a variable or when, so the reason is
    the only record of who owns a hold.

  - **Every move is a Linear release (DRE-4872).** Once it has moved
    `stable` and read the ref back, `promote-channel.yml` writes release
    `stable-<short sha>` to the `bureau-pipeline-channel` pipeline that
    `.github/bureau/release.json` declares (`scripts/release_linear.py`). It
    carries the cards the merges since the previous `stable` name, and its
    note names the Integration Harness run that proved the sha. A held
    channel writes nothing, and a Linear failure is one warning line that
    never fails or reverses the promotion.

  So what `stable` buys today is the pre-tag question above, answered
  continuously instead of by a hand-run dispatch: it is always the newest
  proven sha on `main`, so once a candidate has soaked on the canary the
  operator has its proof already in hand. Choosing WHICH sha to release,
  and cutting it, stays theirs. Whether the
  fleet ever pins `stable` directly — retiring the manual cut — is **not
  decided by DRE-2551** and is not in its scope. Until that decision is
  made and written here, `vN` is the only fleet-facing channel.
- **Vendor actions ride the same channel, by SHA (DRE-3418).** Every
  third-party `uses:` in `.github/workflows` and `.github/actions` is pinned
  to a 40-char commit sha with its release in a trailing `# v<version>`
  comment; `scripts/check_action_pins.py` runs in `tests.yml` and fails on
  any that is not. Our own `dreadnought-foundry/*` references are exempt by
  name — they ARE the channel described above. So a vendor release reaches
  the fleet exactly the way our own code does: **Dependabot proposes, the
  harness proves, the channel carries.** The bump arrives as a PR the critic
  reviews and the harness exercises against the real action, and only then
  does `stable` — and later a hand-cut `vN` — carry it. Before this, nothing
  stood in between: on 2026-09-08 the floating `claude-code-action@v1` tag
  moved to v1.0.218 and every Claude-running job in the fleet died for 72
  minutes (DRE-3416). A floating major also gave Dependabot nothing to bump,
  since a patch release moves the tag silently. `claude-code-action` is the
  one vendor action Dependabot does not propose: `.github/dependabot.yml`
  holds it for every update type (DRE-5121). Its pin moves through the model
  adoption workflow's pin-raise PR (`model-adoption.yml`, DRE-3898, calling
  `scripts/claude_code_pin.py`), trialled on the raised pin before the harness
  and the channel see it, or by hand.

## Queue behind, never cancel (DRE-3070)

`stable` advances only for a commit the harness PROVED, so the channel moves
at the speed the harness finishes — and one sandbox repo means one harness run
at a time. On a busy evening that is a queue, and how the queue behaves is the
difference between a channel that lags and a channel that stops.

**The rule: `harness.yml` holds `cancel-in-progress: false`, and it is
load-bearing for the channel, not just for the sandbox.** The run proving
commit N is allowed to finish and promote; the newest head queues behind it.

**Why `cancel-in-progress: true` would be exactly wrong here.** It reads like
the efficient choice — why prove a commit nobody is on any more? — and it
freezes the channel outright. Every merge would kill the run proving the merge
before it, so on the nights with the most changes waiting, no run would ever
finish and `stable` would never advance. The mechanism built to keep the fleet
off an unproven commit would instead hold it on a stale one, indefinitely.

**What the rule costs, and why that cost is accepted.** GitHub keeps at most
ONE pending run per concurrency group, so a head still waiting when the next
push arrives is cancelled *before it starts*. Intermediate heads are therefore
skipped: the channel advances to N, then to the latest, rather than to every
commit in between. That is the trade — a skipped head, never a skipped
channel. In the run list those skips look alarming and are not: a displaced run
has `run_started_at == created_at`, because it never ran.

**The gap that used to sit here, and how it closed.** The group was once one
constant shared by the `push` and `pull_request` triggers, so a PR harness run
and the trunk's proving run competed for the same pending slot and the trunk's
could lose (2026-09-03, run `33832750432`, `main@46ca2476`, displaced by the
DRE-3059 PR run). DRE-3075 gave `main` its own lane; DRE-4149 then removed the
pull-request run altogether, so `integration-harness-main` is the only lane
and the only runs in it are pushes to `main` and by-hand dispatches.

### The harness proves main, not every pull request (DRE-4149)

From DRE-2103 until 2026-09-17 the harness also ran on pull requests touching
the boundary paths, and a red run held the merge. It no longer does:

- **The PR run largely re-proved `main`.** The sandbox (`bureau-harness`)
  rides `@main` by standing decision and GitHub resolves that ref at dispatch.
  The harness logged it on every PR run — *"agent-task.yml parses at <main
  sha> (refs/heads/main), which is NOT the commit under test"* — and DRE-3101
  had already handed a PR run `main`'s own sweep and probe rules. The run that
  proves the merged result is the one on `main`, and it runs anyway.
- **It was not free.** On 2026-09-17 the worker App's hourly GitHub allowance
  ran out near the end of every hour (DRE-4132). Every harness run that needed
  GitHub in that stretch was refused, so six approved PRs were held by a red
  harness that said nothing about their code, and `stable` sat twelve merges
  behind — with about ten PR runs that morning, each up to 26 minutes of
  polling, among the heaviest spenders of that same allowance.

**What a pull request merges on now:** the critic's approval and the
verifier's verdict, both bound to the head, and the unit/contract suite — the
bar every other repo in the fleet merges on. The merge gate keeps no list of
checks it expects, and branch protection on `main` requires only `scripts unit
tests` and `TDD commit discipline`, so a head with no harness check waits for
nothing (`tests/test_harness_gate_evidence.py`).

**What did not move:** `stable` advances only on a harness run that SUCCEEDED
on `main`, and Red-Main Repair watches the harness on `main`. Products ride
`stable`, so nothing reaches a product repo without a green harness, exactly
as before.

**Runs on `main` collapse to the newest commit** — by the rule above and
nothing else: the proof in progress is never cancelled, the newest head waits
behind it, and a head in between is dropped before it starts.

**Proving a risky branch before it merges** is still possible and is now a
deliberate act: `gh workflow run harness.yml -f pipeline_ref=<branch>`. The
run rides `main`'s lane (it queues; it never runs beside `main`'s proof), and
it proves without gating — its check run lands on the dispatch ref's tip and
its stamp is a commit status, which the merge gate never reads.

**The accepted cost, named (DRE-4149).** A change that breaks the pipeline
end to end is found on `main` after it merges rather than on its pull request
before. `stable` does not move onto it, so no product repo sees it; Red-Main
Repair watches the harness on `main` (DRE-2820) and files the repair card.
With several merges between two `main` runs, the failing run names a range of
commits, not one.

`tests/test_harness_proves_main.py` pins the triggers, so the pull-request run
cannot return without someone deciding it should.

### Reading a channel that did not move

Every completed harness run — on `main`, or by hand against a branch — leaves a
promote-channel receipt naming one outcome, so "the channel is quiet", "the
channel is starved" and "that run was never about the channel" are different
strings instead of the same silence:

| receipt | what happened | what to do |
| --- | --- | --- |
| `harness-passed-promoting` | green run, strictly ahead — `stable` moved | nothing |
| `harness-run-not-on-main` | a run whose branch is not `main` (before DRE-4149, a PR-head run); it proved a commit that is not on the trunk | nothing — it was never a candidate |
| `harness-cancelled-by-newer-push` | displaced by a merge train; never started | nothing — it advances when the trunk quietens |
| `harness-failed` | the harness went red on this commit | a red trunk; the medic and `red-main-repair.yml` own it |
| `channel-held` | `CHANNEL_HOLD` is set | clear the variable when the hold is done |
| `harness-blocked-by-sandbox` | the harness never judged this commit — its own sandbox (reconcile/merge-gate/linear-sync) failed first | nothing proven either way; the next run re-proves this trunk |
| `harness-blocked-by-github-queue` | GitHub left a sandbox run queued with no jobs, and it stayed stuck through the harness's two re-runs (DRE-6147) | nothing proven either way; GitHub's queue, not the code — the next run re-proves this trunk |
| `no-harness-stamp` | no green `integration-harness` status on this sha | fail-closed by design; check the harness run |
| `not-ahead-of-channel` | already there, or behind | nothing — the channel never moves backwards |
| `by-hand-promoting` | a person promoted a commit the harness had already proved | nothing — the run records who, when and why |
| `by-hand-forced-promoting` | a person promoted PAST the harness or the agent-bureau mirror check (DRE-6496), with a reason on the run; the warning names the mirror result it overrode | read the reason; the commit is on the channel unproven |
| `by-hand-candidate-not-on-main` | the named sha is not reachable from `main` | name a merged commit; a green PR head is not one |
| `by-hand-force-needs-reason` | `force` with no reason | dispatch again with the reason that makes it safe |
| `by-hand-force-not-operator` | `force` asked for by a bot login | forcing is operator-only; a person dispatches it |
| `mirror-tests-failed` | agent-bureau's mirror tests pass on `stable` and fail on the candidate (DRE-6496); the reason names each test and the file it mirrors | agent-bureau's mirror follows first — the run filed one card there (`card owed` if Linear refused it); the next run re-evaluates |
| `mirror-check-blocked` | the mirror check could not run — a token, clone or install that failed, pytest that would not start, a run past its time limit, or no result at all | nothing proven either way; on a `main` or by-hand run the run fails so the hold is seen (DRE-6622) — read its reason, and `force` is the way past it |

Before this, those runs concluded `skipped` with nothing else on them: on
2026-09-03 four consecutive PR-head runs each produced one, and learning that
nothing was wrong meant opening all four.

Since DRE-5214 the same runs also leave two GitHub deployment records, so the
console's Train Yard can see the pipeline beside every other train
(`scripts/channel_record.py`). Every main or by-hand run records one
release-train decision under the `pipeline-channel` surface: `release` when
`stable` moved, `no-op` when it already carried the candidate
(`not-ahead-of-channel`), and `held` for every other outcome above, with the
gating run and its failing scenarios in the reason. Each tag move also posts
one `release` deployment to the `pipeline-channel` environment, versioned
`stable@<short sha>` and carrying `from_sha` / `to_sha`. Both are
best-effort: they are written after the tag move, and a refused post never
changes the run's result or whether `stable` moved.

### Moving the channel by hand (DRE-4111)

Until 2026-09-17 `promote-channel.yml` was triggered by exactly one thing — a
`workflow_run` on the harness — with no dispatch, no inputs and no override.
**There was no way for a person to promote the channel.** That is fine while
the harness is a signal about the code; it stops being fine when the harness
fails for a reason that is not about the code, because then `stable` freezes
and the only move is to re-run the harness and hope.

**2026-09-16, the incident this closes.** `stable` sat at `8b54d629a` while
`main` reached `34807775b` — five merge commits and six merged pull requests
behind, including four cards unblocked that afternoon. The harness on main
failed at 01:00Z and 01:05Z, and four of its five scenarios died on the same
line:

```
GitHub API 403: "API rate limit exceeded for installation ID 123249480"
```

The fifth, `lane_contract`, is read-only and passed 15 of 15. Nothing was wrong
with any merged commit — the worker App's hourly bucket, the fleet's known
single point of throttling, was empty, and the harness needs it to drive the
sandbox. Meanwhile every promote-channel run reported SUCCESS, because every
step is guarded on the harness conclusion and a failed harness skips them all.
A row of green runs that promoted nothing read exactly like a channel that was
up to date. The recovery was manual and lucky: read the 403, check the bucket
had refilled (4,280/5,000), re-run the harness on the same sha.

**The ordinary by-hand promote** — a commit the harness HAS proved, just not on
the most recent run:

```bash
gh workflow run promote-channel.yml \
  --repo dreadnought-foundry/bureau-pipeline \
  -f sha=<candidate-sha> \
  -f reason="harness 403 on the shared App bucket; DRE-4111"
```

`sha` may be left blank for the head of `main`. This is **not** a way past the
proof: it re-reads the candidate's own combined commit status and still
requires a green `integration-harness` there. What it drops is the requirement
that the newest run be the one that proved it.

**Forcing past a red or absent status** is a separate, louder act — `-f
force=true`, refused without a reason and refused to a bot login, and it raises
a warning on the run naming the mover and the reason:

```bash
gh workflow run promote-channel.yml \
  --repo dreadnought-foundry/bureau-pipeline \
  -f sha=<candidate-sha> -f force=true \
  -f reason="who=<name> <why this is safe>"
```

**What force does NOT override**, and why the list is short. The harness is
what proves a commit, and `bureau-harness` is deliberately the one repo kept
off the channel so promotion can never validate itself — so force is scoped to
the proof alone:

- the **ancestry rail**: `stable` still cannot move backwards, forced or not;
- the **hold**: `CHANNEL_HOLD` is a standing instruction not to advance the
  channel, and a by-hand promote does not talk over it;
- the **trunk check**: the candidate must be reachable from `main`.
  A by-hand harness run against a branch (`pipeline_ref`) stamps that
  branch's head with a green `integration-harness` of its own — as every PR
  head's run did before DRE-4149 — so without this check a by-hand promote
  could put `stable` on a commit that never merged, the proof present and the
  code unshipped.

Since DRE-6496 the proof has two parts — the harness stamp and agent-bureau's
mirror tests — and **force overrides both**: it promotes past
`mirror-tests-failed` and `mirror-check-blocked` exactly as it promotes past a
red stamp, under the same `by-hand-forced-promoting` warning, which now names
the mirror result it overrode. The three rails above still hold. An ordinary
by-hand promote, without `force`, runs the mirror check like any other.

The push is made with the bot App token on this route too, so
`release-gate.yml` fires and validates the move exactly as it does an
automatic one. The `promote-channel` concurrency group is declared at workflow
level, so a person and the automatic mover queue for the ref rather than race
for it.

**Not closed by this**: making the harness resilient to the shared App bucket,
which is the root cause of that particular instance. The two are independent —
"the proof passed an hour ago and the channel is still behind" has other
causes. Re-pointing `vN` is untouched and stays operator-only.

Every skipped head leaves a `harness-cancelled-by-newer-push` receipt, so a
stalled channel can be read for a **merge train** — two or more `cancelled`
harness runs on `main` since the channel head — rather than reported with an
unknown cause. The console's channel-health monitor raises the stall alarm now
(DRE-6050); the daily job that counted those receipts was retired by DRE-6053.
One skipped head is the rule above working; two is merges arriving faster than
the harness can prove them. The lever is the harness's duration or the merge rate — never
cancelling the run in progress.

### agent-bureau's mirror tests are part of the proof (DRE-6496)

**2026-10-09, the incident this closes.** One change here, the `operator-step`
vocabulary (DRE-6227 and DRE-6228), merged and reached `stable` around 03:00
PT, and broke agent-bureau's CI three separate times that day: the read door's
e2e (found 10:25 PT), the relay's copy of the routing verdicts (about 13:50 PT,
and live behavior in the deployed relay too), and the hold-reason registry
(14:55 PT). Each break held every agent-bureau pull request until someone fixed
it by hand. agent-bureau mirrors several of this repo's vocabularies, and each
mirror's drift test reads bureau-pipeline at `stable` — so a change here was
found only after it was promoted, by every agent-bureau pull request at once.

**What runs now.** Where the decision would otherwise promote — a run on
`main`, a green harness, a green stamp, a candidate strictly ahead, or an
ordinary by-hand promote — `promote-channel.yml` runs agent-bureau's mirror
tests against the candidate before `stable` moves. Every cheaper refusal in the
table above still costs a checkout and one `python3`; a run that does not reach
promotion never clones agent-bureau. `scripts/mirror_check.py` does the work:

- **Which agent-bureau**: the head of its default branch at the moment of the
  run, cloned with the bureau App token scoped to `agent-bureau`. Its sha is in
  the receipt and on any card filed.
- **Which tests**: discovered, never typed — every `test_*.py` or `*_test.py`
  whose text names `BUREAU_PIPELINE_DIR` or `.bureau-pipeline`. The count is in
  the receipt; the gate keeps no list.
- **Which bureau-pipeline**: the candidate, checked out at
  `<agent-bureau>/.bureau-pipeline` and exported as `BUREAU_PIPELINE_DIR`, so
  both ways a test finds the pipeline read it. agent-bureau's own
  dependencies come in through `setup-python-cached`, from its root
  `requirements*.txt` and those in the directories that hold the tests.
- **The check's test tools** come from this repository's own
  `requirements-dev.txt`, installed beside agent-bureau's requirements
  (DRE-6622). agent-bureau's requirements declare no pytest — its CI installs
  its test tools inline — and nothing is read out of agent-bureau's CI: a
  build run here cannot read that repository at all, and pytest is the tool
  this check invokes, so its pin is the check's own. Before any test runs the
  check proves `pytest --version` starts; when it does not, the check is
  blocked with a reason that opens `pytest does not start in the check's
  environment`. A pin here that conflicts with one of agent-bureau's fails the
  install, which blocks the check by name.
- **Both pytest runs carry `--no-cov`** (DRE-6622). agent-bureau's pytest
  settings enforce a coverage floor that its mirror tests alone cannot meet,
  so pytest exited 1 with no failing test and the check could only say
  `blocked`. `--no-cov` is pytest-cov's switch, pinned beside pytest for that
  reason; agent-bureau's other pytest settings stay in force. A run in which
  every mirror test passes is a pass.
- **A failure is re-read against `stable`** before it counts. A test red on
  both is agent-bureau's own red: the receipt names it `already red on stable`
  and it refuses nothing. Only a test that passes on `stable` and fails on the
  candidate refuses, as `mirror-tests-failed`.
- **Which file it mirrors** is read off the test's own string literals — the
  ones naming a file in the candidate. A test naming none reads `mirrors: not
  read from the test`, never a guess.
- **A check that could not run** — a token that did not mint, a clone or an
  install that failed, a run past the step's 15 minutes — is
  `mirror-check-blocked`, never a failing candidate. The step writes `⏱ mirror
  check: Nm (N tests, agent-bureau <sha>)` to the run summary, so the real
  budget is measured on live runs.
- **A `mirror-check-blocked` decision on a `main` or by-hand run fails the
  run** (DRE-6622), in the job's last step, after the summary, the decision
  record and the release record are written. From 02:26 to 08:45 PT on
  2026-10-10 the check could not run at all, and every run held `stable`
  thirteen merges behind while finishing green, until a person forced it.
  Nothing in this repository alarms on a quiet channel, so the red run is the
  signal. One held run fails, with no count: nothing here reads a previous
  run's outcome. A forced promote ends `by-hand-forced-promoting` and is not
  failed; neither is a run refused before the check. The Pipeline Medic may
  re-run the red run once, which repeats the refusal and moves nothing.

**The card a refusal files.** One, in agent-bureau, through `linear_ops.py
oneoff`: titled `agent-bureau: mirrors of <files> must follow bureau-pipeline`
with the sorted mirrored files (no sha), labeled `repo:agent-bureau`,
`agent:engineer`, `initiative:bureau` and `Bug`, landing in `Planning` like any
one-off. A later refusal for the same files finds the open card by its exact
title and comments on it once per candidate, so a harness re-running on every
push to `main` appends to one card. A card that cannot be filed is one warning
and `card owed` in the receipt; the refusal stands either way.

**The order this buys** is the consumer-first one a breaking schema change
already follows: the change merges to `main` as today, agent-bureau's mirror
catches up, and only then does `stable` move onto it.

**The companion this does not build.** agent-bureau's drift tests compare each
mirror against bureau-pipeline at `stable`. Until they also accept a mirror
that matches the head of `main`, the fix pull request in agent-bureau fails its
own drift test and the two repositories deadlock; until then the operator's
`force` is the way past, with one loud step and an exact card.

### How long a run is allowed to take

The other half of a starved channel is a single run that will not end. Every
harness run now writes `⏱ harness scenarios: Nm (budget 40m)` to its summary
and raises a GitHub warning past the budget. Healthy runs measure 9–18 minutes;
the budget sits well above that and well below the job's 180-minute timeout,
which is a ceiling for a bad day rather than a budget — past it the run is
*cancelled*, and a cancelled run stamps nothing at all.

The number exists because on 2026-09-03 a main run sat on `Run harness
scenarios` for 54 minutes: the sandbox's Linear quota had been exhausted and
the scenario was waiting on a sweep that would never come. Nothing
distinguished that from a long queue until an operator read the logs and
cancelled it by hand. **The warning does not shorten the wait** — a scenario's
own wait still has no deadline shorter than the job timeout, and the harness
does not yet fail fast when the sandbox itself has died.

Mechanics of pinning, the canary channel, and the promotion/rollback moves
live in the README under "Release channel: pinning, canary, promotion".
