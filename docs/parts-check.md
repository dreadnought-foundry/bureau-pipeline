# The duplicate-parts check — `scripts/parts_check.py`

A repo whose design system exists ships shared parts: the components in the
folders its `.github/bureau/design-system.json` declares
([`design-system-declaration.md`](design-system-declaration.md)). A copy is one
of those parts declared again somewhere else in the repo — the local `Notice`
an initiatives page grew on 2026-10-30 beside the shared `Notice` molecule.

`scripts/parts_check.py` (DRE-8841) finds them. It has two commands: `count`
counts every copy in a checkout, and `check` judges a pull request's diff. The
reusable workflow runs `check` ([`parts-check-stub.md`](parts-check-stub.md));
the one-time count runs `count`.

**Nothing is kept.** The copies are computed from the checkout's files on every
run, and no baseline file is written or read. A copy that is deleted is gone
from the next count; a copy that is added is in it.

The parts are read through `scripts/design_parts.py` (`load`, `catalog`,
`exists`, `is_ui_file`), and components are recognized with the same patterns
the critic's parts review uses (`scripts/parts_review.py`'s `_declarations`).
The diff is read with that script's `parse_diff`.

## The two commands

Both run on the runner's own `python3` with no pip install:

```
python3 <pipeline>/scripts/parts_check.py check --diff <unified diff file> [--repo-root DIR]
python3 <pipeline>/scripts/parts_check.py count [--repo-root DIR] [--repo SLUG] [--json]
```

`<pipeline>` is `.bureau-pipeline` in a product repo's checkout; in this repo
the path is `scripts/parts_check.py`. `--repo-root` is the working directory
when absent.

## A copy

Every part in the catalog — its name is the part's file stem, as
`design_parts.catalog` returns it — that is declared again as a component in
any other UI file of the repo is one copy.

- **Any other UI file** is every file with a `design_parts.UI_EXTENSIONS`
  extension (`.tsx`, `.jsx`, `.vue`, `.svelte`) other than the part's own
  "ships at" file, inside the declared folders or outside them.
- A file whose name contains `.test.`, `.spec.` or `.stories.` is never read,
  and neither is any path with a `node_modules`, `.git` or `.bureau-pipeline`
  segment.
- The files are the ones `git ls-files` names — tracked, and untracked but not
  ignored — when the root is a git work tree, and the directory tree otherwise.
- A component is declared by `function <Name>(` or `const <Name> = (…` /
  `forwardRef(…`, optionally exported, with a PascalCase name — the patterns
  `parts_review._declarations` recognizes.
- A copy's line is its first declaration of the part's name in the copy file.
  The part's line is the part's own declaration in its "ships at" file, or
  line 1 when no pattern recognizes it there (a `.vue` or `.svelte` part, say).
- When two parts share a name, the first in catalog order is the part, and
  the other's file is a copy of it.

## A copy's identity

A copy's identity is (repo, part name, copy file): the pair (`part`,
`copy_path`) with the repo. A file that declares the part's name twice is one
copy, at its first declaration.

## `check` — the pull request's diff

`check` judges only what the pull request's diff adds, read beside the
checkout the review holds (DRE-8949). It fails for either of two reasons and
names every one.

1. **A copy the diff adds.** A copy from `count` whose own declaration sits on
   a line the diff adds, or for which the diff adds the part's file (its old
   side `/dev/null`) — a new shared part whose name an existing local
   component already uses. A copy whose declaration is on an untouched line,
   of a part whose file the diff does not add, never fails a pull request, so
   an existing copy elsewhere in the repo does not hold the first pull request
   after the check goes live, and neither does a diff that edits the shared
   part's body or its declaration line.
2. **A UI file that imports none of the shared parts.** A UI file the diff
   adds, or in which it adds a line declaring a component, outside every
   declared folder, not a `.test.`/`.spec.`/`.stories.` file, and in which no
   `import` statement imports a catalog part (the import rule, below). A
   component is declared on an added line when a `parts_review._declarations`
   match opens on a line `added_lines` numbers as added; a file the diff adds
   has every line added, so a new file is judged whole. A diff that adds a
   route line to a router shell that declares a component and has no part to
   import does not fail. The file is read from the checkout; when the
   checkout does not hold it, from the diff's added and context lines.

A repo whose design system does not exist passes with a log line saying so;
so does a diff that adds or changes no UI file, and a repo whose design system
exists and ships no part.

## The import rule

An `import` statement imports a catalog part when either:

- **a binding names a part.** A named binding counts by its imported name, so
  `import { Notice as N } from "…"` counts as `Notice`, and a default import
  counts by its local name, so `import Notice from "…"` counts as `Notice`
  whatever the specifier. `import { default as Notice }` is a default import
  and counts by `Notice`. A `type` modifier is read past. Any specifier
  qualifies — an alias such as `@/components/molecules` as much as a package.
- **a relative specifier resolves into a declared folder.** A specifier
  opening `./` or `../` is resolved against the file's own folder, and counts
  when the result is a declared folder or inside one — a part file, a
  subfolder part, or the folder's own index. A namespace import
  (`import * as ui from "../../components/atoms"`) or a bare
  `import "../atoms/Button"` counts this way only.

A commented-out import does not count, and neither does a re-export
(`export { Notice } from "…"`) or a dynamic `import(…)`.

## The lines `check` prints

`check` prints exactly one line to stderr, opening `parts-check:`
(`parts_check.CHECK_TAG`), and exits as follows:

```
parts-check: pass — <u> UI file(s) checked, 0 copies added, 0 UI files importing no shared part      (exit 0)
parts-check: does not apply — <reason>                                                              (exit 0)
parts-check: fail — <k> copy(ies) added, <m> UI file(s) importing no shared part                    (exit 1)
parts-check: UNKNOWN — <problem>                                                                    (exit 1)
```

`<u>` is the number of UI files the diff adds or changes. The reasons `does
not apply` gives are:

- `the repo's design system does not exist (<why>)`, where `<why>` is
  `design_parts.exists`'s — for a repo with neither file, `no
  .github/bureau/design-system.json and no design/LOCK.json`;
- `the diff adds or changes no UI file (.tsx, .jsx, .vue, .svelte)`;
- `no part ships in the folders read from <source>`.

`UNKNOWN` is a diff that is missing, empty or cannot be parsed, a declaration
that cannot be read, or any other error; it fails the check, because a check
that cannot read its inputs has not passed.

On `fail` it prints one stdout line per finding, every copy first, sorted as
`count` sorts them, then every UI file in diff order:

```
parts-check: COPY <Part> — shared part declared at <part path>:<line>; copy declared at <copy path>:<line>
parts-check: NO-SHARED-PART <file> — imports none of the shared parts
```

## The lines `count` prints

`count` prints exactly one line to stderr, opening `parts-count:`
(`parts_check.COUNT_TAG`), where `<who>` is `--repo` when given and the repo
root otherwise (resolved to an absolute path):

```
parts-count: <who> — <n> copy(ies) of shared parts from <source>
parts-count: <who> — none — <reason>
parts-count: <who> — UNKNOWN — <problem>      (the only exit 1)
```

`<source>` is the file the folders were read from,
`.github/bureau/design-system.json` or `design/LOCK.json`. A design system
with parts and no copy is `0 copy(ies)`; `none` is a design system that does
not exist or ships no part, with the same reasons `check` gives.

Without `--json`, stdout carries one line per copy, in the order below:

```
COPY <Part> — shared part declared at <part path>:<line>; copy declared at <copy path>:<line>
```

With `--json`, stdout is one object:

```
{"repo": "<slug or null>", "source": "<declaration source or null>", "reason": "<why none, or null>",
 "copies": [{"part": "Notice", "layer": "molecules", "part_path": "<path>", "part_line": 3,
             "copy_path": "<path>", "copy_line": 41}]}
```

`copies` is sorted by (`part`, `copy_path`, `copy_line`). The pair (`part`,
`copy_path`) with the repo is a copy's stable identity. `layer` is the part's
layer. On `UNKNOWN` the object carries an empty `copies` and the problem as its
`reason`; the exit code, 1, tells it apart from `none`.

## Where the lines are kept

Both tags are declared in `config/proof-reach.json`'s `log_lines`, each
`printed_by` `scripts/parts_check.py` with its constant, so a proof criterion
may quote them.
