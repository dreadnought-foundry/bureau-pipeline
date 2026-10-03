# What's New proof — DRE-5633 (epic DRE-5484)

**Status: PARTIAL. The merge gate's half of observation 1 is proven live: a pull request with no `What's new:` line was held, and the same pull request merged once the line was back. Observations 2 and 3 were not taken, and the fix agent's part of observation 1 could not be observed because the fix agent is switched off.**

- **Held:** agent-bureau-demo #29 had an approving review, green CI, was not a draft, and was opened after the cutover. The gate still declined it, for the missing line alone, at 11:37:07 PT.
- **Merged:** the line went back into the body, followed by one empty commit. The gate merged it at 11:38:29 PT.
- **Not observed:** the critic sending back a lineless pull request, the fix agent answering it, the critic sending back a sentence with a card number, and the release train publishing `whats-new.json`.

Every time below is Pacific Time on 2026-10-03. This record was written by an operator session from the live records at the time of each step. The fleet was shut down, and the CEO asked for the What's New cards to be built and proven by hand.

| Criterion | Result |
|---|---|
| Observation 1: a real pull request opened after the cutover is held by the gate, the critic names the missing line, the fix agent adds `What's new: none` and one empty commit, the critic approves, and the gate merges, with no hand edit | **Partly met.** The gate's hold and the merge after the line came back are observed (§2). The critic was not asked to judge a lineless body: the fixture had an approval from when it carried the line, because the gate only checks the line once a review says merge (§1). The body edit and the empty commit were made **by hand**, because the fix agent is disabled in agent-bureau-demo. "No hand edit" is therefore **not observed** |
| Observation 2: the critic sends back a sentence with a `DRE-` number, and the rewritten sentence is approved | **Not observed.** Not attempted in this session |
| Observation 3, the file half: a real train lap on agent-bureau or portico publishes `whats-new.json` with exactly two items | **Not observed.** No train lap was run in this session |
| Observation 3, the Linear half | **Not observed** |
| The record names the repository, the pipeline ref, the grants (DRE-5579, DRE-5534), that the other roster repositories carry no train, and the dates and times | **Partly met.** The repository, the ref and the times are here. The grants and the train repositories belong to observation 3, which was not taken |
| `docs/whats-new-proof-2026-10.md` is merged to `main` through a pull request | This file, on `agent/DRE-5633-whats-new-proof` |
| The CEO closes this card after reading the record | Open: the CEO's step |

## 0. The switch, and the code the observation ran on

- **The cutover.** `config/whats-new-cutover.json` reads `"enforced_from": "2026-10-03T17:45:58Z"`, which is 10:45:58 PT. It was taken with `date -u` when the file was written (DRE-5576).
- **The merge.** bureau-pipeline PR [#693](https://github.com/dreadnought-foundry/bureau-pipeline/pull/693) merged at **11:25:24 PT** as `690b407`. It was merged by `agent-bureau-qa-bot` after the critic's APPROVE at 10:52:11 PT.
- **The harness.** The Integration Harness ran on `690b407` as run [37144133746](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37144133746) and succeeded at 11:33:47 PT. That run's `agent/harness-*` pull requests carry `What's new: none`, a change made in the same PR #693. Without it the harness would have been held under the new rule and `stable` would have stopped moving.
- **The channel.** Promote Channel run [37144663677](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37144663677) moved `stable` to `690b407` and finished at **11:34:04 PT**. `git ls-remote origin refs/tags/stable` then read `690b407`.
- **The repository observed.** agent-bureau-demo, the sandbox. Its merge-gate and qa-review stubs ride `@stable` with `pipeline_ref: stable`. Both gate runs below log `HEAD is now at 690b407 Merge pull request #693`.

## 1. Why the fixture opened with the line, as a draft

The gate checks the What's new line (condition W) after every other condition, including the critic's verdict. A lineless pull request that the critic sends back is therefore held for the verdict, and the note never names the line. The only way to see condition W itself is to give it a pull request that has an approval but no line. That is the case the condition exists for: an approval carried across a head change, or a review that was skipped.

So the fixture was:

1. opened as a draft, with `What's new: none` in its body, so that the critic would review and approve it and the draft flag would stop the gate from merging;
2. then stripped of the line, marked ready, and handed to the gate by its own `workflow_dispatch` input.

## 2. Observation 1, the gate half

**The pull request.** [agent-bureau-demo #29](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/29) is on branch `agent/DRE-5633-whats-new-gate-proof`. Its diff is one markdown note, `proof-fixtures/DRE-5633.md`.

- It was created at **11:34:24 PT**, `createdAt` `2026-10-03T18:34:24Z`. That is 48 min 26 s after the cutover instant `2026-10-03T17:45:58Z`.
- Its first head was `3b42049`.

| Time (PT) | Step | Record |
|---|---|---|
| 11:34:24 | Opened as a draft, body opening with `What's new: none` | PR #29 |
| 11:36:02 | The critic approved `3b42049` | [verdict](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/29#issuecomment-5972235501), run [37144705404](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37144705404) |
| 11:36:22 | The gate held it for the draft flag: `⏸️ Merge gate: waiting for human merge — the pull request is still a draft …` | [note](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/29#issuecomment-5972238002), run [37144830811](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37144830811) |
| 11:36:52 | The line was removed from the body, the pull request was marked ready, and the gate was dispatched for #29 | — |
| 11:37:07 | **Held for the line.** `⏸️ Merge gate: declined @3b4204985ed9d81c3144b657eaee4e3fa4c242a7 — What's new: The pull request body has no `What's new:` line outside fenced code — put one in the first lines of the body. (standards/whats-new.md)`. The run logs `decision=hold` | [note](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/29#issuecomment-5972244138), run [37144855993](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37144855993) |
| 11:37:32 | The answer `standards/whats-new.md` prescribes: `What's new: none` back in the body, then one empty commit, `07bbac9` `fix(DRE-5633): What's new line`. **Made by hand**: the fix agent is disabled in this repository | — |
| 11:37:52 | The critic **did not re-review** the new head. QA Review run [37144905180](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37144905180) logged `review=false`, `carried_sha=3b42049…`: the diff was unchanged, so the approval carries | — |
| 11:38:25 | **Merge.** The gate logged `decision=merge`, `CI green + critic APPROVE bound to 3b42049…, carried to head 07bbac9…: the PR's own changes are unchanged` | run [37144934971](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/37144934971), [carried-verdict note](https://github.com/dreadnought-foundry/agent-bureau-demo/pull/29#issuecomment-5972256872) |
| 11:38:29 | Merged by `agent-bureau-qa-bot`, merge commit `7f32485` | PR #29 |
| 11:38:42 | linear-sync left DRE-5633 open: `🔒 Merged — card deliberately left open` (the `no-code` guard) | DRE-5633 |

Between the hold and the merge, the only thing that changed was the body's line. The diff, the approval and CI were the same. The empty commit was there only to give the gate a new head to wake on.

## 3. What this observation found besides the pass

- **The critic does not re-review an empty commit after an approval.** `standards/whats-new.md` says "The critic reviews only a new head". Here the new head was reviewed by nobody: `should_review_pr.py` carried the approval because the diff was unchanged, and the gate merged on the carried approval. That is correct for this case, because the gate, not the critic, reads the line on a carried approval. It does mean the path after a critic's REQUEST_CHANGES for a missing line is still unobserved. That is the path observation 1 describes, where there is no approval to carry.
- **The carried-verdict note gives a reason that is false in this case.** It reads "the head moved only because main was merged in, and that merge touched nothing this pull request changes". Nothing was merged in: the head moved by one empty commit on the branch. The fingerprint and the decision are right; only the sentence explaining why the head moved is wrong.

## 4. What is left

- **Observation 1, the rest:**
  - a lineless pull request opened after the cutover, sent back by the critic;
  - answered by the fix agent once it is switched back on, in a repository where it runs;
  - with no hand edit.
- **Observation 2:** a sentence naming a `DRE-` number, sent back by the critic and approved once rewritten.
- **Observation 3:** a real release-train lap on agent-bureau or portico, with its `whats-new.json`, and the Linear half wherever a surface declares `linear_pipelines`.
- **The CEO reads this record and closes DRE-5633**, or keeps it open for the observations still owed.
