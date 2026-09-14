#!/usr/bin/env python3
"""The release train writes a Linear release for every surface that names a pipeline (DRE-3854).

WHY
---
`Done` means merged; a RELEASE says what is live (agent-bureau DRE-2711). Until
this module the only surfaces whose releases reached Linear were agent-bureau's
three, and each of them got there because its OWN surface script called
agent-bureau's `scripts/linear_release.py`. Every other project released by git
tag alone — Portico's portals surface among them — so the CEO could read in
Linear what had merged but not what had gone live. The CEO asked on 2026-09-13
for releases in Linear "for Portico and all the rest of the projects".

Copying that script into every repo's surface script is the wrong seam: it is
~1,500 lines, and every copy is a second writer free to drift. The train is
already the one thing every surface goes through, and it re-checks-out
bureau-pipeline at `pipeline_ref` for its own scripts, so a module HERE is
available to every caller's train with nothing to install. A project opts in by
declaring one key in its `.github/bureau/release.json`:

    "linear_pipelines": {"<surface>": "<Linear release pipeline id>"}

A TOP-LEVEL key, not a field of the surface, on purpose. The schema check
refuses an unknown surface field ("is not a field of the surface schema"), and
a caller's train reads the schema at `@stable` — so a surface field added
before this change reached `stable` would stop that repo's train at `plan`.
The schema has never read anything at the top level but `surfaces`, so the
declaration can land in any order against the channel.

THE SHAPE, AND WHY IT IS AFTER THE TAG
--------------------------------------
agent-bureau's surface scripts cut a release in two phases — sync before the
rollout and park it in a `Deploying` stage (merged, not live), complete after
the tag. The train cannot: the VERSION is the tag, and the surface script
computes the tag itself, last (Portico's at step 11). So the train writes the
release once the script has cut a tag and the train has VERIFIED it — an
annotated tag in the series, at the released commit — which is exactly the
moment the surface is live:

    releaseSync      version = the tag, commitSha = the released commit,
                     the cards the range names          → lands completed
    releaseComplete  the same version and sha           → `completedAt` set,
                                                           whatever the stage
    notes            read the release back, then create or update ONE note

Nothing is lost that the tag does not already carry: a lap that dies before its
tag cuts no tag, so deploy-lag reads the surface BEHIND and the merged-not-live
fact is visible without the stage. What IS gained is that no release in Linear
can ever read live before the surface is.

The mutations are the workspace-key forms (`releaseSync` /
`releaseComplete` with `pipelineId`, `releaseNoteCreate` / `releaseNoteUpdate`,
`releaseUpdateByPipeline` as the failure carrier) driven live on 2026-09-12 for
agent-bureau DRE-3707, with the same facts carried over: `releaseSync` on a
continuous pipeline lands the release completed; `releaseNoteCreate` does NOT
upsert, so an existing note is updated; our note written promptly pre-empts
Linear's ~60s auto-generated one; a description-only `releaseUpdateByPipeline`
leaves a completed release completed. The access-key route is never taken
here: an access key infers its own pipeline, and the train hands the caller's
`LINEAR_RELEASE_KEY` to every surface.

WHICH CARDS
-----------
The range is the surface's previous tag in its series to the released commit —
the same record the train reads to decide the surface is behind. The walk is
the default branch's first-parent line; a change counts if its diff against
its first parent touches the surface's `paths` outside any narrower path
another surface owns, and its cards are the ones its own message names (for a
merge, the `agent/DRE-<n>-<slug>` branch in the subject), falling back to the
SUBJECTS of the commits it carried only when it names none. That is agent-
bureau's DRE-3707 rule, ported. A first release — no previous tag — attaches
NOTHING, rather than every card the repository has ever had.

WHAT IT NEVER DOES
------------------
It never fails a release, and never changes the train's decision. The surface
is live the moment its tag is verified; a Linear outage, a missing key or a
refused mutation is a `WARNING` line and the receipt the train prints is the
one it would have printed anyway. A repo that declares no pipeline for a
surface is not touched and prints nothing.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

#: The top-level key of `.github/bureau/release.json` that opts a surface in.
DATA_KEY = "linear_pipelines"

#: The workspace key the train passes through from the caller's secrets.
API_KEY_ENV = "LINEAR_API_KEY"

#: Opens every line this module prints, inside the train's own `[surface]`.
TAG = "linear-release"

#: Every repo in the fleet shares the one Linear team, `DRE`, so an identifier
#: is a card and anything else in a commit subject is not.
ISSUE_ID = re.compile(r"\bDRE-\d+\b")

#: A Linear id is a UUID. Checked so a pasted slug or URL is refused by the
#: schema check, naming the key, rather than sent to Linear on a live release.
PIPELINE_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

#: What a bullet says for a card with neither a title nor a `## Why`.
NO_TITLE = "(this card has no title)"

#: The floor under the em-dash cut in `bullet_text` (agent-bureau DRE-3458).
TITLE_HEAD_FLOOR = 30

WHY_SECTION = re.compile(r"^#{2,}\s*Why\s*$(.*?)(?=^#{1,6}\s|\Z)", re.M | re.S)

#: What Linear answers when the version is already in the pipeline.
ALREADY_EXISTS = "already exists"


class ReleaseLinearError(RuntimeError):
    """Anything that stops the Linear release being written. Never fatal."""


# --------------------------------------------------------------------------- #
# The declaration                                                              #
# --------------------------------------------------------------------------- #


def check(data) -> list:
    """Every problem with the `linear_pipelines` key, each naming what is wrong.

    Absent is not a problem: most surfaces name no pipeline. Called by the
    train's schema check, so a malformed declaration is refused at `plan` with
    the key named, never discovered by a live release.
    """
    if not isinstance(data, dict) or DATA_KEY not in data:
        return []
    declared = data[DATA_KEY]
    if not isinstance(declared, dict):
        return [f"{DATA_KEY}: must be an object of `<surface>: <pipeline id>`"]
    surfaces = data.get("surfaces") if isinstance(data.get("surfaces"), dict) else {}
    problems = []
    for name, pipeline_id in declared.items():
        where = f"{DATA_KEY}.{name}"
        if name not in surfaces:
            problems.append(f"{where}: names no declared surface")
            continue
        entry = surfaces[name] if isinstance(surfaces[name], dict) else {}
        if entry.get("record", "tag") != "tag":
            problems.append(
                f"{where}: a `{entry.get('record')}` surface cuts no version tag "
                f"and the train never runs it, so it has no release to write")
        if not isinstance(pipeline_id, str) or not PIPELINE_ID.match(pipeline_id):
            problems.append(
                f"{where}: must be the Linear release pipeline's id (a UUID), "
                f"not {pipeline_id!r}")
    return problems


def pipeline_for(data, surface_name: str) -> str | None:
    """The pipeline id this surface declares, or `None`."""
    declared = data.get(DATA_KEY) if isinstance(data, dict) else None
    if not isinstance(declared, dict):
        return None
    value = declared.get(surface_name)
    return value if isinstance(value, str) and value else None


def _under(path: str, prefix: str) -> bool:
    """`path` is `prefix` or lies inside it — a leading directory, never a
    substring, the way `git diff -- <prefix>` reads a plain path."""
    bare = prefix.rstrip("/")
    return path == bare or path.startswith(bare + "/")


def surface_filter(surface_name: str, data: dict) -> tuple:
    """`(paths, exclude)` for one surface: its own `paths`, and every path
    ANOTHER surface declares strictly inside one of them. `[]` means every
    commit, so a `[]` surface excludes every other surface's paths."""
    surfaces = data.get("surfaces") or {}
    own = tuple((surfaces.get(surface_name) or {}).get("paths") or ())
    exclude = sorted({
        path
        for name, other in surfaces.items() if name != surface_name
        for path in ((other or {}).get("paths") or ())
        if not own or any(_under(path, mine) and path.rstrip("/") != mine.rstrip("/")
                          for mine in own)
    })
    return own, tuple(exclude)


def touches(files, paths, exclude) -> bool:
    """One file under the surface's paths and outside `exclude` is enough."""
    return any((not paths or any(_under(f, p) for p in paths))
               and not any(_under(f, x) for x in exclude)
               for f in files)


# --------------------------------------------------------------------------- #
# Which cards ride on this release                                             #
# --------------------------------------------------------------------------- #


def _git(repo_root, *args) -> str:
    done = subprocess.run(["git", "-C", str(repo_root), *args],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise ReleaseLinearError(
            f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def changes_since(repo_root, base: str, head: str, paths=(), exclude=()) -> list:
    """One `(sha, message)` per change on the first-parent line in
    `base..head` that touched the surface — oldest first.

    `message` is the change's own message; for a merge that names no card it is
    followed by the SUBJECTS of the commits it carried (a commit body cites
    other cards as history, and a card that is mentioned has not shipped).
    """
    walk = _git(repo_root, "log", "--first-parent", "--reverse",
                "--diff-merges=first-parent", "--name-only",
                "--format=%x1e%H%x1f%P%x1f%B%x1d", f"{base}..{head}")
    changes = []
    for record in walk.split("\x1e"):
        if not record.strip():
            continue
        header, _, file_block = record.partition("\x1d")
        sha, _, rest = header.partition("\x1f")
        parents, _, message = rest.partition("\x1f")
        files = [line for line in file_block.splitlines() if line.strip()]
        if not touches(files, paths, exclude):
            continue
        message = message.strip("\n")
        parents = parents.split()
        if len(parents) > 1 and not ISSUE_ID.search(message):
            carried = _git(repo_root, "log", "--format=%s", f"^{parents[0]}",
                           *parents[1:])
            subjects = [line for line in carried.splitlines() if line.strip()]
            message = "\n".join([message, *reversed(subjects)])
        changes.append((sha, message))
    return changes


def issue_references(changes) -> list:
    """`IssueReferenceInput` for every card the changes name, each once,
    carrying the first change that named it."""
    references, seen = [], set()
    for sha, message in changes:
        for identifier in ISSUE_ID.findall(message):
            if identifier not in seen:
                seen.add(identifier)
                references.append({"identifier": identifier, "commitSha": sha})
    return references


def uncarded(changes) -> list:
    """The short shas of the changes that name no card."""
    return [sha[:7] for sha, message in changes if not ISSUE_ID.search(message)]


# --------------------------------------------------------------------------- #
# What the note says (agent-bureau DRE-3458, ported)                           #
# --------------------------------------------------------------------------- #


def first_sentence(text: str) -> str:
    flat = " ".join((text or "").split())
    head, stop, _ = flat.partition(". ")
    return f"{head}." if stop else flat


def bullet_text(card: dict) -> str:
    """The title carries the line; the first sentence of `## Why` only when the
    title is empty. A long head before ` — ` stands alone."""
    title = " ".join((card.get("title") or "").split())
    if title.startswith("[EPIC]"):
        title = title[len("[EPIC]"):].strip()
    if not title:
        match = WHY_SECTION.search(card.get("description") or "")
        title = first_sentence(match.group(1)) if match else ""
    if not title:
        return NO_TITLE
    head, dash, _ = title.partition(" — ")
    if dash and len(head) >= TITLE_HEAD_FLOOR:
        title = head.rstrip(" ,;:")
    return title if title.endswith((".", "!", "?")) else f"{title}."


def _card_number(identifier: str) -> int:
    _, _, number = (identifier or "").partition("-")
    return int(number) if number.isdigit() else -1


def assemble_note(*, label: str, version: str, cards, unnamed, first: bool) -> str:
    """A headline, one bullet per card (newest card first), and one line about
    everything else. `unnamed` of `None` is UNKNOWN, rendered as UNKNOWN."""
    short = version[len(label) + 1:] if version.startswith(f"{label}-") else version
    lines = [f"## {label} {short}", ""]
    if cards:
        for card in sorted(cards, key=lambda c: _card_number(c.get("identifier", "")),
                           reverse=True):
            lines.append(f"* {bullet_text(card)} ({card['identifier']})")
    else:
        lines.append("No cards are attached to this release.")
    lines.append("")
    if first:
        lines.append("The first release in this series — there is no earlier "
                     "release to count changes from.")
    elif unnamed is None:
        lines.append("Changes with no card: UNKNOWN — the commit range could "
                     "not be read.")
    elif unnamed:
        count = len(unnamed)
        lines.append(f"{count} change{'' if count == 1 else 's'} with no card: "
                     f"{', '.join(unnamed)}")
    else:
        lines.append("Every change in this release names a card.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The writes                                                                   #
# --------------------------------------------------------------------------- #

_RELEASE_FIELDS = "id version commitSha completedAt url stage { name type }"

SYNC_MUTATION = """
mutation($input: ReleaseSyncInput!) {
  releaseSync(input: $input) { success release { %s } }
}""" % _RELEASE_FIELDS

COMPLETE_MUTATION = """
mutation($input: ReleaseCompleteInput!) {
  releaseComplete(input: $input) { success release { %s } }
}""" % _RELEASE_FIELDS

NOTES_SOURCE_QUERY = """
query($id: String!) {
  release(id: $id) {
    id version url
    releaseNotes { id }
    issues(first: 100) { nodes { identifier title description } }
  }
}"""

NOTE_CREATE_MUTATION = """
mutation($input: ReleaseNoteCreateInput!) {
  releaseNoteCreate(input: $input) { success releaseNote { id url } }
}"""

NOTE_UPDATE_MUTATION = """
mutation($id: String!, $input: ReleaseNoteUpdateInput!) {
  releaseNoteUpdate(id: $id, input: $input) { success releaseNote { id url } }
}"""

DESCRIPTION_MUTATION = """
mutation($input: ReleaseUpdateByPipelineInput!) {
  releaseUpdateByPipeline(input: $input) { success release { id } }
}"""


def _default_call(query: str, variables: dict) -> dict:
    """`linear_ops.gql` — the fleet's one Linear client: its retry, its
    RATELIMITED stop and its budget line. Imported late so a train that
    declares no pipeline never loads it."""
    import linear_ops  # noqa: E402
    try:
        return linear_ops.gql(query, variables)
    except linear_ops.LinearError as error:
        raise ReleaseLinearError(str(error)) from error


def repository_input(repo: str) -> dict:
    owner, _, name = repo.partition("/")
    return {"owner": owner, "name": name, "provider": "github",
            "url": f"https://github.com/{owner}/{name}"}


def write(*, data, surface_name: str, repo: str, repo_root, version: str,
          sha: str, previous_tag: str | None, call=None, env=None,
          out=print) -> dict | None:
    """Write this release to the surface's Linear pipeline. NEVER raises.

    Returns `None` when the surface declares no pipeline (and prints nothing),
    otherwise a summary: `{"pipeline", "cards", "note", "problem"}` where
    `problem` is `None` on success and the first thing that went wrong
    otherwise.
    """
    pipeline_id = pipeline_for(data, surface_name)
    if not pipeline_id:
        return None
    environ = os.environ if env is None else env
    summary = {"pipeline": pipeline_id, "cards": None, "note": None, "problem": None}

    def warn(message: str) -> dict:
        summary["problem"] = message
        out(f"{TAG}: WARNING {message} — the release itself is unaffected")
        return summary

    if not (environ.get(API_KEY_ENV) or "").strip():
        return warn(f"{API_KEY_ENV} is not set, so {version} was not written to "
                    f"Linear pipeline {pipeline_id}")
    call = call or _default_call

    try:
        paths, exclude = surface_filter(surface_name, data)
        if previous_tag:
            changes = changes_since(repo_root, previous_tag, sha, paths, exclude)
        else:
            changes = []
        references = issue_references(changes)
        named = [reference["identifier"] for reference in references]
        out(f"{TAG}: {version} — since {previous_tag or 'nothing (first release)'}; "
            f"cards named: {len(named)}{' — ' + ', '.join(named) if named else ''}")

        try:
            release = call(SYNC_MUTATION, {"input": {
                "pipelineId": pipeline_id,
                "name": version,
                "version": version,
                "commitSha": sha,
                "issueReferences": references,
                "repository": repository_input(repo),
            }})["releaseSync"]["release"]
        except ReleaseLinearError as error:
            if ALREADY_EXISTS in str(error).lower():
                return warn(f"a release named {version} already exists in pipeline "
                            f"{pipeline_id} and carries a different commit; no cards "
                            f"were attached ({error})")
            raise
        release = call(COMPLETE_MUTATION, {"input": {
            "pipelineId": pipeline_id, "version": version, "commitSha": sha,
        }})["releaseComplete"]["release"] or release
        out(f"{TAG}: {version} is live in Linear — {release.get('url', '')}")

        read = call(NOTES_SOURCE_QUERY, {"id": release["id"]})["release"]
        cards = (read.get("issues") or {}).get("nodes") or []
        summary["cards"] = [card["identifier"] for card in cards]
        dropped = sorted(set(named) - set(summary["cards"]))
        if dropped:
            out(f"{TAG}: WARNING Linear did not attach: {', '.join(dropped)}")
        label = f"{repo.partition('/')[2] or repo}-{surface_name}"
        note = assemble_note(label=label, version=version, cards=cards,
                             unnamed=uncarded(changes), first=not previous_tag)
        existing = [entry["id"] for entry in read.get("releaseNotes") or []]
        try:
            if existing:
                call(NOTE_UPDATE_MUTATION, {"id": existing[0], "input": {"content": note}})
                summary["note"] = "updated"
            else:
                call(NOTE_CREATE_MUTATION, {"input": {
                    "pipelineId": pipeline_id, "releaseIds": [release["id"]],
                    "content": note}})
                summary["note"] = "created"
        except (ReleaseLinearError, KeyError, TypeError) as error:
            try:
                call(DESCRIPTION_MUTATION, {"input": {
                    "pipelineId": pipeline_id, "version": version,
                    "description": (f"Release notes could not be written as a note "
                                    f"({error}). They follow.\n\n{note}")}})
            except (ReleaseLinearError, KeyError, TypeError):
                pass
            return warn(f"the notes for {version} were not written as a note ({error})")
        out(f"{TAG}: notes {summary['note']}; cards on the release: "
            f"{len(summary['cards'])}")
        return summary
    except Exception as error:  # noqa: BLE001 — never fail a release on Linear
        return warn(f"{version} was not fully written to Linear pipeline "
                    f"{pipeline_id}: {error}")
