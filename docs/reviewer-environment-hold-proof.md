# An environment crash held after one retry, with its cause named — the proof record

The proof for [DRE-3432](https://linear.app/dreadnoughtfoundry/issue/DRE-3432),
from epic [DRE-3421](https://linear.app/dreadnoughtfoundry/issue/DRE-3421).
Every time below is Pacific (PDT, UTC−7).

**Where this stands.** The card asks for two observations. **Observation 1, the
offline replay of pull request #2370, was made on 2026-09-20 between 12:38 and
12:55 PT and is recorded in full below.** It covers the first acceptance
criterion. **Observation 2, the live dead-credential run on the sandbox
repository, has NOT been made yet.** It covers criteria 2, 3 and 4. It is
scheduled for a weekend window, because setting the review credential to a dead
value on purpose trips the DRE-4219 CRITICAL credential alarm, and that alarm
must not fire during a working day. Section 2 says what is owed, and the
checklist at the end marks each criterion.

**The replay script is committed beside this record** at
[`docs/evidence/DRE-3432/replay_2370.py`](evidence/DRE-3432/replay_2370.py).
**The captured fixture it reads is not committed.** This repository is public,
and the fixture is 31 files of raw material from the private
`dreadnought-foundry/agent-bureau` repository and the private Linear workspace:
three full CI job logs, the whole comment thread of #2370, agent-bureau's run
records for the window, and card DRE-3388's comments. It carries no credential
(every secret in the logs is masked `***` by GitHub), but it is private-repo
content. It stays on the operator's machine, and the table in §1 lists every
file in it and the command each was captured with, so an operator token can
capture it again.

---

## 1. Observation 1 — the replay of pull request #2370

*Made offline on the operator's machine. Nothing was written to GitHub, Linear or AWS.*

### What this shows, in plain English

On the afternoon of 2026-09-08 a vendor update left our build machines unable to start Claude, and the code reviewer for pull request #2370 crashed three times in 33 minutes. The pipeline of that day retried once, then told the operator to "check the critic's auth/token" — the one thing that was not broken — and a person re-ran the review by hand into the same wall. We took the real record of that afternoon and played it back, offline, through the pipeline the fleet runs today. **The criterion holds.** Today's pipeline retries once, and at the very next sweep after the second identical crash it posts exactly one hold notice that names the real cause and the one command that confirms it. On the three sweeps after that it starts nothing and posts nothing. Nothing was written to GitHub, Linear or AWS to show this.

### What was captured, and when

Captured read-only on **2026-09-20 between 12:38 and 12:55 PT** (file times on the fixture) with an operator login that can read `dreadnought-foundry/agent-bureau`.

| What | Source | File under `fixture/` |
|---|---|---|
| #2370's comment thread, 11 comments, in the shape the sweep reads | `gh pr view 2370 --json …comments` (and the REST copy) | `pr-graphql-comments.json`, `comments.json`, `pr.json` |
| Check runs at the crashed head `81bcc242`, every attempt | `gh api …/commits/81bcc242…/check-runs?filter=all` | `check-runs-81bcc242-filter-all.json`, `check-runs-81bcc242.json`, `check-run-102264839064.json` |
| The three crashed review jobs, attempt by attempt, **with their full logs** | `gh api …/actions/runs/<id>/attempts/<n>[/jobs]`, `…/jobs/<id>/logs` | `qa-review-run-*.json`, `job-*.log` |
| Every hand- or sweep-dispatched review run in the window, and every review run from 14:00 PT | same | `dispatch-run-*.json`, `qa-review-runs-window.json`, `qa-review-runs-before-window.json` |
| When the reconcile sweep and the medic really ran | `gh api …/workflows/{reconcile,medic}.yml/runs` | `reconcile-runs-window.json`, `medic-runs-window.json`, `medic-run-34287882336*.json` |
| Card DRE-3388's comments, 15:36–16:30 PT, verbatim | Linear, read-only | `linear-DRE-3388-comments-window.json` |

**Pipeline driven:** bureau-pipeline at the `stable` tag, commit **`fd992006add7639cd5465ac947a3bb15dbaf5807`**, fetched as a tarball from GitHub — not a local clone.

#### The three identical crashes

All three died at the step `Fail if critic never really ran`. The pipeline's own detector (`reviewer_environment.detect`) and the medic's own classifier (`medic_classify.classify`), run over each real log, give the same answer three times: **`native-binary-missing`**, class **`environment_crash`** — "Claude Code native binary not found at /home/runner/.local/bin/claude".

| # | Failed at | Run / attempt | Job (check run) | Started by |
|---|---|---|---|---|
| 1 | 15:39:41 PT | 34286800645 / 1 (the pull request's own review, started 15:36:56 PT) | 102264152565 | opening the PR |
| 2 | 15:49:59 PT | 34287604200 / 1 | 102266703991 | **the sweep's one automatic retry** (its receipt is comment 5592929812, 15:47:08 PT) |
| 3 | 16:11:58 PT | 34287604200 / 2 | 102272082240 | **a person** (`smeed652` pressed re-run at 16:09 PT) |

#### Where the history differs from what the card assumed

- **The third crash was a hand re-run, not the pipeline.** The sweep had already stopped.
- **What the old sweep really did at 16:04 PT** was post `reviewer-down` on the card ("check the critic's auth/token"). That is the behaviour this epic replaces.
- **The medic left one note, not two — and today's medic would also leave exactly one.** It wrote its old "infrastructure rate-limit" sentence at 15:40:17 PT after crash 1. Crashes 2 and 3 were *dispatched* review runs, and GitHub records a dispatched run against the default branch: run 34287604200 carries branch `main` and commit `06a336ea`, not the pull request's `81bcc242`. The medic reads the card out of the branch name (or a `bureau-card:` line in the log, which these logs do not carry — checked with `medic_retry.card_for_run`), so for crashes 2 and 3 it has no card to write to; the real medic run that fired 32 seconds after crash 2 (34287882336) was skipped outright. So the replay adds **one** evidence note, where the real one stood. The unit test `TestTheIncidentReplay` adds a second note after the retry crashes; the history says that note never arrives. **The proof of "the second identical crash" is therefore the spent retry receipt plus crash 1's note — never a second note.** The sweep already works this way (it asks only whether a note names the cause for this commit), which is why the hold still fires.
- The crashed head was **not force-pushed**. It was superseded by an ordinary fix commit at 17:45 PT; the PR merged at 17:54 PT.

### The sweeps

The clock is the real one: the five scheduled `reconcile.yml` runs from 15:46 to 16:46 PT. The first is the retry; **the criterion's four are sweeps 1–4 below.**

| Sweep | Simulated time (real reconcile run) | Receipt the sweep posted | Dispatched? |
|---|---|---|---|
| prelude | 15:46:23 PT (34287547636) — after crash 1 | the re-dispatch receipt: `🔁 crashed-review-redispatch @81bcc242…` | **Yes** — `gh workflow run qa-review.yml --repo dreadnought-foundry/agent-bureau -f pr_number=2370`. This is the run that became crash 2. |
| **1** | 16:04:04 PT (34288926295) — first sweep after crash 2 | **the hold**, once on the PR and once on the card (quoted below) | **No** |
| **2** | 16:20:46 PT (34290216506) — after crash 3 | none | **No** |
| **3** | 16:31:54 PT (34291059495) | none | **No** |
| **4** | 16:46:38 PT (34292139189) | none | **No** |

On sweeps 2–4 the sweep's log line reads: *"PR #2370 head 81bcc242 — HELD: this runner cannot run Claude and nothing has been re-dispatched. Waiting for a critic verdict in this repository or the re-run act on the pull request."*

The hold receipt, verbatim:

> 🛑 runner-environment-hold @81bcc242cc03da6c6f90b864252dca81be9783cc: reviewer cannot run on this runner — native-binary-missing: the vendor action installed Claude Code but left no launcher on this runner, so nothing here can start Claude — twice on 81bcc242; holding, nothing re-dispatched. Check: grep -n 'claude-code-action@' .github/workflows/*.yml in bureau-pipeline, then compare the pinned SHA with anthropics/claude-code-action#1817 (DRE-3416 pinned v1.0.217; DRE-3417 unpins).
>
> What failed is the runner's environment, not the work: no verdict was written, nothing in this pull request was rejected, and nothing has been re-dispatched.
>
> **How this is released:** the first critic verdict posted in this repository after this hold, or a comment on the held pull request whose whole body is `▶️ re-run the review`, re-dispatches the held review once. Until one of those happens, nothing else is coming.
>
> 📎 pipeline-act: reviewer-environment-hold · kind: hold · state: unchanged · next: operator · discharges: review-retried-after-crash · subscriber: reconcile.yml · tag: runner-environment-hold

The replay was driven two ways and both give the table above: **A** — all five sweeps, with the replay's own 15:46 receipt fed forward; **B** — sweeps 1–4 only, over the real thread including the real 15:47 receipt.

#### Two checks that the replay is faithful

- **The prelude matches reality byte for byte.** The re-dispatch receipt the replayed 15:46 sweep wrote is identical to the comment the real sweep posted at 15:47:08 PT.
- **Control — take the evidence note away and the replay does what really happened.** Fed the card as it really was, the same driver at 16:04 PT dispatches nothing and posts `reviewer-down`, byte-identical to the real Linear comment of 16:04:47 PT. So the hold is caused by the note, not by the driver.

### What is real and what is synthesized

**Real (captured):** the comment thread and every timestamp on it; the head sha; the three logs and the signature read from them; every run, attempt and job record; the sweep clock; the card's comments; the pipeline code, unmodified.

**Generated by the pipeline's own writer:** the medic's evidence note — `reviewer_environment.evidence_note()` given the signature the real detector read from crash 1's real log, the real head sha and the real run URL. These are the three things `medic.yml` passes it. It stands where the medic's real (old) note stood, at 15:40:17 PT.

**Captured, then read as of each sweep's moment:** the "review checks at this commit" answer. GitHub's `filter=all` listing still holds every attempt — attempt 1's crashed `call / review` (102264152565) beside attempt 2's later success — so each sweep is given the newest check per name that had started by then, which is what the sweep's default listing showed. One field is not recoverable: the head-bound `QA critic review` check (102264839064) is a single record edited in place; it was created at crash 1 and today carries the 17:26 PT verdict's text. Its conclusion at the time is taken as `failure` — the real 15:46 PT sweep re-dispatched, which it only does on a crashed check. The "is a dispatched review still running" answer is likewise each captured attempt's start and end read at the sweep's time; none was running at any of the five.

**Synthesized or narrowed:** `mergeStateStatus: CLEAN` and `isDraft: false` (not recoverable for a merged PR); the open-PR listing holds #2370 alone (safe here: of every `qa-review` run in agent-bureau created from 14:00 PT on, none finished green between 15:20 PT and the last sweep at 16:46 PT — the first to do so, run 34285500814, finished at 17:01 PT — so no verdict that could release a hold existed in the window); the replay's own writes are stamped 45 seconds after the sweep's start; two real comments that are the *old* sweep's output are left out of what the new sweep is fed — the 16:04 `reviewer-down` report always, and the 15:47 receipt in variant A only.

**Seam:** the one `tests/test_reviewer_environment_sweep.py` uses — `subprocess.run` replaced in both `reconcile` and `reviewer_environment`, and `linear_ops.comment_bodies` / `cmd_comment` replaced with an in-memory card. `tests/test_crashed_review_recovery.py`'s own fake patches only `reconcile`, which would have let the hold's PR comment reach the real #2370, so it was not used as-is. The real `_nudge` runs, so the dispatch is recorded as the literal `gh workflow run …` call. An unrecognised `gh` call raises, and sockets are refused while a sweep runs.

### Verdict against the criterion

> *After the second identical crash the sweep posted exactly one hold receipt and, over the next three sweeps, dispatched nothing.*

**Holds.** One hold receipt on the pull request (mirrored once to the card) at sweep 1; zero dispatches on sweep 1; zero dispatches and zero receipts on sweeps 2, 3 and 4. No write or read failures were recorded on any sweep.

The pipeline's own tests at the same commit, Python 3.12, fresh virtualenv from `requirements-dev.txt`: `tests/test_crashed_review_recovery.py` **25 passed**; `tests/test_reviewer_environment_sweep.py` **33 passed**; `tests/test_reviewer_environment.py` **72 passed**.

**A design fact this replay surfaced (not part of this criterion):** `medic.yml`'s own "held after the second death" job cannot fire for this incident's shape. It needs a second *attempt* of a run whose branch names the card, and the sweep's retry is a fresh dispatched run on `main`. For a crashed review, the sweep's hold — the one shown here — is the only hold there is.

### How to re-run it

The script is [`docs/evidence/DRE-3432/replay_2370.py`](evidence/DRE-3432/replay_2370.py).
It expects this directory layout around it, which is the operator's proof
directory (`proof-3432/`), not this repository:

- `fixture/` — the 31 captured files listed in the table above. They are not
  committed; see the top of this record. Re-capture them read-only with an
  operator token that can read `dreadnought-foundry/agent-bureau`, using the
  commands in that table.
- `stable-ref.json` — the answer to
  `gh api repos/dreadnought-foundry/bureau-pipeline/git/ref/tags/stable` at
  replay time. The script reads only `object.sha` from it, and the replay's
  value was `fd992006add7639cd5465ac947a3bb15dbaf5807`. The tag has moved
  since, so write that sha by hand rather than fetching the tag today.
- `bp/` — bureau-pipeline at that commit:
  `gh api repos/dreadnought-foundry/bureau-pipeline/tarball/fd992006add7639cd5465ac947a3bb15dbaf5807 > bp.tgz && mkdir bp && tar -xzf bp.tgz -C bp --strip-components=1`
- `venv/` — `python3.12 -m venv venv && ./venv/bin/pip install -r bp/requirements-dev.txt`

Then:

```
GH_TOKEN=x ./venv/bin/python replay_2370.py --json replay-result.json
```

Exit code 0 means the criterion held in both variants. The full per-sweep
record, including each sweep's log lines, lands in `replay-result.json`. The
saved run of 2026-09-20 printed the per-sweep tables above, `CRITERION (A) …
HOLDS`, `CRITERION (B) … HOLDS`, `FIDELITY … True` and `CONTROL … True`. The
script sets a dummy `GH_TOKEN` itself and cannot reach the network.

---

## 2. Observation 2 — the live run on the sandbox repository: NOT YET OBSERVED

Nothing in this section has happened yet. It is owed, and it is planned for a
weekend window because the dead credential it needs trips the DRE-4219
CRITICAL alarm.

What the card asks to be watched by hand on `agent-bureau-demo`:

1. Set the review credential to a dead value by hand. Open a throwaway agent
   pull request.
2. The critic crashes. The medic leaves one evidence note naming
   `credential-refused` and `make cred-doctor`. The sweep re-dispatches once,
   and the re-dispatch crashes.
3. The next sweep posts one hold, on the pull request and on the card. It
   dispatches nothing on at least two sweeps after that.
4. Restore the credential. Open a second throwaway pull request so that a real
   verdict posts in that repository.
5. The next sweep re-dispatches the held head exactly once, with no hand
   dispatch, and the verdict lands.
6. The console shows the held card as held, with the reason named, and never as
   the fix agent working. A screenshot is linked from this record.

Owed, to be written here once observed: the run ids, the receipt timestamps
(PT), and the console screenshot.

---

## Acceptance criteria, as they stand on 2026-09-21

- [x] **1. The replay of #2370's history is recorded.** After the second
  identical crash the sweep posted exactly one hold receipt, and over the next
  three sweeps it dispatched nothing. The four sweeps are the real
  `reconcile.yml` runs 34288926295 (16:04:04 PT), 34290216506 (16:20:46 PT),
  34291059495 (16:31:54 PT) and 34292139189 (16:46:38 PT), after the prelude
  retry at 34287547636 (15:46:23 PT). See §1.
- [ ] **2. The dead credential, observed live on the sandbox repository.**
  NOT YET OBSERVED. Owed for the weekend window. See §2.
- [ ] **3. Release after the credential is restored, observed live.** NOT YET
  OBSERVED. Owed for the weekend window. See §2.
- [ ] **4. The console rendering of the held card, with a screenshot.** NOT YET
  OBSERVED. Owed for the weekend window. See §2.
- [ ] **5. The record is on `main` with the run ids and timestamps of the four
  observations.** PARTIAL. Once this pull request merges, the record is on
  `main` at this path with observation 1's run ids and timestamps. The run ids
  and timestamps for criteria 2–4 are still owed and will be added here after
  the weekend run.
