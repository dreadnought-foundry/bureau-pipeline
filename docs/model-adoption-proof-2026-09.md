# Model adoption proof — DRE-3904 (epic DRE-3892)

**Status: the dry run matches the CEO's three rules on every model the live catalog lists. No adoption ran, because no same-family candidate exists today.** A hand-dispatched dry run of `Model adoption` on 2026-09-30 read 13 models from the live Anthropic catalog. The five it had never been configured with all read `ignore`, which is correct for each. None read `ask` or `adopt`. The dry run created no card. The trial-to-merged-PR half of this proof is therefore the dry run's trace. The first real adoption is owed as a follow-up observation in this file (§6).

Observed on 2026-09-30 between 14:06 and 14:13 PT by an operator session on the operator's instruction. Every time below is Pacific Time.

| Criterion | Result |
|---|---|
| A dry run was observed by hand against the live catalog, with its classification table copied here, every row annotated, and the run URL | **Met** (§1, §2) |
| The board was checked by hand after the dry run, no card was created, and the time of the check is stated | **Met**: checked 14:12 PT (§3) |
| A live adoption was observed end to end, or the record states that no same-family candidate existed and names the follow-up owed | **Met, second branch**: no candidate on 2026-09-30. The follow-up is in §6 |
| The record ends with `What the CEO sees`, free of paths, commands and verdict markers | **Met** (§7) |
| Merged to `main` through an operator-opened PR | Open: this PR |
| The CEO has read the merged record and closed the card himself | Open: the CEO's step |

## 0. What ran, and on which code

- Every build card under DRE-3892 is Done: DRE-3895, 3896, 3897, 3898, 3899, 3900, 3903 and DRE-5121. DRE-3898 (the workflow) closed 2026-09-29 20:26 PT, and DRE-3900 closed 2026-09-29 22:06 PT.
- Dispatched with `gh workflow run model-adoption.yml -R dreadnought-foundry/bureau-pipeline -f dry_run=true` at 14:06:27 PT.
- Run: [36777241452](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36777241452), event `workflow_dispatch`, on `main` at `881bc1e0721d257346c067a326dcad884b2fde93`. The workflow lives in this repo and runs from `main`.
- `classify` ran 14:06:31–14:06:39 PT and succeeded. Its log shows `DRY_RUN: true` in every step's environment. Every downstream job (`trial`, `pin-raise`, `pin-trial`, `adopt`, `pin-pr`, `trial-failed`) reads **skipped**, which is what a run with no `adopt` decision does.
- `Fetch the live catalog once` printed `wrote /home/runner/work/_temp/catalog.json (13 models)` at 14:06:36 PT.
- The run uploaded no artifact.

## 1. The classification table, as the run printed it

This is the step summary, verbatim from the `Summarize every candidate and pick what to act on` step:

| Candidate | Rule | Reason |
|---|---|---|
| `claude-opus-4-7` | ignore | claude-opus-4-7 is not newer than claude-opus-5-5, the newest opus rung we run (2026-04-14T00:00:00Z against 2026-09-21T16:24:00Z) |
| `claude-opus-4-6` | ignore | claude-opus-4-6 is not newer than claude-opus-5-5, the newest opus rung we run (2026-02-04T00:00:00Z against 2026-09-21T16:24:00Z) |
| `claude-opus-4-5-20251101` | ignore | claude-opus-4-5-20251101 is not newer than claude-opus-5-5, the newest opus rung we run (2025-11-24T00:00:00Z against 2026-09-21T16:24:00Z) |
| `claude-haiku-4-5-20251001` | ignore | claude-haiku-4-5-20251001 is a haiku model, a line no ladder runs, and it is older than every rung on every ladder (2025-10-15T00:00:00Z against the oldest, claude-sonnet-4-6 at 2026-02-17T00:00:00Z) |
| `claude-sonnet-4-5-20250929` | ignore | claude-sonnet-4-5-20250929 is not newer than claude-sonnet-5-5, the newest sonnet rung we run (2025-09-29T00:00:00Z against 2026-09-28T00:00:00Z) |

**The run prints 5 rows, not 13, by design.** `classify_catalog` drops every id the pipeline already runs, retires or excludes before it classifies. So the other 8 catalog ids never reach the table. The workflow does not print them, and the run left no artifact that lists them.

**Where the 8 missing ids come from.** They are taken from the most recent committed catalog snapshot, `models.json`. The model-drift run of 2026-09-27 23:58 PT ([36389164786](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36389164786)) refreshed it, and DRE-5116 (PR #548) added `claude-sonnet-5-5` to it. It holds 13 ids. Running the same `classify_catalog` over it, with the same `config/models.yaml` at `881bc1e`, gives the same 5 candidates with the same 5 rules. Four reasons are word for word the same. The fifth, `claude-sonnet-4-5-20250929`, differs: locally it names `claude-sonnet-5` (2026-06-29) as "the newest sonnet rung we run", not `claude-sonnet-5-5`. That is because the snapshot gives `claude-sonnet-5-5` no date, and an undated rung ranks oldest. The rule is `ignore` either way. The 8 it drops are:

| Catalog id | Why it is not a candidate |
|---|---|
| `claude-opus-5-5` | on the workhorse, advisory and judgement ladders |
| `claude-sonnet-5-5` | on the workhorse and advisory ladders |
| `claude-opus-5` | on the workhorse ladder |
| `claude-sonnet-5` | on the workhorse and advisory ladders |
| `claude-fable-5-1` | on the judgement ladder |
| `claude-sonnet-4-6` | on the judgement ladder |
| `claude-opus-4-8` | `retired` ("rotated out when Opus 5 landed") |
| `claude-fable-5` | `excluded` ("off every ladder on cost policy (2026-08-12)") |

**This is an inference about the live catalog, not a reading of it.** The live run confirms the count (13) and the 5 candidates. It does not print the other 8 names. That same fifth reason shows the run read the live catalog, not the snapshot. The live run's reason dates `claude-sonnet-5-5` at `2026-09-28T00:00:00Z`, but the committed snapshot has no date for that id. So the snapshot's 13 and the live 13 are not byte-identical. They agree on which ids are candidates and on every rule. The scheduled run at 00:38 PT the same day ([36684872808](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36684872808)) also read 13 models and printed the same five rows.

## 2. Each row against the CEO's three rules

The rules, as the card states them: Opus 4.5–4.7, Sonnet 4.5 and Haiku 4.5 must read `ignore`. Nothing we already run may appear. Any `ask` must be a new family, or a same-family version that is pricier or has no declared price.

| Row | Rule printed | Required | Matches |
|---|---|---|---|
| `claude-opus-4-7` | ignore | ignore (Opus 4.5–4.7) | **yes** |
| `claude-opus-4-6` | ignore | ignore (Opus 4.5–4.7) | **yes** |
| `claude-opus-4-5-20251101` | ignore | ignore (Opus 4.5–4.7) | **yes** |
| `claude-haiku-4-5-20251001` | ignore | ignore (Haiku 4.5) | **yes** |
| `claude-sonnet-4-5-20250929` | ignore | ignore (Sonnet 4.5) | **yes** |
| any model we run | absent | must not appear | **yes**: none of the 6 ladder ids, nor the retired or excluded id, is in the table |
| any `ask` | none | only a new family, or a pricier or unpriced version | **yes**: vacuously, since there is no `ask` |
| any `adopt` | none | only a newer same-family version at or below the rung's price | **yes**: no catalog model is newer than its family's newest rung |

All eight rows match. These five are the class DRE-3881 filed cards for. Today they produce a summary line and nothing else.

## 3. The board after the dry run

Checked by hand at **14:12 PT**. The check was a Linear query for every issue created since 14:06 PT (21:06 UTC), the moment of dispatch, plus title searches for `Spending decision` and `adopt` over two days.

- Created since 14:06 PT: two cards, DRE-5373 and DRE-5374, both at 14:09 PT. Both are portico cards filed by a planner run ("a rebuilt version reads as a rebuild…" and "the five documents that still say the train refuses interactive documents…"). Neither is a model-adoption record, question or trial-failure card.
- No `Spending decision:` card and no adoption record card was created in that window.

**The dry run created no card.**

## 4. The trial, the record card and the pull request

**None ran, because nothing classified `adopt`.** The `trial` job is skipped, and so are `adopt` and `pin-pr`. No same-family candidate exists on 2026-09-30:

- `claude-opus-5-5` (2026-09-21) is the newest Opus and is on three ladders.
- `claude-sonnet-5-5` (2026-09-28) is the newest Sonnet and is on two ladders.
- `claude-fable-5-1` (2026-08-28) is the newest Fable and is on the judgement ladder.
- Haiku has no rung, and Haiku 4.5 is older than every rung.

**The closest thing to an adoption trail is the hand adoption the workflow now automates.** DRE-5116 adopted Sonnet 5.5 by hand on 2026-09-28, under the same rule:

- The trial ran on the then-pinned Claude Code as run [36479502126](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36479502126), from 13:28 PT, and passed (`claude-sonnet-5-5: passed — completed, 3 turns, 7s`).
- The ladder change is PR #548. `agent-bureau-bot` authored it, and `agent-bureau-qa-bot` merged it at 14:30 PT after the critic and the merge gate.

That trail is DRE-5116's, not this workflow's. It is cited to show the shape an automated adoption takes. It is not an observation of one.

## 5. What went wrong on the way

The card predicted one surprise: hardcoded model ids in the model tests going red on a rung change. It happened, one layer over. DRE-3903 and DRE-5116 merged five minutes apart on 2026-09-28. Three `tests/test_model_adoption_actions.py::TestApply` tests then went red on `main` against the new Sonnet 5.5 ladders. DRE-5138 (PR #552, merged 17:06 PT) fixed them. The fix loop did not catch this, because neither PR was red on its own branch; only their combination on `main` was.

Nothing went wrong in the dry run itself.

## 6. Follow-up owed

**The first real adoption, observed end to end and appended to this file.** When the daily run (00:17 PT) first classifies a catalog model `adopt`, the operator records these steps, each with its time in PT:

- the run URL;
- the trial run URL and summary;
- the record card id;
- the adoption PR number;
- the critic's verdict time;
- the merge time;
- `stable` advancing to the merge.

If the trial comes back `degraded`, the pin-raise path runs first (DRE-5121), and its PR and trial belong here too. Until then, the dry run above stands as the proof.

## 7. What the CEO sees

**The Monday table, in plain words.** Anthropic lists 13 models today. Eight of them we already run, have retired, or have ruled out on cost, so the check passes over them without a word. The other five are older versions: Opus 4.5, 4.6 and 4.7, Sonnet 4.5 and Haiku 4.5. Each landed on "ignore", because we already run something newer in the same line (or, for Haiku, because it is older than anything we run at all). Before this change, models like these five each opened a card asking you what to do. Now each gets one line in a summary that nobody has to read, and no card.

**The one real agent run on a candidate model.** None ran today, because nothing new has appeared. When a newer version of a model we run does appear, the check first has it do one small, real piece of agent work. "Passed" means the model finished that work cleanly on the tools our agents actually use, at full strength. If it ran, but could read less or write less at a time than Anthropic advertises, that counts as not ready, and our tools are upgraded first. The last time this happened, by hand on September 28, Sonnet 5.5 passed in three turns and seven seconds.

**The pull request an adoption opens.** A passed trial opens an ordinary change that moves the new model onto our list. The same independent reviewer that reviews all agent work reads it, and the same automatic gate merges it once the tests are green and the reviewer approves. Nobody asks you. The Sonnet 5.5 change on September 28 went through exactly those hands and merged the same afternoon.

**One spending question, as it would reach Green Light.** No model needs a question today, so this is the **fixture**: a made-up new model line called Corvid, run through the real rule and the real wording. Title and body, verbatim:

> Spending decision: claude-corvid-1 — new model family
>
> Anthropic now offers Claude Corvid 1 (claude-corvid-1), released 2026-11-10, at $3.00 per million input tokens and $15.00 per million output tokens. The nearest model we run is claude-sonnet-5-5, at $2.00 per million input tokens and $10.00 per million output tokens. It would join our advisory ladder (the models our reviewers run on). It is a new line of models, not a newer version of one we run. Should we add it? Nothing is adopted until you answer.
>
> What each answer does:
>
> - Yes: the model is added through an ordinary reviewed change, and our agents start using it once that change is merged.
> - No: nothing changes, and we keep running what we run today.
>
> Why this came to you: Our standing rule adopts newer versions of models we already run. A new line of models is a different product, so choosing it is a decision rather than an upgrade.

**What you still decide:** whether to take on a new line of models, or a newer version that costs more than the one it would replace.

**What you no longer do:** approve a newer version of a model we already run at the same price or less. That now happens by itself, tested and reviewed, and you see it afterward.

---

How the fixture was produced (outside the CEO's section): the new-family Decision in `tests/test_model_adoption_actions.py` (`ask_new_family`). It was rebuilt by running `model_adoption.classify_catalog` at `881bc1e` over the committed snapshot plus `claude-corvid-1` at a declared $3/$15. The title and body were rendered with `python3 scripts/model_adoption_actions.py render question-title --decision <file>` and `render question-body --decision <file>` from the same commit, in a scratch copy. Nothing was written to Linear or GitHub.
