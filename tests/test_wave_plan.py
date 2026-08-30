"""The wave route — the machine surface (DRE-2845).

A wave-sized idea produces something no other shape does: a plan for a whole
layer of work, plus the epics it commits to, in order. Nothing in this repo
knew what that document had to contain, so nothing could refuse one that was
missing half of it.

`scripts/wave_plan.py` is that checker, and it is `plan_artifact.py`'s shape
deliberately — same section scanner, same renderer, same sanitiser and CSP.
These tests pin it against the card's acceptance criteria:

  1. THE STANDARD IS THE SOURCE — the required sections are READ from
     `standards/wave-plan.md` in bureau-pipeline at check time, never copied
     into the checker. Point it at a different standard and the requirements
     change with it; a standard that stops stating the rules the checker
     enforces is itself reported.
  2. SIX SECTIONS, NAMED WHEN ABSENT — a plan missing one is refused with the
     section named. Refused, not warned: the CLI exits non-zero.
  3. THE EPICS IT COMMITS TO — a machine-readable ```epics block, in
     dependency order. An epic listed before something it depends on is a
     defect naming both, which is also what catches a cycle.
  4. PROVENANCE — an unsourced number in the research section carries the
     `(unverified)` marker the standard requires, and a citation that does
     not resolve is a defect anywhere in the document. A bad citation is
     worse than none: it looks solid, so nobody opens it.
  5. ONE RENDERER — the page comes from `plan_artifact.py`'s hardened shell.
     A second, hand-rolled one would re-open the ground DRE-2720 covered
     under adversarial review.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(REPO, "scripts")
STANDARD = os.path.join(REPO, "standards", "wave-plan.md")
sys.path.insert(0, SCRIPTS)


def wp():
    import wave_plan  # deferred: RED until DRE-2845 lands the script

    return wave_plan


# --- Fixtures ----------------------------------------------------------------
#
# The plan is ASSEMBLED from the standard's own headings rather than typed out
# here. A fixture that hard-codes the six titles would be the copy this card
# exists to avoid, and it would go stale the day the standard is edited.

EPICS_BLOCK = """```epics
[
  {"key": "standard", "title": "The standard moves where agents read",
   "depends_on": []},
  {"key": "route", "title": "The wave route and its checker",
   "depends_on": ["standard"]}
]
```"""

KPI_BLOCK = """```kpis
[{"name": "Wave plans refused for a missing section", "baseline": 0,
  "unit": "per wave", "direction": "up", "target": 1}]
```"""

BODIES = {
    1: (
        "The plan run fired 40 times in the fortnight to 2026-08-25 — counted "
        "off the thread on DRE-2720 and `scripts/plan_artifact.py`.\n\n"
        "7 of those runs died before writing anything (unverified) — the run "
        "records were pruned before anyone counted them."
    ),
    2: (
        "The evidence went against approving the whole wave at once: two of "
        "its cards were rewritten mid-wave, so we narrowed the approval to "
        "the shape and the order (DRE-2846)."
    ),
    3: (
        "Does the wave own its epics' green lights, or does each epic own its "
        "own? Decided by the CEO before Phase 2 starts."
    ),
    4: (
        "out of scope: the console surface for wave progress — nothing reads "
        "the record yet, so a surface would render an empty box."
    ),
    5: (
        "Phase one lands the checker. Proven in production by the first "
        "wave-shaped card whose plan run refuses a plan with a missing "
        "section, visible on that card's own thread.\n\n" + EPICS_BLOCK
    ),
    6: (
        KPI_BLOCK + "\n\nThe baseline is zero by construction — there is no "
        "checker today, so nothing has ever been refused."
    ),
}

DEFAULT_BODY = "Stated here, in full, so this section is not a title with an intent under it."


def plan_md(drop=(), bodies=None, headings=None, title="Wave 2 — the front door"):
    """A complete wave plan, minus whatever `drop` names by number."""
    reqs = wp().requirements()
    bodies = {**BODIES, **(bodies or {})}
    parts = [f"# {title}", ""]
    for r in reqs:
        if r.number in drop:
            continue
        head = (headings or {}).get(r.number, f"## {r.number}. {r.title}")
        parts += [head, "", bodies.get(r.number, DEFAULT_BODY), ""]
    return "\n".join(parts)


def epics_block(records):
    return "```epics\n" + json.dumps(records, indent=2) + "\n```"


def with_epics(records):
    return plan_md(bodies={5: BODIES[5].replace(EPICS_BLOCK, epics_block(records))})


def fabricated_standard(*titles):
    """A standard with requirements nobody has ever written down, so a checker
    that agreed with it could only have read it."""
    lines = ["# A standard that is not the shipped one", "",
             "## The things every wave plan states", ""]
    for i, title in enumerate(titles, 1):
        lines += [f"### {i}. {title}", "",
                  "It says where it came from, and an unsourced number is "
                  "marked (unverified). A citation that does not resolve is "
                  "itself the defect.", ""]
    return "\n".join(lines)


def write(tmp, name, body):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def run_cli(*args):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "wave_plan.py"), *args],
        capture_output=True, text=True, cwd=REPO,
    )


# --- 1. The standard is the source ------------------------------------------


class TheCheckerReadsTheStandardTest(unittest.TestCase):
    def test_the_standard_it_reads_is_the_pipeline_copy(self):
        self.assertEqual(
            os.path.abspath(STANDARD), os.path.abspath(wp().STANDARD_PATH),
            "the checker must read standards/wave-plan.md from the "
            "bureau-pipeline checkout, not a copy carried anywhere else",
        )
        self.assertTrue(os.path.isfile(wp().STANDARD_PATH))

    def test_the_requirements_are_the_standards_own(self):
        text = open(STANDARD, encoding="utf-8").read()
        reqs = wp().requirements()
        self.assertEqual(6, len(reqs), "the standard states six things")
        self.assertEqual([1, 2, 3, 4, 5, 6], [r.number for r in reqs])
        for r in reqs:
            with self.subTest(requirement=r.number):
                self.assertIn(r.title, text)

    def test_a_different_standard_moves_the_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "wave-plan.md",
                         fabricated_standard("The receipts, with provenance",
                                             "The weather on the day"))
            reqs = wp().requirements(path)
            self.assertEqual(
                ["The receipts, with provenance", "The weather on the day"],
                [r.title for r in reqs],
                "the requirements must come from the file, not from the code",
            )
            found = wp().defects("# A plan with nothing in it\n", standard=path)
            self.assertTrue(
                any("The weather on the day" in d for d in found),
                f"the fabricated section must be named as missing: {found}",
            )

    def test_the_requirement_titles_are_not_copied_into_this_repo(self):
        """A copy is a second source of truth, and the copy is what drifts."""
        titles = [r.title.lower() for r in wp().requirements()]
        for rel in ("scripts/wave_plan.py", ".github/workflows/plan.yml"):
            body = open(os.path.join(REPO, rel), encoding="utf-8").read().lower()
            for title in titles:
                with self.subTest(file=rel, title=title):
                    self.assertNotIn(
                        title, body,
                        f"{rel} carries a copy of the standard's section "
                        f"titles — it must read them from the standard",
                    )

    def test_a_standard_stating_no_requirements_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "wave-plan.md", "# Nothing here\n\nProse only.\n")
            problems = wp().standard_problems(path)
            self.assertTrue(problems, "a standard with no requirements is a problem")
            found = wp().defects(plan_md(), standard=path)
            self.assertTrue(
                found, "a checker whose standard says nothing must refuse, "
                       "not pass everything",
            )

    def test_a_standard_that_drops_the_marker_is_reported(self):
        """The checker enforces the `(unverified)` rule. If the standard stops
        stating it, the checker says so rather than enforcing a rule nobody
        wrote down."""
        with tempfile.TemporaryDirectory() as tmp:
            body = fabricated_standard("The receipts, with provenance")
            path = write(tmp, "wave-plan.md", body.replace("(unverified)", "noted"))
            problems = wp().standard_problems(path)
            self.assertTrue(
                any("unverified" in p for p in problems),
                f"the missing marker rule must be reported: {problems}",
            )


# --- 2. Six sections, named when absent -------------------------------------


class TheSixSectionsTest(unittest.TestCase):
    def test_a_complete_plan_has_no_defects(self):
        found = wp().defects(plan_md())
        self.assertEqual([], found, f"a complete wave plan must pass: {found}")

    def test_every_missing_section_is_named(self):
        for r in wp().requirements():
            with self.subTest(section=r.number):
                found = wp().defects(plan_md(drop=(r.number,)))
                self.assertTrue(
                    any(r.title in d for d in found),
                    f"a plan without section {r.number} must be refused with "
                    f"{r.title!r} named; got {found}",
                )

    def test_an_unrecognised_heading_does_not_substitute(self):
        reqs = wp().requirements()
        target = reqs[2]
        found = wp().defects(plan_md(headings={target.number: "## Some other thing"}))
        self.assertTrue(
            any(target.title in d for d in found),
            "a heading nobody recognises must read as a MISSING section, not "
            "be absorbed as a substitute",
        )

    def test_the_standards_numbering_is_enough(self):
        """Tolerant on the spelling, strict on the answer: a section written
        `## 3.` is section three even if its title is worded differently."""
        reqs = wp().requirements()
        target = reqs[2]
        found = wp().defects(
            plan_md(headings={target.number: f"## {target.number}. Open questions"})
        )
        self.assertEqual([], found, f"numbered headings must be accepted: {found}")

    def test_the_checker_refuses_it_does_not_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            complete = write(tmp, "ok.md", plan_md())
            broken = write(tmp, "bad.md", plan_md(drop=(2,)))
            good = run_cli("check", complete)
            self.assertEqual(0, good.returncode, good.stderr)
            bad = run_cli("check", broken)
            self.assertEqual(
                1, bad.returncode,
                "a wave plan missing a section must FAIL the check, not warn",
            )
            title = wp().requirements()[1].title
            self.assertIn(title, bad.stdout + bad.stderr,
                          "the refusal must name the missing section")


# --- 3. The epics it commits to, in dependency order ------------------------


class TheEpicsItCommitsToTest(unittest.TestCase):
    def test_the_epics_are_read_in_the_order_written(self):
        self.assertEqual(
            ["standard", "route"],
            [e["key"] for e in wp().epics(plan_md())],
        )

    def test_a_plan_that_names_no_epics_is_refused(self):
        md = plan_md(bodies={5: BODIES[5].replace(EPICS_BLOCK, "")})
        found = wp().defects(md)
        self.assertTrue(
            any("epics" in d for d in found),
            f"a wave plan that commits to no epics must be refused: {found}",
        )

    def test_an_epic_before_its_dependency_is_refused(self):
        md = with_epics([
            {"key": "route", "title": "The route", "depends_on": ["standard"]},
            {"key": "standard", "title": "The standard", "depends_on": []},
        ])
        found = wp().defects(md)
        self.assertTrue(
            any("route" in d and "standard" in d for d in found),
            f"an epic listed before what it depends on must be named: {found}",
        )

    def test_a_dependency_the_plan_never_names_is_refused(self):
        md = with_epics([
            {"key": "route", "title": "The route", "depends_on": ["ghost"]},
        ])
        found = wp().defects(md)
        self.assertTrue(any("ghost" in d for d in found), found)

    def test_a_cycle_is_refused(self):
        md = with_epics([
            {"key": "a", "title": "A", "depends_on": ["b"]},
            {"key": "b", "title": "B", "depends_on": ["a"]},
        ])
        self.assertTrue(wp().defects(md), "a dependency cycle cannot be an order")

    def test_an_epic_with_no_title_is_refused(self):
        md = with_epics([{"key": "a", "title": "", "depends_on": []}])
        found = wp().defects(md)
        self.assertTrue(
            any("title" in d for d in found),
            f"the plan must NAME the epics, not key them: {found}",
        )

    def test_two_epics_under_one_key_are_refused(self):
        md = with_epics([
            {"key": "a", "title": "A", "depends_on": []},
            {"key": "a", "title": "Also A", "depends_on": []},
        ])
        self.assertTrue(wp().defects(md))


# --- 4. Provenance: the marker, and citations that resolve ------------------


class ProvenanceTest(unittest.TestCase):
    def test_an_unsourced_number_is_a_defect(self):
        md = plan_md(bodies={1: "The queue held 42 cards on the worst day."})
        found = wp().defects(md)
        self.assertTrue(
            any("unverified" in d for d in found),
            f"an unsourced number must be refused, naming the marker: {found}",
        )

    def test_the_unverified_marker_clears_it(self):
        md = plan_md(bodies={1: "The queue held 42 cards (unverified)."})
        self.assertEqual([], wp().defects(md))

    def test_a_citation_clears_it(self):
        md = plan_md(
            bodies={1: "The queue held 42 cards — see `scripts/reconcile.py`."})
        self.assertEqual([], wp().defects(md))

    def test_a_card_reference_is_a_citation(self):
        md = plan_md(bodies={1: "The queue held 42 cards on DRE-2720."})
        self.assertEqual([], wp().defects(md))

    def test_each_claim_answers_for_itself(self):
        """A citation on one bullet does not cover the bullet under it."""
        md = plan_md(bodies={1: (
            "- The run fired 40 times (`scripts/plan_artifact.py`).\n"
            "- 12 of them produced nothing.\n"
        )})
        found = wp().defects(md)
        self.assertTrue(any("12" in d for d in found), found)


class CitationsResolveTest(unittest.TestCase):
    def test_a_citation_that_does_not_resolve_is_a_defect(self):
        md = plan_md(bodies={1: "Counted in [the sweep](scripts/no_such_file.py)."})
        found = wp().defects(md)
        self.assertTrue(
            any("no_such_file.py" in d for d in found),
            f"a dead citation is worse than none and must be named: {found}",
        )

    def test_a_resolving_citation_passes(self):
        md = plan_md(bodies={1: "Counted in [the artifact](scripts/plan_artifact.py)."})
        self.assertEqual([], wp().defects(md))

    def test_a_line_past_the_end_of_the_file_does_not_resolve(self):
        md = plan_md(bodies={1: "The rule is at `scripts/plan_artifact.py:99999`."})
        found = wp().defects(md)
        self.assertTrue(
            any("99999" in d or "lines" in d for d in found),
            f"a citation naming a line the file does not have must be "
            f"reported: {found}",
        )

    def test_citations_are_checked_everywhere_not_only_in_the_research(self):
        md = plan_md(bodies={4: "out of scope: [the surface](docs/ghost-file.md) — later."})
        found = wp().defects(md)
        self.assertTrue(any("ghost-file.md" in d for d in found), found)

    def test_a_url_is_not_reported_as_broken(self):
        """We did not open it, so we do not say it is broken (console honesty:
        never raise on data we never read)."""
        md = plan_md(
            bodies={1: "40 runs — see https://github.com/example/actions/runs/1."})
        self.assertEqual([], wp().defects(md))


# --- 5. The KPI block, reused from the epic artifact ------------------------


class KpisArePredictedTest(unittest.TestCase):
    def test_a_prose_kpi_section_is_refused(self):
        md = plan_md(bodies={6: "We expect refusals to go up a bit."})
        found = wp().defects(md)
        self.assertTrue(
            any("kpis" in d for d in found),
            f"a prose KPI cannot be diffed against an outcome: {found}",
        )

    def test_a_kpi_without_a_baseline_is_named(self):
        md = plan_md(bodies={6: '```kpis\n[{"name": "Refusals", '
                                '"direction": "up"}]\n```'})
        found = wp().defects(md)
        self.assertTrue(any("baseline" in d for d in found), found)


# --- 6. One renderer --------------------------------------------------------


class OneRendererTest(unittest.TestCase):
    def test_the_page_is_the_hardened_shell(self):
        page = wp().render(plan_md(), "DRE-2668")
        self.assertIn("Content-Security-Policy", page)
        self.assertIn("script-src 'none'", page)

    def test_there_is_no_second_renderer(self):
        source = open(os.path.join(SCRIPTS, "wave_plan.py"), encoding="utf-8").read()
        for hand_rolled in ("<!doctype", "Content-Security-Policy", "<html"):
            with self.subTest(fragment=hand_rolled):
                self.assertNotIn(
                    hand_rolled, source.lower() if hand_rolled.islower() else source,
                    "the wave plan must render through plan_artifact.py — a "
                    "second renderer re-opens the ground DRE-2720 covered",
                )

    def test_sections_and_paragraphs_are_anchored(self):
        page = wp().render(plan_md(), "DRE-2668")
        self.assertIn('id="section-1"', page)
        self.assertIn('id="section-1-p1"', page)

    def test_the_epics_render_as_a_table(self):
        page = wp().render(plan_md(), "DRE-2668")
        self.assertIn("The wave route and its checker", page)
        self.assertNotIn("&quot;depends_on&quot;", page,
                         "the epics must be a table, not raw JSON on the page")

    def test_markup_in_the_plan_never_executes(self):
        md = plan_md(bodies={4: "out of scope: <script>alert(1)</script> — no."})
        page = wp().render(md, "DRE-2668")
        self.assertNotIn("<script>alert", page)


class TheCliTest(unittest.TestCase):
    def test_headings_prints_the_standards_own_sections(self):
        out = run_cli("headings")
        self.assertEqual(0, out.returncode, out.stderr)
        for r in wp().requirements():
            self.assertIn(r.title, out.stdout)

    def test_epics_prints_the_commitment_as_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "plan.md", plan_md())
            out = run_cli("epics", path)
            self.assertEqual(0, out.returncode, out.stderr)
            self.assertEqual(["standard", "route"],
                             [e["key"] for e in json.loads(out.stdout)])

    def test_a_missing_file_is_a_finding_not_a_traceback(self):
        out = run_cli("check", os.path.join(REPO, "no-such-plan.md"))
        self.assertNotEqual(0, out.returncode)
        self.assertNotIn("Traceback", out.stderr)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(not unittest.main(exit=False).result.wasSuccessful())
