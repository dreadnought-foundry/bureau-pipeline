# `linear_ops` reads a card through the read door first (DRE-8898)

`scripts/linear_ops.py` reads a card's comment thread, its description and the
`id` a comment is posted to. Until this card each of those went straight to
Linear even where `BUREAU_READ=on`; the only thing `linear_ops` asked the door
for was the workflow-state list. Now each of them asks the door first, in a
step that opts in. A step that does not opt in reads exactly as before.

The one function is `linear_ops.card_via_door`:

    card_via_door(identifier, *, comments=50, relations=False, seen_after=None, floor=True)
        -> (node, read) | None

It returns the door's node for that one card (the `bureau_read.CARD_FIELDS`
shape, plus `inverseRelations` with `relations=True`, asked under
`bureau_read.RELATIONS_MAX_AGE`) and the `DoorRead` it came in. On `None` the
caller makes its own Linear read, exactly as before.

## The reads that ask the door

| Read | What it asks the door | Notes |
|---|---|---|
| `_fetch_thread`: `_thread`, `_thread_and_viewer`, `comment_timeline`, `count_comments`, `first_comment_at`, `comment_bodies`, `comment_records`, `dump-comments` (with and without `--with-authors`), `count-comments`, and `spoken_thread.py` through `_thread_and_viewer` | `comments=all` for a whole-thread read or inside a pass, else `comments=50` (`COMMENT_WINDOW`) | The door's comment nodes arrive newest first, as Linear's do, and go through `window_nodes`, so every reader still gets oldest→newest. `comments=all` is FRESH only on a thread the door marks whole (`thread-incomplete` otherwise), so a reader that asks for the whole thread never gets part of one. |
| `cmd_comment`'s pre-read, `_comment_target(identifier)` | `comments=1`, no floor | `_card_read_once`'s memo answers first. When neither the memo nor the door answers, its own Linear query asks for the card's `id` alone, `query($id: String!) { issue(id: $id) { id } }`, and the `linear-read:` line names `caller=linear_ops._comment_target`. |
| `description` (`cmd_description`) | `comments=1` | The card's `description` off the door's node. |

The comment fields every reader here selects (`COMMENT_FIELDS` =
`body createdAt user { id }`) are exactly the door's comment node.

## A step opts in: `BUREAU_READ_CARD_MAX_AGE`

The max-age is the step's own: the environment variable
`BUREAU_READ_CARD_MAX_AGE`, in whole seconds from 20 to 3600.

* Unset or empty: `card_via_door` returns `None` at once. It sends no door
  request and prints no `read-fallback:` line, and the caller reads Linear
  exactly as before.
* A value that is not whole seconds from 20 to 3600 (`abc`, `5`, `60s`) is
  treated the same way, and named once on stderr.
* A valid value is sent as the door request's `X-Bureau-Max-Age`.

The door's own variables are still needed for any of this: `BUREAU_READ`,
`BUREAU_READ_URL`, `BUREAU_READ_AUDIENCE` and `BUREAU_PIPELINE_REF`, set
exactly as `reconcile.yml` sets them (`${{ vars.BUREAU_READ || 'off' }}`,
`${{ vars.BUREAU_READ_URL }}`, `${{ vars.BUREAU_READ_AUDIENCE }}`,
`${{ inputs.pipeline_ref }}`). With `BUREAU_READ` other than `on` the door is
not asked, whatever the max-age says.

Every workflow that moves onto the door sets the variable on the jobs or steps
it moves. A workflow that does not set it reads as before.

**Agent Task, the Medic and Agent Fix (DRE-8900)** set the four variables and
the max-age in the job-level `env:` of every job that reads Linear:

| Job | Max-age | Floor (`BUREAU_READ_CARD_SEEN_AFTER`) |
|---|---|---|
| agent-task.yml `execute` | 60 | the job's start |
| agent-fix.yml `fix` | 300 | the job's start |
| medic.yml `classify`, `retry_declined`, `stall_record`, `backoff`, `environment_hold`, `diagnose` | 300 | the failed run's end, `${{ github.event.workflow_run.updated_at }}` |

The Medic's retry gate must see the park, the turn-cap receipt or the hold
receipt the failed run posted seconds before it ended. An age bound cannot
promise that, and the failed run's end can. In `execute` and `fix` the first
step, `Note the job's start for the read door`, writes the job's start (UTC,
to the second) to `$GITHUB_ENV`. No `env:` block in either job names the
variable, because a step-level entry would override it. An earlier run's
receipt was posted before that instant. A receipt the job posts itself, the
agent's ⏳ heartbeats included, is in the own-write record. Two of those jobs'
card reads ask the door through `card_via_door` directly:
`medic_retry.card_facts` (the retry gate and the diagnosis snapshot) and
`hold._read_card` (`hold.py reason`, and a lift that reads the live stamp).
Both read the same fifty-comment window as before and fall back to it on
`None`. The tests are `tests/test_agent_runs_read_door.py`,
`tests/test_medic_card_facts_door.py` and `tests/test_hold_read_door.py`.

A standalone workflow — one that runs in bureau-pipeline and checks out no
`pipeline_ref` — sets no `BUREAU_PIPELINE_REF`, and the client then makes no
ref check (`bureau_read.pipeline_ref_problem`). Triage Repair, Alarm Repair
and Hygiene are three (DRE-8902): `BUREAU_READ`, `BUREAU_READ_URL`,
`BUREAU_READ_AUDIENCE` and `BUREAU_READ_CARD_MAX_AGE: 300` on the job-level
`env:` of triage-repair.yml's `select`, `repair` and `unrepaired`,
alarm-repair.yml's `select` and `repair`, and hygiene.yml's `read`, `keep`
and `summary`, each of which can mint the OIDC token. The jobs that read a
receipt posted before they began — triage-repair.yml's `repair` and
`unrepaired`, alarm-repair.yml's `repair`, hygiene.yml's `keep` — open with
the step `Note the job's start for the read door`, which writes
`BUREAU_READ_CARD_SEEN_AFTER` to `$GITHUB_ENV`: a re-run's `claim` then finds
the claim an earlier attempt posted, and a Hygiene pass finds the receipts
the pass queued ahead of it posted. Hygiene's one door-reachable thread read
is the seam's idempotency check (`hygiene._receipts_on`) on a card the board
does not carry or whose window is partial; `read` reads the board and
`summary` the standing card's window off Linear, and `summary`'s comment asks
the door only for the card's `id`, which never changes. `alarm_repair.read_card` asks `card_via_door(card, comments=50,
relations=True)` first. Those runs read cards of every repo; a card the door
does not serve them reads Linear after one `read=cards reason=not-found` line.

**Inside the Reconcile sweep nothing changes.** `reconcile.yml` sets no
`BUREAU_READ_CARD_MAX_AGE`, so `card_via_door` is never asked in its steps.
The pass cache answers first, as before, and a cache miss reaches
`_fetch_thread`, which reads Linear (`caller=linear_ops._fetch_thread`). Linear
Sync's after-merge sweep, `reconcile.py --conflicts-only`, does set the
variable (DRE-8899). There a thread read asks the door. A `pull_request` run's
token is refused for any card but the one its head branch names, so that read
goes to Linear with `reason=pull-request`.

**Merge Gate, Proof Task and Deliver Rescue** set it at job level, at 300
(DRE-8901): merge-gate.yml's `evaluate`, proof-task.yml's `execute` and
deliver-rescue.yml's `deliver` carry the four door variables and
`BUREAU_READ_CARD_MAX_AGE: 300` in their job `env:`. The gate's job also opens
with `Note the job's start for the read door`, which writes
`BUREAU_READ_CARD_SEEN_AFTER` to `$GITHUB_ENV`. `code_owner_hold.park` and
`release` read the thread for the marker an earlier gate run posted, and the
job's concurrency group means that run finished before this job began, so a
door answer from before the job's start is read from Linear instead (the floor,
below). No `env:` block in that job names the variable, because one that did
would override it. Proof Task and Deliver Rescue get no such step: nothing they
read was posted by an earlier run moments before. The door itself must name
`proof-task` and `deliver-rescue` among the workflows it serves (agent-bureau's
`github_oidc.ALLOWED_WORKFLOWS`). Until it does, their first card read is
refused with `workflow` and they read Linear for the rest of the run, exactly
as before.

**Agent Plan opts in (DRE-8903).** `plan.yml`'s `plan` job sets the four door
variables and `BUREAU_READ_CARD_MAX_AGE: 60` in its job-level `env:`, so every
`linear_ops` thread read and comment pre-read in it asks the door first — the
classifier's and router's whole-thread reads, `spoken_thread.py thread`,
`plan_bound`, `escalation_answers` and `answer_into_card`'s thread among them.
Two of the classifier's own reads ask too: `planning_classify.roll_up_parent`
reads the card's `parent.identifier` off `card_via_door(card, comments=1)`, and
`critic_score.read_card` reads a card off it when the door's node has no
children (one with children still reads Linear, because the door lists them by
internal id). In the `publish` job only the `Plan artifact — announce` step
sets the five, for its `linear_ops.py comment` pre-read. Sixty seconds, because
the planner needs its card as it is now. The planner's reads that stay on
Linear — an epic's children, issue history, the card-validation gate's read,
the review re-dispatch's read, the fleet-wide reads, the initiative context,
the allowance's rate-limit probe and the write path's reads — are listed on
DRE-8903, each by its `linear-read:` caller.

## The floor: a read sees every write it must see

An age bound cannot vouch for a write made seconds before the read. The door's
`as_of` trails real time, so a receipt posted a moment ago can be missing from
a FRESH answer. So a door answer is taken only when its `DoorRead.as_of` is at
or after the card's floor. The floor is the latest of three instants:

1. the `seen_after` argument, an ISO-8601 instant with a UTC offset;
2. the step variable `BUREAU_READ_CARD_SEEN_AFTER`, when the argument is not
   passed (empty or unset means none);
3. the newest write this job made to that card through `linear_ops`, from the
   job's own-write record (below).

An `as_of` before the floor is asked once more before the fallback (the
second ask, below): after the gap plus `ASK_AGAIN_SLACK_SECONDS`, capped at
`ASK_AGAIN_MAX_WAIT_SECONDS`, and a second answer at or after the floor is
taken. A second answer still before the floor, an `as_of` that cannot be read,
or a `seen_after` that cannot be read (`soon`, or an instant with no offset) is
refused. The refusal is one `read-fallback:` line with `read=cards
reason=older-than-needed`, and the caller reads Linear. With none of the three
there is no floor.
`floor=False` skips it, for a read of what never changes: the card's `id`,
which `_comment_target` reads.

## The second ask: once more before the fallback (DRE-9250)

Most of the fallbacks still standing were an answer a few seconds behind or
one token refused: over the 14 hours to 10:06 AM PT on 2026-11-06,
bureau-pipeline's run logs held 29 `read-fallback:` lines, and 24 of them were
`older-than-needed` (20), `stale` (2) or `refused` (2). Each of those is now
asked once more before the read falls back to Linear. Three reasons are asked
again, and nothing else:

| Reason | Where | The wait before the second ask |
|---|---|---|
| `older-than-needed` | `linear_ops.card_via_door`, when the door's `as_of` is before the card's floor | the gap (the floor minus `as_of`) plus `ASK_AGAIN_SLACK_SECONDS` |
| `stale` | `bureau_read`'s four reads with a Linear copy — `board`, `cards`, `dependents`, `workflow_states` — answered the door's own UNKNOWN with `freshness.reason` `stale`, or past the reader's max-age by the client's own check in `_accept` | the overage (the age minus the max-age) plus the slack; the door's UNKNOWN carries no age, so its wait is the slack alone |
| `refused` | `bureau_read._get`, a 401 on one of those four reads that is not a `scope=fleet` refusal (those already leave the door in use, DRE-5848) | none: it is asked again at once |

The constants live in `scripts/bureau_read.py`, and `linear_ops` reads them
from there: `ASK_AGAIN_SLACK_SECONDS = 2`, `ASK_AGAIN_MAX_WAIT_SECONDS = 10`
(no wait is longer) and `ASK_AGAIN_LIMIT = 3`. A gap is counted in whole
seconds, rounded up.

The `refused` ask is a plain retry: a second request, with its own fresh token
from `_token()`, as every request has. It carries nothing the first did not,
and nothing on record says it cures Hygiene's 401 ("This is not a token the
read door accepts."), which came at 08:39 and 04:39 UTC and not at 13:39 UTC
on the same job. Its line's `first=` words are there to diagnose a 401 it does
not cure: the first 80 characters of the first 401's body, so a clock-skew,
audience or replay answer is on the line. Before this, that body was printed
only by the answer that stopped the door, on its `read-door: unavailable`
line, and was lost once a second ask was made. A `refused` row still standing
in the epic's proof hour is expected to need its own follow-up card, which
starts from the `first=` words.

**The bounds.**

* At most one second ask per read: a read asked again for a 401 is not asked
  again for a `stale` second answer, and `card_via_door` does not ask again
  after a read that already has.
* At most `ASK_AGAIN_LIMIT` (3) second asks per process, the three reasons
  together. After the third, every later read falls back at once, exactly as
  before, with no `asked again` line.
* A `linear-hold` skip is never asked again, and neither is an `as_of` that
  cannot be read. In mode `off` or `shadow` nothing changes.
* A second ask spends no Linear request.

**Nothing is switched off.** A second answer that is still older than the
floor, still stale or still refused falls back to Linear exactly as before. It
prints the same one `read-fallback:` line, with the reason of the first answer,
so the budget check counts it as before, and the door is stopped for the run
after a second 401 as it was after a first one. The exit line counts the read
once, by its second answer.

**The line.** Every second ask prints one line through `bureau_read._say`, so
it reaches stderr and the lines file (`BUREAU_READ_LINES_FILE`):

```
read-door: asked again read=<read> reason=<reason> trailing <s>s after <w>s — served
read-door: asked again read=<read> reason=<reason> trailing <s>s after <w>s — fell back
read-door: asked again read=<read> reason=refused trailing 0s after 0s — <served|fell back> first="HTTP 401 <body>"
```

* `<read>` is `board`, `cards`, `dependents` or `workflow-states`, as on the
  `read-fallback:` line, and `<reason>` is `older-than-needed`, `stale` or
  `refused`.
* `<s>` is how far the first answer trailed, in whole seconds: the floor minus
  `as_of`, or the age minus the max-age, and `0` for `refused` and for a door
  UNKNOWN that gives no age. `<w>` is the seconds waited.
* `served` means the second answer was taken (for `refused`, that the second
  request was answered 200); `fell back` means the read went to Linear.
* `<body>` is the first 80 characters of the first 401's body, with each line
  break, control character and `"` written as a space, so the line stays one
  line. Only a `refused` line carries `first=`.

The line opens with the `read-door:` tag that `config/proof-reach.json`
already declares. The tests are `tests/test_read_door_ask_again.py`.

## The job's own-write record

Every card write `linear_ops` makes appends one line to
`$RUNNER_TEMP/linear-own-writes.jsonl` (`linear_ops.OWN_WRITES_FILE`):

    {"card": "DRE-7", "at": "2026-11-03T18:00:00.123456Z"}

* The writes are `_create_comment`, `_set_state`, `set_description`,
  `set_title`, `add_label` and `remove_label`.
* The line is written once Linear has answered the write with
  `success: true`. A write that fails or answers `success: false` adds none.
  `at` is the runner's clock in UTC, read after that answer. The identifier
  is upper-cased.
* GitHub empties `RUNNER_TEMP` at the start of every job, and every step of a
  job shares it. So a write made in one step's process sets the floor of a
  later step's read of the same card, and never of another job's. A write to
  DRE-8 sets no floor on DRE-7.
* Off a runner, with `RUNNER_TEMP` unset, the record is kept in the process's
  memory only.
* A record that cannot be written or read is named once on stderr, and the
  floor is then set by `seen_after` alone.

This is what lets Triage Repair's `apply` see the `started` claim its `claim`
step posted seconds earlier (DRE-8902), and an agent's report step see the ⏳
receipts the agent posted (DRE-8900).

## The viewer is never the door's

The door's `viewer.id` is the console's key. Authorship is judged against this
process's own key, and `authored_by_pipeline` is the credential the plan
critic's round counting trusts (DRE-2721). So:

* on a door answer `_fetch_thread` returns `(nodes, None)`, and
  `_thread_and_viewer` takes the viewer from `viewer_id()`, Linear's
  `viewer { id }`, named `caller=linear_ops.viewer_id` on the `linear-read:`
  line;
* a reader that needs no viewer (`_thread`, `comment_bodies`,
  `count_comments`, `comment_timeline`) makes no viewer read;
* agent-task's card snapshot keeps the door's viewer as it came, so
  `spoken_thread.read` never judges a door-served snapshot's comments against
  it: it takes the viewer from `viewer_id()` instead (DRE-8900). A snapshot
  read from Linear keeps the viewer it was read with;
* outside a pass `viewer_id()` asks at most once per process and bucket, keyed
  by `spending_bucket()`, so a process that fell back to another key mid-run
  asks again. Inside a pass it asks once per pass, as before.

Two whole-thread reads of two cards off the door in one process therefore send
one Linear request, the viewer query.

## What stays on Linear, and why

| Read | Why it stays on Linear |
|---|---|
| `get_issue` | It is the write path's read now, and it selects `team { id }`, `state { id type }` and `project { id }`, which the door's node does not carry. |
| `get_issue(fresh=True)` | It is the DRE-2316 pre-write re-read, which must be live. |
| `_clobbered_terminal_state` | It is the DRE-1877 history read-back of a write made seconds ago, and the door serves no history. |
| `_card_look`, `_comment_look` | They ask whether a create or a comment landed seconds ago, which an answer that trails real time cannot say. |
| `children`, `children-json`, `children-detail`, `child-descriptions` | The door's node lists a card's children by internal id, not identifier. |
| `find_open`, `find_open_prefix` | The door serves its fleet-wide search to the sweeps alone. |
| `viewer_id` | Authorship is judged against this process's own key, and the door's viewer is the console's. |

Each one's docstring says the same in one sentence.

## Readers outside `linear_ops` (DRE-8904)

Two of the planner's lookups call `card_via_door` themselves, in a step that
names the max-age:

* `planning_route._read_returned_child` reads the card, and its parent when it
  has one, with `comments=1`. `_read_epic_wait` reads the epic with
  `relations=True`, under `bureau_read.RELATIONS_MAX_AGE`. Either falls back to
  its own query (`_FAMILY_QUERY`, `_WAIT_QUERY`) on `None`, and the epic's also
  on `inverseRelations.pageInfo.hasNextPage`. The epic's lane history and its
  blockers' own reads are unchanged.
* `dedupe_dispatch`'s lane and label reads ask whether the card moved while
  the dispatch sat in the queue. They pass the run's `created_at` as
  `seen_after`, so the door's node is taken only when its `as_of` is at or
  after the dispatch. A re-run, an unreadable `created_at` or no door answer
  makes the live `get_issue(fresh=True)` read as before. `_one_off_card` reads
  the card and its whole thread with `comments="all"`, under the same
  `seen_after`, because its card is what the build is dispatched with. A
  re-run or an unreadable `created_at` skips the door. If the door cannot
  vouch for the thread, or its `as_of` is older than the dispatch, the card
  falls back to today's two Linear reads, with one `read-fallback:` line. The
  run's `created_at` is read off GitHub once per process.

## The fallback lines

`bureau_read.cards` prints the `read-fallback:` line for a refusal or an
UNKNOWN (`thread-incomplete`, `stale`, `not-found`, `no-token`,
`pull-request`, …). A door that stopped earlier in the run is named by
`card_via_door` itself with the reason it stopped, the way `_door_state_list`
names its own. `linear-hold` returns `None` with no fallback line, because a
card read has no phase to skip. The tests are
`tests/test_linear_ops_card_via_door.py` and
`tests/test_linear_ops_own_writes.py`.
