# Vendor-boundaries standard — the vendor-behavior premortem checklist

Every bug in the 2026-07-12 dependency-automation rollout lived at the GitHub
boundary, not in our logic. Unit TDD held on every fix — what failed was never
asking what the VENDOR actually does. This standard is the look-ahead
discipline: any card or PR touching an external trigger, event, webhook, or
vendor command answers the checklist BEFORE building. "It works in our tests"
proves our logic; it proves nothing about GitHub's actors, secrets stores,
retry semantics, or command limitations.

## The premortem checklist — answer all five before building

1. **Who is the initiating actor?** Which bot identity, human, or
   `github-actions` triggers each event this change touches? Do ALL
   `allowed_bots` lists and authorship gates on the reachable workflows admit
   it? (A `gh workflow run` dispatch initiates as `github-actions`, not as the
   App that minted the token; a bot's own push initiates as that bot.)
2. **Which secrets store does that event context get?** GitHub keys the store
   to the triggering actor: a dependabot-triggered `pull_request` run gets the
   SEPARATE Dependabot secrets store — for us EMPTY — plus a read-only token,
   and `secrets: inherit` passes nothing.
3. **What does the vendor actually do on retry / close / reopen / ignore /
   rebase / re-file** — and does our state machine survive EACH? Closing a
   Dependabot PR does not end it; ignoring one version invites the next; a
   rebase changes the head sha every marker was bound to.
4. **What are the command's limitations?** e.g. `@dependabot ignore this major
   version` suppresses only THAT major (an ignore walk-down re-files the next
   one down) and is single-dependency only — it does not work on grouped PRs.
5. **What happens when OUR run crashes mid-flow?** Do receipts/markers posted
   before the crash block recovery, or does the system retry — bounded, and
   classify-first so an infra crash is never retried blind?

Write the answers into the card/plan (or the PR body when the design happens
there). "Same as the existing X flow" is a valid answer only when X already
answers the question.

## The seed incidents — what GitHub actually did, and the question that would have caught it

The 2026-07-12 rollout (all live, all at the boundary):

- **DRE-2020** — the pool bots' very first authored PR crashed the critic:
  `allowed_bots` hardcoded `agent-bureau-bot`, and GitHub aborts a run
  "initiated by non-human actor: agent-bureau-bot-3". *Q1.*
- **DRE-2037** — the merge gate's update-branch push fired
  `pull_request: synchronize` AS `agent-bureau-qa-bot`, and the review it was
  forcing crashed on the same actor gate. *Q1.*
- **DRE-2039** — dependabot[bot]'s own opens/rebases fire `pull_request` as
  `dependabot`; no allowlist admitted it, so every dependency PR crashed the
  critic and the gate waited forever. *Q1.*
- **DRE-2053** — reconcile's `gh workflow run` dispatches carry the workflow's
  own `GITHUB_TOKEN`, so the dispatched run initiates as `github-actions` —
  all three paced review dispatches crashed identically. *Q1.*
- **The fleet repeat (v3)** — atlas/deltasolv rode the v3 release tag, which
  predated the `github-actions` actor and dependabot-secrets fixes; their
  reviews crashed the same gates that evening until v4 was cut. A boundary fix
  is live only where the release channel has delivered it. *Q1.*
- **DRE-2047 / DRE-2067** — dependabot-triggered `pull_request` runs got the
  empty Dependabot secrets store: the token mint died at required-secret
  validation with ZERO steps, first on this repo's stub, then again on every
  fleet stub calling the reusable with `secrets: inherit`. *Q2.*
- **DRE-2064** — closing critic-rejected majors with `@dependabot ignore this
  major version` made Dependabot immediately re-file each at the next major
  down (~19 PRs, #1948–#1963), burning a critic review per rung of the
  walk-down. *Q3/Q4.*
- **DRE-2062** — `@dependabot ignore` replied "only available on
  single-dependency pull requests" on a grouped bump; the PR had to be
  hand-closed and would re-file weekly until a config `ignore` rule landed.
  *Q4.*
- **DRE-2071 (morning)** — all 27 agent-bureau review dispatches crashed, and
  the sha-bound dispatch receipts still stood: the rail never retried, and an
  operator had to `@dependabot rebase` the entire backlog. *Q5.*
- **DRE-2071 (evening)** — after v4 fixed the fleet crash CAUSE, the stale
  receipts still blocked re-dispatch; an operator hand-dispatched 6 reviews.
  Receipts must be outcome-aware, retry bounded per head. *Q5.*

The June lessons (same class, quota-shaped):

- **2026-06-28 shared-quota exhaustion** — relay dispatch and critic review
  drew one App installation's 5,000/hr bucket and exhausted it in lockstep;
  the qa-bot App split them onto independent quotas. Know which identity's
  quota every call draws from. *Q1.*
- **DRE-1921 / the medic-loop class** — the medic retried a critic that had
  crashed on a GitHub rate-limit: re-running against an exhausted limit cannot
  succeed and deepens it — six PRs looped and burned the bot's quota twice.
  Classify before retrying; every retry at a vendor boundary is bounded. *Q5.*

## Critic: the checklist is a review gate

On a boundary-touching PR — anything changing workflow triggers, `allowed_bots`
or authorship gates, secrets wiring, vendor commands/config (e.g.
`dependabot.yml`), webhook/dispatch handling, or receipt/marker lifecycles —
the critic walks the five questions explicitly. **An unanswered question is a
finding**: if the card, plan, or PR body does not answer it and the diff does
not make the answer obvious, that is a REQUEST_CHANGES-grade gap, same as a
missing test. Cite the question by number and say what evidence would answer
it.
