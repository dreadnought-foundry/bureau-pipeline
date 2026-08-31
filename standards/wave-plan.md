# Wave-plan standard — what a wave plan must state

A **wave** is a batch of work shipped together against one stated objective
(Wave 0, Wave 1, Wave 1.5). Its plan is the document the CEO green-lights
*before* the wave runs, and the document the wave is judged against when it
closes. This standard is what that document must contain.

It lives here, in `standards/`, because that is the only directory a headless
CI agent reads (`README.md` — the workflows inject these files at run time).
A standard kept in a product repo cannot be applied by the agents held to it.

Scope note: this is the **wave** level. `plan-artifact.md` is the **epic**
level — the seven sections an epic publishes for green light. A wave plan sets
the objective; the epic artifacts under it say how each piece is built.

## The six things every wave plan states

A plan missing any one of these is not ready for the CEO. Each is a section,
and each is answered with specifics — a heading with a paragraph of intent
under it is a missing section wearing a title.

### 1. The research, with provenance

Every claim the plan rests on says **where it came from**: the run, the card,
the incident, the log, the file and line. A number with no source is an
opinion, and an opinion cannot be re-checked when the wave closes and the
number disagrees. Cite it inline where the claim is made, not in a footnote
nobody follows.

A number you cannot source is marked ***(unverified)*** — the convention
`architecture/audits/` already uses — and the plan never carries a citation
nobody has followed. **A citation that does not check out is worse than an
absent one:** it looks solid, so nobody opens it, and the first reader who
does has to distrust the whole file. Where an unverified number is
load-bearing for an open decision, say so at that decision (§3), so it cannot
close on evidence nobody has.

### 2. Where the research contradicted the wave

Name the places the evidence went **against** the thing you set out to build,
and what you did about each: dropped it, narrowed it, or proceeded anyway with
the reason. A plan whose research agrees with it everywhere did not do
research — it collected support. This section is the honest one, so write it
before the section that argues the case.

### 3. The decisions still open

The questions the plan does **not** answer, listed as questions, each with who
decides and by when. An unresolved decision written down is a scheduled
choice; the same decision left out is a surprise mid-wave, made by whoever
hits it first. "Undecided" is a valid state. Silence is not.

### 4. What the plan cuts

What the wave deliberately does **not** do, and why — the adjacent work, the
tempting scope, the thing everyone will assume is included. Cuts are stated
positively and by name (`out of scope: <thing> — <reason>`), because an
unstated cut reads as an oversight and gets built by someone being helpful.

**Do not cut by exception list.** An exception list is arbitrary: items join
it because they are awkward, and that is where the hole grows
(cited by [`../architecture/decisions/adr-layer-1-guard-scope.md`](../architecture/decisions/adr-layer-1-guard-scope.md),
which derives its scope from one fact instead). Cut on a rule.

### 5. Every phase, and how it will be proven in production

The plan lists each phase, in order, and for each one states **how it will be
proven in production** — the observation, on the live system, that says the
phase actually works. Not "tests pass": a test proves our logic, and a wave
ships behaviour. Name the run, the log line, the lane transition, the metric
or the card that will show it. A phase with no production proof is a phase
that closes on somebody's opinion.

### 6. The KPIs, predicted before the run

The numbers the wave expects to move, **predicted before the wave starts**,
each with a baseline, a direction, and how the baseline was measured. Written
after the fact they are not predictions, and the wave cannot be wrong.

> *"Predicting two and moving two is a result; moving two and then naming them
> is a story."* — objective O10

That is the whole reason the prediction is dated. So the predictions are
carried the way the epic artifact carries them — a fenced block with the
info-string `kpis`, each record naming a `name`, a NUMERIC `baseline` and a
`direction` — with the prose beside it saying how each baseline was measured.
One grammar at both levels, so a close-out reads a wave and the epics under it
the same way; the fields and the close-out are specified in
[`plan-artifact.md`](plan-artifact.md).

## The epics it commits to

A wave plan names the epics the wave signs up to, **in dependency order**, as a
fenced block with the info-string `epics`:

    ```epics
    [
      {"key": "standard", "title": "The standard moves where agents read it",
       "depends_on": []},
      {"key": "route", "title": "The wave route and its checker",
       "depends_on": ["standard"]}
    ]
    ```

Per record: a `key` unique within the plan, a `title` that names the epic in
the words the CEO will read, and `depends_on` — the keys it cannot start
without. **The order of the list is the order of the wave**, so an epic listed
before something it depends on is a defect, and so is a cycle: one rule catches
both, because a cycle is a set of epics that cannot all be listed after what
they depend on.

Approving a wave approves this shape and this order. It is not an approval of
each epic — what an epic owes when its turn comes is the plan artifact at the
epic level.

## Progressive commitment — each epic gets its own green light (DRE-2846)

That last paragraph is the whole gate, so it is recorded rather than assumed.
Approving a wave approves **the shape and the order, and nothing more.** Every
epic in the block above is recorded on its own card as
**committed-in-sequence**: it belongs to a wave whose shape was approved, and
nothing else about it has been. That is a state the sweep reads — it will not
promote a committed-in-sequence epic that has had no green light of its own,
and it says so on the card.

**When an epic's turn comes** — everything it depends on has finished — it
moves to the lane no epic leaves without a plan artifact, writes that artifact
**then**, and reaches the CEO on it. Not the artifact that existed when the
wave was approved: by then the world has moved, and the epic the wave sketched
in week one may not be the epic worth building in week five. Wave 1.5 is the
argument — approved 2026-08-23 as a shape, and by 2026-08-29 two of its cards
had been rewritten, one had been split into four, and its phase count had gone
from seven to nine. Every one of those changes was right, and a single approval
covering all of them would have been an approval of something nobody had read.

**Reordering or dropping an epic inside an approved wave needs no
re-approval.** The wave committed to a set and an order, and both were always
going to move. What a change owes is a record — its reason and its date — and
that record is written into the wave card's own description, where the CEO
reads the plan:

    python3 scripts/wave_commitment.py reorder <WAVE> <key> --after <key> --because "…"
    python3 scripts/wave_commitment.py reorder <WAVE> <key> --first --because "…"
    python3 scripts/wave_commitment.py drop <WAVE> <key> --because "…"

A change that would put an epic before something it depends on, or that would
strand an epic waiting on one being dropped, is **refused** — judged by the
same dependency rule that judges the `epics` block above, because it is the
same rule.

[`../scripts/wave_commitment.py`](../scripts/wave_commitment.py) is the
mechanical form of this section; the lane an epic's turn sends it to is read
from `config/lane-contract.json`, so it cannot drift from the clause that makes
the artifact compulsory.

## The checker

[`../scripts/wave_plan.py`](../scripts/wave_plan.py) is the mechanical form of
this file, and it **reads this file**: the six sections above are parsed out of
it when the check runs, so editing this standard changes what a plan must state
without touching the code. It refuses; it does not warn.

    python3 scripts/wave_plan.py headings          # the sections, from here
    python3 scripts/wave_plan.py check wave-plan.md

It reports a missing section by name, an `epics` list that is not in dependency
order, a claim in §1 carrying a number with neither a source nor the
*(unverified)* marker, and any citation that does not resolve. A URL is never
called broken — the check did not open it, and a checker that failed a link on
evidence it never gathered would be the failure it exists to catch
([`console-honesty.md`](console-honesty.md) rule 2).

## When the wave closes

Close against the plan, section by section: the KPIs predicted in §6 against
what actually moved, the phases in §5 against what was actually proven in
production, and the open decisions in §3 against what was actually decided.
The cuts in §4 are the record of what the next wave may pick up.
