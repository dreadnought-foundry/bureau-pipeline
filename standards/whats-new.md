# What's New standard — one line per pull request, one file per release

The pull request line is the ONE place an entry is ever written. The release's
`whats-new.json` and its Linear release note are both derived from it — never
edit either by hand. `scripts/whats_new.py` parses and validates it all.

## The line

Put exactly one `What's new:` line in the pull request body, in its first lines
and above any quoted card text. Most pull requests say `none`:

```
What's new: none
What's new: <kind>, <audience>: <sentence> [<second sentence>] [(open: </path>)]
```

- `<kind>` is `new`, `improved` or `fixed`. `<audience>` is `everyone`,
  `moderators` or `admins`. Both are required, lowercase, in that order.
- The first sentence is the title and ends with a period. Anything after it is
  the body. `(open: /path)` is optional and names a page in the product: one
  leading `/`, never `//` or `/\`, no spaces or control characters.
- The label may be bold (`**What's new:**`) and the apostrophe curly. It counts
  only at the start of a line and outside fenced code. The first one wins.
- For example: `What's new: improved, everyone: Searching a document now finds
  words inside tables. (open: /documents)`

## The wording

- Write for the person who uses the product: what they can now do or no longer hits.
  No card numbers (`DRE-123`), no pull request numbers (`#123`), no backticks.
- No commit-speak or internal words: PR, pull request, merge, merged, commit,
  refactor, CI, workflow, endpoint, schema, migration, card, branch.
- List a fix only if a person could have hit the bug. Internal work is `none`.
- `audience` is required — the panel shows each person only what is theirs.

## Who writes it

- A build agent writes it because its brief says so (DRE-5510) — a person
  opening a pull request by hand writes it the same way.
- A head branch starting `dependabot/`, `repair/` or `bot/` owes none — it is
  machine-written, nothing a person sees, and counts as `none` (`required_for`).
- The model adoption workflow's pull requests — `agent/<card>-adopt-<candidate>`,
  `agent/model-adoption-<candidate>` and `agent/claude-code-pin-<version>` —
  carry `What's new: none`, written by their own body renderers (DRE-5573).
- The two rescue pull requests carry no line: `scripts/push_rescue.py` opens one
  on an agent's own `agent/` branch, `scripts/deliver_rescue.py` one on
  `agent/<card>-rescued-delivery`. Both bodies are machine-written because the
  agent's was lost — the fix loop answers them like any sent-back pull request.

## Answering a sent-back or held pull request

Add the line to the body (`gh pr edit <n> --body-file <file>`), then push ONE
empty commit to the same branch (`git commit --allow-empty`) — never a diff
change. The critic reviews only a new head and a body edit alone re-runs
nothing (the fix agent's one exception, DRE-5632). A gate hold with an approval
already standing is answered the same way: the approval carries across the
unchanged diff.

## When the rule bites

The rule is on from the instant `config/whats-new-cutover.json` names
(DRE-5576): `{"enforced_from": "<instant with a UTC offset>", "why": "…"}`.
With no file the rule is off — the critic notes a missing line, nobody holds.

- The gate never holds a pull request opened before that instant — it could
  not have known to write the line (`enforced_for`).
- The critic reads no opening time: it names a missing line on any pull
  request it reviews after the switch-on, and the fix loop answers it in one
  automatic round. One approved before the switch-on merges with no line.

## The file

The train collects a release's lines into one `whats-new.json`:

```json
{
  "product": "portico",
  "release": "portals-v1.2.3",
  "shipped": "2026-10-01T14:05:00-07:00",
  "items": [
    {"kind": "improved", "audience": "everyone",
     "title": "Searching a document now finds words inside tables.",
     "body": "", "open": "/documents", "cards": ["DRE-5893"]},
    {"kind": "fixed", "audience": "moderators",
     "title": "Approving a flagged post no longer hides the next one in the queue.",
     "body": ""}
  ]
}
```

- `product` is the repository name after the owner, `release` the train's
  verified tag, `shipped` the Pacific time it was published, with its offset.
  One release per file, no other keys, never an empty `items` — a release with
  nothing to say publishes no file. `cards`, when present, lists the card that
  delivered the entry, read off the pull request's branch and never written by hand.
- The train publishes it as the `whats-new.json` asset of a GitHub Release on
  the product's tag: it creates the release when the tag has none and replaces
  the asset when it does.
- Validate it with `python3 .bureau-pipeline/scripts/whats_new.py validate whats-new.json`
  — `docs/whats-new.example.json` is the example the suite proves valid.
