# PROOF record — DRE-6381: PROOF: a bookkeeping send-back went to the card and a signed answer was read off the card, observed on the live board (epic DRE-4059)

**Status: PASS.**

The released code does what the epic said, on the real threads. The classifier reads DRE-3879's seven rounds and DRE-3880's rounds as predicted, off the whole thread. The writer puts the CEO's three signed answers on DRE-3879 into its description with no signature. The earlier-answer block names the right answer on both cards. A bookkeeping `QUESTION` goes to the planner's rewrite with its finding in hand, not to Green Light. A card with no criteria is sent to the rewrite before the critic's `PASS` is read. The sanitize step hands the critic and the planner the description with the answers in it.

## How this was recorded

- Run: proof run 38017997245 — https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38017997245
- Release observed: the `stable` tag (this repo's one release surface, `pipeline-channel` in `.github/bureau/release.json`, `record: channel`) at `adda475d78ade457a9492ddd843a10386f47dcd1`, the merge of #878 (DRE-6380). Read with `GH_TOKEN=$GH_READ_TOKEN gh api repos/dreadnought-foundry/bureau-pipeline/git/ref/tags/stable`. `scripts/proof_release.py check` over the six sibling merges printed:

  ```
  proof-release: ready — pipeline-channel (the whole repository): stable carries #859 (a0db009) for DRE-6356
  proof-release: ready — pipeline-channel (the whole repository): stable carries #861 (33fdebb) for DRE-6357
  proof-release: ready — pipeline-channel (the whole repository): stable carries #869 (9ddc63a) for DRE-6358
  proof-release: ready — pipeline-channel (the whole repository): stable carries #866 (491e010) for DRE-6359
  proof-release: ready — pipeline-channel (the whole repository): stable carries #873 (bad5c0f) for DRE-6360
  proof-release: ready — pipeline-channel (the whole repository): stable carries #878 (adda475) for DRE-6380
  ```

- The release went live at **2026-10-09 19:38 PT**: promote-channel run 38017708522 (https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38017708522), step `Move the channel`, logged GitHub's answer `{"ref":"refs/tags/stable",…,"object":{"sha":"adda475d78ade457a9492ddd843a10386f47dcd1",…}}` at 02:38:45 UTC. The run before it (38017220456, 19:30 PT) had moved `stable` only to `0629281`, which does not carry #878.
- Commit observed: every script below ran from a worktree of `adda475d78ade457a9492ddd843a10386f47dcd1` (`git worktree add … adda475`), so each row is the released code, not `main` (`main` was at `f9462ee` when this run started). Every workflow line quoted is from `.github/workflows/plan.yml` at `adda475`.
- Observation window: from **2026-10-09 19:38 PT** (the release went live) to **2026-10-09 19:52 PT** (this run's last read). Every read was made in this one sitting.
- Every time below is Pacific. Every temp file this run wrote is under `/tmp/proof/` on the runner, and nothing was written to any card but this one's heartbeats and its actor marker.

## Identities

- **GitHub, observed: `GH_READ_TOKEN`.** The step summary's line: `proof identity: github read — app agent-bureau-bot-3, owner dreadnought-foundry, permissions contents:read actions:read pull-requests:read metadata:read (variables:read not granted to this installation)`. I used it for every GitHub read: the six sibling pull requests' merges, `.github/bureau/release.json`, the `stable` tag, the two promote-channel runs' logs, and the planning runs in the window and their jobs and logs. The one write I made with it on purpose, at 2026-10-09 19:48 PT:

      GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs \
        -f ref="refs/heads/proof-write-probe-38017997245" -f sha=adda475d78ade457a9492ddd843a10386f47dcd1

  GitHub's answer: `{"message":"Resource not accessible by integration","documentation_url":"https://docs.github.com/rest/git/refs#create-a-reference","status":"403"}` — `gh: Resource not accessible by integration (HTTP 403)`. A read of the ref afterwards answered `404`: no ref was created.
- **GitHub, written: `GH_TOKEN`.** Used only to push `agent/DRE-6381-proof-record` and open this record's pull request.
- **Linear: `LINEAR_API_KEY`, the fleet key** — the pipeline's scripted read identity. Reads: DRE-3879's, DRE-3880's, DRE-6288's and DRE-4109's threads; DRE-3879's and DRE-4109's descriptions, titles and labels; and the board's comments created in the window. Writes: this card's heartbeats and its actor marker, nothing else. No other card was written; `--dry-run` and `decide` with no note or record file write nothing to a card. linear requests: 35 of 40 — 15 for the reads (each read invocation's `linear-calls:` line, summed), 14 for seven heartbeats at 2 each (`⏳ 1/5` to `⏳ 4/5`, and `⏳ 5/5` three times: the first push and two amended pushes), and 6 for three actor markers at 2 each, one after each push.
- **aws: none.** The step summary's line: `proof identity: aws — none, PROOF_ROLE_ARN not provided`. No row here needs one.
- **Scratch state: none created.**

## Criteria

| Criterion | Result |
| -- | -- |
| The record exists at `docs/send-back-class-proof-2026-10.md` and names the release tag that carried the six siblings, read off the release record and not assumed, and the window's start (the release going live) and end (this run's last read) in PT. | Met. This file; `stable` at `adda475`, read off the tag and `proof_release.py check` (`ready` for all six); window 2026-10-09 19:38 PT to 2026-10-09 19:52 PT — see How this was recorded. |
| Row 1: the `send-back-classes` output for DRE-3879 and DRE-3880, run against the live threads, is quoted verbatim and matches the predicted classes, with round 7 of DRE-3879 printed as `pass`, and the record states the comment count of both threads at the time of the read beside the output. | Met. DRE-3879 rounds 1/3/5 `decision`, 2/4/6 `revision`, 7 `pass` (41 comments); DRE-3880 rounds 1/3 `revision`, 2 `decision`, and a later round 4 `pass` (34 comments) — Row 1 below. |
| Row 2: the `answer_into_card.py write DRE-3879 --dry-run` output, run against the live thread, is quoted: the `## Decisions from the CEO` block carries the three signed PT times in order, no quoted answer opens with an `Answer from … PT:` line, the printed description carries no `🔏 console-answer:` trailer, and each of the three comments is quoted as still carrying its trailer. | Met. The block reads 2026-09-14 08:14, 2026-09-15 13:12, 2026-09-16 07:28 PT; zero `🔏` and zero `Answer from` in the output; comments 8, 13 and 21 each still carry their trailer — Row 2 below. |
| Row 3: `prior_answer_block` over DRE-6288's whole live thread is quoted, in the first form, naming the 12:25 PT answer and saying there are two; over DRE-3879's whole live thread it is quoted naming the 2026-09-16 07:28 PT answer and saying there are three; the block over DRE-3879's fifty-newest window is quoted beside the thread's comment count at the read, with the record saying which form it is and why; and the released `escalate()` line that reads the block's thread with `whole_thread=True` is quoted with its line number. | Met. DRE-6288: two, newest 2026-10-08 12:25 PT; DRE-3879: three, newest 2026-09-16 07:28 PT; DRE-3879's window gave the same first form because the thread held 41 comments, under fifty; `planning_escalation.py:770` is the read, called from `escalate()` at line 1516 — Row 3 below. |
| Row 4: `decide --stage one-off --card-file --card-title --card-labels --github-output` over the real round-4 finding under a `QUESTION` header, with DRE-3879's live body, title and labels, writes `action=revise`, `findings_count=1` and the finding as the `findings` block's item 1 to the temp file and prints a `result=SEND_BACK` record whose reason is the round-4 finding; over the real round-1 finding it writes `action=escalate` and `findings_count=0`; the body's `- [ ]` count is stated; and the released workflow's gate line on the Green Light write is quoted — all of it in the record. | Met. Round 4: `action=revise`, `findings_count=1`, `findings=1. The card doesn't say which two branches…`, record `result=SEND_BACK`; round 1: `action=escalate`, `findings_count=0`; body has 10 `- [ ]`; gate at `plan.yml:1828` — Row 4 below. |
| Row 5: `decide --stage one-off --card-file --card-title --card-labels --github-output` over DRE-4109's description with its criteria cut, its live title and labels, and its real round-2 `PASS` line writes `action=revise` to the temp file, prints a `result=SEND_BACK` record whose reason is the missing-criteria finding and a note naming the missing criteria, and writes `findings_count=1` with the missing-criteria reason as the one `findings` item; all of it is quoted, and the title and labels handed over are quoted beside them. | Met. `action=revise`, `findings_count=1`, record `result=SEND_BACK — the card states no acceptance criteria…`, note "it states no acceptance criteria, and its labels and title do not route it" — Row 5 below. |
| Row 6: the released workflow's `answer` step order, the sanitize step's `RAW_DESCRIPTION` line and the two prompts' `steps.card.outputs.description` lines are quoted with line numbers; and the writer's `--dry-run --github-output` description for DRE-3879, run through the sanitizer's `body RAW_DESCRIPTION description` command, is quoted carrying the `## Decisions from the CEO` heading and the three PT times. | Met. `answer` is step 13, before `Classify` (14) and `Sanitize` (27); `RAW_DESCRIPTION` at line 1154; prompts at lines 1428 and 1688; the sanitizer's `description` block carries the heading and the three times — Row 6 below. |
| `## Seen in the window` records each of the four watched events as occurred, with card or run, time and quoted text, or as "did not occur in the window", as prose and not as table rows. | Met. All four are recorded under Seen in the window, as prose. |
| The criterion table is `\| Criterion \| Result \|` with a one-line Result per row, and each row's verbatim output is in its own section below the table. | Met. This table, one line per Result; Rows 1 to 6 each have their own section below. |
| The CEO closes this card after reading the record | Open: the CEO's step |

## Row 1 — the real history reproduces

Comment counts, read with `python3 scripts/linear_ops.py dump-comments <CARD> --with-authors` (the whole thread) and counted:

- DRE-3879: **41 comments** at 2026-10-09 19:45:51 PT (oldest 2026-09-13 21:54 PT, newest 2026-09-16 17:33 PT).
- DRE-3880: **34 comments** at 2026-10-09 19:45:51 PT (oldest 2026-09-13 23:53 PT, newest 2026-10-08 21:52 PT).

`python3 scripts/plan_critic.py send-back-classes DRE-3879`, at 2026-10-09 19:45:44 PT (tab-separated, as printed):

```
round 1	decision	decision — a call nobody has made: "without picking one"	The card offers two different fixes for the same problem without picking one, and choosing between them is a security policy call, not a coding task.
round 2	revision	revision — the card must say it: "doesn't show up anywhere we can actually verify"	DRE-3879 claims the CEO already decided how to fix this, but that decision doesn't show up anywhere we can actually verify he said it.
round 3	decision	decision — a call nobody has made: "nobody has made that call"	Making the new pull request merge itself without a person watching needs widening the pipeline's own trusted-branch list, and that list is deliberately narrow and was never meant to grow just by picking a matching name — nobody has made that call.
round 4	revision	revision — the card must say it: "doesn't say which"	The card doesn't say which two branches are now allowed to merge automatically, even though that was already decided, so the fix still can't land without a person watching it.
round 5	decision	decision — a call nobody has made: "never decides whether"	the card never decides whether the weekly model-update job gets let off our "write the test before the fix" check, and without that decision its pull request can never pass on its own.
round 6	revision	revision — the card must say it: "never written into the card's own checklist"	The CEO's newest decision, letting one specific file skip our "write the test first" rule, was given in a reply but never written into the card's own checklist, so whoever builds this still can't find it there.
round 7	pass	—	every choice this card used to leave open is now a written-down, verified decision from the CEO, and the instructions match how the automation actually works.
```

`python3 scripts/plan_critic.py send-back-classes DRE-3880`, at 2026-10-09 19:45:45 PT:

```
round 1	revision	revision — the card must say it: "not confirmed to be from the CEO"	The switch to the newer model is still an open decision, not a settled one: the only record of anyone approving it is a comment that is explicitly not confirmed to be from the CEO.
round 2	decision	decision — a call nobody has made: "cannot actually be built as written"	Putting the new model onto the everyday work ladder breaks a standing safety rule that keeps our "reviews the work" model and our "does the work" model separate, so this change cannot actually be built as written.
round 3	revision	revision — the card must say it: "the card still … but the CEO's newest, verified answer says"	the card still tells the builder to retire the older model, but the CEO's newest, verified answer says to keep that older model in its safety-net spot and not retire it, so building from this card would undo the CEO's own latest instruction.
round 4	pass	—	The CEO's own latest signed answer now sits in the card and matches every checkbox, and nothing left in it asks an agent to decide anything instead of build it.
```

DRE-3879 matches all seven predicted classes. DRE-3880 matches rounds 1 to 3 as predicted and also prints a round 4 `pass` that the card did not list — a round recorded after the prediction was written, and not one the prediction contradicts. Each command read the whole thread through `linear_ops.comment_bodies(card, whole_thread=True)` (`plan_critic.py:4313`).

## Row 2 — a signed answer lands in the card

`python3 scripts/answer_into_card.py write DRE-3879 --dry-run`, at 2026-10-09 19:45:57 PT, printed this, in full:

```
answer-into-card: dry run, nothing written — would write 3 answer(s) into DRE-3879's description
**CEO decision — signed console answer, 2026-09-16 07:28 PT. This is the instruction; build to it.**

`models.json` counts as **data, not code**. Add that one file to the TDD check's data list, beside `config/` and `agents.yaml`, in this pull request, with a failing test first.

**Nothing else is exempted and no check is skipped.** A red check or a REQUEST_CHANGES verdict still blocks the merge, and any `.py` or other code change on the same pull request still needs its test committed first. The exemption covers exactly `models.json` and nothing else.

**Trusted branches** (signed answer, 2026-09-15 13:12 PT): exactly two literal names, `bot/split-ledger` and `bot/model-drift`, trusted the same way `bot/standards-sync` is. No wildcard; no other branch gains merge rights.

**No branch-protection change** (signed answer, 2026-09-14 08:14 PT): everything reaches `main` through a PR. Adding the two names to the merge gate's trusted list is a change to our own pipeline code in this PR — it is *not* a GitHub branch-protection change. `main` keeps requiring a PR and both required checks.

This card fixes **both** jobs that push straight to `main`: `split-ledger.yml` and `model-drift.yml`. Epic [DRE-3892](https://linear.app/dreadnoughtfoundry/issue/DRE-3892/epic-new-anthropic-model-versions-adopt-themselves-a-newer-version-of) is blocked by this card.

## Acceptance criteria

- [ ] `split-ledger.yml` and `model-drift.yml` no longer run `git push origin HEAD:main`. Each commits to one fixed branch and opens or updates a single PR, never a new PR per run.
- [ ] `split-ledger.yml` commits to `bot/split-ledger`; `model-drift.yml` commits to `bot/model-drift`. Those exact names, nothing else.
- [ ] The merge gate's trusted-branch list (`.github/workflows/merge-gate.yml`, today `agent/*|repair/*|dependabot/*|bot/standards-sync`) gains **exactly these two literal entries**. No wildcard. Every other place that treats `bot/standards-sync` as a trusted pipeline branch (reconcile's `pipeline_owns`, the TDD-commit exemption if it applies) is updated the same way, with tests.
- [ ] `models.json` **is added to the TDD check's data list**, beside `config/` and `agents.yaml` — written as a failing test first, in this PR.
- [ ] The exemption is exactly that one file. A test pins that a `.py` or other code change on the same PR still requires its test committed first, and that no other path gained an exemption.
- [ ] CI and the critic still run on these PRs. A red check or a REQUEST_CHANGES verdict still blocks the merge, and a test pins that.
- [ ] The PR body names which branch it is on.
- [ ] A run with nothing changed opens no PR and exits green.
- [ ] A test pins that neither workflow pushes to `main`.
- [ ] No GitHub branch protection, ruleset or repository setting is changed.

---

*Original report, kept for the record.*

The daily "Split ledger" job regenerates the ledger file correctly and then cannot save it, because GitHub blocks the write to `main`. It has failed this way every day since the card was opened; the sibling "Model drift" job has failed identically since at least 2026-08-10.

Technical detail: `git push origin HEAD:main` in `.github/workflows/split-ledger.yml` is rejected with `GH006: Protected branch update failed` ("Changes must be made through a pull request" / "2 of 2 required status checks are expected") — branch protection on `main` does not admit the `agent-bureau-bot` App token for direct pushes.

## Decisions from the CEO
Copied from his signed console answers; the signature stays on the comment and this copy proves nothing on its own.

**2026-09-14 08:14 PT**

> Open a PR; no exception to branch protection. Fix both split-ledger.yml and model-drift.yml this way, one PR per job updated in place. Don't change any branch protection setting.

**2026-09-15 13:12 PT**

> Yes. Add exactly two literal branches to the merge gate's trusted list: bot/split-ledger and bot/model-drift, trusted the same way bot/standards-sync is. No wildcard; no other branch gains merge rights. CI and the critic still run on these PRs, and a red check or REQUEST_CHANGES still blocks the merge.

**2026-09-16 07:28 PT**

> Count models.json as data, not code. Add that one file to the TDD check's data list beside config/ and agents.yaml, in this pull request, with a failing test first. Nothing else is exempted and no check is skipped: a red check or a REQUEST_CHANGES verdict still blocks the merge, and any .py or code change on the same pull request still needs its test committed first. The trusted branches stay exactly the two literal names already decided, bot/split-ledger and bot/model-drift. That settles every open question on this card — build it.
```

- The block's times, in order: `**2026-09-14 08:14 PT**`, `**2026-09-15 13:12 PT**`, `**2026-09-16 07:28 PT**`.
- No quoted answer opens with `Answer from … PT:` — each `> ` line opens with his words. `grep -c "🔏"` over the output: `0`; `grep -n "console-answer\|Answer from"`: no match.
- The trailers are still on the comments. From the whole thread read for Row 1 (DRE-3879, 41 comments), the three comments carrying `🔏 console-answer:`, each written by the pipeline's key:

  - comment 8, 2026-09-14 08:14 PT, first line `Answer from Sid Conklin (signed in to the console), 2026-09-14 08:14 PT:`, trailer:
    `🔏 console-answer: v1 card=DRE-3879 sha256=b2d3e4260fdda84ab5e289911c3d0b1f3756c78ab2c3a6bb4f72dc93b5f7fc82 user=88c1b3e0-7031-70df-dae2-99dc7efea1ec at=2026-09-14T15:14:58Z kid=7d1f6e507b7bc4ad sig=AlN8XQ4nvOHiG3QKkC0eY4sEfPd26UdE6vzyRXaFL0LTTh_faf1AYlv1up7c7UHDjZKId1pSUseueWMjvPIVDQ`
  - comment 13, 2026-09-15 13:13 PT, first line `Answer from Sid Conklin (signed in to the console), 2026-09-15 13:12 PT:`, trailer:
    `🔏 console-answer: v1 card=DRE-3879 sha256=1ba25c06dae84f23b75a5f3387df9099b43c8c6d0726892663f20d89f6f7525f user=88c1b3e0-7031-70df-dae2-99dc7efea1ec at=2026-09-15T20:12:59Z kid=7d1f6e507b7bc4ad sig=oyu6upyotkLZsZ_bBJEamMHMKi_n6-3giXmTSCwcu2wJ3JKvNumsSkuW1vgsc_bovjHv0Qeryelma3oFqNQdCw`
  - comment 21, 2026-09-16 07:28 PT, first line `Answer from Sid Conklin (signed in to the console), 2026-09-16 07:28 PT:`, trailer:
    `🔏 console-answer: v1 card=DRE-3879 sha256=8bcf2d4f65859a4c0c4f16022cd79e6149a41462af236ff92e0fc964b61ce925 user=88c1b3e0-7031-70df-dae2-99dc7efea1ec at=2026-09-16T14:28:29Z kid=7d1f6e507b7bc4ad sig=YRQiAqCpgoeMqDzb334RgjxWVRxpH30nwROmfL9k3-2pb17cKNb0odFKdmsXgXpu8sE3bYmjyQpItPaMioAyCQ`

The text above the block (from `**CEO decision — signed console answer, 2026-09-16 07:28 PT…**` to the original report) is the card's own hand-written description, left byte for byte; the writer generated only the `## Decisions from the CEO` block.

## Row 3 — a parked question names the earlier answer, off the whole thread

One process, from the release worktree:

```python
thread = linear_ops.comment_timeline(card, whole_thread=True)   # or comment_timeline(card) for the window
print(planning_escalation.prior_answer_block(thread, None, card=card))
```

What it printed:

```
### DRE-6288 whole thread — read at 2026-10-09 19:46:10 PT; comment_timeline returned 44 comments
Your earlier answers on this card: 2. The newest, 2026-10-08 12:25 PT: "go ahead and add it but we are not using it currently". They are on the record, and the question above was raised after the newest; nothing here asks you to answer any of them again.

### DRE-3879 whole thread — read at 2026-10-09 19:46:10 PT; comment_timeline returned 41 comments
Your earlier answers on this card: 3. The newest, 2026-09-16 07:28 PT: "Count models.json as data, not code. Add that one file to the TDD check's data list beside config/ and agents.yaml, in this pull request, with a failing test first. Nothing else is exempted and no check is skipped: a red check or a REQUEST_CHANGES verdict still blocks the merge, and any .py or code…". They are on the record, and the question above was raised after the newest; nothing here asks you to answer any of them again.

### DRE-3879 fifty-newest window — read at 2026-10-09 19:46:11 PT; comment_timeline returned 41 comments
Your earlier answers on this card: 3. The newest, 2026-09-16 07:28 PT: "Count models.json as data, not code. Add that one file to the TDD check's data list beside config/ and agents.yaml, in this pull request, with a failing test first. Nothing else is exempted and no check is skipped: a red check or a REQUEST_CHANGES verdict still blocks the merge, and any .py or code…". They are on the record, and the question above was raised after the newest; nothing here asks you to answer any of them again.
```

- **DRE-6288, whole thread (44 comments):** the first form, naming the 2026-10-08 12:25 PT answer and its first line, and saying there are two. It says the question above "was raised after the newest", which is the block's way of saying the question is a new one.
- **DRE-3879, whole thread (41 comments):** the first form, naming the 2026-09-16 07:28 PT answer and saying there are three.
- **DRE-3879, fifty-newest window (`comment_timeline("DRE-3879")`, 41 comments returned):** the **first form**, the same as the whole thread. The thread held 41 comments at 19:46:11 PT, under the fifty-comment window, so the window still reached the oldest three answers. The second form, `There is no earlier signed answer from you on this card.` (`planning_escalation.py:690`), would appear only once the thread grows past fifty and the answers scroll out.

The released `escalate()` makes the block's read through `_prior_answer`, which takes the whole thread. `scripts/planning_escalation.py` at `adda475`:

```
770:        thread = linear_ops.comment_timeline(identifier, whole_thread=True)
```

That line is in `_prior_answer(linear_ops, identifier)` (line 763), and `escalate()` (line 1390) calls it for the block:

```
1516:                               prior_answer=_prior_answer(linear_ops, identifier))
```

while the read `escalate()` holds for its attempt count is the window:

```
1470:    bodies = list(comments) if handed else linear_ops.comment_timeline(identifier)
```

So the block is built from the whole-thread read, not from the window `escalate()` already holds.

## Row 4 — a bookkeeping question does not reach Green Light

**The inputs.** The findings were read off DRE-3879's live thread — the 41-comment whole-thread read from Row 1 — with `plan_critic.send_back_findings(bodies, "one-off")`. All six one-off send-back rounds are recorded there as `SEND_BACK`. Round 4's finding (item 4) and round 1's (item 1) were each written to a result file:

```
PLAN-CRITIC: QUESTION — The card doesn't say which two branches are now allowed to merge automatically, even though that was already decided, so the fix still can't land without a person watching it.
PLAN-CRITIC: QUESTION — The card offers two different fixes for the same problem without picking one, and choosing between them is a security policy call, not a coding task.
```

DRE-3879's live description, title and labels came from one single-issue read (`critic_score.read_card`, 2026-10-09 19:46:43 PT). The description went to the body file, which carries **10** `- [ ]` items. Handed over:

- `--card-title "Pipeline failure: Split ledger"`
- `--card-labels "repo:bureau-pipeline,size:S,initiative:bureau,agent:devops"`

The labels name no `agent:ops` or `no-code` and the title opens with no routing convention, and the body states criteria, so DRE-6380's precheck had nothing to say and the critic's word decided (no `one-off precheck:` line was printed).

**Round 4**, at 2026-10-09 19:46:56 PT:

```
echo '[]' | python3 scripts/plan_critic.py decide --stage one-off --epic DRE-3879 \
  --result-file /tmp/proof/r4/result-round4.txt --card-file /tmp/proof/r4/body.md \
  --card-title "Pipeline failure: Split ledger" \
  --card-labels "repo:bureau-pipeline,size:S,initiative:bureau,agent:devops" \
  --github-output /tmp/proof/r4/gh-round4.txt
```

Printed (stdout; stderr was empty; exit 0):

```
🔁 **Pre-approval critic — before this is built** — the critic asked the CEO: The card doesn't say which two branches are now allowed to merge automatically, even though that was already decided, so the fix still can't land without a person watching it. — it wrote QUESTION — but what it found names something the card must say (revision — the card must say it: "doesn't say which") rather than a choice only he can make, so it is read as a revision: round 1 of 2. The planner rewrites the card and the critic reads it again.

Reason: The card doesn't say which two branches are now allowed to merge automatically, even though that was already decided, so the fix still can't land without a person watching it.

This card is not going to the build queue yet, and nothing here is for the CEO — it stays in Planning while the planner rewrites it to answer every finding above, and the critic reads it again.

plan-critic: stage=one-off round=1 result=SEND_BACK collisions=0 — The card doesn't say which two branches are now allowed to merge automatically, even though that was already decided, so the fix still can't land without a person watching it.
```

The temp file `/tmp/proof/r4/gh-round4.txt`:

```
action=revise
result=QUESTION
reason=The card doesn't say which two branches are now allowed to merge automatically, even though that was already decided, so the fix still can't land without a person watching it.
round=1
collisions=0
bound=false
findings_count=1
open=unread
open_count=
ran_out=false
note=the critic asked the CEO: The card doesn't say which two branches are now allowed to merge automatically, even though that was already decided, so the fix still can't land without a person watching it. — it wrote QUESTION — but what it found names something the card must say (revision — the card must say it: "doesn't say which") rather than a choice only he can make, so it is read as a revision: round 1 of 2. The planner rewrites the card and the critic reads it again.
findings=1. The card doesn't say which two branches are now allowed to merge automatically, even though that was already decided, so the fix still can't land without a person watching it.
```

`action=revise`; the record line carries `result=SEND_BACK` with the round-4 finding as its reason; the note says "it wrote QUESTION"; `findings_count=1` and the `findings` block's item 1 is the round-4 finding — the list the planner's rewrite is handed.

**Round 1**, the same command over `/tmp/proof/r4/result-round1.txt` with `--github-output /tmp/proof/r4/gh-round1.txt`, at 2026-10-09 19:46:56 PT. Printed:

```
🙋 **Pre-approval critic — before this is built** — The card offers two different fixes for the same problem without picking one, and choosing between them is a security policy call, not a coding task.

This card is not going to the build queue — it is with a person, in the decision queue, with the reason above.

plan-critic: stage=one-off round=1 result=QUESTION collisions=0 — The card offers two different fixes for the same problem without picking one, and choosing between them is a security policy call, not a coding task.
```

The temp file:

```
action=escalate
result=QUESTION
reason=The card offers two different fixes for the same problem without picking one, and choosing between them is a security policy call, not a coding task.
round=1
collisions=0
bound=false
findings_count=0
open=unread
open_count=
ran_out=false
note=The card offers two different fixes for the same problem without picking one, and choosing between them is a security policy call, not a coding task.
```

`action=escalate` and `findings_count=0`, as predicted: a real decision still goes to the CEO.

**The Green Light gate**, `.github/workflows/plan.yml` at `adda475`, the step `One-off critic — escalate` (line 1827), whose run is `planning_escalation.py escalate` (line 1842):

```
1828:        if: steps.oneoff.outputs.action == 'escalate' || steps.oorevised.outputs.outcome == 'asked'
```

`action=revise` matches neither half, so round 4 does not reach the Green Light write; the second half admits the planner's own `asked` outcome.

## Row 5 — a card with no criteria goes to the rewrite

**The inputs.** DRE-4109's live description, read at 2026-10-09 19:46:43 PT (`critic_score.read_card`), with its `## Acceptance criteria` section cut out: the heading and its six `- [ ]` items, lines 15 to 23 of the description, up to the `---` rule that opens the original report. The body file kept everything else, and has no `- [ ]` and no `Acceptance criteria` left. Handed over, from the same read:

- `--card-title "Pipeline failure: Reconcile"`
- `--card-labels "repo:bureau-pipeline,initiative:bureau,agent:devops"`

The round-2 record, read live off DRE-4109's whole thread (`linear_ops.comment_bodies("DRE-4109", whole_thread=True)`, 32 comments, 19:46:44 PT):

```
plan-critic: stage=one-off round=2 result=PASS collisions=0 — Sid already gave a clear, verified answer for how to handle this GitHub rate-limit hiccup, so the only thing left is building exactly what he said, not deciding anything.
```

written to the result file as:

```
PLAN-CRITIC: PASS — Sid already gave a clear, verified answer for how to handle this GitHub rate-limit hiccup, so the only thing left is building exactly what he said, not deciding anything.
```

**The run**, at 2026-10-09 19:47:17 PT:

```
echo '[]' | python3 scripts/plan_critic.py decide --stage one-off --epic DRE-4109 \
  --card-file /tmp/proof/r5/body.md --card-title "Pipeline failure: Reconcile" \
  --card-labels "repo:bureau-pipeline,initiative:bureau,agent:devops" \
  --result-file /tmp/proof/r5/result.txt --github-output /tmp/proof/r5/gh.txt
```

Printed (stdout; stderr was empty; exit 0):

```
🔁 **Pre-approval critic — before this is built** — the card's own text decided before the critic's word was read: it states no acceptance criteria, and its labels and title do not route it — sent back — round 1 of 2. Every finding here is a defect in the card an agent can fix, so the planner revises the card in place and the critic reads it again.

Reason: the card states no acceptance criteria, so there is no exit condition to route on. Name what must be true for this card to be done, as `- [ ]` items. The card has no `## Acceptance` section at all.

This card is not going to the build queue yet, and nothing here is for the CEO — it stays in Planning while the planner rewrites it to answer every finding above, and the critic reads it again.

plan-critic: stage=one-off round=1 result=SEND_BACK collisions=0 — the card states no acceptance criteria, so there is no exit condition to route on. Name what must be true for this card to be done, as `- [ ]` items. The card has no `## Acceptance` section at all.
```

The temp file `/tmp/proof/r5/gh.txt`:

```
action=revise
result=PASS
reason=Sid already gave a clear, verified answer for how to handle this GitHub rate-limit hiccup, so the only thing left is building exactly what he said, not deciding anything.
round=1
collisions=0
bound=false
findings_count=1
open=unread
open_count=
ran_out=false
note=the card's own text decided before the critic's word was read: it states no acceptance criteria, and its labels and title do not route it — sent back — round 1 of 2. Every finding here is a defect in the card an agent can fix, so the planner revises the card in place and the critic reads it again.
findings=1. the card states no acceptance criteria, so there is no exit condition to route on. Name what must be true for this card to be done, as `- [ ]` items. The card has no `## Acceptance` section at all.
```

`action=revise`; the record carries `result=SEND_BACK` with the missing-criteria finding as its reason; the note names the missing criteria and says the card's labels and title do not route it, so the card got past the label and title precedence before the criteria rule decided; `findings_count=1`, and the one `findings` item is the missing-criteria reason. The step output's `result=PASS` and `reason=` lines carry what the critic wrote; the record line is what decided.

## Row 6 — the same-run readers see the write

**The workflow at the release**, `.github/workflows/plan.yml` at `adda475`. The order of `jobs.plan.steps`, parsed with `yaml.safe_load` (index, id, name):

```
13 answer Write the CEO's answers into the card
14 classify Classify the card — one-off, epic or wave
27 card Sanitize untrusted epic text
```

The lines:

```
600:      - name: Write the CEO's answers into the card
601:        id: answer
611:      - name: Classify the card — one-off, epic or wave
1149:      - name: Sanitize untrusted epic text
1150:        id: card
1154:          RAW_DESCRIPTION: ${{ steps.answer.outputs.description || github.event.client_payload.description }}
1428:            ${{ steps.card.outputs.description }}
1688:            ${{ steps.card.outputs.description }}
```

Line 1428 is in `Pre-approval critic — the one-off exit` (line 1368), the one-off critic's prompt. Line 1688 is in `One-off revision — the planner answers the critic` (line 1635), the planner's one-off revise prompt. `RAW_DESCRIPTION` reads `steps.answer.outputs.description` before the payload field.

**The path, walked on real state.** At 2026-10-09 19:47:39 PT:

```
python3 scripts/answer_into_card.py write DRE-3879 --dry-run --github-output /tmp/proof/r6/answer-out.txt
```

printed `answer-into-card: dry run, nothing written — would write 3 answer(s) into DRE-3879's description`, followed by the same description as Row 2 (`cmp` of the two: identical). The `description` value was taken from between the temp file's heredoc delimiters and run through the sanitize step's description half:

```
RAW_DESCRIPTION="$(cat /tmp/proof/r6/description-value.txt)" GITHUB_OUTPUT=/tmp/proof/r6/sanitize-out.txt \
  python3 scripts/sanitize_untrusted.py body RAW_DESCRIPTION description
```

It printed `description: sanitized (body mode, 0 sentinel-lookalike line(s) defanged)`. Lines 2 to 48 of the second file are identical to lines 2 to 48 of Row 2's output (`diff`: no difference) — the card's description and then the block. The second file's first line and its last sixteen lines:

```
description<<EOF-c3a4330c18564355a77f7ea9d5ff2110
…

## Decisions from the CEO
Copied from his signed console answers; the signature stays on the comment and this copy proves nothing on its own.

**2026-09-14 08:14 PT**

> Open a PR; no exception to branch protection. Fix both split-ledger.yml and model-drift.yml this way, one PR per job updated in place. Don't change any branch protection setting.

**2026-09-15 13:12 PT**

> Yes. Add exactly two literal branches to the merge gate's trusted list: bot/split-ledger and bot/model-drift, trusted the same way bot/standards-sync is. No wildcard; no other branch gains merge rights. CI and the critic still run on these PRs, and a red check or REQUEST_CHANGES still blocks the merge.

**2026-09-16 07:28 PT**

> Count models.json as data, not code. Add that one file to the TDD check's data list beside config/ and agents.yaml, in this pull request, with a failing test first. Nothing else is exempted and no check is skipped: a red check or a REQUEST_CHANGES verdict still blocks the merge, and any .py or code change on the same pull request still needs its test committed first. The trusted branches stay exactly the two literal names already decided, bot/split-ledger and bot/model-drift. That settles every open question on this card — build it.
EOF-c3a4330c18564355a77f7ea9d5ff2110
```

That is the text the critic and the planner would be handed on DRE-3879's next planning run: the `## Decisions from the CEO` heading and the three PT times, seen without a run and without a write.

## Seen in the window

The window runs from 2026-10-09 19:38 PT to 2026-10-09 19:52 PT.

How the window was read: one Linear query, as the fleet key at 2026-10-09 19:52:37 PT, for every comment on the board created after 02:38:45 UTC (19:38:45 PT). It returned 61 comments, all of them, with no further page. The planning runs were read from Actions, as the read token: `self-plan.yml` and `plan.yml` runs created since the release.

**The CEO answering a card from the console, and his words in that card's description on its next planning run** — did not occur in the window. None of the 61 comments carries a `🔏 console-answer:` trailer, and no comment in the window is a signed answer.

**A one-off critic `QUESTION` the classifier reads as `revision`, and that card's next lane write not being Green Light** — did not occur in the window. The window holds one one-off critic record, on DRE-6536 at 19:47:02 PT, and the critic wrote `SEND_BACK`, not `QUESTION`: `plan-critic: stage=one-off round=1 result=SEND_BACK collisions=0 — Half of this card changes the step that signs the browser in, and that step does not exist in the code yet, so there is nothing to change and no card is listed as building it first.` No comment in the window carries `result=QUESTION` or the "it wrote QUESTION" note.

**An answered card re-entering Planning, and no escalation after it repeating the Question line he answered** — did not occur in the window. No escalation was posted to any card in the window, and no card carrying a signed answer was planned in it.

**A planning run in this repo after the release, not dispatched as a review ask, printing an `answer-into-card:` line from its `answer` step** — occurred. Run 38018110161 (https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38018110161), `self-plan.yml` calling `.github/workflows/plan.yml@main` at commit `f9462eea62f9a004271a66ea30e0dbc67e52d98d` (the run's `referenced_workflows`). That commit is `main` after #891, which contains `adda475`. The card was DRE-6536. Its step `Dispatch reason — a review the pipeline asked for?` logged `reason '': not a review ask — the front door runs as before` at 19:45:39 PT. Then its step `Write the CEO's answers into the card` logged, at 19:45:39 PT:

    answer-into-card: no verified answer on this card

DRE-6536 has no signed answer, so the step had nothing to write. The run concluded `success` at 19:51:59 PT.
