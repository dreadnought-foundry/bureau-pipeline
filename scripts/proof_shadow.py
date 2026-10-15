#!/usr/bin/env python3
"""Proof shadow: write the shadow's branch, then line its record up against Opus's (DRE-7288).

A proof run can be shadowed: beside the real proof run, a `proof-shadow`
dispatch runs the same card on another model (`PROOF_SHADOW_MODEL`), and the
`Push the shadow record` step of `proof-task.yml` keeps what it wrote on a
throwaway branch instead of opening a pull request. This module owns that
branch at both ends, so the sidecar's shape is defined once.

`push` runs in the checkout the shadow agent wrote into. It finds the `.md`
files under `docs/` or `architecture/` that are new relative to `HEAD`,
writes `proof-shadow.json` (`card`, `model`, `run_url`, `dispatched_at`,
`record_path` — the one added file, or null when there is not exactly one),
commits them on `proof-shadow/<CARD>-<run id>` off `HEAD`, and pushes that
one ref. A branch name that is not `proof-shadow/DRE-<n>-<digits>` is refused
before anything is written, so it can never push under `agent/` or to the
default branch.

`compare` reads every `proof-shadow/` branch of a repository back, filtered
by the sidecar's `card` and `dispatched_at`, and pairs each shadow with the
real record: the file at the FIRST commit on the card's
`agent/<CARD>-proof-record` pull request (open or merged) committed at or
after `dispatched_at`. Opus's run is the first Actions run URL in that
record's `## How this was recorded` section. Rows are read with
`proof_record`'s table reader, never re-derived: closing rows are skipped on
both sides, the rest pair by position, and each is classed `met`, `not-met`,
`not-observed` or `other`. It prints, in this order:

    proof-shadow: card=<DRE-n> shadow=<branch> opus=<sha|none> rows=<n> agree=<k> shadow_met_opus_not=<m> shadow_run=<url> opus_run=<url|none>
    proof-shadow: met-not card=<DRE-n> row=<i> criterion="<first 70 chars>" opus="<first 40 chars of Opus's cell>"
    proof-shadow: unpaired card=<DRE-n> shadow=<branch> — <reason>
    proof-shadow: linear card=<DRE-n> state=<state> shadow_mentions=<k> record_pr=<url|none> record_pr_merged=<yes|no>
    proof-shadow: linear-check cards=<c> requests=<r> shadow_mentions=<k> done_without_merged_record=<j>
    proof-shadow: cards=<c> rows=<n> agree=<k> (<pct>%) shadow_met_opus_not=<m> model=<id> window=<start>..<end> days=<d> bar=<met|not-met>

A card line whose two records hold different row counts ends ` rows-differ`;
every row of the longer one is compared, the extras disagreeing. Pairing
makes no Linear request. Only `--linear-check` reads Linear — each listed
card's state and comments, ten cards to one `issues(filter: …)` query — and
`requests=` is the number of `linear_ops.gql` calls `compare` made.
`<pct>` rounds down; `bar=met` is no shadow-only Met row and at least 90%.
The same report goes to `proof-shadow-report.md` and `proof-shadow-report.json`.

`compare` writes to neither GitHub nor Linear; `push` writes one git ref.

CLI:

    python3 scripts/proof_shadow.py push --card <CARD> --run-id <id> --model <id> \\
        --run-url <url> --dispatched-at <UTC> [--remote origin]
    python3 scripts/proof_shadow.py compare --repo <owner/name> [--cards DRE-1,DRE-2] \\
        [--since <time>] [--until <time>] [--linear-check] --out-dir <dir>
"""

from __future__ import annotations

import argparse
import binascii
import json
import re
import subprocess  # nosec B404 — fixed argv, `git`, no shell
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import linear_ops  # noqa: E402
import merge_gate  # noqa: E402 — `flatten_pages`, the one reading of `--slurp`
import proof_record  # noqa: E402 — the criterion-table reader and the record finder

#: The tag every compare line opens with.
TAG = "proof-shadow:"

#: The throwaway branch's prefix, and the only names `push` writes.
PREFIX = "proof-shadow/"
_BRANCH = re.compile(r"proof-shadow/DRE-\d+-\d+")
_SHADOW_CARD = re.compile(r"proof-shadow/(DRE-\d+)-\d+")
_CARD = re.compile(r"DRE-\d+")

#: The sidecar at the branch's root, and its five keys.
SIDECAR = "proof-shadow.json"
SIDECAR_KEYS = ("card", "model", "run_url", "dispatched_at", "record_path")

REPORT_MD = "proof-shadow-report.md"
REPORT_JSON = "proof-shadow-report.json"

NONE = "none"
MET, NOT_MET, NOT_OBSERVED, OTHER = "met", "not-met", "not-observed", "other"

#: The share of rows that must agree for the bar, with no shadow-only Met row.
BAR_PCT = 90

#: Cards per Linear request under `--linear-check`.
LINEAR_BATCH = 10

#: The section of a record that names the run that wrote it (`briefs/proof.md`).
HOW_HEADING = "## How this was recorded"
_RUN_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/actions/runs/\d+")

_STAMP = "%Y-%m-%dT%H:%M:%SZ"
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")

#: Each card's state and first 100 comments, by number — the `issues(filter: …)`
#: shape `switch_reason._STATES_QUERY` uses, so ten cards cost one request.
CARDS_QUERY = """query($numbers: [Float!]) {
           issues(first: 50, filter: {
             team: {key: {eq: "DRE"}},
             number: {in: $numbers}
           }) { nodes { id identifier state { name }
                        comments(first: 100) { nodes { body }
                                               pageInfo { hasNextPage endCursor } } } } }"""

#: A card's comments past its first 100, one page per request.
MORE_COMMENTS_QUERY = """query($id: String!, $after: String) {
           issue(id: $id) { comments(first: 100, after: $after) { nodes { body }
                                                                 pageInfo { hasNextPage endCursor } } } }"""


class CompareError(Exception):
    """`compare` cannot read what it needs to say anything; the message says why."""


# --------------------------------------------------------------------------- #
# times                                                                        #
# --------------------------------------------------------------------------- #

def parse_time(text) -> datetime:
    """`YYYY-MM-DDTHH:MM:SSZ`, an ISO time with an offset, or a bare
    `YYYY-MM-DD` (00:00 UTC), in UTC. Raises ValueError for anything else."""
    text = str(text or "").strip()
    if _DAY.fullmatch(text):
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError(f"{text!r} carries no UTC offset")
    return moment.astimezone(timezone.utc)


def stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(_STAMP)


def days(start: datetime, end: datetime) -> int:
    """The whole 24-hour periods from `start` to `end`, rounded down."""
    return max(0, int((end - start).total_seconds() // 86400))


# --------------------------------------------------------------------------- #
# push                                                                         #
# --------------------------------------------------------------------------- #

def branch_name(card: str, run_id: str) -> str | None:
    """`proof-shadow/<card>-<run id>`, or None when that is not a name of the
    shape `proof-shadow/DRE-<n>-<digits>`."""
    name = f"{PREFIX}{card}-{run_id}"
    return name if _BRANCH.fullmatch(name) else None


def _git(cwd, *args) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True,  # nosec B603 B607
                          text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"`git {' '.join(args)}` failed: "
                           f"{(done.stderr or done.stdout).strip()}")
    return done.stdout


def added_records(cwd) -> list:
    """The `.md` files under `proof_record.RECORD_DIRS` that are new relative
    to `HEAD` — untracked, or added to the index — sorted."""
    roots = [d.rstrip("/") for d in proof_record.RECORD_DIRS]
    untracked = _git(cwd, "ls-files", "-z", "--others", "--exclude-standard", "--", *roots)
    staged = _git(cwd, "diff", "-z", "--name-only", "--diff-filter=A", "HEAD", "--", *roots)
    paths = {p for p in (untracked + staged).split("\0") if p}
    return sorted(p for p in paths
                  if p.startswith(proof_record.RECORD_DIRS) and p.endswith(".md"))


def sidecar(card: str, model: str, run_url: str, dispatched_at: str, records) -> dict:
    return {"card": card, "model": model, "run_url": run_url,
            "dispatched_at": dispatched_at,
            "record_path": records[0] if len(records) == 1 else None}


def push(card: str, run_id: str, *, model: str, run_url: str, dispatched_at: str,
         remote: str = "origin", cwd=".") -> int:
    """Commit the shadow's new records and the sidecar on its branch off
    `HEAD`, and push that one ref. 0 on a push; 1, having pushed nothing,
    on a refused name or a git failure."""
    branch = branch_name(card, run_id)
    if branch is None:
        print(f"::error::proof shadow push: {PREFIX}{card}-{run_id} is not a "
              f"{PREFIX}DRE-<n>-<digits> branch — nothing is pushed")
        return 1
    try:
        records = added_records(cwd)
        doc = sidecar(card, model, run_url, dispatched_at, records)
        _git(cwd, "checkout", "-q", "-b", branch)
        (Path(cwd) / SIDECAR).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        paths = [*records, SIDECAR]
        _git(cwd, "add", "--", *paths)
        identity = []
        if not _git_config(cwd, "user.email"):
            identity = ["-c", "user.name=proof-shadow",
                        "-c", "user.email=proof-shadow@users.noreply.github.com"]
        _git(cwd, *identity, "commit", "-q", "-m",
             f"proof shadow: {card} on {model}\n\nRun: {run_url}", "--", *paths)
        _git(cwd, "push", "-q", remote, f"refs/heads/{branch}:refs/heads/{branch}")
    except (OSError, RuntimeError) as exc:
        print(f"::error::proof shadow push: {exc}")
        return 1
    print(f"proof shadow push: {branch} — record_path={doc['record_path'] or 'null'}")
    return 0


def _git_config(cwd, key: str) -> str:
    done = subprocess.run(["git", "config", key], cwd=cwd, capture_output=True,  # nosec B603 B607
                          text=True, check=False)
    return done.stdout.strip()


# --------------------------------------------------------------------------- #
# reading GitHub                                                               #
# --------------------------------------------------------------------------- #

def _pages(gh, path: str) -> list:
    """Every item of a paginated list endpoint, through `gh api --paginate --slurp`."""
    return merge_gate.flatten_pages(json.loads(gh(["api", path, "--paginate", "--slurp"]) or "[]"))


def shadow_branches(repo: str, gh) -> list:
    refs = _pages(gh, f"repos/{repo}/git/matching-refs/heads/{PREFIX}")
    names = [(r.get("ref") or "").removeprefix("refs/heads/") for r in refs if isinstance(r, dict)]
    return sorted(n for n in names if n.startswith(PREFIX))


def read_sidecar(repo: str, branch: str, gh) -> tuple:
    """`(sidecar, None)`, or `(None, why)` when it is missing or not the shape."""
    try:
        doc = json.loads(proof_record.record_text(repo, SIDECAR, quote(branch, safe=""), gh))
    except (RuntimeError, ValueError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        return None, f"no readable {SIDECAR}: {proof_record._one_line(exc, 160)}"
    if not isinstance(doc, dict) or not _CARD.fullmatch(str(doc.get("card") or "")):
        return None, f"{SIDECAR} names no card"
    return doc, None


class Pulls:
    """Each card's `agent/<CARD>-proof-record` pull requests, read once."""

    def __init__(self, repo: str, gh):
        self.repo, self.gh = repo, gh
        self._read = {}

    def of(self, card: str) -> list:
        """The open or merged ones, oldest first."""
        if card not in self._read:
            owner = self.repo.split("/", 1)[0]
            head = f"{owner}:agent/{card}-proof-record"
            pulls = _pages(self.gh, f"repos/{self.repo}/pulls?state=all&head={quote(head)}"
                                    "&per_page=100")
            self._read[card] = sorted(
                (p for p in pulls if isinstance(p, dict)
                 and (p.get("state") == "open" or p.get("merged_at"))),
                key=lambda p: p.get("number") or 0)
        return self._read[card]

    def record_pr(self, card: str):
        """`(url, merged)` of the card's record pull request — a merged one
        before an open one — or `(None, False)`."""
        pulls = self.of(card)
        merged = [p for p in pulls if p.get("merged_at")]
        chosen = (merged or pulls or [None])[-1]
        if chosen is None:
            return None, False
        return chosen.get("html_url"), bool(chosen.get("merged_at"))


def _committed(commit: dict):
    inner = commit.get("commit") or {}
    try:
        return parse_time(((inner.get("committer") or {}).get("date")))
    except ValueError:
        return None


def opus_pair(repo: str, card: str, after: datetime, pulls: Pulls, gh) -> tuple:
    """`(sha, path, text, None)` for the first record-branch commit at or
    after `after`, or `(None, None, None, why)`."""
    if not pulls.of(card):
        return None, None, None, f"no open or merged agent/{card}-proof-record pull request"
    found = []
    for pull in pulls.of(card):
        for commit in _pages(gh, f"repos/{repo}/pulls/{pull['number']}/commits?per_page=100"):
            at = _committed(commit) if isinstance(commit, dict) else None
            if at is not None and at >= after and commit.get("sha"):
                found.append((at, len(found), commit["sha"], pull))
    if not found:
        return None, None, None, (f"no commit on agent/{card}-proof-record at or after "
                                  f"{stamp(after)}")
    _, _, sha, pull = min(found)
    files = [{"path": f.get("filename"), "changeType": str(f.get("status") or "").upper()}
             for f in _pages(gh, f"repos/{repo}/pulls/{pull['number']}/files?per_page=100")
             if isinstance(f, dict)]
    read = proof_record.fetch(repo, files, sha, gh)
    if read.text is None:
        return None, None, None, f"the Opus record at {sha}: {read.detail}"
    return sha, read.path, read.text, None


def opus_run(text: str) -> str | None:
    """The first Actions run URL in the record's `## How this was recorded`."""
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith(HOW_HEADING.lower()):
            section = []
            for after in lines[i + 1:]:
                if re.match(r"#{1,2}\s", after):
                    break
                section.append(after)
            found = _RUN_URL.search("\n".join(section))
            return found.group(0) if found else None
    return None


# --------------------------------------------------------------------------- #
# rows                                                                         #
# --------------------------------------------------------------------------- #

def row_class(result: str) -> str:
    if proof_record.row_met(result):
        return MET
    text = proof_record._plain(result).lower()
    if text.startswith("not met."):
        return NOT_MET
    if text.startswith("not observed."):
        return NOT_OBSERVED
    return OTHER


def judged_rows(text: str) -> list:
    """The record's criterion rows but its closing ones, in order."""
    return [(c, r) for c, r in proof_record.criterion_rows(text) or []
            if not proof_record.is_closing_row(c)]


def _cut(text: str, n: int) -> str:
    return " ".join(str(text or "").split()).replace('"', "'")[:n]


def line_up(shadow_text: str, opus_text: str) -> dict:
    """The two records' rows, paired by position."""
    shadow, opus = judged_rows(shadow_text), judged_rows(opus_text)
    agree, met_not = 0, []
    for i in range(min(len(shadow), len(opus))):
        mine, theirs = row_class(shadow[i][1]), row_class(opus[i][1])
        if mine == theirs:
            agree += 1
        elif mine == MET:
            met_not.append({"row": i + 1, "criterion": _cut(shadow[i][0], 70),
                            "opus": _cut(opus[i][1], 40)})
    return {"rows": max(len(shadow), len(opus)), "agree": agree, "met_not": met_not,
            "rows_differ": len(shadow) != len(opus)}


def percent(agree: int, rows: int) -> int:
    return agree * 100 // rows if rows else 0


def bar(agree: int, rows: int, met_not: int) -> str:
    return "met" if met_not == 0 and percent(agree, rows) >= BAR_PCT else "not-met"


# --------------------------------------------------------------------------- #
# Linear                                                                       #
# --------------------------------------------------------------------------- #

class _Counted:
    """`linear_ops.gql`, counted: every request `compare` sends goes here."""

    def __init__(self):
        self.requests = 0

    def __call__(self, query: str, variables: dict) -> dict:
        self.requests += 1
        return linear_ops.gql(query, variables)


def read_cards(cards, gql) -> dict:
    """`{card: (state, [comment bodies])}`, ten cards to a request, a card's
    comments paged further only past its first 100."""
    found = {}
    cards = sorted(set(cards), key=lambda c: int(c.split("-")[1]))
    for at in range(0, len(cards), LINEAR_BATCH):
        batch = cards[at:at + LINEAR_BATCH]
        data = gql(CARDS_QUERY, {"numbers": [int(c.split("-")[1]) for c in batch]})
        for node in (data.get("issues") or {}).get("nodes") or []:
            ident = node.get("identifier")
            if ident not in batch:
                continue
            conn = node.get("comments") or {}
            bodies = [c.get("body") or "" for c in conn.get("nodes") or []]
            seen = set()
            while (conn.get("pageInfo") or {}).get("hasNextPage"):
                after = (conn.get("pageInfo") or {}).get("endCursor")
                if not after or after in seen:
                    break
                seen.add(after)
                conn = ((gql(MORE_COMMENTS_QUERY, {"id": node.get("id"), "after": after})
                         .get("issue") or {}).get("comments") or {})
                bodies += [c.get("body") or "" for c in conn.get("nodes") or []]
            found[ident] = ((node.get("state") or {}).get("name"), bodies)
    return found


def linear_check(cards, run_urls: dict, pulls: Pulls) -> dict:
    counted = _Counted()
    read = read_cards(cards, counted)
    rows = []
    for card in sorted(set(cards), key=lambda c: int(c.split("-")[1])):
        state, bodies = read.get(card, (None, []))
        marks = [u for u in run_urls.get(card, ()) if u] + [PREFIX]
        mentions = sum(1 for b in bodies if any(m in b for m in marks))
        url, merged = pulls.record_pr(card)
        rows.append({"card": card, "state": state or "unread", "shadow_mentions": mentions,
                     "record_pr": url or NONE, "record_pr_merged": merged})
    done_without = sum(1 for r in rows if r["state"] == "Done" and not r["record_pr_merged"])
    return {"cards": rows, "requests": counted.requests,
            "shadow_mentions": sum(r["shadow_mentions"] for r in rows),
            "done_without_merged_record": done_without}


# --------------------------------------------------------------------------- #
# compare                                                                      #
# --------------------------------------------------------------------------- #

def card_line(s: dict) -> str:
    line = (f"{TAG} card={s['card']} shadow={s['shadow']} opus={s['opus'] or NONE} "
            f"rows={s['rows']} agree={s['agree']} shadow_met_opus_not={len(s['met_not'])} "
            f"shadow_run={s['shadow_run'] or NONE} opus_run={s['opus_run'] or NONE}")
    return line + (" rows-differ" if s["rows_differ"] else "")


def compare(repo: str, *, cards=None, since=None, until=None, check_linear=False,
            gh=None) -> dict:
    gh = gh or proof_record._gh
    pulls = Pulls(repo, gh)
    wanted = set(cards or ())
    try:
        branches = shadow_branches(repo, gh)
    except (RuntimeError, ValueError, TypeError) as exc:
        raise CompareError(f"the {PREFIX} branches of {repo} could not be listed: {exc}") from exc

    listed = []  # (dispatched or None, branch, sidecar or None, why)
    for branch in branches:
        doc, why = read_sidecar(repo, branch, gh)
        named = _SHADOW_CARD.fullmatch(branch)
        card = doc["card"] if doc is not None else named.group(1) if named else None
        if card is None or (wanted and card not in wanted):
            continue
        at = None
        if doc is not None:
            try:
                at = parse_time(doc.get("dispatched_at"))
            except ValueError:
                why = f"dispatched_at {doc.get('dispatched_at')!r} is not a time"
        if at is not None and ((since and at < since) or (until and at > until)):
            continue
        listed.append((at, branch, card, doc, why))
    listed.sort(key=lambda s: (s[0] or datetime.max.replace(tzinfo=timezone.utc), s[1]))

    shadows, unpaired, run_urls = [], [], {}
    for at, branch, card, doc, why in listed:
        if doc is not None:
            run_urls.setdefault(card, []).append(doc.get("run_url"))
        if why is None and not doc.get("record_path"):
            why = "record_path is null — the shadow added no single record"
        shadow_text = None
        if why is None:
            try:
                shadow_text = proof_record.record_text(
                    repo, doc["record_path"], quote(branch, safe=""), gh)
            except (RuntimeError, ValueError, TypeError, binascii.Error,
                    UnicodeDecodeError) as exc:
                why = f"{doc['record_path']} could not be read: {proof_record._one_line(exc, 160)}"
        if why is None:
            try:
                sha, _, opus_text, why = opus_pair(repo, card, at, pulls, gh)
            except (RuntimeError, ValueError, TypeError) as exc:
                why = f"the Opus record could not be read: {proof_record._one_line(exc, 160)}"
        if why is not None:
            unpaired.append({"card": card, "shadow": branch, "reason": why})
            continue
        lined = line_up(shadow_text, opus_text)
        shadows.append({"card": card, "shadow": branch, "opus": sha,
                        "dispatched_at": stamp(at), "model": doc.get("model"),
                        "shadow_run": doc.get("run_url"), "opus_run": opus_run(opus_text),
                        **lined})

    rows = sum(s["rows"] for s in shadows)
    agree = sum(s["agree"] for s in shadows)
    met_not = sum(len(s["met_not"]) for s in shadows)
    models = sorted({str(d.get("model")) for _, _, _, d, _ in listed if d and d.get("model")})
    if since and until:
        start, end = since, until
    else:
        times = [at for at, *_ in listed if at is not None]
        start, end = (min(times), max(times)) if times else (None, None)
    summary = {
        "cards": len({s["card"] for s in shadows}), "rows": rows, "agree": agree,
        "pct": percent(agree, rows), "shadow_met_opus_not": met_not,
        "model": ",".join(models) or NONE,
        "window": f"{stamp(start) if start else NONE}..{stamp(end) if end else NONE}",
        "days": days(start, end) if start and end else 0,
        "bar": bar(agree, rows, met_not),
    }

    lines = [card_line(s) for s in shadows]
    for s in shadows:
        lines += [f'{TAG} met-not card={s["card"]} row={m["row"]} criterion="{m["criterion"]}" '
                  f'opus="{m["opus"]}"' for m in s["met_not"]]
    lines += [f"{TAG} unpaired card={u['card']} shadow={u['shadow']} — {u['reason']}"
              for u in unpaired]
    linear = None
    if check_linear:
        linear = linear_check([c for _, _, c, _, _ in listed], run_urls, pulls)
        lines += [f"{TAG} linear card={r['card']} state={r['state']} "
                  f"shadow_mentions={r['shadow_mentions']} record_pr={r['record_pr']} "
                  f"record_pr_merged={'yes' if r['record_pr_merged'] else 'no'}"
                  for r in linear["cards"]]
        lines.append(f"{TAG} linear-check cards={len(linear['cards'])} "
                     f"requests={linear['requests']} shadow_mentions={linear['shadow_mentions']} "
                     f"done_without_merged_record={linear['done_without_merged_record']}")
    lines.append(f"{TAG} cards={summary['cards']} rows={rows} agree={agree} ({summary['pct']}%) "
                 f"shadow_met_opus_not={met_not} model={summary['model']} "
                 f"window={summary['window']} days={summary['days']} bar={summary['bar']}")
    return {"repo": repo, "summary": summary, "shadows": shadows, "unpaired": unpaired,
            "linear_check": linear, "lines": lines}


def render_md(report: dict) -> str:
    s = report["summary"]
    out = [f"# Proof shadow — {s['model']} against Opus", "", report["lines"][-1], "",
           f"Window {s['window']} ({s['days']} days). `bar=met` needs no shadow-only "
           f"Met row and at least {BAR_PCT}% of rows agreeing.", "",
           "| card | shadow run | Opus run | rows | agree | shadow-only Met rows |",
           "| -- | -- | -- | -- | -- | -- |"]
    for row in report["shadows"]:
        differ = " (rows differ)" if row["rows_differ"] else ""
        out.append(f"| {row['card']} | {row['shadow_run'] or NONE} | {row['opus_run'] or NONE} "
                   f"| {row['rows']}{differ} | {row['agree']} | {len(row['met_not'])} |")
    out += ["", "## Shadow-only Met rows", ""]
    met_not = [(row["card"], m) for row in report["shadows"] for m in row["met_not"]]
    if not met_not:
        out.append("None.")
    for card, m in met_not:
        out.append(f"- {card} row {m['row']}: “{m['criterion']}” — Opus: “{m['opus']}”")
    out += ["", "## Unpaired shadows", ""]
    if not report["unpaired"]:
        out.append("None.")
    for u in report["unpaired"]:
        out.append(f"- {u['card']} `{u['shadow']}` — {u['reason']}")
    if report.get("linear_check"):
        linear = report["linear_check"]
        out += ["", "## Linear", "", next(line for line in report["lines"]
                                           if line.startswith(f"{TAG} linear-check")), "",
                "| card | state | shadow mentions | record PR | merged |",
                "| -- | -- | -- | -- | -- |"]
        for r in linear["cards"]:
            out.append(f"| {r['card']} | {r['state']} | {r['shadow_mentions']} | "
                       f"{r['record_pr']} | {'yes' if r['record_pr_merged'] else 'no'} |")
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _cards(text: str) -> list:
    out = [c.strip() for c in (text or "").split(",") if c.strip()]
    bad = [c for c in out if not _CARD.fullmatch(c)]
    if bad:
        raise ValueError(f"`--cards` takes DRE-<n> ids, comma-separated — got {', '.join(bad)}")
    return out


def _compare_main(args, gh=None) -> int:
    try:
        cards = _cards(args.cards)
        since = parse_time(args.since) if args.since else None
        until = parse_time(args.until) if args.until else None
    except ValueError as exc:
        print(f"::error::proof shadow compare: {exc}")
        return 2
    try:
        report = compare(args.repo, cards=cards, since=since, until=until,
                         check_linear=args.linear_check, gh=gh)
    except CompareError as exc:
        print(f"::error::proof shadow compare: {exc}")
        return 1
    except linear_ops.LinearError as exc:
        print(f"::error::proof shadow compare: the Linear check failed: {exc}")
        return 1
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / REPORT_JSON).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
    (out / REPORT_MD).write_text(render_md(report), encoding="utf-8")
    for line in report["lines"]:
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    push_ = sub.add_parser("push", help="write the shadow's branch and push it")
    push_.add_argument("--card", required=True)
    push_.add_argument("--run-id", required=True)
    push_.add_argument("--model", required=True)
    push_.add_argument("--run-url", required=True)
    push_.add_argument("--dispatched-at", required=True)
    push_.add_argument("--remote", default="origin")

    cmp_ = sub.add_parser("compare", help="line each shadow record up against Opus's")
    cmp_.add_argument("--repo", required=True)
    cmp_.add_argument("--cards", default="")
    cmp_.add_argument("--since", default="")
    cmp_.add_argument("--until", default="")
    cmp_.add_argument("--linear-check", action="store_true")
    cmp_.add_argument("--out-dir", required=True)
    return parser


def main(argv=None, *, gh=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "push":
        return push(args.card, args.run_id, model=args.model, run_url=args.run_url,
                    dispatched_at=args.dispatched_at, remote=args.remote)
    return _compare_main(args, gh=gh)


if __name__ == "__main__":
    sys.exit(main())
