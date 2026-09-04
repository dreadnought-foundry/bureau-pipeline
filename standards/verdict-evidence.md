# Verdict-evidence standard — a claim about a run carries the run

The estate already has this law: **capture the error body, not the status.**
It has only ever been applied to clients. A verdict claiming *"I ran X and got
Y"* is the same shape and carries the same obligation.

Origin (DRE-3005, 2026-09-02/03): two pull requests in two repos were blocked
overnight by critic findings that are provably false, and both assert
something about a **run**.

- agent-bureau #2247 — *"I ran `check_tdd_commits.py` against the current head
  and it exits 1."* Run independently at the same ref against the same head, it
  exits 0. The tell is the omission: that script prints a per-commit
  classification line for every commit **unconditionally**, beside the reason,
  and the verdict reproduced the failure string verbatim and none of the
  listing. ~22h blocked, one fix-loop attempt spent, one operator decision.
- portico #407 — *"this PR's own CI checks never ran the new spec through that
  job."* Job `100550113617` in run `33724409256` ran it: **194 passed**. One API
  read away. ~10h blocked.

Both pull requests were correct the entire time. The critic is the correctness
backstop that lets agents ship without a human reading diffs — a backstop that
can assert an unrun command's output blocks correct work, spends fix-loop
attempts, and costs an operator decision each time.

## The three rules

1. **A verdict that cites a command includes that command's actual output.**
   Enough of it to be re-run and compared — the command **and** its result in
   one fenced block, not a quoted fragment. A fence carrying the command alone
   proves nothing; a fence carrying only the failure string is #2247 exactly.
   If the command prints a listing, a summary line or a count beside its
   verdict, that goes in too: what a real run always prints is what makes the
   claim checkable.
2. **A finding about a CI job's coverage cites the job** — the **run id**, the
   **job id**, and the line proving what it ran. Say "job X in run Y ran Z" with
   the proving line pasted, or do not make the claim. A `pull_request` run
   executes from the merge ref, so the base branch's jobs apply even when the
   branch's own workflow file predates them; "the branch's config doesn't have
   it" is not evidence about what ran.
3. **State the snapshot you reviewed.** A review takes minutes and a
   description can be corrected inside them. Re-read the pull request body
   immediately before writing the verdict, or state the moment you read it. The
   pipeline stamps that moment on the verdict comment and flags an edit that
   landed after it — a stale snapshot silently disputing a corrected body is a
   race with no signal, and #407 demanded a correction that had been in the body
   for five minutes.

## Scope — what this does NOT touch

- **The critic's authority on judgement is untouched.** Scope, design and risk
  findings are where the critic is meant to be believed, and no evidence
  citation is asked of them. This is narrowly about **factual claims a command
  can settle**.
- **Only a blocking verdict is gated.** The harm is a verdict that blocks
  correct work. An APPROVE blocks nothing.
- **A claim you cannot evidence is a claim you do not make.** "I could not run
  it" is a legitimate, publishable finding — say that instead, and say what
  evidence would settle it. Softening an unevidenced assertion into a question
  costs one review round; asserting it costs a day.

## Enforcement

`scripts/verdict_evidence.py` reads the finished verdict beside
`check_critic_result.py`. A blocking verdict whose findings assert a run
without carrying it is **not a real verdict**: the review retries once, and a
second failure posts a neutral hold naming the unevidenced claim. That hold
carries the `QA Critic` marker (so it supersedes a stale APPROVE) and no
`VERDICT:` line (so merge is held and the fix agent is not woken to fix
findings nobody proved), and the job goes red for the medic. It can only ever
hold a merge, never grant one.

## Fix, verifier and medic agents

The same obligation runs the other way. A reproduction that disputes a
finding — the shape that caught both incidents — is believed on the same terms
it demands: paste the command and its output, cite the run and job ids. An
agent's `🛑` refusal to push against a finding it can disprove is **correct
behaviour**; evidence is what makes it actionable rather than an opinion.

See also: `standards/console-honesty.md` (unknown is rendered as unknown, never
as the last known value — rule 3 is that rule applied to a review's own read).
