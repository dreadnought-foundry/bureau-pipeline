# Plan — the Initiatives page (a reconstruction of 2026-10-19)

## Summary

We build the Initiatives page from the approved timeline mockup in three cards.

**Inventory:** console/design/approved/initiatives-inventory.md

## The cards

- DRE-9301 — the page header and the initiative rows.
- DRE-9302 — the status pill colors.
- DRE-9303 — the rows read from the initiatives query.

```inventory-coverage
[
  {"inventory": "console/design/approved/initiatives-inventory.md",
   "row": "Initiatives page header | #timeline | Title and initiative count",
   "card": "DRE-9301",
   "criterion": "The page header renders the title and the initiative count."},
  {"inventory": "console/design/approved/initiatives-inventory.md",
   "row": "Initiative row | #timeline | Name, owner and status pill",
   "card": "DRE-9301",
   "criterion": "Each initiative renders as a row with its name, owner and status pill."},
  {"inventory": "console/design/approved/initiatives-inventory.md",
   "row": "Initiative status pill | #timeline | One color per status",
   "card": "DRE-9302",
   "criterion": "Each status pill renders in its status color."},
  {"inventory": "console/design/approved/initiatives-inventory.md",
   "row": "Empty state | #empty | No initiatives yet",
   "deferred": "Initiatives part 2 draws the empty state"}
]
```

## Risk assessment

Low. The work is additive.
