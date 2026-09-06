#!/usr/bin/env python3
"""The release train's rules — the engine half of DRE-3164 (DRE-3167).

`.github/workflows/release-train.yml` is the ninth reusable workflow in this
repo and contains no product's deploy steps: it gathers GitHub's records and
acts on the verdict this module computes, the `promote_channel.py` /
`release_gate.py` shape. Everything that decides anything is here, so every
rule is testable without a token, a runner or a tag push
(`tests/test_release_train.py`).

WHAT A TRAIN DOES, IN ONE PARAGRAPH. A caller's `.github/bureau/release.json`
declares its surfaces. Every time CI completes on the default branch, on the
07:00 PT schedule, and on a hand dispatch, the train reads that file at the
commit it was handed — the SHA CI ran on for a CI-completion run, the head of
the branch at that moment otherwise. The COLLAPSE rule, stated once here
because it is the only reason two pushes a minute apart produce one release:
the per-surface concurrency lane holds the second run behind the first, and
the second reads current if the first released a commit that already contains
its own. For each declared surface it decides, and where the decision is
`release` it runs the surface's own script under the caller's own identity
and verifies the tag the script cut. **The tag IS the receipt** — deploy-lag
reads the newest tag in each surface's series and the console's Shipped-today
panel reads commit ancestry against it, so nothing here writes a second
record.

THE TRAIN IS NEVER STOPPED (DRE-3263, the CEO's rule of 2026-09-06). If the
commit is ready it goes; if it is not, the train leaves without it and the
next train picks it up. Two things follow. First, only the checks that GATE A
MERGE are checks on the commit — the set the merge gate reads, taken from
`merge_gate.gating_check_runs` and never restated here. A fix agent on an
unrelated PR, the medic, the sweep and the train's own run all report check
runs against the default branch's head because that is the ref GitHub runs
them on; they are ignored by verified origin (the producing workflow's event
and path, from GitHub's own record — never a name, which for every reusable
stub is just `call / <job>`). On 2026-09-06 11:36 PT the console's first
supervised release sat waiting on `call / fix PR #2325` and `call / fix PR
#2328`, and that is the fixture. Second, a PENDING gating check is not waited
for: the surface is a no-op that names it, exit 0, no tag, no alert — and the
trigger that makes this true is CI completing on main (the stub's
`workflow_run` on CI), because a push fires before that commit's CI has
started and every push-run would find it pending.

THE ORDER THE RULES ARE READ, and why it is not the order the card's sentence
lists them in. The brake comes first and CI comes LAST of the gates:

  1. the fleet brake            — nothing at all runs while it is set
  2. `record: channel`          — another train advances it; never ours
  3. no script                  — nothing to run, and the card allows it
  4. `auto: false`              — unattended runs skip it; a dispatch does not
  5. current                    — the surface's paths are untouched since its
                                  newest tag, so there is nothing to release
  6. spacing                    — the newest tag in the series is too recent
  7. window                     — the PT clock is outside the surface's hours
  8. green-at-SHA               — every GATING check run on the SHA is green

Rules 2-7 are answered from the caller's own checkout and the clock; rule 8
costs two API reads (the check runs, and the workflow-runs record that says
which of them gate). Asking it first would spend those reads on every trigger
to discover that nothing changed under any surface's paths, and would raise a
REFUSAL about a release nobody was going to make. So the workflow asks the
same `decide()` twice: once optimistically, to build the matrix (no surface
with nothing to release ever reads a check), and once for real inside the
surface's own concurrency lane. One function, one set of rules, asked twice —
never two copies of the order.

A REFUSAL IS LOUD AND A NO-OP IS NOT. `no-op` and `held` conclude the job
`success`: they are the train working — and since DRE-3263 that includes a
gating check still running, which is `not ready` and the next train's
business. `refuse` exits non-zero — a red gating check, a SHA no gating
check has reported on, a script that failed, a script that rolled out and cut
no tag. And a script that exits 0 printing one `deferred: <reason>` line is a
NO-OP reported verbatim, never a failure: the deployment is owed to a person,
deploy-lag goes on reading BEHIND until they do it, and nothing alerts.

THE BRAKE. `RELEASE_HOLD` is read exactly the way `INTAKE_HOLD` is read —
through `intake_controls.hold()`, which is why the empty string is the
load-bearing case: an unset variable interpolates to `""` and a reader that
took that for "closed" would stop the fleet on its very first trigger. Set it
and every surface exits `held`, saying so once each, before any surface job
exists. It is a repository variable, so `vars.RELEASE_HOLD` — see
`standards/release-train.md` for WHERE to set it, because a called workflow's
`vars` context resolves against the CALLING repository plus the organization,
and the fleet-wide brake is therefore the organization variable.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake_controls  # noqa: E402
import merge_gate  # noqa: E402

#: The clock every window is read on. GitHub's `schedule:` takes UTC only and
#: has no timezone field, so the STUB carries two cron lines and knows nothing
#: about PT; the train converts here — the way `linear_ops._PT` and
#: agent-bureau's `check_deploy_activity.py` already do.
PT = ZoneInfo("America/Los_Angeles")

#: Opens every line the train prints, so one run's receipts are greppable.
TAG = "release-train"

#: The fleet-wide brake, in the shape of `INTAKE_HOLD` and read by the same
#: function, empty-string semantics included.
ENV_HOLD = "RELEASE_HOLD"

#: What "green" means, taken from the merge gate rather than restated: one
#: name for one fact. `skipped` and `neutral` are green there because half
#: this fleet's jobs conclude that way on purpose.
GREEN_CONCLUSIONS = merge_gate.GREEN_CONCLUSIONS

#: Which check runs on a SHA are checks OF it — the merge gate's classifier,
#: not a copy (DRE-3263). The train adds exactly one path to the gate's
#: excluded set: its own stub in the caller, because a train run sits on the
#: SHA it is releasing and its own `Release <surface>` job must never read
#: as a pending check.
gating_check_runs = merge_gate.gating_check_runs
TRAIN_WORKFLOW = ".github/workflows/release-train.yml"
IGNORED_WORKFLOWS = merge_gate.DEFAULT_REVIEW_WORKFLOWS + (TRAIN_WORKFLOW,)

#: Where a caller declares its surfaces, and where this repo declares its own.
DATA_PATH = ".github/bureau/release.json"

ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = ROOT / "docs" / "release-train.md"

# The four things a decision can be. `refuse` is the only one that is a
# failure; `held` and `no-op` are the train working.
RELEASE = "release"
NO_OP = "no-op"
REFUSE = "refuse"
HELD = "held"

#: `record` values. `tag` is the default; `channel` is a moving tag another
#: train advances, declared so deploy-lag and the receipts read it like any
#: other surface, and never run by this train.
RECORDS = ("tag", "channel")

WINDOW_ALWAYS = "always"
_WINDOW_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d) PT$")

#: The line a surface script prints to say the deployment is owed to a person.
DEFERRAL_PREFIX = "deferred:"


class Field(NamedTuple):
    """One key of a surface, once — the schema check and `docs/` both read
    this table, so the document cannot drift from what is enforced."""

    name: str
    kind: str
    means: str
    nullable: bool = False


SCHEMA: tuple[Field, ...] = (
    Field("tag_series", "list of tag globs",
          "The newest tag across ALL the globs is the deployment record. It is "
          "what deploy-lag reads and what the console's Shipped-today panel "
          "measures commit ancestry against, so a legacy glob belongs here "
          "rather than in anyone's memory."),
    Field("paths", "list of paths",
          "What counts as changed. `[]` means every commit counts. A surface "
          "whose paths are untouched since its newest tag reads current, and "
          "the train exits a no-op saying so."),
    Field("script", "path in the caller, or null",
          "The surface's own release script, called as `bash <script> "
          "--surface <name>` with `RELEASE_SHA` in the environment and the "
          "identity already assumed. Null only while `auto` is false.",
          nullable=True),
    Field("rollback", "command, or null",
          "The one command that puts the surface back, written out so nobody "
          "has to derive it at 2am. Null only while `auto` is false.",
          nullable=True),
    Field("spacing_minutes", "whole minutes",
          "How long after the newest tag in the series the next release may be "
          "cut. Two triggers inside the spacing produce one release."),
    Field("window", "`HH:MM-HH:MM PT`, or `always`",
          "The hours a release may be cut, read on the America/Los_Angeles "
          "clock. A trigger outside the window is a no-op that names it; a "
          "hand dispatch runs anyway."),
    Field("auto", "true or false",
          "Whether the train releases this surface unattended. False means it "
          "releases only on a hand dispatch naming it — which is how a "
          "supervised first release is run."),
    Field("identity", "role name",
          "Who the script runs as: the caller's own OIDC role, fed from the "
          "one required secret `RELEASE_ROLE_ARN`. On a `channel` surface it "
          "names whatever advances the ref, since nothing is assumed."),
    Field("record", "`tag` or `channel`",
          "`tag` is the default and the deployment record is the annotated tag "
          "the script cuts. `channel` is a moving tag another train advances: "
          "the release train never runs it, and deploy-lag measures it by "
          "compare."),
)

REQUIRED_FIELDS = tuple(f.name for f in SCHEMA if f.name != "record")


class Surface(NamedTuple):
    """One declared surface, as the rules need it."""

    name: str
    tag_series: list
    paths: list
    script: str | None
    rollback: str | None
    spacing_minutes: int
    window: str
    auto: bool
    identity: str
    record: str


class Decision(NamedTuple):
    """What the train does about one surface, and the plain sentence why."""

    act: str
    code: str
    reason: str
    tag: str | None = None

    @property
    def ok(self) -> bool:
        """A no-op and a hold are the train working; only a refusal is red."""
        return self.act != REFUSE

    @property
    def releases(self) -> bool:
        return self.act == RELEASE

    def receipt(self, repo: str, surface_name: str, sha: str | None = None) -> str:
        """The ONE line this surface contributes to the run log."""
        if self.act == RELEASE and self.tag:
            at = f" at {sha[:7]}" if sha else ""
            return f"{TAG}: released {repo} {surface_name} as {self.tag}{at}"
        return f"{TAG}: {self.act} {repo} {surface_name} — {self.reason}"


class Checks(NamedTuple):
    """Green-at-SHA, as one answer plus the detail that names the check —
    and, since DRE-3263, what was read and what was ignored, so a run's log
    shows why it decided what it did."""

    state: str   # green | red | pending | absent
    detail: str
    read: tuple = ()      # the names of the gating check runs that counted
    ignored: tuple = ()   # (name, producing workflow path) for each ignored

    def describe(self) -> str:
        """One clause naming the checks read and the runs ignored, grouped
        by the workflow file that produced them — 148 names is not a line
        anyone reads, six file names is."""
        read = (f"read {len(self.read)} gating check runs: "
                + ", ".join(f"`{name}`" for name in self.read)
                if self.read else "read no gating check run")
        if not self.ignored:
            return f"{read}; ignored nothing"
        by_path: dict[str, int] = {}
        for _, path in self.ignored:
            by_path[path or "?"] = by_path.get(path or "?", 0) + 1
        files = ", ".join(f"{p.rsplit('/', 1)[-1]} ({n})"
                          for p, n in sorted(by_path.items()))
        return (f"{read}; ignored {len(self.ignored)} check runs the commit "
                f"did not trigger, from {files}")


#: What `plan()` assumes so that no surface with nothing to release ever reads
#: a check run. The surface's own job asks the checks API for real.
ASSUMED_GREEN = Checks("green", "assumed green — the surface's own job asks "
                                "the checks API before anything runs")


# ---------------------------------------------------------------------------
# The data
# ---------------------------------------------------------------------------

def load(path=None) -> dict:
    """Read a caller's `release.json`. Absent is an empty declaration, not an
    error: a repo with no surfaces has no train, and saying so beats crashing
    every push."""
    path = Path(path) if path else Path(DATA_PATH)
    if not path.is_file():
        return {"surfaces": {}}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def surface(name: str, data: dict) -> Surface:
    """One surface record → a `Surface`. Shape errors are `check_schema`'s to
    report; this only reads."""
    return Surface(
        name=name,
        tag_series=list(data.get("tag_series") or []),
        paths=list(data.get("paths") or []),
        script=data.get("script"),
        rollback=data.get("rollback"),
        spacing_minutes=int(data.get("spacing_minutes") or 0),
        window=str(data.get("window") or WINDOW_ALWAYS),
        auto=bool(data.get("auto")),
        identity=str(data.get("identity") or ""),
        record=str(data.get("record") or "tag"),
    )


def surfaces(data: dict) -> dict:
    """Every declared surface, in declaration order."""
    return {name: surface(name, entry)
            for name, entry in (data.get("surfaces") or {}).items()}


def check_schema(data, repo_root=None) -> list:
    """Every problem with a `release.json`, each NAMING the field.

    A refusal that says "malformed" and stops is a refusal nobody can act on,
    so every message carries the surface and the field. `repo_root`, when
    given, also asks the caller whether the script each surface names is
    actually there — the commonest way this file goes wrong is a rename.
    """
    problems: list[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("surfaces"), dict):
        return [f"{DATA_PATH}: the top-level `surfaces` object is missing"]

    for name, entry in data["surfaces"].items():
        if not isinstance(entry, dict):
            problems.append(f"{name}: a surface must be an object")
            continue
        problems.extend(_check_surface(name, entry, repo_root))
    return problems


def _check_surface(name: str, entry: dict, repo_root=None) -> list:
    problems: list[str] = []

    def bad(field, said):
        problems.append(f"{name}.{field}: {said}")

    for field in REQUIRED_FIELDS:
        if field not in entry:
            bad(field, "is required and absent")
    for key in entry:
        if key not in {f.name for f in SCHEMA}:
            bad(key, "is not a field of the surface schema")

    series = entry.get("tag_series")
    if "tag_series" in entry and (
        not isinstance(series, list) or not series
        or not all(isinstance(g, str) and g for g in series)
    ):
        bad("tag_series", "must be a non-empty list of tag globs")

    paths = entry.get("paths")
    if "paths" in entry and (
        not isinstance(paths, list)
        or not all(isinstance(p, str) and p for p in paths)
    ):
        bad("paths", "must be a list of paths (`[]` means every commit)")

    spacing = entry.get("spacing_minutes")
    if "spacing_minutes" in entry and (
        isinstance(spacing, bool) or not isinstance(spacing, int) or spacing < 0
    ):
        bad("spacing_minutes", "must be a whole number of minutes, zero or more")

    window = entry.get("window")
    if "window" in entry and not _window_ok(window):
        bad("window", f"must be `HH:MM-HH:MM PT` or `{WINDOW_ALWAYS}`, "
                      f"not {window!r}")

    if "auto" in entry and not isinstance(entry.get("auto"), bool):
        bad("auto", "must be true or false")

    identity = entry.get("identity")
    if "identity" in entry and (not isinstance(identity, str) or not identity):
        bad("identity", "must name the identity the script runs under")

    record = entry.get("record", "tag")
    if record not in RECORDS:
        bad("record", f"must be one of {', '.join(RECORDS)}")

    for field in ("script", "rollback"):
        value = entry.get(field)
        if value is not None and (not isinstance(value, str) or not value):
            bad(field, "must be a non-empty string or null")
        elif value is None and entry.get("auto") is True:
            bad(field, "may be null ONLY while `auto` is false")

    if record == "channel" and entry.get("script") is not None:
        bad("script", "a `channel` surface names no script — the release "
                      "train never runs one, another train advances the ref")

    script = entry.get("script")
    if repo_root and isinstance(script, str) and script:
        if not (Path(repo_root) / script).is_file():
            bad("script", f"names {script}, which this repository does not carry")

    return problems


def _window_ok(window) -> bool:
    return isinstance(window, str) and (
        window == WINDOW_ALWAYS or bool(_WINDOW_RE.match(window))
    )


# ---------------------------------------------------------------------------
# Green-at-SHA
# ---------------------------------------------------------------------------

def read_checks(check_runs, workflow_runs=None) -> Checks:
    """One answer from a `commits/{sha}/check-runs` payload and the
    `actions/runs?head_sha=` record that says which of those runs gate.

    Only the GATING check runs are checks (DRE-3263): the classifier is the
    merge gate's, and the train excludes its own workflow on top. A FAILED
    or ABSENT gating check is a refusal, never a wait; a PENDING one is
    reported as pending and `decide()` makes it a no-op — nothing here ever
    waits. With no workflow-runs record at all nothing can be attributed,
    so nothing is ignored: fail closed, and the next train re-reads.
    """
    counted, ignored = gating_check_runs(
        list(check_runs or []), list(workflow_runs or []), IGNORED_WORKFLOWS)
    read = tuple(r.get("name") for r in counted)
    skipped = tuple((r.get("name"), path) for r, path in ignored)
    if not counted:
        return Checks("absent", "no gating check run has reported on the SHA",
                      read, skipped)

    failed = [r for r in counted
              if (r.get("status") == "completed"
                  and (r.get("conclusion") or "") not in GREEN_CONCLUSIONS)]
    if failed:
        return Checks("red", ", ".join(
            f"`{r.get('name')}` concluded {r.get('conclusion') or 'nothing'}"
            for r in failed), read, skipped)

    pending = [r for r in counted if r.get("status") != "completed"]
    if pending:
        return Checks("pending", ", ".join(
            f"`{r.get('name')}` is still {r.get('status')}" for r in pending),
            read, skipped)

    return Checks("green", f"{len(counted)} gating check runs green at the SHA",
                  read, skipped)


def _gh_lines(path: str, jq: str, what: str, sha: str) -> list:
    """`gh api --paginate` streamed one JSON object per line — a bare
    paginate concatenates whole pages into one unparseable blob."""
    out = subprocess.run(
        ["gh", "api", "--paginate", path, "--jq", jq],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"gh could not read {what} on {sha}: {out.stderr.strip()}")
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


def fetch_checks(repo: str, sha: str) -> Checks:
    """Green-at-SHA from GitHub, read ONCE: the check runs on the SHA and the
    workflow-runs record that ties each check suite to the workflow file and
    event that produced it — the two payloads the merge gate reads, in the
    same shapes. There is no loop and no wait; a pending answer is a pending
    answer."""
    check_runs = _gh_lines(
        f"repos/{repo}/commits/{sha}/check-runs",
        ".check_runs[] | {name, status, conclusion, check_suite: {id: .check_suite.id}}",
        "the checks", sha)
    workflow_runs = _gh_lines(
        f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100",
        ".workflow_runs[] | {id, name, path, event, status, conclusion, check_suite_id}",
        "the workflow runs", sha)
    return read_checks(check_runs, workflow_runs)


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------

def brake(raw=None) -> str | None:
    """`None` when the fleet is running; otherwise WHEN it was braked.

    Read through `intake_controls.hold()` on purpose — that function is
    already the tested reading of this switch, empty-string semantics and
    all, and a second reading is a brake with a hole in it.
    """
    raw = os.environ.get(ENV_HOLD, "") if raw is None else raw
    return intake_controls.hold(raw)


def decide(surface, now, newest_tag_at, lag_state, ci_green, brake, *,
           dispatched: bool = False) -> Decision:
    """The whole rule, as one answer with a plain sentence.

    `lag_state` is `"current"` or `"behind"` (this module's `lag_state()`
    computes it from the caller's git); `ci_green` is a `Checks` or a bare
    bool; `brake` is `None` or the date the fleet was braked. `dispatched` is
    the hand dispatch: it runs an `auto: false` surface and ignores the
    window, and it bypasses nothing else — not the brake, not the spacing,
    not green-at-SHA.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(
            "the release train reads a timezone-aware clock: a naive datetime "
            "cannot be converted to America/Los_Angeles, and a window read on "
            "the runner's UTC clock is the whole bug this rule exists to avoid"
        )
    local = now.astimezone(PT)

    if brake is not None:
        since = f" (set {brake})" if brake else " (no date set on the switch)"
        return Decision(
            HELD, "held",
            f"the fleet brake {ENV_HOLD} is set{since} — {surface.name} "
            f"releases nothing until it is cleared")

    if surface.record == "channel":
        series = ", ".join(surface.tag_series) or "its series"
        return Decision(
            NO_OP, "channel",
            f"{surface.name} records the channel {series}, which another train "
            f"advances — the release train never runs a `channel` surface")

    if not surface.script:
        return Decision(
            NO_OP, "no-script",
            f"{surface.name} declares no script, so there is nothing to run")

    if not surface.auto and not dispatched:
        return Decision(
            NO_OP, "auto-false",
            f"{surface.name} is `auto: false` — it releases only on a hand "
            f"dispatch naming it")

    if lag_state == "current":
        where = ", ".join(surface.paths) or "the whole repository"
        return Decision(
            NO_OP, "current",
            f"{surface.name} reads current: nothing under {where} has changed "
            f"since its newest tag")

    if newest_tag_at is not None and surface.spacing_minutes > 0:
        elapsed = now - newest_tag_at
        if elapsed < timedelta(minutes=surface.spacing_minutes):
            next_at = (newest_tag_at + timedelta(minutes=surface.spacing_minutes))
            minutes = max(0, int(elapsed.total_seconds() // 60))
            return Decision(
                NO_OP, "spacing",
                f"{surface.name} was released {minutes} minutes ago and its "
                f"spacing is {surface.spacing_minutes} minutes — the next "
                f"release may be cut at "
                f"{next_at.astimezone(PT):%H:%M} PT")

    if not dispatched and surface.window != WINDOW_ALWAYS:
        start, end = window_bounds(surface.window)
        if not in_window(surface.window, now):
            return Decision(
                NO_OP, "window",
                f"the clock reads {local:%H:%M} PT and {surface.name}'s window "
                f"is {surface.window} — this defers to {start} PT")

    checks = ci_green if isinstance(ci_green, Checks) else (
        Checks("green", "the caller said so") if ci_green is True
        else Checks("red", "CI at the head SHA did not conclude success"))
    if checks.state == "pending":
        # The train is never stopped (DRE-3263): a gating check still running
        # is not waited for. The commit is not ready, this run leaves without
        # it, and the run CI completion triggers picks it up.
        return Decision(
            NO_OP, "ci-pending",
            f"{surface.name} is not ready — {checks.detail}; the next run "
            f"takes it ({checks.describe()})")
    if checks.state != "green":
        return Decision(REFUSE, f"ci-{checks.state}", _ci_refusal(checks))

    # The checks detail rides along on purpose: the matrix pass asks with CI
    # ASSUMED green, and a plan line that claimed the checks API had answered
    # would be the one sentence in this file that is not true.
    said = checks.detail if checks is ASSUMED_GREEN else (
        f"{checks.detail}; {checks.describe()}")
    return Decision(
        RELEASE, "release",
        f"{surface.name} is behind and the spacing has elapsed — releasing "
        f"({said})")


def _ci_refusal(checks: Checks) -> str:
    if checks.state == "absent":
        return ("no gating check has run on the SHA, and an absent check is a "
                f"refusal, never a wait — no check proves this commit "
                f"({checks.describe()})")
    return f"CI at the SHA is not green: {checks.detail} ({checks.describe()})"


def window_bounds(window: str):
    """(`start`, `end`) as `HH:MM` strings."""
    if window == WINDOW_ALWAYS:
        return "00:00", "24:00"
    match = _WINDOW_RE.match(window)
    if not match:
        raise ValueError(f"not a window: {window!r}")
    a, b, c, d = match.groups()
    return f"{a}:{b}", f"{c}:{d}"


def in_window(window: str, now: datetime) -> bool:
    """Is the PT wall clock inside the surface's hours?

    A window that reads backwards (`21:00-07:00 PT`) wraps midnight — the
    natural reading, and the only one that does not silently mean "never".
    """
    if window == WINDOW_ALWAYS:
        return True
    start, end = window_bounds(window)
    local = now.astimezone(PT)
    clock = f"{local:%H:%M}"
    if start <= end:
        return start <= clock < end
    return clock >= start or clock < end


# ---------------------------------------------------------------------------
# The caller's git — the newest tag, and what has changed since it
# ---------------------------------------------------------------------------

def _git(repo_root, *args, check=True) -> str:
    done = subprocess.run(["git", "-C", str(repo_root), *args],
                          capture_output=True, text=True)
    if check and done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout.strip()


def newest_tag(repo_root, series):
    """(tag, when) — the newest tag across ALL the globs, or (None, None).

    Across all of them, because a surface that was renamed keeps its legacy
    glob and the deployment record is whichever tag is newest, not whichever
    glob was written first.
    """
    listing = _git(repo_root, "for-each-ref", "--sort=-creatordate",
                   "--format=%(refname:short)%09%(creatordate:iso-strict)",
                   "refs/tags")
    for line in listing.splitlines():
        name, _, when = line.partition("\t")
        if any(fnmatch.fnmatch(name, glob) for glob in series):
            return name, (datetime.fromisoformat(when) if when else None)
    return None, None


def lag_state(repo_root, tag, sha, paths) -> str:
    """`"behind"` when the surface owes a release, `"current"` when it does not.

    No tag at all is BEHIND — the first release of a surface is exactly the
    case a "nothing changed" reading would silently swallow.

    A SHA the newest tag already CONTAINS is current (DRE-3263): the
    CI-completion trigger hands the train the commit CI ran on, which is
    older than the newest tag whenever a slow CI on X finishes after Y was
    released. `diff Y..X` is non-empty in the reverse direction and would
    read X as behind — and tag backwards.
    """
    if not tag:
        return "behind"
    contained = subprocess.run(
        ["git", "-C", str(repo_root), "merge-base", "--is-ancestor", sha, tag],
        capture_output=True, text=True,
    )
    if contained.returncode == 0:
        return "current"
    args = ["diff", "--name-only", f"{tag}..{sha}"]
    if paths:
        args += ["--", *paths]
    return "behind" if _git(repo_root, *args) else "current"


def tag_is_annotated(repo_root, tag) -> bool:
    return _git(repo_root, "cat-file", "-t", tag, check=False) == "tag"


def tag_target(repo_root, tag) -> str:
    return _git(repo_root, "rev-list", "-n", "1", tag, check=False)


# ---------------------------------------------------------------------------
# Running a surface
# ---------------------------------------------------------------------------

def script_env(base, *, sha: str, surface_name: str) -> dict:
    """The environment the surface script is handed."""
    env = dict(base)
    env["RELEASE_SHA"] = sha
    env["RELEASE_SURFACE"] = surface_name
    return env


def run_surface(surface, *, repo_root, sha, before=None, env=None,
                out=print) -> Decision:
    """Run the surface's script and VERIFY the tag it cut.

    The contract is `standards/release-train.md`: `bash <script> --surface
    <name>`, non-zero on any failure, and an annotated tag in the series at
    `RELEASE_SHA` when it releases. Three outcomes, and the middle one is the
    one that is easy to get wrong: exit 0 with a `deferred:` line is a NO-OP
    reported verbatim, not a failure and not a release.
    """
    script = Path(repo_root) / surface.script
    done = subprocess.run(
        ["bash", str(script), "--surface", surface.name],
        cwd=str(repo_root), text=True, capture_output=True,
        env=script_env(env if env is not None else os.environ,
                       sha=sha, surface_name=surface.name),
    )
    for stream in (done.stdout, done.stderr):
        for line in (stream or "").splitlines():
            out(f"{TAG}: [{surface.name}] {line}")

    if done.returncode != 0:
        return Decision(
            REFUSE, "script-failed",
            f"{surface.script} exited {done.returncode} — the surface is not "
            f"released and the rollback is `{surface.rollback}`")

    for line in (done.stdout or "").splitlines():
        if line.strip().startswith(DEFERRAL_PREFIX):
            return Decision(NO_OP, "deferred", line.strip())

    after, _ = newest_tag(repo_root, surface.tag_series)
    if not after or after == before:
        return Decision(
            REFUSE, "no-tag",
            f"{surface.script} exited 0 and cut no new tag in "
            f"{', '.join(surface.tag_series)} — the tag IS the deployment "
            f"record, so an untagged rollout is not a release")
    if not tag_is_annotated(repo_root, after):
        return Decision(
            REFUSE, "tag-not-annotated",
            f"{after} is a lightweight tag — the deployment record carries the "
            f"standard annotation")
    target = tag_target(repo_root, after)
    if target != sha:
        return Decision(
            REFUSE, "tag-elsewhere",
            f"{after} points at {target[:7]}, not at the released commit "
            f"{sha[:7]}")
    return Decision(RELEASE, "released",
                    f"{surface.name} released at {sha[:7]} as {after}",
                    tag=after)


def release(surface, *, repo, repo_root, sha, now, checks, brake=None,
            dispatched=False, env=None, out=print) -> Decision:
    """Decide about ONE surface with the real checks, then act — and print the
    one receipt line either way.

    `checks` may be a `Checks` or a callable that produces one. It is only
    called when the local rules have already said this surface would release:
    a job that queued behind another release and now reads current must not
    spend two API reads to be told the same thing.
    """
    tag, tag_at = newest_tag(repo_root, surface.tag_series)
    lag = lag_state(repo_root, tag, sha, surface.paths)
    decision = decide(surface, now, tag_at, lag, ASSUMED_GREEN, brake,
                      dispatched=dispatched)
    if decision.releases:
        settled = checks() if callable(checks) else checks
        decision = decide(surface, now, tag_at, lag, settled, brake,
                          dispatched=dispatched)
    if decision.releases:
        decision = run_surface(surface, repo_root=repo_root, sha=sha,
                               before=tag, env=env, out=out)
    out(decision.receipt(repo, surface.name, sha))
    return decision


def plan(data, *, repo_root, sha, now, brake=None, dispatched_surface=None):
    """Every declared surface and what the train would do about it.

    Asked with CI assumed green, so a surface with nothing to release never
    reads a check run (see the module docstring's ordering note). A hand
    dispatch narrows the plan to the one surface it names.
    """
    out = []
    for name, entry in surfaces(data).items():
        if dispatched_surface and name != dispatched_surface:
            continue
        tag, tag_at = newest_tag(repo_root, entry.tag_series)
        lag = lag_state(repo_root, tag, sha, entry.paths)
        out.append((entry, decide(
            entry, now, tag_at, lag, ASSUMED_GREEN, brake,
            dispatched=bool(dispatched_surface))))
    return out


def matrix(planned) -> list:
    """The surfaces that get a job — one lane each, and nothing else runs."""
    return [entry.name for entry, decision in planned if decision.releases]


# ---------------------------------------------------------------------------
# The document, rendered from the schema
# ---------------------------------------------------------------------------

def render_markdown() -> str:
    out: list[str] = []
    w = out.append
    w("# The release train's data")
    w("")
    w("<!-- GENERATED FILE — do not edit. Source: scripts/release_train.py.")
    w("     Regenerate with `python3 scripts/release_train.py render`. -->")
    w("")
    w(
        "Every repo the train serves declares its surfaces in "
        f"`{DATA_PATH}`. This page is rendered from the same table the schema "
        "check reads, so it cannot drift from what is enforced — run "
        f"`python3 scripts/release_train.py schema` to check a file, and every "
        "refusal names the surface and the field."
    )
    w("")
    w("## A surface")
    w("")
    w("```json")
    w(json.dumps({"surfaces": {"<name>": {
        "tag_series": ["<glob>", "<legacy glob>"],
        "paths": ["<dir>/"],
        "script": "infra/release-<name>.sh",
        "rollback": "make rollback-<name> VERSION=<tag>",
        "spacing_minutes": 30,
        "window": "07:00-21:00 PT",
        "auto": False,
        "identity": "<role name>",
        "record": "tag",
    }}}, indent=2))
    w("```")
    w("")
    w("| Field | Shape | What it means |")
    w("| --- | --- | --- |")
    for field in SCHEMA:
        w(f"| `{field.name}` | {field.kind} | {field.means} |")
    w("")
    w("## What the train decides, in order")
    w("")
    w(
        "One rule set, asked twice: once to build the matrix, and once inside "
        "each surface's own concurrency lane. `no-op` and `held` conclude the "
        "job green — they are the train working; only `refuse` is red."
    )
    w("")
    w("| Order | Decision | Act | Says |")
    w("| --- | --- | --- | --- |")
    for i, (code, act, says) in enumerate(_ORDER, start=1):
        w(f"| {i} | `{code}` | `{act}` | {says} |")
    w("")
    w(
        f"The brake is the repository variable `{ENV_HOLD}`, read the way "
        "`INTAKE_HOLD` is read — see `standards/release-train.md` for where to "
        "set it and what the surface script owes."
    )
    w("")
    w("## The trigger, and what the stub must declare")
    w("")
    w(
        "**The train is never stopped** (DRE-3263, the CEO's rule of "
        "2026-09-06). If the commit is ready it goes; if it is not, the train "
        "leaves without it and the next train picks it up. A gating check "
        "still running is therefore a `no-op` that names it — never a wait, "
        "never a refusal — and the run that picks the commit up is the one "
        "fired by CI completing on the default branch. A `push` fires BEFORE "
        "that commit's CI has started, so a push-triggered stub would find CI "
        "pending on every run and release only from the schedule. The stub in "
        "every caller (`.github/workflows/release-train.yml`) must declare "
        "exactly this:"
    )
    w("")
    w("```yaml")
    w("on:")
    w("  workflow_run:")
    w('    workflows: ["CI"]')
    w("    types: [completed]")
    w("    branches: [main]")
    w("  schedule:")
    w('    - cron: "0 15 * * *"')
    w('    - cron: "0 14 * * *"')
    w("  workflow_dispatch:")
    w("    inputs:")
    w("      surface:")
    w("        type: string")
    w("        required: false")
    w('        default: ""')
    w("```")
    w("")
    w(
        "`workflows: [\"CI\"]` is the `name:` of the caller's CI workflow, "
        "and `branches: [main]` is the branch that CI ran on. On that event "
        "the reusable workflow reads the commit from "
        "`github.event.workflow_run.head_sha` and proceeds only when "
        "`github.event.workflow_run.conclusion` is `success`; a CI run that "
        "concluded anything else is a `no-op` that says so, and the commit "
        "waits for the repair the medic files. The schedule and the hand "
        "dispatch read the head of the branch at that moment, as before."
    )
    w("")
    w(
        f"Which check runs on the commit COUNT is decided in one place — "
        "`merge_gate.gating_check_runs`, the same classifier the merge gate's "
        "all-green rule rests on. A check run counts when GitHub's own "
        "workflow-runs record says the commit itself triggered it (`push`, "
        "`pull_request`, `pull_request_target`) and it is not a review "
        "workflow or the train's own stub; a fix agent, the medic, the sweep, "
        "a hand dispatch and the train's own run all report against the "
        "default branch's head without being about it, and are ignored by "
        "that origin — never by name. Every no-op and refusal line names what "
        "was read and what was ignored, by producing workflow file."
    )
    w("")
    return "\n".join(out)


#: The decision order, once, for the document. The code strings are the ones
#: `decide()` returns, so a renamed reason breaks the render rather than
#: quietly publishing a reason nothing emits.
_ORDER = (
    ("held", HELD, f"`{ENV_HOLD}` is set: every surface exits held, once each, "
                   "before any surface job exists"),
    ("channel", NO_OP, "the surface records a channel another train advances"),
    ("no-script", NO_OP, "the surface declares no script"),
    ("auto-false", NO_OP, "unattended runs skip an `auto: false` surface; a "
                          "hand dispatch runs it"),
    ("current", NO_OP, "nothing under the surface's `paths` has changed since "
                       "its newest tag"),
    ("spacing", NO_OP, "the newest tag in the series is younger than "
                       "`spacing_minutes`"),
    ("window", NO_OP, "the America/Los_Angeles clock is outside the window; a "
                      "hand dispatch runs anyway"),
    ("ci-pending", NO_OP, "a gating check on the SHA is still running — the "
                          "commit is not ready, the train leaves without it, "
                          "and the run CI completion fires takes it (never a "
                          "wait: the train is never stopped)"),
    ("ci-red / ci-absent", REFUSE,
     "a gating check on the SHA failed, or no gating check has reported on "
     "it — named in the refusal, with what was read and what was ignored"),
    ("deferred", NO_OP, "the script exited 0 printing `deferred: …` — the "
                        "deployment is owed to a person, and that is not a "
                        "failure"),
    ("released", RELEASE, "the script ran and the train verified the annotated "
                          "tag it cut at the released commit"),
)


# ---------------------------------------------------------------------------
# The CLI the workflow calls
# ---------------------------------------------------------------------------

def _emit_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")


def _cmd_schema(args) -> int:
    problems = check_schema(load(args.file), repo_root=args.repo_root)
    if problems:
        print(f"{TAG}: {args.file} is malformed —")
        for problem in problems:
            print(f"  {problem}")
        return 1
    declared = ", ".join(surfaces(load(args.file))) or "no surfaces"
    print(f"{TAG}: {args.file} is well formed ({declared})")
    return 0


def _cmd_plan(args) -> int:
    data = load(args.file)
    problems = check_schema(data, repo_root=args.repo_root)
    if problems:
        print(f"{TAG}: refusing to run — {args.file} is malformed:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    planned = plan(data, repo_root=args.repo_root, sha=args.sha,
                   now=datetime.now(tz=PT), brake=brake(),
                   dispatched_surface=args.surface or None)
    for entry, decision in planned:
        print(decision.receipt(args.repo, entry.name, args.sha))
    _emit_output("matrix", json.dumps(matrix(planned)))
    _emit_output("sha", args.sha)
    return 0


def _cmd_release(args) -> int:
    data = load(args.file)
    entry = surfaces(data).get(args.surface)
    if entry is None:
        print(f"{TAG}: {args.file} declares no surface {args.surface!r}")
        return 1
    try:
        decision = release(
            entry, repo=args.repo, repo_root=args.repo_root, sha=args.sha,
            now=datetime.now(tz=PT), brake=brake(), dispatched=args.dispatched,
            checks=lambda: fetch_checks(args.repo, args.sha),
        )
    except RuntimeError as err:
        # FAIL CLOSED. An unreadable answer is not a green one — the same rule
        # `check_train_in_flight.py` states as "UNKNOWN is never clear".
        decision = Decision(REFUSE, "unreadable", str(err))
        print(decision.receipt(args.repo, args.surface, args.sha))
    return 0 if decision.ok else 1


def _cmd_render(args) -> int:
    rendered = render_markdown()
    with open(DOC_PATH, "w", encoding="utf-8") as fh:
        fh.write(rendered)
    print(f"wrote {DOC_PATH} ({len(rendered.splitlines())} lines)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", default=DATA_PATH,
                        help="the caller's release.json")
    parser.add_argument("--repo-root", default=".",
                        help="the caller's checkout")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""),
                        help="owner/repo, for the receipts")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("schema", help="check a release.json and name every problem")

    planner = sub.add_parser("plan", help="decide every surface; emit the matrix")
    planner.add_argument("--sha", required=True)
    planner.add_argument("--surface", default="",
                         help="a hand dispatch: plan only this surface")

    runner = sub.add_parser("release", help="release ONE surface")
    runner.add_argument("--sha", required=True)
    runner.add_argument("--surface", required=True)
    runner.add_argument("--dispatched", action="store_true")

    sub.add_parser("render", help="rewrite docs/release-train.md")

    args = parser.parse_args(argv)
    return {
        "schema": _cmd_schema,
        "plan": _cmd_plan,
        "release": _cmd_release,
        "render": _cmd_render,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
