"""Is the sandbox alive, or is the harness waiting on a corpse? (DRE-3076)

Every scenario wait is a bet that the sandbox is merely SLOW. On 2026-09-03
that bet was wrong for three hours: main's harness run sat on *Run harness
scenarios* from 20:07 PT while the sandbox's own reconcile sweep had died at
20:27 on `Linear API returned 400 … rate limited: 2500 requests/hour
exhausted`. The scenario waiting on that sweep had no way to tell the two
apart, so its only exit was the job's `timeout-minutes: 180` — and the release
channel sat 50 commits behind until an operator killed the run by hand.

This module answers the one question that separates them: **did the sandbox's
own machinery just fail?** A scenario asks it when a wait passes its deadline
(`framework.HarnessContext.wait`), and a YES ends the run at once with the
sandbox's own words quoted, so the promote receipt can say *blocked by
sandbox* rather than *harness failed* (`promote_channel.BLOCKED_MARKER`).

Three rules, each of which is a test:

  * **Only the machinery counts.** A red product-CI run in the sandbox is
    ordinary — `gate_paths` deliberately drives red checks. The sweep, the
    merge gate and linear-sync are the machinery the scenarios wait ON.
  * **Only the MOST RECENT run of each counts.** A sweep that failed and then
    recovered is a healthy sandbox.
  * **Unknown is never dead.** A listing we cannot read, a log GitHub will not
    serve, a cancelled run — none of those block anything. Inventing a block
    from missing data would fail every harness run on a GitHub blip, which is
    the same outage wearing the opposite hat.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

import medic_classify
import promote_channel

#: The sandbox workflows whose failure means the machinery itself has stopped —
#: the sweep, the gate, the Linear sync. Matched on the workflow FILE, so a
#: renamed run title cannot silently empty this set; the `self-` prefix this
#: repo puts on its own dogfooded stubs is tolerated.
MACHINERY_STEMS = ("reconcile", "merge-gate", "linear-sync")

#: Conclusions that mean "this run failed". `cancelled` is deliberately absent:
#: a cancelled sweep proved nothing either way, and unknown is never dead.
FAILED_CONCLUSIONS = ("failure", "timed_out", "startup_failure")

#: GitHub silently truncates a commit-status description at 140 characters, and
#: the receipt has to survive that clamp still carrying its marker.
RECEIPT_LIMIT = 140

#: How much of the sandbox's own error line the full quote carries. The receipt
#: is clamped harder; the run log keeps the long form.
QUOTE_TEXT_LIMIT = 300

# The line every failed Actions run ends with. It is what the operator already
# knew, so it is never the quote when anything more specific is present.
_GENERIC_ERROR = re.compile(r"^##\[error\]Process completed with exit code \d+")

# What a line worth quoting looks like. Ordered scan over the log's tail: the
# FIRST line carrying one of these is the cause, and the generic exit line that
# follows it is the consequence.
_SIGNALS = (
    re.compile(r"\brate limited\b", re.I),
    re.compile(r"\bRATELIMITED\b"),
    re.compile(r"\breturned \d{3}\b"),
    re.compile(r"^Traceback \(most recent call last\)"),
    re.compile(r"##\[error\]"),
    re.compile(r"\b(error|failed|failure)\b\s*:", re.I),
)

# `gh run view --log` prefixes each line with `job\tstep\t`; the raw archive
# prefixes with an ISO-8601 timestamp. Both are noise in a receipt.
_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z?\s+")

# The log is not ours: it is whatever the sandbox printed. It reaches a shell,
# a `key=value` output file and a GitHub API field, so every control character
# — a newline above all, which in that file would be a SECOND key — is dropped
# rather than escaped.
_UNPRINTABLE = re.compile(r"[\x00-\x1f\x7f-\x9f]+")

# How far back in a run's log to look. The cause is near the end; scanning the
# whole thing invites an earlier, unrelated line.
_TAIL_LINES = 400

# The raw payload `linear_ops` appends after it has already NAMED the condition
# (DRE-2923: the body is captured so the medic's classifier can read it). The
# classifier reads the whole log and is untouched by this; a receipt gets the
# sentence, not the JSON.
_BODY_DUMP = re.compile(r"\s+[—-]\s+body:\s.*$")


def workflow_stem(run: dict) -> str:
    """The workflow file's stem, `self-` stripped — `reconcile`, `merge-gate`."""
    path = (run or {}).get("path") or ""
    stem = path.rsplit("/", 1)[-1]
    for suffix in (".yml", ".yaml"):
        stem = stem.removesuffix(suffix)
    return stem.removeprefix("self-")


def is_machinery(run: dict) -> bool:
    return workflow_stem(run) in MACHINERY_STEMS


def error_summary(log_text: str | None) -> Optional[str]:
    """The one line from a failed run's log worth putting in a receipt.

    `Process completed with exit code 75` is what the run already said. The
    line above it — `reconcile: Linear API returned 400 … rate limited: 2500
    requests/hour exhausted` — is the one that would have saved three hours.
    """
    lines = [_clean(line) for line in (log_text or "").splitlines()[-_TAIL_LINES:]]
    fallback = None
    for line in lines:
        if not line:
            continue
        if _GENERIC_ERROR.match(line):
            fallback = fallback or line.removeprefix("##[error]")
            continue
        if any(signal.search(line) for signal in _SIGNALS):
            named = _BODY_DUMP.sub("", line.replace("##[error]", ""))
            return named[:QUOTE_TEXT_LIMIT]
    return fallback[:QUOTE_TEXT_LIMIT] if fallback else None


def _clean(line: str) -> str:
    """Strip the log's own columns: `job\\tstep\\t` then the ISO timestamp."""
    text = (line or "").split("\t")[-1]
    return _TIMESTAMP.sub("", text).strip()


def receipt_line(quote: str) -> str:
    """`quote`, made safe to carry through a `key=value` output file, a shell
    variable and GitHub's 140-character status description.

    The text originates in a sandbox log, so it is sanitised rather than
    trusted: one line, printable characters only, clamped with the marker
    intact at the front (a clamped receipt must still be recognisable to
    `promote_channel.evaluate`).
    """
    flat = _UNPRINTABLE.sub(" ", (quote or "").replace("\t", " "))
    flat = " ".join(flat.split())
    if len(flat) <= RECEIPT_LIMIT:
        return flat
    # Elide the MIDDLE, not the tail. Both ends are load-bearing and the middle
    # is not: the head carries the marker the promote receipt is recognised by,
    # and the CAUSE is at the end — a tail clamp of the 2026-09-03 quote kept
    # "harness blocked: sandbox Reconcile (reusable) failure at 2026-09-03T20:2"
    # and threw away "rate limited: 2500 requests/hour exhausted", which is the
    # only part anyone reads the receipt for.
    keep = RECEIPT_LIMIT - 1
    head = flat[: keep // 2].rstrip()
    tail = flat[-(keep - len(head)):].lstrip()
    return f"{head}…{tail}"


@dataclass
class SandboxFailure:
    """The sandbox's own last word, ready to quote."""

    workflow: str
    run_id: object
    url: str
    when: str
    conclusion: str
    text: Optional[str] = None
    classification: Optional[str] = None

    def quote(self) -> str:
        """The blocked receipt, marker first — the string the driver prints,
        writes to its `blocked_reason` output, and `promote_channel` reads."""
        head = (
            f"{promote_channel.BLOCKED_MARKER} sandbox {self.workflow} "
            f"{self.conclusion} at {self.when}"
        )
        if self.classification and self.classification != "normal":
            head += f" ({self.classification})"
        return f"{head}: {self.text or self.url}"


def machinery_runs(gh, repo: str, log: Callable = print) -> Optional[list]:
    """The sandbox's completed workflow runs, or None when unreadable.

    The None/[] distinction is what lets `probe` fall through to another
    identity: reading Actions runs needs `actions: read`, and a client that
    cannot see the listing must not be mistaken for a healthy sandbox.
    """
    try:
        return list(gh.list_workflow_runs(repo) or [])
    except Exception as e:  # a probe must never be the thing that fails a run
        log(f"sandbox probe: could not list {repo} workflow runs ({e})")
        return None


def latest_failure(gh, repo: str, log: Callable = print) -> Optional[SandboxFailure]:
    """The sandbox's most recent FAILED machinery run, or None.

    None means "no evidence the sandbox is dead" and covers both a healthy
    sandbox and an unreadable one — the caller keeps waiting either way.
    """
    runs = machinery_runs(gh, repo, log=log)
    if runs is None:
        return None
    return failure_in(runs, gh, repo, log=log)


def failure_in(runs, gh, repo: str, log: Callable = print) -> Optional[SandboxFailure]:
    """`latest_failure`'s judgement over an ALREADY-READ listing, so the probe
    never pays for the same page twice."""
    newest: dict[str, dict] = {}
    for run in runs or ():
        if not isinstance(run, dict) or not is_machinery(run):
            continue
        # The listing is newest-first, so the first sighting of a workflow is
        # its most recent run — the only one that says anything about NOW.
        newest.setdefault(workflow_stem(run), run)

    failed = [
        run for run in newest.values()
        if (run.get("conclusion") or "") in FAILED_CONCLUSIONS
    ]
    if not failed:
        return None
    run = max(failed, key=lambda r: r.get("updated_at") or "")

    text, classification = None, None
    try:
        log_text = gh.run_log_text(repo, run.get("id"))
    except Exception as e:
        log(f"sandbox probe: could not read run {run.get('id')} logs ({e})")
        log_text = None
    if log_text:
        text = error_summary(log_text)
        classification = medic_classify.classify(run.get("name") or "", log_text)

    return SandboxFailure(
        workflow=run.get("name") or workflow_stem(run),
        run_id=run.get("id"),
        url=run.get("html_url") or "",
        when=run.get("updated_at") or "an unknown time",
        conclusion=run.get("conclusion") or "failed",
        text=text,
        classification=classification,
    )


def probe(clients, repo: str, log: Callable = print) -> Callable:
    """The callable `HarnessContext.sandbox_probe` holds.

    `clients` are tried in order — reading Actions runs needs `actions: read`,
    which is not the same grant as the checks read the qa App is proven for, so
    a client that cannot see the listing falls through to the next rather than
    turning a slow sandbox into a blocked one.

    Returns a function `(description, elapsed) -> quote | None`.
    """
    usable = [c for c in clients if c is not None]

    def ask(description: str, elapsed: float) -> Optional[str]:
        log(
            f"sandbox probe: {elapsed:.0f}s waiting for {description} — "
            f"checking whether {repo}'s own machinery is alive"
        )
        for client in usable:
            # The first identity that can READ the listing answers; a later one
            # would only re-read the same records with a different token.
            runs = machinery_runs(client, repo, log=log)
            if runs is None:
                continue
            failure = failure_in(runs, client, repo, log=log)
            if failure:
                log(f"sandbox probe: {failure.quote()}")
                return failure.quote()
            return None
        # Loud on purpose (Q1/Q2 of the vendor premortem): reading Actions
        # runs is an `actions: read` grant, which is not the checks grant the
        # qa App is proven for. If neither installation has it the fail-fast
        # is inert — degraded, never wrong — and the ONLY way anyone finds out
        # is an annotation on the first run that needed it.
        log("::warning::sandbox probe: no identity could read "
            f"{repo}'s workflow runs — the sandbox is UNKNOWN, not dead, so "
            "this wait runs its full budget (needs `actions: read`)")
        return None

    return ask
