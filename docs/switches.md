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

The step never changes a variable. It writes to the board in only two cases,
both after a switch's reason clears: it posts one receipt, and later it may
file one alarm card (see [The receipt and the alarm](#the-receipt-and-the-alarm)).

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

## The receipt and the alarm

When a switch is off and its reason has cleared, the same pass tells a person
where they will see it, so nobody has to read a run summary. It uses the
states it already read and makes no second read of them.

**The receipt.** The step reads the thread of the first card the companion
names, in the order written. If no receipt is there yet, it posts one:

```
🔀 switch-cleared: PROOF_DISPATCH_LIVE in agent-bureau — its reason cleared at 2026-10-09 05:00 PT: DRE-6141 Done, DRE-6142 Done, DRE-6143 Done. It may be turned on: gh variable set PROOF_DISPATCH_LIVE --body true -R dreadnought-foundry/agent-bureau
```

A receipt is posted once per switch per repository. The step checks the first
line of each comment for `🔀 switch-cleared: <SWITCH> in <repo-slug>`, so each
repository posts its own receipt and none repeats. The step's line says
`receipt posted on <card>` the first time and `the receipt already stands on
<card>` after that.

**The alarm.** The receipt's time is when the reason cleared. If the receipt
is at least `alarm_after_hours` old (12 in `config/switches.json`) and the
switch is still off, the step files one card into `Planning`:

```
Switch PROOF_DISPATCH_LIVE in agent-bureau is still off after its reason cleared
```

The card names the switch, the repository, the receipt's time, the hours since,
the cards and the one command that turns the switch on. Before filing, the step
looks for an open card with exactly that title and files nothing if it finds
one.

The receipt is written first, and the alarm is timed only from the receipt. A
pass that stops between the two loses nothing: the next pass finds the receipt
and times the alarm from it.

The step reads no thread when the switch is on, when any named card is not yet
terminal or is `unread`, or when `REPO` names no repository. If a read, a post
or a filing fails, the step prints why and the pass still exits 0.

Outside GitHub Actions, or with `--dry-run`, the step writes nothing. It prints
`would: post <card> — <receipt line>` or `would: alarm — <title>` instead.

The receipt is the `switch-reason-cleared` act in `config/pipeline-acts.json`
(`docs/pipeline-acts.md`).
