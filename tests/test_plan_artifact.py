"""The plan artifact — the machine surface (DRE-2720).

An epic's CEO-facing output is today a plain-text Linear comment: a medium
that cannot hold a diagram, a mockup, or anything navigable, and whose KPI
section is prose. These tests pin the artifact that replaces it, section by
section against the card's acceptance criteria:

  1. SEVEN SECTIONS — business case, KPIs, risk assessment, outcome, visual
     model, the cards, proof and demo. A missing one is named, not guessed.
  2. KPIs AS DATA — a fenced ```kpis block of JSON records (name, baseline,
     direction), so a close-out can DIFF prediction against outcome instead
     of re-reading prose. This is O10 pushed down one level
     (architecture/wave-plans/README.md:54 — "predicting two and moving two
     is a result; moving two and then naming them is a story"): an outcome
     reported for a KPI nobody predicted comes back as `unpredicted`, which
     is the story case, mechanically.
  3. A STABLE URL — the publish path is a pure function of the epic id, so
     revision two lands on top of revision one and the link the CEO holds
     never moves.
  4. A GENERATED VERSION RECORD — what changed since the CEO last read it,
     derived by diffing the published source against the new one. Nobody
     writes it by hand, which is the only way it stays true.
  5. A LIVE MOCKUP FOR UI WORK — built from `console/design/tokens.css`, and
     rendered into plan.html as MARKUP, not escaped text and not a
     screenshot. A UI epic with a PNG and no mockup fails the check.
  6. ANCHORS — every section and every paragraph carries a stable id, so
     feedback lands on the paragraph or the mockup it is about.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(REPO, "scripts")
sys.path.insert(0, SCRIPTS)


def _pa():
    import plan_artifact  # deferred: RED until DRE-2720 lands the script

    return plan_artifact


# --- Fixtures: a real-shaped artifact, and the shapes that must be rejected --

KPI_BLOCK_V1 = """```kpis
[
  {"name": "Time to green light", "baseline": 4.0, "unit": "hours",
   "direction": "down", "target": 1.5},
  {"name": "Plans sent back for rework", "baseline": 6, "unit": "per week",
   "direction": "down", "target": 2}
]
```"""

ARTIFACT_V1 = f"""# Wave 1.5 — the intake gate

## Business case

Cards arrive in every shape and the fleet builds them anyway. A gate in front
of the work is cheaper than a rework round behind it.

## KPIs

{KPI_BLOCK_V1}

Baselines measured over the fortnight to 2026-08-25.

## Risk assessment

A gate that bounces too hard stalls the board. Blast radius is one lane;
reversible by turning the guard off.

## Outcome

The CEO reads one artifact per epic and green-lights in minutes, not hours.

## Visual model

Not applicable — this epic ships no UI.

## The cards

| Card | What it does |
| -- | -- |
| DRE-2718 | The Intake lane exists |
| DRE-2719 | Everything goes to Planning |

## Proof and demo

The harness replays a hostile card through the gate; the demo is one epic
walked end to end on the board.
"""

# Revision two: the business case is rewritten, one KPI is added, one
# baseline is re-measured, and everything else is untouched.
KPI_BLOCK_V2 = """```kpis
[
  {"name": "Time to green light", "baseline": 3.2, "unit": "hours",
   "direction": "down", "target": 1.5},
  {"name": "Plans sent back for rework", "baseline": 6, "unit": "per week",
   "direction": "down", "target": 2},
  {"name": "Cards bounced at intake", "baseline": 0, "unit": "per week",
   "direction": "up", "target": 10}
]
```"""

ARTIFACT_V2 = (
    ARTIFACT_V1.replace(KPI_BLOCK_V1, KPI_BLOCK_V2)
    .replace(
        "A gate in front\nof the work is cheaper than a rework round behind it.",
        "A gate in front of the work is cheaper than a rework round behind it,\n"
        "and the fortnight of rework we just paid for is the evidence.",
    )
)

MOCKUP_BLOCK = """```mockup
<div class="board" style="background: var(--surface); color: var(--text)">
  <h3 style="font: var(--font-heading)">Intake</h3>
  <button style="background: var(--accent)">Groom</button>
</div>
```"""

UI_ARTIFACT = ARTIFACT_V1.replace(
    "Not applicable — this epic ships no UI.",
    "The Intake lane, as it will look.\n\n" + MOCKUP_BLOCK,
)

SCREENSHOT_ARTIFACT = ARTIFACT_V1.replace(
    "Not applicable — this epic ships no UI.",
    "![the intake lane](console/design/images/screens/desktop/board.png)",
)


class SevenSectionsTest(unittest.TestCase):
    """AC1 — the artifact carries all seven sections, and a missing one is
    named rather than silently tolerated."""

    def test_a_complete_artifact_has_no_missing_sections(self):
        self.assertEqual(_pa().missing_sections(ARTIFACT_V1), [])

    def test_every_required_section_is_detected_when_removed(self):
        # Each of the seven, one at a time: drop its heading and the check
        # must name exactly that section. A check that only ever finds the
        # first missing one lets the other six ship silently.
        pa = _pa()
        for canonical in pa.REQUIRED_SECTIONS:
            with self.subTest(section=canonical):
                heading = [
                    line
                    for line in ARTIFACT_V1.splitlines()
                    if line.startswith("## ")
                    and pa.normalize_heading(line) == canonical
                ]
                self.assertEqual(len(heading), 1, f"fixture must have {canonical}")
                stripped = ARTIFACT_V1.replace(heading[0] + "\n", "")
                self.assertIn(canonical, pa.missing_sections(stripped))

    def test_headings_with_a_trailing_clause_still_count(self):
        # The card writes "KPIs — as structured data"; planners will too.
        # The section is identified by its subject, not by exact punctuation.
        text = ARTIFACT_V1.replace("## KPIs", "## KPIs — as structured data")
        self.assertEqual(_pa().missing_sections(text), [])

    def test_the_acronym_section_is_spelled_for_the_reader(self):
        # Naive title-casing renders "Kpis" — in the one document the CEO
        # reads, and in the generated version record he reads with it.
        pa = _pa()
        self.assertEqual(pa.section_title("kpis"), "KPIs")
        self.assertEqual(pa.section_title("risk assessment"), "Risk assessment")
        self.assertIn("<h2>KPIs</h2>", pa.render(ARTIFACT_V1, "DRE-2668"))
        self.assertIn("**KPIs**", pa.version_record(ARTIFACT_V1, ARTIFACT_V2))

    def test_section_body_is_returned_for_the_renderer(self):
        body = _pa().sections(ARTIFACT_V1)["outcome"]
        self.assertIn("green-lights in minutes", body)
        self.assertNotIn("## ", body, "a section body must stop at the next heading")


class KpisAsDataTest(unittest.TestCase):
    """AC2 — the KPI section is machine-readable, and the fields are the ones
    a close-out needs: name, baseline, predicted direction."""

    def test_kpis_parse_to_records(self):
        kpis = _pa().kpis(ARTIFACT_V1)
        self.assertEqual([k["name"] for k in kpis],
                         ["Time to green light", "Plans sent back for rework"])
        self.assertEqual(kpis[0]["baseline"], 4.0)
        self.assertEqual(kpis[0]["direction"], "down")

    def test_prose_kpis_are_a_defect(self):
        # The whole point: "we expect review time to come down a lot" is not
        # a KPI. Without the block there is nothing to diff.
        prose = ARTIFACT_V1.replace(
            KPI_BLOCK_V1, "We expect review time to come down a lot."
        )
        defects = _pa().defects(prose)
        self.assertTrue(
            any("kpis" in d.lower() for d in defects),
            f"prose KPIs must be a defect, got {defects}",
        )

    def test_a_missing_baseline_is_a_defect(self):
        # A direction with no baseline cannot be diffed — "down from what?"
        bad = ARTIFACT_V1.replace('"baseline": 4.0, ', "")
        defects = _pa().defects(bad)
        self.assertTrue(
            any("baseline" in d for d in defects), f"got {defects}"
        )

    def test_a_non_numeric_baseline_is_a_defect(self):
        # "baseline": "quite slow" is prose wearing a field name.
        bad = ARTIFACT_V1.replace('"baseline": 4.0', '"baseline": "quite slow"')
        defects = _pa().defects(bad)
        self.assertTrue(any("baseline" in d for d in defects), f"got {defects}")

    def test_an_unknown_direction_is_a_defect(self):
        bad = ARTIFACT_V1.replace('"direction": "down"', '"direction": "better"')
        defects = _pa().defects(bad)
        self.assertTrue(any("direction" in d for d in defects), f"got {defects}")

    def test_malformed_json_is_a_defect_not_a_crash(self):
        bad = ARTIFACT_V1.replace('"name": "Time to green light",', '"name":,')
        self.assertTrue(_pa().defects(bad), "malformed KPI JSON must be reported")

    def test_zero_kpis_is_a_defect(self):
        bad = ARTIFACT_V1.replace(KPI_BLOCK_V1, "```kpis\n[]\n```")
        self.assertTrue(_pa().defects(bad), "an empty KPI list predicts nothing")


class CloseOutDiffTest(unittest.TestCase):
    """AC2 (second half) — a close-out diffs prediction against outcome, and
    the O10 story case (a number named only after it moved) is mechanical."""

    def test_a_kpi_that_moved_as_predicted(self):
        out = _pa().closeout(
            ARTIFACT_V1, [{"name": "Time to green light", "observed": 1.1}]
        )
        row = [k for k in out["kpis"] if k["name"] == "Time to green light"][0]
        self.assertTrue(row["moved"])
        self.assertTrue(row["as_predicted"])
        self.assertTrue(row["target_met"])

    def test_a_kpi_that_moved_the_wrong_way(self):
        out = _pa().closeout(
            ARTIFACT_V1, [{"name": "Time to green light", "observed": 6.0}]
        )
        row = [k for k in out["kpis"] if k["name"] == "Time to green light"][0]
        self.assertTrue(row["moved"])
        self.assertFalse(row["as_predicted"])

    def test_a_kpi_that_did_not_move(self):
        out = _pa().closeout(
            ARTIFACT_V1, [{"name": "Time to green light", "observed": 4.0}]
        )
        row = [k for k in out["kpis"] if k["name"] == "Time to green light"][0]
        self.assertFalse(row["moved"])

    def test_a_predicted_kpi_with_no_outcome_is_unmeasured(self):
        out = _pa().closeout(
            ARTIFACT_V1, [{"name": "Time to green light", "observed": 1.1}]
        )
        self.assertEqual(out["unmeasured"], ["Plans sent back for rework"])

    def test_an_outcome_for_a_kpi_nobody_predicted_is_a_story(self):
        # O10, mechanically: moving a number and THEN naming it is a story.
        # The close-out must be able to tell the two apart on its own.
        out = _pa().closeout(
            ARTIFACT_V1,
            [
                {"name": "Time to green light", "observed": 1.1},
                {"name": "Plans sent back for rework", "observed": 1},
                {"name": "Agent runs per day", "observed": 40},
            ],
        )
        self.assertEqual(out["unpredicted"], ["Agent runs per day"])
        self.assertEqual(out["unmeasured"], [])


class StableUrlTest(unittest.TestCase):
    """AC3 — the URL is a pure function of the epic id; revising the plan
    republishes over it rather than moving it."""

    def test_publish_path_derives_from_the_epic_id(self):
        self.assertEqual(
            _pa().stable_path("DRE-2668"), "plans/dre-2668/index.html"
        )

    def test_the_url_is_absent_not_invented_when_no_portal_is_configured(self):
        # console-honesty rule 2: unknown renders as unknown. A guessed URL
        # is worse than none — the CEO would follow it to a 404.
        self.assertIsNone(_pa().stable_url("DRE-2668", base=""))

    def test_the_url_is_stable_across_revisions(self):
        pa = _pa()
        base = "https://portico.example/docs"
        first = pa.stable_url("DRE-2668", base=base)
        second = pa.stable_url("DRE-2668", base=base + "/")
        self.assertEqual(first, second)
        self.assertIn("dre-2668", first)

    def test_publishing_twice_overwrites_one_location(self):
        pa = _pa()
        with tempfile.TemporaryDirectory() as portal:
            pa.publish(ARTIFACT_V1, "DRE-2668", portal)
            first = pa.publish(ARTIFACT_V2, "DRE-2668", portal)
            page = open(os.path.join(portal, pa.stable_path("DRE-2668"))).read()
            self.assertIn("Cards bounced at intake", page,
                          "revision two must be what the stable URL serves")
            self.assertEqual(first, os.path.join(portal, pa.stable_path("DRE-2668")))
            # Exactly one plan directory — no dre-2668-v2 sibling.
            self.assertEqual(sorted(os.listdir(os.path.join(portal, "plans"))),
                             ["dre-2668"])

    def test_publish_keeps_the_source_for_the_next_version_record(self):
        # The generated version record (AC4) needs the PREVIOUS text, and no
        # human is going to keep it. Publishing stores it beside the page.
        pa = _pa()
        with tempfile.TemporaryDirectory() as portal:
            self.assertIsNone(pa.published_source(portal, "DRE-2668"))
            pa.publish(ARTIFACT_V1, "DRE-2668", portal)
            self.assertEqual(pa.published_source(portal, "DRE-2668"), ARTIFACT_V1)


class VersionRecordTest(unittest.TestCase):
    """AC4 — a second version says what changed since the first, generated."""

    def test_a_changed_section_is_named(self):
        record = _pa().version_record(ARTIFACT_V1, ARTIFACT_V2)
        self.assertIn("Business case", record)

    def test_an_added_kpi_is_named(self):
        record = _pa().version_record(ARTIFACT_V1, ARTIFACT_V2)
        self.assertIn("Cards bounced at intake", record)

    def test_a_rebaselined_kpi_shows_both_numbers(self):
        record = _pa().version_record(ARTIFACT_V1, ARTIFACT_V2)
        self.assertIn("4.0", record)
        self.assertIn("3.2", record)

    def test_untouched_sections_are_listed_as_unchanged(self):
        # "What changed" is only useful if it also says what did not — the
        # CEO's question is where to re-read, and that is the other half.
        record = _pa().version_record(ARTIFACT_V1, ARTIFACT_V2)
        self.assertIn("Unchanged", record)
        self.assertIn("Risk assessment", record.split("Unchanged", 1)[1])

    def test_a_removed_kpi_is_named(self):
        shrunk = ARTIFACT_V2.replace(KPI_BLOCK_V2, KPI_BLOCK_V1)
        record = _pa().version_record(ARTIFACT_V2, shrunk)
        self.assertIn("Cards bounced at intake", record)
        self.assertIn("removed", record.lower())

    def test_an_identical_revision_says_nothing_changed(self):
        record = _pa().version_record(ARTIFACT_V1, ARTIFACT_V1)
        self.assertIn("No changes", record)

    def test_the_record_becomes_a_section_of_the_artifact(self):
        pa = _pa()
        merged = pa.with_version_record(
            ARTIFACT_V2, pa.version_record(ARTIFACT_V1, ARTIFACT_V2)
        )
        self.assertIn("version record", pa.sections(merged))
        self.assertEqual(pa.missing_sections(merged), [],
                         "adding the record must not disturb the seven")

    def test_a_third_revision_replaces_the_record_rather_than_stacking(self):
        pa = _pa()
        once = pa.with_version_record(ARTIFACT_V2, pa.version_record(ARTIFACT_V1, ARTIFACT_V2))
        twice = pa.with_version_record(once, pa.version_record(ARTIFACT_V1, ARTIFACT_V2))
        self.assertEqual(twice.count("## Version record"), 1)


class VisualModelTest(unittest.TestCase):
    """AC5 — a UI epic's artifact carries a live mockup built from the design
    tokens, not a screenshot."""

    def test_a_ui_epic_with_only_a_screenshot_fails(self):
        defects = _pa().defects(SCREENSHOT_ARTIFACT, ui=True)
        self.assertTrue(
            any("mockup" in d.lower() for d in defects),
            f"a PNG is not a visual model for a NEW screen, got {defects}",
        )

    def test_a_screenshot_only_visual_model_fails_without_the_ui_flag_too(self):
        # For a NEW screen there is no PNG to point at, so a visual model
        # that is only an image is the CEO approving a screen he cannot see.
        # That is a defect on its own evidence — no external UI signal needed.
        defects = _pa().defects(SCREENSHOT_ARTIFACT)
        self.assertTrue(
            any("mockup" in d.lower() for d in defects), f"got {defects}"
        )

    def test_a_ui_epic_with_a_token_built_mockup_passes(self):
        self.assertEqual(_pa().defects(UI_ARTIFACT, ui=True), [])

    def test_a_mockup_that_ignores_the_design_tokens_fails(self):
        # If it is not built from tokens.css it is a picture of a UI, not the
        # UI — and it will not inherit the design system by construction.
        untokened = UI_ARTIFACT.replace("var(--surface)", "#101014").replace(
            "var(--text)", "#eee"
        ).replace("var(--font-heading)", "16px sans-serif").replace(
            "var(--accent)", "#3b82f6"
        )
        defects = _pa().defects(untokened, ui=True)
        self.assertTrue(
            any("token" in d.lower() for d in defects), f"got {defects}"
        )

    def test_a_non_ui_epic_may_say_not_applicable_with_a_reason(self):
        self.assertEqual(_pa().defects(ARTIFACT_V1), [])

    def test_an_empty_visual_model_is_a_defect_even_on_a_non_ui_epic(self):
        # Silence is not a decision. Either a mockup, or a stated reason.
        blank = ARTIFACT_V1.replace("Not applicable — this epic ships no UI.", "")
        defects = _pa().defects(blank)
        self.assertTrue(
            any("visual model" in d.lower() for d in defects), f"got {defects}"
        )

    def test_not_applicable_does_not_excuse_a_ui_epic(self):
        defects = _pa().defects(ARTIFACT_V1, ui=True)
        self.assertTrue(defects, "a UI epic cannot opt out of the mockup")


class RenderTest(unittest.TestCase):
    """The build output — plan.html. Anchored (AC6), machine-readable, and
    for UI work carrying the mockup as live markup (AC5)."""

    def test_every_section_gets_a_stable_anchor(self):
        html = _pa().render(ARTIFACT_V1, "DRE-2668")
        for anchor in ("business-case", "kpis", "risk-assessment", "outcome",
                       "visual-model", "the-cards", "proof-and-demo"):
            self.assertIn(f'id="{anchor}"', html)

    def test_every_paragraph_gets_a_stable_anchor(self):
        # AC6: feedback lands on the paragraph it is about, so the paragraph
        # needs an address that survives the next revision of its neighbours.
        html = _pa().render(ARTIFACT_V1, "DRE-2668")
        self.assertIn('id="outcome-p1"', html)
        self.assertIn('id="business-case-p1"', html)

    def test_anchors_survive_an_edit_to_an_earlier_section(self):
        pa = _pa()
        first = pa.render(ARTIFACT_V1, "DRE-2668")
        second = pa.render(ARTIFACT_V2, "DRE-2668")
        self.assertIn('id="outcome-p1"', first)
        self.assertIn('id="outcome-p1"', second)

    def test_the_kpi_data_survives_into_the_page(self):
        # The published page stays machine-readable — a close-out reads the
        # numbers off the artifact without re-parsing markdown.
        html = _pa().render(ARTIFACT_V1, "DRE-2668")
        self.assertIn('id="kpi-data"', html)
        payload = html.split('id="kpi-data"', 1)[1].split(">", 1)[1].split("</script>")[0]
        self.assertEqual(
            [k["name"] for k in json.loads(payload)],
            ["Time to green light", "Plans sent back for rework"],
        )

    def test_the_mockup_renders_as_markup_not_as_text(self):
        html = _pa().render(UI_ARTIFACT, "DRE-2668")
        self.assertIn('<button style="background: var(--accent)">', html)
        self.assertNotIn("&lt;button", html)

    def test_the_page_links_the_design_tokens(self):
        html = _pa().render(UI_ARTIFACT, "DRE-2668")
        self.assertIn("tokens.css", html)

    def test_prose_is_escaped(self):
        # Everything except the mockup block is text and must render as text.
        risky = ARTIFACT_V1.replace(
            "The CEO reads one artifact",
            "The CEO reads <script>alert(1)</script> one artifact",
        )
        html = _pa().render(risky, "DRE-2668")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_the_cards_table_renders_as_a_table(self):
        html = _pa().render(ARTIFACT_V1, "DRE-2668")
        self.assertIn("<table", html)
        self.assertIn("DRE-2718", html)


class CliTest(unittest.TestCase):
    """The workflow drives this by CLI — exit codes and stdout are contract."""

    def _run(self, *args, **kw):
        return subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "plan_artifact.py"), *args],
            capture_output=True, text=True, **kw,
        )

    def test_check_passes_a_complete_artifact(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(ARTIFACT_V1)
        r = self._run("check", f.name)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_check_fails_and_names_the_defects(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(ARTIFACT_V1.replace("## Risk assessment", "## Notes"))
        r = self._run("check", f.name)
        self.assertEqual(r.returncode, 1)
        self.assertIn("risk assessment", (r.stdout + r.stderr).lower())

    def test_check_ui_flag_demands_the_mockup(self):
        # "Not applicable — no UI" is fine for a backend epic and fatal for a
        # UI one; the flag is the only difference.
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(ARTIFACT_V1)
        self.assertEqual(self._run("check", f.name).returncode, 0)
        self.assertEqual(self._run("check", f.name, "--ui").returncode, 1)

    def test_ui_epic_reads_the_signal_off_the_cards(self):
        # The run must not ask the planner whether its own epic is UI work.
        # A card carrying a **Design:** ref is evidence; prose is not.
        r = self._run("ui-epic", input=(
            "Build the intake lane.\n\n"
            "**Design:** console/design/images/screens/desktop/board.png\n"
        ))
        self.assertEqual(r.stdout.strip(), "true", r.stderr)
        r = self._run("ui-epic", input=(
            "Rework the relay's HMAC check. Mentions the board screen in "
            "passing.\n\n## Acceptance criteria\n- [ ] replays are dropped\n"
        ))
        self.assertEqual(r.stdout.strip(), "false", r.stderr)

    def test_ui_epic_on_empty_input_is_false_not_a_crash(self):
        # The planner may have created no children (it asked questions).
        r = self._run("ui-epic", input="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "false")

    def test_a_missing_artifact_file_is_reported_not_traced(self):
        r = self._run("check", "/tmp/no-such-plan-artifact.md")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stdout + r.stderr)

    def test_render_writes_the_build_output(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "plan.md")
            open(src, "w").write(ARTIFACT_V1)
            out = os.path.join(d, "plan.html")
            r = self._run("render", src, "--epic", "DRE-2668", "-o", out)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('id="kpi-data"', open(out).read())

    def test_url_prints_nothing_and_exits_clean_without_a_base(self):
        r = self._run("url", "DRE-2668", "--base", "")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_publish_prints_the_stable_url(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "plan.md")
            open(src, "w").write(ARTIFACT_V1)
            portal = os.path.join(d, "portal")
            r = self._run("publish", src, "--epic", "DRE-2668",
                          "--portal", portal, "--base", "https://portico.example")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("https://portico.example/plans/dre-2668/", r.stdout)

    def test_version_record_against_a_portal_with_no_prior_version(self):
        # Revision one has nothing to diff against and must not invent one.
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "plan.md")
            open(src, "w").write(ARTIFACT_V1)
            r = self._run("version-record", src, "--epic", "DRE-2668",
                          "--portal", os.path.join(d, "portal"))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(r.stdout.strip(), "")

    def test_version_record_against_the_published_previous_version(self):
        pa = _pa()
        with tempfile.TemporaryDirectory() as d:
            portal = os.path.join(d, "portal")
            pa.publish(ARTIFACT_V1, "DRE-2668", portal)
            src = os.path.join(d, "plan.md")
            open(src, "w").write(ARTIFACT_V2)
            r = self._run("version-record", src, "--epic", "DRE-2668",
                          "--portal", portal)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Cards bounced at intake", r.stdout)

    def test_closeout_emits_json_a_close_out_can_read(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "plan.md")
            open(src, "w").write(ARTIFACT_V1)
            outcomes = os.path.join(d, "outcomes.json")
            json.dump([{"name": "Time to green light", "observed": 1.1}],
                      open(outcomes, "w"))
            r = self._run("closeout", src, outcomes)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            data = json.loads(r.stdout)
            self.assertEqual(data["unmeasured"], ["Plans sent back for rework"])


if __name__ == "__main__":
    unittest.main()
