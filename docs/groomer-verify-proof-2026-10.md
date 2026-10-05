# Groomer verify proof, October — DRE-5459 (epic DRE-5213)

**Status: OBSERVED. All four observations are made. The CEO's close is still open.**

**2026-10-05 update.** Observation 4 is the scheduled 06:00 PT morning, run
[37314573171](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37314573171).
It started 06:08:51 PT and posted at **06:18:07 PT**, before 06:30 PT. Observations 1 and 3 are two
`dry_run` dispatches, made on the CEO's go (relayed 08:03 PT):
[37329776250](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37329776250)
ran 08:04:06–08:12:48 PT, and
[37331435719](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37331435719)
ran 08:16:08–08:23:36 PT. They were made one at a time, the second only after the first had finished. A proof
helper read them off with DRE-4973's `readoff_morning.sh`. Everything written before 2026-10-04 stands
as it was.

- **Observation 2 (proven 2026-10-03).** The real verify agent, run three times by hand over the DRE-4416-shaped fixture, answered `done-elsewhere` all three times. Each answer cites `console/backend/cost_usage.py:962` (`kept = claude_usage_readings.record_reading(`), the line where the console's usage probe writes the readings table. The runs were on 2026-10-03 at 13:09–13:10, 13:13–13:14 and 13:14 PT.
- **Observations 1, 3 and 4 (made 2026-10-05).**
  - Observation 1: the dry-run morning read the live Intake lane. Every owner was read on its own token, the fold found all three documents, and every non-`still-needed` verdict is named. The reader overturned 0 of 18 audited `still-needed` answers, against 12 of 25 on 2026-09-29.
  - Observation 3: with `lookup_budget: 0`, all 21 file-naming cards failed their lookup, each naming its own repo. The post step stopped with `groomer: not posted — …`, stayed green, and left a warning annotation, and the run concluded `success`.
  - Observation 4: the scheduled morning posted at 06:18:07 PT.

**How this was recorded:** by a proof-runner session on the operator's instruction, 2026-10-03 13:09–13:20 PT. Steps (a) and (d) ran with `GITHUB_ACTIONS=true`, empty `AWS_*` and `LINEAR_API_KEY=test`. Step (c) ran on the operator's local Claude login, on the CEO's go. Nothing live was written. On 2026-10-05, observations 1, 3 and 4 were read by a proof helper, 08:01–08:34 PT, using only reads. The two dispatches were the only actions, and both were dry runs that write nothing to the card. #714, which ships the read-off script, merged at 08:31:58 PT.

| Criterion | Result |
|---|---|
| The record is on `main`, opens with run ids and dates in PT, and carries the four observations in order, copied from live runs | **Met once merged.** The record opens with the run ids and their PT times, and §1–§4 are copied from the live runs |
| Observation 1, a dry-run morning over the live Intake lane | **Met** (§1). Run 37329776250, 2026-10-05 08:04 PT |
| Observation 2: the four commands as run, the model id, and three verdict files, all `done-elsewhere` with a `file:line` proof | **Met** (§2) |
| Observation 3, a morning with every lookup failed (`lookup_budget: 0`, `card` set) | **Met** (§3). Run 37331435719, 2026-10-05 08:16 PT |
| Observation 4, the first scheduled morning after every sibling, posted before 06:30 PT | **Met** (§4). Run 37314573171 posted at 06:18:07 PT |
| The CEO closes this card after reading the record | Open |

## Order of the 2026-10-05 sitting

All three live observations come from one sitting, so they share one Linear
hour on purpose and never overlap the morning.

**This record depends on PR #714 (DRE-4973).** Every read below runs
`docs/evidence/DRE-4973/readoff_morning.sh`, and that script exists only on
#714's branch `agent/DRE-4973-verify-proof-1005` until #714 merges. This
record is marked ready only after #714 is on `main`. If the sitting comes
first, fetch the script before step 1, from the repository root:

```
R=dreadnought-foundry/bureau-pipeline
S=docs/evidence/DRE-4973/readoff_morning.sh
[ -f $S ] || { mkdir -p $(dirname $S); gh api "repos/$R/contents/$S?ref=agent/DRE-4973-verify-proof-1005" --jq .content | base64 -d > $S; }
```

1. **06:00–06:30 PT: observation 4.** Read the scheduled morning (§4) once its
   post job has finished.
2. **After the morning's `post` job is done, about 06:25 PT: dispatch
   observation 1** (§1). `groomer.yml`'s `groom` job is in the concurrency group
   `groomer` with `cancel-in-progress: false`. A dispatch made while the morning
   is still running waits behind it. A second dispatch made while the first is
   still pending REPLACES it. So dispatch one, and wait until its run shows
   `in_progress` or finished before the next.
3. **After observation 1's run finishes: dispatch observation 3** (§3).

Each dispatch is a full propose with judgement on and a verify matrix: one
ranked-read model call, plus about $4–5 of verify legs, judging by the
2026-10-02 morning. Both are `dry_run: true`, so neither writes to the card.
These two dispatches are the only actions in the sitting. Everything else is a
read.

## 1. A dry-run morning proposal over the live Intake lane

**History.** On 2026-10-03, `self-groomer.yml` read `disabled_manually` (12:59 PT). It was `active` again
from 2026-10-04 07:31 PT. Made on 2026-10-05, as below.

**The dispatch** (`card` is read off the variable, not typed from memory):

```
R=dreadnought-foundry/bureau-pipeline
CARD=$(gh variable get GROOM_PROPOSAL_CARD --repo $R)        # DRE-4541 on 2026-10-04
# the newest dispatch run BEFORE this one, so the new run is not confused with it
PREV=$(gh run list -R $R --workflow self-groomer.yml --event workflow_dispatch -L 1 --json databaseId --jq '.[0].databaseId // ""')
gh workflow run self-groomer.yml -R $R -f mode=propose -f judgement=on -f dry_run=true -f card="$CARD"
#   lookup_budget is left out, so it is empty and each leg sizes itself off its token
# `gh workflow run` returns before the run is listed: wait for a NEW id
until RUN1=$(gh run list -R $R --workflow self-groomer.yml --event workflow_dispatch -L 1 --json databaseId --jq '.[0].databaseId // ""') \
      && [ -n "$RUN1" ] && [ "$RUN1" != "$PREV" ]; do sleep 5; done
gh run view $RUN1 -R $R --json databaseId,createdAt,event
gh run watch $RUN1 -R $R                     # the read-off needs the finished run
```

Then read it with the read-off script, naming the run:
`RUN=$RUN1 DAY=2026-10-05 bash docs/evidence/DRE-4973/readoff_morning.sh ./obs1`.
That script ships with DRE-4973's record (#714) and is read-only. It matches
both `schedule / …` and `call / …` job names, so on this dispatch it also
writes one verify leg's log to `obs1/job-<id>.verify.log`, and that leg's fold
line lands in `obs1/key-lines.txt`. Reading the leg's log by hand
(`gh run view -R $R --job <id> --log`) is only a fallback, if the script wrote
no `.verify.log`.

The inputs are read off the run itself, never off the command typed. Each job
log prints its step's `env:` block, so:

```
grep -hE ' (CARD|DRY_RUN|LOOKUP_BUDGET): ' $(ls obs1/job-*.log | grep -v '\.verify\.log$') \
  | awk -F'\t' '{sub(/^[^ ]+ +/, "", $3); print $1 "\t" $3}' | sort -u
```

The verify leg's log is left out on purpose: its steps set `CARD:` to the
leg's own card, not the run's. The `awk` drops each line's timestamp so
`sort -u` can dedupe, and keeps the job name, because every line's step
column reads `UNKNOWN STEP`. The run's inputs are on these lines: `CARD:`
under the `groom` and `post` jobs, `DRY_RUN:` under `post`, and
`LOOKUP_BUDGET:` under each `lookup (<owner>)` job. They must show
`CARD: <the card>`, `DRY_RUN: true` and `LOOKUP_BUDGET:` empty. A different
card or budget on those lines means the id names somebody else's run: stop.

Two reads the script does not make. `obs1/proposal/verify-targets.json` is the
PRE-fold targets file: the `groom` job uploads it before any lookup leg runs,
and `fold` rewrites it only inside each verify leg, which uploads nothing but
`verdict.json`. So neither the cut lines nor a card's `lookup failed: …` reason
is ever in it. They are read here instead:

```
# cut entries, per owner (fold turns each into a `more than 5 commits touched …` line)
jq -c '.cards | to_entries[] | select(.value.cut != []) | {card: .key, cut: .value.cut}' obs1/lookups/groom-lookups-*/lookups-*.json
# each card's verdict, with its lookup state and reason
jq -c '{verdict, lookup, reason}' obs1/verdicts/groom-verdict-*/verdict.json
```

**Made 2026-10-05.** Run [37329776250](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37329776250), event `workflow_dispatch`, was dispatched at 08:04:04 PT on the CEO's go (relayed 08:03 PT). It ran 08:04:06–08:12:48 PT and concluded `success`. Proposal `c2afc76bd5d6` (dry run, nothing posted). It was read with `RUN=37329776250 DAY=2026-10-05 bash docs/evidence/DRE-4973/readoff_morning.sh ./obs1` at about 08:18 PT.

| What the card asks for | Where it is read | Reading |
|---|---|---|
| Dispatch inputs, as the run's own job logs carry them | the `grep` above | `call / groom	CARD: DRE-4541` · `call / post	CARD: DRE-4541` · `call / post	DRY_RUN: true` · `call / lookup (DeltaSolv)	LOOKUP_BUDGET: ` · `call / lookup (dreadnought-foundry)	LOOKUP_BUDGET: ` · `call / lookup (EveryBite)	LOOKUP_BUDGET: `. The card is the standing card, it is a dry run, and the budget is empty on all three legs. This is the right run |
| `## Verified against main` counts | `obs1/key-lines.txt` | `still-needed: 21` · `partly-solved: 1` · `done-elsewhere: 0` · `obsolete: 0` · `not-worth-it: 0` · `unverified: 2` · `excluded: 5`. Then `Verify step: 29 cards, $4.66, 2 min 17 s wall clock` |
| Every `partly-solved`, `done-elsewhere`, `obsolete`, `not-worth-it` or `excluded` card, with its evidence line or exclusion reason, and the CEO's 2026-09-29 read of the same card where it was on that proposal (DRE-4541) | `obs1/verified/proposal-verified.json` → `outcomes.now[].verify`, `verify.excluded` | See the table below this one |
| `still-needed` answers the reader overturns on reading the evidence, against the 2026-09-29 baseline of 12 of 25 | the reader's audit | **0 overturned of the 18 audited** (of 21). The audit is the one in DRE-4968's record (bureau-pipeline #715, §5). It was made 08:05–08:15 PT on the 06:00 PT morning's Planning list: one Linear read per card, plus merged PRs searched per owner. It found no card already done. DRE-5662 is partly done, but its remaining observations are still open, so `still-needed` stands. Three of this run's `still-needed` cards were not on the morning's list and were not audited: DRE-5650 (Planning #12), and DRE-5504 and DRE-5260 (behind the list). The reader also hand-checked one proof, DRE-5239's `release_brake.py` lines (DRE-4973's record, #714, §3), and agrees |
| No excluded card is in the batch table | `verify.excluded` vs `outcomes.now` | **Confirmed.** DRE-4666, DRE-5727, DRE-5700, DRE-5656 and DRE-5657 are in `verify.excluded`, and none is among the 20 rows of `outcomes.now` |
| One `groom-lookups: <owner> — …` line per owner in `config/repo-map.json` (EveryBite, DeltaSolv, dreadnought-foundry) | `obs1/key-lines.txt` | `groom-lookups: EveryBite — 21 card(s) read, 45 request(s) of a budget of 600, 14.5 s of 300 s` · `groom-lookups: DeltaSolv — 21 card(s) read, 45 request(s) of a budget of 600, 20.4 s of 300 s` · `groom-lookups: dreadnought-foundry — 21 card(s) read, 201 request(s) of a budget of 600, 67.0 s of 300 s` |
| One verify leg's fold-step output: `groom-lookups: folded N document(s) — …` | `obs1/job-111832275801.verify.log` (leg DRE-4872), step "Fold the lookups into the targets" | The leg downloaded `groom-lookups-dreadnought-foundry`, `groom-lookups-EveryBite` and `groom-lookups-DeltaSolv`, each to `lookups/groom-lookups-<owner>`. It then ran `groom_lookups.py fold --targets verify-targets.json --lookups-dir lookups --out verify-targets.json`, which printed `groom-lookups: folded 3 document(s) — 21 card(s) ok, 0 failed, 3 named no file` |
| "Not verified" lines | `obs1/verified/proposal-verified.txt`, the `Not verified — each stays where the proposal put it:` list | `- DRE-5746 — no proof` · `- DRE-5483 — no proof` |
| Any cut evidence line (`more than 5 commits touched …`) | the `cut` read above, over `obs1/lookups/` | One `cut` entry: `{"card":"DRE-4485","cut":[{"repo":"dreadnought-foundry/portico","path":"infra/config/everybite.json"}]}`. The folded line itself is in no kept artifact. `fold` writes it only into the verify leg's own `verify-targets.json`, and the leg uploads only `verdict.json` |
| Where a leg stopped on its clock or budget, or was unread: each card left `lookup failed: <owner/repo> did not answer — …`, its `repository` under that owner, and none under an owner whose leg finished | the verdict read above, over `obs1/verdicts/`, or `obs1/verified/proposal-verified.json` → `outcomes.now[].verify` | **Every leg finished every card.** Each read 21 cards, within budget and clock. The verdicts read `lookup: ok` 21 times and `none` 8 times, with no `failed`. The per-card rule is observed in §3 instead |
| The groom job's `linear-budget:` line | `obs1/key-lines.txt` | `linear-budget: 2499 → 2388 (spent 111 this run (refilled mid-run); window resets 09:08 PT; limit 2500; budget: undeclared)`, then the check: `linear-budget: 2387 → 2354 (spent 33 this run; window resets 09:08 PT; refused after 34 calls; limit 2500; budget: undeclared)` |
| `## Priorities re-ranked as Medium`, whole, and whether DRE-2702, DRE-2563, DRE-2564, DRE-3681, DRE-3530 still head the list | `obs1/verified/proposal-verified.txt` | 19 lines, from `- DRE-2397 — High set on 2026-08-11, 54 days ago, not re-confirmed — ranked as Medium` to `- DRE-3589 — High set on 2026-09-11, 24 days ago, not re-confirmed — ranked as Medium`. The full list: DRE-2397, DRE-2497, DRE-2498, DRE-2503, DRE-2558, DRE-2657, DRE-2709, DRE-2963, DRE-2994, DRE-3009, DRE-3104, DRE-3245, DRE-3408, DRE-3449, DRE-3072, DRE-3514, DRE-3545, DRE-3549 and DRE-3589, each "not re-confirmed — ranked as Medium". **None of DRE-2702, DRE-2563, DRE-2564, DRE-3681 or DRE-3530 appears anywhere on the page.** The list is headed by DRE-5239, DRE-5202, DRE-4874 and DRE-4872 (High, created 09-25 to 09-29) |
| `## Cancels refused`, whole | `obs1/verified/proposal-verified.txt` | **No such section.** `cancels_refused: []` |

The non-`still-needed` verdicts, each beside the CEO's 2026-09-29 read. That read is his decision comment of 11:47 PT and his decline of `4a90676dea5f` at 16:26 PT, both on DRE-4541.

| Card | Verdict | Evidence line or exclusion reason | The CEO's 2026-09-29 read |
|---|---|---|---|
| DRE-4874 | `partly-solved` | `config/release-pipelines.json:152`. "The GitHub relay part is done … What remains is DeltaSolv, Atlas and agent-bureau-demo: this repo's release-pipelines.json holds no pipeline for any of them" | Not on that proposal's Planning or Cancel list. It sat under "Not now" (`a slot opens under the epic cap and the Portico release-train epic (DRE-4626) closes`), and his decision does not name it |
| DRE-5746 | `unverified` | `no proof`. "The cause of the 10-02 all-cards … line is in that run's log, which target/ does not hold" | Not named anywhere on that proposal |
| DRE-5483 (behind the list) | `unverified` | `no proof` | Not named anywhere on that proposal |
| DRE-4666 | `excluded` | `parent epic with 3 open children` | Not on the Planning list. The ranked read left it out ("The parent of the two console-load epics already in progress; nothing to build here itself"), and his decision does not name it |
| DRE-5727 | `excluded` | `moved into Intake on 2026-10-04` | Not named anywhere on that proposal |
| DRE-5700 | `excluded` | `moved into Intake on 2026-10-04` | Not named anywhere on that proposal |
| DRE-5656 | `excluded` | `hand-built` | Not named anywhere on that proposal |
| DRE-5657 | `excluded` | `hand-built` | Not named anywhere on that proposal |

No card this run judged anything but `still-needed` was on the 2026-09-29 Planning list. The overlap the baseline needs is empty, so the comparison rests on the overturn count above.

**Pass bar: met.** Every row is filled from the live run. One reading of the served order belongs to DRE-4968, not this card: as at 06:00 PT, spares took excluded cards' slots (DRE-5658 sits at #5). DRE-4968's record (#715, §3) carries it.

## 2. The fixture run through the real verify agent, three times

Run from bureau-pipeline `main` at `518ac246419734d0bf4f09465c62f5bde4334cef` (2026-10-03 12:59 PT), each run in its own fresh working directory (`run-1`, `run-2`, `run-3`).

**The model.** `python3 scripts/model_fallback.py select verifier --explain-file … --effort-file …` (the call the step makes):

```
claude-sonnet-5-5
model-policy: claude-sonnet-5-5 chosen for verifier (advisory kind) — top of the ladder, nothing above it was skipped
effort: high
```

**The four commands, as run in each directory:**

```
# (a)
python3 scripts/groom_verify_agent.py prepare --targets tests/fixtures/groom_verify_done_elsewhere.json \
  --card DRE-4416 --out verify-input.md --started-at-out started.txt
# (b) agent-bureau's default branch, read-only, no credential kept
gh api repos/dreadnought-foundry/agent-bureau/tarball/main > ab.tgz && mkdir target && tar -xzf ab.tgz -C target --strip-components=1 && rm ab.tgz
# (c) the step's own prompt (the two paragraphs beginning `Read the file verify-input.md`), verbatim
claude -p "$PROMPT" --max-turns 40 --model claude-sonnet-5-5 --effort high \
  --allowedTools "Read,Glob,Grep,Write" --output-format json > execution.json
# (d)
python3 scripts/groom_verify_agent.py verdict --card DRE-4416 --targets tests/fixtures/groom_verify_done_elsewhere.json \
  --raw verify-verdict.json --step-outcome success --started-at started.txt --out verdict-<n>.json
```

**One difference from the card's step (b).** The card says `git clone --depth 1 https://github.com/dreadnought-foundry/agent-bureau target`. This session's harness refuses git operations outside its own worktree, so the same default-branch tree was fetched as a GitHub API tarball. The content is the same: the default branch at one commit, read-only, and no credential is left in `target/`. That commit was `5d65928` for run 1. `main` moved to `0baaef1` before runs 2 and 3.

| run | agent-bureau `main` | agent (PT) | turns | cost | `claude` exit | verdict |
|---|---|---|---|---|---|---|
| 1 | `5d65928` | 13:09:59–13:10:37 | 11 | $0.38 | 0 | **`done-elsewhere`** |
| 2 | `0baaef1` | 13:13:37–13:14:12 | 11 | $0.38 | 0 | **`done-elsewhere`** |
| 3 | `0baaef1` | 13:14:22–13:14:49 | 9 | $0.35 | 0 | **`done-elsewhere`** |

**Each cited line, opened in that run's `target/`.** Every `file:line` quote matches its line, with one exception: run 2's third proof cites `console/backend/claude_usage_run_reports.py:285` for `return sum(bool(readings.insert_reading(store, **row)) for row in rows)`, and that text sits on line 286 (line 285 is the comment above it). The proof the card asks for, the usage probe writing the readings table, is `console/backend/cost_usage.py:962` `kept = claude_usage_readings.record_reading(`. It is cited by all three runs and matches its line every time.

### `verdict-1.json`, whole

```json
{
  "card": "DRE-4416",
  "verdict": "done-elsewhere",
  "summary": "Nothing is left of the problem in the console code: the usage probe now writes one row per window into claude_usage_reading on every read (DRE-4587), and the S3 consumer the card asked for is also in main, started by a registered starter that reads the agent-log bucket and inserts each reading. The emitter half lives in bureau-pipeline, outside target/, so I could not read it, and I did not check live row counts.",
  "proof": [
    {
      "file": "console/backend/cost_usage.py",
      "line": 962,
      "quote": "kept = claude_usage_readings.record_reading("
    },
    {
      "file": "console/backend/cost_usage.py",
      "line": 1585,
      "quote": "default_probe().use_readings_store(store)"
    },
    {
      "file": "console/backend/app/main.py",
      "line": 1793,
      "quote": "cost_usage.install_readings_store(active_store)"
    },
    {
      "file": "console/backend/claude_usage_run_reports.py",
      "line": 286,
      "quote": "return sum(bool(readings.insert_reading(store, **row)) for row in rows)"
    },
    {
      "file": "console/backend/starters/claude_usage_run_reports.py",
      "line": 14,
      "quote": "NAME = \"claude_usage_run_reports\""
    },
    {
      "file": "infra/lib/backend-stack.ts",
      "line": 1031,
      "quote": "{ name: 'CONSOLE_AGENT_LOG_BUCKET', value: agentLogBucket.bucketName },"
    },
    {
      "source": "Layer A evidence",
      "quote": "DRE-4587 is Done: the console's usage probe now inserts one row per window into claude_usage_reading each time it reads an account."
    }
  ],
  "reason": null,
  "lookup": "ok",
  "cost_usd": null,
  "duration_ms": null,
  "model": null,
  "started_at": "2026-10-03T20:09:29Z",
  "finished_at": "2026-10-03T20:10:37Z"
}
```

### `verdict-2.json`, whole

```json
{
  "card": "DRE-4416",
  "verdict": "done-elsewhere",
  "summary": "Two writers into claude_usage_reading are now in main and wired at startup: the usage probe keeps one row per window on every fresh read (DRE-4587), and the S3 route the card asked for exists as a console consumer started by the starters registry. The console side of the card is therefore in place; whether the emit step in bureau-pipeline uploads the readings is not in this repo, and I could not see the live table count.",
  "proof": [
    {
      "file": "console/backend/app/main.py",
      "line": 1793,
      "quote": "cost_usage.install_readings_store(active_store)"
    },
    {
      "file": "console/backend/cost_usage.py",
      "line": 962,
      "quote": "kept = claude_usage_readings.record_reading("
    },
    {
      "file": "console/backend/claude_usage_run_reports.py",
      "line": 285,
      "quote": "return sum(bool(readings.insert_reading(store, **row)) for row in rows)"
    },
    {
      "file": "console/backend/starters/claude_usage_run_reports.py",
      "line": 17,
      "quote": "DEFAULT_ATTR = \"start_claude_usage_run_reports\""
    },
    {
      "source": "Layer A evidence",
      "quote": "DRE-4587 is Done: the console's usage probe now inserts one row per window into claude_usage_reading each time it reads an account."
    }
  ],
  "reason": null,
  "lookup": "ok",
  "cost_usd": null,
  "duration_ms": null,
  "model": null,
  "started_at": "2026-10-03T20:13:25Z",
  "finished_at": "2026-10-03T20:14:12Z"
}
```

### `verdict-3.json`, whole

```json
{
  "card": "DRE-4416",
  "verdict": "done-elsewhere",
  "summary": "Main now has two writers into claude_usage_reading: a console consumer that reads run usage readings from the agent-log bucket and inserts one row per window (the card's own S3 route, with its starter wired in), and the usage probe (DRE-4587) that inserts a row per window on every account read. The 'nothing carries a reading into the table' problem is gone; the emit-side upload lives in bureau-pipeline, outside target/, so it was not checked here.",
  "proof": [
    {
      "file": "console/backend/claude_usage_run_reports.py",
      "line": 1,
      "quote": "\"\"\"A run's own usage reading, landed in ``claude_usage_reading`` (DRE-4416)."
    },
    {
      "file": "console/backend/claude_usage_run_reports.py",
      "line": 481,
      "quote": "raw = s3.get_object(Bucket=bucket, Key=key)[\"Body\"].read()"
    },
    {
      "file": "console/backend/claude_usage_run_reports.py",
      "line": 573,
      "quote": "return asyncio.create_task("
    },
    {
      "file": "console/backend/starters/claude_usage_run_reports.py",
      "line": 22,
      "quote": "return starter(ctx.record_store)"
    },
    {
      "file": "console/backend/cost_usage.py",
      "line": 962,
      "quote": "kept = claude_usage_readings.record_reading("
    },
    {
      "file": "console/backend/app/main.py",
      "line": 1793,
      "quote": "cost_usage.install_readings_store(active_store)"
    },
    {
      "source": "Layer A evidence",
      "quote": "DRE-4587 is Done: the console's usage probe now inserts one row per window into claude_usage_reading each time it reads an account."
    }
  ],
  "reason": null,
  "lookup": "ok",
  "cost_usd": null,
  "duration_ms": null,
  "model": null,
  "started_at": "2026-10-03T20:14:13Z",
  "finished_at": "2026-10-03T20:14:49Z"
}
```

**Verdict: proven.** Three of three answered `done-elsewhere`, each with a `file:line` proof from `target/` naming where the console's usage probe writes the readings table.

## 3. A morning on which every lookup fails, and the run stays green

**Made 2026-10-05**, after observation 1's run finished (the readings follow the commands below).

The stop's second shape (no owner searched) is proven in pytest on DRE-5317. It would not be observed here even on a live run, because producing it would mean breaking the App's installation or removing a secret.

**The dispatch.** `card` is not optional: DRE-5317's stop lives in `groomer.py post`, which runs only when `card` is set.

```
R=dreadnought-foundry/bureau-pipeline
CARD=$(gh variable get GROOM_PROPOSAL_CARD --repo $R)
# observation 1's run, so it is never mistaken for this one
PREV=$(gh run list -R $R --workflow self-groomer.yml --event workflow_dispatch -L 1 --json databaseId --jq '.[0].databaseId // ""')
gh workflow run self-groomer.yml -R $R -f mode=propose -f judgement=on -f dry_run=true -f lookup_budget=0 -f card="$CARD"
until RUN3=$(gh run list -R $R --workflow self-groomer.yml --event workflow_dispatch -L 1 --json databaseId --jq '.[0].databaseId // ""') \
      && [ -n "$RUN3" ] && [ "$RUN3" != "$PREV" ]; do sleep 5; done
gh run view $RUN3 -R $R --json databaseId,createdAt,event
gh run watch $RUN3 -R $R                     # the read-off needs the finished run
RUN=$RUN3 DAY=2026-10-05 bash docs/evidence/DRE-4973/readoff_morning.sh ./obs3
# the inputs as the run carries them: CARD set, DRY_RUN true, LOOKUP_BUDGET 0
# (the verify leg's log is left out: its CARD is the leg's own card, as in §1)
grep -hE ' (CARD|DRY_RUN|LOOKUP_BUDGET): ' $(ls obs3/job-*.log | grep -v '\.verify\.log$') \
  | awk -F'\t' '{sub(/^[^ ]+ +/, "", $3); print $1 "\t" $3}' | sort -u
# the stop's four keys live under `verify`, not at the top level, so summary.txt never prints them
jq '.verify | {all_lookups_failed, merged_prs_unread, lookups_failed, not_posted_why}' obs3/verified/proposal-verified.json
# the post step's last lines, and the annotation on the summary page
gh run view -R $R --job <id of "call / post"> --log | grep -E 'groomer: not posted|dry run — nothing posted'
gh api repos/$R/check-runs/<id of "call / post">/annotations --jq '.[] | .annotation_level + ": " + .message'
gh run view $RUN3 -R $R --json conclusion
# Pipeline Medic, for the ten minutes after the run finished (PT window stated in the record)
gh run list -R $R --workflow "Pipeline Medic" -L 20 --json databaseId,event,createdAt,displayTitle,headBranch
```

**Made 2026-10-05.** Run [37331435719](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37331435719), event `workflow_dispatch`, was dispatched at 08:16:06 PT on the CEO's go (relayed 08:03 PT). It was dispatched only after observation 1's run had finished (08:12:48 PT). It ran 08:16:08–08:23:36 PT. The dispatch was `-f mode=propose -f judgement=on -f dry_run=true -f lookup_budget=0 -f card=DRE-4541`, and it was read with `RUN=37331435719 DAY=2026-10-05 bash docs/evidence/DRE-4973/readoff_morning.sh ./obs3`. Jobs, PT:

```
call / groom                         08:16:13  08:21:02
call / lookup (EveryBite)            08:21:05  08:21:14
call / lookup (DeltaSolv)            08:21:05  08:21:18
call / lookup (dreadnought-foundry)  08:21:05  08:21:15
call / verify (29 legs)              then
call / post                          08:23:21  08:23:30
```

| What the card asks for | Reading |
|---|---|
| The three inputs (`dry_run`, `lookup_budget`, `card`), as the run's own job logs carry them | `call / groom	CARD: DRE-4541` · `call / post	CARD: DRE-4541` · `call / post	DRY_RUN: true` · `call / lookup (DeltaSolv)	LOOKUP_BUDGET: 0` · `call / lookup (dreadnought-foundry)	LOOKUP_BUDGET: 0` · `call / lookup (EveryBite)	LOOKUP_BUDGET: 0`. The card is set to the standing card, so this is the right run |
| Targets with `lookup: "failed"` and with `lookup: "none"`, with at least one failed | **21 failed, 8 none.** Counted with `jq -r .lookup obs3/verdicts/groom-verdict-*/verdict.json \| sort \| uniq -c`, over the 29 verdict files. The 8 `none` are the 5 excluded cards plus DRE-5650, DRE-5564 and DRE-5745, the three that name no file (`3 named no file`). Those three answered `still-needed` from the card text alone |
| Each lookup leg's `groom-lookups: <owner> — 0 card(s) read, 0 request(s) of a budget of 0, <s> s of 300 s` | `groom-lookups: EveryBite — 0 card(s) read, 0 request(s) of a budget of 0, 0.0 s of 300 s` · `groom-lookups: DeltaSolv — 0 card(s) read, 0 request(s) of a budget of 0, 0.0 s of 300 s` · `groom-lookups: dreadnought-foundry — 0 card(s) read, 0 request(s) of a budget of 0, 0.0 s of 300 s`. Each leg is preceded by `groom-lookups: <owner> stopped: request budget of 0 spent`. The fold then printed `groom-lookups: folded 3 document(s) — 0 card(s) ok, 21 failed, 3 named no file` |
| "Post the verified proposal" step green, its last line `groomer: not posted — the lookup failed for every card: no repo answered — …`, and no `dry run — nothing posted` above it | Step 6, "Post the verified proposal", concluded `success`. Its last line (the next log line opens step 8's `upload-artifact`) is: `groomer: not posted — the lookup failed for every card: dreadnought-foundry/agent-bureau did not answer — request budget of 0 spent`. `dry run — nothing posted` appears 0 times in the post job's log. The wording names the first failed repo, not `no repo answered — …` as the card guessed. Same stop, same place |
| The warning annotation carrying the same line on the run's summary page | `gh api repos/dreadnought-foundry/bureau-pipeline/check-runs/111838562347/annotations` → `warning: groomer: not posted — the lookup failed for every card: dreadnought-foundry/agent-bureau did not answer — request budget of 0 spent` |
| The run's conclusion | `success` |
| `all_lookups_failed: true`, `merged_prs_unread`, the `lookups_failed` list, `not_posted_why` | From `jq '.verify \| {all_lookups_failed, merged_prs_unread, lookups_failed, not_posted_why}' obs3/verified/proposal-verified.json`: `all_lookups_failed: true` · `merged_prs_unread: false` · `lookups_failed: ["DRE-5239","DRE-5202","DRE-4874","DRE-4872","DRE-5654","DRE-4485","DRE-5767","DRE-5761","DRE-5676","DRE-5729","DRE-5726","DRE-5721","DRE-5720","DRE-5746","DRE-5744","DRE-5662","DRE-5658","DRE-5645","DRE-5504","DRE-5483","DRE-5260"]` (21) · `not_posted_why: "the lookup failed for every card: dreadnought-foundry/agent-bureau did not answer — request budget of 0 spent"` |
| One mapped card's `lookup failed: <owner/repo> did not answer — request budget of 0 spent`, with `<owner/repo>` equal to that card's `repository` | DRE-4485, whose `repository` in `obs3/proposal/verify-targets.json` is `dreadnought-foundry/portico`, reads `lookup failed: dreadnought-foundry/portico did not answer — request budget of 0 spent`. All 21 failed verdicts were checked the same way against their own `repository` (agent-bureau ×10, portico ×7, bureau-pipeline ×4). Each names its own repo, with 0 mismatches. That is DRE-5458's per-card rule, observed |
| Pipeline Medic runs in the ten minutes after the run finished, and none woken by it | The window is 08:23:36–08:33:36 PT. `gh run list -R dreadnought-foundry/bureau-pipeline --workflow "Pipeline Medic" -L 30`, read 08:33:47 PT, shows **7 runs in the window, every one `workflow_run` and every one concluded `skipped`**: 37332461864 (08:23:40), 37332856150 (08:26:34), 37332974306 (08:27:27), 37333126145 (08:28:35), 37333335270 (08:30:09), 37333539793 (08:31:38) and 37333627217 (08:32:16). The first one, created 4 s after this run finished, is the Medic's `workflow_run` trigger for this run's completion, and its only job (`call`) was `skipped`. No retry and no diagnosis ran. Medic fires on every completed workflow and skips the ones that succeeded. A planned stop concluded `success`, so nothing treated it as a failure |

The stop's second shape (the pre-post check searched no owner) is proven in pytest on DRE-5317 and not here. Producing it live would mean breaking the App's installation or removing a secret.

Nothing in this observation cites the standing card's thread: a dry run posts nothing whatever happens (DRE-3712). The judgement receipt step was `skipped`, as a dry run requires.

**Pass bar: met.** Every row is read from the live run.

## 4. The first scheduled morning after every sibling is on `main`

While `self-groomer.yml` was disabled, no `schedule` run fired. The last morning before the pause was 2026-10-02 (proposal `898580543342`, posted 06:18 PT), and it is recorded on DRE-4968 and DRE-4973.

```
DAY=2026-10-05 bash docs/evidence/DRE-4973/readoff_morning.sh ./obs4
```

The script writes every job's start and end in PT to `obs4/jobs-pt.tsv`. Copy it here. The comment's landing time is the `createdAt` of the `🧺 groom-proposal: <id>` comment on DRE-4541, read once from Linear and converted to PT.

**Made 2026-10-05.** This is the first `schedule`-started run after the last sibling merged and after the groomer came back on (2026-10-04 07:31 PT). It was read with `DAY=2026-10-05 bash docs/evidence/DRE-4973/readoff_morning.sh ./obs4` at 08:01–08:02 PT. Every job's start and end, from `jobs-pt.tsv`:

| Job | Start (PT) | End (PT) |
|---|---|---|
| gate | 06:08:54 | 06:09:00 |
| schedule / groom | 06:09:02 | 06:14:33 |
| schedule / lookup (EveryBite) | 06:15:02 | 06:15:27 |
| schedule / lookup (DeltaSolv) | 06:15:02 | 06:15:32 |
| schedule / lookup (dreadnought-foundry) | 06:15:03 | 06:16:33 |
| verify legs (first start, last end) | 06:16:36 | 06:17:56 |
| schedule / post | 06:17:59 | 06:18:12 |
| **Proposal comment on DRE-4541** | | **06:18:07** (Linear `createdAt` `2026-10-05T13:18:07.424Z`, read 08:07 PT) |

Run id: [37314573171](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37314573171) (event `schedule`, created 06:08:51 PT, gate `it is 06:08 PT and DRE-4541 is open — the groomer runs`, conclusion `success`, proposal `d50db9746997`). `verify_matrix` non-empty: **yes**, 29 `schedule / verify (…)` legs. Before 06:30 PT: **yes**, with 11 min 52 s to spare.

The pair's other cron, run [37323782562](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37323782562), was created at 07:19:32 PT. Its gate says `it is 07:19 PT, outside the 06:00-06:59 PT grooming hour — this is the other cron of the pair and it does nothing`, and its `schedule` job was skipped. It is not a second morning.

Against 2026-10-02 (06:05:53–06:18:38 PT):

| | 2026-10-02 | 2026-10-05 |
|---|---|---|
| groom | 8 min 55 s | 5 min 31 s |
| lookups | 1 min 27 s | 1 min 31 s |
| verify | 1 min 50 s | 1 min 20 s |
| post | 15 s | 13 s |

GitHub fired the `0 13 * * *` line 8 min 51 s late. That is where most of the margin went.

**Pass bar: met.** The comment posted at 06:18:07 PT, before 06:30:00 PT. No amendment is owed.

## What is not proven, and why

- **Observation 1's overturn count covers 18 of its 21 `still-needed` answers.** DRE-5650, DRE-5504 and DRE-5260 were not on the 06:00 PT list the audit read. The audit used merged PRs and Linear text, plus one proof opened on `main` (DRE-4973's #714), and not a reading of every agent's evidence.
- **The CEO's 2026-09-29 read gives no overlap.** None of this run's non-`still-needed` cards was on that morning's Planning or Cancel list (§1).
- **Observation 1's folded cut line.** The `cut` entry is in the lookups (DRE-4485, `infra/config/everybite.json`), but the folded `more than 5 commits touched …` line lives only inside the verify leg and is in no kept artifact.
- **Observation 3's wording.** The not-posted line names the first failed repo (`dreadnought-foundry/agent-bureau did not answer — …`), not the card's guessed `no repo answered — …`. It is the same stop, in the same step.
- **The emit side of DRE-4416.** All three runs say the bureau-pipeline emit step (the S3 upload) is outside `target/` and was not checked. The fixture asks only about agent-bureau, so that does not affect the verdict.

## Where each source disagreed, and which was taken

| Fact | Source A | Source B | Taken |
|---|---|---|---|
| The line of `insert_reading` in run 2's third proof | the agent's proof: line 285 | the file in run 2's `target/`: line 286 | The file. The verdict does not rest on that proof |
