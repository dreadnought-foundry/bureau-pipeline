#!/usr/bin/env python3
"""The CEO's weekly parts list, posted once a week as one `🪴 parts-weekly:`
comment on the standing parts card (DRE-9055).

The weekly sweep (DRE-8945) leaves one folder per roster repo: the count
record `count.json` (`parts_sweep.py`, DRE-8944) and, for a counted repo, the
provisional record `provisional.json` (`parts_provisional.py`, DRE-8941).
`post` reads them and writes the week's list the way the groom scorecard
writes its week (`groom_scorecard.py`, DRE-7970): a first line, plain-English
lines, then one fenced `json` block. The console's Sunday briefing reads that
block as `sections.parts_weekly` (DRE-9057), so its shape is a contract.
`docs/parts-weekly.md` is the long form.

## The critic's reasoning goes only into the comment

Neither record carries the critic's reasoning (epic contract 5): bureau-pipeline
and its logs are public. For each new part the reasoning is read back from the
part's own pull request with `parts_provisional.final_approval`, the verdict the
scan counted. It goes into the Linear comment and nowhere else — never printed,
never written to a file. A pull request that cannot be read leaves it null, and
the comment says so.

## The week

Sunday to Saturday, Pacific, ending on `--week-ending` or by default the
Saturday before today: `groom_scorecard.week_of` and `default_week_ending`,
imported. Every time on the list is Pacific, `YYYY-MM-DD HH:MM PT`.

## Posting, once per week, and only a whole roster's list

An empty `--repos`, or one that is not a JSON list of slugs, is refused before
anything is read: once a week's list stands nothing replaces it, so a list
built from too little is never posted. The card's thread is read with
`spoken_thread.read`; a `pipeline` comment with the same first line means the
week is posted already. The comment goes through `linear_ops.cmd_comment`, and
Linear's comment cap is a failed run.

CLI:
    parts_weekly.py post --in DIR --repos <JSON list of slugs> --card <DRE-n or "">
        --qa-login <login> [--week-ending YYYY-MM-DD] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dead_run  # noqa: E402 — ONE Pacific clock
import linear_ops  # noqa: E402 — the comment write
import merge_gate  # noqa: E402 — a comment's first line
import parts_provisional  # noqa: E402 — the verdict the scan counted, reasoning included
import spoken_thread  # noqa: E402 — who said what on the standing card
from groom_scorecard import default_week_ending, week_of  # noqa: E402 — ONE week

#: Opens every log line.
TAG = "parts-weekly:"
#: Opens the comment's first line; the provisional scan reads its lookback off it.
POST_MARK = parts_provisional.WEEKLY_MARK
#: The fenced block's `schema`.
SCHEMA = "parts-weekly/1"
#: The reason a roster slug with no `count.json` is `failed`.
NO_RECORD = "its leg left no record"

COUNTED = "counted"
STATES = (COUNTED, "skipped", "unread", "archived", "failed")
PERMANENT, REJECTED = parts_provisional.PERMANENT, parts_provisional.REJECTED

_CARD = re.compile(r"^DRE-\d+$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_PR_URL = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(\d+)$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class Problem(Exception):
    """Why the run cannot answer: printed as the `UNKNOWN` line."""


def _now() -> datetime:
    return datetime.now(UTC)


def header(start: str, end: str) -> str:
    """The comment's first line, exactly."""
    return f"{POST_MARK} {start}..{end}"


def roster(text: str) -> list | None:
    """The slugs `--repos` names, in order and once each; None when it names
    none or is not a JSON list of slugs."""
    try:
        slugs = json.loads(text)
    except ValueError:
        return None
    if not isinstance(slugs, list) or not slugs:
        return None
    if not all(isinstance(slug, str) and _SLUG.match(slug) for slug in slugs):
        return None
    return list(dict.fromkeys(slugs))


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def _moment(iso) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _pt(moment: datetime) -> str:
    """`YYYY-MM-DD HH:MM PT`."""
    return dead_run.pacific(moment)


def _day(moment: datetime) -> str:
    return parts_provisional.pacific_day(moment)


# ---------------------------------------------------------------------------
# The records
# ---------------------------------------------------------------------------

def _load(path: Path) -> dict | None:
    """A record, or None when its leg wrote none."""
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise Problem(f"{path.parent.name}/{path.name} cannot be read: "
                      f"{type(exc).__name__}") from exc
    if not isinstance(record, dict):
        raise Problem(f"{path.parent.name}/{path.name} is not a JSON object")
    if record.get("repo") != path.parent.name:
        raise Problem(f"{path.parent.name}/{path.name} names another repo")
    return record


def _whole(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise Problem(f"{what} is not a count")
    return value


def repo_row(slug: str, count: dict | None) -> dict:
    """One `repos` row, from the repo's count record."""
    if count is None:
        return {"repo": slug, "state": "failed", "carded": False, "copies": None,
                "last_week": None, "change": None, "reason": NO_RECORD}
    state = count.get("state")
    if state not in STATES:
        raise Problem(f"{slug}/count.json has the state {state!r}")
    if state != COUNTED:
        return {"repo": slug, "state": state, "carded": False, "copies": None,
                "last_week": None, "change": None,
                "reason": _clean(count.get("reason") or f"{state}, with no reason recorded")}
    copies = _whole(count.get("copies"), f"{slug}'s copies")
    last = count.get("last_week")
    if last is not None:
        last = _whole(last, f"{slug}'s count a week ago")
    return {"repo": slug, "state": COUNTED, "carded": count.get("listed") is True,
            "copies": copies, "last_week": last,
            "change": None if last is None else copies - last,
            "reason": None if last is not None else
            _clean(count.get("last_week_reason") or "not counted a week ago")}


def _clean(text) -> str:
    return parts_provisional._clean(text)


def _parts(slug: str, provisional: dict) -> tuple:
    parts, rejections = provisional.get("parts"), provisional.get("rejections")
    if not isinstance(parts, list) or not isinstance(rejections, list) \
            or not all(isinstance(p, dict) for p in parts) \
            or not all(isinstance(r, dict) for r in rejections):
        raise Problem(f"{slug}/provisional.json carries no parts or rejections list")
    return parts, rejections


class _Reasoning:
    """Each new part's reasoning, read back from its pull request once per
    pull request; None for one that cannot be read."""

    def __init__(self, qa_login: str):
        self.qa_login = qa_login
        self.read = {}

    def of(self, part: dict) -> str | None:
        found = _PR_URL.match(str(part.get("pr_url") or ""))
        if not found or int(found[2]) != part["pr"]:
            return None
        key = (found[1], part["pr"])
        if key not in self.read:
            try:
                self.read[key] = parts_provisional.final_approval(
                    found[1], part["pr"], self.qa_login)
            except Exception:  # an unread pull request is a null reasoning, said in words
                self.read[key] = None
        for record in self.read[key] or ():
            if _clean(record.name) == part["name"] and _clean(record.path) == part["path"]:
                return _clean(record.reasoning)
        return None


def build(directory: Path, slugs: list, card: str, qa_login: str, start: str,
          end: str, now: datetime) -> tuple:
    """`(doc, missing)`: the fenced block's object, and the counted repos that
    left no provisional record."""
    repos, new, permanent, rejected, attempts, missing = [], [], [], [], [], []
    reasoning = _Reasoning(qa_login)
    window = timedelta(days=parts_provisional.WINDOW_DAYS)
    for slug in slugs:
        folder = directory / slug
        row = repo_row(slug, _load(folder / "count.json"))
        repos.append(row)
        provisional = _load(folder / "provisional.json")
        if provisional is None:
            if row["state"] == COUNTED:
                missing.append(slug)
            continue
        parts, rejections = _parts(slug, provisional)
        attempts += [(slug, entry) for entry in rejections]
        for part in parts:
            named = {"repo": slug, "name": _clean(part.get("name")),
                     "path": _clean(part.get("path")),
                     "pr": _whole(part.get("pr"), f"a part's pull request in {slug}")}
            approved = _moment(part.get("approved_at"))
            if approved is None:
                raise Problem(f"{slug}/provisional.json has a part with no approval time")
            if start <= _day(approved) <= end:
                closest = part.get("closest")
                new.append((approved, {
                    **named, "pr_url": _clean(part.get("pr_url")),
                    "approved_at": _pt(approved), "reasoning": None,
                    "closest": ({"name": _clean(closest.get("name")),
                                 "path": _clean(closest.get("path"))}
                                if isinstance(closest, dict) else None),
                    "permanent_on": _day(approved + window),
                    "reject_by": _pt(approved + window),
                    "reject_with": f"reject {slug}/{named['name']}"}))
            late = part.get("recorded_now") is True
            for status, field, into in ((PERMANENT, "permanent_on", permanent),
                                        (REJECTED, "rejected_on", rejected)):
                on = part.get(field)
                if part.get("status") != status or not isinstance(on, str):
                    continue
                if start <= on <= end or (late and on < start):
                    row = {**named, field: on}
                    if status == REJECTED:
                        row["replacement_card"] = part.get("replacement_card") or None
                    into.append({**row, "late": on < start})
    new.sort(key=lambda item: (item[0], item[1]["repo"], item[1]["name"]))
    new_parts = [row for _, row in new]
    for row in new_parts:
        row["reasoning"] = reasoning.of(row)
    return {"schema": SCHEMA, "week_start": start, "week_end": end,
            "posted_at": _pt(now), "card": card, "new_parts": new_parts,
            "became_permanent": permanent, "rejected": rejected,
            "not_counted": not_counted(attempts, start, end), "repos": repos}, missing


def not_counted(attempts: list, start: str, end: str) -> list:
    """Every rejection message no repo counted, signed inside the week, once
    per signed time. One message is read by every repo's scan, so a time any
    repo counted is a counted message; of the rest, the repo the message names
    says why it did not count."""
    counted = {_moment(e.get("at")) for _, e in attempts if e.get("counted") is True}
    chosen = {}
    for slug, entry in attempts:
        at = _moment(entry.get("at"))
        if entry.get("counted") is not False or at is None or at in counted:
            continue
        if not start <= _day(at) <= end:
            continue
        own = str(entry.get("part") or "").startswith(f"{slug}/")
        if at not in chosen or (own and not chosen[at][0]):
            chosen[at] = (own, entry)
    return [{"at": _pt(at), "why": _clean(chosen[at][1].get("why") or "no reason recorded")}
            for at in sorted(chosen)]


# ---------------------------------------------------------------------------
# The comment
# ---------------------------------------------------------------------------

def _change(change: int) -> str:
    return "no change" if change == 0 else f"+{change}" if change > 0 else f"−{-change}"


def render_comment(doc: dict, missing: list) -> str:
    """The comment: the header, plain-English lines, one fenced JSON block."""
    start, end, card = doc["week_start"], doc["week_end"], doc["card"]
    w = [header(start, end), "",
         f"The shared parts week on {card}, Sunday {start} to Saturday {end}. "
         f"Every time here is Pacific.", ""]

    w.append(f"**New parts this week: {len(doc['new_parts'])}**")
    for p in doc["new_parts"]:
        w.append(f"- {p['repo']} — {p['name']}, at `{p['path']}`, added by pull request "
                 f"#{p['pr']} ({p['pr_url']}), approved {p['approved_at']}.")
        w.append(f"  - Why the critic approved it: {p['reasoning']}" if p["reasoning"]
                 else "  - The critic's reasoning could not be read from its pull request.")
        closest = p["closest"]
        w.append(f"  - The closest existing part: {closest['name']}, at `{closest['path']}`."
                 if closest else "  - No existing part was found to compare it with.")
        w.append(f"  - It becomes permanent on {p['permanent_on']}. To reject {p['name']}, "
                 f"write on {card} from the console a message that starts with \"reject\" "
                 f"and names {p['repo']}/{p['name']}, before {p['reject_by']}. A message "
                 f"written in Linear does not count. If nobody rejects it, it becomes "
                 f"permanent then.")
    if not doc["new_parts"]:
        w.append("- None.")

    w += ["", f"**Became permanent this week: {len(doc['became_permanent'])}**"]
    for p in doc["became_permanent"]:
        late = " (recorded late, so listed this week)" if p["late"] else ""
        w.append(f"- {p['repo']} — {p['name']}, at `{p['path']}`, pull request #{p['pr']}: "
                 f"permanent on {p['permanent_on']}{late}.")
    if not doc["became_permanent"]:
        w.append("- None.")

    w += ["", f"**Rejected this week: {len(doc['rejected'])}**"]
    for p in doc["rejected"]:
        late = " (recorded late, so listed this week)" if p["late"] else ""
        filed = (f"its replacement card is {p['replacement_card']}" if p["replacement_card"]
                 else "no replacement card is filed yet")
        w.append(f"- {p['repo']} — {p['name']}, at `{p['path']}`, pull request #{p['pr']}: "
                 f"rejected on {p['rejected_on']}{late}; {filed}.")
    if not doc["rejected"]:
        w.append("- None.")

    w += ["", f"**Rejection messages that did not count: {len(doc['not_counted'])}**"]
    w += [f"- {e['at']} — {e['why']}." for e in doc["not_counted"]] or ["- None."]

    w += ["", "**Copies of shared parts, by repo**"]
    for r in doc["repos"]:
        if r["state"] != COUNTED:
            w.append(f"- {r['repo']}: {r['state']} — {r['reason']}.")
            continue
        said = (f"{r['copies']} copies now, {r['last_week']} last week ({_change(r['change'])})"
                if r["last_week"] is not None
                else f"{r['copies']} copies now, not counted a week ago — {r['reason']}")
        tail = "" if r["carded"] else "; not carded yet (no entry in config/parts-count.json)"
        w.append(f"- {r['repo']}: {said}{tail}.")
    if missing:
        w += ["", "The provisional parts of these counted repos could not be read, because "
                  "their leg left no provisional record: " + ", ".join(missing) + "."]

    w += ["", "```json", json.dumps(doc, indent=2, ensure_ascii=False), "```"]
    return "\n".join(w) + "\n"


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def _posted(card: str, first: str) -> bool:
    """Whether the pipeline already posted this week's list on `card`. Only
    the pipeline's own comment counts: a person's, an integration's, or one
    whose text is withheld (`spoken_thread.REFUSED`, `spoken_thread.UNCHECKED`)
    never stands for the list."""
    try:
        voices = list(spoken_thread.read(card))
    except Exception as exc:  # said as UNKNOWN, never read as an empty thread
        raise Problem(f"the thread of {card} cannot be read: "
                      f"{type(exc).__name__}: {exc}") from exc
    return any(v.kind == spoken_thread.PIPELINE and v.body is not None
               and merge_gate.first_line(v.body.lstrip()).strip() == first
               for v in voices)


def post(args, log) -> int:
    try:
        start, end = week_of(args.week_ending or default_week_ending(_now()))
    except ValueError as exc:
        raise Problem(f"--week-ending {args.week_ending!r}: {exc}") from exc
    slugs = roster(args.repos)
    if slugs is None:
        log(f"{TAG} not posted {start}..{end} — the roster named no repos")
        return 1
    if args.card and not _CARD.match(args.card):
        raise Problem(f"--card {args.card!r} is not a card id (DRE-<n>) or \"\"")
    if not args.card:
        log(f"{TAG} not posted {start}..{end} — no card set")
        return 0
    card, first = args.card, header(start, end)
    if not args.dry_run and _posted(card, first):
        log(f"{TAG} already posted {start}..{end} on {card}")
        return 0

    doc, missing = build(Path(args.directory), slugs, card, args.qa_login, start, end, _now())
    unread = sum(1 for p in doc["new_parts"] if p["reasoning"] is None)
    if unread:
        log(f"{TAG} {unread} part(s) whose reasoning could not be read")
    if args.dry_run:
        log(f"{TAG} would post {start}..{end} on {card} — {len(doc['new_parts'])} new part(s)")
        return 0

    # `cmd_comment` returns the comment-cap condition rather than raising
    # (DRE-3343): a list that did not land is a failed run.
    try:
        refused = linear_ops.cmd_comment(card, render_comment(doc, missing))
    except Exception as exc:  # its message may quote the body, which holds reasoning
        raise Problem(f"Linear would not take the comment on {card}: "
                      f"{type(exc).__name__}") from None
    if refused:
        log(f"{TAG} not posted {start}..{end} — {card} is at Linear's comment limit")
        return 1
    copies = sum(r["copies"] for r in doc["repos"] if r["state"] == COUNTED)
    log(f"{TAG} posted {start}..{end} on {card} — {len(doc['new_parts'])} new part(s), "
        f"{len(doc['became_permanent'])} permanent, {len(doc['rejected'])} rejected, "
        f"{len(doc['not_counted'])} not counted, {copies} copies across "
        f"{len(doc['repos'])} repo(s)")
    return 0


def cmd_post(args) -> int:
    def log(line):
        print(line, file=sys.stderr)

    try:
        return post(args, log)
    except Exception as exc:  # the run always answers
        problem = str(exc) if isinstance(exc, Problem) else f"{type(exc).__name__}: {exc}"
        log(f"{TAG} UNKNOWN — {_CONTROL.sub(' ', problem)}")
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("post", help="post the week's parts list on the standing card")
    command.add_argument("--in", dest="directory", required=True)
    command.add_argument("--repos", required=True)
    command.add_argument("--card", required=True)
    command.add_argument("--qa-login", required=True)
    command.add_argument("--week-ending", default=None)
    command.add_argument("--dry-run", action="store_true")
    command.set_defaults(func=cmd_post)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
