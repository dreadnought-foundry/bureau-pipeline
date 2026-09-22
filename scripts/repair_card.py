#!/usr/bin/env python3
"""The card a Red-Main Repair files before it starts (DRE-3533, stdlib only).

Everything the board tracks has a card. Repair pull requests did not: the loop
opened its PR on `repair/<failing-sha>`, a ref with no card id — and the card id
in the head ref is what the rest of the pipeline reads. `linear-sync` closes a
card on merge by finding `DRE-<n>` there; the console shows a pull request's
card the same way. So a repair PR showed no card, closed no card, and nothing on
the board recorded that the repair had happened (PR #340, 2026-09-09: a real fix
for a real outage, invisible to the board, its bookkeeping filed by hand).

`red-main-repair.yml` calls this between the dispatch decision and the agent:

    repair_card.py open --repo <slug> --head-sha <sha> --workflow-name <name> \\
        --run-url <url> --attempt <n> --fallback-branch <repair/<sha>>

It prints `card=`, `card_url=`, `branch=` and `card_owed=` for `$GITHUB_OUTPUT`,
and the branch it prints is the one the agent creates and the Report step looks
for.

The second subcommand is `settle` (DRE-4411), which the reconcile sweep runs
once per pass — see the `settle` section below. `open` files a card with
exactly one way to close, and `settle` is every other ending.

Three properties, in the order they matter:

  * **A Linear failure never blocks a repair.** Main is red; the bookkeeping is
    worth less than the fix. Every failure path here returns the CALLER's
    fallback branch with `card_owed=true` and exit 0 — the PR opens on the old
    cardless shape and says the card is owed. Nothing in this file raises.
  * **One card per failing commit.** The title is anchored on the workflow and
    the abbreviated sha — never the run id, because attempt 2 is a DIFFERENT
    failed run at the SAME commit and must find the first attempt's card
    instead of minting a second.
  * **The lane is a working lane, never Todo.** A card entering Todo is
    dispatched by the relay, which would put a second agent on work an agent is
    already doing. The repair is in flight before this card exists, so the card
    is filed where the work actually is, and the routing verdict — stamped
    through `routing_verdict.stamp_card`, the ONE writer of that vocabulary —
    says the fleet has nothing to dispatch for it.

## settle — the endings that are not a merge (DRE-4411)

The card `open` files promises *"closes when that pull request merges."* Three
endings have no pull request at all: the failure was a flake and a re-run
passed, another merge turned main green before the repair finished, or the
repair run died without opening one. `hand-built` makes the stranded-card
watchdog stay quiet by design, so nothing moved those cards and a person
cancelled them: of the last seventeen on 2026-09-20, **nine by hand**, none
closed by the pipeline for any reason other than a merge.

`settle` is the other endings. Once per reconcile pass, for every open card
this module filed — found by `TITLE_ANCHOR`, the anchor `card_title` itself is
built from, never by a list somebody keeps — it reads the repo, the workflow
name and the failing sha back off the card and decides, IN THIS ORDER:

  1. a repair pull request is OPEN for that commit (either branch shape) →
     leave the card alone; the merge closes it;
  2. main is green for that workflow — the newest COMPLETED run of the named
     workflow on the default branch concluded `success` — → **Cancel**, with
     one plain-English comment naming the run that proves it. Canceled, never
     Done: nothing was delivered under the card, and a blocker clears at
     Canceled;
  3. main is still red and no pull request is open → leave it. That is the
     existing "the repair budget is spent, a human needs to look" path in
     `red-main-repair.yml`, and this does not touch it;
  4. GitHub or Linear could not be read → **UNKNOWN**: do nothing, and say so
     with the card id. An unreadable answer is never "green".

Both seams arrive as `ops`, so the sweep hands in its own board snapshot and
its own `gh` helpers and a test hands in a fake. The decision is here; nothing
in this module knows how a run listing is fetched.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404 — the gh CLI, fixed args, shell=False
import sys
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import github_output  # noqa: E402
import red_main_repair  # noqa: E402

#: Where the card lands. Never Todo — see the module docstring.
LANE = "In Progress"

#: The verdict the fleet's promoter reads: nothing to dispatch here. The
#: repair's OWN run is the work, and it is already running.
VERDICT = "WORKBENCH"

#: Marks and filters every repair card carries beside its `repo:` label and its
#: role. `hand-built` is what tells the stranded-card watchdog that no
#: dispatched run is coming for this card (the repair run is not one of its
#: dispatches), and `Bug` is what a red main is.
FIXED_LABELS = ("initiative:bureau", "Bug", "hand-built")

#: The repairer is an engineer-tier fixer (config/models.yaml puts it on the
#: workhorse ladder for exactly that reason), so the card carries the engineer
#: role wherever it is filed.
ROLE_LABEL = "agent:engineer"


class _Ops:
    """The Linear seam, in one object so the caller can hand a fake in.

    Imported lazily: this module's pure half (title, labels, body, branch) is
    read by tests and by the workflow's own dry paths without a Linear key.
    """

    def find_open(self, title):
        import linear_ops

        return linear_ops.find_open(title)

    def create_card(self, title, description, *, repo_slug, labels=(),
                    lane="Planning"):
        import linear_ops

        return linear_ops.create_card(title, description, repo_slug=repo_slug,
                                      labels=labels, lane=lane)

    def cmd_comment(self, identifier, body, *flags):
        import linear_ops

        return linear_ops.cmd_comment(identifier, body, *flags)

    def stamp_card(self, identifier, name, why):
        import routing_verdict

        return routing_verdict.stamp_card(identifier, name, why)


#: The stable half of every repair card's title, and the ONE anchor `settle`
#: finds its own cards by (DRE-4411). `card_title` is built from it rather than
#: repeating it, so a reworded title moves the anchor with it and the sweep
#: cannot silently stop finding the cards this module files — which is the
#: failure that would leave the whole of `settle` running and reporting nothing.
TITLE_ANCHOR = "Red main repaired — "

#: What separates the workflow name from the abbreviated sha in the title, so
#: `card_workflow` reads back exactly what `card_title` wrote.
_TITLE_JOIN = " failed at "


def card_title(workflow_name: str, head_sha: str) -> str:
    """The card's title — the workflow that went red and the commit it went red
    at, abbreviated. Deliberately NOT the run id: attempt 2 arrives from a
    different failed run at the same commit and finds this card by exact
    title."""
    return (
        f"{TITLE_ANCHOR}{workflow_name or 'CI'}{_TITLE_JOIN}"
        f"{head_sha[:red_main_repair.SHA_CHARS]}"
    )


def is_repair_card(title: str) -> bool:
    """Is this one of the cards this module files? The whole of `settle`'s
    discovery — a title test, never a list of card ids."""
    return (title or "").startswith(TITLE_ANCHOR)


def card_workflow(title: str) -> str:
    """The workflow name back off the card's own title, or "".

    `rpartition` on purpose: a workflow may legitimately be named "CI — failed
    at load", and the LAST join is the one `card_title` wrote.
    """
    if not is_repair_card(title):
        return ""
    name, joined, _ = title[len(TITLE_ANCHOR):].rpartition(_TITLE_JOIN)
    return name if joined else ""


#: The failing commit as `card_body` writes it — the FULL sha, which the title
#: only carries abbreviated. The body is where the whole answer lives.
_BODY_SHA = re.compile(r"failed at commit `([0-9a-f]{7,40})`")


def card_failing_sha(description: str) -> str | None:
    """The full failing sha off the card's body, or None when it is not there.

    None is a real answer and it is an UNKNOWN, never a green: a body somebody
    rewrote is a card `settle` cannot reason about, and it is left alone.
    """
    found = _BODY_SHA.search(description or "")
    return found.group(1) if found else None


def card_labels(repo_slug: str) -> list[str]:
    """Every label the card carries, `repo:<slug>` first — the whole answer in
    one place, so a reader never has to know that `create_card` prepends the
    repo label itself and deduplicates by name."""
    return [f"repo:{repo_slug}", *FIXED_LABELS, ROLE_LABEL]


def card_body(*, workflow_name: str, run_url: str, head_sha: str,
              repo_slug: str, attempt: int) -> str:
    """The card, in the plain English a person reads on the board."""
    return "\n".join([
        f"**Repo:** {repo_slug}",
        "",
        "## What happened",
        "",
        f"CI went red on this repo's main branch: **{workflow_name or 'CI'}** "
        f"failed at commit `{head_sha}`. The Red-Main Repair loop is fixing it "
        "forward on its own branch, through the ordinary pull request the "
        "critic reviews and the merge gate merges — nobody merges it by hand "
        "(adr-red-main-auto-repair).",
        "",
        f"- The failed run: {run_url}",
        f"- Repair attempt {attempt} of 2 for this commit.",
        "",
        "This card exists so the repair is on the board like everything else: "
        "it opens with the work, carries the pull request, and closes when that "
        "pull request merges.",
        "",
        "## Acceptance criteria",
        "",
        "- [ ] The repair pull request merges and main is green again.",
        "- [ ] Closed by the merge, through the card id in the branch name.",
        "",
        "## Not in this card",
        "",
        "- Any other failure at any other commit — each red main gets its own "
        "card.",
        "- A third repair attempt: after two, the loop stops and raises a card "
        "asking for a human.",
    ]) + "\n"


def verdict_why(run_url: str, attempt: int) -> str:
    """Why this card is not the fleet's to dispatch, in one sentence."""
    return (
        f"Filed by the Red-Main Repair loop (run {run_url}) for repair attempt "
        f"{attempt}. The repair agent is ALREADY running and opens the pull "
        "request itself, so there is nothing here to dispatch — the card is "
        "the board's record of a repair in flight."
    )


def attempt_note(run_url: str, attempt: int) -> str:
    """What a reused card is told when a later attempt starts."""
    return (
        f"🔁 Repair attempt {attempt} of 2 at this commit has started — the "
        f"previous attempt's pull request did not merge. Failed run: {run_url}"
    )


def open_card(*, repo_slug: str, workflow_name: str, head_sha: str,
              run_url: str, attempt: int, fallback_branch: str,
              ops=None) -> dict:
    """File (or find) the repair's card and name its branch. NEVER raises.

    Returns `{card, card_url, branch, card_owed, note}`. `card_owed` is the
    honest half: with it true the repair still happens, on `fallback_branch`,
    and the pull request says the card is owed.
    """
    ops = ops or _Ops()
    title = card_title(workflow_name, head_sha)

    try:
        existing = ops.find_open(title)
    except Exception as exc:  # noqa: BLE001 — Linear is never worth a red main
        existing = None
        print(f"repair card: could not search for an existing card ({exc})",
              file=sys.stderr)

    if existing:
        try:
            ops.cmd_comment(existing, attempt_note(run_url, attempt))
        except Exception as exc:  # noqa: BLE001
            print(f"repair card: could not comment on {existing} ({exc})",
                  file=sys.stderr)
        return _found(existing, "", head_sha, attempt)

    try:
        issue = ops.create_card(
            title,
            card_body(workflow_name=workflow_name, run_url=run_url,
                      head_sha=head_sha, repo_slug=repo_slug, attempt=attempt),
            repo_slug=repo_slug,
            labels=card_labels(repo_slug),
            lane=LANE,
        )
    except Exception as exc:  # noqa: BLE001 — the ONE thing that must not fail
        print(f"repair card: NOT filed ({exc}) — the repair proceeds on "
              f"{fallback_branch} and the pull request says the card is owed",
              file=sys.stderr)
        return {"card": "", "card_url": "", "branch": fallback_branch,
                "card_owed": True, "note": str(exc).replace("\n", " ")[:300]}

    card = issue["identifier"]
    try:
        # The one writer of the routing vocabulary — it posts the verdict
        # comment AND applies the marks the verdict declares. A failure here
        # loses a comment, not the card, so the card still names the branch.
        ops.stamp_card(card, VERDICT, verdict_why(run_url, attempt))
    except Exception as exc:  # noqa: BLE001
        print(f"repair card: {card} filed but its routing verdict did not "
              f"stamp ({exc})", file=sys.stderr)
    return _found(card, issue.get("url") or "", head_sha, attempt)


def _found(card: str, url: str, head_sha: str, attempt: int) -> dict:
    return {
        "card": card,
        "card_url": url,
        "branch": red_main_repair.repair_branch(head_sha, attempt, card=card),
        "card_owed": False,
        "note": "",
    }


# --------------------------------------------------------------------------- #
# settle — the endings that are not a merge (DRE-4411)                         #
# --------------------------------------------------------------------------- #

#: The four answers, in the order `settle` asks them. Named so the sweep log,
#: the tests and any later reader all say the same word for the same decision.
SETTLED_PR_OPEN = "pull-request-open"
SETTLED_GREEN = "main-green"
SETTLED_RED = "main-still-red"
SETTLED_UNKNOWN = "unknown"

#: States a card is already finished in. A card in one of them is nobody's to
#: settle — and since the sweep's board read covers the working lanes only, a
#: card that reaches here in one has been read some other way. Checked anyway:
#: "at most one comment per card ever" must not rest on which query found it.
TERMINAL_STATES = ("Done", "Canceled", "Duplicate")

#: The first words of the settle comment, and its idempotency key. A pass that
#: finds this on the card writes nothing — belt to the terminal-state brace
#: above, for the case where the cancel landed and the comment did not.
SETTLE_MARKER = "🧹 Canceled: main is green again"


class Settled(NamedTuple):
    """What `settle` decided about one card, and why, in one row."""

    card: str
    decision: str
    why: str


class SettleReport(NamedTuple):
    """One pass. `failures` is what Linear refused — the caller's to record on
    its own rail; `settle` never decides a run's exit code."""

    decisions: list
    failures: list


def repair_refs(card: str, head_sha: str) -> set:
    """Every head ref that IS a repair attempt at this commit for this card.

    Derived from `red_main_repair.repair_branch`, the one namer, in both
    shapes it produces: `repair/DRE-<n>-<sha12>` (the normal path since
    DRE-3533) and the cardless `repair/<sha>` fallback a repair whose card
    could not be filed rides. Attempts 1 and 2 are the whole budget
    `red_main_repair.decide` allows, so the set is closed.
    """
    return {
        red_main_repair.repair_branch(head_sha, attempt, card=named)
        for attempt in (1, 2)
        for named in (card, None)
    }


def newest_completed_run(runs) -> dict | None:
    """The newest run that has actually FINISHED, or None.

    `runs` is newest-first, the order GitHub's own run listing answers in. A
    queued or in-progress run at the top is NOT an answer — main was neither
    proved green nor proved red by a run that has not finished — so it is
    skipped and the newest completed one below it decides. That ordering is
    also what makes "at that sha or a later one" true without comparing shas:
    the failing run is itself a completed run at the card's sha, so anything
    above it in this listing started later.
    """
    for run in runs or ():
        if (run.get("status") or "") == "completed":
            return run
    return None


def settle_note(workflow_name: str, run: dict) -> str:
    """The ONE comment a settled card gets: what proves main is green, and
    that nothing was delivered under the card."""
    where = run.get("url") or ""
    at = (run.get("headSha") or "")[:red_main_repair.SHA_CHARS]
    proof = f"**{workflow_name or 'CI'}**"
    if at:
        proof += f" at commit `{at}`"
    if where:
        proof += f" — {where}"
    return (
        f"{SETTLE_MARKER}. The newest completed run of {proof} concluded "
        "success on the default branch, so there is nothing left to repair.\n\n"
        "No repair pull request was ever opened against this card, so nothing "
        "was delivered under it and there is nothing to merge. That is why it "
        "is cancelled rather than finished — and cancelling it also clears any "
        "work this card was blocking.\n\n"
        "If the branch goes red again, the repair loop files a fresh card for "
        "that commit."
    )


def _settle_one(card: dict, repo_slug: str, ops, log) -> Settled | None:
    """Decide one card, and act. None when the card is not this pass's."""
    identifier = card.get("identifier") or ""

    repo = card.get("repo")
    if repo and repo != repo_slug:
        # Every repo runs its own sweep against its own GitHub. Settling
        # another repo's card off this repo's runs would read one estate's
        # green main as another's.
        return None
    if (card.get("state") or "") in TERMINAL_STATES:
        return None
    if any(SETTLE_MARKER in body for body in card.get("comments") or ()):
        return None

    workflow_name = card_workflow(card.get("title") or "")
    head_sha = card_failing_sha(card.get("description") or "")
    if not head_sha:
        return _unknown(identifier, "the failing commit is not on the card", log)

    try:
        refs = ops.open_pull_head_refs()
        runs = ops.workflow_runs(workflow_name)
    except Exception as exc:  # noqa: BLE001 — an unreadable read is an UNKNOWN
        return _unknown(identifier, f"GitHub could not be read ({exc})", log)
    if refs is None:
        return _unknown(identifier, "the open pull requests could not be read", log)
    if runs is None:
        return _unknown(
            identifier, f"the runs of {workflow_name or 'CI'} could not be read", log)

    open_repair = sorted(set(refs) & repair_refs(identifier, head_sha))
    if open_repair:
        why = (f"a repair pull request is open on {open_repair[0]} — its merge "
               "closes the card")
        log(f"repair settle: {identifier} left alone — {why}")
        return Settled(identifier, SETTLED_PR_OPEN, why)

    run = newest_completed_run(runs)
    if run is None:
        return _unknown(
            identifier,
            f"no completed run of {workflow_name or 'CI'} on the default "
            "branch — nothing proves this either way",
            log,
        )
    if (run.get("conclusion") or "") != "success":
        why = (
            f"the newest completed run of {workflow_name or 'CI'} on the "
            f"default branch concluded {run.get('conclusion') or 'nothing'} "
            f"({run.get('url') or 'no url'}) — main is still red, and the "
            "repair budget path in red-main-repair.yml owns what happens next"
        )
        log(f"repair settle: {identifier} left alone — {why}")
        return Settled(identifier, SETTLED_RED, why)

    # Cancel FIRST, then say why: a comment claiming a cancellation that
    # Linear refused is the DRE-1254 shape, and the state change is the half
    # the board, the console and the blocker gates actually read.
    ops.cancel(identifier)
    ops.cmd_comment(identifier, settle_note(workflow_name, run))
    why = (f"main is green again — {run.get('url') or 'the newest completed run'}"
           " concluded success, and no repair pull request was ever opened")
    log(f"repair settle: {identifier} Canceled — {why}")
    return Settled(identifier, SETTLED_GREEN, why)


def _unknown(identifier: str, why: str, log) -> Settled:
    """Say what could not be read, with the card id, and touch nothing."""
    log(f"repair settle: UNKNOWN {identifier} — {why}; the card is left "
        "exactly as it is and re-read next sweep")
    return Settled(identifier, SETTLED_UNKNOWN, why)


def settle(*, repo_slug: str, ops, log=print, fatal=()) -> SettleReport:
    """Close every repair card whose repair is over by other means.

    One pass over the cards `ops.repair_cards()` hands back — the sweep's own
    board snapshot, already filtered to this module's title anchor. Every
    decision is logged; nothing is inferred from silence.

    `fatal` is the exceptions that must NOT be swallowed — the sweep passes
    `linear_ops.LinearRateLimited`, which is the run's own exit code and never
    one card's problem (DRE-2923). Everything else Linear refuses is recorded
    in `failures` and the pass carries on to the next card: one card the board
    would not write must not cost the others their reading.
    """
    try:
        cards = list(ops.repair_cards())
    except Exception as exc:  # noqa: BLE001
        if fatal and isinstance(exc, tuple(fatal)):
            raise
        # UNKNOWN, not a failure: an unreadable board is a reading this pass
        # did not get, said out loud, and the next pass asks again. What it is
        # NOT is an empty board — nothing is settled off it (DRE-2034).
        log(f"repair settle: UNKNOWN — the board could not be read ({exc}); "
            "no card is settled this pass")
        return SettleReport([], [])

    decisions: list = []
    failures: list = []
    for card in cards:
        identifier = card.get("identifier") or "?"
        try:
            settled = _settle_one(card, repo_slug, ops, log)
        except Exception as exc:  # noqa: BLE001
            if fatal and isinstance(exc, tuple(fatal)):
                raise
            failures.append(f"repair settle: {identifier}: {exc}")
            log(f"ERROR: repair settle: {identifier} could not be settled: {exc}")
            continue
        if settled is not None:
            decisions.append(settled)
    if cards:
        log(f"repair settle: {len(cards)} repair card(s) read, "
            f"{sum(1 for d in decisions if d.decision == SETTLED_GREEN)} cancelled")
    return SettleReport(decisions, failures)


def _label_repo(card: dict) -> str | None:
    """A card's repo slug off its `repo:<slug>` label — the canonical signal
    (DRE-1879). The sweep reads it through `reconcile.card_repo`, which also
    falls back to the legacy `**Repo:**` stamp; a repair card is minted by
    `card_labels` above and always carries the label."""
    for node in (card.get("labels") or {}).get("nodes") or ():
        name = (node.get("name") or "").lower()
        if name.startswith("repo:"):
            return name[len("repo:"):].rsplit("/", 1)[-1] or None
    return None


class _SettleOps:
    """The default seams for a CLI `settle`: Linear's board and `gh`.

    The sweep hands in its OWN (`reconcile._RepairSettleOps`) so a pass pays
    for one board read and one pull-request listing however many cards it
    settles. This one is for a person running the subcommand by hand.
    """

    #: Open cards whose title starts with the anchor. `first: 50` is a fleet's
    #: whole plausible range of live repair cards — seventeen was the count of
    #: the last SEVENTEEN EVER on 2026-09-20 — and a listing that filled this
    #: would itself be the finding.
    _QUERY = """query($prefix: String!) {
         issues(first: 50, filter: {
           team: {key: {eq: "DRE"}},
           title: {startsWith: $prefix},
           state: {type: {nin: ["completed", "canceled"]}}
         }) { nodes {
           identifier title description
           state { name } labels { nodes { name } }
           %s
         } } }"""

    def __init__(self, github_repo: str, branch: str = "") -> None:
        self.github_repo = github_repo
        self.branch = branch

    def repair_cards(self) -> list:
        import linear_ops

        data = linear_ops.gql(
            self._QUERY % linear_ops.COMMENT_WINDOW_GQL, {"prefix": TITLE_ANCHOR}
        )
        return [
            {
                "identifier": card.get("identifier") or "",
                "title": card.get("title") or "",
                "description": card.get("description") or "",
                "state": ((card.get("state") or {}).get("name") or ""),
                "repo": _label_repo(card),
                "comments": [
                    node.get("body") or ""
                    for node in linear_ops.window_nodes(card.get("comments"))
                ],
            }
            for card in (data.get("issues") or {}).get("nodes") or ()
        ]

    def open_pull_head_refs(self):
        return [
            pr.get("headRefName") or ""
            for pr in self._json(
                "pr", "list", "--repo", self.github_repo, "--state", "open",
                "--limit", "50", "--json", "headRefName",
            )
        ]

    def workflow_runs(self, workflow_name: str):
        args = ["run", "list", "--repo", self.github_repo,
                "--workflow", workflow_name, "--limit", "20",
                "--json", "status,conclusion,headSha,url"]
        if self.branch:
            args += ["--branch", self.branch]
        return self._json(*args)

    def cancel(self, identifier: str) -> None:
        import linear_ops

        linear_ops.cmd_state(identifier, "Canceled")

    # Named for the seam it IS, exactly as `_Ops.cmd_comment` above is: the
    # completeness guard (scripts/check_act_receipts.py) reads a function
    # called `cmd_comment` as a pass-through it cannot judge, and reads its
    # CALLERS instead — which is where the body is actually composed.
    def cmd_comment(self, identifier: str, body: str) -> None:
        import linear_ops

        linear_ops.cmd_comment(identifier, body)

    @staticmethod
    def _json(*args: str):
        # B603/B607: program-constructed args, shell=False, gh resolves via
        # PATH by design — the same call shape reconcile.py makes.
        done = subprocess.run(  # nosec B603 B607
            ["gh", *args], capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise RuntimeError(
                f"gh {' '.join(args)} failed rc={done.returncode}: "
                f"{done.stderr.strip()[:300]}")
        return json.loads(done.stdout or "[]")


def outputs(result: dict) -> str:
    """`$GITHUB_OUTPUT` lines, through the writer that survives a value with
    a newline in it (DRE-4202).

    This used to collapse every value to one line, which made a multi-line
    value safe by squashing it — and left the safety resting on somebody
    remembering to wrap the NEXT key added here. `github_output.render` moves
    that guarantee into the writer: a value that fits on a line still emits as
    plain `key=value`, and one that does not rides a heredoc delimiter it
    cannot close, so the failure note arrives whole instead of leaking into
    another key or killing the step.
    """
    return github_output.render([
        ("card", result["card"]),
        ("card_url", result["card_url"]),
        ("branch", result["branch"]),
        ("card_owed", "true" if result["card_owed"] else "false"),
        ("card_note", result.get("note")),
    ])


def _settle_cli(args) -> int:
    """`settle` by hand. Exit 1 only on a write the board refused — an UNKNOWN
    is a reading this run did not get, said out loud, and not a failure."""
    report = settle(
        repo_slug=args.repo,
        ops=_SettleOps(args.github_repo, args.branch),
    )
    for failure in report.failures:
        print(f"ERROR: {failure}", file=sys.stderr)
    return 1 if report.failures else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("open")
    o.add_argument("--repo", required=True)
    o.add_argument("--head-sha", required=True)
    o.add_argument("--run-url", required=True)
    o.add_argument("--fallback-branch", required=True)
    o.add_argument("--workflow-name", default="")
    o.add_argument("--attempt", type=int, default=1)
    s = sub.add_parser("settle")
    s.add_argument("--repo", required=True, help="the card's repo slug")
    s.add_argument("--github-repo", default=os.environ.get("REPO", ""),
                   help="owner/name; defaults to $REPO")
    s.add_argument("--branch", default="main",
                   help="the default branch whose runs prove main is green")
    args = parser.parse_args(argv)

    if args.cmd == "settle":
        return _settle_cli(args)

    # Stdout is the output FILE for this step. `open_card` talks to Linear,
    # and the Linear seam talks BACK on stdout — `linear_ops.cmd_comment`
    # prints `commented on DRE-4200`, which is the line portico run
    # 35314499681 died on: a card filed, budget spent, main still red, and a
    # death wearing the fingerprint of a dead credential (DRE-4202/DRE-4201).
    # Human text belongs on stderr whoever wrote it.
    with github_output.only_outputs():
        result = open_card(
            repo_slug=args.repo, workflow_name=args.workflow_name,
            head_sha=args.head_sha, run_url=args.run_url, attempt=args.attempt,
            fallback_branch=args.fallback_branch,
        )
    sys.stdout.write(outputs(result))
    print(
        f"repair card: {result['card'] or 'NOT FILED'} → branch "
        f"{result['branch']}", file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
