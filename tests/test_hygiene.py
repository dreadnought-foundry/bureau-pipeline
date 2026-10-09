"""RED-first: the hygiene agent's core (DRE-5368).

The hygiene agent is an hourly pass that clears the mechanical rows a person
clears by hand today. This card builds the CORE and no lane: `scripts/hygiene.py`
reads the board once, discovers lane modules by the glob `scripts/hygiene_*.py`,
hands each one the cards and pull requests in its leg's scope, and executes what
they return through one guarded seam. Three rules live in the core and nowhere
else, so the lane cards cannot disagree about them:

  1. the idempotency key is (tag, cause), applied by the write seam;
  2. the guard reads the SHAPE of every write — never a substring;
  3. the standing summary card is handed to no lane, ever.

No lane module ships with this card, so every test that needs one writes it into
a temporary directory and points `hygiene.LANE_DIR` at it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hygiene.py -v
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import hygiene  # noqa: E402
import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402

#: 21:05 UTC on 2026-09-30 is 14:05 in Pacific Daylight Time.
NOW = datetime(2026, 9, 30, 21, 5, tzinfo=UTC)
CLOCK = "14:05 PT"

HOME = "dreadnought-foundry"
PIPELINE = "dreadnought-foundry/bureau-pipeline"
PORTICO = "dreadnought-foundry/portico"
ATLAS = "EveryBite/atlas"
SUMMARY_CARD = "DRE-900"

#: The five lanes the board read covers, spelled out here rather than imported:
#: a test that reads the writer's own constant proves the writer agrees with
#: itself.
FIVE = ["Green Light", "Todo", "Triage", "In Progress", "In Review"]
# What the board read covers once Hand-work is live (DRE-5320): the five, plus
# the lane `board_lanes()` adds when the contract declares it.
SIX = FIVE + ["Hand-work"]

#: The twelve act names and their tags, exactly as the card writes them.
ACTS = {
    "hygiene-gate-redispatch": "hyg-gate-redispatched",
    "hygiene-branch-refresh": "hyg-branch-refreshed",
    "hygiene-check-rerun": "hyg-check-rerun",
    "hygiene-decision-needed": "hyg-decision-needed",
    "hygiene-pr-close": "hyg-pr-closed",
    "hygiene-resend-to-planning": "hyg-resent-to-planning",
    "hygiene-card-close": "hyg-card-closed",
    "hygiene-proof-close": "hyg-proof-closed",
    "hygiene-triage-return": "hyg-triage-returned",
    "hygiene-review-move": "hyg-moved-to-review",
    "hygiene-card-cancel": "hyg-card-canceled",
    "hygiene-cause-name": "hyg-cause-named",
    "hygiene-hold-clear": "hyg-hold-cleared",
}


# --------------------------------------------------------------------------- #
# builders                                                                     #
# --------------------------------------------------------------------------- #


def card(ident, lane, slug=None, *, children=(), comments=(), labels=(), archived_at=None):
    """One card in the `board_snapshot.CARD_QUERY` shape. Comments are given
    oldest-first and stored NEWEST FIRST, the way Linear answers the window.
    `archived_at` adds the `archivedAt` a read of a closed card carries; the
    board read selects none, so a card built without it has no such key."""
    names = list(labels) + ([f"repo:{slug}"] if slug else [])
    extra = {} if archived_at is None else {"archivedAt": archived_at}
    return {
        **extra,
        "id": f"uuid-{ident}",
        "identifier": ident,
        "title": f"synthetic {ident}",
        "description": "synthetic",
        "createdAt": "2026-09-01T00:00:00.000Z",
        "updatedAt": "2026-09-01T00:00:00.000Z",
        "state": {"name": lane},
        "labels": {"nodes": [{"name": n} for n in names]},
        "parent": None,
        "children": {"nodes": [
            {"id": f"uuid-{c}", "identifier": c, "createdAt": None,
             "state": {"name": "Todo"}} for c in children
        ]},
        "comments": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [{"body": b, "createdAt": "2026-09-30T00:00:00.000Z",
                       "user": None} for b in reversed(list(comments))],
        },
        "relations": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        "inverseRelations": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        "history": {"nodes": []},
    }


def board_doc(*cards):
    lanes: dict = {}
    for c in cards:
        lanes.setdefault(c["state"]["name"], []).append(c)
    return {"taken_at": "2026-09-30T21:00:00Z", "lanes": lanes}


def pr(number, *, comments=()):
    return {"number": number, "title": f"pr {number}", "body": "",
            "headRefName": f"agent/DRE-{number}", "headRefOid": "a" * 40,
            "baseRefName": "main", "mergeStateStatus": "BEHIND",
            "isDraft": False, "updatedAt": "2026-09-30T20:00:00Z",
            "comments": [{"body": b} for b in comments],
            "statusCheckRollup": []}


class FakeGh:
    """The read-only `gh` a lane sees, answering from a map of argv joined with
    single spaces — the fixture contract's `gh` block."""

    def __init__(self, answers=None):
        self.answers = dict(answers or {})
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        key = " ".join(argv)
        if key in self.answers:
            return self.answers[key]
        if argv[:3] == ["gh", "pr", "list"]:
            return "[]"
        raise AssertionError(f"the fake gh has no answer for {key!r}")


def context(owner=HOME, *, gh=None, dry_run=False, summary_card=SUMMARY_CARD):
    return hygiene.make_context(
        owner,
        gh=gh or FakeGh(),
        linear=lambda q, v=None: {},
        dry_run=dry_run,
        now=NOW,
        summary_card=summary_card,
    )


LANE_TEMPLATE = '''\
import json
import hygiene

LANE = {lane!r}
SEEN = {seen!r}


def _seen(ctx, lane, c):
    with open(SEEN, "a", encoding="utf-8") as fh:
        fh.write(json.dumps([ctx.owner, lane, c["identifier"]]) + "\\n")


def plan(board, ctx):
    out = []
{body}
    return out
'''


def write_lane(directory: Path, name: str, body: str, lane: str = "Todo") -> Path:
    seen = directory / f"{name}.seen"
    path = directory / f"hygiene_{name}.py"
    path.write_text(LANE_TEMPLATE.format(
        lane=lane, seen=str(seen), body=textwrap.indent(textwrap.dedent(body), "    ")
    ), encoding="utf-8")
    return seen


def seen(path: Path) -> list:
    if not path.exists():
        return []
    return [tuple(json.loads(line)) for line in path.read_text().splitlines()]


RECORD_EVERY_CARD = """
for lane in list(board.lanes):
    for c in board.cards(ctx, lane):
        _seen(ctx, lane, c)
"""


@pytest.fixture
def lanes(tmp_path, monkeypatch):
    monkeypatch.setattr(hygiene, "LANE_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def sent(monkeypatch):
    """The write seam, replaced by a recorder: every write the executor
    sends, in order."""
    out: list = []
    monkeypatch.setattr(hygiene, "send", lambda write, ctx: out.append(write))
    return out


# --------------------------------------------------------------------------- #
# read — one paged board read, and the budget floor                            #
# --------------------------------------------------------------------------- #


class FakePaged:
    def __init__(self, cards=(), remaining=None):
        self.cards = list(cards)
        self.remaining = remaining
        self.calls: list = []

    def __call__(self, query, variables=None, **_kwargs):
        self.calls.append((query, variables))
        if self.remaining is not None:
            linear_ops._budget["last"] = self.remaining
            linear_ops._budget["first"] = self.remaining
        return list(self.cards)


@pytest.fixture
def no_gql(monkeypatch):
    calls: list = []

    def refuse(query, variables=None):
        calls.append(query)
        raise AssertionError("read sent a Linear request past the one board read")

    monkeypatch.setattr(linear_ops, "gql", refuse)
    return calls


def _read(tmp_path, monkeypatch, paged, floor=None):
    monkeypatch.setattr(linear_ops, "gql_paged", paged)
    output = tmp_path / "github_output"
    output.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    if floor is None:
        monkeypatch.delenv("HYGIENE_BUDGET_FLOOR", raising=False)
    else:
        monkeypatch.setenv("HYGIENE_BUDGET_FLOOR", str(floor))
    out = tmp_path / "board.json"
    code = hygiene.main(["read", "--out", str(out)])
    return code, out, output.read_text()


class TestRead:
    def test_one_paged_read_over_exactly_the_board_lanes(self, tmp_path, monkeypatch, no_gql):
        paged = FakePaged([card("DRE-1", "Todo"), card("DRE-2", "Triage")], remaining=2000)
        code, out, _ = _read(tmp_path, monkeypatch, paged)
        assert code == 0
        assert len(paged.calls) == 1
        query, variables = paged.calls[0]
        assert sorted(variables["states"]) == sorted(SIX)
        assert len(variables["states"]) == 6
        assert no_gql == []

    def test_the_read_is_the_board_snapshot_query(self, tmp_path, monkeypatch, no_gql):
        import board_snapshot

        paged = FakePaged([], remaining=2000)
        _read(tmp_path, monkeypatch, paged)
        assert paged.calls[0][0] == board_snapshot.CARD_QUERY

    def test_the_board_file_is_keyed_by_lane(self, tmp_path, monkeypatch, no_gql):
        paged = FakePaged([card("DRE-1", "Todo"), card("DRE-2", "Triage")], remaining=2000)
        _, out, _ = _read(tmp_path, monkeypatch, paged)
        doc = json.loads(out.read_text())
        assert doc["taken_at"].endswith("Z")
        assert [c["identifier"] for c in doc["lanes"]["Todo"]] == ["DRE-1"]
        assert [c["identifier"] for c in doc["lanes"]["Triage"]] == ["DRE-2"]
        assert set(doc["lanes"]) == set(SIX)

    def test_hand_work_is_read_once_the_contract_declares_it_live(self):
        # DRE-5315 declared it `arriving`, and no live reader reads an arriving
        # lane. DRE-5320 flipped it to live, and the read took it with nothing
        # here edited; an arriving Hand-work is still left out.
        assert "Hand-work" in lane_contract.lane_names()
        assert sorted(hygiene.board_lanes()) == sorted(SIX)
        contract = json.loads((ROOT / "config" / "lane-contract.json").read_text())
        hand_work = next(entry for entry in contract["lanes"] if entry["name"] == "Hand-work")
        assert hand_work["status"] == "live"
        hand_work["status"] = "arriving"
        assert sorted(hygiene.board_lanes(contract)) == sorted(FIVE)

    def test_below_the_floor_it_stands_down_and_asks_nothing_more(
        self, tmp_path, monkeypatch, capsys, no_gql
    ):
        paged = FakePaged([card("DRE-1", "Todo")], remaining=42)
        code, _, output = _read(tmp_path, monkeypatch, paged, floor=100)
        assert code == 0
        assert "go=false" in output.splitlines()
        why = [line for line in output.splitlines() if line.startswith("why=")]
        assert len(why) == 1 and "42" in why[0] and "100" in why[0]
        printed = capsys.readouterr().out
        assert ("hygiene: stood down — 42 request(s) left on the fleet key, "
                "below the floor of 100") in printed
        assert len(paged.calls) == 1
        assert no_gql == []

    def test_the_floor_defaults_to_one_hundred(self, tmp_path, monkeypatch, no_gql):
        _, _, output = _read(tmp_path, monkeypatch, FakePaged([], remaining=99))
        assert "go=false" in output.splitlines()
        _, _, output = _read(tmp_path, monkeypatch, FakePaged([], remaining=100))
        assert "go=true" in output.splitlines()

    def test_at_or_above_the_floor_it_goes(self, tmp_path, monkeypatch, no_gql):
        code, _, output = _read(tmp_path, monkeypatch, FakePaged([], remaining=1500), floor=100)
        assert code == 0
        assert "go=true" in output.splitlines()
        assert any(line.startswith("why=") and "1500" in line for line in output.splitlines())

    def test_no_rate_limit_header_means_the_floor_is_not_applied(
        self, tmp_path, monkeypatch, capsys, no_gql
    ):
        assert linear_ops._budget["last"] is None
        code, _, output = _read(tmp_path, monkeypatch, FakePaged([]), floor=100)
        assert code == 0
        lines = output.splitlines()
        assert "go=true" in lines
        assert "why=hygiene: floor not applied — no rate-limit header on the board read" in lines
        assert "stood down" not in capsys.readouterr().out

    def test_an_unreadable_board_is_not_an_empty_one(self, tmp_path, monkeypatch, no_gql):
        def broken(*_a, **_k):
            raise linear_ops.LinearError("linear error from api.linear.app: 500")

        code, out, output = _read(tmp_path, monkeypatch, broken)
        assert code == 1
        assert "go=false" in output.splitlines()
        assert not out.exists()

    def test_without_github_output_it_still_reads_and_exits_zero(self, tmp_path, monkeypatch, no_gql):
        monkeypatch.setattr(linear_ops, "gql_paged", FakePaged([], remaining=10))
        monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
        assert hygiene.main(["read", "--out", str(tmp_path / "b.json")]) == 0


# --------------------------------------------------------------------------- #
# run — scope, discovery, the standing card, dry run                           #
# --------------------------------------------------------------------------- #


class TestScope:
    def test_each_of_the_owners_repos_is_listed_exactly_once(self, lanes, sent):
        gh = FakeGh()
        hygiene.run_leg(board_doc(), context(HOME, gh=gh))
        listed = [c for c in gh.calls if c[:3] == ["gh", "pr", "list"]]
        repos = sorted(c[c.index("--repo") + 1] for c in listed)
        mapped = json.loads((ROOT / "config" / "repo-map.json").read_text())
        assert repos == sorted(r for r in mapped.values() if r.startswith(HOME + "/"))
        assert len(listed) == len(set(repos))
        assert all(c == hygiene.pr_list_argv(c[c.index("--repo") + 1]) for c in listed)

    def test_another_owners_leg_lists_only_its_own_repos(self, lanes, sent):
        gh = FakeGh()
        hygiene.run_leg(board_doc(), context("EveryBite", gh=gh))
        assert [c[c.index("--repo") + 1] for c in gh.calls] == [ATLAS]

    def test_the_pr_list_asks_for_the_contract_fields(self):
        argv = hygiene.pr_list_argv(PIPELINE)
        assert argv[:3] == ["gh", "pr", "list"]
        assert "--state" in argv and argv[argv.index("--state") + 1] == "open"
        fields = argv[argv.index("--json") + 1].split(",")
        assert fields == ["number", "title", "body", "headRefName", "headRefOid",
                          "baseRefName", "mergeStateStatus", "isDraft",
                          "updatedAt", "comments", "statusCheckRollup"]

    def test_the_open_pull_requests_reach_the_lane_keyed_by_repo(self, lanes, sent):
        record = lanes / "prs.json"
        write_lane(lanes, "prs", f"""
            open({str(record)!r}, "w").write(json.dumps(board.prs))
        """)
        gh = FakeGh({" ".join(hygiene.pr_list_argv(PIPELINE)): json.dumps([pr(7)])})
        hygiene.run_leg(board_doc(), context(HOME, gh=gh))
        prs = json.loads(record.read_text())
        assert [p["number"] for p in prs[PIPELINE]] == [7]
        assert ATLAS not in prs

    def test_a_card_for_another_owners_repo_reaches_no_lane_on_this_leg(self, lanes, sent):
        log = write_lane(lanes, "probe", RECORD_EVERY_CARD)
        doc = board_doc(card("DRE-1", "Todo", "atlas"), card("DRE-2", "Todo", "portico"))
        hygiene.run_leg(doc, context(HOME))
        hygiene.run_leg(doc, context("EveryBite"))
        assert sorted(seen(log)) == [
            ("EveryBite", "Todo", "DRE-1"),
            (HOME, "Todo", "DRE-2"),
        ]

    def test_an_unlabeled_or_unmapped_card_reaches_a_lane_only_on_the_home_leg(self, lanes, sent):
        log = write_lane(lanes, "probe", RECORD_EVERY_CARD)
        doc = board_doc(card("DRE-1", "Todo"), card("DRE-2", "Triage", "no-such-repo"))
        for owner in (HOME, "EveryBite", "DeltaSolv"):
            hygiene.run_leg(doc, context(owner))
        assert sorted(seen(log)) == [(HOME, "Todo", "DRE-1"), (HOME, "Triage", "DRE-2")]


class TestTheStandingCard:
    @pytest.mark.parametrize("lane", ["Todo", "Hand-work"])
    def test_the_summary_card_reaches_no_lane_on_any_leg(self, lanes, sent, lane):
        log = write_lane(lanes, "probe", RECORD_EVERY_CARD)
        # with and without a repo label: neither scope rule may let it through
        doc = board_doc(card(SUMMARY_CARD, lane), card("DRE-1", lane, "portico"))
        doc["lanes"].setdefault("Green Light", []).append(
            card(SUMMARY_CARD, "Green Light", "atlas"))
        for owner in (HOME, "EveryBite", "DeltaSolv"):
            hygiene.run_leg(doc, context(owner))
        cards = [ident for _, _, ident in seen(log)]
        assert cards == ["DRE-1"]
        assert SUMMARY_CARD not in cards

    def test_the_board_a_lane_reads_directly_does_not_carry_it_either(self, lanes, sent):
        log = write_lane(lanes, "probe", """
            for lane, cards in board.lanes.items():
                for c in cards:
                    _seen(ctx, lane, c)
        """)
        hygiene.run_leg(board_doc(card(SUMMARY_CARD, "Todo")), context(HOME))
        assert seen(log) == []

    def test_a_write_against_the_summary_card_is_refused(self):
        ctx = context(HOME)
        with pytest.raises(hygiene.Forbidden):
            hygiene.guard(hygiene.linear_state(card(SUMMARY_CARD, "Todo"), "Done"), ctx)


def _close(ident="DRE-1", cause="PR dreadnought-foundry/portico#7 merged at aaaaaaa",
           slug="portico", comments=()):
    c = card(ident, "Todo", slug, comments=comments)
    body = hygiene.receipt("hygiene-card-close", cause, ["portico#7"], NOW)
    return c, body


CLOSE_LANE = """
for c in board.cards(ctx, "Todo"):
    cause = "PR dreadnought-foundry/portico#7 merged at aaaaaaa"
    body = hygiene.receipt("hygiene-card-close", cause, ["portico#7"], ctx.now)
    out.append(hygiene.Action(
        lane=LANE, target=c["identifier"], act="hygiene-card-close", cause=cause,
        evidence=["portico#7"],
        writes=[hygiene.linear_comment(c, body), hygiene.linear_state(c, "Done")],
    ))
    out.append(hygiene.Left(lane=LANE, target="DRE-77", why="a person must look",
                            recommendation="close it by hand"))
"""


class TestDiscoveryAndExecution:
    def test_a_lane_module_in_the_lane_dir_is_discovered_by_the_glob(self, lanes):
        write_lane(lanes, "alpha", "")
        (lanes / "not_a_lane.py").write_text("LANE = 'x'\ndef plan(b, c):\n    return []\n")
        found = hygiene.discover()
        assert [m.LANE for m in found] == ["Todo"]

    def test_the_default_lane_dir_is_the_scripts_directory(self):
        assert Path(hygiene.LANE_DIR).resolve() == (ROOT / "scripts").resolve()

    def test_its_actions_are_executed_and_its_left_rows_land_in_the_ledger(self, lanes, sent):
        write_lane(lanes, "closer", CLOSE_LANE)
        ledger = hygiene.run_leg(board_doc(card("DRE-1", "Todo", "portico")), context(HOME))
        assert ledger["owner"] == HOME
        [action] = ledger["actions"]
        assert action["outcome"] == "executed"
        assert action["act"] == "hygiene-card-close"
        assert action["target"] == "DRE-1"
        assert len(action["writes"]) == 2
        assert [w.kind for w in sent] == ["linear_comment", "linear_state"]
        assert ledger["left"] == [{"lane": "Todo", "target": "DRE-77",
                                   "why": "a person must look",
                                   "recommendation": "close it by hand"}]

    def test_a_write_that_fails_after_the_guard_is_failed_and_exits_one(
        self, lanes, monkeypatch, tmp_path
    ):
        write_lane(lanes, "closer", CLOSE_LANE)

        def broken(write, ctx):
            raise RuntimeError("linear said no")

        monkeypatch.setattr(hygiene, "send", broken)
        board = tmp_path / "board.json"
        board.write_text(json.dumps(board_doc(card("DRE-1", "Todo", "portico"))))
        monkeypatch.setattr(hygiene, "GH_RUNNER", FakeGh())
        monkeypatch.setenv("HYGIENE_CARD", SUMMARY_CARD)
        monkeypatch.delenv("HYGIENE_DRY_RUN", raising=False)
        ledger = tmp_path / "ledger.json"
        code = hygiene.main(["run", "--board", str(board), "--owner", HOME,
                             "--ledger", str(ledger)])
        assert code == 1
        [action] = json.loads(ledger.read_text())["actions"]
        assert action["outcome"] == "failed"

    def test_a_completed_pass_exits_zero_and_writes_the_ledger(self, lanes, monkeypatch, tmp_path, sent):
        write_lane(lanes, "closer", CLOSE_LANE)
        board = tmp_path / "board.json"
        board.write_text(json.dumps(board_doc(card("DRE-1", "Todo", "portico"))))
        monkeypatch.setattr(hygiene, "GH_RUNNER", FakeGh())
        monkeypatch.setenv("HYGIENE_CARD", SUMMARY_CARD)
        monkeypatch.delenv("HYGIENE_DRY_RUN", raising=False)
        ledger = tmp_path / "ledger.json"
        assert hygiene.main(["run", "--board", str(board), "--owner", HOME,
                             "--ledger", str(ledger)]) == 0
        doc = json.loads(ledger.read_text())
        assert set(doc) == {"owner", "taken_at", "actions", "left"}
        assert set(doc["actions"][0]) == {"lane", "target", "act", "cause",
                                          "evidence", "writes", "outcome"}

    def test_a_lane_that_returns_something_else_is_refused(self, lanes, sent):
        write_lane(lanes, "odd", "out.append({'act': 'hygiene-card-close'})")
        with pytest.raises(TypeError):
            hygiene.run_leg(board_doc(), context(HOME))

    def test_an_act_the_table_does_not_carry_is_refused(self, lanes, sent):
        write_lane(lanes, "odd", """
            out.append(hygiene.Action(lane=LANE, target="DRE-1", act="merge-it",
                                      cause="x", evidence=["y"], writes=[]))
        """)
        with pytest.raises(hygiene.Forbidden):
            hygiene.run_leg(board_doc(), context(HOME))
        assert sent == []


class TestDryRun:
    def test_every_write_is_printed_as_would_and_the_seam_is_never_reached(
        self, lanes, monkeypatch, tmp_path, capsys
    ):
        write_lane(lanes, "closer", CLOSE_LANE)

        def unreachable(*_a, **_k):
            raise AssertionError("dry run reached the write seam")

        monkeypatch.setattr(hygiene, "send", unreachable)
        for name in ("cmd_state", "cmd_comment", "add_label", "remove_label",
                     "_add_blocked_by", "gql"):
            monkeypatch.setattr(linear_ops, name, unreachable)
        board = tmp_path / "board.json"
        board.write_text(json.dumps(board_doc(card("DRE-1", "Todo", "portico"),
                                              card("DRE-2", "Todo", "bureau-pipeline"))))
        gh = FakeGh()
        monkeypatch.setattr(hygiene, "GH_RUNNER", gh)
        monkeypatch.setenv("HYGIENE_CARD", SUMMARY_CARD)
        ledger = tmp_path / "ledger.json"
        code = hygiene.main(["run", "--board", str(board), "--owner", HOME,
                             "--ledger", str(ledger), "--dry-run"])
        assert code == 0
        would = [line for line in capsys.readouterr().out.splitlines()
                 if line.startswith("would: ")]
        assert len(would) == 4  # two cards, two writes each
        actions = json.loads(ledger.read_text())["actions"]
        assert {a["outcome"] for a in actions} == {"would"}
        assert all(c[:3] == ["gh", "pr", "list"] for c in gh.calls)

    def test_the_environment_switch_is_a_dry_run_too(self, lanes, monkeypatch, tmp_path, capsys):
        write_lane(lanes, "closer", CLOSE_LANE)
        monkeypatch.setattr(hygiene, "send", lambda *_a: pytest.fail("seam reached"))
        board = tmp_path / "board.json"
        board.write_text(json.dumps(board_doc(card("DRE-1", "Todo", "portico"))))
        monkeypatch.setattr(hygiene, "GH_RUNNER", FakeGh())
        monkeypatch.setenv("HYGIENE_DRY_RUN", "1")
        assert hygiene.main(["run", "--board", str(board), "--owner", HOME,
                             "--ledger", str(tmp_path / "l.json")]) == 0
        assert "would: " in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# the idempotency key — (tag, cause), applied by the seam                      #
# --------------------------------------------------------------------------- #


class TestIdempotency:
    def test_the_same_tag_and_the_same_cause_is_suppressed(self, lanes, sent):
        write_lane(lanes, "closer", CLOSE_LANE)
        _, body = _close()
        doc = board_doc(card("DRE-1", "Todo", "portico", comments=[body]))
        [action] = hygiene.run_leg(doc, context(HOME))["actions"]
        assert action["outcome"] == "suppressed"
        assert sent == []

    def test_an_older_clock_on_the_same_receipt_still_suppresses(self, lanes, sent):
        write_lane(lanes, "closer", CLOSE_LANE)
        earlier = hygiene.receipt("hygiene-card-close",
                                  "PR dreadnought-foundry/portico#7 merged at aaaaaaa",
                                  ["portico#7"], datetime(2026, 9, 30, 17, 0, tzinfo=UTC))
        doc = board_doc(card("DRE-1", "Todo", "portico", comments=[earlier]))
        [action] = hygiene.run_leg(doc, context(HOME))["actions"]
        assert action["outcome"] == "suppressed"

    def test_the_same_tag_with_a_different_cause_is_executed(self, lanes, sent):
        write_lane(lanes, "closer", CLOSE_LANE)
        other = hygiene.receipt("hygiene-card-close",
                                "PR dreadnought-foundry/portico#6 merged at bbbbbbb",
                                ["portico#6"], NOW)
        doc = board_doc(card("DRE-1", "Todo", "portico", comments=[other]))
        [action] = hygiene.run_leg(doc, context(HOME))["actions"]
        assert action["outcome"] == "executed"
        assert len(sent) == 2

    def test_the_same_cause_under_another_tag_is_executed(self, lanes, sent):
        write_lane(lanes, "closer", CLOSE_LANE)
        other = hygiene.receipt("hygiene-proof-close",
                                "PR dreadnought-foundry/portico#7 merged at aaaaaaa",
                                ["portico#7"], NOW)
        doc = board_doc(card("DRE-1", "Todo", "portico", comments=[other]))
        [action] = hygiene.run_leg(doc, context(HOME))["actions"]
        assert action["outcome"] == "executed"

    def test_a_pull_request_target_reads_its_own_comments(self, lanes, sent):
        write_lane(lanes, "refresh", """
            for p in board.prs.get("dreadnought-foundry/portico", []):
                cause = "head " + p["headRefOid"][:7] + " behind main"
                body = hygiene.receipt("hygiene-branch-refresh", cause, ["portico#7"], ctx.now)
                out.append(hygiene.Action(
                    lane=LANE, target="dreadnought-foundry/portico#7",
                    act="hygiene-branch-refresh", cause=cause, evidence=["portico#7"],
                    writes=[hygiene.gh_pr_comment("dreadnought-foundry/portico", 7, body),
                            hygiene.gh_update_branch("dreadnought-foundry/portico", 7, p["headRefOid"])],
                ))
        """, lane="Pull requests")
        key = " ".join(hygiene.pr_list_argv(PORTICO))
        posted = hygiene.receipt("hygiene-branch-refresh", "head aaaaaaa behind main",
                                 ["portico#7"], NOW)
        fresh = hygiene.run_leg(board_doc(), context(HOME, gh=FakeGh({key: json.dumps([pr(7)])})))
        assert fresh["actions"][0]["outcome"] == "executed"
        repeat = hygiene.run_leg(board_doc(), context(
            HOME, gh=FakeGh({key: json.dumps([pr(7, comments=[posted])])})))
        assert repeat["actions"][0]["outcome"] == "suppressed"

    def test_a_receipt_older_than_a_cut_short_window_still_suppresses(
        self, lanes, sent, monkeypatch
    ):
        write_lane(lanes, "closer", CLOSE_LANE)
        _, body = _close()
        busy = card("DRE-1", "Todo", "portico", comments=["chatter"] * 3)
        busy["comments"]["pageInfo"]["hasNextPage"] = True
        reads: list = []

        def whole(ident, whole_thread=False):
            reads.append((ident, whole_thread))
            return [{"body": body}, {"body": "chatter"}]

        monkeypatch.setattr(linear_ops, "comment_records", whole)
        [action] = hygiene.run_leg(board_doc(busy), context(HOME))["actions"]
        assert action["outcome"] == "suppressed"
        assert reads == [("DRE-1", True)]

    def test_a_whole_window_is_not_read_again(self, lanes, sent, monkeypatch):
        write_lane(lanes, "closer", CLOSE_LANE)
        monkeypatch.setattr(linear_ops, "comment_records",
                            lambda *_a, **_k: pytest.fail("a whole window was read again"))
        [action] = hygiene.run_leg(board_doc(card("DRE-1", "Todo", "portico")),
                                   context(HOME))["actions"]
        assert action["outcome"] == "executed"

    def test_a_card_the_lane_read_for_itself_is_read_for_its_receipts(
        self, lanes, sent, monkeypatch
    ):
        write_lane(lanes, "child", """
            child = {"id": "uuid-DRE-8", "identifier": "DRE-8",
                     "state": {"name": "Backlog"}, "children": {"nodes": []},
                     "labels": {"nodes": [{"name": "repo:portico"}]}}
            cause = "parent DRE-7 is Canceled"
            body = hygiene.receipt("hygiene-card-cancel", cause, ["DRE-7"], ctx.now)
            out.append(hygiene.Action(lane=LANE, target="DRE-8", act="hygiene-card-cancel",
                cause=cause, evidence=["DRE-7"],
                writes=[hygiene.linear_comment(child, body),
                        hygiene.linear_state(child, "Canceled")]))
        """)
        posted = hygiene.receipt("hygiene-card-cancel", "parent DRE-7 is Canceled",
                                 ["DRE-7"], NOW)
        monkeypatch.setattr(linear_ops, "comment_records",
                            lambda ident, whole_thread=False: [{"body": posted}])
        [action] = hygiene.run_leg(board_doc(), context(HOME))["actions"]
        assert action["outcome"] == "suppressed"
        assert sent == []

    def test_a_second_pass_over_the_first_pass_receipts_executes_nothing(self, lanes, sent):
        write_lane(lanes, "closer", CLOSE_LANE)
        cards = [card("DRE-1", "Todo", "portico"), card("DRE-2", "Todo", "bureau-pipeline")]
        first = hygiene.run_leg(board_doc(*cards), context(HOME))
        assert [a["outcome"] for a in first["actions"]] == ["executed", "executed"]
        for write in sent:
            if write.kind == "linear_comment":
                c = next(c for c in cards if c["identifier"] == write.card)
                c["comments"]["nodes"].insert(0, {"body": write.body})
        sent.clear()
        second = hygiene.run_leg(board_doc(*cards), context(HOME))
        assert [a["outcome"] for a in second["actions"]] == ["suppressed", "suppressed"]
        assert sent == []


# --------------------------------------------------------------------------- #
# the guard — by shape, never by substring                                     #
# --------------------------------------------------------------------------- #


class TestGuard:
    @pytest.mark.parametrize("lane", ["In Progress", "Todo", "Green Light", "Triage", "Intake"])
    def test_a_state_write_into_a_lane_the_agent_may_not_write_is_refused(self, lane):
        with pytest.raises(hygiene.Forbidden):
            hygiene.guard(hygiene.linear_state(card("DRE-1", "Triage", "portico"), lane), context())

    @pytest.mark.parametrize("lane", ["Planning", "Backlog", "In Review", "Done", "Canceled"])
    def test_the_five_lanes_the_contract_admits_it_to_pass(self, lane):
        hygiene.guard(hygiene.linear_state(card("DRE-1", "Triage", "portico"), lane), context())

    @pytest.mark.parametrize("lane", ["Done", "Canceled"])
    def test_closing_a_card_with_children_is_refused(self, lane):
        parent = card("DRE-1", "In Review", "portico", children=["DRE-2"])
        with pytest.raises(hygiene.Forbidden):
            hygiene.guard(hygiene.linear_state(parent, lane), context())

    def test_a_card_that_does_not_say_whether_it_has_children_is_not_closed(self):
        c = card("DRE-1", "In Review", "portico")
        del c["children"]
        with pytest.raises(hygiene.Forbidden):
            hygiene.guard(hygiene.linear_state(c, "Done"), context())

    # `hand-built` is the CEO's own mark, refused at the label seam (DRE-6361).
    @pytest.mark.parametrize("label", ["break-glass", "hand-built"])
    def test_the_operator_only_label_is_refused(self, label):
        with pytest.raises(hygiene.Forbidden):
            hygiene.guard(hygiene.linear_label(card("DRE-1", "Todo", "portico"),
                                               label, True), context())

    def test_an_ordinary_label_passes(self):
        hygiene.guard(hygiene.linear_label(card("DRE-1", "Todo", "portico"),
                                           "operator-step", True), context())

    def test_a_card_outside_this_legs_scope_is_refused(self):
        with pytest.raises(hygiene.Forbidden):
            hygiene.guard(hygiene.linear_state(card("DRE-1", "Todo", "atlas"), "Done"), context(HOME))

    @pytest.mark.parametrize("make", [
        lambda r: hygiene.gh_rerun(r, 123),
        lambda r: hygiene.gh_update_branch(r, 7, "a" * 40),
        lambda r: hygiene.gh_pr_comment(r, 7, _close()[1]),
        lambda r: hygiene.gh_pr_close(r, 7, _close()[1]),
        lambda r: hygiene.gh_dispatch(r, "merge-gate.yml", {"pr_number": "7"}),
    ])
    def test_a_github_write_to_a_repo_outside_this_leg_is_refused(self, make):
        with pytest.raises(hygiene.Forbidden):
            hygiene.guard(make(ATLAS), context(HOME))
        hygiene.guard(make(ATLAS), context("EveryBite"))

    def test_dispatch_admits_exactly_the_repos_own_gate_stub(self):
        ctx = context(HOME)
        hygiene.guard(hygiene.gh_dispatch(PIPELINE, "self-merge-gate.yml", {"pr_number": "7"}), ctx)
        hygiene.guard(hygiene.gh_dispatch(PORTICO, "merge-gate.yml", {"pr_number": "7"}), ctx)
        for repo, wf in ((PIPELINE, "merge-gate.yml"), (PORTICO, "self-merge-gate.yml"),
                         (PORTICO, "release-train.yml"), (PIPELINE, "agent-task.yml")):
            with pytest.raises(hygiene.Forbidden):
                hygiene.guard(hygiene.gh_dispatch(repo, wf, {"pr_number": "7"}), ctx)

    def test_the_gate_stub_is_reconciles_rule(self, monkeypatch):
        import reconcile

        for slug in ("bureau-pipeline", "portico"):
            monkeypatch.setattr(reconcile, "REPO_SLUG", slug)
            expected = reconcile.gate_workflow()
            monkeypatch.undo()
            assert hygiene.gate_stub(f"dreadnought-foundry/{slug}") == expected

    def test_the_constructors_build_exactly_the_contract_argv(self):
        body = _close()[1]
        assert hygiene.gh_dispatch("o/r", "merge-gate.yml", {"pr_number": 7}).argv == (
            "gh", "workflow", "run", "merge-gate.yml", "--repo", "o/r", "-f", "pr_number=7")
        assert hygiene.gh_rerun("o/r", 99).argv == (
            "gh", "run", "rerun", "99", "--failed", "--repo", "o/r")
        assert hygiene.gh_update_branch("o/r", 7, "abc").argv == (
            "gh", "api", "-X", "PUT", "repos/o/r/pulls/7/update-branch",
            "-f", "expected_head_sha=abc")
        assert hygiene.gh_pr_close("o/r", 7, body).argv == (
            "gh", "pr", "close", "7", "--repo", "o/r", "--comment", body)
        assert hygiene.gh_pr_comment("o/r", 7, body).argv == (
            "gh", "pr", "comment", "7", "--repo", "o/r", "--body", body)

    ADMITTED = "gh workflow run self-merge-gate.yml --repo o/r -f pr_number=7"
    REFUSED = (
        "gh pr merge 7",
        "gh api -X PUT repos/o/r/pulls/7/merge",
        "gh api -X DELETE repos/o/r/branches/main/protection",
        "gh workflow run release-train.yml --repo o/r",
        "git push",
    )

    def test_the_shape_admits_the_gate_dispatch_whose_name_says_merge(self):
        assert hygiene.gh_shape(tuple(self.ADMITTED.split())) == "gh_dispatch"

    @pytest.mark.parametrize("argv", REFUSED)
    def test_the_shape_refuses_what_no_constructor_builds(self, argv):
        with pytest.raises(hygiene.Forbidden):
            hygiene.gh_shape(tuple(argv.split()))

    @pytest.mark.parametrize("argv", REFUSED)
    def test_a_forged_argv_handed_to_the_seam_is_refused(self, argv):
        import dataclasses

        real = hygiene.gh_dispatch(PIPELINE, "self-merge-gate.yml", {"pr_number": "7"})
        forged = dataclasses.replace(real, argv=tuple(argv.split()))
        with pytest.raises(hygiene.Forbidden):
            hygiene.guard(forged, context(HOME))

    def test_a_forged_argv_naming_another_repo_than_the_write_is_refused(self):
        import dataclasses

        real = hygiene.gh_rerun(PIPELINE, 5)
        forged = dataclasses.replace(real, argv=("gh", "run", "rerun", "5", "--failed",
                                                 "--repo", PORTICO))
        with pytest.raises(hygiene.Forbidden):
            hygiene.guard(forged, context(HOME))

    def test_a_refused_lane_exits_three_and_writes_no_ledger(self, lanes, sent, monkeypatch, tmp_path):
        write_lane(lanes, "bad", """
            for c in board.cards(ctx, "Todo"):
                out.append(hygiene.Action(lane=LANE, target=c["identifier"],
                    act="hygiene-card-close", cause="x", evidence=["y"],
                    writes=[hygiene.linear_state(c, "Todo")]))
        """)
        board = tmp_path / "board.json"
        board.write_text(json.dumps(board_doc(card("DRE-1", "Todo", "portico"))))
        monkeypatch.setattr(hygiene, "GH_RUNNER", FakeGh())
        monkeypatch.delenv("HYGIENE_DRY_RUN", raising=False)
        ledger = tmp_path / "ledger.json"
        assert hygiene.main(["run", "--board", str(board), "--owner", HOME,
                             "--ledger", str(ledger)]) == 3
        assert not ledger.exists()
        assert sent == []

    def test_the_guard_runs_before_anything_is_sent(self, lanes, sent):
        write_lane(lanes, "bad", """
            for c in board.cards(ctx, "Todo"):
                body = hygiene.receipt("hygiene-card-close", "x", ["y"], ctx.now)
                out.append(hygiene.Action(lane=LANE, target=c["identifier"],
                    act="hygiene-card-close", cause="x", evidence=["y"],
                    writes=[hygiene.linear_comment(c, body),
                            hygiene.linear_state(c, "In Progress")]))
        """)
        with pytest.raises(hygiene.Forbidden):
            hygiene.run_leg(board_doc(card("DRE-1", "Todo", "portico")), context(HOME))
        assert sent == []


class TestTheReadWrapper:
    def _gh(self):
        calls: list = []
        return hygiene.read_only_gh(lambda argv: calls.append(list(argv)) or "ok"), calls

    @pytest.mark.parametrize("argv", [
        "gh pr list --repo o/r --json number,mergeStateStatus",
        "gh api repos/o/r/compare/a...b",
        "gh pr view 7 --repo o/r --json comments",
        "gh pr checks 7 --repo o/r",
        "gh run list --repo o/r --workflow self-merge-gate.yml",
        "gh run view 5 --repo o/r --log-failed",
    ])
    def test_the_five_read_verbs_and_a_flagless_api_read_are_admitted(self, argv):
        gh, calls = self._gh()
        assert gh(argv.split()) == "ok"
        assert calls == [argv.split()]

    @pytest.mark.parametrize("argv", [
        "gh pr merge 7",
        "gh run cancel 5",
        "gh api -X POST repos/o/r/issues/1/comments",
        "gh api --method PATCH repos/o/r/pulls/7",
        "gh api repos/o/r/issues/1/comments -f body=x",
        "gh api repos/o/r/issues/1/comments -F body=x",
        "gh api repos/o/r/issues/1/comments --field body=x",
        "gh api repos/o/r/issues/1/comments --raw-field body=x",
        "gh api repos/o/r/issues/1/comments --input file.json",
        "gh api -XPOST repos/o/r/issues/1/comments",
        "gh pr comment 7 --body hi",
        "gh workflow run self-merge-gate.yml",
        "git push",
    ])
    def test_anything_else_is_refused_and_never_runs(self, argv):
        gh, calls = self._gh()
        with pytest.raises(hygiene.Forbidden):
            gh(argv.split())
        assert calls == []


# The static scan: the literals no hygiene code path may carry. It lives HERE
# and not in the module it scans, because a scanner that spelled the literals
# would find them in itself.
FORBIDDEN_LITERALS = ("pr merge", "git push", "mergePullRequest", "break-glass")
FORBIDDEN_PATHS = (
    re.compile(r"pulls/[^/\s'\"]+/merge\b"),
    re.compile(r"/protection\b"),
    re.compile(r"/secrets\b"),
    re.compile(r"/environments\b"),
)


def scan(path: Path) -> list:
    text = path.read_text(encoding="utf-8")
    found = [f"{path.name}: {literal!r}" for literal in FORBIDDEN_LITERALS if literal in text]
    found += [f"{path.name}: {p.pattern}" for p in FORBIDDEN_PATHS if p.search(text)]
    return found


def hygiene_files(directory: Path) -> list:
    return [directory / "hygiene.py", *sorted(directory.glob("hygiene_*.py"))]


class TestTheStaticScan:
    def test_the_core_and_every_lane_module_carry_no_forbidden_literal(self):
        files = [ROOT / "scripts" / "hygiene.py", *sorted((ROOT / "scripts").glob("hygiene_*.py"))]
        assert files[0].exists()
        problems = [p for f in files for p in scan(f)]
        assert problems == []

    def test_the_scan_reads_the_same_glob_discovery_does(self):
        assert hygiene.LANE_GLOB == "hygiene_*.py"

    @pytest.mark.parametrize("line", [
        'RUN = ["gh", "pr merge"]',
        "os.system('git push')",
        'Q = "mutation { mergePullRequest }"',
        'LABEL = "break-glass"',
        'P = "repos/o/r/pulls/7/merge"',
        'P = f"repos/{r}/pulls/{n}/merge"',
        'P = "repos/o/r/branches/main/protection"',
        'P = "repos/o/r/actions/secrets"',
        'P = "repos/o/r/environments"',
    ])
    def test_a_future_lane_module_carrying_one_is_caught(self, tmp_path, line):
        (tmp_path / "hygiene.py").write_text("")
        (tmp_path / "hygiene_future.py").write_text(line + "\n")
        assert [p for f in hygiene_files(tmp_path) for p in scan(f)] != []

    def test_the_update_branch_path_and_the_gate_stub_are_not_caught(self, tmp_path):
        (tmp_path / "hygiene_ok.py").write_text(
            'P = "repos/o/r/pulls/7/update-branch"\nW = "self-merge-gate.yml"\n'
            'F = "mergeStateStatus"\n')
        assert scan(tmp_path / "hygiene_ok.py") == []


# --------------------------------------------------------------------------- #
# receipts                                                                     #
# --------------------------------------------------------------------------- #


class TestReceipt:
    def test_the_first_line_names_the_tag_the_cause_and_the_pt_time(self):
        body = hygiene.receipt("hygiene-check-rerun", "run 123 failed on a runner fault",
                               ["run 123", "check tests"], NOW)
        lines = body.splitlines()
        assert lines[0] == f"🧹 hygiene: hyg-check-rerun — run 123 failed on a runner fault · {CLOCK}"
        assert lines[1] == "evidence: run 123, check tests"

    def test_it_ends_in_the_acts_trailer(self):
        body = hygiene.receipt("hygiene-check-rerun", "run 123", ["run 123"], NOW)
        fields = pipeline_act.read_trailer(body)
        assert fields["act"] == "hygiene-check-rerun"
        assert fields["tag"] == "hyg-check-rerun"
        assert body.endswith(pipeline_act.trailer("hygiene-check-rerun"))

    def test_it_composes_through_the_one_receipt_writer(self, monkeypatch):
        calls: list = []
        real = pipeline_act.receipt

        def recording(act, detail, *a, **k):
            calls.append((act, detail))
            return real(act, detail, *a, **k)

        monkeypatch.setattr(pipeline_act, "receipt", recording)
        hygiene.receipt("hygiene-pr-close", "superseded by #8", ["#8"], NOW)
        assert [act for act, _ in calls] == ["hygiene-pr-close"]

    @pytest.mark.parametrize("cause", ["a · b", "", "two\nlines"])
    def test_a_cause_that_would_not_read_back_is_refused(self, cause):
        with pytest.raises(ValueError):
            hygiene.receipt("hygiene-pr-close", cause, ["#8"], NOW)

    def test_a_receipt_names_its_evidence(self):
        with pytest.raises(ValueError):
            hygiene.receipt("hygiene-pr-close", "superseded", [], NOW)

    def test_an_act_the_table_does_not_carry_is_refused(self):
        with pytest.raises(hygiene.Forbidden):
            hygiene.receipt("roll-up-activated", "x", ["y"], NOW)

    def test_pt_renders_the_clock(self):
        assert hygiene.pt(NOW) == CLOCK
        assert hygiene.pt(datetime(2026, 12, 1, 21, 5, tzinfo=UTC)) == "13:05 PT"

    @pytest.mark.parametrize("make", [
        lambda body: hygiene.linear_comment(card("DRE-1", "Todo", "portico"), body),
        lambda body: hygiene.gh_pr_comment(PORTICO, 7, body),
        lambda body: hygiene.gh_pr_close(PORTICO, 7, body),
    ])
    def test_a_comment_written_by_hand_is_refused(self, make):
        with pytest.raises(hygiene.Forbidden):
            hygiene.guard(make("🧹 hygiene: hyg-pr-closed — by hand · 14:05 PT"), context())
        with pytest.raises(hygiene.Forbidden):
            hygiene.guard(make("closing this, thanks"), context())
        hygiene.guard(make(_close()[1]), context())

    def test_the_comment_seam_posts_the_composed_receipt(self, monkeypatch):
        posted: list = []
        monkeypatch.setattr(linear_ops, "cmd_comment",
                            lambda ident, body, *f: posted.append((ident, body)))
        c, body = _close()
        hygiene.send(hygiene.linear_comment(c, body), context())
        assert posted == [("DRE-1", body)]

    def test_check_act_receipts_is_green_with_the_seam(self):
        import check_act_receipts

        assert check_act_receipts.problems() == []
        mine = [s for s in check_act_receipts.sites() if s.path == "scripts/hygiene.py"]
        assert mine, "the guard sees no comment-writing site in the hygiene core"
        receipts = [s for s in mine if s.composed_as]
        assert receipts, "the receipt seam does not compose through receipt()"


class TestSend:
    """The one seam, write by write: each constructor's write reaches exactly
    the linear_ops function the contract names, or `gh` with its own argv."""

    @pytest.fixture
    def calls(self, monkeypatch):
        out: list = []
        for name in ("cmd_state", "cmd_comment", "add_label", "remove_label", "_add_blocked_by"):
            monkeypatch.setattr(linear_ops, name,
                                lambda *a, _n=name: out.append((_n, *a)))
        monkeypatch.setattr(hygiene, "GH_RUNNER", lambda argv: out.append(("gh", *argv)) or "")
        return out

    def test_state(self, calls):
        c = card("DRE-1", "Triage", "portico")
        hygiene.send(hygiene.linear_state(c, "Backlog"), context())
        hygiene.send(hygiene.linear_state(c, "Backlog", park=True), context())
        assert calls == [("cmd_state", "DRE-1", "Backlog"),
                         ("cmd_state", "DRE-1", "Backlog", "--park")]

    def test_label(self, calls):
        c = card("DRE-1", "Triage", "portico")
        hygiene.send(hygiene.linear_label(c, "hand-built", True), context())
        hygiene.send(hygiene.linear_label(c, "hand-built", False), context())
        assert calls == [("add_label", "DRE-1", "hand-built"),
                         ("remove_label", "DRE-1", "hand-built")]

    def test_relation(self, calls):
        c = card("DRE-1", "Triage", "portico")
        hygiene.send(hygiene.linear_relation(c, "DRE-2"), context())
        assert calls == [("_add_blocked_by", "uuid-DRE-1", ["DRE-2"])]

    def test_gh(self, calls):
        hygiene.send(hygiene.gh_rerun(PORTICO, 5), context())
        assert calls == [("gh", "gh", "run", "rerun", "5", "--failed", "--repo", PORTICO)]


# --------------------------------------------------------------------------- #
# the archive write — a closed card unarchived for a write, re-archived after  #
# (DRE-6248)                                                                   #
# --------------------------------------------------------------------------- #

ARCHIVED_AT = "2026-10-01T08:00:00.000Z"
#: Every lane a card can stand in that does not close it: the write is
#: refused on each, whatever the read said about `archivedAt`.
OPEN_LANES = ("Todo", "In Progress", "In Review", "Triage", "Green Light",
              "Backlog", "Planning", "Hand-work")


def archived(ident="DRE-1", lane="Done", slug="portico", archived_at=ARCHIVED_AT, **kw):
    return card(ident, lane, slug, archived_at=archived_at, **kw)


class _Recorder:
    """`linear_ops` with every function replaced: `gql` and `get_issue`
    answer and are recorded, and any other call is recorded too, so a test
    can say nothing else was asked."""

    def __init__(self, monkeypatch, issue_id="uuid-from-read"):
        import inspect

        self.calls: list = []
        for name, fn in vars(linear_ops).items():
            if inspect.isfunction(fn) and fn.__module__ == linear_ops.__name__:
                monkeypatch.setattr(linear_ops, name,
                                    lambda *a, _n=name, **k: self.calls.append((_n, a, k)))

        def gql(query, variables=None):
            self.calls.append(("gql", (query, variables), {}))
            return {}

        def get_issue(identifier, **k):
            self.calls.append(("get_issue", (identifier,), k))
            return {"id": issue_id, "identifier": identifier}

        monkeypatch.setattr(linear_ops, "gql", gql)
        monkeypatch.setattr(linear_ops, "get_issue", get_issue)


def _mutation_field(query):
    """The one mutation field a query names: `issueArchive` out of
    `mutation(...) { issueArchive(id: $id) { success } }`."""
    assert query.lstrip().startswith("mutation"), query
    body = query[query.index("{") + 1:]
    return re.match(r"\s*([A-Za-z]+)\s*\(", body).group(1)


class TestTheArchiveWrite:
    def test_the_fifth_linear_kind(self):
        assert hygiene.LINEAR_KINDS == ("linear_state", "linear_comment", "linear_label",
                                        "linear_relation", "linear_archived")

    @pytest.mark.parametrize("flag", [False, True])
    def test_the_constructor_carries_the_card_off_its_dict(self, flag):
        c = archived("DRE-1", "Done", labels=["needs-human"], children=[])
        w = hygiene.linear_archived(c, archived=flag)
        assert w.kind == "linear_archived"
        assert w.archived is flag
        assert w.card == w.target == "DRE-1"
        assert w.card_id == "uuid-DRE-1"
        assert w.labels == ("needs-human", "repo:portico")
        assert w.children == ()
        assert w.state == "Done"
        assert w.archived_at == ARCHIVED_AT

    def test_a_card_with_a_child_carries_it(self):
        w = hygiene.linear_archived(archived(children=["DRE-2"]), archived=False)
        assert w.children == ("DRE-2",)

    def test_a_read_that_carried_no_archived_at_carries_none(self):
        w = hygiene.linear_archived(card("DRE-1", "Done", "portico"), archived=False)
        assert w.archived_at is None

    @pytest.mark.parametrize("bare", ["DRE-1", None, {"id": "uuid-DRE-1"}])
    def test_a_bare_identifier_is_refused_as_for_a_label(self, bare):
        with pytest.raises(hygiene.Forbidden):
            hygiene.linear_label(bare, "hand-built", True)
        with pytest.raises(hygiene.Forbidden):
            hygiene.linear_archived(bare, archived=False)

    def test_describe_is_one_line_naming_the_card_and_the_direction(self):
        c = archived("DRE-7")
        assert hygiene.linear_archived(c, archived=False).describe() == "unarchive DRE-7"
        assert hygiene.linear_archived(c, archived=True).describe() == "archive DRE-7"

    @pytest.mark.parametrize("lane", ["Done", "Canceled"])
    @pytest.mark.parametrize("flag", [False, True])
    def test_a_closed_card_the_read_said_was_archived_is_admitted(self, lane, flag):
        hygiene.guard(hygiene.linear_archived(archived(lane=lane), archived=flag), context(HOME))

    @pytest.mark.parametrize("lane", OPEN_LANES)
    @pytest.mark.parametrize("flag", [False, True])
    def test_the_same_card_in_an_open_lane_is_refused_by_name(self, lane, flag):
        with pytest.raises(hygiene.Forbidden, match="DRE-1"):
            hygiene.guard(hygiene.linear_archived(archived(lane=lane), archived=flag),
                          context(HOME))

    @pytest.mark.parametrize("lane", ["Done", "Canceled"])
    @pytest.mark.parametrize("stamp", [None, ""])
    def test_a_closed_card_the_read_did_not_say_was_archived_is_refused(self, lane, stamp):
        c = card("DRE-1", lane, "portico") if stamp is None else archived(lane=lane, archived_at=stamp)
        for flag in (False, True):
            with pytest.raises(hygiene.Forbidden, match="DRE-1"):
                hygiene.guard(hygiene.linear_archived(c, archived=flag), context(HOME))

    def test_a_card_outside_the_legs_scope_is_refused_by_name(self):
        c = archived("DRE-1", "Done", "atlas")
        for flag in (False, True):
            with pytest.raises(hygiene.Forbidden, match="DRE-1"):
                hygiene.guard(hygiene.linear_archived(c, archived=flag), context(HOME))
        hygiene.guard(hygiene.linear_archived(c, archived=False), context("EveryBite"))

    def test_the_summary_card_is_refused(self):
        with pytest.raises(hygiene.Forbidden, match=SUMMARY_CARD):
            hygiene.guard(hygiene.linear_archived(archived(SUMMARY_CARD), archived=False),
                          context(HOME))

    @pytest.mark.parametrize("flag,field", [(False, "issueUnarchive"), (True, "issueArchive")])
    def test_send_is_exactly_one_mutation_on_the_cards_id(self, monkeypatch, flag, field):
        rec = _Recorder(monkeypatch)
        hygiene.send(hygiene.linear_archived(archived(), archived=flag), context(HOME))
        [(name, (query, variables), _)] = rec.calls
        assert name == "gql"
        assert _mutation_field(query) == field
        assert variables == {"id": "uuid-DRE-1"}

    @pytest.mark.parametrize("flag,field", [(False, "issueUnarchive"), (True, "issueArchive")])
    def test_a_write_with_no_card_id_resolves_it_once_first(self, monkeypatch, flag, field):
        c = archived()
        c["id"] = ""
        rec = _Recorder(monkeypatch, issue_id="uuid-resolved")
        hygiene.send(hygiene.linear_archived(c, archived=flag), context(HOME))
        assert [name for name, _, _ in rec.calls] == ["get_issue", "gql"]
        assert rec.calls[0][1] == ("DRE-1",)
        _, (query, variables), _ = rec.calls[1]
        assert _mutation_field(query) == field
        assert variables == {"id": "uuid-resolved"}


ARCHIVE_LANE = """
c = {"id": "uuid-DRE-5", "identifier": "DRE-5", "state": {"name": "Done"},
     "archivedAt": "2026-10-01T08:00:00.000Z", "children": {"nodes": []},
     "labels": {"nodes": [{"name": "repo:portico"}]}}
out.append(hygiene.Action(lane=LANE, target="DRE-5", act="hygiene-card-close",
    cause="fixture: unarchive a closed card", evidence=["DRE-5"],
    writes=[hygiene.linear_archived(c, archived=False)]))
"""


class TestTheArchiveWriteInADryRun:
    def _pass(self, lanes, monkeypatch, tmp_path, name, body):
        write_lane(lanes, name, body, lane="Done")
        board = tmp_path / f"board-{name}.json"
        board.write_text(json.dumps(board_doc()))
        monkeypatch.setattr(hygiene, "GH_RUNNER", FakeGh())
        ledger = tmp_path / f"ledger-{name}.json"
        assert hygiene.main(["run", "--board", str(board), "--owner", HOME,
                             "--ledger", str(ledger)]) == 0
        (lanes / f"hygiene_{name}.py").unlink()
        return str(ledger)

    def test_it_is_printed_under_would_and_nothing_is_sent(
        self, lanes, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setenv("HYGIENE_DRY_RUN", "1")
        monkeypatch.setenv("HYGIENE_CARD", SUMMARY_CARD)
        monkeypatch.setattr(hygiene, "send", lambda *_a: pytest.fail("dry run reached the seam"))
        rec = _Recorder(monkeypatch)
        monkeypatch.setattr(linear_ops, "comment_records", lambda *_a, **_k: [])
        ledger = self._pass(lanes, monkeypatch, tmp_path, "archive", ARCHIVE_LANE)
        out = capsys.readouterr().out
        assert "would: unarchive DRE-5" in out.splitlines()
        assert [n for n, _, _ in rec.calls] == []
        [action] = json.loads(Path(ledger).read_text())["actions"]
        assert action["outcome"] == "would"
        assert action["writes"] == ["unarchive DRE-5"]

    def test_the_summary_digest_changes_when_it_is_proposed(
        self, lanes, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setenv("HYGIENE_DRY_RUN", "1")
        monkeypatch.setattr(hygiene, "send", lambda *_a: pytest.fail("dry run reached the seam"))
        monkeypatch.setattr(linear_ops, "comment_records", lambda *_a, **_k: [])
        quiet = self._pass(lanes, monkeypatch, tmp_path, "quiet", "")
        proposing = self._pass(lanes, monkeypatch, tmp_path, "archive", ARCHIVE_LANE)
        capsys.readouterr()

        def first_line(path):
            return hygiene.render_summary([json.loads(Path(path).read_text())], NOW).splitlines()[0]

        assert first_line(quiet) != first_line(proposing)
        assert "unarchive DRE-5" in hygiene.render_summary(
            [json.loads(Path(proposing).read_text())], NOW)

        # Under a standing summary of the quiet pass, the quiet pass is
        # "nothing changed" and the proposing pass is the summary it would post.
        s = Summary(monkeypatch, newest=summary_with(digest_of([])))
        hygiene.main(["summarize", quiet])
        assert "nothing changed" in capsys.readouterr().out
        hygiene.main(["summarize", proposing])
        out = capsys.readouterr().out
        assert f"would: comment {SUMMARY_CARD}:" in out
        assert "unarchive DRE-5" in out
        assert s.posts == []

    def test_a_live_ledger_is_digested_as_before(self, tmp_path):
        # Only a dry run proposes; an executed or suppressed action leaves the
        # digest over the left rows alone, so the standing card never carries
        # a digest a live pass could not reproduce.
        doc = json.loads(Path(ledger(tmp_path, "a", actions=[("DRE-1", "executed"),
                                                              ("DRE-2", "suppressed")],
                                     left=[("DRE-3", "stuck")])).read_text())
        first = hygiene.render_summary([doc], NOW).splitlines()[0]
        assert first.split()[3] == digest_of(["DRE-3"])


# --------------------------------------------------------------------------- #
# summarize — posted only when something changed                               #
# --------------------------------------------------------------------------- #


def ledger(tmp_path, name, actions=(), left=()):
    path = tmp_path / f"ledger-{name}.json"
    path.write_text(json.dumps({
        "owner": name, "taken_at": "2026-09-30T21:05:00Z",
        "actions": [{"lane": "Todo", "target": t, "act": "hygiene-card-close",
                     "cause": "PR o/r#7 merged at aaaaaaa", "evidence": ["o/r#7"],
                     "writes": [], "outcome": o} for t, o in actions],
        "left": [{"lane": "Todo", "target": t, "why": w, "recommendation": "look"}
                 for t, w in left],
    }))
    return str(path)


def digest_of(targets):
    return hashlib.sha256("\n".join(sorted(targets)).encode()).hexdigest()[:12]


class Summary:
    """The standing card's newest summary, and what gets posted to it."""

    def __init__(self, monkeypatch, newest=None):
        self.reads: list = []
        self.posts: list = []
        nodes = [] if newest is None else [
            {"body": "an ordinary comment", "createdAt": "2026-09-30T21:00:00Z"},
            {"body": newest, "createdAt": "2026-09-30T20:00:00Z"},
            {"body": f"{hygiene.SUMMARY_MARK} 000000000000 · 12:00 PT",
             "createdAt": "2026-09-30T19:00:00Z"},
        ]

        def gql(query, variables=None):
            self.reads.append(variables)
            assert not query.lstrip().startswith("mutation")
            return {"issue": {"comments": {"pageInfo": {"hasNextPage": False},
                                           "nodes": nodes}}}

        monkeypatch.setattr(linear_ops, "gql", gql)
        monkeypatch.setattr(linear_ops, "cmd_comment",
                            lambda ident, body, *f: self.posts.append((ident, body)))
        monkeypatch.setenv("HYGIENE_CARD", SUMMARY_CARD)


def summary_with(digest):
    return f"{hygiene.SUMMARY_MARK} {digest} · 13:05 PT\n\nTodo\n- left DRE-5"


class TestSummarize:
    def test_the_digest_is_over_the_sorted_left_targets_only(self):
        assert hygiene.digest(["DRE-2", "DRE-1"]) == digest_of(["DRE-1", "DRE-2"])
        assert hygiene.digest([]) == hashlib.sha256(b"").hexdigest()[:12]

    def test_an_executed_action_posts(self, tmp_path, monkeypatch):
        s = Summary(monkeypatch, newest=summary_with(digest_of([])))
        code = hygiene.main(["summarize", ledger(tmp_path, "a", actions=[("DRE-1", "executed")])])
        assert code == 0
        [(ident, body)] = s.posts
        assert ident == SUMMARY_CARD
        first = body.splitlines()[0]
        assert first == f"{hygiene.SUMMARY_MARK} {digest_of([])} · {hygiene.pt(datetime.now(UTC))}"
        assert "DRE-1" in body
        assert len(s.reads) <= 1  # an executed action posts whatever the newest says

    def test_the_summary_says_what_was_cleared_with_its_time_and_what_was_left(
        self, tmp_path, monkeypatch
    ):
        s = Summary(monkeypatch)
        hygiene.main(["summarize",
                      ledger(tmp_path, "a", actions=[("DRE-1", "executed"), ("DRE-3", "suppressed")],
                             left=[("DRE-2", "the pull request is a person's call")])])
        [(_, body)] = s.posts
        assert "Todo" in body
        cleared = next(line for line in body.splitlines() if "DRE-1" in line)
        assert "hyg-card-closed" in cleared and CLOCK in cleared
        left = next(line for line in body.splitlines() if "DRE-2" in line)
        assert "the pull request is a person's call" in left and "look" in left
        assert "DRE-3" not in body

    def test_a_changed_left_set_posts(self, tmp_path, monkeypatch):
        s = Summary(monkeypatch, newest=summary_with(digest_of(["DRE-5"])))
        hygiene.main(["summarize", ledger(tmp_path, "a", left=[("DRE-6", "stuck")])])
        assert len(s.posts) == 1
        assert len(s.reads) == 1
        assert s.posts[0][1].splitlines()[0].split()[3] == digest_of(["DRE-6"])

    def test_the_same_left_set_with_no_action_posts_nothing_even_when_a_why_changed(
        self, tmp_path, monkeypatch
    ):
        s = Summary(monkeypatch, newest=summary_with(digest_of(["DRE-5", "DRE-6"])))
        code = hygiene.main(["summarize",
                             ledger(tmp_path, "a", left=[("DRE-6", "a new reason")]),
                             ledger(tmp_path, "b", left=[("DRE-5", "another")],
                                    actions=[("DRE-9", "suppressed")])])
        assert code == 0
        assert s.posts == []

    def test_nothing_at_all_under_an_empty_set_summary_posts_nothing(self, tmp_path, monkeypatch):
        s = Summary(monkeypatch, newest=summary_with(digest_of([])))
        hygiene.main(["summarize", ledger(tmp_path, "a")])
        assert s.posts == []

    def test_nothing_at_all_under_a_summary_that_still_lists_rows_posts_once(
        self, tmp_path, monkeypatch
    ):
        s = Summary(monkeypatch, newest=summary_with(digest_of(["DRE-5"])))
        hygiene.main(["summarize", ledger(tmp_path, "a")])
        assert len(s.posts) == 1
        again = Summary(monkeypatch, newest=s.posts[0][1])
        hygiene.main(["summarize", ledger(tmp_path, "a")])
        assert again.posts == []

    def test_with_no_summary_yet_it_posts(self, tmp_path, monkeypatch):
        s = Summary(monkeypatch, newest=None)
        hygiene.main(["summarize", ledger(tmp_path, "a")])
        assert len(s.posts) == 1

    def test_dry_run_posts_nothing(self, tmp_path, monkeypatch, capsys):
        s = Summary(monkeypatch, newest=summary_with(digest_of([])))
        hygiene.main(["summarize", ledger(tmp_path, "a", actions=[("DRE-1", "executed")]),
                      "--dry-run"])
        assert s.posts == []
        assert "would: " in capsys.readouterr().out

    def test_an_unset_card_prints_the_summary_and_says_so(self, tmp_path, monkeypatch, capsys):
        s = Summary(monkeypatch)
        monkeypatch.delenv("HYGIENE_CARD")
        code = hygiene.main(["summarize", ledger(tmp_path, "a", actions=[("DRE-1", "executed")])])
        assert code == 0
        out = capsys.readouterr().out
        assert hygiene.SUMMARY_MARK in out and "DRE-1" in out
        assert "HYGIENE_CARD is unset" in out
        assert s.posts == [] and s.reads == []


# --------------------------------------------------------------------------- #
# the two registries                                                           #
# --------------------------------------------------------------------------- #


def acts_doc():
    return json.loads((ROOT / "config" / "pipeline-acts.json").read_text())


def contract_doc():
    return json.loads((ROOT / "config" / "lane-contract.json").read_text())


class TestTheActRegistry:
    def test_the_core_holds_the_one_table_of_tags(self):
        assert hygiene.TAGS == ACTS

    def test_the_twelve_rows(self):
        rows = {r["name"]: r for r in acts_doc()["acts"] if r["name"] in ACTS}
        assert set(rows) == set(ACTS)
        glossary = contract_doc()["writers"]
        for name, row in rows.items():
            assert row["tag"] == ACTS[name]
            assert row["adopted"] is True
            assert row["emits"] == {"file": "scripts/hygiene.py", "anchor": f'"{ACTS[name]}"'}
            assert row["subscriber"] == "hygiene.yml"
            assert row["next_actor"] in glossary
            assert row["cadence_s"] is None

    def test_next_actor_follows_the_card(self):
        rows = {r["name"]: r for r in acts_doc()["acts"] if r["name"] in ACTS}
        expected = {name: "operator" for name in ACTS}
        expected.update({
            "hygiene-gate-redispatch": "merge-gate.yml",
            "hygiene-branch-refresh": "merge-gate.yml",
            "hygiene-check-rerun": "merge-gate.yml",
            "hygiene-resend-to-planning": "plan.yml",
            "hygiene-triage-return": "reconcile.py",
            "hygiene-review-move": "reconcile.py",
            "hygiene-hold-clear": "reconcile.py",
        })
        assert {n: r["next_actor"] for n, r in rows.items()} == expected

    def test_two_holds_and_ten_recoveries(self):
        rows = {r["name"]: r for r in acts_doc()["acts"] if r["name"] in ACTS}
        holds = {n for n, r in rows.items() if r["kind"] == "hold"}
        assert holds == {"hygiene-decision-needed", "hygiene-cause-name"}
        assert {r["kind"] for n, r in rows.items() if n not in holds} == {"recovery"}

    def test_the_summary_is_declared_not_an_act(self):
        rows = [u for u in acts_doc()["unconverted"] if u["file"] == "scripts/hygiene.py"]
        assert len(rows) == 1
        assert rows[0]["kind"] == "not-an-act"

    def test_the_registry_checks_pass(self):
        assert pipeline_act.problems() == []


#: The twelve clauses this card amends, and the sentence each gains.
AMENDED = (
    ("Done", "entrance"), ("Done", "writers"), ("Canceled", "entrance"),
    ("Canceled", "writers"), ("Planning", "writers"), ("Backlog", "writers"),
    ("In Review", "writers"), ("Green Light", "exit"), ("Triage", "exit"),
    ("Todo", "exit"), ("In Progress", "exit"), ("In Review", "exit"),
    # The card's one contract point with DRE-5240: whichever of the two lands
    # second gives the Hand-work exit the sentence the Todo exit carries, or
    # the agent could never close a proof that sits there. DRE-5315 landed
    # the lane first.
    ("Hand-work", "exit"),
)


class TestTheLaneContract:
    def _lane(self, name):
        return next(entry for entry in contract_doc()["lanes"] if entry["name"] == name)

    @pytest.mark.parametrize("lane,clause", AMENDED)
    def test_each_amended_clause_admits_the_agent_in_its_own_words(self, lane, clause):
        assert "hygiene agent" in self._lane(lane)["clauses"][clause]["text"]

    def test_the_hand_work_exit_carries_the_todo_exits_sentence(self):
        sentence = ("Or the hygiene agent closes a card whose pull request merged "
                    "while it sat here (DRE-5365).")
        assert self._lane("Todo")["clauses"]["exit"]["text"].endswith(sentence)
        assert self._lane("Hand-work")["clauses"]["exit"]["text"].endswith(sentence)

    def test_the_writer_glossary_carries_the_agent(self):
        entry = contract_doc()["writers"]["hygiene.py"]
        assert entry["path"] == "scripts/hygiene.py"
        assert entry["what"] == (
            "the hygiene agent — the hourly pass that clears mechanical rows, each "
            "move under a 🧹 receipt naming the evidence it read")

    def test_exactly_five_lanes_permit_it(self):
        permitting = {entry["name"] for entry in contract_doc()["lanes"]
                      if "hygiene.py" in entry["clauses"]["writers"].get("who", [])}
        assert permitting == {"Planning", "Backlog", "In Review", "Done", "Canceled"}

    def test_the_guard_admits_exactly_the_lanes_the_contract_permits(self):
        assert set(hygiene.destinations()) == {"Planning", "Backlog", "In Review",
                                               "Done", "Canceled"}

    @pytest.mark.parametrize("lane", ["Planning", "Backlog", "In Review", "Done", "Canceled"])
    def test_the_writers_clause_still_holds_with_the_agent_in_it(self, lane):
        report = lane_contract.check(vocabulary=lane_contract.pipeline_vocabulary())
        found = [f for f in report.findings if f.clause_id == f"{lane}.writers"]
        assert [f.status for f in found] == ["pass"], found
