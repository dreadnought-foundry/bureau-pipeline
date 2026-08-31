"""Every card `linear_ops.cmd_create` mints carries a `repo:<slug>` label (DRE-2680).

`cmd_create` is the seam the medic, red-main-repair, channel-watch and
model-drift file a failure report through, and it issued `issueCreate` with
`teamId`, `title`, `description` and `stateId` — no `labelIds` at all. Under the
front door a card's `repo:` label is the only product key and the readiness gate
refuses a card without one, so every one of those cards landed in Planning and
could not leave it: the pipeline filed a report about itself that the pipeline
could not act on.

Two halves, and both are needed:

  * the SEAM — `--repo <slug>` is REQUIRED, applied as a `repo:<slug>` label,
    and an unrecognised slug is refused at creation rather than left for the
    relay to reject after the card exists;
  * the CALL SITES — every `linear_ops.py create` invocation in the workflows
    passes the repo it actually ran in.

The call-site half is DISCOVERED, never listed. A test that enumerated today's
six would prove only that today's six are still fixed; the seventh call site,
added next month with no `--repo`, is exactly the one that would go unnoticed.
"""

import io
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import linear_ops  # noqa: E402
import validate_card  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

GOOD_BODY = "Pipeline failure.\n\n## Acceptance criteria\n- [ ] it is diagnosed"


class FakeBoard:
    """A scriptable Linear endpoint for the create seam. `created` stays None
    unless `issueCreate` actually fired — which is what "fails loudly rather
    than creating an unlabelled card" means."""

    LANES = (
        ("state-planning", "Planning", "backlog"),
        ("state-triage", "Triage", "unstarted"),
    )

    def __init__(self):
        self.created = None

    def gql(self, query, variables=None):
        v = variables or {}
        q = " ".join(query.split())
        if "teams(filter:" in q:
            return {"teams": {"nodes": [{"id": "team-1"}]}}
        if "workflowStates" in q:
            return {"workflowStates": {"nodes": [
                {"id": i, "name": n, "type": t} for i, n, t in self.LANES]}}
        if "team(id: $teamId)" in q and "labels(first: 250)" in q:
            return {"team": {"labels": {"nodes": []}}}
        if "issueLabelCreate" in q:
            return {"issueLabelCreate": {"issueLabel": {
                "id": f"lbl-{v['input']['name']}"}}}
        if "issueCreate" in q:
            self.created = v["input"]
            return {"issueCreate": {"issue": {
                "id": "new-uuid", "identifier": "DRE-300", "url": "u"}}}
        raise AssertionError(f"unexpected query: {q[:80]}")

    def label_names(self):
        """The label names the create carried, read back off the resolved ids
        the fake minted."""
        return [i[len("lbl-"):] for i in (self.created or {}).get("labelIds", [])]


def _create(fake, *args):
    buf = io.StringIO()
    with mock.patch.object(linear_ops, "gql", side_effect=fake.gql):
        with redirect_stdout(buf):
            linear_ops.cmd_create(*args)
    return buf.getvalue()


class TheCreateSeamLabelsWhatItMints(unittest.TestCase):

    def setUp(self):
        self.fake = FakeBoard()
        fh = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
        fh.write(GOOD_BODY)
        fh.close()
        self.body_file = fh.name
        self.addCleanup(os.unlink, self.body_file)

    def test_a_created_card_carries_the_repo_label_it_was_given(self):
        _create(self.fake, "Pipeline failure: ci", self.body_file, "--repo", "atlas")
        self.assertEqual(self.fake.label_names(), ["repo:atlas"])

    def test_a_call_with_no_repo_refuses_and_creates_nothing(self):
        # The whole defect in one assertion: without this the card is created,
        # unlabelled, and nothing says so.
        with self.assertRaises(linear_ops.LinearError) as caught:
            _create(self.fake, "Pipeline failure: ci", self.body_file)
        self.assertIsNone(self.fake.created)
        self.assertIn("--repo", str(caught.exception))

    def test_a_repo_flag_with_no_value_refuses_and_creates_nothing(self):
        with self.assertRaises(linear_ops.LinearError):
            _create(self.fake, "Pipeline failure: ci", self.body_file, "--repo")
        self.assertIsNone(self.fake.created)

    def test_an_unrecognised_slug_is_refused_at_creation(self):
        # Refused HERE, not left for the relay to reject after the card exists.
        with self.assertRaises(linear_ops.LinearError) as caught:
            _create(self.fake, "Pipeline failure: ci", self.body_file,
                    "--repo", "not-a-real-repo")
        self.assertIsNone(self.fake.created)
        message = str(caught.exception)
        self.assertIn("not-a-real-repo", message)
        # The refusal names the set, so the caller can fix it in one read.
        for slug in validate_card.VALID_SLUGS:
            self.assertIn(slug, message)

    def test_every_slug_the_routing_snapshot_carries_is_accepted(self):
        # One source of truth for "a real repo": validate_card.VALID_SLUGS,
        # derived from config/repo-map.json. Onboarding a repo is a data edit
        # to the snapshot and this seam follows it with no code change.
        for slug in sorted(validate_card.VALID_SLUGS):
            fake = FakeBoard()
            _create(fake, "Pipeline failure: ci", self.body_file, "--repo", slug)
            self.assertEqual(fake.label_names(), [f"repo:{slug}"], slug)


# Every `linear_ops.py create` written anywhere in the workflows — `run:` blocks
# and the prose prompts handed to agents alike, which is why this reads the raw
# file rather than the parsed YAML (medic.yml's call site lives inside a
# `prompt:` string).
_CREATE_CALL_RE = re.compile(r"^.*linear_ops\.py create\b.*$", re.MULTILINE)
_REPO_ARG_RE = re.compile(r'--repo\s+("[^"]*"|\S+)')


def _create_call_sites():
    sites = []
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for line in _CREATE_CALL_RE.findall(text):
            sites.append((path.name, line.strip(), text))
    return sites


class EveryCallSitePassesTheRepoThatFailed(unittest.TestCase):
    """Discovered, never listed — a `create` added tomorrow inherits the guard
    instead of rediscovering the bug."""

    def test_the_call_sites_are_found(self):
        # Guards the guard: if the match stops firing, everything below passes
        # over an empty list and proves nothing. Six were live when this was
        # written (medic, red-main-repair x2, channel-watch, model-drift x2).
        self.assertGreaterEqual(len(_create_call_sites()), 6)

    def test_every_call_site_passes_a_repo(self):
        for name, line, _ in _create_call_sites():
            self.assertRegex(
                line, r"--repo\b",
                f"{name}: creates a card with no --repo — it lands unlabelled "
                f"and the readiness gate cannot route it: {line}",
            )

    def test_no_call_site_hardcodes_the_slug(self):
        # The slug each passes must be the repo that failed. These workflows are
        # reusable and run in whichever product repo called them, so a literal
        # would file every fleet failure against one repo — the same
        # unroutable-by-construction defect wearing a plausible label.
        for name, line, _ in _create_call_sites():
            value = _REPO_ARG_RE.search(line)
            self.assertIsNotNone(value, f"{name}: no --repo value in {line}")
            self.assertIn(
                "$", value.group(1),
                f"{name}: --repo is a hardcoded literal {value.group(1)!r}, not "
                f"the repo the run is in",
            )

    def test_every_minting_workflow_derives_the_slug_from_the_repo_it_ran_in(self):
        for name, _, text in _create_call_sites():
            self.assertTrue(
                'basename "$GITHUB_REPOSITORY"' in text,
                f"{name}: mints a card but never derives its slug from "
                f"GITHUB_REPOSITORY — the same derivation plan.yml uses",
            )


if __name__ == "__main__":
    unittest.main()
