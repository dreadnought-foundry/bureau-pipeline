# The critic, scored against a review it has never seen

Run live on **2026-08-29** against the real workspace, read-only:

```
python3 scripts/critic_score.py score --out score.json --report score.md
```

Nothing was moved. `score` writes nothing — the alert-and-move path is
`escalate`, a separate, explicit act, the same split the groomer uses between
`propose` and `drain`.

The held-back review is [DRE-2649](https://linear.app/dreadnoughtfoundry/issue/DRE-2649),
the independent Forms review: a human read both Forms epics, all their children,
B7 and the link-out epic against the shipped code on 2026-08-22 and recorded
fourteen findings on the card. It was written before the routing vocabulary
existed, and no part of it is in the classifier's config, its prompt, or the
cards it reads. Its per-card judgements are transcribed once, with a quote each,
in `config/critic-audit-dre2649.json`.

**27 cards, 51 judgements. 24 of those judgements are excluded as
contaminated.** What is left is 8 agreements, 1 disagreement, and 18 cards the
critic could not classify at all.

## What the `hand-built` exclusion actually costs — and why it stays

The review judged the set on more than one axis, and one of them — *must a
person look at it?* — was read during planning and quoted in the plan. The `hand-built`
labels and the `DEMO:` / `SIGN-OFF (OPERATOR)` title conventions the critic
resolves that question from **were set by the answer**. Scoring it grades the
critic on a card it was handed face-up.

Both numbers, from the same run:

| | agree | disagree | could not classify |
| -- | -- | -- | -- |
| **Scored honestly** (this run) | 8 | 1 | 18 |
| Contaminated dimension included | 14 | 1 | 36 |

The contaminated rows would have added **six agreements and no
disagreements** — a clean 6/6 on every row it could answer, and every one of
them decided by a label or a title convention planning had already written.
Counted that way the headline reads 14 of 15; counted honestly it is 8 of 9, on
a set where the critic could not answer two thirds of the questions at all. The
flattery is not in the percentage — it is in six rows that could never have
disagreed. That is why the exclusion is enforced in code rather than
remembered: `scripts/critic_score.py` refuses to score a dimension the
reference marks unscored, and prints the excluded rows rather than dropping
them.

## Agreement

Eight rows, and they are not all worth the same.

| Card | Dimension | Review | Critic | Resolved at |
| -- | -- | -- | -- | -- |
| DRE-2628 | `plan` | plan-test | plan-test | epic |
| DRE-2629 | `plan` | plan-test | plan-test | epic |
| DRE-2492 | `plan` | plan-test | plan-test | epic |
| DRE-2638 | `buildability` | buildable | buildable | title |
| DRE-2639 | `buildability` | buildable | buildable | label |
| DRE-2647 | `buildability` | buildable | buildable | label |
| DRE-2648 | `buildability` | buildable | buildable | label |
| DRE-2650 | `buildability` | buildable | buildable | title |

**The three epic rows are the real ones.** All three epics were read as plans —
children, inheritable labels, an acceptance criterion for the set — and the
critic asked the same question, never the buildability one. That is the epic
rule holding on live data rather than in a fixture.

**The five `buildability` rows are cheap and should be read as such.** A card
resolved at step 1 or step 2 never reaches the acceptance criteria, and no
verdict reachable there is NEEDS WORK — so those rows could only ever have come
out "buildable". They agree because the review found nothing wrong with those
five cards, not because the critic checked.

## Disagreement

One row, and it is the most useful line in this document.

| Card | Dimension | Review | Critic | Resolved at |
| -- | -- | -- | -- | -- |
| DRE-2646 | `buildability` | not-buildable | buildable | title |

DRE-2646 is Epic B's demo card. Its title opens `DEMO:`, so the anchored title
convention routes it WORKBENCH at step 2 — correctly — and the classifier stops
there, having read no acceptance criterion. The review read the criteria and
found the demo broken: *"DRE-2646 claim 1 uses Hagen and Yannan, who share a
domain. The demo passes while the cross-side case is broken."*

**Routing a card correctly and the card being wrong are compatible states**, and
the precedence that makes the critic cheap is exactly what hides the second one.
That is not an argument against the precedence — asking a model about a card
already labelled `agent:ops` is paying to rediscover what the card says — but it
is a limit worth writing down: the steps that cost nothing also see nothing.

## What the critic could not answer

**Eighteen of 27 cards reach a judgement call and carry no recorded verdict.**
The mechanical precedence answers 9: three epics, six by label or title. Every
other card in this set is a question for a model, and no model has been asked
about them.

The consequence, stated plainly: of the **nine cards the review found
not-buildable as written, the critic reproduced none.** Eight of them are in the
unclassified list and the ninth is the disagreement above.

| Review said | Cards | Critic reproduced |
| -- | -- | -- |
| not-buildable | 9 | 0 |
| buildable | 15 | 5 (all at step 1 or 2) |
| plan-test (epics) | 3 | 3 |

That is the honest floor, and it is the same shape the groomer's audit found
against the same review: **the cheap check finds what the cards admit to, and a
human reading the code finds what they do not.** The one thing this run can say
without a model is that the label and title steps work and the epic branch
works.

## What this run says to do next

1. **The audit is only meaningful once a model has actually been asked.** The
   18 unclassified rows are not a critic failure — they are a critic that has
   not run. Re-run this against a population the routing critic has stamped and
   the same table produces a real score. The harness is the deliverable; the
   number in it today is a baseline, and the baseline is *nine of 27 answered
   without inference*, not the 21-of-a-population figure quoted from the
   2026-08-22 sweep.
2. **A card resolved at step 1 or 2 is routed, not reviewed.** DRE-2646 is the
   proof. If the pipeline ever needs "is this card any good" as well as "who
   builds it", that is a second question and the precedence does not answer it.
3. **Nothing was moved, on purpose.** These 27 cards are a reference population,
   not cards the critic gated. Running `escalate` over them would move
   eighteen live Portico cards to Planning on the strength of an audit rehearsal.
   The write path is built and tested (`tests/test_critic_score.py`); it belongs
   to a real pass, not to this one.

## Reproducing this

```
python3 scripts/critic_score.py check     # the reference validates itself
python3 scripts/critic_score.py score     # 27 full reads, one card at a time
```

The reads are serial through the one `LINEAR_API_KEY` — no fan-out. On
2026-08-22 two processes contending for a single credential killed a paid run 23
turns in, and a bounded pass that takes longer is the cheaper failure. There is
no schedule: like the groomer (D5), this runs when someone asks for it, which is
the only way to run it while the fleet is quiet.

Every description is fetched with a per-card `issue(id:)` query. Linear's list
API truncates a description at 500 characters and says nothing about it, and 63
cards in this workspace were edited after creation — a retraction appended to
the end of a long card is invisible to every cheap reader in the pipeline.
`score()` refuses a body it did not read that way rather than scoring the top of
a card.
