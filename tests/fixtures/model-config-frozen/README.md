# The frozen model files (DRE-6859)

`models.yaml`, `model-prices.yaml` and `agents.yaml` here are byte-for-byte
copies of `config/models.yaml`, `config/model-prices.yaml` and `agents.yaml` as
they stood on `main` at:

- commit: `1652714673c14b5af0ffcaf85abe390ef6664abe`
- date: 2026-10-11

That commit is after DRE-6833 merged, so the frozen `models.yaml` carries
`session_agents`. `home.json` beside them is the same frozen choices written
as a home document (DRE-6972, the home contract on DRE-6939), with
`written_by: operator` and the `written_at` of that date (Rule 3). Every test
reaches these files through `tests/model_fixture.py` — never by spelling this
directory's name — and that module's `fixture_tree` builds a throwaway tree
from them with today's `scripts/` and regenerates its mirrors.

## Rule 1: what a test may read the model files for

A rule of the policy or of selection is tested against this frozen copy or
against a config built in the test. A test that reads the committed file checks
only what must hold for any saved state and derives any expected value from
that file, never from a literal. No test asserts a choice the console can change
— which model leads a group, which group an agent is in, which agents exist and
in what order, whether a project has a model of its own — because a legitimate
console save changes every one of them and would turn a rule test red.

## Rule 2: the refresh rule

The fixture pairs frozen data with today's code, so a change to the generator,
the selector, the schema or the policy that makes the frozen files fail to
regenerate or fail `policy_errors` re-freezes the fixture in the same pull
request: when a tree no longer builds, every rule test fails with the sentence
"the frozen model fixture no longer regenerates under today's code —
re-freeze it in this pull request". Copy the three files again from that
branch, rewrite the sha and the date above, and nothing else moves, because
every test reads the fixture through `tests/model_fixture.py` and derives every
id from it. A schema addition the generator tolerates (a missing optional key)
needs no re-freeze. The directory is named for what it is rather than for a date, so a
re-freeze overwrites it in place and renames nothing.

## Rule 3: the frozen document

Since the selector selects on the home or the defaults in the code (DRE-6939),
a scenario is a home document, not an edit of a tree's `models.yaml`. A rule of
the policy or of selection is proved against the frozen document or a document
built in the test — `fixture_home()`, edited with `lead_with`, `move_agent` or
`with_project` and handed to a tree with `home_env` or
`fixture_module(..., doc=...)`, all in `tests/model_fixture.py`.

`home.json` was produced once by running
`python3 scripts/model_home.py render-defaults` inside a tree `fixture_tree`
builds — whose literal is regenerated from the frozen `models.yaml` — with
`written_at` then fixed to the date above. It is never compared with what
`render-defaults` prints now: that stamps the moment it runs, and renders the
live defaults in the code, which every merged save regenerates, so the
comparison would go red on a legitimate save. What pins it is the frozen
files: a test compares its groups, agents and projects with the frozen
`models.yaml` alone, and `model_home.py check` accepts it inside a fixture
tree. Re-render it the same way, inside a fixture tree, when `render-defaults`
changes shape or the three files are re-frozen.
