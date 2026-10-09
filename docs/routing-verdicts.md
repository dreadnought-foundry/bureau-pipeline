# Routing verdicts

<!-- GENERATED FILE — do not edit. Source: config/routing-verdicts.json.
     Regenerate with `python3 scripts/routing_verdict.py render`. -->

A verdict is a **routing decision, not a quality score**. It answers *who builds this, and how* — and every answer sends the card somewhere different. Framed as a score, a critic drifts toward marking things good so it looks useful; framed as routing there is no good or bad, only a wrong destination, which shows up immediately.

This document is rendered from the same file the sweep and the write path read, so it cannot drift from the enforcement. Destinations and actors are bound to `config/lane-contract.json`: a route whose destination is not a lane, or whose actor is not a permitted writer of that lane, fails `python3 scripts/routing_verdict.py check`.

The actor is who is **accountable for the card at the destination** — usually whoever picks it up, and where nobody does, the writer that performs the move. Being a writer somewhere is not enough: `operator` is a real writer and is not permitted on `Backlog`, which is why PARKED naming it sent cards to a lane that actor may not write (DRE-2824).

## The five routes

| Verdict | Means | Destination | Who handles it there | Dispatched? |
| --- | --- | --- | --- | --- |
| **FLEET** | Buildable unattended in one pull request. | Todo | `agent-task.yml` | yes |
| **WORKBENCH** | A person works it at an interactive session, against live system state. The acceptance criteria no longer produce it (DRE-6227). | Hand-work | `operator` | no |
| **OPERATOR** | Not code — a deploy, a migration run, a secret. | Hand-work | `operator` | no |
| **PARKED** | Well-formed and deliberately not to be built. | Backlog | `plan.yml` | no |
| **NEEDS WORK** | Not buildable as written. | Planning | `plan.yml` | no |

- **FLEET** — The sweep promotes it out of Backlog and the relay dispatches a build run. This is the only verdict that may be dispatched.
- **WORKBENCH** — It stays in the vocabulary for the historical `DEMO:` title convention and for the pipeline's own record stampers, `repair_card` and `model_adoption_actions`, which write it onto cards they file. The acceptance criteria no longer produce it: a criterion that can only be met by watching the change run after it ships is a proof observation or a follow-up card, never a reason to hold the build, and reading one off a phrase sent six code cards in a week to a lane where nobody builds them (DRE-6227). It carries no mark: the person's mark it used to put on is the CEO's own, applied only when he asks for it. Backlog was the old answer and Backlog is a dead end. The sweep performs this move itself (DRE-3385), on the same gates a FLEET card passes, because the destination written here was true for a year and nothing ever carried a card to it. It lands in Hand-work, the person-work lane, not Todo (DRE-5240): Todo is the build button, and a person's card waiting there for days read as a stuck build queue.
- **OPERATOR** — Same destination as WORKBENCH and the same actor, because the same person does it; the difference is that no code is produced. `operator-step` says a person performs the step, and the sweep reads it as a person's card (routing_verdict.hand_marks), so it neither dispatches a competing run nor reports the card as stranded (DRE-2524, DRE-6225). It replaced the CEO's own mark here, which nothing automatic applies (DRE-6227). `no-code` is the existing marker for the rest, and it already stops a merged runbook auto-closing the card (linear_ops.auto_done_skip_reason — six false portico closes).  Marked `operator-step`, `no-code`.
- **PARKED** — Backlog IS the right lane for a card that is deliberately inert — the dead end is the point. It is never promoted and never reported as stalled by any sweep. The actor is the planning-exit writer that stamps this verdict and lands the card there, because Backlog is a lane only the process writes; it is not somebody waiting to pick the card up, because for PARKED nobody is.
  - **Who takes it back out:** Only a human revives a PARKED card. Nothing in the pipeline takes it back out of Backlog — no sweep, no run, no label — so a person deciding the card is worth building again is a separate, later act, and never the actor of this routing decision.
- **NEEDS WORK** — It returns to Planning with the specific missing thing named — the verdict comment carries it, so the planner is told what to add rather than asked to guess.

## The rule, and it is mechanical

Route on whether an unattended agent can **satisfy the acceptance criteria** — not on whether it could write the code. That reads the card's own stated exit condition instead of guessing from the title.

Read in strict precedence:

1. An explicit role label — read first, no judgement.
2. The title convention — anchored at the start of the title, never a substring search.
3. The acceptance-criteria rule: can an unattended agent SATISFY the stated exit condition?
4. Only what survives all three reaches a judgement call, and only then is a model asked.

## Title conventions

Anchored at the start of the title, never a substring search. Each one ships an **adversarial fixture** — a title that mentions the token without declaring it — and `config_problems()` refuses a convention that has none, so the mutation test cannot be forgotten. A bare substring match over prose is what froze five cards for five days (DRE-2670).

| Verdict | Pattern | Matches | Must NOT match |
| --- | --- | --- | --- |
| OPERATOR | `^\s*sign-off \(operator\)` | `SIGN-OFF (OPERATOR): rotate the CloudFront key group` | `Add a SIGN-OFF (OPERATOR) section to the runbook template`<br>`Runbook: the SIGN-OFF (OPERATOR) checklist is out of date` |
| WORKBENCH | `^\s*demo:` | `DEMO: Phase 3 — folder access end to end` | `Record the demo: phase 3`<br>`Update demo docs`<br>`Phase 3 demo runner` |

- `^\s*sign-off \(operator\)` — The card's deliverable is a human's sign-off on live work.
- `^\s*demo:` — A reader of HISTORICAL cards since DRE-3669 — the planner files no demo card, and the 25 open ones were cancelled on 2026-09-12 — kept because a card planned before then still routes through here. The card closes only when every end-state claim in its demo report is a PASS — somebody drives the live system and records what it did. That is an interactive flow over live state, which is WORKBENCH; it is not a deploy, so it is not OPERATOR.

## Labels read first

| Label | Verdict |
| --- | --- |
| `agent:ops` | OPERATOR |
| `no-code` | OPERATOR |

Exact match, lower-cased. `no-codegen` is not `no-code`, and reading it as one is the same mistake class as a substring blocker match, one field over.

## What the acceptance criteria are read for

Checkbox criteria only, never free prose, matched on whole words. Signals are tried in the order below. A criterion that can only be met by watching the change run after it ships is a **proof observation or a follow-up card, never a reason to hold the build** — so no criterion routes a card to a person (DRE-6227).

**Every phrase names the real cards that write it (DRE-2831).** The first version of this rule was written from phrases a card author imagined, and six of the nine visual ones appear in zero of this workspace's 1,561 carded issues — so the FLEET half almost never fired and real UI cards were routed by a model instead. A phrase with no card behind it now fails `python3 scripts/routing_verdict.py check`.

### static_visual → FLEET

The criterion states a RENDERED OUTCOME, and static visual fidelity is FLEET-checkable: the agent's own suite asserts the render, and where the surface is a screen, qa-review.yml runs a visual-QA stage (DRE-1481) that installs chromium via Playwright, screenshots the changed screens, and hands the critic both the design PNG and the render, with the instruction to read both images and compare.

| Phrase | Cards that write it | Read from |
| --- | --- | --- |
| `renders` | 184 | DRE-1829, DRE-1298, DRE-2216 |
| `rendered` | 46 | DRE-2148, DRE-1316, DRE-2232 |
| `re-renders` | 5 | DRE-2501, DRE-2222 |
| `screenshot` | 31 | DRE-904, DRE-344 |
| `design tokens` | 23 | DRE-1289, DRE-1410, DRE-2206 |
| `match the design` | 1 | DRE-2004 |

Read on 2026-08-31 across every issue in the Linear DRE workspace — 2,773 cards, 1,561 of them carrying `- [ ]` acceptance criteria.

How this workspace really writes a visual criterion: 'Overview body renders Description, Equipment card, Evidence thumbnails' (DRE-1829), 'No hardcoded hex — all colors via design tokens' (DRE-1289). Two frequent candidates were tested and REJECTED rather than added: 'shows' (189 cards) reads the same on 'synth shows' and 'the log shows', so it cannot tell a screen from a CLI; the bare 'render' (95 cards) names an action or an endpoint — 'GET /d/{docId}/render' — rather than an outcome. Frequency alone is not evidence.

Criteria that name neither signal are a judgement call — the one place a model is worth asking. A card with no acceptance criteria at all is NEEDS WORK: there is no exit condition to route on.

## Epics get a different question

"Could an agent build this unattended" is meaningless for a card the planner owns. An epic gets a **plan test**, never a buildability test and never one of the five routes:

- it has children
- its children carry inheritable labels
- it states an acceptance criterion for the set

