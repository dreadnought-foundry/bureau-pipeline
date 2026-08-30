#!/usr/bin/env python3
"""The wave plan — the wave route's output, as a checkable thing (DRE-2845).

A wave-sized card produces something no other shape does: a plan for a whole
layer of work, plus the epics it commits to, in order. `standards/wave-plan.md`
says what that document must contain; nothing enforced it, so a wave plan
could arrive missing half of it and nothing would say which half.

This module is the mechanical form of that standard, and it is deliberately
`plan_artifact.py`'s shape rather than a second one — same section scanner,
same renderer, same sanitiser and the same hardened page shell, all of which
were hardened under adversarial review (DRE-2720). A hand-rolled second
renderer would re-open that ground.

  check     — the standard's sections are all present, the epics it commits to
              are in dependency order, every number is sourced or marked, and
              every citation resolves.
  epics     — the commitment, as records.
  headings  — the section headings a plan must carry, printed FROM the
              standard so the planner copies them rather than remembering.
  render    — the page, through plan_artifact.py's shell.

## The standard is the source, not a copy

The required sections are READ from `standards/wave-plan.md` in the
bureau-pipeline checkout every time the checker runs — `STANDARD_PATH` is
derived from this file's own location, so on a product repo it is
`.bureau-pipeline/standards/wave-plan.md` and never a copy the product repo
carries. Point it at a different standard and the requirements move with it.
A checker with the sections typed into it is a second source of truth, and
the copy is always the one that drifts.

The two rules this module enforces INSIDE the research come from the standard
too, so the standard is checked before the plan is: a standard that has
stopped stating the marker or the dead-citation rule is reported by
`standard_problems()` rather than quietly enforced from memory.

CLI:

    python3 scripts/wave_plan.py check plan.md [--root DIR]
    python3 scripts/wave_plan.py headings
    python3 scripts/wave_plan.py epics plan.md
    python3 scripts/wave_plan.py render plan.md --wave DRE-N -o wave.html
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_artifact  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)

# The standard this checker enforces, at the one address a headless agent can
# read it (DRE-2842). Derived from this file, so the pipeline checkout answers
# for it wherever the run happens.
STANDARD_PATH = os.path.join(ROOT, "standards", "wave-plan.md")

# The commitment block: the epics this wave signs up to, in dependency order.
# Same grammar as the artifact's ```kpis block — a list the machine reads, not
# prose a reader has to re-derive an order from.
EPICS_FENCE = "epics"
EPIC_COLUMNS = ["key", "title", "depends_on"]

# The marker the standard requires on a number nobody sourced.
UNVERIFIED = "(unverified)"

# A numbered requirement in the standard: `### 3. <what it must state>`.
_REQUIREMENT = re.compile(r"^#{3}\s+(\d+)\.\s+(?P<title>.+?)\s*$", re.MULTILINE)

# A heading in the PLAN, the number a planner may open it with, and the
# trailing clause they attach to it ("— as structured data").
_HEADING_LINE = re.compile(r"^(#{1,6})\s+(?P<text>.+?)\s*$")
_LEADING_NUMBER = re.compile(r"^\s*(\d+)\s*[.)]\s*")
_TRAILING_CLAUSE = re.compile(r"\s+(?:—|–|--|:)\s.*$")

# What counts as saying where a claim came from: a link, a run URL, a card, or
# a file (with a line, when there is one). The standard's own list.
_MD_LINK = re.compile(r"\[[^\]]*\]\(\s*([^)\s]+)[^)]*\)")
_MD_LINK_WHOLE = re.compile(r"\[[^\]]*\]\([^)]*\)")
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_URL = re.compile(r"https?://\S+")
_CARD = re.compile(r"\b[A-Z]{2,6}-\d+\b")

# A code span is read as a FILE citation only when it could be resolved: a
# path with a directory and an extension. `reconcile.py` on its own could be
# anywhere, and reporting it unresolved would be a checker inventing a defect.
_PATH_CITATION = re.compile(
    r"^[\w.@~-]+(?:/[\w.@~-]+)+\.[A-Za-z0-9]{1,6}(?::\d+(?:-\d+)?)?$")
_CITED_LINE = re.compile(r":(\d+)(?:-\d+)?$")

# Digits that are structure rather than evidence. A section reference, a date,
# a release tag and a card id are not claims about the world, and policing them
# would train a planner to write round numbers instead of sourced ones.
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_STRUCTURAL = re.compile(
    r"(?i)(?:§\s*\d+(?:\.\d+)?|\b(?:phase|wave|round|section|step|part)\s+\d+(?:\.\d+)?\b)")
_RELEASE_TAG = re.compile(r"\bv\d[\w.]*\b")
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_DIGIT = re.compile(r"\d")


class WavePlanError(Exception):
    """A malformed plan, or a standard that cannot be read — reported to the
    planner as a finding, never raised at anyone as a traceback."""


@dataclass(frozen=True)
class Requirement:
    """One thing the standard says a wave plan must state."""

    number: int
    title: str

    @property
    def key(self) -> str:
        """The section's identity in a plan, and its anchor on the page.

        Positional on purpose: the anchor a comment is bound to must survive
        the standard rewording a title.
        """
        return f"section-{self.number}"

    @property
    def heading(self) -> str:
        return f"## {self.number}. {self.title}"


# --- Reading the standard ----------------------------------------------------

_CACHE: dict[str, tuple] = {}


def standard_text(path: str | None = None) -> str:
    try:
        with open(path or STANDARD_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        raise WavePlanError(
            f"cannot read the wave-plan standard at {path or STANDARD_PATH}: "
            f"{e.strerror}"
        ) from e


def _parse_requirements(text: str) -> tuple:
    return tuple(
        Requirement(number=int(m.group(1)), title=m.group("title").strip())
        for m in _REQUIREMENT.finditer(text)
    )


def requirements(path: str | None = None) -> tuple:
    """What the standard says a plan must state, in its own order.

    Cached per path — a checker consulted once per section must not put a file
    read inside every lookup.
    """
    key = os.path.abspath(path or STANDARD_PATH)
    if key not in _CACHE:
        _CACHE[key] = _parse_requirements(standard_text(path))
    return _CACHE[key]


def _matching(reqs, word: str):
    for r in reqs:
        if word.lower() in r.title.lower():
            return r
    return None


def provenance_requirement(reqs=None):
    """The section whose subject is where a claim came from — the one the
    marker rule and the citation rule hang off. Found by the standard's own
    word rather than by position, so reordering the standard does not silently
    move the rule to another section."""
    return _matching(reqs if reqs is not None else requirements(), "provenance")


def standard_problems(path: str | None = None) -> list:
    """Everything that stops this standard being enforceable, or an empty list.

    Checked BEFORE any plan is: this module enforces two rules that live in the
    standard's own prose, and a standard that has stopped stating one of them
    must be reported rather than enforced from memory.
    """
    try:
        text = standard_text(path)
    except WavePlanError as e:
        return [str(e)]
    problems: list[str] = []
    reqs = _parse_requirements(text)
    if not reqs:
        problems.append(
            "it states no numbered requirements (`### N. …`), so there is "
            "nothing for a plan to be checked against"
        )
    if not provenance_requirement(reqs):
        problems.append(
            "no requirement is about provenance, so nothing says which "
            "section a claim's source belongs in"
        )
    if UNVERIFIED not in text:
        problems.append(
            f"it no longer states the {UNVERIFIED} marker this checker "
            "enforces on an unsourced number"
        )
    if not re.search(r"citation", text, re.IGNORECASE):
        problems.append(
            "it no longer says what a citation that does not check out is, "
            "and this checker refuses one"
        )
    return problems


# --- Reading the plan --------------------------------------------------------


def _normalize(text: str) -> str:
    text = _LEADING_NUMBER.sub("", text.strip())
    text = _TRAILING_CLAUSE.sub("", text).strip()
    text = text.replace("&", "and").lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"^the ", "", text)


def _resolver(reqs):
    """A `plan_artifact.section_bodies` resolver for this standard.

    Tolerant on the spelling, strict on the answer: a heading is a section
    when it opens with the standard's NUMBER for it, or when its words are the
    standard's words for it. Anything else reads as a heading nobody
    recognises — which must surface as a MISSING section rather than be
    absorbed as a substitute.
    """
    by_number = {r.number: r.key for r in reqs}
    by_title = {_normalize(r.title): r.key for r in reqs}

    def resolve(line: str) -> str:
        m = _HEADING_LINE.match(line.strip())
        if not m:
            return ""
        text = m.group("text")
        opener = _LEADING_NUMBER.match(text)
        if opener and int(opener.group(1)) in by_number:
            return by_number[int(opener.group(1))]
        return by_title.get(_normalize(text), "")

    return resolve


def sections(md: str, reqs=None) -> dict:
    """Section key → body text, for every section the standard names."""
    reqs = reqs if reqs is not None else requirements()
    return plan_artifact.section_bodies(md, _resolver(reqs))


def missing_sections(md: str, reqs=None) -> list:
    """The requirements this plan does not state, in the standard's order."""
    reqs = reqs if reqs is not None else requirements()
    found = sections(md, reqs)
    return [r for r in reqs if r.key not in found]


# --- The epics it commits to -------------------------------------------------


def epics(md: str) -> list:
    """The epics this wave commits to, in the order the plan lists them.

    Raises rather than returning an empty list: a wave that names no epics has
    not been decomposed, and a silent [] would let it read as one that was.
    """
    blocks = plan_artifact.fenced_blocks(md, EPICS_FENCE)
    if not blocks:
        raise WavePlanError(
            "this plan carries no ```epics block — a wave commits to a set of "
            "epics in dependency order, and prose cannot be read as an order"
        )
    try:
        data = json.loads(blocks[0])
    except json.JSONDecodeError as e:
        raise WavePlanError(f"the ```epics block is not valid JSON: {e}") from e
    if not isinstance(data, list):
        raise WavePlanError("the ```epics block must be a JSON list of records")
    return data


def epic_defects(md: str) -> list:
    """Everything wrong with the commitment, named epic by epic."""
    try:
        records = epics(md)
    except WavePlanError as e:
        return [str(e)]
    if not records:
        return ["the ```epics block is empty — a wave that commits to no epic "
                "is a title, not a plan"]

    out: list[str] = []
    keys: list = []
    for i, record in enumerate(records, 1):
        where = f"epic {i}"
        if not isinstance(record, dict):
            out.append(f"{where}: not a record")
            keys.append(None)
            continue
        key = record.get("key")
        if not (isinstance(key, str) and key.strip()):
            out.append(f"{where}: no `key` — the order is read off these keys")
            key = None
        elif key in keys:
            out.append(f"the epic key {key!r} is used twice, so the order "
                       f"cannot say which one comes first")
        keys.append(key)
        title = record.get("title")
        if not (isinstance(title, str) and title.strip()):
            out.append(f"{where} ({key!r}): no `title` — the plan must NAME "
                       f"the epics it commits to, not key them")

    position: dict = {}
    for i, key in enumerate(keys):
        if key is not None and key not in position:
            position[key] = i

    for i, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        key = keys[i]
        deps = record.get("depends_on", [])
        if not isinstance(deps, list):
            out.append(f"the epic {key!r} states `depends_on` as "
                       f"{deps!r} — it must be a list of epic keys")
            continue
        for dep in deps:
            if dep == key:
                out.append(f"the epic {key!r} depends on itself")
            elif dep not in position:
                out.append(f"the epic {key!r} depends on {dep!r}, which this "
                           f"plan does not name")
            elif position[dep] >= i:
                # One rule covers both faults: a cycle is a set of epics that
                # cannot ALL be listed after what they depend on.
                out.append(
                    f"the epics are not in dependency order: {key!r} depends "
                    f"on {dep!r}, which is listed after it"
                )
    return out


# --- Provenance: the marker, and citations that resolve ---------------------


def _strip_fences(md: str) -> str:
    """The prose, with fenced blocks blanked out and the line count kept.

    Data blocks are not claims: the KPI baselines are numbers by design, and
    the epics block is keys.
    """
    out, inside = [], False
    for line in md.split("\n"):
        if line.lstrip().startswith("```"):
            inside = not inside
            out.append("")
            continue
        out.append("" if inside else line)
    return "\n".join(out)


def _claims(body: str) -> list:
    """The body as the units a reader would argue with: one per paragraph, one
    per list item. Wrapped prose is joined, so a citation on the line under a
    claim still counts as that claim's."""
    claims, buf = [], []

    def flush():
        if buf:
            claims.append(" ".join(buf).strip())
            buf.clear()

    inside = False
    for line in body.split("\n"):
        if line.lstrip().startswith("```"):
            inside = not inside
            flush()
            continue
        if inside:
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            flush()
            continue
        if _LIST_MARKER.match(line):
            flush()
            buf.append(_LIST_MARKER.sub("", line).strip())
            continue
        buf.append(line.strip())
    flush()
    return [c for c in claims if c]


def file_citations(text: str) -> list:
    """Every citation in `text` that names a file this repo could open."""
    found: list = []
    for target in _MD_LINK.findall(text):
        target = target.strip()
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        if target and target not in found:
            found.append(target)
    for span in _CODE_SPAN.findall(text):
        span = span.strip()
        if _PATH_CITATION.match(span) and span not in found:
            found.append(span)
    return found


def _cites_something(claim: str) -> bool:
    return bool(
        _MD_LINK.search(claim)
        or _URL.search(claim)
        or _CARD.search(claim)
        or file_citations(claim)
    )


def _carries_an_unsourced_number(claim: str) -> bool:
    work = _MD_LINK_WHOLE.sub(" ", claim)
    work = re.sub(r"`[^`\n]*`", " ", work)
    for pattern in (_URL, _CARD, _DATE, _STRUCTURAL, _RELEASE_TAG):
        work = pattern.sub(" ", work)
    return bool(_DIGIT.search(work))


def default_roots() -> tuple:
    """Where a citation is resolved: the repo the run is in, then the pipeline
    checkout. A wave plan cites both, and it should not have to say which."""
    return (os.getcwd(), ROOT)


def _unresolved(target: str, roots) -> str | None:
    """Why this citation does not check out, or None when it does."""
    path = target.split("#", 1)[0].strip()
    line = None
    numbered = _CITED_LINE.search(path)
    if numbered:
        line = int(numbered.group(1))
        path = path[:numbered.start()]
    if not path:
        return None
    for root in roots:
        full = os.path.join(root, path)
        if os.path.isdir(full):
            return None
        if os.path.isfile(full):
            if line is None:
                return None
            with open(full, encoding="utf-8", errors="replace") as fh:
                count = sum(1 for _ in fh)
            if line <= count:
                return None
            return (f"{path} has only {count} lines, and the citation names "
                    f"line {line}")
    return "there is nothing at that path in this repo or the pipeline checkout"


def citation_defects(md: str, roots=None) -> list:
    """Every citation in the plan that does not check out.

    Document-wide, not research-only: a bad citation is worse than an absent
    one wherever it is written, because it looks solid, so nobody opens it —
    and the first reader who does has to distrust the whole file.

    A URL is never reported. We did not open it, and a checker that called a
    link broken on evidence it never gathered would be the same failure it is
    here to catch (standards/console-honesty.md rule 2).
    """
    roots = tuple(roots) if roots else default_roots()
    out = []
    for target in file_citations(_strip_fences(md)):
        why = _unresolved(target, roots)
        if why:
            out.append(
                f"citation does not resolve: `{target}` — {why}. A citation "
                f"that does not check out is worse than an absent one"
            )
    return out


def provenance_defects(md: str, reqs=None) -> list:
    """Every claim in the research that rests on a number nobody can check.

    Scoped to the research section because that is where the standard puts the
    rule; the resolution check above is document-wide because a dead citation
    is a defect wherever it is written.
    """
    reqs = reqs if reqs is not None else requirements()
    req = provenance_requirement(reqs)
    if req is None:
        return []  # standard_problems() already says the rule has no home
    body = sections(md, reqs).get(req.key)
    if body is None:
        return []  # missing_sections() already reports the absent section
    out = []
    for claim in _claims(body):
        if UNVERIFIED.lower() in claim.lower() or _cites_something(claim):
            continue
        if _carries_an_unsourced_number(claim):
            snippet = claim if len(claim) <= 70 else claim[:70].rstrip() + "…"
            out.append(
                f"an unsourced number needs a citation or the {UNVERIFIED} "
                f"marker: \"{snippet}\""
            )
    return out


# --- The check ---------------------------------------------------------------


def defects(md: str, roots=None, standard: str | None = None) -> list:
    """Every reason this wave plan is not ready for the CEO."""
    out = [f"the standard cannot be enforced as written: {p}"
           for p in standard_problems(standard)]
    try:
        reqs = requirements(standard)
    except WavePlanError as e:
        return out + [str(e)]

    out += [f"missing section: {r.number}. {r.title}"
            for r in missing_sections(md, reqs)]

    found = sections(md, reqs)
    kpi = _matching(reqs, "kpi")
    if kpi is not None and kpi.key in found:
        # The epic artifact's own KPI check, reused: the wave predicts numbers
        # the same way the epics under it do, so a close-out reads one grammar.
        out += plan_artifact.kpi_defects(md)

    out += epic_defects(md)
    out += provenance_defects(md, reqs)
    out += citation_defects(md, roots)
    return out


# --- Rendering ---------------------------------------------------------------


def render(md: str, wave: str, tokens_href: str | None = None,
           standard: str | None = None) -> str:
    """The page, through `plan_artifact.py`'s shell — same anchors, same
    sanitiser, same hardened header. Nothing about a wave plan needs a second
    one, and a second one is what would drift from it."""
    reqs = requirements(standard)
    found = sections(md, reqs)
    title_m = re.search(r"^#\s+(?P<t>.+)$", md, re.MULTILINE)
    title = title_m.group("t").strip() if title_m else wave
    tables = {"kpis": plan_artifact.KPI_COLUMNS, EPICS_FENCE: EPIC_COLUMNS}
    body = "\n".join(
        plan_artifact.render_section(
            r.key, found[r.key], title=f"{r.number}. {r.title}", tables=tables)
        for r in reqs if r.key in found
    )
    try:
        payload = json.dumps(plan_artifact.kpis(md))
    except plan_artifact.ArtifactError:
        payload = "[]"
    return plan_artifact.render_page(
        f"{wave} — {title}" if wave and wave not in title else title,
        body, kpi_payload=payload, tokens_href=tokens_href,
    )


# --- CLI ---------------------------------------------------------------------


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        raise WavePlanError(f"cannot read wave plan {path}: {e.strerror}") from e


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="every reason this plan is not ready")
    c.add_argument("plan")
    c.add_argument("--root", action="append", default=None,
                   help="where a citation resolves (repeatable)")
    c.add_argument("--standard", default=None)

    h = sub.add_parser("headings", help="the sections a plan must carry")
    h.add_argument("--standard", default=None)

    e = sub.add_parser("epics", help="the epics committed to, as JSON")
    e.add_argument("plan")

    r = sub.add_parser("render", help="write the page")
    r.add_argument("plan")
    r.add_argument("--wave", required=True)
    r.add_argument("-o", "--out", required=True)
    r.add_argument("--tokens", default=None)

    args = ap.parse_args(argv)

    if args.cmd == "headings":
        for req in requirements(args.standard):
            print(req.heading)
        return 0

    if args.cmd == "check":
        roots = tuple(args.root) + default_roots() if args.root else None
        found = defects(_read(args.plan), roots=roots, standard=args.standard)
        if not found:
            print("wave plan: complete")
            return 0
        print(f"wave plan: {len(found)} defect(s)")
        for d in found:
            print(f"  - {d}")
        return 1

    if args.cmd == "epics":
        print(json.dumps(epics(_read(args.plan)), indent=2))
        return 0

    if args.cmd == "render":
        page = render(_read(args.plan), args.wave, tokens_href=args.tokens)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(page)
        print(args.out)
        return 0

    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except WavePlanError as e:
        # The only abort: explicit, at top level, CLI-only — a malformed plan
        # is a finding for the planner, never a traceback.
        raise SystemExit(f"wave plan: {e}")
