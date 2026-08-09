# `config/` — the data files the pipeline reads at run time

Two canonical files live here. Both exist for the same reason: the workflows
that read them run in a product repo's GitHub Actions with **no AWS credentials**
and **no token for a private repo**, so anything they need must be a file in the
public `bureau-pipeline` checkout they already do (`.bureau-pipeline` @ `main`).
Neither is ever a runtime lookup.

- **`models.yaml`** — which model each agent runs on (see below).
- **`repo-map.json`** — the relay's routing snapshot (see further below).

---

# `models.yaml` — the ONE model config (DRE-2316)

`models.yaml` is **the only file a human edits to change which model an agent
uses**. It declares named, ordered fallback **ladders** (best → worst), assigns
every agent to one, and lists the ids that stay *readable but not selectable*
(retired ids, and ids excluded by cost policy).

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
