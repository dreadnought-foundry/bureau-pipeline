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

   **The slug names the repo the card's FILES live in** — never the repo the
   work is *about*, and never the repo the card's epic happens to sit in. Every
   repo in the map is a dispatch target, `bureau-pipeline` included, so work in
   the pipeline repo is an ordinary fleet card labelled `repo:bureau-pipeline`
   and built by an agent through a PR like any other (the self-hosting
   convention in `standards/engineering.md`). And **when the title names a repo
   as a `<slug>: …` prefix, the label must name the same one**: the two are one
   fact written twice, only the label routes, and both planner card-validation
   seams refuse the disagreement with the values quoted (DRE-3278 — DRE-3275
   was titled for the repo holding its files and labelled for its epic's).
2. An **`agent:*` label** (`agent:engineer`, `agent:frontend`, `agent:devops`,
   `agent:planner`, …).

The Todo gate is **fix-first**: it auto-repairs a missing piece when it can infer
it (from an `initiative:<x>` label — the one route, since DRE-2874 deleted the
Linear project-name-prefix fallback) and **bounces** in exactly two cases. The
first is a repo it can't infer deterministically, which goes to Planning. The
second is an **epic in Todo** (DRE-5319) — a card with children, an `[EPIC]`
title or an `epic`/`roll-up` stamp, read off the card and never off its labels.
Nothing builds an epic, so the gate repairs no label, stops the build and
carries it out under a `🚫 epic-not-todo` refusal: back to `In Progress` when
that is where it came to Todo from (the CEO had approved it), to `Planning`
otherwise. The relay already dispatches nothing for an epic wearing
`agent:planner`; this stops the one it does dispatch — an epic nobody labelled.
The pipeline's own writers cannot put an epic in Todo at all (DRE-5316), and
`python3 scripts/ready_lane_writers.py check` names any Todo seam that lost that
refusal as `epic-can-enter-todo`. Nothing in this repository can stop a person
dragging an epic into Todo in Linear or the console; one that wears
`agent:planner` is never dispatched, so the gate never meets it, and moving it
out is the sweep's carry (DRE-5347). Get it right and the gate is a no-op.

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
  operator/non-build cards — and a card filed `needs-human` carries `no-code`
  with it (`needs-human` requires `no-code`; the create seam refuses the lone
  label, DRE-3512), because a card a person finishes by hand may not also
  describe a diff.
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
the same lane epics wait in for plan approval. Since epic DRE-3893 that
question carries the three declared lines — Finding, Question and
Recommendation, rendered by `scripts/console_escalation.py` — with
`none given — …` on the Recommendation line where the agent stated none. The
CEO answers and moves the card back to `Todo` to proceed (a fresh run picks up
the guidance) or to `Backlog` to drop it.

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
from `Backlog` (a build agent's blocker: a mechanical class the sweep acts on
within one pass; a question is asked in Green Light), and from `Triage` (the
card itself is malformed and cannot proceed as written, whoever answers).

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

**And every row in `Green Light` is one somebody declared** (DRE-5282). `python3
scripts/green_light_rows.py check` takes the same discovered writes and keeps the
ones into `Green Light`, the CEO's queue, then reconciles them both ways against
the `arrivals` the lane contract declares on that lane's entrance. The kinds are
the contract's vocabulary, read off it and never counted here: today a plan both
critics passed (`passed-plan`), the planner's business question (`question`), a
build's escalation to a person (`agent-escalation`), and an approved epic queued
under the cap (`queued-epic`). A write no record declares fails by location, a
record with no write fails by name, and a kind outside the vocabulary fails by
word. Each site's own gate is read rather than trusted: a passed plan's step must
be gated on both critics' pass, and a queued epic's step must add `epic-queued`
before it moves the card. A write made through a function is attributed to its
CALLER: the callers of each lane-writing function are discovered and must be the
ones its record declares, which is how the sweep's old stall exit, borrowing the
planner's question, would now be named. A destination the discovery cannot read
is a problem here, never a pass, because it could be `Green Light`.

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

**A card sent back to Planning leaves with a fresh verdict (DRE-4884).**
Sending a card back says its routing is in question. When it leaves Planning
again, the exit retires every verdict written before the card re-entered and
stamps the one it reads now. The retirement is a `🪦 verdict-retired` comment
naming the verdict, when it was written and when the card came back. The old
comment stays as the record, and nothing routes on it. An `operator-step` the
old verdict put on comes off unless the new verdict puts it on too. No verdict
applies `hand-built` (DRE-6227), so a `hand-built` on the card is a person's
own, and stays. Two live verdicts on one card are still refused.

| Verdict | Means | Where it goes / who picks it up |
| -- | -- | -- |
| **FLEET** | Buildable unattended in one PR | `Todo` — the sweep promotes it, an agent run builds it. The ONLY verdict that is dispatched. |
| **WORKBENCH** | A person works it at an interactive session, against live system state | `Hand-work`, with no mark — kept for historical demo cards and the pipeline's own record cards (`repair_card`, `model_adoption_actions`); the acceptance criteria never produce it (DRE-6227). Nothing is dispatched. |
| **OPERATOR** | Not code — a deploy, a migration run, a secret | `Hand-work`, marked `operator-step` + `no-code` — the sweep promotes it and stamps the marks; the operator does the step there, and no code is produced. Nothing is dispatched. |
| **PARKED** | Well-formed and deliberately not to be built | `Backlog` — landed there by the planning-exit writer, and nobody picks it up. Never promoted, and **never reported as stalled** by any sweep. |
| **NEEDS WORK** | Not buildable as written | `Planning` — the planner, with the specific missing thing named. |

**The rule is mechanical:** route on whether an unattended agent can SATISFY
the acceptance criteria — not on whether it could write the code. Read in strict
precedence: an explicit role label (`agent:ops`, `no-code`), then the title
convention **anchored at the start of the title**, then the criteria rule; only
what survives all three is a judgement call worth asking a model about.

**Precedence 3 reads static visual fidelity only (DRE-6227).** Static visual
fidelity is FLEET-checkable — `qa-review.yml` screenshots the changed screens
and hands the critic both the design PNG and the render. No criterion phrase
routes a card WORKBENCH any more: "sign in", "by hand" and "in production" once
did, and in one week they sent six cards of buildable code to a lane where
nobody builds them. A criterion that can only be met by watching the change run
after it ships is a proof observation, or a follow-up card under DRE-3075's
two-cards shape (below) — never a hold on the build. The signal reads the phrases
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

## The CEO's words, and where the card goes (DRE-6219)

The CEO, 2026-10-07: "Build it and let everyone know when i say built it, I'm
saying this is a hand built card." Two phrases of his decide where a card is
filed and who builds it, in every repo.

| The CEO says | The card | Who builds it |
| -- | -- | -- |
| **"build it"**, after approving a design or a fix | Filed in **Intake**, the only first lane the lane contract allows, by the person he said it to, carrying the `hand-built` label. His approval, the PT time and the mockup link go on the card. It is never put in Hand-work by hand: that lane is entered only on a WORKBENCH or OPERATOR routing verdict, and the sweep is the writer that carries a card there (`config/lane-contract.json`, Hand-work entrance). | The session he said it to. The card stays in Intake while the work is built and never waits for the groomer, Planning or the fleet. When the pull request opens, the review workflow or the sweep's hand-built-to-review move (`reconcile.move_hand_built_to_review`) carries the card to In Review (DRE-4179, DRE-4356), and the merge closes it (`linear_ops.py card-done`). |
| **"put it into planning"** | Filed in **Intake**, then moved to **Planning** by the same person as a second write — a human is a permitted writer of Planning — so the history shows the Intake step. | Planning classifies it, as for any other card. |

**Anything else he files follows the normal rule, which is Intake first.**

**Why a hand-filed card is safe from the fleet.** Nothing dispatches a card
from Intake: the relay dispatches only from `Todo`, and the one promoter into
`Todo` is the sweep acting on a FLEET verdict, which a card left in Intake never
gets. If the groomer's batch lists a `hand-built` card, verification drops it
from the batch and leaves it where it is (`groom_verify_agent.exclusion`,
DRE-5306). The sweep's stranded watchdog and its nudge loop skip a `hand-built`
card with no pull request (DRE-2524) — only the alarm for hand-built work idle
with no branch and no pull request still fires. The label is the person's own,
applied on the CEO's explicit words, never by automation reading text: the
standard already recognizes a `hand-built` no verdict applied as "a person's
own" (DRE-4884).

**The one route out of Intake: Urgent.** The sweep's Urgent fast path
(`reconcile.advance_urgent_intake`, DRE-4150) moves an Intake card raised to
Urgent, or filed at it, to Planning, and it does not read the `hand-built`
label. In Planning the card is classified like any other and can be routed
FLEET, which dispatches an agent onto work the session is already building. So
a `hand-built` card is never filed at Urgent or raised to it. If the fast path
moves one anyway, move it back to Intake: the fast path moves a card once and
leaves one a person put back where they put it. This rule is guidance only;
no code checks it.

**What is guidance and what is checked.** Building it now, in a
worktree-isolated helper, and shipping it through the normal pull request path
is guidance to the session. Nothing in the pipeline checks it. What the pipeline
does check begins at the pull request: the TDD commit order, the critic's
verdict and the merge gate, the same as for any card.

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

## Every epic ends with a proof card (DRE-2746, halved by DRE-3669)
The last child of every epic is a `PROOF: …` card. **Proof** answers *did it
work* — not a green suite, but the mechanism observed running against real
state, with the observation recorded in the repo so the record merges. An epic
that produces no proof has no way of being wrong in public.

**There is no second closing card.** The CEO decided on 2026-09-12 that there
are no demo sittings: he reads the proof record and closes the proof card
himself, so the record IS how he sees it. Every epic planned before that date
still carries a second closing child; the gate reads past one rather than
bouncing the plan, and no plan adds one back.

**A proof card closes itself on its approved record (DRE-5919).** The CEO
decided on 2026-10-05 that nobody should have to chase a proof down. When the
record merges on the card's own branch with the critic's APPROVE at the merged
head, the merge closes the card. The close comment names the pull request, the
merge time in PT and the approved commit. Without that APPROVE the card stays
open for a person, as every other `no-code` card does.

**An approved record is not enough on its own (DRE-6141).** On 2026-10-07 the
critic approved three records because they were honest about what they had not
seen, and the merge closed their cards with nearly every row `Not observed.` So
the record itself is now read. It is the one `.md` file the record's pull
request adds under `docs/` or `architecture/`. The merge gate holds the pull
request open unless every judged row of its criterion table is met. It also
holds when there is no table, no judged row, or no record it can read. The
close refuses on the same reading of the record at the merged head, and it also
refuses while a `🔬 proof-waiting` hold stands that nothing discharged.
An approved, all-met record that merged overrides an operator's
`🔬 proof-waiting` hold posted before the merge — the proof dispatcher's own
holds and one typed with `linear_ops.py proof-waiting` — because the merged
record is the observation the hold was waiting for (DRE-6489). Two holds
still stand: one naming the CEO's press, until his signed console answer,
and one posted after the merge. A refused card stays open under the
`🔒 Merged — card deliberately left open` comment, which names what refused
it, such as the standing hold or the unmet row.

**A criterion a later decision overtook is accepted, not met (DRE-6244).** The
CEO decided on 2026-10-07 that a record may carry a row the operator accepted
as out of date, provided the record links the written reason. Use it only when
a criterion has been overtaken by a later shipped decision, and the linked
comment names that decision. The operator writes the decision comment and the
row — never the builder, and never the proof run. A proof run never writes an
accepted row on its own judgment: it records `Not observed.` or `Not met.` and
lets the gate hold. The row reads `ACCEPTED by operator decision — <reason>
(<link>)`, where the link is a Linear comment
(`linear.app/<workspace>/issue/DRE-<n>/<slug>#comment-<id>`) or a GitHub pull
request comment (`github.com/<owner>/<repo>/pull/<n>#issuecomment-<id>`) in
the same cell. An accepted row with no such link is held as not met. A record
whose judged rows are all `Met.` or accepted is `**Status: PASS.**`, and the
card's close comment names every accepted row by its criterion. The gate checks
only that a link of the right shape is present. It does not open the link, so
it cannot confirm the comment exists, that the operator wrote it, or that it
names a shipped decision — the critic, whose APPROVE the gate still requires,
is the reader who follows it.

**Proofs never wait on the CEO's sign-in (DRE-5391).** The CEO decided on
2026-09-30 that the operator runs a proof as soon as its build cards finish,
and the card closes on the evidence that run records. The CEO is needed only
for a real business decision, never to sign in so a step can be watched. A
proof observes two kinds of thing, each by its own route:

- **Live facts** — what the deployed system answers, what it records, which
  keys it refuses — are observed against the real deployment through a
  scripted, limited proof-reader identity, never through the CEO's account
  (for Portico's portals, the standing identity DRE-5389 provides).
- **On-screen steps** — click a button, see the result land — are observed in
  a browser on a local run of the same released commit the deployment runs.

**An on-screen step counts as proven only when both halves are seen:** its
screen behavior on the local run, **and** the request it makes succeeding live.
Either half alone proves nothing — a screen that works locally can still send
a request the live system refuses, and a request seen live says nothing about
the screen that sends it. The five conditions below do not change, and the
proof never routes `FLEET`: its `agent:ops` label routes it `OPERATOR`.

**The pipeline starts the proof run itself (DRE-5920).** When an epic's last
build card is Done and the release carrying those merges is live — read off
the release record (the tags, `scripts/proof_release.py`) and never assumed,
because `main` is not released — the sweep dispatches a proof run at the PROOF
card: a dedicated run on its own workflow (`proof-task.yml`, role `proof`,
`briefs/proof.md`), holding only scripted read-only identities and never a
person's account, which observes the criteria and opens the record as a pull
request on `agent/DRE-<n>-proof-record`. The card closes on the approved merge
of that record, by `card-done` under DRE-5919's PROOF rule or by the hygiene
agent's `hyg-proof-closed` receipt (DRE-5365), so the CEO reads the merged
record and nothing waits on him. The closing line on the card (condition 5)
names who reads the record, not who moves the card, and stays verbatim. The
card still routes `OPERATOR`, by its `agent:ops` label, and the five conditions
do not change: the run does the operator's observing, and the operator and the CEO
stay the accountable readers. A criterion only the CEO's own login can satisfy
is written on the card in the words `needs the CEO's press: <the press>`; a run
that meets one records everything else, parks the card once in Green Light
naming the press, and nobody chases it.

Five conditions, and each is checked on the planner's OUTPUT rather than on
any document that states the convention — a convention nothing checks is a
convention that drifts:

1. It is the epic's **last child**.
2. It is **blocked by every other child**, as real Linear `blockedBy`
   relations. Prose is not a relation and the gate reads the relation.
3. **It may not carry `FLEET`** — its `agent:ops` label routes it `OPERATOR`,
   because a proof the fleet can close by merging its own code is not a proof.
   The whole value is that something other than the builder confirms it. The
   acceptable verdicts are derived from `config/routing-verdicts.json` (the
   verdicts whose accountable actor is a human, `WORKBENCH` beside `OPERATOR`
   today), never restated in code.
4. **It may not wear a build role** (DRE-3039) — `agent:engineer`,
   `agent:frontend`, `agent:devops`, `agent:database-architect`. A role a build
   run is dispatched for is a card the fleet picks up, and the thing it would
   build is the proof of its own siblings' work. The card carries `agent:ops`.
   Both lists are derived, never restated: the build roles off `agents.yaml`
   (the roster entries running on `agent-task.yml`), the role the card may wear
   off the routing vocabulary's own label map.
5. **Its body carries the closing line, verbatim**, above the acceptance
   criteria: `The CEO reads this record and closes this card; there is no demo
   sitting.` The decision only holds where the person closing the card reads
   it, and its acceptance criteria say he closes it after reading the record —
   never that he has said so at a sitting.

**And the check writes the verdict it computes onto the card** — the same
`🧭 routing-verdict` comment every other verdict uses, so
`routing_verdict.promotion_refusal` reads it and the sweep carries the card to
`Hand-work`, the person-work lane, marked `no-code`, with no build run
dispatched (DRE-3385, DRE-5321). The proof dispatch starts the proof run there
(`scripts/proof_dispatch.py`, DRE-5926). A proof card receives no person's mark
(`routing_verdict.card_marks`), because the proof run takes it, not the
operator. **Its protection is its `no-code` and the proof dispatch**, and only
the stamp produces the verdict they follow. It used to compute the verdict,
print it and stamp nothing, and a verdictless child promoted exactly as it
always had: the card was dispatched to a build agent the moment its siblings
reached Done (DRE-3039). One writer, the one that already knows the answer. A
legacy epic's second closing child is stamped only where its own verdict is one
a human acts on — nothing checks that child any more, and writing `FLEET` onto
an unchecked card would send the fleet at it rather than keep the fleet off it.

**`hand-built` is the CEO's mark, and nothing automatic applies it.** The CEO,
2026-10-07: "Hand-built cards are typically when I tell them, 'Hey do that by
hand.' It shouldn't come from anybody else." So `hand-built` goes on a card
only when he asks for it. The pipeline reads it — the sweep, the groomer and the
epic cap all leave a `hand-built` card to the person building it — and never
writes it: no verdict marks it, and no stamp or sweep puts it on (DRE-6227).
Before that rule the sweep wore it onto buildable code six times in one week. An
operator step carries `operator-step` instead, the mark OPERATOR applies beside
`no-code`, and buildable code is FLEET.

An epic missing the card is bounced back to `Planning` with the reason
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
the card. Planning's routing verdict is a sign-off the gate accepts as much as
the discovery route's `mid-epic-verdict` (DRE-5900): either one means somebody
read the card, and where the verdict sends it is still the routing gate's call,
so a PARKED card stays put. A card carrying neither is told so once and
sent to `Planning` once for a routing verdict, rather than refused on every
pass; Planning's exit lands it back in Backlog carrying one, and it promotes.
The green light is the epic's newest `In Progress` entry, read off the epic's
newest history. The epic's own description carries the growth record —
green-lit at N cards, running M — and names any card that joined without the
plan moving with it. See `architecture/decisions/adr-mid-epic-discovery.md`.

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

### A turn-cap death is read before it is retried (DRE-4366)
The first death is **read**, not retried blind. When a run hits the turn
ceiling — 400 turns for every run since DRE-4361, so there is no higher rung to
raise it to — the pipeline looks at how far that run got, the furthest
`⏳ n/5` marker it posted, and acts on the reading (`decide` in
`scripts/dead_run.py`):

- **Past implementation green** (`⏳ 3/5` or later) — budget, not size: the
  work was finished and the run was not. The pipeline requeues the card once,
  at the same budget (counted by the `turn-exhaustion-requeue` tag; the cap is
  in `scripts/dead_run.py`), and the retry resumes the dead run's own branch
  once DRE-4368 lands. If that run also dies past implementation green, the
  card parks in `Backlog` with the `needs-human` label — the next section.
- **Before implementation green, or no marker at all** — size, not budget:
  another run at the same ceiling buys more of the same. **Nothing is
  retried, at any count.** The card goes to `Planning` with no label and
  nothing parked, under a receipt that opens
  `✂️ turn-exhaustion-requeue → Planning:`, names the marker the run stopped
  at, lists that run's progress markers, and carries the agent's hand-back
  when it wrote one before it died. **That receipt is the signal to split**,
  not a queue position.

There is **no third attempt**, and nothing starts one for you: two turn-cap
deaths on one card are the most it ever gets. Every one of those receipts
carries `turn-exhaustion-requeue` on its first line — so does the record of a
run the job timeout then killed, of a park Linear refused to write, and of a
death that also took the agent's escalation or blocker exit — so the count and
the split ledger see every death, which they did not before. The reconcile sweep
skips a held card entirely — no requeue, no nudge, no dispatch — and since
DRE-2954 the medic asks the same question before its one automatic retry, of a
card just sent to `Planning` as much as of a parked one, so the medic and the
sweep never re-run a held card's build, fix, plan or review run on their own.
Bookkeeping is the exception (Stage 2 #23, DRE-5620/5622): a held card's
Linear Sync run, and its Merge Gate run, are retried by the medic, and limit
recovery re-runs a held card's `sync` stage. Linear Sync starts no agent. The
gate merges only on critic APPROVE and green CI, and the CEO decided on
2026-10-02 that `needs-human` does not block merging (the gate never read
that label). **One path does start agent work on a held card:** on a
conflicted pull request the gate's conflict arm dispatches Agent Fix in
conflict mode, and Agent Fix clears `needs-human` when it starts. The gate's
own wakes (CI completion, the critic's comment) already did that before this
exception, so a retried gate run only replays what the dead run would have
done. Holding the bookkeeping back is what left merged cards open.

"Nothing retries it" was an aspiration for one day: on 2026-09-01 the medic
re-ran DRE-2937's first dead run sixty seconds after the turn cap parked the
card, and a third ~$16 build was running on a card the pipeline had just
called unbuildable until an operator killed it by hand. The medic now reads
the card before retrying, and refuses a turn-cap death outright — that is a
budget ceiling, not a flake, and the same run re-run hits the same wall.

### …and a park says BUDGET, not size (DRE-3097)
A park only ever follows two deaths that both got past implementation green,
so its receipt reads **budget, not size: the work finishes but the run does
not** — and it never calls that card a split. Splitting it is the move that
already failed — DRE-3088's third death, at $17.53, came *after* it had been
cut down to XS (two edits inside two existing steps of `plan.yml` plus tests),
because the cost was the ~1,850-line file the agent had to keep re-reading,
not the size of the change. The budget a run had is printed in its
`🧠 model-attempt` receipt as `turns=400`. A card can still carry a
`turns:<n>` label — a rung from the reviewed set in `config/turn-budgets.json`
— but since DRE-4361 only to ask for LESS than the default, so there is no
label that raises it. A person reads what the two runs left behind and decides
how the card gets finished.

When the receipt is the replan above, split: the run did not get far enough
for a ceiling to be the reading, and more turns buys more of the same.

### How to split
- **Cut on independence, not size.** Each piece must be shippable and reviewable
  alone, and each new card names the sibling it does NOT depend on.
- **Read the hand-back first if there is one.** DRE-2719's two agents both wrote
  the split and agreed; DRE-2847's and DRE-2838's died before writing one, so
  those had to be derived from the code — slower and less reliable.
- **Cancel the original, never Done.** No code shipped, and `Canceled` clears
  the blocker without claiming delivery.

## An acceptance criterion the card cannot satisfy before merge (DRE-3075)

Some criteria can only be met by watching the change work in production, and
production is downstream of the merge. DRE-3075 asked for two observations of
concurrent Actions runs against real `push`/`pull_request` events — which cannot
happen until the workflow config under review is the one live on `main`. **The
card asked to watch the fix working before it was in a position to work.**

Everyone downstream then behaved correctly and it still deadlocked. The build
agent met the one provable criterion and said so plainly in the PR body ("not
provable before merge"). The critic blocked on unmet criteria, which is its job.
The fix agent could do nothing — there was no code to change — and burned four
dispatches establishing that. PR #252 sat `CONFLICTING` for eleven hours until an
operator decision moved it. **Nobody was wrong; the card was**, and the cost
landed on the human, which is the opposite of what planning is for.

**The tell, readable before the card is filed.** A criterion whose verb is
*observed*, *watched*, *seen in production* — or that names a run id, a deploy,
a live account, a tag move that does not exist yet. If satisfying it requires the
change to already be merged, **it cannot gate the merge**.

**Write it as two cards from the start.** The build card keeps the criteria a
reviewer can check against the diff. The observation goes to a follow-up card
carrying `needs-human` and `no-code`, blocked by the build card and named from
it, under `deferred: <surface> — <reason>` — the shape `standards/design-parity.md`
already sanctions for a gap that is deliberate rather than forgotten.

**Why a note in the PR is not enough.** `linear-sync` closes the build card on
the merge event, so a criterion deferred in prose is owed by nobody the moment
the PR lands. That is the propose-gate shape (DRE-1980), which ran dead for six
weeks because it was everyone's assumption and nobody's card.

## When a plan is two epics — the observation-gated seam (DRE-3244)

The section above is one criterion a card cannot satisfy before merge. This is
the same fault a whole plan wide: **a plan whose later cards depend on
OBSERVING its earlier cards live is not one epic but two.** The plan is split
into child epics under the original, which stays as the parent that rolls them
up — the first child ends at the observation, the second is filed at the same
gate, blocked on the first, and planned in detail only when the first is Done.

The tells below are readable before the plan is written, in the same grammar as
the size tells above. Any ONE of them means the plan is cut at a seam.

1. **A clean-days or clean-releases criterion.** A card whose acceptance
   criterion is a number of clean days or clean releases before something else
   may happen. DRE-3218 asked for "after seven clean console days" — nothing in
   the plan below it can be planned, let alone built, until those seven days
   have been watched, and no plan survives being written seven days early.
2. **An operator card in the middle of the chain.** An `[OPERATOR]` card — a
   supervised release, a grant applied by hand, a switch flipped — that build
   cards wait on. DRE-3166 was one, and DRE-3215 and DRE-3217 were blocked on
   it: everything downstream of a human act is downstream of when that human
   acts, which is not a date the plan knows.
3. **A second surface or repo that joins "after the first is proven".** Portico,
   the relay, the website "after a week of clean console releases". The second
   surface's cards are written against a mechanism whose real shape is whatever
   the first surface turns out to need, so writing them early is writing them
   twice.
4. **More than about eight build cards under one parent.** With a proof card
   that lists all of them as blockers: DRE-3164 had thirteen build cards and one
   proof naming every one. A proof blocked on everything is a proof that runs
   once, at the end, against a plan nobody has been able to check since it was
   approved.

### The worked example — DRE-3164

**A — the engine and its first rider:** DRE-3167, DRE-3165, DRE-3210, DRE-3211,
DRE-3166, plus a PROOF of the first supervised console release. That epic ends
at an observation: a release, watched, by a person.

**B — the fleet:** DRE-3212, DRE-3216, DRE-3213, DRE-3238, DRE-3214, DRE-3215,
DRE-3217, DRE-3218, plus the fleet proof. Blocked on A, and planned only when
A is Done.

(As planned in August each half also carried a demo card. Both were cancelled
on 2026-09-12 with the other 23; the shape above is the one to copy.)

**What it cost as one epic.** A collision with DRE-3060 at the second critic. A
plan drifting out of date against its own children over twenty hours, because
the cards were written before the thing they describe existed. And a critic that
could not finish reading fifteen cards at the old ceiling — the plan was too
long to be reviewed in one pass, which means nobody read all of it.

**The split as it landed on the board.** DRE-3164 kept seven cards. DRE-3245
took eight, blocked on it, sitting in Backlog until part 1 is Done.

### What the planner files instead

Child epics under the parent, not one epic. The original epic stays where it is
and becomes the parent, and the plan is split into child epics under it. The
shape the classifier stamps for this is `roll-up`.

- **Each child is an epic of its own.** A native Linear sub-issue of the
  parent, titled `[EPIC] <slug>: …`, carrying `agent:planner`, with a short
  statement of its slice. The order between children is `blockedBy` relations:
  the first child ends at the observation, and the second is filed at the same
  time, blocked on the first, and planned in detail only when the first is
  Done — so its plan is written against the thing that exists rather than the
  thing that was imagined.
- **Each child is planned and green-lit on its own**, like any epic: its own
  cards, its own proof card, its own trip past both critics to the CEO.
- **The parent is never approved for building and never builds anything.** It
  holds the children and rolls them up, and it closes when every child is Done.
- **Nesting is allowed.** An epic whose children are epics is a roll-up, and a
  child can be one in turn.

The parent's plan for a seam is short: its children in order, and the detail
belongs in each child's own artifact, written when its turn comes.

**When the epic behind a blocker moves (DRE-6407).** Two rules, one per moment.
An epic still waiting to be planned is planned only when its blocker is Done,
proof included — the sweep's auto-advance `waits for Done`, because that plan is
written against what the proof observed. An epic the CEO has already approved,
its cards written, is `released at build-done`: once its blocker has no
buildable card left (`epic_cap.buildable()` — open, not an epic, not a proof,
not marked for a person) and no open child epic, even with the blocker's proof
still open. Card-level `blockedBy` relations are still read, so a card that
really needs an earlier card waits for it; a blocker with work left is `not
built out` and holds. The sweep's `epic-gate:` line names the rule.

**The pre-approval critic treats a seam inside one epic as a mechanical
send-back**, in exactly these words:

    {waiting}: wait on observing {observation} live — that is a child epic under this one, not a later step.

`{waiting}` is the cards past the seam and `{observation}` is the card somebody
has to watch run. It is mechanical because it is readable off the cards, not a
judgement about ambition.

**A seam is NOT the DRE-3075 case.** That one is a single acceptance criterion
that cannot be proved before merge, and the remedy is two CARDS. This is the
epic-level analogue: a whole half of a plan that cannot be written, let alone
proved, before the other half runs, and the remedy is two child EPICS under a
parent. Same fault, different unit — and applying the card remedy to the epic
case leaves twelve cards nobody can plan.

## Dead — do not use
The 8-section XML tags, `**Size:**`, and `scripts/orch/v4` references — v1
conventions the cloud pipeline ignores.
