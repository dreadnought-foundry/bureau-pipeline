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
