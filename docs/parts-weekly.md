# The weekly parts list — `scripts/parts_weekly.py`

Once a week the CEO gets one list of the shared parts: the parts approved that
week and how to reject each, the parts that became permanent or were rejected,
the rejection messages that did not count, and each repo's copies of shared
parts. `scripts/parts_weekly.py` (DRE-9055) writes that list and posts it as
ONE comment on the standing parts card, by the same path the groom scorecard
takes (`scripts/groom_scorecard.py`, DRE-7970): a first line, a few
plain-English lines, then one fenced `json` block.

The console's Sunday briefing reads that comment from its own event store and
stores the block as the section `sections.parts_weekly` (DRE-9057), which the
Sunday email and the Briefing page draw. The block's shape below is shared by
both cards and written identically in both (epic contract 1).

```
python3 <pipeline>/scripts/parts_weekly.py post --in DIR --repos <JSON list of slugs> \
    --card <DRE-n or ""> --qa-login <login> [--week-ending YYYY-MM-DD] [--dry-run]
```

It runs on the runner's own `python3`, standard library only, and reaches
Linear through `linear_ops`.

## What it reads

- **`DIR/<slug>/count.json`** for every slug in `--repos`, the roster's slugs:
  the count record the sweep's `count` and `record` commands write
  (`scripts/parts_sweep.py`, DRE-8944). It gives the repo's state, its copies
  now and a week ago, and `listed`. A slug with no `count.json` — its leg died
  before writing one — is shown as `failed` with the reason
  `its leg left no record`.
- **`DIR/<slug>/provisional.json`**, for a counted repo: the provisional scan's
  record (`scripts/parts_provisional.py`, DRE-8941, [its doc](parts-provisional.md)) —
  each part's approval, status and closest part, and every rejection message.
  A counted repo whose leg left none is named in the comment's words.
- **Each new part's pull request**, for the critic's reasoning (below), on the
  read token the workflow gives it.
- **The standing card's thread**, through `spoken_thread.read(<card>)`, for the
  once-a-week check (below).

A record that exists but cannot be parsed, or names another repo, is an
`UNKNOWN` line and exit 1: nothing is posted.

## The reasoning is read back from the pull request and never stored

bureau-pipeline is public, and so is every log line its workflows print.
Neither record carries the critic's reasoning (epic contract 5), so for each new
part the script reads it back from the part's own pull request with
`parts_provisional.final_approval(full_name, pr, qa_login)` — the same counted
verdict the scan used — and matches the part by name and path. `full_name` is
read off the part's `pr_url`.

The reasoning goes only into the Linear comment: it is never printed, never
written to a file, and never passed on. The run writes no file at all, and
`--dry-run` builds the list without printing it. A pull request it cannot read,
or a verdict that no longer names the part, leaves that part's `reasoning`
null; the comment says the reasoning could not be read, and the run prints the
`could not be read` count line.

## The week

The week is the one `groom_scorecard.py` uses — seven Pacific days, Sunday to
Saturday, ending on `--week-ending` or by default the Saturday before today,
Pacific — computed with its `week_of` and `default_week_ending`, imported, not
copied. A `--week-ending` that is not a Saturday is an `UNKNOWN` line.

A time is inside the week when its Pacific day (`America/Los_Angeles`) is. All
times on the list are Pacific, written `YYYY-MM-DD HH:MM PT`, so a week that
crosses the change from daylight to standard time places every time by the
clock in force at that instant.

What the week holds:

- **New parts** — each part whose approval falls inside the week.
- **Became permanent / rejected** — each part whose `permanent_on` or
  `rejected_on` falls inside the week. A part with `recorded_now: true` whose
  date falls before the week was recorded late, and is listed too, with
  `late: true`.
- **Not counted** — every `rejections` entry with `counted: false` whose `at`
  falls in the week (epic contract 3), taken once per `at` across all the repos'
  records, and dropped when any repo's record counted the same `at`. One console
  message is read by every repo's scan, so a message one repo counted is a
  counted message. Of the records that did not count it, the `why` of the repo
  the message names is the one shown.
- **Repos** — each repo's copies now, last week and the change, or its state
  and reason.

## The comment

Its first line is exactly

```
🪴 parts-weekly: <week_start>..<week_end>
```

(`parts_weekly.POST_MARK = "🪴 parts-weekly:"`, the provisional scan's
`WEEKLY_MARK`, which reads its lookback off the newest one). Then the
plain-English lines: each new part with its pull request, approval time, the
critic's reasoning, the closest existing part named in words (this epic shows
no picture — the pictures are DRE-9041's), the date it becomes permanent and how
to reject it:

```
To reject <Name>, write on <card> from the console a message that starts with "reject" and names <slug>/<Name>, before <YYYY-MM-DD HH:MM> PT. A message written in Linear does not count. If nobody rejects it, it becomes permanent then.
```

then the parts that became permanent or were rejected (a rejected part naming
its replacement card, a late one saying it was recorded late), the messages
not counted with why, and one line per repo — "not carded yet" for a counted
repo with `listed: false`. It ends with one fenced `json` block holding exactly
this object (`parts_weekly.SCHEMA = "parts-weekly/1"`):

```
{"schema": "parts-weekly/1",
 "week_start": "YYYY-MM-DD", "week_end": "YYYY-MM-DD",
 "posted_at": "YYYY-MM-DD HH:MM PT", "card": "DRE-n",
 "new_parts": [{"repo": "agent-bureau", "name": "Badge", "path": "<path>",
                "pr": 4601, "pr_url": "<url>", "approved_at": "YYYY-MM-DD HH:MM PT",
                "reasoning": "<the critic's sentence, or null>",
                "closest": {"name": "Pill", "path": "<path>"},
                "permanent_on": "YYYY-MM-DD", "reject_by": "YYYY-MM-DD HH:MM PT",
                "reject_with": "reject agent-bureau/Badge"}],
 "became_permanent": [{"repo", "name", "path", "pr", "permanent_on", "late"}],
 "rejected": [{"repo", "name", "path", "pr", "rejected_on", "replacement_card", "late"}],
 "not_counted": [{"at": "YYYY-MM-DD HH:MM PT", "why": "<plain words>"}],
 "repos": [{"repo": "agent-bureau", "state": "counted", "carded": true,
            "copies": 3, "last_week": 4, "change": -1, "reason": null}]}
```

Field by field:

- `schema` — `parts-weekly/1`.
- `week_start`, `week_end` — the week's Sunday and Saturday.
- `posted_at` — when the run built the list, Pacific.
- `card` — the standing parts card it is posted on.
- `new_parts` — one row per part approved in the week, oldest approval first.
  `approved_at` is the counted verdict's time; `reasoning` the critic's
  sentence, or null when it could not be read; `closest` the closest existing
  part, or null when there is none; `permanent_on` the Pacific day 14 days after
  approval (`parts_provisional.WINDOW_DAYS`); `reject_by` that instant, before
  which a console rejection counts; `reject_with` the words a rejection opens
  with, `reject <slug>/<Name>`.
- `became_permanent` — `permanent_on` its Pacific day; `late` true for a part
  recorded late.
- `rejected` — `rejected_on` its Pacific day; `replacement_card` the card the
  scan filed to remove it, or null; `late` as above.
- `not_counted` — `at` the message's signed time, Pacific; `why` in plain words.
- `repos` — one row per roster slug, in roster order. `state` is `counted`,
  `skipped`, `unread`, `archived` or `failed`. For every state but `counted`,
  `copies`, `last_week` and `change` are null and `reason` is set. For
  `counted`, `change` is `copies − last_week`; `last_week` and `change` are null
  when there was no count a week earlier, with `reason` saying why (the record's
  `last_week_reason`), and `reason` is null otherwise. `carded` is the record's
  `listed`, and false for every state but `counted`.

## Posting, once per week

With `--card ""` nothing is posted: the run prints its `no card set` line and
exits 0.

Otherwise it reads the card's thread with `spoken_thread.read(<card>)`, the
reader DRE-8941's scan already uses. When a comment of kind `pipeline` there has
the same first line, it posts nothing and prints `already posted`. A comment
with that first line by anyone else — a person in Linear, an integration, a
console answer, or one whose text is withheld — does not count: only the
pipeline's own comment is the list. Its one Linear read stays on Linear because
only the pipeline's shared reader tells the pipeline's own comments apart (epic
DRE-8783), and the workflow declares it. A thread that cannot be read is an
`UNKNOWN` line, never an empty thread.

Otherwise it posts through `linear_ops.cmd_comment`. When that returns Linear's
comment-cap condition, the run prints its `not posted` line and exits 1, as the
groom scorecard does: a list that did not land is a failed run.

`--dry-run` builds the list, reading the reasoning, and posts nothing; it reads
no thread and prints the `would post` line.

## Why an empty roster is refused rather than posted

The post is the week's one list, and once it stands the once-a-week check stops
any later post for that week, so a list built from too little must never be
posted. Before it reads anything, `post` refuses an empty `--repos` (`[]`, or a
value that does not parse as a JSON list of slugs): it prints its
`the roster named no repos` line, posts nothing and exits 1, so the run is red
and the real list can still be posted by a re-run. The workflow (DRE-8945) adds
the other guard: a dispatch that narrows the sweep to one repo always runs
`post` with `--dry-run`.

## The log lines

Under `parts_weekly.TAG = "parts-weekly:"`, on stderr, naming no part and no
reasoning:

```
parts-weekly: posted <week_start>..<week_end> on <DRE-n> — <k> new part(s), <p> permanent, <r> rejected, <x> not counted, <c> copies across <n> repo(s)
parts-weekly: already posted <week_start>..<week_end> on <DRE-n>
parts-weekly: not posted <week_start>..<week_end> — no card set
parts-weekly: not posted <week_start>..<week_end> — <DRE-n> is at Linear's comment limit      (exit 1)
parts-weekly: not posted <week_start>..<week_end> — the roster named no repos      (exit 1)
parts-weekly: would post <week_start>..<week_end> on <DRE-n> — <k> new part(s)
parts-weekly: <u> part(s) whose reasoning could not be read
parts-weekly: UNKNOWN — <problem>      (exit 1)
```

`<c>` is the copies across the counted repos and `<n>` the roster's repos.
Nothing goes to stdout.
