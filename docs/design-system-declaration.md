# A repo's design system and its parts — `.github/bureau/design-system.json`

The design-system standard (`standards/design-system.md`) puts a repo's design
work in one `design/` folder, layered as atoms, molecules, organisms and
templates. A product's shipped components rarely sit there. They live in the
app's own source tree — agent-bureau's console keeps them under
`console/web/src/components/` — so the pipeline cannot guess where a repo's
parts ship. It needs the repo to say two things:

- whether the repo's design system exists,
- if it does, which folders each layer's parts ship from.

The repo says them in `.github/bureau/design-system.json`, beside the
`proof-local.json` and `release.json` the pipeline already reads from that
directory. `scripts/design_parts.py` is the file's one reader (DRE-8717). Both
answers are computed from the checkout's files at run time, never from a stored
list: a part added in a pull request is in that pull request's catalog.

This page is the contract. The critic's parts review that reads the catalog is
DRE-8719's work, and its wiring — including the `design-parts:` tag's entry in
`config/proof-reach.json` — is DRE-8721's. The reader itself changes nothing.

## The schema

```json
{
  "parts": {
    "atoms": ["<repo-relative folder>"],
    "molecules": ["<repo-relative folder>"],
    "organisms": ["<repo-relative folder>"],
    "templates": ["<repo-relative folder>"]
  }
}
```

The file holds exactly one top-level key, `parts`. Under it are exactly the four
keys `atoms`, `molecules`, `organisms` and `templates`, every one required. Each
holds a list of repo-relative folder paths, and a list may be empty. A path is
normalized as it is read, so `./ui//atoms/` and `ui/atoms` are the same folder.

`check` refuses the file, and names the problem, for:

| What is wrong | The refusal names |
| --- | --- |
| an unknown top-level key | `` .github/bureau/design-system.json carries unknown key(s) <keys> (the one key is parts) `` |
| no `parts` key | `` .github/bureau/design-system.json has no `parts` key `` |
| an unknown layer key | `` `parts` carries unknown key(s) <keys> (the keys are atoms, molecules, organisms, templates) `` |
| a missing layer key | `` `parts` is missing layer key(s) <keys> `` |
| a layer that is not a list | `` `parts.<layer>` must be a list of folder paths `` |
| an absolute path | `` `parts.<layer>[<i>]` <path> is an absolute path `` |
| a `..` segment | `` `parts.<layer>[<i>]` <path> has a `..` segment `` |
| a `\` in a path, an empty path, or a control character | the entry, as `` `parts.<layer>[<i>]` `` |
| a file that is not JSON, or cannot be read | `` .github/bureau/design-system.json is not valid JSON: … `` or `… cannot be read: …` |

A typo is named rather than ignored, so a repo cannot believe it declared a
folder the reader never saw.

## When a design system exists

A repo's design system exists when either:

- `design/LOCK.json` is present — the lock the standard records the CEO's
  agreement in, or
- the declaration names at least one folder that is present in the checkout.

If `design/LOCK.json` is present and there is no declaration, the folders are
`design/atoms`, `design/molecules`, `design/organisms` and `design/templates`.
A declaration, when present, is always the one read — a lock beside it still
makes the design system exist, but adds no folder to it.

A declaration whose lists are all empty, or name only folders that are absent,
does not make a design system exist. A named path that is a file, not a folder,
is absent.

## What counts as a part

A part is either:

- a file directly inside a declared folder, with the extension `.tsx`, `.jsx`,
  `.vue` or `.svelte`. Files named `index.*`, and files whose name contains
  `.test.`, `.spec.` or `.stories.`, are not parts. A part's name is its file
  stem: `Button.tsx` is `Button`.
- a direct subfolder that holds `index.tsx`, `index.jsx`, `index.vue` or
  `index.svelte`. It is one part, named after the subfolder: `Card/index.tsx`
  is `Card`. A deeper file is never a part on its own, and a subfolder with no
  such index is not a part.

A part's "ships at" path is its file's repo-relative path — for a subfolder
part, its index file.

A UI file is any file with the extension `.tsx`, `.jsx`, `.vue` or `.svelte`,
inside the declared folders or not — `design_parts.UI_EXTENSIONS`, and
`design_parts.is_ui_file(path)` answers it for one path.

## The commands

All three read from `--repo-root`, the working directory when absent:

    python3 .bureau-pipeline/scripts/design_parts.py check   [--repo-root DIR]
    python3 .bureau-pipeline/scripts/design_parts.py catalog [--repo-root DIR]
    python3 .bureau-pipeline/scripts/design_parts.py exists  [--repo-root DIR]

In this repo the path is `scripts/design_parts.py`. They run on the runner's
own `python3` and import only the standard library, so a step that calls them
needs no setup-python step.

`check` validates the declaration and prints one line. It exits 0 for
`design-parts: ok — .github/bureau/design-system.json declares <n> folder(s)`
and for `design-parts: none — no .github/bureau/design-system.json`, and 1 for
`design-parts: invalid — <problem>`.

`exists` prints `yes — <source>` or `no — <reason>` and exits 0. The source is
`.github/bureau/design-system.json` when the declaration names a present
folder, `design/LOCK.json` otherwise. A declaration that cannot be read prints
`unknown — <problem>` and exits 1.

`catalog` prints the catalog block to stdout, in one of three forms:

    PARTS CATALOG: <n> part(s) from <source>
    - <Name> (<layer>) — ships at <path>

one line per part, in layer order, then declared folder order, then by name;

    PARTS CATALOG: none — <reason>

when the design system does not exist, or exists and ships no part; and

    PARTS CATALOG: UNKNOWN — <problem>

when the declaration cannot be read. It always exits 0. It also prints exactly
one line to stderr, so the line lands in the run's log: `design-parts: `
followed by the stdout first line with `PARTS CATALOG: ` taken off. The three
forms are `design-parts: <n> part(s) from <source>`,
`design-parts: none — <reason>` and `design-parts: UNKNOWN — <problem>`.
`design_parts.TAG` is `"design-parts:"`.

The module offers the same answers to a caller that imports it:
`load(repo_root)` returns the declared folders per layer and their source (None
when the repo has neither a declaration nor a lock), `catalog(repo_root)`
returns the parts as `(name, layer, ships_at)`, and both raise
`design_parts.Invalid` naming the problem.

## A worked example — agent-bureau's console

The console keeps its components under `console/web/src/components/`, one
folder per layer:

```json
{"parts": {"atoms": ["console/web/src/components/atoms"],
           "molecules": ["console/web/src/components/molecules"],
           "organisms": ["console/web/src/components/organisms"],
           "templates": ["console/web/src/components/templates"]}}
```

If the atoms folder held only `Button.tsx`, `Button.test.tsx`, `index.ts` and a
`Card/` subfolder holding `index.tsx`, and the other three folders held no part,
the catalog would read:

    PARTS CATALOG: 2 part(s) from .github/bureau/design-system.json
    - Button (atoms) — ships at console/web/src/components/atoms/Button.tsx
    - Card (atoms) — ships at console/web/src/components/atoms/Card/index.tsx

## The scaffold's starting file

The scaffold gives a new repo the declaration with every list empty:

```json
{"parts": {"atoms": [], "molecules": [], "organisms": [], "templates": []}}
```

`check` accepts it, so a new repo has the declaration in place from its first
commit. It names no folder, so its catalog reads `none` until the repo names its
folders:

    PARTS CATALOG: none — .github/bureau/design-system.json names no folder

Naming a folder is the one edit a repo makes when its first part ships.
