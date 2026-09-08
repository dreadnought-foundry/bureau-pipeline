PLAN-CRITIC: SEND_BACK — the deploy-lag cards collide with One River's DRE-3116/3117, which rewrite the same workflow file
1. DRE-3210 is already shipped — the plan reads a Done child as outstanding work (DRE-3208, DRE-3210)
2. the proof and demo cards' written Blocked-by lines name cards that the split moved out of this epic (DRE-3221, DRE-3222)
3. three cards cite a "07:00 PT schedule the way reconcile.yml does it" precedent that does not exist (DRE-3212, DRE-3214, DRE-3216)

collisions: 1

The working, in plain English for the planner that has to act on these.

The collision is the worst of them because it is the one that cannot be fixed
after the cards are built: two epics rewriting one workflow file conflict on
every merge, whichever lands first.

The Done child is a misread rather than a missing card — the plan describes
work that shipped on 09-05, so an agent dispatched on it would find nothing to
do and close the card having changed nothing.

The Blocked-by lines and the invented precedent are both card text: they say
things about the board and about this repository that are not true, and an
agent has no author to ask.
