# Switches — what each one is, why it is off, and when it may come on

A pipeline switch is a repository variable that a reusable workflow reads from
`vars.*`. The workflow treats it as live only when its value is exactly `true`.
Any other value is off, and so is no value at all. Off usually means a dry run:
the step prints what it would do and writes nothing.

Before DRE-6434 a switch that was off carried no reason. An off switch looked
like any normal dry run, and nothing could say when the reason for it was over.
Now `config/switches.json` lists every switch, and `scripts/switch_reason.py`
reads a switch's declared reason and composes the one line every reader prints.
`python3 scripts/switch_reason.py check` finds every `vars.<NAME>_LIVE` read
under `.github/workflows/` and fails on any read that has no row in the file.
This page is the version of that file a person reads.

## The switches

| Switch | Read by | What `true` does |
| -- | -- | -- |
| `PROOF_DISPATCH_LIVE` | `reconcile.yml`, `Dispatch proof runs` | The sweep starts a proof run at each PROOF card whose epic's build cards are Done and released. |
| `GREEN_LIGHT_REPLY_LIVE` | `reconcile.yml`, `Reply to the CEO's Green Light comments` | A comment the CEO leaves on an epic waiting in Green Light sends the plan back for a review. |
| `HYGIENE_LIVE` | `hygiene.yml`, `Decide dry run` | The scheduled hygiene run writes to the board. |

`config/switches.json` is the source. If this table and the file disagree, the
file is right.

## The companion variable

A switch that is off says why in its companion, a second repository variable
named after the switch with `_OFF_UNTIL` on the end. `PROOF_DISPATCH_LIVE`'s
companion is `PROOF_DISPATCH_LIVE_OFF_UNTIL`. Its value names the cards the
switch is waiting on, as `DRE-<n>` ids separated by commas or spaces. Any other
text around the ids is kept as written.

One command declares a reason:

```
gh variable set PROOF_DISPATCH_LIVE_OFF_UNTIL --body "DRE-6141, DRE-6142, DRE-6143" -R <owner/repo>
```

To turn the switch on, set the switch to `true` and delete the companion
(`gh variable delete PROOF_DISPATCH_LIVE_OFF_UNTIL -R <owner/repo>`). A switch
that is on while its companion is still set is reported as stale.

## The step: `Read the switches`

`Read the switches` is the last step of the `sweep` job in
`.github/workflows/reconcile.yml`. It runs on every full pass, which means
every scheduled Reconcile run, and never on a narrowed one. The caller passes
in each switch and its companion. The step runs `python3
.bureau-pipeline/scripts/switch_reason.py`, which prints one line per switch in
the catalog, in the catalog's order, on every pass. A switch with nothing set
is still printed: it shows as `off — no reason given`. It is never left out.

Every line, plus the `linear-budget:` spend line, is copied into the run's step
summary under `### Switches — spend`.

The step only reads and prints. It posts no comment, files no card and
changes no variable.

### The line grammar

Every line opens `switches: `. The rest is exactly one of:

```
switches: <SWITCH> is on
switches: <SWITCH> is on — its off-reason is stale, delete <SWITCH>_OFF_UNTIL
switches: <SWITCH> is off — no reason given
switches: <SWITCH> is off — its reason names no card: <text>
switches: <SWITCH> is off — until DRE-A, DRE-B land: DRE-A <state>, DRE-B <state>
switches: <SWITCH> is off — its reason cleared: DRE-A <state>, DRE-B <state>; the switch may be turned on
```

For example:

```
switches: PROOF_DISPATCH_LIVE is off — until DRE-6141, DRE-6142, DRE-6143 land: DRE-6141 Done, DRE-6142 Done, DRE-6143 In Review
switches: PROOF_DISPATCH_LIVE is off — its reason cleared: DRE-6141 Done, DRE-6142 Done, DRE-6143 Done; the switch may be turned on
```

A reason clears only when every card it names is `Done`, `Canceled` or
`Duplicate`.

### Reading the cards' states

A switch's cards are read from Linear in one request, however many cards the
companion names. The step makes no request at all when the switch is on, when
its companion is unset, or when the companion names no card.

A card Linear did not return is shown as `unread`. An unread card never counts
as landed, so a reason with an unread card never reads as cleared. If the read
fails, every card of that switch shows as `unread`, the error is added to the
end of the line (`— the read failed: <error>`), and the pass still finishes
with exit code 0.

## What comes next

This card prints and reads, and that is all it does. Two sibling cards build
on the same per-switch loop in `switch_reason.main()`. Neither adds a second
read of the states:

- **The receipt** will post once to the board when a switch's reason clears,
  so a person learns the switch may come on without having to read a run
  summary.
- **The alarm** will be raised when a switch stays off after its reason has
  cleared for longer than `alarm_after_hours` in `config/switches.json`.

This page will describe each of them when it ships.
