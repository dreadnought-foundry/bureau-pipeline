# An epic at Linear's comment cap — what happens, and what to do

Linear holds at most **2,000 comments on one issue**, enforced server-side. The
wave epic DRE-2668 reached that line on 2026-09-08 at 09:35 PT, and for the
rest of that morning every automation that wrote on it failed:

```
'An issue can have a maximum of 2000 comments. This quota is enforced to keep
 the workspace performant.'   meta: {'quota': 'max-comments-per-issue'}
```

Four Phase 7 discovery cards were filed into that epic. Each card was created,
and each then died on the growth record — step two of three — so not one of
them ever reached its `🔎 mid-epic-verdict`, which is the single thing a
mid-epic card cannot promote without. Nothing reported the cap. It surfaced as
a stack trace in whichever job happened to comment next.

This page is the decision, so nobody has to improvise it per job (DRE-3343).

## What the pipeline now does on its own

Three things, none of which need a person:

**The condition is named.** `linear_ops.comment_cap_condition()` classifies the
payload on the PAIR of facts Linear sends — a `QUOTA_EXCEEDED` code and
`meta.quota == max-comments-per-issue` — never on the status, which is the same
400 a malformed query gets, and never on the prose, which is the vendor's to
reword. A `QUOTA_EXCEEDED` for some other quota is deliberately NOT this
condition: it keeps raising, loudly, as an unknown limit should.

**Every comment writer degrades.** `linear_ops.cmd_comment` is the one place a
comment is posted in this repo. At the cap it names the condition on stderr and
RETURNS it instead of raising, so the sweep, the critic and the medic all carry
on. It is a fact about one issue, so it does not arm the process-wide
rate-limit stop — the next card's receipt is unaffected.

**The order changed where it mattered.** `mid_epic._add` now goes card →
sibling verdict → epic growth. The old order was safe against a crash; it was
not safe against a step that fails every time. The growth notice the epic
cannot take is written on the sibling instead, tagged
`mid-epic-growth-capped`, and the epic's growth ARTIFACT still updates — the
cap is on comments, not on the description.

**The sweep warns at 1,800.** `reconcile.report_epic_growth` reads how many
comments each active epic holds, and every pass that finds one at or above
`COMMENT_CAP_WARN` ends with one summary line naming it and the count. Below
the line the sweep says nothing; an unknown count says nothing either, rather
than guessing.

The count is PAGED, and that is not the design anyone wanted. DRE-3343 was
written expecting `comments { totalCount }` — one field on a query the sweep
already makes, no extra request. Linear has no such field:

```
$ … issue(id: "DRE-2668") { comments { totalCount } }
Cannot query field "totalCount" on type "CommentConnection".

$ … __type(name: "CommentConnection") { fields { name } }
edges, nodes, pageInfo
```

`Issue` carries no comment count either. So `linear_ops.comment_count()` pages
comment ids, 250 to a page — and the cap is what makes that affordable: an
issue cannot exceed eight pages, by Linear's own limit. An epic below the line
costs ONE request, because page one comes back with `hasNextPage: false`. Only
an epic near the cap costs eight, and that is precisely the epic the warning
exists for. Measured live on 2026-09-13: DRE-2668, 2,000 comments, 8 requests.
`sweep-spend: report_epic_growth <n> request(s)` is where that cost shows up
per pass (DRE-3639), so a cut can be made against a number rather than a guess.

## What a person does when the warning fires

**Move the receipts to a continuation card.** Create a card titled

    <EPIC> — receipts (cont.)

and point the epic's description at it. Everything that writes a receipt about
the epic from then on writes it there: sweep receipts, medic notices, plan
artifacts. The epic keeps its plan, its children and its growth record — which
is the part that still works at the cap.

**Create it as a TOP-LEVEL card, not a child of the epic.** Three reasons, all
mechanical:

- `mid_epic.refresh_epic_growth` counts children as the epic's scope. A
  bookkeeping card in that roster reads as work that was added to an approved
  plan — the exact silent accretion the growth KPI exists to make visible.
- `reconcile.promote_ready` treats Backlog children of an active epic as
  promotable. A receipts card is not work and must never dispatch an agent.
- The epic's description writes fine at the cap, so a pointer in the plan is a
  more reliable link than the sub-issue list anyway.

**Do not try to empty the thread.** A full thread cannot be emptied — deleting
2,000 comments by hand destroys the record the epic exists to carry, and the
cap is not the problem. A long-running epic reaching 2,000 comments is the
system working; the wave epics are simply first.

## Where the code is

- `scripts/linear_ops.py` — `COMMENT_CAP`, `COMMENT_CAP_WARN`,
  `COMMENT_CAP_CONDITION`, `CommentCapReached`, `comment_cap_condition()`,
  `comment_cap_warning()`, `comment_count()`, and the degrade in `cmd_comment`.
- `scripts/mid_epic.py` — the safe order in `_add`, `GROWTH_CAPPED_TAG`, and
  the epic UUID on `_EPIC_QUERY` that the count is read by.
- `scripts/reconcile.py` — `report_epic_growth` and the sweep's summary line.
- `tests/test_epic_comment_cap.py` — the whole of it, one section per
  acceptance criterion.
