# PROOF record — DRE-5932: this epic's own proof run started by itself — the sweep dispatched it, the run observed as a read-only identity and wrote its record as a pull request, parked once for the CEO's press and returned on his answer, and the card closed on the approved merge, with run ids and PT times (epic DRE-5920)

**Status: PARTIAL.**

The sweep started this proof by itself. 41 scheduled dry passes named this card and wrote nothing. Then the first pass after `PROOF_DISPATCH_LIVE` turned `true` dispatched exactly one proof run, this one, at 16:38 PT on 2026-10-06. The run observed as the read-only App token, held no AWS session, and GitHub refused the one write it tried. The park, the CEO's answer, the return and the close happen after this run ends, so their rows stay `Not observed.` until the return run amends this record. Every time is Pacific, on 2026-10-06 unless a date is given.

| Criterion | Result |
|---|---|
| Observed on the live board: this card was dispatched by a scheduled Reconcile sweep with no person starting it, after at least two recorded dry passes that printed `would:` lines and wrote nothing, with the sweep run id, the `Proof Task` run id, the `🔬 proof-run` receipt and the PT times recorded. | **Met.** 41 scheduled Reconcile passes, from [37464599446](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37464599446) (05:39:05) to [37546413650](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37546413650) (16:26:30), printed `would: dispatch DRE-5932 — first proof run` and `proof-dispatch: eligible 1, dispatched 1, … (dry run)`. Each run's env shows `PROOF_DISPATCH_LIVE` empty. The card's thread carries no `🔬 proof-run` receipt before 16:38:15 (§2). Scheduled pass [37547465134](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37547465134) (event `schedule`, created 16:36:14) shows `PROOF_DISPATCH_LIVE: true` and printed `proof-dispatch: DRE-5932 — dispatched: first proof run (dispatch 1 of 2)` at 16:38:15. The receipt landed on the card at 16:38:15: `🔬 proof-run: dispatched a proof run at 2026-10-06 16:37 PT — first proof run (dispatch 1 of 2)`. It started `Proof Task` run [37547647590](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37547647590): event `repository_dispatch`, triggering actor `agent-bureau-bot[bot]`, created 16:38:17. It is the only `Proof Task` run on bureau-pipeline since 05:00 (§1) |
| Observed in production: the release gate's `proof-release:` reading is recorded as `waiting` for a sha `stable` did not yet carry and `ready` once it did, read off the live tags, with `pipeline-channel` read as the whole repository. | **Met.** `.github/bureau/release.json` declares `pipeline-channel` with `"paths": []` and `tag_series: ["stable"]`. Read as the read-only token at 16:43:35–16:43:39: `proof-release: ready — pipeline-channel (the whole repository): stable carries #756 (afc87ee) for DRE-5931`, the newest sibling merge. Then `proof-release: waiting — pipeline-channel (the whole repository): stable does not carry #770 (e1bd934) for DRE-6015`, with `stable` at `1c4ea91`. No sweep pass read `waiting`. `stable` moved to `afc87ee` at 05:33:37, and the first pass to read this card ran at 05:37:31. Every pass printed `waiting on release 0`, and the sweep prints no `proof-release:` line of its own (§3) |
| Observed: the proof run made every GitHub observation as the read-only App token (the mint step's permissions quoted from the step summary, and one write it was refused quoted from the record), held no AWS session, used the worker token only for the branch push and the pull request, posted its heartbeats, and opened this record on `agent/DRE-5932-proof-record`, with PT times. | **Met.** See `## Identities`. Step summary, 16:39:03: `proof identity: github read — app agent-bureau-bot-3, permissions contents:read actions:read pull-requests:read metadata:read` and `proof identity: aws — none, PROOF_ROLE_ARN not provided`. The refused write was at 16:44:26: `POST git/refs` for `refs/heads/proof-write-probe-37547647590` answered `403 Resource not accessible by integration`, and the ref does not exist. The write this card names was refused too, at 16:46:47: a comment on this record's pull request #771 answered `403 Resource not accessible by integration`. `🧠 model-attempt` at 16:39:00 names the proof agent and run 37547647590. Heartbeats: `⏳ 1/5 read` 16:39:31, `⏳ 2/5 observing` 16:40:33, `⏳ 3/5 record written` 16:46:25, `⏳ 4/5 local checks` 16:46:31, `⏳ 5/5 PR opened` 16:46:46. The worker token pushed `agent/DRE-5932-proof-record` at `329aa66` and opened [#771](https://github.com/dreadnought-foundry/bureau-pipeline/pull/771) at 16:46:39 |
| Observed: this card read `Hand-work` while waiting and while running, `In Review` when the pull request opened, `Green Light` on the park, `Hand-work` on the return and `In Review` on the amended push, with PT times, and was never in `Todo`, `In Progress`, `Backlog`, `Planning` or `Triage`. | **Not observed.** This run saw the first two moves only. It cannot see the park, the return or the amended push, because they happen after it ends. Seen: the card's only lane move before this run is `Backlog → Hand-work` at 05:24:29, by the sweep's auto-promotion. It read `Hand-work` through every dry pass and through this run's start (Linear read at 16:40). `Hand-work → In Review` came at 16:48:36, from QA Review run [37548410452](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37548410452), step `Card → In Review (a pull request opened outside the dispatch)`. The remaining moves are read off the card's history by the return run (§5) |
| Observed: this card was parked exactly once in `Green Light`, under a `🔬 proof-waiting` hold naming the press and an escalation comment naming it, with the sweeps while it waited shown holding it by name and dispatching nothing at it. | **Not observed.** The park follows this run: the run writes its escalation after the pull request is open, and the workflow parks the card when the run ends. Read by the return run off the card's thread and the sweeps that follow it |
| needs the CEO's press: his signed answer from the console, quoted with its PT time, saying whether this card reached his queue once naming the press and whether anything chased him. | **Not observed. needs the CEO's press:** answer this card from the console with the one line that says whether it reached his queue exactly once, named this press, and whether anything chased him before he answered |
| Observed: the return dispatch with the `re-run after the CEO's answer` reason and the `after the CEO's answer` count, the `proof-observed` discharge, the move back to `Hand-work`, the amended record on the same pull request, and `dispatches = 1` still, with PT times. | **Not observed.** The return follows his answer, which has not been given. Before the park, the first-run budget reads `proof-run-state: running — run 37547647590 is in_progress, started after the proof-run receipt of 2026-10-06 16:37 PT` (`proof_run_state.py check`, 16:44:33) |
| Observed in the live passes: at most one proof dispatch per pass, none on a relay-scoped pass, and a person's proof skipped by name where one existed in the window. | **Not observed.** No person's proof existed in the window: DRE-5412's pull request #713 merged on 2026-10-05 at 18:23:27 PT, before the window opened. No card on the board carries a `being observed by hand` hold since 2026-09-25. The other two parts were seen. All 43 scheduled passes show `dispatched 0` or `dispatched 1`. All 14 relay-scoped passes (`sweep-scope: card-done …`) show the `Dispatch proof runs` step `skipped` and no `proof-dispatch:` line. No run died in the window, so no pass printed `proof-run-state: dead` and no `dispatch 2 of 2` receipt exists (§6) |
| The record is merged on `main` at `docs/proof-dispatch-proof-2026-10.md`, opens with the criterion table, names the PT time of every observation, leaves open only the two closing rows the standard leaves open and says where each is read, and the CEO closes this card after reading it; the card's thread then carries the close receipt (DRE-5919's or `hyg-proof-closed`) with no person acting. | Open: this record's merge and the close happen to this file after it is written. Read them off this record's pull request, #771 on `agent/DRE-5932-proof-record` (the critic's verdict at its head sha and the merge gate's merge). Then read this card's thread for the `card-done` close under DRE-5919's rule or the `🧹 hygiene:` `hyg-proof-closed` receipt, and epic DRE-5920's state |
| The CEO closes this card after reading the record | Open: the CEO's step |

## How this was recorded

- **The run:** `Proof Task` [37547647590](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37547647590), workflow `self-proof-task.yml@main` at `1c4ea91`, dispatch reason `first proof run`. It was created at 16:38:17 and its job started at 16:38:39.
- **The commit and release observed:** `main` and `stable` both at `1c4ea9143a78` (Merge pull request #768) while this run read them.
- **The window:** from 05:00 to 16:45 on 2026-10-06. Every Reconcile and `Proof Task` run on bureau-pipeline created since 05:00 was listed off the Actions API, and all 57 Reconcile logs were downloaded and read. The Linear thread and history of DRE-5932, DRE-5412 and DRE-5843 were read at 16:40–16:42.
- **Shape:** the criterion table leads because the card asks for that. The sections below carry the evidence.

## Identities

- **GitHub, observed — `GH_READ_TOKEN`:** an App token for `agent-bureau-bot-3`. The mint step's own summary line, read off the runner's step-summary file at 16:39:03, is `proof identity: github read — app agent-bureau-bot-3, permissions contents:read actions:read pull-requests:read metadata:read`. Every GitHub read above was made with it as `GH_TOKEN=$GH_READ_TOKEN gh api …`: the run lists, the job and step lists, all 57 Reconcile logs and the Promote Channel log. So were the pull requests, the tags, `release.json`, both `proof_release.py check` calls and `proof_run_state.py check`.
- **The refused write, 16:44:26:** `GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs -f ref=refs/heads/proof-write-probe-37547647590 -f sha=1c4ea91…` answered `{"message":"Resource not accessible by integration",…,"status":"403"}`. A read of that ref then answered `404`, so nothing was created and nothing needed deleting.
- **The write the card names, 16:46:47:** `GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/issues/771/comments -f body=…` answered `{"message":"Resource not accessible by integration",…,"status":"403"}`. Pull request #771 held 0 comments afterward.
- **GitHub, written — `GH_TOKEN`:** the worker token, which also acts as `agent-bureau-bot-3[bot]`, the author of #771. It was used for:
  - the branch push at 16:46;
  - `gh pr create` at 16:46:39;
  - the `gh pr list --head agent/DRE-5932-proof-record` check just before it, which confirms no pull request was already open, as the pull-request step requires.

  It was used for no observation in this record.
- **Linear — `LINEAR_API_KEY`:** used for the reads above, this card's heartbeats, the `🤖 agent-actor` marker and nothing else. Nothing was moved or edited, and no other card was commented on.
- **aws: none.** The step summary reads `proof identity: aws — none, PROOF_ROLE_ARN not provided`, and the `Assume the caller's proof identity` step concluded `skipped`. No criterion on this card needs one.
- **One limit:** a job's log cannot be downloaded while the job runs (the API answered `404`). So this run's own step-summary lines were read off the runner's scrubbed step-summary files, not through GitHub.

## 1. The dry passes and the live dispatch

**Dry passes (41).** Run id and the PT time of its `would: dispatch DRE-5932 — first proof run` line:

37464599446 (05:39:05); 37467132203 (05:59:28); 37470099007 (06:22:40); 37472294051 (06:39:12); 37474021810 (06:52:23); 37476103181 (07:07:37); 37479121803 (07:28:54); 37481225288 (07:44:02); 37482662606 (07:54:28); 37484413544 (08:06:52); 37487362254 (08:27:01); 37489058802 (08:38:52); 37490772817 (08:51:26); 37493015762 (09:07:52); 37496212185 (09:31:50); 37498982713 (09:52:53); 37500686247 (10:06:04); 37503070383 (10:24:14); 37504695965 (10:36:47); 37506401168 (10:50:16); 37508717360 (11:08:11); 37511973784 (11:33:14); 37514759212 (11:55:13); 37516147341 (12:06:03); 37518513069 (12:24:29); 37520073026 (12:36:46); 37521593974 (12:49:27); 37523806971 (13:07:51); 37526709473 (13:30:09); 37528661152 (13:45:36); 37529708812 (13:53:49); 37531237076 (14:06:01); 37533668636 (14:25:53); 37534976409 (14:36:55); 37536324850 (14:48:53); 37538293367 (15:06:56); 37540539603 (15:27:45); 37541812840 (15:39:27); 37542842506 (15:49:36); 37544512380 (16:06:30); 37546413650 (16:26:30).

The one scheduled pass before them, 37461042981 at 05:07:33, read `eligible 0 … (dry run)`. The card was still in `Backlog` then.

**The live pass, 37547465134.** It was created at 16:36:14 and its `Dispatch proof runs` step started at 16:37:52 with `PROOF_DISPATCH_LIVE: true`. Its proof lines, at 16:38:15:

    proof-dispatch: DRE-5932 — eligible: first proof run (dispatch 1 of 2)
    proof-dispatch: DRE-5932 — dispatched: first proof run (dispatch 1 of 2)
    commented on DRE-5932
    proof-dispatch: DRE-5843 — deferred — one dispatch per pass, read next pass
    proof-dispatch: DRE-5412 — deferred — one dispatch per pass, read next pass
    proof-dispatch: eligible 1, dispatched 1, waiting on release 0, held 0, someone else's 0, running 0, deferred 2, refused 12 (live)

The other twelve lines refuse cards whose `repo:` label names agent-bureau or portico. The receipt's `16:37 PT` is the minute the phase started; the comment itself is stamped 16:38:15.

**No person started it.** The sweep's event is `schedule`. GitHub's `actor` field on every scheduled run here reads `smeed652`, the same on the 05:37 dry pass as on the live one. GitHub fills that field for scheduled runs from the workflow's last editor; it is not a start. The `Proof Task` run's event is `repository_dispatch`, from `agent-bureau-bot[bot]`. The one operator act is the variable. It was empty on the 16:24:48 pass and `true` on the 16:36:14 pass. The read-only token holds no `variables` permission, so the moment it was set is not read here.

**Order.** The sweep reads `Green Light` returns first, then `Hand-work` first runs oldest first, then `In Review` re-runs. This card entered `Hand-work` at 05:24:29. DRE-5843, the other bureau-pipeline proof in `Hand-work`, entered it at 12:26:06, so it was correctly behind this card. DRE-5412 sits in `In Review`, the re-run lane, which is read last. So no bureau-pipeline proof older than this one was waiting for a first run, and the "older proof served first" branch was not observed.

## 2. The thread during the dry passes

Read off Linear at 16:40. These are DRE-5932's comments in order:

- the `🧭 routing-verdict: OPERATOR` at 2026-10-05 18:07;
- `🧹 Auto-promoted Backlog → Hand-work` at 05:24:30;
- `🚨 unlanded-work-watchdog no branch` at 08:26:16;
- `🔬 proof-run: …` at 16:38:15;
- `🧠 model-attempt` at 16:39:00;
- this run's heartbeats.

No `🔬 proof-run` comment exists before 16:38:15, so the dry passes wrote nothing to the card.

## 3. The release gate

- **The newest sibling merge:** DRE-5931's pull request #756 merged at 05:23:43, as `afc87ee`. The other siblings and their pull requests, all merged earlier on 2026-10-06 UTC: DRE-5919 #742, DRE-5921 #746, DRE-5922 #747, DRE-5923 #748, DRE-5924 #749, DRE-5925 #753, DRE-5926 #754, DRE-5927 #755. All nine blockers read `Done` in Linear.
- **`stable` carried it at 05:33:37.** Promote Channel [37464051768](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37464051768) logged `harness-passed-promoting: promoting stable to afc87ee… — harness green, strictly ahead.` and `pipeline-channel: state=current behind=0 … tag=afc87ee head=afc87ee`.
- **No pass read `waiting`.** The card entered `Hand-work` at 05:24:29. The only Reconcile run between then and 05:33:37 was the relay-scoped 05:24:37 pass, which skips the phase. So every pass that read this card read the release `ready`.
- **The fallback check, as the card asks.** Both readings, quoted in the table, were taken at 16:43 against the live `stable` tag at `1c4ea91`. The `waiting` sha is open pull request #770's head, `e1bd934`. It is on no tag, and it changes `scripts/` and `tests/` files.
- **The record branch tip does not qualify.** The card names it as a sha that would read `waiting`. But `pipeline-channel` declares no `ignore`, so `release_train.DOCUMENTATION_IGNORE` (`**/*.md`, `**/docs/**`) applies. A docs-only record reads `ready — … untouched`, so #770 was used instead. Run at 16:47:05 against this record's first commit: `proof-release: ready — pipeline-channel (the whole repository): untouched — #771 (329aa66) for DRE-5932 changed nothing under the repository that it does not ignore`. That is although `compare/stable...329aa66` answered `ahead`.

## 4. Heartbeats

`⏳ 1/5 read` 16:39:31 · `⏳ 2/5 observing` 16:40:33 · `⏳ 3/5 record written` 16:46:25 · `⏳ 4/5 local checks` 16:46:31 · `⏳ 5/5 PR opened` 16:46:46. A second `⏳ 5/5` follows this amended push.

## 5. The lanes

DRE-5932's history holds one lane move: `Backlog → Hand-work`, at 05:24:29, by Agent-Bureau. It read `Hand-work` when this run read it at 16:40.

The second move is `Hand-work → In Review` at 16:48:36, by Agent-Bureau. It was made by QA Review run 37548410452 (`pull_request` `opened` on #771, created 16:46:43). That run's step `Card → In Review (a pull request opened outside the dispatch)` started at 16:48:35 and concluded `success`. The card's thread, read right after, carries no separate comment receipt for that move. The history entry and the run's step are the record of it.

The moves to `Green Light`, back to `Hand-work`, and `In Review` again come after this run and are read off the same history.

## 6. The guard rails

- **At most one dispatch per pass.** 43 scheduled passes, and every tally reads `dispatched 0` or `dispatched 1`. Only the 16:36 pass was live.
- **Relay-scoped passes ran no phase.** The 14 `repository_dispatch` passes all show `Dispatch proof runs` `skipped`, and none prints a `proof-dispatch:` line: 37460417710 (05:02), 37463035753 (05:24), 37489335233 (08:39), 37494339305 (09:16), 37498496019 (09:47), 37499000793 (09:51), 37508815196 (11:07), 37509766264 (11:14), 37515792821 (12:01), 37518940112 (12:26), 37524242401 (13:08), 37540924385 (15:29), 37545231985 (16:12), 37545575842 (16:15).
- **No person's proof was in play.** DRE-5412 (pull request #713 on `agent/DRE-5412-hygiene-proof`) merged on 2026-10-05 at 18:23:27 PT and has sat in `In Review` since 18:06:43 PT that day. In the window it was never read, only `deferred — one dispatch per pass` behind this card. A Linear search for `being observed by hand` since 2026-09-25 finds no such hold.
- **No dead run.** No log in the window carries `proof-run-state: dead`.
