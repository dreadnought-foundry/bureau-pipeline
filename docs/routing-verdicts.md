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
| **WORKBENCH** | Needs an interactive flow or live system state — driving an auth flow, forcing a token past expiry, confirming something in production. | Todo | `operator` | no |
| **OPERATOR** | Not code — a deploy, a migration run, a secret. | Todo | `operator` | no |
| **PARKED** | Well-formed and deliberately not to be built. | Backlog | `plan.yml` | no |
| **NEEDS WORK** | Not buildable as written. | Planning | `plan.yml` | no |

- **FLEET** — The sweep promotes it out of Backlog and the relay dispatches a build run. This is the only verdict that may be dispatched.
- **WORKBENCH** — It is real work and it goes on the board where work goes — but a person does it at an interactive session, so it carries `hand-built`, which is already the signal that stops the sweep dispatching a competing run or reporting the card as stranded (DRE-2524). Backlog was the old answer and Backlog is a dead end: nothing there ever moves a non-FLEET card on.  Marked `hand-built`.
- **OPERATOR** — Same destination as WORKBENCH and the same actor, because the same person does it; the difference is that no code is produced. `no-code` is the existing marker for that, and it already stops a merged runbook auto-closing the card (linear_ops.auto_done_skip_reason — six false portico closes).  Marked `hand-built`, `no-code`.
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
- `^\s*demo:` — The card closes only when every end-state claim in its demo report is a PASS — somebody drives the live system and records what it did. That is an interactive flow over live state, which is WORKBENCH; it is not a deploy, so it is not OPERATOR.

## Labels read first

| Label | Verdict |
| --- | --- |
| `agent:ops` | OPERATOR |
| `no-code` | OPERATOR |

Exact match, lower-cased. `no-codegen` is not `no-code`, and reading it as one is the same mistake class as a substring blocker match, one field over.

## What the acceptance criteria are read for

Checkbox criteria only, never free prose, matched on whole words. Signals are tried in the order below, and the order is load-bearing: **screenshotting a screen is not driving a flow**, but driving a flow that ends at a screen is still driving a flow.

**Every phrase names the real cards that write it (DRE-2831).** The first version of this rule was written from phrases a card author imagined, and six of the nine visual ones appear in zero of this workspace's 1,561 carded issues — so the FLEET half almost never fired and real UI cards were routed by a model instead. A phrase with no card behind it now fails `python3 scripts/routing_verdict.py check`.

### interactive → WORKBENCH

The criterion names an interactive flow or live system state. An unattended agent has no browser session, no live console and no clock it can move.

| Phrase | Cards that write it | Read from |
| --- | --- | --- |
| `sign in` | 14 | DRE-1621, DRE-1561 |
| `sign out` | 3 | DRE-605, DRE-603 |
| `log in` | 2 | DRE-604 |
| `in production` | 26 | DRE-2553, DRE-2532 |
| `against the live` | 7 | DRE-1274, DRE-2598 |
| `on the live` | 8 | DRE-2715, DRE-2753 |
| `in the live product` | 1 | DRE-2308 |
| `verified live` | 8 | DRE-2310, DRE-2414, DRE-1839 |
| `by hand` | 24 | DRE-2771, DRE-2792 |
| `manually` | 10 | DRE-419, DRE-1176 |

Read on 2026-08-31 across every issue in the Linear DRE workspace — 2,773 cards, 1,561 of them carrying `- [ ]` acceptance criteria.

Attested the same way as the visual half, and seven phrases went the same way as its guesses: 'log out', 'force the token', 'past expiry', 'walk through', 'step through', 'in the browser' and 'interactively' matched no card at all and were dropped, and two more — 'past `exp`' and 'confirm the session' — matched only cards 'sign in' already catches, or a pytest session ('confirm the session fails with the expected error') — this workspace writes 'by hand' (24 cards) and 'manually' (10). 'in the live product' and 'verified live' were ADDED, and only because the visual half was widened: a criterion that states a rendered outcome and then says it is verified in the running product must reach a person, and interactive is read first.

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

