# Routing verdicts

<!-- GENERATED FILE — do not edit. Source: config/routing-verdicts.json.
     Regenerate with `python3 scripts/routing_verdict.py render`. -->

A verdict is a **routing decision, not a quality score**. It answers *who builds this, and how* — and every answer sends the card somewhere different. Framed as a score, a critic drifts toward marking things good so it looks useful; framed as routing there is no good or bad, only a wrong destination, which shows up immediately.

This document is rendered from the same file the sweep and the write path read, so it cannot drift from the enforcement. Destinations and actors are bound to `config/lane-contract.json`: a route whose destination is not a lane, or whose actor is not a permitted writer, fails `python3 scripts/routing_verdict.py check`.

## The five routes

| Verdict | Means | Destination | Who picks it up | Dispatched? |
| --- | --- | --- | --- | --- |
| **FLEET** | Buildable unattended in one pull request. | Todo | `agent-task.yml` | yes |
| **WORKBENCH** | Needs an interactive flow or live system state — driving an auth flow, forcing a token past expiry, confirming something in production. | Todo | `operator` | no |
| **OPERATOR** | Not code — a deploy, a migration run, a secret. | Todo | `operator` | no |
| **PARKED** | Well-formed and deliberately not to be built. | Backlog | `operator` | no |
| **NEEDS WORK** | Not buildable as written. | Planning | `plan.yml` | no |

- **FLEET** — The sweep promotes it out of Backlog and the relay dispatches a build run. This is the only verdict that may be dispatched.
- **WORKBENCH** — It is real work and it goes on the board where work goes — but a person does it at an interactive session, so it carries `hand-built`, which is already the signal that stops the sweep dispatching a competing run or reporting the card as stranded (DRE-2524). Backlog was the old answer and Backlog is a dead end: nothing there ever moves a non-FLEET card on.  Marked `hand-built`.
- **OPERATOR** — Same destination as WORKBENCH and the same actor, because the same person does it; the difference is that no code is produced. `no-code` is the existing marker for that, and it already stops a merged runbook auto-closing the card (linear_ops.auto_done_skip_reason — six false portico closes).  Marked `hand-built`, `no-code`.
- **PARKED** — Backlog IS the right lane for a card that is deliberately inert — the dead end is the point. It is never promoted and never reported as stalled by any sweep; only a human revives it.
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

### interactive → WORKBENCH

The criterion names an interactive flow or live system state. An unattended agent has no browser session, no live console and no clock it can move.

Phrases: `sign in`, `sign out`, `log in`, `log out`, `force the token`, `past expiry`, `past `exp``, `confirm the session`, `in production`, `against the live`, `on the live`, `walk through`, `step through`, `in the browser`, `by hand`, `manually`, `interactively`.

### static_visual → FLEET

Static visual fidelity is FLEET-checkable. qa-review.yml runs a visual-QA stage (DRE-1481): it installs chromium via Playwright, screenshots the changed screens, and hands the critic both the design PNG and the render, with the instruction to read both images and compare.

Phrases: `matches the design`, `match the design`, `renders identically`, `render identically`, `pixel-identical`, `pixel-perfect`, `visual parity`, `screenshot`, `design png`.

Criteria that name neither signal are a judgement call — the one place a model is worth asking. A card with no acceptance criteria at all is NEEDS WORK: there is no exit condition to route on.

## Epics get a different question

"Could an agent build this unattended" is meaningless for a card the planner owns. An epic gets a **plan test**, never a buildability test and never one of the five routes:

- it has children
- its children carry inheritable labels
- it states an acceptance criterion for the set

