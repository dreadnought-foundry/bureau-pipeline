# Where a model choice takes effect, and each sibling's one step back (DRE-7195)

**Regime read at `main` at `afa91e9f` (afa91e9f69e0bb086a538fcf01ce9bc077b572ee), on 2026-10-12 at 10:42 PM PT.**
`stable` pointed at `b2a6e726` at that time, 9 commits behind `main`.
Every time in this record is Pacific.

**The answer, in one line:** today every selecting run says `model-home: unreadable (BUREAU_MODEL_HOME_TOKEN is set and empty, so the token mint failed), running on the defaults in the code`.
So the choice in force is the generated literal in `scripts/model_fallback.py`, rendered from `config/models.yaml`.
The home, `BUREAU_MODEL_CHOICES`, is consulted on every run and is never read.
A change reaches a run when a pull request changes `config/models.yaml` and its generated mirrors, and the run's pipeline ref carries that merge.

This record belongs to DRE-7184, the first child of the model-cost epic.
`tests/test_model_choice_levers.py` pulls every lever it names, against the real selector and a temporary copy of the real config.

## The three places a choice can come from

1. **The model home.** This is the GitHub Actions repository variable `BUREAU_MODEL_CHOICES` on dreadnought-foundry/bureau-pipeline.
   It is read by `read` (`scripts/model_home.py:468`) through `use_home` (`scripts/model_fallback.py:1429`).
   A run asks for it when `BUREAU_MODEL_HOME_TOKEN` or `BUREAU_MODEL_HOME_FILE` is in its environment (`_source`, `scripts/model_home.py:420`).
   Every selecting workflow mints that token in a `Mint the model-home read token` step.
2. **The defaults in the code.** This is the generated literal `_FALLBACK_MODEL_CONFIG`, between the `BEGIN generated model config` and `END generated model config` markers in `scripts/model_fallback.py` (lines 295 to 371).
   It is read only through `code_defaults` (`scripts/model_fallback.py:374`).
   `scripts/sync_model_config.py` renders it from `config/models.yaml`, and `--check` fails CI when the two drift.
3. **The choices written in `config/models.yaml`.** These are `kinds`, `ladders`, `agents`, `ungrouped` and `projects`.
   They are never read on the dispatch path.
   `_load_config` (`scripts/model_fallback.py:1238`) takes only the code-side facts from that file — `effort`, `review_separation`, `discovery`, `retired`, `excluded`, `session_agents`, `about` and `rules` — and takes every choice from the literal (`_CHOICE_KEYS`, line 385).
   The file reaches a run only through the literal it is rendered into.

**The order.** `select_with_reasons` (`scripts/model_fallback.py:1995`) calls `use_home` first (line 2054), once per process.
- If the reader answers `read`, the home's choices replace the defaults in place (lines 1453 to 1454). The literal is not consulted again in that process.
- If the reader answers `not set` or `unreadable`, the choices stay the defaults in the code: the literal, through `code_defaults`.
- Nothing sits between those two. `config/models.yaml`'s choices are never a third rung.

`ladder_for` (`scripts/model_fallback.py:1585`) and `_project_exception` (`scripts/model_fallback.py:1568`) then read whichever choices are in place.
`selection_note` (`scripts/model_fallback.py:2116`) ends the note with the home's clause (`status_clause`, `scripts/model_home.py:508`).

**`config/model-choices.json` is read by the console's Settings › Agents & models page only, never on the dispatch path.**
It is the generator's third target, a whole generated file.
No selecting script opens it.
The test garbles it, then deletes it, in its copy, and the selection note does not change by one character.

## How to tell which regime a run is on

Every selecting run prints one line tagged `model-home:` to stderr, just above its selection note.
The note ends with the same clause:

| The run's line | What it means |
| -- | -- |
| `model-home: read — written <time> by <writer>` | The home is in force. The note ends `· model-home: read`. The code defaults are not consulted, and a change to `config/models.yaml` moves nothing until it is written into the home. |
| `model-home: unreadable — <reason>; running on the defaults in the code` | The generated literal is in force. The note opens `DEGRADED` and ends `· model-home: unreadable (<reason>), running on the defaults in the code`. |
| `model-home: not set — running on the defaults in the code` | Nobody asked for the home, so the literal is in force. The note carries no home clause. No selecting workflow runs this way today, because each one mints a token. |

## What the selecting runs are on today

All five runs below are on dreadnought-foundry/bureau-pipeline.
All of them say the home was unreadable, so **their choices came from the defaults in the code: the generated literal at the commit each run checked out.**

**QA Review** — run 38349343788, https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38349343788.
Created 10:01:53 PM PT on 2026-10-12, reviewing the PR from branch `agent/DRE-6743-sweep-queue-lane`.
Its review job logged this at 10:02:15 PM PT:
`model-home: unreadable — BUREAU_MODEL_HOME_TOKEN is set and empty, so the token mint failed; running on the defaults in the code`
and then
`DEGRADED model-policy: claude-sonnet-5-5 chosen for critic (advisory kind) — build/review separation: this build ran on claude-opus-5-5, so the reviewer does not — top of the ladder, nothing above it was skipped — project bureau-pipeline: no exception, fleet ladder · model-home: unreadable (BUREAU_MODEL_HOME_TOKEN is set and empty, so the token mint failed), running on the defaults in the code`.
A second run, 38351822371 (https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38351822371), logged the same two lines at 10:31:52 PM PT.

**Agent Task** — run 38343864483, https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38343864483.
Card DRE-6971, created 8:54:00 PM PT on `main` at `b2a6e726`.
Its Select model step logged this at 8:54:29 PM PT:
`model-home: unreadable — BUREAU_MODEL_HOME_TOKEN is set and empty, so the token mint failed; running on the defaults in the code`
and then
`DEGRADED model-policy: claude-opus-5-5 chosen for devops (workhorse kind) — top of the ladder, nothing above it was skipped — project bureau-pipeline: no exception, fleet ladder · model-home: unreadable (BUREAU_MODEL_HOME_TOKEN is set and empty, so the token mint failed), running on the defaults in the code`.

**Agent Plan** — run 38352540950, https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38352540950.
Card DRE-7184, on `main` at `afa91e9f`.
It logged this at 10:40:12 PM PT:
`model-home: unreadable — BUREAU_MODEL_HOME_TOKEN is set and empty, so the token mint failed; running on the defaults in the code`
and then
`DEGRADED model-policy: claude-opus-5-5 chosen for planner (judgement kind) — top of the ladder, nothing above it was skipped — project bureau-pipeline: no exception, fleet ladder · model-home: unreadable (…), running on the defaults in the code`.
Run 38352540226 (card DRE-7174) logged the same lines at 10:40:10 PM PT.

**Why the token is empty.** In run 38349343788, the `Mint the model-home read token` step logged two lines at 10:02:14 PM PT:
- a warning that `actions/create-github-app-token` v3.2.0 lists no `permission-variables` input;
- then the refusal: `Failed to create token for "dreadnought-foundry/bureau-pipeline" (attempt 1): The permissions requested are not granted to this installation.`

The step is `continue-on-error`, so the Select model step gets an empty token.
The reader stops before any request goes out (`scripts/model_home.py:438`).
**No run reaches the variables API today.**

Only bureau-pipeline's runs could be read for this record, because this run's token reads no other repository.
A product repository's runs mint the same token with the same App, so the same refusal is expected there.
Treat that as unverified until one of its `model-home:` lines is quoted.

## The 2026-10-12 sandbox reading, explained

Three readings disagreed on 2026-10-12. Here is how they fit together:

- **The exception was real, and written in the file.** The console's save opened bureau-pipeline #993.
  It merged at 12:34:58 AM PT on 2026-10-11 (`f49d6dce`), adding `projects: agent-bureau-demo: workhorse: claude-sonnet-5-5` to `config/models.yaml` and its generated mirrors, the literal included.
- **The sandbox build read it from that file.** Agent Task run 38269194453 on agent-bureau-demo was created at 8:17:52 AM PT and selected at 8:18:33 AM PT.
  It ran `agent-task.yml@refs/tags/stable` at `12ac7585`. Its critic, QA Review run 38269419206, selected at 8:20:27 AM PT.
  The readings are in `docs/proof-dre-6623-project-model-exceptions.md`.
  At `12ac7585`, `scripts/model_home.py` did not exist.
  `_load_config` read every choice straight from `config/models.yaml`, with the literal only as its degrade path, and both carried #993's exception.
  That is why the run's note reads `project agent-bureau-demo: exception claude-sonnet-5-5 leads the workhorse walk` with no `model-home` clause.
  No home existed for it to be in force.
- **The home reader landed later that day.** #1062 (DRE-6939) merged at 10:41 AM PT and moved every choice to the home-or-literal order above.
  #1066 merged at 12:18 PM PT and took the sandbox's exception back off the file and the literal.
  From then on, `config/models.yaml` reads `projects: {}`.
  #1070 (DRE-6940) merged at 2:30 PM PT and gave every selecting step its token mint. Since then the runs say `unreadable` rather than nothing.
- **What "not found" from the variables API can and cannot tell apart.** `GET /repos/dreadnought-foundry/bureau-pipeline/actions/variables/BUREAU_MODEL_CHOICES` answers `404 Not Found` both when the variable does not exist and when the token has no `variables: read`.
  GitHub hides a resource a token cannot see behind the same 404.
  At 10:42 PM PT this run's token, which holds no `variables` permission, got the identical `{"message":"Not Found",…,"status":"404"}` for `BUREAU_MODEL_CHOICES` and for a name nobody created.
  So a 404 shows only that this token cannot see the variable. It does not show whether the variable exists.
  Only a token known to hold `variables: read` can settle that. If listing `GET …/actions/variables` with it answers `200` and the list lacks the name, the variable does not exist.
  The reader reports either case the same way: `unreadable — the GitHub API answered HTTP 404 for BUREAU_MODEL_CHOICES (…)` (`_fetch`, `scripts/model_home.py:389`).

## When a change to `config/models.yaml` reaches the fleet

While the defaults are in force, a change to `config/models.yaml` and its regenerated literal is in force for a run once the run's pipeline ref carries the merge:

- **Callers on `@main`** have it on the merge itself. bureau-pipeline's own `self-*` stubs and the harness sandbox's stubs are on `@main`.
- **Callers pinned to `stable`** have it when Promote Channel moves `stable` past the merge.
  agent-bureau and agent-bureau-demo ran `@refs/tags/stable` on 2026-10-11 and 2026-10-12, per the DRE-6638 record.
  At 10:42 PM PT, `stable` was 9 commits behind `main`.
- **Callers pinned to a `vN` tag** have it when the operator cuts the next `vN` past the merge.

## What would change the answer

The answer above holds until a run's line reads `model-home: read`.
That happens when the bureau App's installation grants `variables: read` **and** the variable holds a document.
The refusal above shows the request for `variables: read` reached GitHub and was turned down, despite the action's warning.
The document can come from:
- the operator's seeding (`python3 scripts/model_home.py render-defaults`, DRE-6945);
- the daily adoption;
- the console, once DRE-6976 ships it;
- `model-home-carry.yml`, which has no run on record as of this reading.

From that run on:

- **The home wins over the defaults in the code.** The literal is not consulted, and neither is `config/models.yaml`.
- **Reverting `config/models.yaml` no longer moves a run by itself.** It moves a run only when the carry bridge carries it into the home (`scripts/model_home_carry.py`, until DRE-6981 removes it).
  The bridge carries only these changes: a group's first model, an agent's group, an added group with its ladder, and a project's model set or cleared.
  A rung changed below the first, or a group removed, is not carried.
- **Each sibling row's lever below changes from `defaults in the code` to `the home`.** Its one step back becomes writing the earlier value back to the home, through the writer `main` carries that day: the console's undo once DRE-6976 ships it, or otherwise the carry of a revert, or an operator document passing `python3 scripts/model_home.py check`.
  The test already pulls that lever (`the home` in its `EXERCISED` table), so changing a row's lever needs no new test.
- **The `model-attempt line` rows do not change.** That lever is a workflow line, not a model choice.

Two epics in progress move these levers further.
DRE-6936's later parts remove the generated literal and the choices held in `config/models.yaml`. After that, no row may name a lever in a file that no longer holds a choice.
DRE-6853 forbids tests that pin a choice the console can change. The test takes every id from the copy it builds, so that guard passes over it.

## The sibling epics' one step back

Each row says:
- what the epic switches;
- the lever its rollback pulls;
- the role the test pulls it on;
- the state switched and the state rolled back;
- the exact step;
- how long until the step is in force;
- the clause the run's selection note carries once it is.

The test reads this table, pulls each row's lever before and after on each named role, and checks that the clause is absent before and present after.
A row naming a lever the test does not pull fails it.

| Epic | What it switches | Lever | Role | Switched → back | One step back | In force | Rolled back, the note shows |
| -- | -- | -- | -- | -- | -- | -- | -- |
| `DRE-7185` | Records the builder, so an unknown-builder review lands on Sonnet 5.5 | `model-attempt line` | `critic`, `verifier` | `posted` → `not posted` | Revert the pull request that makes the fix agent and red-main repair post the `🧠 model-attempt:` line (`agent-fix.yml`, `red-main-repair.yml` and their `self-` stubs) | `@main` callers on the revert's merge; `stable` callers when `stable` moves past it; `vN` callers at the next cut | `this build's model could not be read` |
| `DRE-7187` | Moves the harness reviewer to Sonnet 5.5 | `model-attempt line` | `critic` | `posted` → `not posted` | Revert the pull request that makes the harness's probe fixtures post the `🧠 model-attempt:` line | On the revert's merge — the harness sandbox's stubs ride `@main` | `this build's model could not be read` |
| `DRE-7188` | Moves proof runs to their own group, on Sonnet 5.5 | `defaults in the code` | `proof` | `proof` → `workhorse` | A pull request setting `agents.proof` back to `workhorse` in `config/models.yaml`, with `python3 scripts/sync_model_config.py` regenerating its mirrors (`scripts/model_fallback.py`, `agents.yaml`, `config/model-choices.json`) — the revert of the change that moved it | bureau-pipeline's proof runs (`self-proof-task.yml@main`) on the merge; a product repo's proof runs when `stable` moves past it; `vN` callers at the next cut | `chosen for proof (workhorse kind)` |
| `DRE-7189` | Routes the console's jobs through the selector, on Sonnet 5.5 | `defaults in the code` | `console-weekly-report` | `advisory` → `workhorse` | A pull request putting the console's roles back in `workhorse` in `config/models.yaml`, with its mirrors regenerated — the revert of the change that put them in the Reviewers | When agent-bureau's console runs the reverted literal — see the row's note below | `chosen for console-weekly-report (workhorse kind)` |

**DRE-7185.** Once rolled back, a review of a fix or a red-main repair reads no build model from the card, so it runs `--built-on-unknown`.
Today's walk then ends on Opus 5.5. The test's run in today's regime shows this note:
`DEGRADED model-policy: claude-opus-5-5 chosen for critic (advisory kind) — build/review separation: this build's model could not be read, so the reviewer skips every model that also builds (claude-sonnet-5-5, claude-sonnet-5); claude-opus-5-5, the walk's last rung, also builds for the fleet and reviews this one because nothing else was left — top of the ladder, nothing above it was skipped — project none: fleet ladder · model-home: unreadable (BUREAU_MODEL_HOME_TOKEN is set and empty, so the token mint failed), running on the defaults in the code`.
While switched, the note reads `build/review separation: this build ran on claude-opus-5-5, so the reviewer does not` on `claude-sonnet-5-5`.
The hand-built launcher's half lives in agent-bureau and is reverted there on its own card.
The rule that separates build and review is unchanged either way.

**DRE-7187.** This row has the same lever and the same note as DRE-7185, on the probe pull requests the harness opens in its sandbox.
The harness drives the sandbox's stubs at `@main`, so the next harness run after the merge is on Opus again.

**DRE-7188.** Rolled back, today's note reads:
`DEGRADED model-policy: claude-opus-5-5 chosen for proof (workhorse kind) — top of the ladder, nothing above it was skipped — project none: fleet ladder · model-home: unreadable (…), running on the defaults in the code`.
While switched, it reads `chosen for proof (proof kind)` on `claude-sonnet-5-5`.
The Proof group can stay in the file. Only `agents.proof` moves.

**DRE-7189.** The console's jobs are not in the selector today: they are pinned to Opus in agent-bureau's console.
`console-weekly-report` stands in for the role names DRE-7189 will register. Every such name needs an `agents` entry in `config/models.yaml` and a registry entry in `agents.yaml`.
Rolled back, the note reads `chosen for console-weekly-report (workhorse kind)` on Opus 5.5, the Builders' first rung, which is the model the jobs are pinned to today.
**The delay is the one thing this record could not read.** The console calls a vendored copy of `scripts/model_fallback.py`, so it runs on that copy's literal, or on the home if the console reads it.
This run's token could not read agent-bureau, so the step is in force when the console's copy carries the reverted literal and the console is deployed, which is an operator step.
DRE-7189 must quote the console's own `model-home:` line, or its equivalent, to confirm which source its jobs read before it names this rollback as exercised.
Reverting the agent-bureau change that routes the jobs through the selector restores the hard-coded Opus pin. That is the coarser undo, and it leaves no selection note at all.
