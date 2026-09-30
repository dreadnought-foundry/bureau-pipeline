"""The fleet budget reader's table (DRE-3202), driven off fake log text — no
`gh` call anywhere in here. The reader adds up the `linear-budget:` lines
linear_ops prints; these tests pin how it reads them and what it prints."""

import ast
import os
import sys

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
)

import check_linear_budget as clb  # noqa: E402
import linear_ops  # noqa: E402

# What `gh run view --log` prints: job, step, timestamp, then the line.
_PREFIX = "reconcile\tSweep\t2026-09-05T21:08:29.1234567Z "


def _log(*lines: str) -> str:
    return "\n".join(_PREFIX + line for line in lines) + "\n"


def test_spent_is_read_off_the_line_linear_ops_prints():
    """Producer and consumer agree on the format: compose the line with the
    seam's own function, then read it back."""
    linear_ops._reset_budget_state()
    linear_ops._note_response_headers(
        {"x-ratelimit-requests-remaining": "2400", "x-ratelimit-requests-reset": "1788645600000"}
    )
    linear_ops._note_response_headers({"x-ratelimit-requests-remaining": "2380"})
    assert clb.spent_from_log(_log("some other line", linear_ops.budget_line())) == [20]


def test_rolled_and_unknown_lines_count_as_seen_but_unknowable():
    log = _log(
        "linear-budget: 5 → 2499 (window rolled; window resets 16:00 PT)",
        "linear-budget: unknown (no rate-limit headers seen)",
        "linear-budget: 100 → 97 (spent 3 this run; window resets 16:00 PT; refused after 4 calls)",
        # DRE-3224: a mid-run refill is named after the number, never in its place
        "linear-budget: 1675 → 1605 (spent 70 this run (refilled mid-run); window resets 16:04 PT)",
    )
    assert clb.spent_from_log(log) == [None, None, 3, 70]


def test_the_budget_owner_part_does_not_hide_the_spend():
    """DRE-3321 appends `; budget: <identity>` to every budget line. The reader
    keeps reading `spent N` and `window rolled` out of it — that part is added
    at the END and nothing else on the line moves."""
    log = _log(
        "linear-budget: 1862 → 1784 (spent 78 this run; window resets 16:04 PT; budget: fleet)",
        "linear-budget: 5 → 2499 (window rolled; window resets 16:00 PT; budget: operator-tools)",
        "linear-budget: 100 → 97 (spent 3 this run; window resets 16:00 PT; "
        "refused after 4 calls; budget: undeclared)",
        "linear-budget: unknown (no rate-limit headers seen; budget: fleet)",
    )
    assert clb.spent_from_log(log) == [78, None, 3, None]


def test_producer_and_consumer_still_agree_with_the_owner_named(monkeypatch):
    """The same producer-composed check as above, with a declared identity —
    the reader is pinned against the line the seam actually writes, never a
    restatement of it."""
    monkeypatch.setenv(linear_ops.IDENTITY_ENV, "fleet")
    linear_ops._reset_budget_state()
    linear_ops._note_response_headers(
        {"x-ratelimit-requests-remaining": "1862", "x-ratelimit-requests-reset": "1788645600000"}
    )
    linear_ops._note_response_headers({"x-ratelimit-requests-remaining": "1784"})
    line = linear_ops.budget_line()
    assert line.endswith("; budget: fleet)")
    assert clb.spent_from_log(_log(line)) == [78]


def test_aggregate_sums_per_repo_and_workflow_sorted_by_total_desc():
    rows = clb.aggregate(
        [
            ("atlas", "Reconcile", _log("linear-budget: 900 → 880 (spent 20 this run; window resets 16:00 PT)")),
            ("atlas", "Reconcile", _log("linear-budget: 880 → 830 (spent 50 this run; window resets 16:00 PT)")),
            ("portico", "Medic", _log("linear-budget: 830 → 829 (spent 1 this run; window resets 16:00 PT)")),
            ("agent-bureau", "Reconcile", _log("linear-budget: 829 → 700 (spent 129 this run; window resets 16:00 PT)")),
            ("portico", "Reconcile", _log("no linear call in this run")),
        ]
    )
    assert [(r["repo"], r["workflow"], r["runs"], r["total"], r["max"]) for r in rows] == [
        ("agent-bureau", "Reconcile", 1, 129, 129),
        ("atlas", "Reconcile", 2, 70, 50),
        ("portico", "Medic", 1, 1, 1),
        ("portico", "Reconcile", 1, 0, 0),
    ]


def test_render_table_prints_unknown_for_an_unreadable_repo_and_a_grand_total():
    rows = clb.aggregate(
        [
            ("atlas", "Reconcile", _log("linear-budget: 900 → 880 (spent 20 this run; window resets 16:00 PT)")),
            ("portico", "Medic", _log("linear-budget: 880 → 879 (spent 1 this run; window resets 16:00 PT)")),
        ]
    )
    text = clb.render_table(rows, ["deltasolv"], hours=2, skipped=3)
    lines = text.splitlines()
    assert lines[0] == "linear budget, last 2h"
    assert "deltasolv" in text and "UNKNOWN" in text
    assert "0" not in [tok for tok in next(line for line in lines if "deltasolv" in line).split()[1:]]
    total_line = lines[-1]
    assert total_line.startswith("TOTAL") and " 21" in total_line
    assert "1 repo(s) UNKNOWN" in total_line
    assert "3 run(s) skipped" in total_line
    # sorted: atlas (20) above portico (1)
    assert text.index("atlas") < text.index("portico")


def test_the_reader_only_lists_and_views_runs():
    """READ-ONLY, pinned: the only `gh` verbs in the script are `run list` and
    `run view`. A write verb added later fails here before it fails live."""
    with open(clb.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    verbs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_gh":
            args = [a.value for a in node.args if isinstance(a, ast.Constant)]
            verbs.add(tuple(args[:2]))
    assert verbs == {("run", "list"), ("run", "view")}


# ── DRE-5201: a run's own spend, and only the lines the run printed ─────────
# The DRE-3646 proof (docs/sweep-spend-proof-2026-09.md §4) found two defects
# in the table above. A `linear-budget:` line is the FLEET key's remaining
# quota read at the start and the end of a process, so a sweep that overlaps
# another repo's sweep is charged for both: bureau-pipeline's 19:35 PT pass
# read 59, which is its own 20 plus agent-bureau's 39. And the marker was
# matched anywhere in a line, so the Sweep step's own source — which GitHub
# echoes into the log before running it, and which names `linear-budget:`
# three times — added three "unknown/rolled" lines to every reconcile run.

import pathlib  # noqa: E402

import yaml  # noqa: E402

_RECONCILE_YML = (
    pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows" / "reconcile.yml"
)


def _echoed_sweep_step() -> list[str]:
    """The Sweep step's `run:` script as GitHub echoes it into the log: a
    `##[group]Run` header, then every line of the script wrapped in the ANSI
    colour codes the runner prints. Read from the real workflow, so a comment
    added to that step later is covered without touching this file."""
    workflow = yaml.safe_load(_RECONCILE_YML.read_text(encoding="utf-8"))
    steps = [s for job in workflow["jobs"].values() for s in job.get("steps", [])]
    script = next(s["run"] for s in steps if s.get("name") == "Sweep")
    lines = script.splitlines()
    return [f"##[group]Run {lines[0]}"] + [f"\x1b[36;1m{line}\x1b[0m" for line in lines]


def test_the_sweep_steps_echoed_source_is_not_a_budget_line():
    """The premise first: the echoed source really does carry the marker, so
    the next test exercises the defect rather than a log that never had it."""
    echoed = "\n".join(_echoed_sweep_step())
    assert echoed.count("linear-budget:") >= 3


def test_only_a_line_that_starts_with_the_marker_is_read():
    """The echoed script is not a budget line. What the process itself
    printed — at the start of the line, after `gh`'s job/step/timestamp
    prefix — is the one line read."""
    log = _log(
        *_echoed_sweep_step(),
        "sweep-scope: full pass (schedule)",
        "linear-budget: 900 → 880 (spent 20 this run; window resets 16:00 PT; budget: fleet)",
        "##[endgroup]",
    )
    assert clb.spent_from_log(log) == [20]
    rows = clb.aggregate([("atlas", "Merge Gate", log)])
    assert rows[0]["unknown_lines"] == 0
    assert "unknown/rolled" not in clb.render_table(rows, [])


def test_a_marker_mid_line_is_not_read_even_without_the_gh_prefix():
    """A log saved without `gh`'s prefix reads the same way: the marker opens
    the line or the line is not a budget line."""
    log = "\n".join([
        "echo \"no linear-budget: line — the pass exited before its trailer\"",
        "# the `linear-budget:` trailer it prints as it exits",
        "linear-budget: 100 → 97 (spent 3 this run; window resets 16:00 PT)",
    ])
    assert clb.spent_from_log(log) == [3]


def _sweep_total_line(monkeypatch, spent: int) -> str:
    """The `sweep-spend: total` line composed by the sweep's own ledger, so
    the reader is pinned against what the producer prints."""
    os.environ.setdefault("LINEAR_API_KEY", "test-key")
    os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
    os.environ.setdefault("REPO_SLUG", "agent-bureau")
    os.environ.setdefault("GH_TOKEN", "x")
    import contextlib
    import io

    import reconcile

    counter = iter([0, spent])
    monkeypatch.setattr(linear_ops, "requests_made", lambda: next(counter))
    spend = reconcile.SweepSpend()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        spend.report_total()
    (line,) = out.getvalue().splitlines()
    assert line.startswith("sweep-spend: total ")
    return line


def test_an_overlapping_sweep_is_charged_its_own_spend_not_the_fleet_keys(monkeypatch):
    """The 19:35 PT pair from the proof, as the logs printed it. Both passes
    read the one fleet key, so each budget line counts the other's requests
    too; each pass's `sweep-spend: total` counts only its own. The table
    reads the total — 20 and 39, a grand total of 59 — never 59 and 60."""
    pipeline = _log(
        *_echoed_sweep_step(),
        "sweep-spend: flag_unlanded_work 7 request(s)",
        "sweep-spend: promote_ready 5 request(s)",
        _sweep_total_line(monkeypatch, 20),
        "linear-budget: 731 → 672 (spent 59 this run; window resets 20:34 PT; budget: fleet)",
    )
    bureau = _log(
        *_echoed_sweep_step(),
        "sweep-spend: flag_unlanded_work 10 request(s)",
        "sweep-spend: promote_ready 15 request(s)",
        _sweep_total_line(monkeypatch, 39),
        "linear-budget: 732 → 672 (spent 60 this run; window resets 20:34 PT; budget: fleet)",
    )
    rows = clb.aggregate([
        ("bureau-pipeline", "Reconcile", pipeline),
        ("agent-bureau", "Reconcile", bureau),
    ])
    assert [(r["repo"], r["runs"], r["total"], r["max"], r["unknown_lines"]) for r in rows] == [
        ("agent-bureau", 1, 39, 39, 0),
        ("bureau-pipeline", 1, 20, 20, 0),
    ]
    text = clb.render_table(rows, [])
    assert text.splitlines()[-1].split()[:3] == ["TOTAL", "2", "59"]


def test_a_run_with_no_sweep_total_still_reads_its_budget_lines():
    """Only the sweep prints a `sweep-spend: total`. Every other workflow is
    still read off its budget lines, as before."""
    log = _log(
        "linear-budget: 900 → 895 (spent 5 this run; window resets 16:00 PT; budget: fleet)",
    )
    rows = clb.aggregate([("portico", "Medic", log)])
    assert (rows[0]["total"], rows[0]["max"]) == (5, 5)


def test_max_per_run_is_what_the_whole_run_spent():
    """`max/run` is the most one RUN spent: a run of two processes is charged
    both, and the busiest run wins — not the busiest single line."""
    two_processes = _log(
        "linear-budget: 900 → 895 (spent 5 this run; window resets 16:00 PT)",
        "linear-budget: 895 → 888 (spent 7 this run; window resets 16:00 PT)",
    )
    one_process = _log(
        "linear-budget: 888 → 878 (spent 10 this run; window resets 16:00 PT)",
    )
    rows = clb.aggregate([("atlas", "Agent Plan", two_processes),
                          ("atlas", "Agent Plan", one_process)])
    assert (rows[0]["runs"], rows[0]["total"], rows[0]["max"]) == (2, 22, 12)
