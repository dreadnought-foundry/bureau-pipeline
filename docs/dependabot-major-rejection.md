# Rejecting a Dependabot major — the operator playbook (DRE-2118)

The merge gate auto-merges only grouped minor/patch updates; every major
arrives as its own single-dependency PR that the gate parks for a human
(`decision=human`, `tests/test_merge_gate_dependabot.py`). Accepting one is
easy — merge it. **Rejecting one has exactly one safe path**, because both
`@dependabot` comment commands are booby-trapped by vendor behavior we have
already paid for, and a plain close on its own does not stick: Dependabot
re-files the same major on its weekly schedule.

## The one safe path

### Step 1 — add the config ignore stanza (a normal PR)

In `.github/dependabot.yml`, under the ecosystem's update entry, add (or
extend) an `ignore` list with one block per rejected dependency:

```yaml
    ignore:
      - dependency-name: "<the dependency>"
        update-types: ["version-update:semver-major"]
```

A commented copy of this template lives in the config itself, next to where
it gets pasted. The shape is load-bearing:

- **`dependency-name` is required** — an ignore rule without it swallows the
  whole ecosystem.
- **`update-types` must be exactly `["version-update:semver-major"]`** — it
  rejects ALL future majors for that dependency while minors and patches keep
  flowing through the grouped auto-merge lane. Omit it and Dependabot stops
  proposing those too.

Ship the stanza as a normal PR through the normal rail;
`tests/test_dependabot_config.py` pins the shape.

### Step 2 — close the Dependabot PR

After the config PR merges, close the parked major PR with a **plain GitHub
close — no comment command**. The merged ignore rule is what makes the close
durable: Dependabot will not re-file that dependency's majors on the next
weekly run.

Do the steps in this order. Closing first is harmless but pointless — without
the config rule the PR just comes back weekly.

## Do NOT use the `@dependabot ignore*` comment commands

- **`@dependabot ignore this major version`** suppresses only THAT major, so
  Dependabot immediately re-files the next major down. Live incident:
  DRE-2064 — closing critic-rejected majors this way triggered a walk-down of
  ~19 re-filed PRs, burning a critic review per rung.
- **`@dependabot ignore this dependency` / `@dependabot ignore`** do not work
  on grouped PRs at all — Dependabot replies "only available on
  single-dependency pull requests" (live incident: DRE-2062). And on a
  single-dependency PR they record the rejection as hidden comment state
  instead of reviewable config.

The config stanza has none of these limits, and it lives in version control
where the next operator can see and reverse it.

## Reversing a rejection

Delete the dependency's ignore block in a normal PR. Dependabot proposes the
current major again on the next weekly run.

## Currently rejected majors

None yet — the config carries only the commented template. This section is
the ledger: when a stanza lands, list the dependency and the one-line reason
here in the same PR.
