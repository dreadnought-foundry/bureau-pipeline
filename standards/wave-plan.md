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

That is the whole reason the prediction is dated. At the epic level the same
objective is enforced mechanically, as a machine-readable block that a
close-out diffs prediction against outcome — see
[`plan-artifact.md`](plan-artifact.md).

## When the wave closes

Close against the plan, section by section: the KPIs predicted in §6 against
what actually moved, the phases in §5 against what was actually proven in
production, and the open decisions in §3 against what was actually decided.
The cuts in §4 are the record of what the next wave may pick up.
