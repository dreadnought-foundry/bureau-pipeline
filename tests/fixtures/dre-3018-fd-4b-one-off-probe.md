**A deliberate probe, not real work.** Created 2026-09-03 17:55 PT for [DRE-3013](https://linear.app/dreadnoughtfoundry/issue/DRE-3013/operator-proof-the-front-door-every-path-a-card-can-take-from-intake)'s test FD-4b.

Placed in **Planning** with `agent:planner` so the relay dispatches `agent-plan` to agent-bureau-demo, where `plan.yml` stamps the planning shape ([DRE-2843](https://linear.app/dreadnoughtfoundry/issue/DRE-2843/the-planning-shape-vocabulary-one-off-epic-wave-as-data)) and routes on it ([DRE-2844](https://linear.app/dreadnoughtfoundry/issue/DRE-2844/planning-branches-three-ways-on-the-shape-a-one-off-never-reaches-the)).

**The one-off:** add one line to the demo repo's README stating the date the demo pipeline was last exercised. One file. One pull request. No design, no decision, no second card.

**Contract:** the shape stamp reads **one-off**; `planning_route.py exit` sends it straight to the build queue — a routing verdict on this card, no plan artifact, and it **never reaches Green Light**.
**If it lands in Green Light, or a child card is created, or it sits here unplanned, FD-4b FAILS** as written.

Record on [DRE-3013](https://linear.app/dreadnoughtfoundry/issue/DRE-3013/operator-proof-the-front-door-every-path-a-card-can-take-from-intake): the run id, the shape stamp, the verdict, the lane it landed in, the model the heartbeat names, and the time. Cancel once recorded.
