# Card-quality standard — the Linear card contract

The human-readable contract for how a Linear card must be structured to flow
through the pipeline. **The live enforcer is `scripts/validate_card.py`** (the
Todo-entry gate) and its tests — that code is the real single source of truth;
this is the standard it implements. Create cards with the Linear MCP
(`save_issue`) or `scripts/linear_ops.py`. **Search before you create** to avoid
duplicates.

## Required — a card is valid with BOTH
1. A **`repo:<slug>` label** — the canonical source of truth for the card's
   repo. **The valid slugs are exactly the keys of `config/repo-map.json` in
   bureau-pipeline.** Read that file; do not trust a list copied into a
   document, this one included. It is the same snapshot the relay routes on and
   `validate_card.py` derives `VALID_SLUGS` from, so a slug that is in the file
   routes and a slug that is not is bounced with the full valid set named on the
   card. A restated list is how a document ends up naming five slugs while the
   live map carries more, and everyone who reads it to write a card gets it
   wrong. *(Legacy fallback: a `**Repo:** <slug>` frontmatter line in the
   description is still ACCEPTED for pre-existing cards so they keep routing,
   but it's deprecated — set the label, don't write the stamp. Fenced code is
   ignored.)*
2. An **`agent:*` label** (`agent:engineer`, `agent:frontend`, `agent:devops`,
   `agent:planner`, …).

The Todo gate is **fix-first**: it auto-repairs a missing piece when it can infer
it (from an `initiative:<x>` label or the Linear project-name prefix) and only
**bounces** to Planning when the repo can't be inferred deterministically. Get it
right and the gate is a no-op.

## Optional — only when applicable
- **`**Design:** <png path>`** — UI cards ONLY (e.g.
  `console/design/images/screens/desktop/board.png`). **Forbidden** on non-UI
  cards; its absence is normal. (See `standards/design.md`.)
- **`**Spec:** openspec/changes/<id>/`** — only when the work needs a
  cross-component contract; read it before coding.
- **A dependency is a Linear `blockedBy` relation.** That relation is the
  dependency — the only thing the promotion gate, the epic gate, the console
  and the auto-close path read. Linear models dependencies natively and renders
  them in the UI; leverage what the source system already tracks rather than
  reinventing it with our own tag. **Never name the parent epic** as a blocker
  (epics stay In Progress → deadlock).
- **`**Blocked by:** DRE-N, DRE-M`** — an optional body line that DOCUMENTS the
  relation for human readers. It is not the dependency and cannot create one.
  `linear_ops.py` materialises the line into real `blockedBy` relations at
  creation (`subissue` / `oneoff`), so writing it is the easiest way to GET the
  relation — and if the relation is not there afterwards, the sentence is
  wrong. To be read as a declaration at all it must **open its own line**
  (`Blocked by:` / `Serialize after:` / `Depends on:`, optionally inside a list
  item — bulleted or numbered — or bold markup); a sentence that merely
  mentions "blocked by" or "depends on" is ordinary prose (DRE-2670: epic
  DRE-2492 froze five of its own children for five days on the sentence
  *"neither depends on the other"*).
- **A prose line claiming a dependency the board does not hold sends the card to
  `Triage`** (DRE-2676). The sweep refuses to promote it, says so once on the
  card naming both fixes — set the relation, or reword the line so it does not
  open with a declaring phrase — and moves it to the broken-card lane; the card
  returns to `Backlog`, where the gate re-evaluates it, never to `Todo`. The
  refusal is named `prose-blocker-no-relation` in the sweep log and on the card.
  On an EPIC the same defect refuses and comments but moves nothing, and the
  sweep run goes red once it has stood two hours. Nothing rewrites a
  description: the sentence is the author's to fix. Measured on 2026-08-31,
  every one of the board's 44 prose declarations was corroborated by a relation
  and none was prose-only — `python3 scripts/check_prose_blockers.py`
  recomputes that, and the number is never remembered.
- **Labels:** `initiative:<x>` (the cross-project filter); `no-code` for
  operator/non-build cards.
- **`break-glass`** — the ONE sanctioned way past the Todo-entry gate at 2am
  (DRE-2737). **Operator-only**: applied by hand, in Linear, by a person; the
  pipeline's own label writes refuse it, so **no agent may apply it**. The
  bypass is recorded on the card and counted rather than undone, and the card
  owes the classification it skipped — once its work merges it returns to
  `Planning` for that review instead of going Done. Removing the marker
  afterwards changes neither the record nor the debt.

## Lifecycle — build by default; escalate by exception (DRE-1655)
A card flows `Todo → In Progress → In Review → Done`, **unattended** — one
review lane since DRE-2726, because the two that preceded it both meant "a pull
request is open and being checked". The lane contract is data
(`config/lane-contract.json`) and `docs/lane-contract.md` is rendered from it.
The engineer agent is **autonomous by default**: it researches the card and, if
confident, builds and ships it through the normal PR → critic → merge gates — no
human in the loop (overnight automation is the point). The adversarial critic
and the test suite are the correctness backstop, so the CEO is not gating every
diff.

The agent **stops and asks only by exception** — on genuine uncertainty it
cannot safely resolve: **ambiguous intent**, a **risky/destructive change**, or
a real **business A-vs-B decision** the CEO should own. When it stops, it posts a
**plain-English question** (business terms, no code or diffs) as a comment and
parks the card in the **`Green Light`** lane — the CEO's "needs you" queue,
the same lane epics wait in for plan approval. The CEO answers and moves the
card back to `Todo` to proceed (a fresh run picks up the guidance) or to
`Backlog` to drop it.

**NOT `Triage`, and the distinction is the point.** Triage is the *broken-card*
lane: an unroutable `repo:` label, an archived repo, a card the readiness guard
has returned three times — mechanically wrong, usually an agent or operator fix.
An escalated card is **not broken**. It is correct, and waiting on a judgement
only the CEO can make. Triage became a dead end once by mixing the two — 17
cards, all machine-created, none ever moved — and a real decision sitting in a
lane people scan as a defect list is that same failure wearing a new label
(DRE-2776). DRE-2722's title reads "move the escalations to Triage"; the
criteria it was accepted against say `Green Light` holds what the lane it
renamed held, and that lane held escalations. A title is not what a card was
accepted against.

Escalating is a **high bar** — over-escalating recreates the overnight-stall
the model exists to avoid; routine, reversible choices are just built and noted
in the PR.

`Green Light` (decision needed, build can proceed once answered) is distinct
from `Backlog` (the impossible-as-specified / blocked path, inert until the card
is fixed), and from `Triage` (the card itself is malformed and cannot proceed as
written, whoever answers).

There is **no propose-first hard stop**: cards are not gated awaiting
approval before any work — autonomy is the default, the human is the exception.

## Hand-planning is an escalation and nothing else (DRE-2848)

Sometimes the reasoning IS the deliverable: the thinking cannot be done by an
agent and needs a person. When that happens **the planner escalates with a
stated reason and the card parks in `Green Light`** — the CEO's decision queue,
the same lane a plan waits in. The reason is written in business terms, never a
diff; a reason written as code is not put in front of the CEO at all
(`scripts/planning_escalation.py`).

**No label, flag or lane skips `Planning`.** Hand-planning
is an escalation OUT of Planning, not a way around it, and that distinction is
the whole rule: an escape hatch with a name and a record is a route; an escape
hatch without one is a hole nobody is accountable for. The absence is checked
rather than asserted — `python3 scripts/planning_escalation.py check` reads the
lane contract, the shape and verdict vocabularies, the pipeline's own label
constants and the planner workflow, and names anything that would let a card
past. It is the same `Green Light`/`Triage` split as above: an escalated card is
not broken, so it never goes to Triage.

**And no WRITER puts a card past it either** (DRE-2859). The check above reads
the pipeline's declarations; `python3 scripts/ready_lane_writers.py check` reads
the writers. It discovers every place a card can be put in a lane — the write
layer's own seam, every call of it in the scripts, every invocation of it in the
workflows, and Linear's team-level default, which no code path touches — and
names any that reaches a lane the pipeline treats as ready work without the lane
contract permitting it there. Discovered, never listed: the writer nobody
remembered is exactly the one still open, and a check that enumerated today's
would only prove today's are still fixed. What it CANNOT see is in its own
docstring — above all a hand write in the Linear UI, which nothing in the
pipeline can prevent.

`break-glass` is unchanged by this and is not an exception to it. It is a
bypass of the **Todo-entry gate**, applied by hand by the operator, recorded and
counted — and the card comes back to `Planning` for the classification it
skipped once its work merges. It defers Planning; it does not skip it.

## Routing verdicts (DRE-2724)

Every card leaving the planning segment carries **exactly one** verdict, as a
machine-readable comment (`🧭 routing-verdict: …`). It is a **routing decision,
not a quality score** — it answers *who builds this, and how*, and each answer
sends the card somewhere different. Framed as a score a critic drifts toward
marking things good so it looks useful; framed as routing there is no good or
bad, only a wrong destination, which shows up immediately.

| Verdict | Means | Where it goes / who picks it up |
| -- | -- | -- |
| **FLEET** | Buildable unattended in one PR | `Todo` — the sweep promotes it, an agent run builds it. The ONLY verdict that is dispatched. |
| **WORKBENCH** | Needs an interactive flow or live system state | `Todo`, marked `hand-built` — the operator, at an interactive session. |
| **OPERATOR** | Not code — a deploy, a migration run, a secret | `Todo`, marked `hand-built` + `no-code` — the operator. |
| **PARKED** | Well-formed and deliberately not to be built | `Backlog` — landed there by the planning-exit writer, and nobody picks it up. Never promoted, and **never reported as stalled** by any sweep. |
| **NEEDS WORK** | Not buildable as written | `Planning` — the planner, with the specific missing thing named. |

**The rule is mechanical:** route on whether an unattended agent can SATISFY
the acceptance criteria — not on whether it could write the code. Read in strict
precedence: an explicit role label (`agent:ops`, `no-code`), then the title
convention **anchored at the start of the title**, then the criteria rule; only
what survives all three is a judgement call worth asking a model about.

**Split the visual case carefully.** Static visual fidelity is FLEET-checkable —
`qa-review.yml` screenshots the changed screens and hands the critic both the
design PNG and the render. Interactive or live-state behaviour is WORKBENCH.
Screenshotting a screen is not driving a flow. The signal reads the phrases
real cards write — `renders`, `rendered`, `design tokens`, each one naming the
cards it was read from — after DRE-2831 found the shipped list matching phrases
nobody writes and sending real UI cards to a model. It still does not decide
every visual card: about four in ten name no rendered outcome and fall through
to judgement, which is the designed behaviour and not a promise broken.
`standards/design-parity.md` states the live caveat.

**PARKED is landed by the process, not by a person (DRE-2824).** `Backlog` is
process-controlled and no human may write it, so the actor on a PARKED card is
the planning-exit writer that stamps the verdict and lands the card there. There
is no one waiting to pick it up, because for PARKED nobody is. Reviving it is a
separate, later human act — nothing in the pipeline takes a PARKED card back out
of Backlog: no sweep, no run, no label.

An **epic never gets a buildability verdict.** "Could an agent build this
unattended" is meaningless for a card the planner owns; an epic gets a plan
test — does it have children, do they carry inheritable labels, is there an
acceptance criterion for the set.

The vocabulary is data (`config/routing-verdicts.json` in bureau-pipeline) and
`docs/routing-verdicts.md` is rendered from it; every destination and actor is
bound to the lane contract, so a route with no destination or no actor fails the
check rather than becoming a dead end.

## Epics
Expressed by Linear **native parent/child** (not a label, not frontmatter).
`[EPIC]` in the title OR having children ⇒ the gate infers `agent:planner`. The
epic's **first prose paragraph** is the CEO-readable plan summary — lead with it
(the repo is carried by the `repo:<slug>` label, not a body line). To start an
epic, **move ONLY the epic to In
Progress and stop** — reconcile auto-promotes the unblocked children; never
hand-move children (it double-dispatches and reverts in-progress work).

**A card has no children.** Giving a card sub-issues silently converts it INTO
an epic — the gate infers `agent:planner` from having children, and reconcile
never promotes an `agent:planner` card — so the parent stops being promoted,
permanently, with nothing saying so. `linear_ops.py subissue` refuses a parent
that is not already an epic. Neither a `DRE-1234a` suffix nor a sub-issue: the
new work is a **sibling card under the same epic**, with its own number.

## Every epic ends with a proof card and a demo card (DRE-2746)
The last two children of every epic are a `PROOF: …` card and a `DEMO: …` card.
They answer different questions and neither substitutes for the other:
**proof** answers *did it work* — not a green suite, but the mechanism observed
running against real state, with the observation recorded in the repo so the
record merges; **demo** answers *can the CEO see it*, because a merged PR and a
passing suite are invisible to the person who green-lit the epic. An epic that
produces neither has no way of being wrong in public.

Four conditions, and each is checked on the planner's OUTPUT rather than on
any document that states the convention — a convention nothing checks is a
convention that drifts:

1. Both are the epic's **last two children** (either order between themselves).
2. Both are **blocked by every other child**, as real Linear `blockedBy`
   relations. Prose is not a relation and the gate reads the relation.
3. **Neither may carry `FLEET`** — both route to `WORKBENCH` or `OPERATOR`,
   because a proof the fleet can close by merging its own code is not a proof.
   The whole value is that something other than the builder confirms it. The
   pair of acceptable verdicts is derived from `config/routing-verdicts.json`
   (the verdicts whose accountable actor is a human), never restated in code.
4. **Neither may wear a build role** (DRE-3039) — `agent:engineer`,
   `agent:frontend`, `agent:devops`, `agent:database-architect`. A role a build
   run is dispatched for is a card the fleet picks up, and the thing it would
   build is the proof of its own siblings' work. The pair carries `agent:ops`.
   Both lists are derived, never restated: the build roles off `agents.yaml`
   (the roster entries running on `agent-task.yml`), the role the pair may wear
   off the routing vocabulary's own label map.

**And the check writes the verdict it computes onto both cards** — the same
`🧭 routing-verdict` comment every other verdict uses, so
`routing_verdict.promotion_refusal` reads it and the sweep leaves the pair in
`Backlog` for the person who confirms it. It used to compute the verdict, print
it and stamp nothing, and a verdictless child promotes exactly as it always
had: the pair was dispatched to a build agent the moment its siblings reached
Done (DRE-3039). One writer, the one that already knows the answer.

An epic missing either card is bounced back to `Planning` with the reason
named, the same way an epic with invalid children is. The enforcer is
`scripts/proof_and_demo.py`, run over `linear_ops.py children-detail` in
`plan.yml`; `briefs/planner.md` tells the planner how to satisfy it.

## Mid-epic discovery (DRE-2739)
A finding made **while building**, about an epic that is already approved and
already running, does not go back to Intake. It is filed against the epic with
one line of justification, classified by one question — **does the approved plan
still describe what we are doing?**

    python3 scripts/mid_epic.py discovery <EPIC> --kind addition \
      --because "<one line>" --title "…" --body <file>
    python3 scripts/mid_epic.py discovery <EPIC> --kind amendment --because "<one line>"

- **Addition** — the plan holds, there is just more of it (a second call site
  needs the same fix). A sibling card lands in Backlog carrying a **verdict**
  and promotes normally. **No new green light**: that decision was already made
  for this epic.
- **Amendment** — the plan no longer describes the work (the fix must be split
  and its order reversed). No card is created; the epic goes back to `Planning`
  and is re-green-lit.

Guessing wrong is cheap — the artifact update catches an amendment mislabelled
as an addition. What is NOT cheap is adding the card by hand: a Backlog child of
an active epic dispatches an agent within fifteen minutes, so a card added after
the epic's green light **without a verdict is refused promotion** and said so on
the card. The epic's own description carries the growth record — green-lit at N
cards, running M — and names any card that joined without the plan moving with
it. See `architecture/decisions/adr-mid-epic-discovery.md`.

## Body
A clear, **one-PR-scoped** description with its own `## Acceptance criteria`
(checkable `- [ ]` items). Any string shared across sibling cards (schema field,
route, type, env var) is written **identically** in both — that string is a
contract; the planner greps `main` first to confirm the name is free.

## When a card is too big for one run (DRE-2893, DRE-2913)
"One-PR-scoped" above is the rule; these are its tells, and every one of them is
readable **before the card is filed**. Any ONE of them means split.

1. **Contracts between the pieces.** If deliverable B reads what deliverable A
   writes, it is not one card. Strongest tell. DRE-2719 held six such pieces,
   three of which would have edited the same workflow file.
2. **Two languages or two tiers.** DRE-2838 held a backend correctness defect,
   four frontend surfaces and one file's contradictory rule. It was well-bounded
   and still too big — **bounded is not the same as small**.
3. **A criterion counting something never enumerated.** DRE-2837's headline said
   "the nine derivations" and the nine were named nowhere, so an agent had to
   redo the whole sweep before writing a line. The real number was ~73 across 18
   groups.
4. **An unbounded quantifier** — "every surface", "all call sites". DRE-2838's
   "every surface rendering a work state" was 57 mount sites.
5. **Specific is not small.** DRE-2871 was the best-written card of its night —
   eight sites each with a file and a line, what each one asserts it never read,
   and an explicit not-in-this-card section. **Six runs died on it**, the last
   at 151 turns and $17.44. Countable, and still too big: eight sites across a
   backend **and** a frontend, plus a declared rule, plus an AST guard, is
   **four deliverables in two languages** however precisely each is named.
   DRE-2838 taught the same thing from the other direction — an audit had
   already bounded it from 57 sites to five, and it still died twice.
   **Bounded is not small. Specific is not small.** Both are necessary; neither
   is sufficient.
6. **Cut on FILE FOOTPRINT, not only on concern.** DRE-2837 and DRE-2838 were
   both split cleanly on the problem, and every resulting piece edited the same
   console files: three PRs — #2206, #2207, #2213 — passed full review and went
   `DIRTY` within an hour of each other, purely on merge order, with **no defect
   in any of them**. `standards/engineering.md` already required that "Each
   card/agent owns DISJOINT files, and that is checked at PLAN TIME", and
   `briefs/planner.md` already carried the **contention pre-flight** that checks
   it; the rule existed and **was not applied**. Name the files each piece
   touches, and where two share one, wire `blockedBy` rather than letting the
   gate release both.

**What it cost.** DRE-2719, DRE-2847 and DRE-2838 between them burned six dead
runs and roughly $65, and produced zero pull requests. Every split then shipped
within hours, several inside 90 minutes. The agents were never the problem:
DRE-2838's second run reached "2/5 failing tests written" at 151 turns before
the cap took it, and DRE-2847's reached "3/5 implementation green". Those were
capable runs against impossible cards.

### The arithmetic that catches all six tells
The tells are what you notice; this is what you run. Count, **before filing,
with nothing run**:

1. How many **independent deliverables**?
2. How many **languages or tiers**?
3. Is any deliverable a **CONTRACT the others read**?

DRE-2871 scored **4 / 2 / yes** — the unknown-direction rule was a contract its
eight sites all depended on. That is three cards, and it was **visible at filing
time**.

**A card whose pieces read a shared rule splits that rule out FIRST**, and the
siblings are `blockedBy` it, so they cite a declared answer instead of each
inventing one. That is precisely how DRE-2871's sites 6 and 7 came to resolve
the same absence in opposite directions — `alerts._advanced_recently` says
*stalled*, `project_rollups.is_stale` says *healthy*, over the same field, in
the same codebase.

**A split is not a one-time act: run the arithmetic on every replacement card,
not only on the original.** DRE-2871 was itself one third of an earlier split of
DRE-2837, and was still too big.

### Two turn-cap deaths on one card means SPLIT (operator rule, 2026-09-01)
There is **no third attempt**, and nothing starts one for you. On turn
exhaustion the pipeline requeues the card once (counted by the
`turn-exhaustion-requeue` tag; the cap is in `scripts/dead_run.py`), and the
second death parks it in `Backlog` with the `needs-human` label. The reconcile
sweep skips a held card entirely — no requeue, no nudge, no dispatch — and
since DRE-2954 the medic asks the same question before its one automatic
retry, so nothing retries it until a human acts. **That park is the signal to
split**, not a queue position.

"Nothing retries it" was an aspiration for one day: on 2026-09-01 the medic
re-ran DRE-2937's first dead run sixty seconds after the turn cap parked the
card, and a third ~$16 build was running on a card the pipeline had just
called unbuildable until an operator killed it by hand. The medic now reads
the card before retrying, and refuses a turn-cap death outright — that is a
budget ceiling, not a flake, and the same run re-run hits the same wall.

### How to split
- **Cut on independence, not size.** Each piece must be shippable and reviewable
  alone, and each new card names the sibling it does NOT depend on.
- **Read the hand-back first if there is one.** DRE-2719's two agents both wrote
  the split and agreed; DRE-2847's and DRE-2838's died before writing one, so
  those had to be derived from the code — slower and less reliable.
- **Cancel the original, never Done.** No code shipped, and `Canceled` clears
  the blocker without claiming delivery.

## Dead — do not use
The 8-section XML tags, `**Size:**`, and `scripts/orch/v4` references — v1
conventions the cloud pipeline ignores.
