# PROOF record — DRE-7756: PROOF: a held repo's plan is refused without the hold named, and DRE-7600 gets one repo-held notice with its children shown held (epic DRE-7671)

**Status: PASS.**

All six judged rows were observed and met against real state: agent-bureau-demo's live reconcile stub (held at `max_wip: "0"`), DRE-7600's thread and its three children, and the demo's reconcile runs. The two parts the card declares offline-only (lifting the hold when the cap is restored, and the Intake-hold reading) were not run here and are not marked observed.

## How this was recorded

- **The run:** `Proof Task` [39611738066](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/39611738066), attempt 1. It is the shadow run on `claude-sonnet-5-5` (DRE-7286): it observed every row as the real run would and wrote only this file.
- **The commit and release observed:** the run's checkout is `da2fa6b4ebe71ac2a453316f635c155b5cc04686` (Merge pull request #1201, DRE-7755, merged 2026-10-21 05:33 PT). `git ls-remote --tags origin stable` read `da2fa6b4…` at 06:54 PT (a local `git rev-parse stable` read the same at 06:53 PT), so `stable`, the channel the demo's stub calls (`reconcile.yml@stable`, `pipeline_ref: stable`), is this commit. It carries the merges of DRE-7750 (#1197), DRE-7752 (#1198), DRE-7754 (#1199, with `repo_held_epics` in `9608051a`), DRE-7753 (#1200) and DRE-7755 (#1201). DRE-7751 is not named in any commit subject or pull request title in this repo; `linear_ops.py state-of DRE-7751` read `Done`, and its merge itself was not located.
- **When:** every reading was made between 06:52 PT and 06:55 PT on 2026-10-21. Run times below are GitHub's UTC stamps converted to PT.
- **Scratch workspace:** `/tmp/scratch`, holding only `.github/workflows/reconcile.yml`, the demo's stub as it stands on the demo's default branch `main` (blob last changed 2026-10-02 19:06 PT, commit `88f6e50d`), read with `GH_TOKEN=$GH_READ_TOKEN gh api repos/dreadnought-foundry/agent-bureau-demo/contents/.github/workflows/reconcile.yml -H "Accept: application/vnd.github.raw"`.
- **Not run here:** lifting the hold when the cap is restored (restoring the demo's cap is the CEO's decision, so rows 4–6 read the demo still held) and the Intake-hold reading (the proof identity holds no `variables: read`). The offline tests of DRE-7750, DRE-7754 and DRE-7755 cover those.
- **Local run:** `PROOF_LOCAL_STATUS=none`. No criterion on this card is a `Local screen:` row.

## Identities

- **GitHub, observed:** `GH_READ_TOKEN`. The run's step summary line: `proof identity: github read — app agent-bureau-bot-3, owner dreadnought-foundry, permissions contents:read actions:read pull-requests:read metadata:read (variables:read not granted to this installation)`. Used for the stub read (`gh api …/contents/…`), the workflow listing (`gh api …/contents/.github/workflows`) and `gh run list`, all on `dreadnought-foundry/agent-bureau-demo`, and for the refused write below.
- **The one refused write:** `GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs -f ref=refs/heads/proof-write-probe-39611738066 -f sha=da2fa6b4ebe71ac2a453316f635c155b5cc04686` answered `{"message":"Resource not accessible by integration", … "status":"403"}`. No ref was created.
- **GitHub, written:** `GH_TOKEN`, not used. This is a shadow run: it pushes nothing and opens no pull request.
- **Linear:** `LINEAR_API_KEY`, read only (`BUREAU_LINEAR_READ_ONLY=1`), through `linear_ops.py` and `spoken_thread.py`: `state-of` DRE-7600 and DRE-7751, `dump-comments` DRE-7600 (twice) and DRE-7601, DRE-7602, DRE-7603, `children-json` DRE-7600, and `spoken_thread.py thread` DRE-7600 (read for the notice's time). No Linear write was made. linear requests: 9 of 40.
- **AWS:** none. No role was provided and no row needs one.

## Readings

### Row 1 — the demo's stub, read twice

The two commands, run in `/home/runner/work/bureau-pipeline/bureau-pipeline` on `da2fa6b4`, each with `BUREAU_MAX_WIP` and `INTAKE_HOLD` unset:

`env -u BUREAU_MAX_WIP -u INTAKE_HOLD -u MAX_WIP python3 scripts/sweep_holds.py read --workspace /tmp/scratch`

```json
{"cap": 0, "cap_source": "the caller's reconcile stub .github/workflows/reconcile.yml", "stub_path": ".github/workflows/reconcile.yml", "stub_comment": "HELD at 0 on 2026-09-08 by CEO decision: no card in this repo is promoted to a build until Bureau (agent-bureau, bureau-pipeline) is solid. Cards still enter Planning and get classified; they wait in Backlog with their verdict, and every sweep prints \"WIP at cap (n/0)\". Was \"12\". Restore it to resume building here.", "intake_hold": null, "max_wip_held": true, "intake_held": false, "held": true}
```

`env -u BUREAU_MAX_WIP -u INTAKE_HOLD MAX_WIP=0 python3 scripts/sweep_holds.py read --workspace /tmp/scratch`

```json
{"cap": 0, "cap_source": "this run's max_wip input", "stub_path": ".github/workflows/reconcile.yml", "stub_comment": "HELD at 0 on 2026-09-08 by CEO decision: no card in this repo is promoted to a build until Bureau (agent-bureau, bureau-pipeline) is solid. Cards still enter Planning and get classified; they wait in Backlog with their verdict, and every sweep prints \"WIP at cap (n/0)\". Was \"12\". Restore it to resume building here.", "intake_hold": null, "max_wip_held": true, "intake_held": false, "held": true}
```

Where the comment sits in the stub: it is four comment lines inside the job `call`'s `with:` block, directly above the cap's own line `max_wip: "0"`, with no blank line between the comment and the cap line (nor between the comment and the `pipeline_ref: stable` line above it). It is not above the job key, and not above the `uses:` line. A separate comment block about `INTAKE_HOLD` follows the cap line, with its own `intake_hold: ${{ vars.INTAKE_HOLD }}` line.

### Row 2 — the held plan, refused and then named

Both calls ran over the same scratch workspace with `MAX_WIP=0` set (`BUREAU_MAX_WIP` and `INTAKE_HOLD` unset): `python3 scripts/plan_held_repo.py findings --artifact <plan> --workspace /tmp/scratch --structural-file <file> --note-file <file>`, one empty structural file and one empty note file per call.

- **Artifact 1** (`/tmp/proof7756/plan1.md`): a heading, one paragraph asking the CEO to approve the epic, and one paragraph opening `Step that the plan names is a different sentence…`. No paragraph opens `Step that needs the CEO:`. The call wrote one line to its structural file, and exactly one:

  > held-repo: this plan's repo is held — max_wip "0" in .github/workflows/reconcile.yml, whose comment says: "HELD at 0 on 2026-09-08 by CEO decision: no card in this repo is promoted to a build until Bureau (agent-bureau, bureau-pipeline) is solid. Cards still enter Planning and get classified; they wait in Backlog with their verdict, and every sweep prints "WIP at cap (n/0)". Was "12". Restore it to resume building here." — the plan names no "Step that needs the CEO:" paragraph for lifting it (names max_wip and quotes the stub's comment); write one under ## Risk assessment and repeat it in the plan comment.

  Its note file said: `Repo holds: max_wip "0" (this run's max_wip input) — NOT named as a step that needs the CEO; Intake open — read for this plan's own repo only`.
- **Artifact 2** (`/tmp/proof7756/plan2.md`): artifact 1 plus the paragraph `Step that needs the CEO: lift the hold on this repo by restoring max_wip in its reconcile stub. The stub reads: "HELD at 0 on 2026-09-08 by CEO decision: no card in this repo is promoted to a build until Bureau (agent-bureau, bureau-pipeline) is solid. Cards still enter Planning and get classified; they wait in Backlog with their verdict, and every sweep prints "WIP at cap (n/0)". Was "12". Restore it to resume building here."` The call wrote nothing to its structural file (0 bytes). Its note file said: `Repo holds: max_wip "0" (this run's max_wip input) — named as a step that needs the CEO; Intake open — read for this plan's own repo only`.

### Row 3 — bureau-pipeline, unheld

The checkout is bureau-pipeline at `da2fa6b4` (the released commit). `sweep_holds.py read --workspace <checkout>` with `MAX_WIP`, `BUREAU_MAX_WIP` and `INTAKE_HOLD` unset read `{"cap": 8, "cap_source": "the default 8 — the caller's reconcile stub .github/workflows/self-reconcile.yml names no max_wip number, and no BUREAU_MAX_WIP variable names a number", "stub_path": null, "stub_comment": "", "intake_hold": null, "max_wip_held": false, "intake_held": false, "held": false}`. The same two artifacts were run through `plan_held_repo.py findings` against it. Both structural files stayed at 0 bytes. Both calls' note files read:

> Repo holds: none — max_wip 8 (the default 8 — the caller's reconcile stub .github/workflows/self-reconcile.yml names no max_wip number, and no BUREAU_MAX_WIP variable names a number); Intake open — read for this plan's own repo only

### Rows 4 and 5 — DRE-7600's notice, and the sweeps after it

`linear_ops.py state-of DRE-7600` read `In Progress`. `spoken_thread.py thread DRE-7600` heads the notice `the pipeline, at 2026-10-21 06:41 PT` (comment 27 of 27, and the newest; the one before it is from 2026-10-19 09:55 PT). The reconcile run 39610103947 started 06:40:51 PT and completed 06:42:19 PT, which brackets the notice's minute; the run log was not read, so the record does not say that this run posted it.

The notice, from `dump-comments DRE-7600` (the one comment carrying `pipeline-act: repo-held-notice … tag: repo-held`):

> ⏸️ repo-held: DRE-7600 is approved, and agent-bureau-demo builds nothing — its WIP cap is 0.
>
> The cap is set in `.github/workflows/reconcile.yml`, and the stub says why:
>
> > HELD at 0 on 2026-09-08 by CEO decision: no card in this repo is promoted to a build until Bureau (agent-bureau, bureau-pipeline) is solid. Cards still enter Planning and get classified; they wait in Backlog with their verdict, and every sweep prints "WIP at cap (n/0)". Was "12". Restore it to resume building here.
>
> Every Queued child of DRE-7600 waits on the cap:
>
> - DRE-7601 — held (wip-cap-held)
> - DRE-7602 — held (operator-step)
> - DRE-7603 — held (wip-cap-held)
>
> What lifts it: restoring `max_wip` above 0 in `.github/workflows/reconcile.yml`. The next sweep then lifts every `wip-cap-held` hold listed here, and each child waits on the dependency gate as before. A child held for another reason keeps that hold.
>
> 📎 pipeline-act: repo-held-notice · kind: hold · state: held · next: operator · discharges: nothing · subscriber: reconcile.yml · tag: repo-held

The notice was posted at 06:41 PT on 2026-10-21, 68 minutes after `da2fa6b4`'s merge commit (05:33 PT) and after every merge of this epic. The first `dump-comments DRE-7600` read, at 06:53 PT, returned 27 comments with exactly one carrying the `repo-held` act tag. Linear gives a comment's time only to the minute, so the release's own cut time is not read: the earlier reconcile runs at 05:50 PT and 06:16 PT in the table below left no notice, and the 06:41 PT one is the first thread entry after 2026-10-19.

`GH_TOKEN=$GH_READ_TOKEN gh run list --repo dreadnought-foundry/agent-bureau-demo --workflow reconcile.yml --limit 4 --json databaseId,status,conclusion,createdAt,updatedAt,event,url`, read at 06:54 PT (13:53:54Z), answered, newest first:

| Run | Event | Started (PT) | Completed (PT) | Conclusion |
|---|---|---|---|---|
| [39611801629](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/39611801629) | schedule | 2026-10-21 06:52:22 | 2026-10-21 06:53:34 | success |
| [39610103947](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/39610103947) | schedule | 2026-10-21 06:40:51 | 2026-10-21 06:42:19 | success |
| [39606555564](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/39606555564) | schedule | 2026-10-21 06:16:29 | 2026-10-21 06:17:42 | success |
| [39602822501](https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/39602822501) | schedule | 2026-10-21 05:50:17 | 2026-10-21 05:51:30 | success |

Two runs completed after the notice's 06:41 PT minute: 39610103947 (06:42:19 PT) and 39611801629 (06:53:34 PT). After them, `dump-comments DRE-7600` re-read at 06:54 PT returned the same 27 comments, still with exactly one carrying the `repo-held` act tag (`pipeline-act: repo-held-notice`).

### Row 6 — the children

`linear_ops.py children-json DRE-7600` read three children, all `Queued`, all labelled `repo:agent-bureau-demo`:

| Child | State | Labels | Newest hold stamp (`dump-comments`) | Routing verdict |
|---|---|---|---|---|
| DRE-7601 | Queued | `agent:engineer`, `needs-human` | `🔒 hold: reason=wip-cap-held at=none lifts=cap-restored by=scripts/repo_held_epics.py` | FLEET |
| DRE-7602 | Queued | `agent:devops`, `needs-human`, `no-code`, `operator-step` | `🔒 hold: reason=operator-step at=none lifts=blockers-terminal by=linear_ops.py` | OPERATOR |
| DRE-7603 | Queued | `agent:ops`, `needs-human`, `no-code` | `🔒 hold: reason=wip-cap-held at=none lifts=cap-restored by=scripts/repo_held_epics.py` | OPERATOR |

None of the three carries a PARKED verdict, so the three Queued children without one are DRE-7601, DRE-7602 and DRE-7603. The notice lists those same three, with the reasons `wip-cap-held`, `operator-step` and `wip-cap-held`, matching the stamps above one for one.

## Criterion table

| Criterion | Result |
|---|---|
| Held reading, live stub: the run copies agent-bureau-demo's reconcile stub from its default branch into a scratch workspace's `.github/workflows/`. It then runs `python3 scripts/sweep_holds.py read --workspace <scratch>` twice, with `BUREAU_MAX_WIP` and `INTAKE_HOLD` unset: once with `MAX_WIP` unset, and once with `MAX_WIP=0`, the way the reusable reconcile workflow hands the demo sweep its stub's own cap. The record quotes both JSON objects. Both show `cap` 0, `max_wip_held` true, `stub_path` the demo stub, and a `stub_comment` containing "HELD at 0 on 2026-09-08 by CEO decision", and the second shows `cap_source` "this run's max_wip input". The record also says in words where that comment sits in the stub (on the cap's own line, directly above it, above the job's with or uses line, or above the job key, and whether a blank line separates them), so the layout `stub_comment` was read from is on the record. | Met. Both objects are quoted under Row 1: `cap` 0, `max_wip_held` true, `stub_path` `.github/workflows/reconcile.yml`, and a `stub_comment` opening "HELD at 0 on 2026-09-08 by CEO decision" in each; the second reads `cap_source` "this run's max_wip input" (the first reads "the caller's reconcile stub .github/workflows/reconcile.yml"). The comment is four lines inside the job `call`'s `with:` block, directly above the `max_wip: "0"` line with no blank line between them. Read as `app agent-bureau-bot-3` from agent-bureau-demo's `main`, 2026-10-21 06:52 PT. |
| Held plan refused, live stub: over that same scratch workspace, with `MAX_WIP=0` set as a plan stub that passes the cap hands it in, the run calls `python3 scripts/plan_held_repo.py findings` twice and quotes what the two calls write, one structural file per call. The first artifact has no paragraph opening with the held-repo plan check's CEO-step opener, and its call writes exactly one held-repo finding citing max_wip "0" and quoting the stub comment. The second is the same artifact plus that paragraph, naming max_wip and quoting the stub comment read in the row above, and its call writes nothing. | Met. Call 1 (no `Step that needs the CEO:` paragraph) wrote exactly one `held-repo:` line to its structural file, citing `max_wip "0"` and quoting the stub's comment in full. Call 2 (the same artifact plus a `Step that needs the CEO:` paragraph naming `max_wip` and quoting that comment) wrote 0 bytes, and its note file said `named as a step that needs the CEO`. Both quoted under Row 2. |
| Unheld plan unchanged: the same two `findings` calls against a checkout of bureau-pipeline at the released commit (its stub names no max_wip, env unset) write nothing to either structural file. Both calls' note files say the repo holds none, quoted in the record. | Met. Against the checkout at `da2fa6b4`, both calls left their structural files at 0 bytes. Both note files read `Repo holds: none — max_wip 8 (the default 8 — the caller's reconcile stub .github/workflows/self-reconcile.yml names no max_wip number, and no BUREAU_MAX_WIP variable names a number); Intake open — read for this plan's own repo only`. |
| One notice, live: DRE-7600's comments, read with `linear_ops.py dump-comments DRE-7600`, carry exactly one receipt of the `repo-held` act, posted after the release carrying this epic went live. The record quotes it, naming WIP cap 0 and quoting the demo stub's comment. | Met. `dump-comments DRE-7600` returned 27 comments, exactly one with `pipeline-act: repo-held-notice … tag: repo-held`. It was posted 2026-10-21 06:41 PT, after `stable` carried `da2fa6b4` (merge commit 05:33 PT). It says "its WIP cap is 0" and quotes the stub's comment "HELD at 0 on 2026-09-08 by CEO decision: …". Quoted in full under Rows 4 and 5. |
| No repeat, live: the run reads agent-bureau-demo's reconcile workflow runs with `gh run list`, as the proof identity, and the record quotes at least two runs that completed after the notice's time. After those runs, DRE-7600 still carries exactly one receipt of the `repo-held` act, read with `linear_ops.py dump-comments DRE-7600`. | Met. `gh run list` as `GH_READ_TOKEN` (app `agent-bureau-bot-3`) listed runs 39610103947 (completed 06:42:19 PT) and 39611801629 (completed 06:53:34 PT), both `success`, both after the notice's 06:41 PT. `dump-comments DRE-7600` read again at 06:54 PT, after both: still 27 comments, still exactly one `repo-held` receipt. |
| Children held, live: the run reads DRE-7600's children with `linear_ops.py children-json DRE-7600` and each child's comments with `linear_ops.py dump-comments`. DRE-7601 and DRE-7603 both wear `needs-human` with a newest hold stamp naming the reason `wip-cap-held` (the hold reason; `repo-held` is the notice's act tag, DRE-7754). The notice lists every child that same read shows Queued with no PARKED verdict, each with the reason it is held, and the record quotes the notice's list beside the children read so the two can be compared. | Met. `children-json DRE-7600`: DRE-7601, DRE-7602 and DRE-7603, all Queued. DRE-7601 and DRE-7603 wear `needs-human`, each with its only hold stamp `🔒 hold: reason=wip-cap-held …`. DRE-7602 also wears `needs-human`, with `reason=operator-step`. No child carries a PARKED verdict (verdicts FLEET, OPERATOR, OPERATOR). The notice lists DRE-7601 (wip-cap-held), DRE-7602 (operator-step) and DRE-7603 (wip-cap-held), the same three; both lists are set side by side under Row 6. |
| The record ends `**Status: PASS.**` only when all six judged rows are `Met.`. The CEO reads the merged record and closes this card. | Met. All six judged rows above are `Met.`, and the status line reads `**Status: PASS.**`. |
| The CEO closes this card after reading the record | Open: the CEO's step |
