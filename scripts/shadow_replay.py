#!/usr/bin/env python3
"""Shadow Replay: pick past reviews, then say how often the replays agreed (DRE-7197).

The critic (DRE-7193, `qa-review.yml`) and the verifier (DRE-7194,
`verify.yml`) each replay ONE past pull request in shadow mode: at the head
its recorded verdict was bound to, on a model the caller names, writing the
verdict to the `shadow-verdict-<role>-<pr>` artifact and posting nothing.
`.github/workflows/shadow-replay.yml` runs a batch of them, and this module is
its two ends. The noise-floor reading is taken with it, and the model-switch
epics (DRE-7185, DRE-7187, DRE-7188, DRE-7189) read their bars with it.

`pick` chooses the batch, by default one of two ways:

* `--recorded-model` (critic only): the critic's copies on Linear cards
  labeled `repo:<this repo>` that carry `🧠 model: <recorded_model>` — the
  line `post_verdict.sh` adds to the card's copy. The verdict and its sha
  come off the first line (`merge_gate.verdict_token`, `verdict_sha`); a
  first line that yields no token or no sha — a `could not run` notice, an
  evidence hold — is not a recorded review and never fills a place. The pull
  request is found through `gh api repos/<repo>/commits/<sha>/pulls`, a sha
  the repository no longer has is dropped, one review is kept per pull
  request (its newest), and the newest `--count` are returned. Fewer than
  `--count` fails, naming how many were found: a short batch is never
  replayed silently.
* `--prs`: the listed pull requests, each at the newest qa-bot comment
  carrying the reviewer's marker whose first line yields a token AND a sha —
  `merge_gate.latest_verdict_comment` alone returns a newer `could not run`
  notice. A pull request with none is replayed at its final head with
  `recorded` set to `none`.

Each critic record also names `rules_ref` (DRE-7280): the newest commit on
bureau-pipeline `main` at or before its recorded verdict was written — the
Linear copy's `createdAt`, or the pull request comment's `created_at` — read
with one `gh api repos/dreadnought-foundry/bureau-pipeline/commits?sha=main
&until=<time>&per_page=1` call. The critic's replay judges by that commit's
rules (DRE-7279), so a rule written after the review is not counted against
the model. A record whose commit cannot be read — the read fails, no commit
is that old, or it has no verdict to date — is still replayed, with
`rules_ref` empty and so by today's rules, and the pick log names it. "Main
at that time" is exact for a repo that rides `@main` (bureau-pipeline and
agent-bureau); a repo on another channel would need that channel's position.
A verifier record's `rules_ref` is always empty: `verify.yml` takes none.

`--unknown-builder` (DRE-7304) picks the reviews the selector ran while this
build's model could not be read — the clause is read off
`model_fallback.selection_note`, never retyped — on `--recorded-model`, which
then defaults to `UNKNOWN_BUILDER_MODEL`. A clause review on another model
never counts, and the pick log says how many it skipped.

* critic: the same card copies as `--recorded-model`, kept only when their
  `🧠 model:` line names the model and carries the clause. A pull request
  rejected, fixed and approved carries copies on several heads, so it is
  picked at its newest *acted-on* rejection, at that rejection's own head,
  whenever it has one — a `REQUEST_CHANGES` followed, before the next critic
  verdict, by the worker bot's `🔧 Fix attempt … pushed — CI and critic
  review re-running` (a conflict round's push does not count). Its other
  pull requests contribute their newest review. One record per pull request,
  because a replay's artifact and `read_replays` are keyed by pull request
  alone. Acted-on records come first, newest first, then the rest, and each
  record carries `acted_on`.
* verifier: the runs of the stub `--verify-workflow` names, newest first,
  kept when the job log's `Select model` note says `<model> chosen for
  verifier` with the clause, each paired with the `🧪 QA Verifier — VERDICT:`
  comment posted on its pull request while it ran. Every run passed over —
  an unreadable log, no verdict comment, a known builder, another model, a pull
  request already picked — is named. One record per pull request, the newest.
  The logs are read on `VERIFIER_LOG_READERS` (8) readers at once, never more
  outstanding, and each run is still applied in listing order whatever order
  its log arrives in, so the pick, its lines and its counts are the same as
  reading them one at a time — a run naming no pull request, or on one already
  picked, is skipped without a read. A few logs listed past the run the pick
  stops on may be read too, and are discarded unapplied. Every 50 runs read,
  and once more when it stops, it prints `pick: read <R> run(s), picked <K> of
  <count>`. It stops at its `VERIFIER_PICK_BUDGET_SECONDS` (600-second) time
  budget, inside the pick job's 15 minutes, and a short batch then says it
  stopped there and how many runs it read (DRE-7507).

`--from-pick <file> --acted-on-only` re-emits, unchanged, the acted-on
records of an earlier pick's records file and reads nothing else, so the
Opus control replays the very records the earlier run replayed.

It writes the matrix, `[{pr, sha, recorded, rules_ref}]`, as the `matrix`
step output, and the full records (with each recorded first line and the
verdict's time) to `--out`.

`compare` reads the downloaded artifacts against the pick and prints, in
this order, one line per pull request, one summary, and the bar the role's
model switch waits on (DRE-7304):

    shadow-replay: pr=<n> sha=<40hex> recorded=<token|none> replayed=<token|none> match=<yes|no|n/a>
    shadow-replay: matched <k> of <n> (<pct>%) reviewer=<role> model=<id> recorded_model=<id|none>
    shadow-replay: acted-on rejections <k> of <n> rejected again (<pct>%) reviewer=critic model=<id>
    shadow-replay: recorded FAIL replayed PASS <k> of <n> reviewer=verifier model=<id>

The last two are one per role; the verifier's `<n>` counts the replayed
records whose recorded verdict is FAIL. A replay with no verdict is neither
rejected again nor PASS, and the report names it apart.

A replay with no artifact, or with `"verdict": null`, is a mismatch of kind
`no verdict`, named apart from a `disagreement`. A replay whose recorded
verdict is `none` has nothing to match: `match=n/a`, outside both counts.
`<pct>` is rounded down. The report goes to `shadow-replay-report.md` and
`shadow-replay-report.json`; it names each replay's `rules_ref`, and counts
the replays that ran with none. The lines above never carry `rules_ref`:
`config/proof-reach.json` declares them, and DRE-7198's record reads them.

Nothing here writes to GitHub or Linear: it reads, and prints.

CLI:

    python3 scripts/shadow_replay.py pick --reviewer critic --recorded-model <id> \\
        --count 20 --prs '' --repo <owner/name> --out pick.json --github-output "$GITHUB_OUTPUT"
    python3 scripts/shadow_replay.py pick --reviewer verifier --unknown-builder \\
        --verify-workflow <stub.yml> --count 20 --repo <owner/name> --out pick.json
    python3 scripts/shadow_replay.py pick --reviewer critic --from-pick <earlier.json> \\
        --acted-on-only --repo <owner/name> --out pick.json
    python3 scripts/shadow_replay.py compare --reviewer critic --model <id> \\
        --recorded-model <id> --pick pick.json --artifacts <dir> --out-dir <dir>
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fix_budget  # noqa: E402
import fix_concurrency  # noqa: E402
import fix_dead_run  # noqa: E402
import linear_ops  # noqa: E402
import merge_gate  # noqa: E402
import model_fallback  # noqa: E402

#: The tag every compare line opens with. `config/proof-reach.json` declares
#: the line against this constant, and that file's check holds it here.
TAG = "shadow-replay:"

REVIEWERS = ("critic", "verifier")
MARKERS = {"critic": merge_gate.CRITIC_MARKER, "verifier": merge_gate.VERIFIER_MARKER}

#: What `post_verdict.sh` appends to the card's copy of a critic verdict:
#: `🧠 model: <id> — <why>`.
MODEL_LINE = "🧠 model: "
_MODEL_LINE_RE = re.compile(r"^" + re.escape(MODEL_LINE) + r"(\S+)", re.M)

#: The recorded verdict of a pull request with none to replay against.
NONE = "none"

#: Where the rules a critic judges by live, whichever repo is replayed.
RULES_REPO = "dreadnought-foundry/bureau-pipeline"
RULES_BRANCH = "main"

DISAGREEMENT = "disagreement"
NO_VERDICT = "no verdict"

REPORT_MD = "shadow-replay-report.md"
REPORT_JSON = "shadow-replay-report.json"

CRITIC_COPIES_QUERY = """query($label: String!, $model: String!, $after: String) {
  comments(first: 100, after: $after, filter: {
    body: {contains: $model},
    issue: {labels: {name: {eq: $label}}}
  }) { nodes { createdAt body issue { identifier labels { nodes { name } } } }
       pageInfo { hasNextPage endCursor } } }"""

#: The model whose unknown-builder reviews the switch epic reads (DRE-7304):
#: under `--unknown-builder`, an empty `--recorded-model` means this one.
UNKNOWN_BUILDER_MODEL = "claude-opus-5-5"

REQUEST_CHANGES = "REQUEST_CHANGES"
FAIL = "FAIL"
PASS = "PASS"

#: The worker bot's fix-loop push (`🔧 Fix attempt N pushed — …`), which is
#: what makes a rejection acted on. A conflict round's push carries the same
#: marker and opens `🔀 Conflict resolution`, so it does not count.
FIX_ATTEMPT = fix_budget.BUDGETS["fix"][0]
PUSH_MARKER = fix_dead_run.PUSH_MARKER

#: After this many runs in a row whose log cannot be read, the older runs'
#: logs have expired too (a run's log expires by age, and the runs are read
#: newest first): the verifier's listing stops there, and says so.
UNREADABLE_LOGS_IN_A_ROW = 20

#: The verifier pick's own time limit, in seconds from its start on `clock`
#: (DRE-7507): past it the pick applies no further run, and a short batch
#: says it stopped there and how many runs it read. The pick job's
#: `timeout-minutes` is 15; the 5 left over are its checkout, Python setup
#: and upload, so the job ends with a number instead of being canceled.
VERIFIER_PICK_BUDGET_SECONDS = 600

#: How many verify run logs the pick reads at once (DRE-7507). At most this
#: many reads are outstanding — dispatched and not yet applied, the run
#: being applied included — so one reader is the old one-at-a-time reading.
VERIFIER_LOG_READERS = 8

#: Every this many runs applied, the verifier pick says how far it has read.
VERIFIER_PROGRESS_EVERY = 50

#: The clock the verifier pick's budget is read on; the tests replace it.
clock = time.monotonic

# Where a printed line starts in `gh run view --log`: behind the
# `job\tstep\ttimestamp ` prefix (check_linear_budget._LINE_START, DRE-5201),
# so the echoed script that merely names the note is never read as it.
_LOG_LINE_START = (
    r"^(?:[^\t\n]*\t[^\t\n]*\t)?﻿?"
    r"(?:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z )?"
)
_VERIFIER_NOTE_RE = re.compile(
    _LOG_LINE_START + r"(?:DEGRADED )?model-policy: (\S+) chosen for verifier\b(.*)$", re.M)
_MODEL_LINES_RE = re.compile(r"^" + re.escape(MODEL_LINE) + r"(\S+)(.*)$", re.M)


def unknown_builder_clause() -> str:
    """The words the selector's note says when this build's model could not
    be read, taken off `model_fallback.selection_note` itself and never
    retyped: where the unknown builder's separation sentence parts from a
    known builder's, back to the sentence's start, up to its first comma."""
    decision = {"model": "m", "role": "r", "separated": ["b"]}
    unknown = model_fallback.selection_note({**decision, "built_on_unknown": True})
    known = model_fallback.selection_note({**decision, "built_on_unknown": False})
    start = os.path.commonprefix([unknown, known]).rfind(": ") + 2
    return unknown[start:].split(",", 1)[0].strip()


#: The unknown-builder clause, as the selector writes it.
UNKNOWN_BUILDER_CLAUSE = unknown_builder_clause()


class PickError(Exception):
    """The batch cannot be picked as asked; the message says why."""


# --------------------------------------------------------------------------- #
# reading a recorded verdict                                                   #
# --------------------------------------------------------------------------- #

def recorded_verdict(body: str | None, marker: str):
    """`(token, sha, first_line)` when `body`'s first line is a verdict under
    `marker` bound to a sha; None for anything else — a notice that opens
    with the marker and carries no `VERDICT: <X> @<sha>` included."""
    if not merge_gate.opens_with_marker(body, marker):
        return None
    line = merge_gate.first_line(body)
    token, sha = merge_gate.verdict_token(line, marker), merge_gate.verdict_sha(line)
    if not token or not sha:
        return None
    return token, sha, line


def recorded_on(body: str | None, model: str) -> bool:
    """The comment carries the model line naming exactly `model`."""
    return model in _MODEL_LINE_RE.findall(body or "")


def _labeled(node: dict, label: str) -> bool:
    labels = (((node.get("issue") or {}).get("labels") or {}).get("nodes")) or []
    return any((lab or {}).get("name") == label for lab in labels)


def _moment(iso: str | None) -> datetime:
    try:
        found = datetime.fromisoformat((iso or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# GitHub, through `gh`                                                         #
# --------------------------------------------------------------------------- #

def _gh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=False)


def _gh_json(*args: str):
    done = _gh(*args)
    if done.returncode != 0:
        raise PickError(f"`gh {' '.join(args)}` failed: {done.stderr.strip()}")
    try:
        return json.loads(done.stdout)
    except ValueError as exc:
        raise PickError(f"`gh {' '.join(args)}` did not answer JSON: {exc}") from exc


def commit_exists(repo: str, sha: str) -> bool:
    return _gh("api", f"repos/{repo}/commits/{sha}").returncode == 0


def pull_request_of(repo: str, sha: str) -> int | None:
    pulls = _gh_json("api", f"repos/{repo}/commits/{sha}/pulls")
    for pull in pulls if isinstance(pulls, list) else ():
        if isinstance(pull, dict) and isinstance(pull.get("number"), int):
            return pull["number"]
    return None


def rules_ref_at(verdict_at: str) -> tuple:
    """`(sha, why)`: the newest commit on bureau-pipeline `main` at or before
    `verdict_at`, and `""` with the reason when it cannot be read."""
    moment = _moment(verdict_at)
    if moment == datetime.min.replace(tzinfo=timezone.utc):
        return "", f"the verdict's time {verdict_at!r} does not read"
    until = moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    done = _gh("api", f"repos/{RULES_REPO}/commits?sha={RULES_BRANCH}&until={until}&per_page=1")
    if done.returncode != 0:
        return "", f"the commits read failed: {done.stderr.strip() or done.returncode}"
    try:
        commits = json.loads(done.stdout)
    except ValueError:
        return "", "the commits read did not answer JSON"
    if not isinstance(commits, list) or not commits:
        return "", f"{RULES_REPO} {RULES_BRANCH} has no commit at or before {until}"
    sha = commits[0].get("sha") if isinstance(commits[0], dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        return "", "the commits read named no whole commit id"
    return sha, ""


def with_rules_refs(records) -> list:
    """Each record with its `rules_ref`, one commits read per record; a
    record whose commit cannot be read keeps its place and is named."""
    for r in records:
        if r["recorded"] == NONE or not r.get("verdict_at"):
            sha, why = "", "no recorded verdict to date"
        else:
            sha, why = rules_ref_at(r["verdict_at"])
        r["rules_ref"] = sha
        if not sha:
            print(f"pick: pr={r['pr']} rules_ref unreadable ({why}) — replayed by "
                  "today's rules", file=sys.stderr)
    return records


# --------------------------------------------------------------------------- #
# pick                                                                         #
# --------------------------------------------------------------------------- #

def _record(pr: int, sha: str, recorded: str, line: str, verdict_at: str = "") -> dict:
    return {"pr": pr, "sha": sha, "recorded": recorded, "recorded_line": line,
            "verdict_at": verdict_at, "rules_ref": ""}


def matrix(records) -> list:
    """The replay job's matrix: `{pr, sha, recorded, rules_ref}` per record."""
    return [{"pr": r["pr"], "sha": r["sha"], "recorded": r["recorded"],
             "rules_ref": r.get("rules_ref") or ""} for r in records]


def linear_critic_comments(recorded_model: str, repo_label: str) -> list:
    """Every Linear comment on a `repo_label` card carrying the model line.
    `pick_recorded_model` filters them again, so the query only narrows."""
    return linear_ops.gql_paged(
        CRITIC_COPIES_QUERY,
        {"label": repo_label, "model": MODEL_LINE + recorded_model},
        connection="comments")


def pick_recorded_model(comments, *, recorded_model: str, count: int, repo: str,
                        repo_label: str | None = None) -> list:
    """The newest `count` critic reviews recorded on `recorded_model`, one per
    pull request. Raises PickError naming the number found when short."""
    label = repo_label or "repo:" + repo.rsplit("/", 1)[-1]
    marker = MARKERS["critic"]
    candidates = []
    for c in comments or ():
        if not isinstance(c, dict) or not _labeled(c, label):
            continue
        body = c.get("body")
        found = recorded_verdict(body, marker)
        if found is None or not recorded_on(body, recorded_model):
            continue
        candidates.append((_moment(c.get("createdAt")), c.get("createdAt") or "", found))
    candidates.sort(key=lambda entry: entry[0], reverse=True)

    picked, prs, gone = [], set(), set()
    for _, created, (token, sha, line) in candidates:
        if len(picked) == count:
            break
        if sha in gone:
            continue
        if not commit_exists(repo, sha):
            print(f"pick: {sha} is gone from {repo} — dropped", file=sys.stderr)
            gone.add(sha)
            continue
        pr = pull_request_of(repo, sha)
        if pr is None or pr in prs:
            continue
        prs.add(pr)
        picked.append(_record(pr, sha, token, line, created))
    if len(picked) < count:
        raise PickError(
            f"found {len(picked)} eligible critic review(s) recorded on {recorded_model} "
            f"in {repo}, and the batch asks for {count} — nothing is replayed short")
    return with_rules_refs(picked)


def _pr_comments(repo: str, pr: int) -> list:
    try:
        return merge_gate.flatten_pages(_gh_json(
            "api", f"repos/{repo}/issues/{pr}/comments", "--paginate", "--slurp"))
    except ValueError as exc:
        raise PickError(f"pull request {pr}'s comments did not read: {exc}") from exc


def _login(comment: dict) -> str:
    return ((comment.get("user") or {}).get("login")) or ""


def acted_on(comments, verdict_at: str, *, worker_login: str = fix_dead_run.WORKER_LOGIN,
             qa_login: str = fix_concurrency.QA_BOT_LOGIN) -> bool:
    """A rejection recorded at `verdict_at` was acted on: the worker bot
    posted a `🔧 Fix attempt … pushed — CI and critic review re-running`
    comment after it and before the next critic verdict. A conflict round's
    push, a push before the verdict, and anyone else's comment do not count;
    a `could not run` notice is not the next verdict."""
    start = _moment(verdict_at)
    later = [c for c in comments or () if isinstance(c, dict)
             and _moment(c.get("created_at")) > start]
    for c in sorted(later, key=lambda c: _moment(c.get("created_at"))):
        body = (c.get("body") or "").lstrip()
        if _login(c) == qa_login and recorded_verdict(body, MARKERS["critic"]):
            return False
        if (_login(c).removesuffix("[bot]") == worker_login
                and body.startswith(FIX_ATTEMPT) and PUSH_MARKER in body):
            return True
    return False


def _rejection_time(comments, token: str, sha: str, copied_at: str,
                    qa_login: str = fix_concurrency.QA_BOT_LOGIN) -> str:
    """When the rejection the card copy records was posted on the pull
    request: the newest qa-bot critic comment with the same verdict and sha
    at or before the copy, else the copy's own time."""
    copied, found = _moment(copied_at), ""
    for c in comments or ():
        if not isinstance(c, dict) or _login(c) != qa_login:
            continue
        verdict = recorded_verdict(c.get("body"), MARKERS["critic"])
        created = c.get("created_at") or ""
        if verdict and verdict[:2] == (token, sha) and _moment(created) <= copied:
            if not found or _moment(created) > _moment(found):
                found = created
    return found or copied_at


def _unknown_builder_copy(body: str | None, model: str) -> str:
    """`kept` when a `🧠 model:` line names `model` and carries the
    unknown-builder clause, `elsewhere` when only a line naming another model
    carries it, and `""` for a copy that is no unknown-builder review."""
    lines = _MODEL_LINES_RE.findall(body or "")
    if any(m == model and UNKNOWN_BUILDER_CLAUSE in rest for m, rest in lines):
        return "kept"
    if any(UNKNOWN_BUILDER_CLAUSE in rest for _, rest in lines):
        return "elsewhere"
    return ""


def pick_unknown_builder_critic(comments, *, recorded_model: str, count: int, repo: str,
                                repo_label: str | None = None,
                                qa_login: str = fix_concurrency.QA_BOT_LOGIN) -> list:
    """`count` critic reviews recorded on `recorded_model` while this build's
    model could not be read, one per pull request: the pull request's newest
    acted-on rejection, at that rejection's own head, whenever it has one,
    else its newest review. The acted-on records come first, newest first,
    then the rest, newest first. Raises PickError naming the number found
    when short."""
    label = repo_label or "repo:" + repo.rsplit("/", 1)[-1]
    copies, elsewhere = [], 0
    for c in comments or ():
        if not isinstance(c, dict) or not _labeled(c, label):
            continue
        body = c.get("body")
        found = recorded_verdict(body, MARKERS["critic"])
        kind = _unknown_builder_copy(body, recorded_model) if found else ""
        elsewhere += kind == "elsewhere"
        if kind == "kept":
            copies.append((_moment(c.get("createdAt")), c.get("createdAt") or "", found))
    print(f"pick: skipped {elsewhere} unknown-builder critic copie(s) recorded on another "
          f"model than {recorded_model}", file=sys.stderr)
    copies.sort(key=lambda entry: entry[0], reverse=True)

    by_pr, pr_of = {}, {}
    for moment, created, (token, sha, line) in copies:
        if sha not in pr_of:
            if not commit_exists(repo, sha):
                print(f"pick: {sha} is gone from {repo} — dropped", file=sys.stderr)
                pr_of[sha] = None
                continue
            pr_of[sha] = pull_request_of(repo, sha)
        if pr_of[sha] is not None:
            by_pr.setdefault(pr_of[sha], []).append((moment, created, token, sha, line))

    acted, rest = [], []
    for pr, reviews in by_pr.items():
        chosen, is_acted = reviews[0], False
        if any(review[2] == REQUEST_CHANGES for review in reviews):
            pr_comments = _pr_comments(repo, pr)
            for review in reviews:
                _, created, token, sha, _ = review
                if token == REQUEST_CHANGES and acted_on(
                        pr_comments, _rejection_time(pr_comments, token, sha, created, qa_login),
                        qa_login=qa_login):
                    chosen, is_acted = review, True
                    break
        moment, created, token, sha, line = chosen
        record = {**_record(pr, sha, token, line, created), "acted_on": is_acted}
        (acted if is_acted else rest).append((moment, record))
    acted.sort(key=lambda entry: entry[0], reverse=True)
    rest.sort(key=lambda entry: entry[0], reverse=True)
    picked = [record for _, record in acted + rest][:count]
    if len(picked) < count:
        raise PickError(
            f"found {len(picked)} eligible unknown-builder critic review(s) recorded on "
            f"{recorded_model} in {repo}, and the batch asks for {count} — nothing is "
            "replayed short")
    print(f"pick: acted-on rejections {sum(r['acted_on'] for r in picked)} of {count}",
          file=sys.stderr)
    return with_rules_refs(picked)


def verifier_runs(repo: str, workflow: str):
    """The completed runs of the verifier stub `workflow`, newest first."""
    page = 1
    while True:
        doc = _gh_json("api", f"repos/{repo}/actions/workflows/{workflow}/runs"
                              f"?status=completed&per_page=100&page={page}")
        runs = doc.get("workflow_runs") if isinstance(doc, dict) else None
        if not isinstance(runs, list) or not runs:
            return
        yield from (run for run in runs if isinstance(run, dict))
        if len(runs) < 100:
            return
        page += 1


def run_log(repo: str, run_id) -> str | None:
    """The run's whole log, or None when it cannot be read — expired, or
    any other failing `gh run view`."""
    done = _gh("run", "view", str(run_id), "--repo", repo, "--log")
    return done.stdout if done.returncode == 0 and done.stdout.strip() else None


def verifier_note(log: str | None):
    """`(model, the rest of the note)` off the run's `Select model` note."""
    m = _VERIFIER_NOTE_RE.search(log or "")
    return (m.group(1), m.group(2)) if m else None


def _run_pr(repo: str, run: dict) -> int | None:
    for pull in run.get("pull_requests") or ():
        if isinstance(pull, dict) and isinstance(pull.get("number"), int):
            return pull["number"]
    head = run.get("head_sha")
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
        return None
    try:
        return pull_request_of(repo, head)
    except PickError:
        return None


def _run_verdict(comments, run: dict, qa_login: str):
    """`(token, sha, first_line, created)` of the newest qa-bot verifier
    verdict posted while the run ran, or None."""
    began, ended = _moment(run.get("created_at")), _moment(run.get("updated_at"))
    found = None
    for c in comments or ():
        if not isinstance(c, dict) or _login(c) != qa_login:
            continue
        verdict = recorded_verdict(c.get("body"), MARKERS["verifier"])
        created = c.get("created_at") or ""
        if verdict and began <= _moment(created) <= ended:
            if found is None or _moment(created) >= _moment(found[3]):
                found = (*verdict, created)
    return found


def pick_unknown_builder_verifier(*, recorded_model: str, count: int, repo: str,
                                  workflow: str,
                                  qa_login: str = fix_concurrency.QA_BOT_LOGIN) -> list:
    """`count` verifier verdicts whose run chose `recorded_model` while this
    build's model could not be read, one per pull request (its newest), each
    at the verdict comment its run posted. Every run passed over is named.
    Raises PickError naming the number found when short.

    The logs are read on `VERIFIER_LOG_READERS` readers, never more reads
    outstanding — dispatched and not yet applied, the run being applied
    included — and each run is applied in listing order, whatever order its
    read finishes in. `clock` is read here, never on a reader, before each
    run is applied and before each read is dispatched: once it reads
    `VERIFIER_PICK_BUDGET_SECONDS` gone, no further run is applied and no
    further read dispatched. A read past the run the pick stops on is
    discarded unapplied, and one not yet started is canceled."""
    picked, prs, comments_of = [], set(), {}
    elsewhere = unreadable = read = 0
    out_of_time = False
    readers = max(1, VERIFIER_LOG_READERS)
    started = clock()
    listing = iter(verifier_runs(repo, workflow))
    # (run, its pull request, its log read or None), in listing order.
    window = collections.deque()

    def skip(run_id, why):
        print(f"pick: run {run_id} skipped — {why}", file=sys.stderr)

    def spent() -> bool:
        return clock() - started >= VERIFIER_PICK_BUDGET_SECONDS

    def progress():
        print(f"pick: read {read} run(s), picked {len(picked)} of {count}", file=sys.stderr)

    def top_up(pool) -> bool:
        """Fill the window from the listing; False once the budget is spent."""
        while len(window) < readers:
            run = next(listing, None)
            if run is None:
                return True
            pr = _run_pr(repo, run)
            reading = None
            if pr is not None and pr not in prs:
                if spent():
                    return False
                reading = pool.submit(run_log, repo, run.get("id"))
            window.append((run, pr, reading))
        return True

    def apply(run, pr, reading) -> bool:
        """Give `run` its outcome; True when the listing stops here."""
        nonlocal unreadable, elsewhere
        run_id = run.get("id")
        if pr is None:
            skip(run_id, "it names no pull request")
            return False
        if pr in prs:
            if reading is not None:
                reading.cancel()
            skip(run_id, f"pr={pr} is already picked at a newer run")
            return False
        log = reading.result()
        if log is None:
            skip(run_id, "its log could not be read")
            unreadable += 1
            if unreadable == UNREADABLE_LOGS_IN_A_ROW:
                print(f"pick: {unreadable} run logs in a row could not be read — the older "
                      "runs' logs have expired too, so the listing stops here", file=sys.stderr)
                return True
            return False
        unreadable = 0
        note = verifier_note(log)
        if note is None:
            skip(run_id, "its log carries no Select model note for the verifier")
            return False
        model, rest = note
        if UNKNOWN_BUILDER_CLAUSE not in rest:
            skip(run_id, f"its builder's model was read (chosen {model})")
            return False
        if model != recorded_model:
            elsewhere += 1
            skip(run_id, f"chosen on {model}, not {recorded_model}")
            return False
        if pr not in comments_of:
            comments_of[pr] = _pr_comments(repo, pr)
        verdict = _run_verdict(comments_of[pr], run, qa_login)
        if verdict is None:
            skip(run_id, f"no verifier verdict comment on pr={pr} was posted while it ran")
            return False
        token, sha, line, created = verdict
        prs.add(pr)
        picked.append(_record(pr, sha, token, line, created))
        return False

    pool = ThreadPoolExecutor(max_workers=readers)
    try:
        while len(picked) < count:
            if spent() or not top_up(pool):
                out_of_time = True
                break
            if not window:
                break
            stop = apply(*window.popleft())
            read += 1
            if read % VERIFIER_PROGRESS_EVERY == 0:
                progress()
            if stop:
                break
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    progress()
    print(f"pick: skipped {elsewhere} unknown-builder verifier run(s) chosen on another "
          f"model than {recorded_model}", file=sys.stderr)
    if len(picked) < count:
        budget = (f". It stopped at its {VERIFIER_PICK_BUDGET_SECONDS}-second time budget "
                  f"after reading {read} run(s)") if out_of_time else ""
        raise PickError(
            f"found {len(picked)} eligible unknown-builder verifier review(s) recorded on "
            f"{recorded_model} in {repo}'s {workflow} runs, and the batch asks for {count} — "
            f"nothing is replayed short{budget}")
    return picked


def pick_from(path: str) -> list:
    """Every acted-on record of an earlier pick's records file, unchanged."""
    try:
        records = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PickError(f"the earlier pick {path} could not be read ({exc})") from exc
    if not isinstance(records, list):
        raise PickError(f"the earlier pick {path} is not a list of records")
    kept = [r for r in records if isinstance(r, dict) and r.get("acted_on") is True]
    if not kept:
        raise PickError(f"the earlier pick {path} holds no acted-on record — nothing to replay")
    return kept


def parse_prs(text: str) -> list:
    out = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or int(part) < 1:
            raise PickError(f"`prs` must be pull request numbers, comma-separated — got {part!r}")
        out.append(int(part))
    return out


def pick_prs(prs, *, reviewer: str, repo: str,
             qa_login: str = fix_concurrency.QA_BOT_LOGIN) -> list:
    """Each listed pull request at its newest real `reviewer` verdict, or at
    its final head with `recorded` `none` when it has none. A critic record
    names its `rules_ref`; a verifier record's stays empty."""
    marker = MARKERS[reviewer]
    picked = []
    for pr in prs:
        comments = _pr_comments(repo, pr)
        latest = None
        for c in comments:
            if _login(c) != qa_login:
                continue
            found = recorded_verdict(c.get("body"), marker)
            if found:
                latest = (*found, c.get("created_at") or "")
        if latest:
            token, sha, line, created = latest
            picked.append(_record(pr, sha, token, line, created))
            continue
        head = ((_gh_json("api", f"repos/{repo}/pulls/{pr}").get("head")) or {}).get("sha")
        if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
            raise PickError(f"pull request {pr} has no readable head")
        picked.append(_record(pr, head, NONE, ""))
    return with_rules_refs(picked) if reviewer == "critic" else picked


# --------------------------------------------------------------------------- #
# compare                                                                      #
# --------------------------------------------------------------------------- #

def read_replays(artifacts: Path, role: str) -> dict:
    """`{pr: (verdict | None, first line of the .md, run_url)}` for every
    shadow verdict of `role` under `artifacts`, however it was unpacked."""
    found = {}
    for path in sorted(Path(artifacts).rglob("shadow-verdict.json")) if Path(artifacts).is_dir() else ():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or doc.get("role") != role:
            continue
        try:
            pr = int(doc.get("pr"))
        except (TypeError, ValueError):
            continue
        verdict = doc.get("verdict")
        md = path.with_name("shadow-verdict.md")
        try:
            line = merge_gate.first_line(md.read_text(encoding="utf-8")).strip()
        except OSError:
            line = ""
        found[pr] = (verdict if isinstance(verdict, str) and verdict else None,
                     line, doc.get("run_url") or "")
    return found


def pr_line(pr: int, sha: str, recorded: str, replayed: str | None, match: str) -> str:
    return (f"{TAG} pr={pr} sha={sha} recorded={recorded} "
            f"replayed={replayed or NONE} match={match}")


def summary_line(matched: int, of: int, *, reviewer: str, model: str,
                 recorded_model: str) -> str:
    pct = matched * 100 // of if of else 0
    return (f"{TAG} matched {matched} of {of} ({pct}%) reviewer={reviewer} "
            f"model={model} recorded_model={recorded_model or NONE}")


def acted_on_line(rejected_again: int, of: int, *, model: str) -> str:
    """The critic's bar (DRE-7304): how many acted-on rejections the replay
    rejected again."""
    pct = rejected_again * 100 // of if of else 0
    return (f"{TAG} acted-on rejections {rejected_again} of {of} rejected again ({pct}%) "
            f"reviewer=critic model={model}")


def fail_to_pass_line(replayed_pass: int, of: int, *, model: str) -> str:
    """The verifier's bar (DRE-7304): how many recorded FAILs the replay
    turned into PASS, of the recorded FAILs a zero rests on."""
    return (f"{TAG} recorded FAIL replayed PASS {replayed_pass} of {of} "
            f"reviewer=verifier model={model}")


def _bar(rows, *, reviewer: str, model: str) -> tuple:
    """`(report key, the bar's numbers and pull requests)`. A replay with no
    verdict counts as not rejected again, and as not PASS, and is named
    apart."""
    if reviewer == "critic":
        rows = [r for r in rows if r["acted_on"] and r["recorded"] == REQUEST_CHANGES]
        hit = [r["pr"] for r in rows if r["replayed"] == REQUEST_CHANGES]
        bar = {"line": acted_on_line(len(hit), len(rows), model=model),
               "rejected_again": len(hit), "of": len(rows),
               "pct": len(hit) * 100 // len(rows) if rows else 0,
               "prs_rejected_again": hit,
               "prs_not_rejected_again": [r["pr"] for r in rows
                                          if r["replayed"] not in (REQUEST_CHANGES, NONE)]}
        key = "acted_on_rejections"
    else:
        rows = [r for r in rows if r["recorded"] == FAIL]
        hit = [r["pr"] for r in rows if r["replayed"] == PASS]
        bar = {"line": fail_to_pass_line(len(hit), len(rows), model=model),
               "replayed_pass": len(hit), "of": len(rows), "prs_replayed_pass": hit,
               "prs_not_pass": [r["pr"] for r in rows if r["replayed"] not in (PASS, NONE)]}
        key = "recorded_fail_replayed_pass"
    bar["prs_no_verdict"] = [r["pr"] for r in rows if r["replayed"] == NONE]
    return key, bar


def compare(records, artifacts: Path, *, reviewer: str, model: str,
            recorded_model: str) -> dict:
    replays = read_replays(artifacts, reviewer)
    rows, mismatches, lines = [], [], []
    matched = of = 0
    for r in records:
        pr, sha, recorded = int(r["pr"]), r["sha"], r["recorded"]
        rules_ref = r.get("rules_ref") or ""
        verdict, replayed_line, run_url = replays.get(pr, (None, "", ""))
        if recorded == NONE:
            match = "n/a"
        else:
            of += 1
            match = "yes" if verdict == recorded else "no"
            if match == "yes":
                matched += 1
            else:
                kind = NO_VERDICT if verdict is None else DISAGREEMENT
                if not replayed_line:
                    replayed_line = ("(no artifact)" if pr not in replays
                                     else "(no verdict)")
                mismatches.append({
                    "pr": pr, "sha": sha, "rules_ref": rules_ref, "kind": kind,
                    "recorded": recorded,
                    "replayed": verdict or NONE,
                    "recorded_line": r.get("recorded_line") or "",
                    "replayed_line": replayed_line})
        row = {"pr": pr, "sha": sha, "rules_ref": rules_ref, "recorded": recorded,
               "replayed": verdict or NONE, "match": match, "run_url": run_url}
        if reviewer == "critic":
            row["acted_on"] = r.get("acted_on") is True
        rows.append(row)
        lines.append(pr_line(pr, sha, recorded, verdict, match))
    summary = summary_line(matched, of, reviewer=reviewer, model=model,
                           recorded_model=recorded_model)
    lines.append(summary)
    bar_key, bar = _bar(rows, reviewer=reviewer, model=model)
    lines.append(bar["line"])
    return {
        "reviewer": reviewer, "model": model, "recorded_model": recorded_model or NONE,
        "matched": matched, "of": of, "pct": matched * 100 // of if of else 0,
        "without_rules_ref": sum(1 for row in rows if not row["rules_ref"]),
        "summary": summary, "replays": rows, "mismatches": mismatches,
        bar_key: bar, "lines": lines,
    }


def render_md(report: dict) -> str:
    out = [f"# Shadow Replay — {report['reviewer']} on {report['model']}", "",
           report["summary"], "",
           f"Recorded on: {report['recorded_model']}. A replay whose recorded "
           "verdict is `none` is `n/a` and outside both counts.", "",
           "Ran with no `rules_ref`, so by today's rules: "
           f"{report['without_rules_ref']} of {len(report['replays'])} replays.", "",
           "| PR | sha | rules_ref | recorded | replayed | match |",
           "| -- | -- | -- | -- | -- | -- |"]
    for row in report["replays"]:
        ref = f"`{row['rules_ref']}`" if row["rules_ref"] else NONE
        out.append(f"| {row['pr']} | `{row['sha']}` | {ref} | {row['recorded']} | "
                   f"{row['replayed']} | {row['match']} |")
    out += ["", "## Mismatches", ""]
    if not report["mismatches"]:
        out.append("None.")
    for m in report["mismatches"]:
        out += [f"### PR {m['pr']} — {m['kind']}", "",
                f"- recorded ({m['recorded']}): {m['recorded_line'] or '(not kept)'}",
                f"- replayed ({m['replayed']}): {m['replayed_line']}", ""]
    out += _render_bar(report)
    return "\n".join(out).rstrip() + "\n"


def _prs(prs) -> str:
    return ", ".join(f"PR {pr}" for pr in prs) or "none"


def _render_bar(report: dict) -> list:
    if "acted_on_rejections" in report:
        bar = report["acted_on_rejections"]
        return ["", "## Acted-on rejections", "", bar["line"], "",
                f"- rejected again: {_prs(bar['prs_rejected_again'])}",
                f"- not rejected again: {_prs(bar['prs_not_rejected_again'])}",
                "- no verdict, counted as not rejected again: "
                f"{_prs(bar['prs_no_verdict'])}"]
    bar = report["recorded_fail_replayed_pass"]
    return ["", "## Recorded FAIL replayed PASS", "", bar["line"], "",
            f"- replayed PASS: {_prs(bar['prs_replayed_pass'])}",
            f"- not PASS: {_prs(bar['prs_not_pass'])}",
            f"- no verdict, counted as not PASS: {_prs(bar['prs_no_verdict'])}"]


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _pick_main(args) -> int:
    try:
        if bool(args.from_pick) != args.acted_on_only:
            raise PickError("`--from-pick` and `--acted-on-only` go together: the control "
                            "replays exactly the acted-on records of an earlier pick")
        label = args.repo_label or "repo:" + args.repo.rsplit("/", 1)[-1]
        recorded_model = args.recorded_model or UNKNOWN_BUILDER_MODEL
        if args.from_pick:
            records = pick_from(args.from_pick)
        elif args.count < 1:
            raise PickError(f"`count` must be at least 1 — got {args.count}")
        elif prs := parse_prs(args.prs):
            records = pick_prs(prs, reviewer=args.reviewer, repo=args.repo)
        elif args.unknown_builder and args.reviewer == "critic":
            records = pick_unknown_builder_critic(
                linear_critic_comments(recorded_model, label), recorded_model=recorded_model,
                count=args.count, repo=args.repo, repo_label=args.repo_label)
        elif args.unknown_builder:
            if not args.verify_workflow:
                raise PickError("the verifier's unknown-builder pick reads the runs of the "
                                "verifier stub — name its workflow file in `verify_workflow`")
            records = pick_unknown_builder_verifier(
                recorded_model=recorded_model, count=args.count, repo=args.repo,
                workflow=args.verify_workflow)
        elif args.reviewer == "critic" and args.recorded_model:
            comments = linear_critic_comments(args.recorded_model, label)
            records = pick_recorded_model(
                comments, recorded_model=args.recorded_model, count=args.count,
                repo=args.repo, repo_label=args.repo_label)
        elif args.reviewer == "critic":
            raise PickError("nothing to pick from: name `recorded_model` (the model the "
                            "critic's reviews were recorded on) or list `prs`")
        else:
            raise PickError("the verifier has no recorded-model pick — list the pull "
                            "requests to replay in `prs`")
    except PickError as exc:
        print(f"::error::shadow replay pick: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 — a failed read is a red pick, never a short batch
        print(f"::error::shadow replay pick: the read failed: {exc}")
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    rows = matrix(records)
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"matrix={json.dumps(rows, separators=(',', ':'))}\n")
    print(f"pick: {len(rows)} replay(s), reviewer={args.reviewer}")
    for row in rows:
        print(f"pick: pr={row['pr']} sha={row['sha']} recorded={row['recorded']} "
              f"rules_ref={row['rules_ref'] or NONE}")
    return 0


def _compare_main(args) -> int:
    try:
        records = json.loads(Path(args.pick).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"::error::shadow replay compare: the pick could not be read ({exc}) — "
              "nothing to compare")
        return 1
    report = compare(records, Path(args.artifacts), reviewer=args.reviewer,
                     model=args.model, recorded_model=args.recorded_model)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    lines = report.pop("lines")
    (out / REPORT_JSON).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
    (out / REPORT_MD).write_text(render_md(report), encoding="utf-8")
    for line in lines:
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    pick = sub.add_parser("pick", help="choose the batch and write the matrix")
    pick.add_argument("--reviewer", choices=REVIEWERS, required=True)
    pick.add_argument("--recorded-model", default="")
    pick.add_argument("--count", type=int, default=20)
    pick.add_argument("--prs", default="")
    pick.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    pick.add_argument("--repo-label", default="",
                      help="the Linear label of this repo's cards (default repo:<name>)")
    pick.add_argument("--out", required=True, help="the full records, as JSON")
    pick.add_argument("--github-output", default="")
    pick.add_argument("--unknown-builder", action="store_true",
                      help="pick the reviews run while the build's model could not be read "
                           f"(an empty --recorded-model means {UNKNOWN_BUILDER_MODEL})")
    pick.add_argument("--verify-workflow", default="",
                      help="the verifier stub's workflow file, whose runs the verifier's "
                           "unknown-builder pick reads")
    pick.add_argument("--from-pick", default="",
                      help="an earlier pick's records file (shadow-replay-pick.json)")
    pick.add_argument("--acted-on-only", action="store_true",
                      help="with --from-pick: re-emit only its acted-on records")

    cmp_ = sub.add_parser("compare", help="compare the replays with the recorded verdicts")
    cmp_.add_argument("--reviewer", choices=REVIEWERS, required=True)
    cmp_.add_argument("--model", required=True)
    cmp_.add_argument("--recorded-model", default="")
    cmp_.add_argument("--pick", required=True)
    cmp_.add_argument("--artifacts", required=True)
    cmp_.add_argument("--out-dir", required=True)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "pick":
        return _pick_main(args)
    return _compare_main(args)


if __name__ == "__main__":
    sys.exit(main())
