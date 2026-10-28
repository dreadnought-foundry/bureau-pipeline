# The frozen model files (DRE-6859)

`models.yaml` and `agents.yaml` here are byte-for-byte copies of
`config/models.yaml` and `agents.yaml` as they stood on DRE-6983's branch,
re-frozen once the choices left them:

- commit: `daf3d5ef063ec0431b4d38b6813a694cdf83609a`
- date: 2026-10-28

They carry no choice — no `kinds`, `ladders`, `default_ladder`, `agents`,
`ungrouped` or `projects`, no `model:` or `kind:` line, and no generated
region — only the code-side facts (`session_agents`, `about`, `rules`,
`review_separation`, `effort`, `discovery`, `retired`, `excluded`).

`model-prices.yaml` and `home.json` are still the copies frozen on `main` at:

- choices commit: `1652714673c14b5af0ffcaf85abe390ef6664abe`
- choices date: 2026-10-11

`home.json` is the frozen choices of that commit written as a home document
(DRE-6972, the home contract on DRE-6939), with `written_by: operator` and the
`written_at` of that date (Rule 3); since DRE-6983 it is where the frozen
choices are, and the only place. Every test reaches these files through
`tests/model_fixture.py` — never by spelling this directory's name — and that
module's `fixture_tree` copies a throwaway tree from them with today's
`scripts/`, and never regenerates it (Rule 4).

## Rule 1: what a test may read the model files for

A rule of the policy or of selection is tested against this frozen copy or
against a config built in the test. A test that reads the committed file checks
only what must hold for any saved state and derives any expected value from
that file, never from a literal. No test asserts a choice the console can change
— which model leads a group, which group an agent is in, which agents exist and
in what order, whether a project has a model of its own — because a legitimate
console save changes every one of them and would turn a rule test red.

## Rule 2: the refresh rule

The fixture pairs frozen data with today's code, so a change to the selector,
the schema or the policy that makes the frozen files fail `policy_errors`, or
`home.json` fail `model_home.py check`, re-freezes the fixture in the same pull
request. Copy the files again from that branch, rewrite the sha and the date
above, and nothing else moves, because every test reads the fixture
through `tests/model_fixture.py` and derives every id from it. A schema
addition today's code tolerates (a missing optional key) needs no re-freeze. The directory is named for what it is rather than for a date, so a
re-freeze overwrites it in place and renames nothing.

## Rule 3: the frozen document

Since the selector selects on the home or the defaults in the code (DRE-6939),
a scenario is a home document, not an edit of a tree's `models.yaml`. A rule of
the policy or of selection is proved against the frozen document or a document
built in the test — `fixture_home()`, edited with `lead_with`, `move_agent` or
`with_project` and handed to a tree with `home_env` or
`fixture_module(..., doc=...)`, all in `tests/model_fixture.py`.

`home.json` was produced once (DRE-6972) by running
`python3 scripts/model_home.py render-defaults` inside a fixture tree whose
literal the generator of that day had regenerated from the frozen
`models.yaml`, with `written_at` then fixed to the date above. It is never
compared with what `render-defaults` prints now: that stamps the moment it
runs, and renders the live defaults in the code, `CODE_DEFAULTS`, which are
not the frozen choices. What pins it is the choices commit above: its groups,
agents and projects are that commit's, and `model_home.py check` accepts it
inside a fixture tree. No generator is left to put the frozen choices in a
tree's literal (DRE-6982), and since DRE-6983 the frozen files carry none to
compare it with, so when `render-defaults` changes shape the document is
rewritten by hand from the frozen files of the choices commit, in the shape
`render-defaults` prints, and the same check holds it.

## Rule 4: how a tree is built

A tree is copied and never regenerated. `fixture_tree(dest)` copies today's
`scripts/` and the frozen files and runs nothing: the generator that once
rewrote a tree's literal is gone (DRE-6982), and the defaults in the code are
the tree's own hand-written `CODE_DEFAULTS`. `regenerate` stays a keyword that
defaults to `False`, so the callers that pass `regenerate=False` are
untouched, and `regenerate=True` raises `ValueError`.

A rule of the policy or of selection is proved on a document, in a tree
`fixture_tree` copies. A test that reads the committed files checks only what
must hold for any saved state, and derives every expected value from those
files. The planner's path (`PLANNER_PATH`, DRE-6990) and the rest of the model
tests (`HOME_DOCUMENT_TREES`, DRE-8018), both in
`tests/test_model_fixture.py`, are held to this: every tree they build passes
`regenerate=False`, none names the generator, and none reads a choice off the
frozen `models.yaml`, which carries none — they read it off `home.json`, or
off a tree's own `code_defaults()` when no home is read.
