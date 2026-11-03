#!/usr/bin/env python3
"""A provisional part's two weeks: permanent after 14 days with no rejection,
recorded beside its approval, and one replacement card for a rejected part
(DRE-8941, the parts epic's rule 11).

For one repo, inside that repo's checkout, `scan` reads the provisional
approvals the design critic recorded on merged pull requests and decides where
each part stands on `--now`: still `provisional`, `permanent` or `rejected`.
It writes the outcome as one new comment on the part's own pull request and,
on a rejection, files one Intake card to remove the part and replace its uses.
The weekly sweep runs it once per roster row (DRE-8945) and the weekly post
reads its JSON (`parts_weekly.py`). `docs/parts-provisional.md` is the long
form; the contracts it follows are the epic's 3, 4 and 5.

## The critic's reasoning is never written here

bureau-pipeline is public. The reasoning on a `🪴 part-provisional:` line goes
only into the weekly Linear post, which reads it back from the pull request
through `final_approval`. Nothing this script prints, and nothing in its JSON,
carries it: part names, paths, repo slugs, pull request numbers and card ids
only.

## Which verdict counts

Exactly one verdict on a merged pull request: the newest critic verdict by
`--qa-login` created at or before `merged_at` — the one the merge gate merged
on (`merge_gate.latest_verdict_record`). Its first line must carry `APPROVE`;
its `🪴 part-provisional:` lines (`parts_review.provisional_records`) are the
approved parts, and its `created_at` starts their clock. One part is one
`(name, path)` in the repo; the earlier approval stands.

## Which pull requests

Those merged on or after `since`: 35 days (`LOOKBACK_DAYS`) before the newest
`🪴 parts-weekly:` comment the pipeline posted on `--card`, `PROVISIONAL_SINCE`
when there is none, 35 days before `--now` with `--card ""`, and never earlier
than `PROVISIONAL_SINCE`.

## The record, and the rejection

`🪴 part-permanent: <Name> at <path> — <YYYY-MM-DD>` or `🪴 part-rejected: …`,
one line in one new comment, read back only from `--writer-login`. A part that
carries one keeps it. A rejection counts when it is the CEO's, via the console
(`spoken_thread.read`), its words open `reject` and name `<slug>/<Name>`, and
it was signed inside the part's 14 days. An `unchecked` message, a full thread
that may have cut a window short, a refused write or a failed read prints
`UNKNOWN` and exits 1.

CLI:
    parts_provisional.py scan --repo <slug> --full-name <owner/name> --repo-root DIR
        --qa-login <login> --writer-login <login> --card <DRE-n or "">
        --agent <agent:label> --initiative <initiative:label> --json-out FILE
        [--now <ISO 8601>] [--dry-run]
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess  # nosec B404 — fixed argv, the gh CLI
import sys
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import console_receipt  # noqa: E402 — the answer's words and its signed time
import dead_run  # noqa: E402 — ONE Pacific clock
import design_parts  # noqa: E402 — the catalog
import gh_read_retry  # noqa: E402 — every GitHub read
import linear_ops  # noqa: E402 — the create seam and the comment window
import merge_gate  # noqa: E402 — the verdict the gate merged on
import parts_cleanup  # noqa: E402 — the search for a card already filed
import parts_review  # noqa: E402 — the provisional lines, props and similarity
import spoken_thread  # noqa: E402 — who said what on the standing card

#: Opens every log line.
TAG = "parts-provisional:"
#: The two record lines, one per comment, below the critic's approval.
PERMANENT_MARK = "🪴 part-permanent:"
REJECTED_MARK = "🪴 part-rejected:"
#: Opens the replacement card's identity line.
IDENTITY_MARK = "parts-rejected:"
#: Opens the first line of the weekly post on the standing card.
WEEKLY_MARK = "🪴 parts-weekly:"
#: A provisional part's window.
WINDOW_DAYS = 14
#: How far before the newest weekly post the scan reads.
LOOKBACK_DAYS = 35
#: The Pacific day the critic's `🪴 part-provisional:` line first reached
#: `main`: the first commit `git log --reverse --format=%cI
#: -S'part-provisional:' -- briefs/critic.md scripts/parts_review.py` lists is
#: 2ee57fed (DRE-8719), 2026-11-02T05:27:03Z, 21:27 PST on the 1st.
PROVISIONAL_SINCE = "2026-11-01"
#: Where a replacement card is filed: the front door.
LANE = "Intake"
AGENT_PREFIX = parts_cleanup.AGENT_PREFIX
INITIATIVE_PREFIX = parts_cleanup.INITIATIVE_PREFIX

PROVISIONAL, PERMANENT, REJECTED = "provisional", "permanent", "rejected"
PER_PAGE = 100

_CARD = re.compile(r"^DRE-\d+$")
_FULL_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_RECORD = re.compile(
    rf"^\s*({re.escape(PERMANENT_MARK)}|{re.escape(REJECTED_MARK)}) (.+?) at (.+?) — "
    rf"(\d{{4}}-\d{{2}}-\d{{2}})\s*$")
#: `<slug>/<Name>` in a rejection's words — never the tail of a longer path.
_NAMED = re.compile(r"(?<![\w./-])([a-z0-9][a-z0-9._-]*)/([A-Z][A-Za-z0-9]*)(?![\w])")


class Problem(Exception):
    """Why the run cannot answer: printed as the `UNKNOWN` line."""


class Approval(NamedTuple):
    name: str
    path: str
    pr: int
    pr_url: str
    approved_at: datetime


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def _moment(iso) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def pacific_day(moment: datetime) -> str:
    """The Pacific `YYYY-MM-DD` of a moment."""
    return dead_run.pacific(moment).split(" ")[0]


def _clean(text) -> str:
    """Untrusted text, written to a log, a comment or a card: one line, no
    verdict marker."""
    return merge_gate.without_verdict_markers(_CONTROL.sub(" ", str(text))).strip()


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

def _read(args: list, what: str):
    try:
        text = gh_read_retry.read(args, log=lambda line: print(line, file=sys.stderr))
    except gh_read_retry.GhReadError as exc:
        raise Problem(f"GitHub would not give {what}: {exc}") from exc
    try:
        return json.loads(text or "null")
    except ValueError as exc:
        raise Problem(f"GitHub's answer for {what} is not JSON") from exc


def merged_pulls(full_name: str, since: str) -> list:
    """Every pull request merged on or after the Pacific day `since`, oldest
    merge first. Closed pull requests are read newest-updated first, so the
    walk stops at the first page that ends before `since`: a merge on or after
    it was an update on or after it."""
    found, page = {}, 1
    while True:
        rows = _read(["api", f"repos/{full_name}/pulls?state=closed&sort=updated"
                             f"&direction=desc&per_page={PER_PAGE}&page={page}"],
                     f"{full_name}'s closed pull requests (page {page})")
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise Problem(f"GitHub's list of {full_name}'s closed pull requests is not a list")
        for row in rows:
            merged = _moment(row.get("merged_at"))
            if merged is not None and pacific_day(merged) >= since:
                found[row.get("number")] = row
        last = _moment(rows[-1].get("updated_at")) if rows else None
        if len(rows) < PER_PAGE or (last is not None and pacific_day(last) < since):
            break
        page += 1
    return sorted(found.values(), key=lambda r: (_moment(r["merged_at"]), r["number"]))


def pr_comments(full_name: str, number: int) -> list:
    data = _read(["api", "--paginate", "--slurp",
                  f"repos/{full_name}/issues/{number}/comments?per_page=100"],
                 f"the comments of PR #{number}")
    try:
        comments = merge_gate.flatten_pages(data)
    except ValueError as exc:
        raise Problem(f"the comments of PR #{number} are not a comment record") from exc
    return sorted(comments, key=lambda c: _moment(c.get("created_at"))
                  or datetime.max.replace(tzinfo=UTC))


def _post(full_name: str, number: int, body: str) -> None:
    proc = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
        ["gh", "api", "-X", "POST", f"repos/{full_name}/issues/{number}/comments",
         "--input", "-"],
        input=json.dumps({"body": body}), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise Problem(f"GitHub refused the record comment on PR #{number}: "
                      f"{_CONTROL.sub(' ', (proc.stderr or '').strip()[:300])}")


# ---------------------------------------------------------------------------
# The verdict that counts
# ---------------------------------------------------------------------------

def _counted(comments: list, merged_at: datetime, qa_login: str) -> dict | None:
    """The newest critic verdict by `qa_login` created at or before the merge."""
    before = [c for c in comments
              if (_moment(c.get("created_at")) or merged_at + timedelta(seconds=1))
              <= merged_at]
    return merge_gate.latest_verdict_record(before, qa_login, merge_gate.CRITIC_MARKER)


def _approved(verdict: dict) -> list | None:
    """The verdict's provisional records, two identical lines as one; None
    when its first line does not carry `APPROVE`."""
    body = verdict.get("body") or ""
    token = merge_gate.verdict_token(merge_gate.first_line(body), merge_gate.CRITIC_MARKER)
    if token != "APPROVE":
        return None
    seen, records = set(), []
    for found in parts_review.provisional_records(body):
        if (found.name, found.path) not in seen:
            seen.add((found.name, found.path))
            records.append(found)
    return records


def final_approval(full_name: str, pr: int, qa_login: str) -> list:
    """`[(name, path, reasoning)]` of the verdict pull request `pr` merged on,
    or `[]` — the records the scan counts, reasoning included, for the weekly
    post to read. Raises `Problem` when GitHub cannot be read."""
    pull = _read(["api", f"repos/{full_name}/pulls/{pr}"], f"PR #{pr}")
    merged = _moment(pull.get("merged_at")) if isinstance(pull, dict) else None
    if merged is None:
        return []
    verdict = _counted(pr_comments(full_name, pr), merged, qa_login)
    return (_approved(verdict) or []) if verdict is not None else []


def _records(comments: list, writer_login: str) -> dict:
    """`(name, path)` → `(status, day)` for every record line `writer_login`
    posted; the oldest stands."""
    found = {}
    for comment in comments:
        if (comment.get("user") or {}).get("login") != writer_login:
            continue
        for line in (comment.get("body") or "").splitlines():
            match = _RECORD.match(line)
            if match:
                status = PERMANENT if match[1] == PERMANENT_MARK else REJECTED
                found.setdefault((match[2], match[3]), (status, match[4]))
    return found


# ---------------------------------------------------------------------------
# The standing card's thread
# ---------------------------------------------------------------------------

def _since(voices: list | None, now: datetime) -> str:
    if voices is None:
        since = pacific_day(now - timedelta(days=LOOKBACK_DAYS))
    else:
        posts = [_moment(v.created_at) for v in voices
                 if v.kind == spoken_thread.PIPELINE
                 and merge_gate.first_line((v.body or "").lstrip()).startswith(WEEKLY_MARK)]
        posts = [moment for moment in posts if moment is not None]
        since = (pacific_day(max(posts) - timedelta(days=LOOKBACK_DAYS)) if posts
                 else PROVISIONAL_SINCE)
    return max(since, PROVISIONAL_SINCE)


def _words(voice) -> str | None:
    """What a comment says: a console answer's words with its heading line
    dropped, or the comment itself; None when the text is withheld."""
    if voice.body is None:
        return None
    if voice.kind != spoken_thread.CEO_VIA_CONSOLE:
        return voice.body.strip()
    lines = console_receipt.answer_text(voice.body).split("\n")
    if lines and console_receipt.HEADING_LINE.match(lines[0].strip()):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _entry(at, counted: bool, part, why) -> dict:
    return {"at": _iso(at) if at else None, "counted": counted, "part": part, "why": why}


def read_rejections(voices: list, slug: str, approvals: list) -> tuple:
    """`(entries, counted)`: every rejection attempt, and `(name, path)` → the
    earliest signed time of a rejection that counts."""
    entries, counted = [], {}
    by_name = {}
    for approval in approvals:
        by_name.setdefault(approval.name, []).append(approval)
    for voice in voices:
        if voice.kind == spoken_thread.REFUSED:
            entries.append(_entry(_moment(voice.created_at), False, None,
                                  "refused: its console signature did not verify"))
            continue
        words = _words(voice)
        if not words or not words.lower().startswith("reject"):
            continue
        console = voice.kind == spoken_thread.CEO_VIA_CONSOLE
        receipt = console_receipt.parse_answer(voice.body) if console else None
        signed = _moment(receipt.at) if receipt else _moment(voice.created_at)
        named = list(dict.fromkeys(f"{s}/{n}" for s, n in _NAMED.findall(words)))
        mine = [part.split("/", 1)[1] for part in named if part.split("/", 1)[0] == slug]
        if not console:
            shown = f"{slug}/{mine[0]}" if mine else (named[0] if named else None)
            entries.append(_entry(signed, False, shown,
                                  "written in Linear rather than from the console, so "
                                  "nothing signed it as the CEO's"))
            continue
        if not mine:
            entries.append(_entry(signed, False, named[0] if named else None,
                                  f"names no provisional part of {slug} as {slug}/<Name>"))
            continue
        for name in mine:
            part = f"{slug}/{name}"
            found = by_name.get(name, [])
            if not found:
                entries.append(_entry(signed, False, part,
                                      f"names no provisional part of {slug}"))
                continue
            if len(found) > 1:
                found = [a for a in found if a.path in words]
                if len(found) != 1:
                    entries.append(_entry(signed, False, part,
                                          f"ambiguous: {len(by_name[name])} provisional "
                                          f"parts of {slug} are named {name}, and the "
                                          f"words carry the path of none of them"))
                    continue
            approval = found[0]
            closes = approval.approved_at + timedelta(days=WINDOW_DAYS)
            if signed is None or signed >= closes:
                entries.append(_entry(signed, False, part,
                                      f"signed after the 14-day window, which closed "
                                      f"{dead_run.pacific(closes)}"))
                continue
            entries.append(_entry(signed, True, part, None))
            key = (approval.name, approval.path)
            counted[key] = min(counted.get(key, signed), signed)
    return entries, counted


# ---------------------------------------------------------------------------
# The closest existing part
# ---------------------------------------------------------------------------

class _Catalog:
    """The checkout's catalog, each part's props read once."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        try:
            self.parts = design_parts.catalog(self.root)
        except design_parts.Invalid as exc:
            print(f"{TAG} the design system's declaration cannot be read, so no part "
                  f"has a closest: {_clean(exc)}", file=sys.stderr)
            self.parts = []
        self._props = {}

    def _text(self, path: str) -> str | None:
        target = (self.root / path).resolve()
        if not target.is_relative_to(self.root):
            return None
        try:
            return target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def _of(self, part) -> frozenset:
        if part.ships_at not in self._props:
            self._props[part.ships_at] = parts_review._part_props(
                self._text(part.ships_at) or "", part.name)
        return self._props[part.ships_at]

    def closest(self, name: str, path: str) -> dict | None:
        """The catalog part most like `name` at `path`: the highest
        similarity, then the most shared props, then the name. None when the
        part's own file is not in the checkout or no other part ships."""
        text = self._text(path)
        if text is None:
            return None
        mine = parts_review._part_props(text, name)
        best = None
        for part in self.parts:
            if part.ships_at == path:
                continue
            score, shared = parts_review._similarity(mine, self._of(part))
            rank = (-score, -len(shared), part.name, part.ships_at)
            if best is None or rank < best[0]:
                best = (rank, part)
        if best is None:
            return None
        return {"name": _clean(best[1].name), "path": _clean(best[1].ships_at)}


# ---------------------------------------------------------------------------
# The replacement card
# ---------------------------------------------------------------------------

def identity(slug: str, approval: Approval) -> str:
    return f"{IDENTITY_MARK} {slug} | {approval.name} | {approval.path}"


def title(slug: str, approval: Approval) -> str:
    return f"{slug}: remove the rejected part {approval.name} and replace its uses"


def description(slug: str, approval: Approval, signed: datetime, card: str,
                closest: dict | None) -> str:
    near = (f" — the closest is `{closest['name']}`, at `{closest['path']}`"
            if closest else "")
    return "\n".join([
        f"The CEO rejected the provisional part `{approval.name}` of {slug} from the "
        f"console, on {card}, at {dead_run.pacific(signed)}.",
        "",
        f"It ships at `{approval.path}`. Pull request #{approval.pr} added it: "
        f"{approval.pr_url}",
        "",
        f"The fix deletes the part and moves every use of it to an existing shared "
        f"part{near}. It also removes the part's entry from the repo's own record of "
        f"approved parts where one exists (`design/LOCK.json`, or Portico's "
        f"`atoms.approved.test.ts`).",
        "",
        "Filed by the provisional parts scan (`scripts/parts_provisional.py`, DRE-8941), "
        "which records the rejection beside the part's approval on that pull request. "
        "It files under the identity below, so this part is only ever filed once.",
        "",
        identity(slug, approval),
    ]) + "\n"


def _create(named: str, body: str, slug: str, agent: str, initiative: str) -> dict:
    """One card in `LANE`, through the create seam. The guard is the labels'
    bound, read by `label_writers.py check` as well as at run time."""
    if agent.startswith(AGENT_PREFIX) and initiative.startswith(INITIATIVE_PREFIX):
        # create_card prints its own receipt; stdout is one line per part.
        with redirect_stdout(io.StringIO()):
            return linear_ops.create_card(named, body, repo_slug=slug,
                                          labels=(agent, initiative), lane=LANE)
    raise Problem(f"{agent!r} or {initiative!r} is not an `{AGENT_PREFIX}` or "
                  f"`{INITIATIVE_PREFIX}` label")


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------

def _checked(args) -> datetime:
    for flag, value, prefix in (("--agent", args.agent, AGENT_PREFIX),
                                ("--initiative", args.initiative, INITIATIVE_PREFIX)):
        if not value.startswith(prefix) or not value[len(prefix):].strip():
            raise Problem(f"{flag} {value!r} is not an `{prefix}` label")
    if args.card and not _CARD.match(args.card):
        raise Problem(f"--card {args.card!r} is not a card id (DRE-<n>) or \"\"")
    if not _FULL_NAME.match(args.full_name):
        raise Problem(f"--full-name {args.full_name!r} is not <owner>/<name>")
    try:
        linear_ops.validated_repo_slug(args.repo)
    except linear_ops.LinearError as exc:
        raise Problem(str(exc)) from exc
    if args.now is None:
        return datetime.now(UTC)
    now = _moment(args.now)
    if now is None:
        raise Problem(f"--now {args.now!r} is not an ISO 8601 time")
    return now


def _read_thread(card: str) -> list:
    try:
        voices = list(spoken_thread.read(card))
    except Exception as exc:  # said as UNKNOWN, never read as an empty thread
        raise Problem(f"the thread of {card} cannot be read: "
                      f"{type(exc).__name__}: {exc}") from exc
    if any(v.kind == spoken_thread.UNCHECKED for v in voices):
        raise Problem(f"a console answer on {card} could not be checked (the console's "
                      f"key could not be read), and it may be a rejection")
    return voices


def _approvals(args, since: str, now: datetime, log) -> tuple:
    """`(approvals, records)`: every part approved on a merged pull request
    since `since`, the earlier of two for one `(name, path)`, and each pull
    request's record lines."""
    found, records = [], {}
    for pull in merged_pulls(args.full_name, since):
        number = pull["number"]
        merged = _moment(pull["merged_at"])
        if merged > now:
            continue
        comments = pr_comments(args.full_name, number)
        verdict = _counted(comments, merged, args.qa_login)
        if verdict is None:
            continue
        approved = _approved(verdict)
        if approved is None:
            log(f"{TAG} {args.repo} — PR #{number} merged on a verdict that is not an "
                f"APPROVE, no part approved")
            continue
        records[number] = _records(comments, args.writer_login)
        at = _moment(verdict.get("created_at")) or merged
        url = _clean(pull.get("html_url") or f"https://github.com/{args.full_name}/pull/{number}")
        found += [Approval(_clean(p.name), _clean(p.path), number, url, at) for p in approved]
    found.sort(key=lambda a: (a.approved_at, a.pr, a.name, a.path))
    approvals, first = [], {}
    for approval in found:
        key = (approval.name, approval.path)
        if key in first:
            log(f"{TAG} {args.repo} {approval.name} — duplicate of PR #{first[key].pr}, "
                f"not counted")
            continue
        first[key] = approval
        approvals.append(approval)
    return approvals, records


def _whole_enough(card: str, voices: list, approvals: list, records: dict) -> None:
    """A thread read full may have left a rejection out; it is whole enough
    when its oldest comment is no newer than the earliest open window."""
    if len(voices) < linear_ops.COMMENT_WINDOW:
        return
    opened = [a.approved_at for a in approvals
              if (a.name, a.path) not in records.get(a.pr, {})]
    if not opened:
        return
    times = [_moment(v.created_at) for v in voices]
    oldest = min((t for t in times if t is not None), default=None)
    if oldest is None or oldest > min(opened):
        raise Problem(f"{card}'s thread came back full ({linear_ops.COMMENT_WINDOW} "
                      f"comments) and its oldest is newer than the earliest open window, "
                      f"opened {_iso(min(opened))}, so a rejection may lie beyond it")


def scan(args) -> str:
    """Runs one scan, writes `--json-out`, and returns the summary line;
    `Problem` when it cannot answer."""
    now = _checked(args)
    slug, card = args.repo, args.card or None

    def log(line):
        print(line, file=sys.stderr)

    voices = _read_thread(card) if card else None
    since = _since(voices, now)
    approvals, records = _approvals(args, since, now, log)
    entries, counted = [], {}
    if card:
        _whole_enough(card, voices, approvals, records)
        entries, counted = read_rejections(voices, slug, approvals)
        tally = sum(1 for e in entries if e["counted"])
        log(f"{TAG} {slug} — rejections read on {card}: {tally} counted, "
            f"{len(entries) - tally} not counted")
    else:
        log(f"{TAG} {slug} — no card set, so no rejection can be read; nothing is made "
            f"permanent")

    catalog = _Catalog(Path(args.repo_root))
    window = timedelta(days=WINDOW_DAYS)
    plan = []
    for approval in approvals:
        key = (approval.name, approval.path)
        row = {"name": approval.name, "path": approval.path, "pr": approval.pr,
               "pr_url": approval.pr_url, "approved_at": _iso(approval.approved_at),
               "status": PROVISIONAL, "permanent_on": None, "rejected_on": None,
               "replacement_card": None, "recorded_now": False,
               "closest": catalog.closest(approval.name, approval.path)}
        recorded = records.get(approval.pr, {}).get(key)
        if recorded is not None:
            row["status"] = recorded[0]
            row["permanent_on" if recorded[0] == PERMANENT else "rejected_on"] = recorded[1]
        elif key in counted:
            row.update(status=REJECTED, rejected_on=pacific_day(counted[key]),
                       recorded_now=True)
        elif card and now - approval.approved_at >= window:
            row.update(status=PERMANENT, recorded_now=True,
                       permanent_on=pacific_day(approval.approved_at + window))
        plan.append((approval, row, recorded is not None))

    known = {}
    if any(row["status"] == REJECTED for _, row, _ in plan):
        try:
            known = parts_cleanup.filed(slug, IDENTITY_MARK)
        except Exception as exc:  # files nothing on a search that failed
            raise Problem(f"the search for a replacement card already filed failed: "
                          f"{type(exc).__name__}: {exc}") from exc

    verb = "would record" if args.dry_run else "recorded"
    for approval, row, already in plan:
        head = f"{TAG} {slug} {approval.name} —"
        if row["status"] == REJECTED:
            row["replacement_card"] = known.get(identity(slug, approval)) or None
        if already:
            print(f"{head} {row['status']}, already recorded")
            continue
        if row["status"] == PROVISIONAL:
            age = max(0, (now - approval.approved_at).days)
            print(f"{head} provisional, {age} day(s) of {WINDOW_DAYS}")
            continue
        if row["status"] == PERMANENT:
            if not args.dry_run:
                _post(args.full_name, approval.pr, f"{PERMANENT_MARK} {approval.name} at "
                                                   f"{approval.path} — {row['permanent_on']}")
            print(f"{head} permanent, {verb} on PR #{approval.pr}")
            continue
        filed = row["replacement_card"]
        if filed:
            said = f"replacement card {filed} already filed"
        elif args.dry_run:
            said = "replacement card would file"
        else:
            signed = counted[(approval.name, approval.path)]
            issue = _create(title(slug, approval),
                            description(slug, approval, signed, card, row["closest"]),
                            slug, args.agent, args.initiative)
            row["replacement_card"] = issue["identifier"]
            said = f"replacement card {issue['identifier']} filed"
        if not args.dry_run:
            _post(args.full_name, approval.pr, f"{REJECTED_MARK} {approval.name} at "
                                               f"{approval.path} — {row['rejected_on']}")
        print(f"{head} rejected, {said}")

    parts = [row for _, row, _ in plan]
    doc = {"repo": slug, "card": card, "since": since, "parts": parts,
           "rejections": entries}
    try:
        Path(args.json_out).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    except OSError as exc:
        raise Problem(f"the JSON {args.json_out} cannot be written: {exc}") from exc
    count = {status: sum(1 for row in parts if row["status"] == status)
             for status in (PROVISIONAL, PERMANENT, REJECTED)}
    return (f"{TAG} {slug} — {len(parts)} approval(s) since {since}: "
            f"{count[PROVISIONAL]} provisional, {count[PERMANENT]} permanent, "
            f"{count[REJECTED]} rejected")


def cmd_scan(args) -> int:
    try:
        summary = scan(args)
    except Exception as exc:  # the run always answers
        problem = str(exc) if isinstance(exc, Problem) else f"{type(exc).__name__}: {exc}"
        print(f"{TAG} {args.repo} — UNKNOWN — {_CONTROL.sub(' ', problem)}", file=sys.stderr)
        return 1
    print(summary, file=sys.stderr)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("scan", help="decide and record where each provisional part stands")
    command.add_argument("--repo", required=True)
    command.add_argument("--full-name", required=True)
    command.add_argument("--repo-root", required=True)
    command.add_argument("--qa-login", required=True)
    command.add_argument("--writer-login", required=True)
    command.add_argument("--card", required=True)
    command.add_argument("--agent", required=True)
    command.add_argument("--initiative", required=True)
    command.add_argument("--json-out", required=True)
    command.add_argument("--now", default=None)
    command.add_argument("--dry-run", action="store_true")
    command.set_defaults(func=cmd_scan)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
