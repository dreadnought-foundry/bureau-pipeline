# Architecture standard — the canonical system shape

The shape every agent builds within, and the load-bearing decisions that are
already settled. Build to these; don't relitigate them per card.

## The system (cards in, software out)
```
Linear card → relay Lambda → repository_dispatch on the product repo
  → thin stub workflow → reusable workflow in bureau-pipeline:
      agent-task   card → engineer/frontend/devops agent → PR (author = bureau-bot)
      qa-review    PR → adversarial critic → plain-English verdict
      verify       UI/multi-system PR → independent scenario test → PASS/FAIL
      merge-gate   CI green + verdict APPROVE → qa-bot merges
      linear-sync  merge event → card Done (GitHub's truth, not an agent claim)
      plan         epic → collision-free sub-issues + plan comment
      medic        failed run → retry once → diagnosis agent → Linear
      reconcile    ~15-min sweep: dependency gate + WIP cap + unstick
      agent-fix    critic REQUEST_CHANGES / merge conflict → fixing agent
```
- **One source of truth:** the reusable workflows live in
  `dreadnought-foundry/bureau-pipeline`; product repos carry only ~20-line stubs
  consuming `@main`. A fix here propagates everywhere on the next trigger.
- **The relay is dumb** — it verifies the HMAC signature, drops replays/stale
  events, routes the repo (explicit `**Repo:**`/`repo:` → inferred → default),
  and dispatches. All intelligence and enforcement live in the GitHub workflows.

## The human contract
The CEO is non-technical for code and does only three things: **writes
cards/epics, approves plans (moves an epic to In Progress), reads plain-English
critic verdicts.** Nobody human reads a diff or clicks merge in the normal flow.

## Two-robot safety (enforced by identity)
`agent-bureau-bot` **authors** PRs; `agent-bureau-qa-bot` **merges**. Author ≠
merger is enforced by *different GitHub identities*, not a policy a bot could
ignore. Merge happens only on **CI green AND critic verdict APPROVE**; the card
goes Done off GitHub's merge event.

## Operator does DevOps (the load-bearing boundary)
Agents have **no cloud credentials.** The DevOps agent *authors* CDK / CI /
migrations / monitors and runs only credential-less checks (`cdk synth`/`diff`,
tests); the **operator runs every `cdk deploy`, migration, and rollout**.
Enforced by a hard classifier/permission block, not just a brief.

## Settled stack decisions
- **Vite + React SPA + shadcn/ui — NOT Next.js — for a gated cockpit.** SSR/SEO
  are irrelevant behind a login wall. Only a public/marketing site may reach for
  Astro (evaluate before Next.js). State via zustand, routing via react-router.
- **Auth proxy: httpOnly cookie in, Bearer out.** The SPA calls same-origin
  `/graphql`; the proxy promotes the cookie to a Bearer server-side. The token
  never enters the bundle. **Serve `/assets/*`, manifest, favicon UNGATED** — gate
  them and the browser gets HTML where it expects JS ("Failed to load module
  script") and nothing boots. curl misses this; tests must fetch a real
  `/assets/*.js` and assert JS content-type.
- **Backend: FastAPI + Strawberry GraphQL + SQLAlchemy 2.0 + Alembic + Postgres
  16, driven via psycopg3** (`postgresql+psycopg`, never a bare scheme). GraphQL
  for the UI, thin REST for agents; UPPERCASE enums; JSONB.
- **Multi-tenancy from day one:** `NOT NULL tenant_id` on every tenant-owned
  table from the first migration; a `TenantScopedStore` with **no WHERE-less
  methods**; identity→tenant via a `tenant_membership(sub, tenant_id, role,
  platform_operator)` table by Cognito `sub`, never a JWT custom claim.
- **AWS shape (CDK, us-west-2):** App Runner + Aurora Serverless v2 (RETAIN) +
  Cognito (invite-only) + Route53/ACM (CloudFront certs us-east-1). All infra is
  code in `infra/`.

## Naming rule that bites (DRE-1494)
Two disjoint vocabularies, never mixed:
- **Plan / Entitlement** = a per-tenant paid tier (DB `entitlement`, GraphQL
  `tenantPlan`). Use these for all tenant work.
- **Subscription / account / `costUsage`** = the **Claude/LLM billing** pool only
  (operator-gated). The bare words "subscription"/"account" are FORBIDDEN for
  tenant entitlements — a customer must never see the operator's Claude billing.

## Multi-project / scaling
The pipeline serves many product repos from one relay + one bureau-pipeline.
Onboarding a repo = copy the stubs, write `.github/bureau/overrides.md`, set the
secrets, install both Apps, register the slug with the relay (and mirror it
byte-for-byte into `validate_card.py` `VALID_SLUGS`). No silent killers: every
hard limit (Actions budget, LLM token pool, CI-red-on-main, migration drift)
gets a WARN at ~80% and a loud CRITICAL, in the console Alerts panel.

Fuller detail lives in `architecture/agent-bureau-architecture.md` and
`architecture/new-project-playbook.md` in the agent-bureau repo.
