#!/usr/bin/env python3
"""The planner reads an initiative's context and the epic's approved designs
(DRE-8304).

When the planner plans an epic it was handed the standards, its brief, the
split ledger, the card's thread, the repo's holds and the bound findings —
nothing from Linear's initiative, and nothing attached to the epic. The console
writes the initiative's notes to the initiative as one section headed exactly
`Context (from the console)` (DRE-8278), and uploads each approved design and
document to Linear and attaches it to every fleet epic in the initiative
(DRE-8291). No pull request is opened by the console: a design reaches the
product repo through the epic's first UI card, whose builder runs `fetch`.

## What it reads

One Linear read: `issue(id) { project { initiatives { … } } attachments { … } }`.

  * **Its initiative** is the one `Initiative` the epic's project is in. No
    project, no initiative, or more than one, and none is read — the planner
    never picks one; the status line says which. The `initiative:<x>` label is
    not read.
  * **The section** is a heading line (`#` to `######`) whose text is exactly
    `SECTION_HEADING`, and everything after it up to the next heading of the
    same or a higher level, or the end. It is looked for in the initiative's
    `content` (Linear's long body) first, then in its `description` — the two
    text fields Linear's `Initiative` carries.
  * **An approved design** is an attachment on the epic whose `url` host is
    `UPLOAD_HOST` — the host `fileUpload` returns — and whose `title`, read as
    a file name, ends in one of `DESIGN_ENDINGS`, any case. The console uploads
    only approved files, so there is no unapproved upload to tell apart. Any
    other upload is a **document**: listed, never committed, never on a Design
    line. Every attachment on another host (pull request links, plain links)
    is ignored. A title that could leave the design folder — `/`, `\\`, `..` —
    is skipped and counted. The contract with DRE-8291: the file name,
    extension included, is the attachment's `title`, and the `fileUpload`
    asset URL its `url`.

## Where a design lands

`<design folder>/approved/<initiative-slug>/<title>`, where the design folder
is `console/design` when the product repo's checkout has that directory and
`design` otherwise — the two folders `check_tdd_commits.py` already counts as
docs — and the slug is the initiative's name lowercased, every run of other
characters one `-`, trimmed.

## CLI

    plan_initiative_context.py context <CARD> [--workspace W]
        The `INITIATIVE CONTEXT:` block for the planner's assembled context.
        Always exits 0: a block read for context never fails a plan run, the
        rule `spoken_thread.py` and `plan_held_repo.py` follow.
    plan_initiative_context.py fetch <url> <dest>
        Downloads one upload with the Linear key and `Authorization` header
        `linear_ops.gql` sends. Refuses, without a request, any URL that is not
        `https://uploads.linear.app/…`. Exit 0 written, 2 refused, 1 failed.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import linear_ops  # noqa: E402
import sanitize_untrusted  # noqa: E402

#: The status line every block opens with; `plan.yml` greps it into the log.
STATUS = "INITIATIVE CONTEXT:"

#: The heading text the console writes the initiative's notes under (DRE-8278).
SECTION_HEADING = "Context (from the console)"

BEGIN = "===== BEGIN UNTRUSTED INITIATIVE CONTEXT ====="
END = "===== END UNTRUSTED INITIATIVE CONTEXT ====="

#: The host Linear's `fileUpload` hands back for an uploaded asset.
UPLOAD_HOST = "uploads.linear.app"

DESIGN_ENDINGS = (".png", ".jpg", ".jpeg", ".svg", ".html")

CONSOLE_DESIGN = "console/design"
ROOT_DESIGN = "design"

#: How much of a failed read's reason the status line carries.
REASON_CHARS = 300

QUERY = """
query PlanInitiativeContext($id: String!) {
  issue(id: $id) {
    identifier
    project {
      name
      initiatives(first: 50) { nodes { id name description content } }
    }
    attachments(first: 100) { nodes { title url } }
  }
}
"""

_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_CLOSING_HASHES = re.compile(r"(?:^|[ \t]+)#+$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")

#: A line inside the section that reads as this block's own fence or status
#: line. `sanitize_body` defangs the card-text sentinel; these are this block's.
_MIMIC = re.compile(r"UNTRUSTED\s+INITIATIVE\s+CONTEXT|^\s*INITIATIVE\s+CONTEXT\s*:",
                    re.IGNORECASE)


# --------------------------------------------------------------------------- #
# the pure parts                                                               #
# --------------------------------------------------------------------------- #


def slug(name: str) -> str:
    """`Initiatives page` → `initiatives-page`; `Q4: Portico — pilot!` →
    `q4-portico-pilot`."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _heading(line: str):
    """(level, text) when the line is an ATX heading, else None."""
    match = _HEADING.match(line)
    if not match:
        return None
    text = _CLOSING_HASHES.sub("", match.group(2) or "").strip()
    return len(match.group(1)), text


def section(text: str) -> str | None:
    """The body under the first `SECTION_HEADING` heading, up to the next
    heading of the same or a higher level; None when there is no such heading.
    A `#` line inside a code fence is not a heading."""
    level = None
    body: list[str] = []
    fence = None
    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        opener = _FENCE.match(line)
        if fence is not None:
            if opener and opener.group(1)[0] == fence[0] and len(opener.group(1)) >= len(fence):
                fence = None
            if level is not None:
                body.append(line)
            continue
        if opener:
            fence = opener.group(1)
            if level is not None:
                body.append(line)
            continue
        found = _heading(line)
        if level is None:
            if found and found[1] == SECTION_HEADING:
                level = found[0]
            continue
        if found and found[0] <= level:
            break
        body.append(line)
    if level is None:
        return None
    return "\n".join(body).strip("\n")


def _defang(text: str) -> str:
    """`sanitize_body`, then the same visible prefix on a line that mimics this
    block's fence or its status line."""
    lines = sanitize_untrusted.sanitize_body(text).split("\n")
    prefix = sanitize_untrusted.DEFANG_PREFIX
    return "\n".join(prefix + line if _MIMIC.search(line) and not line.startswith(prefix)
                     else line for line in lines)


def _is_upload(url: str) -> bool:
    try:
        host = urllib.parse.urlsplit(url or "").hostname
    except ValueError:
        return False
    return (host or "").lower() == UPLOAD_HOST


def _unsafe(title: str) -> bool:
    """A title that could leave the design folder, or is no file name at all."""
    return (not title.strip() or "/" in title or "\\" in title or ".." in title
            or any(ord(ch) < 32 for ch in title))


def classify(attachments) -> tuple[list[dict], list[dict], int]:
    """(designs, documents, skipped) from the epic's attachments, in order.
    Every attachment not on `UPLOAD_HOST` is ignored and not counted."""
    designs: list[dict] = []
    documents: list[dict] = []
    skipped = 0
    for node in attachments or []:
        title = str((node or {}).get("title") or "")
        url = str((node or {}).get("url") or "")
        if not _is_upload(url):
            continue
        if _unsafe(title):
            skipped += 1
            continue
        entry = {"title": title, "url": url}
        (designs if title.lower().endswith(DESIGN_ENDINGS) else documents).append(entry)
    return designs, documents, skipped


def design_folder(workspace: str) -> str:
    """`console/design` when the product repo's checkout has it, else `design`."""
    return CONSOLE_DESIGN if os.path.isdir(os.path.join(workspace, CONSOLE_DESIGN)) \
        else ROOT_DESIGN


def target(folder: str, initiative_slug: str, title: str) -> str:
    return f"{folder}/approved/{initiative_slug}/{title}"


# --------------------------------------------------------------------------- #
# the block                                                                    #
# --------------------------------------------------------------------------- #


def _line(text: str) -> str:
    return sanitize_untrusted.sanitize_line(str(text or ""))


def _listing(designs, documents, folder, initiative_slug) -> list[str]:
    out: list[str] = []
    if designs:
        out += ["", "Approved designs attached to this epic (title — URL — target path):"]
        out += [f"- design `{_line(d['title'])}` — {_line(d['url'])} — "
                f"`{_line(target(folder, initiative_slug, d['title']))}`" for d in designs]
    if documents:
        out += ["", "Documents attached to this epic (listed only — never committed, "
                    "never on a Design line):"]
        out += [f"- document `{_line(d['title'])}` — {_line(d['url'])}" for d in documents]
    return out


def render(issue: dict, workspace: str) -> str:
    """The block for one issue as Linear returned it."""
    project = issue.get("project")
    if not project:
        return f"{STATUS} none — the epic is in no project"
    initiatives = ((project.get("initiatives") or {}).get("nodes")) or []
    if not initiatives:
        return f"{STATUS} none — its project is in no initiative"
    if len(initiatives) > 1:
        names = ", ".join(_line(i.get("name")) for i in initiatives)
        return f"{STATUS} none — its project is in {len(initiatives)} initiatives: {names}"

    initiative = initiatives[0]
    name = _line(initiative.get("name"))
    initiative_slug = slug(initiative.get("name") or "")
    attachments = ((issue.get("attachments") or {}).get("nodes")) or []
    designs, documents, skipped = classify(attachments)
    listing = _listing(designs, documents, design_folder(workspace), initiative_slug)

    found = section(initiative.get("content") or "")
    if found is None:
        found = section(initiative.get("description") or "")
    if found is None:
        return "\n".join([f'{STATUS} none — the initiative has no "{SECTION_HEADING}" section']
                         + listing)
    return "\n".join([
        f'{STATUS} read — "{name}", slug {initiative_slug}, {len(designs)} approved '
        f"design(s), {len(documents)} document(s), {skipped} skipped",
        "",
        f"The initiative's \"{SECTION_HEADING}\" section, as the console wrote it:",
        BEGIN,
        _defang(found),
        END,
    ] + listing)


def block(card: str, *, workspace: str | None = None, gql=None) -> str:
    """The `INITIATIVE CONTEXT:` block for `card`. Never raises: a read that
    fails is `UNKNOWN` with its reason."""
    if not (card or "").strip():
        return f"{STATUS} UNKNOWN — no card identifier was given, so nothing was read"
    workspace = workspace or os.environ.get("GITHUB_WORKSPACE") or os.getcwd()
    try:
        data = (gql or linear_ops.gql)(QUERY, {"id": card})
        issue = (data or {}).get("issue")
        if not issue:
            return f"{STATUS} UNKNOWN — Linear returned no issue {_line(card)}"
        return render(issue, workspace)
    except Exception as exc:  # noqa: BLE001 — a context read never fails the run
        reason = _line(f"{type(exc).__name__}: {exc}")[:REASON_CHARS]
        return f"{STATUS} UNKNOWN — the Linear read failed: {reason}"


# --------------------------------------------------------------------------- #
# fetch                                                                        #
# --------------------------------------------------------------------------- #


class _KeepTheKeyHome(urllib.request.HTTPRedirectHandler):
    """A redirect off `UPLOAD_HOST` goes without the Linear key: urllib copies
    every header but the content ones onto the redirected request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and not _is_upload(newurl):
            new.remove_header("Authorization")
        return new


def _refusal(url: str) -> str | None:
    try:
        parts = urllib.parse.urlsplit(url or "")
        host = parts.hostname
    except ValueError:
        return "it is not a URL"
    if parts.scheme != "https":
        return f"its scheme is {parts.scheme or 'missing'}, not https"
    if (host or "").lower() != UPLOAD_HOST:
        return f"its host is {host or 'missing'}, not {UPLOAD_HOST}"
    return None


def fetch(url: str, dest: str, *, opener=None) -> int:
    """Download one upload to `dest` with the key `linear_ops.gql` sends."""
    why = _refusal(url)
    if why:
        print(f"fetch: refused {_line(url)} — {why}; nothing was requested",
              file=sys.stderr)
        return 2
    request = urllib.request.Request(url, headers={"Authorization": linear_ops.api_key()})
    opener = opener or urllib.request.build_opener(_KeepTheKeyHome).open
    try:
        # B310: the scheme and host were checked above — https://uploads.linear.app.
        with opener(request, timeout=60) as resp:  # nosec B310
            payload = resp.read()
    except OSError as exc:
        print(f"fetch: {url} failed — {exc}", file=sys.stderr)
        return 1
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(payload)
    print(f"fetch: wrote {len(payload)} bytes to {dest}")
    return 0


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    ctx = sub.add_parser("context", help="the planner's INITIATIVE CONTEXT: block")
    ctx.add_argument("card")
    ctx.add_argument("--workspace", default=None)
    got = sub.add_parser("fetch", help="download one approved design")
    got.add_argument("url")
    got.add_argument("dest")
    args = parser.parse_args(argv)
    if args.cmd == "context":
        print(block(args.card, workspace=args.workspace))
        return 0
    return fetch(args.url, args.dest)


if __name__ == "__main__":
    sys.exit(main())
