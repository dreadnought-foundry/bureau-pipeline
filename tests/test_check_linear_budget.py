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
    text = clb.render_table(
        rows, ["deltasolv"], hours=2,
        skipped=[("atlas", 34310166200), ("portico", 34310170001)], empty=4,
    )
    lines = text.splitlines()
    assert lines[0] == "linear budget, last 2h"
    assert "deltasolv" in text and "UNKNOWN" in text
    assert "0" not in [tok for tok in next(line for line in lines if "deltasolv" in line).split()[1:]]
    total_line = lines[-1]
    assert total_line.startswith("TOTAL") and " 21" in total_line
    assert "1 repo(s) UNKNOWN" in total_line
    assert ("(2 run(s) skipped: log not available after 3 attempts — "
            "atlas 34310166200, portico 34310170001)") in total_line
    assert "(4 run(s) empty log: no job ran)" in total_line
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
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    monkeypatch.setenv("REPO", "dreadnought-foundry/agent-bureau")
    monkeypatch.setenv("REPO_SLUG", "agent-bureau")
    monkeypatch.setenv("GH_TOKEN", "x")
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


def test_the_planner_bucket_is_kept_apart_from_the_fleet_key(monkeypatch):
    """DRE-5589: a planner on its own OAuth bucket says `budget: planner-oauth`
    — a different 5,000-an-hour bucket, so adding its spend to the fleet key's
    row would charge the fleet for requests it never paid. One run whose token
    died mid-run spends on both, and each spend lands in its own row."""
    monkeypatch.setenv(linear_ops.IDENTITY_ENV, "fleet")
    monkeypatch.setenv(linear_ops.HOME_ENV, "planner-oauth")
    monkeypatch.setenv("LINEAR_API_KEY", "Bearer planner")
    monkeypatch.setenv(linear_ops.FALLBACK_ENV, "lin_api_fleet")
    linear_ops._reset_budget_state()
    linear_ops._note_response_headers({"x-ratelimit-requests-remaining": "4998"})
    linear_ops._note_response_headers({"x-ratelimit-requests-remaining": "4938"})
    planner_line = linear_ops.budget_line()
    assert planner_line.endswith("; budget: planner-oauth)")
    rows = clb.aggregate([
        ("atlas", "Agent Plan", _log(planner_line)),
        ("atlas", "Agent Plan", _log(
            planner_line,
            "linear-budget: 351 → 345 (spent 6 this run; window resets 16:00 PT; budget: fleet)",
        )),
    ])
    got = {(r["workflow"], r["budget"]): (r["runs"], r["total"]) for r in rows}
    assert got == {
        ("Agent Plan", "planner-oauth"): (2, 120),
        ("Agent Plan", ""): (1, 6),
    }
    text = clb.render_table(rows, [])
    assert "Agent Plan [planner-oauth]" in text


def test_a_line_on_the_fleet_key_or_naming_no_bucket_stays_in_todays_row():
    rows = clb.aggregate([
        ("atlas", "Reconcile", _log("linear-budget: 9 → 8 (spent 1 this run; window resets 16:00 PT; budget: fleet)")),
        ("atlas", "Reconcile", _log("linear-budget: 8 → 6 (spent 2 this run; window resets 16:00 PT; budget: undeclared)")),
        ("atlas", "Reconcile", _log("linear-budget: 6 → 3 (spent 3 this run; window resets 16:00 PT)")),
    ])
    assert [(r["workflow"], r["budget"], r["runs"], r["total"]) for r in rows] == [
        ("Reconcile", "", 3, 6),
    ]


# ── Stage 2 #11: every process's own count, preferred ───────────────────────
# Every process that talks to Linear now also prints, as it exits,
#
#     linear-calls: <N> request(s) this run (budget: <bucket>)
#
# — the requests IT sent, counted by the seam, not a difference between two
# readings of a bucket other runs are draining at the same time. A run that
# printed any is charged their sum, per bucket; its `sweep-spend: total` and
# `linear-budget:` lines are not added. The sweep's total is a SUBSET of its
# own process's count (one pass, not the whole process), and it says nothing
# about the run's other processes, so the calls lines outrank it too. A log
# from before this line existed reads exactly as it did.


_TOKENS = iter(f"{4000 + i}@__run-{i:06x}" for i in range(10_000))


def _calls_line(monkeypatch, calls: int, identity: str = "fleet",
                token: str | None = None) -> str:
    """The `linear-calls:` line composed by the seam itself, so the reader is
    pinned against what the producer prints, never a restatement of it. Each
    call is a different PROCESS unless the caller names the token — in a test
    session every line would otherwise carry the one pytest process's token."""
    token = token or next(_TOKENS)
    monkeypatch.setenv(linear_ops.IDENTITY_ENV, identity)
    monkeypatch.setattr(linear_ops, "process_token", lambda: token)
    linear_ops._reset_budget_state()
    linear_ops._budget["calls"] = calls
    (line,) = linear_ops.calls_lines()
    linear_ops._reset_budget_state()
    return line


def test_the_calls_line_is_read_off_the_line_linear_ops_prints(monkeypatch):
    line = _calls_line(monkeypatch, 17)
    assert line.startswith("linear-calls: 17 ")
    assert clb.run_spend_from_log(_log("some other line", line)) == [17]
    rows = clb.aggregate([("portico", "Agent Task", _log(line))])
    assert [(r["budget"], r["runs"], r["total"], r["max"]) for r in rows] == [
        ("", 1, 17, 17),
    ]


def test_an_overlapping_run_is_charged_its_own_count_not_the_shared_difference(
    monkeypatch,
):
    """The budget line says 59 because another run drew on the same key
    meanwhile; the process sent 20. The table says 20."""
    log = _log(
        "linear-budget: 731 → 672 (spent 59 this run; window resets 20:34 PT; budget: fleet)",
        _calls_line(monkeypatch, 20),
    )
    rows = clb.aggregate([("atlas", "Agent Task", log)])
    assert [(r["total"], r["max"], r["unknown_lines"]) for r in rows] == [(20, 20, 0)]


def test_a_headerless_run_is_known_by_its_count(monkeypatch):
    """`linear-budget: unknown` was a run seen with a spend nobody could know.
    With the count beside it, it is known — and not an unknown line."""
    log = _log(
        "linear-budget: unknown (no rate-limit headers seen; budget: fleet)",
        _calls_line(monkeypatch, 4),
    )
    rows = clb.aggregate([("deltasolv", "Medic", log)])
    assert [(r["total"], r["unknown_lines"]) for r in rows] == [(4, 0)]
    assert "unknown/rolled" not in clb.render_table(rows, [])


def test_the_calls_lines_outrank_the_sweeps_own_total(monkeypatch):
    """A reconcile run of two processes: the sweep (whose pass counted 20 of
    the 23 requests its process sent) and a second step that sent 6. The run
    sent 29, and that is what it is charged."""
    log = _log(
        *_echoed_sweep_step(),
        "sweep-spend: promote_ready 5 request(s)",
        _sweep_total_line(monkeypatch, 20),
        "linear-budget: 731 → 672 (spent 59 this run; window resets 20:34 PT; budget: fleet)",
        _calls_line(monkeypatch, 23),
        "linear-budget: 672 → 660 (spent 12 this run; window resets 20:34 PT; budget: fleet)",
        _calls_line(monkeypatch, 6),
    )
    assert clb.run_spend_from_log(log) == [23, 6]
    rows = clb.aggregate([("agent-bureau", "Reconcile", log)])
    assert [(r["runs"], r["total"], r["max"], r["unknown_lines"]) for r in rows] == [
        (1, 29, 29, 0),
    ]


def test_a_log_from_before_the_calls_line_reads_as_it_always_did(monkeypatch):
    """No `linear-calls:` anywhere: the sweep's total, else the budget lines."""
    sweep = _log(_sweep_total_line(monkeypatch, 20),
                 "linear-budget: 731 → 672 (spent 59 this run; window resets 20:34 PT)")
    plain = _log("linear-budget: 900 → 895 (spent 5 this run; window resets 16:00 PT)")
    assert clb.run_spend_from_log(sweep) == [20]
    assert clb.run_spend_from_log(plain) == [5]


def test_each_calls_line_lands_in_the_bucket_it_names(monkeypatch):
    """DRE-5589's split, on the new line: the planner's OAuth bucket keeps its
    own row; `fleet` and `undeclared` are the fleet key's, today's row."""
    monkeypatch.setenv(linear_ops.IDENTITY_ENV, "fleet")
    monkeypatch.setenv(linear_ops.HOME_ENV, "planner-oauth")
    monkeypatch.setenv("LINEAR_API_KEY", "Bearer planner")
    monkeypatch.setenv(linear_ops.FALLBACK_ENV, "lin_api_fleet")
    monkeypatch.setattr(linear_ops, "process_token", lambda: "77@__run-planner")
    linear_ops._reset_budget_state()
    linear_ops._budget["calls"] = 60
    (planner,) = linear_ops.calls_lines()
    assert planner.endswith("; budget: planner-oauth)")
    monkeypatch.delenv(linear_ops.HOME_ENV)
    monkeypatch.delenv(linear_ops.FALLBACK_ENV)
    rows = clb.aggregate([
        ("atlas", "Agent Plan", _log(planner, _calls_line(monkeypatch, 6))),
        ("atlas", "Agent Plan", _log(_calls_line(monkeypatch, 2, identity="nobody"))),
    ])
    got = {(r["workflow"], r["budget"]): (r["runs"], r["total"]) for r in rows}
    assert got == {
        ("Agent Plan", "planner-oauth"): (1, 60),
        ("Agent Plan", ""): (2, 8),
    }
    assert "Agent Plan [planner-oauth]" in clb.render_table(rows, [])


def test_a_calls_marker_mid_line_is_not_read(monkeypatch):
    """Same rule as the other two markers: GitHub echoes a step's script into
    the log, and a script that names the marker is not a process printing it."""
    log = _log(
        "\x1b[36;1mgrep '^linear-calls:' sweep.log\x1b[0m",
        "echo \"linear-calls: 99 request(s) this run (budget: fleet)\"",
        _calls_line(monkeypatch, 3),
    )
    assert clb.run_spend_from_log(log) == [3]


# ── Stage 2 item 28 (H8, K2/K4) ─────────────────────────────────────────────
def test_echoed_calls_line_counted_once(monkeypatch):
    """K2. A step that `cat`s a log the process already printed puts its lines
    in the run log twice — at the start of the line, where the reader looks.
    The process token says it is the same process, so it is counted once."""
    budget = "linear-budget: 900 → 870 (spent 30 this run; window resets 16:00 PT; budget: fleet)"
    sweep = _calls_line(monkeypatch, 25, token="4242@__run_3-a1b2c3")
    other = _calls_line(monkeypatch, 4, token="4250@__run_4-d4e5f6")
    log = _log(budget, sweep, "linear-budget: 870 → 866 (spent 4 this run; "
               "window resets 16:00 PT; budget: fleet)", other,
               "== the sweep's log, kept ==", budget, sweep)
    assert clb.run_spend_from_log(log) == [25, 4]
    rows = clb.aggregate([("agent-bureau", "Reconcile", log)])
    assert [(r["budget"], r["runs"], r["total"], r["max"], r["unknown_lines"])
            for r in rows] == [("", 1, 29, 29, 0)]


def test_mixed_run_reports_undeclared(monkeypatch):
    """K4. One process said its own count; another printed only a budget line
    (killed before it could say more, or a script from before the line). The
    second's spend is not covered by any count, and it is reported as
    `undeclared` — in its own row, never dropped and never folded into the
    fleet key's exact number."""
    log = _log(
        "linear-budget: 900 → 870 (spent 30 this run; window resets 16:00 PT; budget: fleet)",
        _calls_line(monkeypatch, 25),
        "linear-budget: 870 → 858 (spent 12 this run; window resets 16:00 PT; budget: fleet)",
        "linear-budget: unknown (no rate-limit headers seen; budget: fleet)",
    )
    assert clb.run_spend_from_log(log) == [25, 12, None]
    rows = clb.aggregate([("portico", "Agent Task", log)])
    got = {r["budget"]: (r["runs"], r["total"], r["unknown_lines"]) for r in rows}
    assert got == {"": (1, 25, 0), "undeclared": (1, 12, 1)}
    text = clb.render_table(rows, [])
    assert "Agent Task [undeclared]" in text
    assert text.splitlines()[-1].split()[:3] == ["TOTAL", "2", "37"]


def test_a_run_whose_every_budget_line_is_covered_has_no_undeclared_row(monkeypatch):
    log = _log(
        "linear-budget: 900 → 870 (spent 30 this run; window resets 16:00 PT; budget: fleet)",
        _calls_line(monkeypatch, 25),
    )
    rows = clb.aggregate([("portico", "Agent Task", log)])
    assert [r["budget"] for r in rows] == [""]


def test_a_split_process_covers_its_one_budget_line(monkeypatch):
    """A process that fell back prints ONE budget line and a calls line per
    bucket. Both calls lines belong to that budget line; it is covered once
    and the next process's budget line is not."""
    token = "4300@__run_2-0a0b0c"
    log = _log(
        "linear-budget: 351 → 340 (spent 11 this run; window resets 16:00 PT; budget: fleet)",
        _calls_line(monkeypatch, 40, token=token).replace("budget: fleet", "budget: planner-oauth"),
        _calls_line(monkeypatch, 11, token=token),
        "linear-budget: 340 → 335 (spent 5 this run; window resets 16:00 PT; budget: fleet)",
    )
    rows = clb.aggregate([("atlas", "Agent Plan", log)])
    got = {r["budget"]: r["total"] for r in rows}
    assert got == {"planner-oauth": 40, "": 11, "undeclared": 5}


# ── DRE-3242: a run whose log is not yet retrievable is retried, then named ──
# `gh run view --log` answers nonzero for a run still queued or running, and a
# look a few seconds later usually succeeds. On 2026-09-05 at 17:20 PT a single
# read dropped 136 of 222 runs into a bare count. Each run is now read up to
# three times, an exit-0 empty log (every job skipped) is its own note and
# never retried, and a run still unreadable is named. These drive `main` off a
# fake standing in for `_gh` — no live `gh` — and the back-off off a recorder.

import json as _json  # noqa: E402
import subprocess  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402

_IN_PROGRESS = ("run {id} is still in progress; logs will be available when it "
                "is complete")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """The back-off seam, set to zero for every test in this file: the delays
    asked for are recorded and nothing sleeps."""
    asked: list[float] = []
    monkeypatch.setattr(clb, "_sleep", asked.append, raising=False)
    return asked


class _FakeGh:
    """Stands in for `_gh`. `run list` answers each repo's runs, created now;
    `run view <id> --log` answers the next of that run's scripted replies,
    each `(rc, stdout, stderr)`, repeating the last one."""

    def __init__(self, runs: dict[str, list[tuple[int, str]]],
                 replies: dict[int, list[tuple[int, str, str]]]):
        self.runs, self.replies = runs, replies
        self.views: dict[int, int] = {}

    def __call__(self, *args: str) -> subprocess.CompletedProcess:
        if args[:2] == ("run", "list"):
            repo = args[args.index("-R") + 1]
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            out = [{"databaseId": rid, "workflowName": wf, "createdAt": now}
                   for rid, wf in self.runs.get(repo, [])]
            return subprocess.CompletedProcess(args, 0, _json.dumps(out), "")
        assert args[:2] == ("run", "view") and "--log" in args, args
        rid = int(args[2])
        n = self.views[rid] = self.views.get(rid, 0) + 1
        script = self.replies[rid]
        rc, out, err = script[min(n, len(script)) - 1]
        return subprocess.CompletedProcess(args, rc, out, err)


def _drive(monkeypatch, capsys, fake: _FakeGh,
           repo_map: dict[str, str]) -> tuple[str, str]:
    monkeypatch.setattr(clb, "_gh", fake)
    monkeypatch.setattr(clb, "load_repo_map", lambda: repo_map)
    assert clb.main(["--hours", "1"]) == 0
    got = capsys.readouterr()
    return got.out, got.err


_SPENT_20 = "linear-budget: 900 → 880 (spent 20 this run; window resets 16:00 PT; budget: fleet)"


def test_a_run_still_in_progress_is_read_again_and_counted(monkeypatch, capsys, _no_sleep):
    """Nonzero twice — `still in progress` — then the log: the run is counted
    in its row, the footer says nothing was skipped, and `run view` was called
    exactly three times for it, with a short back-off between the reads."""
    rid = 34310166200
    busy = (1, "", _IN_PROGRESS.format(id=rid))
    fake = _FakeGh({"o/atlas": [(rid, "Reconcile")]},
                   {rid: [busy, busy, (0, _log(_SPENT_20), "")]})
    out, _err = _drive(monkeypatch, capsys, fake, {"atlas": "o/atlas"})
    assert fake.views[rid] == 3
    row = next(line for line in out.splitlines() if line.startswith("atlas"))
    assert row.split()[2:4] == ["1", "20"]
    assert out.splitlines()[-1].split()[:3] == ["TOTAL", "1", "20"]
    assert "skipped" not in out and "empty log" not in out
    assert len(_no_sleep) == 2 and 0 < sum(_no_sleep) < 30


def test_a_run_never_readable_is_named_after_three_attempts(monkeypatch, capsys, _no_sleep):
    """Nonzero every time: the reads stop at three, the run is counted in the
    skipped number and named `<slug> <run id>`, and the last thing `gh` said
    for it goes to stderr."""
    gone, fine = 34310170001, 34310170002
    fake = _FakeGh(
        {"o/atlas": [(fine, "Medic")], "o/portico": [(gone, "Reconcile")]},
        {gone: [(1, "", "log not found: 34310170001")],
         fine: [(0, _log(_SPENT_20), "")]},
    )
    out, err = _drive(monkeypatch, capsys, fake,
                      {"atlas": "o/atlas", "portico": "o/portico"})
    assert fake.views == {fine: 1, gone: 3}
    tail = out.splitlines()[-1]
    assert tail.split()[:3] == ["TOTAL", "1", "20"]
    assert ("(1 run(s) skipped: log not available after 3 attempts — "
            "portico 34310170001)") in tail
    assert "empty log" not in tail
    assert "34310170001" in err and "log not found: 34310170001" in err
    assert len(_no_sleep) == 2


def test_an_empty_log_is_read_once_and_noted_on_its_own(monkeypatch, capsys, _no_sleep):
    """Exit 0 and nothing printed: a completed run whose every job was
    skipped. A second look returns the same nothing, so there is one read; it
    is not a run in any row and not in the "log not available" count — it is
    counted in its own note."""
    rid = 34310180000
    fake = _FakeGh({"o/atlas": [(rid, "Agent Task")]}, {rid: [(0, "", "")]})
    out, err = _drive(monkeypatch, capsys, fake, {"atlas": "o/atlas"})
    assert fake.views == {rid: 1}
    assert _no_sleep == []
    tail = out.splitlines()[-1]
    assert tail.split()[:3] == ["TOTAL", "0", "0"]
    assert "skipped" not in out
    assert "(1 run(s) empty log: no job ran)" in tail
    assert err == ""


def test_the_docstring_no_longer_says_an_unreadable_run_is_skipped():
    """The module says what it does: a run whose log cannot be read is retried
    and then named, not quietly skipped into a count."""
    doc = " ".join(clb.__doc__.split())
    assert "is skipped and counted in the `skipped` note" not in doc
    assert "named" in doc and "empty log" in doc
