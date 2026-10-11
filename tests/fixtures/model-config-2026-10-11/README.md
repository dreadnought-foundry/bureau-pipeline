# The model files as they shipped on 2026-10-11 (DRE-6899)

A frozen copy of the four files the model tests read, taken from `main` at
commit `480b5cf5a55d6865552a62e8a3a0e30a30db50ae` (the merge of #1004,
2026-10-11 10:55 UTC), byte for byte:

| Here | Copied from |
| -- | -- |
| `models.yaml` | `config/models.yaml` |
| `model-prices.yaml` | `config/model-prices.yaml` |
| `agents.yaml` | `agents.yaml` |
| `model-choices.json` | `config/model-choices.json` |

## Why it exists

The CEO changes a group's default model from Settings › Agents & models, and
every such save edits `config/models.yaml`. A test that pins how selection, the
policy, the change script, the adoption's edit or the effort table BEHAVES on
one particular file — "the critic gets Sonnet 5.5 when everything is up", "a
Sonnet adoption rewrites these rungs" — reads this copy, so a console save can
never turn it red. What it proves is about the code, and this file is its
input.

A test about the committed tree reads the committed files instead, and derives
what it expects from them: the first model of each group's ladder as written
there, never a model named in the test.

## Rules for this directory

- Never edit these files to follow `config/`. They are a record of one day;
  the tests that read them assert what that day's file held. A test that needs
  a different shape builds it from this copy in the test itself.
- `tests/model_config_fixture.py` is the one place the path is named: it
  loads the copy, builds a throwaway tree with it in `config/` (regenerating
  the selector's last-known-good literal from it), and imports the selector
  against it.
- `tests/test_model_config.py::FixtureRecordTest` holds this copy to what
  this README says: the commit and every file are named here, the copy passes
  the policy, the generated `agents.yaml` and `model-choices.json` name the
  same first model of each ladder as `models.yaml`, and a tree built from it
  passes `sync_model_config.py --check`.
