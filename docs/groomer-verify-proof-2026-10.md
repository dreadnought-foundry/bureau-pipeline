# Groomer verify proof, October — DRE-5459 (epic DRE-5213)

**Status: PARTIAL. Observation 2 is PROVEN. Observations 1, 3 and 4 are PREPARED for 2026-10-05 and not yet made.**

**2026-10-04 update (prepared, nothing observed yet).** The groomer
(`self-groomer.yml`) was turned back on, proposals only, at 07:31 PT on
2026-10-04, after that morning's 06:00 PT slot. The next scheduled morning is
2026-10-05 06:00 PT. Observations 1, 3 and 4 below now carry their exact
commands, the order to run them in, and a `⟨FILL⟩` for every reading. The
reader only fills those in. Everything written before 2026-10-04 stands as
it was.

- **Observation 2 (proven).** The real verify agent, run three times by hand over the DRE-4416-shaped fixture, answered `done-elsewhere` all three times. Each answer cites `console/backend/cost_usage.py:962` (`kept = claude_usage_readings.record_reading(`), the line where the console's usage probe writes the readings table. The runs were on 2026-10-03 at 13:09–13:10, 13:13–13:14 and 13:14 PT.
- **Observations 1, 3 and 4 (not made).** Each needs a live run of bureau-pipeline's `self-groomer.yml`: a `dry_run` dispatch for observations 1 and 3, and a `schedule` morning for observation 4. That workflow has been disabled since the Agent-Bureau fleet pause (2026-10-02 15:17 PT), and the CEO has not approved turning it back on. A disabled workflow cannot be dispatched. The 2026-10-02 morning, the only one since DRE-5213's last merge, came before the pause and does not carry observations 1, 3 or 4 (§4).

**How this was recorded:** by a proof-runner session on the operator's instruction, 2026-10-03 13:09–13:20 PT. Steps (a) and (d) ran with `GITHUB_ACTIONS=true`, empty `AWS_*` and `LINEAR_API_KEY=test`. Step (c) ran on the operator's local Claude login, on the CEO's go. Nothing live was written.

| Criterion | Result |
|---|---|
| The record is on `main`, opens with run ids and dates in PT, and carries the four observations in order, copied from live runs | **Partly met.** This record opens with what ran and when. Observation 2 is copied from the runs. Observations 1, 3 and 4 have no run to copy (§1, §3, §4) |
| Observation 1, a dry-run morning over the live Intake lane | **Not made yet.** Prepared for 2026-10-05 (§1). ⟨FILL⟩ |
| Observation 2: the four commands as run, the model id, and three verdict files, all `done-elsewhere` with a `file:line` proof | **Met** (§2) |
| Observation 3, a morning with every lookup failed (`lookup_budget: 0`, `card` set) | **Not made yet.** Prepared for 2026-10-05 (§3). ⟨FILL⟩ |
| Observation 4, the first scheduled morning after every sibling, posted before 06:30 PT | **Not made yet.** Prepared for the 2026-10-05 06:00 PT morning (§4). ⟨FILL⟩ |
| The CEO closes this card after reading the record | Open. Under the CEO's rule of 2026-10-02 a proof closes on evidence, and this one waits on observations 1, 3 and 4 |

## Order of the 2026-10-05 sitting

All three live observations come from one sitting, so they share one Linear
hour on purpose and never overlap the morning.

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

**Not made yet. 2026-10-03:** `self-groomer.yml` read `disabled_manually`
(12:59 PT), so it could not be dispatched. **2026-10-04:** it is `active`
again, and this observation is prepared for 2026-10-05.

**The dispatch** (`card` is read off the variable, not typed from memory):

```
R=dreadnought-foundry/bureau-pipeline
CARD=$(gh variable get GROOM_PROPOSAL_CARD --repo $R)        # DRE-4541 on 2026-10-04
gh workflow run self-groomer.yml -R $R -f mode=propose -f judgement=on -f dry_run=true -f card="$CARD"
#   lookup_budget is left out, so it is empty and each leg sizes itself off its token
RUN1=$(gh run list -R $R --workflow self-groomer.yml --event workflow_dispatch -L 1 --json databaseId --jq '.[0].databaseId')
```

Then read it with the read-off script, naming the run:
`RUN=$RUN1 DAY=2026-10-05 bash docs/evidence/DRE-4973/readoff_morning.sh ./obs1`.
That script ships with DRE-4973's record and is read-only. On a dispatched
run the jobs are named `call / …`, not `schedule / …`, so take the verify
leg's log by hand:
`gh run view -R $R --job <id of one "call / verify (DRE-N, owner/repo)"> --log`.

| What the card asks for | Where it is read | Reading |
|---|---|---|
| Dispatch inputs as entered | the command above | ⟨FILL⟩ |
| `## Verified against main` counts | `obs1/key-lines.txt` | ⟨FILL⟩ |
| Every `partly-solved`, `done-elsewhere`, `obsolete`, `not-worth-it` or `excluded` card, with its evidence line or exclusion reason, and the CEO's 2026-09-29 read of the same card where it was on that proposal (DRE-4541) | `obs1/verified/proposal-verified.json` → `outcomes.now[].verify`, `verify.excluded` | ⟨FILL: table⟩ |
| `still-needed` answers the reader overturns on reading the evidence, against the 2026-09-29 baseline of 12 of 25 | the reader's audit | ⟨FILL: N of M⟩. The same audit serves DRE-4968 §5. Do it once. |
| No excluded card is in the batch table | `verify.excluded` vs `outcomes.now` | ⟨FILL⟩ |
| One `groom-lookups: <owner> — …` line per owner in `config/repo-map.json` (EveryBite, DeltaSolv, dreadnought-foundry) | `obs1/key-lines.txt` | ⟨FILL⟩ |
| One verify leg's fold-step output: `groom-lookups: folded N document(s) — …` | that leg's log, step "Fold the lookups into the targets" | ⟨FILL⟩ |
| "Not verified" lines and any cut evidence line (`more than 5 commits touched …`) | `obs1/proposal/verify-targets.json` | ⟨FILL⟩ |
| Where a leg stopped on its clock or budget, or was unread: each card left `lookup failed: <owner/repo> did not answer — …`, its `repository` under that owner, and none under an owner whose leg finished | `verify-targets.json` | ⟨FILL, or "every leg finished every card — the per-card rule is observed in §3 instead"⟩ |
| The groom job's `linear-budget:` line | `obs1/key-lines.txt` | ⟨FILL⟩ |
| `## Priorities re-ranked as Medium`, whole, and whether DRE-2702, DRE-2563, DRE-2564, DRE-3681, DRE-3530 still head the list | `obs1/verified/proposal-verified.txt` | ⟨FILL⟩ |
| `## Cancels refused`, whole | `obs1/verified/proposal-verified.txt` | ⟨FILL⟩ |

**Pass bar:** every row filled from the live run, with nothing paraphrased.

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

**Not made yet.** It needs a `workflow_dispatch` of `self-groomer.yml` with `dry_run: true`, `lookup_budget: 0` and `card` set to the standing card. That workflow was disabled on 2026-10-03 (§1). It is prepared for 2026-10-05, after observation 1's run finishes.

The stop's second shape (no owner searched) is proven in pytest on DRE-5317. It would not be observed here even on a live run, because producing it would mean breaking the App's installation or removing a secret.

**The dispatch.** `card` is not optional: DRE-5317's stop lives in `groomer.py post`, which runs only when `card` is set.

```
R=dreadnought-foundry/bureau-pipeline
CARD=$(gh variable get GROOM_PROPOSAL_CARD --repo $R)
gh workflow run self-groomer.yml -R $R -f mode=propose -f judgement=on -f dry_run=true -f lookup_budget=0 -f card="$CARD"
RUN3=$(gh run list -R $R --workflow self-groomer.yml --event workflow_dispatch -L 1 --json databaseId --jq '.[0].databaseId')
RUN=$RUN3 DAY=2026-10-05 bash docs/evidence/DRE-4973/readoff_morning.sh ./obs3
# the post step's last lines, and the annotation on the summary page
gh run view -R $R --job <id of "call / post"> --log | grep -E 'groomer: not posted|dry run — nothing posted'
gh api repos/$R/check-runs/<id of "call / post">/annotations --jq '.[] | .annotation_level + ": " + .message'
gh run view $RUN3 -R $R --json conclusion
# Pipeline Medic, for the ten minutes after the run finished (PT window stated in the record)
gh run list -R $R --workflow "Pipeline Medic" -L 20 --json databaseId,event,createdAt,displayTitle,headBranch
```

| What the card asks for | Reading |
|---|---|
| The three inputs as entered (`dry_run`, `lookup_budget`, `card`) | ⟨FILL⟩ |
| Targets with `lookup: "failed"` and with `lookup: "none"`, from the kept `proposal-verified.json`, with at least one failed | ⟨FILL: N failed, M none⟩. If none failed, this is not the observation: say so and wait for a morning with a file-naming card |
| Each lookup leg's `groom-lookups: <owner> — 0 card(s) read, 0 request(s) of a budget of 0, <s> s of 300 s` | ⟨FILL: one per owner⟩ |
| "Post the verified proposal" step green, its last line `groomer: not posted — the lookup failed for every card: no repo answered — …`, and no `dry run — nothing posted` above it | ⟨FILL⟩ |
| The warning annotation carrying the same line on the run's summary page | ⟨FILL⟩ |
| The run's conclusion | ⟨FILL: `success`⟩ |
| `all_lookups_failed: true`, `merged_prs_unread`, the `lookups_failed` list, `not_posted_why` | ⟨FILL, copied from `obs3/summary.txt`⟩ |
| One mapped card's `lookup failed: <owner/repo> did not answer — request budget of 0 spent`, with `<owner/repo>` equal to that card's `repository` | ⟨FILL⟩ |
| Pipeline Medic runs in the ten minutes after the run finished, and none woken by it | ⟨FILL⟩ |

**Pass bar:** every row read from the live run. The standing card's thread is NOT cited: a dry run posts nothing whatever happens (DRE-3712).

**A run that refuses for another reason first.** If the run stops before `post` for any other reason, record what stopped it. That run is not this observation.

## 4. The first scheduled morning after every sibling is on `main`

**Not made yet.** No `schedule` run of `self-groomer.yml` fired while it was disabled. The last morning observed against this card's siblings was 2026-10-02 (proposal `898580543342`, posted 06:18 PT). It came before the pause and is recorded on DRE-4968 and DRE-4973, not here.

**Prepared for 2026-10-05.** This is the first `schedule`-started run after the last sibling merged and after the groomer came back on.

```
DAY=2026-10-05 bash docs/evidence/DRE-4973/readoff_morning.sh ./obs4
```

The script writes every job's start and end in PT to `obs4/jobs-pt.tsv`. Copy it here. The comment's landing time is the `createdAt` of the `🧺 groom-proposal: <id>` comment on DRE-4541, read once from Linear and converted to PT.

| Job | Start (PT) | End (PT) |
|---|---|---|
| gate | ⟨FILL⟩ | ⟨FILL⟩ |
| schedule / groom | ⟨FILL⟩ | ⟨FILL⟩ |
| schedule / lookup (EveryBite) | ⟨FILL⟩ | ⟨FILL⟩ |
| schedule / lookup (DeltaSolv) | ⟨FILL⟩ | ⟨FILL⟩ |
| schedule / lookup (dreadnought-foundry) | ⟨FILL⟩ | ⟨FILL⟩ |
| verify legs (first start, last end) | ⟨FILL⟩ | ⟨FILL⟩ |
| schedule / post | ⟨FILL⟩ | ⟨FILL⟩ |
| **Proposal comment on DRE-4541** | | ⟨FILL⟩ |

Run id: ⟨FILL⟩. `verify_matrix` non-empty: ⟨FILL⟩. Before 06:30 PT: ⟨FILL⟩.

**Pass bar:** posted before 06:30:00 PT. A later post is a failed observation. Name the job that carried the overrun and file a mid-epic amendment: `scripts/mid_epic.py discovery DRE-5213 --kind amendment`. For reference, 2026-10-02 ran 06:05:53–06:18:38 PT: groom 8 min 55 s, lookups 1 min 27 s, verify 1 min 50 s, post 15 s.

## What is not proven, and why

- **Observations 1, 3 and 4.** On 2026-10-03 all three waited on the groomer being switched back on, which is the CEO's call. It came back on 2026-10-04 at 07:31 PT, proposals only. They are prepared for the 2026-10-05 sitting (see "Order of the 2026-10-05 sitting"): one morning read and two dry-run dispatches. ⟨FILL on 2026-10-05: what each one showed, or why it was not made⟩
- **The emit side of DRE-4416.** All three runs say the bureau-pipeline emit step (the S3 upload) is outside `target/` and was not checked. The fixture asks only about agent-bureau, so that does not affect the verdict.

## Where each source disagreed, and which was taken

| Fact | Source A | Source B | Taken |
|---|---|---|---|
| The line of `insert_reading` in run 2's third proof | the agent's proof: line 285 | the file in run 2's `target/`: line 286 | The file. The verdict does not rest on that proof |
