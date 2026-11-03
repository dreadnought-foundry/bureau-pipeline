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

A standalone workflow — one that runs in bureau-pipeline and checks out no
`pipeline_ref` — sets no `BUREAU_PIPELINE_REF`, and the client then makes no
ref check (`bureau_read.pipeline_ref_problem`). Triage Repair, Alarm Repair
and Hygiene are three (DRE-8902): `BUREAU_READ`, `BUREAU_READ_URL`,
`BUREAU_READ_AUDIENCE` and `BUREAU_READ_CARD_MAX_AGE: 300` on the job-level
`env:` of triage-repair.yml's `select`, `repair` and `unrepaired`,
alarm-repair.yml's `select` and `repair`, and hygiene.yml's `read`, `keep`
and `summary`, each of which can mint the OIDC token. The jobs that read a
receipt posted before they began — triage-repair.yml's `repair` and
`unrepaired`, alarm-repair.yml's `repair` — open with the step `Note the
job's start for the read door`, which writes `BUREAU_READ_CARD_SEEN_AFTER` to
`$GITHUB_ENV`: a re-run's `claim` then finds the claim an earlier attempt
posted. `alarm_repair.read_card` asks `card_via_door(card, comments=50,
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

An `as_of` before the floor, an `as_of` that cannot be read, or a `seen_after`
that cannot be read (`soon`, or an instant with no offset) is refused. The
refusal is one `read-fallback:` line with `read=cards reason=older-than-needed`,
and the caller reads Linear. With none of the three there is no floor.
`floor=False` skips it, for a read of what never changes: the card's `id`,
which `_comment_target` reads.

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

## The fallback lines

`bureau_read.cards` prints the `read-fallback:` line for a refusal or an
UNKNOWN (`thread-incomplete`, `stale`, `not-found`, `no-token`,
`pull-request`, …). A door that stopped earlier in the run is named by
`card_via_door` itself with the reason it stopped, the way `_door_state_list`
names its own. `linear-hold` returns `None` with no fallback line, because a
card read has no phase to skip. The tests are
`tests/test_linear_ops_card_via_door.py` and
`tests/test_linear_ops_own_writes.py`.
