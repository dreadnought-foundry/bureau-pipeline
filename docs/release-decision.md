# The release train's decision, as a deployment

<!-- GENERATED FILE — do not edit. Source: scripts/release_decision.py.
     Regenerate with `python3 scripts/release_decision.py render`. -->

The train decides something about every declared surface on every run. This page is the shape that decision is SENT in: one GitHub deployment plus one deployment status, whose payload is the decision as data. It is rendered from `scripts/release_decision.py`, so it cannot drift from what is posted.

## Why a deployment

GitHub delivers a deployment as `deployment` and `deployment_status` webhook messages — the two messages the Record keeps (DRE-4761), so this shape rides a subscription and an App permission that are already planned. It also keeps the train's own verdict OUT of the check-run population the green-at-SHA classifier and the merge gate read (`merge_gate.gating_check_runs`): DRE-3263 spent a card excluding self-authored checks on the commit being judged, and a check run here would hand that problem straight back.

A deployment created with the run's own `GITHUB_TOKEN` **does not start any workflow** — GitHub does not fire a workflow from an event that token created, `workflow_dispatch` and `repository_dispatch` excepted. That is the rule that keeps the train from triggering itself, and it is why the decision can be written on every run.

## The two messages

`POST /repos/{repo}/deployments` — `<line>` is the description below, `<record>` the payload below:

```json
{
  "ref": "<sha>",
  "environment": "release-train",
  "task": "release-train-decision",
  "auto_merge": false,
  "required_contexts": [],
  "description": "<line>",
  "payload": "<record>"
}
```

`POST /repos/{repo}/deployments/{id}/statuses`:

```json
{
  "state": "<state>",
  "environment": "release-train",
  "description": "<line>",
  "log_url": "<run_url>",
  "auto_inactive": false
}
```

`<line>` is `release-train-decision/1 <act> <code> <surface>`, cut to 140 characters.

## The record, field by field

Every key is present in every record; a null is explicit.

| Field | Carries |
| --- | --- |
| `schema` | always `release-train-decision/1`, and the first token of every description too, so a consumer can filter on the line as well as on the payload |
| `repo` | `owner/name` — the caller the train ran in |
| `surface` | the declared surface's name, as the caller's `release.json` spells it |
| `act` | one of `release`, `no-op`, `held`, `refuse` — `Decision.act`, and what the status's `state` follows from |
| `code` | `Decision.code`, the train's own vocabulary, never restated here — the closed set is `CODES` |
| `reason` | `Decision.reason` — the sentence the run's log line carries, verbatim and never reworded |
| `phase` | `plan` or `release`, whichever of the train's two passes decided this |
| `sha` | the commit the decision is about |
| `head` | the head the plan walked from |
| `deployed` | the surface's newest tag BEFORE the decision — what it stands at; `null` when its series holds none |
| `version` | the tag cut, or `null` when nothing was cut; the train cannot know the next number before the surface script chooses it |
| `hand_act` | the one command a person's hand clears this decision with, or `null` when nobody's hand clears it |
| `re_arm_at` | the UTC minute the run re-armed itself for (`Decision.re_arm_at`), or `null` |
| `run_id` | the Actions run that decided, as a number |
| `run_attempt` | which attempt of that run, as a number |
| `run_url` | that run's URL, and the status's `log_url` |
| `event` | the GitHub event name the run fired on |
| `decided_at` | when the decision was made, UTC, `YYYY-MM-DDTHH:MM:SSZ` |

## The state follows the act

| `act` | the status's `state` |
| --- | --- |
| `release` | `success` |
| `refuse` | `failure` |
| `held` | `inactive` |
| `no-op` | `inactive` |

A hold and a no-op are the train working, so both are `inactive` — reported, not failed. Only a refusal is `failure`.

## What a person's hand clears

`hand_act` answers one command for the codes a person clears, and `null` for every other — nobody's hand clears a spacing hold, a commit that is still checking, or a release that worked.

| Code | The hand act |
| --- | --- |
| `held` | `gh variable delete RELEASE_HOLD --repo <owner>/<name> — or, when the fleet-wide brake is the one that is set, gh variable delete RELEASE_HOLD --org <owner>` |
| `channel-current` | — nobody's hand clears it |
| `channel-advancing` | — nobody's hand clears it |
| `channel-blocked` | — nobody's hand clears it |
| `channel-unknown` | — nobody's hand clears it |
| `no-script` | — nobody's hand clears it |
| `auto-false` | `gh workflow run release-train.yml --repo <owner>/<name> -f surface=<surface>` |
| `current` | — nobody's hand clears it |
| `spacing` | — nobody's hand clears it |
| `window` | — nobody's hand clears it |
| `ci-pending` | — nobody's hand clears it |
| `ci-red` | — nobody's hand clears it |
| `ci-absent` | — nobody's hand clears it |
| `walk-bound` | `gh workflow run release-train.yml --repo <owner>/<name> -f surface=<surface>` |
| `deferred` | `deferred: the line <surface>'s own script printed — this record's reason carries it verbatim, and it names the person the deployment is owed to` |
| `release` | — nobody's hand clears it |
| `released` | — nobody's hand clears it |
| `script-failed` | `<the surface's declared rollback>` |
| `no-tag` | `<the surface's declared rollback>` |
| `tag-not-annotated` | `<the surface's declared rollback>` |
| `tag-elsewhere` | `<the surface's declared rollback>` |
| `unreadable` | — nobody's hand clears it |

The brake is the repository variable `RELEASE_HOLD`, which resolves against the caller repo AND the organization — which of the two is set is invisible from the decision, so the one string names both routes (`standards/release-train.md`).

## The vendor's limits, and the three flags

* **`description` is capped at 140 characters** by GitHub, on the deployment and on the status alike. The line is cut to it rather than refused, and the payload carries the whole sentence in `reason`.
* **`auto_merge: false`** so GitHub does not try to merge the default branch into the ref. A decision is about a commit; it is not a request to move one.
* **`required_contexts: []`** so GitHub does not refuse the deployment because a status on the commit is red. A refusal is exactly the decision this message exists to carry, and a red commit is the case it matters most in.
* **`auto_inactive: false`** so no other deployment is marked inactive by a decision — above all the surface script's own stage deployments (agent-bureau DRE-3519). A decision reports; it does not retire anybody else's record.

## What a consumer filters on

**`task`**, which is always `release-train-decision`. The Record's App receives every `deployment` and `deployment_status` message the repository produces, the surface scripts' own rollouts among them; the task is what tells a train decision apart from a rollout, and it is on the deployment where every status carries it too. `environment` is `release-train` and `payload.schema` is `release-train-decision/1` — the second is the one to version against, and the description's first token repeats it so a log line answers the same question.

`check_record` is what a consumer checks a delivered message with: hand it the payload, or the payload plus the `state` and `description` the messages carry beside it, and it names every problem.

## What this never does

`write` never raises and never retries. A message that could not be sent is one clause — `decision not recorded: <why>` — the run is as green as it was, and the tag stays the only record a release depends on. A 403 is named for what it is: `caller stub lacks deployments: write`.

It also reads nothing back, which is what makes a half-written message harmless. A run that dies between the two posts leaves a deployment with no status, and nothing is waiting on one: the next run writes its own pair, every run's decision is its own record, and `auto_inactive: false` means neither retires the other.
