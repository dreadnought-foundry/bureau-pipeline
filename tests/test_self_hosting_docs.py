"""Self-hosting go-live record (DRE-2033 — the Option A acceptance smoke).

docs/self-hosting.md is the durable record of the day this repo went on its
own dispatch rail (DRE-1929 Option A). These tests pin the load-bearing facts
so the record can't silently rot:

  * the doc exists and names the go-live date and DRE-1929;
  * the v1 release sha it records matches what the tag actually pointed at
    when cut (pinned statically — CI checkouts are shallow and tagless, so
    the doc is the record, not a live `git rev-parse`);
  * the README section the doc points readers at still exists under the
    heading the doc names (a rename there must update the doc too).
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "self-hosting.md"

# The commit the operator cut v1 at (verified via `git rev-parse v1^{commit}`
# on go-live day). The doc records this as a historical fact.
V1_SHA = "7ff9374"
GO_LIVE_DATE = "2026-07-11"
README_SECTION = "Release channel: pinning, canary, promotion"


class TestSelfHostingDoc(unittest.TestCase):
    def setUp(self):
        self.assertTrue(DOC.is_file(), f"missing {DOC.relative_to(ROOT)}")
        self.text = DOC.read_text()

    def test_records_go_live_date_and_decision_card(self):
        self.assertIn(GO_LIVE_DATE, self.text)
        self.assertIn("DRE-1929", self.text)
        self.assertIn("adr-bureau-pipeline-self-host", self.text)

    def test_records_v1_release_sha(self):
        self.assertIn(V1_SHA, self.text)

    def test_names_the_canary_channels(self):
        # Both @main riders must be named: the fleet is on tags, these two
        # soak every merge before promotion.
        self.assertIn("agent-bureau", self.text)
        self.assertIn("@main", self.text)

    def test_points_at_a_readme_section_that_exists(self):
        self.assertIn(README_SECTION, self.text)
        readme = (ROOT / "README.md").read_text()
        self.assertIn(
            f"## {README_SECTION}",
            readme,
            "docs/self-hosting.md points at a README heading that no longer "
            "exists — update the doc's pointer alongside the rename",
        )


class TestHarnessProvesPromotion(unittest.TestCase):
    """DRE-2103: the ADR line grows a third clause — agents author, human
    promotes, HARNESS PROVES. Both operator-facing docs must carry the
    clause and the exact pre-tag command (the pipeline_ref dispatch is how
    a candidate sha earns its green stamp before the tag exists)."""

    PRE_TAG_COMMAND = "gh workflow run harness.yml"

    def setUp(self):
        self.doc = DOC.read_text()
        self.readme = (ROOT / "README.md").read_text()

    def test_both_docs_carry_the_harness_proves_clause(self):
        for text in (self.doc, self.readme):
            self.assertIn("harness proves", text)

    def test_both_docs_document_the_exact_pre_tag_command(self):
        for text in (self.doc, self.readme):
            self.assertIn(self.PRE_TAG_COMMAND, text)
            self.assertIn("pipeline_ref=", text)

    def test_readme_names_the_release_gate_workflow(self):
        # The enforcement half: a v* tag with no green stamp goes loudly
        # red via release-gate.yml — the README promotion section must say
        # so, or the gate reads as an unexplained surprise.
        self.assertIn("release-gate.yml", self.readme)


# The channel entries as they read on 2026-08-20, before DRE-2553 moved the
# fleet onto `@stable`. The guard below must fail on both (DRE-2709).
PRE_DRE_2553_FIXTURES = (
    ROOT / "tests" / "fixtures" / "stable-channel-self-hosting-2026-08-20.md",
    ROOT / "tests" / "fixtures" / "stable-channel-readme-2026-08-20.md",
)

# A quotation is a record of what somebody said, not a claim the doc makes:
# README.md keeps the retired sentence in quotation marks on purpose, so the
# guard reads every document with its quoted spans removed.
_QUOTED_SPAN = re.compile(r"[\"\u201c][^\"\u201d]*[\"\u201d]")


def _unquoted(text: str) -> str:
    return _QUOTED_SPAN.sub(" ", text)


def _flat(text: str) -> str:
    """TEXT lowercased with every run of whitespace one space, so a phrase
    the doc hard-wraps across lines still reads as the phrase."""
    return " ".join(text.split()).lower()


class TestStableChannelDocumented(unittest.TestCase):
    """The stable channel, as both operator docs must describe it where they
    introduce it: `stable` is promoted automatically, the fleet consumes it,
    and what stays manual is `CHANNEL_HOLD` and the `vN` cut, which nothing
    rides (DRE-2551, DRE-2553, DRE-2689; superseded here by DRE-2709).

    Superseded contract — kept so the next reader knows what this guard used
    to hold and why it changed:

      * DRE-2551 (2026-08-20) required each doc's channel block to say "no
        product repo pins" `stable`, that the `vN` cut stayed
        "operator-only", and to name "vN" as the channel the fleet was on.
        That was correct when written: `promote-channel.yml` had just
        started moving `stable` with no operator involved, and "the harness
        moves the tag" was one short step from reading as "the fleet now
        updates itself", which is not what had shipped.
      * DRE-2553 (2026-08-23) re-pointed every consumer stub to `@stable`,
        and DRE-2689 made the paired `pipeline_ref: stable` required. From
        then on the guard REQUIRED the false sentence: correcting
        docs/self-hosting.md turned CI red, and README.md passed only
        because it quotes the retired claim — `assertIn` cannot tell a
        quotation from a live claim. So the phrases are now read with
        quoted spans removed, and the retired claims are refused.

    Revisit clause: if channel promotion ever becomes a human act again — the
    fleet back on a hand-cut ref, or `stable` moved only by a person — this
    guard must be revisited and rewritten to that contract, not quietly
    dropped. Which repo is on which ref is computed by agent-bureau's
    `make check-channel-fleet`; no doc may write a repo count in its place.
    """

    # Read from the block that introduces the channel, not from anywhere in
    # the file: "operator-only" already appears in the README's manual
    # promotion prose, so a whole-file assertion would pass without the
    # channel ever being explained.
    BLOCK_ANCHOR = "promote-channel.yml"
    BLOCK_CHARS = 1600
    REQUIRED = (
        # `stable` is promoted automatically, to the newest proven commit
        "on the newest commit on `main`",
        # ...and the fleet consumes it
        "the fleet pins `@stable`",
        # what stays manual: the pause...
        "`CHANNEL_HOLD`",
        # ...and the `vN` cut, which nothing rides
        "operator-only",
        "no repo consumes a `vN` tag",
    )
    # The pre-DRE-2553 contract, refused wherever a doc states it (quoting it
    # as history is allowed in the README, see _unquoted).
    RETIRED = (
        "no product repo pins",
        "nothing consumes",
        "nothing pins",
        "the fleet is on `vN`",
        "only fleet-facing channel",
        "only channel the fleet consumes",
        "product repos pin `@v",
    )
    # The two docs this card corrected may not carry a retired claim at all,
    # quoted or not.
    STRICT_DOCS = (
        DOC,
        ROOT / "standards" / "engineering.md",
    )

    @classmethod
    def scope_failures(cls, text: str) -> list:
        """Every way TEXT's channel block falls short of the contract, empty
        when it holds. Reads only what the block claims, never what it
        quotes."""
        if cls.BLOCK_ANCHOR not in text:
            return [
                "never names the workflow that moves the channel — a reader "
                "cannot find out the ref moves by itself"
            ]
        start = text.index(cls.BLOCK_ANCHOR)
        block = _flat(_unquoted(text[start : start + cls.BLOCK_CHARS]))
        failures = [
            f'introduces the automatic channel without saying "{phrase}"'
            for phrase in cls.REQUIRED
            if phrase.lower() not in block
        ]
        failures += [
            f'still states the retired claim "{claim}"'
            for claim in cls.RETIRED
            if claim.lower() in block
        ]
        return failures

    def _docs(self):
        return (
            ("docs/self-hosting.md", DOC.read_text()),
            ("README.md", (ROOT / "README.md").read_text()),
        )

    def test_both_docs_name_the_channel_and_its_card(self):
        for name, text in self._docs():
            self.assertIn("stable", text, f"{name} never names the channel")
            self.assertIn("DRE-2551", text, f"{name} never cites the card")

    def test_both_docs_scope_the_channel_where_they_introduce_it(self):
        for name, text in self._docs():
            self.assertEqual(
                self.scope_failures(text),
                [],
                f"{name}: the fleet rides `@stable` (DRE-2553) and the doc has "
                f"to say so where it introduces the channel",
            )

    def test_the_guard_fails_on_the_pre_dre_2553_text(self):
        for fixture in PRE_DRE_2553_FIXTURES:
            failures = self.scope_failures(fixture.read_text())
            self.assertIn(
                'introduces the automatic channel without saying '
                '"the fleet pins `@stable`"',
                failures,
                fixture.name,
            )
            self.assertIn(
                'still states the retired claim "no product repo pins"',
                failures,
                fixture.name,
            )

    def test_the_guard_fails_on_a_doc_that_only_quotes_the_retired_claim(self):
        # Satisfies the superseded REQUIRED tuple in full, by quotation.
        quoting = (
            "`promote-channel.yml` moves `stable`. This section used to say "
            '*"no product repo pins `@stable`; the fleet is on `vN`"*, and '
            "cutting `vN` is operator-only."
        )
        for phrase in ("no product repo pins", "operator-only", "vN"):
            self.assertIn(phrase.lower(), quoting.lower())
        self.assertNotEqual(self.scope_failures(quoting), [])

    def test_the_guard_fails_on_a_doc_that_only_quotes_the_current_contract(self):
        quoting = "`promote-channel.yml` moves `stable`. The card asked for " + (
            ", ".join(f'"{phrase}"' for phrase in self.REQUIRED)
        )
        failures = self.scope_failures(quoting)
        self.assertEqual(len(failures), len(self.REQUIRED), failures)

    def test_the_corrected_docs_state_no_retired_claim_anywhere(self):
        for path in self.STRICT_DOCS:
            text = _flat(path.read_text())
            for claim in self.RETIRED:
                self.assertFalse(
                    claim.lower() in text,
                    f'{path.relative_to(ROOT)} still says "{claim}" — the '
                    f"fleet has ridden `@stable` since DRE-2553 (2026-08-23)",
                )

    def test_the_corrected_docs_cite_the_live_fleet_check(self):
        for path in self.STRICT_DOCS:
            self.assertTrue(
                "make check-channel-fleet" in path.read_text(),
                f"{path.relative_to(ROOT)} must point at agent-bureau's "
                f"computed roster rather than describe the fleet from memory",
            )

    def test_the_engineering_standard_states_the_channel_the_fleet_rides(self):
        """standards/engineering.md's self-hosting section is the trust
        boundary build agents receive in their context. Until DRE-2709 it
        called the `vN` cut "a human act and the permanent gate" and the
        guard required that phrase (DRE-2551) — true while the fleet rode
        `vN`, false once DRE-2553 moved it onto `@stable`. The section now
        has to name the channel the fleet rides and the human act that is
        left."""
        text = (ROOT / "standards" / "engineering.md").read_text()
        section = text.split("## Self-hosting convention", 1)[1].split("\n## ", 1)[0]
        for phrase in ("`@stable`", "DRE-2551", "DRE-2553", "`CHANNEL_HOLD`"):
            self.assertIn(phrase, section)
        self.assertNotIn("permanent gate", section)


if __name__ == "__main__":
    unittest.main()
