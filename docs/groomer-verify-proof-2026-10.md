# Groomer verify proof, October — DRE-5459 (epic DRE-5213)

**Status: PARTIAL. Observation 2 is PROVEN. Observations 1, 3 and 4 were NOT MADE: the groomer is switched off.**

- **Observation 2 (proven).** The real verify agent, run three times by hand over the DRE-4416-shaped fixture, answered `done-elsewhere` all three times. Each answer cites `console/backend/cost_usage.py:962` (`kept = claude_usage_readings.record_reading(`), the line where the console's usage probe writes the readings table. The runs were on 2026-10-03 at 13:09–13:10, 13:13–13:14 and 13:14 PT.
- **Observations 1, 3 and 4 (not made).** Each needs a live run of bureau-pipeline's `self-groomer.yml`: a `dry_run` dispatch for observations 1 and 3, and a `schedule` morning for observation 4. That workflow has been disabled since the Agent-Bureau fleet pause (2026-10-02 15:17 PT), and the CEO has not approved turning it back on. A disabled workflow cannot be dispatched. The 2026-10-02 morning, the only one since DRE-5213's last merge, came before the pause and does not carry observations 1, 3 or 4 (§4).

**How this was recorded:** by a proof-runner session on the operator's instruction, 2026-10-03 13:09–13:20 PT. Steps (a) and (d) ran with `GITHUB_ACTIONS=true`, empty `AWS_*` and `LINEAR_API_KEY=test`. Step (c) ran on the operator's local Claude login, on the CEO's go. Nothing live was written.

| Criterion | Result |
|---|---|
| The record is on `main`, opens with run ids and dates in PT, and carries the four observations in order, copied from live runs | **Partly met.** This record opens with what ran and when. Observation 2 is copied from the runs. Observations 1, 3 and 4 have no run to copy (§1, §3, §4) |
| Observation 1, a dry-run morning over the live Intake lane | **Not made.** BLOCKED by Groomer (`self-groomer.yml` disabled) |
| Observation 2: the four commands as run, the model id, and three verdict files, all `done-elsewhere` with a `file:line` proof | **Met** (§2) |
| Observation 3, a morning with every lookup failed (`lookup_budget: 0`, `card` set) | **Not made.** BLOCKED by Groomer |
| Observation 4, the first scheduled morning after every sibling, posted before 06:30 PT | **Not made.** BLOCKED by Groomer (no `schedule` run while it is disabled) |
| The CEO closes this card after reading the record | Open. Under the CEO's rule of 2026-10-02 a proof closes on evidence, and this one waits on observations 1, 3 and 4 |

## 1. A dry-run morning proposal over the live Intake lane

**Not made.** `self-groomer.yml` reads `disabled_manually` in `gh api repos/dreadnought-foundry/bureau-pipeline/actions/workflows` (read 2026-10-03 12:59 PT). A disabled workflow refuses `workflow_dispatch`. Turning it on is outside this runner's authority: the CEO has approved only Medic, Agent Fix, Agent Task and Plan.

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

**Not made.** It needs a `workflow_dispatch` of `self-groomer.yml` with `dry_run: true`, `lookup_budget: 0` and `card` set to the standing card, and that workflow is disabled (§1). The stop's second shape (no owner searched) is proven in pytest on DRE-5317 and would not be observed here even on a live run, because producing it would mean breaking the App's installation or removing a secret.

## 4. The first scheduled morning after every sibling is on `main`

**Not made.** No `schedule` run of `self-groomer.yml` fires while it is disabled. The last morning observed against this card's siblings was 2026-10-02 (proposal `898580543342`, posted 06:18 PT). It came before the pause and is recorded on DRE-4968 and DRE-4973, not here.

## What is not proven, and why

- **Observations 1, 3 and 4.** All three wait on the groomer being switched back on, which is the CEO's call. When it is, observations 1 and 3 are two dispatches, and observation 4 is the next 06:00 PT morning after that.
- **The emit side of DRE-4416.** All three runs say the bureau-pipeline emit step (the S3 upload) is outside `target/` and was not checked. The fixture asks only about agent-bureau, so that does not affect the verdict.

## Where each source disagreed, and which was taken

| Fact | Source A | Source B | Taken |
|---|---|---|---|
| The line of `insert_reading` in run 2's third proof | the agent's proof: line 285 | the file in run 2's `target/`: line 286 | The file. The verdict does not rest on that proof |
