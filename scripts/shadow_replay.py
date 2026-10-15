#!/usr/bin/env python3
"""Shadow Replay: pick past reviews, then say how often the replays agreed (DRE-7197).

The critic (DRE-7193, `qa-review.yml`) and the verifier (DRE-7194,
`verify.yml`) each replay ONE past pull request in shadow mode: at the head
its recorded verdict was bound to, on a model the caller names, writing the
verdict to the `shadow-verdict-<role>-<pr>` artifact and posting nothing.
`.github/workflows/shadow-replay.yml` runs a batch of them, and this module is
its two ends. The noise-floor reading is taken with it, and the model-switch
epics (DRE-7185, DRE-7187, DRE-7188, DRE-7189) read their bars with it.

`pick` chooses the batch, one of two ways:

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

It writes the matrix, `[{pr, sha, recorded, rules_ref}]`, as the `matrix`
step output, and the full records (with each recorded first line and the
verdict's time) to `--out`.

`compare` reads the downloaded artifacts against the pick and prints, in
this order, one line per pull request and then exactly one summary:

    shadow-replay: pr=<n> sha=<40hex> recorded=<token|none> replayed=<token|none> match=<yes|no|n/a>
    shadow-replay: matched <k> of <n> (<pct>%) reviewer=<role> model=<id> recorded_model=<id|none>

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
    python3 scripts/shadow_replay.py compare --reviewer critic --model <id> \\
        --recorded-model <id> --pick pick.json --artifacts <dir> --out-dir <dir>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fix_concurrency  # noqa: E402
import linear_ops  # noqa: E402
import merge_gate  # noqa: E402

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
        try:
            comments = merge_gate.flatten_pages(_gh_json(
                "api", f"repos/{repo}/issues/{pr}/comments", "--paginate", "--slurp"))
        except ValueError as exc:
            raise PickError(f"pull request {pr}'s comments did not read: {exc}") from exc
        latest = None
        for c in comments:
            if ((c.get("user") or {}).get("login")) != qa_login:
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
        rows.append({"pr": pr, "sha": sha, "rules_ref": rules_ref, "recorded": recorded,
                     "replayed": verdict or NONE, "match": match, "run_url": run_url})
        lines.append(pr_line(pr, sha, recorded, verdict, match))
    summary = summary_line(matched, of, reviewer=reviewer, model=model,
                           recorded_model=recorded_model)
    lines.append(summary)
    return {
        "reviewer": reviewer, "model": model, "recorded_model": recorded_model or NONE,
        "matched": matched, "of": of, "pct": matched * 100 // of if of else 0,
        "without_rules_ref": sum(1 for row in rows if not row["rules_ref"]),
        "summary": summary, "replays": rows, "mismatches": mismatches, "lines": lines,
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
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _pick_main(args) -> int:
    try:
        if args.count < 1:
            raise PickError(f"`count` must be at least 1 — got {args.count}")
        prs = parse_prs(args.prs)
        if prs:
            records = pick_prs(prs, reviewer=args.reviewer, repo=args.repo)
        elif args.reviewer == "critic" and args.recorded_model:
            comments = linear_critic_comments(
                args.recorded_model, args.repo_label or "repo:" + args.repo.rsplit("/", 1)[-1])
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
