# A provisional part's two weeks — `scripts/parts_provisional.py`

When the design critic approves a pull request that adds a new shared part, it
approves the part as provisional: its verdict carries one
`🪴 part-provisional: <Name> at <path> — reasoning: …` line per part
(`scripts/parts_review.py`, DRE-8719). The parts epic's rule 11 gives that part
two weeks. If the CEO does not reject it in that time it becomes permanent; if
he does, the part is removed and its uses move to a shared part that already
exists.

`scripts/parts_provisional.py` (DRE-8941) runs those two weeks for one repo,
inside that repo's checkout. The weekly sweep's workflow (DRE-8945) runs it
once per roster row, and the weekly post (`parts_weekly.py`) reads its JSON.
It follows the epic's contracts 3, 4 and 5.

```
python3 <pipeline>/scripts/parts_provisional.py scan --repo <slug> --full-name <owner/name> \
    --repo-root DIR --qa-login <login> --writer-login <login> --card <DRE-n or ""> \
    --agent <agent:label> --initiative <initiative:label> --json-out FILE \
    [--now <ISO 8601>] [--dry-run]
```

It runs on the runner's own `python3`, standard library only. Every GitHub read
goes through `scripts/gh_read_retry.py`.

## No reasoning is ever written, and why

bureau-pipeline is public, and so is every log line its workflows print. The
critic's reasoning about a part is the design judgment the CEO reads, and it
goes only into the weekly Linear post, which reads it back from the pull
request itself through `final_approval` (below). So this script never writes
the reasoning: not to stdout, not to stderr, not into its JSON, and not onto a
card. Part names, part paths, repo slugs, pull request numbers and card ids may
be printed.

## Which verdict counts, and when the clock starts

Exactly one verdict on a pull request counts: the newest critic verdict created
at or before the pull request's `merged_at` — the verdict the merge gate merged
on. It is found with `merge_gate.latest_verdict_record(comments, qa_login,
merge_gate.CRITIC_MARKER)` over the comments up to that time. Comments by any
login other than `--qa-login` are never verdicts, and a verdict created after
the merge is ignored. A pull request that never merged approved nothing.

The counted verdict's first line must carry `APPROVE` (`merge_gate.verdict_token`).
When it does not, the pull request approved no part and the run says
`not an APPROVE`. The approved parts are that one comment's
`🪴 part-provisional:` lines (`parts_review.provisional_records`), and two
identical lines are one part. A part an earlier APPROVE listed and the final one
dropped is not approved.

**The clock starts at the counted verdict's `created_at`.** One part is one
`(name, path)` in the repo: when two merged pull requests approve the same pair,
the earlier approval stands and the later is logged as `duplicate of PR #<n>`.

`final_approval(full_name, pr, qa_login)` is this logic as one public function,
which the weekly post reuses: the `(name, path, reasoning)` records of the
counted verdict, or `[]`.

## Which pull requests: `since`

The script lists the repo's pull requests merged on or after a Pacific day,
`since`, and reads each one's issue comments. `since` is chosen so that no
approval still waiting for its record can fall out of the scan:

- When the standing parts card's thread (`spoken_thread.read(<card>)`) holds a
  comment of kind `pipeline` whose first line opens `🪴 parts-weekly:`, `since`
  is `LOOKBACK_DAYS` (35) days before the newest such comment. The run that
  posted it recorded every part whose 14 days had passed, unless its scan
  failed, which turned that run red.
- When it holds none (the first run, or a replaced card), `since` is
  `PROVISIONAL_SINCE`, `2026-11-01`: the Pacific day the critic's
  `🪴 part-provisional:` line first reached `main` (commit `2ee57fed`, DRE-8719,
  committed `2026-11-02T05:27:03Z`).
- With `--card ""` no thread is read, and `since` is 35 days before `--now`.

`since` is never earlier than `PROVISIONAL_SINCE`. A part recorded late, its 14
days having ended before this run, carries `recorded_now: true`, so the weekly
post still names it.

## The decision

With `age = now − approved_at` and `WINDOW_DAYS = 14`:

1. A counted rejection signed inside the window → `rejected`, dated the Pacific
   day it was signed.
2. Otherwise `age < 14 days`, or no card set → `provisional`.
3. Otherwise → `permanent`, dated the Pacific day `approved_at + 14 days` falls
   on.

A part already carrying a record line keeps that status, and nothing is written
again (`already recorded`).

## The record

The critic's approval comment is never edited. The outcome is one new comment
on the same pull request, carrying exactly one line, posted once per part:

```
🪴 part-permanent: <Name> at <path> — <YYYY-MM-DD>
🪴 part-rejected: <Name> at <path> — <YYYY-MM-DD>
```

(`PERMANENT_MARK`, `REJECTED_MARK`; the date is the Pacific day the part became
permanent or was rejected.) Only a comment authored by `--writer-login` is read
back as a record. The script never writes or reads a repo-side record
(`design/LOCK.json`, Portico's `atoms.approved.test.ts`). A comment the GitHub
API refuses prints `UNKNOWN` naming the pull request, writes nothing more and
exits 1, so the run is red.

## Rejecting a part

The CEO writes on the standing parts card (`--card`), from the console, a
message opening `reject` and naming the part as `<slug>/<Name>`. A rejection
counts when all of these hold:

- `spoken_thread.read(<card>)` labels it `ceo-via-console`, so the console's
  signature on it verified;
- its words — `console_receipt.answer_text(body)` with the heading line
  dropped — start with `reject` in any case and contain `<slug>/<Name>` for a
  provisional part of this repo. When two provisional parts of the repo share a
  name, the words must also carry the path;
- it was signed (`console_receipt.parse_answer(body).at`) before
  `approved_at + 14 days`.

Every message read as a rejection attempt — readable words starting with
`reject`, or a `refused` message whose words are withheld — goes into the JSON's
`rejections` list with `counted` true or false and, when false, why: written in
Linear rather than the console, signed after the window, ambiguous (a shared
name without a path), names no provisional part of this repo, or `refused` (its
signature did not verify; its `part` is null). Silence keeps the part.

**An `unchecked` message** — the console's key could not be read, so its
signature was never checked — may be a real rejection. The run prints
`UNKNOWN`, writes nothing, files nothing and exits 1.

The read covers the newest `linear_ops.COMMENT_WINDOW` (fifty) comments. Only a
read that came back full can have left older ones out, so only then: when its
oldest comment is newer than the earliest open window's `approved_at` (a part
with no record yet), the run prints `UNKNOWN`, writes nothing and exits 1.

With `--card ""` no rejection can be read, so no part is made permanent: every
part with no record stays `provisional`, the run says `no card set`, and it
exits 0.

## The replacement card

A rejected part gets one card, filed through `linear_ops.create_card` in
`Intake`, titled `<slug>: remove the rejected part <Name> and replace its uses`,
labeled `repo:<slug>`, `--agent` and `--initiative`, and carrying on its own
line the identity

```
parts-rejected: <slug> | <Name> | <path>
```

(`IDENTITY_MARK`). Its body says which part the CEO rejected and when (PT),
where it ships, the pull request that added it, that the fix deletes the part
and moves every use to an existing shared part (naming the closest), and that
it also removes the part's entry from the repo's own record where one exists.

Before filing, the script searches every card in every state for that identity
line, with `parts_cleanup.filed` (imported, not copied). A card found — a Done
or Canceled one included — counts as filed. A search that fails files nothing
and prints `UNKNOWN`. The card is filed before the record comment is posted, so
a run that dies between the two finds the card next time and posts the record.

## The closest existing part

For each approved part, the script reads `design_parts.catalog(<repo root>)`,
leaves the part itself out, and picks the catalog part whose props are most
similar to its own, scored with `parts_review`'s own prop reader and similarity
(`_part_props`, `_similarity`), imported and never copied. The highest score
wins, ties broken by the number of shared props, then by name. No threshold
applies: the list names the closest part even when nothing is near. `closest`
is null when the catalog holds no other part, or when the part's own file is not
in the checkout.

## The JSON

`--json-out` is one object, with no reasoning anywhere in it:

```
{"repo": "<slug>", "card": "<DRE-n or null>", "since": "YYYY-MM-DD",
 "parts": [{"name": "Badge", "path": "<path>", "pr": 4601, "pr_url": "<url>",
            "approved_at": "<ISO 8601 UTC>", "status": "provisional",
            "permanent_on": null, "rejected_on": null, "replacement_card": null,
            "recorded_now": false,
            "closest": {"name": "Pill", "path": "<path>"}}],
 "rejections": [{"at": "<ISO 8601 UTC>", "counted": false, "part": "<slug>/<Name> or null",
                 "why": "<plain words, or null when counted>"}]}
```

`status` is one of `provisional`, `permanent`, `rejected`. `permanent_on` and
`rejected_on` are Pacific `YYYY-MM-DD` or null. `parts` is sorted by
`approved_at`, then `name`. `recorded_now` is true when this run decided the
part's record (with `--dry-run`, when it would have). A run that ends `UNKNOWN`
writes no JSON.

## The log

Every line opens `parts-provisional:` (`TAG`). One stdout line per part; the
rest on stderr; none carries reasoning:

```
parts-provisional: <slug> <Name> — provisional, <d> day(s) of 14
parts-provisional: <slug> <Name> — permanent, recorded on PR #<n>
parts-provisional: <slug> <Name> — rejected, replacement card <DRE-m> filed
parts-provisional: <slug> <Name> — <permanent|rejected>, already recorded
parts-provisional: <slug> <Name> — duplicate of PR #<n>, not counted
parts-provisional: <slug> — PR #<n> merged on a verdict that is not an APPROVE, no part approved
parts-provisional: <slug> — rejections read on <DRE-n>: <k> counted, <u> not counted
parts-provisional: <slug> — no card set, so no rejection can be read; nothing is made permanent
parts-provisional: <slug> — <a> approval(s) since <YYYY-MM-DD>: <p> provisional, <q> permanent, <r> rejected
parts-provisional: <slug> — UNKNOWN — <problem>      (exit 1)
```

A rejected part whose replacement card was found already filed says
`rejected, replacement card <DRE-m> already filed`. `--dry-run` reads
everything, writes no comment, files no card, and says `would record` /
`would file` in place of `recorded` / `filed`.
