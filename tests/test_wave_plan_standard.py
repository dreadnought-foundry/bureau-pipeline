"""The wave-plan standard, at the only address an agent can read (DRE-2842).

The standard for what a wave plan must state lived at
`architecture/wave-plans/README.md` in agent-bureau. Standards reach headless
CI agents by workflow context injection from `standards/` in THIS repo
(DRE-1646), so a wave plan held to a standard that lives in a product repo is
held to a rule its agents cannot open. The move is the house pattern: a
standard lives ONCE, here.

These tests pin the move:

  1. THE FILE — `standards/wave-plan.md` exists and carries all six things a
     wave plan must state. Each requirement is asserted by its own section
     AND by the words that make the section mean something, so deleting a
     requirement's substance fails here even if its heading survives.
  2. THE LINKS — every relative link in it resolves FROM ITS NEW LOCATION.
     Relative links are the one thing a move breaks silently: the old paths
     were written against `architecture/wave-plans/`.
  3. THE INDEX — `standards/README.md` lists it alongside the others, which
     is the only place a reader learns the standard exists.
  4. NO SECOND ADDRESS — nothing tracked in this repo still sends a reader to
     the moved-from path. A citation that survives the move is how a reader
     ends up reading the pointer stub instead of the rule.
"""

import os
import re
import subprocess
import sys
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STANDARDS = os.path.join(REPO, "standards")
STANDARD = os.path.join(STANDARDS, "wave-plan.md")
README = os.path.join(STANDARDS, "README.md")

# The address the standard moved FROM. Nothing here may cite it any more.
OLD_PATH = "architecture/wave-plans/README.md"

# The six things a wave plan must state, each as (label, heading regex,
# substance regex). The heading proves the section is there; the substance
# proves the section still says the thing.
REQUIREMENTS = (
    (
        "research with provenance",
        r"^#{2,3} .*provenance",
        r"where it came from|who said it|source|cite",
    ),
    (
        "where research contradicted the wave",
        r"^#{2,3} .*contradict",
        r"contradict",
    ),
    (
        "decisions still open",
        r"^#{2,3} .*still open",
        r"open|undecided|unresolved",
    ),
    (
        "what the plan cuts",
        r"^#{2,3} .*cuts",
        r"cut|out of scope|not doing",
    ),
    (
        "every phase, and how it will be proven in production",
        r"^#{2,3} .*prov(en|ing) in production",
        r"in production",
    ),
    (
        "the KPIs predicted before the run",
        r"^#{2,3} .*KPI",
        r"before the (wave|run|work) (starts|begins|runs)|predicted before",
    ),
)


def body() -> str:
    with open(STANDARD, encoding="utf-8") as f:
        return f.read()


def tracked_files(*suffixes: str) -> list[str]:
    """Repo-relative paths git tracks, filtered by suffix. `git ls-files` on
    purpose: the run checks out bureau-pipeline into an untracked
    `.bureau-pipeline/` subdirectory, and a filesystem walk would read that
    second copy of every file as if it were this branch's."""
    out = subprocess.run(
        ["git", "-C", REPO, "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout.split("\n")
    return [p for p in out if p.endswith(suffixes)]


class TheStandardExistsHereTest(unittest.TestCase):
    def test_the_file_is_in_standards(self):
        self.assertTrue(
            os.path.isfile(STANDARD),
            "standards/wave-plan.md must exist — a headless agent reads "
            "standards from this directory and nowhere else",
        )

    def test_it_states_all_six_requirements(self):
        text = body()
        for label, heading, substance in REQUIREMENTS:
            with self.subTest(requirement=label):
                self.assertRegex(
                    text, re.compile(heading, re.I | re.M),
                    f"the standard has no section for {label!r}",
                )
                self.assertRegex(
                    text, re.compile(substance, re.I),
                    f"the {label!r} section states no requirement",
                )

    def test_it_is_the_standard_not_a_pointer(self):
        # The agent-bureau copy becomes a one-line pointer; this one must not.
        self.assertGreater(
            len(body().splitlines()), 30,
            "standards/wave-plan.md is too short to be the standard itself",
        )


class TheLinksResolveFromTheNewLocationTest(unittest.TestCase):
    """A relative link written against `architecture/wave-plans/` does not
    resolve from `standards/`, and markdown says nothing when it doesn't."""

    def test_every_relative_link_resolves(self):
        broken = []
        for target in re.findall(r"\]\(([^)]+)\)", body()):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            path = target.split("#", 1)[0].split(":", 1)[0].strip()
            if not path:
                continue
            if not os.path.exists(os.path.join(STANDARDS, path)):
                broken.append(target)
        self.assertEqual(
            [], broken,
            f"link(s) in standards/wave-plan.md do not resolve from "
            f"standards/: {broken}",
        )


class TheIndexListsItTest(unittest.TestCase):
    def test_readme_lists_the_new_file(self):
        with open(README, encoding="utf-8") as f:
            index = f.read()
        self.assertTrue(
            "`wave-plan.md`" in index,
            "standards/README.md must list wave-plan.md alongside the others",
        )
        row = next(
            (l for l in index.splitlines() if "`wave-plan.md`" in l), ""
        )
        self.assertRegex(
            row, r"\|.*\|.*\S.*\|",
            "wave-plan.md must be listed in the standards table WITH what it "
            "covers, not merely mentioned",
        )


class NothingCitesTheOldAddressTest(unittest.TestCase):
    def test_no_tracked_file_points_at_the_moved_from_path(self):
        offenders = []
        for rel in tracked_files(".md", ".py", ".yml", ".yaml", ".json"):
            if rel == os.path.relpath(__file__, REPO):
                continue  # names the old path to forbid it
            with open(os.path.join(REPO, rel), encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    if OLD_PATH in line:
                        offenders.append(f"{rel}:{n}")
        self.assertEqual(
            [], offenders,
            f"still cite {OLD_PATH}, which no longer holds the rules: "
            f"{offenders}",
        )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(not unittest.main(exit=False).result.wasSuccessful())
