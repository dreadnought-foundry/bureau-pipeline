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
  proposing those too — which is wrong for rejecting a major, and is exactly
  the rule that holds a deliberate pin (the section below). Those are the two
  shapes an ignore rule takes here, and there is no third.

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

## Holding a dependency at a deliberate pin (DRE-4336)

A different problem with the same cure. Sometimes a dependency is pinned
**below its newest release on purpose** — a vendor release broke us, and the
upgrade is its own card with its own proof. Dependabot does not know that. It
proposes the newer release inside the weekly grouped minor/patch PR, the pin's
guard tests go red by design, the critic answers REQUEST_CHANGES, and nothing
in the fleet fixes a `dependabot/*` branch. The PR can neither merge nor go
away; a plain close re-files it next week; and every safe bump grouped beside
the held one is stuck with it.

Live incident: bureau-pipeline #452, 2026-09-19. The sweep proposed
`anthropics/claude-code-action` v1.0.217 → v1.0.226 — past the DRE-3416 pin —
grouped with a safe `aws-actions/configure-aws-credentials` minor.

The hold is an ignore rule naming the dependency with **no `update-types`**:

```yaml
    ignore:
      - dependency-name: "<the held dependency>"
```

- **No `update-types`, on purpose.** The bump being refused is usually a patch
  or a minor, so a majors-only rule would not stop it. Without the key
  Dependabot ignores every version update of that one dependency. (Security
  updates are a separate repo-level setting and are not affected.)
- **`dependency-name` is still required**, and names exactly one dependency —
  never a pattern.
- **Comment the rule** with the card that placed the pin and the card that
  lifts it, and **declare it** in `HELD_PINS` in
  `tests/test_dependabot_config.py`. That test refuses an undeclared rule of
  this shape, requires the rule while the tree still sits at the held sha, and
  fails once the pin has moved and the rule is still there — so a hold cannot
  outlive its reason.
- **It leaves in the PR that lifts the pin.** The upgrade card moves the pin by
  hand, deletes the rule and its `HELD_PINS` row, and Dependabot resumes
  proposing that dependency on the next weekly run.

The order is the same as for a major: the rule merges **first**, then the
stuck Dependabot PR gets a plain GitHub close. Where the grouped PR carried a
safe bump as well, land that bump by hand in the same PR as the rule, so it
does not wait a week to be proposed again.

## Currently held pins

None. The one hold this ledger has carried is lifted:

| Dependency | Ecosystem | Held at | Placed by | Lifted by | Rule added |
| -- | -- | -- | -- | -- | -- |
| `anthropics/claude-code-action` | github-actions | v1.0.217 (`9c5ddab2e6d17b83ea679153b31f1d5f023cf636`) — **lifted 2026-09-25**, pin moved to v1.0.234 (Claude Code 2.1.282) | DRE-3416 — v1.0.218's installer left no launcher and every Claude-running job in the fleet died for 72 minutes | DRE-3417, in the same PR that deleted the rule and its `HELD_PINS` row | DRE-4336, after PR #452 |

Dependabot now proposes `claude-code-action` releases again, inside the
weekly minor/patch group, and each one is a PR the critic reviews and the
harness proves before the channel carries it.

## Currently rejected majors

No per-dependency rejection yet — the config carries the commented template,
and the `github-actions` entry ignores majors for every dependency (`"*"`, the
DRE-2064 house shape), so a major there lands only by a deliberate card. This
section is the ledger: when a per-dependency stanza lands, list the dependency
and the one-line reason here in the same PR.
