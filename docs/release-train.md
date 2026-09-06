# The release train's data

<!-- GENERATED FILE — do not edit. Source: scripts/release_train.py.
     Regenerate with `python3 scripts/release_train.py render`. -->

Every repo the train serves declares its surfaces in `.github/bureau/release.json`. This page is rendered from the same table the schema check reads, so it cannot drift from what is enforced — run `python3 scripts/release_train.py schema` to check a file, and every refusal names the surface and the field.

## A surface

```json
{
  "surfaces": {
    "<name>": {
      "tag_series": [
        "<glob>",
        "<legacy glob>"
      ],
      "paths": [
        "<dir>/"
      ],
      "script": "infra/release-<name>.sh",
      "rollback": "make rollback-<name> VERSION=<tag>",
      "spacing_minutes": 30,
      "window": "07:00-21:00 PT",
      "auto": false,
      "identity": "<role name>",
      "record": "tag"
    }
  }
}
```

| Field | Shape | What it means |
| --- | --- | --- |
| `tag_series` | list of tag globs | The newest tag across ALL the globs is the deployment record. It is what deploy-lag reads and what the console's Shipped-today panel measures commit ancestry against, so a legacy glob belongs here rather than in anyone's memory. |
| `paths` | list of paths | What counts as changed. `[]` means every commit counts. A surface whose paths are untouched since its newest tag reads current, and the train exits a no-op saying so. |
| `script` | path in the caller, or null | The surface's own release script, called as `bash <script> --surface <name>` with `RELEASE_SHA` in the environment and the identity already assumed. Null only while `auto` is false. |
| `rollback` | command, or null | The one command that puts the surface back, written out so nobody has to derive it at 2am. Null only while `auto` is false. |
| `spacing_minutes` | whole minutes | How long after the newest tag in the series the next release may be cut. Two triggers inside the spacing produce one release. |
| `window` | `HH:MM-HH:MM PT`, or `always` | The hours a release may be cut, read on the America/Los_Angeles clock. A trigger outside the window is a no-op that names it; a hand dispatch runs anyway. |
| `auto` | true or false | Whether the train releases this surface unattended. False means it releases only on a hand dispatch naming it — which is how a supervised first release is run. |
| `identity` | role name | Who the script runs as: the caller's own OIDC role, fed from the one required secret `RELEASE_ROLE_ARN`. On a `channel` surface it names whatever advances the ref, since nothing is assumed. |
| `record` | `tag` or `channel` | `tag` is the default and the deployment record is the annotated tag the script cuts. `channel` is a moving tag another train advances: the release train never runs it, and deploy-lag measures it by compare. |

## What the train decides, in order

One rule set, asked twice: once to build the matrix, and once inside each surface's own concurrency lane. `no-op` and `held` conclude the job green — they are the train working; only `refuse` is red.

| Order | Decision | Act | Says |
| --- | --- | --- | --- |
| 1 | `held` | `held` | `RELEASE_HOLD` is set: every surface exits held, once each, before any surface job exists |
| 2 | `channel` | `no-op` | the surface records a channel another train advances |
| 3 | `no-script` | `no-op` | the surface declares no script |
| 4 | `auto-false` | `no-op` | unattended runs skip an `auto: false` surface; a hand dispatch runs it |
| 5 | `current` | `no-op` | nothing under the surface's `paths` has changed since its newest tag |
| 6 | `spacing` | `no-op` | the newest tag in the series is younger than `spacing_minutes` |
| 7 | `window` | `no-op` | the America/Los_Angeles clock is outside the window; a hand dispatch runs anyway |
| 8 | `ci-red / ci-absent / ci-pending` | `refuse` | a check on the head SHA failed, never ran, or was still pending after 30 minutes — named in the refusal |
| 9 | `deferred` | `no-op` | the script exited 0 printing `deferred: …` — the deployment is owed to a person, and that is not a failure |
| 10 | `released` | `release` | the script ran and the train verified the annotated tag it cut at the released commit |

The brake is the repository variable `RELEASE_HOLD`, read the way `INTAKE_HOLD` is read — see `standards/release-train.md` for where to set it and what the surface script owes.
