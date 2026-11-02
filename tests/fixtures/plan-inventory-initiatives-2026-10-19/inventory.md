# Initiatives inventory — a RECONSTRUCTION for the DRE-8612 replay, not the historical file

The 10-19 Initiatives plan and the inventory as it stood that day are not in
this repository. This table is rebuilt from the card's account of that day so
`tests/test_plan_inventory.py` can replay it: the Initiatives screens shipped
without their row chevrons, and nobody checked the planner's cards one-to-one
against the mockup. The chevron row is the one the cards as cut never carried.

| Element | Frame | Detail |
|---|---|---|
| Initiatives page header | #timeline | Title and initiative count |
| Initiative row | #timeline | Name, owner and status pill |
| Initiative row expand chevron | #timeline | Chevron |
| Initiative status pill | #timeline | One color per status |
| Empty state | #empty | No initiatives yet |
