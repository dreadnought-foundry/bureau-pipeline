"""DRE-1626: the gate derives its routing knowledge from the canonical snapshot.

`validate_card.VALID_SLUGS` and `_PROJECT_PREFIX_TO_SLUG` used to be hand-edited
literals that had to be kept byte-aligned with the relay's routing map by hand —
onboarding a customer was a two-file code edit and a silent-drift hazard. They
are now DERIVED from the bundled routing snapshot `config/repo-map.json` (the
in-repo mirror of the relay's SSM map `/bureau/relay/repo-map`, seeded from
agent-bureau's canonical `config/repo-map.json`).

These tests make the relay↔gate lockstep STRUCTURAL:

  * the DERIVE works — VALID_SLUGS is the snapshot's keys, the prefix map is
    identity-over-slugs plus the documented product nicknames; and
  * the DIVERGENCE guard — the in-module last-known-good fallback literal must
    equal the on-disk snapshot, so a hand-edit to one that forgets the other
    fails CI here rather than routing one way and validating another.

The cross-repo half (this bundled snapshot must equal agent-bureau's canonical
config/repo-map.json) is enforced on the agent-bureau side, where both files are
present in one checkout; bureau-pipeline CI has neither AWS creds nor a token to
read agent-bureau's PRIVATE snapshot, so it can only pin the in-repo copies.
"""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import validate_card  # noqa: E402

_SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "config" / "repo-map.json"


def _snapshot() -> dict:
    return json.loads(_SNAPSHOT_PATH.read_text())


class RepoMapSnapshotTest(unittest.TestCase):
    def test_snapshot_file_exists_and_is_nonempty(self):
        # The published-JSON read path must actually be bundled in this repo.
        self.assertTrue(_SNAPSHOT_PATH.is_file(), f"missing {_SNAPSHOT_PATH}")
        snap = _snapshot()
        self.assertIsInstance(snap, dict)
        self.assertTrue(snap, "routing snapshot must be non-empty")

    def test_snapshot_lists_every_product_repo(self):
        # Non-vacuous content guard (mirrors agent-bureau's snapshot test): the
        # two repos a stale copy was missing in DRE-1627 must be present, so this
        # module proves more than "two equal copies of an empty map".
        snap = _snapshot()
        self.assertEqual(snap["agent-bureau"], "dreadnought-foundry/agent-bureau")
        self.assertEqual(snap["agent-bureau-demo"], "dreadnought-foundry/agent-bureau-demo")

    def test_bureau_pipeline_is_a_routable_slug(self):
        # DRE-1929 (adr-bureau-pipeline-self-host): this repo itself is on the
        # dispatch rail — `repo:bureau-pipeline` cards must validate and route.
        # Both of the gate's copies (snapshot + fallback literal) must know it,
        # or dispatch works only until the first SSM/snapshot read blip.
        self.assertEqual(
            _snapshot()["bureau-pipeline"], "dreadnought-foundry/bureau-pipeline")
        self.assertIn("bureau-pipeline", validate_card.VALID_SLUGS)
        self.assertEqual(
            validate_card._FALLBACK_REPO_MAP["bureau-pipeline"],
            "dreadnought-foundry/bureau-pipeline")

    # --- the DERIVE works -----------------------------------------------------

    def test_valid_slugs_are_the_snapshot_keys(self):
        # VALID_SLUGS = set(routing map) — onboarding a repo by adding it to the
        # snapshot makes it a valid slug with no edit to validate_card.py.
        self.assertEqual(validate_card.VALID_SLUGS, set(_snapshot()))

    def test_prefix_map_is_identity_over_slugs_plus_aliases(self):
        # The prefix map is derived exactly the way the relay's _infer_slug does:
        # identity over every routable slug, plus the documented product
        # nicknames (the only non-derivable entries).
        expected = {slug: slug for slug in _snapshot()}
        expected.update(validate_card._PROJECT_PREFIX_ALIAS)
        self.assertEqual(validate_card._PROJECT_PREFIX_TO_SLUG, expected)

    def test_documented_alias_targets_are_real_repos(self):
        # Every nickname must point at a slug that is actually in the snapshot,
        # or the gate could infer a slug it then rejects as unknown.
        snap_keys = set(_snapshot())
        for prefix, slug in validate_card._PROJECT_PREFIX_ALIAS.items():
            self.assertIn(slug, snap_keys, f"alias {prefix}->{slug} not a real repo")
        for label, slug in validate_card._INITIATIVE_ALIAS.items():
            self.assertIn(slug, snap_keys, f"initiative alias {label}->{slug} not a real repo")

    # --- the DIVERGENCE guard -------------------------------------------------

    def test_inmodule_fallback_matches_the_snapshot(self):
        # THE divergence test the card asks for: the last-known-good fallback
        # literal baked into validate_card.py MUST equal the on-disk snapshot.
        # If someone edits config/repo-map.json (onboarding) but forgets the
        # fallback — or vice versa — the gate's two copies of the routing map
        # would silently disagree on an SSM-read failure. Fail loudly here.
        self.assertEqual(
            validate_card._FALLBACK_REPO_MAP,
            _snapshot(),
            "validate_card._FALLBACK_REPO_MAP has drifted from config/repo-map.json — "
            "update the fallback literal to match the snapshot (they are the gate's "
            "two copies of the routing map and must agree).",
        )

    def test_loaded_map_equals_snapshot_when_file_present(self):
        # The module reads the on-disk snapshot at import (not the fallback) when
        # the file is present — so onboarding is genuinely a data edit.
        self.assertEqual(validate_card._REPO_MAP, _snapshot())


class FallbackBehaviourTest(unittest.TestCase):
    """The safe fallback: a missing/malformed snapshot degrades to the
    last-known-good literal and logs, rather than hard-failing the gate."""

    def test_missing_snapshot_falls_back_to_literal(self):
        original = validate_card._SNAPSHOT_PATH
        try:
            validate_card._SNAPSHOT_PATH = Path("/nonexistent/repo-map.json")
            self.assertEqual(
                validate_card._load_repo_map(), validate_card._FALLBACK_REPO_MAP
            )
        finally:
            validate_card._SNAPSHOT_PATH = original

    def test_malformed_snapshot_falls_back_to_literal(self):
        import tempfile

        original = validate_card._SNAPSHOT_PATH
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write("{ not valid json ]")
            bad = Path(fh.name)
        try:
            validate_card._SNAPSHOT_PATH = bad
            self.assertEqual(
                validate_card._load_repo_map(), validate_card._FALLBACK_REPO_MAP
            )
        finally:
            validate_card._SNAPSHOT_PATH = original
            bad.unlink(missing_ok=True)

    def test_empty_dict_snapshot_falls_back_to_literal(self):
        import tempfile

        original = validate_card._SNAPSHOT_PATH
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write("{}")
            empty = Path(fh.name)
        try:
            validate_card._SNAPSHOT_PATH = empty
            self.assertEqual(
                validate_card._load_repo_map(), validate_card._FALLBACK_REPO_MAP
            )
        finally:
            validate_card._SNAPSHOT_PATH = original
            empty.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
