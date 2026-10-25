# Hold deadlines — when a hold is chased, when it is late, and the one alert that says so

The pipeline has three hold kinds that wait on something outside a run:
`proof-waiting`, `hand-work-overdue` and `unlanded-work-watchdog`. Each is a
`kind: hold` act in `config/pipeline-acts.json` with `cadence_s: null`, so
until DRE-7964 nothing said when one was overdue. They sat for weeks: the
weekly agent report DRE-7960 counted 2,126.8, 338.7 and 1,060.6 hours stood
still in one week.

Now one file declares a due date for each. `config/hold-deadlines.json` is
keyed by the hold's act tag, and `scripts/hold_deadline.py` is its one reader
and the one writer of the late-hold alert. This page is the human-readable
version of that file. DRE-7964 wired nothing into the sweep: the site cards
(DRE-7965, DRE-7966) call the module from the sweep, and DRE-7967 adds the
`proof-waiting` chase.

## The three words

- **The clock** starts at the moment the hold began — the time its site reads
  for it. Each row names that moment in one sentence (`clock`).
- **A chase** is what the pipeline tries first, before the hold is late
  (`chase`). It comes due `chase_after_hours` after the clock starts. Where the
  row's `chase_after_hours` is `null`, the chase is the site's own existing
  notice, on that notice's own clock, and the hold has no chase moment of its
  own.
- **A late hold** is one whose clock has run `due_hours` or more. A late hold
  is put on the console as one alert per repository and kind.

`phase(tag, started, now)` names where a hold stands: `late` from the due
date, `chase` from the chase moment when the row has one, and `waiting` before
either.

## The three rows

| Tag | Clock starts | Chase | Chase after | Due after | Reported by |
| -- | -- | -- | -- | -- | -- |
| `proof-waiting` | The earliest of the card's open `🔬 proof-waiting` holds that qualify — every one but a hold naming the CEO's press and a time-only hold whose time is still ahead. A time-only hold whose time has passed starts at the later of its post and its time. | One proof run on a `PROOF:` card in Hand-work whose event hold has stood `chase_after_hours` (DRE-7967). A record In Review waiting on an event is re-checked by the proof dispatcher's own 12-hour re-check instead (DRE-7951, epic DRE-7944), so the one hold is never run twice. | 24 hours | 72 hours | DRE-7965 |
| `hand-work-overdue` | When the card entered Hand-work, as the sweep's Hand-work alarm reads it. A `PROOF:` card with a qualifying `proof-waiting` hold is judged on that hold's clock instead, so it is never listed twice. | The sweep's own once-only `hand-work-overdue` alarm on the card, for the operator. | 24 hours — equal to `config/hand-work-age.json` `hours`, and checked equal | 72 hours | DRE-7965 |
| `unlanded-work-watchdog` | The newest commit on the branch no pull request has ever been opened for, the time the work-never-landed notice measures its idle minutes from. | None of its own: the sweep's existing work-never-landed notice, on that notice's own clock. | `null` | 24 hours | DRE-7966 |

Each row also carries a `why`, the reason for its numbers.

## Where the sweep reads them (DRE-7965)

Two sites in `scripts/reconcile.py` keep their 24-hour `hand-work-overdue`
comment exactly as before — that comment is the chase — and each now hands
`settle` its late list once per pass, after its loop:

- **`proof-waiting`** — `alarm_held_proofs`, over this repo's `PROOF:` cards
  in any lane but Green Light. `_proof_wait_clock` reads a card's open holds
  over its whole thread: the board read's comment window when it is the whole
  thread, at no request, and one paged read when it is not. A window that is
  not the whole thread is never judged on its own, and a read that fails
  leaves the card unjudged and the pass incomplete. Event waits count, and
  are the holds nothing else chases. Nothing reads the card's epic, so a
  proof under a closed epic is listed like any other.
- **`hand-work-overdue`** — `flag_stranded`, over this repo's Hand-work cards
  that carry the alarm for their current entry. An unreadable entry, an
  alarm that could not be posted, or a `PROOF:` card past the limit whose
  proof run could not be read leaves the pass incomplete. A `PROOF:` card
  with a qualifying hold is left to `proof-waiting`, so no card is listed
  twice.

A standing card in `config/hand-work-age.json` and a `hand-built` card are
listed by neither. An idle pass, a held board read and `promote_only` call
`settle` from neither.

## The alert

A late hold is put on the console through the existing notice door,
`scripts/pipeline_notice.py` — never a Linear card, and never a new act. The
raise is handed no fallback card, so a door that cannot be reached files
nothing: the outcome is `lost`, and the next pass raises again.

- **Key:** `hold-late:<repo>:<tag>` — one alert per repository and hold kind.
- **Severity:** `WARN`.
- **Title:** `{n} {tag} hold(s) past their due date in {repo}` — for two
  `proof-waiting` holds in agent-bureau,
  `2 proof-waiting hold(s) past their due date in agent-bureau`.
- **Body:** one line per late hold, the oldest clock first, each naming the
  card, since when (PT), its due date (PT), the hours it is late and what it
  waits on. When the read was not complete, one more last line:
  `Some holds of this kind could not be read on this pass, so this list may be short.`

`settle(tag, repo, late, complete=…, now=…)` is the one writer of that key.
It does one of three things:

| Late holds | Complete read | What `settle` does | Outcome |
| -- | -- | -- | -- |
| one or more | yes or no | Raises the alert — a hold that was read late is late. | the door's word: `raised`, `repeat`, `escalated`, or `lost` |
| none | yes | Resolves the key, with no recovery text. | `resolved` or `not-open`, or `resolve-refused` |
| none | no | Calls the door not at all — no raise and no resolve. | `unread` |

It never raises an exception out of a door failure.

### What a complete read is

`complete` is true only when the site judged every hold of its kind it is
responsible for on that pass. Any item the site skipped because something
about it could not be read — a per-card or per-branch read that failed, was
rate-limited or answered nothing usable, a time that could not be read, or a
per-item exception the site caught into `_write_failures` — makes `complete`
false for that pass. Each site card names its own items.

A pass whose whole read did not happen — the board read held or idle,
`promote_only`, or the site's top-level listing unreadable — does not call
`settle` at all.

**Why an incomplete pass with nothing late never resolves the alert.** The
holds it could not read may be exactly the late ones. Resolving on such a pass
would clear an alert that is still true and raise it again on the next
complete pass, so the alert would flicker with every unreadable card. So it
stays as the last complete pass left it, until a complete pass finds nothing
late. An incomplete pass that does find late holds still raises, with the
last line saying the list may be short.

### What a repeat refreshes

Every sweep pass that finds anything late raises again. The console keeps one
alert per key, so a raise while it is open comes back `repeat` (or
`escalated`, the door's own word) and refreshes the text without delivering it
again (`docs/switches.md`). `settle` builds the title and body afresh from that
pass's list every time, so the alert always shows the newest count and list.
Nothing else differs on a repeat.

## The `hold-deadline:` line

Each `settle` call prints exactly one line, under the tag `hold-deadline:`,
for a person reading the sweep's log:

    hold-deadline: <tag> repo=<repo> late=<n> cards=<comma-separated ids, or none> complete=<yes|no> outcome=<outcome>

The door's own `pipeline-notice:` line, which `config/proof-reach.json`
already declares, is the line a proof reads.

## How this differs from the `needs-human` holds

`docs/holds.md` lists the `needs-human` holds: a label on a card that makes
the sweep, the fix loop, the medic and limit recovery leave it alone until a
named lift takes it off, and each row there says what lifts it. The holds here
are not that label. Each is a `kind: hold` act — a receipt or alarm on a card
or branch saying it waits on something outside a run — and this file says only
how long that wait may run before the console calls it late. Nothing here
applies, reads or lifts `needs-human`, and a late alert lifts no hold.

## The check

    python3 scripts/hold_deadline.py check

It exits 1, naming each problem, when the file names a tag that is not a
`kind: hold` act's tag in `config/pipeline-acts.json` (or a hold that is not
one of the three), misses one of the three tags, carries a number that is not
positive, puts `chase_after_hours` at or past `due_hours`, is missing a
sentence, or carries a `hand-work-overdue` `chase_after_hours` that differs
from `config/hand-work-age.json` `hours`. It only reads
`config/pipeline-acts.json`: the three hold rows there keep `cadence_s: null`,
because a number there renders as overdue in the console and must be declared
in agent-bureau first (`docs/pipeline-acts.md`).

CI runs it through the unit suite: `tests/test_hold_deadline.py` runs `check`
against the shipped files and asserts it passes, the way
`tests/test_notice_writers.py` runs `scripts/notice_writers.py check`.
