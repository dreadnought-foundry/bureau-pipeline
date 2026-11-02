#!/usr/bin/env python3
"""No automatic re-run goes back onto the runner service a job just failed on
(DRE-8817). Stdlib only, and no network: the reads are the callers'.

THE RULE, the CEO's of 2026-11-01: when a job fails or hangs, the pipeline
does not just retry it on the same machine. A retry runs on the NEXT service
in the failover order, never the one that just failed it. This module is the
half of that rule this repository can enforce: a refusal. None of the three
retry paths — `canceled_check` through the sweep, `hung_job` and the medic's
`decide` — re-runs a job while the column that job reads still names the
service it just had a hang, an out-of-memory kill or a lost runner on. Moving
the column is the console's (agent-bureau DRE-8816).

THE INCIDENT. agent-bureau #4499, CI run 41379620566: `Console backend shard
3` hung to its 40-minute cap on a RunsOn runner (attempt 1, 6:48-7:33 PM PT).
The canceled-check re-run sent it to studio-1 runner
`bureau-studio-sDcV8SdrTXe9n` (attempt 2, 8:05-8:50 PM PT), where it hung for
another 40 minutes. Every re-run path issues `rerun-failed-jobs`, and GitHub
evaluates a job's `runs-on` again at re-run time from the same workflow file
and the repository's current variables — so the re-run lands wherever the
console's order rests at that moment, and at 7:47 PM PT it had put the CI
column back on mac-studio.

THE COLUMNS. A `runs-on:` line in this fleet reads one of three chains
(`CHAINS`, the README's "The three runner lanes"). `column_of` reads which one
a run's workflow file reads: the column only when the file has at least one
`runs-on:` line, every one is that same column's line, and the file calls no
reusable workflow. Anything else — a literal label, a matrix value, two
columns in one file, a reusable call, no `runs-on:` at all — is None, which is
not a hold: the job reads no column the order moves, or one this rule cannot
attribute, so the path re-runs exactly as before and says why.

THE SERVICES. `service_of` names the service a job ran on off its Jobs API
record; `services_named` names the services a column's effective value, a
JSON label list, can land a job on. Anything neither places is `unknown`, and
`unknown` holds.

THE BAD ENDS (`bad_end`), the three DRE-8816 counts, read with readers this
repository already has: a hang (a job concluded `cancelled` whose annotations
name its own time limit, `canceled_check.limit`, or one `hung_job` judged
hung); an out-of-memory kill (`out_of_memory.from_log`); a lost runner
(GitHub's "lost communication with the server", the wording
`fix_dead_run.py` and `red_main_repair.py` match). A failed job with none of
them changes nothing: this rule only ever holds a re-run, never adds one.

`gate(failed_jobs, column, columns)` holds the re-run when any failed or
canceled job with a bad end ran on an `unknown` service, or the column's
value names `unknown`, or that value's services include the job's.

TWO GAPS STAY OPEN. The callers read the three columns when their job starts
(env vars on the sweep step, the hung-jobs step and the medic's gate step),
so a console move in the minutes between that read and the re-run is not
seen. And the workflow file is read at the run's `head_sha`, so a pull
request whose base changed that same file since is read at the head's text.
"""

from __future__ import annotations

import json
import re
from typing import Iterable, NamedTuple

import canceled_check
import out_of_memory

#: The three `runs-on:` chains, byte for byte as a workflow writes them
#: (`tests/test_runs_on_switchable.py` pins the same three).
CHAINS = {
    "ci": "${{ fromJSON(vars.BUREAU_CI_RUNS_ON || vars.BUREAU_RUNS_ON || "
          "'[\"ubuntu-latest\"]') }}",
    "short": "${{ fromJSON(vars.BUREAU_SHORT_RUNS_ON || vars.BUREAU_RUNS_ON || "
             "'[\"ubuntu-latest\"]') }}",
    "long": "${{ fromJSON(vars.BUREAU_RUNS_ON || '[\"ubuntu-latest\"]') }}",
}

#: The env var each caller hands a column's effective value in.
ENV = {"ci": "CI_RUNS_ON", "short": "SHORT_RUNS_ON", "long": "LONG_RUNS_ON"}

RUNS_ON = "runs-on"
MAC_STUDIO = "mac-studio"
MAC_MINI = "mac-mini"
GITHUB_HOSTED = "github-hosted"
UNKNOWN = "unknown"

HANG = "hang"
OUT_OF_MEMORY = "out-of-memory kill"
LOST_RUNNER = "lost runner"

#: The conclusions of a job a re-run would run again.
FAILED = ("failure", "cancelled", "timed_out")

#: GitHub's annotation for a self-hosted runner that went away mid-job.
LOST_WORDING = "lost communication with the server"

_RUNS_ON_LINE = re.compile(r"^\s*(?:-\s+)?runs-on:(?P<value>.*)$", re.MULTILINE)
_REUSABLE_CALL = re.compile(
    r"^\s*(?:-\s+)?uses:\s*['\"]?[^\s'\"#]*\.github/workflows/", re.MULTILINE)
_COMMENT = re.compile(r"\s+#.*$")
_NORMAL = {column: re.sub(r"\s+", "", chain) for column, chain in CHAINS.items()}
_HOSTED_PREFIXES = ("ubuntu-", "macos-", "windows-")


class Gate(NamedTuple):
    """`allow` — True, False (held), or None when a read it needed failed;
    `service` — the service the deciding job ran on, or ""; `why`."""

    allow: bool | None
    service: str
    why: str


def unread(why: str) -> Gate:
    """The answer for a gate whose reads did not all answer: neither allowed
    nor held, and the caller decides what that means (DRE-8817 section 3)."""
    return Gate(None, "", why)


# --- the column a run's jobs read ---------------------------------------------

def read_column(workflow_text) -> tuple:
    """(the column every `runs-on:` line of the file reads, or None; why)."""
    text = str(workflow_text or "")
    if _REUSABLE_CALL.search(text):
        return None, "it calls a reusable workflow, whose jobs' runners this rule cannot read"
    values = [_COMMENT.sub("", m.group("value")).strip()
              for m in _RUNS_ON_LINE.finditer(text)]
    if not values:
        return None, "it has no runs-on line"
    found = set()
    for value in values:
        normal = re.sub(r"\s+", "", value)
        column = next((c for c, chain in _NORMAL.items() if normal == chain), None)
        if column is None:
            return None, f"its runs-on {value or '(a block)'} reads none of the three columns"
        found.add(column)
    if len(found) > 1:
        return None, f"its jobs read two or more columns ({', '.join(sorted(found))})"
    (column,) = found
    return column, f"every runs-on line reads the `{column}` column"


def column_of(workflow_text) -> str | None:
    """`ci`, `short` or `long` — the column a run's workflow file reads — or
    None (see the module docstring)."""
    return read_column(workflow_text)[0]


def columns_from_env(env) -> dict:
    """The three columns' effective values as the calling step hands them."""
    return {column: str(env.get(name) or "") for column, name in ENV.items()}


# --- services -----------------------------------------------------------------

def _is_runs_on_label(label: str) -> bool:
    return label == RUNS_ON or label.startswith("runs-on=")


def service_of(job: dict) -> str:
    """The service a job ran on, off its Jobs API record. First match wins."""
    job = job if isinstance(job, dict) else {}
    name = str(job.get("runner_name") or "").strip()
    group = str(job.get("runner_group_name") or "").strip()
    labels = [str(label) for label in job.get("labels") or () if label is not None]
    if group == "RunsOn" or name.startswith("runs-on-") or any(
            _is_runs_on_label(label) for label in labels):
        return RUNS_ON
    if name.startswith("bureau-studio-"):
        return MAC_STUDIO
    if name.startswith("bureau-mini-"):
        return MAC_MINI
    if name.startswith("GitHub Actions"):
        return GITHUB_HOSTED
    return UNKNOWN


def services_named(value) -> frozenset:
    """The services a column's effective value — a JSON label list — names."""
    try:
        labels = json.loads(str(value or ""))
    except ValueError:
        return frozenset({UNKNOWN})
    if (not isinstance(labels, list) or not labels
            or not all(isinstance(label, str) for label in labels)):
        return frozenset({UNKNOWN})
    if any(_is_runs_on_label(label) for label in labels):
        return frozenset({RUNS_ON})
    if "self-hosted" in labels:
        if any(label == MAC_STUDIO or label.startswith("machine-studio") for label in labels):
            return frozenset({MAC_STUDIO})
        if any(label == MAC_MINI or label.startswith("machine-mini") for label in labels):
            return frozenset({MAC_MINI})
        return frozenset({MAC_MINI, MAC_STUDIO})
    if all(label.startswith(_HOSTED_PREFIXES) for label in labels):
        return frozenset({GITHUB_HOSTED})
    return frozenset({UNKNOWN})


# --- bad ends -----------------------------------------------------------------

def bad_end(job: dict, annotations=None, log_text=None) -> str | None:
    """`HANG`, `OUT_OF_MEMORY` or `LOST_RUNNER` for a failed job, or None.
    `annotations` is its check run's annotations, `log_text` its log."""
    notes = [note for note in annotations or () if isinstance(note, dict)]
    if (job or {}).get("conclusion") == canceled_check.CANCELLED and \
            canceled_check.limit(notes) is not None:
        return HANG
    if any(LOST_WORDING in str(note.get("message") or "").lower() for note in notes):
        return LOST_RUNNER
    if log_text and out_of_memory.from_log(log_text) is not None:
        return OUT_OF_MEMORY
    return None


# --- the decision -------------------------------------------------------------

def _named(job: dict) -> str:
    return str(job.get("name") or f"job {job.get('id')}")


def _services(services) -> str:
    return " and ".join(sorted(services))


def gate(failed_jobs: Iterable, column: str | None, columns: dict) -> Gate:
    """Held or allowed. `failed_jobs` is (job record, bad end or None) for
    each failed or canceled job; `column` the run's column (`column_of`, or a
    path's constant); `columns` maps `ci`, `short` and `long` to their
    effective values."""
    if column is None:
        return Gate(True, "", "the run reads no single runner column, so it re-runs as before")
    value = str((columns or {}).get(column) or "")
    named = services_named(value)
    first = None
    for job, end in failed_jobs or ():
        if end is None:
            continue
        job = job if isinstance(job, dict) else {}
        service = service_of(job)
        first = first or (job, end, service)
        runner = str(job.get("runner_name") or "").strip() or "(no runner name)"
        said = (f"`{_named(job)}` had a {end} on runner `{runner}` (service "
                f"{service}); the `{column}` column reads `{value or '(empty)'}`, "
                f"which names {_services(named)}")
        if service == UNKNOWN:
            return Gate(False, service, f"{said} — the job's runner service is unknown")
        if UNKNOWN in named:
            return Gate(False, service, f"{said} — the column's services are unknown")
        if service in named:
            return Gate(False, service,
                        f"{said} — still the service it just failed on")
    if first is None:
        return Gate(True, "", "no failed job had a hang, an out-of-memory kill or a "
                              "lost runner")
    job, end, service = first
    return Gate(True, service,
                f"`{_named(job)}` had a {end} on {service}, and the `{column}` "
                f"column now reads `{value}`, which names {_services(named)}")
