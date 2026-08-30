# `config/` — the data files the pipeline reads at run time

Everything here exists for the same reason: the workflows that read these files
run in a product repo's GitHub Actions with **no AWS credentials** and **no
token for a private repo**, so anything they need must be a file in the public
`bureau-pipeline` checkout they already do (`.bureau-pipeline` @ `main`). None
of it is ever a runtime lookup.

- **`models.yaml`** — which model each agent runs on (see below).
- **`repo-map.json`** — the relay's routing snapshot (see further below).
- **`lane-contract.json`** — the board's lanes, their clauses and their
  permitted writers (DRE-2726). `docs/lane-contract.md` is rendered from it.
- **`routing-verdicts.json`** — the routing vocabulary (DRE-2724).
  `docs/routing-verdicts.md` is rendered from it.
- **`planning-shapes.json`** — the planning shape vocabulary (DRE-2843):
  one-off, epic, wave, and per shape the lane it goes to, the actor accountable
  for it there, whether the sweep may promote it and the marks it applies. Read
  through `scripts/planning_shape.py`. Shape is **not** size — `size:XS`…`size:XL`
  mean effort, and a `size:L` one-off is legitimate. `scripts/planning_route.py`
  turns a shape into the route a card takes out of Planning (DRE-2844): the
  destinations below are the ones `plan.yml` sends cards to, and a card carrying
  no shape is refused rather than defaulted.
- **`critic-audit-dre2649.json`** — the held-back review the critic is scored
  against (DRE-2685), transcribed once with a quote per judgement.
  `docs/critic-score-dre2649.md` records the run.

---

# `models.yaml` — the ONE model config (DRE-2316 / DRE-2317)

`models.yaml` is **the only file a human edits to change which model an agent
uses**. It declares named, ordered fallback **ladders** (best → worst), classes
every agent as one of two **role kinds**, and lists the ids that stay *readable
but not selectable* (retired ids, and ids excluded by cost policy).

## Two role kinds — workhorse and advisory

| kind | who | model | why |
|---|---|---|---|
| `workhorse` | engineer, frontend, devops, database-architect, planner, fixer, repairer | cost-appropriate (today Opus) | The hot path. Hundreds of turns per card, every card, every repo — this is what drains the shared rolling session window. |
| `advisory` | critic, verifier, medic | the **strongest** model (today Fable) | Bounded consults at decision points. The critic gates **every unattended merge**; nobody human reads a diff, so a shallow review is a *silent* failure. |

The allocation used to be exactly inverted — the cheapest model judging the most
expensive one's work — and the priciest model sat on the hot path where it could
drain the account. Both halves are fixed by the split, and the second half is
also the structural cost fix: **the strongest model is never on the build path,
so it cannot be consumed at build volume.**

A role is assigned a **kind**, never a ladder name. That indirection is what
stops a future edit from inventing a third ladder and quietly putting build work
on the strongest model.

## Availability is not permission

On 2026-08-09 Anthropic enabled `claude-fable-5`, the availability probe stopped
returning 404, and a best-first ladder promoted the **entire fleet** onto it
inside one TTL window with no human deciding anything. Usage drained and agents
started dying mid-run.

So the rule, now enforced by schema validation in
`model_fallback.policy_errors()` rather than by convention:

- the advisory model must not appear on any build ladder — a config that puts it
  there is **refused**: `sync_model_config.py --check` fails CI red, and the
  selector keeps running the last-known-good ladders instead of honouring it;
- the critic and verifier must stay `advisory`;
- `default_ladder` must be the workhorse ladder, so an unrecognized role lands on
  the cheap side of the fence;
- `discovery.on_new_model` may be `advisory` or `none`. **`workhorse` is
  rejected** — a newly seen model auto-joining the build path *is* the incident.
  `discovery.alert` must be true: the weekly `model-drift` workflow opens one
  Linear card for a human whenever the API offers a model this file does not
  name.

## Every run says which model it used, and why anything above it was skipped

`model_fallback.py select <agent> --explain-file <path>` writes a one-line note
next to the selected id: the model, its kind, and every higher rung that was
skipped with the reason (confirmed unavailable vs. an inconclusive probe). Every
agent workflow records that note in its step summary and, on card-driven runs,
in the `model-attempt:` heartbeat on the Linear card.

A note that starts with `DEGRADED` becomes a `::warning::`. That is the alert
half of the policy: if an advisory role ever falls off the strongest model —
because it is unavailable, or because an advisory budget is introduced and
exhausted — the fallback is the **workhorse** model (the advisory ladder's last
rung, by construction) and the run says so. A silently weakened critic is the
exact failure this policy exists to prevent.

No budget accounting exists today, and none is introduced here. If one is ever
added it must be **per-account**: accounts are per repo, not one fleet-wide
pool — on 2026-08-09 this repo's critic ran green while two other repos' agents
died. The availability cache is in-process, so it already has that property.

The wider design is recorded in `architecture/decisions/adr-model-policy.md` in
**agent-bureau** (PR #2026).

## Who reads it

- **The CI path.** `scripts/model_fallback.py` loads this file and `select()`
  walks the ladder it names for that agent, returning the first model a runtime
  availability probe says is up. Every agent workflow — build agents *and* the
  critic, verifier and medic — takes its `--model` from that step. No workflow
  pins a model id; a test reads the workflow files and fails if one does.
- **The console roster**, indirectly: `agents.yaml`'s per-agent `model:` line is
  a **generated** mirror of this file.

## Generated mirrors and the drift gate

Every copy sits between explicit `BEGIN generated …` / `END generated …`
markers and is produced by one script:

```
python3 scripts/sync_model_config.py          # regenerate the mirrors
python3 scripts/sync_model_config.py --check  # CI: exit 1 if any mirror is stale
```

The mirrors are `agents.yaml`'s `model:` lines and
`model_fallback._FALLBACK_MODEL_CONFIG` — a last-known-good literal used **only**
when the YAML is unreadable, so a truncated checkout degrades instead of
stranding a dispatch. `tests/test_model_config.py` pins the markers, the
byte-for-byte regeneration, the red `--check`, and that editing this file alone
changes what the fleet selects.

## Changing a model

Edit `models.yaml`, run the sync script, commit both. Merging to `main` is
**instantly live fleet-wide** — treat it as a production change. Remember that
availability decides how far *down* a ladder we walk, never how far up: a model
that is not on a ladder is never selected, however available it becomes. Ladder
membership is the spend decision, and it is made here.

---

# `repo-map.json` — the gate's routing snapshot (DRE-1626)

`repo-map.json` is the bureau pipeline's bundled copy of the **canonical routing
snapshot** — `slug → "owner/repo"` — that the Linear→GitHub relay routes on. It
is the read path the Todo-entry card-validation gate (`scripts/validate_card.py`)
uses to derive what a *valid* repo slug is and how a Linear project name maps to
a repo, so onboarding a customer is a **data edit**, not a two-file code change.

## Why a copy lives here

There is ONE source of truth for routing: the relay's SSM parameter
`/bureau/relay/repo-map` in the dreadnought account (us-west-2), seeded from and
mirrored by `config/repo-map.json` in **agent-bureau** (a PRIVATE repo).

The gate runs inside each product repo's GitHub Actions with **no AWS
credentials** and **no token to read agent-bureau's private contents** —
`bureau-pipeline` is checked out as a public repo with no auth. So the gate
cannot read SSM or the private canonical file at runtime. Instead it reads this
**bundled, published JSON** and derives:

- `VALID_SLUGS` = the snapshot's keys, and
- `_PROJECT_PREFIX_TO_SLUG` = identity over those slugs + the documented product
  nicknames (`bureau→agent-bureau`, `demo→agent-bureau-demo`).

This mirrors exactly what the relay does (`_infer_slug` in
`agent-bureau/cloud/relay/lambda_function.py`), so the relay and the gate stay
byte-aligned by reading the same shape.

## Lockstep is enforced, not hoped for

`tests/test_repo_map_snapshot.py` fails CI if:

- `VALID_SLUGS` / the prefix map drift from this snapshot, or
- the last-known-good fallback literal baked into `validate_card.py` disagrees
  with this file (its two copies of the routing map must agree on an SSM-read
  failure).

The cross-repo half — that this file equals agent-bureau's canonical
`config/repo-map.json` — is enforced on the agent-bureau side, where both files
sit in one checkout.

## Onboarding a customer (updating this file)

When you onboard a repo, the relay's SSM map and agent-bureau's canonical
snapshot are updated by `scripts/onboard-customer.py` (in agent-bureau). To keep
the gate in lockstep, add the **same** `slug → "owner/repo"` entry here in a
`bureau-pipeline` PR (and CI's divergence test will hold you to it). A safe
fallback in `validate_card.py` means a briefly-missing entry degrades to the
last-known slug set rather than hard-failing the gate.
