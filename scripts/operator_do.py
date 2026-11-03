#!/usr/bin/env python3
"""The Operator Agent's executor for a `do` card (DRE-8924, epic DRE-8738).

Part 1 decides that a card is `do` and names its step; the model writes the
plan, `<CARD>.plan.json`, in the shape `operator_do_ops.py` declares
(DRE-8922). This module carries the step out, or in a dry run prints it. It
writes nothing to Linear but the one project status the `linear-admin` op
sets: the claim, the receipt and the close are DRE-8923's composers and
DRE-8925's commands, so every other Linear write the Operator Agent makes
stays in `operator_agent_apply.py`, as part 1 declared.

## read

`read <CARD> --out <dir>` reads the card and its whole thread, as part 1's
`select` does, and writes `<dir>/<CARD>.md` with part 1's own writer,
`operator_agent.card_file`: the same sentinel lines and the card's newest
comments, so a `continue` card's newest `do` receipt is in the file the model
reads — and kept whole when the comments it keeps start after it. Its only
key is `LINEAR_API_KEY`, and it only reads. A directory inside this
repository is refused (exit 3), as `select` refuses one.

## check

`check <CARD> --plan <CARD>.plan.json --card <dir>/<CARD>.md --out
<CARD>.check.json [--transcript transcript.json]` runs `operator_do_ops.check`
over the plan against the card's title and description as the card file
lists them — never its comments, which anyone can write — `config/
repo-map.json` and the slug of the `repo:` label on the file's `Labels:`
line. A plan written for another card is refused too. It always writes

    {"ok": <bool>, "kind": "<kind>", "repo": "<owner/name>" | null,
     "command_lines": ["<command line>", ...]}

where `repo` is `operator_do_ops.plan_repo(plan)` — the one repository the
workflow mints a token for (DRE-8928) — and `command_lines` are the lines the
claim lists (DRE-8925), empty for a refused plan. A refused plan prints one
`operator-agent: DRE-<n> do refused — <op> · <why>` line per refusal, writes
a transcript (`--transcript`, by default `transcript.json` beside the check
file) with `failed: true` and the refusal as its one entry (`"command":
"check"`, `"writes": false`, `"exit": 1`), runs nothing and exits 1.

## run

`run <CARD> --plan <CARD>.plan.json --out transcript.json [--dry-run]`
renders every op first, with `operator_do_ops.render`, then runs each in
order with one `subprocess.run` of its argv — never through a shell — from
this repository's root, with an environment holding `PATH`, `HOME` and the
one variable `operator_do_ops.credential(kind)` names, and nothing else. It
stops at the first command that exits non-zero, or cannot start, and marks
the transcript `failed: true`. A plan that does not render, or was written
for another card, runs nothing and is one failed `run` entry. Each command
prints `operator-agent: DRE-<n> ran: <command line> — exit <n>` and records
`writes` from `operator_do_ops.writes(op)`. A `pr-open` that exits 0 names
the `html_url` of its JSON output as the transcript's `pull_request`, read
before the output is cut. Under `--dry-run` nothing runs: each op prints
`operator-agent: DRE-<n> would run: <command line>`, and the transcript says
`dry_run: true` with no exits. `run` exits 0 whenever it wrote a transcript,
failed or not, and 2 only when it could not write one.

    {"card": "DRE-<n>", "kind": "<kind>", "dry_run": <bool>,
     "commands": [{"command": "<command line>", "writes": <bool>,
                   "exit": <int>, "output": "<text>"}],
     "failed": <bool>, "pull_request": "<url>" | null}

is the shape `operator_decide.do_receipt_text` reads (DRE-8923). An output
is the command's stdout, then its stderr; past `operator_decide.OUTPUT_CHARS`
(1,500) it is cut, with `… cut, <n> characters` after it, and any value of
`GH_TOKEN` or `LINEAR_API_KEY` in it reads `[token]` — the rule the receipt
applies — so the transcript never holds a credential.

## probe

`probe` reads and writes nothing. For each value of `config/repo-map.json`
that opens the App's organization (`config/operator-do.json`'s `org`), one
read of the bureau App's installation for that repository, `GET
/repos/<owner/name>/installation`, with an App JWT from
`harness/app_token.py`'s `app_jwt` and its client, printing

    operator-agent: credential <owner/name> — actions_variables=<level> pull_requests=<level> contents=<level>

Each level is read off the answer's `permissions` object under exactly those
keys — `actions_variables` is GitHub's key for the repository-variables grant
(`console_receipt.py`, DRE-8922), never `variables` — and a key the object
lacks reads `none`; a refused read prints `operator-agent: credential
<owner/name> — unread <HTTP status>`. Then one read of the Linear key's own
user: `operator-agent: credential linear — admin=<true|false>
guest=<true|false>`, or `— unread <why>`. `admin=` is printed for the record
and gates nothing: no declared op needs an admin key (DRE-8922). It holds
`BUREAU_APP_ID`, `BUREAU_APP_PRIVATE_KEY` and `LINEAR_API_KEY`, never prints
a token, and exits 0 with an unread line: it reports, it does not gate.

## linear project-status

`linear project-status "<project>" <status>` is the one Linear command the
`linear-admin` op renders to. It refuses a status off the op's `choices`,
reads the project by its exact name, refuses a name that matches none or
more than one, makes one `projectUpdate` setting the status, reads it back
and prints the status read; a read-back that is not the status asked exits
1. A `projectUpdate` Linear refuses exits 1 with Linear's message as its
output. With no `LINEAR_API_KEY` it sends nothing and exits 1.

## The reads, named

Every Linear read here is direct, through `linear_ops.py`, on the planner's
bucket the workflow already gives part 1, and is counted by `linear_ops.py`'s
own `linear-calls:` and `linear-budget:` lines at exit. A pass makes at most:

  * one card read and one whole-thread read per `do` leg (`read`, at most
    `max_do_per_pass`, 3, legs a pass);
  * two reads per `project-status` op (the name lookup, which carries the
    workspace's project statuses in the same request, and the read-back);
  * one read of the key's user (`probe`).

None of them goes through the console's read door, and each for a stated
reason. `read` runs in the `do` job, which never holds `id-token: write`
(DRE-8928 keeps every job that can run a step away from a token that could
assume a cloud role), and the door answers only a GitHub Actions OIDC token.
The door serves no projects, so `project-status` has no door read to make.
The door's `viewer` carries an id and not the key's `admin` or `guest`, so
`probe` has none either. The one new card read that CAN go through the door
— the close's, in the job that already holds the OIDC token for the console
— does (DRE-8925). The read-door epic's per-card child (DRE-8789) names the
Operator Agent as staying on Linear and leaves moving it to this agent's own
epics; this list is what the last child of that epic (DRE-8790) reads to name
or move them.

A step this executor refuses, or cannot finish, is left for the CEO or the
card's owner, who reads exactly what ran on the receipt.

## CLI

    operator_do.py read <CARD> --out DIR             exit 3: DIR is inside this repository
    operator_do.py check <CARD> --plan P --card F --out C [--transcript T]
                                                     exit 1: the plan is refused
    operator_do.py run <CARD> --plan P --out T [--dry-run]
                                                     exit 2: no transcript written
    operator_do.py probe                             exit 0 always
    operator_do.py linear project-status "<project>" <status>
                                                     exit 1: refused or not set
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_snapshot  # noqa: E402 — inside_repo, the card file's one rule
import linear_ops  # noqa: E402
import operator_agent  # noqa: E402 — the card file and the repo map
import operator_decide  # noqa: E402
import operator_do_ops  # noqa: E402
from harness import app_token  # noqa: E402
from harness.github_api import GitHub, GitHubError  # noqa: E402

ROOT = operator_agent.ROOT
TAG = operator_decide.LOG_TAG

#: The variables a command's environment keeps besides its one credential.
KEPT_ENV = ("PATH", "HOME")
#: The installation's permissions the probe prints, in order, under GitHub's
#: own keys.
PROBED = ("actions_variables", "pull_requests", "contents")
NO_LEVEL = "none"
#: How long one command may run before it is a failed command.
COMMAND_SECONDS = 600
#: The exit a command that could not start, or ran out of time, records.
EXIT_NOT_STARTED, EXIT_TIMED_OUT = 127, 124
PR_OPEN = "pr-open"
PROJECT_STATUS = "project-status"

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_NO_TRANSCRIPT = 2
EXIT_INSIDE_REPO = 3

#: The App's REST opener; None is the client's own urlopen. Tests stub it.
_github_opener = None

_CARD_LINE = re.compile(r"^Card: (?P<card>\S+)$", re.MULTILINE)
_TITLE_LINE = re.compile(r"^Title: (?P<title>.*)$", re.MULTILINE)
_LABELS_LINE = re.compile(r"^Labels: (?P<labels>.*)$", re.MULTILINE)
_COMMENTS_HEAD = re.compile(r"^Comments — the \d+ newest, oldest first:$", re.MULTILINE)


# --------------------------------------------------------------------------- #
# output                                                                       #
# --------------------------------------------------------------------------- #


def clean(output) -> str:
    """`output` as a transcript holds it: every value of `GH_TOKEN` or
    `LINEAR_API_KEY` read as `[token]`, then cut at `OUTPUT_CHARS` with
    `… cut, <n> characters` after it."""
    text = operator_decide._scrub(str(output or ""))
    cut = len(text) - operator_decide.OUTPUT_CHARS
    if cut > 0:
        text = f"{text[:operator_decide.OUTPUT_CHARS]}\n… cut, {cut} characters"
    return text


def _say(card: str, what: str) -> None:
    print(f"{TAG} {card} {what}")


def _write_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _read_json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _transcript(card: str, kind, commands: list, *, dry_run: bool = False,
                failed: bool = False, pull_request: str | None = None) -> dict:
    return {"card": card, "kind": kind if isinstance(kind, str) else "", "dry_run": dry_run,
            "commands": commands, "failed": failed, "pull_request": pull_request}


def _refusal_entry(command: str, why) -> dict:
    return {"command": command, "writes": False, "exit": 1, "output": clean(why)}


# --------------------------------------------------------------------------- #
# read                                                                         #
# --------------------------------------------------------------------------- #


_ISSUE_QUERY = """query($id: String!) { issue(id: $id) {
  identifier title description state { name } labels { nodes { name } }
} }"""


def read_card(card: str, out_dir: str) -> str:
    """Write `<out_dir>/<card>.md` and answer its path: one card read, one
    whole-thread read, and nothing written to Linear."""
    issue = linear_ops.gql(_ISSUE_QUERY, {"id": card})["issue"]
    records = linear_ops.comment_records(card, whole_thread=True)
    row = {"card": issue.get("identifier") or card, "title": issue.get("title") or "",
           "lane": operator_agent._lane(issue), "labels": operator_agent._labels(issue),
           "repo": operator_agent.repo_of(issue, operator_agent._repo_map()) or "",
           "description": issue.get("description") or ""}
    standing = operator_agent.do_standing(records, operator_decide.fingerprint(row),
                                          operator_agent._now())
    action, record = standing if standing is not None else (operator_agent.DECIDE, None)
    row.update(action=action, receipt=(record or {}).get("body"))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{card}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(operator_agent.card_file(row, records))
    return path


# --------------------------------------------------------------------------- #
# check                                                                        #
# --------------------------------------------------------------------------- #


def card_text(card_md: str) -> tuple:
    """`(card, text, slug)` off a card file: the card it names, its title and
    description as one text — never its comments — and the slug of the first
    `repo:` label on its `Labels:` line, or None."""
    card = _CARD_LINE.search(card_md)
    title = _TITLE_LINE.search(card_md)
    labels = _LABELS_LINE.search(card_md)
    head, _sep, rest = card_md.partition("\nDescription:\n")
    comments = _COMMENTS_HEAD.search(rest)
    description = rest[:comments.start()] if comments else ""
    slug = next((name.strip()[len("repo:"):].strip().lower()
                 for name in (labels.group("labels").split(",") if labels else [])
                 if name.strip().lower().startswith("repo:")), None)
    return ((card.group("card") if card else None),
            f"{title.group('title') if title else ''}\n{description.strip()}", slug)


def _refusal_line(refusal: str) -> str:
    op, sep, why = refusal.partition(": ")
    return f"do refused — {op} · {why}" if sep else f"do refused — plan · {refusal}"


def check(card: str, plan, card_md: str) -> tuple:
    """`(check file, refusals)` for one plan against its card file."""
    named, text, slug = card_text(card_md)
    refusals = []
    if named != card:
        refusals.append(f"plan: the card file is {named or 'no card'}'s, not {card}'s")
    if isinstance(plan, dict) and plan.get("card") != card:
        refusals.append(f"plan: it is written for {plan.get('card')!r}, not {card}")
    _ok, found = operator_do_ops.check(plan, text, operator_agent._repo_map(), slug)
    refusals += found
    try:
        repo = operator_do_ops.plan_repo(plan)
    except (ValueError, AttributeError):
        repo = None
    lines = []
    if not refusals:
        lines = [operator_do_ops.command_line(op) for op in plan["ops"]]
    kind = plan.get("kind") if isinstance(plan, dict) else None
    return ({"ok": not refusals, "kind": kind if isinstance(kind, str) else "",
             "repo": repo, "command_lines": lines}, refusals)


# --------------------------------------------------------------------------- #
# run                                                                          #
# --------------------------------------------------------------------------- #


def command_env(kind: str, doc: dict | None = None) -> dict:
    """`PATH`, `HOME` and the kind's one credential, as this process holds
    them, and nothing else."""
    keep = KEPT_ENV + (operator_do_ops.credential(kind, doc),)
    return {name: os.environ[name] for name in keep if name in os.environ}


def _pull_request(stdout: str) -> str | None:
    try:
        said = json.loads(stdout)
    except ValueError:
        return None
    url = said.get("html_url") if isinstance(said, dict) else None
    return url if isinstance(url, str) and url.startswith("https://") else None


def _one(argv: list, env: dict) -> tuple:
    """`(exit, stdout, output)` of one command, run without a shell."""
    try:
        done = subprocess.run(argv, shell=False, env=env, cwd=ROOT, capture_output=True,
                              text=True, timeout=COMMAND_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return EXIT_TIMED_OUT, "", f"ran past {COMMAND_SECONDS} seconds and was stopped"
    except OSError as exc:
        return EXIT_NOT_STARTED, "", f"could not start: {exc}"
    stdout, stderr = done.stdout or "", done.stderr or ""
    return done.returncode, stdout, "\n".join(part for part in (stdout, stderr) if part)


def run(card: str, plan, dry_run: bool = False) -> dict:
    """The transcript of one plan's commands, run in order or, `dry_run`,
    only printed. Prints one line per command."""
    kind = plan.get("kind") if isinstance(plan, dict) else None
    try:
        if not isinstance(plan, dict) or plan.get("card") != card:
            raise ValueError(f"the plan is not {card}'s")
        doc = operator_do_ops.load()
        env = command_env(kind, doc)
        steps = [(operator_do_ops.render(op, doc), operator_do_ops.command_line(op, doc),
                  operator_do_ops.writes(op, doc), op.get("op")) for op in plan["ops"]]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        _say(card, f"do refused — run · {exc}")
        return _transcript(card, kind, [_refusal_entry("run", exc)], failed=True)

    if dry_run:
        for _argv, line, _writes, _name in steps:
            _say(card, f"would run: {line}")
        return _transcript(card, kind, [{"command": line, "writes": writes}
                                        for _argv, line, writes, _name in steps], dry_run=True)

    commands, pull_request = [], None
    for argv, line, writes, name in steps:
        code, stdout, output = _one(argv, env)
        _say(card, f"ran: {line} — exit {code}")
        commands.append({"command": line, "writes": writes, "exit": code,
                         "output": clean(output)})
        if code != 0:
            return _transcript(card, kind, commands, failed=True, pull_request=pull_request)
        if name == PR_OPEN:
            pull_request = _pull_request(stdout) or pull_request
    return _transcript(card, kind, commands, pull_request=pull_request)


# --------------------------------------------------------------------------- #
# probe                                                                        #
# --------------------------------------------------------------------------- #


_VIEWER_QUERY = "query { viewer { id admin guest } }"


def _fleet(doc: dict) -> list:
    return list(dict.fromkeys(repo for repo in operator_agent._repo_map().values()
                              if isinstance(repo, str) and repo.startswith(doc["org"])))


def _levels(permissions) -> str:
    permissions = permissions if isinstance(permissions, dict) else {}
    return " ".join(f"{key}={permissions.get(key) or NO_LEVEL}" for key in PROBED)


def _bool(value) -> str:
    return "true" if value is True else "false"


def probe() -> None:
    """One credential line per fleet repository, then one for Linear."""
    doc = operator_do_ops.load()
    app_id = os.environ.get("BUREAU_APP_ID") or ""
    key = os.environ.get("BUREAU_APP_PRIVATE_KEY") or ""
    client, why = None, "no App key in BUREAU_APP_ID and BUREAU_APP_PRIVATE_KEY"
    if app_id and key:
        try:
            client = GitHub(app_token.app_jwt(app_id, key), opener=_github_opener,
                            identity="bureau-app")
        except (RuntimeError, OSError):
            why = "no App JWT could be signed"
    for repo in _fleet(doc):
        if client is None:
            print(f"{TAG} credential {repo} — unread {why}")
            continue
        try:
            installation = client.request("GET", f"/repos/{repo}/installation") or {}
        except GitHubError as exc:
            print(f"{TAG} credential {repo} — unread {exc.status}")
            continue
        print(f"{TAG} credential {repo} — {_levels(installation.get('permissions'))}")
    print(f"{TAG} credential linear — {_linear_user()}")


def _linear_user() -> str:
    if not os.environ.get("LINEAR_API_KEY"):
        return "unread no LINEAR_API_KEY"
    try:
        viewer = (linear_ops.gql(_VIEWER_QUERY) or {}).get("viewer") or {}
    except Exception as exc:  # noqa: BLE001 — it reports, it does not gate
        return f"unread {_flat(operator_decide._scrub(str(exc)))}"
    return f"admin={_bool(viewer.get('admin'))} guest={_bool(viewer.get('guest'))}"


def _flat(text: str, limit: int = 300) -> str:
    return " ".join(text.split())[:limit]


# --------------------------------------------------------------------------- #
# linear project-status                                                        #
# --------------------------------------------------------------------------- #


_PROJECTS_QUERY = """query($name: String!) {
  projects(filter: { name: { eq: $name } }, first: 10) { nodes { id name status { name } } }
  projectStatuses { nodes { id name type } }
}"""
_PROJECT_UPDATE = """mutation($id: String!, $statusId: String!) {
  projectUpdate(id: $id, input: { statusId: $statusId }) { success }
}"""
_PROJECT_QUERY = """query($id: String!) { project(id: $id) { id name status { name } } }"""


def _choices(doc: dict) -> list:
    row = doc["kinds"]["linear-admin"]["ops"][PROJECT_STATUS]
    return list(row["choices"]["status"])


def project_status(name: str, status: str) -> int:
    """Set the status of the one project named exactly `name`; print what it
    read back. Exit 0 when the read-back is `status`, 1 otherwise."""
    choices = _choices(operator_do_ops.load())
    if status not in choices:
        print(f"project-status refused — {status!r} is not one of {', '.join(choices)}")
        return EXIT_REFUSED
    if not os.environ.get("LINEAR_API_KEY"):
        print("project-status refused — no LINEAR_API_KEY; nothing was sent")
        return EXIT_REFUSED
    try:
        found = linear_ops.gql(_PROJECTS_QUERY, {"name": name})
    except linear_ops.LinearError as exc:
        print(f"project-status unread — {exc}")
        return EXIT_REFUSED
    named = [p for p in ((found.get("projects") or {}).get("nodes") or [])
             if isinstance(p, dict) and p.get("name") == name]
    if not named:
        print(f"project-status refused — no project is named exactly {name!r}")
        return EXIT_REFUSED
    if len(named) > 1:
        print(f"project-status refused — {len(named)} projects are named exactly {name!r}")
        return EXIT_REFUSED
    statuses = (found.get("projectStatuses") or {}).get("nodes") or []
    target = (next((s for s in statuses if s.get("name") == status), None)
              or next((s for s in statuses if s.get("type") == status.lower()), None))
    if target is None:
        print(f"project-status refused — the workspace has no {status!r} project status")
        return EXIT_REFUSED
    project = named[0]
    try:
        done = linear_ops.gql(_PROJECT_UPDATE, {"id": project["id"], "statusId": target["id"]})
    except linear_ops.LinearError as exc:
        print(f"project-status refused by Linear — {exc}")
        return EXIT_REFUSED
    if not ((done or {}).get("projectUpdate") or {}).get("success"):
        print(f"project-status refused by Linear — the projectUpdate answered {done!r}")
        return EXIT_REFUSED
    try:
        back = linear_ops.gql(_PROJECT_QUERY, {"id": project["id"]})
    except linear_ops.LinearError as exc:
        print(f"project-status set, and its read-back failed — {exc}")
        return EXIT_REFUSED
    read = (((back or {}).get("project") or {}).get("status") or {}).get("name") or "unread"
    print(f"project {name!r} status read back: {read}")
    return EXIT_OK if read == target.get("name") else EXIT_REFUSED


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _cmd_read(args) -> int:
    if board_snapshot.inside_repo(args.out):
        print(f"ERROR: {args.out} is inside this repository — a card file is written to "
              "the runner's temp directory and never committed", file=sys.stderr)
        return EXIT_INSIDE_REPO
    read_card(args.card, args.out)
    return EXIT_OK


def _cmd_check(args) -> int:
    try:
        plan = _read_json(args.plan)
    except (OSError, ValueError) as exc:
        plan = None
        unreadable = [f"plan: {args.plan} is not readable JSON ({exc.__class__.__name__})"]
    else:
        unreadable = []
    try:
        with open(args.card, encoding="utf-8") as fh:
            card_md = fh.read()
    except OSError as exc:
        unreadable.append(f"plan: the card file {args.card} is not readable "
                          f"({exc.__class__.__name__})")
    if unreadable:
        checked = {"ok": False, "kind": "", "repo": None, "command_lines": []}
        refusals = unreadable
    else:
        checked, refusals = check(args.card_id, plan, card_md)
    _write_json(args.out, checked)
    if not refusals:
        return EXIT_OK
    for refusal in refusals:
        _say(args.card_id, _refusal_line(refusal))
    transcript = args.transcript or os.path.join(os.path.dirname(args.out), "transcript.json")
    _write_json(transcript, _transcript(args.card_id, checked["kind"],
                                        [_refusal_entry("check", "\n".join(refusals))],
                                        failed=True))
    return EXIT_REFUSED


def _cmd_run(args) -> int:
    try:
        plan = _read_json(args.plan)
    except (OSError, ValueError) as exc:
        why = f"{args.plan} is not readable JSON ({exc.__class__.__name__})"
        _say(args.card, f"do refused — run · {why}")
        transcript = _transcript(args.card, "", [_refusal_entry("run", why)], failed=True)
    else:
        transcript = run(args.card, plan, dry_run=args.dry_run)
    try:
        _write_json(args.out, transcript)
    except OSError as exc:
        print(f"ERROR: the transcript could not be written to {args.out} ({exc})",
              file=sys.stderr)
        return EXIT_NO_TRANSCRIPT
    return EXIT_OK


def _cmd_probe(_args) -> int:
    probe()
    return EXIT_OK


def _cmd_project_status(args) -> int:
    return project_status(args.project, args.status)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("read", help="write the card file the model reads; only reads Linear")
    r.add_argument("card")
    r.add_argument("--out", required=True)
    r.set_defaults(fn=_cmd_read)

    c = sub.add_parser("check", help="check the plan against the card; runs nothing")
    c.add_argument("card_id", metavar="card")
    c.add_argument("--plan", required=True)
    c.add_argument("--card", required=True, help="the card file `read` wrote")
    c.add_argument("--out", required=True)
    c.add_argument("--transcript", default=None,
                   help="where a refusal's transcript goes; transcript.json beside --out")
    c.set_defaults(fn=_cmd_check)

    u = sub.add_parser("run", help="run the plan's commands, or with --dry-run print them")
    u.add_argument("card")
    u.add_argument("--plan", required=True)
    u.add_argument("--out", required=True)
    u.add_argument("--dry-run", action="store_true")
    u.set_defaults(fn=_cmd_run)

    p = sub.add_parser("probe", help="read each credential's grants; writes nothing")
    p.set_defaults(fn=_cmd_probe)

    lin = sub.add_parser("linear", help="the one Linear command an op renders to")
    lin_sub = lin.add_subparsers(dest="linear_cmd", required=True)
    ps = lin_sub.add_parser("project-status", help="set one project's status by its exact name")
    ps.add_argument("project")
    ps.add_argument("status")
    ps.set_defaults(fn=_cmd_project_status)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
