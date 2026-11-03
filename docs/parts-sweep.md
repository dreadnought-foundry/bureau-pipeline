# The weekly sweep's count — `scripts/parts_sweep.py`

Once a week the sweep counts every roster repo's copies of shared parts — a
shared part declared again somewhere else in the repo
([`parts-check.md`](parts-check.md)) — as the repo stands now and as it stood
a week earlier, and writes one count record per repo. A repo the sweep skipped
or could not read gets a record too, so the weekly post names every repo.

`scripts/parts_sweep.py` (DRE-8944) is that count. The weekly workflow
(DRE-8945) runs its three commands. Filing the cleanup cards stays with the
one-time count's filer, `scripts/parts_cleanup.py`, under the same
`parts-copy:` identity, so a copy is only ever filed once. The weekly post
(`parts_weekly.py`) reads the count records.

**Nothing is stored between weeks.** Both numbers are counted from the files
on every run — the checkout, and the commit a week back in its history.

## The three commands

They run on the runner's own `python3`, standard library only:

```
python3 <pipeline>/scripts/parts_sweep.py roster [--repo <slug>]
python3 <pipeline>/scripts/parts_sweep.py count --repo <slug> --repo-root DIR --week-ago <ISO 8601> \
    --json-out FILE --count-out FILE
python3 <pipeline>/scripts/parts_sweep.py record --repo <slug> --state <unread|archived|failed> \
    --reason <text> --json-out FILE
```

`<pipeline>` is `.bureau-pipeline` in a product repo's checkout. Each command
also takes `--pipeline-root DIR`, the bureau-pipeline checkout whose `config/`
it reads; it defaults to the checkout the script sits in, and the tests point
it at a copy.

## `roster` — the roster, read live

`roster` reads `config/repo-map.json` at run time, never a copied list, and
prints one row per key, in the map's order — ten today, the four sandbox repos
and bureau-pipeline itself included. No key is left out by name, so a repo
added to the map is in next week's sweep with no other change.

It prints `repos=<JSON list>` on stdout, each row
`{"repo", "owner", "name", "agent", "initiative", "owned_by", "listed"}`, with
`owned_by` an empty string when absent. `--repo <slug>` keeps the one row, and
exits 1 naming a slug the map does not carry. A map or `config/parts-count.json`
that cannot be read is an `UNKNOWN` line and exit 1.

### The labels

- **A listed repo** — one `config/parts-count.json` lists — takes that entry's
  `agent`, `initiative` and `owned_by`, with `listed: true`.
- **Any other repo** gets `listed: false`, `agent:frontend`, no `owned_by`,
  and an initiative read off `validate_card._INITIATIVE_ALIAS` **inverted**.
  That table (`{"bureau": "agent-bureau"}`) maps an initiative to the repo slug
  it routes to. So a slug that is a VALUE in it gets `initiative:<that key>`,
  and any other slug gets `initiative:<slug>`. `agent-bureau` therefore gets
  `initiative:bureau`, never `initiative:agent-bureau`. The table is read on
  every run, never copied.

### Why only a listed repo is carded

An unlisted repo's labels are used only on a replacement card a rejection files
(DRE-8941). No cleanup card is filed under them, because a repo's backlog is
carded by its one-time count, under its owner's labels: the cleanup cards are
filed only for a repo `config/parts-count.json` lists (epic contract 6). The
sweep still counts an unlisted repo, and its count line says it is not carded
yet.

## `count` — one checkout, now and a week ago

`count` runs in one repo's checkout, which must carry its full history.

1. When `design_parts.exists` says no, it prints the skip line with the reason
   and writes a record with `state: skipped`, so a repo with no design system
   is said in the log, never silently passed over. It writes no `--count-out`.
2. Otherwise it counts copies with `parts_check.count` at the checkout and
   writes the unchanged `parts_check.py count --json` object to `--count-out`,
   which `parts_cleanup.py file --count-json` reads.
3. Then it takes the week-ago count: the newest commit on the checked-out
   branch at or before `--week-ago` (`git rev-list -1 --before=…`), counted
   again in a temporary `git worktree` of that commit, which is removed
   afterwards — the checkout itself is never moved.

`--week-ago` is an ISO 8601 time with a UTC offset; a time with no offset is
ambiguous and is an `UNKNOWN` line. It is read as an instant and handed to git
in UTC, so a week that crosses the change from daylight to standard time
chooses the commit at the instant given, never an hour off.

A week-ago commit with no design system, or no commit that old, gives
`last_week: null` with the reason in `last_week_reason`; the repo is still
`counted`.

When the checkout cannot be counted — its declaration cannot be read, git
fails, `--week-ago` cannot be read — `count` prints the `UNKNOWN` line, writes
a record with `state: failed` and the problem as its reason, and exits 1.

## `record` — a repo the workflow did not count

`record` writes the record of a repo the workflow did not count, for the three
states the workflow decides from GitHub (epic contract 6). It prints its line
and exits 0 for `unread` and `archived`, and 1 for `failed`, so that leg — and
the run — is red.

## The five states

| State | Written by | When |
| --- | --- | --- |
| `counted` | `count` | the checkout's design system exists and its copies were counted |
| `skipped` | `count` | `design_parts.exists` says the checkout has no design system |
| `unread` | `record` | a repo in another organization the App cannot reach |
| `archived` | `record` | the repo is archived on GitHub |
| `failed` | `record`, and `count` on `UNKNOWN` | a repo in this organization that cannot be read |

## The record

Written by `count` and `record` to `--json-out`, read by the workflow as
`count.json` and by `parts_weekly.py`:

```
{"repo": "<slug>", "state": "counted", "listed": true, "reason": null,
 "source": "<declaration source>", "copies": 3, "last_week": 4,
 "last_week_sha": "<sha>", "last_week_reason": null}
```

For every state but `counted`, `reason` is set and `copies`, `last_week`,
`last_week_sha` and `last_week_reason` are null. `listed` is read from
`config/parts-count.json` for `counted` and `skipped`, and null otherwise.
`count` writes `--count-out` only for `counted`.

## The three reasons

The reasons the workflow passes, exactly:

- `unread`: `the read token could not be minted for <owner>/<name>`
- `archived`: `archived on GitHub, so it is read-only and not swept`
- `failed`: `the read token could not be minted for <owner>/<name>, inside this organization`

## The log lines

Under `parts_sweep.TAG = "parts-sweep:"`, on stderr — exactly one
`parts-sweep: <slug> — …` line per repo:

```
parts-sweep: <slug> — skipped — no design system (<reason>)
parts-sweep: <slug> — unread — <reason>
parts-sweep: <slug> — archived — <reason>
parts-sweep: <slug> — failed — <reason>      (exit 1)
parts-sweep: <slug> — <n> copy(ies) now, <m> a week ago (<change>)
parts-sweep: <slug> — <n> copy(ies) now, not counted a week ago — <reason>
parts-sweep: <slug> — UNKNOWN — <problem>      (exit 1)
```

`<change>` is `+n`, `−n` or `no change`. For a repo with `listed: false`, each
count line ends `, not carded yet — no entry in config/parts-count.json`.

The lines name repos, counts, declaration files and reasons only. The script
reads no pull request and prints no part's reasoning.
