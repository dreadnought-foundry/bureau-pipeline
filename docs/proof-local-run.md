# A local run of the released commit — `.github/bureau/proof-local.json`

A proof card's on-screen step is written as two observations (`briefs/planner.md`):
the request it makes, observed live, and its screen, observed in a browser on
a local run of the released commit. That second half needs the repo to say
three things nothing in the pipeline can guess:

- how the checked-out commit is served locally,
- which release surface decides what "the released commit" is,
- which page a `Local screen:` criterion opens.

The repo says them in `.github/bureau/proof-local.json`, beside the
`setup.sh` and `release.json` the proof workflow already reads from that
directory. `scripts/proof_local.py` is the file's one reader (DRE-6024). A
repo with no file is a normal answer — most repos have no on-screen steps — and
its `Local screen:` rows stay `Not observed.`

This page is the contract. The browser runtime and the workflow steps that act
on it are the sibling cards' work: DRE-6025 installs the browser (Playwright,
in a virtual environment of its own) and adds the `proof-task.yml` steps, and
DRE-6047's `scripts/proof_session.py` serves the released commit, signs in and
opens the pages. Nothing in this card starts a process or opens a browser.

## The schema

```json
{
  "surface": "<a key of .github/bureau/release.json's surfaces>",
  "node_dir": "<dir inside the released worktree naming the node version; optional>",
  "setup": "<shell command run once in the released worktree before start; optional>",
  "start": "<shell command that serves the app from the released worktree, kept running>",
  "ready_url": "http://127.0.0.1:<port>/<path>",
  "ready_timeout_seconds": 180,
  "pages": {"<key>": "/<path>"},
  "login": {
    "command": "<shell command, run from the root of the default-branch checkout>",
    "form": {"username": "<css selector>", "password": "<css selector>", "submit": "<css selector>"}
  }
}
```

`surface`, `start`, `ready_url` and `pages` are required; `node_dir`, `setup`,
`ready_timeout_seconds` and `login` are optional. Any other key is refused, so a
typo is named rather than ignored.

| Key | Rule | What the proof run does with it |
| --- | --- | --- |
| `surface` | A key of `release_train.surfaces()` over the repo's `.github/bureau/release.json`. | Resolves the released commit: the newest tag across the surface's `tag_series`. |
| `node_dir` | A directory inside the released worktree — relative, no `..`. | The workflow installs the node version that directory's `.nvmrc`, `.node-version` or `package.json` `engines` names. |
| `setup` | A one-line shell command. | Runs once in the released worktree before `start` — a dependency install, say. |
| `start` | A one-line shell command. | Runs in the released worktree and is kept running while the pages are opened. |
| `ready_url` | `http://127.0.0.1:<port>/<path>` — plain http, the loopback address, an explicit port, a path. `localhost` is refused: it can resolve to `::1` while the app listens on 127.0.0.1. | Polled until it answers; the local run is local. |
| `ready_timeout_seconds` | A positive whole number; 180 when absent. | How long `ready_url` is polled before the run gives up. |
| `pages` | A non-empty object of page key → path. Keys are letters, digits, `_`, `-` or `.`; every path opens with `/` and never with `//` or `/\`. | A `Local screen:` criterion names a page by its key; the run opens `ready_url`'s origin plus that path. |
| `login` | Optional. `command` and a `form` with all three of `username`, `password`, `submit` as CSS selectors. | Runs `command` for a throwaway sign-in, then fills and submits the form. |

## Two trees, and which command runs in which

A proof run holds two checkouts of the repo:

- **The default-branch checkout** — the workflow's own checkout,
  `$GITHUB_WORKSPACE`, the working directory of every step, where
  `.github/bureau/setup.sh` has already run and where the run writes its
  record.
- **The released worktree** — the one `proof_browser.py prepare` (DRE-6025)
  adds at `$RUNNER_TEMP/proof-local-run` for the released commit.

`setup` and `start` run in the released worktree, because they serve the
released commit, and `node_dir` names a directory inside it.

`login.command` runs from the root of the default-branch checkout, with the
job's environment — the AWS session the workflow assumed — plus
`PROOF_LOGIN_FILE`. Never in the released worktree: the released tag does not
have to carry the login script, and the script a repo declares is the one on
its default branch beside its other proof scripts.

The command brings its own runner and dependencies. The workflow installs node
for `node_dir` only, so a command that needs more — a TypeScript runner, a
package install — names it in the command itself, or relies on what the repo's
`setup.sh` already installed.

## What `login.command` writes and prints

It writes one JSON object to the path in `$PROOF_LOGIN_FILE`:

```json
{"username": "…", "password": "…", "identity": "<name>",
 "ration": {"spent": 1, "cap": 5, "remaining": 4, "date": "<YYYY-MM-DD, Pacific>"}}
```

What it prints is one rule: the run takes **the last non-empty line of its
standard output** as the product's status line when the command succeeds, and
as its refusal line when it exits non-zero. Earlier lines are ignored — a
command that begins with `npm ci` is within the contract — and standard error
is never quoted. Nothing secret goes on either stream. For Portico (DRE-6033)
the line reads `hosted sign-in ration: <spent> of <cap> spent today
(<YYYY-MM-DD> PT), <remaining> remaining`, and the refusal is the same shape
with `<cap> of <cap>` and `0 remaining`. `scripts/proof_session.py login`
(DRE-6047) runs the command with exactly that working directory and reads
exactly that line.

## The released commit

The released commit is read, never assumed, in the release train's own terms:
the newest tag across the declared surface's `tag_series`
(`release_train.newest_tag`, on the checkout `proof-task.yml` fetches at depth
0), with the commit it points at. `main` is not released — a surface with no
tag resolves to nothing and the reader says `no release yet for surface
<name>`; it never falls back to the default branch.

## The reader

`scripts/proof_local.py` runs on the runner's own `python3` — the interpreter
every other `python3 .bureau-pipeline/scripts/…` step in `proof-task.yml` runs
on. The workflow has no setup-python step and none is added for this, so the
reader imports the standard library and `release_train` and nothing
pip-installed.

- `python3 scripts/proof_local.py check [--declaration .github/bureau/proof-local.json]`
  — exit 0 and `proof-local: ok — surface <name>, <k> page(s), login <declared|none>`
  when valid; exit 0 and `proof-local: none — no .github/bureau/proof-local.json`
  when absent; exit 1 and `proof-local: invalid — <reason>` otherwise, the
  reason naming the key that is wrong.
- `python3 scripts/proof_local.py read [--declaration …] --github-output <path>`
  — appends exactly `declared=true|false`, `node_dir=<dir or empty>` and
  `surface=<name>` to the GitHub output file. An invalid declaration writes
  nothing and exits 1; no value can carry a second line.
- `python3 scripts/proof_local.py resolve [--declaration …] [--repo-root .]`
  — prints `proof-local: released — <surface> at <full sha> (<tag>)` and exits
  0, or `proof-local: no release yet for surface <name>` and exits 2. A repo
  with no declaration prints the `none` line and also exits 2 — there is no
  released commit to run. An invalid declaration exits 1.

From Python: `proof_local.load(path) -> Declaration` (attributes `surface`,
`node_dir`, `setup`, `start`, `ready_url`, `ready_timeout_seconds`, `pages`,
`login`), `proof_local.released(repo_root, declaration) -> Released(sha, tag,
surface) | None`, and `proof_local.DECLARATION`.

## A worked example

Portico's web app, served by Vite from `web/`, with a hosted sign-in:

```json
{
  "surface": "portals",
  "node_dir": "web",
  "setup": "npm ci --prefix web",
  "start": "npm run dev --prefix web -- --host 127.0.0.1 --port 5173 --strictPort",
  "ready_url": "http://127.0.0.1:5173/",
  "ready_timeout_seconds": 180,
  "pages": {
    "home": "/",
    "documents": "/documents"
  },
  "login": {
    "command": "npm ci --prefix scripts/proof && node scripts/proof/sign-in.mjs",
    "form": {
      "username": "input[name=username]",
      "password": "input[name=password]",
      "submit": "button[type=submit]"
    }
  }
}
```

A criterion on that repo's proof card reads `Local screen: on the documents
page of a local run of the released commit, …`; one naming a page the file
does not declare is `Not observed.`
